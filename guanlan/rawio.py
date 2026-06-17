"""写 `raw/` 的 transport-neutral 归口（P5.2，从 `web/rawfeed.py` 抽出）。

「投喂 / 晋级 / CLI convert」三条入口必须共用**同一套** slug / 文本准入 / provenance /
原子写规则，否则两处漂移（安全敏感 + provenance 不一致）。故把这些**无状态原语**沉到本
模块，**两类契约分明**（决策P5.2-6，关键）：

- **校验 / 变换纯函数 → `raise ValueError`**（非 HTTPException）：`raw_slug` /
  `normalize_basename` / `safe_raw_target` / `check_text_admission` / `apply_origin`。
  它们都在**入队之前**跑——web 薄壳（`web/rawfeed.py`）把 `ValueError` 包成
  `HTTPException(400)`，CLI（`convert.py`）映射 `EXIT_USAGE`，**不经 JobQueue worker 的
  退出码归一**。
- **`atomic_write_raw(target, content, overwrite) -> int` → 仍返回退出码、绝不抛
  `ValueError`**（决策P5.2-6 关键）：它在**串行 worker turn 内**复检覆盖语义——同名已
  存在且无 `overwrite` → 返回 `EXIT_USAGE`；落盘 IO 异常（`OSError`）**继续向上抛**。
  为何独不 raise：web 把它当 JobQueue 作业 thunk 跑，worker 只认**返回的 exit_code**
  （`EXIT_USAGE`→409「已存在」、`EXIT_AGENT_ERROR`→500「写盘失败」）；若也改成 raise，
  worker 会把异常归一成 agent error，409 退化成 500，破 P4.6「零行为变更」硬门。

本模块不含任何路由 / 可变状态，故可独立单测、与 web 解耦；`web/rawfeed.py` 保留薄壳
re-export 旧名（`_raw_slug` / `_normalize_basename` / …）并把校验 `ValueError` 包成
`HTTPException(400)`，字节行为不变。
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import unicodedata
from pathlib import Path

import yaml

from .errors import EXIT_OK, EXIT_USAGE
from .pages import split_frontmatter

MAX_RAW_BYTES = 5 * 1024 * 1024  # 投喂正文大小上限（默认 5 MiB），防误粘巨量文本。

# 已知非-md 扩展名拒绝列表（命中即 400，挡多格式误投，与 P5 边界一致）。判定用
# `Path(规范化 basename).suffix.lower() in _RAW_REJECT_EXTENSIONS`——**不**用朴素
# `suffix != ".md"`，以免误杀 `GPT-4.5 笔记`（suffix `.5 笔记`）、`v1.2` 等带点标题（决策P4.1-4）。
_RAW_REJECT_EXTENSIONS = frozenset(
    {".txt", ".pdf", ".docx", ".doc", ".html", ".htm", ".rtf", ".epub"}
)

# 混淆字符规范化映射表（决策P4.1-4）：大模型常给文件名夹带 ASCII 外的同形/排版字符，直接
# 交给 slug 的"其余→`-`"会劣化成 `-` 串（`"X"` → `-X-`）。故先 NFKC 折全角/兼容字符，再过本表
# 处理 NFKC 不覆盖的排版符号。键是 unicode 序数（str.translate 要求），值为替换串或 None（删除）。
# 只作用于**文件名**；正文 `content` 永远按 UTF-8 原样写盘（未加工源保真）。
_RAW_NAME_FOLDMAP: dict[int, str | None] = {
    # 各类引号 → 删除（不留 `-`，避免 `"X"` 变 `-X-`）。
    **{ord(c): None for c in '"\'“”‟„«»‘’‚‛‹›「」『』`'},
    # 各类破折号 / 连接号 / 波浪号 → 单个 `-`（`～` 已被 NFKC 折成 `~`，一并收）。
    **{ord(c): "-" for c in "—–―‒~"},
    # 省略号 → 删除（注：NFKC 已把 `…` 展成 `...`，本项仅兜未展开者）。
    ord("…"): None,
    # NFKC 不覆盖的零宽空白 → 普通空格（随后 slug 收敛成 `-`；NBSP/全角空格已由 NFKC 折成空格）。
    **{ord(c): " " for c in "​﻿"},
}

# slug：保留 CJK/字母/数字/`-`/`_`/`.`（`\w` 在 str 正则下含 CJK），其余（含空格）成 `-`；折叠连续 `-`。
_RAW_SLUG_STRIP = re.compile(r"[^\w.\-]+")
_RAW_SLUG_DASHES = re.compile(r"-{2,}")


def raw_slug(stem: str) -> str:
    """把已规范化的 basename（去后缀）收敛为安全 slug；首尾 `-`/`.`/空白剥净。

    首尾 `.` 一并剥净：杜绝盘上落出隐藏文件（`.foo` → `foo`）或双点（`notes.` → `notes`）；
    内部点保留（`v1.2` 不变），故带点标题仍保真。
    """
    return _RAW_SLUG_DASHES.sub("-", _RAW_SLUG_STRIP.sub("-", stem)).strip("-.")


def find_source_page(sources_dir: Path, raw_stem: str) -> Path | None:
    """按 raw 文件名 stem 定位其 source 摘要页路径；容忍 `.`/`-` 归一分歧，无则 None。

    `raw_slug` **保留内部点**（`1.标准体系` 原样），而 ingest Agent 按「kebab-case（同源文件名）」
    命名摘要页时，常把开头枚举序号的点写成横杠（落成 `1-标准体系`）。两边对这个点处理不一致，会让
    「同名 source 页存在 = 已收录」的纯读判定（`web._list_raw`）与 `raw_digest` stamp 定位
    （`ingest._stamp_source_digest`）都认不出**其实已建好**的页——表现为长期「未收录」+ 无指纹。
    故：① 先按确定性 `raw_slug` 精确命中（保点形）；② 未命中再把点折成横杠退一步认 kebab 形。
    回退**单向**（仅点→横杠，因 raw_slug 只会多出点）且仍按文件名精确 stat、不扫目录，把误配面压到最小。
    """
    slug = raw_slug(raw_stem)
    if not slug:
        return None
    exact = sources_dir / f"{slug}.md"
    if exact.is_file():
        return exact
    dashed = _RAW_SLUG_DASHES.sub("-", slug.replace(".", "-")).strip("-.")
    if dashed and dashed != slug:
        kebab = sources_dir / f"{dashed}.md"
        if kebab.is_file():
            return kebab
    return None


def normalize_basename(name: str) -> str:
    """剥目录成分 → NFKC + 混淆字符映射 + 剥首尾空白（投喂 raw/ 与上传 workspace/ 同一归口）。

    单一来源杜绝两处文件名清洗规则漂移（安全敏感）：`safe_raw_target` / `_safe_workspace_target`
    都先调它再各自处理后缀（raw 强制 `.md`、workspace 保留原扩展名）。
    """
    base = Path(name).name  # 剥掉任何目录成分，杜绝 ../、绝对路径、子目录穿越。
    return unicodedata.normalize("NFKC", base).translate(_RAW_NAME_FOLDMAP).strip()


def safe_raw_target(root: Path, name: str) -> Path:
    """把用户给的文件名/标题解析为 `<kb>/raw/<安全名>.md`（决策P4.1-4，与 `_safe_wiki_file` 并列）。

    判定顺序须钉死：① 剥目录 → ② NFKC + 映射表规范化 + **剥首尾空白** → ③ 基于**规范化 basename**
    取 `suffix.lower()`（命中拒绝列表即 `ValueError`；`.md` 视作已带后缀、归一小写）→ ④ slug → ⑤ 强制 `.md`
    → ⑥ resolve 越界校验。规范化在取 suffix **之前**：否则全角点 `x．PDF` 会漏成 `x.PDF.md`。
    步骤 ② 必须 `.strip()`：尾随空白会让 `Path("foo.MD ").suffix == ".MD "`（带空格）逃过 `.md`
    归一、落成 `foo.MD.md`（大写双后缀）；剥净后 `foo.MD` → `.md` 归一 → `foo.md`。

    校验失败一律 `raise ValueError`（薄壳/命令层各自映射 HTTP 400 / EXIT_USAGE，决策P5.2-6）。
    """
    normalized = normalize_basename(name)  # ①剥目录 + ②NFKC + 映射 + 剥空白（共用归口）
    suffix = Path(normalized).suffix.lower()  # ③ 基于规范化 basename
    if suffix in _RAW_REJECT_EXTENSIONS:
        raise ValueError(f"投喂只收 .md 文本；拒绝扩展名 {suffix}。")
    # `.md`（大小写不敏感）视作已带后缀，剥掉再补归一的小写 `.md`（免盘上混入 .MD）。
    stem = normalized[: -len(suffix)] if suffix == ".md" else normalized
    slug = raw_slug(stem)  # ④
    if not slug:
        raise ValueError("文件名经规范化后为空，请改名。")
    safe = f"{slug}.md"  # ⑤
    raw = (root / "raw").resolve()
    target = (raw / safe).resolve()  # ⑥ 纵深防御：理论上 ① 已挡住，仍校验落点。
    try:
        target.relative_to(raw)
    except ValueError:
        raise ValueError(f"路径越界（须在 raw/ 内）：{name}") from None
    return target


def check_text_admission(content: str) -> None:
    """投喂 / 晋级 / convert 共用的文本闸（决策P4.1-5 / P4.6-4）：空 / 超限 / NUL / 控制字符 → `ValueError`。

    三条入口过**同一道闸**，杜绝二进制 / 超大 / 含控制字符的 `.md` 混进 `raw/`。
    """
    if not content.strip():  # 空正文
        raise ValueError("正文不能为空。")
    if len(content.encode("utf-8")) > MAX_RAW_BYTES:
        raise ValueError(f"正文超过 {MAX_RAW_BYTES} 字节上限。")
    # 拒 NUL / `\t\n\r` 以外的 C0 控制字符（判二进制 / 垃圾）。
    if "\x00" in content or any(c < " " and c not in "\t\n\r" for c in content):
        raise ValueError("raw/ 只收文本素材；检测到 NUL/控制字符。")


def apply_origin(text: str, origin: str) -> str:
    """按 provenance 规则把 `origin` 注入 frontmatter（决策P4.6-10）。返回归一后的内容。

    确定性、**复用 `split_frontmatter`**（非裸插一行），四分支：① **无块**（含未闭合块——
    `split_frontmatter` 对二者都返回「无块」、不可区分，均按本支处理）→ 新建 `---` 块前置、
    **原文逐字作 body**；② **有闭合块、得 mapping 且缺 `origin`** → 插入 `origin` 键、重序列化；
    ③ **有块且已有 `origin`** → **永远保留 parsed 自带值、忽略传入 origin**（`overwrite` 专表
    「覆盖同名 raw/ 文件」、不重载它表达「覆盖 origin」）；④ **有闭合块但 `yaml.safe_load`
    不可解析 / 非 mapping（list/标量）** → `ValueError`（不静默当 body、不插坏块）。

    `origin` 一律作 YAML 标量经 `yaml.safe_dump` 写入，**绝不裸拼** `origin: <值>`——否则含
    `:`、引号、`#`、换行、前后空白的出处会生成坏 frontmatter（决策P4.6-10）。
    """
    block, body = split_frontmatter(text)
    bad_block = ValueError("parsed frontmatter 损坏或非键值映射，请在可写会话修正后再晋级。")
    if block is None:  # ① 无块（含未闭合）→ 新建块、原文作 body
        dumped = yaml.safe_dump({"origin": origin}, allow_unicode=True, sort_keys=False)
        return f"---\n{dumped}---\n{text}"
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError:
        raise bad_block from None
    if meta is None:
        meta = {}  # 空块（`---\n---` / 纯空白 / `null`）→ 视作空映射、插入 origin（非坏块）
    elif not isinstance(meta, dict):  # ④ 非 mapping（list / 标量）
        raise bad_block
    if "origin" in meta:  # ③ 已有 origin → 永久保留、忽略传入值
        return text
    meta["origin"] = origin  # ② 缺 origin → 插入键、重序列化（body 逐字保留）
    dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    return f"---\n{dumped}---\n{body}"


def atomic_write_raw(target: Path, content: str, overwrite: bool) -> int:
    """在**串行 worker turn 内**复检覆盖语义并原子落盘（决策P4.1-2/3）。返回退出码。

    两个并发同名投喂时端点的 existence 预检可能都放过；故这里在真正串行的临界区里再查一次
    `overwrite`，第二个返回 `EXIT_USAGE`（端点转 409）。落盘写同目录临时文件再 `os.replace`
    换名（原子）；IO 异常（`OSError`）向上抛——web 由 worker 归一为 EXIT_AGENT_ERROR（端点转
    500），CLI `convert` 由命令壳 `except OSError` 转 EXIT_USAGE（决策P5.2-6）。**绝不抛
    `ValueError`**：否则 worker 把同名冲突归一成 agent error，web 的 409 退化成 500。
    """
    if target.exists() and not overwrite:
        print(f"raw/{target.name} 已存在。")  # 经 worker redirect_stdout → job.output（409 detail）
        return EXIT_USAGE
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)  # UTF-8 原样：不渲染、不重写 [[wikilink]]（raw/ 是未加工源）。
        os.replace(tmp, target)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return EXIT_OK
