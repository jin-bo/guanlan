"""FastAPI app + 路由（P4，见 docs/P4-Web宿主.md §4/§5）。

宿主自身**不做任何确定性/语义判断**：端点只负责"收 HTTP 请求 → 调既有包内函数或嵌入
agent → 序列化/转发结果"。阻塞调用一律卸到线程（零 LLM 报告经 `anyio.to_thread.run_sync`、
写作业 `ingest` 经单 worker 线程），绝不在事件循环里直接跑阻塞代码（决策P4-2）。

C1 仅落地骨架：`GET /` 返回随包 `index.html` + `/static/*` 静态挂载。后续提交按
§5 契约逐片加 `/api/pages`·`/api/page`·`/api/raw`（C2）、报告（C3）、`ingest`（C4）、`chat`（C5）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..runtime import AgentRunner

# 随包前端静态资源目录（guanlan/web/static/，由 wheel artifacts 携带，见 pyproject）。
STATIC_DIR = Path(__file__).parent / "static"


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

    return app
