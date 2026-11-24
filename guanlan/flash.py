"""快速收录（flash ingest）**原型**：`guanlan ingest --flash raw/<file>.md`。

完整 ingest 的成本主要在跨页整合（读既有页、合并、overview、写后核对），请求数随触及页数增长
（docs/backlog/notes/ingest-performance-research.md）。flash 把写集**硬性**缩到本篇 source 页一张：

1. Agent 只把候选 source 页写到暂存区 `workspace/flash/<slug>.md`，不得写 `wiki/`；
2. wrapper 校验候选（frontmatter、`type: source`、本篇来源名、正文非空）后才提交到
   `wiki/sources/<slug>.md`，再跑增量门禁（与 ingest 同一 `gate.enforce`），不过就回滚该页、
   把问题回喂 Agent 修候选（有界）；
3. index 与 log 由 wrapper **确定性**登记，可安全重试（已登记不重复）；
4. 页上写 `raw_digest`（供 audit 检测原文漂移）与 `ingest_depth: flash`（待加深标记）；完整 ingest
   成功后由 wrapper 清掉该标记（`clear_flash_marker`）。

越界写 `wiki/` 的处理：运行前取 `wiki/` 全部文件的字节快照，运行后发现任何变动就逐字还原并判失败——
flash 的契约是「wiki/ 只由 wrapper 写」，不像 heal 那样只报告。

已有非 flash 的 source 页（完整入库过、手写或桩页）一律跳过，不覆盖已整合的内容；只有候选、未能认定
本篇 source 页时也不动手，交给完整 ingest 判断。
"""

from __future__ import annotations

import datetime
import os
import re
import sys
import time
from pathlib import Path

import yaml

from .catalog import CATALOG_FORMAT, build_catalog
from .errors import (
    EXIT_AGENT_ERROR,
    EXIT_CHECK_FAILED,
    EXIT_OK,
    EXIT_RAW_MUTATED,
    EXIT_USAGE,
    GuanlanError,
)
from .gate import (
    GateResult,
    GuardedWriteResult,
    _render_violations,
    check_baseline,
    diff_raw,
    enforce,
    format_run_stats,
    report_agent_error,
    report_outcome,
    snapshot_raw,
)
from .pages import index_md_links, load_page, page_title, split_frontmatter
from .paths import require_kb_root
from .provenance import (
    FLASH_KEY,
    FLASH_VALUE,
    RAW_DIGEST_KEY,
    compute_raw_digest,
    format_digest_value,
    is_flash_meta,
)
from .rawio import atomic_write_bytes, atomic_write_text, detect_eol, dump_frontmatter, read_text_verbatim
from .reindex import _aliases, _apply_additions, _join_lines, _safe_anchor, _split_lines
from .runtime import AgentRunner, AgentRunResult, run_agent_task
from .sourcepage import locate_source_page, source_slug

# 候选页专用键：Agent 写给 index 的一句话，wrapper 取走、不落进正式页。
SUMMARY_KEY = "index_summary"
SUMMARY_MAX_CHARS = 200
STAGING_DIR = Path("workspace") / "flash"
MAX_REPAIR_ATTEMPTS = 2
# 正文实质字数下限：低于它视为没写完（与 health 桩页阈值同量级，略高以挡「只有标题 + 一句」）。
MIN_BODY_CHARS = 80
# flash 的名录预算比 ingest 小：名录只用来选链接名，不做查重，太大就不附，让模型按原文措辞写前向引用。
FLASH_CATALOG_BUDGET_CHARS = 40_000

