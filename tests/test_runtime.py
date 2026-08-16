"""P2 runtime 测试：RunResult 信封解析 + agentao 不在 PATH 的兜底 + 空 API key 撞空清洗
+ 信封编码契约（issue #50）（不打真实 LLM）。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import guanlan
from guanlan.runtime import (
    _parse_envelope,
    drop_poisoned_api_keys,
    envelope_child_env,
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


def test_parse_envelope_none_stdout_is_runtime_error():
    """Windows GBK：reader 线程解码猝死 → `proc.stdout is None` → 必须归一为 runtime_error，
    而不是 `json.loads(None)` 的裸 TypeError 把真因盖掉（issue #50）。"""
    r = _parse_envelope(0, None, None)
    assert not r.ok and r.error_type == "runtime_error"
    assert "stdout" in r.final_text  # 有可读诊断，不是空串
    assert r.raw is None


def test_parse_envelope_none_stdout_keeps_stderr_diagnostic():
    """stdout 读失败但 stderr 有料时，优先展示 stderr——原始编码错误才不被兜底文案顶掉。"""
    r = _parse_envelope(1, None, "UnicodeDecodeError: 'gbk' codec can't decode byte 0x8a")
    assert not r.ok and r.error_type == "runtime_error"
    assert "0x8a" in r.final_text


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


# ───────── 信封编码契约（issue #50：Windows GBK locale 下解码 agentao 输出失败）─────────


def test_subprocess_runner_pins_utf8_on_both_ends(tmp_path: Path, monkeypatch):
    """信封是机器协议：父端解码与子端 std 流**同时**钉 UTF-8，两者缺一不可。

    只钉父端会修好 Windows、却打断 POSIX matched-locale（`LANG=zh_CN.GBK` 下子端仍按 GBK 发）；
    只钉子端则 POSIX 修好、Windows 父端仍按 CP936 解。故这条断言必须成对。
    """
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, '{"status":"ok","final_text":"答案"}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = run_agent_task("q", working_directory=tmp_path, skills=())

    assert r.ok
    assert captured["encoding"] == "utf-8"  # 父端：不跟 locale 走
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"  # 子端：同上
    # strict 在 Windows 上不可救——异常死在 subprocess 自己的 reader 线程里，父进程 catch 不到，
    # 只会再次拿到 stdout=None。故 errors 必须是非严格档。
    assert captured["errors"] == "replace"
    # 只钉 std 流：PYTHONUTF8 连 fsencoding（argv/文件名口径）一起改，血溅面过大，刻意不设。
    assert "PYTHONUTF8" not in captured["env"]


def test_envelope_child_env_overrides_user_ioencoding(monkeypatch):
    """用户手设的 `PYTHONIOENCODING=cp936` 会被**覆盖**（非 setdefault）：这条缝是机器协议、
    不是给人看的控制台，编码不可协商。同时毒空 key 清洗照旧生效（两条清洗叠加、互不吃掉）。"""
    monkeypatch.setenv("PYTHONIOENCODING", "cp936")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real")

    env = envelope_child_env()
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert "ANTHROPIC_API_KEY" not in env
    assert env["DEEPSEEK_API_KEY"] == "sk-real"
    assert os.environ["PYTHONIOENCODING"] == "cp936"  # 只换给子进程的副本，不动自己的环境


# 在**非 UTF-8 父端 locale**下真起子进程跑一遍 `run_agent_task` 的驱动脚本。
# 自身 stdout 在该 locale 下就是 ASCII，故结果用默认 `ensure_ascii=True` 转义回传。
_NON_UTF8_DRIVER = r"""
import json, locale, os, sys
from pathlib import Path

sys.path.insert(0, os.environ["GUANLAN_SRC"])
from guanlan.runtime import run_agent_task

r = run_agent_task("q", working_directory=Path(os.environ["GUANLAN_WD"]), skills=())
sys.stdout.write(json.dumps({
    "locale_encoding": locale.getpreferredencoding(False),
    "ok": r.ok,
    "final_text": r.final_text,
}))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only：假 agentao 靠 shebang 可执行")
def test_envelope_round_trips_under_non_utf8_parent_locale(tmp_path: Path):
    """**真管道**回归：父端 locale 非 UTF-8 时，中文信封仍逐字往返（issue #50 的可复现形态）。

    进程内 monkeypatch 造不出这个缺陷——locale 解码档在解释器启动时就定了（同理见 backlog
    §1.③）。故此处真起一个 `LC_ALL=C` + `-X utf8=0` 的解释器（父端 locale 编码 = ASCII，
    与 Windows CP936 同构：**父端 locale 解不动子端发来的 UTF-8**），PATH 上摆一个只吐 UTF-8
    字节的假 agentao。修复前：`text=True` 按 ASCII 解 → `UnicodeDecodeError` 冒出 `subprocess.run`
    （POSIX 形态）→ 驱动非零退出；修复后：父端钉 UTF-8 → 中文原样回来。
    """
    expected = "已摄入《测试文档》，新建 3 页。"
    payload = json.dumps({"status": "ok", "final_text": expected}, ensure_ascii=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agentao"
    # 假 agentao 走 `stdout.buffer` 直发 UTF-8 字节——正是 agentao 真身在钉死编码后的行为，
    # 且脚本源码全 ASCII，不把测试自身的写盘编码也搅进来。
    fake.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write({payload.encode()!r})\n",
        encoding="ascii",
    )
    fake.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["LC_ALL"] = env["LANG"] = "C"
    env["PYTHONCOERCECLOCALE"] = "0"  # 挡掉 PEP 538 把 C locale 强升成 C.UTF-8
    env.pop("PYTHONUTF8", None)  # PEP 540 UTF-8 模式会让整个用例退化成空跑
    env.pop("PYTHONIOENCODING", None)
    env["GUANLAN_SRC"] = str(Path(guanlan.__file__).resolve().parent.parent)
    env["GUANLAN_WD"] = str(tmp_path)

    proc = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", _NON_UTF8_DRIVER],
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"驱动异常退出：\n{proc.stderr}"
    got = json.loads(proc.stdout)
    if "utf" in got["locale_encoding"].lower():  # 造不出非 UTF-8 父端 → 诚实跳过，不静默空跑
        pytest.skip(f"本环境的父端 locale 仍是 {got['locale_encoding']}，无法复现该缺陷")
    assert got["ok"]
    assert got["final_text"] == expected
