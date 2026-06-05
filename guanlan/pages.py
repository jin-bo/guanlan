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
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

__all__ = [
    "Violation",
    "Finding",
    "WIKILINK_RE",
    "split_frontmatter",
    "parse_frontmatter",
    "load_page",
    "page_title",
    "page_type",
    "link_stem",
    "iter_pages",
    "link_target_stems",
    "index_md_links",
    "report_json",
]

# config 页（非 content）：仅 wiki/ 顶层的这三个文件；子目录里的同名文件不算 config。
# SCHEMA.md 在根、不在 wiki/ 下，天然不被扫。排除出 frontmatter/断链/graph/lint。
_CONFIG_PAGES = frozenset({"index.md", "log.md", "overview.md"})

# 非贪婪提取 [[…]]；目标里不含 ] 或换行。
WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
# index.md 用 markdown 链接 [文字](路径)（见 conventions §index.md），与 [[wikilink]] 分立。
# 链接文字段不含 ]/换行；目标段不含 )/换行。注意：[[X]] 无 `](` 结构，天然不被误吃。
_MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\n]+?)\)")


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
    """

    page: str
    kind: str
    detail: str


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


def _load_yaml_mapping(block: str) -> tuple[dict | None, str | None]:
    """解析 frontmatter 块为映射，两档共用以杜绝口径分叉。

    返回 `(meta, error)`：成功 `(dict, None)`；YAML 报错 `(None, 错误消息)`；
    解析出非映射 `(None, '…不是键值映射')`。严格档据 `error` 报 violation，容错档忽略之。
    """
    try:
        meta = yaml.safe_load(block)
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
    meta, error = _load_yaml_mapping(block)
    if error is not None:
        return None, Violation("", "frontmatter.unparsable", error)
    return meta, None


def load_page(path: Path) -> tuple[dict | None, str]:
    """**容错档**读取一张页面（`health`/`lint`/`graph` 专用）。

    返回 `(meta, body)`：frontmatter 缺块 / 无法解析 / 非映射时 `meta=None`、**绝不抛**
    （决策P3-8）。frontmatter 正确性的报错单一归口 `check`，审计命令不复制这套校验。

    非 UTF-8 字节用 `errors="replace"` 兜底（坏字符→`�`），兑现"绝不抛"承诺：否则一张
    GBK/Latin-1 页面会让 health/lint/graph 乃至 Web 浏览整体崩在 `UnicodeDecodeError`。
    严格档 `check` 自有读取（不走本函数），编码硬错仍可由其暴露。
    """
    block, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    if block is None:
        return None, body
    meta, _error = _load_yaml_mapping(block)  # 坏 meta → None，错误消息丢弃（不报错）。
    return meta, body


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


def iter_pages(wiki: Path) -> Iterator[Path]:
    """按稳定排序遍历 `wiki/` 下所有**非 config** 的 `*.md` 页面（供 check/health/lint/graph 复用）。"""
    for path in sorted(wiki.rglob("*.md")):
        if not path.is_file():
            continue
        if path.parent == wiki and path.name in _CONFIG_PAGES:
            continue
        yield path


def link_target_stems(wiki: Path) -> frozenset[str]:
    """`[[wikilink]]` 解析集 = `wiki/` 下**所有**页面 stem（**含 config**），小写、大小写不敏感。

    与 `check` / `graph` 完全同口径（决策P3-6）：扫描排除（哪些页被校验/建节点）与链接解析集
    （哪些 stem 算合法目标）是两件事——config 页不被校验/不建节点，但可作合法链接目标。
    """
    return frozenset(p.stem.lower() for p in wiki.rglob("*.md") if p.is_file())


def report_json(*, ok: bool, pages_checked: int, items_key: str, items: list) -> str:
    """`check`/`health`/`lint` 共用的机器可读报告信封（稳定契约 `{ok, pages_checked, <items_key>}`）。

    单一归口，杜绝三命令的 JSON schema 漂移。`items` 是 `Violation`/`Finding` 同形 dataclass 列表；
    `items_key` 为 `"violations"`（check）或 `"findings"`（health/lint）。无尾随换行（与 P2 行为一致）。
    """
    return json.dumps(
        {"ok": ok, "pages_checked": pages_checked, items_key: [asdict(i) for i in items]},
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