FLASH_PROMPT = (
    "本次是**快速收录（flash）**，不走 `guanlan-wiki` skill 的完整 ingest 工作流：只为资料 `raw/{rel}` "
    "写一张 source 页候选，写到 `{staging}`（用 `write_file` 一次写成完整页）。"
    "该资料路径已由 wrapper 校验存在，按原样读取，不要替换其中的引号、空格或 CJK 字符。\n"
    "**不得写或修改 `wiki/` 下任何文件**：index、log 由 wrapper 登记，实体/概念页留待以后的完整入库；"
    "wrapper 在你返回后核对，`wiki/` 有任何变动都会被还原并判本次失败。"
    "**不要读 `wiki/` 下的既有页**，也不要读其他 raw 资料——本次只看这一篇。\n"
    "步骤：\n"
    "1. 读资料与根级 `SCHEMA.md`；source 页的 frontmatter 与正文模板、直接引语规则见 skill 的 conventions。"
    "本篇的行数、字数与标题行号见文末〈资料结构〉，不要再用工具统计；"
    "超过 2000 行或 4 万字的长资料按标题区间用 `read_file` 的 `offset`/`limit` 分段读到末尾，不要只读开头。\n"
    "2. 写候选页 frontmatter：`title`、`type: source`、`tags`（按 SCHEMA 的词表）、"
    "`sources: ['{slug}']`（只列本篇）、`last_updated: {today}`，"
    "另加一个 `{summary_key}`：一句话说明本篇是什么、核心内容与关键时间（{summary_max} 字以内，wrapper 用它登记 index）。"
    "字符串值用单引号。\n"
    "3. 正文按 source 页模板写摘要、要点与关键论断，保留关键数字、限定条件与时间（按资料自身日期换算成绝对时间）；"
    "直接引语逐字照抄。\n"
    "4. 真实体、概念、方法名首次出现写成 `[[名称]]`：{link_rule}"
    "不要为这些名称建页或读页。\n"
    "5. 本篇内部有影响论断的具体缺口时，在候选页就地写 `## ⚠️ 资料冲突与待核` 节（格式见 conventions）；"
    "不做跨资料比对。\n"
    "**不要运行 shell 命令；读写文件只用内置文件工具。** 完成后用一句话说明。"
)
_LINK_WITH_CATALOG = (
    "名称优先用文末〈页面名录〉（{fmt}）里已有页的文件名或别名，原文措辞不同时写 `[[页面名|原文措辞]]`；"
    "名录里没有的照写前向引用 `[[名称]]`，断链不阻断。"
)
_LINK_WITHOUT_CATALOG = "本库名录过大未附，按资料原文措辞写前向引用即可，断链不阻断。"

REPAIR_PROMPT = (
    "快速收录（flash）的候选页 `{staging}` 未通过 wrapper 校验（正式路径 `{target}` 报出的问题即对应候选页）：\n"
    "{problems}\n"
    "只修改候选页 `{staging}` 修正这些问题，不要写 `wiki/` 下任何文件，不要运行 shell。"
    "frontmatter 字符串值用单引号，值内单引号翻倍为 `''`。修完用一句话说明。"
)


def clear_flash_marker(page: Path) -> bool:
    """完整 ingest 成功后去掉 `ingest_depth: flash`。无标记 / 读不出 → False、不写盘。"""
    if page.is_symlink() or not page.is_file():
        return False
    try:
        original = read_text_verbatim(page)
    except OSError:
        return False
    block, body = split_frontmatter(original)
    if block is None:
        return False
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError:
        return False
    if not isinstance(meta, dict) or FLASH_KEY not in meta:
        return False
    meta.pop(FLASH_KEY)
    try:
        atomic_write_text(page, dump_frontmatter(meta, body, eol=detect_eol(original)))
    except OSError:
        return False
    return True


# ---------------------------------------------------------------- wiki/ 字节快照（越界写还原）


def _snapshot_tree(base: Path) -> dict[str, bytes | None]:
    """`base` 下全部条目 → 字节（符号链接记 None，只报告不还原）。目录本身不记。"""
    out: dict[str, bytes | None] = {}
    if not base.is_dir():
        return out
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            p = Path(dirpath) / name
            rel = p.relative_to(base).as_posix()
            if p.is_symlink():
                out[rel] = None
                continue
            try:
                out[rel] = p.read_bytes()
            except OSError:
                out[rel] = None
    return out


def _restore_tree(base: Path, before: dict[str, bytes | None]) -> list[str]:
    """把 `base` 还原到快照：新增的删掉、改动/删除的写回原字节。返回变动过的相对路径。"""
    after = _snapshot_tree(base)
    changed = sorted(
        rel
        for rel in before.keys() | after.keys()
        if before.get(rel, b"\0missing") != after.get(rel, b"\0missing")
    )
    for rel in changed:
        p = base / rel
        if rel not in before:
            try:
                p.unlink()
            except OSError:
                pass
        elif before[rel] is not None:
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(p, before[rel])
    return changed


# ---------------------------------------------------------------- 候选页校验


