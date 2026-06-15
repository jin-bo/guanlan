"""P3.8 链接归一测试（wikilink 解析期消变体，零 LLM，见 docs/P3.8-链接归一.md §3）。

覆盖：fold_stem/link_fold_stem 折叠规则（NFKC + casefold + `_`→`-`，最小集）；link_stem 语义不变；
resolve_owner 两段探针（精确优先 + fold 兜底）；单一解析表 link_resolution_index 的机械 variant 规则
（撞则不折叠）；四处调用点（check / graph / heal 回执 / Web）同口径；零正文改写 + 向后兼容。
"""

import unicodedata
from pathlib import Path

import pytest
from conftest import make_runner, write_page

from guanlan.check import run_check
from guanlan.graph import build_graph
from guanlan.heal import run_heal_result
from guanlan.pages import (
    _base_resolution_index,
    fold_stem,
    link_fold_stem,
    link_resolution_index,
    link_stem,
    resolve_owner,
)

FM = (
    "---\ntitle: '{title}'\ntype: {type}\ntags: []\n{aliases}"
    "sources: []\nlast_updated: 2026-06-03\n---\n\n{body}\n"
)


def _page(wiki: Path, rel: str, *, title="T", type="concept", aliases=None, body="正文") -> None:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    alias_line = "" if aliases is None else f"aliases: {aliases}\n"
    p.write_text(FM.format(title=title, type=type, aliases=alias_line, body=body), encoding="utf-8")


def _seed_config(wiki: Path) -> None:
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")


def _broken(result) -> list:
    return [v for v in result.violations if v.kind == "wikilink.broken"]


# ───────────────────────── fold_stem / link_fold_stem ─────────────────────────


def test_fold_stem_underscore_to_hyphen():
    assert fold_stem("multi_head_attention") == "multi-head-attention"


def test_fold_stem_casefold_stronger_than_lower():
    assert fold_stem("FOO") == "foo"
    # casefold 比 .lower() 更全：德文 ß → ss（.lower() 不变）。
    assert fold_stem("straße") == "strasse"
    assert "straße".lower() == "straße"  # 对照：lower 不折 ß


def test_fold_stem_nfkc_fullwidth_and_compat():
    assert fold_stem("ＡＢＣ") == "abc"  # 全角 → 半角
    assert fold_stem("ﬁle") == "file"  # fi 连字（U+FB01）→ file


def test_fold_stem_nfd_and_nfc_converge():
    nfc = "Café"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd  # 两种码点序列（组合记号 vs 预组合）
    assert fold_stem(nfc) == fold_stem(nfd) == "café"


def test_fold_stem_minimal_set_no_overreach():
    # 仅 `_`→`-` 一条字符替换：不折重复 `-`、不剥首尾 `-`（决策P3.8-1）。
    assert fold_stem("a--b") == "a--b"
    assert fold_stem("-x-") == "-x-"


def test_link_fold_stem_strips_then_folds():
    # link_fold_stem = fold_stem(link_stem(...))：先剥 .md/锚/管 + 取末段，再折叠。
    assert link_fold_stem("Multi_Head_Attention.md") == "multi-head-attention"
    assert link_fold_stem("path/to/Foo_Bar#anchor") == "foo-bar"


def test_link_stem_semantics_unchanged():
    # 关键约束：link_stem 一字不动——不折 `_`、不做 NFKC，仅 .lower()（决策P3.8-2）。
    assert link_stem("foo_bar") == "foo_bar"
    assert link_stem("ＡＢＣ") == "ａｂｃ"  # 仅小写，全角不归一


# ───────────────────────── resolve_owner 两段探针 ─────────────────────────


