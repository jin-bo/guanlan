"""P4.30 Web 一键入库端点：准备 → 确认 → 落盘 + 入库；暂停/继续/放弃；库级锁与 CLI 互斥；reader 裁剪。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from guanlan import oneclick  # noqa: E402
from guanlan.imageio import ConvertResult  # noqa: E402
from guanlan.kblock import acquire  # noqa: E402
from guanlan.pages import split_frontmatter  # noqa: E402
from guanlan.provenance import compute_raw_digest, format_digest_value  # noqa: E402
from guanlan.rawio import raw_slug  # noqa: E402
from guanlan.web import oneclick_routes  # noqa: E402
from guanlan.web.app import create_app  # noqa: E402


def _para(n: int) -> str:
    return ("文" * 49 + "。") * (n // 50)


def _book(chapters: int = 3) -> str:
    out = ["# 示例书", ""]
    for i in range(1, chapters + 1):
        out += [f"## 第{i}章", ""] + [_para(2_000), ""] * 8
    return "\n".join(out)


def _wait(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["state"] == "done":
            return data
        time.sleep(0.02)
    raise AssertionError(f"作业 {job_id} 未完成")


class FakeIngest:
    def __init__(self, fail: set[str] | None = None, gate: threading.Event | None = None) -> None:
        self.calls: list[str] = []
        self.fail = set(fail or ())
        self.gate = gate
        self.started = threading.Event()

    def __call__(self, raw_rel: str, *, root, model=None, runner=None) -> int:
        self.started.set()
        if self.gate is not None:
            self.gate.wait(5)
        self.calls.append(raw_rel)
        if raw_rel in self.fail:
            return 5
        root = Path(root)
        stem = raw_slug(Path(raw_rel).stem)
        page = root / "wiki" / "sources" / f"{stem}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        digest = format_digest_value(raw_rel, compute_raw_digest(root / raw_rel))
        page.write_text(
            f"---\ntitle: {stem}\ntype: source\ntags: []\nsources: []\nlast_updated: 2026-11-10\n"
            f"raw_digest: '{digest}'\n---\n\n摘要\n",
            encoding="utf-8",
        )
        return 0


@pytest.fixture
def fake_convert(monkeypatch):
    def convert(src, *, stem, backend, cwd, progress=None):
        if progress:
            progress("[mineru] 转换中…")
        return ConvertResult(markdown=_book(), backend="mineru")

    monkeypatch.setattr(oneclick, "convert_to_markdown", convert)


@pytest.fixture
def ingest(monkeypatch):
    fake = FakeIngest()
    monkeypatch.setattr(oneclick_routes, "run_ingest", fake)
    return fake


def _upload(client, name="书.pdf") -> str:
    r = client.post("/api/upload", files={"file": (name, b"%PDF fake", "application/pdf")})
    assert r.status_code == 200
    return r.json()["saved"]


def _prepare(client) -> dict:
    up = _upload(client)
    r = client.post("/api/oneclick", json={"upload": up})
    assert r.status_code == 200
    job = _wait(client, r.json()["job_id"])
    assert job["exit_code"] == 0, job["output"]
    assert "[mineru] 转换中…" in job["output"]
    active = client.get("/api/oneclick").json()["active"]
    assert active["state"] == "awaiting_confirm"
    return active


def test_prepare_then_confirm_lands_and_ingests(kb, fake_convert, ingest):
    with TestClient(create_app(kb)) as client:
        task = _prepare(client)
        assert len(task["items"]) == 3
        assert list((kb / "raw").iterdir()) == []
        meta = yaml.safe_load(split_frontmatter((kb / "workspace" / "parsed" / "书.md").read_text("utf-8"))[0])
        assert meta["parsed_by"] == "mineru" and meta["origin"] == "uploads/书.pdf"
        # 「已解析」里能预览分卷
        part = "workspace/" + task["items"][0]["parsed"]
        assert client.get("/api/workspace/file", params={"path": part}).status_code == 200
        r = client.post(f"/api/oneclick/{task['task_id']}/confirm", json={})
        job = _wait(client, r.json()["job_id"])
        assert job["exit_code"] == 0, job["output"]
        assert "[3/3] 入库" in job["output"]
        assert client.get("/api/oneclick").json()["active"] is None
        done = client.get(f"/api/oneclick/{task['task_id']}").json()
        assert done["state"] == "done" and done["done"] == 3
        assert any("一键入库完成" in ln for ln in done["log_tail"])
    assert ingest.calls == [it["raw"] for it in task["items"]]
    assert all((kb / it["raw"]).is_file() for it in task["items"])


def test_failure_pauses_then_resume(kb, fake_convert, monkeypatch):
    fake = FakeIngest(fail={"raw/书/书-02-第2章.md"})
    monkeypatch.setattr(oneclick_routes, "run_ingest", fake)
    with TestClient(create_app(kb)) as client:
        task = _prepare(client)
        job = _wait(client, client.post(f"/api/oneclick/{task['task_id']}/confirm", json={}).json()["job_id"])
        assert job["exit_code"] == 5
        active = client.get("/api/oneclick").json()["active"]
        assert active["state"] == "paused"
        fake.fail.clear()
        job = _wait(client, client.post(f"/api/oneclick/{task['task_id']}/resume").json()["job_id"])
        assert job["exit_code"] == 0
    assert fake.calls == ["raw/书/书-01-第1章.md", "raw/书/书-02-第2章.md", "raw/书/书-02-第2章.md", "raw/书/书-03-第3章.md"]


def test_pause_takes_effect_between_items(kb, fake_convert, monkeypatch):
    gate = threading.Event()
    fake = FakeIngest(gate=gate)
    monkeypatch.setattr(oneclick_routes, "run_ingest", fake)
    with TestClient(create_app(kb)) as client:
        task = _prepare(client)
        tid = task["task_id"]
        job_id = client.post(f"/api/oneclick/{tid}/confirm", json={}).json()["job_id"]
        assert fake.started.wait(5)  # 第一篇已开始，再请求暂停
        assert client.post(f"/api/oneclick/{tid}/pause").json() == {"pausing": True}
        assert client.get("/api/oneclick").json()["active"]["pausing"] is True
        gate.set()
        job = _wait(client, job_id)
        assert job["exit_code"] == 0
        active = client.get("/api/oneclick").json()["active"]
        assert active["state"] == "paused" and active["done"] == 1
    assert len(fake.calls) == 1


def test_cancel_and_conflict_rules(kb, fake_convert, ingest):
    with TestClient(create_app(kb)) as client:
        task = _prepare(client)
        up = "workspace/uploads/书.pdf"
        assert client.post("/api/oneclick", json={"upload": up}).status_code == 409  # 唯一任务
        assert client.post(f"/api/oneclick/{task['task_id']}/cancel").json() == {"cancelled": task["task_id"]}
        assert client.get("/api/oneclick").json()["active"] is None
        assert (kb / "workspace" / "parsed" / "书.md").is_file()  # parsed/ 产物保留
        assert list((kb / "raw").iterdir()) == []


def test_web_job_refused_while_cli_holds_kb_lock(kb, fake_convert, ingest):
    with TestClient(create_app(kb)) as client:
        up = _upload(client)
        lock = acquire(kb, "cli: ingest")
        try:
            r = client.post("/api/oneclick", json={"upload": up})
            job = _wait(client, r.json()["job_id"])
            assert job["exit_code"] == 1
            assert "cli: ingest" in job["output"]
            assert client.get("/api/oneclick").json()["holder"].startswith("cli: ingest")
        finally:
            lock.release()
        job = _wait(client, client.post("/api/oneclick", json={"upload": up}).json()["job_id"])
        assert job["exit_code"] == 0


def test_web_releases_kb_lock_between_writes(kb, fake_convert, ingest):
    """Web 只在写操作期间持库级锁：作业结束后 CLI 可以拿到锁。"""
    with TestClient(create_app(kb)) as client:
        _prepare(client)
        acquire(kb, "cli").release()


def test_bad_upload_and_backend(kb):
    with TestClient(create_app(kb)) as client:
        assert client.post("/api/oneclick", json={"upload": "workspace/uploads/none.pdf"}).status_code == 404
        assert client.post("/api/oneclick", json={"upload": "raw/x.md"}).status_code == 400
        assert client.post("/api/oneclick", json={"upload": "workspace/uploads/x.pdf", "backend": "gpt"}).status_code == 422


def test_reader_does_not_register_oneclick(kb):
    with TestClient(create_app(kb, reader=True)) as c:
        assert c.get("/api/oneclick").status_code == 404
        assert c.post("/api/oneclick", json={"upload": "workspace/uploads/x.pdf"}).status_code in (404, 405)


def test_startup_recovers_stale_running_task(kb, fake_convert, ingest):
    with TestClient(create_app(kb)) as client:
        task = _prepare(client)
    t = oneclick.load_task(kb, task["task_id"])
    t["state"] = "running"
    oneclick.save_task(kb, t)
    with TestClient(create_app(kb)) as client:
        assert client.get("/api/oneclick").json()["active"]["state"] == "paused"


def test_graph_rebuild_409_while_cli_holds_kb_lock(kb):
    with TestClient(create_app(kb)) as client:
        lock = acquire(kb, "cli: heal")
        try:
            r = client.get("/graph", follow_redirects=False)
            assert r.status_code == 409 and "cli: heal" in r.json()["detail"]
        finally:
            lock.release()
        assert client.get("/graph", follow_redirects=False).status_code == 302
