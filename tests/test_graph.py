"""P3 确定性 graph 测试（零 LLM，见 docs/P3-健康与图谱.md §11）。

覆盖：坏 frontmatter 容错建节点；边三分类（非 config→边 / config→丢弃 / 未命中→broken）；
`graph.broken` 与 `check.wikilink.broken` 在同一 fixture 上对齐（决策P3-6）；自环去重；
孤儿统计；graph.json 排序稳定 + 幂等；graph.html 自包含；--json-only 不写 html；写 graph/ 失败退 1。
"""

import json
from pathlib import Path

from guanlan.check import run_check
from guanlan.graph import (
    build_graph,
    compute_backlinks,
    compute_orphans,
    dump_json,
    graph_entrypoint,
    render_html,
)

FM = "---\ntitle: '{title}'\ntype: {type}\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n{body}\n"


def _page(wiki: Path, rel: str, *, title="T", type="concept", body="正文") -> None:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(title=title, type=type, body=body), encoding="utf-8")


def _seed_config(wiki: Path) -> None:
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")


def _kb(tmp_path: Path) -> Path:
    """最小知识库根（满足 require_kb_root writable=False：仅需 wiki/）。"""
    _seed_config(tmp_path / "wiki")
    return tmp_path


# ---------- 建图基础 ----------


def test_nodes_exclude_config_and_edges_resolve(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="entity", body="见 [[Bar]]")
    _page(wiki, "concepts/Bar.md", type="concept", body="无链接")

    g = build_graph(wiki)
    assert {n.id for n in g.nodes} == {"foo", "bar"}  # config 不建节点
    assert any(e.source == "foo" and e.target == "bar" and e.resolved for e in g.edges)
    assert g.adjacency["foo"] == {"bar"}


