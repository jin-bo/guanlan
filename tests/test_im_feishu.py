"""P4.21 飞书适配器测试（见 docs/P4.21-IM宿主.md §10.3）。

**skip / fail 分两层，不得混同（决策P4.21-45，经评审 #22 细化）**：

- **`import lark_oapi` 本身失败** → `pytest.skip`（本地开发机没装 extra，合理）；
- **SDK 导入成功但签名里没有 `extra_ua_tags`** → **`pytest.fail` 并提示升级到 ≥1.6.8**。

后者正是这条测试存在的理由——一次 SDK 降级如果只换来一条 skip，等于没测。

除那一条签名探针外，本文件其余用例用**假 SDK 模块**跑，故在任何环境下都执行：
飞书的真实逻辑（围栏切行、结构化 `mentions`、post→text 降级、线程桥）与 SDK 版本无关，
不该因为本地没装 extra 就整体失守。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import threading
import types
from pathlib import Path

import pytest

from guanlan.errors import EXIT_USAGE, GuanlanError
from guanlan.im import server as im_server
from guanlan.im.adapters import feishu as fs
from guanlan.im.adapters.feishu import (
    CAPS,
    ENV_APP_ID,
    ENV_APP_SECRET,
    ENV_BOT_OPEN_ID,
    ENV_DOMAIN,
    MIN_SDK_VERSION,
    FeishuAdapter,
    build_markdown_post_rows,
    map_event,
    post_content,
)
from guanlan.im.contract import CHAT_DM, CHAT_GROUP, KIND_OTHER, KIND_TEXT
from guanlan.im.state import CredentialLock

BOT_OPEN_ID = "ou_bot"


# ───────────────────────── 真 SDK 探针（唯一一条 skip/fail 分层的用例）─────────


def test_ws_client_accepts_extra_ua_tags():
    """**①（决策P4.21-29）**：真 SDK 的 WS 客户端必须接受 `extra_ua_tags`。

    不传这个 tag，飞书服务端**不会**经 WebSocket 推送群 @ 事件（只推 P2P 单聊），
    而该参数自 `lark-oapi` **1.6.8** 起才有。

    > **导入不到 → skip**（本地没装 extra）；**导入到了但签名缺 tag → fail**。
    """
    try:
        import lark_oapi
    except ImportError:
        pytest.skip("本地未装 guanlan-wiki[im-feishu]；CI 必装并必跑（见 §10.3）")
    params = inspect.signature(lark_oapi.ws.Client.__init__).parameters
    if "extra_ua_tags" not in params:
        pytest.fail(
            f"lark-oapi 版本过旧：WS 客户端不接受 extra_ua_tags，群 @ 事件收不到。"
            f"请升级到 >={MIN_SDK_VERSION}。"
        )


# ───────────────────────── 假 SDK ─────────────────────────


class _FakeResponse:
    def __init__(self, ok: bool, *, message_id: str = "", code: int = 0, msg: str = "") -> None:
        self._ok = ok
        self.code = code
        self.msg = msg
        self.data = types.SimpleNamespace(message_id=message_id)

    def success(self) -> bool:
        return self._ok


class _FakeMessageApi:
    def __init__(self) -> None:
        self.creates: list = []
        self.updates: list = []
        self.reject_post = False

    def create(self, request):
        self.creates.append(request)
        if self.reject_post and request.body.msg_type == "post":
            return _FakeResponse(False, code=230001, msg="invalid post content")
        return _FakeResponse(True, message_id=f"om_{len(self.creates)}")

    def update(self, request):
        self.updates.append(request)
        if self.reject_post and request.body.msg_type == "post":
            return _FakeResponse(False, code=230001, msg="invalid post content")
        return _FakeResponse(True)


class _Builder:
    """通用的链式 builder：`.x(v)` 记进 kwargs，`.build()` 出一个 SimpleNamespace。"""

    def __init__(self, cls) -> None:
        self._cls = cls
        self._kw: dict = {}

    def __getattr__(self, name):
        def setter(value):
            self._kw[name] = value
            return self

        return setter

    def build(self):
        return self._cls(**self._kw)


class _MessageBody:
    def __init__(self, **kw) -> None:
        self.receive_id = kw.get("receive_id", "")
        self.msg_type = kw.get("msg_type", "")
        self.content = kw.get("content", "")

    @staticmethod
    def builder():
        return _Builder(_MessageBody)


class _MessageRequest:
    def __init__(self, **kw) -> None:
        self.receive_id_type = kw.get("receive_id_type", "")
        self.message_id = kw.get("message_id", "")
        self.body = kw.get("request_body")

    @staticmethod
    def builder():
        return _Builder(_MessageRequest)


class _FakeWSClient:
    instances: list = []

    # 形参**逐一具名**（不是 `**kw`）：`_require_channel_tag` 用 `inspect.signature` 探
    # `extra_ua_tags`，假件若用 `**kw` 就探不到——那样这条探针在本文件里等于被架空。
    def __init__(
        self, *, app_id, app_secret, domain, event_handler, extra_ua_tags=None
    ) -> None:
        self.kwargs = {
            "app_id": app_id,
            "app_secret": app_secret,
            "domain": domain,
            "event_handler": event_handler,
            "extra_ua_tags": extra_ua_tags,
        }
        self.started = threading.Event()
        self.stopped = threading.Event()
        # 真 SDK 在 `_connect()` 成功后把 socket 存进 `_conn`，断开时置回 None——首连看门狗
        # 就是读它当"连上了"的正面证据，故假件必须照着模型，否则整组用例都在测一个不存在的形状。
        self._conn = None
        _FakeWSClient.instances.append(self)

    def start(self) -> None:  # 阻塞（真 SDK 亦然），故宿主放 daemon 线程里跑
        self._conn = object()
        self.started.set()
        self.stopped.wait(timeout=10)

    def stop(self) -> None:
        self.stopped.set()


class _FakeEventHandler:
    def __init__(self) -> None:
        self.callback = None

    @staticmethod
    def builder(a, b):
        return _FakeEventHandler._B()

    class _B:
        def __init__(self) -> None:
            self._h = _FakeEventHandler()

        def register_p2_im_message_receive_v1(self, cb):
            self._h.callback = cb
            return self

        def build(self):
            return self._h


class _FakeClient:
    def __init__(self, **kw) -> None:
        self.kwargs = kw
        self.im = types.SimpleNamespace(v1=types.SimpleNamespace(message=_FakeMessageApi()))
        self.bot_payload: dict | None = {"bot": {"open_id": BOT_OPEN_ID, "app_name": "示例机器人"}}
        self.request_error: BaseException | None = None

    @staticmethod
    def builder():
        return _Builder(_FakeClient)

    def request(self, _request):
        if self.request_error is not None:
            raise self.request_error
        raw = types.SimpleNamespace(
            content=json.dumps(self.bot_payload or {}).encode("utf-8")
        )
        return types.SimpleNamespace(raw=raw)


@pytest.fixture
def fake_lark(monkeypatch):
    """把假 `lark_oapi` 装进 `sys.modules`。适配器的 SDK import 全在函数内，故无需 reload。"""
    _FakeWSClient.instances.clear()
    lark = types.ModuleType("lark_oapi")
    lark.FEISHU_DOMAIN = "https://open.feishu.cn"
    lark.LARK_DOMAIN = "https://open.larksuite.com"
    lark.Client = _FakeClient
    lark.EventDispatcherHandler = _FakeEventHandler
    lark.ws = types.SimpleNamespace(Client=_FakeWSClient)
    lark.HttpMethod = types.SimpleNamespace(GET="GET")
    lark.AccessTokenType = types.SimpleNamespace(TENANT="tenant")
    lark.BaseRequest = types.SimpleNamespace(builder=lambda: _Builder(dict))
    im_v1 = types.ModuleType("lark_oapi.api.im.v1")
    im_v1.CreateMessageRequest = _MessageRequest
    im_v1.CreateMessageRequestBody = _MessageBody
    im_v1.UpdateMessageRequest = _MessageRequest
    im_v1.UpdateMessageRequestBody = _MessageBody
    for name, mod in (
        ("lark_oapi", lark),
        ("lark_oapi.api", types.ModuleType("lark_oapi.api")),
        ("lark_oapi.api.im", types.ModuleType("lark_oapi.api.im")),
        ("lark_oapi.api.im.v1", im_v1),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setenv(ENV_APP_ID, "cli_x")
    monkeypatch.setenv(ENV_APP_SECRET, "secret_x")
    monkeypatch.delenv(ENV_DOMAIN, raising=False)
    monkeypatch.delenv(ENV_BOT_OPEN_ID, raising=False)
    return lark


@pytest.fixture(autouse=True)
def _im_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANLAN_IM_HOME", str(tmp_path / "imhome"))


def event(
    *,
    text: str = "你好",
    msg_type: str = "text",
    chat_type: str = "p2p",
    mentions: list | None = None,
    chat_id: str = "oc_1",
    msg_id: str = "om_1",
    user: str = "ou_user",
    tenant: str = "tk_1",
):
    message = types.SimpleNamespace(
        message_id=msg_id,
        chat_id=chat_id,
        chat_type=chat_type,
        message_type=msg_type,
        content=json.dumps({"text": text}, ensure_ascii=False),
        mentions=mentions or [],
    )
    sender = types.SimpleNamespace(sender_id=types.SimpleNamespace(open_id=user))
    return types.SimpleNamespace(
        header=types.SimpleNamespace(tenant_key=tenant),
        event=types.SimpleNamespace(message=message, sender=sender),
    )


def mention(open_id: str):
    return types.SimpleNamespace(id=types.SimpleNamespace(open_id=open_id))


async def started_adapter(tmp_path: Path, **kw) -> FeishuAdapter:
    ad = FeishuAdapter(tmp_path / "feishu", **kw)
    await ad.start()
    return ad


# ───────────────────────── 构造与 bot 身份 ─────────────────────────


def test_channel_ua_tag_is_actually_passed(fake_lark, tmp_path):
    """**②（决策P4.21-29）**：我们**真的传了** `extra_ua_tags=["channel"]`。

    防后人重构时丢掉——那会**静默失去群消息**，且表现为「群里怎么都不理我」。
    """

    async def scenario():
        ad = await started_adapter(tmp_path)
        await ad.close()

    asyncio.run(scenario())
    assert _FakeWSClient.instances, "没建 WS 客户端"
    kw = _FakeWSClient.instances[0].kwargs
    assert kw["extra_ua_tags"] == ["channel"]
    assert kw["domain"] == fake_lark.FEISHU_DOMAIN


def test_old_sdk_signature_is_refused(fake_lark, tmp_path, monkeypatch):
    """**探针的反向用例**：签名里没有 `extra_ua_tags` 的 SDK 必须**拒启**。

    上一条只证明「新 SDK 能过」；若探针哪天被改成恒返回（比如 `except` 放太宽），
    正例照绿、而一次 SDK 降级会**静默失去群消息**。漏报是沉默的，正例测不出来。
    """

    class _OldWSClient:
        def __init__(self, *, app_id, app_secret, domain, event_handler) -> None:
            pass  # 1.6.8 之前没有 extra_ua_tags

    monkeypatch.setattr(fake_lark, "ws", types.SimpleNamespace(Client=_OldWSClient))

    async def scenario():
        with pytest.raises(GuanlanError) as exc:
            await started_adapter(tmp_path)
        assert exc.value.exit_code == EXIT_USAGE
        assert MIN_SDK_VERSION in str(exc.value)

    asyncio.run(scenario())


def test_sdk_probe_runs_before_any_network_call(fake_lark, tmp_path, monkeypatch):
    """探针必须跑在**任何副作用之前**（§9.2「非法用法一律就地拒启、不建立任何连接」）。

    上一条只证明"最终会拒"，不管拒之前干了什么。探针是一次纯 `inspect.signature`——零成本、
    结论与凭据 / 网络无关，却曾排在 `_resolve_bot_identity()` 的网络往返**之后**：SDK 过旧
    的用户若同时填错了 `app_secret`，先撞上的是超时或认证失败，于是把排查方向带到凭据上，
    真正的原因（"群 @ 事件永远收不到"）一个字都不会出现。
    """
    calls: list = []
    original = _FakeClient.request
    monkeypatch.setattr(
        _FakeClient, "request", lambda self, req: (calls.append(req), original(self, req))[1]
    )

    class _OldWSClient:
        def __init__(self, *, app_id, app_secret, domain, event_handler) -> None:
            pass

    monkeypatch.setattr(fake_lark, "ws", types.SimpleNamespace(Client=_OldWSClient))

    async def scenario():
        with pytest.raises(GuanlanError):
            await started_adapter(tmp_path)

    asyncio.run(scenario())
    assert not calls, "SDK 版本探针之前就发了网络请求，真正的错因会被凭据类报错盖住"


# ───────────────────────── 首连看门狗（§8.1，决策P4.21-57 的够不到的那一半）─────────


def _ws_variant(behavior: str, *, has_conn: bool = True) -> type:
    """造一个可编排首连行为的假 WS **类**。

    `_FakeWSClient` 模拟的是"连上了"这一条正路；看门狗要守的全是**歪路**，故另起一套。

    必须返回**类**而不是可调用实例：`_require_channel_tag` 探的是 `ws_client_cls.__init__`
    的签名，实例的 `__init__` 是工厂自己的形参，会被那条探针当成旧版 SDK 一律拒掉。
    """

    class _WS:
        instances: list = []

        # 形参逐一具名，理由同 `_FakeWSClient.__init__`（`inspect.signature` 要看得见它）。
        def __init__(
            self, *, app_id, app_secret, domain, event_handler, extra_ua_tags=None
        ) -> None:
            if has_conn:
                self._conn = None
            self.stopped = threading.Event()
            _WS.instances.append(self)

        def start(self) -> None:
            if behavior == "reconnecting":
                # SDK 首连失败即走这条：钩子响一声，然后进 120 秒一轮的**无限**重连，
                # `start()` 永不返回——`_WS_DEAD` 哨兵因此永远投不出去。
                self.on_reconnecting()
                self.stopped.wait(timeout=10)
            elif behavior == "recovered":
                self.on_reconnecting()  # 首连失败……
                if has_conn:
                    self._conn = object()  # ……但这一次重试立刻成功了
                self.on_reconnected()
                self.stopped.wait(timeout=10)
            elif behavior == "thread-dies":
                raise RuntimeError("连不上，线程直接退")
            else:  # "hangs"：既不连上也不报错（DNS 黑洞 / 握手挂死）
                self.stopped.wait(timeout=10)

        def stop(self) -> None:
            self.stopped.set()

    return _WS


@pytest.fixture
def fast_watchdog(monkeypatch):
    """把看门狗窗口缩短，使超时分支在测试里是**毫秒**级而不是 30 秒。"""
    monkeypatch.setattr(fs, "WS_FIRST_CONNECT_TIMEOUT", 0.3)
    monkeypatch.setattr(fs, "_WS_POLL_INTERVAL", 0.01)


def _install_ws(fake_lark, monkeypatch, ws_cls: type) -> type:
    monkeypatch.setattr(fake_lark, "ws", types.SimpleNamespace(Client=ws_cls))
    return ws_cls


def test_first_connect_failure_refuses_to_start(fake_lark, tmp_path, monkeypatch, fast_watchdog):
    """首连失败 → **拒启**，不许以「假装启动成功」的姿态继续。

    这是 `_WS_DEAD` 哨兵**够不到**的一种活死人：SDK 把 `_connect()` 的异常吞进 `_reconnect()`，
    而首连成功前 `_reconnect_count == -1` ⇒ `while True` 每 120 秒重试、永不放弃 ⇒ 那个 daemon
    线程永远不返回 ⇒ `_ws_runner` 末尾的哨兵永远投不出去。表现是「启动成功、日志安静、
    一条消息都收不到」。真机上的 `SSL: CERTIFICATE_VERIFY_FAILED` 走的正是这条。
    """
    _install_ws(fake_lark, monkeypatch, _ws_variant("reconnecting"))

    async def scenario():
        with pytest.raises(GuanlanError) as exc:
            await started_adapter(tmp_path)
        assert exc.value.exit_code == EXIT_USAGE
        assert "重连" in str(exc.value)

    asyncio.run(scenario())


def test_first_connect_that_never_lands_refuses_at_deadline(
    fake_lark, tmp_path, monkeypatch, fast_watchdog
):
    """**既不连上也不报错**（DNS 黑洞 / 握手挂死）同样拒启——沉默不算通过。

    与上一条的区别：这里连 `on_reconnecting` 都不会响，唯一能用的证据是"到点了 `_conn` 还是
    `None`"。少了这条判据，最安静的那种故障恰好能溜过看门狗。
    """
    _install_ws(fake_lark, monkeypatch, _ws_variant("hangs"))

    async def scenario():
        with pytest.raises(GuanlanError) as exc:
            await started_adapter(tmp_path)
        assert exc.value.exit_code == EXIT_USAGE
        assert "没有连上" in str(exc.value)

    asyncio.run(scenario())


def test_ws_thread_dying_before_connect_refuses_to_start(
    fake_lark, tmp_path, monkeypatch, fast_watchdog
):
    """线程在连上之前就带着异常退出 → 立刻拒启，不等满窗口。"""
    _install_ws(fake_lark, monkeypatch, _ws_variant("thread-dies"))

    async def scenario():
        with pytest.raises(GuanlanError) as exc:
            await started_adapter(tmp_path)
        assert exc.value.exit_code == EXIT_USAGE
        assert "退出" in str(exc.value)

    asyncio.run(scenario())


def test_immediate_recovery_is_not_treated_as_failure(
    fake_lark, tmp_path, monkeypatch, fast_watchdog
):
    """**反向用例**：首连失败但当场重连成功 → 必须放行。

    判据是「失败了**且没恢复**」，不是「失败过」。写成后者的话，一次抖动就让宿主起不来，
    而它明明已经连上了——看门狗的误杀比它要防的病还常见。
    """
    _install_ws(fake_lark, monkeypatch, _ws_variant("recovered"))

    async def scenario():
        ad = await started_adapter(tmp_path)
        await ad.close()

    asyncio.run(scenario())


def test_unobservable_sdk_degrades_to_a_warning_not_a_refusal(
    fake_lark, tmp_path, monkeypatch, fast_watchdog, caplog
):
    """SDK 换了形状、`_conn` 不复存在 → **告警放行**，绝不拒启。

    观测不到 ≠ 坏了。这里若一律拒启，一次 SDK 升级就能让所有人起不了服——**看门狗自己
    成了故障源**。留一条 WARNING：日后真出「机器人不理我」时，它是唯一能说明"当时看门狗
    是瞎的"的证据。真 SDK 的形状另有探针用例守着，那条变红比运行期悄悄降级早得多。
    """
    _install_ws(fake_lark, monkeypatch, _ws_variant("hangs", has_conn=False))

    async def scenario():
        with caplog.at_level("WARNING", logger="guanlan.im"):
            ad = await started_adapter(tmp_path)
        await ad.close()

    asyncio.run(scenario())
    assert any("未生效" in r.getMessage() for r in caplog.records), "降级时没留下任何痕迹"


def test_ws_client_exposes_the_watchdog_hooks():
    """**真 SDK 探针**（同 `extra_ua_tags` 那条的分层）：看门狗依赖的三个形状还在吗。

    三者任一变了，看门狗都会**静默降级**成"看不见"——而它的全部价值就在于不让人静默。
    故宁可在这里红，也不要在某天线上变成"机器人不理我，日志一片安静"。
    """
    lark = pytest.importorskip("lark_oapi", reason="未装 [im-feishu] extra")

    client = lark.ws.Client(app_id="probe", app_secret="probe")
    assert hasattr(client, "_conn"), "SDK 不再暴露 `_conn`：看门狗失去唯一的成功证据"
    # 首连成功之前 `_reconnect_count` 是 -1 ＝ `while True` 无限重连——**这正是本看门狗
    # 存在的原因**。哪天上游改成有限次，`start()` 会自己抛出、`_WS_DEAD` 就够得着了。
    assert client._reconnect_count < 0, "上游已改成有限次重连，看门狗的前提变了，请复核"
    source = inspect.getsource(type(client)._reconnect)
    assert "self.on_reconnecting()" in source, "SDK 不再调用 `on_reconnecting` 钩子"


def test_domain_env_selects_lark(fake_lark, tmp_path, monkeypatch):
    """`GUANLAN_IM_FEISHU_DOMAIN=lark` → 国际站域名（两套域名在构造期就要选定）。"""
    monkeypatch.setenv(ENV_DOMAIN, "lark")

    async def scenario():
        ad = await started_adapter(tmp_path)
        await ad.close()

    asyncio.run(scenario())
    assert _FakeWSClient.instances[0].kwargs["domain"] == fake_lark.LARK_DOMAIN


def test_invalid_domain_is_rejected_with_legal_values(fake_lark, tmp_path, monkeypatch):
    """域名取值非法 → `EXIT_USAGE` 并**列出合法值**。

    替代「用默认值连错域名后报认证失败」这种误导性故障（§9.2）。
    """
    monkeypatch.setenv(ENV_DOMAIN, "lark-cn")

    async def scenario():
        with pytest.raises(GuanlanError) as exc:
            await started_adapter(tmp_path)
        assert exc.value.exit_code == EXIT_USAGE
        assert "feishu" in str(exc.value) and "lark" in str(exc.value)

    asyncio.run(scenario())


def test_credentials_can_come_from_dotenv(fake_lark, tmp_path, monkeypatch):
    """**`.env` 里的飞书凭据要读得到**（决策P4.21-78）。

    进程里确实有人加载 `.env`——但那是 `build_from_environment`，它要等**第一次建会话**才跑，
    已在 `adapter.start()` **之后**。不显式加载的话，同一个 `.env` 文件会出现
    「模型 API key 读得到、飞书 App ID/Secret 读不到」这种毫无道理的不一致。
    """
    monkeypatch.delenv(ENV_APP_ID, raising=False)
    monkeypatch.delenv(ENV_APP_SECRET, raising=False)
    (tmp_path / ".env").write_text(
        f"{ENV_APP_ID}=cli_from_dotenv\n{ENV_APP_SECRET}=secret_from_dotenv\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)  # find_dotenv 从 cwd 逐级上溯

    im_server.load_dotenv_for_im()

    async def scenario():
        ad = await started_adapter(tmp_path)
        got = ad._app_id
        await ad.close()
        return got

    assert asyncio.run(scenario()) == "cli_from_dotenv"


def test_real_env_wins_over_dotenv(fake_lark, tmp_path, monkeypatch):
    """**真环境变量压过 `.env`**（no-override）——与 agentao 读模型 key 的优先级逐字节一致。

    这条方向弄反的后果很难查：`.env` 里一份过期的凭据会**静默盖掉**你刚 `export` 的那份，
    表现为「我明明改了却还是连不上」。
    """
    (tmp_path / ".env").write_text(f"{ENV_APP_ID}=cli_from_dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENV_APP_ID, "cli_from_real_env")

    im_server.load_dotenv_for_im()

    assert os.environ[ENV_APP_ID] == "cli_from_real_env"


def test_dotenv_load_is_idempotent(tmp_path, monkeypatch):
    """`setdefault` 语义 ⇒ 反复加载**幂等**，与随后 `build_from_environment` 自己那次不冲突。"""
    (tmp_path / ".env").write_text(f"{ENV_APP_ID}=cli_once\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_APP_ID, raising=False)
    im_server.load_dotenv_for_im()
    im_server.load_dotenv_for_im()
    assert os.environ[ENV_APP_ID] == "cli_once"


def test_missing_credentials_is_usage_error(fake_lark, tmp_path, monkeypatch):
    """凭据 env-only（决策P4.21-6）：缺失 → `EXIT_USAGE`；**绝不**提供明文命令行参数。"""
    monkeypatch.delenv(ENV_APP_SECRET, raising=False)

    async def scenario():
        with pytest.raises(GuanlanError) as exc:
            await started_adapter(tmp_path)
        assert exc.value.exit_code == EXIT_USAGE and ENV_APP_SECRET in str(exc.value)

    asyncio.run(scenario())


def test_bot_identity_is_probed_not_taken_from_env(fake_lark, tmp_path, monkeypatch, caplog):
    """**总是探测**（§8.2）：env 与探测值不一致时**用探测值并 WARNING**。

    原设计要求必填 `GUANLAN_IM_FEISHU_BOT_OPEN_ID`——多余的：`/open-apis/bot/v3/info` 用
    tenant access token 即可、**无需额外权限**。且**不因 env 已设而跳过**——陈旧的 env 值会
    **静默破坏群 @ 判定**。
    """
    import logging

    caplog.set_level(logging.WARNING, logger="guanlan.im")
    monkeypatch.setenv(ENV_BOT_OPEN_ID, "ou_stale")

    async def scenario():
        ad = await started_adapter(tmp_path)
        got = ad._bot_open_id
        await ad.close()
        return got

    assert asyncio.run(scenario()) == BOT_OPEN_ID
    assert any(ENV_BOT_OPEN_ID in r.getMessage() for r in caplog.records)


def test_bot_identity_falls_back_to_env(fake_lark, tmp_path, monkeypatch):
    """探测失败 → 回退 env（env 仅作回退，不是首选）。"""
    monkeypatch.setenv(ENV_BOT_OPEN_ID, "ou_env")
    original = _FakeClient.request

    def boom(self, _req):
        raise RuntimeError("网络不通")

    monkeypatch.setattr(_FakeClient, "request", boom)

    async def scenario():
        ad = await started_adapter(tmp_path)
        got = ad._bot_open_id
        await ad.close()
        return got

    assert asyncio.run(scenario()) == "ou_env"
    monkeypatch.setattr(_FakeClient, "request", original)


def test_bot_identity_failure_is_fatal_when_group_wanted(fake_lark, tmp_path, monkeypatch):
    """探测失败 + 无 env + **要收群消息** → 拒启并明示。

    否则群内 @ 判定永远 False、表现为「群里怎么都不理我」——这类故障几乎无法自行排查。
    """

    def boom(self, _req):
        raise RuntimeError("网络不通")

    monkeypatch.setattr(_FakeClient, "request", boom)

    async def scenario():
        with pytest.raises(GuanlanError) as exc:
            await started_adapter(tmp_path, group_wanted=True)
        assert exc.value.exit_code == EXIT_USAGE and ENV_BOT_OPEN_ID in str(exc.value)
        # 只做单聊时不致命（DM 用不到 bot open_id）
        ad = await started_adapter(tmp_path, group_wanted=False)
        await ad.close()

    asyncio.run(scenario())


# ───────────────────────── 入站映射与线程桥 ─────────────────────────


def test_event_mapping(fake_lark):
    """事件 → `InboundMessage` 映射（§8.3）；`chat_type == "p2p"` → `"dm"`。"""
    m = map_event(event(), bot_open_id=BOT_OPEN_ID)
    assert m is not None
    assert (m.tenant, m.chat_id, m.user_id, m.msg_id) == ("tk_1", "oc_1", "ou_user", "om_1")
    assert m.chat_type == CHAT_DM and m.text == "你好"
    assert m.msg_kind == KIND_TEXT and m.has_attachments is False
    g = map_event(event(chat_type="group"), bot_open_id=BOT_OPEN_ID)
    assert g is not None and g.chat_type == CHAT_GROUP


def test_non_text_message_is_other(fake_lark):
    """非文本 → `msg_kind="other"` + `has_attachments=True`（提示归核心，§5.1）。"""
    m = map_event(event(msg_type="image"), bot_open_id=BOT_OPEN_ID)
    assert m is not None and m.msg_kind == KIND_OTHER and m.has_attachments is True


def test_mentioned_me_uses_structured_field(fake_lark):
    """**`mentioned_me` 取结构化字段，绝不靠文本猜**（§5.1 闸③）。

    正文里写「@机器人」四个字**不算** @——否则任何人复述一句就能触发群内问答。
    """
    fake = map_event(
        event(text="@机器人 你好", chat_type="group", mentions=[]), bot_open_id=BOT_OPEN_ID
    )
    assert fake is not None and fake.mentioned_me is False
    real = map_event(
        event(text="你好", chat_type="group", mentions=[mention(BOT_OPEN_ID)]),
        bot_open_id=BOT_OPEN_ID,
    )
    assert real is not None and real.mentioned_me is True
    other = map_event(
        event(chat_type="group", mentions=[mention("ou_someone")]), bot_open_id=BOT_OPEN_ID
    )
    assert other is not None and other.mentioned_me is False


def test_sdk_thread_callback_reaches_inbound(fake_lark, tmp_path):
    """线程桥：**SDK 线程**回调的消息能被 `inbound()` 正确取到（真线程 + `call_soon_threadsafe`）。

    绝不在 SDK 线程里直接碰事件循环对象——那会丢消息 / 卡流 / 偶发崩。
    """

    async def scenario():
        ad = await started_adapter(tmp_path)
        handler = _FakeWSClient.instances[0].kwargs["event_handler"]
        got: list = []

        async def consume():
            async for m in ad.inbound():
                got.append(m)

        task = asyncio.create_task(consume())
        thread = threading.Thread(target=lambda: handler.callback(event(text="来自 SDK 线程")))
        thread.start()
        for _ in range(400):
            await asyncio.sleep(0.005)
            if got:
                break
        thread.join(timeout=5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await ad.close()
        assert got and got[0].text == "来自 SDK 线程"
        assert threading.current_thread() is threading.main_thread()

    asyncio.run(scenario())


# ───────────────────────── 出站：围栏切行与降级 ─────────────────────────


def test_fence_rows_are_split():
    """**围栏切行是 §8.4 的唯一真实逻辑**：飞书的 `md` 渲染器在一个大元素里含围栏块时
    **会吞掉围栏之后的内容**。故围栏块独占一 row，前后散文各成一 row。

    观澜的答案经常带围栏块（代码 / mermaid / flint），**这个坑必踩**。
    """
    rows = build_markdown_post_rows("前言\n```flint\n{\"a\":1}\n```\n后记")
    assert len(rows) == 3
    assert rows[0][0]["text"] == "前言"
    assert rows[1][0]["text"].startswith("```flint") and rows[1][0]["text"].endswith("```")
    assert rows[2][0]["text"] == "后记"
    assert len(build_markdown_post_rows("只有散文")) == 1


def test_unclosed_fence_is_still_sent():
    """未闭合的围栏也**原样送出**——降级契约是保留源码，绝不静默丢弃（§6.5）。"""
    rows = build_markdown_post_rows("前言\n```python\nprint(1)")
    assert "```python" in json.dumps(rows, ensure_ascii=False)


def test_post_content_shape():
    """`post` 富文本信封形状：`{"zh_cn": {"content": [[{tag: md, text: …}]]}}`。"""
    payload = json.loads(post_content("正文"))
    assert payload["zh_cn"]["content"][0][0] == {"tag": "md", "text": "正文"}


def test_send_falls_back_to_text_when_post_rejected(fake_lark, tmp_path, caplog):
    """`post` 被拒 → 回退纯文本（**send 侧**）。参考实现在两侧各写了一遍，说明这是实际会发生的。"""
    import logging

    caplog.set_level(logging.WARNING, logger="guanlan.im")

    async def scenario():
        ad = await started_adapter(tmp_path)
        ad._client.im.v1.message.reject_post = True
        ref = await ad.send("oc_1", "正文")
        await ad.close()
        return ad._client.im.v1.message.creates if ad._client else [], ref

    # `close()` 会把 _client 置 None，故在 scenario 内取
    async def scenario2():
        ad = await started_adapter(tmp_path)
        api = ad._client.im.v1.message
        api.reject_post = True
        ref = await ad.send("oc_1", "正文")
        creates = list(api.creates)
        await ad.close()
        return creates, ref

    creates, ref = asyncio.run(scenario2())
    assert [c.body.msg_type for c in creates] == ["post", "text"]
    assert json.loads(creates[1].body.content) == {"text": "正文"}
    assert ref.message_id.startswith("om_")
    assert any("回退纯文本" in r.getMessage() for r in caplog.records)
    del scenario


def test_edit_falls_back_to_text_when_post_rejected(fake_lark, tmp_path):
    """`post` 被拒 → 回退纯文本（**edit 侧**，与 send 各一例）。"""

    async def scenario():
        ad = await started_adapter(tmp_path)
        api = ad._client.im.v1.message
        api.reject_post = True
        await ad.edit(__import__("guanlan.im.contract", fromlist=["OutboundRef"]).OutboundRef("oc_1", "om_9"), "改写")
        updates = list(api.updates)
        await ad.close()
        return updates

    updates = asyncio.run(scenario())
    assert [u.body.msg_type for u in updates] == ["post", "text"]
    assert all(u.message_id == "om_9" for u in updates)


def test_caps_are_feishu_shaped():
    """§8.6：能编辑、不 typing、能群聊、8000×3。"""
    assert CAPS.supports_edit is True and CAPS.supports_typing is False
    assert CAPS.supports_group is True
    assert (CAPS.max_message_length, CAPS.max_parts) == (8000, 3)


def test_typing_is_not_supported(fake_lark, tmp_path):
    """`supports_typing=False` → 核心永不调 `typing`；真调到必须炸得明白。"""

    async def scenario():
        ad = FeishuAdapter(tmp_path / "feishu")
        with pytest.raises(NotImplementedError):
            await ad.typing("oc_1", True)

    asyncio.run(scenario())


# ───────────────────────── 单实例锁（§8.5）─────────────────────────


def test_lock_key_is_platform_directory_level():
    """锁键是 `~/.guanlan/im/feishu/credential.lock`（**平台目录级**，与 `app_id` 无关）。

    原设计的租户级键**实现不了**：微信首次 `im-login` 时 `account_id` 要扫码成功才拿得到，
    两个并发首登进程无法预先互斥、会互相覆盖 `account.json`（决策P4.21-42）。
    """
    lock = CredentialLock("feishu", command="guanlan im")
    assert lock.path.name == "credential.lock"
    assert lock.path.parent.name == "feishu"


def test_second_instance_is_refused_across_subcommands():
    """**跨子命令互斥**：`im` 在跑时 `im-identify` 拒启，反之亦然；报错含 owner pid **与子命令名**。"""
    first = CredentialLock("feishu", command="guanlan im")
    first.acquire()
    try:
        with pytest.raises(GuanlanError) as exc:
            CredentialLock("feishu", command="guanlan im-identify").acquire()
        assert "guanlan im" in str(exc.value) and str(os.getpid()) in str(exc.value)
    finally:
        first.release()
    second = CredentialLock("feishu", command="guanlan im-identify")
    second.acquire()
    try:
        with pytest.raises(GuanlanError):
            CredentialLock("feishu", command="guanlan im").acquire()
    finally:
        second.release()


def test_stale_lock_is_reclaimed():
    """陈锁（pid 已死）被自动清理。"""
    from guanlan.im.state import write_json_atomic

    lock = CredentialLock("feishu", command="guanlan im")
    write_json_atomic(lock.path, {"pid": 999_999_997, "command": "guanlan im"})
    lock.acquire()
    lock.release()
