"""P2 门禁测试：raw/ 快照 + diff + enforce 优先级（零 LLM，见 docs/P2-最小闭环.md §12）。

P2.1 源不回退 + 正文骤缩信号测试见文件末尾「P2.1」段（docs/P2.1-摄入写入纪律.md §8）。
"""

import inspect
from pathlib import Path

from conftest import make_runner

from guanlan.errors import (
    EXIT_AGENT_ERROR,
    EXIT_CHECK_FAILED,
    EXIT_OK,
    EXIT_RAW_MUTATED,
)
from guanlan.gate import (
    SHRINK_FLOOR,
    SHRINK_RATIO,
    GateResult,
    PageMetaFingerprint,
    _check_source_regression,
    check_baseline,
    diff_raw,
    enforce,
    enforce_write_result,
    report_outcome,
    run_guarded_write,
    run_guarded_write_result,
    snapshot_page_meta,
    snapshot_raw,
)
from guanlan.pages import Violation
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


# --- run_guarded_write_result（决策P3.2-13：结果版不向 stdout 打印门禁报告）---


def _kb_writable(tmp_path: Path) -> Path:
    """satisfy check_baseline / snapshot：含 raw/ 与 wiki/ 三 config 页。"""
    _kb(tmp_path)
    return tmp_path


def test_guarded_write_result_returns_structured_no_stdout(tmp_path, capsys):
    """结果版回传 exit_code/final_text/gate，且 stdout 无门禁报告（report_outcome 不触发）。"""
    from guanlan.gate import run_guarded_write_result

    _kb_writable(tmp_path)
    runner = make_runner(lambda root: _good_page(root), final_text="建好了")
    result = run_guarded_write_result(tmp_path, "PROMPT", runner=runner)

    assert result.exit_code == 0
    assert result.final_text == "建好了"
    assert result.gate.ok
    out = capsys.readouterr().out
    assert out == ""  # 结果版绝不向 stdout 打印（heal 自渲染 / 出 --json 靠这点）


def test_guarded_write_thin_shell_still_prints(tmp_path, capsys):
    """薄壳 run_guarded_write 仍打印门禁报告（ingest 路径不回归）。"""
    from guanlan.gate import run_guarded_write

    _kb_writable(tmp_path)
    rc = run_guarded_write(tmp_path, "PROMPT", runner=make_runner(_good_page))
    assert rc == 0
    assert "门禁通过" in capsys.readouterr().out


# ====================================================================
# P2.1 摄入写入纪律：源不回退（阻断+自愈）+ 正文骤缩（警告非阻断）
# docs/P2.1-摄入写入纪律.md §8。零 LLM，全程 mock runner 制造写后末态。
# ====================================================================


def _src(tmp_path: Path, slug: str) -> None:
    """建一张 wiki/sources/<slug>.md（供 entity 页的 sources 解析通过 check）。"""
    p = tmp_path / "wiki" / "sources" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(type="source", sources="[]", body="源"), encoding="utf-8")


def _entity(tmp_path: Path, name: str, sources: str, body: str = "正文") -> None:
    """建/改一张 wiki/entities/<name>.md（sources 为 YAML flow 串，如 '[a, b]'）。"""
    p = tmp_path / "wiki" / "entities" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(type="entity", sources=sources, body=body), encoding="utf-8")


def _PMF(sources, body_len: int) -> PageMetaFingerprint:
    return PageMetaFingerprint(sources=sources, body_len=body_len)


# --- snapshot_page_meta：可信度判定 + 容错 + config 排除 ---


def test_snapshot_page_meta_trusted_sources_and_config_excluded(tmp_path: Path):
    """content 页记可信 frozenset；config 页（index/log/overview）不进快照。"""
    _kb(tmp_path)
    _src(tmp_path, "a")
    _entity(tmp_path, "X", "[a]", body="hello")
    snap = snapshot_page_meta(tmp_path)
    assert snap["wiki/entities/X.md"].sources == frozenset({"a"})
    assert snap["wiki/entities/X.md"].body_len > 0
    # config 页与 SCHEMA/AGENTAO 不在内容快照里
    assert all(not k.endswith(("index.md", "log.md", "overview.md")) for k in snap)


