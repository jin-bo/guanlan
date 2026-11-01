"""P4.27 Web 图可视化测试（见 docs/P4.27-Web图可视化.md §5）。零 LLM、进程内 TestClient、临时知识库。"""

import os
from pathlib import Path

import pytest

from conftest import write_page

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from guanlan.web import graphview  # noqa: E402
from guanlan.web.app import create_app  # noqa: E402


def _page(kb: Path, stem: str, links=(), *, folder="concepts", type="concept", aliases=None) -> None:
    body = " ".join(f"[[{t}]]" for t in links) or "正文"
    write_page(kb, f"wiki/{folder}/{stem}.md", type=type, body=body)
    if aliases:
        p = kb / f"wiki/{folder}/{stem}.md"
        text = p.read_text(encoding="utf-8").replace("tags: []", f"tags: []\naliases: {aliases}", 1)
        p.write_text(text, encoding="utf-8")


def _two_clusters(kb: Path) -> None:
    """两个四页团，由 a1→b1 一条边相连；外加一张孤立页 lone。"""
    a = ["a1", "a2", "a3", "a4"]
    b = ["b1", "b2", "b3", "b4"]
    for grp in (a, b):
        for s in grp:
            _page(kb, s, [t for t in grp if t != s])
    _page(kb, "a1", ["a2", "a3", "a4", "b1"])
    _page(kb, "lone", [], folder="sources", type="source")


@pytest.fixture
def client(kb):
    with TestClient(create_app(kb)) as c:
        yield c


