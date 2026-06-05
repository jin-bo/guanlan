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
    assert data["stats"] == {"nodes": 2, "edges": 2, "broken": 1, "orphans": 0}


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
