"""P4.37 Web 轮内并行子 Agent（见 docs/P4.37-Web轮内并行子Agent.md）。

复用 `test_web.py` 的 fake 工厂。假子 Agent 直接在宿主传入的 `bg_store` 上登记任务、起线程，
经宿主的 `PolicyFileSystem` 写盘，再把记录翻到终态——与 agentao `_launch_background` 的
「先跑完、再 `update(status=…)`」同序，屏障据 `count_in_flight()` 判定。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from agentao.agents import BackgroundTaskStore  # noqa: E402
from agentao.host import CancellationToken  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_web import _FakeAgent  # noqa: E402

import guanlan.cli as cli  # noqa: E402
from guanlan.errors import GuanlanError  # noqa: E402
from guanlan.web.app import create_app  # noqa: E402
from guanlan.web.conversation import Conversation  # noqa: E402
from guanlan.web.policy_fs import make_policy_fs  # noqa: E402


@pytest.fixture
def chat_env(kb, monkeypatch):
    import guanlan.web.chat as chat_mod

    captured: dict = {"kwargs": [], "agents": []}
    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)

    def fake_bfe(**kwargs):
        agent = _FakeAgent(kwargs)
        captured["kwargs"].append(kwargs)
        captured["agents"].append(agent)
        return agent

    monkeypatch.setattr(chat_mod, "build_from_environment", fake_bfe)
    # 测试里把「停止后多久改提示」缩短，复查也加快。
    monkeypatch.setattr(Conversation, "_BG_STOP_HINT_SECONDS", 0.3)
    monkeypatch.setattr(Conversation, "_BG_POLL_INTERVAL", 0.02)
    monkeypatch.setattr(Conversation, "_BG_WATCH_INTERVAL", 0.02)
    return kb, captured


def _client(kb, **kw):
    return TestClient(create_app(kb, **kw))


# ── 假后台子 Agent ─────────────────────────────────────────────────────────────


class _Child:
    """在 store 上登记一个后台任务并起线程：先 `delay` 秒，再经 fs 写 `path`，最后翻终态。

    `ignore_cancel_for`：收到取消后再拖这么久才停（模拟卡在不响应取消的阻塞调用里），停前仍写一次。
    `wait_for_cancel`：不自己结束，一直等到被取消（模拟长任务中途被停）。
    """

    def __init__(self, store: BackgroundTaskStore, fs, path: Path, *, delay: float = 0.0,
                 wait_for_cancel: bool = False, ignore_cancel_for: float = 0.0,
                 write_before: Path | None = None, release: threading.Event | None = None,
                 task: str = "task", observers: list | None = None, tools: int = 0) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.token = CancellationToken()
        store.register(self.id, "generalist", task[:80])  # agentao 任务表同样只存前 80 字
        store.register_token(self.id, self.token)
        self.wrote_at: float | None = None
        self.started = threading.Event()  # 已登记、已做完 write_before，进入等待

        def run() -> None:
            # 任何异常都要把记录翻到终态：否则屏障会一直等（真 agentao 的 _run 同样在 finally 里收尾）。
            try:
                body()
            except BaseException:
                store.update(self.id, status="failed", error="test child crashed")
                raise

        def relay(event) -> None:  # 镜像 agentao relay_subagent_events：子会话事件到父观察者
            for cb in observers or []:
                cb(event)

        def body() -> None:
            store.mark_running(self.id)
            sid = f"child-{self.id}"
            relay(SimpleNamespace(event_type="subagent_lifecycle", phase="spawned",
                                  parent_task_id=self.id, child_session_id=sid))
            for k in range(tools):
                relay(SimpleNamespace(event_type="tool_lifecycle", phase="started",
                                      session_id=sid, tool_name="read_file"))
                if k < tools - 1:
                    relay(SimpleNamespace(event_type="tool_lifecycle", phase="completed",
                                          session_id=sid, tool_name="read_file"))
            if write_before is not None:
                fs.write_text(write_before, "早写\n")
            self.started.set()
            if release is not None:
                release.wait(timeout=10)
            elif wait_for_cancel:
                while not self.token.is_cancelled:
                    time.sleep(0.01)
                if ignore_cancel_for:
                    time.sleep(ignore_cancel_for)
            else:
                time.sleep(delay)
            fs.write_text(path, "子 Agent 写的\n")
            self.wrote_at = time.monotonic()
            store.update(self.id, status="cancelled" if self.token.is_cancelled else "completed",
                         result="ok")

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()


def _spawn_action(children: list, **kw):
    """fake agent 的 action：在 arun 的工作线程里派一个假后台子 Agent（镜像主 Agent 派出后就收尾）。"""
    path = kw.pop("path", "wiki/sources/bg.md")
    raise_after = kw.pop("raise_after", False)
    once = kw.pop("once", False)  # 只在第一轮派（goal 续跑时后续轮不派，回归时不至于卡死）

    def action(agent) -> None:
        if once and children:
            return
        store = agent.kwargs["bg_store"]
        fs = agent.kwargs["filesystem"]
        root = Path(agent.kwargs["working_directory"])
        extra = dict(kw)
        if extra.get("write_before") is not None:
            extra["write_before"] = root / extra["write_before"]
        extra.setdefault("observers", getattr(agent, "host_observers", []))
        children.append(_Child(store, fs, root / path, **extra))
        if raise_after:
            raise RuntimeError("主 Agent 派发后出错")

    return action


def _stream(client, body, *, url="/api/chat", on_frame=None):
    frames: list[tuple[str, object]] = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        event = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip())
                frames.append((event, payload))
                if on_frame is not None:
                    on_frame(event, payload)
    return frames


def _stream_in_thread(client, body, *, url="/api/chat"):
    """后台线程跑一轮（TestClient 把整条 SSE 流缓冲到结束才交出，读帧时无法插入操作）。"""
    frames: list[tuple[str, object]] = []
    th = threading.Thread(target=lambda: frames.extend(_stream(client, body, url=url)), daemon=True)
    th.start()
    return th, frames


def _wait_child(children: list, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not children and time.monotonic() < deadline:
        time.sleep(0.01)
    assert children, "假子 Agent 未派出"
    assert children[0].started.wait(timeout), "假子 Agent 未起跑"
    return children[0]


def _lock_free(gate) -> bool:
    ok = gate.write_lock.acquire(blocking=False)
    if ok:
        gate.write_lock.release()
    return ok


def _first(frames, event):
    return next((p for e, p in frames if e == event), None)


def _new_conv(client) -> str:
    frames = _stream(client, {"message": "hi"})
    done = _first(frames, "done")
    assert done is not None
    return done["conversation_id"]


# ── 开关 ──────────────────────────────────────────────────────────────────────


def test_off_by_default_passes_none(chat_env):
    kb, captured = chat_env
    with _client(kb) as c:
        _new_conv(c)
    assert captured["kwargs"][-1]["bg_store"] is None


def test_on_passes_a_memory_store_per_conversation(chat_env):
    kb, captured = chat_env
    with _client(kb, background_agents=3) as c:
        _new_conv(c)
        _new_conv(c)
    s1, s2 = captured["kwargs"][-2]["bg_store"], captured["kwargs"][-1]["bg_store"]
    assert isinstance(s1, BackgroundTaskStore) and isinstance(s2, BackgroundTaskStore)
    assert s1 is not s2  # 每会话一个，计数天然隔离
    assert s1.max_concurrent == 3
    assert s1._resolve_persistence_path() is None  # 纯内存，不写 .agentao/background_tasks.json
    assert not (kb / ".agentao" / "background_tasks.json").exists()


def test_reader_forces_off(chat_env):
    kb, captured = chat_env
    with _client(kb, reader=True, background_agents=4) as c:
        _new_conv(c)
    assert captured["kwargs"][-1]["bg_store"] is None


def test_invalid_cap_rejected(chat_env):
    kb, _ = chat_env
    with pytest.raises(GuanlanError):
        create_app(kb, background_agents=0)


def test_cli_flag_parsing():
    parser = cli.build_parser()
    assert parser.parse_args(["web"]).background_agents is None
    assert parser.parse_args(["web", "--background-agents"]).background_agents == 4
    assert parser.parse_args(["web", "--background-agents", "6"]).background_agents == 6


# ── 收尾屏障 ──────────────────────────────────────────────────────────────────


def test_barrier_waits_late_write_into_journal_undo_and_check(chat_env):
    """主 Agent 派出就收尾；子 Agent 0.3 s 后才写。done 帧的撤销含这条写，check 已跑。"""
    kb, captured = chat_env
    children: list = []
    with _client(kb, mode="workspace-write", background_agents=4) as c:
        cid = _new_conv(c)
        captured["agents"][-1].action = _spawn_action(children, delay=0.3)
        frames = _stream(c, {"message": "go", "conversation_id": cid})
    done = _first(frames, "done")
    assert done is not None
    assert "wiki/sources/bg.md" in done["undo"]["paths"]
    assert "check" in done
    bg = [p for e, p in frames if e == "bg_agents"]
    assert bg[0]["running"] == 1 and bg[-1]["running"] == 0
    assert (kb / "wiki" / "sources" / "bg.md").exists()


def test_barrier_covers_arun_raising_after_dispatch(chat_env):
    """派发后 arun 抛错：晚到的写仍进本轮日志与 check，屏障结束前不释库写锁。"""
    kb, captured = chat_env
    children: list = []
    release = threading.Event()
    with _client(kb, mode="workspace-write", background_agents=4) as c:
        cid = _new_conv(c)
        captured["agents"][-1].action = _spawn_action(children, release=release, raise_after=True)
        th, frames = _stream_in_thread(c, {"message": "go", "conversation_id": cid})
        _wait_child(children)
        time.sleep(0.2)  # 让 arun 抛错、进到收尾屏障
        assert th.is_alive()
        assert not _lock_free(c.app.state.write_gate)  # 屏障期间仍持库写锁
        release.set()
        th.join(timeout=10)
        assert not th.is_alive()
    err = _first(frames, "error")
    assert err is not None and "主 Agent 派发后出错" in err["message"]
    assert "wiki/sources/bg.md" in err["undo"]["paths"]
    assert "check" in err


def test_read_only_turn_also_waits(chat_env):
    kb, captured = chat_env
    children: list = []
    with _client(kb, background_agents=4) as c:
        cid = _new_conv(c)
        captured["agents"][-1].action = _spawn_action(children, delay=0.2, path="workspace/x.md")
        frames = _stream(c, {"message": "go", "conversation_id": cid})
    assert _first(frames, "done") is not None
    assert children[0].wrote_at is not None  # done 之前子 Agent 已写完
    # 帧按整份内容去重：任务 pending→running 也算变化，可能多推一帧 running=1；只看计数的相邻去重序列。
    counts = [p["running"] for e, p in frames if e == "bg_agents"]
    assert [n for i, n in enumerate(counts) if i == 0 or n != counts[i - 1]] == [1, 0]


def test_no_background_task_no_frame(chat_env):
    kb, _ = chat_env
    with _client(kb, mode="workspace-write", background_agents=4) as c:
        frames = _stream(c, {"message": "hi"})
    assert _first(frames, "bg_agents") is None
    assert _first(frames, "done") is not None


def test_frames_during_arun_show_dispatch_and_progress(chat_env):
    """主 Agent 派出后自己等结果（镜像 check_background_agent）：屏障前的轮询已推出「运行中→已结束」，
    帧只带 ID 与状态，不带任务原文。"""
    kb, captured = chat_env
    children: list = []
    release = threading.Event()
    spawn = _spawn_action(children, release=release)

    def action(agent):
        spawn(agent)
        child = children[0]
        time.sleep(0.1)  # 轮询看见「运行中」
        release.set()
        child.thread.join(timeout=5)
        time.sleep(0.1)  # 轮询看见「已结束」

    with _client(kb, mode="workspace-write", background_agents=4) as c:
        cid = _new_conv(c)
        captured["agents"][-1].action = action
        frames = _stream(c, {"message": "go", "conversation_id": cid})
    bg = [p for e, p in frames if e == "bg_agents"]
    assert bg[0]["running"] == 1 and bg[0]["finished"] == 0
    # 登记到子线程 mark_running 之间状态是 pending，负载高时首帧可能先推出 pending——那也是正确帧。
    assert bg[0]["tasks"][0]["status"] in ("pending", "running")
    first = next(t for p in bg for t in p["tasks"] if t["status"] == "running")
    assert first["id"] == children[0].id
    assert "label" not in first  # 任务首行没有【…】标签
    assert bg[-1]["running"] == 0 and bg[-1]["finished"] == 1
    assert all(set(p) == {"running", "finished", "stopping", "tasks"} for p in bg)
    assert len(bg) == len({json.dumps(p, sort_keys=True) for p in bg})  # 内容不变不重复推


def test_frames_count_only_this_turn(chat_env):
    """上一轮已结束的任务不计入本轮显示；本轮没派就不推帧。"""
    kb, captured = chat_env
    children: list = []
    with _client(kb, background_agents=4) as c:
        cid = _new_conv(c)
        agent = captured["agents"][-1]
        agent.action = _spawn_action(children, delay=0.05)
        _stream(c, {"message": "第一轮", "conversation_id": cid})
        agent.action = None
        frames = _stream(c, {"message": "第二轮", "conversation_id": cid})
        agent.action = _spawn_action(children, delay=0.05, path="workspace/y.md")
        frames3 = _stream(c, {"message": "第三轮", "conversation_id": cid})
    assert _first(frames, "bg_agents") is None
    bg3 = [p for e, p in frames3 if e == "bg_agents"]
    assert bg3[-1]["finished"] == 1 and [t["id"] for t in bg3[-1]["tasks"]] == [children[1].id]


def test_frames_show_label_and_tool_progress_but_not_task_text(chat_env):
    """P4.37 §3.1：帧里有行首【…】标签、工具次数、当前工具与起止时间；任务正文不上线。"""
    kb, captured = chat_env
    children: list = []
    release = threading.Event()
    spawn = _spawn_action(children, release=release, tools=3,
                          task="【第 2 组：A.2】你是核查员。机密正文不应出现在界面上。")

    def action(agent):
        spawn(agent)
        time.sleep(0.15)  # 轮询看见「运行中 · 3 次工具 · read_file」
        release.set()
        children[0].thread.join(timeout=5)
        time.sleep(0.1)

    with _client(kb, mode="workspace-write", background_agents=4) as c:
        cid = _new_conv(c)
        agent = captured["agents"][-1]
        assert len(agent.host_observers) == 1  # 开开关即挂观察者
        assert agent.kwargs["relay_subagent_events"] is True
        agent.action = action
        frames = _stream(c, {"message": "go", "conversation_id": cid})
    bg = [p for e, p in frames if e == "bg_agents"]
    running = next(p["tasks"][0] for p in bg if p["tasks"][0]["status"] == "running")
    assert running["label"] == "第 2 组：A.2"
    assert running["tools"] == 3 and running["tool"] == "read_file"
    assert isinstance(running["started_at"], float) and "finished_at" not in running
    last = bg[-1]["tasks"][0]
    assert last["status"] == "completed" and "tool" not in last and "finished_at" in last
    assert "机密正文" not in json.dumps(frames, ensure_ascii=False)


def test_no_label_no_label_key_and_off_switch_has_no_observer(chat_env):
    kb, captured = chat_env
    children: list = []
    with _client(kb, background_agents=4) as c:
        cid = _new_conv(c)
        captured["agents"][-1].action = _spawn_action(children, delay=0.05, task="你是核查员", path="workspace/z.md")
        frames = _stream(c, {"message": "go", "conversation_id": cid})
    assert all("label" not in t for e, p in frames if e == "bg_agents" for t in p["tasks"])
    with _client(kb) as c:
        _new_conv(c)
    assert "relay_subagent_events" not in captured["kwargs"][-1]
    assert not getattr(captured["agents"][-1], "host_observers", [])


def test_host_event_observer_ignores_foreground_and_unknown_sessions(chat_env):
    """前台子 Agent 的 spawned 不带 parent_task_id，不登记；未登记会话的工具事件不计数；坏事件不抛。"""
    kb, captured = chat_env
    conv = Conversation("cid-obs", kb, None, persist=False, background_agents=2)
    try:
        conv._on_host_event(SimpleNamespace(event_type="subagent_lifecycle", phase="spawned",
                                            parent_task_id=None, child_session_id="fg"))
        conv._on_host_event(SimpleNamespace(event_type="tool_lifecycle", phase="started",
                                            session_id="fg", tool_name="read_file"))
        conv._on_host_event(object())
        assert conv._bg_progress == {} and conv._bg_child_sessions == {}
    finally:
        conv.close()


# ── 停止 ──────────────────────────────────────────────────────────────────────


def test_stop_during_barrier_cancels_each_and_returns_stopped(chat_env):
    """arun 已正常返回、屏障中按停止：逐个 cancel（不 close_owner），排空后发 stopped（带撤销/检查）。"""
    kb, captured = chat_env
    children: list = []
    with _client(kb, mode="workspace-write", background_agents=4) as c:
        cid = _new_conv(c)
        agent = captured["agents"][-1]
        store = agent.kwargs["bg_store"]
        closed: list = []
        store.close_owner = lambda *a, **k: closed.append(1) or []
        agent.action = _spawn_action(children, wait_for_cancel=True, write_before="wiki/sources/early.md")
        th, frames = _stream_in_thread(c, {"message": "go", "conversation_id": cid})
        child = _wait_child(children)
        time.sleep(0.2)  # arun 已返回，屏障在等
        assert th.is_alive()
        assert c.post(f"/api/chat/{cid}/stop").json()["stopped"] is True
        th.join(timeout=10)
        assert not th.is_alive()
    assert closed == []
    assert child.token.is_cancelled
    assert _first(frames, "done") is None
    st = _first(frames, "stopped")
    assert st is not None
    assert set(st["undo"]["paths"]) >= {"wiki/sources/early.md", "wiki/sources/bg.md"}
    assert "check" in st
    assert store.count_in_flight() == 0


def test_stop_past_hint_keeps_write_lock_until_drained(chat_env):
    """停止后迟迟不停：推 stopping 提示，但不放行——其间另一写者拿不到库写锁；停下前的写仍进本轮日志。"""
    kb, captured = chat_env
    children: list = []
    with _client(kb, mode="workspace-write", background_agents=4) as c:
        cid = _new_conv(c)
        captured["agents"][-1].action = _spawn_action(children, wait_for_cancel=True, ignore_cancel_for=1.0)
        th, frames = _stream_in_thread(c, {"message": "go", "conversation_id": cid})
        _wait_child(children)
        time.sleep(0.1)
        c.post(f"/api/chat/{cid}/stop")
        time.sleep(0.6)  # 已过提示阈值（0.3 s），子 Agent 仍没停
        assert th.is_alive()
        assert not _lock_free(c.app.state.write_gate)
        th.join(timeout=10)
        assert not th.is_alive()
    assert any(e == "bg_agents" and p.get("stopping") for e, p in frames)
    st = _first(frames, "stopped")
    assert st is not None and "wiki/sources/bg.md" in st["undo"]["paths"]


def test_goal_turn_stopped_in_barrier_pauses(chat_env):
    """goal 轮在屏障中被停：按停止收为 paused，不计 turns_used。"""
    kb, captured = chat_env
    children: list = []
    with _client(kb, mode="workspace-write", background_agents=4) as c:
        cid = _new_conv(c)
        captured["agents"][-1].action = _spawn_action(children, wait_for_cancel=True, once=True)
        assert c.post(f"/api/chat/{cid}/goal", json={"objective": "x", "turns": 5}).status_code == 200
        th, frames = _stream_in_thread(c, {}, url=f"/api/chat/{cid}/goal/run")
        _wait_child(children)
        time.sleep(0.2)
        c.post(f"/api/chat/{cid}/stop")
        th.join(timeout=10)
        assert not th.is_alive()
        info = c.get(f"/api/chat/{cid}/info").json()["goal"]
    assert _first(frames, "turn_done").get("stopped") is True
    assert info["status"] == "paused" and info["turns_used"] == 0


# ── 写日志加锁 ────────────────────────────────────────────────────────────────


def test_concurrent_writes_keep_first_before_bytes(tmp_path: Path):
    kb = tmp_path
    (kb / "wiki").mkdir()
    target = kb / "wiki" / "p.md"
    target.write_text("原文\n", encoding="utf-8")
    fs = make_policy_fs(kb)
    fs.begin_journal()
    threads = [
        threading.Thread(target=fs.write_text, args=(target, f"第{i}版\n")) for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    journal = fs.end_journal()
    (before, _after), = journal.values()
    assert before == "原文\n".encode()


def test_end_journal_waits_for_inflight_journaled_write(tmp_path: Path):
    """写线程已在记账中时调 end_journal：它要等这条写记完，取走的日志含这条写。"""
    kb = tmp_path
    (kb / "wiki").mkdir()
    target = kb / "wiki" / "p.md"
    fs = make_policy_fs(kb)
    inner = fs._fs
    entered, release = threading.Event(), threading.Event()
    real_write = inner.write_text

    class _Slow:
        def __getattr__(self, name):
            return getattr(inner, name)

        def write_text(self, path, data, *, append=False):
            entered.set()
            release.wait(timeout=5)
            real_write(path, data, append=append)

    fs._fs = _Slow()
    fs.begin_journal()
    w = threading.Thread(target=fs.write_text, args=(target, "x\n"))
    w.start()
    assert entered.wait(timeout=5)
    got: dict = {}
    e = threading.Thread(target=lambda: got.setdefault("j", fs.end_journal()))
    e.start()
    time.sleep(0.1)
    assert e.is_alive()  # 被记账锁挡住
    release.set()
    w.join()
    e.join()
    assert target.resolve() in got["j"]


def test_write_outside_undo_scope_does_not_take_lock(tmp_path: Path):
    kb = tmp_path
    (kb / "workspace").mkdir()
    fs = make_policy_fs(kb)
    fs.begin_journal()
    with fs._journal_lock:  # 锁被占也不挡范围外的写
        fs.write_text(kb / "workspace" / "r.md", "x\n")
    assert fs.end_journal() == {}



def test_asyncio_cancel_during_arun_cancels_children(chat_env):
    """asyncio 取消落在 `await arun`（停服 / 断线取消 pending 轮）：屏障按停止处理，逐个取消子 Agent、
    等它们停下才释写锁，再照常抛 CancelledError。"""
    import asyncio

    from guanlan.web.jobs import WriteGate

    kb, captured = chat_env
    children: list = []
    gate = WriteGate(kb)
    conv = Conversation("cid-cancel", kb, None, persist=False, mode="workspace-write",
                        write_gate=gate, background_agents=4)
    spawn = _spawn_action(children, wait_for_cancel=True)

    def action(agent):
        spawn(agent)
        while not children[0].token.is_cancelled:  # 主 Agent 仍在 arun 里（等结果）
            time.sleep(0.01)

    captured["agents"][-1].action = action

    async def main():
        task = asyncio.ensure_future(conv.turn("go", lambda k, d: None, {}))
        while not (children and children[0].started.is_set()):
            await asyncio.sleep(0.01)
        task.cancel()
        t0 = time.monotonic()
        done, _ = await asyncio.wait({task}, timeout=3)  # 只等不再取消：屏障须自己取消子 Agent
        assert done, "屏障没有取消子 Agent，一直在等它们自然结束"
        assert time.monotonic() - t0 < 3
        with pytest.raises(asyncio.CancelledError):
            task.result()

    try:
        asyncio.run(main())
    finally:
        for ch in children:  # 失败时别把测试进程挂住
            ch.token.cancel("test-cleanup")
        conv.close()
    assert children[0].token.is_cancelled
    assert children[0].wrote_at is not None  # 停下前的写已完成，屏障等到了它
    assert _lock_free(gate)
