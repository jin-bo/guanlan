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
import logging
import logging.handlers
import threading
from collections.abc import Callable
from pathlib import Path

from agentao.embedding import build_from_environment
from agentao.embedding.compat import build_compat_transport
from agentao.permissions import PermissionMode

from ..skill import SKILL_NAME, ensure_skill_available

# 嵌入会话共享的 logger（坑③：注入它 = 我们自管日志栈）。默认由 configure_agent_log 给它挂
# 一个落 <wd>/agentao.log 的 file handler；不挂时无 handler、不写文件（如测试 / --no-agent-log）。
_logger = logging.getLogger("guanlan.web.chat")

# 已接上 agentao.log 的库根（幂等：重复 serve/create_app 不重挂 handler，避免每行写多遍）。
_agent_log_paths: set[str] = set()

# 内存会话数硬上限：超出拒新建（决策P4-8：v1 不上 LRU，仅一个保守上界给内存设界）。
MAX_CONVERSATIONS = 100


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
    """一会话一 `Agentao` 对象 + 一把 `asyncio.Lock`，仅存活于 server 内存（v1 不落盘）。"""

    def __init__(self, cid: str, kb: Path, model: str | None) -> None:
        self.id = cid
        self.lock = asyncio.Lock()  # 同一会话两轮不并发跑同一 agent 对象
        self.title: str | None = None
        self.turns = 0
        self.closed = False  # 删除后置位（锁内读写）；拦下"已接受但尚未起跑"的排队 turn
        self._emit: Emit | None = None  # 当前 turn 的线程安全 emit；transport 固定回调转发到它

        ensure_skill_available(kb)  # 嵌入式同样需保证 skill 可发现（坑②前置）

        opts: dict = dict(
            working_directory=kb,
            logger=_logger,
            # token 流式的**唯一活线**：transport 在构造期冻结（EventType.LLM_TEXT→chunk）。
            # 事后改 self.agent.llm_text_callback 是死代码（只读存档，运行时不重读）。
            transport=build_compat_transport(llm_text_callback=self._on_token),
        )
        if model is not None:
            # --model 仅在给定时入 overrides：显式 model=None 会盖掉 .env 发现的模型、
            # 触发 agent.py 的 "model required" ValueError（默认不带 --model 的常路就崩）。
            opts["model"] = model

        self.agent = build_from_environment(**opts)
        # 只读姿态两点同步置位（缺第二步 = 没真正只读，照搬 cli/run.py）：
        self.agent.permission_engine.set_mode(PermissionMode.READ_ONLY)
        self.agent.tool_runner.set_readonly_mode(True)
        self.agent.skill_manager.activate_skill(
            SKILL_NAME, task_description="观澜 Web 只读问答"
        )

    def _on_token(self, chunk: str) -> None:
        """transport 固定回调，**在 arun 的 executor 线程里跑**；lock 串行化同会话各 turn，单槽无竞态。"""
        if self._emit is not None:
            self._emit("token", chunk)

    async def turn(self, msg: str, emit: Emit) -> str:
        """跑一轮：把 token 经 emit 推前端，返回完整答案；`asyncio.Lock` 串行同会话各轮。"""
        async with self.lock:
            # StreamingResponse 在路由返回**后**才起跑 turn；其间并发 DELETE 可能已拿锁删本会话、
            # close 了 agent。锁内复检 closed，拒绝在已关闭 agent 上跑（端点据异常发 error 事件）。
            if self.closed:
                raise RuntimeError("会话已删除")
            loop = asyncio.get_running_loop()

            def thread_safe_emit(kind: str, data: object) -> None:
                # _on_token 落在线程池线程，碰 asyncio.Queue 必须 call_soon_threadsafe 桥回 loop
                # （直接跨线程 put_nowait 会丢 token / 卡流 / 偶发崩）。
                loop.call_soon_threadsafe(emit, kind, data)

            self._emit = thread_safe_emit
            self.turns += 1
            if self.title is None:
                self.title = msg.strip()[:50] or "（空）"
            try:
                return await self.agent.arun(msg)  # arun = chat 的 async 包装（内部 run_in_executor）
            finally:
                self._emit = None

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
    """内存会话表：进程退出即清（v1 不调 persist/load_session，决策P4-8）。"""

    def __init__(self, kb: Path, default_model: str | None) -> None:
        self._kb = kb
        self._default_model = default_model
        self._convs: dict[str, Conversation] = {}
        self._counter = 0
        # create 经 anyio.to_thread 卸载（构造慢），故跑在**线程池线程**——计数器自增 + 字典写
        # 必须用 threading.Lock 护住，否则两个并发新建会丢增量、撞 id、互相覆盖会话。
        self._lock = threading.Lock()

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
            self._counter += 1
            cid = str(self._counter)
            conv = Conversation(cid, self._kb, conv_model)
            self._convs[cid] = conv
            return conv

    def get(self, cid: str) -> Conversation | None:
        with self._lock:
            return self._convs.get(cid)

    def delete(self, cid: str) -> bool:
        with self._lock:
            conv = self._convs.pop(cid, None)
        if conv is None:
            return False
        conv.close()  # 慢（可能 MCP 断开等），锁外
        return True

    def list(self) -> list[dict]:
        with self._lock:
            convs = list(self._convs.values())  # 锁内只拍快照，避免与并发 create 撞"字典改大小"
        return [{"id": c.id, "title": c.title, "turns": c.turns} for c in convs]
