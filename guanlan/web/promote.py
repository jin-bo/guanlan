"""晋级连图一起搬的专用 promotion job 机制（P4.6.1，决策P4.6.1-12/15/16）。

P4.6 晋级（`POST /api/raw {source}`）原在**入队前**把 source 读成 `content`、worker thunk 仅
`_atomic_write_raw(target, content)`——临界区里已丢 source 路径 + 图片上下文，无从连图一起搬。
P4.6.1 改成**两段式专用作业**：

- **`prepare_promotion`（入队前，本线程/`to_thread`）**：校验 source → 扫 md 图片引用 →
  经 `workspace/` root `_admit_image_ref` 准入（相对引用**必须**准入+存在，否则 400「先 relocalize」，
  外链原样保留）→ 读字节入内存 + 按 **target stem** 归一重写引用 → provenance → 文本准入 → 记
  source md + 各图 **SHA256 指纹**（决策P4.6.1-16）。任一坏处直接转 4xx（worker 只回退出码、无 400 路径）。
- **`commit_promotion`（写锁内，JobQueue worker thunk）**：⓿ **指纹复检**（复读复算 SHA256，与
  入队前不符 → fail-closed 409「source 已变」）→ ① 图 staging-swap 到 `raw/images/<target_stem>/`
  → ② md 末步 `atomic_write_raw` 提交 → ③ 任一步失败回滚（沿 P5.2.1-9 落盘顺序）。**图搬 + md
  提交在同一写临界区**，绝不把搬图放队列外。

准入容量边界 root 取 **`workspace/`**（决策P4.6.1-8 的安全超集）：parsed 源的本地图在
`workspace/parsed/images/<slug>/`，uploads 退化源（§6）的本地图亦在 workspace 内——两者都被
`relative_to(workspace)` 含住、挡越界/symlink 逃逸，比单钉 `parsed/` 更稳且天然覆盖 uploads 退化路径。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from ..errors import EXIT_USAGE
from ..imageio import (
    ConvertError,
    PromotionImage,
    collect_for_promotion,
    commit_md_with_images,
)
from .rawfeed import _apply_origin, _check_text_admission, _safe_promotion_source


@dataclass(frozen=True)
class PromotionPlan:
    """入队前算好的晋级方案，整体作 thunk 闭包带进写临界区（决策P4.6.1-12）。"""

    target: Path  # raw/<target_stem>.md 的落点
    content: str  # 已 provenance + 已按 target_stem 归一重写引用的最终 md 文本
    images: tuple[PromotionImage, ...]  # 引用驱动收集的图（含 realpath + 入队前 SHA256）
    source_md: Path  # 源 .md realpath（写锁内复读复算指纹用）
    source_md_sha256: str  # 入队前源 md 字节 SHA256（写锁内复检，决策P4.6.1-16）
    skipped: int = 0  # 原样保留的外链引用数（供回执提示）


def prepare_promotion(root: Path, source: str, target: Path, origin: str | None) -> PromotionPlan:
    """入队前准备晋级方案（决策P4.6.1-12/15/16）。坏处一律 `HTTPException`（4xx，不进队列）。

    在端点的 `to_thread` 里跑（含读盘，阻塞）。`target` 已由 `_safe_raw_target` 解析（其 stem =
    target_stem，决策P4.6.1-13，可与 source stem 不同 = 改名晋级）。
    """
    path = _safe_promotion_source(root, source)  # 越界 400 / 非 md 400 / 缺失 404
    try:
        raw_bytes = path.read_bytes()
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400, detail="source 不是 UTF-8 文本，无法晋级为 raw/ 源。"
        ) from None
    workspace_root = (root / "workspace").resolve()  # 准入容量边界（决策P4.6.1-8 安全超集）
    # 引用驱动收集（决策P4.6.1-15）：相对引用必须准入+存在，否则 ValueError → 400；外链原样保留。
    # 归一 keyed on target stem（决策P4.6.1-13）：图改名 <target_stem>-N.ext、引用重写 images/<target_stem>/…。
    try:
        collected = collect_for_promotion(
            text, source_md=path, root=workspace_root, stem=target.stem
        )
    except ValueError as exc:  # 相对引用未准入/悬空（断链）
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ConvertError as exc:  # 容量闸超限 → 400，不静默丢图（决策P4.6.1-8）；不兜其它异常以免掩盖 bug
        raise HTTPException(status_code=400, detail=f"图片处理失败：{exc}") from None
    # provenance（决策P4.6-10）：origin strip；省略/空白 → 回退 source 路径（绝不写空 provenance）。
    origin_value = (origin or "").strip() or source
    content = _apply_origin(collected.markdown, origin_value)  # 坏 frontmatter → 400
    _check_text_admission(content)  # 空 / 超 MAX_RAW_BYTES / 控制字符 → 400
    return PromotionPlan(
        target=target,
        content=content,
        images=collected.images,
        source_md=path,
        source_md_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        skipped=collected.skipped,
    )


def _fingerprint_changed(plan: PromotionPlan) -> bool:
    """写锁内复读复算 SHA256，与入队前快照比对（决策P4.6.1-16）。任一变化/读失败 → True（fail-closed）。

    SHA256 是**唯一权威判据**：mtime+size 会漏「保时间戳原位等长替换」，安全边界须用内容哈希；
    源 md + 各收集图都复算（图字节也绑入一致性，比 P4.6 文本晋级只看 md 更严）。
    """
    try:
        if hashlib.sha256(plan.source_md.read_bytes()).hexdigest() != plan.source_md_sha256:
            return True
        for img in plan.images:
            if hashlib.sha256(img.source.read_bytes()).hexdigest() != img.sha256:
                return True
    except OSError:  # 复读时文件已被删/改形 → 视作已变（不提交旧快照）
        return True
    return False


def commit_promotion(plan: PromotionPlan, overwrite: bool) -> int:
    """写锁内提交晋级（JobQueue worker thunk，决策P4.6.1-12/16）。返回退出码（worker 据其分流 409/500）。

    ⓿ 指纹复检不符 → EXIT_USAGE（端点转 409「source 已变，请重新审阅」，不提交旧快照）；
    ① 图 staging-swap 到 raw/images/<target_stem>/ →② md 末步 atomic_write_raw 提交 →③ 失败回滚。
    `atomic_write_raw` 返回 EXIT_USAGE（同名冲突）时回滚图、透传退出码（端点转 409）；落盘
    `OSError` 回滚图后**继续上抛**（worker 归一 EXIT_AGENT_ERROR，端点转 500）。
    """
    if _fingerprint_changed(plan):
        print("source 已变，请重新审阅后再晋级。")  # 经 worker redirect → job.output（409 detail）
        return EXIT_USAGE
    # 图 + md 原子提交（共用归口，决策P4.6.1-4）：staging-swap → md 末步提交 → 失败回滚。落盘 OSError
    # 上抛 → worker EXIT_AGENT_ERROR → 500；EXIT_USAGE（同名冲突）透传 → 端点 409。
    return commit_md_with_images(plan.target, plan.content, plan.images, overwrite=overwrite)
