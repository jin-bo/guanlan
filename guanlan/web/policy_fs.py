"""写边界守卫原语（P4.5，见 docs/P4.5-可写Web工作会话.md §2 决策P4.5-2/3）。

本模块**无业务智能**，只提供三组确定性原语，供 `chat.py` 的可写会话装配：

- **层①** `PolicyFileSystem`：包住 agentao 的 `FileSystem` capability，对一切经 `agent.filesystem`
  的结构化写（`write_file`/`replace` 终调 `write_text`）做**叶解引用包含判定**——命中 immutable 集
  （`raw/` ∪ `{AGENTAO.md}`，**`SCHEMA.md` 不在内**，决策P4.5-12）即拒；并在 immutable 判定**前**
  先防御性断「目标在 kb 根内」（不把 kb-containment 委托上游）。它还顺带在可写 turn 内记 per-turn
  写日志 `(路径, 写前字节, 写后哈希)`——`wiki/` 页与 `SCHEMA.md` 都经该 capability 落盘 → 都进同一份
  日志，供「撤销本轮写」乐观回放（§3，决策P4.5-4/13）。
- **层②** `snapshot_agentao`/`restore_agentao`：仅对 `AGENTAO.md` 一个文件（行为硬约束/宪法）做
  快照 + 自动还原——shell 直写绕过层①时把它写回原字节；**先 lstat 判形态**，被换成 symlink/目录/fifo
  则先清替身（unlink/rmtree）、绝不顺 symlink 写穿，再原子写回普通文件（决策P4.5-3/c，评审 High）。
- **撤销回放** `hash_file`/`restore_path`：供 `Conversation.apply_undo` 乐观校验 + 还原本轮写日志。

`raw/` 树**不**做 per-turn 快照（其 shell 直写并入残留，决策P4.5-3）；shell 经 `shell` capability、
不经 `agent.filesystem`，本模块的层① 够不着——那条由层②（仅 `AGENTAO.md`）与层③（时序互斥）兜。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agentao.capabilities.filesystem import FileSystem, LocalFileSystem


@dataclass(frozen=True)
class _Rule:
    """层① 策略：immutable 集（命中即拒结构化写）。

    `immutable` 留下 `writable` 的对偶——cwd（kb 根）隐式可写（augment 语义），故无需显式 writable 集。
    姿态无关、构造期定一次：read-only 与 workspace-write 下 immutable 恒等（read-only 由 tool_runner
    全拦写、workspace-write 由本 wrapper 守只读子集），故 `/mode` 翻姿态不动 wrapper（决策P4.5-2）。
    """

    immutable: tuple[Path, ...]


def _effective_target(raw: str, base: Path) -> Path:
    """`open()` 真正会写的位置：父链 ..-safe resolve、叶 symlink 跟随后再判成员。

    这是唯一正确的判定基（不能用 `contain_file`：它在解引用叶 symlink *之前* 判包含，会 fail-open，
    放过 `wiki/link → raw/secret` 的软链 clobber，上游 host-fs-policy.md §Interim 已点名）。
    """
    p = (base / raw).expanduser() if not Path(raw).is_absolute() else Path(raw).expanduser()
    t = p.parent.resolve(strict=False) / p.name
    return t.resolve(strict=False) if t.is_symlink() else t


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """同目录临时文件 + `os.replace` 原子写回（绝不顺 symlink 写穿——调用方须先清替身）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        with _suppress_oserror():
            os.unlink(tmp)
        raise


class _suppress_oserror:
    """轻量 contextlib.suppress(OSError) 替身，免在本模块再引 contextlib。"""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


def hash_file(path: Path) -> Optional[str]:
    """文件当前字节的 sha256（缺失/不可读 → None）。供撤销乐观校验。"""
    try:
        return _sha256(path.read_bytes())
    except OSError:
        return None


def restore_path(path: Path, before: Optional[bytes]) -> None:
    """把 `path` 还原到 `before`（None = 删除该文件）。供撤销回放、原子写。"""
    if before is None:
        with _suppress_oserror():
            path.unlink()
        return
    _atomic_write_bytes(path, before)


