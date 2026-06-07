"""P3.2 heal 测试：worklist（零 LLM）+ 写路径（fake runner）+ 写后回执（见 docs/P3.2-缺失实体物化.md §9）。"""

import json
from pathlib import Path

from conftest import make_runner, write_page

from guanlan.errors import (
    EXIT_CHECK_FAILED,
    EXIT_OK,
    EXIT_RAW_MUTATED,
)
from guanlan.heal import compute_worklist, run_heal
from guanlan.lint import run_lint
from guanlan.runtime import AgentRunResult

# 带 aliases 的 entity 页模板（用于"规范标题 + 别名收编"场景）。
FM_ALIAS = (
    "---\ntitle: '{title}'\ntype: entity\ntags: []\nsources: []\n"
    "aliases: {aliases}\nlast_updated: 2026-06-03\n---\n\n{body}\n"
)


def _ref(kb: Path, name: str, *targets: str) -> None:
    """在 wiki/concepts/<name>.md 写一页，正文引用给定 [[target]]。"""
    body = "见 " + "、".join(f"[[{t}]]" for t in targets)
    write_page(kb, f"wiki/concepts/{name}.md", body=body)


# ── worklist（零 LLM）─────────────────────────────────────────────────────


def test_worklist_matches_lint_missing_entity(kb: Path):
    """默认阈值下 worklist 与 lint.missing_entity 同源逐项相等（共用聚合访问器）。"""
    _ref(kb, "a", "大模型", "GPT")
    _ref(kb, "b", "大模型", "gpt")
    _ref(kb, "c", "孤词")  # 单页引用 → 低于阈值，不入选

    wl = compute_worklist(kb / "wiki")
    lint_targets = {
        f.detail.split("[[", 1)[1].split("]]", 1)[0]
        for f in run_lint(kb / "wiki").findings
        if f.kind == "lint.missing_entity"
    }
    assert {w.target for w in wl} == lint_targets == {"大模型", "gpt"}
    assert "孤词" not in {w.target for w in wl}


def test_worklist_ref_pages_and_sort(kb: Path):
    """引用页清单 == 真实来源页；按 (频次降序, target 升序) 稳定排序。"""
    _ref(kb, "a", "大模型", "gpt")
    _ref(kb, "b", "大模型", "gpt")
    _ref(kb, "c", "大模型")  # 大模型 3 页、gpt 2 页 → 大模型 在前

    wl = compute_worklist(kb / "wiki")
    assert [w.target for w in wl] == ["大模型", "gpt"]
    big = wl[0]
    assert big.ref_count == 3
    assert big.ref_pages == (
        "wiki/concepts/a.md",
        "wiki/concepts/b.md",
        "wiki/concepts/c.md",
    )


def test_worklist_min_refs_raises_threshold(kb: Path):
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")  # 2 页
    assert {w.target for w in compute_worklist(kb / "wiki", min_refs=2)} == {"大模型"}
    assert compute_worklist(kb / "wiki", min_refs=3) == []  # 提阈值后退出


def test_worklist_limit_postpones(kb: Path):
    _ref(kb, "a", "大模型", "gpt")
    _ref(kb, "b", "大模型", "gpt")
    wl = compute_worklist(kb / "wiki", limit=1)
    batch = [w for w in wl if not w.postponed]
    postponed = [w for w in wl if w.postponed]
    # 频次相同按 target 升序：ASCII "gpt" < CJK "大模型"，故 gpt 在前。
    assert [w.target for w in batch] == ["gpt"]
    assert [w.target for w in postponed] == ["大模型"]  # 推迟、非静默丢弃


def test_empty_worklist_short_circuits_without_runner(kb: Path):
    """无缺失实体 → 短路 EXIT_OK 且**不调 runner**（零 LLM 成本）。"""
    runner = make_runner(None)
    rc = run_heal(root=kb, runner=runner)
    assert rc == EXIT_OK
    assert runner.calls == []


