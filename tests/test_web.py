"""P4 Web 宿主测试（见 docs/P4-Web宿主.md §11）。**两类 LLM 都打桩，不打真实 LLM。**

宿主测试用 `fastapi.testclient.TestClient`（进程内、无 socket）+ 临时知识库。缺 web extra
时整组 `pytest.importorskip("fastapi")` 跳过。
"""

import asyncio
import json
import socket
import sys
import time

import pytest

from guanlan.errors import GuanlanError
from guanlan.runtime import AgentRunResult

from conftest import make_runner, write_page

pytest.importorskip("fastapi")

from agentao.permissions import PermissionMode  # noqa: E402
from agentao.transport.events import AgentEvent, EventType  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from guanlan.web.app import STATIC_DIR, create_app  # noqa: E402


@pytest.fixture
def client(kb):
    """绑定到临时知识库的进程内 TestClient（无 socket）。"""
    with TestClient(create_app(kb)) as c:
        yield c


# ───────────────────────── 安全 / 形态（C1） ─────────────────────────


def test_static_index_bundled() -> None:
    """C1 不留空目录：占位 index.html 必须存在，否则 StaticFiles/打包落空。"""
    assert (STATIC_DIR / "index.html").is_file()


def test_index_served(kb) -> None:
    app = create_app(kb)
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert "观澜" in resp.text


def test_serve_binds_localhost_only(kb, monkeypatch) -> None:
    """serve 仅以 host=127.0.0.1、workers=1 起 uvicorn（决策P4-2/P4-5）。"""
    import guanlan.web.server as server

    captured: dict = {}

    def fake_run(app, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    rc = server.serve(kb, port=8799, open_browser=False)

    assert rc == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["workers"] == 1


def test_serve_never_opens_browser_when_disabled(kb, monkeypatch) -> None:
    import guanlan.web.server as server

    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kw: None)
    opened: list = []
    monkeypatch.setattr(server.webbrowser, "open", lambda url: opened.append(url))

    server.serve(kb, port=8798, open_browser=False)
    assert opened == []


def test_web_requires_kb_root(tmp_path) -> None:
    """未 init 的目录起服 → EXIT_USAGE（require_kb_root(writable=True) 前置）。"""
    from guanlan.cli import main

    rc = main(["-C", str(tmp_path), "web", "--no-browser"])
    assert rc == 1


def test_web_port_in_use(kb) -> None:
    """端口被占 → GuanlanError(EXIT_USAGE)，提示换端口。"""
    from guanlan.web.server import serve

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        with pytest.raises(GuanlanError) as excinfo:
            serve(kb, port=port, open_browser=False)
    assert excinfo.value.exit_code == 1


# ───────────────────────── 零 LLM 报告（C3） ─────────────────────────


def test_report_check_byte_aligned(client, kb) -> None:
    from guanlan.check import format_report, run_check

    write_page(kb, "wiki/concepts/Bad.md", body="引用断链 [[Ghost]]。")  # → 一条 wikilink.broken
    expected = format_report(run_check(kb / "wiki"), json_output=True)

    resp = client.get("/api/report/check")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.text == expected  # 字节级与 CLI --json 对齐（无尾换行、ensure_ascii=False）


def test_report_health_byte_aligned(client, kb) -> None:
    from guanlan.health import format_report, run_health

    write_page(kb, "wiki/entities/Stub.md", type="entity", body="")  # 桩页 → 一条 finding
    expected = format_report(run_health(kb / "wiki"), json_output=True)

    resp = client.get("/api/report/health")
    assert resp.status_code == 200
    assert resp.text == expected


def test_report_lint_byte_aligned(client, kb) -> None:
    from guanlan.lint import format_report, run_lint

    write_page(kb, "wiki/entities/Foo.md", type="entity", body="孤儿页正文内容足够长。")
    expected = format_report(run_lint(kb / "wiki"), json_output=True)

    resp = client.get("/api/report/lint")
    assert resp.status_code == 200
    assert resp.text == expected


