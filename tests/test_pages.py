"""P3 共享页面原语测试（零 LLM，见 docs/P3-健康与图谱.md §11）。

抽取后行为不变是 P3 前置重构的硬约束：本文件对 frontmatter 切分、严格/容错两档解析、
link_stem、iter_pages、index_md_links 逐一断言，并与 P2 `check` 既有行为对齐（回归护栏）。
"""

from pathlib import Path

from guanlan.pages import (
    index_md_links,
    iter_pages,
    link_stem,
    link_target_stems,
    load_page,
    parse_frontmatter,
    split_frontmatter,
)

_GOOD_FM = "---\ntitle: 'T'\ntype: concept\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n"


# ---------- split_frontmatter ----------


def test_split_frontmatter_extracts_block_and_body():
    block, body = split_frontmatter(_GOOD_FM)
    assert block is not None and "title: 'T'" in block
    assert body.strip() == "正文"


def test_split_frontmatter_no_block():
    block, body = split_frontmatter("# 无 frontmatter\n正文\n")
    assert block is None
    assert body == "# 无 frontmatter\n正文\n"  # body 为全文


def test_split_frontmatter_unclosed_block_is_none():
    block, body = split_frontmatter("---\ntitle: X\n没有闭合\n正文\n")
    assert block is None  # 起始 --- 但无闭合 → 视作无合法块
    assert "没有闭合" in body


# ---------- parse_frontmatter（严格档） ----------


def test_parse_frontmatter_ok():
    block, _ = split_frontmatter(_GOOD_FM)
    meta, fatal = parse_frontmatter(block)
    assert fatal is None
    assert meta["type"] == "concept"


def test_parse_frontmatter_block_missing():
    meta, fatal = parse_frontmatter(None)
    assert meta is None
    assert fatal is not None and fatal.kind == "frontmatter.block_missing"


def test_parse_frontmatter_unparsable():
    meta, fatal = parse_frontmatter("title: 'X\ntype: [unclosed")  # 非法 YAML
    assert meta is None
    assert fatal is not None and fatal.kind == "frontmatter.unparsable"


def test_parse_frontmatter_non_mapping():
    meta, fatal = parse_frontmatter("- 我是列表\n- 不是映射")
    assert meta is None
    assert fatal is not None and fatal.kind == "frontmatter.unparsable"


# ---------- load_page（容错档，决策P3-8：绝不抛） ----------


def test_load_page_good(tmp_path: Path):
    p = tmp_path / "Foo.md"
    p.write_text(_GOOD_FM, encoding="utf-8")
    meta, body = load_page(p)
    assert meta is not None and meta["title"] == "T"
    assert body.strip() == "正文"


def test_load_page_missing_block_returns_none_meta(tmp_path: Path):
    p = tmp_path / "NoFm.md"
    p.write_text("# 只有标题\n正文\n", encoding="utf-8")
    meta, body = load_page(p)  # 不抛
    assert meta is None
    assert "正文" in body


def test_load_page_unparsable_returns_none_meta(tmp_path: Path):
    p = tmp_path / "Bad.md"
    p.write_text("---\ntitle: 'X\ntype: [unclosed\n---\n\n正文\n", encoding="utf-8")
    meta, body = load_page(p)  # 坏 YAML 也不抛
    assert meta is None
    assert "正文" in body


def test_load_page_non_mapping_returns_none_meta(tmp_path: Path):
    p = tmp_path / "List.md"
    p.write_text("---\n- a\n- b\n---\n\n正文\n", encoding="utf-8")
    meta, body = load_page(p)
    assert meta is None


# ---------- link_stem ----------


def test_link_stem_alias_anchor_md_suffix_and_case():
    assert link_stem("Foo") == "foo"
    assert link_stem("foo|别名") == "foo"
    assert link_stem("Foo#要点") == "foo"
    assert link_stem("entities/Foo.md") == "foo"
    assert link_stem("Foo.MD") == "foo"
    assert link_stem("  Foo  ") == "foo"


