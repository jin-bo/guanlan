"""P4.21 个人微信适配器测试（见 docs/P4.21-IM宿主.md §10.2）。

`httpx.MockTransport` 驱动，**零真实网络、不 skip**——`httpx` 是 `[im-weixin]` extra 的唯一依赖，
但它已随 agentao 依赖链进环境；真缺时整体 skip（本地无 extra 的开发机）。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

from guanlan.errors import EXIT_AGENT_ERROR, EXIT_USAGE, GuanlanError  # noqa: E402
from guanlan.im.adapters import weixin as wx  # noqa: E402
from guanlan.im.adapters.weixin import (  # noqa: E402
    CAPS,
    EP_GETCONFIG,
    EP_GETUPDATES,
    EP_SENDMESSAGE,
    EP_SENDTYPING,
    ITEM_TEXT,
    WeixinAdapter,
    is_stale_session,
    map_inbound,
)
from guanlan.im.contract import CHAT_DM, KIND_OTHER, KIND_TEXT  # noqa: E402
from guanlan.im.state import CredentialLock, read_json, write_json_atomic  # noqa: E402

ACCOUNT = "acc_self"


@pytest.fixture(autouse=True)
def _im_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANLAN_IM_HOME", str(tmp_path / "imhome"))


@pytest.fixture
def state(tmp_path: Path) -> Path:
    d = tmp_path / "weixin"
    d.mkdir()
    write_json_atomic(
        d / "account.json",
        {"account_id": ACCOUNT, "token": "tok", "base_url": "https://x"},
    )
    return d


def text_item(text: str) -> dict:
    return {"type": ITEM_TEXT, "text_item": {"text": text}}


def frame(**kw) -> dict:
    base = {
        "from_user_id": "peer1",
        "to_user_id": ACCOUNT,
        "message_id": "m1",
        "item_list": [text_item("你好")],
    }
    base.update(kw)
    return base


class Recorder:
    """把每次请求记下来，并按端点回一份可编排的响应。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.headers: list[dict] = []
        self.responses: dict[str, list[dict]] = {}

    def queue(self, endpoint: str, *payloads: dict) -> None:
        self.responses.setdefault(endpoint, []).extend(payloads)

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        path = request.url.path
        self.requests.append((path, body))
        self.headers.append(dict(request.headers))
        queued = self.responses.get(path)
        if queued:
            return httpx.Response(200, json=queued.pop(0))
        return httpx.Response(200, json={"ret": 0})

    def bodies(self, endpoint: str) -> list[dict]:
        return [b for p, b in self.requests if p == endpoint]


def make_adapter(
    state: Path, rec: Recorder, *, sleeps: list[float] | None = None
) -> WeixinAdapter:
    async def fake_sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)
        await asyncio.sleep(0)

    return WeixinAdapter(
        state, transport=httpx.MockTransport(rec.handler), sleep=fake_sleep
    )


async def collect(adapter: WeixinAdapter, *, count: int, extra_polls: int = 1) -> list:
    """在独立 task 里收 `count` 条，再多轮询几次（让批次收尾的游标推进跑到），然后**取消**它。

    **不能用 `async for … break`**：`break` 发生在生成器挂在 `yield` 的那一刻，批次收尾的
    「推进 offset」根本没跑——这正是「取消点上不留半提交状态」的契约（决策P4.21-56），
    也是本适配器 at-least-once 的来源。
    """
    got: list = []

    async def consume() -> None:
        async for m in adapter.inbound():
            got.append(m)

    task = asyncio.create_task(consume())
    for _ in range(4000):
        await asyncio.sleep(0)
        if len(got) >= count:
            break
    for _ in range(extra_polls * 8):  # 再放几轮，让批次收尾的游标推进落地
        # **必须是真 sleep**：游标/context-token 落盘经 `anyio.to_thread`（§4.3 卸线程通则，
        # `write_json_atomic` 会 fsync），纯 `sleep(0)` 只让出一个 tick，等不到线程池回话，
        # 于是"批次收尾已落地"这件事在断言时还没发生。
        await asyncio.sleep(0.005)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return got


# ───────────────────────── 请求构造 ─────────────────────────


