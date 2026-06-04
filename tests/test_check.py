"""P2 `guanlan check` 确定性校验测试（零 LLM，见 docs/P2-最小闭环.md §12）。"""

from pathlib import Path

from guanlan.check import format_report, main, run_check

FM = """---
title: "{title}"
type: {type}
tags: []
sources: {sources}
last_updated: 2026-06-03
---

{body}
"""


def _page(path: Path, *, title="T", type="concept", sources="[]", body="正文") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FM.format(title=title, type=type, sources=sources, body=body), encoding="utf-8")


def _seed_config(wiki: Path) -> None:
    """index/log/overview 是 config 页，给最小占位（不带 frontmatter 也不应被校验）。"""
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("无 frontmatter 的活体综述\n", encoding="utf-8")


def _kinds(result) -> set[str]:
    return {v.kind for v in result.violations}


def test_clean_wiki_passes(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki / "sources" / "src-a.md", type="source", sources='["src-a"]')
    _page(wiki / "entities" / "Foo.md", type="entity", body="见 [[src-a]] 与 [[Foo]]")

    result = run_check(wiki)
    assert result.ok
    assert result.pages_checked == 2  # config 页不计入
    assert result.violations == []


def test_config_pages_excluded_but_are_link_targets(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # 链到 config 页 overview/index —— 合法目标，不应断链。
    _page(wiki / "concepts" / "Bar.md", body="参见 [[overview]] 和 [[index]]")

    result = run_check(wiki)
    assert result.ok, result.violations


def test_missing_wiki_dir_fails(tmp_path: Path):
    """wiki/ 不存在时判失败（写门禁收尾不能把'空扫描'当干净）。"""
    result = run_check(tmp_path / "wiki")  # 不创建
    assert not result.ok
    assert result.pages_checked == 0
    assert "wiki.missing" in _kinds(result)


def test_missing_frontmatter_block(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    (wiki / "entities" / "NoFm.md").parent.mkdir(parents=True, exist_ok=True)
    (wiki / "entities" / "NoFm.md").write_text("# 无 frontmatter\n", encoding="utf-8")

    result = run_check(wiki)
    assert not result.ok
    assert "frontmatter.block_missing" in _kinds(result)


def test_missing_key(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    p = wiki / "concepts" / "NoDate.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '---\ntitle: "X"\ntype: concept\ntags: []\nsources: []\n---\n\n正文\n',
        encoding="utf-8",
    )

    result = run_check(wiki)
    assert not result.ok
    assert any(
        v.kind == "frontmatter.missing_key" and "last_updated" in v.detail
        for v in result.violations
    )


def test_bad_type(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki / "concepts" / "BadType.md", type="bogus")

    result = run_check(wiki)
    assert not result.ok
    assert "frontmatter.bad_type" in _kinds(result)


def test_bad_type_unhashable_does_not_crash(tmp_path: Path):
    """type 为 list/dict 等 unhashable 值时记 bad_type，而非抛 TypeError。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    p = wiki / "concepts" / "Weird.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '---\ntitle: "X"\ntype: []\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n',
        encoding="utf-8",
    )

    result = run_check(wiki)  # 不抛异常
    assert not result.ok
    assert "frontmatter.bad_type" in _kinds(result)


def test_broken_wikilink(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki / "concepts" / "Bar.md", body="链到不存在的 [[Nope]]")

    result = run_check(wiki)
    assert not result.ok
    assert any(v.kind == "wikilink.broken" and "Nope" in v.detail for v in result.violations)


def test_wikilink_alias_and_anchor_resolve(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki / "entities" / "Foo.md", type="entity")
    # 别名 + 锚点 + 大小写差异都应解析到 Foo.md。
    _page(wiki / "concepts" / "Bar.md", body="见 [[foo|福]] 与 [[Foo#要点]]")

    result = run_check(wiki)
    assert result.ok, result.violations


def test_wikilink_title_with_dot_resolves(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki / "entities" / "大语言模型3.5技术报告.md", type="entity")
    _page(wiki / "concepts" / "指令微调.md", body="典型范例：[[大语言模型3.5技术报告]]")

    result = run_check(wiki)
    assert result.ok, result.violations


def test_sources_unresolved(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki / "entities" / "Foo.md", type="entity", sources='["ghost"]')

    result = run_check(wiki)
    assert not result.ok
    assert any(
        v.kind == "sources.unresolved" and "ghost" in v.detail for v in result.violations
    )


def test_sources_path_traversal_rejected(tmp_path: Path):
    """sources slug 用 `..` 越界指向 sources/ 之外的页面，必须判 unresolved，不能借 is_file 蒙混。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki / "concepts" / "Foo.md", type="concept")  # 存在但不在 sources/ 下
    # 若不校验 ../，`wiki/sources/../concepts/Foo.md` 会命中 concepts/Foo.md 而漏判。
    _page(wiki / "entities" / "Bar.md", type="entity", sources='["../concepts/Foo"]')

    result = run_check(wiki)
    assert not result.ok
    assert any(
        v.kind == "sources.unresolved" and "../concepts/Foo" in v.detail
        for v in result.violations
    )


def test_json_contract_and_exit_codes(tmp_path: Path, capsys):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki / "sources" / "src-a.md", type="source", sources='["src-a"]')

    # 合规 → 0
    assert main(["-C", str(tmp_path), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out
    assert '"pages_checked": 1' in out

    # 断链 → 3
    _page(wiki / "concepts" / "Bad.md", body="[[Ghost]]")
    assert main(["-C", str(tmp_path), "--json"]) == 3
    # --json 的失败报告也走 stdout（机器可读契约；`--json > file` 不应得空文件）。
    out = capsys.readouterr().out
    assert '"ok": false' in out
    assert "wikilink.broken" in out


def test_format_report_human_readable():
    from guanlan.check import CheckResult, Violation

    result = CheckResult(
        ok=False,
        pages_checked=3,
        violations=[Violation("wiki/concepts/Bar.md", "wikilink.broken", "[[Baz]] 无对应页面")],
    )
    text = format_report(result, json_output=False)
    assert "wikilink.broken" in text
    assert "wiki/concepts/Bar.md" in text
