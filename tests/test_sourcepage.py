"""本篇 source 页身份定位（`guanlan/sourcepage.py`）与 ingest prompt / raw_digest stamp 的接线。**零 LLM。**

主断言是**证据分档**：`raw_digest` 精确路径与唯一的源文件声明行可认定归属；正文普通提及、多篇声明、
证据冲突都只能当候选——**即使只有一张命中也不升格**，否则一张引用了本篇的别篇摘要会被当成本篇覆盖、
还被 stamp 上本篇指纹。多张有归属依据时全部列为候选，不自动选定。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from conftest import make_runner

from guanlan.errors import EXIT_OK
from guanlan.ingest import build_ingest_prompt, run_ingest
from guanlan.pages import load_page
from guanlan.provenance import compute_raw_digest, format_digest_value, parse_digest_value
from guanlan.sourcepage import locate_source_page

RAW = "专题/示例报告 第1期.md"


def _src(kb: Path, name: str, *, digest: str | None = None, body: str = "正文") -> Path:
    p = kb / "wiki" / "sources" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = f"title: '{name}'\ntype: source\ntags: []\nsources: ['{name}']\nlast_updated: 2026-10-17\n"
    if digest is not None:
        fm += f"raw_digest: '{digest}@sha256:{'0' * 64}'\n"
    p.write_text(f"---\n{fm}---\n\n# {name}\n\n{body}\n", encoding="utf-8")
    return p


def _decl(rel: str) -> str:
    return f"> 原始资料：`raw/{rel}`"


def _raw(kb: Path, rel: str = RAW) -> Path:
    p = kb / "raw" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("资料正文\n", encoding="utf-8")
    return p


def test_filename_match_wins_without_scanning(kb: Path):
    page = _src(kb, "示例报告-第1期")
    _src(kb, "legacy", body=_decl(RAW))  # 有声明的旧页也不抢：文件名命中优先
    loc = locate_source_page(kb, RAW)
    assert (loc.page, loc.basis) == (page, "文件名")


def test_declaration_line_identifies_renamed_page(kb: Path):
    """实测情形：旧页用英文 slug 命名、正文有模板声明行 → 认定为本篇，不再让 Agent 另建。"""
    page = _src(kb, "example-report-issue-1", body=_decl(RAW))
    loc = locate_source_page(kb, RAW)
    assert (loc.page, loc.basis) == (page, "原始资料声明")


def test_raw_digest_identifies_renamed_page(kb: Path):
    page = _src(kb, "legacy", digest=f"raw/{RAW}")
    loc = locate_source_page(kb, RAW)
    assert (loc.page, loc.basis) == (page, "raw_digest")


@pytest.mark.parametrize(
    "body",
    [
        f"对比参见 `raw/{RAW}` 的第二节。",  # 普通提及
        f"> 原始资料：`raw/{RAW}`，另见附件",  # 声明行之外还有别的字：不是模板行
        f"```\n{_decl(RAW)}\n```",  # 围栏代码块里的示例
    ],
)
def test_plain_mention_is_only_a_candidate_even_if_unique(kb: Path, body: str):
    """只有一张页提到本篇也不认定——它可能是别篇摘要在引用本篇。"""
    page = _src(kb, "other-summary", body=body)
    loc = locate_source_page(kb, RAW)
    assert loc.page is None
    assert [p for p, _ in loc.candidates] == [page]


def test_multiple_declarations_are_candidate(kb: Path):
    page = _src(kb, "combined", body=f"{_decl(RAW)}\n{_decl('别篇.md')}")
    loc = locate_source_page(kb, RAW)
    assert loc.page is None
    assert loc.candidates == [(page, "声明了多篇原始资料，含本篇")]


def test_conflicting_digest_is_candidate_not_owner(kb: Path):
    """`raw_digest` 指向别篇、却写着本篇声明行：证据冲突，不认定。"""
    page = _src(kb, "conflict", digest="raw/别篇.md", body=_decl(RAW))
    loc = locate_source_page(kb, RAW)
    assert loc.page is None
    assert [p for p, _ in loc.candidates] == [page]


def test_two_owners_are_listed_not_chosen(kb: Path):
    a = _src(kb, "a-legacy", body=_decl(RAW))
    b = _src(kb, "b-legacy", digest=f"raw/{RAW}")
    loc = locate_source_page(kb, RAW)
    assert loc.page is None
    assert {p for p, _ in loc.candidates} == {a, b}


def test_path_match_is_verbatim(kb: Path):
    """只认逐字路径：同名不同目录的声明不算本篇。"""
    _src(kb, "legacy", body=_decl("别的目录/示例报告 第1期.md"))
    assert locate_source_page(kb, RAW) == locate_source_page(kb, "不存在.md")
    assert locate_source_page(kb, RAW).page is None
    assert locate_source_page(kb, RAW).candidates == []


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="需 POSIX 非 root 权限语义")
def test_unreadable_page_is_skipped(kb: Path):
    locked = _src(kb, "locked", body=_decl(RAW))
    page = _src(kb, "legacy", body=_decl(RAW))
    locked.chmod(0)
    try:
        assert locate_source_page(kb, RAW).page == page
    finally:
        locked.chmod(0o644)


def test_prompt_found_by_evidence_names_page_and_slug(kb: Path):
    _src(kb, "example-report-issue-1", body=_decl(RAW))
    prompt = build_ingest_prompt(kb, RAW)
    assert "本篇 source 页**已存在**：`wiki/sources/example-report-issue-1.md`" in prompt
    assert "按其原始资料声明认定为本篇的 source 页；沿用该页，不改名" in prompt
    assert "本篇在各页 `sources` 里写作 `example-report-issue-1`" in prompt
    assert "先确认 `wiki/sources/<该名>.md` 确实存在" in prompt


def test_prompt_candidates_are_listed_without_auto_choice(kb: Path):
    _src(kb, "other-summary", body=f"参见 `raw/{RAW}`。")
    prompt = build_ingest_prompt(kb, RAW)
    assert "wrapper 未能确定本篇 source 页" in prompt
    assert "`wiki/sources/other-summary.md`（页内提到本篇路径，可能只是引用）" in prompt
    assert "确认都不是才新建 `wiki/sources/示例报告-第1期.md`" in prompt
    assert "不要自行合并、改名或删除这些页" in prompt
    assert "已存在" not in prompt


def test_prompt_missing_gives_slug_and_search_first(kb: Path):
    prompt = build_ingest_prompt(kb, RAW)
    assert (
        "wrapper 未定位到本篇 source 页；检索中若发现同源既有页，优先读取并更新它，"
        "确认不存在后才新建 `wiki/sources/示例报告-第1期.md`" in prompt
    )
    assert "本篇在各页 `sources` 里写作 `示例报告-第1期`" in prompt


def _digest_of(page: Path) -> str | None:
    meta, _ = load_page(page)
    parsed = parse_digest_value((meta or {}).get("raw_digest"))
    return parsed[0] if parsed else None


def test_ingest_stamps_page_found_by_declaration(kb: Path):
    raw = _raw(kb)
    page = _src(kb, "example-report-issue-1", body=_decl(RAW))
    rc = run_ingest(f"raw/{RAW}", root=kb, runner=make_runner(None))
    assert rc == EXIT_OK
    meta, _ = load_page(page)
    assert meta["raw_digest"] == format_digest_value(f"raw/{RAW}", compute_raw_digest(raw))


def test_ingest_does_not_stamp_candidate(kb: Path):
    _raw(kb)
    page = _src(kb, "other-summary", body=f"参见 `raw/{RAW}`。")
    rc = run_ingest(f"raw/{RAW}", root=kb, runner=make_runner(None))
    assert rc == EXIT_OK
    assert _digest_of(page) is None


def test_ingest_stamps_page_agent_created_by_slug(kb: Path):
    """摄入后重新定位：Agent 按 prompt 给的 slug 新建了页 → stamp 它，不 stamp 只是提及本篇的旁页。"""
    _raw(kb)
    other = _src(kb, "other-summary", body=f"参见 `raw/{RAW}`。")

    def agent(root):
        _src(Path(root), "示例报告-第1期", body=_decl(RAW))

    rc = run_ingest(f"raw/{RAW}", root=kb, runner=make_runner(agent))
    assert rc == EXIT_OK
    assert _digest_of(kb / "wiki" / "sources" / "示例报告-第1期.md") == f"raw/{RAW}"
    assert _digest_of(other) is None