def test_headers_and_base_info(state):
    """headers 六件套齐全、body 含 `base_info.channel_version`（漏掉服务端会拒）。"""

    async def scenario():
        rec = Recorder()
        ad = make_adapter(state, rec)
        await ad.start()
        await ad.send("peer1", "答案")
        await ad.close()
        h = rec.headers[0]
        assert h["authorization"] == "Bearer tok"
        assert h["authorizationtype"] == "ilink_bot_token"
        assert h["ilink-app-id"] == "bot"
        assert h["ilink-app-clientversion"] == str((2 << 16) | (2 << 8) | 0)
        assert h["x-wechat-uin"]
        assert h["content-type"].startswith("application/json")
        body = rec.bodies(EP_SENDMESSAGE)[0]
        assert body["base_info"]["channel_version"] == "2.2.0"

    asyncio.run(scenario())


def test_start_without_credentials_is_usage_error(tmp_path):
    """缺凭据 → `EXIT_USAGE` 并指向 `im-login`（**不吐 traceback**）。"""

    async def scenario():
        ad = WeixinAdapter(tmp_path / "empty")
        with pytest.raises(GuanlanError) as exc:
            await ad.start()
        assert exc.value.exit_code == EXIT_USAGE and "im-login" in str(exc.value)

    asyncio.run(scenario())


def test_start_refuses_credential_without_account_id(tmp_path):
    """**反向用例**：`token` 齐、`account_id` 缺的凭据必须**拒启**，不能当"可选字段"降级。

    上一条只证明「完全没凭据会被挡」；缺 `account_id` 这半吊子凭据能过闸的话，`map_inbound`
    的自消息过滤（`from_user == account_id`）就恒不命中——机器人把自己的回复当成新提问，
    **回声循环 + 无上限 LLM 花费**，而现场只看到"它在自言自语"，与启动这一步毫无关联。
    旧版本 / 参考实现写下的 `account.json` 正可能没有这个键，故不是理论问题。
    """
    d = tmp_path / "weixin"
    d.mkdir()
    write_json_atomic(d / "account.json", {"token": "tok", "base_url": "https://x"})

    async def scenario():
        with pytest.raises(GuanlanError) as exc:
            await WeixinAdapter(d).start()
        assert exc.value.exit_code == EXIT_USAGE
        assert "account_id" in str(exc.value) and "im-login" in str(exc.value)

    asyncio.run(scenario())


def test_caps_follow_source_not_docs():
    """§7.1 三条口径差：一律以参考实现**源码**为准（其文档三处与代码不符）。"""
    assert CAPS.max_message_length == 2000  # 文档写 4000
    assert CAPS.chunk_delay_s == 1.5  # 文档写 0.3
    assert CAPS.supports_group is False and CAPS.supports_edit is False


# ───────────────────────── 入站与映射 ─────────────────────────


def test_getupdates_maps_and_advances_cursor(state):
    """getupdates → `InboundMessage` 映射正确；游标落盘并在其后推进。"""

    async def scenario():
        rec = Recorder()
        rec.queue(
            EP_GETUPDATES,
            {"ret": 0, "msgs": [frame()], "get_updates_buf": "cur2"},
            {"ret": 0, "msgs": [], "get_updates_buf": "cur3"},
        )
        ad = make_adapter(state, rec)
        await ad.start()
        got = await collect(ad, count=1)
        await ad.close()
        assert got[0].chat_id == "peer1" and got[0].user_id == "peer1"
        assert read_json(state / "sync.json")["get_updates_buf"] == "cur3"

    asyncio.run(scenario())


def test_long_poll_timeout_is_not_an_error(state):
    """**长轮询超时不是错误**——返回空并立即重轮询，不计入失败、不退避。"""

    async def scenario():
        rec = Recorder()
        sleeps: list[float] = []
        rec.queue(
            EP_GETUPDATES,
            {"ret": 0, "msgs": [], "get_updates_buf": "c1"},
            {"ret": 0, "msgs": [], "get_updates_buf": "c2"},
            {"ret": 0, "msgs": [frame()], "get_updates_buf": "c3"},
        )
        ad = make_adapter(state, rec, sleeps=sleeps)
        await ad.start()
        got = await collect(ad, count=1)
        await ad.close()
        assert len(got) == 1 and sleeps == [], "空轮询触发了退避"

    asyncio.run(scenario())


