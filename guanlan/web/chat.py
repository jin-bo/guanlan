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
from agentao.embedding import (
    build_from_environment,
    delete_session,
    list_sessions,
    load_session,
    save_session,
)
from agentao.embedding.compat import build_compat_transport
from agentao.permissions import PermissionMode

from ..skill import SKILL_NAME, ensure_skill_available

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

    def __init__(self, cid: str, kb: Path, model: str | None, *, persist: bool) -> None:
        self.id = cid
        self._kb = kb
        self._model = model  # _save 用（save_session 的 model 参数）；恢复时 = load 出的 model
        self._persist = persist
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
                answer = await self.agent.arun(msg)  # arun = chat 的 async 包装（内部 run_in_executor）
            finally:
                self._emit = None
            if self._persist:
                # 仅成功轮落盘、off-loop 不堵事件循环；任何异常（prune / save_session）只记日志，
                # **绝不**让它冒泡把已成功的 arun 答案翻成 error（失败不毁答案，同 §4.4 降级精神）。
                try:
                    await anyio.to_thread.run_sync(self._save)
                except Exception:  # noqa: BLE001 — 落盘失败仅记日志，本轮答案照常返回
                    _logger.warning("会话 %s 落盘失败，本轮未持久化", self.id, exc_info=True)
            return answer

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

    def __init__(self, kb: Path, default_model: str | None, *, persist: bool = True) -> None:
        self._kb = kb
        self._default_model = default_model
        self._persist = persist
        self._convs: dict[str, Conversation] = {}
        # create/restore 经 anyio.to_thread 卸载（构造慢），故跑在**线程池线程**——字典写必须用
        # threading.Lock 护住，否则两个并发新建/恢复会撞 id、互相覆盖会话。
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
            # id 改用 agentao 的稳定 UUID（写进每份快照的 session_id）：进程内自增计数器重启归零、
            # 与盘上对不上无法续；UUID 跨重启稳定、读/删/续全归口同一 id（决策P4.2-2）。
            cid = str(uuid.uuid4())
            conv = Conversation(cid, self._kb, conv_model, persist=self._persist)
            self._convs[cid] = conv
            return conv

    def get(self, cid: str) -> Conversation | None:
        with self._lock:
            return self._convs.get(cid)

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
            conv = Conversation(cid, self._kb, self._default_model, persist=self._persist)
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
