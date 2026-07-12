"""P2 ingest 测试：fake runner 下各退出码路径（不打真实 LLM，见 docs/P2-最小闭环.md §12）。"""

from pathlib import Path

from conftest import make_runner, write_page

from guanlan.errors import (
    EXIT_AGENT_ERROR,
    EXIT_CHECK_FAILED,
    EXIT_OK,
    EXIT_RAW_MUTATED,
    EXIT_USAGE,
)
from guanlan.ingest import run_ingest
from guanlan.provenance import (
    compute_raw_digest,
    format_digest_value,
    stamp_raw_digest,
)


def _put_raw(kb: Path, name="doc.md") -> str:
    (kb / "raw" / name).write_text("原始资料\n", encoding="utf-8")
    return f"raw/{name}"


def _write_bad_fm(root: Path, relpath: str) -> None:
    """写一个 frontmatter 阻断性违规页（bad_type）—— 仍阻断写入、会触发自愈。"""
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '---\ntitle: "T"\ntype: bogus\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n',
        encoding="utf-8",
    )


def test_ingest_writes_compliant_wiki_ok(kb: Path):
    target = _put_raw(kb)

    def action(root: Path):
        write_page(root, "wiki/sources/doc.md", type="source", sources='["doc"]')

    rc = run_ingest(target, root=kb, runner=make_runner(action))
    assert rc == EXIT_OK
    assert (kb / "wiki" / "sources" / "doc.md").is_file()


def test_ingest_broken_link_is_warning_ok(kb: Path):
    """断链只作警告、不阻断写入（决策8）：写出断链页仍 EXIT_OK。"""
    target = _put_raw(kb)

    def action(root: Path):
        write_page(root, "wiki/concepts/Bad.md", body="[[Ghost]]")

    rc = run_ingest(target, root=kb, runner=make_runner(action))
    assert rc == EXIT_OK
    assert (kb / "wiki" / "concepts" / "Bad.md").is_file()


def test_ingest_self_heals_check_failure(kb: Path):
    """首轮写出阻断性 frontmatter 违规，自愈轮修好 → 最终 EXIT_OK（决策7）。"""
    from guanlan.runtime import AgentRunResult

    calls = {"n": 0}

    def runner(prompt, **kwargs):
        root = kwargs["working_directory"]
        calls["n"] += 1
        if calls["n"] == 1:  # 首轮：坏 frontmatter
            _write_bad_fm(root, "wiki/concepts/Bad.md")
        else:  # 自愈轮：写成合规页
            write_page(root, "wiki/concepts/Bad.md")
        return AgentRunResult(ok=True, final_text="done")

    rc = run_ingest(_put_raw(kb), root=kb, runner=runner)
    assert rc == EXIT_OK
    assert calls["n"] == 2  # 首轮 + 1 次自愈


def test_ingest_self_heal_bounded(kb: Path):
    """自愈轮数有界：持续阻断性违规最多重试 MAX_REPAIR_ATTEMPTS 次后判 CHECK_FAILED。"""
    from guanlan.gate import MAX_REPAIR_ATTEMPTS

    def action(root: Path):
        _write_bad_fm(root, "wiki/concepts/Bad.md")

    runner = make_runner(action)
    rc = run_ingest(_put_raw(kb), root=kb, runner=runner)
    assert rc == EXIT_CHECK_FAILED
    assert len(runner.calls) == 1 + MAX_REPAIR_ATTEMPTS  # 首轮 + N 次自愈


def test_ingest_preexisting_blocking_does_not_fail(kb: Path):
    """增量门禁（决策7）：库里已有的阻断性违规页不连累本次 ingest，仍 EXIT_OK。"""
    _write_bad_fm(kb, "wiki/concepts/Old.md")  # ingest 前就坏了

    def action(root: Path):
        write_page(root, "wiki/concepts/New.md")  # 本次只写合规页

    rc = run_ingest(_put_raw(kb), root=kb, runner=make_runner(action))
    assert rc == EXIT_OK


