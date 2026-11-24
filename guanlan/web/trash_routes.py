"""回收区端点（P4.36 T1，见 docs/P4.36-Web回收区.md §6）。reader 下都不注册。

- `GET /api/trash`：列表（含删除保护）；`GET /api/trash/{name}`：详情。只读、零 LLM。
- `POST /api/trash/purge {name}`：彻底删除一条。删除只碰 `.trash/`，**不进任务中心**，同步执行：
  层③ 423 → 非阻塞取进程写锁（被 Web 写任务占着 → 409）→ 该锁同时取库级锁（被 CLI 占着 → 409）→
  核对**真持**库级锁（锁文件建不出时的进程内退化、嵌套放行都不算 → 409）→ 锁内复核保护并删除。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from .. import trash
from ..kblock import KBLockBusy


class PurgeBody(BaseModel):
    name: str


def _http(exc: trash.TrashError) -> HTTPException:
    if isinstance(exc, trash.TrashNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, trash.TrashProtected):
        return HTTPException(status_code=409, detail={"message": str(exc), "reasons": exc.reasons})
    if isinstance(exc, trash.TrashLockError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def register(app, *, root: Path, writer_only, reject_if_writable_active, write_lock) -> None:
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
