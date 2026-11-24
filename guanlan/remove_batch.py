"""按原件撤回全部分卷（P4.35，见 docs/P4.35-Web撤回源.md §3.2–§3.6）。**零 LLM、人发起的宿主确定性写。**

一键入库把一本书拆成多卷，落在 `raw/<sample>/<stem>.md`，每卷 frontmatter 带 `split.parent` / `split.index`。
误导入整本书时，逐卷撤回要预览、确认 N 次；本模块把同一原件的全部分卷算成一份整批计划，复用 P3.9 的撤回原语
（来源摘除、index 行删除、移入回收区），但影响面按整批合算：

- **成员**：目标 raw 所在目录 + `split.parent` 组成原件键；只枚举同目录、同 `parent` 的 raw，按 `split.index` 排序。
  `parent` 只用于判成员，不进入任何路径——回收目录名取盘上实际的 raw 目录名。
- **归属**：每卷收集**全部**明确认领它的摘要页（文件名、`raw_digest`、单篇 / 多篇原始资料声明），不用
  `locate_source_page`（它按文件名命中就返回，看不到同时存在的改名认领页）。两张以上 → 冲突；共享摘要页认领了
  批外 raw → 冲突；只在正文提到路径的页只作提示。
- **失源**：用同一份「路径 ↔ 源身份」关系分别算撤回前、撤回后能否解析。撤回前能解析、撤回后解析不到的引用就是
  因本批移动而失效的引用；页上还有撤回后仍能解析的来源则一次摘净，否则原样保留并告警。原本就悬空的不动。
- **执行**：一次一个回收目录、一份 manifest；先摘来源、删 index 行，再逐卷移图片目录、摘要页、raw。

单源撤回（`scope=one`）不走这里，继续用 `remove.run_remove_result`，与 CLI 逐项一致。
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from .gate import _trusted_sources
from .graph import build_graph
from .pages import iter_pages, load_page, load_page_text
from .provenance import RAW_DIGEST_KEY, parse_digest_value
from .rawio import atomic_write_text, find_source_page, read_text_verbatim, same_name_key, source_identity_keys
from .remove import (
    _drop_slugs_from_page,
    _move_into_trash,
    _new_trash_dir,
    _prune_empty_raw_dirs,
    _prune_index_targets,
    canonical_digest,
    path_digest,
)
from .reindex import _prune_dangling
from .sourcepage import _declared_raw_paths

__all__ = [
    "BatchConflict",
    "BatchError",
    "BatchPlan",
    "GroupKey",
    "ReusedIdentity",
    "build_batch_plan",
    "count_members",
    "execute_batch",
    "group_key_for_raw",
]


class BatchError(ValueError):
    """无法可靠识别分卷组、参数不合法（Web 映射 400）。"""


class BatchConflict(ValueError):
    """库的当前状态不允许整批撤回，须人先处理（Web 映射 409）：同原件导入未完成、归属冲突、共享摘要页含批外原文。"""

    def __init__(self, message: str, *, reason: str, details: list[dict] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = details or []


@dataclass(frozen=True)
class GroupKey:
    """原件键：raw 目录（`raw/<sample>`，相对库根 posix）+ frontmatter 的 `split.parent`。"""

    raw_dir: str
    parent: str

    @property
    def sample(self) -> str:
        return PurePosixPath(self.raw_dir).name

    def to_dict(self) -> dict:
        return {"raw_dir": self.raw_dir, "parent": self.parent}


@dataclass(frozen=True)
class ReusedIdentity:
    """任务信封里保存的成员身份（§3.5）：只在「没有任何认领页 ∧ raw 内容未变」时并入。"""

    raw: str
    identity_keys: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class BatchMember:
    raw: str
    index: int
    sha256: str
    identity_keys: tuple[str, ...]  # same_name_key 后、排序
    page: str | None  # 该卷的摘要页（相对库根 posix）；共享摘要页时多卷同一页
    images: str | None  # 同级图片目录（存在才记）
    reused: bool = False  # 身份含信封里复用的旧身份

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "index": self.index,
            "sha256": self.sha256,
            "identity_keys": list(self.identity_keys),
            "page": self.page,
            "images": self.images,
            "reused": self.reused,
        }


@dataclass(frozen=True)
class BatchDrop:
    """一页的来源摘除：`dropped` 按 `before_sources` 原序；`resolved_to[slug]` = 这条引用撤回前解析到的落点。"""

    page: str
    before_sources: list[str]
    dropped: list[str]
    resolved_to: dict[str, dict]

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "before_sources": list(self.before_sources),
            "dropped": list(self.dropped),
            "resolved_to": self.resolved_to,
        }


@dataclass(frozen=True)
class BatchPlan:
    key: GroupKey
    members: list[BatchMember]
    relocate: list[str]
    drops: list[BatchDrop]
    orphans: list[str]
    index_lines: list[str]
    backlinks: list[str]
    shared_pages: list[dict]  # {page, raws}
    hints: list[dict] = field(default_factory=list)  # {raw, pages}：只在正文提到路径，待核实
    digest: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.members)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "scope": "all",
            "group": {
                **self.key.to_dict(),
                "sample": self.key.sample,
                "members": [m.to_dict() for m in self.members],
                "shared_pages": self.shared_pages,
            },
            "relocate": list(self.relocate),
            "drop_slug": [d.to_dict() for d in self.drops],
            "orphans": list(self.orphans),
            "index_lines": list(self.index_lines),
            "backlinks": list(self.backlinks),
            "hints": list(self.hints),
            "plan_digest": self.digest,
        }


# ── 原件与成员 ────────────────────────────────────────────────────────────────


def _split_meta(path: Path) -> tuple[str, object] | None:
    meta, _body = load_page(path)
    split = (meta or {}).get("split")
    if not isinstance(split, dict):
        return None
    parent = split.get("parent")
    if not isinstance(parent, str) or not parent.strip():
        return None
    return parent, split.get("index")


def group_key_for_raw(root: Path, raw_rel: str) -> GroupKey:
    """所选 raw → 原件键。不在 `raw/<sample>/` 下、没有可核实的分卷元数据 → `BatchError`。"""
    p = PurePosixPath(raw_rel)
    if len(p.parts) != 3 or p.parts[0] != "raw":
        raise BatchError("这份原文不在 raw/<原件>/ 分卷目录里，不能按原件撤回全部分卷。")
    found = _split_meta(Path(root) / raw_rel)
    if found is None:
        raise BatchError("这份原文没有可核实的分卷元数据（split.parent），不能按原件撤回全部分卷。")
    return GroupKey(raw_dir=str(p.parent), parent=found[0])


def _matching(root: Path, key: GroupKey) -> list[tuple[str, object]]:
    """同目录、同 `split.parent` 的 raw（相对库根 posix）与其 `split.index` 原值，不校验序号。"""
    d = Path(root) / key.raw_dir
    # 分卷目录须真在 raw/ 下：`raw/<sample>` 是指向别处的符号链接时不枚举，免得整批把库外文件移进回收区
    if not d.is_dir() or d.is_symlink() or d.resolve().parent != (Path(root) / "raw").resolve():
        return []
    out: list[tuple[str, object]] = []
    for path in sorted(d.iterdir()):
        # 只认小写 `.md`：与 `_all_raws` / `remove._raw_file` 的 `rglob("*.md")` 同口径，否则 `.MD` 分卷算进成员、
        # 却不在身份表与文件名认领里，摘要页会被漏移。
        if path.name.startswith(".") or path.suffix != ".md" or not path.is_file():
            continue
        found = _split_meta(path)
        if found is None or found[0] != key.parent:
            continue  # 同目录里没有匹配元数据的文件留在原处
        out.append((path.relative_to(root).as_posix(), found[1]))
    return out


def _enumerate(root: Path, key: GroupKey) -> list[tuple[str, int]]:
    """同目录、同 `split.parent` 的 raw 分卷，按 `split.index` 排序。序号无效或重复 → `BatchError`。"""
    out: list[tuple[str, int]] = []
    for rel, idx in _matching(root, key):
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 1:
            raise BatchError(f"分卷 {rel} 的 split.index 无效，认不准顺序，不能按原件撤回。")
        out.append((rel, idx))
    seen: dict[int, str] = {}
    for rel, idx in out:
        if idx in seen:
            raise BatchError(f"分卷 {seen[idx]} 与 {rel} 的 split.index 都是 {idx}，认不准顺序，不能按原件撤回。")
        seen[idx] = rel
    return sorted(out, key=lambda x: x[1])


def count_members(root: Path, key: GroupKey) -> int:
    """当前盘面上这个原件还剩几卷（待续资格 ③ 用）。不校验序号：序号坏了也是「还有成员」，
    待续任务照样可见，由计划阶段报出 `BatchError`，而不是被当成「本批已无剩余文件」悄悄放过。"""
    return len(_matching(Path(root), key))


# ── 导入冲突 ──────────────────────────────────────────────────────────────────


def check_import_conflict(root: Path, key: GroupKey) -> None:
    """同一原件还有未完成的一键导入（`oneclick.ACTIVE_STATES`）→ `BatchConflict`。

    已生成条目的看目标 raw 是否落在本原件目录；拆分结果还没生成的看清单的 `sample` / `docs[].sample`。"""
    from .oneclick import ACTIVE_STATES, iter_tasks  # 一键入库模块较重：用到时再载入

    names = {same_name_key(key.sample), same_name_key(key.parent)}
    raw_dir = same_name_key(key.raw_dir)
    for task in iter_tasks(Path(root)):
        if task.get("state") not in ACTIVE_STATES:
            continue
        items = [it for it in task.get("items") or [] if isinstance(it, dict)]
        hit = any(
            isinstance(it.get("raw"), str) and same_name_key(str(PurePosixPath(it["raw"]).parent)) == raw_dir
            for it in items
        )
        if not hit:
            # 拆分结果还没生成的原件看样本名。批量任务逐份准备、逐份归并 items：前几份有了条目时，后面还没准备的
            # 那几份仍只能按 `docs[].sample` 认，不能因为 items 非空就整批跳过。
            docs = task.get("docs")
            if docs:
                with_items = {it.get("doc") for it in items}
                samples = [d.get("sample") for i, d in enumerate(docs) if isinstance(d, dict) and i not in with_items]
            else:
                samples = [] if items else [task.get("sample")]
            hit = any(isinstance(s, str) and same_name_key(s) in names for s in samples)
        if hit:
            tid = task.get("task_id") or ""
            raise BatchConflict(
                f"这个原件还有未完成的一键导入任务（{tid}），请先完成或放弃导入，再撤回全部分卷。",
                reason="import_active",
                details=[{"task_id": tid, "state": task.get("state")}],
            )


# ── 盘面索引：摘要页认领与源身份 ──────────────────────────────────────────────────


@dataclass
class _PageInfo:
    rel: str
    stem: str
    digest_path: str | None
    declared: set[str]
    text: str


def _scan_source_pages(root: Path) -> list[_PageInfo]:
    sources_dir = root / "wiki" / "sources"
    out: list[_PageInfo] = []
    if not sources_dir.is_dir():
        return out
    for page in sorted(sources_dir.rglob("*.md")):
        if page.name.startswith(".") or not page.is_file():
            continue
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        meta, body = load_page_text(text)
        parsed = parse_digest_value((meta or {}).get(RAW_DIGEST_KEY))
        out.append(
            _PageInfo(
                rel=page.relative_to(root).as_posix(),
                stem=page.stem,
                digest_path=parsed[0] if parsed else None,
                declared=_declared_raw_paths(body),
                text=text,
            )
        )
    return out


def _all_raws(root: Path) -> list[str]:
    """`raw/` 下全部 `.md`（相对库根 posix），排除各处 `images/` 与隐藏目录。"""
    raw = root / "raw"
    if not raw.is_dir():
        return []
    out = []
    for path in sorted(raw.rglob("*.md")):
        parts = path.relative_to(raw).parts
        if any(part == "images" or part.startswith(".") for part in parts[:-1]) or parts[-1].startswith("."):
            continue
        if path.is_file():
            out.append(path.relative_to(root).as_posix())
    return out


@dataclass
class _Disk:
    raws: list[str]
    raw_keys: dict[str, frozenset[str]]  # raw → 身份键
    filename_page: dict[str, str]  # raw → 按文件名命中的摘要页
    pages: list[_PageInfo]
    page_by_rel: dict[str, _PageInfo]


def _scan(root: Path) -> _Disk:
    sources_dir = root / "wiki" / "sources"
    raws = _all_raws(root)
    raw_keys: dict[str, frozenset[str]] = {}
    filename_page: dict[str, str] = {}
    for rel in raws:
        stem = PurePosixPath(rel).stem
        raw_keys[rel] = source_identity_keys(sources_dir, stem, rel)
        hit = find_source_page(sources_dir, stem, rel)
        if hit is not None:
            filename_page[rel] = hit.relative_to(root).as_posix()
    pages = _scan_source_pages(root)
    return _Disk(raws, raw_keys, filename_page, pages, {p.rel: p for p in pages})


def _claims(disk: _Disk, raw_rel: str) -> tuple[dict[str, list[str]], list[str]]:
    """`raw_rel` 的全部明确认领 `{页: [依据…]}` 与只在正文提到路径的提示页（不提前返回）。"""
    key = same_name_key(raw_rel)
    claims: dict[str, list[str]] = {}
    hits: list[str] = []
    fp = disk.filename_page.get(raw_rel)
    if fp is not None:
        claims.setdefault(fp, []).append("文件名")
    forms = {raw_rel, key, unicodedata.normalize("NFD", raw_rel)}
    for info in disk.pages:
        bases = []
        if info.digest_path and same_name_key(info.digest_path) == key:
            bases.append("raw_digest")
        declared = {same_name_key(x) for x in info.declared}
        if key in declared:
            bases.append("原始资料声明" if len(declared) == 1 else "多篇原始资料声明")
        if bases:
            claims.setdefault(info.rel, []).extend(bases)
        elif info.rel not in claims and any(f in info.text for f in forms):
            hits.append(info.rel)
    return claims, hits


def _page_claimed_raws(disk: _Disk, page: str) -> set[str]:
    """一张摘要页认领的全部 raw（same_name_key 后）：文件名命中 ∪ `raw_digest` ∪ 原始资料声明。"""
    info = disk.page_by_rel.get(page)
    out = {same_name_key(r) for r, p in disk.filename_page.items() if p == page}
    if info is not None:
        if info.digest_path:
            out.add(same_name_key(info.digest_path))
        out |= {same_name_key(x) for x in info.declared}
    return out


# ── 整批计划 ──────────────────────────────────────────────────────────────────


def build_batch_plan(
    root: Path,
    key: GroupKey,
    *,
    reuse: list[ReusedIdentity] | None = None,
    check_imports: bool = True,
) -> BatchPlan:
    """算一份整批撤回计划。**纯函数、只读、不写盘。** 冲突 → `BatchConflict`；识别不了 → `BatchError`。

    `reuse`：任务入口从旧信封带来的成员身份（§3.5），只在「没有任何认领页 ∧ raw sha256 未变」时并入。
    成员为空（本批已撤完）时返回 `ok=False` 的空计划。"""
    root = Path(root)
    if check_imports:
        check_import_conflict(root, key)
    found = _enumerate(root, key)
    if not found:
        return BatchPlan(key, [], [], [], [], [], [], [], digest="")
    disk = _scan(root)
    batch_raws = [rel for rel, _ in found]
    batch_keys = {same_name_key(r) for r in batch_raws}
    reuse_by_raw = {same_name_key(r.raw): r for r in reuse or []}

    conflicts: list[dict] = []
    outside: list[dict] = []
    hints: list[dict] = []
    members: list[BatchMember] = []
    page_members: dict[str, list[str]] = {}
    on_disk = {same_name_key(r) for r in disk.raws}  # 盘上真有的原文（判共享摘要页的批外认领用）
    for rel, idx in found:
        claims, hint_pages = _claims(disk, rel)
        if hint_pages:
            hints.append({"raw": rel, "pages": hint_pages})
        page: str | None = None
        if len(claims) > 1:
            conflicts.append({"raw": rel, "pages": [{"page": p, "basis": b} for p, b in sorted(claims.items())]})
        elif claims:
            page = next(iter(claims))
            extra = sorted(_page_claimed_raws(disk, page) - batch_keys)
            # 只算盘上真有的批外原文：指向已不在的路径的声明不让整批卡住
            extra = [r for r in extra if r in on_disk]
            if extra:
                outside.append({"page": page, "raws": extra, "basis": claims[page]})
            page_members.setdefault(page, []).append(rel)
        sha = path_digest(root, rel)
        keys = set(disk.raw_keys.get(rel, frozenset()))
        if page is not None:
            keys.add(same_name_key(PurePosixPath(page).stem))
        reused = False
        stored = reuse_by_raw.get(same_name_key(rel))
        if not claims and stored is not None and stored.sha256 == sha:
            keys |= {same_name_key(k) for k in stored.identity_keys}
            reused = True
        images_rel = (PurePosixPath(rel).parent / "images" / PurePosixPath(rel).stem).as_posix()
        members.append(
            BatchMember(
                raw=rel,
                index=idx,
                sha256=sha if isinstance(sha, str) else "",
                identity_keys=tuple(sorted(keys)),
                page=page,
                images=images_rel if (root / images_rel).exists() else None,
                reused=reused,
            )
        )
    if conflicts:
        raise BatchConflict(
            "有分卷同时被两张以上的摘要页认领，归属无法确定；请先处理这些页，再撤回全部分卷。",
            reason="claim_conflict",
            details=conflicts,
        )
    if outside:
        raise BatchConflict(
            "有摘要页同时认领了本原件之外的原文；撤回会让那些原文失去摘要页，请先处理这些页。",
            reason="shared_page_outside",
            details=outside,
        )

    # 待移动：每卷依序 图片目录 → 摘要页 → raw；共享摘要页随它认领的第一卷移
    relocate: list[str] = []
    for m in members:
        for rel in (m.images, m.page, m.raw):
            if rel and rel not in relocate:
                relocate.append(rel)
    moving = set(relocate)
    moving_pages = {m.page for m in members if m.page}
    shared_pages = [{"page": p, "raws": raws} for p, raws in sorted(page_members.items()) if len(raws) > 1]

    # 撤回前 / 撤回后能解析的源身份
    reused_keys = {k for m in members if m.reused for k in m.identity_keys}
    page_stems = {same_name_key(p.stem): p.rel for p in disk.pages}
    before = {k for ks in disk.raw_keys.values() for k in ks} | set(page_stems) | reused_keys
    after = {k for r, ks in disk.raw_keys.items() if r not in moving for k in ks}
    # 按页逐张算（不走 page_stems）：不同子目录里同 stem 的两张页在字典里只留一张，留下的若是待移动页，
    # 另一张留在库里的页的 stem 会被误判为撤回后解析不到。
    after |= {same_name_key(p.stem) for p in disk.pages if p.rel not in moving_pages}

    def resolved_to(slug_key: str) -> dict:
        page = page_stems.get(slug_key)
        if page in moving_pages:
            return {"page": page, "raws": [m.raw for m in members if m.page == page]}
        return {"raws": [m.raw for m in members if slug_key in m.identity_keys]}

    drops: list[BatchDrop] = []
    orphans: list[str] = []
    wiki = root / "wiki"
    for path in iter_pages(wiki):
        rel = path.relative_to(root).as_posix()
        if rel in moving_pages:
            continue
        meta, _body = load_page(path)
        if _trusted_sources(meta) is None:
            continue
        raw_list = (meta or {}).get("sources")
        sources = [s for s in raw_list if isinstance(s, str)] if isinstance(raw_list, list) else []
        invalid = [s for s in sources if same_name_key(s) in before and same_name_key(s) not in after]
        if not invalid:
            continue
        remaining = [s for s in sources if s not in invalid and same_name_key(s) in after]
        if remaining:
            drops.append(
                BatchDrop(
                    page=rel,
                    before_sources=list(sources),
                    dropped=invalid,
                    resolved_to={s: resolved_to(same_name_key(s)) for s in invalid},
                )
            )
        else:
            orphans.append(rel)

    index_targets = {PurePosixPath(p).relative_to("wiki").as_posix() for p in moving_pages}
    index_lines: list[str] = []
    index_path = wiki / "index.md"
    if index_targets and index_path.is_file():
        _kept, index_lines = _prune_dangling(read_text_verbatim(index_path), index_targets)

    backlinks = _batch_backlinks(root, moving_pages)

    payload = {
        "scope": "all",
        "group": key.to_dict(),
        "members": [[m.raw, list(m.identity_keys), m.sha256] for m in members],
        "relocate": [[rel, path_digest(root, rel)] for rel in relocate],
        "drop_slug": [d.to_dict() for d in drops],
        "orphans": orphans,
        "index_lines": index_lines,
        "backlinks": backlinks,
        "shared_pages": shared_pages,
    }
    return BatchPlan(
        key=key,
        members=members,
        relocate=relocate,
        drops=drops,
        orphans=orphans,
        index_lines=index_lines,
        backlinks=backlinks,
        shared_pages=shared_pages,
        hints=hints,
        digest=canonical_digest(payload),
    )


def _batch_backlinks(root: Path, moving_pages: set[str]) -> list[str]:
    """批外保留页到待移动摘要页的入链（批内摘要页互链不报）。同-stem 歧义的目标跳过（同 P3.9 `_backlink_pages`）。"""
    if not moving_pages:
        return []
    g = build_graph(root / "wiki")
    by_id: dict[str, list[str]] = {}
    for n in g.nodes:
        by_id.setdefault(n.id, []).append(n.path)
    targets = set()
    for page in moving_pages:
        tid = PurePosixPath(page).stem.lower()
        if by_id.get(tid) == [page]:
            targets.add(tid)
    linkers = {e.source for e in g.edges if e.resolved and e.target in targets and e.source not in targets}
    return sorted({p for lid in linkers for p in by_id.get(lid, []) if p not in moving_pages})


# ── 执行 ──────────────────────────────────────────────────────────────────────


def _safe_trash_name(root: Path, name: str) -> str:
    """回收目录名取 raw 目录名：须是单个路径成分，建出来仍在 `.trash/` 内。"""
    if not name or name in (".", "..") or "/" in name or "\\" in name or name.startswith(".") or "\0" in name:
        raise BatchError(f"原件目录名 {name!r} 不能用作回收目录名，没有执行任何改动。")
    trash = (root / ".trash").resolve()
    target = (trash / f"{name}@x").resolve()
    if target.parent != trash:
        raise BatchError(f"原件目录名 {name!r} 不能用作回收目录名，没有执行任何改动。")
    return name


def execute_batch(root: Path, plan: BatchPlan, *, before_write: Callable[[], None] | None = None) -> Path:
    """执行整批计划，返回回收目录。调用方须已在写锁内重算并核对过摘要。

    顺序：校验回收目录名 → `before_write()`（抛异常则零写入）→ 建 `.trash/<sample>@<ts>/` → 写 manifest →
    一次摘净各页的失效引用 → 删 index 行 → 逐卷移图片目录、摘要页、raw → 清空分卷目录。每步幂等，
    中断后重新预览、再提交即可向前收敛。"""
    root = Path(root)
    if not plan.ok:
        raise BatchError("本批已无剩余文件可撤回。")
    name = _safe_trash_name(root, plan.key.sample)
    if before_write is not None:
        before_write()
    now = datetime.now()
    trash_dir = _new_trash_dir(root, name, now)
    manifest = {
        "scope": "all",
        "removed_at": now.isoformat(timespec="seconds"),
        "group": {
            **plan.key.to_dict(),
            "trash_name": name,
            "members": [
                {"raw": m.raw, "index": m.index, "identity_keys": list(m.identity_keys), "page": m.page, "sha256": m.sha256}
                for m in plan.members
            ],
            "shared_pages": plan.shared_pages,
        },
        "moved": list(plan.relocate),  # 计划移动的文件：中断时可能只移了一部分，恢复以回收目录里实际存在的为准
        "slug_dropped_from": [
            {
                "page": d.page,
                "dropped_slug": slug,
                "before_sources": list(d.before_sources),
                "resolved_to": d.resolved_to[slug],
            }
            for d in plan.drops
            for slug in d.dropped
        ],
        "orphaned": list(plan.orphans),
        "backlinks": list(plan.backlinks),
        "index_lines_removed": list(plan.index_lines),
    }
    atomic_write_text(trash_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    for d in plan.drops:
        _drop_slugs_from_page(root / d.page, set(d.dropped))
    targets = {PurePosixPath(m.page).relative_to("wiki").as_posix() for m in plan.members if m.page}
    if targets:
        _prune_index_targets(root / "wiki" / "index.md", targets)
    for rel in plan.relocate:
        _move_into_trash(root / rel, root, trash_dir)
    for m in plan.members:
        _prune_empty_raw_dirs(root, [m.raw], m.raw)
    return trash_dir


def format_batch(plan: BatchPlan, *, executed: bool, trash_dir: str | None = None) -> str:
    """整批回执（人类可读）。"""
    verb = "已撤回" if executed else "将撤回（预览，未写盘）"
    out = [f"· 按原件撤回 {plan.key.raw_dir}/（{len(plan.members)} 卷）：{verb}"]
    if trash_dir:
        out.append(f"    回收目录：{trash_dir}/（含 manifest.json）")
    for m in plan.members:
        out.append(f"    第 {m.index} 卷：{m.raw}" + (f"（摘要页 {m.page}）" if m.page else ""))
    for d in plan.drops:
        rest = len(d.before_sources) - len(d.dropped)
        out.append(f"    摘来源 {', '.join(d.dropped)}（剩 {rest} 源）：{d.page}")
    for ln in plan.index_lines:
        out.append(f"    删 index 行：{ln}")
    if plan.orphans:
        out.append("    ⚠ 撤回后失去有效来源的页（原样保留）：")
        out.extend(f"        {o}" for o in plan.orphans)
    if plan.backlinks:
        out.append("    ⚠ 撤回后会出现悬链的页（不改）：")
        out.extend(f"        {b}" for b in plan.backlinks)
    if plan.drops or plan.orphans:
        out.append("    注：被摘来源或失源的页正文可能仍含已撤资料的内容，建议重 ingest 或审计复核。")
    return "\n".join(out)
