"""P5.5.2 Web 问答画像（见 docs/P5.5.2-Web问答画像.md）。

复用 `test_web.py` 的 fake 工厂（`chat_env` 把 `build_from_environment` 换成 `_FakeAgent`，记下构造参数）。
`~` 由 conftest 的 autouse 夹具指向空临时目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from test_web import _chat, _FakeAgent  # noqa: E402

import guanlan.cli as cli  # noqa: E402
from guanlan.errors import GuanlanError  # noqa: E402
from guanlan.query import build_profile_instructions  # noqa: E402
from guanlan.web.app import create_app  # noqa: E402
from guanlan.web.conversation_store import ConversationStore  # noqa: E402
from guanlan.web.profile import ProfileSource, startup_line  # noqa: E402

DEFAULT_SHOWN = str(Path("~") / ".guanlan" / "USER.md")


@pytest.fixture
def chat_env(kb, monkeypatch):
    """同 `test_web.chat_env`：fake `build_from_environment`，记下每次构造参数与 agent。"""
    import guanlan.web.chat as chat_mod

    captured = {"kwargs": [], "agents": []}
    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)

    def fake_bfe(**kwargs):
        agent = _FakeAgent(kwargs)
        captured["kwargs"].append(kwargs)
        captured["agents"].append(agent)
        return agent

    monkeypatch.setattr(chat_mod, "build_from_environment", fake_bfe)
    return kb, captured


@pytest.fixture
def chat_client(chat_env):
    kb, captured = chat_env
    with TestClient(create_app(kb)) as c:
        yield c, captured


def _write_default(text: str = "## 回答方式\n画像标记词-甲") -> Path:
    p = Path.home() / ".guanlan" / "USER.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _outside(tmp_path_factory, text: str = "显式画像-乙", name: str = "p.md") -> Path:
    p = tmp_path_factory.mktemp("outside") / name
    p.write_text(text, encoding="utf-8")
    return p


def _new_conv(client) -> str:
    _, done, error = _chat(client, "问")
    assert error is None
    return done["conversation_id"]


def _info(client, cid: str) -> dict:
    resp = client.get(f"/api/chat/{cid}/info")
    assert resp.status_code == 200
    return resp.json()


# ───────── W1/W2 来源 ─────────


def test_no_profile_source_passes_nothing(chat_env) -> None:
    kb, captured = chat_env
    _write_default()  # 文件在，但 create_app 未给来源（IM、直建 app、--no-profile 的形态）
    with TestClient(create_app(kb)) as client:
        cid = _new_conv(client)
        assert "project_instructions" not in captured["kwargs"][0]
        assert _info(client, cid)["profile"] == {"state": "none", "path": None, "warning": None}


def test_default_profile_reaches_new_session(chat_env) -> None:
    kb, captured = chat_env
    _write_default()
    with TestClient(create_app(kb, profile=ProfileSource())) as client:
        assert client.get("/api/info").json()["profile"] == {
            "source": "default", "path": DEFAULT_SHOWN, "warning": None
        }
        cid = _new_conv(client)
        want = build_profile_instructions(kb, "## 回答方式\n画像标记词-甲")
        assert captured["kwargs"][0]["project_instructions"] == want
        info = _info(client, cid)
        assert info["profile"] == {"state": "active", "path": DEFAULT_SHOWN, "warning": None}
        assert "画像标记词" not in str(info)  # info 不含画像正文


def test_default_absent_then_created_after_start(chat_env) -> None:
    """启动后才建的默认画像，对之后的新会话生效（每个会话重新解析，决策W2）。"""
    kb, captured = chat_env
    with TestClient(create_app(kb, profile=ProfileSource())) as client:
        assert client.get("/api/info").json()["profile"] == {"source": None, "path": None, "warning": None}
        _new_conv(client)
        assert "project_instructions" not in captured["kwargs"][0]
        _write_default()
        assert client.get("/api/info").json()["profile"]["source"] == "default"
        _new_conv(client)
        assert "画像标记词-甲" in captured["kwargs"][1]["project_instructions"]


def test_session_snapshot_and_new_session_rereads(chat_env) -> None:
    """改画像只对新会话生效；已开会话不变（决策W3）。"""
    kb, captured = chat_env
    p = _write_default("版本一")
    with TestClient(create_app(kb, profile=ProfileSource())) as client:
        first = _new_conv(client)
        p.write_text("版本二", encoding="utf-8")
        _chat(client, "再问", conversation_id=first)  # 同一会话续聊：不重读
        assert len(captured["agents"]) == 1
        assert captured["agents"][0].kwargs["project_instructions"].endswith("版本一")
        _new_conv(client)
        assert captured["kwargs"][1]["project_instructions"].endswith("版本二")


def test_broken_profile_still_builds_session(chat_env) -> None:
    """会话构造时读取失败：会话照建、不带画像、state=failed（决策W3）。"""
    kb, captured = chat_env
    _write_default("  ")
    with TestClient(create_app(kb, profile=ProfileSource())) as client:
        app_prof = client.get("/api/info").json()["profile"]  # 首轮前就如实报读不了
        assert app_prof["source"] == "default" and "空的" in app_prof["warning"]
        cid = _new_conv(client)
        assert "project_instructions" not in captured["kwargs"][0]
        prof = _info(client, cid)["profile"]
        assert prof["state"] == "failed" and "空的" in prof["warning"]
        assert client.post(f"/api/chat/{cid}/profile", json={"enabled": True}).status_code == 409


def test_explicit_profile_used(chat_env, tmp_path_factory) -> None:
    kb, captured = chat_env
    _write_default("默认的")
    p = _outside(tmp_path_factory)
    with TestClient(create_app(kb, profile=ProfileSource(p))) as client:
        assert client.get("/api/info").json()["profile"]["source"] == "explicit"
        _new_conv(client)
        assert captured["kwargs"][0]["project_instructions"].endswith("显式画像-乙")


def test_reader_never_carries_profile(chat_env) -> None:
    kb, captured = chat_env
    _write_default()
    with TestClient(create_app(kb, reader=True, profile=ProfileSource())) as client:
        assert "profile" not in client.get("/api/info").json()
        cid = _new_conv(client)
        assert "project_instructions" not in captured["kwargs"][0]
        assert client.post(f"/api/chat/{cid}/profile", json={"enabled": False}).status_code == 404


def test_im_style_store_never_carries_profile(chat_env) -> None:
    """IM 构造 ConversationStore 时不传 profile：默认画像存在也不带（决策W1）。"""
    kb, captured = chat_env
    _write_default()
    store = ConversationStore(kb, None, persist=False, confirm_ui=False)
    conv = store.create()
    try:
        assert "project_instructions" not in captured["kwargs"][0]
        assert conv.profile_info()["state"] == "none"
    finally:
        conv.close()


# ───────── W4 姿态无关 ─────────


def test_writable_start_and_mode_switch_keep_profile(chat_env) -> None:
    kb, captured = chat_env
    _write_default()
    with TestClient(create_app(kb, mode="workspace-write", profile=ProfileSource())) as client:
        cid = _new_conv(client)
        agent = captured["agents"][0]
        with_profile = agent.kwargs["project_instructions"]
        assert "画像标记词-甲" in with_profile
        assert client.post(f"/api/chat/{cid}/mode", json={"mode": "read-only"}).status_code == 200
        assert getattr(agent, "project_instructions", with_profile) == with_profile
        assert client.post(f"/api/chat/{cid}/mode", json={"mode": "workspace-write"}).status_code == 200
        assert getattr(agent, "project_instructions", with_profile) == with_profile
        assert _info(client, cid)["profile"]["state"] == "active"


# ───────── W6 /profile on|off ─────────


def test_profile_off_on_swaps_project_instructions(chat_env) -> None:
    from agentao.prompts import load_project_instructions

    kb, captured = chat_env
    _write_default()
    with TestClient(create_app(kb, profile=ProfileSource())) as client:
        cid = _new_conv(client)
        agent = captured["agents"][0]
        with_profile = agent.kwargs["project_instructions"]

        resp = client.post(f"/api/chat/{cid}/profile", json={"enabled": False})
        assert resp.status_code == 200 and resp.json()["state"] == "off"
        assert agent.project_instructions == (load_project_instructions(kb) or "")
        assert _info(client, cid)["profile"]["state"] == "off"

        resp = client.post(f"/api/chat/{cid}/profile", json={"enabled": True})
        assert resp.status_code == 200 and resp.json()["state"] == "active"
        assert agent.project_instructions == with_profile
        assert len(captured["agents"]) == 1  # 不重建 agent


def test_profile_toggle_without_profile_is_409(chat_client) -> None:
    client, _ = chat_client
    cid = _new_conv(client)
    for enabled in (True, False):
        assert client.post(f"/api/chat/{cid}/profile", json={"enabled": enabled}).status_code == 409


def test_profile_toggle_unknown_and_cold(chat_env) -> None:
    kb, _ = chat_env
    _write_default()
    with TestClient(create_app(kb, profile=ProfileSource())) as client:
        cid = _new_conv(client)
        assert client.post("/api/chat/nope/profile", json={"enabled": False}).status_code == 404
    with TestClient(create_app(kb, profile=ProfileSource())) as client:  # 模拟重启：会话在盘上、未激活
        assert client.post(f"/api/chat/{cid}/profile", json={"enabled": False}).status_code == 409


def test_profile_toggle_rejected_while_writable_turn_active(chat_env) -> None:
    """取 conv.lock 前先按层③ 拒（同 /mode），免得 agent 在可写轮里 curl 本端点自死锁。"""
    kb, _ = chat_env
    _write_default()
    app = create_app(kb, profile=ProfileSource())
    with TestClient(app) as client:
        cid = _new_conv(client)
        app.state.write_gate.enter_writable()
        try:
            assert client.post(f"/api/chat/{cid}/profile", json={"enabled": False}).status_code == 423
        finally:
            app.state.write_gate.exit_writable()


def test_restore_rereads_current_profile_and_resets_off(chat_env) -> None:
    """懒恢复读当前文件；关闭状态不落盘，恢复后回到带画像（决策W3/W6）。"""
    kb, captured = chat_env
    p = _write_default("恢复前")
    with TestClient(create_app(kb, profile=ProfileSource())) as client:
        cid = _new_conv(client)
        client.post(f"/api/chat/{cid}/profile", json={"enabled": False})
    p.write_text("恢复后", encoding="utf-8")
    with TestClient(create_app(kb, profile=ProfileSource())) as client:
        _chat(client, "续聊", conversation_id=cid)
        assert captured["agents"][-1].kwargs["project_instructions"].endswith("恢复后")
        assert _info(client, cid)["profile"]["state"] == "active"


# ───────── W7 落盘 ─────────


def test_session_snapshot_has_no_profile_text(chat_env) -> None:
    kb, _ = chat_env
    _write_default("## 回答方式\n画像标记词-落盘检查")
    with TestClient(create_app(kb, profile=ProfileSource())) as client:
        _new_conv(client)
    snaps = list((kb / ".agentao" / "sessions").glob("*.json"))
    assert snaps
    assert all("画像标记词-落盘检查" not in s.read_text(encoding="utf-8") for s in snaps)


# ───────── 启动信息与 CLI ─────────


def test_startup_line(kb, tmp_path_factory) -> None:
    assert startup_line(None, kb) is None
    assert startup_line(ProfileSource(), kb) is None  # 默认文件不存在
    _write_default()
    line = startup_line(ProfileSource(), kb)
    assert line.startswith(f"提问者画像：{DEFAULT_SHOWN}") and "agentao.log" in line
    _write_default("  ")
    assert startup_line(ProfileSource(), kb).startswith("⚠ 默认画像未使用")
    with pytest.raises(GuanlanError):  # 显式路径不合格：拒绝启动
        startup_line(ProfileSource(_outside(tmp_path_factory, "  ")), kb)


def test_serve_rejects_reader_with_profile(kb) -> None:
    from guanlan.web.server import serve

    with pytest.raises(GuanlanError):
        serve(kb, reader=True, profile=ProfileSource(), open_browser=False)


def test_serve_rejects_bad_explicit_profile_before_binding(kb, tmp_path_factory, monkeypatch) -> None:
    import guanlan.web.server as server

    monkeypatch.setattr(server.uvicorn, "run", lambda *a, **k: pytest.fail("不应起服务"))
    with pytest.raises(GuanlanError) as exc:
        server.serve(kb, profile=ProfileSource(_outside(tmp_path_factory, "  ")), open_browser=False)
    assert "空的" in str(exc.value)


@pytest.mark.parametrize(
    "argv",
    [["--reader", "--profile", "x.md"], ["--profile", "x.md", "--no-profile"]],
)
def test_cli_web_profile_conflicts(kb, monkeypatch, argv) -> None:
    monkeypatch.setattr("guanlan.web.serve", lambda *a, **k: pytest.fail("不应起服务"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["-C", str(kb), "web", "--no-browser", *argv])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "argv, want",
    [
        ([], ProfileSource()),
        (["--no-profile"], None),
        (["--reader"], None),
        (["--profile", "/somewhere/p.md"], ProfileSource(Path("/somewhere/p.md"))),
    ],
)
def test_cli_web_profile_source(kb, monkeypatch, argv, want) -> None:
    seen = {}
    monkeypatch.setattr("guanlan.web.serve", lambda *a, **k: seen.update(k) or 0)
    assert cli.main(["-C", str(kb), "web", "--no-browser", *argv]) == 0
    assert seen["profile"] == want
