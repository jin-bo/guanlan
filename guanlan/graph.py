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
from .graphstats import (
    FragileTopology,
    detect_communities,
    fragile_topology,
    hub_nodes,
    isolated_communities,
    thin_intercommunity_links,
    undirected_adjacency,
)
from .pages import (
    WIKILINK_RE,
    iter_pages,
    link_resolution_index,
    link_stem,
    load_page,
    page_title,
    page_type,
    resolve_owner,
)
from .paths import require_kb_root

__all__ = [
    "Node",
    "Edge",
    "Graph",
    "build_graph",
    "compute_orphans",
    "compute_backlinks",
    "graph_to_dict",
    "dump_json",
    "render_html",
    "write_graph",
    "build_and_write_graph",
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

    每个 `[[…]]` 经 `resolve_owner`（精确 `link_stem` + fold 兜底）解析为 owner 路径后分类
    （决策P3-6 / P3.1-5 / P3.8-3，与 check/heal/Web 同一张解析表）：
      ① owner 命中**内容页**（直接 stem / 别名 / fold variant）→ resolved 边，归到拥有页节点
         （不建别名/fold 幽灵节点）；
      ② owner 命中 **config 页**（index/log/overview）→ 丢弃（不建边、不算断链）；
      ③ owner is None（精确 + fold 皆不中）→ broken 边（resolved=False）。
    自环保留为边但 `(source,target)` 去重，避免可视化重边。
    """
    wiki = Path(wiki)
    root = wiki.parent

    # 节点 id = stem（小写），**必须**等于链接解析键：边目标经 link_stem 归一为 stem，邻接表按
    # 此 id 寻址，故唯有 id==stem 才能让 graph.broken ≡ check.wikilink.broken（决策P3-6）。
    # 这意味着整个系统（含 check 与本模块共用的同一张 link_resolution_index 解析表）都假设**页面
    # stem 全库唯一**——
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

    # 解析表 = 精确 stem/别名（含 config）∪ 安全 fold variant → owner path，与 check/heal/Web 同一
    # 归口（P3.8，决策P3.8-3）。content_path_to_id：owner 路径 → 节点 id（与 idx 的 owner 值同基，皆
    # 相对库根 posix）；config 页不在其中，故指向 config 的链接经 resolve_owner 命中却归类为"丢弃"。
    idx = link_resolution_index(wiki)
    content_path_to_id = {n.path: n.id for n in nodes}

    adjacency: dict[str, set[str]] = {nid: set() for nid in node_ids}
    resolved_pairs: set[tuple[str, str]] = set()
    broken_pairs: set[tuple[str, str]] = set()
    for nid, body in bodies:
        for raw in WIKILINK_RE.findall(body):
            target = link_stem(raw)
            if not target:
                continue
            owner = resolve_owner(raw, idx)
            if owner is None:
                broken_pairs.add((nid, target))  # 精确 + fold 皆不中 → 断链边（断链键用 link_stem）。
            elif owner in content_path_to_id:
                tid = content_path_to_id[owner]  # 命中内容页（直接/别名/fold）→ 归到拥有页节点。
                resolved_pairs.add((nid, tid))
                adjacency[nid].add(tid)
            # else: owner 非空但非内容页（config index/log/overview）→ 丢弃（决策P3-6）。

    edge_tuples = [(s, t, True) for s, t in resolved_pairs]
    edge_tuples += [(s, t, False) for s, t in broken_pairs]
    edge_tuples.sort(key=lambda e: (e[0], e[1]))  # 稳定排序，幂等可重建。
    edges = [Edge(s, t, r) for s, t, r in edge_tuples]
    broken = [e for e in edges if not e.resolved]

    nodes.sort(key=lambda n: n.id)
    return Graph(nodes=nodes, edges=edges, adjacency=adjacency, broken=broken)


def _inlink_counts(g: Graph) -> dict[str, int]:
    """每个节点 id 的入度（resolved 边、**排自环**、排 broken）——入链口径的**单一归口**。

    `compute_orphans`（入度==0）与 `compute_backlinks`（入度本身）皆由此派生，故「改一处即见另一处」
    是字面真的：自环（`source != target`）/ broken（`e.resolved`）的判定只在这一处。`e.target in counts`
    护 `+=` 不 KeyError（resolved 边目标恒为节点 id，此为防御性兜底）。
    """
    counts = {n.id: 0 for n in g.nodes}
    for e in g.edges:
        if e.resolved and e.source != e.target and e.target in counts:
            counts[e.target] += 1
    return counts


def compute_orphans(g: Graph) -> list[Node]:
    """入度为 0 的节点（**自环不算入链**——孤儿定义是"无任何**其他**页链入"，§5.1）。

    供 graph 的 stats.orphans 与 lint.orphan 共用，保证两处口径一致（建图逻辑单份）。入链口径
    复用 `_inlink_counts` 归口（与 `compute_backlinks` 同一份，不漂移）。
    """
    counts = _inlink_counts(g)
    return [n for n in g.nodes if counts[n.id] == 0]


def compute_backlinks(g: Graph) -> dict[str, int]:
    """每页入链数（resolved 边入度，排自环、排 broken）。键用 `node.path`（相对库根 posix），对齐
    `search.DocBag.page`（决策P5.3-3，供 P5.0 检索 backlink 重排做文档先验）。

    与 `compute_orphans` **同一入链口径**——同走 `_inlink_counts` 归口，`compute_orphans` 即「入度==0」、
    本函数即「入度」，二者绝不漂移。alias/fold 链已在 `build_graph` 解析期归到拥有页节点
    （决策P3-6/P3.1-5/P3.8-3），故反链计数 ≡ graph 入度 ≡ lint/check 同口径。
    """
    counts = _inlink_counts(g)
    return {n.path: counts[n.id] for n in g.nodes}  # 转 path 键，对齐 DocBag.page


def graph_to_dict(
    g: Graph,
    *,
    communities: dict[str, int] | None = None,
    frag: FragileTopology | None = None,
) -> dict:
    """graph.json 的稳定数据结构（§6.2 + P3.5 §3.2 富化）。

    stats.edges = resolved 边数（adjacency 关系数）；stats.broken = 断链边数；二者分列。
    edges 数组含 resolved + broken 两类，按 (source,target) 排序。
    **P3.5 additive**：stats 多 `communities` 计数、每节点多 `community` 社区号（既有键/顺序/排序
    一字不动，决策P3.5-4）。`communities` 可由调用方算好传入（避免一次写盘重复算确定性社区）。
    **P3.6 additive**：stats 再多 `bridges`/`cut_vertices` 两计数（接在 `communities` 之后，**镜像
    既有 `stats.orphans`**——计数进 stats、明细不进 node/edge 字典，由 html/lint 承载，决策P3.6-6）。
    `frag` 同 `communities` 可由调用方算好传入，避免一次写盘（json+html）重复跑 Tarjan。
    """
    if communities is None:
        communities = detect_communities(g)
    if frag is None:
        frag = fragile_topology(g)
    resolved_count = sum(1 for e in g.edges if e.resolved)
    return {
        "generated_from": "wiki/",
        "stats": {
            "nodes": len(g.nodes),
            "edges": resolved_count,
            "broken": len(g.broken),
            "orphans": len(compute_orphans(g)),
            "communities": len(set(communities.values())),
            "bridges": len(frag.bridges),
            "cut_vertices": len(frag.cut_vertices),
        },
        "nodes": [
            {
                "id": n.id,
                "title": n.title,
                "type": n.type,
                "path": n.path,
                "community": communities[n.id],
            }
            for n in g.nodes
        ],
        "edges": [{"source": e.source, "target": e.target, "resolved": e.resolved} for e in g.edges],
    }


def dump_json(
    g: Graph,
    *,
    communities: dict[str, int] | None = None,
    frag: FragileTopology | None = None,
) -> str:
    """渲染 graph.json 文本：同一 wiki 两次产出**字节级一致**（稳定排序 + 无时间戳/随机）。"""
    return (
        json.dumps(
            graph_to_dict(g, communities=communities, frag=frag),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def _render_topology_hints(
    g: Graph, communities: dict[str, int], *, frag: FragileTopology | None = None
) -> str:
    """末尾「拓扑提示」段：确定性列出枢纽 / 稀疏跨社区链接 / 孤岛 / 割边 / 割点（与 lint 同数据，文字呈现）。

    纯静态列表、Python 端稳定排序生成，天然字节稳定（决策P3.5-5，守决策P3-7）。
    `frag` 可由调用方传入复用同一份 Tarjan 结果（默认 None 即用同一份 `adj` 内部算）。
    """
    title_by_id = {n.id: n.title for n in g.nodes}
    path_by_id = {n.id: n.path for n in g.nodes}
    adj = undirected_adjacency(g)  # 算一次，三特征函数共用（免 3× 重复构建）。

    def _block(heading: str, items: list[str]) -> list[str]:
        out = [f"<h3>{heading}</h3>"]
        if items:
            out.append("<ul class='hints'>")
            out.extend(f"<li>{it}</li>" for it in items)
            out.append("</ul>")
        else:
            out.append("<p class='meta'>无</p>")
        return out

    hubs = [
        f"<strong>{html.escape(title_by_id.get(nid, nid))}</strong> "
        f"<code>{html.escape(nid)}</code> — 无向度 {deg}"
        for nid, deg in hub_nodes(g, communities, adj=adj)
    ]
    thin: list[str] = []
    for u, v in thin_intercommunity_links(g, communities, adj=adj):
        a, b = sorted((communities[u], communities[v]))
        thin.append(
            f"社区 {a}↔{b}：<code>{html.escape(u)}</code>—<code>{html.escape(v)}</code>"
        )
    silos = [
        f"社区 {c}：" + "、".join(html.escape(path_by_id.get(mid, mid)) for mid in members)
        for c, members in isolated_communities(g, communities, adj=adj)
    ]
    # P3.6 割边/割点（删之即断的单点故障，决策P3.6-7：仍纯静态文字列表）。
    if frag is None:
        frag = fragile_topology(g, adj=adj)
    bridges = [
        f"<code>{html.escape(u)}</code>—<code>{html.escape(v)}</code>"
        for u, v in frag.bridges
    ]
    cuts = [
        f"<strong>{html.escape(title_by_id.get(nid, nid))}</strong> "
        f"<code>{html.escape(path_by_id.get(nid, nid))}</code>"
        for nid in frag.cut_vertices
    ]

    parts = ["<h2>拓扑提示 <span class='n'>(确定性)</span></h2>"]
    parts += _block("枢纽节点", hubs)
    parts += _block("稀疏跨社区链接", thin)
    parts += _block("孤岛社区", silos)
    parts += _block("割边（单点故障边）", bridges)
    parts += _block("割点（关节点）", cuts)
    return "\n".join(parts)


def render_html(
    g: Graph,
    *,
    communities: dict[str, int] | None = None,
    frag: FragileTopology | None = None,
) -> str:
    """渲染自包含、零网络的最小只读邻接列表静态视图（决策P3-7）。

    单文件、内联数据、无 CDN/第三方库、无图形布局算法——纯列表结构在 Python 端按稳定排序生成，
    天然字节稳定、可幂等重建。`type` 分组 → 每页 + 其 resolved 外链邻接，孤儿/断链以文字标注。
    **P3.5**：每节点尾部加社区徽标、顶部摘要加社区数、末尾加确定性「拓扑提示」段（决策P3.5-5，
    仍守决策P3-7 零 JS/零第三方库/纯静态列表）。**P3.6**：拓扑提示段再加「割边」「割点」两块、
    摘要加割边/割点计数（决策P3.6-7，仍纯静态文字）。`frag` 同 `communities` 可由调用方传入，
    一次 `render_html` 内 stats 摘要与拓扑提示段共用同一份 Tarjan 结果，免重复算。
    """
    if communities is None:
        communities = detect_communities(g)
    if frag is None:
        frag = fragile_topology(g)
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
            comm_badge = f" <span class='comm'>社区 {communities[n.id]}</span>"
            head = (
                f"<strong>{html.escape(n.title)}</strong> "
                f"<code>{html.escape(n.path)}</code>{comm_badge}{tags}"
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
    # 一次构建，内联数据与统计摘要共用，免重复计算（communities/frag 均复用上面这份）。
    data = graph_to_dict(g, communities=communities, frag=frag)
    data_blob = json.dumps(data, ensure_ascii=False, indent=2).replace("<", "\\u003c")
    stats = data["stats"]
    summary = (
        f"节点 {stats['nodes']} · 链接 {stats['edges']} · "
        f"断链 {stats['broken']} · 孤儿 {stats['orphans']} · 社区 {stats['communities']} · "
        f"割边 {stats['bridges']} · 割点 {stats['cut_vertices']}"
    )
    parts.append(_render_topology_hints(g, communities, frag=frag))
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
  .comm {{ color: #3060a0; font-size: .82em; }}
  h3 {{ font-size: 1rem; margin: .8rem 0 .2rem; color: #444; }}
  ul.hints {{ margin: .2rem 0 .6rem 1.2rem; }}
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


def write_graph(
    g: Graph, root: Path, *, json_only: bool, communities: dict[str, int] | None = None
) -> dict[str, int]:
    """把已建好的 `Graph` 写派生 `graph/`（**只写不读、无打印**）；写失败抛 `OSError`。

    与 `build_graph`（只读 `wiki/`）分立，让调用方能把"读页"与"写派生"的 `OSError` 分开归因
    （读不可读的页 ≠ 写不进 graph/），不把读错标成"写 graph 失败"。绝不碰 `raw/`/`wiki/`。
    `communities` 可由调用方传入（默认 None 即内部算一次）并被返回，供随后打印 stats 复用，
    免重复跑确定性 Louvain。json/html 共用同一份确定性社区号 → 字节稳定。**P3.6**：割边/割点
    （`frag`）同样一次算定、json/html 共用，免一次写盘重复跑 Tarjan。
    """
    if communities is None:
        communities = detect_communities(g)
    frag = fragile_topology(g)  # 一次算定，json/html 共用（与 communities 同理）。
    graph_dir = root / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "graph.json").write_text(
        dump_json(g, communities=communities, frag=frag), encoding="utf-8"
    )
    if not json_only:
        (graph_dir / "graph.html").write_text(
            render_html(g, communities=communities, frag=frag), encoding="utf-8"
        )
    return communities


def build_and_write_graph(root: Path, *, json_only: bool) -> Graph:
    """建图并写派生 `graph/`（**无打印、无退出码**），返回 `Graph`；建/写失败抛 `OSError`。

    从 `graph_entrypoint` 抽出 IO 内核，供 Web 宿主复用（决策P4-7）：Web 写作业 worker 用
    进程级 `redirect_stdout` 捕获 ingest 输出，故任何**并发**的 stdout 打印者都是隐患——`/graph`
    端点改调本函数（不打印），就不会把"已写 graph"那行漏进某个并行 ingest 作业的捕获里。
    只读 `wiki/`、只写派生 `graph/`，绝不碰 `raw/`/`wiki/`。
    """
    g = build_graph(root / "wiki")
    write_graph(g, root, json_only=json_only)
    return g


def graph_entrypoint(root_dir: str | Path, *, json_only: bool) -> int:
    """`guanlan graph` 的单一落地：建图 → 写 graph/ → 退出码。

    只读 `wiki/`、只写派生 `graph/`，**绝不**碰 `raw/`/`wiki/`。写 graph/ 失败 → EXIT_USAGE。
    """
    try:
        root = require_kb_root(root_dir, writable=False)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    g = build_graph(root / "wiki")  # 读 wiki/：读不可读页的 OSError 直接外抛，不混入"写失败"标签。
    try:
        communities = write_graph(g, root, json_only=json_only)
    except OSError as exc:
        print(f"写 {root / 'graph'} 失败：{exc}", file=sys.stderr)
        return EXIT_USAGE

    stats = graph_to_dict(g, communities=communities)["stats"]  # 复用已算社区号，不二次跑 Louvain。
    written = "graph.json" if json_only else "graph.json + graph.html"
    print(
        f"✓ 已写 graph/（{written}）：节点 {stats['nodes']} · 链接 {stats['edges']} · "
        f"断链 {stats['broken']} · 孤儿 {stats['orphans']} · 社区 {stats['communities']}。"
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