def _single_line(text: str, limit: int) -> str:
    line = re.sub(r"\s+", " ", text).strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _read_candidate(
    staging: Path, slug: str, keep_sources: list[str], today: str
) -> tuple[dict, str, str] | list[str]:
    """读并校验候选页。通过 → `(meta, body, summary)`（meta 已并好来源、去掉 summary 键）；否则问题清单。"""
    if staging.is_symlink() or not staging.is_file():
        return [f"候选页 {STAGING_DIR.as_posix()}/{staging.name} 不存在（须用 write_file 写成完整页）"]
    try:
        text = staging.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"候选页读不出：{exc}"]
    block, body = split_frontmatter(text)
    if block is None:
        return ["候选页缺 frontmatter（须以 `---` 开头的 YAML 块）"]
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        return [f"frontmatter 不是合法 YAML：{_single_line(str(exc), 200)}"]
    if not isinstance(meta, dict):
        return ["frontmatter 须为键值映射"]
    problems: list[str] = []
    if meta.get("type") != "source":
        problems.append(f"type 须为 'source'，实为 {meta.get('type')!r}")
    title = meta.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append("title 须为非空字符串")
    sources = meta.get("sources")
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        problems.append(f"sources 须为字符串列表，且含本篇 '{slug}'")
    elif slug not in sources:
        problems.append(f"sources 须含本篇 '{slug}'")
    if len(re.sub(r"\s+", "", re.sub(r"(?m)^#+.*$", "", body))) < MIN_BODY_CHARS:
        problems.append(f"正文过短（实质内容不足 {MIN_BODY_CHARS} 字），须写完摘要与要点")
    if problems:
        return problems

    raw_summary = meta.pop(SUMMARY_KEY, None)
    summary = _single_line(raw_summary, SUMMARY_MAX_CHARS) if isinstance(raw_summary, str) else ""
    # 摘要接在 index 登记行的链接之后：中和方括号，免得模型写的 `[x](y)` 被 index 解析成多一条登记
    summary = summary.replace("[", "［").replace("]", "］")
    merged = list(dict.fromkeys([*keep_sources, *sources]))  # 重收录只增不减来源
    meta["sources"] = merged
    if "last_updated" not in meta:
        meta["last_updated"] = today
    return meta, body, summary


# ---------------------------------------------------------------- index / log（确定性、可重试）


def _register_index(wiki: Path, page: Path, summary: str) -> bool:
    """在 Sources 分区登记本页；已有指向它的链接就不动（重试不重复）。返回是否写盘。"""
    index = wiki / "index.md"
    text = read_text_verbatim(index) if index.is_file() else ""
    rel = page.relative_to(wiki).as_posix()
    if rel in index_md_links(text):
        return False
    meta, _body = load_page(page)
    line = f"- [{_safe_anchor(page_title(meta, page.stem))}]({rel})"
    aliases = _aliases(meta)
    tail = summary + (f"（别名：{'/'.join(aliases)}）" if aliases else "")
    if tail:
        line += f" — {tail}"
    lines, eol, trailing = _split_lines(text)
    lines = _apply_additions(lines, {"Sources": [line]})
    atomic_write_text(index, _join_lines(lines, eol, trailing or not text))
    return True


def _append_log(wiki: Path, today: str, title: str) -> bool:
    """追加一行 ingest 记录（conventions：每条一行标题）；末条已是同一行就不重复（重试安全）。返回是否写盘。"""
    log = wiki / "log.md"
    entry = f"## [{today}] ingest | {_single_line(title, 120)}（快速收录）"
    text = read_text_verbatim(log) if log.is_file() else ""
    eol = detect_eol(text) if text else "\n"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and lines[-1] == entry:
        return False
    sep = "" if not text or text.endswith(("\n", "\r")) else eol
    atomic_write_text(log, text + sep + entry + eol)
    return True


# ---------------------------------------------------------------- prompt


