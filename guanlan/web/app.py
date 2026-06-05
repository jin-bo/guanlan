"""FastAPI app + 路由（P4，见 docs/P4-Web宿主.md §4/§5）。

宿主自身**不做任何确定性/语义判断**：端点只负责"收 HTTP 请求 → 调既有包内函数或嵌入
agent → 序列化/转发结果"。阻塞调用一律卸到线程（零 LLM 报告经 `anyio.to_thread.run_sync`、
写作业 `ingest` 经单 worker 线程），绝不在事件循环里直接跑阻塞代码（决策P4-2）。

C1 仅落地骨架：`GET /` 返回随包 `index.html` + `/static/*` 静态挂载。后续提交按
§5 契约逐片加 `/api/pages`·`/api/page`·`/api/raw`（C2）、报告（C3）、`ingest`（C4）、`chat`（C5）。
"""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..check import format_report as _format_check
from ..check import run_check
from ..graph import build_and_write_graph
from ..health import format_report as _format_health
from ..health import run_health
from ..ingest import run_ingest
from ..lint import format_report as _format_lint
from ..lint import run_lint
from ..pages import iter_pages, load_page, page_title, page_type
from ..runtime import AgentRunner
from .chat import ConversationStore
from .jobs import JobQueue
from .render import render_markdown, render_page


class IngestBody(BaseModel):
    """`POST /api/ingest` 请求体。`target` 仍由 run_ingest 内部 _resolve_raw_target 兜底校验。"""

    target: str
    model: str | None = None


class ChatBody(BaseModel):
    """`POST /api/chat` 请求体。省略 `conversation_id` → 新建会话（一次性问答=单轮）。"""

    message: str
    conversation_id: str | None = None
    model: str | None = None


def _sse(kind: str, data: object) -> str:
    """编码一个 SSE 事件帧（`event:`/`data:` + 空行）。data 一律 JSON（ensure_ascii=False）。"""
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

# 随包前端静态资源目录（guanlan/web/static/，随 packages 自动入 wheel，见 pyproject）。
STATIC_DIR = Path(__file__).parent / "static"


def _safe_wiki_file(root: Path, rel: str) -> Path:
    """把请求 `path` 解析为 `wiki/` 内存在的文件；越界 → 409、不存在 → 404（路径穿越防御）。

    `rel` 是相对知识库根的 posix 路径（如 `wiki/entities/Foo.md`，与 `/api/pages` 回传一致）。
    绝对路径 / `..` 越界经 `resolve()` + `relative_to(wiki)` 拦下（决策P4-4 / §8）。
    """
    wiki = (root / "wiki").resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(wiki)
    except ValueError:
        raise HTTPException(status_code=409, detail=f"路径越界（须在 wiki/ 内）：{rel}") from None
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"页面不存在：{rel}")
    return candidate


def _list_pages(root: Path) -> list[dict]:
    """非 config 页清单（排除 config 由 `iter_pages` 兜底，与 check/graph 同口径）。"""
    wiki = root / "wiki"
    pages: list[dict] = []
    for path in iter_pages(wiki):
        meta, _body = load_page(path)  # 容错档：坏 frontmatter 不抛。
        pages.append(
            {
                "path": path.relative_to(root).as_posix(),
                "title": page_title(meta, path.stem),
                "type": page_type(meta),
            }
        )
    return pages


def _report_response(json_text: str) -> Response:
    """把既有序列化器输出的 JSON 文本**原样**作为响应体。

    红线（决策P4-7 / §11）：必须复用 `format_report(report, json_output=True)`（底层 `report_json`
    是 `ensure_ascii=False, indent=2`、**无尾换行**），并以 `media_type` 直发——绝不返回 dict /
    默认 `JSONResponse`（那会变 compact + `ensure_ascii=True`，与 CLI `--json` 字节不等）。
    """
    return Response(content=json_text, media_type="application/json")


def _list_raw(root: Path) -> list[dict]:
    """列 `raw/*.md`（**只列、不经 Web 写 raw**，§1 / 决策P4-1）。"""
    raw = root / "raw"
    files: list[dict] = []
    for path in sorted(raw.glob("*.md")):
        if path.is_file():
            files.append({"name": path.name, "size": path.stat().st_size})
    return files