def test_snapshot_page_meta_empty_sources_is_trusted_frozenset(tmp_path: Path):
    """合法空 `[]` → 可信的 `frozenset()`（非 None），用于抓「洗光来源」。"""
    _kb(tmp_path)
    _entity(tmp_path, "X", "[]")
    assert snapshot_page_meta(tmp_path)["wiki/entities/X.md"].sources == frozenset()


def test_snapshot_page_meta_untrusted_sources_is_none(tmp_path: Path):
    """坏 frontmatter / sources 类型非法 → sources=None（不可信），但 body_len 仍可算。"""
    _kb(tmp_path)
    _entity(tmp_path, "X", "justastring")  # YAML 解析为字符串、非列表 → 不可信
    fp = snapshot_page_meta(tmp_path)["wiki/entities/X.md"]
    assert fp.sources is None
    assert fp.body_len > 0


def test_snapshot_page_meta_missing_wiki_is_empty_not_raise(tmp_path: Path):
    """wiki/ 缺失 → 空 dict、绝不抛（决策P3-8 口径）。"""
    assert snapshot_page_meta(tmp_path) == {}


# --- _check_source_regression：源回退（阻断）---


def test_regression_dropped_multi_slug_sorted():
    """[a,b]→[] → 每丢一 slug 一条、按 slug 稳定排序。"""
    blocking, warnings = _check_source_regression(
        {"p": _PMF(frozenset({"b", "a"}), 10)}, {"p": _PMF(frozenset(), 10)}
    )
    assert [v.kind for v in blocking] == ["sources.dropped", "sources.dropped"]
    assert "'a'" in blocking[0].detail and "'b'" in blocking[1].detail  # sorted a 先 b 后
    assert warnings == []


def test_regression_source_only_grows_passes():
    """源只增（[a]→[a,b,c]）→ 无 dropped。"""
    blocking, _ = _check_source_regression(
        {"p": _PMF(frozenset({"a"}), 10)},
        {"p": _PMF(frozenset({"a", "b", "c"}), 10)},
    )
    assert blocking == []


def test_regression_empty_list_is_real_regression():
    """[a,b]→[]（可信空集）是真回退，非 None-跳过 → 两条 dropped。"""
    blocking, _ = _check_source_regression(
        {"p": _PMF(frozenset({"a", "b"}), 10)}, {"p": _PMF(frozenset(), 10)}
    )
    assert len(blocking) == 2 and all(v.kind == "sources.dropped" for v in blocking)


def test_regression_none_side_skips_dropped():
    """before 或 after 任一不可信（None）→ 跳过 dropped（去重 check，决策P2.1-11）。"""
    # after 坏 → 跳过（after frontmatter 错误已由 check 阻断）
    assert _check_source_regression(
        {"p": _PMF(frozenset({"a", "b"}), 10)}, {"p": _PMF(None, 10)}
    )[0] == []
    # before 坏 → 无可信基线、同样跳过
    assert _check_source_regression(
        {"p": _PMF(None, 10)}, {"p": _PMF(frozenset(), 10)}
    )[0] == []


def test_regression_only_intersection_new_and_deleted_pages_ignored():
    """新建页（仅 after）/ 删页（仅 before）都不在 before∩after，不判（决策P2.1-8）。"""
    blocking, warnings = _check_source_regression(
        {"old": _PMF(frozenset({"a"}), 1000)},  # 删页
        {"new": _PMF(frozenset(), 10)},  # 新建页
    )
    assert blocking == [] and warnings == []


# --- _check_source_regression：正文骤缩（警告非阻断）+ 阈值边界 ---


