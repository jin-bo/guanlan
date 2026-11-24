"""P2 测试公共夹具：构造最小知识库 + 可编程 fake AgentRunner（不打真实 LLM）。"""

from pathlib import Path

import pytest

from guanlan.runtime import AgentRunResult

_FM = '---\ntitle: "T"\ntype: {type}\ntags: []\nsources: {sources}\nlast_updated: 2026-06-03\n---\n\n{body}\n'


@pytest.fixture(autouse=True)
def _jobs_home(tmp_path_factory, monkeypatch) -> Path:
    """P4.33 后台任务的前像放在库外（默认 `~/.guanlan/jobs/`）：测试一律改到临时目录，不碰真实家目录。"""
    home = tmp_path_factory.mktemp("jobs-home")
    monkeypatch.setenv("GUANLAN_JOBS_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch) -> Path:
    """`~` 一律指向空临时目录（P5.5.1 §4）：CLI query 会认 `~/.guanlan/USER.md`，不隔离的话旧用例会读到
    开发机上的真实画像——作者本机挂、CI（没有画像）却一直绿，看不出来。Windows 的 `Path.home()` 读 `USERPROFILE`。
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


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
