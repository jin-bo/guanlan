"""任务前像：后台写任务开始前拍下可写范围的副本，取消或中断后据此回滚（P4.33 T2，§4.3–4.5）。**零 LLM。**

- **范围**：`wiki/` 下全部条目，加库根 `SCHEMA.md`、`AGENTAO.md`。不含 `raw/`（另记哈希快照、只核对不恢复，
  与写门禁「raw_mutated 判死」一致）、`workspace/`、`graph/`、`.trash/`。名为 `.DS_Store` 的普通文件忽略（Finder
  随手建的，与 Agent 无关；同 `gate.snapshot_raw`）。
- **形式**：按任务隔离的普通文件副本（决策 D4）——`<dest>/files/<相对路径>` + `manifest.json`（每个文件的 SHA256、
  目录清单、符号链接目标）+ `raw.json`（`gate.snapshot_raw`）+ `baseline.json`（`gate.check_baseline`）。四样都写完、
  落盘（fsync）后**最后**写 `READY`。没有 `READY` 的前像不能用于恢复；调用方据此保证「没有 READY 就不启动 Agent」
  （决策 D9）。不做内容寻址、去重、GC。
- **回滚**（`rollback`）幂等，中途崩溃后可整段重跑：先校验前像自身完整（哈希对得上），再
  ① 前像里没有的条目移进 trash → ② 删掉回滚后多出的空目录 → ③ 内容不同的文件先把当前版本移进 trash、再原子写回前像
  字节，缺失的直接写回 → ④ 补回前像里有的空目录与符号链接 → ⑤ 核对 `raw/`、复核 `check`。
  **回滚不丢字节**：被覆盖或移走的当前版本一律进 trash，trash 目录里追加写 `manifest.jsonl` 逐项记录（先记后移）。
  先移走多余条目、后写回：大小写 / Unicode 写法不敏感的文件系统上，Agent 只改了大小写的改名（`A.md` → `a.md`）
  在两步之间不会互相踩到。
- **不负责**：确认 Agent 进程组已全部退出（调用方的事，§4.6）；`raw/` 被改后的恢复；范围外的写。

调用方（Web 任务层）在**持写锁**时调用本模块；本模块不取锁。
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .errors import EXIT_USAGE, GuanlanError
from .gate import check_baseline, snapshot_raw
from .rawio import atomic_write_bytes, sha256_file

__all__ = [
    "PreimageError",
    "Preimage",
    "RollbackResult",
    "take",
    "is_ready",
    "load",
    "verify",
    "rollback",
    "discard",
    "read_trash_log",
]

ROOT_FILES = ("SCHEMA.md", "AGENTAO.md")
READY = "READY"
_IGNORED_NAME = ".DS_Store"
_FORMAT = 1


class PreimageError(GuanlanError):
    """前像缺失、未就绪或已损坏：不能用于恢复。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_USAGE)


@dataclass(frozen=True)
class Preimage:
    dest: Path
    files: dict[str, str]  # 相对库根 posix 路径 → sha256
    dirs: frozenset[str]  # 前像时存在的目录（相对库根，含 "wiki"）
    links: dict[str, str]  # 符号链接 → 链接目标（readlink 原文）
    raw: dict[str, str]  # gate.snapshot_raw
    baseline: frozenset[tuple[str, str, str]]  # gate.check_baseline
    seconds: float = 0.0  # 拍前像耗时（take 返回时填）
    wiki_link: str | None = None  # `wiki/` 本身是指向目录的符号链接时的目标（readlink 原文）；普通目录为 None


@dataclass
class RollbackResult:
    restored: list[str] = field(default_factory=list)  # 写回前像字节的文件（含缺失补回）
    removed: list[str] = field(default_factory=list)  # 前像里没有、移进 trash 的条目
    trash: str | None = None  # 相对库根的 trash 目录；没有移动任何东西时为 None
    raw_ok: bool = True
    raw_changes: list[str] = field(default_factory=list)
    check_ok: bool = True
    new_violations: list[tuple[str, str, str]] = field(default_factory=list)
    links_skipped: list[str] = field(default_factory=list)  # 目标在库外、没有补回的符号链接

    @property
    def ok(self) -> bool:
        return self.raw_ok and self.check_ok

    def to_dict(self) -> dict:
        return {
            "restored": self.restored,
            "removed": self.removed,
            "trash": self.trash,
            "raw_ok": self.raw_ok,
            "raw_changes": self.raw_changes,
            "check_ok": self.check_ok,
            "new_violations": [list(v) for v in self.new_violations],
            "links_skipped": self.links_skipped,
        }


