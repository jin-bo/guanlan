"""P4 Web 宿主测试（见 docs/P4-Web宿主.md §11）。**两类 LLM 都打桩，不打真实 LLM。**

宿主测试用 `fastapi.testclient.TestClient`（进程内、无 socket）+ 临时知识库。缺 web extra
时整组 `pytest.importorskip("fastapi")` 跳过。
"""

import asyncio
import json
import socket
import sys
import threading
import time
import uuid

import pytest

from guanlan.errors import GuanlanError
from guanlan.runtime import AgentRunResult

from conftest import make_runner, write_page

pytest.importorskip("fastapi")

from agentao.cancellation import AgentCancelledError  # noqa: E402
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
    # 前端引用随包静态资源（C6）。
    assert "/static/app.js" in resp.text and "/static/app.css" in resp.text


def test_static_assets_served(client) -> None:
    """随包前端资源命中（C6：app.js / app.css）。"""
    js = client.get("/static/app.js")
    css = client.get("/static/app.css")
    assert js.status_code == 200 and "fetch" in js.text
    assert css.status_code == 200 and "--lan-ripple" in css.text  # 观澜配色变量


def test_static_assets_bundled() -> None:
    for name in ("index.html", "app.js", "app.css", "logo.png"):
        assert (STATIC_DIR / name).is_file()


def test_logo_served_and_referenced(client) -> None:
    """观澜图标随包、可经 /static/logo.png 取到，且首页用作 favicon + 顶栏品牌标记。"""
    resp = client.get("/static/logo.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] in ("image/png", "image/x-png")
    index = client.get("/").text
    assert 'rel="icon"' in index and "/static/logo.png" in index
    assert 'class="brand-icon"' in index


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


def test_unknown_report_name_404(client) -> None:
    """表驱动 /api/report/{name}：未知报告名 → 404（白名单，不是 500/任意执行）。"""
    assert client.get("/api/report/check").status_code == 200
    assert client.get("/api/report/bogus").status_code == 404


def test_graph_file_whitelist_404(client, kb) -> None:
    """/graph/{filename} 限死 graph.html/json 白名单：未知名 → 404，挡穿越（无 ../ 逃逸）。"""
    client.get("/graph")  # 先建，确保白名单内的文件确实存在
    assert client.get("/graph/graph.html").status_code == 200
    assert client.get("/graph/bogus.txt").status_code == 404
    # 穿越式文件名不在白名单 → 404（且 . 段会被 starlette 规整，绝不读到 graph/ 外）
    assert client.get("/graph/graph.html.bak").status_code == 404


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


def test_api_page_neutralizes_dangerous_link(client, kb) -> None:
    """markdown 链接的 javascript:/data: 协议被中和（纵深防御 XSS）。"""
    write_page(
        kb,
        "wiki/entities/Link.md",
        type="entity",
        body="点 [危险](javascript:fetch('/api/raw')) 与 [正常](https://example.com)。",
    )
    resp = client.get("/api/page", params={"path": "wiki/entities/Link.md"})
    html = resp.json()["html"]
    assert "javascript:" not in html  # 危险协议失活
    assert 'href="#"' in html  # 被改写为锚点
    assert "https://example.com" in html  # 安全链接保留


def test_render_markdown_for_chat() -> None:
    """对话答案的 markdown 渲染：富排版 + 安全 + [[页]] 解析（给 wiki）。"""
    from guanlan.web.render import render_markdown

    html = render_markdown("# 标题\n\n- 一项\n- 两项\n\n正文 `代码` 与 <script>x</script>。")
    assert "<h1>" in html and "<ul>" in html and "<code>" in html
    assert "<script" not in html and "&lt;script&gt;" in html  # 原始 HTML 转义


def test_render_markdown_wikilink_resolves(client, kb) -> None:
    from guanlan.web.render import render_markdown

    write_page(kb, "wiki/entities/Foo.md", type="entity", body="正文足够长的内容。")
    html = render_markdown("见 [[Foo]] 与 [[Ghost]]。", kb / "wiki")
    assert 'data-page="wiki/entities/Foo.md"' in html  # 解析到存在页
    assert "wikilink broken" in html  # 不存在 → 标灰


def test_render_markdown_code_path_linkifies_source_citation(client, kb) -> None:
    """兜底：源出处被写成【路径+反引号】的行内 code，若精确解析到现有页 → 转 wikilink。"""
    from guanlan.web.render import render_markdown

    write_page(kb, "wiki/sources/s13-注意力机制.md", type="source", body="正文足够长的内容。")
    wiki = kb / "wiki"

    # 路径+反引号、裸 stem+反引号 → 都联链到同一页，显示干净 stem（去 sources/ 与 .md）
    for text in ("引自 `wiki/sources/s13-注意力机制.md`", "见 `s13-注意力机制`"):
        html = render_markdown(text, wiki)
        assert 'data-page="wiki/sources/s13-注意力机制.md"' in html
        assert ">s13-注意力机制</a>" in html
        assert "<code>" not in html  # 已从 code 转成 a

    # 含空格的合法页名也应成链（不能被"有空格就跳过"误杀）
    write_page(kb, "wiki/concepts/Smart Tools 模块.md", type="concept", body="正文足够长。")
    for text in ("见 `Smart Tools 模块`", "见 `wiki/concepts/Smart Tools 模块.md`"):
        html = render_markdown(text, wiki)
        assert 'data-page="wiki/concepts/Smart Tools 模块.md"' in html
        assert "<code>" not in html

    # 解析不到的普通代码 / 命令（含某页末段但整体非忠实引用）/ 围栏代码块 → 保持字面 code
    assert "<code>git status</code>" in render_markdown("跑 `git status`", wiki)
    assert "<code>cat wiki/sources/s13-注意力机制.md</code>" in render_markdown(
        "`cat wiki/sources/s13-注意力机制.md`", wiki
    )
    fenced = render_markdown("```\nwiki/sources/s13-注意力机制.md\n```", wiki)
    assert "wikilink" not in fenced  # 围栏代码块字面保留（决策P4-3）

    # 不给 wiki（无解析集）→ 行内 code 原样，不联链
    assert "<code>" in render_markdown("引自 `wiki/sources/s13-注意力机制.md`")

    # code 当 markdown 链接文字 → 不得转成嵌套锚（保留外层 [..](url)，内层留 code）
    nested = render_markdown("[`wiki/sources/s13-注意力机制.md`](https://example.com)", wiki)
    assert nested.count("<a ") == 1  # 只有外层一个锚，无嵌套
    assert "<code>" in nested and 'href="https://example.com"' in nested


def test_render_markdown_code_wrapped_wikilink_is_tolerated(client, kb) -> None:
    """兜底：模型把 `[[...]]` 套进行内 code 时，整段忠实 wikilink 仍按站内链接渲染。"""
    from guanlan.web.render import render_markdown

    write_page(kb, "wiki/entities/Foo.md", type="entity", body="正文足够长的内容。")
    wiki = kb / "wiki"

    html = render_markdown("见 `[[Foo]]`、`[[Foo|别名]]`、`[[Foo#要点]]` 与 `[[Ghost]]`。", wiki)
    assert html.count('data-page="wiki/entities/Foo.md"') == 3
    assert ">Foo</a>" in html
    assert ">别名</a>" in html
    assert "wikilink broken" in html
    assert ">Ghost</span>" in html
    assert "<code>[[Foo]]</code>" not in html

    # 只有整段 code 恰好是 wikilink 才兜底；命令/代码块仍保持字面语义。
    assert "<code>cat [[Foo]]</code>" in render_markdown("`cat [[Foo]]`", wiki)
    fenced = render_markdown("```\n[[Foo]]\n```", wiki)
    assert "wikilink" not in fenced


def test_configure_agent_log_writes_and_is_idempotent(kb) -> None:
    """会话日志像 CLI 那样落 <kb>/agentao.log；重复配置不重挂 handler（不会把每行写多遍）。"""
    from guanlan.web import chat as chatmod

    target = (kb / "agentao.log").resolve()
    mine = lambda: [  # noqa: E731 — 本会话挂在共享 logger 上、指向本 kb 的 handler
        h for h in chatmod._logger.handlers
        if getattr(h, "baseFilename", None) == str(target)
    ]
    assert chatmod.configure_agent_log(kb) == target
    try:
        assert len(mine()) == 1  # 挂了且仅一个
        chatmod._logger.info("hello-agentao-log")
        for h in mine():
            h.flush()
        assert "hello-agentao-log" in target.read_text(encoding="utf-8")
        chatmod.configure_agent_log(kb)  # 幂等：再配置不新增 handler
        assert len(mine()) == 1
    finally:  # 清理全局 logger 状态，避免泄漏到其它测试
        for h in mine():
            chatmod._logger.removeHandler(h)
            h.close()
        chatmod._agent_log_paths.discard(str(target))


def test_is_safe_url_strips_control_chars() -> None:
    """控制符不能绕过协议白名单（浏览器会先剥控制符再导航）。"""
    from guanlan.web.render import _is_safe_url

    assert _is_safe_url("https://example.com") is True
    assert _is_safe_url("/wiki/x.md") is True
    assert _is_safe_url("#anchor") is True
    assert _is_safe_url("javascript:alert(1)") is False
    assert _is_safe_url("java\tscript:alert(1)") is False
    assert _is_safe_url("java\nscript:alert(1)") is False
    assert _is_safe_url("  javascript:alert(1)") is False
    assert _is_safe_url("data:text/html,x") is False
    # HTML 实体编码绕过（浏览器导航前会解码）。
    assert _is_safe_url("&#106;avascript:alert(1)") is False
    assert _is_safe_url("java&#x09;script:alert(1)") is False
    assert _is_safe_url("https://x?a=1&b=2") is True  # 合法 query 不误伤


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


# ───────────────────────── 投喂 POST /api/raw（P4.1 C1） ─────────────────────────


def test_raw_write_happy_path(client, kb) -> None:
    """投喂正常路：返回 saved/bytes（同步、无需轮询）；盘上字节 == content；GET /api/raw 列出。"""
    content = "# 新素材\n一些**正文**与 [[页]] 链接。\n"
    resp = client.post("/api/raw", json={"name": "我的笔记", "content": content})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"saved": "raw/我的笔记.md", "bytes": len(content.encode("utf-8"))}
    # 盘上字节与 content **逐字节相等**（UTF-8 原样，不渲染、不重写 wikilink）。
    assert (kb / "raw" / "我的笔记.md").read_bytes() == content.encode("utf-8")
    names = {f["name"] for f in client.get("/api/raw").json()["files"]}
    assert "我的笔记.md" in names


def test_raw_write_strips_path_traversal(client, kb) -> None:
    """`name` 含目录/穿越成分 → 剥成 basename 落 raw/ 内，绝不越界。"""
    resp = client.post("/api/raw", json={"name": "../../etc/passwd", "content": "x\n"})
    assert resp.status_code == 200
    saved = resp.json()["saved"]
    assert saved == "raw/passwd.md"
    target = (kb / saved).resolve()
    target.relative_to((kb / "raw").resolve())  # 断言落点在 raw/ 内（越界会抛）
    assert not (kb / "etc").exists()  # 没写到库外


@pytest.mark.parametrize("body", [{"name": "", "content": "x"}, {"name": "n", "content": ""}, {"name": "n", "content": "   \n  "}])
def test_raw_write_empty_is_400(client, body) -> None:
    assert client.post("/api/raw", json=body).status_code == 400


def test_raw_write_too_large_is_400(client, kb) -> None:
    from guanlan.web.app import MAX_RAW_BYTES

    resp = client.post("/api/raw", json={"name": "big", "content": "a" * (MAX_RAW_BYTES + 1)})
    assert resp.status_code == 400
    assert not (kb / "raw" / "big.md").exists()  # 超限不落盘


def test_raw_confusable_normalization(client, kb) -> None:
    """文件名混淆字符规范化：弯引号删除、中文破折号→单 `-`、空格→`-`、全角折叠；正文原样保真。"""
    # 弯引号 + 中文破折号 + 空格：落点 raw/深度学习-导论.md。正文含同类字符必须逐字节保真。
    content = "正文里的“弯引号”与 —— 破折号 全保留。\n"
    resp = client.post("/api/raw", json={"name": "“深度学习” —— 导论", "content": content})
    assert resp.status_code == 200
    assert resp.json()["saved"] == "raw/深度学习-导论.md"
    assert (kb / "raw" / "深度学习-导论.md").read_bytes() == content.encode("utf-8")

    # 全角字母 + 空格 → NFKC + 空格转 `-`。
    r2 = client.post("/api/raw", json={"name": "ＡＩ 学习 笔记", "content": "x\n"})
    assert r2.json()["saved"] == "raw/AI-学习-笔记.md"

    # NBSP / 全角空格同样收敛为 `-`（不留空白、不留 `-` 串）。
    r3 = client.post("/api/raw", json={"name": "甲 乙　丙", "content": "x\n"})
    assert r3.json()["saved"] == "raw/甲-乙-丙.md"


@pytest.mark.parametrize("bad", ["a\x00b", "ctrl\x07here", "vt\x0bx"])
def test_raw_text_admission_rejects_binary(client, kb, bad) -> None:
    """含 NUL / C0 控制字符（非 \\t\\n\\r）→ 400 且盘上不留文件。"""
    resp = client.post("/api/raw", json={"name": "bin", "content": bad})
    assert resp.status_code == 400
    assert not (kb / "raw" / "bin.md").exists()


