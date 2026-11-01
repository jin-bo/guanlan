"""Web 图可视化的视图裁剪与聚合（P4.27，见 docs/P4.27-Web图可视化.md）。**零 LLM、零写。**

三个视图，全部从内存图算出，不写 `graph/`：

- `overview`：节点 = 页数 ≥2 的社区（以锚页为 id），边 = 社区间相连的无向页对数；
- `community`：按锚页寻址，节点 = 锚页当前所在社区的页，边 = 社区内无向页对；
- `page`：单页一跳邻域（中心页 + 邻居 + 它们之间的边）。

口径全部复用既有核心：`build_graph`（建图，P3）、`undirected_adjacency` / `detect_communities`
（无向化与社区，P3.5）、`link_resolution_index` / `resolve_owner`（页名归口，P3.8）。本模块只做裁剪、聚合
与一个单槽 memo，不另算任何图论量（决策P4.27-5/8）。纯函数为主、不依赖 FastAPI，端点见 `app.py`。
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..graph import Graph, Node, build_graph
from ..graphstats import detect_communities, undirected_adjacency
from ..pages import resolve_owner

__all__ = [
    "GRAPH_VIEW_MAX_NODES",
    "GraphSnapshot",
    "GraphViewCache",
    "GraphViewNotFound",
    "overview_view",
    "community_view",
    "page_view",
]

# 每个视图的节点上限（决策P4.27-7；U1 实测后定）。函数调用时才读模块全局，测试可 monkeypatch 调小。
GRAPH_VIEW_MAX_NODES = 300


class GraphViewNotFound(LookupError):
    """锚页 / 页名解析不到内容页 → 端点回 404。"""


@dataclass(frozen=True)
class GraphSnapshot:
    """一次建图的只读产物：图、无向邻接、社区划分与页名解析表。memo 的值。"""

    graph: Graph
    adj: dict[str, set[str]]
    community: dict[str, int]
    nodes: dict[str, Node]
    path_to_id: dict[str, str]
    resolution: dict[str, str]
    groups: dict[int, list[str]]  # 社区号 → 成员页 id（建一次，三个视图共用）


def build_snapshot(wiki: Path) -> GraphSnapshot:
    g = build_graph(wiki)
    adj = undirected_adjacency(g)
    community = detect_communities(g, adj=adj)
    groups: dict[int, list[str]] = {}
    for nid, c in community.items():
        groups.setdefault(c, []).append(nid)
    return GraphSnapshot(
        graph=g,
        adj=adj,
        community=community,
        nodes={n.id: n for n in g.nodes},
        path_to_id={n.path: n.id for n in g.nodes},
        resolution=g.resolution,  # build_graph 已建好的同一张解析表，免整库 frontmatter 二次解析
        groups=groups,
    )


def _signature(wiki: Path) -> tuple:
    """`wiki/` 下全部 `.md` 的 (相对路径, mtime_ns, size)。比建图便宜一个量级（只 stat、不读页）。

    取全部 `.md`（含 config 页与 `iter_pages` 会跳过的位置）：范围只会比建图读到的大，多出来的只会让
    memo 多失效一次，不会漏失效。
    """
    out = []
    for p in wiki.rglob("*.md"):
        try:
            st = p.stat()
        except OSError:  # 扫描中途被删：跳过，下一次请求自然重算
            continue
        out.append((p.relative_to(wiki).as_posix(), st.st_mtime_ns, st.st_size))
    out.sort()
    return tuple(out)


class GraphViewCache:
    """单槽 memo：签名不变复用上次建好的图，变了整体重建（决策P4.27-10）。

    只借鉴 `CorpusCache` 的签名做法，不扩展检索缓存体系。并发首请求可能各建一次、结果相同，
    故不做 singleflight；锁只护单槽替换。`builds` 计数供测试断言命中。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._snap: GraphSnapshot | None = None
        self.builds = 0

    def snapshot(self, wiki: Path) -> GraphSnapshot:
        wiki = Path(wiki)
        key = (str(wiki.resolve()), _signature(wiki))
        with self._lock:
            if self._key == key and self._snap is not None:
                return self._snap
        snap = build_snapshot(wiki)
        with self._lock:
            self._key, self._snap = key, snap
            self.builds += 1
        return snap


# ───────────────────────── 共用小件 ─────────────────────────


def _degree(snap: GraphSnapshot, nid: str) -> int:
    return len(snap.adj.get(nid, ()))


def _rank_key(snap: GraphSnapshot, nid: str) -> tuple[int, str]:
    """页面视图的截断 / 锚页排序键：全图邻居数降序、id 升序（决策P4.27-6/7）。"""
    return (-_degree(snap, nid), nid)


def _anchor(snap: GraphSnapshot, members: list[str]) -> str:
    return min(members, key=lambda nid: _rank_key(snap, nid))


def _page_node(snap: GraphSnapshot, nid: str) -> dict:
    n = snap.nodes[nid]
    return {"id": n.id, "path": n.path, "title": n.title, "type": n.type, "degree": _degree(snap, nid)}


