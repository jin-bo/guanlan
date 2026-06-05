"""P4 Web 宿主测试（见 docs/P4-Web宿主.md §11）。**两类 LLM 都打桩，不打真实 LLM。**

宿主测试用 `fastapi.testclient.TestClient`（进程内、无 socket）+ 临时知识库。缺 web extra
时整组 `pytest.importorskip("fastapi")` 跳过。
"""

import socket
import sys

import pytest

from guanlan.errors import GuanlanError

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from guanlan.web.app import STATIC_DIR, create_app  # noqa: E402


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
