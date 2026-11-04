"""ingest 的页面名录：每次现算、拼进 prompt 的「路径 + 标题 + 别名」清单。**零 LLM。**

动机（docs/backlog/notes/ingest-performance-research.md〈上下文体积〉）：skill 原先要求每次 ingest 整读
`wiki/index.md` 查重，而 index 随库线性增长（实测千页级库达十余万字），且 agentao `read_file` 不带
`limit` 时只回 2000 行、超出部分要模型自己续读——查重最需要的信息反而可能看不全。去重决定树真正要的
只是「已有哪些页、各叫什么别名」：实测名录约为 index 的 0.2–0.4（含路径与标题）。

- **不列 `wiki/sources/`**：去重决定树管的是 entity/concept 页；source 页与 raw 一一对应、由 wrapper 直接
  告知本篇那张的位置（`ingest.build_ingest_prompt`）。实测千页级库的名录 98% 是 source 页名，每次请求都重发。
  按**实际路径**省略（`wiki/sources/` 下的文件），不按 frontmatter `type`——type 写错的页不会因此被藏起来。
  名录只服务语义查重：source 页名与别名照样参与全库链接解析与撞名校验，门禁（`check`）范围**不变**。
- **派生物、不落盘**：与 `check`/`graph` 同一页面枚举（`iter_pages`）与别名口径（`alias_index`：只收
  `aliases` 列表里的字符串项）；每次 ingest 现算，不新增任何持久状态，index 本身不动。
- **记录格式 = JSON Lines，按目录分节**（格式说明见 `CATALOG_FORMAT`）：对象行 `{"dir":"wiki/entities"}`
  开一节，其后每页一个数组行 `[文件名, 标题, [别名…]]`，路径 = 节目录 + `/` + 文件名 + `.md`。页名、
  别名里的分隔符、引号、换行都由 JSON 转义，不会拆坏记录。目录前缀与字段名不逐页重复——千页级库上
  这两项占逐页对象写法的一半体积。标题与文件名相同时记 `null`；`aliases` 原样保留（不归一、不去重）；
  尾部空项省略。frontmatter 读不出的页仍列出，第 4 项记 `"frontmatter-unreadable"`——它的别名看不到，
  不能当作「没有别名」。
- **overview 结构**（`overview_outline`）：`overview.md` 的 ATX 标题行 + 行号，让模型按主题用
  `read_file` 的 `offset`/`limit` 读相关章节，而不是每次整读一份可能已膨胀到十余万字的综述。行号按
  markdown 口径切行（`\r\n`/`\r`/`\n`），与 agentao `read_file` 的行号一致；围栏代码块里的 `#` 不算标题。
- **资料结构**（`raw_outline`）：本篇 raw 原文的行数 / 字数 / 标题数、`read_file` 会截断的超长行、标题
  行号。动机见 docs/backlog/notes/长文档入库-实测缺陷与改进.md（P1-2/3）：长资料只读开头、模型自己调
  `wc` 统计规模（违反「写工作流不调 shell」直接失败）。目录部分有预算，超出时只列高层标题并**明说非全集**。
- **超预算不截断**：名录超过 `CATALOG_BUDGET_CHARS` 时由调用方明确报告、整份不注入，退回整读 index；
  绝不静默只给页名或截掉一部分（那恰好丢掉查重最需要的信息）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .pages import _mask_fenced_blocks, _split_md_lines, iter_pages, load_page, page_title

__all__ = [
    "CATALOG_BUDGET_CHARS",
    "CATALOG_FORMAT",
    "PageCatalog",
    "LONG_RAW_CHARS",
    "RawOutline",
    "build_catalog",
    "overview_outline",
    "raw_outline",
]

# 名录字符预算（按 Python 字符计，CJK 一字计 1）。实测 1,500 页级的库名录约 8 万字；超出即整份不注入
# （见模块 docstring）。改它只影响「何时退回整读 index」，不影响名录内容。
CATALOG_BUDGET_CHARS = 120_000

# 拼进 prompt 的格式说明——与 `build_catalog` 的产出逐条对应，改格式必须同改这里。
CATALOG_FORMAT = (
    "名录为 JSON Lines：`{\"dir\":…}` 行开一节，其后每页一行数组 `[文件名, 标题, [别名…]]`；"
    "页路径 = 节目录 + `/` + 文件名 + `.md`；标题为 null 表示与文件名相同；尾部空项省略；"
    "第 4 项为 `\"frontmatter-unreadable\"` 表示该页 frontmatter 读不出、别名未知（不等于没有别名）"
)

_UNREADABLE = "frontmatter-unreadable"
_SOURCES_DIR = "sources"


@dataclass(frozen=True)
class PageCatalog:
    text: str  # 分节 JSON Lines（见 CATALOG_FORMAT）；空库为空串
    pages: int  # 名录列出的页数（不含 sources）
    omitted_sources: int  # 按路径省略的 `wiki/sources/` 页数
    budget: int  # 建名录时的 CATALOG_BUDGET_CHARS——报告与判定读同一个值

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def over_budget(self) -> bool:
        return self.chars > self.budget


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _entry(path: Path) -> list:
    entry: list = [path.stem, None, []]  # iter_pages 只收 `*.md`，stem 即去掉 `.md` 的文件名
    try:
        meta, _body = load_page(path)  # 容错档：坏/缺 frontmatter → None，不抛
    except OSError:
        # 读盘失败（权限等）load_page 照抛：同按「读不出」列出，名录生成不崩。注意这只管名录这一步——
        # 随后门禁基线读同一页仍会抛 PermissionError（既有行为，main 同样），不在本处承诺范围内。
        meta = None
    if not isinstance(meta, dict):
        return [entry[0], None, None, _UNREADABLE]
    title = page_title(meta, entry[0])  # 与 graph/Web 同一取值器：无合法 title 回退 stem
    if title != entry[0]:
        entry[1] = title
    aliases = meta.get("aliases")
    if isinstance(aliases, list):
        entry[2] = [a for a in aliases if isinstance(a, str)]  # 与 alias_index 同口径：只认字符串项
    while len(entry) > 1 and entry[-1] in (None, []):
        entry.pop()  # 尾部空项省略
    return entry


def build_catalog(wiki: Path) -> PageCatalog:
    """枚举 `wiki/` 下 content 页（`iter_pages` 稳定序，不含 config 页与 `wiki/sources/`），产出分节 JSON Lines 名录。"""
    wiki = Path(wiki)
    root = wiki.parent
    lines: list[str] = []
    current_dir: str | None = None
    pages = 0
    omitted = 0
    for path in iter_pages(wiki):
        if path.relative_to(wiki).parts[0] == _SOURCES_DIR:  # 根下的 `sources.md` 不受影响
            omitted += 1
            continue
        dir_rel = path.parent.relative_to(root).as_posix()
        if dir_rel != current_dir:
            # iter_pages 按完整路径排序，同目录页未必相邻（`a/x.md` < `a/y/z.md` < `a/z.md`）：
            # 目录变了就再开一节，同一目录可出现多节——每行的归属只看它上方最近的节头。
            lines.append(_dumps({"dir": dir_rel}))
            current_dir = dir_rel
        lines.append(_dumps(_entry(path)))
        pages += 1
    return PageCatalog(
        text="\n".join(lines),
        pages=pages,
        omitted_sources=omitted,
        budget=CATALOG_BUDGET_CHARS,
    )


# ATX 标题（CommonMark）：至多 3 个前导空格 + 1–6 个 `#` + 空白或行尾。前导空格须认——渲染器把
# `   ## X` 当标题，漏掉它模型就看不到这一章、无从按行号定位。
_ATX_HEADING_RE = re.compile(r" {0,3}#{1,6}(\s|$)")


_EOL = "\r\n"


def _outline_scan(text: str) -> tuple[list[str], list[tuple[int, str, int]]]:
    """按 markdown 口径切行（保留行尾），返回 `(lines, headings)`；headings 为 `(行号, 标题行, 层级)`。

    开头的 frontmatter（与 `pages.split_frontmatter` 同判：首行 `---`、到下一条 `---` 闭合）里的
    `# 注释` 行不算标题；只跳过、不删行，行号不变。围栏代码块里的 `#` 不算标题。
    """
    lines = _split_md_lines(text)
    masked = _mask_fenced_blocks(lines)
    body_from = 1  # 正文起始行号（1 起）
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_from = i + 2
                break
    headings = [
        (i, line.rstrip(_EOL), len(scan.lstrip(" ")) - len(scan.lstrip(" ").lstrip("#")))
        for i, (line, scan) in enumerate(zip(lines, masked), start=1)
        if i >= body_from and _ATX_HEADING_RE.match(scan)
    ]
    return lines, headings


def overview_outline(path: Path) -> str | None:
    """`overview.md` 的结构摘要：首行「全文 N 行、M 字」，其后每个 ATX 标题一行 `L<行号> <标题行>`。

    文件不存在或读不出 → None（调用方据此不提结构）。标题行原样给出（含 `#` 层级），不截断。
    frontmatter 与围栏的口径见 `_outline_scan`。
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines, headings = _outline_scan(text)
    head = f"全文 {len(lines)} 行、{len(text):,} 字，{len(headings)} 个标题"
    return "\n".join([head, *(f"L{i} {h}" for i, h, _ in headings)])