def _page_edges(snap: GraphSnapshot, keep: set[str]) -> list[dict]:
    """保留集内的无向页对；`mutual` = 双向互链（决策P4.27-5）。"""
    fwd = snap.graph.adjacency
    edges = []
    for u in sorted(keep):
        for v in sorted(snap.adj.get(u, ())):
            if u < v and v in keep:
                mutual = v in fwd.get(u, ()) and u in fwd.get(v, ())
                edges.append({"source": u, "target": v, "mutual": mutual})
    return edges


def _truncated(total: int, shown: int) -> dict | None:
    return {"total": total, "shown": shown} if shown < total else None


def _resolve(snap: GraphSnapshot, name: str) -> str:
    """页路径（相对库根，如 `wiki/entities/Foo.md`）或页名（stem / 别名 / fold 变体）→ 节点 id。

    路径精确命中优先：Web「在图中查看」手里就是路径，按 stem 再解析会被 `link_stem` 剥掉 `#`/`|` 后段而错页；
    页名经 P3.8 单一 owner 归口，不另写一套按名查找。
    """
    if name in snap.path_to_id:
        return snap.path_to_id[name]
    owner = resolve_owner(name, snap.resolution) if name and name.strip() else None
    nid = snap.path_to_id.get(owner) if owner else None
    if nid is None:
        raise GraphViewNotFound(name)
    return nid


def _community_summary(snap: GraphSnapshot, members: list[str]) -> dict:
    anchor = _anchor(snap, members)
    return {"anchor": anchor, "anchor_title": snap.nodes[anchor].title, "size": len(members)}


# ───────────────────────── 三个视图 ─────────────────────────


def overview_view(snap: GraphSnapshot) -> dict:
    """社区总览：页数 ≥2 的社区为节点（id = 锚页），单页社区只计数（决策P4.27-1/6）。"""
    limit = GRAPH_VIEW_MAX_NODES
    groups = snap.groups
    formed = {c: m for c, m in groups.items() if len(m) >= 2}
    isolated = sum(len(m) for m in groups.values() if len(m) < 2)

    summaries = {c: _community_summary(snap, m) for c, m in formed.items()}
    ranked = sorted(formed, key=lambda c: (-len(formed[c]), summaries[c]["anchor"]))
    kept = ranked[:limit]
    kept_set = set(kept)

    weight: Counter[tuple[str, str]] = Counter()
    for u, nbrs in snap.adj.items():
        cu = snap.community[u]
        if cu not in kept_set:
            continue
        for v in nbrs:
            cv = snap.community[v]
            if u < v and cv != cu and cv in kept_set:
                a, b = sorted((summaries[cu]["anchor"], summaries[cv]["anchor"]))
                weight[(a, b)] += 1

    nodes = sorted(
        (
            {"id": s["anchor"], "anchor_title": s["anchor_title"], "size": s["size"]}
            for s in (summaries[c] for c in kept)
        ),
        key=lambda n: n["id"],
    )
    edges = [{"source": a, "target": b, "weight": w} for (a, b), w in sorted(weight.items())]
    return {
        "view": "overview",
        "nodes": nodes,
        "edges": edges,
        "pages": len(snap.nodes),
        "isolated": isolated,
        "truncated": _truncated(len(formed), len(kept)),
    }


def community_view(snap: GraphSnapshot, anchor: str) -> dict:
    """社区展开：按锚页寻址，取该页**当前**所在社区（决策P4.27-6）。"""
    limit = GRAPH_VIEW_MAX_NODES
    nid = _resolve(snap, anchor)
    c = snap.community[nid]
    members = snap.groups[c]
    kept = sorted(members, key=lambda m: _rank_key(snap, m))[:limit]
    keep = set(kept)

    nodes = []
    for m in sorted(keep):
        node = _page_node(snap, m)
        node["external"] = sum(1 for v in snap.adj.get(m, ()) if snap.community[v] != c)
        nodes.append(node)
    return {
        "view": "community",
        "community": _community_summary(snap, members),
        "nodes": nodes,
        "edges": _page_edges(snap, keep),
        "truncated": _truncated(len(members), len(kept)),
    }


def page_view(snap: GraphSnapshot, name: str) -> dict:
    """单页一跳邻域：中心页恒保留，邻居按全图邻居数截取（决策P4.27-7/8）。"""
    limit = GRAPH_VIEW_MAX_NODES
    center = _resolve(snap, name)
    neighbors = sorted(snap.adj.get(center, ()), key=lambda m: _rank_key(snap, m))
    kept = neighbors[: max(limit - 1, 0)]
    keep = {center, *kept}
    members = snap.groups[snap.community[center]]
    return {
        "view": "page",
        "center": center,
        # 面包屑「总览 › 某社区 › 某页」要的上一级；单页社区（孤立页）没有可展开的社区 → None。
        "community": _community_summary(snap, members) if len(members) >= 2 else None,
        "nodes": [_page_node(snap, m) for m in sorted(keep)],
        "edges": _page_edges(snap, keep),
        "truncated": _truncated(len(neighbors) + 1, len(keep)),
    }
