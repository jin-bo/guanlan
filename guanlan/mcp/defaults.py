"""MCP 宿主的可配默认值与合法取值——**单一来源**，供 `serve_mcp` 的签名与 `cli.py` 的 argparse 共用。

与 `guanlan/im/defaults.py`、`guanlan/web/defaults.py` 同一理由、同一形态：cli 要在**建 parser 时**
读到这些值，而 `guanlan/mcp/server.py` 会拉起官方 `mcp` SDK——**缺 `[mcp]` extra 时
`guanlan mcp --help` 都不该崩**（`_cmd_mcp` 那句"请先 pip install"的降级发生在解析之后）。
故本模块**保持零 import**，且 `guanlan/mcp/__init__.py` 为此改成了 PEP 562 惰性取名。
"""

from __future__ import annotations

# 传输：stdio 是默认，且**必须保持默认**——P4.17 引入 http 时的向后兼容硬约束（决策P4.17-1）。
TRANSPORTS = ("stdio", "http")
DEFAULT_TRANSPORT = TRANSPORTS[0]

# http 绑定地址：环回是默认。非环回须配 `--auth-token-env`（决策P4.17-2），那条校验在
# `server.py` 里，取的是"是不是环回"而不是"等不等于本常量"，故此处只管默认值。
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766  # 与 web 的 8765 错开（help 文案里那句话取的就是 web 的常量）

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "DEFAULT_TRANSPORT", "TRANSPORTS"]
