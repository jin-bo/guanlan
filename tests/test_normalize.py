"""P4.30 §3 规范化整理（零 LLM）：去页码/页眉/水印、跨页接句、frontmatter。"""

from __future__ import annotations

import yaml

from guanlan.normalize import count_chars, normalize
from guanlan.pages import split_frontmatter

HEADER = "示例读本·生产篇"


def _book(pages: dict[int, str], total: int | None = None) -> str:
    out = ["# 示例读本", ""]
    for no in sorted(pages):
        anchor = f"<!-- page {no} of {total} -->" if total else f"<!-- page {no} -->"
        out += [anchor, "", pages[no], ""]
    return "\n".join(out)


def _body(text: str) -> str:
    return split_frontmatter(text)[1]


def _filler(no: int) -> str:
    return f"第{no}页的正文段落，讲述示例制度的具体要求。"


def _middle(no: int, line: str) -> str:
    """把 `line` 放在页中间：首尾各 3 个本页独有的段落，避开页首尾窗口。"""
    top = "\n\n".join(f"第{no}页开头第{k}段。" for k in range(3))
    bottom = "\n\n".join(f"第{no}页结尾第{k}段。" for k in range(3))
    return f"{top}\n\n{line}\n\n{bottom}"


# ── 跨页接句 ──────────────────────────────────────────────────────────────────


def test_cross_page_example_reproduced_verbatim():
    """§3.4 题目例子逐字复现：残句 10 字 > head 2 字 → 「规定，」挪到上一页。"""
    src = (
        "食品安全监管部门依法履行法定职责，食品生产者应按照法律\n\n"
        "<!-- page 24 of 539 -->\n\n"
        "规定，遵照相关制度要求，积极配合监管部门工作。重要的食品安全监管制度如下:\n"
    )
    r = normalize(src, title_hint="t")
    assert (
        "食品安全监管部门依法履行法定职责，食品生产者应按照法律规定，\n\n"
        "<!-- page 24 of 539 -->\n\n"
        "遵照相关制度要求，积极配合监管部门工作。重要的食品安全监管制度如下:\n"
    ) in r.body
    assert [o.kind for o in r.ops] == ["join"]


def test_short_residual_moves_after_anchor():
    src = "上一段完整结束。示例\n\n<!-- page 3 -->\n\n制度的具体条款需要逐项落实，否则无效。\n"
    r = normalize(src, title_hint="t")
    assert "上一段完整结束。\n\n<!-- page 3 -->\n\n示例制度的具体条款需要逐项落实，否则无效。" in r.body


def test_no_join_after_sentence_end_or_before_heading():
    src = "这一句已经结束。\n\n<!-- page 2 -->\n\n下一页新起一段。\n\n未完的句子\n\n<!-- page 3 -->\n\n## 标题\n"
    r = normalize(src, title_hint="t")
    assert not [o for o in r.ops if o.kind == "join"]


def test_latin_join_adds_space_and_hyphen_rejoins():
    src = "the quick brown\n\n<!-- page 2 -->\n\nfox jumps over, and more text here.\n\ninter-\n\n<!-- page 3 -->\n\nnational law applies.\n"
    r = normalize(src, title_hint="t")
    assert "the quick brown fox jumps over," in r.body
    assert "international law applies." in r.body


def _page_of(body: str, text: str) -> int | None:
    """text 所在的页（其前最近一个锚点的页号）。"""
    import re

    pos = body.index(text)
    nums = [int(m.group(1)) for m in re.finditer(r"<!-- page (\d+)", body[:pos])]
    return nums[-1] if nums else None


def test_no_join_standalone_page_end_text():
    """页末是落款 / 日期 / 未标 # 的章名 / 文号，下页另起：都不接，文本留在原页（#171）。"""
    cases = [
        ("本规定自发布之日起施行。\n\n某某管理局\n\n2023年5月1日", "关于印发某某办法的通知"),
        ("第一条 为了规范管理，制定本办法。\n\n第二章 管理职责", "第五条 主管部门负责本行政区域内的监督管理。"),
        ("文件编号 某发〔2023〕1号", "发布日期：2023年5月1日"),
        ("二〇二三年五月一日", "附件：某某申报表"),
    ]
    for tail, head in cases:
        src = f"{tail}\n\n<!-- page 2 -->\n\n{head}\n"
        r = normalize(src, title_hint="t")
        assert not [o for o in r.ops if o.kind == "join"], tail
        assert f"\n\n<!-- page 2 -->\n\n{head}\n" in r.body
        assert _page_of(r.body, tail.split("\n")[-1]) is None  # 仍在锚点之前（第 1 页）


