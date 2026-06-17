"""知识库根的前置校验（P2，见 docs/P2-最小闭环.md §6）。

`require_kb_root` 是 ingest/query/check 共用的单一前置校验 helper：**只查文件存在性**，
不做自动发现、不做修复。失败抛 `GuanlanError(EXIT_USAGE)` 并提示 `guanlan init`。
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import EXIT_USAGE, GuanlanError


def count_files_modified_since(directory: Path, since: float) -> int:
    """统计 `directory` 下 mtime ≥ `since`（墙钟秒）的文件数（新建 + 改写都算）。

    供「自某时刻起改了几个文件」的进度提示复用（CLI 子进程心跳 / Web 入库作业心跳，均为
    A+ 心跳方案）。**best-effort**：任何 OSError（遍历中文件被删 / 权限变 / 目录不存在）一律
    吞掉、不中断——它只是进展提示，绝不该让计数本身抛错拖垮调用方（心跳线程）。
    """
    n = 0
    try:
        for root, _dirs, files in os.walk(directory):
            for name in files:
                try:
                    if os.stat(os.path.join(root, name)).st_mtime >= since:
                        n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n

# 写入口（ingest、query --backfill）要求齐全；只读路径（query、check）仅要求 wiki/。
_WRITABLE_REQUIRED: tuple[tuple[str, bool], ...] = (
    ("AGENTAO.md", False),
    ("SCHEMA.md", False),
    ("raw", True),
    ("wiki", True),
)
_READONLY_REQUIRED: tuple[tuple[str, bool], ...] = (("wiki", True),)


def require_kb_root(root: str | Path, *, writable: bool) -> Path:
    """校验 `root` 是有效观澜知识库，返回 resolve 后的绝对路径。

    Args:
        root: 知识库根目录。
        writable: 写入口（ingest / query --backfill）传 True，要求 raw/、wiki/、
            AGENTAO.md、SCHEMA.md 齐全；只读路径（query / check）传 False，仅要求 wiki/。

    Raises:
        GuanlanError: 任一必需项缺失，`exit_code == EXIT_USAGE`。
    """
    root = Path(root).expanduser().resolve()
    required = _WRITABLE_REQUIRED if writable else _READONLY_REQUIRED

    missing = [
        name
        for name, is_dir in required
        if not ((root / name).is_dir() if is_dir else (root / name).is_file())
    ]
    if missing:
        raise GuanlanError(
            f"{root} 不是有效的观澜知识库（缺：{', '.join(missing)}）。"
            "先运行 `guanlan init` 生成模板。",
            exit_code=EXIT_USAGE,
        )
    return root
