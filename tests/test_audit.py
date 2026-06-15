"""P3.7 audit 测试：Layer-1 候选（零 LLM）+ provenance stamp + 写路径（fake runner）+ 分组原子
刷新 / log 行判据 / 本批界定 / slugs 精确匹配（见 docs/P3.7-语义审计.md §9）。"""

import json
from pathlib import Path

from conftest import make_runner, write_page

from guanlan.audit import (
    audit_candidates,
    audit_result_dict,
    run_audit,
    run_audit_result,
)
from guanlan.errors import EXIT_CHECK_FAILED, EXIT_OK
from guanlan.ingest import run_ingest
from guanlan.pages import load_page
from guanlan.provenance import (
    RAW_DIGEST_KEY,
    admit_raw_path,
    compute_raw_digest,
    format_digest_value,
    parse_digest_value,
    stamp_raw_digest,
)

# source 页模板（带合法 frontmatter + raw_digest）。
_SRC_FM = (
    "---\ntitle: '{title}'\ntype: source\ntags: []\nsources: ['{slug}']\n"
    "last_updated: 2026-06-03\nraw_digest: '{digest}'\n---\n\n{body}\n"
)


# ── 夹具辅助 ───────────────────────────────────────────────────────────────


def put_raw(kb: Path, name: str, content: str = "原始内容 v1\n") -> Path:
    p = kb / "raw" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def source_with_digest(
    kb: Path,
    slug: str,
    *,
    raw_name: str | None = None,
    content: str = "原始内容 v1\n",
    body: str = "本摘要页正文，足够长以避免桩页判定。",
    digest: str | None = None,
) -> Path:
    """写 raw/<raw_name> + 带 raw_digest 的 wiki/sources/<slug>.md（指向该 raw 当时指纹）。"""
    raw_name = raw_name or f"{slug}.md"
    p = put_raw(kb, raw_name, content)
    if digest is None:
        digest = format_digest_value(f"raw/{raw_name}", compute_raw_digest(p))
    sp = kb / "wiki" / "sources" / f"{slug}.md"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(
        _SRC_FM.format(title=slug, slug=slug, digest=digest, body=body), encoding="utf-8"
    )
    return sp


def cite(kb: Path, name: str, *slugs: str, dirn: str = "entities", type: str = "entity") -> None:
    """写一页引用给定 source slug（sources:[…]）。"""
    src = "[" + ", ".join(f'"{s}"' for s in slugs) + "]"
    write_page(kb, f"wiki/{dirn}/{name}.md", type=type, sources=src, body="引用若干源的综合正文。")


def drift_raw(kb: Path, raw_name: str, content: str = "改过的内容 v2 完全不同\n") -> None:
    (kb / "raw" / raw_name).write_text(content, encoding="utf-8")


def _section(reviews, *, date="2026-06-15", note="复核漂移源", bad_json=False) -> str:
    lines = [f"\n## [{date}] audit | {note}"]
    for page, slugs, status in reviews:
        if bad_json:
            lines.append(f"- page={page} slugs={list(slugs)} status={status}")
        else:
            lines.append(
                "- "
                + json.dumps(
                    {"page": page, "drifted_slugs": list(slugs), "status": status},
                    ensure_ascii=False,
                )
            )
    return "\n".join(lines) + "\n"


def log_action(reviews, *, extra=None, mode="append", **kw):
    """构造 runner action：可选改页 + 追加一段 audit 留痕到 log.md。

    mode: append（正常）/ rewrite（整盘改写，破坏 append-only）/ two_sections（追加两段 audit）。
    """

    def action(root: Path):
        if extra is not None:
            extra(root)
        log = root / "wiki" / "log.md"
        before = log.read_text(encoding="utf-8")
        sec = _section(reviews, **kw)
        if mode == "append":
            log.write_text(before + sec, encoding="utf-8")
        elif mode == "rewrite":  # 整盘替换 → after 不以 before 开头
            log.write_text(sec.lstrip("\n"), encoding="utf-8")
        elif mode == "two_sections":
            log.write_text(before + sec + sec, encoding="utf-8")

    return action


