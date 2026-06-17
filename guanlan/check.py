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
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .errors import EXIT_CHECK_FAILED, EXIT_OK, GuanlanError
from .pages import (
    VALID_TYPES,
    Violation,
    WIKILINK_RE,
    iter_pages,
    link_resolution_index,
    link_stem,
    load_page_text,
    page_stem_index,
    parse_frontmatter,
    report_json,
    resolve_owner,
    split_frontmatter,
)
from .paths import require_kb_root

# Violation 自 P3 起单一定义在 pages.py（共享原语）；此处 re-export 保持 `from guanlan.check
# import Violation` 与 gate.py 的 `from .check import Violation` 不变。
__all__ = ["CheckResult", "Violation", "run_check", "format_report", "check_entrypoint", "main"]

# 合法页型集自 P3.10 起归口 pages.VALID_TYPES（check/health 共用、不分叉）；保留 `_VALID_TYPES`
# 别名使本文件内既有引用零-behavior-change（决策P3.10-3 的机会性清理）。
_VALID_TYPES = VALID_TYPES
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class CheckResult:
    ok: bool
    pages_checked: int
    violations: list[Violation]


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


def _check_wikilinks(page: str, body: str, idx: dict[str, str]) -> list[Violation]:
    """正文 `[[…]]` 断链校验：经 `resolve_owner`（精确 + fold 兜底）皆不中 → broken（P3.8）。

    `idx` = `link_resolution_index`（精确 stem/别名 ∪ 安全 fold variant → owner path）。**断链判据
    一律走 `resolve_owner`**，不再用 `link_stem(raw) in 键集`（会漏 fold 兜底命中，决策P3.8-3）。
    """
    violations: list[Violation] = []
    for raw in WIKILINK_RE.findall(body):
        stem = link_stem(raw)
        if not stem:
            continue  # 空键（如 [[|别名]]/[[#锚]]）不校验，与历史行为一致。
        if resolve_owner(raw, idx) is None:
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


def _check_aliases_type(page: str, meta: dict) -> tuple[list[Violation], list[str]]:
    """校验 frontmatter `aliases` 类型，返回 `(violations, 归一别名键)`（P3.1，决策P3.1-4）。

    `aliases` 可选——缺键合法、返回空。存在时须为**非空字符串列表**，否则记一条 `bad_type`
    （阻断，已被自愈 prompt 覆盖）。归一用 `link_stem`，与 `[[…]]` 查找口径对称（决策P3.1-3）。
    """
    if "aliases" not in meta:
        return [], []
    raw = meta["aliases"]
    if not isinstance(raw, list) or not all(isinstance(a, str) and a.strip() for a in raw):
        return [Violation(page, "frontmatter.bad_type", "aliases 须为非空字符串列表")], []
    return [], [k for k in (link_stem(a) for a in raw) if k]


