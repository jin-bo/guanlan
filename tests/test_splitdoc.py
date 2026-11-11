"""P4.30 §4 拆分（零 LLM）：阈值、按章/按大小/递归、切点、命名、元数据、目录索引、图片引用改写。"""

from __future__ import annotations

import yaml

from guanlan.normalize import count_chars
from guanlan.pages import split_frontmatter
from guanlan.splitdoc import (
    BASENAME_MAX_BYTES,
    CHAPTER_FLOOR,
    PART_MAX,
    SPLIT_MIN,
    render_index,
    render_part,
    rewrite_image_refs,
    split_document,
)


def _para(n_chars: int, tag: str = "文") -> str:
    """约 n_chars 个非空白字的一段（每 50 字一句号）。"""
    sent = (tag * 49) + "。"
    k, r = divmod(n_chars, 50)
    return sent * k + tag * r


def _section(title: str, n_chars: int, level: int = 2, para: int = 2_000) -> str:
    paras = []
    left = n_chars
    while left > 0:
        paras.append(_para(min(para, left)))
        left -= para
    return f"{'#' * level} {title}\n\n" + "\n\n".join(paras) + "\n\n"


def _assert_lossless(body: str, parts) -> None:
    assert "".join(p.body for p in parts) == body


def test_under_threshold_not_split():
    body = _section("第一章", SPLIT_MIN - 100)
    assert split_document(body, sample="样例") == []


def test_chapter_split_and_lossless():
    body = "# 书名\n\n" + "".join(_section(f"第{i}章 题{i}", 15_000) for i in range(1, 5))
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    assert len(parts) == 4
    assert all(p.method == "chapter" for p in parts)
    assert parts[0].stem == "样例-01-第1章-题1"
    assert parts[0].body.startswith("# 书名")  # 小前言并入第一章
    assert [p.path for p in parts] == [[f"第{i}章 题{i}"] for i in range(1, 5)]


def test_large_preamble_becomes_00_part():
    body = _para(CHAPTER_FLOOR + 500) + "\n\n" + "".join(_section(f"第{i}章", 15_000) for i in range(1, 4))
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    assert parts[0].stem == "样例-00-前言"


def test_all_chapters_small_falls_back_to_size():
    body = "".join(_section(f"第{i}节", 3_000) for i in range(1, 30))  # 87k，每节 < 1 万
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    assert all(p.method == "size" for p in parts)
    assert all(p.chars <= PART_MAX for p in parts)
    assert parts[0].stem == "样例-01"
    # 切点优先落在标题前
    assert all(p.body.startswith("## ") for p in parts)


def test_oversized_chapter_recurses_into_subheadings():
    ch2 = "## 第二章 大章\n\n" + "".join(_section(f"第{i}节 小节{i}", 15_000, level=3) for i in range(1, 5))
    body = _section("第一章 小章", 12_000) + ch2 + _section("第三章 末章", 12_000)
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    stems = [p.stem for p in parts]
    assert stems[0] == "样例-01-第一章-小章"
    assert stems[1] == "样例-02-第二章-大章-01-第1节-小节1"
    assert stems[-1] == "样例-03-第三章-末章"
    assert parts[1].path == ["第二章 大章", "第1节 小节1"]


def test_oversized_chapter_without_subheadings_sized():
    body = _section("第一章", 15_000) + _section("第二章", 95_000)
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    assert [p.stem for p in parts][:2] == ["样例-01-第一章", "样例-02-第二章-01"]
    assert all(p.chars <= PART_MAX for p in parts)
    assert parts[1].method == "size"


def test_adjacent_small_chapters_packed():
    smalls = "".join(_section(f"附{i}", 300) for i in range(1, 8))
    body = _section("第一章", 30_000) + smalls + _section("第二章", 30_000)
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    assert len(parts) == 2  # 小章不单独成份
    assert all(p.chars <= PART_MAX for p in parts)


def test_size_cut_never_inside_paragraph():
    paras = [_para(1_000, tag=chr(0x4E00 + i)) for i in range(100)]
    body = "\n\n".join(paras) + "\n"
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    for p in parts[1:]:
        assert p.body[0] != "\n" and (p.body.startswith(tuple(x[:5] for x in paras)))


def test_single_oversized_paragraph_cut_at_sentence_end():
    body = _para(SPLIT_MIN + PART_MAX) + "\n"
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    assert len(parts) >= 2
    assert parts[0].body.endswith("。")
    assert all(p.chars <= PART_MAX for p in parts)