def test_link_to_config_is_discarded_not_broken(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Bar.md", body="参见 [[overview]] 和 [[index]]")

    g = build_graph(wiki)
    # 指向 config 页 → 既不建边也不算断链（决策P3-6）。
    assert g.broken == []
    assert g.adjacency["bar"] == set()


def test_broken_edge_for_unresolved_target(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Bar.md", body="链到不存在的 [[Nope]]")

    g = build_graph(wiki)
    assert [(e.source, e.target) for e in g.broken] == [("bar", "nope")]


def test_graph_broken_matches_check_broken(tmp_path: Path):
    """决策P3-6：graph.broken 与 check.wikilink.broken 在同一 fixture 上逐一对齐。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="entity", body="见 [[Bar]]、[[overview]]、[[Ghost]]")
    _page(wiki, "concepts/Bar.md", body="回链 [[Foo]] 与不存在的 [[Phantom]]")

    g = build_graph(wiki)
    graph_broken = sorted((e.source, e.target) for e in g.broken)

    check = run_check(wiki)
    # check 的断链按 (page, 目标 stem) —— 取 Violation 还原成 (source_id, target_stem) 比对。
    from guanlan.pages import link_stem

    check_broken = sorted(
        (Path(v.page).stem.lower(), link_stem(v.detail.split("[[", 1)[1].split("]]", 1)[0]))
        for v in check.violations
        if v.kind == "wikilink.broken"
    )
    assert graph_broken == check_broken
    assert graph_broken == [("bar", "phantom"), ("foo", "ghost")]


def test_self_loop_kept_and_deduped(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="entity", body="自指 [[Foo]] 再 [[Foo]] 又 [[Foo]]")

    g = build_graph(wiki)
    foo_edges = [e for e in g.edges if e.source == "foo"]
    assert foo_edges == [e for e in foo_edges if e.target == "foo"]  # 仅自环
    assert len(foo_edges) == 1  # 去重 (source,target)


def test_self_loop_does_not_save_from_orphan(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="entity", body="只自指 [[Foo]]")

    g = build_graph(wiki)
    assert {n.id for n in compute_orphans(g)} == {"foo"}  # 自环不算入链


def test_orphan_stats(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="entity", body="见 [[Bar]]")
    _page(wiki, "concepts/Bar.md", body="无出链")
    _page(wiki, "concepts/Lonely.md", body="无人链入")

    g = build_graph(wiki)
    orphans = {n.id for n in compute_orphans(g)}
    assert orphans == {"foo", "lonely"}  # bar 被 foo 链入，非孤儿


# ---------- 容错（决策P3-8） ----------


def test_bad_frontmatter_still_builds_node(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # 缺 frontmatter 块的页：照常建节点，title=stem、type=unknown，不抛。
    p = wiki / "entities" / "Broken.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# 没有 frontmatter\n正文 [[overview]]\n", encoding="utf-8")

    g = build_graph(wiki)  # 不抛
    node = next(n for n in g.nodes if n.id == "broken")
    assert node.title == "Broken"
    assert node.type == "unknown"


def test_unparsable_frontmatter_falls_back(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    p = wiki / "concepts" / "Weird.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\ntitle: 'X\ntype: [unclosed\n---\n\n正文\n", encoding="utf-8")

    g = build_graph(wiki)  # 不抛
    node = next(n for n in g.nodes if n.id == "weird")
    assert node.title == "Weird" and node.type == "unknown"


# ---------- graph.json 稳定 + 幂等 ----------


def test_json_sorted_and_idempotent(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Zeta.md", type="entity", body="见 [[Alpha]] 与 [[Nope]]")
    _page(wiki, "concepts/Alpha.md", body="回链 [[Zeta]]")

    g1 = build_graph(wiki)
    g2 = build_graph(wiki)
    assert dump_json(g1) == dump_json(g2)  # 字节级一致

    data = json.loads(dump_json(g1))
    assert [n["id"] for n in data["nodes"]] == ["alpha", "zeta"]  # 节点按 id 排序
    assert data["edges"] == sorted(data["edges"], key=lambda e: (e["source"], e["target"]))
    # P3.5 additive：stats 多 communities（Alpha↔Zeta 互链 → 1 社区）、每节点多 community。
    # P3.6 additive：stats 再多 bridges/cut_vertices（两节点单边 → 较小侧 1 < 阈值 → 0/0）。
    assert data["stats"] == {
        "nodes": 2, "edges": 2, "broken": 1, "orphans": 0,
        "communities": 1, "bridges": 0, "cut_vertices": 0,
    }
    assert all("community" in n for n in data["nodes"])


# ---------- graph.html 自包含 ----------


def test_html_self_contained_and_stable(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="entity", body="见 [[Bar]] 与 [[Ghost]]")
    _page(wiki, "concepts/Bar.md", body="无出链")

    g = build_graph(wiki)
    html_text = render_html(g)
    assert "http://" not in html_text and "https://" not in html_text  # 零外链
    assert 'id="graph-data"' in html_text  # 内联数据
    assert "[[ghost]]" in html_text  # 断链目标（归一为 stem）文字标注
    assert render_html(g) == html_text  # 邻接列表字节稳定


def test_html_escapes_special_chars(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="entity", title="A <b> & </script>", body="正文")

    html_text = render_html(build_graph(wiki))
    assert "<b>" not in html_text  # 渲染列表转义 + JSON blob 把 < 转 <，无裸 <b>
    assert "&lt;b&gt;" in html_text  # 列表里标题被 html.escape


# ---------- entrypoint / 落盘 ----------


def test_entrypoint_writes_json_and_html(tmp_path: Path):
    kb = _kb(tmp_path)
    _page(kb / "wiki", "entities/Foo.md", type="entity", body="见 [[Bar]]")
    _page(kb / "wiki", "concepts/Bar.md", body="x")

    rc = graph_entrypoint(kb, json_only=False)
    assert rc == 0
    assert (kb / "graph" / "graph.json").is_file()
    assert (kb / "graph" / "graph.html").is_file()
    # graph 绝不碰 wiki/raw
    data = json.loads((kb / "graph" / "graph.json").read_text(encoding="utf-8"))
    assert data["stats"]["nodes"] == 2


def test_entrypoint_json_only_skips_html(tmp_path: Path):
    kb = _kb(tmp_path)
    _page(kb / "wiki", "entities/Foo.md", type="entity")

    rc = graph_entrypoint(kb, json_only=True)
    assert rc == 0
    assert (kb / "graph" / "graph.json").is_file()
    assert not (kb / "graph" / "graph.html").exists()


def test_entrypoint_non_kb_root_fails(tmp_path: Path):
    # 没有 wiki/ → require_kb_root 失败 → EXIT_USAGE(1)
    assert graph_entrypoint(tmp_path, json_only=True) == 1


def test_entrypoint_write_failure_returns_usage(tmp_path: Path, monkeypatch):
    kb = _kb(tmp_path)
    _page(kb / "wiki", "entities/Foo.md", type="entity")

    # 让写 graph/ 抛 OSError（模拟权限/IO 失败）→ EXIT_USAGE(1)，不污染 wiki。
    import guanlan.graph as graph_mod

    orig_write = Path.write_text

    def boom(self, *a, **k):
        if self.parent.name == "graph":
            raise OSError("disk full")
        return orig_write(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", boom)
    assert graph_mod.graph_entrypoint(kb, json_only=True) == 1


def test_entrypoint_write_failure_labels_write_not_read(tmp_path: Path, monkeypatch, capsys):
    """写派生 graph/ 失败 → 仍标"写...失败"（OSError 来自写阶段，归因正确）。"""
    kb = _kb(tmp_path)
    _page(kb / "wiki", "entities/Foo.md", type="entity")
    import guanlan.graph as graph_mod

    def boom(_g, _root, *, json_only):
        raise OSError("disk full")

    monkeypatch.setattr(graph_mod, "write_graph", boom)
    assert graph_mod.graph_entrypoint(kb, json_only=True) == 1
    assert "写" in capsys.readouterr().err  # 写阶段错才贴"写...失败"标签


def test_entrypoint_read_failure_not_mislabeled_as_write(tmp_path: Path, monkeypatch):
    """读 wiki/ 页时的 OSError **不**被当成"写 graph 失败"吞掉，而是外抛（不误导用户去查 graph/）。"""
    import pytest

    kb = _kb(tmp_path)
    _page(kb / "wiki", "entities/Foo.md", type="entity")
    import guanlan.graph as graph_mod

    def boom(_wiki):
        raise OSError("Permission denied: wiki/Foo.md")

    monkeypatch.setattr(graph_mod, "build_graph", boom)
    with pytest.raises(OSError, match="Permission denied"):
        graph_mod.graph_entrypoint(kb, json_only=True)  # 读错外抛，不被 except OSError 误标为写失败


# ---------- P5.3 反链计数（compute_backlinks）----------


def test_compute_backlinks_inlink_degree(tmp_path: Path):
    """入链数 = resolved 边入度，排自环、排 broken；键用 node.path（对齐 DocBag.page，决策P5.3-3）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # Hub 被 A、B 指向（入链 2）；A 自指（自环不计）；A 指向断链目标（broken 不计）。
    _page(wiki, "entities/Hub.md", type="entity", body="枢纽页正文")
    _page(wiki, "entities/A.md", type="entity", body="见 [[Hub]] 和 [[A]] 与 [[不存在的页]]")
    _page(wiki, "concepts/B.md", body="也见 [[Hub]]")
    g = build_graph(wiki)
    bl = compute_backlinks(g)
    assert bl["wiki/entities/Hub.md"] == 2  # A、B 各一条，去重后 2
    assert bl["wiki/entities/A.md"] == 0  # 自环不算入链
    assert bl["wiki/concepts/B.md"] == 0
    # 键为相对库根 posix，与 graph node.path 集合一致（对齐 DocBag.page）。
    assert set(bl) == {n.path for n in g.nodes}


def test_compute_backlinks_consistent_with_orphans(tmp_path: Path):
    """与 `compute_orphans` 入链集互证：入度==0 ⟺ 是孤儿（同一入链口径，决策P5.3-3）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Hub.md", type="entity", body="枢纽")
    _page(wiki, "entities/A.md", type="entity", body="见 [[Hub]]")
    _page(wiki, "concepts/Lonely.md", body="无人链入，也不外链")
    g = build_graph(wiki)
    bl = compute_backlinks(g)
    orphan_paths = {n.path for n in compute_orphans(g)}
    zero_inlink = {path for path, c in bl.items() if c == 0}
    assert zero_inlink == orphan_paths  # 入度0 集 ≡ 孤儿集


def test_compute_backlinks_alias_link_counts_to_owner(tmp_path: Path):
    """别名链计入拥有页（复用 build_graph 解析期归一，alias-aware，决策P5.3-3）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # LLM 页声明别名「大模型」；另页用 [[大模型]] 链接 → 计入 LLM 页入链。
    (wiki / "entities").mkdir(parents=True, exist_ok=True)
    (wiki / "entities/LLM.md").write_text(
        "---\ntitle: 大语言模型\ntype: entity\naliases: ['大模型']\n---\n\n一种模型。\n",
        encoding="utf-8",
    )
    _page(wiki, "concepts/Use.md", body="它基于 [[大模型]] 构建")
    g = build_graph(wiki)
    bl = compute_backlinks(g)
    assert bl["wiki/entities/LLM.md"] == 1  # 别名链归到拥有页
