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


def test_strip_html_comments_recognizes_whole_line_comment_under_crlf_and_cr():
    """CRLF / 裸 CR 文本里的整行注释同样要被认出来。

    行锚定若用 `^`/`$`+MULTILINE（只认 `\\n`），CRLF 文本里 `-->` 与 `\\n` 之间隔着 `\\r`、`$`
    匹配不上，整行注释**当场失效**——`reindex --prune` 会把 CRLF 库的模板提示行当悬空项删掉。
    自 `read_text_verbatim` 上线后这些行尾会**真的**喂进本函数，不再有 `Path.read_text` 替它归一。
    """
    from guanlan.pages import strip_html_comments

    for eol in ("\r\n", "\r", "\n"):
        text = f"# 标题{eol}<!-- ingest 自动追加：- [<名称>](entities/<Name>.md) -->{eol}正文{eol}"
        out = strip_html_comments(text)
        assert "entities" not in out  # 注释被抹
        assert len(out) == len(text)  # 等长
        assert out.count(eol) == text.count(eol)  # 行分隔符逐字保留
        assert out.startswith(f"# 标题{eol}") and out.endswith(f"正文{eol}")
        # 跨行注释块同样识别
        block = f"# 标题{eol}<!--{eol}- [藏页](entities/Hidden.md){eol}-->{eol}尾{eol}"
        assert "entities" not in strip_html_comments(block)


def test_strip_html_comments_never_swallows_real_links_under_crlf():
    """**反向**（漏报守卫）：换成 CRLF 后，收窄规则不得反过来失效。

    正例（"注释被忽略了"）测不出漏报——漏报是沉默的。故这里摆真实的断链引用与真实的登记行，
    周围放会触发过滤的字面标记，断言它们**仍被扫到**。
    """
    from guanlan.pages import WIKILINK_RE, index_md_links, strip_html_comments

    for eol in ("\r\n", "\r"):
        # (a) 行内 code 里的字面标记跨段配对，不得吞掉中间的真引用
        body = eol.join(["注释以 `<!--` 开头。", "", "本页引用了 [[压根不存在的页]]。", "", "以 `-->` 结尾。"])
        assert WIKILINK_RE.findall(strip_html_comments(body)) == ["压根不存在的页"]
        # (b) 未闭合 `<!--` 不与后一段真注释的 `-->` 配对，中间的真登记行仍算收录项
        idx = eol.join([
            "## Entities", "", "<!-- TODO 稍后整理", "- [Foo](entities/Foo.md) — 一句话", "",
            "<!-- ingest 自动追加：- [<名称>](entities/<Name>.md) -->", "",
        ])
        assert "entities/Foo.md" in index_md_links(idx)
        # (c) 行内注释仍不算注释（CRLF 下也别把行尾备注当整行注释）
        inline = f"- [Bar](entities/Bar.md) <!-- 备注 -->{eol}"
        assert "entities/Bar.md" in index_md_links(inline)


def test_link_regexes_do_not_span_a_line_break():
    """`[[…]]` 与 `[文字](路径)` 都不得跨行粘连——`\\n` 与 `\\r` 都要排除。

    只排 `\\n` 的话，CRLF / 裸 CR 文本里 `\\r` 会被当普通字符，`[名](路径\\r更多)` 能匹配出一个
    含 `\\r` 的假目标，index↔磁盘同步据此判定就会错。
    """
    from guanlan.pages import WIKILINK_RE, index_md_links

    for eol in ("\r\n", "\r", "\n"):
        assert WIKILINK_RE.findall(f"[[前{eol}后]]") == []
        assert index_md_links(f"[文字](entities/A.md{eol}entities/B.md)") == set()
        # 正常同行链接照常识别（别把守卫写成"什么都不认"）
        assert index_md_links(f"- [A](entities/A.md){eol}- [B](entities/B.md)") == {
            "entities/A.md",
            "entities/B.md",
        }


# ---------- link_scan_text：代码里的 [[…]] 不算引用（§2.1，对应 llm_wiki 0013ca3） ----------
#
# 语义权威是渲染器自己写的那句：「围栏内本就不成链，扫描器也不该把代码示例算作引用」
# （`web/render.py`）。下面每条「不抹」的断言都对应一次实测的渲染器行为，别凭直觉改。


def test_link_scan_text_masks_fenced_and_inline_code():
    """围栏块 / 行内 code 里的 `[[…]]` 不再被扫成引用（幽灵断链的根因）。"""
    from guanlan.pages import WIKILINK_RE, link_scan_text

    for text in (
        '```python\ncols = df[["date","value"]]\n```',
        '~~~\n[[Foo]]\n~~~',
        '````\n[[Foo]]\n````',
        '```flint\n{"values": [[1,2]]}\n```',
        '行内 `df[["x"]]` 示例',
        '双反引号 ``x = [[Foo]]`` 示例',
    ):
        assert WIKILINK_RE.findall(link_scan_text(text)) == [], text