def _get(client, **params):
    r = client.get("/api/graph", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── 总览 ─────────────────────────


def test_overview_communities_anchor_and_weight(kb, client):
    _two_clusters(kb)
    d = _get(client)
    assert d["view"] == "overview"
    assert d["pages"] == 9 and d["isolated"] == 1  # lone 只计数、不出节点
    # 锚页 = 社区内邻居数最多者：a1 连 4 页（三个同伴 + b1），b1 连 4 页（三个同伴 + a1）
    assert [n["id"] for n in d["nodes"]] == ["a1", "b1"]
    assert all(n["size"] == 4 for n in d["nodes"])
    assert d["edges"] == [{"source": "a1", "target": "b1", "weight": 1}]  # 社区间无向页对数
    assert d["truncated"] is None
    assert "1" not in {str(n["id"]) for n in d["nodes"]}  # 不暴露社区号


def test_anchor_tie_breaks_by_id(kb, client):
    for s in ["y", "x", "z"]:  # 三角形：三页邻居数相同
        _page(kb, s, [t for t in ["x", "y", "z"] if t != s])
    d = _get(client)
    assert [n["id"] for n in d["nodes"]] == ["x"]


def test_overview_truncates_by_size(kb, client, monkeypatch):
    _two_clusters(kb)
    for s in ["c1", "c2"]:  # 第三个社区只有两页
        _page(kb, s, ["c2" if s == "c1" else "c1"])
    monkeypatch.setattr(graphview, "GRAPH_VIEW_MAX_NODES", 2)
    d = _get(client)
    assert {n["id"] for n in d["nodes"]} == {"a1", "b1"}  # 按页数保留，小社区先丢
    assert d["truncated"] == {"total": 3, "shown": 2}


# ───────────────────────── 社区视图 ─────────────────────────


def test_community_view_by_anchor(kb, client):
    _two_clusters(kb)
    d = _get(client, view="community", anchor="a1")
    assert d["community"] == {"anchor": "a1", "anchor_title": "T", "size": 4}
    assert [n["id"] for n in d["nodes"]] == ["a1", "a2", "a3", "a4"]
    a1 = d["nodes"][0]
    assert a1["path"] == "wiki/concepts/a1.md" and a1["degree"] == 4 and a1["external"] == 1
    assert len(d["edges"]) == 6  # 四页团内六对，b1 不在本视图
    assert all(e["mutual"] for e in d["edges"])


def test_community_view_any_member_page_is_an_address(kb, client):
    """锚页只是推荐地址：任一成员页都能定位到它当前所在的社区。"""
    _two_clusters(kb)
    assert _get(client, view="community", anchor="a3")["community"]["anchor"] == "a1"


def test_community_anchor_survives_renumbering(kb, client):
    """wiki 改动使社区重编号后，同一锚页仍落到它所在的社区（决策P4.27-6）。"""
    _two_clusters(kb)
    before = _get(client, view="community", anchor="b1")
    for s in ["0a", "0b", "0c"]:  # 新增一个 id 排在最前的社区，挤动编号
        _page(kb, s, [t for t in ["0a", "0b", "0c"] if t != s])
    after = _get(client, view="community", anchor="b1")
    assert [n["id"] for n in after["nodes"]] == [n["id"] for n in before["nodes"]] == ["b1", "b2", "b3", "b4"]


def test_community_truncates_by_degree(kb, client, monkeypatch):
    _two_clusters(kb)
    monkeypatch.setattr(graphview, "GRAPH_VIEW_MAX_NODES", 2)
    d = _get(client, view="community", anchor="a1")
    assert [n["id"] for n in d["nodes"]] == ["a1", "a2"]  # a1 度 4 先保留，其余度 3 按 id
    assert d["edges"] == [{"source": "a1", "target": "a2", "mutual": True}]  # 只留两端都在的边
    assert d["truncated"] == {"total": 4, "shown": 2}


# ───────────────────────── 单页邻域 ─────────────────────────


def test_page_view_one_hop(kb, client):
    _two_clusters(kb)
    d = _get(client, view="page", name="b1")
    assert d["center"] == "b1"
    assert [n["id"] for n in d["nodes"]] == ["a1", "b1", "b2", "b3", "b4"]
    assert d["community"]["anchor"] == "b1"
    ab = [e for e in d["edges"] if {e["source"], e["target"]} == {"a1", "b1"}]
    assert ab == [{"source": "a1", "target": "b1", "mutual": False}]  # 只有 a1→b1 单向


def test_page_view_keeps_center_when_truncated(kb, client, monkeypatch):
    _two_clusters(kb)
    monkeypatch.setattr(graphview, "GRAPH_VIEW_MAX_NODES", 2)
    d = _get(client, view="page", name="b4")  # b4 度 3，低于邻居 b1（度 4）
    assert [n["id"] for n in d["nodes"]] == ["b1", "b4"]
    assert d["truncated"] == {"total": 4, "shown": 2}


def test_page_view_isolated_page_has_no_community(kb, client):
    _two_clusters(kb)
    d = _get(client, view="page", name="lone")
    assert d["nodes"][0]["id"] == "lone" and d["edges"] == [] and d["community"] is None


def test_name_resolves_alias_and_fold_variant(kb, client):
    _page(kb, "Foo_Bar", ["baz"], aliases="[别名甲]")
    _page(kb, "baz", ["Foo_Bar"])
    assert _get(client, view="page", name="别名甲")["center"] == "foo_bar"
    assert _get(client, view="page", name="Foo-Bar")["center"] == "foo_bar"  # P3.8 fold 变体


# ───────────────────────── 边口径 / 确定性 ─────────────────────────


def test_edges_exclude_broken_config_and_self(kb, client):
    _page(kb, "p", ["q", "p", "不存在", "index", "overview"])
    _page(kb, "q", ["p"])
    d = _get(client, view="page", name="p")
    assert [n["id"] for n in d["nodes"]] == ["p", "q"]
    assert d["edges"] == [{"source": "p", "target": "q", "mutual": True}]


def test_byte_stable(kb, client):
    _two_clusters(kb)
    for params in ({}, {"view": "community", "anchor": "a2"}, {"view": "page", "name": "a1"}):
        r1 = client.get("/api/graph", params=params)
        client.app.state.graph_cache._key = None  # 强制重建，比的是两次独立计算
        r2 = client.get("/api/graph", params=params)
        assert r1.content == r2.content


# ───────────────────────── 错误码 ─────────────────────────


def test_errors(kb, client):
    _two_clusters(kb)
    assert client.get("/api/graph", params={"view": "page", "name": "没有这页"}).status_code == 404
    assert client.get("/api/graph", params={"view": "community", "anchor": "没有这页"}).status_code == 404
    assert client.get("/api/graph", params={"view": "page", "name": "index"}).status_code == 404  # config 页不是节点
    assert client.get("/api/graph", params={"view": "all"}).status_code == 422
    assert client.get("/api/graph", params={"view": "community"}).status_code == 422
    assert client.get("/api/graph", params={"view": "page"}).status_code == 422


def test_empty_wiki(kb, client):
    d = _get(client)
    assert d["nodes"] == [] and d["edges"] == [] and d["pages"] == 0 and d["truncated"] is None


# ───────────────────────── memo / reader / 零写 ─────────────────────────


def test_memo_reuses_until_signature_changes(kb, client):
    _two_clusters(kb)
    cache = client.app.state.graph_cache
    _get(client)
    _get(client, view="community", anchor="a1")
    assert cache.builds == 1
    p = kb / "wiki/concepts/a2.md"
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    _get(client)
    assert cache.builds == 2


def _snapshot_tree(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_reader_mode_registered_and_zero_write(kb):
    _two_clusters(kb)
    before = _snapshot_tree(kb)
    with TestClient(create_app(kb, reader=True)) as c:
        assert c.get("/api/graph").status_code == 200
        assert c.get("/api/graph", params={"view": "community", "anchor": "a1"}).status_code == 200
        assert c.get("/api/graph", params={"view": "page", "name": "a1"}).status_code == 200
        assert c.get("/graph").status_code == 404  # 写 graph/ 的旧路由 reader 下仍不注册
    assert _snapshot_tree(kb) == before
    assert not (kb / "graph").exists()
