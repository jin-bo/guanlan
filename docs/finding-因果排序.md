# finding 因果排序（lint/health 输出根因先于症状，零 LLM）

> 纯微改、**不单列里程碑编号**（同 P4.11 信任边界那种"借形状的小条目"处理）。源自反向评审
> （[`backlog/notes/gbrain-反向评审结论.md`](backlog/notes/gbrain-反向评审结论.md) §3）：借 gbrain
> `doctor-cause-rank.ts` 的**形状**——把非 ok 检查按**根因优先**重排、让人/Agent 先修对的那个。
> 收口落点见 [`backlog/notes/未来工作计划-反向评审收口.md`](backlog/notes/未来工作计划-反向评审收口.md) §2 轨 A。

## 0. 缺口

`guanlan lint` / `guanlan health` 原本**平铺**输出 findings（按检测先后：lint = 孤儿→断链→缺失实体→
拓扑；health = 逐页桩页/漂移→index 同步）。平铺让人/Agent 可能**先去手修症状**而非根因——典型例：
`lint.broken_link` 是症状，`lint.missing_entity`（同一目标被 ≥2 页引用却无页）才是根因，**建那一页即
消解它聚合的多条断链**。症状排在根因前，读者容易逐条手补断链而错过"建一页全消"的高杠杆动作。

## 1. 落法（只借形状）

加一道**纯展示层、零 LLM、确定性**的根因排序归口 `pages.order_findings(findings)`：按
`pages.FINDING_CAUSAL_ORDER` 给每个 finding `kind` 一个稳定 rank，输出时 `sorted` 稳定重排。
`run_lint` / `run_health` 在构造报告前各调一次，故 **CLI 文本 / `--json` / MCP `report_dict` /
Web 四处消费者同序**。

三档优先序（根因/数据完整性 → 内容/组织 → 拓扑优化）：

| 档 | kind（rank 升序） | 含义 |
|---|---|---|
| ① 根因 / 数据完整性 | `lint.missing_entity` → `lint.broken_link` → `health.index_missing_page` → `health.index_dangling` → `lint.orphan` | 修之即消解下游症状 |
| ② 内容 / 组织质量 | `health.stub_page` → `health.type_dir_mismatch` → `health.uncharted_page` | 页内/归类建议 |
| ③ 拓扑优化建议 | `lint.hub_node` → `lint.thin_intercommunity_link` → `lint.isolated_community` → `lint.bridge_edge` → `lint.cut_vertex` | 结构 nice-to-have、非坏数据 |

每条命令的报告只含**自己那套 kind**，故跨 lint/health 的交错 rank 不影响单命令内的相对序；起作用的
只是**命令内**的相对名次。

## 2. 不变量（硬约束）

- **不改 finding 集合**：同一组 finding，只换顺序。集合/计数恒等。
- **不改退出码**：仍 riding `EXIT_LINT_FINDINGS`——默认退 0、`--strict` 有 findings → 6。
- **稳定 / 字节稳定**：`sorted` 稳定，各 kind 内**既有确定性次序原样保留**（如 `broken_link` 仍按
  `(source, target)` 升序、`missing_entity` 仍按 `target` 升序）；同库同跑两次逐字节一致。
- **未登记 kind 取末档 rank**、稳定排在已登记 kind 之后——新增 finding 类型不至于被静默吞序。
- **不就地改入参**：`order_findings` 返回新列表。

## 3. 决策

- **决策（只借形状、不借实现）**：只取 gbrain 那道"根因优先重排"的**展示层形状**，**不**借其 `top_issues[]`
  截断（不丢任何 finding）、不引 LLM、不加新命令/退出码/SSE/依赖。
- **决策（唯一机械因果 = `missing_entity → broken_link`，其余为优先序）**：缺失实体是 `g.broken` 的聚合子集
  （`lint._aggregate_missing` 决策P3.2-1 同源），建页即消解其聚合的那几条断链——**真因果**，故因排在果前。
  其余档位是"先修对的那个"的**优先序**（数据完整性 > 内容/组织 > 拓扑 nice-to-have），非机械因果，不
  宣称强于此。
- **决策（跨命令不合并）**：gbrain 例「`index_missing` → `orphan`/`broken`」跨 `health` 与 `lint` 两条独立命令、
  各出独立报告，**无法在单份报告里合并**；故只在**各命令报告内**排序，并守住命令内那对真因果
  （lint 的 `missing_entity → broken_link`）。真要跨命令统一根因视图属更大的口子（候选并入未来 `doctor` 聚合，非本条）。
- **决策（落 `run_*` 而非仅 `format_report`）**：排序在报告构造期一次完成，使文本/JSON/MCP/Web 同序；
  `format_report` 不再各自排序、零分叉。`check` 的 `Violation`（门禁、非建议）**不**走此排序。

## 4. 边界（明确不做）

- 不做 LLM 根因推断、不做跨 finding 的因果图推导——只按**静态 kind 优先表**排。
- 不引入 `top_issues[]` 截断 / 严重度分数 / Brier 式评分（gbrain 预测域专属，红线对照见收口 §5）。
- 不碰 `check`（门禁顺序保持）、不碰 `raw/`、不起 LLM、不引依赖。
- 测试护栏：`tests/test_pages.py`（`order_findings` 根因先于果 / 拓扑沉底 / kind 内稳定 + 未登记取末档 /
  不就地改入参）+ `tests/test_lint.py`（`missing_entity` 先于 `broken_link` / 拓扑沉底 / 稳定可重放 /
  集合·退出码不变）+ `tests/test_health.py`（index 同步先于内容质量 / 确定性 + 集合·退出码不变）。