def test_self_message_is_dropped():
    """**自消息过滤**：`from_user_id == account_id` 直接丢——否则回声循环。**这条不能漏。**"""
    assert map_inbound(frame(from_user_id=ACCOUNT), account_id=ACCOUNT) is None


def test_group_frame_is_dropped():
    """群消息（`room_id` 非空）被丢弃；`caps.supports_group is False`。"""
    assert map_inbound(frame(room_id="r1"), account_id=ACCOUNT) is None
    assert map_inbound(frame(chat_room_id="r1"), account_id=ACCOUNT) is None
    assert CAPS.supports_group is False


def test_missing_message_id_is_dropped():
    """`message_id` 缺失 → 丢弃且**不自造 id**（自造会绕过去重）。"""
    f = frame()
    f.pop("message_id")
    assert map_inbound(f, account_id=ACCOUNT) is None


@pytest.mark.parametrize(
    ("items", "kind", "attach", "text"),
    [
        ([text_item("甲"), text_item("乙")], KIND_TEXT, False, "甲\n乙"),
        ([{"type": 2, "image_item": {}}], KIND_OTHER, True, ""),
        # 混合 item：判 `"other"` 走附件提示，`text` 仍填已提取部分但**不进 LLM**
        (
            [text_item("这张图什么意思"), {"type": 2}],
            KIND_OTHER,
            True,
            "这张图什么意思",
        ),
    ],
)
def test_eight_field_mapping(items, kind, attach, text):
    """**八字段映射逐条对 §7.2.1**（决策P4.21-43）：否则"映射正确"这条测试没有标准答案。

    混合 item 的取舍：只答文字部分会给出**看似回答了、实则没看到图**的答案——
    比明说「暂不处理附件」更糟。**宁可少答，不可假答。**
    """
    m = map_inbound(frame(item_list=items), account_id=ACCOUNT)
    assert m is not None
    assert m.tenant == ACCOUNT  # 取自本机凭据，**不取自消息**
    assert m.chat_id == m.user_id == "peer1"  # 单聊的正常形态，不是 bug
    assert m.chat_type == CHAT_DM  # 恒 dm，**永不**吐出 group
    assert m.mentioned_me is False  # 单聊无 @ 概念
    assert m.msg_kind == kind and m.has_attachments is attach and m.text == text


# ───────────────────────── 错误码分流（§7.4）─────────────────────────


def test_is_stale_session_triage():
    """`-14` 是 stale；`-2` **只有** `errmsg == "unknown error"` 才是 stale，其余是真频控。"""
    assert is_stale_session(-14, 0, "") is True
    assert is_stale_session(0, -14, "") is True
    assert is_stale_session(-2, 0, "unknown error") is True
    assert is_stale_session(-2, 0, "Unknown Error") is True  # 大小写无关
    assert is_stale_session(-2, 0, "rate limit") is False


def test_inbound_stale_pauses_and_warns(state, caplog):
    """① **入站** `-14` → 告警 + 暂停 600s（注入时钟，不真 sleep）。"""
    import logging

    caplog.set_level(logging.ERROR, logger="guanlan.im")

    async def scenario():
        rec = Recorder()
        sleeps: list[float] = []
        rec.queue(
            EP_GETUPDATES,
            {"ret": -14, "errmsg": "unknown error"},
            {"ret": 0, "msgs": [frame()], "get_updates_buf": "c1"},
        )
        ad = make_adapter(state, rec, sleeps=sleeps)
        await ad.start()
        got = await collect(ad, count=1)
        await ad.close()
        assert len(got) == 1 and sleeps == [600.0]

    asyncio.run(scenario())
    assert any("im-login" in r.getMessage() for r in caplog.records)


