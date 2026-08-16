"""观澜 MCP 宿主子包（P4.10，可选叠加层，见 docs/P4.10-MCP宿主.md）。

P4「可选宿主层」的第二种传输：把只读核心搬进任意 MCP 客户端（stdio）。与 `guanlan/web/` 并列、
**同样零业务智能**——只把 `search`/`pages`/`graph`/`health`/`lint`/`runtime` 的只读能力包成 MCP 工具。

> 取 `serve_mcp`（经 `from .server import serve_mcp`）会触发官方 `mcp` SDK 导入；缺 `guanlan-wiki[mcp]`
> extra 时 `from guanlan.mcp import serve_mcp` 抛 `ImportError`，由 `guanlan/cli.py` 捕获并优雅引导安装
> （镜像 web 决策P4-2 / 决策P4.10-2）。

**PEP 562 惰性化**（与 `guanlan/web/__init__.py`、`guanlan/im/__init__.py` 同形）：`serve_mcp` 改为
在 `__getattr__` 里按需导入，使 `from guanlan.mcp.defaults import …` 这类**只取常量**的导入不再
顺带拉起 SDK——`cli.py` 建 parser 时正要读那几个默认值，而那时还没人保证装了 `[mcp]` extra。
`from guanlan.mcp import serve_mcp` 在缺 extra 时仍抛 `ImportError`（`_cmd_mcp` 的降级路径逐字不变）。

注：本子包名 `guanlan.mcp` 与官方 SDK 顶层包 `mcp` 不冲突——子模块里的 `from mcp.server.mcpserver ...`
是**绝对导入**，命中顶层 SDK；内部互引用一律相对导入（`.tools` / `..errors`）。底座为 SDK **v2**
（`mcp>=2,<3`；P4.18 从 v1 `mcp.server.fastmcp` 迁来，见 docs/P4.18-MCP2.0迁移.md）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 类型检查期照旧可见（运行期不导入，见模块 docstring）
    from .server import serve_mcp


def __getattr__(name: str):
    """按需导入 `serve_mcp`（PEP 562）：只有真取这个名字才拉官方 mcp SDK。"""
    if name == "serve_mcp":
        from .server import serve_mcp

        return serve_mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["serve_mcp"]