# ── 遍历 ──────────────────────────────────────────────────────────────────────


@dataclass
class _Scan:
    files: dict[str, Path] = field(default_factory=dict)  # 普通文件
    dirs: set[str] = field(default_factory=set)
    links: dict[str, str] = field(default_factory=dict)  # 符号链接（不跟随）
    other: set[str] = field(default_factory=set)  # fifo / socket 等：只可能是多出来的，回滚时移走
    dir_links: set[str] = field(default_factory=set)  # wiki/ 内部指向目录的符号链接（内容不在前像里）
    wiki_link: str | None = None  # `wiki/` 本身是指向目录的符号链接：其目标（遍历会跟进去）


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _under_root(root: Path, path: Path) -> Path:
    """相对路径按库根解释（如 `.trash/job-<id>@…`；Web 任务层传的前像目录是库外的绝对路径），
    不按进程 cwd——Web 宿主 `-C` 启动时 cwd 不是库根。"""
    path = Path(path)
    return path if path.is_absolute() else Path(root) / path


def _scan(root: Path) -> _Scan:
    """遍历可写范围。`wiki/` 本身是指向目录的符号链接时跟进去（把 wiki 放在别处再链进来是常见做法，
    写入都经由它落到目标里）；`wiki/` **内部**的符号链接一律不跟随，只记链接本身。"""
    out = _Scan()
    wiki = root / "wiki"
    if wiki.is_symlink() and not wiki.is_dir():
        out.links["wiki"] = os.readlink(wiki)
    elif wiki.is_dir():
        if wiki.is_symlink():
            out.wiki_link = os.readlink(wiki)
        out.dirs.add("wiki")
        for dirpath, dirnames, filenames in os.walk(wiki):
            base = Path(dirpath)
            for name in list(dirnames):
                p = base / name
                if p.is_symlink():  # os.walk 默认不跟随，但会把指向目录的链接列在 dirnames 里
                    out.links[_rel(root, p)] = os.readlink(p)
                    out.dir_links.add(_rel(root, p))
                    dirnames.remove(name)
                else:
                    out.dirs.add(_rel(root, p))
            for name in filenames:
                p = base / name
                rel = _rel(root, p)
                if p.is_symlink():
                    out.links[rel] = os.readlink(p)
                elif p.is_file():
                    if name != _IGNORED_NAME:
                        out.files[rel] = p
                else:
                    out.other.add(rel)
    elif wiki.exists():
        out.other.add("wiki")
    for name in ROOT_FILES:
        p = root / name
        if p.is_symlink():
            out.links[name] = os.readlink(p)
        elif p.is_file():
            out.files[name] = p
        elif p.exists():
            out.other.add(name)
    return out


def _safe_rel(rel: str) -> str:
    """清单里的相对路径必须落在库根内（防篡改的清单把回滚写到库外）。"""
    if not isinstance(rel, str):
        raise PreimageError(f"前像清单含非法路径：{rel!r}")
    p = PurePosixPath(rel)
    if not rel or p.is_absolute() or ".." in p.parts or rel != p.as_posix():
        raise PreimageError(f"前像清单含非法路径：{rel!r}")
    if not (rel in ROOT_FILES or rel == "wiki" or rel.startswith("wiki/")):
        raise PreimageError(f"前像清单含范围外路径：{rel!r}")
    return rel


# ── 拍前像 ────────────────────────────────────────────────────────────────────