def test_ingest_mutates_raw_while_ok(kb: Path):
    target = _put_raw(kb)

    def action(root: Path):
        write_page(root, "wiki/concepts/Foo.md")
        (root / "raw" / "sneaky.md").write_text("x", encoding="utf-8")

    rc = run_ingest(target, root=kb, runner=make_runner(action))
    assert rc == EXIT_RAW_MUTATED


def test_ingest_mutates_raw_then_fails(kb: Path):
    """agent 改了 raw/ 后返回 not ok → 仍判 RAW_MUTATED（失败路径兜底完整性）。"""
    target = _put_raw(kb)

    def action(root: Path):
        (root / "raw" / "sneaky.md").write_text("x", encoding="utf-8")

    rc = run_ingest(target, root=kb, runner=make_runner(action, ok=False, error_type="runtime_error"))
    assert rc == EXIT_RAW_MUTATED


def test_ingest_agent_error_clean_raw(kb: Path):
    target = _put_raw(kb)
    rc = run_ingest(
        target, root=kb, runner=make_runner(None, ok=False, error_type="max_iterations")
    )
    assert rc == EXIT_AGENT_ERROR


def test_ingest_non_md_target_usage(kb: Path):
    (kb / "raw" / "doc.pdf").write_text("x", encoding="utf-8")
    rc = run_ingest("raw/doc.pdf", root=kb, runner=make_runner(None))
    assert rc == EXIT_USAGE


def test_ingest_target_outside_raw_usage(kb: Path):
    write_page(kb, "wiki/concepts/Foo.md")
    rc = run_ingest("wiki/concepts/Foo.md", root=kb, runner=make_runner(None))
    assert rc == EXIT_USAGE


def test_ingest_missing_file_usage(kb: Path):
    rc = run_ingest("raw/nope.md", root=kb, runner=make_runner(None))
    assert rc == EXIT_USAGE


def test_ingest_rejects_source_slug_collision(kb: Path, capsys):
    """raw/ 子目录里两篇 `.md` 落到同一 source 页 slug → 摄入前确定性拒绝，不跑 agent。"""
    (kb / "raw" / "a").mkdir()
    (kb / "raw" / "b").mkdir()
    (kb / "raw" / "a" / "report.md").write_text("甲\n", encoding="utf-8")
    (kb / "raw" / "b" / "report.md").write_text("乙\n", encoding="utf-8")

    def boom(root: Path):  # agent 不应被调用
        raise AssertionError("撞页应在跑 agent 前被拒绝")

    rc = run_ingest("raw/a/report.md", root=kb, runner=make_runner(boom))
    assert rc == EXIT_USAGE
    err = capsys.readouterr().err
    assert "report" in err and "raw/a/report.md" in err and "raw/b/report.md" in err


def test_ingest_rejects_slug_fold_collision(kb: Path, capsys):
    """异 basename 但 `raw_slug` 相同（空格↔连字符）仍会撞同一张页 → 也须拒（review §1：按 basename 判太窄）。"""
    (kb / "raw" / "a").mkdir()
    (kb / "raw" / "b").mkdir()
    (kb / "raw" / "a" / "annual report.md").write_text("甲\n", encoding="utf-8")
    (kb / "raw" / "b" / "annual-report.md").write_text("乙\n", encoding="utf-8")

    rc = run_ingest("raw/a/annual report.md", root=kb, runner=make_runner(None))
    assert rc == EXIT_USAGE
    assert "annual-report" in capsys.readouterr().err


def test_ingest_same_stem_different_ext_not_rejected(kb: Path):
    """convert 同源对 `report.pdf`+`report.md`（slug 同、但只 `.md` 参与判）不误伤：`.md` 仍可摄入。"""
    (kb / "raw" / "report.pdf").write_text("源 PDF\n", encoding="utf-8")
    (kb / "raw" / "report.md").write_text("转换后的 md\n", encoding="utf-8")

    def action(root: Path):
        write_page(root, "wiki/sources/report.md", type="source", sources='["report"]')

    rc = run_ingest("raw/report.md", root=kb, runner=make_runner(action))
    assert rc == EXIT_OK


