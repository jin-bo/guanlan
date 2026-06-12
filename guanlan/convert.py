"""多格式摄入 `guanlan convert`（P5.2，见 docs/P5.2-多格式摄入.md）。**脚本零 LLM、宿主写 `raw/`。**

`guanlan convert path/to/报告.pdf`：把 PDF/DOCX/PPTX/XLSX/HTML/图片… 经**既有
`pdf-to-markdown` skill 的 `convert.py`**（MinerU→marker→pypdf 分层兜底、已随 wheel 全局安装）
转成 markdown，落成 `raw/<slug>.md` 源（含 `origin` provenance），**复用 `.md` 单格式 ingest
不动、复用 P4.6 的 raw 写入规则**（`rawio` 归口）。这补的是 CLI 那个洞——当前 `ingest` 仍硬拒
非 `.md`，命令行用户没有官方的 `PDF/DOCX → raw/*.md → ingest` 路径（决策P5.2-1）。

要点：
- **两步默认、不自动 ingest**（决策P5.2-1/8）：`convert` 只做「转换 + 落源」；建页那步仍由
  独立的 `guanlan ingest raw/<slug>.md`（走 Agentao + gate）完成。`--ingest` 是可选便利串联。
- **纯宿主写 `raw/`、零 LLM、不起 Agentao、不取 raw 快照**（决策P5.2-3）：是「人投喂源」（与
  P4.1 投喂同性质），不走 ingest 的 `run_guarded_write` 子进程 + 快照门禁。
- **「脚本零 LLM」管 guanlan 自身**（决策P5.2-4）：本模块**不内嵌 LLM 客户端/密钥**，只 shell
  out 到外部转换进程；该进程（如 marker）是否用 LLM 增强、用谁的 key，由**用户环境**决定，
  与 guanlan 正交——故**不做 env-scrub、不暴露 `--model`、不向 skill 透传 model**。
- **无新依赖、无新 extra**（决策P5.2-5）：转换后端复用 skill 既有分层兜底与 graceful degrade。
- **内核 / 命令壳分立**（决策P5.2-9）：`convert_to_markdown`（内核）与 `run_convert`（命令壳）
  分立，供将来 Web 机会性优化直接复用内核（仿 P5.0 `search_pages` 被 P5.1 复用，不单列阶段）。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .ingest import run_ingest
from .paths import require_kb_root
from .rawio import apply_origin, atomic_write_raw, check_text_admission, safe_raw_target
from .skill import bundled_skill_dir

# skill `convert.py` 支持的后端（透传给 skill；格式白名单归口在 skill，不在此重复，决策P5.2-2/5）。
_BACKENDS = ("auto", "mineru", "marker", "python")

# 同名已存在文案归口（早预检 + atomic_write_raw TOCTOU 回退两处共用，免改一处漏一处）。
_RAW_EXISTS_MSG = "raw/{name} 已存在；改名（`--name`）或加 `--overwrite` 覆盖。"

# 图片扩展白名单（与 skill `SUPPORTED_EXTENSIONS` 的图片子集对齐，决策P5.2.1-5(e)）。
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}
# 容量上限（决策P5.2.1-11）——护 convert 入内存 + 下游 ingest 的 snapshot_raw 递归 hash 整个 raw/。
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 单图 ≤ 20 MiB
MAX_IMAGES_TOTAL_BYTES = 200 * 1024 * 1024  # 单次累计 ≤ 200 MiB
MAX_IMAGE_COUNT = 500  # 单次张数 ≤ 500

# markdown 图片语法 `![alt](pre url post)`：url 取到空白或 `)` 为止；分组用于「只换 url、保 alt/title」。
# 仅匹配 markdown 图片语法——HTML `<img>` 不在范围（mineru/marker 产物用 markdown，§0.1 实测）。
_MD_IMAGE = re.compile(r"(?P<head>!\[[^\]]*\]\()(?P<pre>\s*)(?P<url>[^)\s]+)(?P<post>[^)]*)\)")
# scheme（`http:`/`https:`/`file:`/`data:`/Windows 盘符 `C:` 等）：拒一切带 scheme 的 url（决策P5.2.1-5(a)）。
_URL_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*:")


class ConvertError(Exception):
    """转换失败（skill 缺失 / 不支持的格式 / 全后端耗尽 / 图片超容量）。命令层映射 `EXIT_USAGE`。"""


@dataclass(frozen=True)
class ConvertedImage:
    """一张随转换文件落盘的图片（决策P5.2.1-4）。"""

    name: str  # 落盘后文件名 <stem>-<n>.<ext>（n 从 1，ext 取原图后缀小写）
    data: bytes  # 图片字节，逐字拷贝、不重编码（决策P5.2.1-8）


@dataclass(frozen=True)
class ConvertResult:
    """转换内核产物：重写后 markdown + 收集到的图片（决策P5.2.1-4）。"""

    markdown: str  # 引用已重写为 images/<stem>/<stem>-<n>.<ext>
    images: tuple[ConvertedImage, ...] = ()  # 按 md 内首次出现序；无图 → 空 tuple
    skipped: int = 0  # 未准入/未搬运的图片引用数（原样保留），供命令壳 stderr 计数


def _skill_convert_script() -> Path:
    """定位 `pdf-to-markdown` skill 的 `scripts/convert.py`（复用 `skill.py` 的归口）。

    `bundled_skill_dir("pdf-to-markdown")` 解析安装态 `guanlan/_skill/` 或开发期仓库根
    `skills/`，取其 `scripts/convert.py`。找不到 → `ConvertError`（命令层 `EXIT_USAGE`，提示重装）。
    """
    try:
        script = bundled_skill_dir("pdf-to-markdown") / "scripts" / "convert.py"
    except FileNotFoundError as exc:
        raise ConvertError(
            f"找不到随包 pdf-to-markdown skill：{exc}。请 `guanlan install-skill --force` 重装。"
        ) from None
    if not script.is_file():
        raise ConvertError(
            f"pdf-to-markdown skill 缺 scripts/convert.py：{script}。"
            "请 `guanlan install-skill --force` 重装。"
        )
    return script


def _admit_image_ref(url: str, md_parent: Path, tmp_root: Path) -> Path | None:
    """图片引用准入安全闸（决策P5.2.1-5，**安全敏感**，五条 AND）。命中 → 返回解析后 realpath；否则 None。

    (a) 仅相对路径：拒一切带 scheme（`http(s):`/`file:`/`data:`/Windows `C:`）/协议相对 `//`/绝对 `/`/`~`；
    (b) `urllib.parse.unquote` 解码（挡 `%2e%2e` 等编码穿越）后按相对 `md_parent` 解析；
    (c) `os.path.realpath`（解 symlink）后必须 `relative_to(tmp_root)`——越界/symlink-逃逸一律拒收；
    (d) 落点须是真实普通文件（`is_file()` 且非 symlink 自身、非目录/FIFO/设备）；
    (e) 后缀（小写）∈ `IMAGE_EXTS` 白名单。
    """
    if _URL_SCHEME.match(url) or url.startswith(("//", "/", "~")):  # (a)
        return None
    decoded = urllib.parse.unquote(url)  # (b)
    candidate = Path(os.path.realpath(md_parent / decoded))  # (c) 解 symlink
    try:
        candidate.relative_to(tmp_root)
    except ValueError:
        return None
    if candidate.is_symlink() or not candidate.is_file():  # (d)
        return None
    if candidate.suffix.lower() not in IMAGE_EXTS:  # (e)
        return None
    return candidate


def _collect_and_rewrite_images(
    markdown: str, *, produced_md: Path, tmp_root: Path, stem: str
) -> ConvertResult:
    """在 temp 销毁前扫描 markdown 图片引用、收集字节、按首次出现序编号、重写引用（决策P5.2.1-3/5）。

    引擎无关：mineru 与 marker 的引用**都相对产物 md 自身目录**（§0.1 实测），故统一按相对
    `produced_md.parent` 解析（叠加 `_admit_image_ref` 安全闸），不写引擎分支。按解析后 realpath
    去重（同图多引复用同号）；逐张累计校验容量三上限，任一超限 → `ConvertError`（非静默丢弃）。
    """
    md_parent = produced_md.parent
    collected: dict[str, ConvertedImage] = {}  # realpath -> 已收集图片（去重）
    order: list[ConvertedImage] = []  # 按首次出现序
    stats = {"skipped": 0, "total_bytes": 0}

    def repl(m: re.Match[str]) -> str:
        resolved = _admit_image_ref(m.group("url"), md_parent, tmp_root)
        if resolved is None:  # 未准入 → 原样保留引用、不编号
            stats["skipped"] += 1
            return m.group(0)
        key = str(resolved)
        img = collected.get(key)
        if img is None:
            n = len(order) + 1
            if n > MAX_IMAGE_COUNT:
                raise ConvertError(
                    f"图片张数超过上限 {MAX_IMAGE_COUNT}；请拆分文档或用 --dry-run 排查。"
                )
            # 先 stat 校验单图/累计上限，**再** read_bytes：否则一张超大图会先被整盘读入内存、
            # 才被拒，§2.1 容量闸的「护 convert 入内存」承诺落空（决策P5.2.1-11）。
            size = resolved.stat().st_size
            if size > MAX_IMAGE_BYTES:
                raise ConvertError(
                    f"单图 {resolved.name} 超过上限 {MAX_IMAGE_BYTES} 字节；请降分辨率后重转。"
                )
            if stats["total_bytes"] + size > MAX_IMAGES_TOTAL_BYTES:
                raise ConvertError(
                    f"图片累计超过上限 {MAX_IMAGES_TOTAL_BYTES} 字节；请拆分文档后重转。"
                )
            data = resolved.read_bytes()
            stats["total_bytes"] += len(data)
            img = ConvertedImage(name=f"{stem}-{n}{resolved.suffix.lower()}", data=data)
            collected[key] = img
            order.append(img)
        new_url = f"images/{stem}/{img.name}"  # raw/<slug>.md → raw/images/<slug>/ 的正确相对路径
        return f"{m.group('head')}{m.group('pre')}{new_url}{m.group('post')})"

    rewritten = _MD_IMAGE.sub(repl, markdown)
    return ConvertResult(markdown=rewritten, images=tuple(order), skipped=stats["skipped"])


def convert_to_markdown(
    src: Path, *, stem: str, backend: str = "auto", cwd: Path | None = None
) -> ConvertResult:
    """**转换内核**：把一个多格式文件转成 markdown + 随源图片，零 LLM。复用 skill `convert.py` 当引擎。

    `stem` = 最终 raw slug（命令壳传 `target.stem`），用于图片重命名与引用重写口径对齐 md 文件名
    （决策P5.2.1-4）。`cwd` = 子进程工作目录 / skill `.env` 发现锚点（`run_convert` 传 **KB root**；
    None → 回退 `Path.cwd()`）：

    1. **temp 暂存**：把 src 复制进 `tempfile.TemporaryDirectory`、得**绝对路径** `staged`
       （skill `convert.py` 把产物写在*输入文件同目录*、无 `--output_dir`——`staged` 在 tmpdir 内，
       故产物落 tmpdir、不污染用户目录，决策P5.2-10）；
    2. **子进程调用**：`[sys.executable, convert_py, staged, "--backend", backend]`，
       **cwd 取 KB root、绝不取 tmpdir**（决策P5.2-12）：skill `find_dotenv()` 从 cwd 向上找
       `.env`，cwd=tmpdir 会让它找不到 KB/项目的 `.env`、marker 无法经 `.env` 启用 Gemini。
       `staged` 是绝对路径、产物目录来自 `input.parent`=tmpdir、**与 cwd 无关**，故 cwd≠tmpdir
       **不**重新引入污染。**透传调用方环境**（不做 env-scrub，决策P5.2-4），且**不透传 model**；
    3. **解析产物**：成功（exit 0）→ stdout 末行即产物 `.md` 路径（convert.py 把 backend 日志全
       导 stderr、stdout 只留路径行）→ `read_text(errors="replace")`（容错口径，P5.0-16）；
    4. **收图（P5.2.1）**：在读出产物 md 文本的**同一 with 块内**（temp 销毁前）扫描图片引用、
       按安全闸收集字节、编号、重写引用为 `images/<stem>/<stem>-<n>.<ext>`，返回 `ConvertResult`；
    5. exit≠0（全后端耗尽 / skill 不支持的格式 / 文件不存在）→ raise `ConvertError`（带 skill stderr）。

    **不**承诺字节确定性（决策P5.2-4）：输出随 mineru/marker/pypdf 版本、输入结构、及外部后端
    是否启用 LLM 增强而变——它是**源**、非可重建派生物。
    """
    script = _skill_convert_script()
    run_cwd = str(cwd or Path.cwd())  # Path 永真，None → Path.cwd()（决策P5.2-12：cwd=KB root）。
    with tempfile.TemporaryDirectory(prefix="guanlan-convert-") as tmp:
        tmp_root = Path(tmp).resolve()
        staged = tmp_root / src.name  # 绝对路径；产物落 tmp（决策P5.2-10）。
        shutil.copyfile(src, staged)
        proc = subprocess.run(
            [sys.executable, str(script), str(staged), "--backend", backend],
            cwd=run_cwd,  # KB root，绝非 tmpdir（决策P5.2-12）；透传环境、不透传 model（决策P5.2-4）。
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ConvertError((proc.stderr or "").strip() or "skill convert.py 非零退出。")
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if not lines:
            raise ConvertError("skill convert.py 成功退出但未输出产物路径。")
        produced = Path(lines[-1].strip())
        if not produced.is_file():
            raise ConvertError(f"skill 报告的产物路径不存在：{produced}")
        md_text = produced.read_text(encoding="utf-8", errors="replace")
        # 图片字节必须在 TemporaryDirectory 销毁前读入（决策P5.2.1-4）。
        return _collect_and_rewrite_images(
            md_text, produced_md=produced, tmp_root=tmp_root, stem=stem
        )


def _default_origin(src: Path, root: Path) -> str:
    """默认 origin 口径（决策P5.2-11）：取**转换前**用户原始 `src`，绝不取 temp staged / 产物路径。

    `src` resolve 为绝对路径后：**库内→相对库根 posix 路径**（可移植、随库迁移仍有效）、
    **库外（常态）→resolved 绝对路径字符串**。`--origin` 显式给定时覆盖此默认。
    """
    resolved = src.expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def _images_dir(target: Path) -> Path:
    """图片落点归口（决策P5.2.1-2）：锚在 **md 目标的父目录**。

    `target` = `<kb>/raw/<slug>.md` → `target.parent` = `<kb>/raw/` → 落点恒为
    `<kb>/raw/images/<slug>/`，与 md 内 `images/<slug>/…` 相对引用对齐。**绝不**写成
    `root/"images"/stem`（那会落到 `<kb>/images/<slug>/`、与引用断裂）。
    """
    return target.parent / "images" / target.stem


def _stage_and_swap_images(
    target: Path, images: tuple[ConvertedImage, ...]
) -> Path | None:
    """落 images 到 `raw/images/<slug>/`（决策P5.2.1-9）：写 staging → rename 换图目录（旧图存 `.bak`）。

    返回 `bak`（overwrite 时旧图目录的暂存路径，否则 None）供 md 提交后清理 / 失败回滚。可失败的
    字节写**全隔离在 sibling staging**（ENOSPC/权限只可能在此，real 与旧 md 未被碰）；随后两次
    `rename` 是同 fs 不分配空间的元数据操作、近乎不可失败。`OSError` 向上抛由命令壳处理。
    """
    real = _images_dir(target)  # raw/images/<slug>/
    images_root = real.parent  # raw/images/
    images_root.mkdir(parents=True, exist_ok=True)
    staging = images_root / f".{real.name}.staging-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    try:
        for img in images:
            (staging / img.name).write_bytes(img.data)  # 唯一可能 ENOSPC/权限失败处
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)  # real 与旧 md 原封不动
        raise
    bak: Path | None = None
    try:
        if real.exists():  # overwrite：旧图整盘存 .bak（决策P5.2.1-6）
            bak = images_root / f".{real.name}.bak-{uuid.uuid4().hex[:8]}"
            os.rename(real, bak)
        os.rename(staging, real)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        if bak is not None and not real.exists():  # staging→real 失败 → 复位旧图
            with contextlib.suppress(OSError):
                os.rename(bak, real)
        raise
    return bak


def _rollback_images(target: Path, bak: Path | None) -> None:
    """md 提交失败时回滚图目录（决策P5.2.1-9）：删本次新换上的 real；overwrite 则从 `.bak` 复位旧图。"""
    real = _images_dir(target)
    shutil.rmtree(real, ignore_errors=True)
    if bak is not None:
        with contextlib.suppress(OSError):
            os.rename(bak, real)


def run_convert(
    src: str | Path,
    *,
    root: Path,
    name: str | None = None,
    origin: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    do_ingest: bool = False,
    backend: str = "auto",
) -> int:
    """命令壳 + 退出码：算 slug → 早存在性预检 → 转换+收图 → 文本准入 → provenance → 落 raw/（md +
    `images/`，或 `--dry-run` 打印）→（可选）串联 ingest。所有用法/转换错误 → `EXIT_USAGE`（决策P5.2-7）。

    落盘顺序「图先换、md 末步提交」（决策P5.2.1-9）：先把图写 staging、rename 换 `raw/images/<slug>/`
    （旧图存 `.bak`），**最后**经 `atomic_write_raw` 提交 md——故任何成功落盘的新 md 永不指向缺失图片；
    md 提交失败则从 `.bak`/`rmtree` 回滚图目录。图片/md 落盘 IO 失败均由本壳转 `EXIT_USAGE`（镜像
    `graph_entrypoint`，不外抛 traceback，决策P5.2.1-10）。
    """
    if dry_run and do_ingest:  # 互斥：dry-run 没落源，无可 ingest（决策P5.2-8）。
        print("`--dry-run` 与 `--ingest` 互斥（dry-run 未落源、无可 ingest）。", file=sys.stderr)
        return EXIT_USAGE

    src_path = Path(src).expanduser()
    if not src_path.is_file():
        print(f"输入文件不存在：{src}", file=sys.stderr)
        return EXIT_USAGE
    if src_path.suffix.lower() == ".md":  # 已是 markdown，不必 convert（决策P5.2-2）。
        print(
            f"{src} 已是 .md——直接 `guanlan ingest`（或 Web 投喂/晋级），不必 convert。",
            file=sys.stderr,
        )
        return EXIT_USAGE

    # provenance 必须在 staging 之前就捕获原始 src（决策P5.2-11）。
    origin_value = (origin or "").strip() or _default_origin(src_path, root)

    # 先算 target/slug（与转换无关，可提前；坏名快失败，决策P5.2.1-6）。
    try:
        target = safe_raw_target(root, name if name is not None else src_path.stem)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    stem = target.stem

    # 早存在性预检（默认不覆盖、非 dry-run → 连转换/收集都不跑，快失败，决策P5.2.1-6）。
    if target.exists() and not overwrite and not dry_run:
        print(
            _RAW_EXISTS_MSG.format(name=target.name),
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        result = convert_to_markdown(src_path, stem=stem, backend=backend, cwd=root)
    except ConvertError as exc:
        print(f"转换失败：{exc}", file=sys.stderr)
        print(
            "提示：高保真后端可 `pip install -U 'mineru[core]'` 或 `pip install marker-pdf`；"
            "纯文本兜底 `pip install pypdf`（仅 PDF）。",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        check_text_admission(result.markdown)  # 空/超限/控制字符 → ValueError（决策P5.2-6）。
        content = apply_origin(result.markdown, origin_value)  # provenance（YAML 安全、绝不裸拼）。
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    nimg = len(result.images)

    if dry_run:  # 转换 + 收集 + 重写照跑，但 raw/（含图片）零写：打印重写后 md 供人审（决策P5.2.1-7）。
        print(content, end="" if content.endswith("\n") else "\n")
        if nimg:
            print(
                f"含 {nimg} 张图片，--dry-run 未落盘；引用已重写为 images/{stem}/{stem}-N.ext。",
                file=sys.stderr,
            )
        if result.skipped:
            print(f"另有 {result.skipped} 处图片引用未准入、原样保留。", file=sys.stderr)
        return EXIT_OK

    # 落图（staging-swap），随后 md 末步提交（决策P5.2.1-9）。
    bak: Path | None = None
    if result.images:
        try:
            bak = _stage_and_swap_images(target, result.images)
        except OSError as exc:  # 写 staging 字节失败 → real/旧 md 原封不动（决策P5.2.1-10）。
            print(f"写 raw/images 失败：{exc}", file=sys.stderr)
            return EXIT_USAGE

    try:
        code = atomic_write_raw(target, content, overwrite)  # 末步提交 md（权威写 + 不覆盖门禁）。
    except OSError as exc:  # 落盘 IO 失败 → 回滚图目录、EXIT_USAGE，不外抛 traceback（镜像 graph）。
        if result.images:
            _rollback_images(target, bak)
        print(f"写 raw/ 失败：{exc}", file=sys.stderr)
        return EXIT_USAGE
    if code != EXIT_OK:  # 罕见 TOCTOU 抢建（早预检已挡常态）→ 回滚图目录，旧态完整复位。
        if result.images:
            _rollback_images(target, bak)
        if code == EXIT_USAGE:
            print(
                _RAW_EXISTS_MSG.format(name=target.name),
                file=sys.stderr,
            )
        return code
    if bak is not None:  # 提交成功 → 清旧图 .bak。
        shutil.rmtree(bak, ignore_errors=True)

    nbytes = target.stat().st_size  # 实际落盘字节（UTF-8），免二次 encode 大字符串。
    receipt = f"✓ 已写 raw/{target.name}（origin: {origin_value}，{nbytes} 字节"
    if nimg:
        receipt += f"，{nimg} 张图片 → raw/images/{stem}/"
    print(receipt + "）。")
    if result.skipped:
        print(
            f"  注：另有 {result.skipped} 处图片引用未准入、原样保留（远程/越界/非图片）。",
            file=sys.stderr,
        )
    if overwrite:
        print(
            "  注：覆盖了已被引用的源可能让 wiki 页失真，建议覆盖后重 `ingest` + `check`。",
            file=sys.stderr,
        )

    if do_ingest:  # 便利串联：用 ingest 自身默认 model（convert 不透传 model，决策P5.2-4）。
        rel = f"raw/{target.name}"
        print(f"→ 串联 ingest {rel}……")
        return run_ingest(rel, root=root)
    return EXIT_OK


def convert_entrypoint(
    root_dir: str | Path,
    *,
    src: str,
    name: str | None,
    origin: str | None,
    overwrite: bool,
    dry_run: bool,
    do_ingest: bool,
    backend: str,
) -> int:
    """`guanlan convert` 的单一落地：校验**可写**库根 → run_convert。零 LLM、宿主写 `raw/`。"""
    try:
        root = require_kb_root(root_dir, writable=True)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code
    return run_convert(
        src,
        root=root,
        name=name,
        origin=origin,
        overwrite=overwrite,
        dry_run=dry_run,
        do_ingest=do_ingest,
        backend=backend,
    )


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.convert` 入口（与 `guanlan convert` 共享 convert_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.convert",
        description="多格式转 markdown 并落 raw/（复用 pdf-to-markdown skill，脚本零 LLM）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("src", help="待转换的文件（PDF/DOCX/PPTX/XLSX/HTML/图片…）")
    parser.add_argument("--name", default=None, help="覆盖目标 slug（默认取原件 stem）")
    parser.add_argument("--origin", default=None, help="显式出处（默认 = 转换前原始 src 路径）")
    parser.add_argument(
        "--overwrite", action="store_true", help="同名 raw/ 已存在时显式覆盖（默认不覆盖）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只把转换结果打到 stdout，raw/ 零写（人审预览）"
    )
    parser.add_argument(
        "--ingest", action="store_true", help="转换成功后串联 `ingest raw/<slug>.md`（默认关）"
    )
    parser.add_argument(
        "--backend", choices=_BACKENDS, default="auto", help="转换后端（透传 skill，默认 auto）"
    )
    args = parser.parse_args(argv)
    return convert_entrypoint(
        args.dir,
        src=args.src,
        name=args.name,
        origin=args.origin,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        do_ingest=args.ingest,
        backend=args.backend,
    )


if __name__ == "__main__":
    raise SystemExit(main())
