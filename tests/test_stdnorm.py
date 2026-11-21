"""P4.30.1 国家 / 行业标准文件的整理。夹具全部是虚构的标准号、名称和条文，只模仿 GB/T 1.1 的版式。"""

from __future__ import annotations

from guanlan.normalize import normalize
from guanlan.stdnorm import STD_STRUCT_START_RE, detect_standard, preprocess
from guanlan.tocnorm import normalize_by_toc

COVER = """<!-- page 1 of 6 -->

ICS 01.234 A 00

GB

# 中华人民共和国国家标准

GB/T 12345—2020/ISO 99999:2019

代替 GB/T 12345—2010

# 示例实验室能力通用要求

# General requirements for example laboratories

(ISO 99999:2019, IDT)

2020-01-01 发布

2020-07-01 实施

示例市场监督管理总局 发布
"""

TOC = """<!-- page 2 of 6 -->

GB/T 12345—2020

## 目次

- 前言 …… I
- 1 范围 …… 1
- 2 规范性引用文件 …… 1
- 3 术语和定义 …… 1
- 4 通用要求 …… 2
- 4.1 公正性 …… 2
- 4.2 保密性 …… 2
- 附录A（资料性附录） 示例溯源 …… 3
- 参考文献 …… 4
"""

BODY = """<!-- page 3 of 6 -->

GB/T 12345—2020

# 前言

本标准按照示例规则起草。

<!-- page 4 of 6 -->

GB/T 12345—2020

# 示例实验室能力通用要求

## 1 范围

本标准规定了示例实验室的通用要求。

## 2 规范性引用文件

下列文件对于本文件的应用是必不可少的。

## 3 术语和定义

下列术语和定义适用于本文件。

## 3.1

## 示例性 exampleness

一种示例的性质。

## 3.2

## 样品 sample

用于示例的物品。

## 4 通用要求

### 4.1 公正性

#### 4.1.1 实验室应确保其示例活动公正，且不受任何不当压力的影响:

a) 第一项；

### 4.2 保密性

实验室应对示例信息保密。

<!-- page 5 of 6 -->

GB/T 12345—2020

# 附录A

# （资料性附录）

# 示例溯源

## A.1 总则

本附录给出示例溯源的信息。

<!-- page 6 of 6 -->

GB/T 12345—2020

## 参考文献

[1] 示例文献
"""

DOC = COVER + "\n" + TOC + "\n" + BODY


def _headings(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln.startswith("#")]


# ── 识别 ──────────────────────────────────────────────────────────────────────


def test_detect_cover_fields():
    info = detect_standard(DOC.split("\n"))
    assert info is not None
    assert info.code == "GB/T 12345—2020"
    assert info.name == "示例实验室能力通用要求"
    assert info.title == "GB/T 12345—2020 示例实验室能力通用要求"
    assert info.meta == {
        "code": "GB/T 12345—2020",
        "replaces": ["GB/T 12345—2010"],
        "adopts": "ISO 99999:2019, IDT",
        "issued": "2020-01-01",
        "effective": "2020-07-01",
    }


def test_detect_industry_codes():
    for code, cover in (
        ("SF/T 0999—2023", "司法鉴定技术规范"),
        ("GA/T 9999—2021", "中华人民共和国公共安全行业标准"),
        ("SF/Z JD0999001—2018", "司法鉴定技术规范"),
    ):
        text = f"<!-- page 1 of 3 -->\n\n{cover}\n\n{code}\n\n# 示例检验技术规范\n\n2021-01-01 发布\n\n<!-- page 2 of 3 -->\n\n正文"
        info = detect_standard(text.split("\n"))
        assert info is not None, code
        assert info.name == "示例检验技术规范"


def test_detect_two_line_name_joined():
    text = (
        "<!-- page 1 of 2 -->\n\nICS 07.140\n\nGB/T 12345—2020\n\n# 示例科学 某物质检验\n\n# 某色谱法\n\n"
        "# Forensic sciences—Example\n\n<!-- page 2 of 2 -->\n\n正文"
    )
    info = detect_standard(text.split("\n"))
    assert info is not None and info.name == "示例科学 某物质检验　某色谱法"


def test_not_detected_when_only_cited():
    """正文里引用标准号的普通文档、只有标准号没有封面特征的片段：不启用。"""
    cited = "<!-- page 1 of 2 -->\n\n# 某单位质量手册\n\n本手册依据 GB/T 12345—2020 编写。\n\n<!-- page 2 of 2 -->\n\n正文"
    assert detect_standard(cited.split("\n")) is None
    bare = "<!-- page 1 of 2 -->\n\nGB/T 12345—2020\n\n# 示例要求\n\n<!-- page 2 of 2 -->\n\n正文"
    assert detect_standard(bare.split("\n")) is None
    # 第二页才出现封面特征：只看第一页
    late = "<!-- page 1 of 2 -->\n\n# 汇编前言\n\n<!-- page 2 of 2 -->\n\nICS 01.234\n\nGB/T 12345—2020\n\n# 示例要求"
    assert detect_standard(late.split("\n")) is None


