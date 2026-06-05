"""FastAPI app + 路由（P4，见 docs/P4-Web宿主.md §4/§5）。

宿主自身**不做任何确定性/语义判断**：端点只负责"收 HTTP 请求 → 调既有包内函数或嵌入
agent → 序列化/转发结果"。阻塞调用一律卸到线程（零 LLM 报告经 `anyio.to_thread.run_sync`、
写作业 `ingest` 经单 worker 线程），绝不在事件循环里直接跑阻塞代码（决策P4-2）。

C1 仅落地骨架：`GET /` 返回随包 `index.html` + `/static/*` 静态挂载。后续提交按
§5 契约逐片加 `/api/pages`·`/api/page`·`/api/raw`（C2）、报告（C3）、`ingest`（C4）、`chat`（C5）。
"""

from __future__ import annotations

from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..pages import iter_pages, load_page, page_title, page_type
from ..runtime import AgentRunner
from .render import render_page

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

    return app
