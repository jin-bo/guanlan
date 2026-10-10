"""Agentao 接缝契约测试（P4.23 §5 第 4 步，见 docs/P4.23-Agentao0.5.3接入.md）。

**本文件与其它 Web 用例的根本区别：这里的 agent 是真的。** `tests/test_web.py` 把
`build_from_environment` 猴补成 `_FakeAgent`——那对验宿主逻辑很好用，但它**验不出上游签名
变了**：替身永远符合我们记忆中的旧签名。0.5.0 那批破坏性改动（删 `agentao.harness` /
`agentao.session`、删旧 callback 构造参数、构造器第五参后强制关键字、sessions API 强制
`project_root`）在一个全替身的测试库里可以一条都测不出来。

所以这里走**真实工厂**：真 `build_from_environment` → 真 `Agentao`，只把 LLM 端点指向一个
不存在的本地地址。构造期不发任何请求，故离线、无网可跑；要跑一轮时才猴补 `agent.chat`
（= `arun` 包的那层，也是 agentao 自己的测试用的接缝）。

覆盖面按调研 §5 第 4 步列举：构造与关闭、注入面（filesystem / transport / extra_tools /
bg_store）、只读与可写双姿态、取消、持久化恢复、以及 §3.2 的推理数据清理与 §2.1 的子 Agent
工具继承。
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from guanlan.init import run_init
from guanlan.search import CorpusCache


@pytest.fixture
def real_kb(tmp_path: Path, monkeypatch) -> Path:
    """一个真知识库 + 一份**指向死地址**的 LLM 配置。

    构造 `LLMClient` 不发请求，故 base_url 无需可达；用 127.0.0.1:1 而非真地址，是为了万一
    将来哪条路径真去连，它会立刻 ECONNREFUSED 而不是把用例变成一次真实 API 调用。
    """
    kb = tmp_path / "kb"
    run_init(kb)
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-contract-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("OPENAI_MODEL", "contract-test-model")
    # `.env` 若存在会被 `safe_load_dotenv` 读进来盖掉上面几项（dev 机器上常有）——用例必须
    # 只认自己设的环境，否则本地绿、CI 红（或更糟：本地真去连某个网关）。
    monkeypatch.setattr("guanlan.web.chat.ensure_skill_available", lambda _kb: None)
    return kb


@pytest.fixture
def real_conv(real_kb: Path):
    """经**观澜自己的构造路径**建一个真 Conversation（不猴补 factory）。"""
    from guanlan.web.conversation import Conversation

    conv = Conversation(
        "contract-cid", real_kb, None, persist=False, search_cache=CorpusCache()
    )
    try:
        yield conv
    finally:
        conv.close()


# ── 构造与关闭 ────────────────────────────────────────────────────────────────


def test_real_factory_builds_a_real_agent(real_conv) -> None:
    """真 `build_from_environment` 能在观澜的参数下构造出真 `Agentao`，且能干净关闭。"""
    agent = real_conv.agent
    assert type(agent).__module__.startswith("agentao.")
    assert type(agent).__name__ == "Agentao"
    assert agent.get_current_model() == "contract-test-model"


def test_injected_seams_reach_the_agent(real_conv) -> None:
    """构造期注入的三样东西都真的落到了 agent 上（层① / token 流 / 召回工具）。"""
    agent = real_conv.agent
    # 层①：PolicyFileSystem 必须是 agent 实际在用的那一个，否则 immutable 判定形同虚设。
    assert type(agent.filesystem).__name__ == "PolicyFileSystem"
    assert agent.filesystem is real_conv._policy_fs
    # P5.1：召回工具进了真注册表（`extra_tools` 这条路还在）。
    assert agent.tools.get("guanlan_search") is not None


def test_last_turn_surface_exists_and_starts_empty(real_conv) -> None:
    """P4.23 §3.1 依赖的公开面：`agent.last_turn`，未跑轮时为 None。"""
    agent = real_conv.agent
    assert hasattr(agent, "last_turn")
    assert agent.last_turn is None


def test_turn_outcome_fields_we_rely_on_still_exist() -> None:
    """`TurnOutcome` 上我们读的每个字段都还在，且 `is_answer` 仍是那条公开公式。

    宿主自己算 `is_answer` 而不读属性（防 Mock），故这里**反过来**验两者一致：一旦上游改了
    公式而我们没跟，这条会红。
    """
    from agentao.runtime.outcome import TurnOutcome

    ok = TurnOutcome(text="答", status="ok", incomplete_reason=None, tool_count=0)
    bad = TurnOutcome(
        text="", status="error", incomplete_reason="llm_error", tool_count=0, error="x"
    )
    assert ok.is_answer is True and bad.is_answer is False
    for field in ("text", "status", "incomplete_reason", "tool_count", "error"):
        assert hasattr(ok, field)
    # 0.5.x 的诊断位：存在、默认 False、且**不**影响 is_answer（上游明确的口径）。
    missing = TurnOutcome(
        text="答", status="ok", incomplete_reason=None, tool_count=0,
        finish_reason_missing=True,
    )
    assert missing.finish_reason_missing is True and missing.is_answer is True


# ── 姿态（只读 / 可写）───────────────────────────────────────────────────────


def test_starts_in_constructed_posture(real_conv) -> None:
    """开局姿态经 `build_from_environment(permission_mode=)` 交给 factory（0.5.12 起）——
    构造完两点都已落位，观澜不再补翻。少一点就是「看着只读、其实能写」。"""
    agent = real_conv.agent
    assert agent.permission_engine.active_mode.value == "read-only"
    assert agent.tool_runner.readonly_mode is True


def test_both_posture_points_still_flip(real_conv) -> None:
    """姿态是**两点**设置（engine 预设 + `tool_runner.readonly_mode`）——少一点就没真切换。
    运行期经 `set_permission_mode` 一次翻齐，并发 PERMISSION_MODE_CHANGED。"""
    from agentao.transport import EventType

    agent = real_conv.agent
    seen: list = []
    unsubscribe = agent.transport.subscribe(seen.append)
    try:
        real_conv._apply_mode("workspace-write")
        assert agent.permission_engine.active_mode.value == "workspace-write"
        assert agent.tool_runner.readonly_mode is False
        real_conv._apply_mode("read-only")
        assert agent.permission_engine.active_mode.value == "read-only"
        assert agent.tool_runner.readonly_mode is True
    finally:
        unsubscribe()
    changes = [e.data for e in seen if e.type == EventType.PERMISSION_MODE_CHANGED]
    assert [(c["previous"], c["current"]) for c in changes] == [
        ("read-only", "workspace-write"), ("workspace-write", "read-only"),
    ]


def test_compaction_events_reach_host_listener(real_conv) -> None:
    """Web 的压缩进度帧靠 `transport.subscribe` 旁听：真 agent 的 `agent.transport` 必须就是
    观澜构造的那只（没被换、没被包成不转发的东西），且事件名与载荷键仍是我们读的那几个。"""
    from agentao.transport import AgentEvent, EventType

    got: list = []
    real_conv._emit = lambda kind, data: got.append((kind, data))
    try:
        real_conv.agent.transport.emit(AgentEvent(EventType.COMPACTION_STARTED, {
            "trigger": "auto", "kind": "full", "reason": "compression_threshold",
        }))
        real_conv.agent.transport.emit(AgentEvent(EventType.COMPACTION_SETTLED, {
            "kind": "full", "status": "failed", "pre_msgs": 3, "post_msgs": 3, "duration_ms": 10,
        }))
        # 毫秒级的 microcompact 不发 started，也不上线结果行（只报 full）。
        real_conv.agent.transport.emit(AgentEvent(EventType.COMPACTION_SETTLED, {
            "kind": "microcompact", "status": "success", "pre_msgs": 3, "post_msgs": 3, "duration_ms": 1,
        }))
    finally:
        real_conv._emit = None
    assert got == [
        ("compaction", {"phase": "started"}),
        ("compaction", {"phase": "settled", "status": "failed", "pre_msgs": 3, "post_msgs": 3, "duration_ms": 10}),
    ]


def test_unknown_mode_is_rejected(real_conv) -> None:
    """Web **只**认 read-only / workspace-write：full-access / plan 也在 agentao 的枚举里，
    但绝不许出现在 Web（决策P4.5-1），故拦在观澜这一层、转 422。"""
    from agentao.permissions import PermissionMode

    assert {m.value for m in PermissionMode} >= {"read-only", "workspace-write"}
    with pytest.raises(ValueError):
        real_conv._apply_mode("full-access")


def test_static_fallback_agrees_with_real_is_read_only(real_conv) -> None:
    """`/tools` 的静态兜底表（无 `is_read_only` 元数据时才用）必须与真工具元数据同口径。

    0.5.4 起上游只读放行 `todo_write` / `activate_skill`，而兜底表曾把 `todo_write` 列为写工具——
    真 agent 下兜底不触发、测不出漂移，故逐个拿真工具核对。
    """
    from guanlan.web.chat_support import _READ_TOOL_NAMES, _WRITE_TOOL_NAMES

    for tool in real_conv.agent.tools.list_tools():
        if tool.name in _WRITE_TOOL_NAMES:
            assert tool.is_read_only is False, tool.name
        elif tool.name in _READ_TOOL_NAMES:
            assert tool.is_read_only is True, tool.name


# ── 工具注册面（goal 循环依赖它）──────────────────────────────────────────────


def test_add_tool_replace_and_remove_tool(real_conv) -> None:
    """P4.16 的 goal 循环靠 `add_tool(..., replace=True)` / `remove_tool` 做 loop 作用域注入。"""
    from agentao.cli.goal_state import GoalState
    from agentao.tools.goal import UpdateGoalTool

    agent = real_conv.agent
    goal = GoalState(objective="x")
    agent.add_tool(UpdateGoalTool(goal, lambda: None), replace=True)
    assert agent.tools.get("update_goal") is not None
    agent.add_tool(UpdateGoalTool(goal, lambda: None), replace=True)  # 幂等替换，不得抛
    assert agent.remove_tool("update_goal") is True
    assert agent.remove_tool("update_goal") is False


def test_goal_primitives_still_importable() -> None:
    """P4.16 复用的 agentao goal 原语（`GoalState` / `parse_duration` / 状态枚举）仍在原处。"""
    from agentao.cli.duration import parse_duration
    from agentao.cli.goal_state import GoalState, GoalStatus, budget_summary

    g = GoalState(objective="x", max_turns=2)
    assert g.is_active and g.status is GoalStatus.ACTIVE
    g.mark_blocked()
    assert not g.is_active
    assert parse_duration("90s") == 90
    assert isinstance(budget_summary(g), str)


# ── 取消 ─────────────────────────────────────────────────────────────────────


def test_arun_forwards_cancellation_token_and_images(real_conv, monkeypatch) -> None:
    """`arun` 仍是 `chat` 的 async 包装，且把取消令牌与 images 原样转下去。

    猴补的是 `chat`（agentao 自己的测试用的同一接缝），`arun` 本身是真的——这样验的是
    "包装层的签名与转发语义"，而不是我们自己写的假货。
    """
    import asyncio

    seen: dict = {}

    def fake_chat(user_message, max_iterations=100, cancellation_token=None, images=None):
        # 位置参数照抄真 `chat` 的签名：`arun` 是按位置转发的，写成 `**kw` 会假绿。
        seen.update(
            msg=user_message,
            max_iterations=max_iterations,
            cancellation_token=cancellation_token,
            images=images,
        )
        return "答案"

    monkeypatch.setattr(real_conv.agent, "chat", fake_chat)
    from agentao.cancellation import CancellationToken

    token = CancellationToken()
    out = asyncio.run(
        real_conv.agent.arun("问", cancellation_token=token, images=[{"data": "x"}])
    )
    assert out == "答案"
    assert seen["msg"] == "问"
    assert seen["cancellation_token"] is token
    assert seen["images"] == [{"data": "x"}]
    # 纯文本轮必须传 None（观澜 `turn()` 传的是 `images or None`，与 chat() 契约一致）。
    seen.clear()
    asyncio.run(real_conv.agent.arun("问", cancellation_token=token, images=None))
    assert seen["images"] is None


def test_cancelled_error_type_is_where_we_catch_it() -> None:
    """Web/IM 到处 `except AgentCancelledError`——它必须还在 `agentao.cancellation`。"""
    from agentao.cancellation import AgentCancelledError, CancellationToken

    token = CancellationToken()
    assert token.is_cancelled is False
    token.cancel("user-stop")
    assert token.is_cancelled is True and token.reason == "user-stop"
    assert issubclass(AgentCancelledError, Exception)


def test_stopped_turn_surfaces_as_agent_cancelled_error(real_conv, monkeypatch) -> None:
    """**真** `arun` 在停止时并不抛：`run_turn` 把 `AgentCancelledError` 吞成 `[Cancelled: …]`
    + `last_turn.status == "cancelled"` 返回。宿主的停止路径（Web stopped 帧、IM 停机静默、goal
    暂停）全按"抛 AgentCancelledError"写成，故 `turn()` 必须把它还原回来，且**不**记成 incomplete。

    猴补的是 `_chat_inner`（`run_turn` 包的那层），这样 `run_turn` 的异常映射与 `last_turn` 落值
    都是真的——替身 agent 天然"会抛"，正是它把这个缺口藏了起来。
    """
    import asyncio

    from agentao.cancellation import AgentCancelledError

    def inner(msg, max_iterations, token, images=None):
        token.cancel("user-stop")
        raise AgentCancelledError("user-stop")

    monkeypatch.setattr(real_conv.agent, "_chat_inner", inner)
    meta: dict = {}
    with pytest.raises(AgentCancelledError):
        asyncio.run(real_conv.turn("问", lambda _k, _d: None, meta))
    assert real_conv.agent.last_turn.status == "cancelled"  # 上游确实是"返回"而非"抛"
    assert "incomplete" not in meta  # 停止不是"没答出来"


# ── 持久化与恢复 ──────────────────────────────────────────────────────────────


def test_session_roundtrip_requires_project_root(real_kb: Path, real_conv) -> None:
    """0.5.x 的 sessions API **强制** `project_root`；存→列→读→删一圈都走真实现。"""
    from agentao.embedding import (
        delete_session,
        list_sessions,
        load_session,
        save_session,
    )

    messages = [
        {"role": "user", "content": "问"},
        {"role": "assistant", "content": "答"},
    ]
    save_session(
        messages, "contract-test-model", session_id="sess-1", project_root=real_kb
    )
    listed = list_sessions(real_kb)
    assert any(s.get("session_id") == "sess-1" for s in listed)
    loaded, model, _skills = load_session("sess-1", project_root=real_kb)
    assert [m["content"] for m in loaded] == ["问", "答"]
    assert model == "contract-test-model"
    # `project_root` 是关键字**必填**（0.5.x 强制）：漏了要立刻炸，不能悄悄落到 cwd 上。
    with pytest.raises(TypeError):
        load_session("sess-1")  # type: ignore[call-arg]
    assert delete_session("sess-1", real_kb) is True


def test_purge_clears_every_wire_carrier(real_kb: Path) -> None:
    """P4.23 §3.2 契约：清理必须覆盖 `WIRE_CARRIER_KEYS` 的**每一项**。

    这条是**防漏报**用的（漏清理是沉默的：正向用例全绿，只有真换模型时才炸）。上游新增一种
    wire 只需往那个元组加一项——若哪天我们改成自己抄字段清单，这条会立刻红。
    """
    from agentao.llm._stream_response import WIRE_CARRIER_KEYS

    from guanlan.web.chat_support import purge_model_specific

    assert WIRE_CARRIER_KEYS, "上游把载体键元组清空了？先看它改了什么"
    msg = {
        "role": "assistant",
        "content": "正文要留下",
        "reasoning_content": "该清",
        **{k: ["该清"] for k in WIRE_CARRIER_KEYS},
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "thought_signature": "该清",
                "function": {"name": "f", "arguments": "{}", "thought_signature": "该清"},
            }
        ],
    }
    removed = purge_model_specific([msg])
    # reasoning_content + 每个载体键 + 两级 thought_signature
    assert removed == 1 + len(WIRE_CARRIER_KEYS) + 2
    assert "reasoning_content" not in msg
    for key in WIRE_CARRIER_KEYS:
        assert key not in msg, f"载体键 {key} 没被清掉"
    assert "thought_signature" not in msg["tool_calls"][0]
    assert "thought_signature" not in msg["tool_calls"][0]["function"]
    # 普通正文与工具调用配对必须原样保留——清过头比不清更难查。
    assert msg["content"] == "正文要留下"
    assert msg["tool_calls"][0]["id"] == "call-1"
    assert msg["tool_calls"][0]["function"]["name"] == "f"


def test_restore_purges_before_handing_history_to_the_agent(real_kb: Path, monkeypatch) -> None:
    """恢复路径真的清了：盘上带推理数据的历史，恢复出来的 `agent.messages` 是干净的。

    观澜恢复**有意不回放盘上的 model**、一律绑当前进程模型，所以"换模型恢复"是常态——这正是
    必须清的原因（见 `purge_model_specific` 的 docstring）。
    """
    from agentao.embedding import save_session
    from agentao.llm._stream_response import WIRE_CARRIER_KEYS

    from guanlan.skill import SKILL_NAME
    from guanlan.web.conversation_store import ConversationStore

    dirty = [
        {"role": "user", "content": "问"},
        {
            "role": "assistant",
            "content": "答",
            "reasoning_content": "旧模型的推理",
            **{k: ["旧模型铸的"] for k in WIRE_CARRIER_KEYS},
        },
    ]
    cid = "11111111-2222-4333-8444-555555555555"  # 须是规范 uuid，否则 store 不认
    # `active_skills` 必须带 SKILL_NAME：restore 的闸②只认属于 Web 只读会话的条目
    # （决策P4.2-6/7），否则这份快照会被当成 agentao CLI 落的、直接 404。
    save_session(
        dirty, "别的模型", [SKILL_NAME], session_id=cid, project_root=real_kb
    )

    store = ConversationStore(real_kb, None, search_cache=CorpusCache())
    conv = None
    try:
        conv = store.restore(cid)  # `get` 只看内存；冷会话走 restore（就是清理发生的那条路）
        assert conv is not None, "会话没被恢复出来"
        assistant = [m for m in conv.agent.messages if m.get("role") == "assistant"][0]
        assert assistant["content"] == "答"  # 正文没被清过头
        assert "reasoning_content" not in assistant
        for key in WIRE_CARRIER_KEYS:
            assert key not in assistant, f"恢复后仍残留 {key}：换模型时会被 provider 拒掉"
    finally:
        if conv is not None:
            conv.close()


# ── 子 Agent（§2.1 / §3.4）────────────────────────────────────────────────────
#
# 这三条**必须用带 `.agentao/agents/*.md` 的知识库**跑：标准库里 `guanlan init` 不生成它，
# 没有 agent 定义就不会建 AgentToolWrapper，两条路径全都跑不到——绿了也说明不了任何事。


@pytest.fixture
def kb_with_agent(real_kb: Path) -> Path:
    """在真知识库里放一份项目级 agent 定义。"""
    agents = real_kb / ".agentao" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: 复核页面\n---\n你是复核者。\n", encoding="utf-8"
    )
    return real_kb


def test_bg_store_none_disables_background_but_not_the_subagent(kb_with_agent: Path) -> None:
    """`bg_store=None` **只关后台面**，前台子 Agent 照常注册（P4.23 §3.4 的核心事实）。

    这条用例存在的理由是一次真实的误判：先前以为传 `bg_store=None` 就等于"观澜不用子
    Agent"，于是认为 `guanlan_search` 的工具继承问题会自动消失。实际上 agent 工具照常注册、
    前台可用——下面这条 `agent_reviewer is not None` 就是那个误判的反例。
    """
    from guanlan.web.conversation import Conversation

    conv = Conversation("cid-sub", kb_with_agent, None, persist=False,
                        search_cache=CorpusCache())
    try:
        agent = conv.agent
        assert agent.bg_store is None
        assert len(agent.agent_manager.definitions) == 1  # 定义确实被发现了
        tool = agent.tools.get("agent_reviewer")
        assert tool is not None, "前台子 Agent 工具应当照常注册"
        # 后台面确实关了：参数 schema 里没有 run_in_background，也没有配套的查询工具。
        assert "run_in_background" not in (tool.parameters.get("properties") or {})
        with pytest.raises(KeyError):
            agent.tools.get("check_background_agent")
    finally:
        conv.close()


def test_guanlan_search_declares_itself_copyable_to_subagents(real_kb: Path) -> None:
    """§2.1：0.4.24 起不声明就不进子 Agent，且声明**必须是 property**（方法会被判为未声明）。"""
    from guanlan.web.chat_support import make_guanlan_search_tool

    tool = make_guanlan_search_tool(CorpusCache(), wiki=real_kb / "wiki")
    declared = tool.copies_to_subagents
    assert declared is True
    # 绑定方法恒真 → 上游把"可调用"专门判为**未声明**。写成方法会静默失效，故在此钉死。
    assert not callable(declared)


def test_subagent_copy_satisfies_the_runtime_checks(real_kb: Path) -> None:
    """副本要过上游那三关：不是同一实例、名字不变、可浅拷贝（且共享 cache 是安全的）。"""
    from agentao.agents.tools._wrapper import _copy_declared_host_tool

    from guanlan.web.chat_support import make_guanlan_search_tool

    cache = CorpusCache()
    tool = make_guanlan_search_tool(cache, wiki=real_kb / "wiki")

    plain = copy.copy(tool)
    assert plain is not tool and plain.name == tool.name
    assert plain._search_cache is cache  # 浅拷贝共享 cache——正是我们要的，且 cache 自带锁

    # 直接走上游那个决定"给不给子 Agent"的函数：它返回 None 就是没给。
    copied = _copy_declared_host_tool(tool, tool.name, "reviewer")
    assert copied is not None, "上游判定不给子 Agent——声明没生效"
    assert copied is not tool and copied.name == tool.name


def test_an_undeclared_host_tool_is_still_left_out(real_kb: Path) -> None:
    """对照组：没声明的宿主工具**确实**进不去——证明上面那条不是恒真。

    缺这条，`test_subagent_copy_satisfies_the_runtime_checks` 就可能在"上游根本没在看声明"
    的情况下照样绿（漏报）。
    """
    from agentao.agents.tools._wrapper import _copy_declared_host_tool
    from agentao.tools.base import Tool

    from guanlan.web.chat_support import make_guanlan_search_tool

    tool = make_guanlan_search_tool(CorpusCache(), wiki=real_kb / "wiki")

    # 同一个工具类、只把声明回落到 `Tool` 基类的默认（False）——走的是上游"未声明、静默不给"
    # 那条路。**不能**用一个没有该属性的裸类：那会走"读声明抛 AttributeError"的另一条拒绝路径，
    # 照样返回 None，对照组就不是在对照"声明"本身了。
    undeclared_cls = type(
        "_Undeclared", (type(tool),), {"copies_to_subagents": Tool.copies_to_subagents}
    )
    undeclared = undeclared_cls()
    assert undeclared.copies_to_subagents is False
    assert _copy_declared_host_tool(undeclared, tool.name, "reviewer") is None


# ── 内部依赖面（§3.3：收敛前先钉死现状）──────────────────────────────────────


def test_private_agentao_imports_we_depend_on_still_resolve() -> None:
    """几处**私有**依赖：都带 docstring 说明理由，但上游改名时必须立刻有人知道。"""
    from agentao._env import safe_load_dotenv  # noqa: F401  runtime.drop_poisoned_api_keys
    from agentao.embedding.compat import build_compat_transport  # noqa: F401
    from agentao.llm._stream_response import WIRE_CARRIER_KEYS  # noqa: F401
    from agentao.runtime.model import purge_thinking_artifacts  # noqa: F401


def test_compat_transport_still_exposes_the_ask_user_seam(real_conv) -> None:
    """P4.15b 把 `SdkTransport._ask_user` 换成富回调——这个接缝还在，且我们换上去了。"""
    transport = real_conv.agent.transport
    assert hasattr(transport, "_ask_user")
    assert transport._ask_user == real_conv._ask_user_cb


def test_filesystem_protocol_is_publicly_exported() -> None:
    """§3.3 计划把 `FileSystem` 类型改从公开的 `host.protocols` 取——先确认它真在那儿。"""
    from agentao.capabilities.filesystem import FileSystem as PrivateFS
    from agentao.host.protocols import FileSystem as PublicFS

    assert PublicFS is PrivateFS  # 同一个对象：换 import 路径是零风险的纯收敛


def test_api_format_env_knob_is_read_by_the_factory() -> None:
    """§4：原生协议靠 `{PROVIDER}_API_FORMAT` 选，观澜不必自己实现协议适配。"""
    from agentao.llm._api_format import DEFAULT_API_FORMAT, resolve_api_format

    assert DEFAULT_API_FORMAT == "openai-completions"  # 不设就还是老协议
    assert resolve_api_format("openai-responses") == "openai-responses"
    assert resolve_api_format("anthropic-messages") == "anthropic-messages"
    assert resolve_api_format(None) == DEFAULT_API_FORMAT


def test_python_floor_unchanged() -> None:
    """0.5.x 没有抬 Python 下限——观澜仍声明 >=3.10，别跟着漂。"""
    import sys

    assert sys.version_info >= (3, 10)
    root = Path(__file__).resolve().parent.parent
    assert 'requires-python = ">=3.10"' in (root / "pyproject.toml").read_text("utf-8")


# ── 远端技能的受闸确认（P4.24 附录 B，agentao 0.5.10）──────────────────────────────
#
# 上游约定：runner 在 `gated(note)` 里**同一线程同步**调 `transport.confirm_tool`，宿主据
# `gate_note()` 认出这是一次「只能现在问人、不得由常设授权代答、答复不授予本次之外任何东西」的确认。
# 以下用例走真 compat transport（`real_conv.agent.transport`），只模拟 runner 那层 `gated()`。


def _confirm_in_thread(transport, note):
    """在**新线程**里模拟 runner：`gated(note)` 内调 `confirm_tool`。gate_note 是线程局部的，
    故 gated 必须与 confirm_tool 在同一线程——与 tool_runner 的真实调用形状一致。"""
    import threading

    from agentao.transport.confirmation import gated

    out: dict = {}

    def run():
        with gated(note):
            out["result"] = transport.confirm_tool("activate_skill", "desc", {"name": "x"})

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, out


def test_gate_note_primitive_is_thread_local() -> None:
    from agentao.transport import gate_note
    from agentao.transport.confirmation import gated

    assert gate_note() is None
    with gated("note-1"):
        assert gate_note() == "note-1"
    assert gate_note() is None


def test_auto_mode_still_waives_ungated_confirmations(real_conv) -> None:
    """对照组：非受闸确认在 auto 下照旧静默放行（P4.15 行为不变）。"""
    real_conv._confirm_mode = "auto"
    th, out = _confirm_in_thread(real_conv.agent.transport, None)
    th.join(5)
    assert out["result"] is True


def test_gated_confirmation_is_denied_at_once_without_a_confirm_ui(real_conv) -> None:
    """IM（confirm_ui=False）：受闸确认无人可答——即便会话在 auto 也**立即**拒，不等超时。"""
    real_conv._confirm_mode = "auto"
    real_conv._confirm_ui = False
    th, out = _confirm_in_thread(real_conv.agent.transport, "consent to remote skill")
    th.join(5)
    assert not th.is_alive() and out["result"] is False


def _capture_request(real_conv):
    import threading

    seen: dict = {}
    got = threading.Event()

    def emit(kind, data):
        if kind == "confirm_request":
            seen.update(data)
            got.set()

    real_conv._emit = emit
    real_conv._confirm_timeout = 5
    return seen, got


def test_gated_confirmation_asks_even_in_auto_mode(real_conv) -> None:
    """auto 捷径对受闸确认不生效：必弹，且帧里带 gated + 说明（前端据它不出「本会话起自动放行」）。"""
    real_conv._confirm_mode = "auto"
    seen, got = _capture_request(real_conv)
    th, out = _confirm_in_thread(real_conv.agent.transport, "consent to remote skill")
    assert got.wait(5), "受闸确认在 auto 模式下被静默放行了"
    assert seen["gated"] is True and seen["gate_note"] == "consent to remote skill"
    assert real_conv.resolve_confirm(seen["interaction_id"], "deny")
    th.join(5)
    assert out["result"] is False


@pytest.mark.parametrize("gated_note, mode_after", [("consent", "ask"), (None, "auto")])
def test_allow_session_only_flips_mode_for_ungated_confirmations(
    real_conv, gated_note, mode_after
) -> None:
    """受闸确认上的 allow_session 只放行**这一次**、confirm_mode 不变（后端闸，不靠前端藏按钮）；
    非受闸确认上照旧翻 auto（对照组）。"""
    real_conv._confirm_mode = "ask"
    seen, got = _capture_request(real_conv)
    th, out = _confirm_in_thread(real_conv.agent.transport, gated_note)
    assert got.wait(5)
    assert real_conv.resolve_confirm(seen["interaction_id"], "allow_session")
    th.join(5)
    assert out["result"] is True
    assert real_conv.confirm_mode == mode_after


def test_new_mcp_resource_and_skill_tools_are_read_only() -> None:
    """0.5.10 新增的四个工具只在某个 server 声明 resources/skills 时才注册，`real_conv` 里没有它们，
    故 `test_static_fallback_agrees_with_real_is_read_only` 覆盖不到——这里直接按类核对。"""
    from agentao.mcp.resource_tools import (
        ListMcpResourcesTool,
        ListMcpResourceTemplatesTool,
        ReadMcpResourceTool,
    )
    from agentao.mcp.skill_tools import ReadSkillFileTool

    from guanlan.web.chat_support import _READ_TOOL_NAMES

    tools = [
        ListMcpResourcesTool(None),
        ListMcpResourceTemplatesTool(None),
        ReadMcpResourceTool(None),
        ReadSkillFileTool(None),
    ]
    for tool in tools:
        assert tool.is_read_only is True, tool.name
        assert tool.name in _READ_TOOL_NAMES, tool.name
