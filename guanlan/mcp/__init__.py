"""观澜 MCP 宿主子包（P4.10，可选叠加层，见 docs/P4.10-MCP宿主.md）。

P4「可选宿主层」的第二种传输：把只读核心搬进任意 MCP 客户端（stdio）。与 `guanlan/web/` 并列、
**同样零业务智能**——只把 `search`/`pages`/`graph`/`health`/`lint`/`runtime` 的只读能力包成 MCP 工具。

> 导入本模块（经 `from .server import serve_mcp`）会触发官方 `mcp` SDK 导入；缺 `guanlan-wiki[mcp]`
> extra 时 `from guanlan.mcp import serve_mcp` 抛 `ImportError`，由 `guanlan/cli.py` 捕获并优雅引导安装
> （镜像 web 决策P4-2 / 决策P4.10-2）。

注：本子包名 `guanlan.mcp` 与官方 SDK 顶层包 `mcp` 不冲突——子模块里的 `from mcp.server.fastmcp ...`
是**绝对导入**，命中顶层 SDK；内部互引用一律相对导入（`.tools` / `..errors`）。
"""

from __future__ import annotations

from .server import serve_mcp

__all__ = ["serve_mcp"]
