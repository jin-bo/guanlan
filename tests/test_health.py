"""P3 health 文件级体检测试（零 LLM，见 docs/P3-健康与图谱.md §11）。"""

import os
from pathlib import Path

import pytest

from guanlan.health import (
    OVERVIEW_ADVISORY_CHARS,
    STUB_MIN_CHARS,
    health_entrypoint,
    run_health,
)

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


# ---------- index 链接与 HTML 注释 ----------


def test_commented_index_link_is_not_dangling(tmp_path: Path):
    """注释里的 index 链接不是收录项，不该报 index_dangling。"""
    wiki = tmp_path / "wiki"
    _seed_config(
        wiki,
        index="# 索引\n\n<!-- ingest 自动追加：- [<名称>](entities/<Name>.md) — <一句话> -->\n",
    )

    report = run_health(wiki)
    assert "health.index_dangling" not in _kinds(report)


def test_real_index_link_outside_comment_still_dangling(tmp_path: Path):
    """注释外的悬空链接照报——不是把整个 index.md 豁免了。"""
    wiki = tmp_path / "wiki"
    _seed_config(
        wiki,
        index="# 索引\n\n<!-- 注掉 [x](entities/Hidden.md) -->\n- [死页](entities/Dead.md)\n",
    )

    report = run_health(wiki)
    details = [f.detail for f in report.findings if f.kind == "health.index_dangling"]
    assert any("entities/Dead.md" in d for d in details)
    assert not any("entities/Hidden.md" in d for d in details)


def test_freshly_initialized_kb_is_health_clean(tmp_path: Path):
    """**用真模板**跑一遍：全新 `guanlan init` 的库体检必须一条建议都没有。

    这条盯的是"夹具与出厂模板漂移"——本仓库的 index 夹具此前用的是不带链接的
    `<!-- ingest 自动追加 -->`，而真模板带占位链接，于是四条 index_dangling 误报
    一直没被任何测试看见。故此处刻意走 run_init 而非夹具。
    """
    from guanlan.init import run_init

    run_init(tmp_path, today="2026-06-03")

    report = run_health(tmp_path / "wiki")
    assert report.findings == []


def test_commented_out_registration_counts_as_unregistered(tmp_path: Path):
    """注掉的登记行**不算**已收录——index↔磁盘同步的另一个方向，与 dangling 方向同一口径。

    后果是有意的：该页会被报 `index_missing_page`、并被 `reindex` 重新登记。注释不是内容，
    guanlan 没有「已收录但暂时下架」这个状态；想不收录就把页面挪出 `wiki/`。
    """
    wiki = tmp_path / "wiki"
    _seed_config(wiki, index="# 索引\n\n<!-- 暂时下架：- [Foo](entities/Foo.md) — 摘要 -->\n")
    _page(wiki, "entities/Foo.md", title="Foo", type="entity")

    report = run_health(wiki)
    assert "health.index_missing_page" in _kinds(report)
    assert "health.index_dangling" not in _kinds(report)  # 注释行不产生悬空误报


# ---------- overview 体积 ----------


def test_overview_over_advisory_line_is_reported_without_rewrite(tmp_path: Path):
    """超线 → 一条 advisory（记在 overview.md 上）；文件字节不动——只提示，绝不压缩改写。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    big = "# 综述\n" + "字" * OVERVIEW_ADVISORY_CHARS
    (wiki / "overview.md").write_text(big, encoding="utf-8")

    report = run_health(wiki)
    hits = [f for f in report.findings if f.kind == "health.overview_large"]
    assert [f.page for f in hits] == ["wiki/overview.md"]
    assert f"{len(big):,} 字" in hits[0].detail
    assert (wiki / "overview.md").read_text(encoding="utf-8") == big


def test_overview_at_advisory_line_is_quiet(tmp_path: Path):
    """恰在线上不报（`>` 而非 `>=`）；缺 overview 也不报（不归本检查管）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    (wiki / "overview.md").write_text("字" * OVERVIEW_ADVISORY_CHARS, encoding="utf-8")
    assert "health.overview_large" not in _kinds(run_health(wiki))
    (wiki / "overview.md").unlink()
    assert "health.overview_large" not in _kinds(run_health(wiki))


# ---------- source 页无原文（llm_wiki v0.6.13 反向评审 §1.A） ----------

LONG = "实质内容" * 10  # 过桩页线，免得断言被 stub_page 干扰


def _raw(kb: Path, rel: str, text: str = "原文\n") -> None:
    p = kb / "raw" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _orphan_sources(wiki: Path) -> list[str]:
    return sorted(f.page for f in run_health(wiki).findings if f.kind == "health.source_without_raw")


