"""P4.34 Web 体检聚合（`guanlan.doctor.run_doctor`）测试：分组、去重、计数口径。零 LLM、不起服务。"""

from pathlib import Path

from conftest import write_page
from test_audit import drift_raw, source_with_digest

from guanlan.check import run_check
from guanlan.doctor import run_doctor
from guanlan.gate import partition_violations
from guanlan.graph import build_graph
from guanlan.pages import link_stem


def _cats(report: dict) -> dict:
    return {c["key"]: c for c in report["categories"]}


def _link_page(kb: Path, name: str, *targets: str, dirn: str = "concepts") -> None:
    body = "正文足够长，避免被判为桩页。" + " ".join(f"[[{t}]]" for t in targets)
    write_page(kb, f"wiki/{dirn}/{name}.md", body=body)


def test_clean_kb_has_no_blocking_and_counts_shown_items(kb) -> None:
    r = run_doctor(kb)
    assert r["ok"] is True
    cats = _cats(r)
    assert list(cats) == ["blocking", "missing_entity", "broken_link", "pending_raw", "drift", "index", "quality", "topology"]
    for c in r["categories"]:
        assert c["count"] == len(c["items"])  # 计数 = 最终展示项数
    assert r["total"] == sum(c["count"] for c in r["categories"] if c["section"] != "structure")


