"""引语核对 `guanlan quotes`（P3.14，见 docs/P3.14-引语核对.md）。**零 LLM、只读。**

逐页取出引号内的文字，到它出处所指的 raw 原文里逐字查找，对不上的按成因分类报告。默认退出码 0；
`--strict` 只对一档（`altered` ∧ 无 OCR 提示 ∧ 非 `label_like`，两轮抽样精确率 85%）退 6；raw 遍历不完整时
`--strict` 也退 6（决策P3.14-7）。

**候选集（封闭，新形态改本 docstring，不改正则；决策P3.14-2）**：
- 收：`“…”`、`"…"` 与 `「…」` 引号内文字，归一后 ≥ `MIN_LEN` 字；`--no-corner` 不收 `「…」`（初版默认不收，
  模型用「」写的直接引语因此整批漏查，见 docs/P3.14 §14）。
- 不收：围栏代码块内（含 mermaid）、行内 code 内（其中的引号也不参与配对）、`overview.md` 以外的 config 页、序列标签（§3.5：按 → ↔ ⇒ — ／ 切成 ≥3 段且每段
  归一后 ≤6 字、段内无标点）。
- 不收日期、数字（另行度量）。

**引号配对（§3.1）**：两态逐字扫描。引语外遇 `“`/`"` 开、遇 `”` 计一次未闭合；`“` 打开的引语内遇
`”`/`"` 闭合、再遇 `“` 视为前一个未闭合并以新的 `“` 重开；`"` 打开的引语内允许内嵌 `“…”`，嵌套外遇
`"`/`”` 闭合；嵌套中的 `“` 没闭合就遇到 `"`，直引号闭合外层并计一次未闭合；行尾仍在引语内计一次未闭合。未闭合只计数、不报 finding。

**归一（§3.2）**：引语侧先按 `……`/`…`/`...` 切分（另认 `⋯`、全角 `．．．`、输入法常见的 `···` 与 `。。。`：切分在 NFKC 之前，不会被归一成 `...`），再逐段 `norm()`；raw 侧先剥 frontmatter 再 `norm()`。
`norm()` 两侧共用：去上标数字（须在 NFKC 前）→ NFKC → 链接语法取显示文本 → 去 `[1]`～`[999]` 脚注 →
去 `(未验证)` 标记 → 删全部标点与空白。不做繁简与数字写法互换。

**命中（§3.3）**：全部片段在**同一份** raw 中依次、不重叠出现。**覆盖率（§3.4）**：单份 raw 上 4 字
片段的命中比例，取各候选 raw 的最大值，不跨 raw 合计。

**出处范围（§4，决策P3.14-3/6）**，以一行为一个条目：
- 引用标记：`[[X]]:N`（带名）、`` `raw/...md`:N ``（带名，指 raw 路径）、单独的 `:N`（裸行号）；
- 引语之后、同一句内（以 `。`、`；` 或行尾为界）的第一个引用标记定出处：带名 → 在它的 raw 里核
  （`scope: cite`）；裸行号 → 承接本行此前最近的带名引用，没有则回退本页主资料（`scope: inherited`）；
- 句内没有引用标记 → 本行反引号里的 raw 路径 ∪ 本页主资料（`scope: page`）。本页主资料：source 页
  只取它自己的 raw（读不到即 `no_source`，不回退），其他页取 frontmatter `sources` 所指 source 页的 raw；
- 后面不跟 `:N` 的 `[[链接]]` 不作出处；出处到 raw 的映射复用 `sourcepage.claimed_raw_paths`；
- 带名出处映射不到任何可读 raw → `source_unreadable`，不回退、不扩大范围；
- **出处只用来缩小核对范围**：范围外逐字命中算措辞属实，记 `misattributed`，只计数，
  `--show-misattributed` 才逐条列出，恒不计入 strict。

**OCR 提示（§5.4）**：`ocr_hint` 原样展示命中 raw 的 frontmatter 中 `parsed_by` 行与含 `ocr` 字样的行；
只有提示文本里出现 `ocr` 这个词（不分大小写，以它为词头或词尾——至少一侧不紧挨英文字母：`pdf-ocr`、
`PaddleOCR`、`本地OCR` 算，`Democracy` 不算）时才视为 OCR 来源、不计入 strict——不把后端名等同于用了 OCR。
"""

from __future__ import annotations

import argparse
import collections
import os
import posixpath
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .errors import EXIT_LINT_FINDINGS, EXIT_OK, GuanlanError
from .pages import (
    _CONFIG_PAGES,
    _split_md_lines,
    mask_code_spans,
    iter_pages,
    link_resolution_index,
    load_page_text,
    report_dict,
    resolve_owner,
    split_frontmatter,
    strip_html_comments,
    validate_page_arg,
)
from .paths import require_kb_root
from .search import positive_int
from .sourcepage import claimed_raw_paths

