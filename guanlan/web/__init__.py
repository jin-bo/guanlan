"""观澜 Web 宿主子包（P4，可选叠加层，见 docs/P4-Web宿主.md）。

本子包是 MVP 之后的**可选** Web 入口：不装 `guanlan-wiki[web]`、不起 `guanlan web`，
整套东西照旧用 CLI 跑通（DESIGN §1）。它**不承载业务智能**——只把 P2/P3 已交付的命令
搬进浏览器：浏览只读、零 LLM 报告复用既有 `*Report`/`Graph` 序列化、写仅经 `ingest`
单 worker 串行（P2 门禁不动）、问答走只读进程内嵌入。

> 取 `serve`（经 `from .server import serve`）会触发 `fastapi`/`uvicorn` 导入；缺 web extra
> 时 `from guanlan.web import serve` 抛 `ImportError`，由 `guanlan/cli.py` 捕获并优雅引导安装。

**PEP 562 惰性化（P4.21 §3.0，决策P4.21-4）**：`serve` 改为在 `__getattr__` 里按需导入，使
`from guanlan.web.chat import Conversation` 这类**只用会话层**的导入不再顺带拉起 uvicorn/fastapi
——IM 宿主复用同一套 `Conversation`/`ConversationStore`，但只装 `[im-weixin]`/`[im-feishu]`
的环境里没有 web extra。会话层本体（`conversation.py`/`conversation_store.py`/`chat_support.py`/
`jobs.py`/`policy_fs.py`/`goal_io.py`）已核对**不 import fastapi/starlette**，故这一行改动就够。
`from guanlan.web import serve` 在缺 extra 时仍抛 `ImportError`（`_cmd_web` 的降级路径逐字不变）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 类型检查期照旧可见（运行期不导入，见模块 docstring）
    from .server import serve


def __getattr__(name: str):
    """按需导入 `serve`（PEP 562）：只有真取这个名字才拉 fastapi/uvicorn。"""
    if name == "serve":
        from .server import serve

        return serve
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["serve"]
