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
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = ["TocResult", "TocReviewer", "normalize_by_toc", "toc_title_lines"]

# 目录审校（#175）：(目录区原文, 规则草稿 [{title, page, level}]) → 修正后的条目列表。
# 由调用方注入（一键入库经 Agentao 只读调用模型，见 tocreview.py）；本模块不碰 LLM。
TocReviewer = Callable[[str, list[dict]], list[dict]]

TOC_MIN_ENTRIES = 5  # 带页码的条目至少这么多才算目录
TOC_MIN_MATCH = 0.8  # 命中率门槛
FUZZY_MIN_RATIO = 0.85  # 模糊匹配的相似度下限
PAGE_TOLERANCE = 3  # 印刷页码 + 偏移与实际所在页允许的误差
_TOC_GAP_MAX = 3  # 连续这么多行没有页码 → 目录结束
_ENTRY_MAX_CHARS = 120  # 单个条目（含折行）上限
_LINE_MAX_CHARS = 150  # 正文里比这长的行不可能是标题
REVIEW_MAX_CHARS = 40_000  # 目录区超过这么长不送审（截断的目录会让审校结果缺条目）
_REVIEW_SCAN_LINES = 400  # 规则没认出目录时，从目录标题起最多送审这么多行
REVIEW_MAX_DROP = 0.2  # 审校结果里对不上目录原文的条目超过这个比例 → 整份不用

_CN_NUM = "一二三四五六七八九十百千零〇○"
_TOC_TITLE_RE = re.compile(r"^(?:目录|目次|contents|tableofcontents)$", re.IGNORECASE)
_ROMAN = "IVXLCDMivxlcdmⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ"
# 引导点后的页码（行中也认）：转换器常把几条目录并成一行「摘要....I Abstract....II 引言 1」，按它切开
_LEADER_REF_RE = re.compile(rf"(?:[.．…·]{{2,}}|…)\s*[(（]?\s*(\d{{1,4}}|[{_ROMAN}]{{1,6}})\s*[)）]?")
# 行尾页码（无引导点）：括号页码「(3)」、贴在书名号 / 括号后的「》188」、空白后的裸数字「绪论 1」
_TAIL_REF_RE = re.compile(
    rf"(?:[(（]\s*(\d{{1,4}}|[{_ROMAN}]{{1,6}})\s*[)）]|(?<=[》）)」”])\s*(\d{{1,4}})|(?<=[^\d.\s])\s+(\d{{1,4}}))\s*$"
)
_LEADER_RE = re.compile(r"[\s.．…·•]+$")
# 图表目录：表头去掉；「图1.1 …」「表 2-3 …」条目不算正文目录
_FIG_LIST_HEAD_RE = re.compile(r"^(?:图表目录|插图目录|附图目录|图目录|表目录|图表清单)\s*")
_FIG_ENTRY_RE = re.compile(r"^(?:图|表|附图|附表|Figure|Fig\.|Table)\s*\d+(?:\s*[.\-－]\s*\d+)*(?!\d)", re.IGNORECASE)
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
    ref: bool = False  # 带页码（含罗马页码，此时 page 为 None）
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
    promoted: list[tuple[int | None, int, str]] = field(default_factory=list)  # (页, 级, 文本)
    relevelled: int = 0  # 改了层级 / 降为正文的其它标题数
    source: str = "rules"  # 条目来源：rules（规则解析）/ llm（经审校）
    review: str | None = None  # 审校结论：None = 未送审；ok / rejected:<原因> / failed:<原因>
    toc: list[dict] = field(default_factory=list)  # 最终条目 [{title, page, level, matched}]，供落盘核对


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


def _page_value(token: str | None) -> int | None:
    return int(token) if token and token.isdigit() else None  # 罗马页码（前置页）不参与页码约束


def _segments(s: str) -> list[tuple[str, int | None, bool]]:
    """一行目录切成 [(条目文本, 页码, 是否带页码)]；并成一行的多条按行中引导点页码切开。"""
    out: list[tuple[str, int | None, bool]] = []
    pos = 0
    for m in _LEADER_REF_RE.finditer(s):
        out.append((_LEADER_RE.sub("", s[pos : m.start()]).strip(), _page_value(m.group(1)), True))
        pos = m.end()
    tail = s[pos:].strip()
    if tail:
        m = _TAIL_REF_RE.search(tail)
        if m:
            token = m.group(1) or m.group(2) or m.group(3)
            out.append((_LEADER_RE.sub("", tail[: m.start()]).strip(), _page_value(token), True))
        else:
            out.append((tail, None, False))
    # 空文本只在带页码时保留：「…………(440)」是上一条折行的页码
    return [(t, p, r) for t, p, r in out if t or r]


