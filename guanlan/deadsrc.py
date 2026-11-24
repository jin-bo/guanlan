"""摘除失效来源（P4.34 §6.3，见 docs/P4.34-Web体检.md）。**零 LLM。**

一页 frontmatter `sources` 里有解析不到 `wiki/sources/<slug>.md` 的项（`check` 报 `sources.unresolved`，
写门禁口径的阻断），**且同页另有能解析的来源**时，把失效项从 `sources` 摘掉。

典型来路：撤回（P3.9）时页面只有被撤回的那一个来源 → 按决策P3.9-3 原样保留、只报孤儿；之后再入库补上新来源，
ingest 只追加、不清旧项——失效项就一直挂着。撤回当时若页面已有其它来源，`remove` 会当场摘掉（`drop_slug`），
这里补的是「先撤回、后补源」这条顺序。

- **判据**：`check.source_resolves`，与 `sources.unresolved` 同一口径。
- **只改 `sources`**：复用 `remove._drop_slugs_from_page`（frontmatter 重出、正文逐字不动、原子写、非 UTF-8 页跳过）。
  正文里对失效来源的 `[[slug]]` 与 `raw/<slug>.md:N` 引用不改，之后照常报断链 / 引文找不到出处（警告）。
- **不摘**：全部来源都失效的页（摘完 `sources` 为空，等于抹掉出处——要从回收区恢复、删页或补源，人来定），
  以及 `sources` 不是字符串列表的页（类型问题归 frontmatter 校验）。
- 不写 `log.md`、不过写门禁（同 `reindex`，决策P3.4-6）；Web 侧作为任务执行，任务记录即审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .check import source_resolves
from .gate import _trusted_sources
from .pages import iter_pages, parse_frontmatter, split_frontmatter
from .remove import _drop_slugs_from_page

__all__ = [
    "DeadSources",
    "PrunePlan",
    "plan_dead_sources",
    "drop_dead_sources",
    "format_drop_result",
    "prunable",
    "plan_prune",
    "prune_pages",
    "format_prune_result",
    "preview_prune",
    "slugs_in_trash",
    "index_readable",
]


@dataclass(frozen=True)
class DeadSources:
    """一页的失效来源。`page` 相对库根 posix；`dead` / `live` 按 `sources` 原顺序去重。"""

    page: str
    dead: list[str]
    live: list[str]

    @property
    def droppable(self) -> bool:
        return bool(self.live)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def plan_dead_sources(root: Path) -> list[DeadSources]:
    """列出所有带失效来源的页（按页路径排序），含可摘（`droppable`）与不可摘的。只读。"""
    root = Path(root)
    wiki = root / "wiki"
    out: list[DeadSources] = []
    if not wiki.is_dir():
        return out
    for path in iter_pages(wiki):
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue  # 非 UTF-8 页：check 只报 page.not_utf8、不报 sources.unresolved，重写也会跳过——这里同样不列
        except OSError:
            continue
        # 严格档解析（同 check）：容错档能读、严格档报 frontmatter 违规的页，check 不报 sources.unresolved，这里也不碰。
        block, _body = split_frontmatter(text)
        meta, fatal = parse_frontmatter(block)
        if fatal is not None or _trusted_sources(meta) is None:
            continue  # 坏 / 缺块、sources 非字符串列表：已是别的阻断违规，这里不碰
        slugs = _unique(list(meta["sources"]))
        dead = [s for s in slugs if not source_resolves(wiki, s)]
        if dead:
            live = [s for s in slugs if s not in dead]
            out.append(DeadSources(page=path.relative_to(root).as_posix(), dead=dead, live=live))
    out.sort(key=lambda d: d.page)
    return out


def drop_dead_sources(
    root: Path, drops: dict[str, list[str]] | None = None
) -> tuple[list[DeadSources], list[DeadSources]]:
    """执行时**重算**再摘。`drops` = 预览里人看过的 `{页: [失效 slug]}`：只在这些页里、只摘其中**执行时仍失效**的 slug
    （预览之后才失效的页或 slug 人没看过，不碰，同删页）。`None` = 不限（只供测试与内部调用）。

    返回 `(已摘, 未摘)`：已摘项的 `dead` 是实际摘掉的那些；未摘 = 全部来源失效的页，以及重写时被跳过的页
    （非 UTF-8 等，`_drop_slugs_from_page` 返回 False）。请求的 slug 已都不失效的页不列：剩下的失效项是预览之后
    才出现的、人没看过，下次体检再报——列进未摘会套上「无法安全重写」的错误原因。"""
    root = Path(root)
    done: list[DeadSources] = []
    left: list[DeadSources] = []
    for item in plan_dead_sources(root):
        dead = item.dead
        if drops is not None:
            if item.page not in drops:
                continue
            seen = set(drops[item.page])
            dead = [s for s in item.dead if s in seen]
            if not dead:
                continue
        if item.droppable and dead and _drop_slugs_from_page(root / item.page, set(dead)):
            done.append(DeadSources(page=item.page, dead=dead, live=item.live))
        else:
            left.append(item)
    return done, left


def format_drop_result(done: list[DeadSources], left: list[DeadSources]) -> str:
    """任务回执（中文，同 CLI 回执风格）。"""
    lines = [f"✓ 摘除失效来源：{len(done)} 页" if done else "· 没有可摘除的失效来源"]
    for d in done:
        lines.append(f"    {d.page}: 摘除 {', '.join(d.dead)}（保留 {len(d.live)} 个有效来源）")
    if left:
        lines.append(f"  ⚠ {len(left)} 页未改：")
        for d in left:
            if d.droppable:
                why = "页面无法安全重写（如非 UTF-8），请手动编辑"
            elif d.page.startswith("wiki/sources/"):
                why = "摘要页全部来源失效，请到回收区恢复或用撤回处理"
            else:
                why = "全部来源都失效，可在体检里整页删除"
            lines.append(f"    {d.page}: {', '.join(d.dead)}（{why}）")
    if done:
        lines.append("  正文里对这些来源的引用未改，会照常报断链 / 引文找不到出处。")
    return "\n".join(lines)


# ── 删除失源页（P4.34 §6.4）──────────────────────────────────────────────────
# 全部来源都失效、`dropsrc` 摘不了的页：由人在预览里确认后整页移入回收区（`.trash/prune@<ts>/`），
# 同时删 index.md 里指向它的登记行。只做这两件：不改别的页（指向它的 `[[链接]]` 之后报断链，预览先列出），
# 不写 log.md（记录文件即审计，同 remove）。回收区里可看、可彻底删；不做自动恢复——按记录文件手动移回。


@dataclass(frozen=True)
class PrunePlan:
    """`pages` 待删页（`DeadSources`）；`index_lines` 将删的 index 登记行；`backlinks` 每页的入链页（删后悬链）。"""

    pages: list[DeadSources]
    index_lines: list[str]
    backlinks: dict[str, list[str]]


def prunable(root: Path, items: list[DeadSources] | None = None) -> list[DeadSources]:
    """可整页删的失源页：全部来源都失效，且不是摘要页（摘要页随源走，归 `remove`）。

    `items`：调用方已算好的 `plan_dead_sources(root)`，免整库再读一遍。"""
    items = plan_dead_sources(root) if items is None else items
    return [d for d in items if not d.droppable and not d.page.startswith("wiki/sources/")]


def _index_targets(pages: list[DeadSources]) -> set[str]:
    return {d.page[len("wiki/"):] for d in pages}


def _backlinks(root: Path, pages: list[DeadSources], *, exclude_batch: bool = True) -> dict[str, list[str]]:
    """每张待删页的入链页（复用 `graph.build_graph` 的解析边，别名 / fold 变体同样算数；排除待删页自身）。
    同 stem 多页共用节点时宁可多列（同 `remove._backlink_pages` 来源侧的取舍）。

    `exclude_batch`：执行时同批被删的页不算入链（它们也走了）；预览传 False——人可能取消勾选其中几页，
    哪些入链留下由前端按勾选现算，服务端不能先替它排除。"""
    from .graph import build_graph

    g = build_graph(Path(root) / "wiki")
    by_path = {n.path: n.id for n in g.nodes}
    gone = {d.page for d in pages} if exclude_batch else set()
    # 反向邻接与 id→路径各建一次：数百张待删页 × 全库边/节点逐页全扫会让预览卡上好几秒
    incoming: dict[str, set[str]] = {}
    for e in g.edges:
        if e.resolved and e.source != e.target:
            incoming.setdefault(e.target, set()).add(e.source)
    paths_by_id: dict[str, list[str]] = {}
    for n in g.nodes:
        paths_by_id.setdefault(n.id, []).append(n.path)
    out: dict[str, list[str]] = {}
    for d in pages:
        nid = by_path.get(d.page)
        if nid is None:
            out[d.page] = []
            continue
        out[d.page] = sorted(
            p for src in incoming.get(nid, ()) for p in paths_by_id.get(src, ()) if p not in gone
        )
    # 图不含 config 页，但 overview.md 是人读的总览、常链向实体页：删后它会断链，必须列出（index.md 的登记行另算）。
    overview = _overview_targets(root)
    for d in pages:
        if d.page in overview and OVERVIEW not in out[d.page]:
            out[d.page] = sorted([*out[d.page], OVERVIEW])
    return out


OVERVIEW = "wiki/overview.md"


def _overview_targets(root: Path) -> set[str]:
    """`wiki/overview.md` 里 `[[…]]` 解析到的页（相对库根 posix）。与 check 同一解析表、同一预处理（注释 / 代码里的不算）。
    读不了 → 空集（只是提示，不挡预览）。"""
    from .pages import WIKILINK_RE, link_resolution_index, link_scan_text, resolve_owner
    from .rawio import read_text_verbatim

    path = Path(root) / OVERVIEW
    if not path.is_file():
        return set()
    try:
        text = read_text_verbatim(path)
    except (OSError, UnicodeDecodeError):
        return set()
    idx = link_resolution_index(Path(root) / "wiki")
    return {o for o in (resolve_owner(raw, idx) for raw in WIKILINK_RE.findall(link_scan_text(text))) if o}


def plan_prune(root: Path, pages: list[str] | None = None) -> PrunePlan:
    """预览 / 执行共用的计划。`pages` 给出时只取其中**当前仍可删**的（人没看过的页不删）。只读。"""
    from .reindex import _prune_dangling
    from .rawio import read_text_verbatim

    root = Path(root)
    chosen = prunable(root)
    if pages is not None:
        wanted = set(pages)
        chosen = [d for d in chosen if d.page in wanted]
    index_path = root / "wiki" / "index.md"
    lines: list[str] = []
    if chosen and index_path.is_file():
        _kept, lines = _prune_dangling(read_text_verbatim(index_path), _index_targets(chosen))
    return PrunePlan(pages=chosen, index_lines=lines, backlinks=_backlinks(root, chosen) if chosen else {})


def prune_pages(root: Path, pages: list[str], *, now=None) -> tuple[Path | None, PrunePlan, list[str]]:
    """执行：重算计划（只删 `pages` 里当前仍可删的），先写记录文件、再移页、最后删 index 行。

    返回 `(回收目录 | None, 计划, 跳过的页)`。跳过 = 提交时在清单里、执行时已不可删（补了来源、已被删、被改名）。"""
    import json
    from datetime import datetime

    from .remove import _move_into_trash, _new_trash_dir, _prune_index_targets
    from .rawio import atomic_write_text
    from .trash import PRUNE_FORMAT, PRUNE_RECORD

    root = Path(root)
    try:
        plan = plan_prune(root, pages)
    except UnicodeDecodeError as exc:  # 在建回收目录、移动任何页之前中止
        raise ValueError("wiki/index.md 不是 UTF-8 编码，删不了其中的登记行；请先转存为 UTF-8 再删页") from exc
    done = {d.page for d in plan.pages}
    skipped = [p for p in dict.fromkeys(pages) if p not in done]
    if not plan.pages:
        return None, plan, skipped
    now = now or datetime.now()
    trash_dir = _new_trash_dir(root, "prune", now)
    record = {
        "format": PRUNE_FORMAT,
        "kind": "prune",
        "pruned_at": now.isoformat(timespec="seconds"),
        "moved": [d.page for d in plan.pages],
        "dead_sources": {d.page: list(d.dead) for d in plan.pages},
        "index_lines_removed": list(plan.index_lines),
        "backlinks": plan.backlinks,
    }
    # 记录先落盘：中途崩溃时回收区能认出这是哪次删页、哪些页还在原处（状态「删页未完成」；不受删除保护，见 `trash.protections`）。
    atomic_write_text(trash_dir / PRUNE_RECORD, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    for d in plan.pages:
        _move_into_trash(root / d.page, root, trash_dir)
    _prune_index_targets(root / "wiki" / "index.md", _index_targets(plan.pages))
    return trash_dir, plan, skipped


def format_prune_result(trash_dir: Path | None, plan: PrunePlan, skipped: list[str], root: Path) -> str:
    if trash_dir is None:
        lines = ["· 没有可删除的页（可能已补上来源或已被删除）"]
    else:
        rel = trash_dir.relative_to(Path(root)).as_posix()
        lines = [f"✓ 已删除 {len(plan.pages)} 页，移入 {rel}/"]
        lines.extend(f"    {d.page}" for d in plan.pages)
        if plan.index_lines:
            lines.append(f"  删除 index.md 登记行 {len(plan.index_lines)} 条")
        linkers = sorted({p for ps in plan.backlinks.values() for p in ps})
        if linkers:
            lines.append(f"  以下 {len(linkers)} 页仍链向被删页，之后报断链：")
            lines.extend(f"    {p}" for p in linkers)
    if skipped:
        lines.append(f"  ⚠ 跳过 {len(skipped)} 页（执行时已不再是全部来源失效的页）：")
        lines.extend(f"    {p}" for p in skipped)
    return "\n".join(lines)


def preview_prune(root: Path, items: list[DeadSources] | None = None) -> list[dict]:
    """预览用：每张可删页各自的 `{page, dead, live, index_lines, backlinks}`。

    与 `plan_prune` 不同，入链**不排除同批其它可删页**、index 行**按页分派**（链向多页的行另列 `index_lines_shared`）：预览里可以逐页取消勾选，
    前端按勾选集合现算「将删的 index 行」（勾选页各自行的并集）与「删后断链的页」（入链里去掉同样被勾选删除的）。
    执行仍以 `prune_pages` 的重算为准。只读。"""
    from .rawio import read_text_verbatim
    from .reindex import _prune_dangling

    root = Path(root)
    pages = prunable(root, items)
    if not pages:
        return []
    index_path = root / "wiki" / "index.md"
    try:
        text = read_text_verbatim(index_path) if index_path.is_file() else None
    except (OSError, UnicodeDecodeError):
        text = None  # index.md 读不了 / 不是 UTF-8：预览照出（不列 index 行），由 index_readable 告诉前端；执行时 prune_pages 会中止
    links = _backlinks(root, pages, exclude_batch=False)
    from .pages import index_md_links

    # 一行可能链向多张可删页：按全体可删页算一次将删行，再按每行的目标页分派——只指向本页的进 `index_lines`，
    # 指向多页的进 `index_lines_shared`（带 `pages`），前端只在这些页全被勾选时才计入（与执行时的重算一致）。
    removed = _prune_dangling(text, _index_targets(pages))[1] if text is not None else []
    tagged = [(ln, sorted(f"wiki/{t}" for t in index_md_links(ln))) for ln in removed]
    out = []
    for d in pages:
        own = [ln for ln, ps in tagged if ps == [d.page]]
        shared = [{"line": ln, "pages": ps} for ln, ps in tagged if len(ps) > 1 and d.page in ps]
        out.append(
            {
                "page": d.page,
                "dead": d.dead,
                "live": d.live,
                "index_lines": own,
                "index_lines_shared": shared,
                "backlinks": links[d.page],
            }
        )
    return out


def slugs_in_trash(root: Path) -> set[str]:
    """回收区里还可恢复的撤回条目所带的源 slug（摘要页 `wiki/sources/<slug>.md` 的 stem）。

    摘除失效来源时要提示这些：恢复只按撤回 manifest 的 `slug_dropped_from` 补回来源，而「先撤回、后补源」的页
    撤回时是独源页、不在那张清单里——摘掉之后恢复那篇原文，这页不会自动重新挂上它（P4.34 §6.3）。读不了回收区 → 空集。"""
    from .restore import restore_block
    from .trash import TrashError, scan

    try:
        entries = scan(Path(root))
    except (TrashError, OSError):
        return set()
    except Exception:  # noqa: BLE001 — 只是预览里的一句提示：回收区记录有意外形态也不能让整个预览 500（同 trash.summary）
        return set()
    out: set[str] = set()
    for e in entries:
        # 只认真能一键恢复的条目：旧格式 / 续撤 / 未完成 / 部分缺失的条目 restore_block 会拒，提示「恢复它就能补回」就是误导
        if e.kind not in ("remove", "remove_batch") or restore_block(e) is not None:
            continue
        for it in e.items:
            # 只认归档侧真有这份摘要页的项：归档缺失（partial）的恢复不了，不能提示「恢复它就能补回」
            if it.archived and it.path.startswith("wiki/sources/") and it.path.endswith(".md"):
                out.add(it.path[len("wiki/sources/"):-3])
    return out


def index_readable(root: Path) -> bool:
    """`wiki/index.md` 能否按 UTF-8 读（不存在算能）。删页要删其中的登记行，读不了就不能删页。"""
    index_path = Path(root) / "wiki" / "index.md"
    if not index_path.is_file():
        return True
    try:
        index_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return True
