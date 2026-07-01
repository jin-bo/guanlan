"""嵌入式 chat 的共享底座（P4）：常量 + 进程级 logger + 无状态 helper + 召回工具工厂。

从 `chat.py` 析出，使 `Conversation` / `ConversationStore` 各自成文件。本模块**无任何包内反向
依赖**（不 import `chat`/`conversation`/`conversation_store`），故可被它们安全直接引入、不成环。
`chat.py` 再把这些名字**原样再导出**，保持 `from .chat import …` / `chatmod.<helper>` 的既有
公开面（含测试猴补点）不变。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import uuid
from collections.abc import Callable
from pathlib import Path

from agentao.tools.base import Tool

from ..check import Violation
from ..search import CorpusCache, search_result_dict

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

# 内存会话数硬上限**默认值**：超出拒新建（决策P4-8：v1 不上 LRU，仅一个保守上界给内存设界）。
# P4.9 起改为 `ConversationStore(max_conversations=…)` 形参可配（决策P4.9-18），多用户部署可调高；
# 本常量仅作默认，仍被 `create_app`/`serve`/CLI 一路透传（直读它的旧点已归口 self._max_conversations）。
MAX_CONVERSATIONS = 100

# 内存会话 idle 回收 TTL（秒，决策P4.9-6）：久无活动（> 此值）且无在飞 turn 的会话在「新建/恢复」
# 锁内惰性淘汰，缓解多并发用户顶满 max_conversations。时间源 time.monotonic（免墙钟跳变）、可注入
# （测试决定性）。默认 30 min；置 None 关闭回收（纯保留旧硬上限语义）。
IDLE_TTL_SECONDS = 30 * 60

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


# guanlan_search 工具的稳定元数据（每实例同名/同描述/同 schema；只 cache/wiki 闭包不同）。
_GUANLAN_SEARCH_NAME = "guanlan_search"
_GUANLAN_SEARCH_DESC = (
    "对本知识库 wiki/ 下的内容页做确定性整页召回（BM25 + 中文 2-gram + 标题/别名加权），"
    "按相关度降序返回候选页路径 + 片段。**优先用它召回候选页**再读取综合，比裸 grep 更全"
    "（命中正文、别名、跨义）。返回 JSON 字符串：{ok, query, pages_searched, results:["
    "{page,title,type,score,snippet}]}。纯读、不写盘。"
)
_GUANLAN_SEARCH_PARAMS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "检索词（关键词/短语；中文按 2-gram 切分）"},
        "limit": {
            "type": "integer",
            "description": "召回条数（默认 10，须 ≥ 1）",
            "default": 10,
        },
    },
    "required": ["query"],
}


def make_guanlan_search_tool(search_cache: CorpusCache, *, wiki: Path) -> Tool:
    """工厂：**每个 `Conversation` 新建一个** `guanlan_search` 工具实例，只共享同一个
    `CorpusCache`（决策P5.1-6，§5）。

    为何工厂、为何每会话新实例：agentao 在 `register_extra_tools` 时把 `working_directory`/
    `filesystem`/`shell` 等 **per-agent 绑定写到工具实例上**（见 `tools/base._BaseTool`），跨会话
    复用同一实例会让后注册会话**覆盖**前一会话的绑定。故每会话用本工厂新建实例；`search_cache`
    与固定 `wiki` 路径由闭包捕获——**不**依赖 agentao 绑到实例的 `working_directory` 取路径
    （少一层隐式耦合，两参在 `create_app` 期已知、对全会话恒定）。

    `is_read_only=True` 是硬要求：否则 agentao 在只读姿态按 `tool_planning._decide`
    （`readonly_mode and not is_read_only → DENY`）会把它和 shell 一起拦掉，只读会话依旧召回不了
    （正是本阶段要解的问题）。该工具纯读 `wiki/`、不写盘，标只读名正言顺。
    """

    class _GuanlanSearchTool(Tool):
        # 注：同步 `execute`（非 `async_execute`）。`Agentao.arun` 已把整轮 `chat()` 放进
        # `loop.run_in_executor()`，故本 execute 跑在该 turn 的 **chat-executor 线程**、**不**在
        # FastAPI 事件循环上——不会卡住 loop / SSE。代价是它占住该 turn 的 executor 线程整段时长
        # （全库 stat + 重建 + BM25），并持 CorpusCache 锁、与并发 /api/search 串行（§5，已接受）。
        def __init__(self) -> None:
            super().__init__()
            # 工厂参数落实例私有属性（等价闭包捕获、且可供自省/测试断言「共享 cache」）：cache/wiki
            # 在 create_app 期已知、对全会话恒定。**不**依赖 agentao 绑到实例的 working_directory 取
            # 路径（少一层隐式耦合）；下划线私有名与 agentao 绑定面（working_directory/filesystem/shell）
            # 无碰撞，注册时不会被覆盖。
            self._search_cache = search_cache
            self._wiki = wiki

        @property
        def name(self) -> str:
            return _GUANLAN_SEARCH_NAME

        @property
        def description(self) -> str:
            return _GUANLAN_SEARCH_DESC

        @property
        def parameters(self) -> dict:
            return _GUANLAN_SEARCH_PARAMS

        @property
        def is_read_only(self) -> bool:
            return True  # 硬要求：只读姿态不被 DENY（镜像 tool_planning._decide）

        def execute(self, *, query: str = "", limit: int = 10, **_kw) -> str:
            # 工具路径自校验 limit（决策P5.0-15）：HTTP 有 Query(ge=1) 兜底，工具是 LLM 填参、无此门。
            # `score` 对 limit<1 raise ValueError，故先 clamp 到 ≥1（坏类型也回落 10），不让它冒泡成
            # 工具崩溃。空/纯标点 query 不必额外管——score 对零 query-token 短路回空、安全。
            try:
                lim = max(1, int(limit))
            except (TypeError, ValueError):
                lim = 10
            # query 同样防坏类型（LLM 可能填 number/array/null）：非 str 一律 str() 归一，否则
            # tokenize→re.finditer 抛 TypeError（被 agentao executor 吞成错误串、召回静默失败）。
            if not isinstance(query, str):
                query = "" if query is None else str(query)
            # P5.3：`CorpusCache.search` 单一入口内部配好 corpus + 反链文档先验 + 打分（决策P5.3-4/5），
            # 无从漏传 inlinks 而静默丢重排；反链由语料签名 memo（签名变才 build_graph）。
            result = self._search_cache.search(self._wiki, query, limit=lim)
            # 工具契约 execute() -> str（执行器把返回值当 result_text）：返回 **JSON 字符串**，
            # 经 search_result_dict 单一归口、与 /api/search 字段同形（决策P5.1-4）。
            return json.dumps(search_result_dict(result), ensure_ascii=False)

    return _GuanlanSearchTool()


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
                obj = json.load(f)
            # 合法但非 dict 的快照（`[]`/`null`/标量）`.get` 会抛 `AttributeError`——它不在 catch
            # 元组内，会逃出循环、把**每轮 save 前**的本卫生步打崩（违 docstring「全程 best-effort」
            # 承诺，且形同 gbrain「整循环崩在 checkpoint 写」）。isinstance 守卫后非 dict 即跳过。
            sid = obj.get("session_id") if isinstance(obj, dict) else None
            if sid == session_id:
                p.unlink()
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            # 坏 UTF-8 字节的快照（半写截断多字节 / 非 UTF-8 编辑器存）→ `json.load` 抛
            # `UnicodeDecodeError`（ValueError 子类，不在 JSONDecodeError 内）；不并入则它逃出**每轮
            # save 前**的卫生步、令 `_save` 静默不落盘（`read_goal` 同样容 ValueError，口径对齐）。
            continue  # 读坏 / 删失败：跳过，best-effort


def _violation_key(v: Violation) -> tuple:
    """check violation 的稳定身份键（页+类+详情）：用于「本轮新增」差集与基线缓存（决策P4.5-4）。"""
    return (v.page, v.kind, v.detail)


def _lean_messages(messages: list[dict]) -> list[dict]:
    """落盘前把多模态 content 列表压成纯文本（chahua 不变量：base64 **绝不入快照**）。

    图像轮在 `agent.messages` 里是 OpenAI 形态的 content 列表（text part + `image_url` data-URL
    part，单图 base64 可达 ~27MB）；会话快照每轮重写一份，照存会让 `.agentao/sessions/` 膨胀到
    不可用。这里只取 text part 拼接（text 里已含与 `_source` 同 uri 的 `<attachment>` 标签，文本
    引用不丢）——与 agentao 模型拒图后的降级重写**同形**，恢复出的会话等价于「降级过的历史」。
    不可变处理：返回新列表/新 dict，绝不改 `agent.messages` 本体（live 会话保留视觉上下文）。
    """
    out: list[dict] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            text = "\n".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
            )
            m = {**m, "content": text}
        out.append(m)
    return out


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


def disable_agent_log() -> None:
    """摘掉 `_logger` 上已挂的 agentao.log file handler，并清幂等缓存（`--no-agent-log` / reader 用）。

    `_logger` 是**进程级共享单例**：若同进程先前 `serve(agent_log=True)` 挂过 `RotatingFileHandler`，
    后续 `serve(reader=True / agent_log=False)` 仅「跳过 configure」**并不会**摘掉旧 handler——reader
    会话仍续写 `<kb>/agentao.log`，破「默认 KB 零字节写入」契约（评审 codex P2）。本函数显式 remove +
    close 这些 file handler、清空 `_agent_log_paths`，使「关日志」真生效。`serve` 阻塞跑 uvicorn，同
    进程同一时刻只一个活跃 server，故清空全部 file handler 不会误伤并发 server。"""
    for h in list(_logger.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            _logger.removeHandler(h)
            h.close()
    _agent_log_paths.clear()


# 当前 turn 的事件发射器：emit(kind, data)。kind ∈ {"token"}（done/error 由端点补）。
Emit = Callable[[str, object], None]
