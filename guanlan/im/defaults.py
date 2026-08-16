"""IM 宿主的可配默认值——**单一来源**，供 `serve_im` 的签名与 `cli.py` 的 argparse 共用。

为什么单独开一个模块，而不是就放在 `server.py` 里：`cli.py` 要在**建 parser 时**读到这些值
（`default=` 与 help 文案都取它），而 `import guanlan.im.server` 会拉起 agentao + anyio
（实测 +53 ms、模块数 177 → 242）——`guanlan check` 这类零 LLM 命令没有理由为 IM 付这笔钱。
`session.py` 同样不行（它 `import anyio.to_thread`）。

所以本模块**保持零 import**：它是叶子，谁都可以廉价地取。

不这么做的代价不是抽象的洁癖：常量若只作 `serve_im()` 的签名默认、而 cli 各抄一份字面量，
那么 CLI 用户**永远**拿的是抄的那份——改常量对他们毫无效果，help 文案还会跟着漂，
且没有任何测试会红。参 `heal` / `audit` 的既有惯例（`default=DEFAULT_LIMIT` + f-string help）。
"""

from __future__ import annotations

DEFAULT_MAX_CONVERSATIONS = 100  # 内存会话硬上限
DEFAULT_IDLE_TTL = 1800.0  # 会话闲置多久算过期（秒）＝ 30 分钟
DEFAULT_MCP_REQUEST_TIMEOUT = 120.0  # 外部 MCP 等一次 tools/call 回话的秒数上界（§4.7）
DEFAULT_IDENTIFY_SECONDS = 300.0  # im-identify 的收集窗口（秒）

__all__ = [
    "DEFAULT_IDENTIFY_SECONDS",
    "DEFAULT_IDLE_TTL",
    "DEFAULT_MAX_CONVERSATIONS",
    "DEFAULT_MCP_REQUEST_TIMEOUT",
]
