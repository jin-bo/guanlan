"""query 工作流（P2，见 docs/P2-最小闭环.md §8）。

`guanlan query "…"`：默认只读问答（`read-only` 姿态双重兜底，不取快照、不跑 check）。
`--backfill`：`workspace-write` + 与 ingest 完全一致的门禁，允许回填 `wiki/syntheses/`。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import EXIT_AGENT_ERROR, EXIT_OK, GuanlanError
from .gate import report_agent_error, run_guarded_write
from .paths import require_kb_root
from .runtime import AgentRunner, run_agent_task

if TYPE_CHECKING:  # 仅类型：query 的 import 面不背检索层
    from .search import CorpusCache

# 召回由**宿主先做**（修 P5.1 决策P5.1-6 的落空回退）：本提示只给 `agentao run` 子进程用（CLI query、
# `--backfill`、MCP `query` 工具；Web 对话不经这里）。这些运行里既没有 `guanlan_search` 工具，shell 也用不了：
# 只读姿态硬拒 `run_shell_command`，workspace-write 下它要确认、`--interaction-policy reject` 直接拒；而
# `agentao run` 遇到**任何一次**拒绝就终止整次运行（`agentao/cli/run.py` 的 `_on_event` → `token.cancel`），
# 模型没有机会退回扫目录。旧措辞「宿主工具 / `guanlan search` CLI」等于邀请模型去试 shell，CLI 只读 query 因此
# 频繁 `permission_denied`。现由宿主先跑一次零 LLM 的 `search_pages`，把 top-N 候选页写进提示，并明说不要调 shell。
# index.md 只当目录、不整读：大库的 index 有数十万字，整读会被单次读取上限截成首尾两段（与 skill query 第 2 步同口径）。
QUERY_PROMPT = (
    "请按 `guanlan-wiki` skill 的 query 工作流回答：{question}。"
    "**本次运行不能用 shell，也没有 `guanlan_search` 工具：不要调用 `run_shell_command`"
    "（会使本次运行直接失败）。**"
    "{candidates}"
    "`wiki/index.md` 只是目录，**不要整读**（库大时会超出单次读取上限被截断），"
    "需要浏览某类页面时用 `search_file_content` 按分区或关键词定位条目、只读命中范围；"
    "**不要重复等价检索；证据足够即回答。**"
    "候选页与定位都空手而回时再扫相关目录或请我补关键词；"
    "综合出**带 `[[页]]` 引用**的答案；无可靠来源时明说、不编造。"
    "**默认只读，不要写 `wiki/`。**"
)

# 宿主召回的候选页数：够覆盖一题的主要来源页，又不把提示撑大（每条一行：路径 + 标题）。
QUERY_PREFETCH_LIMIT = 8


def _prefetch_section(kb: Path, question: str, search_cache: CorpusCache | None = None) -> str:
    """宿主侧确定性召回（零 LLM，与 `guanlan search` 同一 `search_pages`），渲染成提示里的候选段。

    长驻宿主（MCP `query`）传入自己的 `CorpusCache`，复用已预热的语料与反链，免得每问一次全库冷算；
    CLI 无长驻 cache，走冷算 `search_pages`（两者字节等价）。
    召回失败或无命中都不阻断问答：退化成「用 `search_file_content` 定位」的指引。
    """
    from .search import search_pages  # 函数级导入：query 的 import 面不背检索层

    try:
        if search_cache is not None:
            result = search_cache.search(kb / "wiki", question, limit=QUERY_PREFETCH_LIMIT)
        else:
            result = search_pages(kb / "wiki", question, limit=QUERY_PREFETCH_LIMIT)
    except Exception:  # noqa: BLE001 —— 召回是辅助，坏页/空库不该让问答失败
        result = None
    if result is None or not result.hits:
        return "宿主按问题做的确定性召回没有命中：先用 `search_file_content` 按关键词定位候选页、只读命中范围，再综合；"
    # 标题是页面 frontmatter 的单行文本；去掉反引号与换行，免得打断提示里的列表/代码段。
    lines = "\n".join(
        f"- `{h.page}` — {' '.join(h.title.replace('`', '').split())}" for h in result.hits
    )
    return (
        f"宿主已按问题做过确定性召回（整页 BM25，CJK 2-gram，含别名；{result.matched} 页命中），"
        f"前 {len(result.hits)} 个候选页：\n{lines}\n"
        "先读这些候选页综合；不够时用 `search_file_content` 按关键词补充定位、只读命中范围。"
    )


def build_query_prompt(kb: Path, question: str, *, search_cache: CorpusCache | None = None) -> str:
    """CLI query / `--backfill` / MCP `query` 共用的子进程提示：问题 + 宿主召回的候选页。"""
    return QUERY_PROMPT.format(
        question=question, candidates=_prefetch_section(Path(kb), question, search_cache)
    )

# --backfill 时追加：允许把好答案回填 syntheses/。
_BACKFILL_SUFFIX = (
    " 若这是一个值得沉淀的好答案，把它回填到 `wiki/syntheses/<slug>.md`"
    "（`type: synthesis`、frontmatter 齐全、引用转 `[[wikilink]]`），并在 `index.md` 的"
    " Syntheses 区追加一行（用 `search_file_content` 定位该分区，不整读 index；描述一到两句、200 字以内），"
    "向 `log.md` 追加一条 `## [<日期>] backfill | <标题>`。"
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
        build_query_prompt(kb, question),
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
        build_query_prompt(kb, question) + _BACKFILL_SUFFIX,
        model=model,
        runner=runner,
    )
