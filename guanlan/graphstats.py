"""确定性图拓扑分析（P3.5，见 docs/P3.5-图谱分析.md）。**零 LLM、零新依赖。**

在 `graph.build_graph` 已建好的 `Graph` 邻接表上算**确定性**社区与拓扑特征——不重新 walk/parse、
不引 networkx/python-louvain（决策P3.5-3/2）。对外纯函数：

- `undirected_adjacency`：resolved 有向邻接 → 无向投影（断链不算连通、**显式过滤自环**）；
- `detect_communities`：手写**确定性 Louvain**，node_id → 规范化社区号（0..k-1），同图字节稳定；
- `hub_nodes` / `thin_intercommunity_links` / `isolated_communities`：三类拓扑特征，供 `lint` 出建议、
  `graph` 富化 html 拓扑提示段。

确定性三支柱（决策P3.5-2）：① 每轮按 node_id 升序遍历；② 仅 ΔQ > 0 才移动、平局优先当前社区、
其次社区最小成员 id 最小；③ 规范化重编号（按社区最小原始成员 id 升序赋 0..k-1）——故社区号与
遍历/聚合过程无关，`graph.json` 保持字节稳定。无 RNG。
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型；运行期不 import graph，避免 graph↔graphstats 循环依赖。
    from .graph import Graph

__all__ = [
    "HUB_SIGMA",
    "HUB_MIN_DEGREE",
    "MIN_COMMUNITY_SIZE",
    "LOUVAIN_RESOLUTION",
    "undirected_adjacency",
    "detect_communities",
    "hub_nodes",
    "thin_intercommunity_links",
    "isolated_communities",
]

# 阈值常量单一归口（决策P3.5-8），无魔数散落。
HUB_SIGMA = 2.0  # 枢纽度阈值 = 均值 + HUB_SIGMA·σ（总体标准差）。
HUB_MIN_DEGREE = 5  # 枢纽绝对度地板：低于它即便过 σ 阈值也不报（小库不被噪声淹没）。
MIN_COMMUNITY_SIZE = 2  # 孤岛社区最小规模：单节点社区（孤儿）不报 isolated_community。
LOUVAIN_RESOLUTION = 1.0  # 模块度分辨率 γ；1.0 = 标准 Louvain。

_MOD_MIN = 1e-7  # 外层聚合停止阈：模块度增量小于它即收敛（与 python-louvain 同量级）。


def undirected_adjacency(g: Graph) -> dict[str, set[str]]:
    """把 resolved 有向邻接对称化为无向图（决策P3.5-9/10）。

    拓扑看连通性、方向只在建边时有意义；**断链不算连通**（broken 不构成入链，与 orphan 口径一致）。
    **显式过滤 source == target 自环**（决策P3.5-11）：`build_graph` 只对 (source,target) 去重、
    **不删自环**（graph.py:82），自链页若不过滤会被计入 hub 度数——与 `compute_orphans`
    「自环不算入链」(graph.py:147) 同口径。返回含**全部**节点（孤立点 → 空集）。
    """
    adj: dict[str, set[str]] = {n.id: set() for n in g.nodes}
    for e in g.edges:
        if not e.resolved or e.source == e.target:
            continue
        if e.source in adj and e.target in adj:
            adj[e.source].add(e.target)
            adj[e.target].add(e.source)
    return adj


# ---------------------------------------------------------------------------
# 确定性 Louvain（手写，零依赖）
# ---------------------------------------------------------------------------


class _Status:
    """单层 Louvain 局部移动的可变记账（com=社区标签、node=当前层（超）节点）。

    `degrees`：社区 Σ_tot（社区所有节点的度和）；`internals`：社区 Σ_in（社区内部边权×2 含自环）；
    `gdegrees`：节点度（自环计两次）；`loops`：节点自环权。初始每节点自成一社区（标签 = 节点自身）。
    """

    __slots__ = ("node2com", "degrees", "internals", "gdegrees", "loops")

    def __init__(self, graph: dict) -> None:
        self.node2com: dict = {}
        self.degrees: dict = {}
        self.internals: dict = {}
        self.gdegrees: dict = {}
        self.loops: dict = {}
        for node, nbrs in graph.items():
            deg = 0.0
            for v, w in nbrs.items():
                deg += w
                if v == node:  # 自环：度计两次。
                    deg += w
            self_w = nbrs.get(node, 0.0)
            self.node2com[node] = node
            self.gdegrees[node] = deg
            self.degrees[node] = deg
            self.loops[node] = self_w
            self.internals[node] = self_w


def _total_weight(graph: dict) -> float:
    """图总权 m = (Σ 度)/2（自环在度里计两次，故此口径与模块度公式自洽）。"""
    deg_sum = 0.0
    for node, nbrs in graph.items():
        for v, w in nbrs.items():
            deg_sum += w
            if v == node:
                deg_sum += w
    return deg_sum / 2.0


def _neigh_communities(node, graph: dict, node2com: dict) -> dict:
    """node 到各邻居社区的权重和（**排除自环**）：com → Σ 权。"""
    weights: dict = {}
    for nbr, w in graph[node].items():
        if nbr == node:
            continue
        com = node2com[nbr]
        weights[com] = weights.get(com, 0.0) + w
    return weights


def _one_level(graph: dict, status: _Status, resolution: float, m: float) -> None:
    """单层局部移动：反复按 node_id 升序遍历，把节点移入 ΔQ 最大且 **> 0** 的邻居社区。

    平局优先保持当前社区（best 初值 = 当前社区、增益 0、严格 `>` 才覆盖），其次社区标签最小——
    标签经 `_renumber` 规范化后即「社区最小原始成员 id」次序（决策P3.5-2）。全候选 ΔQ ≤ 0 则原地不动，
    绝不做非正向移动 → 每次移动严格提升有界模块度 ⇒ 必然收敛、不振荡。
    """
    node_order = sorted(graph)  # 节点集每层固定，一次排序即可（每轮按同一升序遍历，决策P3.5-2）。
    modified = True
    while modified:
        modified = False
        for node in node_order:
            com_node = status.node2com[node]
            deg_node = status.gdegrees[node]
            degc_totw = deg_node / (2.0 * m)
            neigh = _neigh_communities(node, graph, status.node2com)
            dnc_node = neigh.get(com_node, 0.0)
            # 先把 node 从当前社区摘出（degrees/internals 回到「不含 node」态）。
            status.degrees[com_node] -= deg_node
            status.internals[com_node] -= dnc_node + status.loops[node]
            # 增益口径同 python-louvain：边项 = γ·dnc（不除 m），罚项 = Σ_tot·k_i/(2m)。
            remove_cost = -resolution * dnc_node + status.degrees[com_node] * degc_totw
            best_com = com_node
            best_increase = 0.0
            for com in sorted(neigh):  # 确定性：按社区标签升序。
                incr = remove_cost + resolution * neigh[com] - status.degrees[com] * degc_totw
                if incr > best_increase:
                    best_increase = incr
                    best_com = com
            # 落位（best_com == com_node 时即原样放回）。
            status.degrees[best_com] += deg_node
            status.internals[best_com] += neigh.get(best_com, 0.0) + status.loops[node]
            status.node2com[node] = best_com
            if best_com != com_node:
                modified = True


def _modularity(status: _Status, m: float, resolution: float) -> float:
    """当前划分的模块度 Q = Σ_c [γ·Σ_in(c)/m − (Σ_tot(c)/2m)²]（口径同 python-louvain）。"""
    q = 0.0
    for com in set(status.node2com.values()):
        q += status.internals[com] * resolution / m - (status.degrees[com] / (2.0 * m)) ** 2
    return q


def _renumber(node2com: dict, member_min: dict) -> tuple[dict, dict]:
    """规范化重编号：按「社区最小原始成员 id」升序给社区赋 0..k-1（决策P3.5-2 步骤 5）。

    `member_min`：当前层（超）节点 → 其最小原始成员 id（字符串）。返回
    `(mapping: 当前层节点 → 规范社区号, com_member_min: 规范社区号 → 最小原始成员)`——
    后者喂给聚合后的下一层，使各层社区号次序始终对齐「最小原始成员」，平局口径稳定。
    """
    com_min: dict = {}
    for node, com in node2com.items():
        mm = member_min[node]
        if com not in com_min or mm < com_min[com]:
            com_min[com] = mm
    ordered = sorted(com_min, key=lambda c: com_min[c])
    relabel = {com: i for i, com in enumerate(ordered)}
    mapping = {node: relabel[com] for node, com in node2com.items()}
    com_member_min = {relabel[com]: com_min[com] for com in com_min}
    return mapping, com_member_min


def _edges(graph: dict):
    """无向唯一边遍历：每条 (u,v) 仅一次（u ≤ v），自环（u == v）一次。"""
    for u, nbrs in graph.items():
        for v, w in nbrs.items():
            if u <= v:
                yield u, v, w


def _induced_graph(mapping: dict, graph: dict) -> dict:
    """超节点聚合：以 `mapping`（节点 → 规范社区号）把当前层图压成「社区图」。

    社区间边权累加为跨社区边；社区内部边权累加进该社区的**自环**（携带 Σ_in 进下一层）。
    """
    new_graph: dict = {c: {} for c in set(mapping.values())}
    for u, v, w in _edges(graph):
        cu, cv = mapping[u], mapping[v]
        new_graph[cu][cv] = new_graph[cu].get(cv, 0.0) + w
        if cu != cv:
            new_graph[cv][cu] = new_graph[cv].get(cu, 0.0) + w
    return new_graph


def detect_communities(
    g: Graph, *, adj: dict[str, set[str]] | None = None
) -> dict[str, int]:
    """确定性 Louvain：node_id → 规范化社区号（0..k-1）。同图每次结果一致、`graph.json` 字节稳定。

    空图 → `{}`（0 社区）；单节点 / 无边图 → 每点各自单元素社区。孤立点（度 0，即 orphan）恒为
    单元素社区（不报 `isolated_community`，规模 < MIN_COMMUNITY_SIZE）。`adj` 可由调用方算好传入，
    与三特征函数共用同一份无向邻接、免重复构建（默认 None 即内部算）。
    """
    node_ids = [n.id for n in g.nodes]
    if not node_ids:
        return {}

    if adj is None:
        adj = undirected_adjacency(g)
    graph: dict = {n: {} for n in node_ids}
    for u in node_ids:
        for v in adj[u]:
            graph[u][v] = graph[u].get(v, 0.0) + 1.0

    m = _total_weight(graph)
    if m == 0:  # 无边：每点各自成社区，按 id 升序规范编号。
        return {n: i for i, n in enumerate(sorted(node_ids))}

    member_min = {n: n for n in node_ids}  # 层节点 → 最小原始成员；level 0 即恒等。
    status = _Status(graph)
    _one_level(graph, status, LOUVAIN_RESOLUTION, m)
    mod = _modularity(status, m, LOUVAIN_RESOLUTION)
    mapping, member_min = _renumber(status.node2com, member_min)
    result = {n: mapping[n] for n in node_ids}  # 原始节点 → 当前社区号。

    while True:  # 聚合超节点后重复，直到模块度不再提升（决策P3.5-2 步骤 4）。
        graph = _induced_graph(mapping, graph)
        status = _Status(graph)
        _one_level(graph, status, LOUVAIN_RESOLUTION, m)
        new_mod = _modularity(status, m, LOUVAIN_RESOLUTION)
        if new_mod - mod < _MOD_MIN:
            break
        mapping, member_min = _renumber(status.node2com, member_min)
        result = {n: mapping[result[n]] for n in node_ids}
        mod = new_mod
    return result


# ---------------------------------------------------------------------------
# 拓扑特征（建议 / html 提示的零 LLM 数据源）
# ---------------------------------------------------------------------------


def hub_nodes(
    g: Graph, comm: dict[str, int], *, adj: dict[str, set[str]] | None = None
) -> list[tuple[str, int]]:
    """过载枢纽：无向度 ≥ 均值 + HUB_SIGMA·σ **且** ≥ HUB_MIN_DEGREE 的节点。

    返回 `(node_id, 度)`，按 `(-度, id)` 稳定排序。`comm` 仅为与三特征函数签名统一（度判定不依赖社区）；
    `adj` 可由调用方复用（默认 None 即内部算）。
    """
    if adj is None:
        adj = undirected_adjacency(g)
    degrees = {nid: len(adj[nid]) for nid in adj}
    if not degrees:
        return []
    vals = list(degrees.values())
    n = len(vals)
    mean = sum(vals) / n
    std = (sum((d - mean) ** 2 for d in vals) / n) ** 0.5
    threshold = mean + HUB_SIGMA * std
    hubs = [
        (nid, deg)
        for nid, deg in degrees.items()
        if deg >= threshold and deg >= HUB_MIN_DEGREE
    ]
    hubs.sort(key=lambda x: (-x[1], x[0]))
    return hubs


def thin_intercommunity_links(
    g: Graph, comm: dict[str, int], *, adj: dict[str, set[str]] | None = None
) -> list[tuple[str, str]]:
    """一对社区间**仅由单条跨社区边**相连的那条边 (u, v)，u < v；按 (u, v) 稳定排序。

    **注意：这不是图论 bridge**——只看「该社区对间唯一直接互链」，**不**判断删边后是否真断连
    （A-B 单边但 A-C-B 仍通时并不脆弱）。语义是「跨社区引用过稀」而非「删之即断」，故命名
    `thin_intercommunity_link` 而非 `fragile_bridge`，避免误导（决策P3.5-13）。`adj` 可复用（默认内部算）。
    """
    if adj is None:
        adj = undirected_adjacency(g)
    pair_edges: dict[tuple[int, int], list[tuple[str, str]]] = defaultdict(list)
    for u in adj:
        for v in adj[u]:
            if u >= v:  # 每条无向边一次。
                continue
            cu, cv = comm[u], comm[v]
            if cu == cv:
                continue
            pair_edges[(min(cu, cv), max(cu, cv))].append((u, v))
    out = [edges[0] for edges in pair_edges.values() if len(edges) == 1]
    out.sort()
    return out


def isolated_communities(
    g: Graph, comm: dict[str, int], *, adj: dict[str, set[str]] | None = None
) -> list[tuple[int, list[str]]]:
    """孤岛社区：规模 ≥ MIN_COMMUNITY_SIZE 且与其余社区**零跨社区边**。返回 `(社区号, 成员 id sorted)`。

    **前置守卫（决策P3.5-12）**：仅当全库社区数 > 1 才可能成立——「与其余 wiki 零跨社区边」在整库
    只有一个社区（社区外无节点）时平凡成立，会把正常小库误判孤岛、与「小库不报噪声」冲突，故
    `len(set(comm.values())) <= 1` 直接返回 `[]`。按社区号升序稳定排序。`adj` 可复用（默认内部算）。
    """
    if len(set(comm.values())) <= 1:
        return []
    if adj is None:
        adj = undirected_adjacency(g)
    members: dict[int, list[str]] = defaultdict(list)
    for node, c in comm.items():
        members[c].append(node)
    out: list[tuple[int, list[str]]] = []
    for c, mem in members.items():
        if len(mem) < MIN_COMMUNITY_SIZE:
            continue
        has_cross = any(comm[v] != c for u in mem for v in adj[u])
        if not has_cross:
            out.append((c, sorted(mem)))
    out.sort()
    return out
