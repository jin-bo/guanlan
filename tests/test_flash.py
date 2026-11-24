"""快速收录（flash ingest）原型：fake runner 下的写集约束、提交校验、index/log 登记与加深。"""

from pathlib import Path

from conftest import make_runner, write_page

from guanlan.errors import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RAW_MUTATED, EXIT_USAGE
from guanlan.flash import SUMMARY_KEY, run_flash_ingest
from guanlan.ingest import run_ingest
from guanlan.pages import load_page
from guanlan.provenance import FLASH_KEY, RAW_DIGEST_KEY

BODY = "本篇讲述[[示例概念]]在二〇二四年的进展，给出三条关键结论与限定条件。" * 3


def _put_raw(kb: Path, name: str = "doc.md") -> str:
    (kb / "raw" / name).write_text("# 标题\n\n原始资料正文\n", encoding="utf-8")
    return f"raw/{name}"


def _candidate(slug="doc", *, type_="source", sources=None, body=BODY, summary="一句话摘要"):
    srcs = sources if sources is not None else [slug]
    return (
        "---\n"
        "title: '示例资料'\n"
        f"type: {type_}\n"
        "tags: []\n"
        f"sources: {srcs!r}\n"
        "last_updated: 2026-11-23\n"
        f"{SUMMARY_KEY}: '{summary}'\n"
        "---\n\n"
        f"# 示例资料\n\n{body}\n"
    )


def _write_candidate(text: str):
    def action(root: Path) -> None:
        p = root / "workspace" / "flash" / "doc.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    return action


