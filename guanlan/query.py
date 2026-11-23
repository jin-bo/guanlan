"""query 工作流（P2，见 docs/P2-最小闭环.md §8）。

`guanlan query "…"`：默认只读问答（`read-only` 姿态双重兜底，不取快照、不跑 check）。
`--backfill`：`workspace-write` + 与 ingest 完全一致的门禁，允许回填 `wiki/syntheses/`。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import EXIT_AGENT_ERROR, EXIT_OK, EXIT_USAGE, GuanlanError
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

# ───────── P5.5 提问者画像（`guanlan query --profile`，见 docs/P5.5-CLI问答画像.md）─────────

# 上限参照 Hermes USER.md 的 1,375 字符，按中文略放宽（决策D4）。
PROFILE_MAX_CHARS = 1500
# 画像进系统提示的「Project Instructions」段（决策D3），用途句把它限定在写法上、并让位于项目规约。
PROFILE_HEADING = "## 提问者画像"
PROFILE_USAGE_NOTE = (
    "画像仅用于调整切入角度、详略和术语；事实判断和引用以库内资料为准；"
    "如与项目规约或引用要求冲突，以项目规约和引用要求为准。"
)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def load_profile(path: str | Path, kb: Path) -> str:
    """读取并校验画像文件（决策D4），返回去首尾空白的全文；不合格抛 `GuanlanError`（EXIT_USAGE）。

    全部检查在调模型之前做；不截断、不回退成「不带画像」——路径写错时用户不该以为带上了。
    """
    p = Path(path).expanduser()
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise GuanlanError(f"画像文件不存在：{path}") from None
    if not real.is_file():
        raise GuanlanError(f"画像路径不是普通文件：{path}")
    # 按解析符号链接后的真实路径判：只读 query 的 Agent 能读库内文件，画像会被当成资料读到。
    # 逐级 samefile 比对祖先目录，而非字符串前缀：macOS/Windows 大小写不敏感的盘上，大小写写法
    # 不同的路径（`resolve` 不规范化大小写）照样指向库内。
    kb_real = Path(kb).resolve()
    if real.is_relative_to(kb_real) or any(_same_dir(parent, kb_real) for parent in real.parents):
        raise GuanlanError(f"画像文件不能放在知识库目录内（解析后为 {real}）：请移到库外再指定。")
    try:
        # 先按字节粗拦：UTF-8 一字最多 4 字节，远超上限的大文件不必整读进内存。
        if real.stat().st_size > PROFILE_MAX_CHARS * 4 + 4096:
            raise GuanlanError(f"画像文件过大，超过上限 {PROFILE_MAX_CHARS} 字；不截断，请精简后再试：{path}")
        # utf-8-sig：记事本类编辑器存的 BOM 不进系统提示、也不计入字数。
        text = real.read_text(encoding="utf-8-sig").strip()
    except UnicodeDecodeError:
        raise GuanlanError(f"画像文件不是 UTF-8 文本：{path}") from None
    except OSError as exc:
        raise GuanlanError(f"读不了画像文件：{path}（{exc}）") from None
    if not text:
        raise GuanlanError(f"画像文件是空的：{path}")
    if len(text) > PROFILE_MAX_CHARS:
        raise GuanlanError(
            f"画像 {len(text)} 字，超过上限 {PROFILE_MAX_CHARS} 字；不截断，请精简后再试：{path}"
        )
    return text


# 画像里的 ATX 标题（`#`–`######` + 空白或行尾）；缩进 ≤3 个空格，与 CommonMark 一致。
_ATX_HEADING_RE = re.compile(r"^( {0,3})(#{1,6})(?=[ \t]|$)")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# 画像段自己的标题是第 2 级，画像里最浅的标题落到第 3 级，层次相对不变。
PROFILE_TOP_LEVEL = 3


def shift_profile_headings(text: str) -> str:
    """把画像里最浅的 ATX 标题调到第 3 级，其余标题保持相对层次（最深到第 6 级为止）。

    例：最浅是 `#` 时，`# 姓名` → `### 姓名`、`## 背景` → `#### 背景`。这样画像整体挂在
    `## 提问者画像` 之下，不会与项目指令的标题平级。围栏代码块里的 `#` 不是标题，不动；
    没有标题时原样返回。Setext 标题（下划线式）不处理。
    """
    lines = text.split("\n")
    fence: str | None = None
    found: list[tuple[int, re.Match]] = []
    for i, line in enumerate(lines):
        m = _FENCE_RE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is None:
            h = _ATX_HEADING_RE.match(line)
            if h:
                found.append((i, h))
    if not found:
        return text
    delta = PROFILE_TOP_LEVEL - min(len(h.group(2)) for _, h in found)
    for i, h in found:
        level = min(6, len(h.group(2)) + delta)
        lines[i] = h.group(1) + "#" * level + lines[i][h.end():]
    return "\n".join(lines)


def build_profile_instructions(kb: Path, profile: str) -> str:
    """run spec `instructions` 的内容：项目指令 + 画像段（决策D3）。

    `instructions` 会**顶替** agentao 读 `AGENTAO.md`，所以项目指令由宿主用 agentao 自己的
    `load_project_instructions` 读（同一套 `AGENTAO.md` → `AGENTS.md` 回退与去 frontmatter），
    与它不带画像时读到的逐字一致；两者都没有时只剩画像段。画像标题先经 `shift_profile_headings`
    挂到画像段之下。
    """
    from agentao.prompts import load_project_instructions

    section = f"{PROFILE_HEADING}\n\n{PROFILE_USAGE_NOTE}\n\n{shift_profile_headings(profile)}"
    project = load_project_instructions(Path(kb))
    return f"{project.rstrip()}\n\n{section}" if project and project.strip() else section


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
    profile: str | Path | None = None,
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> int:
    """对知识库提问，返回退出码（见 errors.py）。`profile`：库外画像文件路径（P5.5，仅只读）。"""
    if backfill and profile is not None:
        # CLI 已由 argparse 互斥组在解析时拦下（早于取锁）；这里只给直接调用者兜底。
        print("--profile 不能与 --backfill 同用：回填内容进共享的 wiki/syntheses/。", file=sys.stderr)
        return EXIT_USAGE
    try:
        kb = require_kb_root(root, writable=backfill)
        instructions = (
            build_profile_instructions(kb, load_profile(profile, kb)) if profile is not None else None
        )
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    if not backfill:
        return _run_readonly(question, kb, model, runner, instructions)
    return _run_backfill(question, kb, model, runner)


def _run_readonly(
    question: str,
    kb: Path,
    model: str | None,
    runner: AgentRunner | None,
    instructions: str | None = None,
) -> int:
    """默认只读路径：read-only 姿态拦写入，不取 raw 快照、不跑 check。

    画像只改系统提示（`instructions`），用户消息与宿主召回与不带画像时逐字相同（决策D2/D3）。
    """
    run_result = run_agent_task(
        build_query_prompt(kb, question),
        working_directory=kb,
        permission_mode="read-only",
        model=model,
        instructions=instructions,
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
