"""`serve(...)`：编程式起 uvicorn（P4，见 docs/P4-Web宿主.md §7 决策P4-2/P4-5）。

仅监听 `127.0.0.1`、**强制 `workers=1`**（内存作业表 + 会话表 + 单写者假设要求单进程单
事件循环；多 worker = 多进程 = 状态分裂 + `raw/` 快照被并发写互踩）。默认起服后开浏览器，
`--no-browser` 跳过。端口被占 → `GuanlanError(EXIT_USAGE)`，由 CLI 捕获并提示换端口。
"""

from __future__ import annotations

import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from ..errors import EXIT_OK, EXIT_USAGE, GuanlanError
from ..paths import require_kb_root
from ..runtime import AgentRunner
from .app import create_app
from .chat import configure_agent_log

HOST = "127.0.0.1"  # 红线：绝不 0.0.0.0（决策P4-4，写端口严禁暴露网络）。


def _ensure_port_free(host: str, port: int) -> None:
    """起服前探测端口可用性，被占则抛 `GuanlanError(EXIT_USAGE)` 引导换端口。

    单用户本地工具，这里有极小的 TOCTOU 窗口（探测后到 uvicorn 真正 bind 之间），可接受——
    比起 uvicorn 自己 bind 失败只打日志、不抛异常（进程静默退出），预探测能给出明确退出码。
    """
    # 先校验端口范围：范围外的值（如 99999 / -1）会让 bind() 抛 OverflowError 而非 OSError，
    # 绕过下面的 GuanlanError 路径、向用户吐 traceback。显式校验给出清晰用法错误。
    if not 1 <= port <= 65535:
        raise GuanlanError(
            f"端口须在 1–65535 之间：{port}。换一个端口：`guanlan web --port <N>`。",
            exit_code=EXIT_USAGE,
        )
    # 探测不设 SO_REUSEADDR：检测探针要至少和真实 bind 一样严格，REUSEADDR 反而会放过
    # 一些被占端口（如 TIME_WAIT），削弱本函数"被占即早失败"的承诺。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError as exc:
            raise GuanlanError(
                f"端口 {port} 已被占用（{exc}）。换一个端口：`guanlan web --port <N>`。",
                exit_code=EXIT_USAGE,
            ) from None


def _open_browser_when_ready(host: str, port: int, *, timeout: float = 10.0) -> None:
    """后台守护线程：轮询端口可连后再开浏览器（避免在服务 ready 前打开）。"""

    def _wait_and_open() -> None:
        # 用单调时钟算 deadline，使预算与每次探测耗时无关（否则 connect 超时会让总等待翻倍）。
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.1)
                if probe.connect_ex((host, port)) == 0:
                    webbrowser.open(f"http://{host}:{port}/")
                    return
            time.sleep(0.1)

    threading.Thread(target=_wait_and_open, daemon=True).start()


def serve(
    root: str | Path,
    *,
    port: int = 8765,
    open_browser: bool = True,
    model: str | None = None,
    runner: AgentRunner | None = None,
    agent_log: bool = True,
) -> int:
    """起本地 Web 宿主，长驻直到 Ctrl-C；正常停服返回 `EXIT_OK`。

    前置 `require_kb_root(writable=True)`（Web 含写入口，要求 raw/wiki/AGENTAO.md/SCHEMA.md
    齐全）与端口探测都可能抛 `GuanlanError(EXIT_USAGE)`，由 CLI 捕获转退出码。
    `agent_log`（默认开）把会话 agent 日志像 CLI 那样落 `<kb>/agentao.log`；`--no-agent-log` 关。
    """
    kb = require_kb_root(root, writable=True)
    _ensure_port_free(HOST, port)
    if agent_log:
        configure_agent_log(kb)  # chat 会话日志落 <kb>/agentao.log（像 CLI；已 gitignore/不扫描）

    app = create_app(kb, model=model, runner=runner)
    if open_browser:
        _open_browser_when_ready(HOST, port)

    # workers=1 硬编码（决策P4-2/P4-5）：内存作业/会话表 + 单写者假设要求单进程单事件循环。
    uvicorn.run(app, host=HOST, port=port, workers=1, log_level="warning")
    return EXIT_OK
