"""问答 / 多轮会话 = 只读进程内嵌入（P4，见 docs/P4-Web宿主.md §4.4 决策P4-8）。

已核对 agentao 源码：`agentao run` 子进程无 session resume，多轮**只能**靠在内存里持有一个
`Agentao` 对象、反复 `arun()` 让 `self.messages` 跨轮累积。一次性提问 = "新建会话只跑一轮"。
用 agentao **文档化的嵌入面**（`build_from_environment` + `.arun` + 构造期 `transport`），
不碰内部 API，逐一化解 P2 §4.3 嵌入四坑。

四坑化解：① 凭据缺失 → `build_from_environment`（读 `.env`/`~/.agentao`）；② skill 不自动激活
→ 构造后显式激活 `guanlan-wiki`（且构造前 `ensure_skill_available` 保其可发现）；③ `<wd>/
agentao.log` 由**我们自己的 `logger=`** 接管（嵌入契约：注入 logger = 宿主自管日志栈，
LLMClient 不再自挂 handler）——默认经 `configure_agent_log` 给这个共享 logger 挂**一个**
`RotatingFileHandler` 落 `<wd>/agentao.log`（**像 CLI 一样**记会话日志，`guanlan web
--no-agent-log` 可关）；④ 内部 API 耦合 → 只用 `build_from_environment`/`.arun`/`transport`
这一组面。

只读姿态在构造后**两点同步置位**（照搬 `cli/run.py`）：engine 的 read-only 预设为空，真正拦截
写/shell 的是 `ToolRunner.readonly_mode`，少调第二步就不是真只读。

**模块组织（P4 拆分）**：会话本体析出到 `conversation.py`（`Conversation`）/
`conversation_store.py`（`ConversationStore`），共享底座（常量/logger/helper/召回工具工厂）在
`chat_support.py`。本模块现是**第三方接缝 + 再导出 facade**：它持有 `build_from_environment` /
`ensure_skill_available` / `run_check` / `save_session` / `load_session` 这些**测试以
`monkeypatch.setattr(chat, …)` 猴补、且被两会话类经 `chat.<name>` 在运行期取用**的名字，使既有
猴补与 `from .chat import …` 公开面（app.py / server.py / 测试）逐字不变。
"""

from __future__ import annotations

from pathlib import Path  # noqa: F401 — 保留 `chat.Path`（测试经 `chat_mod.Path.unlink` 猴补）

# —— 第三方接缝：被两会话类经 `chat.<name>` 取用、被测试在本模块猴补（故必须在此具名导入） ——
from agentao.embedding import build_from_environment, load_session, save_session  # noqa: F401

from ..check import run_check  # noqa: F401
from ..skill import ensure_skill_available  # noqa: F401

# —— 共享底座再导出：仅保留**确有外部 `chat.<name>` 访问点**的名字（app.py / server.py / 测试）——
# `_logger` / `_agent_log_paths` 被测试读取，故须经本模块可见；其余纯内部 helper（`_violation_key`/
# `_blocked_in_mode`/… ）由 conversation*.py 直接 `from .chat_support import` 取用、无人经 `chat.X` 访问，
# 不在此再导出（避免误导性的“为再导出而 import”死重）。
from .chat_support import (  # noqa: F401
    IDLE_TTL_SECONDS,
    MAX_CONVERSATIONS,
    _agent_log_paths,
    _logger,
    configure_agent_log,
    disable_agent_log,
    make_guanlan_search_tool,
)

# —— facade：会话类经本模块再导出，保 `chat.Conversation` / `from .chat import ConversationStore`
# （app.py / 测试）不变。置于文件末尾、避开与两会话类「末尾 from . import chat」的循环导入。 ——
from .conversation import Conversation  # noqa: E402,F401
from .conversation_store import ConversationStore  # noqa: E402,F401

__all__ = [
    "Conversation",
    "ConversationStore",
    "Emit",
    "IDLE_TTL_SECONDS",
    "MAX_CONVERSATIONS",
    "build_from_environment",
    "configure_agent_log",
    "disable_agent_log",
    "ensure_skill_available",
    "load_session",
    "make_guanlan_search_tool",
    "run_check",
    "save_session",
]