def test_source_page_without_raw_is_reported(tmp_path: Path):
    """被引文献误建的 source 页：raw/ 里没有它 → 报；有原文的同目录页不报。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, "real-report.md")
    _page(wiki, "sources/real-report.md", type="source", body=LONG)
    _page(wiki, "sources/cited-paper.md", type="source", body=LONG)

    assert _orphan_sources(wiki) == ["wiki/sources/cited-paper.md"]


def test_missing_raw_dir_reports_every_source_page(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "sources/a.md", type="source", body=LONG)
    assert _orphan_sources(wiki) == ["wiki/sources/a.md"]


def test_only_sources_dir_is_checked(tmp_path: Path):
    """entities/concepts/syntheses 本就没有 raw 对应，绝不能报。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _page(wiki, "entities/A.md", type="entity", body=LONG)
    _page(wiki, "syntheses/B.md", type="synthesis", body=LONG)
    assert _orphan_sources(wiki) == []


def test_filename_tolerance_matches_find_source_page(tmp_path: Path):
    """文件名一路与 `find_source_page` 同口径：精确 slug、点折横杠的 kebab 形、逐字原名都算有原文。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, "示例 报告.md")  # 空格 → slug `示例-报告`
    _raw(tmp_path, "1.示例报告.md")  # Agent 常把序号点写成横杠
    _raw(tmp_path, "“引号”标题.md")  # 批量导入桩页直接用原文件名
    _page(wiki, "sources/示例-报告.md", type="source", body=LONG)
    _page(wiki, "sources/1-示例报告.md", type="source", body=LONG)
    _page(wiki, "sources/“引号”标题.md", type="source", body=LONG)
    assert _orphan_sources(wiki) == []


def test_never_reports_a_page_find_source_page_would_claim(tmp_path: Path):
    """正反两向一致性：凡 ingest 认作「已收录」的页，health 都不得说它没原文。"""
    from guanlan.rawio import find_source_page

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    names = ["a.b.c", "v1.2 笔记", "“X”-Y", "--dash--", "1.报告"]
    for name in names:
        _raw(tmp_path, f"{name}.md")
        _page(wiki, f"sources/{name.replace('.', '-')}.md", type="source", body=LONG)
    reported = set(_orphan_sources(wiki))
    claimed = {find_source_page(wiki / "sources", name) for name in names} - {None}
    claimed_rel = {p.relative_to(tmp_path).as_posix() for p in claimed}
    every = {p.relative_to(tmp_path).as_posix() for p in (wiki / "sources").glob("*.md")}
    # 双向一致：被认领的页一张不报，没被认领的（`v1-2 笔记` 三种形都对不上）照报——
    # 只断言前半截时，认领失败的名字会让用例形同虚设。
    assert claimed_rel and not (claimed_rel & reported)
    assert reported == every - claimed_rel


def test_raw_in_subdir_and_non_md_original_count(tmp_path: Path):
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, "子目录/示例数据.md")
    _raw(tmp_path, "示例报告.pdf")
    _page(wiki, "sources/示例数据.md", type="source", body=LONG)
    _page(wiki, "sources/示例报告.md", type="source", body=LONG)
    assert _orphan_sources(wiki) == []


def test_hidden_raw_files_do_not_anchor(tmp_path: Path):
    """漏报向：`raw/.x.md` 经 raw_slug 剥首点会变成 `x`；隐藏文件/目录若算原文就会吞掉真孤儿。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, ".x.md")
    _raw(tmp_path, ".cache/y.md")
    _page(wiki, "sources/x.md", type="source", body=LONG)
    _page(wiki, "sources/y.md", type="source", body=LONG)
    assert _orphan_sources(wiki) == ["wiki/sources/x.md", "wiki/sources/y.md"]


def test_nfd_raw_name_matches_nfc_page(tmp_path: Path):
    """Finder 落盘可能是 NFD；APFS 上 ingest 能认领，这里也不得误报。"""
    import unicodedata

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, unicodedata.normalize("NFD", "Café 研究") + ".md")
    _page(wiki, "sources/" + unicodedata.normalize("NFC", "Café-研究") + ".md", type="source", body=LONG)
    assert _orphan_sources(wiki) == []


def _source_with(wiki: Path, rel: str, *, digest: str | None = None, body: str = LONG) -> None:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    extra = f"raw_digest: '{digest}'\n" if digest else ""
    p.write_text(
        "---\ntitle: 'T'\ntype: source\ntags: []\nsources: []\n"
        f"last_updated: 2026-10-30\n{extra}---\n\n{body}\n",
        encoding="utf-8",
    )