def test_blocking_reuses_gate_partition(kb) -> None:
    """必须修 = 写门禁的阻断部分：不止 frontmatter / sources，还有 aliases.* 等；断链不在这里。"""
    (kb / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (kb / "wiki" / "concepts" / "Bad.md").write_text("没有 frontmatter 的页 [[不存在]]\n", encoding="utf-8")
    write_page(kb, "wiki/concepts/A.md")
    write_page(kb, "wiki/concepts/B.md")
    for name in ("A", "B"):  # 两页声明同一个别名 → aliases.duplicate（阻断）
        p = kb / "wiki" / "concepts" / f"{name}.md"
        p.write_text(p.read_text(encoding="utf-8").replace("tags: []", "tags: []\naliases: [同名]"), encoding="utf-8")
    r = run_doctor(kb)
    blocking = _cats(r)["blocking"]["items"]
    expected, _ = partition_violations(run_check(kb / "wiki").violations)
    assert {(v["page"], v["kind"]) for v in blocking} == {(v.page, v.kind) for v in expected}
    kinds = {v["kind"] for v in blocking}
    assert "aliases.duplicate" in kinds and any(k.startswith("frontmatter.") for k in kinds)
    assert "wikilink.broken" not in kinds
    assert r["ok"] is False


def test_broken_links_deduplicated_and_aggregated_by_target(kb) -> None:
    """同一批断链只出现一次：被多页引用的进「高频缺页」，其余按目标聚合进「其余断链」。"""
    _link_page(kb, "P1", "常见概念", "孤零零")
    _link_page(kb, "P2", "常见概念", "常见概念")  # 同页重复引用只算一页
    _link_page(kb, "P3", "常见概念")
    cats = _cats(run_doctor(kb))
    missing = cats["missing_entity"]["items"]
    broken = cats["broken_link"]["items"]
    assert [m["target"] for m in missing] == ["常见概念"] and len(missing[0]["ref_pages"]) == 3
    assert [b["target"] for b in broken] == ["孤零零"] and broken[0]["ref_pages"] == ["wiki/concepts/P1.md"]
    assert cats["missing_entity"]["action"] == "heal" and cats["broken_link"]["action"] == "heal"  # 其余断链以 min_refs=1 进 heal


def test_broken_link_targets_match_check(kb) -> None:
    """钉住决策P3-6：check 的断链（页, 折叠后目标）集合 = 链接图的断链集合。两边分叉时体检会悄悄漏报。"""
    _link_page(kb, "P1", "APEC方式", "甲")
    _link_page(kb, "P2", "apec方式", "乙#节", "丙|别名")
    check_pairs = set()
    for v in run_check(kb / "wiki").violations:
        if v.kind == "wikilink.broken":
            raw = v.detail[v.detail.index("[[") + 2 : v.detail.index("]]")]
            check_pairs.add((v.page, link_stem(raw)))
    g = build_graph(kb / "wiki")
    node_path = {n.id: n.path for n in g.nodes}
    graph_pairs = {(node_path[e.source], e.target) for e in g.broken}
    assert check_pairs == graph_pairs and len(graph_pairs) == 5


def test_drift_counts_all_groups_beyond_preview_limit(kb) -> None:
    """审计预览默认只把前 10 组放进 groups：体检合并 postponed，全量计数。"""
    for i in range(12):
        source_with_digest(kb, f"src{i:02d}")
        drift_raw(kb, f"src{i:02d}.md")
    drift = _cats(run_doctor(kb))["drift"]
    assert drift["count"] == 12 and drift["action"] == "audit"


def test_pending_raw_lists_uningested_sources(kb) -> None:
    (kb / "raw" / "新资料.md").write_text("# 新\n", encoding="utf-8")
    source_with_digest(kb, "已入库")
    pending = _cats(run_doctor(kb))["pending_raw"]
    assert [p["page"] for p in pending["items"]] == ["raw/新资料.md"] and pending["action"] == "ingest"


def test_index_quality_topology_and_total_excludes_structure(kb) -> None:
    write_page(kb, "wiki/concepts/孤页.md", body="短")  # 未进 index、桩页、孤页
    r = run_doctor(kb)
    cats = _cats(r)
    assert {i["kind"] for i in cats["index"]["items"]} == {"health.index_missing_page"}
    quality = {i["kind"] for i in cats["quality"]["items"]}
    assert {"health.stub_page", "lint.orphan"} <= quality
    assert cats["topology"]["section"] == "structure"
    assert r["total"] == sum(c["count"] for c in r["categories"] if c["section"] != "structure")


def test_unknown_lint_kind_falls_into_quality_not_dropped(kb, monkeypatch) -> None:
    """lint 新增类型不在任何白名单里时也得计入（落「页面质量」），不能被分类过滤悄悄吞掉；
    断链 / 缺失实体则仍只出现在按目标聚合的那份里。"""
    import guanlan.doctor as doctor_mod
    from guanlan.pages import Finding

    real = doctor_mod.lint_from_graph

    def fake(g, **kw):
        rep = real(g, **kw)
        rep.findings.append(Finding("wiki/concepts/P1.md", "lint.future_kind", "新类型"))
        return rep

    monkeypatch.setattr(doctor_mod, "lint_from_graph", fake)
    _link_page(kb, "P1", "常见概念")
    _link_page(kb, "P2", "常见概念")
    quality = {i["kind"] for i in _cats(run_doctor(kb))["quality"]["items"]}
    assert "lint.future_kind" in quality
    assert not quality & {"lint.broken_link", "lint.missing_entity"}


def test_doctor_endpoint_and_old_reports_kept(kb) -> None:
    import pytest

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from guanlan.web.app import STATIC_DIR, create_app

    _link_page(kb, "P1", "孤零零")
    for reader in (False, True):  # 体检只读：reader 部署照常可用
        with TestClient(create_app(kb, reader=reader)) as client:
            r = client.get("/api/doctor")
            assert r.status_code == 200
            cats = {c["key"]: c for c in r.json()["categories"]}
            assert cats["broken_link"]["count"] == 1
            for name in ("check", "health", "lint"):  # 旧接口保留（CLI --json 字节对齐红线）
                assert client.get(f"/api/report/{name}").status_code == 200
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="doctor-btn"' in html and "data-report=" not in html  # 三个按钮已合并
    # reader 部署隐藏体检按钮（沿用决策P4.9-10）：字面 "doctor-btn"（带引号、无 #）只命中 applyReaderMode 的 hideIds。
    chat_js = (STATIC_DIR / "chat.js").read_text(encoding="utf-8")
    assert '"doctor-btn"' in chat_js
