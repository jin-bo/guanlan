"""FastAPI app + 路由（P4，见 docs/P4-Web宿主.md §4/§5）。

宿主自身**不做任何确定性/语义判断**：端点只负责"收 HTTP 请求 → 调既有包内函数或嵌入
agent → 序列化/转发结果"。阻塞调用一律卸到线程（零 LLM 报告经 `anyio.to_thread.run_sync`、
写作业 `ingest` 经单 worker 线程），绝不在事件循环里直接跑阻塞代码（决策P4-2）。

C1 仅落地骨架：`GET /` 返回随包 `index.html` + `/static/*` 静态挂载。后续提交按
§5 契约逐片加 `/api/pages`·`/api/page`·`/api/raw`（C2）、报告（C3）、`ingest`（C4）、`chat`（C5）。
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import anyio
from agentao.cancellation import AgentCancelledError
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from ..audit import (
    DEFAULT_LIMIT as AUDIT_DEFAULT_LIMIT,
)
from ..audit import (
    AuditRun,
    audit_result_dict,
    run_audit_result,
)
from ..check import format_report as _format_check
from ..check import run_check
from ..errors import EXIT_OK, EXIT_USAGE, GuanlanError
from ..graph import build_and_write_graph
from ..health import format_report as _format_health
from ..health import run_health
from ..heal import (
    HealRun,
    heal_result_dict,
    run_heal_result,
)
from ..ingest import run_ingest
from ..lint import MISSING_ENTITY_MIN_REFS
from ..lint import format_report as _format_lint
from ..lint import run_lint
from ..query import run_query
from ..paths import count_files_modified_since
from ..runtime import HEARTBEAT_INTERVAL_S, AgentRunner
from ..search import CorpusCache, search_result_dict, tokenize
from .chat import IDLE_TTL_SECONDS, MAX_CONVERSATIONS, ConversationStore
from .conversation import GoalActiveError
from .helpers import (
    _audit_preview,
    _heal_preview,
    _image_file_response,
    _list_pages,
    _list_raw,
    _message_text,
    _report_response,
    _safe_raw_file,
    _safe_raw_image,
    _safe_wiki_file,
    _sse,
    _wiki_image_src,
    _workspace_image_src,
)
from .jobs import JobQueue, WriteGate
from .parsefeed import (
    _BACKENDS,
    image_lint,
    parse_target,
    parse_upload,
    relocalize_commit,
)
from .promote import commit_promotion, prepare_promotion
from .rawfeed import (
    _atomic_write_raw,
    _check_text_admission,
    _safe_raw_target,
)
from .render import render_markdown, render_page
from .uploads import (
    MAX_UPLOAD_BYTES,
    _atomic_write_upload,
    _augment_with_attachments,
    _classify_upload,
    _safe_upload_file,
    _safe_workspace_target,
)
from .workspace import (
    _delete_workspace_scratch,
    _list_workspace,
    _rmtree_workspace_dir,
    _safe_workspace_dir,
    _safe_workspace_md,
    _safe_workspace_scratch,
)

# chat SSE 心跳间隔（秒）= 共用节拍 HEARTBEAT_INTERVAL_S 的本模块别名（保留独立名便于测试
# monkeypatch）：静默间隙（长工具调用 / 首 token 前思考）每隔这么久补一帧 `heartbeat`，让前端证明
# 会话还活着；token 正常流动时永不触发（每来一帧就重置等待）。
_CHAT_HEARTBEAT_INTERVAL_S = HEARTBEAT_INTERVAL_S

# P4.16 安全上限默认（对齐 agentao cli/commands/goal.py，决策P4.16-17/§11）：省略 --for/--turns 时
# 套这两个值；`--turns` 是主防跑飞（25 轮已是大量工作），`--for` 只防墙钟病态、设在轮数正常跑完点
# 之上（120m）。`--unbounded` 显式退出。
_GOAL_DEFAULT_MAX_TURNS = 25
_GOAL_DEFAULT_TIME_BUDGET_S = 120 * 60


def _resolve_goal_budget(
    for_: str | None, turns: int | None, unbounded: bool
) -> tuple[int | None, int | None]:
    """把 `--for/--turns/--unbounded` 解析为 `(time_budget_seconds, max_turns)`，省略套默认
    （镜像 agentao `_resolve_budget` + `_parse_turns`，复用 `parse_duration`）。非法值抛
    `DurationParseError`/`ValueError`（端点转 400）。`unbounded` → 双 None。"""
    from agentao.cli.duration import parse_duration

    if unbounded:
        # --unbounded 与显式 --for/--turns 冲突：静默丢弃后者会让用户以为设了上限其实没有（评审 #14）。
        if for_ or turns is not None:
            raise ValueError("--unbounded 不能与 --for/--turns 同用（要么无上限、要么给具体上限）。")
        return None, None
    time_budget = parse_duration(for_) if for_ else _GOAL_DEFAULT_TIME_BUDGET_S
    if turns is None:
        max_turns: int | None = _GOAL_DEFAULT_MAX_TURNS
    else:
        if turns <= 0:
            raise ValueError(f"--turns 须为正整数，收到 {turns!r}")
        max_turns = turns
    return time_budget, max_turns


def _resolve_goal_budget_partial(
    for_: str | None, turns: int | None
) -> tuple[int | None, int | None]:
    """解析 `/goal budget` 的 `--for/--turns`——**省略的轴留 None**（=不改该轴，由 `set_budget`
    保持原值），**不**套默认（与设目标不同）。非法值抛 `DurationParseError`/`ValueError`。"""
    from agentao.cli.duration import parse_duration

    time_budget = parse_duration(for_) if for_ else None
    if turns is not None and turns <= 0:
        raise ValueError(f"--turns 须为正整数，收到 {turns!r}")
    return time_budget, turns


class IngestBody(BaseModel):
    """`POST /api/ingest` 请求体。`target` 仍由 run_ingest 内部 _resolve_raw_target 兜底校验。"""

    target: str
    model: str | None = None


class BackfillBody(BaseModel):
    """`POST /api/backfill` 请求体（P4.8）。`question` 必填、**strip 后非空**（含纯空白 `"   "`
    → 422，对齐 CLI 必传问句、杜绝空白问句白触发一次 gated LLM 写）。`model` **省略或显式 `null`
    均回落 app 级 `model`**（端点 `body.model or model`，与 ingest/heal 同口径，决策P4.8-6）；二者皆无
    时**解析出的** `None` 直透子进程 runner 也合法（backfill 走 run_guarded_write 子进程、非嵌入会话、
    无 P4-8 模型坑）。"""

    question: str
    model: str | None = None

    @field_validator("question")
    @classmethod
    def _question_nonblank(cls, v: str) -> str:
        # `min_length=1` 挡不住纯空白（`"   "` 长度非零、会过校验后入队白触发一次 gated 写）。
        # 故 strip 后判空 → 抛 ValueError（Pydantic 归一为 422）；并**返回 strip 后的值**，使
        # 发给 Agent 的问句不带前后空白噪声（决策P4.8-8）。
        s = v.strip()
        if not s:
            raise ValueError("question 不能为空（含纯空白）。")
        return s


class ChatBody(BaseModel):
    """`POST /api/chat` 请求体。省略 `conversation_id` → 新建会话（一次性问答=单轮）。

    `attachments`（可选）：随消息附带的文件，每项是 `POST /api/upload` 回传的 `workspace/uploads/<名>`
    路径。端点按 agentao 附件约定把每个附件以 `<attachment uri="…" [mimetype="…"]/>` 标签追加进
    发给 agent 的消息（agent 凭只读工具自己读文本附件；用户选定「附件随消息发」、非 P4.6 的
    「晋级为源」管线，故文件留 workspace/uploads/、不进 raw/）；**图像**附件额外经 `arun(images=)`
    走视觉通道（base64），模型不支持视觉时由 agentao 自动降级为同格式标签文本（宿主不做能力探测，
    见 `_augment_with_attachments`）。前端气泡只显示原始 message + 文件徽章/缩略图（见 §5）。
    """

    message: str
    conversation_id: str | None = None
    model: str | None = None
    attachments: list[str] | None = None


class GoalBody(BaseModel):
    """`POST /api/chat/{id}/goal` 请求体（设目标，P4.16 §8.2）。`for`/`turns` 省略 → 套安全默认
    （25 轮 / 120m，决策P4.16-17）；`unbounded` → 双 None（无上限）。`for` 是 Python 关键字，故
    别名映射到 `for_`（前端发 JSON 键 `for`）。"""

    objective: str
    for_: str | None = Field(default=None, alias="for")
    turns: int | None = None
    unbounded: bool = False

    model_config = {"populate_by_name": True}


class GoalBudgetBody(BaseModel):
    """`POST /api/chat/{id}/goal/budget` 请求体（改/清上限）。`clear`/`unbounded` → 双 None。"""

    for_: str | None = Field(default=None, alias="for")
    turns: int | None = None
    unbounded: bool = False
    clear: bool = False

    model_config = {"populate_by_name": True}


class GoalRunBody(BaseModel):
    """`POST /api/chat/{id}/goal/run` 请求体（驱动续跑 SSE 流）。`attachments` 同 ChatBody——仅
    **首轮**经 `arun(images=)` 走视觉通道（决策P4.16-18），base64 不入 GoalState/sidecar。"""

    attachments: list[str] | None = None


class RawBody(BaseModel):
    """`POST /api/raw` 请求体（判别式 body：`content` XOR `source`，决策P4.6-4）。

    `name` 经 `_safe_raw_target` slug 化 + 强制 `.md`。两种「人投喂源」共用本端点：
    - **投喂**（P4.1）：给 `content`（粘贴正文，原样存进 raw/）。
    - **晋级**（P4.6）：给 `source`（指向 `workspace/` 内一个 `.md` 派生物），可选 `origin`
      （出处，前端预填上传原件路径 / 人手填外部 URL；省略或空白 → 回退 `source`）。宿主读
      `source` 内容、过文本准入、按 provenance 归一 frontmatter 后晋级为 `raw/<安全名>.md`。

    `content` 与 `source` **互斥且必选其一**（两者同给 / 都不给 → 400）。`origin` 仅
    `source` 分支有意义（content 分支忽略）。
    """

    name: str
    content: str | None = None
    source: str | None = None
    origin: str | None = None
    overwrite: bool = False


class ParseBody(BaseModel):
    """`POST /api/parse` 请求体（P4.6.1，决策P4.6.1-1/7）。

    `upload` = `POST /api/upload` 回传的 `workspace/uploads/<名>` 路径；宿主确定性解析（直调
    `convert_to_markdown`）成 `workspace/parsed/<slug>.md` + 随源图片。`backend` 透传 skill 转换后端
    （`auto`/`mineru`/`marker`/`python`），非法值 → 422。"""

    upload: str
    backend: str = "auto"

    @field_validator("backend")
    @classmethod
    def _backend_known(cls, v: str) -> str:
        if v not in _BACKENDS:
            raise ValueError(f"未知 backend：{v}（须 ∈ {_BACKENDS}）。")
        return v


class RelocalizeBody(BaseModel):
    """`POST /api/workspace/relocalize` 请求体（P4.6.1，决策P4.6.1-5/6）。

    `file` = `workspace/parsed/` 内一个 `.md` 路径；把它引用的图 copy 到 `images/<file_stem>/`、
    改名编号、重写引用、全局零引用 GC 原图。仅作用于 parsed/（拆分/合并断链修复场景）。"""

    file: str


class ModeBody(BaseModel):
    """`POST /api/chat/{id}/mode` 请求体（P4.5）。`mode` 仅接受 read-only / workspace-write；
    full-access / plan / full 等 → set_mode 抛 ValueError → 端点 422（决策P4.5-1）。"""

    mode: str


class UndoBody(BaseModel):
    """`POST /api/chat/{id}/undo` 请求体（P4.5）。`token` = done.undo.token（最近可写 turn 写日志）。"""

    token: str


class ConfirmBody(BaseModel):
    """`POST /api/chat/{id}/confirm` 请求体（P4.15a）。`interaction_id` = confirm_request 帧带的 id；
    `decision` 三值（决策P4.15-13）：`allow`=放行这一个 / `allow_session`=放行+本会话起自动放行
    （翻 confirm_mode=auto，**非** full-access）/ `deny`=拒绝。非三值之一 → 422。"""

    interaction_id: str
    decision: str

    @field_validator("decision")
    @classmethod
    def _decision_known(cls, v: str) -> str:
        if v not in ("allow", "allow_session", "deny"):
            raise ValueError(f"未知 decision：{v}（须 ∈ allow/allow_session/deny）。")
        return v


class AnswerBody(BaseModel):
    """`POST /api/chat/{id}/answer` 请求体（P4.15b）。`answer` = 用户对模型 `ask_user` 的答案串
    （原样交回模型）；前端按 `options`/`allow_custom` 约束 UI，端点不校验答案语义（交回模型容错）。"""

    interaction_id: str
    answer: str


class ConfirmModeBody(BaseModel):
    """`POST /api/chat/{id}/confirm-mode` 请求体（P4.15）。`confirm_mode` 仅 `ask`/`auto`，非法 → 422。
    **仅切本会话未来模式**、不碰在飞 pending（§5.2）——与气泡②（放行当前+翻 auto）不同。"""

    confirm_mode: str

    @field_validator("confirm_mode")
    @classmethod
    def _mode_known(cls, v: str) -> str:
        if v not in ("ask", "auto"):
            raise ValueError(f"未知 confirm_mode：{v}（须 ∈ ask/auto）。")
        return v


# Web 端 heal 默认本批上限：刻意小于 CLI 的 DEFAULT_LIMIT（=10）——浏览器里一次少物化几个、
# 看清回执再续批更顺手；`min_refs` 仍与 CLI 同源（只 limit 这一项 Web 单独取值）。
WEB_HEAL_DEFAULT_LIMIT = 5


class HealBody(BaseModel):
    """`POST /api/heal` 请求体（P4.3）。`limit` 默认用 Web 专属 `WEB_HEAL_DEFAULT_LIMIT`（=5，
    小于 CLI 的 10）、`min_refs` 仍与 CLI 同源；`ge=1` 对齐 CLI `positive_int` 的「须 ≥ 1」，
    越界 → 422。`model` 含 `None` 直接透传子进程 runner（heal 非嵌入会话、无 P4-8 模型坑，决策P4.3-5）。

    `targets`（可选，决策P4.3-3 修订）：UI 勾选的子集，**只作过滤器**——作业仍服务端
    `compute_worklist` 确定性重算 worklist，再取交集只物化命中者（陈旧/越界目标被交集丢弃，
    防 TOCTOU）。省略/`null` = 物化整批（旧行为）。"""

    limit: int = Field(default=WEB_HEAL_DEFAULT_LIMIT, ge=1)
    min_refs: int = Field(default=MISSING_ENTITY_MIN_REFS, ge=1)
    targets: list[str] | None = None
    model: str | None = None


class AuditBody(BaseModel):
    """`POST /api/audit` 请求体（P4.12）。`limit` 默认 **import 自 CLI 的 `audit.DEFAULT_LIMIT`**
    （按漂移源**组**计，不另设 Web 专属值——组数通常远小于 heal 缺页数，无需像 heal 那样压小，决策P4.12-1）；
    `ge=1` 对齐 CLI `positive_int`，越界 → 422。`model` 含 `None` 直接透传子进程 runner（audit 走
    `run_guarded_write` 子进程、非嵌入会话、无 P4-8 模型坑，决策P4.12-5）。一期不收 `slugs` 子集
    过滤器（决策P4.12-3：audit 唯一旋钮是 `--limit`，子集过滤需改 core）。"""

    limit: int = Field(default=AUDIT_DEFAULT_LIMIT, ge=1)
    model: str | None = None


# 随包前端静态资源目录（guanlan/web/static/，随 packages 自动入 wheel，见 pyproject）。
# 仍留在 app.py（与下方静态挂载强绑定、且被 tests/test_web.py 直接 import）。
STATIC_DIR = Path(__file__).parent / "static"


class _NoCacheStatic(StaticFiles):
    """静态资源禁缓存复用（`Cache-Control: no-cache`，仍走 ETag/Last-Modified 304 协商）。

    Starlette StaticFiles 默认不发 Cache-Control，浏览器走启发式缓存——升级观澜后拿**旧
    app.js 渲染新接口**会出怪症（如图像附件徽章落「📄 文本」、不出缩略图）。本地单用户下
    每次协商一趟 304 的开销可忽略，换升级即生效。
    """

    def file_response(self, *args, **kwargs):  # noqa: ANN002, ANN003 — 透传基类签名
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app(
    root: Path,
    *,
    model: str | None = None,
    runner: AgentRunner | None = None,
    session_persist: bool = True,
    mode: str = "read-only",
    reader: bool = False,
    max_conversations: int | None = None,
    confirm: str = "ask",
    confirm_timeout: float = 120.0,
    goal_enabled: bool = True,
) -> FastAPI:
    """构造绑定到知识库 `root` 的 FastAPI app。

    Args:
        root: 已 `require_kb_root(writable=True)` 校验过的知识库根（绝对路径）。
        model: `--model` 透传给写作业（C4）与会话嵌入（C5）；None 表示不覆盖、由环境发现。
        runner: 可注入的 `AgentRunner`（测试用 fake，不打真实 LLM）；None 走默认子进程 runner。
        session_persist: 会话落盘开关（P4.2，默认开）；关时退回 P4 纯内存（`--no-session-persist`）。
        mode: 新会话开局姿态（P4.5，默认 `read-only`）；`--mode workspace-write` 起即可写。
        reader: 只读多会话部署模式（P4.9，默认关）。开时**不注册**全部写路由 + 会话枚举端点
            （决策P4.9-2/3/17），并在 `create_app` 内**强制** `session_persist=False` + `mode="read-only"`
            （覆盖入参——任何 caller 直建也零写、只读姿态，钳制落点，决策P4.9-2）。
        max_conversations: 内存会话硬上限（P4.9-18，`None`=用默认 100）。显式 `< 1` 即抛
            `GuanlanError(EXIT_USAGE)`（权威校验同落 `create_app`，堵「绕过 CLI 直建得到坏配置 app」
            的直建漏口）。`None` 透传 `ConversationStore`、调用时回落模块常量。
        confirm: 新会话 confirm 开局姿态（P4.15，默认 `ask`）：`ask`=workspace-write 下 ASK 决策
            （带操作符 shell / `requires_confirmation` 工具）弹给人确认；`auto`=沿用 P4.5 静默放行
            逃生舱。每会话开局继承、之后可经气泡②或 `/confirm-mode` 翻（决策P4.15-7/13）。
        confirm_timeout: confirm/ask 等待超时秒数（P4.15，默认 120）。无人应答即默认拒绝（§4.2），
            兜「人走开 → 写锁永占」。
        goal_enabled: `/goal` 长任务续跑总开关（P4.16，默认开；`--goal off` 关，决策P4.16-17）。
            关时设/恢复/复活目标的端点 403；瞬时只读端点（show/pause/clear/edit/budget 非复活）仍可用。
            `reader`（匿名多用户只读部署）下**强制关**——无鉴权下放任自动续跑是 LLM 成本陷阱（见下）。
    """
    # 权威校验（决策P4.9-18）：任何 caller（CLI/serve/嵌入/测试直建）显式传 < 1 都早失败，
    # 不下沉 ConversationStore（那时 app 已建、太晚）。CLI/serve 可另作友好早提示，但权威点在此。
    # None = 未指定 → 不校验（ConversationStore 回落默认 100，合法）。
    if max_conversations is not None and max_conversations < 1:
        raise GuanlanError(
            f"--max-conversations 须 ≥ 1（收到 {max_conversations}）。", exit_code=EXIT_USAGE
        )
    # 解析有效上限（None → 模块默认 100）：传给 ConversationStore 并供 /api/info 如实回报（非 reader）。
    effective_max = MAX_CONVERSATIONS if max_conversations is None else max_conversations
    # reader 钳制（决策P4.9-2）：只读部署强制零写姿态——覆盖入参的 session_persist/mode，使
    # 「测试/嵌入只关了路由却仍落 .agentao/sessions/ 或起可写会话」的漏口从源头堵死。
    if reader:
        session_persist = False
        mode = "read-only"
        goal_enabled = False  # P4.16：匿名多用户只读部署不放任自动续跑（无鉴权 LLM 成本陷阱）
    app = FastAPI(title="观澜 Web 宿主", docs_url=None, redoc_url=None)
    # 配置挂在 app.state：后续提交的端点（报告/写作业/会话）从这里读 root/model/runner，
    # 避免在模块级全局变量上分裂状态（与 workers=1 单进程单事件循环假设一致，决策P4-2）。
    app.state.root = root
    app.state.model = model
    app.state.runner = runner
    app.state.mode = mode
    # 进程级单写者协调（P4.5 决策P4.5-6/10）：write_lock（包真正写执行）+ active_writable_turns
    # （层③ 423 时序互斥）。注入 JobQueue（worker 跑 fn() 时持 write_lock）与 ConversationStore
    # （可写 turn 异步取 write_lock + 计数）；写端点经 app.state.write_gate 查计数。
    write_gate = WriteGate()
    app.state.write_gate = write_gate
    # 单写者作业队列（ingest/heal/backfill/audit/投喂走它，FIFO 串行；决策P4-5）：worker 跑 fn() 包 write_lock。
    # on_job_done：ingest/heal/backfill/audit 写 wiki/ → bump 代际，使可写会话缓存的 check 基线失效、下轮重拍
    # （投喂/上传写 raw//workspace、不动 wiki，不 bump，P4.5-4 缓存基线；backfill 写 syntheses/，决策P4.8-5；
    # audit 刷 source 页 raw_digest + LLM 可能重写正文，改 wiki/，决策P4.12-6）。
    jobs = JobQueue(
        write_lock=write_gate.write_lock,
        on_job_done=lambda kind: (
            write_gate.bump_wiki_generation()
            if kind in ("ingest", "heal", "backfill", "audit")
            else None
        ),
    )
    app.state.jobs = jobs
    # P5.1 检索长驻缓存（决策P5.1-2）：进程内、不落盘、按 (mtime_ns,size) 增量失效；`/api/search`
    # 与 chat 的 `guanlan_search` 工具**共吃这一个**实例（§3.1），同一进程多次搜索不重读全库。重启
    # 即空、首搜按需重建；删除页由 CorpusCache.corpus() 的 stale 剪枝处理。不改 P5.0「无盘上派生」承诺。
    search_cache = CorpusCache()
    app.state.search_cache = search_cache
    # 会话表（问答走嵌入会话）：persist 开时每轮落 .agentao/sessions/ + 懒恢复（P4.2 决策P4.2-1）；
    # default_mode/write_gate 透传到每个会话（P4.5）；search_cache 透传到每个会话（P5.1 §3.1：
    # 每会话用 make_guanlan_search_tool 新建工具实例、只共享这一个 cache）。
    conversations = ConversationStore(
        root,
        model,
        persist=session_persist,
        default_mode=mode,
        write_gate=write_gate,
        max_conversations=effective_max,
        search_cache=search_cache,
        # P4.15：confirm 进程默认（ask=ASK 弹给人 / auto=P4.5 静默放行逃生舱）+ 确认等待超时
        # （秒）。每会话开局继承 confirm_mode、之后可被气泡②/`/confirm-mode` 翻（决策P4.15-7/13）。
        confirm_mode=confirm,
        confirm_timeout=confirm_timeout,
        # idle 回收**仅 reader 启用**（决策P4.9-6，评审 codex P2）：reader=多用户共享，需缓解并发顶满
        # 上限；非 reader=单用户、无上限压力，且 workspace-write 会话被逐会丢 memory-only 的 undo 日志/
        # 姿态（用户拿着的 undo token 随即 404）——故非 reader 关回收（idle_ttl=None），不动既有 P4/P4.5 行为。
        idle_ttl=IDLE_TTL_SECONDS if reader else None,
    )
    app.state.conversations = conversations
    app.state.reader = reader

    # reader 路由裁剪（决策P4.9-2/3/17）：写路由 + GET /graph 重建 + GET /api/conversations 枚举 +
    # POST .../undo 包进此装饰器——reader 下**不注册**（命中即 404、物理写不了 KB / 枚举不了他人会话），
    # 非 reader 原样注册。比「注册后运行时拒绝」强：写端点根本不存在。用法：把 `@app.post("/x")` 换成
    # `@_writer_only(app.post("/x"))`，函数体不动。
    def _writer_only(decorator):
        def wrap(fn):
            if not reader:
                decorator(fn)
            return fn  # 恒返回原函数（FastAPI 装饰器本就返回原函数）：reader 下只是不注册路由

        return wrap

    app.mount("/static", _NoCacheStatic(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        # 与 /static 同口径 no-cache：入口页若被缓存，里面引用的脚本/样式同样会滞后。
        return FileResponse(
            STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"}
        )

    # 只读自省（P4.4，决策P4.4-2）：app 级 `GET /api/info` 无会话也能答（喂 /status /mode），
    # 零 LLM、不建 agent；仅读 app.state 配置 + 内存会话计数（in-memory，零盘读）。恒 200。
    @app.get("/api/info")
    async def api_info() -> dict:
        info = {
            "kb_name": root.name,
            "model": model,  # --model 覆盖（可能 None：未覆盖时由各会话构造期环境发现）
            "mode": mode,  # 进程默认开局姿态（P4.5：read-only / workspace-write）
            "persist": session_persist,
            "reader": reader,  # P4.9：驱动前端隐藏写/历史/维护按钮（非安全边界，仅 UX，决策P4.9-9）
        }
        # reader 下**移除** conversations/max_conversations（决策P4.9-9）：无鉴权多用户下，在线会话数
        # 与硬上限对「占满上限挤掉别人」者是情报，不外泄。前端 reader 路径不依赖它们（/status 跳过该行）。
        if not reader:
            info["conversations"] = conversations.live_count()
            info["max_conversations"] = effective_max
        return info

    def _reject_if_writable_active(exclude_id: str | None = None) -> None:
        """层③ 时序互斥（决策P4.5-10）：可写 turn 活跃期间宿主写端点一律 `423 Locked`。

        兜「shell `curl http://127.0.0.1/api/raw` 让宿主替 agent 写 `raw/`」的旁路——curl 在可写
        turn 内到达即被拒、根本不入队。用 `423`（非 `409`）以与 `/api/raw` 既有「不覆盖冲突」`409`
        区分。read-only turn 不计数、不触发。

        **P4.16 决策P4.16-20**：把「正在跑的可写 goal」也算 writable-active，且判定**进程级**（扫全体
        会话）——使**进程内至多一个活跃可写 goal**。可写 turn 进行期 `active_writable_turns>0` 已拒；
        但 goal 轮间窄缝里 `active_writable_turns` 归 0，故须另查 `has_active_writable_goal`，否则会话 A
        的 goal 轮间会话 B 能起第二个可写 goal、二者争 `write_lock`。`exclude_id` 让「驱动本会话自己的
        goal」不被自己拦（只读 goal 不持写锁、不在此列）。
        """
        if write_gate.active_writable_turns > 0 or conversations.has_active_writable_goal(
            exclude_id
        ):
            raise HTTPException(
                status_code=423, detail="可写会话进行中，请稍后重试。"
            )

    # 浏览（读）：阻塞的文件扫描/渲染一律经 anyio.to_thread 卸离事件循环（决策P4-2）。
    @app.get("/api/pages")
    async def api_pages() -> dict:
        return {"pages": await anyio.to_thread.run_sync(_list_pages, root)}

    @app.get("/api/page")
    async def api_page(path: str = Query(..., description="相对知识库根的页面路径")) -> dict:
        page_file = _safe_wiki_file(root, path)  # 同步、廉价；越界/缺失即抛 HTTPException。
        # 把 wiki 页里 `../../raw/images/<slug>/…` 嵌图改写为只读 /api/raw/image 端点，使浏览器可显示
        # ingest 随源保留的嵌图（决策P5.2.1 / SKILL.md 约定）；阻塞读盘/渲染卸到线程（决策P4-2）。
        image_src = _wiki_image_src(root, page_file)

        def _render():
            return render_page(root / "wiki", page_file, image_src=image_src)

        return await anyio.to_thread.run_sync(_render)

    @app.get("/api/raw")
    async def api_raw() -> dict:
        return {"files": await anyio.to_thread.run_sync(_list_raw, root)}

    # raw 源预览（读，ingest 前看清正文，决策：渲染 markdown 与 /api/page·workspace 预览同口径）：
    # 纯读 raw/、不写盘/不入队/不取快照，与 /api/raw 列表同读线。复用 render_page 同一 sanitize 归口
    # （wikilink 仍按 wiki/ 解析）；reader 下仍注册（非写，但 ingest 入口本身是隐藏的写 chrome，故只在
    # 非 reader 选单被触达）。阻塞的读盘/渲染经 anyio.to_thread 卸离事件循环（决策P4-2）。
    @app.get("/api/raw/file")
    async def api_raw_file(
        name: str = Query(..., description="raw/ 内的 .md 文件名"),
    ) -> dict:
        raw_file = _safe_raw_file(root, name)  # 越界 409 / 非 md·缺失 404
        # 库内相对 `<img src>`（P5.2.1 落的 images/<slug>/…）改写指向只读图片端点，使预览能显示嵌图;
        # `allow_tables` 放行 mineru/marker emit 的复杂 `<table>` HTML（allowlist 消毒后还原）。
        image_src = lambda rel: "/api/raw/image?path=" + quote(rel)  # noqa: E731

        def _render():
            return render_page(
                root / "wiki", raw_file, image_src=image_src, allow_tables=True
            )

        return await anyio.to_thread.run_sync(_render)

    # raw 嵌图原字节（只读，配合 /api/raw/file 预览显示 P5.2.1 随源落盘的 raw/images/ 图）：path-contained
    # 到 raw/images/ 子树、且只服务图像扩展名（_RAW_IMAGE_EXT_TO_MIME）——非图/越界一律 4xx，绝不漏出
    # raw/*.md 源或它处文件。纯读、不入队/不取快照，reader 下仍注册（非写，与 /api/raw/file 同读线）。
    @app.get("/api/raw/image")
    async def api_raw_image(
        path: str = Query(..., description="raw/images/ 内的图像文件相对路径"),
    ) -> FileResponse:
        target = _safe_raw_image(root, path)  # 越界 409 / 缺失 404
        return _image_file_response(target)  # 白名单 404 + svg 安全交付（共用归口）

    # 检索（读，P5.1 决策P5.1-1/7）：直接调 P5.0 内核 `search_cache.search(wiki, q, …)`（P5.3 单一入口），
    # **不** shell out `guanlan search`、不入 JobQueue、不取快照、不 bump 代际——纯读 `wiki/`，与
    # `/api/pages`/`/api/page` 同读线。reader 下仍注册（非写、决策P5.1-7）。阻塞的 stat/分词/打分经
    # `anyio.to_thread.run_sync` 卸离事件循环（CorpusCache 内部自持锁，Web 层不另加全局搜索锁，§3.3）。
    # 成功体经 `search_result_dict` 单一归口（与 CLI `--json` 字段同形）；空/纯标点 query → 422
    # （HTTP 原生 `{"detail":…}`，不复刻 CLI `{"ok":false,…}`，决策P5.1-4/5）；`limit<1` 由 `Query(ge=1)` → 422。
    @app.get("/api/search")
    async def api_search(
        q: str = Query(..., min_length=1, description="检索词"),
        limit: int = Query(10, ge=1, description="召回条数（默认 10，须 ≥ 1）"),
    ) -> dict:
        if not tokenize(q):  # 纯空白/标点 → 无可检索词；与 CLI run_search 同口径，但走 HTTP 422
            raise HTTPException(status_code=422, detail="query 为空或无可检索词（纯空白/标点）。")

        # P5.3：`CorpusCache.search` 单一入口内部配好 corpus + 反链文档先验 + 打分（决策P5.3-4 脚枪收口）；
        # 反链 backlinks() 由语料签名 memo（签名变才 build_graph，决策P5.3-5）——热路绝不每搜建图。整个
        # 阻塞段经 `anyio.to_thread.run_sync` 卸离事件循环（CorpusCache 内部自持锁）。
        def _run():
            return search_cache.search(root / "wiki", q, limit=limit)

        return search_result_dict(await anyio.to_thread.run_sync(_run))

    # 投喂（P4.1）：把粘贴正文存为 raw/<安全名>.md。校验在端点（快 4xx）、写盘进单写者队列
    # 串行（杜绝与在飞 ingest 的 raw/ 快照窗口竞态，决策P4.1-2）。**同步等作业完成**再返回——
    # 投喂自身极快，但会**排在队列前序写作业之后**（如在飞 ingest），故响应可能等数十秒。
    @_writer_only(app.post("/api/raw"))  # reader 下不注册（投喂/晋级是写，决策P4.9-2）
    async def write_raw(body: RawBody) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423（决策P4.5-10）
        # 判别式 body（决策P4.6-4）：content 与 source 互斥且必选其一（同给/都不给 → 400）。
        if (body.content is None) == (body.source is None):
            raise HTTPException(
                status_code=400, detail="content 与 source 二选一（互斥且必选其一）。"
            )
        target = _safe_raw_target(root, body.name)  # 400/越界即抛，无需进队列（其 stem = target_stem）
        # `abandon_on_cancel=True`：等待在**前序写作业**（如在飞 ingest）后排队，可能数十秒甚至——
        # 若该 ingest 子进程挂死——无限久。设为可取消，使客户端断开 / 服务器关停能立即回收**请求
        # 协程**（worker 线程仍跑完作业、文件照常落盘，但请求不再被无限期钉死，关停不被拖住）。
        if body.source is not None:
            # 晋级（P4.6.1，决策P4.6.1-12）：**入队前**读 source + 引用驱动收图 + 归一重写 +
            # provenance + 文本准入 + 记 SHA256 指纹（坏处直接 4xx，不进队列）；thunk 在**同一写
            # 临界区**做指纹复检 + 图 staging-swap + md 末步提交 + 失败回滚（图 + md 原子提交）。
            plan = await anyio.to_thread.run_sync(
                prepare_promotion, root, body.source, target, body.origin
            )
            if target.exists() and not body.overwrite:
                raise HTTPException(
                    status_code=409, detail=f"raw/{target.name} 已存在；改名或传 overwrite=true。"
                )
            payload_bytes = len(plan.content.encode("utf-8"))
            job = await anyio.to_thread.run_sync(
                jobs.submit_and_wait,
                "promote",
                lambda emit: commit_promotion(plan, body.overwrite),
                abandon_on_cancel=True,
            )
            nimg: int | None = len(plan.images)
        else:
            # 投喂（P4.1）：粘贴正文过同一道文本闸；worker thunk 仅原子写 md（无图）。
            content = body.content
            _check_text_admission(content)
            if target.exists() and not body.overwrite:
                raise HTTPException(
                    status_code=409, detail=f"raw/{target.name} 已存在；改名或传 overwrite=true。"
                )
            payload_bytes = len(content.encode("utf-8"))
            job = await anyio.to_thread.run_sync(
                jobs.submit_and_wait,
                "raw_write",
                lambda emit: _atomic_write_raw(target, content, body.overwrite),
                abandon_on_cancel=True,
            )
            nimg = None  # 投喂无图概念：返回体不带 images 键（保 P4.1 字节）
        # 退出码 → HTTP 分流（对齐 §2，**不可**一律塌缩成 409，否则"磁盘满"被误报"已存在"）。
        # 晋级的 EXIT_USAGE 含两类 409：同名冲突（atomic_write_raw）或指纹复检失败（source 已变），
        # 两者 detail 取自 job.output、均为合法 409 冲突（决策P4.6.1-16）。
        if job.exit_code == EXIT_USAGE:
            raise HTTPException(status_code=409, detail=job.output or "raw/ 同名文件已存在。")
        if job.exit_code != EXIT_OK:  # 落盘 IO 失败 → 归一为 EXIT_AGENT_ERROR
            raise HTTPException(status_code=500, detail=job.output or "写盘失败。")
        out = {"saved": f"raw/{target.name}", "bytes": payload_bytes}
        if nimg is not None:  # 晋级额外回报随源搬的图片数（投喂不带，保 P4.1 字节）
            out["images"] = nimg
        return out

    # 文件上传（决策P4.6-3）：multipart 文件 → workspace/uploads/<安全名>（多格式、保留原扩展名）。
    # 网络接收不持锁；收齐后把原子写**提交单写者 JobQueue**，与投喂/ingest/可写 turn 同写者串行。
    # 层③：可写 turn 活跃 → 423（与 /api/raw 同口径——否则 agent shell `curl /api/upload` 会让本端点
    # 的写作业排在 turn 的 write_lock 后空等、而 turn 在等这条响应，互相空等死锁）。
    @_writer_only(app.post("/api/upload"))  # reader 下不注册（上传是写，决策P4.9-2）
    async def upload(file: UploadFile = File(...)) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423（决策P4.5-10）
        data = await file.read()  # 网络接收不持锁（大文件不阻塞队列）
        if not data:
            raise HTTPException(status_code=400, detail="空文件。")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=400, detail=f"文件超过 {MAX_UPLOAD_BYTES} 字节上限。"
            )
        target = _safe_workspace_target(root, "uploads", file.filename or "")
        # 入队写盘 + 同步等完成；阻塞经 to_thread 卸离事件循环（同投喂，决策P4.1-2）。
        job = await anyio.to_thread.run_sync(
            jobs.submit_and_wait,
            "upload",
            lambda emit: _atomic_write_upload(target, data),  # thunk 收 emit、此处忽略（决策P4.6.1-11）
            abandon_on_cancel=True,
        )
        if job.exit_code != EXIT_OK:  # 落盘 IO 失败 → 归一为 EXIT_AGENT_ERROR
            raise HTTPException(status_code=500, detail=job.output or "写盘失败。")
        return {
            "saved": f"workspace/uploads/{target.name}",
            "name": target.name,
            "bytes": len(data),
            "kind": _classify_upload(target, data),  # image / text / binary（前端徽章用）
        }

    # workspace 浏览/预览/删除（决策P4.6-5/11）：浏览/预览只读、path-contained；删除是宿主写、
    # 经单写者 + 层③ 423。供「暂存区」弹层晋级前审阅、修订后刷新、清 scratch。
    @app.get("/api/workspace")
    async def workspace_list(
        path: str | None = Query(None, description="要浏览的 workspace 子目录；省略 = 根视图"),
    ) -> dict:
        return await anyio.to_thread.run_sync(_list_workspace, root, path)

    @app.get("/api/workspace/file")
    async def workspace_file(
        path: str = Query(..., description="相对知识库根的 workspace 内 .md 路径"),
    ) -> dict:
        page_file = _safe_workspace_md(root, path)  # 越界 409 / 非 md/缺失 404
        # parsed 预览：把 `images/<slug>/…` 嵌图改写到 /api/workspace/raw（P4.6.1 解析随源落的图，§2.1）。
        image_src = _workspace_image_src(root, page_file)

        def _render():
            # 额外回传原始 md 文本（含 frontmatter）供前端「Markdown ⇄ 源码」切换；前端按 textContent
            # 转义显示，无注入。读盘已在本线程内、复用一次 stat 路径，单文件预览代价可忽略。
            rendered = render_page(root / "wiki", page_file, image_src=image_src)
            rendered["source"] = page_file.read_text(encoding="utf-8", errors="replace")
            return rendered

        return await anyio.to_thread.run_sync(_render)

    # 图像原字节（仅供「暂存区」缩略图）：path-contained 到 uploads/parsed scratch 两子目录，且
    # **只服务图像扩展名**（与视觉白名单同口径）——非图一律 404，杜绝把任意 scratch 文件 inline 出去。
    @app.get("/api/workspace/raw")
    async def workspace_raw(
        path: str = Query(..., description="workspace/uploads|parsed 内的图像文件"),
    ) -> FileResponse:
        target = _safe_workspace_scratch(root, path)  # 白名单外 400 / 缺失 404
        # 与 `/api/raw/image` 共用扩展名白名单 + svg 安全交付：覆盖 convert 落盘的 bmp/tif/tiff/svg，
        # 否则 parsed 预览对这些已收集图片显示断图（决策P5.2.1：parsed 与 raw/ 图目录两处同形）。
        return _image_file_response(target)

    @_writer_only(app.delete("/api/workspace/file"))  # reader 下不注册（删除是写，决策P4.9-2）
    async def workspace_delete(
        path: str = Query(..., description="待删的 workspace/uploads/ 或 parsed/ 内文件"),
    ) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423（防删到 turn 正读/写的 scratch）
        target = _safe_workspace_scratch(root, path)  # 白名单外 400 / 缺失 404
        # 删除是宿主写：入单写者 FIFO 队列、与上传/晋级/ingest 串行（同投喂，决策P4.6-11）。
        job = await anyio.to_thread.run_sync(
            jobs.submit_and_wait,
            "workspace-delete",
            lambda emit: _delete_workspace_scratch(target),  # thunk 收 emit、此处忽略
            abandon_on_cancel=True,
        )
        if job.exit_code != EXIT_OK:  # 删除 IO 失败 → 归一为 EXIT_AGENT_ERROR
            raise HTTPException(status_code=500, detail=job.output or "删除失败。")
        return {"deleted": path}

    # 整目录删除（决策P4.6-11）：递归删 uploads/parsed 内的一个**子目录**（根目录拒，400）。
    # 同文件删除：单写者 FIFO + 层③ 423。
    @_writer_only(app.delete("/api/workspace/dir"))  # reader 下不注册（删除是写，决策P4.9-2）
    async def workspace_delete_dir(
        path: str = Query(..., description="待整删的 workspace/uploads|parsed 内子目录"),
    ) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423
        target, _base = _safe_workspace_dir(root, path, allow_base_root=False)  # 根目录 400 / 缺失 404
        job = await anyio.to_thread.run_sync(
            jobs.submit_and_wait,
            "workspace-delete",
            lambda emit: _rmtree_workspace_dir(target),  # thunk 收 emit、此处忽略
            abandon_on_cancel=True,
        )
        if job.exit_code != EXIT_OK:
            raise HTTPException(status_code=500, detail=job.output or "删除失败。")
        return {"deleted": path}

    # 确定性解析（P4.6.1，决策P4.6.1-1/2/7）：把 workspace/uploads/<名> 直调 convert_to_markdown 内核
    # 转成 workspace/parsed/<slug>.md + 随源图片（**不落 raw/**）。慢（marker/mineru 分钟级）→ 入单写者
    # FIFO 异步队列、即时返回 job_id、前端轮询 /api/jobs/{id}（running 期即见 emit 推上的 backend 分级
    # 日志，决策P4.6.1-10/11，不新开 SSE）。upload 路径**入队前**校验（缺失即 404，快反馈）。
    @_writer_only(app.post("/api/parse"))  # reader 下不注册（解析写 workspace/parsed，决策P4.9-2）
    async def parse(body: ParseBody) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423（决策P4.5-10）
        upload_path = _safe_upload_file(root, body.upload)  # 越界 400 / 缺失 404
        target = parse_target(root, upload_path)
        backend = body.backend
        return {
            "job_id": jobs.enqueue(
                "parse",
                lambda emit: parse_upload(
                    upload_path, target, root=root, backend=backend, emit=emit
                ),
            )
        }

    # 解析根（parsed/）归口：image-lint / relocalize 共用，且强制目标落在 parsed/ 子树内。
    parsed_root = (root / "workspace" / "parsed").resolve()

    def _require_parsed_md(file_rel: str) -> Path:
        """把 `file` 解析为 parsed/ 内存在的 `.md`；越界/非 parsed 409、非 md/缺失 404（断链检查/重整专用）。"""
        page = _safe_workspace_md(root, file_rel)  # workspace/uploads|parsed 内 .md（越界 409/缺失 404）
        try:
            page.relative_to(parsed_root)
        except ValueError:
            raise HTTPException(
                status_code=409, detail=f"断链检查/重整仅作用于 workspace/parsed/：{file_rel}"
            ) from None
        return page

    # 图片断链检查（只读，决策P4.6.1-5）：扫一个 parsed .md 的图片引用，分类悬空/错位。reader 下仍
    # 注册（纯读），但 reader 无 parsed 文件、自然 404/不被触达。阻塞读盘卸到线程（决策P4-2）。
    @app.get("/api/workspace/image-lint")
    async def workspace_image_lint(
        file: str = Query(..., description="workspace/parsed/ 内待检查的 .md 路径"),
    ) -> dict:
        page = _require_parsed_md(file)
        result = await anyio.to_thread.run_sync(image_lint, page, parsed_root)
        return {"file": page.relative_to(root).as_posix(), **result}

    # 图片重整（写，决策P4.6.1-5/6）：把该文件引用图 copy 到 images/<file_stem>/ + 改名编号 + 重写引用
    # + 全局零引用 GC。入单写者 FIFO + 层③ 423（与删除/晋级同写者串行）。file 入队前校验落 parsed/。
    @_writer_only(app.post("/api/workspace/relocalize"))  # reader 下不注册（重整是写，决策P4.9-2）
    async def workspace_relocalize(body: RelocalizeBody) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423（决策P4.5-10）
        page = _require_parsed_md(body.file)
        job = await anyio.to_thread.run_sync(
            jobs.submit_and_wait,
            "relocalize",
            lambda emit: relocalize_commit(page, parsed_root),
            abandon_on_cancel=True,
        )
        if job.exit_code == EXIT_USAGE:  # 罕见 TOCTOU 写冲突 → 409
            raise HTTPException(status_code=409, detail=job.output or "重整冲突。")
        if job.exit_code != EXIT_OK:  # 落盘 IO 失败 → 500
            raise HTTPException(status_code=500, detail=job.output or "重整失败。")
        return {"file": page.relative_to(root).as_posix(), "output": job.output}

    # 零 LLM 报告（决策P4-7）：只序列化既有 *Report，与 CLI `--json` 字节对齐；阻塞跑 to_thread。
    wiki = root / "wiki"

    # 名 → (跑报告, 序列化器)。三报告同形（跑 wiki → format(..., json_output=True) 字节对齐 CLI），
    # 表驱动避免三份 copy-paste 漂移（如某个漏 json_output=True）。health 的 strict 只影响 CLI
    # 退出码、不改 JSON 体（ok 已反映有无 findings），故 Web 不收该参。
    _reports = {
        "check": (run_check, _format_check),
        "health": (run_health, _format_health),
        "lint": (run_lint, _format_lint),
    }

    @app.get("/api/report/{name}")
    async def report(name: str) -> Response:
        entry = _reports.get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"未知报告：{name}")
        run_fn, format_fn = entry
        result = await anyio.to_thread.run_sync(run_fn, wiki)
        return _report_response(format_fn(result, json_output=True))

    # graph：构建（写派生 graph/）后 302 到自包含静态视图。json_only 时改跳 graph.json。
    @_writer_only(app.get("/graph"))  # reader 下不注册（重建写 graph/，决策P4.9-11）
    async def graph(json_only: bool = False) -> RedirectResponse:
        # 用无打印的 build_and_write_graph（非 graph_entrypoint）：worker 的进程级 redirect_stdout
        # 期间不引入并发打印者（决策P4-5 红线）。写失败 → 500，别 302 到缺失文件让用户撞 404。
        #
        # /graph 是同步写 graph/ 的宿主写者（决策P4.5-6）：纳入 write_lock 与 ingest/heal/可写 turn
        # 串行（防并发写 graph/ 互踩/截断、防读到撕裂的 wiki/ 出残图）；**异步**取阻塞锁、绝不在
        # 事件循环线程直接 acquire。
        # 同样受层③ 423（评审 P1，自死锁修复）：可写 turn 全程持 write_lock，若 agent 在 turn 内
        # `curl http://127.0.0.1/graph`，本端点会去抢**同一把不可重入** write_lock —— 而 turn 又
        # 在等这条 HTTP 响应，互相空等、write_lock 永不释放、后续一切写永久卡死。故进锁**前**先按
        # 层③ 拒（与 /api/raw·ingest·heal 同口径）：可写 turn 活跃 → 423、根本不抢锁，从源头断死锁。
        _reject_if_writable_active()
        # held 在 try **之前**声明、acquire 在 try **之内**：客户端断开取消落在 acquire 的 await 处
        # 时（run_sync 默认 abandon_on_cancel=False、锁已到手），finally 仍据 held[0] 释放，绝不泄漏
        # 进程级写锁（评审 P1，与 Conversation.turn 同一守法）。
        held = [False]
        try:
            await anyio.to_thread.run_sync(write_gate.acquire_thunk(held))
            await anyio.to_thread.run_sync(
                functools.partial(build_and_write_graph, root, json_only=json_only)
            )
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"graph 构建失败：{exc}") from exc
        finally:
            if held[0]:
                write_gate.write_lock.release()
        target = "/graph/graph.json" if json_only else "/graph/graph.html"
        return RedirectResponse(url=target, status_code=302)

    # 文件名 → media_type 白名单：既去掉两份 copy-paste，又把 {filename} 限死在这两项、杜绝穿越。
    _graph_files = {"graph.html": "text/html", "graph.json": "application/json"}

    @app.get("/graph/{filename}")
    async def graph_file(filename: str) -> FileResponse:
        media_type = _graph_files.get(filename)
        if media_type is None:
            raise HTTPException(status_code=404, detail=f"未知 graph 文件：{filename}")
        path = root / "graph" / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{filename} 尚未生成，请先 GET /graph")
        return FileResponse(path, media_type=media_type)

    # 作业心跳回调工厂（A+ 心跳，决策：瞬时进度行）：长跑的 agent 作业（ingest/heal/backfill/audit）
    # 全程靠 agentao 子进程（capture_output）静默、worker 又 redirect stderr 到 buf，tty 心跳够不到
    # 模态。改走 JobQueue 的 progress 通道：running 期把「⏳ 正在<verb>…仍在运行 Ns · wiki/ 已写 N 页」
    # 刷进 job.progress（pollJob 实时渲染），作业收尾清空 → 不污染最终干净摘要。verb 如 摄入/物化/沉淀/审计。
    def _wiki_job_progress(verb: str):
        wiki_dir = root / "wiki"

        def _progress(elapsed: float) -> str:
            line = f"⏳ 正在{verb}…仍在运行 {int(elapsed)}s"
            # 自作业起跑（墙钟 ≈ now − elapsed）以来 wiki/ 写了几页；0 时省略后缀（首拍前 / 尚未落页）。
            changed = count_files_modified_since(wiki_dir, time.time() - elapsed)
            if changed:
                line += f" · wiki/ 已写 {changed} 页"
            return line

        return _progress

    # 写（唯一写入口 = ingest）：即时入队、立刻返回 job_id；前端轮询 /api/jobs/{id}（无 SSE）。
    @_writer_only(app.post("/api/ingest"))  # reader 下不注册（入库是写，决策P4.9-2）
    async def ingest(body: IngestBody) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423（决策P4.5-10）
        # target 不在此预校验：run_ingest 内部 _resolve_raw_target 是单一归口（须在 raw/、是 .md、
        # 存在），Web 不旁路 P2 入口校验；非法 target → 作业以 EXIT_USAGE 完成，轮询可见。
        def _job(emit) -> int:  # thunk 收 emit（决策P4.6.1-11）；ingest 仍靠 print() 经 redirect
            return run_ingest(
                body.target,
                root=root,
                model=body.model or model,
                runner=runner,
            )

        return {"job_id": jobs.enqueue("ingest", _job, progress=_wiki_job_progress("摄入"))}

    # heal 预览（零 LLM 读，决策P4.3-4）：只算 worklist（== heal --dry-run），不入队、不触 Agentao、
    # 不取 raw/ 快照；阻塞跑 to_thread。limit 默认用 Web 专属值（=5）、min_refs 与 CLI 同源，
    # 与 POST /api/heal 一致；越界 422（Query ge=1）。
    @app.get("/api/heal/preview")
    async def heal_preview(
        limit: int = Query(WEB_HEAL_DEFAULT_LIMIT, ge=1),
        min_refs: int = Query(MISSING_ENTITY_MIN_REFS, ge=1),
    ) -> dict:
        return await anyio.to_thread.run_sync(_heal_preview, root, limit, min_refs)

    # heal 物化（写，决策P4.3-2）：入单写者 FIFO 队列、即时返回 job_id、前端轮询（与 ingest 同构）。
    # worklist 由作业**服务端重算**（不收 target 列表，决策P4.3-3）；走子进程 runner、过 P2 写门禁。
    @_writer_only(app.post("/api/heal"))  # reader 下不注册（物化是写，决策P4.9-2）
    async def heal(body: HealBody) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423（决策P4.5-10）

        def _job(emit) -> HealRun:  # 收 emit、忽略；返回结构化结果（非 int）→ worker 鸭子分流存 result
            run = run_heal_result(
                root=root,
                limit=body.limit,
                min_refs=body.min_refs,
                targets=body.targets,
                model=body.model or model,
                runner=runner,
            )
            if run.final_text:  # agent 散文 → worker 的 redirect 捕获进 job.output（与 ingest 同口径）
                print(run.final_text)
            return run

        return {"job_id": jobs.enqueue("heal", _job, progress=_wiki_job_progress("物化"))}

    # 写（唯一写入口之一 = backfill，P4.8）：即时入队、立刻返回 job_id；前端轮询 /api/jobs/{id}（无 SSE）。
    # 与 ingest 端点逐行同构——整体复用 run_query(backfill=True)，答案 + 门禁回执经 stdout 捕获进 job.output。
    # 比 ingest 还薄：_job 返回退出码 int → worker 走既有 int 鸭子分流分支、result 恒 None（决策P4.8-2）。
    @_writer_only(app.post("/api/backfill"))  # reader 下不注册（回填是 gated 写，决策P4.9-2）
    async def backfill(body: BackfillBody) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423（决策P4.5-10 / P4.8-5）

        def _job(emit) -> int:  # 收 emit、忽略；返回退出码 int（与 ingest 同形）→ result 恒 None
            return run_query(
                body.question,
                root=root,
                backfill=True,
                model=body.model or model,
                runner=runner,
            )

        return {"job_id": jobs.enqueue("backfill", _job, progress=_wiki_job_progress("沉淀"))}

    # audit 预览（零 LLM 读，决策P4.12-4）：只算漂移源组（== audit --dry-run），不入队、不触 Agentao、
    # 不取 raw/ 快照；阻塞跑 to_thread。limit 默认 import 自 CLI 的 audit.DEFAULT_LIMIT、越界 422（Query ge=1）。
    @app.get("/api/audit/preview")
    async def audit_preview_route(
        limit: int = Query(AUDIT_DEFAULT_LIMIT, ge=1),
    ) -> dict:
        return await anyio.to_thread.run_sync(_audit_preview, root, limit)

    # audit 审计（写，决策P4.12-2）：入单写者 FIFO 队列、即时返回 job_id、前端轮询（与 heal 同构）。
    # 走子进程 runner、过 P2 写门禁 + P2.1 源不回退闸（page_guard=True 由 core 内写死，决策P3.7-4）；
    # 返回结构化 AuditRun → worker 鸭子分流存 job.result（决策P4.12-1，零 jobs.py 改动）。
    @_writer_only(app.post("/api/audit"))  # reader 下不注册（审计是 gated 写，决策P4.9-2）
    async def audit(body: AuditBody) -> dict:
        _reject_if_writable_active()  # 层③：可写 turn 活跃 → 423（决策P4.5-10 / P4.12-6）

        def _job(emit) -> AuditRun:  # 收 emit、忽略；返回结构化结果（非 int）→ worker 鸭子分流存 result
            run = run_audit_result(
                root=root,
                limit=body.limit,
                model=body.model or model,
                runner=runner,
            )
            if run.final_text:  # agent 散文 → worker 的 redirect 捕获进 job.output（与 heal 同口径）
                print(run.final_text)
            return run

        return {"job_id": jobs.enqueue("audit", _job, progress=_wiki_job_progress("审计"))}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str) -> dict:
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"未知作业：{job_id}")
        # heal / audit 作业多回一个 `result`（机器回执，经既有序列化器）；ingest/投喂/backfill 为 null、
        # 前端忽略（决策P4.3-1 / P4.12-1）。agent 散文一律在 `output`，不进 `result`。
        if isinstance(job.result, HealRun):
            result = heal_result_dict(job.result.result, job.result.postponed)
        elif isinstance(job.result, AuditRun):
            result = audit_result_dict(job.result.result, list(job.result.postponed))
        else:
            result = None
        return {
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "exit_code": job.exit_code,
            "output": job.output,
            "progress": job.progress,  # 瞬时进度（A+ 心跳）：running 期活跃提示，done 后恒为 ""
            "result": result,
        }

    # 问答 / 多轮会话（只读嵌入，决策P4-8）：POST 直接返回 text/event-stream，前端 fetch 读 body。
    @app.post("/api/chat")
    async def chat(body: ChatBody) -> StreamingResponse:
        # 层③ 自死锁修复（评审 P1，与 /graph·/mode·/undo 同口径）：--mode workspace-write 下可写 turn
        # 全程持 write_lock + 自身 conv.lock，若 agent 在 turn 内 `curl POST /api/chat` 起**嵌套可写**
        # turn，新 turn 会等同一把 write_lock —— 外层又在等这条 HTTP 响应，互相空等、锁永不释放、后续
        # 写全卡死。故起 turn 前先按层③ 拒。
        # **仅拦「会写的」turn**（评审 P2）：只读 turn 不取 write_lock、跑在各自 conv.lock 上，与活跃写者
        # 无锁交集、不会死锁 —— 故纯只读 Web / 默认只读姿态 / 切到别的只读会话续聊一律放行，不误伤。
        # 判定基 = 本 turn 将以何姿态跑：新会话用进程默认姿态（在 create **前**判，免建一个随即被拒的
        # 空会话白占 MAX_CONVERSATIONS）；已存在会话用其当前姿态（决策P4.5-10）。
        if body.conversation_id is None:
            if conversations.default_mode == "workspace-write":
                _reject_if_writable_active()
            try:
                conv = await anyio.to_thread.run_sync(
                    functools.partial(conversations.create, body.model)
                )
            except RuntimeError as exc:  # 会话数达上限
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        else:
            conv = conversations.get(body.conversation_id)
            if conv is None:
                # 内存未命中 → 试懒恢复盘上的只读会话（P4.2 决策P4.2-3）。restore 含 load_session
                # （磁盘）+ agent 构造（LLM client）两个慢操作，**必须** off-loop 卸到线程池——否则
                # 在 async route 里同步调用会阻塞整个 ASGI 事件循环。
                try:
                    conv = await anyio.to_thread.run_sync(
                        conversations.restore, body.conversation_id
                    )
                except RuntimeError as exc:  # 恢复时内存已满 → 同 create 转 503
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
                if conv is None:  # 非规范 id / 盘上无此 Web 会话 / 持久化关 / catalog-文件竞态读不出
                    raise HTTPException(
                        status_code=404, detail=f"未知会话：{body.conversation_id}"
                    )
            # 正被 goal 续跑驱动的会话不接普通 turn（评审 #12）：否则普通 turn 在 goal 内层轮之间的
            # 缝里抢 conv.lock 跑，可能在 run_goal finally 摘除 update_goal 后才调它 → 未知工具错；且
            # 把 goal 专用工具暴露给非 goal 轮。先停 goal（/goal pause）再普通对话。
            if conv.in_goal():
                raise HTTPException(
                    status_code=409, detail="目标续跑进行中，先 /goal pause 再普通对话。"
                )
            # 已知会话：仅当它会跑可写 turn（取 write_lock / 自锁 conv.lock）才按层③ 拒（评审 P1/P2）。
            # 恢复的冷会话开局 = 进程默认姿态，故 conv.mode 已反映本 turn 将以何姿态跑。
            if conv.mode == "workspace-write":
                _reject_if_writable_active()

        # 附件：按 agentao 约定把 <attachment> 标签追加进消息，图像另读盘 base64 走 arun(images=)。
        # 在起 turn **前**做（路径越界 400 / 缺失 404 即抛、走 HTTP 错误，不进流）；阻塞读盘卸到线程。
        # 失败前若已新建会话则会留一个空会话（可接受、随进程退出清；不为此回收而复杂化）。
        agent_message = body.message
        images: list[dict[str, str]] = []
        if body.attachments:
            agent_message, images = await anyio.to_thread.run_sync(
                _augment_with_attachments, root, body.message, body.attachments
            )

        queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()

        def emit(kind: str, data: object) -> None:
            queue.put_nowait((kind, data))

        async def _run_turn() -> None:
            # 标记在飞**再**发 start（决策P4-8 / codex 评审）：start 带 conversation_id 让前端尽早拿到
            # id，停止按钮才能在首轮就 POST /api/chat/{id}/stop。begin_turn 必须**先于** emit("start")——
            # 否则前端一收到 start 就 POST /stop 时，turn 可能还没进到装令牌的临界区，request_stop 把它
            # 当 idle 丢弃、前端不重试，首轮停止被静默吞掉。标记在飞后，该窗口内的停止被记为待停、turn
            # 进锁即兑现；令牌仍在锁内安装，故并发同会话时 stop 只打活跃轮、不误伤排队轮。
            conv.begin_turn()
            emit("start", {"conversation_id": conv.id})
            # 可写 turn 收尾元数据归口（P4.5 §7）：turn 在 finally 填本 dict（check / immutable_mutated
            # / undo），由本协程独占——无论 turn 返回还是抛错都读自己这只 dict，杜绝跨轮读串。
            # read-only turn 保持空，done/error 帧不带这些字段。
            meta: dict = {}
            # 显式 Task（而非裸 coro）+ shield：客户端断开会取消本协程，但**不可**靠 asyncio 取消
            # 打断 arun——那只转发 token.cancel() 便立刻 re-raise、不等线程收尾，于是 lock 会在后台
            # executor 线程仍在跑时被释放，下一轮就可能与残线程并发改 agent.messages / 串错 token。
            # shield 让 turn 跑到自然结束（lock 全程持有），杜绝该竞态；代价是断开后该轮仍跑完（本地
            # 单用户、轮次有界，可接受）。**主动停止**走另一条干净路径：停止端点经 conv 上的取消令牌
            # 打断，arun 持锁等线程真正收尾才抛 AgentCancelledError（见下 except）。持显式 task 句柄
            # 是为在断线分支消费它最终的异常（P4.15 §4.3：断线 request_stop 会让后台 turn 抛
            # AgentCancelledError，无人 await 即 asyncio「Task exception was never retrieved」噪声）。
            turn_task = asyncio.ensure_future(conv.turn(agent_message, emit, meta, images))
            try:
                answer = await asyncio.shield(turn_task)
                # 答案已完整流出；再渲染安全 markdown HTML（[[页]] → 站内链接）作收尾。渲染失败
                # **不能**丢掉这条成功答案：省略 answer_html，前端回退用纯文本 answer 上屏。
                payload: dict = {"answer": answer, "conversation_id": conv.id, **meta}
                try:
                    payload["answer_html"] = await anyio.to_thread.run_sync(
                        render_markdown, answer, root / "wiki"
                    )
                except Exception:  # noqa: BLE001 — 渲染失败仅降级排版，不毁答案、不转 error
                    pass
                emit("done", payload)
            except AgentCancelledError:
                # 用户按停止：已流出的 token 已在前端气泡，发 stopped 收尾（这不是错误，不转 error）。
                # 可写 turn 即便被停，写已发生、层②已还原、check/undo 已落 meta，照样带上（§7）。
                emit("stopped", {"conversation_id": conv.id, **meta})
            except asyncio.CancelledError:
                # 客户端断开：外层被取消，但 shielded turn_task 仍在后台跑到自然收尾（写锁释放/落盘
                # 在 turn 自身 finally 完成）。断线时若 §4.3 的 request_stop 已 trip 令牌，turn_task 会
                # 抛 AgentCancelledError——挂一个吞异常回调消费它，免「Task exception was never
                # retrieved」噪声（t.cancelled() 短路守 .exception() 不在已取消任务上反抛）。
                turn_task.add_done_callback(lambda t: t.cancelled() or t.exception())
                raise  # 客户端已断开，流没了，不再 emit
            except Exception as exc:  # noqa: BLE001 — 任何失败都转 error 事件，不泄 traceback 到流
                # 带上 conversation_id：首轮失败时前端据此记住已建会话，避免下次另起新会话堆积。
                # error 帧同样带 meta（可写 turn 抛错前的写已被 §3 收尾捕获，§7）。
                emit("error", {"message": f"{type(exc).__name__}: {exc}", "conversation_id": conv.id, **meta})
            finally:
                conv.end_turn()  # 与 begin_turn 配对：本轮收尾，清在飞标记/未兑现待停（codex 竞态修复）
                queue.put_nowait(None)  # 哨兵：通知流结束

        async def event_stream() -> AsyncIterator[str]:
            task = asyncio.create_task(_run_turn())
            started = time.monotonic()
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), _CHAT_HEARTBEAT_INTERVAL_S
                        )
                    except asyncio.TimeoutError:
                        # 静默间隙（长工具调用 / 首 token 前的思考）：补一帧心跳证明还活着。
                        # 取消的是内层 queue.get()——尚未取到任何项，不丢事件；token 正常流动时
                        # 每帧都重置等待、永不触发。纯事件循环侧、无线程（区别于 CLI 的心跳线程）。
                        yield _sse(
                            "heartbeat", {"elapsed": int(time.monotonic() - started)}
                        )
                        continue
                    if item is None:
                        break
                    kind, data = item
                    yield _sse(kind, data)
            finally:
                # 断线处理的精确执行序（P4.15 §4.3，落地钉死）：必须**先 trip 取消令牌、再取消
                # 外层 task**。① 客户端断开时若本会话有未决 pending（confirm/ask 正阻塞内层 shielded
                # turn、全程持写锁），仅 task.cancel() **不够**——外层 _run_turn 在 await shield 处秒回、
                # 但 shield 保护的内层 conv.turn 仍卡在 _wait_interaction 等应答，会白占写锁达
                # CONFIRM_TIMEOUT_S。故先 conv.request_stop()（幂等、线程安全、loop 线程直调）trip
                # 同一令牌 → 内层下一拍读到 is_cancelled 立即返回拒绝 → arun 抛 AgentCancelledError →
                # promptly 释写锁。**无 pending 的普通长轮不动**：保留 shield「后台跑完落盘」语义，不误杀。
                # ② 再取消外层 task。顺序不可换：gather 等的是外层 task，它秒回 ≠ 内层结束，真正解开
                # 内层阻塞的是 ①。窄竞态（断线与「刚要建 pending」擦肩）由 §4.2 超时兜底。
                if conv.has_pending():
                    conv.request_stop()
                if not task.done():
                    task.cancel()  # 客户端断开 → 取消外层 _run_turn（它走 finally end_turn）
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # 会话级只读自省（P4.4，喂 /status /context /skills /tools /mode）：内存命中 → conv.info()
    # 全量；冷会话 → cold_info（不建 agent、出部分信息，决策P4.4-7）；否则 404。零 LLM、无副作用、
    # 不切姿态、不入队、不建作业；阻塞（token 估算 / catalog 读盘）经 to_thread 卸离事件循环（决策P4-2）。
    @app.get("/api/chat/{conversation_id}/info")
    async def chat_info(conversation_id: str) -> dict:
        conv = conversations.get(conversation_id)
        if conv is not None:
            return await anyio.to_thread.run_sync(conv.info)  # 内存态全量自省（off-loop）
        info = await anyio.to_thread.run_sync(conversations.cold_info, conversation_id)
        if info is None:  # 非规范 / 盘上无此 Web 会话 / 持久化关 → 404（同 chat / messages 口径）
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        return info

    # 停止在飞的一轮（停止按钮）：经会话上的取消令牌打断当前 arun。幂等、零写、无新退出码。
    @app.post("/api/chat/{conversation_id}/stop")
    async def chat_stop(conversation_id: str) -> dict:
        conv = conversations.get(conversation_id)
        if conv is None:
            # 内存无此会话 = 没有在飞 turn 可停（冷会话尚未懒恢复）。404 让前端别误以为停成了。
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        # request_stop 仅置位令牌（线程安全、不阻塞），无在飞 turn 时返回 False。
        return {"stopped": conv.request_stop()}

    # P4.15a 工具确认应答（confirm_request → 用户点 允许/本会话起自动放行/拒绝）：解阻塞内层
    # confirm_tool。**绝不**取 write_lock/conv.lock（只 put_nowait、loop 线程瞬返，§4.1/§5.2）——
    # 否则与「持锁等它应答」的 turn 死锁。kind 必须匹配 confirm（跨类拒，决策P4.15-12/§5.2）。
    @app.post("/api/chat/{conversation_id}/confirm")
    async def chat_confirm(conversation_id: str, body: ConfirmBody) -> dict:
        conv = conversations.get(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        # id 对不上 / kind 不符（陈旧点击、已超时/停止、打错端点）/ 已有应答在途 → 409。
        if not conv.resolve_confirm(body.interaction_id, body.decision):
            raise HTTPException(status_code=409, detail="无匹配的待应答")
        return {"ok": True}

    # P4.15b 模型提问应答（ask_request → 用户填答案）：原样交回模型。同样瞬返、不取锁；kind
    # 必须匹配 ask（跨类拒，决策P4.15-12）——否则 /answer 的非空串被 confirm_tool 当 truthy True。
    @app.post("/api/chat/{conversation_id}/answer")
    async def chat_answer(conversation_id: str, body: AnswerBody) -> dict:
        conv = conversations.get(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        if not conv.resolve_answer(body.interaction_id, body.answer):
            raise HTTPException(status_code=409, detail="无匹配的待应答")
        return {"ok": True}

    # P4.15 翻本会话 confirm 姿态（ask↔auto）：**仅切未来模式**、不碰在飞 pending（§5.2 评审
    # Medium）——「恢复逐次确认」走它（auto→ask），主动关确认也走它。镜像 /mode 的 per-conversation
    # 语义：只动本会话、不动进程默认。冷会话/无会话 → 404（无 live agent 可翻）。零写、不取写锁。
    @app.post("/api/chat/{conversation_id}/confirm-mode")
    async def chat_confirm_mode(conversation_id: str, body: ConfirmModeBody) -> dict:
        conv = conversations.get(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        return {"confirm_mode": conv.set_confirm_mode(body.confirm_mode)}

    # ── P4.16 长任务目标续跑（docs/P4.16-Web目标续跑.md §8）──────────────────────────
    #
    # **状态端点瞬返 + 一条驱动端点走 SSE**：set/budget/pause/resume/clear/edit 只改 GoalState（瞬返
    # JSON、loop 线程、只取 `_goal_lock`，绝不取 conv.lock/write_lock，同 P4.15 /confirm 锁纪律）；
    # `/goal/run` 是**唯一**驱动续跑的 SSE 端点（设/恢复/复活 limit_reached 后由前端链式发起，决策
    # P4.16-8）。写权限三分（§2 表）：端点=user 面（绝不置 complete/blocked）、UpdateGoalTool=agent
    # 面、run_goal 预检=host 面（limit_reached）。

    def _require_goal_enabled() -> None:
        if not goal_enabled:
            raise HTTPException(
                status_code=403,
                detail="/goal 长任务续跑已禁用（--goal off / 只读部署）。",
            )

    async def _get_or_restore_conv(cid: str):
        """内存命中即返回，否则懒恢复盘上会话（同 `/api/chat`，off-loop）。冷会话恢复后其
        `__init__` 已 reload goal sidecar（§10）——故重启/idle 回收后盘上 stranded 目标可经
        `/goal/resume` 复活（评审 codex P2：goal 端点须像 chat 一样懒恢复，否则恢复永远 404）。
        返回 None → 端点 404；内存满 → 503。"""
        conv = conversations.get(cid)
        if conv is not None:
            return conv
        try:
            return await anyio.to_thread.run_sync(conversations.restore, cid)
        except RuntimeError as exc:  # 恢复时内存已满 → 503（同 /api/chat）
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/chat/{conversation_id}/goal")
    async def chat_goal_set(conversation_id: str, body: GoalBody) -> dict:
        """设目标（user 面，瞬返）。active 目标存在 → 409（先 pause）；本会话有在飞轮 → 409
        （决策P4.16-19）。**不**驱动——前端据返回的 goal 再 POST `/goal/run` 起 SSE（决策P4.16-8）。"""
        _require_goal_enabled()
        conv = await _get_or_restore_conv(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        objective = body.objective.strip()
        if not objective:
            raise HTTPException(status_code=400, detail="objective 不能为空。")
        if conv.is_busy():  # 同会话已有 turn/goal 在飞 → 先停（决策P4.16-19）
            raise HTTPException(status_code=409, detail="本会话有进行中的轮次，先停再设目标。")
        # 可写姿态：进程内至多一个活跃可写 goal（决策P4.16-20）——设之前先拦（驱动时再查一次）。
        if conv.mode == "workspace-write":
            _reject_if_writable_active(exclude_id=conv.id)
        try:
            time_budget, max_turns = _resolve_goal_budget(
                body.for_, body.turns, body.unbounded
            )
        except Exception as exc:  # DurationParseError / ValueError → 400
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            conv.create_goal(
                objective, time_budget_seconds=time_budget, max_turns=max_turns
            )
        except GoalActiveError as exc:  # active 目标存在 → 先 pause（决策P4.16-15）
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"goal": conv.goal_snapshot()}

    @app.post("/api/chat/{conversation_id}/goal/budget")
    async def chat_goal_budget(conversation_id: str, body: GoalBudgetBody) -> dict:
        """改/清 live 目标上限（user 面，瞬返）。limit_reached 且新上限有余量 → 复活（返回
        `revived:true`，前端随后 POST `/goal/run` 续跑，决策P4.16-8）。"""
        conv = await _get_or_restore_conv(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        if not (body.clear or body.unbounded or body.for_ or body.turns is not None):
            raise HTTPException(
                status_code=400,
                detail="用法：/goal budget [--for <时长>] [--turns <n>] | --clear",
            )
        # --clear/--unbounded（清上限）与显式 --for/--turns（给上限）冲突：旧实现径取 (None, None)
        # 静默丢弃后者，留下「以为设了上限其实无上限」的假象（codex 评审 P2）。对齐设目标
        # `_resolve_goal_budget` 的 #14 校验：宁可 400，让用户二选一。
        if (body.clear or body.unbounded) and (body.for_ or body.turns is not None):
            raise HTTPException(
                status_code=400,
                detail="--clear/--unbounded 不能与 --for/--turns 同用（要么清上限、要么给具体上限）。",
            )
        try:
            time_budget, max_turns = (
                (None, None)
                if (body.clear or body.unbounded)
                else _resolve_goal_budget_partial(body.for_, body.turns)
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # 何时允许把 limit_reached 复活成 active（否则只改上限）——三道闸，任一不满足都不复活，
        # 避免留下「active 却无 driver / 无法 clear」的 stranded active：
        #   ① goal_enabled：`--goal off`/只读部署下复活无意义（/goal/run·resume 皆 403，评审 codex P2）；
        #   ② not in_goal：本会话正被驱动（如 wrap-up 轮在飞）时复活会与在飞循环抢状态（评审 #6）；
        #   ③ 可写 goal 须进程内独占：别处有活跃可写 goal 时复活后 /goal/run 必 423（评审 #8）。
        allow_reactivate = goal_enabled and not conv.in_goal()
        if conv.mode == "workspace-write":
            allow_reactivate = (
                allow_reactivate
                and not conversations.has_active_writable_goal(exclude_id=conv.id)
            )
        result = conv.set_budget(
            time_budget_seconds=time_budget,
            max_turns=max_turns,
            unbounded=body.unbounded,
            clear=body.clear,
            allow_reactivate=allow_reactivate,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="无目标可改预算。")
        revived, summary = result
        return {"revived": revived, "summary": summary, "goal": conv.goal_snapshot()}

    @app.post("/api/chat/{conversation_id}/goal/pause")
    async def chat_goal_pause(conversation_id: str) -> dict:
        """暂停目标（user 面，瞬返）。锁内 pause + `request_stop()` 即时打断在飞内层轮（§8.2）。"""
        conv = await _get_or_restore_conv(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        return {"paused": conv.pause_goal(), "goal": conv.goal_snapshot()}

    @app.post("/api/chat/{conversation_id}/goal/resume")
    async def chat_goal_resume(conversation_id: str) -> dict:
        """恢复 paused/blocked（或 stranded active）→ active（user 面，瞬返）。本会话有在飞轮 →
        409（决策P4.16-19）。返回 `active:true` 时前端随后 POST `/goal/run` 驱动续跑。"""
        _require_goal_enabled()
        conv = await _get_or_restore_conv(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        if conv.is_busy():
            raise HTTPException(status_code=409, detail="本会话有进行中的轮次，先停再恢复目标。")
        # 进程内至多一个活跃可写 goal（决策P4.16-20）：别处有活跃可写 goal 时**先 423**，绝不先把本
        # 目标 resume 成 active 再让随后的 /goal/run 423 —— 那会留下 active-but-undriven（评审 pass 6）。
        # 与 set 同序（set 在 create 前查、run 也查），故 resume→run 一致。
        if conv.mode == "workspace-write":
            _reject_if_writable_active(exclude_id=conv.id)
        return {"active": conv.resume_goal(), "goal": conv.goal_snapshot()}

    @app.post("/api/chat/{conversation_id}/goal/edit")
    async def chat_goal_edit(conversation_id: str, body: GoalBody) -> dict:
        """改 objective（保 status + 上限，user 面，瞬返）。"""
        conv = await _get_or_restore_conv(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        objective = body.objective.strip()
        if not objective:
            raise HTTPException(status_code=400, detail="objective 不能为空。")
        if not conv.edit_goal(objective):
            raise HTTPException(status_code=404, detail="无目标可编辑。")
        return {"goal": conv.goal_snapshot()}

    @app.post("/api/chat/{conversation_id}/goal/clear")
    async def chat_goal_clear(conversation_id: str) -> dict:
        """删目标（user 面，瞬返）。active → 409（先 pause，决策P4.16-15）。"""
        conv = await _get_or_restore_conv(conversation_id)
        if conv is None:
            # 会话不可恢复（无 session 快照），但 goal sidecar 可能是孤儿（评审 #5）→ 兜底删它；
            # 删到 → cleared，否则纯未知 id → 404。
            if conversations.clear_orphan_goal(conversation_id):
                return {"cleared": True}
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        try:
            cleared = conv.clear_goal()
        except GoalActiveError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"cleared": cleared}

    @app.post("/api/chat/{conversation_id}/goal/run")
    async def chat_goal_run(
        conversation_id: str, body: GoalRunBody | None = None
    ) -> StreamingResponse:
        """驱动续跑 SSE 流（host 面）。会话有 active 目标 + 无在飞轮才驱动；可写 goal 须过进程级
        `_reject_if_writable_active`（决策P4.16-20）。begin_turn/end_turn/断线 finally 只在此最外层
        包一次——inner turn 的 shield/排空全归 `conv.run_goal`（§4）。"""
        _require_goal_enabled()
        conv = await _get_or_restore_conv(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        if not conv.goal_is_active():
            raise HTTPException(status_code=409, detail="无活跃目标可驱动（先设目标或恢复）。")
        if conv.mode == "workspace-write":  # 进程内至多一个活跃可写 goal（决策P4.16-20）
            _reject_if_writable_active(exclude_id=conv.id)
        if conv.is_busy():  # 同会话两条 SSE 流抢 conv.lock/串 begin_turn → 拒（决策P4.16-19）
            raise HTTPException(status_code=409, detail="本会话有进行中的轮次，先停再驱动目标。")
        # **原子预订**（评审 codex P2）：is_busy 检查与 reserve_goal_run 之间**绝无 await**（单事件
        # 循环下二者原子）——故两条近乎同时的 /goal/run 不会都过 is_busy 再各起一个 _drive 驱动同一
        # GoalState。reserve = begin_turn（整个 goal run 期 _inflight=1：停止打到内层令牌、idle 不回收，
        # §9）**并立刻置 in_goal**，使进程级可写 goal 互斥在下方 await 附件读盘期间已生效（否则别的可写
        # 会话能在该窗口溜过 _reject_if_writable_active 起第二个）。预订后、起流前失败须回滚（下方 try）；
        # _drive **不再** begin_turn、只配对 end_turn。
        conv.reserve_goal_run()

        # 首轮附件 → first_images（决策P4.16-18）：仅首轮经 arun(images=) 走视觉通道，base64 不入
        # GoalState/sidecar。空消息只为复用 _augment_with_attachments 的图像读盘（标签文本丢弃）。
        images: list[dict[str, str]] = []
        try:
            if body is not None and body.attachments:
                _msg, images = await anyio.to_thread.run_sync(
                    _augment_with_attachments, root, "", body.attachments
                )
        except BaseException:  # incl. CancelledError——预订后、起流前任何失败都回滚（否则预订泄漏）
            conv.release_goal_run()
            raise

        queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()

        def emit(kind: str, data: object) -> None:
            queue.put_nowait((kind, data))

        async def _render(answer: str) -> str:
            return await anyio.to_thread.run_sync(render_markdown, answer, root / "wiki")

        async def _drive() -> None:
            # begin_turn/in_goal 已在端点同步预订（见上），释放经 task 的 done_callback（见下）——**不**在
            # 此 finally 里 end_turn：否则若响应体迭代器从未启动（客户端在 return 后、`async for` 前断开），
            # event_stream 永不跑、_drive 也不会被 gather → 预订永久泄漏、进程级写锁永锁（评审 #2/codex P3-2）。
            emit("goal_start", conv.goal_snapshot() or {})
            try:
                await conv.run_goal(emit, first_images=images or None, render=_render)
            except asyncio.CancelledError:
                raise  # 客户端断开：流没了，不再 emit（run_goal 已收为 paused 落盘，§5.3）
            except Exception as exc:  # noqa: BLE001 — 任何失败转 error 帧（不泄 traceback 到流）
                emit(
                    "error",
                    {"message": f"{type(exc).__name__}: {exc}", "conversation_id": conv.id},
                )
            finally:
                queue.put_nowait(None)  # 哨兵：通知流结束

        # **在端点（而非 event_stream）建 _drive task + done_callback 释放预订**（评审 #2）：task 一经
        # `ensure_future` 即被事件循环调度，其终态（完成/取消/异常）**必**触发 done_callback——与响应体
        # 迭代器是否启动无关，故 `reserve_goal_run` 的 begin_turn/in_goal 绝不泄漏。`release_goal_run`
        # 幂等于 run_goal finally 已清的 in_goal（再清一次无害），并配对预订的 end_turn（仅此一次）。
        task: asyncio.Future = asyncio.ensure_future(_drive())
        task.add_done_callback(lambda _t: conv.release_goal_run())

        async def event_stream() -> AsyncIterator[str]:
            started = time.monotonic()
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), _CHAT_HEARTBEAT_INTERVAL_S
                        )
                    except asyncio.TimeoutError:
                        yield _sse(
                            "heartbeat", {"elapsed": int(time.monotonic() - started)}
                        )
                        continue
                    if item is None:
                        break
                    kind, data = item
                    yield _sse(kind, data)
            finally:
                # 断线处理（§5.3，扩展 P4.15）：goal 运行期断线即 request_stop（trip 当前轮令牌 +
                # 置 _stop_requested），run_goal 的 except 收为 paused 落盘；再 cancel 外层 task →
                # 穿到 run_goal 的 await shield 处（其 except CancelledError 排空 inner + pause + 传播）。
                if conv.in_goal() or conv.has_pending():
                    conv.request_stop()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # 运行时翻姿态（P4.5 决策P4.5-5）：read-only ↔ workspace-write 真切换（只翻两点置位、不重建
    # agent、不动层① wrapper）。持会话锁与在飞 turn 串行。404 未知 id；409 冷会话（未激活、无 live
    # agent 可翻）；422 非法 mode（含 full-access/plan/full）。逻辑落 chat.py，本端点只分流（决策P4.5-9）。
    @app.post("/api/chat/{conversation_id}/mode")
    async def chat_mode(conversation_id: str, body: ModeBody) -> dict:
        # reader 强制只读姿态（决策P4.9-4）：拒绝切到 workspace-write（否则可写工作会话能写
        # workspace/，破「全只读」）。端点本身保留（仍可「切回」read-only、查询无害），仅挡可写姿态。
        if reader and body.mode == "workspace-write":
            raise HTTPException(
                status_code=409, detail="只读部署（--reader）下不可切换到 workspace-write 姿态。"
            )
        conv = conversations.get(conversation_id)
        if conv is None:
            # 冷会话（盘上有、内存无 live agent）→ 409「先续聊一轮恢复再切」；纯未知 → 404。
            cold = await anyio.to_thread.run_sync(conversations.cold_info, conversation_id)
            if cold is not None:
                raise HTTPException(
                    status_code=409, detail="会话未激活，先续聊一轮恢复再切姿态。"
                )
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        # 层③ 自死锁修复（评审 P2，与 /graph 同口径）：可写 turn 全程持 conv.lock，若 agent 在
        # turn 内 `curl /api/chat/{id}/mode`，本端点会等同一把 conv.lock —— 而 turn 又在等这条 HTTP
        # 响应，互相空等、turn 永不收尾、计数/写锁永不释放。故取 conv.lock **前**先按层③ 拒。
        _reject_if_writable_active()
        try:
            async with conv.lock:  # 与在飞 turn 串行（持锁翻两点置位）
                new_mode = conv.set_mode(body.mode)
        except ValueError as exc:  # 非法 mode（full-access/plan/未知）→ 422
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"mode": new_mode}

    # 撤销本轮写（P4.5 决策P4.5-4/13）：乐观回放最近可写 turn 的写日志（wiki/ + SCHEMA.md）。
    # 统一锁序 conversation.lock → write_lock（与可写 turn 同序、绝不反序，否则交叉死锁，评审 High）；
    # 逐文件乐观校验当前哈希 == 本 turn 写后哈希，相等才还原、否则跳过并计入 conflicts。某文件冲突 →
    # 整体 409（前端标红、其余仍还原）；全清 → 200。token 失效 / 无可撤销 → 409。404 未知/冷会话。
    @_writer_only(app.post("/api/chat/{conversation_id}/undo"))  # reader 下不注册（撤销是写，决策P4.9-17）
    async def chat_undo(conversation_id: str, body: UndoBody) -> dict:
        conv = conversations.get(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        # 层③ 自死锁修复（评审 P2，与 /graph·/mode 同口径）：可写 turn 全程持 conv.lock（同会话）
        # 并持 write_lock（任意会话），若 agent 在 turn 内 `curl /undo`（即便 token 瞎填），本端点会
        # 等这两把锁 —— 而 turn 又在等这条 HTTP 响应，互相空等、turn 永不收尾、锁永不释放。故取锁
        # **前**先按层③ 拒。合法撤销发生在 turn 收尾后（计数归 0），不受影响。
        _reject_if_writable_active()
        async with conv.lock:  # 锁序①：会话锁（与同会话在飞 turn 串行，护 undo 日志不竞态）
            # 取 write_lock **前**先廉价判可撤：陈旧/空 token 不必白排在在飞 ingest 之后再回 409
            # （评审 BUG 4）。判定与 apply_undo 间持 conv.lock，token 状态稳定。
            if not conv.can_undo(body.token):
                raise HTTPException(
                    status_code=409, detail="撤销已失效（无本轮写日志或已有后续写）。"
                )
            # 锁序②：进程 write_lock（撤销是写、须单写者串行）。异步取阻塞锁、不堵事件循环。
            # held 在 try 前声明、acquire 在 try 内：取消落在 acquire 的 await 处也据 held[0] 释放，
            # 不泄漏进程级写锁（评审 P1，与 /graph、Conversation.turn 同一守法）。
            held = [False]
            try:
                await anyio.to_thread.run_sync(write_gate.acquire_thunk(held))
                result = await anyio.to_thread.run_sync(conv.apply_undo, body.token)
            finally:
                if held[0]:
                    write_gate.write_lock.release()
        if result is None:  # token 失效 / 无可撤销 → 409（理论上已被上面短路，留作纵深防御）
            raise HTTPException(
                status_code=409, detail="撤销已失效（无本轮写日志或已有后续写）。"
            )
        if result["conflicts"]:  # 部分文件已被后续写改动 → 409（前端标红，其余仍还原）
            return JSONResponse(status_code=409, content=result)
        return result

    # 建一个 warm 空会话但**不起 turn**（决策P4.4-7 的 UX 反转）：供前端在 /tools·/skills·/context·
    # /goal·/mode 等「需活动 agent」的斜杠命令于无活动会话时先开一个空活会话，免去「先提一个问题以开启
    # 会话」的生硬提示。纯建会话**零 LLM、零盘写**（agent 构造期注册工具/激活 skill，`_save` 仅成功轮后
    # 才落盘），故空会话只在内存、未进盘上枚举；用户随后第一条真问题进同一会话、不二次新建。
    # **不**取 write_lock、不跑 turn，故无须层③ writable-active 拒（与 POST /api/chat 起 turn 前的拒不同，
    # 决策P4.5-10）；满则同 chat 转 503。
    # **reader 下不注册**（决策P4.9-2，评审）：匿名多用户部署里，一个**零 LLM、可匿名循环调用**的建会话
    # 端点会让攻击者廉价占满 MAX_CONVERSATIONS、把真实读者顶成 503；且 reader 开 idle 回收，未落盘的空
    # 会话被回收后前端 `?c=` 续聊会 404。reader 读者仍可经 `POST /api/chat`（首条消息）隐式建会话——前端在
    # reader 下也不自动开会话、保留「先提一个问题」原提示（见 chat.js `ensureActiveConversation` 的 reader 闸）。
    @_writer_only(app.post("/api/conversations"))  # reader 下不注册（建会话占槽、防匿名占满，决策P4.9-2）
    async def create_conversation() -> dict:
        try:
            conv = await anyio.to_thread.run_sync(conversations.create)
        except RuntimeError as exc:  # 会话数达上限（同 chat）
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"conversation_id": conv.id, "mode": conv.mode}

    @_writer_only(app.get("/api/conversations"))  # reader 下不注册：防枚举他人会话 id（决策P4.9-3）
    async def list_conversations() -> dict:
        # persist 开时 list() 即时 list_sessions(kb) 读盘（合并去重），故 off-loop 卸到线程池。
        return {"conversations": await anyio.to_thread.run_sync(conversations.list)}

    @app.get("/api/conversations/{conversation_id}/messages")
    async def conversation_messages(conversation_id: str) -> dict:
        # 纯查看：回放历史会话的 user/assistant 气泡。内存命中读内存、冷会话读盘（**不建 agent**，
        # 续聊时再由 POST /api/chat 懒恢复）。未知/非规范/非 Web/持久化关下盘上 id → 404（同 chat）。
        messages = await anyio.to_thread.run_sync(
            conversations.messages_for, conversation_id
        )
        if messages is None:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        out: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue  # 只回放对话气泡，跳过 system/tool 等内部消息
            text = _message_text(m.get("content"))
            if not text:
                continue
            item: dict = {"role": role, "content": text}
            if role == "assistant":
                # assistant 答案过同一安全 markdown 渲染（[[页]]→站内链），与流式收尾的 answer_html
                # 一致；渲染失败仅省略 html、前端回退纯文本（不毁回放）。
                try:
                    item["html"] = await anyio.to_thread.run_sync(
                        render_markdown, text, root / "wiki"
                    )
                except Exception:  # noqa: BLE001 — 渲染失败仅降级排版
                    pass
            out.append(item)
        return {"messages": out}

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str) -> dict:
        # 按内存命中与否分两支（决策P4.2-5）：勿把 disk-only 的精确闸加到内存命中支，否则一个
        # live 但盘上已被 rotate 掉的会话会删不掉、`新会话` 的 best-effort DELETE 也会失效。
        conv = conversations.get(conversation_id)
        if conv is not None:
            # 内存命中：先拿会话锁等当前/断开后仍在 shield 跑完的 turn 收尾，避免 agent.close() 与
            # 在飞的 arun 抢同一 agent 资源（决策P4-8 单 agent 假设）；delete 内含 best-effort 删盘。
            async with conv.lock:
                await anyio.to_thread.run_sync(conversations.delete, conversation_id)
            return {"deleted": conversation_id}
        # 仅在盘上：须规范全 UUID + _disk_session 精确/作用域校验才删盘，否则 404（§3.3/§2.2）。
        deleted = await anyio.to_thread.run_sync(conversations.delete_disk, conversation_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"未知会话：{conversation_id}")
        return {"deleted": conversation_id}

    return app
