"""P2 query 测试：只读路径 + --backfill 门禁（不打真实 LLM，见 docs/P2-最小闭环.md §12）。"""

from pathlib import Path

import pytest

from conftest import make_runner, write_page

from guanlan.errors import (
    EXIT_AGENT_ERROR,
    EXIT_CHECK_FAILED,
    EXIT_OK,
    EXIT_RAW_MUTATED,
)
from guanlan.query import run_query


# --- 召回收敛红线（llm_wiki v0.6 反向评审 §2.1）---


def test_query_prompt_has_convergence_rule():
    """CLI query 提示含收敛红线：别重复等价检索、证据足够即答。"""
    from guanlan.query import QUERY_PROMPT

    assert "不要重复等价检索" in QUERY_PROMPT


def test_query_prompt_does_not_read_whole_index():
    """回归：CLI query 提示曾要求「读候选页 + `wiki/index.md`」。大库 index 数十万字，整读会被单次读取上限截断，
    与 skill query 第 2 步「index 只当目录、不整读」冲突。只读与回填两条路径都只许定位、不许整读。"""
    from guanlan.query import _BACKFILL_SUFFIX, QUERY_PROMPT

    assert "+ `wiki/index.md`" not in QUERY_PROMPT
    assert "不要整读" in QUERY_PROMPT and "search_file_content" in QUERY_PROMPT
    assert "不整读 index" in _BACKFILL_SUFFIX


# --- 宿主召回 + 不调 shell（agentao run 遇任何一次拒绝即终止整次运行）---


def _concept(kb: Path, title: str, body: str) -> None:
    p = kb / "wiki" / "concepts" / f"{title}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f'---\ntitle: "{title}"\ntype: concept\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n{body}\n',
        encoding="utf-8",
    )


def test_query_prompt_forbids_shell_and_drops_cli_search():
    """回归：旧提示建议「`guanlan search` CLI」，只读 CLI query 里模型一试 shell 就被硬拒、整次运行终止
    （permission_denied）。提示须明说不要调 shell，且不再把 CLI search 当可用入口。"""
    from guanlan.query import QUERY_PROMPT

    assert "不要调用 `run_shell_command`" in QUERY_PROMPT
    assert "guanlan search" not in QUERY_PROMPT


def test_query_prompt_carries_prefetched_candidates(kb: Path):
    """只读路径：提示里带宿主 BM25 召回的候选页路径（零 LLM，与 `guanlan search` 同一实现）。"""
    _concept(kb, "注意力机制", "注意力机制依赖查询、键与值三组向量。")
    _concept(kb, "无关页", "完全不相干的内容。")
    runner = make_runner(None, final_text="ans")
    assert run_query("注意力机制需要什么前提？", root=kb, runner=runner) == EXIT_OK
    prompt = runner.calls[0]["prompt"]
    assert "`wiki/concepts/注意力机制.md` — 注意力机制" in prompt
    assert "无关页" not in prompt


def test_query_prompt_no_hits_falls_back_to_locate(kb: Path):
    """召回无命中不阻断：退化为「用 search_file_content 定位」的指引，仍然发起问答。"""
    runner = make_runner(None, final_text="ans")
    assert run_query("完全不存在的主题词", root=kb, runner=runner) == EXIT_OK
    prompt = runner.calls[0]["prompt"]
    assert "没有命中" in prompt and "search_file_content" in prompt


def test_query_prefetch_failure_does_not_block(kb: Path, monkeypatch):
    """召回本身抛错（坏页等）也只退化，不让问答失败。"""
    import guanlan.search as search_mod

    def boom(*a, **k):
        raise RuntimeError("坏页")

    monkeypatch.setattr(search_mod, "search_pages", boom)
    runner = make_runner(None, final_text="ans")
    assert run_query("q", root=kb, runner=runner) == EXIT_OK
    assert "没有命中" in runner.calls[0]["prompt"]