class PolicyFileSystem:
    """层①：只能收紧、不能放宽的 `FileSystem` 策略 wrapper（决策P4.5-2）。

    对一切经 `agent.filesystem` 的结构化写做包含判定，命中 immutable 集即 `PermissionError`；
    其余文件操作（读/列/glob/stat…）经 `__getattr__` 原样透传内层。`begin_journal`/`end_journal`
    控制 per-turn 写日志（`wiki/` + `SCHEMA.md` 范围），供撤销本轮写（§3）。
    """

    def __init__(self, inner: FileSystem, kb: Path, rule: _Rule) -> None:
        self._fs = inner
        self._kb = kb.resolve()
        # immutable 集**规范化到与 _effective_target 同一坐标系**（同样 resolve(strict=False)）——
        # 否则 kb 为相对路径 / 经 symlink 进入 / raw/ 父链含 symlink 时，`t == m or m in t.parents`
        # 跨两套路径表示比较会 fail-open，放过对 raw//AGENTAO.md 的写（评审 Medium）。
        self._immutable = tuple(m.resolve(strict=False) for m in rule.immutable)
        # 撤销本轮写日志：path -> (写前字节|None, 写后哈希)。None = 不在记账窗口（read-only turn）。
        self._journal: Optional[dict[Path, tuple[Optional[bytes], str]]] = None
        # 记账作用域：wiki/ 子树 + SCHEMA.md（撤销范围，决策P4.5-4/12）。
        self._wiki = (self._kb / "wiki").resolve(strict=False)
        self._schema = (self._kb / "SCHEMA.md").resolve(strict=False)

    def _check(self, raw: str) -> Path:
        t = _effective_target(str(raw), self._kb)
        if t != self._kb and self._kb not in t.parents:  # 防御：目标必须在 kb 根内
            raise PermissionError(f"FsPolicy: out-of-kb: {t}")  # 不把 kb-containment 委托上游
        if any(t == m or m in t.parents for m in self._immutable):  # 两侧均 resolved，同坐标系
            raise PermissionError(f"FsPolicy: immutable: {t}")  # raw/ 或 AGENTAO.md → 拒
        return t

    def _in_undo_scope(self, t: Path) -> bool:
        """该写是否进撤销日志：wiki/ 子树内、或就是 SCHEMA.md（决策P4.5-4/12）。"""
        return t == self._schema or t == self._wiki or self._wiki in t.parents

    def begin_journal(self) -> None:
        """可写 turn 起跑时调：开一份新写日志（替换上轮）。"""
        self._journal = {}

    def end_journal(self) -> dict[Path, tuple[Optional[bytes], str]]:
        """可写 turn 收尾时调：取走并关闭本轮写日志。"""
        journal = self._journal or {}
        self._journal = None
        return journal

    def write_text(self, path, data, *, append: bool = False) -> None:
        t = self._check(path)
        # 进撤销作用域且在记账窗口 → 记 (写前字节, 写后哈希)。写前字节须在写**之前**读；同一 path
        # 一轮多写时保留首条写前字节、只刷新写后哈希（撤销=回到 turn 起点状态，决策P4.5-13）。
        record = self._journal is not None and self._in_undo_scope(t)
        before: Optional[bytes] = None
        if record:
            before = hash_file_bytes(t)
        self._fs.write_text(path, data, append=append)
        if record:
            after = hash_file(t) or ""
            prev = self._journal.get(t)  # type: ignore[union-attr]
            first_before = prev[0] if prev is not None else before
            self._journal[t] = (first_before, after)  # type: ignore[index]

    def __getattr__(self, name):
        return getattr(self._fs, name)  # 其余方法/属性透传内层（只读、列目录、glob…）


def hash_file_bytes(path: Path) -> Optional[bytes]:
    """读文件原字节（缺失/不可读 → None）。供写日志记写前字节。"""
    try:
        return path.read_bytes()
    except OSError:
        return None


def make_policy_fs(kb: Path) -> PolicyFileSystem:
    """构造绑定到 kb 的层① wrapper：inner = 原生 LocalFileSystem，immutable = raw/ ∪ {AGENTAO.md}。

    `SCHEMA.md` **刻意不在** immutable 集（agent 可写、人驱动，决策P4.5-12）。姿态无关、构造期
    装一次（决策P4.5-2）。
    """
    rule = _Rule(immutable=(kb / "raw", kb / "AGENTAO.md"))
    return PolicyFileSystem(LocalFileSystem(), kb, rule)


# ── 层②：AGENTAO.md 单文件快照 + 自动还原（决策P4.5-3/c） ─────────────────────────

_AGENTAO_NAME = "AGENTAO.md"


class AgentaoRestoreError(RuntimeError):
    """层②还原无法完成（快照不可读 → 无原字节可写回）。供收尾捕获记告警（评审 P2）。"""