def test_link_stem_title_with_dot_keeps_full_stem():
    # 标题里含 . 但不以 .md 结尾，不该被当后缀剥掉（P2 回归用例同源）。
    assert link_stem("大语言模型3.5技术报告") == "大语言模型3.5技术报告"


# ---------- iter_pages / link_target_stems ----------


def _seed_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    (wiki / "entities").mkdir(parents=True)
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    (wiki / "entities" / "Foo.md").write_text(_GOOD_FM, encoding="utf-8")
    return wiki


def test_iter_pages_excludes_config(tmp_path: Path):
    wiki = _seed_wiki(tmp_path)
    names = {p.name for p in iter_pages(wiki)}
    assert names == {"Foo.md"}  # config 三页被排除


def test_iter_pages_subdir_named_like_config_not_excluded(tmp_path: Path):
    """只有 wiki/ 顶层的 index/log/overview 算 config；子目录同名文件仍是 content。"""
    wiki = _seed_wiki(tmp_path)
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "index.md").write_text(_GOOD_FM, encoding="utf-8")
    rels = {p.relative_to(wiki).as_posix() for p in iter_pages(wiki)}
    assert "concepts/index.md" in rels
    assert "index.md" not in rels


def test_link_target_stems_includes_config(tmp_path: Path):
    wiki = _seed_wiki(tmp_path)
    stems = link_target_stems(wiki)
    # 解析集含 config（index/log/overview 可作合法链接目标），与 iter_pages 排除相对照。
    assert {"foo", "index", "log", "overview"} <= stems


# ---------- index_md_links ----------


def test_index_md_links_parses_markdown_targets():
    text = (
        "## Overview\n- [总览](overview.md) — 活体综述\n\n"
        "## Sources\n- [甲](sources/jia.md) — 一句话\n"
    )
    assert index_md_links(text) == {"overview.md", "sources/jia.md"}


def test_index_md_links_strips_anchor_and_dot_slash():
    text = "- [x](./entities/Foo.md#要点) — y\n"
    assert index_md_links(text) == {"entities/Foo.md"}


def test_index_md_links_skips_external_and_pure_anchor():
    text = "- [外](https://example.com) — z\n- [节](#section) — w\n- [邮](mailto:a@b.c) — q\n"
    assert index_md_links(text) == set()


def test_index_md_links_does_not_eat_wikilinks():
    # [[wikilink]] 无 `](` 结构，不该被 markdown 链接解析误吃。
    assert index_md_links("正文里有 [[Foo]] 和 [[Bar|别名]]\n") == set()


def test_index_md_links_supports_balanced_parentheses():
    text = "- [示例页](entities/示例实体(分部).md) — 介绍\n"
    assert index_md_links(text) == {"entities/示例实体(分部).md"}


def test_index_md_links_malformed_unbalanced_paren_does_not_truncate():
    # 畸形链接（目标内未配对 `(`）：宁可整体不匹配，也不截出半截错目标当悬挂链接误报。
    # 旧惰性正则会截出 `a(b`；新正则要求 `(` 必起一对，未配对即整体不匹配。
    assert index_md_links("- [x](dir/a(b.md\n") == set()


def test_index_md_links_two_level_nesting_skips_rather_than_wrong_target():
    # 双层嵌套括号（极罕见）：整体不匹配（跳过），而非截出错目标 `dir/A(B(C)` 造成假悬挂。
    assert index_md_links("- [x](dir/A(B(C)).md)\n") == set()


# ---------- order_findings：finding 因果排序归口（gbrain §3，纯展示层） ----------