def _find_toc(lines: list[str]) -> tuple[int, int, list[_Entry]] | None:
    """返回 (目录标题行, 目录末行, 条目)；没有像样的目录 → None。"""
    for start in toc_title_lines(lines):
        entries: list[_Entry] = []
        ends: list[int] = []  # 每个条目的最后一行
        open_: _Entry | None = None  # 还没拿到页码、可续折行的条目
        gap = 0
        last_ref_line = start
        stop = False
        for k in range(start + 1, len(lines)):
            s = _clean(lines[k])
            if not s or _ANCHOR_RE.match(lines[k]) or _PAGE_ONLY_RE.match(s):
                continue
            s = _FIG_LIST_HEAD_RE.sub("", s)
            line_has_ref = False
            for text, page, ref in _segments(s):
                if not text:  # 只有页码：给还没拿到页码的上一条
                    if open_ is not None:
                        open_.page, open_.ref, open_ = page, True, None
                        ends[-1] = k
                    line_has_ref = True
                    continue
                if _FIG_ENTRY_RE.match(_nfkc(text)):  # 图表目录条目：跳过，但目录仍在继续
                    line_has_ref = line_has_ref or ref
                    open_ = None
                    continue
                mk, _rest = _marker(text)
                if open_ is not None and mk is None and len(open_.title) + len(text) <= _ENTRY_MAX_CHARS:
                    open_.title += text  # 上一条折行
                    ends[-1] = k
                    if ref:
                        open_.page, open_.ref, open_ = page, True, None
                elif ref:
                    entries.append(_Entry(text, page, ref=True))
                    ends.append(k)
                    open_ = None
                elif len(text) <= _ENTRY_MAX_CHARS // 2:
                    open_ = _Entry(text, None)  # 无页码的层级行（「二、国务院行政法规」）或折行条目的首行
                    entries.append(open_)
                    ends.append(k)
                else:
                    stop = True
                    break
                line_has_ref = line_has_ref or ref
            if stop:
                break
            if line_has_ref:
                gap, last_ref_line = 0, k
            else:
                gap += 1
                if gap > _TOC_GAP_MAX:
                    break
        # 目录以最后一条带页码的条目结束：其后的无页码行是正文开头的同名标题，不算目录
        last = max((n for n, e in enumerate(entries) if e.ref), default=-1)
        entries, ends = entries[: last + 1], ends[: last + 1]
        if sum(1 for e in entries if e.ref) >= TOC_MIN_ENTRIES:
            return start, max(ends[-1], last_ref_line), entries
    return None


def toc_title_lines(lines: list[str]) -> list[int]:
    """「目录 / 目 录 / 目次 / Contents」标题所在的行。"""
    return [i for i, raw in enumerate(lines) if _TOC_TITLE_RE.match(re.sub(r"\s+", "", _nfkc(_clean(raw))))]


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


def _fenced(lines: list[str]) -> list[bool]:
    """每行是否在代码围栏内（含围栏线本身）。与 normalize 同口径（CommonMark 闭合规则）。"""
    from .normalize import _FENCE_RE, fence_closes  # 延迟导入：normalize 在模块级导入本模块

    out: list[bool] = []
    mark = ""
    for line in lines:
        if mark:
            out.append(True)
            if fence_closes(mark, line):
                mark = ""
            continue
        m = _FENCE_RE.match(line)
        if m:
            mark = m.group(1)
        out.append(bool(m))
    return out


@dataclass
class _Cand:
    idx: int
    page: int | None
    marker: tuple[str, str] | None
    key: str


def _candidates(lines: list[str], lo: int, fenced: list[bool]) -> list[_Cand]:
    out: list[_Cand] = []
    page: int | None = None
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        m = _ANCHOR_RE.match(line)
        if m:
            page = int(m.group(1))
            continue
        if i <= lo:
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


# ── 审校 ─────────────────────────────────────────────────────────────────────


def _review_region(lines: list[str], found: tuple[int, int, list[_Entry]] | None) -> tuple[int, int] | None:
    """送审的目录区 [lo, hi]：规则认出了目录就用它；没认出但有目录标题 → 从标题起取一段。"""
    if found is not None:
        return found[0], found[1]
    titles = toc_title_lines(lines)
    if not titles:
        return None
    lo = titles[0]
    return lo, min(len(lines) - 1, lo + _REVIEW_SCAN_LINES)


def _validate_review(items: object, region_text: str) -> tuple[list[_Entry] | None, str]:
    """校验审校结果：结构合法、条目文字能在目录原文里找到（不许编造）。返回 (条目, 结论)。"""
    if not isinstance(items, list) or not items:
        return None, "rejected:结果不是非空列表"
    region_key = _KEY_DROP_RE.sub("", re.sub(r"\s+", "", _nfkc(region_text))).casefold()
    # 目录原文作 seq2 只建一次索引（最长可达 REVIEW_MAX_CHARS），逐条只换 seq1
    sm = difflib.SequenceMatcher(None, "", region_key, autojunk=False)
    entries: list[_Entry] = []
    dropped = 0
    for it in items:
        if not isinstance(it, dict):
            dropped += 1
            continue
        title, page, level = it.get("title"), it.get("page"), it.get("level")
        if not isinstance(title, str) or not title.strip() or len(title) > _ENTRY_MAX_CHARS:
            dropped += 1
            continue
        if isinstance(page, bool) or not (page is None or (isinstance(page, int) and 0 < page < 100_000)):
            page = None
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 6:
            dropped += 1
            continue
        k = _KEY_DROP_RE.sub("", re.sub(r"\s+", "", _nfkc(title))).casefold()
        if not k:
            dropped += 1
            continue
        if k not in region_key:
            sm.set_seq1(k)
            if sm.find_longest_match(0, len(k), 0, len(region_key)).size < 0.85 * len(k):
                dropped += 1  # 原文里找不到：编造或改写过度
                continue
        e = _Entry(title.strip(), page, ref=True)
        e.level = level
        entries.append(e)
    total = len(items)
    if dropped > total * REVIEW_MAX_DROP:
        return None, f"rejected:{dropped}/{total} 条对不上目录原文"
    if len(entries) < TOC_MIN_ENTRIES:
        return None, f"rejected:只有 {len(entries)} 条"
    for e in entries:
        e.marker, _ = _marker(e.title)
        e.key = _key(e.title)
    return entries, "ok"