# ── §3 Layer-1：audit_candidates（零 LLM）──────────────────────────────────


def test_candidates_source_drift_and_propagation(kb: Path):
    """raw 改 → source 页(source-drift) + 所有引该 slug 的页(cites-drifted-source) 全进候选。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    cite(kb, "Y", "rep")
    drift_raw(kb, "rep.md")

    cands = audit_candidates(kb / "wiki", kb / "raw")
    by_page = {c.page: c for c in cands}
    assert by_page["wiki/sources/rep.md"].reason == "source-drift"
    assert by_page["wiki/entities/X.md"].reason == "cites-drifted-source"
    assert by_page["wiki/entities/Y.md"].reason == "cites-drifted-source"
    assert all(c.drifted_slugs == ("rep",) for c in cands)


def test_candidates_no_drift_empty(kb: Path):
    """raw 未动 → 零候选。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    assert audit_candidates(kb / "wiki", kb / "raw") == []


def test_candidates_no_raw_digest_skipped(kb: Path):
    """source 页无 raw_digest（旧页）→ 跳过不报（向后兼容，决策P3.7-5）。"""
    write_page(kb, "wiki/sources/old.md", type="source", sources='["old"]')
    put_raw(kb, "old.md")
    cite(kb, "X", "old")
    assert audit_candidates(kb / "wiki", kb / "raw") == []


def test_candidates_raw_deleted_not_reported(kb: Path):
    """raw 文件被删 → 不在 audit 报（不抢 check 缺源口径，§3）。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    (kb / "raw" / "rep.md").unlink()
    assert audit_candidates(kb / "wiki", kb / "raw") == []


def test_candidates_multi_source_one_drifted(kb: Path):
    """多源页只一源漂移 → drifted_slugs 只列变的那个。"""
    source_with_digest(kb, "a")
    source_with_digest(kb, "b")
    cite(kb, "X", "a", "b")
    drift_raw(kb, "a.md")  # 只动 a

    cands = audit_candidates(kb / "wiki", kb / "raw")
    by_page = {c.page: c for c in cands}
    assert by_page["wiki/entities/X.md"].drifted_slugs == ("a",)
    assert "wiki/sources/a.md" in by_page
    assert "wiki/sources/b.md" not in by_page  # b 未漂移


def test_candidates_sorted_stable(kb: Path):
    """候选按 page 升序字节稳定。"""
    source_with_digest(kb, "rep")
    cite(kb, "Z", "rep")
    cite(kb, "A", "rep")
    drift_raw(kb, "rep.md")
    cands = audit_candidates(kb / "wiki", kb / "raw")
    assert [c.page for c in cands] == sorted(c.page for c in cands)


def test_candidates_out_of_bounds_raw_digest_skipped(kb: Path):
    """raw_digest 路径越界/绝对 → audit 当无信号跳过（决策P3.7-12，不崩、不抢 check 缺源）。"""
    for bad in ("/etc/passwd@sha256:" + "0" * 64, "../secret.md@sha256:" + "0" * 64):
        write_page(kb, "wiki/sources/s.md", type="source", sources='["s"]')
        sp = kb / "wiki" / "sources" / "s.md"
        sp.write_text(
            _SRC_FM.format(title="s", slug="s", digest=bad, body="正文足够长避免桩页判定。"),
            encoding="utf-8",
        )
        cite(kb, "X", "s")
        assert audit_candidates(kb / "wiki", kb / "raw") == []


# ── provenance 单元（stamp 安全 / admit / parse）────────────────────────────


def test_admit_raw_path(kb: Path):
    put_raw(kb, "ok.md")
    assert admit_raw_path(kb, "raw/ok.md") is not None
    assert admit_raw_path(kb, "/etc/passwd") is None  # 绝对
    assert admit_raw_path(kb, "../raw/ok.md") is None  # 越界
    assert admit_raw_path(kb, "raw/../wiki/index.md") is None  # 塌出 raw/
    assert admit_raw_path(kb, "wiki/index.md") is None  # 不以 raw/ 起
    assert admit_raw_path(kb, "raw\\ok.md") is None  # 反斜杠
    assert admit_raw_path(kb, "raw") is None  # 指向 raw/ 目录本身


def test_admit_raw_path_symlink_escape(kb: Path, tmp_path: Path):
    """raw/link → 库外目录，经它的路径 realpath 逃逸 → None（决策P3.7-12）。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("x", encoding="utf-8")
    (kb / "raw" / "link").symlink_to(outside, target_is_directory=True)
    assert admit_raw_path(kb, "raw/link/secret.md") is None


