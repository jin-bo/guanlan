"""ingest 工作流（P2，见 docs/P2-最小闭环.md §7）。

`guanlan ingest raw/<file>.md`：前置校验 → 取 raw 快照 → Agentao + skill 摄入 → 收尾门禁。
真正的建页步骤在 `guanlan-wiki` skill 里；本模块只做编排与确定性门禁。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .errors import EXIT_USAGE, GuanlanError
from .gate import run_guarded_write
from .paths import require_kb_root
from .runtime import AgentRunner

# 薄 prompt：真正步骤在 skill。{rel} = 相对 raw/ 的 posix 路径。
INGEST_PROMPT = (
    "请按 `guanlan-wiki` skill 的 ingest 工作流摄入资料 `raw/{rel}`："
    "该路径已由 wrapper 校验存在，必须按原样读取，不要替换其中的引号、空格或 CJK 字符；"
    "读该 `.md` 源与 `wiki/index.md`、`wiki/overview.md` 建上下文；"
    "写/更新 source·entity·concept 页（frontmatter 齐全、术语转 `[[wikilink]]`）；"
    "更新 `index.md` 与 `overview.md`；发现矛盾就地标 `## ⚠️ 矛盾与存疑`；"
    "向 `log.md` 追加一条 `## [<日期>] ingest | <标题>`。"
    "遵循 `AGENTAO.md` 硬约束与 conventions 默认。**永不修改 `raw/`。** "
    "**不要运行 shell 命令；读写文件必须使用内置文件工具。不要自行执行 `guanlan check`，wrapper 会在你返回后强制校验。** "
    "完成后用一两句说明触及了哪些页面。"
)


def _resolve_raw_target(root: Path, target: str) -> Path:
    """把 target 解析为 `<root>/raw/` 下存在的 `.md` 文件，否则抛 GuanlanError(EXIT_USAGE)。"""
    tpath = Path(target).expanduser()
    if not tpath.is_absolute():
        tpath = root / tpath
    tpath = tpath.resolve()

    # raw_dir 也 resolve：raw/ 本身可能是符号链接（指向库外存储），否则 relative_to 永远失败。
    raw_dir = (root / "raw").resolve()
    try:
        tpath.relative_to(raw_dir)
    except ValueError:
        raise GuanlanError(
            f"摄入目标必须位于 raw/ 下：{target}", exit_code=EXIT_USAGE
        ) from None
    if tpath.suffix.lower() != ".md":
        raise GuanlanError(
            f"MVP ingest 只吃 `.md`（多格式摄入属 P5）：{target}", exit_code=EXIT_USAGE
        )
    if not tpath.is_file():
        raise GuanlanError(f"文件不存在：{target}", exit_code=EXIT_USAGE)
    return tpath


def run_ingest(
    target: str,
    *,
    root: str | Path = ".",
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> int:
    """摄入一篇 `.md`，返回退出码（见 errors.py）。"""
    try:
        kb = require_kb_root(root, writable=True)
        tpath = _resolve_raw_target(kb, target)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    rel = tpath.relative_to((kb / "raw").resolve()).as_posix()
    return run_guarded_write(
        kb, INGEST_PROMPT.format(rel=rel), model=model, runner=runner
    )
