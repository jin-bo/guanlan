"""确定性门禁（P2，见 docs/P2-最小闭环.md §5.1 / §5.3）。**零 LLM。**

两部分：

1. **`raw/` 快照** —— 调用 Agentao **前**取 before、**后**取 after，任意增/删/改/重命名都判违规
   （按相对 posix 路径建键的内容 SHA256，决策4——不受 mtime 漂移/伪造影响）。
2. **组合门禁** —— `enforce` 先判 `raw/` 完整性（更严重），再跑 `guanlan check`。

写入口（ingest、query --backfill）的收尾统一走 `enforce_write_result`：agent 失败仍兜底
`raw/` 完整性（agent 可能先改 raw 再失败），不漏判硬约束。
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from .check import Violation, run_check
from .errors import (
    EXIT_AGENT_ERROR,
    EXIT_CHECK_FAILED,
    EXIT_OK,
    EXIT_RAW_MUTATED,
)
from .runtime import AgentRunner, AgentRunResult, run_agent_task

# 写入口门禁失败后的有界自愈轮数（决策7，见 docs/P2-最小闭环.md §10）。
# 仅对**新引入的阻断性**违规（frontmatter/sources）自愈——把违规回喂同一 agent 就地修；
# `raw_mutated`（完整性破坏）与 `agent_error`（运行时失败）不自愈，直接判死。
MAX_REPAIR_ATTEMPTS = 2

# **断链 = 警告，不阻断写入（决策8，见 docs/P2-最小闭环.md §10）。**
# Karpathy wiki 模式建库期，正文常先出现 `[[X]]` 而 X 的页面尚未建——这些前向引用会随后续
# 资料的加入**自我消除**。故 `wikilink.broken` 在写门禁里只作警告：既不阻断 ingest、也不喂自愈
# （否则会逼 agent 提前建桩页或删链接，反而破坏自然增长）。其余 kind 仍阻断。
# 注：独立 `guanlan check` 仍把断链当违规全量报告（按需审计用），与写门禁的执行策略分工不同。
_WARNING_KINDS = frozenset({"wikilink.broken"})

# 自愈轮的薄 prompt：只修门禁列出的**阻断性**违规（frontmatter/sources），不碰断链、不新增内容、不动 raw/。
REPAIR_PROMPT = (
    "上一轮写入后，确定性门禁发现以下**必须修复**的问题，请**只修复这些**，"
    "不要新增内容、不要改动 `raw/`、不要处理断链（断链会随后续资料自然消除，无需现在补页）：\n{violations}\n"
    "逐条修正：frontmatter 必须是合法 YAML（字符串值一律用**单引号**包裹，"
    "值内出现单引号时翻倍为 `''`，**切勿在双引号里再套双引号**）、"
    "必备键 `title/type/tags/sources/last_updated` 齐全且类型正确；"
    "`sources` 列的每个 slug 必须对应存在的 `wiki/sources/<slug>.md`；"
    "若有 `aliases`，须为非空字符串列表、全局唯一、且不与任何页面名（stem）同名。"
    "**不要运行 shell 命令或 `guanlan check`；读写文件只用内置文件工具。** "
    "修完用一句话说明改了什么。"
)


def _render_violations(violations: list[Violation]) -> str:
    """把违规渲染成回喂 agent 的逐行清单（与 report_outcome 同格式）。"""
    return "\n".join(f"- [{v.kind}] {v.page}: {v.detail}" for v in violations)


def _vkey(v: Violation) -> tuple[str, str, str]:
    """违规的稳定身份键，用于增量门禁的基线 diff（区分'已存在'与'本次新引入'）。"""
    return (v.page, v.kind, v.detail)


def partition_violations(
    violations: list[Violation],
) -> tuple[list[Violation], list[Violation]]:
    """按严重度切分：`(blocking, warnings)`。`_WARNING_KINDS`（断链）归警告，其余阻断。"""
    blocking = [v for v in violations if v.kind not in _WARNING_KINDS]
    warnings = [v for v in violations if v.kind in _WARNING_KINDS]
    return blocking, warnings


def check_baseline(root: Path) -> frozenset[tuple[str, str, str]]:
    """写操作**前**的阻断性违规基线（身份键集合）。增量门禁只追究不在此集合里的新违规。"""
    result = run_check(Path(root) / "wiki")
    blocking, _ = partition_violations(result.violations)
    return frozenset(_vkey(v) for v in blocking)


@dataclass(frozen=True)
class RawChange:
    """一处 `raw/` 改动。`kind` ∈ {added, removed, modified}；重命名 = 一删一增。"""

    kind: str
    path: str  # 相对 raw/ 的 posix 路径


def snapshot_raw(root: Path) -> dict[str, str]:
    """递归遍历 `root/raw/`，返回 {相对posix路径: 指纹}。

    - 符号链接 → **不跟随**，按 lstat/链接目标建指纹（最先判定）。否则 `is_file()` 会跟随到
      目标、只记目标内容：把 symlink 改指向别处、或换成同字节真文件，`raw/` 条目其实变了却
      漏判，绕过不可变门禁。
    - 普通文件 → 内容 sha256（捕获增/删/改/重命名）。
    - 目录 → 以 `<相对路径>/` 为键、`<dir>` 为值（捕获**空**目录的增删——只记文件会漏掉
      像 `raw/processed/` 这类无文件的标记目录，绕过 `raw/` 不可变硬约束）。
    - 其余条目（fifo、socket、设备节点等，`is_file`/`is_dir` 均 False）→ 同样按 lstat 建指纹。

    另含一个根标记键 `.`：区分"`raw/` 是存在的目录" vs "被删/被换成文件或符号链接"——否则
    **空** `raw/` 被删除前后都 diff 为 `{}`，删 `raw/` 也能蒙混过门禁。`raw/` 不存在 → 无根标记
    （空 dict），故"空目录存在"(`{".": "<raw-dir>"}`) 与"已删除"(`{}`)可区分。

    `raw/` 本身、或其下的**任一目录符号链接**指向目录时（`_resolve_raw_target` 支持的配置），
    既记链接指纹（捕获改指），又**跟随descend 进去递归内容**——否则经 `raw/link/...` 的写入会
    全程不可见、绕过门禁。`rglob` 不会下降进符号链接目录，故这里手写遍历并带环保护。
    """
    raw = Path(root) / "raw"
    out: dict[str, str] = {}
    if raw.is_symlink():
        out["."] = _special_fingerprint(raw)  # 记链接本身；指向目录则下面继续遍历内容
    elif not raw.exists():
        return out  # raw/ 不存在 → 无根标记
    elif not raw.is_dir():
        out["."] = _special_fingerprint(raw)  # 被换成文件等
        return out
    else:
        out["."] = "<raw-dir>"

    if not raw.is_dir():
        return out  # 指向非目录的坏符号链接 → 无内容可遍历
    try:
        seen = {raw.resolve()}
    except OSError:
        seen = set()
    _walk_raw(raw, raw, out, seen)
    return out


def _walk_raw(
    current: Path, base: Path, out: dict[str, str], seen: set[Path]
) -> None:
    """递归遍历 `current`，把条目以相对 `base` 的 posix 路径记进 `out`。

    跟随**指向目录**的符号链接（经它的写入也要被门禁捕获），用已访问真实路径集 `seen` 防环。
    """
    try:
        entries = sorted(current.iterdir())
    except OSError:
        return
    for entry in entries:
        rel = entry.relative_to(base).as_posix()
        if entry.is_symlink():
            out[rel] = _special_fingerprint(entry)  # 记链接本身（捕获改指/换文件）
            if entry.is_dir():  # 指向目录 → 跟随，捕获经它的写入
                _descend(entry, base, out, seen)
        elif entry.is_file():
            out[rel] = hashlib.sha256(entry.read_bytes()).hexdigest()
        elif entry.is_dir():
            out[rel + "/"] = "<dir>"
            _descend(entry, base, out, seen)
        else:
            out[rel] = _special_fingerprint(entry)


def _descend(entry: Path, base: Path, out: dict[str, str], seen: set[Path]) -> None:
    """带环保护地下降进目录 `entry`（真实路径去重）。"""
    try:
        real = entry.resolve()
    except OSError:
        return
    if real in seen:
        return
    seen.add(real)
    _walk_raw(entry, base, out, seen)


def _special_fingerprint(path: Path) -> str:
    """非文件/非目录条目的稳定指纹（不跟随符号链接）。"""
    try:
        info = path.lstat()
    except OSError:
        return "<gone>"
    if stat.S_ISLNK(info.st_mode):
        try:
            return f"<symlink:{os.readlink(path)}>"
        except OSError:
            return "<symlink:?>"
    return f"<special:{stat.S_IFMT(info.st_mode)}>"


def diff_raw(before: dict[str, str], after: dict[str, str]) -> list[RawChange]:
    """比对前后快照。增/删/改/重命名都体现为条目；任意非空即违规。"""
    changes: list[RawChange] = []
    for rel in sorted(before.keys() - after.keys()):
        changes.append(RawChange("removed", rel))
    for rel in sorted(after.keys() - before.keys()):
        changes.append(RawChange("added", rel))
    for rel in sorted(before.keys() & after.keys()):
        if before[rel] != after[rel]:
            changes.append(RawChange("modified", rel))
    return changes


@dataclass
class GateResult:
    """门禁结论。`kind`: None=通过 / "raw_mutated" / "check_failed" / "agent_error"。

    `warnings` 携带非阻断的断链（`wikilink.broken`），通过/失败都可能非空，仅供报告。
    """

    ok: bool
    kind: str | None = None
    raw_changes: list[RawChange] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)
    agent_error_type: str | None = None

    # 构造器（注：字段名占用了 `ok`，故工厂方法用 passed/… 命名，见 P2 §5.3）。
    @classmethod
    def passed(cls, warnings: list[Violation] | None = None) -> GateResult:
        return cls(ok=True, kind=None, warnings=warnings or [])

    @classmethod
    def raw_mutated(cls, changes: list[RawChange]) -> GateResult:
        return cls(ok=False, kind="raw_mutated", raw_changes=changes)

    @classmethod
    def check_failed(
        cls, violations: list[Violation], warnings: list[Violation] | None = None
    ) -> GateResult:
        return cls(ok=False, kind="check_failed", violations=violations, warnings=warnings or [])

    @classmethod
    def agent_error(cls, error_type: str | None) -> GateResult:
        return cls(ok=False, kind="agent_error", agent_error_type=error_type)

    @property
    def exit_code(self) -> int:
        return {
            None: EXIT_OK,
            "raw_mutated": EXIT_RAW_MUTATED,
            "check_failed": EXIT_CHECK_FAILED,
            "agent_error": EXIT_AGENT_ERROR,
        }[self.kind]


def enforce(
    root: Path,
    snapshot_before: dict[str, str],
    *,
    baseline: frozenset[tuple[str, str, str]] = frozenset(),
) -> GateResult:
    """完整门禁：先判 `raw/` 完整性（更严重），再跑 `guanlan check`。

    断链（`_WARNING_KINDS`）只作警告、不阻断（决策8）；其余 kind 为阻断性。
    `baseline` 给出写操作前**已存在**的阻断性违规（决策7 的增量门禁）——只追究不在基线里的
    **新引入**违规，历史欠债不连累本次写入。默认空基线 = 全量阻断（独立 `enforce` 调用语义不变）。
    """
    raw_changes = diff_raw(snapshot_before, snapshot_raw(root))
    if raw_changes:
        return GateResult.raw_mutated(raw_changes)
    check = run_check(Path(root) / "wiki")
    blocking, warnings = partition_violations(check.violations)
    new_blocking = [v for v in blocking if _vkey(v) not in baseline]
    if new_blocking:
        return GateResult.check_failed(new_blocking, warnings=warnings)
    return GateResult.passed(warnings=warnings)


def enforce_write_result(
    root: Path,
    snapshot_before: dict[str, str],
    run_result: AgentRunResult,
    *,
    baseline: frozenset[tuple[str, str, str]] = frozenset(),
) -> GateResult:
    """写入口（ingest、query --backfill）收尾的统一裁决。

    - `run_result.ok` 为 False：agent 失败，**仍兜底 `raw/` 完整性**——变了 → raw_mutated，
      否则 → agent_error（不跑 check：半成品 wiki/ 无意义）。
    - `run_result.ok` 为 True：走完整 `enforce`（先 raw 后 check，断链作警告、阻断性增量判定）。
    """
    if not run_result.ok:
        raw_changes = diff_raw(snapshot_before, snapshot_raw(root))
        if raw_changes:
            return GateResult.raw_mutated(raw_changes)
        return GateResult.agent_error(run_result.error_type)
    return enforce(root, snapshot_before, baseline=baseline)


def run_guarded_write(
    root: Path,
    prompt: str,
    *,
    model: str | None = None,
    runner: AgentRunner | None = None,
    max_repair: int = MAX_REPAIR_ATTEMPTS,
) -> int:
    """写入口（ingest、query --backfill）的统一编排：

    快照 `raw/` → Agentao(`workspace-write`) → `enforce_write_result` → 有界自愈 → 报告 → 退出码。
    两个写入口仅 prompt 不同，故收尾逻辑收口在此，避免漂移（docs/P2 §7/§8 承诺一致）。

    **增量门禁（决策7/8）**：写操作**前**先取 `raw/` 快照与阻断性违规基线 `baseline`；门禁只追究
    **本次新引入**的阻断性违规（frontmatter/sources），历史断链/欠债不连累本次。断链全程作警告。

    **有界自愈（决策7）**：首轮门禁若 `check_failed`（即出现**新的阻断性**违规），把这些违规
    回喂同一 agent 就地修，最多 `max_repair` 轮——把"ingest 后人工修 wiki"前移到 ingest 内。
    `raw_mutated`/`agent_error` 不自愈（完整性/运行时问题该硬失败）。
    成功后展示**首轮**的实质摘要（自愈轮只动元数据）；失败/报错展示最后一轮的结果。
    """
    before = snapshot_raw(root)
    baseline = check_baseline(root)  # 写前已存在的阻断性违规：增量门禁的基线
    first_result = run_agent_task(
        prompt,
        working_directory=root,
        permission_mode="workspace-write",
        model=model,
        runner=runner,
    )
    last_result = first_result
    gate = enforce_write_result(root, before, first_result, baseline=baseline)

    attempt = 0
    while gate.kind == "check_failed" and attempt < max_repair:
        attempt += 1
        print(
            f"↻ 门禁未过（{len(gate.violations)} 条新阻断性违规），自动修复第 {attempt}/{max_repair} 轮…",
            file=sys.stderr,
        )
        last_result = run_agent_task(
            REPAIR_PROMPT.format(violations=_render_violations(gate.violations)),
            working_directory=root,
            permission_mode="workspace-write",
            model=model,
            runner=runner,
        )
        gate = enforce_write_result(root, before, last_result, baseline=baseline)

    # 成功时优先展示首轮摘要（实质内容）；失败/报错时展示最后一轮（错误来源）。
    report_outcome(gate, first_result if gate.ok else last_result)
    return gate.exit_code


def report_agent_error(
    run_result: AgentRunResult, error_type: str | None = None, *, err: IO[str] | None = None
) -> None:
    """统一渲染"Agentao 运行失败"到 stderr（写入口与只读 query 共用，避免措辞漂移）。"""
    err = err or sys.stderr
    et = error_type or run_result.error_type or "runtime_error"
    print(f"✗ Agentao 运行失败（{et}）。", file=err)
    if run_result.final_text:
        print(run_result.final_text, file=err)


def report_outcome(
    gate: GateResult, run_result: AgentRunResult, *, out: IO[str] | None = None
) -> None:
    """打印写入口结论：成功 → 答案 + 门禁通过到 stdout；失败 → 违规项到 stderr。"""
    out = out or sys.stdout
    if gate.ok:
        if run_result.final_text:
            print(run_result.final_text, file=out)
        print("✓ 门禁通过（frontmatter + sources + raw/ 快照）。", file=out)
        _report_dangling(gate.warnings, file=out)
        return

    err = sys.stderr
    if gate.kind == "agent_error":
        report_agent_error(run_result, gate.agent_error_type, err=err)
    elif gate.kind == "raw_mutated":
        print("✗ raw/ 被改动（只读不可变被破坏）：", file=err)
        for change in gate.raw_changes:
            print(f"    [{change.kind}] raw/{change.path}", file=err)
        print("  raw/ 未保留副本，无法自动还原；请人工检查后重跑。", file=err)
    elif gate.kind == "check_failed":
        print(f"✗ 内容校验失败（{len(gate.violations)} 条新阻断性违规）：", file=err)
        for v in gate.violations:
            print(f"    [{v.kind}] {v.page}: {v.detail}", file=err)
        print("  wiki/ 改动已留在磁盘，供人工修正后重跑。", file=err)
        _report_dangling(gate.warnings, file=err)


def _report_dangling(warnings: list[Violation], *, file: IO[str]) -> None:
    """把非阻断的断链汇成一行提示（建库期正常，随新页加入自然消除，不阻断写入）。"""
    if not warnings:
        return
    pages = len({w.page for w in warnings})
    print(
        f"ℹ 另有 {len(warnings)} 处断链（{pages} 页，警告非阻断）：建库期正常，"
        "随后续资料加入会自我消除；如需排查可跑 `guanlan check`。",
        file=file,
    )