def test_graph_builds_and_redirects(client, kb) -> None:
    write_page(kb, "wiki/entities/Foo.md", type="entity", body="see [[Bar]] 正文够长。")
    write_page(kb, "wiki/concepts/Bar.md", type="concept", body="目标页正文内容足够长。")

    resp = client.get("/graph", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/graph/graph.html"
    assert (kb / "graph" / "graph.json").is_file()
    assert (kb / "graph" / "graph.html").is_file()

    html = client.get("/graph/graph.html")
    assert html.status_code == 200
    assert "观澜" in html.text


def test_graph_json_only_redirects_to_json(client, kb) -> None:
    resp = client.get("/graph", params={"json_only": True}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/graph/graph.json"
    assert (kb / "graph" / "graph.json").is_file()
    assert not (kb / "graph" / "graph.html").is_file()  # json_only 跳过 html


def test_graph_static_404_before_build(client) -> None:
    assert client.get("/graph/graph.html").status_code == 404
    assert client.get("/graph/graph.json").status_code == 404


# ───────────────────────── 静态 / 浏览（C2） ─────────────────────────


def test_api_pages_excludes_config_and_groups_by_type(client, kb) -> None:
    write_page(kb, "wiki/entities/Foo.md", type="entity", body="一段实体描述正文内容。")
    write_page(kb, "wiki/concepts/Bar.md", type="concept", body="一段概念描述正文内容。")

    resp = client.get("/api/pages")
    assert resp.status_code == 200
    pages = resp.json()["pages"]
    paths = {p["path"] for p in pages}

    assert "wiki/entities/Foo.md" in paths
    assert "wiki/concepts/Bar.md" in paths
    # config 页（index/log/overview）排除。
    assert not any(p["path"].endswith(("index.md", "log.md", "overview.md")) for p in pages)
    types = {p["path"]: p["type"] for p in pages}
    assert types["wiki/entities/Foo.md"] == "entity"
    assert types["wiki/concepts/Bar.md"] == "concept"


def test_api_page_renders_meta_and_html(client, kb) -> None:
    write_page(kb, "wiki/entities/Foo.md", type="entity", body="正文里引用 [[Bar]]。")
    write_page(kb, "wiki/concepts/Bar.md", type="concept", body="目标页正文内容足够长。")

    resp = client.get("/api/page", params={"path": "wiki/entities/Foo.md"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["type"] == "entity"
    # [[Bar]] 解析到存在页 → 站内锚链（class=wikilink + data-page）。
    assert 'class="wikilink"' in data["html"]
    assert 'data-page="wiki/concepts/Bar.md"' in data["html"]


def test_api_page_broken_wikilink_greyed(client, kb) -> None:
    write_page(kb, "wiki/entities/Foo.md", type="entity", body="引用不存在的 [[Ghost]]。")
    resp = client.get("/api/page", params={"path": "wiki/entities/Foo.md"})
    assert resp.status_code == 200
    html = resp.json()["html"]
    assert "wikilink broken" in html
    assert "data-page" not in html  # 断链不可点。


def test_api_page_escapes_raw_html_xss(client, kb) -> None:
    """页面里的原始 HTML 被转义、不可执行（XSS 防御，决策P4-4）。"""
    write_page(
        kb,
        "wiki/entities/Evil.md",
        type="entity",
        body="正文夹带 <img src=x onerror=alert(1)> 与 <script>alert(2)</script>。",
    )
    resp = client.get("/api/page", params={"path": "wiki/entities/Evil.md"})
    assert resp.status_code == 200
    html = resp.json()["html"]
    # 关键：没有可执行的原始标签；payload 仅作为转义文本存在。
    assert "<img" not in html and "<script" not in html
    assert "&lt;img" in html and "&lt;script&gt;" in html


def test_api_page_tolerates_bad_frontmatter(client, kb) -> None:
    """坏/缺 frontmatter → meta=null 仍渲染正文（容错档，决策P3-8）。"""
    bad = kb / "wiki" / "entities" / "Bad.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("没有 frontmatter 的正文，直接成段。", encoding="utf-8")

    resp = client.get("/api/page", params={"path": "wiki/entities/Bad.md"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"] is None
    assert "正文" in data["html"]


def test_api_pages_survives_non_utf8_page(client, kb) -> None:
    """单张非 UTF-8 页不应让整张页面清单 500（load_page 容错 errors='replace'）。"""
    write_page(kb, "wiki/entities/Good.md", type="entity", body="正常 UTF-8 正文内容。")
    bad = kb / "wiki" / "entities" / "Gbk.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes("标题：观澜\n\n正文乱码".encode("gbk"))  # 非 UTF-8 字节

    resp = client.get("/api/pages")
    assert resp.status_code == 200
    assert any(p["path"] == "wiki/entities/Good.md" for p in resp.json()["pages"])

    # 坏字节页本身也能打开（不 500），坏字符被替换为 �。
    page = client.get("/api/page", params={"path": "wiki/entities/Gbk.md"})
    assert page.status_code == 200


@pytest.mark.parametrize(
    "evil",
    [
        "../../etc/passwd",
        "wiki/../../etc/passwd",
        "/etc/passwd",
        "../SCHEMA.md",  # 库内但 wiki/ 外（config）。
    ],
)
def test_api_page_path_traversal_rejected(client, evil) -> None:
    resp = client.get("/api/page", params={"path": evil})
    assert resp.status_code == 409


def test_api_page_missing_is_404(client) -> None:
    resp = client.get("/api/page", params={"path": "wiki/entities/Nope.md"})
    assert resp.status_code == 404


def test_api_raw_lists_only(client, kb) -> None:
    (kb / "raw" / "a.md").write_text("# A\n", encoding="utf-8")
    (kb / "raw" / "b.md").write_text("# B 长一点\n", encoding="utf-8")
    (kb / "raw" / "ignore.txt").write_text("not md\n", encoding="utf-8")

    resp = client.get("/api/raw")
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()["files"]}
    assert names == {"a.md", "b.md"}  # 只列 *.md
    assert all(isinstance(f["size"], int) for f in resp.json()["files"])


# ───────────────────────── ingest 写作业（C4） ─────────────────────────


def _put_raw(kb, name="doc.md") -> str:
    (kb / "raw" / name).write_text("# 资料\n一些内容。\n", encoding="utf-8")
    return f"raw/{name}"


def _wait_job(client, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["state"] == "done":
            return data
        time.sleep(0.02)
    raise AssertionError(f"作业 {job_id} 未在 {timeout}s 内完成")


def _ingest_once(kb, runner, target) -> dict:
    with TestClient(create_app(kb, runner=runner)) as client:
        resp = client.post("/api/ingest", json={"target": target})
        assert resp.status_code == 200
        return _wait_job(client, resp.json()["job_id"])


def test_ingest_job_success(kb) -> None:
    target = _put_raw(kb)
    runner = make_runner(lambda root: write_page(root, "wiki/concepts/New.md"))
    data = _ingest_once(kb, runner, target)
    assert data["kind"] == "ingest"
    assert data["state"] == "done"
    assert data["exit_code"] == 0  # EXIT_OK


def test_ingest_job_check_failure_is_3(kb) -> None:
    # 持续写出阻断性 frontmatter 违规（type 非法），自愈耗尽 → EXIT_CHECK_FAILED（断链只是警告，
    # 不会到 3，见 test_ingest_broken_link_is_warning_ok）。
    target = _put_raw(kb)

    def action(root):
        p = root / "wiki" / "concepts" / "Bad.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '---\ntitle: "T"\ntype: bogus\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n',
            encoding="utf-8",
        )

    data = _ingest_once(kb, make_runner(action), target)
    assert data["exit_code"] == 3  # EXIT_CHECK_FAILED


def test_ingest_job_raw_mutation_is_4(kb) -> None:
    target = _put_raw(kb)

    def action(root):
        (root / "raw" / "doc.md").write_text("被 agent 改了", encoding="utf-8")  # 动 raw/

    data = _ingest_once(kb, make_runner(action), target)
    assert data["exit_code"] == 4  # EXIT_RAW_MUTATED


def test_ingest_job_agent_error_is_5(kb) -> None:
    target = _put_raw(kb)
    runner = make_runner(None, ok=False, final_text="boom", error_type="runtime_error")
    data = _ingest_once(kb, runner, target)
    assert data["exit_code"] == 5  # EXIT_AGENT_ERROR


def test_two_ingests_run_serially(kb) -> None:
    """两个 ingest FIFO 串行完成，raw/ 快照不互踩（决策P4-5）。"""
    _put_raw(kb, "a.md")
    _put_raw(kb, "b.md")
    order: list[str] = []

    def runner(prompt, **kwargs):
        n = len(order) + 1
        order.append(prompt)
        write_page(kwargs["working_directory"], f"wiki/concepts/N{n}.md")
        return AgentRunResult(ok=True, final_text="done")

    with TestClient(create_app(kb, runner=runner)) as client:
        id_a = client.post("/api/ingest", json={"target": "raw/a.md"}).json()["job_id"]
        id_b = client.post("/api/ingest", json={"target": "raw/b.md"}).json()["job_id"]
        data_a = _wait_job(client, id_a)
        data_b = _wait_job(client, id_b)

    assert data_a["exit_code"] == 0
    assert data_b["exit_code"] == 0
    assert len(order) == 2  # 两个作业都跑了（单 worker 串行）


def test_ingest_invalid_body_is_422(client) -> None:
    assert client.post("/api/ingest", json={}).status_code == 422  # 缺 target


def test_unknown_job_is_404(client) -> None:
    assert client.get("/api/jobs/999999").status_code == 404


# ───────────────────────── 只读多轮 chat（C5） ─────────────────────────


class _Recorder:
    """记录任意方法调用（permission_engine / tool_runner / skill_manager 桩）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def rec(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return rec


class _FakeAgent:
    """打桩 agent：arun 把 user/assistant 入 messages，并**从工作线程**经传入的 transport
    发 LLM_TEXT（镜像真 arun 的 run_in_executor 线程模型，逼出 call_soon_threadsafe 桥）。"""

    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.transport = kwargs["transport"]  # 构造期传入的真 transport（token 唯一活线）
        self.messages: list[tuple[str, str]] = []
        self.permission_engine = _Recorder()
        self.tool_runner = _Recorder()
        self.skill_manager = _Recorder()
        self.closed = False

    async def arun(self, msg: str, **_kw) -> str:
        self.messages.append(("user", msg))
        n = sum(1 for role, _ in self.messages if role == "user")
        answer = f"#{n} 回应：{msg}"  # 含轮次 → 第二轮答案体现累积历史

        loop = asyncio.get_running_loop()

        def work() -> None:  # 在线程池线程发事件（镜像真 arun）
            for ch in answer:
                self.transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": ch}))

        await loop.run_in_executor(None, work)
        self.messages.append(("assistant", answer))
        return answer

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def chat_client(kb, monkeypatch):
    """注入 fake build_from_environment + no-op ensure_skill_available；捕获 kwargs/agents。"""
    import guanlan.web.chat as chat_mod

    captured = {"kwargs": [], "agents": []}
    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)

    def fake_bfe(**kwargs):
        agent = _FakeAgent(kwargs)
        captured["kwargs"].append(kwargs)
        captured["agents"].append(agent)
        return agent

    monkeypatch.setattr(chat_mod, "build_from_environment", fake_bfe)
    with TestClient(create_app(kb)) as c:
        yield c, captured


def _chat(client, message, conversation_id=None, model=None):
    """发一轮 chat，解析 SSE 流；返回 (tokens, done, error)。"""
    body = {"message": message}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    if model is not None:
        body["model"] = model

    tokens: list[str] = []
    done = error = None
    with client.stream("POST", "/api/chat", json=body) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        event = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
                if event == "token":
                    tokens.append(data)
                elif event == "done":
                    done = data
                elif event == "error":
                    error = data
    return tokens, done, error


def test_chat_new_conversation_streams_and_returns_id(chat_client) -> None:
    client, _ = chat_client
    tokens, done, error = _chat(client, "第一问")
    assert error is None
    assert done is not None
    assert done["conversation_id"]  # 新建会话回传 id
    # token 从工作线程流出，经 call_soon_threadsafe 桥回、完整到达（拼接 == 答案）。
    assert "".join(tokens) == done["answer"]
    assert done["answer"].startswith("#1")


def test_chat_multiturn_accumulates_context(chat_client) -> None:
    client, captured = chat_client
    _, done1, _ = _chat(client, "第一问")
    cid = done1["conversation_id"]
    _, done2, _ = _chat(client, "第二问", conversation_id=cid)

    assert done2["answer"].startswith("#2")  # 第二轮看到累积历史
    assert len(captured["agents"]) == 1  # 同一会话只建一个 agent
    assert len(captured["agents"][0].messages) == 4  # 2 user + 2 assistant 跨轮累积


def test_chat_construction_contract(chat_client, kb) -> None:
    client, captured = chat_client
    _chat(client, "问")

    kwargs = captured["kwargs"][0]
    assert kwargs["working_directory"] == kb
    assert "transport" in kwargs  # token 靠构造期 transport，非事后赋 llm_text_callback
    assert "logger" in kwargs  # 自带 logger（不落 <wd>/agentao.log）
    assert "permission_mode" not in kwargs  # 不传该形参（否则 TypeError）
    assert "model" not in kwargs  # 省略 --model → 无 model 键（绝非 model=None）

    agent = captured["agents"][0]
    # 只读姿态两点同步置位。
    assert any(c[0] == "set_mode" and c[1] == (PermissionMode.READ_ONLY,) for c in agent.permission_engine.calls)
    assert any(c[0] == "set_readonly_mode" and c[1] == (True,) for c in agent.tool_runner.calls)
    # guanlan-wiki 被激活。
    assert any(c[0] == "activate_skill" and c[1][0] == "guanlan-wiki" for c in agent.skill_manager.calls)


def test_chat_model_passed_only_when_given(chat_client) -> None:
    client, captured = chat_client
    _chat(client, "问", model="gpt-test")
    assert captured["kwargs"][0]["model"] == "gpt-test"


def test_chat_unknown_conversation_404(chat_client) -> None:
    client, _ = chat_client
    resp = client.post("/api/chat", json={"message": "x", "conversation_id": "999"})
    assert resp.status_code == 404


def test_chat_error_event_on_failure(kb, monkeypatch) -> None:
    import guanlan.web.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)

    class _BoomAgent(_FakeAgent):
        async def arun(self, msg, **_kw):
            raise RuntimeError("炸了")

    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _BoomAgent(kw))
    with TestClient(create_app(kb)) as client:
        tokens, done, error = _chat(client, "问")
    assert done is None
    assert error is not None and "炸了" in error["message"]


def test_chat_invalid_body_422(client) -> None:
    assert client.post("/api/chat", json={}).status_code == 422  # 缺 message


def test_same_conversation_turns_serialized(kb, monkeypatch) -> None:
    """同一会话两轮被 asyncio.Lock 串行（start/end 不交错）。"""
    import guanlan.web.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)
    events: list[str] = []

    class _SlowAgent(_FakeAgent):
        async def arun(self, msg, **_kw):
            events.append(f"start:{msg}")
            await asyncio.sleep(0.05)
            events.append(f"end:{msg}")
            return "ok"

    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _SlowAgent(kw))

    async def main() -> None:
        store = chat_mod.ConversationStore(kb, None)
        conv = store.create()
        await asyncio.gather(
            conv.turn("A", lambda k, d: None),
            conv.turn("B", lambda k, d: None),
        )

    asyncio.run(main())
    # 串行 → 一轮的 end 必在下一轮 start 之前。
    assert events[0].startswith("start") and events[1].startswith("end")
    assert events[2].startswith("start") and events[3].startswith("end")


def test_conversation_store_concurrent_create_no_collision(kb, monkeypatch) -> None:
    """并发 create（经线程池）id 不撞、会话不被覆盖（threading.Lock 护 id 分配 + 字典写）。"""
    import threading

    import guanlan.web.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)
    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _FakeAgent(kw))

    store = chat_mod.ConversationStore(kb, None)
    ids: list[str] = []
    ids_lock = threading.Lock()

    def worker() -> None:
        conv = store.create()
        with ids_lock:
            ids.append(conv.id)

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 30
    assert len(set(ids)) == 30  # 无 id 碰撞
    assert len(store.list()) == 30  # 无会话被覆盖丢失


def test_turn_on_deleted_conversation_refused(kb, monkeypatch) -> None:
    """已删除（closed）会话拒绝排队 turn，不在已关闭 agent 上跑（决策P4-8 删除竞态）。"""
    import guanlan.web.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)
    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _FakeAgent(kw))

    async def main() -> None:
        store = chat_mod.ConversationStore(kb, None)
        conv = store.create()
        store.delete(conv.id)  # close → conv.closed = True
        with pytest.raises(RuntimeError):
            await conv.turn("x", lambda k, d: None)

    asyncio.run(main())


def test_conversation_cap_is_hard(kb, monkeypatch) -> None:
    """达上限后拒新建（硬上限，决策P4-8）。"""
    import guanlan.web.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)
    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _FakeAgent(kw))
    monkeypatch.setattr(chat_mod, "MAX_CONVERSATIONS", 2)

    store = chat_mod.ConversationStore(kb, None)
    store.create()
    store.create()
    with pytest.raises(RuntimeError):
        store.create()


def test_delete_conversation(chat_client) -> None:
    client, _ = chat_client
    _, done, _ = _chat(client, "问")
    cid = done["conversation_id"]

    assert any(c["id"] == cid for c in client.get("/api/conversations").json()["conversations"])
    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert client.delete(f"/api/conversations/{cid}").status_code == 404  # 已丢


def test_chat_does_not_persist_session(chat_client, monkeypatch) -> None:
    """会话仅内存：不调 persist/load_session（决策P4-8）。"""
    import agentao.embedding.sessions as sess

    def _boom(*_a, **_k):
        raise AssertionError("不应落盘会话")

    monkeypatch.setattr(sess, "save_session", _boom, raising=False)
    monkeypatch.setattr(sess, "load_session", _boom, raising=False)

    client, _ = chat_client
    _, done, error = _chat(client, "问")
    assert error is None and done is not None


def test_absent_endpoints(client) -> None:
    """范围红线：无 /api/query、无 POST /api/raw、无写作业 SSE 订阅端点（§10）。"""
    assert client.get("/api/query").status_code == 404
    assert client.post("/api/query", json={"question": "x"}).status_code in (404, 405)
    assert client.post("/api/raw", json={}).status_code in (404, 405)
    assert client.get("/api/jobs/1/events").status_code == 404


@pytest.mark.parametrize("bad_port", [99999, -1, 0])
def test_web_invalid_port(kb, bad_port) -> None:
    """范围外端口 → GuanlanError(EXIT_USAGE)，不向用户吐 traceback（OverflowError 被前置校验拦下）。"""
    from guanlan.web.server import serve

    with pytest.raises(GuanlanError) as excinfo:
        serve(kb, port=bad_port, open_browser=False)
    assert excinfo.value.exit_code == 1


def test_web_missing_extra_degrades(kb, monkeypatch, capsys) -> None:
    """缺 web extra（fastapi 导入失败）→ EXIT_USAGE 并引导 `pip install 'guanlan[web]'`。"""
    from guanlan.cli import main

    # 清掉已缓存的 web 子模块，令重新 import 时命中被打桩为不可用的 fastapi。
    for name in list(sys.modules):
        if name.startswith("guanlan.web"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "fastapi", None)  # `import fastapi` → ImportError

    rc = main(["-C", str(kb), "web", "--no-browser"])
    assert rc == 1
    assert "guanlan[web]" in capsys.readouterr().err
