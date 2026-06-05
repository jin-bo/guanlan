"""P2 门禁测试：raw/ 快照 + diff + enforce 优先级（零 LLM，见 docs/P2-最小闭环.md §12）。"""

from pathlib import Path

from guanlan.errors import EXIT_AGENT_ERROR, EXIT_CHECK_FAILED, EXIT_RAW_MUTATED
from guanlan.gate import (
    GateResult,
    check_baseline,
    diff_raw,
    enforce,
    enforce_write_result,
    snapshot_raw,
)
from guanlan.runtime import AgentRunResult

FM = '---\ntitle: "T"\ntype: {type}\ntags: []\nsources: {sources}\nlast_updated: 2026-06-03\n---\n\n{body}\n'


def _kb(tmp_path: Path) -> Path:
    (tmp_path / "raw").mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    return tmp_path


def _good_page(tmp_path: Path) -> None:
    p = tmp_path / "wiki" / "concepts" / "Foo.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(type="concept", sources="[]", body="正文"), encoding="utf-8")


def _broken_page(tmp_path: Path) -> None:
    """断链页（wikilink.broken）—— 在新语义下属**警告**，不阻断写入。"""
    p = tmp_path / "wiki" / "concepts" / "Bad.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(type="concept", sources="[]", body="[[Ghost]]"), encoding="utf-8")


def _bad_fm_page(tmp_path: Path, name: str = "BadFm") -> str:
    """frontmatter 阻断性违规页（bad_type）—— 仍阻断写入。返回相对路径。"""
    rel = f"wiki/concepts/{name}.md"
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(type="bogus", sources="[]", body="正文"), encoding="utf-8")
    return rel


# --- snapshot_raw / diff_raw ---


def test_snapshot_empty_raw_is_stable(tmp_path: Path):
    _kb(tmp_path)
    # 空 raw/ 含根标记，稳定无 diff。
    assert snapshot_raw(tmp_path) == {".": "<raw-dir>"}
    assert diff_raw(snapshot_raw(tmp_path), snapshot_raw(tmp_path)) == []


def test_snapshot_catches_empty_raw_deletion(tmp_path: Path):
    """空 raw/ 被整个删除（或换成文件）也算违规——根标记把'存在'和'已删'区分开。"""
    _kb(tmp_path)
    before = snapshot_raw(tmp_path)
    (tmp_path / "raw").rmdir()
    changes = diff_raw(before, snapshot_raw(tmp_path))
    assert [(c.kind, c.path) for c in changes] == [("removed", ".")]

    # 换成普通文件同样违规。
    (tmp_path / "raw").write_text("x", encoding="utf-8")
    changes = diff_raw(before, snapshot_raw(tmp_path))
    assert ("modified", ".") in {(c.kind, c.path) for c in changes}


def test_snapshot_catches_empty_dir_mutation(tmp_path: Path):
    """新建/删除 raw/ 下的空目录也算违规（只记文件会漏掉标记目录）。"""
    _kb(tmp_path)
    before = snapshot_raw(tmp_path)
    (tmp_path / "raw" / "processed").mkdir()
    changes = diff_raw(before, snapshot_raw(tmp_path))
    assert [(c.kind, c.path) for c in changes] == [("added", "processed/")]


def test_snapshot_catches_broken_symlink(tmp_path: Path):
    """raw/ 下新增坏符号链接（既非 file 也非 dir）也算违规，不被快照漏过。"""
    _kb(tmp_path)
    before = snapshot_raw(tmp_path)
    (tmp_path / "raw" / "link").symlink_to(tmp_path / "raw" / "nonexistent-target")
    changes = diff_raw(before, snapshot_raw(tmp_path))
    assert [(c.kind, c.path) for c in changes] == [("added", "link")]