def test_outbound_stale_with_token_retries_without_it(state):
    """② **出站 `-14` 且带 token → 丢 token 重试一次并成功**（这是修正的核心，必须有正例）。

    原设计「`-14` 一律重扫码 + 停 600s」会在**正常的 context token 过期**时错误停服 10 分钟
    并要求重登录（决策P4.21-30）。
    """

    async def scenario():
        rec = Recorder()
        sleeps: list[float] = []
        rec.queue(
            EP_GETUPDATES,
            {"ret": 0, "msgs": [frame(context_token="ctx1")], "get_updates_buf": "c1"},
        )
        rec.queue(EP_SENDMESSAGE, {"ret": -14, "errmsg": "unknown error"}, {"ret": 0})
        ad = make_adapter(state, rec, sleeps=sleeps)
        await ad.start()
        assert await collect(ad, count=1)
        await ad.send("peer1", "答案")
        await ad.close()
        sends = rec.bodies(EP_SENDMESSAGE)
        assert len(sends) == 2
        assert sends[0]["msg"]["context_token"] == "ctx1"
        assert "context_token" not in sends[1]["msg"], "去 token 重试没把该键摘掉"
        assert sleeps == [], "错误地当成频控退避了"
        assert read_json(state / "context-tokens.json") == {}, "陈旧 token 没被丢弃"

    asyncio.run(scenario())


def test_outbound_stale_without_token_raises(state):
    """③ 已去 token 重试过仍 stale → 抛错交上层，**不静默**。"""

    async def scenario():
        rec = Recorder()
        rec.queue(EP_SENDMESSAGE, {"ret": -14, "errmsg": "unknown error"})
        ad = make_adapter(state, rec)
        await ad.start()
        with pytest.raises(GuanlanError) as exc:
            await ad.send("peer1", "答案")
        await ad.close()
        assert "im-login" in str(exc.value)

    asyncio.run(scenario())


def test_outbound_rate_limit_backs_off_then_breaks(state):
    """④ 出站 `-2` + **其他** errmsg → 3× 退避重试 + 熔断（**不**走 stale 分支）。"""

    async def scenario():
        rec = Recorder()
        sleeps: list[float] = []
        rec.queue(EP_SENDMESSAGE, *[{"ret": -2, "errmsg": "rate limit"}] * 4)
        ad = make_adapter(state, rec, sleeps=sleeps)
        await ad.start()
        with pytest.raises(GuanlanError) as exc:
            await ad.send("peer1", "答案")
        await ad.close()
        assert "频控" in str(exc.value)
        assert sleeps == [3.0, 6.0, 9.0], f"退避序列不对：{sleeps}"
        assert len(rec.bodies(EP_SENDMESSAGE)) == 4

    asyncio.run(scenario())


def test_context_token_roundtrip_and_absence(state):
    """`context_token`：入站落盘 → 出站回带；缺失时**不**把该键塞进 payload。"""

    async def scenario():
        rec = Recorder()
        rec.queue(
            EP_GETUPDATES,
            {"ret": 0, "msgs": [frame(context_token="ctxA")], "get_updates_buf": "c1"},
        )
        ad = make_adapter(state, rec)
        await ad.start()
        assert await collect(ad, count=1)
        await ad.send("peer1", "答案")
        await ad.send("peer2", "另一位")  # 从没入站过 → 无 token
        await ad.close()
        sends = rec.bodies(EP_SENDMESSAGE)
        assert sends[0]["msg"]["context_token"] == "ctxA"
        assert "context_token" not in sends[1]["msg"]
        assert read_json(state / "context-tokens.json") == {"peer1": "ctxA"}

    asyncio.run(scenario())


def test_sync_cursor_survives_restart(state):
    """`sync.json` 落盘与重启续读；**原子替换**（断言没有半截临时文件残留）。"""

    async def scenario():
        rec = Recorder()
        rec.queue(
            EP_GETUPDATES, {"ret": 0, "msgs": [frame()], "get_updates_buf": "cursorX"}
        )
        ad = make_adapter(state, rec)
        await ad.start()
        assert await collect(ad, count=1)
        await ad.close()
        assert read_json(state / "sync.json")["get_updates_buf"] == "cursorX"
        # 模拟重启：新适配器必须**带着上次的游标**发第一次请求，否则重放旧消息
        rec2 = Recorder()
        rec2.queue(
            EP_GETUPDATES,
            {"ret": 0, "msgs": [frame(message_id="m2")], "get_updates_buf": "cursorY"},
        )
        ad2 = make_adapter(state, rec2)
        await ad2.start()
        assert await collect(ad2, count=1)
        await ad2.close()
        assert rec2.bodies(EP_GETUPDATES)[0]["get_updates_buf"] == "cursorX"
        assert not list(state.glob(".*tmp")), "留下了半截临时文件"

    asyncio.run(scenario())


