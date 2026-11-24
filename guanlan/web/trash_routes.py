"""回收区端点（P4.36 T1，见 docs/P4.36-Web回收区.md §6）。reader 下都不注册。

- `GET /api/trash`：列表（含删除保护）；`GET /api/trash/{name}`：详情。只读、零 LLM。
- `GET /api/trash/restore/preview?name=`、`POST /api/restore {name, plan_digest}`（T2）：恢复撤回条目。提交在**独占
  库级锁内**核对资格与摘要、落盘任务信封，与删除互斥（CLI 也拿同一把锁）；作业取得写锁后重算、核对摘要，
  `restore_writes_began` 落盘之后才动文件。任务种类 `restore`，与撤回一样不拍前像。
- `POST /api/trash/purge {name}`：彻底删除一条。删除只碰 `.trash/`，**不进任务中心**，同步执行：
  层③ 423 → 非阻塞取进程写锁（被 Web 写任务占着 → 409）→ 该锁同时取库级锁（被 CLI 占着 → 409）→
  核对**真持**库级锁（锁文件建不出时的进程内退化、嵌套放行都不算 → 409）→ 锁内复核保护并删除。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Query
from pydantic import BaseModel

from .. import trash
from ..errors import EXIT_OK, EXIT_USAGE
from ..kblock import KBLockBusy
from ..restore import execute_restore, format_result, plan_restore


class PurgeBody(BaseModel):
    name: str


class RestoreBody(BaseModel):
    name: str
    plan_digest: str


@dataclass
class RestoreSpec:
    """交给 `_submit_task` 的任务规格（与撤回的 `RemoveSpec` 同形）。"""

    title: str
    params: dict
    extra: dict | None
    make_job: Callable[[str | None], Callable]
    retry_of: str | None = None
    verb: str = "恢复"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _http(exc: trash.TrashError) -> HTTPException:
    if isinstance(exc, trash.TrashNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, trash.TrashProtected):
        return HTTPException(status_code=409, detail={"message": str(exc), "reasons": exc.reasons})
    if isinstance(exc, trash.TrashLockError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def register(app, *, root: Path, writer_only, reject_if_writable_active, write_lock, tasks=None, submit_restore=None) -> None:
    import anyio

    def _list() -> dict:
        entries = trash.annotate(root, trash.scan(root))
        return {"entries": [e.brief() for e in entries], "size": sum(e.size for e in entries)}

    @writer_only(app.get("/api/trash"))
    async def trash_list() -> dict:
        try:
            return await anyio.to_thread.run_sync(_list)
        except trash.TrashError as exc:
            raise _http(exc) from None

    @writer_only(app.get("/api/trash/{name}"))
    async def trash_detail(name: str) -> dict:
        try:
            entry = await anyio.to_thread.run_sync(trash.get_entry, root, name)
        except trash.TrashError as exc:
            raise _http(exc) from None
        return entry.detail()

    def _purge(name: str) -> dict:
        # 非阻塞：Web 写任务（worker 持 write_lock）在跑 → 不排队，直接 409。
        if not write_lock.acquire(blocking=False):
            raise trash.TrashLockError("有写任务正在执行，请等它结束后再删除。")
        try:
            # KBWriteLock 在锁文件建不出时退化为只有进程内锁；嵌套放行也不算独占（§4.1）。
            if not getattr(write_lock, "owns_kb_lock", False):
                raise trash.TrashLockError("无法独占库级锁（锁文件建不出或只拿到嵌套放行），不删除。")
            entry = trash.purge_locked(root, name)
        finally:
            write_lock.release()
        return {"purged": entry.name, "size": entry.size, "files": entry.files}

    @writer_only(app.post("/api/trash/purge"))
    async def trash_purge(body: PurgeBody) -> dict:
        reject_if_writable_active()  # 层③：可写 turn 活跃 → 423
        try:
            return await anyio.to_thread.run_sync(_purge, body.name)
        except KBLockBusy as exc:  # 库正被 CLI 写
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except trash.TrashError as exc:
            raise _http(exc) from None
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"删除失败：{exc}") from None

    # ── 恢复（T2）────────────────────────────────────────────────────────────

    @writer_only(app.get("/api/trash/restore/preview"))
    async def restore_preview(name: str = Query(...)) -> dict:
        try:
            plan = await anyio.to_thread.run_sync(plan_restore, root, name)
        except trash.TrashError as exc:
            raise _http(exc) from None
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"读取库内文件失败，无法预览恢复：{exc}") from None
        return plan.to_dict()

    def _acquire_strict() -> None:
        if not write_lock.acquire(blocking=False):
            raise trash.TrashLockError("有写任务正在执行，请等它结束后再提交。")
        if not getattr(write_lock, "owns_kb_lock", False):
            write_lock.release()
            raise trash.TrashLockError("无法独占库级锁（锁文件建不出或只拿到嵌套放行），不提交。")

    def _check(name: str, digest: str):
        try:
            plan = plan_restore(root, name)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"读取库内文件失败，没有提交恢复：{exc}") from None
        if not plan.ok:
            raise HTTPException(
                status_code=409,
                detail={"message": plan.blocked or "有冲突，不能恢复。", "conflicts": plan.conflicts},
            )
        if plan.digest != digest:
            # reason=stale：前端据此自动重新预览（旧摘要再点也只会再 409），与「锁忙」的 409 区分开
            raise HTTPException(
                status_code=409, detail={"message": "预览之后库或回收区有变化，请重新预览。", "reason": "stale"}
            )
        return plan

    def _mark_superseded(name: str, task_id: str | None) -> None:
        """恢复完成后，给同一条目写了一半的旧恢复任务（中断 / 失败）记 `superseded_by`：任务中心不再提示它待处理，
        保留策略也照常清理（同撤回，P4.35）。失败只影响引导。"""
        if tasks is None or task_id is None:
            return
        for env in tasks.envelopes("restore"):
            if (
                env.get("id") != task_id
                and (env.get("params") or {}).get("name") == name
                and env.get("state") in ("interrupted", "failed")
                and env.get("restore_writes_began")
                and not env.get("superseded_by")
                and not tasks.set_fields(env["id"], superseded_by=task_id)
            ):
                print(f"（未能在旧任务 {env['id']} 上记下「已由本任务完成」，任务中心可能还会提示它待处理。）")

    def _make_job(name: str, digest: str):
        def bind(task_id: str | None):
            def job(emit) -> int:
                # 执行同样不接受退化的锁（§4.1）：worker 持的 write_lock 须真持库级锁。
                if not getattr(write_lock, "owns_kb_lock", False):
                    print("✗ 没有执行任何改动：无法独占库级锁（锁文件建不出或只拿到嵌套放行）。")
                    return EXIT_USAGE
                try:
                    plan = plan_restore(root, name)
                except (trash.TrashError, OSError) as exc:  # OSError：读回收区 / 库内文件失败，同样一字未动
                    print(f"✗ 没有执行任何改动：{exc}")
                    return EXIT_USAGE
                if not plan.ok:
                    print(f"✗ 没有执行任何改动：{plan.blocked or '；'.join(plan.conflicts)}")
                    return EXIT_USAGE
                if plan.digest != digest:
                    print("✗ 预览之后库或回收区有变化，没有执行任何改动，请重新预览。")
                    return EXIT_USAGE
                began: list[bool] = []

                def before() -> None:
                    if (
                        tasks is None
                        or task_id is None
                        or tasks.envelope(task_id) is None
                        or not tasks.set_fields(task_id, restore_writes_began=_now())
                    ):
                        raise OSError("任务记录写不进（workspace/ 不可写？）")
                    began.append(True)

                try:
                    result = execute_restore(root, plan, task_id=task_id, before_write=before)
                except Exception as exc:  # noqa: BLE001 — 开始写之后任何异常都要给「重新预览续跑」的引导
                    if began:
                        print(f"✗ 恢复时出错，可能已部分执行；到回收区重新预览并提交一次即可完成剩余步骤：{exc}")
                    else:
                        print(f"✗ 没有执行任何改动：{exc}")
                    return EXIT_USAGE
                print(format_result(result))
                _mark_superseded(name, task_id)
                return EXIT_OK

            return job

        return bind

    @writer_only(app.post("/api/restore"))
    async def restore_submit(body: RestoreBody) -> dict:
        reject_if_writable_active()  # 层③：可写 turn 活跃 → 423
        if submit_restore is None or tasks is None:
            raise HTTPException(status_code=503, detail="任务记录不可用（workspace/ 读写不了），没有提交恢复。")
        try:
            await anyio.to_thread.run_sync(_acquire_strict)
        except KBLockBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except trash.TrashError as exc:
            raise _http(exc) from None
        try:
            # 锁内核对资格与摘要、落盘任务信封：删除也在这把锁内读信封，二者不会交错（§4.2）。
            try:
                plan = await anyio.to_thread.run_sync(_check, body.name, body.plan_digest)
            except trash.TrashError as exc:
                raise _http(exc) from None
            spec = RestoreSpec(
                title=f"恢复回收区条目：{plan.name}",
                params={"name": plan.name, "plan_digest": body.plan_digest},
                extra={"entry": {"trash": plan.name}},
                make_job=_make_job(plan.name, body.plan_digest),
            )
            return await submit_restore(spec)
        finally:
            # 同步释放（只关锁文件、还进程内锁，不阻塞）：请求被取消时 finally 里的 await 会先抛取消、锁就漏了。
            write_lock.release()

