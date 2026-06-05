"""P4 Web 宿主测试（见 docs/P4-Web宿主.md §11）。**两类 LLM 都打桩，不打真实 LLM。**

宿主测试用 `fastapi.testclient.TestClient`（进程内、无 socket）+ 临时知识库。缺 web extra
时整组 `pytest.importorskip("fastapi")` 跳过。
"""

import socket
import sys

import pytest

from guanlan.errors import GuanlanError

from conftest import write_page

pytest.importorskip("fastapi")

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