def test_resolve_owner_exact_first_then_fold(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/multi-head-attention.md")
    _page(wiki, "concepts/ABC.md")
    idx = link_resolution_index(wiki)
    # 精确不中、fold 兜底命中（`_` 变体 + 全角变体）。
    assert resolve_owner("multi_head_attention", idx) == "wiki/concepts/multi-head-attention.md"
    assert resolve_owner("ＡＢＣ", idx) == "wiki/concepts/ABC.md"
    # 精确命中优先（自身就在表里）。
    assert resolve_owner("ABC", idx) == "wiki/concepts/ABC.md"


def test_resolve_owner_miss_returns_none(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Foo.md")
    idx = link_resolution_index(wiki)
    assert resolve_owner("不存在", idx) is None


# ───────────────────────── 解析表机械 variant 规则 ─────────────────────────


def test_fold_variant_generated_for_underscore_page(tmp_path: Path):
    """下划线页 → 生成无歧义 dash variant 键（决策P3.8-4）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/foo_bar.md")
    idx = link_resolution_index(wiki)
    assert idx["foo_bar"] == "wiki/concepts/foo_bar.md"  # 精确键
    assert idx["foo-bar"] == "wiki/concepts/foo_bar.md"  # 新增 fold variant 键


def test_twin_pages_each_resolve_exactly(tmp_path: Path):
    """foo_bar.md + foo-bar.md 同存：各精确键各归各页、零串台（撞则不折叠，决策P3.8-6）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/foo_bar.md")
    _page(wiki, "concepts/foo-bar.md")
    idx = link_resolution_index(wiki)
    assert resolve_owner("foo_bar", idx) == "wiki/concepts/foo_bar.md"
    assert resolve_owner("foo-bar", idx) == "wiki/concepts/foo-bar.md"
    # 两页都不丢、check 不误报断链。
    _page(wiki, "entities/Ref.md", type="entity", body="[[foo_bar]] 与 [[foo-bar]]")
    assert _broken(run_check(wiki)) == []


def test_fold_collision_two_owners_drops_variant(tmp_path: Path):
    """两个不同 base 键折叠到同一 fold 键（且无该 fold 名页）→ 撞名、不生成 variant，
    歧义 fold 形保持断链（决策P3.8-4/6）。用全角别名构造第二个拥有者，不依赖文件系统大小写。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/foo_bar.md")  # base 键 foo_bar
    _page(wiki, "concepts/Bar.md", aliases="['ｆｏｏ_ｂａｒ']")  # 别名 base 键 ｆｏｏ_ｂａｒ（全角）
    idx = link_resolution_index(wiki)
    # foo_bar 与 ｆｏｏ_ｂａｒ 都折叠到 foo-bar，且无 foo-bar.md → 2 拥有者 → 不折叠。
    assert resolve_owner("foo-bar", idx) is None  # 歧义形保持断链、不猜
    # 各精确键仍各归各页。
    assert resolve_owner("foo_bar", idx) == "wiki/concepts/foo_bar.md"
    assert resolve_owner("ｆｏｏ_ｂａｒ", idx) == "wiki/concepts/Bar.md"


# ───────────────────────── 四处调用点同口径 ─────────────────────────


def test_check_fold_hit_not_broken(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/multi-head-attention.md")
    _page(wiki, "entities/Foo.md", type="entity", body="见 [[multi_head_attention]]")
    assert run_check(wiki).ok
    assert _broken(run_check(wiki)) == []


def test_graph_broken_equiv_check_with_fold(tmp_path: Path):
    """fold 命中后 graph.broken ≡ check.wikilink.broken 仍恒等（决策P3-6 / P3.8-3）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/multi-head-attention.md")
    _page(wiki, "entities/Foo.md", type="entity", body="[[multi_head_attention]] 与 [[不存在]]")
    g = build_graph(wiki)
    assert {e.target for e in g.broken} == {"不存在"}  # fold 命中不算断链
    # fold 命中 → resolved 边归到拥有页节点（不建 fold 幽灵节点）。
    assert ("foo", "multi-head-attention") in {(e.source, e.target) for e in g.edges if e.resolved}
    assert "multi_head_attention" not in {n.id for n in g.nodes}
    chk = run_check(wiki)
    broken = [v for v in chk.violations if v.kind == "wikilink.broken"]
    assert len(broken) == 1 and "不存在" in broken[0].detail


def test_graph_broken_equiv_check_combined(tmp_path: Path):
    """富场景同图回归：fold 命中 + 歧义 fold 丢弃→断链 + 别名 fold + config 丢弃 + 真断链，
    五类混在一页里仍保 `graph.broken ≡ check.wikilink.broken`（守 graph 边三分类重写后的红线）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/multi-head-attention.md")  # fold 目标
    _page(wiki, "concepts/MHA.md", aliases="['big_model']")  # 别名 fold 目标
    _page(wiki, "concepts/foo_bar.md")  # 与下方全角别名同折叠 → 歧义
    _page(wiki, "concepts/Bar.md", aliases="['ｆｏｏ_ｂａｒ']")  # 第二个 foo-bar 拥有者 → 撞名丢弃
    _page(
        wiki,
        "entities/Ref.md",
        type="entity",
        body=(
            "[[multi_head_attention]] [[big-model]] [[index]] [[foo-bar]] [[ghost]]"
        ),
    )
    g = build_graph(wiki)
    # 断链 = 歧义 fold 形（foo-bar，2 拥有者不折叠）+ 真缺失（ghost）；fold/别名命中与 config 均不算。
    assert {e.target for e in g.broken} == {"foo-bar", "ghost"}
    resolved = {(e.source, e.target) for e in g.edges if e.resolved}
    assert ("ref", "multi-head-attention") in resolved  # fold 命中
    assert ("ref", "mha") in resolved  # 别名 fold 命中，归到拥有页节点
    assert all(t not in {"index", "overview"} for _, t in resolved)  # config 不建边
    # check 与 graph 逐条对齐：同一 idx + resolve_owner 判据。
    chk = run_check(wiki)
    broken_raw = {v.detail.split("[[", 1)[1].split("]]", 1)[0] for v in _broken(chk)}
    assert broken_raw == {"foo-bar", "ghost"}
    assert len(_broken(chk)) == len(g.broken)


def test_alias_fold_hit(tmp_path: Path):
    """别名也吃 fold：alias `multi_head` 让 `[[multi-head]]` 命中拥有页（决策P3.8-4）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/MHA.md", aliases="['multi_head']")
    idx = link_resolution_index(wiki)
    assert resolve_owner("multi-head", idx) == "wiki/concepts/MHA.md"  # 别名 fold variant


def test_config_target_and_fold_neither_broken_nor_edge(tmp_path: Path):
    """指向 config 页（含其 fold 形）既不算断链也不建边（决策P3-6 兜底）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="entity", body="[[index]] 与 [[INDEX]] 与 [[overview]]")
    assert _broken(run_check(wiki)) == []
    g = build_graph(wiki)
    assert g.broken == []
    assert all(e.target not in {"index", "overview"} for e in g.edges)  # config 不建边


def test_heal_receipt_fold_hit_resolved(kb: Path):
    """heal 写后回执 fold 命中：w.target=multi_head_attention、Agent 建 multi-head-attention.md
    → receipt 判 resolved（不误报 still_broken，走 resolve_owner 而非 resolution.get，决策P3.8-3）。"""
    write_page(kb, "wiki/concepts/a.md", body="见 [[multi_head_attention]]")
    write_page(kb, "wiki/concepts/b.md", body="见 [[multi_head_attention]]")
    runner = make_runner(lambda root: write_page(root, "wiki/concepts/multi-head-attention.md"))
    run = run_heal_result(root=kb, limit=10, min_refs=2, runner=runner)
    [r] = run.result.receipts
    assert r.target == "multi_head_attention"
    assert r.status == "resolved"
    assert r.resolved_to == "wiki/concepts/multi-head-attention.md"
    assert r.created_path == "wiki/concepts/multi-head-attention.md"


def test_web_wikilink_folds_but_code_ref_does_not(tmp_path: Path):
    """Web：`[[foo_bar]]` 经 fold 命中 foo-bar.md 成站内锚链；行内 code `foo_bar` 不折叠、不成链
    （仅 _resolve_wikilink 走 fold，_code_ref_target 不接，决策P3.8-7 边界）。"""
    pytest.importorskip("markdown")
    from guanlan.web.render import render_markdown

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/foo-bar.md")
    html = render_markdown("见 [[foo_bar]] 与 `foo_bar`", wiki)
    assert 'data-page="wiki/concepts/foo-bar.md"' in html  # [[foo_bar]] fold 命中
    assert html.count("data-page=") == 1  # 行内 `foo_bar` 未成链


# ───────────────────────── 零正文改写 + 向后兼容 ─────────────────────────


def test_fold_resolution_no_body_rewrite(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/multi-head-attention.md")
    _page(wiki, "entities/Foo.md", type="entity", body="[[multi_head_attention]]")
    before = {p: p.read_bytes() for p in wiki.rglob("*.md")}
    run_check(wiki)
    build_graph(wiki)
    link_resolution_index(wiki)
    after = {p: p.read_bytes() for p in wiki.rglob("*.md")}
    assert before == after  # 全程纯解析期，不动一字


def test_no_fold_hit_index_equals_base(tmp_path: Path):
    """无 fold 命中的库：link_resolution_index 与 _base_resolution_index 逐字相等
    （variant 不污染、向后兼容，决策P3.8-3）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Foo.md")
    _page(wiki, "entities/Bar.md", type="entity")
    assert link_resolution_index(wiki) == _base_resolution_index(wiki)
