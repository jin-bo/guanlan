"""入站流水线：去重 / 白名单 / @判定 / 分片合并 / 斜杠分流（P4.21 §5，全部平台无关）。

**卸线程通则（§4.3，决策P4.21-35）**：本包里一切同步的磁盘 / CPU / 阻塞网络路径都必须
`await anyio.to_thread.run_sync(...)`，否则一次调用卡住整个事件循环、**所有人的消息都收不到**。
判据不是"这个调用看起来慢不慢"，而是**"它会不会去拿一把别人可能长期持有的锁"**
——`ConversationStore.get()` 正是栽在这一条上（它与 `create()` 共用同一把 `threading.Lock`）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .contract import CHAT_GROUP, KIND_TEXT, ORIGIN_ACTION, AdapterCaps, InboundMessage

_logger = logging.getLogger("guanlan.im")

# 去重表：`msg_id` → 首见时刻。漏则**双倍 LLM 花费**（两家平台都会重投）。
DEDUP_TTL_SECONDS = 300.0
DEDUP_MAX_ENTRIES = 4096
DEDUP_KEEP_ENTRIES = DEDUP_MAX_ENTRIES * 3 // 4  # 剪枝后的水位：留余量，见 `_prune`

# 群内 @ 前缀剥除：否则 `@bot /search x` 识别不出斜杠命令（§5.1 第 3 道闸）。
_MENTION_PREFIX = re.compile(r"^(?:@\S+\s*)+")

# 零 LLM 的斜杠命令（§5.1 第 5 道闸）。`attachment` 不是用户能打出来的，由 `msg_kind` 触发。
COMMANDS = ("search", "new", "help", "page")

# ★ **来源白名单**（P4.22 §5.5，决策P4.22-14）：`origin == ORIGIN_ACTION` 的合成消息**只能**
# 跑这几条命令，其余一律静默丢弃。**这是红线 1（按钮永不承载写操作）的唯一有效闸**，因为
# 它必须在**接收端**：`classify()` 对**未知**斜杠命令并不拒绝，而是当普通提问返回
# `(None, text)`（见下），于是一个被改动过的回传串（经平台服务端往返 = 外部输入）里塞
# `/foo 帮我写一篇…` 就成了一次不该发生的 LLM 调用。只在构造 `Action` 那一端"自觉"是挡不住的。
#
# **必须是 `COMMANDS` 的真子集**：`/help` `/new` 都是零 LLM 的，却都**不在**这里——
# 前者是噪音、后者会**清掉用户的上下文**，都不该由一次误点/伪造回传触发。
ACTION_COMMANDS = ("page",)


@dataclass(frozen=True)
class AccessPolicy:
    """授权真值表（§5.2，决策P4.21-33/39）。

    | 场景 | 放行条件 |
    |---|---|
    | 单聊 | `allow_all` **OR** `user_id ∈ allow_users` |
    | 群聊 | `chat_id ∈ allow_chats` **AND** (`allow_all` **OR** `user_id ∈ allow_users`) **AND** `mentioned_me` |

    **① 群聊里 `user` 与 `chat` 是 AND**：群成员集合由**群主**控制且随时变化——只查群等于把授权
    外包给群主，一次拉人就把整库开给了新成员。

    **② `--allow-all-users` 只旁路「用户」名单，永不旁路「群」名单**（决策P4.21-39）：
    `allow_chats` 是**受众物理边界**（部署者把机器人放进哪个房间），`allow_users` 是**人员边界**；
    该旗标表达"我不想逐个列人"，**不表达**"我不在乎它在哪个群里"。故**没有任何旗标能一次开放
    所有群**，这是有意的。

    **ID 精确匹配、大小写敏感、不剥前缀**：平台 ID 是**不透明标识**（飞书 `open_id` 是大小写敏感
    的编码串），`.lower()` 归一**可能把两个不同主体合并**。只在构造时 `.strip()`。
    """

    allow_users: frozenset[str]
    allow_chats: frozenset[str]
    allow_all: bool = False

    def permits(self, msg: InboundMessage) -> bool:
        user_ok = self.allow_all or msg.user_id in self.allow_users
        if msg.chat_type == CHAT_GROUP:
            return msg.chat_id in self.allow_chats and user_ok and msg.mentioned_me
        return user_ok


@dataclass(frozen=True)
class Ready:
    """过完五道闸、可以投递的一次请求。"""

    session_key: str
    chat_id: str
    text: str
    command: str | None  # "search"/"new"/"help"/"page"/"attachment"；None = 走 LLM
    argument: str = ""


@dataclass
class _Bucket:
    """一个会话键的分片合并缓冲。"""

    parts: list[str] = field(default_factory=list)
    chat_id: str = ""
    task: asyncio.Task | None = None


def session_key_of(msg: InboundMessage) -> str:
    """会话键 = `tenant:chat_id:user_id`（决策P4.21-18）。

    群里两个人各自有独立会话历史（同 chat 不同 user）；跨租户不撞号（tenant 打头）。
    """
    return f"{msg.tenant}:{msg.chat_id}:{msg.user_id}"


def strip_mention(text: str) -> str:
    """剥掉群消息开头的 @ 前缀。"""
    return _MENTION_PREFIX.sub("", text, count=1).lstrip()


def classify(text: str, *, msg_kind: str, has_attachments: bool) -> tuple[str | None, str]:
    """第 5 道闸：把一条已剥前缀的正文分流成 `(command, argument)`。

    非文本 / 带附件 → `("attachment", "")`：固定提示、零 LLM（决策P4.21-36）。
    `/search` `/new` `/help` `/page` → 对应命令（零 LLM，**不建会话、不烧 token**）。
    其余 → `(None, text)` 走 LLM——**包括未知的斜杠命令**。这条"未知即提问"是有意的
    （用户打错命令名不该被当哑巴），但它也意味着"以 `/` 开头"**不构成任何安全闸**：
    合成消息的只读约束由 `ACTION_COMMANDS` 在 `offer()` 里单独兜（决策P4.22-14）。
    """
    if msg_kind != KIND_TEXT or has_attachments:
        return "attachment", ""
    stripped = text.strip()
    if stripped.startswith("/"):
        head, _, rest = stripped[1:].partition(" ")
        name = head.strip().lower()
        if name in COMMANDS:
            return name, rest.strip()
    return None, stripped


class Intake:
    """五道闸 + 分片合并。`offer()` **绝不阻塞在应答上**——否则一个 90 秒的问答会堵住整条入站流。"""

    def __init__(
        self,
        caps: AdapterCaps,
        policy: AccessPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._caps = caps
        self._policy = policy
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._buckets: dict[str, _Bucket] = {}
        self._sink: Callable[[Ready], None] | None = None
        # 未授权 / 群消息的丢弃计数：debug 级汇总，不逐条刷屏（§7.2 群消息同款）。
        self.dropped_unauthorized = 0
        self.dropped_duplicate = 0

    def set_sink(self, on_ready: Callable[[Ready], None]) -> None:
        """接 `Delivery.submit`——**同步**（建 task + 登记，不 await），使 intake 永不阻塞（§4.4）。"""
        self._sink = on_ready

    def set_caps(self, caps: AdapterCaps) -> None:
        """`adapter.start()` **之后**重读一次能力位（★ 决策P4.22-27）。

        `Intake` 是在 `start()` 之前构造的（`serve_im` 装配期），而适配器**会在 `start()` 里就地
        降级**能力位（如飞书探不到卡片回调注册面 → 关掉 `supports_actions`）。`Delivery` 每次派发
        都现读 `self._adapter.caps`，故天然跟得上；`Intake` 拿的是**构造期快照**，跟不上。

        今天恰好无害——降级只动 `supports_actions`/`max_actions`，而 `Intake` 只读长度与节流阈值。
        但"下一个会在 `start()` 里降级、且恰好被 `Intake` 读到的能力位"（`max_message_length` /
        `batch_delay_s` …）会**静默**用陈旧值，且没有任何用例会红。这个方法把那类 bug 一次性掐掉。
        """
        self._caps = caps

    # ── 闸① 去重 ────────────────────────────────────────────────────────────
    def _is_duplicate(self, msg_id: str) -> bool:
        now = self._clock()
        first = self._seen.get(msg_id)
        if first is not None and now - first <= DEDUP_TTL_SECONDS:
            return True
        self._seen[msg_id] = now
        if len(self._seen) > DEDUP_MAX_ENTRIES:
            self._prune(now)
        return False

    def _prune(self, now: float) -> None:
        """先按 TTL 清，仍超界则按首见时刻最旧优先剪**到留有余量的水位**。

        余量（`DEDUP_KEEP_ENTRIES < DEDUP_MAX_ENTRIES`）不是可有可无的调优：剪到恰好等于上界
        的话，下一条消息又会 +1 越界 → **此后每条消息都要对全表排一次序**（O(n log n)，n=4096）。
        留 1/4 余量后，排序摊薄到每 1024 条消息一次。
        """
        for k in [k for k, t in self._seen.items() if now - t > DEDUP_TTL_SECONDS]:
            self._seen.pop(k, None)
        if len(self._seen) > DEDUP_KEEP_ENTRIES:
            for k, _ in sorted(self._seen.items(), key=lambda kv: kv[1])[
                : len(self._seen) - DEDUP_KEEP_ENTRIES
            ]:
                self._seen.pop(k, None)

    # 注：**刻意没有内容指纹去重**（决策P4.21-34）。原设计照抄参考实现的 `content:{user}:{md5}`
    # 兜底，但它**无法区分「平台换 id 重投」与「用户合法重复」**，而后者在 IM 里很常见（重复
    # `/help`、追问同一问题）。**静默吞掉合法消息比偶尔重跑一次更糟**——前者用户完全无从察觉。
    # `tests/test_im.py` 有一条**反向用例**守着这个决策不被后人好心加回来。

    async def offer(self, msg: InboundMessage) -> None:
        """过五道闸；到期的合并结果经 `sink` 同步投递。"""
        if self._is_duplicate(msg.msg_id):  # 闸①
            self.dropped_duplicate += 1
            return
        if not self._policy.permits(msg):  # 闸②：**静默丢弃**，不回「你没有权限」
            # 回一句就把「这里有个知识库机器人」泄露给了未授权者（§5.1）。
            self.dropped_unauthorized += 1
            _logger.debug("丢弃未授权消息：chat_type=%s", msg.chat_type)
            return
        text = strip_mention(msg.text) if msg.chat_type == CHAT_GROUP else msg.text  # 闸③
        key = session_key_of(msg)
        command, argument = classify(  # 闸⑤（先于合并：命令不该被合进下一条正文）
            text, msg_kind=msg.msg_kind, has_attachments=msg.has_attachments
        )
        if msg.origin == ORIGIN_ACTION and command not in ACTION_COMMANDS:  # 闸⑤′
            # **静默丢弃，绝不落进 LLM 分支**（决策P4.22-14）。走 `dropped_unauthorized`
            # 而不是另开一个计数器：它就是"这条消息没被授权做这件事"，语义同源。
            self.dropped_unauthorized += 1
            _logger.debug("丢弃越权的合成消息：command=%s", command)
            return
        if command is not None:
            await self._flush(key)  # 命令到达即先把缓冲里的散文送走，保序
            self._emit(Ready(key, msg.chat_id, text, command, argument))
            return
        await self._offer_text(key, msg.chat_id, argument)  # 闸④

    # ── 闸④ 分片合并（quiet-period 双阈值，阈值取自 caps）─────────────────────
    async def _offer_text(self, key: str, chat_id: str, text: str) -> None:
        bucket = self._buckets.setdefault(key, _Bucket())
        bucket.chat_id = chat_id
        bucket.parts.append(text)
        if bucket.task is not None:
            bucket.task.cancel()
        # 上一片接近上限 → 用加长静默期（用户多半还在打下一片）。
        near_limit = len(text) >= self._caps.max_message_length * 0.9
        delay = self._caps.batch_split_delay_s if near_limit else self._caps.batch_delay_s
        bucket.task = asyncio.create_task(self._debounce(key, delay))

    async def _debounce(self, key: str, delay: float) -> None:
        current = asyncio.current_task()
        await asyncio.sleep(delay)  # （原先裹了一层 `except CancelledError: raise` —— 纯空转）
        # ★ cancel-delivery 竞态守卫（决策P4.21-11）：sleep 定时器与 `cancel()` **同时**触发时，
        # `CancelledError` 会延迟到下一个 await 才投递；届时本 task 已把事件 pop 走，后继 task
        # 看到空桶而**静默丢消息**。故 sleep 之后**同步**复查"我还是当前 task 吗"，
        # 中间**不得有任何 await**。
        bucket = self._buckets.get(key)
        if bucket is None or bucket.task is not current:
            return
        await self._flush(key)

    async def _flush(self, key: str) -> None:
        bucket = self._buckets.pop(key, None)
        if bucket is None or not bucket.parts:
            return
        if bucket.task is not None and bucket.task is not asyncio.current_task():
            bucket.task.cancel()
        text = "\n".join(p for p in bucket.parts if p)
        if text:
            self._emit(Ready(key, bucket.chat_id, text, None, text))

    def _emit(self, ready: Ready) -> None:
        if self._sink is not None:
            self._sink(ready)

    async def drain(self, *, flush: bool = False) -> None:
        """停机用（§4.4 步骤②）。默认 `flush=False` ＝ **丢弃**未到期的 debounce 缓冲。

        半条消息不该在关机时触发一轮 LLM 花费。`flush=True` 仅供测试/未来的"优雅收尾"语义。
        """
        # 丢弃计数在这里**汇总打一次**（§7.2 的"不逐条刷屏"是指不逐条，不是指不打）：
        # 两个计数器本来一路只加不读，等于白记——而"发了没人理"时它们正是唯一能自证
        # 「消息收到了、是被闸挡掉的」的证据。
        if self.dropped_unauthorized or self.dropped_duplicate:
            _logger.debug(
                "本次运行共丢弃：未授权 %d 条、重复 %d 条",
                self.dropped_unauthorized,
                self.dropped_duplicate,
            )
        keys = list(self._buckets)
        for key in keys:
            bucket = self._buckets.get(key)
            if bucket is None:
                continue
            if bucket.task is not None and bucket.task is not asyncio.current_task():
                bucket.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bucket.task
            if flush:
                await self._flush(key)
            else:
                self._buckets.pop(key, None)
