"""一键入库的规范化整理（P4.30 §3，决策P4.30-3/11/13）。**全部确定性、零 LLM、不写盘。**

输入转换器产出的 `<sample>.origin.md` 文本，返回 `(整理后文本, 操作日志)`：

1. **分页**（§3.2）：按 `<!-- page N of M -->` / `<!-- page N -->` 锚点切页；没有锚点 → 跳过 2–3，只写 frontmatter。
2. **去噪**（§3.3）：页码、页眉页脚（全书 ≥30%、奇偶分计、同行页码剥离、章节页眉连续 ≥3 次）、
   明确模式的水印。只动页首尾窗口里的命中行；标题/列表/表格/代码/引用/图片一概不当页码与水印删。
   证据不足一律保留，疑似项只记日志。
3. **跨页接句**（§3.4）：去噪之后再接，把被锚点切开的句子挪到长的一侧；锚点本身保留。
4. **frontmatter**（§3.1）：title / origin / parsed_by / source_sha256 / pages / chars / normalized。

阈值与窗口都是本模块的内置常量（决策P4.30-11：不暴露给用户）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from .pages import split_frontmatter
from .rawio import dump_frontmatter

__all__ = [
    "EDGE_WINDOW",
    "HEADER_MAX_CHARS",
    "HEADER_MIN_PAGES",
    "HEADER_MIN_RATIO",
    "CHAPTER_HEADER_RUN",
    "PAGE_ANCHOR_RE",
    "Op",
    "NormalizeResult",
    "count_chars",
    "normalize",
]

# ── 内置常量（§3.3；集中定义、带测试） ─────────────────────────────────────────
EDGE_WINDOW = 3  # 页首/页尾各看几个非空行
HEADER_MAX_CHARS = 60  # 页眉页脚/水印候选的最大非空白字数
HEADER_MIN_PAGES = 3  # 至少出现在几页
HEADER_MIN_RATIO = 0.30  # 至少占（同奇偶）有内容页面的比例
CHAPTER_HEADER_RUN = 3  # 章节页眉：同奇偶序列连续出现次数
PAGE_NUMBER_SLACK = 20  # 页码值上限 = 最大页号 + 余量（挡掉孤行的年份等大数字）

# 分页锚点：mineru `<!-- page 24 of 539 -->`；pypdf `<!-- page 3 -->` / `<!-- page 3 (no text) -->`。
PAGE_ANCHOR_RE = re.compile(r"^\s*<!--\s*page\s+(\d+)(?:\s+of\s+(\d+))?(?:\s*\([^)]*\))?\s*-->\s*$")

# 明确的水印模式（§3.3.1 第 3 步）：全行匹配，少而明确；泛化词（仅供学习/内部资料/网址/版权）不在此列。
_WATERMARK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:由)?\s*扫描全能王\s*扫描创建$"),
    re.compile(r"^(?:由)?\s*全能扫描王\s*扫描创建$"),
    re.compile(r"^Scanned\s+(?:with|by)\s+CamScanner$", re.IGNORECASE),
)

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
_IMAGE_LINE_RE = re.compile(r"^\s*!\[")
_COMMENT_RE = re.compile(r"^\s*<!--")
_HTML_TAG_RE = re.compile(r"^\s*</?[A-Za-z][^>]*>\s*$")

_ROMAN_RE = re.compile(r"^(?=[ivxlcdm]+$)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.IGNORECASE)
_PAGE_NUMBER_FORMS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[-–—]?\s*(\d{1,4})\s*[-–—]?$"),  # 15 / - 15 - / —15—
    re.compile(r"^第\s*(\d{1,4})\s*页$"),  # 第 15 页
    re.compile(r"^(\d{1,4})\s*/\s*\d{1,4}$"),  # 15/539
    re.compile(r"^[Pp](?:age|\.)?\s*(\d{1,4})$"),  # Page 15 / P.15
)
# 页码与页眉同行：行首/行尾、带空白分隔的数字字段（含全角空格）。
_LEAD_NUM_RE = re.compile(r"^(\d{1,4})[\s　]+(\S.*)$")
_TAIL_NUM_RE = re.compile(r"^(.*\S)[\s　]+(\d{1,4})$")

# 句末标点（§3.4）：末字是这些之一 → 不算被切开。`:`/`：` 结尾的列表引导同样不接。
_SENTENCE_END = set("。！？；…」』”）】.!?;:：\"')]")
# 断点标点（锚点后第一段的第一个）与句读标点（上一页残句的起点）。
_BREAK_PUNCT = set("，。；：！？,;:!?")
_CLAUSE_PUNCT = _BREAK_PUNCT | set("、")

_URL_PART_RE = re.compile(r"(!?\[[^\]]*\])\([^)]*\)")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Op:
    """一条整理操作（进作业输出 / 任务日志，供事后核对）。"""

    kind: str  # page_number | header | watermark | watermark_suspect | heading_kept | join
    page: int | None
    text: str
    rule: str = ""

    def describe(self) -> str:
        where = f"第 {self.page} 页" if self.page is not None else "—"
        label = {
            "page_number": "删页码",
            "header": "删页眉/页脚",
            "watermark": "删水印",
            "watermark_suspect": "疑似水印（保留）",
            "heading_kept": "保留首个同名标题",
            "join": "跨页接句",
        }.get(self.kind, self.kind)
        rule = f"［{self.rule}］" if self.rule else ""
        return f"{label} {where}{rule}：{self.text}"


@dataclass
class NormalizeResult:
    text: str  # 含 frontmatter 的完整文本（写 parsed/<sample>.md）
    body: str  # 去 frontmatter 的正文
    meta: dict  # frontmatter 映射
    ops: list[Op] = field(default_factory=list)
    title: str = ""
    pages: int | None = None
    chars: int = 0

    @property
    def summary(self) -> dict:
        return dict(self.meta.get("normalized") or {})


def count_chars(body: str) -> int:
    """字数口径（§4.1）：非空白字符数，不计分页锚点、markdown 图片/链接的 URL 部分。"""
    total = 0
    for line in body.split("\n"):
        if PAGE_ANCHOR_RE.match(line):
            continue
        line = _URL_PART_RE.sub(r"\1", line)
        total += len(_WS_RE.sub("", line))
    return total


def _nonws_len(s: str) -> int:
    return len(_WS_RE.sub("", s))


def _key(s: str) -> str:
    """页眉比对键：只去首尾空白、合并连续空白；保留数字与标点（§3.3）。"""
    return _WS_RE.sub(" ", s.strip())


def _page_number_value(s: str) -> int | None:
    t = s.strip()
    for pat in _PAGE_NUMBER_FORMS:
        m = pat.match(t)
        if m:
            return int(m.group(1))
    if _ROMAN_RE.match(t) and len(t) <= 7:
        return 0  # 罗马数字：值不参与上限判断
    return None


# ── 行分类 ───────────────────────────────────────────────────────────────────


def _classify(lines: list[str]) -> list[str]:
    """逐行标注结构：blank / anchor / fence（围栏内或围栏线）/ heading / list / table / quote / image / html / text。"""
    kinds: list[str] = []
    in_fence = False
    fence_mark = ""
    for line in lines:
        m = _FENCE_RE.match(line)
        if in_fence:
            kinds.append("fence")
            if m and m.group(1) == fence_mark:
                in_fence = False
            continue
        if m:
            in_fence, fence_mark = True, m.group(1)
            kinds.append("fence")
            continue
        s = line.strip()
        if not s:
            kinds.append("blank")
        elif PAGE_ANCHOR_RE.match(line):
            kinds.append("anchor")
        elif _HEADING_RE.match(line):
            kinds.append("heading")
        elif s.startswith("|"):
            kinds.append("table")
        elif s.startswith(">"):
            kinds.append("quote")
        elif _LIST_RE.match(line):
            kinds.append("list")
        elif _IMAGE_LINE_RE.match(line):
            kinds.append("image")
        elif _COMMENT_RE.match(line) or _HTML_TAG_RE.match(line):
            kinds.append("html")
        else:
            kinds.append("text")
    return kinds


@dataclass
class _Page:
    number: int
    lines: list[int]  # 本页非空、非锚点行的下标（文档序）


def _paginate(lines: list[str], kinds: list[str]) -> tuple[list[_Page], int | None]:
    pages: list[_Page] = []
    total: int | None = None
    max_no = 0
    cur: _Page | None = None
    for i, k in enumerate(kinds):
        if k == "anchor":
            m = PAGE_ANCHOR_RE.match(lines[i])
            assert m
            no = int(m.group(1))
            max_no = max(max_no, no)
            if m.group(2):
                total = int(m.group(2))
            cur = _Page(no, [])
            pages.append(cur)
        elif k != "blank" and cur is not None:
            cur.lines.append(i)
    if pages and total is None:
        total = max_no
    return pages, total


# ── 去噪（§3.3） ─────────────────────────────────────────────────────────────


def _strip_same_line_page_no(text: str, page_no: int) -> str | None:
    """同行页码（§3.3 补充二）：行首/行尾带分隔、且等于锚点页号的数字字段剥掉后的剩余；不匹配 → None。"""
    s = text.strip()
    m = _LEAD_NUM_RE.match(s)
    if m and int(m.group(1)) == page_no:
        return m.group(2)
    m = _TAIL_NUM_RE.match(s)
    if m and int(m.group(2)) == page_no:
        return m.group(1)
    return None


def _edge_hits(page: _Page) -> list[tuple[int, str]]:
    """页首尾窗口内的 (行下标, 位置 top|bottom)；短页首尾重叠时同一行记一次（优先 top）。"""
    top = page.lines[:EDGE_WINDOW]
    bottom = page.lines[-EDGE_WINDOW:]
    out = [(i, "top") for i in top]
    out += [(i, "bottom") for i in bottom if i not in top]
    return out


def _detect_noise(
    lines: list[str], kinds: list[str], pages: list[_Page], max_page: int
) -> tuple[dict[int, Op], list[Op]]:
    """返回 (待删行 → Op, 只记日志的 Op)。"""
    delete: dict[int, Op] = {}
    notes: list[Op] = []
    content_pages = [p for p in pages if p.lines]
    if not content_pages:
        return delete, notes

    # 1) 页码：首尾窗口内整行只有页码形态（非标题/列表/表格等结构行）。
    for p in content_pages:
        for i, _pos in _edge_hits(p):
            if kinds[i] not in ("text", "list"):
                continue
            v = _page_number_value(lines[i])
            if v is not None and v <= max_page + PAGE_NUMBER_SLACK:
                delete[i] = Op("page_number", p.number, lines[i].strip())

    # 2) 页眉/页脚：统计 (键, 位置) 出现在哪些页。标题行可参与（标题形式的页眉），其余结构行不参与。
    occ: dict[tuple[str, str], list[tuple[_Page, int]]] = {}
    for p in content_pages:
        seen: set[tuple[str, str]] = set()
        for i, pos in _edge_hits(p):
            if i in delete or kinds[i] not in ("text", "heading"):
                continue
            raw = lines[i].strip()
            stripped = _strip_same_line_page_no(raw, p.number) if kinds[i] == "text" else None
            key = _key(stripped if stripped is not None else raw)
            if not key or _nonws_len(key) > HEADER_MAX_CHARS:
                continue
            if (key, pos) in seen:
                continue
            seen.add((key, pos))
            occ.setdefault((key, pos), []).append((p, i))

    n_all = len(content_pages)
    parity_pages = {par: [p for p in content_pages if p.number % 2 == par] for par in (0, 1)}

    def qualifies(count: int, n: int) -> bool:
        return count >= HEADER_MIN_PAGES and n > 0 and count >= HEADER_MIN_RATIO * n

    for (key, pos), hits in occ.items():
        chosen: list[tuple[_Page, int, str]] = []
        if qualifies(len(hits), n_all):
            chosen = [(p, i, "全书重复") for p, i in hits]
        else:
            for par in (0, 1):
                ph = [(p, i) for p, i in hits if p.number % 2 == par]
                if qualifies(len(ph), len(parity_pages[par])):
                    chosen += [(p, i, "奇偶页重复") for p, i in ph]
                elif pos == "top" and not key.startswith("#"):
                    # 章节页眉：页首、同奇偶序列里连续 ≥ CHAPTER_HEADER_RUN 次（§3.3 补充三）。
                    # 只认页首的普通文本行：标题形式的页眉须达到全书/奇偶页门槛（决策P4.30-13），
                    # 否则连续几页各自小节开头的同名小标题（「（一）适用范围」）会被误删。
                    seq = parity_pages[par]
                    pos_of = {p.number: n for n, p in enumerate(seq)}
                    ordered = sorted(ph, key=lambda t: pos_of[t[0].number])
                    run: list[tuple[_Page, int]] = []
                    for item in ordered + [None]:  # type: ignore[list-item]
                        if run and (item is None or pos_of[item[0].number] != pos_of[run[-1][0].number] + 1):
                            if len(run) >= CHAPTER_HEADER_RUN:
                                chosen += [(p, i, "章节页眉") for p, i in run]
                            run = []
                        if item is not None:
                            run.append(item)
        for p, i, rule in chosen:
            delete[i] = Op("header", p.number, lines[i].strip(), rule)

    # 标题形式的页眉：文档里首个同文标题保留（通常就是真实的章/书名标题，§3.3 补充一）。
    first_heading: dict[str, int] = {}
    for i, k in enumerate(kinds):
        if k == "heading":
            first_heading.setdefault(_key(lines[i]), i)
    for i in list(delete):
        if kinds[i] == "heading" and first_heading.get(_key(lines[i])) == i:
            op = delete.pop(i)
            notes.append(Op("heading_kept", op.page, op.text, op.rule))

    # 3) 水印（§3.3.1）：独立短文本行候选（≥3 页且 ≥30%），再须全行命中明确模式才删。
    page_of: dict[int, int] = {}
    for p in content_pages:
        for i in p.lines:
            page_of[i] = p.number
    wm: dict[str, list[int]] = {}
    for i, k in enumerate(kinds):
        if k != "text" or i in delete or i not in page_of:
            continue
        standalone = (i == 0 or kinds[i - 1] in ("blank", "anchor")) and (
            i == len(kinds) - 1 or kinds[i + 1] in ("blank", "anchor")
        )
        key = _key(lines[i])
        if standalone and key and _nonws_len(key) <= HEADER_MAX_CHARS:
            wm.setdefault(key, []).append(i)
    for key, idxs in wm.items():
        npages = len({page_of[i] for i in idxs})
        if not qualifies(npages, n_all):
            continue
        if any(pat.match(key) for pat in _WATERMARK_PATTERNS):
            for i in idxs:
                delete[i] = Op("watermark", page_of[i], key, "明确模式")
        else:
            pages_list = sorted({page_of[i] for i in idxs})
            shown = ",".join(str(n) for n in pages_list[:10]) + ("…" if len(pages_list) > 10 else "")
            notes.append(Op("watermark_suspect", None, f"{key}（出现于第 {shown} 页）", "未命中明确模式"))
    return delete, notes


def _apply_deletions(lines: list[str], delete: set[int]) -> list[str]:
    """删命中行，并顺带删掉一个相邻空行，避免留下成串空行。"""
    drop = set(delete)
    n = len(lines)
    for i in sorted(delete):
        if i + 1 < n and not lines[i + 1].strip() and (i + 1) not in drop:
            drop.add(i + 1)
        elif i - 1 >= 0 and not lines[i - 1].strip() and (i - 1) not in drop:
            drop.add(i - 1)
    return [ln for i, ln in enumerate(lines) if i not in drop]


# ── 跨页接句（§3.4） ─────────────────────────────────────────────────────────


def _is_ascii_word(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _join(a: str, b: str) -> str:
    """接合两段：CJK 直接拼；拉丁字母间补空格；行尾连字符断词去掉连字符。"""
    if not a or not b:
        return a + b
    if a.endswith("-") and len(a) >= 2 and _is_ascii_word(a[-2]) and _is_ascii_word(b[0]):
        return a[:-1] + b
    if _is_ascii_word(a[-1]) and _is_ascii_word(b[0]):
        return a + " " + b
    return a + b


def _join_sentences(lines: list[str]) -> tuple[list[str], list[Op]]:
    ops: list[Op] = []
    kinds = _classify(lines)
    remove: set[int] = set()
    anchors = [i for i, k in enumerate(kinds) if k == "anchor"]
    for a in anchors:
        j = a - 1
        while j >= 0 and kinds[j] == "blank":
            j -= 1
        k = a + 1
        while k < len(lines) and kinds[k] == "blank":
            k += 1
        if j < 0 or k >= len(lines) or j in remove or k in remove:
            continue
        # 上一段须是正文/列表项；锚点后第一段须是普通正文（不是标题、列表、表格、图片、引用块）。
        if kinds[j] not in ("text", "list") or kinds[k] != "text":
            continue
        prev = lines[j].rstrip()
        nxt = lines[k].lstrip()
        if not prev or not nxt or prev[-1] in _SENTENCE_END:
            continue
        m = PAGE_ANCHOR_RE.match(lines[a])
        page_no = int(m.group(1)) if m else None
        # head = 锚点后第一段到第一个断点标点为止；找不到 → 整段。
        cut = next((n for n, ch in enumerate(nxt) if ch in _BREAK_PUNCT), None)
        if cut is None:
            head, rest = nxt, ""
        else:
            head, rest = nxt[: cut + 1], nxt[cut + 1 :].lstrip()
        head_len = _nonws_len(head.rstrip("".join(_BREAK_PUNCT)))
        last = max((n for n, ch in enumerate(prev) if ch in _CLAUSE_PUNCT), default=-1)
        residual = prev[last + 1 :]
        if _nonws_len(residual) >= head_len:
            lines[j] = _join(prev, head)
            ops.append(Op("join", page_no, head, "后段前移"))
            if rest:
                indent = lines[k][: len(lines[k]) - len(lines[k].lstrip())]
                lines[k] = indent + rest
            else:
                remove.add(k)
        else:
            moved = residual.lstrip()
            if not moved:  # 上一页恰好停在逗号等句读处：没有残句可挪
                continue
            kept = prev[: last + 1]
            lines[k] = lines[k][: len(lines[k]) - len(nxt)] + _join(moved, nxt)
            ops.append(Op("join", page_no, moved, "残句后移"))
            if kept.strip():
                lines[j] = kept
            else:
                remove.add(j)
        kinds = _classify(lines)
    if remove:
        lines = _apply_deletions(lines, remove)
    return lines, ops


# ── 入口 ─────────────────────────────────────────────────────────────────────


def _first_h1(body: str) -> str | None:
    in_fence = False
    for line in body.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            m = re.match(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", line)
            if m:
                return m.group(1).strip()
    return None


def _split_meta(text: str) -> tuple[dict, str]:
    """切出已有 frontmatter（键值映射）；坏块/非映射 → 视为无块，全文当正文。"""
    block, body = split_frontmatter(text)
    if block is None:
        return {}, text
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}, text
    if meta is None:
        return {}, body
    if not isinstance(meta, dict):
        return {}, text
    return meta, body


def normalize(
    text: str,
    *,
    title_hint: str,
    origin: str | None = None,
    parsed_by: str | None = None,
    source_sha256: str | None = None,
) -> NormalizeResult:
    """规范化整理（§3）。顺序固定：分页 → 去噪 → 跨页接句 → 统计正文、写元数据。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    old_meta, body = _split_meta(text)
    lines = body.split("\n")
    kinds = _classify(lines)
    pages, total = _paginate(lines, kinds)

    ops: list[Op] = []
    if pages:
        delete, notes = _detect_noise(lines, kinds, pages, total or 0)
        ops.extend(delete[i] for i in sorted(delete))
        ops.extend(notes)
        lines = _apply_deletions(lines, set(delete))
        lines, join_ops = _join_sentences(lines)
        ops.extend(join_ops)
    new_body = "\n".join(lines)

    title = _first_h1(new_body) or str(old_meta.get("title") or "").strip() or title_hint
    chars = count_chars(new_body)
    summary = {
        "headers_removed": sum(1 for o in ops if o.kind == "header"),
        "page_numbers_removed": sum(1 for o in ops if o.kind == "page_number"),
        "watermarks_removed": sum(1 for o in ops if o.kind == "watermark"),
        "sentences_joined": sum(1 for o in ops if o.kind == "join"),
    }
    meta: dict = {"title": title}
    if origin:
        meta["origin"] = origin
    pb = parsed_by or old_meta.get("parsed_by")
    if pb:
        meta["parsed_by"] = pb
    if source_sha256:
        meta["source_sha256"] = source_sha256
    if total:
        meta["pages"] = total
    meta["chars"] = chars
    meta["normalized"] = summary
    for key, value in old_meta.items():  # 转换器自带的其它键原样保留在后面
        meta.setdefault(key, value)

    full = dump_frontmatter(meta, new_body.lstrip("\n"))
    return NormalizeResult(
        text=full,
        body=new_body.lstrip("\n"),
        meta=meta,
        ops=ops,
        title=title,
        pages=total,
        chars=chars,
    )
