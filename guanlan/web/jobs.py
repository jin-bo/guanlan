"""单后台 worker + 作业表（P4，见 docs/P4-Web宿主.md §4.2 决策P4-5）。

写作业（`ingest`、P4.1 投喂、P4.3 `heal`）进一个 `queue.Queue`，由**单条** worker 线程（与
uvicorn 事件循环并存）FIFO 取出串行执行。串行保两件事：① 至多一个写作业，`raw/` 前后快照不被
并发写互踩（P2 门禁假设单写者）；② 写作业用模块级 `print` 输出人读文本，串行 + `redirect_stdout`
捕获**无跨线程竞态**。**红线：`redirect_stdout` 捕获只许单 worker 做**——故零 LLM 报告与问答
都不进此队列（前者返回 dataclass、后者返回字符串，皆无 stdout 捕获）。

`fn` 可返回**退出码 `int`**（ingest/投喂）或带 `.exit_code` 的**结构化结果**（heal 的 `HealRun`，
决策P4.3-1）——worker 鸭子分流，前者存 `exit_code`、后者整体存 `result` 再取退出码；worker
**域无关**（只碰 `.exit_code`、不 import heal 类型），`result` 的序列化落在端点。

作业表只在内存（单进程、单用户，进程退出即清）；`job_id` 用自增计数器 + 进程内字典，不引外部存储。
"""

from __future__ import annotations

import contextlib
import io
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from ..errors import EXIT_AGENT_ERROR

JobState = Literal["queued", "running", "done"]


@dataclass
class Job:
    """一个写作业的内存记录。`output` 是捕获的人读输出（决策P4-7）。

    `done_event` 在 worker 收尾时 `set()`，供 `submit_and_wait` 的同步等待者唤醒（P4.1
    决策P4.1-2：投喂作业入队后**同步等完成**再返回，复用 ingest 同一条 FIFO worker）。
    """

    id: str
    kind: str
    state: JobState = "queued"
    exit_code: int | None = None
    output: str = ""
    result: object | None = None  # 进程内结构化结果（heal 的 HealRun）；ingest/投喂为 None。
    done_event: threading.Event = field(default_factory=threading.Event)


class JobQueue:
    """单 worker 线程 + FIFO 队列 + 内存作业表。线程随进程退出（daemon）。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Job, Callable[[], object]]] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()  # 仅护 _jobs / _counter；worker 改 job 字段无并发读写竞态点
        self._counter = 0
        self._worker = threading.Thread(target=self._run, name="guanlan-jobs", daemon=True)
        self._worker.start()

    def _register(self, kind: str, fn: Callable[[], object]) -> Job:
        """建表 + 入队的内部归口，返回 Job 本体（enqueue / submit_and_wait 共用）。"""
        with self._lock:
            self._counter += 1
            job = Job(id=str(self._counter), kind=kind)
            self._jobs[job.id] = job
        self._queue.put((job, fn))
        return job

    def enqueue(self, kind: str, fn: Callable[[], object]) -> str:
        """登记一个作业并入队，立即返回 `job_id`（不阻塞）。

        `fn` 返回**退出码 `int`**（ingest/投喂）**或**带 `.exit_code` 的结构化结果（heal 的
        `HealRun`）——worker 鸭子分流（见 `_run`）。异步作业（ingest/heal）用：入队即返回
        job_id，前端轮询 `/api/jobs/{id}`。
        """
        return self._register(kind, fn).id

    def submit_and_wait(self, kind: str, fn: Callable[[], object]) -> Job:
        """入队一个作业并**阻塞到它完成**，返回 Job 本体（直接持 Job，无 Optional）。

        同步作业（P4.1 投喂）用：作业自身极快（一次文件写），单独起轮询体验割裂；故入队后
        在调用线程上 `done_event.wait()` 等其在**同一条 FIFO worker** 上轮到并跑完。端点经
        `anyio.to_thread` 调用本方法，阻塞的是线程池线程、不堵事件循环（决策P4.1-2）。
        ⚠️ 会**排在队列前序写作业之后**：若此刻有 ingest 在飞，会一直等到它跑完才轮到。
        """
        job = self._register(kind, fn)
        job.done_event.wait()
        return job

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
                    outcome = fn()
                # 鸭子分流（决策P4.3-1）：int → 退出码（ingest/投喂，口径不变）；否则为带
                # `.exit_code` 的结构化结果（heal 的 HealRun），整体存进 result、再取退出码。
                # worker 仍**域无关**——只碰 `.exit_code`，不 import heal 类型；result 的序列化
                # 落在 /api/jobs/{id} 端点。
                if isinstance(outcome, int):
                    job.exit_code = outcome
                else:
                    job.result = outcome
                    job.exit_code = outcome.exit_code
            except Exception as exc:  # noqa: BLE001 — worker 绝不能死，异常归一为作业失败
                job.exit_code = EXIT_AGENT_ERROR
                buf.write(f"\n作业执行异常：{type(exc).__name__}: {exc}")
            finally:
                # 顺序要紧：先写 output、再翻 state="done"，最后 set()。轮询读者只在见到
                # state=="done" 后读 output（同线程程序序 + GIL 原子赋值保证"见 done 必见完整
                # output"，无锁可行）；submit_and_wait 的等待者由 done_event 唤醒、醒来即见完整
                # output/exit_code/state（决策P4.1-2）。
                job.output = buf.getvalue()
                job.state = "done"
                job.done_event.set()
