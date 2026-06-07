"""缺失实体物化 heal（P3.2，见 docs/P3.2-缺失实体物化.md）。

`guanlan heal`：把 `lint.missing_entity`（被 ≥ 阈值张不同页引用却无页的高价值断链）按需用 LLM
物化成 `wiki/entities/` 页。**检测确定性、生成有门禁、默认非自动、按需且有界。**

- **worklist**（§2，零 LLM）：复用 `lint.missing_entities` 的结构化聚合——与 `guanlan lint` 同源同阈值。
- **写路径**（§3）：复用 P2 写门禁的结果版 `gate.run_guarded_write_result`，与 ingest 同一条编排核心；
  门禁、自愈、退出码全继承，wrapper 只额外做 `wiki/` 写集审计与写后回执。
- **回执**（§5，零 LLM）：写完**重算 `build_graph`**，逐目标判 `resolved` / `still_broken`，并把有害写集
  收进批次级 `unexpected_writes`。**全程不解析 agent 输出**——正确性只看图。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import EXIT_OK, GuanlanError
from .gate import diff_raw, run_guarded_write_result
from .lint import MISSING_ENTITY_MIN_REFS, missing_entities
from .pages import link_resolution_index
from .paths import require_kb_root
from .runtime import AgentRunner

__all__ = [
    "HealWorkItem",
    "HealReceipt",
    "HealResult",
    "compute_worklist",
    "positive_int",
    "run_heal",
    "heal_entrypoint",
    "main",
]


def positive_int(value: str) -> int:
    """argparse 类型：仅接受 ≥ 1 的整数（`--limit`/`--min-refs` 用，挡 0/负值的静默无操作）。"""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须是整数：{value}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"必须是 ≥ 1 的整数：{value}")
    return n

# 默认每批物化上限（§10 风险：批越大上下文越杂、质量越降，故起步保守）。超额项报告为"本次推迟"。
DEFAULT_LIMIT = 10

# 薄 prompt：真正步骤在 skill。{targets} = 逐目标的"名 + 引用页清单"。
HEAL_PROMPT = (
    "请按 `guanlan-wiki` skill 的 heal 工作流，为下列**缺失实体**各物化一页 entity 定义。"
    "目标名已是归一键，**直接作文件名** `wiki/entities/<目标>.md`：\n{targets}\n"
    "对每个目标：**只读所列引用页**与 `wiki/index.md` 建上下文；若上下文足以确认其为实体，"
    "在 `wiki/entities/<目标>.md` 合成一页 entity 定义（frontmatter 齐全、正文术语转 `[[wikilink]]`）；"
    "向 `log.md` 追加一条 `## [<日期>] heal | <目标>`。"
    "若上下文不足、目标更像概念/主题页、或疑似已有页别名，**跳过该目标**并用一句话说明（无需特定格式）。"
    "只准从引用上下文合成，**不臆造引用页里没有的事实**；`sources` 列引用页有出处可顺延，否则留空。"
    "**永不修改 `raw/`、不新建 `raw/` 资料、不覆盖或删除已有页、不运行 shell 命令或 `guanlan check`；"
    "读写文件只用内置文件工具。** 完成后用一两句说明建了哪些页、跳过了哪些。"
)


@dataclass(frozen=True)
class HealWorkItem:
    """worklist 一项。`target` 是归一断链键（== 新页 stem，决策P3.2-14）。"""

    target: str
    ref_count: int
    ref_pages: tuple[str, ...]
    postponed: bool = False  # 因 --limit 推迟（本批不处理，dry-run/预览仍报出）


@dataclass(frozen=True)
class HealReceipt:
    """逐目标回执，status **纯由写后重算 graph 判定**（决策P3.2-12，与 agent 输出无关）。"""

    target: str
    status: Literal["resolved", "still_broken"]  # v1 仅图判定二值；未来可扩 "skipped"
    resolved_to: str | None  # 解析到的页路径（仅 resolved 必有）
    created_path: str | None  # 本轮新建且解析了该目标的页；未知/未建则 None
    reason: str


@dataclass(frozen=True)
class HealResult:
    """批次级机器契约（CLI/未来 Web 共用）；人读输出只是它的渲染。"""

    receipts: tuple[HealReceipt, ...] = ()
    unexpected_writes: tuple[str, ...] = ()  # 有害写路径（删/改现有页），批次级、非逐目标
    changed_paths: tuple[str, ...] = ()  # 本轮 wiki/ 写集 diff 全量（增/删/改）
    exit_code: int = EXIT_OK


# ── §2 worklist（确定性，零 LLM）────────────────────────────────────────────


def compute_worklist(
    wiki: Path, *, min_refs: int = MISSING_ENTITY_MIN_REFS, limit: int = DEFAULT_LIMIT
) -> list[HealWorkItem]:
    """算 heal worklist：复用 `lint.missing_entities`，按 (引用数降序, target 升序) 排序，
    前 `limit` 项 `postponed=False`（本批执行）、其余 `postponed=True`（推迟，不静默丢弃）。"""
    items = missing_entities(Path(wiki), min_refs=min_refs)
    items.sort(key=lambda m: (-m.ref_count, m.target))
    return [
        HealWorkItem(
            target=m.target,
            ref_count=m.ref_count,
            ref_pages=m.ref_pages,
            postponed=i >= limit,
        )
        for i, m in enumerate(items)
    ]


def _format_targets(batch: list[HealWorkItem]) -> str:
    """渲染喂给 skill 的逐目标"名 + 引用页清单"（确定性事实，决策P3.2-2）。"""
    return "\n".join(
        f"- {w.target}（被 {w.ref_count} 页引用：{', '.join(w.ref_pages)}）" for w in batch
    )


# ── §3/§5 写集审计 + 写后回执（零 LLM）──────────────────────────────────────


def _snapshot_wiki(root: Path) -> dict[str, str]:
    """{wiki/... posix 路径: 指纹}，用于写集 before/after diff（决策P3.2-11）。

    指纹：普通文件取内容 sha256；**符号链接按链接目标指纹、不跟随到内容**——否则把一张真实页
    换成指向同字节文件的符号链接会被漏判（与 `gate.snapshot_raw` 同口径的防绕过）。
    """
    out: dict[str, str] = {}
    for p in sorted((Path(root) / "wiki").rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            try:  # readlink 可能在并发删除 / 权限下抛 OSError——容错不崩（同 gate._special_fingerprint）。
                out[rel] = f"<symlink:{os.readlink(p)}>"
            except OSError:
                out[rel] = "<symlink:?>"
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _read_log(root: Path) -> str:
    """读 `wiki/log.md` 文本（不存在则空串），供 append-only 校验。"""
    log = Path(root) / "wiki" / "log.md"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


# heal 的允许写入面：仅新建 `wiki/entities/` 下的页 + 追加 `wiki/log.md`（决策P3.2-11）。
_ENTITIES_PREFIX = "wiki/entities/"
_LOG_PATH = "wiki/log.md"


def _writeset(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    """返回 `(changed_paths, unexpected_writes)`，复用 `gate.diff_raw` 的增/删/改分类。

    `changed_paths` = 增/删/改全量；`unexpected_writes` 收**越界写**（决策P3.2-11）：
    - **删除**任何页；
    - **修改/覆盖已有**页（`wiki/log.md` 在此豁免，其追加的合法性另由 append-only 校验把关）；
    - **新建**到 `wiki/entities/` 之外的页（heal 只该建 entity 页，建错目录即越界）。
    """
    changed: list[str] = []
    unexpected: list[str] = []
    for c in diff_raw(before, after):
        changed.append(c.path)
        if c.kind == "removed":
            unexpected.append(c.path)
        elif c.kind == "modified" and c.path != _LOG_PATH:
            unexpected.append(c.path)
        elif c.kind == "added" and not c.path.startswith(_ENTITIES_PREFIX):
            unexpected.append(c.path)
    return sorted(changed), sorted(unexpected)


def _build_receipts(
    wiki: Path, batch: list[HealWorkItem], added: set[str], *, gate_ok: bool
) -> list[HealReceipt]:
    """写后判每个目标是否真解析（§5，零 LLM）。

    `resolved` 的判据是「`[[target]]` **现在指向一张真实页**」——即 `target`（已是 `link_stem` 归一键）
    命中写后的 `link_resolution_index`（stem ∪ 别名 → 页路径，决策P3.1-6）。这比「无断链边」更准：
    若引用页恰被删致断链边消失却无页可指，`resolved_to` 仍为 None、判 `still_broken`。

    **门禁未过（`gate_ok=False`）时一律 `still_broken`**：写入未干净落地（改动留盘待修），此时哪怕
    磁盘上躺着一张 frontmatter 非法的同名页、`link_resolution_index` 按 stem 也会"命中"它，不能据此
    报 resolved——整体成败以退出码为准（§5），逐目标只在干净写入后才声明解析。
    """
    resolution = link_resolution_index(Path(wiki)) if gate_ok else {}
    receipts: list[HealReceipt] = []
    for w in batch:
        resolved_to = resolution.get(w.target)
        if resolved_to is not None:
            receipts.append(
                HealReceipt(
                    target=w.target,
                    status="resolved",
                    resolved_to=resolved_to,
                    created_path=resolved_to if resolved_to in added else None,
                    reason="断链已消除",
                )
            )
        else:
            receipts.append(
                HealReceipt(
                    target=w.target,
                    status="still_broken",
                    resolved_to=None,
                    created_path=None,
                    reason="门禁未过，改动留盘待修" if not gate_ok else "未建页 / 命名不符 / 被跳过",
                )
            )
    return receipts


# ── 编排 + 渲染 ─────────────────────────────────────────────────────────────


def run_heal(
    *,
    root: str | Path = ".",
    limit: int = DEFAULT_LIMIT,
    min_refs: int = MISSING_ENTITY_MIN_REFS,
    model: str | None = None,
    dry_run: bool = False,
    json_output: bool = False,
    runner: AgentRunner | None = None,
) -> int:
    """物化缺失实体，返回退出码（见 errors.py）。"""
    try:
        kb = require_kb_root(root, writable=True)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    wiki = kb / "wiki"
    worklist = compute_worklist(wiki, min_refs=min_refs, limit=limit)
    batch = [w for w in worklist if not w.postponed]
    postponed = [w for w in worklist if w.postponed]

    # dry-run / 空 worklist：纯读、零 LLM、不触 Agentao、必 EXIT_OK（决策P3.2-5/6）。
    if dry_run:
        _print_preview(batch, postponed, json_output=json_output)
        return EXIT_OK
    if not batch:
        if json_output:
            print(_dumps({"worklist": [], "postponed": [_item_dict(w) for w in postponed]}))
        elif postponed:
            # 有缺失实体、却因 --limit 全数推迟：别误报"无可物化"（决策P3.2-1：有界即声明）。
            print(
                f"· --limit={limit} 下本批未物化任何目标；{len(postponed)} 个高频缺失实体全部推迟"
                "（提高 --limit 续补，或 --dry-run 预览）。"
            )
        else:
            print("✓ 无可物化的缺失实体（图已充分连通或均低于阈值）。")
        return EXIT_OK

    # 写路径（§3）：wiki 写集基线 → 写门禁结果版（不打印）→ 重算 graph 回执。
    before = _snapshot_wiki(kb)
    before_log = _read_log(kb)
    write = run_guarded_write_result(
        kb, HEAL_PROMPT.format(targets=_format_targets(batch)), model=model, runner=runner
    )
    after = _snapshot_wiki(kb)
    changed, unexpected = _writeset(before, after)
    # log.md 唯一合法写 = **仍是普通文件且 append-only 追加**；任何别的改动（截断/改写历史、或被换成
    # 符号链接）都算有害（决策P3.2-11）。注意 append-only 检查会读穿符号链接，故先排除符号链接替换。
    if _LOG_PATH in changed:
        still_plain_file = not after.get(_LOG_PATH, "").startswith("<symlink:")
        if not (still_plain_file and _read_log(kb).startswith(before_log)):
            unexpected = sorted(set(unexpected) | {_LOG_PATH})
    added = set(after.keys() - before.keys())
    receipts = _build_receipts(wiki, batch, added, gate_ok=write.gate.ok)

    result = HealResult(
        receipts=tuple(receipts),
        unexpected_writes=tuple(unexpected),
        changed_paths=tuple(changed),
        exit_code=write.exit_code,
    )
    if json_output:
        print(_dumps(_result_dict(result, postponed)))
    else:
        _print_human(write, result, postponed)
    return result.exit_code


def heal_entrypoint(
    root_dir: str | Path,
    *,
    limit: int,
    min_refs: int,
    model: str | None,
    dry_run: bool,
    json_output: bool,
) -> int:
    """`guanlan heal` 的单一落地（CLI 与 `python -m guanlan.heal` 共用）。"""
    return run_heal(
        root=root_dir,
        limit=limit,
        min_refs=min_refs,
        model=model,
        dry_run=dry_run,
        json_output=json_output,
    )


# ── 渲染辅助 ───────────────────────────────────────────────────────────────


def _dumps(obj: object) -> str:
    """与仓内 `report_json` 同风格：UTF-8 原样、缩进 2、无尾换行。"""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _item_dict(w: HealWorkItem) -> dict:
    return {"target": w.target, "ref_count": w.ref_count, "ref_pages": list(w.ref_pages)}


def _result_dict(result: HealResult, postponed: list[HealWorkItem]) -> dict:
    return {
        "worklist": [{"target": r.target} for r in result.receipts],
        "postponed": [_item_dict(w) for w in postponed],
        "receipts": [dataclasses.asdict(r) for r in result.receipts],
        "unexpected_writes": list(result.unexpected_writes),
        "changed_paths": list(result.changed_paths),
        "exit_code": result.exit_code,
    }


def _print_preview(
    batch: list[HealWorkItem], postponed: list[HealWorkItem], *, json_output: bool
) -> None:
    if json_output:
        print(
            _dumps(
                {
                    "worklist": [_item_dict(w) for w in batch],
                    "postponed": [_item_dict(w) for w in postponed],
                }
            )
        )
        return
    if not batch and not postponed:
        print("✓ 无可物化的缺失实体（图已充分连通或均低于阈值）。")
        return
    print(f"· heal 预览（dry-run，零 LLM）：本批将物化 {len(batch)} 个高频缺失实体：")
    for w in batch:
        print(f"    + {w.target}（{w.ref_count} 页引用：{', '.join(w.ref_pages)}）")
    if postponed:
        print(f"  另有 {len(postponed)} 个因 --limit 本次推迟（重跑续补）：")
        for w in postponed:
            print(f"    · {w.target}（{w.ref_count} 页引用）")


def _print_human(write, result: HealResult, postponed: list[HealWorkItem]) -> None:
    """自渲染人读报告（不走 gate 的 report_outcome）。"""
    if write.final_text:
        print(write.final_text)
    n = len(result.receipts)
    resolved = [r for r in result.receipts if r.status == "resolved"]
    still = [r for r in result.receipts if r.status == "still_broken"]
    print(
        f"· heal 物化 {n} 个目标：{len(resolved)} 个已解析，{len(still)} 个仍断"
        + (f"，{len(postponed)} 个推迟" if postponed else "")
        + (f"，{len(result.unexpected_writes)} 处非预期写" if result.unexpected_writes else "")
        + "。"
    )
    for r in resolved:
        print(f"    ✓ {r.target} → {r.resolved_to}")
    for r in still:
        print(f"    · {r.target}（仍断：{r.reason}）")
    if result.unexpected_writes:
        print("  ⚠ 非预期 wiki 写入（人工审计）：")
        for p in result.unexpected_writes:
            print(f"    ! {p}")
    if postponed:
        print(f"  另有 {len(postponed)} 个缺失实体因 --limit 推迟，重跑续补。")


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.heal` 入口（与 `guanlan heal` 共享 heal_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.heal",
        description="缺失实体物化：把高频断链按需 LLM 建成 entity 页（走 P2 写门禁）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument(
        "--limit", type=positive_int, default=DEFAULT_LIMIT, help=f"本批上限（默认 {DEFAULT_LIMIT}，须 ≥ 1）"
    )
    parser.add_argument(
        "--min-refs",
        type=positive_int,
        default=MISSING_ENTITY_MIN_REFS,
        help="入选阈值（默认对齐 lint，须 ≥ 1）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印 worklist，不写、不触 LLM")
    parser.add_argument("--model", default=None, help="覆盖 Agentao 模型")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON")
    args = parser.parse_args(argv)
    return heal_entrypoint(
        args.dir,
        limit=args.limit,
        min_refs=args.min_refs,
        model=args.model,
        dry_run=args.dry_run,
        json_output=args.json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
