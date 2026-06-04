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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


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
    max_iterations: int = 100,
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