def _check_aliases_global(
    alias_owners: dict[str, list[str]], page_stems: frozenset[str]
) -> list[Violation]:
    """别名命名空间全局唯一校验（跨页聚合，P3.1 决策P3.1-4）。**纯算术、零 LLM。**

    别名与页面 stem 同一解析命名空间，歧义会破坏确定性解析，故两类违规均阻断：
      - `aliases.collides_stem`：归一别名撞某现有页 stem；
      - `aliases.duplicate`：同一归一别名在库内被声明 ≥2 次（跨页或同页）。
    `page_stems` 是纯页面 stem 集（不含别名），用于撞名判定。输出按页排序，确定可重建。
    """
    violations: list[Violation] = []
    for alias in sorted(alias_owners):
        owners = alias_owners[alias]
        unique_owners = sorted(set(owners))
        if alias in page_stems:
            for page in unique_owners:
                violations.append(
                    Violation(
                        page,
                        "aliases.collides_stem",
                        f"别名 {alias!r} 与现有页面 stem 同名，会产生解析歧义",
                    )
                )
        if len(owners) > 1:  # 跨页或同页重复声明同一别名
            for page in unique_owners:
                violations.append(
                    Violation(
                        page,
                        "aliases.duplicate",
                        f"别名 {alias!r} 在库内被声明 {len(owners)} 次（须全局唯一）",
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

    # 第一遍：逐页**只读盘一次**，攒下 (page, body, meta, fatal) 校验记录与 (path, 容错 meta) 解析对。
    # 把解析对透传给 link_resolution_index 复用——免「主循环读一遍 + 解析表内部 alias_index 又
    # iter_pages+load_page 把整库再读一遍」的二次读盘/二次目录遍历（每次 ingest 跑 2–4 次 run_check）。
    #
    # 解析表必须与 graph/heal/Web **同口径（容错档 load_page，libyaml）**，否则破 `broken≡check` 不变式
    # （决策P3.8-2/P3-6）：严格档（纯 Python SafeLoader，供 frontmatter.unparsable 报错文本确定性）与容错档
    # （libyaml CSafeLoader）对**「是否可解析」会分歧**——如 flow 序列里的字面 TAB（`aliases: [a\tb]`），
    # libyaml 收、纯 Python 抛。故**绝不把严格 meta 塞进解析表**（会让 check 的解析表与 graph 不一致、对这类
    # 页误报 wikilink.broken）。改为对**已读文本**走容错档 `load_page_text` 解析入 loaded：它与 `load_page`
    # 逐字等价（文本已以 utf-8 成功解码——非 UTF-8 页严格 read_text 早抛，与原主循环同款），令 loaded **逐页
    # 等同 load_page** → `link_resolution_index(wiki, loaded=loaded)` 与 `link_resolution_index(wiki)` 逐字节相同。
    # 省下的是二次**读盘+遍历**，容错档那次 libyaml 解析（廉价）仍保留以维持口径一致。
    parsed: list[tuple[str, str, dict | None, Violation | None]] = []
    loaded: list[tuple[Path, dict | None]] = []
    for path in iter_pages(wiki):
        page = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        block, body = split_frontmatter(text)
        meta, fatal = parse_frontmatter(block)  # 严格档：违规判定（坏块硬报，纯 Python loader）。
        parsed.append((page, body, meta, fatal))
        loaded.append((path, load_page_text(text)[0]))  # 容错档：解析表口径，与 graph/heal/Web 一致。

    # 链接解析表 = 精确 stem/别名（含 config 页）∪ 安全 fold variant → owner path（P3.8，决策P3.8-3）。
    # 注意：扫描排除（哪些页被校验，iter_pages 排 config）与解析表（哪些键算合法目标，含 config）是两件
    # 事——口径单一归口 pages.py，断链判定（resolve_owner）与 graph/heal/Web 完全一致（决策P3-2/P3-6）。
    idx = link_resolution_index(wiki, loaded=loaded)  # 复用上面已读文本的容错 meta，免整库二次读盘。
    # 别名撞名判定要的是**纯页面 stem 集**（不含别名/fold），与解析表 idx 不同（决策P3.1-4）。
    # page_stem_index 只 rglob 取 stem、不读 YAML，不在文本复用之列。
    page_stems = frozenset(page_stem_index(wiki))

    # 第二遍：在已解析记录上跑逐页校验。违规产出顺序（逐页 frontmatter/alias/wikilink/sources，
    # 末尾全局 alias）与原单遍实现逐字一致。
    violations: list[Violation] = []
    alias_owners: dict[str, list[str]] = defaultdict(list)  # 归一别名 → 声明页（含重复）
    for page, body, meta, fatal in parsed:
        if fatal is not None:
            violations.append(Violation(page, fatal.kind, fatal.detail))
        else:
            violations.extend(_check_frontmatter(page, meta))
            alias_vios, alias_keys = _check_aliases_type(page, meta)
            violations.extend(alias_vios)
            for key in alias_keys:
                alias_owners[key].append(page)
        violations.extend(_check_wikilinks(page, body, idx))
        violations.extend(_check_sources(page, meta, wiki))

    violations.extend(_check_aliases_global(alias_owners, page_stems))

    return CheckResult(ok=not violations, pages_checked=len(parsed), violations=violations)


def format_report(result: CheckResult, *, json_output: bool) -> str:
    """渲染校验结果：`--json` 走稳定契约；否则人类可读逐行报告。"""
    if json_output:
        return report_json(
            ok=result.ok,
            pages_checked=result.pages_checked,
            items_key="violations",
            items=result.violations,
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
