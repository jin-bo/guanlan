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
    assert list(cats) == [
        "blocking", "missing_entity", "broken_link", "pending_raw", "drift", "index", "quality", "flash_pending", "topology",
    ]
    for c in r["categories"]:
        assert c["count"] == len(c["items"])  # 计数 = 最终展示项数
    assert r["total"] == sum(c["count"] for c in r["categories"] if c["section"] in ("blocking", "suggest"))


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


def _mark_flash(page: Path) -> None:
    page.write_text(page.read_text(encoding="utf-8").replace("raw_digest:", "ingest_depth: flash\nraw_digest:", 1), encoding="utf-8")


def test_flash_pending_listed_with_raw_and_not_counted(kb) -> None:
    """快速收录页（P5.6 D8）是状态不是问题：单列「待加深」并附 raw 供逐篇加深，不计入 total、不影响 ok。"""
    _mark_flash(source_with_digest(kb, "快收"))
    source_with_digest(kb, "完整")
    before = run_doctor(kb)
    cat = _cats(before)["flash_pending"]
    assert cat["section"] == "status" and cat["action"] is None
    assert cat["items"] == [{"page": "wiki/sources/快收.md", "raw": "raw/快收.md"}]
    assert before["ok"] is True
    assert before["total"] == sum(c["count"] for c in before["categories"] if c["section"] in ("blocking", "suggest"))
    assert "flash_pending" not in {c["key"] for c in before["categories"] if c["section"] in ("blocking", "suggest")}


def test_flash_pending_without_usable_digest_lists_page_only(kb) -> None:
    """digest 越界或坏掉时只列页面、不给 raw（前端随之不给加深按钮），不能因此漏报这一页。"""
    _mark_flash(source_with_digest(kb, "越界", digest="raw/../SCHEMA.md@sha256:" + "0" * 64))
    items = _cats(run_doctor(kb))["flash_pending"]["items"]
    assert items == [{"page": "wiki/sources/越界.md"}]


def test_index_quality_topology_and_total_excludes_structure(kb) -> None:
    write_page(kb, "wiki/concepts/孤页.md", body="短")  # 未进 index、桩页、孤页
    r = run_doctor(kb)
    cats = _cats(r)
    assert {i["kind"] for i in cats["index"]["items"]} == {"health.index_missing_page"}
    quality = {i["kind"] for i in cats["quality"]["items"]}
    assert {"health.stub_page", "lint.orphan"} <= quality
    assert cats["topology"]["section"] == "structure"
    assert r["total"] == sum(c["count"] for c in r["categories"] if c["section"] in ("blocking", "suggest"))


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


# ---------- 体检深查 · 引文核对（P4.34 §6.1）----------
def _quotes_kb(tmp_path: Path) -> Path:
    from test_quotes import Q_OK, RAW_A, _concept, _kb, _src

    altered = Q_OK.replace("丙", "甲")  # 改一个字：计入 strict
    unsupported = "这一整句话在原文里根本就找不到出处的引语"  # 原文中找不到
    label = "甲乙丙丁戊己庚辛壬癸→子丑寅卯辰巳午未"  # 含结构符号：标签样，不计入 strict
    return _kb(
        tmp_path,
        {"raw/a.md": RAW_A},
        {
            "sources/a.md": _src("raw/a.md", f'正文说"{Q_OK}"。又说"{altered}"。还说"{unsupported}"。另有"{label}"。'),
            "concepts/c.md": _concept('没有出处的页说"天地玄黄宇宙洪荒日月盈昃辰宿列张"。', []),
        },
    )


def test_doctor_quotes_groups_strict_review_unchecked(tmp_path) -> None:
    from guanlan.doctor import run_doctor_quotes
    from guanlan.quotes import format_report, run_quotes

    kb = _quotes_kb(tmp_path)
    r = run_doctor_quotes(kb)
    cats = {c["key"]: c for c in r["categories"]}
    assert list(cats) == ["strict", "review", "unchecked"]
    for c in r["categories"]:
        assert c["count"] == len(c["items"])  # 未超上限时明细即全量
    assert [i["reason"] for i in cats["strict"]["items"]] == ["strict"]
    assert cats["strict"]["items"][0]["best_raw"] == "raw/a.md"
    assert {(i["kind"], i["reason"]) for i in cats["review"]["items"]} == {("unsupported", "unsupported"), ("altered", "label_like")}
    assert [(i["page"], i["kind"]) for i in cats["unchecked"]["items"]] == [("wiki/concepts/c.md", "no_source")]
    # 汇总与 `guanlan quotes --json` 同口径
    import json

    summary = json.loads(format_report(run_quotes(kb), json_output=True))["summary"]
    assert (r["candidates"], r["passed"]) == (summary["candidates"], summary["passed"])
    assert cats["strict"]["count"] == summary["strict_findings"]


