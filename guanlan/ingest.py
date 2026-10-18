"""ingest 工作流（P2，见 docs/P2-最小闭环.md §7）。

`guanlan ingest raw/<file>.md`：前置校验 → 取 raw 快照 → Agentao + skill 摄入 → 收尾门禁。
真正的建页步骤在 `guanlan-wiki` skill 里；本模块只做编排与确定性门禁。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .catalog import (
    CATALOG_FORMAT,
    build_catalog,
    overview_outline,
)
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
from .sourcepage import locate_source_page, source_slug

# 薄 prompt：真正步骤在 skill。{rel} = 相对 raw/ 的 posix 路径；{source} = 本篇 source 页定位（wrapper
# 现查）；{context} = 下面两种查重上下文之一。
# overview 结构与页面名录拼在 prompt **末尾**（见 `build_ingest_prompt`），让任务指令先于大块参考数据。
# 注意这**不**带来跨 ingest 的前缀缓存收益：`{rel}` 在开头第一句、`{source}` 也逐篇不同，提示词从那里
# 起就已分叉；名录本身也随每次入库变化。
INGEST_PROMPT = (
    "请按 `guanlan-wiki` skill 的 ingest 工作流摄入资料 `raw/{rel}`："
    "该路径已由 wrapper 校验存在，必须按原样读取，不要替换其中的引号、空格或 CJK 字符；"
    "{source}"
    "{context}"
    "`wiki/overview.md` 不必整读：文末〈overview 结构〉给出它的标题行与行号，"
    "按本篇主题用 `read_file` 的 `offset`/`limit` 读相关章节，需要跨章节综合时再扩展（全文较短时可直接整读）；"
    "决定修改时先读全待改范围再改，`read_file` 回报截断时按提示用 `offset` 续读；"
    "写/更新 source·entity·concept 页（frontmatter 齐全、术语转 `[[wikilink]]`）；"
    "更新 `index.md`；`overview.md` 仅当本次引入新角度、新论断或新矛盾时才改（纯增量细节由 entity/concept 页吸收）；发现矛盾就地标 `## ⚠️ 矛盾与存疑`；"
    "向 `log.md` 追加一条 `## [<日期>] ingest | <标题>`。"
    "遵循 `AGENTAO.md` 硬约束与 conventions 默认。**永不修改 `raw/`。** "
    "**不要运行 shell 命令；读写文件必须使用内置文件工具。不要自行执行 `guanlan check`，wrapper 会在你返回后强制校验。** "
    "完成后用一两句说明触及了哪些页面。"
)

# 本篇 source 页：wrapper 用 `locate_source_page`（与 raw_digest stamp 同一定位归口）现查，三种结论。
_SOURCE_FOUND = (
    "本篇 source 页**已存在**：`{path}`{basis}——先读它再合并更新；桩页也是既有页，补全正文、保留既有 "
    "frontmatter 与来源，不整页覆盖，也不另建一张；"
)
# 文件名对不上、按页内证据认定时补一句，免得 Agent 以为该改名或另建「规范名」页。
_SOURCE_FOUND_BY_EVIDENCE = "（文件名与本篇不同，按其{basis}认定为本篇的 source 页；沿用该页，不改名）"
_SOURCE_CANDIDATES = (
    "wrapper 未能确定本篇 source 页，以下既有页可能同源：{listing}——逐一读后判断：是本篇的同源页就更新它，"
    "只是引用了本篇就不动它；确认都不是才新建{new}；不要自行合并、改名或删除这些页；"
)
_SOURCE_MISSING = (
    "wrapper 未定位到本篇 source 页；检索中若发现同源既有页，优先读取并更新它，确认不存在后才新建{new}；"
)
# 来源名传递：告诉 Agent 本篇在 `sources` 里该写什么，并要求其他来源名先确认存在（防凭空写来源名）。
_SOURCE_SLUG = "本篇在各页 `sources` 里写作 `{slug}`；"
_SOURCE_SLUG_UNDECIDED = "本篇在各页 `sources` 里写所更新或新建的那张 source 页的文件名（不含 `.md`）；"
_SOURCE_OTHERS = "`sources` 里列其他来源前，先确认 `wiki/sources/<该名>.md` 确实存在；"


def _source_clause(kb: Path, rel: str) -> str:
    """prompt 的「本篇 source 页」一段：定位结论 + 本篇 slug + 其他来源须确认存在。"""
    loc = locate_source_page(kb, rel)
    slug = source_slug(rel)
    new = f" `wiki/sources/{slug}.md`" if slug else "（按 skill 约定命名）"
    if loc.page is not None:
        basis = "" if loc.basis == "文件名" else _SOURCE_FOUND_BY_EVIDENCE.format(basis=loc.basis)
        found = _SOURCE_FOUND.format(path=loc.page.relative_to(kb).as_posix(), basis=basis)
        return found + _SOURCE_SLUG.format(slug=loc.page.stem) + _SOURCE_OTHERS
    if loc.candidates:
        listing = "；".join(
            f"`{p.relative_to(kb).as_posix()}`（{why}）" for p, why in loc.candidates
        )
        clause = _SOURCE_CANDIDATES.format(listing=listing, new=new)
        return clause + _SOURCE_SLUG_UNDECIDED + _SOURCE_OTHERS
    slug_clause = _SOURCE_SLUG.format(slug=slug) if slug else _SOURCE_SLUG_UNDECIDED
    return _SOURCE_MISSING.format(new=new) + slug_clause + _SOURCE_OTHERS

# 有名录：查重以名录为准，index 只按分区读写。
_CONTEXT_WITH_CATALOG = (
    "读该 `.md` 源建上下文；**查重用文末〈页面名录〉**，它列出本库除 `wiki/sources/` 外的全部现有页"
    "（{fmt}）——建页前必须对照其中的文件名、标题与别名，已有同名或别名的页就合并更新、不另建；"
    "`wiki/sources/` 下另有 {sources} 页未逐页列出，某个名称可能指向 source 页时，"
    "用 `search_file_content` 或 `glob` 在 `wiki/sources/` 定向查；"
    "**不要整读 `wiki/index.md`**：登记新页时用 `search_file_content` 在 index 里定位对应分区，只读该分区再改；"
)
# 无名录（超预算）：沿用整读 index。
_CONTEXT_WITHOUT_CATALOG = (
    "读该 `.md` 源与 `wiki/index.md` 建上下文（`read_file` 回报截断时按提示用 `offset` 读完全文，"
    "查重需要完整的页名与别名）；"
)


def build_ingest_prompt(kb: Path, rel: str) -> str:
    """组装 ingest prompt：告知本篇 source 页位置；末尾附 overview 结构；名录不超预算就整份附上，
    超了就明确报告并退回整读 index。

    **超预算不截断**（见 `catalog` 模块 docstring）：只给一部分或只给页名，恰好丢掉查重最需要的别名，
    比不给更糟——模型会以为名录是全集。stderr 报告一行，stdout 不受影响。
    """
    source = _source_clause(kb, rel)
    outline = overview_outline(kb / "wiki" / "overview.md")
    tail = [f"〈overview 结构〉{outline or 'overview.md 不存在或读不出'}\n〈overview 结构完〉"]
    catalog = build_catalog(kb / "wiki")
    if catalog.over_budget:
        print(
            f"ℹ 页面名录 {catalog.chars:,} 字（{catalog.pages} 页）超出预算 {catalog.budget:,} 字，"
            "本次不注入名录、退回整读 index.md（不截断名录）。",
            file=sys.stderr,
        )
        context = _CONTEXT_WITHOUT_CATALOG
    else:
        context = _CONTEXT_WITH_CATALOG.format(
            fmt=CATALOG_FORMAT, sources=catalog.omitted_sources
        )
        body = catalog.text or "（本库尚无页面）"
        tail.append(f"〈页面名录〉共 {catalog.pages} 页：\n{body}\n〈页面名录完〉")
    return "\n\n".join(
        [INGEST_PROMPT.format(rel=rel, source=source, context=context), *tail]
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

    source 摘要页由 `find_source_page` 首选按 **`raw_slug(stem)`**（=页身份归口）定位，故凡此键相同的两篇
    raw `.md`——`a/report.md` 与 `b/report.md`、`annual report.md` 与 `annual-report.md`、
    `.report.md` 与 `report.md` ——都会误关联到**同一张**页：一张压另一张、`raw_digest` 只认得一个
    版本（review §1「按 basename 判太窄」）。**只堵不重构**：不新增 slug 方案/哈希/迁移，直接复用既有
    `raw_slug` 算键。**只比 `.md`**（唯一会被 ingest 建 source 页者），故不误伤 convert 的
    `report.pdf`+`report.md` 同源对。残留：`find_source_page` 的 `.`→`-` 回退（`1.报告`↔`1-报告`）
    与逐字文件名回退（桩页）键不同、不在此拦，属窄边角，留文档不追（不复刻 rawio 折叠逻辑以免漂移）。

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
    thinking: str | None = None,
    runner: AgentRunner | None = None,
) -> int:
    """摄入一篇 `.md`，返回退出码（见 errors.py）。`thinking` 透传 `agentao run --thinking`，见 runtime。"""
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
        kb, build_ingest_prompt(kb, rel), model=model, thinking=thinking, runner=runner
    )
    if rc == EXIT_OK:
        _stamp_source_digest(kb, tpath, rel)
    return rc


def _stamp_source_digest(kb: Path, raw_file: Path, rel: str) -> None:
    """门禁后由 wrapper 把本次摄入 raw 的内容指纹 stamp 进对应 source 摘要页（P3.7 §4.2，决策P3.7-3a）。

    **只 stamp 本次那一张** source 摘要页（经 `locate_source_page`：先按 slug 定位、容忍 `.`/`-` 归一
    分歧，未命中再认页内归属依据；只有候选、未认定时不 stamp，绝不顺手刷别页——否则抹平别处真实漂移，
    是致命 bug）。摄入后**重新定位**：Agent 可能刚按 slug 新建了这张页。定位不到 / 该页 frontmatter 本就坏 /
    写后 check 不过 → `stamp_raw_digest` 跳过并回滚、记一句降级到 stderr，**绝不阻断 ingest**（决策P3.7-9）。
    指纹计算与写入是 wrapper 确定性代码、不持 LLM client（红线）。
    """
    source_page = locate_source_page(kb, rel).page
    if source_page is None:
        return  # 无对应 source 页 / 只有候选 → 无声跳过（决策P3.7-5 安全退化、下次自然补）
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
