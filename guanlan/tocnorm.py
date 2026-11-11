"""按目录规范化标题层级（#175，零 LLM、纯函数）。

转换器（MinerU 等）给出的 Markdown 标题层级常不可信：同层文档标题时而 `#` 时而 `##`，中间层干脆是
普通文本。书里自带的目录才是层级的权威来源。本模块在规范化里（去噪之后、跨页接句之前）：

1. 识别目录：「目录 / 目 录 / 目次 / Contents」标题之后、以 `……(页码)` 结尾的条目；
2. 按目录顺序在正文中定位每个条目（编号一致、顺序单调、印刷页码 + 偏移在预期附近；先精确后模糊）；
3. 命中率 ≥ TOC_MIN_MATCH 时改写标题层级——只改行首的标题标记，正文一字不动：
   - 命中的行按目录层级改成 `#` / `##` / …（普通文本行补标题标记）；
   - 目录区内的标题降为普通文本（不能被拆分当章）；目录及其之前的标题、不在目录里的标题排到目录最深层之下。

命中率不足 → 原样返回（退回 splitdoc 的标题形状判断，再退回纯层级）。
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

__all__ = ["TocResult", "normalize_by_toc"]

TOC_MIN_ENTRIES = 5  # 带页码的条目至少这么多才算目录
TOC_MIN_MATCH = 0.8  # 命中率门槛
FUZZY_MIN_RATIO = 0.85  # 模糊匹配的相似度下限
PAGE_TOLERANCE = 3  # 印刷页码 + 偏移与实际所在页允许的误差
_TOC_GAP_MAX = 3  # 连续这么多行没有页码 → 目录结束
_ENTRY_MAX_CHARS = 120  # 单个条目（含折行）上限
_LINE_MAX_CHARS = 150  # 正文里比这长的行不可能是标题

_CN_NUM = "一二三四五六七八九十百千零〇○"
_TOC_TITLE_RE = re.compile(r"^(?:目录|目次|contents|tableofcontents)$", re.IGNORECASE)
# 条目末尾的页码：括号页码「(3)」「（ 19）」，或引导点后的裸数字「…… 3」
_PAGE_REF_RE = re.compile(r"(?:[(（]\s*(\d{1,4})\s*[)）]|(?:[.．…·]{2,}|…)\s*(\d{1,4}))\s*$")
_LEADER_RE = re.compile(r"[\s.．…·•]+$")
_PAGE_ONLY_RE = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,8})$", re.IGNORECASE)
_HEADING_MARK_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_LIST_MARK_RE = re.compile(r"^\s*(?:[-*+]\s+|>\s*)")
_ANCHOR_RE = re.compile(r"^\s*<!--\s*page\s+(\d+)")
# 编号形式（按目录里首次出现的顺序定层级）。在保留空格的文本上匹配：「1 范围」靠空格才认得出编号
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("编", re.compile(rf"^第\s*[\d{_CN_NUM}]+\s*编")),
    ("部分", re.compile(rf"^第\s*[\d{_CN_NUM}]+\s*部分")),
    ("章", re.compile(rf"^第\s*[\d{_CN_NUM}]+\s*章")),
    ("节", re.compile(rf"^第\s*[\d{_CN_NUM}]+\s*节")),
    ("cn", re.compile(rf"^[{_CN_NUM}]+\s*、")),
    ("paren-cn", re.compile(rf"^\(\s*[{_CN_NUM}]+\s*\)")),
    ("paren-num", re.compile(r"^\(\s*\d{1,3}\s*\)")),
    # 多级数字编号按级数分形式：1.1 与 1.1.1 不同级
    ("num-multi", re.compile(r"^\d{1,3}(?:\.\d{1,3})+\.?(?=\s|[^\d.]|$)")),
    ("num", re.compile(r"^\d{1,3}(?:[.、](?!\d)|(?=\s+\S))")),
)
# 日期括注：「(1995年3月18日)」；括注残缺时（「(试行X1992年1月4日)」）从含年份的左括号起删到行尾
_DATE_PAREN_RE = re.compile(r"\([^()]*\d{4}\s*年[^()]*\)|\([^()]*\d{4}\s*年.*$")
# 施行状态括注：目录与正文常一边有一边无（或括注残缺），比对时不计
_STATUS_PAREN_RE = re.compile(r"\((?:试行|暂行|草案|征求意见稿)\)")
_KEY_DROP_RE = re.compile(r"[\s《》〈〉“”\"'‘’.…·•、，,:：;；!！?？\-—_]")


@dataclass
class _Entry:
    title: str
    page: int | None
    marker: tuple[str, str] | None = None  # (形式, 规范化编号)
    key: str = ""
    level: int = 0
    line: int | None = None  # 定位到的候选（`_Cand` 列表下标）


@dataclass
class TocResult:
    lines: list[str]
    entries: int = 0  # 目录条目数（0 = 没找到目录）
    matched: int = 0
    applied: bool = False
    promoted: list[tuple[int | None, int, str]] = None  # type: ignore[assignment]  # (页, 级, 文本)
    relevelled: int = 0  # 改了层级 / 降为正文的其它标题数


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _clean(line: str) -> str:
    """去行首标题标记 / 列表标记 / 引用标记后的文本。"""
    s = _HEADING_MARK_RE.sub("", line)
    s = _LIST_MARK_RE.sub("", s)
    return s.strip()


def _marker(text: str) -> tuple[tuple[str, str] | None, str]:
    """(编号, 去编号并去空白后的正文)。比对前统一全角半角（NFKC）。"""
    t = _nfkc(text).strip()
    for kind, pat in _MARKERS:
        m = pat.match(t)
        if m:
            value = re.sub(r"\s+", "", m.group(0)).rstrip(".、")
            if kind == "num-multi":
                kind = f"num{value.count('.') + 1}"
            return (kind, value), re.sub(r"\s+", "", t[m.end() :])
    return None, re.sub(r"\s+", "", t)


def _key(text: str) -> str:
    _mk, rest = _marker(text)
    rest = _STATUS_PAREN_RE.sub("", _DATE_PAREN_RE.sub("", rest))
    return _KEY_DROP_RE.sub("", rest).casefold()


# ── 识别目录 ─────────────────────────────────────────────────────────────────


def _find_toc(lines: list[str]) -> tuple[int, int, list[_Entry]] | None:
    """返回 (目录标题行, 目录末行, 条目)；没有像样的目录 → None。"""
    for start, raw in enumerate(lines):
        if not _TOC_TITLE_RE.match(re.sub(r"\s+", "", _nfkc(_clean(raw)))):
            continue
        entries: list[_Entry] = []
        ends: list[int] = []  # 每个条目的最后一行
        open_: _Entry | None = None  # 还没拿到页码、可续折行的条目
        gap = 0
        for k in range(start + 1, len(lines)):
            s = _clean(lines[k])
            if not s or _ANCHOR_RE.match(lines[k]) or _PAGE_ONLY_RE.match(s):
                continue
            m = _PAGE_REF_RE.search(s)
            page = int(m.group(1) or m.group(2)) if m else None
            text = _LEADER_RE.sub("", s[: m.start()] if m else s)
            mk, _rest = _marker(text)
            if open_ is not None and mk is None and len(open_.title) + len(text) <= _ENTRY_MAX_CHARS:
                open_.title += text  # 上一条折行
                ends[-1] = k
                if page is not None:
                    open_.page, open_, gap = page, None, 0
                else:
                    gap += 1
            elif page is not None:
                entries.append(_Entry(text, page))
                ends.append(k)
                open_, gap = None, 0
            elif len(text) <= _ENTRY_MAX_CHARS // 2:
                open_ = _Entry(text, None)  # 无页码的层级行（「二、国务院行政法规」）或折行条目的首行
                entries.append(open_)
                ends.append(k)
                gap += 1
            else:
                break
            if gap > _TOC_GAP_MAX:
                break
        # 目录以最后一条带页码的条目结束：其后的无页码行是正文开头的同名标题，不算目录
        last = max((n for n, e in enumerate(entries) if e.page is not None), default=-1)
        entries, ends = entries[: last + 1], ends[: last + 1]
        if sum(1 for e in entries if e.page is not None) >= TOC_MIN_ENTRIES:
            return start, ends[-1], entries
    return None


def _assign_levels(entries: list[_Entry]) -> None:
    """层级按编号形式在目录中首次出现的顺序定。无编号条目：在首个有编号条目之前（前言、凡例）算第 1 级，
    之后（「附：…」、后记）与上一条同级——算第 1 级会把其后整段都挂到它下面。"""
    order: list[str] = []
    for e in entries:
        e.marker, _ = _marker(e.title)
        e.key = _key(e.title)
        if e.marker and e.marker[0] not in order:
            order.append(e.marker[0])
    prev = 1
    for e in entries:
        e.level = min(6, order.index(e.marker[0]) + 1) if e.marker else prev
        prev = e.level


# ── 定位 ─────────────────────────────────────────────────────────────────────


@dataclass
class _Cand:
    idx: int
    page: int | None
    marker: tuple[str, str] | None
    key: str


def _candidates(lines: list[str], lo: int) -> list[_Cand]:
    out: list[_Cand] = []
    page: int | None = None
    in_fence = False
    for i, line in enumerate(lines):
        m = _ANCHOR_RE.match(line)
        if m:
            page = int(m.group(1))
            continue
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if i <= lo or in_fence:
            continue
        s = _clean(line)
        if not s or len(s) > _LINE_MAX_CHARS or s.startswith(("|", "!", "<")):
            continue
        mk, _ = _marker(s)
        out.append(_Cand(i, page, mk, _key(s)))
    return out


def _page_ok(e: _Entry, c: _Cand, offset: int | None) -> bool:
    if offset is None or e.page is None or c.page is None:
        return True
    return abs(c.page - e.page - offset) <= PAGE_TOLERANCE


def _marker_ok(e: _Entry, c: _Cand) -> bool:
    return e.marker is None or c.marker is None or e.marker == c.marker


def _locate(entries: list[_Entry], cands: list[_Cand]) -> None:
    by_key: dict[str, list[int]] = {}
    for n, c in enumerate(cands):
        if c.key:
            by_key.setdefault(c.key, []).append(n)

    def exact_pass(offset: int | None) -> None:
        cursor = -1
        for e in entries:
            e.line = None
            pool = [n for n in by_key.get(e.key, []) if n > cursor and _marker_ok(e, cands[n])] if e.key else []
            if not pool:
                continue
            # 同名多处：页码在预期附近 > 编号一致 > 最先出现
            def rank(n: int) -> tuple[int, int, int]:
                c = cands[n]
                return (0 if _page_ok(e, c, offset) else 1, 0 if e.marker and c.marker == e.marker else 1, n)

            best = min(pool, key=rank)
            if not _page_ok(e, cands[best], offset):
                continue
            e.line = best
            cursor = best

    exact_pass(None)
    offs = [cands[e.line].page - e.page for e in entries if e.line is not None and e.page and cands[e.line].page]
    offset = Counter(offs).most_common(1)[0][0] if offs else None
    exact_pass(offset)

    hits = sum(1 for e in entries if e.line is not None)
    if hits < len(entries) * 0.5:
        return

    def bracket(k: int) -> tuple[int, int]:
        lo = max((x.line for x in entries[:k] if x.line is not None), default=-1)
        hi = min((x.line for x in entries[k + 1 :] if x.line is not None), default=len(cands))
        return lo, hi

    # 目录页码印错（相邻两条印成同一页）：前后两个已定位条目之间、编号一致的精确命中不再卡页码
    for k, e in enumerate(entries):
        if e.line is not None or not e.key:
            continue
        lo, hi = bracket(k)
        pool = [n for n in by_key.get(e.key, []) if lo < n < hi and _marker_ok(e, cands[n])]
        if pool:
            e.line = pool[0]

    # 模糊补漏：只在前后两个已定位条目之间找，编号、页码同样约束
    for k, e in enumerate(entries):
        if e.line is not None or not e.key:
            continue
        lo, hi = bracket(k)
        best_n, best_r = None, FUZZY_MIN_RATIO
        for n in range(lo + 1, hi):
            c = cands[n]
            # 长度差超过两成不可能达到 FUZZY_MIN_RATIO：先筛掉，免得逐行跑 SequenceMatcher
            if not c.key or abs(len(c.key) - len(e.key)) > 0.2 * max(len(c.key), len(e.key)):
                continue
            if not _marker_ok(e, c) or not _page_ok(e, c, offset):
                continue
            sm = difflib.SequenceMatcher(None, e.key, c.key, autojunk=False)
            if sm.quick_ratio() < best_r:
                continue
            r = sm.ratio()
            if r > best_r or (r == best_r and best_n is None):
                best_n, best_r = n, r
        if best_n is not None:
            e.line = best_n


# ── 改写 ─────────────────────────────────────────────────────────────────────


# 不在目录里的标题按编号形式排深浅（固定次序；认不出编号的排最深）
_SUB_ORDER = ("编", "部分", "章", "节", "cn", "paren-cn", "num2", "num3", "num4", "num5", "num", "paren-num")


def normalize_by_toc(lines: list[str]) -> TocResult:
    """按目录改写标题层级；没有目录或命中率不足 → 原样返回（applied=False）。"""
    found = _find_toc(lines)
    if found is None:
        return TocResult(lines, promoted=[])
    start, end, entries = found
    _assign_levels(entries)
    cands = _candidates(lines, end)
    _locate(entries, cands)
    matched = [e for e in entries if e.line is not None]
    res = TocResult(lines, entries=len(entries), matched=len(matched), promoted=[])
    if len(matched) < len(entries) * TOC_MIN_MATCH:
        return res

    out = list(lines)
    deepest = max(e.level for e in entries)
    target: dict[int, tuple[int, int | None]] = {cands[e.line].idx: (e.level, cands[e.line].page) for e in matched}  # type: ignore[index]
    first_hit = min(target)
    # 不在目录里的标题：多级数字编号（1.1 / 1.1.1，深度由编号自身决定）在目录里出现过 → 用目录里该级的层级
    # （目录漏列的「1.1.4」与「1.1.3」同级）。「一、」「1.」等单级编号不继承：汇编里各篇自己的「七、…」
    # 与目录第 1 级同形，继承会把它们提成顶层切点。其余按编号形式（_SUB_ORDER）排在目录最深层之下，
    # 只给正文里实际出现的形式排名；认不出编号的排最深
    toc_kind = {e.marker[0]: e.level for e in entries if e.marker and re.fullmatch(r"num[2-9]", e.marker[0])}

    def kind_of(text: str) -> str | None:
        mk, _ = _marker(text)
        return mk[0] if mk else None

    present = {
        kind_of(_clean(ln)) for i, ln in enumerate(lines) if i > end and i not in target and _HEADING_MARK_RE.match(ln)
    }
    ranks = [k for k in _SUB_ORDER if k in present and k not in toc_kind] + [None]

    def sub_level(text: str) -> int:
        k = kind_of(text)
        if k in toc_kind:
            return toc_kind[k]
        return min(6, deepest + 1 + ranks.index(k if k in ranks else None))

    for i, line in enumerate(lines):
        if i in target:
            level, page = target[i]
            text = _clean(line)
            new = f"{'#' * level} {text}"
            if new != line:
                out[i] = new
                res.promoted.append((page, level, text))
            continue
        hm = _HEADING_MARK_RE.match(line)
        if not hm:
            continue
        text = line[hm.end() :]
        if start < i <= end:  # 目录区里的标题：降为正文，免得拆分时当章
            new = text
        elif i < first_hit:  # 目录标题与其前的封面 / 扉页标题：不与目录第 1 级争层级
            new = f"{'#' * min(6, deepest + 1)} {text}"
        else:
            new = f"{'#' * sub_level(text)} {text}"
        if new != line:
            out[i] = new
            res.relevelled += 1
    res.lines = out
    res.applied = True
    return res
