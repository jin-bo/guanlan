#!/usr/bin/env python3
"""ingest 上下文体积基准：旧做法（整读 index + overview）对新做法（名录 + overview 结构）。

只用临时合成库，零 LLM、不读用户库。从仓库根运行：.venv/bin/python scripts/bench_context.py
可选：--pages 100 1000 5000 --overview-sections 40
只报**字符数与 UTF-8 字节数**——没有与目标模型对应的 tokenizer，不折算 token。实际 token 成本还受
`read_file` 截断、上下文压缩与缓存影响，以真实运行的用量行为准。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guanlan.catalog import build_catalog, overview_outline  # noqa: E402
from guanlan.ingest import build_ingest_prompt  # noqa: E402

# read_file 不带 limit 时最多回这么多行（agentao `MAX_LINES_DEFAULT`）。
READ_FILE_MAX_LINES = 2000
# agentao 单个工具结果超过这么多字符就落盘，上下文只留首 8k + 尾 32k（`TOOL_OUTPUT_SAVE_THRESHOLD`）。
# 旧做法「整读」大 index 时，模型实际看到的不超过这个数——便宜是因为看不全，不是因为省。
TOOL_OUTPUT_SAVE_THRESHOLD = 40_000


def make_kb(root: Path, pages: int, sections: int) -> None:
    for name in ("AGENTAO.md", "SCHEMA.md"):
        (root / name).write_text("# Benchmark\n", encoding="utf-8")
    (root / "raw").mkdir()
    dirs = ("sources", "entities", "concepts")
    for d in dirs:
        (root / "wiki" / d).mkdir(parents=True)
    # 按 conventions 的 index 行格式：`- [标题](dir/stem.md) — 一句话（别名：…）`。
    index = ["# 索引", ""]
    by_dir: dict[str, list[str]] = {d: [] for d in dirs}
    for i in range(pages):
        d = dirs[i % 3]
        stem = f"page-{i:05d}"
        title = f"页面标题{i}"
        aliases = [f"别名{i}", f"Alias{i}"] if i % 4 == 0 else []
        fm_aliases = f"aliases: [{', '.join(repr(a) for a in aliases)}]\n" if aliases else ""
        (root / "wiki" / d / f"{stem}.md").write_text(
            f"---\ntitle: '{title}'\ntype: {d[:-1] if d != 'entities' else 'entity'}\n"
            f"tags: []\nsources: []\n{fm_aliases}last_updated: 2026-10-17\n---\n\n正文。\n",
            encoding="utf-8",
        )
        note = f"（别名：{'/'.join(aliases)}）" if aliases else ""
        by_dir[d].append(f"- [{title}]({d}/{stem}.md) — 关于第 {i} 个主题的一句话摘要{note}")
    for d in dirs:
        index += [f"## {d}", "", *by_dir[d], ""]
    (root / "wiki" / "index.md").write_text("\n".join(index), encoding="utf-8")
    overview = ["# 总览", ""]
    for s in range(sections):
        overview += [f"## 专题 {s}", "", "跨资料综述段落，对比各来源的论断与分歧。" * 20, ""]
    (root / "wiki" / "overview.md").write_text("\n".join(overview), encoding="utf-8")
    (root / "wiki" / "log.md").write_text("# 时间线\n", encoding="utf-8")


def _size(text: str) -> str:
    return f"{len(text):>9,} 字 {len(text.encode('utf-8')):>10,} B"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, nargs="+", default=[100, 1000, 5000])
    parser.add_argument("--overview-sections", type=int, default=40)
    args = parser.parse_args()

    for pages in args.pages:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_kb(root, pages, args.overview_sections)
            wiki = root / "wiki"
            index = (wiki / "index.md").read_text(encoding="utf-8")
            overview = (wiki / "overview.md").read_text(encoding="utf-8")
            catalog = build_catalog(wiki)
            outline = overview_outline(wiki / "overview.md") or ""
            prompt = build_ingest_prompt(root, "doc.md")
            index_lines = index.count("\n") + 1
            print(f"== {pages} 页（index {index_lines} 行"
                  f"{'，超 read_file 默认 2000 行' if index_lines > READ_FILE_MAX_LINES else ''}）")
            print(f"   旧 · 整读 index      {_size(index)}"
                  f"{'（模型实际只见首尾 4 万字）' if len(index) > TOOL_OUTPUT_SAVE_THRESHOLD else ''}")
            print(f"   旧 · 整读 overview   {_size(overview)}")
            print(f"   新 · 名录            {_size(catalog.text)}  预算内={not catalog.over_budget}")
            print(f"   新 · overview 结构   {_size(outline)}")
            print(f"   新 · prompt 全文     {_size(prompt)}")
            old = len(index) + len(overview)
            new = len(catalog.text) + len(outline)
            print(f"   名录+结构 / index+overview = {new / old:.2f}")


if __name__ == "__main__":
    main()