def test_query_prompt_question_with_braces(kb: Path):
    """问题里的花括号原样进提示（format 只替换模板占位，不解析问题文本）。"""
    runner = make_runner(None, final_text="ans")
    assert run_query("{candidates} 与 {x} 是什么", root=kb, runner=runner) == EXIT_OK
    assert "{candidates} 与 {x} 是什么" in runner.calls[0]["prompt"]


def test_backfill_prompt_also_prefetched(kb: Path):
    """`--backfill`（workspace-write）同样不能用 shell（要确认 → reject 策略即终止），提示同样带召回。"""
    _concept(kb, "注意力机制", "注意力机制依赖查询、键与值。")
    runner = make_runner(None, final_text="ans")
    run_query("注意力机制", root=kb, backfill=True, runner=runner)
    prompt = runner.calls[0]["prompt"]
    assert "不要调用 `run_shell_command`" in prompt
    assert "`wiki/concepts/注意力机制.md`" in prompt


def test_query_prompt_cache_path_matches_cold(kb: Path):
    """长驻宿主（MCP `query`）传 `CorpusCache` 复用预热语料：提示与 CLI 冷算字节一致。"""
    from guanlan.query import build_query_prompt
    from guanlan.search import CorpusCache

    _concept(kb, "注意力机制", "注意力机制依赖查询、键与值。")
    cold = build_query_prompt(kb, "注意力机制")
    assert "`wiki/concepts/注意力机制.md`" in cold
    assert build_query_prompt(kb, "注意力机制", search_cache=CorpusCache()) == cold


# --- 默认只读路径 ---


def test_query_readonly_prints_answer_no_snapshot(kb: Path, capsys):
    runner = make_runner(None, final_text="带 [[Foo]] 引用的答案")
    rc = run_query("什么是 Foo？", root=kb, runner=runner)
    assert rc == EXIT_OK
    assert "带 [[Foo]] 引用的答案" in capsys.readouterr().out
    # 只读路径透传 read-only 姿态。
    assert runner.calls[0]["permission_mode"] == "read-only"


def test_query_readonly_agent_error(kb: Path):
    rc = run_query(
        "q", root=kb, runner=make_runner(None, ok=False, error_type="runtime_error")
    )
    assert rc == EXIT_AGENT_ERROR


def test_query_readonly_only_needs_wiki(tmp_path: Path):
    """只读路径仅要求 wiki/（无 raw/、AGENTAO.md 也能跑）。"""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    rc = run_query("q", root=tmp_path, runner=make_runner(None, final_text="ans"))
    assert rc == EXIT_OK


# --- --backfill 路径（与 ingest 门禁一致）---


def test_backfill_compliant_synthesis_ok(kb: Path):
    def action(root: Path):
        write_page(root, "wiki/syntheses/q.md", type="synthesis")

    runner = make_runner(action)
    rc = run_query("q", root=kb, backfill=True, runner=runner)
    assert rc == EXIT_OK
    assert runner.calls[0]["permission_mode"] == "workspace-write"


def test_backfill_broken_check_failed(kb: Path):
    """阻断性 frontmatter 违规（bad_type）→ CHECK_FAILED；断链已降级为警告，不再触发。"""
    def action(root: Path):
        p = root / "wiki" / "syntheses" / "q.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '---\ntitle: "T"\ntype: bogus\ntags: []\nsources: []\nlast_updated: 2026-06-03\n---\n\n正文\n',
            encoding="utf-8",
        )

    rc = run_query("q", root=kb, backfill=True, runner=make_runner(action))
    assert rc == EXIT_CHECK_FAILED


def test_backfill_broken_link_is_warning_ok(kb: Path):
    """--backfill 写出断链综述 → 断链作警告、不阻断 → EXIT_OK（决策8）。"""
    def action(root: Path):
        write_page(root, "wiki/syntheses/q.md", type="synthesis", body="[[Ghost]]")

    rc = run_query("q", root=kb, backfill=True, runner=make_runner(action))
    assert rc == EXIT_OK


def test_backfill_mutates_raw_while_ok(kb: Path):
    def action(root: Path):
        write_page(root, "wiki/syntheses/q.md", type="synthesis")
        (root / "raw" / "sneaky.md").write_text("x", encoding="utf-8")

    rc = run_query("q", root=kb, backfill=True, runner=make_runner(action))
    assert rc == EXIT_RAW_MUTATED


