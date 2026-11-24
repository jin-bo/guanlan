"""P4.36 T2 恢复撤回条目（`guanlan/restore.py`）与撤回 manifest 的前置改动。零 LLM。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

import guanlan.remove as rmmod
import guanlan.restore as rsmod
import guanlan.trash as trash
from conftest import write_page
from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.pages import split_frontmatter
from guanlan.remove import remove_entrypoint
from guanlan.remove_batch import build_batch_plan, execute_batch, group_key_for_raw
from guanlan.restore import execute_restore, plan_restore
from guanlan.trash import TrashProtected


def _sources(root: Path, rel: str) -> list:
    block, _ = split_frontmatter((root / rel).read_text(encoding="utf-8"))
    return yaml.safe_load(block)["sources"]


def _single(root: Path) -> None:
    (root / "raw" / "foo.md").write_text("原文甲", encoding="utf-8")
    img = root / "raw" / "images" / "foo"
    img.mkdir(parents=True)
    (img / "1.png").write_bytes(b"png")
    write_page(root, "wiki/sources/foo.md", type="source")
    (root / "raw" / "bar.md").write_text("原文乙", encoding="utf-8")
    write_page(root, "wiki/sources/bar.md", type="source")
    write_page(root, "wiki/concepts/multi.md", sources="[foo, bar]")
    write_page(root, "wiki/concepts/only.md", sources="[foo]")
    (root / "wiki" / "index.md").write_text(
        "# 索引\n\n## Sources\n- [foo](sources/foo.md)\n- [bar](sources/bar.md)\n", encoding="utf-8"
    )


def _remove(root: Path, slug: str = "foo") -> str:
    before = set(os.listdir(root / ".trash")) if (root / ".trash").is_dir() else set()
    assert remove_entrypoint(root, src=slug, yes=True, json_output=False) == EXIT_OK
    [name] = set(os.listdir(root / ".trash")) - before
    return name


def _manifest(root: Path, name: str) -> dict:
    return json.loads((root / ".trash" / name / "manifest.json").read_text(encoding="utf-8"))


def _restore(root: Path, name: str):
    plan = plan_restore(root, name)
    assert plan.ok, (plan.blocked, plan.conflicts)
    return execute_restore(root, plan)


# ---------- 前置：manifest 格式与续撤判定 ----------


def test_manifest_records_format_origin_and_page_order(kb) -> None:
    _single(kb)
    name = _remove(kb)
    m = _manifest(kb, name)
    assert m["format"] == trash.MANIFEST_FORMAT and m["origin"] == {"kind": "first"}
    [drop] = m["slug_dropped_from"]
    assert drop["before_sources"] == ["foo", "bar"]  # 页面原顺序，不再排序


def test_interrupted_single_then_resume_marks_resume(kb, monkeypatch) -> None:
    _single(kb)
    raw = kb / "raw" / "foo.md"
    orig = rmmod._move_into_trash

    def fail_on_raw(src, root_, trash_dir):
        if src == raw:
            raise OSError("模拟中断")
        return orig(src, root_, trash_dir)

    monkeypatch.setattr(rmmod, "_move_into_trash", fail_on_raw)
    assert remove_entrypoint(kb, src="foo", yes=True, json_output=False) == EXIT_USAGE
    [first] = os.listdir(kb / ".trash")
    monkeypatch.setattr(rmmod, "_move_into_trash", orig)
    second = _remove(kb)
    assert _manifest(kb, second)["origin"] == {"kind": "resume", "from": [first]}
    for n in (first, second):
        assert plan_restore(kb, n).blocked  # 首次目录 partial、续撤目录 resume：都不给恢复


def test_cross_entry_resume_single_then_batch(kb, monkeypatch) -> None:
    """单源撤回 A 卷做了一半，再从 B 卷整批撤回：整批记为续撤，不给恢复（§5.1）。"""
    for stem, idx in (("a", 1), ("b", 2)):
        p = kb / "raw" / "book" / f"{stem}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = {"title": stem, "split": {"parent": "book", "index": idx, "total": 2}}
        p.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n\n正文\n", encoding="utf-8")
        write_page(kb, f"wiki/sources/{stem}.md", type="source")
    raw_a = kb / "raw" / "book" / "a.md"
    orig = rmmod._move_into_trash

    def fail_on_raw(src, root_, trash_dir):
        if src == raw_a:
            raise OSError("模拟中断")
        return orig(src, root_, trash_dir)

    monkeypatch.setattr(rmmod, "_move_into_trash", fail_on_raw)
    assert remove_entrypoint(kb, src="a", yes=True, json_output=False) == EXIT_USAGE
    [single] = os.listdir(kb / ".trash")
    monkeypatch.setattr(rmmod, "_move_into_trash", orig)
    plan = build_batch_plan(kb, group_key_for_raw(kb, "raw/book/b.md"))
    batch = execute_batch(kb, plan).name
    origin = _manifest(kb, batch)["origin"]
    assert origin["kind"] == "resume" and single in origin["from"]
    assert plan_restore(kb, batch).blocked


def test_restored_source_removed_again_is_first(kb) -> None:
    _single(kb)
    _restore(kb, _remove(kb))
    again = _remove(kb)
    assert _manifest(kb, again)["origin"] == {"kind": "first"}


# ---------- 资格 ----------


def test_old_format_and_rollback_blocked(kb) -> None:
    _single(kb)
    name = _remove(kb)
    path = kb / ".trash" / name / "manifest.json"
    m = _manifest(kb, name)
    del m["format"]
    path.write_text(json.dumps(m), encoding="utf-8")
    assert "旧格式" in plan_restore(kb, name).blocked
    d = kb / ".trash" / "job-20261124-100000-heal-abcd@20261124T100100"
    d.mkdir()
    (d / "manifest.jsonl").write_text(json.dumps({"meta": {}}) + "\n", encoding="utf-8")
    assert "回滚" in plan_restore(kb, d.name).blocked


def test_wiki_symlink_kb_is_manual_only(tmp_path) -> None:
    """`wiki/` 是软链接的库：撤回记录标不合规项，T2 不给恢复，原因写明途经链接（§7）。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "AGENTAO.md").write_text("# A\n", encoding="utf-8")
    (kb / "SCHEMA.md").write_text("# S\n", encoding="utf-8")
    (kb / "raw").mkdir()
    real = tmp_path / "wiki-real"
    real.mkdir()
    os.symlink(real, kb / "wiki")
    for f, t in (("index.md", "# 索引\n"), ("log.md", "# 时间线\n"), ("overview.md", "综述\n")):
        (real / f).write_text(t, encoding="utf-8")
    _single(kb)
    name = _remove(kb)
    plan = plan_restore(kb, name)
    assert plan.blocked and "wiki/sources/foo.md：路径途经软链接（wiki/）" in plan.blocked


