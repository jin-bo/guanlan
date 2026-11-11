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
    monkeypatch.setattr(N, "normalize_by_toc", lambda lines: TocResult(lines, promoted=[]))
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
