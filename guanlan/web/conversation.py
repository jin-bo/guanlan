"""单个嵌入式只读/可写会话 `Conversation`（P4，见 docs/P4-Web宿主.md §4.4 决策P4-8）。

从 `chat.py` 析出。**模块底座**（常量/logger/helper/召回工具工厂）来自 `chat_support`，可被直接
引入、不成环；而测试在 `chat` 模块上猴补的第三方接缝（`build_from_environment`/
`ensure_skill_available`/`run_check`/`save_session`）则在运行期经 `chat.<name>` 取用，使既有
`monkeypatch.setattr(chat, …)` 照旧生效（`from . import chat` 置于文件**末尾**，避开循环导入：
两模块互引时各自所需的顶层名在对方的「末尾导入」触发前均已定义）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import anyio
from agentao.cancellation import CancellationToken
from agentao.embedding.compat import build_compat_transport
from agentao.permissions import PermissionMode

from ..gate import REPAIR_PROMPT, _render_violations
from ..search import CorpusCache
from ..skill import SKILL_NAME
from .chat_support import (
    Emit,
    _blocked_in_mode,
    _context_stats,
    _lean_messages,
    _logger,
    _prune_old_snapshots,
    _violation_key,
    make_guanlan_search_tool,
)
from .jobs import WriteGate
from .policy_fs import (
    AgentaoSnapshot,
    make_policy_fs,
    restore_agentao,
    restore_path,
    snapshot_agentao,
)


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
        search_cache: CorpusCache | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.id = cid
        self._kb = kb
        self._search_cache = search_cache  # P5.1：共享的 CorpusCache（None=不注册召回工具，纯读测试）
        # idle 回收用时间戳（决策P4.9-6）：time.monotonic（免墙钟跳变）、可注入（测试决定性）。
        # 每轮起跑 / 控制操作刷新（见 begin_turn / request_stop）；store 在「新建/恢复」锁内据
        # `is_idle` 淘汰久无活动者。
        self._clock = clock
        self.last_active = clock()
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
        # 写后 check 基线缓存（决策P4.5-4 修订，性能）：上次见到的 wiki/ violation 键集 + 拍它时的
        # 写代际。可写 turn 起跑时若代际未变即复用、跳过重拍基线（同会话连续对话省每轮 ~0.36s）；
        # 代际变了（别处 ingest/heal、或本会话上轮改了 violation 集）或首轮 → 重新拍。None = 未拍。
        self._check_baseline: set[tuple] | None = None
        self._check_baseline_gen = -1

        chat.ensure_skill_available(kb)  # 嵌入式同样需保证 skill 可发现（坑②前置）

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

        # P5.1（§3.1/§5）：构造期注入 `guanlan_search` 召回工具——与 transport/filesystem 同在构造期
        # 一处装齐、无「构造后再补挂」时序窗口（决策P5.1-6）。**每会话新建一个实例**（工厂闭包捕获
        # 共享 cache 与固定 `wiki/`），只共享 cache、不共享实例（避免 agentao per-agent 绑定互相覆盖）。
        # 新建与懒恢复**共用本 __init__**，故恢复出的会话也带召回工具，两路零漂移。is_read_only=True
        # 使只读姿态下也不被 DENY——这正是只读 Web 会话首次拥有确定性召回入口（§1.2 死指令的解药）。
        if search_cache is not None:
            opts["extra_tools"] = [
                make_guanlan_search_tool(search_cache, wiki=kb / "wiki")
            ]

        self.agent = chat.build_from_environment(**opts)
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
        self.last_active = self._clock()  # 刷新活跃时间戳：本会话有轮起跑，idle 回收顺延（决策P4.9-6）

    def is_idle(self, now: float, ttl: float) -> bool:
        """供 store 在「新建/恢复」锁内判本会话是否可被 idle 回收（决策P4.9-6）。

        **必须无在飞 turn**（`_inflight == 0`）——否则会淘汰正在流式回答的会话；再要久无活动
        （`now - last_active > ttl`）。`now`/`ttl` 由 store 用其注入时钟与 TTL 给出，判定纯读、无副作用。
        """
        return self._inflight == 0 and (now - self.last_active) > ttl

    def end_turn(self) -> None:
        """端点在该轮彻底收尾（含 stopped/error）后调，与 `begin_turn` 配对。"""
        self._inflight -= 1
        # 收尾也刷新活跃时间戳（评审 codex P2）：begin_turn 只在**起跑**打点，若一轮跑得比 IDLE_TTL
        # 还久（长 LLM/工具轮），收尾瞬间 _inflight→0 而 last_active 仍是 30min 前的起跑值，下一个
        # create/restore 会立刻把这个**刚用完**的会话逐掉。故按「最后一次完成活动」重新打点。
        self.last_active = self._clock()
        if self._inflight <= 0:
            self._inflight = 0
            self._stop_requested = False  # 无在飞轮了：清掉未兑现的待停，绝不泄漏到下一轮

    async def turn(
        self,
        msg: str,
        emit: Emit,
        meta_out: dict | None = None,
        images: list[dict[str, str]] | None = None,
    ) -> str:
        """跑一轮：把 token 经 emit 推前端，返回完整答案；`asyncio.Lock` 串行同会话各轮。

        `images`（可选）：图像附件载荷 `[{data, mimeType, _source}, …]`，原样透传 `arun(images=)`
        走视觉通道（agentao 文档化嵌入面）；模型不支持视觉时由 agentao 自动降级为
        `<attachment uri= mimetype=/>` 标签文本重试（宿主不做能力探测）。`_source` = 消息里
        既有标签的 uri，降级后 prompt 前后引用一致。

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
            before_check: set[tuple] | None = None
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
                    # check 基线（决策P4.5-4 修订）：收尾只 surface 本轮**新增**的 violations——否则
                    # 库存量问题（数百条断链很常见）每个可写轮整批刷屏、把「本轮搞坏了什么」淹没。
                    # 基线即起跑时的 wiki/ violation 键集；**缓存复用**（性能）：写代际未变即复用上次
                    # 见到的集、跳过重拍（同会话连续对话省每轮 ~0.36s）；代际变了或首轮才真拍。
                    # 拍取失败 → before_check 留 None，收尾退化为全量呈现（绝不让基线毁本轮）。
                    gen = self._write_gate.wiki_generation if self._write_gate else 0
                    if self._check_baseline is not None and self._check_baseline_gen == gen:
                        before_check = self._check_baseline  # 复用：零 check 成本
                    else:
                        try:
                            r = await anyio.to_thread.run_sync(chat.run_check, self._kb / "wiki")
                            before_check = {_violation_key(v) for v in r.violations}
                            self._check_baseline = before_check
                            self._check_baseline_gen = gen
                        except Exception:  # noqa: BLE001 — 基线失败只降级呈现口径，不毁本轮
                            _logger.warning("会话 %s check 基线拍取失败", self.id, exc_info=True)
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
                # images 仅本轮有图时传（None 走纯文本路径，与 agentao chat() 契约一致）。
                answer = await self.agent.arun(
                    msg, cancellation_token=token, images=images or None
                )
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
                        await self._finalize_writable(before_agentao, before_check, meta_out)
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
        self,
        before_agentao: AgentaoSnapshot | None,
        before_check: set[tuple] | None,
        meta_out: dict | None,
    ) -> None:
        """可写 turn 收尾：层②还原 + 无条件写后 check + 撤销可用性，写进 `meta_out`(决策P4.5-3/4)。

        在 `write_lock` 内跑（权威 `wiki/` 门禁，§5）：① 先把被旁路改的 `AGENTAO.md` 还原（早于
        error SSE）；② 取本轮写日志、登记撤销 token（写日志非空即可用，**独立于 check**）；③ 无条件
        跑 `run_check(wiki)`——既不靠写日志、也不用 mtime/size 指纹（评审 High/Medium），覆盖 shell
        直写；呈现按「本轮新增」口径与起跑基线 `before_check` 做差（决策P4.5-4 修订，见 §3 步骤）。
        任何一步抛错只记日志，绝不连累已成功的答案（同 §4.4 降级精神）。
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

        # ③ 无条件写后 check（零 LLM、锁内一致状态；覆盖 shell 直写 wiki/）。**呈现按「本轮新增」
        # 口径**（决策P4.5-4 修订）：与起跑基线（同样锁内拍取）做差——`violations` 只装本轮新增、
        # 库存量进 `total`、本轮消除数进 `resolved`、`ok` = 本轮未新增。否则存量问题（数百条断链
        # 很常见）在每个可写轮整批刷屏，把本轮真正引入的问题彻底淹没。守卫强度不变：check 仍每轮
        # 全量跑、shell 直写引入的新问题照样落进差集。基线缺失（拍取失败）退化为全量呈现。
        check: dict | None = None
        try:
            result = await anyio.to_thread.run_sync(chat.run_check, self._kb / "wiki")
            after = {_violation_key(v) for v in result.violations}
            if before_check is not None:
                new = [v for v in result.violations if _violation_key(v) not in before_check]
                resolved = len(before_check - after)
            else:
                new = list(result.violations)
                resolved = 0
            # 刷新基线缓存为本轮收尾态（下轮起跑代际未变即复用、零重拍）。violation 集变了 →
            # bump 写代际：使**别的**可写会话缓存失效、下轮重拍（本会话已持最新 after，更新自己的 gen）。
            self._check_baseline = after
            if before_check is None or after != before_check:
                if self._write_gate is not None:
                    self._write_gate.bump_wiki_generation()
            self._check_baseline_gen = (
                self._write_gate.wiki_generation if self._write_gate else 0
            )
            check = {
                "ok": not new,  # 本轮口径：未新增即通过；整库现状看 total（==0 才是全绿）
                "total": len(result.violations),
                "resolved": resolved,
                "violations": [
                    {"page": v.page, "kind": v.kind, "detail": v.detail} for v in new
                ],
            }
            if new:
                # 「让 Agent 修复」用的下一轮消息：服务端用 gate.REPAIR_PROMPT 格式化（复用 ingest
                # 自愈同一薄 prompt，§3 决策P4.5-4）。只针对**本轮新增**——不驱动 agent 一上来
                # 修几百条存量（那是 heal/lint 的活）。前端把它当下一轮 user 消息发出即驱动修复。
                check["repair_prompt"] = REPAIR_PROMPT.format(
                    violations=_render_violations(new)
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
        self.last_active = self._clock()  # 控制操作也刷新活跃时间戳（决策P4.9-6：每轮问答/控制刷新）
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
        chat.save_session(
            _lean_messages(self.agent.messages),  # 多模态 base64 不入快照（见 _lean_messages）
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


# 末尾导入（见模块 docstring）：仅取 `chat` 模块对象、运行期经 `chat.<name>` 取测试可猴补的
# 第三方接缝（build_from_environment / ensure_skill_available / run_check / save_session）。置于
# 类定义之后，使「先 import conversation」与「先 import chat」两种顺序下都不触发半初始化 ImportError。
from . import chat  # noqa: E402
