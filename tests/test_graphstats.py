"""P3.5 确定性图拓扑测试（零 LLM，见 docs/P3.5-图谱分析.md §7）。

覆盖：确定性社区（连跑两次一致 + graph.json/html 字节一致）；社区正确性（两团单边 → 恰 2 社区 +
thin_intercommunity_link；有替代路径仍报，证明非图论 bridge）；枢纽阈值；孤岛（多节点簇报 / 单社区
不误判 / 单节点孤儿不报）；自环不计入度；正增益收敛；空图/单节点不崩。
"""

import json
from pathlib import Path

from guanlan.graph import build_graph, dump_json, render_html
from guanlan.graphstats import (
    MIN_COMMUNITY_SIZE,
    detect_communities,
    fragile_topology,
    hub_nodes,
    isolated_communities,
    thin_intercommunity_links,
    undirected_adjacency,
)
from guanlan.lint import run_lint

FM = "---\ntitle: '{title}'\ntype: {type}\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n{body}\n"


def _seed_config(wiki: Path) -> None:
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")


def _page(wiki: Path, stem: str, *, type="concept", links=(), folder="concepts") -> None:
    p = wiki / folder / f"{stem}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = " ".join(f"[[{t}]]" for t in links) or "正文"
    p.write_text(FM.format(title=stem, type=type, body=body), encoding="utf-8")


def _clique(wiki: Path, names: list[str], *, extra: dict[str, list[str]] | None = None) -> None:
    """互链团：每页链向团内其余页；`extra` 为指定页追加跨团边。"""
    for nm in names:
        links = [o for o in names if o != nm] + list((extra or {}).get(nm, []))
        _page(wiki, nm, links=links)


def _kinds(report) -> list[str]:
    return [f.kind for f in report.findings]


# ---------- 确定性 ----------