def test_typing_ticket_is_cached(state):
    """打字指示：`typing_ticket` 由 `getconfig` 取、**按 user 缓存 10 分钟**。"""

    async def scenario():
        rec = Recorder()
        rec.queue(EP_GETCONFIG, {"ret": 0, "typing_ticket": "tk"})
        ad = make_adapter(state, rec)
        await ad.start()
        await ad.typing("peer1", True)
        await ad.typing("peer1", False)
        await ad.close()
        assert len(rec.bodies(EP_GETCONFIG)) == 1, "ticket 没被缓存"
        typings = rec.bodies(EP_SENDTYPING)
        assert [t["status"] for t in typings] == [1, 2]
        assert all(t["typing_ticket"] == "tk" for t in typings)

    asyncio.run(scenario())


def test_edit_is_not_supported(state):
    """`caps.supports_edit=False` → 核心永不调 `edit`；真调到必须炸得明白。"""

    async def scenario():
        ad = make_adapter(state, Recorder())
        with pytest.raises(NotImplementedError):
            await ad.edit(None, "x")  # type: ignore[arg-type]

    asyncio.run(scenario())


# ───────────────────────── 凭据锁与 CLI（§8.5/§9.1）─────────────────────────


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("guanlan im-login", "guanlan im-login"),
        ("guanlan im-login", "guanlan im"),
        ("guanlan im-login", "guanlan im-identify"),
    ],
)
def test_credential_lock_pairs_are_exclusive(first, second):
    """**三对互斥**：第二个进程 `EXIT_USAGE`，且报错含 owner pid **与子命令名**。

    只说「凭据被占用」，用户分不清是自己起了两个服还是忘了关 identify（决策P4.21-38）。
    """
    a = CredentialLock("weixin", command=first)
    a.acquire()
    try:
        b = CredentialLock("weixin", command=second)
        with pytest.raises(GuanlanError) as exc:
            b.acquire()
        assert exc.value.exit_code == EXIT_USAGE
        assert first in str(exc.value) and str(os.getpid()) in str(exc.value)
    finally:
        a.release()


def test_stale_lock_is_cleaned():
    """陈锁（pid 已死）自动清理。"""
    lock = CredentialLock("weixin", command="guanlan im")
    write_json_atomic(lock.path, {"pid": 999_999_998, "command": "guanlan im-identify"})
    lock.acquire()
    assert read_json(lock.path)["pid"] == os.getpid()
    lock.release()
    assert not lock.path.exists()


def test_im_login_rejects_feishu_at_parse_time():
    """`im-login --platform feishu` 在 **CLI 解析期**即拒（不碰锁、不进运行期）。"""
    from guanlan.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["im-login", "--platform", "feishu"])


def test_run_login_rejects_platform_without_flow():
    """`run_login` 查 `LOGIN_FLOWS` 注册表——**不是** `if platform == "weixin"`（决策P4.21-3）。"""
    from guanlan.im.server import run_login

    with pytest.raises(GuanlanError) as exc:
        run_login(platform="feishu")
    assert exc.value.exit_code == EXIT_USAGE and "环境变量" in str(exc.value)