def test_raw_text_admission_allows_tab_newline(client, kb) -> None:
    content = "第一行\t带制表\r\n第二行\n"
    resp = client.post("/api/raw", json={"name": "ok", "content": content})
    assert resp.status_code == 200
    assert (kb / "raw" / "ok.md").read_bytes() == content.encode("utf-8")


def test_raw_reject_extensions(client, kb) -> None:
    """带点标题补 `.md`、不误杀；已知非-md 扩展 → 400（大小写不敏感 + 全角点不漏）。"""
    # 带点标题视作普通标题，补 .md（不被 suffix 误判 400）。
    assert client.post("/api/raw", json={"name": "GPT-4.5 笔记", "content": "x\n"}).json()["saved"] == "raw/GPT-4.5-笔记.md"
    assert client.post("/api/raw", json={"name": "v1.2", "content": "x\n"}).json()["saved"] == "raw/v1.2.md"
    # X.MD：视作已带后缀，归一小写、不叠补。
    assert client.post("/api/raw", json={"name": "X.MD", "content": "x\n"}).json()["saved"] == "raw/X.md"
    # 拒绝列表内（大小写不敏感）→ 400。
    for bad in ("x.txt", "y.pdf", "X.PDF", "foo.Docx"):
        assert client.post("/api/raw", json={"name": bad, "content": "x\n"}).status_code == 400
    # 全角点不漏：x．PDF → NFKC → x.PDF → suffix .pdf → 400（不漏成 x.PDF.md）。
    assert client.post("/api/raw", json={"name": "x．PDF", "content": "x\n"}).status_code == 400


def test_raw_suffix_trailing_space_and_leading_dot(client, kb) -> None:
    """尾随空白不破坏 `.md` 归一；首尾 `.` 剥净，杜绝隐藏文件 / 双后缀。"""
    # 尾随空白：'foo.MD ' 须归一为 'foo.md'（剥空白前 suffix 是 '.MD '、逃过 `.md` 归一，会落 foo.MD.md）。
    assert client.post("/api/raw", json={"name": "foo.MD ", "content": "x\n"}).json()["saved"] == "raw/foo.md"
    # 尾随空白 + 带点标题：'GPT-4.5 笔记 ' 仍补 .md、不残留空白。
    assert client.post("/api/raw", json={"name": "GPT-4.5 笔记 ", "content": "x\n"}).json()["saved"] == "raw/GPT-4.5-笔记.md"
    # 纯扩展名 / 首点：'.md' 不落隐藏双后缀 '.md.md'；落点 basename 不以 `.` 开头。
    saved = client.post("/api/raw", json={"name": ".md", "content": "x\n"}).json()["saved"]
    assert saved == "raw/md.md"
    assert not saved.split("/")[-1].startswith(".")


def test_raw_overwrite_semantics(client, kb) -> None:
    """已存在无 overwrite → 409 且原内容不变；overwrite=true → 覆盖成功。"""
    (kb / "raw" / "dup.md").write_text("旧内容\n", encoding="utf-8")
    r1 = client.post("/api/raw", json={"name": "dup", "content": "新内容\n"})
    assert r1.status_code == 409
    assert (kb / "raw" / "dup.md").read_text(encoding="utf-8") == "旧内容\n"  # 未被改

    r2 = client.post("/api/raw", json={"name": "dup", "content": "新内容\n", "overwrite": True})
    assert r2.status_code == 200
    assert (kb / "raw" / "dup.md").read_text(encoding="utf-8") == "新内容\n"


def test_atomic_write_raw_worker_recheck(kb) -> None:
    """worker turn 内复检：已存在 + 无 overwrite → EXIT_USAGE（端点据此转 409），不覆盖。"""
    from guanlan.errors import EXIT_OK, EXIT_USAGE
    from guanlan.web.app import _atomic_write_raw

    target = kb / "raw" / "x.md"
    target.write_text("原\n", encoding="utf-8")
    assert _atomic_write_raw(target, "新\n", overwrite=False) == EXIT_USAGE
    assert target.read_text(encoding="utf-8") == "原\n"  # 没动
    assert _atomic_write_raw(target, "新\n", overwrite=True) == EXIT_OK
    assert target.read_text(encoding="utf-8") == "新\n"


def test_raw_exit_code_http_split(kb, monkeypatch) -> None:
    """退出码→HTTP 分流（对齐 §2）：worker EXIT_USAGE→409、落盘抛错→500（非 409）。"""
    from guanlan.errors import EXIT_USAGE
    from guanlan.web import app as app_mod

    # ① worker 返回 EXIT_USAGE（撞同名复检）→ 409。
    monkeypatch.setattr(app_mod, "_atomic_write_raw", lambda *a, **k: EXIT_USAGE)
    with TestClient(create_app(kb)) as client:
        assert client.post("/api/raw", json={"name": "a", "content": "x\n"}).status_code == 409

    # ② worker 落盘抛 OSError（被 _run 归一为 EXIT_AGENT_ERROR）→ 500，且带原因。
    def boom(*a, **k):
        raise OSError("磁盘满")

    monkeypatch.setattr(app_mod, "_atomic_write_raw", boom)
    with TestClient(create_app(kb)) as client:
        resp = client.post("/api/raw", json={"name": "b", "content": "x\n"})
    assert resp.status_code == 500
    assert "磁盘满" in resp.json()["detail"]


def test_jobqueue_submit_and_wait_is_fifo_behind_prior(kb) -> None:
    """JobQueue：submit_and_wait 排在前序作业之后（FIFO），完成后 done_event 唤醒、Job 字段完整。"""
    from guanlan.web.jobs import JobQueue

    jq = JobQueue()
    order: list[str] = []
    gate = threading.Event()

    def slow() -> int:
        order.append("ingest-start")
        gate.wait(timeout=3)
        order.append("ingest-end")
        return 0

    prior_id = jq.enqueue("ingest", slow)  # 占住 worker
    result: dict = {}

    def submit() -> None:
        result["job"] = jq.submit_and_wait("raw_write", lambda: order.append("raw") or 0)

    t = threading.Thread(target=submit)
    t.start()
    time.sleep(0.1)
    assert order == ["ingest-start"]  # 投喂被挡在在飞 ingest 之后（同一 FIFO worker）
    gate.set()
    t.join(timeout=3)
    assert order == ["ingest-start", "ingest-end", "raw"]  # FIFO：投喂在 ingest 之后才落盘
    assert result["job"].exit_code == 0
    assert jq.get_job(prior_id).exit_code == 0


def test_raw_write_serial_does_not_collide_with_ingest(kb) -> None:
    """端点级：投喂排在在飞 ingest 之后落盘，该 ingest **不被冤判** EXIT_RAW_MUTATED。"""
    _put_raw(kb, "src.md")
    gate = threading.Event()
    order: list[str] = []

    def runner(prompt, **kwargs):
        order.append("ingest")
        gate.wait(timeout=3)  # 卡住，模拟在飞 ingest（其 raw/ 快照窗口张开）
        write_page(kwargs["working_directory"], "wiki/concepts/N.md")
        return AgentRunResult(ok=True, final_text="done")

    with TestClient(create_app(kb, runner=runner)) as client:
        ing_id = client.post("/api/ingest", json={"target": "raw/src.md"}).json()["job_id"]
        result: dict = {}

        def feed() -> None:
            result["resp"] = client.post("/api/raw", json={"name": "投喂源", "content": "新源\n"})

        t = threading.Thread(target=feed)
        t.start()
        time.sleep(0.1)
        assert not (kb / "raw" / "投喂源.md").exists()  # 投喂尚未落盘（被 ingest 挡住）
        assert order == ["ingest"]
        gate.set()  # 放行 ingest
        t.join(timeout=3)
        ing_job = _wait_job(client, ing_id)

    assert result["resp"].status_code == 200
    assert (kb / "raw" / "投喂源.md").exists()  # 投喂在 ingest 之后落盘
    assert ing_job["exit_code"] == 0  # 投喂没落进 ingest 快照窗口（否则会是 4=EXIT_RAW_MUTATED）


# ───────────────────────── 只读多轮 chat（C5） ─────────────────────────


class _Recorder:
    """记录任意方法调用（permission_engine / tool_runner / skill_manager 桩）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def rec(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return rec


class _FakeSkillManager:
    """打桩 skill_manager：记录 activate_skill 调用，并让 get_active_skills 反映已激活的 skill
    （P4.2 `_save` 镜像 cli/session.py 取 `get_active_skills().keys()` 落 active_skills）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._active: dict[str, dict] = {}

    def activate_skill(self, name, *args, **kwargs):
        self.calls.append(("activate_skill", (name, *args), kwargs))
        self._active[name] = {}

    def get_active_skills(self) -> dict:
        return dict(self._active)

    def list_available_skills(self) -> list[str]:
        # guanlan-wiki（构造期被激活）+ 一个未激活技能：让 /skills 的 available/active 区分可断言。
        return ["guanlan-wiki", "other-skill"]

    def get_skill_description(self, name: str) -> str:
        return f"desc:{name}"


_UNSET = object()  # 哨兵：区分「未定义 is_read_only 属性」（走静态/unknown 路）与「定义为 False」。


class _FakeTool:
    """打桩工具（喂 `/tools` 自省）：`is_read_only` 缺省**不设属性**（`_UNSET`）以触发
    `_blocked_in_readonly` 的静态名/unknown 分支；显式 True/False 则走 agentao 元数据分支。"""

    def __init__(self, name, *, description="d", requires_confirmation=False, is_read_only=_UNSET):
        self.name = name
        self.description = description
        self.requires_confirmation = requires_confirmation
        if is_read_only is not _UNSET:
            self.is_read_only = is_read_only


# 覆盖 _blocked_in_readonly 三条路：元数据（read_file=只读→False / write_file=非只读→True）、
# 已知名静态（replace→True / search_file_content→False，**无** is_read_only 属性）、未知（→ "unknown"）。
def _default_tools() -> list[_FakeTool]:
    return [
        _FakeTool("write_file", is_read_only=False),  # 元数据：只读下被拦
        _FakeTool("read_file", is_read_only=True),  # 元数据：只读放行
        _FakeTool("replace"),  # 无元数据 + 已知写名 → 静态 True
        _FakeTool("search_file_content"),  # 无元数据 + 已知读名 → 静态 False
        _FakeTool("mystery_tool"),  # 无元数据 + 未知名 → "unknown"
    ]


class _FakeToolRegistry:
    def __init__(self, tools) -> None:
        self._tools = tools

    def list_tools(self) -> list:
        return list(self._tools)

    def to_openai_format(self, plan_mode=False) -> list:
        # 非空 schema：让 _context_stats 的 tools 分项被计入（验 tools 传进了 breakdown）。
        return [{"type": "function", "function": {"name": t.name}} for t in self._tools]


class _FakeContextManager:
    """打桩 context_manager：估算口径镜像真 agentao——breakdown 区分 system/tools 是否计入，
    headline（get_usage_stats）走 messages-only。逼出「context 取数须含 system+tools」的修复。"""

    def __init__(self) -> None:
        self.stats_calls: list[list] = []

    def estimate_tokens_breakdown(self, messages, tools=None) -> dict:
        has_system = any(m.get("role") == "system" for m in messages)
        system = 50 if has_system else 0  # 系统提示计入与否：差 50（验 _build_system_prompt 被前置）
        tool_tok = 30 if tools else 0  # tools schema 计入与否：差 30（验 to_openai_format 被传入）
        msg_tok = 10 * len([m for m in messages if m.get("role") != "system"])
        return {
            "system": system, "messages": msg_tok,
            "tools": tool_tok, "total": system + msg_tok + tool_tok,
        }

    def get_usage_stats(self, messages, tools=None) -> dict:
        self.stats_calls.append(list(messages))
        bd = self.estimate_tokens_breakdown(messages, tools=tools)  # headline: messages-only
        return {
            "estimated_tokens": bd["total"],
            "token_count_source": "local",
            "max_tokens": 1000,
            "usage_percent": round(bd["total"] / 1000 * 100, 1),
            "message_count": len(messages),
            "token_breakdown": bd,
        }


class _FakeAgent:
    """打桩 agent：arun 把 user/assistant 入 messages（**dict 形态，镜像真 agent.messages**），
    并**从工作线程**经传入的 transport 发 LLM_TEXT（镜像真 arun 的 run_in_executor 线程模型，
    逼出 call_soon_threadsafe 桥）。messages 可被外部赋值（恢复路径 agent.messages = loaded）。"""

    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.transport = kwargs["transport"]  # 构造期传入的真 transport（token 唯一活线）
        self.messages: list[dict] = []  # dict 形态 {"role","content"}：可被 save/load_session 序列化
        self.permission_engine = _Recorder()
        self.tool_runner = _Recorder()
        self.skill_manager = _FakeSkillManager()
        self.context_manager = _FakeContextManager()  # /context /status 自省（P4.4）
        self.tools = _FakeToolRegistry(_default_tools())  # /tools 自省（P4.4）
        self._model = kwargs.get("model")  # 省略 --model 时无 model 键 → None → get_current_model 兜底
        self._plan_mode = False  # _context_stats 读它传给 to_openai_format（镜像真 agent）
        self.closed = False
        # P4.5：可写 turn 测试用——arun 内（executor 线程，镜像真 turn 写盘时机）跑此回调，
        # 经注入的 PolicyFileSystem（kwargs["filesystem"]）做结构化写、或直接写盘模拟 shell 旁路。
        self.action = None

    def get_current_model(self) -> str:
        return self._model or "fake-model"

    def _build_system_prompt(self) -> str:
        return "SYS"  # _context_stats 前置它入 messages_with_system（验 system 分项被计入）

    async def arun(self, msg: str, **_kw) -> str:
        self.messages.append({"role": "user", "content": msg})
        n = sum(1 for m in self.messages if m.get("role") == "user")
        answer = f"#{n} 回应：{msg}"  # 含轮次 → 第二轮答案体现累积历史

        loop = asyncio.get_running_loop()

        def work() -> None:  # 在线程池线程发事件（镜像真 arun）
            for ch in answer:
                self.transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": ch}))
            if self.action is not None:  # P4.5：可写 turn 在写盘时机执行注入的写动作
                self.action(self)

        await loop.run_in_executor(None, work)
        self.messages.append({"role": "assistant", "content": answer})
        return answer

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def chat_env(kb, monkeypatch):
    """注入 fake build_from_environment + no-op ensure_skill_available；返回 (kb, captured)。

    供需要多 store / 模拟重启 / 自定 session_persist 的 P4.2 测试用——它们自建 TestClient 或
    直接构造 ConversationStore（同一 monkeypatch 对后建的 app/store 同样生效）。
    """
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
    """绑定到临时知识库、注入 fake agent 的 TestClient（默认 session_persist 开）。"""
    kb, captured = chat_env
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


