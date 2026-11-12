"""可中途停止的长子进程（#173：一键入库准备阶段的「停止」）。

准备阶段（解析 → 整理 → 拆分 → 预检）里耗时的是两个子进程：`pdf-to-markdown` skill 的转换（MinerU 解析扫描版
PDF 可能要几分钟）和目录审校的 `agentao run`。Web 的准备作业在 worker 线程里跑，要停只能由别的线程置一个标志，
再由等子进程的那一处发现并动手。

做法：调用方用 `stop_scope(event)` 包住准备阶段；`convert._run_converter` 与 `runtime._run_in_new_session` 发现
当前有停止标志时，把子进程放进新会话 / 新进程组，等待改为轮询；标志一置位就**整组**结束子进程，抛 `StopRequested`。
没有 `stop_scope` 时（`guanlan convert`、ingest 等）行为不变。

整组杀的范围：skill 脚本、`mineru parse` 客户端、marker、agentao 及其派生的 shell。MinerU 4 的本地 doclib server
由 MinerU 以 `start_new_session=True` 拉起、在它自己的会话里，不受影响；客户端被杀后服务端那次解析照常跑完并进缓存，
下次导入同一文件直接命中。

标志放在 `ContextVar` 里而不是层层传参：转换内核、`run_agent_task` 与注入的 runner 签名都不用改，只有准备阶段
所在的线程看得到它。
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextvars import ContextVar

__all__ = [
    "POLL_SECONDS",
    "StopRequested",
    "check",
    "current",
    "isolate_kwargs",
    "kill_group",
    "stop_scope",
    "wait",
]

POLL_SECONDS = 0.2  # 轮询间隔：停止在 0.2 秒内生效，空转开销可忽略

_current: ContextVar[threading.Event | None] = ContextVar("guanlan_stop_event", default=None)


class StopRequested(Exception):
    """用户要求停止（Web 准备阶段的「停止」）。不是失败：调用方据此把任务记为已放弃。"""

    def __init__(self, message: str = "准备阶段已按要求停止") -> None:
        super().__init__(message)


@contextlib.contextmanager
def stop_scope(event: threading.Event | None) -> Iterator[None]:
    """在本线程（及其 `contextvars` 上下文）内登记停止标志；`event=None` 时什么也不做。"""
    if event is None:
        yield
        return
    token = _current.set(event)
    try:
        yield
    finally:
        _current.reset(token)


def current() -> threading.Event | None:
    return _current.get()


def check() -> None:
    """步与步之间调用：标志已置位 → 抛 `StopRequested`。"""
    ev = _current.get()
    if ev is not None and ev.is_set():
        raise StopRequested


def isolate_kwargs() -> dict:
    """`Popen` 参数：子进程自成一组，`kill_group` 才杀得干净、也不误伤本进程。"""
    if sys.platform == "win32":  # pragma: no cover - CI 主跑 POSIX
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    return {"start_new_session": True}


def kill_group(proc: subprocess.Popen) -> None:
    """结束子进程及其派生的整组进程（子进程须以 `isolate_kwargs()` 启动，会话首进程 pid 即组号）。"""
    try:
        if sys.platform == "win32":  # pragma: no cover - CI 主跑 POSIX
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()
    proc.wait()


def wait(proc: subprocess.Popen) -> int:
    """等子进程结束；当前停止标志置位 → 整组结束它并抛 `StopRequested`。无标志时等同 `proc.wait()`。"""
    ev = _current.get()
    if ev is None:
        return proc.wait()
    while True:
        if ev.is_set():
            kill_group(proc)
            raise StopRequested
        try:
            return proc.wait(timeout=POLL_SECONDS)
        except subprocess.TimeoutExpired:
            continue