def test_renamed_page_anchored_by_raw_digest_or_declaration(tmp_path: Path):
    """改过名的页：`raw_digest` 或「原始资料」声明指向现存 raw 即算有原文。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, "中文原名.md")
    _source_with(wiki, "sources/english-slug.md", digest="raw/中文原名.md@sha256:" + "0" * 64)
    _source_with(wiki, "sources/another.md", body="> 原始资料：`raw/中文原名.md`\n\n" + LONG)
    assert _orphan_sources(wiki) == []


def test_claims_pointing_nowhere_still_reported(tmp_path: Path):
    """漏报向：声明了但原文不在、越界、或声明写在围栏代码里——都不算有原文。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, "exists.md")
    (tmp_path / "secret.md").write_text("x", encoding="utf-8")
    _source_with(wiki, "sources/gone.md", digest="raw/gone.md@sha256:" + "0" * 64)
    _source_with(wiki, "sources/escape.md", digest="raw/../secret.md@sha256:" + "0" * 64)
    _source_with(
        wiki,
        "sources/fenced.md",
        body="```markdown\n> 原始资料：`raw/exists.md`\n```\n\n" + LONG,
    )
    assert _orphan_sources(wiki) == [
        "wiki/sources/escape.md",
        "wiki/sources/fenced.md",
        "wiki/sources/gone.md",
    ]


def test_source_without_raw_ordered_before_content_quality(tmp_path: Path):
    """来源链断裂属数据完整性档，排在桩页之前；仍是建议，默认退 0、--strict 退 6。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki, index="# 索引\n\n## Sources\n- [c](sources/cited.md) — x\n")
    _page(wiki, "sources/cited.md", type="source", body="短")

    kinds = _kinds(run_health(wiki))
    assert kinds.index("health.source_without_raw") < kinds.index("health.stub_page")
    assert health_entrypoint(tmp_path, json_output=False, strict=False) == 0
    assert health_entrypoint(tmp_path, json_output=False, strict=True) == 6


def test_claim_to_hidden_raw_does_not_anchor(tmp_path: Path):
    """漏报向：与文件名一路同口径——声明 / raw_digest 指向隐藏路径也不算原文。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, ".cache/a.md")
    _raw(tmp_path, ".b.md")
    _source_with(wiki, "sources/x.md", body="> 原始资料：`raw/.cache/a.md`\n\n" + LONG)
    _source_with(wiki, "sources/y.md", digest="raw/.b.md@sha256:" + "0" * 64)
    assert _orphan_sources(wiki) == ["wiki/sources/x.md", "wiki/sources/y.md"]


def test_nfc_claim_matches_nfd_raw_on_any_platform(tmp_path: Path):
    """NFD 落盘的原文、NFC 写的声明：逐字不中时按目录实际文件名的 NFC 形逐段再比（Linux 上走这条退路）。"""
    import unicodedata

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, unicodedata.normalize("NFD", "Données/Café 研究") + ".md")
    nfc = unicodedata.normalize("NFC", "raw/Données/Café 研究.md")
    _source_with(wiki, "sources/renamed.md", body=f"> 原始资料：`{nfc}`\n\n" + LONG)
    _source_with(wiki, "sources/digest.md", digest=f"{nfc}@sha256:" + "0" * 64)
    assert _orphan_sources(wiki) == []


def test_claim_through_symlinked_raw_dir_anchors(tmp_path: Path):
    """声明经 `raw/` 下指向库外的链接目录也算原文——与文件名一路、gate 快照同口径。

    链接名用 NFD、声明写 NFC：Linux 上逐字找不到，走 NFC 逐段退路后仍须跟进链接认出原文。
    """
    import unicodedata

    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / unicodedata.normalize("NFD", "é.md")).write_text("x", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / unicodedata.normalize("NFD", "lïnk")).symlink_to(outside, target_is_directory=True)
    claim = unicodedata.normalize("NFC", "raw/lïnk/é.md")
    _source_with(wiki, "sources/z.md", body=f"> 原始资料：`{claim}`\n\n" + LONG)
    _source_with(wiki, "sources/w.md", digest=f"{claim}@sha256:" + "0" * 64)
    assert _orphan_sources(wiki) == []


