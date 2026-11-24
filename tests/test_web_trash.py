"""P4.36 T1 Web 回收区：列表、详情、彻底删除（`guanlan/web/trash_routes.py`）。零 LLM。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import write_page

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from guanlan import kblock  # noqa: E402
from guanlan.remove import remove_entrypoint  # noqa: E402
from guanlan.web.app import create_app  # noqa: E402

_CLI_HOLDER = """
import sys
from pathlib import Path
from guanlan import kblock
lock = kblock.acquire(Path(sys.argv[1]), "cli")
print("held", flush=True)
sys.stdin.read()
"""


def _removed(root: Path) -> str:
    (root / "raw" / "foo.md").write_text("原文", encoding="utf-8")
    write_page(root, "wiki/sources/foo.md", type="source")
    (root / "raw" / "bar.md").write_text("原文", encoding="utf-8")
    write_page(root, "wiki/sources/bar.md", type="source")
    write_page(root, "wiki/concepts/multi.md", sources="[foo, bar]")
    assert remove_entrypoint(root, src="foo", yes=True, json_output=False) == 0
    [entry] = sorted((root / ".trash").iterdir())
    return entry.name


def test_list_detail_purge(kb) -> None:
    name = _removed(kb)
    with TestClient(create_app(kb)) as client:
        data = client.get("/api/trash").json()
        assert [e["name"] for e in data["entries"]] == [name] and data["size"] > 0
        d = client.get(f"/api/trash/{name}").json()
        assert d["kind"] == "remove" and d["state"] == "complete"
        assert d["slug_dropped_from"][0]["page"] == "wiki/concepts/multi.md"
        doc = client.get("/api/doctor").json()
        assert doc["trash"]["count"] == 1
        r = client.post("/api/trash/purge", json={"name": name})
        assert r.status_code == 200 and r.json()["purged"] == name
        assert client.get("/api/trash").json()["entries"] == []
        assert client.get("/api/doctor").json()["trash"] == {"count": 0, "size": 0}
    assert not (kb / ".trash" / name).exists()


def test_errors(kb) -> None:
    name = _removed(kb)
    (kb / ".trash" / name / "restore.json").write_text('{"started_at": "x"}', encoding="utf-8")  # 恢复中 → 受保护
    with TestClient(create_app(kb)) as client:
        assert client.get("/api/trash/nope").status_code == 404
        assert client.post("/api/trash/purge", json={"name": "nope"}).status_code == 404
        assert client.post("/api/trash/purge", json={"name": "a/b"}).status_code == 400
        assert client.post("/api/trash/purge", json={"name": ".."}).status_code == 400
        r = client.post("/api/trash/purge", json={"name": name})
        assert r.status_code == 409 and r.json()["detail"]["reasons"]
    assert (kb / ".trash" / name).is_dir()


def test_reader_mode(kb) -> None:
    _removed(kb)
    with TestClient(create_app(kb, reader=True)) as client:
        assert client.get("/api/trash").status_code in (404, 405)
        assert client.post("/api/trash/purge", json={"name": "x"}).status_code in (404, 405)
        assert "trash" not in client.get("/api/doctor").json()


def test_writable_turn_is_423(kb) -> None:
    name = _removed(kb)
    with TestClient(create_app(kb)) as client:
        gate = client.app.state.write_gate
        gate.enter_writable()
        try:
            assert client.post("/api/trash/purge", json={"name": name}).status_code == 423
        finally:
            gate.exit_writable()
    assert (kb / ".trash" / name).is_dir()


def test_busy_write_task_is_409(kb) -> None:
    name = _removed(kb)
    with TestClient(create_app(kb)) as client:
        lock = client.app.state.write_gate.write_lock
        lock.acquire()
        try:
            assert client.post("/api/trash/purge", json={"name": name}).status_code == 409
        finally:
            lock.release()
    assert (kb / ".trash" / name).is_dir()


def test_degraded_kb_lock_is_409(kb, monkeypatch) -> None:
    """库级锁文件建不出：Web 写锁退化为进程内锁。删除不接受这种退化，条目仍在（§4.1）。"""
    name = _removed(kb)

    def no_lockfile(root, origin):
        raise OSError("只读介质")

    with TestClient(create_app(kb)) as client:
        monkeypatch.setattr(kblock, "acquire", no_lockfile)
        assert client.post("/api/trash/purge", json={"name": name}).status_code == 409
    assert (kb / ".trash" / name).is_dir()


def test_cli_holding_lock_is_409(kb) -> None:
    name = _removed(kb)
    env = {k: v for k, v in os.environ.items() if k != "GUANLAN_KB_LOCK_TOKEN"}
    proc = subprocess.Popen(
        [sys.executable, "-c", _CLI_HOLDER, str(kb)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env
    )
    try:
        assert proc.stdout.readline().strip() == "held"
        with TestClient(create_app(kb)) as client:
            assert client.post("/api/trash/purge", json={"name": name}).status_code == 409
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)
    assert (kb / ".trash" / name).is_dir()


def test_frontend_wired() -> None:
    static = Path(__file__).resolve().parents[1] / "guanlan" / "web" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    assert "/static/trash.js" in html and 'data-tab="trash"' in html
    js = (static / "trash.js").read_text(encoding="utf-8")
    assert "/api/trash/purge" in js and "confirm(" not in js  # 二次确认在浮层里，不弹浏览器 confirm
