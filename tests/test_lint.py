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


# ---------- finding 因果排序（gbrain §3，纯展示层） ----------


def test_missing_entity_ordered_before_broken_link(tmp_path: Path):
    """根因 missing_entity 排在其果 broken_link 之前（机械因果：建页即消解聚合断链）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # [[Baz]] 被三页引用（> MISSING_ENTITY_MIN_REFS=2，留余量）→ missing_entity，
    # 且每条引用本身也是 broken_link。
    for n in "ABC":
        _page(wiki, f"concepts/{n}.md", body="见 [[Baz]]")

    kinds = _kinds(run_lint(wiki))
    assert "lint.missing_entity" in kinds and "lint.broken_link" in kinds
    assert kinds.index("lint.missing_entity") < kinds.index("lint.broken_link")


def test_topology_findings_sink_below_data_integrity(tmp_path: Path):
    """拓扑优化建议（hub/cut_vertex/…）沉底于数据完整性类（broken_link/orphan）之后。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # 八辐星形枢纽（度 8 > HUB_MIN_DEGREE=5，留余量、不卡在度地板上）+ 一条断链：
    # 既出 hub_node 又出 broken_link。
    spokes = "ABCDEFGH"
    body = " ".join(f"[[{n}]]" for n in spokes) + " 断链 [[Ghost]]"
    _page(wiki, "concepts/Hub.md", body=body)
    for n in spokes:
        _page(wiki, f"concepts/{n}.md", body="回链 [[Hub]]")

    kinds = _kinds(run_lint(wiki))
    topo = {
        "lint.hub_node",
        "lint.cut_vertex",
        "lint.thin_intercommunity_link",
        "lint.isolated_community",
        "lint.bridge_edge",
    }
    present_topo = [k for k in kinds if k in topo]
    assert present_topo, "本用例应至少触发一类拓扑建议"
    first_topo = min(kinds.index(k) for k in present_topo)
    # broken_link 是数据完整性类，必须排在任何拓扑建议之前。
    assert kinds.index("lint.broken_link") < first_topo


def test_ordering_preserves_intra_kind_order_and_is_stable(tmp_path: Path):
    """稳定排序：各 kind 内既有确定性次序（broken_link 按 (source,target)）不变，且可重放。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/A.md", body="断链 [[zzz]]")
    _page(wiki, "concepts/B.md", body="断链 [[aaa]]")

    findings1 = run_lint(wiki).findings
    findings2 = run_lint(wiki).findings
    # 同库同跑两次完全一致（确定性、字节稳定）。
    assert [(f.page, f.kind, f.detail) for f in findings1] == [
        (f.page, f.kind, f.detail) for f in findings2
    ]
    # broken_link 内部仍按源页路径升序（A.md 在 B.md 前），重排不打乱 kind 内次序。
    broken_pages = [f.page for f in findings1 if f.kind == "lint.broken_link"]
    assert broken_pages == sorted(broken_pages)


def test_ordering_does_not_change_finding_set_or_exit_code(tmp_path: Path):
    """因果排序只改顺序：finding 集合、计数、退出码（建议非门禁）全不变。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Lonely.md", body="孤儿 + 断链 [[Ghost]]")

    report = run_lint(wiki)
    # 集合（与顺序无关）即检测所得，逐项核对——确认重排不增不减、不去重。
    # Lonely.md 无入链 → orphan；[[Ghost]] 单引 → broken_link（未达 missing_entity 阈值）。
    assert {(f.page, f.kind) for f in report.findings} == {
        ("wiki/concepts/Lonely.md", "lint.orphan"),
        ("wiki/concepts/Lonely.md", "lint.broken_link"),
    }
    assert lint_entrypoint(tmp_path, json_output=False, strict=False) == 0
    assert lint_entrypoint(tmp_path, json_output=False, strict=True) == 6
