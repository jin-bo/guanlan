"""图感知的结构 lint（P3，见 docs/P3-健康与图谱.md §5）。**零 LLM。**

`lint` 先 `graph.build_graph(wiki)` 拿到节点与有向边（含未解析边），再在邻接表上算三类 finding：

- **孤儿页**（`lint.orphan`）：入度为 0 的非 config 页（自环不算入链）。
- **断链**（`lint.broken_link`）：未解析的 `[[…]]`——与 `check.wikilink.broken` 同源同口径（决策P3-6）。
- **缺失实体页**（`lint.missing_entity`）：同一未解析目标被 ≥ `MISSING_ENTITY_MIN_REFS` 张不同页引用却无页。

P3.5 在同一份图上再加三类**确定性拓扑**建议（零 LLM，见 docs/P3.5-图谱分析.md §3.4）：
- **过载枢纽**（`lint.hub_node`）：无向度 ≥ 均值+`HUB_SIGMA`σ 且 ≥ `HUB_MIN_DEGREE`。
- **稀疏跨社区链接**（`lint.thin_intercommunity_link`）：一对社区仅靠单条跨社区边相连（非图论 bridge）。
- **孤岛社区**（`lint.isolated_community`）：规模 ≥2、全库社区数 >1、且与其余社区零跨边。

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
from .graph import Graph, build_graph, compute_orphans
from .graphstats import (
    HUB_SIGMA,
    detect_communities,
    hub_nodes,
    isolated_communities,
    thin_intercommunity_links,
    undirected_adjacency,
)
from .pages import Finding, report_json
from .paths import require_kb_root

__all__ = [
    "LintReport",
    "MissingEntity",
    "missing_entities",
    "run_lint",
    "format_report",
    "lint_entrypoint",
    "main",
]

# 缺失实体阈值：同一未解析目标被 ≥ 此数张**不同**页引用时，从零散断链升格为"该建页"的建议。
# 2 = "被复述过一次以上"，是术语反复出现的最低信号；低于它的单次断链留给 broken_link。
MISSING_ENTITY_MIN_REFS = 2


@dataclass(frozen=True)
class MissingEntity:
    """一个被 ≥ 阈值张**不同**页引用却无对应页的归一断链键（lint 与 heal 共用的单一聚合产物）。

    `target` 是 `pages.link_stem` 归一后的键（== `graph.Edge.target`，决策P3.2-14）；`ref_pages`
    是引用它的页面相对库根的 posix 路径，按字典序稳定排序（决策P3.2-2）。
    """

    target: str
    ref_count: int
    ref_pages: tuple[str, ...]


def _aggregate_missing(g: Graph, *, min_refs: int) -> list[MissingEntity]:
    """在**已建好的图**上聚合缺失实体：同一未解析目标被 ≥ `min_refs` 张不同页引用。

    `lint` 与 `heal` 的**唯一**聚合点——同一份 `g.broken`、同一阈值口径，永不分叉（决策P3.2-1）。
    返回按 `target` 升序的稳定列表；消费侧（heal）若需别的次序自行再排。
    """
    node_path = {n.id: n.path for n in g.nodes}
    refs_by_target: dict[str, set[str]] = defaultdict(set)
    for edge in g.broken:
        refs_by_target[edge.target].add(edge.source)
    out: list[MissingEntity] = []
    for target in sorted(refs_by_target):
        sources = refs_by_target[target]
        if len(sources) >= min_refs:
            ref_pages = tuple(sorted(node_path.get(s, s) for s in sources))
            out.append(MissingEntity(target, len(sources), ref_pages))
    return out


def missing_entities(
    wiki: Path, *, min_refs: int = MISSING_ENTITY_MIN_REFS
) -> list[MissingEntity]:
    """对 `wiki/` 建图后聚合缺失实体（结构化，零 LLM）。供 `heal` worklist 与 `lint` 共用。"""
    return _aggregate_missing(build_graph(Path(wiki)), min_refs=min_refs)


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

    # 断链：源自 graph.broken（与 check.wikilink.broken 同口径，决策P3-6）。
    for edge in sorted(g.broken, key=lambda e: (e.source, e.target)):
        source_page = node_path.get(edge.source, edge.source)
        findings.append(
            Finding(source_page, "lint.broken_link", f"[[{edge.target}]] 无对应页面")
        )

    # 缺失实体（断链的高价值子集）：跨页聚合、无单一归属页，page 留空串（消费侧据此识别全局 finding，§5.4）。
    # 走与 heal 共用的 `_aggregate_missing`（决策P3.2-1），target 升序输出不变。
    for me in _aggregate_missing(g, min_refs=MISSING_ENTITY_MIN_REFS):
        findings.append(
            Finding(
                "",
                "lint.missing_entity",
                f"[[{me.target}]] 被 {me.ref_count} 页引用却无页面，建议建 entities/{me.target}.md",
            )
        )

    # P3.5 拓扑建议（接在 orphan/broken/missing_entity 之后，保既有顺序）：在**同一份 g** 上算
    # 确定性社区与拓扑特征，三类建议同走 lint「建议非门禁」语义（决策P3.5-7）。
    findings.extend(_topology_findings(g, node_path))

    return LintReport(ok=not findings, pages_checked=len(g.nodes), findings=findings)


def _topology_findings(g: Graph, node_path: dict[str, str]) -> list[Finding]:
    """P3.5 三类拓扑 `Finding`：枢纽 / 稀疏跨社区链接 / 孤岛（确定性、零 LLM）。

    `node_path`：node_id → 相对库根 posix 路径，用于把拓扑函数返回的 stem 映射回页面路径。
    """
    # 无向邻接算一次，社区检测与三特征函数共用（免 4× 重复构建）。
    adj = undirected_adjacency(g)
    comm = detect_communities(g, adj=adj)
    findings: list[Finding] = []

    # 枢纽（god node）：度过载，记在该页（hub_nodes 已按 (-度, id) 稳定排序）。
    for nid, deg in hub_nodes(g, comm, adj=adj):
        findings.append(
            Finding(
                node_path.get(nid, nid),
                "lint.hub_node",
                f"无向度 {deg}（≥ 均值+{HUB_SIGMA:g}σ），疑为过载枢纽，考虑拆分",
            )
        )

    # 稀疏跨社区链接：一对社区仅靠单条跨社区边相连（**非图论 bridge**）。全局 finding（page=""）。
    for u, v in thin_intercommunity_links(g, comm, adj=adj):
        a, b = sorted((comm[u], comm[v]))
        findings.append(
            Finding(
                "",
                "lint.thin_intercommunity_link",
                f"社区 {a}↔{b} 仅靠 [[{u}]]—[[{v}]] 一条边互链，跨社区引用偏稀，建议补交叉引用",
            )
        )

    # 孤岛社区：规模 ≥2、全库社区数 >1、且与其余社区零跨边。全局 finding（page=""）。
    for c, members in isolated_communities(g, comm, adj=adj):
        pages = ", ".join(node_path.get(mid, mid) for mid in members)
        findings.append(
            Finding(
                "",
                "lint.isolated_community",
                f"社区{{{pages}}}与其余 wiki 零互链（孤岛），建议补桥接",
            )
        )

    return findings


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