def test_no_join_when_next_page_opens_field_or_clause():
    """上一页是长正文残句，但下一页以条款 / 列项 / 字段起头 → 不接（#171）。"""
    for head in ("第十二条 违反本办法的，依法处理。", "（一）建立健全管理制度；", "联系人：张某某"):
        src = f"本办法适用于本行政区域内依法设立的各类单位以及与其相关的组织和个人\n\n<!-- page 9 -->\n\n{head}\n"
        r = normalize(src, title_hint="t")
        assert not [o for o in r.ops if o.kind == "join"], head
        assert _page_of(r.body, head) == 9


def test_url_on_next_page_is_not_a_field():
    """下一页以 URL 起头不是「标签：」字段，照常接句（#171）。"""
    src = "更多信息请访问本局官方网站，网址为\n\n<!-- page 3 -->\n\nhttps://example.com 获取。\n"
    r = normalize(src, title_hint="t")
    assert [o for o in r.ops if o.kind == "join"]


def test_short_body_fragment_and_mid_sentence_refs_still_join():
    """页末只有一小段正文残句、句中引文号、续句以「附件」起头：都照接（#171 不能矫枉过正）。"""
    cases = [
        ("所谓单位是指依法设立并从事", "相关活动的组织。", "所谓单位是指依法设立并从事相关活动的组织。"),
        ("各地应当依据国发〔2023〕1号", "文件要求，落实相关工作。", "依据国发〔2023〕1号文件要求，"),
        ("具体名单详见", "附件所列清单中的单位。", "具体名单详见附件所列清单中的单位。"),
        ("资料可以从网址", "https://example.com 获取。", "资料可以从网址https://example.com 获取。"),
    ]
    for tail, head, joined in cases:
        r = normalize(f"{tail}\n\n<!-- page 2 -->\n\n{head}\n", title_hint="t")
        assert [o.kind for o in r.ops] == ["join"], tail
        assert joined in r.body, tail


def test_no_join_page_end_signature():
    """页末孤立一行是机关落款：不接（#171）。"""
    r = normalize("以上意见请认真贯彻执行。\n\n某某市人民政府\n\n<!-- page 2 -->\n\n抄送本市各区教育局\n", title_hint="t")
    assert not [o for o in r.ops if o.kind == "join"]


def test_body_continuation_still_joins_with_page_attribution():
    """正常正文续句照接：挪动的片段按规则归到对应页（#171 回归）。"""
    src = "本办法所称的单位，是指依法设立并从事\n\n<!-- page 5 -->\n\n相关经营活动的组织，包括分支机构。\n"
    r = normalize(src, title_hint="t")
    assert [o.rule for o in r.ops if o.kind == "join"] == ["后段前移"]
    assert "是指依法设立并从事相关经营活动的组织，\n\n<!-- page 5 -->\n\n包括分支机构。" in r.body
    assert _page_of(r.body, "相关经营活动") is None and _page_of(r.body, "包括分支机构") == 5


def test_join_after_noise_removed():
    """页码、页眉夹在被切开的句子之间：先去噪再接句，结果仍正确。"""
    pages = {}
    for no in range(1, 13):
        pages[no] = f"{HEADER}\n\n{_filler(no)}\n\n{no}"
    pages[6] = f"{HEADER}\n\n前文完整。食品生产者应按照法律\n\n6"
    pages[7] = f"{HEADER}\n\n规定，遵照相关制度要求。\n\n7"
    r = normalize(_book(pages, 12), title_hint="t")
    assert "食品生产者应按照法律规定，" in r.body
    assert HEADER not in r.body
    assert "\n7\n" not in r.body


# ── 页码 ─────────────────────────────────────────────────────────────────────


