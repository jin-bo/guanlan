"""#175 按目录规范化标题层级（零 LLM）：识别目录、按序定位、改写层级、把关与「目录优先」拆分。"""

from __future__ import annotations

import re

from guanlan.normalize import normalize
from guanlan.splitdoc import split_document
from guanlan.tocnorm import normalize_by_toc

OFFSET = 2  # 锚点页号 = 印刷页码 + OFFSET（前置页两页）

TOC = """\
## 目 录

## 一、基本规定

- (一）综合管理……(3)
- 1. 甲市物业管理条例（2001年3月18日)………(3)
- 2.乙省消防安全办法(试行)(2002年5月10日）……(5)
- 3.丙区档案管理规定(试行X2003年1月4日)……(7)
- 4.庚市(县)档案管理办法（2003年6月1日）……(8)
- (二)专项管理…(9)
- 1.丁县支付算办法（2004年9月9日）……(9)
- 2.戊市监管责任制（暂行)(2005年4月24日)……(11)
- 3.辛市统计管理规定
- (2006年1月2日)……(11)
二、实施细则

- 1. 己市实施细则……(13)
"""


def _filler(tag: str, n: int = 3_000) -> str:
    sent = tag * 29 + "。"
    return sent * (n // 30)


def _doc(heading: str, tag: str, n: int = 3_000) -> str:
    return f"{heading}\n\n## 第一章 总则\n\n{_filler(tag, n)}\n\n## 第二章 附则\n\n{_filler(tag, n)}\n\n"


def _book(n: int = 3_000) -> str:
    """封面 + 目录 + 正文：正文标题层级混乱（#/## 混用、中间层是普通文本、同名多处）。"""
    body = [
        "# 某某法规汇编\n\n<!-- page 1 -->\n\n",
        TOC,
        f"\n<!-- page {3 + OFFSET} -->\n\n## 一、基本规定\n\n(一)综合管理\n\n",
        _doc("# 1. 甲市物业管理条例", "甲", n),
        f"<!-- page {5 + OFFSET} -->\n\n",
        _doc("## 2. 乙省消防安全办法（试行）", "乙", n),
        f"<!-- page {7 + OFFSET} -->\n\n",
        _doc("# 3. 丙区档案管理规定（试行）", "丙", n),
        f"<!-- page {8 + OFFSET} -->\n\n",
        _doc("## 4. 庚市（县）档案管理办法", "庚", n),
        f"<!-- page {9 + OFFSET} -->\n\n(二)专项管理\n\n",
        _doc("## 1. 丁县支付结算办法", "丁", n),
        f"<!-- page {11 + OFFSET} -->\n\n",
        "# 2. 戊市监管责任制（暂行）\n\n# 关于印发《戊市监管责任制》（暂行）的通知\n\n各单位：现印发你们。\n\n",
        _doc("## 戊市监管责任制（暂行）", "戊", n),
        # 目录印的是第 11 页，实际在第 15 页（目录页码印错）：前后条目夹住、精确命中仍认
        f"<!-- page {15 + OFFSET} -->\n\n",
        _doc("# 3. 辛市统计管理规定", "辛", n),
        f"<!-- page {16 + OFFSET} -->\n\n二、实施细则\n\n",
        _doc("# 1. 己市实施细则", "己", n),
    ]
    return "".join(body)


def _strip_marks(line: str) -> str:
    return re.sub(r"^\s{0,3}#{1,6}\s+", "", line)


def _headings(body: str) -> list[str]:
    return [ln for ln in body.split("\n") if re.match(r"^#{1,6} ", ln)]


def test_levels_follow_toc():
    r = normalize(_book(), title_hint="t")
    s = r.summary
    assert (s["toc_entries"], s["toc_matched"], s["toc_applied"]) == (12, 12, True)
    hs = _headings(r.body)
    for want in (
        "# 一、基本规定",
        "## (一)综合管理",  # 普通文本补成标题
        "### 1. 甲市物业管理条例",
        "### 2. 乙省消防安全办法（试行）",
        "### 3. 丙区档案管理规定（试行）",  # 目录日期括注残缺
        "### 4. 庚市（县）档案管理办法",  # 全角半角混用
        "## (二)专项管理",
        "### 1. 丁县支付结算办法",  # 目录 OCR 少一字 → 模糊匹配
        "### 2. 戊市监管责任制（暂行）",
        "### 3. 辛市统计管理规定",  # 目录折行 + 页码印错
        "# 二、实施细则",
        "### 1. 己市实施细则",
    ):
        assert want in hs, want
    # 不在目录里的标题排到目录最深层（3）之下：章 = 4，其它 = 5
    assert "#### 第一章 总则" in hs
    assert "##### 关于印发《戊市监管责任制》（暂行）的通知" in hs
    # 同名多处：取编号一致的那一处，文内重复标题不提升
    assert "##### 戊市监管责任制（暂行）" in hs


def test_toc_region_and_cover_do_not_compete():
    r = normalize(_book(), title_hint="t")
    assert r.title == "某某法规汇编"  # 书名取自改层级之前的封面标题
    hs = _headings(r.body)
    assert "#### 某某法规汇编" in hs and "#### 目 录" in hs
    # 目录区里的「## 一、基本规定」降为正文；正文里的那一处才是第 1 级
    assert hs.count("# 一、基本规定") == 1
    toc_part = r.body[r.body.index("目 录") : r.body.index("1. 己市实施细则……")]
    assert not re.search(r"^#", toc_part.split("\n", 1)[1], re.M)


def test_only_heading_marks_change(monkeypatch):
    """与关掉按目录规范化的结果逐行比：只有行首标题标记不同（目录区的标题 / 列表标记保留原文）。"""
    import guanlan.normalize as N
    from guanlan.tocnorm import TocResult

    src = _book()
    after = normalize(src, title_hint="t").body.split("\n")
    monkeypatch.setattr(N, "normalize_by_toc", lambda lines, **kw: TocResult(lines, promoted=[]))
    before = normalize(src, title_hint="t").body.split("\n")
    assert len(after) == len(before) and after != before
    for a, b in zip(after, before):
        assert _strip_marks(a) == _strip_marks(b)


def test_split_paths_follow_toc():
    r = normalize(_book(4_000), title_hint="t")
    parts = split_document(r.body, sample="s", trust_levels=True)
    assert "".join(p.body for p in parts) == r.body
    for p in parts:
        assert p.path[0] in ("一、基本规定", "二、实施细则"), p.path
    owner = {tag: {tuple(p.path[:2]) for p in parts if tag * 20 in p.body} for tag in "甲丁己"}
    assert owner["甲"] == {("一、基本规定", "(一)综合管理")}
    assert owner["丁"] == {("一、基本规定", "(二)专项管理")}
    assert {o[0] for o in owner["己"]} == {"二、实施细则"}


def test_trusted_levels_pack_small_chapters_instead_of_size_split():
    """目录优先：各篇都 < 1 万字时按章装箱，不退回按大小拆（切出几百字的碎片）。"""
    r = normalize(_book(2_000), title_hint="t")
    trusted = split_document(r.body, sample="s", trust_levels=True)
    assert all(p.method == "chapter" for p in trusted)
    assert all(p.path for p in trusted)


def test_no_toc_is_noop():
    src = "# 标题\n\n<!-- page 1 -->\n\n正文。\n\n## 第一章\n\n更多正文。\n"
    r = normalize(src, title_hint="t")
    assert "toc_entries" not in r.summary
    assert normalize_by_toc(src.split("\n")).lines == src.split("\n")


def test_low_match_rate_leaves_levels_alone():
    """目录条目大多在正文里找不到 → 不动层级，记一条 toc_skipped。"""
    toc = "## 目录\n\n" + "".join(f"- {k}. 某某规定{k}……({k})\n" for k in range(1, 9))
    body = "\n# 1. 某某规定1\n\n正文。\n\n# 其它标题\n\n正文。\n"
    r = normalize(toc + body, title_hint="t")
    assert r.summary["toc_applied"] is False and r.summary["toc_matched"] == 1
    assert any(o.kind == "toc_skipped" for o in r.ops)
    assert "# 1. 某某规定1" in r.body and "# 其它标题" in r.body


def test_multilevel_numbers_and_spaced_numbers():
    """「1 范围」靠空格认编号；1.1 与 1.1.1 分级；目录漏列的 1.1.2 继承 1.1.1 的层级。"""
    toc = "## 目 次\n\n" + "".join(
        f"- {t}……({p})\n"
        for t, p in (("前言", 1), ("1 范围", 2), ("1.1 适用对象", 2), ("1.1.1 一般对象", 3), ("2 术语", 4), ("2.1 定义", 4))
    )
    body = (
        "\n<!-- page 3 -->\n\n## 前言\n\n正文。\n\n# 1 范围\n\n正文。\n\n# 1.1 适用对象\n\n正文。\n\n"
        "## 1.1.1 一般对象\n\n正文。\n\n## 1.1.2 特殊对象\n\n正文。\n\n<!-- page 5 -->\n\n"
        "## 2 术语\n\n正文。\n\n## 2.1 定义\n\n正文。\n\n## (1) 细目\n\n正文。\n"
    )
    r = normalize(toc + body, title_hint="t")
    assert r.summary["toc_applied"] is True
    hs = _headings(r.body)
    for want in ("# 前言", "# 1 范围", "## 1.1 适用对象", "### 1.1.1 一般对象", "### 1.1.2 特殊对象", "# 2 术语", "## 2.1 定义"):
        assert want in hs, want
    assert "#### (1) 细目" in hs


def test_in_document_sections_sharing_toc_marker_are_not_promoted():
    """汇编里各篇自己的「七、…」与目录第 1 级「一、…」同形：不能提成顶层（继承只限多级数字编号）。"""
    r = normalize(_book().replace("## 第二章 附则", "## 七、附则", 1), title_hint="t")
    hs = _headings(r.body)
    assert "# 七、附则" not in hs
    assert sum(1 for h in hs if h.startswith("# ")) == 2


# ── 规则解析补强：并行、图表目录、贴字页码 ─────────────────────────────────


def _thesis(toc: str) -> str:
    body = "\n<!-- page 3 -->\n\n## 摘要\n\n正文。\n\n## Abstract\n\n正文。\n\n<!-- page 5 -->\n\n# 引言\n\n正文。\n\n"
    for n in range(1, 5):
        body += f"<!-- page {4 + 2 * n} -->\n\n## {n} 第{n}部分\n\n正文。\n\n# {n}.1 小节{n}\n\n正文。\n\n"
    return "# 某某论文\n\n" + toc + body


def test_merged_toc_lines_figure_lists_and_glued_pages():
    toc = (
        "## 目录\n\n"
        "摘要....I Abstract....II 引言 1\n\n"  # 三条并成一行，罗马页码
        "1 第1部分……2\n\n1.1 小节1……2\n\n"
        "2 第2部分》4\n\n"  # 页码贴字（无引导点）——规则只认书名号/括号后的贴字页码，这一条当折行
        "2 第2部分 4\n\n2.1 小节2 4\n\n"  # 空白后的裸页码
        "3 第3部分……(6)\n\n3.1 小节3……(6)\n\n4 第4部分……8\n\n4.1 小节4……8\n\n"
        "图表目录图1.1 某某示意图……3\n\n表2-1 某某统计……5\n\n"  # 图表目录：不算条目
    )
    r = normalize(_thesis(toc), title_hint="t")
    assert r.summary["toc_applied"] is True
    titles = [e["title"] for e in r.toc]
    assert titles[:3] == ["摘要", "Abstract", "引言"]
    assert not any(t.startswith(("图", "表")) for t in titles)
    hs = _headings(r.body)
    assert "# 1 第1部分" in hs and "## 1.1 小节1" in hs and "# 4 第4部分" in hs


def test_glued_page_after_book_title_mark():
    toc = "## 目录\n\n" + "".join(f"- {k}.《某某规定{k}》{k * 2}\n" for k in range(1, 7))
    body = "".join(f"\n<!-- page {k * 2} -->\n\n## {k}.《某某规定{k}》\n\n正文。\n" for k in range(1, 7))
    r = normalize(toc + body, title_hint="t")
    assert r.summary["toc_applied"] is True and r.summary["toc_matched"] == 6
    assert [e["page"] for e in r.toc] == [2, 4, 6, 8, 10, 12]


# ── 模型审校（注入假审校函数；真调用见 tocreview） ─────────────────────────────


def _bad_toc_book() -> str:
    """规则解析只认出 3 条带页码的条目（不够 5 条，不成目录），需要审校才能用。"""
    toc = "## 目 录\n\n第一章 总则 第二章 管理 第三章 监督 第四章 责任……(9)\n\n第五章 附则……(12)\n\n"
    body = "".join(
        f"\n<!-- page {p} -->\n\n# 第{c}章 {t}\n\n正文{t}。\n"
        for p, c, t in ((3, "一", "总则"), (5, "二", "管理"), (7, "三", "监督"), (9, "四", "责任"), (12, "五", "附则"))
    )
    return "# 某某办法\n\n" + toc + body


_GOOD_REVIEW = [
    {"title": "第一章 总则", "page": 3, "level": 1},
    {"title": "第二章 管理", "page": 5, "level": 1},
    {"title": "第三章 监督", "page": 7, "level": 1},
    {"title": "第四章 责任", "page": 9, "level": 1},
    {"title": "第五章 附则", "page": 12, "level": 1},
]


def test_review_fixes_toc_rules_could_not_parse():
    seen = {}

    def reviewer(region: str, draft: list[dict]) -> list[dict]:
        seen["region"], seen["draft"] = region, draft
        return _GOOD_REVIEW

    r = normalize(_bad_toc_book(), title_hint="t", toc_reviewer=reviewer)
    assert "目 录" in seen["region"] and seen["draft"] == []  # 规则没认出目录：草稿为空，送审标题起的一段
    s = r.summary
    assert (s["toc_source"], s["toc_review"], s["toc_applied"], s["toc_matched"]) == ("llm", "ok", True, 5)
    assert r.title == "某某办法"


def test_review_rejected_when_titles_not_in_toc_text():
    """审校编造条目（目录原文里没有）→ 整份不用，沿用规则解析。"""

    def reviewer(region: str, draft: list[dict]) -> list[dict]:
        return _GOOD_REVIEW + [{"title": f"第{n}章 编造的章节", "page": 20, "level": 1} for n in "六七八"]

    r = normalize(_bad_toc_book(), title_hint="t", toc_reviewer=reviewer)
    assert r.summary.get("toc_review") == "rejected"
    assert "toc_applied" not in r.summary  # 规则本就没认出目录：与不送审一样
    assert any(o.kind == "toc_review" for o in r.ops)


def test_review_failure_falls_back_to_rules():
    def reviewer(region: str, draft: list[dict]) -> list[dict]:
        raise RuntimeError("没有配置模型")

    src = _book()
    r = normalize(src, title_hint="t", toc_reviewer=reviewer)
    s = r.summary
    assert (s["toc_source"], s["toc_review"], s["toc_applied"]) == ("rules", "failed", True)
    assert _headings(r.body) == _headings(normalize(src, title_hint="t").body)


def test_review_invalid_items_rejected():
    for bad in ([], "不是列表", [{"title": "第一章 总则", "page": 3, "level": 9}] * 6):
        r = normalize(_bad_toc_book(), title_hint="t", toc_reviewer=lambda region, draft, b=bad: b)
        assert r.summary.get("toc_review") == "rejected", bad


def test_review_cannot_change_body_text():
    """审校只给条目；正文仍只有标题标记会变。"""
    r = normalize(_bad_toc_book(), title_hint="t", toc_reviewer=lambda region, draft: _GOOD_REVIEW)
    assert r.summary["toc_applied"] is True
    before = normalize(_bad_toc_book(), title_hint="t").body.split("\n")
    after = r.body.split("\n")
    assert len(after) == len(before)
    assert [_strip_marks(x) for x in after] == [_strip_marks(x) for x in before]


def test_page_only_continuation_line_gives_page_to_previous_entry():
    """折行条目的页码单独一行「…………(440)」：挂到上一条，目录不在这里断开（866 页汇编实测）。"""
    toc = "## 目录\n\n" + "".join(f"- {k}.某某管理办法{k}（1997年1月{k}日）\n- …………({k * 2})\n" for k in range(1, 8))
    body = "".join(f"\n<!-- page {k * 2} -->\n\n## {k}. 某某管理办法{k}\n\n正文。\n" for k in range(1, 8))
    r = normalize(toc + body, title_hint="t")
    assert r.summary["toc_entries"] == 7 and r.summary["toc_applied"] is True
    assert [e["page"] for e in r.toc] == [2, 4, 6, 8, 10, 12, 14]


def test_code_fence_comments_untouched():
    """代码块里的 `# 注释` 不是标题：按目录改层级时一字不动（四个反引号的块里 ``` 不闭合）。"""
    fence = "````python\n# 甲市物业管理条例\n```\n## 第一章 总则\n````\n\n"
    book = _book().replace("## 第一章 总则\n\n", "## 第一章 总则\n\n" + fence, 1)
    r = normalize(book, title_hint="t")
    assert r.summary["toc_applied"]
    assert fence in r.body


def test_reviewed_toc_end_accepts_fuzzy_entries():
    """审校条目被模糊放行（目录原文末尾 OCR 多了字），按序对条目时也认，目录结尾不提前截断。"""
    from guanlan.tocnorm import _Entry, _reviewed_end

    lines = [
        "## 目录",
        "第一章 市场主体登记管理的一般规定……3",
        "第二章 市场主体的监督检查与行政处罚程序规则……9",  # 审校条目末尾抄成「等」：不是子串，只能模糊对上
        "第三章 附则……12",
        "",
        "# 第一章 市场主体登记管理的一般规定",
        "# 第二章 市场主体的监督检查与行政处罚程序",
        "# 第三章 附则",
    ]
    entries = [
        _Entry("第一章 市场主体登记管理的一般规定", 3),
        _Entry("第二章 市场主体的监督检查与行政处罚程序等", 9),
        _Entry("第三章 附则", 12),
    ]
    assert _reviewed_end(lines, 0, len(lines) - 1, entries) == 3
