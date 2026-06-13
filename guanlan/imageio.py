"""图片随源落盘 / 重整的 transport-neutral 归口（P4.6.1，从 `convert.py` 抽出，决策P4.6.1-4）。

「CLI convert（落 `raw/`）/ Web 解析（落 `workspace/parsed/`）/ 晋级连图（parsed→raw）/ 拆分合并
re-localize（parsed 内）」四处共用**同一套**图片引用准入安全闸、首现序编号、引用重写、staging-swap
落盘原语，否则多处漂移（安全敏感 + 引用一致性）。故把这些**无状态原语**沉到本模块，**`rawio.py`
一字不改**（决策P4.6.1-1：图片落盘是 convert/imageio 专属、与 raw 文本写正交）。

三类收集语义（共用安全闸/编号/重写，落盘语义不同）：

- **`collect_and_rewrite`（宽松，convert + 解析用）**：从转换器 temp 树 / parsed 产物扫引用、按
  安全闸**收集字节入内存**、编号、重写；未准入引用（远程/越界/非白名单）**原样保留**、计入 `skipped`，
  **不报错**——它服务「转换产物落源」，远程图本就该保留为外链。
- **`collect_for_promotion`（严格，晋级用，决策P4.6.1-15）**：晋级要求 raw 自洽，故**相对**引用必须准入
  + 存在，否则 `raise ValueError`（端点转 400「先 relocalize」）；**外链**（带 scheme / `//` / `/` / `~`）
  原样保留、不视为错误。额外记每图 realpath + SHA256 指纹供写锁内 TOCTOU 复检（决策P4.6.1-16）。
- **`relocalize`（磁盘 copy + 全局 GC，拆分/合并用，决策P4.6.1-5/6）**：对**磁盘上既有** parsed md +
  图文件做 copy 到「该文件名下 image 目录」+ 改名编号 + 重写引用，原图仅在**全局零引用**时回收。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import EXIT_OK
from .rawio import atomic_write_raw

# 图片扩展白名单（与 skill `SUPPORTED_EXTENSIONS` 的图片子集对齐，决策P5.2.1-5(e)）。
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}
# 容量上限（决策P5.2.1-11 / P4.6.1-8）——护入内存 + 下游 ingest 的 snapshot_raw 递归 hash 整个 raw/。
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 单图 ≤ 20 MiB
MAX_IMAGES_TOTAL_BYTES = 200 * 1024 * 1024  # 单次累计 ≤ 200 MiB
MAX_IMAGE_COUNT = 500  # 单次张数 ≤ 500

# markdown 图片语法 `![alt](pre url post)`：url 取到空白或 `)` 为止；分组用于「只换 url、保 alt/title」。
# 仅匹配 markdown 图片语法——HTML `<img>` 不在范围（mineru/marker 产物用 markdown，§0.1 实测）。
_MD_IMAGE = re.compile(r"(?P<head>!\[[^\]]*\]\()(?P<pre>\s*)(?P<url>[^)\s]+)(?P<post>[^)]*)\)")
# scheme（`http:`/`https:`/`file:`/`data:`/Windows 盘符 `C:` 等）：拒一切带 scheme 的 url（决策P5.2.1-5(a)）。
_URL_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*:")


class ConvertError(Exception):
    """转换 / 图片处理失败（全后端耗尽 / 图片超容量）。命令层 / 端点映射 EXIT_USAGE / 4xx。"""


@dataclass(frozen=True)
class ConvertedImage:
    """一张随转换文件落盘的图片（决策P5.2.1-4）。"""

    name: str  # 落盘后文件名 <stem>-<n>.<ext>（n 从 1，ext 取原图后缀小写）
    data: bytes  # 图片字节，逐字拷贝、不重编码（决策P5.2.1-8）


@dataclass(frozen=True)
class ConvertResult:
    """转换内核 / 解析产物：重写后 markdown + 收集到的图片（决策P5.2.1-4）。"""

    markdown: str  # 引用已重写为 images/<stem>/<stem>-<n>.<ext>
    images: tuple[ConvertedImage, ...] = ()  # 按 md 内首次出现序；无图 → 空 tuple
    skipped: int = 0  # 未准入/未搬运的图片引用数（原样保留），供命令壳 stderr 计数


@dataclass(frozen=True)
class PromotionImage:
    """晋级连图一起搬的一张图（决策P4.6.1-12/15/16）：含 realpath + SHA256 供写锁内 TOCTOU 复检。"""

    name: str  # 归一后落盘名 <target_stem>-<n>.<ext>
    data: bytes  # 入队前读入内存的字节（写锁内 stage-swap 用）
    source: Path  # 入队前解析的 realpath（写锁内复读复算 SHA256 用）
    sha256: str  # 入队前字节 SHA256（写锁内复检，决策P4.6.1-16）


@dataclass(frozen=True)
class PromotionCollection:
    """`collect_for_promotion` 产物：重写后 markdown + 收集图（含指纹）+ 源 md 指纹。"""

    markdown: str  # 引用已按 target_stem 归一重写为 images/<target_stem>/…
    images: tuple[PromotionImage, ...] = ()
    skipped: int = 0  # 原样保留的外链引用数（带 scheme/绝对/~，不搬不报错）


def _is_external_ref(url: str) -> bool:
    """判一条图片引用是否为**外链**（带 scheme / 协议相对 `//` / 绝对 `/` / `~`）。

    外链在晋级时**原样保留**（不搬、不报错，决策P4.6.1-15）：它指向库外资源，本就不该被本地化。
    其余（纯相对路径）才是「本地图、须准入 + 搬运」的对象。判据与 `_admit_image_ref` 的 (a) 同源。
    """
    return bool(_URL_SCHEME.match(url)) or url.startswith(("//", "/", "~"))


def _admit_image_ref(url: str, md_parent: Path, root: Path) -> Path | None:
    """图片引用准入安全闸（决策P5.2.1-5，**安全敏感**，五条 AND）。命中 → 返回解析后 realpath；否则 None。

    (a) 仅相对路径：拒一切带 scheme（`http(s):`/`file:`/`data:`/Windows `C:`）/协议相对 `//`/绝对 `/`/`~`；
    (b) `urllib.parse.unquote` 解码（挡 `%2e%2e` 等编码穿越）后按相对 `md_parent` 解析；
    (c) `os.path.realpath`（解 symlink）后必须 `relative_to(root)`——越界/symlink-逃逸一律拒收；
    (d) 落点须是真实普通文件（`is_file()` 且非 symlink 自身、非目录/FIFO/设备）；
    (e) 后缀（小写）∈ `IMAGE_EXTS` 白名单。

    `root` = 容量边界根（convert=tmp_root；解析/晋级/relocalize=`workspace/parsed/`，决策P4.6.1-8）。
    """
    if _is_external_ref(url):  # (a)
        return None
    decoded = urllib.parse.unquote(url)  # (b)
    candidate = Path(os.path.realpath(md_parent / decoded))  # (c) 解 symlink
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_symlink() or not candidate.is_file():  # (d)
        return None
    if candidate.suffix.lower() not in IMAGE_EXTS:  # (e)
        return None
    return candidate


def _enforce_caps(n: int, size: int, total: int) -> None:
    """容量三道闸（决策P5.2.1-11）：超限 → ConvertError（非静默丢图）。`n`=本张序号、`total`=累计前值。"""
    if n > MAX_IMAGE_COUNT:
        raise ConvertError(f"图片张数超过上限 {MAX_IMAGE_COUNT}；请拆分文档或用 --dry-run 排查。")
    if size > MAX_IMAGE_BYTES:
        raise ConvertError(f"单图超过上限 {MAX_IMAGE_BYTES} 字节；请降分辨率后重转。")
    if total + size > MAX_IMAGES_TOTAL_BYTES:
        raise ConvertError(f"图片累计超过上限 {MAX_IMAGES_TOTAL_BYTES} 字节；请拆分文档后重转。")


def collect_and_rewrite(
    markdown: str, *, produced_md: Path, root: Path, stem: str
) -> ConvertResult:
    """**宽松**收集（convert + 解析用）：扫图片引用、收集字节、按首次出现序编号、重写引用（决策P5.2.1-3/5）。

    引擎无关：mineru 与 marker 的引用**都相对产物 md 自身目录**（§0.1 实测），故统一按相对
    `produced_md.parent` 解析（叠加 `_admit_image_ref` 安全闸），不写引擎分支。按解析后 realpath
    去重（同图多引复用同号）；逐张累计校验容量三上限，任一超限 → `ConvertError`（非静默丢弃）。
    **未准入引用原样保留、计入 `skipped`、不报错**（远程图该留外链）。
    """
    md_parent = produced_md.parent
    collected: dict[str, ConvertedImage] = {}  # realpath -> 已收集图片（去重）
    order: list[ConvertedImage] = []  # 按首次出现序
    stats = {"skipped": 0, "total_bytes": 0}

    def repl(m: re.Match[str]) -> str:
        resolved = _admit_image_ref(m.group("url"), md_parent, root)
        if resolved is None:  # 未准入 → 原样保留引用、不编号
            stats["skipped"] += 1
            return m.group(0)
        key = str(resolved)
        img = collected.get(key)
        if img is None:
            n = len(order) + 1
            # 先 stat 校验单图/累计上限，**再** read_bytes：否则一张超大图会先被整盘读入内存、
            # 才被拒，§2.1 容量闸的「护入内存」承诺落空（决策P5.2.1-11）。
            size = resolved.stat().st_size
            _enforce_caps(n, size, stats["total_bytes"])
            data = resolved.read_bytes()
            stats["total_bytes"] += len(data)
            img = ConvertedImage(name=f"{stem}-{n}{resolved.suffix.lower()}", data=data)
            collected[key] = img
            order.append(img)
        new_url = f"images/{stem}/{img.name}"  # <md>.md → images/<stem>/ 的正确相对路径
        return f"{m.group('head')}{m.group('pre')}{new_url}{m.group('post')})"

    rewritten = _MD_IMAGE.sub(repl, markdown)
    return ConvertResult(markdown=rewritten, images=tuple(order), skipped=stats["skipped"])


def collect_for_promotion(
    markdown: str, *, source_md: Path, root: Path, stem: str
) -> PromotionCollection:
    """**严格**收集（晋级用，决策P4.6.1-15/16）：相对引用必须准入 + 存在，否则 `ValueError`（端点 400）。

    与 `collect_and_rewrite` 共用安全闸/首现序编号/去重/容量闸/引用重写，但三点不同：
    1. **相对引用未准入/悬空 → `raise ValueError`**（不静默保留）——晋级要 raw 自洽，断链须先 relocalize；
    2. **外链原样保留、计 `skipped`、不报错**（带 scheme/`//`/`/`/`~`，指向库外，本不该本地化）；
    3. 额外记每图 **realpath + SHA256**（入队前字节哈希），供写锁内 TOCTOU 复检（决策P4.6.1-16）。

    `root` = `workspace/parsed/`（含越界/symlink 防御）；`stem` = 目标 raw slug（target_stem，决策P4.6.1-13）。
    """
    md_parent = source_md.parent
    collected: dict[str, PromotionImage] = {}  # realpath -> 已收集图（去重）
    order: list[PromotionImage] = []
    stats = {"skipped": 0, "total_bytes": 0}

    def repl(m: re.Match[str]) -> str:
        url = m.group("url")
        if _is_external_ref(url):  # 外链：原样保留、不搬、不报错
            stats["skipped"] += 1
            return m.group(0)
        resolved = _admit_image_ref(url, md_parent, root)
        if resolved is None:  # 相对但未准入（越界/symlink/非白名单/悬空）→ 硬错
            raise ValueError(
                f"图片引用无法解析或越界：{url}（请先在暂存区「重整」修复断链后再晋级）。"
            )
        key = str(resolved)
        img = collected.get(key)
        if img is None:
            n = len(order) + 1
            size = resolved.stat().st_size
            _enforce_caps(n, size, stats["total_bytes"])
            data = resolved.read_bytes()
            stats["total_bytes"] += len(data)
            img = PromotionImage(
                name=f"{stem}-{n}{resolved.suffix.lower()}",
                data=data,
                source=resolved,
                sha256=hashlib.sha256(data).hexdigest(),
            )
            collected[key] = img
            order.append(img)
        new_url = f"images/{stem}/{img.name}"
        return f"{m.group('head')}{m.group('pre')}{new_url}{m.group('post')})"

    rewritten = _MD_IMAGE.sub(repl, markdown)
    return PromotionCollection(
        markdown=rewritten, images=tuple(order), skipped=stats["skipped"]
    )


def _images_dir(target: Path) -> Path:
    """图片落点归口（决策P5.2.1-2）：锚在 **md 目标的父目录**。

    `target` = `<base>/<slug>.md` → `target.parent` = `<base>/` → 落点恒为 `<base>/images/<slug>/`，
    与 md 内 `images/<slug>/…` 相对引用对齐。CLI convert 传 `raw/<slug>.md`、解析传
    `parsed/<slug>.md`、晋级传 `raw/<target_stem>.md`——三处同形（决策P4.6.1-2）。**绝不**写成
    `root/"images"/stem`（那会落到 `<base>/../images/<slug>/`、与引用断裂）。
    """
    return target.parent / "images" / target.stem


def _stage_and_swap_images(
    target: Path, images: tuple[ConvertedImage, ...] | tuple[PromotionImage, ...]
) -> Path | None:
    """落 images 到 `<base>/images/<slug>/`（决策P5.2.1-9）：写 staging → rename 换图目录（旧图存 `.bak`）。

    返回 `bak`（overwrite 时旧图目录的暂存路径，否则 None）供 md 提交后清理 / 失败回滚。可失败的
    字节写**全隔离在 sibling staging**（ENOSPC/权限只可能在此，real 与旧 md 未被碰）；随后两次
    `rename` 是同 fs 不分配空间的元数据操作、近乎不可失败。`OSError` 向上抛由调用方处理。

    接受 `ConvertedImage`（convert/解析）与 `PromotionImage`（晋级）两类——都只读 `.name`/`.data`。
    """
    real = _images_dir(target)  # <base>/images/<slug>/
    images_root = real.parent  # <base>/images/
    images_root.mkdir(parents=True, exist_ok=True)
    staging = images_root / f".{real.name}.staging-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    try:
        for img in images:
            (staging / img.name).write_bytes(img.data)  # 唯一可能 ENOSPC/权限失败处
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)  # real 与旧 md 原封不动
        raise
    bak: Path | None = None
    try:
        if real.exists():  # overwrite：旧图整盘存 .bak（决策P5.2.1-6）
            bak = images_root / f".{real.name}.bak-{uuid.uuid4().hex[:8]}"
            os.rename(real, bak)
        os.rename(staging, real)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        if bak is not None and not real.exists():  # staging→real 失败 → 复位旧图
            with contextlib.suppress(OSError):
                os.rename(bak, real)
        raise
    return bak


def _rollback_images(target: Path, bak: Path | None) -> None:
    """md 提交失败时回滚图目录（决策P5.2.1-9）：删本次新换上的 real；overwrite 则从 `.bak` 复位旧图。"""
    real = _images_dir(target)
    shutil.rmtree(real, ignore_errors=True)
    if bak is not None:
        with contextlib.suppress(OSError):
            os.rename(bak, real)


def commit_md_with_images(
    target: Path,
    content: str,
    images: tuple[ConvertedImage, ...] | tuple[PromotionImage, ...],
    *,
    overwrite: bool,
) -> int:
    """落「图 + md」原子提交归口（决策P5.2.1-9 落盘顺序）：图先 staging-swap → md 末步提交 → 失败回滚。

    供 `commit_promotion` / `parse_upload` / `relocalize_commit` **三处共用**（消安全关键落盘序列漂移，
    决策P4.6.1-4 altitude）：

    - **须动图目录**（`need_swap`）= `images` 非空 **或** `overwrite` 且旧图目录已存在——后者是**无图
      覆盖**场景：新 md 不再引用本地图，旧 `<base>/images/<slug>/` 必须一并清掉，否则旧图仍会被
      端点服务/列出、且 `raw/` 下还会被后续快照/ingest 收入（决策P5.2.1-6 整盘替换语义的补全）；
    - `_stage_and_swap_images` 落 `<base>/images/<slug>/`（旧图存 `.bak`，`images` 空时换上的是空目录）；
      其 `OSError` **向上抛**（此时 real/旧 md 未碰，调用方按场景映射 500 / EXIT_USAGE）；
    - 末步 `atomic_write_raw` 提交 md：`OSError` → 回滚图后**继续上抛**；返回 `EXIT_USAGE`（同名冲突）→
      回滚图、透传退出码；
    - 提交成功（`EXIT_OK`）→ 清旧图 `.bak`；**无图**时再删掉换上的空 `images/<slug>/`（不留空目录）。

    `target.parent` 缺失时先建（parsed/ 首解析、无图分支也安全）。返回 `atomic_write_raw` 退出码。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    real = _images_dir(target)
    # 须动图目录：有新图要落，或 overwrite 且旧图目录残留（须清，避免悬空旧图被服务/进快照）。
    need_swap = bool(images) or (overwrite and real.exists())
    bak: Path | None = None
    if need_swap:
        bak = _stage_and_swap_images(target, images)  # OSError 上抛（real/旧 md 未碰）
    try:
        code = atomic_write_raw(target, content, overwrite)  # 末步提交 md
    except OSError:
        if need_swap:
            _rollback_images(target, bak)
        raise
    if code != EXIT_OK:  # 同名冲突（TOCTOU 抢建）→ 回滚图、透传退出码
        if need_swap:
            _rollback_images(target, bak)
        return code
    if bak is not None:  # 提交成功 → 清旧图 .bak
        shutil.rmtree(bak, ignore_errors=True)
    if need_swap and not images:  # 无图覆盖：不留空的 images/<slug>/（仅当确为空才删）
        with contextlib.suppress(OSError):
            real.rmdir()
    return EXIT_OK
