"""FastAPI app + 路由（P4，见 docs/P4-Web宿主.md §4/§5）。

宿主自身**不做任何确定性/语义判断**：端点只负责"收 HTTP 请求 → 调既有包内函数或嵌入
agent → 序列化/转发结果"。阻塞调用一律卸到线程（零 LLM 报告经 `anyio.to_thread.run_sync`、
写作业 `ingest` 经单 worker 线程），绝不在事件循环里直接跑阻塞代码（决策P4-2）。

C1 仅落地骨架：`GET /` 返回随包 `index.html` + `/static/*` 静态挂载。后续提交按
§5 契约逐片加 `/api/pages`·`/api/page`·`/api/raw`（C2）、报告（C3）、`ingest`（C4）、`chat`（C5）。
"""

from __future__ import annotations

import functools
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
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
from .jobs import JobQueue
from .render import render_page


class IngestBody(BaseModel):
    """`POST /api/ingest` 请求体。`target` 仍由 run_ingest 内部 _resolve_raw_target 兜底校验。"""

    target: str
    model: str | None = None

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

    @app.get("/api/report/check")
    async def report_check() -> Response:
        result = await anyio.to_thread.run_sync(run_check, wiki)
        return _report_response(_format_check(result, json_output=True))

    @app.get("/api/report/health")
    async def report_health() -> Response:
        # strict 只影响 CLI 退出码，不改 JSON 体（ok 已反映有无 findings），故 Web 不收该参。
        report = await anyio.to_thread.run_sync(run_health, wiki)
        return _report_response(_format_health(report, json_output=True))

    @app.get("/api/report/lint")
    async def report_lint() -> Response:
        report = await anyio.to_thread.run_sync(run_lint, wiki)
        return _report_response(_format_lint(report, json_output=True))

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

    @app.get("/graph/graph.html")
    async def graph_html() -> FileResponse:
        path = root / "graph" / "graph.html"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="graph.html 尚未生成，请先 GET /graph")
        return FileResponse(path, media_type="text/html")

    @app.get("/graph/graph.json")
    async def graph_json() -> FileResponse:
        path = root / "graph" / "graph.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="graph.json 尚未生成，请先 GET /graph")
        return FileResponse(path, media_type="application/json")

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

    return app
