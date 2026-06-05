"""确定性 wikilink graph（P3，见 docs/P3-健康与图谱.md §6 决策P3-5/6/7）。**零 LLM。**

解析 `wiki/` 下非 config 页的 `[[wikilink]]` → 有向边，产出：

- `<root>/graph/graph.json` —— 排序稳定、幂等可重建的契约（§6.2）；
- `<root>/graph/graph.html` —— 自包含、零网络的**最小只读邻接列表**静态视图（§6.3 决策P3-7）。

是 `lint` 的算法底座（`lint` 复用 `build_graph` 的邻接表与 broken 边）。建图口径与 `check`
完全一致——`graph.broken ≡ check.wikilink.broken`（决策P3-6）：链接解析集沿用 `pages` 的全页面
stem 集（含 config），指向 config 页的链接**直接丢弃**（既不建边也不算断链），唯有谁都不命中的记 broken。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .pages import (
    WIKILINK_RE,
    iter_pages,
    link_stem,
    link_target_stems,
    load_page,
    page_title,
    page_type,
)
from .paths import require_kb_root

__all__ = [
    "Node",
    "Edge",
    "Graph",
    "build_graph",
    "compute_orphans",
    "graph_to_dict",
    "dump_json",
    "render_html",
    "graph_entrypoint",
    "main",
]


@dataclass(frozen=True)
class Node:
    id: str  # = pages 解析键（stem 小写），与 check/lint 完全一致。
    title: str
    type: str
    path: str  # 相对知识库根的 posix 路径，如 wiki/entities/Foo.md。


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    resolved: bool  # False 即断链边（target 谁都没命中）。


@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)  # resolved + broken，按 (source,target) 排序。
    adjacency: dict[str, set[str]] = field(default_factory=dict)  # 仅 resolved 出边。
    broken: list[Edge] = field(default_factory=list)  # edges 中 resolved=False 的子集。


def build_graph(wiki: Path) -> Graph:
    """建图：节点=非 config 页，边=已解析 `[[wikilink]]`（含断链边）。

    每个 `[[…]]` 经 `link_stem` 归一后三分类（决策P3-6）：
      ① 命中非 config 页 → resolved 边（进 adjacency）；
      ② 命中 config 页（index/log/overview）→ 丢弃（不建边、不算断链）；
      ③ 谁都没命中 → broken 边（resolved=False）。
    自环保留为边但 `(source,target)` 去重，避免可视化重边。
    """
    wiki = Path(wiki)
    root = wiki.parent

    # 节点 id = stem（小写），**必须**等于链接解析键：边目标经 link_stem 归一为 stem，邻接表按
    # 此 id 寻址，故唯有 id==stem 才能让 graph.broken ≡ check.wikilink.broken（决策P3-6）。
    # 这意味着整个系统（含 P2 check 的 link_target_stems）都假设**页面 stem 全库唯一**——
    # 这是 wikilink 按名解析的固有前提，由命名约定保证。若两张非 config 页 stem 相同
    # （如 sources/Foo.md 与 entities/Foo.md），它们会共享 id、在邻接/孤儿上被视作同一逻辑节点；
    # 这是 stem 寻址模型下不可消除的歧义，应由"页名唯一"约定避免，而非在此改用路径 id（那会破坏
    # 上述不变式）。重名探测属"duplicate-stem lint"，超出 P3 §6 范围（§10 推后）。
    nodes: list[Node] = []
    node_ids: set[str] = set()
    bodies: list[tuple[str, str]] = []  # (node_id, body)
    for path in iter_pages(wiki):
        meta, body = load_page(path)  # 容错档：坏 frontmatter 不抛、照常建节点。
        nid = path.stem.lower()
        nodes.append(
            Node(
                id=nid,
                title=page_title(meta, path.stem),
                type=page_type(meta),
                path=path.relative_to(root).as_posix(),
            )
        )
        node_ids.add(nid)
        bodies.append((nid, body))

    # 解析集 = 全页面 stem（含 config），与 check 同口径；config-only stem 用于"丢弃"分类。
    all_stems = link_target_stems(wiki)

    adjacency: dict[str, set[str]] = {nid: set() for nid in node_ids}
    resolved_pairs: set[tuple[str, str]] = set()
    broken_pairs: set[tuple[str, str]] = set()
    for nid, body in bodies:
        for raw in WIKILINK_RE.findall(body):
            target = link_stem(raw)
            if not target:
                continue
            if target in node_ids:
                resolved_pairs.add((nid, target))
                adjacency[nid].add(target)
            elif target in all_stems:
                continue  # 指向 config 页 → 丢弃（决策P3-6）。
            else:
                broken_pairs.add((nid, target))

    edge_tuples = [(s, t, True) for s, t in resolved_pairs]
    edge_tuples += [(s, t, False) for s, t in broken_pairs]
    edge_tuples.sort(key=lambda e: (e[0], e[1]))  # 稳定排序，幂等可重建。
    edges = [Edge(s, t, r) for s, t, r in edge_tuples]
    broken = [e for e in edges if not e.resolved]

    nodes.sort(key=lambda n: n.id)
    return Graph(nodes=nodes, edges=edges, adjacency=adjacency, broken=broken)


def compute_orphans(g: Graph) -> list[Node]:
    """入度为 0 的节点（**自环不算入链**——孤儿定义是"无任何**其他**页链入"，§5.1）。

    供 graph 的 stats.orphans 与 lint.orphan 共用，保证两处口径一致（建图逻辑单份）。
    """
    has_inlink = {e.target for e in g.edges if e.resolved and e.source != e.target}
    return [n for n in g.nodes if n.id not in has_inlink]


def graph_to_dict(g: Graph) -> dict:
    """graph.json 的稳定数据结构（§6.2）。

    stats.edges = resolved 边数（adjacency 关系数）；stats.broken = 断链边数；二者分列。
    edges 数组含 resolved + broken 两类，按 (source,target) 排序。
    """
    resolved_count = sum(1 for e in g.edges if e.resolved)
    return {
        "generated_from": "wiki/",
        "stats": {
            "nodes": len(g.nodes),
            "edges": resolved_count,
            "broken": len(g.broken),
            "orphans": len(compute_orphans(g)),
        },
        "nodes": [{"id": n.id, "title": n.title, "type": n.type, "path": n.path} for n in g.nodes],
        "edges": [{"source": e.source, "target": e.target, "resolved": e.resolved} for e in g.edges],
    }


def dump_json(g: Graph) -> str:
    """渲染 graph.json 文本：同一 wiki 两次产出**字节级一致**（稳定排序 + 无时间戳/随机）。"""
    return json.dumps(graph_to_dict(g), ensure_ascii=False, indent=2) + "\n"


def render_html(g: Graph) -> str:
    """渲染自包含、零网络的最小只读邻接列表静态视图（决策P3-7）。

    单文件、内联数据、无 CDN/第三方库、无图形布局算法——纯列表结构在 Python 端按稳定排序生成，
    天然字节稳定、可幂等重建。`type` 分组 → 每页 + 其 resolved 外链邻接，孤儿/断链以文字标注。
    """
    orphan_ids = {n.id for n in compute_orphans(g)}
    broken_by_source: dict[str, list[str]] = {}
    for e in g.broken:
        broken_by_source.setdefault(e.source, []).append(e.target)

    # 节点按 (type, id) 分组；type 排序稳定。
    types = sorted({n.type for n in g.nodes})
    parts: list[str] = []
    for type_ in types:
        group = [n for n in g.nodes if n.type == type_]
        parts.append(f"<h2>{html.escape(type_)} <span class='n'>({len(group)})</span></h2>")
        parts.append("<ul class='nodes'>")
        for n in group:
            tags = " <span class='orphan'>[孤儿]</span>" if n.id in orphan_ids else ""
            head = (
                f"<strong>{html.escape(n.title)}</strong> "
                f"<code>{html.escape(n.path)}</code>{tags}"
            )
            parts.append(f"<li>{head}")
            out_links = sorted(g.adjacency.get(n.id, set()))
            broken_targets = sorted(broken_by_source.get(n.id, []))
            if out_links or broken_targets:
                parts.append("<ul class='links'>")
                for t in out_links:
                    parts.append(f"<li>→ <code>{html.escape(t)}</code></li>")
                for t in broken_targets:
                    parts.append(
                        f"<li class='broken'>⚠ [[{html.escape(t)}]] <span>（断链）</span></li>"
                    )
                parts.append("</ul>")
            parts.append("</li>")
        parts.append("</ul>")

    # 内联 graph.json：转义所有 < 为 <，彻底杜绝 title/path 里的 </script>、<!-- 等
    # 截断或干扰脚本块（比仅转义 </ 更稳妥；JSON 里 < 解析回 <，语义无损）。
    data = graph_to_dict(g)  # 一次构建，内联数据与统计摘要共用，免重复计算。
    data_blob = json.dumps(data, ensure_ascii=False, indent=2).replace("<", "\\u003c")
    stats = data["stats"]
    summary = (
        f"节点 {stats['nodes']} · 链接 {stats['edges']} · "
        f"断链 {stats['broken']} · 孤儿 {stats['orphans']}"
    )
    body = "\n".join(parts)
    return f"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>观澜 graph — wiki/</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 60rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
  h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 1.5rem; border-bottom: 1px solid #ddd; }}
  code {{ background: #f4f4f4; padding: 0 .25rem; border-radius: 3px; }}
  .n {{ color: #888; font-weight: normal; }}
  ul.links {{ margin: .2rem 0 .6rem 1.2rem; }}
  .orphan {{ color: #b06000; }} .broken {{ color: #b00020; }}
  .meta {{ color: #666; }}
</style>
</head>
<body>
<h1>观澜 wiki 链接图</h1>
<p class="meta">{html.escape(summary)} —— 由 <code>guanlan graph</code> 确定性生成（派生物，可重建）。</p>
<script type="application/json" id="graph-data">{data_blob}</script>
{body}
</body>
</html>
"""


