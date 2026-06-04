"""知识库根的前置校验（P2，见 docs/P2-最小闭环.md §6）。

`require_kb_root` 是 ingest/query/check 共用的单一前置校验 helper：**只查文件存在性**，
不做自动发现、不做修复。失败抛 `GuanlanError(EXIT_USAGE)` 并提示 `guanlan init`。
"""

from __future__ import annotations

from pathlib import Path

from .errors import EXIT_USAGE, GuanlanError

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
