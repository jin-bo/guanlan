"""Web 后台任务记录（P4.33 T1，见 docs/P4.33-Web后台任务.md §3–§7）。

`JobQueue` 仍是执行层（单写者 FIFO、写锁、redirect 捕获、心跳）；本模块只在它外面加一层**持久的任务信封**：

- 补录 / 沉淀 / heal / 审计 / 索引修复（`TASK_KINDS`）提交时先 `create()` 一份信封 `workspace/jobs/<id>.json`，再带
  `task_id` 入队；`JobQueue` 在起跑 / 每次输出 / 收尾三处回调本类（`JobObserver` 接口），信封随之翻状态、
  输出追加写进 `workspace/jobs/<id>.log`。刷新页面或重启服务后，记录与日志都还在。
- 一键入库的清单留在 `workspace/tasks/`，本类**只读**它来展示（决策 D2）：那些清单兼作 `raw/` 文件归属依据
  （`oneclick._owned_by_other_tasks`），不能混进别的 JSON，也不受本类的保留策略影响。
- 重启恢复（决策 D5）：原执行进程已退出的信封，`running` 记 `interrupted`、`queued` 记 `cancelled`。
  **不自动续跑、不自动重排**——重启不悄悄花钱。
- 保留（决策 D8）：终态信封只留最近 `MAX_HISTORY` 条，只清 `workspace/jobs/`，需要处理的不清。
- 前像与回滚（T2 第二部分，§4.3–4.5）：`guarded()` 包住任务作业——取得写锁后先拍前像、再在 `stop_scope` 里跑 Agent；
  运行中取消即整组结束子进程、按前像回滚。前像放在**库外**（`preimage_base()`），不让 Agent 在库里搜到旧页副本；
  前像清单的摘要记在内存与信封里，回滚前先核摘要、再逐文件核哈希，对不上就不动 `wiki/`、记 `rollback_failed`。
  中断任务由用户选「回滚」（作为写作业入队）或「保留现状」（删前像）。**残留进程探测（§4.6）暂缓**：回滚前确认
  没有残留的 Agent 进程是用户的事，界面上写明。

信封与日志都是派生物：放 `workspace/` 下（不进 zip、索引、图谱），删掉不影响知识库。零 LLM。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from .. import kblock, preimage, stopscope
from ..errors import EXIT_AGENT_ERROR, EXIT_USAGE
from ..oneclick import ACTIVE_STATES as IMPORT_ACTIVE_STATES
from ..oneclick import iter_tasks as iter_import_tasks
from ..rawio import atomic_write_text
from .jobs import CANCELLED_BEFORE_START, Emit, Job

TASK_KINDS = ("ingest", "backfill", "heal", "audit", "reindex", "remove")  # reindex：索引修复（P4.34 §6.2）；remove：撤回源（P4.35）
# 不拍前像的任务（P4.35 §2）：撤回的核心动作是移走 raw，前像不含 raw/，回滚必然 rollback_failed 且留下更糟的半态。
# 这类任务只能在开始前取消，运行中取消 → 409；中断后不给「回滚 / 保留现状」，按「重新预览再提交」向前收敛。
NO_PREIMAGE_KINDS = ("remove",)
REMOVE_INTERRUPTED = (
    "撤回执行中服务退出，可能已部分执行。重新预览并提交一次即可完成剩余步骤；"
    "回收区 .trash/ 里可能已有本次的 manifest（单源为 <slug>@…，整批为 <原件目录>@…）。"
)
REMOVE_NOT_STARTED = "服务在撤回开始写入前退出，没有写入任何内容；需要时可重新预览后再提交。"
PENDING_STATES = ("queued", "waiting_lock")  # 还没开始（未取得写锁）：可取消
# 持写锁在写的：运行中、取消后正在回滚、中断任务正在回滚。
WRITING_STATES = ("running", "cancelling", "rolling_back")
ACTIVE_STATES = (*PENDING_STATES, *WRITING_STATES)
ATTENTION_STATES = ("interrupted", "rollback_failed")
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "abandoned")  # 参与保留策略的终态

MAX_HISTORY = 200  # 终态信封保留条数（决策 D8）
MAX_ACTIVE = 50  # 未结束任务上限（§4.7），超出提交 429
DELETABLE_STATES = (*TERMINAL_STATES, *ATTENTION_STATES)
PREIMAGE_SLOW_S = 2.0  # 拍前像超过这个秒数在任务日志记一行耗时（§4.3）
RECENT_LIMIT = 20  # /api/tasks「最近完成」条数（决策 D7）
LOG_CHUNK_BYTES = 256 * 1024  # 详情接口单次返回的日志上限

_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z]+-[0-9a-f]{4}$")
# 本进程实例的标识：容器里服务每次都可能以同一个 pid（如 1）起来，单靠 pid 认不出「上一次的自己」留下的信封。
_BOOT_ID = secrets.token_hex(8)


class TaskLimitError(Exception):
    """未结束的任务已达上限。"""


class TaskStateError(Exception):
    """任务当前状态不允许这个操作。"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def jobs_dir(root: Path) -> Path:
    return Path(root) / "workspace" / "jobs"


def preimage_base(root: Path) -> Path:
    """本库前像的存放目录：`~/.guanlan/jobs/<库路径摘要>/`（`GUANLAN_JOBS_HOME` 可改根目录，测试用）。

    放在库外：后台任务的 Agent 以 `workspace-write` 运行，会搜索、也可能改写库里的文件；前像若在 `workspace/`
    下，旧页副本会被它搜到、链接甚至改动。agentao 的写文件工具不限目录，库外并不是「写不到」，只是不会被搜到；
    防误改另靠清单摘要（见 `TaskStore._check_digest`）。按库路径（realpath）分目录，多个库不混放。"""
    home = os.environ.get("GUANLAN_JOBS_HOME")
    base = Path(home) if home else Path.home() / ".guanlan" / "jobs"
    key = hashlib.sha256(os.path.realpath(str(root)).encode("utf-8")).hexdigest()[:16]
    return base / key