def _write_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _fsync_path(path: Path) -> None:
    """fsync 一个文件或目录；best-effort（Windows 不能打开目录）。"""
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _dump(obj: object) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def take(root: Path, dest: Path) -> Preimage:
    """拍前像到 `dest`（Web 任务层用库外的 `~/.guanlan/jobs/<库摘要>/<id>/before`）。`dest` 已存在则先清掉（上次残缺的）。

    依次写 `files/`、`manifest.json`、`raw.json`、`baseline.json`，各自 fsync，**最后**写 `READY`。
    任一步失败直接上抛：此时没有 `READY`，调用方不得启动 Agent。
    """
    started = time.monotonic()
    root = Path(root)
    dest = _under_root(root, dest)
    files_dir = dest / "files"
    scan = _scan(root)
    if scan.dir_links:
        # 链接进来的子目录：里面的页面会被 Agent 写，却不在前像里，回滚时恢复不了——宁可不拍、不让任务开始。
        raise PreimageError(
            f"wiki/ 里有指向目录的符号链接，前像覆盖不到其中的页面：{', '.join(sorted(scan.dir_links))}"
        )
    if scan.other:
        # fifo / socket 之类出现在 wiki/ 里不正常；前像不复制它们，回滚时会被当作多余条目移走——提前说清楚。
        # 在复制任何文件之前判：免得白复制整个 wiki/ 再失败。
        raise PreimageError(f"可写范围里有无法备份的特殊文件：{', '.join(sorted(scan.other))}")
    if dest.exists():  # 校验通过才清掉上次残缺的：拒拍时不顺手毁掉 dest 里已有的东西
        shutil.rmtree(dest)
    hashes: dict[str, str] = {}
    copy_dirs: set[Path] = set()
    for rel, src in sorted(scan.files.items()):
        data = src.read_bytes()
        hashes[rel] = hashlib.sha256(data).hexdigest()
        copy = files_dir / rel
        _write_durable(copy, data)
        d = copy.parent
        while d not in copy_dirs and d != dest:  # 副本所在目录直到 files/ 本身
            copy_dirs.add(d)
            d = d.parent
        with contextlib.suppress(OSError):  # 记下权限位：回滚补回缺失文件时照抄，不落成 mkstemp 的 0600
            shutil.copymode(src, copy)
    # 副本的目录项也要落盘：否则断电后 READY 在、副本却丢了，前像只能被 verify 判坏、无法用于恢复。
    for d in copy_dirs:
        _fsync_path(d)
    manifest = {
        "format": _FORMAT,
        "files": hashes,
        "dirs": sorted(scan.dirs),
        "links": dict(sorted(scan.links.items())),
        "wiki_link": scan.wiki_link,
    }
    _write_durable(dest / "manifest.json", _dump(manifest))
    raw = snapshot_raw(root)
    _write_durable(dest / "raw.json", _dump(raw))
    baseline = check_baseline(root)
    _write_durable(dest / "baseline.json", _dump(sorted(list(k) for k in baseline)))
    _fsync_path(dest)
    _write_durable(dest / READY, b"")
    _fsync_path(dest)
    return Preimage(
        dest=dest,
        files=hashes,
        dirs=frozenset(scan.dirs),
        links=dict(scan.links),
        raw=raw,
        baseline=frozenset(baseline),
        seconds=time.monotonic() - started,
        wiki_link=scan.wiki_link,
    )


# ── 读 ────────────────────────────────────────────────────────────────────────


def is_ready(dest: Path, *, root: Path | None = None) -> bool:
    """`root` 给出时相对 `dest` 按库根解释（与 `take` / `rollback` 同口径）；否则按原样。"""
    dest = _under_root(root, dest) if root is not None else Path(dest)
    return (dest / READY).is_file()


