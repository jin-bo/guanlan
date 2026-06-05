"""P2 query 测试：只读路径 + --backfill 门禁（不打真实 LLM，见 docs/P2-最小闭环.md §12）。"""

from pathlib import Path

from conftest import make_runner, write_page

from guanlan.errors import (
    EXIT_AGENT_ERROR,
    EXIT_CHECK_FAILED,
    EXIT_OK,
    EXIT_RAW_MUTATED,
)
from guanlan.query import run_query


# --- 默认只读路径 ---


def test_query_readonly_prints_answer_no_snapshot(kb: Path, capsys):
    runner = make_runner(None, final_text="带 [[Foo]] 引用的答案")
    rc = run_query("什么是 Foo？", root=kb, runner=runner)
    assert rc == EXIT_OK
    assert "带 [[Foo]] 引用的答案" in capsys.readouterr().out
    # 只读路径透传 read-only 姿态。
    assert runner.calls[0]["permission_mode"] == "read-only"


def test_query_readonly_agent_error(kb: Path):
    rc = run_query(
        "q", root=kb, runner=make_runner(None, ok=False, error_type="runtime_error")
    )
    assert rc == EXIT_AGENT_ERROR


def test_query_readonly_only_needs_wiki(tmp_path: Path):
    """只读路径仅要求 wiki/（无 raw/、AGENTAO.md 也能跑）。"""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    rc = run_query("q", root=tmp_path, runner=make_runner(None, final_text="ans"))
    assert rc == EXIT_OK


# --- --backfill 路径（与 ingest 门禁一致）---


def test_backfill_compliant_synthesis_ok(kb: Path):
    def action(root: Path):
        write_page(root, "wiki/syntheses/q.md", type="synthesis")

    runner = make_runner(action)
    rc = run_query("q", root=kb, backfill=True, runner=runner)
    assert rc == EXIT_OK
    assert runner.calls[0]["permission_mode"] == "workspace-write"


def test_backfill_broken_check_failed(kb: Path):
    """阻断性 frontmatter 违规（bad_type）→ CHECK_FAILED；断链已降级为警告，不再触发。"""
    def action(root: Path):
        p = root / "wiki" / "syntheses" / "q.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '---\ntitle: "T"\ntype: bogus\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n',
            encoding="utf-8",
        )

    rc = run_query("q", root=kb, backfill=True, runner=make_runner(action))
    assert rc == EXIT_CHECK_FAILED


def test_backfill_broken_link_is_warning_ok(kb: Path):
    """--backfill 写出断链综述 → 断链作警告、不阻断 → EXIT_OK（决策8）。"""
    def action(root: Path):
        write_page(root, "wiki/syntheses/q.md", type="synthesis", body="[[Ghost]]")

    rc = run_query("q", root=kb, backfill=True, runner=make_runner(action))
    assert rc == EXIT_OK


def test_backfill_mutates_raw_while_ok(kb: Path):
    def action(root: Path):
        write_page(root, "wiki/syntheses/q.md", type="synthesis")
        (root / "raw" / "sneaky.md").write_text("x", encoding="utf-8")

    rc = run_query("q", root=kb, backfill=True, runner=make_runner(action))
    assert rc == EXIT_RAW_MUTATED


def test_backfill_mutates_raw_then_fails(kb: Path):
    def action(root: Path):
        (root / "raw" / "sneaky.md").write_text("x", encoding="utf-8")

    rc = run_query(
        "q",
        root=kb,
        backfill=True,
        runner=make_runner(action, ok=False, error_type="runtime_error"),
    )
    assert rc == EXIT_RAW_MUTATED


def test_backfill_agent_error_clean_raw(kb: Path):
    rc = run_query(
        "q",
        root=kb,
        backfill=True,
        runner=make_runner(None, ok=False, error_type="max_iterations"),
    )
    assert rc == EXIT_AGENT_ERROR
