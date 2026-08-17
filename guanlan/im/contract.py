"""IM 适配器契约：`AdapterCaps` / `InboundMessage` / `OutboundRef` / `Action` / `IMAdapter`（P4.21 §3.2）。

**本模块是整个 IM 宿主里唯一的"平台相关面"**，且它只描述形状、不含任何平台代码——
无第三方依赖（不 import httpx / lark-oapi），故核心与测试可零 extra 导入。

核心永不写 `if platform == "weixin"`（决策P4.21-3）：一切分派只读 `caps`。
新增平台 = 实现 `IMAdapter` + 填一份 `AdapterCaps` + 在 `adapters/__init__.py` 注册一行，核心零改。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# `msg_kind` 的两个取值（§3.2 / §5.1 第 5 道闸）。适配器只做**事实映射**、绝不自行回复；
# "见到附件回一句固定提示"是**核心**行为，故可用 FakeAdapter 决定性地测到（决策P4.21-36）。
KIND_TEXT = "text"
KIND_OTHER = "other"

# `chat_type` 的两个取值。个人微信恒 `"dm"`（群消息在适配器层就丢了，§7.2.1）。
CHAT_DM = "dm"
CHAT_GROUP = "group"

# `origin` 的两个取值（P4.22 §5.5，决策P4.22-14）。**这是事实字段，不是策略字段**：
# 适配器只回答"这条消息是人打出来的还是点按钮点出来的"，**不判断**点出来的能干什么——
# 那条只读白名单（`intake.ACTION_COMMANDS`）留在核心。
ORIGIN_USER = "user"
ORIGIN_ACTION = "action"


@dataclass(frozen=True)
class AdapterCaps:
    """一个平台的能力位 + 频控参数——**核心的唯一分派依据**（决策P4.21-3）。

    `max_parts` 是长答案契约的**有界化参数**（评审 #4）：`max_parts × max_message_length`
    之外的内容会被显式截断告示替代，绝不静默丢弃（§6.3）。
    """

    max_message_length: int
    max_parts: int  # 单次回答最多几片（末片可能是截断告示，§6.3）
    supports_edit: bool
    supports_typing: bool
    supports_late_push: bool
    supports_group: bool
    chunk_delay_s: float  # 分片之间的间隔（防频控）
    batch_delay_s: float  # 入站分片合并的静默期
    batch_split_delay_s: float  # 上一片接近上限时的加长静默期
    # ── P4.22：可点引用 ──────────────────────────────────────────────────
    # **默认关**：新增能力位一律默认关，使既有平台（与既有测试夹具）行为零变化——
    # 一个"默认开"的能力位等于要求每个适配器都记得关掉它，漏一个就是运行期 AttributeError。
    supports_actions: bool = False  # 出站能否携带可点动作（飞书卡片按钮）
    max_actions: int = 0  # 一次最多几个按钮；超出部分**显式告示**、不静默丢（决策P4.21-31）


@dataclass(frozen=True)
class Action:
    """一个可点动作（P4.22 §5.1，★ 决策P4.22-2）。

    `command` 是**核心生成的入站正文**（`"/page 甲实体"`），不是"页面名"也不是"动作类型"：
    适配器只负责把这句话塞进按钮、并在回调时**原样取回**，它**不知道 `/page` 是什么**。
    这条边界一划，新增动作类型（将来的 `/graph`、`/related`）适配器零改，且核心不必为
    "点击"新开一条处理路径——点击最终变成一条与用户手打**无法区分**的入站消息。
    """

    label: str  # 按钮上显示的字
    command: str  # 点击后要合成的入站正文


@dataclass(frozen=True)
class InboundMessage:
    """一条入站消息的**平台无关**表示（映射规则见 §7.2.1 / §8.3 / P4.22 §5.2）。"""

    tenant: str  # account_id / tenant_key：防跨租户撞号
    chat_id: str
    chat_type: str  # CHAT_DM | CHAT_GROUP
    user_id: str
    text: str  # msg_kind != "text" 时可能为空
    msg_id: str
    mentioned_me: bool  # 群内是否 @ 了机器人（**平台结构化字段**，绝不靠文本猜）
    msg_kind: str  # KIND_TEXT | KIND_OTHER
    has_attachments: bool
    # P4.22：ORIGIN_USER（人打的）| ORIGIN_ACTION（点按钮点出来的合成消息）。
    # **默认 `user`**：只有明确知道自己在合成消息的适配器才填 `action`，故忘了填 = 更严
    # （走完整的五道闸 + LLM 分流），而不是更松。
    origin: str = ORIGIN_USER


@dataclass(frozen=True)
class OutboundRef:
    """一条已发出的消息的定位信息。不支持 edit 的适配器把 `message_id` 回空串。"""

    chat_id: str
    message_id: str


@runtime_checkable
class IMAdapter(Protocol):
    """薄适配器契约：连接 / 收流 / 发消息，**零业务判断**。

    **`inbound()` 的可取消义务（评审 #29，决策P4.21-56）**：刻意**不**给 Protocol 加
    `stop_receiving()`——那会把停机责任摊到每个适配器头上，而它们要做的事完全一样。改为一条
    **义务**：`inbound()` 必须**只阻塞在 I/O 等待上**（socket 读 / `asyncio.Queue.get`），使外部
    `task.cancel()` 即可让它干净结束；**取消点上不得留下半提交的状态**（如"已推进 offset
    但事件还没交出去"）。停机时宿主取消收流 task，随后才 `close()` 适配器（§4.4）。

    > 微信长轮询被取消在 `getupdates` 半途 → 该批事件的 offset 未推进 → 下次启动会重投。
    > 这是可接受的 at-least-once：它们本来就没被回答过。
    """

    name: str
    caps: AdapterCaps

    async def start(self) -> None:
        """建立连接 / 校验凭据。抛错 → 宿主 `close()` 兜底并以 `EXIT_AGENT_ERROR` 退出（§4.1）。"""
        ...

    async def close(self) -> None:
        """拆连接。宿主保证它在**所有 turn 真正结束之后**才被调用（§4.4 步骤⑥）。"""
        ...

    def inbound(self) -> AsyncIterator[InboundMessage]:
        """入站事件流。见上方"可取消义务"。"""
        ...

    async def send(self, chat_id: str, text: str) -> OutboundRef: ...

    async def edit(self, ref: OutboundRef, text: str, *, finalize: bool = False) -> None:
        """原地改写已发出的消息（仅 `supports_edit` 的平台会被调用）。"""
        ...

    async def typing(self, chat_id: str, on: bool) -> None:
        """打字指示（仅 `supports_typing` 的平台会被调用）。"""
        ...

    async def send_actions(self, chat_id: str, text: str, actions: list[Action]) -> None:
        """发一条**带可点动作**的消息（仅 `supports_actions` 的平台会被调用，P4.22 §5）。

        与 `typing()` **完全对称**：能力位为 False 的平台永不被调到，故那些平台里这个方法
        抛 `NotImplementedError` 即可——它是"契约被违反时立刻炸"，不是运行期路径。
        """
        ...
