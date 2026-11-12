"""#173：一键入库准备阶段的「停止」。

- 转换子进程（及其派生的孙进程）整组结束；目录审校的 agentao 子进程同样；
- 任务记 cancelled（不是 failed）、写锁释放、可立即重新导入；
- CLI 准备阶段第一次 Ctrl+C 即停止。

假转换器是真子进程（替换 skill 的 convert.py），才验证得到「整组结束」。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from guanlan import convert as convert_mod
from guanlan import oneclick, stopscope
from guanlan.errors import EXIT_USAGE
from guanlan.kblock import acquire
from guanlan.oneclick import StopRequested, active_task, iter_tasks, prepare
from guanlan.runtime import _run_in_new_session

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="按 pid 查孙进程存活只在 POSIX 上写")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover
        return True
    # 已退出但尚未被回收的僵尸也算死：看 ps 状态
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def _wait_dead(pids: list[int], timeout: float = 5.0) -> list[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            return []
        time.sleep(0.05)
    return [p for p in pids if _alive(p)]


def _read_pids(pidfile: Path, n: int, timeout: float = 10.0) -> list[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pidfile.exists():
            pids = [int(x) for x in pidfile.read_text().split()]
            if len(pids) >= n:
                return pids
        time.sleep(0.02)
    raise AssertionError("假转换器没写出 pid")


@pytest.fixture
def slow_converter(tmp_path: Path, monkeypatch) -> Path:
    """替换 skill 的 convert.py：派生一个长睡的孙进程，自己也长睡，并把两个 pid 写进 pidfile。"""
    pidfile = tmp_path / "pids.txt"
    script = tmp_path / "fake_convert.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            with open({str(pidfile)!r}, "w") as fh:
                fh.write(f"{{os.getpid()}} {{child.pid}}")
            print("[mineru] 解析中…", file=sys.stderr, flush=True)
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(convert_mod, "_skill_convert_script", lambda: script)
    return pidfile


def _pdf(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "书.pdf"
    src.parent.mkdir()
    src.write_bytes(b"%PDF fake")
    return src


# ── 子进程层 ─────────────────────────────────────────────────────────────────


@posix_only
@pytest.mark.parametrize("with_progress", [False, True])
def test_converter_stop_kills_whole_group(tmp_path: Path, slow_converter: Path, with_progress: bool):
    """CLI（无 progress）与 Web（有 progress）两条路径：停止后子进程与孙进程都结束，抛 StopRequested。"""
    ev = threading.Event()
    lines: list[str] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with stopscope.stop_scope(ev):
                convert_mod.convert_to_markdown(
                    _pdf(tmp_path), stem="书", cwd=tmp_path, progress=lines.append if with_progress else None
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    pids = _read_pids(slow_converter, 2)
    ev.set()
    t.join(5)
    assert not t.is_alive(), "停止后转换没在 5 秒内返回"
    assert len(errors) == 1 and isinstance(errors[0], StopRequested)
    assert _wait_dead(pids) == []


def test_converter_without_scope_unchanged(tmp_path: Path, monkeypatch):
    """不在 stop_scope 内：仍走 subprocess.run，不进新会话（guanlan convert 行为不变）。"""
    seen: dict = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 1, "", "boom")

    monkeypatch.setattr(convert_mod.subprocess, "run", fake_run)
    assert convert_mod._run_converter(["x"], cwd=str(tmp_path), progress=None) == (1, "", "boom")
    assert "start_new_session" not in seen


def test_stop_set_before_start_never_spawns(tmp_path: Path, monkeypatch):
    def boom(*a, **kw):  # pragma: no cover - 不该被调用
        raise AssertionError("停止后不该再起子进程")

    monkeypatch.setattr(convert_mod.subprocess, "Popen", boom)
    ev = threading.Event()
    ev.set()
    with stopscope.stop_scope(ev), pytest.raises(StopRequested):
        convert_mod._run_converter(["x"], cwd=str(tmp_path), progress=None)


@posix_only
def test_agent_subprocess_stop_kills_group(tmp_path: Path):
    """目录审校的 agentao 子进程（`_run_in_new_session`）：停止标志置位即整组结束，stdin 照常送达。"""
    pidfile = tmp_path / "pids.txt"
    code = textwrap.dedent(
        f"""
        import os, subprocess, sys, time
        sys.stdin.read()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open({str(pidfile)!r}, "w").write(f"{{os.getpid()}} {{child.pid}}")
        time.sleep(60)
        """
    )
    ev = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with stopscope.stop_scope(ev):
                _run_in_new_session(
                    [sys.executable, "-c", code], input="spec", capture_output=True, text=True, start_new_session=True
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    pids = _read_pids(pidfile, 2)
    ev.set()
    t.join(5)
    assert not t.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], StopRequested)
    assert _wait_dead(pids) == []


def test_agent_subprocess_in_scope_returns_output(tmp_path: Path):
    """stop_scope 内没人停：轮询等待照样拿全 stdout（input 只送一次、超时重试不丢输出）。"""
    code = "import sys, time; data = sys.stdin.read(); time.sleep(0.6); print(data.upper())"
    with stopscope.stop_scope(threading.Event()):
        res = _run_in_new_session(
            [sys.executable, "-c", code], input="spec", capture_output=True, text=True, start_new_session=True
        )
    assert res.returncode == 0 and res.stdout.strip() == "SPEC"


# ── 任务层 ───────────────────────────────────────────────────────────────────


@posix_only
def test_prepare_stop_cancels_task_and_releases_lock(kb: Path, tmp_path: Path, slow_converter: Path, monkeypatch):
    src = _pdf(tmp_path)
    ev = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with acquire(kb, "test"):
                prepare(kb, src, origin_kind="web", stop=ev)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    pids = _read_pids(slow_converter, 2)
    assert active_task(kb)["state"] == "preparing"
    ev.set()
    t.join(5)
    assert not t.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], StopRequested)
    assert _wait_dead(pids) == []
    assert active_task(kb) is None
    (task,) = iter_tasks(kb)
    assert task["state"] == "cancelled" and "停止" in task["reason"]
    log = (kb / task["log"]).read_text(encoding="utf-8")
    assert "已按要求停止" in log
    # 写锁已释放、可立即重新导入（这次让转换秒回）
    from guanlan.imageio import ConvertResult

    monkeypatch.setattr(
        oneclick, "convert_to_markdown", lambda *a, **k: ConvertResult(markdown="# 书\n\n正文。\n", backend="mineru")
    )
    with acquire(kb, "test"):
        task2 = prepare(kb, src, origin_kind="web", stop=threading.Event())
    assert task2["state"] == "awaiting_confirm"


def test_stop_between_steps(kb: Path, tmp_path: Path, monkeypatch):
    """转换已返回、还没进整理时点停止：步与步之间的检查接住，任务同样记已放弃。"""
    from guanlan.imageio import ConvertResult

    ev = threading.Event()

    def convert(*a, **k):
        ev.set()  # 转换刚结束时用户点了停止
        return ConvertResult(markdown="# 书\n\n正文。\n", backend="mineru")

    monkeypatch.setattr(oneclick, "convert_to_markdown", convert)
    with acquire(kb, "test"), pytest.raises(StopRequested):
        prepare(kb, _pdf(tmp_path), origin_kind="web", stop=ev)
    (task,) = iter_tasks(kb)
    assert task["state"] == "cancelled" and task["stages"]["parse"] == "done"
    assert task["stages"]["normalize"] == "pending"


def test_stop_set_while_queued_creates_no_task(kb: Path, tmp_path: Path):
    ev = threading.Event()
    ev.set()
    with acquire(kb, "test"), pytest.raises(StopRequested):
        prepare(kb, _pdf(tmp_path), origin_kind="web", stop=ev)
    assert iter_tasks(kb) == []


def test_toc_review_stop_is_not_swallowed(kb: Path, tmp_path: Path, monkeypatch):
    """目录审校失败会退回规则解析；但「停止」不是失败，必须穿过去让任务停下。"""
    from guanlan.imageio import ConvertResult

    toc = ["# 书", "", "目录", ""] + [f"第{i}章 标题{i} ...... {i * 10}" for i in range(1, 8)] + [""]
    body = []
    for i in range(1, 8):
        body += [f"第{i}章 标题{i}", "", "正文。" * 50, ""]
    monkeypatch.setattr(
        oneclick, "convert_to_markdown", lambda *a, **k: ConvertResult(markdown="\n".join(toc + body), backend="mineru")
    )
    called = []

    def reviewer(region, draft):
        called.append(1)
        raise StopRequested

    with acquire(kb, "test"), pytest.raises(StopRequested):
        prepare(kb, _pdf(tmp_path), origin_kind="web", toc_reviewer=reviewer, stop=threading.Event())
    assert called, "测试前提：目录被识别、送审"
    (task,) = iter_tasks(kb)
    assert task["state"] == "cancelled"


def test_cli_ctrl_c_during_prepare_cancels(kb: Path, tmp_path: Path, monkeypatch, capsys):
    """CLI 准备阶段第一次 Ctrl+C 即停止：任务记已放弃、锁释放，可立即重新导入。"""

    def convert(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(oneclick, "convert_to_markdown", convert)
    code = oneclick.import_entrypoint(kb, src=str(_pdf(tmp_path)), yes=True, toc_review=False, ingest=lambda r, root: 0)
    assert code == EXIT_USAGE
    assert "已停止准备" in capsys.readouterr().err
    (task,) = iter_tasks(kb)
    assert task["state"] == "cancelled"
    assert active_task(kb) is None
    with acquire(kb, "test"):  # 锁已释放
        pass
