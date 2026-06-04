"""P2 测试公共夹具：构造最小知识库 + 可编程 fake AgentRunner（不打真实 LLM）。"""

from pathlib import Path

import pytest

from guanlan.runtime import AgentRunResult

_FM = '---\ntitle: "T"\ntype: {type}\ntags: []\nsources: {sources}\nlast_updated: 2026-06-03\n---\n\n{body}\n'


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """最小但合法的知识库根（满足 require_kb_root 写入口）。"""
    (tmp_path / "AGENTAO.md").write_text("# AGENTAO\n", encoding="utf-8")
    (tmp_path / "SCHEMA.md").write_text("# SCHEMA\n", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    return tmp_path


def write_page(root: Path, relpath: str, *, type="concept", sources="[]", body="正文") -> None:
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_FM.format(type=type, sources=sources, body=body), encoding="utf-8")


def make_runner(action, *, ok=True, final_text="done", error_type=None):
    """构造 fake runner：调用时先跑 `action(root)` 改磁盘，再返回 AgentRunResult。

    同时把收到的关键字参数记到 `runner.calls`，供断言 permission_mode 等透传。
    """
    calls: list[dict] = []

    def runner(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        if action is not None:
            action(kwargs["working_directory"])
        return AgentRunResult(ok=ok, final_text=final_text, error_type=error_type)

    runner.calls = calls
    return runner
