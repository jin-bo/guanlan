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
        if data["state"] not in ("queued", "waiting_lock", "running"):
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

    monkeypatch.setattr(TaskStore, "create_or_reuse", boom)
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
    real_create = TaskStore.create_or_reuse

    def create_then_writable(self, *a, **k):
        out = real_create(self, *a, **k)
        app.state.write_gate.enter_writable()  # 模拟可写 turn 恰在此刻起跑
        return out

    monkeypatch.setattr(TaskStore, "create_or_reuse", create_then_writable)
    with TestClient(app) as client:
        try:
            assert client.post("/api/ingest", json={"target": target}).status_code == 423
        finally:
            app.state.write_gate.exit_writable()
        assert client.get("/api/tasks").json() == {"attention": [], "active": [], "recent": []}
    assert not list(jobs_dir(kb).glob("*.json"))


# ───────────────────────── T2 第一部分：取消（未开始）/ 等锁退避 / 查重与上限 / 重试 / 删除 ─────────────────────────

import threading  # noqa: E402

import guanlan.web.jobs as jobs_mod  # noqa: E402


def _wait_state(client, task_id: str, states: tuple, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/tasks/{task_id}").json()
        if data["state"] in states:
            return data
        time.sleep(0.02)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内进入 {states}：{data['state']}")


def test_cancel_queued_and_lock_waiting_tasks(kb) -> None:
    """写锁被占（如可写会话）时：等锁中的与排在后面的都能取消，Agent 一次都不被调用。"""
    for n in ("a.md", "b.md"):
        _put_raw(kb, n)
    runner = make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md"))
    app = create_app(kb, runner=runner)
    with TestClient(app) as client:
        gate = app.state.write_gate
        gate.write_lock.acquire()
        try:
            t1 = client.post("/api/ingest", json={"target": "raw/a.md"}).json()["task_id"]
            t2 = client.post("/api/ingest", json={"target": "raw/b.md"}).json()["task_id"]
            time.sleep(0.3)  # t1 已被 worker 取到、正在等锁；t2 还在队列里
            for tid in (t2, t1):
                r = client.post(f"/api/tasks/{tid}/cancel")
                assert r.status_code == 200 and r.json()["state"] == "cancelled"
                assert client.get(f"/api/tasks/{tid}").json()["state"] == "cancelled"  # 立即生效
            assert client.post(f"/api/tasks/{t1}/cancel").status_code == 409  # 已取消的不能再取消
        finally:
            gate.write_lock.release()
        time.sleep(0.6)  # worker 取到它们时只是跳过
        for tid in (t1, t2):
            d = client.get(f"/api/tasks/{tid}").json()
            assert d["state"] == "cancelled" and d["exit_code"] is None
    assert runner.calls == []


def test_cancel_running_is_409_for_now(kb) -> None:
    target = _put_raw(kb)
    release = threading.Event()

    def action(root):
        release.wait(5)
        _ingest_writes(root, "wiki/concepts/New.md")

    with TestClient(create_app(kb, runner=make_runner(action))) as client:
        tid = client.post("/api/ingest", json={"target": target}).json()["task_id"]
        _wait_state(client, tid, ("running",))
        assert client.post(f"/api/tasks/{tid}/cancel").status_code == 409
        release.set()
        assert _wait_task(client, tid)["state"] == "succeeded"
        assert client.post(f"/api/tasks/{tid}/cancel").status_code == 409  # 已结束
        assert client.post("/api/tasks/20261101-100000-ingest-abcd/cancel").status_code == 404


def test_import_tasks_are_controlled_in_staging(kb) -> None:
    _oneclick_manifest(kb, "20261101-080000-某本书", state="paused")
    with TestClient(create_app(kb)) as client:
        for path in ("cancel", "retry"):
            assert client.post(f"/api/tasks/20261101-080000-某本书/{path}").status_code == 409
        assert client.delete("/api/tasks/20261101-080000-某本书").status_code == 409


_CLI_HOLDER = """
import sys, time
from pathlib import Path
from guanlan import kblock
lock = kblock.acquire(Path(sys.argv[1]), "cli")
print("held", flush=True)
sys.stdin.read()  # 父进程关 stdin 即释放
"""


def _hold_kb_lock(kb):
    """另起一个进程持库级锁（同进程内取锁会被当作嵌套放行，模拟不了命令行写者）。"""
    env = {k: v for k, v in os.environ.items() if k != "GUANLAN_KB_LOCK_TOKEN"}
    proc = subprocess.Popen(
        [sys.executable, "-c", _CLI_HOLDER, str(kb)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
    )
    assert proc.stdout.readline().strip() == "held"
    return proc


def _release(proc) -> None:
    proc.stdin.close()
    proc.wait(timeout=5)


def test_waits_for_cli_lock_then_runs(kb, monkeypatch) -> None:
    monkeypatch.setattr(jobs_mod, "LOCK_BACKOFF_START_S", 0.1)
    target = _put_raw(kb)
    runner = make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md"))
    holder = _hold_kb_lock(kb)
    try:
        with TestClient(create_app(kb, runner=runner)) as client:
            tid = client.post("/api/ingest", json={"target": target}).json()["task_id"]
            d = _wait_state(client, tid, ("waiting_lock",))
            assert "cli" in (d["holder"] or "")
            assert client.get("/api/tasks").json()["active"][0]["state"] == "waiting_lock"
            _release(holder)
            done = _wait_task(client, tid)
            assert done["state"] == "succeeded" and done["holder"] is None
    finally:
        if holder.poll() is None:
            _release(holder)


def test_cli_lock_wait_gives_up_after_limit(kb, monkeypatch) -> None:
    monkeypatch.setattr(jobs_mod, "LOCK_BACKOFF_START_S", 0.05)
    monkeypatch.setattr(jobs_mod, "LOCK_WAIT_LIMIT_S", 0.4)
    target = _put_raw(kb)
    runner = make_runner(None)
    holder = _hold_kb_lock(kb)
    try:
        with TestClient(create_app(kb, runner=runner)) as client:
            tid = client.post("/api/ingest", json={"target": target}).json()["task_id"]
            done = _wait_task(client, tid)
            assert done["state"] == "failed" and "占用" in done["log"]["text"]
    finally:
        _release(holder)
    assert runner.calls == []


def test_cancel_while_waiting_for_cli_lock(kb, monkeypatch) -> None:
    monkeypatch.setattr(jobs_mod, "LOCK_BACKOFF_START_S", 5.0)  # 正睡在退避里：取消要在 LOCK_POLL_S 内生效
    target = _put_raw(kb)
    runner = make_runner(None)
    holder = _hold_kb_lock(kb)
    try:
        with TestClient(create_app(kb, runner=runner)) as client:
            tid = client.post("/api/ingest", json={"target": target}).json()["task_id"]
            _wait_state(client, tid, ("waiting_lock",))
            assert client.post(f"/api/tasks/{tid}/cancel").status_code == 200
            time.sleep(0.5)
            job = next(j for j in app_jobs(client) if j.task_id == tid)
            assert job.state == "done" and job.cancelled
    finally:
        _release(holder)
    assert runner.calls == []


def app_jobs(client):
    return list(client.app.state.jobs._jobs.values())


def test_duplicate_submission_reuses_task(kb) -> None:
    target = _put_raw(kb)
    app = create_app(kb, runner=make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md")))
    with TestClient(app) as client:
        app.state.write_gate.write_lock.acquire()
        try:
            first = client.post("/api/ingest", json={"target": target}).json()
            again = client.post("/api/ingest", json={"target": target}).json()
            other = client.post("/api/backfill", json={"question": "问题"}).json()
        finally:
            app.state.write_gate.write_lock.release()
        assert again == {"job_id": None, "task_id": first["task_id"], "deduplicated": True}
        assert other["task_id"] != first["task_id"] and not other["deduplicated"]
        _wait_task(client, first["task_id"])
        # 结束之后再提交同样的，是新任务
        assert client.post("/api/ingest", json={"target": target}).json()["deduplicated"] is False
    assert len([j for j in app.state.jobs._jobs.values() if j.kind == "ingest"]) == 2


def test_active_task_limit(kb, monkeypatch) -> None:
    monkeypatch.setattr(tasks_mod, "MAX_ACTIVE", 2)
    for n in ("a.md", "b.md", "c.md"):
        _put_raw(kb, n)
    app = create_app(kb, runner=make_runner(None))
    with TestClient(app) as client:
        app.state.write_gate.write_lock.acquire()
        try:
            assert client.post("/api/ingest", json={"target": "raw/a.md"}).status_code == 200
            assert client.post("/api/ingest", json={"target": "raw/b.md"}).status_code == 200
            r = client.post("/api/ingest", json={"target": "raw/c.md"})
            assert r.status_code == 429 and "上限" in r.json()["detail"]
        finally:
            app.state.write_gate.write_lock.release()
    assert len(list(jobs_dir(kb).glob("*.json"))) == 2  # 被拒的没留信封


def test_retry_failed_task(kb) -> None:
    target = _put_raw(kb)
    outcome = {"ok": False}

    def runner(prompt, **kw):
        from guanlan.runtime import AgentRunResult

        if outcome["ok"]:
            _ingest_writes(kw["working_directory"], "wiki/concepts/New.md")
        return AgentRunResult(ok=outcome["ok"], final_text="x", error_type=None if outcome["ok"] else "boom")

    app = create_app(kb, runner=runner)
    with TestClient(app) as client:
        tid = client.post("/api/ingest", json={"target": target}).json()["task_id"]
        assert _wait_task(client, tid)["state"] == "failed"
        outcome["ok"] = True
        r = client.post(f"/api/tasks/{tid}/retry").json()
        assert r["task_id"] != tid and not r["deduplicated"]
        done = _wait_task(client, r["task_id"])
        assert done["state"] == "succeeded" and done["retry_of"] == tid and done["params"] == {"target": target}
        # 层③：可写会话进行中不能重试
        app.state.write_gate.enter_writable()
        try:
            assert client.post(f"/api/tasks/{tid}/retry").status_code == 423
        finally:
            app.state.write_gate.exit_writable()


def test_retry_rejects_active_and_bad_params(kb) -> None:
    _write_env(kb, "20261101-100000-ingest-aaaa", state="running", finished=None, owner_pid=os.getppid())
    _write_env(kb, "20261101-100001-heal-bbbb", kind="heal", state="failed", params={"limit": 0})
    with TestClient(create_app(kb)) as client:
        assert client.post("/api/tasks/20261101-100000-ingest-aaaa/retry").status_code == 409
        r = client.post("/api/tasks/20261101-100001-heal-bbbb/retry")
        assert r.status_code == 409 and "参数" in r.json()["detail"]


def test_delete_task_record(kb) -> None:
    _write_env(kb, "20261101-100000-ingest-aaaa")
    (jobs_dir(kb) / "20261101-100000-ingest-aaaa.log").write_text("x\n", encoding="utf-8")
    (jobs_dir(kb) / "20261101-100000-ingest-aaaa").mkdir()
    _write_env(kb, "20261101-100001-ingest-bbbb", state="interrupted")
    (jobs_dir(kb) / "20261101-100001-ingest-bbbb" / "before").mkdir(parents=True)  # 还有未处理的前像
    _write_env(kb, "20261101-100002-ingest-cccc", state="running", finished=None, owner_pid=os.getppid())
    with TestClient(create_app(kb)) as client:
        assert client.delete("/api/tasks/20261101-100000-ingest-aaaa").status_code == 200
        assert client.get("/api/tasks/20261101-100000-ingest-aaaa").status_code == 404
        r = client.delete("/api/tasks/20261101-100001-ingest-bbbb")
        assert r.status_code == 409 and "前像" in r.json()["detail"]
        assert client.delete("/api/tasks/20261101-100002-ingest-cccc").status_code == 409
    names = {p.name for p in jobs_dir(kb).iterdir()}
    assert not any(n.startswith("20261101-100000-ingest-aaaa") for n in names)


def test_recover_waiting_lock_as_cancelled(kb) -> None:
    _write_env(kb, "20261101-100000-ingest-aaaa", state="waiting_lock", finished=None, holder="cli，pid 1")
    with TestClient(create_app(kb)) as client:
        d = client.get("/api/tasks/20261101-100000-ingest-aaaa").json()
    assert d["state"] == "cancelled"


def test_prune_keeps_tasks_with_preimage(kb, monkeypatch) -> None:
    monkeypatch.setattr(tasks_mod, "MAX_HISTORY", 1)
    _write_env(kb, "20261101-100000-ingest-aaaa", finished="2026-11-01 10:00:00")
    (jobs_dir(kb) / "20261101-100000-ingest-aaaa" / "before").mkdir(parents=True)
    _write_env(kb, "20261101-100001-ingest-bbbb", finished="2026-11-01 10:00:01")
    (jobs_dir(kb) / "20261101-100001-ingest-bbbb").mkdir()
    store = TaskStore(kb)
    tid = store.create("ingest", "补录", {"target": "raw/z.md"})
    store.on_start(_fake_job(tid))
    store.on_finish(_fake_job(tid))
    names = {p.name for p in jobs_dir(kb).iterdir()}
    assert "20261101-100000-ingest-aaaa.json" in names  # 有前像：不自动清
    assert "20261101-100001-ingest-bbbb.json" not in names and "20261101-100001-ingest-bbbb" not in names


def test_task_waiting_for_cli_does_not_block_worker(kb, monkeypatch) -> None:
    """等命令行释放库的任务不占 worker：排在它后面的投喂照旧立即失败（409），不跟着等。"""
    monkeypatch.setattr(jobs_mod, "LOCK_BACKOFF_START_S", 5.0)
    target = _put_raw(kb)
    holder = _hold_kb_lock(kb)
    try:
        with TestClient(create_app(kb, runner=make_runner(None))) as client:
            tid = client.post("/api/ingest", json={"target": target}).json()["task_id"]
            _wait_state(client, tid, ("waiting_lock",))
            t0 = time.monotonic()
            r = client.post("/api/raw", json={"name": "feed.md", "content": "# 投喂\n"})
            assert r.status_code == 409 and time.monotonic() - t0 < 3
            assert client.get(f"/api/tasks/{tid}").json()["state"] == "waiting_lock"  # 仍在等，没被判失败
            assert client.post(f"/api/tasks/{tid}/cancel").status_code == 200
    finally:
        _release(holder)


def test_dedupe_and_limit_ignore_other_processes(kb, monkeypatch) -> None:
    """别的进程（还活着，recover 不动它）留下的未结束信封：不参与本进程的查重与上限。"""
    monkeypatch.setattr(tasks_mod, "MAX_ACTIVE", 1)
    target = _put_raw(kb)
    _write_env(kb, "20261101-100000-ingest-aaaa", state="queued", finished=None,
               params={"target": target}, owner_pid=os.getppid())
    with TestClient(create_app(kb, runner=make_runner(lambda root: _ingest_writes(root, "wiki/concepts/New.md")))) as client:
        r = client.post("/api/ingest", json={"target": target}).json()
        assert r["task_id"] != "20261101-100000-ingest-aaaa" and not r["deduplicated"]
        assert _wait_task(client, r["task_id"])["state"] == "succeeded"


def test_parked_tasks_keep_submission_order(kb, monkeypatch) -> None:
    """等命令行释放库期间：先提交的任务退避间隔已经长了，后提交的也不能先跑（「先 ingest 再 heal」这类依赖）。"""
    monkeypatch.setattr(jobs_mod, "LOCK_BACKOFF_START_S", 0.05)
    for n in ("a.md", "b.md"):
        _put_raw(kb, n)
    order: list[str] = []

    def runner(prompt, **kw):
        from guanlan.runtime import AgentRunResult

        name = "a" if "raw/a.md" in prompt else "b"
        order.append(name)
        _ingest_writes(kw["working_directory"], f"wiki/concepts/{name}.md", raw=f"{name}.md")
        return AgentRunResult(ok=True, final_text="x", error_type=None)

    holder = _hold_kb_lock(kb)
    try:
        with TestClient(create_app(kb, runner=runner)) as client:
            ta = client.post("/api/ingest", json={"target": "raw/a.md"}).json()["task_id"]
            _wait_state(client, ta, ("waiting_lock",))
            time.sleep(0.6)  # a 已退避多轮，间隔涨到远大于 b 的起始间隔
            tb = client.post("/api/ingest", json={"target": "raw/b.md"}).json()["task_id"]
            time.sleep(0.1)
            _release(holder)
            assert _wait_task(client, ta, timeout=10)["state"] == "succeeded"
            assert _wait_task(client, tb, timeout=10)["state"] == "succeeded"
    finally:
        if holder.poll() is None:
            _release(holder)
    assert order == ["a", "b"]


def test_cancel_is_atomic_with_envelope(kb) -> None:
    """执行层撤不下作业（已开始）时信封不动；撤下了才记 cancelled。"""
    store = TaskStore(kb)
    tid = store.create("ingest", "补录", {"target": "raw/x.md"})
    assert store.cancel(tid, lambda _id: False) is False
    assert store.envelope(tid)["state"] == "queued"
    assert store.cancel(tid, lambda _id: True) is True
    assert store.envelope(tid)["state"] == "cancelled"
    # 取消后同参数再提交：是新任务，不会并到已取消的那个上
    tid2, created = store.create_or_reuse("ingest", "补录", {"target": "raw/x.md"})
    assert created and tid2 != tid


def test_dedupe_heal_targets_order_and_model(kb) -> None:
    app = create_app(kb, runner=make_runner(None))
    with TestClient(app) as client:
        app.state.write_gate.write_lock.acquire()
        try:
            a = client.post("/api/heal", json={"targets": ["乙", "甲"]}).json()
            b = client.post("/api/heal", json={"targets": ["甲", "乙"]}).json()
            c = client.post("/api/heal", json={"targets": ["甲", "乙"], "model": "other/m"}).json()
        finally:
            app.state.write_gate.write_lock.release()
        assert b["deduplicated"] and b["task_id"] == a["task_id"]
        assert not c["deduplicated"] and c["task_id"] != a["task_id"]
        env = client.get(f"/api/tasks/{c['task_id']}").json()
        assert env["params"]["targets"] == sorted(["甲", "乙"])  # 排序后存
        assert env["params"]["model"] == "other/m"


def test_task_parked_behind_reports_waiting(kb, monkeypatch) -> None:
    """排在一个等锁任务后面停放的任务，也显示「等待写锁」与占用者（它等的是同一个命令行）。"""
    monkeypatch.setattr(jobs_mod, "LOCK_BACKOFF_START_S", 0.05)
    for n in ("a.md", "b.md"):
        _put_raw(kb, n)
    holder = _hold_kb_lock(kb)
    try:
        with TestClient(create_app(kb, runner=make_runner(None))) as client:
            ta = client.post("/api/ingest", json={"target": "raw/a.md"}).json()["task_id"]
            _wait_state(client, ta, ("waiting_lock",))
            tb = client.post("/api/ingest", json={"target": "raw/b.md"}).json()["task_id"]
            d = _wait_state(client, tb, ("waiting_lock",))
            assert "cli" in (d["holder"] or "")
            for tid in (ta, tb):
                assert client.post(f"/api/tasks/{tid}/cancel").status_code == 200
    finally:
        _release(holder)


def test_explicit_default_model_dedupes_with_omitted(kb) -> None:
    target = _put_raw(kb)
    app = create_app(kb, model="prov/default-m", runner=make_runner(None))
    with TestClient(app) as client:
        app.state.write_gate.write_lock.acquire()
        try:
            a = client.post("/api/ingest", json={"target": target}).json()
            b = client.post("/api/ingest", json={"target": target, "model": "prov/default-m"}).json()
        finally:
            app.state.write_gate.write_lock.release()
    assert b["deduplicated"] and b["task_id"] == a["task_id"]


def test_lock_wait_deadline_restarts_after_lock_was_taken() -> None:
    """停放在前面任务后面时记下的等待截止时刻，只属于那一段占用：其间有作业取到过写锁（占用已结束），
    再遇到占用要重新起算——否则一个排了很久的任务一遇占用就立即判失败。"""
    q = jobs_mod.JobQueue()
    job = jobs_mod.Job(id="1", kind="ingest", task_id="t")
    q._begin_wait(job, 100.0)
    assert job.lock_wait_deadline == 100.0 + jobs_mod.LOCK_WAIT_LIMIT_S
    q._begin_wait(job, 200.0)  # 同一段：不变
    assert job.lock_wait_deadline == 100.0 + jobs_mod.LOCK_WAIT_LIMIT_S
    q._lock_epoch += 1  # 期间有作业取到了写锁
    q._begin_wait(job, 5000.0)
    assert job.lock_wait_deadline == 5000.0 + jobs_mod.LOCK_WAIT_LIMIT_S


def test_pending_envelope_not_used_for_dedupe(kb) -> None:
    """建好信封、还没入队（入队前的层③ 复查可能撤掉它）：同参数的并发提交不并到它上面。"""
    store = TaskStore(kb)
    t1, created1 = store.create_or_reuse("ingest", "补录", {"target": "raw/x.md"})
    t2, created2 = store.create_or_reuse("ingest", "补录", {"target": "raw/x.md"})
    assert created1 and created2 and t1 != t2
    store.discard(t1)
    store.committed(t2)
    t3, created3 = store.create_or_reuse("ingest", "补录", {"target": "raw/x.md"})
    assert not created3 and t3 == t2


def test_unparked_task_returns_to_queued(kb, monkeypatch) -> None:
    """命令行释放库、停放的任务放回队列后：信封回到 queued、清掉占用者，不再挂着已经不在的命令行。"""
    monkeypatch.setattr(jobs_mod, "LOCK_BACKOFF_START_S", 0.05)
    for n in ("a.md", "b.md"):
        _put_raw(kb, n)
    release_a = threading.Event()

    def runner(prompt, **kw):
        from guanlan.runtime import AgentRunResult

        if "raw/a.md" in prompt:
            release_a.wait(5)
        return AgentRunResult(ok=False, final_text="x", error_type="boom")

    holder = _hold_kb_lock(kb)
    try:
        with TestClient(create_app(kb, runner=runner)) as client:
            ta = client.post("/api/ingest", json={"target": "raw/a.md"}).json()["task_id"]
            _wait_state(client, ta, ("waiting_lock",))
            tb = client.post("/api/ingest", json={"target": "raw/b.md"}).json()["task_id"]
            _wait_state(client, tb, ("waiting_lock",))
            _release(holder)
            _wait_state(client, ta, ("running",))  # a 拿到锁在跑；b 排在它后面
            d = _wait_state(client, tb, ("queued",))
            assert d["holder"] is None
            release_a.set()
            _wait_task(client, tb, timeout=10)
    finally:
        release_a.set()
        if holder.poll() is None:
            _release(holder)


def test_concurrent_identical_submissions_dedupe(kb) -> None:
    """同参数的并发提交（连点两下）：提交路径串行，后一个并到前一个已入队的任务上，只入队一次。"""
    import concurrent.futures

    target = _put_raw(kb)
    app = create_app(kb, runner=make_runner(None))
    with TestClient(app) as client:
        app.state.write_gate.write_lock.acquire()
        try:
            with concurrent.futures.ThreadPoolExecutor(4) as ex:
                results = list(ex.map(lambda _: client.post("/api/ingest", json={"target": target}).json(), range(4)))
        finally:
            app.state.write_gate.write_lock.release()
    assert len({r["task_id"] for r in results}) == 1
    assert sum(not r["deduplicated"] for r in results) == 1
    assert len([j for j in app.state.jobs._jobs.values() if j.kind == "ingest"]) == 1


def test_park_timer_start_failure_requeues(kb) -> None:
    """停放定时器起不来（线程耗尽）：停放的任务直接放回队列，不会永远卡在停放里。"""
    from guanlan.web.jobs import JobQueue

    jq = JobQueue()
    job = Job(id="7", kind="ingest", task_id="t")
    item = (job, lambda emit: 0, None)

    class Boom:
        def __init__(self, *a, **k):
            self.daemon = True

        def start(self):
            raise RuntimeError("can't start new thread")

        def cancel(self):
            pass

    orig = jobs_mod.threading.Timer
    jobs_mod.threading.Timer = Boom
    try:
        jq._park(item, 1.0)
    finally:
        jobs_mod.threading.Timer = orig
    assert jq._parked == {} and jq._park_timer is None
    assert job.done_event.wait(3)  # 被放回、由 worker 跑完


def test_backfill_question_whitespace_dedupes(kb) -> None:
    app = create_app(kb, runner=make_runner(None))
    with TestClient(app) as client:
        app.state.write_gate.write_lock.acquire()
        try:
            a = client.post("/api/backfill", json={"question": "某个问题"}).json()
            b = client.post("/api/backfill", json={"question": "  某个问题 \n"}).json()
        finally:
            app.state.write_gate.write_lock.release()
    assert b["deduplicated"] and b["task_id"] == a["task_id"]
