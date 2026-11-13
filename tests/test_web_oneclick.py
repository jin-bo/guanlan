"""P4.30 Web 一键入库端点：准备 → 确认 → 落盘 + 入库；暂停/继续/放弃；库级锁与 CLI 互斥；reader 裁剪。"""

from __future__ import annotations

import os
import re
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
    def convert(src, *, stem, backend, cwd, progress=None, **kw):
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
    # 浮层进度每行带 [HH:MM:SS]（解析、审校目录都是分钟级，要看得出每步何时开始）
    assert all(re.match(r"\[\d\d:\d\d:\d\d\] ", ln) for ln in job["output"].splitlines() if ln.strip())
    active = client.get("/api/oneclick").json()["active"]
    assert active["state"] == "awaiting_confirm"
    pi = active["progress"]  # 任务卡进度：共几篇、完成几篇、预计剩余（等待确认时还没有当前篇）
    assert pi["total"] == len(active["items"]) and pi["done"] == 0 and pi["current"] is None
    assert pi["remaining"] == pi["total"] and pi["eta_seconds"] > 0
    return active


def test_prepare_then_confirm_lands_and_ingests(kb, fake_convert, ingest):
    with TestClient(create_app(kb)) as client:
        task = _prepare(client)
        assert len(task["items"]) == 3
        assert list((kb / "raw").iterdir()) == []
        meta = yaml.safe_load(split_frontmatter((kb / "workspace" / "parsed" / "书.md").read_text("utf-8"))[0])
        assert meta["parsed_by"] == "mineru" and meta["origin"] == "workspace/uploads/书.pdf"
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


# ── #172：上传不排在一键入库后面 ─────────────────────────────────────────────────


def test_upload_completes_while_oneclick_is_ingesting(kb, fake_convert, monkeypatch):
    """一键入库作业正在入第一篇（占着写作业队列和写锁）时，上传另一本立即完成，不等整本入完。"""
    gate = threading.Event()
    fake = FakeIngest(gate=gate)
    monkeypatch.setattr(oneclick_routes, "run_ingest", fake)
    with TestClient(create_app(kb)) as client:
        task = _prepare(client)
        job_id = client.post(f"/api/oneclick/{task['task_id']}/confirm", json={}).json()["job_id"]
        assert fake.started.wait(5)
        try:
            r = client.post("/api/upload", files={"file": ("第二本.pdf", b"%PDF two", "application/pdf")})
            assert r.status_code == 200, r.text
            assert fake.calls == []  # 第一篇还卡在 gate 上：上传没有排在入库后面
            assert (kb / "workspace" / "uploads" / "第二本.pdf").read_bytes() == b"%PDF two"
        finally:
            gate.set()
        assert _wait(client, job_id)["exit_code"] == 0


def test_upload_ignores_cli_kb_lock(kb):
    """上传只写 workspace/uploads/，CLI 不写这里：库被 CLI 占着时照样能传（不再 409「知识库正被占用」）。"""
    lock = acquire(kb, "cli: ingest")
    try:
        with TestClient(create_app(kb)) as client:
            assert _upload(client) == "workspace/uploads/书.pdf"
    finally:
        lock.release()


def test_upload_refuses_to_replace_original_being_prepared(kb, monkeypatch):
    """准备作业正在解析的原件不能被同名上传换掉（409）；别的文件照传。准备完成后同名也能传。"""
    release = threading.Event()
    started = threading.Event()

    def convert(src, *, stem, backend, cwd, progress=None, **kw):
        started.set()
        release.wait(5)
        return ConvertResult(markdown=_book(), backend="mineru")

    monkeypatch.setattr(oneclick, "convert_to_markdown", convert)
    with TestClient(create_app(kb)) as client:
        up = _upload(client)
        job_id = client.post("/api/oneclick", json={"upload": up}).json()["job_id"]
        assert started.wait(5)
        try:
            r = client.post("/api/upload", files={"file": ("书.pdf", b"%PDF new", "application/pdf")})
            assert r.status_code == 409 and "准备" in r.json()["detail"]
            assert (kb / "workspace" / "uploads" / "书.pdf").read_bytes() == b"%PDF fake"
            assert _upload(client, "另一本.pdf") == "workspace/uploads/另一本.pdf"
        finally:
            release.set()
        assert _wait(client, job_id)["exit_code"] == 0
        assert client.post("/api/upload", files={"file": ("书.pdf", b"%PDF new", "application/pdf")}).status_code == 200
    leftovers = [p.name for p in (kb / "workspace" / "uploads").iterdir() if p.name.startswith(".")]
    assert leftovers == []  # 被拒的那次不留临时文件


