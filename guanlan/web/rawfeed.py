"""投喂 / 晋级写 `raw/` 的 web 薄壳（P4.1 / P4.6；核心已沉到 `guanlan/rawio.py`）。

P5.2 把无状态的 slug / 文本准入 / provenance / 原子写原语抽到 transport-neutral 的
`guanlan/rawio.py`（被 CLI `convert` 与本薄壳共用，消 web↔cli 漂移，决策P5.2-6）。本模块
**保留旧的下划线私名**（`_raw_slug` / `_safe_raw_target` / `_atomic_write_raw` …）供 `app.py`
/ `uploads.py` 原样 import，并把**校验函数**的 `ValueError` 包成 `HTTPException(400)`——
字节行为与 P4.6 完全一致（同 detail 文案、同状态码）。`_atomic_write_raw` 是**返回退出码**的
原子写，**原样透传** rawio 实现（worker 据 exit_code 分流 409/500，绝不能改 raise）。

仍 web-coupled、未下沉的 helper（`_safe_promotion_source` 等）留在本模块：它们引用 `workspace/`、
直接抛 `HTTPException`（含 404），与 web 端点强耦合。P4.6.1 把「读 source + 收图 + 重写 +
provenance + 指纹」的晋级准备整体上移到 `web/promote.py`（连图一起搬，决策P4.6.1-12），旧的
`_prepare_promotion`（只读文本、不搬图）已退役；`promote.prepare_promotion` 复用本模块的
`_safe_promotion_source` / `_apply_origin` / `_check_text_admission`。
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