def test_detect_without_anchors_uses_first_lines():
    text = "ICS 01.234\n\nGB/T 12345—2020\n\n# 示例要求\n\n2020-01-01 发布"
    info = detect_standard(text.split("\n"))
    assert info is not None and info.name == "示例要求"


# ── 预处理 ────────────────────────────────────────────────────────────────────


def test_appendix_lines_merged():
    lines, ops = preprocess(BODY.split("\n"))
    assert "# 附录A （资料性附录） 示例溯源" in lines
    assert "## A.1 总则" in lines  # 紧跟的 A.1 不被吞进附录标题
    assert ("std_appendix", "附录A （资料性附录） 示例溯源") in ops


def test_appendix_reference_in_sentence_untouched():
    src = ["参见附录 A。", "", "附录内容另见说明"]
    lines, ops = preprocess(src)
    assert lines == src and not ops


def test_terms_merged_only_inside_terms_chapter():
    lines, _ops = preprocess(BODY.split("\n"))
    assert "## 3.1 示例性 exampleness" in lines
    assert "## 3.2 样品 sample" in lines
    outside = ["## 4 通用要求", "", "## 4.3", "", "## 示例", "", "正文"]
    assert preprocess(outside)[0] == outside


def test_untitled_clause_demoted_but_titled_kept():
    lines, ops = preprocess(BODY.split("\n"))
    assert "4.1.1 实验室应确保其示例活动公正，且不受任何不当压力的影响:" in lines
    assert "### 4.1 公正性" in lines
    assert any(k == "std_clause" for k, _ in ops)


def test_untitled_clause_in_toc_is_promoted_back():
    """漏报反例：目次里列出的条款即使形似无标题条，按目录定层级后仍是标题。"""
    toc = "## 目次\n\n" + "\n".join(f"- {n} 第{n}章 …… {n}" for n in range(1, 6)) + "\n- 5.1 实验室应确保: …… 5\n"
    body = "\n".join(f"## {n} 第{n}章\n\n正文" for n in range(1, 6)) + "\n\n### 5.1 实验室应确保:\n\n正文"
    lines, _ = preprocess((toc + "\n" + body).split("\n"))
    res = normalize_by_toc(lines, standard=True)
    assert res.applied
    assert "## 5.1 实验室应确保:" in res.lines


# ── 层级 ──────────────────────────────────────────────────────────────────────


def test_levels_appendix_and_fixed_elements_top():
    res = normalize(DOC, title_hint="x")
    heads = _headings(res.body)
    assert "# 前言" in heads
    assert "# 1 范围" in heads and "## 4.1 公正性" in heads
    assert "# 附录A （资料性附录） 示例溯源" in heads
    assert "## A.1 总则" in heads  # 与 4.1 同级
    assert "# 参考文献" in heads  # 不跟随上一条
    assert "## 3.1 示例性 exampleness" in heads


def test_frontmatter_title_standard_profile():
    res = normalize(DOC, title_hint="x")
    assert res.title == "GB/T 12345—2020 示例实验室能力通用要求"
    assert res.meta["standard"]["code"] == "GB/T 12345—2020"
    norm = res.meta["normalized"]
    assert norm["profile"] == "standard"
    assert norm["appendices_merged"] == 1 and norm["terms_merged"] == 2 and norm["clauses_demoted"] == 1
    assert norm["toc_applied"] is True


def test_title_fallback_logged():
    text = "<!-- page 1 of 2 -->\n\nICS 01.234\n\n# GB/T 12345—2020\n\n# General requirements\n\n<!-- page 2 of 2 -->\n\n正文"
    res = normalize(text, title_hint="样本")
    assert res.meta["normalized"]["profile"] == "standard"
    assert any(o.kind == "standard_title_fallback" for o in res.ops)
    assert res.title == "GB/T 12345—2020"  # 退回首个一级标题


def test_non_standard_document_unchanged_by_profile():
    """通用文档：不写 profile / standard；附录编号形式不认，A.1 照旧排在目录最深层之下（与原行为一致）。"""
    entries = ["第1章 章1 …… 1", "第1节 节1 …… 1"] + [f"第{n}章 章{n} …… {n}" for n in range(2, 6)] + ["附录A 资料 …… 9"]
    text = "# 某书\n\n## 目录\n\n" + "\n".join(f"- {e}" for e in entries) + "\n\n"
    text += "# 第1章 章1\n\n正文\n\n## 第1节 节1\n\n正文\n\n"
    text += "\n".join(f"# 第{n}章 章{n}\n\n正文" for n in range(2, 6)) + "\n\n# 附录A 资料\n\n## A.1 总则\n\n正文"
    res = normalize(text, title_hint="x")
    assert "profile" not in res.meta["normalized"] and "standard" not in res.meta
    heads = _headings(res.body)
    assert "# 附录A 资料" in heads  # 无编号条目跟随上一条（第5章，第 1 级）
    assert "### A.1 总则" in heads  # 认不出编号：目录最深层（2）之下