__all__ = [
    "MIN_LEN",
    "COVERAGE_THRESHOLD",
    "QuoteFinding",
    "QuotesReport",
    "norm",
    "segments",
    "pair_quotes",
    "run_quotes",
    "format_report",
    "quotes_entrypoint",
    "main",
]

MIN_LEN = 15
# config 页里唯一写正文的一页：活体综述，ingest 与问答都会读，引语同样要核对（P3.14 §15）。
_CHECKED_CONFIG = frozenset({"overview.md"})
COVERAGE_THRESHOLD = 0.6
TOP_PAGES = 15
STRICT_LIST = 50

# ---------- §3.2 归一 ----------
_LINK_RE = re.compile(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
# 上标数字须在 NFKC **之前**去掉：NFKC 会把 `¹` 归一成普通 `1`，之后就分不出是脚注还是正文数字了。
_SUPERSCRIPT_RE = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_FOOTNOTE_RE = re.compile(r"\[\d{1,3}\]")
# Markdown 链接与图片 `[文字](url)` / `![alt](url)`：只留文字。convert 产出的 raw 常带链接，不剥则 URL 混进正文。
_MD_LINK_RE = re.compile(r"!?\[([^\[\]]*)\]\([^()\s]*(?:\s+\"[^\"]*\")?\)")
_UNVERIFIED_RE = re.compile(r"[（(]未验证[）)]")
_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)
# 切分在 norm()（含 NFKC）之前，故 `⋯`（部分输入法的省略号）与全角 `．．．` 须显式列出，否则被当标点删掉、
# 片段被拼成一段，原文里不相连的两句引语就判成 altered（且计入 strict）。
_ELLIPSIS_RE = re.compile(r"……|…|⋯+|\.{3,}|．{3,}|·{3,}|。{3,}")


def norm(text: str) -> str:
    """两侧共用的归一（§3.2 第 2 步）：去上标 → NFKC → 链接取显示文本（`[[…]]` 与 Markdown `[文字](url)`）→ 去脚注编号 → 去 `(未验证)` → 删标点空白。"""
    text = unicodedata.normalize("NFKC", _SUPERSCRIPT_RE.sub("", text))
    # `[[x|y]]` 取 `y`；`[[x#小节]]` 取 `x`（锚点不是措辞，不去掉就会被拼进正文）。`**`/`__` 强调由末步删标点一并去掉。
    text = _LINK_RE.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(1).split("#", 1)[0], text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _FOOTNOTE_RE.sub("", text)
    text = _UNVERIFIED_RE.sub("", text)
    return _PUNCT_RE.sub("", text)


def segments(quote: str) -> list[str]:
    """引语侧：先按省略号切分，再逐段 `norm()`（顺序不可颠倒，否则省略号先被当标点删掉）。"""
    return [s for s in (norm(p) for p in _ELLIPSIS_RE.split(quote)) if s]


# ---------- §3.1 引号配对 ----------
def pair_quotes(line: str) -> tuple[list[tuple[str, int, int]], int]:
    """返回 `([(引语, 开引号位置, 闭引号位置)], 未闭合计数)`。规则见模块 docstring。"""
    out: list[tuple[str, int, int]] = []
    unclosed = 0
    start = 0
    opener: str | None = None
    nested = False
    for i, ch in enumerate(line):
        if opener is None:
            if ch in "“\"":
                opener, start = ch, i
            elif ch == "”":
                unclosed += 1
        elif opener == '"':
            if nested:
                if ch == "”":
                    nested = False
                elif ch == '"':  # 内嵌的 “ 没闭合就遇到直引号：直引号闭合外层，免得吞掉本行其余引语
                    out.append((line[start + 1 : i], start, i))
                    opener, nested = None, False
                    unclosed += 1
            elif ch == "“":
                nested = True
            elif ch in "\"”":
                out.append((line[start + 1 : i], start, i))
                opener = None
        elif ch in "”\"":
            out.append((line[start + 1 : i], start, i))
            opener = None
        elif ch == "“":
            unclosed += 1
            start = i
    if opener is not None:
        unclosed += 1
    return out, unclosed


# ---------- §3.5 结构标签 ----------
_SEQ_SEP_RE = re.compile(r"→|↔|⇒|—+|／")
_SEG_PUNCT_RE = re.compile(r"[，。；：、,.;:！？!?]")
_LABEL_LIKE_RE = re.compile(r"[→↔⇒＋＝／Ｘ]|—|(?<![A-Za-z])X(?![A-Za-z])")


def is_sequence_label(quote: str) -> bool:
    """序列标签：按分隔符切成 ≥3 段，且每段归一后 ≤6 字、段内无标点。"""
    parts = [p for p in _SEQ_SEP_RE.split(quote) if p.strip()]
    if len(parts) < 3:
        return False
    return all(len(norm(p)) <= 6 and not _SEG_PUNCT_RE.search(p) for p in parts)


