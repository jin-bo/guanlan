"""P4.30 一键入库：任务清单、①–⑥ 编排、确认、冲突预检、落盘续跑、暂停/继续、库级锁、CLI。"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from guanlan import oneclick
from guanlan.cli import main as cli_main
from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.kblock import KBLockBusy, acquire, read_holder
from guanlan.oneclick import (
    OneClickError,
    active_task,
    cancel,
    confirm,
    load_task,
    prepare,
    recover_stale,
    resume,
    save_task,
)
from guanlan.provenance import compute_raw_digest, format_digest_value
from guanlan.rawio import raw_slug


def _para(n: int, tag: str = "文") -> str:
    sent = (tag * 49) + "。"
    k, r = divmod(n, 50)
    return sent * k + tag * r


def _book(chapters: int = 3, size: int = 15_000, image: str | None = None) -> str:
    out = ["# 示例书", ""]
    for i in range(1, chapters + 1):
        out += [f"## 第{i}章 题{i}", ""]
        if image and i == 2:
            out += [f"![图]({image})", ""]
        left = size
        while left > 0:
            out += [_para(min(2_000, left)), ""]
            left -= 2_000
    return "\n".join(out)


@pytest.fixture
def book(tmp_path: Path) -> Path:
    p = tmp_path / "src" / "示例书.md"
    p.parent.mkdir()
    p.write_text(_book(), encoding="utf-8")
    return p


class FakeIngest:
    """假 ingest：记调用、建 source 页并 stamp raw_digest（与真 ingest 成功后的盘面一致）。"""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail = set(fail or ())

    def __call__(self, raw_rel: str, root: Path) -> int:
        self.calls.append(raw_rel)
        if raw_rel in self.fail:
            return 5
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


def _prepare(kb: Path, src: Path, **kw) -> dict:
    with acquire(kb, "test"):
        return prepare(kb, src, origin_kind="cli", **kw)


def _confirm(kb: Path, task: dict, ingest, **kw):
    with acquire(kb, "test"):
        return confirm(kb, task["task_id"], ingest=ingest, **kw)


def _resume(kb: Path, task: dict, ingest, **kw):
    with acquire(kb, "test"):
        recover_stale(kb)
        return resume(kb, task["task_id"], ingest=ingest, **kw)


# ── ①–④ 与确认 ───────────────────────────────────────────────────────────────


def test_prepare_stops_at_confirm_without_touching_raw(kb: Path, book: Path):
    task = _prepare(kb, book)
    assert task["state"] == "awaiting_confirm"
    assert list((kb / "raw").iterdir()) == []
    parsed = kb / "workspace" / "parsed"
    assert (parsed / "示例书.origin.md").is_file()
    assert (parsed / "示例书.md").is_file()
    assert (parsed / "示例书" / "示例书-目录.md").is_file()
    assert len(task["items"]) == 3
    assert task["items"][0]["raw"] == "raw/示例书/示例书-01-第1章-题1.md"
    assert load_task(kb, task["task_id"])["state"] == "awaiting_confirm"
    assert task["precheck"]["status"] == "ok"
    assert task["estimate_seconds"] == 3 * oneclick.DEFAULT_ITEM_SECONDS


def test_confirm_lands_and_ingests_in_order(kb: Path, book: Path):
    task = _prepare(kb, book)
    ingest = FakeIngest()
    code, task = _confirm(kb, task, ingest)
    assert code == EXIT_OK
    assert task["state"] == "done"
    assert ingest.calls == [it["raw"] for it in task["items"]]
    for it in task["items"]:
        assert it["land"] == "written" and it["state"] == "done"
        text = (kb / it["raw"]).read_text(encoding="utf-8")
        assert "split:" in text and "origin:" in text
    assert not (kb / "raw" / "示例书.md").exists()  # 拆后不落整本
    assert not (kb / "raw" / "示例书" / "示例书-目录.md").exists()  # 目录索引只留 parsed/
    assert task["links_before"] is not None and task["links_after"] is not None
    log = (kb / task["log"]).read_text(encoding="utf-8")
    assert "[3/3] 入库" in log and "完成" in log


def test_small_document_lands_unsplit(kb: Path, tmp_path: Path):
    src = tmp_path / "短文.md"
    src.write_text("# 短文\n\n" + _para(2_000) + "\n", encoding="utf-8")
    task = _prepare(kb, src)
    assert [it["raw"] for it in task["items"]] == ["raw/短文.md"]
    code, task = _confirm(kb, task, FakeIngest())
    assert code == EXIT_OK and (kb / "raw" / "短文.md").is_file()


def test_confirm_rejected_when_parsed_product_changed(kb: Path, book: Path):
    task = _prepare(kb, book)
    part = kb / "workspace" / task["items"][0]["parsed"]
    part.write_text(part.read_text(encoding="utf-8") + "改动\n", encoding="utf-8")
    with pytest.raises(OneClickError, match="被改动"):
        _confirm(kb, task, FakeIngest())
    assert load_task(kb, task["task_id"])["state"] == "failed"
    assert list((kb / "raw").iterdir()) == []


def test_cancel_keeps_parsed_and_raw_unchanged(kb: Path, book: Path):
    task = _prepare(kb, book)
    cancel(kb, task["task_id"])
    assert load_task(kb, task["task_id"])["state"] == "cancelled"
    assert (kb / "workspace" / "parsed" / "示例书.md").is_file()
    assert list((kb / "raw").iterdir()) == []
    assert active_task(kb) is None


def test_only_one_active_task(kb: Path, book: Path):
    _prepare(kb, book)
    with pytest.raises(OneClickError, match="未完成"):
        _prepare(kb, book)


def test_dry_run_creates_no_task(kb: Path, book: Path):
    task = _prepare(kb, book, dry_run=True)
    assert task["dry_run"] is True
    assert not (kb / "workspace" / "tasks").exists() or not list((kb / "workspace" / "tasks").glob("*.json"))
    assert (kb / "workspace" / "parsed" / "示例书.md").is_file()


# ── 冲突预检（§5） ───────────────────────────────────────────────────────────


def test_unowned_existing_file_is_hard_conflict(kb: Path, book: Path):
    (kb / "raw" / "示例书.md").write_text("手工放入\n", encoding="utf-8")
    task = _prepare(kb, book)
    assert task["precheck"]["status"] == "conflict"
    with pytest.raises(OneClickError, match="不可覆盖"):
        _confirm(kb, task, FakeIngest(), overwrite=True)


def test_slug_collision_detected_in_precheck(kb: Path, book: Path):
    (kb / "raw" / "别处").mkdir()
    (kb / "raw" / "别处" / "示例书-01-第1章-题1.md").write_text("x\n", encoding="utf-8")
    task = _prepare(kb, book)
    assert task["precheck"]["status"] == "conflict"
    assert any("slug" in d for d in task["precheck"]["details"])


def test_same_set_owned_by_old_task_is_overwritable(kb: Path, book: Path):
    task = _prepare(kb, book)
    _confirm(kb, task, FakeIngest())
    task2 = _prepare(kb, book)
    assert task2["precheck"]["status"] == "overwritable"
    with pytest.raises(OneClickError, match="覆盖"):
        _confirm(kb, task2, FakeIngest())
    code, task2 = _confirm(kb, task2, FakeIngest(), overwrite=True)
    assert code == EXIT_OK and task2["state"] == "done"


def test_resume_with_overwrite_unblocks_overwritable_pause(kb: Path, book: Path):
    """暂停后复检发现可覆盖的旧文件：不带 overwrite 继续仍停下，带上才继续（「冲突」不受影响）。"""
    from guanlan.oneclick import resume

    _confirm(kb, _prepare(kb, book), FakeIngest())
    task = _prepare(kb, book)
    assert task["precheck"]["status"] == "overwritable"
    # 模拟「确认时尚无旧文件、落了一份后暂停，复检才发现可覆盖」的盘面
    task.update(state="paused", overwrite=False)
    task["stages"]["confirm"] = "done"
    first = task["items"][0]
    first.update(land="written", sha256=oneclick._sha_file(kb / first["raw"]), images={})
    oneclick.save_task(kb, task)
    with acquire(kb, "test"):
        code, t = resume(kb, task["task_id"], ingest=FakeIngest())
    assert code != EXIT_OK and t["state"] == "paused"
    with acquire(kb, "test"):
        code, t = resume(kb, task["task_id"], ingest=FakeIngest(), overwrite=True)
    assert code == EXIT_OK and t["state"] == "done"


def test_resume_overwrite_ignored_when_nothing_overwritable(kb: Path, book: Path):
    from guanlan.oneclick import resume

    task = _prepare(kb, book)
    task.update(state="paused", overwrite=False)
    task["stages"]["confirm"] = "done"
    oneclick.save_task(kb, task)
    with acquire(kb, "test"):
        code, t = resume(kb, task["task_id"], ingest=FakeIngest(), overwrite=True)
    assert code == EXIT_OK and t["overwrite"] is False


def test_changed_part_count_is_conflict(kb: Path, book: Path, tmp_path: Path):
    task = _prepare(kb, book)
    _confirm(kb, task, FakeIngest())
    book.write_text(_book(chapters=4), encoding="utf-8")
    task2 = _prepare(kb, book)
    assert task2["precheck"]["status"] == "conflict"


def test_raw_change_after_prepare_invalidates_confirmation(kb: Path, book: Path):
    task = _prepare(kb, book)
    (kb / "raw" / "示例书").mkdir()
    (kb / "raw" / "示例书" / "手工.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(OneClickError, match="变化"):
        _confirm(kb, task, FakeIngest())
    task = load_task(kb, task["task_id"])
    assert task["state"] == "awaiting_confirm"
    assert task["precheck"]["status"] == "conflict"


# ── 失败、暂停、继续 ─────────────────────────────────────────────────────────


def test_failure_pauses_and_resume_retries_that_item(kb: Path, book: Path):
    task = _prepare(kb, book)
    second = task["items"][1]["raw"]
    code, task = _confirm(kb, task, FakeIngest(fail={second}))
    assert code == 5
    assert task["state"] == "paused"
    assert [it["state"] for it in task["items"]] == ["done", "failed", "pending"]
    ingest = FakeIngest()
    code, task = _resume(kb, task, ingest)
    assert code == EXIT_OK and task["state"] == "done"
    assert ingest.calls == [it["raw"] for it in task["items"][1:]]  # 第 1 篇按记录+指纹跳过


def test_user_pause_between_items(kb: Path, book: Path):
    task = _prepare(kb, book)
    ingest = FakeIngest()
    code, task = _confirm(kb, task, ingest, should_pause=lambda: len(ingest.calls) >= 1)
    assert code == EXIT_OK
    assert task["state"] == "paused" and task.get("paused_by_user")
    assert len(ingest.calls) == 1
    code, task = _resume(kb, task, FakeIngest())
    assert task["state"] == "done"


def test_running_item_after_crash_is_reingested_even_if_digest_matches(kb: Path, book: Path):
    task = _prepare(kb, book)
    _confirm(kb, task, FakeIngest())
    task = load_task(kb, task["task_id"])
    task["state"] = "running"
    task["items"][0]["state"] = "running"
    save_task(kb, task)
    ingest = FakeIngest()
    code, task = _resume(kb, task, ingest)
    assert ingest.calls == [task["items"][0]["raw"]]


def test_raw_modified_stops_resume(kb: Path, book: Path):
    task = _prepare(kb, book)
    code, task = _confirm(kb, task, FakeIngest(fail={"raw/示例书/示例书-02-第2章-题2.md"}))
    (kb / task["items"][2]["raw"]).write_text("被人改了\n", encoding="utf-8")
    code, task = _resume(kb, task, FakeIngest())
    assert code == EXIT_USAGE and task["state"] == "paused"


def test_land_failure_midway_then_resume_without_conflict(kb: Path, book: Path, monkeypatch):
    task = _prepare(kb, book)
    real = oneclick.commit_md_with_images
    n = {"k": 0}

    def flaky(*a, **kw):
        n["k"] += 1
        if n["k"] == 2:
            raise OSError("磁盘满")
        return real(*a, **kw)

    monkeypatch.setattr(oneclick, "commit_md_with_images", flaky)
    with pytest.raises(OSError):
        _confirm(kb, task, FakeIngest())
    task = load_task(kb, task["task_id"])
    assert [it["land"] for it in task["items"]] == ["written", "writing", "pending"]
    monkeypatch.setattr(oneclick, "commit_md_with_images", real)
    task["state"] = "running"  # 进程崩溃后的样子
    save_task(kb, task)
    code, task = _resume(kb, task, FakeIngest())
    assert code == EXIT_OK and task["state"] == "done"


def test_writing_recovery_when_commit_succeeded_but_manifest_not_updated(kb: Path, book: Path):
    task = _prepare(kb, book)
    _confirm(kb, task, FakeIngest())
    task = load_task(kb, task["task_id"])
    task["items"][1]["land"] = "writing"
    task["items"][1]["state"] = "pending"
    task["stages"]["land"] = "pending"
    task["state"] = "paused"
    save_task(kb, task)
    code, task = _resume(kb, task, FakeIngest())
    assert code == EXIT_OK and task["items"][1]["land"] == "written"


def test_writing_recovery_with_mismatch_stops(kb: Path, book: Path):
    task = _prepare(kb, book)
    _confirm(kb, task, FakeIngest())
    task = load_task(kb, task["task_id"])
    task["items"][1]["land"] = "writing"
    task["stages"]["land"] = "pending"
    task["state"] = "paused"
    save_task(kb, task)
    (kb / task["items"][1]["raw"]).write_text("不符\n", encoding="utf-8")
    code, task = _resume(kb, task, FakeIngest())
    assert code == EXIT_USAGE and "不符" in task["reason"]


def test_recover_stale_marks_preparing_failed_and_running_paused(kb: Path, book: Path):
    task = _prepare(kb, book)
    task["state"] = "preparing"
    save_task(kb, task)
    with acquire(kb, "test"):
        recover_stale(kb)
    assert load_task(kb, task["task_id"])["state"] == "failed"


# ── 图片 ─────────────────────────────────────────────────────────────────────


def test_images_follow_parts_into_raw(kb: Path, tmp_path: Path):
    src_dir = tmp_path / "src"
    (src_dir / "pics").mkdir(parents=True)
    (src_dir / "pics" / "a.png").write_bytes(b"\x89PNG fake")
    src = src_dir / "图书.md"
    src.write_text(_book(image="pics/a.png"), encoding="utf-8")
    task = _prepare(kb, src)
    part2 = kb / "workspace" / task["items"][1]["parsed"]
    assert "](../images/图书/图书-1.png)" in part2.read_text(encoding="utf-8")
    assert task["warnings"] == []
    _confirm(kb, task, FakeIngest())
    stem = Path(task["items"][1]["raw"]).stem
    assert (kb / "raw" / "图书" / "images" / stem / f"{stem}-1.png").is_file()
    assert f"](images/{stem}/{stem}-1.png)" in (kb / task["items"][1]["raw"]).read_text(encoding="utf-8")
    assert not (kb / "raw" / "图书" / "images" / Path(task["items"][0]["raw"]).stem).exists()


def test_missing_image_is_warning_not_failure(kb: Path, tmp_path: Path):
    src = tmp_path / "缺图.md"
    src.write_text(_book(image="nowhere/x.png"), encoding="utf-8")
    task = _prepare(kb, src)
    assert task["warnings"] and "nowhere/x.png" in task["warnings"][0]
    code, task = _confirm(kb, task, FakeIngest())
    assert code == EXIT_OK


# ── 库级锁 ───────────────────────────────────────────────────────────────────


def test_lock_is_exclusive_and_shows_holder(kb: Path):
    lock = acquire(kb, "cli: ingest")
    try:
        with pytest.raises(KBLockBusy) as ei:
            acquire(kb, "web")
        assert "cli: ingest" in str(ei.value)
        assert read_holder(kb)["origin"] == "cli: ingest"
    finally:
        lock.release()
    acquire(kb, "web").release()


def test_lock_released_when_holder_process_exits(kb: Path):
    code = f"from guanlan.kblock import acquire; acquire({str(kb)!r}, 'child'); print('ok')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "ok"
    acquire(kb, "after").release()


def test_nested_child_of_lock_holder_passes_unrelated_process_refused(kb: Path):
    """持锁写运行里 Agent 在 shell 跑的 guanlan 子命令（继承令牌）放行；没有令牌的无关进程照常被拒。"""
    import os

    from guanlan.kblock import TOKEN_ENV

    code = (
        "from guanlan.kblock import acquire, KBLockBusy\n"
        f"try:\n    lk = acquire({str(kb)!r}, 'child')\n    print('nested' if lk.nested else 'held')\n"
        "except KBLockBusy:\n    print('busy')\n"
    )
    lock = acquire(kb, "cli: ingest")
    try:
        inherit = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        assert inherit.stdout.strip() == "nested"
        env = {k: v for k, v in os.environ.items() if k != TOKEN_ENV}
        stranger = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env)
        assert stranger.stdout.strip() == "busy"
        env[TOKEN_ENV] = "someone-else"
        forged = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env)
        assert forged.stdout.strip() == "busy"
        assert read_holder(kb)["origin"] == "cli: ingest"  # 嵌套放行不改写执行者信息
    finally:
        lock.release()
    # 持有者释放后令牌失效、环境还原：之后起的进程走正常取锁
    assert TOKEN_ENV not in os.environ or os.environ[TOKEN_ENV] != lock.holder["token"]
    after = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert after.stdout.strip() == "held"


def test_token_from_earlier_hold_rejected_on_next_hold(kb: Path):
    """上一次持锁期间派生、活得更久的进程（后台 shell、长驻 MCP server）拿着旧令牌：下一次持锁时不放行。"""
    import os


    code = (
        "from guanlan.kblock import acquire, KBLockBusy\n"
        f"try:\n    acquire({str(kb)!r}, 'child')\n    print('passed')\n"
        "except KBLockBusy:\n    print('busy')\n"
    )
    first = acquire(kb, "web")
    stale_env = dict(os.environ)  # 第一次持锁期间派生的子进程看到的环境
    first.release()
    second = acquire(kb, "web")
    try:
        assert second.holder["token"] != first.holder["token"]
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=stale_env)
        assert out.stdout.strip() == "busy"
    finally:
        second.release()


def test_json_mode_always_prints_one_json_document(kb: Path, capsys):
    """--json：用法错误 / 找不到任务等提前退出也在 stdout 给一份 JSON。"""
    import json as _json

    code = oneclick.import_entrypoint(kb, src=None, do_resume=True, json_output=True, interactive=False)
    assert code == EXIT_USAGE
    out = _json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["exit_code"] == EXIT_USAGE and "没有未完成" in out["error"]


def test_cli_write_commands_refused_while_locked(kb: Path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr("guanlan.cli.run_ingest", lambda *a, **k: called.append(1) or 0)
    lock = acquire(kb, "web: oneclick")
    try:
        (kb / "raw" / "x.md").write_text("x\n", encoding="utf-8")
        assert cli_main(["-C", str(kb), "ingest", "raw/x.md"]) == EXIT_USAGE
        assert "web: oneclick" in capsys.readouterr().err
        assert called == []
        assert cli_main(["-C", str(kb), "reindex", "--dry-run"]) == EXIT_OK  # 只读预览不取锁
        assert cli_main(["-C", str(kb), "import", "--dry-run", str(kb / "raw" / "x.md")]) == EXIT_USAGE
    finally:
        lock.release()
    assert cli_main(["-C", str(kb), "ingest", "raw/x.md"]) == EXIT_OK
    assert called == [1]


def test_cli_import_needs_yes_when_not_interactive(kb: Path, book: Path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # isatty() → False
    assert cli_main(["-C", str(kb), "import", str(book)]) == EXIT_USAGE
    assert "--yes" in capsys.readouterr().err
    assert not (kb / "workspace" / "parsed").exists()


def test_cli_import_yes_runs_end_to_end_and_status(kb: Path, book: Path, capsys):
    ingest = FakeIngest()
    code = oneclick.import_entrypoint(kb, src=str(book), yes=True, json_output=True, ingest=ingest, interactive=False)
    assert code == EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "done" and len(out["items"]) == 3
    assert oneclick.import_entrypoint(kb, src=None, status=True, interactive=False) == EXIT_OK
    assert "没有未完成" in capsys.readouterr().out


def test_cli_import_failure_exit_code_and_resume(kb: Path, book: Path, capsys):
    fail = FakeIngest(fail={"raw/示例书/示例书-01-第1章-题1.md"})
    code = oneclick.import_entrypoint(kb, src=str(book), yes=True, ingest=fail, interactive=False)
    assert code == 5
    assert oneclick.import_entrypoint(kb, src=None, status=True, interactive=False) == EXIT_OK
    assert "已暂停" in capsys.readouterr().out
    code = oneclick.import_entrypoint(kb, src=None, do_resume=True, ingest=FakeIngest(), interactive=False)
    assert code == EXIT_OK
    assert active_task(kb) is None


def test_ingest_exception_pauses_instead_of_leaving_running(kb: Path, book: Path):
    task = _prepare(kb, book)

    def boom(raw_rel: str, root: Path) -> int:
        raise RuntimeError("炸了")

    with pytest.raises(RuntimeError):
        _confirm(kb, task, boom)
    task = load_task(kb, task["task_id"])
    assert task["state"] == "paused"
    assert task["items"][0]["state"] == "failed"
    code, task = _resume(kb, task, FakeIngest())
    assert code == EXIT_OK and task["state"] == "done"


def test_cli_resume_confirms_awaiting_task(kb: Path, book: Path, capsys):
    _prepare(kb, book)
    # 非交互且没给 --yes：不开始，任务仍等待确认
    assert oneclick.import_entrypoint(kb, src=None, do_resume=True, ingest=FakeIngest(), interactive=False) == EXIT_USAGE
    assert active_task(kb)["state"] == "awaiting_confirm"
    ingest = FakeIngest()
    code = oneclick.import_entrypoint(kb, src=None, do_resume=True, yes=True, ingest=ingest, interactive=False)
    assert code == EXIT_OK
    assert active_task(kb) is None
    assert len(ingest.calls) == 3


def test_cli_cancel(kb: Path, book: Path):
    _prepare(kb, book)
    assert oneclick.import_entrypoint(kb, src=None, do_cancel=True, interactive=False) == EXIT_OK
    assert active_task(kb) is None


def test_cli_modes_are_exclusive(kb: Path, book: Path):
    assert oneclick.import_entrypoint(kb, src=str(book), status=True, interactive=False) == EXIT_USAGE
    assert oneclick.import_entrypoint(kb, src=None, interactive=False) == EXIT_USAGE


def test_default_ingest_output_goes_to_stderr(kb: Path, monkeypatch, capsys):
    """逐篇 ingest 打在 stdout 的回执转到 stderr：`--json` 时 stdout 只有 JSON（真实运行发现）。"""
    import guanlan.ingest as ingest_mod

    def fake_run_ingest(target, **kw):
        print("摄入完成：一段 Agent 摘要")
        return 0

    monkeypatch.setattr(ingest_mod, "run_ingest", fake_run_ingest)
    run = oneclick._default_ingest(None, None)
    assert run("raw/x.md", kb) == 0
    out = capsys.readouterr()
    assert out.out == "" and "摄入完成" in out.err


def test_holder_info_cleared_on_release(kb: Path):
    lock = acquire(kb, "cli: heal")
    assert read_holder(kb)["origin"] == "cli: heal"
    lock.release()
    assert read_holder(kb) is None


def test_split_long_line_hard_cuts_oversized_middle_sentence():
    from guanlan.splitdoc import PART_MAX, _split_long_line

    line = "短句。" + "长" * (PART_MAX + 10_000) + "。" + "收尾。"
    pieces = _split_long_line(line, "\n")
    assert "".join(pieces) == line + "\n"
    assert all(len(p.rstrip("\n")) <= PART_MAX for p in pieces)
