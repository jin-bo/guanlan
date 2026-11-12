"""P4.31 Web 网址导入端点：后台下载 → 事件循环提交（同名不覆盖）→ 可选直接一键入库。

下载器本身（网段、类型判定…）在 tests/test_fetch.py；这里把 `fetch_url` 换成假的，只测端点与提交边界。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from guanlan import oneclick  # noqa: E402
from guanlan.fetch import FetchCancelled, FetchError, FetchResult  # noqa: E402
from guanlan.sourcerec import read_source_record, source_record_path  # noqa: E402
from guanlan.imageio import ConvertResult  # noqa: E402
from guanlan.web import fetch_routes  # noqa: E402
from guanlan.web.app import create_app  # noqa: E402
from guanlan.web.uploads import commit_download  # noqa: E402

PDF = b"%PDF-1.7\n" + b"x" * 64


@pytest.fixture(autouse=True)
def _no_agentao_fallback(monkeypatch, tmp_path_factory):
    """开发机 ~/.env 可能配了 AGENTAO_WEB_FETCH_*（下载器会按 agentao 的顺序往上找 .env）：本文件不许真起浏览器或连 jina。"""
    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)
    monkeypatch.delenv("AGENTAO_WEB_FETCH_ALLOW_CIDRS", raising=False)
    for name in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


class FakeFetch:
    """替身 `fetch_url`：在目标目录写隐藏临时文件，返回 FetchResult；可用 gate 卡住模拟长下载。"""

    def __init__(self, name="report.pdf", body=PDF, kind="pdf", error: Exception | None = None, gate=None):
        self.name, self.body, self.kind, self.error, self.gate = name, body, kind, error, gate
        self.started = threading.Event()
        self.urls: list[str] = []

    def __call__(self, url, dest_dir, *, progress=None, cancel=None, **kw):
        self.urls.append(url)
        self.started.set()
        if self.gate is not None:
            while not self.gate.wait(0.01):
                if cancel is not None and cancel.is_set():
                    raise FetchCancelled
        if self.error is not None:
            raise self.error
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest_dir / f".upload-{time.monotonic_ns()}.tmp"
        tmp.write_bytes(self.body)
        if progress:
            progress(len(self.body), len(self.body))
        record = {
            "url": url,
            "final_url": url,
            "fetched_at": "2026-11-11T10:00:00+08:00",
            "sha256": hashlib.sha256(self.body).hexdigest(),
            "content_type": "application/pdf",
            "charset": None,
            "bytes": len(self.body),
        }
        return FetchResult(tmp=tmp, suggested_name=self.name, kind=self.kind, size=len(self.body), record=record)


def _wait(client, timeout=5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = client.get("/api/upload/url").json()
        if v and v["state"] not in ("downloading", "committing"):
            return v
        time.sleep(0.02)
    raise AssertionError("下载未结束")


def _download(client, url="https://e.org/report.pdf", **body) -> dict:
    r = client.post("/api/upload/url", json={"url": url, **body})
    assert r.status_code == 200, r.text
    return _wait(client)


def _uploads(kb: Path) -> Path:
    return kb / "workspace" / "uploads"


def _visible(kb: Path) -> list[str]:
    return sorted(p.name for p in _uploads(kb).iterdir() if not p.name.startswith("."))


@pytest.fixture
def fake(monkeypatch):
    f = FakeFetch()
    monkeypatch.setattr(fetch_routes, "fetch_url", f)
    return f


def test_download_saves_file_and_source_record(kb, fake):
    with TestClient(create_app(kb)) as client:
        v = _download(client)
        assert v["state"] == "done" and v["saved"] == "workspace/uploads/report.pdf"
        assert v["kind"] == "pdf" and v["received"] == len(PDF)
        f = _uploads(kb) / "report.pdf"
        assert f.read_bytes() == PDF
        assert read_source_record(f)["url"] == "https://e.org/report.pdf"
        # 列表：🔗 的依据；出处记录是点文件、不单独列出
        items = client.get("/api/workspace").json()["uploads"]
        assert [(it["name"], it.get("source_url")) for it in items] == [("report.pdf", "https://e.org/report.pdf")]
    assert not [p for p in _uploads(kb).iterdir() if p.name.startswith(".upload-")]  # 无临时文件残留


def test_second_download_same_name_gets_number(kb, fake):
    with TestClient(create_app(kb)) as client:
        _download(client)
        fake.body = PDF + b"2"
        v = _download(client)
        assert v["name"] == "report-2.pdf"
    assert (_uploads(kb) / "report.pdf").read_bytes() == PDF
    assert (_uploads(kb) / "report-2.pdf").read_bytes() == PDF + b"2"


def test_upload_first_then_download_gets_number(kb, fake):
    with TestClient(create_app(kb)) as client:
        assert client.post("/api/upload", files={"file": ("report.pdf", b"%PDF mine", "application/pdf")}).status_code == 200
        v = _download(client)
        assert v["name"] == "report-2.pdf"
    assert (_uploads(kb) / "report.pdf").read_bytes() == b"%PDF mine"  # 上传的文件不被下载替掉
    assert not source_record_path(_uploads(kb) / "report.pdf").exists()


def test_download_first_then_upload_overwrites_and_record_expires(kb, fake):
    """上传语义不变：下载先、上传后同名 → 上传照旧覆盖；下载的出处记录对不上内容，作废。"""
    with TestClient(create_app(kb)) as client:
        _download(client)
        assert client.post("/api/upload", files={"file": ("report.pdf", b"%PDF mine", "application/pdf")}).status_code == 200
        items = client.get("/api/workspace").json()["uploads"]
        assert items[0]["name"] == "report.pdf" and "source_url" not in items[0]  # 不再标 🔗
    f = _uploads(kb) / "report.pdf"
    assert f.read_bytes() == b"%PDF mine"
    assert read_source_record(f) is None


def test_file_written_outside_the_loop_not_overwritten(kb, fake):
    _uploads(kb).mkdir(parents=True)
    (_uploads(kb) / "report.pdf").write_bytes(b"copied in by hand")
    with TestClient(create_app(kb)) as client:
        assert _download(client)["name"] == "report-2.pdf"
    assert (_uploads(kb) / "report.pdf").read_bytes() == b"copied in by hand"


def test_concurrent_commits_never_overwrite(tmp_path):
    """换名本身也不覆盖：同一目标的并发提交各得各的文件，内容不串（兜事件循环之外的写入者）。"""
    d = tmp_path / "uploads"
    d.mkdir()
    bodies = [f"%PDF-{i}".encode() for i in range(8)]
    tmps = []
    for i, b in enumerate(bodies):
        t = d / f".upload-{i}.tmp"
        t.write_bytes(b)
        tmps.append(t)
    results: list[Path] = []
    barrier = threading.Barrier(len(tmps))

    def run(t, b):
        barrier.wait()
        results.append(commit_download(t, d / "report.pdf", {"sha256": hashlib.sha256(b).hexdigest(), "final_url": "u", "bytes": len(b)}))

    threads = [threading.Thread(target=run, args=(t, b)) for t, b in zip(tmps, bodies)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len({p.name for p in results}) == len(bodies)
    assert sorted(p.read_bytes() for p in results) == sorted(bodies)
    for p in results:  # 每份出处记录都对得上自己的文件
        assert read_source_record(p) is not None


def test_failure_and_cancel(kb, monkeypatch):
    monkeypatch.setattr(fetch_routes, "fetch_url", FakeFetch(error=FetchError("服务器返回 HTTP 403。")))
    with TestClient(create_app(kb)) as client:
        v = _download(client)
        assert v["state"] == "failed" and "403" in v["error"]
        gate = threading.Event()
        slow = FakeFetch(gate=gate)
        monkeypatch.setattr(fetch_routes, "fetch_url", slow)
        did = client.post("/api/upload/url", json={"url": "https://e.org/report.pdf"}).json()["download_id"]
        assert slow.started.wait(5)
        assert client.post("/api/upload/url/nope/cancel").status_code == 409
        assert client.post(f"/api/upload/url/{did}/cancel").json() == {"cancelling": True}
        v = _wait(client)
        assert v["state"] == "cancelled"
    assert not _uploads(kb).exists() or _visible(kb) == []


def test_only_one_active_download(kb, monkeypatch):
    gate = threading.Event()
    slow = FakeFetch(gate=gate)
    monkeypatch.setattr(fetch_routes, "fetch_url", slow)
    with TestClient(create_app(kb)) as client:
        client.post("/api/upload/url", json={"url": "https://e.org/a.pdf"})
        assert slow.started.wait(5)
        r = client.post("/api/upload/url", json={"url": "https://e.org/b.pdf"})
        assert r.status_code == 409 and "e.org/a.pdf" in r.json()["detail"]
        v = client.get("/api/upload/url").json()  # 页面刷新后找回进行中的下载
        assert v["state"] == "downloading" and v["url"] == "https://e.org/a.pdf"
        gate.set()
        assert _wait(client)["state"] == "done"


def test_bad_url_400_and_writable_423(kb, fake):
    with TestClient(create_app(kb)) as client:
        assert client.post("/api/upload/url", json={"url": "ftp://e.org/a"}).status_code == 400
        assert client.post("/api/upload/url", json={"url": "https://e.org/a", "html_extract": "x"}).status_code == 422
        client.app.state.write_gate.enter_writable()
        try:
            assert client.post("/api/upload/url", json={"url": "https://e.org/a.pdf"}).status_code == 423
        finally:
            client.app.state.write_gate.exit_writable()
    assert fake.urls == []


def test_writable_session_started_during_download_blocks_commit(kb, monkeypatch):
    """下载要几分钟：期间开始的可写对话，提交时同样要挡（层③），临时文件删掉。"""
    gate = threading.Event()
    slow = FakeFetch(gate=gate)
    monkeypatch.setattr(fetch_routes, "fetch_url", slow)
    with TestClient(create_app(kb)) as client:
        client.post("/api/upload/url", json={"url": "https://e.org/a.pdf"})
        assert slow.started.wait(5)
        client.app.state.write_gate.enter_writable()
        try:
            gate.set()
            v = _wait(client)
        finally:
            client.app.state.write_gate.exit_writable()
        assert v["state"] == "failed" and "可写对话" in v["error"]
    assert not any(p.name.startswith(".upload-") for p in _uploads(kb).iterdir())
    assert _visible(kb) == []


def test_reader_host_has_no_fetch_endpoints(kb):
    with TestClient(create_app(kb, reader=True)) as client:
        assert client.post("/api/upload/url", json={"url": "https://e.org/a.pdf"}).status_code in (404, 405)
        assert client.get("/api/upload/url").status_code in (404, 405)


def test_delete_removes_source_record(kb, fake):
    with TestClient(create_app(kb)) as client:
        _download(client)
        f = _uploads(kb) / "report.pdf"
        assert source_record_path(f).exists()
        assert client.delete("/api/workspace/file?path=workspace/uploads/report.pdf").status_code == 200
    assert not f.exists() and not source_record_path(f).exists()


# ── 下载后直接一键入库（§6）与出处进 raw/（§5） ─────────────────────────────


def _book() -> str:
    return "# 示例报告\n\n" + ("正文内容。" * 40 + "\n\n") * 3


@pytest.fixture
def fake_convert(monkeypatch):
    calls: list[dict] = []

    def convert(src, *, stem, backend, cwd, progress=None, **kw):
        calls.append({"src": src, **kw})
        return ConvertResult(markdown=_book(), backend="pandoc")

    monkeypatch.setattr(oneclick, "convert_to_markdown", convert)
    return calls


def _wait_job(client, job_id, timeout=10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["state"] == "done":
            return data
        time.sleep(0.02)
    raise AssertionError("作业未完成")


def test_download_then_oneclick_records_url_as_origin(kb, monkeypatch, fake_convert):
    f = FakeFetch(name="report.pdf")
    monkeypatch.setattr(fetch_routes, "fetch_url", f)
    with TestClient(create_app(kb)) as client:
        v = _download(client, url="https://e.org/r?id=1", oneclick=True)
        assert v["state"] == "done" and v["oneclick_job"] and v["oneclick_error"] is None
        job = _wait_job(client, v["oneclick_job"])
        assert job["exit_code"] == 0, job["output"]
        task = client.get("/api/oneclick").json()["active"]
        assert task["state"] == "awaiting_confirm"  # 确认关口保留（决策P4.31-10）
        assert task["source_url"] == "https://e.org/r?id=1"  # 任务卡显示原网址
    (t,) = oneclick.iter_tasks(kb)
    assert t["source_url"] == "https://e.org/r?id=1"
    whole = (kb / "workspace" / "parsed" / "report.md").read_text(encoding="utf-8")
    assert "origin: https://e.org/r?id=1" in whole
    assert "fetched: '2026-11-11'" in whole or "fetched: 2026-11-11" in whole
    assert fake_convert[0]["html_base_url"] == "https://e.org/r?id=1"  # 选项照传；只对 HTML 生效由转换层判断


def test_oneclick_after_download_blocked_by_active_task_keeps_file(kb, monkeypatch, fake_convert):
    oneclick.save_task(kb, {"task_id": "20261111-000000-x", "state": "paused", "upload": "workspace/uploads/x.pdf", "created": "x"})
    monkeypatch.setattr(fetch_routes, "fetch_url", FakeFetch())
    with TestClient(create_app(kb)) as client:
        v = _download(client, oneclick=True)
        assert v["state"] == "done" and v["oneclick_job"] is None and "未完成" in v["oneclick_error"]
    assert _visible(kb) == ["report.pdf"]


def test_html_whole_page_passes_extract_off_and_base(kb, monkeypatch, fake_convert):
    html = b"<!doctype html><html><body><p>hi</p></body></html>"
    f = FakeFetch(name="page.html", body=html, kind="html")
    monkeypatch.setattr(fetch_routes, "fetch_url", f)
    with TestClient(create_app(kb)) as client:
        v = _download(client, url="https://e.org/news/1.html", oneclick=True, html_extract="off")
        assert _wait_job(client, v["oneclick_job"])["exit_code"] == 0
    (call,) = fake_convert
    assert call["html_extract"] == "off" and call["html_base_url"] == "https://e.org/news/1.html"
    (t,) = oneclick.iter_tasks(kb)
    assert t["html_extract"] == "off"


def test_oneclick_endpoint_accepts_html_extract(kb, fake_convert):
    """暂存区 HTML 行的「整页一键入库」：`POST /api/oneclick {html_extract: off}` 透传到转换。"""
    with TestClient(create_app(kb)) as client:
        assert client.post("/api/upload", files={"file": ("p.html", b"<html><body>x</body></html>", "text/html")}).status_code == 200
        r = client.post("/api/oneclick", json={"upload": "workspace/uploads/p.html", "html_extract": "off"})
        assert _wait_job(client, r.json()["job_id"])["exit_code"] == 0
        assert client.post("/api/oneclick", json={"upload": "workspace/uploads/p.html", "html_extract": "bad"}).status_code == 422
    assert fake_convert[0]["html_extract"] == "off"
    assert fake_convert[0]["html_base_url"] is None  # 上传的文件没有出处记录


def test_record_on_disk_is_json(kb, fake):
    with TestClient(create_app(kb)) as client:
        _download(client)
    rec = json.loads(source_record_path(_uploads(kb) / "report.pdf").read_text(encoding="utf-8"))
    assert set(rec) >= {"url", "final_url", "fetched_at", "sha256", "bytes"}
    assert os.path.basename(source_record_path(_uploads(kb) / "report.pdf")).startswith(".")


def test_link_fallback_on_filesystem_without_hardlinks(tmp_path, monkeypatch):
    """exFAT / 部分 SMB 不支持硬链接：退到 O_EXCL 占名 + replace，照样不覆盖已有文件。"""
    import errno

    def no_link(src, dst):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_link)
    d = tmp_path / "uploads"
    d.mkdir()
    (d / "report.pdf").write_bytes(b"existing")
    t = d / ".upload-1.tmp"
    t.write_bytes(PDF)
    out = commit_download(t, d / "report.pdf", {"sha256": hashlib.sha256(PDF).hexdigest(), "final_url": "u"})
    assert out.name == "report-2.pdf" and out.read_bytes() == PDF
    assert (d / "report.pdf").read_bytes() == b"existing" and not t.exists()


def test_commit_failure_reaches_the_page(kb, monkeypatch):
    """提交阶段的失败（如 423）也要带 `finished`，否则前端不会接手、错误不显示。"""
    gate = threading.Event()
    slow = FakeFetch(gate=gate)
    monkeypatch.setattr(fetch_routes, "fetch_url", slow)
    with TestClient(create_app(kb)) as client:
        client.post("/api/upload/url", json={"url": "https://e.org/a.pdf"})
        assert slow.started.wait(5)
        client.app.state.write_gate.enter_writable()
        try:
            gate.set()
            v = _wait(client)
        finally:
            client.app.state.write_gate.exit_writable()
    assert v["state"] == "failed" and v["finished"]


def test_unexpected_oneclick_error_keeps_download_done(kb, monkeypatch, fake_convert):
    monkeypatch.setattr(fetch_routes, "fetch_url", FakeFetch())

    def boom(root):
        raise OSError("disk read failed")

    monkeypatch.setattr("guanlan.web.oneclick_routes.active_task", boom)
    with TestClient(create_app(kb)) as client:
        v = _download(client, oneclick=True)
    assert v["state"] == "done" and v["saved"] and "disk read failed" in v["oneclick_error"]
    assert _visible(kb) == ["report.pdf"]


def test_signed_url_credentials_stay_out_of_raw_and_task(kb, monkeypatch, fake_convert):
    """预签名链接：raw/ 的 origin 与任务清单不带签名 / 令牌（会随 zip 分享）；完整网址只在隐藏出处记录里。"""
    url = "https://b.s3.amazonaws.com/report.pdf?X-Amz-Date=1&X-Amz-Signature=SECRETSIG&X-Amz-Security-Token=SECRETTOK"
    monkeypatch.setattr(fetch_routes, "fetch_url", FakeFetch())
    with TestClient(create_app(kb)) as client:
        v = _download(client, url=url, oneclick=True)
        assert _wait_job(client, v["oneclick_job"])["exit_code"] == 0
    whole = (kb / "workspace" / "parsed" / "report.md").read_text(encoding="utf-8")
    assert "origin: https://b.s3.amazonaws.com/report.pdf?X-Amz-Date=1" in whole
    task_files = list((kb / "workspace" / "tasks").glob("*.json"))
    leaked = [p for p in [*task_files, *(kb / "workspace" / "parsed").rglob("*.md")] if "SECRET" in p.read_text(encoding="utf-8")]
    assert leaked == []
    # 出处记录写入时就脱敏：盘上任何地方都不留签名
    assert "SECRET" not in source_record_path(_uploads(kb) / "report.pdf").read_text(encoding="utf-8")


def test_same_size_upload_overwrite_drops_source_record(kb, fake):
    """同名上传覆盖下载件、且字节数恰好相同：列表只比字节数，旧出处记录须随覆盖删掉，不能继续标 🔗。"""
    same_size = b"%PDF-1.7\n" + b"y" * 64
    assert len(same_size) == len(PDF)
    with TestClient(create_app(kb)) as client:
        _download(client)
        assert client.post("/api/upload", files={"file": ("report.pdf", same_size, "application/pdf")}).status_code == 200
        items = client.get("/api/workspace").json()["uploads"]
        assert items[0]["name"] == "report.pdf" and "source_url" not in items[0]
    assert not source_record_path(_uploads(kb) / "report.pdf").exists()


def test_list_marker_needs_matching_mtime(kb, fake):
    """同大小的文件被直接拷进来替换：修改时间对不上，就不再标 🔗（列表不算哈希）。"""
    with TestClient(create_app(kb)) as client:
        _download(client)
        f = _uploads(kb) / "report.pdf"
        assert client.get("/api/workspace").json()["uploads"][0].get("source_url")
        st = f.stat()
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        assert "source_url" not in client.get("/api/workspace").json()["uploads"][0]


def test_upload_blocked_while_download_is_starting_oneclick(kb, monkeypatch, fake_convert):
    """下载提交与一键入库登记之间的空档：同名上传不能把刚下载的原件换掉。"""
    from guanlan.web import oneclick_routes

    monkeypatch.setattr(fetch_routes, "fetch_url", FakeFetch())
    entered, release = threading.Event(), threading.Event()
    real = oneclick_routes.active_task

    def slow_active(root):
        entered.set()
        release.wait(5)
        return real(root)

    monkeypatch.setattr(oneclick_routes, "active_task", slow_active)
    with TestClient(create_app(kb)) as client:
        client.post("/api/upload/url", json={"url": "https://e.org/report.pdf", "oneclick": True})
        assert entered.wait(5)  # 文件已提交，start_prepare 正在查盘
        try:
            r = client.post("/api/upload", files={"file": ("report.pdf", b"%PDF other", "application/pdf")})
            assert r.status_code == 409
        finally:
            release.set()
        v = _wait(client)
        assert v["oneclick_job"]
    assert (_uploads(kb) / "report.pdf").read_bytes() == PDF


def test_status_url_redacted_and_parsed_dir_skips_records(kb, monkeypatch):
    gate = threading.Event()
    slow = FakeFetch(gate=gate)
    monkeypatch.setattr(fetch_routes, "fetch_url", slow)
    url = "https://b.s3.amazonaws.com/report.pdf?X-Amz-Date=1&X-Amz-Signature=SECRETSIG"
    with TestClient(create_app(kb)) as client:
        client.post("/api/upload/url", json={"url": url})
        assert slow.started.wait(5)
        v = client.get("/api/upload/url").json()
        assert "SECRETSIG" not in v["url"] and v["url"].startswith("https://b.s3.amazonaws.com/report.pdf")
        gate.set()
        assert _wait(client)["state"] == "done"
        assert "SECRETSIG" not in client.get("/api/workspace").json()["uploads"][0]["source_url"]


def test_idn_too_long_label_is_readable_error(tmp_path):
    from guanlan.fetch import fetch_url as real_fetch

    with pytest.raises(FetchError, match="国际化域名"):
        real_fetch("https://" + "例" * 70 + ".中国/a.pdf", tmp_path, resolver=lambda h, p: ["93.184.216.34"])


def test_long_upload_name_is_capped_not_500(kb):
    """普通上传超长中文名：以前换名时 ENAMETOOLONG → 500；现在按字节截短后照常保存。"""
    name = "很长的报告标题" * 20 + ".pdf"
    with TestClient(create_app(kb)) as client:
        r = client.post("/api/upload", files={"file": (name, b"%PDF x", "application/pdf")})
    assert r.status_code == 200
    saved = r.json()["name"]
    assert saved.endswith(".pdf") and len(saved.encode("utf-8")) <= 184


def test_startup_sweeps_stale_htmlprep_dirs(tmp_path):
    from guanlan.web.uploads import STALE_UPLOAD_TMP_SECONDS, sweep_stale_upload_tmps

    d = tmp_path / "uploads"
    old, fresh = d / ".htmlprep-old", d / ".htmlprep-new"
    for p in (old, fresh):
        p.mkdir(parents=True)
        (p / "x.html").write_text("x", encoding="utf-8")
    past = time.time() - STALE_UPLOAD_TMP_SECONDS - 10
    os.utime(old, (past, past))
    assert sweep_stale_upload_tmps(d) == 1
    assert not old.exists() and fresh.exists()


def test_long_names_sharing_a_prefix_do_not_overwrite(kb):
    prefix = "很长的报告标题" * 20
    with TestClient(create_app(kb)) as client:
        a = client.post("/api/upload", files={"file": (prefix + "-第一卷.pdf", b"%PDF 1", "application/pdf")}).json()["name"]
        b = client.post("/api/upload", files={"file": (prefix + "-第二卷.pdf", b"%PDF 2", "application/pdf")}).json()["name"]
        again = client.post("/api/upload", files={"file": (prefix + "-第一卷.pdf", b"%PDF 1b", "application/pdf")}).json()["name"]
    assert a != b and again == a  # 不同长名不撞；同名照旧覆盖
    assert (_uploads(kb) / b).read_bytes() == b"%PDF 2"


def test_url_markdown_images_become_links_against_page(kb, monkeypatch):
    """网址下载的 Markdown：图片按原网址补全成普通链接，既不读本机同名图，也不让 ⑤ 去 parsed/ 找图。"""
    body = "# 标题\n\n![截图](images/shot.png) 与 ![](https://cdn.x.org/b.png)\n\n" + "正文。" * 100 + "\n"
    monkeypatch.setattr(fetch_routes, "fetch_url", FakeFetch(name="README.md", body=body.encode(), kind="md"))
    with TestClient(create_app(kb)) as client:
        v = _download(client, url="https://e.org/docs/README.md", oneclick=True)
        assert _wait_job(client, v["oneclick_job"])["exit_code"] == 0
    whole = (kb / "workspace" / "parsed" / "README.md").read_text(encoding="utf-8")
    assert "[图：截图](https://e.org/docs/images/shot.png)" in whole
    assert "[图](https://cdn.x.org/b.png)" in whole and "![" not in whole


def test_long_pseudo_suffix_is_capped(kb):
    """`1.<几百字节>`：最后一个点之后不是扩展名，也要截短，不能换名时 ENAMETOOLONG。"""
    with TestClient(create_app(kb)) as client:
        r = client.post("/api/upload", files={"file": ("1." + "很长的说明文字" * 15, b"x", "application/octet-stream")})
    assert r.status_code == 200 and len(r.json()["name"].encode("utf-8")) <= 184


def test_error_text_with_signed_url_is_redacted(kb, monkeypatch):
    """后备失败等报错原文里常带完整网址（如浏览器的 `net::ERR_… at https://…`）：上屏前同样去掉签名。"""
    err = FetchError("网站拒绝了自动下载（HTTP 403）（已按 AGENTAO_WEB_FETCH_FALLBACK=playwright 再取一次，仍失败："
                     "page.goto: net::ERR_FAILED at https://b.s3.amazonaws.com/r.pdf?X-Amz-Date=1&X-Amz-Signature=SECRETSIG）")
    monkeypatch.setattr(fetch_routes, "fetch_url", FakeFetch(error=err))
    with TestClient(create_app(kb)) as client:
        v = _download(client, url="https://b.s3.amazonaws.com/r.pdf?X-Amz-Date=1&X-Amz-Signature=SECRETSIG")
    assert v["state"] == "failed" and "SECRET" not in v["error"] and "X-Amz-Date=1" in v["error"]
    assert "SECRET" not in v["url"]


def test_url_markdown_cannot_fake_provenance(kb, monkeypatch, fake_convert):
    """下载来的 Markdown 自带的 parsed_by / pages / source_sha256 等出处类键不进 raw/：只认观澜自己写的。"""
    body = ("---\ntitle: 说明\nparsed_by: mineru/standard\npages: 99\nsource_sha256: deadbeef\nauthor: 某人\n---\n\n"
            "# 说明\n\n" + "正文。" * 100 + "\n")
    monkeypatch.setattr(fetch_routes, "fetch_url", FakeFetch(name="readme.md", body=body.encode(), kind="md"))
    with TestClient(create_app(kb)) as client:
        v = _download(client, url="https://e.org/readme.md", oneclick=True)
        assert _wait_job(client, v["oneclick_job"])["exit_code"] == 0
    head = (kb / "workspace" / "parsed" / "readme.md").read_text(encoding="utf-8").split("\n---\n", 1)[0]
    assert "mineru/standard" not in head and "pages: 99" not in head and "deadbeef" not in head
    assert "author: 某人" in head  # 与出处无关的键照旧保留
