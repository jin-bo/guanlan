"""Web 网址导入端点（P4.31，见 docs/P4.31-网址导入.md §2.1、§6）。薄壳：下载器在 `guanlan/fetch.py`。

- 走上传「快车道」（决策P4.31-1）：只写 `workspace/uploads/`，不进写作业队列、不取库级锁。
- 本期一个网址、同一时刻一个下载（决策P4.31-12）；状态只在内存（§9 Q4），页面刷新后 `GET` 找回。
- 后台线程只下字节；**选名、检查、换名回到事件循环线程**、中间不 await，与 `POST /api/upload` 的提交串行
  （决策P4.31-4）。
- `oneclick=true`：提交后调 `start_prepare` 发起准备作业，确认关口照旧（决策P4.31-10）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
import uuid
from pathlib import Path

from fastapi import HTTPException

from ..fetch import FetchCancelled, FetchError, FetchResult, check_url, fetch_url
from ..sourcerec import redact_text, redact_url
from .oneclick_routes import ImportOptions
from .uploads import _safe_workspace_target, commit_download, discard_upload


class FetchBody(ImportOptions):
    """`backend` / `html_extract` 与 `POST /api/oneclick` 同一套校验（422），只在 `oneclick=true` 时用到。"""

    url: str
    oneclick: bool = False


_ACTIVE = ("downloading", "committing")


def _exc_text(exc: BaseException) -> str:
    """异常 → 给人看的一句。`TimeoutError()` / `CancelledError()` 的 str 是空串（异常对象本身恒为真，
    `exc or 类名` 退不到类名）：没消息时只给类名，免得显示成「保存失败：」。"""
    msg = str(exc)
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


COMMIT_TIMEOUT_SECONDS = 120  # 选名 + 换名 + 发起一键入库：正常远不到这么久


def register(app, *, root: Path, writer_only, reject_if_writable_active, start_prepare) -> None:
    """挂 `POST /api/upload/url`、`GET /api/upload/url`、`POST /api/upload/url/{id}/cancel`。

    三个都经 `writer_only`：reader 宿主不注册（决策P4.9-2），连查询端点也不给——它只服务下载这个写操作。
    """
    uploads_dir = root / "workspace" / "uploads"
    lock = threading.Lock()  # 只护 `slot` 的读写；下载、提交都不在锁内
    slot: dict = {"state": None}  # 当前或最近一次下载

    def _view() -> dict | None:
        with lock:
            st = slot["state"]
            if st is None:
                return None
            view = {k: v for k, v in st.items() if not k.startswith("_")}
        # 进度行与列表的 🔗 同一口径：签名链接的凭据参数不上屏（截图、共享屏幕时不漏）。报错文字里也常带完整网址
        # （浏览器后备的 `net::ERR_… at https://…`、底层异常原文），一并脱敏
        view["url"] = redact_url(view["url"])
        for key in ("error", "oneclick_error"):
            if view.get(key):
                view[key] = redact_text(view[key])
        return view

    def _update(st: dict, **kw) -> None:
        with lock:
            st.update(kw)

    async def _commit(st: dict, res: FetchResult, body: FetchBody) -> None:
        """事件循环线程：选名、检查、换名之间没有 await（决策P4.31-4）；之后才可能 await 发起一键入库。"""
        try:
            if st["_cancel"].is_set():
                discard_upload(res.tmp)
                _update(st, state="cancelled", error="已停止下载。", finished=time.time())
                return
            try:
                reject_if_writable_active()  # 下载可能要几分钟，期间开始的可写会话同样要挡（层③）
                try:
                    target = _safe_workspace_target(root, "uploads", res.suggested_name)
                except HTTPException:
                    # 网址给的文件名规范化后为空（全是标点等）：换个通用名，不为名字丢掉整个下载
                    target = _safe_workspace_target(root, "uploads", "download" + Path(res.suggested_name).suffix)
            except HTTPException as exc:
                discard_upload(res.tmp)
                msg = "可写对话进行中，未保存下载的文件；请结束对话后重试。" if exc.status_code == 423 else str(exc.detail)
                _update(st, state="failed", error=msg, finished=time.time())
                return
            try:
                final = commit_download(res.tmp, target, res.record)
            except OSError as exc:
                _update(st, state="failed", error=f"写盘失败：{exc}", finished=time.time())
                return
        except BaseException:
            discard_upload(res.tmp)
            raise
        _update(st, saved=f"workspace/uploads/{final.name}", name=final.name)
        if body.oneclick:
            try:
                job_id = await start_prepare(final, body.backend, body.html_extract)
                _update(st, oneclick_job=job_id)
            except HTTPException as exc:  # 已有未完成的入库任务等：文件留在暂存区，记原因、不报错
                _update(st, oneclick_error=str(exc.detail))
            except Exception as exc:  # noqa: BLE001 — 文件已保存：不能因发起失败把整次下载记成失败
                _update(st, oneclick_error=_exc_text(exc))
        _update(st, state="done", finished=time.time())

    def _worker(st: dict, body: FetchBody, loop: asyncio.AbstractEventLoop) -> None:
        def progress(received: int, total: int | None) -> None:
            _update(st, received=received, total=total)

        try:
            res = fetch_url(st["url"], uploads_dir, progress=progress, cancel=st["_cancel"], env_root=root)
        except FetchCancelled:
            _update(st, state="cancelled", error="已停止下载。", finished=time.time())
            return
        except FetchError as exc:
            _update(st, state="failed", error=str(exc), finished=time.time())
            return
        except Exception as exc:  # noqa: BLE001 — 后台线程不能把异常丢给没人看的地方
            _update(st, state="failed", error=f"下载失败：{_exc_text(exc)}", finished=time.time())
            return
        _update(st, state="committing", kind=res.kind, received=res.size, total=res.size, via=res.record.get("fetched_via"))
        try:
            # 有上限地等：服务关停时事件循环停了却没关，提交协程永远不跑，不设上限就会永远停在 committing
            fut = asyncio.run_coroutine_threadsafe(_commit(st, res, body), loop)
            try:
                fut.result(timeout=COMMIT_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                fut.cancel()
                raise
        except BaseException as exc:  # noqa: BLE001 — 事件循环已关（服务停止）等；关停时 `_commit` 被取消抛的
            # CancelledError 不是 Exception 子类，漏掉的话状态会永远停在 committing、后续下载一律 409
            discard_upload(res.tmp)
            if st.get("saved"):  # 文件已落定、只是发起一键入库时被打断：不能把已保存的下载报成失败
                _update(st, state="done", oneclick_error=st.get("oneclick_error") or _exc_text(exc),
                        finished=time.time())
            else:
                _update(st, state="failed", error=f"保存失败：{_exc_text(exc)}", finished=time.time())

    @writer_only(app.post("/api/upload/url"))
    async def fetch_start(body: FetchBody) -> dict:
        reject_if_writable_active()
        try:
            url = check_url(body.url)
        except FetchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        with lock:
            cur = slot["state"]
            if cur is not None and cur["state"] in _ACTIVE:
                raise HTTPException(status_code=409, detail=f"已有下载在进行：{redact_url(cur['url'])}")
            st = {
                "download_id": uuid.uuid4().hex[:12],
                "url": url,
                "state": "downloading",
                "received": 0,
                "total": None,
                "kind": None,
                "saved": None,
                "name": None,
                "error": None,
                "oneclick": body.oneclick,
                "oneclick_job": None,
                "oneclick_error": None,
                "via": None,  # 403 后经 agentao 后备取得时：playwright / jina
                "started": time.time(),
                "finished": None,
                "_cancel": threading.Event(),
            }
            slot["state"] = st
        loop = asyncio.get_running_loop()
        threading.Thread(target=_worker, args=(st, body, loop), name="guanlan-fetch", daemon=True).start()
        return {"download_id": st["download_id"]}

    @writer_only(app.get("/api/upload/url"))
    async def fetch_status() -> dict | None:
        return _view()

    @writer_only(app.post("/api/upload/url/{download_id}/cancel"))
    async def fetch_cancel(download_id: str) -> dict:
        with lock:
            st = slot["state"]
            if st is None or st["download_id"] != download_id or st["state"] not in _ACTIVE:
                raise HTTPException(status_code=409, detail="没有正在进行的这个下载。")
            if st["state"] == "committing":  # 已在保存：提交只在开头看一次停止标志，此时答应停止会是假话
                raise HTTPException(status_code=409, detail="下载已完成、正在保存，不能停止。")
            st["_cancel"].set()
        return {"cancelling": True}
