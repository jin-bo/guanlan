"""文件上传 / 分类 / 对话附件的确定性 helper（P4.5 / P4.6，从 `app.py` 抽出）。

上传是**人投喂二进制/任意文件**到暂存区 `workspace/uploads/`（非源、多格式、保留原扩展名），
与投喂 `raw/`（`.md`-only 文本）刻意不同。本模块放上传安全落点、原子写、内容/扩展名分类、
以及把上传文件按 agentao `<attachment>` 约定追加进 chat 消息（图像另走视觉通道）的纯函数。
被 `app.py` 的 `POST /api/upload` 与 `POST /api/chat` 路由调用；复用 `rawfeed` 的文件名归口。
"""

from __future__ import annotations

import base64
import contextlib
import os
import tempfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

from agentao.media_limits import MAX_IMAGE_BYTES, MAX_IMAGES_PER_TURN
from fastapi import HTTPException

from ..errors import EXIT_OK
from .rawfeed import _normalize_basename, _raw_slug

# ── 文件上传（POST /api/upload）：暂存进 workspace/uploads/，作对话附件 ───────────────
#
# 上传是**人投喂二进制/任意文件**到暂存区（非源）：与投喂（写 raw/、`.md`-only 文本）不同——
# 这里**多格式、不限扩展名、保留原扩展名**（暂存区非源，PDF/docx/任意二进制皆可，决策P4.6-3）。
# 落点 `workspace/uploads/`（P4.5 可写会话里 agent 可读、可解析），落盘经单写者 JobQueue 串行。
# 上传后该文件可作 chat **附件**（见 ChatBody.attachments / _augment_with_attachments）。

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 上传单文件大小上限（默认 50 MiB，> 投喂的 5 MiB；PDF 体量大）。

# 图像扩展名 → MIME（与 agentao 视觉通道同口径白名单，镜像 chahua `_EXT_TO_MIME`）：命中者除
# `<attachment>` 标签外，还经 `arun(images=)` 传 base64 走视觉通道；其余扩展名只发标签（agent 自己
# 用读工具取内容）。`.svg` 刻意不入此表（文本格式、视觉通道不收），仍按文本附件分类。
_IMAGE_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# 已知文本扩展名：命中即按文本附件处理（仍以 UTF-8 解码成功为准）。空扩展名也并入（README 等无扩展名
# 文本）。不在表内者再以「前 64KiB 能否 UTF-8 解码且无 NUL」兜底判文本，故本表只是快路径、非白名单。
_TEXT_EXTENSIONS = frozenset(
    {
        "", ".md", ".markdown", ".txt", ".text", ".rst", ".org", ".log",
        ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
        ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".scss",
        ".sh", ".bash", ".zsh", ".c", ".h", ".cpp", ".hpp", ".cc", ".java", ".go", ".rs",
        ".rb", ".php", ".pl", ".lua", ".sql", ".xml", ".svg", ".tex", ".bib", ".r", ".jl",
    }
)


def _safe_workspace_target(root: Path, subdir: str, filename: str) -> Path:
    """把上传文件名解析为 `<kb>/workspace/<subdir>/<安全名>`（保留原扩展名，决策P4.6-3）。

    与 `_safe_raw_target` 同骨架（剥目录 → NFKC + 映射规范化 + 剥空白 → slug stem），但**保留**
    原扩展名（小写归一）——agent 要按扩展名挑解析器，故不强制 `.md`、也无 `.md`-only 准入。
    落点经 resolve 越界校验须在 `workspace/<subdir>/` 内（纵深防御，决策P4.6-3/5）。
    """
    normalized = _normalize_basename(filename)  # 剥目录 + NFKC + 映射 + 剥空白（共用归口）
    suffix = Path(normalized).suffix.lower()
    stem = normalized[: -len(suffix)] if suffix else normalized
    slug = _raw_slug(stem)
    if not slug:
        raise HTTPException(status_code=400, detail="文件名经规范化后为空，请改名。")
    safe = f"{slug}{suffix}"  # 保留原扩展名（小写）
    base_dir = (root / "workspace" / subdir).resolve()
    target = (base_dir / safe).resolve()
    try:
        target.relative_to(base_dir)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"路径越界（须在 workspace/{subdir}/ 内）：{filename}"
        ) from None
    return target