def test_backfill_mutates_raw_then_fails(kb: Path):
    def action(root: Path):
        (root / "raw" / "sneaky.md").write_text("x", encoding="utf-8")

    rc = run_query(
        "q",
        root=kb,
        backfill=True,
        runner=make_runner(action, ok=False, error_type="runtime_error"),
    )
    assert rc == EXIT_RAW_MUTATED


def test_backfill_agent_error_clean_raw(kb: Path):
    rc = run_query(
        "q",
        root=kb,
        backfill=True,
        runner=make_runner(None, ok=False, error_type="max_iterations"),
    )
    assert rc == EXIT_AGENT_ERROR


# --- P5.5 --profile：库外画像进系统提示（run spec instructions），不改用户消息与宿主召回 ---


@pytest.fixture
def outside(tmp_path_factory) -> Path:
    """库外目录：`kb` 夹具就是 tmp_path 本身，画像必须放在另一棵临时树里。"""
    return tmp_path_factory.mktemp("outside")


def _profile(d: Path, text: str = "## 回答方式\n先给结论，再列依据。", name: str = "USER.md") -> Path:
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


_SECTION_HEAD = (
    "## 提问者画像\n\n画像仅用于调整切入角度、详略和术语；事实判断和引用以库内资料为准；"
    "如与项目规约或引用要求冲突，以项目规约和引用要求为准。\n\n"
)


def test_query_without_profile_passes_no_instructions(kb: Path):
    """不给 --profile：runner 收不到 instructions（agentao 自己读 AGENTAO.md），提示与现状逐字相同。"""
    from guanlan.query import build_query_prompt

    runner = make_runner(None, final_text="ans")
    assert run_query("注意力机制", root=kb, runner=runner) == EXIT_OK
    assert "instructions" not in runner.calls[0]
    assert runner.calls[0]["prompt"] == build_query_prompt(kb, "注意力机制")


def test_query_profile_goes_to_system_prompt_not_user_message(kb: Path, outside: Path, capsys):
    """带画像：用户消息（问题 + 宿主候选）与不带画像时逐字相同；画像只在 instructions 里，排在项目指令之后。"""
    from agentao.prompts import load_project_instructions

    _concept(kb, "注意力机制", "注意力机制依赖查询、键与值。")
    _concept(kb, "卷积网络", "卷积网络用局部感受野。")
    plain = make_runner(None, final_text="ans")
    run_query("注意力机制", root=kb, runner=plain)
    capsys.readouterr()

    # 画像里的词只命中「卷积网络」页：若画像参与召回，该页会混进候选。
    prof = _profile(outside, "我常做卷积网络与局部感受野相关的工作。")
    runner = make_runner(None, final_text="ans")
    assert run_query("注意力机制", root=kb, profile=prof, runner=runner) == EXIT_OK
    call = runner.calls[0]
    assert call["prompt"] == plain.calls[0]["prompt"]
    assert "`wiki/concepts/卷积网络.md`" not in call["prompt"]
    assert "提问者画像" not in call["prompt"]
    assert call["permission_mode"] == "read-only"
    assert call["instructions"] == (
        load_project_instructions(kb).rstrip() + "\n\n" + _SECTION_HEAD + "我常做卷积网络与局部感受野相关的工作。"
    )
    assert capsys.readouterr().out == "ans\n"  # stdout 只有答案，没有加载提示


def test_query_profile_keeps_agentao_md_without_frontmatter(kb: Path, outside: Path):
    """项目指令照 agentao 的读法：AGENTAO.md 去 frontmatter 后整段在前。"""
    (kb / "AGENTAO.md").write_text("---\nx: 1\n---\n# 库规约\n引用要带 [[页]]。\n", encoding="utf-8")
    runner = make_runner(None, final_text="ans")
    run_query("q", root=kb, profile=_profile(outside), runner=runner)
    ins = runner.calls[0]["instructions"]
    assert ins.startswith("# 库规约\n引用要带 [[页]]。\n\n## 提问者画像\n\n")
    assert "x: 1" not in ins


