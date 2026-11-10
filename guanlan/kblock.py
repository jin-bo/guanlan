"""库级写锁（P4.30 §6.4，决策P4.30-15）：`<库根>/.guanlan.lock` 上的**操作系统文件锁**。

- 非阻塞排他锁：POSIX `fcntl.flock`，Windows `msvcrt.locking`；不新增依赖。拿不到 → `KBLockBusy`，
  立即失败、不排队。
- 锁随持有进程退出由操作系统释放，**没有残留锁**；锁文件里的 pid / origin / 开始时间只用来**显示是谁在写**，
  不参与正确性判断。
- **只由最外层入口取锁**（`cli.py` 子命令分发处、Web `WriteGate`）；`run_ingest`、`run_guarded_write` 等内核
  函数一律不取。POSIX `flock` 对同一进程里另开的文件描述符同样互斥，重复获取会让自己失败。
- **嵌套写放行**：持锁的写运行里，Agent 照 `AGENTAO.md` / `SKILL.md` 在 shell 里跑 `guanlan convert` /
  `ingest` / `reindex` 等子命令时，子进程拿不到锁。取锁进程在 `os.environ` 放一个**进程令牌**（只设一次，
  子进程继承），并写进锁文件的执行者信息；子孙进程发现锁的持有者带着自己继承来的令牌、且持有者还活着，
  就当作嵌套调用、不再取锁（返回不持有任何东西的 `KBLock`）。无关进程没有这个令牌，照常互斥；
  持有者释放时清空执行者信息，令牌随之失效。
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .errors import EXIT_USAGE, GuanlanError

__all__ = [
    "LOCK_NAME",
    "TOKEN_ENV",
    "KBLock",
    "KBLockBusy",
    "acquire",
    "kb_write_lock",
    "run_locked",
    "read_holder",
    "holder_alive",
    "describe_holder",
]

LOCK_NAME = ".guanlan.lock"
TOKEN_ENV = "GUANLAN_KB_LOCK_TOKEN"
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


def _process_token() -> str:
    """本进程（及其子孙）的锁令牌：继承来的就沿用，否则生成一个并放进 `os.environ` 让子进程继承。"""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        token = secrets.token_hex(8)
        os.environ[TOKEN_ENV] = token
    return token


def _nested_holder(root: Path) -> dict | None:
    """锁的持有者是不是本进程的祖先写运行：令牌一致、不是本进程、且还活着。"""
    token = os.environ.get(TOKEN_ENV)
    holder = read_holder(root)
    if not token or not holder or holder.get("token") != token:
        return None
    if holder.get("pid") == os.getpid() or not holder_alive(holder):
        return None
    return holder


class KBLock:
    """已获取的库级锁。`release()` 幂等。`nested=True`：祖先进程持锁，本对象不持有任何东西。"""

    def __init__(self, root: Path, fd: int | None, holder: dict, *, nested: bool = False) -> None:
        self.root = root
        self._fd: int | None = fd
        self.holder = holder
        self.nested = nested

    @property
    def held(self) -> bool:
        return self._fd is not None or self.nested

    def release(self) -> None:
        self.nested = False
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
    """非阻塞获取库级锁；被占用 → `KBLockBusy`（带执行者信息）。祖先写运行持锁 → 嵌套放行。"""
    root = Path(root).resolve()
    path = root / LOCK_NAME
    token = _process_token()
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _try_lock(fd):
            nested = _nested_holder(root)
            if nested is not None:
                os.close(fd)
                return KBLock(root, None, nested, nested=True)
            raise KBLockBusy(read_holder(root))
        holder = {
            "pid": os.getpid(),
            "origin": origin,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "token": token,
        }
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


def run_locked(root_dir: str | Path, origin: str, fn: Callable[[], int]) -> int:
    """CLI 写命令的最外层取锁（决策P4.30-15）：持库级锁跑 `fn`，被占用 → 打印执行者、返回 EXIT_USAGE。

    库根无效 / 不可写 / 锁文件建不出（`GuanlanError` / `OSError`）时不取锁，照跑 `fn`，交给命令自己
    报错（它们都会先 `require_kb_root`）。
    """
    from .paths import require_kb_root

    try:
        root = require_kb_root(root_dir, writable=True)
        lock = acquire(root, origin)
    except KBLockBusy as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code
    except (GuanlanError, OSError):
        return fn()
    try:
        return fn()
    finally:
        lock.release()
