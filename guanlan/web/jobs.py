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

# 进度 sink（决策P4.6.1-11）：作业 thunk 收到一个 `emit(line)` 回调，把一行人读进度**实时**累加进
# `job.output`（running 期即可被 `/api/jobs/{id}` 轮询看见，不必等 done）。既有 thunk 忽略它、仍靠
# `print()` 经 redirect 进 buf（行为不变）；仅 parse thunk 用它把转换内核的 stderr 行实时推上去。
Emit = Callable[[str], None]


class WriteGate:
    """进程级单写者协调（P4.5 §5 决策P4.5-6/10）：一把 `write_lock` + `active_writable_turns` 计数。

    **两者刻意分离、互不混用**（评审 P4.5 #2）：

    - `write_lock`（进程级 `threading.Lock`）：**只包真正的写执行**——JobQueue worker 跑 `fn()`
      前后、可写 chat turn 起跑/收尾、撤销本轮写回放、`GET /graph` 的 `build_and_write_graph`
      前后都 acquire/release，互斥 → 任意时刻至多一个写者，`raw/`/`wiki/`/`workspace/`/`graph/`
      四树写永不交错、`raw/` 快照窗口不被并发写互踩。跨「JobQueue 后台线程写者」与「async chat
      写者」共享，故是 `threading.Lock`（非 `asyncio.Lock`）；async 侧须**异步获取**
      （`anyio.to_thread.run_sync(write_lock.acquire)`），绝不在事件循环线程直接阻塞 acquire。
    - `active_writable_turns`（独立计数 + 自有锁）：层③ 时序互斥——可写 turn 活跃期间宿主写端点
      （`/api/raw`/`ingest`/`heal`）一律 `423`，兜「shell `curl http://127.0.0.1/api/raw` 让宿主
      替 agent 写 `raw/`」的旁路（决策P4.5-10）。**独立于 `write_lock`/`JobQueue._lock`**：它表
      「有可写会话在跑」、不是「有人持写锁」，两者语义不同。
    """

    def __init__(self) -> None:
        self.write_lock = threading.Lock()
        self._turns = 0
        self._turns_lock = threading.Lock()  # 独立于 write_lock：只护计数器
        # wiki 写代际（P4.5-4 修订）：每次 wiki/ 内容可能变化（ingest/heal 作业、改了 violation
        # 集的可写 turn）即 +1，单调递增。可写 turn 用它判「缓存的 check 基线是否仍准」——同一会话
        # 连续对话、代际没变 → 复用基线、收尾只查一次（省掉每轮重拍基线那 ~0.36s）；代际变了或
        # 首轮 → 重新拍。纯软优化：漏/多 bump 至多多查一次基线，绝不影响守卫（收尾 check 仍每轮全跑）。
        self._wiki_generation = 0
        self._gen_lock = threading.Lock()  # 独立小锁：只护代际计数原子自增

    def bump_wiki_generation(self) -> None:
        """标记 wiki/ 可能已变（ingest/heal 作业完成、或改了 violation 集的可写 turn 收尾时调）。"""
        with self._gen_lock:
            self._wiki_generation += 1

    @property
    def wiki_generation(self) -> int:
        with self._gen_lock:
            return self._wiki_generation

    def enter_writable(self) -> None:
        with self._turns_lock:
            self._turns += 1

    def exit_writable(self) -> None:
        with self._turns_lock:
            self._turns -= 1
            if self._turns < 0:
                self._turns = 0

    @property
    def active_writable_turns(self) -> int:
        with self._turns_lock:
            return self._turns

    def acquire_thunk(self, held: list[bool]) -> Callable[[], None]:
        """返回一个在**调用线程内** acquire `write_lock` 并置 `held[0]=True` 的 thunk（评审 P1）。

        async 侧统一这样取写锁，杜绝取消窗口泄漏：
        ```
        held = [False]
        try:
            await anyio.to_thread.run_sync(write_gate.acquire_thunk(held))
            ...  # 临界区
        finally:
            if held[0]:
                write_gate.write_lock.release()
        ```
        `anyio.to_thread.run_sync` 默认 `abandon_on_cancel=False`——被取消时仍等 `acquire()` 返回
        （锁已到手）、再在 await 处抛 `CancelledError`，使「await 之后」的赋值永不执行。把置位放进
        thunk、且 `held` 在 `try` **之前**声明，则不论 `CancelledError` 落在哪、`finally` 读到的都是
        真实持有态，绝不泄漏进程级写锁（否则后续 ingest/heal/graph/可写 turn 全永久卡死）。
        """

        def _acq() -> None:
            self.write_lock.acquire()
            held[0] = True

        return _acq


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
    # 进度写锁（决策P4.6.1-14）：parse 内核 `progress=` 经 Popen 两条并发 drain 线程调 emit，emit 写
    # 非线程安全的 `StringIO buf` + `job.output`、又与 worker finally 的 `job.output` 赋值竞态。故 emit
    # 的 buf.write+getvalue+赋值 与 finally 的 getvalue 全取这把锁。非流式作业单线程、不触锁外并发。
    output_lock: threading.Lock = field(default_factory=threading.Lock)