def test_dry_run_no_runner_no_write(kb: Path):
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    runner = make_runner(lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"))
    rc = run_heal(root=kb, dry_run=True, runner=runner)
    assert rc == EXIT_OK
    assert runner.calls == []  # dry-run 不触 Agentao
    assert not (kb / "wiki" / "entities" / "大模型.md").exists()


def test_dry_run_json_contract(kb: Path, capsys):
    _ref(kb, "a", "大模型", "gpt")
    _ref(kb, "b", "大模型", "gpt")
    run_heal(root=kb, dry_run=True, limit=1, json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert [w["target"] for w in data["worklist"]] == ["gpt"]
    assert [w["target"] for w in data["postponed"]] == ["大模型"]


# ── 写路径（fake runner）──────────────────────────────────────────────────


def test_heal_materializes_entity_resolved(kb: Path):
    """fake runner 把 target 建成 entities/<target>.md → 门禁过、EXIT_OK、回执 resolved。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        write_page(root, "wiki/entities/大模型.md", type="entity")

    runner = make_runner(action)
    rc = run_heal(root=kb, runner=runner)
    assert rc == EXIT_OK
    assert "大模型" in runner.calls[0]["prompt"]  # 目标随 prompt 喂给 skill
    assert (kb / "wiki" / "entities" / "大模型.md").is_file()


def test_heal_receipt_resolved_json(kb: Path, capsys):
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    runner = make_runner(lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"))
    run_heal(root=kb, runner=runner, json_output=True)
    data = json.loads(capsys.readouterr().out)
    r = data["receipts"][0]
    assert r["target"] == "大模型"
    assert r["status"] == "resolved"
    assert r["resolved_to"] == "wiki/entities/大模型.md"
    assert r["created_path"] == "wiki/entities/大模型.md"
    assert data["unexpected_writes"] == []
    assert data["exit_code"] == EXIT_OK


def test_heal_canonical_title_with_aliases_resolved(kb: Path, capsys):
    """建**新**的规范标题页（stem≠target）+ aliases 收编原键 → resolved，且新页不进 unexpected_writes。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        p = root / "wiki" / "entities" / "大语言模型.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            FM_ALIAS.format(title="大语言模型", aliases="['大模型']", body="定义"),
            encoding="utf-8",
        )

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    r = data["receipts"][0]
    assert r["status"] == "resolved"
    assert r["resolved_to"] == "wiki/entities/大语言模型.md"  # 经别名归到拥有页
    assert data["unexpected_writes"] == []  # 新建页不算越界


def test_heal_stem_mismatch_still_broken(kb: Path, capsys):
    """建了页但 stem≠target、又未 aliases 收编 → 门禁过但回执 still_broken。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    runner = make_runner(lambda root: write_page(root, "wiki/entities/无关.md", type="entity"))
    rc = run_heal(root=kb, runner=runner, json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == EXIT_OK  # 断链是警告、不阻断
    assert data["receipts"][0]["status"] == "still_broken"
    assert data["unexpected_writes"] == []  # 无关.md 是新建 → 不越界


def test_heal_unexpected_write_is_batch_level(kb: Path, capsys):
    """改已有非目标页 → 进批次级 unexpected_writes，且不污染目标 status。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    write_page(kb, "wiki/entities/老页.md", type="entity", body="原内容")

    def action(root: Path):
        write_page(root, "wiki/entities/大模型.md", type="entity")  # 解析目标
        write_page(root, "wiki/entities/老页.md", type="entity", body="被改写")  # 越界改

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert data["receipts"][0]["status"] == "resolved"  # 目标仍按图判定
    assert "wiki/entities/老页.md" in data["unexpected_writes"]


def test_heal_no_false_resolved_when_refs_vanish(kb: Path, capsys):
    """断链边因引用页被删而消失、却无页可指 → 不得误报 resolved（判 still_broken）。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        # 删掉两张引用页：[[大模型]] 不再出现 → 无断链边，但也没建 entities/大模型.md。
        (root / "wiki" / "concepts" / "a.md").unlink()
        (root / "wiki" / "concepts" / "b.md").unlink()

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    r = data["receipts"][0]
    assert r["status"] == "still_broken"  # 无页可指 → 非 resolved
    assert r["resolved_to"] is None
    assert "wiki/concepts/a.md" in data["unexpected_writes"]  # 删除属有害写


def test_heal_delete_is_unexpected(kb: Path, capsys):
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    write_page(kb, "wiki/entities/老页.md", type="entity")

    def action(root: Path):
        (root / "wiki" / "entities" / "老页.md").unlink()  # 删除 → 有害

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/entities/老页.md" in data["unexpected_writes"]


def test_heal_mutates_raw(kb: Path):
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        (root / "raw" / "sneaky.md").write_text("x", encoding="utf-8")

    rc = run_heal(root=kb, runner=make_runner(action))
    assert rc == EXIT_RAW_MUTATED


def test_heal_self_heals_bad_frontmatter(kb: Path):
    """首轮坏 frontmatter → 自愈轮修好 → EXIT_OK、回执 resolved。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    calls = {"n": 0}

    def runner(prompt, **kwargs):
        root = kwargs["working_directory"]
        calls["n"] += 1
        p = root / "wiki" / "entities" / "大模型.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        if calls["n"] == 1:  # 坏 type
            p.write_text(
                "---\ntitle: 'T'\ntype: bogus\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n",
                encoding="utf-8",
            )
        else:  # 自愈轮：合规
            write_page(root, "wiki/entities/大模型.md", type="entity")
        return AgentRunResult(ok=True, final_text="done")

    rc = run_heal(root=kb, runner=runner)
    assert rc == EXIT_OK
    assert calls["n"] == 2


def test_heal_unfixable_frontmatter_check_failed(kb: Path):
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        p = root / "wiki" / "entities" / "大模型.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntitle: 'T'\ntype: bogus\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n",
            encoding="utf-8",
        )

    rc = run_heal(root=kb, runner=make_runner(action), json_output=True)
    assert rc == EXIT_CHECK_FAILED  # 改动留盘
    assert (kb / "wiki" / "entities" / "大模型.md").is_file()