def test_identify_prints_full_id_and_never_replies(state, capsys, monkeypatch):
    """`im-identify`：收到消息后**打印完整 ID 且不发出任何出站请求**；到时自动退出。

    「绝不回复」是安全性的来源——对方只看到「发了没人理」，不泄露「这里有个知识库机器人」。
    """
    rec = Recorder()
    rec.queue(EP_GETUPDATES, {"ret": 0, "msgs": [frame()], "get_updates_buf": "c1"})

    def fake_factory(_state, *, group_wanted=False):
        return make_adapter(state, rec)

    monkeypatch.setitem(
        __import__("guanlan.im.adapters", fromlist=["ADAPTERS"]).ADAPTERS,
        "weixin",
        fake_factory,
    )
    from guanlan.im.server import run_identify

    code = run_identify(platform="weixin", seconds=0.3)
    out = capsys.readouterr().out
    assert code == 0
    assert "peer1" in out and "--allow-user" in out
    assert rec.bodies(EP_SENDMESSAGE) == [], "identify 发出了出站请求"


# ── 扫码登录（§7.6，决策P4.21-23）────────────────────────────────────────────
# 这一段是**真机联调打出来的**：`im-login` 首跑回 `missing bot_type`，因为 `bot_type` 是 query
# 参数而非请求体字段，且合法值只有 `3`（§13-3 实测表）。整条登录流此前**零覆盖**——
# `run_qrcode_login` 早就留了 `transport` 形参专供伪造，却从没有测试用过它。


class QRServer:
    """伪造 iLink 扫码端点，**照真机的口径校验**：`bot_type` 不在 query 里就回 missing。"""

    def __init__(
        self, *, pages: list[dict] | None = None, require_bot_type: bool = True
    ) -> None:
        self.urls: list[httpx.URL] = []
        self.require_bot_type = require_bot_type
        # `pages or [...]` 会把显式传入的**空列表**当成"没传"，于是"永远 wait"的用例
        # 反而拿到了成功页——故用 `is None` 判据。
        # **真机实测的确认响应形态**（§7.6）：token 叫 `bot_token`、机器人身份叫
        # `ilink_bot_id`（形如 `xxx@im.bot`），另有服务端自报的 `baseurl`。
        default = [
            {
                "ret": 0,
                "status": "confirmed",
                "bot_token": "botid@im.bot:hex",
                "ilink_bot_id": "botid@im.bot",
                "ilink_user_id": "the-human-who-scanned",
                "baseurl": "https://ilinkai.weixin.qq.com",
            }
        ]
        self.pages = list(default if pages is None else pages)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(request.url)
        path = request.url.path
        if path == wx.EP_QRCODE:
            if self.require_bot_type and request.url.params.get("bot_type") != str(
                wx.BOT_TYPE
            ):
                return httpx.Response(
                    200, json={"err_msg": "missing bot_type", "ret": 1}
                )
            return httpx.Response(
                200,
                json={
                    "ret": 0,
                    "qrcode": "c0de",
                    "qrcode_img_content": "https://liteapp.weixin.qq.com/q/X?qrcode=c0de",
                },
            )
        if path == wx.EP_QRCODE_STATUS:
            return httpx.Response(
                200, json=self.pages.pop(0) if self.pages else {"ret": 0}
            )
        return httpx.Response(200, json={"ret": 0})

    def params(self, endpoint: str) -> list[dict]:
        return [dict(u.params) for u in self.urls if u.path == endpoint]


def test_qrcode_login_puts_bot_type_in_query_not_body(tmp_path, capsys):
    """`bot_type` 必须走 **query**：放进请求体服务端读不到，会回 `missing bot_type`（真机实测）。"""
    server = QRServer()
    d = tmp_path / "weixin"
    d.mkdir()
    code = asyncio.run(
        wx.run_qrcode_login(d, transport=httpx.MockTransport(server.handler))
    )
    assert code == 0, capsys.readouterr().out
    assert server.params(wx.EP_QRCODE) == [{"bot_type": "3"}]
    assert read_json(d / "account.json") == {
        # `ilink_bot_id` 而非 `ilink_user_id`：后者是**扫码那个人**，取错就自消息过滤失效 → 回声循环。
        "account_id": "botid@im.bot",
        "token": "botid@im.bot:hex",
        "base_url": "https://ilinkai.weixin.qq.com",
    }


