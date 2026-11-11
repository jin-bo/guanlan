"""pdf-to-markdown skill `convert.py` 的 MinerU 4 引擎就绪等待（#171）。

自动启动 MinerU 后，doclib 服务先起、本地解析引擎（basic/standard/advanced）要再加载模型；此时提交解析会得到
`quality_tier_unavailable` 并被降到 flash。这里用假 `subprocess.run` 驱动脚本，不需要真装 MinerU。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "pdf-to-markdown" / "scripts" / "convert.py"


@pytest.fixture(scope="module")
def conv():
    spec = importlib.util.spec_from_file_location("skill_convert_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _status(*, mode="managed", healthy=False, starting=False, tiers=(), error=None, restarts=0, running=True):
    return {
        "running": running,
        "parse_server": {
            "local": {
                "mode": mode,
                "healthy": healthy,
                "starting": starting,
                "supported_tiers": list(tiers),
                "error_code": error,
                "error_msg": "boom" if error else None,
                "restart_count": restarts,
                "max_restart_attempts": 3,
            }
        },
    }


READY = _status(healthy=True, tiers=("basic", "standard", "advanced"))


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ({"status": "starting", "pid": 1}, "starting"),  # 服务本身还在启动：status 命令只回这个短对象
        ({"running": False}, "starting"),
        (_status(mode="disabled"), "disabled"),  # 默认配置：本地只有 flash，等也没用
        (_status(mode=None), "disabled"),
        (_status(starting=True), "starting"),
        (_status(healthy=True), "starting"),  # 健康但还没报出档位
        (READY, "ready"),
        (_status(error="engine_crash", restarts=1), "starting"),  # 还会重启
        (_status(error="engine_crash", restarts=3), "failed"),  # 重启次数用完
        (_status(error="engine_crash", restarts=3, starting=True), "starting"),
    ],
)
def test_local_engine_state(conv, status, state):
    assert conv.local_engine_state(status)[0] == state


def test_engine_state_reports_tiers_and_reason(conv):
    assert conv.local_engine_state(READY) == ("ready", ["basic", "standard", "advanced"], "")
    _, _, reason = conv.local_engine_state(_status(error="model_missing"))
    assert reason == "model_missing: boom"


def test_wait_for_engine_polls_until_ready(conv, monkeypatch):
    seq = iter([{"status": "starting"}, _status(starting=True), READY])
    monkeypatch.setattr(conv, "mineru_server_status", lambda: next(seq))
    sleeps: list[float] = []
    state, tiers, _ = conv.wait_for_engine(60, sleep=sleeps.append, clock=lambda: 0.0)
    assert state == "ready" and tiers == ["basic", "standard", "advanced"]
    assert len(sleeps) == 2


def test_wait_for_engine_times_out_with_reason(conv, monkeypatch):
    monkeypatch.setattr(conv, "mineru_server_status", lambda: _status(starting=True))
    now = [0.0]

    def sleep(sec):
        now[0] += 10

    state, _, reason = conv.wait_for_engine(30, sleep=sleep, clock=lambda: now[0])
    assert state == "starting" and "starting" in reason
    assert now[0] == 30


def test_wait_for_engine_stops_at_once_when_disabled(conv, monkeypatch):
    monkeypatch.setattr(conv, "mineru_server_status", lambda: _status(mode="disabled"))
    assert conv.wait_for_engine(60, sleep=lambda s: pytest.fail("不该等"), clock=lambda: 0.0)[0] == "disabled"


def test_mineru_server_status_parses_json_after_notices(conv, monkeypatch):
    out = "notice: something\n" + json.dumps(READY)
    monkeypatch.setattr(conv.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=out))
    assert conv.mineru_server_status() == READY
    monkeypatch.setattr(conv.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="garbage"))
    assert conv.mineru_server_status() == {}


# --------------------------------------------------------------------------- #
# run_mineru_v4 全流程：假 `mineru parse` / `mineru server status`
# --------------------------------------------------------------------------- #


class FakeMineru:
    """按脚本回答 `mineru parse`（parse_results 依次取）和 `mineru server status`（statuses 依次取，取完重复最后一个）。"""

    def __init__(self, parse_results, statuses=()):
        self.parse_results = list(parse_results)
        self.statuses = list(statuses)
        self.calls: list[list[str]] = []

    def run(self, cmd, *a, **k):
        self.calls.append(cmd)
        if cmd[:3] == ["mineru", "server", "status"]:
            st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return SimpleNamespace(returncode=0, stdout=json.dumps(st))
        assert cmd[:2] == ["mineru", "parse"], cmd
        result = self.parse_results.pop(0)
        if "error" not in result:
            out = Path(cmd[cmd.index("-o") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("# 正文\n", encoding="utf-8")
        return SimpleNamespace(returncode=0 if "error" not in result else 1, stdout=json.dumps(result))

    def parse_tiers(self):
        return [c[c.index("--tier") + 1] if "--tier" in c else None for c in self.calls if c[1] == "parse"]

    def status_calls(self):
        return sum(1 for c in self.calls if c[1] == "server")


def _err(code):
    return {"error": {"code": code, "message": f"{code} msg"}}


def _ok(tier):
    return {"parse": {"status": "done", "tier": tier}}


def _args(**kw):
    base = dict(method=None, lang=None, mineru_backend=None, start=None, end=None, wait=60,
                tier=None, force=False, remote=False, engine_wait=120)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def run(conv, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(conv, "ENGINE_POLL_INTERVAL", 0)
    started: list[list[str]] = []
    monkeypatch.setattr(conv, "run_backend", lambda cmd: started.append(cmd) or 0)
    src = tmp_path / "书.pdf"
    src.write_bytes(b"%PDF-1.4")

    def _run(fake: FakeMineru, **kw):
        monkeypatch.setattr(conv.subprocess, "run", fake.run)
        out = conv.run_mineru_v4(src, tmp_path, _args(**kw))
        return out, capsys.readouterr().err, started

    return _run


def test_auto_start_waits_for_engine_then_parses_at_quality(run):
    fake = FakeMineru(
        [_err("server_not_running"), _ok("standard")],
        statuses=[{"status": "starting"}, _status(starting=True), READY],
    )
    out, log, started = run(fake)
    assert started == [["mineru", "server", "start"]]
    assert fake.parse_tiers() == [None, None]  # 没降到 flash
    assert fake.status_calls() == 3
    assert "engine ready: supported tiers=basic,standard,advanced" in log
    assert "[mineru] tier=standard" in log.splitlines()
    assert out.is_file()


def test_running_server_with_loading_engine_waits_and_retries(run):
    fake = FakeMineru([_err("quality_tier_unavailable"), _ok("standard")], statuses=[_status(starting=True), READY])
    _, log, started = run(fake)
    assert started == []
    assert fake.parse_tiers() == [None, None]
    assert "fallback" not in log


def test_disabled_engine_falls_back_to_flash_with_reason(run):
    fake = FakeMineru([_err("server_not_running"), _err("quality_tier_unavailable"), _ok("flash")],
                      statuses=[_status(mode="disabled")])
    _, log, _ = run(fake)
    assert fake.parse_tiers() == [None, None, "flash"]
    assert "parse_server.local.mode=disabled" in log
    assert "fallback: retrying with --tier flash" in log
    assert "[mineru] tier=flash" in log.splitlines()


def test_engine_timeout_falls_back_to_flash(run, conv, monkeypatch):
    monkeypatch.setattr(conv, "wait_for_engine", lambda timeout: ("starting", [], "local parse engine is starting"))
    fake = FakeMineru([_err("quality_tier_unavailable"), _ok("flash")])
    _, log, _ = run(fake)
    assert fake.parse_tiers() == [None, "flash"]  # 引擎没起来就不重试原档位
    assert "engine not ready (local parse engine is starting)" in log


def test_engine_wait_zero_skips_status_polling(run):
    fake = FakeMineru([_err("quality_tier_unavailable"), _ok("flash")], statuses=[READY])
    run(fake, engine_wait=0)
    assert fake.status_calls() == 0
    assert fake.parse_tiers() == [None, "flash"]


def test_explicit_flash_never_waits(run):
    fake = FakeMineru([_err("server_not_running"), _ok("flash")], statuses=[READY])
    run(fake, tier="flash")
    assert fake.status_calls() == 0


def test_explicit_quality_tier_waits_but_never_falls_back(run):
    fake = FakeMineru([_err("quality_tier_unavailable")], statuses=[_status(mode="disabled")])
    with pytest.raises(Exception, match="quality_tier_unavailable"):
        run(fake, tier="standard")
    assert fake.parse_tiers() == ["standard"]


def test_remote_never_waits_for_local_engine(run):
    fake = FakeMineru([_err("server_not_running"), _ok("standard")], statuses=[READY])
    run(fake, remote=True)
    assert fake.status_calls() == 0


def test_status_command_failure_is_not_ready(conv, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("mineru", 30)

    monkeypatch.setattr(conv.subprocess, "run", boom)
    assert conv.local_engine_state(conv.mineru_server_status())[0] == "starting"


def test_mineru_pipes_decode_as_utf8(conv, monkeypatch, tmp_path):
    """MinerU 4 的 std 流固定 UTF-8（与 locale 无关）：skill 读它的 stdout 必须按 UTF-8 解，
    否则 Windows（cp1252 / cp936）上 --json 里的中文产物路径解成乱码，被误判「没有产出」而悄悄降级。"""
    seen: list[dict] = []
    fake = FakeMineru([_ok("standard")], statuses=[READY])

    def run(cmd, *a, **k):
        seen.append(k)
        if cmd[1] == "--version":
            return SimpleNamespace(returncode=0, stdout="MinerU version: 4.0.12\n")
        return fake.run(cmd, *a, **k)

    monkeypatch.setattr(conv.subprocess, "run", run)
    src = tmp_path / "书.pdf"
    src.write_bytes(b"%PDF-1.4")
    conv.run_mineru_v4(src, tmp_path, _args())
    conv.mineru_server_status()
    assert conv.mineru_major_version() == 4
    assert len(seen) == 3
    assert all(k.get("encoding") == "utf-8" and k.get("errors") == "replace" for k in seen), seen
    assert not any(k.get("text") for k in seen)


def test_load_dotenv_reads_utf8_regardless_of_locale(conv, tmp_path):
    p = tmp_path / ".env"
    p.write_bytes("﻿# 中文注释\nKEY=值\n".encode("utf-8"))  # 带 BOM 也认
    assert conv.load_dotenv(p) == {"KEY": "值"}
