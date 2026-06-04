"""P2 runtime 测试：RunResult 信封解析 + agentao 不在 PATH 的兜底（不打真实 LLM）。"""

import subprocess
from pathlib import Path

from guanlan.runtime import _parse_envelope, run_agent_task


def test_parse_envelope_ok():
    r = _parse_envelope(0, '{"status": "ok", "final_text": "答案"}', "")
    assert r.ok and r.final_text == "答案" and r.error_type is None


def test_parse_envelope_error_type():
    r = _parse_envelope(
        3, '{"status": "error", "error": {"type": "permission_required"}}', ""
    )
    assert not r.ok and r.error_type == "permission_required"


def test_parse_envelope_nonzero_without_error_block():
    r = _parse_envelope(1, '{"status": "ok", "final_text": "x"}', "")
    assert not r.ok and r.error_type == "runtime_error"


def test_parse_envelope_unparsable_stdout():
    r = _parse_envelope(0, "not json", "boom")
    assert not r.ok and r.error_type == "runtime_error" and "boom" in r.final_text


def test_parse_envelope_falls_back_to_error_message():
    """final_text 缺失时用 error.message 作诊断（如 invalid_spec / permission_denied）。"""
    r = _parse_envelope(
        3,
        '{"status": "error", "error": {"type": "invalid_spec", "message": "skill not found"}}',
        "",
    )
    assert not r.ok and r.error_type == "invalid_spec"
    assert "skill not found" in r.final_text


def test_status_ok_but_llm_api_error_is_failure():
    """status=ok + 退出码 0，但 final_text 含 `[LLM API error:]` → 仍判失败（不当成功 no-op）。"""
    r = _parse_envelope(
        0, '{"status": "ok", "final_text": "[LLM API error: 401 unauthorized]"}', ""
    )
    assert not r.ok
    assert r.error_type == "runtime_error"
    assert "LLM API error" in r.final_text


def test_missing_agentao_executable_is_runtime_error(tmp_path: Path, monkeypatch):
    """agentao 不在 PATH → subprocess 抛 OSError → 归一为 runtime_error，不抛 traceback。"""

    def boom(*args, **kwargs):
        raise FileNotFoundError("agentao")

    monkeypatch.setattr(subprocess, "run", boom)

    # skills=() 时不触发 skill 兜底；直接走到 subprocess.run 的 OSError 分支。
    r = run_agent_task("q", working_directory=tmp_path, skills=())
    assert not r.ok
    assert r.error_type == "runtime_error"
    assert "PATH" in r.final_text
