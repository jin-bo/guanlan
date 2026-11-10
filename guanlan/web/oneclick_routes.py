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
        lines = (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
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
) -> None:
    """把一键入库端点挂到 `app`（reader 下 `writer_only` 不注册写端点；只读端点同样只给可写宿主）。"""
    pause_flags: dict[str, threading.Event] = {}
    running_jobs: dict[str, str] = {}
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
        if (read_holder(root) or {}).get("pid") == os.getpid():
            recover_stale(root)

    def _ingest(raw_rel: str, kb: Path) -> int:
        return run_ingest(raw_rel, root=kb, model=model, runner=runner)

    def _flag(task_id: str) -> threading.Event:
        with state_lock:
            return pause_flags.setdefault(task_id, threading.Event())

    def _job_for(task_id: str) -> str | None:
        with state_lock:
            job_id = running_jobs.get(task_id)
        if job_id is None:
            return None
        job = jobs.get_job(job_id)
        return job_id if job is not None and job.state != "done" else None

    def _track(task_id: str, job_id: str) -> None:
        with state_lock:
            running_jobs[task_id] = job_id

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
        job_id = _job_for(task["task_id"])
        return task_view(root, task, job_id=job_id, pausing=job_id is not None and _flag(task["task_id"]).is_set())

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
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"已有未完成的入库任务 {existing['task_id']}；请先继续或放弃它。",
            )
        backend = body.backend

        def _prepare(emit) -> int:
            _recover_in_job()
            prepare(root, upload_path, origin_kind="web", backend=backend, emit=emit)
            return EXIT_OK

        return {"job_id": jobs.enqueue("oneclick-prepare", _wrap(_prepare))}

    @writer_only(app.post("/api/oneclick/{task_id}/confirm"))
    async def oneclick_confirm(task_id: str, body: ConfirmBody) -> dict:
        reject_if_writable_active()
        task = await anyio.to_thread.run_sync(_load, task_id)
        if task.get("state") != "awaiting_confirm":
            raise HTTPException(status_code=409, detail=f"任务当前状态为 {task.get('state')}，不在等待确认。")
        if _job_for(task_id) is not None:  # 确认作业已在队列里（排在别的写作业后，状态还没翻）
            raise HTTPException(status_code=409, detail="该任务已确认，正在排队执行。")
        flag = _flag(task_id)
        flag.clear()
        overwrite = body.overwrite

        def _run(emit) -> int:
            code, _task = confirm(
                root, task_id, overwrite=overwrite, emit=emit, ingest=_ingest, should_pause=flag.is_set
            )
            return code

        job_id = jobs.enqueue("oneclick", _wrap(_run))
        _track(task_id, job_id)
        return {"job_id": job_id}

    @writer_only(app.post("/api/oneclick/{task_id}/resume"))
    async def oneclick_resume(task_id: str) -> dict:
        reject_if_writable_active()
        task = await anyio.to_thread.run_sync(_load, task_id)
        if task.get("state") not in ("paused", "running"):
            raise HTTPException(status_code=409, detail=f"任务当前状态为 {task.get('state')}，不能继续。")
        if _job_for(task_id) is not None:
            raise HTTPException(status_code=409, detail="该任务正在执行。")
        flag = _flag(task_id)
        flag.clear()

        def _run(emit) -> int:
            _recover_in_job()  # 原执行者已退出的 running → paused
            code, _task = resume(root, task_id, emit=emit, ingest=_ingest, should_pause=flag.is_set)
            return code

        job_id = jobs.enqueue("oneclick", _wrap(_run))
        _track(task_id, job_id)
        return {"job_id": job_id}

    @writer_only(app.post("/api/oneclick/{task_id}/pause"))
    async def oneclick_pause(task_id: str) -> dict:
        if _job_for(task_id) is None:
            raise HTTPException(status_code=409, detail="该任务当前没有在执行。")
        _flag(task_id).set()
        return {"pausing": True}

    @writer_only(app.post("/api/oneclick/{task_id}/cancel"))
    async def oneclick_cancel(task_id: str) -> dict:
        reject_if_writable_active()
        await anyio.to_thread.run_sync(_load, task_id)
        if _job_for(task_id) is not None:
            raise HTTPException(status_code=409, detail="该任务正在执行；请先暂停。")
        job = await anyio.to_thread.run_sync(
            jobs.submit_and_wait,
            "oneclick-cancel",
            _wrap(lambda emit: (cancel(root, task_id), EXIT_OK)[1]),
            abandon_on_cancel=True,
        )
        if job.exit_code != EXIT_OK:
            raise HTTPException(status_code=409, detail=job.output.strip() or "放弃失败。")
        return {"cancelled": task_id}
