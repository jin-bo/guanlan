"""投喂写 `raw/` 的 web 薄壳（P4.1；核心已沉到 `guanlan/rawio.py`）。

P5.2 把无状态的 slug / 文本准入 / provenance / 原子写原语抽到 transport-neutral 的
`guanlan/rawio.py`（被 CLI `convert` 与本薄壳共用，消 web↔cli 漂移，决策P5.2-6）。本模块
**保留旧的下划线私名**（`_raw_slug` / `_safe_raw_target` / `_atomic_write_raw` …）供 `app.py`
/ `uploads.py` 原样 import，并把**校验函数**的 `ValueError` 包成 `HTTPException(400)`——
字节行为与 P4.6 完全一致（同 detail 文案、同状态码）。`_atomic_write_raw` 是**返回退出码**的
原子写，**原样透传** rawio 实现（worker 据 exit_code 分流 409/500，绝不能改 raise）。

P4.6 / P4.6.1 的晋级（`source` → raw/）随 P4.30 一键入库下线（决策P4.30-2），其专用 helper 一并删除。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from ..rawio import (
    MAX_RAW_BYTES,
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
    "_atomic_write_raw",
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
