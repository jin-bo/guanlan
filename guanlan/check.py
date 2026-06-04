"""确定性校验器（P2，见 docs/P2-最小闭环.md §5.2）。**零 LLM。**

校验 `wiki/` 下的页面三件事：

1. **frontmatter** —— 每张被扫页面以 `---\n…\n---` 起始、可被 yaml 解析，且必备键齐全、类型正确。
2. **wikilink 断链** —— 正文 `[[…]]` 的目标必须解析到某张 `wiki/` 页面（按 stem，大小写不敏感）。
3. **sources 解析** —— frontmatter `sources` 里每个 slug 必须对应存在的 `wiki/sources/<slug>.md`。

唯一实现在包内（决策1）：`guanlan check` 子命令、`python -m guanlan.check`、wrapper 门禁
（`gate.run_check`）三者走同一函数，单一可信源。
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from .errors import EXIT_CHECK_FAILED, EXIT_OK, GuanlanError
from .paths import require_kb_root

# config 页（非 content）：排除出 frontmatter/断链校验。仅 wiki/ 顶层的这三个文件，
# 子目录里的同名文件不算 config。SCHEMA.md 在根、不在 wiki/ 下，天然不被扫。
_CONFIG_PAGES = frozenset({"index.md", "log.md", "overview.md"})

_VALID_TYPES = frozenset({"source", "entity", "concept", "synthesis"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 非贪婪提取 [[…]]；目标里不含 ] 或换行。
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")


@dataclass(frozen=True)
class Violation:
    """单条违规。`page` 是相对知识库根的 posix 路径（如 `wiki/entities/Foo.md`）。"""

    page: str
    kind: str
    detail: str


@dataclass
class CheckResult:
    ok: bool
    pages_checked: int
    violations: list[Violation]


def _extract_frontmatter(text: str) -> tuple[str | None, str]:
    """切出 frontmatter 块与正文。

    返回 `(block, body)`：`block` 是两条 `---` 之间的原文（不含分隔线），无合法块时为 None，
    此时 `body` 为全文（断链校验仍在全文上跑）。
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    # 起始有 --- 但无闭合 → 视作无合法 frontmatter。
    return None, text


def _parse_frontmatter(block: str | None) -> tuple[dict | None, Violation | None]:
    """解析 frontmatter 块（每页只解析一次，供 frontmatter 与 sources 校验复用）。

    返回 `(meta, fatal)`：成功时 `(dict, None)`；块缺失/无法解析/非映射时 `(None, 违规)`。
    `page` 由调用方补到违规上——这里只关心解析本身。
    """
    if block is None:
        return None, Violation("", "frontmatter.block_missing", "缺 frontmatter（--- 块）")
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        return None, Violation("", "frontmatter.unparsable", f"frontmatter 无法解析：{exc}")
    if not isinstance(meta, dict):
        return None, Violation("", "frontmatter.unparsable", "frontmatter 不是键值映射")
    return meta, None


def _check_frontmatter(page: str, meta: dict) -> list[Violation]:
    """校验已解析的 frontmatter 必备键与类型，返回违规列表。"""
    violations: list[Violation] = []

    def missing(key: str) -> bool:
        if key not in meta:
            violations.append(Violation(page, "frontmatter.missing_key", f"缺 {key}"))
            return True
        return False

    def bad_type(key: str, detail: str) -> None:
        violations.append(Violation(page, "frontmatter.bad_type", f"{key} {detail}"))

    if not missing("title"):
        if not isinstance(meta["title"], str) or not meta["title"].strip():
            bad_type("title", "须为非空字符串")
    if not missing("type"):
        # 先判 str：YAML 可能给出 list/dict 等 unhashable 值，直接 `in frozenset` 会抛 TypeError。
        if not isinstance(meta["type"], str) or meta["type"] not in _VALID_TYPES:
            bad_type("type", f"须 ∈ {sorted(_VALID_TYPES)}，实为 {meta['type']!r}")
    if not missing("tags"):
        if not isinstance(meta["tags"], list):
            bad_type("tags", "须为列表")
    if not missing("sources"):
        src = meta["sources"]
        if not isinstance(src, list) or not all(isinstance(s, str) for s in src):
            bad_type("sources", "须为字符串列表")
    if not missing("last_updated"):
        lu = meta["last_updated"]
        # YAML 会把未加引号的 `2026-06-03` 解析为 datetime.date（约定模板正是不加引号），
        # 故 date 与匹配 YYYY-MM-DD 的字符串都算合法；datetime（带时间）不算。
        ok = (isinstance(lu, datetime.date) and not isinstance(lu, datetime.datetime)) or (
            isinstance(lu, str) and _DATE_RE.match(lu) is not None
        )
        if not ok:
            bad_type("last_updated", "须为 YYYY-MM-DD（日期或同格式字符串）")

    return violations


def _link_stem(target: str) -> str:
    """把 `[[…]]` 目标归一为解析键：剥 `|别名`、`#锚点` 与可选 `.md` 后缀。"""
    target = target.split("|", 1)[0].split("#", 1)[0].strip()
    target = target.replace("\\", "/").rsplit("/", 1)[-1]
    if target.lower().endswith(".md"):
        target = target[:-3]
    return target.lower()


