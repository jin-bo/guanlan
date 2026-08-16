"""Web 宿主的可配默认值与合法取值——**单一来源**，供 `serve` 的签名与 `cli.py` 的 argparse 共用。

与 `guanlan/im/defaults.py` 同一理由、同一形态（见那边的说明）：`cli.py` 必须在**建 parser 时**
读到这些值，而 `chat_support.py` / `server.py` 都拉得动 agentao 乃至 fastapi——后者更要命，
**缺 `[web]` extra 时 `guanlan web --help` 都不该崩**（`_cmd_web` 的优雅降级正是为此）。
故本模块**保持零 import**。

`confirm_timeout` 尤其值得收口：原先 `120.0` 在 `server.py` / `app.py` / `conversation_store.py`
/ `conversation.py` 四层签名 + cli 的 `default=` + help 文案里各写一遍，改一处等于什么也没改。
"""

from __future__ import annotations

DEFAULT_PORT = 8765  # 监听端口（仅 127.0.0.1；与 mcp 的 8766 错开）
DEFAULT_MAX_CONVERSATIONS = 100  # 内存会话硬上限（决策P4.9-18）
DEFAULT_CONFIRM_TIMEOUT = 120.0  # confirm/ask 等人应答的秒数（P4.15）；到点默认拒绝

# 新会话开局姿态：**绝不接受 full-access/plan**（决策P4.5-1）。cli 的 choices 与
# `Conversation` 的校验取同一份，避免「命令行收下了、运行期再 422」这种两头不一致。
WEB_MODES = ("read-only", "workspace-write")
DEFAULT_MODE = WEB_MODES[0]

# workspace-write 下 ASK 决策的处置（P4.15）。同上：cli 的 choices 与 app/conversation
# 两处运行期校验共用一份。
CONFIRM_MODES = ("ask", "auto")
DEFAULT_CONFIRM = CONFIRM_MODES[0]

__all__ = [
    "CONFIRM_MODES",
    "DEFAULT_CONFIRM",
    "DEFAULT_CONFIRM_TIMEOUT",
    "DEFAULT_MAX_CONVERSATIONS",
    "DEFAULT_MODE",
    "DEFAULT_PORT",
    "WEB_MODES",
]