def build_flash_prompt(kb: Path, rel: str, slug: str, staging_rel: str, today: str) -> str:
    from .ingest import _raw_structure_block  # 与 ingest 同一份〈资料结构〉

    tail = [_raw_structure_block(kb, rel)]
    catalog = build_catalog(kb / "wiki")
    if catalog.chars > FLASH_CATALOG_BUDGET_CHARS:
        print(
            f"ℹ 页面名录 {catalog.chars:,} 字超出 flash 预算 {FLASH_CATALOG_BUDGET_CHARS:,} 字，本次不附名录。",
            file=sys.stderr,
        )
        link_rule = _LINK_WITHOUT_CATALOG
    else:
        link_rule = _LINK_WITH_CATALOG.format(fmt=CATALOG_FORMAT)
        body = catalog.text or "（本库尚无页面）"
        tail.append(f"〈页面名录〉共 {catalog.pages} 页：\n{body}\n〈页面名录完〉")
    head = FLASH_PROMPT.format(
        rel=rel,
        staging=staging_rel,
        slug=slug,
        today=today,
        summary_key=SUMMARY_KEY,
        summary_max=SUMMARY_MAX_CHARS,
        link_rule=link_rule,
    )
    return "\n\n".join([head, *tail])


# ---------------------------------------------------------------- 编排


def _finish_failed(
    kb: Path,
    wiki_before: dict[str, bytes | None],
    staging: Path,
) -> list[str]:
    """失败收尾：还原 wiki/ 越界写、删暂存候选。返回被还原的路径。"""
    restored = _restore_tree(kb / "wiki", wiki_before)
    try:
        staging.unlink()
    except OSError:
        pass
    return restored