def test_link_scan_text_keeps_inline_code_that_is_exactly_one_wikilink():
    """`` `[[Foo]]` `` 是渲染器**有意**兜底成链接的形状，扫描器必须跟着认。

    抹掉它会造出反向漂移：页面上是链接、`check` 却不校验它，断链能静默上线。
    """
    from guanlan.pages import WIKILINK_RE, link_scan_text

    assert WIKILINK_RE.findall(link_scan_text("看 `[[Foo]]` 这个")) == ["Foo"]
    assert WIKILINK_RE.findall(link_scan_text("看 ` [[Foo]] ` 这个")) == ["Foo"]


def test_link_scan_text_preserves_length_lines_and_offsets():
    """长度、行数、列偏移三保——与 `strip_html_comments` 同契约（`reindex --prune` 逐行对齐依赖它）。"""
    from guanlan.pages import link_scan_text

    for text in (
        '```py\n[[Foo]]\n```\n后面 [[Bar]]\n',
        '```py\r\n[[Foo]]\r\n```\r\n后面 [[Bar]]\r\n',
        '```py\r[[Foo]]\r```\r后面 [[Bar]]\r',
        '行内 `x=[[Foo]]` 与 [[Bar]]\n',
    ):
        out = link_scan_text(text)
        assert len(out) == len(text)
        assert out.count("\n") == text.count("\n")
        assert out.count("\r") == text.count("\r")
        assert len(out.splitlines()) == len(text.splitlines())


# ---------- 漏报护栏：过滤规则最危险的方向是「本该报出却被吞掉」 ----------
#
# 正例（代码里的引用不再报）测不出漏报：抹多了同样"没有断链违规"。故每条收窄都配一条
# 反向用例，钉住「围栏/反引号附近的真引用必须活下来」。


def test_link_scan_text_never_swallows_links_outside_code():
    """围栏块前后的真引用一个都不能少。"""
    from guanlan.pages import WIKILINK_RE, link_scan_text

    text = '前面 [[Before]]\n\n```py\nx = [[InCode]]\n```\n\n后面 [[After]]\n'
    assert WIKILINK_RE.findall(link_scan_text(text)) == ["Before", "After"]


def test_link_scan_text_unclosed_fence_does_not_swallow_rest():
    """未闭合围栏**不抹**、不吃到文末——同 `strip_html_comments` 对未闭合 `<!--` 的处置。

    让一个落单的围栏吞掉后文全部链接，是把漏报伪装成通过。渲染器同样不认未闭合围栏（实测）。
    """
    from guanlan.pages import WIKILINK_RE, link_scan_text

    text = '```py\nx = 1\n\n后面 [[Real]] 还在\n'
    assert WIKILINK_RE.findall(link_scan_text(text)) == ["Real"]


def test_link_scan_text_asymmetric_fence_is_not_a_block():
    """开闭栏不对称一律当未闭合：CommonMark 与 python-markdown 在此分歧，取更严的那档。"""
    from guanlan.pages import WIKILINK_RE, link_scan_text

    assert WIKILINK_RE.findall(link_scan_text('```\n[[Foo]]\n`````')) == ["Foo"]  # 闭栏更长
    assert WIKILINK_RE.findall(link_scan_text('~~~\n[[Foo]]\n```\n[[Bar]]')) == ["Foo", "Bar"]  # 异字符


def test_link_scan_text_does_not_mask_indented_code_or_list_continuations():
    """缩进块**有意不抹**：列表项的缩进续行 / 嵌套项在渲染器里是正常成链的（实测）。

    裸 `^(?: {4}|\\t)` 分不开"缩进代码块"与"列表续行"，抹了就是把真链接静默吞掉。
    缩进围栏同理不抹（渲染器在缩进 1–3 上的行为随 info string 而变，无法对齐）。
    """
    from guanlan.pages import WIKILINK_RE, link_scan_text

    assert WIKILINK_RE.findall(link_scan_text("- 一项\n\n    续行 [[Foo]] 在这\n")) == ["Foo"]
    assert WIKILINK_RE.findall(link_scan_text("- 一项\n    - 子项 [[Foo]]\n")) == ["Foo"]
    assert WIKILINK_RE.findall(link_scan_text("   ```yaml\n   [[Foo]]\n   ```")) == ["Foo"]