def test_heal_gate_failure_no_false_resolved(kb: Path, capsys):
    """门禁未过（坏 frontmatter 留盘）→ 不得据磁盘同名页报 resolved；判 still_broken。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        p = root / "wiki" / "entities" / "大模型.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(  # 坏 type：会阻断、自愈耗尽后 CHECK_FAILED 留盘
            "---\ntitle: 'T'\ntype: bogus\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n",
            encoding="utf-8",
        )

    rc = run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == EXIT_CHECK_FAILED
    assert (kb / "wiki" / "entities" / "大模型.md").is_file()  # 留盘
    assert data["receipts"][0]["status"] == "still_broken"  # 不因留盘的非法页误报 resolved


def test_heal_new_page_outside_entities_is_unexpected(kb: Path, capsys):
    """heal 只该建 entities/ 页；建到别的目录（即便解析了目标）也算越界写。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    # 写到 concepts/ 而非 entities/——[[大模型]] 仍解析（按 stem），但目录越界。
    runner = make_runner(lambda root: write_page(root, "wiki/concepts/大模型.md", type="concept"))
    run_heal(root=kb, runner=runner, json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert data["receipts"][0]["status"] == "resolved"  # 链接确实解析了
    assert "wiki/concepts/大模型.md" in data["unexpected_writes"]  # 但建错目录 → 越界


def test_heal_destructive_log_rewrite_is_unexpected(kb: Path, capsys):
    """log.md 仅允许追加；截断/改写历史属有害写。"""
    (kb / "wiki" / "log.md").write_text("# 时间线\n## 旧记录\n", encoding="utf-8")
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        write_page(root, "wiki/entities/大模型.md", type="entity")
        (root / "wiki" / "log.md").write_text("全新内容\n", encoding="utf-8")  # 非 append

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/log.md" in data["unexpected_writes"]


def test_heal_log_symlink_swap_is_unexpected(kb: Path, capsys):
    """把 log.md 换成符号链接（即便链接目标内容是 append-only）也算有害——别让审计读穿链接被骗。"""
    (kb / "wiki" / "log.md").write_text("# 时间线\n", encoding="utf-8")
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        write_page(root, "wiki/entities/大模型.md", type="entity")
        log = root / "wiki" / "log.md"
        target = root / "wiki" / "entities" / "_evil.md"  # 内容是"旧 log + 追加"，骗过 startswith
        target.write_text("# 时间线\n## 注入\n", encoding="utf-8")
        log.unlink()
        log.symlink_to(target)

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/log.md" in data["unexpected_writes"]


def test_heal_append_log_is_allowed(kb: Path, capsys):
    """log.md 的正常追加不算越界。"""
    (kb / "wiki" / "log.md").write_text("# 时间线\n", encoding="utf-8")
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        write_page(root, "wiki/entities/大模型.md", type="entity")
        log = root / "wiki" / "log.md"
        log.write_text(log.read_text(encoding="utf-8") + "## [2026-06-06] heal | 大模型\n", encoding="utf-8")

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/log.md" not in data["unexpected_writes"]


def test_heal_tolerates_dangling_symlink_in_wiki(kb: Path):
    """wiki/ 下存在悬空符号链接 .md → _snapshot_wiki 不跟随、不崩（容错指纹）。"""
    (kb / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
    (kb / "wiki" / "entities" / "broken.md").symlink_to(kb / "wiki" / "entities" / "nonexistent-target.md")
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    runner = make_runner(lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"))
    rc = run_heal(root=kb, runner=runner)  # 不抛 OSError
    assert rc == EXIT_OK


def test_heal_json_includes_changed_paths(kb: Path, capsys):
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    runner = make_runner(lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"))
    run_heal(root=kb, runner=runner, json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/entities/大模型.md" in data["changed_paths"]


# ── P3.3：收编既有页 + 安全别名收编窄缝（fake runner）────────────────────────


def _write_alias_page(root: Path, relpath: str, title: str, aliases: list[str], body="定义") -> None:
    """写一张带 aliases 的 entity/concept 页（标题/正文可控，供收编场景前置/动作复用）。"""
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM_ALIAS.format(title=title, aliases=repr(list(aliases)), body=body), encoding="utf-8")


def test_heal_collect_into_existing_entity_resolved(kb: Path, capsys):
    """C 模式：向已有 entity 页 aliases 末尾纯追加本批目标 → resolved、created_path=None、不计越界。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    _write_alias_page(kb, "wiki/entities/大语言模型.md", "大语言模型", [])

    def action(root: Path):
        _write_alias_page(root, "wiki/entities/大语言模型.md", "大语言模型", ["大模型"])

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    r = data["receipts"][0]
    assert r["status"] == "resolved"
    assert r["resolved_to"] == "wiki/entities/大语言模型.md"
    assert r["created_path"] is None  # 收编非新建
    assert data["unexpected_writes"] == []


def test_heal_collect_into_existing_concept_resolved(kb: Path, capsys):
    """目录白名单含 concepts/：收编到 concept 页同样 resolved、不计越界（entity/concept 同构）。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    _write_alias_page(kb, "wiki/concepts/LLM.md", "LLM", [])

    def action(root: Path):
        _write_alias_page(root, "wiki/concepts/LLM.md", "LLM", ["大模型"])

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    r = data["receipts"][0]
    assert r["status"] == "resolved"
    assert r["resolved_to"] == "wiki/concepts/LLM.md"
    assert data["unexpected_writes"] == []


def test_heal_collect_body_change_is_unexpected(kb: Path, capsys):
    """追加别名但同时改了正文 → 整处计 unexpected（条件 2：body 须逐字节不变）。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    _write_alias_page(kb, "wiki/entities/大语言模型.md", "大语言模型", [])

    def action(root: Path):
        _write_alias_page(root, "wiki/entities/大语言模型.md", "大语言模型", ["大模型"], body="偷改了正文")

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/entities/大语言模型.md" in data["unexpected_writes"]


def test_heal_collect_other_frontmatter_change_is_unexpected(kb: Path, capsys):
    """追加别名、正文不变，但悄改 title → 计 unexpected（snapshot 须存非 aliases frontmatter 指纹，Finding 1）。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    _write_alias_page(kb, "wiki/entities/大语言模型.md", "大语言模型", [])

    def action(root: Path):
        _write_alias_page(root, "wiki/entities/大语言模型.md", "偷改的标题", ["大模型"])

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/entities/大语言模型.md" in data["unexpected_writes"]


def test_heal_collect_piggyback_unrelated_alias_is_unexpected(kb: Path, capsys):
    """夹带无关别名（非本批目标）→ 计 unexpected（条件 3：新增键须全部 ∈ 本批 target，挡 piggyback）。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    _write_alias_page(kb, "wiki/entities/大语言模型.md", "大语言模型", [])

    def action(root: Path):
        _write_alias_page(root, "wiki/entities/大语言模型.md", "大语言模型", ["大模型", "张三"])

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/entities/大语言模型.md" in data["unexpected_writes"]


def test_heal_collect_reorder_then_append_is_unexpected(kb: Path, capsys):
    """重排既有别名后再追加 → 集合 ⊇ 但非前缀 → 计 unexpected（Finding 1：按有序列表前缀判）。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    _write_alias_page(kb, "wiki/entities/大语言模型.md", "大语言模型", ["LLM", "GPT"])

    def action(root: Path):
        _write_alias_page(root, "wiki/entities/大语言模型.md", "大语言模型", ["GPT", "LLM", "大模型"])

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/entities/大语言模型.md" in data["unexpected_writes"]


def test_heal_collect_delete_original_alias_is_unexpected(kb: Path, capsys):
    """删掉一个原值再加目标（['LLM','LLM']→['LLM','大模型']）→ 非前缀 → 计 unexpected（Finding 1）。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    _write_alias_page(kb, "wiki/entities/大语言模型.md", "大语言模型", ["LLM", "LLM"])

    def action(root: Path):
        _write_alias_page(root, "wiki/entities/大语言模型.md", "大语言模型", ["LLM", "大模型"])

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/entities/大语言模型.md" in data["unexpected_writes"]


def test_heal_collect_symlink_page_is_unexpected(kb: Path, capsys):
    """被收编页 after 是 symlink（meta=None）→ 即便终态像别名增长也计 unexpected（Finding 3）。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    _write_alias_page(kb, "wiki/entities/大语言模型.md", "大语言模型", [])

    def action(root: Path):
        external = root / "external_target.md"
        external.write_text(
            FM_ALIAS.format(title="大语言模型", aliases="['大模型']", body="定义"), encoding="utf-8"
        )
        big = root / "wiki" / "entities" / "大语言模型.md"
        big.unlink()
        big.symlink_to(external)  # 把真实页替换成 symlink，终态伪装成别名增长

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "wiki/entities/大语言模型.md" in data["unexpected_writes"]


def test_heal_index_md_edit_is_allowed(kb: Path, capsys):
    """index.md 纳入允许面：建新页并在 index.md 插行 → index.md 不计越界、仍进 changed_paths。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")

    def action(root: Path):
        write_page(root, "wiki/entities/大模型.md", type="entity")
        idx = root / "wiki" / "index.md"
        idx.write_text(
            idx.read_text(encoding="utf-8") + "\n## Entities\n- [大模型](entities/大模型.md) — 定义\n",
            encoding="utf-8",
        )

    run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert data["receipts"][0]["status"] == "resolved"
    assert "wiki/index.md" not in data["unexpected_writes"]
    assert "wiki/index.md" in data["changed_paths"]


def test_heal_collect_not_exempt_when_gate_fails(kb: Path, capsys):
    """门禁未过（无关新页坏 frontmatter）→ 本该安全的收编也不豁免（_writeset 仅 gate_ok 时移除）。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    _write_alias_page(kb, "wiki/entities/大语言模型.md", "大语言模型", [])

    def action(root: Path):
        # 本身合规的安全收编：
        _write_alias_page(root, "wiki/entities/大语言模型.md", "大语言模型", ["大模型"])
        # 无关的新引入阻断违规（坏 type）→ 自愈耗尽 → CHECK_FAILED：
        bad = root / "wiki" / "entities" / "坏页.md"
        bad.write_text(
            "---\ntitle: 'T'\ntype: bogus\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n",
            encoding="utf-8",
        )

    rc = run_heal(root=kb, runner=make_runner(action), json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == EXIT_CHECK_FAILED
    assert "wiki/entities/大语言模型.md" in data["unexpected_writes"]  # gate_ok=False → 不豁免


def test_heal_tolerates_nonstring_frontmatter_keys(kb: Path):
    """库内存在非字符串/混合类型 YAML frontmatter 键（合法 YAML、load_page 容错）→ snapshot 不崩。

    回归：_fm_fingerprint 对混合类型键做 `json.dumps(sort_keys=True)` 会抛 TypeError，破坏
    「审计不因坏数据中断」（决策P3-8）；键须先 str() 归一。
    """
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    # 顶层整数键 + 布尔键：合法 YAML，load_page 原样返回（不报错）。
    (kb / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
    (kb / "wiki" / "entities" / "怪页.md").write_text(
        "---\ntitle: 怪页\ntype: entity\ntags: []\nsources: []\nlast_updated: 2026-06-03\n"
        "2026: '年份备注'\ntrue: 开关\n---\n正文\n",
        encoding="utf-8",
    )
    runner = make_runner(lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"))
    rc = run_heal(root=kb, runner=runner)  # 不抛 TypeError
    assert rc == EXIT_OK


def test_heal_idempotent_rerun_skips(kb: Path):
    """物化后重跑 → worklist 空、EXIT_OK、不调 runner、现页未被覆盖。"""
    _ref(kb, "a", "大模型")
    _ref(kb, "b", "大模型")
    write_page(kb, "wiki/entities/大模型.md", type="entity")  # 模拟上一轮已物化
    page = kb / "wiki" / "entities" / "大模型.md"
    before = page.read_bytes()

    runner = make_runner(lambda root: write_page(root, "wiki/entities/大模型.md", type="entity"))
    rc = run_heal(root=kb, runner=runner)
    assert rc == EXIT_OK
    assert runner.calls == []  # 已解析、不再入选
    assert page.read_bytes() == before  # 未覆盖