def test_snapshot_detects_symlink_retarget(tmp_path: Path):
    """symlink 指向真文件后被改指/换成同字节真文件，都应判 modified（不跟随到目标内容）。"""
    _kb(tmp_path)
    (tmp_path / "raw" / "a.md").write_text("AAA", encoding="utf-8")
    (tmp_path / "raw" / "b.md").write_text("BBB", encoding="utf-8")
    link = tmp_path / "raw" / "ref"
    link.symlink_to(tmp_path / "raw" / "a.md")
    before = snapshot_raw(tmp_path)

    # 改指向 b.md（内容不同但 link 条目本身变了）
    link.unlink()
    link.symlink_to(tmp_path / "raw" / "b.md")
    changes = diff_raw(before, snapshot_raw(tmp_path))
    assert ("modified", "ref") in {(c.kind, c.path) for c in changes}

    # 换成与原目标同字节的真文件 —— 跟随会漏判，按 symlink 指纹则能判出。
    link.unlink()
    link.write_text("AAA", encoding="utf-8")
    changes = diff_raw(before, snapshot_raw(tmp_path))
    assert ("modified", "ref") in {(c.kind, c.path) for c in changes}


def test_snapshot_recurses_into_symlinked_raw_dir(tmp_path: Path):
    """raw/ 本身是指向目录的符号链接（受支持配置）时，内容增改仍要被捕获。"""
    store = tmp_path / "store"
    store.mkdir()
    (store / "a.md").write_text("AAA", encoding="utf-8")
    # 用 raw/ -> store 符号链接替代真目录。
    (tmp_path / "wiki").mkdir()
    (tmp_path / "raw").symlink_to(store)
    before = snapshot_raw(tmp_path)
    assert "a.md" in before  # 递归到了内容

    # 经 raw/ 改内容 → 必须判 modified。
    (store / "a.md").write_text("AAA-changed", encoding="utf-8")
    changes = diff_raw(before, snapshot_raw(tmp_path))
    assert ("modified", "a.md") in {(c.kind, c.path) for c in changes}

    # 新增文件 → added。
    (store / "b.md").write_text("BBB", encoding="utf-8")
    assert ("added", "b.md") in {(c.kind, c.path) for c in diff_raw(before, snapshot_raw(tmp_path))}


def test_snapshot_descends_into_inner_symlinked_dir(tmp_path: Path):
    """raw/ 下的目录符号链接：经它的写入（raw/link/file.md）也要被捕获。"""
    _kb(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / "x.md").write_text("X", encoding="utf-8")
    (tmp_path / "raw" / "link").symlink_to(external)
    before = snapshot_raw(tmp_path)
    assert "link/x.md" in before  # 跟随进了符号链接目录

    # 经符号链接改内容 → modified。
    (external / "x.md").write_text("X-changed", encoding="utf-8")
    changes = diff_raw(before, snapshot_raw(tmp_path))
    assert ("modified", "link/x.md") in {(c.kind, c.path) for c in changes}

    # 经符号链接新增文件 → added。
    (external / "y.md").write_text("Y", encoding="utf-8")
    assert ("added", "link/y.md") in {(c.kind, c.path) for c in diff_raw(before, snapshot_raw(tmp_path))}


def test_snapshot_symlink_cycle_terminates(tmp_path: Path):
    """raw/ 下的目录符号链接成环不应让快照死循环。"""
    _kb(tmp_path)
    d = tmp_path / "raw" / "d"
    d.mkdir()
    (d / "self").symlink_to(d)  # 自指环
    snap = snapshot_raw(tmp_path)  # 不挂死
    assert "d/self" in snap


def test_diff_detects_add_remove_modify_rename(tmp_path: Path):
    _kb(tmp_path)
    (tmp_path / "raw" / "a.md").write_text("AAA", encoding="utf-8")
    before = snapshot_raw(tmp_path)

    # 增
    (tmp_path / "raw" / "b.md").write_text("BBB", encoding="utf-8")
    assert [c.kind for c in diff_raw(before, snapshot_raw(tmp_path))] == ["added"]

    # 改
    (tmp_path / "raw" / "a.md").write_text("AAA-changed", encoding="utf-8")
    kinds = {c.kind for c in diff_raw(before, snapshot_raw(tmp_path))}
    assert "modified" in kinds and "added" in kinds

    # 重命名 a.md -> c.md = 一删一增
    (tmp_path / "raw" / "a.md").rename(tmp_path / "raw" / "c.md")
    (tmp_path / "raw" / "b.md").unlink()
    changes = {(c.kind, c.path) for c in diff_raw(before, snapshot_raw(tmp_path))}
    assert ("removed", "a.md") in changes
    assert ("added", "c.md") in changes