def test_qrcode_login_surfaces_server_error_detail(tmp_path, capsys):
    """**反向用例**：服务端回了 `err_msg` 却被我们吞掉时，这条必须红。

    原实现只打「服务端未返回 qrcode 字段」，把唯一的线索（`missing bot_type` / `ret=1`）
    扔了——操作者除了重试无事可做，而重试一万次也还是这个结果。漏报是沉默的。
    """
    d = tmp_path / "weixin"
    d.mkdir()

    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"err_msg": "missing bot_type", "ret": 1})

    code = asyncio.run(wx.run_qrcode_login(d, transport=httpx.MockTransport(deny)))
    out = capsys.readouterr().out
    assert code == EXIT_AGENT_ERROR
    assert "missing bot_type" in out and "ret=1" in out
    assert not (d / "account.json").exists()


def test_qrcode_poll_tolerates_read_timeout(tmp_path, monkeypatch, capsys):
    """长轮询被本地读超时掐断是**预期内**的，不该让登录带着 traceback 崩掉。"""
    monkeypatch.setattr(wx, "QRCODE_POLL_INTERVAL", 0.0)
    d = tmp_path / "weixin"
    d.mkdir()
    calls: list[str] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path == wx.EP_QRCODE:
            return httpx.Response(
                200, json={"ret": 0, "qrcode": "c0de", "qrcode_img_content": ""}
            )
        calls.append("status")
        if len(calls) == 1:
            raise httpx.ReadTimeout("long-poll cut", request=request)
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "status": "confirmed",
                "bot_token": "tok2",
                "ilink_bot_id": "bot2@im.bot",
            },
        )

    code = asyncio.run(wx.run_qrcode_login(d, transport=httpx.MockTransport(flaky)))
    assert code == 0, capsys.readouterr().out
    assert len(calls) == 2, "读超时后没有重来"
    assert read_json(d / "account.json")["token"] == "tok2"


def test_qrcode_poll_gives_up_at_wait_deadline(monkeypatch):
    """服务端一直 `wait`（用户扫码前走开）→ 到墙钟上限收场，**不是**无限静默等待。

    「已过期」的响应形态从未被观测到，故不能只靠 `status == "expired"` 收场（决策P4.21-57
    的活死人正是这么来的）。
    """
    monkeypatch.setattr(wx, "QRCODE_POLL_INTERVAL", 0.0)
    server = QRServer(pages=[])  # 永远回 {"ret": 0}，既无 token 也不说过期
    now = [0.0]

    def clock() -> float:
        now[0] += 20.0
        return now[0]

    async def go():
        async with httpx.AsyncClient(
            base_url=wx.BASE_URL, transport=httpx.MockTransport(server.handler)
        ) as c:
            return await wx._poll_qrcode(c, "c0de", clock=clock)

    assert asyncio.run(go()) is None
    assert now[0] >= wx.QRCODE_WAIT_SECONDS


QR_URL = "https://liteapp.weixin.qq.com/q/X?qrcode=c0de&bot_type=3"


def test_qrcode_is_drawn_in_terminal(capsys):
    """**默认在终端画码**，用户不用打开任何链接——链接只作补充信息附在后面。"""
    pytest.importorskip("qrcode")
    wx._present_qrcode(QR_URL, "c0de")
    out = capsys.readouterr().out
    assert "█" in out, "没有画出二维码"
    assert out.count("\n") > 10, "画出来的行数不像一个二维码"
    assert "扫描上方二维码" in out and QR_URL in out


def test_qrcode_falls_back_when_terminal_too_narrow(capsys, monkeypatch):
    """窄终端会把每行折行、画出**扫不出来的乱码**，而用户看不出哪里不对，只当"码坏了"。

    宁可不画并说明原因。
    """
    pytest.importorskip("qrcode")
    monkeypatch.setattr(
        wx.shutil,
        "get_terminal_size",
        lambda fallback=(80, 24): os.terminal_size((20, 24)),
    )
    wx._present_qrcode(QR_URL, "c0de")
    out = capsys.readouterr().out
    assert "█" not in out
    assert "终端宽度不足" in out and QR_URL in out


def test_qrcode_missing_dep_says_how_to_fix(capsys, monkeypatch):
    """`qrcode` 缺失时给出**可执行的下一步**，而不是默默退回一行光秃秃的 URL。"""
    import builtins

    real_import = builtins.__import__

    def no_qrcode(name, *a, **kw):
        if name == "qrcode":
            raise ImportError("no qrcode")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_qrcode)
    wx._present_qrcode(QR_URL, "c0de")
    out = capsys.readouterr().out
    assert "pip install qrcode" in out and QR_URL in out


