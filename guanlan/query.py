"""query 工作流（P2，见 docs/P2-最小闭环.md §8）。

`guanlan query "…"`：默认只读问答（`read-only` 姿态双重兜底，不取快照、不跑 check）。
`--backfill`：`workspace-write` + 与 ingest 完全一致的门禁，允许回填 `wiki/syntheses/`。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .errors import EXIT_AGENT_ERROR, EXIT_OK, GuanlanError
from .gate import report_agent_error, run_guarded_write
from .paths import require_kb_root
from .runtime import AgentRunner, run_agent_task

# 召回措辞**传输中立**（P5.1 决策P5.1-6）：不硬编码「先用 `guanlan search` CLI」——只读 CLI query
# 与只读 Web 会话都没 shell，那是死指令；改成「用可用的 search 入口」，宿主 `guanlan_search` 工具
# （Web 只读会话）/ `guanlan search` CLI（有 shell 时）/ 扫目录（都不可用）按可达性回退。
QUERY_PROMPT = (
    "请按 `guanlan-wiki` skill 的 query 工作流回答：{question}。"
    "先用**可用的 search 入口**（宿主 `guanlan_search` 工具 / `guanlan search \"<关键词>\"` CLI）召回候选页"
    "（确定性整页 BM25 召回，CJK 走 2-gram、别名已纳入匹配面），读这些候选页 + `wiki/index.md` 综合；"
    "都不可用或空手而回时再扫相关目录或请我补关键词；"
    "综合出**带 `[[页]]` 引用**的答案；无可靠来源时明说、不编造。"
    "**默认只读，不要写 `wiki/`。**"
)

# --backfill 时追加：允许把好答案回填 syntheses/。
_BACKFILL_SUFFIX = (
    " 若这是一个值得沉淀的好答案，把它回填到 `wiki/syntheses/<slug>.md`"
    "（`type: synthesis`、frontmatter 齐全、引用转 `[[wikilink]]`），并在 `index.md` 的"
    " Syntheses 区追加一行、向 `log.md` 追加一条 `## [<日期>] backfill | <标题>`。"
    "**永不修改 `raw/`。**"
)


def run_query(
    question: str,
    *,
    root: str | Path = ".",
    backfill: bool = False,
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> int:
    """对知识库提问，返回退出码（见 errors.py）。"""
    try:
        kb = require_kb_root(root, writable=backfill)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    if not backfill:
        return _run_readonly(question, kb, model, runner)
    return _run_backfill(question, kb, model, runner)


def _run_readonly(
    question: str, kb: Path, model: str | None, runner: AgentRunner | None
) -> int:
    """默认只读路径：read-only 姿态拦写入，不取 raw 快照、不跑 check。"""
    run_result = run_agent_task(
        QUERY_PROMPT.format(question=question),
        working_directory=kb,
        permission_mode="read-only",
        model=model,
        runner=runner,
    )
    if not run_result.ok:
        report_agent_error(run_result)
        return EXIT_AGENT_ERROR
    print(run_result.final_text)
    return EXIT_OK


def _run_backfill(
    question: str, kb: Path, model: str | None, runner: AgentRunner | None
) -> int:
    """--backfill：workspace-write + 与 ingest 完全一致的完整门禁。"""
    return run_guarded_write(
        kb,
        QUERY_PROMPT.format(question=question) + _BACKFILL_SUFFIX,
        model=model,
        runner=runner,
    )