# ── 跨页接句 ──────────────────────────────────────────────────────────────────


def test_std_struct_markers():
    for s in ("5.2.1 实验室应", "A.3 证明", "a) 第一项", "b）第二项", "注 1：说明", "示例：", "表 1 示例", "图A.2 示意", "附录 B"):
        assert STD_STRUCT_START_RE.match(s), s
    for s in ("规定，", "实验室应", "15 倍的"):
        assert not STD_STRUCT_START_RE.match(s), s


def test_join_vetoed_for_clause_at_page_top_in_standard_only():
    page_break = "\n\n<!-- page 2 of 2 -->\n\n"
    std = COVER.replace("<!-- page 1 of 6 -->", "<!-- page 1 of 2 -->") + "\n实验室应满足下列要求" + page_break + "5.2.1 设备应经校准。"
    res = normalize(std, title_hint="x")
    assert "实验室应满足下列要求\n" in res.body and "5.2.1 设备应经校准。" in res.body
    plain = "<!-- page 1 of 2 -->\n\n# 某报告\n\n产量增长了" + page_break + "1.5 倍，创历史新高。"
    res2 = normalize(plain, title_hint="x")
    assert "产量增长了1.5 倍，" in res2.body  # 通用模式照接


def test_detected_when_cover_code_equals_running_header():
    """封面标准号行与每页页眉相同、又落在第一页页首窗口里：识别须在去噪之前，否则它先被当页眉删掉。"""
    pages = ["<!-- page 1 of 4 -->\n\n# 司 法 鉴 定 技 术 规 范\n\nSF/Z JD0999001— 2018\n\n# 示例鉴定技术规范\n\n2018-11-08 发布 2019-01-01 实施"]
    for n in range(2, 5):
        pages.append(f"<!-- page {n} of 4 -->\n\nSF/Z JD0999001— 2018\n\n第{n}页正文。\n\n{n}")
    res = normalize("\n\n".join(pages), title_hint="x")
    assert res.meta["normalized"].get("profile") == "standard"
    assert res.title == "SF/Z JD0999001—2018 示例鉴定技术规范"  # 破折号两侧的空白去掉


def test_review_fix_jjf_code_and_metrology_cover():
    """评审补漏：JJF / JJG 三字母代号不被拆成「JJ F」；「国家计量技术规范」是封面类别行，不进标准名称。"""
    from guanlan.stdnorm import detect_standard

    info = detect_standard(
        ["<!-- page 1 -->", "JJF 1069—2012", "中华人民共和国国家计量技术规范", "示例机构考核规范", "2012-03-02 发布", "2012-06-01 实施"]
    )
    assert info is not None
    assert info.code == "JJF 1069—2012"
    assert info.title == "JJF 1069—2012 示例机构考核规范"


def test_review_fix_appendix_marker_only_line():
    from guanlan.stdnorm import preprocess

    lines, _ops = preprocess(["附录A", "## ", "正文。"])
    assert lines[0] == "附录A"


def test_review_fix_terms_after_toc_entry():
    """目次里页码另起一行时，目次条目「3 术语和定义」不挡住正文那一章的术语合并。"""
    from guanlan.stdnorm import preprocess

    lines = ["目次", "3 术语和定义", "1", "4 通用要求", "2", "", "## 3 术语和定义", "3.1", "示例术语 example", "定义文字。", "## 4 通用要求"]
    out, ops = preprocess(lines)
    assert "3.1 示例术语 example" in out
    assert any(k == "std_term" for k, _ in ops)


def test_unnumbered_entry_after_appendix_follows_corrected_level():
    """附录改为第 1 级后，紧跟的无编号条目跟随改过之后的上一条（第 1 级），不沿用附录前的第 2 级。"""
    toc = ["1 范围 …… 1", "2 要求 …… 1", "2.1 甲 …… 1", "2.2 乙 …… 2", "附录A（资料性） 示例 …… 3", "补充说明 …… 4"]
    text = "## 目次\n\n" + "\n".join(f"- {e}" for e in toc) + "\n\n"
    text += "# 1 范围\n\n正文\n\n# 2 要求\n\n## 2.1 甲\n\n正文\n\n## 2.2 乙\n\n正文\n\n# 附录A（资料性） 示例\n\n正文\n\n# 补充说明\n\n正文"
    res = normalize_by_toc(text.split("\n"), standard=True)
    assert res.applied
    levels = {d["title"]: d["level"] for d in res.toc}
    assert levels["附录A（资料性） 示例"] == 1
    assert levels["补充说明"] == 1
