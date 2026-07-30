"""P2 runtime 测试：RunResult 信封解析 + agentao 不在 PATH 的兜底 + 空 API key 撞空清洗
（不打真实 LLM）。"""

import os
import subprocess
from pathlib import Path

from guanlan.runtime import (
    _parse_envelope,
    drop_poisoned_api_keys,
    poisoned_api_key_names,
    run_agent_task,
    scrubbed_environ,
)


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


# ───────── 空 API key 撞空清洗（gbrain v0.42.58 反向评审 §2，探针 gbrain #1249）─────────


def test_scrubbed_environ_drops_empty_api_keys_only(monkeypatch):
    """只剔除**空/纯空白**的 `*_API_KEY`；真值、其他空变量、同名非 key 变量一律照传。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # Claude Code 注入的毒值
    monkeypatch.setenv("OPENAI_API_KEY", "   ")  # 纯空白同样是毒值
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real")  # 真值必须留下
    monkeypatch.setenv("SOME_OTHER_VAR", "")  # 空但不是 API key → 不管
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")  # 空但不是 `_API_KEY` 后缀 → 不管

    env = scrubbed_environ()
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["DEEPSEEK_API_KEY"] == "sk-real"
    assert env["SOME_OTHER_VAR"] == ""
    assert env["ANTHROPIC_BASE_URL"] == ""
    # 不动 os.environ（子进程路径换的是传给子进程的副本）。
    assert os.environ["ANTHROPIC_API_KEY"] == ""


def test_subprocess_runner_passes_scrubbed_env(tmp_path: Path, monkeypatch):
    """`agentao run` 子进程拿到的 env **不含**毒空 key、但含真 key 与其余变量（不再裸继承）。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, '{"status":"ok","final_text":"x"}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = run_agent_task("q", working_directory=tmp_path, skills=())

    assert r.ok
    env = captured["env"]
    assert "ANTHROPIC_API_KEY" not in env  # 毒值没进子进程
    assert env["DEEPSEEK_API_KEY"] == "sk-real"  # 真值照传
    assert env.get("PATH")  # 其余环境完整继承（否则 agentao 都找不到）
    assert captured["stdin"] is subprocess.DEVNULL  # 既有姿态不受影响


def test_drop_poisoned_api_keys_is_in_place_and_idempotent(monkeypatch):
    """进程内路径：就地摘除、返回被摘名字、重复调用安全（Web 每建会话都会走一次）。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real")

    assert poisoned_api_key_names() == ["ANTHROPIC_API_KEY"]
    assert drop_poisoned_api_keys() == ["ANTHROPIC_API_KEY"]
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-real"  # 真值不受影响
    assert drop_poisoned_api_keys() == []  # 幂等：第二次无可摘


def test_poisoned_names_never_expose_values(monkeypatch):
    """守「wrapper 不持 API key」：清洗 API 只回**变量名**，任何返回值里都不出现真 key 字样。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-must-not-leak")

    assert "sk-must-not-leak" not in str(poisoned_api_key_names())
    assert "sk-must-not-leak" not in str(drop_poisoned_api_keys())
