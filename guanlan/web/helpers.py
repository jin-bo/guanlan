"""Web 宿主的无状态 helper（P4）。

这里只放**与 `create_app` 闭包无关、纯函数式**的辅助：路径穿越防御（`_safe_*`）、只读
图像/报告响应构造、页面/源清单派生、heal/audit 预览归口、SSE 编码、历史消息归一。它们都把
`root` 当显式入参、不捕获任何 app 级共享状态，故从 `app.py` 抽出后可独立测试、并让 `app.py`
回归"只做 HTTP 接线"。`STATIC_DIR` / `_NoCacheStatic` 仍留在 `app.py`（与静态挂载强绑定、且被
测试直接 import）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from ..audit import audit_preview
from ..heal import compute_worklist
from ..pages import iter_pages, load_page, page_title, page_type
from ..rawio import find_source_page
from .uploads import _IMAGE_EXT_TO_MIME


def _sse(kind: str, data: object) -> str:
    """编码一个 SSE 事件帧（`event:`/`data:` + 空行）。data 一律 JSON（ensure_ascii=False）。"""
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


def _safe_raw_file(root: Path, name: str) -> Path:
    """把预览 `name` 解析为 `raw/` 内存在的 `.md`；越界 409、非 md/缺失 404（只读，路径穿越防御）。

    与 `_safe_wiki_file` 同骨架，但夹在 `raw/`、强制 `.md`（raw 仅 `.md` 源、预览复用 `render_page`
    只对 markdown 有意义）。**不**复用 `_safe_raw_target`——那是*写*目标校验器（撞已存在抛 409），
    语义相反；本端点纯读。
    """
    raw = (root / "raw").resolve()
    candidate = (root / "raw" / name).resolve()
    try:
        candidate.relative_to(raw)
    except ValueError:
        raise HTTPException(status_code=409, detail=f"路径越界（须在 raw/ 内）：{name}") from None
    if candidate.suffix.lower() != ".md" or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"raw 文件不存在或非 .md：{name}")
    return candidate


# raw 嵌图扩展名→MIME 白名单（与 convert 的 `IMAGE_EXTS` 对齐，P5.2.1）；非表内一律 404，杜绝把
# 任意 raw/ 文件 inline 出去。复用上传通道的图像表再补 convert 也落的 bmp/tiff/svg。
_RAW_IMAGE_EXT_TO_MIME = {
    **_IMAGE_EXT_TO_MIME,
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
}


def _image_file_response(target: Path) -> FileResponse:
    """按 `_RAW_IMAGE_EXT_TO_MIME` 白名单发图像原字节；非表内一律 404（杜绝把任意文件 inline 出去）。

    两处只读图像端点（`/api/raw/image` raw 嵌图、`/api/workspace/raw` parsed 缩略图）**共用一份白名单**，
    覆盖 convert/`IMAGE_EXTS` 全部落盘扩展名（png/jpg/gif/webp/bmp/tif/tiff/svg）——否则 parsed 预览会对
    bmp/tiff/svg 等已收集图片显示断图。

    SVG 是**活跃内容**（可内嵌 `<script>`/事件处理）：以 `image/svg+xml` 从本源 inline 直发会带来同源
    脚本执行风险——直接导航/新标签打开该 URL 即以「文档」身份渲染并执行脚本、进而调用本地 Web API。故 svg
    不以可执行文档身份交付：① `Content-Disposition: attachment` 让**直接导航变下载**（`<img src>` 等子资源
    加载**不受**该头影响、缩略图预览仍正常渲染，且 `<img>` 上下文本就不执行脚本）；② `CSP: default-src
    'none'; sandbox` + `nosniff` 双保险关死脚本与 MIME 嗅探。栅格图无活跃内容、按常规 inline 直发。
    """
    mime = _RAW_IMAGE_EXT_TO_MIME.get(target.suffix.lower())
    if mime is None:
        raise HTTPException(status_code=404, detail="仅供图像：非图像扩展名不经本端点提供。")
    if target.suffix.lower() == ".svg":
        return FileResponse(
            target,
            media_type=mime,
            headers={
                "Content-Disposition": "attachment",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )
    return FileResponse(target, media_type=mime)


def _safe_raw_image(root: Path, rel: str) -> Path:
    """把 raw 预览的图片 `rel` 解析为 `raw/images/` 内存在的文件；越界 409、缺失 404（只读，穿越防御）。

    `rel` 是 raw md 内重写后的相对路径（如 `images/<slug>/<slug>-1.jpg`，相对 `raw/`）。**夹在
    `raw/images/` 子树**（而非整个 `raw/`）——只服务 convert 随源落盘的嵌图，绝不经本端点漏出
    `raw/*.md` 源文本或其它文件。扩展名白名单在端点再判（镜像 `/api/workspace/raw`）。
    """
    images = (root / "raw" / "images").resolve()
    candidate = (root / "raw" / rel).resolve()
    try:
        candidate.relative_to(images)
    except ValueError:
        raise HTTPException(
            status_code=409, detail=f"路径越界（须在 raw/images/ 内）：{rel}"
        ) from None
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"raw 图片不存在：{rel}")
    return candidate


def _wiki_image_src(root: Path, page_file: Path):
    """供 wiki 页渲染把 `../../raw/images/<slug>/…` 相对嵌图改写为只读 `/api/raw/image` 端点 URL。

    ingest（按 skill 约定）在 wiki 页里用 `../../raw/images/<slug>/<文件名>` 引用随源落盘的嵌图——
    浏览器无法解析这种库内相对路径（页在 `wiki/<类型>/`、图在 `raw/images/`），不改写就渲染成裂图。
    解析 src **相对页文件父目录**、`resolve()` 后须落在 `raw/images/` 子树内才改写（路径穿越防御，
    `/api/raw/image` 的 `_safe_raw_image` 再校验一次）；不指向 raw/images/ 的相对图原样保留（不裂得更糟）。
    """
    raw = (root / "raw").resolve()
    raw_images = (root / "raw" / "images").resolve()

    def _src(rel: str) -> str:
        candidate = (page_file.parent / rel).resolve()
        try:
            candidate.relative_to(raw_images)
        except ValueError:
            return rel  # 不在 raw/images/ 内（外链已被 _is_relative_local 排除）→ 原样
        return "/api/raw/image?path=" + quote(candidate.relative_to(raw).as_posix())

    return _src


def _workspace_image_src(root: Path, page_file: Path):
    """供 parsed/uploads 预览把 `images/<slug>/…` 相对嵌图改写为只读 `/api/workspace/raw` 端点 URL。

    P4.6.1 解析把图落 `workspace/parsed/images/<slug>/`、引用写 `images/<slug>/…`（相对 parsed md）。
    预览渲染须把这些相对图改写到 scratch 图片端点才能显示。解析 src 相对页文件父目录、`resolve()` 后须
    落在 `workspace/` 子树内才改写（`/api/workspace/raw` 的 `_safe_workspace_scratch` 再校验白名单子目录
    + 图像扩展名）；否则原样保留。
    """
    workspace = (root / "workspace").resolve()

    def _src(rel: str) -> str:
        candidate = (page_file.parent / rel).resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError:
            return rel
        return "/api/workspace/raw?path=" + quote(candidate.relative_to(root).as_posix())

    return _src


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


def _audit_preview(root: Path, limit: int) -> dict:
    """零-LLM 算 audit 预览（== `audit --dry-run --json` 体），返回 `{groups, postponed}`。

    复用 `audit.audit_preview` 单一归口（决策P4.12-1/4：预览序列化 CLI/Web 共口径，宿主不重写）；
    纯读 `wiki/`、不取 `raw/` 快照、不触 Agentao、不入队。经 `anyio.to_thread.run_sync` 卸离事件循环（决策P4-2）。
    """
    return audit_preview(root / "wiki", limit=limit)


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
    """列 `raw/*.md`，并标记是否「已收录」（**只列、不经 Web 写 raw**，§1 / 决策P4-1）。

    `ingested` 是纯读派生信号、不落盘、可随时重算：raw 源经 ingest 会在
    `wiki/sources/<slug>.md` 落一篇摘要页（slug = 同源文件名 kebab-case，见 SKILL.md
    与 `raw_slug` 归口），故同名 source 页存在即视为该源已收录。定位交给
    `find_source_page`——它在精确 slug 之外容忍 `.`/`-` 归一分歧（Agent 常把枚举序号
    `1.` 命成 `1-`），免得已建好的页被误判「未收录」。前端默认只显未收录、
    一键可切看已收录（非破坏：永不隐藏磁盘文件、已收录源仍可预览/重投 ingest）。
    """
    raw = root / "raw"
    sources = root / "wiki" / "sources"
    files: list[dict] = []
    for path in sorted(raw.glob("*.md")):
        if path.is_file():
            ingested = find_source_page(sources, path.stem) is not None
            files.append(
                {"name": path.name, "size": path.stat().st_size, "ingested": ingested}
            )
    return files