def test_shrink_warns_below_ratio():
    """before≥SHRINK_FLOOR 且 after<0.5×before → 一条 body.shrank 警告（非阻断）。"""
    blocking, warnings = _check_source_regression(
        {"p": _PMF(frozenset(), 1000)}, {"p": _PMF(frozenset(), 300)}
    )
    assert blocking == []
    assert [v.kind for v in warnings] == ["body.shrank"]


def test_shrink_boundary_above_ratio_no_warning():
    """after 600 > 0.5×1000 → 不报（未达腰斩级）。"""
    _, warnings = _check_source_regression(
        {"p": _PMF(frozenset(), 1000)}, {"p": _PMF(frozenset(), 600)}
    )
    assert warnings == []


def test_shrink_boundary_below_floor_no_warning():
    """before 150 < SHRINK_FLOOR(200) 骤缩到 10 → 桩页豁免、不报。"""
    assert SHRINK_FLOOR == 200 and SHRINK_RATIO == 0.5  # 阈值钉死
    _, warnings = _check_source_regression(
        {"p": _PMF(frozenset(), 150)}, {"p": _PMF(frozenset(), 10)}
    )
    assert warnings == []


def test_shrink_and_dropped_orthogonal():
    """同页同时源回退 + 骤缩 → 阻断 sources.dropped 与警告 body.shrank 互不吞没。"""
    blocking, warnings = _check_source_regression(
        {"p": _PMF(frozenset({"a", "b"}), 1000)}, {"p": _PMF(frozenset({"a"}), 200)}
    )
    assert [v.kind for v in blocking] == ["sources.dropped"]  # 丢 b
    assert [v.kind for v in warnings] == ["body.shrank"]


# --- enforce 接入：page_before 缺省向后兼容 ---


def test_enforce_no_page_before_ignores_regression(tmp_path: Path):
    """page_before=None → enforce 逐字节走原路径，看不见源回退（硬回归门）。"""
    _kb(tmp_path)
    _src(tmp_path, "a")
    _src(tmp_path, "b")
    _entity(tmp_path, "X", "[a, b]")
    before_raw = snapshot_raw(tmp_path)
    _entity(tmp_path, "X", "[a]")  # 丢 b，但 check 看 [a] 仍干净
    assert enforce(tmp_path, before_raw).ok  # 不传 page_before → 放行


def test_enforce_with_page_before_blocks_source_regression(tmp_path: Path):
    """传 page_before → 既有页丢源 → check_failed 含 sources.dropped。"""
    _kb(tmp_path)
    _src(tmp_path, "a")
    _src(tmp_path, "b")
    _entity(tmp_path, "X", "[a, b]")
    before_raw = snapshot_raw(tmp_path)
    page_before = snapshot_page_meta(tmp_path)
    _entity(tmp_path, "X", "[a]")  # 丢 b
    gate = enforce(tmp_path, before_raw, page_before=page_before)
    assert gate.kind == "check_failed" and gate.exit_code == EXIT_CHECK_FAILED
    dropped = [v for v in gate.violations if v.kind == "sources.dropped"]
    assert len(dropped) == 1 and dropped[0].page == "wiki/entities/X.md" and "'b'" in dropped[0].detail


def test_enforce_shrink_is_warning_not_blocking(tmp_path: Path):
    """正文骤缩 → 进 warnings、gate.ok、不阻断（决策P2.1-3）。"""
    _kb(tmp_path)
    _entity(tmp_path, "X", "[]", body="字" * 1000)
    before_raw = snapshot_raw(tmp_path)
    page_before = snapshot_page_meta(tmp_path)
    _entity(tmp_path, "X", "[]", body="字" * 100)  # 腰斩级骤缩
    gate = enforce(tmp_path, before_raw, page_before=page_before)
    assert gate.ok and gate.kind is None
    assert any(w.kind == "body.shrank" for w in gate.warnings)


