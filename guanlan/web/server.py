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
from .chat import MAX_CONVERSATIONS, configure_agent_log, disable_agent_log

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
    agent_log: bool | None = None,
    session_persist: bool = True,
    mode: str = "read-only",
    reader: bool = False,
    max_conversations: int = MAX_CONVERSATIONS,
) -> int:
    """起本地 Web 宿主，长驻直到 Ctrl-C；正常停服返回 `EXIT_OK`。

    前置 `require_kb_root(writable=True)`（Web 含写入口，要求 raw/wiki/AGENTAO.md/SCHEMA.md
    齐全）与端口探测都可能抛 `GuanlanError(EXIT_USAGE)`，由 CLI 捕获转退出码。
    `agent_log` 把会话 agent 日志像 CLI 那样落 `<kb>/agentao.log`。**三态默认归口在此**（评审 codex P2）：
    省略（`None`）时按 `reader` 取默认——**reader 默认关**（KB 零写契约）/ **非 reader 默认开**；显式
    `True`/`False` 覆盖。公开 `serve` API 自洽，直接调用者无需自己算（CLI 只把 argparse 的
    `None`/`True`/`False` 透传，决策P4.9-15）。开时先**独立写探针**
    `open(<kb>/agentao.log,"a")`：只读挂载/既有日志不可写 → 启动即 `GuanlanError(EXIT_USAGE)`，
    早失败而非启动后写崩（决策P4.9-13；`require_kb_root` 只查存在性、给不了此保证）。
    `session_persist`（默认开，P4.2）把只读问答会话落 `<kb>/.agentao/sessions/` 并跨重启恢复；
    `--no-session-persist` 关，退回 P4 纯内存。
    `mode`（默认 `read-only`，P4.5）定新会话开局姿态；`--mode workspace-write` 起即可写。
    `reader`（P4.9，默认关）= 只读多会话部署：与 `--mode workspace-write` **互斥**（同给 →
    `EXIT_USAGE`）；钳制（裁写路由 + 强制 session_persist=False / read-only）落在 `create_app`。
    `max_conversations`（P4.9-18，默认 100）= 内存会话硬上限；权威校验在 `create_app`，此处另作友好早提示。
    """
    if reader and mode == "workspace-write":
        raise GuanlanError(
            "--reader 与 --mode workspace-write 互斥（只读部署不可起可写姿态）。",
            exit_code=EXIT_USAGE,
        )
    # 友好早提示（决策P4.9-18）：权威校验在 create_app，此处先于 require_kb_root/绑端口给清晰错误。
    if max_conversations < 1:
        raise GuanlanError(
            f"--max-conversations 须 ≥ 1（收到 {max_conversations}）。", exit_code=EXIT_USAGE
        )
    # 三态默认归口（评审 codex P2）：省略 agent_log 时按 reader 取默认——reader 默认关（零写）、
    # 非 reader 默认开。直接调用 serve(reader=True) 即守零写契约，无需 caller 自己传 agent_log=False。
    if agent_log is None:
        agent_log = not reader
    kb = require_kb_root(root, writable=True)
    _ensure_port_free(HOST, port)
    if agent_log:
        # 写权限早失败（决策P4.9-13）：独立 `open(..., "a")` 探针——**不**复用 configure_agent_log
        # 的返回（它命中 `_agent_log_paths` 幂等缓存即早返回、不重开文件，证明不了当前可写）。覆盖
        # 「目录可写但既有 agentao.log 不可写 / ACL / 挂载语义」等 require_kb_root（只查存在性）测不到的情形。
        try:
            with open(kb / "agentao.log", "a", encoding="utf-8"):
                pass
        except OSError as exc:
            raise GuanlanError(
                f"无法写入会话日志 {kb / 'agentao.log'}（{exc}）。"
                "改用 --no-agent-log，或修复目录写权限。",
                exit_code=EXIT_USAGE,
            ) from None
        configure_agent_log(kb)  # chat 会话日志落 <kb>/agentao.log（像 CLI；已 gitignore/不扫描）
    else:
        # 关日志（reader 默认 / --no-agent-log）：摘掉同进程先前 serve(agent_log=True) 可能留下的
        # 进程级 file handler——否则 reader 会话仍续写 agentao.log、破「零字节写入」（评审 codex P2）。
        disable_agent_log()

    app = create_app(
        kb,
        model=model,
        runner=runner,
        session_persist=session_persist,
        mode=mode,
        reader=reader,
        max_conversations=max_conversations,
    )
    if open_browser:
        _open_browser_when_ready(HOST, port)
    # P5.4：后台预热检索缓存，把首搜 ~1 分钟冷算移出用户关键路径（只预热本进程挂载的库，daemon、失败静默）。
    app.state.search_cache.prewarm_async(kb / "wiki")

    # workers=1 硬编码（决策P4-2/P4-5）：内存作业/会话表 + 单写者假设要求单进程单事件循环。
    uvicorn.run(app, host=HOST, port=port, workers=1, log_level="warning")
    return EXIT_OK
