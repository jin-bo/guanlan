"""缺失实体物化 heal（P3.2，见 docs/P3.2-缺失实体物化.md）。

`guanlan heal`：把 `lint.missing_entity`（被 ≥ 阈值张不同页引用却无页的高价值断链）按需用 LLM
物化成内容页——按目标的实体/概念属性落 `wiki/entities/`（人物/组织/产品/系统）或 `wiki/concepts/`
（方法/理论/术语/战法等）。**检测确定性、生成有门禁、默认非自动、按需且有界。**

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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import EXIT_OK, GuanlanError
from .gate import diff_raw, run_guarded_write_result
from .lint import MISSING_ENTITY_MIN_REFS, missing_entities
from .pages import link_resolution_index, link_stem, load_page_text
from .paths import require_kb_root
from .runtime import AgentRunner

__all__ = [
    "HealWorkItem",
    "HealReceipt",
    "HealResult",
    "HealRun",
    "compute_worklist",
    "heal_result_dict",
    "positive_int",
    "run_heal",
    "run_heal_result",
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

# 薄 prompt：真正步骤（判据/收编纪律）在 skill。{targets} = 逐目标的"名 + 引用页清单"。
# P3.3：从「一律直接作文件名」补三态引导（A 用目标名 / B 规范标题+收编 / C 收编既有页）+ index 登记。
# 概念分类：每目标先判实体/概念，A/B 落到对应目录 `entities/` 或 `concepts/`（战法等方法类是概念）。
HEAL_PROMPT = (
    "请按 `guanlan-wiki` skill 的 heal 工作流，为下列**缺失实体**收割断链（目标名已是归一键）：\n{targets}\n"
    "**只读所列引用页**与 `wiki/index.md` 建上下文。每个目标先判**实体还是概念**"
    "（实体=人物/组织/产品/系统，落 `wiki/entities/`；概念=方法/理论/术语/战法等，落 `wiki/concepts/`；"
    "拿不准当实体），记其目录为 `<dir>`，再三选一（判据见 skill，**拿不准走 A**）："
    "A) 直接建 `wiki/<dir>/<目标>.md`（目标名作文件名，`[[原引用]]` 天然解析）；"
    "B) 若引用上下文给出更规范全称，建 `wiki/<dir>/<规范名>.md` 并在 frontmatter `aliases` **收编原目标名**；"
    "C) 若目标其实是某**已有** entity/concept 页的变体，**只向该页 `aliases` 末尾追加原目标名**"
    "（不改正文、不动其它 frontmatter、不新建重复页）。"
    "建页/收编后在 `wiki/index.md` 对应分区登记一行（B/C 句末注记别名），并向 `log.md` 追加 `## [<日期>] heal | <目标>`。"
    "只准从引用上下文合成，**不臆造引用页里没有的事实**；`sources` 列引用页有出处可顺延，否则留空。"
    "上下文不足、目标更像主题页、或无法判定时，**跳过该目标**并用一句话说明（无需特定格式）。"
    "**永不修改 `raw/`、不新建 `raw/` 资料、不删除或覆盖重写已有页正文、不运行 shell 命令或 `guanlan check`；"
    "读写文件只用内置文件工具。** 完成后用一两句说明建了/收编了哪些页、跳过了哪些。"
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


@dataclass(frozen=True)
class HealRun:
    """一轮真实 heal 的结构化产物（CLI 渲染 + Web 回执共用的**进程内**对象，决策P4.3-1）。

    `result` 是 P3.2/P3.3 既有批次契约；`postponed` 是因 `--limit` 推迟项（worklist 级，
    `HealResult` 本不含）；`had_batch=False` 表示空 worklist 短路（未触 Agentao，决策P3.2-6），
    供 CLI 薄壳选空批次特判渲染；`final_text` 是 agent 散文（来自 `GuardedWriteResult.final_text`，
    空批次短路时为 `""`）。`final_text`/`had_batch` 只是进程内字段，不进任何 wire 契约
    （Web 的 `result` 由端点经 `heal_result_dict` 产出六字段机器回执，散文另走 `job.output`）。
    """

    result: HealResult
    postponed: tuple[HealWorkItem, ...] = ()
    had_batch: bool = False
    final_text: str = ""

    @property
    def exit_code(self) -> int:
        return self.result.exit_code


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


@dataclass(frozen=True)
class _PageMeta:
    """普通内容页的**解析字段**，仅供 `_is_safe_alias_collection` 判「安全别名收编」（P3.3 §3）。

    不参与 `diff_raw` / `changed_paths`（那只看 `_PageSnapshot.raw` 原始指纹，口径不退化）。
    `aliases` 存**有序原始字符串**（非集合、非归一集）以做前缀校验；`aliases_valid` 标记形态合法
    （缺失或字符串列表 → True；非列表/含非字符串项 → False）。
    """

    body_fp: str  # 正文 sha256
    fm_fp: str  # frontmatter 除 {aliases,last_updated} 的规范化指纹
    aliases_valid: bool  # aliases 形态合法（缺失/字符串列表）
    aliases: tuple[str, ...]  # 有序原始别名字符串（aliases_valid 时有意义；非法则为空）


@dataclass(frozen=True)
class _PageSnapshot:
    """每页写集快照的**双层**值（P3.3 §3）：`raw` 驱动 diff/changed_paths，`meta` 仅供豁免判定。"""

    raw: str  # 原始指纹：普通文件 sha256 / 符号链接目标（同 gate.snapshot_raw 防绕过口径）
    meta: _PageMeta | None  # 仅 entities/concepts 普通文件解析；symlink/其它 → None（不豁免，Finding 3）


def _fm_fingerprint(meta: dict | None) -> str:
    """frontmatter 除 `aliases`/`last_updated` 外字段的规范化指纹（title/type/tags/sources/… 任一变即变）。

    键一律 `str()` 归一后再 `sort_keys`：YAML 容许非字符串/混合类型键（`2026: x`、`true: x`、`null: x`——
    `load_page` 容错档原样返回），不归一会让 `json.dumps(sort_keys=True)` 抛 `TypeError`、破坏「审计不因
    坏数据中断」（决策P3-8）。归一只用于本指纹比对，不回写页面。
    """
    if not isinstance(meta, dict):
        return "<no-meta>"
    subset = {str(k): v for k, v in meta.items() if k not in ("aliases", "last_updated")}
    payload = json.dumps(subset, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _aliases_info(meta: dict | None) -> tuple[bool, tuple[str, ...]]:
    """读 `aliases`：返回 `(形态合法, 有序原始字符串)`。缺失=合法空；字符串列表=合法；其余=非法。"""
    if not isinstance(meta, dict) or "aliases" not in meta:
        return True, ()
    raw = meta["aliases"]
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        return True, tuple(raw)
    return False, ()


def _page_meta_from_text(text: str) -> _PageMeta:
    """从**已读入文本**解析 `_PageMeta`（容错档 `load_page_text`，绝不抛；避免二次 I/O）。"""
    meta, body = load_page_text(text)
    valid, aliases = _aliases_info(meta)
    return _PageMeta(
        body_fp=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        fm_fp=_fm_fingerprint(meta),
        aliases_valid=valid,
        aliases=aliases,
    )


def _snapshot_wiki(root: Path) -> dict[str, _PageSnapshot]:
    """{wiki/... posix 路径: `_PageSnapshot`(原始指纹 + 解析字段)}，用于写集 before/after diff。

    - **原始指纹**（`.raw`）：普通文件内容 sha256；**符号链接按链接目标指纹、不跟随到内容**——否则把
      一张真实页换成指向同字节文件的符号链接会被漏判（与 `gate.snapshot_raw` 同口径的防绕过）。
    - **解析字段**（`.meta`）：**仅 `entities/`/`concepts/` 下的普通文件**才算——`_writeset` 只对这些路径查
      豁免（§3），故 config 页 / 其它目录 / symlink 一律 `meta=None`，省去全库无谓解析。每个普通文件**只读
      一次字节**：sha256 取原始指纹，再从同一份字节解析 meta（不二次 I/O）。
    """
    out: dict[str, _PageSnapshot] = {}
    for p in sorted((Path(root) / "wiki").rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            try:  # readlink 可能在并发删除 / 权限下抛 OSError——容错不崩（同 gate._special_fingerprint）。
                raw = f"<symlink:{os.readlink(p)}>"
            except OSError:
                raw = "<symlink:?>"
            out[rel] = _PageSnapshot(raw=raw, meta=None)
        elif p.is_file():
            data = p.read_bytes()
            meta = (
                _page_meta_from_text(data.decode("utf-8", errors="replace"))
                if rel.startswith(_CONTENT_PREFIXES)
                else None  # 只有内容页参与安全收编豁免；其余无需解析
            )
            out[rel] = _PageSnapshot(raw=hashlib.sha256(data).hexdigest(), meta=meta)
    return out


def _read_log(root: Path) -> str:
    """读 `wiki/log.md` 文本（不存在则空串），供 append-only 校验。"""
    log = Path(root) / "wiki" / "log.md"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


# heal 的允许写入面（决策P3.2-11 / P3.3-4/5）：
# - **新建** `wiki/entities/` 或 `wiki/concepts/` 下的页（物化，A/B 模式；按实体/概念分类落目录）；
# - **追加** `wiki/log.md`（严格 append-only，另由 run_heal 复查）；
# - **编辑** `wiki/index.md`（config catalog，分区插行，不套 append-only）；
# - **向已有 `entities/`/`concepts/` 页纯追加别名收编本批目标**（C 模式，§3 安全收编窄缝）。
_CONTENT_PREFIXES = ("wiki/entities/", "wiki/concepts/")
_LOG_PATH = "wiki/log.md"
_INDEX_PATH = "wiki/index.md"


def _is_safe_alias_collection(
    before: _PageMeta | None, after: _PageMeta | None, work_targets: set[str]
) -> bool:
    """对一处「修改已有内容页」判是否为**安全别名收编**（P3.3 §3 决策P3.3-5，纯 final-state、零 LLM）。

    当且仅当全部成立才豁免（条件 4「过门禁」由 `_writeset` 的 `gate_ok` 把关，不在此判）：
    - 两侧均**普通文件**（任一侧 symlink/special → meta=None → 不豁免，Finding 3）；
    - 正文 body 与其余 frontmatter（除 aliases/last_updated）**不变**；
    - before/after 的 aliases 形态均合法（缺失或字符串列表）；
    - before 原始别名列表是 after 的**前缀**（原序原次保留、只尾部追加，挡删值/重排，Finding 1/2）；
    - 新增别名**非空**且其归一键**全部** ∈ 本批 `work_targets`（挡 piggyback，Finding 2）。
    """
    if before is None or after is None:
        return False
    if before.body_fp != after.body_fp or before.fm_fp != after.fm_fp:
        return False
    if not (before.aliases_valid and after.aliases_valid):
        return False
    n = len(before.aliases)
    if after.aliases[:n] != before.aliases:  # 必须是前缀：不删、不重排
        return False
    new = after.aliases[n:]
    if not new:  # 无新增 → 非收编
        return False
    new_keys = [link_stem(a) for a in new]
    if not all(new_keys):  # 归一后空键不算有效收编
        return False
    return all(k in work_targets for k in new_keys)


def _writeset(
    before: dict[str, _PageSnapshot],
    after: dict[str, _PageSnapshot],
    work_targets: set[str],
    *,
    gate_ok: bool,
) -> tuple[list[str], list[str]]:
    """返回 `(changed_paths, unexpected_writes)`，复用 `gate.diff_raw`（只比 `.raw` 原始指纹）。

    `changed_paths` = 增/删/改全量（任何字节变化都进，含别名重排，口径不退化）；
    `unexpected_writes` 收**越界写**：
    - **删除**任何页；
    - **修改**已有页——`wiki/log.md`/`wiki/index.md`（config catalog）豁免；`entities/`/`concepts/` 页
      若为**安全别名收编**（`_is_safe_alias_collection`）且 `gate_ok` 也豁免（§3）；其余皆越界；
    - **新建**到 `wiki/entities/`∪`wiki/concepts/` 之外的页（heal 只该建实体/概念页，建错目录即越界）。
    """
    raw_before = {k: v.raw for k, v in before.items()}
    raw_after = {k: v.raw for k, v in after.items()}
    changed: list[str] = []
    unexpected: list[str] = []
    for c in diff_raw(raw_before, raw_after):
        changed.append(c.path)
        if c.kind == "removed":
            unexpected.append(c.path)
        elif c.kind == "modified":
            if c.path in (_LOG_PATH, _INDEX_PATH):
                continue  # config catalog：log.md 另由 append-only 复查；index.md 自由编辑
            if (
                gate_ok
                and c.path.startswith(_CONTENT_PREFIXES)
                and _is_safe_alias_collection(before[c.path].meta, after[c.path].meta, work_targets)
            ):
                continue  # 安全别名收编（§3），豁免
            unexpected.append(c.path)
        elif c.kind == "added" and not c.path.startswith(_CONTENT_PREFIXES):
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


def run_heal_result(
    *,
    root: str | Path = ".",
    limit: int = DEFAULT_LIMIT,
    min_refs: int = MISSING_ENTITY_MIN_REFS,
    targets: Sequence[str] | None = None,
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> HealRun:
    """heal 真实写路径的**不打印**编排核心（仿 P3.2 决策P3.2-13 的 `run_guarded_write_result`）。

    `require_kb_root` → `compute_worklist` → 空 batch 短路（`had_batch=False`、EXIT_OK，未触
    Agentao）/（`snapshot` → `run_guarded_write_result` → `_writeset` → log 复查 → `_build_receipts`
    → `HealResult`）。**不碰 stdout、不处理 dry_run/json_output**——渲染与短路特判全留给调用方
    （CLI 薄壳 `run_heal` / Web 端点）。`require_kb_root` 的 `GuanlanError` 向上抛：CLI 薄壳
    try/except 打印，作业 worker 归一为失败（决策P4.3-1/-5）。

    `targets`（可选，Web 勾选子集用，决策P4.3-3 修订）：**仅作过滤器**——worklist 仍服务端
    `compute_worklist` 确定性重算，再**取交集**只物化其中 `target` 命中者；故绝不物化服务端没独立
    推出的目标（防 TOCTOU），客户端发来的陈旧/越界目标自然被交集丢弃。`None` = 不过滤（CLI 常路、
    与旧行为逐字节一致）；交集为空 → 同空 batch 短路。`limit` 仍先界定可选范围（推迟项无勾选框）。
    """
    kb = require_kb_root(root, writable=True)
    wiki = kb / "wiki"
    worklist = compute_worklist(wiki, min_refs=min_refs, limit=limit)
    batch = [w for w in worklist if not w.postponed]
    if targets is not None:  # 勾选子集：与服务端重算的本批取交集（保留确定性重算这一安全属性）
        selected = set(targets)
        batch = [w for w in batch if w.target in selected]
    postponed = tuple(w for w in worklist if w.postponed)

    # 空 worklist：短路 EXIT_OK、不触 Agentao（决策P3.2-6）；had_batch=False 供薄壳特判渲染。
    if not batch:
        return HealRun(result=HealResult(), postponed=postponed, had_batch=False)

    # 写路径（§3）：wiki 写集基线 → 写门禁结果版（不打印）→ 重算 graph 回执。
    before = _snapshot_wiki(kb)
    before_log = _read_log(kb)
    write = run_guarded_write_result(
        kb, HEAL_PROMPT.format(targets=_format_targets(batch)), model=model, runner=runner
    )
    after = _snapshot_wiki(kb)
    work_targets = {w.target for w in batch}
    changed, unexpected = _writeset(before, after, work_targets, gate_ok=write.gate.ok)
    # log.md 唯一合法写 = **仍是普通文件且 append-only 追加**；任何别的改动（截断/改写历史、或被换成
    # 符号链接）都算有害（决策P3.2-11）。注意 append-only 检查会读穿符号链接，故先排除符号链接替换。
    if _LOG_PATH in changed:
        after_log = after.get(_LOG_PATH)
        still_plain_file = after_log is not None and not after_log.raw.startswith("<symlink:")
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
    return HealRun(
        result=result, postponed=postponed, had_batch=True, final_text=write.final_text
    )


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
    """物化缺失实体，返回退出码（见 errors.py）。

    薄壳（决策P4.3-1）：dry-run 纯读预览不进 core；真实写路径调 `run_heal_result`（不打印），
    再据 `had_batch` 选**空批次特判**或**真实 heal** 渲染——CLI 行为/字节与重构前一致
    （含 dry-run / 空 worklist 的 2 键 `--json` 与人读消息逐字节不变）。
    """
    # dry-run：纯读、零 LLM、不触 Agentao、必 EXIT_OK（决策P3.2-5）——不进 core（职责更清）。
    if dry_run:
        try:
            kb = require_kb_root(root, writable=True)
        except GuanlanError as exc:
            print(exc, file=sys.stderr)
            return exc.exit_code
        worklist = compute_worklist(kb / "wiki", min_refs=min_refs, limit=limit)
        batch = [w for w in worklist if not w.postponed]
        postponed = [w for w in worklist if w.postponed]
        _print_preview(batch, postponed, json_output=json_output)
        return EXIT_OK

    try:
        run = run_heal_result(
            root=root, limit=limit, min_refs=min_refs, model=model, runner=runner
        )
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    postponed = list(run.postponed)
    # 空 worklist：保留现有特判（**勿**走六字段渲染——会把 --json 从 2 键涨到 6 键，破坏字节不变）。
    if not run.had_batch:
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
        return run.exit_code

    if json_output:
        print(_dumps(heal_result_dict(run.result, postponed)))
    else:
        _print_human(run.final_text, run.result, postponed)
    return run.exit_code


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


def heal_result_dict(result: HealResult, postponed: Sequence[HealWorkItem]) -> dict:
    """heal 批次回执的六字段 wire 契约（CLI `--json` 与 Web `/api/jobs` 的 `result` 共用，决策P4.3-1）。

    纯重命名自原私有 `_result_dict`、不改结构/字段（六字段不增不减），只把既定契约显式化、
    供 `web/app.py` 复用而无需跨模块调私有 helper。`postponed` 容忍 list / tuple（仅迭代）。
    """
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


def _print_human(final_text: str, result: HealResult, postponed: list[HealWorkItem]) -> None:
    """自渲染人读报告（不走 gate 的 report_outcome）。"""
    if final_text:
        print(final_text)
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
        description="缺失实体物化：把高频断链按需 LLM 建成实体/概念页（走 P2 写门禁）。",
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