# ───────────────────────── 只读自省 /api/info（P4.4 C1） ─────────────────────────


def test_app_info_no_conversation(chat_client) -> None:
    """`GET /api/info`（app 级）：无会话也答，含约定字段、mode 恒 read-only、恒 200、零 LLM。"""
    client, captured = chat_client
    resp = client.get("/api/info")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "kb_name", "model", "mode", "persist", "conversations", "max_conversations"
    }
    assert body["mode"] == "read-only"
    assert body["persist"] is True
    assert body["conversations"] == 0  # 尚无会话
    assert body["max_conversations"] == 100
    assert captured["agents"] == []  # app 级 info 不建 agent


def test_app_info_counts_live_conversations(chat_client) -> None:
    """建一个会话后 `conversations` 计数 +1（in-memory，不依赖盘读）。"""
    client, _ = chat_client
    _chat(client, "问")
    assert client.get("/api/info").json()["conversations"] == 1


def test_chat_info_live_full(chat_client) -> None:
    """热（内存）会话 info 全量：model/turns/messages/mode/context{…}/skills{active,available}/tools[…]。"""
    client, captured = chat_client
    _, done, _ = _chat(client, "第一问")
    cid = done["conversation_id"]

    info = client.get(f"/api/chat/{cid}/info")
    assert info.status_code == 200
    body = info.json()
    assert body["id"] == cid and body["live"] is True
    assert body["mode"] == "read-only"
    assert body["model"] == "fake-model"  # 省略 --model → get_current_model 兜底
    assert body["turns"] == 1
    assert body["messages"] == 2  # 1 user + 1 assistant
    # context breakdown 含 system prompt + tools schema（codex P2 修复）：fake 下 system=50（_build_
    # system_prompt 被前置）、tools=30（to_openai_format 被传入）、messages=2×10=20 → total=100；
    # 而 headline（estimated_tokens/usage_percent）仍 messages-only（system/tools 不计、镜像 agentao）。
    bd = body["context"]["token_breakdown"]
    assert bd["system"] == 50 and bd["tools"] == 30  # 关键：非 0（messages-only 取法会是 0）
    assert bd["messages"] == 20 and bd["total"] == 100
    assert body["context"]["estimated_tokens"] == 20  # headline 仍 messages-only（system/tools 不计）
    assert body["context"]["usage_percent"] == 2.0
    # skills：active 含 guanlan-wiki；available 标 other-skill 未激活。
    assert body["skills"]["active"] == ["guanlan-wiki"]
    avail = {s["name"]: s for s in body["skills"]["available"]}
    assert avail["guanlan-wiki"]["active"] is True
    assert avail["other-skill"]["active"] is False
    assert avail["other-skill"]["description"] == "desc:other-skill"
    # info 无副作用：agent.messages 未被 info 改动（仍恰 2 条：1 user + 1 assistant，
    # 而非 `== self[:2]` 那种恒真比较）；get_usage_stats 也是拿快照、不回写 agent.messages。
    agent = captured["agents"][0]
    assert len(agent.messages) == 2
    assert [m["role"] for m in agent.messages] == ["user", "assistant"]


def test_chat_info_tools_blocked_static(chat_client) -> None:
    """/tools 的 blocked：元数据（read_file=False/write_file=True）+ 已知名静态（replace=True/
    search_file_content=False）+ 未知名（mystery_tool="unknown"）；按名排序、不试调工具、不随请求变。"""
    client, _ = chat_client
    _, done, _ = _chat(client, "问")
    tools = client.get(f"/api/chat/{done['conversation_id']}/info").json()["tools"]
    assert [t["name"] for t in tools] == [  # 按工具名排序
        "mystery_tool", "read_file", "replace", "search_file_content", "write_file"
    ]
    blocked = {t["name"]: t["blocked"] for t in tools}
    assert blocked["read_file"] is False and blocked["write_file"] is True  # 元数据路
    assert blocked["replace"] is True and blocked["search_file_content"] is False  # 静态名路
    assert blocked["mystery_tool"] == "unknown"  # 未识别 → unknown（非 True/False）


def test_chat_info_tools_unblocked_in_workspace_write(chat_client) -> None:
    """切到 workspace-write 后 /tools 的 blocked 一律 False（评审 P2）：可写姿态下
    tool_runner.readonly_mode=False、无工具被分类拦截，自省须如实指示写能力、不再标灰写/shell。"""
    client, _ = chat_client
    _, done, _ = _chat(client, "问")
    cid = done["conversation_id"]
    # read-only（默认）下写工具仍被拦
    assert {t["name"]: t["blocked"] for t in
            client.get(f"/api/chat/{cid}/info").json()["tools"]}["write_file"] is True
    # 翻到 workspace-write → 全部 False（含 write/replace/未知）
    assert client.post(f"/api/chat/{cid}/mode", json={"mode": "workspace-write"}).status_code == 200
    blocked = {t["name"]: t["blocked"] for t in
               client.get(f"/api/chat/{cid}/info").json()["tools"]}
    assert all(v is False for v in blocked.values()), blocked
    # 翻回 read-only → 恢复逐项判定
    assert client.post(f"/api/chat/{cid}/mode", json={"mode": "read-only"}).status_code == 200
    assert {t["name"]: t["blocked"] for t in
            client.get(f"/api/chat/{cid}/info").json()["tools"]}["write_file"] is True


def test_chat_info_unknown_404(chat_client) -> None:
    """未知且非盘上 Web 会话 id → 404（非规范 id 同样走 404）。"""
    client, _ = chat_client
    assert client.get("/api/chat/not-a-uuid/info").status_code == 404
    assert client.get(f"/api/chat/{uuid.uuid4()}/info").status_code == 404


def test_chat_info_cold_partial_no_agent(chat_env, kb) -> None:
    """冷（盘上-only）会话 info：live:false、有 title/model/messages、context/skills/tools==null，
    **不建 agent**（决策P4.4-7：纯自省不值当一次重恢复）。"""
    kb, captured = chat_env
    cold = str(uuid.uuid4())
    _write_foreign_session(kb, cold, active_skills=[SKILL_NAME])  # 盘上 Web 会话、内存无
    with TestClient(create_app(kb)) as client:
        resp = client.get(f"/api/chat/{cold}/info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["live"] is False
    assert body["id"] == cold
    assert body["title"] == "外部会话"  # 取自 catalog
    assert body["model"] == "m"  # _write_foreign_session 落的 model
    assert body["messages"] == 2  # message_count（非轮次）
    assert body["mode"] == "read-only"
    assert body["context"] is None and body["skills"] is None and body["tools"] is None
    assert captured["agents"] == []  # 关键：冷自省全程不建 agent


def test_chat_info_cold_foreign_404(chat_env, kb) -> None:
    """盘上但非 Web 会话（active_skills 不含 SKILL_NAME）→ cold_info None → 404（作用域闸）。"""
    kb, _ = chat_env
    foreign = str(uuid.uuid4())
    _write_foreign_session(kb, foreign, active_skills=["other-skill"])
    with TestClient(create_app(kb)) as client:
        assert client.get(f"/api/chat/{foreign}/info").status_code == 404


def test_chat_info_cold_404_when_persist_off(chat_env, kb) -> None:
    """持久化关：盘上 Web 会话的 info 一律 404（等价纯内存，不读盘）。"""
    kb, _ = chat_env
    leftover = str(uuid.uuid4())
    _write_foreign_session(kb, leftover, active_skills=[SKILL_NAME])
    with TestClient(create_app(kb, session_persist=False)) as client:
        assert client.get(f"/api/chat/{leftover}/info").status_code == 404
        assert client.get("/api/info").json()["persist"] is False


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
    # 即便首轮失败，error 也带 conversation_id：前端据此记住已建会话，不会下次另起新会话堆到 503。
    assert error.get("conversation_id")


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


# ───────────────────────── 停止按钮（中断在飞轮） ─────────────────────────


class _CancelAgent(_FakeAgent):
    """打桩 agent：先流出一个 token，再**在 executor 线程里阻塞等取消令牌**，被置位后抛
    AgentCancelledError——镜像真 arun「读同一 token、被 cancel 后抛、await 直到线程收尾」的契约。"""

    async def arun(self, msg, cancellation_token=None, **_kw):
        self.messages.append({"role": "user", "content": msg})
        loop = asyncio.get_running_loop()

        def work() -> None:
            self.transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "部分答案"}))
            while cancellation_token is None or not cancellation_token.is_cancelled:
                time.sleep(0.005)
            raise AgentCancelledError(cancellation_token.reason)

        await loop.run_in_executor(None, work)  # 线程收尾（抛出）后 await 才返回
        return "不可达"


def test_conversation_request_stop_cancels_turn(kb, monkeypatch) -> None:
    """request_stop 置位令牌 → arun 收尾抛 AgentCancelledError；已流出的 token 仍到达。"""
    import guanlan.web.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)
    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _CancelAgent(kw))

    async def main() -> None:
        store = chat_mod.ConversationStore(kb, None)
        conv = store.create()
        emitted: list[tuple[str, object]] = []

        async def run() -> str:
            try:
                await conv.turn("问", lambda k, d: emitted.append((k, d)))
            except AgentCancelledError:
                return "cancelled"
            return "done"

        task = asyncio.create_task(run())
        # 等 turn 进入在飞态（令牌就位）再停。
        while conv._cancel_token is None:
            await asyncio.sleep(0.005)
        assert conv.request_stop() is True
        assert await task == "cancelled"
        # 停止前已流出的 token 不丢。
        assert any(k == "token" for k, _ in emitted)
        # 轮结束后令牌已清，再停 = False（无在飞轮）。
        assert conv.request_stop() is False

    asyncio.run(main())


def test_request_stop_during_start_window_is_honored(kb, monkeypatch) -> None:
    """codex 竞态修复（首轮）：begin_turn 后、turn 装令牌前到达的停止被兑现，不再被吞。

    端点在 emit('start') 前调 begin_turn；前端一收 start 就 POST /stop。这里模拟该顺序：
    begin_turn → request_stop（令牌未装上 → 记待停、返回 True）→ turn 进锁装令牌即兑现待停 →
    _CancelAgent 立即见 cancelled → 抛 AgentCancelledError。（修复前 _cancel_token 为 None、
    request_stop 当 idle 返回 False、停止静默失败。）"""
    import guanlan.web.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)
    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _CancelAgent(kw))

    async def main() -> None:
        store = chat_mod.ConversationStore(kb, None)
        conv = store.create()
        conv.begin_turn()  # 端点在 emit('start') 前调
        assert conv.request_stop() is True  # 令牌未装上但有轮在飞 → 记待停（不当 idle 丢）
        with pytest.raises(AgentCancelledError):  # turn 进锁兑现待停 → 立即抛
            await conv.turn("问", lambda k, d: None)
        conv.end_turn()
        assert conv._cancel_token is None  # finally 归口清理
        assert conv.request_stop() is False  # end_turn 后无在飞轮 → idle → False

    asyncio.run(main())


def test_stop_targets_active_turn_not_queued(kb, monkeypatch) -> None:
    """codex 竞态修复（并发）：第二轮起跑（begin_turn）不改 _cancel_token——stop 仍打断持锁的活跃轮，
    不误伤排队轮。即「令牌锁内安装」的不变量：_cancel_token 恒指向持锁者，而非最后一个起跑的轮。"""
    import guanlan.web.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)
    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _CancelAgent(kw))

    async def main() -> None:
        store = chat_mod.ConversationStore(kb, None)
        conv = store.create()
        conv.begin_turn()
        t1 = asyncio.create_task(conv.turn("活跃轮", lambda k, d: None))
        while conv._cancel_token is None:  # 等活跃轮进锁装上令牌
            await asyncio.sleep(0.005)
        token1 = conv._cancel_token
        conv.begin_turn()  # 模拟并发第二轮起跑（端点在 emit('start') 前调）——绝不能覆盖活跃轮令牌
        assert conv._cancel_token is token1  # 关键不变量：未被排队轮覆盖
        assert conv.request_stop() is True
        assert token1.is_cancelled  # 打断的是活跃轮 token1（而非排队轮）
        with pytest.raises(AgentCancelledError):
            await t1
        conv.end_turn()  # 收掉活跃轮
        conv.end_turn()  # 收掉那个未真正跑的排队标记

    asyncio.run(main())