def _check_wikilinks(page: str, body: str, link_targets: frozenset[str]) -> list[Violation]:
    violations: list[Violation] = []
    for raw in _WIKILINK_RE.findall(body):
        stem = _link_stem(raw)
        if not stem:
            continue
        if stem not in link_targets:
            violations.append(
                Violation(page, "wikilink.broken", f"[[{raw.strip()}]] 无对应页面")
            )
    return violations


def _check_sources(page: str, meta: dict | None, wiki: Path) -> list[Violation]:
    """frontmatter `sources` 里每个 slug 必须有对应的 wiki/sources/<slug>.md。"""
    if meta is None:
        return []
    sources = meta.get("sources")
    if not isinstance(sources, list):
        return []  # 类型问题已由 frontmatter 校验记账，这里不重复。

    violations: list[Violation] = []
    for slug in sources:
        if not isinstance(slug, str):
            continue
        # slug 必须解析为 wiki/sources/ 下的**直接**文件：禁止路径分隔符与 `..`，否则像
        # `../concepts/Foo` 这样的 slug 会借 is_file() 跟随 `..` 命中 sources/ 之外的页面，
        # 绕过"每个 source 必须落在 wiki/sources/"的硬约束。
        safe = "/" not in slug and "\\" not in slug and slug not in (".", "..")
        if not (safe and (wiki / "sources" / f"{slug}.md").is_file()):
            violations.append(
                Violation(
                    page,
                    "sources.unresolved",
                    f"sources 列出的 {slug!r} 无 wiki/sources/{slug}.md",
                )
            )
    return violations


def run_check(wiki: Path) -> CheckResult:
    """对 `wiki/` 下所有页面跑确定性校验，返回 `CheckResult`。

    `page` 路径相对知识库根（`wiki.parent`）输出，形如 `wiki/entities/Foo.md`。
    """
    wiki = Path(wiki)
    root = wiki.parent

    # 作为写门禁的收尾校验，必须先确认 wiki/ 还在：若 agent 删/改名了 wiki/，rglob 会得到空集
    # 而误判 ok（pages_checked=0）。缺失/非目录 → 直接判失败，不把"空扫描"当作干净。
    if not wiki.is_dir():
        return CheckResult(
            ok=False,
            pages_checked=0,
            violations=[Violation("wiki", "wiki.missing", "wiki/ 不存在或不是目录")],
        )

    all_md = sorted(p for p in wiki.rglob("*.md") if p.is_file())

    # 链接解析集 = 所有页面 stem（含 config 页），大小写不敏感。
    # 注意：扫描排除（哪些页被校验）与解析集（哪些 stem 算合法目标）是两件事。
    link_targets = frozenset(p.stem.lower() for p in all_md)

    violations: list[Violation] = []
    pages_checked = 0
    for path in all_md:
        is_config = path.parent == wiki and path.name in _CONFIG_PAGES
        if is_config:
            continue
        pages_checked += 1
        page = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        block, body = _extract_frontmatter(text)
        meta, fatal = _parse_frontmatter(block)  # 每页只解析一次。
        if fatal is not None:
            violations.append(Violation(page, fatal.kind, fatal.detail))
        else:
            violations.extend(_check_frontmatter(page, meta))
        violations.extend(_check_wikilinks(page, body, link_targets))
        violations.extend(_check_sources(page, meta, wiki))

    return CheckResult(ok=not violations, pages_checked=pages_checked, violations=violations)


def format_report(result: CheckResult, *, json_output: bool) -> str:
    """渲染校验结果：`--json` 走稳定契约；否则人类可读逐行报告。"""
    if json_output:
        return json.dumps(
            {
                "ok": result.ok,
                "pages_checked": result.pages_checked,
                "violations": [asdict(v) for v in result.violations],
            },
            ensure_ascii=False,
            indent=2,
        )

    if result.ok:
        return f"✓ check 通过：{result.pages_checked} 页，无违规。"
    lines = [f"✗ check 失败：{result.pages_checked} 页，{len(result.violations)} 条违规："]
    for v in result.violations:
        lines.append(f"    [{v.kind}] {v.page}: {v.detail}")
    return "\n".join(lines)


def check_entrypoint(root_dir: str | Path, *, json_output: bool) -> int:
    """`guanlan check` 与 `python -m guanlan.check` 的共享落地：校验 → 渲染 → 退出码。

    单一实现，避免两个入口在错误流、退出码规则、报告格式上漂移。
    """
    try:
        root = require_kb_root(root_dir, writable=False)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    result = run_check(root / "wiki")
    # --json 是机器可读契约：无论成败都走 stdout（失败由退出码表达），否则 `--json > out.json`
    # 在最需要查看的失败场景里会得到空文件。人类报告失败才转 stderr。
    stream = sys.stdout if (json_output or result.ok) else sys.stderr
    print(format_report(result, json_output=json_output), file=stream)
    return EXIT_OK if result.ok else EXIT_CHECK_FAILED


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.check` 入口（与 `guanlan check` 共享 check_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.check",
        description="确定性校验 wiki/（frontmatter + 断链 + sources）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 契约")
    args = parser.parse_args(argv)
    return check_entrypoint(args.dir, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