def test_link_scan_text_unpaired_backtick_is_ordinary_text():
    """未配对的反引号 run 是普通文本，其后的真引用照常参与扫描。"""
    from guanlan.pages import WIKILINK_RE, link_scan_text

    assert WIKILINK_RE.findall(link_scan_text("单个 ` 反引号 [[Foo]] 后面")) == ["Foo"]
    assert WIKILINK_RE.findall(link_scan_text("``不配对 [[Foo]] 后面")) == ["Foo"]
    # 跨行的行内 code 不识别（两行各自当未配对 run）——同样是宁可多报。
    assert WIKILINK_RE.findall(link_scan_text("开头 `代码\n[[Foo]] 续行`")) == ["Foo"]


def test_link_scan_text_escape_only_when_backslash_count_is_odd():
    r"""`\[[X]]` 被转义（渲染器不成链），`\\[[X]]` 没有（渲染器成链）——两边都实测过。"""
    from guanlan.pages import WIKILINK_RE, link_scan_text

    assert WIKILINK_RE.findall(link_scan_text(r"转义 \[[Foo]] 不算")) == []
    assert WIKILINK_RE.findall(link_scan_text(r"转义 \\[[Foo]] 仍算")) == ["Foo"]


def test_link_scan_text_keeps_embed_shape_scannable():
    """`![[X]]` 在观澜里**仍是引用**：渲染器实测会成链，故不借 llm_wiki 的 `!` 跳过。

    观澜没有 Obsidian 嵌入语义（嵌图走 `![](路径)`，见 conventions §图片引用），
    照抄上游那条会凭空造出一类漏报。
    """
    from guanlan.pages import WIKILINK_RE, link_scan_text

    assert WIKILINK_RE.findall(link_scan_text("图 ![[Foo]] 在这")) == ["Foo"]


def test_link_scan_text_tab_after_fence_marker_is_still_a_fence():
    """开栏标记后跟 Tab 也是**合法开栏**——渲染器的 `normalize_whitespace`(30) 先于
    `fenced_code`(25) 把 Tab 展成空格（实测）。

    只认半角空格时，扫描器认不出这个开栏，转而把它的**闭栏**当成下一个开栏——奇偶整体错位一格，
    随后一整段正文被当代码抹掉。这不是多报，是**漏报**：实证里 `check` 对下面这页的真断链退 0。
    闭栏那侧本来就用 `.strip(" \t")` 认 Tab，两侧必须对称。
    """
    from guanlan.pages import WIKILINK_RE, link_scan_text

    text = "```\t\nx = 1\n```\n\n见 [[Real]]\n\n```\ny = 2\n```\n"
    assert WIKILINK_RE.findall(link_scan_text(text)) == ["Real"]


def test_link_scan_text_non_ascii_blank_line_is_not_a_block_boundary():
    """只含 NBSP / 全角空格的行**不是空行**——渲染器的 `(?<=\\n) +\\n` 只抹半角空格行。

    误当空行会提前重置"悬空反引号"状态（块边界），把渲染器明明成链的 `[[…]]` 当行内 code 抹掉。
    中文库里一行只打了个全角空格再常见不过，故这条是**实际会踩到的漏报**，不是理论边界。
    """
    from guanlan.pages import WIKILINK_RE, link_scan_text

    for blank in ("\u00a0", "\u3000"):
        text = f"a ` b\n{blank}\nc `x=[[Foo]]` d"
        assert WIKILINK_RE.findall(link_scan_text(text)) == ["Foo"], repr(blank)


def test_link_scan_text_splits_lines_the_way_markdown_does():
    """切行只认 `\\r\\n` / `\\r` / `\\n`：`str.splitlines` 多认的那几个字符不许切出围栏。

    拿 `splitlines` 切行，会在渲染器眼里的**一行中间**凭空切出开/闭栏，把那一行里的真链接抹掉
    （渲染器只把它们当普通字符，实测下面每个都照常成链）。PDF→markdown（P5.2 `convert`）
    产出的页里这些字符并不罕见。
    """
    from guanlan.pages import WIKILINK_RE, link_scan_text

    for sep in ("\u2028", "\u2029", "\x0b", "\x0c", "\x85", "\x1c", "\x1d", "\x1e"):
        text = f"a{sep}```{sep}[[Foo]]{sep}```"
        assert WIKILINK_RE.findall(link_scan_text(text)) == ["Foo"], repr(sep)


def test_link_scan_text_still_strips_whole_line_comments():
    """注释那半的老行为原样保留（顺序：注释在前、代码在后）。"""
    from guanlan.pages import WIKILINK_RE, link_scan_text

    assert WIKILINK_RE.findall(link_scan_text("<!-- [[Hidden]] -->\n[[Real]]")) == ["Real"]
    # 整行注释注掉一整段围栏：注释先抹，围栏开栏随之消失，后文不被当成代码。
    assert WIKILINK_RE.findall(link_scan_text("<!--\n```\n-->\n[[Real]]\n")) == ["Real"]
