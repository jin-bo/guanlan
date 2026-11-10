"""库级写锁（P4.30 §6.4，决策P4.30-15）：`<库根>/.guanlan.lock` 上的**操作系统文件锁**。

- 非阻塞排他锁：POSIX `fcntl.flock`，Windows `msvcrt.locking`；不新增依赖。拿不到 → `KBLockBusy`，
  立即失败、不排队。
- 锁随持有进程退出由操作系统释放，**没有残留锁**；锁文件里的 pid / origin / 开始时间只用来**显示是谁在写**，
  不参与正确性判断。
- **只由最外层入口取锁**（`cli.py` 子命令分发处、Web `WriteGate`）；`run_ingest`、`run_guarded_write` 等内核
  函数一律不取。POSIX `flock` 对同一进程里另开的文件描述符同样互斥，重复获取会让自己失败。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from .errors import EXIT_USAGE, GuanlanError

__all__ = [
    "LOCK_NAME",
    "KBLock",
    "KBLockBusy",
    "acquire",
    "kb_write_lock",
    "read_holder",
    "holder_alive",
    "describe_holder",
]

LOCK_NAME = ".guanlan.lock"
_WIN_LOCK_OFFSET = 1 << 30  # Windows 锁一个远离文件内容的字节，执行者信息仍可被其它进程读取


class KBLockBusy(GuanlanError):
    """库正被另一个写者占用。"""

    def __init__(self, holder: dict | None) -> None:
        self.holder = holder
        super().__init__(f"知识库正在被另一个写操作占用（{describe_holder(holder)}），请等它结束后再试。", exit_code=EXIT_USAGE)


def describe_holder(holder: dict | None) -> str:
    if not holder:
        return "执行者未知"
    bits = []
    if holder.get("origin"):
        bits.append(str(holder["origin"]))
    if holder.get("pid"):
        bits.append(f"pid {holder['pid']}")
    if holder.get("started"):
        bits.append(f"开始于 {holder['started']}")
    return "，".join(bits) or "执行者未知"


def read_holder(root: Path) -> dict | None:
    """读锁文件里的执行者信息（只用于显示）。读不到/坏/空（锁空闲）→ None。

    持有者被 kill 时来不及清空，信息会留到下一次获取；显示前可用 `holder_alive` 粗判。
    """
    try:
        text = (Path(root) / LOCK_NAME).read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def holder_alive(holder: dict | None) -> bool:
    """执行者 pid 是否还活着（只用于显示，不参与正确性）。"""
    pid = (holder or {}).get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # 无权限探测 = 进程存在
        return True
    return True


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":  # pragma: no cover - CI 跑 POSIX
        import msvcrt

        os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover
        import msvcrt

        os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)


class KBLock:
    """已获取的库级锁。`release()` 幂等。"""

    def __init__(self, root: Path, fd: int, holder: dict) -> None:
        self.root = root
        self._fd: int | None = fd
        self.holder = holder

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        with contextlib.suppress(OSError):  # 清掉执行者信息：锁空闲时不再显示「谁在写」
            os.ftruncate(fd, 0)
        _unlock(fd)
        os.close(fd)

    def __enter__(self) -> KBLock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire(root: str | Path, origin: str) -> KBLock:
    """非阻塞获取库级锁；被占用 → `KBLockBusy`（带执行者信息）。"""
    root = Path(root).resolve()
    path = root / LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _try_lock(fd):
            raise KBLockBusy(read_holder(root))
        holder = {"pid": os.getpid(), "origin": origin, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        data = json.dumps(holder, ensure_ascii=False).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data)
    except BaseException:
        os.close(fd)
        raise
    return KBLock(root, fd, holder)


@contextlib.contextmanager
def kb_write_lock(root: str | Path, origin: str) -> Iterator[KBLock]:
    lock = acquire(root, origin)
    try:
        yield lock
    finally:
        lock.release()
