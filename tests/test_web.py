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

    write_page(kb, "wiki/sources/s13-洗钱战法.md", type="source", body="正文足够长的内容。")
    wiki = kb / "wiki"

    # 路径+反引号、裸 stem+反引号 → 都联链到同一页，显示干净 stem（去 sources/ 与 .md）
    for text in ("引自 `wiki/sources/s13-洗钱战法.md`", "见 `s13-洗钱战法`"):
        html = render_markdown(text, wiki)
        assert 'data-page="wiki/sources/s13-洗钱战法.md"' in html
        assert ">s13-洗钱战法</a>" in html
        assert "<code>" not in html  # 已从 code 转成 a

    # 含空格的合法页名也应成链（不能被"有空格就跳过"误杀）
    write_page(kb, "wiki/concepts/Smart Tools 模块.md", type="concept", body="正文足够长。")
    for text in ("见 `Smart Tools 模块`", "见 `wiki/concepts/Smart Tools 模块.md`"):
        html = render_markdown(text, wiki)
        assert 'data-page="wiki/concepts/Smart Tools 模块.md"' in html
        assert "<code>" not in html

    # 解析不到的普通代码 / 命令（含某页末段但整体非忠实引用）/ 围栏代码块 → 保持字面 code
    assert "<code>git status</code>" in render_markdown("跑 `git status`", wiki)
    assert "<code>cat wiki/sources/s13-洗钱战法.md</code>" in render_markdown(
        "`cat wiki/sources/s13-洗钱战法.md`", wiki
    )
    fenced = render_markdown("```\nwiki/sources/s13-洗钱战法.md\n```", wiki)
    assert "wikilink" not in fenced  # 围栏代码块字面保留（决策P4-3）

    # 不给 wiki（无解析集）→ 行内 code 原样，不联链
    assert "<code>" in render_markdown("引自 `wiki/sources/s13-洗钱战法.md`")

    # code 当 markdown 链接文字 → 不得转成嵌套锚（保留外层 [..](url)，内层留 code）
    nested = render_markdown("[`wiki/sources/s13-洗钱战法.md`](https://example.com)", wiki)
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
        self.closed = False

    async def arun(self, msg: str, **_kw) -> str:
        self.messages.append({"role": "user", "content": msg})
        n = sum(1 for m in self.messages if m.get("role") == "user")
        answer = f"#{n} 回应：{msg}"  # 含轮次 → 第二轮答案体现累积历史

        loop = asyncio.get_running_loop()

        def work() -> None:  # 在线程池线程发事件（镜像真 arun）
            for ch in answer:
                self.transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": ch}))

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
