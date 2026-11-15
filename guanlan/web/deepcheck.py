"""体检深查的单飞只读作业（P4.34 §6.1）：引文核对这类慢的零 LLM 检查，点击才跑，在后台线程里跑完。

刻意**不进**任务中心（P4.33）：任务中心按写任务设计——取写锁排队、拍整库前像、取消即回滚、重启恢复。
只读核对放进去会排在长时间的入库后面、反过来挡住可写会话，还要在回滚 / 恢复各处加只读分支。这里只有：

- 进程内同时只跑一次：在跑时再「开始」返回当前状态，不另起一份（不排队、不报错）；
- 结果只留最近一次、只在内存：关掉体检浮层再打开仍看得到；服务重启即没了，重新点一次即可；
- 不取写锁、不能中途停止：跑完约十几秒，期间库若被写入，结果反映的是读到时的样子。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

__all__ = ["DeepCheck"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DeepCheck:
    """一个只读检查的单飞后台运行。`run` 是零参数函数，返回可 JSON 序列化的结果。"""

    def __init__(self, run: Callable[[], dict]) -> None:
        self._run = run
        self._lock = threading.Lock()
        self._state = "idle"  # idle / running / done / failed
        self._started: float | None = None
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._result: dict | None = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> dict:
        """没在跑就起一次（上次结果随之作废）；已在跑则不动。返回启动后的状态。"""
        with self._lock:
            if self._state != "running":
                self._state = "running"
                self._started = time.monotonic()
                self._started_at = _now()
                self._finished_at = None
                self._result = None
                self._error = None
                self._thread = threading.Thread(target=self._work, name="guanlan-deepcheck", daemon=True)
                self._thread.start()
            return self._snapshot_locked()

    def _work(self) -> None:
        try:
            result = self._run()
        except Exception as exc:  # noqa: BLE001 — 任何异常都要落成 failed，不能让状态停在 running
            with self._lock:
                self._state = "failed"
                self._error = f"{type(exc).__name__}: {exc}"
                self._finished_at = _now()
            return
        with self._lock:
            self._state = "done"
            self._result = result
            self._finished_at = _now()

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict:
        running = self._state == "running" and self._started is not None
        return {
            "state": self._state,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "running_s": round(time.monotonic() - self._started, 1) if running else None,
            "result": self._result,
            "error": self._error,
        }

    def join(self, timeout: float | None = None) -> None:
        """测试用：等当前这次跑完。"""
        t = self._thread
        if t is not None:
            t.join(timeout)