_ROMAN = ["", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv"]


def test_page_number_forms_removed():
    forms = ["{n}", "- {n} -", "第 {n} 页", "{n}/20", "{r}"]
    pages = {
        no: f"{_filler(no)}\n\n{forms[no % len(forms)].format(n=no + 4, r=_ROMAN[no + 4])}" for no in range(1, 11)
    }
    r = normalize(_book(pages, 20), title_hint="t")
    assert r.meta["normalized"]["page_numbers_removed"] == 10
    for f in ("- 10 -", "第 11 页", "12/20"):
        assert f not in r.body


def test_lone_edge_numbers_without_cross_page_evidence_kept():
    """页尾孤立的一个数字、列表项「- 12」凑不出跨页一致的偏移：证据不足，保留。"""
    pages = {no: _filler(no) for no in range(1, 11)}
    pages[4] += "\n\n42"
    pages[7] += "\n\n- 12"
    r = normalize(_book(pages, 300), title_hint="t")
    assert r.meta["normalized"]["page_numbers_removed"] == 0
    assert "42" in r.body and "- 12" in r.body  # 可能被跨页接句挪位，但内容不丢


def test_numbers_in_body_not_removed():
    """表格里的数字、正文中间的年份行、超出页数范围的孤立数字不当页码删。"""
    pages = {}
    for no in range(1, 8):
        pages[no] = f"{_filler(no)}\n\n第一段。\n\n第二段。\n\n第三段。\n\n2024\n\n第四段。\n\n第五段。\n\n第六段。"
    pages[8] = "| 年份 | 数量 |\n|---|---|\n| 2024 | 15 |\n\n2024"
    r = normalize(_book(pages, 8), title_hint="t")
    assert r.body.count("2024") == 9
    assert r.meta["normalized"]["page_numbers_removed"] == 0


# ── 页眉页脚 ─────────────────────────────────────────────────────────────────


def test_alternating_even_page_header_removed():
    """偶数页印书名（全书只占 50%，奇偶分计后达标）。"""
    pages = {no: (f"{HEADER}\n\n" if no % 2 == 0 else "") + _filler(no) for no in range(1, 13)}
    r = normalize(_book(pages, 12), title_hint="t")
    assert HEADER not in r.body
    assert r.meta["normalized"]["headers_removed"] == 6


def test_header_below_threshold_kept_underreport_guard():
    """漏报守卫：只在 2 页出现的行不删（< 3 页），而在 4 页出现的同类行被删——阈值改松改紧都能发现。"""
    pages = {no: _filler(no) for no in range(1, 13)}
    for no in (2, 9):
        pages[no] = "偶见的边注\n\n" + pages[no]
    for no in (2, 4, 6, 8):
        pages[no] = "常见页眉\n\n" + pages[no]
    r = normalize(_book(pages, 12), title_hint="t")
    assert r.body.count("偶见的边注") == 2
    assert "常见页眉" not in r.body


def test_heading_style_header_removed_but_real_heading_kept():
    """每页顶部 `## 书名` 当页眉删；首个同名标题（真实标题）与正文中间的同名标题保留。"""
    pages = {no: f"## 示例书名\n\n{_filler(no)}" for no in range(1, 11)}
    pages[5] += "\n\n中间段落。\n\n再一段。\n\n又一段。\n\n## 示例书名\n\n后续段落。\n\n结尾段。\n\n末段。"
    r = normalize(_book(pages, 10), title_hint="t")
    assert r.body.count("## 示例书名") == 2  # 第 1 页的首个 + 第 5 页正文中间
    assert any(o.kind == "heading_kept" for o in r.ops)


def test_repeated_legit_section_headings_kept():
    """「练习」「小结」这类合法重复标题不在页边稳定位置 → 保留，仍可参与拆分。"""
    pages = {}
    for no in range(1, 11):
        pages[no] = f"{_filler(no)}\n\n第二段。\n\n第三段。\n\n### 练习\n\n题目。\n\n第六段。\n\n第七段。"
    r = normalize(_book(pages, 10), title_hint="t")
    assert r.body.count("### 练习") == 10


def test_same_line_page_number_stripped_when_equal_to_anchor():
    pages = {no: f"{no}　{HEADER}\n\n{_filler(no)}" for no in range(1, 11)}
    r = normalize(_book(pages, 10), title_hint="t")
    assert HEADER not in r.body


def test_same_line_page_number_with_offset_kept():
    """印刷页码与锚点有偏移 → 不剥离、整行保留（首版不做偏移推断）。"""
    pages = {no: f"{no + 10}　仅出现一次的页眉{no}\n\n{_filler(no)}" for no in range(1, 11)}
    r = normalize(_book(pages, 10), title_hint="t")
    assert r.body.count("仅出现一次的页眉") == 10


def test_chapter_header_consecutive_run():
    """奇数页印章名：同奇偶连续 3 次删，只连续 2 次的保留。"""
    pages = {no: _filler(no) for no in range(1, 31)}
    for no in (3, 5, 7):
        pages[no] = "第一章 总则\n\n" + pages[no]
    for no in (21, 23):
        pages[no] = "第九章 附则\n\n" + pages[no]
    pages[13] = "第五章 罚则\n\n" + pages[13]
    pages[17] = "第五章 罚则\n\n" + pages[17]  # 不连续
    r = normalize(_book(pages, 30), title_hint="t")
    assert "第一章 总则" not in r.body
    assert r.body.count("第九章 附则") == 2
    assert r.body.count("第五章 罚则") == 2


# ── 水印 ─────────────────────────────────────────────────────────────────────


def test_scanner_watermark_removed():
    pages = {no: _middle(no, "扫描全能王 扫描创建") for no in range(1, 9)}
    r = normalize(_book(pages, 8), title_hint="t")
    assert "扫描全能王" not in r.body
    assert r.meta["normalized"]["watermarks_removed"] == 8


def test_generic_repeated_phrases_kept_and_logged_as_suspect():
    pages = {no: _middle(no, "仅供学习交流\n\n第二条 内容") for no in range(1, 9)}
    r = normalize(_book(pages, 8), title_hint="t")
    assert r.body.count("仅供学习交流") == 8
    assert r.body.count("第二条 内容") == 8
    assert any(o.kind == "watermark_suspect" and "仅供学习交流" in o.text for o in r.ops)


def test_watermark_inside_paragraph_or_structures_kept():
    pages = {no: _middle(no, "> 扫描全能王 扫描创建\n\n段内文字扫描全能王 扫描创建混在一起。") for no in range(1, 9)}
    r = normalize(_book(pages, 8), title_hint="t")
    assert r.body.count("扫描全能王") == 16


def test_watermark_under_threshold_kept():
    pages = {no: _middle(no, _filler(no)) for no in range(1, 13)}
    for no in (2, 7):
        pages[no] = _middle(no, "扫描全能王 扫描创建")
    r = normalize(_book(pages, 12), title_hint="t")
    assert r.body.count("扫描全能王") == 2


# ── frontmatter / 无锚点 ─────────────────────────────────────────────────────


def test_no_anchor_input_only_gets_frontmatter():
    src = "# 一份文档\n\n示例读本·生产篇\n\n正文。\n"
    r = normalize(src, title_hint="hint", origin="uploads/a.docx", parsed_by="python", source_sha256="ab")
    assert r.body == src
    meta = yaml.safe_load(split_frontmatter(r.text)[0])
    assert meta["title"] == "一份文档"
    assert meta["origin"] == "uploads/a.docx"
    assert meta["parsed_by"] == "python"
    assert meta["source_sha256"] == "ab"
    assert "pages" not in meta
    assert meta["chars"] == count_chars(src)
    assert meta["normalized"]["headers_removed"] == 0


def test_frontmatter_title_falls_back_to_hint_and_keeps_converter_keys():
    src = "---\nparsed_by: mineru\nextra: 1\n---\n正文 <!-- page 1 of 3 --> 无标题。\n"
    r = normalize(src, title_hint="文件名")
    assert r.meta["title"] == "文件名"
    assert r.meta["parsed_by"] == "mineru"
    assert r.meta["extra"] == 1


def test_count_chars_excludes_anchors_and_urls():
    body = "<!-- page 1 of 2 -->\n中文 ab\n![图](images/x/very-long-name.png) [链](http://example.com/long)\n"
    assert count_chars(body) == len("中文ab") + len("![图]") + len("[链]")


def test_consecutive_same_subheading_at_page_top_not_treated_as_chapter_header():
    """连续几页各自小节开头的同名小标题（标题形式，未达全书/奇偶门槛）不当章节页眉删。"""
    pages = {no: _filler(no) for no in range(1, 31)}
    for no in (3, 5, 7):
        pages[no] = "##### （一）适用范围\n\n" + pages[no]
    r = normalize(_book(pages, 30), title_hint="t")
    assert r.body.count("（一）适用范围") == 3


def test_chapter_run_rule_only_at_page_top():
    pages = {no: _filler(no) for no in range(1, 31)}
    for no in (3, 5, 7):
        pages[no] = _middle(no, pages[no]) + "\n\n年月日"  # 只落在页尾窗口
    r = normalize(_book(pages, 30), title_hint="t")
    assert r.body.count("年月日") == 3


def test_option_letters_and_roman_like_words_not_page_numbers():
    """试卷选项行「C」「D」、单个大写字母、像罗马数字的单词不当页码删；小写前言页码照删。"""
    from guanlan.normalize import _page_number_value

    for kept in ("C", "D", "M", "L", "I", "V", "X", "Mix", "DL", "CD"):
        assert _page_number_value(kept) is None, kept
    for form, value in (("i", 1), ("iv", 4), ("xii", 12), ("XII", 12), ("IV", 4), ("xxxix", 39), ("ix", 9)):
        assert _page_number_value(form) == value, form


def test_control_chars_cleaned_so_raw_admission_passes():
    from guanlan.rawio import check_text_admission

    text = "# 读本\n\n第一段\x0c第二段\x00。\x07\n"
    r = normalize(text, title_hint="t")
    check_text_admission(r.text)
    assert "第一段\n第二段。" in r.body
    assert r.meta["normalized"]["control_chars_removed"] == 3


def test_four_backtick_fence_not_closed_by_inner_three_backticks():
    """CommonMark：四反引号块里的 ``` 示例不闭合围栏，其后的 `# 注释` 仍在代码块里，不当标题。"""
    from guanlan.normalize import _classify, _first_h1, fence_closes

    body = "````md\n```python\n# 注释\n```\n````\n\n# 真标题\n"
    assert _first_h1(body) == "真标题"
    kinds = _classify(body.split("\n"))
    assert kinds[:5] == ["fence"] * 5 and kinds[6] == "heading"
    assert not fence_closes("```", "```python") and fence_closes("```", "````  ")


# ── 页边行号（草案逐页行号，test2 实测 NIST AI 800-2 ipd） ──────────────────────

DRAFT_HEADER = "NIST AI 800-2 ipd January 2026"
PARA = "This draft identifies practices for conducting automated benchmark evaluations of language models."


def _numbered_page(no: int, start: int, n: int = 8, header: str = DRAFT_HEADER) -> str:
    """一页草案：开头一列行号 start..start+n-1，接页眉，再接正文。"""
    nums = "\n".join(str(start + k) for k in range(n))
    return f"{nums}\n\n{header}\n\n第{no}页：{PARA}\n\n{PARA}"


def test_margin_line_number_blocks_removed_and_header_found_behind_them():
    """行号块删掉；它后面的页眉也能认出来（原先窗口被行号占满，页眉一处都认不出）。"""
    text = _book({no: _numbered_page(no, 1 + 30 * (no % 2)) for no in range(1, 7)}, total=6)
    r = normalize(text, title_hint="x")
    body = _body(r.text)
    assert DRAFT_HEADER not in body
    assert not any(ln.strip().isdigit() for ln in body.split("\n"))
    s = r.meta["normalized"]
    assert s["line_numbers_removed"] == 6 * 8 and s["headers_removed"] == 6
    assert body.count(PARA) == 12  # 正文一句没少


def test_line_number_block_needs_cross_page_evidence():
    """只有两页有一串数字：证据不足，保留（漏报是沉默的，这条守着「不该删的别删」）。"""
    pages = {no: f"第{no}页：{PARA}\n\n{PARA}" for no in range(1, 7)}
    pages[2] = _numbered_page(2, 1)
    pages[5] = _numbered_page(5, 1)
    body = _body(normalize(_book(pages, total=6), title_hint="x").text)
    assert "\n1\n2\n3\n4\n5\n" in body


def test_number_columns_that_are_not_line_numbers_kept():
    """文档确有行号块（规则已开启）时，表格抽成的不递增数字列、贴页尾但不足 5 行的递增串仍保留。"""
    pages = {}
    for no in range(1, 7):
        values = "\n".join(str(no * 100 + x) for x in (40, 7, 90, 3, 60, 21))  # 不递增；每页不同，不会被当页眉
        short = "\n".join(str(no * 100 + 50 + k) for k in range(4))  # 递增但只有 4 行，贴着页尾
        pages[no] = f"{_numbered_page(no, 1)}\n\n{values}\n\n{PARA}\n\n{short}"
    r = normalize(_book(pages, total=6), title_hint="x")
    body = _body(r.text)
    for no in range(1, 7):
        assert "\n".join(str(no * 100 + x) for x in (40, 7, 90, 3, 60, 21)) in body
        assert "\n".join(str(no * 100 + 50 + k) for k in range(4)) in body
    assert r.meta["normalized"]["line_numbers_removed"] == 6 * 8


def test_inline_line_numbers_stripped_only_with_block_evidence():
    """并进段落的行号：文档有页边行号块为证才删，且须连续递增 ≥4 个、相隔约一行。"""
    merged = (
        "2 This draft document identifies practices for conducting automated 3 benchmark evaluations of "
        "language models and similar models that output 4 text. Evaluations of these models are often embedded "
        "into systems 5 capable of functioning as chatbots and AI agents."
    )
    ordinary = "In 2019 there were 3 cases and 4 appeals across 5 districts, out of 6 regions surveyed in total."
    pages = {no: _numbered_page(no, 1) for no in range(1, 5)}
    pages[3] = _numbered_page(3, 1) + f"\n\n{merged}\n\n{ordinary}"
    body = _body(normalize(_book(pages, total=4), title_hint="x").text)
    assert "This draft document identifies practices for conducting automated benchmark evaluations of" in body
    assert " 3 benchmark" not in body and " 5 capable" not in body
    assert ordinary in body  # 相隔太近、不像逐行行号：不动

    # 没有行号块为证：同一段原样保留
    plain = {no: f"第{no}页：{PARA}" for no in range(1, 5)}
    plain[3] += f"\n\n{merged}"
    assert merged in _body(normalize(_book(plain, total=4), title_hint="x").text)


def test_page_footnote_html_becomes_blockquote_and_is_not_joined():
    """转换器的页脚注 HTML 压成引用块；跨页接句不会把下一页正文接到脚注后面。"""
    note = '<small><span class="docvortex-page-footnote" data-block-type="page_footnote" style="color:#6b7280">1 Terms defined in the Glossary are underlined</span></small>'
    text = _book({1: f"第一页正文没有说完的半句\n\n{note}", 2: "接着上一页的后半句。"}, total=2)
    r = normalize(text, title_hint="x")
    body = _body(r.text)
    assert "> 1 Terms defined in the Glossary are underlined" in body
    assert "<span" not in body and "underlined接着" not in body and "underlined 接着" not in body
    assert r.meta["normalized"]["footnotes_flattened"] == 1


def test_join_skips_page_end_footnotes():
    """页末有脚注时，跨页残句越过脚注接到正文末行；脚注原样留在页末（test2 实测「…斗 / 争。」）。"""
    note = '<small><span data-block-type="page_footnote">① 这是一条脚注。</span></small>'
    text = _book({1: f"这一页正文的最后一句话讲到一半就到了页末的斗\n\n{note}", 2: "争。下一页的正文继续往下讲。"}, total=2)
    body = _body(normalize(text, title_hint="x").text)
    assert "页末的斗争。" in body and "> ① 这是一条脚注。" in body
    assert "\n争。" not in body
