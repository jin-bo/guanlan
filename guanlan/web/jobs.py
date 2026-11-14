"""单后台 worker + 作业表（P4，见 docs/P4-Web宿主.md §4.2 决策P4-5）。

写作业（`ingest`、P4.1 投喂、P4.3 `heal`）进一个按提交序号排序的队列，由**单条** worker 线程（与
uvicorn 事件循环并存）FIFO 取出串行执行。串行保两件事：① 至多一个写作业，`raw/` 前后快照不被
并发写互踩（P2 门禁假设单写者）；② 写作业用模块级 `print` 输出人读文本，串行 + `redirect_stdout`
捕获**无跨线程竞态**。**红线：`redirect_stdout` 捕获只许单 worker 做**——故零 LLM 报告与问答
都不进此队列（前者返回 dataclass、后者返回字符串，皆无 stdout 捕获）。

`fn` 可返回**退出码 `int`**（ingest/投喂）或带 `.exit_code` 的**结构化结果**（heal 的 `HealRun`，
决策P4.3-1）——worker 鸭子分流，前者存 `exit_code`、后者整体存 `result` 再取退出码；worker
**域无关**（只碰 `.exit_code`、不 import heal 类型），`result` 的序列化落在端点。

作业表只在内存（单进程、单用户，进程退出即清）；`job_id` 用自增计数器 + 进程内字典，不引外部存储。
需要持久化的作业（P4.33 后台任务：ingest/backfill/heal/audit）由注入的 `observer`（`web/tasks.py` 的
`TaskStore`）在入队前建任务信封、经 `task_id` 关联到本作业；worker 在起跑 / 每次输出 / 收尾三处回调它。
worker 仍域无关：只认 `task_id` 与观察者接口，不知道信封长什么样；观察者异常一律吞掉，不影响作业本身。
"""

from __future__ import annotations

import contextlib
import io
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from .. import kblock
from ..errors import EXIT_AGENT_ERROR, EXIT_USAGE
from ..runtime import HEARTBEAT_INTERVAL_S

JobState = Literal["queued", "running", "done"]

# 进度 sink（决策P4.6.1-11）：作业 thunk 收到一个 `emit(line)` 回调，把一行人读进度**实时**累加进
# `job.output`（running 期即可被 `/api/jobs/{id}` 轮询看见，不必等 done）。既有 thunk 忽略它、仍靠
# `print()` 经 redirect 进 buf（行为不变）；仅 parse thunk 用它把转换内核的 stderr 行实时推上去。
Emit = Callable[[str], None]

# 心跳回调（A+ 心跳方案）：入队时可附一个 `progress(elapsed_s) -> 一行人读进度`。worker 在作业
# running 期每隔 `_JOB_HEARTBEAT_INTERVAL_S` 秒调它，把返回串写进**瞬时**字段 `job.progress`
# （pollJob running 态实时渲染）。与 `emit`/`job.output` 正交：progress 是会被收尾清空的活跃提示、
# 不进最终结果，故入库等长跑作业既能 running 期见进展、完成后又只剩干净摘要（决策：瞬时进度行）。
ProgressCb = Callable[[float], str]
# = 共用节拍 HEARTBEAT_INTERVAL_S 的本模块别名（保留独立名便于测试 monkeypatch）。
_JOB_HEARTBEAT_INTERVAL_S = HEARTBEAT_INTERVAL_S

# P4.33 T2 取锁：后台任务等进程内写锁时按此间隔轮询取消标志；库被命令行占用时按退避重试，累计超过上限判失败。
LOCK_POLL_S = 0.2
LOCK_BACKOFF_START_S = 5.0
LOCK_BACKOFF_MAX_S = 60.0
LOCK_WAIT_LIMIT_S = 30 * 60
# 开始前被取消的作业输出 / 任务信封 error 共用的说明。
CANCELLED_BEFORE_START = "已取消：任务还没开始，没有写入任何内容。"


