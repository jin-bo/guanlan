"""内存会话表 + 盘上 catalog 懒恢复 `ConversationStore`（P4.2，决策P4.2-1/3）。

从 `chat.py` 析出。常量/helper 来自 `chat_support`（直接引入、不成环），`Conversation` 来自
`conversation`；测试在 `chat` 模块上猴补的接缝（`MAX_CONVERSATIONS` / `load_session`）运行期经
`chat.<name>` 取用——`_cap()` 仍读 `chat.MAX_CONVERSATIONS`（call-time，使构造后改全局再触发
上限的既有测试照旧生效）。`from .conversation import …` / `from . import chat` 置于文件**末尾**
避开循环导入（见 `conversation.py` 同款说明）。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from agentao.embedding import delete_session, list_sessions

from ..search import CorpusCache
from ..skill import SKILL_NAME
from .chat_support import IDLE_TTL_SECONDS, _is_canonical_uuid
from .jobs import WriteGate


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
        max_conversations: int | None = None,
        idle_ttl: float | None = IDLE_TTL_SECONDS,
        search_cache: CorpusCache | None = None,
        confirm_mode: str = "ask",
        confirm_timeout: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._kb = kb
        self._default_model = default_model
        self._persist = persist
        # P5.1：共享的检索 CorpusCache（create_app 注入这一个）；透传到每个会话，由 Conversation
        # 用 make_guanlan_search_tool 新建**每会话一个**工具实例、只共享本 cache（§3.1/§5）。None=不注册。
        self._search_cache = search_cache
        # 新会话开局姿态 = 进程 --mode 默认（决策P4.5-8：恢复不回放盘上姿态、用进程默认）。
        self._default_mode = default_mode
        # P4.15：新会话开局 confirm 姿态（ask/auto，= 进程 --confirm 默认）+ 确认等待超时秒数
        # （--confirm-timeout）；透传每个会话（新建与懒恢复两路，决策P4.15-7）。
        self._confirm_mode = confirm_mode
        self._confirm_timeout = confirm_timeout
        self._write_gate = write_gate  # 进程级单写者协调，注入每个会话
        # 内存会话硬上限（决策P4.9-18）：取代直读模块常量 MAX_CONVERSATIONS，供多用户部署可配。
        # 存原值（可能 None=未指定）；**在 create/restore 取上限时**经 `_cap()` 解析——None 则读模块
        # 全局。这样既保「默认 100」，又让 `monkeypatch.setattr(chat, "MAX_CONVERSATIONS", n)` 这类既有
        # 测试（构造后改全局再触发上限）照旧生效，不被 def-time / 构造期绑死。
        self._max_conversations = max_conversations
        # idle 回收（决策P4.9-6）：注入时钟（默认 monotonic）+ TTL（None=关）；create/restore 锁内惰性扫。
        self._idle_ttl = idle_ttl
        self._clock = clock
        self._convs: dict[str, Conversation] = {}
        # create/restore 经 anyio.to_thread 卸载（构造慢），故跑在**线程池线程**——字典写必须用
        # threading.Lock 护住，否则两个并发新建/恢复会撞 id、互相覆盖会话。
        self._lock = threading.Lock()

    def _cap(self) -> int:
        """解析当前内存会话硬上限：构造未指定（None）则读模块全局（call-time，决策P4.9-18）。"""
        return chat.MAX_CONVERSATIONS if self._max_conversations is None else self._max_conversations

    def _reclaim_idle_locked(self) -> list[Conversation]:
        """惰性回收久无活动且无在飞 turn 的会话（决策P4.9-6）。**调用方须持 `self._lock`**。

        在 `create`/`restore` 取上限判定**之前**调：先腾出 idle slack，缓解多并发用户顶满
        `max_conversations` 后新用户被拒。跳过有在飞 turn 的会话（`is_idle` 守卫）——绝不淘汰正在
        流式回答者。`idle_ttl=None` 时短路（不回收，退回纯硬上限语义）。

        **只 pop、不 close**：返回被逐出的会话，由调用方在**释锁后** `close()`——`close()` 慢（可能
        MCP 断开），锁内做会把所有 get/create/restore/list 串行在网络拆连之后（与 `delete()` 同约定，
        见其「锁外 close」注释，评审 P4.9）。被回收会话的盘上快照（persist 开时）**不删**——仅从内存
        逐出、仍可懒恢复（reader 用 persist=False，无盘上副本，UX 悬崖见 §4 决策P4.9-6）。
        """
        if self._idle_ttl is None:
            return []
        now = self._clock()
        stale = [cid for cid, c in self._convs.items() if c.is_idle(now, self._idle_ttl)]
        evicted: list[Conversation] = []
        for cid in stale:
            conv = self._convs.pop(cid, None)
            if conv is not None:
                evicted.append(conv)
        return evicted

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
        evicted: list[Conversation] = []
        try:
            with self._lock:
                evicted = self._reclaim_idle_locked()  # 先腾 idle slack（决策P4.9-6）再判上限（仅 pop）
                cap = self._cap()
                if len(self._convs) >= cap:
                    raise RuntimeError(
                        f"内存会话数已达上限 {cap}，请先 DELETE 一些会话。"
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
                    search_cache=self._search_cache,
                    confirm_mode=self._confirm_mode,
                    confirm_timeout=self._confirm_timeout,
                    clock=self._clock,
                )
                self._convs[cid] = conv
                return conv
        finally:
            # 锁外 close 被逐会话（决策P4.9-6/评审 P4.9）：return/raise 都先退出 with（释锁）再跑本
            # finally，故 close() 的慢 MCP 拆连不占 store 锁、不阻塞并发 get/create/restore。
            for c in evicted:
                c.close()

    def get(self, cid: str) -> Conversation | None:
        with self._lock:
            conv = self._convs.get(cid)
            if conv is not None:
                # 任何按-id 命中都刷新活跃时间戳（锁内、与 reclaim 串行）：堵「端点 get() 拿到 conv →
                # 起 turn 前（begin_turn 尚未 +inflight）被并发 create/restore 的 reclaim 误逐、turn 撞
                # closed agent」这段竞态（评审 P4.9）。get 与 reclaim 都持 self._lock，故刷新原子可见：
                # 一旦本会话被取出即非 idle，后续 reclaim 不会逐它。/info·/stop·/mode 走 get 同样保活。
                conv.last_active = self._clock()
            return conv

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
            messages, _model, _ = chat.load_session(cid, project_root=self._kb)
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
            messages, _model, _ = chat.load_session(cid, project_root=self._kb)  # 全 UUID + 已确认存在
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None  # 竞态/坏文件 → 当未知 id（404），**绝不**冒泡成流式 error
        evicted: list[Conversation] = []
        try:
            with self._lock:  # 同 create：构造慢但本地单用户罕见，换无并发绕过
                existing = self._convs.get(cid)  # double-check：并发已 rebuild 同一 id → 复用、丢弃本次
                if existing is not None:
                    return existing
                evicted = self._reclaim_idle_locked()  # 先腾 idle slack（决策P4.9-6）再判上限（仅 pop）
                cap = self._cap()
                if len(self._convs) >= cap:  # 恢复同样受内存硬上限约束
                    raise RuntimeError(
                        f"内存会话数已达上限 {cap}，请先 DELETE 一些会话。"
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
                    search_cache=self._search_cache,  # 懒恢复同样带召回工具（§3.1，两路零漂移）
                    confirm_mode=self._confirm_mode,
                    confirm_timeout=self._confirm_timeout,
                    clock=self._clock,
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
        finally:
            for c in evicted:  # 锁外 close 被逐会话（同 create，慢 MCP 拆连不占 store 锁）
                c.close()

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


# 末尾导入（见模块 docstring）：`Conversation`（运行期构造）与 `chat` 模块对象（运行期经
# `chat.MAX_CONVERSATIONS` / `chat.load_session` 取测试可猴补的接缝）。置于类定义之后避开循环导入。
from . import chat  # noqa: E402
from .conversation import Conversation  # noqa: E402