# ---------- 往返 ----------


def test_single_round_trip(kb) -> None:
    _single(kb)
    name = _remove(kb)
    assert _sources(kb, "wiki/concepts/multi.md") == ["bar"]
    plan = plan_restore(kb, name)
    assert [it.path for it in plan.items] == ["raw/foo.md", "wiki/sources/foo.md", "raw/images/foo"]
    assert [(e.page, e.mode) for e in plan.edits] == [("wiki/concepts/multi.md", "clean")]
    assert plan.index_add == ["wiki/sources/foo.md"]
    assert "wiki/concepts/only.md" in plan.orphaned
    result = execute_restore(kb, plan)
    assert result.clean == ["wiki/concepts/multi.md"] and result.indexed == ["wiki/sources/foo.md"]
    assert (kb / "raw" / "foo.md").read_text(encoding="utf-8") == "原文甲"
    assert (kb / "raw" / "images" / "foo" / "1.png").read_bytes() == b"png"
    assert (kb / "wiki" / "sources" / "foo.md").is_file()
    assert _sources(kb, "wiki/concepts/multi.md") == ["foo", "bar"]  # 原位补回
    assert "(sources/foo.md)" in (kb / "wiki" / "index.md").read_text(encoding="utf-8")
    e = trash.get_entry(kb, name)
    assert e.state == "restored" and e.protected == [] and e.restore_blocked
    assert sorted(os.listdir(kb / ".trash" / name)) == ["manifest.json", "restore.json"]


