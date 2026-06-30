"""共享页面原语（P3，见 docs/P3-健康与图谱.md §3 决策P3-2 / P3-8）。**零 LLM。**

从 P2 `check.py` 抽取，供 `check` / `health` / `lint` / `graph` 复用**同一套**页面遍历、
frontmatter 解析与链接解析口径——杜绝大小写 / `|别名` / `#锚点` / `.md` 后缀剥离规则在
四个模块间漂移（决策P3-2）。

frontmatter 读取**分两档、不可混用**：

- **严格档（`check` 专用）**：`split_frontmatter` + `parse_frontmatter`——块缺失/无法解析/
  非映射各返回一条 fatal `Violation`，**不吞错**（即 P2 `check.py` 原行为，写门禁靠此兜底）。
- **容错档（`health`/`lint`/`graph` 专用）**：`load_page(path)` 返回 `(meta_or_None, body)`，
  坏/缺 frontmatter 时 `meta=None`、**绝不抛**（决策P3-8：审计命令不因坏数据中断，
  frontmatter 正确性单一归口 `check`）。

**严禁让 `check` 改用 `load_page`**——否则写门禁会静默吞掉 frontmatter 错误。
"""

from __future__ import annotations

import json
import posixpath
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

# 优先用 libyaml 的 C 实现 SafeLoader（CSafeLoader）：纯 Python SafeLoader 是全库 frontmatter 解析的
# 热点（`build_graph` 冷算里约 78%）。CSafeLoader 与 SafeLoader 同一套安全 schema、解析结果等价，仅 C
# 加速；未随 PyYAML 装上 libyaml 时优雅回落纯 Python。报错仍是 `yaml.YAMLError` 子类，下游 except 不变。
try:
    from yaml import CSafeLoader as _SafeLoader
except ImportError:  # pragma: no cover - 取决于 PyYAML 是否带 libyaml
    from yaml import SafeLoader as _SafeLoader

__all__ = [
    "Violation",
    "Finding",
    "WIKILINK_RE",
    "split_frontmatter",
    "parse_frontmatter",
    "load_page",
    "load_page_text",
    "page_title",
    "page_type",
    "DIR_TO_TYPE",
    "VALID_TYPES",
    "link_stem",
    "fold_stem",
    "link_fold_stem",
    "iter_pages",
    "page_stem_index",
    "alias_index",
    "link_target_stems",
    "link_resolution_index",
    "resolve_owner",
    "index_md_links",
    "index_sync_state",
    "FINDING_CAUSAL_ORDER",
    "order_findings",
    "report_dict",
    "report_json",
]

# config 页（非 content）：仅 wiki/ 顶层的这三个文件；子目录里的同名文件不算 config。
# SCHEMA.md 在根、不在 wiki/ 下，天然不被扫。排除出 frontmatter/断链/graph/lint。
_CONFIG_PAGES = frozenset({"index.md", "log.md", "overview.md"})

# 规范页型分类法：wiki/ 一级目录 → frontmatter `type`。`health` 的页型↔目录一致性体检
# （docs/P3.10）取此归口；`VALID_TYPES` 是合法 type 集（`check` 可改引为零-behavior-change 一行）。
# 注意：这只是 health/check 所需的最小公共常量，**不**是「目录↔type↔index 分区」三元的大一统归口
# （决策P3.10-3）——reindex 的目录↔分区映射仍各管各的。
DIR_TO_TYPE = {
    "sources": "source",
    "entities": "entity",
    "concepts": "concept",
    "syntheses": "synthesis",
}
VALID_TYPES = frozenset(DIR_TO_TYPE.values())

# 非贪婪提取 [[…]]；目标里不含 ] 或换行。
WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
# index.md 用 markdown 链接 [文字](路径)（见 conventions §index.md），与 [[wikilink]] 分立。
# 链接文字段不含 ]/换行；目标段允许**成对**圆括号（文件名含 `(抚顺)` 之类），到第一个**非配对**
# `)` 收尾。目标拆为「非括号字符 | 单层成对 `(…)`」的序列——`(` 必起一对、不再当普通字符，故无
# 惰性嵌套量词的歧义/回溯，且畸形链接（未配对 `(` 后跟孤立 `)`）只会整体不匹配、不会截出错目标。
# 注意：[[X]] 无 `](` 结构，天然不被误吃。
_MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(((?:[^()\n]|\([^()\n]*\))*)\)")