def test_order_findings_root_cause_before_effect():
    from guanlan.pages import Finding, order_findings

    # 乱序输入：果在前、因在后。
    inp = [
        Finding("a.md", "lint.broken_link", "x"),
        Finding("", "lint.missing_entity", "y"),
        Finding("b.md", "lint.orphan", "z"),
    ]
    out = [f.kind for f in order_findings(inp)]
    assert out == ["lint.missing_entity", "lint.broken_link", "lint.orphan"]


def test_order_findings_topology_sinks_last():
    from guanlan.pages import Finding, order_findings

    inp = [
        Finding("h.md", "lint.cut_vertex", ""),
        Finding("a.md", "lint.broken_link", ""),
        Finding("h.md", "lint.hub_node", ""),
    ]
    out = [f.kind for f in order_findings(inp)]
    assert out.index("lint.broken_link") < out.index("lint.hub_node") < out.index(
        "lint.cut_vertex"
    )


def test_order_findings_stable_within_kind_and_unknown_sinks():
    from guanlan.pages import Finding, order_findings

    # 同 kind 的相对顺序（A 前 B）须保留；未登记 kind 取末档、稳定排在已登记之后。
    inp = [
        Finding("B.md", "lint.broken_link", ""),
        Finding("x.md", "lint.unknown_future", ""),
        Finding("A.md", "lint.broken_link", ""),
        Finding("", "lint.missing_entity", ""),
    ]
    out = order_findings(inp)
    kinds = [f.kind for f in out]
    assert kinds[0] == "lint.missing_entity"
    # 两条 broken_link 保持输入相对序（B 在 A 前）。
    broken = [f.page for f in out if f.kind == "lint.broken_link"]
    assert broken == ["B.md", "A.md"]
    # 未登记 kind 排在所有已登记之后。
    assert kinds[-1] == "lint.unknown_future"


def test_order_findings_does_not_mutate_input():
    from guanlan.pages import Finding, order_findings

    inp = [
        Finding("a.md", "lint.broken_link", ""),
        Finding("", "lint.missing_entity", ""),
    ]
    snapshot = list(inp)
    order_findings(inp)
    assert inp == snapshot  # 返回新列表、不就地改


def test_order_findings_preserves_multiset():
    """核心不变量：重排只换序、**不增不减不去重**——输出是输入的一个排列（含重复项）。"""
    from collections import Counter

    from guanlan.pages import Finding, order_findings

    inp = [
        Finding("a.md", "lint.broken_link", "x"),
        Finding("", "lint.missing_entity", "y"),
        Finding("z.md", "lint.unknown_future", "u"),  # 未登记 kind 也须保留
        Finding("a.md", "lint.broken_link", "x"),  # 完全相同的重复项不得被去重
    ]
    out = order_findings(inp)
    assert Counter(out) == Counter(inp)  # 多重集相等（Finding 是 frozen dataclass、可哈希）
    assert len(out) == len(inp)



# ---------- HTML 注释剥离（各链接扫描器共用的口径） ----------


def test_strip_html_comments_preserves_length_and_line_count():
    """长度与行数**逐位守恒**——`reindex --prune` 按行号对齐原行与判定行，错位即错删。

    裸 `\\r` 那条是踩过的坑：早期实现统一补 `\\n`，它会与前一个 `\\r` 合成一个 CRLF 边界，
    行数凭空少一，`_prune_dangling` 的 strict zip 直接抛。
    """
    from guanlan.pages import strip_html_comments

    for text in [
        "a\n<!-- 注释 -->\nb\n",
        "a\n<!-- 跨\n三\n行 -->\nb\n",
        "a\r\n<!-- 跨\r\n行 -->\r\nb\r\n",
        "a\r<!--x\r-->\rb",  # 老式裸 \r 断行
        "<!-- 含\x0c罕见分隔符 -->\nb\n",
        "a <!--x --> b",
        "无注释\n",
    ]:
        out = strip_html_comments(text)
        assert len(out) == len(text), text  # 逐字符一一对应
        assert len(out.splitlines()) == len(text.splitlines()), text


