"""图感知的结构 lint（P3，见 docs/P3-健康与图谱.md §5）。**零 LLM。**

`lint` 先 `graph.build_graph(wiki)` 拿到节点与有向边（含未解析边），再在邻接表上算三类 finding：

- **孤儿页**（`lint.orphan`）：入度为 0 的非 config 页（自环不算入链）。
- **断链**（`lint.broken_link`）：未解析的 `[[…]]`——与 `check.wikilink.broken` 同源同口径（决策P3-6）。
- **缺失实体页**（`lint.missing_entity`）：同一未解析目标被 ≥ `MISSING_ENTITY_MIN_REFS` 张不同页引用却无页。

findings 是**建议性**（决策P3-4）：默认退 0，`--strict` 下有 findings → `EXIT_LINT_FINDINGS(6)`。
只做结构 lint，语义 lint（矛盾复检/过期论断/资料缺口，需 LLM）属 P3 之后。
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .errors import EXIT_LINT_FINDINGS, EXIT_OK, GuanlanError
from .graph import build_graph, compute_orphans
from .pages import Finding, report_json
from .paths import require_kb_root

__all__ = ["LintReport", "run_lint", "format_report", "lint_entrypoint", "main"]

# 缺失实体阈值：同一未解析目标被 ≥ 此数张**不同**页引用时，从零散断链升格为"该建页"的建议。
# 2 = "被复述过一次以上"，是术语反复出现的最低信号；低于它的单次断链留给 broken_link。
MISSING_ENTITY_MIN_REFS = 2


@dataclass
class LintReport:
    ok: bool
    pages_checked: int
    findings: list[Finding]


def run_lint(wiki: Path) -> LintReport:
    """对 `wiki/` 跑图级质量 lint，返回 `LintReport`（孤儿 / 断链 / 缺失实体）。"""
    wiki = Path(wiki)
    g = build_graph(wiki)
    node_path = {n.id: n.path for n in g.nodes}

    findings: list[Finding] = []

    # 孤儿（入度 0）——建议级，报告里带 type（source 页天然入度低，§5.1）。
    for node in compute_orphans(g):
        findings.append(
            Finding(node.path, "lint.orphan", f"无任何入链（type={node.type}）")
        )

    # 断链 + 缺失实体：都源自 graph.broken（与 check.wikilink.broken 同口径，决策P3-6）。
    refs_by_target: dict[str, set[str]] = defaultdict(set)
    for edge in sorted(g.broken, key=lambda e: (e.source, e.target)):
        source_page = node_path.get(edge.source, edge.source)
        findings.append(
            Finding(source_page, "lint.broken_link", f"[[{edge.target}]] 无对应页面")
        )
        refs_by_target[edge.target].add(edge.source)

    # 缺失实体（断链的高价值子集）：同一目标被 ≥ 阈值张**不同**页引用——跨页聚合、无单一归属页，
    # page 留空串（消费侧据此识别全局 finding，§5.4）。
    for target in sorted(refs_by_target):
        ref_count = len(refs_by_target[target])
        if ref_count >= MISSING_ENTITY_MIN_REFS:
            findings.append(
                Finding(
                    "",
                    "lint.missing_entity",
                    f"[[{target}]] 被 {ref_count} 页引用却无页面，建议建 entities/{target}.md",
                )
            )

    return LintReport(ok=not findings, pages_checked=len(g.nodes), findings=findings)


def format_report(report: LintReport, *, json_output: bool) -> str:
    """渲染 lint 结果：`--json` 走稳定契约；否则人类可读逐行报告。"""
    if json_output:
        return report_json(
            ok=report.ok,
            pages_checked=report.pages_checked,
            items_key="findings",
            items=report.findings,
        )

    if report.ok:
        return f"✓ lint 通过：{report.pages_checked} 页，无质量建议。"
    lines = [f"· lint 质量建议：{report.pages_checked} 页，{len(report.findings)} 条："]
    for f in report.findings:
        where = f.page or "(全局)"
        lines.append(f"    [{f.kind}] {where}: {f.detail}")
    return "\n".join(lines)


def lint_entrypoint(root_dir: str | Path, *, json_output: bool, strict: bool) -> int:
    """`guanlan lint` 的单一落地：lint → 渲染 → 退出码。

    默认退 `EXIT_OK`（findings 是建议非门禁，决策P3-4）；`--strict` 且有 findings → `EXIT_LINT_FINDINGS`。
    """
    try:
        root = require_kb_root(root_dir, writable=False)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    report = run_lint(root / "wiki")
    print(format_report(report, json_output=json_output))
    if strict and not report.ok:
        return EXIT_LINT_FINDINGS
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.lint` 入口（与 `guanlan lint` 共享 lint_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.lint",
        description="图感知结构 lint：孤儿 / 断链 / 缺失实体（零 LLM，建议非门禁）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 契约")
    parser.add_argument("--strict", action="store_true", help="有建议则以退出码 6 失败（供 CI/nightly）")
    args = parser.parse_args(argv)
    return lint_entrypoint(args.dir, json_output=args.json, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