def test_doctor_quotes_items_capped_but_count_full(tmp_path, monkeypatch) -> None:
    from guanlan import doctor

    monkeypatch.setattr(doctor, "QUOTE_ITEMS_LIMIT", 1)
    review = {c["key"]: c for c in doctor.run_doctor_quotes(_quotes_kb(tmp_path))["categories"]}["review"]
    assert review["count"] == 2 and len(review["items"]) == 1


def test_doctor_quotes_reason_codes_cover_every_quotes_reason() -> None:
    # 前端按原因代号取文案：quotes 新增 / 改写原因文案时，代号表必须跟上（否则一律落成 unchecked）。
    from guanlan import doctor
    from guanlan.quotes import _REASONS

    assert doctor._QUOTE_REASON_KEY == {v: k for k, v in _REASONS.items()}
    assert len(doctor._QUOTE_REASON_KEY) == len(_REASONS)  # 文案两两不同，反查不丢键
    i18n = (Path(doctor.__file__).parent / "web" / "static" / "i18n.js").read_text(encoding="utf-8")
    for key in _REASONS:
        assert i18n.count(f'"doctor.quotes.reason.{key}"') == 2  # 中英各一份


def test_deepcheck_single_flight_and_failure() -> None:
    import threading

    from guanlan.web.deepcheck import DeepCheck

    gate = threading.Event()
    calls: list[int] = []

    def run() -> dict:
        calls.append(1)
        gate.wait(5)
        return {"n": len(calls)}

    dc = DeepCheck(run)
    assert dc.snapshot()["state"] == "idle"
    first = dc.start()
    assert first["state"] == "running" and first["running_s"] is not None
    assert dc.start()["started_at"] == first["started_at"]  # 在跑时再点：不另起一份
    gate.set()
    dc.join(5)
    done = dc.snapshot()
    assert done["state"] == "done" and done["result"] == {"n": 1} and done["finished_at"]
    assert calls == [1]

    def boom() -> dict:
        raise ValueError("坏了")

    bad = DeepCheck(boom)
    bad.start()
    bad.join(5)
    snap = bad.snapshot()
    assert snap["state"] == "failed" and "坏了" in snap["error"] and snap["result"] is None


def test_doctor_quotes_endpoint_writer_only(tmp_path) -> None:
    import pytest

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from guanlan.web.app import create_app

    kb = _quotes_kb(tmp_path)
    for name in ("AGENTAO.md", "SCHEMA.md"):  # create_app 才认得出知识库（同 conftest 的 kb）
        (kb / name).write_text("# x\n", encoding="utf-8")
    with TestClient(create_app(kb)) as client:
        assert client.get("/api/doctor/quotes").json()["state"] == "idle"
        assert client.post("/api/doctor/quotes").json()["state"] in ("running", "done")
        client.app.state.quotes_check.join(30)
        snap = client.get("/api/doctor/quotes").json()
        assert snap["state"] == "done"
        cats = {c["key"]: c["count"] for c in snap["result"]["categories"]}
        assert cats == {"strict": 1, "review": 2, "unchecked": 1}
    with TestClient(create_app(kb, reader=True)) as client:  # reader：不注册（体检按钮本就隐藏）
        assert client.get("/api/doctor/quotes").status_code in (404, 405)
        assert client.post("/api/doctor/quotes").status_code in (404, 405)


# ---------- 索引修复（P4.34 §6.2）----------
_INDEX = "# 索引\n\n## Sources\n\n## Entities\n\n- [死页](entities/Dead.md) — 悬空\n\n## Concepts\n\n## Syntheses\n"


def _reindex_kb(kb: Path) -> Path:
    (kb / "wiki" / "index.md").write_text(_INDEX, encoding="utf-8")
    write_page(kb, "wiki/concepts/漏登.md", body="正文足够长，避免被判为桩页。")  # 磁盘有、index 没有
    return kb


def _client(kb, **kw):
    import pytest

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from guanlan.web.app import create_app

    return TestClient(create_app(kb, **kw))


