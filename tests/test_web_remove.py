"""P4.35 Web 撤回源：预览、提交、摘要核对、任务中心续跑（`guanlan/web/remove_routes.py`）。零 LLM。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml

from conftest import write_page

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import guanlan.web.tasks as tasks_mod  # noqa: E402
from guanlan.remove import remove_entrypoint  # noqa: E402
from guanlan.web.app import create_app  # noqa: E402
from guanlan.web.tasks import REMOVE_INTERRUPTED, REMOVE_NOT_STARTED, TaskStore, jobs_dir  # noqa: E402

_ACTIVE = ("queued", "waiting_lock", "running", "cancelling", "rolling_back")


def _wait(client, task_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/tasks/{task_id}").json()
        if data["state"] not in _ACTIVE:
            return data
        time.sleep(0.02)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内结束")


def _vol(root: Path, stem: str, index: int, *, sample="book", parent="book") -> Path:
    p = root / "raw" / sample / f"{stem}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = {"title": stem, "split": {"parent": parent, "index": index, "total": 2}}
    p.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n\n正文\n", encoding="utf-8")
    return p


def _book(root: Path) -> None:
    _vol(root, "a", 1)
    _vol(root, "b", 2)
    write_page(root, "wiki/sources/a.md", type="source")
    write_page(root, "wiki/sources/b.md", type="source")
    (root / "raw" / "c.md").write_text("批外", encoding="utf-8")
    write_page(root, "wiki/sources/c.md", type="source")


def _single(root: Path) -> None:
    (root / "raw" / "foo.md").write_text("原文", encoding="utf-8")
    write_page(root, "wiki/sources/foo.md", type="source")
    (root / "raw" / "bar.md").write_text("原文", encoding="utf-8")
    write_page(root, "wiki/sources/bar.md", type="source")
    write_page(root, "wiki/concepts/multi.md", sources="[foo, bar]")


def _trash(root: Path) -> list[Path]:
    t = root / ".trash"
    return sorted(t.iterdir()) if t.is_dir() else []


def _env(root: Path, task_id: str) -> dict:
    return json.loads((jobs_dir(root) / f"{task_id}.json").read_text(encoding="utf-8"))


# ---------- 单源 ----------


def test_single_preview_matches_cli_json(kb, capsys) -> None:
    _single(kb)
    assert remove_entrypoint(kb, src="foo", yes=False, json_output=True) == 0
    cli = json.loads(capsys.readouterr().out)
    with TestClient(create_app(kb)) as client:
        r = client.get("/api/remove/preview", params={"source": "foo"}).json()
    for k in ("ok", "slug", "requested", "relocate", "drop_slug", "orphans", "index_lines", "backlinks"):
        assert r[k] == cli[k], k
    assert r["plan_digest"] and r["scope"] == "one" and r["group_available"] is False
    assert _trash(kb) == []  # 预览不写盘


def test_single_submit_executes_like_cli(kb) -> None:
    _single(kb)
    with TestClient(create_app(kb)) as client:
        p = client.get("/api/remove/preview", params={"source": "foo"}).json()
        r = client.post("/api/remove", json={"source": "foo", "scope": "one", "plan_digest": p["plan_digest"]}).json()
        done = _wait(client, r["task_id"])
    assert done["state"] == "succeeded", done["log"]["text"]
    assert not (kb / "raw" / "foo.md").exists() and (kb / "raw" / "bar.md").exists()
    [entry] = _trash(kb)
    assert entry.name.startswith("foo@") and (entry / "manifest.json").is_file()
    env = _env(kb, r["task_id"])
    assert env["params"] == {"scope": "one", "source": "foo", "plan_digest": p["plan_digest"]}
    assert env["remove_writes_began"] and env.get("preimage") is None
    assert not (tasks_mod.preimage_base(kb) / r["task_id"]).exists()  # 不拍前像


def test_single_literal_named_page_submits(kb) -> None:
    """摘要页按原文件名逐字命名（全角括号）：按页路径预览、提交都认得出，与 CLI 同一兜底。"""
    lit = "报告（2024）"
    (kb / "raw" / f"{lit}.md").write_text("原文", encoding="utf-8")
    write_page(kb, f"wiki/sources/{lit}.md", type="source", sources=f"['{lit}']")
    src = f"wiki/sources/{lit}.md"
    with TestClient(create_app(kb)) as client:
        p = client.get("/api/remove/preview", params={"source": src}).json()
        assert p["slug"] == lit and set(p["relocate"]) == {f"raw/{lit}.md", src}
        r = client.post("/api/remove", json={"source": src, "scope": "one", "plan_digest": p["plan_digest"]}).json()
        done = _wait(client, r["task_id"])
    assert done["state"] == "succeeded", done["log"]["text"]
    assert not (kb / "raw" / f"{lit}.md").exists() and not (kb / src).exists()


def test_digest_mismatch_writes_nothing(kb) -> None:
    _single(kb)
    with TestClient(create_app(kb)) as client:
        p = client.get("/api/remove/preview", params={"source": "foo"}).json()
        write_page(kb, "wiki/concepts/later.md", sources="[foo, bar]")  # 预览之后多了一页引用
        r = client.post("/api/remove", json={"source": "foo", "scope": "one", "plan_digest": p["plan_digest"]}).json()
        done = _wait(client, r["task_id"])
    assert done["state"] == "failed" and "请重新预览" in done["log"]["text"]
    assert (kb / "raw" / "foo.md").exists() and _trash(kb) == []
    assert not _env(kb, r["task_id"]).get("remove_writes_began")


def test_overwritten_raw_invalidates_digest(kb) -> None:
    _single(kb)
    with TestClient(create_app(kb)) as client:
        p = client.get("/api/remove/preview", params={"source": "foo"}).json()
        (kb / "raw" / "foo.md").write_text("换了内容", encoding="utf-8")
        assert client.get("/api/remove/preview", params={"source": "foo"}).json()["plan_digest"] != p["plan_digest"]


@pytest.mark.parametrize(
    "params, status",
    [
        ({"source": "nope"}, 404),
        ({"source": "  "}, 400),
        ({"source": "foo", "scope": "bad"}, 400),
        ({}, 400),
        ({"source": "foo", "task": "x"}, 400),
        ({"source": "foo", "scope": "all"}, 400),  # 顶层 raw：没有分卷元数据
    ],
)
def test_preview_errors(kb, params, status) -> None:
    _single(kb)
    with TestClient(create_app(kb)) as client:
        assert client.get("/api/remove/preview", params=params).status_code == status


def test_reader_and_writable_turn(kb) -> None:
    _single(kb)
    with TestClient(create_app(kb, reader=True)) as client:
        assert client.get("/api/remove/preview", params={"source": "foo"}).status_code in (404, 405)
        assert client.post("/api/remove", json={"source": "foo", "plan_digest": "x"}).status_code in (404, 405)
    with TestClient(create_app(kb)) as client:
        gate = client.app.state.write_gate
        gate.enter_writable()
        try:
            assert client.post("/api/remove", json={"source": "foo", "plan_digest": "x"}).status_code == 423
        finally:
            gate.exit_writable()
    assert (kb / "raw" / "foo.md").exists()


def test_envelope_failure_is_503_and_writes_nothing(kb, monkeypatch) -> None:
    _single(kb)
    with TestClient(create_app(kb)) as client:
        p = client.get("/api/remove/preview", params={"source": "foo"}).json()

        def boom(*a, **k):
            raise OSError("workspace 不可写")

        monkeypatch.setattr(TaskStore, "create_or_reuse", boom)
        r = client.post("/api/remove", json={"source": "foo", "scope": "one", "plan_digest": p["plan_digest"]})
        assert r.status_code == 503
        assert not client.app.state.jobs._jobs
    assert (kb / "raw" / "foo.md").exists() and _trash(kb) == []


def test_no_task_store_is_503(kb, monkeypatch) -> None:
    _single(kb)

    def broken(self, *a, **k):
        raise OSError("workspace 读不了")

    monkeypatch.setattr(TaskStore, "__init__", broken)
    with TestClient(create_app(kb)) as client:
        assert client.app.state.tasks is None
        r = client.post("/api/remove", json={"source": "foo", "scope": "one", "plan_digest": "x"})
        assert r.status_code == 503
    assert (kb / "raw" / "foo.md").exists()


def test_retry_is_409_even_when_writable_turn_active(kb) -> None:
    _single(kb)
    with TestClient(create_app(kb)) as client:
        p = client.get("/api/remove/preview", params={"source": "foo"}).json()
        tid = client.post("/api/remove", json={"source": "foo", "scope": "one", "plan_digest": "stale"}).json()["task_id"]
        assert _wait(client, tid)["state"] == "failed"
        n = len(list(jobs_dir(kb).glob("*.json")))
        gate = client.app.state.write_gate
        gate.enter_writable()
        try:
            r = client.post(f"/api/tasks/{tid}/retry")
        finally:
            gate.exit_writable()
        assert r.status_code == 409 and "重新预览" in r.json()["detail"]
        assert len(list(jobs_dir(kb).glob("*.json"))) == n
        assert p["plan_digest"]


def test_rollback_and_abandon_refused(kb) -> None:
    _single(kb)
    with TestClient(create_app(kb)) as client:
        tid = client.post("/api/remove", json={"source": "foo", "scope": "one", "plan_digest": "stale"}).json()["task_id"]
        _wait(client, tid)
        client.app.state.tasks.set_fields(tid, state="interrupted")
        assert client.post(f"/api/tasks/{tid}/rollback").status_code == 409
        assert client.post(f"/api/tasks/{tid}/abandon").status_code == 409


def test_job_done_bumps_wiki_generation(kb) -> None:
    with TestClient(create_app(kb)) as client:
        gate = client.app.state.write_gate
        before = gate.wiki_generation
        client.app.state.jobs._on_job_done("remove")
        assert gate.wiki_generation == before + 1


# ---------- 任务记录：取消与重启恢复 ----------


def test_running_remove_cannot_be_stopped(kb) -> None:
    store = TaskStore(kb)
    tid = store.create("remove", "撤回源：foo", {"scope": "one", "source": "foo", "plan_digest": "d"})
    store._envs[tid]["state"] = "running"
    assert store.request_stop(tid) is False
    assert store._envs[tid]["state"] == "running"


def test_recover_remove_by_writes_began(kb) -> None:
    import subprocess
    import sys

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    d = jobs_dir(kb)
    d.mkdir(parents=True)
    tid = "20261123-100000-remove-abcd"
    began = "20261123-100000-remove-abcd"  # 已记 remove_writes_began：可能已部分执行
    fresh = "20261123-100001-remove-abce"  # 没记：第一次写盘前退出，库一字未动
    for tid, extra in ((began, {"remove_writes_began": "2026-11-23 10:00:01"}), (fresh, {})):
        env = {"id": tid, "kind": "remove", "title": "撤回源：foo", "params": {"scope": "one"}, "state": "running",
               "owner_pid": proc.pid, "created": "2026-11-23 10:00:00", **extra}
        (d / f"{tid}.json").write_text(json.dumps(env), encoding="utf-8")
    store = TaskStore(kb)
    assert sorted(store.recover()) == [began, fresh]
    got = store.envelope(began)
    assert got["state"] == "interrupted" and got["error"] == REMOVE_INTERRUPTED
    got = store.envelope(fresh)
    assert got["state"] == "cancelled" and got["error"] == REMOVE_NOT_STARTED
    assert not store.has_preimage(began) and not store.has_preimage(fresh)


# ---------- 整批 ----------


def test_batch_preview_and_submit(kb) -> None:
    _book(kb)
    write_page(kb, "wiki/concepts/k.md", sources="[a, b, c]")
    with TestClient(create_app(kb)) as client:
        one = client.get("/api/remove/preview", params={"source": "a"}).json()
        assert one["group_available"] is True
        p = client.get("/api/remove/preview", params={"source": "a", "scope": "all"}).json()
        assert [m["raw"] for m in p["group"]["members"]] == ["raw/book/a.md", "raw/book/b.md"]
        assert p["group"]["sample"] == "book" and p["drop_slug"][0]["dropped"] == ["a", "b"]
        r = client.post("/api/remove", json={"source": "b", "scope": "all", "plan_digest": p["plan_digest"]}).json()
        done = _wait(client, r["task_id"])
    assert done["state"] == "succeeded", done["log"]["text"]
    assert not (kb / "raw" / "book").exists()
    [entry] = _trash(kb)
    assert entry.name.startswith("book@")
    env = _env(kb, r["task_id"])
    assert env["params"] == {"scope": "all", "raw_dir": "raw/book", "parent": "book", "plan_digest": p["plan_digest"]}
    assert [m["raw"] for m in env["group"]["members"]] == ["raw/book/a.md", "raw/book/b.md"]
    assert env["entry"] == {"source": "b"} and env["supersedes"] == []


def test_batch_from_two_entries_dedupes(kb) -> None:
    _book(kb)
    app = create_app(kb)
    with TestClient(app) as client:
        pa = client.get("/api/remove/preview", params={"source": "a", "scope": "all"}).json()
        pb = client.get("/api/remove/preview", params={"source": "b", "scope": "all"}).json()
        assert pa["plan_digest"] == pb["plan_digest"]
        app.state.write_gate.write_lock.acquire()
        try:
            r1 = client.post("/api/remove", json={"source": "a", "scope": "all", "plan_digest": pa["plan_digest"]}).json()
            r2 = client.post("/api/remove", json={"source": "b", "scope": "all", "plan_digest": pb["plan_digest"]}).json()
        finally:
            app.state.write_gate.write_lock.release()
        assert r2["deduplicated"] and r2["task_id"] == r1["task_id"]
        assert _wait(client, r1["task_id"])["state"] == "succeeded"


def test_batch_conflict_is_409_with_details(kb) -> None:
    _book(kb)
    write_page(kb, "wiki/sources/mixed.md", type="source", body="> 原始资料：`raw/book/a.md`\n> 原始资料：`raw/c.md`")
    with TestClient(create_app(kb)) as client:
        r = client.get("/api/remove/preview", params={"source": "a", "scope": "all"})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] in ("shared_page_outside", "claim_conflict")


def _interrupted_batch(client, kb: Path, monkeypatch) -> str:
    """整批撤回在移完第一卷后中断：信封带 remove_writes_began，状态改成 interrupted（模拟服务退出后的恢复结果）。"""
    import guanlan.remove_batch as rb

    p = client.get("/api/remove/preview", params={"source": "a", "scope": "all"}).json()
    orig = rb._move_into_trash

    def stop_at_b(src, root_, trash_dir):
        if src.name == "b.md" and "sources" in src.parts:
            raise OSError("模拟中断")
        return orig(src, root_, trash_dir)

    monkeypatch.setattr(rb, "_move_into_trash", stop_at_b)
    tid = client.post("/api/remove", json={"source": "a", "scope": "all", "plan_digest": p["plan_digest"]}).json()["task_id"]
    done = _wait(client, tid)
    monkeypatch.setattr(rb, "_move_into_trash", orig)
    assert done["state"] == "failed" and _env(kb, tid)["remove_writes_began"]
    return tid


def test_pending_task_redirects_source_entry_and_resumes(kb, monkeypatch) -> None:
    _book(kb)
    with TestClient(create_app(kb)) as client:
        old = _interrupted_batch(client, kb, monkeypatch)
        r = client.get("/api/remove/preview", params={"source": "b", "scope": "all"})
        assert r.status_code == 409 and r.json()["detail"]["task_id"] == old
        p = client.get("/api/remove/preview", params={"task": old}).json()
        assert [m["raw"] for m in p["group"]["members"]] == ["raw/book/b.md"] and p["done_members"] == 1
        r = client.post("/api/remove", json={"from_task": old, "scope": "all", "plan_digest": p["plan_digest"]}).json()
        done = _wait(client, r["task_id"])
        assert done["state"] == "succeeded", done["log"]["text"]
        assert done["retry_of"] == old
    assert _env(kb, r["task_id"])["supersedes"] == [old]
    assert _env(kb, old)["superseded_by"] == r["task_id"]
    assert not (kb / "raw" / "book").exists()


def test_superseded_task_no_longer_redirects_after_reimport(kb, monkeypatch) -> None:
    _book(kb)
    with TestClient(create_app(kb)) as client:
        old = _interrupted_batch(client, kb, monkeypatch)
        p = client.get("/api/remove/preview", params={"task": old}).json()
        new = client.post("/api/remove", json={"from_task": old, "scope": "all", "plan_digest": p["plan_digest"]}).json()["task_id"]
        assert _wait(client, new)["state"] == "succeeded"
        client.delete(f"/api/tasks/{new}")  # 成功记录被清掉
        _vol(kb, "a", 1)  # 同一原件重新导入
        _vol(kb, "b", 2)
        assert client.get("/api/remove/preview", params={"source": "a", "scope": "all"}).status_code == 200
        r = client.get("/api/remove/preview", params={"task": old})
        assert r.status_code == 409 and "完成" in r.json()["detail"]["message"]


def test_zero_write_failure_is_not_resumable(kb) -> None:
    _book(kb)
    with TestClient(create_app(kb)) as client:
        tid = client.post("/api/remove", json={"source": "a", "scope": "all", "plan_digest": "stale"}).json()["task_id"]
        assert _wait(client, tid)["state"] == "failed"
        assert client.get("/api/remove/preview", params={"source": "a", "scope": "all"}).status_code == 200
        r = client.get("/api/remove/preview", params={"task": tid})
        assert r.status_code == 409 and "没有进入执行" in r.json()["detail"]["message"]
        r = client.post("/api/remove", json={"from_task": tid, "scope": "all", "plan_digest": "x"})
        assert r.status_code == 409


def test_task_entry_rejects_single_and_other_kinds(kb) -> None:
    _single(kb)
    with TestClient(create_app(kb)) as client:
        tid = client.post("/api/remove", json={"source": "foo", "scope": "one", "plan_digest": "stale"}).json()["task_id"]
        _wait(client, tid)
        assert client.get("/api/remove/preview", params={"task": tid}).status_code == 400
        assert client.get("/api/remove/preview", params={"task": "20261123-000000-remove-ffff"}).status_code == 404


def test_import_conflict_blocks_batch(kb) -> None:
    _book(kb)
    d = kb / "workspace" / "tasks"
    d.mkdir(parents=True)
    (d / "20261123-000000-book.json").write_text(
        json.dumps({"task_id": "20261123-000000-book", "state": "paused", "items": [{"raw": "raw/book/a.md"}]}),
        encoding="utf-8",
    )
    with TestClient(create_app(kb)) as client:
        r = client.get("/api/remove/preview", params={"source": "a", "scope": "all"})
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "import_active"
        assert client.get("/api/remove/preview", params={"source": "a"}).status_code == 200  # 单卷不受影响


@pytest.mark.parametrize("raw_dir", ["raw/../wiki/entities", "raw/../../outside", "/tmp/x", "raw/a/b", "raw/.hidden"])
def test_task_entry_rejects_untrusted_raw_dir(kb, monkeypatch, raw_dir) -> None:
    """信封在 workspace/ 下、Agent 可写：被改出的 raw_dir 不能让续跑去枚举、移动 raw/<原件>/ 之外的文件。"""
    _book(kb)
    with TestClient(create_app(kb)) as client:
        old = _interrupted_batch(client, kb, monkeypatch)
        path = jobs_dir(kb) / f"{old}.json"
        env = json.loads(path.read_text(encoding="utf-8"))
        env["params"]["raw_dir"] = raw_dir
        path.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(kb)) as client:  # 重启：从盘上重新载入被改过的信封
        assert client.get("/api/remove/preview", params={"task": old}).status_code == 400
        r = client.post("/api/remove", json={"from_task": old, "scope": "all", "plan_digest": "x"})
        assert r.status_code == 400
