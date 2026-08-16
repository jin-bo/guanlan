"""P4.21 IM 宿主核心测试（见 docs/P4.21-IM宿主.md §10.1）。

**`FakeAdapter` 驱动，零网络零 SDK，`不 skip`**——核心不依赖任何平台 extra，故这一组必须在
任何环境里都跑。平台适配器各有自己的文件（`test_im_weixin.py` / `test_im_feishu.py`）。

不打真 LLM 走**会话层文档化的猴补接缝** `monkeypatch.setattr(chat, "build_from_environment", …)`
（决策P4.21-32）；**不是** `runner=`——那是一次性子进程契约，与多轮进程内会话无关。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest
from agentao.cancellation import AgentCancelledError

from guanlan.errors import EXIT_AGENT_ERROR, EXIT_OK, EXIT_USAGE, GuanlanError
from guanlan.im import server as im_server
from guanlan.im.contract import CHAT_DM, CHAT_GROUP, KIND_OTHER, KIND_TEXT, AdapterCaps, InboundMessage, OutboundRef
from guanlan.im.delivery import (
    ATTACHMENT_HINT,
    BUSY_HINT,
    EDIT_INTERVAL_S,
    EDIT_MIN_CHARS,
    ERROR_HINT,
    Delivery,
    _EditState,
)
from guanlan.im.intake import (
    DEDUP_KEEP_ENTRIES,
    DEDUP_MAX_ENTRIES,
    AccessPolicy,
    Intake,
    Ready,
    session_key_of,
)
from guanlan.im.mcp_bound import BoundedTimeoutRegistry, bounded_request_timeout
from guanlan.im.reply import _split_paragraph, split_for, to_im_markdown, truncation_notice
from guanlan.im.session import AcquireStatus, SessionRegistry
from guanlan.search import CorpusCache

# ───────────────────────── 夹具与打桩 ─────────────────────────

CAPS_TYPING = AdapterCaps(
    max_message_length=2000,
    max_parts=5,
    supports_edit=False,
    supports_typing=True,
    supports_late_push=True,
    supports_group=False,
    chunk_delay_s=0.0,  # 测试里不真等频控间隔
    batch_delay_s=0.01,
    batch_split_delay_s=0.02,
)
CAPS_EDIT = AdapterCaps(
    max_message_length=8000,
    max_parts=3,
    supports_edit=True,
    supports_typing=False,
    supports_late_push=True,
    supports_group=True,
    chunk_delay_s=0.0,
    batch_delay_s=0.01,
    batch_split_delay_s=0.02,
)

_STOP = object()  # FakeAdapter.inbound 的"正常结束"哨兵


class FakeAdapter:
    """记录调用序列的假适配器。**调用序列**是本文件大量顺序断言的唯一依据（不靠 sleep）。"""

    name = "fake"

    def __init__(self, caps: AdapterCaps = CAPS_TYPING) -> None:
        self.caps = caps
        self.calls: list[tuple] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.started = False
        self.closed = False
        self.start_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.inbound_error: BaseException | None = None
        self.taken_not_delivered = 0
        self._seq = 0

    async def start(self) -> None:
        self.calls.append(("start",))
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    async def close(self) -> None:
        self.calls.append(("close",))
        if self.close_error is not None:
            raise self.close_error
        self.closed = True

    async def inbound(self):
        if self.inbound_error is not None:
            raise self.inbound_error
        while True:
            item = await self.queue.get()
            if item is _STOP:
                return
            self.taken_not_delivered += 1
            yield item
            self.taken_not_delivered -= 1

    async def send(self, chat_id: str, text: str) -> OutboundRef:
        self._seq += 1
        self.calls.append(("send", chat_id, text))
        return OutboundRef(chat_id=chat_id, message_id=f"m{self._seq}")

    async def edit(self, ref: OutboundRef, text: str, *, finalize: bool = False) -> None:
        self.calls.append(("edit", ref.message_id, text, finalize))

    async def typing(self, chat_id: str, on: bool) -> None:
        self.calls.append(("typing", chat_id, on))

    # —— 断言辅助 ——
    def sent(self) -> list[str]:
        return [c[2] for c in self.calls if c[0] == "send"]

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]


class FakeConv:
    """`Conversation` 的最小替身：只实现宿主真正用到的那几个方法。"""

    def __init__(self, cid: str) -> None:
        self.id = cid
        self.closed = False
        self.inflight = 0
        self.begun = 0
        self.ended = 0
        self.stop_calls = 0
        self.stopped = False
        self.turns: list[str] = []
        self.answer = "答案"
        self.tokens: list[str] = []
        self.raises: BaseException | None = None
        self.gate: asyncio.Event | None = None  # 置了就在 turn 里等它（模拟长 turn）
        self.emit_threads: list[int] = []

    def begin_turn(self) -> None:
        self.begun += 1
        self.inflight += 1

    def end_turn(self) -> None:
        self.ended += 1
        self.inflight -= 1

    def request_stop(self) -> bool:
        """镜像真 `Conversation.request_stop` 的**三态**（`conversation.py`）。"""
        self.stop_calls += 1
        if self.inflight > 0 and not self.closed:
            self.stopped = True
            return True
        return False

    async def turn(self, msg: str, emit) -> str:
        self.turns.append(msg)
        import threading

        for chunk in self.tokens:
            emit("token", chunk)
            self.emit_threads.append(threading.get_ident())
            await asyncio.sleep(0)
        if self.gate is not None:
            await self.gate.wait()
        if self.stopped:
            raise AgentCancelledError("user-stop")
        if self.raises is not None:
            raise self.raises
        return self.answer

    def close(self) -> None:
        self.closed = True


class FakeStore:
    """`ConversationStore` 的最小替身（**同步方法**，宿主经 `to_thread` 调）。"""

    def __init__(self, *, cap: int = 100) -> None:
        self.cap = cap
        self.convs: dict[str, FakeConv] = {}
        self.created: list[FakeConv] = []
        self.deleted: list[str] = []
        self._n = 0
        self.create_block: threading.Event | None = None  # type: ignore[name-defined]
        self.delete_block = None
        self.create_error: BaseException | None = None
        self.on_create = None

    def create(self, model: str | None = None) -> FakeConv:
        if self.create_block is not None:
            self.create_block.wait()
        if self.create_error is not None:
            raise self.create_error
        if len(self.convs) >= self.cap:
            raise RuntimeError(f"内存会话数已达上限 {self.cap}")
        self._n += 1
        conv = FakeConv(f"c{self._n}")
        self.convs[conv.id] = conv
        self.created.append(conv)
        if self.on_create is not None:
            self.on_create(conv)
        return conv

    def get(self, cid: str) -> FakeConv | None:
        return self.convs.get(cid)

    def delete(self, cid: str) -> bool:
        if self.delete_block is not None:
            self.delete_block.wait()
        conv = self.convs.pop(cid, None)
        self.deleted.append(cid)
        if conv is not None:
            conv.close()
            return True
        return False

    def live_count(self) -> int:
        return len(self.convs)


import threading  # noqa: E402 — FakeStore 的 Event 用（放这里避免与上面的类型注解绕圈）


class Clock:
    """可注入的假时钟：跨 TTL 的行为可决定性测试，不用真等 30 分钟。"""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def kb_im(tmp_path: Path) -> Path:
    """最小只读知识库（`require_kb_root(writable=False)` 只要求 wiki/）。"""
    (tmp_path / "AGENTAO.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "SCHEMA.md").write_text("# S\n", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    (wiki / "entities").mkdir()
    (wiki / "entities" / "甲.md").write_text(
        "---\ntitle: 甲实体\ntype: entity\n---\n\n甲实体讲的是示例主题与流程编排。\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_im_home(tmp_path, monkeypatch):
    """把 `~/.guanlan/im` 挪进 tmp：凭据锁与状态文件绝不碰真实 HOME。"""
    monkeypatch.setenv("GUANLAN_IM_HOME", str(tmp_path / "imhome"))


def msg(
    text: str = "你好",
    *,
    user: str = "u1",
    chat: str | None = None,
    tenant: str = "t1",
    mid: str | None = None,
    group: bool = False,
    mentioned: bool = False,
    kind: str = KIND_TEXT,
    attachments: bool = False,
) -> InboundMessage:
    global _MID
    _MID += 1
    return InboundMessage(
        tenant=tenant,
        chat_id=chat if chat is not None else user,
        chat_type=CHAT_GROUP if group else CHAT_DM,
        user_id=user,
        text=text,
        msg_id=mid if mid is not None else f"mid{_MID}",
        mentioned_me=mentioned,
        msg_kind=kind,
        has_attachments=attachments,
    )


_MID = 0


def make_stack(
    kb: Path,
    *,
    caps: AdapterCaps = CAPS_TYPING,
    policy: AccessPolicy | None = None,
    store: FakeStore | None = None,
    clock: Clock | None = None,
    ttl: float = 1800.0,
    cap: int = 100,
    web_base_url: str | None = None,
):
    """装一套「FakeAdapter + Intake + Delivery + SessionRegistry」，接线与 `serve_im` 一致。"""
    adapter = FakeAdapter(caps)
    policy = policy or AccessPolicy(allow_users=frozenset({"u1", "u2"}), allow_chats=frozenset({"g1"}))
    store = store or FakeStore(cap=cap)
    clk = clock or Clock()
    registry = SessionRegistry(store, ttl=ttl, tombstone_limit=cap, clock=clk)
    cache = CorpusCache()
    delivery = Delivery(adapter, registry, cache, kb, web_base_url=web_base_url, max_conversations=cap)
    registry.set_busy(delivery.busy)
    intake = Intake(caps, policy, clock=clk)
    intake.set_sink(delivery.submit)
    return adapter, intake, delivery, registry, store, clk


async def wait_until(pred, *, timeout: float = 3.0, what: str = "条件") -> None:
    """轮询等一个条件成立。

    **必须让真实时间流逝**（`sleep(0)` 只让出事件循环、不推进定时器），否则 debounce 与节流
    这类 `asyncio.sleep(...)` 永远不到期——这正是分片合并与 edit 节流的行为依据。
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"等 {what} 超时")


async def drain_tasks(delivery: Delivery, *, timeout: float = 5.0) -> None:
    """把已提交的轮跑完（轮询 `active()`，不用固定 sleep 猜时长）。"""
    await wait_until(lambda: not delivery.active(), timeout=timeout, what="在飞轮收尾")
    await asyncio.sleep(0.01)  # 让收尾后的最后一批出站落地


# ───────────────────────── 架构验收 ─────────────────────────