def test_duplicate_stop_does_not_cancel_queued_turn(kb, monkeypatch) -> None:
    """codex 评审（幂等）：活跃轮令牌已 cancel 后，重复 /stop 不得经待停误杀排队轮。

    活跃轮 token1 已 cancel、排队轮在飞（_inflight=2）时再 stop：应幂等返回 True，但**不**置
    `_stop_requested`（否则排队轮进锁会把它当待停兑现、把下一轮也停掉）。"""
    import guanlan.web.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ensure_skill_available", lambda _kb: None)
    monkeypatch.setattr(chat_mod, "build_from_environment", lambda **kw: _CancelAgent(kw))

    async def main() -> None:
        store = chat_mod.ConversationStore(kb, None)
        conv = store.create()
        conv.begin_turn()
        t1 = asyncio.create_task(conv.turn("活跃轮", lambda k, d: None))
        while conv._cancel_token is None:
            await asyncio.sleep(0.005)
        conv.begin_turn()  # 排队轮起跑标记（_inflight=2）
        assert conv.request_stop() is True  # 停活跃轮 → cancel token1
        assert conv._cancel_token.is_cancelled
        # 重复 stop（活跃轮令牌已 cancel、排队轮在飞）：幂等 True，但绝不设待停。
        assert conv.request_stop() is True
        assert conv._stop_requested is False  # 关键：排队轮不会被误兑现
        with pytest.raises(AgentCancelledError):
            await t1
        conv.end_turn()
        conv.end_turn()

    asyncio.run(main())


def test_chat_stop_unknown_conversation_404(chat_client) -> None:
    client, _ = chat_client
    resp = client.post("/api/chat/does-not-exist/stop")
    assert resp.status_code == 404


def test_chat_stop_idle_conversation_returns_false(chat_client) -> None:
    """对已存在但无在飞轮的会话停止 → 200 {"stopped": false}（幂等、不报错）。"""
    client, _ = chat_client
    _, done, _ = _chat(client, "问")
    cid = done["conversation_id"]
    resp = client.post(f"/api/chat/{cid}/stop")
    assert resp.status_code == 200
    assert resp.json() == {"stopped": False}


def test_chat_emits_start_event_with_id(chat_client) -> None:
    """流首帧 start 即带 conversation_id（首轮停止按钮所需）。"""
    client, _ = chat_client
    first_event = first_data = None
    with client.stream("POST", "/api/chat", json={"message": "问"}) as resp:
        event = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
                if first_event is None:
                    first_event = event
            elif line.startswith("data:") and first_data is None:
                first_data = json.loads(line.split(":", 1)[1].strip())
                break
    assert first_event == "start"
    assert first_data and first_data["conversation_id"]


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


def test_absent_endpoints(client) -> None:
    """范围红线：无 /api/query、无写作业 SSE 订阅端点（§10）。

    P4.1 起 `POST /api/raw` 是真实端点（投喂）；但删/改 raw 仍不在范围（无 DELETE /api/raw）。
    """
    assert client.get("/api/query").status_code == 404
    assert client.post("/api/query", json={"question": "x"}).status_code in (404, 405)
    assert client.delete("/api/raw").status_code in (404, 405)  # 仍无删源端点（P4.1 只新增）
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


# ───────────────────────── 会话落盘与跨重启恢复（P4.2） ─────────────────────────

from guanlan.skill import SKILL_NAME  # noqa: E402


def _session_files(kb) -> list:
    """盘上现存的会话快照文件（newest-first 无关，仅做计数/读内容用）。"""
    d = kb / ".agentao" / "sessions"
    return sorted(d.glob("*.json")) if d.exists() else []


def _snapshots_for(kb, session_id: str) -> list:
    """盘上属于某 `session_id` 的快照文件（验 prune 后每会话仅 1 份）。"""
    out = []
    for p in _session_files(kb):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("session_id") == session_id:
                out.append(p)
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _write_foreign_session(kb, session_id: str, *, active_skills=None) -> None:
    """直接落一份会话快照（模拟 agentao CLI 或构造特定 active_skills 的会话）。"""
    from agentao.embedding import save_session

    save_session(
        [{"role": "user", "content": "外部会话"}, {"role": "assistant", "content": "回应"}],
        "m",
        active_skills=active_skills if active_skills is not None else ["other-skill"],
        session_id=session_id,
        project_root=kb,
    )


def test_session_persist_round_trip(chat_client, kb) -> None:
    """一轮后盘上出现该会话文件；load_session 取回含本轮 user/assistant；文件内 session_id==conv.id。"""
    from agentao.embedding import load_session

    client, _ = chat_client
    _, done, _ = _chat(client, "第一问")
    cid = done["conversation_id"]

    snaps = _snapshots_for(kb, cid)
    assert len(snaps) == 1  # 落盘且 session_id 写进文件内容
    assert uuid.UUID(cid)  # conv.id 是规范 UUID（决策P4.2-2）

    messages, _model, active = load_session(cid, project_root=kb)
    assert {m["role"] for m in messages} == {"user", "assistant"}
    assert any(m["content"] == "第一问" for m in messages)
    assert SKILL_NAME in active  # 落了 guanlan-wiki（恢复时据此过滤/激活）


def test_session_multiturn_dedup_and_restore_latest(chat_client, kb) -> None:
    """同会话连发 3 轮：①GET 只 1 条（按 session_id 去重）；②盘上该会话仅 1 份；③恢复到第 3 轮最新。"""
    client, captured = chat_client
    _, done, _ = _chat(client, "Q1")
    cid = done["conversation_id"]
    _chat(client, "Q2", conversation_id=cid)
    _chat(client, "Q3", conversation_id=cid)

    convs = client.get("/api/conversations").json()["conversations"]
    assert sum(1 for c in convs if c["id"] == cid) == 1  # 去重：非 3 条快照
    assert len(_snapshots_for(kb, cid)) == 1  # prune：每会话仅 1 份

    # 模拟重启：新 store 读盘恢复，messages 已到第 3 轮最新状态。
    import guanlan.web.chat as chat_mod

    store2 = chat_mod.ConversationStore(kb, None)
    conv = store2.restore(cid)
    assert conv is not None
    contents = [m["content"] for m in conv.agent.messages]
    assert "Q3" in contents and "#3 回应：Q3" in contents  # 第 3 轮内容在
    assert conv.turns == 3


def test_cross_restart_restore_via_http(chat_env, kb) -> None:
    """模拟重启：用同一 kb 新建 store/app → GET 列出上一进程会话（live=false）→ 带 id 续聊。"""
    _kb, _ = chat_env
    with TestClient(create_app(kb)) as c1:
        _, done, _ = _chat(c1, "重启前一问")
        cid = done["conversation_id"]

    # 新进程：全新 app（空内存），盘上 catalog 仍在。
    with TestClient(create_app(kb)) as c2:
        listed = c2.get("/api/conversations").json()["conversations"]
        entry = next(e for e in listed if e["id"] == cid)
        assert entry["live"] is False  # 来自盘上 catalog
        assert "messages" in entry  # 冷条目报消息数（非 turns）

        _, done2, error = _chat(c2, "重启后追问", conversation_id=cid)
        assert error is None and done2 is not None
        assert done2["conversation_id"] == cid
        assert done2["answer"].startswith("#2")  # 看到重启前累积的上下文（第 2 轮）


def test_restore_preserves_readonly_posture(chat_env, kb) -> None:
    """恢复后两点只读置位 + 激活 guanlan-wiki（由共用构造，非回放盘上 active_skills）。"""
    import guanlan.web.chat as chat_mod

    with TestClient(create_app(kb)) as c1:
        _, done, _ = _chat(c1, "一问")
        cid = done["conversation_id"]

    store2 = chat_mod.ConversationStore(kb, None)
    conv = store2.restore(cid)
    assert conv is not None
    agent = conv.agent
    assert any(call[0] == "set_mode" and call[1] == (PermissionMode.READ_ONLY,) for call in agent.permission_engine.calls)
    assert any(call[0] == "set_readonly_mode" and call[1] == (True,) for call in agent.tool_runner.calls)
    assert any(call[0] == "activate_skill" and call[1][0] == SKILL_NAME for call in agent.skill_manager.calls)


def test_delete_live_cascades_to_disk(chat_client, kb) -> None:
    """内存命中支：DELETE 后内存无对象 **且** 盘上该 session_id 全部快照被删。"""
    client, _ = chat_client
    _, done, _ = _chat(client, "问")
    cid = done["conversation_id"]
    assert _snapshots_for(kb, cid)  # 先有盘文件

    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert not _snapshots_for(kb, cid)  # 级联删盘
    assert not any(c["id"] == cid for c in client.get("/api/conversations").json()["conversations"])


def test_delete_disk_only_session(chat_env, kb) -> None:
    """仅在盘上（内存无对象）：规范 UUID + 作用域命中 → 删成功；皆无 → 404。"""
    _kb, _ = chat_env
    with TestClient(create_app(kb)) as c1:
        _, done, _ = _chat(c1, "问")
        cid = done["conversation_id"]

    with TestClient(create_app(kb)) as c2:  # 新进程：cid 仅在盘上
        assert c2.delete(f"/api/conversations/{cid}").status_code == 200
        assert not _snapshots_for(kb, cid)
        assert c2.delete(f"/api/conversations/{cid}").status_code == 404  # 已删
        assert c2.delete(f"/api/conversations/{uuid.uuid4()}").status_code == 404  # 皆无


def test_delete_live_even_when_disk_rotated(chat_env, kb, monkeypatch) -> None:
    """live 但盘文件已被删：仍走内存命中支删内存（best-effort delete_session 不报错）。"""
    _kb, _ = chat_env
    with TestClient(create_app(kb)) as client:
        _, done, _ = _chat(client, "问")
        cid = done["conversation_id"]
        # 模拟盘文件已被 rotate 掉：手动删光，再 DELETE，仍应 200（不被 _disk_session 闸挡）。
        for p in _snapshots_for(kb, cid):
            p.unlink()
        assert client.delete(f"/api/conversations/{cid}").status_code == 200
        assert not any(c["id"] == cid for c in client.get("/api/conversations").json()["conversations"])


def test_restore_race_returns_404_not_error(chat_env, kb, monkeypatch) -> None:
    """_disk_session 命中后、load_session 前文件被删/写坏 → restore 返回 None → 端点 404，不冒流式 error。"""
    import guanlan.web.chat as chat_mod

    with TestClient(create_app(kb)) as c1:
        _, done, _ = _chat(c1, "问")
        cid = done["conversation_id"]

    # load_session 抛 JSONDecodeError（模拟竞态/坏文件）。
    def _boom(*_a, **_k):
        raise json.JSONDecodeError("x", "y", 0)

    monkeypatch.setattr(chat_mod, "load_session", _boom)
    with TestClient(create_app(kb)) as c2:
        resp = c2.post("/api/chat", json={"message": "续", "conversation_id": cid})
        assert resp.status_code == 404  # 不是流式 error、不冒 traceback


def test_scope_filter_non_web_session(chat_env, kb) -> None:
    """active_skills 不含 SKILL_NAME 的会话：GET 不列、restore → 404（决策P4.2-6）。"""
    foreign = str(uuid.uuid4())
    _write_foreign_session(kb, foreign, active_skills=["agentao-cli-skill"])

    with TestClient(create_app(kb)) as client:
        listed = client.get("/api/conversations").json()["conversations"]
        assert not any(c["id"] == foreign for c in listed)  # 不列非 Web 会话
        resp = client.post("/api/chat", json={"message": "x", "conversation_id": foreign})
        assert resp.status_code == 404  # restore 拒之


def test_exact_id_rejects_prefix_and_timestamp(chat_env, kb, monkeypatch) -> None:
    """非规范 id（短前缀/时间戳 stem/乱串）→ 404，且 load_session 未被调用（_is_canonical_uuid 先挡）。"""
    import guanlan.web.chat as chat_mod

    with TestClient(create_app(kb)) as c1:
        _, done, _ = _chat(c1, "问")
        cid = done["conversation_id"]

    called = []
    real_load = chat_mod.load_session
    monkeypatch.setattr(chat_mod, "load_session", lambda *a, **k: called.append(1) or real_load(*a, **k))

    with TestClient(create_app(kb)) as c2:
        for bad in (cid[:8], _session_files(kb)[0].stem, "not-a-uuid"):
            resp = c2.post("/api/chat", json={"message": "x", "conversation_id": bad})
            assert resp.status_code == 404, bad
        assert called == []  # 规范化闸先挡，绝不喂前缀匹配的 load_session


def test_delete_prefix_does_not_cross_delete(chat_env, kb) -> None:
    """DELETE 传某会话 session_id 短前缀 → 404 且该会话快照仍在（不经 delete_session 前缀删一片）。"""
    with TestClient(create_app(kb)) as c1:
        _, done, _ = _chat(c1, "问")
        cid = done["conversation_id"]

    with TestClient(create_app(kb)) as c2:  # 新进程：仅在盘上
        assert c2.delete(f"/api/conversations/{cid[:8]}").status_code == 404
        assert _snapshots_for(kb, cid)  # 快照仍在，未被前缀误删


def test_delete_foreign_session_scope_blocked(chat_env, kb) -> None:
    """DELETE 传非 Web 会话（无 SKILL_NAME）全 UUID → 404 且文件保留（_disk_session 作用域拦截）。"""
    foreign = str(uuid.uuid4())
    _write_foreign_session(kb, foreign, active_skills=["other"])

    with TestClient(create_app(kb)) as client:
        assert client.delete(f"/api/conversations/{foreign}").status_code == 404
        assert _snapshots_for(kb, foreign)  # 非 Web 会话不被 Web 端删