def _wait_task(client, task_id: str, timeout: float = 10.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/tasks/{task_id}").json()
        if data["state"] not in ("queued", "waiting_lock", "running", "cancelling", "rolling_back"):
            return data
        time.sleep(0.02)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内结束")


def test_doctor_index_category_offers_reindex_action(kb) -> None:
    cats = _cats(run_doctor(_reindex_kb(kb)))
    assert cats["index"]["action"] == "reindex"
    assert {i["kind"] for i in cats["index"]["items"]} == {"health.index_missing_page", "health.index_dangling"}


def test_reindex_preview_lists_both_and_writes_nothing(kb) -> None:
    _reindex_kb(kb)
    with _client(kb) as client:
        r = client.get("/api/reindex/preview").json()
    assert [(e["page"], e["section"]) for e in r["added"]] == [("wiki/concepts/漏登.md", "Concepts")]
    assert r["pruned"] == ["- [死页](entities/Dead.md) — 悬空"]
    assert (kb / "wiki" / "index.md").read_text(encoding="utf-8") == _INDEX  # 预览不写盘


def test_reindex_task_registers_and_prunes_only_when_asked(kb) -> None:
    _reindex_kb(kb)
    with _client(kb) as client:
        r = client.post("/api/reindex", json={}).json()
        env = _wait_task(client, r["task_id"])
        assert env["state"] == "succeeded" and env["kind"] == "reindex" and env["params"] == {"prune": False}
        idx = (kb / "wiki" / "index.md").read_text(encoding="utf-8")
        assert "(concepts/漏登.md)" in idx and "entities/Dead.md" in idx  # 默认不删悬空（同 CLI）
        assert "登记 1 页" in client.get(f"/api/tasks/{r['task_id']}").json()["log"]["text"]

        r = client.post("/api/reindex", json={"prune": True}).json()
        assert _wait_task(client, r["task_id"])["state"] == "succeeded"
        idx = (kb / "wiki" / "index.md").read_text(encoding="utf-8")
        assert "entities/Dead.md" not in idx and idx.count("(concepts/漏登.md)") == 1  # 不重复登记


def test_reindex_job_bumps_wiki_generation(kb) -> None:
    # 写了 wiki/：可写会话缓存的 check 基线要重拍。直接调完工回调——它在信封转终态之后才跑，等任务结束再看会有时序竞态。
    with _client(kb) as client:
        gate = client.app.state.write_gate
        before = gate.wiki_generation
        client.app.state.jobs._on_job_done("reindex")
        assert gate.wiki_generation == before + 1


def test_reindex_rejected_during_writable_turn_and_absent_for_reader(kb) -> None:
    _reindex_kb(kb)
    with _client(kb) as client:
        client.app.state.write_gate.enter_writable()
        try:
            assert client.post("/api/reindex", json={}).status_code == 423  # 层③
        finally:
            client.app.state.write_gate.exit_writable()
    with _client(kb, reader=True) as client:
        assert client.get("/api/reindex/preview").status_code in (404, 405)
        assert client.post("/api/reindex", json={}).status_code in (404, 405)
    assert (kb / "wiki" / "index.md").read_text(encoding="utf-8") == _INDEX


def test_reindex_task_can_be_retried(kb) -> None:
    _reindex_kb(kb)
    with _client(kb) as client:
        tid = client.post("/api/reindex", json={"prune": True}).json()["task_id"]
        assert _wait_task(client, tid)["state"] == "succeeded"
        r = client.post(f"/api/tasks/{tid}/retry")
        assert r.status_code == 200, r.text  # 参数按 ReindexBody 重建
        assert _wait_task(client, r.json()["task_id"])["params"] == {"prune": True}


def test_reindex_preview_lists_items_it_cannot_fix(kb) -> None:
    # 体检报了、reindex 修不了的：分区目录外的页、与有效链接同行的悬空条目。预览不能报「已同步」。
    (kb / "wiki" / "index.md").write_text(
        "# 索引\n\n## Concepts\n\n- [活](concepts/活.md)、[死](concepts/死.md)\n", encoding="utf-8"
    )
    write_page(kb, "wiki/concepts/活.md", body="正文足够长，避免被判为桩页。")
    write_page(kb, "wiki/notes/散页.md", body="正文足够长，避免被判为桩页。")
    with _client(kb) as client:
        r = client.get("/api/reindex/preview").json()
    assert r["added"] == [] and r["pruned"] == []
    assert r["manual_pages"] == ["wiki/notes/散页.md"]
    assert r["manual_links"] == ["concepts/死.md"]


# ── 摘除失效来源（P4.34 §6.3）──────────────────────────────────────────────

_BODY_WITH_DEAD = "正文足够长，避免被判为桩页。事实一（[[old]]，raw/old.md:3）。事实二（[[new]]）。"


def _dead_source_kb(kb: Path) -> Path:
    """`Mixed` 另有有效来源 new（可摘）；`Lonely` 只剩失效来源 old（不可摘，要人工）。"""
    write_page(kb, "wiki/sources/new.md", type="source", sources="['new']", body="新来源摘要，正文足够长。")
    write_page(kb, "wiki/entities/Mixed.md", type="entity", sources="['old', 'new']", body=_BODY_WITH_DEAD)
    write_page(kb, "wiki/entities/Lonely.md", type="entity", sources="['old']", body="正文足够长，避免被判为桩页。")
    return kb


def test_check_hints_droppable_dead_source_only_when_page_has_live_source(kb) -> None:
    _dead_source_kb(kb)
    unresolved = {v.page: v.detail for v in run_check(kb / "wiki").violations if v.kind == "sources.unresolved"}
    assert "另有 1 个有效来源，此项可直接摘除" in unresolved["wiki/entities/Mixed.md"]
    # 漏报防线的反面：全部失效的页不能被说成「可直接摘除」
    assert "可直接摘除" not in unresolved["wiki/entities/Lonely.md"]


def test_plan_and_drop_dead_sources(kb) -> None:
    from guanlan.deadsrc import drop_dead_sources, plan_dead_sources

    _dead_source_kb(kb)
    plan = {d.page: d for d in plan_dead_sources(kb)}
    assert set(plan) == {"wiki/entities/Mixed.md", "wiki/entities/Lonely.md"}
    assert plan["wiki/entities/Mixed.md"].droppable and plan["wiki/entities/Mixed.md"].live == ["new"]
    assert not plan["wiki/entities/Lonely.md"].droppable

    lonely_before = (kb / "wiki/entities/Lonely.md").read_bytes()
    done, left = drop_dead_sources(kb)
    assert [d.page for d in done] == ["wiki/entities/Mixed.md"]
    assert [d.page for d in left] == ["wiki/entities/Lonely.md"]
    mixed = (kb / "wiki/entities/Mixed.md").read_text(encoding="utf-8")
    assert "sources:\n- new\n" in mixed or "sources: [new]" in mixed or "sources:\n  - new\n" in mixed
    assert _BODY_WITH_DEAD in mixed  # 正文逐字不动
    assert (kb / "wiki/entities/Lonely.md").read_bytes() == lonely_before  # 全部失效：一字不改

    # 摘完后 Mixed 不再阻断；Lonely 仍在；幂等：再跑一次什么都不摘
    pages = {v.page for v in run_check(kb / "wiki").violations if v.kind == "sources.unresolved"}
    assert pages == {"wiki/entities/Lonely.md"}
    assert drop_dead_sources(kb)[0] == []


def test_plan_skips_untrusted_sources(kb) -> None:
    from guanlan.deadsrc import plan_dead_sources

    write_page(kb, "wiki/entities/Weird.md", type="entity", sources="[{a: 1}, 'old']", body="正文足够长。")
    assert plan_dead_sources(kb) == []  # sources 非字符串列表：归 frontmatter 校验，不在这里动


def test_doctor_marks_droppable_blocking_items(kb) -> None:
    cats = _cats(run_doctor(_dead_source_kb(kb)))
    blocking = cats["blocking"]
    assert blocking["action"] == "dropsrc"
    fixes = {i["page"]: i.get("fix") for i in blocking["items"] if i["kind"] == "sources.unresolved"}
    # 两页都有修复入口：Mixed 摘失效项（§6.3），Lonely 可整页删（§6.4）
    assert fixes == {"wiki/entities/Mixed.md": "dropsrc", "wiki/entities/Lonely.md": "dropsrc"}


def test_doctor_blocking_has_no_action_for_dead_summary_page(kb) -> None:
    # 摘要页随源走：全部来源失效的摘要页既不能摘、也不在这里删（归撤回 / 恢复）
    write_page(kb, "wiki/sources/S.md", type="source", sources="['old']", body="正文足够长，避免被判为桩页。")
    cats = _cats(run_doctor(kb))
    assert cats["blocking"]["action"] is None
    assert [i.get("fix") for i in cats["blocking"]["items"] if i["kind"] == "sources.unresolved"] == [None]


def test_drop_sources_preview_and_task(kb) -> None:
    _dead_source_kb(kb)
    with _client(kb) as client:
        r = client.get("/api/sources/drop-dead/preview").json()
        assert r["droppable"] == [{"page": "wiki/entities/Mixed.md", "dead": ["old"], "live": ["new"], "in_trash": []}]
        assert r["prunable"] == [
            {"page": "wiki/entities/Lonely.md", "dead": ["old"], "live": [], "index_lines": [], "index_lines_shared": [], "backlinks": [], "in_trash": []}
        ]
        assert r["manual"] == []
        assert "'old'" in (kb / "wiki/entities/Mixed.md").read_text(encoding="utf-8")  # 预览不写盘

        r = client.post("/api/sources/drop-dead", json={"drops": {"wiki/entities/Mixed.md": ["old"]}}).json()
        env = _wait_task(client, r["task_id"])
        assert env["state"] == "succeeded" and env["kind"] == "dropsrc" and env["params"] == {"drops": {"wiki/entities/Mixed.md": ["old"]}}
        log = client.get(f"/api/tasks/{r['task_id']}").json()["log"]["text"]
        assert "摘除失效来源：1 页" in log and "wiki/entities/Lonely.md" not in log  # 只处理请求清单里的页
        assert client.post(f"/api/tasks/{r['task_id']}/retry").status_code == 200  # 参数按 DropSourcesBody 重建
    assert {v.page for v in run_check(kb / "wiki").violations if v.kind == "sources.unresolved"} == {
        "wiki/entities/Lonely.md"
    }


def test_drop_sources_rejected_during_writable_turn_and_absent_for_reader(kb) -> None:
    _dead_source_kb(kb)
    with _client(kb) as client:
        client.app.state.write_gate.enter_writable()
        try:
            assert client.post("/api/sources/drop-dead", json={"drops": {"wiki/entities/Mixed.md": ["old"]}}).status_code == 423  # 层③
        finally:
            client.app.state.write_gate.exit_writable()
        client.app.state.jobs._on_job_done("dropsrc")  # 写了 wiki/：代际要递增（同 reindex 的测试手法）
    with _client(kb, reader=True) as client:
        assert client.get("/api/sources/drop-dead/preview").status_code in (404, 405)
        assert client.post("/api/sources/drop-dead", json={"drops": {"wiki/entities/Mixed.md": ["old"]}}).status_code in (404, 405)


def test_every_task_kind_fits_task_id_pattern() -> None:
    # 任务 id 形如 `<日期>-<时间>-<kind>-<hex4>`，kind 段只认小写字母：带下划线的 kind 建得出信封却查不到（404）。
    from guanlan.web.tasks import _ID_RE, TASK_KINDS

    for kind in TASK_KINDS:
        assert _ID_RE.match(f"20261123-220000-{kind}-abcd"), kind


def _prune_kb(kb: Path) -> Path:
    _dead_source_kb(kb)
    (kb / "wiki" / "index.md").write_text(
        "# 索引\n\n## Entities\n\n- [Lonely](entities/Lonely.md) — 失源\n- [Mixed](entities/Mixed.md) — 留\n", encoding="utf-8"
    )
    write_page(kb, "wiki/concepts/Linker.md", body="正文足够长，避免被判为桩页。见 [[Lonely]]。")
    return kb


def test_plan_prune_lists_pages_backlinks_and_index_lines(kb) -> None:
    from guanlan.deadsrc import plan_prune

    plan = plan_prune(_prune_kb(kb))
    assert [d.page for d in plan.pages] == ["wiki/entities/Lonely.md"]  # Mixed 另有来源，不在删页之列
    assert plan.backlinks == {"wiki/entities/Lonely.md": ["wiki/concepts/Linker.md"]}
    assert plan.index_lines == ["- [Lonely](entities/Lonely.md) — 失源"]
    assert plan_prune(kb, ["wiki/entities/Mixed.md"]).pages == []  # 只认当前仍全部失效的页


def test_prune_moves_page_to_trash_and_trash_recognizes_it(kb) -> None:
    from guanlan.deadsrc import prune_pages
    from guanlan.restore import restore_block
    from guanlan.trash import annotate, scan

    _prune_kb(kb)
    lonely = (kb / "wiki/entities/Lonely.md").read_bytes()
    trash_dir, plan, skipped = prune_pages(kb, ["wiki/entities/Lonely.md", "wiki/entities/Mixed.md"])
    assert skipped == ["wiki/entities/Mixed.md"]  # 提交了但不可删：不碰
    assert not (kb / "wiki/entities/Lonely.md").exists()
    assert (trash_dir / "wiki/entities/Lonely.md").read_bytes() == lonely
    assert (kb / "wiki/entities/Mixed.md").exists()
    idx = (kb / "wiki/index.md").read_text(encoding="utf-8")
    assert "entities/Lonely.md" not in idx and "entities/Mixed.md" in idx
    # 阻断消失；Linker 的 [[Lonely]] 变断链（警告）
    vs = run_check(kb / "wiki").violations
    assert not [v for v in vs if v.page == "wiki/entities/Lonely.md"]
    assert any(v.kind == "wikilink.broken" for v in vs if v.page == "wiki/concepts/Linker.md")

    [entry] = annotate(kb, scan(kb))
    assert (entry.kind, entry.state, entry.protected, entry.subject) == ("prune", "complete", [], "Lonely")
    assert "不支持自动恢复" in restore_block(entry)
    d = entry.detail()
    assert d["index_lines_removed"] == ["- [Lonely](entities/Lonely.md) — 失源"]
    assert d["backlinks"] == ["wiki/concepts/Linker.md"]


def test_prune_entry_incomplete_is_not_protected_and_bad_paths_flagged(kb) -> None:
    import json

    from guanlan.trash import annotate, scan

    _prune_kb(kb)
    entry_dir = kb / ".trash" / "prune@20261123T120000_000000"
    entry_dir.mkdir(parents=True)
    rec = {"format": 1, "kind": "prune", "moved": ["wiki/entities/Lonely.md", "wiki/sources/new.md", "../x.md"]}
    (entry_dir / "prune.json").write_text(json.dumps(rec), encoding="utf-8")
    [entry] = annotate(kb, scan(kb))
    assert entry.kind == "prune" and entry.state == "incomplete"  # Lonely 仍在原处：删页没做完
    # 删页没有续跑步骤：未完成也不保护（取消回滚后页已放回原处，保护会让条目永远删不掉）
    assert entry.protected == []
    bad = {it.path: it.invalid for it in entry.items if it.invalid}
    assert set(bad) == {"wiki/sources/new.md", "../x.md"}


def test_prune_task_only_deletes_listed_pages(kb) -> None:
    _prune_kb(kb)
    write_page(kb, "wiki/entities/Other.md", type="entity", sources="['gone']", body="正文足够长，避免被判为桩页。")
    with _client(kb) as client:
        r = client.get("/api/sources/drop-dead/preview").json()
        assert [d["page"] for d in r["prunable"]] == ["wiki/entities/Lonely.md", "wiki/entities/Other.md"]
        assert r["prunable"][0]["backlinks"] == ["wiki/concepts/Linker.md"]
        assert r["prunable"][0]["index_lines"] == ["- [Lonely](entities/Lonely.md) — 失源"]
        r = client.post("/api/pages/prune", json={"pages": ["wiki/entities/Lonely.md"]}).json()
        env = _wait_task(client, r["task_id"])
        assert env["state"] == "succeeded" and env["kind"] == "prune"
        assert env["params"] == {"pages": ["wiki/entities/Lonely.md"]}
        assert "已删除 1 页" in client.get(f"/api/tasks/{r['task_id']}").json()["log"]["text"]
        assert client.post("/api/pages/prune", json={"pages": []}).status_code == 422
    assert not (kb / "wiki/entities/Lonely.md").exists()
    assert (kb / "wiki/entities/Other.md").exists()  # 没列在请求里：人没看过，不删
    with _client(kb, reader=True) as client:
        assert client.post("/api/pages/prune", json={"pages": ["wiki/entities/Other.md"]}).status_code in (404, 405)


def test_gate_baseline_ignores_droppable_hint_when_live_source_added(kb) -> None:
    """先撤回、后补源：补上有效来源只让既有失效项多出「可直接摘除」提示，写门禁不能把它当成新违规。"""
    from guanlan.gate import _vkey, check_baseline, partition_violations

    _dead_source_kb(kb)
    baseline = check_baseline(kb)
    page = kb / "wiki/entities/Lonely.md"
    page.write_text(page.read_text(encoding="utf-8").replace("['old']", "['old', 'new']"), encoding="utf-8")
    blocking, _ = partition_violations(run_check(kb / "wiki").violations)
    lonely = [v for v in blocking if v.page == "wiki/entities/Lonely.md"]
    assert lonely and "可直接摘除" in lonely[0].detail  # 文案确实变了
    assert [v for v in blocking if _vkey(v) not in baseline] == []


def test_preview_prune_keeps_same_batch_linkers_per_page(kb) -> None:
    # 预览里可以取消勾选：A 被同批可删页 B 链向时，B 必须出现在 A 的入链里（只删 A 时 B 会断链），
    # index 行按页分开，前端才能按勾选现算。执行（plan_prune）仍排除同批页。
    from guanlan.deadsrc import plan_prune, preview_prune

    (kb / "wiki" / "index.md").write_text(
        "# 索引\n\n## Entities\n\n- [A](entities/A.md) — a\n- [B](entities/B.md) — b\n", encoding="utf-8"
    )
    write_page(kb, "wiki/entities/A.md", type="entity", sources="['gone']", body="正文足够长，避免被判为桩页。")
    write_page(kb, "wiki/entities/B.md", type="entity", sources="['gone']", body="正文足够长，避免被判为桩页。见 [[A]]。")
    rows = {r["page"]: r for r in preview_prune(kb)}
    assert rows["wiki/entities/A.md"]["backlinks"] == ["wiki/entities/B.md"]
    assert rows["wiki/entities/A.md"]["index_lines"] == ["- [A](entities/A.md) — a"]
    assert rows["wiki/entities/B.md"]["index_lines"] == ["- [B](entities/B.md) — b"]
    assert plan_prune(kb).backlinks["wiki/entities/A.md"] == []  # 两页一起删：B 也走了，不算断链


def test_preview_prune_shared_index_line_needs_all_pages(kb) -> None:
    # 一行链向两张可删页：单删其中一页时执行不会删这行，预览不能把它算进单页的 index_lines，
    # 而要另列 index_lines_shared（带全部目标页），前端只在都勾选时计入。
    from guanlan.deadsrc import plan_prune, preview_prune

    (kb / "wiki" / "index.md").write_text(
        "# 索引\n\n## Entities\n\n- [A](entities/A.md) / [B](entities/B.md) — ab\n", encoding="utf-8"
    )
    write_page(kb, "wiki/entities/A.md", type="entity", sources="['gone']", body="正文足够长，避免被判为桩页。")
    write_page(kb, "wiki/entities/B.md", type="entity", sources="['gone']", body="正文足够长，避免被判为桩页。")
    rows = {r["page"]: r for r in preview_prune(kb)}
    shared = {"line": "- [A](entities/A.md) / [B](entities/B.md) — ab", "pages": ["wiki/entities/A.md", "wiki/entities/B.md"]}
    for page in ("wiki/entities/A.md", "wiki/entities/B.md"):
        assert rows[page]["index_lines"] == []
        assert rows[page]["index_lines_shared"] == [shared]
    assert plan_prune(kb, ["wiki/entities/A.md"]).index_lines == []  # 执行口径一致：单删不删这行
    assert plan_prune(kb).index_lines == [shared["line"]]


def test_chat_violation_key_ignores_droppable_hint() -> None:
    # Web 对话的「本轮新增」差集与写门禁同一口径：补上有效来源只改提示、不算新问题
    from guanlan.check import Violation
    from guanlan.web.chat_support import _violation_key

    plain = Violation("wiki/entities/X.md", "sources.unresolved", "sources 列出的 'old' 无 wiki/sources/old.md")
    from guanlan.check import SOURCES_HINT_SEP, SOURCES_HINT_TAIL

    hinted = Violation(plain.page, plain.kind, plain.detail + SOURCES_HINT_SEP + "1" + SOURCES_HINT_TAIL)
    assert _violation_key(plain) == _violation_key(hinted)


def test_plan_dead_sources_skips_non_utf8_pages(kb) -> None:
    # check 对非 UTF-8 页只报 page.not_utf8；这里若列为「可摘」，一键摘除每次都会跳过它
    from guanlan.deadsrc import plan_dead_sources

    write_page(kb, "wiki/sources/new.md", type="source", sources="['new']", body="新来源摘要，正文足够长。")
    page = kb / "wiki/entities/Gbk.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes("---\ntitle: 甲\ntype: entity\ntags: []\nsources: ['old', 'new']\nlast_updated: 2026-06-03\n---\n\n正文\n".encode("gbk"))
    assert plan_dead_sources(kb) == []


def test_prune_entry_notes_leftover_index_lines(kb) -> None:
    # 页已移进回收区、删 index 行之前中断：条目上要说清楚还剩登记行、怎么补
    import json

    from guanlan.trash import scan

    _prune_kb(kb)
    entry_dir = kb / ".trash" / "prune@20261123T120000_000000"
    (entry_dir / "wiki/entities").mkdir(parents=True)
    (kb / "wiki/entities/Lonely.md").rename(entry_dir / "wiki/entities/Lonely.md")
    rec = {"format": 1, "kind": "prune", "moved": ["wiki/entities/Lonely.md"],
           "index_lines_removed": ["- [Lonely](entities/Lonely.md) — 失源"]}
    (entry_dir / "prune.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    [entry] = scan(kb)
    assert entry.state == "complete" and "reindex --prune" in entry.note


def test_vkey_only_strips_trailing_hint() -> None:
    # slug 里碰巧含提示字样：不能把它截掉、让两个不同的失效 slug 撞成同一身份键
    from guanlan.check import Violation
    from guanlan.gate import _vkey

    a = Violation("wiki/x.md", "sources.unresolved", "sources 列出的 'a；该页另有 1' 无 wiki/sources/a；该页另有 1.md")
    b = Violation("wiki/x.md", "sources.unresolved", "sources 列出的 'a；该页另有 2' 无 wiki/sources/a；该页另有 2.md")
    assert _vkey(a) != _vkey(b)
    from guanlan.check import SOURCES_HINT_SEP, SOURCES_HINT_TAIL

    hinted = Violation(a.page, a.kind, a.detail + SOURCES_HINT_SEP + "3" + SOURCES_HINT_TAIL)
    assert _vkey(hinted) == _vkey(a)


def test_drop_preview_flags_slugs_still_in_trash(kb) -> None:
    # 先撤回（P 当时只有 old，原样保留）、后补源：摘掉 old 后再恢复它，P 不会自动补回——预览要逐项标出
    from guanlan.remove import run_remove

    write_page(kb, "wiki/sources/old.md", type="source", sources="['old']", body="旧来源摘要，正文足够长。")
    write_page(kb, "wiki/entities/P.md", type="entity", sources="['old']", body="正文足够长，避免被判为桩页。")
    assert run_remove(kb, src="old", yes=True, json_output=False) == 0
    write_page(kb, "wiki/sources/new.md", type="source", sources="['new']", body="新来源摘要，正文足够长。")
    write_page(kb, "wiki/entities/P.md", type="entity", sources="['old', 'new']", body="正文足够长，避免被判为桩页。")
    with _client(kb) as client:
        r = client.get("/api/sources/drop-dead/preview").json()
    assert r["droppable"] == [{"page": "wiki/entities/P.md", "dead": ["old"], "live": ["new"], "in_trash": ["old"]}]


def test_prune_preview_flags_trash_and_unreadable_index(kb) -> None:
    from guanlan.remove import run_remove

    write_page(kb, "wiki/sources/old.md", type="source", sources="['old']", body="旧来源摘要，正文足够长。")
    write_page(kb, "wiki/entities/P.md", type="entity", sources="['old']", body="正文足够长，避免被判为桩页。")
    assert run_remove(kb, src="old", yes=True, json_output=False) == 0
    with _client(kb) as client:
        r = client.get("/api/sources/drop-dead/preview").json()
        assert r["prunable"][0]["in_trash"] == ["old"] and r["index_readable"] is True
        (kb / "wiki/index.md").write_bytes("# 索引\n\n- [P](entities/P.md) — 页\n".encode("gbk"))
        r = client.get("/api/sources/drop-dead/preview")
        assert r.status_code == 200 and r.json()["index_readable"] is False  # 预览照出，不 500
        tid = client.post("/api/pages/prune", json={"pages": ["wiki/entities/P.md"]}).json()["task_id"]
        assert _wait_task(client, tid)["state"] == "failed"
    assert (kb / "wiki/entities/P.md").exists()  # 中止在移动任何页之前
    assert not list((kb / ".trash").glob("prune@*"))


def test_drop_sources_only_touches_listed_pages(kb) -> None:
    # 预览之后才变成可摘的页人没看过：不碰（同删页）；空清单 422
    _dead_source_kb(kb)
    write_page(kb, "wiki/entities/Later.md", type="entity", sources="['gone', 'new']", body="正文足够长，避免被判为桩页。")
    later = (kb / "wiki/entities/Later.md").read_bytes()
    with _client(kb) as client:
        assert client.post("/api/sources/drop-dead", json={"drops": {}}).status_code == 422
        r = client.post("/api/sources/drop-dead", json={"drops": {"wiki/entities/Mixed.md": ["old"]}}).json()
        assert _wait_task(client, r["task_id"])["state"] == "succeeded"
    assert "'old'" not in (kb / "wiki/entities/Mixed.md").read_text(encoding="utf-8")
    assert (kb / "wiki/entities/Later.md").read_bytes() == later


def test_drop_sources_only_drops_previewed_slugs(kb) -> None:
    # 预览里这页只列出 old；之后 gone 也失效了——执行时只摘 old，gone 人没看过，留着
    from guanlan.deadsrc import drop_dead_sources

    write_page(kb, "wiki/sources/new.md", type="source", sources="['new']", body="新来源摘要，正文足够长。")
    write_page(kb, "wiki/entities/X.md", type="entity", sources="['old', 'gone', 'new']", body="正文足够长，避免被判为桩页。")
    done, _left = drop_dead_sources(kb, {"wiki/entities/X.md": ["old"]})
    assert [(d.page, d.dead) for d in done] == [("wiki/entities/X.md", ["old"])]
    text = (kb / "wiki/entities/X.md").read_text(encoding="utf-8")
    assert "old" not in text.split("---")[1] and "gone" in text


def test_prune_backlinks_include_overview(kb) -> None:
    # 图不含 config 页；overview.md 链向被删页时删后会断链，预览必须列出
    from guanlan.deadsrc import plan_prune

    _dead_source_kb(kb)
    (kb / "wiki/overview.md").write_text("# 总览\n\n见 [[Lonely]]。<!-- [[Mixed]] -->\n", encoding="utf-8")
    assert plan_prune(kb).backlinks == {"wiki/entities/Lonely.md": ["wiki/overview.md"]}
