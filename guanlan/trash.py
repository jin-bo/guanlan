"""回收区管理（P4.36 T1，见 docs/P4.36-Web回收区.md）：浏览 `.trash/`、单条彻底删除。零 LLM。

- `scan(root)`：纯函数、不写盘。一律扫 `.trash/` 得出，不从任务记录反查（CLI 撤回没有任务记录）。
- **目录安全与条目识别分开判**（§3.1）：`.trash` 本身须是真目录；条目是它的直接子项、本身不是软链接才往里看；
  扫描与删除都不跟随任何链接。种类只按「名字形态 + 能解析的记录文件」识别，其余一律 `unknown`——照样列出、可删。
- **记录文件不可信**（§3.3）：它在库内，Agent 也能写。`moved` / `saved_as` 等路径先核形状再用，不合规的项标 `invalid`。
- **彻底删除**（§4）：一次一条、不可恢复。必须独占库级锁（不接受锁文件建不出时的退化，也不接受嵌套放行）；
  在锁内按实际续跑条件判删除保护，匹配与删除都用扫到的实际目录名。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import kblock
from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .paths import require_kb_root

TRASH = ".trash"
REMOVE_MANIFEST = "manifest.json"
ROLLBACK_LOG = "manifest.jsonl"  # 与 preimage.TRASH_LOG 同名（回滚的逐项记录）
RESTORE_RECORD = "restore.json"  # T2 恢复进度（§5.3）；T1 只读它判状态与删除保护

# 系统条目的目录名形态：`<名字>@<YYYYmmddTHHMMSS>[_微秒][-n]`（撤回 / 整批 / 回滚三种都是这个形）。
_SYSTEM_NAME = re.compile(r"^[^/\\]+@(\d{8}T\d{6})(?:_\d{6})?(?:-\d+)?$")
_JOB_NAME = re.compile(r"^job-(\d{8}-\d{6}-[a-z]+-[0-9a-f]{4})@")
_TASK_ID = re.compile(r"^\d{8}-\d{6}-[a-z]+-[0-9a-f]{4}$")

_REMOVE_PREFIXES = ("raw/", "wiki/sources/")
_ROLLBACK_PREFIXES = ("wiki/",)
_ROLLBACK_FILES = ("AGENTAO.md", "SCHEMA.md")
_CONFIG_PAGES = ("wiki/index.md", "wiki/log.md", "wiki/overview.md")

# 回滚还可能续跑的任务状态（§4.2 第 2 条）：取消后正在回滚 / 回滚在跑或排队；或中断 / 回滚失败且前像仍在。
_ROLLBACK_BUSY_STATES = ("cancelling", "rolling_back")
_ROLLBACK_RESUMABLE_STATES = ("interrupted", "rollback_failed")
_TASK_ACTIVE_STATES = ("queued", "waiting_lock", "running", "cancelling", "rolling_back")


class TrashError(GuanlanError):
    """回收区操作失败（目录不安全、条目找不到、锁拿不到等）。"""


class TrashNotFound(TrashError):
    pass


class TrashProtected(TrashError):
    """条目受删除保护（§4.2）。`reasons` 是给人看的原因列表。"""

    def __init__(self, name: str, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__(f"条目 {name} 受保护，不能删除：" + "；".join(reasons))


class TrashLockError(TrashError):
    """没能独占库级锁（被占用、锁文件建不出、或只拿到嵌套放行）。"""


@dataclass
class TrashItem:
    """记录文件里的一项：撤回的 `moved` 项，或回滚的 `{path, saved_as}`。"""

    path: str  # 原位置（相对库根）
    saved_as: str  # 在条目里的位置（相对条目根）
    archived: bool = False  # 归档侧还在
    origin_exists: bool = False  # 原位置有东西（撤回判 incomplete 用；回滚不看）
    invalid: str | None = None  # 路径不合规的原因

    def to_dict(self) -> dict:
        d = {"path": self.path, "saved_as": self.saved_as, "archived": self.archived, "origin_exists": self.origin_exists}
        if self.invalid:
            d["invalid"] = self.invalid
        return d


@dataclass
class TrashEntry:
    name: str
    kind: str  # remove / remove_batch / rollback / unknown
    state: str  # complete / incomplete / partial / restoring / restored / unknown
    when: str
    size: int
    files: int
    is_dir: bool
    subject: str = ""
    origin: dict | None = None
    items: list[TrashItem] = field(default_factory=list)
    record: dict = field(default_factory=dict)  # 解析后的记录文件（详情用）
    restore: dict | None = None  # restore.json（有则记）
    note: str = ""  # unknown 的原因等
    protected: list[str] = field(default_factory=list)

    @property
    def invalid(self) -> list[str]:
        return [f"{it.path}：{it.invalid}" for it in self.items if it.invalid]

    def brief(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "state": self.state,
            "when": self.when,
            "size": self.size,
            "files": self.files,
            "subject": self.subject,
            "origin": self.origin,
            "invalid": bool(self.invalid),
            "protected": list(self.protected),
            "note": self.note,
        }

    def detail(self) -> dict:
        d = self.brief()
        d["items"] = [it.to_dict() for it in self.items]
        d["invalid_items"] = self.invalid
        rec = self.record
        if self.kind in ("remove", "remove_batch"):
            d["slug_dropped_from"] = [
                {k: x.get(k) for k in ("page", "dropped_slug", "before_sources")}
                for x in _as_list(rec.get("slug_dropped_from"))
                if isinstance(x, dict)
            ]
            for key in ("index_lines_removed", "orphaned", "backlinks"):
                d[key] = [str(x) for x in _as_list(rec.get(key)) if isinstance(x, str)]
            if self.kind == "remove_batch":
                g = rec.get("group") if isinstance(rec.get("group"), dict) else {}
                d["group"] = {
                    "raw_dir": g.get("raw_dir"),
                    "members": [
                        {"raw": m.get("raw"), "index": m.get("index"), "page": m.get("page")}
                        for m in _as_list(g.get("members"))
                        if isinstance(m, dict)
                    ],
                }
        elif self.kind == "rollback":
            d["meta"] = {k: v for k, v in rec.items() if isinstance(v, (str, int, float, bool))}
        if self.restore is not None:
            d["restore"] = {k: v for k, v in self.restore.items() if k in ("started_at", "finished_at", "task_id")}
        return d


# ── 目录安全 ────────────────────────────────────────────────────────────────


def trash_dir(root: Path) -> Path | None:
    """`.trash` 的位置；不存在 → None（回收区为空）。是软链接、不是目录、解析后不在库根下 → `TrashError`。"""
    root = Path(root)
    path = root / TRASH
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        raise TrashError(f"{TRASH} 是软链接，为安全起见不扫描也不删除，请人工处理。")
    if not stat.S_ISDIR(st.st_mode):
        raise TrashError(f"{TRASH} 不是目录，请人工处理。")
    if path.resolve().parent != root.resolve():
        raise TrashError(f"{TRASH} 解析后不在库根下，请人工处理。")
    return path


def _walk_size(path: Path) -> tuple[int, int]:
    """大小与文件数，不跟随任何链接：链接按自身计一个文件、计自身大小。"""
    try:
        st = os.lstat(path)
    except OSError:
        return 0, 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size, 1
    size = files = 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        else:
                            size += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return size, files


def _clean_rel(rel: object) -> str | None:
    """记录文件里的路径：须是相对 posix、无 `..`、无 `.`、无空段、无反斜杠与 NUL。合规返回原串，否则 None。"""
    if not isinstance(rel, str) or not rel or "\\" in rel or "\0" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return rel


def _has_link_component(base: Path, rel: str) -> bool:
    """`base/rel` 途经的各级目录（不含最后一级）里有没有软链接。不存在的级别到此为止。"""
    cur = base
    for part in PurePosixPath(rel).parts[:-1]:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except OSError:
            return False
        if stat.S_ISLNK(st.st_mode):
            return True
    return False


def _lexists(base: Path, rel: str) -> bool:
    """`base/rel` 在不在（不跟最后一级链接；途经链接的视为不在，免得被引到别处）。"""
    if _has_link_component(base, rel):
        return False
    return os.path.lexists(base / rel)


def _check_remove_path(rel: object) -> tuple[str, str | None]:
    clean = _clean_rel(rel)
    if clean is None:
        return str(rel), "路径不合规（须是库内相对路径、不含 ..）"
    if not clean.startswith(_REMOVE_PREFIXES):
        return clean, "不在 raw/ 或 wiki/sources/ 下"
    return clean, None


def _check_rollback_path(rel: object) -> tuple[str, str | None]:
    clean = _clean_rel(rel)
    if clean is None:
        return str(rel), "路径不合规（须是库内相对路径、不含 ..）"
    if not (clean.startswith(_ROLLBACK_PREFIXES) or clean in _ROLLBACK_FILES):
        return clean, "不在前像范围（wiki/、AGENTAO.md、SCHEMA.md）内"
    return clean, None


# ── 记录文件 ────────────────────────────────────────────────────────────────


def _as_list(value: object) -> list:
    """记录文件里应为列表的字段：不是列表（被改成数字、字符串、对象）一律当空表，不让扫描抛 TypeError。"""
    return value if isinstance(value, list) else []


def _read_json(path: Path) -> object:
    """读一个 JSON 文件；不是普通文件（链接、目录）、读不了、解析不了 → None。"""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_rollback_log(path: Path) -> tuple[dict, list[dict]] | None:
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    meta: dict | None = None
    actions: list[dict] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and "meta" in rec and meta is None:
            meta = rec["meta"] if isinstance(rec["meta"], dict) else {}
        elif isinstance(rec, dict) and "path" in rec:
            actions.append(rec)
    if meta is None:
        return None
    return meta, actions


def _name_time(name: str) -> str | None:
    m = _SYSTEM_NAME.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").isoformat(timespec="seconds")
    except ValueError:
        return None


def _mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(os.lstat(path).st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def _entry(root: Path, trash: Path, name: str) -> TrashEntry:
    path = trash / name
    size, files = _walk_size(path)
    try:
        st = os.lstat(path)
        is_dir = stat.S_ISDIR(st.st_mode)
        is_link = stat.S_ISLNK(st.st_mode)
    except OSError:
        is_dir = is_link = False
    when = _name_time(name) or _mtime(path)
    base = TrashEntry(name=name, kind="unknown", state="unknown", when=when, size=size, files=files, is_dir=is_dir)
    if is_link:
        base.note = "条目是软链接（不跟随）"
        return base
    if not is_dir:
        base.note = "不是目录（手动放入的文件）"
        return base
    if not _SYSTEM_NAME.match(name):
        base.note = "名字不是系统条目的形态"
        return base

    manifest = _read_json(path / REMOVE_MANIFEST)
    if isinstance(manifest, dict) and isinstance(manifest.get("moved"), list):
        return _remove_entry(root, path, base, manifest)
    log = _read_rollback_log(path / ROLLBACK_LOG)
    if log is not None:
        return _rollback_entry(root, path, base, *log)
    base.note = "没有能解析的记录文件"
    return base


def _remove_entry(root: Path, path: Path, e: TrashEntry, manifest: dict) -> TrashEntry:
    e.kind = "remove_batch" if isinstance(manifest.get("group"), dict) else "remove"
    e.record = manifest
    if isinstance(manifest.get("removed_at"), str):
        e.when = manifest["removed_at"]
    if e.kind == "remove":
        e.subject = str(manifest.get("slug") or "")
    else:
        g = manifest["group"]
        members = g.get("members") if isinstance(g.get("members"), list) else []
        e.subject = f"{g.get('raw_dir') or ''} · {len(members)}"
    e.origin = manifest.get("origin") if isinstance(manifest.get("origin"), dict) else None
    for rel in manifest["moved"]:
        clean, bad = _check_remove_path(rel)
        item = TrashItem(path=clean, saved_as=clean, invalid=bad)
        if bad is None and (_has_link_component(path, clean) or _has_link_component(root, clean)):
            item.invalid = "路径途经软链接"
        if _clean_rel(rel) is not None:
            # 不合规的项不给恢复，但仍参与状态判断：原位置还在（库侧目录是链接也照常跟过去看）就算撤回未完成，
            # 宁可多保护，不能因为记录看不清就把续跑要用的 manifest 当成完整的删掉。归档侧途经链接的不认作已归档。
            item.archived = not _has_link_component(path, clean) and os.path.lexists(path / clean)
            item.origin_exists = os.path.lexists(root / clean)
        e.items.append(item)
    for x in _as_list(manifest.get("slug_dropped_from")):
        page = x.get("page") if isinstance(x, dict) else None
        clean = _clean_rel(page)
        if clean is None or not clean.startswith("wiki/") or not clean.endswith(".md") or clean in _CONFIG_PAGES:
            e.items.append(TrashItem(path=str(page), saved_as="", invalid="来源编辑记录的页面路径不合规"))
    # 只看归档侧（§3.2）：全在 → complete；缺项且原位还在 → incomplete（撤回未完成）；缺项、原位也没有 → partial。
    missing = [it for it in e.items if it.saved_as and not it.archived and _clean_rel(it.path) is not None]
    if any(it.origin_exists for it in missing):
        e.state = "incomplete"
    elif missing:
        e.state = "partial"
    else:
        e.state = "complete"
    _apply_restore(path, e)
    return e


def _rollback_entry(root: Path, path: Path, e: TrashEntry, meta: dict, actions: list[dict]) -> TrashEntry:
    e.kind = "rollback"
    e.record = meta
    task_id = meta.get("task_id") if isinstance(meta.get("task_id"), str) else None
    if task_id is None:
        m = _JOB_NAME.match(e.name)
        task_id = m.group(1) if m else ""
    e.subject = task_id or ""
    for rec in actions:
        clean, bad = _check_rollback_path(rec.get("path"))
        saved = _clean_rel(rec.get("saved_as"))
        item = TrashItem(path=clean, saved_as=saved or str(rec.get("saved_as")), invalid=bad)
        if saved is None:
            item.invalid = item.invalid or "saved_as 不合规（须落在本条目内）"
        elif not item.invalid:
            if _has_link_component(path, saved):
                item.invalid = "saved_as 途经软链接"
            else:
                item.archived = os.path.lexists(path / saved)
        e.items.append(item)
    # 回滚条目看归档侧（§3.2）：原位置被回滚写回前像内容是正常的，不看。
    # 同一路径可能有多行：回滚「先记录、后移动」，崩在两步之间会留下一行 saved_as 不存在的记录，重跑再以新序号移一次、
    # 再记一行（preimage._Trash）。同一路径只要有一行已归档就算这一项在。
    archived_paths = {it.path for it in e.items if it.archived}
    e.state = "complete" if all(it.path in archived_paths for it in e.items if not it.invalid) else "partial"
    return e


def _apply_restore(path: Path, e: TrashEntry) -> None:
    """有 `restore.json`：有 `finished_at` → restored，否则 restoring。读不了也按 restoring（保守：保住进度）。"""
    rec_path = path / RESTORE_RECORD
    if not os.path.lexists(rec_path):
        return
    rec = _read_json(rec_path)
    e.restore = rec if isinstance(rec, dict) else {}
    e.state = "restored" if isinstance(rec, dict) and rec.get("finished_at") else "restoring"


def scan(root: Path) -> list[TrashEntry]:
    """列出回收区全部条目（按时间倒序）。`.trash` 不存在 → 空表；不安全 → `TrashError`。不含删除保护（见 `annotate`）。"""
    root = Path(root)
    trash = trash_dir(root)
    if trash is None:
        return []
    try:
        names = sorted(os.listdir(trash))
    except OSError as exc:
        raise TrashError(f"读不了 {TRASH}/：{exc}") from None
    entries = [_entry(root, trash, n) for n in names]
    entries.sort(key=lambda e: (e.when, e.name), reverse=True)
    return entries


def summary(root: Path) -> dict:
    """体检那一行（§2）：条目数与总大小。回收区不安全时 `error` 给原因，不抛。"""
    try:
        entries = scan(root)
    except TrashError as exc:
        return {"count": 0, "size": 0, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 体检里的信息行：任何意外都不能让整个体检 500
        return {"count": 0, "size": 0, "error": f"{type(exc).__name__}: {exc}"}
    return {"count": len(entries), "size": sum(e.size for e in entries)}


# ── 条目定位（实际目录名）─────────────────────────────────────────────────────


def _valid_request_name(name: object) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\0" in name
    ):
        raise TrashError("条目名不合规：须是 .trash/ 的直接子项名。")
    return name


def resolve_name(root: Path, name: str) -> str:
    """把请求名对到 `.trash/` 里的**实际目录名**（§4.2）。大小写不敏感的卷上另一种拼写也指向同一项，之后一律用实际名。"""
    name = _valid_request_name(name)
    trash = trash_dir(Path(root))
    if trash is None:
        raise TrashNotFound(f"回收区里没有 {name}。")
    try:
        names = os.listdir(trash)
    except OSError as exc:
        raise TrashError(f"读不了 {TRASH}/：{exc}") from None
    if name in names:
        return name
    try:
        want = os.lstat(trash / name)
    except OSError:
        raise TrashNotFound(f"回收区里没有 {name}。") from None
    for n in names:
        try:
            st = os.lstat(trash / n)
        except OSError:
            continue
        if (st.st_ino, st.st_dev) == (want.st_ino, want.st_dev):
            return n
    raise TrashNotFound(f"回收区里没有 {name}。")


def get_entry(root: Path, name: str) -> TrashEntry:
    root = Path(root)
    actual = resolve_name(root, name)
    trash = trash_dir(root)
    assert trash is not None
    entry = _entry(root, trash, actual)
    annotate(root, [entry])
    return entry


# ── 删除保护（§4.2）───────────────────────────────────────────────────────────


def _envelopes(root: Path) -> list[dict]:
    """磁盘上的任务信封（`workspace/jobs/*.json`）。CLI 也读它，不依赖 Web 是否在跑。读不了的跳过。"""
    from .web.tasks import jobs_dir

    d = jobs_dir(root)
    out: list[dict] = []
    try:
        paths = sorted(d.glob("*.json"))
    except OSError:
        return out
    for p in paths:
        if not _TASK_ID.match(p.stem):
            continue
        data = _read_json(p)
        if isinstance(data, dict) and data.get("id") == p.stem:
            out.append(data)
    return out


def _same_entry(root: Path, rel: object, trash: Path, actual: str) -> bool:
    """任务信封里记的回收目录（如 `rollback_trash`）是不是这个条目：先按实际名比，再按 inode 比（大小写不敏感卷）。"""
    if not isinstance(rel, str):
        return False
    if rel == f"{TRASH}/{actual}":
        return True
    clean = _clean_rel(rel)
    if clean is None or not clean.startswith(TRASH + "/") or clean.count("/") != 1:
        return False
    try:
        a = os.lstat(Path(root) / clean)
        b = os.lstat(trash / actual)
    except OSError:
        return False
    return (a.st_ino, a.st_dev) == (b.st_ino, b.st_dev)


def _preimage_present(root: Path, task_id: str, env: dict) -> bool:
    """与 `TaskStore._has_preimage` 同一判据：信封带前像标记，且按 task_id 推出的前像目录还在。"""
    if not env.get("preimage"):
        return False
    try:
        from .web.tasks import preimage_base

        return (preimage_base(Path(root)) / task_id / "before").exists()
    except (OSError, RuntimeError):
        return True  # 确定不了家目录：保守按「还在」，宁可多保护


def protections(root: Path, entry: TrashEntry, envelopes: list[dict] | None = None) -> list[str]:
    """条目当前受哪些删除保护（§4.2），返回原因列表；空表 = 可删。"""
    root = Path(root)
    reasons: list[str] = []
    if entry.state == "incomplete":
        reasons.append("撤回尚未完成，续跑还要用这份记录（重新预览撤回或重跑 guanlan remove 完成后再删）")
    if entry.state == "restoring":
        reasons.append("恢复做了一半，续跑要用这份进度")
    trash = trash_dir(root)
    if trash is None:
        return reasons
    for env in envelopes if envelopes is not None else _envelopes(root):
        tid = env.get("id")
        state = env.get("state")
        if not isinstance(tid, str):
            continue
        if _same_entry(root, env.get("rollback_trash"), trash, entry.name):
            if state in _ROLLBACK_BUSY_STATES:
                reasons.append(f"任务 {tid} 正在回滚，会写进这个目录")
            elif state in _ROLLBACK_RESUMABLE_STATES and _preimage_present(root, tid, env):
                reasons.append(f"任务 {tid} 还可以再次回滚，会并入这个目录（在任务中心选「保留现状」或删除记录后可删）")
        if env.get("kind") == "restore" and state in _TASK_ACTIVE_STATES:
            params = env.get("params") if isinstance(env.get("params"), dict) else {}
            if _same_entry(root, f"{TRASH}/{params.get('name')}", trash, entry.name):
                reasons.append(f"恢复任务 {tid} 正在使用这个条目")
    return reasons


def annotate(root: Path, entries: list[TrashEntry]) -> list[TrashEntry]:
    """给每个条目填上 `protected`，回滚条目的 `subject` 补上任务标题（任务记录还在时）。只读一遍任务信封。"""
    envs = _envelopes(Path(root))
    titles = {env["id"]: env.get("title") for env in envs if isinstance(env.get("title"), str) and env.get("title")}
    for e in entries:
        e.protected = protections(root, e, envs)
        if e.kind == "rollback" and e.subject in titles:
            e.subject = f"{e.subject} · {titles[e.subject]}"
    return entries


# ── 彻底删除（§4）─────────────────────────────────────────────────────────────


def acquire_exclusive(root: Path, origin: str) -> kblock.KBLock:
    """独占库级锁：忙 → `KBLockBusy`；锁文件建不出（OSError）或只拿到嵌套放行 → `TrashLockError`。"""
    try:
        lock = kblock.acquire(root, origin)
    except OSError as exc:
        raise TrashLockError(f"建不了库级锁（{exc}），无法与其它写者互斥，不删除。") from None
    if lock.nested or not lock.held:
        lock.release()
        raise TrashLockError("库正被另一个写操作持有（本进程只是它的子进程），不能独占，不删除。")
    return lock


def _rm(path: Path) -> None:
    """删一个条目，不跟随任何链接：链接 / 文件 → unlink；目录 → rmtree（它不跟随内部链接，对顶层链接直接拒）。"""
    st = os.lstat(path)
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def purge_locked(root: Path, name: str) -> TrashEntry:
    """**调用方须已独占库级锁**。按实际目录名定位、复核保护、删除。返回被删条目的快照。"""
    root = Path(root)
    entry = get_entry(root, name)
    if entry.protected:
        raise TrashProtected(entry.name, entry.protected)
    trash = trash_dir(root)
    assert trash is not None
    _rm(trash / entry.name)
    return entry


def purge(root: Path, name: str, *, origin: str = "trash purge") -> TrashEntry:
    """CLI 用：独占库级锁 → `purge_locked` → 释放。"""
    lock = acquire_exclusive(Path(root), origin)
    try:
        return purge_locked(root, name)
    finally:
        lock.release()


# ── CLI ───────────────────────────────────────────────────────────────────


_KIND_LABEL = {"remove": "撤回", "remove_batch": "整批撤回", "rollback": "回滚", "unknown": "未知"}
_STATE_LABEL = {
    "complete": "完整",
    "incomplete": "撤回未完成",
    "partial": "部分缺失",
    "restoring": "恢复中",
    "restored": "已恢复",
    "unknown": "未知",
}


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _line(e: TrashEntry) -> str:
    flags = []
    if e.invalid:
        flags.append("记录有不合规项")
    if e.protected:
        flags.append("受保护")
    tail = f"  [{'，'.join(flags)}]" if flags else ""
    subject = f"  {e.subject}" if e.subject else ""
    return (
        f"{e.name}\n    {_KIND_LABEL.get(e.kind, e.kind)} · {_STATE_LABEL.get(e.state, e.state)} · {e.when} · "
        f"{fmt_size(e.size)} · {e.files} 个文件{subject}{tail}"
    )


def _print_detail(e: TrashEntry) -> None:
    print(_line(e))
    if e.note:
        print(f"  说明：{e.note}")
    d = e.detail()
    files = [it for it in e.items if it.saved_as]
    for it in e.items:
        if not it.saved_as:  # 不合规的来源编辑记录（slug_dropped_from 的页面路径），不是移进来的文件
            print(f"  ✗ {it.path}（{it.invalid}）")
    if files:
        head = "移走的条目" if e.kind != "rollback" else "回滚移走的条目"
        print(f"  {head}：")
        for it in files:
            mark = "✗" if it.invalid else ("·" if it.archived else "?")
            extra = f"（{it.invalid}）" if it.invalid else ("" if it.archived else "（不在条目里）")
            print(f"    {mark} {it.path}{extra}")
    for x in d.get("slug_dropped_from") or []:
        print(f"  摘除来源：{x.get('page')} ← {x.get('dropped_slug')}")
    for ln in d.get("index_lines_removed") or []:
        print(f"  删除的 index 行：{ln}")
    for reason in e.protected:
        print(f"  受保护：{reason}")


def run_trash(root: Path, *, action: str, name: str | None, yes: bool, json_output: bool) -> int:
    try:
        if action == "list":
            entries = annotate(root, scan(root))
            if json_output:
                print(json.dumps({"entries": [e.brief() for e in entries]}, ensure_ascii=False, indent=2))
            elif not entries:
                print("回收区为空。")
            else:
                print(f"回收区：{len(entries)} 项，共 {fmt_size(sum(e.size for e in entries))}")
                for e in entries:
                    print(_line(e))
            return EXIT_OK
        assert name is not None
        if action == "show":
            e = get_entry(root, name)
            if json_output:
                print(json.dumps(e.detail(), ensure_ascii=False, indent=2))
            else:
                _print_detail(e)
            return EXIT_OK
        if action == "purge":
            if not yes:
                e = get_entry(root, name)
                if json_output:
                    print(json.dumps({"executed": False, **e.brief()}, ensure_ascii=False, indent=2))
                else:
                    print(_line(e))
                    if e.protected:
                        print("受保护，不能删除：" + "；".join(e.protected))
                        return EXIT_USAGE
                    print("将彻底删除以上条目，不可恢复。加 --yes 执行。")
                return EXIT_USAGE if e.protected else EXIT_OK
            e = purge(root, name)
            if json_output:
                print(json.dumps({"executed": True, **e.brief()}, ensure_ascii=False, indent=2))
            else:
                print(f"✓ 已彻底删除 {TRASH}/{e.name}（{fmt_size(e.size)}，{e.files} 个文件）。")
            return EXIT_OK
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code
    except OSError as exc:  # 删除中途失败（权限、占用等）：报错退出，不抛回溯；条目可能已删掉一部分
        if action == "purge" and yes:
            print(f"删除失败：{exc}（条目可能已删掉一部分，可再次执行）", file=sys.stderr)
        else:
            print(f"读取回收区失败：{exc}", file=sys.stderr)
        return EXIT_USAGE
    raise ValueError(f"未知动作：{action}")


def trash_entrypoint(root_dir: str | Path, *, action: str, name: str | None, yes: bool, json_output: bool) -> int:
    try:
        root = require_kb_root(root_dir, writable=action == "purge" and yes)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code
    return run_trash(root, action=action, name=name, yes=yes, json_output=json_output)


def add_trash_arguments(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="trash_action", required=True, metavar="{list,show,purge}")
    ls = sub.add_parser("list", help="列出回收区条目（零 LLM、只读）")
    ls.add_argument("--json", action="store_true", help="输出 JSON")
    show = sub.add_parser("show", help="查看一个条目的详情（只读）")
    show.add_argument("name", help=".trash/ 下的条目名")
    show.add_argument("--json", action="store_true", help="输出 JSON")
    pg = sub.add_parser("purge", help="彻底删除一个条目（不可恢复；默认只预览，--yes 才删）")
    pg.add_argument("name", help=".trash/ 下的条目名")
    pg.add_argument("--yes", action="store_true", help="确认删除")
    pg.add_argument("--json", action="store_true", help="输出 JSON")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m guanlan.trash", description="回收区管理：浏览、彻底删除（零 LLM）。")
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    add_trash_arguments(parser)
    args = parser.parse_args(argv)
    return trash_entrypoint(
        args.dir,
        action=args.trash_action,
        name=getattr(args, "name", None),
        yes=getattr(args, "yes", False),
        json_output=args.json,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "TrashEntry",
    "TrashError",
    "TrashItem",
    "TrashLockError",
    "TrashNotFound",
    "TrashProtected",
    "acquire_exclusive",
    "annotate",
    "fmt_size",
    "get_entry",
    "protections",
    "purge",
    "purge_locked",
    "resolve_name",
    "scan",
    "summary",
    "trash_dir",
    "trash_entrypoint",
]