def test_prune_single_long_conversation_keeps_one(chat_client, kb) -> None:
    """单会话 >10 轮：每轮 best-effort prune，该会话盘上始终仅 1 份、不冲掉别人。"""
    client, _ = chat_client
    _, done, _ = _chat(client, "Q1")
    cid = done["conversation_id"]
    for i in range(2, 14):  # 共 13 轮
        _chat(client, f"Q{i}", conversation_id=cid)
    assert len(_snapshots_for(kb, cid)) == 1
    assert len(_session_files(kb)) == 1  # 只有这一个会话


def test_prune_ten_sessions_then_update_one(chat_env, kb) -> None:
    """10 会话各 1 份后，更新其中一个：save-前-prune 不把目录顶到 11，另 9 个仍在（best-effort 口径）。"""
    with TestClient(create_app(kb)) as client:
        cids = []
        for i in range(10):
            _, done, _ = _chat(client, f"会话{i}")
            cids.append(done["conversation_id"])
        assert len({p.name for p in _session_files(kb)}) == 10

        _chat(client, "会话0-续", conversation_id=cids[0])  # 更新会话 0
        on_disk = {
            json.loads(p.read_text(encoding="utf-8"))["session_id"] for p in _session_files(kb)
        }
        assert set(cids) <= on_disk  # 10 个会话都还在盘上（prune 在前没把别人顶出）
        assert len(_session_files(kb)) == 10  # 仍 10 份（会话 0 prune 旧份后重存）


def test_more_than_ten_sessions_rotate(chat_env, kb) -> None:
    """>10 个不同会话各 1 轮：list_sessions 去重后受 _MAX_SESSIONS 文件轮转，盘上 catalog ≤10。"""
    with TestClient(create_app(kb)) as client:
        for i in range(13):
            _chat(client, f"会话{i}")  # 各新建（无 conversation_id）

    # 新进程（空内存）：合并视图只剩盘上 catalog，被 _rotate_sessions 截到 10。
    with TestClient(create_app(kb)) as fresh:
        listed = fresh.get("/api/conversations").json()["conversations"]
        assert len(listed) == 10
        assert all(c["live"] is False for c in listed)


def test_prune_unlink_failure_does_not_block_save(chat_env, kb, monkeypatch) -> None:
    """prune 删旧份抛 OSError → save_session 仍被调用、本轮答案正常返回（best-effort）。"""
    import guanlan.web.chat as chat_mod

    saved = []
    real_save = chat_mod.save_session
    monkeypatch.setattr(
        chat_mod, "save_session", lambda *a, **k: saved.append(1) or real_save(*a, **k)
    )

    with TestClient(create_app(kb)) as client:
        _, done, _ = _chat(client, "Q1")
        cid = done["conversation_id"]
        # 第二轮：prune 试删第一轮快照时抛 OSError。
        orig_unlink = chat_mod.Path.unlink

        def boom_unlink(self, *a, **k):
            raise OSError("不可删")

        monkeypatch.setattr(chat_mod.Path, "unlink", boom_unlink)
        _, done2, error = _chat(client, "Q2", conversation_id=cid)
        monkeypatch.setattr(chat_mod.Path, "unlink", orig_unlink)

    assert error is None and done2 is not None  # 答案正常返回
    assert len(saved) >= 2  # 两轮都调了 save（prune 失败跳过、不阻断）


def test_restore_uses_current_model_not_persisted(chat_env, kb) -> None:
    """恢复**不**用盘上持久的 model，而用当前进程模型（镜像 agentao PR #81）。"""
    from agentao.embedding import load_session

    import guanlan.web.chat as chat_mod

    # 进程 1：默认模型 old-model，落盘记录该名字。
    with TestClient(create_app(kb, model="old-model")) as c1:
        _, done, _ = _chat(c1, "问")
        cid = done["conversation_id"]
    _msgs, saved_model, _ = load_session(cid, project_root=kb)
    assert saved_model == "old-model"  # 快照里存的是存盘时的模型名

    # 进程 2：默认模型 new-model → restore 必须用 new-model（当前进程），不回绑盘上 old-model。
    store2 = chat_mod.ConversationStore(kb, "new-model")
    conv = store2.restore(cid)
    assert conv is not None
    assert conv.agent.kwargs.get("model") == "new-model"  # 用当前进程模型
    assert conv.agent.kwargs.get("model") != "old-model"  # 不回绑盘上持久模型


def test_failed_save_does_not_break_answer(chat_env, kb, monkeypatch) -> None:
    """save 抛错：off-loop 失败仅记日志，已成功的 arun 答案照常返回（失败不毁答案，§3.1）。"""
    import guanlan.web.chat as chat_mod

    with TestClient(create_app(kb)) as client:
        _, done, _ = _chat(client, "Q1")
        cid = done["conversation_id"]
        monkeypatch.setattr(
            chat_mod, "save_session", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        )
        _, done2, error = _chat(client, "Q2", conversation_id=cid)
        assert error is None and done2 is not None  # 落盘失败不冒泡、不毁答案


def test_restore_backfills_title_and_caps_memory(chat_env, kb) -> None:
    """恢复回填 title（取自首条 user）；并发 restore 同一 id 只产 1 个；内存满 → restore 抛。"""
    import guanlan.web.chat as chat_mod

    with TestClient(create_app(kb)) as c1:
        _, done, _ = _chat(c1, "标题来源问")
        cid = done["conversation_id"]

    store = chat_mod.ConversationStore(kb, None)
    conv = store.restore(cid)
    assert conv is not None and conv.title  # 非空（取自首条 user）
    assert store.restore(cid) is conv  # double-check：再恢复同一 id 复用同一对象

    # 内存满 → restore 抛 RuntimeError（同 create，端点转 503）。
    store2 = chat_mod.ConversationStore(kb, None)
    monkeypatch_max = chat_mod.MAX_CONVERSATIONS
    try:
        chat_mod.MAX_CONVERSATIONS = 0
        with pytest.raises(RuntimeError):
            store2.restore(cid)
    finally:
        chat_mod.MAX_CONVERSATIONS = monkeypatch_max


def test_conversation_messages_replay_live(chat_client) -> None:
    """live 会话：GET …/messages 回放 user/assistant 气泡，assistant 带渲染 html。"""
    client, _ = chat_client
    _, done, _ = _chat(client, "第一问")
    cid = done["conversation_id"]

    resp = client.get(f"/api/conversations/{cid}/messages")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "第一问"
    assert "html" in msgs[1] and "回应：第一问" in msgs[1]["html"]  # assistant 富排版（安全 HTML）


def test_conversation_messages_replay_cold(chat_env, kb) -> None:
    """冷会话（模拟重启）：GET …/messages 读盘回放、**不建 agent**（查看 ≠ 续聊懒恢复）。"""
    _kb, captured = chat_env
    with TestClient(create_app(kb)) as c1:
        _, done, _ = _chat(c1, "重启前一问")
        cid = done["conversation_id"]

    built_before = len(captured["agents"])
    with TestClient(create_app(kb)) as c2:
        resp = c2.get(f"/api/conversations/{cid}/messages")
        assert resp.status_code == 200
        contents = [m["content"] for m in resp.json()["messages"]]
        assert "重启前一问" in contents
        assert len(captured["agents"]) == built_before  # 纯查看不构造 agent


def test_conversation_messages_strips_system_reminder(chat_env, kb) -> None:
    """回放剥掉 user 消息里的 <system-reminder> 噪声（与 list_sessions 取 title 同口径）。"""
    from agentao.embedding import save_session

    cid = str(uuid.uuid4())
    save_session(
        [
            {"role": "user", "content": "真问题<system-reminder>隐藏注入</system-reminder>"},
            {"role": "assistant", "content": "答案"},
        ],
        "m",
        active_skills=[SKILL_NAME],
        session_id=cid,
        project_root=kb,
    )
    with TestClient(create_app(kb)) as client:
        msgs = client.get(f"/api/conversations/{cid}/messages").json()["messages"]
        assert msgs[0]["content"] == "真问题"  # 注入块被剥掉
        assert "system-reminder" not in msgs[0]["content"]


def test_conversation_messages_404_unknown_and_foreign(chat_env, kb) -> None:
    """未知 / 非规范 id / 非 Web 会话（无 SKILL_NAME）→ 404（同 chat/delete 的作用域+精确闸）。"""
    foreign = str(uuid.uuid4())
    _write_foreign_session(kb, foreign, active_skills=["other"])
    with TestClient(create_app(kb)) as client:
        assert client.get(f"/api/conversations/{uuid.uuid4()}/messages").status_code == 404
        assert client.get("/api/conversations/not-a-uuid/messages").status_code == 404
        assert client.get(f"/api/conversations/{foreign}/messages").status_code == 404


def test_conversation_messages_disk_only_404_when_persist_off(chat_env, kb) -> None:
    """--no-session-persist：盘上遗留会话的 messages 一律 404（不读盘）；live 会话仍可回放。"""
    leftover = str(uuid.uuid4())
    _write_foreign_session(kb, leftover, active_skills=[SKILL_NAME])
    with TestClient(create_app(kb, session_persist=False)) as client:
        assert client.get(f"/api/conversations/{leftover}/messages").status_code == 404
        _, done, _ = _chat(client, "问")
        cid = done["conversation_id"]
        assert client.get(f"/api/conversations/{cid}/messages").status_code == 200  # live 内存可回放


def test_no_session_persist_equivalent_to_memory_only(chat_env, kb) -> None:
    """--no-session-persist：turn 不落盘、GET 只见内存、disk-only id 一律 404、DELETE 内存命中只删内存。"""
    _kb, _ = chat_env
    with TestClient(create_app(kb, session_persist=False)) as client:
        _, done, _ = _chat(client, "问")
        cid = done["conversation_id"]
        assert not _session_files(kb)  # 不落盘

        # 即便盘上有遗留快照，带其 id 的 chat 也 404（restore 短路 not persist、不读盘）。
        leftover = str(uuid.uuid4())
        _write_foreign_session(kb, leftover, active_skills=[SKILL_NAME])
        resp = client.post("/api/chat", json={"message": "x", "conversation_id": leftover})
        assert resp.status_code == 404
        # GET 只见内存会话，不列盘上遗留。
        listed = client.get("/api/conversations").json()["conversations"]
        assert [c["id"] for c in listed] == [cid]
        # disk-only id（盘上遗留）DELETE → 404、不碰盘（遗留快照保留）。
        assert client.delete(f"/api/conversations/{leftover}").status_code == 404
        assert _snapshots_for(kb, leftover)
        # 内存命中 DELETE：只删内存、不调 delete_session（无盘文件可删，本就没落）。
        assert client.delete(f"/api/conversations/{cid}").status_code == 200


# ───────────────────────── Web-heal（P4.3）─────────────────────────
#
# 测试只聚焦 adapter（preview / 入队 / result 序列化 / 旧 job 兼容 / FIFO 烟测 / model 透传 /
# 形态）；P2/P3 的门禁/自愈/写集行为已由 test_heal.py 的 core 用例证过，**不在 Web 层重复证明**。


def _ref_missing(kb, name, *targets) -> None:
    """写一页 wiki/concepts/<name>.md 引用给定 [[target]]（构造高频缺失实体）。"""
    body = "见 " + "、".join(f"[[{t}]]" for t in targets)
    write_page(kb, f"wiki/concepts/{name}.md", body=body)


def test_heal_preview_matches_compute_worklist(kb) -> None:
    """GET /api/heal/preview 与同库 compute_worklist 逐项一致；零 LLM、不入队、不触 runner。"""
    from guanlan.heal import compute_worklist

    _ref_missing(kb, "a", "大模型", "gpt")
    _ref_missing(kb, "b", "大模型", "gpt")

    runner = make_runner(lambda root: None)
    with TestClient(create_app(kb, runner=runner)) as client:
        resp = client.get("/api/heal/preview")
        assert resp.status_code == 200
        data = resp.json()

    items = compute_worklist(kb / "wiki")
    expected = {
        "worklist": [
            {"target": w.target, "ref_count": w.ref_count, "ref_pages": list(w.ref_pages)}
            for w in items if not w.postponed
        ],
        "postponed": [
            {"target": w.target, "ref_count": w.ref_count, "ref_pages": list(w.ref_pages)}
            for w in items if w.postponed
        ],
    }
    assert data == expected
    assert {it["target"] for it in data["worklist"]} == {"大模型", "gpt"}
    assert runner.calls == []  # 纯读：未触 Agentao


def test_heal_preview_limit_and_min_refs(client, kb) -> None:
    """limit 推迟项进 postponed；min_refs 上调缩小 worklist。"""
    _ref_missing(kb, "a", "大模型", "gpt")
    _ref_missing(kb, "b", "大模型", "gpt")

    # limit=1：频次相同按 target 升序（ASCII "gpt" < CJK "大模型"）→ gpt 入批、大模型 推迟。
    data = client.get("/api/heal/preview", params={"limit": 1}).json()
    assert [w["target"] for w in data["worklist"]] == ["gpt"]
    assert [w["target"] for w in data["postponed"]] == ["大模型"]

    # min_refs=3：两者均 2 页引用 → 全数低于阈值 → 空。
    data3 = client.get("/api/heal/preview", params={"min_refs": 3}).json()
    assert data3["worklist"] == [] and data3["postponed"] == []