def test_strip_html_comments_does_not_glue_tokens():
    """抹成等长空白而非删空：注释两侧不得被粘起来，凭空造出原文没有的链接。"""
    from guanlan.pages import WIKILINK_RE, index_md_links, strip_html_comments

    # `]` 与 `(` 被注释隔开 → 原文不是 markdown 链接，抹后也不该变成链接。
    assert index_md_links(strip_html_comments("- [名]<!--注-->(entities/Missing.md)")) == set()
    # `[[Mis<!--注-->sing]]` 不得粘成 `[[Missing]]`（那可能真解析到某页 → 幽灵边）。
    assert WIKILINK_RE.findall(strip_html_comments("见 [[Mis<!--注-->sing]]")) != ["Missing"]


def test_strip_html_comments_only_matches_whole_line_comments():
    """只认**独占整行**的注释：行中间的 `<!--` 永远不构成注释开头。

    这条收窄是评审逼出来的——行内标记一旦被当注释开头，正文/行内 code 里的字面 `<!--`
    就能与后文任意一个 `-->` 配对，把中间的真链接整段抹掉（`check` 于是对真断链退 0）。
    """
    from guanlan.pages import strip_html_comments

    # 整行注释 → 抹掉
    assert "隐藏" not in strip_html_comments("<!-- 隐藏 -->")
    assert "隐藏" not in strip_html_comments("   <!-- 隐藏 -->   ")
    assert "隐藏" not in strip_html_comments("<!--\n隐藏\n-->")
    # 行内注释 → 原样保留（其中的链接照常参与扫描：宁可多报，不可少报）
    for text in ["正文 <!-- 备注 --> 更多", "<!-- 甲 --> 见 [[真页]] <!-- 乙 -->"]:
        assert strip_html_comments(text) == text


def test_strip_html_comments_never_swallows_real_links():
    """**核心不变量**：任何写法都不得让真实链接凭空消失——漏报比误报危险得多。"""
    from guanlan.pages import WIKILINK_RE, index_md_links, strip_html_comments

    # (a) 行内 code 里的字面标记跨段配对（写文档讲注释写法时的常见形状）
    body = "注释以 `<!--` 开头。\n\n本页引用了 [[压根不存在的页]]。\n\n以 `-->` 结尾。"
    assert WIKILINK_RE.findall(strip_html_comments(body)) == ["压根不存在的页"]

    # (b) 未闭合的 `<!--` 与**后一段真注释**的 `-->` 配对（init 模板自带四段注释，触发面极大）
    idx = (
        "## Entities\n\n<!-- TODO 稍后整理\n- [Foo](entities/Foo.md) — 一句话\n\n"
        "<!-- ingest 自动追加：- [<名称>](entities/<Name>.md) -->\n"
    )
    assert "entities/Foo.md" in index_md_links(idx)


def test_strip_html_comments_does_not_reglue_via_downstream_strip():
    """紧贴 `[[…]]` / `(…)` 内缘的注释不得被识别——否则下游 `.strip()` 会把它重新粘成有效引用。"""
    from guanlan.pages import index_md_links, link_stem, strip_html_comments

    # `[[Foo<!--旧名-->]]` 一旦抹成 `[[Foo    ]]`，link_stem 的 strip 会得到 `foo` → 断链伪装成通过
    assert link_stem(strip_html_comments("[[Foo<!--旧名-->]]")[2:-2]) != "foo"
    # 同理链接目标内缘
    assert index_md_links(strip_html_comments("- [名](entities/Bar.md<!--备注-->)")) != {
        "entities/Bar.md"
    }


def test_strip_html_comments_leaves_unterminated_open():
    """未闭合 `<!--` 原样保留：吃到文末会把后文真链接静默吞掉，门禁宁多报不少报。"""
    from guanlan.pages import strip_html_comments

    text = "<!-- 忘了闭合\n后面还有 [[真页]]\n"
    assert strip_html_comments(text) == text