def create_app(
    root: Path,
    *,
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> FastAPI:
    """构造绑定到知识库 `root` 的 FastAPI app。

    Args:
        root: 已 `require_kb_root(writable=True)` 校验过的知识库根（绝对路径）。
        model: `--model` 透传给写作业（C4）与会话嵌入（C5）；None 表示不覆盖、由环境发现。
        runner: 可注入的 `AgentRunner`（测试用 fake，不打真实 LLM）；None 走默认子进程 runner。
    """
    app = FastAPI(title="观澜 Web 宿主", docs_url=None, redoc_url=None)
    # 配置挂在 app.state：后续提交的端点（报告/写作业/会话）从这里读 root/model/runner，
    # 避免在模块级全局变量上分裂状态（与 workers=1 单进程单事件循环假设一致，决策P4-2）。
    app.state.root = root
    app.state.model = model
    app.state.runner = runner
    # 单写者作业队列（唯一写作业 ingest 走它，FIFO 串行；决策P4-5）。
    jobs = JobQueue()
    app.state.jobs = jobs
    # 内存会话表（所有问答走只读嵌入，进程退出即清；决策P4-8）。
    conversations = ConversationStore(root, model)
    app.state.conversations = conversations

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # 浏览（读）：阻塞的文件扫描/渲染一律经 anyio.to_thread 卸离事件循环（决策P4-2）。
    @app.get("/api/pages")
    async def api_pages() -> dict:
        return {"pages": await anyio.to_thread.run_sync(_list_pages, root)}

    @app.get("/api/page")
    async def api_page(path: str = Query(..., description="相对知识库根的页面路径")) -> dict:
        page_file = _safe_wiki_file(root, path)  # 同步、廉价；越界/缺失即抛 HTTPException。
        return await anyio.to_thread.run_sync(render_page, root / "wiki", page_file)

    @app.get("/api/raw")
    async def api_raw() -> dict:
        return {"files": await anyio.to_thread.run_sync(_list_raw, root)}

    # 零 LLM 报告（决策P4-7）：只序列化既有 *Report，与 CLI `--json` 字节对齐；阻塞跑 to_thread。
    wiki = root / "wiki"

    # 名 → (跑报告, 序列化器)。三报告同形（跑 wiki → format(..., json_output=True) 字节对齐 CLI），
    # 表驱动避免三份 copy-paste 漂移（如某个漏 json_output=True）。health 的 strict 只影响 CLI
    # 退出码、不改 JSON 体（ok 已反映有无 findings），故 Web 不收该参。
    _reports = {
        "check": (run_check, _format_check),
        "health": (run_health, _format_health),
        "lint": (run_lint, _format_lint),
    }

    @app.get("/api/report/{name}")
    async def report(name: str) -> Response:
        entry = _reports.get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"未知报告：{name}")
        run_fn, format_fn = entry
        result = await anyio.to_thread.run_sync(run_fn, wiki)
        return _report_response(format_fn(result, json_output=True))

    # graph：构建（写派生 graph/）后 302 到自包含静态视图。json_only 时改跳 graph.json。
    @app.get("/graph")
    async def graph(json_only: bool = False) -> RedirectResponse:
        # 用无打印的 build_and_write_graph（非 graph_entrypoint）：worker 的进程级 redirect_stdout
        # 期间不引入并发打印者（决策P4-5 红线）。写失败 → 500，别 302 到缺失文件让用户撞 404。
        try:
            await anyio.to_thread.run_sync(
                functools.partial(build_and_write_graph, root, json_only=json_only)
            )
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"graph 构建失败：{exc}") from exc
        target = "/graph/graph.json" if json_only else "/graph/graph.html"
        return RedirectResponse(url=target, status_code=302)

    # 文件名 → media_type 白名单：既去掉两份 copy-paste，又把 {filename} 限死在这两项、杜绝穿越。
    _graph_files = {"graph.html": "text/html", "graph.json": "application/json"}

    @app.get("/graph/{filename}")
    async def graph_file(filename: str) -> FileResponse:
        media_type = _graph_files.get(filename)
        if media_type is None:
            raise HTTPException(status_code=404, detail=f"未知 graph 文件：{filename}")
        path = root / "graph" / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{filename} 尚未生成，请先 GET /graph")
        return FileResponse(path, media_type=media_type)

    # 写（唯一写入口 = ingest）：即时入队、立刻返回 job_id；前端轮询 /api/jobs/{id}（无 SSE）。
    @app.post("/api/ingest")
    async def ingest(body: IngestBody) -> dict:
        # target 不在此预校验：run_ingest 内部 _resolve_raw_target 是单一归口（须在 raw/、是 .md、
        # 存在），Web 不旁路 P2 入口校验；非法 target → 作业以 EXIT_USAGE 完成，轮询可见。
        def _job() -> int:
            return run_ingest(
                body.target,
                root=root,
                model=body.model or model,
                runner=runner,
            )

        return {"job_id": jobs.enqueue("ingest", _job)}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str) -> dict:
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"未知作业：{job_id}")
        return {
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "exit_code": job.exit_code,
            "output": job.output,
        }

    # 问答 / 多轮会话（只读嵌入，决策P4-8）：POST 直接返回 text/event-stream，前端 fetch 读 body。
    @app.post("/api/chat")
    async def chat(body: ChatBody) -> StreamingResponse:
        if body.conversation_id is None:
            try:
                conv = await anyio.to_thread.run_sync(
                    functools.partial(conversations.create, body.model)
                )
            except RuntimeError as exc:  # 会话数达上限
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        else:
            conv = conversations.get(body.conversation_id)
            if conv is None:
                raise HTTPException(status_code=404, detail=f"未知会话：{body.conversation_id}")

        queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()

        def emit(kind: str, data: object) -> None:
            queue.put_nowait((kind, data))

        async def _run_turn() -> None:
            try:
                # shield：客户端断开会取消本任务，但**不可**中途打断 arun——agentao 的 arun 在
                # 取消时只转发 token.cancel() 便立刻 re-raise，不等线程收尾，于是 lock 会在后台
                # executor 线程仍在跑时被释放，下一轮就可能与残线程并发改 agent.messages / 串错
                # token。shield 让 turn 始终跑到自然结束（lock 全程持有），杜绝该竞态；代价是断开
                # 后该轮仍跑完（本地单用户、轮次有界，可接受）。
                answer = await asyncio.shield(conv.turn(body.message, emit))
                # 答案已完整流出；再渲染安全 markdown HTML（[[页]] → 站内链接）作收尾。渲染失败
                # **不能**丢掉这条成功答案：省略 answer_html，前端回退用纯文本 answer 上屏。
                payload: dict = {"answer": answer, "conversation_id": conv.id}
                try:
                    payload["answer_html"] = await anyio.to_thread.run_sync(
                        render_markdown, answer, root / "wiki"
                    )
                except Exception:  # noqa: BLE001 — 渲染失败仅降级排版，不毁答案、不转 error
                    pass
                emit("done", payload)
            except asyncio.CancelledError:
                raise  # 客户端已断开，流没了，不再 emit
            except Exception as exc:  # noqa: BLE001 — 任何失败都转 error 事件，不泄 traceback 到流
                # 带上 conversation_id：首轮失败时前端据此记住已建会话，避免下次另起新会话堆积。
                emit("error", {"message": f"{type(exc).__name__}: {exc}", "conversation_id": conv.id})
            finally:
                queue.put_nowait(None)  # 哨兵：通知流结束

        async def event_stream() -> AsyncIterator[str]:
            task = asyncio.create_task(_run_turn())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    kind, data = item
                    yield _sse(kind, data)
            finally:
                if not task.done():
                    task.cancel()  # 客户端断开 → 取消在飞的 turn
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/conversations")
    async def list_conversations() -> dict:
        return {"conversations": conversations.list()}

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str) -> dict:
        conv = conversations.get(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        # 先拿会话锁再删：等当前/断开后仍在 shield 跑完的 turn 收尾，避免 agent.close() 与在飞
        # 的 arun 抢同一 agent 资源（决策P4-8 只读会话单 agent 假设）。close() 可能阻塞 → to_thread。
        async with conv.lock:
            await anyio.to_thread.run_sync(conversations.delete, conversation_id)
        return {"deleted": conversation_id}

    return app