def test_flash_commits_source_page_index_and_log(kb):
    runner = make_runner(_write_candidate(_candidate()))
    rc = run_flash_ingest(_put_raw(kb), root=kb, runner=runner)
    assert rc == EXIT_OK
    page = kb / "wiki" / "sources" / "doc.md"
    meta, body = load_page(page)
    assert meta[FLASH_KEY] == "flash"
    assert meta[RAW_DIGEST_KEY].startswith("raw/doc.md")
    assert SUMMARY_KEY not in meta  # 候选专用键不落盘
    assert "[[示例概念]]" in body
    index = (kb / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "- [示例资料](sources/doc.md) — 一句话摘要" in index
    assert "## Sources" in index
    log = (kb / "wiki" / "log.md").read_text(encoding="utf-8")
    assert log.endswith("ingest | 示例资料（快速收录）\n")
    assert "\n\n## [" not in log  # 不插空行，与既有条目连排
    assert not (kb / "workspace" / "flash" / "doc.md").exists()
    prompt = runner.calls[0]["prompt"]
    assert "workspace/flash/doc.md" in prompt and "〈资料结构〉" in prompt


def test_flash_restores_stray_wiki_writes(kb):
    def action(root: Path) -> None:
        _write_candidate(_candidate())(root)
        write_page(root, "wiki/concepts/示例概念.md", sources='["doc"]')
        (root / "wiki" / "index.md").write_text("被改了\n", encoding="utf-8")

    before_index = (kb / "wiki" / "index.md").read_bytes()
    rc = run_flash_ingest(_put_raw(kb), root=kb, runner=make_runner(action))
    assert rc == EXIT_CHECK_FAILED
    assert not (kb / "wiki" / "concepts" / "示例概念.md").exists()
    assert (kb / "wiki" / "index.md").read_bytes() == before_index
    assert not (kb / "wiki" / "sources" / "doc.md").exists()


def test_flash_repairs_bad_candidate(kb):
    texts = iter([_candidate(type_="concept"), _candidate()])

    def action(root: Path) -> None:
        _write_candidate(next(texts))(root)

    runner = make_runner(action)
    rc = run_flash_ingest(_put_raw(kb), root=kb, runner=runner)
    assert rc == EXIT_OK
    assert len(runner.calls) == 2
    assert "type 须为 'source'" in runner.calls[1]["prompt"]


def test_flash_repairs_gate_violation_and_rolls_back_page(kb):
    """候选自身合法但过不了门禁（来源名不存在）→ 正式页回滚、问题回喂；修不好则什么都不留。"""
    runner = make_runner(_write_candidate(_candidate(sources=["doc", "不存在的来源"])))
    rc = run_flash_ingest(_put_raw(kb), root=kb, runner=runner)
    assert rc == EXIT_CHECK_FAILED
    assert len(runner.calls) == 3  # 首轮 + 两轮自愈
    assert "sources.unresolved" in runner.calls[1]["prompt"]
    assert not (kb / "wiki" / "sources" / "doc.md").exists()
    assert "doc.md" not in (kb / "wiki" / "index.md").read_text(encoding="utf-8")


def test_flash_missing_candidate_fails_cleanly(kb):
    runner = make_runner(None)
    rc = run_flash_ingest(_put_raw(kb), root=kb, runner=runner)
    assert rc == EXIT_CHECK_FAILED
    assert "不存在" in runner.calls[1]["prompt"]
    assert not (kb / "wiki" / "sources").exists() or not any((kb / "wiki" / "sources").iterdir())


def test_flash_skips_existing_full_page(kb):
    write_page(kb, "wiki/sources/doc.md", type="source", sources='["doc"]', body="已整合的正文")
    runner = make_runner(None)
    rc = run_flash_ingest(_put_raw(kb), root=kb, runner=runner)
    assert rc == EXIT_OK
    assert runner.calls == []
    assert "已整合的正文" in (kb / "wiki" / "sources" / "doc.md").read_text(encoding="utf-8")


def test_reflash_keeps_sources_and_does_not_duplicate_index_or_log(kb):
    target = _put_raw(kb)
    write_page(kb, "wiki/sources/other.md", type="source", sources='["other"]')
    assert run_flash_ingest(target, root=kb, runner=make_runner(_write_candidate(_candidate()))) == EXIT_OK
    page = kb / "wiki" / "sources" / "doc.md"
    text = page.read_text(encoding="utf-8").replace("- doc", "- doc\n- other", 1)
    page.write_text(text, encoding="utf-8")
    assert run_flash_ingest(target, root=kb, runner=make_runner(_write_candidate(_candidate()))) == EXIT_OK
    meta, _ = load_page(page)
    assert meta["sources"] == ["other", "doc"] or set(meta["sources"]) == {"doc", "other"}
    index = (kb / "wiki" / "index.md").read_text(encoding="utf-8")
    assert index.count("sources/doc.md") == 1
    log = (kb / "wiki" / "log.md").read_text(encoding="utf-8")
    assert log.count("（快速收录）") == 1


def test_flash_raw_mutation_fails(kb):
    def action(root: Path) -> None:
        _write_candidate(_candidate())(root)
        (root / "raw" / "doc.md").write_text("改了\n", encoding="utf-8")

    rc = run_flash_ingest(_put_raw(kb), root=kb, runner=make_runner(action))
    assert rc == EXIT_RAW_MUTATED
    assert not (kb / "wiki" / "sources" / "doc.md").exists()


def test_flash_refuses_ambiguous_candidates(kb):
    write_page(
        kb, "wiki/sources/别名页.md", type="source", sources='["别名页"]',
        body="正文提到 `raw/doc.md` 但不是声明行",
    )
    runner = make_runner(None)
    rc = run_flash_ingest(_put_raw(kb), root=kb, runner=runner)
    assert rc == EXIT_USAGE
    assert runner.calls == []


def test_full_ingest_deepens_flash_page(kb):
    target = _put_raw(kb)
    assert run_flash_ingest(target, root=kb, runner=make_runner(_write_candidate(_candidate()))) == EXIT_OK
    page = kb / "wiki" / "sources" / "doc.md"

    def deepen(root: Path) -> None:
        write_page(root, "wiki/concepts/示例概念.md", sources='["doc"]')

    runner = make_runner(deepen)
    assert run_ingest(target, root=kb, runner=runner) == EXIT_OK
    assert "快速收录" in runner.calls[0]["prompt"]
    meta, _ = load_page(page)
    assert FLASH_KEY not in meta
    assert meta[RAW_DIGEST_KEY].startswith("raw/doc.md")


def test_health_lists_flash_pending_without_failing(kb):
    import json

    from guanlan.health import format_report, run_health

    assert run_flash_ingest(_put_raw(kb), root=kb, runner=make_runner(_write_candidate(_candidate()))) == EXIT_OK
    report = run_health(kb / "wiki")
    assert report.flash_pending == ["wiki/sources/doc.md"]
    assert all(f.page != "wiki/sources/doc.md" for f in report.findings)  # 状态不是问题
    assert "待加深：1 篇" in format_report(report, json_output=False)
    assert json.loads(format_report(report, json_output=True))["flash_pending"] == ["wiki/sources/doc.md"]


def test_health_json_unchanged_without_flash_pages(kb):
    import json

    from guanlan.health import format_report, run_health

    data = json.loads(format_report(run_health(kb / "wiki"), json_output=True))
    assert "flash_pending" not in data


def test_flash_interrupt_restores_wiki_and_drops_candidate(kb):
    """Agent 运行中被打断（Ctrl-C / 取消）也要还原越界写、删候选，再把中断原样上抛。"""
    import pytest

    def action(root: Path) -> None:
        _write_candidate(_candidate())(root)
        write_page(root, "wiki/concepts/越界.md", sources='["doc"]')
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_flash_ingest(_put_raw(kb), root=kb, runner=make_runner(action))
    assert not (kb / "wiki" / "concepts" / "越界.md").exists()
    assert not (kb / "wiki" / "sources" / "doc.md").exists()
    assert not (kb / "workspace" / "flash" / "doc.md").exists()


def test_flash_summary_brackets_neutralized_in_index(kb):
    """模型写的摘要里的 `[x](y)` 不能被 index 解析成多一条登记。"""
    runner = make_runner(_write_candidate(_candidate(summary="见[原文](raw/doc.md)")))
    assert run_flash_ingest(_put_raw(kb), root=kb, runner=runner) == EXIT_OK
    index = (kb / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "— 见［原文］(raw/doc.md)" in index
    assert "[原文](raw/doc.md)" not in index
