"""Agentao 集成——**唯一** LLM 步骤的落点（P2，见 docs/P2-最小闭环.md §4）。

P2 经 `agentao run` 子进程驱动（不在进程内嵌入 `Agentao(...)`，那留待 P4）。
wrapper 以工作目录 = 知识库根调用：

    agentao run --prompt "<task>" --format json \
                --skill guanlan-wiki \
                --permission-mode <read-only|workspace-write> \
                --interaction-policy reject \
                [--model M] [--max-iterations N]

stdout 是 `RunResult` JSON 信封；字段名是 `error.type`（非 `error.kind`）。
`runner` 可注入以便测试——fake runner 模拟"写 wiki + 返回摘要"，不起子进程、不打真实 LLM。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .paths import count_files_modified_since

# 心跳节拍（秒）——**单一真相源**：CLI 子进程心跳（本模块）/ Web chat SSE（app.py）/ Web 作业心跳
# （jobs.py）共用同一节拍，后两者各以本值起本模块别名（保留独立名便于测试各自 monkeypatch）。
# 子进程运行期每隔这么久在 stderr 同一行原地刷新「仍在运行」（\r 覆盖，仅交互式终端）。
HEARTBEAT_INTERVAL_S = 15.0


@dataclass
class AgentRunResult:
    ok: bool
    final_text: str
    error_type: str | None = None  # 取自信封 error.type 或退出码归一
    raw: dict | None = None  # 原始 RunResult，便于排错


# 测试注入点：签名与 run_agent_task 的内部调用一致（关键字参数）。
AgentRunner = Callable[..., AgentRunResult]

# agentao 把 LLM 调用失败包装成这个标记串塞进 agent 输出（status 仍可能为 ok）。
_LLM_API_ERROR_MARKER = "[LLM API error:"


def run_agent_task(
    prompt: str,
    *,
    working_directory: Path,
    permission_mode: str = "workspace-write",
    skills: tuple[str, ...] = ("guanlan-wiki",),
    model: str | None = None,
    max_iterations: int = 200,
    runner: AgentRunner | None = None,
) -> AgentRunResult:
    """跑一段 prompt、拿结构化结果。`runner is None` 时用默认子进程 runner。"""
    runner = runner or _subprocess_runner
    return runner(
        prompt,
        working_directory=working_directory,
        permission_mode=permission_mode,
        skills=skills,
        model=model,
        max_iterations=max_iterations,
    )


@contextmanager
def _progress_heartbeat(working_directory: Path):
    """Agentao 子进程运行期间，每 `HEARTBEAT_INTERVAL_S` 秒在 stderr 同一行原地刷新（\r 覆盖）存活提示。

    **仅当 stderr 是交互式终端时启用**——管道 / 重定向 / CI / `--json` 消费者一律静默，
    既不污染日志、也保证非交互行为逐字节不变（默认子进程 runner 之外的注入 runner 走不到这）。
    心跳顺带数 `wiki/` 下「自子进程启动后被写过」的文件数，让「还活着」带上真实进展含义：
    长跑的 ingest 不再看着像卡死（决策：A+ 心跳方案，不动子进程协议与快照门禁）。
    """
    if not sys.stderr.isatty():
        yield
        return
    start = time.monotonic()
    start_wall = time.time()  # 用墙钟比对文件 mtime（monotonic 不可比 mtime）
    wiki_dir = working_directory / "wiki"
    stop = threading.Event()
    printed = False  # 是否打过至少一拍——决定收尾要不要补一个换行收束滚动行
    width = 0  # 上一拍行宽（字符数）：用空格补齐覆盖更短一拍的残留，不依赖 ANSI

    def _beat() -> None:
        # wait(interval) 命中超时返回 False → 打一拍；stop.set() 后返回 True → 退出，无忙等。
        nonlocal printed, width
        while not stop.wait(HEARTBEAT_INTERVAL_S):
            line = f"  ⏳ 仍在运行 {int(time.monotonic() - start)}s"
            changed = count_files_modified_since(wiki_dir, start_wall)
            if changed:  # 0 时省略后缀：读路径（query）永远 0、ingest 首拍前也 0
                line += f" · wiki/ 已变动 {changed} 个文件"
            # count_files_modified_since 的 os.walk 可能跑过 join 的 1s 超时；停止信号已在本拍
            # 计算期间到达就丢弃这拍——否则会把无换行的滚动行打到收尾换行之后、与结果摘要撞行。
            if stop.is_set():
                return
            # \r 刷回行首原地覆盖上一拍（同一行滚动计时，不逐行刷屏）；行尾用空格补到上一拍宽度，
            # 盖掉更短一拍（如文件数位数变少）的残留——不走 ANSI \033[K，dumb 终端也不漏转义串。
            print(f"\r{line}{' ' * max(0, width - len(line))}", end="", file=sys.stderr, flush=True)
            width = len(line)
            printed = True

    thread = threading.Thread(target=_beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)
        if printed:  # 收束滚动行：补一个换行，让后续结果从干净的新行开始
            print(file=sys.stderr, flush=True)


def _subprocess_runner(
    prompt: str,
    *,
    working_directory: Path,
    permission_mode: str,
    skills: tuple[str, ...],
    model: str | None,
    max_iterations: int,
) -> AgentRunResult:
    # 安装态下用户库的发现路径里没有 guanlan-wiki skill，首次需要时幂等装到全局
    # （best-effort；放在默认 runner 而非 run_agent_task，注入 runner 的测试不碰全局目录）。
    from .skill import SKILL_NAME, ensure_skill_available

    if SKILL_NAME in skills:
        ensure_skill_available(working_directory)

    cmd = [
        "agentao",
        "run",
        "--prompt",
        prompt,
        "--format",
        "json",
        "--permission-mode",
        permission_mode,
        "--interaction-policy",
        "reject",
        "--max-iterations",
        str(max_iterations),
    ]
    for skill in skills:
        cmd += ["--skill", skill]
    if model:
        cmd += ["--model", model]

    try:
        # capture_output 仍把子进程 stdout（JSON 信封）缓冲到结束；心跳是父进程旁路打到 stderr，
        # 二者互不干扰——既保住信封解析，又在交互式终端给出「还活着」的进展信号。
        with _progress_heartbeat(working_directory):
            proc = subprocess.run(
                cmd,
                cwd=str(working_directory),
                capture_output=True,
                text=True,
                # 我们总是显式传 --prompt；切断继承的 stdin，否则父进程被管道/重定向喂 stdin 时，
                # agentao 会把管道 stdin 当成 run spec，与 --prompt 冲突而拒绝执行（破坏自动化场景）。
                stdin=subprocess.DEVNULL,
            )
    except OSError as exc:
        # agentao 不在 PATH（或无法启动子进程）：归一为运行时错误，遵守退出码契约，
        # 不让 CLI 抛 traceback。常见于只装了 Python 依赖但 scripts 目录未入 PATH。
        return AgentRunResult(
            False,
            f"无法启动 `agentao run`（{exc}）。确认 agentao 已安装且在 PATH 上。",
            error_type="runtime_error",
            raw=None,
        )
    return _parse_envelope(proc.returncode, proc.stdout, proc.stderr)


def _parse_envelope(returncode: int, stdout: str, stderr: str) -> AgentRunResult:
    """把子进程结果归一为 AgentRunResult。stdout 解析失败 → runtime_error（不可信任为成功）。"""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        detail = stderr.strip() or stdout.strip() or "无法解析 agentao run 输出"
        return AgentRunResult(False, detail, error_type="runtime_error", raw=None)
    if not isinstance(data, dict):
        return AgentRunResult(False, stdout.strip(), error_type="runtime_error", raw=None)

    err = data.get("error")
    error_type = err.get("type") if isinstance(err, dict) else None
    final_text = data.get("final_text") or ""
    # 失败信封常把诊断放在 error.message 而非 final_text（如 invalid_spec / permission_denied）：
    # final_text 为空时回退到 error.message，否则真实失败原因会被吞掉、只剩一个类型名。
    if not final_text and isinstance(err, dict):
        final_text = err.get("message") or ""
    ok = returncode == 0 and data.get("status") == "ok"
    # agentao 0.4.8 的 LLM 调用失败可能仍返回 status=ok + 退出码 0，错误只体现在 final_text 的
    # `[LLM API error: …]` 标记里（见 agentao runtime/chat_loop/_runner.py）。据此降级为失败，
    # 否则 ingest 会把"没真正摄入"的 no-op 当成功（既有 wiki 恰好过 check 时尤其危险）。
    if ok and _LLM_API_ERROR_MARKER in final_text:
        ok = False
    if not ok and not error_type:
        error_type = "runtime_error"
    return AgentRunResult(ok=ok, final_text=final_text, error_type=error_type, raw=data)
