"""文件上传 / 分类 / 对话附件的确定性 helper（P4.5 / P4.6，从 `app.py` 抽出）。

上传是**人投喂二进制/任意文件**到暂存区 `workspace/uploads/`（非源、多格式、保留原扩展名），
与投喂 `raw/`（`.md`-only 文本）刻意不同。本模块放上传安全落点、原子写、内容/扩展名分类、
以及把上传文件按 agentao `<attachment>` 约定追加进 chat 消息（图像另走视觉通道）的纯函数。
被 `app.py` 的 `POST /api/upload` 与 `POST /api/chat` 路由调用；复用 `rawfeed` 的文件名归口。
"""

from __future__ import annotations

import base64
import contextlib
import errno
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import BinaryIO
from xml.sax.saxutils import quoteattr

from agentao.media_limits import MAX_IMAGE_BYTES, MAX_IMAGES_PER_TURN
from fastapi import HTTPException

from ..rawio import NAME_STEM_MAX_BYTES, cap_utf8
from ..sourcerec import TMP_PREFIX, TMP_SUFFIX, redact_url, source_record_path
from .rawfeed import _normalize_basename, _raw_slug

# ── 文件上传（POST /api/upload）：暂存进 workspace/uploads/，作对话附件 ───────────────
#
# 上传是**人投喂二进制/任意文件**到暂存区（非源）：与投喂（写 raw/、`.md`-only 文本）不同——
# 这里**多格式、不限扩展名、保留原扩展名**（暂存区非源，PDF/docx/任意二进制皆可，决策P4.6-3）。
# 落点 `workspace/uploads/`（P4.5 可写会话里 agent 可读、可解析）。落盘**不进**单写者 JobQueue、不取库级锁
# （#172）：上传只写 `workspace/uploads/`，CLI 与 wiki 写作业都不写这里；分块写临时文件再原子换名。
# 上传后该文件可作 chat **附件**（见 ChatBody.attachments / _augment_with_attachments）。

MAX_UPLOAD_BYTES = 256 * 1024 * 1024  # 上传单文件大小上限（默认 256 MiB，> 投喂的 5 MiB；扫描版 PDF 体量大）。


_UPLOAD_CHUNK = 1024 * 1024  # 分块写盘的块大小：内存里同时只留一块，与文件大小无关
_UPLOAD_HEAD = 65536  # 留给 `_classify_upload` 判类型的文件头