def _atomic_write_upload(target: Path, data: bytes) -> int:
    """在串行 worker turn 内原子落盘二进制上传（决策P4.6-3）。同名直接覆盖（暂存区、非源）。

    暂存区语义：同名重传即替换（content 由文件名定位，重复 attach 同一文件不该 409 卡住）；与投喂
    （写 raw/ 源、默认不覆盖）刻意不同。建目录 → 写同目录临时文件 → `os.replace` 原子换名。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return EXIT_OK


def _classify_upload(target: Path, data: bytes) -> str:
    """判上传是 `image` / `text` / `binary`，供前端徽章配图（图像 → 缩略图）与附件分流。

    图像按扩展名白名单（`_IMAGE_EXT_TO_MIME`，与视觉通道同口径）先判；其余扩展名命中文本表且能
    UTF-8 解码 → text；再按内容兜底：前 64KiB 含 NUL → binary；能 UTF-8 解码 → text；否则 binary。
    """
    if target.suffix.lower() in _IMAGE_EXT_TO_MIME:
        return "image"
    head = data[:65536]
    if target.suffix.lower() in _TEXT_EXTENSIONS:
        try:
            head.decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            return "binary"
    if b"\x00" in head:
        return "binary"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "binary"


def _safe_upload_file(root: Path, rel: str) -> Path:
    """把附件引用 `rel` 解析为 `workspace/uploads/` 内存在的文件；越界 400、缺失 404（路径穿越防御）。

    `rel` 是 `POST /api/upload` 回传的相对路径（`workspace/uploads/<名>`）。绝对路径 / `..` 越界经
    `resolve()` + `relative_to(uploads)` 拦下（与 `_safe_wiki_file` 同精神，决策P4.6-5）。
    """
    uploads = (root / "workspace" / "uploads").resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(uploads)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"附件路径越界（须在 workspace/uploads/ 内）：{rel}"
        ) from None
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"附件不存在：{rel}")
    return candidate


def _classify_by_ext(path: Path) -> str:
    """按**扩展名**判 `image`/`text`/`binary`（目录列表用，免逐文件读盘）。

    列表的 `kind` 只驱动前端两处判断——图像缩略图（`kind=="image"`）与 `uploads/*.md` 直接晋级
    （`kind=="text"` 且 `.md`），扩展名足矣。内容嗅探的 `_classify_upload` 仅上传端点用（字节已在
    内存），不必为列表对每个文件多开一次盘 + 读 64KiB（大 uploads/ 目录下每次列举/导航/刷新都重读）。
    """
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXT_TO_MIME:
        return "image"
    if suffix in _TEXT_EXTENSIONS:
        return "text"
    return "binary"


def _attachment_tag(rel: str, mime: str | None) -> str:
    """按 agentao 附件约定渲染一枚自闭合标签：`<attachment uri="…" [mimetype="…"]/>`。

    与 agentao `_render_image_reference_fallback` 的降级标签**逐字同构**（uri 即传给 `images=` 的
    `_source`）：模型拒图时 agentao 把多模态消息替换为同格式标签文本，prompt 里前后引用一致。
    属性值一律 `quoteattr` 转义（安全名虽已剥引号，仍按约定转义、不赌上游规则）。
    """
    attrs = f"uri={quoteattr(rel)}"
    if mime:
        attrs += f" mimetype={quoteattr(mime)}"
    return f"<attachment {attrs}/>"


def _augment_with_attachments(
    root: Path, message: str, attachments: list[str]
) -> tuple[str, list[dict[str, str]]]:
    """把附件按 agentao `<attachment>` 约定追加进消息；图像另解析为 `arun(images=)` 载荷。

    返回 `(发给 agent 的消息, images)`。所有附件（图/非图）都在消息末尾追加
    `<attachment uri="workspace/uploads/<安全名>" …/>` 标签（非图不带 mimetype）——agent 凭
    只读工具自己读取文本附件、二进制由其如实说明无法解析（镜像 chahua `_attach_files_to_text`）。
    图像附件（扩展名命中 `_IMAGE_EXT_TO_MIME`、大小 ∈ (0, MAX_IMAGE_BYTES]、单轮前
    MAX_IMAGES_PER_TURN 张）额外读盘 base64 成 `{data, mimeType, _source}` 走视觉通道；超限/
    超额者只留标签（与降级后的文本引用同形）。**模型不支持视觉的降级归 agentao**：
    `_is_image_unsupported` 命中即自动以同格式标签重试，宿主不做能力探测、不维护视觉模型表。
    路径经 `_safe_upload_file` 校验（越界 400 / 缺失 404）；base64 只进本轮请求，
    **不落会话快照**（见 chat._lean_messages）。前端气泡只显示原始 message + 徽章/缩略图。
    """
    tags: list[str] = []
    images: list[dict[str, str]] = []
    for rel in attachments:
        path = _safe_upload_file(root, rel)
        canon = f"workspace/uploads/{path.name}"  # 规范 rel：与落盘安全名一致（uri == _source）
        mime = _IMAGE_EXT_TO_MIME.get(path.suffix.lower())
        tags.append(_attachment_tag(canon, mime))
        if mime is None or len(images) >= MAX_IMAGES_PER_TURN:
            continue  # 非图 / 超单轮张数上限：只留标签
        try:
            data = path.read_bytes()
        except OSError:  # 校验后到读取间被并发删除/改形（TOCTOU）→ 当缺失处理，不抛未捕获 500
            raise HTTPException(status_code=404, detail=f"附件不存在：{rel}") from None
        if not data or len(data) > MAX_IMAGE_BYTES:
            continue  # 空 / 超视觉单图上限：不走视觉通道，标签仍在（文本引用）
        images.append(
            {"data": base64.b64encode(data).decode("ascii"), "mimeType": mime, "_source": canon}
        )
    if not tags:
        return message, images
    appendix = "\n".join(tags)  # 一行一枚标签；与正文以空行分隔（同 agentao 降级附录格式）
    return (f"{message}\n\n{appendix}" if message.strip() else appendix), images