def _seed_owned_summary(kb: Path) -> Path:
    """建 raw/2024/summary.md（属主）+ raw/2025/summary.md（同 slug 旁支草稿）+
    既有 wiki/sources/summary.md，其 raw_digest 指向 raw/2024/summary.md（模拟先前摄入）。返回属主 raw。"""
    (kb / "raw" / "2024").mkdir()
    (kb / "raw" / "2025").mkdir()
    owner = kb / "raw" / "2024" / "summary.md"
    owner.write_text("2024 摘要\n", encoding="utf-8")
    (kb / "raw" / "2025" / "summary.md").write_text("2025 草稿（未摄）\n", encoding="utf-8")
    write_page(kb, "wiki/sources/summary.md", type="source", sources='["summary"]')
    assert stamp_raw_digest(
        kb / "wiki" / "sources" / "summary.md",
        format_digest_value("raw/2024/summary.md", compute_raw_digest(owner)),
    )
    return owner


def test_ingest_reingest_owner_not_blocked_by_sibling(kb: Path):
    """合法重摄：既有属主页在（raw_digest 指向本文件），同 slug 旁支存在也放行（review §2 所有权豁免）。"""
    _seed_owned_summary(kb)

    def action(root: Path):  # 重摄：更新既有属主页
        write_page(root, "wiki/sources/summary.md", type="source", sources='["summary"]')

    rc = run_ingest("raw/2024/summary.md", root=kb, runner=make_runner(action))
    assert rc == EXIT_OK


def test_ingest_non_owner_into_owned_slug_rejected(kb: Path, capsys):
    """真撞：拿非属主旁支去覆盖属主页 → 摄入时当场拒（review §2 安全性不减）。"""
    _seed_owned_summary(kb)

    def boom(root: Path):  # agent 不应被调用
        raise AssertionError("非属主撞页应在跑 agent 前被拒")

    rc = run_ingest("raw/2025/summary.md", root=kb, runner=make_runner(boom))
    assert rc == EXIT_USAGE
    assert "summary" in capsys.readouterr().err


def test_ingest_not_a_kb_usage(tmp_path: Path):
    rc = run_ingest("raw/x.md", root=tmp_path, runner=make_runner(None))
    assert rc == EXIT_USAGE


def test_ingest_passes_workspace_write(kb: Path):
    target = _put_raw(kb)
    runner = make_runner(lambda root: write_page(root, "wiki/concepts/Foo.md"))
    run_ingest(target, root=kb, runner=runner)
    assert runner.calls[0]["permission_mode"] == "workspace-write"
    assert "raw/doc.md" in runner.calls[0]["prompt"]


def test_ingest_reingest_guards_sources_union(kb: Path):
    """P2.1 端到端：re-ingest 第二个源时，Agent 若把既有页 sources 覆盖成只含本次源，

    门禁 `sources.dropped` 阻断并自愈、把既有页 sources 守为并集（只增不减，决策P2.1-2）。
    """
    from guanlan.pages import load_page
    from guanlan.runtime import AgentRunResult

    # 库里已有源 a 与引用它的实体 X（sources:[a]）
    write_page(kb, "wiki/sources/a.md", type="source")
    write_page(kb, "wiki/entities/X.md", type="entity", sources='["a"]')
    target = _put_raw(kb, "b.md")  # 本次摄入第二个源 raw/b.md

    calls = {"n": 0}

    def runner(prompt, **kwargs):
        root = kwargs["working_directory"]
        calls["n"] += 1
        write_page(root, "wiki/sources/b.md", type="source")  # 建本次源页
        if calls["n"] == 1:
            write_page(root, "wiki/entities/X.md", type="entity", sources='["b"]')  # 覆盖丢 a
        else:
            write_page(root, "wiki/entities/X.md", type="entity", sources='["a", "b"]')  # 自愈并回
        return AgentRunResult(ok=True, final_text="done")

    rc = run_ingest(target, root=kb, runner=runner)
    assert rc == EXIT_OK
    assert calls["n"] == 2  # 首轮覆盖丢 a → 门禁阻断 → 1 次自愈并回
    meta, _ = load_page(kb / "wiki" / "entities" / "X.md")
    assert set(meta["sources"]) == {"a", "b"}  # 末态守为并集