def is_label_like(quote: str) -> bool:
    """含结构符号或占位符：照常核对，只降级出 strict。"""
    return bool(_LABEL_LIKE_RE.search(quote))


# ---------- §3.3 命中 / §3.4 覆盖率 ----------
def ordered_hit(segs: list[str], text: str) -> bool:
    pos = 0
    for seg in segs:
        i = text.find(seg, pos)
        if i < 0:
            return False
        pos = i + len(seg)
    return True


def coverage(segs: list[str], text: str) -> float:
    """§3.4：4 字片段只在**各段内部**取——跨省略号边界的片段在原文中本就不该相连，算进去会压低覆盖率。"""
    grams = [g for seg in segs for g in ([seg[i : i + 4] for i in range(len(seg) - 3)] or [seg])]
    return sum(g in text for g in grams) / len(grams) if grams else 0.0


# ---------- §4 引用标记 ----------
_NAMED_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]\s*[:：](\d+)")
# 扩展名不分大小写：ingest 按 `suffix.lower()` 收 `.MD`，这里只认小写会把它的引用当成不存在。
_RAW_CITE_RE = re.compile(r"`(raw/[^`]+?\.(?i:md))`\s*[:：](\d+)")
# 冒号前紧挨 `”`、`）`、`)` 的不算裸行号：「”：2024 年起」「（二）：1.」是正文，不是出处。
# `（同上:N）`/`（同篇:N）` 例外：`上`、`篇` 是 \w，不放行就漏掉这种裸行号，引语被降成 page 范围。
_BARE_RE = re.compile(r"(?:(?<![\w\]`:：”）)])|(?<=同上)|(?<=同篇))[:：](\d+)")
_RAW_PATH_RE = re.compile(r"`(raw/[^`]+?\.(?i:md))`")
# 「配对存疑」只认明确的出处形态：带名引用、raw 路径引用、括号里的裸行号；不认「（二）：1.」这类正文编号。
_SUSPECT_RE = re.compile(r"\[\[[^\]]+\]\]\s*[:：]\d|`raw/[^`]+`\s*[:：]\d|[（(]\s*(?:同上|同篇)?\s*[:：]\d")
_SENTENCE_END = ("。", "；")


def cite_marks(line: str) -> list[tuple[int, str, str | None]]:
    """本行引用标记：`[(位置, 'named'|'raw'|'bare', 目标)]`，按位置排序。"""
    marks: list[tuple[int, str, str | None]] = []
    taken: set[int] = set()
    for m in _NAMED_RE.finditer(line):
        marks.append((m.start(), "named", m.group(1).strip()))
        taken.add(m.end())
    for m in _RAW_CITE_RE.finditer(line):
        marks.append((m.start(), "raw", m.group(1)))
        taken.add(m.end())
    for m in _BARE_RE.finditer(line):
        if m.end() not in taken:
            marks.append((m.start(), "bare", None))
    return sorted(marks)


# ---------- raw 语料 ----------
# 以 `ocr` 为词头或词尾（`pdf-ocr`、`PaddleOCR`、`ocr_engine`），不吃 `democracy`、`Socrates` 这种词中的字串。
_OCR_RE = re.compile(r"(?<![a-z])ocr|ocr(?![a-z])", re.IGNORECASE)