def test_parse_digest_value():
    sha = "a" * 64
    assert parse_digest_value(f"raw/foo.md@sha256:{sha}") == ("raw/foo.md", sha)
    assert parse_digest_value("no-marker") is None
    assert parse_digest_value(f"@sha256:{sha}") is None  # 空路径段
    assert parse_digest_value("raw/foo.md@sha256:xyz") is None  # hex 非法
    assert parse_digest_value("raw/foo.md@sha256:" + "a" * 63) is None  # 长度不对
    assert parse_digest_value(123) is None  # 非串


def test_stamp_yaml_safe_roundtrip(kb: Path):
    """raw 路径含 ' / : / 空格 → stamp 后 frontmatter 可重解析、值往返一致（验证不裸拼，决策P3.7-9）。"""
    source_with_digest(kb, "rep")
    sp = kb / "wiki" / "sources" / "rep.md"
    value = format_digest_value("raw/it's a doc: v1.md", "b" * 64)
    assert stamp_raw_digest(sp, value) is True
    meta, _ = load_page(sp)
    assert meta[RAW_DIGEST_KEY] == value  # 往返一致


def test_stamp_idempotent(kb: Path):
    source_with_digest(kb, "rep")
    sp = kb / "wiki" / "sources" / "rep.md"
    value = format_digest_value("raw/rep.md", "c" * 64)
    assert stamp_raw_digest(sp, value) is True
    before = sp.read_text(encoding="utf-8")
    assert stamp_raw_digest(sp, value) is True  # 幂等
    assert sp.read_text(encoding="utf-8") == before  # 零写盘（字节不变）


def test_stamp_rollback_on_broken_frontmatter(kb: Path):
    """目标 source 页 frontmatter 预先损坏 → stamp 跳过、页字节不变（不留坏页，决策P3.7-9）。"""
    sp = kb / "wiki" / "sources" / "bad.md"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("---\ntitle: 'x'\n  bad: [unclosed\n---\n\n正文\n", encoding="utf-8")
    before = sp.read_text(encoding="utf-8")
    assert stamp_raw_digest(sp, format_digest_value("raw/x.md", "d" * 64)) is False
    assert sp.read_text(encoding="utf-8") == before


def test_stamp_skips_symlink(kb: Path):
    source_with_digest(kb, "rep")
    real = kb / "wiki" / "sources" / "rep.md"
    link = kb / "wiki" / "sources" / "link.md"
    link.symlink_to(real)
    assert stamp_raw_digest(link, format_digest_value("raw/rep.md", "e" * 64)) is False


# ── wrapper stamp（ingest 后初 stamp，决策P3.7-3a）──────────────────────────


def test_ingest_stamps_raw_digest(kb: Path):
    """ingest raw/X.md 成功后 → wiki/sources/X.md 被写入 raw_digest = 该 raw 字节 sha256。"""
    put_raw(kb, "X.md", "原始资料\n")

    def action(root: Path):
        write_page(root, "wiki/sources/X.md", type="source", sources='["X"]')

    rc = run_ingest("raw/X.md", root=kb, runner=make_runner(action))
    assert rc == EXIT_OK
    meta, _ = load_page(kb / "wiki" / "sources" / "X.md")
    expected = format_digest_value("raw/X.md", compute_raw_digest(kb / "raw" / "X.md"))
    assert meta[RAW_DIGEST_KEY] == expected


