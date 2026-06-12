"""投喂 / 晋级写 `raw/` 的 web 薄壳（P4.1 / P4.6；核心已沉到 `guanlan/rawio.py`）。

P5.2 把无状态的 slug / 文本准入 / provenance / 原子写原语抽到 transport-neutral 的
`guanlan/rawio.py`（被 CLI `convert` 与本薄壳共用，消 web↔cli 漂移，决策P5.2-6）。本模块
**保留旧的下划线私名**（`_raw_slug` / `_safe_raw_target` / `_atomic_write_raw` …）供 `app.py`
/ `uploads.py` 原样 import，并把**校验函数**的 `ValueError` 包成 `HTTPException(400)`——
字节行为与 P4.6 完全一致（同 detail 文案、同状态码）。`_atomic_write_raw` 是**返回退出码**的
原子写，**原样透传** rawio 实现（worker 据 exit_code 分流 409/500，绝不能改 raise）。

仍 web-coupled、未下沉的两个 helper（`_safe_promotion_source` / `_prepare_promotion`）留在
本模块：它们引用 `workspace/`、直接抛 `HTTPException`（含 404），与 web 端点强耦合。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from ..rawio import (
    MAX_RAW_BYTES,
    apply_origin,
    atomic_write_raw as _atomic_write_raw,  # 返回退出码、原样透传（worker 据 exit_code 分流 409/500）
    check_text_admission,
    normalize_basename as _normalize_basename,
    raw_slug as _raw_slug,
    safe_raw_target,
)

__all__ = [
    "MAX_RAW_BYTES",
    "_raw_slug",
    "_normalize_basename",
    "_safe_raw_target",
    "_check_text_admission",
    "_apply_origin",
    "_atomic_write_raw",
    "_safe_promotion_source",
    "_prepare_promotion",
]


def _safe_raw_target(root: Path, name: str) -> Path:
    """`rawio.safe_raw_target` 的 web 薄壳：校验 `ValueError` → `HTTPException(400)`。"""
    try:
        return safe_raw_target(root, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _check_text_admission(content: str) -> None:
    """`rawio.check_text_admission` 的 web 薄壳：`ValueError` → `HTTPException(400)`。"""
    try:
        check_text_admission(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _apply_origin(text: str, origin: str) -> str:
    """`rawio.apply_origin` 的 web 薄壳：坏 frontmatter 的 `ValueError` → `HTTPException(400)`。"""
    try:
        return apply_origin(text, origin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _safe_promotion_source(root: Path, rel: str) -> Path:
    """把晋级 `source` 解析为 `workspace/` 内一个**存在的 `.md`**；否则 400/404（决策P4.6-4）。

    校验顺序：① 路径包含——resolve + `relative_to(workspace)`（越界 400）；② 必须 `.md`
    （`raw/` 是 `.md` 单格式源，非 `.md` 400、提示先解析）；③ 必须存在（404）。`uploads/*.md`
    与 `parsed/*.md` 都在 `workspace/` 内、皆合格（印证 §6 退化路径：上传 `.md` 可直接晋级）。
    """
    workspace = (root / "workspace").resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"source 路径越界（须在 workspace/ 内）：{rel}"
        ) from None
    if candidate.suffix.lower() != ".md":
        raise HTTPException(
            status_code=400, detail="source 必须是 .md；请先在可写会话里解析成 .md 再晋级。"
        )
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"source 不存在：{rel}")
    return candidate


def _prepare_promotion(root: Path, source: str, origin: str | None) -> str:
    """读 `source`、过文本准入、按 provenance 归一 frontmatter，返回待写 `raw/` 的最终内容。

    在端点的 `to_thread` 里跑（含读盘，阻塞）——读发生在原子写**之前**而非单写者作业内：
    这样非 UTF-8 / 超限 / 坏 frontmatter 能直接转 `400`（作业只回退出码、映射 409/500，无 400），
    与投喂 `content` 分支「端点校验、只把写入队」同构。层③ 423 已挡可写 turn 活跃期的并发改写，
    `source` 读后随即原子写、TOCTOU 窗口极小（决策P4.6-4）。
    """
    path = _safe_promotion_source(root, source)
    try:
        content = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400, detail="source 不是 UTF-8 文本，无法晋级为 raw/ 源。"
        ) from None
    _check_text_admission(content)  # 与投喂同闸：空 / 超 MAX_RAW_BYTES / 控制字符 → 400
    # provenance（决策P4.6-10）：origin 先 strip；省略 / 空白 → 回退 source 路径（绝不写空 provenance）。
    origin_value = (origin or "").strip() or source
    return _apply_origin(content, origin_value)
