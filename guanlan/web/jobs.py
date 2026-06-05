"""单后台 worker + 作业表（P4，见 docs/P4-Web宿主.md §4.2 决策P4-5）。

v1 的**唯一写作业 `ingest`** 进一个 `queue.Queue`，由**单条** worker 线程（与 uvicorn 事件
循环并存）FIFO 取出串行执行。串行保两件事：① 至多一个写作业，`raw/` 前后快照不被并发写互踩
（P2 门禁假设单写者）；② `run_ingest` 用模块级 `print` 输出人读文本，串行 + `redirect_stdout`
捕获**无跨线程竞态**。**红线：`redirect_stdout` 捕获只许单 worker 做**——故零 LLM 报告与问答
都不进此队列（前者返回 dataclass、后者返回字符串，皆无 stdout 捕获）。

作业表只在内存（单进程、单用户，进程退出即清）；`job_id` 用自增计数器 + 进程内字典，不引外部存储。
"""

from __future__ import annotations

import contextlib
import io
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ..errors import EXIT_AGENT_ERROR

JobState = Literal["queued", "running", "done"]


@dataclass
class Job:
    """一个写作业的内存记录。`output` 是捕获的人读输出（决策P4-7）。"""

    id: str
    kind: str
    state: JobState = "queued"
    exit_code: int | None = None
    output: str = ""


class JobQueue:
    """单 worker 线程 + FIFO 队列 + 内存作业表。线程随进程退出（daemon）。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Job, Callable[[], int]]] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()  # 仅护 _jobs / _counter；worker 改 job 字段无并发读写竞态点
        self._counter = 0
        self._worker = threading.Thread(target=self._run, name="guanlan-jobs", daemon=True)
        self._worker.start()

    def enqueue(self, kind: str, fn: Callable[[], int]) -> str:
        """登记一个作业并入队，立即返回 `job_id`（不阻塞）。`fn` 返回退出码。"""
        with self._lock:
            self._counter += 1
            job_id = str(self._counter)
            job = Job(id=job_id, kind=kind)
            self._jobs[job_id] = job
        self._queue.put((job, fn))
        return job_id

    def get_job(self, job_id: str) -> Job | None:
        """取作业快照；未知 id → None（端点据此 404）。"""
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self) -> None:
        while True:
            job, fn = self._queue.get()
            job.state = "running"
            buf = io.StringIO()
            try:
                # 进程级 redirect：捕获 run_ingest 的人读 stdout/stderr。只在此单 worker 串行
                # 发生，故无跨线程竞态（读/问答路径都不打印，见模块 docstring 红线）。
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    job.exit_code = fn()
            except Exception as exc:  # noqa: BLE001 — worker 绝不能死，异常归一为作业失败
                job.exit_code = EXIT_AGENT_ERROR
                buf.write(f"\n作业执行异常：{type(exc).__name__}: {exc}")
            finally:
                # 顺序要紧：先写 output 再翻 state="done"。读者只在见到 state=="done" 后读
                # output，故同线程程序序 + GIL 原子赋值保证"见 done 必见完整 output"（无锁可行）。
                job.output = buf.getvalue()
                job.state = "done"
