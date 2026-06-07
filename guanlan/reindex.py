"""索引回填 reindex（P3.4，见 docs/P3.4-索引回填.md）。**零 LLM。**

`guanlan reindex`：把"磁盘存在但未收录进 index.md"的内容页（`health.index_missing_page`）
**确定性地**登记进 `index.md` 对应分区；可选 `--prune` 删除指向不存在文件的悬空行（`index_dangling`）。

- **检测复用 `pages.index_sync_state`**（与 `health` 同一归口，决策P3.4-4），登记纯 Python；
- **零 LLM**：分区由目录定、路径由文件位置定、锚文本取 frontmatter title、别名注记取 aliases；
- 唯一非确定性的"一句话摘要"**不在此生成**，留待后续 ingest / 人工补（§0、决策P3.4-1）；
- 只写 `wiki/index.md`，不碰 `raw/`、不起 Agentao、无写门禁、不写 `log.md`（决策P3.4-6）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import EXIT_OK, GuanlanError
from .pages import index_md_links, index_sync_state, iter_pages, load_page, page_title
from .paths import require_kb_root

__all__ = [
    "ReindexEntry",
    "ReindexResult",
    "run_reindex",
    "reindex_entrypoint",
    "main",
]

# 内容页目录 → index.md 分区标题（决策P3.4：分区由目录确定，零 LLM）。
_DIR_TO_SECTION = {
    "sources": "Sources",
    "entities": "Entities",
    "concepts": "Concepts",
    "syntheses": "Syntheses",
}

# `## <标题>` ATX 二级标题（index.md 分区行）。
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")


@dataclass(frozen=True)
class ReindexEntry:
    """一条登记：`page` 相对库根 posix，`section` 分区标题，`line` 写入 index 的整行。"""

    page: str
    section: str
    line: str


@dataclass(frozen=True)
class ReindexResult:
    ok: bool
    pages_checked: int
    added: list[ReindexEntry]
    pruned: list[str]  # 被 --prune 删除的整行原文（默认空）。
    # 注：是否有改动不另设字段，由 `run_reindex` 第二返回值 `new_text is None` 即唯一信号，免冗余。


def _aliases(meta: dict | None) -> list[str]:
    """容错取 frontmatter aliases：非映射 / 非列表 → 空；保留显示形（去首尾空白、滤空）。"""
    if not isinstance(meta, dict):
        return []
    raw = meta.get("aliases")
    if not isinstance(raw, list):
        return []
    return [a.strip() for a in raw if isinstance(a, str) and a.strip()]


def _safe_anchor(text: str) -> str:
    """中和 markdown 链接文字里的裸 `]`（→ 全角 `］`）。

    `index_md_links` 的链接文字段是 `[^\\]\\n]*`，遇 `]` 即截断（且不识转义 `\\]`）。标题含裸
    `]`（如 `[草稿]稿`）会让生成行**无法被自己解析**→ 该页登记不被 `health` 看见→ 下一轮重复登记，
    破坏幂等。`[` 不截断（在文字段内合法），故只换 `]`。
    """
    return text.replace("]", "］")


def _format_entry(path: Path, wiki: Path) -> str:
    """构造一行 index 登记 `- [标题](相对路径)[ — （别名：…）]`（零 LLM，全来自 frontmatter/位置）。"""
    meta, _body = load_page(path)
    title = _safe_anchor(page_title(meta, path.stem))
    target = path.relative_to(wiki).as_posix()
    line = f"- [{title}]({target})"
    aliases = _aliases(meta)
    if aliases:
        line += f" — （别名：{'/'.join(aliases)}）"
    return line


def _section_for(path: Path, wiki: Path) -> str | None:
    """页所属分区标题 = 相对 wiki 的首段目录名映射；落在四目录外 → None（跳过、不猜）。"""
    parts = path.relative_to(wiki).parts
    if len(parts) >= 2:
        return _DIR_TO_SECTION.get(parts[0])
    return None


def _split_lines(text: str) -> tuple[list[str], str, bool]:
    """拆 index.md 为 (无换行行列表, 检测到的 EOL, 是否以换行结尾)，供最小扰动重组。"""
    eol = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(("\n", "\r"))
    return text.splitlines(), eol, trailing


def _join_lines(lines: list[str], eol: str, trailing: bool) -> str:
    if not lines:
        return ""
    return eol.join(lines) + (eol if trailing else "")


def _apply_additions(lines: list[str], by_section: dict[str, list[str]]) -> list[str]:
    """把各分区待加行插到该分区末尾（最后一条非空行之后）；分区标题缺失则文末补建（§3.3）。"""
    # 现有分区边界：标题行号 → (heading_idx, 下一个标题/EOF)。
    headings = [(i, m.group(1)) for i, ln in enumerate(lines) if (m := _SECTION_RE.match(ln))]
    bounds: dict[str, tuple[int, int]] = {}
    for k, (idx, title) in enumerate(headings):
        end = headings[k + 1][0] if k + 1 < len(headings) else len(lines)
        bounds.setdefault(title, (idx, end))

    inserts: list[tuple[int, list[str]]] = []
    missing_sections: list[tuple[str, list[str]]] = []
    for section, new_lines in by_section.items():
        if not new_lines:
            continue
        if section in bounds:
            heading_idx, end = bounds[section]
            insert_at = heading_idx + 1  # 分区为空（仅标题）时紧跟标题。
            for j in range(end - 1, heading_idx, -1):
                if lines[j].strip():
                    insert_at = j + 1  # 最后一条非空行（已有条目/占位注释）之后。
                    break
            inserts.append((insert_at, new_lines))
        else:
            missing_sections.append((section, new_lines))

    # 已有分区的插入：从高行号到低行号施加，避免前面的插入移位后面的下标。
    for insert_at, new_lines in sorted(inserts, key=lambda x: -x[0]):
        lines[insert_at:insert_at] = new_lines

    # 缺失分区：文末补建 `## <Section>` 再追加（与上面无下标耦合，最后做）。
    for section, new_lines in missing_sections:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"## {section}")
        lines.extend(new_lines)
    return lines


def _prune_dangling(lines: list[str], dangling: set[str]) -> tuple[list[str], list[str]]:
    """删除整行链接目标**全部** ∈ dangling 的行（单行单链接、精确匹配，不误删正常行）。"""
    kept: list[str] = []
    removed: list[str] = []
    for ln in lines:
        targets = index_md_links(ln)
        if targets and targets <= dangling:
            removed.append(ln)
        else:
            kept.append(ln)
    return kept, removed


def run_reindex(wiki: Path, *, prune: bool = False) -> tuple[ReindexResult, str | None]:
    """算并构造 index.md 回填后的新文本。**纯函数、不写盘**（写由 entrypoint 决定）。

    返回 `(result, new_text)`：无任何改动时 `new_text=None`（即"是否改动"的唯一信号）。
    """
    wiki = Path(wiki)
    root = wiki.parent
    pages = list(iter_pages(wiki))  # 只 walk 一次，detection 复用此快照（免二次遍历）。
    missing, dangling = index_sync_state(wiki, pages)

    index_path = wiki / "index.md"
    text = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    lines, eol, trailing = _split_lines(text)

    # 1) 可选剪枝（先删悬空，再登记——登记行指向真实文件，不会被随后剪掉）。
    pruned: list[str] = []
    if prune and dangling:
        lines, pruned = _prune_dangling(lines, set(dangling))

    # 2) 登记缺失页（按 _DIR_TO_SECTION 固定序聚合，分区内按 iter_pages 稳定序）。
    by_section: dict[str, list[str]] = {section: [] for section in _DIR_TO_SECTION.values()}
    added: list[ReindexEntry] = []
    for path in missing:
        section = _section_for(path, wiki)
        if section is None:
            print(
                f"  跳过 {path.relative_to(root).as_posix()}：不在 "
                f"{'/'.join(_DIR_TO_SECTION)} 任一目录下，无法归区。",
                file=sys.stderr,
            )
            continue
        line = _format_entry(path, wiki)
        by_section[section].append(line)
        added.append(ReindexEntry(path.relative_to(root).as_posix(), section, line))

    lines = _apply_additions(lines, by_section)
    new_text = _join_lines(lines, eol, trailing)

    result = ReindexResult(ok=True, pages_checked=len(pages), added=added, pruned=pruned)
    return result, (new_text if new_text != text else None)


def format_result(result: ReindexResult, *, dry_run: bool, json_output: bool) -> str:
    """渲染回执：`--json` 走稳定契约；否则人类可读。"""
    if json_output:
        return json.dumps(
            {
                "ok": result.ok,
                "pages_checked": result.pages_checked,
                "added": [asdict(e) for e in result.added],
                "pruned": result.pruned,
            },
            ensure_ascii=False,
            indent=2,
        )

    if not result.added and not result.pruned:
        return "✓ reindex：index 与磁盘已同步，无需登记。"

    verb = "将登记" if dry_run else "登记"
    head = f"· reindex{'（dry-run，未写盘）' if dry_run else ''}：{verb} {len(result.added)} 页"
    if result.pruned:
        head += f"，{'将删除' if dry_run else '删除'} {len(result.pruned)} 条悬空"
    lines = [head + "："]
    for e in result.added:
        lines.append(f"    + [{e.section}] {e.line}")
    for ln in result.pruned:
        lines.append(f"    - {ln}")
    return "\n".join(lines)


def reindex_entrypoint(
    root_dir: str | Path, *, prune: bool, dry_run: bool, json_output: bool
) -> int:
    """`guanlan reindex` 的单一落地：算 → （非 dry-run 则写）→ 渲染 → 退出码。"""
    try:
        # 只读 wiki/、写其下 config catalog index.md，与 graph/health/lint 同属零 LLM 维护族。
        root = require_kb_root(root_dir, writable=False)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    wiki = root / "wiki"
    result, new_text = run_reindex(wiki, prune=prune)
    if not dry_run and new_text is not None:
        (wiki / "index.md").write_text(new_text, encoding="utf-8")
    print(format_result(result, dry_run=dry_run, json_output=json_output))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.reindex` 入口（与 `guanlan reindex` 共享 reindex_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.reindex",
        description="索引回填：把磁盘已存在但未收录的内容页登记进 index.md（零 LLM）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("--dry-run", action="store_true", help="只打印 worklist，不写盘")
    parser.add_argument("--prune", action="store_true", help="额外删除指向不存在文件的悬空行")
    parser.add_argument("--json", action="store_true", help="输出 JSON 契约")
    args = parser.parse_args(argv)
    return reindex_entrypoint(
        args.dir, prune=args.prune, dry_run=args.dry_run, json_output=args.json
    )


if __name__ == "__main__":
    raise SystemExit(main())
