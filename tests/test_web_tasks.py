"""P4.33 T1 后台任务中心测试（见 docs/P4.33-Web后台任务.md §11 T1）。不打真实 LLM：Agent 一律用 fake runner。"""

import json
import os
import subprocess
import sys
import time

import pytest

from conftest import make_runner, write_page

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import guanlan.web.tasks as tasks_mod  # noqa: E402
from guanlan.oneclick import iter_tasks as iter_import_tasks  # noqa: E402
from guanlan.web.app import STATIC_DIR, create_app  # noqa: E402
from guanlan.web.jobs import Job  # noqa: E402
from guanlan.web.tasks import TaskStore, jobs_dir  # noqa: E402


def _put_raw(kb, name="doc.md") -> str:
    (kb / "raw" / name).write_text("# 资料\n一些内容。\n", encoding="utf-8")
    return f"raw/{name}"


def _ingest_writes(root, page: str, raw: str = "doc.md") -> None:
    write_page(root, page)
    slug = raw.rsplit(".", 1)[0]
    write_page(root, f"wiki/sources/{slug}.md", type="source", sources=f'["{slug}"]')


def _wait_task(client, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/tasks/{task_id}").json()
        if data["state"] not in ("queued", "running"):
            return data
        time.sleep(0.02)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内结束")


def _dead_pid() -> int:
    """一个刚退出的进程 pid（极小概率被复用，测试里可忽略）。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _write_env(kb, task_id: str, **fields) -> None:
    env = {
        "id": task_id,
        "kind": "ingest",
        "title": "补录：raw/x.md",
        "params": {"target": "raw/x.md"},
        "state": "succeeded",
        "created": "2026-11-01 10:00:00",
        "started": None,
        "finished": "2026-11-01 10:01:00",
        "exit_code": 0,
        "summary": None,
        "error": None,
        "owner_pid": _dead_pid(),
    }
    env.update(fields)
    d = jobs_dir(kb)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")


def _fake_job(task_id: str, exit_code: int = 0) -> Job:
    job = Job(id="1", kind="ingest", task_id=task_id)
    job.exit_code = exit_code
    return job


# ───────────────────────── 端到端：提交 → 信封 → 终态 ─────────────────────────


def test_ingest_submit_creates_task_and_finishes(kb) -> None:
    target = _put_raw(kb)
    runner = make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md"))
    with TestClient(create_app(kb, runner=runner)) as client:
        resp = client.post("/api/ingest", json={"target": target}).json()
        task_id = resp["task_id"]
        assert task_id and resp["job_id"]
        assert (jobs_dir(kb) / f"{task_id}.json").is_file()  # 入队前已落盘
        done = _wait_task(client, task_id)
        assert done["state"] == "succeeded" and done["exit_code"] == 0
        assert done["kind"] == "ingest" and done["params"] == {"target": target}
        assert done["log"]["text"]  # 输出已追加写进日志
        # 列表：最近完成里有它；作业接口也带回 task_id
        listing = client.get("/api/tasks").json()
        assert task_id in [r["id"] for r in listing["recent"]]
        assert client.get(f"/api/jobs/{resp['job_id']}").json()["task_id"] == task_id
    env = json.loads((jobs_dir(kb) / f"{task_id}.json").read_text(encoding="utf-8"))
    assert env["state"] == "succeeded" and env["started"] and env["finished"]


def test_failed_job_marks_task_failed(kb) -> None:
    target = _put_raw(kb)
    runner = make_runner(None, ok=False, error_type="boom")
    with TestClient(create_app(kb, runner=runner)) as client:
        task_id = client.post("/api/ingest", json={"target": target}).json()["task_id"]
        done = _wait_task(client, task_id)
    assert done["state"] == "failed" and done["exit_code"] != 0


def test_log_after_is_incremental(kb) -> None:
    target = _put_raw(kb)
    runner = make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md"))
    with TestClient(create_app(kb, runner=runner)) as client:
        task_id = client.post("/api/ingest", json={"target": target}).json()["task_id"]
        full = _wait_task(client, task_id)["log"]
        assert full["offset"] == 0 and full["next"] > 0
        tail = client.get(f"/api/tasks/{task_id}?log_after={full['next']}").json()["log"]
        assert tail["text"] == "" and tail["next"] == full["next"]
        assert client.get(f"/api/tasks/{task_id}?log_after=-1").status_code == 422


def test_unknown_task_404(kb) -> None:
    with TestClient(create_app(kb)) as client:
        assert client.get("/api/tasks/20261101-100000-ingest-abcd").status_code == 404
        assert client.get("/api/tasks/..%2F..%2Fetc").status_code == 404


def test_reader_does_not_register_tasks(kb) -> None:
    with TestClient(create_app(kb, reader=True)) as client:
        assert client.get("/api/tasks").status_code == 404
        assert client.get("/api/tasks/20261101-100000-ingest-abcd").status_code == 404
    assert not jobs_dir(kb).exists()


def test_task_store_unwritable_falls_back_to_plain_job(kb, monkeypatch) -> None:
    """建信封失败（workspace 写不了）：作业照常入队、照常跑，只是 task_id 为 None。"""

    def boom(self, *a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(TaskStore, "create", boom)
    target = _put_raw(kb)
    runner = make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md"))
    with TestClient(create_app(kb, runner=runner)) as client:
        resp = client.post("/api/ingest", json={"target": target}).json()
        assert resp["task_id"] is None and resp["job_id"]
        deadline = time.monotonic() + 5
        while client.get(f"/api/jobs/{resp['job_id']}").json()["state"] != "done":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert client.get(f"/api/jobs/{resp['job_id']}").json()["exit_code"] == 0


def test_observer_failure_does_not_break_job(kb, monkeypatch) -> None:
    """观察者（写日志）抛错不能拖垮作业：作业照常完成，终态照常落进信封。"""

    def boom(self, job, text):
        raise OSError("disk full")

    monkeypatch.setattr(TaskStore, "on_output", boom)
    target = _put_raw(kb)
    runner = make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md"))
    with TestClient(create_app(kb, runner=runner)) as client:
        task_id = client.post("/api/ingest", json={"target": target}).json()["task_id"]
        done = _wait_task(client, task_id)
    assert done["state"] == "succeeded"


def test_other_job_kinds_get_no_task(kb) -> None:
    """投喂等其它作业不建任务信封（只有补录/沉淀/heal/审计是后台任务）。"""
    with TestClient(create_app(kb)) as client:
        assert client.post("/api/raw", json={"name": "a.md", "content": "# a\n"}).status_code == 200
    assert not jobs_dir(kb).exists() or not list(jobs_dir(kb).glob("*.json"))


# ───────────────────────── 重启恢复（决策 D5）─────────────────────────


def test_recover_marks_dead_owner_tasks(kb) -> None:
    _write_env(kb, "20261101-100000-ingest-aaaa", state="running", finished=None)
    _write_env(kb, "20261101-100001-heal-bbbb", kind="heal", state="queued", finished=None)
    _write_env(kb, "20261101-100002-audit-cccc", kind="audit", state="running", finished=None, owner_pid=os.getppid())
    (jobs_dir(kb) / "20261101-100003-ingest-dddd.json").write_text("{坏", encoding="utf-8")

    with TestClient(create_app(kb)) as client:
        listing = client.get("/api/tasks").json()

    by_id = {r["id"]: r for g in listing.values() for r in g}
    assert by_id["20261101-100000-ingest-aaaa"]["state"] == "interrupted"
    assert by_id["20261101-100000-ingest-aaaa"]["group"] == "attention"
    assert by_id["20261101-100001-heal-bbbb"]["state"] == "cancelled"  # 未开始：不自动重排
    assert by_id["20261101-100002-audit-cccc"]["state"] == "running"  # 原进程还活着：不动
    assert "20261101-100003-ingest-dddd" not in by_id  # 坏文件跳过，不拖垮列表
    env = json.loads((jobs_dir(kb) / "20261101-100000-ingest-aaaa.json").read_text(encoding="utf-8"))
    assert env["state"] == "interrupted" and env["finished"]


def test_recover_same_pid_previous_boot(kb) -> None:
    """容器重启常以同一 pid 起来：pid 等于本进程、但不是本进程实例建的信封，照样恢复。"""
    _write_env(kb, "20261101-100000-ingest-aaaa", state="running", finished=None, owner_pid=os.getpid(), owner_boot="old")
    with TestClient(create_app(kb)) as client:
        listing = client.get("/api/tasks").json()
    by_id = {r["id"]: r for g in listing.values() for r in g}
    assert by_id["20261101-100000-ingest-aaaa"]["state"] == "interrupted"


def test_attention_never_crowded_out_by_recent(kb) -> None:
    """较早的 interrupted 不会被「最近完成 20 条」挤掉（决策 D7）。"""
    _write_env(kb, "20261101-090000-ingest-0000", state="interrupted", finished="2026-11-01 09:00:00")
    for i in range(30):
        _write_env(kb, f"20261102-1000{i:02d}-ingest-{i:04x}", finished=f"2026-11-02 10:00:{i:02d}")
    with TestClient(create_app(kb)) as client:
        listing = client.get("/api/tasks").json()
    assert [r["id"] for r in listing["attention"]] == ["20261101-090000-ingest-0000"]
    assert len(listing["recent"]) == tasks_mod.RECENT_LIMIT
    assert listing["recent"][0]["id"] == "20261102-100029-ingest-001d"  # 新→旧


# ───────────────────────── 保留策略（决策 D8）与一键入库清单的边界（D2）─────────────────────────


def _oneclick_manifest(kb, task_id: str, state: str = "done") -> None:
    d = kb / "workspace" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    task = {
        "task_id": task_id,
        "state": state,
        "title": "某本书",
        "created": "2026-11-01 08:00:00",
        "finished": "2026-11-01 08:30:00" if state == "done" else None,
        "items": [
            {"doc": "a", "raw": "raw/a.md", "land": "written", "sha256": "x", "state": "done"},
            {"doc": "b", "raw": "raw/b.md", "land": "written", "sha256": "y", "state": "pending" if state != "done" else "done"},
        ],
    }
    (d / f"{task_id}.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")


def test_retention_only_prunes_job_envelopes(kb, monkeypatch) -> None:
    monkeypatch.setattr(tasks_mod, "MAX_HISTORY", 3)
    _write_env(kb, "20261101-080000-ingest-0aaa", state="interrupted", finished="2026-11-01 08:00:00")
    for i in range(3):
        _write_env(kb, f"20261101-1000{i:02d}-ingest-{i:04x}", finished=f"2026-11-01 10:00:{i:02d}")
        (jobs_dir(kb) / f"20261101-1000{i:02d}-ingest-{i:04x}.log").write_text("x\n", encoding="utf-8")
    _oneclick_manifest(kb, "20261020-080000-某本书")
    before_owned = [t["task_id"] for t in iter_import_tasks(kb)]

    store = TaskStore(kb)
    task_id = store.create("ingest", "补录：raw/y.md", {"target": "raw/y.md"})
    store.on_start(_fake_job(task_id))
    store.on_finish(_fake_job(task_id))

    names = sorted(p.name for p in jobs_dir(kb).glob("*"))
    assert "20261101-100000-ingest-0000.json" not in names  # 最旧的终态被清
    assert "20261101-100000-ingest-0000.log" not in names  # 日志同删
    assert "20261101-080000-ingest-0aaa.json" in names  # 需要处理的不清
    assert f"{task_id}.json" in names
    # 一键入库清单一个不动，且 iter_tasks 读不到 workspace/jobs/ 的信封
    assert (kb / "workspace" / "tasks" / "20261020-080000-某本书.json").is_file()
    assert [t["task_id"] for t in iter_import_tasks(kb)] == before_owned


def test_import_manifests_listed_read_only(kb) -> None:
    _oneclick_manifest(kb, "20261101-080000-某本书", state="paused")
    _oneclick_manifest(kb, "20261020-080000-旧书", state="done")
    with TestClient(create_app(kb)) as client:
        listing = client.get("/api/tasks").json()
        detail = client.get("/api/tasks/20261101-080000-某本书").json()
    active = {r["id"]: r for r in listing["active"]}
    row = active["20261101-080000-某本书"]
    assert row["kind"] == "import" and row["source"] == "oneclick"
    assert row["state"] == "paused" and row["done"] == 1 and row["total"] == 2
    assert "20261020-080000-旧书" in [r["id"] for r in listing["recent"]]
    assert detail["kind"] == "import"


def test_summary_saved_on_finish(kb) -> None:
    store = TaskStore(kb, summarize=lambda job: {"receipts": [{"status": "resolved"}]})
    task_id = store.create("heal", "补全", {"limit": 5})
    store.on_start(_fake_job(task_id))
    store.on_output(_fake_job(task_id), "agent 散文\n")
    store.on_finish(_fake_job(task_id))
    d = store.detail(task_id)
    assert d["summary"] == {"receipts": [{"status": "resolved"}]}
    assert d["log"]["text"] == "agent 散文\n"
    # 新进程重新加载：信封与日志都还在
    again = TaskStore(kb).detail(task_id)
    assert again["state"] == "succeeded" and again["summary"] == d["summary"]


def test_running_brief(kb) -> None:
    store = TaskStore(kb)
    assert store.running_brief() is None
    task_id = store.create("backfill", "沉淀：问题", {"question": "问题"})
    store.on_start(_fake_job(task_id))
    brief = store.running_brief()
    assert brief["task_id"] == task_id and brief["kind"] == "backfill"
    store.on_finish(_fake_job(task_id))
    assert store.running_brief() is None


# ───────────────────────── 前端接线 ─────────────────────────


def test_frontend_wired() -> None:
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="tasks-btn"' in index and 'id="task-drawer"' in index and "#i-tasks" in index
    assert '<script src="/static/tasks.js"></script>' in index
    tasks_js = (STATIC_DIR / "tasks.js").read_text(encoding="utf-8")
    assert '"/api/tasks"' in tasks_js
    assert "/api/tasks/${encodeURIComponent(id)}" in tasks_js  # 消失的 id 补查详情
    jobs_js = (STATIC_DIR / "jobs.js").read_text(encoding="utf-8")
    assert jobs_js.count("submitBackgroundTask(") >= 5  # 定义 + 四个提交入口
    assert "trackSubmittedTask" in jobs_js
    chat_js = (STATIC_DIR / "chat.js").read_text(encoding="utf-8")
    assert '"waiting_for_task"' in chat_js and "chat.waitStopRequested" in chat_js
    assert '"tasks-btn"' in chat_js  # reader 下隐藏


# ───────────────────────── 评审补充：等锁期间不算 running；启动读不了 workspace ─────────────────────────


def test_task_stays_queued_while_waiting_for_write_lock(kb) -> None:
    """写锁被别人持有（如可写会话）时，排在 worker 上的任务仍是 queued：等待提示不把它说成「正在写」，
    服务此时退出也只会被恢复成「已取消、未写入」，而不是「可能已写入一部分」的 interrupted。"""
    target = _put_raw(kb)
    runner = make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md"))
    app = create_app(kb, runner=runner)
    with TestClient(app) as client:
        gate = app.state.write_gate
        gate.write_lock.acquire()
        try:
            task_id = client.post("/api/ingest", json={"target": target}).json()["task_id"]
            time.sleep(0.3)  # worker 已取到作业、正卡在取锁上
            assert client.get(f"/api/tasks/{task_id}").json()["state"] == "queued"
            assert app.state.tasks.running_brief() is None
            env = json.loads((jobs_dir(kb) / f"{task_id}.json").read_text(encoding="utf-8"))
            assert env["state"] == "queued" and env["started"] is None
        finally:
            gate.write_lock.release()
        done = _wait_task(client, task_id)
    assert done["state"] == "succeeded" and done["started"]


def test_task_store_constructor_failure_degrades(kb, monkeypatch) -> None:
    """启动时任务记录读不了（OSError）：Web 照常起来，列表为空、详情 404，提交照常执行。"""

    def boom(self, *a, **k):
        raise OSError("unreadable")

    monkeypatch.setattr(TaskStore, "__init__", boom)
    target = _put_raw(kb)
    runner = make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md"))
    with TestClient(create_app(kb, runner=runner)) as client:
        assert client.get("/api/tasks").json() == {"attention": [], "active": [], "recent": []}
        assert client.get("/api/tasks/20261101-100000-ingest-abcd").status_code == 404
        resp = client.post("/api/ingest", json={"target": target}).json()
        assert resp["task_id"] is None and resp["job_id"]


def test_writable_turn_starting_during_create_still_gets_423(kb, monkeypatch) -> None:
    """建信封的 await 期间起跑了可写 turn：入队前再查层③，仍 423，刚建的信封被撤掉、作业不入队。"""
    target = _put_raw(kb)
    app = create_app(kb, runner=make_runner(None))
    real_create = TaskStore.create

    def create_then_writable(self, *a, **k):
        task_id = real_create(self, *a, **k)
        app.state.write_gate.enter_writable()  # 模拟可写 turn 恰在此刻起跑
        return task_id

    monkeypatch.setattr(TaskStore, "create", create_then_writable)
    with TestClient(app) as client:
        try:
            assert client.post("/api/ingest", json={"target": target}).status_code == 423
        finally:
            app.state.write_gate.exit_writable()
        assert client.get("/api/tasks").json() == {"attention": [], "active": [], "recent": []}
    assert not list(jobs_dir(kb).glob("*.json"))
