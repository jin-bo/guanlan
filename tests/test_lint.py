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


# ---------- P3.11 断链「最近页」建议（零 LLM token overlap） ----------


def _broken(report, target_stem: str):
    """取 detail 指向给定 target stem 的唯一 broken_link finding。"""
    hits = [
        f
        for f in report.findings
        if f.kind == "lint.broken_link" and f"[[{target_stem}]]" in f.detail
    ]
    assert len(hits) == 1, f"期望唯一 broken_link for {target_stem}，得 {len(hits)}"
    return hits[0]


def test_suggestion_cjk_overlap(tmp_path: Path):
    """CJK 2-gram 重叠：断链 [[多头注意力机制]] → 既有页 多头注意力.md（决策P3.11-3）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/多头注意力.md", title="多头注意力", body="正文")
    _page(wiki, "concepts/ref.md", title="引用页", body="见 [[多头注意力机制]]")

    f = _broken(run_lint(wiki), "多头注意力机制")
    assert f.suggestion == "wiki/concepts/多头注意力.md"
    assert "疑似已有页" in f.detail and "多头注意力.md" in f.detail


def test_suggestion_not_diluted_by_mixed_language_stem(tmp_path: Path):
    """分字段打分：英文 kebab slug stem 不稀释 CJK title 的强匹配（决策P3.11-3a，Codex 复审 P2）。

    页 multi-head-attention.md（title 多头注意力）应被 [[多头注意力机制]] 命中——title 单算
    4/6=0.67 ≥ 阈值；若把英文 stem token 并进同一分母会跌到 4/9 被压掉。
    """
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/multi-head-attention.md", title="多头注意力", body="正文")
    _page(wiki, "concepts/ref.md", title="引用页", body="见 [[多头注意力机制]]")

    f = _broken(run_lint(wiki), "多头注意力机制")
    assert f.suggestion == "wiki/concepts/multi-head-attention.md"


def test_suggestion_ascii_shared_token(tmp_path: Path):
    """共享整词：断链 [[attention]] → self-attention.md（Jaccard 0.5 ≥ 阈值）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/self-attention.md", title="Self-Attention", body="正文")
    _page(wiki, "concepts/ref.md", title="引用页", body="见 [[attention]]")

    f = _broken(run_lint(wiki), "attention")
    assert f.suggestion == "wiki/concepts/self-attention.md"


def test_no_suggestion_for_typo(tmp_path: Path):
    """不纠拼写：断链 [[Atention]] 与 attention.md 无共享 token → 不建议（决策P3.11-3）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/attention.md", title="Attention", body="正文")
    _page(wiki, "concepts/ref.md", title="引用页", body="见 [[Atention]]")

    f = _broken(run_lint(wiki), "atention")  # link_stem 小写化
    assert f.suggestion is None
    assert "疑似已有页" not in f.detail


def test_no_suggestion_of_referencing_page(tmp_path: Path):
    """候选只看 stem/title、不扫正文：正文含 [[ghost]] 的引用页不被建议为目标（§0.1#1）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # 两张引用页正文都含 ghost，但其 stem/title 与 ghost 无 overlap。
    _page(wiki, "concepts/alpha.md", title="阿尔法", body="见 [[ghost]] ghost ghost")
    _page(wiki, "concepts/beta.md", title="贝塔", body="也见 [[ghost]] ghost")

    report = run_lint(wiki)
    me = [f for f in report.findings if f.kind == "lint.missing_entity"]
    assert len(me) == 1 and "[[ghost]]" in me[0].detail
    assert me[0].suggestion is None  # 引用页正文含 ghost 却不被建议
    assert all(
        f.suggestion is None for f in report.findings if f.kind == "lint.broken_link"
    )


def test_no_self_suggestion_for_own_broken_link(tmp_path: Path):
    """不把写有断链的页建议成它自己的解析目标（决策P3.11-4a，review CONFIRMED）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # 页自身 stem/title 与断链 target 高度重叠，但它正是该断链的源页 → 不得自荐；无第三方候选 → 无建议。
    _page(wiki, "concepts/多头注意力机制.md", title="多头注意力机制", body="见 [[多头注意力]]")

    f = _broken(run_lint(wiki), "多头注意力")
    assert f.suggestion is None
    assert "疑似已有页" not in f.detail


def test_missing_entity_excludes_referencing_pages_even_if_overlapping(tmp_path: Path):
    """缺失实体的建议排除**所有引用页**——即便引用页 title 与 target 高度重叠（决策P3.11-4a）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # 两页 title 都与 target 高度重叠且都引用它 → 都是引用页 → 都被排除；无第三方候选 → 无建议。
    # （若不排除引用页，p1/p2 会被高分自荐——正是 review 指出的反模式。）
    _page(wiki, "concepts/p1.md", title="多头注意力机制甲", body="见 [[多头注意力机制]]")
    _page(wiki, "concepts/p2.md", title="多头注意力机制乙", body="也见 [[多头注意力机制]]")

    me = [f for f in run_lint(wiki).findings if f.kind == "lint.missing_entity"]
    assert len(me) == 1
    assert me[0].suggestion is None