def test_query_profile_falls_back_to_agents_md(tmp_path_factory, outside: Path):
    """没有 AGENTAO.md、有 AGENTS.md：带画像也不能丢掉库级规约（instructions 顶替了 agentao 的读盘）。"""
    root = tmp_path_factory.mktemp("kb_agents")
    (root / "wiki").mkdir()
    (root / "wiki" / "index.md").write_text("# 索引\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# 通用规约\n只读作答。\n", encoding="utf-8")
    runner = make_runner(None, final_text="ans")
    assert run_query("q", root=root, profile=_profile(outside), runner=runner) == EXIT_OK
    assert runner.calls[0]["instructions"].startswith("# 通用规约\n只读作答。\n\n## 提问者画像\n\n")


def test_query_profile_without_any_project_file(tmp_path_factory, outside: Path):
    """AGENTAO.md、AGENTS.md 都没有：instructions 只有画像段。"""
    root = tmp_path_factory.mktemp("kb_bare")
    (root / "wiki").mkdir()
    (root / "wiki" / "index.md").write_text("# 索引\n", encoding="utf-8")
    runner = make_runner(None, final_text="ans")
    run_query("q", root=root, profile=_profile(outside, "画像正文"), runner=runner)
    assert runner.calls[0]["instructions"] == _SECTION_HEAD + "画像正文"


def _assert_profile_rejected(kb: Path, profile, capsys, needle: str):
    from guanlan.errors import EXIT_USAGE

    runner = make_runner(None, final_text="ans")
    assert run_query("q", root=kb, profile=profile, runner=runner) == EXIT_USAGE
    assert runner.calls == []
    assert needle in capsys.readouterr().err


def test_query_profile_missing(kb: Path, outside: Path, capsys):
    _assert_profile_rejected(kb, outside / "nope.md", capsys, "不存在")


def test_query_profile_not_a_file(kb: Path, outside: Path, capsys):
    _assert_profile_rejected(kb, outside, capsys, "不是普通文件")


def test_query_profile_inside_kb(kb: Path, capsys):
    p = kb / "workspace" / "USER.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("画像", encoding="utf-8")
    _assert_profile_rejected(kb, p, capsys, "知识库目录内")


def test_query_profile_symlink_into_kb(kb: Path, outside: Path, capsys):
    """库外的符号链接指向库内文件：按解析后的真实路径判，照样拒。"""
    target = kb / "workspace" / "USER.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("画像", encoding="utf-8")
    link = outside / "link.md"
    link.symlink_to(target)
    _assert_profile_rejected(kb, link, capsys, "知识库目录内")


def test_query_profile_not_utf8(kb: Path, outside: Path, capsys):
    p = outside / "gbk.md"
    p.write_bytes("画像内容".encode("gbk"))
    _assert_profile_rejected(kb, p, capsys, "UTF-8")


def test_query_profile_empty(kb: Path, outside: Path, capsys):
    _assert_profile_rejected(kb, _profile(outside, "  \n\n "), capsys, "空的")


def test_query_profile_over_limit_not_truncated(kb: Path, outside: Path, capsys):
    """超限报错、不截断；去首尾空白后恰好 1500 字可用。"""
    from guanlan.query import PROFILE_MAX_CHARS

    _assert_profile_rejected(
        kb, _profile(outside, "字" * (PROFILE_MAX_CHARS + 1), "big.md"), capsys, "超过上限"
    )
    runner = make_runner(None, final_text="ans")
    ok = _profile(outside, "\n" + "字" * PROFILE_MAX_CHARS + "\n", "edge.md")
    assert run_query("q", root=kb, profile=ok, runner=runner) == EXIT_OK
    assert runner.calls[0]["instructions"].endswith("字" * PROFILE_MAX_CHARS)


def test_query_profile_with_backfill_api_guard(kb: Path, outside: Path, capsys):
    """直接调 run_query 同时给 backfill 与 profile：兜底拒绝，不调 runner。"""
    from guanlan.errors import EXIT_USAGE

    runner = make_runner(None, final_text="ans")
    rc = run_query("q", root=kb, backfill=True, profile=_profile(outside), runner=runner)
    assert rc == EXIT_USAGE and runner.calls == []