def test_enforce_dedup_broken_after_frontmatter_no_double_count(tmp_path: Path):
    """after frontmatter 坏（sources 类型非法）→ 只由 check 记一条、无 sources.dropped（决策P2.1-11）。"""
    _kb(tmp_path)
    _src(tmp_path, "a")
    _src(tmp_path, "b")
    _entity(tmp_path, "X", "[a, b]")
    before_raw = snapshot_raw(tmp_path)
    page_before = snapshot_page_meta(tmp_path)
    _entity(tmp_path, "X", "justastring")  # sources 变非列表 → check bad_type、快照记 None
    gate = enforce(tmp_path, before_raw, page_before=page_before)
    assert gate.kind == "check_failed"
    assert any(v.kind == "frontmatter.bad_type" for v in gate.violations)
    assert not any(v.kind == "sources.dropped" for v in gate.violations)  # 不重复记账


# --- run_guarded_write_result / run_guarded_write：page_guard 范围 + 自愈 ---


def _runner_drop_b(heal: bool = False):
    """mock runner：首轮把 X 的 sources 从 [a,b] 改成 [a]（丢 b）；heal=True 时自愈轮并回 b。"""
    state = {"n": 0, "prompts": []}

    def runner(prompt, **kwargs):
        state["n"] += 1
        state["prompts"].append(prompt)
        root = kwargs["working_directory"]
        if heal and state["n"] >= 2:
            _entity(root, "X", "[a, b]")  # 并回 b
        else:
            _entity(root, "X", "[a]")  # 丢 b
        return AgentRunResult(ok=True, final_text="done")

    runner.state = state
    return runner


def _kb_with_x(tmp_path: Path) -> None:
    _kb(tmp_path)
    _src(tmp_path, "a")
    _src(tmp_path, "b")
    _entity(tmp_path, "X", "[a, b]")


def test_source_regression_self_heals(tmp_path: Path):
    """源回退 → 首轮 check_failed → REPAIR_PROMPT 含「并回」→ 自愈并回 → EXIT_OK（决策P2.1-2）。"""
    _kb_with_x(tmp_path)
    runner = _runner_drop_b(heal=True)
    result = run_guarded_write_result(tmp_path, "PROMPT", runner=runner, page_guard=True)
    assert result.exit_code == EXIT_OK
    assert runner.state["n"] == 2  # 首轮 + 1 自愈
    repair_prompt = runner.state["prompts"][1]
    assert "并回" in repair_prompt and "sources.dropped" in repair_prompt


def test_source_regression_unhealed_fails_check(tmp_path: Path):
    """持续丢源、2 轮自愈未并回 → EXIT_CHECK_FAILED，gate 含 sources.dropped。"""
    _kb_with_x(tmp_path)
    result = run_guarded_write_result(
        tmp_path, "PROMPT", runner=_runner_drop_b(), page_guard=True
    )
    assert result.exit_code == EXIT_CHECK_FAILED
    assert any(v.kind == "sources.dropped" for v in result.gate.violations)


def test_source_regression_not_suppressed_by_baseline(tmp_path: Path):
    """历史阻断欠债被基线豁免，但 sources.dropped 必判为本次新引入、永不被压制（§2.1）。"""
    _kb_with_x(tmp_path)
    _bad_fm_page(tmp_path, "Old")  # 写前已存在的历史阻断违规
    result = run_guarded_write_result(
        tmp_path, "PROMPT", runner=_runner_drop_b(), page_guard=True
    )
    assert result.exit_code == EXIT_CHECK_FAILED
    assert any(v.kind == "sources.dropped" for v in result.gate.violations)
    assert not any(v.page == "wiki/concepts/Old.md" for v in result.gate.violations)


def test_page_guard_defaults_signature():
    """签名层硬门（决策P2.1-10）：薄壳默认 True、核心默认 False，防有人翻默认。"""
    assert inspect.signature(run_guarded_write).parameters["page_guard"].default is True
    assert (
        inspect.signature(run_guarded_write_result).parameters["page_guard"].default
        is False
    )


