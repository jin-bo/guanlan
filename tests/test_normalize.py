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


def test_page_number_forms_removed():
    forms = ["15", "- 15 -", "第 15 页", "15/20", "xii"]
    pages = {no: f"{_filler(no)}\n\n{forms[no % len(forms)]}" for no in range(1, 11)}
    r = normalize(_book(pages, 20), title_hint="t")
    assert r.meta["normalized"]["page_numbers_removed"] == 10
    for f in ("- 15 -", "第 15 页", "15/20"):
        assert f not in r.body


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
    for removed in ("i", "iv", "xii", "XII", "IV", "xxxix"):
        assert _page_number_value(removed) == 0, removed
