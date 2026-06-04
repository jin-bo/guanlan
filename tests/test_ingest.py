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


def _put_raw(kb: Path, name="doc.md") -> str:
    (kb / "raw" / name).write_text("原始资料\n", encoding="utf-8")
    return f"raw/{name}"


def test_ingest_writes_compliant_wiki_ok(kb: Path):
    target = _put_raw(kb)

    def action(root: Path):
        write_page(root, "wiki/sources/doc.md", type="source", sources='["doc"]')

    rc = run_ingest(target, root=kb, runner=make_runner(action))
    assert rc == EXIT_OK
    assert (kb / "wiki" / "sources" / "doc.md").is_file()


def test_ingest_broken_page_check_failed(kb: Path):
    target = _put_raw(kb)

    def action(root: Path):
        write_page(root, "wiki/concepts/Bad.md", body="[[Ghost]]")

    rc = run_ingest(target, root=kb, runner=make_runner(action))
    assert rc == EXIT_CHECK_FAILED
    # 失败时 wiki/ 改动留在磁盘待人工修正。
    assert (kb / "wiki" / "concepts" / "Bad.md").is_file()


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


def test_ingest_not_a_kb_usage(tmp_path: Path):
    rc = run_ingest("raw/x.md", root=tmp_path, runner=make_runner(None))
    assert rc == EXIT_USAGE


def test_ingest_passes_workspace_write(kb: Path):
    target = _put_raw(kb)
    runner = make_runner(lambda root: write_page(root, "wiki/concepts/Foo.md"))
    run_ingest(target, root=kb, runner=runner)
    assert runner.calls[0]["permission_mode"] == "workspace-write"
    assert "raw/doc.md" in runner.calls[0]["prompt"]
