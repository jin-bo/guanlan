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
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path

import anyio.to_thread

from ...errors import EXIT_USAGE, GuanlanError
from ..contract import (
    CHAT_DM,
    CHAT_GROUP,
    KIND_OTHER,
    KIND_TEXT,
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
)

# 「一条 WS 长连已经死了」的哨兵（见 `_ws_runner`）。放队列里传，使 `inbound()` 能把它变成异常。
_WS_DEAD = object()


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

    def __init__(self, state: Path, *, group_wanted: bool = False) -> None:
        self._state = Path(state)
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
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)  # v1 只注册这一个事件
            .build()
        )
        ws_cls = lark.ws.Client  # 版本探针已在 `start()` 开头跑过（副作用之前）
        self._ws = ws_cls(
            app_id=self._app_id,
            app_secret=self._app_secret,
            domain=self._domain,
            event_handler=handler,
            # 不传这个 tag，飞书服务端**不会**经 WebSocket 推送群 @ 事件（只推 P2P 单聊）。
            extra_ua_tags=["channel"],
        )
        # SDK 的 `start()` 是**阻塞**的（内部自建事件循环），故放进 daemon 线程。
        self._ws_thread = threading.Thread(
            target=self._ws_runner, name="guanlan-im-feishu-ws", daemon=True
        )
        self._ws_thread.start()

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
        with contextlib.suppress(RuntimeError):  # loop 已关（停机竞态）→ 丢弃即可
            loop.call_soon_threadsafe(self._queue.put_nowait, msg)

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


def _ok(response: object) -> bool:
    success = getattr(response, "success", None)
    if callable(success):
        return bool(success())
    return bool(success)


def _err(response: object) -> str:
    return f"code={getattr(response, 'code', '?')} msg={getattr(response, 'msg', '?')}"