def test_ingest_stamp_does_not_touch_other_source_pages(kb: Path):
    """stamp 只动本次那一张 → 别的 source 页 raw_digest 一字不动（防顺手刷新抹平漂移）。"""
    other = source_with_digest(kb, "other")
    other_before = other.read_text(encoding="utf-8")
    put_raw(kb, "X.md", "原始资料\n")

    def action(root: Path):
        write_page(root, "wiki/sources/X.md", type="source", sources='["X"]')

    run_ingest("raw/X.md", root=kb, runner=make_runner(action))
    assert other.read_text(encoding="utf-8") == other_before


def test_ingest_stamp_skips_when_source_missing(kb: Path):
    """Agent 没建对应 source 页 → ingest 仍 EXIT_OK、不崩（安全退化）。"""
    put_raw(kb, "X.md")

    def action(root: Path):
        write_page(root, "wiki/concepts/Foo.md")  # 没建 sources/X.md

    rc = run_ingest("raw/X.md", root=kb, runner=make_runner(action))
    assert rc == EXIT_OK
    assert not (kb / "wiki" / "sources" / "X.md").exists()


# ── 写路径 / 门禁复用（fake runner）─────────────────────────────────────────


def test_empty_worklist_short_circuits_without_runner(kb: Path):
    """无漂移 → 短路 EXIT_OK 且不调 runner（零 LLM 成本）。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")  # 未漂移
    runner = make_runner(None)
    assert run_audit(root=kb, runner=runner) == EXIT_OK
    assert runner.calls == []


def test_audit_passes_page_guard_true(kb: Path, monkeypatch):
    """断言 audit 调 run_guarded_write_result 时显式 page_guard=True（决策P3.7-4）。"""
    import guanlan.audit as audit_mod

    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    drift_raw(kb, "rep.md")

    captured: dict = {}
    real = audit_mod.run_guarded_write_result

    def spy(root, prompt, **kw):
        captured.update(kw)
        return real(root, prompt, **kw)

    monkeypatch.setattr(audit_mod, "run_guarded_write_result", spy)
    reviews = [("wiki/sources/rep.md", ["rep"], "confirmed"), ("wiki/entities/X.md", ["rep"], "confirmed")]
    run_audit_result(root=kb, runner=make_runner(log_action(reviews)))
    assert captured.get("page_guard") is True


def test_audit_source_regression_blocked(kb: Path):
    """复核中删掉既有 sources 一项 → 被 P2.1 源不回退闸阻断（page_guard=True 守门）。"""
    source_with_digest(kb, "a")
    source_with_digest(kb, "b")
    cite(kb, "X", "a", "b")
    drift_raw(kb, "a.md")

    def drop_source(root: Path):
        write_page(root, "wiki/entities/X.md", type="entity", sources='["b"]')  # 丢 a

    reviews = [
        ("wiki/sources/a.md", ["a"], "confirmed"),
        ("wiki/entities/X.md", ["a"], "confirmed"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews, extra=drop_source)))
    assert run.exit_code == EXIT_CHECK_FAILED  # sources.dropped 阻断
    assert run.result.refreshed_slugs == ()  # 门禁未过 → 不刷新


def test_audit_refresh_and_idempotent(kb: Path):
    """整组复核留痕 → 刷新 raw_digest → 复算候选离开；连跑两次第二次空。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    drift_raw(kb, "rep.md")

    reviews = [
        ("wiki/sources/rep.md", ["rep"], "confirmed"),
        ("wiki/entities/X.md", ["rep"], "confirmed"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)))
    assert run.exit_code == EXIT_OK
    assert run.result.refreshed_slugs == ("rep",)
    # 刷新后复算候选：整组离开。
    assert audit_candidates(kb / "wiki", kb / "raw") == []
    # 连跑第二次：空 worklist 短路、不触 runner。
    runner2 = make_runner(None)
    assert run_audit(root=kb, runner=runner2) == EXIT_OK
    assert runner2.calls == []


