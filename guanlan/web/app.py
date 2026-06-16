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
import json
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
from ..runtime import AgentRunner
from ..search import CorpusCache, search_result_dict, tokenize
from .chat import IDLE_TTL_SECONDS, MAX_CONVERSATIONS, ConversationStore
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

    def _reject_if_writable_active() -> None:
        """层③ 时序互斥（决策P4.5-10）：可写 turn 活跃期间宿主写端点一律 `423 Locked`。

        兜「shell `curl http://127.0.0.1/api/raw` 让宿主替 agent 写 `raw/`」的旁路——curl 在可写
        turn 内到达即被拒、根本不入队。用 `423`（非 `409`）以与 `/api/raw` 既有「不覆盖冲突」`409`
        区分。read-only turn 不计数、不触发。
        """
        if write_gate.active_writable_turns > 0:
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

        return {"job_id": jobs.enqueue("ingest", _job)}

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

        return {"job_id": jobs.enqueue("heal", _job)}

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

        return {"job_id": jobs.enqueue("backfill", _job)}

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

        return {"job_id": jobs.enqueue("audit", _job)}

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
            try:
                # shield：客户端断开会取消本任务，但**不可**靠 asyncio 取消打断 arun——那只转发
                # token.cancel() 便立刻 re-raise、不等线程收尾，于是 lock 会在后台 executor 线程仍
                # 在跑时被释放，下一轮就可能与残线程并发改 agent.messages / 串错 token。shield 让
                # turn 跑到自然结束（lock 全程持有），杜绝该竞态；代价是断开后该轮仍跑完（本地单
                # 用户、轮次有界，可接受）。**主动停止**走另一条干净路径：停止端点经 conv 上的取消
                # 令牌打断，arun 持锁等线程真正收尾才抛 AgentCancelledError（见下 except）。
                answer = await asyncio.shield(conv.turn(agent_message, emit, meta, images))
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
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    kind, data = item
                    yield _sse(kind, data)
            finally:
                if not task.done():
                    task.cancel()  # 客户端断开 → 取消在飞的 turn
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
