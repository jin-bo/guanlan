"""FastAPI app + 路由（P4，见 docs/P4-Web宿主.md §4/§5）。

宿主自身**不做任何确定性/语义判断**：端点只负责"收 HTTP 请求 → 调既有包内函数或嵌入
agent → 序列化/转发结果"。阻塞调用一律卸到线程（零 LLM 报告经 `anyio.to_thread.run_sync`、
写作业 `ingest` 经单 worker 线程），绝不在事件循环里直接跑阻塞代码（决策P4-2）。

C1 仅落地骨架：`GET /` 返回随包 `index.html` + `/static/*` 静态挂载。后续提交按
§5 契约逐片加 `/api/pages`·`/api/page`·`/api/raw`（C2）、报告（C3）、`ingest`（C4）、`chat`（C5）。
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
from agentao.cancellation import AgentCancelledError
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..check import format_report as _format_check
from ..check import run_check
from ..errors import EXIT_OK, EXIT_USAGE
from ..graph import build_and_write_graph
from ..health import format_report as _format_health
from ..health import run_health
from ..heal import (
    HealRun,
    compute_worklist,
    heal_result_dict,
    run_heal_result,
)
from ..ingest import run_ingest
from ..lint import MISSING_ENTITY_MIN_REFS
from ..lint import format_report as _format_lint
from ..lint import run_lint
from ..pages import iter_pages, load_page, page_title, page_type
from ..runtime import AgentRunner
from .chat import MAX_CONVERSATIONS, ConversationStore
from .jobs import JobQueue
from .render import render_markdown, render_page


class IngestBody(BaseModel):
    """`POST /api/ingest` 请求体。`target` 仍由 run_ingest 内部 _resolve_raw_target 兜底校验。"""

    target: str
    model: str | None = None


class ChatBody(BaseModel):
    """`POST /api/chat` 请求体。省略 `conversation_id` → 新建会话（一次性问答=单轮）。"""

    message: str
    conversation_id: str | None = None
    model: str | None = None


class RawBody(BaseModel):
    """`POST /api/raw` 请求体（P4.1 投喂）。`name` 经 `_safe_raw_target` slug 化 + 强制 `.md`。"""

    name: str
    content: str
    overwrite: bool = False


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


def _sse(kind: str, data: object) -> str:
    """编码一个 SSE 事件帧（`event:`/`data:` + 空行）。data 一律 JSON（ensure_ascii=False）。"""
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

# 随包前端静态资源目录（guanlan/web/static/，随 packages 自动入 wheel，见 pyproject）。
STATIC_DIR = Path(__file__).parent / "static"


def _safe_wiki_file(root: Path, rel: str) -> Path:
    """把请求 `path` 解析为 `wiki/` 内存在的文件；越界 → 409、不存在 → 404（路径穿越防御）。

    `rel` 是相对知识库根的 posix 路径（如 `wiki/entities/Foo.md`，与 `/api/pages` 回传一致）。
    绝对路径 / `..` 越界经 `resolve()` + `relative_to(wiki)` 拦下（决策P4-4 / §8）。
    """
    wiki = (root / "wiki").resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(wiki)
    except ValueError:
        raise HTTPException(status_code=409, detail=f"路径越界（须在 wiki/ 内）：{rel}") from None
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"页面不存在：{rel}")
    return candidate


# ── P4.1 投喂（POST /api/raw）：文件名 slug + 安全 + 原子写 ──────────────────────
#
# 投喂是**人投喂源**（普通文件写），非 gated agent 写（决策P4.1-1）；只收 `.md` 文本
# （多格式属 P5）。落盘动作经单写者 JobQueue 串行，杜绝与在飞 ingest 的 `raw/` 快照窗口
# 竞态（决策P4.1-2，见 POST /api/raw）。

MAX_RAW_BYTES = 5 * 1024 * 1024  # 投喂正文大小上限（默认 5 MiB），防误粘巨量文本。

# 已知非-md 扩展名拒绝列表（命中即 400，挡多格式误投，与 P5 边界一致）。判定用
# `Path(规范化 basename).suffix.lower() in _RAW_REJECT_EXTENSIONS`——**不**用朴素
# `suffix != ".md"`，以免误杀 `GPT-4.5 笔记`（suffix `.5 笔记`）、`v1.2` 等带点标题（决策P4.1-4）。
_RAW_REJECT_EXTENSIONS = frozenset(
    {".txt", ".pdf", ".docx", ".doc", ".html", ".htm", ".rtf", ".epub"}
)

# 混淆字符规范化映射表（决策P4.1-4）：大模型常给文件名夹带 ASCII 外的同形/排版字符，直接
# 交给 slug 的"其余→`-`"会劣化成 `-` 串（`"X"` → `-X-`）。故先 NFKC 折全角/兼容字符，再过本表
# 处理 NFKC 不覆盖的排版符号。键是 unicode 序数（str.translate 要求），值为替换串或 None（删除）。
# 只作用于**文件名**；正文 `content` 永远按 UTF-8 原样写盘（§4，未加工源保真）。
_RAW_NAME_FOLDMAP: dict[int, str | None] = {
    # 各类引号 → 删除（不留 `-`，避免 `"X"` 变 `-X-`）。
    **{ord(c): None for c in '"\'“”‟„«»‘’‚‛‹›「」『』`'},
    # 各类破折号 / 连接号 / 波浪号 → 单个 `-`（`～` 已被 NFKC 折成 `~`，一并收）。
    **{ord(c): "-" for c in "—–―‒~"},
    # 省略号 → 删除（注：NFKC 已把 `…` 展成 `...`，本项仅兜未展开者）。
    ord("…"): None,
    # NFKC 不覆盖的零宽空白 → 普通空格（随后 slug 收敛成 `-`；NBSP/全角空格已由 NFKC 折成空格）。
    **{ord(c): " " for c in "​﻿"},
}

# slug：保留 CJK/字母/数字/`-`/`_`/`.`（`\w` 在 str 正则下含 CJK），其余（含空格）成 `-`；折叠连续 `-`。
_RAW_SLUG_STRIP = re.compile(r"[^\w.\-]+")
_RAW_SLUG_DASHES = re.compile(r"-{2,}")


def _raw_slug(stem: str) -> str:
    """把已规范化的 basename（去后缀）收敛为安全 slug；首尾 `-`/`.`/空白剥净。

    首尾 `.` 一并剥净：杜绝盘上落出隐藏文件（`.foo` → `foo`）或双点（`notes.` → `notes`）；
    内部点保留（`v1.2` 不变），故带点标题仍保真。
    """
    return _RAW_SLUG_DASHES.sub("-", _RAW_SLUG_STRIP.sub("-", stem)).strip("-.")


def _safe_raw_target(root: Path, name: str) -> Path:
    """把用户给的文件名/标题解析为 `<kb>/raw/<安全名>.md`（决策P4.1-4，与 `_safe_wiki_file` 并列）。

    判定顺序须钉死：① 剥目录 → ② NFKC + 映射表规范化 + **剥首尾空白** → ③ 基于**规范化 basename**
    取 `suffix.lower()`（命中拒绝列表即 400；`.md` 视作已带后缀、归一小写）→ ④ slug → ⑤ 强制 `.md`
    → ⑥ resolve 越界校验。规范化在取 suffix **之前**：否则全角点 `x．PDF` 会漏成 `x.PDF.md`。
    步骤 ② 必须 `.strip()`：尾随空白会让 `Path("foo.MD ").suffix == ".MD "`（带空格）逃过 `.md`
    归一、落成 `foo.MD.md`（大写双后缀）；剥净后 `foo.MD` → `.md` 归一 → `foo.md`。
    """
    base = Path(name).name  # ① 剥掉任何目录成分，杜绝 ../、绝对路径、子目录穿越。
    normalized = (
        unicodedata.normalize("NFKC", base).translate(_RAW_NAME_FOLDMAP).strip()  # ②
    )
    suffix = Path(normalized).suffix.lower()  # ③ 基于规范化 basename
    if suffix in _RAW_REJECT_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"投喂只收 .md 文本；拒绝扩展名 {suffix}。")
    # `.md`（大小写不敏感）视作已带后缀，剥掉再补归一的小写 `.md`（免盘上混入 .MD）。
    stem = normalized[: -len(suffix)] if suffix == ".md" else normalized
    slug = _raw_slug(stem)  # ④
    if not slug:
        raise HTTPException(status_code=400, detail="文件名经规范化后为空，请改名。")
    safe = f"{slug}.md"  # ⑤
    raw = (root / "raw").resolve()
    target = (raw / safe).resolve()  # ⑥ 纵深防御：理论上 ① 已挡住，仍校验落点。
    try:
        target.relative_to(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"路径越界（须在 raw/ 内）：{name}") from None
    return target


def _atomic_write_raw(target: Path, content: str, overwrite: bool) -> int:
    """在**串行 worker turn 内**复检覆盖语义并原子落盘（决策P4.1-2/3）。返回退出码。

    两个并发同名投喂时端点的 existence 预检可能都放过；故这里在真正串行的临界区里再查一次
    `overwrite`，第二个返回 `EXIT_USAGE`（端点转 409）。落盘写同目录临时文件再 `os.replace`
    换名（原子）；IO 异常向上抛，由 worker 归一为 EXIT_AGENT_ERROR（端点转 500）。
    """
    if target.exists() and not overwrite:
        print(f"raw/{target.name} 已存在。")  # 经 worker redirect_stdout → job.output（409 detail）
        return EXIT_USAGE
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)  # UTF-8 原样：不渲染、不重写 [[wikilink]]（raw/ 是未加工源）。
        os.replace(tmp, target)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return EXIT_OK


def _list_pages(root: Path) -> list[dict]:
    """非 config 页清单（排除 config 由 `iter_pages` 兜底，与 check/graph 同口径）。"""
    wiki = root / "wiki"
    pages: list[dict] = []
    for path in iter_pages(wiki):
        meta, _body = load_page(path)  # 容错档：坏 frontmatter 不抛。
        pages.append(
            {
                "path": path.relative_to(root).as_posix(),
                "title": page_title(meta, path.stem),
                "type": page_type(meta),
            }
        )
    return pages


def _heal_preview(root: Path, limit: int, min_refs: int) -> dict:
    """零-LLM 算 heal worklist（== `heal --dry-run --json` 体），返回 `{worklist, postponed}`。

    复用 `heal.compute_worklist`（纯读 `wiki/`、不取 `raw/` 快照、不触 Agentao、不入队，
    决策P4.3-4）；按 `postponed` 标志分两组、各项序列化为 `{target, ref_count, ref_pages}`。
    经 `anyio.to_thread.run_sync` 卸离事件循环调用（决策P4-2）。
    """
    items = compute_worklist(root / "wiki", min_refs=min_refs, limit=limit)
    item = lambda w: {  # noqa: E731
        "target": w.target,
        "ref_count": w.ref_count,
        "ref_pages": list(w.ref_pages),
    }
    return {
        "worklist": [item(w) for w in items if not w.postponed],
        "postponed": [item(w) for w in items if w.postponed],
    }


def _report_response(json_text: str) -> Response:
    """把既有序列化器输出的 JSON 文本**原样**作为响应体。

    红线（决策P4-7 / §11）：必须复用 `format_report(report, json_output=True)`（底层 `report_json`
    是 `ensure_ascii=False, indent=2`、**无尾换行**），并以 `media_type` 直发——绝不返回 dict /
    默认 `JSONResponse`（那会变 compact + `ensure_ascii=True`，与 CLI `--json` 字节不等）。
    """
    return Response(content=json_text, media_type="application/json")


# 历史会话回放时用于把一条 message 的 content 归一为可显示文本：
# ① content 可能是多模态 block 列表（取其中 type=="text" 的片段）；② 剥掉首条 user 里的
# <system-reminder>…</system-reminder> 噪声（与 list_sessions 取 title 的口径一致，避免气泡显示
# 一大段注入提示）。自含小实现，不耦合 agentao 私有 _content_to_text（只用其文档化会话面）。
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _message_text(content: object) -> str:
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    if not isinstance(content, str):
        return ""
    return _SYSTEM_REMINDER_RE.sub("", content).strip()


def _list_raw(root: Path) -> list[dict]:
    """列 `raw/*.md`（**只列、不经 Web 写 raw**，§1 / 决策P4-1）。"""
    raw = root / "raw"
    files: list[dict] = []
    for path in sorted(raw.glob("*.md")):
        if path.is_file():
            files.append({"name": path.name, "size": path.stat().st_size})
    return files


def create_app(
    root: Path,
    *,
    model: str | None = None,
    runner: AgentRunner | None = None,
    session_persist: bool = True,
) -> FastAPI:
    """构造绑定到知识库 `root` 的 FastAPI app。

    Args:
        root: 已 `require_kb_root(writable=True)` 校验过的知识库根（绝对路径）。
        model: `--model` 透传给写作业（C4）与会话嵌入（C5）；None 表示不覆盖、由环境发现。
        runner: 可注入的 `AgentRunner`（测试用 fake，不打真实 LLM）；None 走默认子进程 runner。
        session_persist: 会话落盘开关（P4.2，默认开）；关时退回 P4 纯内存（`--no-session-persist`）。
    """
    app = FastAPI(title="观澜 Web 宿主", docs_url=None, redoc_url=None)
    # 配置挂在 app.state：后续提交的端点（报告/写作业/会话）从这里读 root/model/runner，
    # 避免在模块级全局变量上分裂状态（与 workers=1 单进程单事件循环假设一致，决策P4-2）。
    app.state.root = root
    app.state.model = model
    app.state.runner = runner
    # 单写者作业队列（唯一写作业 ingest 走它，FIFO 串行；决策P4-5）。
    jobs = JobQueue()
    app.state.jobs = jobs
    # 会话表（所有问答走只读嵌入）：persist 开时每轮落 .agentao/sessions/ + 懒恢复（P4.2 决策P4.2-1）。
    conversations = ConversationStore(root, model, persist=session_persist)
    app.state.conversations = conversations

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # 只读自省（P4.4，决策P4.4-2）：app 级 `GET /api/info` 无会话也能答（喂 /status /mode），
    # 零 LLM、不建 agent；仅读 app.state 配置 + 内存会话计数（in-memory，零盘读）。恒 200。
    @app.get("/api/info")
    async def api_info() -> dict:
        return {
            "kb_name": root.name,
            "model": model,  # --model 覆盖（可能 None：未覆盖时由各会话构造期环境发现）
            "mode": "read-only",  # Web 恒只读（决策P4.4-6）
            "persist": session_persist,
            "conversations": conversations.live_count(),
            "max_conversations": MAX_CONVERSATIONS,
        }

    # 浏览（读）：阻塞的文件扫描/渲染一律经 anyio.to_thread 卸离事件循环（决策P4-2）。
    @app.get("/api/pages")
    async def api_pages() -> dict:
        return {"pages": await anyio.to_thread.run_sync(_list_pages, root)}

    @app.get("/api/page")
    async def api_page(path: str = Query(..., description="相对知识库根的页面路径")) -> dict:
        page_file = _safe_wiki_file(root, path)  # 同步、廉价；越界/缺失即抛 HTTPException。
        return await anyio.to_thread.run_sync(render_page, root / "wiki", page_file)

    @app.get("/api/raw")
    async def api_raw() -> dict:
        return {"files": await anyio.to_thread.run_sync(_list_raw, root)}

    # 投喂（P4.1）：把粘贴正文存为 raw/<安全名>.md。校验在端点（快 4xx）、写盘进单写者队列
    # 串行（杜绝与在飞 ingest 的 raw/ 快照窗口竞态，决策P4.1-2）。**同步等作业完成**再返回——
    # 投喂自身极快，但会**排在队列前序写作业之后**（如在飞 ingest），故响应可能等数十秒。
    @app.post("/api/raw")
    async def write_raw(body: RawBody) -> dict:
        if not body.content.strip():  # 空正文
            raise HTTPException(status_code=400, detail="正文不能为空。")
        payload = body.content.encode("utf-8")
        if len(payload) > MAX_RAW_BYTES:
            raise HTTPException(status_code=400, detail=f"正文超过 {MAX_RAW_BYTES} 字节上限。")
        # 文本准入（决策P4.1-5）：拒 NUL / `\t\n\r` 以外的 C0 控制字符（判二进制/垃圾）。
        if "\x00" in body.content or any(
            c < " " and c not in "\t\n\r" for c in body.content
        ):
            raise HTTPException(status_code=400, detail="raw/ 只收文本素材；检测到 NUL/控制字符。")
        target = _safe_raw_target(root, body.name)  # 400/越界即抛，无需进队列
        if target.exists() and not body.overwrite:
            raise HTTPException(
                status_code=409, detail=f"raw/{target.name} 已存在；改名或传 overwrite=true。"
            )
        # 入队写盘作业 + 同步等完成；阻塞经 to_thread 卸离事件循环（决策P4.1-2）。
        # `abandon_on_cancel=True`：等待在**前序写作业**（如在飞 ingest）后排队，可能数十秒甚至——
        # 若该 ingest 子进程挂死——无限久。设为可取消，使客户端断开 / 服务器关停能立即回收**请求
        # 协程**（worker 线程仍跑完作业、文件照常落盘，但请求不再被无限期钉死，关停不被拖住）。
        job = await anyio.to_thread.run_sync(
            jobs.submit_and_wait,
            "raw_write",
            lambda: _atomic_write_raw(target, body.content, body.overwrite),
            abandon_on_cancel=True,
        )
        # 退出码 → HTTP 分流（对齐 §2，**不可**一律塌缩成 409，否则"磁盘满"被误报"已存在"）。
        if job.exit_code == EXIT_USAGE:  # worker 复检撞同名（并发抢占）
            raise HTTPException(status_code=409, detail=job.output or "raw/ 同名文件已存在。")
        if job.exit_code != EXIT_OK:  # 落盘 IO 失败 → 归一为 EXIT_AGENT_ERROR
            raise HTTPException(status_code=500, detail=job.output or "写盘失败。")
        return {"saved": f"raw/{target.name}", "bytes": len(payload)}

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
    @app.get("/graph")
    async def graph(json_only: bool = False) -> RedirectResponse:
        # 用无打印的 build_and_write_graph（非 graph_entrypoint）：worker 的进程级 redirect_stdout
        # 期间不引入并发打印者（决策P4-5 红线）。写失败 → 500，别 302 到缺失文件让用户撞 404。
        try:
            await anyio.to_thread.run_sync(
                functools.partial(build_and_write_graph, root, json_only=json_only)
            )
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"graph 构建失败：{exc}") from exc
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
    @app.post("/api/ingest")
    async def ingest(body: IngestBody) -> dict:
        # target 不在此预校验：run_ingest 内部 _resolve_raw_target 是单一归口（须在 raw/、是 .md、
        # 存在），Web 不旁路 P2 入口校验；非法 target → 作业以 EXIT_USAGE 完成，轮询可见。
        def _job() -> int:
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
    @app.post("/api/heal")
    async def heal(body: HealBody) -> dict:
        def _job() -> HealRun:  # 返回结构化结果（非 int）→ worker 走鸭子分流存 job.result
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

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str) -> dict:
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"未知作业：{job_id}")
        # heal 作业多回一个 `result`（六字段机器回执，经既有序列化器）；ingest/投喂为 null、前端
        # 忽略（决策P4.3-1）。agent 散文一律在 `output`，不进 `result`。
        result = (
            heal_result_dict(job.result.result, job.result.postponed)
            if isinstance(job.result, HealRun)
            else None
        )
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
        if body.conversation_id is None:
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
            try:
                # shield：客户端断开会取消本任务，但**不可**靠 asyncio 取消打断 arun——那只转发
                # token.cancel() 便立刻 re-raise、不等线程收尾，于是 lock 会在后台 executor 线程仍
                # 在跑时被释放，下一轮就可能与残线程并发改 agent.messages / 串错 token。shield 让
                # turn 跑到自然结束（lock 全程持有），杜绝该竞态；代价是断开后该轮仍跑完（本地单
                # 用户、轮次有界，可接受）。**主动停止**走另一条干净路径：停止端点经 conv 上的取消
                # 令牌打断，arun 持锁等线程真正收尾才抛 AgentCancelledError（见下 except）。
                answer = await asyncio.shield(conv.turn(body.message, emit))
                # 答案已完整流出；再渲染安全 markdown HTML（[[页]] → 站内链接）作收尾。渲染失败
                # **不能**丢掉这条成功答案：省略 answer_html，前端回退用纯文本 answer 上屏。
                payload: dict = {"answer": answer, "conversation_id": conv.id}
                try:
                    payload["answer_html"] = await anyio.to_thread.run_sync(
                        render_markdown, answer, root / "wiki"
                    )
                except Exception:  # noqa: BLE001 — 渲染失败仅降级排版，不毁答案、不转 error
                    pass
                emit("done", payload)
            except AgentCancelledError:
                # 用户按停止：已流出的 token 已在前端气泡，发 stopped 收尾（这不是错误，不转 error）。
                emit("stopped", {"conversation_id": conv.id})
            except asyncio.CancelledError:
                raise  # 客户端已断开，流没了，不再 emit
            except Exception as exc:  # noqa: BLE001 — 任何失败都转 error 事件，不泄 traceback 到流
                # 带上 conversation_id：首轮失败时前端据此记住已建会话，避免下次另起新会话堆积。
                emit("error", {"message": f"{type(exc).__name__}: {exc}", "conversation_id": conv.id})
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

    @app.get("/api/conversations")
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
