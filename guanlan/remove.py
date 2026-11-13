"""源撤回 remove（P3.9，见 docs/P3.9-源撤回.md）。**零 LLM、人发起的宿主确定性写。**

`guanlan remove <源>`：把一个误摄 / 已撤稿源的"自身落盘物"（`raw/<slug>.md` 或一键入库的分卷
`raw/<sample>/<slug>.md` + 同级 `images/<slug>/` 图片、`wiki/sources/<slug>.md` 摘要页）**整体移入回收区
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
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

import yaml

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .gate import _trusted_sources  # 读衍生页可信 sources 的单一归口（坏/缺 → None）
from .graph import build_graph  # 入链清单的单一归口（同 check/lint/heal 的解析表）
from .pages import iter_pages, load_page, split_frontmatter
from .paths import require_kb_root
from .rawio import (  # slug 归一 + 逐字读写（避免半写、不静默改行尾）同口径
    atomic_write_text,
    detect_eol,
    dump_frontmatter,
    normalize_basename,
    raw_slug,
    read_text_verbatim,
    same_name_key,
    source_identity,
)
from .reindex import (  # index 行删除的单一归口
    _join_lines,
    _prune_dangling,
    _split_lines,
)

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
    backlinks: list[str] = field(default_factory=list)  # 入链页：撤回后将悬链（advisory，不改）
    requested: str = ""  # 用户给的源名（规范化后）；与 `slug` 不同 = 摘要页是按旧算法建的旧名页（#203）


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


def _raw_file(root: Path, slug: str) -> str | None:
    """源的 raw 文件（相对库根 posix）。先看 `raw/<slug>.md`；没有再找子目录里同名的——一键入库的分卷落在
    `raw/<sample>/<slug>.md`（P4.30 §5），source 页仍按文件名 stem 命名。子目录里同名不止一份 → `ValueError`
    （认不准就不动）。`raw/images/` 与各处 `images/` 图片目录、隐藏目录不算。"""
    top = f"raw/{slug}.md"
    if (root / top).exists():
        return top
    raw = root / "raw"
    if not raw.is_dir():
        return None
    name = same_name_key(f"{slug}.md")
    hits = []
    for path in raw.rglob("*.md"):  # 逐个比文件名：slug 里可能有 `[` 等 glob 元字符
        parts = path.relative_to(raw).parts
        # 按 NFC 比：盘上可能存 NFD 写法（Linux 上 NFC 键入的名字到不了它，#203）
        if same_name_key(path.name) != name or not path.is_file():
            continue
        if any(part == "images" or part.startswith(".") for part in parts[:-1]):
            continue
        hits.append(path.relative_to(root).as_posix())
    if len(hits) > 1:
        raise ValueError(f"raw/ 下有多份 {name}，认不准要撤回哪一份：{'、'.join(sorted(hits))}")
    return hits[0] if hits else None


def _resolve_target(root: Path, slug: str) -> tuple[str | None, str]:
    """用户给的源名 → (raw 文件, 摘要页 slug)。摘要页 slug 通常就是 `slug`，有两种例外（#203）：

    - 找到了 raw 文件，而它的摘要页是 #201 之前按旧算法建的旧名页（NFD `résumé` 的 `re-sume`，`raw_digest`
      确证属于它）：摘要页、衍生页 `sources`、index 行、入链都按旧名处理，否则原文进了回收区、旧页成孤页。
    - 按旧页名给的（`remove re-sume`）：raw 按名字找不到；旧页的 `raw_digest` 指向的原文若以这张页为身份，
      一并撤回。只认「页身份正是这张页」的原文，不把改过名的普通页牵连到别的资料上。
    """
    sources = root / "wiki" / "sources"
    raw_rel = _raw_file(root, slug)
    if raw_rel is not None:
        return raw_rel, source_identity(sources, PurePosixPath(raw_rel).stem, raw_rel)
    return _raw_from_page(root, sources, slug), slug


def _raw_from_page(root: Path, sources: Path, slug: str) -> str | None:
    """`wiki/sources/<slug>.md` 的 `raw_digest` 指向的原文——仅当该原文的页身份就是这张页（旧名页）。"""
    from .provenance import RAW_DIGEST_KEY, parse_digest_value

    page = sources / f"{slug}.md"
    if not page.is_file():
        return None
    meta, _body = load_page(page)
    parsed = parse_digest_value((meta or {}).get(RAW_DIGEST_KEY))
    if parsed is None:
        return None
    rel = parsed[0]
    if not rel.startswith("raw/") or not (root / rel).is_file():
        return None
    if same_name_key(source_identity(sources, PurePosixPath(rel).stem, rel)) != same_name_key(slug):
        return None
    return rel


def _locate(root: Path, raw_rel: str | None, page_slug: str) -> list[str]:
    """探源自身落盘物（raw 文件 / 图片目录 / 摘要页），返回存在者的相对库根 posix 列表。

    图片目录与 raw 文件同级、按 raw 文件名命名：`raw/<stem>.md` → `raw/images/<stem>/`，分卷
    `raw/<sample>/<stem>.md` → `raw/<sample>/images/<stem>/`（`imageio._images_dir` 同口径）。"""
    found: list[str] = []
    if raw_rel:
        images_rel = (PurePosixPath(raw_rel).parent / "images" / PurePosixPath(raw_rel).stem).as_posix()
    else:
        images_rel = f"raw/images/{page_slug}"
    for rel in (raw_rel, images_rel, f"wiki/sources/{page_slug}.md"):
        if rel and (root / rel).exists():
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
    _kept, removed = _prune_dangling(read_text_verbatim(index_path), _index_target(slug))
    return removed


def _backlink_pages(root: Path, slug: str) -> list[str]:
    """反扫「谁 `[[链向]]` 这张摘要页」——撤回后会悬链的页（相对库根 posix，排自环、去重排序）。

    **复用 `graph.build_graph` 已解析的边**（与 check/lint/heal 同一张解析表，故别名 / fold 变体
    链接一样算数），不新写第二套反链逻辑。**不用 `compute_backlinks`**：它只出入链**计数**
    （`dict[str, int]`），拿不到页面清单。摘要页不在盘上（只剩 `raw/`）→ 图里无此节点 → 空列表。

    **按节点 id（小写 stem）定位目标、不拿路径字面比**：`_locate` 用的是 `Path.exists()`，在大小写
    不敏感的文件系统（macOS/Windows）上 `remove foo` 照样能定位到盘上的 `sources/Foo.md`，而
    `n.path == "wiki/sources/foo.md"` 字面比会落空 → 静默报"无入链"，恰好瞒掉本功能唯一要说的事。

    **同-stem 歧义（目标侧）一律退回空清单**（docs/P3.9 §7 评审发现4 点名的那道）：`sources/<slug>`
    与 `entities/<slug>` 同 stem 时二者共用 `Node.id`，入链归属不到具体一页；且此时撤走摘要页后
    `[[<slug>]]` 仍被同名页兜住、**根本不会悬链**，照报只会是假告警。退空即回落到"跑 `guanlan lint`
    复核全库断链"那条兜底（`format_plan` 末尾那句一直在）。

    **同-stem 歧义（来源侧）反过来：宁可多列、绝不少列。** 两张链者页同 stem（`concepts/bar` 与
    `entities/bar`）时它们也共用 `Node.id`，`{id: path}` 只会留下其中一条 → 另一条**静默漏报**，而
    漏报恰好瞒掉本功能唯一要说的事。故按 id 反查出**全部**同 id 的页一并列出：多列一页人一眼可辨，
    少列一页无人察觉（目标侧退空是因为那种情形下"不会悬链"是确定的，与此处不同）。

    **已知过报**：若别的内容页把 `<slug>` 声明成 `aliases`，撤走摘要页后 `[[<slug>]]` 会被该别名接管、
    并不悬链，而这里照列。此撞名本身已被 `check` 判 `aliases.collides_stem`（见 `pages.
    _base_resolution_index`），属库已违规的状态，不为它再扫一遍全库别名表。

    代价是整库再建一次图（`_scan_derivatives` 已扫过一遍 frontmatter）。`remove` 是一次性的人发起
    命令、默认先出预览，这点重复可接受；**不**为它给 `build_graph` 开预加载入口（那是另一件事）。
    """
    target_id = slug.lower()  # Node.id = 小写 stem，与 build_graph 的寻址口径同基。
    g = build_graph(root / "wiki")
    owners = [n.path for n in g.nodes if n.id == target_id]
    # len != 1 → 图里无此节点（摘要页不在盘上）或同-stem 歧义；非 sources/ 下 → 同名页不是本摘要页。
    if len(owners) != 1 or not owners[0].startswith("wiki/sources/"):
        return []
    linkers = {  # 早退之后再算，源页不在盘上时不白扫全库边。
        e.source
        for e in g.edges
        if e.resolved and e.target == target_id and e.source != target_id
    }
    return sorted(n.path for n in g.nodes if n.id in linkers)


def run_remove_result(root: Path, slug: str) -> RemovePlan:
    """算一次撤回的 plan。**纯函数、只读、不写盘**（仿 `reindex.run_reindex` / `heal.run_heal_result`）。"""
    root = Path(root)
    raw_rel, page_slug = _resolve_target(root, slug)
    relocate = _locate(root, raw_rel, page_slug)
    if not relocate:  # 源自身落盘物全缺 → 无可撤
        return RemovePlan(ok=False, slug=slug, relocate=[], drop_slug=[], orphans=[], index_lines=[], requested=slug)
    # 衍生页 `sources`、index 行、入链都按摘要页 slug（旧名页即旧名，#203）
    drops, orphans = _scan_derivatives(root, page_slug)
    index_lines = _plan_index_lines(root / "wiki", page_slug)
    return RemovePlan(
        ok=True,
        slug=page_slug,
        relocate=relocate,
        drop_slug=drops,
        orphans=orphans,
        index_lines=index_lines,
        backlinks=_backlink_pages(root, page_slug),
        requested=slug,
    )


def _drop_slug_from_page(path: Path, slug: str) -> bool:
    """从一页 frontmatter 的 `sources` 摘掉 slug、重写回盘。返回是否改动（幂等：已无 → False）。

    复用 `split_frontmatter` + `rawio.dump_frontmatter`（同 `apply_origin`，**绝不裸拼**）；**body 逐字
    保留**（`split_frontmatter` 返回原文 body），只重序列化 frontmatter 块。坏/无块/非映射 → 不动。

    非 UTF-8 页（`load_page` 在 plan 期用 `errors='replace'` 容错读、仍可能进 drop 清单）这里**跳过**
    （strict 读会抛 `UnicodeDecodeError`——是 `ValueError` 子类、不被 `run_remove` 的 `except OSError`
    接住）：用 replace 解码后回写会损坏原字节，故不安全重写、留人工，**绝不崩在执行中途**。

    读走 `read_text_verbatim`、块按原 EOL 重出（`dump_frontmatter`）：CRLF 页只改 `sources` 那几行，
    不会被顺手整份改成 LF（旧实现读侧归一 + 写侧逐字，等于每次摘源都重写全文行尾）。
    """
    try:
        text = read_text_verbatim(path)
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
    # 原子覆盖：崩溃不把内容页写成半截；块按原 EOL 出、body 逐字不动。
    atomic_write_text(path, dump_frontmatter(meta, body, eol=detect_eol(text)))
    return True


def _prune_index_line(index_path: Path, slug: str) -> list[str]:
    """从当前 index.md 删摘要页登记行（幂等：行已删 → 不写）。返回被删行原文。"""
    if not index_path.is_file():
        return []
    text = read_text_verbatim(index_path)  # 逐字读：与逐字写成对，CRLF 不被静默改成 LF
    _lines, eol, trailing = _split_lines(text)  # 只为拿 EOL/尾换行，重组时按原样保真
    kept, removed = _prune_dangling(text, _index_target(slug))
    if removed:
        atomic_write_text(index_path, _join_lines(kept, eol, trailing))  # 自管 EOL、逐字原子写
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
        "backlinks": list(plan.backlinks),  # 入链页（未改动）——同属 blast-radius，二期 restore 的复核面
        "index_lines_removed": list(plan.index_lines),
    }
    # 原子写：半写的恢复配方会让二期 restore 无法解析 JSON；新建文件下失败则不残留半截。
    atomic_write_text(
        trash_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )

    # ② 摘多源页 slug（幂等）→ ③ 删 index 行（从当前文件重算，幂等）→ ④ 最后移源
    for d in plan.drop_slug:
        _drop_slug_from_page(root / d.page, plan.slug)
    _prune_index_line(root / "wiki" / "index.md", plan.slug)
    for rel in plan.relocate:
        _move_into_trash(root / rel, root, trash_dir)
    _prune_empty_raw_dirs(root, plan.relocate)
    return trash_dir


def _prune_empty_raw_dirs(root: Path, moved: list[str]) -> None:
    """分卷全撤完后，`raw/<sample>/`（及其下的 `images/`）空了就删掉；`raw/` 本身与非空目录不动。"""
    raw = (root / "raw").resolve()
    dirs = {(root / rel).parent for rel in moved if rel.startswith("raw/")}
    for d in sorted(dirs, key=lambda x: len(x.parts), reverse=True):
        cur = d
        while cur.resolve() != raw and raw in cur.resolve().parents:
            try:
                cur.rmdir()  # 非空 → OSError，停
            except OSError:
                break
            cur = cur.parent


def format_plan(plan: RemovePlan, *, executed: bool, json_output: bool) -> str:
    """渲染回执：`--json` 走稳定契约；否则人类可读 worklist。"""
    if json_output:
        return json.dumps(
            {
                "ok": plan.ok,
                "slug": plan.slug,
                "requested": plan.requested or plan.slug,
                "executed": executed,
                "relocate": list(plan.relocate),
                "drop_slug": [asdict(d) for d in plan.drop_slug],
                "orphans": list(plan.orphans),
                "index_lines": list(plan.index_lines),
                "backlinks": list(plan.backlinks),
            },
            ensure_ascii=False,
            indent=2,
        )

    verb = "已撤回" if executed else "将撤回（预览，未写盘；加 --yes 执行）"
    out = [f"· remove {plan.slug}：{verb}"]
    if plan.requested and plan.requested != plan.slug:
        out.append(f"    注：摘要页是按旧算法建的旧名页 `{plan.slug}`（输入 `{plan.requested}`），按旧名撤回与摘除引用。")
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
    if plan.backlinks:
        # 措辞对预览/已执行两态都成立（`executed=True` 时链接已经悬空，不能只说"将"）。
        out.append("    ⚠ 入链页（指向本摘要页的 `[[链接]]` 撤回后即悬空，advisory——这些页一字不改）：")
        out.extend(f"        {b}" for b in plan.backlinks)
    if plan.drop_slug:
        out.append("    注：被摘 slug 的多源页正文可能仍含已撤源内容，建议重 `ingest` 复核。")
    out.append("    注：撤回后请跑 `guanlan lint` 复核全库断链（上面只列指向本摘要页的入链）。")
    return "\n".join(out)


def run_remove(root: Path, *, src: str, yes: bool, json_output: bool) -> int:
    """`guanlan remove` 的核心：解析源 → 算 plan →（`--yes` 则执行）→ 渲染 → 退出码。"""
    try:
        slug = _resolve_slug(src)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE

    try:
        plan = run_remove_result(root, slug)
    except ValueError as exc:  # raw/ 下同名多份，认不准
        print(exc, file=sys.stderr)
        return EXIT_USAGE
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
