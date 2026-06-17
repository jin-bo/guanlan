"""P3.1 别名解析测试（零 LLM，见 docs/P3.1-别名解析.md §7）。

覆盖：pages 解析归口（alias_index / link_target_stems / link_resolution_index）；check 校验
（类型 / 撞 stem / 重复 / 缺键合法）；graph 把 [[别名]] 边归到拥有页节点且不破 broken≡check 不变式；
lint 假断链/缺失实体消解；Web 站内 [[别名]] 联链（缺 web extra/markdown 时跳过该用例）。
"""

from pathlib import Path

import pytest

from guanlan.check import run_check
from guanlan.graph import build_graph
from guanlan.lint import run_lint
from guanlan.pages import alias_index, link_resolution_index, link_target_stems

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


def _kinds(result) -> set[str]:
    items = getattr(result, "violations", None)
    if items is None:
        items = result.findings
    return {i.kind for i in items}


# ───────────────────────── pages 解析归口 ─────────────────────────


def test_alias_index_normalizes_and_maps(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['大模型', '大语言模型', 'Large Language Model']")
    idx = alias_index(wiki)
    assert idx["大模型"] == "llm"
    assert idx["大语言模型"] == "llm"
    assert idx["large language model"] == "llm"  # link_stem 归一小写


def test_alias_index_only_content_pages_and_tolerant(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # 坏 frontmatter 页：容错跳过、不抛。
    (wiki / "concepts" / "Bad.md").parent.mkdir(parents=True, exist_ok=True)
    (wiki / "concepts" / "Bad.md").write_text("没有 frontmatter\n[[X]]\n", encoding="utf-8")
    _page(wiki, "concepts/LLM.md", aliases="['大模型']")
    idx = alias_index(wiki)
    assert idx == {"大模型": "llm"}


def test_alias_index_first_wins_deterministic(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/A.md", aliases="['共用名']")
    _page(wiki, "concepts/B.md", aliases="['共用名']")
    # iter_pages 稳定排序 → A 先到先得（真冲突另由 check 报 duplicate）。
    assert alias_index(wiki)["共用名"] == "a"


def test_link_target_stems_includes_aliases(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['大模型']")
    targets = link_target_stems(wiki)
    assert "llm" in targets and "大模型" in targets


def test_link_resolution_index_alias_to_owner_path_and_stem_priority(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['大模型']")
    ri = link_resolution_index(wiki)
    assert ri["大模型"] == "wiki/concepts/LLM.md"
    assert ri["llm"] == "wiki/concepts/LLM.md"  # 真 stem 仍在


def test_loaded_passthrough_equals_default(tmp_path: Path):
    """`build_graph` 透传已加载 `(path, meta)` 给 alias_index/_base/link_resolution_index，避免整库
    二次解析（性能改）。锁定「loaded 路径 ≡ 默认路径」：撞名 setdefault 先到先得、坏页跳过、fold 兜底
    任一处漂移都会悄悄改 graph 边/断链分类——此处直接对拍三层归口，默认路径用例覆盖不到 loaded 入参。
    """
    from guanlan.pages import _base_resolution_index, iter_pages, load_page

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['大模型', '大语言模型']")
    _page(wiki, "concepts/Attention.md", aliases="['注意力', 'multi_head_attention']")  # fold 兜底
    _page(wiki, "concepts/A.md", aliases="['共用名']")
    _page(wiki, "concepts/B.md", aliases="['共用名']")  # 撞名 → 先到先得
    (wiki / "concepts" / "Bad.md").write_text("没有 frontmatter\n", encoding="utf-8")  # 坏页两路都跳过

    # 复刻 build_graph 的加载口径：同 iter_pages 序、同 load_page 容错档。
    loaded = [(p, load_page(p)[0]) for p in iter_pages(wiki)]

    assert alias_index(wiki, loaded=loaded) == alias_index(wiki)
    assert _base_resolution_index(wiki, loaded=loaded) == _base_resolution_index(wiki)
    assert link_resolution_index(wiki, loaded=loaded) == link_resolution_index(wiki)
    # 撞名先到先得在 loaded 路径下仍确定（A 先于 B）。
    assert alias_index(wiki, loaded=loaded)["共用名"] == "a"


def test_check_broken_equiv_graph_on_loader_divergent_page(tmp_path: Path):
    """`run_check`（单次读盘性能改）复用已读文本建解析表时，**必须仍走容错档**（`load_page_text`），不可
    改用其逐页严格档 meta：严格档（纯 Python `SafeLoader`，供 unparsable 报错确定性）与容错档（libyaml
    `CSafeLoader`）对**「是否可解析」会分歧**（flow 序列里的字面 TAB：libyaml 收、纯 Python 抛）。若解析表
    误用严格 meta，这类页的别名会从 check 的解析表掉出、而 graph/heal/Web 仍用容错档得到它，当场破
    `check.wikilink.broken ≡ graph.broken` 不变式（决策P3.8-2）。

    端到端钉死该不变式而非某个绝对结果，故**与是否装 libyaml 无关、两环境皆有效**：A 页 frontmatter 的
    aliases flow 含字面 TAB 且声明别名 `张三`，B 页 `[[张三]]` 引用它——
      - 装 libyaml：容错档收 TAB → 别名入表 → check 与 graph **都判 B 解析**；
      - 未装 libyaml：容错档亦纯 Python → 别名不入表 → check 与 graph **都判 B 断链**。
    两环境下 check 对 B 的断链判定都必须与 graph **同判**；bug（解析表误用严格 meta）会让 check 在装 libyaml
    时单边判 B 断链、与 graph 分叉。A 的 `frontmatter.unparsable` 来自恒为纯 Python 的严格档，与环境无关。
    """
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # A：aliases flow 序列含字面 TAB —— 严格档抛、libyaml 收的分歧页。
    (wiki / "entities").mkdir(parents=True, exist_ok=True)
    (wiki / "entities" / "A.md").write_text(
        "---\ntitle: 'A'\ntype: entity\ntags: []\naliases: [张三,\tother]\n"
        "sources: []\nlast_updated: 2026-06-03\n---\n\n正文\n",
        encoding="utf-8",
    )
    _page(wiki, "entities/B.md", type="entity", body="见 [[张三]]")

    viols = {(v.page, v.kind) for v in run_check(wiki).violations}
    # A 严格档恒不可解析（纯 Python loader 锁定）→ 恒报 unparsable，与是否装 libyaml 无关。
    assert ("wiki/entities/A.md", "frontmatter.unparsable") in viols

    # 核心不变式：check 对 B 链接的断链判定 ≡ graph 的判定（两者共用容错档解析表）。
    g = build_graph(wiki)
    check_b_broken = ("wiki/entities/B.md", "wikilink.broken") in viols
    graph_b_broken = any(e.source == "b" and not e.resolved for e in g.edges)
    assert check_b_broken == graph_b_broken


# ───────────────────────── check 校验 ─────────────────────────


def test_alias_resolves_wikilink_not_broken(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['大模型']")
    _page(wiki, "entities/Foo.md", type="entity", body="见 [[大模型]]")
    result = run_check(wiki)
    assert result.ok
    assert "wikilink.broken" not in _kinds(result)


def test_missing_aliases_key_is_valid(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md")  # 不声明 aliases
    assert run_check(wiki).ok


def test_alias_bad_type_reported(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="'notalist'")  # 标量非列表
    result = run_check(wiki)
    assert not result.ok
    assert any(
        v.kind == "frontmatter.bad_type" and "aliases" in v.detail for v in result.violations
    )


def test_alias_empty_string_element_bad_type(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['', '大模型']")  # 含空串元素
    assert "frontmatter.bad_type" in _kinds(run_check(wiki))


def test_alias_collides_stem_reported_on_declaring_page(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['Foo']")  # 归一 'foo' 撞下面页面 stem
    _page(wiki, "entities/Foo.md", type="entity")
    result = run_check(wiki)
    collides = [v for v in result.violations if v.kind == "aliases.collides_stem"]
    assert collides and collides[0].page == "wiki/concepts/LLM.md"


def test_alias_duplicate_across_pages_reported_on_both(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/A.md", aliases="['共用名']")
    _page(wiki, "concepts/B.md", aliases="['共用名']")
    result = run_check(wiki)
    dup_pages = {v.page for v in result.violations if v.kind == "aliases.duplicate"}
    assert dup_pages == {"wiki/concepts/A.md", "wiki/concepts/B.md"}


# ───────────────────────── graph / lint ─────────────────────────


def test_graph_alias_edge_to_owner_no_phantom_node(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['大模型']")
    _page(wiki, "entities/Foo.md", type="entity", body="见 [[大模型]]")
    g = build_graph(wiki)
    assert g.broken == []
    assert ("foo", "llm") in {(e.source, e.target) for e in g.edges if e.resolved}
    assert "大模型" not in {n.id for n in g.nodes}  # 不建别名幽灵节点


def test_graph_broken_equiv_check_with_aliases(tmp_path: Path):
    """别名解析后，graph.broken 仍 ≡ check.wikilink.broken（决策P3.1-5 不变式回归）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['大模型']")
    _page(wiki, "entities/Foo.md", type="entity", body="[[大模型]] 与 [[不存在]]")
    g = build_graph(wiki)
    assert {e.target for e in g.broken} == {"不存在"}  # 别名不算断链
    chk = run_check(wiki)
    broken = [v for v in chk.violations if v.kind == "wikilink.broken"]
    assert len(broken) == 1 and "不存在" in broken[0].detail


def test_lint_alias_removes_broken_and_missing_entity(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['大模型']")
    _page(wiki, "entities/A.md", type="entity", body="[[大模型]]")
    _page(wiki, "entities/B.md", type="entity", body="[[大模型]]")  # 两页引用 → 否则会 missing_entity
    kinds = _kinds(run_lint(wiki))
    assert "lint.broken_link" not in kinds
    assert "lint.missing_entity" not in kinds


# ───────────────────────── Web 联链（需 markdown） ─────────────────────────


def test_web_alias_wikilink_links_but_code_does_not(tmp_path: Path):
    pytest.importorskip("markdown")
    from guanlan.web.render import render_markdown

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/LLM.md", aliases="['大模型']")
    html = render_markdown("见 [[大模型]] 与 `大模型`", wiki)
    # [[别名]] → 站内锚链；行内 code `别名` 不成链（兜底只认页面真名，决策P3.1-6）。
    assert 'data-page="wiki/concepts/LLM.md"' in html
    assert html.count("data-page=") == 1