def test_cli_profile_backfill_mutually_exclusive(kb: Path, outside: Path, monkeypatch, capsys):
    """CLI：--profile 与 --backfill 在参数解析时即被拒（退出码 2），不取库级锁、不进 run_query。"""
    import guanlan.cli as cli

    def fail(*a, **k):
        raise AssertionError("不应走到取锁或 run_query")

    monkeypatch.setattr(cli, "run_locked", fail)
    monkeypatch.setattr(cli, "run_query", fail)
    with pytest.raises(SystemExit) as exc:
        cli.main(["-C", str(kb), "query", "q", "--backfill", "--profile", str(_profile(outside))])
    assert exc.value.code == 2
    assert "--profile" in capsys.readouterr().err


def test_cli_profile_passed_through(kb: Path, outside: Path, monkeypatch):
    """CLI：--profile 原样交给 run_query；只读 query 不取锁。"""
    import guanlan.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "run_query", lambda q, **kw: seen.update(kw, q=q) or 0)
    prof = _profile(outside)
    assert cli.main(["-C", str(kb), "query", "q", "--profile", str(prof)]) == 0
    assert seen["profile"] == str(prof) and seen["backfill"] is False


def test_query_profile_utf8_bom_stripped(kb: Path, outside: Path):
    """记事本存的 UTF-8 BOM 不进 instructions。"""
    p = outside / "bom.md"
    p.write_bytes("﻿画像正文".encode("utf-8"))
    runner = make_runner(None, final_text="ans")
    assert run_query("q", root=kb, profile=p, runner=runner) == EXIT_OK
    assert runner.calls[0]["instructions"].endswith(_SECTION_HEAD + "画像正文")
    assert "﻿" not in runner.calls[0]["instructions"]


def test_query_profile_huge_file_rejected_by_size(kb: Path, outside: Path, capsys):
    """远超上限的大文件按字节先拦，不整读。"""
    from guanlan.query import PROFILE_MAX_CHARS

    p = outside / "huge.md"
    p.write_bytes(b"a" * (PROFILE_MAX_CHARS * 4 + 8192))
    _assert_profile_rejected(kb, p, capsys, "过大")



@pytest.mark.parametrize(
    "src, want",
    [
        ("# 姓名：示例用户\n正文", "### 姓名：示例用户\n正文"),
        ("# 甲\n## 乙\n### 丙", "### 甲\n#### 乙\n##### 丙"),
        ("## 甲\n### 乙", "### 甲\n#### 乙"),  # 最浅是 2 级：整体下移 1 级
        ("#### 甲\n##### 乙", "### 甲\n#### 乙"),  # 最浅比 3 级还深：上提到 3 级
        ("# 甲\n###### 深", "### 甲\n###### 深"),  # 最深到 6 级为止
        ("没有标题\n#话题 不是标题", "没有标题\n#话题 不是标题"),
        ("# 甲\n```\n# 代码注释\n```\n## 乙", "### 甲\n```\n# 代码注释\n```\n#### 乙"),
        ("   # 缩进标题\n    # 四格缩进是代码", "   ### 缩进标题\n    # 四格缩进是代码"),
        ("#\n## 乙", "###\n#### 乙"),  # 空标题也是标题
    ],
)
def test_shift_profile_headings(src: str, want: str):
    from guanlan.query import shift_profile_headings

    assert shift_profile_headings(src) == want


def test_query_profile_headings_nested_under_section(kb: Path, outside: Path):
    """注入时画像最浅的标题落到第 3 级，挂在「## 提问者画像」之下。"""
    runner = make_runner(None, final_text="ans")
    run_query("q", root=kb, profile=_profile(outside, "# 姓名：示例用户\n## 回答方式\n先给结论。"), runner=runner)
    assert runner.calls[0]["instructions"].endswith(
        _SECTION_HEAD + "### 姓名：示例用户\n#### 回答方式\n先给结论。"
    )