@dataclass(frozen=True)
class Violation:
    """单条违规（`check` 用）。`page` 是相对知识库根的 posix 路径（如 `wiki/entities/Foo.md`）。"""

    page: str
    kind: str
    detail: str


@dataclass(frozen=True)
class Finding:
    """单条审计建议（`health`/`lint` 用）；字段形状同 `Violation`，但语义是**建议**而非门禁违规。

    跨页聚合的全局 finding（如 `lint.missing_entity`）`page` 留空串，消费侧据此识别。

    `suggestion`（P3.11）：可选的「疑似已有页」相对路径，由 `lint` 对断链 target 做确定性
    token-overlap 算得（零 LLM）。默认 None；`report_dict` 序列化时丢弃为 None 的可选键，故不附
    建议的 finding 其 JSON 与未引入本字段前**字节一致**（决策P3.11-5）。
    """

    page: str
    kind: str
    detail: str
    suggestion: str | None = None


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """切出 frontmatter 块与正文（与档无关的纯文本切分）。

    返回 `(block, body)`：`block` 是两条 `---` 之间的原文（不含分隔线），无合法块时为 None，
    此时 `body` 为全文（断链/正文校验仍在全文上跑）。
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    # 起始有 --- 但无闭合 → 视作无合法 frontmatter。
    return None, text


def _load_yaml_mapping(
    block: str, *, loader: type = _SafeLoader
) -> tuple[dict | None, str | None]:
    """解析 frontmatter 块为映射，两档共用以杜绝口径分叉。

    返回 `(meta, error)`：成功 `(dict, None)`；YAML 报错 `(None, 错误消息)`；
    解析出非映射 `(None, '…不是键值映射')`。严格档据 `error` 报 violation，容错档忽略之。

    `loader` 默认 `_SafeLoader`（libyaml 优先，仅 C 加速、解析**值**与纯 Python 等价）——供**容错档**
    （`load_page`→graph/health/lint 热路）提速；它丢弃 `error` 文本，故两 loader 的报错文本差异无影响。
    **严格档**（`check`，经 `parse_frontmatter`）显式传 `yaml.SafeLoader`：libyaml 与纯 Python loader 的
    `str(exc)` 文本不同（C 不含源码片段+^ 标记、个别 problem 措辞亦异），而该文本会进 `check` 的
    `frontmatter.unparsable` detail；锁定纯 Python loader 使 `check` 报错文本与宿主是否装 libyaml 无关、
    跨环境字节一致（且与本次提速前的 `safe_load` 行为逐字相同）。
    """
    try:
        meta = yaml.load(block, Loader=loader)
    except yaml.YAMLError as exc:
        return None, f"frontmatter 无法解析：{exc}"
    if not isinstance(meta, dict):
        return None, "frontmatter 不是键值映射"
    return meta, None


def parse_frontmatter(block: str | None) -> tuple[dict | None, Violation | None]:
    """**严格档**解析 frontmatter 块（`check` 专用）。

    返回 `(meta, fatal)`：成功时 `(dict, None)`；块缺失/无法解析/非映射时 `(None, 违规)`。
    `page` 由调用方补到违规上——这里只关心解析本身。坏数据**硬报**，不吞错。
    """
    if block is None:
        return None, Violation("", "frontmatter.block_missing", "缺 frontmatter（--- 块）")
    # 严格档锁定纯 Python SafeLoader：报错文本进 check 的 unparsable detail，须与宿主 libyaml 无关（见
    # _load_yaml_mapping 文档）。容错档不传 loader，照用默认 _SafeLoader（libyaml 提速、丢弃报错文本）。
    meta, error = _load_yaml_mapping(block, loader=yaml.SafeLoader)
    if error is not None:
        return None, Violation("", "frontmatter.unparsable", error)
    return meta, None


def load_page_text(text: str) -> tuple[dict | None, str]:
    """**容错档**：从**已读入的页面文本**解析 `(meta, body)`，**绝不抛**（决策P3-8）。

    与 `load_page` 同口径，但不读盘——供已持有文本/字节的调用方（如 heal 的写集快照，避免对
    同一文件二次 I/O）复用同一套解析，杜绝口径分叉。
    """
    block, body = split_frontmatter(text)
    if block is None:
        return None, body
    meta, _error = _load_yaml_mapping(block)  # 坏 meta → None，错误消息丢弃（不报错）。
    return meta, body


def load_page(path: Path) -> tuple[dict | None, str]:
    """**容错档**读取一张页面（`health`/`lint`/`graph` 专用）。

    返回 `(meta, body)`：frontmatter 缺块 / 无法解析 / 非映射时 `meta=None`、**绝不抛**
    （决策P3-8）。frontmatter 正确性的报错单一归口 `check`，审计命令不复制这套校验。

    非 UTF-8 字节用 `errors="replace"` 兜底（坏字符→`�`），兑现"绝不抛"承诺：否则一张
    GBK/Latin-1 页面会让 health/lint/graph 乃至 Web 浏览整体崩在 `UnicodeDecodeError`。
    严格档 `check` 自有读取（不走本函数），编码硬错仍可由其暴露。
    """
    return load_page_text(path.read_text(encoding="utf-8", errors="replace"))


def page_title(meta: dict | None, stem: str) -> str:
    """容错取 title：无合法 meta / title 非非空字符串时回退 stem（决策P3-8）。

    与 `page_type` 一起是 `graph` 节点标签 / Web 页面清单**共用**的 frontmatter 展示取值器
    （单一归口，杜绝两处口径漂移）。**不**校验合法性——那是 `check` 的职责。
    """
    if isinstance(meta, dict):
        title = meta.get("title")
        if isinstance(title, str) and title.strip():
            return title
    return stem


def page_type(meta: dict | None) -> str:
    """容错取 type：无合法 meta / type 非字符串时回退 `'unknown'`（决策P3-8）。只作展示标签。"""
    if isinstance(meta, dict):
        type_ = meta.get("type")
        if isinstance(type_, str) and type_:
            return type_
    return "unknown"


def link_stem(target: str) -> str:
    """把 `[[…]]` 目标归一为解析键：剥 `|别名`、`#锚点` 与可选 `.md` 后缀，取末段并小写。"""
    target = target.split("|", 1)[0].split("#", 1)[0].strip()
    target = target.replace("\\", "/").rsplit("/", 1)[-1]
    if target.lower().endswith(".md"):
        target = target[:-3]
    return target.lower()


def fold_stem(stem: str) -> str:
    """把已归一的 stem **进一步**折叠掉常见等价变体（NFKC → casefold → `_`→`-`）。（P3.8）

    解析期非破坏「消变体」的单一归口（决策P3.8-1 最小折叠集）：

    - **NFKC**：折全角/半角、兼容字符、组合记号（`Café`(NFD) → `café`(NFC) 同形）；
    - **casefold**（非 `.lower()`）：Unicode 大小写折叠更全；纯 ASCII/CJK 与 `.lower()` 一致、无回归；
    - **`_`→`-`**：kebab-case 为主、`_` 是最常见同义变体——**仅此一条**字符替换，不折重复 `-`、
      不剥首尾 `-`（过激会误并真实独立名）。

    **绝不替换 `link_stem`**：fold 永远叠在 `link_stem` 之上（见 `link_fold_stem`），`link_stem`
    自身语义不动——否则 `foo_bar` 当场被改写成 `foo-bar`、丢「raw 精确优先」、波及全体 `link_stem`
    消费者（`alias_index`/check/graph/heal/Web，决策P3.8-2）。
    """
    return unicodedata.normalize("NFKC", stem).casefold().replace("_", "-")


def link_fold_stem(raw: str) -> str:
    """`[[raw]]` 的折叠键 = `fold_stem(link_stem(raw))`（fold 叠在 link_stem 之上，决策P3.8-2）。"""
    return fold_stem(link_stem(raw))


def iter_pages(wiki: Path) -> Iterator[Path]:
    """按稳定排序遍历 `wiki/` 下所有**非 config** 的 `*.md` 页面（供 check/health/lint/graph 复用）。"""
    for path in sorted(wiki.rglob("*.md")):
        if not path.is_file():
            continue
        if path.parent == wiki and path.name in _CONFIG_PAGES:
            continue
        yield path


def page_stem_index(wiki: Path) -> dict[str, str]:
    """`wiki/` 下**所有**页面（**含 config**）的 stem(小写) → 相对知识库根的 posix 路径。

    `[[wikilink]]` 解析的**单一归口**：`_base_resolution_index`（→ `link_resolution_index` 解析表 +
    `resolve_owner`，check/graph/heal/Web 共用）由它派生，避免各扫一遍、解析口径漂移（决策P3-6：config
    页不被校验/不建节点，但**可作合法链接目标**）。stem 全库唯一是按名解析的固有前提；万一
    重名，按排序取第一个，保持确定性。
    """
    root = wiki.parent
    index: dict[str, str] = {}
    for path in sorted(wiki.rglob("*.md")):
        if path.is_file():
            index.setdefault(path.stem.lower(), path.relative_to(root).as_posix())
    return index


def alias_index(
    wiki: Path, *, loaded: list[tuple[Path, dict | None]] | None = None
) -> dict[str, str]:
    """content 页 frontmatter `aliases` → 拥有页 stem(小写)。**零 LLM。**（P3.1，决策P3.1-1/2/3）

    别名进入 `[[wikilink]]` 解析命名空间（与 stem 同口径、大小写不敏感）：`[[别名]]` 解析到声明页，
    消假断链、补 CJK 同义召回（见 docs/P3.1-别名解析.md）。

    - **仅 content 页**：只扫 `iter_pages`（config 页不声明别名）。
    - **容错档**：用 `load_page` 读取，坏/缺 frontmatter 跳过、**绝不抛**（与 health/lint/graph 一致）。
    - **归一**：别名经 `link_stem` 归一后入键，与 `[[…]]` 查找口径**完全对称**（决策P3.1-3）。
    - **幂等**：同名别名按 `iter_pages` 稳定排序**先到先得**（`setdefault`）；真冲突（撞 stem / 撞另一
      别名）由 `check` 报错（决策P3.1-4），此处不裁决，只保证确定性。
    - **复用已加载**：`loaded` 给 `(path, meta)` 对（调用方**已 `load_page`** 的结果，如 `build_graph`
      的节点循环按 `iter_pages` 序产出的 `meta`）时直接复用、**不再 `iter_pages`+`load_page` 重解析整库**
      （消 `build_graph` 双解析；与 `index_sync_state` 的 `pages` 透传同款）。缺省 `None` 则内部
      `iter_pages`+`load_page` 一次（原行为）。须与 `iter_pages` 同序、同 `load_page` 口径以保确定性。
    """
    out: dict[str, str] = {}
    items = (
        loaded
        if loaded is not None
        else ((path, load_page(path)[0]) for path in iter_pages(wiki))
    )
    for path, meta in items:
        if not isinstance(meta, dict):
            continue
        raw_aliases = meta.get("aliases")
        if not isinstance(raw_aliases, list):
            continue
        owner = path.stem.lower()
        for item in raw_aliases:
            if not isinstance(item, str):
                continue
            key = link_stem(item)
            if key:
                out.setdefault(key, owner)
    return out


def link_target_stems(wiki: Path) -> frozenset[str]:
    """`[[wikilink]]` 解析键集（**仅兼容用途**）= `link_resolution_index` 的键集。（P3.8 退化）

    自 P3.8 起，断链判定一律走 `resolve_owner`（精确 + fold 兜底两段探针）；本集**断不可再当断链
    判据**——`link_stem(raw) in 此集` 会漏掉所有 fold 兜底命中（如 `[[multi_head_attention]]` 命中
    `multi-head-attention.md`），退回误断链。保留它只为仍需「键集」的旧调用方（无内部调用）。
    """
    return frozenset(link_resolution_index(wiki))


def _base_resolution_index(
    wiki: Path, *, loaded: list[tuple[Path, dict | None]] | None = None
) -> dict[str, str]:
    """解析键(stem | 别名, 小写) → 拥有页相对库根 posix 路径（P3.1 基底，决策P3.1-6）。

    在 `page_stem_index`（stem→path）之上叠加 别名→拥有页 path；**页面 stem 优先于别名**——撞名时
    别名不遮蔽真实页（且该撞名已由 `check` 报 `aliases.collides_stem`）。指向别名拥有页缺失者跳过。
    P3.8 把它从 `link_resolution_index` 抽出作基底：fold variant 叠加层在其上派生（见下）。

    `loaded` 透传给 `alias_index` 复用已 `load_page` 的 `(path, meta)`、免整库重解析（`page_stem_index`
    只 rglob 取 stem、不读 YAML，不在复用之列）。
    """
    stem_to_path = page_stem_index(wiki)
    resolved = dict(stem_to_path)
    for alias, owner in alias_index(wiki, loaded=loaded).items():
        if alias in resolved:
            continue  # 页面 stem 优先；撞名由 check 报错
        path = stem_to_path.get(owner)
        if path is not None:
            resolved[alias] = path
    return resolved


def link_resolution_index(
    wiki: Path, *, loaded: list[tuple[Path, dict | None]] | None = None
) -> dict[str, str]:
    """解析表：键(精确 stem|别名 ∪ **安全 fold variant**) → 拥有页相对库根 posix 路径。（P3.8，决策P3.8-3）

    `check`(owner 是否 None) / `graph`(owner→节点) / `heal`(写后回执判 still_broken) / `Web`(owner→
    路径) **全复用**此一张表 + `resolve_owner`，杜绝各写一套 owner 逻辑、口径漂移。

    `loaded` 透传给底层 `alias_index`：`build_graph` 把节点循环已 `load_page` 的 `(path, meta)` 传进来，
    避免「建节点解析一遍 + 建解析表又解析一遍」的整库 frontmatter 双解析（缺省 None = 原行为，
    check/heal/Web 等既有调用方不受影响）。

    在 `_base_resolution_index`（精确 stem/别名）之上**只叠加无冲突的 fold variant**（决策P3.8-4 机械规则）：
    对每个 base 键算 `fold_stem`，**当且仅当**「该 fold 键不在 base 键集」**且**「该 fold 键的 fold group
    拥有者恰 1 个」才生成 variant 键 → 拥有者路径。

    - variant 键与 base 键**天然不相交**（`fk not in base` 门），故整表每键唯一 owner、无须区分
      「raw 命中 vs fold 命中」。
    - `foo_bar.md` + `foo-bar.md` 同存：两者 base 键各在表中、fold 键 `foo-bar` 已是 base 键 → 不新增
      → 两页各由精确键解析、零串台（**撞则不折叠**自动成立，无需额外检测，决策P3.8-6）。
    - fold group ≥2 拥有者（真撞名）→ 丢弃该 variant、不影响 base 键解析（歧义 fold 形保持断链、不猜）。
    """
    base = _base_resolution_index(wiki, loaded=loaded)
    groups: dict[str, set[str]] = defaultdict(set)
    for key, owner in base.items():
        fk = fold_stem(key)
        if fk not in base:  # fk 撞任意已有精确键 → 不新增（精确优先、撞则不折叠，自动成立）
            groups[fk].add(owner)
    for fk, owners in groups.items():
        if len(owners) == 1:  # fold group **唯一拥有者**才生成 variant；≥2 → 撞名，丢弃
            base[fk] = next(iter(owners))
    return base


def resolve_owner(raw: str, idx: Mapping[str, str]) -> str | None:
    """`[[raw]]` → 拥有页相对库根 posix 路径；皆不中 → None。（P3.8 单一 owner 归口，决策P3.8-2/3）

    两段探针：**raw 精确（`link_stem`）优先**、**fold（`link_fold_stem`）兜底**——绝不把 fold 塞进
    `link_stem`（会丢精确优先、波及全体 `link_stem` 消费者）。`idx` 取 `link_resolution_index(wiki)`。
    """
    k = link_stem(raw)
    if k in idx:
        return idx[k]  # raw stem / 别名 精确命中（优先）
    fk = link_fold_stem(raw)
    if fk in idx:
        return idx[fk]  # fold variant 兜底
    return None


# finding 因果排序（gbrain 反向评审 §3「doctor-cause-rank」借形状；纯展示层、零 LLM、确定性）。
# `health`/`lint` 默认平铺输出 findings，让人/Agent 可能先去手修**症状**而非**根因**。这里按
# 「根因/数据完整性 → 内容质量 → 拓扑优化」三档给每个 kind 一个稳定 rank，输出时据此重排——
# **不改 finding 集合、不改退出码**（仍 riding `EXIT_LINT_FINDINGS`），只改顺序。
#
# 唯一**机械因果**对是 `lint.missing_entity → lint.broken_link`：缺失实体是同一未解析目标被 ≥N 页
# 引用的聚合，建页即消解它聚合的那几条 broken_link（决策P3.2-1 同源），故因排在果前。其余为
# 「先修对的那个」的优先序（数据完整性 > 内容/组织 > 拓扑 nice-to-have），非机械因果。
# 注：gbrain 例「index_missing → orphan/broken」跨 health/lint 两条命令、无法在单份报告里合并，
# 故只在**各命令报告内**排序、并守住命令内那对真因果。
FINDING_CAUSAL_ORDER: tuple[str, ...] = (
    # 第一档 · 根因 / 数据完整性（修之即消解下游症状）
    "lint.missing_entity",  # 根因：建页即消解其聚合的多条 broken_link（机械因果）
    "lint.broken_link",  # 断链（missing_entity 之果 + 零散单引）
    "health.index_missing_page",  # 磁盘有页未入 index（结构未接好）
    "health.index_dangling",  # index 悬挂链接
    "lint.orphan",  # 无入链（待接入）
    # 第二档 · 内容 / 组织质量
    "health.stub_page",
    "health.type_dir_mismatch",
    "health.uncharted_page",
    # 第三档 · 拓扑优化建议（结构 nice-to-have，非坏数据）
    "lint.hub_node",
    "lint.thin_intercommunity_link",
    "lint.isolated_community",
    "lint.bridge_edge",
    "lint.cut_vertex",
)
_FINDING_RANK = {kind: i for i, kind in enumerate(FINDING_CAUSAL_ORDER)}
# 未登记 kind 的兜底 rank：排在所有已登记 kind 之后（恒大于任一已登记 rank）。
_FINDING_RANK_FALLBACK = len(FINDING_CAUSAL_ORDER)


def order_findings(findings: list[Finding]) -> list[Finding]:
    """按因果/优先级对 findings 做**纯展示层稳定重排**：上游因排在下游果之前（gbrain §3 借形状）。

    零-LLM、确定性；**不改 finding 集合、不改退出码**——只改输出顺序。`sorted` 稳定，故各 kind
    内既有的确定性次序（如 broken_link 的 `(source,target)` 升序）原样保留；未登记的 kind 取末档
    rank、稳定地排在已登记 kind 之后。新返回列表，不就地改入参。
    """
    return sorted(findings, key=lambda f: _FINDING_RANK.get(f.kind, _FINDING_RANK_FALLBACK))


def report_dict(*, ok: bool, pages_checked: int, items_key: str, items: list) -> dict:
    """`check`/`health`/`lint` 报告信封的**产-dict 单一归口**（稳定契约 `{ok, pages_checked, <items_key>}`）。

    `report_json` 在此之上做序列化；MCP 宿主的 `health`/`lint` 工具**直接拿这份 dict**喂结构化输出
    （决策P4.10-10），不再 `json.loads(format_report(...))` 绕字符串往返。`items` 是 `Violation`/
    `Finding` 同形 dataclass 列表；`items_key` 为 `"violations"`（check）或 `"findings"`（health/lint）。

    **丢弃值为 `None` 的可选键**（P3.11，决策P3.11-5）：`Finding.suggestion` 不附建议时为 None，
    剔除后该 finding 的 JSON 与未引入 `suggestion` 字段前**字节一致**；`Violation` 三字段恒非 None、
    不受影响（故 check 序列化不变）。
    """
    return {
        "ok": ok,
        "pages_checked": pages_checked,
        items_key: [
            {k: v for k, v in asdict(i).items() if v is not None} for i in items
        ],
    }


def report_json(*, ok: bool, pages_checked: int, items_key: str, items: list) -> str:
    """`check`/`health`/`lint` 共用的机器可读报告信封（稳定契约 `{ok, pages_checked, <items_key>}`）。

    单一归口，杜绝三命令的 JSON schema 漂移。底层 dict 经 `report_dict` 产出（MCP 工具共用同一份），
    本函数只负责序列化：`ensure_ascii=False, indent=2`、**无尾随换行**（与 P2 行为一致；MCP 拆分须
    原样保留这套参数，否则破 CLI/Web JSON 字节契约，决策P4.10-10）。
    """
    return json.dumps(
        report_dict(ok=ok, pages_checked=pages_checked, items_key=items_key, items=items),
        ensure_ascii=False,
        indent=2,
    )


def index_md_links(text: str) -> set[str]:
    """解析 `index.md` 正文的 **markdown 链接**目标集合（相对 `wiki/` 的 posix 路径）。

    只取本地页面链接：剥 `#锚点`、`./` 前缀与首尾空白；跳过外链（`http(s)://`/`mailto:` 等）
    与纯锚点。返回去重的目标集合，供 health 的 index↔磁盘双向同步比对（§4.2）。
    解析的是 `[文字](路径)`，**不是** `[[wikilink]]`（index 用前者，见 conventions §index.md）。
    """
    targets: set[str] = set()
    for raw in _MD_LINK_RE.findall(text):
        # 先归一反斜杠为 posix，再剥前缀——否则 `.\foo` 会漏掉 ./ 检测、错成 `/foo`。
        target = raw.split("#", 1)[0].strip().replace("\\", "/")
        if not target:
            continue  # 纯锚点链接（如 [x](#节)），非页面引用。
        if "://" in target or target.startswith("mailto:"):
            continue  # 外链，不参与 index↔磁盘同步。
        if target.startswith("./"):
            target = target[2:]
        targets.add(target)
    return targets


def _target_within_wiki(target: str) -> bool:
    """index 链接目标是否安全落在 `wiki/` 内（拒绝绝对路径与 `..` 越界）。

    与 `check` 的 sources 防越界一致：否则 `../outside.md` / `/tmp/x.md` 会让 `(wiki/target)`
    解析到库外、若该文件恰好存在则误判 index 链接"有对应文件"、漏报悬空。
    """
    if target.startswith("/"):
        return False
    norm = posixpath.normpath(target)
    return norm != ".." and not norm.startswith("../")


def index_sync_state(
    wiki: Path, pages: list[Path] | None = None
) -> tuple[list[Path], list[str]]:
    """index ↔ 磁盘双向存在性同步的**单一归口**（P3 §4.2 / P3.4 §2，决策P3.4-4）。**零 LLM。**

    `health`（包成 `Finding` 报告）与 `reindex`（拿来登记/剪枝）共用此判定，杜绝"检测与修复
    口径漂移"。**只做存在性，不校验分区/type 匹配。**返回 `(missing, dangling)`：

    - `missing`：`pages` 里 rel 路径不在 `index_md_links` 的内容页（绝对 `Path`，输入序）；
    - `dangling`：`index.md` 链接里**越界**或**磁盘无对应文件**的目标（相对 `wiki/` 字符串，`sorted`）。

    `pages` 可传入调用方**已遍历**的内容页列表（`iter_pages` 结果），避免重复 walk，且让 `health`
    的 missing 判定与其桩页检查用**同一份**快照（消 TOCTOU 漂移）；缺省则内部 `iter_pages` 一次。
    """
    wiki = Path(wiki)
    if pages is None:
        pages = list(iter_pages(wiki))
    index_path = wiki / "index.md"
    index_text = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    linked = index_md_links(index_text)  # index 收录的目标集合（相对 wiki/）。

    missing = [
        path for path in pages if path.relative_to(wiki).as_posix() not in linked
    ]
    dangling = [
        target
        for target in sorted(linked)
        # 越界/绝对路径目标视作悬空（不让 is_file 跟随 .. 命中库外文件而漏报）。
        if not _target_within_wiki(target) or not (wiki / target).is_file()
    ]
    return missing, dangling
