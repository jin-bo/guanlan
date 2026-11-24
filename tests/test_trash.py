"""P4.36 T1 回收区：扫描、识别、状态、记录校验、删除保护、彻底删除（`guanlan/trash.py`）。零 LLM。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import guanlan.remove as rmmod
import guanlan.trash as trash
from conftest import write_page
from guanlan import kblock
from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.remove import remove_entrypoint
from guanlan.trash import TrashError, TrashLockError, TrashNotFound, TrashProtected

TASK_ID = "20261124-100000-heal-abcd"
JOB = f"job-{TASK_ID}@20261124T100100"


def _single(root: Path) -> None:
    (root / "raw" / "foo.md").write_text("原文", encoding="utf-8")
    write_page(root, "wiki/sources/foo.md", type="source")
    (root / "raw" / "bar.md").write_text("原文", encoding="utf-8")
    write_page(root, "wiki/sources/bar.md", type="source")
    write_page(root, "wiki/concepts/multi.md", sources="[foo, bar]")


def _removed(root: Path) -> trash.TrashEntry:
    _single(root)
    assert remove_entrypoint(root, src="foo", yes=True, json_output=False) == EXIT_OK
    [e] = trash.scan(root)
    return e


def _rollback(root: Path, actions: list[dict], name: str = JOB) -> Path:
    d = root / ".trash" / name
    d.mkdir(parents=True)
    lines = [json.dumps({"meta": {"task_id": TASK_ID, "reason": "cancel"}})]
    for a in actions:
        lines.append(json.dumps(a))
        saved = d / a["saved_as"]
        if a.get("_make", True):
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text("x", encoding="utf-8")
    (d / "manifest.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def _envelope(root: Path, **fields) -> None:
    d = root / "workspace" / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    env = {"id": TASK_ID, "kind": "heal", "state": "interrupted", "params": {}, **fields}
    (d / f"{TASK_ID}.json").write_text(json.dumps(env), encoding="utf-8")


# ---------- 扫描与识别 ----------


def test_empty_trash(kb) -> None:
    assert trash.scan(kb) == []
    assert trash.summary(kb) == {"count": 0, "size": 0}


def test_remove_entry_is_recognised_and_complete(kb) -> None:
    e = _removed(kb)
    assert e.kind == "remove" and e.state == "complete" and e.subject == "foo"
    assert e.files >= 3 and e.size > 0
    d = trash.get_entry(kb, e.name).detail()
    assert {it["path"] for it in d["items"]} == {"raw/foo.md", "wiki/sources/foo.md"}
    assert d["slug_dropped_from"][0]["page"] == "wiki/concepts/multi.md"
    assert d["protected"] == []


def test_reingest_after_complete_removal_keeps_entry_complete(kb) -> None:
    """完整撤回后同一路径重新入库：只看归档侧，旧条目仍完整、可删除（§3.2）。"""
    e = _removed(kb)
    (kb / "raw" / "foo.md").write_text("重新入库", encoding="utf-8")
    write_page(kb, "wiki/sources/foo.md", type="source")
    again = trash.get_entry(kb, e.name)
    assert again.state == "complete" and again.protected == []
    trash.purge(kb, e.name)
    assert not (kb / ".trash" / e.name).exists()
    assert (kb / "raw" / "foo.md").read_text(encoding="utf-8") == "重新入库"


def test_interrupted_removal_is_incomplete_and_protected(kb, monkeypatch) -> None:
    _single(kb)
    raw = kb / "raw" / "foo.md"
    orig = rmmod._move_into_trash

    def fail_on_raw(src, root_, trash_dir):
        if src == raw:
            raise OSError("模拟中断")
        return orig(src, root_, trash_dir)

    monkeypatch.setattr(rmmod, "_move_into_trash", fail_on_raw)
    assert remove_entrypoint(kb, src="foo", yes=True, json_output=False) == EXIT_USAGE
    [e] = trash.annotate(kb, trash.scan(kb))
    assert e.state == "incomplete" and e.protected
    with pytest.raises(TrashProtected):
        trash.purge(kb, e.name)
    assert trash.trash_entrypoint(kb, action="purge", name=e.name, yes=True, json_output=False) == EXIT_USAGE
    assert (kb / ".trash" / e.name).is_dir()

    # 续跑完成后：第一个目录缺 raw、原位也没了 → partial，解除保护
    monkeypatch.setattr(rmmod, "_move_into_trash", orig)
    assert remove_entrypoint(kb, src="foo", yes=True, json_output=False) == EXIT_OK
    first = trash.get_entry(kb, e.name)
    assert first.state == "partial" and first.protected == []


def test_unknown_entries_listed_and_purgeable(kb, tmp_path_factory) -> None:
    t = kb / ".trash"
    (t / "manual").mkdir(parents=True)
    (t / "manual" / "a.md").write_text("a", encoding="utf-8")
    (t / "loose.txt").write_text("hi", encoding="utf-8")
    (t / "x@20261124T100000").mkdir()  # 名字合形态、但没有记录文件
    (t / "y@20261124T100000").mkdir()
    (t / "y@20261124T100000" / "manifest.json").write_text("{坏", encoding="utf-8")
    outside = tmp_path_factory.mktemp("outside")
    (outside / "keep.md").write_text("库外", encoding="utf-8")
    os.symlink(outside, t / "link")
    entries = {e.name: e for e in trash.scan(kb)}
    assert {e.kind for e in entries.values()} == {"unknown"}
    assert entries["manual"].files == 1 and entries["loose.txt"].files == 1
    assert entries["link"].files == 1  # 链接按自身计，不跟进去
    for name in list(entries):
        trash.purge(kb, name)
    assert not any(t.iterdir())
    assert (outside / "keep.md").read_text(encoding="utf-8") == "库外"  # 链接目标完好


def test_internal_links_not_followed(kb, tmp_path_factory) -> None:
    e = _removed(kb)
    outside = tmp_path_factory.mktemp("outside")
    (outside / "big.bin").write_bytes(b"0" * 100_000)
    os.symlink(outside, kb / ".trash" / e.name / "evil")
    got = trash.get_entry(kb, e.name)
    assert got.size < 100_000
    trash.purge(kb, e.name)
    assert (outside / "big.bin").stat().st_size == 100_000


def test_trash_symlink_is_refused(kb, tmp_path_factory) -> None:
    outside = tmp_path_factory.mktemp("outside")
    os.symlink(outside, kb / ".trash")
    with pytest.raises(TrashError):
        trash.scan(kb)
    assert trash.summary(kb)["error"]
    assert trash.trash_entrypoint(kb, action="list", name=None, yes=False, json_output=False) != EXIT_OK


def test_manifest_paths_validated(kb, tmp_path_factory) -> None:
    d = kb / ".trash" / "foo@20261124T100000_000000"
    d.mkdir(parents=True)
    outside = tmp_path_factory.mktemp("outside")
    os.symlink(outside, d / "raw")  # 归档侧途经链接
    manifest = {
        "slug": "foo",
        "moved": ["../x.md", "/abs.md", "wiki/concepts/a.md", "raw/foo.md"],
        "slug_dropped_from": [{"page": "wiki/index.md", "dropped_slug": "foo", "before_sources": []}],
    }
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    e = trash.get_entry(kb, d.name)
    bad = {it.path: it.invalid for it in e.items}
    assert bad["../x.md"] and bad["/abs.md"] and bad["wiki/concepts/a.md"] and bad["raw/foo.md"]
    assert bad["wiki/index.md"]  # 来源编辑记录指向配置页
    assert e.detail()["invalid"] is True


def test_batch_entry_kind(kb) -> None:
    d = kb / ".trash" / "book@20261124T100000_000000"
    (d / "raw" / "book").mkdir(parents=True)
    (d / "raw" / "book" / "a.md").write_text("a", encoding="utf-8")
    manifest = {"slug": None, "moved": ["raw/book/a.md"], "group": {"raw_dir": "raw/book", "members": [{"raw": "raw/book/a.md"}]}}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    e = trash.get_entry(kb, d.name)
    assert e.kind == "remove_batch" and e.state == "complete" and e.subject.startswith("raw/book")


# ---------- 回滚条目 ----------


def test_rollback_entry_state_by_archive_side(kb) -> None:
    _rollback(kb, [{"path": "wiki/concepts/new.md", "action": "remove", "saved_as": "wiki/concepts/new.md"}])
    write_page(kb, "wiki/concepts/new.md")  # 原位置被写回：不影响
    e = trash.get_entry(kb, JOB)
    assert e.kind == "rollback" and e.state == "complete" and e.subject == TASK_ID


def test_rollback_entry_missing_and_invalid(kb) -> None:
    _rollback(
        kb,
        [
            {"path": "wiki/a.md", "action": "remove", "saved_as": "wiki/a.md", "_make": False},
            {"path": "raw/x.md", "action": "remove", "saved_as": "../out.md", "_make": False},
        ],
    )
    e = trash.get_entry(kb, JOB)
    assert e.state == "partial"
    assert [it.path for it in e.items if it.invalid] == ["raw/x.md"]


# ---------- 删除保护 ----------


def test_resumable_rollback_is_protected(kb, monkeypatch, tmp_path_factory) -> None:
    home = tmp_path_factory.mktemp("jobs-home")
    monkeypatch.setenv("GUANLAN_JOBS_HOME", str(home))
    from guanlan.web.tasks import preimage_base

    _rollback(kb, [{"path": "wiki/a.md", "action": "remove", "saved_as": "wiki/a.md"}])
    pre = preimage_base(kb) / TASK_ID / "before"
    pre.mkdir(parents=True)
    _envelope(kb, state="interrupted", preimage=str(pre), rollback_trash=f".trash/{JOB}")
    assert trash.get_entry(kb, JOB).protected
    with pytest.raises(TrashProtected):
        trash.purge(kb, JOB)

    _envelope(kb, state="rolling_back", rollback_trash=f".trash/{JOB}")  # 回滚在跑或排队
    assert trash.get_entry(kb, JOB).protected

    _envelope(kb, state="interrupted", preimage=str(pre), rollback_trash=".trash/job-other@20261124T100100")
    assert trash.get_entry(kb, JOB).protected == []  # 指向别的目录：不误伤

    _envelope(kb, state="interrupted", preimage=str(pre), rollback_trash=f".trash/{JOB}")
    pre.rmdir()  # 选了「保留现状」：前像删掉
    assert trash.get_entry(kb, JOB).protected == []
    trash.purge(kb, JOB)


def test_protection_uses_actual_name(kb, monkeypatch, tmp_path_factory) -> None:
    """另一种大小写拼写：大小写不敏感的卷上仍按实际目录名命中保护；敏感的卷上找不到。"""
    home = tmp_path_factory.mktemp("jobs-home")
    monkeypatch.setenv("GUANLAN_JOBS_HOME", str(home))
    _rollback(kb, [{"path": "wiki/a.md", "action": "remove", "saved_as": "wiki/a.md"}])
    _envelope(kb, state="rolling_back", rollback_trash=f".trash/{JOB}")
    other = JOB.upper()
    insensitive = os.path.exists(kb / ".trash" / other)
    if insensitive:
        assert trash.resolve_name(kb, other) == JOB
        with pytest.raises(TrashProtected):
            trash.purge(kb, other)
        assert (kb / ".trash" / JOB).is_dir()
    else:
        with pytest.raises(TrashNotFound):
            trash.purge(kb, other)


def test_restore_record_states(kb) -> None:
    e = _removed(kb)
    rec = kb / ".trash" / e.name / "restore.json"
    rec.write_text(json.dumps({"started_at": "2026-11-24T10:00:00"}), encoding="utf-8")
    got = trash.get_entry(kb, e.name)
    assert got.state == "restoring" and got.protected
    rec.write_text("{坏", encoding="utf-8")  # 读不了：保守按恢复中
    assert trash.get_entry(kb, e.name).state == "restoring"
    rec.write_text(json.dumps({"started_at": "x", "finished_at": "y"}), encoding="utf-8")
    got = trash.get_entry(kb, e.name)
    assert got.state == "restored" and got.protected == []


def test_bad_names(kb) -> None:
    (kb / ".trash").mkdir()
    for bad in ("", ".", "..", "a/b", "a\\b"):
        with pytest.raises(TrashError):
            trash.resolve_name(kb, bad)
    with pytest.raises(TrashNotFound):
        trash.resolve_name(kb, "nope")


# ---------- 锁 ----------


def test_purge_requires_real_kb_lock(kb, monkeypatch) -> None:
    e = _removed(kb)

    def no_lockfile(root, origin):
        raise OSError("只读介质")

    monkeypatch.setattr(kblock, "acquire", no_lockfile)
    with pytest.raises(TrashLockError):
        trash.purge(kb, e.name)
    assert (kb / ".trash" / e.name).is_dir()

    monkeypatch.setattr(kblock, "acquire", lambda root, origin: kblock.KBLock(Path(root), None, {}, nested=True))
    with pytest.raises(TrashLockError):
        trash.purge(kb, e.name)
    assert trash.trash_entrypoint(kb, action="purge", name=e.name, yes=True, json_output=False) == EXIT_USAGE
    assert (kb / ".trash" / e.name).is_dir()


def test_purge_busy(kb, monkeypatch) -> None:
    e = _removed(kb)

    def busy(root, origin):
        raise kblock.KBLockBusy({"pid": 1, "origin": "cli"})

    monkeypatch.setattr(kblock, "acquire", busy)
    assert trash.trash_entrypoint(kb, action="purge", name=e.name, yes=True, json_output=False) == EXIT_USAGE
    assert (kb / ".trash" / e.name).is_dir()


# ---------- CLI ----------


def test_cli_list_show_purge(kb, capsys) -> None:
    e = _removed(kb)
    capsys.readouterr()
    assert trash.trash_entrypoint(kb, action="list", name=None, yes=False, json_output=True) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert [x["name"] for x in data["entries"]] == [e.name]
    assert trash.trash_entrypoint(kb, action="show", name=e.name, yes=False, json_output=False) == EXIT_OK
    assert "wiki/concepts/multi.md" in capsys.readouterr().out
    assert trash.trash_entrypoint(kb, action="purge", name=e.name, yes=False, json_output=False) == EXIT_OK
    assert "--yes" in capsys.readouterr().out and (kb / ".trash" / e.name).is_dir()  # 预览不删
    assert trash.trash_entrypoint(kb, action="purge", name=e.name, yes=True, json_output=False) == EXIT_OK
    assert not (kb / ".trash" / e.name).exists()


def test_cli_subcommand_wired(kb, capsys) -> None:
    from guanlan.cli import main

    _removed(kb)
    capsys.readouterr()
    assert main(["-C", str(kb), "trash", "list"]) == EXIT_OK
    assert "回收区：1 项" in capsys.readouterr().out


def test_invalid_item_with_origin_present_stays_protected(kb, tmp_path_factory) -> None:
    """记录里有不合规项（途经链接）时不能当作完整：原位置还在 → 撤回未完成、受保护。"""
    d = kb / ".trash" / "foo@20261124T100000_000000"
    (d / "wiki" / "sources").mkdir(parents=True)
    (d / "wiki" / "sources" / "foo.md").write_text("摘要", encoding="utf-8")
    outside = tmp_path_factory.mktemp("outside")
    os.symlink(outside, d / "raw")  # 归档侧 raw/ 是链接：raw 项看不清
    (kb / "raw" / "foo.md").write_text("原文", encoding="utf-8")  # raw 还在原位：撤回没做完
    manifest = {"slug": "foo", "moved": ["raw/foo.md", "wiki/sources/foo.md"]}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    e = trash.get_entry(kb, d.name)
    assert e.invalid and e.state == "incomplete" and e.protected


def test_rollback_rerun_duplicate_record_is_complete(kb) -> None:
    """回滚崩在「记录」与「移动」之间、重跑以新序号再移一次：同一路径有一行已归档就算完整。"""
    _rollback(
        kb,
        [
            {"path": "wiki/a.md", "action": "remove", "saved_as": "wiki/a.md", "_make": False},
            {"path": "wiki/a.md", "action": "remove", "saved_as": "wiki/a.md.1"},
        ],
    )
    assert trash.get_entry(kb, JOB).state == "complete"


def test_non_list_manifest_fields_do_not_crash_scan(kb) -> None:
    # 记录文件不可信：应为列表的字段被改成数字 / 对象时，扫描、详情、体检那一行都不能抛（否则 /api/doctor 500）。
    d = kb / ".trash" / "foo@20261124T100000"
    d.mkdir(parents=True)
    manifest = {"moved": [], "slug_dropped_from": 5, "index_lines_removed": 7, "group": {"raw_dir": "raw/x", "members": 3}}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    [e] = trash.scan(kb)
    assert e.kind == "remove_batch"
    detail = trash.get_entry(kb, e.name).detail()
    assert detail["slug_dropped_from"] == [] and detail["index_lines_removed"] == []
    assert detail["group"]["members"] == []
    assert trash.summary(kb)["count"] == 1


def test_rollback_subject_has_task_title(kb) -> None:
    _rollback(kb, [{"path": "wiki/a.md", "action": "remove", "saved_as": "wiki/a.md"}])
    assert trash.get_entry(kb, JOB).subject == TASK_ID  # 任务记录不在：只有 id
    _envelope(kb, state="succeeded", title="补全：3 个缺页")
    assert trash.get_entry(kb, JOB).subject == f"{TASK_ID} · 补全：3 个缺页"
