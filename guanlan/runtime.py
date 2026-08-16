"""Agentao 集成——**唯一** LLM 步骤的落点（P2，见 docs/P2-最小闭环.md §4）。

P2 经 `agentao run` 子进程驱动（不在进程内嵌入 `Agentao(...)`，那留待 P4）。
wrapper 以工作目录 = 知识库根调用：

    agentao run --prompt "<task>" --format json \
                --skill guanlan-wiki \
                --permission-mode <read-only|workspace-write> \
                --interaction-policy reject \
                [--model M] [--max-iterations N]

stdout 是 `RunResult` JSON 信封；字段名是 `error.type`（非 `error.kind`）。信封的编码是
**协议的一部分、固定 UTF-8**，父子两端都显式钉死、不随 locale 变（issue #50，见 `envelope_child_env`）。
`runner` 可注入以便测试——fake runner 模拟"写 wiki + 返回摘要"，不起子进程、不打真实 LLM。
"""

from __future__ import annotations

import json
import os
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


# ───────── 空 API key 撞空清洗（源自 gbrain v0.42.58 反向评审 §2，探针 gbrain #1249）─────────

_POISON_SUFFIX = "_API_KEY"


def _is_poisoned_key(name: str, value: str) -> bool:
    """`*_API_KEY` 且值为空 / 纯空白 → 毒值。空 key 永不承载语义，留着只会盖住真值。"""
    return name.endswith(_POISON_SUFFIX) and not value.strip()


def poisoned_api_key_names(env: dict[str, str] | None = None) -> list[str]:
    """列出环境里的毒空 `*_API_KEY` 变量名（不返回任何值——守「wrapper 不持 API key」）。"""
    items = os.environ if env is None else env
    return sorted(k for k, v in items.items() if _is_poisoned_key(k, v))


def scrubbed_environ() -> dict[str, str]:
    """`os.environ` 的副本，剔除毒空 `*_API_KEY`——喂给 `agentao run` 子进程用。

    **为什么需要**：Claude Code 会给子进程注入 `ANTHROPIC_API_KEY=''` 以掐断子进程的 LLM 调用。
    agentao 侧两处叠加把这个空串变成硬失败：① `embedding/factory.py` 收 key 只判 `is not None`
    （空串照收进 `api_key`，与它自己 docstring 声称的「空/纯空白视作未设」相矛盾）；②
    `_env.py:safe_load_dotenv` 用 `os.environ.setdefault`（no-override），故 `.env` 里的**真** key
    盖不进已被注入空串的变量。于是「provider=Anthropic + 从 Claude Code 会话里跑 guanlan」时，
    `.env` 明明有真 key 却报无 key/空 key（gbrain #1249 同款，本仓 issue 面已实测成立）。

    剔掉毒空值后，agentao 自己的 `safe_load_dotenv` 就能把 `.env` 里的真 key 正常 `setdefault` 进去
    ——**修复靠的是让 agentao 恢复正常发现，不是我们去读 key**。

    **硬约束**：只删空串、**绝不注入或读取任何真 key**（守「脚本零 LLM、wrapper 不持 API key」不变量
    ——删一个毒空值 ≠ 持钥）。与本接缝已有的 `stdin=DEVNULL` 同类：都是「喂给子进程前的环境清洗」。
    """
    return {k: v for k, v in os.environ.items() if not _is_poisoned_key(k, v)}


# ───────── 信封编码契约（issue #50：Windows GBK locale 下解码 agentao 输出失败）─────────

ENVELOPE_ENCODING = "utf-8"
# 管道解码用**非严格**档，理由见 `envelope_child_env` 末段——这不是「怕出错就 replace」的懒惰档，
# 是 Windows 上唯一可控的档：strict 的 UnicodeDecodeError 死在 subprocess 自己的 reader 线程里。
ENVELOPE_ERRORS = "replace"