def test_anchor_preferred_over_blank_and_pages_metadata():
    blocks = []
    for no in range(1, 101):
        blocks.append(f"<!-- page {no} of 100 -->\n\n{_para(500)}\n\n{_para(500)}\n")
    body = "\n".join(blocks)
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    for p in parts[1:]:
        assert p.body.startswith("<!-- page ")
    assert parts[0].pages[0] == 1
    assert parts[-1].pages[1] == 100
    assert parts[1].pages[0] == parts[0].pages[1] + 1


def test_heading_inside_fence_is_not_a_cut():
    fence = "```\n## 不是标题\n```\n\n"
    body = _section("第一章", 25_000) + fence + _section("第二章", 25_000)
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    assert not any(p.body.startswith("## 不是标题") for p in parts)


def test_oversized_fence_rendered_parts_stay_balanced():
    """超过 PART_MAX 的代码块只能在围栏内切：正文切片照旧无损，渲染出的每份围栏仍成对。"""
    code = "".join(f"# 代码注释{i}\n" for i in range(PART_MAX // 4))
    body = _section("第一章", 5_000) + "```\n" + code + "```\n\n" + _section("第二章", 5_000)
    parts = split_document(body, sample="样例")
    _assert_lossless(body, parts)
    assert len(parts) >= 2
    for p in parts:
        assert render_part(p, sample="样例", doc_meta={"title": "书"}).count("```") % 2 == 0


def test_filename_byte_limit_with_long_cjk_titles():
    """超长中文章名：basename 按 UTF-8 字节限 200（Linux 按字节计）。"""
    long = "很长的章节标题" * 20
    body = "".join(_section(f"第{i}章{long}", 15_000) for i in range(1, 4))
    parts = split_document(body, sample="样" * 80)
    for p in parts:
        assert len((p.stem + ".md").encode("utf-8")) <= BASENAME_MAX_BYTES
    assert len({p.stem for p in parts}) == len(parts)


def test_three_digit_numbers_when_over_99():
    body = "".join(_section(f"节{i}", 10_500) for i in range(1, 102))
    parts = split_document(body, sample="样例")
    assert parts[0].stem.startswith("样例-001-")
    assert sorted(p.stem for p in parts) == [p.stem for p in parts]  # 文件名排序 = 原文顺序


def test_render_part_metadata_chain_and_images():
    body = "".join(_section(f"第{i}章", 15_000) for i in range(1, 4))
    body = body.replace("## 第2章\n\n", "## 第2章\n\n![图](images/样例/样例-1.png) ![外](https://x/y.png)\n\n", 1)
    parts = split_document(body, sample="样例")
    doc_meta = {"title": "示例书", "origin": "uploads/a.pdf", "pages": 9, "chars": count_chars(body), "normalized": {}}
    texts = [render_part(p, sample="样例", doc_meta=doc_meta) for p in parts]
    metas = [yaml.safe_load(split_frontmatter(t)[0]) for t in texts]
    assert metas[0]["split"]["prev"] is None and metas[-1]["split"]["next"] is None
    assert [m["split"]["index"] for m in metas] == [1, 2, 3]
    assert all(m["split"]["total"] == 3 for m in metas)
    assert metas[1]["split"]["prev"] == parts[0].stem and metas[1]["split"]["next"] == parts[2].stem
    assert metas[1]["origin"] == "uploads/a.pdf"
    assert metas[1]["chars"] == parts[1].chars
    assert metas[1]["title"] == "示例书·第2章"
    assert "](../images/样例/样例-1.png)" in texts[1]
    assert "](https://x/y.png)" in texts[1]


def test_render_index_lists_all_parts():
    body = "".join(_section(f"第{i}章", 15_000) for i in range(1, 4))
    parts = split_document(body, sample="样例")
    idx = render_index(parts, sample="样例", doc_meta={"title": "示例书"})
    for p in parts:
        assert f"({p.stem}.md)" in idx or f"(<{p.stem}.md>)" in idx
    assert "共 3 份" in idx


def test_rewrite_image_refs_only_local():
    t = "![a](images/s/1.png) ![b](http://e/2.png) ![c](/abs/3.png)"
    assert rewrite_image_refs(t) == "![a](../images/s/1.png) ![b](http://e/2.png) ![c](/abs/3.png)"


def test_unicode_line_separators_do_not_split_markdown_lines():
    """U+2028 等不是 markdown 的换行：`a\u2028# x` 是一行正文，不能当成标题行来切。"""
    from guanlan.splitdoc import _units

    body = "前文\u2028# 不是标题\n下一行\n"
    units = _units(body)
    assert "".join(u.text for u in units) == body
    assert [u.text for u in units] == ["前文\u2028# 不是标题\n", "下一行\n"]


# ── #171 标题形状：多篇法规连排、Markdown 层级不一致 ─────────────────────────


def _law(title: str, title_level: int, ch_level: int, n_ch: int = 3, ch_chars: int = 6_000, tag: str = "文") -> str:
    out = f"{'#' * title_level} {title}\n\n"
    for c in range(1, n_ch + 1):
        out += f"{'#' * ch_level} 第{c}章 章名{c}\n\n" + "\n\n".join(_para(2_000, tag) for _ in range(ch_chars // 2_000)) + "\n\n"
    return out


def _owner(parts, tag: str) -> list[list[str]]:
    return [p.path for p in parts if tag in p.body]


def test_lower_leveled_doc_title_starts_its_own_part():
    """第三篇的标题被识别成 ##：不能并进第二篇（#171）。"""
    body = (
        _law("甲市物业管理条例", 1, 2, tag="甲")
        + _law("乙省消防安全办法", 1, 2, tag="乙")
        + _law("丙区档案管理规定", 2, 3, tag="丙")
    )
    parts = split_document(body, sample="s")
    _assert_lossless(body, parts)
    assert [p.path for p in parts] == [["甲市物业管理条例"], ["乙省消防安全办法"], ["丙区档案管理规定"]]
    assert _owner(parts, "丙") == [["丙区档案管理规定"]]
    assert _owner(parts, "乙") == [["乙省消防安全办法"]]


def test_higher_leveled_chapters_do_not_become_doc_parts():
    """乙篇的章被识别成 #（与文档标题同级）：只按文档标题切，章跟随本篇（#171）。"""
    body = (
        _law("甲市物业管理条例", 1, 2, ch_chars=14_000, tag="甲")
        + _law("乙省消防安全办法", 1, 1, ch_chars=14_000, tag="乙")
    )
    parts = split_document(body, sample="s")
    _assert_lossless(body, parts)
    assert {p.path[0] for p in parts} == {"甲市物业管理条例", "乙省消防安全办法"}
    assert all(p.path[0] == "甲市物业管理条例" for p in parts if "甲" * 10 in p.body)
    assert all(p.path[0] == "乙省消防安全办法" for p in parts if "乙" * 10 in p.body)
    # 下一层仍按章拆（含被识别成 # 的章），且本篇标题不会自成一章
    yi = [p.path for p in parts if p.path[0] == "乙省消防安全办法"]
    assert yi == [["乙省消防安全办法", "第1章 章名1"], ["乙省消防安全办法", "第2章 章名2"], ["乙省消防安全办法", "第3章 章名3"]]


def test_unrecognized_heading_shapes_keep_level_behavior():
    """认不出形状的标题（普通书的章节名）维持纯层级行为：低一级的标题不被提升。"""
    body = _section("引言", 15_000, level=1) + _section("研究背景", 15_000, level=1) + _section("讨论", 15_000, level=2)
    parts = split_document(body, sample="s")
    _assert_lossless(body, parts)
    assert [p.path for p in parts] == [["引言"], ["研究背景"]]


def test_nested_doc_shaped_subsections_are_not_promoted():
    """章下的「一般规定」「审查意见」形状像文档标题，却嵌在本篇章节之下：不能提升成文档切点（#171）。"""
    body = ""
    for title, tag in (("甲市物业管理条例", "甲"), ("乙省消防安全办法", "乙")):
        body += f"# {title}\n\n"
        for c in range(1, 4):
            body += f"## 第{c}章 章名{c}\n\n{_para(4_000, tag)}\n\n### 一般规定\n\n{_para(4_000, tag)}\n\n"
            body += f"### 审查意见\n\n{_para(4_000, tag)}\n\n"
    parts = split_document(body, sample="s")
    _assert_lossless(body, parts)
    assert [p.path for p in parts] == [["甲市物业管理条例"], ["乙省消防安全办法"]]
