"""问答 / 多轮会话 = 只读进程内嵌入（P4，见 docs/P4-Web宿主.md §4.4 决策P4-8）。

已核对 agentao 源码：`agentao run` 子进程无 session resume，多轮**只能**靠在内存里持有一个
`Agentao` 对象、反复 `arun()` 让 `self.messages` 跨轮累积。一次性提问 = "新建会话只跑一轮"。
用 agentao **文档化的嵌入面**（`build_from_environment` + `.arun` + 构造期 `transport`），
不碰内部 API，逐一化解 P2 §4.3 嵌入四坑。

四坑化解：① 凭据缺失 → `build_from_environment`（读 `.env`/`~/.agentao`）；② skill 不自动激活
→ 构造后显式激活 `guanlan-wiki`（且构造前 `ensure_skill_available` 保其可发现）；③ `<wd>/
agentao.log` 由**我们自己的 `logger=`** 接管（嵌入契约：注入 logger = 宿主自管日志栈，
LLMClient 不再自挂 handler）——默认经 `configure_agent_log` 给这个共享 logger 挂**一个**
`RotatingFileHandler` 落 `<wd>/agentao.log`（**像 CLI 一样**记会话日志，`guanlan web
--no-agent-log` 可关）；④ 内部 API 耦合 → 只用 `build_from_environment`/`.arun`/`transport`
这一组面。

只读姿态在构造后**两点同步置位**（照搬 `cli/run.py`）：engine 的 read-only 预设为空，真正拦截
写/shell 的是 `ToolRunner.readonly_mode`，少调第二步就不是真只读。
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

import anyio
from agentao.cancellation import CancellationToken
from agentao.embedding import (
    build_from_environment,
    delete_session,
    list_sessions,
    load_session,
    save_session,
)
from agentao.embedding.compat import build_compat_transport
from agentao.permissions import PermissionMode

from ..check import run_check
from ..gate import REPAIR_PROMPT, _render_violations
from ..skill import SKILL_NAME, ensure_skill_available
from .jobs import WriteGate
from .policy_fs import (
    AgentaoSnapshot,
    make_policy_fs,
    restore_agentao,
    restore_path,
    snapshot_agentao,
)

# Web 双姿态（决策P4.5-1）：仅对齐 agentao 枚举的 read-only / workspace-write 两值。
# full-access（绕全部 preset 规则）/ plan（内部态）永不在 Web 出现 → set_mode 抛 ValueError → 端点 422。
_WEB_MODES = ("read-only", "workspace-write")

# 嵌入会话共享的 logger（坑③：注入它 = 我们自管日志栈）。默认由 configure_agent_log 给它挂
# 一个落 <wd>/agentao.log 的 file handler；不挂时无 handler、不写文件（如测试 / --no-agent-log）。
# propagate=False：日志栈归我们自管，绝不上抛 root——否则 --no-agent-log 下无 handler 的
# WARNING+ 会经 logging.lastResort 落到 sys.stderr，而 ingest worker 正用进程级 redirect_stderr
# 捕获输出（jobs.py），并发会话的日志会被串进某个 ingest 作业的结果面板。
_logger = logging.getLogger("guanlan.web.chat")
_logger.propagate = False

# 已接上 agentao.log 的库根（幂等：重复 serve/create_app 不重挂 handler，避免每行写多遍）。
_agent_log_paths: set[str] = set()

# 内存会话数硬上限：超出拒新建（决策P4-8：v1 不上 LRU，仅一个保守上界给内存设界）。
MAX_CONVERSATIONS = 100

# 只读姿态下某工具是否被 `tool_runner` 拦（喂 `/tools` 的 `blocked` 列，决策P4.4-2/§3）。
# 判定**纯静态、绝不试调工具**：① 优先读 agentao 工具元数据 `is_read_only`——这正是 agentao
# 自己在只读下的拦截基准（`runtime/tool_planning._decide`：`readonly_mode and not tool.is_read_only
# → DENY`），故 `blocked = not is_read_only` 与真实拦截逐项同口径；② 无该元数据（鸭子桩/异形工具）
# 时退回**已知工具名**静态映射；③ 两者都不命中 → `"unknown"`（如实示弱，不为求一个 bool 去跑工具）。
# P4.5 起姿态可翻（read-only / workspace-write），故 `blocked` 须随当前姿态变（见 `_blocked_in_mode`）：
# 可写姿态下 `tool_runner.readonly_mode=False`、`_decide` 的只读 DENY 分支根本不触发，无工具被分类拦截。
_WRITE_TOOL_NAMES = frozenset(
    {
        "write_file", "replace", "edit_file",
        "run_shell_command", "shell", "bash",
        "save_memory", "todo_write", "plan_save", "plan_finalize",
    }
)
_READ_TOOL_NAMES = frozenset(
    {
        "read_file", "list_directory", "list_files", "ls",
        "glob", "search_file_content", "grep",
    }
)


def _blocked_in_readonly(tool: object) -> bool | str:
    """只读姿态下该工具是否被拦：`True`/`False`/`"unknown"`（纯静态，绝不试调工具）。

    优先 agentao 元数据 `is_read_only`（与 `tool_planning._decide` 同基准）；无元数据退回
    已知工具名静态映射；都不命中返回 `"unknown"`（决策P4.4-2，§3）。
    """
    is_read_only = getattr(tool, "is_read_only", None)
    if isinstance(is_read_only, bool):
        return not is_read_only  # 只读工具放行；其余在只读下被 DENY（镜像 agentao 真实拦截）
    name = getattr(tool, "name", "")
    if name in _WRITE_TOOL_NAMES:
        return True
    if name in _READ_TOOL_NAMES:
        return False
    return "unknown"


def _blocked_in_mode(tool: object, mode: str) -> bool | str:
    """当前姿态下该工具是否被拦（喂 `/tools` 的 `blocked` 列，P4.5 评审 P2）。

    `workspace-write` 下 `tool_runner.readonly_mode=False`——`tool_planning._decide` 的只读 DENY
    分支不触发，无工具被**分类**拦截（写/shell 都放行；raw//AGENTAO.md 的拦截是层① 路径级、非
    工具级），故一律 `False`。`read-only` 退回 `_blocked_in_readonly` 的逐项判定。否则 `/tools` 会
    在可写会话里仍把写/shell 工具标灰，正是徽标/自省该如实指示写能力时误导（评审 P2）。
    """
    if mode == "workspace-write":
        return False
    return _blocked_in_readonly(tool)


def _context_stats(agent: object, msgs: list) -> dict:
    """会话上下文用量（喂 `/status` `/context`），口径**完全对齐** agentao `get_conversation_summary`。

    headline（`estimated_tokens`/`usage_percent`）走 `get_usage_stats(messages)`——messages-only 的本地
    估算（或 Tier-1 API 真值），刻意不含 system/tools，使新会话显示 0、API 计数时已涵盖全部开销，与
    agentao 一致、**不动**。但 `token_breakdown` 另用含 **system prompt + tools schema** 的输入重算：
    系统提示不在 `agent.messages` 里、`get_usage_stats` 不传 `tools` 时 tools 分项恒 0，否则 `/context`
    的 system/tools 分项被低报（codex 评审）。私有面（`_build_system_prompt`/`_plan_mode`/`to_openai_format`）
    取不到或抛错时降级为 messages-only breakdown，绝不让自省崩。
    """
    cm = agent.context_manager
    stats = cm.get_usage_stats(msgs)  # headline 与 agentao 同口径（messages-only / api），保持不动
    if not msgs:
        return stats  # 空会话：三分项皆 0（镜像 get_conversation_summary 的 /new 复位）
    try:
        tools_schema = agent.tools.to_openai_format(plan_mode=getattr(agent, "_plan_mode", False))
        messages_with_system = [
            {"role": "system", "content": agent._build_system_prompt()}
        ] + msgs
        # 仅重算 breakdown（含 system + tools），headline 不动——逐行镜像 agent.py 的取法。
        stats["token_breakdown"] = cm.estimate_tokens_breakdown(
            messages_with_system, tools=tools_schema
        )
    except Exception:  # noqa: BLE001 — 私有面缺失/变更则降级 messages-only breakdown，不崩自省
        _logger.debug("精确 context breakdown 取数失败，降级 messages-only", exc_info=True)
    return stats


def _is_canonical_uuid(s: object) -> bool:
    """HTTP id 是否规范全 UUID（P4.2 §2.2 闸①，决策P4.2-7）。

    `load_session`/`delete_session` 内部按 `session_id.startswith(input)` 前缀匹配（且
    `load_session` 无果还 fallback 到时间戳 stem 前缀）。规范全 UUID（`str(uuid.UUID(s)) == s`）
    下「前缀匹配」收敛为「精确匹配」——36 字符 UUID 不会是另一个等长 UUID 的真前缀、也不会
    前缀命中 21 字符时间戳 stem。拒短前缀 / 时间戳 stem / 非规范写法，绝不把裸 id 喂前缀匹配。
    """
    try:
        return isinstance(s, str) and str(uuid.UUID(s)) == s
    except (ValueError, AttributeError, TypeError):
        return False


def _prune_old_snapshots(kb: Path, session_id: str) -> None:
    """best-effort 删掉本 `session_id` 现存的旧快照，使每会话一般只留一份文件（**save 前**调）。

    放在 save *前*（决策P4.2-1，回应 codex 轮转评审）：先把本会话旧份删净，再让 `save_session`
    写新份——这样目录在 save 时**不会**因「旧份 + 新份」瞬时多一份而顶到 11、触发 `_rotate_sessions`
    把**别的**会话挤出 10 文件槽（save-后-prune 会在已满 10 时**每次更新**都误淘汰别人，更频繁/更糟）。
    代价是单一可接受窗口：删旧份后若 `save_session` 失败、且进程在下一轮成功 save 前崩溃，丢**本**
    会话最近持久副本——但它仍 live 在内存、进程存活下一轮即补上，不误伤他会话（§2.1 代价①）。

    纯卫生、**全程 best-effort**：读坏 JSON 跳过、删不掉也跳过（不抛、不阻断后续 save）。它只为
    「长会话不挤占别人 10 文件槽」的保留质量，不是正确性前提——不引「删不掉就别保存」那类硬
    语义，也不追求「文件数=会话数」硬不变量（§2.1）。
    """
    sessions = kb / ".agentao" / "sessions"
    if not sessions.exists():
        return
    for p in sessions.glob("*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                sid = json.load(f).get("session_id")
            if sid == session_id:
                p.unlink()
        except (OSError, json.JSONDecodeError):
            continue  # 读坏 / 删失败：跳过，best-effort


def configure_agent_log(root: Path) -> Path:
    """把嵌入 chat 的会话日志接到 `<root>/agentao.log`（与 CLI 同名同轮转），**全进程仅挂一次**。

    嵌入契约（见 agentao `LLMClient` 文档）：一旦注入 `logger=`，宿主就**自管日志栈**——
    LLMClient 不再往 `logging.getLogger("agentao")` 自挂 file handler。故这里给共享单例
    `_logger` 挂**一个** `RotatingFileHandler`，chat 的 LLM 交互便像 CLI 那样落 `agentao.log`。
    只挂一次很关键：`build_from_environment` 每会话构造一次，若每次都挂 handler（LLMClient
    对重复 handler **不去重**）会把每行写 N 遍。文件已被 `.gitignore`（`agentao.log[.*]`）、
    且扫描只看 `*.md`，不污染知识库。
    """
    target = (root / "agentao.log").resolve()
    key = str(target)
    if key in _agent_log_paths:
        return target
    handler = logging.handlers.RotatingFileHandler(
        target, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(logging.DEBUG)  # LLM 交互打在 INFO；DEBUG 全收，与 agentao 默认一致。
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _logger.setLevel(logging.DEBUG)
    _logger.addHandler(handler)
    _agent_log_paths.add(key)
    return target

# 当前 turn 的事件发射器：emit(kind, data)。kind ∈ {"token"}（done/error 由端点补）。
Emit = Callable[[str, object], None]


class Conversation:
    """一会话一 `Agentao` 对象 + 一把 `asyncio.Lock`，按需把只读会话落 `.agentao/sessions/`。

    P4.2：`id` 改用 agentao 的稳定 UUID（=每份快照里的 `session_id`），跨重启稳定、可懒恢复；
    `persist` 开时每轮成功后 off-loop 落盘（决策P4.2-2/5）。新建与恢复**共用本 `__init__`** 的
    只读两点置位 + 激活 `SKILL_NAME`——恢复零漂移、绝不恢复出可写会话（决策P4.2-4）。
    """

    def __init__(
        self,
        cid: str,
        kb: Path,
        model: str | None,
        *,
        persist: bool,
        mode: str = "read-only",
        write_gate: WriteGate | None = None,
    ) -> None:
        self.id = cid
        self._kb = kb
        # 撤销/写日志的路径都来自 policy_fs 的 `resolve(strict=False)`（同 kb.resolve() 坐标系）；
        # 对外算相对路径须用**同一 resolved kb**，否则 macOS `/var`→`/private/var` 等 symlink 下
        # `relative_to(未 resolve 的 kb)` 抛 ValueError（写日志键是 resolved、kb 不是）。
        self._kb_resolved = kb.resolve()
        self._model = model  # _save 用（save_session 的 model 参数）；恢复时 = load 出的 model
        self._persist = persist
        self._mode = mode  # 当前会话姿态（P4.5：read-only / workspace-write），/mode 翻转
        self._write_gate = write_gate  # 进程级单写者协调（write_lock + 层③ 计数）；None=纯读测试
        self.lock = asyncio.Lock()  # 同一会话两轮不并发跑同一 agent 对象
        self.title: str | None = None
        self.turns = 0
        self.closed = False  # 删除后置位（锁内读写）；拦下"已接受但尚未起跑"的排队 turn
        self._emit: Emit | None = None  # 当前 turn 的线程安全 emit；transport 固定回调转发到它
        self._cancel_token: CancellationToken | None = None  # 当前在飞 turn 的取消令牌；停止按钮经它打断
        self._inflight = 0  # 在飞轮计数（端点 begin_turn/end_turn 维护）：>0 时 start→装令牌窗口内
        # 到达的停止记为待停、而非当 idle 丢弃（codex 竞态修复）。
        self._stop_requested = False  # 待停标志：有轮起跑但令牌尚未装上时由 request_stop 置位，
        # turn 进锁装令牌后立即兑现。
        # 撤销本轮写（决策P4.5-4/13）：最近一个可写 turn 的写日志 + 一次性 token。
        self._undo_token: str | None = None
        self._undo_journal: dict[Path, tuple[bytes | None, str]] = {}

        ensure_skill_available(kb)  # 嵌入式同样需保证 skill 可发现（坑②前置）

        # 层①（决策P4.5-2）：构造期注入 PolicyFileSystem wrapper（姿态无关、装一次），包住
        # agent.filesystem，对经该 capability 的结构化写守 immutable 集（raw/ + AGENTAO.md）。
        # 它还在可写 turn 内记 per-turn 写日志（wiki/ + SCHEMA.md），供撤销本轮写（§3）。
        self._policy_fs = make_policy_fs(kb)

        opts: dict = dict(
            working_directory=kb,
            logger=_logger,
            filesystem=self._policy_fs,  # 层①：透传到 agent.filesystem → 绑定每个写工具（C0 已验）
            # token 流式的**唯一活线**：transport 在构造期冻结（EventType.LLM_TEXT→chunk）。
            # 事后改 self.agent.llm_text_callback 是死代码（只读存档，运行时不重读）。
            transport=build_compat_transport(llm_text_callback=self._on_token),
        )
        if model is not None:
            # --model 仅在给定时入 overrides：显式 model=None 会盖掉 .env 发现的模型、
            # 触发 agent.py 的 "model required" ValueError（默认不带 --model 的常路就崩）。
            opts["model"] = model

        self.agent = build_from_environment(**opts)
        # 姿态两点同步置位（缺第二步 = 没真正切换，照搬 cli/run.py）：开局用构造姿态。
        self._apply_mode(mode)
        self.agent.skill_manager.activate_skill(
            SKILL_NAME, task_description="观澜 Web 工作会话"
        )

    def _apply_mode(self, mode: str) -> None:
        """把姿态落到 agent 的两点置位（engine Mode + tool_runner.readonly）——镜像 cli/run.py。

        只翻「能不能写」，**不动层① wrapper**（wrapper 姿态无关、守「写到哪」，决策P4.5-2/5）。
        绝不接受 full-access/plan（决策P4.5-1）：非 _WEB_MODES → ValueError，端点转 422。
        """
        if mode == "read-only":
            self.agent.permission_engine.set_mode(PermissionMode.READ_ONLY)
            self.agent.tool_runner.set_readonly_mode(True)
        elif mode == "workspace-write":
            self.agent.permission_engine.set_mode(PermissionMode.WORKSPACE_WRITE)
            self.agent.tool_runner.set_readonly_mode(False)
        else:
            raise ValueError(f"未知姿态：{mode}")  # 端点转 422；绝不接受 full-access/plan
        self._mode = mode

    @property
    def mode(self) -> str:
        """当前会话姿态（read-only / workspace-write）。供端点判「本 turn 是否会写」（评审 P2）。"""
        return self._mode

    def set_mode(self, mode: str) -> str:
        """`/mode` 运行时真切换（只翻两点置位、不重建 agent、不动 wrapper，决策P4.5-5）。

        调用方须持 `self.lock`（与在飞 turn 串行）。返回翻转后的姿态。
        """
        self._apply_mode(mode)
        return self._mode

    def _on_token(self, chunk: str) -> None:
        """transport 固定回调，**在 arun 的 executor 线程里跑**；lock 串行化同会话各 turn，单槽无竞态。"""
        if self._emit is not None:
            self._emit("token", chunk)

    def begin_turn(self) -> None:
        """端点在 emit('start') **之前**调：标记本会话有一轮正在起跑（codex 竞态修复）。

        关掉「start 帧已发、`turn` 还没进到装令牌的临界区」那段停止竞态：前端一收到 start 就可能
        POST /stop，那一刻 `_cancel_token` 仍是 None。有了在飞标记，`request_stop` 会把这次停止记为
        待停（而非当 idle 丢弃），待 `turn` 进锁装上令牌即兑现。令牌**仍在锁内**创建/安装，故并发同
        会话时 `_cancel_token` 始终指向持锁的活跃轮、stop 不会误打排队轮（不同于把令牌装在锁外）。"""
        self._inflight += 1

    def end_turn(self) -> None:
        """端点在该轮彻底收尾（含 stopped/error）后调，与 `begin_turn` 配对。"""
        self._inflight -= 1
        if self._inflight <= 0:
            self._inflight = 0
            self._stop_requested = False  # 无在飞轮了：清掉未兑现的待停，绝不泄漏到下一轮

    async def turn(self, msg: str, emit: Emit, meta_out: dict | None = None) -> str:
        """跑一轮：把 token 经 emit 推前端，返回完整答案；`asyncio.Lock` 串行同会话各轮。

        可写姿态（workspace-write）下还在收尾把**层②还原 / 写后 check / 撤销可用性**写进 `meta_out`
        （由端点装进 done/error/stopped 帧的可选字段，§7）。`meta_out` 由端点持有 → 无论本轮返回
        还是抛错，端点都读到自己这只 dict，杜绝跨轮读串（决策P4.5-9）。read-only 姿态零开销、
        `meta_out` 保持空。

        **锁序（决策P4.5-6/13，评审 High）**：先持 `self.lock`（本方法 `async with`）、再异步取进程
        `write_lock`——所有会话态写操作（可写 turn 与撤销端点）同序，绝不反序。
        """
        async with self.lock:
            # StreamingResponse 在路由返回**后**才起跑 turn；其间并发 DELETE 可能已拿锁删本会话、
            # close 了 agent。锁内复检 closed，拒绝在已关闭 agent 上跑（端点据异常发 error 事件）。
            guarded = self._mode == "workspace-write" and not self.closed
            before_agentao: AgentaoSnapshot | None = None
            # 写锁持有标志用**可变容器**、且在 acquire 的线程函数内置位：`run_sync(acquire)` 默认
            # abandon_on_cancel=False——被取消时仍等 acquire 返回（锁已到手）、再在 await 处抛
            # CancelledError，使「await 之后」的赋值永不执行。把置位放进线程函数则不论 CancelledError
            # 落在哪、finally 读到的都是真实持有态，杜绝锁泄漏（评审 BUG 1；当前虽被 shield 兜住、仍兜底）。
            lock_held = [False]
            try:
                if self.closed:
                    raise RuntimeError("会话已删除")
                loop = asyncio.get_running_loop()

                def thread_safe_emit(kind: str, data: object) -> None:
                    # _on_token 落在线程池线程，碰 asyncio.Queue 必须 call_soon_threadsafe 桥回 loop
                    # （直接跨线程 put_nowait 会丢 token / 卡流 / 偶发崩）。
                    loop.call_soon_threadsafe(emit, kind, data)

                self._emit = thread_safe_emit
                if guarded:
                    # 层③（决策P4.5-10）：进可写区，宿主写端点这段内一律 423（兜 shell curl 旁路）。
                    if self._write_gate is not None:
                        self._write_gate.enter_writable()
                        # 单写者写锁（决策P4.5-6）：**异步**取阻塞锁，绝不在事件循环线程直接 acquire
                        # 阻塞（否则 read-only turn / job 轮询 / UI 全卡死）。锁序：已持 self.lock。
                        # 持有标志经 acquire_thunk 在线程函数内置位（见 lock_held 注释），不依赖 await
                        # 之后的赋值——与 /graph、/undo 同一抗取消守法（评审 P1）。
                        await anyio.to_thread.run_sync(
                            self._write_gate.acquire_thunk(lock_held)
                        )
                    # 层②（决策P4.5-3/c）：只拍 AGENTAO.md 一个文件（近免费）；raw/ 树不扫。
                    before_agentao = snapshot_agentao(self._kb)
                    self._policy_fs.begin_journal()  # 开本轮写日志（wiki/ + SCHEMA.md）

                # 每轮一枚取消令牌，**锁内**创建/安装：故 _cancel_token 恒指向持锁的活跃轮，并发同会话
                # 时 stop 只打活跃轮、不误伤排队轮（codex 评审）。停止按钮经 request_stop() 置位它，arun
                # 的 executor 线程读到后自然收尾、把 AgentCancelledError 经 future 抛回——await arun 直到
                # 线程真正结束才返回，故 lock 全程持有、绝无"残线程并发改 agent.messages"的竞态（不同于
                # asyncio 取消）。
                token = CancellationToken()
                self._cancel_token = token
                # 兑现 start→装令牌窗口内到达的待停（begin_turn 已标记在飞、request_stop 记了待停）：
                # 装好令牌后立即 cancel，arun 起步即收到 → 首轮停止不再被静默吞掉（codex 竞态修复）。
                if self._stop_requested:
                    self._stop_requested = False
                    token.cancel("user-stop")
                self.turns += 1
                if self.title is None:
                    self.title = msg.strip()[:50] or "（空）"
                # arun = chat 的 async 包装（内部 run_in_executor）；传入令牌让停止可控。
                answer = await self.agent.arun(msg, cancellation_token=token)
                if self._persist:
                    # 仅成功轮落盘、off-loop 不堵事件循环；任何异常（prune / save_session）只记日志，
                    # **绝不**让它冒泡把已成功的 arun 答案翻成 error（失败不毁答案，同 §4.4 降级精神）。
                    try:
                        await anyio.to_thread.run_sync(self._save)
                    except Exception:  # noqa: BLE001 — 落盘失败仅记日志，本轮答案照常返回
                        _logger.warning("会话 %s 落盘失败，本轮未持久化", self.id, exc_info=True)
                return answer
            finally:
                # ── 可写 turn 收尾（决策P4.5-3/4，评审 High：还原须早于 error SSE / 计数-- / 释锁）──
                if guarded:
                    try:
                        await self._finalize_writable(before_agentao, meta_out)
                    finally:
                        # 计数-- 与释锁须发生在 finally 内、且在 meta 落定之后（评审 High）。
                        if lock_held[0] and self._write_gate is not None:
                            self._write_gate.write_lock.release()
                        if self._write_gate is not None:
                            self._write_gate.exit_writable()
                # 任何退出路径（closed 早退 / arun 抛 / 正常返回）都清掉 emit 与令牌（一处归口）。
                self._emit = None
                self._cancel_token = None

    async def _finalize_writable(
        self, before_agentao: AgentaoSnapshot | None, meta_out: dict | None
    ) -> None:
        """可写 turn 收尾：层②还原 + 无条件写后 check + 撤销可用性，写进 `meta_out`（决策P4.5-3/4）。

        在 `write_lock` 内跑（权威 `wiki/` 门禁，§5）：① 先把被旁路改的 `AGENTAO.md` 还原（早于
        error SSE）；② 取本轮写日志、登记撤销 token（写日志非空即可用，**独立于 check**）；③ 无条件
        跑 `run_check(wiki)`——既不靠写日志、也不用 mtime/size 指纹（评审 High/Medium），覆盖 shell
        直写。任何一步抛错只记日志，绝不连累已成功的答案（同 §4.4 降级精神）。
        """
        # ① 层②：AGENTAO.md 被旁路改/删/换形态 → 还原原字节（先清替身、不顺 symlink 写穿）。
        try:
            mutated = await anyio.to_thread.run_sync(
                restore_agentao, self._kb, before_agentao
            )
        except Exception:  # noqa: BLE001 — 还原失败仅记日志，不毁答案
            _logger.warning("会话 %s AGENTAO.md 还原失败", self.id, exc_info=True)
            mutated = None

        # ② 取本轮写日志 → 撤销可用性（写日志非空驱动、独立于 check，评审 Medium）。
        journal = self._policy_fs.end_journal()
        undo: dict | None = None
        if journal:
            self._undo_journal = journal
            self._undo_token = str(uuid.uuid4())
            undo = {
                "available": True,
                "token": self._undo_token,
                "paths": [p.relative_to(self._kb_resolved).as_posix() for p in journal],
            }
        else:
            self._undo_journal = {}
            self._undo_token = None

        # ③ 无条件写后 check（零 LLM、锁内一致状态；覆盖 shell 直写 wiki/）。
        check: dict | None = None
        try:
            result = await anyio.to_thread.run_sync(run_check, self._kb / "wiki")
            check = {
                "ok": result.ok,
                "violations": [
                    {"page": v.page, "kind": v.kind, "detail": v.detail}
                    for v in result.violations
                ],
            }
            if not result.ok:
                # 「让 Agent 修复」用的下一轮消息：服务端用 gate.REPAIR_PROMPT 格式化（复用 ingest
                # 自愈同一薄 prompt，§3 决策P4.5-4）。前端把它当下一轮 user 消息发出即驱动修复。
                check["repair_prompt"] = REPAIR_PROMPT.format(
                    violations=_render_violations(result.violations)
                )
        except Exception:  # noqa: BLE001 — check 失败仅记日志，不毁答案
            _logger.warning("会话 %s 写后 check 失败", self.id, exc_info=True)

        if meta_out is not None:
            if check is not None:
                meta_out["check"] = check
            if mutated:
                meta_out["immutable_mutated"] = [mutated]
            if undo is not None:
                meta_out["undo"] = undo

    def can_undo(self, token: str) -> bool:
        """本轮写日志是否可被此 token 撤销（廉价、纯读；端点据此在取 write_lock **前**短路）。

        让陈旧/空 token 不必白白排队取进程级 write_lock（可能卡在在飞 ingest 之后数十秒）再回 409
        （评审 BUG 4）。调用方须持 `self.lock`，使「可撤」判定与随后的 `apply_undo` 之间状态稳定
        （token 仅由同会话另一轮 turn 或 apply_undo 改、二者都要 `self.lock`）。
        """
        return bool(token) and token == self._undo_token and bool(self._undo_journal)

    def apply_undo(self, token: str) -> dict | None:
        """撤销本轮写（决策P4.5-13）：乐观回放最近一个可写 turn 的写日志，返回 `{undone, conflicts}`。

        **假设调用方已持 `self.lock` 与进程 `write_lock`**（端点按 `conversation.lock → write_lock`
        统一锁序取，§5/§7，评审 High——绝不反序）。token 不匹配 / 无可撤销 → `None`（端点转 409）。
        逐文件**乐观校验**当前内容哈希 == 本 turn 写后哈希：相等才还原（写前字节，None=删新建页）、
        否则跳过并计入 `conflicts`（该文件已被后续写改动，决策P4.5-13）。撤销一次性、回放后失效。
        """
        if not self.can_undo(token):
            return None
        from .policy_fs import hash_file  # 局部引入，避免顶部循环面

        undone: list[str] = []
        conflicts: list[str] = []
        for path, (before, after_hash) in self._undo_journal.items():
            rel = path.relative_to(self._kb_resolved).as_posix()
            if hash_file(path) != after_hash:  # 已被后续写改动 → 跳过、记冲突
                conflicts.append(rel)
                continue
            restore_path(path, before)
            undone.append(rel)
        # 一次性：回放后令本 token 失效（不做全局时间旅行，决策P4.5-13）。
        self._undo_token = None
        self._undo_journal = {}
        return {"undone": undone, "conflicts": conflicts}

    def request_stop(self) -> bool:
        """请求打断当前在飞的 turn（停止按钮调）。无在飞 turn → 返回 False。

        `CancellationToken.cancel()` 自身线程安全、幂等，可在事件循环线程直接调；executor 线程随后在
        下个检查点抛 `AgentCancelledError`，arun 经 future 把它抛回 turn（已流出的 token 留在前端气泡）。
        **不** await 收尾——`turn` 内的 `await arun` 会持锁等到线程真正结束。

        三态：① **有**活跃令牌（持锁轮）→ 幂等打断它：未取消则 cancel，已取消则直接返回 True，
        **绝不**落到待停分支（否则重复 stop 会被排队轮误兑现、把下一轮也停掉，codex 评审）；并发时
        活跃令牌即正在流式的持锁轮、非排队轮。② **无**活跃令牌但有轮正在起跑（`begin_turn` 已标记、
        令牌尚未装上）→ 记待停，turn 进锁即兑现（关首轮 start→装令牌窗口的竞态）。③ 无在飞轮（idle）
        → False。待停只在「无任何活跃令牌」时设，故绝不殃及已装令牌的排队/活跃轮。
        """
        token = self._cancel_token
        if token is not None:
            # 持锁活跃轮：幂等打断（已 cancel 则什么都不做），不设待停——重复 stop 不外溢到排队轮。
            if not token.is_cancelled:
                token.cancel("user-stop")
            return True
        if self._inflight > 0 and not self.closed:
            self._stop_requested = True  # 令牌尚未装上：记待停，turn 进锁后立即兑现
            return True
        return False

    def _save(self) -> None:
        """把本会话当前 `messages` 落 `<kb>/.agentao/sessions/`（每轮新建一份快照，决策P4.2-1/2）。

        **先 best-effort prune 本会话旧快照、再 save**（让它一般只占一份文件、不冲掉别人 10 文件槽）。
        prune-在-前是为不误伤他会话：先删净本会话旧份，`save_session` 写新份时目录不会因「旧+新」
        瞬时顶到 11、触发 `_rotate_sessions` 把别的会话挤出（save-后-prune 在已满 10 时每次更新都会
        误淘汰别人，更糟，回应 codex 轮转评审，§2.1）。prune 失败不阻断 save（见 `_prune_old_snapshots`）。
        无全局锁、不追求「文件数=会话数」硬不变量（§2.1）；会话落 `.agentao/`、不碰 `raw/`，故零写串行。
        """
        active = list(self.agent.skill_manager.get_active_skills().keys())  # 镜像 cli/session.py
        _prune_old_snapshots(self._kb, self.id)
        save_session(
            self.agent.messages,
            self._model,
            active_skills=active,
            session_id=self.id,
            project_root=self._kb,
        )

    def info(self) -> dict:
        """会话级只读自省（喂 `/status /context /skills /tools /mode`，决策P4.4-2）。

        只读 agentao 文档化自省面（`get_current_model`/`context_manager.get_usage_stats`/
        `skill_manager`/`tools.list_tools`），**不改任何 agent 状态、不试调工具、零 LLM**。
        先把 `messages` 拍成快照（`list(...)`）再用：避免与并发在飞 turn 追加消息时
        `get_usage_stats` 边迭代边被改大小。端点经 `to_thread` 卸载调用（token 估算非廉价）。
        """
        msgs = list(self.agent.messages)  # 快照：隔离并发 turn 对 agent.messages 的追加
        sm = self.agent.skill_manager
        active = sm.get_active_skills()  # name -> {...}
        skills = {
            "active": list(active.keys()),
            "available": [
                {"name": n, "description": sm.get_skill_description(n), "active": n in active}
                for n in sm.list_available_skills()
            ],
        }
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "requires_confirmation": bool(getattr(t, "requires_confirmation", False)),
                "blocked": _blocked_in_mode(t, self._mode),
            }
            for t in sorted(self.agent.tools.list_tools(), key=lambda t: t.name)
        ]
        return {
            "id": self.id,
            "title": self.title,
            "turns": self.turns,
            "live": True,
            "model": self.agent.get_current_model(),
            "mode": self._mode,  # P4.5：报当前会话真实姿态（read-only / workspace-write）
            "messages": len(msgs),
            # context：headline（messages-only / api）+ 含 system prompt & tools schema 的精确 breakdown，
            # 完全对齐 agentao get_conversation_summary（否则 system/tools 分项被低报，codex 评审）。
            "context": _context_stats(self.agent, msgs),
            "skills": skills,
            "tools": tools,
        }

    def close(self) -> None:
        """释放 agent 资源；进程内会话被丢弃 / 删除时调用（删除端点持本会话锁时调，故 closed
        置位与 turn 的复检串行）。`Agentao.close` 是文档化嵌入面，自身已吞内部错误，这里再裹一层
        只为隔离意外（绝不连累删除请求）。"""
        self.closed = True
        try:
            self.agent.close()
        except Exception:  # noqa: BLE001 — 关闭失败不应连累请求
            pass


class ConversationStore:
    """内存会话表 + 盘上 catalog 懒恢复（P4.2，决策P4.2-1/3）。

    `persist` 开（默认）时：每会话每轮落 `<kb>/.agentao/sessions/`，`GET` 合并内存 ∪ 盘上 catalog
    （即时 `list_sessions`、按 `session_id` 去重、过滤 `SKILL_NAME`），打开冷会话时才 `restore`
    （懒恢复，off-loop）。`persist` 关（`--no-session-persist`）时退回 P4 纯内存：不落盘、不读盘。
    """

    def __init__(
        self,
        kb: Path,
        default_model: str | None,
        *,
        persist: bool = True,
        default_mode: str = "read-only",
        write_gate: WriteGate | None = None,
    ) -> None:
        self._kb = kb
        self._default_model = default_model
        self._persist = persist
        # 新会话开局姿态 = 进程 --mode 默认（决策P4.5-8：恢复不回放盘上姿态、用进程默认）。
        self._default_mode = default_mode
        self._write_gate = write_gate  # 进程级单写者协调，注入每个会话
        self._convs: dict[str, Conversation] = {}
        # create/restore 经 anyio.to_thread 卸载（构造慢），故跑在**线程池线程**——字典写必须用
        # threading.Lock 护住，否则两个并发新建/恢复会撞 id、互相覆盖会话。
        self._lock = threading.Lock()

    @property
    def default_mode(self) -> str:
        """新会话/恢复的开局姿态。供端点在 create **前**判「新 turn 是否会写」（评审 P2）。"""
        return self._default_mode

    def create(self, model: str | None = None) -> Conversation:
        """新建会话（含一次性问答=单轮）。超出**硬**上限抛 RuntimeError，由端点转 503。

        锁全程持有（含较慢的 agent 构造），使 cap 是真正的硬上限——否则并发新建会各自越过
        容量检查再一起插入，绕过内存上界（决策P4-8 要"硬上限"）。本地单用户下新建罕见，构造期
        短暂阻塞并发 get/list 可接受；为此换得简单且无并发绕过的实现。
        """
        conv_model = model if model is not None else self._default_model
        with self._lock:
            if len(self._convs) >= MAX_CONVERSATIONS:
                raise RuntimeError(
                    f"内存会话数已达上限 {MAX_CONVERSATIONS}，请先 DELETE 一些会话。"
                )
            # id 改用 agentao 的稳定 UUID（写进每份快照的 session_id）：进程内自增计数器重启归零、
            # 与盘上对不上无法续；UUID 跨重启稳定、读/删/续全归口同一 id（决策P4.2-2）。
            cid = str(uuid.uuid4())
            conv = Conversation(
                cid,
                self._kb,
                conv_model,
                persist=self._persist,
                mode=self._default_mode,
                write_gate=self._write_gate,
            )
            self._convs[cid] = conv
            return conv

    def get(self, cid: str) -> Conversation | None:
        with self._lock:
            return self._convs.get(cid)

    def live_count(self) -> int:
        """内存现存会话数（喂 `GET /api/info` 的 `conversations` 计数，零盘读）。"""
        with self._lock:
            return len(self._convs)

    def cold_info(self, cid: str) -> dict | None:
        """盘上-only 会话的部分自省（决策P4.4-7）：title/model/messages 取自即时 catalog，
        `context`/`skills`/`tools` 置 `null`、`live:false`，**不建 agent**（纯自省不值当一次重恢复）。

        过 P4.2 §2.2 两道精确闸（规范全 UUID + `_disk_session` 作用域）——非 Web / 未知 / 非规范 /
        持久化关下盘上 id → `None`（端点据此 404）。catalog 条目已含 `model`/`message_count`/`title`，
        故连消息体都不必再读，`turns` 不可廉价得到则留 `null`（续聊转 live 后即有真实轮次）。
        """
        if not self._persist:  # 持久化关时等价纯内存：盘上 id 一律视未知
            return None
        if not _is_canonical_uuid(cid):  # 闸①：非规范 id 绝不喂前缀匹配
            return None
        entry = self._disk_session(cid)  # 闸②：盘上须有此精确 session_id 的 Web 会话
        if entry is None:
            return None
        return {
            "id": cid,
            "title": entry.get("title") or "（空）",
            "turns": None,  # 不建 agent / 不载消息：轮次留空，续聊转 live 后即有
            "live": False,
            "model": entry.get("model"),
            "mode": self._default_mode,  # 冷会话恢复用进程默认姿态（决策P4.5-8）
            "messages": entry.get("message_count", 0),
            "context": None,
            "skills": None,
            "tools": None,
        }

    def messages_for(self, cid: str) -> list[dict] | None:
        """返回某会话的原始 `messages` 供 UI 回放气泡：内存命中读内存，否则**读盘但不建 agent**。

        与 `restore` 的区别：这是**纯查看**路径，冷会话只 `load_session` 取消息、不构造 LLM
        client（避免"点开浏览历史"就建一堆 agent 顶满 MAX_CONVERSATIONS）；真正续聊时再由
        `POST /api/chat` 走 `restore` 懒建。冷读同样过 §2.2 两道精确闸（规范 UUID + `_disk_session`
        作用域）。未知 / 非规范 / 非 Web 会话 / 持久化关下盘上 id → None（端点据此 404）。
        """
        conv = self.get(cid)
        if conv is not None:
            return list(conv.agent.messages)  # 内存态：含本会话全部跨轮消息
        if not self._persist:  # 持久化关时不读盘（同 restore，等价纯内存）
            return None
        if not _is_canonical_uuid(cid) or self._disk_session(cid) is None:
            return None
        try:
            messages, _model, _ = load_session(cid, project_root=self._kb)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None  # 竞态/坏文件 → 当未知 id（404）
        return messages

    def _disk_session(self, cid: str) -> dict | None:
        """即时 catalog 精确匹配源（§2.2 闸②）：`session_id` 全等 + 属 Web 只读会话才认。

        扫即时 `list_sessions(kb)`（newest-first，本地单用户 ≤十几份、可忽略）找
        `session_id == cid` **且** `SKILL_NAME in active_skills` 的条目——把「身份精确」与
        「作用域归属」一次性夹死，不依赖底层前缀语义恰好收敛，也把 `agentao` CLI 落的非 Web 会话
        拦在外（决策P4.2-6/7）。
        """
        for e in list_sessions(self._kb):
            if e.get("session_id") == cid and SKILL_NAME in (e.get("active_skills") or []):
                return e
        return None

    def restore(self, cid: str) -> Conversation | None:
        """懒恢复盘上的只读会话（端点经 anyio.to_thread.run_sync 调，off-loop 慢路径）。

        含 `load_session`（磁盘）+ `Conversation` 构造（LLM client）两个慢操作。load 前过 §2.2 两道
        精确闸（规范全 UUID + 即时 catalog 精确匹配 + `SKILL_NAME` 作用域），杜绝前缀/时间戳 fallback
        误命中错会话或越作用域；恢复走与新建**同一只读构造**、只激活 `SKILL_NAME`、**不**回放盘上
        任意 `active_skills`（决策P4.2-4/6）。返回 None → 端点 404；内存满抛 RuntimeError → 端点 503。
        """
        if not self._persist:  # 持久化关时不读盘（破坏「--no-session-persist 等价纯内存」）
            return None
        if not _is_canonical_uuid(cid):  # 闸①：非规范 id 当未知 id，绝不喂前缀匹配的 load_session
            return None
        if self._disk_session(cid) is None:  # 闸②：盘上无此精确 session_id 的 Web 会话 → 404
            return None
        try:  # catalog 与文件间有竞态：命中后文件可能刚被删/轮转/读坏
            messages, _model, _ = load_session(cid, project_root=self._kb)  # 全 UUID + 已确认存在
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None  # 竞态/坏文件 → 当未知 id（404），**绝不**冒泡成流式 error
        with self._lock:  # 同 create：构造慢但本地单用户罕见，换无并发绕过
            existing = self._convs.get(cid)  # double-check：并发已 rebuild 同一 id → 复用、丢弃本次
            if existing is not None:
                return existing
            if len(self._convs) >= MAX_CONVERSATIONS:  # 恢复同样受内存硬上限约束
                raise RuntimeError(
                    f"内存会话数已达上限 {MAX_CONVERSATIONS}，请先 DELETE 一些会话。"
                )
            # **不**恢复盘上持久的 model（镜像 agentao PR #81 "don't restore persisted model on
            # resume"）：快照只存 model **名**、不存其 provider（api_key/base_url 从不落盘）。把这个
            # 名字重新绑到当前进程恰好在用的 provider 上，会得到不一致的 (provider, model) 对——例如
            # 在 provider A 存的模型在当前 provider B 上根本不存在，只在下次 LLM 调用时才炸。故恢复
            # 一律用与新建**同一**的当前进程模型（self._default_model，= --model / 环境发现），保持已
            # 一致的 (provider, model)；盘上的 _model 仅留作参考、不回绑。
            # 恢复用**当前进程默认姿态**、不回放盘上姿态（盘上不存运行时姿态，决策P4.5-8）。
            conv = Conversation(
                cid,
                self._kb,
                self._default_model,
                persist=self._persist,
                mode=self._default_mode,
                write_gate=self._write_gate,
            )
            conv.agent.messages = messages  # 镜像 agentao cli/commands/sessions.py 的 resume
            # 只认构造已激活的 SKILL_NAME，**不**回放盘上任意 active_skills（扩大姿态，决策P4.2-4/6）
            conv.turns = len([m for m in messages if m.get("role") == "user"])  # 还原轮次
            first_user = next(
                (m.get("content") for m in messages if m.get("role") == "user"), ""
            )
            # 回填标题：否则 live 视图（内存优先合并）会把刚续聊的历史会话标题显示成空。
            conv.title = (first_user if isinstance(first_user, str) else "")[:50] or "（空）"
            self._convs[cid] = conv
            return conv

    def delete(self, cid: str) -> bool:
        """内存命中支：丢内存对象 + best-effort 删盘（决策P4.2-5）。返回是否命中内存。

        live 对象已确属 Web 会话、其 id 即自身 `uuid4()`，故**不**过 `_disk_session`——否则盘文件
        已被 rotate 掉的 live 会话会删不掉（§3.3）。盘上副本可能早被 rotate 掉，故 delete_session
        best-effort：删不到 / 无盘文件都不报错。`persist` 关时只删内存、不碰盘（§3.3）。
        """
        with self._lock:
            conv = self._convs.pop(cid, None)
        if conv is None:
            return False
        conv.close()  # 慢（可能 MCP 断开等），锁外
        if self._persist:
            try:
                delete_session(cid, project_root=self._kb)  # best-effort 级联删盘
            except OSError:
                pass
        return True

    def delete_disk(self, cid: str) -> bool:
        """disk-only 支：须规范全 UUID + `_disk_session` 命中才删盘，否则 False→端点 404（§3.3）。

        这才是防 `delete_session` 前缀误删一串 / 删到 `agentao` CLI 非 Web 会话的闸（§2.2）。
        `persist` 关时短路不读 catalog、不碰盘（disk-only id 一律 404，等价纯内存）。
        """
        if not self._persist:
            return False
        if not _is_canonical_uuid(cid) or self._disk_session(cid) is None:
            return False
        try:
            return delete_session(cid, project_root=self._kb)
        except OSError:
            return False

    def list(self) -> list[dict]:
        """合并视图：内存现存 ∪ 盘上即时 catalog（按 `session_id` 去重，内存态优先，决策P4.2-3）。

        即时 `list_sessions(kb)`（不缓存，≤十几份文件可忽略）、过滤 `active_skills` 含 `SKILL_NAME`
        （库级目录可能含 `agentao` CLI 非 Web 会话）、对外 id=`session_id`（**不**暴露 `path.stem`）。
        live 条目带真实 `turns`，冷条目带 `messages`=消息总数（非轮次，`list_sessions` 给 message_count）。
        """
        disk: dict[str, dict] = {}
        if self._persist:
            for e in list_sessions(self._kb):  # newest-first
                sid = e.get("session_id")
                if sid and sid not in disk and SKILL_NAME in (e.get("active_skills") or []):
                    disk[sid] = e  # 首见即最新（去重）
        with self._lock:
            convs = list(self._convs.values())  # 锁内只拍快照，避免与并发 create 撞"字典改大小"
        result: list[dict] = []
        seen: set[str] = set()
        for c in convs:  # 内存态优先
            seen.add(c.id)
            entry = disk.get(c.id)
            result.append(
                {
                    "id": c.id,
                    "title": c.title,
                    "updated_at": entry.get("updated_at") if entry else None,
                    "live": True,
                    "turns": c.turns,
                }
            )
        for sid, e in disk.items():  # 盘上独有（冷会话），保 newest-first
            if sid in seen:
                continue
            result.append(
                {
                    "id": sid,
                    "title": e.get("title") or "（空）",
                    "updated_at": e.get("updated_at"),
                    "live": False,
                    "messages": e.get("message_count", 0),
                }
            )
        return result
