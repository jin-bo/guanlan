r"""本篇 source 页的身份定位：先按文件名，再按页内 provenance 证据。**零 LLM。**

ingest 告诉 Agent「本篇 source 页在哪 / 没有」，摄入后 wrapper 再按同一结论给它 stamp `raw_digest`。
按文件名定位（`rawio.find_source_page`）认不出**改过名的既有页**——实测一个库里有页用英文 slug
命名、正文却写着 `> 原始资料：\`raw/…\``；wrapper 说「未找到、新建」，Agent 就照做，同一篇资料落成
两张 source 页。故文件名未命中时再扫 `wiki/sources/`，按证据强弱分两档：

- **归属依据**（可直接认定）：frontmatter `raw_digest` 的路径恰为本篇；或页内 conventions 模板的源文件
  声明行（整行、不在围栏代码块里） `> 原始资料：\`raw/<本篇>\`` 是该页**唯一**声明的原始资料、且没有指向别篇的 `raw_digest`。
- **候选**（只列给 Agent，不认定）：正文其他位置提到本篇路径（可能只是引用）、声明了多篇原始资料、
  或 `raw_digest` 指向别篇却提到本篇（证据冲突）。**即使只有一张命中也不升格**为归属。

恰好一张有归属依据 → 认定；多张 → 全部作为候选、不自动选定或合并；只有候选 → 列出；都没有 → 未定位。

**能力上限**：只认路径**逐字**一致（不做 NFC/NFD、大小写、`.`/`-` 折叠）；只扫 `wiki/sources/`；页内
既无 `raw_digest` 又没写声明行、文件名也对不上的同源页仍认不出——那时 prompt 要求 Agent 自行检索确认后
才新建。只在文件名未命中时才扫，千页级库一次 ingest 多读一遍 sources 目录，与门禁快照同阶。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .pages import _mask_fenced_blocks, _split_md_lines, load_page_text
from .provenance import RAW_DIGEST_KEY, parse_digest_value
from .rawio import find_source_page, raw_slug

# conventions 的 source 页模板行：`> 原始资料：\`raw/<原文件名>\``（全角或半角冒号）。
_DECLARATION_RE = re.compile(r"^[ \t]*>[ \t]*原始资料[:：][ \t]*`(raw/[^`\n]+)`[ \t]*$", re.M)


@dataclass(frozen=True)
class SourceLocation:
    """`page` 非 None = 已认定；`basis` 说明依据。`candidates` 只在未认定时有意义：(页, 原因)。"""

    page: Path | None
    basis: str = ""
    candidates: list[tuple[Path, str]] = field(default_factory=list)


def locate_source_page(kb: Path, rel: str) -> SourceLocation:
    """定位 `raw/<rel>` 的 source 页（`rel` 相对 `raw/`、posix）。读不出的页跳过，不抛。"""
    sources_dir = kb / "wiki" / "sources"
    by_name = find_source_page(sources_dir, Path(rel).stem)
    if by_name is not None:
        return SourceLocation(by_name, "文件名")
    if not sources_dir.is_dir():
        return SourceLocation(None)

    target = f"raw/{rel}"
    owners: list[tuple[Path, str]] = []
    candidates: list[tuple[Path, str]] = []
    for page in sorted(sources_dir.rglob("*.md")):
        if page.name.startswith(".") or not page.is_file():
            continue
        try:
            text = page.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if target not in text:
            continue  # 两档证据都要求页内出现本篇路径
        meta, body = load_page_text(text)
        parsed = parse_digest_value((meta or {}).get(RAW_DIGEST_KEY))
        unfenced = "".join(_mask_fenced_blocks(_split_md_lines(body)))  # 围栏里的示例不算声明
        declared = set(_DECLARATION_RE.findall(unfenced))
        if parsed is not None and parsed[0] == target:
            owners.append((page, "raw_digest"))
        elif parsed is not None:
            candidates.append((page, "raw_digest 指向别的资料，但页内提到本篇路径"))
        elif declared == {target}:
            owners.append((page, "原始资料声明"))
        elif target in declared:
            candidates.append((page, "声明了多篇原始资料，含本篇"))
        else:
            candidates.append((page, "页内提到本篇路径，可能只是引用"))

    if len(owners) == 1:
        page, basis = owners[0]
        return SourceLocation(page, basis)
    return SourceLocation(None, "", owners + candidates)


def source_slug(rel: str) -> str:
    """新建 source 页应使用的 slug（= 文件名 stem），与 `find_source_page` 首选的精确形一致。"""
    return raw_slug(Path(rel).stem)
