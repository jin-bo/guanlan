"""确定性字面检索 grep（P5.5，见 docs/P5.5-字面检索.md）。**零 LLM、零依赖、零写盘、零索引。**

`guanlan grep "<串>"`：在 `wiki/` 非 config 页的**正文**里找**字面出现**的那个串，按页/行序打印
命中行号与片段。它是**第二条召回路**，与 P5.0 的 `search` 并列、互不替代：

| | `search`（P5.0） | `grep`（本模块） |
|---|---|---|
| 口径 | 分词 + BM25 打分，按相关度排 | **字面子串**，按页/行序列 |
| 擅长 | 自然语言问题、概念、模糊表述 | **专名、编号、版本号、标识符、固定短语** |
| 产物 | 页 + 分数 + 片段 | 页 + **行号** + 片段 |

为什么要它：`search` 的分词把非 CJK 段切成 `[a-z0-9]+`，于是 `bge-m3` 成 `bge`+`m3`、`P4.22` 成
`p4`+`22`、`v0.1.23` 成三段——**召回不丢**（query 与文档同一 `tokenize`）**但精度丢**：一条标识符
查询实际退化成"其中最稀有的那一段"，数字段在真实库里是高频噪声、被 IDF 压到近乎无贡献。而观澜
自己的语料恰恰塞满这类标识符（决策编号、阶段号、版本号、模型名、slug）。本模块**不动 `tokenize`
一个字节**，另开一条不打分、不排序、不建索引的路把这类查询接住（决策P5.5-1）。

设计要点：

- **匹配口径钉死、无旋钮**（决策P5.5-2）：`re.escape` 后**字面**匹配（输入永不当正则）+ `IGNORECASE`
  直接在**原行**上跑（故命中偏移对原文有效）；**不做 NFKC / 全半角 / CJK 归一**。
- **扫描面 = `iter_pages`**（决策P5.5-3）：`wiki/` 下非 config 页，与 `search` 同一批页；不扫 `raw/`。
- **正文限定 + 原文行号**（决策P5.5-5）：frontmatter 块不参与匹配，但行号按**原文件**计（1-based），
  照着行号能直接跳到那一行。
- **双封顶 + 显式截断**（决策P5.5-4）：单页至多 `MAX_MATCHES_PER_PAGE` 条、全库至多 `limit` 条；
  触顶时回执里 `truncated=True` 并在文本/JSON 两侧都说出来——**绝不静默丢**。
- **确定性**：页序取 `iter_pages` 的排序、页内按行号升序，无时间戳/随机/盘上派生物，同库同串字节稳定。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .pages import iter_pages, split_frontmatter
from .paths import require_kb_root

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_MATCHES_PER_PAGE",
    "SNIPPET_WIDTH",
    "GrepHit",
    "GrepResult",
    "grep_pages",
    "grep_result_dict",
    "run_grep",
    "grep_entrypoint",
]

# 单页命中上限（决策P5.5-4）：一张把某个编号重复几十次的页不该把整个回执吃光。**非旋钮**。
MAX_MATCHES_PER_PAGE = 5
# 全库命中上限默认值（`--limit` 可调，须 ≥ 1）。
DEFAULT_LIMIT = 50
# 片段窗口宽度，与 `search.SNIPPET_WIDTH` 同值同理由（§3.4），两处各自持有、不跨模块耦合。
SNIPPET_WIDTH = 140


@dataclass(frozen=True)
class GrepHit:
    """一条字面命中：**页 + 行号 + 片段**。无分数——本路不排序，加个假分数只会误导（决策P5.5-1）。"""

    page: str  # 相对库根的 posix 路径（同 `SearchHit.page` 口径，带 `wiki/` 前缀）
    line: int  # **原文件** 1-based 行号（frontmatter 不参与匹配，但计入行号，决策P5.5-5）
    snippet: str  # 命中处 SNIPPET_WIDTH 字符窗、空白已折叠


@dataclass(frozen=True)
class GrepResult:
    """字面检索回执：所有消费侧（CLI / JSON / 将来 web·mcp）从同一份生成（同决策P5.0-10）。"""

    hits: list[GrepHit]
    pages_searched: int  # 扫过的 content 页数
    pages_matched: int  # 其中有命中的页数
    pattern: str
    truncated: bool  # 因双封顶而未列全（决策P5.5-4：触顶必须说出来）
    limit: int = DEFAULT_LIMIT  # 本次生效的全库上限——截断文案要说清是**哪道闸**先拦住的


def _snippet(line: str, start: int) -> str:
    """命中处约 `SNIPPET_WIDTH` 字符窗、折叠空白（与 `search._snippet` 同款确定性口径）。

    窗口**左对齐到命中点**而非居中：命中串本身永远在窗内可见，这是本路唯一要给的证据。
    """
    return " ".join(line[start : start + SNIPPET_WIDTH].split())


def _body_line_offset(text: str, body: str) -> int:
    """正文首行在**原文件**中的 0-based 行偏移。

    `split_frontmatter` 的 `body` 是 `text` 的**逐字后缀**（由 `splitlines(keepends=True)` 重拼），
    故前缀长度即 `len(text) - len(body)`，其中的换行数就是偏移。这样行号指回原文件，照着能跳过去
    ——若按 body 自己的行号报，带 frontmatter 的页会**整体偏移若干行**，那种行号是错的。
    """
    return text.count("\n", 0, len(text) - len(body))


def grep_pages(wiki: Path, pattern: str, *, limit: int = DEFAULT_LIMIT) -> GrepResult:
    """无状态冷算：逐页读盘 → 正文逐行字面匹配 → 双封顶。**不建索引、不落派生物。**

    `limit < 1` → `ValueError`（内核归口，同 `search.score`，web/mcp 直接复用、不靠 CLI 兜底）。
    空/纯空白 `pattern` → `ValueError`（"找空串"在任何页上都命中，不是一个有意义的查询）。
    读取沿用 `errors="replace"` 容错口径（决策P5.0-16），坏页照扫、**绝不抛**。
    """
    if limit < 1:
        raise ValueError(f"limit 必须 ≥ 1：{limit}")
    if not pattern.strip():
        raise ValueError("检索串为空或纯空白。")

    wiki = Path(wiki)
    root = wiki.parent
    needle = re.compile(re.escape(pattern), re.IGNORECASE)  # 字面 + 大小写不敏感（决策P5.5-2）

    hits: list[GrepHit] = []
    pages_searched = 0
    pages_matched = 0
    truncated = False

    for path in iter_pages(wiki):  # 已按路径稳定排序、已排除 config 页
        pages_searched += 1
        if len(hits) >= limit:
            truncated = True
            continue  # 仍走完循环把 pages_searched 数全——回执里那个"扫了几页"不该因截断而失真
        text = path.read_text(encoding="utf-8", errors="replace")
        _, body = split_frontmatter(text)
        offset = _body_line_offset(text, body)
        page = path.relative_to(root).as_posix()
        page_hits = 0
        for i, line in enumerate(body.splitlines()):
            m = needle.search(line)
            if m is None:
                continue
            if page_hits >= MAX_MATCHES_PER_PAGE or len(hits) >= limit:
                truncated = True
                break
            hits.append(GrepHit(page=page, line=offset + i + 1, snippet=_snippet(line, m.start())))
            page_hits += 1
        if page_hits:
            pages_matched += 1

    return GrepResult(
        hits=hits,
        pages_searched=pages_searched,
        pages_matched=pages_matched,
        pattern=pattern,
        truncated=truncated,
        limit=limit,
    )


def grep_result_dict(result: GrepResult) -> dict:
    """成功回执的**字段单一归口**（同决策P5.1-4）：`GrepResult` → JSON 契约 dict。

    CLI `--json`、将来的 Web/MCP 接入三处共用本函数，杜绝字段名各写一份后漂移。返回 **dict**，
    各侧自行序列化：字段/结构同形，字节因序列化参数而异。
    """
    return {
        "ok": True,
        "pattern": result.pattern,
        "pages_searched": result.pages_searched,
        "pages_matched": result.pages_matched,
        "truncated": result.truncated,
        "limit": result.limit,
        "results": [
            {"page": h.page, "line": h.line, "snippet": h.snippet} for h in result.hits
        ],
    }


def _emit_error(message: str, *, json_output: bool, exit_code: int = EXIT_USAGE) -> int:
    """出错输出：`--json` 吐 `{"ok":false,"error":…}` 到 stdout；否则 stderr 纯文本（同决策P5.0-21）。"""
    if json_output:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    else:
        print(message, file=sys.stderr)
    return exit_code


def _render(result: GrepResult, *, json_output: bool) -> str:
    """渲染成功回执：JSON 契约或人类可读文本。两侧都从同一 `GrepResult` 出。"""
    if json_output:
        return json.dumps(grep_result_dict(result), ensure_ascii=False, indent=2)

    if not result.hits:
        return f"（无命中）字面「{result.pattern}」· 扫描 {result.pages_searched} 页。"
    head = (
        f"字面「{result.pattern}」· 扫描 {result.pages_searched} 页 · "
        f"命中 {len(result.hits)} 处（{result.pages_matched} 页）："
    )
    lines = [head]
    for h in result.hits:
        lines.append(f"  {h.page}:{h.line}")
        lines.append(f"      {h.snippet}")
    if result.truncated:
        # 报**两道闸各自的值**，不报 `len(hits)`——单页闸先拦住时，后者会让人误以为 `--limit` 是 5。
        lines.append(
            f"  （已截断：单页至多 {MAX_MATCHES_PER_PAGE} 条、全库至多 {result.limit} 条；"
            "缩小范围或提高 --limit 看更多）"
        )
    return "\n".join(lines)


def run_grep(pattern: str, *, root: Path, limit: int, json_output: bool) -> int:
    """`guanlan grep` 的打印壳：冷算 `grep_pages` → 渲染 → 退出码（无新码，同决策P5.0-6）。"""
    try:
        result = grep_pages(root / "wiki", pattern, limit=limit)
    except ValueError as exc:  # 空串 / limit<1（CLI 的 positive_int 是第一道门）
        return _emit_error(str(exc), json_output=json_output)
    print(_render(result, json_output=json_output))
    return EXIT_OK


def grep_entrypoint(
    root_dir: str | Path, *, pattern: str, limit: int, json_output: bool
) -> int:
    """`guanlan grep` 的单一落地：校验库根 → run_grep。纯读、零 LLM、零写盘。"""
    try:
        root = require_kb_root(root_dir, writable=False)
    except GuanlanError as exc:
        return _emit_error(str(exc), json_output=json_output, exit_code=exc.exit_code)
    return run_grep(pattern, root=root, limit=limit, json_output=json_output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="确定性字面检索：在 wiki/ 正文里找字面出现的串，打印页:行号 + 片段（零 LLM）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("pattern", help="要找的**字面**串（不是正则）")
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help=f"全库命中上限（默认 {DEFAULT_LIMIT}）"
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 契约")
    args = parser.parse_args(argv)
    return grep_entrypoint(
        args.dir, pattern=args.pattern, limit=args.limit, json_output=args.json
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