def test_heal_preview_default_limit_is_5(kb) -> None:
    """Web 端 heal 缺省 limit=5（刻意小于 CLI 的 10）：6 个等频缺失实体 → 5 入批、1 推迟。"""
    for i in range(6):
        _ref_missing(kb, f"a{i}", f"t{i}")
        _ref_missing(kb, f"b{i}", f"t{i}")
    with TestClient(create_app(kb)) as client:
        data = client.get("/api/heal/preview").json()  # 不带 limit → 用服务端缺省
    assert len(data["worklist"]) == 5
    assert len(data["postponed"]) == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"min_refs": 0}, {"limit": -1}])
def test_heal_preview_out_of_range_is_422(client, params) -> None:
    """limit/min_refs < 1 → 422（Query ge=1，对齐 CLI positive_int）。"""
    assert client.get("/api/heal/preview", params=params).status_code == 422


def test_heal_post_enqueues_and_serializes_result(kb) -> None:
    """POST /api/heal 即时返回 job_id（非阻塞）→ 轮询至 done：exit_code==0、result 为六字段机器
    回执（receipts 报 resolved）、**散文在 output 不在 result**（决策P4.3-1）。"""
    _ref_missing(kb, "a", "大模型")
    _ref_missing(kb, "b", "大模型")
    runner = make_runner(
        lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"),
        final_text="已建大模型页",
    )
    with TestClient(create_app(kb, runner=runner)) as client:
        resp = client.post("/api/heal", json={})
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        data = _wait_job(client, job_id)

    assert data["kind"] == "heal"
    assert data["exit_code"] == 0
    # result 是六字段机器回执，receipts 报 resolved。
    result = data["result"]
    assert set(result) == {
        "worklist", "postponed", "receipts", "unexpected_writes", "changed_paths", "exit_code"
    }
    assert result["receipts"][0]["target"] == "大模型"
    assert result["receipts"][0]["status"] == "resolved"
    assert result["exit_code"] == 0
    # 散文进 output、不掺 result。
    assert "已建大模型页" in data["output"]
    assert "已建大模型页" not in json.dumps(result, ensure_ascii=False)


def test_heal_post_targets_filters_over_server_recompute(kb) -> None:
    """targets（决策P4.3-3 修订）= 服务端重算 worklist 的**过滤器**：勾选子集只物化命中者，
    陈旧/伪造目标被交集丢弃（绝不物化服务端没独立推出的目标，防 TOCTOU）。"""
    _ref_missing(kb, "a", "大模型", "gpt")
    _ref_missing(kb, "b", "大模型", "gpt")
    runner = make_runner(lambda root: write_page(root, "wiki/entities/gpt.md", type="entity"))
    with TestClient(create_app(kb, runner=runner)) as client:
        # 勾选 gpt（真在 worklist）+ 伪造目标（不在）→ 只物化 gpt，伪造目标被交集丢弃。
        resp = client.post("/api/heal", json={"targets": ["gpt", "伪造目标"]})
        assert resp.status_code == 200
        data = _wait_job(client, resp.json()["job_id"])
    assert [r["target"] for r in data["result"]["receipts"]] == ["gpt"]