def test_page_guard_thin_shell_defaults_on(tmp_path: Path):
    """行为层（belt）：run_guarded_write 不显式传 → 守护默认开 → 丢源 → EXIT_CHECK_FAILED。

    丢 b 后 wiki 对 check 而言干净（[a] 可解析），若守护没开则会 EXIT_OK；故 CHECK_FAILED 证明默认开。
    """
    _kb_with_x(tmp_path)
    rc = run_guarded_write(tmp_path, "PROMPT", runner=_runner_drop_b())
    assert rc == EXIT_CHECK_FAILED


def test_page_guard_core_defaults_off(tmp_path: Path):
    """行为层（suspenders）：run_guarded_write_result 不显式传 → 守护默认关 → 丢源也放行（heal 路径不变）。"""
    _kb_with_x(tmp_path)
    result = run_guarded_write_result(tmp_path, "PROMPT", runner=_runner_drop_b())
    assert result.exit_code == EXIT_OK
    assert not any(
        v.kind in ("sources.dropped", "body.shrank") for v in result.gate.violations
    )
    assert not any(w.kind == "body.shrank" for w in result.gate.warnings)


def test_page_guard_toggle_reverse(tmp_path: Path):
    """反向各一：薄壳可显式关、核心可显式开（heal 将来若要守护有路可走，决策P2.1-10）。"""
    _kb_with_x(tmp_path)
    assert run_guarded_write(tmp_path, "P", runner=_runner_drop_b(), page_guard=False) == EXIT_OK

    _entity(tmp_path, "X", "[a, b]")  # 重置 X 的源回 [a,b]
    result = run_guarded_write_result(
        tmp_path, "P", runner=_runner_drop_b(), page_guard=True
    )
    assert result.exit_code == EXIT_CHECK_FAILED


def test_guarded_write_failed_path_skips_regression(tmp_path: Path):
    """agent 失败 → 仍只判 raw/、不跑 check、不算 sources.dropped（失败路径语义不动）。"""
    _kb_with_x(tmp_path)

    def runner(prompt, **kwargs):
        _entity(kwargs["working_directory"], "X", "[a]")  # 丢 b 但随即报失败
        return AgentRunResult(ok=False, final_text="boom", error_type="runtime_error")

    result = run_guarded_write_result(tmp_path, "P", runner=runner, page_guard=True)
    assert result.gate.kind == "agent_error"
    assert not any(v.kind == "sources.dropped" for v in result.gate.violations)


def test_guarded_write_raw_priority_over_regression(tmp_path: Path):
    """既改 raw/ 又源回退 → 仍判 raw_mutated（更严重者先，顺序不动）。"""
    _kb_with_x(tmp_path)

    def runner(prompt, **kwargs):
        root = kwargs["working_directory"]
        _entity(root, "X", "[a]")  # 丢 b
        (root / "raw" / "sneaky.md").write_text("x", encoding="utf-8")
        return AgentRunResult(ok=True, final_text="done")

    result = run_guarded_write_result(tmp_path, "P", runner=runner, page_guard=True)
    assert result.gate.kind == "raw_mutated"
    assert result.exit_code == EXIT_RAW_MUTATED


# ====================================================================
# 确定性 frontmatter 引号修复：消除最常见的自愈轮（见 guanlan/fmrepair.py）。
# 坏引号页本可解析失败 → 触发一整轮 LLM 自愈；宿主确定性修引号后零自愈直接过。
# ====================================================================


def _write_quote_broken(root: Path, name: str = "Q") -> None:
    """写一页 frontmatter 引号写坏（title 双引号套双引号）的实体页——除此外全合法。"""
    p = root / "wiki" / "entities" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '---\ntitle: "他说"你好""\ntype: entity\ntags: []\nsources: []\n'
        "last_updated: 2026-06-03\n---\n\n正文\n",
        encoding="utf-8",
    )


def _runner_quote_broken():
    """每次被调都写同一页坏引号 frontmatter（若有自愈轮会再写一次→可观测）。"""
    state = {"n": 0}

    def runner(prompt, **kwargs):
        state["n"] += 1
        _write_quote_broken(kwargs["working_directory"])
        return AgentRunResult(ok=True, final_text="done")

    runner.state = state
    return runner


