"""P3 health 文件级体检测试（零 LLM，见 docs/P3-健康与图谱.md §11）。"""

from pathlib import Path

from guanlan.health import STUB_MIN_CHARS, health_entrypoint, run_health

FM = "---\ntitle: '{title}'\ntype: {type}\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n{body}\n"


def _page(wiki: Path, rel: str, *, title="T", type="concept", body="正文") -> None:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(title=title, type=type, body=body), encoding="utf-8")


def _seed_config(wiki: Path, index: str = "# 索引\n") -> None:
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text(index, encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")


def _kinds(report) -> list[str]:
    return [f.kind for f in report.findings]


# ---------- 桩页 ----------


def test_empty_body_is_stub(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Empty.md", type="entity", body="   \n  \n")

    report = run_health(wiki)
    assert any(
        f.kind == "health.stub_page" and "entities/Empty.md" in f.page for f in report.findings
    )


def test_only_heading_is_stub(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Head.md", type="entity", body="# 标题\n\n## 小节\n")

    report = run_health(wiki)
    assert "health.stub_page" in _kinds(report)


def test_below_threshold_is_stub_at_threshold_is_not(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    short = "短" * (STUB_MIN_CHARS - 1)
    enough = "够" * STUB_MIN_CHARS
    _page(wiki, "concepts/Short.md", body=short)
    _page(wiki, "concepts/Enough.md", body=enough)

    report = run_health(wiki)
    stub_pages = {f.page for f in report.findings if f.kind == "health.stub_page"}
    assert any("Short.md" in p for p in stub_pages)
    assert not any("Enough.md" in p for p in stub_pages)  # 达阈值不判


def test_heading_plus_enough_body_not_stub(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "concepts/Good.md", body="# 标题\n\n" + "实质内容" * 10)

    report = run_health(wiki)
    assert not any(
        f.kind == "health.stub_page" and "Good.md" in f.page for f in report.findings
    )


# ---------- index 双向同步 ----------


def test_index_missing_page(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki, index="# 索引\n\n## Concepts\n")  # 不收录任何页
    _page(wiki, "concepts/Bar.md", body="实质内容" * 10)

    report = run_health(wiki)
    assert any(
        f.kind == "health.index_missing_page" and "concepts/Bar.md" in f.page
        for f in report.findings
    )


def test_index_covered_page_not_flagged(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki, index="# 索引\n\n## Concepts\n- [Bar](concepts/Bar.md) — 一句话\n")
    _page(wiki, "concepts/Bar.md", body="实质内容" * 10)

    report = run_health(wiki)
    assert "health.index_missing_page" not in _kinds(report)


def test_index_dangling(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki, index="# 索引\n\n## Sources\n- [X](sources/x.md) — 不存在\n")

    report = run_health(wiki)
    assert any(
        f.kind == "health.index_dangling" and "sources/x.md" in f.detail
        for f in report.findings
    )


def test_index_link_escaping_wiki_is_dangling(tmp_path: Path):
    """index 链接用 `..`/绝对路径指向库外即使该文件存在，也判 index_dangling，不借 is_file 蒙混。"""
    wiki = tmp_path / "wiki"
    # 在 wiki/ 之外放一个真实文件，确保 (wiki/../outside.md) 能 is_file 命中。
    (tmp_path / "outside.md").write_text("库外文件\n", encoding="utf-8")
    _seed_config(wiki, index="# 索引\n\n## X\n- [越界](../outside.md) — 指向库外\n")

    report = run_health(wiki)
    assert any(
        f.kind == "health.index_dangling" and "../outside.md" in f.detail
        for f in report.findings
    )


def test_config_pages_excluded_from_stub_but_index_is_reference(tmp_path: Path):
    """config 页本身不被体检（overview.md 很短也不报桩页），但 index.md 作参照物被读取。"""
    wiki = tmp_path / "wiki"
    # overview.md 很短，但作为 config 不该被判桩页。
    _seed_config(wiki, index="# 索引\n\n## Overview\n- [总览](overview.md) — 综述\n")

    report = run_health(wiki)
    assert "health.stub_page" not in _kinds(report)  # config 不被体检
    assert "health.index_dangling" not in _kinds(report)  # overview.md 存在，非悬空


# ---------- 页型↔目录一致性（P3.10） ----------


def _raw(wiki: Path, rel: str, content: str) -> None:
    """直写一张页面（绕过 `_page` 的固定 frontmatter，用于坏/缺 type 用例）。"""
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_type_matches_dir_no_mismatch(tmp_path: Path):
    """四规范目录各放一张 type 相符的页 → 无 type_dir_mismatch。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "sources/A.md", type="source", body="实质内容" * 10)
    _page(wiki, "entities/B.md", type="entity", body="实质内容" * 10)
    _page(wiki, "concepts/C.md", type="concept", body="实质内容" * 10)
    _page(wiki, "syntheses/D.md", type="synthesis", body="实质内容" * 10)

    report = run_health(wiki)
    assert "health.type_dir_mismatch" not in _kinds(report)
    assert "health.uncharted_page" not in _kinds(report)  # 四目录内页不报 uncharted


def test_type_dir_mismatch_detected(tmp_path: Path):
    """entities/ 目录配 type=source → 一条 type_dir_mismatch，detail 含两侧 type。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/Foo.md", type="source", body="实质内容" * 10)

    report = run_health(wiki)
    hits = [f for f in report.findings if f.kind == "health.type_dir_mismatch"]
    assert len(hits) == 1
    assert "entities/Foo.md" in hits[0].page
    assert "type=source" in hits[0].detail and "entity" in hits[0].detail


def test_uncharted_pages_flagged(tmp_path: Path):
    """杂目录页与根级非 config 页 → uncharted_page；四目录内页不报。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "misc/Y.md", body="实质内容" * 10)  # 杂目录
    _page(wiki, "Z.md", body="实质内容" * 10)  # 直接躺 wiki/ 根（非 config）
    _page(wiki, "concepts/Charted.md", body="实质内容" * 10)  # 规范目录

    report = run_health(wiki)
    uncharted = {f.page for f in report.findings if f.kind == "health.uncharted_page"}
    assert any("misc/Y.md" in p for p in uncharted)
    assert any(p.endswith("wiki/Z.md") for p in uncharted)
    assert not any("Charted.md" in p for p in uncharted)


def test_uncharted_not_also_type_mismatch(tmp_path: Path):
    """游离目录页只报 uncharted、不顺带报 type_dir_mismatch（两者互斥）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "misc/Y.md", type="entity", body="实质内容" * 10)

    report = run_health(wiki)
    page_kinds = {f.kind for f in report.findings if "misc/Y.md" in f.page}
    assert "health.uncharted_page" in page_kinds
    assert "health.type_dir_mismatch" not in page_kinds


def test_bad_or_missing_type_not_mismatch(tmp_path: Path):
    """缺 type / 非法 type / 非字符串 type 一律不报 type_dir_mismatch（交 check 阻断）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    # 缺 type（check 报 missing_key）。
    _raw(
        wiki,
        "entities/NoType.md",
        "---\ntitle: 'NoType'\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n"
        + "实质内容" * 10
        + "\n",
    )
    # 非法 type 字符串（check 报 bad_type）。
    _page(wiki, "entities/Bogus.md", type="bogus", body="实质内容" * 10)
    # 非字符串 type（YAML list；check 报 bad_type）。
    _raw(
        wiki,
        "entities/ListType.md",
        "---\ntitle: 'ListType'\ntype: [a, b]\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n"
        + "实质内容" * 10
        + "\n",
    )

    report = run_health(wiki)
    assert "health.type_dir_mismatch" not in _kinds(report)


def test_schema_drift_strict_exit_six(tmp_path: Path):
    """仅 schema 漂移 finding 时，--strict 也退 6、默认退 0。"""
    wiki = tmp_path / "wiki"
    # 收录该页避免 index_missing 噪声——隔离出 type_dir_mismatch 单一 finding。
    _seed_config(wiki, index="# 索引\n\n## Entities\n- [Foo](entities/Foo.md) — 一句话\n")
    _page(wiki, "entities/Foo.md", type="source", body="实质内容" * 10)

    report = run_health(wiki)
    assert _kinds(report) == ["health.type_dir_mismatch"]  # 别无他噪
    assert health_entrypoint(tmp_path, json_output=False, strict=False) == 0
    assert health_entrypoint(tmp_path, json_output=False, strict=True) == 6


# ---------- 退出码 / JSON ----------


def test_exit_codes_default_zero_strict_six(tmp_path: Path, capsys):
    _seed_config(tmp_path / "wiki", index="# 索引\n")
    _page(tmp_path / "wiki", "concepts/Bar.md", body="短")  # 桩 + 未收录，必有 findings

    # 默认：即便有建议也退 0（建议非门禁）。
    assert health_entrypoint(tmp_path, json_output=False, strict=False) == 0
    # --strict：有建议 → 退 6。
    assert health_entrypoint(tmp_path, json_output=False, strict=True) == 6


def test_clean_wiki_strict_zero(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki, index="# 索引\n\n## Concepts\n- [Bar](concepts/Bar.md) — 一句话\n")
    _page(wiki, "concepts/Bar.md", body="实质内容" * 10)

    assert health_entrypoint(tmp_path, json_output=False, strict=True) == 0  # 无 findings


def test_json_contract(tmp_path: Path, capsys):
    import json

    _seed_config(tmp_path / "wiki", index="# 索引\n")
    _page(tmp_path / "wiki", "concepts/Bar.md", body="短")

    health_entrypoint(tmp_path, json_output=True, strict=False)
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["pages_checked"] == 1
    assert {"page", "kind", "detail"} <= set(data["findings"][0].keys())


def test_non_kb_root_fails(tmp_path: Path):
    assert health_entrypoint(tmp_path, json_output=False, strict=False) == 1  # 无 wiki/


# ---------- finding 因果排序（gbrain §3，纯展示层） ----------


def test_index_sync_ordered_before_content_quality(tmp_path: Path):
    """数据完整性/结构同步类（index_missing_page）排在内容/组织建议（stub_page）之前。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki, index="# 索引\n\n## Concepts\n")  # 不收录任何页 → index_missing
    _page(wiki, "entities/Stub.md", type="entity", body="短")  # 桩页 + 未收录

    kinds = _kinds(run_health(wiki))
    assert "health.index_missing_page" in kinds and "health.stub_page" in kinds
    assert kinds.index("health.index_missing_page") < kinds.index("health.stub_page")


def test_health_ordering_deterministic_and_set_preserved(tmp_path: Path):
    """因果排序确定性可重放；finding 集合与退出码不变（建议非门禁）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki, index="# 索引\n\n## Concepts\n")
    _page(wiki, "concepts/Stub.md", body="短")  # 桩页 + 未收录
    _page(wiki, "entities/Empty.md", type="entity", body="  \n")  # 空桩页 + 未收录

    r1, r2 = run_health(wiki), run_health(wiki)
    assert [(f.page, f.kind, f.detail) for f in r1.findings] == [
        (f.page, f.kind, f.detail) for f in r2.findings
    ]
    # 集合（与顺序无关）逐项核对：两页各出 stub_page + index_missing_page，重排不增不减。
    assert {(f.page, f.kind) for f in r1.findings} == {
        ("wiki/concepts/Stub.md", "health.stub_page"),
        ("wiki/entities/Empty.md", "health.stub_page"),
        ("wiki/concepts/Stub.md", "health.index_missing_page"),
        ("wiki/entities/Empty.md", "health.index_missing_page"),
    }
    assert health_entrypoint(tmp_path, json_output=False, strict=False) == 0
    assert health_entrypoint(tmp_path, json_output=False, strict=True) == 6