class JobObserver(Protocol):
    """作业生命周期观察者（P4.33）：只对带 `task_id` 的作业回调。实现方须自行保证线程安全——
    `on_output` 可能来自 worker 线程（`print` 经 redirect）或解析作业的 drain 线程（`emit`）。"""

    def on_start(self, job: Job) -> None: ...

    def on_output(self, job: Job, text: str) -> None: ...

    def on_waiting(self, job: Job, holder: str) -> None: ...

    def on_finish(self, job: Job) -> None: ...


class _ObservedIO(io.StringIO):
    """`StringIO` 外加写入回调：worker 的捕获缓冲区，每段写入同时转给观察者（落任务日志）。"""

    def __init__(self, on_write: Callable[[str], None] | None) -> None:
        super().__init__()
        self._on_write = on_write

    def write(self, s: str) -> int:
        n = super().write(s)
        if self._on_write is not None and s:
            with contextlib.suppress(Exception):  # 日志写失败绝不能拖垮作业
                self._on_write(s)
        return n


class KBWriteLock:
    """`threading.Lock` 外加库级 OS 文件锁（P4.30，决策P4.30-15）：Web 写者与 CLI 写命令互斥。

    接口与 `threading.Lock` 相容（`acquire` / `release` / `locked` / with）。`acquire` 先取进程内锁，
    再**非阻塞**取库级锁；库正被 CLI 等其它进程写 → 释放进程内锁并抛 `kblock.KBLockBusy`（不排队）。
    只在每个写操作期间持有——开着 Web 时 CLI 照样能写。锁文件建不出来（OSError，如只读介质）时
    退化为只有进程内锁，不让 Web 因此不可用。
    """

    def __init__(self, root: Path) -> None:
        self._lock = threading.Lock()
        self._root = Path(root)
        self._kb: kblock.KBLock | None = None
        kblock.reserve_token_slot()  # 构造在启动线程：之后各写线程取锁只换令牌值，不增删 os.environ 的键

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if not self._lock.acquire(blocking, timeout):
            return False
        try:
            self._kb = kblock.acquire(self._root, "web")
        except OSError:
            self._kb = None
        except BaseException:  # KBLockBusy 及任何意外：不能把进程内锁留在持有态，否则此后所有写永久卡死
            self._lock.release()
            raise
        return True

    def release(self) -> None:
        kb, self._kb = self._kb, None
        try:
            if kb is not None:
                kb.release()
        finally:  # 库级锁释放出错（如 os.close 的 EIO）也必须还进程内锁，否则此后所有写永久卡死
            self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    @property
    def owns_kb_lock(self) -> bool:
        """本进程此刻**真持**库级 OS 锁（不是嵌套放行、也不是锁文件建不出时的进程内退化锁）。
        只有这时才能断定原执行者已退出、可以恢复它留下的任务。"""
        kb = self._kb
        return kb is not None and not kb.nested and kb.held

    def __enter__(self) -> KBWriteLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


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

    def __init__(self, root: Path | None = None) -> None:
        # root 给定（可写宿主）→ 写锁同时持库级 OS 文件锁（P4.30）；None（reader / 旧测试）→ 纯进程内锁。
        self.write_lock = KBWriteLock(root) if root is not None else threading.Lock()
        self._turns = 0
        self._turns_lock = threading.Lock()  # 独立于 write_lock：只护计数器
        # wiki 写代际（P4.5-4 修订）：每次 wiki/ 内容可能变化（ingest/heal 作业、改了 violation
        # 集的可写 turn）即 +1，单调递增。可写 turn 用它判「缓存的 check 基线是否仍准」——同一会话
        # 连续对话、代际没变 → 复用基线、收尾只查一次（省掉每轮重拍基线那 ~0.36s）；代际变了或
        # 首轮 → 重新拍。纯软优化：漏/多 bump 至多多查一次基线，绝不影响守卫（收尾 check 仍每轮全跑）。
        self._wiki_generation = 0
        self._gen_lock = threading.Lock()  # 独立小锁：只护代际计数原子自增
        # P4.33：「此刻谁在写」的描述回调（create_app 注入 TaskStore.running_brief）。可写 turn 等写锁前
        # 用它给前端推一帧等待提示；只用于显示，不参与锁的正确性。None = 不推（reader / 旧测试）。
        self.describe_writer: Callable[[], dict | None] | None = None

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

    def try_acquire_thunk(self, held: list[bool]) -> Callable[[], bool]:
        """`acquire_thunk` 的非阻塞版（P4.33）：在调用线程内**试一次**取写锁，取到则置 `held[0]=True` 并返回 True，
        被占返回 False。可写 turn 先试一次、取不到再推等待提示并阻塞取——避免「先查 locked() 再阻塞取」之间
        锁被别人抢走、这一轮无声卡住。置位守法同 `acquire_thunk`（取消安全）。库被 CLI 占用照样抛 `KBLockBusy`。"""

        def _try() -> bool:
            if self.write_lock.acquire(blocking=False):
                held[0] = True
                return True
            return False

        return _try


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
    # 瞬时进度（A+ 心跳）：running 期由 worker 心跳线程刷新的一行活跃提示（如「⏳ 仍在运行 45s」），
    # worker 收尾即清空——**不进最终结果**（最终只看 output）。无心跳回调的作业恒为 ""。
    progress: str = ""
    result: object | None = None  # 进程内结构化结果（heal 的 HealRun）；ingest/投喂为 None。
    done_event: threading.Event = field(default_factory=threading.Event)
    # 进度写锁（决策P4.6.1-14）：parse 内核 `progress=` 经 Popen 两条并发 drain 线程调 emit，emit 写
    # 非线程安全的 `StringIO buf` + `job.output`、又与 worker finally 的 `job.output` 赋值竞态。故 emit
    # 的 buf.write+getvalue+赋值 与 finally 的 getvalue 全取这把锁。非流式作业单线程、不触锁外并发。
    output_lock: threading.Lock = field(default_factory=threading.Lock)
    # P4.33：关联的后台任务信封 id（`TaskStore` 建的）；None = 不持久化的作业（投喂、上传、解析等）。
    task_id: str | None = None
    # P4.33 T2 取消（只对还没开始的作业）：`started` = 已取得写锁、即将执行；`cancel_requested` 由
    # `JobQueue.cancel_task` 置位；worker 见到后不执行 fn，置 `cancelled` 并照常收尾（exit_code 为 None）。
    # 三者的读写都在 `JobQueue._lock` 内，「取消」与「开始」不会同时成立。
    started: bool = False
    cancel_requested: bool = False
    cancelled: bool = False
    # P4.33 T2 等命令行释放库：等待截止时刻、下次退避间隔、上次看到的占用者。等待期间作业**不占 worker**，
    # 而是「停放」起来、到点按原提交顺序放回队列（见 `JobQueue._park`）。
    lock_wait_deadline: float | None = None
    lock_wait_delay: float = 0.0
    lock_holder: str | None = None
    # 截止时刻属于哪一段等待（`JobQueue._lock_epoch`）：期间有作业取到过写锁，说明那次占用已经结束，再遇到占用
    # 是新的一段等待，截止时刻重新算——否则停放在前面任务后面时记下的截止时刻会被带到很久以后，一遇占用立即判失败。
    lock_wait_epoch: int = -1