def test_upload_refuses_original_of_preparing_task_on_disk(kb):
    """盘上「准备中」的任务（如 CLI `guanlan import workspace/uploads/…` 正在解析）同样拦同名覆盖。"""
    (kb / "workspace" / "uploads").mkdir(parents=True)
    (kb / "workspace" / "uploads" / "书.pdf").write_bytes(b"%PDF old")
    oneclick.save_task(
        kb,
        {"task_id": "20261111-120000-书", "state": "preparing", "upload": "workspace/uploads/书.pdf", "created": "x"},
    )
    lock = acquire(kb, "cli: import")  # 执行者还活着：Web 启动时不会把它恢复成失败
    try:
        with TestClient(create_app(kb)) as client:
            r = client.post("/api/upload", files={"file": ("书.pdf", b"%PDF new", "application/pdf")})
            assert r.status_code == 409 and "20261111-120000-书" in r.json()["detail"]
            assert _upload(client, "别的.pdf") == "workspace/uploads/别的.pdf"
    finally:
        lock.release()
    assert (kb / "workspace" / "uploads" / "书.pdf").read_bytes() == b"%PDF old"


def test_upload_ignores_stale_preparing_task(kb):
    """残留的「准备中」（执行者已退出、没人持锁）不拦同名上传：否则「重新上传再一键入库」永远 409。"""
    (kb / "workspace" / "uploads").mkdir(parents=True)
    (kb / "workspace" / "uploads" / "书.pdf").write_bytes(b"%PDF old")
    with TestClient(create_app(kb)) as client:
        # 启动恢复之后才出现的残留（如 CLI 被 kill）：清单停在「准备中」，锁已无人持有
        oneclick.save_task(
            kb,
            {"task_id": "20261111-120000-书", "state": "preparing", "upload": "workspace/uploads/书.pdf", "created": "x"},
        )
        assert oneclick.active_task(kb)["state"] == "preparing"
        r = client.post("/api/upload", files={"file": ("书.pdf", b"%PDF new", "application/pdf")})
        assert r.status_code == 200, r.text
    assert (kb / "workspace" / "uploads" / "书.pdf").read_bytes() == b"%PDF new"


def test_same_file_matches_by_identity_not_spelling(tmp_path):
    """同一文件的另一个名字（大小写不敏感 FS 上的 `X.PDF`/`x.pdf`，这里用硬链接模拟）也算同一份原件。"""
    a = tmp_path / "书.PDF"
    a.write_bytes(b"x")
    b = tmp_path / "书.pdf.link"
    os.link(a, b)
    assert oneclick_routes._same_file(a, b)
    assert not oneclick_routes._same_file(a, tmp_path / "另一本.pdf")  # 不存在：退回比路径


# ── #173：准备阶段「停止」 ────────────────────────────────────────────────────


@pytest.fixture
def stoppable_convert(monkeypatch):
    """假转换：一直跑到停止标志置位（模拟 MinerU 解析扫描版的几分钟），按 stopscope 的口径抛 StopRequested。"""
    from guanlan import stopscope

    started = threading.Event()

    def convert(src, *, stem, backend, cwd, progress=None, **html_opts):  # P4.31 起另传 html_* 参数
        started.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            stopscope.check()
            time.sleep(0.02)
        raise AssertionError("没收到停止")

    monkeypatch.setattr(oneclick, "convert_to_markdown", convert)
    return started


def test_stop_preparing_task_from_card(kb, stoppable_convert):
    """任务卡：「准备中」可停止（走 /pause），任务记已放弃，库立即可用、可重新导入。"""
    with TestClient(create_app(kb)) as client:
        up = _upload(client)
        job_id = client.post("/api/oneclick", json={"upload": up}).json()["job_id"]
        assert stoppable_convert.wait(5)
        active = client.get("/api/oneclick").json()["active"]
        assert active["state"] == "preparing" and active["stoppable"] and not active["stopping"]
        r = client.post(f"/api/oneclick/{active['task_id']}/pause")
        assert r.status_code == 200 and r.json() == {"stopping": True}
        job = _wait(client, job_id)
        assert job["exit_code"] == 1 and "已按要求停止" in job["output"]
        assert client.get("/api/oneclick").json()["active"] is None
        task = client.get(f"/api/oneclick/{active['task_id']}").json()
        assert task["state"] == "cancelled" and not task["stoppable"]
        # 写锁已释放：重新导入不 409
        assert client.post("/api/oneclick", json={"upload": up}).status_code == 200


def test_stop_prepare_by_job_id(kb, stoppable_convert):
    """解析进度浮层只知道作业号：按作业号停止；作业结束后再停 → 409。"""
    with TestClient(create_app(kb)) as client:
        up = _upload(client)
        job_id = client.post("/api/oneclick", json={"upload": up}).json()["job_id"]
        assert stoppable_convert.wait(5)
        assert client.post("/api/oneclick/prepare/other-job/stop").status_code == 409
        assert client.post(f"/api/oneclick/prepare/{job_id}/stop").json() == {"stopping": True}
        assert _wait(client, job_id)["exit_code"] == 1
        (task,) = oneclick.iter_tasks(kb)
        assert task["state"] == "cancelled"
        assert client.post(f"/api/oneclick/prepare/{job_id}/stop").status_code == 409


