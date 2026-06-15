"""源撤回 remove（P3.9，见 docs/P3.9-源撤回.md）。**零 LLM、人发起的宿主确定性写。**

`guanlan remove <源>`：把一个误摄 / 已撤稿源的"自身落盘物"（`raw/<slug>.md` + 可选
`raw/images/<slug>/` 图片、`wiki/sources/<slug>.md` 摘要页）**整体移入回收区
`<kb>/.trash/<slug>@ts>/` 而非 `rm`**（gbrain 软删形状 + `manifest.json` 留档，给反悔窗），
多源衍生页里的 `<slug>` 引用**确定性摘除**（provenance 编辑、正文一字不改），独源衍生页 /
撤回后留下的悬链**只报 advisory、不删不改**。

一期表面积刻意小（决策P3.9-10，像 `reindex`）：

- **默认预览、显式 `--yes` 才写**；无 `restore`/`--purge`/`--cascade`（后续项）。
- **人发起的宿主确定性写**：不起 Agentao、不经 `run_guarded_write` 快照门禁（同 `convert`
  纯宿主写 `raw/`，是 `raw/` 的**合法删者**）；不写 `log.md`（manifest 即审计轨迹）。
- **不承诺事务回滚**：manifest-先写 + 幂等 + **移源放最后**（源文件是 locate 的锚，放最后
  使中断后重跑可向前收敛）；失败报 partial。
- 复用既有归口（防口径漂移）：`gate._trusted_sources` 读 `sources`、`pages.iter_pages` 反扫、
  `reindex._prune_dangling` 删 index 行（同 `convert` 复用 `imageio` 私有原语的跨模块先例）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .gate import _trusted_sources  # 读衍生页可信 sources 的单一归口（坏/缺 → None）
from .pages import iter_pages, load_page, split_frontmatter
from .paths import require_kb_root
from .rawio import normalize_basename, raw_slug  # slug 归一：与投喂 / convert 建源同口径
from .reindex import _join_lines, _prune_dangling, _split_lines  # index 行删除的单一归口

__all__ = [
    "DropSlug",
    "RemovePlan",
    "run_remove_result",
    "format_plan",
    "run_remove",
    "remove_entrypoint",
    "main",
]


@dataclass(frozen=True)
class DropSlug:
    """一条多源页的 slug 摘除：`page` 相对库根 posix，`before_sources` 摘除**前**的整张 sources。

    记 `before_sources`（而非仅 dropped slug）：既供人工 / 二期 restore union 并回，又让二期
    restore 能比对页现 `sources` 是否已被改（冲突检测），故一期就把字段定准（决策P3.9-2，发现2）。
    """

    page: str
    before_sources: list[str]


@dataclass(frozen=True)
class RemovePlan:
    """一次撤回的纯计算结果（只读、不写盘）。`ok=False` 仅表示"源自身落盘物全缺"。"""

    ok: bool
    slug: str
    relocate: list[str]  # 待移入 .trash/ 的源自身落盘物（相对库根 posix，存在者才入列）
    drop_slug: list[DropSlug]  # 多源页待摘 slug
    orphans: list[str]  # 独源衍生页（advisory，一期不删；相对库根 posix）
    index_lines: list[str]  # 待删的 index.md 登记行原文（通常 0/1 条）


def _resolve_slug(src: str) -> str:
    """把 `<源>` 三种写法归一到 slug：`<slug>` / `raw/<slug>.md` / `wiki/sources/<slug>.md`。

    **复用 `rawio` 的 slug 归口**（决策P3.9 §5）——`normalize_basename`（NFKC + 混淆字符折叠 +
    剥目录成分，防 `../` 穿越）→ 剥 `.md` → `raw_slug`（同 `safe_raw_target` 的 slug 收敛）。
    必须与投喂 / `convert` **建源时**的 slug 同口径，否则全角 / 混淆字符标题建的源（落
    `raw/<折叠后>.md`）会 `remove` 不到。归一后为空 → `ValueError`（命令壳映射 `EXIT_USAGE`）。
    """
    raw = src.strip()
    if not raw:
        raise ValueError("源标识不能为空。")
    name = normalize_basename(raw)  # NFKC + 混淆字符折叠 + 剥目录成分（防穿越）
    if name.lower().endswith(".md"):
        name = name[:-3]
    slug = raw_slug(name)  # 与 safe_raw_target 同款 slug 收敛
    if not slug:
        raise ValueError(f"无法从 {src!r} 解析出合法源 slug。")
    return slug


def _locate(root: Path, slug: str) -> list[str]:
    """探源自身落盘物（raw 文件 / 图片目录 / 摘要页），返回存在者的相对库根 posix 列表。"""
    found: list[str] = []
    for rel in (f"raw/{slug}.md", f"raw/images/{slug}", f"wiki/sources/{slug}.md"):
        if (root / rel).exists():
            found.append(rel)
    return found


def _scan_derivatives(root: Path, slug: str) -> tuple[list[DropSlug], list[str]]:
    """反扫 wiki/ 内容页，按 `sources` 是否含 slug 分类为"多源页摘除"与"独源孤儿"。

    **跳过摘要页本身**（它随源整体移走、不做 slug 编辑）；坏/缺 frontmatter 的页 `_trusted_sources`
    返回 None、自然跳过。复用 `gate._trusted_sources`，不重写 sources 解析。
    """
    wiki = root / "wiki"
    source_page = wiki / "sources" / f"{slug}.md"
    drops: list[DropSlug] = []
    orphans: list[str] = []
    for page in iter_pages(wiki):
        if page == source_page:
            continue
        meta, _body = load_page(page)
        sources = _trusted_sources(meta)
        if sources is None or slug not in sources:
            continue
        rel = page.relative_to(root).as_posix()
        if sources == frozenset({slug}):
            orphans.append(rel)  # 独源：一期只 advisory、不删（决策P3.9-3）
        else:
            drops.append(DropSlug(page=rel, before_sources=sorted(sources)))
    return drops, orphans


def _index_target(slug: str) -> set[str]:
    """摘要页在 index.md 里的链接目标（相对 wiki/ 的 posix），`_prune_dangling` 的入参归口。"""
    return {f"sources/{slug}.md"}


def _plan_index_lines(wiki: Path, slug: str) -> list[str]:
    """算 index.md 里待删的摘要页登记行（复用 `_prune_dangling`，只读、不写盘）。"""
    index_path = wiki / "index.md"
    if not index_path.is_file():
        return []
    lines, _eol, _trailing = _split_lines(index_path.read_text(encoding="utf-8"))
    _kept, removed = _prune_dangling(lines, _index_target(slug))
    return removed


def run_remove_result(root: Path, slug: str) -> RemovePlan:
    """算一次撤回的 plan。**纯函数、只读、不写盘**（仿 `reindex.run_reindex` / `heal.run_heal_result`）。"""
    root = Path(root)
    relocate = _locate(root, slug)
    if not relocate:  # 源自身落盘物全缺 → 无可撤
        return RemovePlan(ok=False, slug=slug, relocate=[], drop_slug=[], orphans=[], index_lines=[])
    drops, orphans = _scan_derivatives(root, slug)
    index_lines = _plan_index_lines(root / "wiki", slug)
    return RemovePlan(
        ok=True, slug=slug, relocate=relocate, drop_slug=drops, orphans=orphans, index_lines=index_lines
    )


def _drop_slug_from_page(path: Path, slug: str) -> bool:
    """从一页 frontmatter 的 `sources` 摘掉 slug、重写回盘。返回是否改动（幂等：已无 → False）。

    复用 `split_frontmatter` + `yaml.safe_dump`（同 `rawio.apply_origin`，**绝不裸拼**）；**body 逐字
    保留**（`split_frontmatter` 返回原文 body），只重序列化 frontmatter 块。坏/无块/非映射 → 不动。

    非 UTF-8 页（`load_page` 在 plan 期用 `errors='replace'` 容错读、仍可能进 drop 清单）这里**跳过**
    （strict 读会抛 `UnicodeDecodeError`——是 `ValueError` 子类、不被 `run_remove` 的 `except OSError`
    接住）：用 replace 解码后回写会损坏原字节，故不安全重写、留人工，**绝不崩在执行中途**。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    block, body = split_frontmatter(text)
    if block is None:
        return False
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError:
        return False
    if not isinstance(meta, dict):
        return False
    sources = meta.get("sources")
    if not isinstance(sources, list) or slug not in sources:
        return False  # 幂等：重跑时 slug 已不在 → no-op
    meta["sources"] = [s for s in sources if s != slug]
    dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    path.write_text(f"---\n{dumped}---\n{body}", encoding="utf-8")
    return True