def test_group_atomic_partial_not_refreshed(kb: Path):
    """组 = source + A + B；仅 source+A 留痕、B 漏 → 整组不刷新、下次仍在候选（决策P3.7-8/10）。"""
    source_with_digest(kb, "rep")
    cite(kb, "A", "rep")
    cite(kb, "B", "rep")
    drift_raw(kb, "rep.md")

    reviews = [  # 漏 B
        ("wiki/sources/rep.md", ["rep"], "confirmed"),
        ("wiki/entities/A.md", ["rep"], "confirmed"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)))
    assert run.exit_code == EXIT_OK
    assert run.result.refreshed_slugs == ()  # 漏一页 → 整组不刷新
    pages = {c.page for c in audit_candidates(kb / "wiki", kb / "raw")}
    assert pages == {"wiki/sources/rep.md", "wiki/entities/A.md", "wiki/entities/B.md"}


def test_gate_passes_but_missing_log_line_not_refreshed(kb: Path):
    """门禁通过但漏写组内某页 log 行 → 不刷新（验证判据是 log 行、非门禁通过，决策P3.7-10）。"""
    source_with_digest(kb, "rep")
    cite(kb, "A", "rep")
    drift_raw(kb, "rep.md")
    # 只留 source 页、漏 A（门禁仍通过，因 log 追加 + 无坏页）。
    reviews = [("wiki/sources/rep.md", ["rep"], "confirmed")]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)))
    assert run.exit_code == EXIT_OK
    assert run.result.refreshed_slugs == ()


def test_non_json_bullet_voids_batch(kb: Path):
    """段内任一 bullet 非合法 JSON → 任何组都不刷新（整批留候选，决策P3.7-14）。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    drift_raw(kb, "rep.md")
    reviews = [
        ("wiki/sources/rep.md", ["rep"], "confirmed"),
        ("wiki/entities/X.md", ["rep"], "confirmed"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews, bad_json=True)))
    assert run.result.refreshed_slugs == ()


def test_page_with_pipe_in_path_attributed_via_json(kb: Path):
    """page 路径含 | → 仍正确归属（JSON 解析，不靠 | 切分，决策P3.7-14）。"""
    source_with_digest(kb, "rep")
    cite(kb, "a|b", "rep", dirn="concepts", type="concept")
    drift_raw(kb, "rep.md")
    reviews = [
        ("wiki/sources/rep.md", ["rep"], "confirmed"),
        ("wiki/concepts/a|b.md", ["rep"], "flagged"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)))
    assert run.result.refreshed_slugs == ("rep",)


# ── 本批界定（决策P3.7-13）──────────────────────────────────────────────────


def test_old_audit_section_not_misread(kb: Path):
    """log.md 已有同日旧 audit 段含 P 留痕、本次漏写 P → 只看新增 suffix、不误读旧段。"""
    source_with_digest(kb, "rep")
    cite(kb, "A", "rep")
    drift_raw(kb, "rep.md")
    # 预置旧 audit 段（含 A 的旧留痕）。
    log = kb / "wiki" / "log.md"
    log.write_text(
        log.read_text(encoding="utf-8")
        + _section([("wiki/entities/A.md", ["rep"], "confirmed")], date="2026-06-14", note="旧批"),
        encoding="utf-8",
    )
    # 本次只留 source 页、漏 A。
    reviews = [("wiki/sources/rep.md", ["rep"], "confirmed")]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)))
    assert run.result.refreshed_slugs == ()  # 不从旧段误读 A


def test_suffix_not_append_only_voids(kb: Path):
    """suffix 非 append-only（整盘改写 log）→ 任何组都不刷新。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    drift_raw(kb, "rep.md")
    reviews = [
        ("wiki/sources/rep.md", ["rep"], "confirmed"),
        ("wiki/entities/X.md", ["rep"], "confirmed"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews, mode="rewrite")))
    assert run.result.refreshed_slugs == ()


