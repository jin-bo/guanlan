"""图感知的结构 lint（P3，见 docs/P3-健康与图谱.md §5）。**零 LLM。**

`lint` 先 `graph.build_graph(wiki)` 拿到节点与有向边（含未解析边），再在邻接表上算三类 finding：

- **孤儿页**（`lint.orphan`）：入度为 0 的非 config 页（自环不算入链）。
- **断链**（`lint.broken_link`）：未解析的 `[[…]]`——与 `check.wikilink.broken` 同源同口径（决策P3-6）。
- **缺失实体页**（`lint.missing_entity`）：同一未解析目标被 ≥ `MISSING_ENTITY_MIN_REFS` 张不同页引用却无页。

P3.5 在同一份图上再加三类**确定性拓扑**建议（零 LLM，见 docs/P3.5-图谱分析.md §3.4）：
- **过载枢纽**（`lint.hub_node`）：无向度 ≥ 均值+`HUB_SIGMA`σ 且 ≥ `HUB_MIN_DEGREE`。
- **稀疏跨社区链接**（`lint.thin_intercommunity_link`）：一对社区仅靠单条跨社区边相连（非图论 bridge）。
- **孤岛社区**（`lint.isolated_community`）：规模 ≥2、全库社区数 >1、且与其余社区零跨边。

P3.6 在同一份图上再加两类**确定性图论割边/割点**建议（零 LLM，见 docs/P3.6-图论桥与割点.md §3.4）：
- **割边**（`lint.bridge_edge`）：删之 wiki 图谱断为两块的「单点故障边」（图论 bridge，与 thin link 正交）。
- **割点**（`lint.cut_vertex`）：删之 wiki 图谱断为多块的「关节点」（articulation point）。

findings 是**建议性**（决策P3-4）：默认退 0，`--strict` 下有 findings → `EXIT_LINT_FINDINGS(6)`。
只做结构 lint，语义 lint（矛盾复检/过期论断/资料缺口，需 LLM）属 P3 之后。

输出按 `pages.order_findings` 做**因果排序**（纯展示层、零 LLM、确定性）：根因 `lint.missing_entity`
排在其果 `lint.broken_link` 之前、拓扑优化建议沉底——不改 finding 集合/退出码，只改顺序
（gbrain 反向评审 §3 借形状，见 docs/finding-因果排序.md）。
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
    fragile_topology,
    hub_nodes,
    isolated_communities,
    thin_intercommunity_links,
    undirected_adjacency,
)
from .pages import Finding, order_findings, report_json
from .paths import require_kb_root
from .search import tokenize

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

# P3.11：断链「最近页」建议的 token-overlap（Jaccard）下限。低于它不附建议（宁缺勿误，决策P3.11-3）。
# 0.5 = 候选键 token 集与断链 target 的交并比过半：CJK 2-gram 重叠（如 多头注意力机制↔多头注意力）
# 与「共享整词」（如 attention↔self-attention）都能过，而拼写差异无共享 token、得 0、自然不建议。
SUGGESTION_MIN_OVERLAP = 0.5


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


def _build_suggestion_index(
    g: Graph,
) -> dict[str, list[tuple[str, frozenset[str]]]]:
    """倒排索引 `token → [(页路径, 该字段 token 集), …]`，作断链「最近页」候选（P3.11，零 LLM）。

    候选字段 = 每页 stem(`Node.id`) / title(`Node.title`)——`g.nodes` 现成、**真零额外扫描**（决策P3.11-2）；
    aliases 留可选后续（须另跑 `alias_index`，决策P3.11-2a，本阶段不取）。**按字段分开、不并袋**
    （决策P3.11-3a）：否则英文 kebab slug stem 的无关 token 会稀释 CJK title 的强匹配（观澜「slug=ASCII、
    title=中文」是常态）。**倒排**（决策P3.11-4a）让 `_suggest_nearest` 只比对与 target 共享 ≥1 token 的
    候选字段、而非全库扫描——把成本从 O(断链数 × 页数) 降到 O(断链数 × 共享 token 的候选字段数)。
    """
    inverted: dict[str, list[tuple[str, frozenset[str]]]] = defaultdict(list)
    for n in g.nodes:
        for field in (frozenset(tokenize(n.id)), frozenset(tokenize(n.title))):
            for tok in field:
                inverted[tok].append((n.path, field))
    return inverted


def _suggest_nearest(
    target: str,
    inverted: dict[str, list[tuple[str, frozenset[str]]]],
    *,
    exclude: frozenset[str] = frozenset(),
) -> str | None:
    """对断链 `target` 找**任一字段** token-overlap（Jaccard）最高的既有页，≥ 阈值返回相对路径否则 None。

    确定性、零 LLM：复用 `search.tokenize`（CJK-2-gram 单一归口，决策P5.0-18），**不做编辑距离**
    （拼写差异无共享 token、得 0、自然不建议，决策P3.11-3）；逐字段算 Jaccard 取最大、不并袋（决策P3.11-3a）。
    `exclude` = 引用该 target 的页（含写有该断链的源页本身）——**不把引用页/链接所在页建议成它自己的
    解析目标**（决策P3.11-4a）。只比对与 target 共享 token 的候选字段；同最高分取**字典序最小 path**，
    故与候选/token 的访问次序无关、输出稳定。
    """
    target_tokens = frozenset(tokenize(target))
    if not target_tokens:
        return None
    seen: set[tuple[str, frozenset[str]]] = set()
    best_path: str | None = None
    best_score = 0.0
    for tok in target_tokens:
        for entry in inverted.get(tok, ()):
            if entry in seen:  # 同一 (页, 字段) 可经多个共享 token 命中，只算一次。
                continue
            seen.add(entry)
            path, field = entry
            if path in exclude:
                continue
            score = len(target_tokens & field) / len(target_tokens | field)
            if score > best_score or (
                score == best_score and best_path is not None and path < best_path
            ):
                best_score, best_path = score, path
    return best_path if best_score >= SUGGESTION_MIN_OVERLAP else None


def _suggestion_suffix(path: str) -> str:
    """断链/缺失实体 detail 的「疑似已有页」建议后缀（人读层；结构化值另进 `Finding.suggestion`）。

    措辞把它定位为**与「建页」并列的可选项**（若同义则并入既有页、而非新建），避免 missing_entity 的
    「建议建 entities/X.md」与本后缀读成两条矛盾指令（决策P3.11-3，review 质量项）。
    """
    return f"（疑似已有页 {path}：若同义，宜并入其 aliases 而非新建页）"


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

    # P3.11：断链「最近页」建议（只读 advisory）。倒排候选索引 + 按 target 记忆化（决策P3.11-2/-3a/-4a）：
    # 同一 target 的多条断链 / 缺失实体共用一次计算；排除集 = 引用该 target 的页（含写有该链接的源页本身），
    # 故不把引用页 / 链接所在页建议成它自己的解析目标。refs_by_target 覆盖全部断链 target（missing 是其子集）。
    sugg_index = _build_suggestion_index(g)
    refs_by_target: dict[str, set[str]] = defaultdict(set)
    for edge in g.broken:
        refs_by_target[edge.target].add(node_path.get(edge.source, edge.source))
    suggestion_by_target = {
        target: _suggest_nearest(target, sugg_index, exclude=frozenset(srcs))
        for target, srcs in refs_by_target.items()
    }

    # 断链：源自 graph.broken（与 check.wikilink.broken 同口径，决策P3-6）。
    for edge in sorted(g.broken, key=lambda e: (e.source, e.target)):
        source_page = node_path.get(edge.source, edge.source)
        suggestion = suggestion_by_target.get(edge.target)
        detail = f"[[{edge.target}]] 无对应页面"
        if suggestion:
            detail += _suggestion_suffix(suggestion)
        findings.append(
            Finding(source_page, "lint.broken_link", detail, suggestion=suggestion)
        )

    # 缺失实体（断链的高价值子集）：跨页聚合、无单一归属页，page 留空串（消费侧据此识别全局 finding，§5.4）。
    # 走与 heal 共用的 `_aggregate_missing`（决策P3.2-1），target 升序输出不变。
    for me in _aggregate_missing(g, min_refs=MISSING_ENTITY_MIN_REFS):
        suggestion = suggestion_by_target.get(me.target)
        detail = (
            f"[[{me.target}]] 被 {me.ref_count} 页引用却无页面，建议建 entities/{me.target}.md"
        )
        if suggestion:
            detail += _suggestion_suffix(suggestion)
        findings.append(
            Finding("", "lint.missing_entity", detail, suggestion=suggestion)
        )

    # P3.5 拓扑建议（接在 orphan/broken/missing_entity 之后，保既有顺序）：在**同一份 g** 上算
    # 确定性社区与拓扑特征，三类建议同走 lint「建议非门禁」语义（决策P3.5-7）。
    findings.extend(_topology_findings(g, node_path))

    # finding 因果排序（纯展示层、零 LLM）：把根因（missing_entity）排在其果（broken_link）之前、
    # 拓扑优化建议沉底。稳定排序保各 kind 内既有确定性次序不变（gbrain §3，见 pages.order_findings）。
    return LintReport(
        ok=not findings, pages_checked=len(g.nodes), findings=order_findings(findings)
    )


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

    # P3.6 图论割边/割点（接在 P3.5 三类之后，保既有顺序）：同一份无向邻接上算「删之即断」的单点故障，
    # 与 thin_intercommunity_link 正交、不去重（决策P3.6-2）。
    frag = fragile_topology(g, adj=adj)

    # 割边（单点故障边）：删之图谱断为两块。全局 finding（page=""，同 thin link 体例）。
    for u, v in frag.bridges:
        findings.append(
            Finding(
                "",
                "lint.bridge_edge",
                f"[[{u}]]—[[{v}]] 是割边：删之 wiki 图谱即断为两块（单点故障），建议补冗余交叉引用",
            )
        )

    # 割点（关节点）：删之图谱断为多块。记在该页（同 hub_node 体例）。
    for nid in frag.cut_vertices:
        findings.append(
            Finding(
                node_path.get(nid, nid),
                "lint.cut_vertex",
                "本页是割点：删之 wiki 图谱断为多块（关节点），建议为其邻接页补旁路链接",
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