def run_flash_ingest(
    target: str,
    *,
    root: str | Path = ".",
    model: str | None = None,
    thinking: str | None = None,
    runner: AgentRunner | None = None,
) -> int:
    """快速收录一篇 `.md`，返回退出码（同 ingest，见 errors.py）。"""
    from .ingest import _reject_source_slug_collision, _resolve_raw_target

    try:
        kb = require_kb_root(root, writable=True)
        tpath = _resolve_raw_target(kb, target)
        raw_dir = (kb / "raw").resolve()
        _reject_source_slug_collision(raw_dir, tpath, kb / "wiki" / "sources")
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    rel = tpath.relative_to(raw_dir).as_posix()
    wiki = kb / "wiki"
    loc = locate_source_page(kb, rel)
    keep_sources: list[str] = []
    if loc.page is not None:
        meta, _body = load_page(loc.page)
        if not is_flash_meta(meta):
            print(
                f"· 跳过：本篇已有 source 页 {loc.page.relative_to(kb).as_posix()}（非快速收录产出），"
                f"flash 不覆盖；要加深请跑 `guanlan ingest raw/{rel}`。",
                file=sys.stderr,
            )
            return EXIT_OK
        page = loc.page
        if isinstance(meta, dict) and isinstance(meta.get("sources"), list):
            keep_sources = [s for s in meta["sources"] if isinstance(s, str)]
    elif loc.candidates:
        listing = "\n".join(f"    - {p.relative_to(kb).as_posix()}（{why}）" for p, why in loc.candidates)
        print(
            f"✗ 未能认定本篇 source 页，以下既有页可能同源：\n{listing}\n"
            f"  flash 不做同源判断；请跑完整 `guanlan ingest raw/{rel}`。",
            file=sys.stderr,
        )
        return EXIT_USAGE
    else:
        slug = source_slug(rel)
        if not slug:
            print(f"✗ 无法从文件名推出 source 页名：raw/{rel}", file=sys.stderr)
            return EXIT_USAGE
        page = wiki / "sources" / f"{slug}.md"
    slug = page.stem

    today = datetime.date.today().isoformat()
    staging = kb / STAGING_DIR / f"{slug}.md"
    staging.parent.mkdir(parents=True, exist_ok=True)
    if staging.exists() or staging.is_symlink():
        staging.unlink()  # 上次失败的残留：暂存区归 wrapper 管
    staging_rel = (STAGING_DIR / staging.name).as_posix()

    if sys.stderr.isatty():
        print(f"→ 正在快速收录 raw/{rel}（flash：只建 source 页）…", file=sys.stderr, flush=True)

    started = time.monotonic()
    raw_before = snapshot_raw(kb)
    wiki_before = _snapshot_tree(wiki)
    baseline = check_baseline(kb)
    runs: list[AgentRunResult] = []

    def _run(prompt: str) -> AgentRunResult:
        result = run_agent_task(
            prompt,
            working_directory=kb,
            permission_mode="workspace-write",
            model=model,
            thinking=thinking,
            runner=runner,
        )
        runs.append(result)
        return result

    def _stats(gate: GateResult) -> None:
        line = format_run_stats(
            GuardedWriteResult(
                gate=gate, run_result=runs[0], runs=tuple(runs), elapsed_s=time.monotonic() - started
            )
        )
        if line:
            print(line, file=sys.stderr)

    prev_bytes = page.read_bytes() if page.is_file() else None  # 须在运行前取：Agent 越界写会改它
    # 运行中被打断（Ctrl-C / 取消 / 意外异常）也守住「wiki/ 只由 wrapper 写」：还原越界写、删候选再上抛。
    try:
        result = _run(build_flash_prompt(kb, rel, slug, staging_rel, today))
        attempt = 0
        while True:
            raw_changes = diff_raw(raw_before, snapshot_raw(kb))
            if raw_changes:
                _finish_failed(kb, wiki_before, staging)
                report_outcome(GateResult.raw_mutated(raw_changes), result)
                _stats(GateResult.raw_mutated(raw_changes))
                return EXIT_RAW_MUTATED
            if not result.ok:
                restored = _finish_failed(kb, wiki_before, staging)
                report_agent_error(result)
                if restored:
                    print(f"  另：已还原 wiki/ 下 {len(restored)} 个被改动的文件。", file=sys.stderr)
                _stats(GateResult.agent_error(result.error_type))
                return EXIT_AGENT_ERROR
            stray = _restore_tree(wiki, wiki_before)
            if stray:
                try:
                    staging.unlink()
                except OSError:
                    pass
                print("✗ flash 越界写：Agent 改动了 wiki/（已逐字还原），本次未收录：", file=sys.stderr)
                for rel_path in stray:
                    print(f"    wiki/{rel_path}", file=sys.stderr)
                _stats(GateResult.check_failed([]))
                return EXIT_CHECK_FAILED

            problems: list[str]
            parsed = _read_candidate(staging, slug, keep_sources, today)
            gate: GateResult | None = None
            if isinstance(parsed, list):
                problems = parsed
            else:
                meta, body, summary = parsed
                meta[RAW_DIGEST_KEY] = format_digest_value(f"raw/{rel}", compute_raw_digest(tpath))
                meta[FLASH_KEY] = FLASH_VALUE
                page.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(page, dump_frontmatter(meta, body))
                gate = enforce(kb, raw_before, baseline=baseline)
                if gate.ok:
                    break
                # 回滚正式页，问题回喂候选
                if prev_bytes is None:
                    page.unlink()
                else:
                    atomic_write_bytes(page, prev_bytes)
                problems = [_render_violations(gate.violations)] if gate.violations else ["门禁未通过"]
                if gate.kind == "raw_mutated":
                    continue

            if attempt >= MAX_REPAIR_ATTEMPTS:
                _finish_failed(kb, wiki_before, staging)
                print(f"✗ 快速收录失败（自愈 {attempt} 轮后候选页仍未通过）：", file=sys.stderr)
                for p in problems:
                    print(f"    {p}", file=sys.stderr)
                _stats(gate or GateResult.check_failed([]))
                return EXIT_CHECK_FAILED
            attempt += 1
            print(
                f"↻ 候选页未过校验（{len(problems)} 项），自动修复第 {attempt}/{MAX_REPAIR_ATTEMPTS} 轮…",
                file=sys.stderr,
            )
            result = _run(
                REPAIR_PROMPT.format(
                    staging=staging_rel,
                    target=page.relative_to(kb).as_posix(),
                    problems="\n".join(f"- {p}" for p in problems),
                )
            )

        index_written = _register_index(wiki, page, summary)
        log_written = _append_log(wiki, today, str(meta.get("title", slug)))
        try:
            staging.unlink()
        except OSError:
            pass
    except BaseException:
        _finish_failed(kb, wiki_before, staging)
        raise
    report_outcome(gate, runs[0])
    print(
        f"✓ 快速收录：{page.relative_to(kb).as_posix()}（ingest_depth: flash）"
        f"{'；已登记 index' if index_written else ''}{'；已记 log' if log_written else ''}。"
        f"要整合进实体/概念页，之后跑 `guanlan ingest raw/{rel}`。",
    )
    _stats(gate)
    return EXIT_OK