def test_no_audit_section_voids(kb: Path):
    """门禁通过但 Agent 完全没写 audit 段（log 无新增）→ 任何组都不刷新（决策P3.7-13 零段）。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    drift_raw(kb, "rep.md")
    # runner 不动 log.md（无 audit 段）。
    run = run_audit_result(root=kb, runner=make_runner(None))
    assert run.exit_code == EXIT_OK  # 门禁通过（无写、无坏页）
    assert run.result.refreshed_slugs == ()  # 但零留痕 → 不刷新


def test_two_audit_sections_voids(kb: Path):
    """suffix 含 >1 个新 audit 段 → 任何组都不刷新。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    drift_raw(kb, "rep.md")
    reviews = [
        ("wiki/sources/rep.md", ["rep"], "confirmed"),
        ("wiki/entities/X.md", ["rep"], "confirmed"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews, mode="two_sections")))
    assert run.result.refreshed_slugs == ()


# ── slugs 精确匹配（决策P3.7-15）────────────────────────────────────────────


def test_slug_exact_match_partial_slug_not_refreshed(kb: Path):
    """一页 P 同属源 a、b 两组，留痕只写 ["a"] → P 视作未留痕、a 与 b 两组均不刷新。"""
    source_with_digest(kb, "a")
    source_with_digest(kb, "b")
    cite(kb, "P", "a", "b")
    drift_raw(kb, "a.md")
    drift_raw(kb, "b.md")

    reviews = [
        ("wiki/sources/a.md", ["a"], "confirmed"),
        ("wiki/sources/b.md", ["b"], "confirmed"),
        ("wiki/entities/P.md", ["a"], "confirmed"),  # 少写 b
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)), limit=2)
    assert run.result.refreshed_slugs == ()  # P 未留痕 → 两组均不刷新


def test_slug_order_insensitive(kb: Path):
    """slugs 顺序不同但集合相同（["b","a"]）→ 排序后相等、算有效。"""
    source_with_digest(kb, "a")
    source_with_digest(kb, "b")
    cite(kb, "P", "a", "b")
    drift_raw(kb, "a.md")
    drift_raw(kb, "b.md")

    reviews = [
        ("wiki/sources/a.md", ["a"], "confirmed"),
        ("wiki/sources/b.md", ["b"], "confirmed"),
        ("wiki/entities/P.md", ["b", "a"], "confirmed"),  # 乱序
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)), limit=2)
    assert set(run.result.refreshed_slugs) == {"a", "b"}


def test_two_groups_one_complete_one_not(kb: Path):
    """一页同属两源组：一组成员齐全刷新、另一组缺成员不刷新。"""
    source_with_digest(kb, "a")
    source_with_digest(kb, "b")
    cite(kb, "P", "a", "b")  # P 同属 a、b 两组
    drift_raw(kb, "a.md")
    drift_raw(kb, "b.md")

    # 组 a = {sources/a, P}；组 b = {sources/b, P}。漏 sources/b → 只 a 完整。
    reviews = [
        ("wiki/sources/a.md", ["a"], "confirmed"),
        ("wiki/entities/P.md", ["a", "b"], "confirmed"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)), limit=2)
    assert run.result.refreshed_slugs == ("a",)  # 仅 a 整组留痕


# ── --limit 限组数 ─────────────────────────────────────────────────────────