def envelope_child_env() -> dict[str, str]:
    """喂给 `agentao run` 的环境：`scrubbed_environ()` 之上再**显式钉死子进程 std 流编码为 UTF-8**。

    **为什么需要**（issue #50）：`--format json` 的 stdout 信封是**机器协议**，两端必须约定同一个
    编码；此前两端都没约定，各自跟 locale 走，于是只在「父子 locale 恰好一致」时侥幸成立：

    | 环境 | 子端（agentao）实际编码 | 父端（`text=True`）解码 | 结果 |
    |---|---|---|---|
    | Windows + CP936 | **UTF-8**（agentao `_ensure_utf8()` 只在 win32 强制） | GBK（locale） | ❌ 失配 |
    | POSIX + UTF-8 locale | UTF-8 | UTF-8 | ✅ CI 只覆盖这一格 |
    | POSIX + `LANG=zh_CN.GBK` | GBK（locale） | GBK（locale） | ✅ matched-locale 侥幸 |

    失配那格有**两种**表现，崩溃反而是少数派：中文信封的 UTF-8 字节按 GBK 解，约 1/3 撞上非法序列
    （报告里那条 `UnicodeDecodeError`），另约 2/3 **静默解成乱码且 `json.loads` 照样成功**——
    JSON 骨架全是 ASCII、撑得住，死的只有中文正文。于是 `query` 会以退出码 0 交付一段乱码答案。

    **为什么必须两端一起钉**：只钉父端 `encoding="utf-8"` 会修好 Windows 那格、却打断 POSIX
    matched-locale 那格（子端仍按 GBK 发、父端强解 UTF-8 → 乱码）。这个坑 `convert.py` 已经踩过
    一次并回退，见 docs/backlog/notes/gbrain-v0.42.53-反向审计-guanlan缺陷.md §1.③/§2.4b：
    「须两端协同」。本接缝比 convert 那条好办——对端是 agentao 而非裸 `print` 的 skill 脚本，
    一个环境变量就钉得住。

    **为什么是 `PYTHONIOENCODING` 而不是 `PYTHONUTF8=1`**：后者连 fsencoding（argv/文件名口径）
    一起改，会波及 agentao 对磁盘上非 UTF-8 文件名的读写，血溅面远大于这条协议缝；
    `PYTHONIOENCODING` 只动 std 流，正是要的粒度。用户手工设的 `PYTHONIOENCODING=cp936` 会被
    **覆盖**（非 setdefault）：这条缝是机器协议、不是给人看的控制台，编码不可协商。
    （agentao 侧 `_ensure_utf8()` 用的正是 `setdefault`，故显式传入与它不冲突、且在 POSIX 上补上了
    它主动跳过的那一半。）

    **为什么父端还要 `errors="replace"`**：钉死两端后管道里本该只有合法 UTF-8，`replace` 兜的是
    第三方混入的杂散字节（子进程链上的原生崩溃信息等）。**strict 在 Windows 上不可救**——
    `capture_output` 在 Windows 走 `_readerthread`，解码异常死在 subprocess 内部线程里，父进程
    `try/except` 够不着，只会再次拿到 `proc.stdout is None`（issue #50 报告的 traceback 即此形态）。
    所以「保持 strict、捕获 `UnicodeDecodeError`」这条路在 Windows 上根本走不通。
    """
    env = scrubbed_environ()
    env["PYTHONIOENCODING"] = ENVELOPE_ENCODING
    return env


def drop_poisoned_api_keys() -> list[str]:
    """就地从 `os.environ` 摘掉毒空 `*_API_KEY`，返回被摘的变量名（供日志/测试）。

    进程内嵌入路径（Web 聊天的 `chat.build_from_environment`）用这支：它直接吃我们自己的
    `os.environ`，没有子进程边界可以换 env。**摘除时机必须早于** `build_from_environment`
    ——后者在**调用期**才 `safe_load_dotenv()`，故先摘掉空串，真 key 才 setdefault 得进来。
    幂等；只删空值故无需恢复（毒值本身无意义）。理由与边界见 `scrubbed_environ`。
    """
    dropped = poisoned_api_key_names()
    for name in dropped:
        os.environ.pop(name, None)
    return dropped


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
                # 信封是机器协议：**不跟 locale 走**，父端解码与子端 std 流（下面 env 里的
                # PYTHONIOENCODING）一起钉死 UTF-8；errors 非严格档在 Windows 上是硬需求。
                # 完整理由（含只钉一端为何会回归 POSIX matched-locale）见 `envelope_child_env`。
                encoding=ENVELOPE_ENCODING,
                errors=ENVELOPE_ERRORS,
                # 我们总是显式传 --prompt；切断继承的 stdin，否则父进程被管道/重定向喂 stdin 时，
                # agentao 会把管道 stdin 当成 run spec，与 --prompt 冲突而拒绝执行（破坏自动化场景）。
                stdin=subprocess.DEVNULL,
                # 显式传 env（不再裸继承）：剔除毒空 `*_API_KEY`（见 scrubbed_environ）+ 钉死子端
                # std 流编码（见 envelope_child_env），其余逐项照传。
                env=envelope_child_env(),
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


def _parse_envelope(
    returncode: int, stdout: str | None, stderr: str | None
) -> AgentRunResult:
    """把子进程结果归一为 AgentRunResult。stdout 解析失败 → runtime_error（不可信任为成功）。

    **`str | None` 不是防御性洁癖**：`capture_output` 在 Windows 用 reader 线程收管道，线程里
    抛异常（如解码失败）会让 `proc.stdout` 变成 `None`，`json.loads(None)` 再抛一个 `TypeError`
    把真正的原因盖掉——issue #50 报的就是这条二次异常。此处**只是错误呈现层的兜底**，真修在
    `envelope_child_env` 的两端编码契约；留着它是因为「读不到 stdout」不止编码一种成因。
    """
    stdout_missing = stdout is None  # reader 线程猝死的独有形态，值得单独给一句人话诊断
    stdout = stdout or ""
    stderr = stderr or ""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError, TypeError):  # TypeError 双保险（上面已归一非 None）
        fallback = (
            "未能读到 agentao run 的 stdout（子进程输出流读取失败）"
            if stdout_missing
            else "无法解析 agentao run 输出"
        )
        detail = stderr.strip() or stdout.strip() or fallback
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