def graph_entrypoint(root_dir: str | Path, *, json_only: bool) -> int:
    """`guanlan graph` 的单一落地：建图 → 写 graph/ → 退出码。

    只读 `wiki/`、只写派生 `graph/`，**绝不**碰 `raw/`/`wiki/`。写 graph/ 失败 → EXIT_USAGE。
    """
    try:
        root = require_kb_root(root_dir, writable=False)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    g = build_graph(root / "wiki")
    graph_dir = root / "graph"
    try:
        graph_dir.mkdir(parents=True, exist_ok=True)
        (graph_dir / "graph.json").write_text(dump_json(g), encoding="utf-8")
        if not json_only:
            (graph_dir / "graph.html").write_text(render_html(g), encoding="utf-8")
    except OSError as exc:
        print(f"写 {graph_dir} 失败：{exc}", file=sys.stderr)
        return EXIT_USAGE

    stats = graph_to_dict(g)["stats"]
    written = "graph.json" if json_only else "graph.json + graph.html"
    print(
        f"✓ 已写 graph/（{written}）：节点 {stats['nodes']} · 链接 {stats['edges']} · "
        f"断链 {stats['broken']} · 孤儿 {stats['orphans']}。"
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.graph` 入口（与 `guanlan graph` 共享 graph_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.graph",
        description="确定性建图：解析 [[wikilink]] → graph.json + graph.html。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("--json-only", action="store_true", help="只写 graph.json，跳过 graph.html")
    args = parser.parse_args(argv)
    return graph_entrypoint(args.dir, json_only=args.json_only)


if __name__ == "__main__":
    raise SystemExit(main())
