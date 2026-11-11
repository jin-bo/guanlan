"""一键入库的拆分（P4.30 §4，决策P4.30-3/4/9/13）。**确定性、零 LLM、不写盘。**

输入规范化后的 `<sample>.md`（frontmatter + 正文），返回分份列表：

- 字数 ≤ `SPLIT_MIN` → 不拆（返回空列表）；
- 否则按「本段出现 ≥ 2 次的最高一级标题」切章；找不到或各章都 < `CHAPTER_FLOOR` → 按大小拆；
  单章 > `PART_MAX` → 对该章递归（换下一级标题），没有更低一级标题时改按大小拆；
- 按大小拆：每份 ≤ `PART_MAX`，切点优先级 标题前 > 分页锚点前 > 空行，绝不切在段落内部；
  单段超限时退到句末标点切。

正文按「单元」（一行，或超长行在句末处切开的片段）组织：所有切点都落在单元边界上，
因此 **各份正文按序拼接 == 拆前正文**（无丢失、无重复，测试锁住）。

渲染（`render_part` / `render_index`）负责 frontmatter、`split:` 元数据、图片引用改写
（分卷位于 `parsed/<sample>/` 下，`images/<sample>/…` → `../images/<sample>/…`）与目录索引。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .imageio import _MD_IMAGE, _is_external_ref
from .normalize import _FENCE_RE, PAGE_ANCHOR_RE, count_chars, fence_closes  # 围栏判定与 normalize 同一口径
from .rawio import dump_frontmatter, raw_slug

__all__ = [
    "SPLIT_MIN",
    "CHAPTER_FLOOR",
    "PART_MAX",
    "NAME_MAX_CHARS",
    "BASENAME_MAX_BYTES",
    "INDEX_SUFFIX",
    "Part",
    "split_document",
    "render_part",
    "render_index",
    "rewrite_image_refs",
    "index_name",
]

SPLIT_MIN = 40_000  # 不拆的上限
CHAPTER_FLOOR = 10_000  # 章节下限：全部章都小于它 → 改按大小拆；前言小于它 → 并入第一章
PART_MAX = 40_000  # 每份上限
SIZE_MIN_FILL = 0.5  # 按大小拆时，优先在已装满一半以上的范围里找高优先级切点（避免切出碎片）
NAME_MAX_CHARS = 24  # 章名截断长度
BASENAME_MAX_BYTES = 200  # 整个 basename（含 .md）字节上限：Linux 按字节计 255
INDEX_SUFFIX = "目录"

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE_CUT_RE = re.compile(r"[。！？；!?;]+[”」』）)]*")

# 切点优先级
_P_HEADING, _P_ANCHOR, _P_BLANK, _P_SENTENCE = 3, 2, 1, 0


@dataclass
class _Unit:
    text: str  # 含自身行尾（超长行切出的中间片段不含）
    chars: int
    heading: int = 0  # ATX 标题级别（0 = 非标题）
    title: str = ""
    page: int | None = None  # 本单元若是分页锚点 → 页号
    cut: int | None = None  # 在本单元**之前**切的优先级（None = 不可切）


@dataclass
class Part:
    """一份拆分结果（叶子）。`body` 是原文的精确切片。"""

    body: str
    chars: int
    path: list[str]  # 标题路径
    method: str  # chapter | size
    name_segments: list[tuple[int, int, str]]  # [(序号, 同级总数, 章名), …]
    pages: tuple[int, int] | None = None
    stem: str = ""
    index: int = 0
    total: int = 0
    prev: str | None = None
    next: str | None = None
    title: str = ""
    extra: dict = field(default_factory=dict)


# ── 单元化 ───────────────────────────────────────────────────────────────────


def _split_long_line(line: str, eol: str) -> list[str]:
    """单行超过 PART_MAX → 在句末标点后切成片段（拼回 == 原行）；连句末标点都没有时按字符硬切。"""
    segs: list[str] = []
    start = 0
    last_cut: int | None = None
    acc = 0  # count_chars(line[start:last_cut])，逐句累加：不每句都从 start 重数（超长行是 O(句数×PART_MAX)）
    for m in _SENTENCE_CUT_RE.finditer(line):
        prev_end = last_cut if last_cut is not None else start
        sent = count_chars(line[prev_end : m.end()])
        if last_cut is not None and last_cut > start and acc + sent > PART_MAX:
            segs.append(line[start:last_cut])
            start, acc = last_cut, 0
        acc += sent
        last_cut = m.end()
    segs.append(line[start:])
    pieces: list[str] = []
    for seg in segs:  # 单句本身超限（不只是末段）同样硬切，保证每片 ≤ PART_MAX
        while count_chars(seg) > PART_MAX:
            pieces.append(seg[:PART_MAX])
            seg = seg[PART_MAX:]
        pieces.append(seg)
    pieces[-1] += eol
    return pieces


def _units(body: str) -> list[_Unit]:
    units: list[_Unit] = []
    in_fence = False
    fence_mark = ""
    prev_blank = True
    # 只按 "\n" 切行（与 normalize / count_chars 同口径）：`splitlines` 还会在 U+2028 / U+0085 等处切，
    # 行中间的 `# …` 会被误当标题 / 围栏，切点落进 markdown 的同一行里。
    raw_lines = re.findall(r"[^\n]*\n|[^\n]+$", body)
    for line in raw_lines:
        content = line.rstrip("\r\n")
        eol = line[len(content) :]
        m_f = _FENCE_RE.match(content)
        if in_fence:
            units.append(_Unit(line, count_chars(content)))
            if fence_closes(fence_mark, content):
                in_fence = False
            prev_blank = False
            continue
        if m_f:
            in_fence, fence_mark = True, m_f.group(1)
            units.append(_Unit(line, count_chars(content), cut=_P_BLANK if prev_blank else None))
            prev_blank = False
            continue
        blank = not content.strip()
        m_h = _HEADING_RE.match(content)
        m_a = PAGE_ANCHOR_RE.match(content)
        if blank:
            units.append(_Unit(line, 0))
        elif m_h:
            units.append(
                _Unit(line, count_chars(content), heading=len(m_h.group(1)), title=m_h.group(2).strip(), cut=_P_HEADING)
            )
        elif m_a:
            units.append(_Unit(line, 0, page=int(m_a.group(1)), cut=_P_ANCHOR))
        else:
            n = count_chars(content)
            if n > PART_MAX:
                pieces = _split_long_line(content, eol)
                for k, piece in enumerate(pieces):
                    cut = (_P_BLANK if prev_blank else None) if k == 0 else _P_SENTENCE
                    units.append(_Unit(piece, count_chars(piece), cut=cut))
            else:
                units.append(_Unit(line, n, cut=_P_BLANK if prev_blank else None))
        prev_blank = blank
    return units


# ── 拆分 ─────────────────────────────────────────────────────────────────────


@dataclass
class _Seg:
    lo: int
    hi: int  # units[lo:hi]
    path: list[str]
    names: list[tuple[int, int, str]]
    method: str = "chapter"


def _chars(units: list[_Unit], lo: int, hi: int) -> int:
    return sum(u.chars for u in units[lo:hi])


def _size_split(units: list[_Unit], lo: int, hi: int) -> list[tuple[int, int]]:
    """贪心装箱：每份 ≤ PART_MAX；先在已装满一半以上的范围里取最高优先级、最靠后的切点。"""
    out: list[tuple[int, int]] = []
    start = lo
    while _chars(units, start, hi) > PART_MAX:
        acc = 0
        limit = start  # 最远可切位置（units[start:limit] ≤ PART_MAX）
        for i in range(start, hi):
            if acc + units[i].chars > PART_MAX:
                break
            acc += units[i].chars
            limit = i + 1
        cands: list[tuple[int, int, int]] = []  # (优先级, 位置, 已装字数)
        acc = 0
        for i in range(start + 1, min(limit, hi - 1) + 1):
            acc += units[i - 1].chars
            if units[i].cut is not None:
                cands.append((units[i].cut, i, acc))
        if not cands:
            # 连句末切点都没有（单元自身就超限）：至少前进一个单元，保证终止
            cut_at = max(limit, start + 1)
        else:
            full = [c for c in cands if c[2] >= PART_MAX * SIZE_MIN_FILL] or cands
            best = max(c[0] for c in full)
            cut_at = max(c[1] for c in full if c[0] == best)
        out.append((start, cut_at))
        start = cut_at
    out.append((start, hi))
    return out


def _headings_by_level(units: list[_Unit], lo: int, hi: int, min_level: int) -> dict[int, list[int]]:
    found: dict[int, list[int]] = {}
    for i in range(lo, hi):
        lv = units[i].heading
        if lv and lv >= min_level:
            found.setdefault(lv, []).append(i)
    return found


@dataclass
class _Group:
    lo: int
    hi: int
    titles: list[str]
    main: int  # titles 里作为名字的那一项
    chars: int

    @property
    def name(self) -> str:
        return self.titles[self.main]

    @property
    def label(self) -> str | None:
        if not self.titles or self.titles[0] is None:  # 单独成份的前言
            return None
        if len(self.titles) == 1:
            return self.titles[0]
        if self.main == 0 and all(t is not None for t in self.titles):
            return f"{self.titles[0]}～{self.titles[-1]}"
        return self.titles[self.main]


def _pack(units: list[_Unit], pieces: list[tuple[int, int, str | None]]) -> list[_Group]:
    """相邻小章（< CHAPTER_FLOOR）在不超过 PART_MAX 的前提下装进同一份，避免切出几十字的碎片。

    第一遍把连续的小章合成一组（名为「首章～末章」）；第二遍把仍 < CHAPTER_FLOOR 的组并进
    后一章（作其引子，名随后一章），放不下再并进前一章（名随前一章）。单独成份的前言不参与。
    """
    groups: list[_Group] = []
    for lo, hi, title in pieces:
        c = _chars(units, lo, hi)
        last = groups[-1] if groups else None
        if (
            last is not None
            and title is not None
            and last.titles[0] is not None
            and c < CHAPTER_FLOOR
            and last.chars < CHAPTER_FLOOR
            and last.chars + c <= PART_MAX
        ):
            last.hi = hi
            last.titles.append(title)
            last.chars += c
        else:
            groups.append(_Group(lo, hi, [title], 0, c))  # type: ignore[list-item]
    k = 0
    while k < len(groups):
        g = groups[k]
        if g.chars >= CHAPTER_FLOOR or g.titles[0] is None or len(groups) == 1:
            k += 1
            continue
        nxt = groups[k + 1] if k + 1 < len(groups) else None
        prv = groups[k - 1] if k > 0 else None
        if nxt is not None and nxt.titles[0] is not None and nxt.chars + g.chars <= PART_MAX:
            groups[k + 1] = _Group(g.lo, nxt.hi, g.titles + nxt.titles, len(g.titles) + nxt.main, g.chars + nxt.chars)
            del groups[k]
        elif prv is not None and prv.titles[0] is not None and prv.chars + g.chars <= PART_MAX:
            groups[k - 1] = _Group(prv.lo, g.hi, prv.titles + g.titles, prv.main, prv.chars + g.chars)
            del groups[k]
        else:
            k += 1
    return groups


def _split_seg(units: list[_Unit], seg: _Seg, min_level: int) -> list[_Seg]:
    """§4.2 递归：返回叶子段列表（names 尚未编号，(0, 0, 名) 占位由调用方补）。"""
    levels = _headings_by_level(units, seg.lo, seg.hi, min_level)
    level = next((lv for lv in sorted(levels) if len(levels[lv]) >= 2), None)
    if level is not None:
        starts = levels[level]
        bounds = [seg.lo] + starts + [seg.hi]
        chapters: list[tuple[int, int, str]] = []  # (lo, hi, 标题)
        pre_lo, pre_hi = bounds[0], bounds[1]
        for k in range(1, len(bounds) - 1):
            chapters.append((bounds[k], bounds[k + 1], units[bounds[k]].title))
        if all(_chars(units, lo, hi) < CHAPTER_FLOOR for lo, hi, _ in chapters):
            level = None
        else:
            pieces: list[tuple[int, int, str | None]] = []
            if pre_hi > pre_lo and _chars(units, pre_lo, pre_hi) > 0:
                if _chars(units, pre_lo, pre_hi) < CHAPTER_FLOOR:
                    lo0, hi0, t0 = chapters[0]
                    chapters[0] = (pre_lo, hi0, t0)
                else:
                    pieces.append((pre_lo, pre_hi, None))
            elif pre_hi > pre_lo:  # 只有空行：并入第一章
                lo0, hi0, t0 = chapters[0]
                chapters[0] = (pre_lo, hi0, t0)
            pieces.extend((lo, hi, t) for lo, hi, t in chapters)
            groups = _pack(units, pieces)
            n = len(groups)
            leaves: list[_Seg] = []
            numbered = 0
            for g in groups:
                if g.label is None:
                    names = seg.names + [(0, n, "前言")]
                    path = seg.path + ["前言"]
                else:
                    numbered += 1
                    names = seg.names + [(numbered, n, g.name)]
                    path = seg.path + [g.label]
                child = _Seg(g.lo, g.hi, path, names, "chapter")
                if _chars(units, g.lo, g.hi) > PART_MAX:
                    leaves.extend(_split_seg(units, child, level + 1))
                else:
                    leaves.append(child)
            return leaves
    # 按大小拆
    ranges = _size_split(units, seg.lo, seg.hi)
    if len(ranges) == 1:
        return [seg]
    n = len(ranges)
    return [_Seg(lo, hi, seg.path, seg.names + [(k + 1, n, "")], "size") for k, (lo, hi) in enumerate(ranges)]


# ── 命名 ─────────────────────────────────────────────────────────────────────


def _width(total: int) -> int:
    return max(2, len(str(total)))


def _stem(sample: str, names: list[tuple[int, int, str]], name_max: int) -> str:
    parts = [sample]
    for no, total, title in names:
        parts.append(str(no).zfill(_width(total)))
        slug = raw_slug(title)[:name_max].strip("-.") if title and name_max > 0 else ""
        if slug:
            parts.append(slug)
    return "-".join(parts)


def _fit_stem(sample: str, names: list[tuple[int, int, str]]) -> str:
    """章名截到 NAME_MAX_CHARS；整个 basename（含 .md）仍超 BASENAME_MAX_BYTES 就继续缩章名，最后缩 sample。"""
    name_max = NAME_MAX_CHARS
    while True:
        stem = _stem(sample, names, name_max)
        if len((stem + ".md").encode("utf-8")) <= BASENAME_MAX_BYTES or name_max == 0:
            break
        name_max -= 1
    while len((stem + ".md").encode("utf-8")) > BASENAME_MAX_BYTES and sample:
        sample = sample[:-1].rstrip("-.")
        stem = _stem(sample, names, 0)
    return stem


def split_document(body: str, *, sample: str) -> list[Part]:
    """拆分正文。返回空列表 = 不拆（字数 ≤ SPLIT_MIN）。"""
    if count_chars(body) <= SPLIT_MIN:
        return []
    units = _units(body)
    leaves = _split_seg(units, _Seg(0, len(units), [], []), 1)
    if len(leaves) <= 1:
        return []
    # 每个单元起始时所在页（锚点之前的内容属于上一页）
    page_at: list[int | None] = []
    cur: int | None = None
    for u in units:
        page_at.append(cur)
        if u.page is not None:
            cur = u.page
    # 每个单元起始时所在的未闭合围栏（"" = 不在围栏内）。超过 PART_MAX 的代码块只能在围栏内硬切，
    # 渲染时据此给分卷补开 / 补闭围栏，否则代码行（含 `# 注释`）在分卷里会被当正文 / 标题。
    fence_at: list[str] = []
    in_fence, mark = False, ""
    for u in units:
        fence_at.append(mark if in_fence else "")
        if u.cut == _P_SENTENCE:  # 超长行切出的后续片段：不是行首，不可能是围栏线（同 `_units`）
            continue
        m_f = _FENCE_RE.match(u.text.rstrip("\r\n"))
        if in_fence:
            if fence_closes(mark, u.text.rstrip("\r\n")):
                in_fence = False
        elif m_f:
            in_fence, mark = True, m_f.group(1)
    fence_at.append(mark if in_fence else "")
    parts: list[Part] = []
    for leaf in leaves:
        text = "".join(u.text for u in units[leaf.lo : leaf.hi])
        start_page = page_at[leaf.lo] if leaf.lo < len(units) else None
        if leaf.lo < len(units) and units[leaf.lo].page is not None:
            start_page = units[leaf.lo].page
        last = start_page
        for u in units[leaf.lo : leaf.hi]:
            if u.page is not None:
                if start_page is None:  # 首个锚点之前的内容不属于任何页：从本份第一个锚点算起
                    start_page = u.page
                last = u.page
        pages = (start_page, last) if start_page is not None else None
        parts.append(
            Part(
                body=text,
                chars=_chars(units, leaf.lo, leaf.hi),
                path=list(leaf.path),
                method=leaf.method,
                name_segments=list(leaf.names),
                pages=pages,  # type: ignore[arg-type]
                extra={"fence_open": fence_at[leaf.lo], "fence_close": fence_at[leaf.hi]},
            )
        )
    total = len(parts)
    for k, p in enumerate(parts):
        p.stem = _fit_stem(sample, p.name_segments)
        p.index = k + 1
        p.total = total
    for k, p in enumerate(parts):
        p.prev = parts[k - 1].stem if k > 0 else None
        p.next = parts[k + 1].stem if k + 1 < total else None
    return parts


# ── 渲染 ─────────────────────────────────────────────────────────────────────


def rewrite_image_refs(text: str, prefix: str = "../") -> str:
    """分卷搬进 `parsed/<sample>/` 后，把本地相对图片引用加前缀（外链原样）。"""

    def repl(m: re.Match[str]) -> str:
        url = m.group("url")
        if _is_external_ref(url):
            return m.group(0)
        return f"{m.group('head')}{m.group('pre')}{prefix}{url}{m.group('post')})"

    return _MD_IMAGE.sub(repl, text)


def render_part(part: Part, *, sample: str, doc_meta: dict) -> str:
    """分卷文本：继承整本 frontmatter（title 加标题路径、chars 改本份），加 `split:`，并改写图片引用。"""
    doc_title = str(doc_meta.get("title") or sample)
    label = part.path[-1] if part.path else f"第 {part.index} 份"
    meta: dict = {}
    for key, value in doc_meta.items():  # 保留整本 frontmatter 的键序，只替换 title / chars
        if key == "title":
            meta[key] = f"{doc_title}·{label}"
        elif key == "chars":
            meta[key] = part.chars
        elif key != "split":
            meta[key] = value
    meta.setdefault("title", f"{doc_title}·{label}")
    meta.setdefault("chars", part.chars)
    split: dict = {
        "parent": sample,
        "index": part.index,
        "total": part.total,
        "path": list(part.path),
        "method": part.method,
    }
    if part.pages:
        split["pages"] = [part.pages[0], part.pages[1]]
    split["prev"] = part.prev
    split["next"] = part.next
    meta["split"] = split
    body = rewrite_image_refs(part.body)
    if not body.endswith("\n"):
        body += "\n"
    # 切在代码块中间（见 split_document 的 fence_at）：分卷各自补成闭合的围栏，`body` 本身仍是原文精确切片。
    if part.extra.get("fence_open"):
        body = f"{part.extra['fence_open']}\n{body}"
    if part.extra.get("fence_close"):
        body += f"{part.extra['fence_close']}\n"
    return dump_frontmatter(meta, body)


def render_index(parts: list[Part], *, sample: str, doc_meta: dict) -> str:
    """目录索引 `<sample>-目录.md`（§4.4）：只落 parsed/，不进 raw/、不入库。"""
    title = str(doc_meta.get("title") or sample)
    lines = [
        f"# {title} · 目录",
        "",
        f"共 {len(parts)} 份，总字数 {sum(p.chars for p in parts)}。本文件只留在暂存区，不入库。",
        "",
        "| 序号 | 文件 | 标题路径 | 页码 | 字数 |",
        "|---:|---|---|---|---:|",
    ]
    for p in parts:
        path = " / ".join(p.path) if p.path else "—"
        pages = f"{p.pages[0]}–{p.pages[1]}" if p.pages else "—"
        link = f"[{p.stem}](<{p.stem}.md>)"
        lines.append(f"| {p.index} | {link} | {path.replace('|', '｜')} | {pages} | {p.chars} |")
    meta = {"title": f"{title} · 目录", "split_index": sample, "parts": len(parts)}
    return dump_frontmatter(meta, "\n".join(lines) + "\n")


def index_name(sample: str) -> str:
    return f"{sample}-{INDEX_SUFFIX}.md"
