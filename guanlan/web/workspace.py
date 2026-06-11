"""workspace scratch（`uploads/` + `parsed/`）浏览/预览/删除的确定性 helper（P4.6，从 `app.py` 抽出）。

只白名单两个 scratch 子目录（`_SCRATCH_SUBDIRS`），一级一级浏览（不展平）、按子目录列举、
预览 `.md`、删文件/子目录——绝不删整树、绝不碰 `raw/`/`wiki/`，也不碰将来可能出现的
状态/会话目录。被 `app.py` 的 `GET/DELETE /api/workspace*` 路由调用；分类复用 `uploads`。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import HTTPException

from ..errors import EXIT_OK
from .uploads import _classify_by_ext

# workspace scratch 两子目录（决策P4.6-5/11）：列举（_list_workspace）与删除白名单
# （_safe_workspace_scratch）的单一来源——不删整树、不碰将来可能出现的状态/会话目录。
_SCRATCH_SUBDIRS = ("uploads", "parsed")


def _dir_items(root: Path, d: Path) -> list[dict]:
    """列一个目录的**直接子项**（不递归）：子目录在前（按名）、文件在后（按 mtime 倒序）。

    每项 `{path, name, is_dir, mtime}`，文件另带 `{kind, bytes}`。`path` 相对知识库根，**直接作**
    `?path=` 浏览（目录）/ 预览·删除（文件）/ `POST /api/raw {source}`（文件）的实参；`name` 为
    basename。点文件 / 点目录（`.DS_Store`/`.git`）跳过。`kind` 按扩展名判（`_classify_by_ext`，不读盘）。
    """
    dirs: list[dict] = []
    files: list[dict] = []
    for path in d.iterdir():
        if path.name.startswith("."):  # 跳过 .DS_Store / .git 等点文件/点目录
            continue
        try:
            st = path.stat()
        except OSError:
            continue  # 列举与 stat 间被并发删/改（TOCTOU）→ 跳过该项
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            dirs.append({"path": rel, "name": path.name, "is_dir": True, "mtime": st.st_mtime})
        elif path.is_file():
            files.append(
                {
                    "path": rel, "name": path.name, "is_dir": False,
                    "kind": _classify_by_ext(path), "bytes": st.st_size, "mtime": st.st_mtime,
                }
            )
    dirs.sort(key=lambda it: it["name"])
    files.sort(key=lambda it: it["mtime"], reverse=True)
    return dirs + files


def _safe_workspace_dir(root: Path, rel: str, *, allow_base_root: bool) -> tuple[Path, str]:
    """把 `rel` 解析为 `workspace/uploads|parsed` 内的一个**目录**，返回 `(目录, 所属根名)`。

    白名单两 scratch 根的自身或其后代目录；越界/其它子目录 → 400；非目录/缺失 → 404。
    `allow_base_root=False` 时连根目录本身（`workspace/uploads`、`workspace/parsed`）也拒（400）——
    供删除用（不许整删 scratch 根，决策P4.6-11 精神）；列举时 `allow_base_root=True`。
    """
    candidate = (root / rel).resolve()
    for subdir in _SCRATCH_SUBDIRS:
        base = (root / "workspace" / subdir).resolve()
        try:
            candidate.relative_to(base)  # base.relative_to(base) 成功 → 根目录也走这条
        except ValueError:
            continue
        if candidate == base and not allow_base_root:
            raise HTTPException(status_code=400, detail=f"不可整删 workspace/{subdir}/ 根目录。")
        if not candidate.is_dir():
            raise HTTPException(status_code=404, detail=f"目录不存在：{rel}")
        return candidate, subdir
    raise HTTPException(
        status_code=400, detail=f"只能浏览/删除 workspace/uploads/ 或 workspace/parsed/ 内目录：{rel}"
    )


def _list_workspace(root: Path, path: str | None = None) -> dict:
    """浏览 workspace scratch（**一级一级**，不展平，决策P4.6-5/12）。

    - `path` 省略/空 → **根视图**：`{root: True, uploads: [...], parsed: [...]}`，各为 uploads/ 与
      parsed/ 的直接子项（子目录 + 文件）。
    - `path` 给定 → **目录视图**：`{root: False, path, base, items}`，为该目录直接子项；`path` 须落在
      uploads/ 或 parsed/ 内（含其后代目录），否则 400 / 404。
    """
    if not path:
        out: dict = {"root": True}
        for subdir in _SCRATCH_SUBDIRS:
            d = root / "workspace" / subdir
            out[subdir] = _dir_items(root, d) if d.is_dir() else []
        return out
    d, base = _safe_workspace_dir(root, path, allow_base_root=True)
    return {"root": False, "path": d.relative_to(root).as_posix(), "base": base, "items": _dir_items(root, d)}


def _rmtree_workspace_dir(target: Path) -> int:
    """在串行 worker turn 内递归删一个 workspace scratch 子目录（决策P4.6-11）。幂等。

    已被并发删除（TOCTOU）→ 当作达成目标（不报错）；其它 IO 异常向上抛 → worker 归一 500。
    """
    try:
        shutil.rmtree(target)
    except FileNotFoundError:
        pass
    return EXIT_OK


def _safe_workspace_md(root: Path, rel: str) -> Path:
    """把预览 `path` 解析为 `workspace/uploads|parsed` 内存在的 `.md`；越界 409、非 md/缺失 404。

    与浏览/删除同一 `_SCRATCH_SUBDIRS` 白名单（决策P4.6-5/11）——**不**放宽到整个 `workspace/`，
    免将来 workspace/ 下出现的状态/会话目录里的 markdown 被预览泄漏。越界沿用 `_safe_wiki_file`
    的 409；非 md/缺失 404（预览复用 `render_page`，只对 markdown 有意义）。
    """
    candidate = (root / rel).resolve()
    for subdir in _SCRATCH_SUBDIRS:
        base = (root / "workspace" / subdir).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        if candidate.suffix.lower() != ".md" or not candidate.is_file():
            raise HTTPException(status_code=404, detail=f"workspace 文件不存在或非 .md：{rel}")
        return candidate
    raise HTTPException(
        status_code=409, detail=f"路径越界（须在 workspace/uploads|parsed 内）：{rel}"
    )


def _safe_workspace_scratch(root: Path, rel: str) -> Path:
    """把删除 `path` 解析为 `workspace/uploads/` 或 `workspace/parsed/` 内的文件（决策P4.6-11）。

    **白名单两子目录**：`relative_to(uploads)` **或** `relative_to(parsed)` 命中其一才放行——
    `workspace/` 根、其它子目录（未来的状态/会话目录）、越界 → 400；缺失 → 404。绝不删整树、
    绝不碰 `raw/`/`wiki/`。目录本身（非文件）经 `is_file()` 落 404，等效不删整树。
    """
    candidate = (root / rel).resolve()
    for subdir in _SCRATCH_SUBDIRS:
        base = (root / "workspace" / subdir).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在：{rel}")
        return candidate
    raise HTTPException(
        status_code=400,
        detail=f"只能删 workspace/uploads/ 或 workspace/parsed/ 内文件：{rel}",
    )


def _delete_workspace_scratch(target: Path) -> int:
    """在串行 worker turn 内删一个 workspace scratch 文件（决策P4.6-11）。幂等。

    已被并发删除（TOCTOU）→ 当作达成目标（不报错）；其它 IO 异常向上抛，由 worker 归一为
    EXIT_AGENT_ERROR（端点转 500）。
    """
    try:
        target.unlink()
    except FileNotFoundError:
        pass  # 已不在 = 目标达成（幂等）
    return EXIT_OK
