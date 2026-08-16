"""`serve_im` / `run_login` / `run_identify`——IM 宿主的装配与生命周期（P4.21 §4.1/§4.4/§9）。

**校验全部先于任何副作用**：非法用法一律**就地拒启、不建立任何连接**（§9.2）。

**优雅停机没有绝对时间上界，这一点必须说明白**（决策P4.21-68）。逐层如实声明：

| 层次 | 谁来保证 | 是不是绝对 |
|---|---|---|
| `tools/call` 的**出站写入** | **没有人** | **否**（计时器在 write **之后**才起，决策P4.21-75） |
| `tools/call` **写完后等响应** | `--mcp-request-timeout` = T（§4.7） | 是，但只管**这一段** |
| MCP 握手、Streamable HTTP 传输往返 | agentao 的 startup 超时（`wait_for`） | 是，但只管**这一段** |
| **legacy SSE 传输进入、stdio 传输进入、失败清理、`disconnect()`** | **没有人** | **否**（决策P4.21-73/74） |
| 一次完整的 MCP 工具执行 | —— | **否**：含上面那行，故**无严格总墙钟上界** |
| 模型请求的空闲间隙 | `openai` 的 `read=600` | 是，但只管**空闲**，不管总时长 |
| 一轮问答的总时长 | **没有人** | **否** |
| **进程一定退得掉** | **第二次中断信号 → `os._exit(5)`** | **是** |

**于是硬退出不是"兜底"，而是唯一的绝对保证**——凡与"保证退出"冲突的东西一律让位，
包括我们自己想留下的那句提示（决策P4.21-59/66）。**一个说错了的保证，比没有保证更危险。**
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from ..errors import EXIT_AGENT_ERROR, EXIT_OK, EXIT_USAGE, GuanlanError
from ..paths import require_kb_root
from ..search import CorpusCache
from .contract import CHAT_GROUP, IMAdapter
from .defaults import (
    DEFAULT_IDENTIFY_SECONDS,
    DEFAULT_IDLE_TTL,
    DEFAULT_MAX_CONVERSATIONS,
    DEFAULT_MCP_REQUEST_TIMEOUT,
)
from .delivery import Delivery
from .intake import AccessPolicy, Intake
from .mcp_bound import BoundedTimeoutRegistry, _finite_positive
from .session import SessionRegistry
from .state import CredentialLock, state_dir

_logger = logging.getLogger("guanlan.im")

# 四个默认值的**单一来源**是 `defaults.py`（零依赖叶子）：cli 要在建 parser 时读同一份，
# 而 import 本模块会拉起 agentao + anyio。此前 cli 各抄一份字面量，于是改这里的值对
# CLI 用户毫无效果——常量沦为装饰。下面四个名字继续经 `__all__` 转出，外部导入点不变。


# ── 装配前置 ────────────────────────────────────────────────────────────────
def load_dotenv_for_im() -> None:
    """把 `.env` 里的值 `setdefault` 进 `os.environ`（决策P4.21-78）。

    **为什么必须显式做这一步**：进程里确实有人加载 `.env`——但那是 `build_from_environment`，
    而它要等到**第一次建 `Conversation`** 才跑，那已经在 `adapter.start()` **之后**了。
    于是「模型 API key 从 `.env` 读得到、飞书 App ID/Secret 读不到」，同一个文件对两类凭据
    行为不一致，而用户完全没有理由预期这种差别。

    **刻意复用 agentao 的 `safe_load_dotenv`**（而不是自己调 `dotenv`）：要的正是**逐字节同一套
    语义**——同样 `find_dotenv(usecwd=True)` 从 cwd 逐级上溯、同样 `os.environ.setdefault`
    的 **no-override**（真环境变量永远压过 `.env`）、同样剔除粘贴进来的 NUL 字节。
    自己再写一遍必然漂移，而漂移的表现会是"某个 key 在这条路上读得到、在那条路上读不到"。

    它是 agentao 的**私有模块**，故留一条 `python-dotenv` 兜底：上游哪天改了名，
    该失败的是这一个函数，而不是让 `guanlan im` 在启动时炸一个 ImportError。

    `setdefault` 语义使本函数**幂等**，与随后 `build_from_environment` 自己那次加载不冲突。
    """
    try:
        from agentao._env import safe_load_dotenv  # 私有模块，见 docstring
    except ImportError:  # pragma: no cover - 仅在上游改名时走到
        from dotenv import find_dotenv, load_dotenv

        path = find_dotenv(usecwd=True)
        if path:
            load_dotenv(path)  # 同为 no-override
        return
    safe_load_dotenv()


def _split_env_ids(raw: str | None) -> list[str]:
    """`--allow-user-env` 指向的环境变量：逗号分隔。只 `.strip()`，**不** `.lower()`。"""
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def build_policy(
    *,
    allow_user: list[str] | None,
    allow_user_env: str | None,
    allow_chat: list[str] | None,
    allow_all_users: bool,
    environ: dict[str, str] | None = None,
) -> AccessPolicy:
    """组装白名单。四者全空 → `GuanlanError(EXIT_USAGE)`，报错文案**直接指向 `im-identify`**。

    **ID 精确匹配、大小写敏感、不剥前缀**（决策P4.21-33）：平台 ID 是不透明标识，
    `.lower()` 归一**可能把两个不同主体合并**。只做 `.strip()`。
    """
    env = os.environ if environ is None else environ
    users = [u.strip() for u in (allow_user or []) if u.strip()]
    users += _split_env_ids(env.get(allow_user_env) if allow_user_env else None)
    chats = [c.strip() for c in (allow_chat or []) if c.strip()]
    if not users and not chats and not allow_all_users:
        raise GuanlanError(
            "必须给出白名单：--allow-user / --allow-user-env / --allow-chat / --allow-all-users "
            "至少给一个。还不知道对方的 ID？先跑 `guanlan im-identify --platform <平台>` "
            "（零 LLM、绝不回复、限时），它会把完整 ID 打到终端。",
            exit_code=EXIT_USAGE,
        )
    if allow_all_users:
        # 启动日志必须**大声**打（§5.2）——这是把整库开给一群人的旗标。
        # **措辞必须分两种情形**：只讲"群"是把最危险的那种用法说漏了——`--allow-all-users`
        # 同时旁路**单聊**的用户名单（真值表：单聊放行条件就是 `allow_all` OR 名单命中），
        # 于是不给 `--allow-chat` 时它的实际含义是「**任何能私聊到本机器人的人**都可读取整库」。
        # 而原文案「--allow-chat 列出的群里任何人」在没有 `--allow-chat` 时读起来恰好是"没有人"，
        # 与事实完全相反——**一个说反了的告警比没有告警更危险**。
        if chats:
            _logger.warning(
                "⚠️ 全员可问：**任何能私聊到本机器人的人**、以及 --allow-chat 列出的群里 @ 它的"
                "任何人，都可读取整库（--allow-all-users 只旁路「用户」名单，**永不**旁路「群」名单）。"
            )
        else:
            _logger.warning(
                "⚠️ 全员可问且未限定任何群：**任何能私聊到本机器人的人**都可读取整库。"
                "若只想开放给固定几个人，请改用 --allow-user / --allow-user-env。"
            )
    return AccessPolicy(
        allow_users=frozenset(users), allow_chats=frozenset(chats), allow_all=allow_all_users
    )


def check_caps(caps, policy: AccessPolicy, *, platform: str) -> None:
    """④ 能力/请求一致性拒启（决策P4.21-25）。

    给了 `--allow-chat` 但 `caps.supports_group=False`（个人微信）→ **拒启并明示**。
    替代「静默不生效」——后者让人以为配好了、实际永远等不到，且无从排查。
    """
    if policy.allow_chats and not caps.supports_group:
        raise GuanlanError(
            f"平台 {platform} 收不到群消息（supports_group=False），--allow-chat 不会生效；"
            "去掉它，或换用支持群聊的平台。",
            exit_code=EXIT_USAGE,
        )
    if policy.allow_chats and not policy.allow_users and not policy.allow_all:
        # 同一条「拒启并明示 ≫ 静默不生效」的判据（决策P4.21-25）：群聊放行是
        # 群 **AND** 人 **AND** @我，故只给 `--allow-chat`、不给任何人 → `user_ok` 恒 False
        # → **一条消息都过不了闸**，宿主起得来却完全是个聋子，且日志里一个字都没有。
        raise GuanlanError(
            "只给了 --allow-chat：群聊放行是「群 AND 人 AND @我」，没有任何用户被放行时"
            "一条消息也过不了闸（单聊同样收不到）。请补 --allow-user / --allow-user-env，"
            "或用 --allow-all-users 表示「该群里任何人都可以问」。",
            exit_code=EXIT_USAGE,
        )


def _validate_timeout(value: float) -> float:
    """`--mcp-request-timeout` 在**入口**校验为有限正数，否则 `EXIT_USAGE`（决策P4.21-63）。

    传 `0` / 负数 / `NaN` 会让 §4.7 的合并规则算出一个没有意义的值，**兜底自己先破了**。
    """
    if not _finite_positive(value):
        raise GuanlanError(
            f"--mcp-request-timeout 须为有限正数（收到 {value!r}）。", exit_code=EXIT_USAGE
        )
    return float(value)


def _build_mcp_registry(kb: Path, *, no_mcp: bool, request_timeout: float):
    """`--no-mcp` → `None`（彻底关掉文件发现）；否则包一层有限 request 上界（§4.7）。

    **为什么不干脆默认关掉 MCP**：P4.19 那半阶段正是围绕"用户已经往这个库里注入的外部 MCP
    server"做的，Web 端答问用得到它们。IM 端一刀关掉会让**同一个库、同一个问题，在手机上和
    浏览器里答得不一样**，而用户无从知道差别从哪来。**给上界比砍能力诚实。**
    """
    if no_mcp:
        return None
    from agentao.mcp.registry import FileBackedMCPRegistry
    from agentao.paths import user_root

    return BoundedTimeoutRegistry(
        FileBackedMCPRegistry(project_root=kb, user_root=user_root()),
        request_timeout=request_timeout,
    )


# ── 信号处理 ────────────────────────────────────────────────────────────────
class _SignalHandle:
    """可还原的信号处理器句柄。

    `_install_signal_handlers` 换掉的是**进程级**状态。失败路径不还原，调用方（测试、或将来把
    `serve_im` 嵌进别的进程的人）就继承了一对指向已死对象的处理器——其中一个还会 `os._exit`
    （决策P4.21-67）。
    """

    def __init__(self, previous: dict[int, object]) -> None:
        self._previous = previous

    def restore(self) -> None:
        for sig, handler in self._previous.items():
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(sig, handler)  # type: ignore[arg-type]
        self._previous = {}


def _on_second_interrupt(pending: int) -> NoReturn:
    """第二次 SIGINT/SIGTERM：**强制退出，且只有 `os._exit()` 算数**（决策P4.21-53）。

    agentao 的 `arun` 用它**自己的** `ThreadPoolExecutor`（刻意不用 loop 默认执行器），其 worker
    是**非守护线程**，而 `concurrent.futures.thread` 注册了 `atexit` 钩子在解释器退出时
    `join()` 它们——**线程真卡死时，进程会卡在"退出"这一步**，正是用户按第二次 Ctrl-C 想解决的
    那个现象。`sys.exit()` / 从 `asyncio.run()` 返回都救不了。

    三条纪律，一条都不能少：

    - **绝不在这条路上碰 `logging`**（决策P4.21-59）：`logging.shutdown()` 会对每个 handler
      `acquire()` + `flush()` + `close()`——后台线程正持着那把 handler 锁、或它的 flush 正卡在
      同一个死掉的下游上时，**主线程就到不了 `os._exit()`**。那样"唯一能保证退出的手段"
      自己先卡住了。故日志完整性只能 best-effort：缓冲里没刷出去的记录会丢，这是**代价**、不是缺陷。
    - **那行提示本身也是 best-effort**（决策P4.21-66）：`os.write(2, …)` 在 stderr 接到**已满的
      阻塞管道**时照样会阻塞。故先 `set_blocking(2, False)` 再写、整段裹 `try/except`，
      **写不出去就直接走**。凡是与"保证退出"冲突的东西，一律让位——包括我们自己想留下的那句话。
    - **不跑有序 close**：进程即将消失，socket 交给 OS 回收；此时按顺序关反而给残留线程制造
      "连接还在"的假象。**退出码仍是 5，绝不是 0**——把没停干净的退出报成 0，
      是把运维的判断依据一起骗了。

    前提说清楚：这套信号处理要求**事件循环线程本身还在转**（我们的卡死场景是 agentao 的 worker
    线程，满足此前提）。若 loop 自身也卡死，任何信号方案都不保证，只剩 `SIGKILL`。
    **不假装能兜住这种情况。**
    """
    try:
        os.set_blocking(2, False)
        os.write(2, f"强制退出：仍有 {pending} 轮未收尾，可能残留后台线程\n".encode())
    except Exception:  # noqa: BLE001 — 写不出去就算了，退出优先级最高
        pass
    os._exit(EXIT_AGENT_ERROR)


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event, pending: Callable[[], int]
) -> _SignalHandle:
    """第一次信号 → 优雅停；第二次 → `os._exit(5)`。返回可还原句柄。"""
    seen = 0

    def _handler(_signum: int, _frame: object) -> None:
        nonlocal seen
        seen += 1
        if seen == 1:
            # 经 `call_soon_threadsafe` 置位：它会写自管道唤醒可能正阻塞在 select 上的 loop。
            loop.call_soon_threadsafe(stop_event.set)
            return
        _on_second_interrupt(pending())

    previous: dict[int, object] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):  # 非主线程 / 平台不支持：不装即可，别拖垮启动
            pass
    return _SignalHandle(previous)


# ── 主循环与停机 ────────────────────────────────────────────────────────────
async def _receive_loop(adapter: IMAdapter, intake: Intake) -> None:
    async for msg in adapter.inbound():
        await intake.offer(msg)  # 五道闸，**不阻塞**（debounce 用独立 task）


async def _shutdown(
    recv: asyncio.Task,
    stop: asyncio.Task,
    intake: Intake,
    delivery: Delivery,
    adapter: IMAdapter,
    *,
    warn_interval: float,
) -> list[str]:
    """停机序列。**每步各自兜异常并记账**（决策P4.21-70/71）：后面的照跑，但失败不会被吞成"成功退出"。

    v7 把三步顺序 `await` 排开，`intake.drain()` 或 `stop_all()` 一抛，`adapter.close()` 就没了
    ——而"①~⑥ 都要跑完"正是刚立的契约。**清理代码自己不能有单点故障。**
    v8 又用 `suppress(Exception)` 兜住 `adapter.close()`，于是**连接没关干净、进程照样返回
    `EXIT_OK`**——这是**最坏的一种矛盾**：运维看到 0，以为一切正常。故记账用 `list[str]`
    而非布尔——日志里要能说清是**哪一步**没干净。
    """
    failed: list[str] = []

    async def _step(name: str, coro_fn: Callable[[], object]) -> None:
        try:
            await coro_fn()  # type: ignore[misc]
        except Exception:  # noqa: BLE001 — 清理不因单步失败中断
            _logger.error("停机步骤 %s 失败", name, exc_info=True)
            failed.append(name)

    async def _cancel_tasks() -> None:
        for t in (recv, stop):
            if not t.done():
                # 收流 task 只阻塞在 I/O 等待上，取消即停（决策P4.21-56）。
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            # ★ 已完成的 recv：主协程已 `exception()` 读过，**绝不再 `await`**——对已完成的 task
            # 取消无效、而 `await` 会把原异常重新抛出，于是 drain/stop_all/close 一个都不跑
            # （决策P4.21-62）。"收流出错"会因此变成"既没收流也没清理"。

    await _step("cancel-tasks", _cancel_tasks)
    await _step("intake-drain", lambda: intake.drain(flush=False))  # ②
    await _step("stop-all", lambda: delivery.stop_all(warn_interval=warn_interval))  # ③④⑤
    await _step("adapter-close", adapter.close)  # ⑥
    return failed


def _format_banner(
    *, platform: str, kb: Path, policy: AccessPolicy, mcp_on: bool, mcp_timeout: float
) -> str:
    """启动横幅文案（决策P4.21-80）。纯函数，故可直接单测。

    **只打数量、绝不打 ID**——§9.1 的日志脱敏在这里**适用**。`im-identify` 是唯一的例外
    （把完整 ID 打到终端是它**唯一**的用途，且它限时、绝不回复），而 `guanlan im` 是长驻宿主，
    stdout 常被重定向进日志文件 / systemd journal / 运维面板，受众远不止敲命令的那个人。
    数量已经足够回答横幅要回答的那个问题：**「我配的名单加载上了吗」**。
    """
    if policy.allow_all:
        who = "全员可问" + ("" if policy.allow_chats else "（任何能私聊到它的人）")
    else:
        who = f"用户 {len(policy.allow_users)} 人"
    if policy.allow_chats:
        who += f" · 群 {len(policy.allow_chats)} 个"
    mcp = (
        f"外部 MCP 已启用（单次请求上限 {mcp_timeout:g}s）" if mcp_on else "外部 MCP 已关闭（--no-mcp）"
    )
    return "\n".join(
        (
            f"[guanlan im] 已连上 {platform}，等待消息中（Ctrl-C 停服）",
            f"  知识库  {kb}（只读）",
            f"  白名单  {who}",
            f"  {mcp}",
        )
    )


async def _run(
    adapter: IMAdapter,
    intake: Intake,
    delivery: Delivery,
    *,
    warn_interval: float,
    banner: str | None = None,
) -> int:
    stop_event = asyncio.Event()
    handle = _install_signal_handlers(
        asyncio.get_running_loop(), stop_event, lambda: len(delivery.active())
    )
    try:
        try:
            await adapter.start()
        except GuanlanError:
            # **用法错必须保住自己的退出码与"不吐 traceback"**（§9.2）：`start()` 里最常见的
            # 抛出是「缺凭据 / 凭据空白 / SDK 版本不对 / 域名取值非法」——这些是 `EXIT_USAGE`，
            # 用户要看到的是**一句能照做的话**，不是一屏栈。落到下面的 `except Exception`
            # 会把它们统统压成 `EXIT_AGENT_ERROR(5)` + `exc_info=True`，既报错了退出码、
            # 又把可操作的提示埋在 traceback 里。
            # 清理照做（`close()` 兜底 + 外层 `finally` 还原信号处理器 + `serve_im` 释放凭据锁），
            # 然后**原样抛出**——由 CLI 顶层打一行并返回 `exc.exit_code`。
            with contextlib.suppress(Exception):
                await adapter.close()
            raise
        except Exception:  # noqa: BLE001 — 起不来就是起不来，但要收干净（决策P4.21-67）
            _logger.error("适配器 %s 启动失败", adapter.name, exc_info=True)
            with contextlib.suppress(Exception):
                await adapter.close()  # start() 可能已建了一半连接
            return EXIT_AGENT_ERROR
        if banner:
            # ★ **打在 `start()` 成功之后**（决策P4.21-80）：连接建立之前打「已连上」就是撒谎，
            # 而「进程起来了、连接没建起来」恰恰是 §15.6 首连看门狗刚消灭的那种活死人——
            # 横幅若抢在前面打，等于把看门狗刚拆掉的假象又装了回去。`im-identify` 的那句提示
            # 同样在 `start()` 之后打（同一条判据）。
            #
            # **走 stdout 而不是 `_logger.info`**：宿主不配置 logging，INFO 级没有任何 handler
            # 接（`logging.lastResort` 只兜 WARNING+），横幅的**全部用途**就是让操作者看见——
            # 用一条默认看不见的通道发它等于没写。stdout 在 IM 宿主上也没有协议占用
            # （那是 `guanlan mcp --transport stdio` 的约束，与这里无关）。
            with contextlib.suppress(Exception):  # 诊断辅助绝不能自己成为故障源
                print(banner, flush=True)
        recv = asyncio.create_task(_receive_loop(adapter, intake))
        stop = asyncio.create_task(stop_event.wait())
        code = EXIT_OK
        try:
            # ★ 两者**同时等**（决策P4.21-57）：只等 stop_event 的话，收流一死进程就成了活死人
            # ——**进程活着、日志一片安静、消息再也收不到**，用户能看到的只有"机器人不理我了"。
            await asyncio.wait({recv, stop}, return_when=asyncio.FIRST_COMPLETED)
            if recv.done():
                # **正常返回也算错**：长驻宿主不存在"入站流正常结束"这回事。
                # 且异常**必须读一次**——否则 asyncio 只在 task 被 GC 时吐一句
                # "Task exception was never retrieved"，很可能根本没人看见。
                exc = None if recv.cancelled() else recv.exception()
                _logger.error("入站流已中断，宿主无法继续服务", exc_info=exc)
                code = EXIT_AGENT_ERROR
        finally:
            # ★ 停机序列必须在 `finally` 里（决策P4.21-62）：无论上面怎么退出的，①~⑥ 都要跑完。
            failed = await _shutdown(
                recv, stop, intake, delivery, adapter, warn_interval=warn_interval
            )
            if failed:
                code = EXIT_AGENT_ERROR  # 清理没干净 → **绝不报 0**
        return code
    finally:
        handle.restore()


# ── 三个入口 ────────────────────────────────────────────────────────────────
def serve_im(
    root: str | Path,
    *,
    platform: str,
    allow_user: list[str] | None = None,
    allow_user_env: str | None = None,
    allow_chat: list[str] | None = None,
    allow_all_users: bool = False,
    web_base_url: str | None = None,
    model: str | None = None,
    max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
    idle_ttl: float = DEFAULT_IDLE_TTL,
    mcp_request_timeout: float = DEFAULT_MCP_REQUEST_TIMEOUT,
    no_mcp: bool = False,
    adapter: IMAdapter | None = None,
    store: object | None = None,
    clock: Callable[[], float] = time.monotonic,
    warn_interval: float = 30.0,
) -> int:
    """起 IM 宿主，长驻直到收到中断信号；正常停服返回 `EXIT_OK`。

    `adapter=` / `store=` 是**测试注入口**（决策P4.21-32）：不打真 LLM 请用会话层文档化的猴补
    接缝 `monkeypatch.setattr(chat, "build_from_environment", fake)`——**不是** `runner=`，
    那是**一次性子进程**契约（`runtime.AgentRunner`），与多轮进程内会话无关。
    """
    from ..web.chat import ConversationStore  # 惰性：核心命令不为会话层背 import 成本

    # ★ **早于 `build_policy` 与 `adapter.start()`**（决策P4.21-78）：前者要读
    # `--allow-user-env` 指向的变量，后者要读平台凭据——两者都该能从 `.env` 取到，
    # 而 `build_from_environment` 那次加载来得太晚（第一次建会话时才跑）。
    load_dotenv_for_im()
    kb = require_kb_root(root, writable=False)  # ① 只读门（与 MCP 宿主同，决策P4.10-3）
    policy = build_policy(  # ② 白名单：四者全空 → EXIT_USAGE
        allow_user=allow_user,
        allow_user_env=allow_user_env,
        allow_chat=allow_chat,
        allow_all_users=allow_all_users,
    )
    timeout = _validate_timeout(mcp_request_timeout)
    if max_conversations < 1:
        raise GuanlanError(f"--max-conversations 须 ≥ 1（收到 {max_conversations}）。", exit_code=EXIT_USAGE)
    if not _finite_positive(idle_ttl):
        # 与 `--mcp-request-timeout` / `--seconds` 同口径（决策P4.21-63）：`0` / 负数 → 每条消息
        # 都判「上下文已过期」；`nan` → 比较恒为假，**会话永不过期、`_sweep_expired` 永远腾不出
        # 容量**，顶满之后所有新用户恒收「会话数已满」。两种都是无声的坏，故入口就拒。
        raise GuanlanError(f"--idle-ttl 须为有限正数（收到 {idle_ttl!r}）。", exit_code=EXIT_USAGE)
    lock = CredentialLock(platform, command="guanlan im")
    lock.acquire()  # 三命令互斥（决策P4.21-38）：抢不到即 EXIT_USAGE，且报错含 owner pid + 子命令名
    try:
        ad = (  # ③ 建适配器（读凭据；缺/空白 → EXIT_USAGE）
            adapter
            if adapter is not None
            else _make_adapter(platform, group_wanted=bool(policy.allow_chats))
        )
        check_caps(ad.caps, policy, platform=platform)  # ④ 能力/请求一致性
        cache = CorpusCache()  # ⑤ P5.4 预热：把首搜冷算移出用户关键路径
        cache.prewarm_async(kb / "wiki")
        mcp_reg = _build_mcp_registry(kb, no_mcp=no_mcp, request_timeout=timeout)  # §4.7
        conv_store = store
        if conv_store is None:
            conv_store = ConversationStore(
                kb,
                model,
                persist=False,  # P4.9 reader 姿态：IM 会话不落盘（KB 零字节写）
                default_mode="read-only",
                write_gate=None,  # **不存在可写分支可切**（决策P4.21-8）
                max_conversations=max_conversations,
                idle_ttl=None,  # ← TTL 归 registry 独占（决策P4.21-46），理由见 session.py
                clock=clock,  # ← 与 registry **同一只**时钟（测试里两边时间轴一致）
                search_cache=cache,
                mcp_registry=mcp_reg,
            )
        registry = SessionRegistry(
            conv_store, ttl=idle_ttl, tombstone_limit=max_conversations, clock=clock
        )
        delivery = Delivery(
            ad,
            registry,
            cache,
            kb,
            web_base_url=web_base_url,
            max_conversations=max_conversations,
        )
        registry.set_busy(delivery.busy)  # 在飞守卫接线（§4.5），避免构造期循环依赖
        intake = Intake(ad.caps, policy, clock=clock)
        intake.set_sink(delivery.submit)  # task 的创建权归 Delivery（决策P4.21-52）
        banner = _format_banner(
            platform=platform,
            kb=kb,
            policy=policy,
            mcp_on=mcp_reg is not None,
            mcp_timeout=timeout,
        )
        return asyncio.run(
            _run(ad, intake, delivery, warn_interval=warn_interval, banner=banner)
        )
    finally:
        lock.release()  # 与 start() 失败路径共用同一套兜底（§4.1）


def run_login(*, platform: str) -> int:
    """交互式登录（决策P4.21-23）。`--platform feishu` 在 **CLI 解析期**就被拒，不进这里。

    平台是否需要登录**查注册表**（`LOGIN_FLOWS`），不写 `if platform ==`——核心零平台分支
    （决策P4.21-3）的判据对 `!=` 同样成立。
    """
    from .adapters import LOGIN_FLOWS

    flow = LOGIN_FLOWS.get(platform)
    if flow is None:
        raise GuanlanError(
            f"平台 {platform!r} 不需要（也不支持）`guanlan im-login`：它的凭据走环境变量。"
            f"需要登录的平台：{', '.join(sorted(LOGIN_FLOWS)) or '（无）'}。",
            exit_code=EXIT_USAGE,
        )
    lock = CredentialLock(platform, command="guanlan im-login")
    lock.acquire()
    try:
        return asyncio.run(flow(state_dir(platform)))  # type: ignore[arg-type]
    finally:
        lock.release()


def run_identify(*, platform: str, seconds: float = DEFAULT_IDENTIFY_SECONDS) -> int:
    """`guanlan im-identify`——解开白名单死锁的那把钥匙（决策P4.21-28）。

    | 性质 | 为什么这样设计 |
    |---|---|
    | **绝不回复任何消息** | 对方只看到「发了没人理」，与机器人不存在无异——不泄露「这里有个知识库机器人」 |
    | **不加载 KB、不建会话、不调 LLM** | 零成本、零内容泄露面；连 `-C` 都不接受 |
    | **限时**（默认 300s） | 不会长期敞开一个无鉴权的监听态；也保证忘关的 identify 最多把宿主挡 5 分钟 |
    | **把完整 ID 打到终端 stdout** | 这是它**唯一**的用途；日志脱敏规则在此**不适用**（终端 = 操作者本人） |
    """
    if not _finite_positive(seconds):
        raise GuanlanError(f"--seconds 须为有限正数（收到 {seconds!r}）。", exit_code=EXIT_USAGE)
    # identify 不接 `-C`、也不建会话，故这里是**唯一**会加载 `.env` 的地方——飞书的
    # identify 同样需要 App ID/Secret 才连得上（决策P4.21-78）。
    load_dotenv_for_im()
    lock = CredentialLock(platform, command="guanlan im-identify")
    lock.acquire()
    try:
        ad = _make_adapter(platform)
        return asyncio.run(_identify_loop(ad, platform=platform, seconds=float(seconds)))
    finally:
        lock.release()


async def _identify_loop(adapter: IMAdapter, *, platform: str, seconds: float) -> int:
    try:
        await adapter.start()
    except GuanlanError:  # 用法错保住退出码、不吐 traceback，与 `_run` 同口径（§9.2）
        with contextlib.suppress(Exception):
            await adapter.close()
        raise
    except Exception:  # noqa: BLE001
        _logger.error("适配器 %s 启动失败", adapter.name, exc_info=True)
        with contextlib.suppress(Exception):
            await adapter.close()
        return EXIT_AGENT_ERROR
    print(
        f"[im-identify] 监听中，{seconds:g}s 后自动退出（不会回复任何消息，Ctrl-C 提前结束）",
        flush=True,
    )

    async def _listen() -> None:
        seen = 0
        async for msg in adapter.inbound():
            seen += 1
            # **日志脱敏规则在此不适用**：终端 = 操作者本人，而这是本命令唯一的用途（§9.1）。
            print(f"\n[{seen}] 平台={platform}  租户={msg.tenant}")
            print(f"    用户ID={msg.user_id}        ← 填入 --allow-user")
            if msg.chat_type == CHAT_GROUP:
                print(f"    会话ID={msg.chat_id}            ← 填入 --allow-chat（群聊才需要）")
                print(f"    聊天类型={msg.chat_type}   @了机器人={'是' if msg.mentioned_me else '否'}")
            else:
                print("    会话ID=（单聊，无需 --allow-chat）")
                print(f"    聊天类型={msg.chat_type}")
            print("", flush=True)

    task = asyncio.create_task(_listen())
    try:
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=seconds)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(Exception):
            await adapter.close()
    return EXIT_OK


def _make_adapter(platform: str, *, group_wanted: bool = False) -> IMAdapter:
    from .adapters import ADAPTERS

    factory = ADAPTERS.get(platform)
    if factory is None:
        raise GuanlanError(
            f"未知平台 {platform!r}；可用：{', '.join(sorted(ADAPTERS))}。", exit_code=EXIT_USAGE
        )
    return factory(state_dir(platform), group_wanted=group_wanted)


__all__ = [
    "DEFAULT_IDENTIFY_SECONDS",
    "DEFAULT_MAX_CONVERSATIONS",
    "DEFAULT_MCP_REQUEST_TIMEOUT",
    "build_policy",
    "check_caps",
    "run_identify",
    "run_login",
    "serve_im",
]
