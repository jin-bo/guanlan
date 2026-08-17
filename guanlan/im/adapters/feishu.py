"""适配器 B：飞书 / Lark（官方 `lark-oapi` SDK，WS 长连，P4.21 §8）。

**必传 `extra_ua_tags=["channel"]`（决策P4.21-29）**：不传这个 tag，飞书服务端**不会**经
WebSocket 推送群 @ 事件（只推 P2P 单聊）。该参数自 `lark-oapi` **1.6.8** 起才有——故 extra 下限
必须 `>=1.6.8`，且启动时先用 `inspect.signature` 探针，缺则拒启并明示升级。
旧版本装上去会 `TypeError: unexpected keyword argument`，**这比静默收不到群消息好**。

`lark-oapi` 是**同步 SDK**：WS 客户端跑自己的线程、回调在 SDK 线程触发；HTTP 调用也是阻塞的。故：

- **入站**：SDK 回调里 `loop.call_soon_threadsafe(queue.put_nowait, inbound)`，`inbound()` 从
  `asyncio.Queue` 取——**绝不**在 SDK 线程里直接碰事件循环对象。
- **出站**：`await anyio.to_thread.run_sync(...)`（§4.3 卸线程通则）。
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from pathlib import Path

import anyio.to_thread

from ...errors import EXIT_USAGE, GuanlanError
from ..contract import (
    CHAT_DM,
    CHAT_GROUP,
    KIND_OTHER,
    KIND_TEXT,
    ORIGIN_ACTION,
    Action,
    AdapterCaps,
    InboundMessage,
    OutboundRef,
)
from ..reply import FENCE_LINE

_logger = logging.getLogger("guanlan.im")

ENV_APP_ID = "GUANLAN_IM_FEISHU_APP_ID"
ENV_APP_SECRET = "GUANLAN_IM_FEISHU_APP_SECRET"
ENV_DOMAIN = "GUANLAN_IM_FEISHU_DOMAIN"  # feishu | lark，默认 feishu
ENV_BOT_OPEN_ID = "GUANLAN_IM_FEISHU_BOT_OPEN_ID"  # 已**不再必填**（§8.2），仅作探测失败的回退

MIN_SDK_VERSION = "1.6.8"
BOT_INFO_URI = "/open-apis/bot/v3/info"

# CARD 帧兼容层的**同任务**传递通道（`_card_aware_ws_client`：改写帧头 → 写出回执前还原）。
# 用 `ContextVar` 而不是实例属性：帧派发是并发的（每帧一个 task），实例属性会串台——两个回调
# 同时在飞时，后进的会把前一个的帧对象覆盖掉，还原到错的帧上。ContextVar 天然跟着 task 走。
_CARD_FRAME: ContextVar[tuple | None] = ContextVar("guanlan_feishu_card_frame", default=None)

CAPS = AdapterCaps(
    max_message_length=8000,
    max_parts=3,  # 8000×3 ≈ 2.4 万字
    supports_edit=True,  # im.v1.message.update
    # 飞书的「处理中」要用 reaction 表情，与 typing 语义不同，v1 不做——它有 edit 档，
    # 不需要 typing 兜底。
    supports_typing=False,
    supports_late_push=True,  # 无窗口限制
    supports_group=True,  # 须 channel UA tag（见模块 docstring）
    chunk_delay_s=0.3,
    batch_delay_s=1.0,
    batch_split_delay_s=2.0,
    supports_actions=True,  # P4.22：卡片 2.0 回传交互按钮（缺 SDK 注册面时**就地降级**）
    max_actions=8,
)

# ── P4.22 可点引用 ───────────────────────────────────────────────────────────
# 回传信封的**卫生上界**（决策P4.22-18）：量的是**整条命令串**，与"页面名多长算合法"无关——
# 后者是命令语义，归核心那份唯一的 `pageview.validate_page_arg`。两处各量一次"≤200"的下场是：
# 页面名落在 194–200 字符时**手打能开、点按钮被丢**。这里只挡畸形/超大 `value`。
MAX_CALLBACK_COMMAND = 256
# 会话事实缓存（★ 决策P4.22-5）的容量。溢出即逐出最老的会话 ⇒ 那些会话里的旧卡片按钮失效，
# 用户看到「会话已过期，请重新提问」——**这是可接受的失效，不是可接受的放行**。
CHAT_CACHE_MAX = 4096

TOAST_ACK = "已收到"
TOAST_EXPIRED = "会话已过期，请重新提问"
TOAST_BAD = "这个按钮无法识别"
CARD_HINT = (
    "已发出可点引用卡片。若点击无响应，请检查开发者后台「事件与回调 → 回调」的订阅方式"
    "是否为长连接（与「事件订阅」是**两套**配置，漏配则卡片发得出、点了没反应、无日志）。"
)

# 「一条 WS 长连已经死了」的哨兵（见 `_ws_runner`）。放队列里传，使 `inbound()` 能把它变成异常。
_WS_DEAD = object()

# 首连看门狗的等待上界（秒）。正常情形下「端点发现 POST + WS 握手」两秒内就完成，故这个窗口
# 只在**出事时**才走满。取值要能容下慢网络的一次握手，又远小于 SDK 那个 120 秒的重连间隔。
WS_FIRST_CONNECT_TIMEOUT = 30.0
_WS_POLL_INTERVAL = 0.1


def build_markdown_post_rows(text: str) -> list[list[dict]]:
    """按**围栏块**切行（§8.4 的唯一真实逻辑）。

    飞书**不直接渲染 markdown**，但 `post` 的 `md` 元素吃裸 markdown——转换几乎免费。
    唯一的坑：飞书的 `md` 渲染器在**一个大元素里含围栏块时会吞掉围栏之后的内容**。故在真围栏行
    处切开，**围栏块独占一 row**，前后散文各成一 row。观澜的答案经常带围栏块
    （代码 / mermaid / flint），**这个坑必踩**。
    """
    rows: list[list[dict]] = []
    buf: list[str] = []
    fence: list[str] = []
    in_fence = False

    def _flush(lines: list[str]) -> None:
        body = "\n".join(lines).strip("\n")
        if body:
            rows.append([{"tag": "md", "text": body}])

    for line in text.split("\n"):
        if FENCE_LINE.match(line):
            if in_fence:
                fence.append(line)
                _flush(fence)
                fence = []
                in_fence = False
            else:
                _flush(buf)
                buf = []
                fence = [line]
                in_fence = True
            continue
        (fence if in_fence else buf).append(line)
    _flush(fence if in_fence else buf)  # 未闭合的围栏也原样送出（保留源码，绝不静默丢弃）
    return rows or [[{"tag": "md", "text": text}]]


def post_content(text: str) -> str:
    return json.dumps(
        {"zh_cn": {"content": build_markdown_post_rows(text)}}, ensure_ascii=False
    )


def text_content(text: str) -> str:
    return json.dumps({"text": text}, ensure_ascii=False)


def map_event(event: object, *, bot_open_id: str) -> InboundMessage | None:
    """飞书事件 → `InboundMessage`（§8.3）。

    | `InboundMessage` | 飞书事件字段 |
    |---|---|
    | `tenant` | `header.tenant_key` |
    | `chat_id` | `event.message.chat_id` |
    | `chat_type` | `chat_type == "p2p"` → `"dm"`，否则 `"group"` |
    | `user_id` | `event.sender.sender_id.open_id` |
    | `text` | `json.loads(event.message.content)["text"]`（仅 `msg_type == "text"`） |
    | `msg_id` | `event.message.message_id` |
    | `mentioned_me` | `event.message.mentions[].id.open_id == <bot_open_id>` ← **结构化字段** |

    `mentioned_me` **绝不靠文本猜**：正文里写「@机器人」四个字不算 @。
    """
    header = getattr(event, "header", None)
    body = getattr(event, "event", None)
    message = getattr(body, "message", None)
    if message is None:
        return None
    msg_id = str(getattr(message, "message_id", "") or "")
    chat_id = str(getattr(message, "chat_id", "") or "")
    if not msg_id or not chat_id:
        return None
    sender_id = getattr(getattr(body, "sender", None), "sender_id", None)
    user_id = str(getattr(sender_id, "open_id", "") or "")
    msg_type = str(getattr(message, "message_type", "") or getattr(message, "msg_type", "") or "")
    text = ""
    if msg_type == "text":
        with contextlib.suppress(ValueError, TypeError, json.JSONDecodeError):
            payload = json.loads(getattr(message, "content", "") or "{}")
            text = str(payload.get("text") or "")
    mentioned = False
    for mention in getattr(message, "mentions", None) or []:
        mid = getattr(getattr(mention, "id", None), "open_id", "")
        if bot_open_id and str(mid or "") == bot_open_id:
            mentioned = True
            break
    is_p2p = str(getattr(message, "chat_type", "") or "") == "p2p"
    return InboundMessage(
        tenant=str(getattr(header, "tenant_key", "") or ""),
        chat_id=chat_id,
        chat_type=CHAT_DM if is_p2p else CHAT_GROUP,
        user_id=user_id,
        text=text,
        msg_id=msg_id,
        mentioned_me=mentioned,
        msg_kind=KIND_TEXT if msg_type == "text" else KIND_OTHER,
        has_attachments=msg_type != "text",
    )


def build_actions_card(text: str, actions: list[Action]) -> dict:
    """把一排 `Action` 拼成一张**卡片 JSON 2.0**（P4.22 §6.1）。

    每个按钮的 `behaviors[0].value` 只放 `{"c": <核心生成的命令文本>}`——**不放路径、不放
    KB 位置、不放凭据、不放会话 id**（红线 5）。页面名本来就已经印在卡片上给人看了，不构成
    新泄露；而路径是本机信息，不该经飞书服务端往返。

    两处易错、都已订正：按钮文本是 **`{"tag": "plain_text", "content": …}` 对象**（不是裸串，
    决策P4.22-15）；`enable_forward=false` 是**纵深防御**，**绝不作为安全依据**——真正的闸是
    适配器里那张会话事实缓存（决策P4.22-5），因为转发出去的卡片照样能点。
    """
    elements: list[dict] = [{"tag": "markdown", "content": text}]
    for action in actions:
        elements.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": action.label},
                "type": "default",
                "behaviors": [{"type": "callback", "value": {"c": action.command}}],
            }
        )
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "enable_forward": False},
        "body": {"elements": elements},
    }


def card_content(text: str, actions: list[Action]) -> str:
    return json.dumps(build_actions_card(text, actions), ensure_ascii=False)


def callback_chat_key(data: object) -> tuple[str, str]:
    """回调 → `(tenant, chat_id)`，即会话事实缓存的键（★ 决策P4.22-17）。

    **键的两半都必须与 `map_event` 同源**：`tenant` 取 **`header.tenant_key`**——回调里
    **同时存在** `event.operator.tenant_key`，取它在外部群 / 跨租户场景下可能是**另一个值**，
    于是合法点击**查不到缓存**、被误判成"会话已过期"，表现为「按钮点了说过期，再问一次又好了」
    这种最难查的间歇故障。**键取错的后果不是报错，是查不到。**

    抽成一个函数是为了让"同源"这件事**结构上成立**：写缓存（`map_event` 的产物）、读缓存、
    以及 `map_card_action` 都只有这一个取值口。
    """
    header = getattr(data, "header", None)
    context = getattr(getattr(data, "event", None), "context", None)
    return (
        str(getattr(header, "tenant_key", "") or ""),
        str(getattr(context, "open_chat_id", "") or ""),
    )


def _callback_command(data: object) -> str:
    """从回调里取回传串，并做**信封级**校验。不合规 → 空串。

    **输入一律不可信**（决策P4.22-8）：回传串是经飞书服务端往返的外部输入，"这是我们自己发
    出去的卡片回来的"不构成信任依据。这里只判信封（非空、以 `/` 开头、≤256、无控制字符），
    **参数长度不在这里判**——那是命令语义，归核心的单一校验器。
    """
    action = getattr(getattr(data, "event", None), "action", None)
    value = getattr(action, "value", None)
    if isinstance(value, str):
        # 平台偶有把 `value` 原样回成 JSON 串的情形；解不开就当没有，绝不猜。
        with contextlib.suppress(ValueError, TypeError, json.JSONDecodeError):
            value = json.loads(value)
    if not isinstance(value, dict):
        return ""
    command = value.get("c")
    if not isinstance(command, str):
        return ""
    command = command.strip()
    if not command or not command.startswith("/") or len(command) > MAX_CALLBACK_COMMAND:
        return ""
    if any(ch < " " or ch == "\x7f" for ch in command):
        return ""
    return command


def map_card_action(data: object, *, chat_type: str) -> InboundMessage | None:
    """卡片回调 → `InboundMessage`（P4.22 §5.2，与 `map_event` 对称）。返回 `None` = 整条丢弃。

    | `InboundMessage` | 回调字段 |
    |---|---|
    | `tenant` | `header.tenant_key`（★ 与 `map_event` 同源，见 `callback_chat_key`） |
    | `chat_id` | `event.context.open_chat_id` |
    | `chat_type` | **由调用方查会话事实缓存后传入**（回调不带这个字段） |
    | `user_id` | `event.operator.open_id`（与消息事件同一命名空间，白名单可直接比） |
    | `text` | `event.action.value["c"]` |
    | `msg_id` | `f"card:{header.event_id}"` ← **加前缀**：与消息 id 分属两个命名空间 |
    | `mentioned_me` | 恒 `True`（**点击即寻址**，决策P4.22-4） |
    | `origin` | 恒 `"action"` ← 核心据此把可执行命令收窄到只读白名单（决策P4.22-14） |

    映射完就是一条**与用户手打无法区分**的入站消息，五道闸原样复用、`server.py` 零改。
    """
    tenant, chat_id = callback_chat_key(data)
    event_id = str(getattr(getattr(data, "header", None), "event_id", "") or "")
    operator = getattr(getattr(data, "event", None), "operator", None)
    user_id = str(getattr(operator, "open_id", "") or "")
    command = _callback_command(data)
    if not chat_id or not event_id or not user_id or not command:
        return None
    return InboundMessage(
        tenant=tenant,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
        text=command,
        msg_id=f"card:{event_id}",
        mentioned_me=True,
        msg_kind=KIND_TEXT,
        has_attachments=False,
        origin=ORIGIN_ACTION,
    )


def _toast(content: str) -> dict:
    """回调的同步应答（3 秒内必须返回，§0.3 第 3 条）。

    **刻意回一个普通 dict、不构造 SDK 的 `P2CardActionTriggerResponse`**：处理函数的返回值
    被 SDK 直接交给 `JSON.marshal`，而那个模型 marshal 出来的就是这个形状——**线上的形状才是
    契约**。回 dict 换来的是：不必在 SDK 线程里 import 模型、假件测试不必伪造模型模块、
    模型改名也不会把我们炸掉。
    """
    return {"toast": {"type": "info", "content": content}}


def _card_aware_ws_client(lark) -> type:
    """★ **CARD 帧兼容层**（决策P4.22-21）：让卡片回调无论走哪种帧都能到达处理函数。

    `lark-oapi` 的 WS 客户端在 `_handle_data_frame` 里对 `MessageType.CARD` **直接 return**
    （1.7.2 仍如此，`ws/client.py`），而**只有 EVENT 分支**会走 `_do_without_validation`——
    后者才是查 `_callback_processor_map`（即 `register_p2_card_action_trigger` 注册进去的那张表）
    的地方。于是"卡片回调到底以哪种帧下发"直接决定按钮能不能用，而这件事的证据是**矛盾**的
    （见 docs/P4.22 §0.2）：Go SDK 的 EVENT 分支写着 `// for cardCallback`，而上游 issue #126
    的真机报告指认 CARD 帧被丢、客户端报 200340 超时。

    **本层的作用是让这个问题不必先有答案**：CARD 帧进来就把帧头改写成 EVENT 再交回父类，
    于是两种假说下行为一致。若平台走的本来就是 EVENT 帧，本层**一次都不会触发**——它是纯粹的
    超集，不改变任何既有路径。

    **改写必须在写出回执前还原**（★ 决策P4.22-26）：父类末尾是
    `frame.payload = ...; await self._write_message(frame.SerializeToString())`——**回执复用的
    就是入站那个帧对象**（1.7.2 `ws/client.py` 确认）。不还原的话，CARD 假说成立时应答会带着
    我们伪造的 `type=event` 回去；平台若按帧型路由应答，那颗 toast 就没了。页面照样会到（入队
    发生在应答之前），但"点了没反应"正是这一相位最难排查的症状，不该由我们自己制造。父类在
    `_handle_data_frame` 内部就完成了序列化，事后还原来不及，故在**写出这一刻**拦一道。

    代价是吃了两个私有方法名，故：**探不到就退回原类并告警**（降级不拒启，同决策P4.22-10），
    且 `tests/test_im_feishu.py` 有一条形状探针用例守着——**上游修好即可整段删除**。
    """
    base = lark.ws.Client
    if not hasattr(base, "_handle_data_frame"):
        _logger.warning(
            "`lark-oapi` 的 WS 客户端没有 `_handle_data_frame`：卡片帧兼容层未启用。"
            "若卡片按钮点了没反应，见 docs/P4.22-IM可点引用.md §0.2。"
        )
        return base
    if not hasattr(base, "_write_message"):
        # 还原不了就不改写：宁可回到"CARD 帧被父类丢弃"的原状（按钮不工作、但没有伪造的帧头
        # 流出去），也不要让一个我们无法收尾的改写留在回执上。
        _logger.warning(
            "`lark-oapi` 的 WS 客户端没有 `_write_message`：卡片帧兼容层未启用（无法还原回执帧头）。"
        )
        return base
    try:
        from lark_oapi.ws.const import HEADER_TYPE
        from lark_oapi.ws.enum import MessageType
    except ImportError:  # SDK 换了内部布局：降级，不拒启
        _logger.warning("无法加载 `lark-oapi` 的 WS 帧常量：卡片帧兼容层未启用。")
        return base

    class _CardAwareClient(base):  # type: ignore[misc, valid-type]
        async def _handle_data_frame(self, frame):  # noqa: D401
            token = None
            for header in frame.headers:
                if header.key == HEADER_TYPE and header.value == MessageType.CARD.value:
                    # 唯一的差别就是父类那条 `return`，故改一个帧头即可复用它全部的
                    # 分包合并 / 响应回写逻辑——绝不把那 40 行抄一遍。
                    header.value = MessageType.EVENT.value
                    token = _CARD_FRAME.set((frame, header))
                    _logger.debug("卡片回调以 CARD 帧下发，已并入 EVENT 分支处理")
                    break
            try:
                return await super()._handle_data_frame(frame)
            finally:
                if token is not None:
                    _CARD_FRAME.reset(token)

        async def _write_message(self, data):  # noqa: D401
            pending = _CARD_FRAME.get()
            if pending is not None:
                _CARD_FRAME.set(None)  # 一帧只还原一次；后续写出与本帧无关
                frame, header = pending
                header.value = MessageType.CARD.value
                try:
                    data = frame.SerializeToString()
                except Exception:  # noqa: BLE001 — 重序列化失败就照发原串，绝不把回执吞掉
                    _logger.debug("卡片回执帧头还原后重序列化失败，按原样发出", exc_info=True)
            return await super()._write_message(data)

    return _CardAwareClient


def _require_channel_tag(ws_client_cls: type) -> None:
    """SDK 签名探针：缺 `extra_ua_tags` → 拒启并明示升级（决策P4.21-29）。"""
    try:
        params = inspect.signature(ws_client_cls.__init__).parameters
    except (TypeError, ValueError):  # C 扩展 / 奇异实现：探不到就别拦（真传参时会自己炸）
        return
    if "extra_ua_tags" not in params:
        raise GuanlanError(
            f"`lark-oapi` 版本过旧：WS 客户端不接受 `extra_ua_tags`，服务端**不会**推送群 @ 事件。"
            f"请升级到 `lark-oapi>={MIN_SDK_VERSION}`。",
            exit_code=EXIT_USAGE,
        )


class FeishuAdapter:
    """飞书适配器：WS 长连入站（SDK 线程 → 队列桥）+ 阻塞 SDK 出站（卸线程）。"""

    name = "feishu"
    caps = CAPS

    def __init__(
        self, state: Path, *, group_wanted: bool = False, actions_wanted: bool = True
    ) -> None:
        self._state = Path(state)
        # 实例自持一份能力位：SDK 缺卡片注册面时**就地降级**（决策P4.22-10），而核心每次分派
        # 都读 `self._adapter.caps`，故降级立刻生效、无须改动装配处。
        #
        # `actions_wanted=False` 是**平台无关的部署意图**（与 `group_wanted` 同款，不是平台分支）：
        # `im-identify` 用它。那个模式的安全性**整个建立在「绝不回复任何消息」上**（决策P4.21-28）——
        # 对方只看到"发了没人理"。而卡片回调是要**同步作答一个 toast** 的：上一次 `guanlan im` 发出的
        # 卡片在 identify 期间被点一下，就会回一句"会话已过期"，等于告诉对方**机器人此刻正活着**。
        # 那扇门只能在**注册之前**关掉，故这是个构造期参数、不是运行期判断。
        self.caps = CAPS if actions_wanted else replace(CAPS, supports_actions=False, max_actions=0)
        # `group_wanted` 是**平台无关**的部署意图（"这次部署要不要收群消息"），由核心从白名单
        # 推出后传进来——不是平台分支（决策P4.21-3 守住的是"核心不写 if platform"，不是
        # "适配器不许收参数"）。飞书用它决定 bot 身份探测失败是否致命（§8.2）。
        self._group_wanted = group_wanted
        self._app_id = ""
        self._app_secret = ""
        self._domain = ""
        self._bot_open_id = ""
        self._client = None
        self._ws = None
        self._ws_thread: threading.Thread | None = None
        # 队列里除 `InboundMessage` 外还可能出现 `_WS_DEAD` 哨兵（见 `_ws_runner`）。
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing = False  # `close()` 已开始 → WS 线程结束属正常，别再报"连接已断"
        # 首连看门狗的两个跨线程标志（SDK 的钩子在 WS 线程里被调），见 `_await_first_connection`。
        self._ws_reconnecting = threading.Event()  # SDK 已进入重连 ⇒ 首连**失败了**
        self._ws_reconnected = threading.Event()  # 重连成功（首连成功不走这个钩子）
        # ★ 会话事实缓存（P4.22 §5.2.1，决策P4.22-5）：`(tenant, chat_id) → chat_type`。
        # **事实，不是策略**——适配器本来就是 `chat_type` 的产地，`allow_chats` 一个字都不进
        # 这里。回调不带 `chat_type`，而 `open_chat_id` 的前缀区分不了单聊与群（都是 `oc_`），
        # 故只认**真实入站消息见过**的会话，未命中 fail-closed。
        # 锁：写在 `_on_message`、读在 `_on_card_action`，两者都在 SDK 线程上；单线程时锁是
        # 纳秒级空转，而它挡住的是"SDK 哪天换成线程池"这种不会有人告诉我们的变化。
        self._chats: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._chats_lock = threading.Lock()
        self._card_hint_logged = False

    # ── 凭据 ────────────────────────────────────────────────────────────────
    def _load_credentials(self) -> None:
        """凭据 env-only（决策P4.21-6）：**绝不**提供明文命令行参数、**绝不**进日志。"""
        self._app_id = (os.environ.get(ENV_APP_ID) or "").strip()
        self._app_secret = (os.environ.get(ENV_APP_SECRET) or "").strip()
        if not self._app_id or not self._app_secret:
            raise GuanlanError(
                f"缺少飞书凭据：请设置环境变量 {ENV_APP_ID} 与 {ENV_APP_SECRET}。",
                exit_code=EXIT_USAGE,
            )
        raw_domain = (os.environ.get(ENV_DOMAIN) or "feishu").strip().lower()
        if raw_domain not in {"feishu", "lark"}:
            # 替代「用默认值连错域名后报认证失败」这种误导性故障（§9.2）。
            raise GuanlanError(
                f"{ENV_DOMAIN} 取值非法：{raw_domain!r}；合法值为 feishu（国内站）或 lark（国际站）。",
                exit_code=EXIT_USAGE,
            )
        self._domain_key = raw_domain

    async def start(self) -> None:
        import lark_oapi as lark

        self._load_credentials()
        # ★ SDK 版本探针要在**任何副作用之前**跑（§9.2「非法用法一律就地拒启、不建立任何连接」）：
        # 它是一次纯 `inspect.signature`，零成本、结论与凭据/网络无关。原先放在 `_resolve_bot_identity()`
        # **之后**，于是 SDK 过旧的用户要先等一次 `/open-apis/bot/v3/info` 网络往返（凭据错时还会先
        # 撞上超时或认证失败），才看到那句"请升级 lark-oapi"——排查方向直接被带偏。
        _require_channel_tag(lark.ws.Client)
        # 飞书国内站与 Lark 国际站是两套域名，SDK 构造期就要选定。
        self._domain = getattr(
            lark, "FEISHU_DOMAIN" if self._domain_key == "feishu" else "LARK_DOMAIN", self._domain_key
        )
        self._client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .domain(self._domain)
            .build()
        )
        await self._resolve_bot_identity()
        self._loop = asyncio.get_running_loop()
        builder = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(
            self._on_message
        )
        # ★ 卡片回传交互（P4.22 §6.3）。**SDK 签名探针 → 缺则降级而非拒启**（决策P4.22-10）：
        # 卡片是叠加层，没有它宿主照常工作。这与决策P4.21-29 的 `extra_ua_tags` 档次不同——
        # 那条缺了会**静默收不到群消息**（主功能），故是拒启。
        # `supports_actions=False` ＝ 本次部署不要可点面（`actions_wanted=False`，如 `im-identify`）：
        # **连注册都不做**，于是回调根本到不了 `_on_card_action`，"绝不回复"才是结构上的、不是自觉的。
        if self.caps.supports_actions:
            if hasattr(builder, "register_p2_card_action_trigger"):
                builder = builder.register_p2_card_action_trigger(self._on_card_action)
            else:
                # 从 `self.caps` 派生（**不是**从模块级 `CAPS`）：降级只该关掉可点面这一位，
                # 别把实例上别的调整顺手抹回默认值。
                self.caps = replace(self.caps, supports_actions=False, max_actions=0)
                _logger.warning(
                    "`lark-oapi` 没有 `register_p2_card_action_trigger`：本次不提供可点引用"
                    "（答案里的 [[引用]] 仍原样保留，可手打 `/page 名字` 打开）。"
                )
        handler = builder.build()
        # 版本探针已在 `start()` 开头跑过（副作用之前）；这里再叠一层 CARD 帧兼容层。
        ws_cls = _card_aware_ws_client(lark) if self.caps.supports_actions else lark.ws.Client
        self._ws = ws_cls(
            app_id=self._app_id,
            app_secret=self._app_secret,
            domain=self._domain,
            event_handler=handler,
            # 不传这个 tag，飞书服务端**不会**经 WebSocket 推送群 @ 事件（只推 P2P 单聊）。
            extra_ua_tags=["channel"],
        )
        # 首连看门狗的两个钩子：SDK 的 `_reconnect()` 会 `self.on_reconnecting()` / 成功后
        # `self.on_reconnected()`，两者都包在 try/except 里。**类上并没有定义它们**——这是
        # SDK 留给使用方的鸭子类型扩展点（不赋值就 AttributeError、被它自己吞掉）。
        self._ws_reconnecting.clear()
        self._ws_reconnected.clear()
        self._ws.on_reconnecting = self._ws_reconnecting.set
        self._ws.on_reconnected = self._ws_reconnected.set
        # SDK 的 `start()` 是**阻塞**的（内部自建事件循环），故放进 daemon 线程。
        self._ws_thread = threading.Thread(
            target=self._ws_runner, name="guanlan-im-feishu-ws", daemon=True
        )
        self._ws_thread.start()
        await self._await_first_connection()

    async def _await_first_connection(self) -> None:
        """**首连看门狗**：起服时必须拿到「长连真的建起来了」的证据，否则拒启。

        没有它，一次失败的首连会变成**决策P4.21-57 明写要杜绝的活死人**，而且是 `_WS_DEAD`
        哨兵**够不到**的那一种：SDK 的 `start()` 把 `_connect()` 的异常吞进 `_reconnect()`，
        而首连成功之前 `_reconnect_count` 还是默认的 `-1` ⇒ 走 `while True` 分支、每
        `_reconnect_interval`（实测默认 **120 秒**）重试一次、**永不放弃**。于是那个 daemon
        线程永远不返回，`_ws_runner` 末尾那句 `_WS_DEAD` 永远投不出去——宿主一路"启动成功"，
        日志安静，消息一条收不到。真机上撞见的 `SSL: CERTIFICATE_VERIFY_FAILED` 正是这条路径。

        判据是**非对称**的，这一点是有意的：

        - **有失败的正面证据** → 拒启。`on_reconnecting` 已触发（＝首连失败、已进无限重连），
          或线程已死，或 `_conn` 到点仍是 `None`。
        - **有成功的正面证据** → 放行。`_conn` 非空，或 `on_reconnected` 触发。
        - **两种证据都取不到**（SDK 换了形状、`_conn` 这个私有属性不复存在）→ **放行并告警**，
          绝不拒启。观测不到不等于坏了；这里误判一次就是让所有升级 SDK 的人起不了服。
          真 SDK 的形状另有一条探针用例守着（`test_ws_client_exposes_the_watchdog_hooks`），
          它变红比运行期悄悄降级成"看不见"要早得多。

        首连失败**不等它重试**：重试间隔 120 秒，任何合理的启动窗口都接不住第二次尝试。与其
        让用户对着两分钟的静默发呆，不如立刻报错——重跑一次的成本远低于误判"已经在跑了"。
        """
        deadline = asyncio.get_running_loop().time() + WS_FIRST_CONNECT_TIMEOUT
        observable = hasattr(self._ws, "_conn")  # 取不到就只能靠钩子，见上文第三条
        while True:
            if self._ws_reconnected.is_set() or getattr(self._ws, "_conn", None) is not None:
                return
            if self._ws_reconnecting.is_set():
                raise GuanlanError(
                    "飞书 WS 首次连接失败，SDK 已进入无限重连（每 120 秒一次），"
                    "宿主此刻收不到任何消息，故拒绝以「假装启动成功」的姿态继续。"
                    "常见原因：网络不通、app_id/app_secret 不对、或本机 TLS 根证书库为空"
                    "（macOS 官方版 Python 需跑一次 `Install Certificates.command`）。"
                    "上面几行 `[Lark] ... connect failed` 的日志里有服务端原话。",
                    exit_code=EXIT_USAGE,
                )
            thread = self._ws_thread
            if thread is not None and not thread.is_alive():
                raise GuanlanError(
                    "飞书 WS 长连线程在建立连接前就退出了——上面的 ERROR 日志里有原因。",
                    exit_code=EXIT_USAGE,
                )
            if asyncio.get_running_loop().time() >= deadline:
                if not observable:
                    # 观测不到 ⇒ 不拒启，但要留下痕迹：日后真出「机器人不理我」时，这行是
                    # 唯一能说明"看门狗当时是瞎的"的证据。
                    _logger.warning(
                        "无法确认飞书 WS 是否连上（SDK 未暴露 `_conn`，版本可能已变）："
                        "首连看门狗本次**未生效**，若收不到消息请检查网络与凭据。"
                    )
                    return
                raise GuanlanError(
                    f"飞书 WS 在 {WS_FIRST_CONNECT_TIMEOUT:g} 秒内没有连上，"
                    "宿主此刻收不到任何消息，故拒绝以「假装启动成功」的姿态继续。"
                    "请检查网络连通性与 app_id/app_secret。",
                    exit_code=EXIT_USAGE,
                )
            await asyncio.sleep(_WS_POLL_INTERVAL)

    def _ws_runner(self) -> None:
        """在 daemon 线程里跑 SDK 的阻塞 `start()`，**并把它的死亡报回事件循环**。

        直接 `target=self._ws.start` 会让长连的死亡**完全无声**：线程带着异常退出、`inbound()`
        继续阻塞在 `Queue.get()` 上永不返回，于是宿主变成决策P4.21-57 明写要避免的那种「活死人」
        ——**进程活着、日志一片安静、消息再也收不到**，用户只看到「机器人不理我了」。
        `_run` 的 `recv.done()` 判据要生效，收流侧就必须**真的结束**，故这里投一个哨兵。
        """
        try:
            self._ws.start()
        except BaseException:  # noqa: BLE001 — 线程边界：不记就彻底消失了
            _logger.error("飞书 WS 长连线程异常退出", exc_info=True)
        loop = self._loop
        if self._closing or loop is None:  # 正常停机：线程结束是预期的，不报错
            return
        with contextlib.suppress(RuntimeError):  # loop 已关 → 无处可报，进程本就在退
            loop.call_soon_threadsafe(self._queue.put_nowait, _WS_DEAD)

    async def _resolve_bot_identity(self) -> None:
        """**启动时总是探测**（§8.2，决策P4.21-29）。

        原设计要求必填 `GUANLAN_IM_FEISHU_BOT_OPEN_ID`——**多余的**：`GET /open-apis/bot/v3/info`
        用 tenant access token 即可，**无需额外权限**。且**不因 env 已设而跳过**——陈旧的 env 值会
        **静默破坏群 @ 判定**，表现为「群里怎么都不理我」。env 仅作探测失败时的回退。
        """
        probed = ""
        with contextlib.suppress(Exception):
            probed = await anyio.to_thread.run_sync(self._probe_bot_open_id)
        env_value = (os.environ.get(ENV_BOT_OPEN_ID) or "").strip()
        if probed:
            if env_value and env_value != probed:
                _logger.warning(
                    "%s 与探测到的 bot open_id 不一致，**以探测值为准**（陈旧的 env 会静默破坏群 @ 判定）。",
                    ENV_BOT_OPEN_ID,
                )
            self._bot_open_id = probed
            return
        if env_value:
            _logger.warning("bot 身份探测失败，回退到 %s 的值。", ENV_BOT_OPEN_ID)
            self._bot_open_id = env_value
            return
        if self._group_wanted:
            raise GuanlanError(
                "无法确定 bot 的 open_id（探测 /open-apis/bot/v3/info 失败，且未设 "
                f"{ENV_BOT_OPEN_ID}）：群内 @ 判定会永远为 False，表现为「群里怎么都不理我」。"
                "请检查应用凭据/网络，或显式设置该环境变量。",
                exit_code=EXIT_USAGE,
            )
        _logger.warning("无法确定 bot 的 open_id；本次未启用群聊，故不影响单聊。")

    def _probe_bot_open_id(self) -> str:
        """同步 SDK 调用（由调用方卸线程）。任何异常交给调用方的 `suppress` 兜。"""
        import lark_oapi as lark

        request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri(BOT_INFO_URI)
            .token_types({lark.AccessTokenType.TENANT})
            .build()
        )
        response = self._client.request(request)
        raw = getattr(response, "raw", None)
        payload = json.loads((getattr(raw, "content", b"") or b"{}").decode("utf-8"))
        bot = payload.get("bot") or {}
        name = bot.get("app_name")
        if name:
            _logger.info("飞书 bot 身份：%s", name)
        return str(bot.get("open_id") or "")

    # ── 入站：SDK 线程 → 事件循环 ───────────────────────────────────────────
    def _on_message(self, event: object) -> None:
        """**在 SDK 线程里跑**：绝不直接碰事件循环对象，一律经 `call_soon_threadsafe` 桥回。"""
        loop = self._loop
        if loop is None:
            return
        msg = map_event(event, bot_open_id=self._bot_open_id)
        if msg is None:
            return
        self._remember_chat(msg)  # ★ P4.22：记下这个会话是单聊还是群，供回调查
        with contextlib.suppress(RuntimeError):  # loop 已关（停机竞态）→ 丢弃即可
            loop.call_soon_threadsafe(self._queue.put_nowait, msg)

    # ── P4.22：会话事实缓存 + 卡片回调 ──────────────────────────────────────
    def _remember_chat(self, msg: InboundMessage) -> None:
        """记一条 `(tenant, chat_id) → chat_type`（LRU，上限 `CHAT_CACHE_MAX`）。

        键取自**已映射好的 `InboundMessage`**，故与 `map_event` 天然同源——回调那侧用
        `callback_chat_key` 取同样的两个字段（决策P4.22-17）。
        """
        with self._chats_lock:
            key = (msg.tenant, msg.chat_id)
            self._chats[key] = msg.chat_type
            self._chats.move_to_end(key)
            while len(self._chats) > CHAT_CACHE_MAX:
                self._chats.popitem(last=False)

    def _known_chat(self, tenant: str, chat_id: str) -> str | None:
        with self._chats_lock:
            chat_type = self._chats.get((tenant, chat_id))
            if chat_type is not None:
                self._chats.move_to_end((tenant, chat_id))
            return chat_type

    def _on_card_action(self, data: object) -> dict:
        """卡片按钮被点了（P4.22 §5.4）。**在 SDK 线程里跑，且必须 3 秒内返回。**

        故这里有且只有两步：映射（纯 CPU、微秒级）+ 投队列（`call_soon_threadsafe`）。
        **绝不**在此做磁盘 / 网络 / LLM——`/page` 要读整个 wiki 建解析表，放这里必然超时。
        真活儿由队列另一端的既有流水线接手，慢多久都不影响这 3 秒。

        线程边界与 `_on_message` **逐字同构**——这不是巧合，是把"回调"归一成"入站消息"
        之后的必然结果。

        **toast 不过白名单**（决策P4.22-7）：白名单判定在核心、异步之后，而这里必须同步作答。
        中性 toast 的信息量为零——卡片本身就在那个群里对所有人可见，"这里有个机器人且它在听"
        早已公开；反过来把 `AccessPolicy` 下放到适配器，代价是白名单有两份实现。
        """
        tenant, chat_id = callback_chat_key(data)
        if not chat_id:
            # 连会话 id 都没有 ⇒ 信封本身就不合规，与"这个会话没见过"是两回事。回"已过期"会
            # 把现场引向错误方向（去查 LRU / 重启），故与下面的载荷不合规同归一句。
            return _toast(TOAST_BAD)
        chat_type = self._known_chat(tenant, chat_id)
        if chat_type is None:
            # **未见过的会话一律丢弃**（fail-closed，决策P4.22-5）：我们只会把卡片发进
            # 回答过问题的会话，所以正常点击必定命中；命不中只有两种来源——**卡片被转发到
            # 别处**（飞书卡片可转发，这条路不是假想），或**进程重启后点了旧卡片**（卡片可
            # 交互期 14 天）。前者必须挡，后者挡掉只是失效一次。
            return _toast(TOAST_EXPIRED)
        msg = map_card_action(data, chat_type=chat_type)
        if msg is None:  # 信封不合规：会话是认识的，但这个按钮的载荷不对
            return _toast(TOAST_BAD)
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._queue.put_nowait, msg)
            except RuntimeError:  # loop 已关（停机竞态）
                pass
            else:
                return _toast(TOAST_ACK)
        # **投不进队列就别说"已收到"**：那条消息不会有任何后续，而用户会一直等着那一页。
        # 唯一可能走到这里的时机是停机竞态，届时"会话已过期，请重新提问"正是该说的话。
        return _toast(TOAST_EXPIRED)

    async def inbound(self) -> AsyncIterator[InboundMessage]:
        """只阻塞在 `Queue.get` 上，故外部 `task.cancel()` 即可干净结束（决策P4.21-56）。"""
        while True:
            item = await self._queue.get()
            if item is _WS_DEAD:
                # 抛出即让 `_run` 的 `recv.done()` 成立 → 记 ERROR、`EXIT_AGENT_ERROR`（决策P4.21-57）。
                raise RuntimeError("飞书 WS 长连已断开，宿主无法继续收流")
            yield item  # type: ignore[misc]

    async def close(self) -> None:
        self._closing = True  # 必须早于 stop()：否则线程一返回就误投 `_WS_DEAD`
        ws, self._ws = self._ws, None
        if ws is not None:
            # SDK 各版本的停法不一：能停就停，停不掉靠 daemon 线程随进程退出。
            for name in ("stop", "_disconnect", "disconnect"):
                stopper = getattr(ws, name, None)
                if callable(stopper):
                    with contextlib.suppress(Exception):
                        result = stopper()
                        if inspect.isawaitable(result):
                            await result
                    break
        self._client = None
        self._loop = None

    # ── 出站（阻塞 SDK → 卸线程）────────────────────────────────────────────
    async def send(self, chat_id: str, text: str) -> OutboundRef:
        message_id = await anyio.to_thread.run_sync(partial(self._send_sync, chat_id, text))
        return OutboundRef(chat_id=chat_id, message_id=message_id)

    def _send_sync(self, chat_id: str, text: str) -> str:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        def _create(msg_type: str, content: str):
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )
            return self._client.im.v1.message.create(request)

        response = _create("post", post_content(text))
        if not _ok(response):
            # **`post` 被拒 → 回退纯文本**：参考实现在 send 与 edit 两侧各写了一遍，
            # 说明这是实际会发生的（某些 markdown 会被 API 判非法，§8.4）。
            _logger.warning("飞书 post 消息被拒（%s），回退纯文本", _err(response))
            response = _create("text", text_content(text))
            if not _ok(response):
                raise GuanlanError(f"飞书发送失败：{_err(response)}", exit_code=EXIT_USAGE)
        return str(getattr(getattr(response, "data", None), "message_id", "") or "")

    async def edit(self, ref: OutboundRef, text: str, *, finalize: bool = False) -> None:
        await anyio.to_thread.run_sync(partial(self._edit_sync, ref.message_id, text))

    def _edit_sync(self, message_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import UpdateMessageRequest, UpdateMessageRequestBody

        def _update(msg_type: str, content: str):
            body = UpdateMessageRequestBody.builder().msg_type(msg_type).content(content).build()
            request = (
                UpdateMessageRequest.builder().message_id(message_id).request_body(body).build()
            )
            return self._client.im.v1.message.update(request)

        response = _update("post", post_content(text))
        if not _ok(response):
            _logger.warning("飞书 post 编辑被拒（%s），回退纯文本", _err(response))
            response = _update("text", text_content(text))
            if not _ok(response):
                raise GuanlanError(f"飞书编辑失败：{_err(response)}", exit_code=EXIT_USAGE)

    async def typing(self, chat_id: str, on: bool) -> None:
        """飞书 `supports_typing=False`，核心永不调到这里。"""
        raise NotImplementedError("飞书 v1 不做 typing（caps.supports_typing=False）")

    async def send_actions(self, chat_id: str, text: str, actions: list[Action]) -> None:
        """发一张带可点引用按钮的卡片（P4.22 §6.1）。

        **承载在追发的独立消息上**（决策P4.22-6）：正文是 `post` 富文本、卡片是 `interactive`，
        `msg_type` 不可中途变；更重要的是本相位的全部新东西因此隔离在一条**可以整条不发**的
        消息上，P4.21 那段单写者 / 节流 / finalize 的竞态代码一个字不动。
        """
        if not actions:
            return
        await anyio.to_thread.run_sync(partial(self._send_card_sync, chat_id, text, actions))
        if not self._card_hint_logged:
            # **漏配「回调 → 长连接」是静默的**（§6.4）：卡片发得出、点了没反应、无任何日志。
            # 这条 INFO 是那种故障现场唯一的可执行线索，故只记一次、但一定要记。
            self._card_hint_logged = True
            _logger.info(CARD_HINT)

    def _send_card_sync(self, chat_id: str, text: str, actions: list[Action]) -> None:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(card_content(text, actions))
            .build()
        )
        request = (
            CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        )
        response = self._client.im.v1.message.create(request)
        if not _ok(response):
            # **不回退纯文本**（有别于 `_send_sync`）：正文里的 `[[引用]]` 原文本来就还在
            # （决策P4.21-76 在本相位继续成立），再补一条纯文本清单只是重复噪音。
            raise GuanlanError(f"飞书卡片发送失败：{_err(response)}", exit_code=EXIT_USAGE)


def _ok(response: object) -> bool:
    success = getattr(response, "success", None)
    if callable(success):
        return bool(success())
    return bool(success)


def _err(response: object) -> str:
    return f"code={getattr(response, 'code', '?')} msg={getattr(response, 'msg', '?')}"