def _prune_index_line(index_path: Path, slug: str) -> list[str]:
    """从当前 index.md 删摘要页登记行（幂等：行已删 → 不写）。返回被删行原文。"""
    if not index_path.is_file():
        return []
    lines, eol, trailing = _split_lines(index_path.read_text(encoding="utf-8"))
    kept, removed = _prune_dangling(lines, _index_target(slug))
    if removed:
        index_path.write_text(_join_lines(kept, eol, trailing), encoding="utf-8")
    return removed


def _move_into_trash(src: Path, root: Path, trash_dir: Path) -> bool:
    """把 `src` 移入 `trash_dir` 下保留相对结构的落点。返回是否移动（幂等：源已不在 → False）。"""
    if not src.exists():  # 幂等：上一次 partial 已移走
        return False
    dest = trash_dir / src.relative_to(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return True


def _execute(root: Path, plan: RemovePlan) -> Path:
    """按 manifest-先写 + 幂等 + **移源放最后** 落盘（决策P3.9-10）。返回 trash_dir。

    执行序：① 写 manifest（意图先落） → ② 摘多源页 slug → ③ 删 index 行 → ④ **最后**移源落盘物。
    移源放最后：源文件是 `_locate` 的锚，先移走会让中断后重跑 locate 失败而无法收敛。每步幂等，
    故中途 IO 失败后**重跑同命令向前收敛**（不假装事务回滚）。
    """
    now = datetime.now()
    # 碰撞安全建目录：微秒级时间戳几乎不撞，但若撞（或人为预建）则 exist_ok=True 会把后续
    # `shutil.move` 的目录套嵌（`images/<slug>/<slug>`），故用 exist_ok=False + 序号兜底，保证全新目录。
    base = root / ".trash" / f"{plan.slug}@{now.strftime('%Y%m%dT%H%M%S_%f')}"
    (root / ".trash").mkdir(parents=True, exist_ok=True)
    trash_dir, n = base, 1
    while True:
        try:
            trash_dir.mkdir()
            break
        except FileExistsError:
            trash_dir = base.with_name(f"{base.name}-{n}")
            n += 1

    # ① manifest 先写（纯记录：审计 + 人工恢复配方 + 二期 restore 输入）
    manifest = {
        "slug": plan.slug,
        "removed_at": now.isoformat(timespec="seconds"),
        "moved": list(plan.relocate),
        "slug_dropped_from": [
            {"page": d.page, "dropped_slug": plan.slug, "before_sources": list(d.before_sources)}
            for d in plan.drop_slug
        ],
        "orphaned": list(plan.orphans),  # 独源孤儿（未改动）——记入审计/blast-radius，非恢复所需
        "index_lines_removed": list(plan.index_lines),
    }
    (trash_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # ② 摘多源页 slug（幂等）→ ③ 删 index 行（从当前文件重算，幂等）→ ④ 最后移源
    for d in plan.drop_slug:
        _drop_slug_from_page(root / d.page, plan.slug)
    _prune_index_line(root / "wiki" / "index.md", plan.slug)
    for rel in plan.relocate:
        _move_into_trash(root / rel, root, trash_dir)
    return trash_dir


def format_plan(plan: RemovePlan, *, executed: bool, json_output: bool) -> str:
    """渲染回执：`--json` 走稳定契约；否则人类可读 worklist。"""
    if json_output:
        return json.dumps(
            {
                "ok": plan.ok,
                "slug": plan.slug,
                "executed": executed,
                "relocate": list(plan.relocate),
                "drop_slug": [asdict(d) for d in plan.drop_slug],
                "orphans": list(plan.orphans),
                "index_lines": list(plan.index_lines),
            },
            ensure_ascii=False,
            indent=2,
        )

    verb = "已撤回" if executed else "将撤回（预览，未写盘；加 --yes 执行）"
    out = [f"· remove {plan.slug}：{verb}"]
    if plan.relocate:
        out.append(f"    移入 .trash/：{', '.join(plan.relocate)}")
    for d in plan.drop_slug:
        rest = len([s for s in d.before_sources if s != plan.slug])
        out.append(f"    摘 slug（多源页保留，剩 {rest} 源）：{d.page}")
    for ln in plan.index_lines:
        out.append(f"    删 index 行：{ln}")
    if plan.orphans:
        out.append("    ⚠ 独源孤儿（advisory，一期不删——如需删留未来 prune-orphans）：")
        out.extend(f"        {o}" for o in plan.orphans)
    if plan.drop_slug:
        out.append("    注：被摘 slug 的多源页正文可能仍含已撤源内容，建议重 `ingest` 复核。")
    out.append("    注：撤回后请跑 `guanlan lint` 复核断链（remove 不自算入链）。")
    return "\n".join(out)


def run_remove(root: Path, *, src: str, yes: bool, json_output: bool) -> int:
    """`guanlan remove` 的核心：解析源 → 算 plan →（`--yes` 则执行）→ 渲染 → 退出码。"""
    try:
        slug = _resolve_slug(src)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE

    plan = run_remove_result(root, slug)
    if not plan.ok:
        if json_output:
            print(json.dumps({"ok": False, "slug": slug, "error": "source_not_found"}, ensure_ascii=False, indent=2))
        else:
            print(
                f"找不到源 {slug!r}（raw/{slug}.md 与 wiki/sources/{slug}.md 均不存在；可能已撤回）。",
                file=sys.stderr,
            )
        return EXIT_USAGE

    if yes:
        try:
            trash_dir = _execute(root, plan)
        except OSError as exc:  # 落盘 IO 失败 → 报 partial、不假装回滚；重跑可收敛（决策P3.9-10）
            print(
                f"撤回 {slug!r} 时写盘失败（可能已部分执行，重跑同命令可向前收敛）：{exc}",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if not json_output:
            print(f"✓ 已移入 {trash_dir.relative_to(root).as_posix()}/（含 manifest.json）。")

    print(format_plan(plan, executed=yes, json_output=json_output))
    return EXIT_OK


def remove_entrypoint(root_dir: str | Path, *, src: str, yes: bool, json_output: bool) -> int:
    """`guanlan remove` 的单一落地：校验**可写**库根 → run_remove。"""
    try:
        root = require_kb_root(root_dir, writable=True)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code
    return run_remove(root, src=src, yes=yes, json_output=json_output)


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.remove` 入口（与 `guanlan remove` 共享 remove_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.remove",
        description="源撤回：把误摄/已撤稿源移入 .trash/ + 摘多源页引用 + 修 index（零 LLM、人发起）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("src", help="源标识：<slug> / raw/<slug>.md / wiki/sources/<slug>.md")
    parser.add_argument("--yes", action="store_true", help="确认执行（不带则只预览、零写盘）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 契约")
    args = parser.parse_args(argv)
    return remove_entrypoint(args.dir, src=args.src, yes=args.yes, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