def load(dest: Path, *, root: Path | None = None) -> Preimage:
    """读一份**已就绪**的前像。没有 `READY`、清单缺失 / 损坏 / 含非法路径 → `PreimageError`。
    `root` 给出时相对 `dest` 按库根解释。"""
    dest = _under_root(root, dest) if root is not None else Path(dest)
    if not is_ready(dest):
        raise PreimageError(f"前像未就绪（没有 {READY}），不能用于恢复：{dest}")
    try:
        manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        raw = json.loads((dest / "raw.json").read_text(encoding="utf-8"))
        baseline = json.loads((dest / "baseline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PreimageError(f"前像清单读不出：{exc}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != _FORMAT:
        raise PreimageError("前像清单格式不对")
    shapes = (("files", dict), ("links", dict), ("dirs", list))
    if any(not isinstance(manifest.get(k) or typ(), typ) for k, typ in shapes):  # 被手改成别的类型：判坏，不让 .items() 炸出非 PreimageError
        raise PreimageError("前像清单格式不对")
    files = {_safe_rel(k): str(v) for k, v in (manifest.get("files") or {}).items()}
    dirs = frozenset(_safe_rel(d) for d in manifest.get("dirs") or [])
    links = {_safe_rel(k): str(v) for k, v in (manifest.get("links") or {}).items()}
    if not isinstance(raw, dict) or not isinstance(baseline, list):
        raise PreimageError("前像的 raw 快照或 check 基线格式不对")
    wiki_link = manifest.get("wiki_link")
    return Preimage(
        dest=dest,
        files=files,
        dirs=dirs,
        links=links,
        raw={str(k): str(v) for k, v in raw.items()},
        baseline=frozenset(tuple(x) for x in baseline if isinstance(x, list) and len(x) == 3),
        wiki_link=str(wiki_link) if wiki_link is not None else None,
    )


def verify(pre: Preimage) -> list[str]:
    """前像副本与清单对不上的文件（缺失或哈希不符）。空列表 = 完好。"""
    bad: list[str] = []
    for rel, sha in pre.files.items():
        p = pre.dest / "files" / rel
        try:
            if p.is_symlink() or not p.is_file() or sha256_file(p) != sha:
                bad.append(rel)
        except OSError:
            bad.append(rel)
    return sorted(bad)


# ── 回滚 ──────────────────────────────────────────────────────────────────────


TRASH_LOG = "manifest.jsonl"


class _Trash:
    """把当前条目移进 trash 目录（保持相对路径），并逐项记录。目录首次需要时才建。

    记录是追加写的 `manifest.jsonl`：首行 `{"meta": …}`，之后每移动一项**先**追加一行（fsync）再移动——回滚中途
    崩溃也不会出现「文件进了 trash 却没有记录」。反过来，崩溃若落在记录与移动之间，会多一行 `saved_as` 并不存在的
    记录（重跑时该项会以新序号再移一次、再记一行），只是多一条无害的记录。重跑追加到同一个文件。
    """

    def __init__(self, root: Path, trash_dir: Path, meta: dict) -> None:
        self.root = root
        self.dir = trash_dir
        self.meta = meta
        self.actions: list[dict] = []

    def _append(self, record: dict) -> None:
        log = self.dir / TRASH_LOG
        new = not log.exists()
        self.dir.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            if new:
                fh.write(json.dumps({"meta": self.meta}, ensure_ascii=False) + "\n")
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def move(self, rel: str, action: str) -> None:
        src = self.root / rel
        dst = self.dir / rel
        n = 1
        while dst.exists() or dst.is_symlink():  # 重跑时同一路径可能已有一份：加序号，不覆盖
            dst = self.dir / f"{rel}.{n}"
            n += 1
        record = {"path": rel, "action": action, "saved_as": _rel(self.dir, dst)}
        self._append(record)  # 先记录再移动
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dst)
        except OSError as exc:
            # `wiki/` 链到别的卷时条目与 `.trash/` 不在同一文件系统：rename 报 EXDEV，退回复制后删除。
            if exc.errno != errno.EXDEV:
                raise
            shutil.move(str(src), str(dst))
        self.actions.append(record)


def read_trash_log(trash_dir: Path) -> tuple[dict, list[dict]]:
    """读回 trash 记录：`(meta, actions)`。坏行跳过。"""
    meta: dict = {}
    actions: list[dict] = []
    try:
        lines = (Path(trash_dir) / TRASH_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return meta, actions
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and "meta" in rec and not meta:
            meta = rec["meta"] if isinstance(rec["meta"], dict) else {}
        elif isinstance(rec, dict) and "path" in rec:
            actions.append(rec)
    return meta, actions


def _link_target_inside(root: Path, rel: str, target: str) -> bool:
    """链接目标（按链接所在目录解释）是否落在库根内。两边都取 realpath：库根本身经过符号链接（macOS 的
    /tmp → /private/tmp）时字面比较会误判；目标路径里途经的符号链接也一并解开，经它逃出库外的照样判在库外。
    目标不存在的部分 realpath 按字面保留。`wiki/` 本身链到库外时（`take` 支持的配置），wiki 内部的相对链接
    解开后落在链接目标里，也算在范围内——否则这种库里的链接一个都补不回来。"""
    base = os.path.dirname(os.path.join(os.path.realpath(str(root)), rel))
    full = os.path.realpath(os.path.join(base, target))
    allowed = {os.path.realpath(str(root)), os.path.realpath(os.path.join(str(root), "wiki"))}
    return any(full == a or full.startswith(a + os.sep) for a in allowed)


def _depth(rel: str) -> int:
    return rel.count("/")


def rollback(root: Path, pre: Preimage, trash_dir: Path, *, meta: dict | None = None) -> RollbackResult:
    """把可写范围恢复到前像，返回回执。**调用方须已确认 Agent 进程组全部退出并持写锁。**

    `trash_dir`（如 `.trash/job-<id>@<时间>`）由调用方定并持久化，重跑时传同一个，动作并入同一份记录。
    前像副本自身损坏 → 在动任何文件之前抛 `PreimageError`。`raw/` 被改或 `check` 出现新阻断违规不抛异常，
    记在回执的 `raw_ok` / `check_ok` 里，由调用方判 `rollback_failed`。
    """
    root = Path(root)
    bad = verify(pre)
    if bad:
        raise PreimageError(f"前像副本损坏，拒绝回滚：{', '.join(bad[:5])}{' …' if len(bad) > 5 else ''}")
    wiki = root / "wiki"
    if wiki.is_dir() or pre.wiki_link is not None:
        cur_link = os.readlink(wiki) if wiki.is_symlink() else None
        # 前像时 wiki/ 是链接、现在链接没了（或悬空）：照常回滚会在库根新建一个真目录写回页面，与链接目标分叉。
        if cur_link != pre.wiki_link or not wiki.is_dir():
            # wiki/ 被换成了指向别处的链接（或反过来）：遍历会跟进去，回滚就会在库外移走、写入文件。不动，交给人处理。
            raise PreimageError(
                f"wiki/ 的链接状态与前像不同（前像 {pre.wiki_link!r}，现在 {cur_link!r}），拒绝回滚"
            )
    trash = _Trash(root, _under_root(root, trash_dir), meta or {})
    result = RollbackResult()

    # ① 前像里没有的条目移走（文件、符号链接、特殊文件；内容不同的链接也移走，④ 再补回）。
    scan = _scan(root)
    extras = [rel for rel in scan.files if rel not in pre.files]
    extras += [rel for rel, target in scan.links.items() if pre.links.get(rel) != target]
    extras += sorted(scan.other)
    for rel in sorted(set(extras), key=lambda r: (-_depth(r), r)):
        trash.move(rel, "removed")
        result.removed.append(rel)

    # ② 回滚后多出的空目录删掉（深的先删）；非空的说明里面还有前像里的东西，留着。遍历时忽略的 `.DS_Store`
    # 也会让多出的目录删不掉（③ 往同名路径写回文件、④ 补回链接时随之失败），故先把它移进 trash。
    for rel in sorted(scan.dirs - pre.dirs, key=lambda r: (-_depth(r), r)):
        junk = f"{rel}/{_IGNORED_NAME}"
        if (root / junk).is_file() and not (root / junk).is_symlink():
            trash.move(junk, "removed")
            result.removed.append(junk)
        with contextlib.suppress(OSError):
            (root / rel).rmdir()

    # ③ 写回文件：内容不同的先把当前版本移进 trash。
    for rel, sha in sorted(pre.files.items()):
        target = root / rel
        # 文件被换成了链接：已在 ① 移走。被换成了目录：里面全是前像外的条目，① 已移走、② 已删空。
        existed = target.is_file() and not target.is_symlink()
        if existed:
            if sha256_file(target) == sha:
                continue
            trash.move(rel, "replaced")
        target.parent.mkdir(parents=True, exist_ok=True)
        copy = pre.dest / "files" / rel
        atomic_write_bytes(target, copy.read_bytes())
        with contextlib.suppress(OSError):  # 新建的 inode 是 0600：按前像副本补回原权限位
            shutil.copymode(copy, target)
        _fsync_path(target)  # 写回的内容落盘：调用方回滚成功后会删前像，断电不能两头都丢
        _fsync_path(target.parent)
        result.restored.append(rel)

    # ④ 补回前像里的空目录与符号链接。
    for rel in sorted(pre.dirs, key=_depth):
        (root / rel).mkdir(parents=True, exist_ok=True)
    for rel, target in sorted(pre.links.items()):
        p = root / rel
        if p.is_symlink():
            continue
        # 清单可能被改过（前像放在库外也不是 Agent 写不到）：只补回目标仍在库内的链接，指向库外的不建，列进回执由人处理。
        if not _link_target_inside(root, rel, target):
            result.links_skipped.append(rel)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, p)
        result.restored.append(rel)

    if trash.actions or trash.dir.is_dir():  # 重跑且这次没动任何东西：沿用上一次的 trash 路径
        result.trash = _rel(root, trash.dir) if trash.dir.is_relative_to(root) else str(trash.dir)

    # ⑤ 核对 raw/、复核 check。
    now_raw = snapshot_raw(root)
    keys = set(now_raw) | set(pre.raw)
    result.raw_changes = sorted(k for k in keys if now_raw.get(k) != pre.raw.get(k))
    result.raw_ok = not result.raw_changes
    after = check_baseline(root)
    result.new_violations = sorted(after - pre.baseline)
    result.check_ok = not result.new_violations
    return result


def discard(dest: Path, *, root: Path | None = None) -> None:
    """删掉一份前像（任务正常结束 / 用户选「保留现状」后）。不存在不报错。`root` 给出时相对 `dest` 按库根解释。"""
    dest = _under_root(root, dest) if root is not None else Path(dest)
    shutil.rmtree(dest, ignore_errors=True)
