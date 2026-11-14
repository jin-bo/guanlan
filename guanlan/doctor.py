"""Web 体检（P4.34，见 docs/P4.34-Web体检.md）：把 check / health / lint 与零 LLM 预览汇成一张分组清单。**零 LLM、只读。**

不新增任何检测规则——每一条都来自已有检测，这里只做三件事：

1. **分组**：必须修（写门禁口径的阻断违规）/ 建议处理 / 结构提示。阻断判定直接复用 `gate.partition_violations`，
   不另维护阻断类型白名单（check 新增阻断类型时体检自动跟上）。
2. **去重**：断链在 check、lint 与「缺失实体」里各报一遍。这里只取一份：链接图的结构化断链边（`graph.broken`，
   与 `check.wikilink.broken` 同口径，决策P3-6），按折叠后的目标聚合；被多页引用的目标归「高频缺页」（= heal worklist），
   其余归「其余断链」。不解析任何中文 `detail`。
3. **计数按最终展示项**：断链 / 缺页按目标数、漂移源按组数、待入库按文件数；`total` 不含结构提示。

CLI 的 `check` / `health` / `lint` 不受影响（退出码契约不变）；本模块只供 Web `GET /api/doctor` 用。
"""

from __future__ import annotations

import time
from pathlib import Path

from .audit import audit_preview
from .check import run_check
from .gate import partition_violations
from .graph import build_graph
from .health import run_health
from .lint import (
    MISSING_ENTITY_MIN_REFS,
    broken_link_detail,
    lint_from_graph,
    missing_entities_from_graph,
    missing_entity_detail,
)
from .pages import TOPOLOGY_FINDING_KINDS
from .rawio import raw_ingest_status

__all__ = ["run_doctor", "INDEX_KINDS", "TOPOLOGY_KINDS"]

INDEX_KINDS = ("health.index_missing_page", "health.index_dangling")
# lint 里只是「图谱上值得看一眼」的拓扑建议：不参与总评。清单与 lint 因果排序的第三档同一份。
TOPOLOGY_KINDS = TOPOLOGY_FINDING_KINDS
# 已由图上结构化断链边按目标聚合的那份代表，lint 里的同类 finding 不再重复列出。
_DEDUPED_LINT_KINDS = ("lint.broken_link", "lint.missing_entity")


def _finding(f) -> dict:
    out = {"page": f.page, "kind": f.kind, "detail": f.detail}
    if f.suggestion:
        out["suggestion"] = f.suggestion
    return out


def _pending_raw(root: Path) -> list[dict]:
    """`raw/*.md` 中还没有对应 source 页的（与 Web 补录列表的「未收录」同一判据：`raw_ingest_status`）。"""
    return [
        {"page": f"raw/{path.name}", "name": path.name}
        for path, ingested in raw_ingest_status(root)
        if not ingested
    ]


def _category(key: str, section: str, items: list[dict], action: str | None = None) -> dict:
    return {"key": key, "section": section, "count": len(items), "items": items, "action": action}


def run_doctor(root: Path) -> dict:
    """跑一次体检，返回 `{ok, pages_checked, total, elapsed_s, categories: [...]}`。

    `categories` 按展示顺序排列，每项 `{key, section, count, items, action}`：`section` ∈ blocking / suggest / structure；
    `action` 是修复入口（heal / ingest / audit / graph，或 None）。`count == len(items)`，即最终展示项数。
    """
    started = time.monotonic()
    root = Path(root)
    wiki = root / "wiki"

    check = run_check(wiki)
    blocking, _warnings = partition_violations(check.violations)  # 断链（警告）不在这里：取下面图上的结构化那份

    g = build_graph(wiki)
    suggestions: dict[str, str | None] = {}
    lint = lint_from_graph(g, suggestions=suggestions)
    # 全部断链按目标聚合（min_refs=1，与 heal / lint 同一聚合点 `_aggregate_missing`）；
    # 被 ≥ MISSING_ENTITY_MIN_REFS 页引用的归「高频缺页」，其余归「其余断链」。
    by_target = missing_entities_from_graph(g, min_refs=1)

    def _item(m) -> dict:
        sugg = suggestions.get(m.target)
        frequent = m.ref_count >= MISSING_ENTITY_MIN_REFS
        detail = (
            missing_entity_detail(m.target, m.ref_count, sugg)
            if frequent
            else broken_link_detail(m.target, sugg)
        )
        return {
            "target": m.target,
            "ref_pages": list(m.ref_pages),
            "detail": detail,
            **({"suggestion": sugg} if sugg else {}),
        }

    missing = [m for m in by_target if m.ref_count >= MISSING_ENTITY_MIN_REFS]
    # 与 heal worklist 同序（引用数降序、target 升序）：前端每类只渲染前若干条，引用最多的要排在前面。
    missing.sort(key=lambda m: (-m.ref_count, m.target))
    missing_items = [_item(m) for m in missing]
    broken_items = [_item(m) for m in by_target if m.ref_count < MISSING_ENTITY_MIN_REFS]

    health = run_health(wiki)
    index_items = [_finding(f) for f in health.findings if f.kind in INDEX_KINDS]
    quality_items = [_finding(f) for f in health.findings if f.kind not in INDEX_KINDS]
    # lint 的断链 / 缺失实体已由上面按目标聚合的那份代表（去重）；拓扑建议归结构提示；
    # 其余（孤页，以及将来新增的 lint 类型）一律落「页面质量」——兜底计入，不因白名单漏报。
    quality_items += [
        _finding(f) for f in lint.findings if f.kind not in _DEDUPED_LINT_KINDS and f.kind not in TOPOLOGY_KINDS
    ]
    topology_items = [_finding(f) for f in lint.findings if f.kind in TOPOLOGY_KINDS]

    preview = audit_preview(wiki)
    drift_items = list(preview.get("groups") or []) + list(preview.get("postponed") or [])  # 全量：limit 只管一批审几组

    categories = [
        _category("blocking", "blocking", [{"page": v.page, "kind": v.kind, "detail": v.detail} for v in blocking]),
        _category("missing_entity", "suggest", missing_items, "heal"),
        _category("broken_link", "suggest", broken_items, "heal"),  # 前端以 min_refs=1 打开 heal 预览（原顶栏 heal 可调到 1）
        _category("pending_raw", "suggest", _pending_raw(root), "ingest"),
        _category("drift", "suggest", drift_items, "audit"),
        _category("index", "suggest", index_items),
        _category("quality", "suggest", quality_items),
        _category("topology", "structure", topology_items, "graph"),
    ]
    total = sum(c["count"] for c in categories if c["section"] != "structure")
    return {
        "ok": not blocking,
        "pages_checked": check.pages_checked,
        "total": total,
        "elapsed_s": round(time.monotonic() - started, 2),
        "categories": categories,
    }
