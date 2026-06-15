"""语义审计 audit（P3.7，见 docs/P3.7-语义审计.md）。

`guanlan audit`：**确定性粗筛（零 LLM）→ LLM 复核（复用 P2 写门禁）**的两层命令，对既有 wiki 做
语义体检。**精确复刻 P3.2 的 `lint.missing_entity`（检测）↔ `heal`（物化）配对**——把"检测"从
结构升到语义触发、把"物化"换成"复核标注"。

- **Layer-1**（§3，零 LLM）：`audit_candidates()` 比对每张 source 摘要页 `raw_digest` 记录指纹与
  raw 现字节，圈出**漂移源**及沿 `sources:` slug 图传播到的**引用页**。同喂 `audit --dry-run`
  （advisory 预览）与 `audit`（worklist），单一口径不分叉。
- **Layer-2**（§5，LLM via 门禁）：对疑点页交 Agentao + skill 做语义判断，确认则就地标
  `## ⚠️ 矛盾与存疑` / 更新过期论断、记一行 JSON 留痕；走 `run_guarded_write_result(...,
  page_guard=True)`（**显式开** P2.1 源不回退闸，决策P3.7-4——该核心默认 `page_guard=False`）。
- **指纹刷新**（§4.2/§7，零 LLM，**wrapper 执行**）：仅当某漂移源**整组**（其 source 页 + 全部
  引它的页）都在本批留下**有效 JSON 留痕**时，才把该 source 页 `raw_digest` 刷成现值（决策P3.7-8
  分组原子）。"有效留痕"= 本批新增 `log.md` audit suffix 里有该页对应 JSON 行、且其 `drifted_slugs`
  与 target 精确一致（决策P3.7-10/13/14/15），**不是"门禁通过"**。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import EXIT_OK, GuanlanError
from .gate import run_guarded_write_result
from .heal import positive_int
from .pages import iter_pages, load_page
from .paths import require_kb_root
from .provenance import (
    RAW_DIGEST_KEY,
    admit_raw_path,
    compute_raw_digest,
    format_digest_value,
    parse_digest_value,
    stamp_raw_digest,
)
from .runtime import AgentRunner

__all__ = [
    "AuditCandidate",
    "AuditGroup",
    "AuditReceipt",
    "AuditResult",
    "AuditRun",
    "audit_candidates",
    "audit_preview",
    "audit_preview_dict",
    "audit_result_dict",
    "run_audit",
    "run_audit_result",
    "audit_entrypoint",
    "main",
]

# 默认每批复核上限（按**漂移源组**计，决策P3.7-8）。超额组报告为"本次推迟"（不静默丢弃）。
DEFAULT_LIMIT = 10

# 合法的逐页留痕 status 取值（决策P3.7-14）。
_VALID_STATUS = frozenset({"confirmed", "flagged", "updated"})

# 薄 prompt：真步骤在 skill 的 audit 工作流。{targets} 每行已由 wrapper 钉好
# `page | reason | drifted_slugs | raw_paths`（决策P3.7-11），Agent 不推断漂移源/raw 路径。
AUDIT_PROMPT = (
    "请按 `guanlan-wiki` skill 的 audit 工作流，逐一复核下列目标页。"
    "**每行已由 wrapper 钉好 `page | reason | drifted_slugs | raw_paths`，"
    "你无需自己推断漂移源或 raw 路径**：\n{targets}\n"
    "对每页：读该页正文 + 该行 `raw_paths` 列出的 `raw/` 源**现版本**，判断页中论断是否仍被现源支持。\n"
    " - `reason=cites-drifted-source`：对照本页跨这些源的综合是否仍成立；"
    "`reason=source-drift`（该行就是 source 摘要页自身，其 raw 在它自己的 `raw_digest` 里、不在 "
    "`sources:`）：对照本页摘要与其 raw 现版本。\n"
    " - 仍准：无需改正文（指纹刷新由 wrapper 在你返回后自动处理，**你不要碰 `raw_digest`**），"
    "status=`confirmed`。\n"
    " - 已过期 / 现源与页冲突：就地按 conventions 标 `## ⚠️ 矛盾与存疑`(status=`flagged`)，"
    "或最小化更新该论断(status=`updated`)，其余正文一字不动。\n"
    " - **逐页留痕（强制）**：在唯一一条 `## [<日期>] audit | <一句话批次说明>` 标题下，对**每个目标页**"
    "追加一行**单行 JSON** `- {{\"page\":\"<相对库根 posix>\",\"drifted_slugs\":[\"…\"],"
    "\"status\":\"confirmed|flagged|updated\"}}`（一行一个对象、不折行；`drifted_slugs` "
    "**照抄该 target 行给你的那串、不增不删**——少写会被判未复核）。这是 wrapper 判定"
    '"整组复核完"的**唯一凭据**，**漏写 = 该组不刷新指纹**；本次只能新增这一段 audit 标题，不要改历史。\n'
    "遵循 `AGENTAO.md` 硬约束。**永不修改 `raw/`。** "
    "不要运行 shell 改文件、不要自跑 check（wrapper 会强制校验）。"
    "完成后用一两句说明哪些页确认、哪些标了存疑。"
)


# ── 数据模型 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuditCandidate:
    """Layer-1 一条疑点（per-page）。`drifted_slugs` 是触发本候选的、已漂移的 source slug（升序）。"""

    page: str  # 相对库根 posix（source 摘要页 或 引用它的页）
    reason: str  # "source-drift"（直接）/ "cites-drifted-source"（传播）
    drifted_slugs: tuple[str, ...]


@dataclass(frozen=True)
class AuditGroup:
    """一个漂移源组（决策P3.7-8 的原子单元）：源 slug + 其 source 摘要页 + 全部引它的页 + raw 路径。

    刷新 `raw_digest` 与 `--limit` 计数都以**组**为单位。`members` 含 source 摘要页自身、升序。
    """

    slug: str
    source_page: str  # wiki/sources/<slug>.md（相对库根 posix）
    raw_path: str  # 该 source 页 raw_digest 解出并准入的 raw 相对路径
    members: tuple[str, ...]  # 组内全部候选页（含 source_page），升序


@dataclass(frozen=True)
class AuditReceipt:
    """逐组回执（零 LLM 可验证，§7）。`status` 纯由「整组是否都有有效 log 留痕 + 指纹是否刷成」判定。"""

    slug: str
    status: Literal["refreshed", "incomplete"]
    members: tuple[str, ...]
    reviewed: tuple[str, ...]  # 本批留下有效 JSON 留痕的成员页（升序）
    reason: str


@dataclass(frozen=True)
class AuditResult:
    """批次级机器契约（CLI `--json` / 未来 Web 共用）。"""

    receipts: tuple[AuditReceipt, ...] = ()
    refreshed_slugs: tuple[str, ...] = ()  # 本批刷新了 raw_digest 的漂移源 slug（升序）
    exit_code: int = EXIT_OK


@dataclass(frozen=True)
class AuditRun:
    """一轮真实 audit 的结构化产物（仿 `HealRun`）。`had_batch=False` = 空 worklist 短路。"""

    result: AuditResult
    postponed: tuple[AuditGroup, ...] = ()
    had_batch: bool = False
    final_text: str = ""

    @property
    def exit_code(self) -> int:
        return self.result.exit_code


# ── §3 Layer-1：确定性粗筛（零 LLM）─────────────────────────────────────────


def _page_source_slugs(meta: object) -> set[str]:
    """从容错解析的 `meta` 取 `sources` slug 集（非 dict / 非字符串列表 → 空集，绝不抛）。"""
    if not isinstance(meta, dict):
        return set()
    src = meta.get("sources")
    if not isinstance(src, list):
        return set()
    return {s for s in src if isinstance(s, str)}


def _scan_drifted_sources(wiki: Path, kb: Path) -> dict[str, str]:
    """**第一步**：遍历 `wiki/sources/*.md`，找指纹已漂移的源。返回 `{漂移 slug: 记录的 raw 相对路径}`。

    每张带 `raw_digest` 的 source 页：解值 → 路径准入（决策P3.7-12）→ 读 raw 现字节算 sha256 比对。
    无/坏 `raw_digest`（决策P3.7-5）/ 准入失败（决策P3.7-12）/ raw 已不存在（不抢 check 缺源口径）
    → 跳过不罚。**指纹只在 source 页比一次**（引用页只做廉价 slug 求交，§3）。
    """
    out: dict[str, str] = {}
    sources_dir = wiki / "sources"
    if not sources_dir.is_dir():
        return out
    for path in sorted(sources_dir.glob("*.md")):
        if not path.is_file():
            continue
        meta, _body = load_page(path)
        if not isinstance(meta, dict):
            continue
        parsed = parse_digest_value(meta.get(RAW_DIGEST_KEY))
        if parsed is None:
            continue  # 无/坏 raw_digest → 跳过不罚
        raw_rel, recorded_sha = parsed
        raw_abs = admit_raw_path(kb, raw_rel)
        if raw_abs is None or not raw_abs.is_file():
            continue  # 准入失败 / raw 已不存在 → 当无信号跳过
        try:
            current = compute_raw_digest(raw_abs)
        except OSError:
            continue
        if current != recorded_sha:
            out[path.stem] = raw_rel
    return out


def _compute(wiki: Path) -> tuple[list[AuditCandidate], dict[str, str]]:
    """算候选 + `{漂移 slug: raw 路径}`（核与 `audit_candidates` 共用，避免重复扫盘）。"""
    wiki = Path(wiki)
    kb = wiki.parent
    slug_to_raw = _scan_drifted_sources(wiki, kb)
    drifted = set(slug_to_raw)
    if not drifted:
        return [], slug_to_raw

    sources_dir = wiki / "sources"
    candidates: list[AuditCandidate] = []
    for path in iter_pages(wiki):  # 非 config 页（同 health/check 口径）
        meta, _body = load_page(path)
        # 本页是否就是某漂移源的 source 摘要页（仅 wiki/sources/ 的直接子页才算）。
        own = path.parent == sources_dir and path.stem in drifted
        related = _page_source_slugs(meta) & drifted  # 第二步：沿 sources: slug 图传播
        if own:
            related = related | {path.stem}
        if not related:
            continue
        reason = "source-drift" if own else "cites-drifted-source"
        candidates.append(
            AuditCandidate(path.relative_to(kb).as_posix(), reason, tuple(sorted(related)))
        )
    candidates.sort(key=lambda c: c.page)  # 按 page 升序字节稳定
    return candidates, slug_to_raw


def audit_candidates(wiki: Path, raw: Path | None = None) -> list[AuditCandidate]:
    """Layer-1 确定性粗筛（零 LLM）：返回按 page 升序的疑点候选（§3）。

    `raw` 仅为 call-site 对称保留（漂移源的 raw 路径由各 source 页 `raw_digest` 内含、经准入解析），
    实际边界取 `wiki.parent/"raw"`；缺省即可。
    """
    return _compute(Path(wiki))[0]


def _build_groups(candidates: list[AuditCandidate], slug_to_raw: dict[str, str]) -> list[AuditGroup]:
    """按漂移 slug 把 per-page 候选聚合成原子组（决策P3.7-8），按 slug 升序。"""
    members: dict[str, set[str]] = defaultdict(set)
    for c in candidates:
        for s in c.drifted_slugs:
            members[s].add(c.page)
    return [
        AuditGroup(
            slug=slug,
            source_page=f"wiki/sources/{slug}.md",
            raw_path=slug_to_raw[slug],
            members=tuple(sorted(members[slug])),
        )
        for slug in sorted(members)
    ]


# ── §5 Layer-2：targets 渲染 + 写后留痕解析（零 LLM）─────────────────────────


def _format_targets(
    batch: list[AuditGroup], candidates: list[AuditCandidate]
) -> tuple[str, dict[str, tuple[str, ...]]]:
    """渲染 `{targets}`（决策P3.7-11）+ 返回每页的**精确 drifted_slugs**（供决策P3.7-15 校验）。

    本批选中若干完整漂移源组去重后的页集，**每行 `page | reason | drifted_slugs | raw_paths`**——
    `drifted_slugs` 是该页所属**本批组**的 slug 集（升序），`raw_paths` 由这些组的 raw 路径预解析
    （含 source 页自身：它的 raw 在自己的 raw_digest、不在 `sources:`）。一页同属多组只列一次。
    """
    raw_by_slug = {g.slug: g.raw_path for g in batch}
    reason_by_page = {c.page: c.reason for c in candidates}
    page_slugs: dict[str, set[str]] = defaultdict(set)
    for g in batch:
        for page in g.members:
            page_slugs[page].add(g.slug)

    lines: list[str] = []
    target_slugs: dict[str, tuple[str, ...]] = {}
    for page in sorted(page_slugs):
        slugs = tuple(sorted(page_slugs[page]))
        target_slugs[page] = slugs
        raw_paths = ", ".join(raw_by_slug[s] for s in slugs)
        reason = reason_by_page.get(page, "cites-drifted-source")
        lines.append(f"- {page} | {reason} | {','.join(slugs)} | {raw_paths}")
    return "\n".join(lines), target_slugs


def _read_log(kb: Path) -> str:
    """读 `wiki/log.md` 文本（不存在则空串），供本批界定 + append-only 校验（决策P3.7-13）。"""
    log = Path(kb) / "wiki" / "log.md"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


def _parse_review_log(before_log: str, after_log: str) -> dict[str, tuple[str, ...]] | None:
    """从**本批新增** log suffix 解析逐页有效留痕（决策P3.7-13/14）。返回 `{page: 升序 drifted_slugs}`。

    严格契约——任一不满足 → 返回 `None`（**任何组都不刷新**，整批留候选）：
    - **append-only**：`after_log.startswith(before_log)`（仿 `heal.py:433`）；
    - suffix 内**恰好一个**新 `## […] audit …` 段（多段/零段 → None）；
    - 该段内每个 `- ` bullet 都是合法单行 JSON 且为 `{page:str, drifted_slugs:[str], status:合法}`
      （任一行非法 / 缺 page / status 非法 → None，决策P3.7-14）。

    well-formed 但 `page`/`drifted_slugs` 与本批 target 不符的语义判定不在此做（留给消费侧逐组比对）。
    """
    if not after_log.startswith(before_log):
        return None  # 非 append-only（截断/改史）
    suffix = after_log[len(before_log):]
    section_lines = _single_audit_section(suffix)
    if section_lines is None:
        return None  # 非恰好一段 audit 标题
    reviewed: dict[str, tuple[str, ...]] = {}
    for line in section_lines:
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue  # 段内非 bullet 行（散文/空行）跳过
        try:
            obj = json.loads(stripped[2:])
        except (json.JSONDecodeError, ValueError):
            return None  # 任一 bullet 非合法 JSON → 整批不刷新
        if not _valid_review_obj(obj):
            return None  # 缺 page / drifted_slugs 非字符串列表 / status 非法 → 整批不刷新
        reviewed[obj["page"]] = tuple(sorted(obj["drifted_slugs"]))
    return reviewed


_AUDIT_HEADING_PREFIX = "## "
_AUDIT_TOKEN = "audit"


def _single_audit_section(suffix: str) -> list[str] | None:
    """切出 suffix 内**恰好一个**新 `## […] audit …` 段的行集；非恰好一段 → None（决策P3.7-13）。

    "audit 段" = 行以 `## ` 起且含 `audit` 标记（容忍 `## [日期] audit | …` 体例）。返回该段从标题
    到下一个 `## ` 标题（或 suffix 末）之间的行（不含标题行本身）。本批应只新增一个 audit 段——
    若 suffix 里出现 0 个或 ≥2 个 audit 段（含 Agent 误改历史引入），返回 None。
    """
    lines = suffix.splitlines()
    audit_heads = [
        i
        for i, ln in enumerate(lines)
        if ln.startswith(_AUDIT_HEADING_PREFIX) and _AUDIT_TOKEN in ln.lower()
    ]
    if len(audit_heads) != 1:
        return None
    start = audit_heads[0]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith(_AUDIT_HEADING_PREFIX):
            end = j
            break
    return lines[start + 1:end]


def _valid_review_obj(obj: object) -> bool:
    """逐页留痕 JSON 形态校验：`{page:非空str, drifted_slugs:[str], status:合法}`（决策P3.7-14）。"""
    if not isinstance(obj, dict):
        return False
    page = obj.get("page")
    slugs = obj.get("drifted_slugs")
    status = obj.get("status")
    if not isinstance(page, str) or not page:
        return False
    if not isinstance(slugs, list) or not all(isinstance(s, str) for s in slugs):
        return False
    return isinstance(status, str) and status in _VALID_STATUS


# ── §4.2/§7 分组原子刷新 + 回执（零 LLM，wrapper 执行）──────────────────────


def _refresh_one(kb: Path, g: AuditGroup) -> bool:
    """把组 `g` 的 source 页 `raw_digest` 刷成现 raw 字节指纹（同款 YAML-safe 归口 + 写后 check）。"""
    raw_abs = admit_raw_path(kb, g.raw_path)
    if raw_abs is None or not raw_abs.is_file():
        return False
    try:
        sha = compute_raw_digest(raw_abs)
    except OSError:
        return False
    value = format_digest_value(g.raw_path, sha)
    return stamp_raw_digest(kb / g.source_page, value)


def _refresh_groups(
    kb: Path,
    batch: list[AuditGroup],
    target_slugs: dict[str, tuple[str, ...]],
    reviewed: dict[str, tuple[str, ...]] | None,
    *,
    gate_ok: bool,
) -> tuple[list[AuditReceipt], list[str]]:
    """逐组判完整 + 刷新（决策P3.7-8/10/15），返回 `(receipts, 已刷新 slug 列表)`。

    某组完整 ⟺ `gate_ok` **且** 留痕解析成功 **且** 组内每个成员页都有有效留痕——"有效" = 该页有
    JSON 行、且其 `drifted_slugs` 与 `_format_targets` 给该页的 `drifted_slugs` 排序后**精确一致**
    （决策P3.7-15）。完整才刷新该源 `raw_digest`；否则整组留待下次（自愈：下次整组重进候选）。
    """

    def valid(page: str) -> bool:
        return (
            reviewed is not None
            and page in reviewed
            and reviewed[page] == target_slugs.get(page)
        )

    receipts: list[AuditReceipt] = []
    refreshed: list[str] = []
    for g in batch:
        reviewed_members = tuple(m for m in g.members if valid(m))
        complete = gate_ok and reviewed is not None and len(reviewed_members) == len(g.members)
        if complete and _refresh_one(kb, g):
            refreshed.append(g.slug)
            receipts.append(
                AuditReceipt(g.slug, "refreshed", g.members, reviewed_members, "整组复核留痕，已刷新源指纹")
            )
            continue
        receipts.append(
            AuditReceipt(
                g.slug,
                "incomplete",
                g.members,
                reviewed_members,
                _incomplete_reason(gate_ok, reviewed, g, reviewed_members, complete),
            )
        )
    return receipts, refreshed


def _incomplete_reason(
    gate_ok: bool,
    reviewed: dict[str, tuple[str, ...]] | None,
    g: AuditGroup,
    reviewed_members: tuple[str, ...],
    complete: bool,
) -> str:
    """组未刷新的一句话原因（仅展示，不影响退出码）。"""
    if not gate_ok:
        return "门禁未过，改动留盘待修，整组留待下次"
    if reviewed is None:
        return "本批 log 留痕解析失败（非 append-only / 非单段 / 含非法 JSON 行），整批不刷新"
    if complete:  # 全部留痕齐全但刷新写盘失败
        return "源指纹刷新失败（source 页不可写 / 写后校验未过），整组留待下次"
    return f"{len(reviewed_members)}/{len(g.members)} 页有有效留痕，组未复核全，留待下次"


# ── 编排 + 渲染 ─────────────────────────────────────────────────────────────


def run_audit_result(
    *,
    root: str | Path = ".",
    limit: int = DEFAULT_LIMIT,
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> AuditRun:
    """audit 真实写路径的**不打印**编排核心（仿 `run_heal_result`）。

    `require_kb_root` → `_compute` → `_build_groups` → 空 batch 短路（未触 Agentao）/（`before_log`
    快照 → `run_guarded_write_result(page_guard=True)` → `_parse_review_log` → `_refresh_groups`）。
    **不碰 stdout、不处理 dry_run/json_output**——渲染与短路特判全留给薄壳。
    """
    kb = require_kb_root(root, writable=True)
    wiki = kb / "wiki"
    candidates, slug_to_raw = _compute(wiki)
    groups = _build_groups(candidates, slug_to_raw)
    batch = groups[:limit]  # --limit 限**组**数（决策P3.7-8）
    postponed = tuple(groups[limit:])

    # 空 worklist：短路 EXIT_OK、不触 Agentao（仿决策P3.2-6）。
    if not batch:
        return AuditRun(result=AuditResult(), postponed=postponed, had_batch=False)

    targets_text, target_slugs = _format_targets(batch, candidates)
    before_log = _read_log(kb)
    write = run_guarded_write_result(
        kb,
        AUDIT_PROMPT.format(targets=targets_text),
        model=model,
        runner=runner,
        page_guard=True,  # 显式开 P2.1 源不回退闸（决策P3.7-4，核心默认 False）
    )
    after_log = _read_log(kb)
    reviewed = _parse_review_log(before_log, after_log)
    receipts, refreshed = _refresh_groups(
        kb, batch, target_slugs, reviewed, gate_ok=write.gate.ok
    )

    result = AuditResult(
        receipts=tuple(receipts),
        refreshed_slugs=tuple(refreshed),
        exit_code=write.exit_code,
    )
    return AuditRun(
        result=result, postponed=postponed, had_batch=True, final_text=write.final_text
    )


def run_audit(
    *,
    root: str | Path = ".",
    limit: int = DEFAULT_LIMIT,
    model: str | None = None,
    dry_run: bool = False,
    json_output: bool = False,
    runner: AgentRunner | None = None,
) -> int:
    """语义审计，返回退出码（见 errors.py）。薄壳：dry-run 纯读不进核；真实写路径调 `run_audit_result`。"""
    # dry-run：纯读、零 LLM、不触 Agentao、必 EXIT_OK（决策P3.7 §6）——不进 core。
    if dry_run:
        try:
            kb = require_kb_root(root, writable=False)
        except GuanlanError as exc:
            print(exc, file=sys.stderr)
            return exc.exit_code
        candidates, slug_to_raw = _compute(kb / "wiki")
        groups = _build_groups(candidates, slug_to_raw)
        _print_preview(groups[:limit], groups[limit:], json_output=json_output)
        return EXIT_OK

    try:
        run = run_audit_result(root=root, limit=limit, model=model, runner=runner)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    postponed = list(run.postponed)
    if not run.had_batch:
        if json_output:
            print(_dumps({"refreshed": [], "postponed": [_group_dict(g) for g in postponed]}))
        elif postponed:
            print(
                f"· --limit={limit} 下本批未复核任何组；{len(postponed)} 个漂移源组全部推迟"
                "（提高 --limit 续审，或 --dry-run 预览）。"
            )
        else:
            print("✓ 无漂移源（raw 指纹与建页时一致，或无带 raw_digest 的 source 页）。")
        return run.exit_code

    if json_output:
        print(_dumps(audit_result_dict(run.result, postponed)))
    else:
        _print_human(run.final_text, run.result, postponed)
    return run.exit_code


def audit_entrypoint(
    root_dir: str | Path,
    *,
    limit: int,
    model: str | None,
    dry_run: bool,
    json_output: bool,
) -> int:
    """`guanlan audit` 的单一落地（CLI 与 `python -m guanlan.audit` 共用）。"""
    return run_audit(
        root=root_dir, limit=limit, model=model, dry_run=dry_run, json_output=json_output
    )


# ── 渲染辅助 ───────────────────────────────────────────────────────────────


def _dumps(obj: object) -> str:
    """与仓内 `report_json` 同风格：UTF-8 原样、缩进 2、无尾换行。"""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _group_dict(g: AuditGroup) -> dict:
    return {
        "slug": g.slug,
        "source_page": g.source_page,
        "raw_path": g.raw_path,
        "members": list(g.members),
    }


def audit_preview_dict(
    batch: Sequence[AuditGroup], postponed: Sequence[AuditGroup]
) -> dict:
    """audit **预览**（dry-run）的 wire 契约（CLI `--dry-run --json` / Web `GET /api/audit/preview`
    共用，仿 `audit_result_dict`）：本批将复核的漂移源组 + 因 `--limit` 推迟的组。"""
    return {
        "groups": [_group_dict(g) for g in batch],
        "postponed": [_group_dict(g) for g in postponed],
    }


def audit_preview(wiki: str | Path, *, limit: int = DEFAULT_LIMIT) -> dict:
    """零-LLM 预览漂移源组（== `audit --dry-run`），返回 `{groups, postponed}`。

    单一归口：`_compute` → `_build_groups` → 按 `limit` 切分 → `audit_preview_dict`。CLI dry-run 与
    Web `GET /api/audit/preview` 共用此口径（决策P4.12-1：预览序列化不再宿主侧重写）。纯读 `wiki/`、
    不取 `raw/` 快照、不触 Agentao、不入队。
    """
    candidates, slug_to_raw = _compute(Path(wiki))
    groups = _build_groups(candidates, slug_to_raw)
    return audit_preview_dict(groups[:limit], groups[limit:])


def audit_result_dict(result: AuditResult, postponed: Sequence[AuditGroup]) -> dict:
    """audit 批次回执的 wire 契约（CLI `--json` / 未来 Web 共用，仿 `heal_result_dict`）。"""
    return {
        "refreshed": list(result.refreshed_slugs),
        "postponed": [_group_dict(g) for g in postponed],
        "receipts": [dataclasses.asdict(r) for r in result.receipts],
        "exit_code": result.exit_code,
    }


def _print_preview(
    batch: list[AuditGroup], postponed: list[AuditGroup], *, json_output: bool
) -> None:
    if json_output:
        print(_dumps(audit_preview_dict(batch, postponed)))
        return
    if not batch and not postponed:
        print("✓ 无漂移源（raw 指纹与建页时一致，或无带 raw_digest 的 source 页）。")
        return
    print(f"· audit 预览（dry-run，零 LLM）：本批将复核 {len(batch)} 个漂移源组：")
    for g in batch:
        print(f"    ⚠ {g.slug}（源 {g.raw_path} 已变；{len(g.members)} 页待复核：{', '.join(g.members)}）")
    if postponed:
        print(f"  另有 {len(postponed)} 个漂移源组因 --limit 本次推迟（提高 --limit 续审）：")
        for g in postponed:
            print(f"    · {g.slug}（{len(g.members)} 页）")


def _print_human(final_text: str, result: AuditResult, postponed: list[AuditGroup]) -> None:
    """自渲染人读报告（不走 gate 的 report_outcome）。"""
    if final_text:
        print(final_text)
    n = len(result.receipts)
    refreshed = [r for r in result.receipts if r.status == "refreshed"]
    incomplete = [r for r in result.receipts if r.status == "incomplete"]
    print(
        f"· audit 复核 {n} 个漂移源组：{len(refreshed)} 组已刷新指纹，{len(incomplete)} 组未完成"
        + (f"，{len(postponed)} 组推迟" if postponed else "")
        + "。"
    )
    for r in refreshed:
        print(f"    ✓ {r.slug} → raw_digest 已刷新（{len(r.members)} 页整组复核）")
    for r in incomplete:
        print(f"    · {r.slug}（{r.reason}）")
    if postponed:
        print(f"  另有 {len(postponed)} 个漂移源组因 --limit 推迟，提高 --limit 续审。")


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.audit` 入口（与 `guanlan audit` 共享 audit_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.audit",
        description="语义审计：确定性粗筛漂移源 → LLM 复核过期论断（走 P2 写门禁）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=DEFAULT_LIMIT,
        help=f"本批复核的漂移源组上限（默认 {DEFAULT_LIMIT}，按 slug 升序；须 ≥ 1）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印漂移源组，不写、不触 LLM")
    parser.add_argument("--model", default=None, help="覆盖 Agentao 模型")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON")
    args = parser.parse_args(argv)
    return audit_entrypoint(
        args.dir,
        limit=args.limit,
        model=args.model,
        dry_run=args.dry_run,
        json_output=args.json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