def test_pause_still_409_for_preparing_task_not_ours(kb):
    """盘上「准备中」但不是本进程在跑（如 CLI 正在准备）：不给停止，/pause 仍 409。"""
    (kb / "workspace" / "uploads").mkdir(parents=True)
    (kb / "workspace" / "uploads" / "书.pdf").write_bytes(b"%PDF")
    oneclick.save_task(
        kb,
        {"task_id": "20261111-120000-书", "state": "preparing", "upload": "workspace/uploads/书.pdf", "created": "x"},
    )
    lock = acquire(kb, "cli: import")
    try:
        with TestClient(create_app(kb)) as client:
            active = client.get("/api/oneclick").json()["active"]
            assert active["state"] == "preparing" and not active["stoppable"]
            assert client.post("/api/oneclick/20261111-120000-书/pause").status_code == 409
    finally:
        lock.release()


# ── P4.32：CLI 发起的目录批量任务在 Web 里 ──────────────────────────────────


def _batch_dir(kb: Path) -> Path:
    d = kb / "src" / "报告集"
    d.mkdir(parents=True)
    (d / "上册.md").write_text(_book(), encoding="utf-8")
    (d / "下篇.md").write_text("# 下篇\n\n" + _para(3_000) + "\n", encoding="utf-8")
    return d


def _prepare_batch(kb: Path) -> dict:
    with acquire(kb, "cli: import"):
        return oneclick.prepare_batch(kb, _batch_dir(kb), origin_kind="cli")


def test_batch_task_shown_but_confirm_refused(kb, ingest):
    """批量只在终端确认（P4.32 §8）：任务卡看得到原件分组，确认端点直接 409，任务仍等待确认，放弃照常可用。"""
    task = _prepare_batch(kb)
    with TestClient(create_app(kb)) as client:
        active = client.get("/api/oneclick").json()["active"]
        assert active["kind"] == "batch" and [d["sample"] for d in active["docs"]] == ["上册", "下篇"]
        assert [it["doc"] for it in active["items"]] == [0, 0, 0, 1]
        r = client.post(f"/api/oneclick/{task['task_id']}/confirm", json={})
        assert r.status_code == 409 and "--resume" in r.json()["detail"]
        assert oneclick.load_task(kb, task["task_id"])["state"] == "awaiting_confirm"
        assert ingest.calls == [] and list((kb / "raw").iterdir()) == []
        assert client.post(f"/api/oneclick/{task['task_id']}/cancel").status_code == 200
    assert oneclick.active_task(kb) is None


def test_web_resumes_paused_batch_task(kb, ingest):
    """CLI 发起、失败暂停的批量任务可在 Web 继续（P4.30「CLI 发起的任务可在 Web 里继续」不因批量而破）。"""
    task = _prepare_batch(kb)

    def fail_second(raw_rel: str, root: Path) -> int:
        return 5 if raw_rel == "raw/下篇.md" else ingest(raw_rel, root=root)

    with acquire(kb, "cli: import"):
        code, task = oneclick.confirm(kb, task["task_id"], ingest=fail_second)
    assert code == 5 and task["state"] == "paused"
    with TestClient(create_app(kb)) as client:
        r = client.post(f"/api/oneclick/{task['task_id']}/resume", json={})
        job = _wait(client, r.json()["job_id"])
        assert job["exit_code"] == 0, job["output"]
        done = client.get(f"/api/oneclick/{task['task_id']}").json()
        assert done["state"] == "done" and done["progress"]["docs_total"] == 2
    assert ingest.calls[-1] == "raw/下篇.md"


def test_upload_refuses_original_of_preparing_batch(kb):
    """批量准备中：顶层 upload 是目录，被读的原件在 `docs[*].upload`，同名上传覆盖同样要拦（#172）。"""
    uploads = kb / "workspace" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "书.pdf").write_bytes(b"%PDF old")
    oneclick.save_task(
        kb,
        {
            "task_id": "20261112-120000-uploads-批量",
            "kind": "batch",
            "state": "preparing",
            "upload": "workspace/uploads",
            "docs": [{"upload": "workspace/uploads/甲.pdf", "sample": "甲"}, {"upload": "workspace/uploads/书.pdf", "sample": "书"}],
            "created": "x",
        },
    )
    lock = acquire(kb, "cli: import")
    try:
        with TestClient(create_app(kb)) as client:
            r = client.post("/api/upload", files={"file": ("书.pdf", b"%PDF new", "application/pdf")})
            assert r.status_code == 409 and "uploads-批量" in r.json()["detail"]
            assert _upload(client, "别的.pdf") == "workspace/uploads/别的.pdf"
    finally:
        lock.release()
    assert (uploads / "书.pdf").read_bytes() == b"%PDF old"