def test_communities_deterministic_and_byte_stable(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3", "A4"], extra={"A1": ["B1"]})
    _clique(wiki, ["B1", "B2", "B3", "B4"])

    g1 = build_graph(wiki)
    g2 = build_graph(wiki)
    assert detect_communities(g1) == detect_communities(g2)
    assert dump_json(g1) == dump_json(g2)  # graph.json 字节一致
    assert render_html(g1) == render_html(g2)  # graph.html 字节一致


# ---------- 社区正确性 ----------


def test_two_cliques_single_edge_two_communities_and_thin_link(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3", "A4"], extra={"A1": ["B1"]})  # 单跨边 A1—B1
    _clique(wiki, ["B1", "B2", "B3", "B4"])

    g = build_graph(wiki)
    comm = detect_communities(g)
    assert len(set(comm.values())) == 2  # 恰两社区
    # 两团各自同社区
    assert comm["a1"] == comm["a2"] == comm["a3"] == comm["a4"]
    assert comm["b1"] == comm["b2"] == comm["b3"] == comm["b4"]
    assert comm["a1"] != comm["b1"]

    thin = thin_intercommunity_links(g, comm)
    assert thin == [("a1", "b1")]  # 那条单边被报

    kinds = _kinds(run_lint(wiki))
    assert "lint.thin_intercommunity_link" in kinds


def test_thin_link_reported_even_with_alternative_path(tmp_path: Path):
    """三团两两单边相连：A↔B 删边后仍可经 C 连通（非图论 bridge），但仍报 thin link（决策P3.5-13）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3", "A4"], extra={"A1": ["B1"], "A2": ["C1"]})
    _clique(wiki, ["B1", "B2", "B3", "B4"], extra={"B2": ["C2"]})
    _clique(wiki, ["C1", "C2", "C3", "C4"])

    g = build_graph(wiki)
    comm = detect_communities(g)
    assert len(set(comm.values())) == 3
    thin = thin_intercommunity_links(g, comm)
    # 三对社区各一条单跨边 → 三条 thin link（A-B 有 A-C-B 替代路径仍在内）。
    assert len(thin) == 3
    assert ("a1", "b1") in thin


# ---------- 枢纽 ----------


def test_hub_node_detected_on_star(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    leaves = [f"L{i}" for i in range(8)]
    _page(wiki, "Center", links=leaves)  # 中心度 8，叶子度 1
    for leaf in leaves:
        _page(wiki, leaf, links=["Center"])

    g = build_graph(wiki)
    comm = detect_communities(g)
    hubs = dict(hub_nodes(g, comm))
    assert "center" in hubs and hubs["center"] == 8
    assert all(leaf.lower() not in hubs for leaf in leaves)  # 普通叶子不报

    assert "lint.hub_node" in _kinds(run_lint(wiki))


def test_no_hub_below_min_degree(tmp_path: Path):
    """小度数图：即便某点相对突出也不过 HUB_MIN_DEGREE 地板 → 不报枢纽。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3"])  # 度均为 2，远低于地板 5

    g = build_graph(wiki)
    assert hub_nodes(g, detect_communities(g)) == []
    assert "lint.hub_node" not in _kinds(run_lint(wiki))


# ---------- 孤岛 ----------


def test_isolated_communities_two_clusters(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3"])  # 无跨边
    _clique(wiki, ["B1", "B2", "B3"])

    g = build_graph(wiki)
    comm = detect_communities(g)
    silos = isolated_communities(g, comm)
    assert len(silos) == 2  # 两个多节点簇各报
    for _c, members in silos:
        assert len(members) >= MIN_COMMUNITY_SIZE
    assert _kinds(run_lint(wiki)).count("lint.isolated_community") == 2


def test_single_community_not_flagged_as_silo(tmp_path: Path):
    """整库连成一个社区（规模 ≥2、社区外无节点）→ 零 isolated_community（决策P3.5-12）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3", "A4"])

    g = build_graph(wiki)
    comm = detect_communities(g)
    assert len(set(comm.values())) == 1
    assert isolated_communities(g, comm) == []
    assert "lint.isolated_community" not in _kinds(run_lint(wiki))


def test_single_node_orphan_not_isolated_community(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3"])
    _page(wiki, "Lonely")  # 单节点孤儿

    g = build_graph(wiki)
    comm = detect_communities(g)
    silos = isolated_communities(g, comm)
    # Lonely 自成单元素社区，规模 < 阈值 → 不在孤岛里。
    assert all("lonely" not in members for _c, members in silos)
    assert "lint.orphan" in _kinds(run_lint(wiki))


# ---------- 自环 / 无向口径 ----------


def test_self_loop_excluded_from_degree(tmp_path: Path):
    """自链页（[[自身]]）的自环不进无向邻接、不计入度（决策P3.5-11）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "A", links=["A", "B"])  # 自链 + 链 B
    _page(wiki, "B", links=["A"])

    g = build_graph(wiki)
    adj = undirected_adjacency(g)
    assert adj["a"] == {"b"}  # 自环被过滤，不含 "a"
    assert "a" not in adj["a"]


def test_broken_edge_not_in_adjacency(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "A", links=["B", "Ghost"])  # Ghost 无对应页 → 断链
    _page(wiki, "B", links=["A"])

    g = build_graph(wiki)
    adj = undirected_adjacency(g)
    assert adj["a"] == {"b"}  # 断链 Ghost 不参与连通


# ---------- 空图 / 单节点 ----------


def test_empty_and_single_node(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)  # 仅 config，无内容页
    g = build_graph(wiki)
    assert detect_communities(g) == {}  # 空图 0 社区
    assert isolated_communities(g, {}) == []

    _page(wiki, "Solo")
    g2 = build_graph(wiki)
    comm2 = detect_communities(g2)
    assert comm2 == {"solo": 0}  # 单节点 1 社区
    assert isolated_communities(g2, comm2) == []  # 单社区 → 不报孤岛


def test_disconnected_singletons_get_distinct_communities(tmp_path: Path):
    """无边图：每点各自单元素社区，按 id 升序规范编号。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "A")
    _page(wiki, "B")
    _page(wiki, "C")

    g = build_graph(wiki)
    comm = detect_communities(g)
    assert comm == {"a": 0, "b": 1, "c": 2}


# ---------- P3.6 图论割边 / 割点 ----------


def _findings_by_kind(report, kind: str):
    return [f for f in report.findings if f.kind == kind]


def test_two_cliques_single_edge_is_bridge_and_cut_vertices(tmp_path: Path):
    """两团由单边相连：那条边是割边、两端点是割点（"命脉"场景）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3", "A4"], extra={"A1": ["B1"]})  # 单跨边 A1—B1
    _clique(wiki, ["B1", "B2", "B3", "B4"])

    g = build_graph(wiki)
    frag = fragile_topology(g)
    assert frag.bridges == [("a1", "b1")]  # 删之 → 两块各 4，较小侧 4 ≥ 2
    assert frag.cut_vertices == ["a1", "b1"]  # 删任一端点，对面整团脱钩

    report = run_lint(wiki)
    # 割边是全局 finding（page=""）；割点记在该页（page=路径）。
    bridge_f = _findings_by_kind(report, "lint.bridge_edge")
    assert len(bridge_f) == 1 and bridge_f[0].page == ""
    cut_f = _findings_by_kind(report, "lint.cut_vertex")
    assert {f.page for f in cut_f} == {
        "wiki/concepts/A1.md",
        "wiki/concepts/B1.md",
    }


def test_orthogonal_to_thin_link_not_deduped(tmp_path: Path):
    """唯一跨社区且为割边的那条边：thin_intercommunity_link 与 bridge_edge **两条都报**（决策P3.6-2）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3", "A4"], extra={"A1": ["B1"]})
    _clique(wiki, ["B1", "B2", "B3", "B4"])

    kinds = _kinds(run_lint(wiki))
    assert "lint.thin_intercommunity_link" in kinds  # 社区视角
    assert "lint.bridge_edge" in kinds  # 连通性视角，两者不合并


def test_cycle_has_no_bridge_no_cut_vertex(tmp_path: Path):
    """环 A–B–C–D–A：恒有旁路 → 零割边零割点（证明是真图论判断，与 thin link 对照）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "A", links=["B"])
    _page(wiki, "B", links=["C"])
    _page(wiki, "C", links=["D"])
    _page(wiki, "D", links=["A"])

    frag = fragile_topology(build_graph(wiki))
    assert frag.bridges == []
    assert frag.cut_vertices == []
    kinds = _kinds(run_lint(wiki))
    assert "lint.bridge_edge" not in kinds and "lint.cut_vertex" not in kinds


def test_path_only_middle_edge_is_bridge(tmp_path: Path):
    """链 A–B–C–D：仅中间边 B–C 报割边（两侧各 2）；切单叶的端边不报；无割点。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "A", links=["B"])
    _page(wiki, "B", links=["C"])
    _page(wiki, "C", links=["D"])
    _page(wiki, "D")  # 须真实建页，否则 C→D 是断链、链退化成 A–B–C

    frag = fragile_topology(build_graph(wiki))
    assert frag.bridges == [("b", "c")]  # A–B / C–D 切单叶被压
    assert frag.cut_vertices == []  # B/C 删后第二大块 = 1，被压


def test_pendant_leaf_edge_not_a_bridge(tmp_path: Path):
    """团 + 一张单边挂接的叶子页：挂接边切单叶（较小侧 1 < 2）→ 不报割边/割点（噪声过滤）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3", "A4"])
    _page(wiki, "Leaf", links=["A1"])  # 单边挂接

    frag = fragile_topology(build_graph(wiki))
    assert frag.bridges == []
    assert frag.cut_vertices == []
    assert "lint.orphan" in _kinds(run_lint(wiki))  # Leaf 走 orphan（无入链）


def test_star_center_is_hub_not_cut_vertex(tmp_path: Path):
    """星形中心是 hub_node（P3.5）但**非** cut_vertex（删后全单叶，第二大块 = 1）；辐条非割边。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    leaves = [f"L{i}" for i in range(8)]
    _page(wiki, "Center", links=leaves)
    for leaf in leaves:
        _page(wiki, leaf, links=["Center"])

    frag = fragile_topology(build_graph(wiki))
    assert frag.bridges == []
    assert frag.cut_vertices == []
    kinds = _kinds(run_lint(wiki))
    assert "lint.hub_node" in kinds  # 仍是枢纽
    assert "lint.cut_vertex" not in kinds


def test_graph_json_stats_has_fragile_counts(tmp_path: Path):
    """graph.json stats additive 多 bridges/cut_vertices 计数；连跑两次字节稳定。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _clique(wiki, ["A1", "A2", "A3", "A4"], extra={"A1": ["B1"]})
    _clique(wiki, ["B1", "B2", "B3", "B4"])

    g1, g2 = build_graph(wiki), build_graph(wiki)
    assert dump_json(g1) == dump_json(g2)  # 字节稳定
    assert render_html(g1) == render_html(g2)
    stats = json.loads(dump_json(g1))["stats"]
    assert stats["bridges"] == 1
    assert stats["cut_vertices"] == 2


def test_empty_and_single_node_fragile_no_crash(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    assert fragile_topology(build_graph(wiki)) == ([], [])  # 空图

    _page(wiki, "Solo")
    frag = fragile_topology(build_graph(wiki))
    assert frag.bridges == [] and frag.cut_vertices == []


def test_long_chain_does_not_blow_stack(tmp_path: Path):
    """超递归上限的长链路：迭代式 Tarjan 正常返回、不爆栈（决策P3.6-4）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    n = 1200  # 远超 Python 默认递归上限（~1000）
    for i in range(n - 1):
        _page(wiki, f"N{i:05d}", links=[f"N{i + 1:05d}"])
    _page(wiki, f"N{n - 1:05d}")

    frag = fragile_topology(build_graph(wiki))  # 不抛 RecursionError
    # 长链中段边均为割边（两侧 ≥2），数量级应接近 n（端边被压）。
    assert len(frag.bridges) > n - 5
