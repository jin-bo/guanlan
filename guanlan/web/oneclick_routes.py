"""Web 一键入库端点（P4.30 §7）：薄壳，内核在 `guanlan/oneclick.py`，与 CLI `guanlan import` 共用任务清单。

- 准备（①–④）与执行（⑤⑥）都作为 JobQueue 写作业跑：worker 持 `WriteGate.write_lock`（含库级 OS 文件锁，
  决策P4.30-15），与 ingest/heal/可写 turn 同一单写者 FIFO；进度经 `emit` 实时推进 `job.output`，同时
  追加写 `workspace/tasks/<id>.log`（页面刷新或服务重启后仍可看）。
- 暂停（决策P4.30-5）：进程内标志，作业在篇与篇之间检查；不中途杀 Agentao。
- 全部写端点 `_writer_only` + 层③ 423（可写 turn 活跃时拒）。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import anyio
from fastapi import HTTPException
from pydantic import BaseModel, field_validator

from ..convert import _BACKENDS
from ..errors import EXIT_OK, EXIT_USAGE
from ..ingest import run_ingest
from ..kblock import KBLockBusy, acquire, describe_holder, holder_alive, read_holder
from ..oneclick import (
    OneClickError,
    active_task,
    cancel,
    confirm,
    load_task,
    prepare,
    recover_stale,
    resume,
)
from .uploads import _safe_upload_file

LOG_TAIL_LINES = 200
_LOG_TAIL_BYTES = 256 * 1024  # 每 2 秒轮询只读日志末尾这么多字节，不随长任务的日志整读


class OneClickBody(BaseModel):
    """`POST /api/oneclick`：`upload` = `workspace/uploads/<名>`；`backend` 透传转换后端。"""

    upload: str
    backend: str = "auto"

    @field_validator("backend")
    @classmethod
    def _backend_known(cls, v: str) -> str:
        if v not in _BACKENDS:
            raise ValueError(f"未知 backend：{v}（须 ∈ {_BACKENDS}）。")
        return v


class ConfirmBody(BaseModel):
    overwrite: bool = False


def _log_tail(root: Path, task: dict) -> list[str]:
    rel = task.get("log")
    if not rel:
        return []
    try:
        with (root / rel).open("rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            start = max(0, size - _LOG_TAIL_BYTES)
            fh.seek(start)
            data = fh.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:  # 从中间截起：第一行可能是半行，丢掉
        lines = lines[1:]
    return lines[-LOG_TAIL_LINES:]


def task_view(root: Path, task: dict, *, job_id: str | None = None, pausing: bool = False) -> dict:
    keep = (
        "task_id",
        "state",
        "reason",
        "upload",
        "sample",
        "title",
        "pages",
        "chars",
        "split",
        "normalized",
        "precheck",
        "overwrite",
        "warnings",
        "estimate_seconds",
        "links_before",
        "links_after",
        "created",
        "confirmed",
        "finished",
        "origin",
        "log",
    )
    out = {k: task.get(k) for k in keep}
    out["items"] = [
        {k: it.get(k) for k in ("raw", "parsed", "chars", "pages", "path", "method", "land", "state", "exit_code", "seconds")}
        for it in task.get("items", [])
    ]
    out["done"] = sum(1 for it in task.get("items", []) if it.get("state") == "done")
    out["job_id"] = job_id
    out["pausing"] = pausing
    out["log_tail"] = _log_tail(root, task)
    return out


def register(
    app,
    *,
    root: Path,
    jobs,
    writer_only,
    reject_if_writable_active,
    model: str | None,
    runner=None,
    reader: bool = False,
    owns_kb_lock=None,
) -> None:
    """把一键入库端点挂到 `app`（reader 下 `writer_only` 不注册写端点；只读端点同样只给可写宿主）。

    `owns_kb_lock()`：作业内问写锁「本进程是否真持库级锁」（`KBWriteLock.owns_kb_lock`）；不给则按锁文件
    里的 pid 判断（只给旧测试兜底）。
    """
    # 任务 → (本进程在跑的作业号, 该作业的暂停标志)。标志随作业登记、随作业清理，不另开一张只增不减的表。
    running_jobs: dict[str, tuple[str, threading.Event]] = {}
    # 任务 → 本进程最近一次失败作业的输出（确认/继续作业没跑起来或被拒：库被 CLI 占用、冲突复检等）。
    # 这些失败不一定写进任务日志，任务又退回「等待确认 / 已暂停」，不透出就成了「点了没反应」。
    # 下一次确认/继续登记作业时清掉。
    last_failure: dict[str, str] = {}
    pending_prepare: dict[str, str] = {}  # 本进程最近一次准备作业（只在事件循环线程读写）
    state_lock = threading.Lock()

    # 启动时恢复（§6.4）：拿得到库级锁 → 原执行者已退出，preparing→failed、running→paused；拿不到 → 不改清单。
    # reader 零写（决策P4.9-2）：连锁文件都不建。
    if not reader:
        try:
            with acquire(root, "web: startup") as lock:
                if not lock.nested:  # 嵌套放行 = 祖先写运行还活着，不能改它的清单（同 oneclick._recover_if_owner）
                    recover_stale(root)
        except (KBLockBusy, OSError):
            pass

    def _recover_in_job() -> None:
        """作业内恢复：只有本进程**真持**库级锁时才恢复。嵌套放行（持有者是祖先进程）或锁文件建不出
        （`KBWriteLock` 退化为只有进程内锁）时，都不能断定原执行者已退出。"""
        owns = owns_kb_lock() if owns_kb_lock is not None else (read_holder(root) or {}).get("pid") == os.getpid()
        if owns:
            recover_stale(root)

    def _ingest(raw_rel: str, kb: Path) -> int:
        return run_ingest(raw_rel, root=kb, model=model, runner=runner)

    def _running(task_id: str) -> tuple[str, threading.Event] | None:
        """本进程为该任务在跑（未结束）的作业及其暂停标志；作业已结束 → 清掉登记、返回 None。"""
        with state_lock:
            entry = running_jobs.get(task_id)
        if entry is None:
            return None
        job = jobs.get_job(entry[0])
        if job is not None and job.state != "done":
            return entry
        with state_lock:
            if running_jobs.get(task_id) is entry:  # 只清这一条：期间新登记的作业不受影响
                running_jobs.pop(task_id, None)
                if job is not None and job.exit_code not in (None, EXIT_OK):
                    tail = (job.output or "").strip().splitlines()[-5:]  # 逐篇 ingest 的输出可能很长，只留末尾
                    last_failure[task_id] = "\n".join(tail) or f"退出码 {job.exit_code}"
        return None

    def _job_for(task_id: str) -> str | None:
        entry = _running(task_id)
        return entry[0] if entry is not None else None

    def _track(task_id: str, job_id: str, flag: threading.Event) -> None:
        with state_lock:
            running_jobs[task_id] = (job_id, flag)
            last_failure.pop(task_id, None)

    def _wrap(fn):
        """作业 thunk：一键入库的用法/状态错误打印给 job.output、以 EXIT_USAGE 结束。"""

        def thunk(emit) -> int:
            try:
                return fn(emit)
            except OneClickError as exc:
                print(str(exc))
                return EXIT_USAGE

        return thunk

    def _view(task: dict) -> dict:
        # 暂停标志只在本进程作业还在跑时有意义：作业结束后留着的标志不能让任务显示成「正在暂停」。
        entry = _running(task["task_id"])
        view = task_view(
            root,
            task,
            job_id=entry[0] if entry is not None else None,
            pausing=entry is not None and entry[1].is_set(),
        )
        with state_lock:
            view["last_error"] = last_failure.get(task["task_id"])
        return view

    def _load(task_id: str) -> dict:
        try:
            return load_task(root, task_id)
        except OneClickError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @writer_only(app.get("/api/oneclick"))
    async def oneclick_status() -> dict:
        task = await anyio.to_thread.run_sync(active_task, root)
        holder = await anyio.to_thread.run_sync(read_holder, root)
        if not holder_alive(holder):
            holder = None
        if task is None:
            return {"active": None, "holder": describe_holder(holder) if holder else None}
        view = await anyio.to_thread.run_sync(lambda: _view(task))
        return {"active": view, "holder": describe_holder(holder) if holder else None}

    @writer_only(app.get("/api/oneclick/{task_id}"))
    async def oneclick_task(task_id: str) -> dict:
        task = await anyio.to_thread.run_sync(_load, task_id)
        return await anyio.to_thread.run_sync(lambda: _view(task))  # 读日志尾是阻塞 IO，不占事件循环

    @writer_only(app.post("/api/oneclick"))
    async def oneclick_start(body: OneClickBody) -> dict:
        reject_if_writable_active()
        upload_path = _safe_upload_file(root, body.upload)  # 越界 400 / 缺失 404
        existing = await anyio.to_thread.run_sync(active_task, root)
        # 残留的「准备中」（执行者已退出：CLI 被 kill / 关终端、或本进程启动时锁被占而没恢复）没有继续/放弃
        # 的出口：不在这里 409，交给作业——作业持锁先 `_recover_in_job` 把它改成失败，`prepare` 再复检。
        # 持有者是本进程（正在跑别的写作业）时，准备中的任务若是本进程的，下面的排队检查会拦住；
        # 否则它就是别处留下的残留，不能因为「持有者活着」而 409。
        holder = await anyio.to_thread.run_sync(read_holder, root)
        stale_preparing = (
            existing is not None
            and existing.get("state") == "preparing"
            and (not holder_alive(holder) or (holder or {}).get("pid") == os.getpid())
        )
        if existing is not None and not stale_preparing:
            raise HTTPException(
                status_code=409,
                detail=f"已有未完成的入库任务 {existing['task_id']}；请先继续或放弃它。",
            )
        # 准备作业排在别的写作业后面时，任务清单还没建出来：上面的检查拦不住第二次点击，
        # 第二个作业会白跑到 prepare 里才失败。按本进程排队中的准备作业再拦一次。
        pending = pending_prepare.get("job_id")
        if pending is not None:
            job = jobs.get_job(pending)
            if job is not None and job.state != "done":
                raise HTTPException(status_code=409, detail="已有一个入库准备作业在排队或执行中。")
        backend = body.backend

        def _prepare(emit) -> int:
            _recover_in_job()
            prepare(root, upload_path, origin_kind="web", backend=backend, emit=emit)
            return EXIT_OK

        job_id = jobs.enqueue("oneclick-prepare", _wrap(_prepare))
        pending_prepare["job_id"] = job_id
        return {"job_id": job_id}

    @writer_only(app.post("/api/oneclick/{task_id}/confirm"))
    async def oneclick_confirm(task_id: str, body: ConfirmBody) -> dict:
        reject_if_writable_active()
        task = await anyio.to_thread.run_sync(_load, task_id)
        if task.get("state") != "awaiting_confirm":
            raise HTTPException(status_code=409, detail=f"任务当前状态为 {task.get('state')}，不在等待确认。")
        if _job_for(task_id) is not None:  # 确认作业已在队列里（排在别的写作业后，状态还没翻）
            raise HTTPException(status_code=409, detail="该任务已确认，正在排队执行。")
        flag = threading.Event()  # 每个作业一枚新标志
        overwrite = body.overwrite

        def _run(emit) -> int:
            code, _task = confirm(
                root, task_id, overwrite=overwrite, emit=emit, ingest=_ingest, should_pause=flag.is_set
            )
            return code

        job_id = jobs.enqueue("oneclick", _wrap(_run))
        _track(task_id, job_id, flag)
        return {"job_id": job_id}

    @writer_only(app.post("/api/oneclick/{task_id}/resume"))
    async def oneclick_resume(task_id: str, body: ConfirmBody | None = None) -> dict:
        reject_if_writable_active()
        task = await anyio.to_thread.run_sync(_load, task_id)
        if task.get("state") not in ("paused", "running"):
            raise HTTPException(status_code=409, detail=f"任务当前状态为 {task.get('state')}，不能继续。")
        if _job_for(task_id) is not None:
            raise HTTPException(status_code=409, detail="该任务正在执行。")
        flag = threading.Event()  # 每个作业一枚新标志

        def _run(emit) -> int:
            _recover_in_job()  # 原执行者已退出的 running → paused
            code, _task = resume(
                root, task_id, emit=emit, ingest=_ingest, should_pause=flag.is_set, overwrite=bool(body and body.overwrite)
            )
            return code

        job_id = jobs.enqueue("oneclick", _wrap(_run))
        _track(task_id, job_id, flag)
        return {"job_id": job_id}

    @writer_only(app.post("/api/oneclick/{task_id}/pause"))
    async def oneclick_pause(task_id: str) -> dict:
        entry = _running(task_id)
        if entry is None:
            raise HTTPException(status_code=409, detail="该任务当前没有在执行。")
        entry[1].set()
        return {"pausing": True}

    @writer_only(app.post("/api/oneclick/{task_id}/cancel"))
    async def oneclick_cancel(task_id: str) -> dict:
        reject_if_writable_active()
        await anyio.to_thread.run_sync(_load, task_id)
        if _job_for(task_id) is not None:
            raise HTTPException(status_code=409, detail="该任务正在执行；请先暂停。")
        def _cancel(emit) -> int:
            _recover_in_job()  # 原执行者已退出的 running → paused，才能放弃（同 CLI --cancel）
            cancel(root, task_id)
            return EXIT_OK

        job = await anyio.to_thread.run_sync(
            jobs.submit_and_wait,
            "oneclick-cancel",
            _wrap(_cancel),
            abandon_on_cancel=True,
        )
        if job.exit_code != EXIT_OK:
            raise HTTPException(status_code=409, detail=job.output.strip() or "放弃失败。")
        return {"cancelled": task_id}
