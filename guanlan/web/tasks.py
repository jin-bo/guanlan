"""Web 后台任务记录（P4.33 T1，见 docs/P4.33-Web后台任务.md §3–§7）。

`JobQueue` 仍是执行层（单写者 FIFO、写锁、redirect 捕获、心跳）；本模块只在它外面加一层**持久的任务信封**：

- 补录 / 沉淀 / heal / 审计（`TASK_KINDS`）提交时先 `create()` 一份信封 `workspace/jobs/<id>.json`，再带
  `task_id` 入队；`JobQueue` 在起跑 / 每次输出 / 收尾三处回调本类（`JobObserver` 接口），信封随之翻状态、
  输出追加写进 `workspace/jobs/<id>.log`。刷新页面或重启服务后，记录与日志都还在。
- 一键入库的清单留在 `workspace/tasks/`，本类**只读**它来展示（决策 D2）：那些清单兼作 `raw/` 文件归属依据
  （`oneclick._owned_by_other_tasks`），不能混进别的 JSON，也不受本类的保留策略影响。
- 重启恢复（决策 D5）：原执行进程已退出的信封，`running` 记 `interrupted`、`queued` 记 `cancelled`。
  **不自动续跑、不自动重排**——重启不悄悄花钱。
- 保留（决策 D8）：终态信封只留最近 `MAX_HISTORY` 条，只清 `workspace/jobs/`，需要处理的不清。

信封与日志都是派生物：放 `workspace/` 下（不进 zip、索引、图谱），删掉不影响知识库。零 LLM。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from .. import kblock
from ..oneclick import ACTIVE_STATES as IMPORT_ACTIVE_STATES
from ..oneclick import iter_tasks as iter_import_tasks
from ..rawio import atomic_write_text
from .jobs import Job

TASK_KINDS = ("ingest", "backfill", "heal", "audit")
ACTIVE_STATES = ("queued", "running")
ATTENTION_STATES = ("interrupted",)  # T2 加 rollback_failed
TERMINAL_STATES = ("succeeded", "failed", "cancelled")  # 参与保留策略的终态

MAX_HISTORY = 200  # 终态信封保留条数（决策 D8）
RECENT_LIMIT = 20  # /api/tasks「最近完成」条数（决策 D7）
LOG_CHUNK_BYTES = 256 * 1024  # 详情接口单次返回的日志上限

_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z]+-[0-9a-f]{4}$")
# 本进程实例的标识：容器里服务每次都可能以同一个 pid（如 1）起来，单靠 pid 认不出「上一次的自己」留下的信封。
_BOOT_ID = secrets.token_hex(8)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def jobs_dir(root: Path) -> Path:
    return Path(root) / "workspace" / "jobs"


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
        self._load_all()

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _file(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.json"

    def _log_file(self, task_id: str) -> Path:
        return self._dir / f"{task_id}.log"

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
                pid = env.get("owner_pid")
                if pid == me:
                    if env.get("owner_boot") == _BOOT_ID:
                        continue
                elif kblock.holder_alive({"pid": pid}):
                    continue
                if state == "running":
                    env["state"] = "interrupted"
                    env["error"] = "执行中断：服务在任务运行期间退出。任务可能已写入一部分内容，请检查后决定是否重试。"
                else:
                    env["state"] = "cancelled"
                    env["error"] = "服务重启时任务尚未开始，已取消；需要时可重试。"
                env["finished"] = env.get("finished") or _now()
                with contextlib.suppress(OSError):
                    self._save(env)
                changed.append(env["id"])
        return changed

    # ── 创建 ──────────────────────────────────────────────────────────────────

    def create(self, kind: str, title: str, params: dict) -> str:
        if kind not in TASK_KINDS:
            raise ValueError(f"未知任务类型：{kind}")
        with self._lock:
            while True:
                task_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{secrets.token_hex(2)}"
                if task_id not in self._envs and not self._file(task_id).exists():
                    break
            env = {
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
                "owner_pid": os.getpid(),
                "owner_boot": _BOOT_ID,
            }
            self._save(env)  # 先落盘再入队：入队后的回调一定找得到信封
            self._envs[task_id] = env
        return task_id

    def discard(self, task_id: str) -> None:
        """入队失败时撤掉刚建的信封（只用于提交路径的异常回滚）。"""
        with self._lock:
            self._envs.pop(task_id, None)
            with contextlib.suppress(OSError):
                self._file(task_id).unlink()

    # ── JobObserver ───────────────────────────────────────────────────────────

    def on_start(self, job: Job) -> None:
        with self._lock:
            env = self._envs.get(job.task_id or "")
            if env is None:
                return
            self._live[env["id"]] = job
            env["state"] = "running"
            env["started"] = _now()
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
            env = self._envs.get(task_id)
            if env is None:
                return
            env["state"] = "succeeded" if job.exit_code == 0 else "failed"
            env["exit_code"] = job.exit_code
            env["finished"] = _now()
            env["summary"] = summary
            self._save(env)
            self._prune_locked()

    # ── 保留（决策 D8）─────────────────────────────────────────────────────────

    def _prune_locked(self) -> None:
        done = [e for e in self._envs.values() if e.get("state") in TERMINAL_STATES]
        if len(done) <= MAX_HISTORY:
            return
        done.sort(key=lambda e: (e.get("finished") or "", e["id"]))
        for env in done[: len(done) - MAX_HISTORY]:
            self._envs.pop(env["id"], None)
            for path in (self._file(env["id"]), self._log_file(env["id"])):
                with contextlib.suppress(OSError):
                    path.unlink()

    # ── 读 ────────────────────────────────────────────────────────────────────

    def _brief_locked(self, env: dict) -> dict:
        state = env.get("state")
        live = self._live.get(env["id"])
        if state in ATTENTION_STATES:
            group = "attention"
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
        }

    def running_brief(self) -> dict | None:
        """此刻在跑的任务（给可写 turn 的等待提示用）；没有则 None。"""
        with self._lock:
            for task_id in self._live:
                env = self._envs.get(task_id)
                if env is not None and env.get("state") == "running":
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