# --- enforce 优先级 ---


def test_enforce_passes_clean(tmp_path: Path):
    _kb(tmp_path)
    _good_page(tmp_path)
    before = snapshot_raw(tmp_path)
    result = enforce(tmp_path, before)
    assert result.ok and result.kind is None


def test_enforce_raw_before_check(tmp_path: Path):
    """raw/ 改动 + 断链页同时存在时，raw_mutated 优先于 check_failed。"""
    _kb(tmp_path)
    _broken_page(tmp_path)
    before = snapshot_raw(tmp_path)
    (tmp_path / "raw" / "sneaky.md").write_text("x", encoding="utf-8")

    result = enforce(tmp_path, before)
    assert result.kind == "raw_mutated"
    assert result.exit_code == EXIT_RAW_MUTATED


def test_enforce_check_failed(tmp_path: Path):
    """阻断性违规（frontmatter bad_type）→ check_failed。"""
    _kb(tmp_path)
    _bad_fm_page(tmp_path)
    before = snapshot_raw(tmp_path)
    result = enforce(tmp_path, before)
    assert result.kind == "check_failed"
    assert result.exit_code == EXIT_CHECK_FAILED


def test_enforce_broken_link_is_warning_not_blocking(tmp_path: Path):
    """断链只作警告：enforce 通过、ok，但 warnings 非空（决策8）。"""
    _kb(tmp_path)
    _broken_page(tmp_path)
    before = snapshot_raw(tmp_path)
    result = enforce(tmp_path, before)
    assert result.ok and result.kind is None
    assert any(w.kind == "wikilink.broken" for w in result.warnings)


def test_enforce_incremental_baseline_excuses_preexisting(tmp_path: Path):
    """增量门禁：已在基线里的阻断性违规不连累本次；新引入的才判 check_failed（决策7）。"""
    _kb(tmp_path)
    _bad_fm_page(tmp_path, "Old")  # 写操作"前"就坏了
    before = snapshot_raw(tmp_path)
    baseline = check_baseline(tmp_path)

    # 仅有历史欠债 → 增量门禁放行。
    assert enforce(tmp_path, before, baseline=baseline).ok

    # 再引入一个新的阻断页 → 只追究新的那条。
    _bad_fm_page(tmp_path, "New")
    result = enforce(tmp_path, before, baseline=baseline)
    assert result.kind == "check_failed"
    assert {v.page for v in result.violations} == {"wiki/concepts/New.md"}


# --- enforce_write_result（写入口收尾裁决）---


def test_write_result_ok_clean(tmp_path: Path):
    _kb(tmp_path)
    _good_page(tmp_path)
    before = snapshot_raw(tmp_path)
    r = AgentRunResult(ok=True, final_text="done")
    assert enforce_write_result(tmp_path, before, r).ok


def test_write_result_agent_failed_clean_raw(tmp_path: Path):
    _kb(tmp_path)
    before = snapshot_raw(tmp_path)
    r = AgentRunResult(ok=False, final_text="boom", error_type="max_iterations")
    gate = enforce_write_result(tmp_path, before, r)
    assert gate.kind == "agent_error"
    assert gate.exit_code == EXIT_AGENT_ERROR


def test_write_result_agent_failed_but_raw_mutated(tmp_path: Path):
    """agent 失败但先改了 raw/ → 仍兜底完整性，判 raw_mutated（不退化为 agent_error）。"""
    _kb(tmp_path)
    before = snapshot_raw(tmp_path)
    (tmp_path / "raw" / "evil.md").write_text("x", encoding="utf-8")
    r = AgentRunResult(ok=False, final_text="boom", error_type="runtime_error")
    gate = enforce_write_result(tmp_path, before, r)
    assert gate.kind == "raw_mutated"
    assert gate.exit_code == EXIT_RAW_MUTATED


def test_gateresult_exit_code_mapping():
    assert GateResult.passed().exit_code == 0
    assert GateResult.raw_mutated([]).exit_code == EXIT_RAW_MUTATED
    assert GateResult.check_failed([]).exit_code == EXIT_CHECK_FAILED
    assert GateResult.agent_error(None).exit_code == EXIT_AGENT_ERROR
