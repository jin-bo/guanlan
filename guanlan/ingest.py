"""ingest 工作流（P2，见 docs/P2-最小闭环.md §7）。

`guanlan ingest raw/<file>.md`：前置校验 → 取 raw 快照 → Agentao + skill 摄入 → 收尾门禁。
真正的建页步骤在 `guanlan-wiki` skill 里；本模块只做编排与确定性门禁。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .gate import run_guarded_write
from .pages import load_page
from .paths import require_kb_root
from .provenance import (
    RAW_DIGEST_KEY,
    compute_raw_digest,
    format_digest_value,
    parse_digest_value,
    stamp_raw_digest,
)
from .rawio import find_source_page, raw_slug
from .runtime import AgentRunner

# 薄 prompt：真正步骤在 skill。{rel} = 相对 raw/ 的 posix 路径。
INGEST_PROMPT = (
    "请按 `guanlan-wiki` skill 的 ingest 工作流摄入资料 `raw/{rel}`："
    "该路径已由 wrapper 校验存在，必须按原样读取，不要替换其中的引号、空格或 CJK 字符；"
    "读该 `.md` 源与 `wiki/index.md`、`wiki/overview.md` 建上下文；"
    "写/更新 source·entity·concept 页（frontmatter 齐全、术语转 `[[wikilink]]`）；"
    "更新 `index.md` 与 `overview.md`；发现矛盾就地标 `## ⚠️ 矛盾与存疑`；"
    "向 `log.md` 追加一条 `## [<日期>] ingest | <标题>`。"
    "遵循 `AGENTAO.md` 硬约束与 conventions 默认。**永不修改 `raw/`。** "
    "**不要运行 shell 命令；读写文件必须使用内置文件工具。不要自行执行 `guanlan check`，wrapper 会在你返回后强制校验。** "
    "完成后用一两句说明触及了哪些页面。"
)


def _resolve_raw_target(root: Path, target: str) -> Path:
    """把 target 解析为 `<root>/raw/` 下存在的 `.md` 文件，否则抛 GuanlanError(EXIT_USAGE)。"""
    tpath = Path(target).expanduser()
    if not tpath.is_absolute():
        tpath = root / tpath
    tpath = tpath.resolve()

    # raw_dir 也 resolve：raw/ 本身可能是符号链接（指向库外存储），否则 relative_to 永远失败。
    raw_dir = (root / "raw").resolve()
    try:
        tpath.relative_to(raw_dir)
    except ValueError:
        raise GuanlanError(
            f"摄入目标必须位于 raw/ 下：{target}", exit_code=EXIT_USAGE
        ) from None
    if tpath.suffix.lower() != ".md":
        raise GuanlanError(
            f"ingest 只吃 `.md`；多格式请先 `guanlan convert {target}` 转成 raw/<name>.md 再 ingest。",
            exit_code=EXIT_USAGE,
        )
    if not tpath.is_file():
        raise GuanlanError(f"文件不存在：{target}", exit_code=EXIT_USAGE)
    return tpath


def _target_page_owned_by(sources_dir: Path, tpath: Path, raw_dir: Path) -> bool:
    """目标 source 页是否**已确证归属**正被摄入的这个 raw 文件（=合法重摄，可豁免撞名拒绝）。

    仅当：源页存在（`find_source_page` 同 `_stamp_source_digest` 的定位归口）+ frontmatter 可解析出
    `raw_digest` + 其 raw 相对路径 == 本次 `tpath`（review §2 所有权判定）时返回 True。页不存在 / 无或坏
    `raw_digest`（未 stamp / 手写页 / stamp 曾失败）/ 指纹指向**别的** raw 文件 → False：**宁保守拒**
    （所有权未确证时不放行，绝不因一个未 stamp 的同 slug 页而放任另一个 raw 覆盖它）。
    """
    page = find_source_page(sources_dir, tpath.stem)
    if page is None:
        return False
    meta, _body = load_page(page)  # 容错档：坏/缺 frontmatter → meta=None
    if meta is None:
        return False
    parsed = parse_digest_value(meta.get(RAW_DIGEST_KEY))
    if parsed is None:
        return False
    owner_raw_rel, _sha = parsed  # 形如 "raw/a/summary.md"
    try:
        rel = tpath.relative_to(raw_dir).as_posix()
    except ValueError:
        return False
    return owner_raw_rel == f"raw/{rel}"


def _reject_source_slug_collision(raw_dir: Path, tpath: Path, sources_dir: Path) -> None:
    """摄入前挡「`raw/` 里多篇 `.md` 会落到同一张 wiki/sources 摘要页」（见 docs/backlog/notes/llm_wiki-反向评审-v0.6.md §1-C/§2.2）。

    source 摘要页由 `find_source_page` 按 **`raw_slug(stem)`**（=页身份归口）定位，故凡此键相同的两篇
    raw `.md`——`a/report.md` 与 `b/report.md`、`annual report.md` 与 `annual-report.md`、
    `.report.md` 与 `report.md` ——都会误关联到**同一张**页：一张压另一张、`raw_digest` 只认得一个
    版本（review §1「按 basename 判太窄」）。**只堵不重构**：不新增 slug 方案/哈希/迁移，直接复用既有
    `raw_slug` 算键。**只比 `.md`**（唯一会被 ingest 建 source 页者），故不误伤 convert 的
    `report.pdf`+`report.md` 同源对。残留：`find_source_page` 的 `.`→`-` 回退（`1.报告`↔`1-报告`）
    键不同、不在此拦，属窄边角，留文档不追（不复刻 rawio 折叠逻辑以免漂移）。

    **合法重摄豁免（review §2）**：目标页已存在且 `raw_digest` 确证归属**本文件**时放行——重摄既有源页
    是常规操作，同 slug 旁支不是当前属主，不该挡它；真撞（拿一个**非属主**旁支去覆盖属主页）会在**摄入
    那个旁支时**（`_target_page_owned_by` 判否）当场被拒，安全性不减、假阳大降。

    **性能取舍（review §3，已接受）**：每次 ingest 全量 `rglob` 一遍 `raw/`；`run_guarded_write` 里的
    `gate.snapshot_raw` 本就同量级遍历 `raw/`（还带哈希），故本遍历是同阶、更轻的附加成本，不另优化。
    """
    target_slug = raw_slug(tpath.stem)
    if not target_slug:
        return
    dups = [
        p
        for p in raw_dir.rglob("*")
        if p.suffix.lower() == ".md"
        and raw_slug(p.stem) == target_slug
        and p.is_file()
        and p.resolve() != tpath
    ]
    if not dups:
        return
    if _target_page_owned_by(sources_dir, tpath, raw_dir):
        return  # 合法重摄既有属主页 → 放行（review §2）
    listing = "\n".join(
        f"  - raw/{p.relative_to(raw_dir).as_posix()}" for p in [tpath, *dups]
    )
    raise GuanlanError(
        f"raw/ 下多篇 `.md` 的 source 页 slug 都是 `{target_slug}`，摄入会撞进同一张 "
        f"wiki/sources/{target_slug}.md：\n{listing}\n"
        "请把其中之一改名（source 页 slug 需全库唯一）再 ingest。",
        exit_code=EXIT_USAGE,
    )


def run_ingest(
    target: str,
    *,
    root: str | Path = ".",
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> int:
    """摄入一篇 `.md`，返回退出码（见 errors.py）。"""
    try:
        kb = require_kb_root(root, writable=True)
        tpath = _resolve_raw_target(kb, target)
        raw_dir = (kb / "raw").resolve()  # 单算一次：guard 与 rel 共用（review §cleanup）。
        _reject_source_slug_collision(raw_dir, tpath, kb / "wiki" / "sources")
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    rel = tpath.relative_to(raw_dir).as_posix()
    # 开场提示（仅交互式终端，stderr 不污染 stdout 信封/管道）：摄入经 Agentao 跑 LLM，
    # 可能耗时数分钟且全程静默——配合 runtime 的心跳，让用户确认不是卡死（A+ 心跳方案）。
    if sys.stderr.isatty():
        print(
            f"→ 正在摄入 raw/{rel}（经 Agentao + guanlan-wiki skill，可能耗时数分钟）…",
            file=sys.stderr,
            flush=True,
        )
    rc = run_guarded_write(
        kb, INGEST_PROMPT.format(rel=rel), model=model, runner=runner
    )
    if rc == EXIT_OK:
        _stamp_source_digest(kb, tpath, rel)
    return rc


def _stamp_source_digest(kb: Path, raw_file: Path, rel: str) -> None:
    """门禁后由 wrapper 把本次摄入 raw 的内容指纹 stamp 进对应 source 摘要页（P3.7 §4.2，决策P3.7-3a）。

    **只 stamp 本次那一张** source 摘要页（经 `find_source_page` 按 slug 定位、容忍 `.`/`-` 归一
    分歧，绝不顺手刷别页——否则抹平别处真实漂移，是致命 bug）。定位不到 / 该页 frontmatter 本就坏 /
    写后 check 不过 → `stamp_raw_digest` 跳过并回滚、记一句降级到 stderr，**绝不阻断 ingest**（决策P3.7-9）。
    指纹计算与写入是 wrapper 确定性代码、不持 LLM client（红线）。
    """
    source_page = find_source_page(kb / "wiki" / "sources", Path(rel).stem)
    if source_page is None:
        return  # 无对应 source 页（slug 不符约定）→ 无声跳过（决策P3.7-5 安全退化、下次自然补）
    try:
        # raw 字节读取也可能抛 OSError（极小窗口：门禁后被删/权限变）；与 audit `_refresh_one` 同口径
        # 兜底——**绝不让 stamp 在门禁已过后崩掉 ingest**（决策P3.7-9）。
        value = format_digest_value(f"raw/{rel}", compute_raw_digest(raw_file))
        ok = stamp_raw_digest(source_page, value)
    except OSError:
        ok = False
    if not ok:
        print(
            f"ℹ raw_digest 未写入 {source_page.relative_to(kb).as_posix()}"
            "（frontmatter 不可解析或写后校验未过，已回滚；不影响摄入）。",
            file=sys.stderr,
        )
