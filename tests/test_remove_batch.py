"""P4.35 按原件撤回全部分卷（`guanlan/remove_batch.py`）。零 LLM、纯文件系统。"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest
import yaml

import guanlan.remove_batch as rb
from guanlan.pages import split_frontmatter
from guanlan.provenance import compute_raw_digest, format_digest_value, stamp_raw_digest
from guanlan.remove_batch import (
    BatchConflict,
    BatchError,
    GroupKey,
    ReusedIdentity,
    build_batch_plan,
    count_members,
    execute_batch,
    group_key_for_raw,
)

FM = "---\ntitle: '{title}'\ntype: {type}\ntags: []\nsources: {sources}\nlast_updated: 2026-06-03\n---\n\n{body}\n"
KEY = GroupKey(raw_dir="raw/book", parent="book")


def _kb(tmp_path: Path) -> Path:
    (tmp_path / "AGENTAO.md").write_text("# AGENTAO\n", encoding="utf-8")
    (tmp_path / "SCHEMA.md").write_text("# SCHEMA\n", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    return tmp_path


def _page(root: Path, rel: str, *, type="concept", sources="[]", body="正文内容。") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(title="T", type=type, sources=sources, body=body), encoding="utf-8")
    return p


def _vol(root: Path, stem: str, index: int, *, sample="book", parent="book", images=False) -> Path:
    p = root / "raw" / sample / f"{stem}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = {"title": stem, "split": {"parent": parent, "index": index, "total": 9}}
    p.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n\n正文\n", encoding="utf-8")
    if images:
        img = p.parent / "images" / stem / "1.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"png")
    return p


def _book(root: Path, *, pages=True, images=False) -> None:
    """raw/book/ 下 a、b 两卷（各带摘要页），外加一份批外原文 c 及其摘要页。"""
    _vol(root, "a", 1, images=images)
    _vol(root, "b", 2, images=images)
    (root / "raw" / "c.md").write_text("批外原文", encoding="utf-8")
    if pages:
        _page(root, "wiki/sources/a.md", type="source", body="见 [[b]]。")
        _page(root, "wiki/sources/b.md", type="source", body="见 [[a]]。")
    _page(root, "wiki/sources/c.md", type="source")


def _sources(path: Path) -> list:
    block, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    return yaml.safe_load(block)["sources"]


def _trash(root: Path) -> list[Path]:
    t = root / ".trash"
    return sorted(t.iterdir()) if t.is_dir() else []


# ---------- 原件与成员 ----------


def test_members_by_dir_and_parent_sorted_by_index(tmp_path):
    root = _kb(tmp_path)
    _vol(root, "z-first", 1)
    _vol(root, "a-second", 2)
    _vol(root, "other", 1, parent="另一本")  # 同目录、parent 不同：不进本批
    (root / "raw" / "book" / "notes.md").write_text("无元数据", encoding="utf-8")
    assert group_key_for_raw(root, "raw/book/a-second.md") == KEY
    plan = build_batch_plan(root, KEY)
    assert [m.raw for m in plan.members] == ["raw/book/z-first.md", "raw/book/a-second.md"]
    assert "raw/book/other.md" not in plan.relocate and "raw/book/notes.md" not in plan.relocate


def test_group_key_rejects_top_level_and_missing_meta(tmp_path):
    root = _kb(tmp_path)
    (root / "raw" / "c.md").write_text("x", encoding="utf-8")
    (root / "raw" / "book").mkdir()
    (root / "raw" / "book" / "plain.md").write_text("x", encoding="utf-8")
    with pytest.raises(BatchError):
        group_key_for_raw(root, "raw/c.md")
    with pytest.raises(BatchError):
        group_key_for_raw(root, "raw/book/plain.md")


@pytest.mark.parametrize("bad", [0, "1", True])
def test_invalid_or_duplicate_index_rejected(tmp_path, bad):
    root = _kb(tmp_path)
    _vol(root, "a", 1)
    _vol(root, "b", bad)
    with pytest.raises(BatchError):
        build_batch_plan(root, KEY)


def test_duplicate_index_rejected(tmp_path):
    root = _kb(tmp_path)
    _vol(root, "a", 1)
    _vol(root, "b", 1)
    with pytest.raises(BatchError, match="split.index"):
        build_batch_plan(root, KEY)


def test_relocate_order_images_page_raw_per_volume(tmp_path):
    root = _kb(tmp_path)
    _book(root, images=True)
    plan = build_batch_plan(root, KEY)
    assert plan.relocate == [
        "raw/book/images/a",
        "wiki/sources/a.md",
        "raw/book/a.md",
        "raw/book/images/b",
        "wiki/sources/b.md",
        "raw/book/b.md",
    ]


# ---------- 失源判断 ----------


def test_drops_orphans_and_preexisting_dangling(tmp_path):
    root = _kb(tmp_path)
    _book(root)
    both = _page(root, "wiki/concepts/both.md", sources="[a, b]")
    plus_c = _page(root, "wiki/concepts/plus-c.md", sources="[a, b, c]")
    ghost = _page(root, "wiki/concepts/ghost.md", sources="[a, ghost, c]")  # ghost 原本就悬空：不动
    plan = build_batch_plan(root, KEY)
    assert plan.orphans == ["wiki/concepts/both.md"]
    drops = {d.page: d.dropped for d in plan.drops}
    assert drops == {"wiki/concepts/plus-c.md": ["a", "b"], "wiki/concepts/ghost.md": ["a"]}
    execute_batch(root, plan)
    assert _sources(both) == ["a", "b"]
    assert _sources(plus_c) == ["c"]
    assert _sources(ghost) == ["ghost", "c"]


def test_backlinks_only_from_outside_pages(tmp_path):
    root = _kb(tmp_path)
    _book(root)  # a、b 两张摘要页互链
    _page(root, "wiki/concepts/x.md", body="引用 [[a]]。")
    plan = build_batch_plan(root, KEY)
    assert plan.backlinks == ["wiki/concepts/x.md"]


def test_index_lines_for_all_moving_pages(tmp_path):
    root = _kb(tmp_path)
    _book(root)
    (root / "wiki" / "index.md").write_text(
        "# 索引\n\n- [A](sources/a.md) — x\n- [B](sources/b.md) — y\n- [C](sources/c.md) — z\n", encoding="utf-8"
    )
    plan = build_batch_plan(root, KEY)
    assert len(plan.index_lines) == 2
    execute_batch(root, plan)
    text = (root / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "sources/c.md" in text and "sources/a.md" not in text and "sources/b.md" not in text


_NFD = unicodedata.normalize("NFD", "résumé")


def _legacy_book(root: Path) -> Path:
    """第 1 卷是 NFD 文件名、摘要页是旧名 `re-sume`（raw_digest 确证）；第 2 卷普通。"""
    raw = _vol(root, _NFD, 1)
    _vol(root, "b", 2)
    page = _page(root, "wiki/sources/re-sume.md", type="source")
    assert stamp_raw_digest(page, format_digest_value(raw.relative_to(root).as_posix(), compute_raw_digest(raw)))
    _page(root, "wiki/sources/b.md", type="source")
    (root / "raw" / "c.md").write_text("批外", encoding="utf-8")
    _page(root, "wiki/sources/c.md", type="source")
    return raw


def test_dual_identity_keys_all_count_as_invalid(tmp_path):
    root = _kb(tmp_path)
    _legacy_book(root)
    only_new = _page(root, "wiki/concepts/only-new.md", sources="['résumé']")
    both = _page(root, "wiki/concepts/both.md", sources="['re-sume', 'résumé']")
    plus_c = _page(root, "wiki/concepts/plus-c.md", sources="['re-sume', 'résumé', c]")
    plan = build_batch_plan(root, KEY)
    assert set(plan.orphans) == {"wiki/concepts/only-new.md", "wiki/concepts/both.md"}
    [drop] = plan.drops
    assert drop.page == "wiki/concepts/plus-c.md" and drop.dropped == ["re-sume", "résumé"]
    trash = execute_batch(root, plan)
    assert _sources(plus_c) == ["c"]
    assert _sources(only_new) == ["résumé"] and _sources(both) == ["re-sume", "résumé"]
    manifest = json.loads((trash / "manifest.json").read_text(encoding="utf-8"))
    entries = [e for e in manifest["slug_dropped_from"] if e["page"] == "wiki/concepts/plus-c.md"]
    assert [e["dropped_slug"] for e in entries] == ["re-sume", "résumé"]
    raw_rel = f"raw/book/{_NFD}.md"
    assert entries[0]["resolved_to"] == {"page": "wiki/sources/re-sume.md", "raws": [raw_rel]}
    assert entries[1]["resolved_to"] == {"raws": [raw_rel]}
    member = manifest["group"]["members"][0]
    assert {"re-sume", unicodedata.normalize("NFC", "résumé")} <= set(member["identity_keys"])


# ---------- 归属 ----------


def test_renamed_page_claim_moves_with_volume(tmp_path):
    root = _kb(tmp_path)
    _book(root, pages=False)
    a = root / "raw" / "book" / "a.md"
    page = _page(root, "wiki/sources/renamed.md", type="source")
    assert stamp_raw_digest(page, format_digest_value("raw/book/a.md", compute_raw_digest(a)))
    plan = build_batch_plan(root, KEY)
    assert plan.members[0].page == "wiki/sources/renamed.md"
    assert "wiki/sources/renamed.md" in plan.relocate


def test_filename_and_renamed_claims_conflict(tmp_path):
    root = _kb(tmp_path)
    _book(root)
    a = root / "raw" / "book" / "a.md"
    page = _page(root, "wiki/sources/renamed.md", type="source")
    assert stamp_raw_digest(page, format_digest_value("raw/book/a.md", compute_raw_digest(a)))
    with pytest.raises(BatchConflict) as exc:
        build_batch_plan(root, KEY)
    assert exc.value.reason == "claim_conflict"
    pages = {d["page"] for d in exc.value.details[0]["pages"]}
    assert pages == {"wiki/sources/a.md", "wiki/sources/renamed.md"}


def test_shared_page_inside_batch_moves_once(tmp_path):
    root = _kb(tmp_path)
    _book(root, pages=False)
    _page(root, "wiki/sources/whole.md", type="source", body="> 原始资料：`raw/book/a.md`\n> 原始资料：`raw/book/b.md`")
    keep = _page(root, "wiki/concepts/k.md", sources="[whole, c]")
    plan = build_batch_plan(root, KEY)
    assert plan.relocate.count("wiki/sources/whole.md") == 1
    assert plan.shared_pages == [{"page": "wiki/sources/whole.md", "raws": ["raw/book/a.md", "raw/book/b.md"]}]
    trash = execute_batch(root, plan)
    assert _sources(keep) == ["c"]
    manifest = json.loads((trash / "manifest.json").read_text(encoding="utf-8"))
    [entry] = manifest["slug_dropped_from"]
    assert entry["resolved_to"] == {"page": "wiki/sources/whole.md", "raws": ["raw/book/a.md", "raw/book/b.md"]}
    assert manifest["group"]["shared_pages"][0]["page"] == "wiki/sources/whole.md"


def test_shared_page_with_outside_raw_blocks(tmp_path):
    root = _kb(tmp_path)
    _book(root, pages=False)
    _page(root, "wiki/sources/mixed.md", type="source", body="> 原始资料：`raw/book/a.md`\n> 原始资料：`raw/c.md`")
    with pytest.raises(BatchConflict) as exc:
        build_batch_plan(root, KEY)
    assert exc.value.reason == "shared_page_outside"
    assert exc.value.details[0]["raws"] == ["raw/c.md"]


def test_mention_only_page_is_hint_not_claim(tmp_path):
    root = _kb(tmp_path)
    _book(root)
    _page(root, "wiki/sources/note.md", type="source", body="参见 `raw/book/a.md`。")
    plan = build_batch_plan(root, KEY)  # 不冲突
    assert plan.members[0].page == "wiki/sources/a.md"
    assert plan.hints == [{"raw": "raw/book/a.md", "pages": ["wiki/sources/note.md"]}]
    assert "wiki/sources/note.md" not in plan.relocate


# ---------- 导入冲突 ----------


def _import_task(root: Path, tid: str, **fields) -> None:
    d = root / "workspace" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps({"task_id": tid, **fields}, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(
    "fields, blocked",
    [
        ({"state": "paused", "items": [{"raw": "raw/book/c9.md"}]}, True),
        ({"state": "done", "items": [{"raw": "raw/book/c9.md"}]}, False),
        ({"state": "awaiting_confirm", "sample": "book"}, True),
        ({"state": "preparing", "docs": [{"sample": "other"}, {"sample": "book"}]}, True),
        ({"state": "paused", "sample": "other", "items": [{"raw": "raw/other/x.md"}]}, False),
        # 批量逐份准备：第 0 份已有条目，第 1 份（本原件）还没拆——不能因 items 非空就漏判
        (
            {"state": "preparing", "docs": [{"sample": "other"}, {"sample": "book"}],
             "items": [{"raw": "raw/other/x.md", "doc": 0}]},
            True,
        ),
        # 反例：本原件那一份已有条目且落在别处，样本名不再参与判断
        (
            {"state": "preparing", "docs": [{"sample": "other"}, {"sample": "book"}],
             "items": [{"raw": "raw/other/x.md", "doc": 0}, {"raw": "raw/book2/y.md", "doc": 1}]},
            False,
        ),
    ],
)
def test_import_conflict(tmp_path, fields, blocked):
    root = _kb(tmp_path)
    _book(root)
    _import_task(root, "20261123-000000-book", **fields)
    if blocked:
        with pytest.raises(BatchConflict) as exc:
            build_batch_plan(root, KEY)
        assert exc.value.reason == "import_active"
    else:
        assert build_batch_plan(root, KEY).ok


def test_import_conflict_follows_oneclick_active_states(tmp_path, monkeypatch):
    """状态集取自 `oneclick.ACTIVE_STATES`：往里加一个状态，撤回照样被拦（防两边各写一份而漏判）。"""
    import guanlan.oneclick as oc

    root = _kb(tmp_path)
    _book(root)
    _import_task(root, "20261123-000000-book", state="newstate", items=[{"raw": "raw/book/a.md"}])
    assert build_batch_plan(root, KEY).ok
    monkeypatch.setattr(oc, "ACTIVE_STATES", (*oc.ACTIVE_STATES, "newstate"))
    with pytest.raises(BatchConflict):
        build_batch_plan(root, KEY)


# ---------- 摘要 ----------


def test_digest_tracks_content_and_advisory_changes(tmp_path):
    root = _kb(tmp_path)
    _book(root, images=True)
    d0 = build_batch_plan(root, KEY).digest
    assert build_batch_plan(root, KEY).digest == d0
    (root / "raw" / "book" / "images" / "b" / "1.png").write_bytes(b"changed")
    d1 = build_batch_plan(root, KEY).digest
    assert d1 != d0
    _page(root, "wiki/concepts/new-link.md", body="[[b]]")  # 只多一条入链（advisory）也算变化
    assert build_batch_plan(root, KEY).digest != d1


def test_digest_independent_of_entry_volume(tmp_path):
    root = _kb(tmp_path)
    _book(root)
    ka = group_key_for_raw(root, "raw/book/a.md")
    kb = group_key_for_raw(root, "raw/book/b.md")
    assert build_batch_plan(root, ka).digest == build_batch_plan(root, kb).digest


# ---------- 执行 ----------


def test_execute_one_trash_dir_and_cleanup(tmp_path):
    root = _kb(tmp_path)
    _book(root, images=True)
    plan = build_batch_plan(root, KEY)
    trash = execute_batch(root, plan)
    assert [p.name.split("@")[0] for p in _trash(root)] == ["book"]
    assert not (root / "raw" / "book").exists()
    assert (trash / "raw" / "book" / "a.md").is_file() and (trash / "raw" / "book" / "images" / "b" / "1.png").is_file()
    assert (trash / "wiki" / "sources" / "a.md").is_file()
    assert (root / "raw" / "c.md").is_file() and (root / "wiki" / "sources" / "c.md").is_file()
    manifest = json.loads((trash / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["moved"] == plan.relocate and manifest["group"]["trash_name"] == "book"


def test_before_write_failure_writes_nothing(tmp_path):
    root = _kb(tmp_path)
    _book(root)
    _page(root, "wiki/concepts/k.md", sources="[a, c]")
    plan = build_batch_plan(root, KEY)

    def boom():
        raise OSError("信封写不进")

    with pytest.raises(OSError):
        execute_batch(root, plan, before_write=boom)
    assert _trash(root) == [] and (root / "raw" / "book" / "a.md").is_file()
    assert _sources(root / "wiki" / "concepts" / "k.md") == ["a", "c"]


def test_trash_name_comes_from_raw_dir_not_frontmatter(tmp_path):
    root = _kb(tmp_path)
    _vol(root, "a", 1, parent="../escape")
    _vol(root, "b", 2, parent="../escape")
    key = group_key_for_raw(root, "raw/book/a.md")
    assert key.parent == "../escape"
    trash = execute_batch(root, build_batch_plan(root, key))
    assert trash.parent == root / ".trash" and trash.name.startswith("book@")


@pytest.mark.parametrize("name", ["..", ".hidden", "a/b", ""])
def test_unsafe_trash_name_rejected(tmp_path, name):
    with pytest.raises(BatchError):
        rb._safe_trash_name(_kb(tmp_path), name)


# ---------- 中断后重开 ----------


def test_interrupt_after_edits_leaves_only_moves(tmp_path, monkeypatch):
    root = _kb(tmp_path)
    _book(root)
    _page(root, "wiki/concepts/k.md", sources="[a, c]")
    (root / "wiki" / "index.md").write_text("# 索引\n\n- [A](sources/a.md) — x\n", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("模拟中断")

    monkeypatch.setattr(rb, "_move_into_trash", boom)
    with pytest.raises(OSError):
        execute_batch(root, build_batch_plan(root, KEY))
    monkeypatch.undo()
    again = build_batch_plan(root, KEY)
    assert again.drops == [] and again.index_lines == []
    assert len(again.relocate) == 4


def test_interrupt_midway_edits_still_listed(tmp_path, monkeypatch):
    root = _kb(tmp_path)
    _book(root)
    _page(root, "wiki/concepts/k1.md", sources="[a, c]")
    _page(root, "wiki/concepts/k2.md", sources="[b, c]")
    calls = []
    orig = rb._drop_slugs_from_page

    def once(path, slugs):
        if calls:
            raise OSError("模拟中断")
        calls.append(path)
        return orig(path, slugs)

    monkeypatch.setattr(rb, "_drop_slugs_from_page", once)
    with pytest.raises(OSError):
        execute_batch(root, build_batch_plan(root, KEY))
    monkeypatch.undo()
    again = build_batch_plan(root, KEY)
    assert [d.page for d in again.drops] == ["wiki/concepts/k2.md"]


def test_after_first_volume_moved_remaining_ref_is_orphan(tmp_path, monkeypatch):
    """A 已移走、B 待撤：`[A, B]` 的 A 不算剩余有效来源，列为失源、原样保留（不摘成只剩 `[A]`）。"""
    root = _kb(tmp_path)
    _book(root)
    plan = build_batch_plan(root, KEY)
    orig = rb._move_into_trash

    def stop_at_b(src, root_, trash_dir):
        if src.name == "b.md" and "sources" in src.parts:
            raise OSError("模拟中断")
        return orig(src, root_, trash_dir)

    monkeypatch.setattr(rb, "_move_into_trash", stop_at_b)
    with pytest.raises(OSError):
        execute_batch(root, plan)
    monkeypatch.undo()
    page = _page(root, "wiki/concepts/later.md", sources="[a, b]")  # 中断之后才出现的引用
    again = build_batch_plan(root, KEY)
    assert [m.raw for m in again.members] == ["raw/book/b.md"]
    assert again.orphans == ["wiki/concepts/later.md"] and again.drops == []
    execute_batch(root, again)
    assert _sources(page) == ["a", "b"]
    assert count_members(root, KEY) == 0
    assert not build_batch_plan(root, KEY).ok


def test_reused_identity_merges_only_when_unclaimed_and_unchanged(tmp_path):
    root = _kb(tmp_path)
    _book(root, pages=False)
    a = root / "raw" / "book" / "a.md"
    from guanlan.remove import path_digest

    sha = path_digest(root, "raw/book/a.md")
    original = a.read_text(encoding="utf-8")
    page = _page(root, "wiki/concepts/k.md", sources="[old-a, c]")
    reuse = [ReusedIdentity(raw="raw/book/a.md", identity_keys=("old-a",), sha256=sha)]
    plan = build_batch_plan(root, KEY, reuse=reuse)
    assert plan.members[0].reused and [d.dropped for d in plan.drops] == [["old-a"]]
    execute_batch(root, plan)
    assert _sources(page) == ["c"]

    (tmp_path / "second").mkdir()
    root2 = _kb(tmp_path / "second")
    _book(root2, pages=False)
    (root2 / "raw" / "book" / "a.md").write_text(original + "改过\n", encoding="utf-8")
    _page(root2, "wiki/concepts/k.md", sources="[old-a, c]")
    plan2 = build_batch_plan(root2, KEY, reuse=reuse)
    assert not plan2.members[0].reused and plan2.drops == []


def test_symlinked_sample_dir_is_not_enumerated(tmp_path: Path) -> None:
    """`raw/<sample>` 是指向库外的符号链接：不枚举成员（否则整批会把库外文件移进回收区）；同一份分卷放在真目录里照常枚举。"""
    (tmp_path / "kb").mkdir()
    root = _kb(tmp_path / "kb")
    outside = tmp_path / "outside"
    _vol(outside, "a", 1)  # outside/raw/book/a.md
    (root / "raw" / "book").symlink_to(outside / "raw" / "book", target_is_directory=True)
    assert count_members(root, KEY) == 0
    assert not build_batch_plan(root, KEY).ok
    # 反例：真目录下的同一份分卷必须照常认出（过滤规则不能把正常情况一并吞掉）
    (root / "raw" / "book").unlink()
    _vol(root, "a", 1)
    assert count_members(root, KEY) == 1