def preimage_digest(dest: Path) -> str:
    """前像清单（`manifest.json` + `raw.json` + `baseline.json`）的摘要。副本本身由 `preimage.verify` 按清单逐个核。"""
    h = hashlib.sha256()
    for name in ("manifest.json", "raw.json", "baseline.json"):
        h.update(name.encode("utf-8") + b"\0")
        h.update((Path(dest) / name).read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _owned_here(env: dict) -> bool:
    """信封是本进程（本次启动）建的。"""
    return env.get("owner_pid") == os.getpid() and env.get("owner_boot") == _BOOT_ID


def _brief_import(task: dict) -> dict:
    """一键入库清单 → 任务列表行（只读展示；操作在暂存区原卡，决策 D7）。"""
    items = task.get("items") or []
    state = task.get("state")
    return {
        "id": task.get("task_id"),
        "source": "oneclick",
        "kind": "import",
        "title": task.get("title") or task.get("sample") or task.get("task_id"),
        "params": {},
        "state": state,
        "group": "active" if state in IMPORT_ACTIVE_STATES else "recent",
        "created": task.get("created"),
        "started": task.get("confirmed"),
        "finished": task.get("finished"),
        "updated": task.get("updated") or task.get("created"),
        "exit_code": None,
        "progress": "",
        "done": sum(1 for it in items if it.get("state") == "done"),
        "total": len(items),
    }


def _incomplete_utf8_tail(data: bytes) -> int:
    """`data` 末尾残缺的 UTF-8 多字节序列的字节数（完整则 0）：日志正被并发写时读到一半不切碎字符。"""
    for back in range(1, min(4, len(data)) + 1):
        b = data[-back]
        if b & 0xC0 == 0x80:  # 续字节，继续往前找首字节
            continue
        need = 2 if b & 0xE0 == 0xC0 else 3 if b & 0xF0 == 0xE0 else 4 if b & 0xF8 == 0xF0 else 1
        return back if back < need else 0
    return 0


class TaskStore:
    """任务信封的读写与 `JobQueue` 观察者。信封在内存里另有一份缓存，列表接口不读盘。

    线程：`create` 在事件循环线程的线程池里调，`on_*` 在 worker / drain 线程调，`list_view` / `detail`
    在线程池里调——信封字典与日志句柄统一由 `self._lock` 护。
    """

    def __init__(self, root: Path, *, summarize: Callable[[Job], dict | None] | None = None) -> None:
        self._root = Path(root)
        self._dir = jobs_dir(self._root)
        self._summarize = summarize
        # 可重入：worker 跑作业时 stdout/stderr 被**进程级** redirect 到观察缓冲区，持本锁的线程若此时往
        # stderr 写（warnings 等），会经 `_ObservedIO` 回到 `on_output` 再取本锁——普通 Lock 即自死锁。
        self._lock = threading.RLock()
        self._envs: dict[str, dict] = {}
        self._live: dict[str, Job] = {}  # task_id → 本进程的在途作业（读心跳行用）
        self._logs: dict[str, IO[str]] = {}
        # 已建信封、还没确认入队的 id：入队前的层③ 复查可能把它撤掉，查重不能把别的提交并到它上面。
        self._pending: set[str] = set()
        # 运行中取消（T2）：task_id → 停止标志。建信封时建好，作业进入「收尾」时移除——此后取消一律 409，
        # 「取消」与「正常完成」在本类锁里二选一。
        self._stops: dict[str, threading.Event] = {}
        self._digests: dict[str, str] = {}  # task_id → 前像清单摘要（内存一份，信封里另存一份）
        # 作业函数已自己定下的终态（取消后回滚完成 / 回滚失败 / 中断任务的回滚结果），on_finish 照用、不按退出码改判。
        self._outcomes: dict[str, str] = {}
        self._pre_base = preimage_base(self._root)
        self._load_all()

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _file(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.json"

    def _log_file(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.log"

    def _task_dir(self, task_id: str) -> Path:
        return self._dir / task_id

    def preimage_dir(self, task_id: str) -> Path:
        return self._pre_base / task_id / "before"

    def _has_preimage(self, env: dict) -> bool:
        # 信封在库内 workspace/ 下、Agent 可写：其中的 `preimage` 只当「有前像」的标记，位置一律按 task_id 推出，
        # 不信任信封里的路径（否则改一行 JSON 就能让回滚 / 保留现状去删库外任意目录）。
        return bool(env.get("preimage")) and self.preimage_dir(env["id"]).exists()

    def _remove_locked(self, task_id: str) -> None:
        self._envs.pop(task_id, None)
        self._stops.pop(task_id, None)
        self._digests.pop(task_id, None)
        self._outcomes.pop(task_id, None)
        for path in (self._file(task_id), self._log_file(task_id)):
            with contextlib.suppress(OSError):
                path.unlink()
        shutil.rmtree(self._task_dir(task_id), ignore_errors=True)
        # 库外前像目录：信封已不认前像（`_drop_preimage` 先改信封后删目录，中间崩溃会留下它），一并清掉，免得永久残留。
        self._rm_preimage(task_id)

    def _save(self, env: dict) -> None:
        env["updated"] = _now()
        self._dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._file(env["id"]), json.dumps(env, ensure_ascii=False, indent=2) + "\n")

    def _load_all(self) -> None:
        if not self._dir.is_dir():
            return
        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # 坏文件跳过，不拖垮列表
            if isinstance(data, dict) and data.get("id") == path.stem and _ID_RE.match(path.stem):
                self._envs[path.stem] = data

    # ── 重启恢复（决策 D5）─────────────────────────────────────────────────────

    def recover(self) -> list[str]:
        """把原执行进程已退出的非终态信封改成 `interrupted` / `cancelled`。返回改动的 id。

        只认「原进程已不在」：同一个库同时开着另一个 Web 进程时，它的在途任务不受影响。pid 与本进程相同、
        但 `owner_boot` 不是本进程的，是上一次以同一 pid 起来的自己留下的（容器重启常见），照样恢复。
        （pid 被无关进程复用时会漏判，信封停在原状态——只影响显示，不影响知识库。）
        """
        changed: list[str] = []
        me = os.getpid()
        with self._lock:
            for env in self._envs.values():
                state = env.get("state")
                if state not in ACTIVE_STATES:
                    continue
                drop_pre = False
                pid = env.get("owner_pid")
                if pid == me:
                    if env.get("owner_boot") == _BOOT_ID:
                        continue
                elif kblock.holder_alive({"pid": pid}):
                    continue
                if env.get("settled"):
                    # 作业已定下结局（前像已删，与结局同一次落盘），只差 on_finish 翻状态：照结局记终态。
                    env["state"] = env.pop("settled")
                elif state == "rolling_back":
                    # 回滚本身可重复执行：回到回滚前的「需要处理」状态，让用户再点一次。
                    env["state"] = env.get("rollback_from") or "interrupted"
                    if env.get("preimage") and self.preimage_dir(env["id"]).is_dir():
                        env["error"] = "回滚过程中服务退出，回滚没有完成。回滚可以重复执行，可再次选择「回滚」。"
                    else:  # 前像已不在（~/.guanlan 被清掉等）：不能再回滚，别提示「再次回滚」
                        env["preimage"] = None
                        env["preimage_digest"] = None
                        env["error"] = "回滚过程中服务退出，回滚没有完成，且找不到这个任务的前像，不能再回滚；可以选择保留现状。"
                elif state in WRITING_STATES and env.get("kind") in NO_PREIMAGE_KINDS:
                    # 撤回不拍前像，靠 `remove_writes_began` 判断写没写（P4.35 §3.5）：它在第一次写盘之前落盘，
                    # 没有它 ⇒ 库一字未动，记 cancelled；有它 ⇒ 可能已部分执行，记 interrupted，靠重新预览收敛。
                    if env.get("remove_writes_began"):
                        env["state"] = "interrupted"
                        env["error"] = REMOVE_INTERRUPTED
                    else:
                        env["state"] = "cancelled"
                        env["error"] = REMOVE_NOT_STARTED
                elif state in WRITING_STATES:
                    pre_dir = self.preimage_dir(env["id"])
                    has_pre = bool(env.get("preimage")) and pre_dir.is_dir()
                    if has_pre and (not preimage.is_ready(pre_dir) or not env.get("preimage_digest")):
                        # 前像没拍完（目录在、没有 READY），或拍完了但摘要还没记进信封 ⇒ Agent 一定还没启动、库没被写过
                        # （§4.3 就绪标记；摘要落盘失败时 guarded 不启动 Agent）。
                        # 目录整个不见了（库被移动改了摘要键、~/.guanlan 被清掉）说明不了什么，按下面的「已中断」保守处理。
                        # 目录在信封落盘之后再删（同 `_drop_preimage` 的先后）：落盘失败就留着，下次启动照样判得出。
                        drop_pre = True
                        env["preimage"] = None
                        env["state"] = "cancelled"
                        env["error"] = "服务在任务开始写入前退出，没有写入任何内容；需要时可重试。"
                    else:
                        if not has_pre:
                            # 前像找不到：信封不再认前像，否则「重试」会以「还有前像」拒绝，而界面（has_preimage）已显示可重试。
                            env["preimage"] = None
                            env["preimage_digest"] = None
                        env["state"] = "interrupted"
                        env["error"] = (
                            "执行中断：服务在任务运行期间退出，任务可能已写入一部分内容。"
                            + (
                                "可以回滚到任务开始前，或保留现状。"
                                if has_pre
                                else "找不到这个任务的前像，不能回滚，请检查后决定是否重试。"
                            )
                        )
                else:
                    env["state"] = "cancelled"
                    env["error"] = (
                        REMOVE_NOT_STARTED  # 撤回没有「重试」（P4.35 §3.5）：引导重新预览
                        if env.get("kind") in NO_PREIMAGE_KINDS
                        else "服务重启时任务尚未开始，已取消；需要时可重试。"
                    )
                env["holder"] = None  # 等锁中被恢复的：占用者描述已过时
                env["finished"] = env.get("finished") or _now()
                try:
                    self._save(env)
                except OSError:
                    drop_pre = False
                if drop_pre:
                    self._rm_preimage(env["id"])
                changed.append(env["id"])
        self._sweep_orphan_preimages()
        return changed

    def _sweep_orphan_preimages(self) -> None:
        """删掉没有任务记录的库外前像目录（`workspace/jobs/` 被整个删掉、信封文件丢失等）：否则整库副本永久残留。

        按磁盘上的信封文件判，不按内存：信封总在拍前像之前落盘，前像目录在、信封文件不在，就一定是孤儿——
        别的 Web 进程在本进程载入之后新建的任务也不会被误删。"""
        if not self._dir.is_dir():
            # 信封目录整个不在（被删、所在卷没挂上）：判断不了谁是孤儿，宁可不删。下次建过任务、目录回来后再扫。
            return
        try:
            children = list(self._pre_base.iterdir())
        except OSError:
            return
        for child in children:
            if _ID_RE.match(child.name) and child.is_dir() and not self._file(child.name).exists():
                shutil.rmtree(child, ignore_errors=True)

    # ── 创建 ──────────────────────────────────────────────────────────────────

    def create(self, kind: str, title: str, params: dict) -> str:
        """总是新建一份信封并立即视为已入队：不查重、不受上限（不经 Web 提交路径的调用方与测试用）。"""
        task_id = self.create_or_reuse(kind, title, params, reuse=False)[0]
        self.committed(task_id)
        return task_id

    def create_or_reuse(
        self,
        kind: str,
        title: str,
        params: dict,
        *,
        retry_of: str | None = None,
        reuse: bool = True,
        extra: dict | None = None,
    ) -> tuple[str, bool]:
        """建信封，返回 `(task_id, created)`。查重与上限和建信封在同一把锁里，并发提交不会各建一份。

        - 同 kind + 同 params 的任务还没结束（排队 / 等锁 / 运行中）→ 不新建，返回 `(已有 id, False)`。
        - 未结束任务已有 `MAX_ACTIVE` 个 → `TaskLimitError`。
        """
        if kind not in TASK_KINDS:
            raise ValueError(f"未知任务类型：{kind}")
        with self._lock:
            # 只算本进程建的：别的进程（或 pid 被复用后认不出的旧进程）留下的未结束信封不在本进程的队列里，
            # 拿它查重会让新提交被并到一个永远不会跑的任务上，拿它算上限会把配额白白占掉。
            active = [
                e
                for e in self._envs.values()
                if e.get("state") in ACTIVE_STATES and _owned_here(e)
            ]
            settled = [e for e in active if e["id"] not in self._pending]
            for env in settled if reuse else ():
                # 正在取消 / 回滚的任务不会产出结果：同参数的新提交不并过去。
                if env.get("state") in ("cancelling", "rolling_back"):
                    continue
                if env.get("kind") == kind and env.get("params") == params:
                    return env["id"], False
            if reuse and len(active) >= MAX_ACTIVE:
                raise TaskLimitError(f"未结束的后台任务已有 {len(active)} 个（上限 {MAX_ACTIVE}），请等一些完成后再提交。")
            while True:
                task_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{secrets.token_hex(2)}"
                if task_id not in self._envs and not self._file(task_id).exists():
                    break
            env = {
                **(extra or {}),  # 不参与查重的附加字段（撤回的原件身份等，P4.35）；放最前，不能盖掉下面的核心字段
                "id": task_id,
                "kind": kind,
                "title": title,
                "params": params,
                "state": "queued",
                "created": _now(),
                "started": None,
                "finished": None,
                "exit_code": None,
                "summary": None,
                "error": None,
                "holder": None,
                "retry_of": retry_of,
                "owner_pid": os.getpid(),
                "owner_boot": _BOOT_ID,
            }
            self._save(env)  # 先落盘再入队：入队后的回调一定找得到信封
            self._envs[task_id] = env
            self._pending.add(task_id)
            self._stops[task_id] = threading.Event()
        return task_id, True

    def committed(self, task_id: str) -> None:
        """信封对应的作业已入队：此后才参与查重。"""
        with self._lock:
            self._pending.discard(task_id)

    def set_fields(self, task_id: str, **fields: object) -> bool:
        """改信封字段并落盘（作业内记录进展用，如撤回的 `remove_writes_began`）。返回是否已落盘（信封不存在也算没落盘）。

        落盘失败时把内存里的字段退回原样：否则内存信封显示「已开始写」，而库其实一字未动（P4.35 §3.5）。"""
        with self._lock:
            env = self._envs.get(task_id)
            if env is None:
                return False  # 没有信封：什么也没记下
            missing = object()
            old = {k: env.get(k, missing) for k in fields}
            env.update(fields)
            try:
                self._save(env)
            except OSError:
                for k, v in old.items():
                    if v is missing:
                        env.pop(k, None)
                    else:
                        env[k] = v
                return False
            return True

    def envelopes(self, kind: str) -> list[dict]:
        """某类任务的全部信封（副本）。"""
        with self._lock:
            return [dict(e) for e in self._envs.values() if e.get("kind") == kind]

    def envelope(self, task_id: str) -> dict | None:
        with self._lock:
            env = self._envs.get(task_id)
            return dict(env) if env is not None else None

    def cancel(self, task_id: str, cancel_job: Callable[[str], bool]) -> bool:
        """取消一个还没开始的任务：在本类的锁里先让执行层撤下作业（`cancel_job`，即 `JobQueue.cancel_task`），
        成功就立即把信封记 cancelled。两步在同一把锁里，期间到达的同参数提交不会被查重并到这个将被取消的任务上。
        锁序：本类锁 → JobQueue 表锁（JobQueue 持表锁时从不回调本类）。"""
        with self._lock:
            env = self._envs.get(task_id)
            if env is not None and env.get("state") == "rolling_back":
                # 中断任务的回滚还没开始（排队 / 等命令行释放库）：撤下回滚作业，退回原来的「需要处理」状态，库未改动。
                if not cancel_job(task_id):
                    return False
                env["state"] = env.get("rollback_from") or "interrupted"
                env["holder"] = None
                env["error"] = "已取消回滚，库没有改动。可以再次回滚，或保留现状。"
                with contextlib.suppress(OSError):
                    self._save(env)
                return True
            if env is None or env.get("state") not in PENDING_STATES:
                return False
            if not cancel_job(task_id):
                return False
            # 作业已撤下：信封落盘失败也算取消成功（内存里已是 cancelled，worker 收尾时 on_finish 再存一次）。
            with contextlib.suppress(OSError):
                self.mark_cancelled(task_id)
            return True

    def mark_cancelled(self, task_id: str) -> None:
        """排队 / 等锁中的任务被取消：信封立即记 cancelled（worker 之后取到它时只是跳过）。"""
        with self._lock:
            env = self._envs.get(task_id)
            if env is None or env.get("state") not in PENDING_STATES:
                return
            env["state"] = "cancelled"
            env["holder"] = None
            env["finished"] = _now()
            env["error"] = CANCELLED_BEFORE_START
            self._save(env)
            self._prune_locked()

    def delete(self, task_id: str) -> None:
        """删一条已结束任务的记录：信封、日志、任务目录。有前像（T2 回滚所需）→ `TaskStateError`。"""
        with self._lock:
            env = self._envs.get(task_id)
            if env is None:
                return
            if env.get("state") not in DELETABLE_STATES:
                raise TaskStateError(f"任务当前状态为 {env.get('state')}，不能删除记录。")
            if self._has_preimage(env):
                raise TaskStateError("任务还有未处理的前像，先选择「回滚」或「保留现状」。")
            self._remove_locked(task_id)

    def discard(self, task_id: str) -> None:
        """入队失败时撤掉刚建的信封（只用于提交路径的异常回滚）。"""
        with self._lock:
            self._envs.pop(task_id, None)
            self._pending.discard(task_id)
            self._stops.pop(task_id, None)
            with contextlib.suppress(OSError):
                self._file(task_id).unlink()

    # ── 前像、运行中取消、回滚（T2 第二部分，§4.3–4.5）─────────────────────────────

    def guarded(self, task_id: str, inner: Callable[[Emit], object]) -> Callable[[Emit], object]:
        """包住任务作业：worker 取得写锁后调用。拍前像 → `stop_scope` 里跑 `inner` → 按是否被取消回滚或删前像。

        - 拍前像失败：不启动 Agent，任务记 failed（库没被动过）。
        - 运行中取消：`stop_scope` 让 Agent 子进程在 0.2 秒内整组结束、抛 `StopRequested`；确定性阶段（门禁、修复）
          跑完当前一步，下一次起子进程时停。`inner` 已正常返回、但收尾前到达的取消同样回滚——**任务完成前发出的取消，
          结果都是回滚**。回滚完成记 cancelled，失败记 rollback_failed（前像保留）。
        - 正常结束（成功或失败）：删前像。
        """

        def run(emit: Emit) -> object:
            dest = self.preimage_dir(task_id)
            ev = self._stops.get(task_id) or threading.Event()
            if ev.is_set():
                return self._stopped_before_agent(task_id)
            try:
                pre = preimage.take(self._root, dest)
                digest = preimage_digest(dest)
            except Exception as exc:  # noqa: BLE001 — 任何拍前像异常都不能让残缺前像留在信封上（任务会记 failed 却删不掉）
                with self._lock:  # 关闭取消窗口；拍前像期间已到达的取消（信封已是 cancelling）照取消收尾，不改判 failed
                    self._stops.pop(task_id, None)
                    cancelled = ev.is_set()
                    if cancelled:
                        self._outcomes[task_id] = "cancelled"
                detail = str(exc) if isinstance(exc, (OSError, preimage.PreimageError)) else f"{type(exc).__name__}: {exc}"
                if cancelled:  # 详情里写明：已取消，且前像本来就没拍成。终态同一次落盘（同 `_stopped_before_agent`）
                    self._drop_preimage(task_id, settled="cancelled", error=f"已取消：Agent 还没启动，没有写入任何内容（拍前像也失败了：{detail}）。"
                    )
                else:  # 终态同一次落盘：否则落盘与收尾之间服务退出，重启会把没写过库的任务记成「已中断」
                    self._drop_preimage(task_id, settled="failed", exit_code=EXIT_USAGE)
                print(f"拍前像失败，任务没有开始，库未被改动：{detail}")
                return EXIT_USAGE
            with self._lock:
                self._digests[task_id] = digest
            if not self._set_fields(task_id, preimage=str(dest), preimage_digest=digest):
                # 摘要没落盘就不启动 Agent：重启恢复据此认定「有 READY、没有摘要」的任务一定没写过库。
                with self._lock:
                    self._stops.pop(task_id, None)
                self._drop_preimage(task_id, settled="failed", exit_code=EXIT_USAGE)
                print("任务记录写不进（workspace/ 不可写？），任务没有开始，库未被改动。")
                return EXIT_USAGE
            if pre.seconds > PREIMAGE_SLOW_S:
                print(f"（拍前像用时 {pre.seconds:.1f} 秒，{len(pre.files)} 个文件）")
            if ev.is_set():  # 拍前像期间到达的取消：Agent 还没启动
                return self._stopped_before_agent(task_id)
            stopped = False
            outcome: object = None
            try:
                with stopscope.stop_scope(ev):
                    outcome = inner(emit)
            except stopscope.StopRequested:
                stopped = True
            except Exception as exc:
                with self._lock:  # 与 request_stop 二选一：判定与关闭取消窗口在同一把锁里
                    stopped = ev.is_set()
                    self._stops.pop(task_id, None)
                if not stopped:
                    # 终态同一次落盘（同正常结束）：否则落盘与收尾之间服务退出，重启会把失败的任务记成「已中断」。
                    self._drop_preimage(task_id, settled="failed", exit_code=EXIT_AGENT_ERROR)
                    raise
                # 取消之后才出的异常（如子进程被结束引发的错误）：照样按取消回滚，异常留一行在日志里
                print(f"\n（取消后作业抛出异常：{type(exc).__name__}: {exc}）")
            except BaseException:
                with self._lock:
                    self._stops.pop(task_id, None)
                self._drop_preimage(task_id)
                raise
            with self._lock:  # 关闭取消窗口：此后 request_stop 一律失败
                stopped = stopped or ev.is_set()
                self._stops.pop(task_id, None)
            if not stopped:
                # 终态与「前像已删」同一次落盘：否则两次落盘之间服务退出，重启会把已结束的任务记成「已中断、找不到前像」。
                code = outcome if isinstance(outcome, int) else getattr(outcome, "exit_code", None)
                self._drop_preimage(task_id, settled="succeeded" if code == 0 else "failed", exit_code=code)
                return outcome
            print("\n已取消：Agent 已停止，正在把本任务的写入回滚到任务开始前…")
            self._rollback_and_record(task_id, reason="cancel")
            # 信封终态由 `_outcomes` 定；作业退出码一律非 0：`/api/jobs/{id}` 不能把被取消的任务报成成功。
            return EXIT_USAGE

        return run

    def has_preimage(self, task_id: str) -> bool:
        """任务还有可用的前像（与列表里的 `has_preimage` 同一判据）。"""
        with self._lock:
            env = self._envs.get(task_id)
            return env is not None and self._has_preimage(env)

    def _set_fields(self, task_id: str, **fields: object) -> bool:
        """改信封字段并落盘。返回是否已落盘（信封已不在也算 True：没有需要保持一致的记录）。"""
        with self._lock:
            env = self._envs.get(task_id)
            if env is None:
                return True
            env.update(fields)
            try:
                self._save(env)
            except OSError:
                return False
            return True

    def _rm_preimage(self, task_id: str) -> None:
        """删本任务在库外的整个前像目录（`<base>/<id>/`）。不存在不报错。"""
        shutil.rmtree(self._pre_base / task_id, ignore_errors=True)

    def _drop_preimage(self, task_id: str, **final: object) -> None:
        # 先改信封、再删目录：反过来的话，删掉 READY 后、信封落盘前服务退出，重启恢复会把一个已经写过库的任务
        # 误判成「前像没拍完、没有写入」。信封没落盘就不删目录（同理）：内存里已不认前像，目录留到删记录 / 清理时一并删。
        # `final`：与「不再有前像」同一次落盘的结局字段——`settled`（作业已定下的终态）及 error / rollback / exit_code 等。
        # 否则两次落盘之间服务退出，重启会看到「运行中、没有前像」，把一个已经回滚干净（或根本没写）的任务记成「已中断、
        # 可能写了一部分」。**对外状态不在这里改**：作业还持写锁在收尾，`state` 留到 on_finish 才翻终态；重启恢复读 `settled`。
        with self._lock:
            self._digests.pop(task_id, None)
        if self._set_fields(task_id, preimage=None, preimage_digest=None, **final):
            self._rm_preimage(task_id)

    def _stopped_before_agent(self, task_id: str) -> int:
        msg = "已取消：Agent 还没启动，没有写入任何内容。"
        with self._lock:
            self._stops.pop(task_id, None)
            self._outcomes[task_id] = "cancelled"
        self._drop_preimage(task_id, settled="cancelled", error=msg)
        print(msg)
        return EXIT_USAGE  # 同取消回滚：`/api/jobs/{id}` 不报成功

    def request_stop(self, task_id: str) -> bool:
        """运行中取消：记 cancelling、置停止标志。不在运行（或已进入收尾）→ False。"""
        with self._lock:
            env = self._envs.get(task_id)
            ev = self._stops.get(task_id)
            if env is None or env.get("state") != "running" or ev is None or not _owned_here(env):
                return False
            if env.get("kind") in NO_PREIMAGE_KINDS:  # 没有前像可回滚：运行中不可取消
                return False
            env["state"] = "cancelling"
            with contextlib.suppress(OSError):
                self._save(env)
            ev.set()
            return True

    def _check_digest(self, task_id: str, env: dict, dest: Path) -> None:
        """前像清单与任务开始时记下的摘要一致；不一致（被改过）→ `PreimageError`。内存里有就以内存为准。"""
        want = self._digests.get(task_id) or env.get("preimage_digest")
        if not want:
            raise preimage.PreimageError("没有记录前像清单摘要，无法确认前像未被改动，拒绝回滚")
        try:
            got = preimage_digest(dest)
        except OSError as exc:
            raise preimage.PreimageError(f"前像清单读不出：{exc}") from None
        if got != want:
            raise preimage.PreimageError("前像清单与任务开始时记录的不一致（可能被改动过），拒绝回滚")

    def _rollback_and_record(self, task_id: str, *, reason: str) -> bool:
        """在持写锁的作业里执行回滚，回执写进信封、终态记进 `_outcomes`。返回是否成功。"""
        with self._lock:
            env = dict(self._envs.get(task_id) or {})
        dest = self.preimage_dir(task_id)
        # 信封在库内、Agent 可写：沿用记下的 trash 路径前先核形状，不合规（被改成库外 / `..` 路径）就新取一个。
        trash = env.get("rollback_trash")
        if not (isinstance(trash, str) and re.fullmatch(rf"\.trash/job-{re.escape(task_id)}@\d{{8}}T\d{{6}}", trash)):
            trash = f".trash/job-{task_id}@{time.strftime('%Y%m%dT%H%M%S')}"
        self._set_fields(task_id, rollback_trash=trash)  # 先记下：重跑时并入同一个 trash 目录
        receipt: dict
        try:
            self._check_digest(task_id, env, dest)
            pre = preimage.load(dest)
            result = preimage.rollback(self._root, pre, trash, meta={"task_id": task_id, "reason": reason})
            receipt = result.to_dict()
            ok = result.ok
        except Exception as exc:  # noqa: BLE001 — 任何意外都要落成 rollback_failed，否则取消路径的信封会停在无法处理的 failed + 前像
            receipt = {"error": str(exc) if isinstance(exc, (OSError, preimage.PreimageError)) else f"{type(exc).__name__}: {exc}"}
            ok = False
        if ok:
            error = "已取消，本任务的写入已回滚到任务开始前。" if reason == "cancel" else "已回滚到任务开始前。"
            self._drop_preimage(task_id, settled="cancelled", rollback=receipt, error=error)
            print(f"✓ {error}")
        else:
            why = receipt.get("error") or (
                "raw/ 被改动过" if not receipt.get("raw_ok", True) else "回滚后 check 出现新的阻断性问题"
            )
            error = f"回滚失败：{why}。前像已保留，可以再试回滚，或选择保留现状。"
            print(f"✗ {error}")
        with self._lock:
            self._outcomes[task_id] = "cancelled" if ok else "rollback_failed"
            env2 = self._envs.get(task_id)
            if env2 is not None:
                env2["rollback"] = receipt
                env2["error"] = error
        return ok

    def start_rollback(self, task_id: str, enqueue: Callable[[str, Callable[[Emit], object]], object]) -> None:
        """中断 / 回滚失败的任务：记 rolling_back，把回滚作为写作业入队（`enqueue(task_id, fn)`）。

        **调用方须已提示用户确认没有残留的 Agent 进程**（§4.6 的自动探测暂缓）。"""
        with self._lock:
            env = self._envs.get(task_id)
            if env is None:
                raise TaskStateError("未知任务")
            if env.get("kind") in NO_PREIMAGE_KINDS:
                raise TaskStateError("撤回任务没有前像，不能回滚；重新预览并提交一次即可完成剩余步骤。")
            if env.get("state") not in ATTENTION_STATES:
                raise TaskStateError(f"任务当前状态为 {env.get('state')}，不能回滚。")
            if not self._has_preimage(env) or not preimage.is_ready(self.preimage_dir(task_id)):
                raise TaskStateError("这个任务没有可用的前像，不能回滚；可以选择保留现状。")
            keys = ("state", "error", "rollback_from", "owner_pid", "owner_boot")
            before = {k: env.get(k) for k in keys}
            prev = env["state"]
            env.update(
                state="rolling_back",
                rollback_from=prev,
                owner_pid=os.getpid(),
                owner_boot=_BOOT_ID,
                error=None,
            )
            try:
                self._save(env)
            except OSError:
                # 没落盘也没入队：内存里不能停在 rolling_back（没有作业会来收尾，任务就卡住了）。
                env.update(before)
                raise

            def fn(emit: Emit) -> int:
                print("正在回滚到任务开始前…")
                return 0 if self._rollback_and_record(task_id, reason="interrupted") else EXIT_USAGE

            try:
                enqueue(task_id, fn)
            except BaseException:
                env.update(before)
                with contextlib.suppress(OSError):
                    self._save(env)
                raise

    def abandon(self, task_id: str) -> None:
        """保留现状：删前像，任务记 abandoned。只对中断 / 回滚失败的任务。"""
        with self._lock:
            env = self._envs.get(task_id)
            if env is None:
                raise TaskStateError("未知任务")
            if env.get("kind") in NO_PREIMAGE_KINDS:
                raise TaskStateError("撤回任务没有「保留现状」：重新预览并提交一次即可完成剩余步骤，或删除这条记录。")
            if env.get("state") not in ATTENTION_STATES:
                raise TaskStateError(f"任务当前状态为 {env.get('state')}，不能选择保留现状。")
            keys = ("state", "error", "preimage", "preimage_digest", "finished")
            before = {k: env.get(k) for k in keys}
            env.update(state="abandoned", error="已保留现状，前像已删除。", preimage=None, preimage_digest=None)
            env["finished"] = _now()  # 处理时刻：刚处理的这条排在最近，不被保留策略当场清掉
            try:
                self._save(env)  # 先改信封、再删目录（同 `_drop_preimage`）
            except OSError:
                env.update(before)  # 没落盘：内存也退回原状态，前像照旧可用
                raise
            self._digests.pop(task_id, None)
            self._prune_locked()
        # 前像可能有上千个文件：删目录放在本类锁外，不让 worker 的日志回调与任务列表跟着卡住。
        self._rm_preimage(task_id)

    # ── JobObserver ───────────────────────────────────────────────────────────

    def on_start(self, job: Job) -> None:
        with self._lock:
            env = self._envs.get(job.task_id or "")
            if env is None:
                return
            self._live[env["id"]] = job
            if env.get("state") != "rolling_back":  # 中断任务的回滚作业：状态在 start_rollback 已记
                env["state"] = "running"
                env["started"] = _now()
                # 先记下前像位置、再拍：服务若在拍完之前退出，重启时据此看出「没有 READY ⇒ Agent 没启动」。
                if env.get("kind") not in NO_PREIMAGE_KINDS:  # 撤回不拍前像（P4.35 §2）
                    env["preimage"] = str(self.preimage_dir(env["id"]))
            env["holder"] = None
            self._save(env)
            # 有任务取得了写锁 ⇒ 库已不被命令行占着：本进程其余「等待写锁」的任务其实只是排在它后面，
            # 改回 queued、清掉已经不在的占用者（再撞上命令行时会重新报）。
            for other in self._envs.values():
                if not _owned_here(other):
                    continue
                if other.get("state") == "waiting_lock":
                    other["state"] = "queued"
                    other["holder"] = None
                    self._save(other)
                elif other.get("state") == "rolling_back" and other.get("holder") and other is not env:
                    other["holder"] = None  # 停放着的回滚作业：占用者描述同样已过时
                    self._save(other)

    def on_waiting(self, job: Job, holder: str) -> None:
        """库被命令行等外部写者占用：worker 退避重试期间信封记 waiting_lock，并写明是谁在写。"""
        with self._lock:
            env = self._envs.get(job.task_id or "")
            if env is not None and env.get("state") == "rolling_back":  # 回滚作业在等：状态不变，只写明谁占着库
                env["holder"] = holder
                self._save(env)
                return
            if env is None or env.get("state") not in PENDING_STATES:
                return
            env["state"] = "waiting_lock"
            env["holder"] = holder
            self._save(env)

    def on_output(self, job: Job, text: str) -> None:
        with self._lock:
            task_id = job.task_id or ""
            if task_id not in self._envs:
                return
            fh = self._logs.get(task_id)
            if fh is None:
                self._dir.mkdir(parents=True, exist_ok=True)
                fh = self._log_file(task_id).open("a", encoding="utf-8", errors="replace")
                self._logs[task_id] = fh
            fh.write(text)
            fh.flush()

    def on_finish(self, job: Job) -> None:
        summary = None
        if self._summarize is not None:
            with contextlib.suppress(Exception):  # 回执序列化失败不影响终态
                summary = self._summarize(job)
        with self._lock:
            task_id = job.task_id or ""
            fh = self._logs.pop(task_id, None)
            if fh is not None:
                with contextlib.suppress(OSError):
                    fh.close()
            self._live.pop(task_id, None)
            self._stops.pop(task_id, None)
            outcome = self._outcomes.pop(task_id, None)
            env = self._envs.get(task_id)
            if env is None:
                return
            env.pop("settled", None)  # 收尾：结局由下面翻成正式状态（各分支都会落盘）
            if outcome is not None:
                # 作业函数已定下终态（取消后回滚 / 中断任务的回滚）：照用，不按退出码改判。
                env["state"] = outcome
                env["holder"] = None
                env["finished"] = _now()
                if env.get("summary") is None and job.kind != "rollback":
                    env["summary"] = summary
                self._save(env)
                self._prune_locked()
                return
            if job.kind == "rollback" and job.cancelled:
                # 开始前被取消的回滚作业：cancel() 已把信封退回原状态。此后用户可能已再次回滚（信封又是 rolling_back、
                # 新作业在排队），这里不能把它改判成「回滚没有执行」，也不动占用者。
                return
            if env.get("state") == "rolling_back":
                # 回滚作业没跑起来（如取不到写锁：命令行占着库）：退回原来的「需要处理」状态，可以再试。
                env["state"] = env.get("rollback_from") or "interrupted"
                tail = (job.output or "").strip().splitlines()
                env["error"] = "回滚没有执行：" + (tail[-1] if tail else "未知原因") + "。可以稍后再试。"
                env["holder"] = None
                self._save(env)
                return
            if job.cancelled or env.get("state") == "cancelled":
                # 取消在开始前生效：mark_cancelled 已记过终态；worker 跳过它时只补齐字段，不改判成失败。
                if env.get("state") != "cancelled":
                    env["state"] = "cancelled"
                    env["error"] = CANCELLED_BEFORE_START
                env["holder"] = None
                env["finished"] = env.get("finished") or _now()
                self._save(env)
                self._prune_locked()
                return
            env["state"] = "succeeded" if job.exit_code == 0 else "failed"
            env["exit_code"] = job.exit_code
            env["holder"] = None
            env["finished"] = _now()
            env["summary"] = summary
            self._save(env)
            self._prune_locked()

    # ── 保留（决策 D8）─────────────────────────────────────────────────────────

    def _prune_locked(self) -> None:
        # 已由后来的撤回完成的中断撤回（P4.35，`superseded_by`）不再需要处理：与终态一样参与保留策略。
        done = [
            e
            for e in self._envs.values()
            if e.get("state") in TERMINAL_STATES or (e.get("state") in ATTENTION_STATES and e.get("superseded_by"))
        ]
        if len(done) <= MAX_HISTORY:
            return
        done.sort(key=lambda e: (e.get("finished") or "", e["id"]))
        for env in done[: len(done) - MAX_HISTORY]:
            if self._has_preimage(env):
                continue  # 还有前像：要等用户处理，不自动清
            if (
                env.get("kind") in NO_PREIMAGE_KINDS
                and env.get("state") == "failed"
                and (env.get("params") or {}).get("scope") == "all"
                and env.get("remove_writes_began")
                and not env.get("superseded_by")
            ):
                # 写了一半的整批撤回（P4.35 §3.5）：续跑要用信封里的原件身份，不自动清，由用户删除。
                # 成功的、单源的撤回照常按保留策略清（§3.5：资格靠旧记录自身标记，不靠成功记录）。
                continue
            self._remove_locked(env["id"])

    # ── 读 ────────────────────────────────────────────────────────────────────

    def _brief_locked(self, env: dict) -> dict:
        state = env.get("state")
        live = self._live.get(env["id"])
        if state in ATTENTION_STATES and not env.get("superseded_by"):
            group = "attention"
        elif state in ATTENTION_STATES:  # 中断的撤回已由后来的任务完成（P4.35）：不再需要处理
            group = "recent"
        elif state in ACTIVE_STATES:
            group = "active"
        else:
            group = "recent"
        return {
            "id": env["id"],
            "source": "jobs",
            "kind": env.get("kind"),
            "title": env.get("title"),
            "params": env.get("params") or {},
            "state": state,
            "group": group,
            "created": env.get("created"),
            "started": env.get("started"),
            "finished": env.get("finished"),
            "updated": env.get("updated"),
            "exit_code": env.get("exit_code"),
            "progress": live.progress if live is not None and state == "running" else "",
            "error": env.get("error"),
            "holder": env.get("holder"),
            "retry_of": env.get("retry_of"),
            "rollback_from": env.get("rollback_from"),
            # 回滚作业已入队、还没取得写锁开始执行（排在别的写任务后面，或等命令行释放库）
            "rollback_queued": state == "rolling_back" and env["id"] not in self._live,
            "has_preimage": self._has_preimage(env),
            "rollback": env.get("rollback"),
            # 撤回（P4.35）：任务中心据此决定「重新预览」走哪个入口、是否还能续跑
            "entry": env.get("entry"),
            "remove_writes_began": env.get("remove_writes_began"),
            "superseded_by": env.get("superseded_by"),
        }

    def running_brief(self) -> dict | None:
        """此刻在跑的任务（给可写 turn 的等待提示用）；没有则 None。"""
        with self._lock:
            for task_id in self._live:
                env = self._envs.get(task_id)
                if env is not None and env.get("state") in WRITING_STATES:
                    return {"task_id": task_id, "kind": env.get("kind"), "title": env.get("title"), "params": env.get("params") or {}}
        return None

    def _import_briefs(self) -> list[dict]:
        try:
            return [_brief_import(t) for t in iter_import_tasks(self._root)]
        except OSError:
            return []

    def list_view(self) -> dict:
        """全部「需要处理」+ 全部活跃 + 最近完成 `RECENT_LIMIT` 条（决策 D7），含一键入库。"""
        with self._lock:
            rows = [self._brief_locked(e) for e in self._envs.values()]
        rows += self._import_briefs()
        attention = [r for r in rows if r["group"] == "attention"]
        active = [r for r in rows if r["group"] == "active"]
        recent = [r for r in rows if r["group"] == "recent"]
        attention.sort(key=lambda r: r.get("finished") or "", reverse=True)
        active.sort(key=lambda r: (r.get("created") or "", r["id"]))
        recent.sort(key=lambda r: (r.get("finished") or r.get("updated") or "", r["id"]), reverse=True)
        return {"attention": attention, "active": active, "recent": recent[:RECENT_LIMIT]}

    def detail(self, task_id: str, log_after: int = 0) -> dict | None:
        """单个任务详情：信封 + 回执 + 日志增量（从字节偏移 `log_after` 起，至多 `LOG_CHUNK_BYTES`）。

        一键入库任务只给列表行（详情在暂存区原卡）；未知 id → None。
        """
        if _ID_RE.match(task_id):
            with self._lock:
                env = self._envs.get(task_id)
                out = None
                if env is not None:
                    out = self._brief_locked(env)
                    out["summary"] = env.get("summary")
            if out is not None:
                out["log"] = self._read_log(task_id, max(0, log_after))
                return out
        # 一键入库 id（`<时间>-<名字>[-n]`）可能恰好也长得像信封 id（如名字以 4 位十六进制结尾），故不在上面短路。
        for row in self._import_briefs():
            if row["id"] == task_id:
                return row
        return None

    def _read_log(self, task_id: str, offset: int) -> dict:
        path = self._log_file(task_id)
        try:
            with path.open("rb") as fh:
                size = fh.seek(0, os.SEEK_END)
                start = min(offset, size)
                if size - start > LOG_CHUNK_BYTES:  # 落后太多：只给末尾一段，标出被跳过
                    start = size - LOG_CHUNK_BYTES
                    fh.seek(start)
                    head = fh.read(3)  # 截断点可能落在多字节字符中间：跳过续字节（0b10xxxxxx）
                    start += next((i for i, b in enumerate(head) if b & 0xC0 != 0x80), len(head))
                fh.seek(start)
                data = fh.read(size - start)
            data = data[: len(data) - _incomplete_utf8_tail(data)]  # 末尾半个字符留到下一次
        except OSError:
            return {"offset": offset, "next": offset, "text": "", "skipped": False}
        return {
            "offset": start,
            "next": start + len(data),
            "text": data.decode("utf-8", errors="replace"),
            "skipped": start > offset,
        }