def test_frontmatter_quote_repair_skips_selfheal(tmp_path: Path, capsys):
    """坏引号 frontmatter → 宿主确定性修 → 门禁通过，**自愈轮数为 0**（runner 只被调一次）。"""
    _kb_writable(tmp_path)
    runner = _runner_quote_broken()
    result = run_guarded_write_result(tmp_path, "PROMPT", runner=runner, page_guard=True)
    assert result.exit_code == EXIT_OK
    assert result.gate.ok
    assert runner.state["n"] == 1  # 关键：无 REPAIR_PROMPT 自愈轮
    err = capsys.readouterr().err
    assert "确定性修正" in err and "门禁未过" not in err  # 修了、且未进自愈


def test_frontmatter_quote_repair_actually_fixes_disk(tmp_path: Path):
    """修复是真写盘：通过后该页 frontmatter 严格档可解析、title 还原。"""
    _kb_writable(tmp_path)
    run_guarded_write_result(
        tmp_path, "PROMPT", runner=_runner_quote_broken(), page_guard=True
    )
    from guanlan.pages import parse_frontmatter, split_frontmatter

    text = (tmp_path / "wiki" / "entities" / "Q.md").read_text(encoding="utf-8")
    meta, fatal = parse_frontmatter(split_frontmatter(text)[0])
    assert fatal is None and meta["title"] == '他说"你好"'


def test_frontmatter_repair_leaves_non_quote_violations_to_selfheal(tmp_path: Path, capsys):
    """非引号类阻断违规（bad_type）不被修复触碰 → 仍走完整有界自愈、行为与今相同。"""
    _kb_writable(tmp_path)
    state = {"n": 0}

    def runner(prompt, **kwargs):
        state["n"] += 1
        _bad_fm_page(kwargs["working_directory"])  # type=bogus，可解析但 bad_type
        return AgentRunResult(ok=True, final_text="done")

    result = run_guarded_write_result(tmp_path, "PROMPT", runner=runner, page_guard=True)
    assert result.exit_code == EXIT_CHECK_FAILED
    assert any(v.kind == "frontmatter.bad_type" for v in result.gate.violations)
    assert state["n"] == 3  # 首轮 + 2 自愈，未被修复短路
    assert "确定性修正" not in capsys.readouterr().err


def test_frontmatter_repair_untouched_when_clean(tmp_path: Path, capsys):
    """合法页过门禁 → 修复零触碰、stderr 无修正行。"""
    _kb_writable(tmp_path)
    result = run_guarded_write_result(
        tmp_path, "PROMPT", runner=make_runner(_good_page), page_guard=True
    )
    assert result.exit_code == EXIT_OK
    assert "确定性修正" not in capsys.readouterr().err


def test_frontmatter_repair_reverts_source_regression(tmp_path: Path, capsys):
    """修复页若仍含 page_guard 源不回退（sources.dropped，`run_check` 看不见）→ 门禁回滚修复（回归 Codex P2）。

    既有页 X（sources {a,b}）被 agent 写成「坏引号 + 丢源 b」：unparsable 掩盖了 sources.dropped；
    修引号后 dropped 浮现 → 门禁重判（含 `_check_source_regression`）判 X 仍阻断 → **回滚**到 agent 原写。
    无回滚（旧 `run_check`-only 验收）会保留 `title: 'a"b'` 半成品并打印「确定性修正」——这里反证已回滚。
    """
    _kb(tmp_path)
    _src(tmp_path, "a")
    _src(tmp_path, "b")
    _entity(tmp_path, "X", "[a, b]")  # 既有页 X，sources {a,b} 进 page_before

    def runner(prompt, **kwargs):
        (kwargs["working_directory"] / "wiki" / "entities" / "X.md").write_text(
            '---\ntitle: "a"b"\ntype: entity\ntags: []\nsources: [a]\n'
            "last_updated: 2026-06-03\n---\n\n正文\n",
            encoding="utf-8",
        )
        return AgentRunResult(ok=True, final_text="done")

    result = run_guarded_write_result(
        tmp_path, "P", runner=runner, page_guard=True, max_repair=0
    )
    assert result.exit_code == EXIT_CHECK_FAILED
    x = (tmp_path / "wiki" / "entities" / "X.md").read_text(encoding="utf-8")
    assert 'title: "a"b"' in x  # 回滚到 agent 原写（坏引号），非保留修复后的 'a"b'
    assert "确定性修正" not in capsys.readouterr().err  # kept 为空 → 不打印