def test_batch_round_trip_with_shared_page(kb) -> None:
    for stem, idx in (("a", 1), ("b", 2)):
        p = kb / "raw" / "book" / f"{stem}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = {"title": stem, "split": {"parent": "book", "index": idx, "total": 2}}
        p.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n\n正文\n", encoding="utf-8")
        write_page(kb, f"wiki/sources/{stem}.md", type="source")
    (kb / "raw" / "c.md").write_text("批外", encoding="utf-8")
    write_page(kb, "wiki/sources/c.md", type="source")
    write_page(kb, "wiki/concepts/k.md", sources="[a, c, b]")  # 同一页摘了两个 slug
    plan = build_batch_plan(kb, group_key_for_raw(kb, "raw/book/a.md"))
    name = execute_batch(kb, plan).name
    assert _sources(kb, "wiki/concepts/k.md") == ["c"]
    rp = plan_restore(kb, name)
    assert rp.ok and [(e.page, e.add, e.mode) for e in rp.edits] == [("wiki/concepts/k.md", ["a", "b"], "clean")]
    execute_restore(kb, rp)
    assert _sources(kb, "wiki/concepts/k.md") == ["a", "c", "b"]
    assert (kb / "raw" / "book" / "a.md").is_file() and (kb / "raw" / "book" / "b.md").is_file()


# ---------- 冲突与跳过 ----------


def _tree(root: Path) -> dict[str, bytes]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".trash" not in p.parts:
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def test_occupied_target_is_conflict_and_zero_write(kb) -> None:
    _single(kb)
    name = _remove(kb)
    (kb / "raw" / "foo.md").write_text("重新入库", encoding="utf-8")
    os.symlink(kb / "nowhere", kb / "wiki" / "sources" / "foo.md")  # 悬空链接也算占用
    snap = _tree(kb)
    plan = plan_restore(kb, name)
    assert not plan.ok and len(plan.conflicts) == 2
    with pytest.raises(rsmod.RestoreError):
        execute_restore(kb, plan)
    assert _tree(kb) == snap


def test_page_changed_appends_and_bad_page_skipped(kb) -> None:
    _single(kb)
    write_page(kb, "wiki/concepts/other.md", sources="[foo, bar]")
    name = _remove(kb)
    write_page(kb, "wiki/concepts/multi.md", sources="[bar, baz]")  # 撤回后被改过
    (kb / "wiki" / "concepts" / "other.md").write_bytes("---\nsources: [bar]\n---\n\xff".encode("latin-1"))
    plan = plan_restore(kb, name)
    modes = {e.page: e.mode for e in plan.edits}
    assert modes == {"wiki/concepts/multi.md": "append", "wiki/concepts/other.md": "skip"}
    r = execute_restore(kb, plan)
    assert r.appended == ["wiki/concepts/multi.md"] and r.skipped == ["wiki/concepts/other.md"]
    assert _sources(kb, "wiki/concepts/multi.md") == ["bar", "baz", "foo"]


def test_index_is_directory_conflicts(kb) -> None:
    _single(kb)
    name = _remove(kb)
    (kb / "wiki" / "index.md").unlink()
    (kb / "wiki" / "index.md").mkdir()
    plan = plan_restore(kb, name)
    assert not plan.ok and any("index.md" in c for c in plan.conflicts)
    assert not (kb / "raw" / "foo.md").exists()