@dataclass(frozen=True)
class AgentaoSnapshot:
    """层② 起跑快照的**三态**（评审 P2：必须区分「不存在」与「存在但不可读」）。

    旧设计把快照压成 `Optional[bytes]`，`None` 同时表「文件本不存在」与「读失败」——后者（如起服后
    `AGENTAO.md` 被 chmod 掉读权限）在收尾会被 `restore_agentao` 当成「本不存在」而 `unlink`，反把
    宪法文件删掉，正违它要守的不可变保护。故显式三态：

    - **present**（`existed=True, data=bytes`）：拍到原字节，收尾还原到 `data`。
    - **absent**（`existed=False`）：确实不存在，收尾确保最终也不存在（删掉旁路新建的）。
    - **unreadable**（`existed=True, data=None`）：存在但拍快照时读失败——**无原字节可还原**，收尾
      绝不删普通文件、绝不写穿，仅清被换上的非普通替身后上抛 `AgentaoRestoreError`（surfacing
      a restore failure）。
    """

    existed: bool
    data: Optional[bytes]


def snapshot_agentao(kb: Path) -> AgentaoSnapshot:
    """可写 turn 起跑时拍 `AGENTAO.md` 三态快照（present / absent / unreadable）。一个文件、近免费。

    用 `lstat` 先判**存在**（区别于读字节失败）：`FileNotFoundError` → absent；存在且为普通文件能读
    → present；存在但读失败（权限/非普通形态）→ unreadable（保守，收尾绝不当 absent 删）。
    """
    path = kb / _AGENTAO_NAME
    try:
        st = path.lstat()
    except FileNotFoundError:
        return AgentaoSnapshot(existed=False, data=None)  # 确实不存在
    if stat.S_ISREG(st.st_mode):
        try:
            return AgentaoSnapshot(existed=True, data=path.read_bytes())  # 拍到原字节
        except OSError:
            return AgentaoSnapshot(existed=True, data=None)  # 存在但不可读（如权限被收）
    return AgentaoSnapshot(existed=True, data=None)  # symlink/目录/fifo：存在但无原字节可还原


def restore_agentao(kb: Path, snap: AgentaoSnapshot) -> Optional[str]:
    """把被旁路改/删/换形态的 `AGENTAO.md` 还原到快照态；返回被还原的文件名或 None（无变更）。

    形态判定不能省（评审 High）：shell 旁路不止 `echo`，还能 `ln -s`/`mkdir`/`mkfifo` 把它换成
    symlink/目录/fifo。① 命中非普通文件 → 先清替身（unlink，目录则 rmtree），**绝不顺 symlink
    write_bytes**（否则写穿到 symlink 指向的 kb 外/敏感目标）；② 再按快照三态收尾：
    - **unreadable**：无原字节可写回（评审 P2）——只清非普通替身（安全），普通文件**原样保留绝不删**，
      再抛 `AgentaoRestoreError` 让收尾记告警；
    - **absent**：确保最终不存在（删掉旁路新建的普通文件）；
    - **present**：以普通文件原子写回原字节。
    """
    path = kb / _AGENTAO_NAME
    try:
        st = path.lstat()
    except FileNotFoundError:
        st = None

    # unreadable：起跑时存在但读不到原字节 → 无从还原内容。绝不把普通文件误删（修复 P2），
    # 仅清被换上的非普通替身（symlink/目录/fifo，安全），然后上抛让收尾记「还原失败」告警。
    if snap.existed and snap.data is None:
        if st is not None and not stat.S_ISREG(st.st_mode):
            if stat.S_ISDIR(st.st_mode):
                shutil.rmtree(path)
            else:
                path.unlink()
        raise AgentaoRestoreError(f"AGENTAO.md 起跑时不可读，无法保证还原：{path}")

    changed = False
    if st is not None and not stat.S_ISREG(st.st_mode):
        # 被换成 symlink/目录/fifo/special：先清替身，绝不顺 symlink 写穿。
        changed = True
        if stat.S_ISDIR(st.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink()
        st = None

    if not snap.existed:  # 快照记不存在
        if st is not None:  # 但现在有普通文件 → 删掉旁路新建的
            path.unlink()
            changed = True
        return _AGENTAO_NAME if changed else None

    before = snap.data  # present：原字节非空
    current = path.read_bytes() if st is not None else None
    if current != before:
        _atomic_write_bytes(path, before)
        changed = True
    return _AGENTAO_NAME if changed else None
