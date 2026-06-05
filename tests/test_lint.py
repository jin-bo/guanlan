"""P3 lint 图级质量测试（零 LLM，见 docs/P3-健康与图谱.md §11）。"""

from pathlib import Path

from guanlan.check import run_check
from guanlan.lint import MISSING_ENTITY_MIN_REFS, lint_entrypoint, run_lint

FM = "---\ntitle: '{title}'\ntype: {type}\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n{body}\n"


def _page(wiki: Path, rel: str, *, title="T", type="concept", body="正文") -> None:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(title=title, type=type, body=body), encoding="utf-8")


def _seed_config(wiki: Path) -> None:
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")


def _kinds(report) -> list[str]:
    return [f.kind for f in report.findings]


# ---------- 孤儿 ----------


def test_orphan_detected_and_linked_not(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="entity", body="见 [[Bar]]")
    _page(wiki, "concepts/Bar.md", body="无出链")  # 被 Foo 链入 → 非孤儿
    _page(wiki, "concepts/Lonely.md", body="无人链入")  # 孤儿

    report = run_lint(wiki)
    orphans = {f.page for f in report.findings if f.kind == "lint.orphan"}
    assert any("Lonely.md" in p for p in orphans)
    assert any("Foo.md" in p for p in orphans)  # Foo 无入链也是孤儿
    assert not any("Bar.md" in p for p in orphans)


def test_orphan_detail_carries_type(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "sources/x.md", type="source", body="资料摘要")

    report = run_lint(wiki)
    assert any(
        f.kind == "lint.orphan" and "type=source" in f.detail for f in report.findings
    )


# ---------- 断链：与 check 同口径 ----------


def test_broken_link_matches_check(tmp_path: Path):
    """决策P3-6：lint.broken_link 与 check.wikilink.broken 同源同口径。

    lint 走 graph，目标已归一为 stem（[[baz]]）；check 回显原文（[[Baz]]）。同一断链事实，
    比对在 stem 层成立——config 链接（[[overview]]）两边都不报。
    """
    from guanlan.pages import link_stem

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Bar.md", body="链到 [[Baz]] 和 config [[overview]]")

    def _targets(details: list[str]) -> list[str]:
        return sorted(link_stem(d.split("[[", 1)[1].split("]]", 1)[0]) for d in details)

    report = run_lint(wiki)
    lint_broken = [f.detail for f in report.findings if f.kind == "lint.broken_link"]
    check = run_check(wiki)
    check_broken = [v.detail for v in check.violations if v.kind == "wikilink.broken"]

    assert _targets(lint_broken) == _targets(check_broken) == ["baz"]


# ---------- 缺失实体 ----------


def test_missing_entity_threshold(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # [[Baz]] 被两张不同页引用且无页 → missing_entity（达阈值 2）。
    _page(wiki, "concepts/A.md", body="见 [[Baz]]")
    _page(wiki, "concepts/B.md", body="也见 [[Baz]]")
    # [[Solo]] 只被一页引用 → 不达阈值，仅 broken_link。
    _page(wiki, "concepts/C.md", body="见 [[Solo]]")

    report = run_lint(wiki)
    missing = [f for f in report.findings if f.kind == "lint.missing_entity"]
    assert len(missing) == 1
    assert "[[baz]]" in missing[0].detail and "2 页" in missing[0].detail
    assert missing[0].page == ""  # 跨页聚合、无单一归属页
    assert not any("solo" in f.detail.lower() for f in missing)


def test_missing_entity_same_page_twice_not_counted(tmp_path: Path):
    """同一页引用同一缺失目标多次只算一票（边按 (source,target) 去重）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/A.md", body="见 [[Baz]] 再见 [[Baz]] 又见 [[Baz]]")

    report = run_lint(wiki)
    assert MISSING_ENTITY_MIN_REFS == 2
    assert "lint.missing_entity" not in _kinds(report)  # 仅 1 张不同页


def test_existing_page_not_missing_entity(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Baz.md", type="entity", body="实体页")  # 已存在
    _page(wiki, "concepts/A.md", body="见 [[Baz]]")
    _page(wiki, "concepts/B.md", body="也见 [[Baz]]")

    report = run_lint(wiki)
    assert "lint.missing_entity" not in _kinds(report)  # 已有页 → 解析成功，非断链
    assert "lint.broken_link" not in _kinds(report)


# ---------- 退出码 / JSON ----------


def test_exit_codes_default_zero_strict_six(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Lonely.md", body="孤儿 + 断链 [[Ghost]]")

    assert lint_entrypoint(tmp_path, json_output=False, strict=False) == 0
    assert lint_entrypoint(tmp_path, json_output=False, strict=True) == 6


def test_clean_wiki_strict_zero(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # 互相链接、无断链、无孤儿。
    _page(wiki, "entities/Foo.md", type="entity", body="见 [[Bar]]")
    _page(wiki, "concepts/Bar.md", body="回链 [[Foo]]")

    assert lint_entrypoint(tmp_path, json_output=False, strict=True) == 0


def test_json_contract(tmp_path: Path, capsys):
    import json

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Lonely.md", body="孤儿 [[Ghost]]")

    lint_entrypoint(tmp_path, json_output=True, strict=False)
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["pages_checked"] == 1
    kinds = {f["kind"] for f in data["findings"]}
    assert "lint.orphan" in kinds and "lint.broken_link" in kinds


def test_non_kb_root_fails(tmp_path: Path):
    assert lint_entrypoint(tmp_path, json_output=False, strict=False) == 1