# ---- 资料结构（ingest 的 raw 原文规模 + 标题行号） ----
# agentao `read_file` 的两道硬限（agentao/tools/file_ops.py：MAX_LINES_DEFAULT / MAX_LINE_LENGTH）：
# 不带 limit 只回前 2000 行；单行（去行尾后）超过 2000 字只回前 2000 字——按行号分段读也读不到被截的部分。
READ_FILE_MAX_LINES = 2000
READ_FILE_MAX_LINE_CHARS = 2000
# 「长资料」口径：超过任一项即算。字数阈值 = agentao 单个工具结果的落盘线（超出只留首 8k + 尾 32k）。
LONG_RAW_CHARS = 40_000
# 目录（标题行）部分的字符预算。超出时只列最高两级、再超只列最高一级、再超截取前若干条，**并明说非全集**。
# 与页面名录「超预算不截断」不同：名录服务查重，缺一条就会误建重复页；这里只是阅读导航，缺下级标题
# 至多让模型多读几行——前提是明说省略了多少，不让它误以为目录是全集。
RAW_OUTLINE_BUDGET_CHARS = 6_000
_LONG_LINES_LISTED = 20


@dataclass(frozen=True)
class RawOutline:
    """raw 原文的结构摘要。`text` 直接拼进 prompt；其余字段供 stderr 提示与测试。"""

    text: str
    lines: int
    chars: int
    headings: int
    listed_headings: int
    long_lines: tuple[tuple[int, int], ...]  # (行号, 去行尾后的字数)

    @property
    def is_long(self) -> bool:
        return self.chars > LONG_RAW_CHARS or self.lines > READ_FILE_MAX_LINES