class JobQueue:
    """单 worker 线程 + FIFO 队列 + 内存作业表。线程随进程退出（daemon）。"""

    def __init__(
        self,
        write_lock: threading.Lock | None = None,
        on_job_done: Callable[[str], None] | None = None,
        observer: JobObserver | None = None,
    ) -> None:
        # 按提交序号排序的优先队列（P4.33 T2）：停放后放回的任务回到它原来的位置，不被后提交的超过。
        # 未停放过的作业序号单调递增，行为与 FIFO 相同。
        self._queue: queue.PriorityQueue[
            tuple[int, tuple[Job, Callable[[Emit], object], ProgressCb | None]]
        ] = queue.PriorityQueue()
        # 等命令行释放库而停放的任务作业：序号 → 队列项；一个共用定时器到点把它们一起按序放回。
        self._parked: dict[int, tuple] = {}
        self._by_task: dict[str, Job] = {}  # task_id → 作业（取消时直接查，不扫整张作业表）
        self._park_timer: threading.Timer | None = None
        self._lock_epoch = 0  # 每有作业取到写锁 +1：区分不同的「库被占用」等待段（见 Job.lock_wait_epoch）
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
        self._observer = observer  # P4.33 任务信封观察者；None = 不持久化（旧测试 / reader）
        self._worker = threading.Thread(target=self._run, name="guanlan-jobs", daemon=True)
        self._worker.start()

    def _register(
        self,
        kind: str,
        fn: Callable[[Emit], object],
        progress: ProgressCb | None = None,
        task_id: str | None = None,
    ) -> Job:
        """建表 + 入队的内部归口，返回 Job 本体（enqueue / submit_and_wait 共用）。

        `fn` 统一收一个 `emit(line)` 进度 sink（决策P4.6.1-11）：既有 thunk 忽略它、仅 parse 用之。
        签名升 `Callable[[Emit], object]` **须在本归口统一**——它同服务 `enqueue`（异步）与
        `submit_and_wait`（同步），只改 enqueue 会漏掉同步作业（raw_write/upload/delete）。

        `progress`（可选，A+ 心跳）：附则 worker running 期周期刷新 `job.progress`；None = 无心跳。
        """
        with self._lock:
            self._counter += 1
            job = Job(id=str(self._counter), kind=kind, task_id=task_id)
            self._jobs[job.id] = job
            if task_id is not None:
                self._by_task[task_id] = job
        self._put((job, fn, progress))
        return job

    def enqueue(
        self,
        kind: str,
        fn: Callable[[Emit], object],
        *,
        progress: ProgressCb | None = None,
        task_id: str | None = None,
    ) -> str:
        """登记一个作业并入队，立即返回 `job_id`（不阻塞）。

        `fn(emit)` 返回**退出码 `int`**（ingest/投喂）**或**带 `.exit_code` 的结构化结果（heal 的
        `HealRun`）——worker 鸭子分流（见 `_run`）。异步作业（ingest/heal/parse）用：入队即返回
        job_id，前端轮询 `/api/jobs/{id}`（parse 期间即可见 emit 推上的增量 backend 日志）。

        `progress`（可选）：长跑作业（如 ingest）附心跳回调，running 期把活跃提示刷进 `job.progress`。
        `task_id`（可选，P4.33）：关联已建好的任务信封，worker 据此回调 `observer`。
        """
        return self._register(kind, fn, progress, task_id).id

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

    def cancel_task(self, task_id: str) -> bool:
        """取消一个**还没开始**（未取得写锁）的任务作业：置取消标志，worker 取到或等锁时见到即跳过。
        已开始 / 已结束 / 找不到 → False。"""
        with self._lock:
            job = self._by_task.get(task_id)
            if job is None or job.started or job.state == "done":
                return False
            job.cancel_requested = True
            item = self._parked.pop(int(job.id), None)
            if item is not None:  # 正停放着等命令行释放库：立即放回，worker 取到即按取消收尾
                self._put(item)
            return True

    def get_job(self, job_id: str) -> Job | None:
        """取作业快照；未知 id → None（端点据此 404）。"""
        with self._lock:
            return self._jobs.get(job_id)

    def _notify(self, method: str, job: Job, *args: object) -> None:
        """回调观察者（只对带 task_id 的作业）；观察者的任何异常都吞掉——worker 绝不能因它而死。"""
        if self._observer is None or job.task_id is None:
            return
        with contextlib.suppress(Exception):
            getattr(self._observer, method)(job, *args)

    def _mark_started(self, job: Job) -> bool:
        """取得写锁（或无锁）后、执行前：在表锁内二选一——已请求取消则返回 False，否则记 started。"""
        with self._lock:
            if job.cancel_requested:
                return False
            job.started = True
            return True

    def _acquire_for(self, job: Job) -> tuple[Exception | None, bool, float | None]:
        """为作业取写锁，返回 `(lock_error, cancelled, retry_after)`；三者皆空（`(None, False, None)`）时**已持锁**。

        - 非任务作业（投喂、上传等，`task_id is None`）：与 P4.33 之前相同——阻塞取，库被占用立即失败。
        - 任务作业：等**进程内**写锁（可写会话等）时每 LOCK_POLL_S 查一次取消。库被**命令行**占用（`KBLockBusy`）
          时不在这里等：记 waiting_lock（占用者换了就更新），返回 `retry_after` 让 worker 把它定时重新入队、先去跑
          后面的作业——否则一个等锁的任务会占住唯一的 worker 长达 LOCK_WAIT_LIMIT_S，后面的投喂、上传都跟着卡住。
          退避从 LOCK_BACKOFF_START_S 起翻倍、封顶 LOCK_BACKOFF_MAX_S；累计超过 LOCK_WAIT_LIMIT_S 判失败。
        """
        lock = self._write_lock
        if job.task_id is None:
            try:
                lock.acquire()
            except Exception as exc:  # noqa: BLE001 — 取锁的任何意外都不能让 worker 死
                return exc, False, None
            job.started = True  # 非任务作业：取到锁即开始（不经取消检查，它们没有取消入口）
            self._lock_epoch += 1
            return None, False, None
        while True:
            if job.cancel_requested:
                return None, True, None
            try:
                got = lock.acquire(timeout=LOCK_POLL_S)
            except kblock.KBLockBusy as exc:
                now = time.monotonic()
                self._begin_wait(job, now)
                holder = kblock.describe_holder(exc.holder)
                if holder != job.lock_holder:  # 占用者换了（上一个命令结束、下一个接上）也要如实更新
                    job.lock_holder = holder
                    self._notify("on_waiting", job, holder)
                if now >= job.lock_wait_deadline:
                    return exc, False, None
                retry = min(job.lock_wait_delay, max(0.0, job.lock_wait_deadline - now))
                job.lock_wait_delay = min(job.lock_wait_delay * 2, LOCK_BACKOFF_MAX_S)
                return None, False, retry
            except Exception as exc:  # noqa: BLE001
                return exc, False, None
            if got:
                self._lock_epoch += 1
                if self._mark_started(job):
                    return None, False, None
                lock.release()  # 取到锁的同一刻被取消：放回去，不执行
                return None, True, None

    def _begin_wait(self, job: Job, now: float) -> None:
        """进入（或延续）一段「等命令行释放库」：同一段内截止时刻不变；上一段之后有作业取到过写锁则重新起算。"""
        if job.lock_wait_deadline is None or job.lock_wait_epoch != self._lock_epoch:
            job.lock_wait_deadline = now + LOCK_WAIT_LIMIT_S
            job.lock_wait_delay = LOCK_BACKOFF_START_S
            job.lock_wait_epoch = self._lock_epoch
            # 新的一段等待：上一段记下的占用者已过时（期间信封可能已被 on_start 改回 queued），清掉，
            # 好让再遇到占用时（哪怕描述相同，如「执行者未知」）照样回调 on_waiting、把信封记回 waiting_lock。
            job.lock_holder = None

    def _put(self, item: tuple) -> None:
        self._queue.put((int(item[0].id), item))

    def _parked_ahead(self, job: Job) -> Job | None:
        """停放着的、比 `job` 更早提交的任务里最早的一个；没有则 None。"""
        with self._lock:
            seqs = [seq for seq in self._parked if seq < int(job.id)]
            return self._parked[min(seqs)][0] if seqs else None

    def _park(self, item: tuple, delay: float) -> None:
        """把等命令行释放库的任务作业停放起来，`delay` 秒后（与已停放的共用一个定时器）一起按序放回。

        已请求取消的不停放、直接放回（worker 取到即按取消收尾）。定时器已在走时不重设：放回时序号最小的那个先
        取锁，其余按序跟上；各自的退避间隔与截止时刻仍按自己真正去取锁的次数累计。"""
        with self._lock:
            if item[0].cancel_requested:
                self._put(item)
                return
            self._parked[int(item[0].id)] = item
            if self._park_timer is not None:
                return
            timer = threading.Timer(delay, self._release_parked)
            timer.daemon = True
            self._park_timer = timer
        try:
            timer.start()
        except RuntimeError:  # 起不了线程：不能让 _park_timer 停在一个没启动的定时器上，否则停放的任务永远放不回
            self._release_parked()

    def _release_parked(self) -> None:
        # 放回队列，信封**不**在这里改回 queued：命令行多半还占着，worker 马上又会撞上、再停放，每个退避周期
        # 来回翻「排队中 / 等待写锁」只是噪声。什么时候真不再等命令行了？——有任务取得写锁的那一刻：
        # TaskStore.on_start 把本进程其余 waiting_lock 信封改回 queued（见那里）。
        with self._lock:
            items, self._parked = list(self._parked.values()), {}
            self._park_timer = None
            for item in items:
                self._put(item)

    def _run(self) -> None:
        while True:
            _, item = self._queue.get()
            job, fn, progress = item

            # 单写者写锁（P4.5）：包住真正的写执行 `fn(emit)`，与可写 chat turn / 撤销 / GET /graph
            # 串行。在 worker 线程上阻塞 acquire 无碍（非事件循环线程）；None 时退回无锁（P4 行为）。
            # 库被 CLI 等其它进程占用（P4.30 库级锁）：非任务作业直接以 EXIT_USAGE 完成、说明谁在写；
            # 任务作业定时重新入队（P4.33 T2），不占着 worker 等。
            lock_error: Exception | None = None
            cancelled = False
            if self._write_lock is not None:
                # 有更早提交的任务正停放着等命令行释放库：这个任务也停放，不超车（依赖顺序如「先 ingest 再 heal」）。
                ahead = self._parked_ahead(job) if job.task_id is not None and not job.cancel_requested else None
                if ahead is not None:
                    # 它其实也在等同一个命令行释放库：照样记 waiting_lock（写明占用者），30 分钟的等待上限从现在算，
                    # 而不是等前面的放弃后才开始——否则排在第三个的任务要等上 90 分钟才判失败。
                    self._begin_wait(job, time.monotonic())
                    if ahead.lock_holder and ahead.lock_holder != job.lock_holder:
                        job.lock_holder = ahead.lock_holder
                        self._notify("on_waiting", job, job.lock_holder)
                    self._park(item, LOCK_BACKOFF_START_S)
                    continue
                lock_error, cancelled, retry_after = self._acquire_for(job)
                if retry_after is not None:
                    self._park(item, retry_after)
                    continue
            else:
                cancelled = not self._mark_started(job)
            job.cancelled = cancelled
            job.state = "running"  # 取得写锁（或确定不执行）之后才算 running：等锁、停放期间 /api/jobs 仍是 queued

            on_write = None
            if self._observer is not None and job.task_id is not None:
                on_write = lambda s, _job=job: self._notify("on_output", _job, s)  # noqa: E731
            buf = _ObservedIO(on_write)

            # 进度心跳（A+ 心跳，决策：瞬时进度行）：附了 progress 回调的作业（ingest）起一条守护
            # 线程，running 期每隔间隔把 `progress(elapsed)` 刷进 **瞬时** `job.progress`（pollJob
            # running 态实时渲染）。worker 收尾会清空 progress，故不污染最终 output。无回调 → 不起线程。
            hb_stop: threading.Event | None = None
            hb_thread: threading.Thread | None = None
            if progress is not None and not cancelled:  # 开始前取消的作业不执行，不起心跳
                hb_stop = threading.Event()
                hb_start = time.monotonic()

                def _beat(
                    _job: Job = job,
                    _cb: ProgressCb = progress,
                    _stop: threading.Event = hb_stop,
                    _start: float = hb_start,
                ) -> None:
                    while not _stop.wait(_JOB_HEARTBEAT_INTERVAL_S):
                        try:
                            _job.progress = _cb(time.monotonic() - _start)
                        except Exception:  # noqa: BLE001 — 进度提示绝不能拖垮作业
                            pass

                hb_thread = threading.Thread(target=_beat, name="guanlan-job-hb", daemon=True)
                hb_thread.start()

            # 增量进度 sink（决策P4.6.1-11/14）：thunk 经它把一行进度实时累加进 job.output，running
            # 期即可被轮询看见。buf 写 + 整串赋值在 job.output_lock 内（parse 的 drain 线程并发调时
            # 不撕裂、不与 finally 竞态）。既有 thunk 不调它（仍 print() 经 redirect）；仅 parse 用。
            def emit(line: str, _job: Job = job, _buf: _ObservedIO = buf) -> None:
                with _job.output_lock:
                    _buf.write(line + "\n")
                    _job.output = _buf.getvalue()

            # 任务信封翻 running 放在**取得写锁之后**（P4.33）：等锁期间信封仍是 queued——等待提示不会把一个
            # 还在排队的任务说成「正在写」，服务在等锁时退出也不会把它记成「可能已写入一部分」的 interrupted。
            # 取锁失败（库被 CLI 占用等）不翻 running，直接由收尾记 failed。
            if lock_error is None and not cancelled:
                self._notify("on_start", job)
            try:
                if cancelled:
                    buf.write(CANCELLED_BEFORE_START)
                elif lock_error is not None:
                    if isinstance(lock_error, kblock.KBLockBusy):
                        buf.write(str(lock_error))
                        job.exit_code = EXIT_USAGE
                    else:
                        buf.write(f"取写锁异常：{type(lock_error).__name__}: {lock_error}")
                        job.exit_code = EXIT_AGENT_ERROR
                else:
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
                # 先停心跳：set()+join 后再无线程写 job.progress，随后清空它才不被迟到的一拍盖回。
                # join 超时（心跳正卡在 progress 回调的 os.walk）也无碍——daemon 线程随进程亡，且最终
                # 态渲染只看 output，迟到写进 progress 的串对已 done 作业不可见（pollJob done 分支忽略）。
                if hb_stop is not None:
                    hb_stop.set()
                    if hb_thread is not None:
                        hb_thread.join(timeout=1.0)
                job.progress = ""  # 清瞬时进度：最终结果只看 output（干净摘要，不含心跳历史）
                if self._write_lock is not None and lock_error is None and not cancelled:
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
                if job.task_id is not None:
                    with self._lock:  # 已结束的不能再取消：不必再按 task_id 查到它（免得这张表随任务数只增不减）
                        self._by_task.pop(job.task_id, None)
                # 任务信封收尾（P4.33）：在 set() 之前，submit_and_wait 的等待者醒来时信封已是终态。
                self._notify("on_finish", job)
                job.done_event.set()
            # 完工回调在 write_lock 释放后调（不在临界区内做额外工作）；best-effort：异常不能
            # 让 worker 死（它已捕获作业本体异常，这里再裹一层只为隔离回调意外）。
            if self._on_job_done is not None and not cancelled:  # 开始前取消的作业没写任何东西
                with contextlib.suppress(Exception):
                    self._on_job_done(job.kind)
