"""适配器状态目录 `~/.guanlan/im/<platform>/` 的原子读写 + 凭据锁（P4.21 §7.3/§8.5）。

写盘一律**原子替换**（临时文件 + `os.replace`）。这**不破 KB 零写承诺**（全在 KB 外），
但「零写 ≠ 进程无状态」必须说清楚（决策P4.21-20）。

**凭据锁是平台状态目录级，不是租户级**（决策P4.21-42）：原设计写「微信用 `account_id`、
飞书用 `app_id`」，**微信那条实现不了**——首次 `im-login` 时 `account_id` 要等扫码成功才拿得到，
两个并发的首次登录进程**无法预先互斥**，会同时扫码并互相覆盖 `account.json`。
统一到目录级即消除这个先有鸡还是先有蛋的问题。

代价说清楚：同一台机器不能同时跑两个不同飞书 app 的宿主。v1 接受——「一进程一库一平台」
本就是既定边界（§1）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..errors import EXIT_USAGE, GuanlanError

_ENV_HOME = "GUANLAN_IM_HOME"  # 测试与多实例部署用：整体挪走状态根


def im_home() -> Path:
    """状态根：`$GUANLAN_IM_HOME` 或 `~/.guanlan/im`。"""
    override = os.environ.get(_ENV_HOME)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".guanlan" / "im"


def state_dir(platform: str) -> Path:
    """某平台的状态目录（不存在则建，`chmod 0700`）。"""
    d = im_home() / platform
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:  # Windows / 奇异文件系统：权限收紧是加分项，失败不该拦住启动
        pass
    return d


def write_json_atomic(path: Path, data: dict, *, mode: int = 0o600) -> None:
    """临时文件 + `os.replace` 原子替换；`chmod` 在 replace **之前**（避免短暂的宽权限窗口）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        with_suppress_unlink(tmp)
        raise


def with_suppress_unlink(path: str | Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def read_json(path: Path) -> dict | None:
    """读一份状态文件；不存在 / 坏文件 → `None`（调用方据此走"未配置"路径）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _owner_pid(owner: dict | None) -> int | None:
    """从锁文件内容里取 pid。**读不出数字 → `None`**（＝"未知"，与"读出来但已死"必须分开）。

    两点：
    1. 裸 `int(owner.get("pid"))` 会在锁文件被写坏（或将来换了字段类型）时抛 `ValueError`，
       于是 `guanlan im` 不是打一行「凭据被占用」而是吐一屏栈——排查锁冲突时最不需要的东西。
    2. **不能退回 `0`**：`_pid_alive(0)` 是 `False`，那等于把"pid 读不出来"判成"进程已死"，
       从而**删掉别人刚抢到的锁** —— 正是 `acquire()` 里那条 `owner is None` 注释要挡的事故。
       故未知就是未知，调用方按"还活着"处理。
    """
    if not owner:
        return None
    try:
        return int(owner.get("pid") or 0)
    except (TypeError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """本机 pid 是否还活着（陈锁自动清理的判据）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # 存在但不属于我们 —— 仍算活着
        return True
    except OSError:
        return True
    return True


class CredentialLock:
    """三个子命令共用的**本机**凭据锁（决策P4.21-38/42）。

    `im-identify` / `im-login` / `im` 三者不能同时跑——它们抢的是同一份平台凭据：
    微信 iLink **一个 token 只允许一个长轮询实例**；飞书同一 `app_id` 起两个 WS 客户端会让消息
    **随机分发**到其中一条连接。**这不是可以绕开的实现细节，是平台语义。**

    抢不到即 `EXIT_USAGE` 拒启，且报错必须**同时给出 owner pid 与占用者子命令名**——
    只说「凭据被占用」，用户分不清是自己起了两个服，还是忘了关 identify。
    不做跨机分布式锁。
    """

    def __init__(self, platform: str, *, command: str) -> None:
        self._platform = platform
        self._command = command
        self._path = state_dir(platform) / "credential.lock"
        self._held = False

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> None:
        """`O_CREAT|O_EXCL` 抢锁；陈锁（pid 已死）自动清理后重试一次。"""
        for attempt in (0, 1):
            try:
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                owner = read_json(self._path)
                pid = _owner_pid(owner)  # None = pid 读不出来（按"还活着"处理，绝不误删）
                # ★ `owner is None` **绝不能当陈锁**：`O_EXCL` 建出文件与写进 pid 之间有一个真实
                # 窗口，两个同时启动的宿主里后到的那个正会读到空文件——按"pid 读不出 ⇒ 进程已死"
                # 处理就会**把对方刚抢到的锁删掉**，于是两个进程同时持有同一份平台凭据。而那正是
                # 本锁存在的理由（一个 iLink token 只允许一个长轮询；同 app_id 两条 WS 会随机分发）。
                # 只有**能读到 pid、且该 pid 确已死**才算陈锁。
                if attempt == 0 and pid is not None and not _pid_alive(pid):
                    with_suppress_unlink(self._path)  # 陈锁：进程已死，清掉重试
                    continue
                if owner is None:
                    raise GuanlanError(
                        f"另一个进程正在获取 {self._platform} 凭据锁（{self._path}）——稍后重试 "
                        f"`{self._command}`；若确认没有别的进程在跑，手动删掉该文件即可。",
                        exit_code=EXIT_USAGE,
                    ) from None
                raise GuanlanError(
                    f"{owner.get('command', '另一个 guanlan im 进程')} (pid {pid or '未知'}) "
                    f"正持有 {self._platform} 凭据——等它退出或先停掉它，再运行 `{self._command}`。",
                    exit_code=EXIT_USAGE,
                ) from None
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"pid": os.getpid(), "command": self._command}, fh)
                self._held = True
                return

    def release(self) -> None:
        """幂等释放。只删**自己**写的锁（pid 比对），避免误删别人刚抢到的那把。"""
        if not self._held:
            return
        self._held = False
        if _owner_pid(read_json(self._path)) == os.getpid():  # 同 acquire：坏 pid 不炸、只是不删
            with_suppress_unlink(self._path)

    def __enter__(self) -> CredentialLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