def _fit_headings(headings: list[tuple[int, str, int]], budget: int) -> tuple[list[str], str | None]:
    """按预算挑要列的标题；返回 `(条目, 省略说明或 None)`。"""
    def entries(hs):
        return [f"L{i} {h}" for i, h, _ in hs]

    full = entries(headings)
    if sum(len(e) + 1 for e in full) <= budget:
        return full, None
    top = min(lv for _, _, lv in headings)
    for depth, label in ((top + 1, "最高两级"), (top, "最高一级")):
        kept = [x for x in headings if x[2] <= depth]
        if len(kept) < len(headings) and sum(len(e) + 1 for e in entries(kept)) <= budget:
            return entries(kept), f"标题较多，仅列{label}（另有 {len(headings) - len(kept)} 个下级标题未列，此目录非全集）"
    kept_entries, used = [], 0
    for e in entries([x for x in headings if x[2] == top]):
        if used + len(e) + 1 > budget:
            break
        kept_entries.append(e)
        used += len(e) + 1
    return kept_entries, f"标题过多，仅列最高一级的前 {len(kept_entries)} 个（共 {len(headings)} 个标题，此目录非全集）"


def raw_outline(path: Path, *, budget: int | None = None) -> RawOutline | None:
    """raw 原文的〈资料结构〉：规模（行数 / 字数 / 标题数）、`read_file` 会截断的超长行、标题行号。

    读不出 → None（调用方据此说明「规模未知」，不让 ingest 因此失败）。行号口径与 `overview_outline`
    相同（markdown 切行，与 agentao `read_file` 一致）。超长行按 `read_file` 的判法：去掉行尾后长度
    **大于** `READ_FILE_MAX_LINE_CHARS`。
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines, headings = _outline_scan(text)
    long_lines = tuple(
        (i, n)
        for i, line in enumerate(lines, start=1)
        if (n := len(line.rstrip(_EOL))) > READ_FILE_MAX_LINE_CHARS
    )
    out = [f"全文 {len(lines):,} 行、{len(text):,} 字，{len(headings)} 个标题"]
    if long_lines:
        shown = "、".join(f"L{i}（{n:,} 字）" for i, n in long_lines[:_LONG_LINES_LISTED])
        more = f" 等 {len(long_lines)} 处" if len(long_lines) > _LONG_LINES_LISTED else ""
        out.append(
            f"超过 {READ_FILE_MAX_LINE_CHARS:,} 字的行 {len(long_lines)} 处（`read_file` 只回这些行的前 "
            f"{READ_FILE_MAX_LINE_CHARS:,} 字，其余部分读不到）：{shown}{more}"
        )
    listed: list[str] = []
    if headings:
        listed, note = _fit_headings(headings, RAW_OUTLINE_BUDGET_CHARS if budget is None else budget)
        if note:
            out.append(note)
        out.extend(listed)
    return RawOutline(
        text="\n".join(out),
        lines=len(lines),
        chars=len(text),
        headings=len(headings),
        listed_headings=len(listed),
        long_lines=long_lines,
    )