def test_non_url_qrcode_content_is_printed_raw(capsys):
    """服务端若哪天不回 URL，也**必须把原始值交给操作者**——呈现不了不等于失败。"""
    wx._present_qrcode("", "c0de")
    out = capsys.readouterr().out
    assert "c0de" in out


def test_login_prefers_server_reported_baseurl(tmp_path, capsys):
    """服务端自报的 `baseurl` **优先于常量**——它要迁域名/分片时这是唯一的通知渠道。"""
    server = QRServer(
        pages=[
            {
                "ret": 0,
                "status": "confirmed",
                "bot_token": "t",
                "ilink_bot_id": "b@im.bot",
                "baseurl": "https://shard-7.example.com",
            }
        ]
    )
    d = tmp_path / "weixin"
    d.mkdir()
    code = asyncio.run(
        wx.run_qrcode_login(d, transport=httpx.MockTransport(server.handler))
    )
    assert code == 0, capsys.readouterr().out
    assert read_json(d / "account.json")["base_url"] == "https://shard-7.example.com"


def test_confirmed_without_token_field_fails_loudly(tmp_path):
    """**反向用例**：确认了却取不到 token（字段改名）必须**炸响**，而不是继续 `wait`。

    这正是本轮真机踩到的坑：实现认猜出来的 `token`、服务端给的是 `bot_token`，于是扫完码
    一切"正常"、只是永远等下去——用户看到「扫了没反应」，日志一个字都没有。
    静默失败比崩溃难查一个数量级，所以这里宁可崩。
    """
    server = QRServer(pages=[{"ret": 0, "status": "confirmed", "some_new_name": "t"}])
    d = tmp_path / "weixin"
    d.mkdir()
    with pytest.raises(GuanlanError) as exc:
        asyncio.run(
            wx.run_qrcode_login(d, transport=httpx.MockTransport(server.handler))
        )
    assert "bot_token" in str(exc.value) and "some_new_name" in str(exc.value)
    assert not (d / "account.json").exists()


def test_confirmed_without_bot_id_field_fails_loudly(tmp_path):
    """**同一条判据、另一个字段**：`ilink_bot_id` 取不到时也必须拒绝写凭据。

    上一条守的是 `bot_token`（拿不到 token ⇒ 根本连不上，症状显眼）。`ilink_bot_id` 缺失
    更阴：登录**看起来成功**，写出一份 `account_id: ""` 的凭据，直到下次起服才以"机器人
    自言自语"的形式发作。宁可在这里炸响，也不落一份过滤失效的凭据。
    """
    server = QRServer(
        pages=[{"ret": 0, "status": "confirmed", "bot_token": "t", "renamed_id": "b@im.bot"}]
    )
    d = tmp_path / "weixin"
    d.mkdir()
    with pytest.raises(GuanlanError) as exc:
        asyncio.run(
            wx.run_qrcode_login(d, transport=httpx.MockTransport(server.handler))
        )
    assert "ilink_bot_id" in str(exc.value) and "renamed_id" in str(exc.value)
    assert not (d / "account.json").exists(), "写出了 account_id 为空的凭据 → 回声循环"


def test_bot_id_not_user_id_is_the_account_identity():
    """`ilink_bot_id` 是机器人自己（`xxx@im.bot`），`ilink_user_id` 是**扫码的人**。

    取错则 `map_inbound` 的自消息过滤失效——机器人会把自己的话当成新消息，**回声循环**。
    """
    assert wx.FIELD_BOT_ID == "ilink_bot_id"
    bot = "b@im.bot"
    # account_id 取对时：机器人自己发的帧被丢弃
    assert map_inbound(frame(from_user_id=bot), account_id=bot) is None
    # 取成扫码人的 id 时：同一帧会被当成正常入站消息 —— 回声循环就是这么来的
    assert map_inbound(frame(from_user_id=bot), account_id="the-human") is not None
