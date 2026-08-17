"""适配器 A：个人微信（腾讯 iLink Bot API，P4.21 §7）。

**三条必须写进代码的口径差（决策P4.21-24）**——同一份平台文档三处与参考实现的源码不符、
且两处与安全/行为相关，**观澜一律以源码为准**：

| 项 | 参考实现**源码** | 平台官方文档 | 取 |
|---|---|---|---|
| `max_message_length` | **2000** | 4000 | **源码** |
| `dm_policy` 默认 | **`pairing`** | `open` | **源码**（安全相关） |
| 分片间隔 | **1.5s** | 0.3s | **源码** |

**身份档位 = 弱档（§0.2）**：扫码得到的是 iLink bot 身份，入站 `from_user_id` 是 iLink 作用域内
的标识——**稳定、但不可枚举、不可反查、与真实身份无绑定**。结论不是"弱档不能用"，是
**"弱档只适合小圈子"**：几个人的手工授权是一次性成本，200 人的组织不可行。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import shutil
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import anyio.to_thread

from ...errors import EXIT_AGENT_ERROR, EXIT_USAGE, GuanlanError
from ..contract import (
    CHAT_DM,
    KIND_OTHER,
    KIND_TEXT,
    AdapterCaps,
    InboundMessage,
    OutboundRef,
)
from ..state import read_json, write_json_atomic

_logger = logging.getLogger("guanlan.im")

BASE_URL = "https://ilinkai.weixin.qq.com"
EP_GETUPDATES = "/ilink/bot/getupdates"
EP_SENDMESSAGE = "/ilink/bot/sendmessage"
EP_SENDTYPING = "/ilink/bot/sendtyping"
EP_GETCONFIG = "/ilink/bot/getconfig"
EP_QRCODE = "/ilink/bot/get_bot_qrcode"
EP_QRCODE_STATUS = "/ilink/bot/get_qrcode_status"

ITEM_TEXT = 1  # item_list 里的纯文本项
MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2
TYPING_START = 1
TYPING_STOP = 2

LONGPOLL_SECONDS = 35.0
CHANNEL_VERSION = "2.2.0"
CLIENT_VERSION = (2 << 16) | (2 << 8) | 0
TYPING_TICKET_TTL = 600.0  # getconfig 拿到的 ticket 按 user 缓存 10 分钟
STALE_SESSION_PAUSE = 600.0  # 入站登录失效后的暂停时长
RATE_LIMIT_ATTEMPTS = 4
RATE_LIMIT_BASE_BACKOFF = 3.0
CIRCUIT_BREAK_SECONDS = 30.0
FAILURE_BACKOFF_SECONDS = 30.0
FAILURE_SHORT_BACKOFF = 2.0
FAILURE_THRESHOLD = 3

CAPS = AdapterCaps(
    max_message_length=2000,  # 源码值（其文档写 4000），§7.1
    max_parts=5,  # 2000×5 ≈ 1 万字
    supports_edit=False,
    supports_typing=True,
    supports_late_push=True,
    supports_group=False,  # iLink 通常不投递群事件
    chunk_delay_s=1.5,  # 源码值（其文档写 0.3），防频控丢消息
    batch_delay_s=3.0,  # 源码实测值：iLink 逐条投递，合并静默期必须长
    batch_split_delay_s=5.0,
    # P4.22：个人微信只有**纯文本出站面**，没有任何可点结构 ⇒ 恒关。核心据此完全走现状：
    # 答案里的 `[[引用]]` 保留原文（决策P4.21-76），`send_actions` 一次都不会被调到。
    supports_actions=False,
    max_actions=0,
)


def is_stale_session(ret: object, errcode: object, errmsg: object) -> bool:
    """iLink 的 stale-session 判据（§7.4，决策P4.21-30）。

    原设计「`-14` 一律重扫码 + 停 600s；`-2` 一律频控」是**错的**，会在正常的 context token
    过期时错误停服 10 分钟并要求重登录。正确判据来自参考实现里同款的 stale-session 判据：

    - `-14` 是 stale 信号；
    - `-2` **且 `errmsg` 恰为 `"unknown error"`** 才是 stale，其余 `-2` 是**真频控**。

    注意这只是"信号识别"；**同一个信号在入站与出站是两件事**——见 `_classify_send_error`。
    """
    if ret == -14 or errcode == -14:
        return True
    if (ret == -2 or errcode == -2) and str(errmsg or "").lower() == "unknown error":
        return True
    return False


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "AuthorizationType": "ilink_bot_token",
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": str(CLIENT_VERSION),
        "X-WECHAT-UIN": str(random.randint(1_000_000, 9_999_999)),
        "Content-Type": "application/json",
    }


def _with_base_info(body: dict) -> dict:
    """**每个**请求体都并入 `base_info`——漏掉服务端会拒（§7.2）。"""
    return {**body, "base_info": {"channel_version": CHANNEL_VERSION}}


def map_inbound(raw: dict, *, account_id: str) -> InboundMessage | None:
    """入站字段映射（§7.2.1，决策P4.21-43）。返回 `None` = 该条应被丢弃。

    | 字段 | 取值 | 说明 |
    |---|---|---|
    | `tenant` | `account_id`（本机凭据里的，**不取自消息**） | 防跨账号撞号 |
    | `chat_id` | `from_user_id` | iLink 单聊没有独立会话实体，用对端 user id 充当 chat 键 |
    | `chat_type` | 恒 `"dm"` | 群消息已在适配器层丢弃，**永不**向核心吐出 `"group"` |
    | `user_id` | `from_user_id` | 与 `chat_id` 同值，这是单聊的正常形态，不是 bug |
    | `text` | 所有 `type == ITEM_TEXT` 的 `text_item.text` 按序拼接（换行分隔） | 无文本 item 则空串 |
    | `msg_id` | `message_id`；缺失则**丢弃该条**（不自造 id） | 自造 id 会绕过去重 |
    | `mentioned_me` | 恒 `False` | 单聊无 @ 概念 |
    | `msg_kind` | 全部 item 均为 `ITEM_TEXT` → `"text"`；否则 `"other"` | **混合消息判 `"other"`** |
    | `has_attachments` | 存在任一 `type != ITEM_TEXT` 的 item → `True` | 图片/语音/文件/视频四类都算 |

    **混合 item 的取舍**：判 `"other"` 走固定附件提示，`text` 字段**仍填**已提取的文本部分但
    **不进 LLM**。理由：用户发「这张图什么意思」+ 一张图时，只答文字部分会给出一个**看似回答了、
    实则没看到图**的答案——那比明说「暂不处理附件」更糟。**宁可少答，不可假答。**
    """
    from_user = str(raw.get("from_user_id") or "")
    if not from_user:
        return None
    if from_user == account_id:
        # **自消息过滤**：否则回声循环。**这条不能漏。**
        return None
    if raw.get("room_id") or raw.get("chat_room_id"):
        # 群消息：`caps.supports_group=False` → 丢弃并计数（debug 级，不刷屏）。
        _logger.debug("丢弃 iLink 群消息（本平台 supports_group=False）")
        return None
    msg_id = str(raw.get("message_id") or "")
    if not msg_id:
        _logger.debug("丢弃缺 message_id 的 iLink 帧（不自造 id，否则绕过去重）")
        return None
    items = raw.get("item_list") or []
    texts: list[str] = []
    has_other = False
    for item in items:
        if not isinstance(item, dict):
            has_other = True
            continue
        if item.get("type") == ITEM_TEXT:
            texts.append(str((item.get("text_item") or {}).get("text") or ""))
        else:
            has_other = True
    return InboundMessage(
        tenant=account_id,
        chat_id=from_user,
        chat_type=CHAT_DM,
        user_id=from_user,
        text="\n".join(t for t in texts if t),
        msg_id=msg_id,
        mentioned_me=False,
        msg_kind=KIND_OTHER if has_other else KIND_TEXT,
        has_attachments=has_other,
    )


class WeixinAdapter:
    """个人微信适配器：HTTP 长轮询入站 + 串行出站。"""

    name = "weixin"
    caps = CAPS

    def __init__(
        self,
        state: Path,
        *,
        transport: object | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] = asyncio.sleep,
    ) -> None:
        self._state = Path(state)
        self._transport = transport  # 测试注入 httpx.MockTransport
        self._clock = clock
        self._sleep = sleep
        self._client = None
        self._account_id = ""
        self._token = ""
        self._base_url = BASE_URL
        self._cursor = ""
        self._context_tokens: dict[str, str] = {}
        self._typing_tickets: dict[str, tuple[str, float]] = {}
        self._send_gate = asyncio.Lock()  # 出站串行化：微信频控是真的会丢消息
        self._failures = 0
        self._circuit_until = 0.0
        self._dropped_groups = 0

    # ── 状态文件（§7.3；全在 KB 外，不破零写承诺，但「零写 ≠ 无状态」）──────
    @property
    def _account_path(self) -> Path:
        return self._state / "account.json"

    @property
    def _sync_path(self) -> Path:
        return self._state / "sync.json"

    @property
    def _ctx_path(self) -> Path:
        return self._state / "context-tokens.json"

    async def start(self) -> None:
        import httpx

        account = read_json(self._account_path)
        if not account or not str(account.get("token") or "").strip():
            raise GuanlanError(
                f"缺少微信凭据（{self._account_path}）。先跑 `guanlan im-login --platform weixin` 扫码登录。",
                exit_code=EXIT_USAGE,
            )
        self._account_id = str(account.get("account_id") or "").strip()
        if not self._account_id:
            # **`account_id` 缺失必须拒启，不能当"可选字段"降级**：自消息过滤全靠
            # `from_user == account_id`（见 `map_inbound`），空串永不等于任何真实 `from_user_id`
            # ⇒ 过滤**整条失效** ⇒ 机器人把自己的回复当成新提问 ⇒ **回声循环 + 无上限 LLM 花费**。
            # 旧版本 / 参考实现写下的 `account.json` 正可能没有这个键，故这条闸不是理论问题。
            raise GuanlanError(
                f"微信凭据缺少 account_id（{self._account_path}）：没有它就无法过滤机器人自己的消息，"
                "会进入回声循环。请重新跑 `guanlan im-login --platform weixin` 生成凭据。",
                exit_code=EXIT_USAGE,
            )
        self._token = str(account["token"]).strip()
        self._base_url = str(account.get("base_url") or BASE_URL).rstrip("/")
        self._cursor = str(
            (read_json(self._sync_path) or {}).get("get_updates_buf") or ""
        )
        self._context_tokens = {
            str(k): str(v) for k, v in (read_json(self._ctx_path) or {}).items() if v
        }
        kwargs: dict = {
            "base_url": self._base_url,
            "headers": _headers(self._token),
            # 长轮询要 35s 不算超时；`read` 留足余量。
            "timeout": httpx.Timeout(LONGPOLL_SECONDS + 15.0, connect=10.0),
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._client = httpx.AsyncClient(**kwargs)

    async def close(self) -> None:
        if self._dropped_groups:
            _logger.debug("本次运行共丢弃 %d 条 iLink 群消息", self._dropped_groups)
        await self._persist_cursor()
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    # `write_json_atomic` = mkstemp + json.dump + **fsync** + os.replace。在事件循环线程上直接跑
    # 就是一次同步磁盘同步写，且它在每条带 context_token 的入站消息上都会发生——**违背本包的
    # §4.3 卸线程通则**（决策P4.21-35），一次慢盘就把所有人的消息一起卡住。故一律卸线程。
    async def _persist_cursor(self) -> None:
        if self._cursor:
            with contextlib.suppress(OSError):
                await anyio.to_thread.run_sync(
                    write_json_atomic,
                    self._sync_path,
                    {"get_updates_buf": self._cursor},
                )

    async def _persist_context_tokens(self) -> None:
        snapshot = dict(self._context_tokens)  # 快照：落盘期间 dict 仍可能被改
        with contextlib.suppress(OSError):
            await anyio.to_thread.run_sync(write_json_atomic, self._ctx_path, snapshot)

    async def _post(self, endpoint: str, body: dict) -> dict:
        assert self._client is not None, "适配器未 start()"
        resp = await self._client.post(endpoint, json=_with_base_info(body))
        resp.raise_for_status()
        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # ── 入站 ────────────────────────────────────────────────────────────────
    async def inbound(self) -> AsyncIterator[InboundMessage]:
        """长轮询 35s。**超时不是错误**——返回空并立即重轮询（§7.2）。

        **可取消义务（决策P4.21-56）**：本迭代器只阻塞在 HTTP 读与 `sleep` 上，外部
        `task.cancel()` 即可让它干净结束。被取消在 `getupdates` 半途 → 该批事件的 offset 未推进
        → 下次启动会重投；这是可接受的 at-least-once：**它们本来就没被回答过**。
        """
        while True:
            # ★ 每轮先让出一次事件循环：**服务端若一直立即返回空批**（配置错、或本地假件），
            # 整个循环里就可能没有任何真正挂起的 await——协程不让出，`task.cancel()` 的
            # 回调就永远轮不上，「可取消义务」名存实亡。一次 `sleep(0)` 换一个硬保证。
            await asyncio.sleep(0)
            try:
                data = await self._post(
                    EP_GETUPDATES, {"get_updates_buf": self._cursor}
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — 网络抖动不该终结收流
                await self._on_poll_failure()
                continue
            if is_stale_session(
                data.get("ret"), data.get("errcode"), data.get("errmsg")
            ):
                # **入站**方向的 stale = **登录会话失效**（§7.4 第一行）。
                _logger.error(
                    "微信登录会话已失效，暂停 %g 秒后重试；请运行 `guanlan im-login --platform weixin` 重新扫码。",
                    STALE_SESSION_PAUSE,
                )
                await self._sleep(STALE_SESSION_PAUSE)
                continue
            self._failures = 0
            cursor = data.get("get_updates_buf")
            for raw in data.get("msgs") or []:
                if not isinstance(raw, dict):
                    continue
                if raw.get("room_id") or raw.get("chat_room_id"):
                    self._dropped_groups += 1
                token = str(raw.get("context_token") or "")
                sender = str(raw.get("from_user_id") or "")
                # 只在**真的变了**时才落盘：token 在一段会话里是稳定的，逐条重写整张表等于给每条
                # 消息挂一次 fsync，纯浪费（内容一模一样）。
                if token and sender and self._context_tokens.get(sender) != token:
                    self._context_tokens[sender] = token
                    await self._persist_context_tokens()
                msg = map_inbound(raw, account_id=self._account_id)
                if msg is not None:
                    yield msg
            # ★ offset 在**事件全部交出去之后**才推进：取消点上不留半提交状态。
            if cursor:
                self._cursor = str(cursor)
                await self._persist_cursor()

    async def _on_poll_failure(self) -> None:
        self._failures += 1
        delay = (
            FAILURE_BACKOFF_SECONDS
            if self._failures >= FAILURE_THRESHOLD
            else FAILURE_SHORT_BACKOFF
        )
        _logger.warning(
            "微信长轮询失败（连续 %d 次），%g 秒后重试",
            self._failures,
            delay,
            exc_info=True,
        )
        await self._sleep(delay)
        if self._failures >= FAILURE_THRESHOLD:
            self._failures = 0  # 退避一次后清零计数（§7.4 末行）

    # ── 出站 ────────────────────────────────────────────────────────────────
    async def send(self, chat_id: str, text: str) -> OutboundRef:
        async with self._send_gate:  # 出站串行化（§7.4）
            await self._respect_circuit()
            await self._send_locked(chat_id, text)
        return OutboundRef(chat_id=chat_id, message_id="")  # 不支持 edit → 回空串

    async def _respect_circuit(self) -> None:
        remaining = self._circuit_until - self._clock()
        if remaining > 0:
            await self._sleep(remaining)

    async def _send_locked(self, chat_id: str, text: str) -> None:
        dropped_token = False
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            token = self._context_tokens.get(chat_id)
            body = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": chat_id,
                    "client_id": uuid.uuid4().hex,
                    "message_type": MESSAGE_TYPE_BOT,
                    "message_state": MESSAGE_STATE_FINISH,
                    "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
                }
            }
            if token:
                body["msg"]["context_token"] = token  # 缺失时**不**把该键塞进 payload
            data = await self._post(EP_SENDMESSAGE, body)
            ret, errcode, errmsg = (
                data.get("ret"),
                data.get("errcode"),
                data.get("errmsg"),
            )
            if not _is_error(ret, errcode):
                return
            if is_stale_session(ret, errcode, errmsg):
                if token and not dropped_token:
                    # **出站** stale 且本次**带了** token 且尚未重试 → 该 peer 的 context_token
                    # 陈旧：丢掉它、**去 token 重试一次**（iLink 接受无 token 发送作降级）。
                    # **不停服、不要求重登录**——原设计在这里错误停服 10 分钟（决策P4.21-30）。
                    self._context_tokens.pop(chat_id, None)
                    await self._persist_context_tokens()
                    dropped_token = True
                    continue
                raise GuanlanError(
                    f"微信出站被拒且已去 token 重试过（ret={ret} errcode={errcode} errmsg={errmsg!r}），"
                    "多半是登录会话真的失效了：请运行 `guanlan im-login --platform weixin`。",
                    exit_code=EXIT_AGENT_ERROR,
                )
            # 到这里就是**真频控**：3× 退避重试，累计触发熔断。
            if attempt < RATE_LIMIT_ATTEMPTS - 1:
                await self._sleep(RATE_LIMIT_BASE_BACKOFF * (attempt + 1))
                continue
            self._circuit_until = self._clock() + CIRCUIT_BREAK_SECONDS
            raise GuanlanError(
                f"微信出站持续被频控（errmsg={errmsg!r}），熔断 {CIRCUIT_BREAK_SECONDS:g} 秒。",
                exit_code=EXIT_AGENT_ERROR,
            )

    async def edit(
        self, ref: OutboundRef, text: str, *, finalize: bool = False
    ) -> None:
        """个人微信不支持 edit（`caps.supports_edit=False`），核心永不调到这里。"""
        raise NotImplementedError("个人微信不支持消息编辑（caps.supports_edit=False）")

    async def send_actions(self, chat_id: str, text: str, actions: list) -> None:
        """个人微信无可点面（`caps.supports_actions=False`），核心永不调到这里（P4.22 §6.5）。

        与 `edit` / `typing` 的处置完全对称：抛异常只为"契约被违反时立刻炸"，不是运行期路径。
        """
        raise NotImplementedError("个人微信无可点面（caps.supports_actions=False）")

    async def typing(self, chat_id: str, on: bool) -> None:
        ticket = await self._typing_ticket(chat_id)
        if not ticket:
            return
        await self._post(
            EP_SENDTYPING,
            {
                "ilink_user_id": chat_id,
                "typing_ticket": ticket,
                "status": TYPING_START if on else TYPING_STOP,
            },
        )

    async def _typing_ticket(self, chat_id: str) -> str:
        cached = self._typing_tickets.get(chat_id)
        now = self._clock()
        if cached and now < cached[1]:
            return cached[0]
        data = await self._post(EP_GETCONFIG, {"ilink_user_id": chat_id})
        ticket = str(data.get("typing_ticket") or "")
        if ticket:
            self._typing_tickets[chat_id] = (ticket, now + TYPING_TICKET_TTL)
        return ticket


def _is_error(ret: object, errcode: object) -> bool:
    """iLink 的成功判据：`ret`/`errcode` 都不是非零数字即成功。"""
    for value in (ret, errcode):
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value != 0
        ):
            return True
    return False


# ── 扫码登录（§7.6，决策P4.21-23）────────────────────────────────────────────
# 以下四个常量全部来自**真机实测**（§11 步骤 4；实测记录见 §13-3），非推断：
#
# | 观测 | 结论 |
# |---|---|
# | 不带 `bot_type` → `{"err_msg":"missing bot_type","ret":1}`；放 body 里**照样报 missing** | 它是 **query 参数**，不是请求体字段 |
# | `bot_type` 逐值试：仅 `3` 回 `ret:0`，其余（0/1/2/4…100、字符串）均 `invalid bot_type` | `BOT_TYPE = 3` |
# | `get_qrcode_status` 未扫时**挂满 30s** 才回 `{"ret":0,"status":"wait"}` | 是**长轮询**，读超时必须 > 30s |
# | `qrcode_img_content` = `https://liteapp.weixin.qq.com/q/...?qrcode=…&bot_type=3` | **是可扫 URL**，§13-3 存疑项就此关闭 |
BOT_TYPE = 3
# 扫码确认后 `get_qrcode_status` 的**实测响应**（`ret=0`）：
#   {"status":"confirmed", "bot_token":"<botid>:<hex>", "ilink_bot_id":"<12字符>@im.bot",
#    "ilink_user_id":"<38字符>", "baseurl":"https://ilinkai.weixin.qq.com"}
# 原实现认的是猜出来的 `token`/`account_id`，**一个都不对**——见 `_poll_qrcode` 里那条硬错误。
STATUS_CONFIRMED = "confirmed"
FIELD_TOKEN = "bot_token"
FIELD_BOT_ID = "ilink_bot_id"
QRCODE_LONGPOLL_SECONDS = 30.0
QRCODE_POLL_INTERVAL = 2.0
QRCODE_MAX_REFRESH = 3
# 单个二维码的等待上限。服务端「已过期」的响应形态**从未被观测到**（真机实测时二维码在扫掉前
# 一直是 `wait`），故不能只依赖 `status == "expired"` 收场——否则用户扫码前走开，`_poll_qrcode`
# 就永远 `wait` 下去，终端一片安静，正是决策P4.21-57 要杜绝的活死人。加一道墙钟兜底。
QRCODE_WAIT_SECONDS = 180.0


async def run_qrcode_login(state: Path, *, transport: object | None = None) -> int:
    """`guanlan im-login --platform weixin`：扫码拿 token 并落 `account.json`。

    原设计写「不内建扫码向导」——**推翻**（决策P4.21-23）：没有扫码流程，本适配器对没跑过参考
    实现的用户**完全不可用**；装了就用不了不叫有界，叫没做完。

    > **`qrcode_img_content` 的形态已由真机实测确定**（§13-3 关闭）：是可扫 URL
    > （`https://liteapp.weixin.qq.com/q/...`）。仍**两路都兜**——像 URL 就用可选依赖 `qrcode`
    > 在终端画码（缺则打印 URL），否则打印原始值并给指引。**绝不因为呈现不了就失败**：
    > 至少要让操作者拿到可自行处理的原始值。
    """
    import httpx

    # 读超时必须**大于** `get_qrcode_status` 的 30s 长轮询，否则每一轮都在服务端正要回话时
    # 被自己掐断（沿用入站长轮询同款的 +15s 余量）。
    kwargs: dict = {
        "base_url": BASE_URL,
        "timeout": httpx.Timeout(QRCODE_LONGPOLL_SECONDS + 15.0, connect=10.0),
    }
    if transport is not None:
        kwargs["transport"] = transport
    async with httpx.AsyncClient(**kwargs) as client:
        for refresh in range(QRCODE_MAX_REFRESH):
            # `bot_type` 走 **query**：放进请求体服务端读不到，照样回 `missing bot_type`。
            resp = await client.post(
                EP_QRCODE, json=_with_base_info({}), params={"bot_type": BOT_TYPE}
            )
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            code = str(data.get("qrcode") or "")
            if not code:
                # **把服务端原话打出来**：只说"未返回 qrcode 字段"等于把唯一的线索
                # （`{"err_msg":"missing bot_type","ret":1}`）丢掉，操作者除了重试无事可做。
                detail = str(data.get("err_msg") or data.get("errmsg") or "").strip()
                ret = data.get("ret")
                suffix = (
                    f"（服务端：{detail}；ret={ret}）"
                    if detail or ret is not None
                    else ""
                )
                print(f"获取二维码失败：服务端未返回 qrcode 字段{suffix}。", flush=True)
                return EXIT_AGENT_ERROR
            _present_qrcode(str(data.get("qrcode_img_content") or ""), code)
            status = await _poll_qrcode(client, code)
            if status is None:  # 过期 → 刷新重来
                print(
                    f"二维码已过期或等待超时，正在刷新（{refresh + 1}/{QRCODE_MAX_REFRESH}）…",
                    flush=True,
                )
                continue
            bot_id = str(status.get(FIELD_BOT_ID) or "").strip()
            if not bot_id:
                # 与上面 `FIELD_TOKEN` 那条**同一条判据**：拿不到就必须炸得响。写一份
                # `account_id: ""` 的凭据出去，下次起服时自消息过滤整条失效 → **回声循环**，
                # 而现场看到的只是"机器人自言自语"，与登录这一步毫无关联。
                raise GuanlanError(
                    "扫码已确认，但响应里没有 `%s` 字段——服务端大概改了字段名。"
                    "少了它就无法过滤机器人自己的消息（会回声循环），故拒绝写入凭据。"
                    "本次响应的键：%s。请据此更新 `FIELD_BOT_ID`（见 §7.6 实测表）。"
                    % (FIELD_BOT_ID, sorted(status)),
                    exit_code=EXIT_AGENT_ERROR,
                )
            write_json_atomic(
                state / "account.json",
                {
                    # `ilink_bot_id` 形如 `<12字符>@im.bot`，**正是入站帧里 `to_user_id` 的那个
                    # 身份**——自消息过滤（`from_user == account_id`）靠它，取错就会回声循环。
                    # 同响应里的 `ilink_user_id` 是**扫码那个人**的 id，不是机器人，别混。
                    # 空值已在上面拒掉，绝不落一份"过滤失效"的凭据。
                    "account_id": bot_id,
                    "token": str(status.get(FIELD_TOKEN) or ""),
                    # 服务端会自报 `baseurl`（实测与常量同值）。**以服务端为准**：它要分片或迁域名时
                    # 这是唯一的通知渠道，钉死常量等于把自己焊在当前那台机器上。
                    "base_url": str(status.get("baseurl") or "").strip() or BASE_URL,
                },
            )
            print(
                f"✓ 登录成功，凭据已写入 {state / 'account.json'}（权限 0600）。",
                flush=True,
            )
            return 0
    print("二维码连续过期，已放弃。请稍后重试。", flush=True)
    return EXIT_AGENT_ERROR


def _present_qrcode(img_content: str, code: str) -> None:
    """**默认在终端里直接画出二维码**，用手机微信对着屏幕扫即可，不必打开任何链接。

    退回打印 URL 是**下策而非等价选择**：那个 URL 就是登录凭据本身，把它变成能扫的码，
    要么贴进第三方生成器（等于把登录链接交给外人），要么想办法挪到手机上。故 `qrcode`
    已从"可选增强"提为 `[im-weixin]` 的正式依赖（纯 Python、无运行时依赖）。
    """
    if not img_content.startswith("http"):
        print("服务端返回的二维码内容不是链接，原样打印如下：", flush=True)
        print(f"  qrcode = {code}", flush=True)
        if img_content:
            print(f"  qrcode_img_content = {img_content[:200]}…", flush=True)
        return
    if not _draw_qrcode(img_content):
        # 画不出来不是失败，但要说清**为什么**——否则用户只看到一行光秃秃的 URL。
        print(f"（无法在终端画码）请扫描此链接对应的二维码：{img_content}", flush=True)
        return
    print(
        f"请用手机微信扫描上方二维码登录。（同一内容的链接：{img_content}）", flush=True
    )


def _draw_qrcode(url: str) -> bool:
    """把 `url` 画成终端二维码。返回 False = 没画（调用方退回打印 URL）。"""
    try:
        import qrcode
    except ImportError:
        print(
            "终端画码需要 `qrcode` 包：`pip install qrcode`（或重装 `guanlan-wiki[im-weixin]`）。",
            flush=True,
        )
        return False
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    try:
        qr.make()
    except Exception:  # noqa: BLE001 — 编不出码就退回 URL，不能让登录流程崩在这儿
        return False
    # 窄终端会把每一行折行，画出来的是一团**扫不出来的乱码**——而用户完全看不出哪里不对，
    # 只会以为"码坏了"。宁可不画。
    width = qr.modules_count + 2 * qr.border
    if shutil.get_terminal_size(fallback=(80, 24)).columns < width:
        print(f"（终端宽度不足 {width} 列，跳过画码以免折行成乱码）", flush=True)
        return False
    # `tty=True` 让 SDK 显式写死前景白 / 背景黑的 ANSI 色，于是**深浅色主题的终端里都扫得出**；
    # 只用 `invert=True` 的话，浅色主题下黑白正好反过来，多数扫码器认不出。
    # 非 tty（重定向 / 被 pytest 捕获）传 `tty=True` 会抛 OSError，故按实际情况降级。
    is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    try:
        qr.print_ascii(invert=True, tty=is_tty)
    except Exception:  # noqa: BLE001
        return False
    return True


async def _poll_qrcode(
    client, code: str, *, clock: Callable[[], float] = time.monotonic
) -> dict | None:
    """长轮询直到确认；过期（或等满 `QRCODE_WAIT_SECONDS`）返回 `None`。

    服务端每次挂满 30s 才回一次 `{"ret":0,"status":"wait"}`，故这里**不是**忙轮询——
    `QRCODE_POLL_INTERVAL` 只是两次长轮询之间的喘息。
    """
    import httpx

    deadline = clock() + QRCODE_WAIT_SECONDS
    while clock() < deadline:
        try:
            resp = await client.get(EP_QRCODE_STATUS, params={"qrcode": code})
        except httpx.ReadTimeout:
            # 长轮询被本地读超时掐断是**预期内**的（服务端也可能挂超过 30s），重来即可，
            # 不该让整个登录流程带着 traceback 崩掉。
            continue
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        state = str(data.get("status") or data.get("qrcode_status") or "").lower()
        if data.get(FIELD_TOKEN):
            return data
        if state == STATUS_CONFIRMED:
            # 已确认却取不到 token = **字段又改名了**。这里必须炸得响：原实现认的是猜出来的
            # `token`，真实字段是 `bot_token`，于是扫完码一切如常、只是永远 `wait` 下去
            # ——用户看到的是"扫了没反应"，日志一个字都没有。**这条静默失败不能再来一次。**
            raise GuanlanError(
                "扫码已确认，但响应里没有 `%s` 字段——服务端大概改了字段名。"
                "本次响应的键：%s。请据此更新 `FIELD_TOKEN`（见 §7.6 实测表）。"
                % (FIELD_TOKEN, sorted(data)),
                exit_code=EXIT_AGENT_ERROR,
            )
        if state in {"expired", "timeout"} or data.get("expired"):
            return None
        await asyncio.sleep(QRCODE_POLL_INTERVAL)
    return None