class _RawStore:
    """全部 raw 的正文（已剥 frontmatter）与 frontmatter 原文；遍历口径同 `health._raw_anchor_stems`。

    遍历时只读盘、解码（读不出的照旧当场记入 `unreachable`，`--strict` 与 scan_incomplete 的口径不变）；
    `norm()` 是大头（全库 raw 归一占 `--page` 耗时九成），推迟到 `get()` 首次取用时做并缓存。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        # 值先是解码后的原始正文，首次 `get()` 时换成归一正文；`_normed` 记哪些已换。
        self._text: dict[str, str] = {}
        self._normed: set[str] = set()
        self.meta: dict[str, str] = {}
        # 真实路径 → 已入库的键：防环会跳过「指向已遍历目录的链接目录」，经另一条路径写的声明靠它找回。
        self.real: dict[str, str] = {}
        self.unreachable: list[str] = []
        raw = root / "raw"
        if not raw.is_dir():
            if raw.is_symlink():
                self.unreachable.append("raw")
        else:
            self._walk(raw)

    def _rel(self, path: str) -> str:
        return Path(os.path.relpath(path, self.root)).as_posix()

    def _walk(self, raw: Path) -> None:
        seen: set[str] = set()

        def on_error(exc: OSError) -> None:
            self.unreachable.append(self._rel(exc.filename) if exc.filename else "raw")

        for dirpath, dirnames, filenames in os.walk(raw, onerror=on_error, followlinks=True):
            real = os.path.realpath(dirpath)
            if real in seen:
                dirnames[:] = []
                continue
            seen.add(real)
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                path = os.path.join(dirpath, name)
                # 断开的链接**不论扩展名**都算没遍历到（同 health）：`raw/archive` 这种指向未挂载盘的
                # 目录链接没有 `.md` 后缀，先按扩展名筛掉就会静默漏报、把本该 scan_incomplete 的判成 altered。
                if os.path.islink(path) and not os.path.exists(path):
                    self.unreachable.append(self._rel(path))
                    continue
                if not name.lower().endswith(".md") or not os.path.isfile(path):
                    continue
                # 键取 NFC 形（同 health）：macOS Finder 落盘的文件名可能是 NFD，Linux 不做规范化，逐字比会把
                # NFC 写的声明判成 no_source / source_unreadable。查找侧经 `key()` 同样归一。
                rel = unicodedata.normalize("NFC", self._rel(path))
                try:
                    # utf-8-sig：带 BOM 的 raw 否则认不出 frontmatter，`parsed_by` 等 OCR 提示丢失，OCR 来源的 altered 被误计入 strict。
                    content = Path(path).read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    self.unreachable.append(rel)
                    continue
                block, body = split_frontmatter(content)
                self._text[rel] = body
                self.meta[rel] = block or ""
                self.real.setdefault(os.path.realpath(path), rel)

    def get(self, rel: str) -> str | None:
        """归一后的正文；不在库里 → None。"""
        if rel not in self._text:
            return None
        if rel not in self._normed:
            self._text[rel] = norm(self._text[rel])
            self._normed.add(rel)
        return self._text[rel]

    def keys(self) -> list[str]:
        """全部已读入 raw 的键，遍历序。"""
        return list(self._text)

    def key(self, claimed: str) -> str | None:
        """页内写的 raw 路径 → 已读入 raw 的键（normpath + NFC，与入库口径一致）；读不到 → None。"""
        k = unicodedata.normalize("NFC", posixpath.normpath(claimed))
        if k in self._text:
            return k
        # 防环按真实路径只走一遍：`raw/alias -> sub` 先被遍历时，`raw/sub/x.md` 不在键里，但它是同一份原文。
        if not k.startswith("raw/"):
            return None
        try:
            return self.real.get(os.path.realpath(self.root / k))
        except (OSError, ValueError):
            return None

    def ocr_hint(self, rel: str) -> list[str]:
        lines = self.meta.get(rel, "").splitlines()
        return [ln.strip() for ln in lines if re.match(r"\s*parsed_by\s*:", ln) or _OCR_RE.search(ln)]


# ---------- 报告 ----------
@dataclass
class QuoteFinding:
    page: str
    line: int
    kind: str
    quote: str
    scope: str
    label_like: bool
    checked_raw: list[str]
    strict: bool
    strict_reason: str
    best_raw: str | None = None
    coverage: float | None = None
    hit_raw: str | None = None
    ocr_hint: list[str] | None = None


@dataclass
class QuotesReport:
    ok: bool
    pages_checked: int
    findings: list[QuoteFinding]
    counts: dict[str, int] = field(default_factory=dict)
    unclosed_quotes: int = 0
    raw_unreachable: list[str] = field(default_factory=list)
    strict_mode: bool = False
    single_page: bool = False


_REASONS = {
    "strict": "改字、缩写或拼接（非 OCR、非标签形态）",
    "label_like": "含结构符号或占位符，可能是标签而非引语",
    "ocr": "所核原文带 OCR 提示，差异可能来自识别错字",
    "unsupported": "原文中找不到：多为转述、标签或引本库措辞，未达度量门槛",
    "misattributed": "措辞逐字属实，出处未能核实",
    "unchecked": "未能核对",
}


def _strict_of(kind: str, label_like: bool, ocr: list[str]) -> tuple[bool, str]:
    if kind == "altered":
        if label_like:
            return False, _REASONS["label_like"]
        if any(_OCR_RE.search(h) for h in ocr):
            return False, _REASONS["ocr"]
        return True, _REASONS["strict"]
    if kind == "unsupported":
        return False, _REASONS["unsupported"]
    if kind == "misattributed":
        return False, _REASONS["misattributed"]
    return False, _REASONS["unchecked"]


def run_quotes(
    root: Path,
    *,
    page: str | None = None,
    min_len: int = MIN_LEN,
    include_corner: bool = True,
    show_misattributed: bool = False,
    strict: bool = False,
) -> QuotesReport:
    """核对全库（或 `page` 一页）的引语，返回结构化报告。`root` 为知识库根。"""
    wiki = root / "wiki"
    idx = link_resolution_index(wiki)
    raws = _RawStore(root)
    claims: dict[str, list[str]] = {}
    owners: dict[str, str | None] = {}

    def claims_of(page_rel: str) -> list[str]:
        if page_rel not in claims:
            # utf-8-sig 同主循环：`load_page` 按 utf-8 读、不剥 BOM，带 BOM 的 source 页认不出 frontmatter，
            # `raw_digest` 丢失；结果还会进缓存，主循环轮到这张 source 页时也沿用这份错的声明。
            text = (root / page_rel).read_text(encoding="utf-8-sig", errors="replace")
            claims[page_rel] = own_raws(*load_page_text(text))
        return claims[page_rel]

    def own_raws(meta: dict | None, body: str) -> list[str]:
        return sorted({k for r in claimed_raw_paths(meta, body) if (k := raws.key(r))})

    def source_owner(target: str) -> str | None:
        # 同一出处名在全库反复出现（frontmatter sources、`[[x]]:N`），按名缓存，免得逐页逐条重复 stat。
        if target not in owners:
            owners[target] = _source_owner(target)
        return owners[target]

    def _source_owner(target: str) -> str | None:
        # 出处一律指 source 页：先按 `check._check_sources` 的口径直取 `wiki/sources/<slug>.md`，
        # 再退回通用解析。只走 `resolve_owner` 时，同名的 entity/concept 页（health.duplicate_stem）
        # 排序在前会抢走解析，source 被当成「非 source 页」丢掉，引语误判 no_source/source_unreadable。
        # 限定写法 `[[sources/x]]`、`[[wiki/sources/x.md]]` 同样直取：只走 `resolve_owner` 会按末段取同名页里
        # 排序最前的 concepts/x，把明确写了 sources/ 的出处判成 source_unreadable。其他目录前缀不直取。
        name = target.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/")
        if name.lower().endswith(".md"):
            name = name[:-3]
        head, _, name = name.removeprefix("wiki/").rpartition("/")
        if head in ("", "sources") and name and name not in (".", ".."):
            try:
                # Linux 上超长名（模型臆造的 `[[…]]:N`、`--page`）让 `is_file` 抛 ENAMETOOLONG，当没命中（同 health）。
                hit = (wiki / "sources" / f"{name}.md").is_file()
            except OSError:
                hit = False
            if hit:
                return f"wiki/sources/{name}.md"
        owner = resolve_owner(target, idx)
        return owner if owner and owner.startswith("wiki/sources/") else None

    def target_raws(kind: str, target: str) -> list[str]:
        if kind == "raw":
            k = raws.key(target)
            return [k] if k else []
        owner = source_owner(target)
        return claims_of(owner) if owner else []

    if page is not None:
        # 同名页（health.duplicate_stem）时与出处解析同序：先取 source 页，再退回通用解析。
        # 带目录的 `--page entities/x` 先按路径直取：按名解析只看末段，同名页时会静默换成另一页。
        owner = _direct_page(root, page) or source_owner(page) or resolve_owner(page, idx)
        if owner is not None and "/" in page.replace("\\", "/"):
            # 路径形直取未中时，按名解析只看末段：`--page entities/x` 不存在会静默换成 concepts/x 或 sources/x。
            # 只认与所写路径（不分大小写）一致的结果——`Sources/x` 照收，另一目录的同名页不收。
            want = posixpath.normpath("wiki/" + page.replace("\\", "/").removeprefix("wiki/"))
            if owner.removesuffix(".md").casefold() != want.removesuffix(".md").casefold():
                owner = None
        if owner is None or not (root / owner).is_file():
            raise GuanlanError(f"找不到页面：{page}")
        if "/" not in owner.removeprefix("wiki/") and owner.rsplit("/", 1)[-1] in _CONFIG_PAGES - _CHECKED_CONFIG:
            raise GuanlanError(f"{owner} 是 config 页，不核对引语")
        paths = [root / owner]
    else:
        paths = sorted([*iter_pages(wiki), *(p for n in _CHECKED_CONFIG if (p := wiki / n).is_file())])

    counts: collections.Counter[str] = collections.Counter()
    findings: list[QuoteFinding] = []
    unclosed = 0
    for path in paths:
        rel = path.relative_to(root).as_posix()
        # utf-8-sig：BOM 不占行，行号不变；不剥则认不出 frontmatter，`sources` 丢失、整页误判 no_source。
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        meta, body = load_page_text(text)
        if rel.startswith("wiki/sources/"):
            # source 页只认自己的 raw；读不到就是 no_source，不回退到 frontmatter sources 扩大范围（§4）。
            # 已读入的正文直接算并入缓存，免得 claims_of 再读一遍盘。
            if rel not in claims:
                claims[rel] = own_raws(meta, body)
            main_raws = claims[rel]
        else:
            srcs = (meta or {}).get("sources")
            # 非字符串条目（`null`、数字）不是来源名，同 `check._check_sources` 跳过；`str(None)` 会去解析名为 None 的页。
            page_srcs = [o for s in (srcs if isinstance(srcs, list) else [])
                         if isinstance(s, str) and (o := source_owner(s))]
            main_raws = sorted({r for p in page_srcs for r in claims_of(p)})
        # 切行与围栏口径复用 pages（markdown 行尾、只认闭合且逐字对称的列 0 围栏；未闭合不吞后文）。
        body_lines = _split_md_lines(body)
        offset = len(_split_md_lines(text)) - len(body_lines)
        # 引号配对在**另抹了行内 code** 的副本上做（等长、列偏移不变）：code 里落单的 `"` 不得翻转本行
        # 其后的配对状态；引语文字与引用标记仍从未抹行取（`` `raw/...md`:N `` 本身就在反引号里）。
        # 整行 HTML 注释同抹（口径同 `link_scan_text`）：注释掉的引语渲染后不可见，不该被核对。
        masked_lines = _split_md_lines(mask_code_spans(strip_html_comments(body)))
        # 围栏只在 masked 上抹一次（mask_code_spans 已含，且在抹注释之后、口径与配对一致）：候选位置全取自
        # masked，围栏行不会产生候选，`line` 只在有候选时读，无须再抹一遍。
        for i, line in enumerate(body_lines):
            line = line.rstrip("\r\n")
            masked = masked_lines[i].rstrip("\r\n")
            spans, n_unclosed = pair_quotes(masked)
            unclosed += n_unclosed
            quotes = [(line[s + 1 : e], s, e) for _q, s, e in spans]
            corners = [(line[m.start() + 1 : m.end() - 1], m.start(), m.end() - 1)
                       for m in re.finditer(r"「([^「」\n]+)」", masked)] if include_corner else []
            # 两种引号互相嵌套时只核外层，内层随外层一起核对，不重复计数。
            both = quotes + corners
            cands = [c for c in both if not any(o is not c and o[1] < c[1] and c[2] < o[2] for o in both)]
            cands = [c for c in cands if len(norm(c[0])) >= min_len and not is_sequence_label(c[0])]
            # 原文引语里不会出现本库的出处标记；出现了，多半是配对错位抓到了两段引语之间的叙述
            suspect = [c for c in cands if _SUSPECT_RE.search(c[0])]
            if suspect:
                counts["pairing_suspect"] += len(suspect)
                cands = [c for c in cands if c not in suspect]
            if not cands:
                continue
            # 落在其他引语内文里的标记不是本句的出处（`“甲”又说“乙[[y]]:2”`：前一条不得借后一条内文里的 y）。
            inner = [(s, e) for _q, s, e in quotes + corners]
            # 行内 code 里的 `[[x]]:N`/`:N` 是写法示例、不是出处（`raw` 形本身就在反引号里，照收）。
            marks = [m for m in cite_marks(line) if not any(s < m[0] < e for s, e in inner)
                     and (m[1] == "raw" or masked[m[0]] == line[m[0]])]
            # 定句界用的副本：行内 code 已抹，各引语内部也抹成空白——后一条引语自带的 `。`（`又说“好。”[[x]]:1`）
            # 不是本句的句界，否则前一条引语够不着句末的带名出处，被降成 page 范围、错判 unsupported。
            stop_chars = list(masked)
            for _q, s, e in quotes + corners:
                stop_chars[s + 1 : e] = " " * (e - s - 1)
            stop_line = "".join(stop_chars)
            line_raws = [k for r in _RAW_PATH_RE.findall(line) if (k := raws.key(r))]
            for quote, _start, end in cands:
                counts["candidates"] += 1
                scope, checked, unreadable = _scope(marks, end, stop_line, main_raws, line_raws, target_raws)
                segs = segments(quote)
                label_like = is_label_like(quote)
                base = dict(page=rel, line=offset + i + 1, quote=quote, scope=scope,
                            label_like=label_like, checked_raw=checked)
                if not checked:
                    kind = "source_unreadable" if unreadable else "no_source"
                    counts[kind] += 1
                    findings.append(QuoteFinding(kind=kind, strict=False, strict_reason=_REASONS["unchecked"], **base))
                    continue
                if any(ordered_hit(segs, raws.get(r) or "") for r in checked):
                    counts["passed"] += 1
                    continue
                if raws.unreachable:
                    counts["scan_incomplete"] += 1
                    findings.append(QuoteFinding(kind="scan_incomplete", strict=False,
                                                 strict_reason=_REASONS["unchecked"], **base))
                    continue
                # 范围内的 raw 刚核过、必不命中，不再重扫。
                hit = next((r for r in raws.keys() if r not in checked and ordered_hit(segs, raws.get(r) or "")), None)
                if hit is not None:
                    counts["misattributed"] += 1
                    if show_misattributed:
                        findings.append(QuoteFinding(kind="misattributed", strict=False,
                                                     strict_reason=_REASONS["misattributed"], hit_raw=hit,
                                                     ocr_hint=raws.ocr_hint(hit), **base))
                    continue
                cov, best = max((coverage(segs, raws.get(r) or ""), r) for r in checked)
                kind = "altered" if cov >= COVERAGE_THRESHOLD else "unsupported"
                hint = raws.ocr_hint(best)
                is_strict, reason = _strict_of(kind, label_like, hint)
                counts[kind] += 1
                findings.append(QuoteFinding(kind=kind, strict=is_strict, strict_reason=reason, best_raw=best,
                                             coverage=round(cov, 3), ocr_hint=hint, **base))
    # 决策P3.14-7：--strict 下 raw 遍历不完整也算失败——判不了的结果不能让 CI 通过。
    ok = not (strict and (any(f.strict for f in findings) or raws.unreachable))
    return QuotesReport(ok=ok, pages_checked=len(paths), findings=findings, counts=dict(counts),
                        unclosed_quotes=unclosed, raw_unreachable=sorted(set(raws.unreachable)), strict_mode=strict,
                        single_page=page is not None)


def _direct_page(root: Path, page: str) -> str | None:
    """`--page` 写成 `目录/名`（可带 `wiki/` 前缀与 `.md` 后缀）时，按路径直取 `wiki/` 下的页；不是路径形或不存在 → None。"""
    name = page.replace("\\", "/")
    if "/" not in name:
        return None
    cand = posixpath.normpath("wiki/" + name.removeprefix("wiki/"))
    if not cand.endswith(".md"):
        cand += ".md"
    if not cand.startswith("wiki/"):
        return None
    try:
        if not (root / cand).is_file():
            return None
        # 大小写不敏感的盘（macOS）上 `Sources/x` 也 is_file：须按盘上真实大小写比对，否则 `rel` 不以
        # `wiki/sources/` 开头，source 页被当成普通页、改用 frontmatter sources 定范围（与全库跑结果不一致）。
        parts = cand.split("/")
        exact = all(parts[i] in os.listdir(root.joinpath(*parts[:i])) for i in range(1, len(parts)))
        return cand if exact else None
    except OSError:
        return None


def _scope(marks, end, stop_line, main_raws, line_raws, target_raws) -> tuple[str, list[str], bool]:
    """§4：返回 `(scope, 核对的 raw 列表, 是否「带名出处但不可读」)`。`stop_line` 是抹去行内 code 与引语内文的等长副本。"""
    tail = stop_line[end + 1 :]
    stops = [j for j in (tail.find(p) for p in _SENTENCE_END) if j >= 0]
    limit = end + 1 + (min(stops) if stops else len(tail))
    after = [m for m in marks if end < m[0] <= limit]
    if after:
        pos, kind, target = after[0]
        if kind in ("named", "raw"):
            got = target_raws(kind, target)
            return "cite", got, not got
        prev = [m for m in marks if m[0] < pos and m[1] in ("named", "raw")]
        if prev:
            got = target_raws(prev[-1][1], prev[-1][2])
            return "inherited", got, not got
        return "inherited", list(main_raws), False
    return "page", sorted(set(line_raws) | set(main_raws)), False


def format_report(report: QuotesReport, *, json_output: bool) -> str:
    """`--json`：`report_dict` 信封外加 `summary`；否则人读汇总，`--page` 时逐条列出。"""
    summary = {
        "candidates": report.counts.get("candidates", 0),
        "passed": report.counts.get("passed", 0),
        "kinds": {k: report.counts.get(k, 0) for k in
                  ("altered", "unsupported", "misattributed", "no_source", "source_unreadable", "scan_incomplete")},
        "strict_findings": sum(f.strict for f in report.findings),
        "unclosed_quotes": report.unclosed_quotes,
        "pairing_suspect": report.counts.get("pairing_suspect", 0),
        "raw_unreachable": report.raw_unreachable,
    }
    if json_output:
        import json

        payload = report_dict(ok=report.ok, pages_checked=report.pages_checked, items_key="findings",
                              items=report.findings)
        payload["summary"] = summary
        return json.dumps(payload, ensure_ascii=False, indent=2)

    n = summary["candidates"] or 1
    k = summary["kinds"]
    out = [
        f"· quotes 引语核对：{report.pages_checked} 页，候选引语 {summary['candidates']} 条，"
        f"逐字通过 {summary['passed']}（{summary['passed'] / n:.1%}）",
        f"  改字／缩写／拼接 altered {k['altered']} · 原文中找不到 unsupported {k['unsupported']}"
        f" · 措辞属实、出处未能核实 {k['misattributed']}",
        f"  未能核对：无出处 {k['no_source']} · 出处不可读 {k['source_unreadable']}"
        f" · raw 遍历不完整 {k['scan_incomplete']}；未闭合引号 {summary['unclosed_quotes']}"
        f" · 配对存疑 {summary['pairing_suspect']}",
        f"  计入 --strict：{summary['strict_findings']} 条（altered ∧ 无 OCR 提示 ∧ 非标签形态）",
    ]
    if report.raw_unreachable:
        out.append(f"  ⚠ raw 有 {len(report.raw_unreachable)} 处读不到：{', '.join(report.raw_unreachable[:5])}")
    if report.single_page:
        for f in report.findings:
            mark = "✗" if f.strict else "·"
            where = f.best_raw or "；".join(f.checked_raw) or "（无）"
            if f.hit_raw:  # misattributed：核对的是出处范围，命中处另列，免得读成「核对了命中的那份」
                where += f"；命中：{f.hit_raw}"
            out.append(f"  {mark} {f.page}:{f.line} [{f.kind}] 「{f.quote}」")
            out.append(f"      核对：{where}" + (f"（覆盖 {f.coverage}）" if f.coverage is not None else "")
                       + f" — {f.strict_reason}")
    else:
        bad = [f.page for f in report.findings if f.kind in ("altered", "unsupported")]
        per_dir = collections.Counter(p.rsplit("/", 1)[0] for p in bad)
        if per_dir:
            out.append("  按目录：" + " · ".join(f"{d} {c}" for d, c in sorted(per_dir.items())))
        per_page = collections.Counter(bad)
        if per_page:
            out.append(f"  不符最多的页（前 {TOP_PAGES}；用 --page 查看逐条）：")
            out += [f"    {c:>4}  {p}" for p, c in per_page.most_common(TOP_PAGES)]
        # `--show-misattributed` 的条目只在它打开时才进 findings；全库文本模式也须逐条列出，否则该参数静默无效。
        shown = [f for f in report.findings if f.kind == "misattributed"]
        if shown:
            out.append(f"  措辞属实、出处未能核实（前 {STRICT_LIST} 条）：")
            out += [f"    {f.page}:{f.line} 「{f.quote[:60]}」 → {f.hit_raw}" for f in shown[:STRICT_LIST]]
            if len(shown) > STRICT_LIST:
                out.append(f"    ……另有 {len(shown) - STRICT_LIST} 条，用 --json 或 --page 查看")
    if report.strict_mode and not report.ok:
        hits = [f for f in report.findings if f.strict]
        if report.raw_unreachable:
            out.append(f"✗ --strict：raw 遍历不完整（{len(report.raw_unreachable)} 处读不到），核对结果不完整，不能判通过")
        if hits:
            out.append(f"✗ --strict：{len(hits)} 条计入 strict 的引语不符")
        if not report.single_page:  # 单页模式上面已逐条列出；全库时列出前 STRICT_LIST 条，便于 CI 日志定位
            for f in hits[:STRICT_LIST]:
                out.append(f"    {f.page}:{f.line} 「{f.quote[:60]}」")
            if len(hits) > STRICT_LIST:
                out.append(f"    ……另有 {len(hits) - STRICT_LIST} 条，用 --json 或 --page 查看")
    return "\n".join(out)


def quotes_entrypoint(
    root_dir: str | Path,
    *,
    json_output: bool,
    strict: bool,
    page: str | None = None,
    min_len: int = MIN_LEN,
    include_corner: bool = True,
    show_misattributed: bool = False,
) -> int:
    """`guanlan quotes` 的单一落地：核对 → 渲染 → 退出码（默认 0；`--strict` 且有 strict 条目或 raw 遍历不完整 → 6）。"""
    try:
        root = require_kb_root(root_dir, writable=False)
        if page is not None:
            name = validate_page_arg(page)
            if name is None:
                raise GuanlanError(f"页面名不合法：{page!r}")
            page = name
        report = run_quotes(root, page=page, min_len=min_len, include_corner=include_corner,
                            show_misattributed=show_misattributed, strict=strict)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code
    print(format_report(report, json_output=json_output))
    return EXIT_LINT_FINDINGS if (strict and not report.ok) else EXIT_OK


def add_quotes_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="输出 JSON 契约（含 summary）")
    p.add_argument("--strict", action="store_true",
                   help="有计入 strict 的引语不符（altered ∧ 无 OCR 提示 ∧ 非标签形态）或 raw 遍历不完整，则以退出码 6 失败")
    p.add_argument("--page", metavar="名", help="只核对一页，并逐条列出")
    p.add_argument("--min-len", type=positive_int, default=MIN_LEN, metavar="N", help=f"引语归一后最少字数（默认 {MIN_LEN}）")
    p.add_argument("--no-corner", dest="include_corner", action="store_false", help="不收「」内文字（默认收）")
    # 旧开关：「」已默认收，保留为无操作，免得既有脚本报错。
    p.add_argument("--include-corner", dest="include_corner", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--show-misattributed", action="store_true", help="逐条列出措辞属实、出处未能核实的引语")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m guanlan.quotes",
                                     description="引语核对：引号内文字是否逐字出自所引 raw 原文（零 LLM，只读）。")
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    add_quotes_arguments(parser)
    a = parser.parse_args(argv)
    return quotes_entrypoint(a.dir, json_output=a.json, strict=a.strict, page=a.page, min_len=a.min_len,
                             include_corner=a.include_corner, show_misattributed=a.show_misattributed)


if __name__ == "__main__":
    raise SystemExit(main())
