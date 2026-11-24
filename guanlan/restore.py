"""恢复撤回条目（P4.36 T2，见 docs/P4.36-Web回收区.md §5）。零 LLM。

- `plan_restore(root, name)`：纯函数、不写盘。资格（§5.1）→ 放回文件的逐项判定（首次 / 续跑，§5.2–5.3）→
  首写前校验（重复与重叠、父路径、归档类型、同卷）→ 补回 `sources` 的逐页判定 → 补登记 index 的判定 →
  与撤回任务的冲突 → `digest`。`ok` 为真才可执行。
- `execute_restore(root, plan)`：**调用方须已独占库级锁、并在锁内重算计划、核对摘要**。顺序是撤回的逆序：
  写 `restore.json`（逐项指纹）→ 放回文件（raw → 摘要页 → 图片目录）→ 补回 `sources` → 补登记 index →
  记 `finished_at`。每步幂等，中断后重新预览、再提交即可向前收敛。
- 只用同卷原子重命名（`os.replace`），不做复制后删除：中断后每一项只可能在两侧之一。
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

import yaml

from . import trash as trashmod
from .pages import index_md_links, split_frontmatter
from .rawio import atomic_write_text, detect_eol, dump_frontmatter, read_text_verbatim
from .reindex import _apply_additions, _format_entry, _join_lines, _section_for, _split_lines
from .trash import RESTORE_RECORD, TRASH, TrashEntry, TrashError

_REMOVE_ACTIVE = ("queued", "waiting_lock", "running", "cancelling")


class RestoreError(TrashError):
    """恢复不能进行（不合资格、有冲突、摘要失配等）。"""


@dataclass
class RestoreItem:
    path: str  # 原位置（相对库根）
    kind: str  # file / dir
    fingerprint: str
    status: str  # pending（待放回）/ done（续跑时已放回）


@dataclass
class PageEdit:
    page: str
    add: list[str]  # 要补回的 slug（按记录顺序）
    mode: str  # clean（按原位补回）/ append（页已改动，追加到末尾）/ noop（都已在）/ skip（不能安全编辑）
    reason: str = ""


@dataclass
class RestorePlan:
    name: str
    kind: str = ""
    subject: str = ""
    blocked: str | None = None  # 不合资格的原因（整条不给恢复）
    resume: bool = False
    items: list[RestoreItem] = field(default_factory=list)
    edits: list[PageEdit] = field(default_factory=list)
    index_add: list[str] = field(default_factory=list)  # 要补登记的摘要页（相对库根）
    conflicts: list[str] = field(default_factory=list)
    index_lines_removed: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    backlinks: list[str] = field(default_factory=list)
    digest: str = ""

    @property
    def ok(self) -> bool:
        return self.blocked is None and not self.conflicts

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


# ── 资格（§5.1）──────────────────────────────────────────────────────────────


def restore_block(entry: TrashEntry) -> str | None:
    """这个条目能不能恢复；不能 → 原因（给人看）。不看盘面冲突，只看条目本身。"""
    if entry.kind == "rollback":
        return "回滚条目不恢复（恢复它等于撤销一次回滚）；需要时按 manifest.jsonl 人工取回。"
    if entry.kind not in ("remove", "remove_batch"):
        return "不是系统撤回条目，无法恢复。"
    if entry.state == "restored":
        return "这个条目已经恢复过了。"
    if entry.invalid:
        return "记录里有不合规的路径，只支持按 manifest 手动恢复：" + "；".join(entry.invalid)
    rec = entry.record
    fmt = rec.get("format")
    if not isinstance(fmt, int) or fmt < trashmod.MANIFEST_FORMAT:
        return "旧格式条目（撤回于回收区恢复功能之前），只支持按 manifest 手动恢复。"
    origin = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
    if origin.get("kind") != "first" and origin.get("kind") != "resume":
        return "撤回时没能确认这是不是首次撤回（当时回收区读不了），只支持按 manifest 手动恢复。"
    if origin.get("kind") != "first":
        return "这是中断后续撤产生的条目，只含原批次的一部分，单独恢复会补不全；请按 manifest 手动恢复。"
    if entry.state == "incomplete":
        return "这次撤回还没做完（有文件仍在原位置），先完成撤回。"
    if entry.state == "partial":
        return "有些记录里的文件已经不知去向，按记录补回会指向不存在的源；请按 manifest 手动恢复。"
    if entry.kind == "remove_batch":
        moved = set(trashmod._as_list(rec.get("moved")))
        group = rec.get("group") if isinstance(rec.get("group"), dict) else {}
        for m in trashmod._as_list(group.get("members")):
            raw = m.get("raw") if isinstance(m, dict) else None
            if not isinstance(raw, str) or raw not in moved:
                return "整批记录里的成员不全在本条目内，只支持按 manifest 手动恢复。"
    return None


# ── 指纹（不跟随任何链接）─────────────────────────────────────────────────────


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(path: Path) -> tuple[str, str]:
    """`(kind, 指纹)`。文件须是普通文件；目录整棵树只能有普通文件和真目录。遇到链接或特殊文件 → `RestoreError`。"""
    st = os.lstat(path)
    if stat.S_ISREG(st.st_mode):
        return "file", _file_sha(path)
    if not stat.S_ISDIR(st.st_mode):
        raise RestoreError(f"{path.name} 不是普通文件或目录")
    rows: list[list[str]] = []
    stack = [path]
    while stack:
        cur = stack.pop()
        with os.scandir(cur) as it:
            for e in it:
                rel = Path(e.path).relative_to(path).as_posix()
                if e.is_symlink():
                    raise RestoreError(f"目录里有软链接：{rel}")
                if e.is_dir(follow_symlinks=False):
                    rows.append([rel + "/", ""])
                    stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    rows.append([rel, _file_sha(Path(e.path))])
                else:
                    raise RestoreError(f"目录里有特殊文件：{rel}")
    rows.sort()
    return "dir", hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


# ── 首写前校验 ────────────────────────────────────────────────────────────────


def _order(path: str) -> tuple[int, str]:
    """放回顺序：raw 文件 → 摘要页 → 图片目录（raw 是定位源的锚，先放回）。"""
    if path.startswith("raw/") and "/images/" not in f"/{path}" and not path.startswith("raw/images/"):
        return 0, path
    if path.startswith("wiki/sources/"):
        return 1, path
    return 2, path


def _parent_problem(root: Path, rel: str) -> tuple[str | None, int | None]:
    """目标父路径能不能逐级建出：途中已有的每一级都须是真目录。返回 `(问题, 最近已有祖先的 st_dev)`。"""
    cur = root
    dev = os.lstat(root).st_dev
    for part in PurePosixPath(rel).parts[:-1]:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return None, dev
        if stat.S_ISLNK(st.st_mode):
            return f"{rel}：路径途经软链接（{cur.relative_to(root).as_posix()}/）", None
        if not stat.S_ISDIR(st.st_mode):
            return f"{rel}：上级 {cur.relative_to(root).as_posix()} 不是目录", None
        dev = st.st_dev
    return None, dev


def _write_problem(root: Path, edir: Path, rel: str) -> str | None:
    """放回要在两侧的上级目录里改目录项：原位置最近的已有上级、回收区里的上级都须可写，否则移到一半才失败。"""
    cur = (root / rel).parent
    while cur != root and not os.path.lexists(cur):
        cur = cur.parent
    if not os.access(cur, os.W_OK):
        return f"{rel}：原位置的上级目录不可写"
    if not os.access((edir / rel).parent, os.W_OK):
        return f"{rel}：回收区里的上级目录不可写"
    return None


def _overlap(paths: list[str]) -> list[str]:
    out = []
    seen: set[str] = set()
    for p in paths:
        if p in seen:
            out.append(f"{p}：清单里重复")
        seen.add(p)
    for a in paths:
        for b in paths:
            if a != b and b.startswith(a + "/"):
                out.append(f"{b}：与 {a} 重叠")
    return out


def _read_restore_record(entry_dir: Path) -> dict | None:
    rec = trashmod._read_json(entry_dir / RESTORE_RECORD)
    return rec if isinstance(rec, dict) else None


# ── sources 与 index ─────────────────────────────────────────────────────────


def _load_page_meta(root: Path, rel: str) -> tuple[dict | None, str, str, str]:
    """`(meta, body, text, 不能编辑的原因)`。原因非空 → 跳过这一页。"""
    path = root / rel
    if trashmod._has_link_component(root, rel):
        return None, "", "", "路径途经软链接"
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, "", "", "页面已不在"
    if not stat.S_ISREG(st.st_mode):
        return None, "", "", "不是普通文件"
    try:
        text = read_text_verbatim(path)
    except UnicodeDecodeError:
        return None, "", "", "不是 UTF-8，无法安全编辑"
    except OSError as exc:
        return None, "", "", f"读不了：{exc}"
    block, body = split_frontmatter(text)
    if block is None:
        return None, "", "", "没有 frontmatter"
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError:
        return None, "", "", "frontmatter 解析失败"
    if not isinstance(meta, dict):
        return None, "", "", "frontmatter 不是映射"
    sources = meta.get("sources")
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        return None, "", "", "sources 不是字符串列表"
    return meta, body, text, ""


def _page_groups(rec: dict) -> list[tuple[str, list[str], list[str]]]:
    """`slug_dropped_from` 按页分组：`[(页, 要补的 slug（按记录序、去重）, before_sources)]`。"""
    groups: dict[str, tuple[list[str], list[str]]] = {}
    for x in trashmod._as_list(rec.get("slug_dropped_from")):
        if not isinstance(x, dict):
            continue
        page, slug = x.get("page"), x.get("dropped_slug")
        before = [s for s in trashmod._as_list(x.get("before_sources")) if isinstance(s, str)]
        if not isinstance(page, str) or not isinstance(slug, str):
            continue
        slugs, _b = groups.setdefault(page, ([], before))
        if slug not in slugs:
            slugs.append(slug)
    return [(p, s, b) for p, (s, b) in groups.items()]


def _edit_for(root: Path, page: str, slugs: list[str], before: list[str]) -> tuple[PageEdit, list[str] | None]:
    """一页的补回判定；第二项是补回后的新 `sources`（noop / skip 为 None）。"""
    meta, _body, _text, why = _load_page_meta(root, page)
    if why:
        return PageEdit(page, slugs, "skip", why), None
    current = list(meta["sources"])
    missing = [s for s in slugs if s not in current]
    if not missing:
        return PageEdit(page, slugs, "noop"), None
    expected = [s for s in before if s not in slugs]
    if current == expected:
        return PageEdit(page, slugs, "clean"), list(before)
    return PageEdit(page, slugs, "append", "页面在撤回后改过，补到末尾，请核对"), current + missing


def _index_state(root: Path) -> tuple[str | None, str | None]:
    """`(index 文本, 不能安全读取的原因)`。"""
    rel = "wiki/index.md"
    if trashmod._has_link_component(root, rel):
        return None, "wiki/index.md 路径途经软链接"
    path = root / rel
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, "wiki/index.md 不存在"
    if not stat.S_ISREG(st.st_mode):
        return None, "wiki/index.md 不是普通文件"
    try:
        return read_text_verbatim(path), None
    except UnicodeDecodeError:
        return None, "wiki/index.md 不是 UTF-8"
    except OSError as exc:
        return None, f"wiki/index.md 读不了：{exc}"


# ── 与撤回任务的冲突（§5.2 第 5 条）──────────────────────────────────────────


def _remove_task_conflicts(root: Path, rec: dict) -> list[str]:
    slugs: set[str] = set()
    if isinstance(rec.get("slug"), str):
        slugs.add(rec["slug"])
    raw_dirs: set[str] = set()
    for p in trashmod._as_list(rec.get("moved")):
        if not isinstance(p, str):
            continue
        if p.startswith("wiki/sources/") and p.endswith(".md"):
            slugs.add(PurePosixPath(p).stem)
        if p.startswith("raw/") and p.endswith(".md"):
            raw_dirs.add(PurePosixPath(p).parent.as_posix())
    group = rec.get("group") if isinstance(rec.get("group"), dict) else {}
    if isinstance(group.get("raw_dir"), str):
        raw_dirs.add(group["raw_dir"])
    out = []
    for env in trashmod._envelopes(root):
        if env.get("kind") != "remove":
            continue
        state = env.get("state")
        params = env.get("params") if isinstance(env.get("params"), dict) else {}
        pending = (
            state in ("interrupted", "failed")
            and params.get("scope") == "all"
            and env.get("remove_writes_began")
            and not env.get("superseded_by")
        )
        if state not in _REMOVE_ACTIVE and not pending:
            continue
        if params.get("source") in slugs or params.get("raw_dir") in raw_dirs:
            out.append(f"撤回任务 {env.get('id')} 涉及同一个源，等它结束（或在任务中心处理）后再恢复")
    return out


# ── 计划 ─────────────────────────────────────────────────────────────────────


def plan_restore(root: Path, name: str) -> RestorePlan:
    """恢复计划（纯函数、不写盘）。条目找不到 → `TrashNotFound`；其余问题记进 `blocked` / `conflicts`。"""
    root = Path(root)
    entry = trashmod.get_entry(root, name)
    plan = RestorePlan(name=entry.name, kind=entry.kind, subject=entry.subject)
    plan.blocked = restore_block(entry)
    if plan.blocked:
        return plan
    trash = trashmod.trash_dir(root)
    assert trash is not None
    edir = trash / entry.name
    rec = entry.record
    plan.index_lines_removed = [s for s in trashmod._as_list(rec.get("index_lines_removed")) if isinstance(s, str)]
    plan.orphaned = [s for s in trashmod._as_list(rec.get("orphaned")) if isinstance(s, str)]
    plan.backlinks = [s for s in trashmod._as_list(rec.get("backlinks")) if isinstance(s, str)]

    moved = [p for p in trashmod._as_list(rec.get("moved")) if isinstance(p, str)]
    moved.sort(key=_order)
    plan.conflicts.extend(_overlap(moved))

    progress = _read_restore_record(edir)
    plan.resume = progress is not None and not progress.get("finished_at")
    recorded: dict[str, dict] = {}
    if plan.resume:
        items = [x for x in trashmod._as_list(progress.get("items")) if isinstance(x, dict)]
        recorded = {x.get("path"): x for x in items if isinstance(x.get("path"), str)}
        if sorted(recorded) != sorted(moved) or len(items) != len(moved):
            plan.conflicts.append("恢复进度记录（restore.json）与撤回记录的文件清单对不上，请人工处理。")
            return _finish(root, plan)

    for rel in moved:
        problem, dev = _parent_problem(root, rel)
        if problem:
            plan.conflicts.append(problem)
            continue
        src, dst = edir / rel, root / rel
        src_there = os.path.lexists(src)
        dst_there = os.path.lexists(dst)  # 悬空链接也算占用
        try:
            if not plan.resume:
                if dst_there:
                    plan.conflicts.append(f"{rel}：原位置已被占用")
                    continue
                if os.lstat(src).st_dev != dev:
                    plan.conflicts.append(f"{rel}：回收区与原位置不在同一个文件系统，不能原子移动")
                    continue
                if wp := _write_problem(root, edir, rel):
                    plan.conflicts.append(wp)
                    continue
                kind, fp = fingerprint(src)
                plan.items.append(RestoreItem(rel, kind, fp, "pending"))
                continue
            want = recorded[rel]
            if src_there and not dst_there:
                kind, fp = fingerprint(src)
                if (kind, fp) != (want.get("kind"), want.get("fingerprint")):
                    plan.conflicts.append(f"{rel}：回收区里的内容在中断后被改过")
                    continue
                if os.lstat(src).st_dev != dev:
                    plan.conflicts.append(f"{rel}：回收区与原位置不在同一个文件系统，不能原子移动")
                    continue
                if wp := _write_problem(root, edir, rel):
                    plan.conflicts.append(wp)
                    continue
                plan.items.append(RestoreItem(rel, kind, fp, "pending"))
            elif not src_there and dst_there:
                kind, fp = fingerprint(dst)
                if (kind, fp) != (want.get("kind"), want.get("fingerprint")):
                    plan.conflicts.append(f"{rel}：原位置的内容与恢复记录不符")
                    continue
                plan.items.append(RestoreItem(rel, kind, fp, "done"))
            elif src_there and dst_there:
                plan.conflicts.append(f"{rel}：回收区和原位置都有，请人工处理")
            else:
                plan.conflicts.append(f"{rel}：回收区和原位置都没有")
        except RestoreError as exc:
            plan.conflicts.append(f"{rel}：{exc}")
        except OSError as exc:
            plan.conflicts.append(f"{rel}：读不了（{exc}）")
    return _finish(root, plan, rec)


def _finish(root: Path, plan: RestorePlan, rec: dict | None = None) -> RestorePlan:
    rec = rec or {}
    for page, slugs, before in _page_groups(rec):
        clean = trashmod._clean_rel(page)
        if clean is None or not clean.startswith("wiki/") or not clean.endswith(".md"):
            plan.edits.append(PageEdit(page, slugs, "skip", "页面路径不合规"))
            continue
        edit, _new = _edit_for(root, clean, slugs, before)
        plan.edits.append(edit)

    summaries = [it.path for it in plan.items if it.path.startswith("wiki/sources/") and it.path.endswith(".md")]
    index_sha = ""
    if summaries:
        text, why = _index_state(root)
        if why:
            plan.conflicts.append(f"无法确认摘要页是否已登记：{why}")
        else:
            linked = index_md_links(text or "")
            plan.index_add = [p for p in summaries if PurePosixPath(p).relative_to("wiki").as_posix() not in linked]
            index_sha = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
            if plan.index_add and not os.access(root / "wiki", os.W_OK):
                plan.conflicts.append("wiki/index.md 所在目录不可写，无法补登记")
    plan.conflicts.extend(_remove_task_conflicts(root, rec))

    payload = {
        "name": plan.name,
        "resume": plan.resume,
        "items": [[it.path, it.kind, it.fingerprint, it.status] for it in plan.items],
        "edits": [[e.page, e.add, e.mode] for e in plan.edits],
        "pages": {e.page: _page_digest(root, e.page) for e in plan.edits},
        "index_add": plan.index_add,
        "index": index_sha,
        "conflicts": plan.conflicts,
    }
    plan.digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return plan


def _page_digest(root: Path, rel: str) -> str:
    try:
        if trashmod._clean_rel(rel) is None or trashmod._has_link_component(root, rel):
            return ""
        return _file_sha(root / rel) if os.path.isfile(root / rel) else ""
    except OSError:
        return ""


# ── 执行 ─────────────────────────────────────────────────────────────────────


@dataclass
class RestoreResult:
    name: str
    moved: list[str] = field(default_factory=list)
    clean: list[str] = field(default_factory=list)
    appended: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    indexed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _prune_empty_dirs(edir: Path) -> None:
    """放回之后条目里留下的空目录（raw/、wiki/sources/…）自下而上删掉；只删空的真目录。"""
    for cur, dirs, _files in os.walk(edir, topdown=False):
        for d in dirs:
            p = Path(cur) / d
            try:
                if not os.path.islink(p):
                    p.rmdir()
            except OSError:
                pass


def execute_restore(
    root: Path, plan: RestorePlan, *, task_id: str | None = None, before_write: Callable[[], None] | None = None
) -> RestoreResult:
    """执行恢复。**调用方须已独占库级锁，并已在锁内重算计划、核对过摘要**。"""
    root = Path(root)
    if not plan.ok:
        raise RestoreError(plan.blocked or "；".join(plan.conflicts))
    trash = trashmod.trash_dir(root)
    assert trash is not None
    edir = trash / plan.name
    if before_write is not None:
        before_write()
    result = RestoreResult(name=plan.name)

    # ① restore.json：第一次移动之前逐项记下指纹；续跑沿用首次的记录（只补记本次任务 id）。
    rec_path = edir / RESTORE_RECORD
    record = _read_restore_record(edir) if plan.resume else None
    if record is None:
        record = {
            "started_at": _now(),
            "task_id": task_id,
            "plan_digest": plan.digest,
            "items": [{"path": it.path, "kind": it.kind, "fingerprint": it.fingerprint} for it in plan.items],
        }
    elif task_id:
        record["task_id"] = task_id
    atomic_write_text(rec_path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")

    # ② 放回文件：raw → 摘要页 → 图片目录（plan.items 已按此排序）。只用同卷原子重命名。
    for it in plan.items:
        if it.status != "pending":
            continue
        dst = root / it.path
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(edir / it.path, dst)
        result.moved.append(it.path)

    # ③ 补回 sources：执行时按当前盘面重判（幂等），一页一次写回。
    for page, slugs, before in _page_groups_from_plan(plan, edir):
        edit, new = _edit_for(root, page, slugs, before)
        if edit.mode == "skip":
            result.skipped.append(page)
            continue
        if new is None:
            continue
        meta, body, text, why = _load_page_meta(root, page)
        if why:
            result.skipped.append(page)
            continue
        meta["sources"] = new
        atomic_write_text(root / page, dump_frontmatter(meta, body, eol=detect_eol(text)))
        (result.clean if edit.mode == "clean" else result.appended).append(page)

    # ④ 补登记 index：只登记本次恢复出来的摘要页。
    summaries = [it.path for it in plan.items if it.path.startswith("wiki/sources/") and it.path.endswith(".md")]
    if summaries:
        text, why = _index_state(root)
        if why:
            raise RestoreError(f"文件已放回，但无法补登记 index：{why}；可跑 guanlan reindex 补上。")
        wiki = root / "wiki"
        linked = index_md_links(text or "")
        by_section: dict[str, list[str]] = {}
        for rel in summaries:
            path = root / rel
            if PurePosixPath(rel).relative_to("wiki").as_posix() in linked or not path.is_file():
                continue
            section = _section_for(path, wiki)
            if section is None:
                continue
            by_section.setdefault(section, []).append(_format_entry(path, wiki))
            result.indexed.append(rel)
        if by_section:
            lines, eol, trailing = _split_lines(text or "")
            lines = _apply_additions(lines, by_section)
            atomic_write_text(wiki / "index.md", _join_lines(lines, eol, trailing))

    # ⑤ 记完成；条目里只留 manifest.json 与 restore.json。
    record["finished_at"] = _now()
    atomic_write_text(rec_path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    _prune_empty_dirs(edir)
    return result


def _page_groups_from_plan(plan: RestorePlan, edir: Path) -> list[tuple[str, list[str], list[str]]]:
    rec = trashmod._read_json(edir / trashmod.REMOVE_MANIFEST)
    groups = _page_groups(rec if isinstance(rec, dict) else {})
    allowed = {e.page for e in plan.edits}  # 预览判为跳过的页执行时同样重判（照样跳过、记进回执）
    return [g for g in groups if g[0] in allowed]


def format_plan(plan: RestorePlan) -> str:
    lines = [f"恢复 {TRASH}/{plan.name}" + ("（续跑）" if plan.resume else "")]
    if plan.blocked:
        lines.append(f"  不能恢复：{plan.blocked}")
        return "\n".join(lines)
    for it in plan.items:
        lines.append(f"  {'放回' if it.status == 'pending' else '已放回'}：{it.path}")
    for e in plan.edits:
        label = {"clean": "补回来源", "append": "追加来源（页已改动，请核对）", "noop": "来源已在", "skip": "跳过"}[e.mode]
        tail = f"（{e.reason}）" if e.reason and e.mode == "skip" else ""
        lines.append(f"  {label}：{e.page} ← {'、'.join(e.add)}{tail}")
    for p in plan.index_add:
        lines.append(f"  补登记 index：{p}")
    for p in plan.orphaned:
        lines.append(f"  提示：{p} 会重新有来源")
    for c in plan.conflicts:
        lines.append(f"  ✗ {c}")
    return "\n".join(lines)


def format_result(r: RestoreResult) -> str:
    out = [f"✓ 已恢复 {TRASH}/{r.name}：放回 {len(r.moved)} 项，干净补回 {len(r.clean)} 页，追加补回 {len(r.appended)} 页"
           f"（要核对），跳过 {len(r.skipped)} 页，补登记 index {len(r.indexed)} 条。"]
    for p in r.appended:
        out.append(f"  请核对：{p}")
    for p in r.skipped:
        out.append(f"  跳过：{p}")
    out.append("  恢复不会重新摄入；撤回期间改写过的页不处理，可跑 guanlan check / audit 复核。")
    return "\n".join(out)


__all__ = [
    "PageEdit",
    "RestoreError",
    "RestoreItem",
    "RestorePlan",
    "RestoreResult",
    "execute_restore",
    "fingerprint",
    "format_plan",
    "format_result",
    "plan_restore",
    "restore_block",
]