def test_index_only_registers_restored_page(kb) -> None:
    _single(kb)
    write_page(kb, "wiki/concepts/unlisted.md")
    _restore(kb, _remove(kb))
    text = (kb / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "(sources/foo.md)" in text and "unlisted" not in text


def test_symlink_in_archived_images_is_conflict(kb, tmp_path_factory) -> None:
    _single(kb)
    name = _remove(kb)
    outside = tmp_path_factory.mktemp("out")
    os.symlink(outside, kb / ".trash" / name / "raw" / "images" / "foo" / "evil")
    plan = plan_restore(kb, name)
    assert any("软链接" in c for c in plan.conflicts)


def test_remove_task_in_flight_conflicts(kb) -> None:
    _single(kb)
    name = _remove(kb)
    d = kb / "workspace" / "jobs"
    d.mkdir(parents=True)
    tid = "20261124-100000-remove-abcd"
    env = {"id": tid, "kind": "remove", "state": "queued", "params": {"scope": "one", "source": "foo"}}
    (d / f"{tid}.json").write_text(json.dumps(env), encoding="utf-8")
    assert any(tid in c for c in plan_restore(kb, name).conflicts)


# ---------- 中断与续跑 ----------


def _interrupt_second_move(monkeypatch):
    """raw 已放回、放摘要页时中断。"""
    real = os.replace

    def flaky(src, dst):
        if str(dst).endswith("wiki/sources/foo.md") and ".trash" in str(src):
            raise OSError("模拟中断")
        return real(src, dst)

    monkeypatch.setattr(rsmod.os, "replace", flaky)
    return real


def test_interrupted_restore_resumes(kb, monkeypatch) -> None:
    _single(kb)
    name = _remove(kb)
    real = _interrupt_second_move(monkeypatch)
    with pytest.raises(OSError):
        execute_restore(kb, plan_restore(kb, name))
    monkeypatch.setattr(rsmod.os, "replace", real)
    e = trash.get_entry(kb, name)
    assert e.state == "restoring" and e.protected
    with pytest.raises(TrashProtected):
        trash.purge(kb, name)
    plan = plan_restore(kb, name)
    assert plan.resume and plan.ok
    assert [(it.path, it.status) for it in plan.items][0] == ("raw/foo.md", "done")
    execute_restore(kb, plan)
    assert trash.get_entry(kb, name).state == "restored"
    assert _sources(kb, "wiki/concepts/multi.md") == ["foo", "bar"]


def test_remove_during_half_done_restore_is_resume(kb, monkeypatch) -> None:
    """恢复做了一半（raw 已放回、其余仍在旧条目里）时再撤回同一个源：新条目只含一部分，记续撤、不给恢复。"""
    _single(kb)
    name = _remove(kb)
    real = _interrupt_second_move(monkeypatch)
    with pytest.raises(OSError):
        execute_restore(kb, plan_restore(kb, name))
    monkeypatch.setattr(rsmod.os, "replace", real)
    assert trash.get_entry(kb, name).state == "restoring"
    again = _remove(kb)
    assert _manifest(kb, again)["origin"] == {"kind": "resume", "from": [name]}
    assert plan_restore(kb, again).blocked


def test_resume_refuses_tampered_archive(kb, monkeypatch) -> None:
    _single(kb)
    name = _remove(kb)
    real = _interrupt_second_move(monkeypatch)
    with pytest.raises(OSError):
        execute_restore(kb, plan_restore(kb, name))
    monkeypatch.setattr(rsmod.os, "replace", real)
    (kb / ".trash" / name / "wiki" / "sources" / "foo.md").write_text("中断后被改", encoding="utf-8")
    plan = plan_restore(kb, name)
    assert not plan.ok and any("被改过" in c for c in plan.conflicts)


def test_resume_refuses_mismatched_progress_record(kb, monkeypatch) -> None:
    _single(kb)
    name = _remove(kb)
    real = _interrupt_second_move(monkeypatch)
    with pytest.raises(OSError):
        execute_restore(kb, plan_restore(kb, name))
    monkeypatch.setattr(rsmod.os, "replace", real)
    rec_path = kb / ".trash" / name / "restore.json"
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    rec["items"] = rec["items"][:1]
    rec_path.write_text(json.dumps(rec), encoding="utf-8")
    assert not plan_restore(kb, name).ok


# ---------- CLI ----------


def test_cli_restore(kb, capsys) -> None:
    _single(kb)
    name = _remove(kb)
    capsys.readouterr()
    assert trash.trash_entrypoint(kb, action="restore", name=name, yes=False, json_output=False) == EXIT_OK
    assert "加 --yes" in capsys.readouterr().out and not (kb / "raw" / "foo.md").exists()
    assert trash.trash_entrypoint(kb, action="restore", name=name, yes=True, json_output=False) == EXIT_OK
    assert (kb / "raw" / "foo.md").is_file()
    assert trash.trash_entrypoint(kb, action="restore", name=name, yes=True, json_output=False) == EXIT_USAGE