def test_frontmatter_repair_reverts_hidden_alias_collision(tmp_path: Path, capsys):
    """unparsable 掩盖的跨页 alias 撞名，修引号后浮现 → 门禁回滚（`run_check` 侧，回归 Codex P2）。"""
    _kb(tmp_path)
    foo = tmp_path / "wiki" / "concepts" / "Foo.md"  # 既有页 stem foo
    foo.parent.mkdir(parents=True)
    foo.write_text(FM.format(type="concept", sources="[]", body="正文"), encoding="utf-8")

    def runner(prompt, **kwargs):
        (kwargs["working_directory"] / "wiki" / "concepts" / "Bar.md").write_text(
            '---\ntitle: "a"b"\ntype: concept\ntags: []\nsources: []\n'
            "last_updated: 2026-06-03\naliases: ['Foo']\n---\n\n正文\n",
            encoding="utf-8",
        )
        return AgentRunResult(ok=True, final_text="done")

    result = run_guarded_write_result(
        tmp_path, "P", runner=runner, page_guard=True, max_repair=0
    )
    assert result.exit_code == EXIT_CHECK_FAILED
    bar = (tmp_path / "wiki" / "concepts" / "Bar.md").read_text(encoding="utf-8")
    assert 'title: "a"b"' in bar  # 回滚（修复后会是 'a"b' 且撞名 Foo）
    assert "确定性修正" not in capsys.readouterr().err


def test_frontmatter_repair_skipped_on_heal_path(tmp_path: Path, capsys):
    """page_guard=False（heal 直调本核心）→ 修复不启用，保 heal 逐字节不变契约。

    坏引号页本可被宿主修好；但 heal 路不该被改页，故仍走自愈（runner 持续重写坏页 → 修不掉）
    → EXIT_CHECK_FAILED、stderr 无「确定性修正」。
    """
    _kb_writable(tmp_path)
    runner = _runner_quote_broken()
    result = run_guarded_write_result(tmp_path, "PROMPT", runner=runner, page_guard=False)
    assert result.exit_code == EXIT_CHECK_FAILED
    assert any(v.kind == "frontmatter.unparsable" for v in result.gate.violations)
    assert "确定性修正" not in capsys.readouterr().err


# --- report_outcome：骤缩警告独立成行（与断链并列、区分 kind）---


def test_report_outcome_reports_shrink_line(capsys):
    """body.shrank 警告经 report_outcome 汇成一行「骤缩」提示（不影响 gate.ok）。"""
    gate = GateResult.passed(warnings=[Violation("wiki/entities/X.md", "body.shrank", "…")])
    report_outcome(gate, AgentRunResult(ok=True, final_text="答案"))
    out = capsys.readouterr().out
    assert "骤缩" in out and "答案" in out


def test_report_outcome_dangling_and_shrink_separate(capsys):
    """断链与骤缩两类警告分别成行、互不混淆。"""
    gate = GateResult.passed(
        warnings=[
            Violation("wiki/a.md", "wikilink.broken", "[[X]] 无对应页面"),
            Violation("wiki/b.md", "body.shrank", "…"),
        ]
    )
    report_outcome(gate, AgentRunResult(ok=True, final_text=""))
    out = capsys.readouterr().out
    assert "断链" in out and "骤缩" in out