def test_limit_counts_groups(kb: Path):
    """--limit 计的是漂移源组数，不是页数。"""
    source_with_digest(kb, "a")
    source_with_digest(kb, "b")
    cite(kb, "X", "a")
    cite(kb, "Y", "b")
    drift_raw(kb, "a.md")
    drift_raw(kb, "b.md")

    reviews = [
        ("wiki/sources/a.md", ["a"], "confirmed"),
        ("wiki/entities/X.md", ["a"], "confirmed"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)), limit=1)
    assert run.result.refreshed_slugs == ("a",)  # 只选 1 组
    assert {g.slug for g in run.postponed} == {"b"}  # b 组推迟


# ── targets 契约（决策P3.7-11）──────────────────────────────────────────────


def test_format_targets_contract(kb: Path):
    """{targets} 每行含 page | reason | drifted_slugs | raw_paths；source 页 raw 取自自身 raw_digest。"""
    source_with_digest(kb, "rep", raw_name="rep.md")
    cite(kb, "X", "rep")
    drift_raw(kb, "rep.md")

    runner = make_runner(
        log_action(
            [
                ("wiki/sources/rep.md", ["rep"], "confirmed"),
                ("wiki/entities/X.md", ["rep"], "confirmed"),
            ]
        )
    )
    run_audit_result(root=kb, runner=runner)
    prompt = runner.calls[0]["prompt"]
    assert "wiki/sources/rep.md | source-drift | rep | raw/rep.md" in prompt
    assert "wiki/entities/X.md | cites-drifted-source | rep | raw/rep.md" in prompt


# ── dry-run / JSON 契约 ────────────────────────────────────────────────────


def test_dry_run_no_runner_no_write(kb: Path):
    """--dry-run：纯读零 LLM、不触 runner、不 stamp/刷新、必 EXIT_OK。"""
    source_with_digest(kb, "rep")
    sp = kb / "wiki" / "sources" / "rep.md"
    before = sp.read_text(encoding="utf-8")
    cite(kb, "X", "rep")
    drift_raw(kb, "rep.md")

    runner = make_runner(None)
    assert run_audit(root=kb, dry_run=True, runner=runner) == EXIT_OK
    assert runner.calls == []
    assert sp.read_text(encoding="utf-8") == before  # 不刷新指纹


def test_dry_run_json_contract(kb: Path, capsys):
    source_with_digest(kb, "a")
    source_with_digest(kb, "b")
    cite(kb, "X", "a")
    cite(kb, "Y", "b")
    drift_raw(kb, "a.md")
    drift_raw(kb, "b.md")
    run_audit(root=kb, dry_run=True, limit=1, json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert [g["slug"] for g in data["groups"]] == ["a"]
    assert [g["slug"] for g in data["postponed"]] == ["b"]


def test_dry_run_backward_compat_no_digest(kb: Path, capsys):
    """无 raw_digest 的旧库跑 audit --dry-run → 零候选、EXIT_OK。"""
    write_page(kb, "wiki/sources/old.md", type="source", sources='["old"]')
    put_raw(kb, "old.md")
    rc = run_audit(root=kb, dry_run=True)
    assert rc == EXIT_OK
    assert "无漂移源" in capsys.readouterr().out


def test_audit_result_dict_fields(kb: Path):
    """audit_result_dict 字段稳定（refreshed / postponed / receipts / exit_code）。"""
    source_with_digest(kb, "rep")
    cite(kb, "X", "rep")
    drift_raw(kb, "rep.md")
    reviews = [
        ("wiki/sources/rep.md", ["rep"], "confirmed"),
        ("wiki/entities/X.md", ["rep"], "confirmed"),
    ]
    run = run_audit_result(root=kb, runner=make_runner(log_action(reviews)))
    d = audit_result_dict(run.result, run.postponed)
    assert set(d) == {"refreshed", "postponed", "receipts", "exit_code"}
    assert d["refreshed"] == ["rep"]
    assert d["exit_code"] == EXIT_OK
    r = d["receipts"][0]
    assert set(r) == {"slug", "status", "members", "reviewed", "reason"}
    assert r["status"] == "refreshed"
