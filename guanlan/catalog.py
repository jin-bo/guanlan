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
    "build_catalog",
    "overview_outline",
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


def overview_outline(path: Path) -> str | None:
    """`overview.md` 的结构摘要：首行「全文 N 行、M 字」，其后每个 ATX 标题一行 `L<行号> <标题行>`。

    文件不存在或读不出 → None（调用方据此不提结构）。标题行原样给出（含 `#` 层级），不截断。
    开头的 frontmatter（与 `pages.split_frontmatter` 同判：首行 `---`、到下一条 `---` 闭合）里的
    `# 注释` 行不算标题；只跳过、不删行，行号不变。
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = _split_md_lines(text)
    masked = _mask_fenced_blocks(lines)
    body_from = 1  # 正文起始行号（1 起）
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_from = i + 2
                break
    headings = [
        f"L{i} {line.rstrip(chr(13) + chr(10))}"
        for i, (line, scan) in enumerate(zip(lines, masked), start=1)
        if i >= body_from and _ATX_HEADING_RE.match(scan)
    ]
    head = f"全文 {len(lines)} 行、{len(text):,} 字，{len(headings)} 个标题"
    return "\n".join([head, *headings])