def test_claim_lexically_outside_raw_never_anchors(tmp_path: Path):
    """漏报向：放开 realpath 准入后，字面越界（`..`、绝对路径、反斜杠、非 raw/ 前缀）仍一律不算。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    (tmp_path / "raw").mkdir()
    (tmp_path / "secret.md").write_text("x", encoding="utf-8")
    claims = ["raw/../secret.md", str(tmp_path / "secret.md"), "raw\\..\\secret.md", "secret.md"]
    for i, claim in enumerate(claims):
        _source_with(wiki, f"sources/p{i}.md", body=f"> 原始资料：`{claim}`\n\n" + LONG)
    assert _orphan_sources(wiki) == [f"wiki/sources/p{i}.md" for i in range(len(claims))]


def test_raw_under_symlinked_dir_anchors(tmp_path: Path):
    """`raw/` 下指向目录的符号链接是支持的配置（同 gate 快照口径）：其中原文的页不得误报。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    store = tmp_path / "store"
    store.mkdir()
    (store / "linked.md").write_text("x", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "ext").symlink_to(store, target_is_directory=True)
    (tmp_path / "raw" / "loop").symlink_to(tmp_path / "raw", target_is_directory=True)  # 环保护
    _page(wiki, "sources/linked.md", type="source", body=LONG)
    assert _orphan_sources(wiki) == []


def test_subdir_source_page_not_anchored_by_filename(tmp_path: Path):
    """漏报向：`find_source_page` 只认 `sources/<stem>.md`；子目录同名页 ingest 判「未收录」，这里也不得按文件名认领。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, "x.md")
    _page(wiki, "sources/sub/x.md", type="source", body=LONG)
    _source_with(wiki, "sources/sub/y.md", body="> 原始资料：`raw/x.md`\n\n" + LONG)
    assert _orphan_sources(wiki) == ["wiki/sources/sub/x.md"]


# ---------- raw 遍历不完整（gbrain v0.60 反向评审 §2.1：判不了就是未知） ----------


def _walk_findings(wiki: Path) -> list:
    return [f for f in run_health(wiki).findings if f.kind == "health.raw_walk_incomplete"]


def test_dangling_link_in_raw_suppresses_source_without_raw(tmp_path: Path):
    """指向未挂载盘的链接：其中原文的页不得被报「无原文」，改报一条遍历不完整，并说清压下了几条。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "ext").symlink_to(tmp_path / "unmounted", target_is_directory=True)
    (tmp_path / "raw" / "gone.md").symlink_to(tmp_path / "moved.md")
    _page(wiki, "sources/on-the-disk.md", type="source", body=LONG)

    assert _orphan_sources(wiki) == []
    [walk] = _walk_findings(wiki)
    assert walk.page == ""
    assert "`raw/ext`" in walk.detail and "`raw/gone.md`" in walk.detail
    assert "本次不报 1 条" in walk.detail


def test_unreadable_raw_subdir_suppresses_source_without_raw(tmp_path: Path):
    if os.geteuid() == 0:
        pytest.skip("root 无视目录权限")
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    locked = tmp_path / "raw" / "locked"
    locked.mkdir(parents=True)
    (locked / "secret.md").write_text("x", encoding="utf-8")
    _page(wiki, "sources/secret.md", type="source", body=LONG)
    locked.chmod(0)
    try:
        assert _orphan_sources(wiki) == []
        [walk] = _walk_findings(wiki)
        assert "`raw/locked`" in walk.detail
    finally:
        locked.chmod(0o755)


def test_dangling_raw_root_is_incomplete_not_missing(tmp_path: Path):
    """`raw` 本身是断开的链接（整块盘没挂上）≠ 库里没有 raw/：后者照常逐页报。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    (tmp_path / "raw").symlink_to(tmp_path / "unmounted", target_is_directory=True)
    _page(wiki, "sources/a.md", type="source", body=LONG)
    assert _orphan_sources(wiki) == []
    assert len(_walk_findings(wiki)) == 1


def test_incomplete_walk_without_suppressed_findings_is_silent(tmp_path: Path):
    """读不到的位置没压下任何判定时不影响结论，不报（低噪）。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    _raw(tmp_path, "real.md")
    (tmp_path / "raw" / "gone.md").symlink_to(tmp_path / "moved.md")
    _page(wiki, "sources/real.md", type="source", body=LONG)
    assert _walk_findings(wiki) == []
    assert _orphan_sources(wiki) == []


def test_hidden_dangling_link_does_not_count(tmp_path: Path):
    """隐藏项本就不算原文，断了也不该把整批判定压下——否则一个 `.DS_Store` 式残留就能让本检查失明。"""
    wiki = tmp_path / "wiki"
    _seed_config(wiki)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / ".cache").symlink_to(tmp_path / "nowhere", target_is_directory=True)
    _page(wiki, "sources/cited.md", type="source", body=LONG)
    assert _orphan_sources(wiki) == ["wiki/sources/cited.md"]
    assert _walk_findings(wiki) == []