class JobQueue:
    """单 worker 线程 + FIFO 队列 + 内存作业表。线程随进程退出（daemon）。"""

    def __init__(
        self,
        write_lock: threading.Lock | None = None,
        on_job_done: Callable[[str], None] | None = None,
    ) -> None:
        self._queue: queue.Queue[tuple[Job, Callable[[Emit], object]]] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()  # 仅护 _jobs / _counter；worker 改 job 字段无并发读写竞态点
        self._counter = 0
        # 作业完工回调（域无关）：worker 跑完即以 job.kind 调它；kind→语义（如哪些写 wiki）
        # 的判定留给注入方（create_app 用它 bump wiki 代际，见 P4.5-4 缓存基线）。None = 不回调。
        self._on_job_done = on_job_done
        # 单写者写锁（P4.5 决策P4.5-6）：worker 跑 `fn()`（真正的写执行）时持有，与可写 chat
        # turn / 撤销回放 / GET /graph 串行。None = 未注入（旧测试 / 纯读场景）→ 不串行（沿用 P4
        # 行为：唯一写者就是本 worker，自带 FIFO 串行）。绝不与 `_lock`（只护作业表）混用。
        self._write_lock = write_lock
        self._worker = threading.Thread(target=self._run, name="guanlan-jobs", daemon=True)
        self._worker.start()

    def _register(self, kind: str, fn: Callable[[Emit], object]) -> Job:
        """建表 + 入队的内部归口，返回 Job 本体（enqueue / submit_and_wait 共用）。

        `fn` 统一收一个 `emit(line)` 进度 sink（决策P4.6.1-11）：既有 thunk 忽略它、仅 parse 用之。
        签名升 `Callable[[Emit], object]` **须在本归口统一**——它同服务 `enqueue`（异步）与
        `submit_and_wait`（同步），只改 enqueue 会漏掉同步作业（raw_write/upload/delete）。
        """
        with self._lock:
            self._counter += 1
            job = Job(id=str(self._counter), kind=kind)
            self._jobs[job.id] = job
        self._queue.put((job, fn))
        return job

    def enqueue(self, kind: str, fn: Callable[[Emit], object]) -> str:
        """登记一个作业并入队，立即返回 `job_id`（不阻塞）。

        `fn(emit)` 返回**退出码 `int`**（ingest/投喂）**或**带 `.exit_code` 的结构化结果（heal 的
        `HealRun`）——worker 鸭子分流（见 `_run`）。异步作业（ingest/heal/parse）用：入队即返回
        job_id，前端轮询 `/api/jobs/{id}`（parse 期间即可见 emit 推上的增量 backend 日志）。
        """
        return self._register(kind, fn).id

    def submit_and_wait(self, kind: str, fn: Callable[[Emit], object]) -> Job:
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

            # 增量进度 sink（决策P4.6.1-11/14）：thunk 经它把一行进度实时累加进 job.output，running
            # 期即可被轮询看见。buf 写 + 整串赋值在 job.output_lock 内（parse 的 drain 线程并发调时
            # 不撕裂、不与 finally 竞态）。既有 thunk 不调它（仍 print() 经 redirect）；仅 parse 用。
            def emit(line: str, _job: Job = job, _buf: io.StringIO = buf) -> None:
                with _job.output_lock:
                    _buf.write(line + "\n")
                    _job.output = _buf.getvalue()

            # 单写者写锁（P4.5）：包住真正的写执行 `fn(emit)`，与可写 chat turn / 撤销 / GET /graph
            # 串行。在 worker 线程上阻塞 acquire 无碍（非事件循环线程）；None 时退回无锁（P4 行为）。
            if self._write_lock is not None:
                self._write_lock.acquire()
            try:
                # 进程级 redirect：捕获 run_ingest 的人读 stdout/stderr。只在此单 worker 串行
                # 发生，故无跨线程竞态（读/问答路径都不打印，见模块 docstring 红线）。
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    outcome = fn(emit)
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
                if self._write_lock is not None:
                    self._write_lock.release()  # 写执行收尾即释放，早于状态翻转/唤醒
                # 顺序要紧：先写 output、再翻 state="done"，最后 set()。轮询读者只在见到
                # state=="done" 后读 output（同线程程序序 + GIL 原子赋值保证"见 done 必见完整
                # output"，无锁可行）；submit_and_wait 的等待者由 done_event 唤醒、醒来即见完整
                # output/exit_code/state（决策P4.1-2）。终态 getvalue 取 output_lock：与 parse 的
                # drain 线程若有迟到 emit 不撕裂（决策P4.6.1-14；fn 返回后 drain 线程通常已 join，
                # 取锁仅作纵深防御、与最后一次 emit 幂等）。
                with job.output_lock:
                    job.output = buf.getvalue()
                job.state = "done"
                job.done_event.set()
            # 完工回调在 write_lock 释放后调（不在临界区内做额外工作）；best-effort：异常不能
            # 让 worker 死（它已捕获作业本体异常，这里再裹一层只为隔离回调意外）。
            if self._on_job_done is not None:
                with contextlib.suppress(Exception):
                    self._on_job_done(job.kind)
