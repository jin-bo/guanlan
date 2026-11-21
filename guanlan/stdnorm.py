"""国家 / 行业标准文件的整理（P4.30.1，零 LLM、纯函数、不写盘）。

由 `normalize.normalize` 调用（识别在去噪之前，其余在去噪之后）：

1. **识别**（§1）：只看第一页（首个分页锚点到第二个锚点；无锚点取正文前 40 个非空行），标准号行与封面特征
   同时成立才判为标准文件。认不出就什么都不做（不出现半启用）。
2. **封面信息**（§2.1）：标准名称 → `title`（「标准号 标准名称」）；`standard:` 结构化字段只写从封面原文抽到的键。
3. **行级预处理**（§2.2、§2.4、§2.5），在按目录定层级之前做：附录多行标题合并、术语条目编号与术语合成一行、
   无标题条降为正文。
4. **接句否决**（§2.6）：`STD_STRUCT_START_RE` 给跨页接句多认几种标准的结构标记。

所有改动只动行首标记或把相邻几行合成一行，文字一字不改。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

__all__ = ["STD_STRUCT_START_RE", "StandardInfo", "detect_standard", "preprocess"]

_FIRST_PAGE_FALLBACK_LINES = 40  # 无分页锚点时，「第一页」取正文前这么多个非空行
_NAME_MAX_CHARS = 60  # 标准名称单行上限
_APPENDIX_TITLE_MAX = 40  # 附录名称上限
_APPENDIX_LOOKAHEAD = 3  # 附录标签后最多看几个非空行
_TERM_MAX_CHARS = 60  # 术语行上限

_ANCHOR_RE = re.compile(r"^\s*<!--\s*page\s+\d+")
_HEADING_MARK_RE = re.compile(r"^(\s{0,3}#{1,6})\s+")
_BOLD_RE = re.compile(r"\*\*|__")

# 标准代号 + 编号 + 年份；其后可跟「/ISO…」采标标识。全角破折号、半角连字符都认
_CODE_RE = re.compile(
    r"^(?P<code>(?:GB(?:/[TZ])?|GA(?:/T)?|SF/[TZ](?:\s*JD)?|RB/T|JJ[FG]|[A-Z]{2}(?:/[TZ])?)\s*[\d.]+\s*[—–\-]+\s*\d{4})"
    r"(?:\s*/\s*(?P<adopt>[A-Z][A-Za-z/ ]*\s*[\d.\-]+(?::\d{4})?))?\s*$"
)
_ICS_RE = re.compile(r"^ICS\s*\d{2}\.\d{3}")
# 封面类别行：「中华人民共和国国家标准」「…××行业标准」「…国家计量技术规范」「…国家职业卫生标准」等整行
_COVER_TITLE_RE = re.compile(r"^(?:中华人民共和国.{0,12}(?:标准|技术规范|指导性技术文件)|司法鉴定技术规范)$")
_ISSUED_RE = re.compile(r"(\d{4})\s*[-－]\s*(\d{1,2})\s*[-－]\s*(\d{1,2})\s*发布")
_EFFECTIVE_RE = re.compile(r"(\d{4})\s*[-－]\s*(\d{1,2})\s*[-－]\s*(\d{1,2})\s*实施")
_REPLACES_RE = re.compile(r"^代替\s*(.+)$")
_ADOPTS_RE = re.compile(r"^[(（]\s*((?:ISO|IEC|ITU|EN|IEEE|ASTM|OIML)[^,，)）]*?)\s*[,，]\s*(IDT|MOD|NEQ)\s*[)）]$")
_CJK_RE = re.compile(r"[一-鿿]")
_SENT_END = "。；：:;！？!?"

# 附录：标签行「附录A」，可带「（规范性附录）/（资料性）」
_APPENDIX_LABEL_RE = re.compile(r"^附录\s*([A-Z])\s*(?P<paren>[(（]\s*(?:规范性|资料性)(?:附录)?\s*[)）])?\s*$")
_APPENDIX_PAREN_RE = re.compile(r"^[(（]\s*(?:规范性|资料性)(?:附录)?\s*[)）]$")
# 条款编号：多级数字 / 附录条款
_NUM_ONLY_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3})+$")
_CLAUSE_RE = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3})+|[A-Z]\.\d{1,3}(?:\.\d{1,3})*)\.?\s*(?P<rest>.*)$")
_ANY_NUMBER_START_RE = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3})*|[A-Z]\.\d)")
_TERMS_CHAPTER_RE = re.compile(r"^(\d{1,2})\s*术语(?:和|与)?定义$")
_TOP_CLAUSE_RE = re.compile(r"^(\d{1,2})\s+(?!\d)\S")  # 单级编号 + 空白 + 非数字（「4 通用要求」，不含「4.1」）
_SHALL_RE = re.compile(r"应当|不应|应|宜|可")
_UNTITLED_MIN_CHARS = 20

# 跨页接句：标准模式下多认的页首结构标记（§2.6）。只做否决，不产生新的接合
STD_STRUCT_START_RE = re.compile(
    r"^(?:\d{1,3}(?:\.\d{1,3})+\.?\s|[A-Z]\.\d{1,3}|[a-z][)）]|\d{1,2}[)）]|注\s*\d*\s*[:：]|示例\s*\d*\s*[:：]"
    r"|[表图]\s*[A-Z]?\.?\d+|附录\s*[A-Z](?![A-Za-z]))"
)


@dataclass
class StandardInfo:
    code: str
    name: str = ""
    meta: dict = field(default_factory=dict)  # 写进 frontmatter 的 `standard:`

    @property
    def title(self) -> str:
        return f"{self.code} {self.name}" if self.name else ""


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _clean(line: str) -> str:
    s = _HEADING_MARK_RE.sub("", line)
    s = re.sub(r"^\s*(?:[-*+]\s+|>\s*)", "", s)
    return _BOLD_RE.sub("", s).strip()


def _first_page(lines: list[str]) -> list[str]:
    anchors = [i for i, ln in enumerate(lines) if _ANCHOR_RE.match(ln)]
    if anchors:
        hi = anchors[1] if len(anchors) > 1 else len(lines)
        return [ln for ln in lines[anchors[0] + 1 : hi] if ln.strip()]
    return [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("<!--")][:_FIRST_PAGE_FALLBACK_LINES]


def _code_match(text: str) -> re.Match[str] | None:
    return _CODE_RE.match(_nfkc(text).replace("　", " ").strip())


def _norm_code(raw: str) -> str:
    """标准号保留原件写法，只把代号与编号之间的空白规整为一个空格（「GB/T27025」→「GB/T 27025」）。"""
    m = re.match(r"^((?:JJ[FG]|[A-Z]{2}(?:/[TZ])?)(?:\s*JD)?)\s*(.+)$", raw.strip())
    if not m:
        return raw.strip()
    head = re.sub(r"\s*JD$", " JD", m.group(1))
    sep = "" if head.endswith("JD") else " "  # 「SF/Z JD0401002—2015」：JD 与编号之间不加空格
    tail = re.sub(r"\s*([—–\-－]+)\s*", r"\1", m.group(2).strip())  # 破折号两侧的空白是排版 / OCR 留下的
    return f"{head}{sep}{tail}"


def detect_standard(lines: list[str]) -> StandardInfo | None:
    """§1：第一页同时有标准号行与封面特征 → 标准文件；否则 None。"""
    page = [_clean(ln) for ln in _first_page(lines)]
    page = [p for p in page if p]
    code_idx = next((i for i, p in enumerate(page) if _code_match(p)), None)
    if code_idx is None:
        return None
    joined = "\n".join(page)
    cover = (
        any(_ICS_RE.match(_nfkc(p)) for p in page)
        or any(_COVER_TITLE_RE.match(re.sub(r"\s+", "", p)) for p in page)
        or (_ISSUED_RE.search(_nfkc(joined)) is not None and _EFFECTIVE_RE.search(_nfkc(joined)) is not None)
    )
    if not cover:
        return None
    m = _code_match(page[code_idx])
    assert m is not None
    # 标准号保留原件写法（全角破折号不改）：取原行到年份为止，去掉其后的「/ISO…」采标标识
    raw = re.match(r"^(.*?[—–\-－]+\s*[\d０-９]{4})", page[code_idx].strip())
    info = StandardInfo(code=_norm_code(raw.group(1) if raw else m.group("code")))
    info.name = _cover_name(page, code_idx)
    info.meta = _cover_meta(page, info.code, m.group("adopt"))
    return info


def _is_english(s: str) -> bool:
    letters = [ch for ch in s if ch.isalpha()]
    return bool(letters) and sum(1 for ch in letters if ch.isascii()) >= 0.6 * len(letters)


def _cover_name(page: list[str], code_idx: int) -> str:
    """标准号行（及「代替…」行）之后、第一个英文行 / 采标括注 / 发布日期行之前的中文标题行，按一行拼接。"""
    parts: list[str] = []
    for p in page[code_idx + 1 :]:
        q = _nfkc(p)
        if _REPLACES_RE.match(q.strip()) and not parts:
            continue
        if _is_english(p) or _ADOPTS_RE.match(q.strip()) or _ISSUED_RE.search(q) or _EFFECTIVE_RE.search(q):
            break
        if _code_match(p) or _COVER_TITLE_RE.match(re.sub(r"\s+", "", p)) or _ICS_RE.match(q):
            if parts:
                break
            continue
        if not _CJK_RE.search(p) or len(p) > _NAME_MAX_CHARS or p[-1] in _SENT_END:
            break
        parts.append(p.strip())
        if len(parts) >= 3:
            break
    return "　".join(parts)


def _date(m: re.Match[str] | None) -> str | None:
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _cover_meta(page: list[str], code: str, adopt: str | None) -> dict:
    """§2.1 决策P4.30.1-1：只写从封面原文抽到的键，抽不到就不写，不猜测。"""
    meta: dict = {"code": code}
    replaces: list[str] = []
    adopts = None
    for p in page:
        q = _nfkc(p).strip()
        m = _REPLACES_RE.match(q)
        if m:
            replaces += [_norm_code(x) for x in re.split(r"\s*[、,，;；]\s*", p.strip()[2:].strip()) if _code_match(x)]
        m2 = _ADOPTS_RE.match(q)
        if m2 and adopts is None:
            adopts = f"{m2.group(1).strip()}, {m2.group(2)}"
    if replaces:
        meta["replaces"] = replaces
    if adopts:
        meta["adopts"] = adopts
    elif adopt:
        meta["adopts"] = adopt.strip()
    joined = _nfkc("\n".join(page))
    issued, effective = _date(_ISSUED_RE.search(joined)), _date(_EFFECTIVE_RE.search(joined))
    if issued:
        meta["issued"] = issued
    if effective:
        meta["effective"] = effective
    return meta


# ── 行级预处理（§2.2 / §2.4 / §2.5） ────────────────────────────────────────


def _mark(line: str) -> str:
    m = _HEADING_MARK_RE.match(line)
    return m.group(1).strip() + " " if m else ""


def _is_structural(line: str) -> bool:
    """分页锚点、表格、列表、图片、HTML、引用：合并到此为止。"""
    s = line.strip()
    return bool(
        _ANCHOR_RE.match(s) or s.startswith(("|", "!", "<", ">")) or re.match(r"^(?:[-*+]|\d{1,3}[.)])\s+", s)
    )


def _next_nonblank(lines: list[str], i: int, limit: int) -> list[int]:
    out: list[int] = []
    k = i + 1
    while k < len(lines) and len(out) < limit:
        if lines[k].strip():
            out.append(k)
        k += 1
    return out


def _merge_appendices(lines: list[str], ops: list[tuple[str, str]]) -> list[str]:
    drop: set[int] = set()
    for i, line in enumerate(lines):
        if i in drop or _is_structural(line):
            continue
        label = _clean(line)
        m = _APPENDIX_LABEL_RE.match(_nfkc(label))
        if not m:
            continue
        parts = [label]
        has_paren = m.group("paren") is not None
        has_title = False
        for k in _next_nonblank(lines, i, _APPENDIX_LOOKAHEAD):
            if _is_structural(lines[k]):
                break
            s = _clean(lines[k])
            if not s:  # 只剩标记的行（「## 」「****」）：合并到此为止
                break
            if not has_paren and not has_title and _APPENDIX_PAREN_RE.match(_nfkc(s)):
                parts.append(s)
                has_paren = True
                drop.add(k)
                continue
            if (
                not has_title
                and len(s) <= _APPENDIX_TITLE_MAX
                and s[-1] not in _SENT_END
                and not _ANY_NUMBER_START_RE.match(_nfkc(s))
                and not _APPENDIX_LABEL_RE.match(_nfkc(s))
            ):
                parts.append(s)
                has_title = True
                drop.add(k)
            break
        if len(parts) > 1:
            lines[i] = _mark(line) + " ".join(parts)
            ops.append(("std_appendix", lines[i].lstrip("# ").strip()))
    return [ln for n, ln in enumerate(lines) if n not in drop]


def _terms_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """「N 术语和定义」这一章的行范围：到下一个顶层条款（编号不同的单级数字）/ 附录 / 参考文献为止。
    起止行须是标题行或短的独立行，正文里提到「术语和定义」的句子不算。
    可能不止一处：目次里页码另起一行时，目次条目「3 术语和定义」本身也像章标题——全部返回，正文那一章不被它挡住。"""
    out: list[tuple[int, int]] = []
    start: int | None = None
    top = 0
    for i, line in enumerate(lines):
        s = _nfkc(_clean(line))
        if not s:
            continue
        short = bool(_mark(line)) or len(s) <= 30
        if start is None:
            m = _TERMS_CHAPTER_RE.match(re.sub(r"\s+", "", s))
            if m and short:
                start, top = i, int(m.group(1))
            continue
        m2 = _TOP_CLAUSE_RE.match(s)
        ends = (m2 is not None and int(m2.group(1)) != top) or re.match(r"^(?:附录\s*[A-Z]|参考文献|索引)", s)
        if ends and short:
            out.append((start, i))
            start = None
            m = _TERMS_CHAPTER_RE.match(re.sub(r"\s+", "", s))
            if m:  # 结束行本身就是下一处「术语和定义」
                start, top = i, int(m.group(1))
    if start is not None:
        out.append((start, len(lines)))
    return out


def _merge_terms(lines: list[str], ops: list[tuple[str, str]]) -> list[str]:
    drop: set[int] = set()
    for lo, hi in _terms_ranges(lines):
        _merge_terms_in(lines, lo, hi, drop, ops)
    return [ln for n, ln in enumerate(lines) if n not in drop]


def _merge_terms_in(lines: list[str], lo: int, hi: int, drop: set[int], ops: list[tuple[str, str]]) -> None:
    for i in range(lo + 1, hi):
        if i in drop:
            continue
        num = _nfkc(_clean(lines[i]))
        if not _NUM_ONLY_RE.match(num):
            continue
        nxt = _next_nonblank(lines, i, 1)
        if not nxt or nxt[0] >= hi or _is_structural(lines[nxt[0]]):
            continue
        k = nxt[0]
        term = _clean(lines[k])
        if not term or len(term) > _TERM_MAX_CHARS or term[-1] in _SENT_END or _ANY_NUMBER_START_RE.match(_nfkc(term)):
            continue
        lines[i] = _mark(lines[i]) + f"{_clean(lines[i])} {term}"
        drop.add(k)
        ops.append(("std_term", f"{_clean(lines[i])}"))


def _demote_untitled(lines: list[str], ops: list[tuple[str, str]]) -> list[str]:
    """无标题条：编号后直接是要求的标题行 → 去标题标记作正文（目次里有的，按目录定层级时会再提回标题）。"""
    for i, line in enumerate(lines):
        if not _HEADING_MARK_RE.match(line):
            continue
        s = _clean(line)
        m = _CLAUSE_RE.match(_nfkc(s))
        if not m:
            continue
        rest = m.group("rest").strip()
        if not rest:
            continue
        if rest[-1] in "：:。；;" or (_SHALL_RE.search(rest) and len(rest) > _UNTITLED_MIN_CHARS):
            lines[i] = s
            ops.append(("std_clause", s[:40]))
    return lines


def preprocess(lines: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """§2.2 / §2.4 / §2.5 的行级预处理。返回 (新行, [(操作种类, 文本)])。"""
    ops: list[tuple[str, str]] = []
    lines = list(lines)
    lines = _merge_appendices(lines, ops)
    lines = _merge_terms(lines, ops)
    lines = _demote_untitled(lines, ops)
    return lines, ops