def test_heal_post_forged_targets_heal_nothing(kb) -> None:
    """只勾选服务端 worklist 外的伪造目标 → 交集空 → 空批次短路、runner 零调用（TOCTOU 安全）。"""
    _ref_missing(kb, "a", "大模型")
    _ref_missing(kb, "b", "大模型")
    runner = make_runner(lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"))
    with TestClient(create_app(kb, runner=runner)) as client:
        resp = client.post("/api/heal", json={"targets": ["伪造目标"]})
        data = _wait_job(client, resp.json()["job_id"])
    assert data["result"]["receipts"] == []
    assert runner.calls == []


def test_heal_post_no_targets_heals_all(kb) -> None:
    """省略 targets（旧行为）：服务端重算整批全物化。"""
    _ref_missing(kb, "a", "大模型")
    _ref_missing(kb, "b", "大模型")
    runner = make_runner(lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"))
    with TestClient(create_app(kb, runner=runner)) as client:
        resp = client.post("/api/heal", json={})
        data = _wait_job(client, resp.json()["job_id"])
    assert data["result"]["receipts"][0]["target"] == "大模型"


def test_heal_empty_worklist_job(kb) -> None:
    """无缺失实体 → heal 作业空批次：exit_code==0、result 六字段（空 receipts）、runner 零调用。"""
    runner = make_runner(lambda root: None)
    with TestClient(create_app(kb, runner=runner)) as client:
        resp = client.post("/api/heal", json={})
        data = _wait_job(client, resp.json()["job_id"])
    assert data["exit_code"] == 0
    assert data["result"]["receipts"] == []
    assert runner.calls == []  # 空 worklist 短路、未触 Agentao


def test_jobs_result_null_for_ingest(kb) -> None:
    """旧作业兼容：ingest 作业 result 恒为 null（worker int 分支不变、零回归）。"""
    target = _put_raw(kb)
    runner = make_runner(lambda root: write_page(root, "wiki/concepts/New.md"))
    data = _ingest_once(kb, runner, target)
    assert data["kind"] == "ingest"
    assert data["result"] is None


def test_heal_model_passthrough(kb) -> None:
    """省略 model → 用 app 级 model；给 model → 透传进 run_heal_result（含 None，走子进程无嵌入坑）。"""
    _ref_missing(kb, "a", "大模型")
    _ref_missing(kb, "b", "大模型")

    # 给 model：透传到子进程 runner。
    runner = make_runner(lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"))
    with TestClient(create_app(kb, model="app-model", runner=runner)) as client:
        _wait_job(client, client.post("/api/heal", json={"model": "req-model"}).json()["job_id"])
    assert runner.calls[0]["model"] == "req-model"

    # 省略 model：回落 app 级 model（用新目标 新词，避开已 resolved 的 大模型）。
    _ref_missing(kb, "c", "新词")
    _ref_missing(kb, "d", "新词")
    runner2 = make_runner(lambda root: write_page(root, "wiki/entities/新词.md", type="entity"))
    with TestClient(create_app(kb, model="app-model", runner=runner2)) as client:
        _wait_job(client, client.post("/api/heal", json={}).json()["job_id"])
    assert runner2.calls[0]["model"] == "app-model"


def test_heal_serial_behind_ingest(kb) -> None:
    """单写者 FIFO 烟测：先入队卡住的 ingest，再 POST /api/heal → heal 在 ingest 之后完成，
    两者皆 done、ingest 不被冤判 raw_mutated（同 worker 串行，决策P4.3-2）。"""
    _put_raw(kb, "src.md")
    _ref_missing(kb, "a", "大模型")
    _ref_missing(kb, "b", "大模型")
    gate = threading.Event()
    order: list[str] = []

    def runner(prompt, **kwargs):
        # 第一个作业（ingest）卡住，张开 raw/ 快照窗口；heal 在其后才跑。
        if "大模型" in prompt:  # heal prompt 含目标名
            order.append("heal")
            write_page(kwargs["working_directory"], "wiki/entities/大模型.md", type="entity")
        else:
            order.append("ingest")
            gate.wait(timeout=3)
            write_page(kwargs["working_directory"], "wiki/concepts/N.md")
        return AgentRunResult(ok=True, final_text="done")

    with TestClient(create_app(kb, runner=runner)) as client:
        ing_id = client.post("/api/ingest", json={"target": "raw/src.md"}).json()["job_id"]
        heal_id = client.post("/api/heal", json={}).json()["job_id"]
        time.sleep(0.1)
        assert order == ["ingest"]  # heal 被挡在在飞 ingest 之后（同 FIFO worker）
        gate.set()
        ing = _wait_job(client, ing_id)
        heal = _wait_job(client, heal_id)

    assert order == ["ingest", "heal"]  # FIFO：heal 在 ingest 之后才跑
    assert ing["exit_code"] == 0  # ingest 没被 heal 的写冤判 EXIT_RAW_MUTATED
    assert heal["exit_code"] == 0
    assert heal["result"]["receipts"][0]["status"] == "resolved"


def test_heal_frontend_wired(client) -> None:
    """前端接线（C2）：顶栏有 heal 按钮，app.js 拉 preview/POST heal 并按 result 渲染回执。"""
    index = client.get("/").text
    assert 'id="heal-btn"' in index
    js = client.get("/static/app.js").text
    assert "/api/heal/preview" in js
    assert '"/api/heal"' in js  # POST 物化
    assert "renderHealDone" in js and "job.result" in js  # 按结构化 result 渲染


def test_heal_no_sse_no_path_param(client) -> None:
    """形态红线：无 heal SSE（/api/heal/{id}/events → 404/405）；端点无 path/name 入参（无穿越面）。"""
    assert client.get("/api/heal/1/events").status_code in (404, 405)
    # POST /api/heal 不接受 path/name（只 limit/min_refs/model）：传非法 limit 才 422，path 被忽略。
    assert client.post("/api/heal", json={"limit": 0}).status_code == 422


# ═══════════════════════ P4.5 可写 Web 工作会话（C0 + a/b/c）═══════════════════════
#
# 见 docs/P4.5-可写Web工作会话.md §10。可写/守卫/翻姿态用注入 fake/轻量 agent，不打真实 LLM；
# P2 的 gate/check 行为已由 test_gate/test_check 证过，这里只验「可写 turn 正确复用这些原语」。

from pathlib import Path  # noqa: E402

from agentao.capabilities.filesystem import LocalFileSystem  # noqa: E402

from guanlan.web.jobs import JobQueue, WriteGate  # noqa: E402
from guanlan.web.policy_fs import (  # noqa: E402
    PolicyFileSystem,
    _Rule,
    hash_file,
    make_policy_fs,
    restore_agentao,
    snapshot_agentao,
)


# ───────────────────────── 层①：PolicyFileSystem wrapper（决策P4.5-2/12）─────────────

def test_policy_fs_rejects_immutable_allows_writable(kb) -> None:
    fs = make_policy_fs(kb)
    for rel in ["raw/x.md", "raw/sub/y.md", "AGENTAO.md"]:
        with pytest.raises(PermissionError):
            fs.write_text(kb / rel, "x")
    # SCHEMA.md **不在** immutable 集（决策P4.5-12）+ wiki/ + workspace/ → 放行（真落盘）。
    fs.write_text(kb / "SCHEMA.md", "# new schema\n")
    assert (kb / "SCHEMA.md").read_text() == "# new schema\n"
    fs.write_text(kb / "wiki" / "entities" / "Foo.md", "foo")
    assert (kb / "wiki" / "entities" / "Foo.md").read_text() == "foo"
    fs.write_text(kb / "workspace" / "u" / "a.md", "a")
    assert (kb / "workspace" / "u" / "a.md").read_text() == "a"


def test_policy_fs_rejects_out_of_kb(kb) -> None:
    fs = make_policy_fs(kb)
    with pytest.raises(PermissionError):
        fs.write_text("/tmp/evil.md", "x")
    with pytest.raises(PermissionError):  # kb 根之外的兄弟路径（防御性 kb 包含，不委托上游）
        fs.write_text(kb.parent / "outside.md", "x")


def test_policy_fs_blocks_softlink_clobber(kb) -> None:
    """wiki/link → raw/secret 软链 clobber：叶解引用后命中 immutable，拒（不 fail-open）。"""
    secret = kb / "raw" / "secret.md"
    secret.write_text("s")
    link = kb / "wiki" / "link.md"
    link.symlink_to(secret)
    fs = make_policy_fs(kb)
    with pytest.raises(PermissionError):
        fs.write_text(str(link), "clobber")
    assert secret.read_text() == "s"  # 未被写穿


def test_policy_fs_coord_consistency_relative_kb(kb, monkeypatch) -> None:
    """kb 传相对路径：immutable 集须 __init__ resolve 到同坐标系，否则 fail-open（评审 Medium）。"""
    monkeypatch.chdir(kb.parent)
    rel_kb = Path(kb.name)
    fs = PolicyFileSystem(
        LocalFileSystem(), rel_kb, _Rule(immutable=(rel_kb / "raw", rel_kb / "AGENTAO.md"))
    )
    with pytest.raises(PermissionError):  # 绝对目标 vs（已规范化的）相对 immutable → 仍拒
        fs.write_text(kb / "raw" / "x.md", "x")


def test_policy_fs_coord_consistency_symlink_kb(kb) -> None:
    link_kb = kb.parent / "linked_kb"
    link_kb.symlink_to(kb)
    fs = make_policy_fs(link_kb)
    with pytest.raises(PermissionError):
        fs.write_text(str(link_kb / "raw" / "x.md"), "x")


def test_policy_fs_coord_consistency_symlink_raw(kb) -> None:
    real_raw = kb.parent / "real_raw"
    real_raw.mkdir()
    (kb / "raw").rmdir()
    (kb / "raw").symlink_to(real_raw)
    fs = make_policy_fs(kb)
    with pytest.raises(PermissionError):
        fs.write_text(kb / "raw" / "x.md", "x")


def test_policy_fs_passthrough_reads(kb) -> None:
    fs = make_policy_fs(kb)
    assert fs.exists(kb / "AGENTAO.md") is True
    assert fs.read_bytes(kb / "AGENTAO.md") == (kb / "AGENTAO.md").read_bytes()


def test_policy_fs_journal_wiki_and_schema_only(kb) -> None:
    """写日志只记 wiki/ + SCHEMA.md（撤销范围）；workspace/ 不入；同 path 多写保首条写前字节。"""
    fs = make_policy_fs(kb)
    fs.begin_journal()
    fs.write_text(kb / "wiki" / "entities" / "A.md", "a1")
    fs.write_text(kb / "wiki" / "entities" / "A.md", "a2")  # 二次写同 path
    fs.write_text(kb / "SCHEMA.md", "newschema")
    fs.write_text(kb / "workspace" / "w.md", "w")  # 不在撤销范围
    journal = fs.end_journal()
    assert {p.name for p in journal} == {"A.md", "SCHEMA.md"}
    a_path = (kb / "wiki" / "entities" / "A.md").resolve()
    before, after = journal[a_path]
    assert before is None  # 首次写时文件不存在
    assert after == hash_file(a_path)  # 写后哈希 = 末次内容（a2）
    s_path = (kb / "SCHEMA.md").resolve()
    s_before, _ = journal[s_path]
    assert s_before == b"# SCHEMA\n"  # SCHEMA 写前原字节


# ───────────────────────── 层②：AGENTAO.md 快照 + 自动还原（决策P4.5-3/c）─────────────

def test_snapshot_restore_agentao_content(kb) -> None:
    before = snapshot_agentao(kb)
    assert before.existed and before.data is not None  # present 三态
    (kb / "AGENTAO.md").write_text("HACKED")  # shell 直写旁路
    assert restore_agentao(kb, before) == "AGENTAO.md"
    assert (kb / "AGENTAO.md").read_bytes() == before.data
    assert restore_agentao(kb, snapshot_agentao(kb)) is None  # 干净 → 无误判


def test_restore_agentao_recreates_deleted(kb) -> None:
    before = snapshot_agentao(kb)
    (kb / "AGENTAO.md").unlink()
    assert restore_agentao(kb, before) == "AGENTAO.md"
    assert (kb / "AGENTAO.md").read_bytes() == before.data


def test_restore_agentao_removes_when_snapshot_absent(kb) -> None:
    (kb / "AGENTAO.md").unlink()
    before = snapshot_agentao(kb)  # absent：existed=False
    assert not before.existed
    (kb / "AGENTAO.md").write_text("appeared")
    assert restore_agentao(kb, before) == "AGENTAO.md"
    assert not (kb / "AGENTAO.md").exists()


def test_restore_agentao_symlink_replacement_no_writethrough(kb) -> None:
    """被换成 symlink → 先清替身再原子写回普通文件，**绝不顺 symlink 写穿**（评审 High）。"""
    before = snapshot_agentao(kb)
    target = kb.parent / "evil_target.md"
    target.write_text("ORIG")
    (kb / "AGENTAO.md").unlink()
    (kb / "AGENTAO.md").symlink_to(target)
    assert restore_agentao(kb, before) == "AGENTAO.md"
    assert not (kb / "AGENTAO.md").is_symlink()
    assert (kb / "AGENTAO.md").read_bytes() == before.data
    assert target.read_text() == "ORIG"  # symlink 目标未被写穿


def test_restore_agentao_dir_replacement(kb) -> None:
    before = snapshot_agentao(kb)
    (kb / "AGENTAO.md").unlink()
    (kb / "AGENTAO.md").mkdir()
    (kb / "AGENTAO.md" / "junk").write_text("j")
    assert restore_agentao(kb, before) == "AGENTAO.md"
    assert (kb / "AGENTAO.md").is_file()
    assert (kb / "AGENTAO.md").read_bytes() == before.data


def test_snapshot_agentao_unreadable_distinct_from_absent(kb) -> None:
    """存在但不可读 → existed=True/data=None（区别于 absent 的 existed=False，评审 P2）。"""
    path = kb / "AGENTAO.md"
    path.chmod(0o000)  # 收读权限，模拟起服后被 chmod
    try:
        snap = snapshot_agentao(path.parent)
    finally:
        path.chmod(0o644)  # 还权限，免污染后续/清理
    assert snap.existed and snap.data is None  # unreadable，不是 absent


def test_restore_agentao_unreadable_preserves_file(kb, monkeypatch) -> None:
    """快照不可读时收尾**绝不删**现存普通文件，且上抛 AgentaoRestoreError（修复 P2 数据丢失）。"""
    import guanlan.web.policy_fs as pfs

    path = kb / "AGENTAO.md"
    original = path.read_bytes()
    # 构造 unreadable 快照（不真改文件权限——直接造三态值，跨平台稳）。
    snap = pfs.AgentaoSnapshot(existed=True, data=None)
    with pytest.raises(pfs.AgentaoRestoreError):
        restore_agentao(kb, snap)
    assert path.is_file()  # 未被误删
    assert path.read_bytes() == original  # 内容原样保留


def test_restore_agentao_unreadable_cleans_symlink_replacement(kb) -> None:
    """快照不可读但被换成 symlink → 仍清替身（安全），再抛 AgentaoRestoreError（不顺 symlink 写穿）。"""
    import guanlan.web.policy_fs as pfs

    target = kb.parent / "evil_unreadable_target.md"
    target.write_text("ORIG")
    (kb / "AGENTAO.md").unlink()
    (kb / "AGENTAO.md").symlink_to(target)
    snap = pfs.AgentaoSnapshot(existed=True, data=None)
    with pytest.raises(pfs.AgentaoRestoreError):
        restore_agentao(kb, snap)
    assert not (kb / "AGENTAO.md").exists()  # symlink 替身已清
    assert target.read_text() == "ORIG"  # 目标未被写穿/删


# ───────────────────────── C0：层①地基（门槛闸，决策P4.5-2）──────────────────────────

def test_c0_builtin_write_routes_through_policy_fs(kb) -> None:
    """内建 write_file 终经 self.filesystem.write_text → PolicyFileSystem 真被命中（C0 ②）。"""
    from agentao.tools.file_ops import WriteFileTool

    fs = make_policy_fs(kb)
    tool = WriteFileTool()
    tool.working_directory = kb
    tool.filesystem = fs
    out = tool.execute(file_path=str(kb / "wiki" / "entities" / "X.md"), content="hi")
    assert "Successfully" in out
    assert (kb / "wiki" / "entities" / "X.md").read_text() == "hi"  # 经 wrapper 落盘
    # immutable：wrapper 拒 → 工具回错误串、文件未建。
    assert "Error" in tool.execute(file_path=str(kb / "raw" / "x.md"), content="bad")
    assert not (kb / "raw" / "x.md").exists()
    assert "Error" in tool.execute(file_path=str(kb / "AGENTAO.md"), content="bad")
    assert (kb / "AGENTAO.md").read_text() == "# AGENTAO\n"


def test_c0_filesystem_injected_into_agent(chat_client) -> None:
    """build_from_environment 收到 filesystem= 且为 PolicyFileSystem（C0 ①透传）。"""
    client, captured = chat_client
    _chat(client, "问")
    fs = captured["agents"][0].kwargs["filesystem"]
    assert isinstance(fs, PolicyFileSystem)


# ───────────────────────── /mode 翻姿态（决策P4.5-1/5）────────────────────────────────

def test_mode_switch_flips_two_points_no_rebuild(chat_client) -> None:
    client, captured = chat_client
    _, done, _ = _chat(client, "问")
    cid = done["conversation_id"]
    agent = captured["agents"][0]
    r = client.post(f"/api/chat/{cid}/mode", json={"mode": "workspace-write"})
    assert r.status_code == 200 and r.json()["mode"] == "workspace-write"
    assert any(
        c[0] == "set_mode" and c[1] == (PermissionMode.WORKSPACE_WRITE,)
        for c in agent.permission_engine.calls
    )
    assert any(
        c[0] == "set_readonly_mode" and c[1] == (False,) for c in agent.tool_runner.calls
    )
    assert client.get(f"/api/chat/{cid}/info").json()["mode"] == "workspace-write"
    assert len(captured["agents"]) == 1  # 同一 agent 对象、未重建
    assert client.post(f"/api/chat/{cid}/mode", json={"mode": "read-only"}).json()["mode"] == "read-only"


@pytest.mark.parametrize("bad", ["full-access", "plan", "full", "FULL", "nonsense", ""])
def test_mode_illegal_rejected_422(chat_client, bad) -> None:
    client, captured = chat_client
    _, done, _ = _chat(client, "问")
    cid = done["conversation_id"]
    assert client.post(f"/api/chat/{cid}/mode", json={"mode": bad}).status_code == 422
    agent = captured["agents"][0]
    assert not any(
        c[0] == "set_mode" and c[1] and c[1][0] in (PermissionMode.FULL_ACCESS, PermissionMode.PLAN)
        for c in agent.permission_engine.calls
    )


def test_mode_unknown_404(chat_client) -> None:
    client, _ = chat_client
    assert client.post(f"/api/chat/{uuid.uuid4()}/mode", json={"mode": "read-only"}).status_code == 404
    assert client.post("/api/chat/not-a-uuid/mode", json={"mode": "read-only"}).status_code == 404


def test_mode_cold_session_409(chat_env, kb) -> None:
    kb, _ = chat_env
    cold = str(uuid.uuid4())
    _write_foreign_session(kb, cold, active_skills=[SKILL_NAME])
    with TestClient(create_app(kb)) as client:
        assert client.post(f"/api/chat/{cold}/mode", json={"mode": "workspace-write"}).status_code == 409


def test_mode_subject_to_423_during_writable_turn(chat_client) -> None:
    """可写 turn 活跃时 /mode 必 423（评审 P2 自死锁修复）：agent 在持 conv.lock 的 turn 内
    `curl /mode` 不能去等同一把 conv.lock，否则互相空等、turn 永不收尾、锁永不释放。"""
    client, _ = chat_client
    _, done, _ = _chat(client, "问")
    cid = done["conversation_id"]
    client.app.state.write_gate.enter_writable()
    try:
        r = client.post(f"/api/chat/{cid}/mode", json={"mode": "workspace-write"})
        assert r.status_code == 423  # 可写 turn 活跃 → 拒、不取锁、断死锁
    finally:
        client.app.state.write_gate.exit_writable()
    # turn 收尾后恢复
    assert client.post(f"/api/chat/{cid}/mode", json={"mode": "read-only"}).status_code == 200


def test_info_reports_process_default_mode(chat_env, kb) -> None:
    kb, _ = chat_env
    with TestClient(create_app(kb, mode="workspace-write")) as client:
        assert client.get("/api/info").json()["mode"] == "workspace-write"


# ───────────────────────── 层③：宿主写端点时序互斥（决策P4.5-10）────────────────────

def test_layer3_423_when_writable_active(kb) -> None:
    app = create_app(kb)
    with TestClient(app) as client:
        app.state.write_gate.enter_writable()
        try:
            assert client.post("/api/raw", json={"name": "a", "content": "x\n"}).status_code == 423
            assert client.post("/api/ingest", json={"target": "raw/foo.md"}).status_code == 423
            assert client.post("/api/heal", json={}).status_code == 423
        finally:
            app.state.write_gate.exit_writable()
        # 计数归 0 → 端点恢复（投喂 200）。
        assert client.post("/api/raw", json={"name": "a", "content": "x\n"}).status_code == 200


def test_layer3_423_distinct_from_409_exists(kb) -> None:
    """423(锁定) 优先于 409(不覆盖)，二者可区分（决策P4.5-10）。"""
    app = create_app(kb)
    with TestClient(app) as client:
        (kb / "raw" / "a.md").write_text("existing")
        app.state.write_gate.enter_writable()
        try:
            assert client.post("/api/raw", json={"name": "a", "content": "x\n"}).status_code == 423
        finally:
            app.state.write_gate.exit_writable()
        assert client.post("/api/raw", json={"name": "a", "content": "x\n"}).status_code == 409


def test_graph_subject_to_423_during_writable_turn(kb) -> None:
    """可写 turn 活跃时 /graph 必 423（评审 P1 自死锁修复）：agent 在持 write_lock 的 turn 内
    `curl /graph` 不能去抢同一把不可重入锁，否则互相空等、write_lock 永不释放、后续写全卡死。"""
    app = create_app(kb)
    with TestClient(app) as client:
        assert client.get("/graph", follow_redirects=False).status_code == 302  # 无活跃可写 → 照常
        app.state.write_gate.enter_writable()
        try:
            r = client.get("/graph", follow_redirects=False)
            assert r.status_code == 423  # 可写 turn 活跃 → 拒、不抢锁、断死锁
        finally:
            app.state.write_gate.exit_writable()
        # turn 收尾后恢复
        assert client.get("/graph", follow_redirects=False).status_code == 302


# ───────────────────────── 单写者并发：两锁 + 报告 best-effort（决策P4.5-6）──────────

def test_write_gate_counter() -> None:
    g = WriteGate()
    assert g.active_writable_turns == 0
    g.enter_writable()
    g.enter_writable()
    assert g.active_writable_turns == 2
    g.exit_writable()
    assert g.active_writable_turns == 1
    g.exit_writable()
    g.exit_writable()  # 永不低于 0
    assert g.active_writable_turns == 0


def test_jobqueue_serializes_under_write_lock() -> None:
    """worker 跑 fn() 须持 write_lock：锁被外部持有时作业不完成，释放后才完成。"""
    lock = threading.Lock()
    jq = JobQueue(write_lock=lock)
    lock.acquire()
    done: list[int] = []
    jid = jq.enqueue("t", lambda: (done.append(1), 0)[1])
    job = jq.get_job(jid)
    assert not job.done_event.wait(0.3)  # 锁被持 → 卡住
    assert done == []
    lock.release()
    assert job.done_event.wait(2.0)  # 释放后完成
    assert done == [1]


def test_jobqueue_lock_separate_from_job_table() -> None:
    """write_lock 被持时 _lock 仍即时——enqueue/get_job 不被长写卡死（两锁分离）。"""
    lock = threading.Lock()
    jq = JobQueue(write_lock=lock)
    lock.acquire()
    try:
        jid = jq.enqueue("t", lambda: 0)  # 入队即返回（不被 write_lock 卡）
        assert jq.get_job(jid) is not None  # 查表即时返回
    finally:
        lock.release()


def test_report_endpoints_not_blocked_by_write_lock(kb) -> None:
    """报告端点 best-effort 读、不取 write_lock：长写持锁时仍即时 200（决策P4.5-6，评审 Medium）。"""
    app = create_app(kb)
    with TestClient(app) as client:
        app.state.write_gate.write_lock.acquire()
        try:
            for name in ("check", "health", "lint"):
                assert client.get(f"/api/report/{name}").status_code == 200
        finally:
            app.state.write_gate.write_lock.release()


def test_report_check_tolerates_bad_frontmatter(client, kb) -> None:
    """报告对坏 frontmatter 的瞬态页不崩、整体仍 200（非原子写中间态，filesystem.py:117）。"""
    (kb / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
    (kb / "wiki" / "entities" / "Bad.md").write_text("not valid frontmatter at all\n")
    assert client.get("/api/report/check").status_code == 200


# ───────────────────────── 可写 turn 收尾：check / undo / 层②（决策P4.5-3/4/13）──────

def _writable_app(chat_env):
    """build 一个 workspace-write 默认姿态的 app（注入 fake agent，不打 LLM）。"""
    kb, captured = chat_env
    return create_app(kb, mode="workspace-write"), kb, captured


def test_chat_writable_turn_subject_to_423(chat_env) -> None:
    """可写 turn 活跃时**会写的** /api/chat 必 423（评审 P1 嵌套可写 chat 自死锁修复）：
    新会话（默认 workspace-write）与已存在的 workspace-write 会话都拦——它们会取同一把 write_lock。"""
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done, _ = _chat(client, "建会话")  # 先建一个 workspace-write 会话
        cid = done["conversation_id"]
        app.state.write_gate.enter_writable()
        try:
            # 新会话（默认 workspace-write）→ create 前即拒
            assert client.post("/api/chat", json={"message": "hi"}).status_code == 423
            # 已存在的 workspace-write 会话 → 解析后按姿态拒
            assert client.post(
                "/api/chat", json={"message": "hi", "conversation_id": cid}
            ).status_code == 423
        finally:
            app.state.write_gate.exit_writable()


def test_chat_readonly_turn_not_rejected_during_writable(chat_env) -> None:
    """**只读** turn 不受层③（评审 P2）：只读 turn 不取 write_lock、跑各自 conv.lock，与活跃写者
    无锁交集、不死锁，故活跃可写期间仍放行——不误伤并发只读 chat / 默认只读姿态。"""
    kb, captured = chat_env
    app = create_app(kb, mode="read-only")  # 进程默认只读
    with TestClient(app) as client:
        _, done, _ = _chat(client, "建只读会话")
        cid = done["conversation_id"]
        app.state.write_gate.enter_writable()  # 模拟别处有可写 turn 在飞
        try:
            # 新只读会话照常完成（非 423）
            _, d1, e1 = _chat(client, "新只读问")
            assert e1 is None and d1 is not None
            # 已存在只读会话续聊照常完成（非 423）
            _, d2, e2 = _chat(client, "续聊", conversation_id=cid)
            assert e2 is None and d2 is not None
        finally:
            app.state.write_gate.exit_writable()


def test_writable_turn_runs_check_unconditionally(chat_env) -> None:
    """每条可写 turn 收尾无条件跑 check：干净 wiki → ok；agent 写坏页 → violations surface。"""
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done1, _ = _chat(client, "建会话")  # 本轮无写
        assert done1["check"]["ok"] is True  # check 仍无条件跑（干净）
        assert "undo" not in done1  # 无写 → 无撤销
        cid = done1["conversation_id"]
        agent = captured["agents"][0]

        def write_bad(a):  # 经 wrapper 写一个坏 frontmatter 的 wiki 页
            a.kwargs["filesystem"].write_text(
                kb / "wiki" / "entities" / "Bad.md", "garbage no frontmatter\n"
            )

        agent.action = write_bad
        _, done2, _ = _chat(client, "写页", conversation_id=cid)
        assert done2["check"]["ok"] is False
        assert any("Bad.md" in v["page"] for v in done2["check"]["violations"])
        assert "repair_prompt" in done2["check"]  # 「让 Agent 修复」用的下一轮消息
        assert done2["undo"]["available"] is True
        assert any("Bad.md" in p for p in done2["undo"]["paths"])
        assert (kb / "wiki" / "entities" / "Bad.md").exists()  # 不硬阻断：页照常写盘


def test_writable_turn_undo_restores_wiki_and_schema(chat_env) -> None:
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done1, _ = _chat(client, "建会话")
        cid = done1["conversation_id"]
        agent = captured["agents"][0]
        orig_schema = (kb / "SCHEMA.md").read_bytes()

        def write_two(a):
            fs = a.kwargs["filesystem"]
            fs.write_text(kb / "wiki" / "entities" / "New.md", "x")  # 新建页
            fs.write_text(kb / "SCHEMA.md", "changed schema\n")  # 改 SCHEMA

        agent.action = write_two
        _, done2, _ = _chat(client, "写", conversation_id=cid)
        token = done2["undo"]["token"]
        assert (kb / "wiki" / "entities" / "New.md").exists()
        r = client.post(f"/api/chat/{cid}/undo", json={"token": token})
        assert r.status_code == 200
        body = r.json()
        assert set(body["undone"]) == {"wiki/entities/New.md", "SCHEMA.md"}
        assert body["conflicts"] == []
        assert not (kb / "wiki" / "entities" / "New.md").exists()  # 新页被删
        assert (kb / "SCHEMA.md").read_bytes() == orig_schema  # SCHEMA 复原
        # 一次性：同 token 再撤 → 409
        assert client.post(f"/api/chat/{cid}/undo", json={"token": token}).status_code == 409


def test_writable_turn_undo_optimistic_conflict(chat_env) -> None:
    """撤销前文件被后续写改动（当前哈希 ≠ 本 turn 写后）→ 跳过 + 409，不覆盖后续写（评审 High）。"""
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done1, _ = _chat(client, "建会话")
        cid = done1["conversation_id"]
        agent = captured["agents"][0]

        def write_one(a):
            a.kwargs["filesystem"].write_text(kb / "wiki" / "entities" / "C.md", "v1")

        agent.action = write_one
        _, done2, _ = _chat(client, "写", conversation_id=cid)
        token = done2["undo"]["token"]
        (kb / "wiki" / "entities" / "C.md").write_text("v2-later")  # 模拟后续写
        r = client.post(f"/api/chat/{cid}/undo", json={"token": token})
        assert r.status_code == 409
        assert "wiki/entities/C.md" in r.json()["conflicts"]
        assert (kb / "wiki" / "entities" / "C.md").read_text() == "v2-later"  # 未被覆盖


def test_writable_turn_undo_available_independent_of_check(chat_env) -> None:
    """SCHEMA-only 改：check 通过（无 wiki violations）但撤销键仍可用（评审 Medium）。"""
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done1, _ = _chat(client, "建会话")
        cid = done1["conversation_id"]
        agent = captured["agents"][0]

        def write_schema(a):
            a.kwargs["filesystem"].write_text(kb / "SCHEMA.md", "newschema\n")

        agent.action = write_schema
        _, done2, _ = _chat(client, "改 schema", conversation_id=cid)
        assert done2["check"]["ok"] is True  # SCHEMA 不被 check 消费
        assert done2["undo"]["available"] is True
        assert done2["undo"]["paths"] == ["SCHEMA.md"]


def test_writable_turn_restores_agentao_md(chat_env) -> None:
    """层②：shell 直写 AGENTAO.md → turn 收尾自动还原 + done.immutable_mutated（决策P4.5-3）。"""
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done1, _ = _chat(client, "建会话")
        cid = done1["conversation_id"]
        agent = captured["agents"][0]
        orig = (kb / "AGENTAO.md").read_bytes()

        def shell_hack(a):  # 直接写盘（不经 wrapper）模拟 shell 旁路
            (kb / "AGENTAO.md").write_text("HACKED constitution")

        agent.action = shell_hack
        _, done2, _ = _chat(client, "改宪法", conversation_id=cid)
        assert done2["immutable_mutated"] == ["AGENTAO.md"]
        assert (kb / "AGENTAO.md").read_bytes() == orig  # 已自动还原
        assert "undo" not in done2  # 直写不经 wrapper → 不入写日志


def test_writable_turn_raw_shell_write_is_residual(chat_env) -> None:
    """raw/ 的 shell 直写是残留：层②不扫树、不还原、不入写日志（决策P4.5-3/11）。"""
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done1, _ = _chat(client, "建会话")
        cid = done1["conversation_id"]
        agent = captured["agents"][0]

        def raw_hack(a):
            (kb / "raw" / "injected.md").write_text("shell wrote this")

        agent.action = raw_hack
        _, done2, _ = _chat(client, "x", conversation_id=cid)
        assert "immutable_mutated" not in done2  # raw/ 不被层②扫
        assert "undo" not in done2  # raw/ 不入写日志
        assert (kb / "raw" / "injected.md").exists()  # 残留：未回滚


def test_read_only_turn_no_writable_meta(chat_env) -> None:
    """read-only turn 零开销：done 不带 check/undo/immutable_mutated。"""
    app, kb, captured = _writable_app(chat_env)
    # 进程默认 workspace-write，但本会话切回 read-only 后应无可写元数据。
    with TestClient(app) as client:
        _, done1, _ = _chat(client, "建会话")
        cid = done1["conversation_id"]
        client.post(f"/api/chat/{cid}/mode", json={"mode": "read-only"})
        _, done2, _ = _chat(client, "只读问", conversation_id=cid)
        assert "check" not in done2
        assert "undo" not in done2
        assert "immutable_mutated" not in done2


def test_undo_bogus_token_409_no_serialize(chat_env) -> None:
    """陈旧/空 token → 409（端点取 write_lock 前廉价短路，评审 BUG 4）。"""
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done1, _ = _chat(client, "建会话")  # 本轮无写 → 无 undo
        cid = done1["conversation_id"]
        # 无任何写日志：任意 token 都 409。
        assert client.post(f"/api/chat/{cid}/undo", json={"token": "nope"}).status_code == 409
        # 端点未取 write_lock（短路）：write_lock 仍可被立刻拿到。
        assert app.state.write_gate.write_lock.acquire(blocking=False) is True
        app.state.write_gate.write_lock.release()


def test_undo_unknown_session_404(chat_client) -> None:
    client, _ = chat_client
    assert client.post(f"/api/chat/{uuid.uuid4()}/undo", json={"token": "x"}).status_code == 404


def test_undo_subject_to_423_during_writable_turn(chat_env) -> None:
    """可写 turn 活跃时 /undo 必 423（评审 P2 自死锁修复）：agent 在持 conv.lock+write_lock 的
    turn 内 `curl /undo`（即便 token 瞎填）不能去等同一批锁，否则互相空等、turn 永不收尾。
    优先于 BUG4 的 409 短路：取任何锁前先按层③ 拒。"""
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done, _ = _chat(client, "建会话")
        cid = done["conversation_id"]
        app.state.write_gate.enter_writable()
        try:
            r = client.post(f"/api/chat/{cid}/undo", json={"token": "nope"})
            assert r.status_code == 423  # 层③ 先于 409 短路、不取锁、断死锁
        finally:
            app.state.write_gate.exit_writable()
        # turn 收尾后恢复到 BUG4 的 409（无写日志 → 陈旧 token）
        assert client.post(f"/api/chat/{cid}/undo", json={"token": "nope"}).status_code == 409


def test_graph_error_releases_write_lock(kb, monkeypatch) -> None:
    """/graph 写失败（OSError→500）后 write_lock 仍被释放（评审 P1：acquire 移入 try、finally 据 held 释放）。"""
    import guanlan.web.app as app_mod

    app = create_app(kb)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(app_mod, "build_and_write_graph", boom)
    with TestClient(app) as client:
        assert client.get("/graph", follow_redirects=False).status_code == 500
        assert app.state.write_gate.write_lock.acquire(blocking=False) is True  # 未泄漏
        app.state.write_gate.write_lock.release()


def test_graph_success_releases_write_lock(kb) -> None:
    app = create_app(kb)
    with TestClient(app) as client:
        assert client.get("/graph", follow_redirects=False).status_code == 302
        assert app.state.write_gate.write_lock.acquire(blocking=False) is True
        app.state.write_gate.write_lock.release()


def test_undo_success_releases_write_lock(chat_env) -> None:
    app, kb, captured = _writable_app(chat_env)
    with TestClient(app) as client:
        _, done1, _ = _chat(client, "建会话")
        cid = done1["conversation_id"]
        agent = captured["agents"][0]
        agent.action = lambda a: a.kwargs["filesystem"].write_text(
            kb / "wiki" / "entities" / "Z.md", "z"
        )
        _, done2, _ = _chat(client, "写", conversation_id=cid)
        client.post(f"/api/chat/{cid}/undo", json={"token": done2["undo"]["token"]})
        assert app.state.write_gate.write_lock.acquire(blocking=False) is True  # 撤销后未泄漏
        app.state.write_gate.write_lock.release()