def spool_upload(src: BinaryIO, target_dir: Path) -> tuple[Path, int, bytes]:
    """把上传体**分块**写到 `target_dir` 下的隐藏临时文件（#172）：返回 `(临时文件, 字节数, 文件头)`。

    同步函数，由端点经 `anyio.to_thread` 调用，不占事件循环。内存里同时只有一块（`_UPLOAD_CHUNK`），
    不再把最多 `MAX_UPLOAD_BYTES` 的整个文件读进内存。超限（读到第一个越界的块即停）或空文件 → 删临时
    文件、抛 400。临时文件以点号开头，导入面板的列表不显示它（`workspace._dir_items` 跳过点文件）；
    建在目标目录里，提交时 `os.replace` 是同一文件系统内的原子换名。

    **作用域说准**（见 docs/backlog/notes/sag-2026-09-反向评审.md §1.A）：进入端点处理函数之前，
    multipart **已接收完毕并暂存**（Starlette 的 `UploadFile` 超过 1 MiB 即转存临时文件），故本函数
    **不限制网络接收、也不限制那份临时文件**，它只消除端点内的全量内存读取。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target_dir), prefix=TMP_PREFIX, suffix=TMP_SUFFIX)  # 与网址下载同前缀，启动清理一并收
    tmp = Path(tmp_name)
    size = 0
    head = b""
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := src.read(_UPLOAD_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=400, detail=f"文件超过 {MAX_UPLOAD_BYTES} 字节上限。")
                if len(head) < _UPLOAD_HEAD:
                    head += chunk[: _UPLOAD_HEAD - len(head)]
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="空文件。")
    except BaseException:
        discard_upload(tmp)
        raise
    return tmp, size, head


def commit_upload(tmp: Path, target: Path) -> None:
    """把 `spool_upload` 的临时文件原子换名为 `target`（同名直接覆盖：暂存区非源，决策P4.6-3）。"""
    try:
        os.replace(tmp, target)
    except OSError:
        discard_upload(tmp)
        raise
    # 覆盖了网址导入来的同名文件：旧出处记录已不属于新内容，一并删掉（入库时也会按哈希判作废，这里删掉是让列表不再标 🔗）
    with contextlib.suppress(OSError):
        source_record_path(target).unlink()


def commit_download(tmp: Path, target: Path, record: dict) -> Path:
    """网址下载的提交（P4.31 §2.4，决策P4.31-4）：**不覆盖**，同名加序号；连同出处记录。返回最终落点。

    **调用方须在事件循环线程上、中间不 await 地调用**：与 `POST /api/upload` 的 `commit_upload` 串行，
    「选名 → 换名」之间不会被另一次提交插进来。换名本身也不覆盖（POSIX `os.link`、Windows `os.rename`
    遇已存在即失败），兜住事件循环之外的写入者（用户直接往 `uploads/` 拷文件）。

    出处记录先写临时文件、文件换名后再换名：中途失败只会留下「有文件、没记录」，等同一个普通上传。
    """
    rec_tmp = tmp.with_name(tmp.stem + "-source.tmp")  # 仍是 `.upload-*.tmp`：中途被杀时启动清理一并收掉
    try:
        # 硬链接 / replace 都保留 inode 的 mtime：记下它，列表判 🔗 时连同字节数一起比（文件被别的途径换掉就对不上）
        record = {**record, "mtime_ns": tmp.stat().st_mtime_ns}
        # 落盘这一处再脱敏一次（下载器已脱敏；这里是记录写进磁盘的唯一出口，不管记录是谁造的都不留签名）
        for key in ("url", "final_url"):
            if isinstance(record.get(key), str):
                record[key] = redact_url(record[key])
        rec_tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        stem, suffix = target.stem, target.suffix
        n = 1
        while True:
            candidate = target if n == 1 else target.with_name(f"{stem}-{n}{suffix}")
            try:
                if os.name == "nt":  # pragma: no cover - CI 主跑 POSIX；Windows 的 rename 遇已存在即失败
                    os.rename(tmp, candidate)
                else:
                    _link_no_replace(tmp, candidate)
                break
            except FileExistsError:
                n += 1
                if n > 999:
                    raise OSError(f"同名文件太多：{target.name}") from None
    except BaseException:
        discard_upload(tmp)
        rec_tmp.unlink(missing_ok=True)
        raise
    # 文件已落定：记录换名失败只丢出处（等同普通上传），不能把已保存的文件报成「写盘失败」
    try:
        os.replace(rec_tmp, source_record_path(candidate))
    except OSError:
        rec_tmp.unlink(missing_ok=True)
    return candidate


# 不支持硬链接的文件系统（exFAT、部分 SMB / FUSE 挂载）上 `os.link` 报这些错，而不是 FileExistsError。
_NO_HARDLINK_ERRNOS = {errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EXDEV, errno.ENOSYS, errno.EMLINK}


def _link_no_replace(tmp: Path, candidate: Path) -> None:
    """把 `tmp` 放到 `candidate`，已存在就抛 FileExistsError、绝不覆盖（决策P4.31-4）。

    首选 `os.link`（原子、遇已存在即失败）。文件系统不支持硬链接时退一步：先以 `O_EXCL` 建一个空的占位文件
    **占住名字**（已存在同样失败），再 `os.replace` 换成真文件——被替换的只会是自己刚建的占位。
    """
    try:
        os.link(tmp, candidate)
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in _NO_HARDLINK_ERRNOS:
            raise
        fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)  # 已存在 → FileExistsError
        os.close(fd)
        try:
            os.replace(tmp, candidate)
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
        return
    with contextlib.suppress(OSError):  # 已链接成功、文件已落定；临时名删不掉由启动清理收掉
        tmp.unlink()


def discard_upload(tmp: Path) -> None:
    with contextlib.suppress(OSError):
        tmp.unlink()


STALE_UPLOAD_TMP_SECONDS = 3600  # 隐藏临时文件超过这么久没动过，才算上次进程中途退出留下的残留


def sweep_stale_upload_tmps(uploads_dir: Path, *, now: float | None = None) -> int:
    """删掉 `spool_upload` 中途被杀（kill / 断电）留下的 `.upload-*.tmp`（可写宿主启动时调，#172）。

    这些文件是点文件，导入面板看不见，又可能各有上百 MiB。只删 mtime 超过 `STALE_UPLOAD_TMP_SECONDS`
    的：同一个库若还开着另一个 Web 进程、正在写某份上传，它的临时文件在持续更新，不会被误删。返回删除个数。
    """
    if not uploads_dir.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - STALE_UPLOAD_TMP_SECONDS
    removed = 0
    for path in uploads_dir.glob(f"{TMP_PREFIX}*{TMP_SUFFIX}"):
        with contextlib.suppress(OSError):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
    # Agent 直接用 pdf-to-markdown skill 转 uploads/ 里的网页时，HTML 预处理的隐藏目录 `.htmlprep-*`（P4.31 §4）
    # 在进程被杀时会留下；同样只清超时的
    for path in uploads_dir.glob(".htmlprep-*"):
        with contextlib.suppress(OSError):
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path)
                removed += 1
    return removed


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
    if len(suffix.encode("utf-8")) > 16:  # `1.<几百字节>` 这种「扩展名」不是扩展名：整段并进 stem 一起截短
        suffix = ""
    stem = normalized[: -len(suffix)] if suffix else normalized
    # Linux 文件名按字节计 255：stem 按 UTF-8 字节截短（不切坏字符），给 `-<序号>`、出处记录 `.<名>.source.json`
    # 留出余量。以前超长中文名上传会在换名时 ENAMETOOLONG、报 500（P4.31 代码评审）。
    slug = _raw_slug(stem)
    capped = cap_utf8(slug, NAME_STEM_MAX_BYTES - 9)
    if capped != slug:
        # 截短后加原名的短哈希：前 170 多字节相同的两个长文件名不会互相覆盖；同一个名字照旧落到同一处（覆盖语义不变）
        slug = f"{capped}-{hashlib.sha1(slug.encode('utf-8')).hexdigest()[:8]}"
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


def _classify_upload(target: Path, data: bytes) -> str:
    """判上传是 `image` / `text` / `binary`，供前端徽章配图（图像 → 缩略图）与附件分流。

    图像按扩展名白名单（`_IMAGE_EXT_TO_MIME`，与视觉通道同口径）先判；其余扩展名命中文本表且能
    UTF-8 解码 → text；再按内容兜底：前 64KiB 含 NUL → binary；能 UTF-8 解码 → text；否则 binary。
    """
    if target.suffix.lower() in _IMAGE_EXT_TO_MIME:
        return "image"
    head = data[:_UPLOAD_HEAD]
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