def test_suggestion_below_threshold_omitted(tmp_path: Path):
    """低于 Jaccard 阈值不建议：[[deep-learning-model]](3 token) vs deep.md(1) = 0.33 < 0.5。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/deep.md", title="Deep", body="正文")
    _page(wiki, "concepts/ref.md", title="引用页", body="见 [[deep-learning-model]]")

    assert _broken(run_lint(wiki), "deep-learning-model").suggestion is None


def test_missing_entity_also_gets_suggestion(tmp_path: Path):
    """缺失实体（≥2 页引用）同样附建议；结构化字段与 detail 一致。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/多头注意力.md", title="多头注意力", body="正文")
    _page(wiki, "concepts/A.md", title="甲", body="见 [[多头注意力机制]]")
    _page(wiki, "concepts/B.md", title="乙", body="也见 [[多头注意力机制]]")

    me = [f for f in run_lint(wiki).findings if f.kind == "lint.missing_entity"]
    assert len(me) == 1
    assert me[0].suggestion == "wiki/concepts/多头注意力.md"
    assert "疑似已有页" in me[0].detail


def test_json_byte_stable_when_no_suggestion(tmp_path: Path):
    """report_dict 丢 None：无建议 finding 的 JSON 无 suggestion 键、仍为三字段（决策P3.11-5）。"""
    from guanlan.pages import report_dict

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Lonely.md", body="孤儿 + 断链 [[毫不相干的词]]")  # 无候选

    report = run_lint(wiki)
    d = report_dict(
        ok=report.ok,
        pages_checked=report.pages_checked,
        items_key="findings",
        items=report.findings,
    )
    assert d["findings"], "本用例应有 finding"
    for item in d["findings"]:
        assert "suggestion" not in item
        assert set(item) == {"page", "kind", "detail"}


def test_json_includes_suggestion_when_present(tmp_path: Path):
    """有建议时 JSON 多出 suggestion 键（值为相对路径）。"""
    from guanlan.pages import report_dict

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/self-attention.md", title="Self-Attention", body="正文")
    _page(wiki, "concepts/ref.md", title="引用页", body="见 [[attention]]")

    report = run_lint(wiki)
    d = report_dict(
        ok=report.ok,
        pages_checked=report.pages_checked,
        items_key="findings",
        items=report.findings,
    )
    broken = [
        i
        for i in d["findings"]
        if i["kind"] == "lint.broken_link" and "[[attention]]" in i["detail"]
    ]
    assert len(broken) == 1
    assert broken[0]["suggestion"] == "wiki/concepts/self-attention.md"


def test_violation_json_unaffected_by_none_drop():
    """回归：report_dict 丢 None 不影响 Violation 序列化（三字段恒非 None，check 不变）。"""
    from guanlan.pages import Violation, report_dict

    items = [Violation("wiki/x.md", "frontmatter.missing_key", "缺 title")]
    d = report_dict(ok=False, pages_checked=1, items_key="violations", items=items)
    assert d["violations"] == [
        {"page": "wiki/x.md", "kind": "frontmatter.missing_key", "detail": "缺 title"}
    ]


def test_suggestion_deterministic_and_lint_avoids_heavy_paths(tmp_path: Path):
    """建议确定性可重放；lint 模块不引检索召回/语料/别名扫描（决策P3.11-2，只复用 tokenize）。"""
    import guanlan.lint as lint_mod

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/多头注意力.md", title="多头注意力", body="正文")
    _page(wiki, "concepts/ref.md", title="引用页", body="见 [[多头注意力机制]]")

    a = [(f.kind, f.page, f.detail, f.suggestion) for f in run_lint(wiki).findings]
    b = [(f.kind, f.page, f.detail, f.suggestion) for f in run_lint(wiki).findings]
    assert a == b  # 字节稳定、可重放
    for heavy in ("build_corpus", "search_pages", "alias_index"):
        assert not hasattr(lint_mod, heavy)