def _key_in(k: str, text_key: str) -> bool:
    """条目文字是否出现在一段文字里：整串包含，或最长公共子串 ≥ 85%（与 `_validate_review` 同口径）。"""
    if k in text_key:
        return True
    if not k or not text_key:
        return False
    sm = difflib.SequenceMatcher(None, k, text_key, autojunk=False)
    return sm.find_longest_match(0, len(k), 0, len(text_key)).size >= 0.85 * len(k)


def _reviewed_end(lines: list[str], lo: int, hi: int, entries: list[_Entry]) -> int:
    """审校后的目录末行：从目录标题起按顺序逐条对上条目文字，全部对完的那一行。

    规则没认出目录时送审区会延伸进正文，正文标题也含条目文字；目录在前、按序列出全部条目，
    所以第一次按序对完全部条目的地方就是目录结尾。对上的口径与审校校验相同（含模糊），否则
    校验放行的条目在这里对不上，结尾会提前截断。对不完 → 取最后一次对上的行。
    """
    keys = [_KEY_DROP_RE.sub("", re.sub(r"\s+", "", _nfkc(e.title))).casefold() for e in entries]
    pos, end = 0, lo
    for i in range(lo + 1, hi + 1):
        lk = _KEY_DROP_RE.sub("", re.sub(r"\s+", "", _nfkc(_clean(lines[i])))).casefold()
        hit = False
        while pos < len(keys) and _key_in(keys[pos], lk):
            pos, hit = pos + 1, True
        if hit:
            end = i
        if pos == len(keys):
            break
    return end


def _apply_review(
    lines: list[str], found: tuple[int, int, list[_Entry]] | None, reviewer: TocReviewer
) -> tuple[tuple[int, int, list[_Entry]] | None, str, str | None]:
    """送审并校验。返回 (采用的 (start, end, entries), 来源, 审校结论)；不可用时沿用规则结果。"""
    region = _review_region(lines, found)
    if region is None:
        return found, "rules", None
    lo, hi = region
    text = "\n".join(lines[lo : hi + 1])
    if len(text) > REVIEW_MAX_CHARS:
        return found, "rules", "rejected:目录区过长，未送审"
    draft = [{"title": e.title, "page": e.page, "level": e.level} for e in (found[2] if found else [])]
    try:
        items = reviewer(text, draft)
    except Exception as exc:  # noqa: BLE001 —— 审校只是增强：任何失败都退回规则结果
        return found, "rules", f"failed:{str(exc)[:200] or type(exc).__name__}"
    entries, verdict = _validate_review(items, text)
    if entries is None:
        return found, "rules", verdict
    end = _reviewed_end(lines, lo, hi, entries)
    if found is not None:  # 规则已认出的目录区不缩：审校条目对不全时，余下目录行不能当正文候选
        end = max(end, found[1])
    return (lo, end, entries), "llm", verdict


def normalize_by_toc(lines: list[str], reviewer: TocReviewer | None = None) -> TocResult:
    """按目录改写标题层级；没有目录或命中率不足 → 原样返回（applied=False）。

    `reviewer`：规则解析后送审目录（#175）。审校结果须通过 `_validate_review`，否则沿用规则结果；
    之后的定位、把关、改写都是确定性的，审校改不了正文。
    """
    found = _find_toc(lines)
    if found is not None:
        _assign_levels(found[2])
    source, review = "rules", None
    if reviewer is not None:
        found, source, review = _apply_review(lines, found, reviewer)
    if found is None:
        return TocResult(lines, promoted=[], review=review)
    start, end, entries = found
    fenced = _fenced(lines)
    cands = _candidates(lines, end, fenced)
    _locate(entries, cands)
    matched = [e for e in entries if e.line is not None]
    res = TocResult(lines, entries=len(entries), matched=len(matched), promoted=[], source=source, review=review)
    res.toc = [{"title": e.title, "page": e.page, "level": e.level, "matched": e.line is not None} for e in entries]
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
        kind_of(_clean(ln))
        for i, ln in enumerate(lines)
        if i > end and i not in target and not fenced[i] and _HEADING_MARK_RE.match(ln)
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
        hm = None if fenced[i] else _HEADING_MARK_RE.match(line)  # 代码块里的 `# 注释` 不是标题
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
