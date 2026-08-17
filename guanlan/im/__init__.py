"""观澜 IM 宿主子包（P4.21，可选叠加层，见 docs/P4.21-IM宿主.md）。

P4「可选宿主层」的**第三种传输**：Web 是浏览器里的人、MCP 是 Agent 客户端，IM 是**聊天窗口
里的人**。三者共用**同一套只读会话逻辑**（`Conversation.turn()`）、**同一零写契约**、
**同一零业务智能**。

形态是「**一个平台无关的核心 + 若干薄适配器**」，不是「某一个平台的宿主」（决策P4.21-1）：
核心永不写 `if platform == "weixin"`，一切分派只读 `AdapterCaps`（决策P4.21-3）。

> 取 `serve_im` 会顺带导入会话层（`guanlan.web.chat`，已惰性化故不拉 fastapi）；平台 SDK
> 只在**真正建那个平台的适配器**时才导入，故缺 `[im-feishu]` 不妨碍跑微信（反之亦然）。

**卸线程通则（§4.3，决策P4.21-35）**：本包里一切同步的磁盘 / CPU / 阻塞网络路径都必须
`await anyio.to_thread.run_sync(...)`。判据不是"这个调用看起来慢不慢"，而是**"它会不会去拿
一把别人可能长期持有的锁"**——`ConversationStore.get()` 正是栽在这一条上。

**点击 = 一条合成的入站消息（P4.22 的枢纽，决策P4.22-2）**：答案里的 `[[引用]]` 在支持的平台上
会变成可点按钮，而按钮携带的是**核心生成的命令文本**（`"/page 甲实体"`）。适配器只负责把这句话
塞进按钮、回调时原样取回，并把它映射成一条 `origin="action"` 的 `InboundMessage` 投回**同一条
入站流**——于是五道闸原样复用、`server.py` 零改，核心也分不清（也不需要分清）这条消息是人打的
还是点出来的。唯一按来源分的事是那条只读命令白名单（`intake.ACTION_COMMANDS`）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 类型检查期可见；运行期走下面的惰性 __getattr__
    from .server import run_identify, run_login, serve_im

_LAZY = {"serve_im", "run_login", "run_identify"}


def __getattr__(name: str):
    if name in _LAZY:
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["run_identify", "run_login", "serve_im"]