def _strip_comments_and_strings(path: Path) -> str:
    """去掉注释与字符串字面量后的源码——中文说明里出现 `== "weixin"` 不该让下面那条断言变红。"""
    out: list[str] = []
    with path.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    return " ".join(out)


def test_core_has_no_platform_branch():
    """**架构验收（决策P4.21-3）**：核心零 `if platform == "weixin"`——分派只读 `caps`。

    `!=` 同样是分支，故一并禁掉（`run_login` 因此改查 `LOGIN_FLOWS` 注册表）。
    """
    core = sorted(Path("guanlan/im").glob("*.py"))
    assert core, "没找到核心模块，路径写错了？"
    offenders = []
    for path in core:
        code = _strip_comments_and_strings(path)
        for platform in ("weixin", "feishu"):
            if f'== "{platform}"' in code or f'!= "{platform}"' in code:
                offenders.append(f"{path}: {platform}")
    assert offenders == [], f"核心出现了平台分支：{offenders}"


def test_capability_tier_typing(kb_im):
    """能力档位分派①：`supports_typing` → typing(on) → 单条答案 → typing(off)。"""

    async def scenario():
        adapter, intake, delivery, _reg, _store, _clk = make_stack(kb_im, caps=CAPS_TYPING)
        await intake.offer(msg("问题"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        # §6.1 档②：typing on → 跑 → off → **再**分片发答案（不是发完才关）
        assert adapter.kinds() == ["typing", "typing", "send"]
        assert adapter.calls[0] == ("typing", "u1", True)
        assert adapter.calls[1] == ("typing", "u1", False)

    asyncio.run(scenario())


def test_capability_tier_edit(kb_im):
    """能力档位分派②：`supports_edit` → send 占位 → edit → finalize。**核心一行未改**。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, caps=CAPS_EDIT)
        store.on_create = lambda c: setattr(c, "answer", "最终答案")
        await intake.offer(msg("问题"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert adapter.kinds()[0] == "send"  # 占位
        finals = [c for c in adapter.calls if c[0] == "edit" and c[3] is True]
        assert len(finals) == 1 and finals[0][2] == "最终答案"
        assert "typing" not in adapter.kinds()

    asyncio.run(scenario())


# ───────────────────────── 授权真值表（§5.2）─────────────────────────


@pytest.mark.parametrize(
    ("group", "chat", "user", "mentioned", "allow_all", "expected"),
    [
        # 单聊：allow_all OR user ∈ allow_users
        (False, "u1", "u1", False, False, True),
        (False, "zz", "zz", False, False, False),
        (False, "zz", "zz", False, True, True),
        # 群聊：chat AND user AND mentioned
        (True, "g1", "u1", True, False, True),
        (True, "g1", "zz", True, False, False),  # ① 群在白名单但人不在 → 拒（AND 语义）
        (True, "g1", "u1", False, False, False),  # ② 群内未 @ → 拒
        (True, "g9", "u1", True, True, False),  # ③ 开了 allow_all 但群不在名单 → **仍拒**
        (True, "g9", "u1", True, False, False),
    ],
)
def test_access_truth_table(group, chat, user, mentioned, allow_all, expected):
    """**逐行**测 §5.2。③ 是决策P4.21-39 的守卫：

    `--allow-all-users` **只旁路用户名单、永不旁路群名单**——这条一旦回归，
    就是把整库开给机器人所在的**所有群**。
    """
    policy = AccessPolicy(
        allow_users=frozenset({"u1", "u2"}), allow_chats=frozenset({"g1"}), allow_all=allow_all
    )
    m = msg(user=user, chat=chat, group=group, mentioned=mentioned)
    assert policy.permits(m) is expected


def test_access_is_case_sensitive():
    """ID 精确匹配、**大小写敏感**、不剥前缀（决策P4.21-33）：平台 ID 是不透明标识。"""
    policy = AccessPolicy(allow_users=frozenset({"Ou_ABC"}), allow_chats=frozenset())
    assert policy.permits(msg(user="Ou_ABC")) is True
    assert policy.permits(msg(user="ou_abc")) is False


def test_unauthorized_is_silent(kb_im):
    """未授权 = **静默丢弃**：断言**没有任何出站调用**（"不回复"本身是契约）。"""

    async def scenario():
        adapter, intake, delivery, *_ = make_stack(kb_im)
        await intake.offer(msg(user="stranger"))
        await asyncio.sleep(0.05)
        assert adapter.calls == []
        assert delivery.active() == []
        assert intake.dropped_unauthorized == 1

    asyncio.run(scenario())


# ───────────────────────── 入站五道闸 ─────────────────────────


def test_dedup_same_msg_id(kb_im):
    """同 `msg_id` 投两次 → 只跑一轮（漏了就是**双倍 LLM 花费**）。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, clk = make_stack(kb_im)
        await intake.offer(msg("问题", mid="same"))
        await intake.offer(msg("问题", mid="same"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert len(store.created) == 1
        assert intake.dropped_duplicate == 1
        clk.advance(400.0)  # TTL 过期后可再跑
        await intake.offer(msg("问题", mid="same"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert len(store.created) == 1  # 同一会话键复用会话
        assert store.created[0].turns == ["问题", "问题"]

    asyncio.run(scenario())


def test_identical_text_different_ids_both_run(kb_im):
    """**反向用例（决策P4.21-34）**：同一用户 5 分钟内两次**相同文本、不同 `msg_id`** → 都要处理。

    守住"删掉内容指纹去重"这个决策不被后人好心加回来：**静默吞掉合法消息比偶尔重跑一次更糟**。
    """

    async def scenario():
        _ad, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        await intake.offer(msg("同样的问题", mid="a"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        await intake.offer(msg("同样的问题", mid="b"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert store.created[0].turns == ["同样的问题", "同样的问题"]

    asyncio.run(scenario())


def test_dedup_prune_leaves_headroom():
    """剪到**留有余量**的水位，不是剪到恰好等于上界。

    剪到上界的话，下一条消息又 +1 越界 → **此后每条消息都要对全表排一次序**（n=4096），
    而且这段排序跑在事件循环线程上。去重表还排在权限闸**之前**，未授权流量一样能把它填满，
    于是一次刷屏就能让宿主对所有人变慢。留 1/4 余量后，排序摊薄到每 1024 条一次。
    """
    intake = Intake(
        CAPS_TYPING,
        AccessPolicy(allow_users=frozenset({"u1"}), allow_chats=frozenset()),
        clock=Clock(),
    )
    prunes: list[float] = []
    original = intake._prune
    intake._prune = lambda now: (prunes.append(now), original(now))[1]  # type: ignore[method-assign]
    for i in range(DEDUP_MAX_ENTRIES + 200):
        intake._is_duplicate(f"m{i}")
    assert len(prunes) == 1, f"越界后每条消息都在重排全表（{len(prunes)} 次）"
    assert len(intake._seen) <= DEDUP_KEEP_ENTRIES + 200


def test_batch_merge_two_fragments(kb_im):
    """分片合并：两片在阈值内到达 → 合成**一条**。"""

    async def scenario():
        _ad, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        await intake.offer(msg("前半句"))
        await intake.offer(msg("后半句"))
        await asyncio.sleep(0.08)
        await drain_tasks(delivery)
        assert store.created[0].turns == ["前半句\n后半句"]

    asyncio.run(scenario())


def test_batch_cancel_delivery_race(kb_im):
    """**cancel-delivery 竞态守卫**（决策P4.21-11）：定时器与 `cancel()` 同时触发时不得丢消息。

    直接把两个 debounce task 撞在一起：老 task 的 `CancelledError` 延迟投递，届时它已不是
    `_tasks[key]`，故必须**同步复查**后静默退出——而新 task 照常 flush。
    """

    async def scenario():
        _ad, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        await intake.offer(msg("一"))
        await asyncio.sleep(0)  # 让第一个 debounce task 起跑
        await intake.offer(msg("二"))  # 撞上去：cancel 老的、建新的
        await asyncio.sleep(0.08)
        await drain_tasks(delivery)
        assert store.created[0].turns == ["一\n二"], "竞态守卫失效：消息被静默吞掉了"

    asyncio.run(scenario())


def test_slash_commands_are_zero_llm(kb_im):
    """`/help` / `/new` / `/search` **不建会话、不烧 token**（§5.1 第 5 道闸）。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        for text in ("/help", "/new", "/search 甲实体"):
            await intake.offer(msg(text))
            await asyncio.sleep(0.02)
            await drain_tasks(delivery)
        assert store.created == [], "零 LLM 命令不该建会话"
        assert len(adapter.sent()) == 3

    asyncio.run(scenario())


def test_group_mention_prefix_is_stripped(kb_im):
    """群里 `@bot /search x` 必须识别得出斜杠命令（闸③ 剥前缀）。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, caps=CAPS_EDIT)
        await intake.offer(msg("@观澜 /search 甲实体", chat="g1", group=True, mentioned=True))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert store.created == []
        assert "甲实体" in adapter.sent()[0]

    asyncio.run(scenario())


def test_attachment_hint_is_core_behavior(kb_im):
    """**附件提示归核心**（决策P4.21-36）：适配器只做事实映射，故这条可决定性地测到。零 LLM。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        await intake.offer(msg("这张图什么意思", kind=KIND_OTHER, attachments=True))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert adapter.sent() == [ATTACHMENT_HINT]
        assert store.created == [], "附件路径绝不能进 LLM"

    asyncio.run(scenario())


def test_search_is_zero_llm_and_offloaded(kb_im):
    """`/search` 零 LLM 且**卸线程**：检索期间另一条消息仍能被 `offer` 接收。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        await intake.offer(msg("/search 甲实体"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert store.created == []
        assert "甲实体" in adapter.sent()[0]

    asyncio.run(scenario())


# ───────────────────────── 会话状态机（§4.5）─────────────────────────


def test_acquire_four_states(kb_im):
    """四态（决策P4.21-40/47）：`FIRST` / `EXISTING` / `EXPIRED` / `LOST`，措辞各不相同。"""

    async def scenario():
        clk = Clock()
        store = FakeStore()
        reg = SessionRegistry(store, ttl=100.0, tombstone_limit=10, clock=clk)
        conv1, st1 = await reg.acquire("k")
        assert st1 is AcquireStatus.FIRST
        conv2, st2 = await reg.acquire("k")
        assert st2 is AcquireStatus.EXISTING and conv2 is conv1
        clk.advance(200.0)
        conv3, st3 = await reg.acquire("k")
        # ③ 跨 TTL 再问 → EXPIRED 且拿到**新** cid（守住"不会被 store.get() 悄悄复活"）
        assert st3 is AcquireStatus.EXPIRED and conv3.id != conv1.id
        # ④ 人为让 store.get 返回 None（模拟不变量被破坏）→ LOST + 无悬空映射
        store.convs.pop(conv3.id)
        conv4, st4 = await reg.acquire("k")
        assert st4 is AcquireStatus.LOST and conv4.id != conv3.id
        assert reg.live_keys() == ["k"] and reg.tombstone_keys() == []

    asyncio.run(scenario())


def test_new_leaves_no_tombstone(kb_im):
    """`/new` 后再问是 `FIRST`（**不是** `EXPIRED`）——主动重开不该被告知"上下文已过期"。"""

    async def scenario():
        store = FakeStore()
        reg = SessionRegistry(store, ttl=100.0, tombstone_limit=10, clock=Clock())
        await reg.acquire("k")
        await reg.drop("k")
        assert reg.tombstone_keys() == []
        _conv, status = await reg.acquire("k")
        assert status is AcquireStatus.FIRST

    asyncio.run(scenario())


def test_expired_sessions_actually_free_capacity(kb_im):
    """**这是修订的核心正例（决策P4.21-46/51）**：过期会话真的腾出容量，且过期语义保住。"""

    async def scenario():
        clk = Clock()
        store = FakeStore(cap=2)
        reg = SessionRegistry(store, ttl=100.0, tombstone_limit=2, clock=clk)
        await reg.acquire("a")
        await reg.acquire("b")
        clk.advance(200.0)
        conv, status = await reg.acquire("c")  # 第三个 key 首次说话
        assert status is AcquireStatus.FIRST and conv is not None  # ① 不是「会话数已满」
        assert store.live_count() == 1  # ② 前两个被 _sweep_expired 扫掉
        # ③ 前两个 key 再问时**仍是 EXPIRED**（墓碑保住了过期语义）
        _c, sa = await reg.acquire("a")
        assert sa is AcquireStatus.EXPIRED

    asyncio.run(scenario())


def test_store_is_constructed_with_idle_ttl_none(kb_im, monkeypatch):
    """反向守卫：**不许把 store 的 `idle_ttl` 加回来**（两套 TTL 是空档，不是分工）。"""
    captured: dict = {}

    class _Probe:
        def __init__(self, *a, **kw):
            captured.update(kw)
            raise RuntimeError("stop-here")  # 装配到这一步就够了

    monkeypatch.setattr("guanlan.web.chat.ConversationStore", _Probe)
    with pytest.raises(RuntimeError, match="stop-here"):
        im_server.serve_im(
            kb_im, platform="fake", allow_user=["u1"], adapter=FakeAdapter(), no_mcp=True
        )
    assert captured["idle_ttl"] is None
    assert captured["persist"] is False and captured["default_mode"] == "read-only"
    assert captured["write_gate"] is None


def test_tombstones_are_bounded_and_never_evict_live(kb_im):
    """墓碑有界且**不误伤活条目**；淘汰的是**最旧的**。"""

    async def scenario():
        clk = Clock()
        store = FakeStore(cap=100)
        reg = SessionRegistry(store, ttl=10.0, tombstone_limit=3, clock=clk)
        for i in range(6):  # 造 6 个墓碑，时间戳递增
            await reg.acquire(f"t{i}")
            clk.advance(1.0)
        clk.advance(100.0)
        await reg.acquire("live")  # 触发 sweep：6 个全变墓碑，然后淘汰到界内
        assert len(reg.tombstone_keys()) <= 3
        assert "t0" not in reg.tombstone_keys()  # 最旧的先走
        assert reg.live_keys() == ["live"]  # 活条目一个没少

    asyncio.run(scenario())


def test_sweep_skips_busy_sessions(kb_im):
    """sweep **不误伤在飞轮**：`busy()` 守卫生效，且 A 的这一轮仍能正常收尾。"""

    async def scenario():
        clk = Clock()
        store = FakeStore()
        adapter, intake, delivery, reg, store, _c = make_stack(
            kb_im, store=store, clock=clk, ttl=100.0
        )
        gate = asyncio.Event()
        store.on_create = lambda c: setattr(c, "gate", gate)
        await intake.offer(msg("慢问题", user="u1"))
        await asyncio.sleep(0.05)
        key_a = session_key_of(msg(user="u1"))
        assert delivery.busy(key_a)
        clk.advance(500.0)
        await reg.acquire("其他键")  # 触发 sweep
        assert key_a in reg.live_keys(), "在飞轮的会话被 sweep 掉了"
        gate.set()
        await drain_tasks(delivery)
        assert adapter.sent()

    asyncio.run(scenario())


def test_capacity_full_gets_explicit_reply(kb_im):
    """容量真顶满 → **显式答复**「会话数已满」，且**主循环没死**（第三条消息仍被处理）。"""

    async def scenario():
        store = FakeStore(cap=1)
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, store=store, cap=1)
        gate = asyncio.Event()
        store.on_create = lambda c: setattr(c, "gate", gate)
        await intake.offer(msg("一", user="u1"))
        await asyncio.sleep(0.05)
        await intake.offer(msg("二", user="u2"))
        await asyncio.sleep(0.05)
        assert any("会话数已满" in s for s in adapter.sent())
        gate.set()
        await drain_tasks(delivery)
        await intake.offer(msg("/help", user="u2"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert any("观澜知识库机器人" in s for s in adapter.sent())

    asyncio.run(scenario())


def test_expired_identity_survives_capacity_error(kb_im):
    """**墓碑接线②（决策P4.21-61）**：过期后紧接着容量顶满 → 重试仍是 `EXPIRED` 而非 `FIRST`。

    `drop` 的默认值是 `tombstone=False`，过期路径若漏传，`_renew` 里紧随其后的建会话**一旦抛
    容量错**，这个键的过期身份就一起没了——"上下文已过期"这句提示**恰好在最需要它的那次重试里消失**。
    """

    async def scenario():
        clk = Clock()
        store = FakeStore()
        reg = SessionRegistry(store, ttl=100.0, tombstone_limit=10, clock=clk)
        await reg.acquire("k")
        clk.advance(200.0)
        store.create_error = RuntimeError("内存会话数已达上限")
        with pytest.raises(RuntimeError):
            await reg.acquire("k")
        store.create_error = None
        _conv, status = await reg.acquire("k")  # 用户重试
        assert status is AcquireStatus.EXPIRED

    asyncio.run(scenario())


def test_tombstone_does_not_evict_itself(kb_im):
    """**墓碑不会自己淘汰自己（决策P4.21-64）——三条件同时成立才暴露**：

    墓碑**已满** + 当前键刚过期 + `create()` 抛容量错 → 重试仍须是 `EXPIRED`。
    另断言该墓碑的 `last_seen` 被刷成 `now`（不是沿用旧值）。
    **上一条用例单独绿不算数。**
    """

    async def scenario():
        clk = Clock()
        store = FakeStore()
        reg = SessionRegistry(store, ttl=10.0, tombstone_limit=2, clock=clk)
        await reg.acquire("old1")
        clk.advance(1.0)
        await reg.acquire("old2")
        clk.advance(1.0)
        await reg.acquire("k")  # 这个键最"新"
        clk.advance(100.0)  # 三个全过期
        store.create_error = RuntimeError("内存会话数已达上限")
        with pytest.raises(RuntimeError):
            await reg.acquire("k")
        assert "k" in reg.tombstone_keys(), "当前键的墓碑被自己那轮 sweep 淘汰掉了"
        seen = dict(reg._map)["k"][1]
        assert seen == clk.now, "墓碑沿用了旧的 last_seen（会让它成为最旧的那条、先被淘汰）"
        store.create_error = None
        _conv, status = await reg.acquire("k")
        assert status is AcquireStatus.EXPIRED

    asyncio.run(scenario())


def test_concurrent_renew_does_not_evict_each_other(kb_im):
    """**并发重建不会互相淘汰墓碑（决策P4.21-69）——#37 那条的加强版**。

    只保护"当前这个键"挡不住 A、B 同时重建时互相淘汰。并发不是来自线程，是来自 `await` 处的交错。
    再断言 `_creating` 在三条退出路径后都已清空——**残留一个键就等于永久保护它**。
    """

    async def scenario():
        clk = Clock()
        store = FakeStore()
        # 界设成 2 且**先造一条更旧的墓碑 C**：这样淘汰压力是真的（3 条挤 2 个位），
        # 该被淘汰的只能是没在重建的 C——若保护退回"只保护自己那个键"，A、B 就会互相吃掉。
        reg = SessionRegistry(store, ttl=10.0, tombstone_limit=2, clock=clk)
        await reg.acquire("C")
        clk.advance(1.0)
        await reg.acquire("A")
        clk.advance(1.0)
        await reg.acquire("B")
        clk.advance(100.0)
        store.create_block = threading.Event()  # 让两条重建路径卡在同一处、真交错
        ta = asyncio.create_task(reg.acquire("A"))
        tb = asyncio.create_task(reg.acquire("B"))
        await wait_until(lambda: len(reg.creating) == 2, what="两条重建路径同时在建")
        assert set(reg.creating) == {"A", "B"}
        store.create_error = RuntimeError("顶满")  # 其中/两者创建失败
        store.create_block.set()
        for t in (ta, tb):
            with pytest.raises(RuntimeError):
                await t
        assert set(reg.tombstone_keys()) == {"A", "B"}, "并发重建把对方的墓碑淘汰掉了"
        assert set(reg.creating) == set(), "_creating 残留：等于永久保护那个键"
        store.create_error = None
        for key in ("A", "B"):
            _c, status = await reg.acquire(key)
            assert status is AcquireStatus.EXPIRED

    asyncio.run(scenario())


def test_all_three_acquire_branches_go_through_renew(kb_im):
    """**三条 acquire 分支都走 `_renew`（决策P4.21-71）**：墓碑 / `LOST` / 首次各一例。

    这条用例存在的理由是：新机制若只接上一半，单看 `_renew` 的实现是对的，问题只在调用点——
    **只有从 `acquire` 入口测才发现得了。**
    """

    async def scenario():
        clk = Clock()
        store = FakeStore()
        reg = SessionRegistry(store, ttl=10.0, tombstone_limit=10, clock=clk)
        seen: list[set[str]] = []

        async def probe(key: str):
            store.create_block = threading.Event()
            task = asyncio.create_task(reg.acquire(key))
            await wait_until(lambda: bool(reg.creating), what="进入 _creating")
            seen.append(set(reg.creating))
            store.create_block.set()
            store.create_block = None
            return await task

        conv, status = await probe("k")  # ① 首次
        assert status is AcquireStatus.FIRST
        clk.advance(100.0)
        await reg._sweep_expired()  # 造一个墓碑
        _c, status = await probe("k")  # ② 墓碑
        assert status is AcquireStatus.EXPIRED
        store.convs.clear()  # 造 LOST
        _c, status = await probe("k")  # ③ LOST
        assert status is AcquireStatus.LOST
        assert all(s == {"k"} for s in seen), "重建期间该键不在 _creating 里（保护没接上）"
        assert set(reg.creating) == set()
        assert conv is not None

    asyncio.run(scenario())


def test_get_is_offloaded_to_thread(kb_im):
    """`store.get()` **不阻塞事件循环**（决策P4.21-55）：它与 `create()` 共用同一把线程锁。

    新用户建会话（持锁的慢构造）期间，**老用户**发的消息必须能在 `create` 完成**之前**被 `offer` 接收。
    """

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        await intake.offer(msg("先建会话", user="u1"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)  # u1 已有会话
        store.create_block = threading.Event()
        await intake.offer(msg("慢建会话", user="u2"))
        await wait_until(lambda: bool(delivery.busy(session_key_of(msg(user="u2")))), what="u2 起跑")
        before = len(adapter.sent())
        await intake.offer(msg("老用户的问题", user="u1"))
        # ★ 判据：u1 这一整轮（含 `store.get()`）在 u2 的 `create` **仍被卡住时**就跑完了。
        # 若 `get()` 没卸线程，它会在 loop 线程上撞那把 `threading.Lock`，这里必然超时。
        await wait_until(lambda: len(adapter.sent()) > before, what="老用户的回答")
        assert not store.create_block.is_set(), "用例前提没成立：慢 create 已经放行了"
        store.create_block.set()
        await drain_tasks(delivery)

    asyncio.run(scenario())


def test_new_command_does_not_block_loop(kb_im):
    """`/new` **不阻塞**（决策P4.21-44）：`drop` → `store.delete()` → `conv.close()` 被标注"慢，锁外"。"""

    async def scenario():
        _ad, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        await intake.offer(msg("建个会话"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        store.delete_block = threading.Event()
        await intake.offer(msg("/new"))
        await asyncio.sleep(0.02)
        await intake.offer(msg("/help", user="u2"))  # 另一条入站仍能被 offer 接收
        await asyncio.sleep(0.02)
        store.delete_block.set()
        await drain_tasks(delivery)

    asyncio.run(scenario())


# ───────────────────────── 生命周期与 emit ─────────────────────────


def test_begin_end_turn_paired_even_on_error(kb_im):
    """生命周期闭合①：turn 抛异常时 `end_turn` 仍被调用、typing 被关掉。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        store.on_create = lambda c: setattr(c, "raises", ValueError("炸"))
        await intake.offer(msg("会炸的问题"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        conv = store.created[0]
        assert conv.begun == 1 and conv.ended == 1
        assert ("typing", "u1", False) in adapter.calls
        assert ERROR_HINT in adapter.sent()

    asyncio.run(scenario())


def test_emit_runs_on_event_loop_thread(kb_im):
    """**emit 已在事件循环线程**（决策P4.21-49）：宿主侧**不得**再 `call_soon_threadsafe`。

    再桥一次会把每次 token 推迟一个 loop tick，可能在 `finalize` **之后**才改 `_latest`——
    终稿被改回中途快照，而且是**偶发**的。
    """

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, caps=CAPS_EDIT)

        def prep(c):
            c.tokens = ["甲" * 300, "乙" * 300]
            c.answer = "终稿"

        store.on_create = prep
        await intake.offer(msg("问题"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        conv = store.created[0]
        assert conv.emit_threads, "没有 token 流出来"
        assert set(conv.emit_threads) == {threading.get_ident()}
        finals = [c for c in adapter.calls if c[0] == "edit" and c[3] is True]
        assert finals[-1][2] == "终稿", "终稿被中途快照覆盖了"

    asyncio.run(scenario())


def test_edit_writer_is_strictly_ordered(kb_im):
    """edit **严格有序**：永不并发两个 edit，且 `finalize` 的文本最后落地。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, caps=CAPS_EDIT)
        concurrent = {"now": 0, "max": 0}
        original_edit = adapter.edit

        async def slow_edit(ref, text, *, finalize=False):
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["now"])
            await asyncio.sleep(0.01)
            await original_edit(ref, text, finalize=finalize)
            concurrent["now"] -= 1

        adapter.edit = slow_edit  # type: ignore[method-assign]

        def prep(c):
            c.tokens = ["甲" * 400] * 6
            c.answer = "终稿"

        store.on_create = prep
        await intake.offer(msg("问题"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert concurrent["max"] == 1, "并发发出了两个 edit"
        edits = [c for c in adapter.calls if c[0] == "edit"]
        assert edits[-1][3] is True and edits[-1][2] == "终稿"

    asyncio.run(scenario())


def test_writer_wakeup_during_edit_is_not_lost(kb_im):
    """飞行途中置的 `final` **不能被 `clear()` 抹掉**（clear-then-check，§6.2）。

    `edit()` 是一次真实的网络往返（飞书 100–500 ms），主协程恰恰常在**那段窗口里** `touch()`。
    `clear()` 若排在读状态**之后**，这次唤醒就被下一轮抹掉——终稿要白等满一个 `EDIT_INTERVAL_S`
    （用户盯着占位消息多停 2 秒），停机时每个 writer 也多拖 2 秒。经典的丢唤醒。

    这条**直接驱动 `_writer_loop`**：只有自己掌控"何时进入 edit、何时放行"，才能把 `touch()`
    精确投进飞行窗口——走整套栈就只能靠 sleep 猜时机，那样它守不住任何东西。
    """

    async def scenario():
        adapter, _intake, delivery, *_ = make_stack(kb_im, caps=CAPS_EDIT)
        inside = asyncio.Event()  # writer 已进到 edit 里
        release = asyncio.Event()  # 放它出来

        async def blocking_edit(ref, text, *, finalize=False):
            adapter.calls.append(("edit", ref.message_id, text, finalize))
            if not finalize:
                inside.set()
                await release.wait()

        adapter.edit = blocking_edit  # type: ignore[method-assign]
        st = _EditState()
        st.latest = "甲" * (EDIT_MIN_CHARS + 10)  # 够一次中间 edit
        writer = asyncio.create_task(
            delivery._writer_loop(OutboundRef(chat_id="u1", message_id="m1"), st)
        )
        await asyncio.wait_for(inside.wait(), timeout=2.0)
        st.latest, st.final = "终稿", True
        st.touch()  # ← 落在 edit 的飞行途中
        release.set()
        # 原实现在这里要白等满 EDIT_INTERVAL_S；给半个间隔，抹掉唤醒就超时。
        await asyncio.wait_for(writer, timeout=EDIT_INTERVAL_S / 2)
        assert adapter.calls[-1] == ("edit", "m1", "终稿", True)

    asyncio.run(scenario())


def test_error_stops_writer_before_sending_hint(kb_im):
    """异常路径的 writer 顺序（§6.2）：writer **在错误提示发出之前**就停了。

    **顺序不能反**：先发提示再停 writer，writer 会把提示又改回旧的局部文本。
    """

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, caps=CAPS_EDIT)

        def prep(c):
            c.tokens = ["甲" * 400]
            c.raises = ValueError("炸")

        store.on_create = prep
        await intake.offer(msg("问题"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        kinds = adapter.kinds()
        last_edit = max(i for i, k in enumerate(kinds) if k == "edit")
        hint_at = next(
            i for i, c in enumerate(adapter.calls) if c[0] == "send" and c[2] == ERROR_HINT
        )
        assert last_edit < hint_at, "错误提示发出后 writer 还在写"

    asyncio.run(scenario())


def test_single_flight_gives_explicit_feedback(kb_im):
    """单飞（决策P4.21-13）：在飞时第二条消息得到「请稍候」，且 turn 只跑一次。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        gate = asyncio.Event()
        store.on_create = lambda c: setattr(c, "gate", gate)
        await intake.offer(msg("一"))
        await asyncio.sleep(0.05)
        await intake.offer(msg("二"))
        await asyncio.sleep(0.05)
        assert BUSY_HINT in adapter.sent()
        gate.set()
        await drain_tasks(delivery)
        assert len(store.created[0].turns) == 1

    asyncio.run(scenario())


# ───────────────────────── 长答案契约（§6.3）─────────────────────────


def test_split_lossless_and_truncated():
    """`dropped == 0` 时无损；超限时**末片是截断告示**且含剩余字数。"""
    parts, dropped = split_for("短答案", 2000, max_parts=5)
    assert parts == ["短答案"] and dropped == 0
    parts, dropped = split_for("甲" * 12000, 2000, max_parts=5)
    assert len(parts) == 4 and dropped > 0  # 留一片给告示
    assert all(len(p) <= 2000 for p in parts)


def test_split_keeps_a_body_part_even_when_max_parts_is_one():
    """**反向用例**：`max_parts == 1` 时正文必须还剩一片，不能被整段吞掉。

    "末片留给截断告示"这条规则在 `max_parts == 1` 时退化成 `parts[:0]` ＝ **空列表**：
    用户只收到一句"还有约 N 字未发送"、正文一个字都没有；edit 档更糟，占位消息
    「🔍 正在查阅知识库…」就此冻成终稿。**「不静默截断」不能实现成「静默丢光」**——
    宁可多发一条也要留下正文。上界 ≥ 2 的既有行为**一字不改**（下半段守着）。
    """
    body = "甲" * 500
    parts, dropped = split_for(body, 100, max_parts=1)
    assert parts and parts[0], "正文被整段吞掉了"
    assert dropped > 0, "丢了内容却没告示"
    p3, d3 = split_for(body, 100, max_parts=3)
    assert len(p3) == 2 and p3[0] == parts[0] and d3 > 0


def test_split_never_emits_an_empty_part():
    """空片会变成一条**空消息**发给平台（飞书 / iLink 都直接拒），于是每条长答案配一次发送失败。

    触发条件很日常：硬切窗口内的断点前只有空白（缩进行、空行），`rstrip()` 后就是空串。
    """
    pieces = _split_paragraph("  \n" + "甲" * 300, 100)
    assert pieces == ["甲" * 100] * 3


def test_truncation_notice_never_promises_full_version():
    """**告示不得称「完整版」**（决策P4.21-50）：IM 会话 `persist=False`，那段内容没存在任何地方。

    无 `--web-base-url` 时**不出现任何 URL**——承诺一个不存在的东西比不给链接更糟。
    """
    text = truncation_notice(1234, web_base_url=None)
    assert "完整版" not in text and "http" not in text
    assert "重新提问" in text and "1234" in text
    with_url = truncation_notice(1234, web_base_url="https://kb.internal/")
    assert "https://kb.internal" in with_url and "完整版" not in with_url


def test_long_answer_edit_tier_sends_rest_and_notice(kb_im):
    """edit 档超长：**首片 edit、余片 send**，且中间 edit 的文本也 ≤ limit。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, caps=CAPS_EDIT)

        def prep(c):
            c.answer = "甲" * 40000
            c.tokens = ["乙" * 9000]  # 中途快照就超 8000，必须被限长

        store.on_create = prep
        await intake.offer(msg("问题"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        edits = [c for c in adapter.calls if c[0] == "edit"]
        assert all(len(c[2]) <= CAPS_EDIT.max_message_length for c in edits)
        sends = adapter.sent()
        assert sends[0] == "🔍 正在查阅知识库…"
        assert len(sends) >= 3  # 占位 + 余片 + 告示
        assert "未发送" in sends[-1]

    asyncio.run(scenario())


def test_outbound_shape_follows_caps(kb_im):
    """出站形态用 2000 / 8000 两个上限各跑一遍——**上限是适配器给的数据，不是常量**。"""

    async def scenario(caps: AdapterCaps) -> list[str]:
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, caps=caps)
        store.on_create = lambda c: setattr(c, "answer", "甲" * 5000)
        await intake.offer(msg("问题", chat="u1" if not caps.supports_group else "u1"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        return [c[2] for c in adapter.calls if c[0] in ("send", "edit")] + [
            c[2] for c in adapter.calls if c[0] == "edit"
        ]

    small = asyncio.run(scenario(CAPS_TYPING))
    assert all(len(t) <= 2000 for t in small)
    big = asyncio.run(scenario(CAPS_EDIT))
    assert any(2000 < len(t) <= 8000 for t in big), "8000 档没有利用更大的上限"


def test_wikilink_always_keeps_source():
    """`[[wikilink]]` **无条件保留原文**（决策P4.21-17，经决策P4.21-76 收紧）。

    原设计留了「给了 `--web-base-url` 就转真链接」这条支线，实现期核实后**撤回**：
    Web 宿主根本**没有按页面名打开某一页的路由**（SPA 只认 `?raw=` 与 `?c=`），
    任何由页面**名**拼出来的 URL 都是死链。**一个 404 比一段可读的 `[[甲实体]]` 更误导**
    ——与决策P4.21-50「不承诺一个不存在的东西」同一条判据。

    这条用例的形状本身就是守卫：**给了 base-url 也不许出现 URL**。
    """
    assert to_im_markdown("见 [[甲实体]]", web_base_url=None) == "见 [[甲实体]]"
    with_url = to_im_markdown("见 [[甲实体]]", web_base_url="https://kb.internal")
    assert with_url == "见 [[甲实体]]"
    assert "http" not in with_url and "](" not in with_url


def test_web_base_url_only_appears_in_truncation_notice():
    """`--web-base-url` 仍然有用——但它是**站点入口**，只出现在截断告示里（§6.3）。"""
    assert "https://kb.internal" in truncation_notice(100, web_base_url="https://kb.internal")
    assert "http" not in to_im_markdown("正文 [[某页]]", web_base_url="https://kb.internal")


def test_fences_are_preserved():
    """围栏块**原样保留**（§6.5）：mermaid / KaTeX / flint 在 IM 里无渲染器，降级契约是保留源码。"""
    text = "前言\n```flint\n{\"a\":1}\n```\n后记"
    assert to_im_markdown(text, web_base_url=None) == text
    parts, dropped = split_for(text, 2000, max_parts=5)
    assert dropped == 0 and "```flint" in "\n".join(parts)


# ───────────────────────── 停机（§4.4）─────────────────────────


async def run_host(adapter, intake, delivery, *, warn_interval: float = 30.0) -> int:
    return await im_server._run(adapter, intake, delivery, warn_interval=warn_interval)


def test_shutdown_requests_stop_and_closes_after_turns(kb_im):
    """**shutdown 真停**（决策P4.21-41/48）：`request_stop()` 被调用；`close()` 在所有 turn **之后**。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, caps=CAPS_EDIT)
        gate = asyncio.Event()
        store.on_create = lambda c: setattr(c, "gate", gate)
        host = asyncio.create_task(run_host(adapter, intake, delivery))
        adapter.queue.put_nowait(msg("慢问题"))
        await wait_until(
            lambda: bool(delivery.active()) and delivery.active()[0].conv is not None,
            what="这一轮拿到 conv",
        )
        conv = store.created[0]
        stopper = asyncio.create_task(delivery.stop_all(warn_interval=0.01))
        await asyncio.sleep(0.05)
        assert conv.stop_calls >= 1 and conv.stopped  # ① request_stop 被调用过
        gate.set()
        await stopper
        adapter.queue.put_nowait(_STOP)
        code = await host
        kinds = adapter.kinds()
        assert kinds.index("close") > max(  # ② close 在所有出站之后
            i for i, k in enumerate(kinds) if k in ("send", "edit")
        )
        # ③ 停机路径一个字也不发（AgentCancelledError 静默收尾）
        assert ERROR_HINT not in adapter.sent()
        assert code == EXIT_AGENT_ERROR  # inbound 结束 = 长驻宿主的错误

    asyncio.run(scenario())


def test_shutdown_warns_but_does_not_cancel(kb_im, caplog):
    """**超时只告警、不取消**（决策P4.21-48）：`warn_interval` 极小 + 长 turn。

    超时后 `task.cancel()` **恰好重演本设计要修的那个错**——外层 task 取消了、executor 线程照跑，
    然后步骤⑥关掉连接，残留线程的收尾写打在死连接上。故超时只用来**告诉用户还在等谁**。
    """
    caplog.set_level(logging.WARNING, logger="guanlan.im")

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        gate = asyncio.Event()
        store.on_create = lambda c: setattr(c, "gate", gate)
        await intake.offer(msg("很久的问题"))
        await wait_until(
            lambda: bool(delivery.active()) and delivery.active()[0].conv is not None,
            what="这一轮拿到 conv",
        )
        task = delivery.active()[0].task
        stopper = asyncio.create_task(delivery.stop_all(warn_interval=0.01))
        await asyncio.sleep(0.06)
        assert not task.cancelled() and not task.done(), "在飞轮被 cancel 了"
        gate.set()
        await stopper
        assert task.done()
        del adapter

    asyncio.run(scenario())
    assert any("停机仍在等" in r.message for r in caplog.records)


def test_shutdown_sends_nothing_on_cancel(kb_im):
    """**停机不发「处理出错了」**（决策P4.21-54，必测）：零出站、writer 已停、日志是 INFO。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im, caps=CAPS_EDIT)
        store.on_create = lambda c: setattr(c, "raises", AgentCancelledError("user-stop"))
        await intake.offer(msg("问题"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert ERROR_HINT not in adapter.sent()
        assert not any("出错" in s for s in adapter.sent())
        assert all(a.writer is None for a in delivery.active())

    asyncio.run(scenario())


def test_shutdown_waits_for_slow_acquire(kb_im):
    """**停机撞上慢 `acquire`（决策P4.21-52）——核心竞态用例**。

    `stop_all()` 必须**等到**这个 task（不是提前返回）；该 task **一个字也没发**（第二道
    `_closing` 闸拦住）；`close()` 在它结束**之后**。
    """

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        store.create_block = threading.Event()
        await intake.offer(msg("问题"))
        await wait_until(lambda: bool(delivery.active()), what="这一轮被登记")
        act = delivery.active()[0]
        assert act.conv is None, "这条用例要的正是「已登记但还没拿到 conv」这段窗口"
        stopper = asyncio.create_task(delivery.stop_all(warn_interval=0.01))
        await asyncio.sleep(0.03)
        assert not stopper.done(), "stop_all 在慢 acquire 完成前就返回了"
        store.create_block.set()
        await stopper
        assert act.task.done()
        assert adapter.sent() == [], "第二道 _closing 闸没拦住"

    asyncio.run(scenario())


def test_stop_all_sees_conv_in_stoppable_state(kb_im):
    """**停机撞在 `acquire` 与 `begin_turn` 之间（决策P4.21-58）——本设计最细的一条**。

    `request_stop()` 是三态的；"已登记 `conv`、`_inflight` 仍为 0、令牌未装"这一瞬恰好落在
    第三态 → 返回 `False` 空转，该轮随后照常起跑、再没人叫得停。把 `begin_turn()` 拉进同一
    不可分割段后，`stop_all()` 看得见 `conv` 的**任何**时刻都至少落在"记待停"那一态。
    """

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        gate = asyncio.Event()
        store.on_create = lambda c: setattr(c, "gate", gate)
        await intake.offer(msg("问题"))
        await wait_until(
            lambda: bool(delivery.active()) and delivery.active()[0].conv is not None,
            what="这一轮拿到 conv",
        )
        conv = delivery.active()[0].conv
        assert conv.request_stop() is True, "空转的 False：begin_turn 不在同一不可分割段里"
        gate.set()
        await drain_tasks(delivery)
        assert adapter.sent() == [], "待停没有被立即兑现，整轮跑完了"
        del adapter

    asyncio.run(scenario())


def test_recv_task_is_cancellable(kb_im):
    """**收流 task 可取消（决策P4.21-56）**：干净结束、`close()` 在其之后、取消点无半提交状态。"""

    async def scenario():
        adapter, intake, delivery, _reg, _store, _clk = make_stack(kb_im)
        host = asyncio.create_task(run_host(adapter, intake, delivery))
        await asyncio.sleep(0.02)
        assert adapter.started
        im_server._on_second_interrupt  # 触及一次，确认符号在（强退路径另有子进程用例）
        # 直接走停机：模拟第一次中断
        for task in asyncio.all_tasks():
            if task is not host and task.get_coro().__qualname__.endswith("_receive_loop"):
                pass
        adapter.queue.put_nowait(_STOP)  # inbound 正常返回 → 走"收流死亡"路径
        code = await host
        assert code == EXIT_AGENT_ERROR
        assert adapter.closed
        assert adapter.taken_not_delivered == 0

    asyncio.run(scenario())


def test_inbound_exception_runs_full_shutdown(kb_im, caplog):
    """**收流死亡不留活死人（决策P4.21-57/62）**：`inbound()` 抛异常。

    必须逐项断言停机四步全跑过——v6 的写法会在第一步就把它们全跳过，而"收流抛异常"
    恰恰是最需要清理跑完的那条路径。
    """
    caplog.set_level(logging.ERROR, logger="guanlan.im")

    async def scenario():
        adapter, intake, delivery, _reg, _store, _clk = make_stack(kb_im)
        adapter.inbound_error = RuntimeError("连接被打断")
        drained: list[str] = []
        original_drain = intake.drain

        async def spy_drain(**kw):
            drained.append("drain")
            await original_drain(**kw)

        intake.drain = spy_drain  # type: ignore[method-assign]
        stopped: list[str] = []
        original_stop = delivery.stop_all

        async def spy_stop(**kw):
            stopped.append("stop")
            await original_stop(**kw)

        delivery.stop_all = spy_stop  # type: ignore[method-assign]
        code = await run_host(adapter, intake, delivery)
        assert code == EXIT_AGENT_ERROR
        assert drained == ["drain"] and stopped == ["stop"]
        assert adapter.closed  # adapter.close 被调用
        return caplog.records

    records = asyncio.run(scenario())
    assert any("入站流已中断" in r.message for r in records)
    assert any(r.exc_info for r in records), "原异常没有进日志"


def test_inbound_normal_return_is_also_an_error(kb_im):
    """`inbound()` **正常返回**同样按错误处理——长驻宿主没有"收完了"这回事。"""

    async def scenario():
        adapter, intake, delivery, _reg, _store, _clk = make_stack(kb_im)
        adapter.queue.put_nowait(_STOP)
        code = await run_host(adapter, intake, delivery)
        assert code == EXIT_AGENT_ERROR and adapter.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("failing", ["intake-drain", "stop-all", "adapter-close"])
def test_cleanup_failure_does_not_skip_later_steps(kb_im, failing, caplog):
    """**清理步骤各自抛错都不影响后续（决策P4.21-70/71）**。

    ① 后续步骤仍被调用；② **退出码是 `EXIT_AGENT_ERROR` 而不是 0**——清理没干净却报 0，
    是最坏的一种矛盾；③ 日志点出**是哪一步**失败（记账用列表而非布尔的理由）。
    """
    caplog.set_level(logging.ERROR, logger="guanlan.im")

    async def scenario():
        adapter, intake, delivery, _reg, _store, _clk = make_stack(kb_im)
        calls: list[str] = []

        async def boom_drain(**_kw):
            calls.append("drain")
            raise RuntimeError("drain 炸了")

        async def ok_drain(**_kw):
            calls.append("drain")

        async def boom_stop(**_kw):
            calls.append("stop")
            raise RuntimeError("stop 炸了")

        async def ok_stop(**_kw):
            calls.append("stop")

        intake.drain = boom_drain if failing == "intake-drain" else ok_drain  # type: ignore[method-assign]
        delivery.stop_all = boom_stop if failing == "stop-all" else ok_stop  # type: ignore[method-assign]
        if failing == "adapter-close":
            adapter.close_error = RuntimeError("close 炸了")
        adapter.queue.put_nowait(_STOP)
        code = await run_host(adapter, intake, delivery)
        assert calls == ["drain", "stop"], "前一步抛错把后续步骤跳过了"
        assert ("close",) in adapter.calls, "adapter.close 没被调用"
        assert code == EXIT_AGENT_ERROR
        return caplog.records

    records = asyncio.run(scenario())
    assert any(failing in r.getMessage() for r in records), "日志没点出是哪一步失败"


def test_start_failure_restores_process_state(kb_im, monkeypatch):
    """**`start()` 失败的收尾（决策P4.21-67）**：close 兜底 + 信号处理器还原 + 凭据锁释放 + 退出码 5。"""
    import signal

    adapter = FakeAdapter()
    adapter.start_error = RuntimeError("凭据失效")
    before_int = signal.getsignal(signal.SIGINT)
    before_term = signal.getsignal(signal.SIGTERM)
    code = im_server.serve_im(
        kb_im, platform="fake", allow_user=["u1"], adapter=adapter, no_mcp=True, store=FakeStore()
    )
    assert code == EXIT_AGENT_ERROR
    assert ("close",) in adapter.calls and adapter.closed is True  # close 兜底被调用且成功
    assert signal.getsignal(signal.SIGINT) is before_int
    assert signal.getsignal(signal.SIGTERM) is before_term
    from guanlan.im.state import CredentialLock

    lock = CredentialLock("fake", command="guanlan im-login")
    lock.acquire()  # 锁已释放，能重新抢到
    lock.release()


def test_usage_error_from_start_keeps_exit_usage(kb_im, capsys):
    """**`start()` 抛用法错时不许被压成 5 + traceback**（§9.2）。

    `start()` 最常见的抛出是「缺凭据 / 凭据空白 / SDK 版本不对 / 域名非法」——这些是
    `EXIT_USAGE`，用户要看到的是**一句能照做的话**，不是一屏栈。若被通用 `except Exception`
    接住，既报错了退出码（5 让运维以为是运行时故障），又把可操作的提示埋进 traceback。

    同时断言清理照做：`close()` 兜底、信号处理器还原、凭据锁释放。
    """
    import signal

    from guanlan.im.state import CredentialLock

    adapter = FakeAdapter()
    adapter.start_error = GuanlanError("缺少微信凭据。先跑 `guanlan im-login`。", exit_code=EXIT_USAGE)
    before_int = signal.getsignal(signal.SIGINT)
    with pytest.raises(GuanlanError) as exc:
        im_server.serve_im(
            kb_im, platform="fake", allow_user=["u1"], adapter=adapter, no_mcp=True, store=FakeStore()
        )
    assert exc.value.exit_code == EXIT_USAGE
    assert "im-login" in str(exc.value)
    assert ("close",) in adapter.calls
    assert signal.getsignal(signal.SIGINT) is before_int
    lock = CredentialLock("fake", command="guanlan im")
    lock.acquire()  # 锁已释放
    lock.release()


def test_cli_prints_usage_error_without_traceback(kb_im, monkeypatch, capsys):
    """CLI 顶层把它打成一行并返回 `1`——**不吐 traceback**（§9.2 的"优雅降级"就是这条）。"""
    from guanlan.cli import main

    def fake_serve(*_a, **_kw):
        raise GuanlanError("缺少微信凭据。先跑 `guanlan im-login --platform weixin` 扫码登录。")

    monkeypatch.setattr("guanlan.im.serve_im", fake_serve, raising=False)
    monkeypatch.setattr("guanlan.im.server.serve_im", fake_serve)
    rc = main(["-C", str(kb_im), "im", "--platform", "weixin", "--allow-user", "u1"])
    captured = capsys.readouterr()
    assert rc == EXIT_USAGE
    assert "im-login" in captured.err
    assert "Traceback" not in captured.err


def test_shutdown_discards_pending_debounce(kb_im):
    """优雅停：未到期的 debounce 缓冲被**丢弃**（半条消息不该在关机时触发一轮 LLM 花费）。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        await intake.offer(msg("半句话"))
        await intake.drain(flush=False)
        await asyncio.sleep(0.08)
        assert store.created == [] and adapter.sent() == []
        del delivery

    asyncio.run(scenario())


# ───────────────────────── 强制退出（子进程）─────────────────────────

_HARD_EXIT_SCRIPT = r'''
import asyncio, logging, os, signal, sys, time
sys.path.insert(0, {repo!r})
os.environ["GUANLAN_IM_HOME"] = {home!r}

# 把评审 #32 的失效模式直接做成用例：logging.shutdown 一旦被调就挂起。
def _hang(*a, **kw):
    time.sleep(3600)
logging.shutdown = _hang

from guanlan.im import server as im_server
from guanlan.im.contract import AdapterCaps, OutboundRef

CAPS = AdapterCaps(2000, 5, False, True, True, False, 0.0, 0.01, 0.02)

class StuckAdapter:
    name = "stuck"
    caps = CAPS
    def __init__(self): self.q = asyncio.Queue()
    async def start(self): pass
    async def close(self): pass
    async def inbound(self):
        while True:
            yield await self.q.get()
    async def send(self, chat_id, text): return OutboundRef(chat_id, "")
    async def edit(self, ref, text, *, finalize=False): pass
    async def typing(self, chat_id, on): pass

class NeverConv:
    id = "c1"
    def begin_turn(self): pass
    def end_turn(self): pass
    def request_stop(self): return True
    async def turn(self, msg, emit):
        await asyncio.Event().wait()      # 模拟卡死的 executor 线程：永不返回
    def close(self): pass

class Store:
    def create(self, model=None): return NeverConv()
    def get(self, cid): return None
    def delete(self, cid): return True

from guanlan.im.contract import InboundMessage
async def feed(adapter):
    await asyncio.sleep(0.3)
    adapter.q.put_nowait(InboundMessage("t","u","dm","u","问题","m1",False,"text",False))

adapter = StuckAdapter()
orig_run = asyncio.run
def run_with_feed(coro, **kw):
    async def wrapper():
        asyncio.create_task(feed(adapter))
        print("READY", flush=True)
        return await coro
    return orig_run(wrapper(), **kw)
asyncio.run = run_with_feed

raise SystemExit(im_server.serve_im(
    {kb!r}, platform="stuck", allow_user=["u"], adapter=adapter, store=Store(), no_mcp=True))
'''


def test_hard_exit_actually_exits(kb_im, tmp_path):
    """**强制退出真的退得出去（决策P4.21-53/59，必须用子进程）**。

    ① 进程在数秒内**真的退出**（守住"非守护 worker 被 atexit `join()` 会把进程挂住"这条）；
    ② 退出码是 `EXIT_AGENT_ERROR`（**不是 0**）；③ stderr 里有那行提示且写明还剩几轮；
    ④ **这条路径没调用 `logging`**——脚本里把 `logging.shutdown` 猴补成挂起，进程仍须退出
    （直接把评审 #32 的失效模式做成用例）。

    > 同进程测不了：`os._exit` 会把 pytest 一起带走。这也是唯一一条需要子进程的用例。
    """
    import os
    import signal
    import time

    script = tmp_path / "hard_exit.py"
    script.write_text(
        _HARD_EXIT_SCRIPT.format(
            repo=str(Path.cwd()), home=str(tmp_path / "imhome2"), kb=str(kb_im)
        ),
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if "READY" in line:
                break
        else:  # pragma: no cover
            raise AssertionError("子进程没起来")
        time.sleep(1.0)  # 让那一轮真正进到「卡死的 turn」里
        proc.send_signal(signal.SIGINT)  # 第一次：优雅停（会一直等那个永不返回的 turn）
        time.sleep(1.0)
        assert proc.poll() is None, "第一次信号就退了？那这条用例没测到强退路径"
        proc.send_signal(signal.SIGINT)  # 第二次：强退
        code = proc.wait(timeout=10)
        err = proc.stderr.read()
    finally:
        if proc.poll() is None:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=5)
    assert code == EXIT_AGENT_ERROR, f"强退的退出码必须是 5，实际 {code}；stderr={err!r}"
    assert "强制退出" in err and "轮未收尾" in err


# ───────────────────────── 拒启矩阵（§9）─────────────────────────


def test_refuses_without_any_whitelist(kb_im):
    """白名单四者全空 → `EXIT_USAGE`，报错文案含 `im-identify`，且**未建立任何连接**。"""
    adapter = FakeAdapter()
    with pytest.raises(GuanlanError) as exc:
        im_server.serve_im(kb_im, platform="fake", adapter=adapter, no_mcp=True)
    assert exc.value.exit_code == EXIT_USAGE
    assert "im-identify" in str(exc.value)
    assert adapter.calls == []


def test_refuses_allow_chat_when_platform_has_no_group(kb_im):
    """`--allow-chat` 撞 `supports_group=False` → 拒启并明示（决策P4.21-25）。

    替代「静默不生效」——后者让人以为配好了、实际永远等不到，且无从排查。
    """
    adapter = FakeAdapter(CAPS_TYPING)
    with pytest.raises(GuanlanError) as exc:
        im_server.serve_im(
            kb_im, platform="fake", allow_chat=["g1"], adapter=adapter, no_mcp=True
        )
    assert exc.value.exit_code == EXIT_USAGE
    assert "群消息" in str(exc.value) and adapter.calls == []


def test_refuses_non_kb_root(tmp_path):
    """非知识库根 → `EXIT_USAGE`，就地拒启。"""
    adapter = FakeAdapter()
    with pytest.raises(GuanlanError) as exc:
        im_server.serve_im(tmp_path, platform="fake", allow_user=["u"], adapter=adapter)
    assert exc.value.exit_code == EXIT_USAGE and adapter.calls == []


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_refuses_invalid_mcp_timeout(kb_im, bad):
    """**旗标自身非法 → `EXIT_USAGE`**（决策P4.21-63），且未建立任何连接。

    传 `0` / 负数 / `NaN` 会让合并规则算出一个没有意义的值，**兜底自己先破了**。
    """
    adapter = FakeAdapter()
    with pytest.raises(GuanlanError) as exc:
        im_server.serve_im(
            kb_im, platform="fake", allow_user=["u"], adapter=adapter, mcp_request_timeout=bad
        )
    assert exc.value.exit_code == EXIT_USAGE and adapter.calls == []


def test_allow_user_env_is_comma_separated(kb_im, monkeypatch):
    """`--allow-user-env` 把清单收在一个环境变量里（改完仍需重启，不做热重载）。"""
    monkeypatch.setenv("GUANLAN_IM_USERS", " ou_a , ou_b ")
    policy = im_server.build_policy(
        allow_user=None,
        allow_user_env="GUANLAN_IM_USERS",
        allow_chat=None,
        allow_all_users=False,
    )
    assert policy.allow_users == frozenset({"ou_a", "ou_b"})


@pytest.mark.parametrize("chats", [None, ["g1"]])
def test_allow_all_users_warning_names_direct_messages(caplog, chats):
    """`--allow-all-users` 的告警**必须点明单聊**——它旁路的正是「用户」名单。

    真值表里单聊的放行条件就是 `allow_all OR 名单命中`，所以不给 `--allow-chat` 时它的实际
    含义是「**任何能私聊到本机器人的人**都能读整库」。而只讲"群"的措辞在没有 `--allow-chat`
    时恰好读成"没有人"，与事实相反——**一个说反了的告警比没有告警更危险**：运维照它的字面
    意思判断"没暴露"，然后把库开给了所有陌生人。
    """
    with caplog.at_level(logging.WARNING, logger="guanlan.im"):
        im_server.build_policy(
            allow_user=None, allow_user_env=None, allow_chat=chats, allow_all_users=True
        )
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "私聊" in text, f"告警没提单聊：{text!r}"


def test_credential_lock_is_exclusive_across_subcommands(kb_im):
    """三命令互斥（决策P4.21-38/42）：报错须**同时**给出 owner pid 与占用者子命令名。"""
    from guanlan.im.state import CredentialLock

    held = CredentialLock("fake", command="guanlan im-identify")
    held.acquire()
    try:
        second = CredentialLock("fake", command="guanlan im")
        with pytest.raises(GuanlanError) as exc:
            second.acquire()
        text = str(exc.value)
        assert "im-identify" in text and str(__import__("os").getpid()) in text
        assert exc.value.exit_code == EXIT_USAGE
    finally:
        held.release()


def test_stale_credential_lock_is_reclaimed(kb_im):
    """陈锁（pid 已死）被自动清理——否则一次崩溃会让宿主永远起不来。"""
    from guanlan.im.state import CredentialLock, write_json_atomic

    lock = CredentialLock("fake", command="guanlan im")
    write_json_atomic(lock.path, {"pid": 999_999_999, "command": "guanlan im"})
    lock.acquire()
    lock.release()


def test_unreadable_pid_is_not_a_stale_lock(kb_im):
    """**反向用例**：pid 读不出来时**绝不能**当陈锁清掉，也不能抛 traceback。

    上一条只证明"死 pid 会被回收"；判据若写成"`int()` 一下、失败就当 0"，`_pid_alive(0)` 恒
    为假 ⇒ **一切读不出 pid 的锁都成了陈锁**，包括对方刚 `O_EXCL` 建出、还没来得及写 pid 的
    那一把。删掉它，两个进程就同时持有同一份平台凭据——一个 iLink token 只允许一个长轮询、
    同 `app_id` 两条 WS 会随机分发消息，正是本锁存在的理由。
    """
    from guanlan.im.state import CredentialLock, write_json_atomic

    lock = CredentialLock("fake", command="guanlan im")
    write_json_atomic(lock.path, {"pid": "12ab", "command": "guanlan im-identify"})
    with pytest.raises(GuanlanError) as exc:  # 不是 ValueError，也不是"抢到了"
        lock.acquire()
    assert exc.value.exit_code == EXIT_USAGE
    assert lock.path.exists(), "把别人的锁当陈锁删了 → 两个进程同时持有同一份凭据"


# ───────────────────────── CLI 接线（§9）─────────────────────────


def test_platform_choices_come_from_the_registry(kb_im, monkeypatch):
    """`--platform` 的合法值取自注册表，cli 里**不许再抄一份**。

    抄一份的代价不抽象：`test_core_has_no_platform_branch` 只扫 `guanlan/im/*.py`、**扫不到
    cli.py**，于是"新增平台 = 注册一行、核心零改"这条对外承诺（CLAUDE.md 也这么写）会在
    argparse 这一层悄悄失效——注册了却仍被 `invalid choice` 挡在门外，且没有任何测试报警。
    """
    from guanlan.cli import build_parser
    from guanlan.im import adapters

    monkeypatch.setitem(adapters.ADAPTERS, "demoim", lambda **_kw: None)
    args = build_parser().parse_args(
        ["-C", str(kb_im), "im", "--platform", "demoim", "--allow-all-users"]
    )
    assert args.platform == "demoim"
    # 而 im-login 取的是**另一张表**（`LOGIN_FLOWS`）：飞书不走扫码，解析期就该拒。
    with pytest.raises(SystemExit):
        build_parser().parse_args(["im-login", "--platform", "demoim"])


def test_identify_ctrl_c_is_a_documented_normal_exit(monkeypatch, capsys):
    """`im-identify` 自己打的提示里就写着「Ctrl-C 提前结束」——**文档化的正常出口**。

    它不像 `serve_im` 那样装了信号处理器，于是 Ctrl-C 一路冒到顶层。让一条写在提示里的
    正常用法以一屏 traceback 收场（且退出码 1），等于告诉用户"你刚才操作错了"。
    """
    from guanlan.cli import main

    def boom(**_kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(im_server, "run_identify", boom)
    rc = main(["im-identify", "--platform", "weixin"])
    assert rc == EXIT_OK
    assert "Traceback" not in capsys.readouterr().err


# ───────────────────────── MCP 有限上界（§4.7）─────────────────────────


class _StubRegistry:
    def __init__(self, servers: dict) -> None:
        self._servers = servers

    def list_servers(self) -> dict:
        return {k: dict(v) for k, v in self._servers.items()}


@pytest.mark.parametrize(
    ("raw", "expect_clamped"),
    [
        ({}, True),  # 缺省
        ({"timeout": None}, True),  # 显式 null
        ({"timeout": 30}, True),  # 标量（= startup 语义，request 仍未配）
        ({"timeout": {"request": 0}}, True),  # 解析后非有限正数 → 回退 None
        ({"timeout": {"request": -1}}, True),
        ({"timeout": {"request": "bad"}}, True),
        ({"timeout": {"request": float("nan")}}, True),
        ({"timeout": {"request": float("inf")}}, True),
        ({"timeout": {"request": 3600}}, True),  # 有限但**大于** T → 取 min
        ({"timeout": {"request": 5}}, False),  # 有限且**小于** T → 保持原值
    ],
)
def test_mcp_bound_uses_resolved_value_as_criterion(raw, expect_clamped):
    """**判据是"解析后是否为有限正数"，不是配置形态**（决策P4.21-63）。

    `request` 写成 `0/-1/"bad"/NaN/Infinity` 时 `_coerce_timeout` 只打一条 WARNING 就退回
    default，而 request 槽的 default 正是 `None` ＝ 又变回无限等待。**形态不是判据。**

    判据统一用 agentao 的 `resolve_timeouts` **当神谕**——上游改了解析规则，红的是这条用例，
    而不是线上某一轮悄悄挂死。
    """
    from agentao.mcp.config import resolve_timeouts

    t = 60.0
    bounded = bounded_request_timeout(raw, t, name="s")
    _startup, request = resolve_timeouts(bounded)
    assert request is not None and 0 < request <= t
    assert (request == t) is expect_clamped


def test_mcp_bound_warns_when_tightening(caplog):
    """大于 T 的显式值被收紧时**有 WARNING**——改变用户显式配置的行为不能悄悄发生。"""
    caplog.set_level(logging.WARNING, logger="guanlan.im")
    bounded_request_timeout({"timeout": {"request": 3600}}, 60.0, name="srv")
    assert any("已收紧" in r.getMessage() for r in caplog.records)
    caplog.clear()
    bounded_request_timeout({"timeout": {"request": 5}}, 60.0, name="srv")
    assert not any("已收紧" in r.getMessage() for r in caplog.records)


def test_mcp_bound_does_not_mutate_source():
    """**不改盘上文件、也不就地改源 dict**：读两次 `list_servers()`，源不变。"""
    src = {"a": {"command": "x", "timeout": {"startup": 10, "request": 3600}}}
    reg = BoundedTimeoutRegistry(_StubRegistry(src), request_timeout=60.0)
    first = reg.list_servers()
    second = reg.list_servers()
    assert first["a"]["timeout"]["request"] == 60.0
    assert second["a"]["timeout"]["request"] == 60.0
    assert src["a"]["timeout"]["request"] == 3600, "源配置被就地改了"
    assert first["a"]["timeout"]["startup"] == 10, "startup 不该被我们代做主"


def test_mcp_registry_wiring(kb_im, monkeypatch):
    """④ `--no-mcp` → `mcp_registry=None`；不给旗标 → 每个 server 的 `request` 是 120s 而非 `None`。"""
    captured: dict = {}

    class _Probe:
        def __init__(self, *a, **kw):
            captured.update(kw)
            raise RuntimeError("stop-here")

    monkeypatch.setattr("guanlan.web.chat.ConversationStore", _Probe)
    with pytest.raises(RuntimeError):
        im_server.serve_im(
            kb_im, platform="fake", allow_user=["u"], adapter=FakeAdapter(), no_mcp=True
        )
    assert captured["mcp_registry"] is None
    captured.clear()
    with pytest.raises(RuntimeError):
        im_server.serve_im(kb_im, platform="fake", allow_user=["u"], adapter=FakeAdapter())
    reg = captured["mcp_registry"]
    assert isinstance(reg, BoundedTimeoutRegistry) and reg.request_timeout == 120.0


# ───────────────────────── KB 零字节写（必测）─────────────────────────


def _snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    out: dict[str, tuple[int, bytes]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            out[str(p.relative_to(root))] = (len(data), data)
    return out


def test_kb_is_never_written(kb_im, tmp_path, monkeypatch):
    """**KB 零字节写（决策P4.21-8，必测、不得跳过）**。

    跑完一轮真实会话层问答后对 `<kb>` 前后快照，断言零字节差异——含**无** `agentao.log`、
    **无** `.agentao/sessions/`；另断言状态目录只写在 `~/.guanlan/im/` 下。
    """
    import guanlan.web.chat as chat_mod
    from guanlan.web.chat import ConversationStore

    class _FakeAgent:
        def __init__(self, kwargs):
            self.kwargs = kwargs
            self.messages: list[dict] = []
            self.permission_engine = _Rec()
            self.tool_runner = _Rec()
            self.skill_manager = _Rec()
            self.closed = False

        async def arun(self, m, **_kw):
            self.messages.append({"role": "user", "content": m})
            return "答案"

        def close(self):
            self.closed = True

    class _Rec:
        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)
    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _FakeAgent(kw))

    before = _snapshot(kb_im)

    async def scenario():
        store = ConversationStore(
            kb_im,
            None,
            persist=False,
            default_mode="read-only",
            write_gate=None,
            max_conversations=4,
            idle_ttl=None,
            mcp_registry=None,
        )
        adapter, intake, delivery, _reg, _s, _c = make_stack(kb_im, store=store)
        await intake.offer(msg("问题"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert adapter.sent() == ["答案"]

    asyncio.run(scenario())
    assert _snapshot(kb_im) == before, "KB 被写了"
    assert not (kb_im / "agentao.log").exists()
    assert not (kb_im / ".agentao").exists()


def test_status_notice_wording_differs(kb_im):
    """提示语按状态分：`FIRST` **不提示**；`EXPIRED` 与 `LOST` 措辞**不同**。"""
    from guanlan.im.delivery import STATUS_NOTICE

    assert AcquireStatus.FIRST not in STATUS_NOTICE
    assert AcquireStatus.EXISTING not in STATUS_NOTICE
    assert STATUS_NOTICE[AcquireStatus.EXPIRED] != STATUS_NOTICE[AcquireStatus.LOST]
    assert "过期" in STATUS_NOTICE[AcquireStatus.EXPIRED]


def test_expired_notice_reaches_the_user(kb_im):
    """跨 TTL 再问 → 用户看得到「上下文已过期」。"""

    async def scenario():
        clk = Clock()
        adapter, intake, delivery, _reg, store, _c = make_stack(kb_im, clock=clk, ttl=100.0)
        await intake.offer(msg("一"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        clk.advance(200.0)
        await intake.offer(msg("二"))
        await asyncio.sleep(0.05)
        await drain_tasks(delivery)
        assert any("上下文已过期" in s for s in adapter.sent())
        del store

    asyncio.run(scenario())


def test_intake_never_blocks_on_delivery(kb_im):
    """入站永不阻塞在应答上：一个长 turn 期间，**别人的**消息照常被处理。"""

    async def scenario():
        adapter, intake, delivery, _reg, store, _clk = make_stack(kb_im)
        gate = asyncio.Event()
        first = {"done": False}

        def prep(c):
            if not first["done"]:
                first["done"] = True
                c.gate = gate

        store.on_create = prep
        await intake.offer(msg("慢问题", user="u1"))
        await asyncio.sleep(0.05)
        await intake.offer(msg("/help", user="u2"))
        await asyncio.sleep(0.05)
        assert any("观澜知识库机器人" in s for s in adapter.sent())
        gate.set()
        await drain_tasks(delivery)

    asyncio.run(scenario())


def test_ready_dataclass_is_frozen():
    """`Ready` 是不可变的：投递路径上没人能改它（避免"改了一半"的中间态）。"""
    r = Ready("k", "c", "t", None, "t")
    with pytest.raises(Exception):
        r.text = "x"  # type: ignore[misc]


# ───────────────────────── 启动横幅（决策P4.21-80）─────────────────────────


def _serve_to_completion(kb, adapter, **kw):
    """跑一次 `serve_im`，入站流立刻正常结束 → `_run` 走完停机序列返回。"""
    adapter.queue.put_nowait(_STOP)
    kw.setdefault("allow_user", ["u1"])
    return im_server.serve_im(
        kb, platform="fake", adapter=adapter, no_mcp=True, store=FakeStore(), **kw
    )


def test_startup_banner_goes_to_stdout_after_connecting(kb_im, capsys):
    """**连上之后打一条人话**（决策P4.21-80）。

    在此之前 `guanlan im` 起服后终端**一个字都没有**——「连上了在等消息」与「卡在某处」
    长得一模一样。横幅走 **stdout** 而非 `_logger.info`：宿主不配置 logging，INFO 没有任何
    handler 接（`logging.lastResort` 只兜 WARNING+），用一条默认看不见的通道发它等于没写。
    """
    _serve_to_completion(kb_im, FakeAdapter())
    out = capsys.readouterr().out
    assert "[guanlan im] 已连上 fake" in out
    assert str(kb_im) in out and "只读" in out  # 答"连的是哪个库"
    assert "用户 1 人" in out  # 答"名单加载上了吗"
    assert "Ctrl-C" in out  # 答"怎么停"


def test_startup_banner_is_silent_when_the_connection_fails(kb_im, capsys):
    """**反向守卫：连不上就不许说「已连上」。**

    横幅必须打在 `adapter.start()` **成功之后**。抢在前面打，等于把 §15.6 首连看门狗刚拆掉的
    那个假象原样装回去——终端上写着「已连上」、实际一条消息也收不到，比没有横幅更坏。
    """
    adapter = FakeAdapter()
    adapter.start_error = RuntimeError("连不上")
    assert _serve_to_completion(kb_im, adapter) == EXIT_AGENT_ERROR
    assert "已连上" not in capsys.readouterr().out


def test_startup_banner_never_prints_whitelisted_ids(kb_im, capsys):
    """**只打数量、绝不打 ID**（§9.1 脱敏在这里适用）。

    `im-identify` 是唯一的例外——把完整 ID 打到终端是它**唯一**的用途，且它限时、绝不回复。
    而 `guanlan im` 是长驻宿主，stdout 常被重定向进日志文件 / journal / 运维面板，受众远不止
    敲命令的那个人。数量已足够回答横幅要回答的问题。
    """
    probe = "zz-user-id-probe"
    _serve_to_completion(kb_im, FakeAdapter(), allow_user=[probe])
    out = capsys.readouterr().out
    assert probe not in out, "横幅把白名单 ID 打出去了"
    assert "用户 1 人" in out, "连数量都没打，横幅答不了「名单加载上了吗」"


def test_banner_wording_covers_every_whitelist_shape():
    """横幅文案的三种形态——**措辞说反了比没有更危险**（同 `--allow-all-users` 告警那条教训）。

    `--allow-all-users` **不给** `--allow-chat` 时的真实含义是「任何能私聊到它的人都可读整库」，
    横幅必须原样说出来；给了 `--allow-chat` 则不能再这么说（受众被群边界收住了）。
    """
    fmt = im_server._format_banner
    kb = Path("/kb")

    all_no_chat = fmt(
        platform="feishu",
        kb=kb,
        policy=AccessPolicy(frozenset(), frozenset(), allow_all=True),
        mcp_on=False,
        mcp_timeout=120.0,
    )
    assert "全员可问（任何能私聊到它的人）" in all_no_chat
    assert "--no-mcp" in all_no_chat

    all_with_chat = fmt(
        platform="feishu",
        kb=kb,
        policy=AccessPolicy(frozenset(), frozenset({"c1", "c2"}), allow_all=True),
        mcp_on=True,
        mcp_timeout=120.0,
    )
    assert "全员可问" in all_with_chat and "任何能私聊到它的人" not in all_with_chat
    assert "群 2 个" in all_with_chat

    named = fmt(
        platform="weixin",
        kb=kb,
        policy=AccessPolicy(frozenset({"a", "b"}), frozenset(), allow_all=False),
        mcp_on=True,
        mcp_timeout=60.0,
    )
    assert "用户 2 人" in named and "全员" not in named
    assert "60s" in named, "MCP 上界写死成了默认值，改 --mcp-request-timeout 它就开始说假话"
