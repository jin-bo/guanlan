# gbrain 反向评审结论（backlog）

> 状态：**反向评审结论，非排期项**。记录对 `../llm-wiki/gbrain`（v0.42.42、TypeScript/Bun、Postgres/PGLite、~146K 页的生产级"第二大脑"，CLI + MCP/HTTP 宿主）逐条款的"借不借/怎么落"研判，供后续排期参照。**本笔记不改变现状**。
>
> **进展更新（实现后回填）**：本笔记 §10 推荐排期的**四条零-LLM 借鉴全部已实现**——§2 检索 backlink 重排 = **P5.3** ✅、§3 finding 因果排序 ✅、§4 schema detect 漂移侦测 = **P3.10** ✅、§5 源撤回恢复窗 = **P3.9** ✅；§6 检索质量回放 = `tests/test_search_quality.py` ✅、§7 矛盾检测 = **P3.7** ✅。余下 §6 成本闸（存疑借）、§7 autopilot/SkillOpt（park=E3/P6）、§8 别借（E1/E2）未变。
> 关联：[`openkb-反向评审结论.md`](openkb-反向评审结论.md)（同类反向评审先例）、[`next-milestone-and-graph-viz.md`](next-milestone-and-graph-viz.md)（反向评审收口先例）、[`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md)（检索增强线，§3 借点并入此线）、[`../../P5.0-检索层.md`](../../P5.0-检索层.md)、[`../../P3.7-语义审计.md`](../../P3.7-语义审计.md)、[`p3.7-语义审计-raw_digest写入主体未决.md`](p3.7-语义审计-raw_digest写入主体未决.md)、[`../../P6-技能蒸馏-草案.md`](../../P6-技能蒸馏-草案.md)、DESIGN §8（语义维护 / CJK 检索 / graph 增强）/ §7（路线表 E1·E2·E3）。
>
> **增量评审（2026-06-18 pull，commit `4ee530f3→9bf96db8`，v0.42.43–v0.42.51，9 版本 ~13K 行）**：见 **§11**。净新增可借两条——push-based `volunteer_context`（§11.1，强借）+ `advisor` 聚合器（§11.2，借）；外加 CI 卫生快赢（§11.3）。其余佐证或归并既有结论：spend controls 并入 §6 成本闸；honest freshness **观澜已领先**（同 §9）；git durability / federated read / DB pacing 别借（越界 / E2 / E1）。**本次同样只借形状、不借实现**。

## 0. 一句话 / 为什么记

gbrain 与观澜**同源不同量级**（同 Karpathy LLM Wiki 模式，但 gbrain 走"Postgres/PGLite + 持久向量索引 + durable 多相位后台 daemon"的生产级重路线）。它基本就是观澜 DESIGN §8 / **E1·E2·E3 那些被刻意推迟能力的参考实现**。逐条款过滤观澜红线（`guanlan/` 不携带业务智能 / 零-LLM 脚本 vs LLM 工作流 / `raw/` 不可变 + 源不回退 / markdown 唯一真相 + 派生物可重建 / 薄壳 / 故意绑 agentao / CJK 优先）后：**借鉴面很窄——大部分是对观澜"先 park 后排"路线的佐证**，真正不破红线可落的零-LLM 净新增只有 §2/§3/§4 三两条。**只借形状、不借实现**贯穿始终。

## 1. 总览表

| 条款（gbrain） | 结论 | 落点 / 去向 |
|---|---|---|
| **检索结果按反向链接/图信号确定性重排**（backlink boost，log 缩放） | 🟢 **强借**（零-LLM，观澜已有图邻接，查询期算、不落派生物） | 本笔记 §2 → 候选 `P5.x`，扩 `search.py` 排序 |
| **doctor 因果排序**（根因先于症状，`top_issues[]`） | 🟢 **小强借**（纯展示层，零-LLM） | 本笔记 §3 → `lint`/`health` finding 排序归口 |
| **`schema detect` 漂移侦测**（盘面页型聚类 vs `SCHEMA.md`） | 🟡 **窄借**（只借零-LLM detect 半，弃 LLM-suggest/mutate/migration） | 本笔记 §4 → 并入 health/lint 家族 advisory |
| **软删 + 恢复窗**（72h soft-delete → `purge` 硬删） | 🟡 **形状借**（佐证并加固源撤回） | 本笔记 §5 → 喂候选 `docs/P3.9-源撤回.md`（见 [openkb §5](openkb-反向评审结论.md)） |
| **检索质量回放评测**（录真实 query+结果 → replay 测代码回归） | 🟡 **借纪律**（落成 test fixture，非运行时命令）→ **已实现** | 本笔记 §6 → `tests/test_search_quality.py`（黄金集 P@k 回归闸） |
| **wrapper 侧成本闸**（`--max-usd`，超预算 abort） | 🟡 **存疑借**（需 1 行确认 agentao 是否已托底） | 本笔记 §6 → `ingest`/`query`/`backfill` |
| **矛盾检测**（LLM judge 配对 + 持久缓存，入 dream cycle） | 🔵 **已在路上** = P3.7；gbrain 仅佐证 + 加"缓存已判对"实现点 | 本笔记 §7 → [`P3.7-语义审计.md`](../../P3.7-语义审计.md) |
| **autopilot / 夜间富化**（daemon 多相位维护） | 🔵 **已 park** = DESIGN §8 / E3；gbrain 是带刺的反面参照 | 本笔记 §7 → E3 选型 |
| **SkillOpt**（把 `SKILL.md` 当可训练参数、按 eval 自变异） | 🔵 **关联 P6**（方向相反、自变异有风险，借 propose-only 安全形状） | 本笔记 §7 → [`P6-技能蒸馏-草案.md`](../../P6-技能蒸馏-草案.md) |
| Postgres/PGLite DB · 持久向量索引 · hybrid 向量检索 · query cache · RRF 融合 | 🅴 **E1，别借** | 本笔记 §8；DESIGN §8 检索增强 |
| durable minion 任务队列 · OAuth/HTTP MCP · 多脑租户 | 🅴 **E2，别借** | 本笔记 §8 |
| 多 provider embedding · 跨模型方差评测（Opus/Sonnet/Haiku） | 🔴 **别借**（故意绑 agentao） | 本笔记 §8 |
| schema mutate/migrate/retype 回填 · calibration/takes/Brier 评分 | 🔴 **别借**（过重 / 绑 DB / gbrain 专属预测域） | 本笔记 §8 |

> 观澜**已领先**、不该反向借（详 §9）：无状态图重建（无残留陈旧边——gbrain 自认 add-only 留 stale edge 是缺陷）、拓扑 lint（桥/割点/社区——gbrain 只把图用于检索加权、不做健康度）、`raw/` 不可变 + 快照门禁（gbrain 有软删但无等价的源不可变闸）、零-LLM/LLM 干净分档（gbrain 把结构修复与 LLM 合成揉进同一相位链）、薄壳（观澜 wrapper 不携带业务智能；gbrain **本身就是**那坨业务智能）。

## 2. 🟢 检索结果按反向链接确定性重排（强借，零-LLM）

- **形状**：gbrain hybrid 检索在 RRF 融合后叠了一串**确定性** boost——`backlink boost`（按入链数 log 缩放）、`exact-match`（标题短语/别名命中）、`salience`、`recency decay`、floor-ratio 门限。各 boost 只改名次、不改召回内核。
- **真实缺口**：观澜 P5.0 只做了 title/aliases **字段加权**（BM25F-lite：boost 只抬 `tf`），**没有用上图**。而 `graph.build_graph` 已经把邻接算好了——一页被越多页 `[[链接]]` 指向，越可能是该主题的枢纽页。
- **只借形状的落法**：把 BM25 命中页**按入链数做一道 log 缩放重排**——**零-LLM、确定性、字节稳定**，且**仍在查询期算、不落任何派生物**（不碰 P5.0 "markdown 唯一真相、无持久化派生"边界）。这是 gbrain 给观澜**最干净的一条净新增**。
- **⚠️ 须钉死**：复用同一 `tokenize` / `CorpusCache` 语料归口；boost **只改名次、不动 BM25 内核**（同 P5.0 字段加权"只抬 tf"纪律）；入链数取自 `graph` 同一邻接归口（与 `lint`/`graphstats` 不漂移）；reader / MCP 都该可用（非写，符合 P5.1 `is_read_only` 立场）；保持 byte-stable 输出（score 取整 + path tie-break）。
- **决策建议**：值得做，候选 `docs/P5.x`，并入 [`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md) 检索增强线（与"②同义词表 / E1 向量"并列为 BM25 之上的确定性增强）。**先于** E1 向量——它零基建、贴 P5.0 边界。

## 3. 🟢 finding 因果排序（小强借，纯展示层）

- **形状**：gbrain `doctor-cause-rank.ts` 把非 ok 检查**按根因优先**重排，`top_issues[]` 让 Agent 先修对的那个（根因在前、症状在后）。
- **真实缺口**：观澜 `lint` / `health` 现在是**平铺** findings。但 finding 间有因果（例：`health.index_missing_page` 是**因**，它引发的 `lint.orphan` / `broken_link` 是**果**；P3.4 reindex 修因后果自消）。平铺让人/Agent 可能先去手修症状。
- **只借形状的落法**：加一道**纯展示层的根因排序**——零-LLM、确定性，把上游因排在它引发的下游果之前。**不改 finding 集合、不改退出码**（仍 riding `EXIT_LINT_FINDINGS`），只改**输出顺序**。最小、零风险。
- **决策建议**：值得做，小到可随手落（同 `health`/`lint` 归口内排序）。不必单列里程碑；若留痕记 `docs/` 小条目。

## 4. 🟡 `schema detect` 漂移侦测（窄借，只借零-LLM 半）

- **形状**：gbrain `schema detect`（盘面文件聚类成建议页型）→ `suggest`（LLM 精修）→ `review-candidates --apply`（提升）→ `mutate`/`migrate`（改库 + retype 回填）。`path_prefix` → 页型推断是零-LLM 信号（`people/bob` → `person`）。
- **真实缺口**：观澜 `SCHEMA.md` 是**单文件人写配置**，无任何机制提醒"盘面已漂移出 `SCHEMA.md` 定义"（例：`concepts/` 下堆了 30 页但 `SCHEMA.md` 没定义 concept 型）。
- **只借形状的关键**：**只借最前面那道零-LLM detect**，做成 advisory（同 `health`/`lint` 档，riding `EXIT_LINT_FINDINGS`），报页型/目录与 `SCHEMA.md` 声明的漂移。**弃**后面 LLM-suggest + mutate/migration/retype 回填——过重、绑 DB、且 mutate 改内容越"wrapper 不携带业务智能"线。观澜 P3.4 reindex 的 **dir→section** 已是同一直觉（目录即结构信号），可复用其 dir 解析归口。
- **决策建议**：可做，但**排在 §2/§3 之后**（漂移侦测是 nice-to-have，不如检索重排/因果排序刚需）。候选并入 health/lint 家族，非新命令。

## 5. 🟡 软删 + 恢复窗（形状借，喂源撤回）

- **形状**：gbrain 删除走**软删 + 72h 恢复窗**——`pages` 行标 `deleted_at`/`archived`，`purge` 相位（cycle 末步）才硬删过期项；运营可恢复近期误删。
- **关联缺口**：观澜**无路**撤回误摄/已撤稿的源（`raw/` 不可变 + 源不回退把"只增"焊死）。OpenKB §5 `remove` 已把"源撤回"识别为运营刚需、候选 [`docs/P3.9-源撤回.md`](openkb-反向评审结论.md)（尚未起草）。
- **gbrain 加的那一点**：OpenKB `remove` 是**直接清理**；gbrain 的**恢复窗**是更稳的形状——撤回不立即硬删，先移入"回收态"（观澜无 DB，对应**移入 `.trash/` 带 TTL** 而非 `rm`），给一个反悔窗口。这与观澜"`raw/` 不可变"张力更小:不是改写源、是把源**整体移到回收区**，可恢复。
- **决策建议**：**喂进 P3.9 源撤回候选**——把"软删恢复窗"列为 `remove` 的实现选项之一（`--dry-run` 预览 + 移 `.trash/` + 可选 `--purge` 过期硬删），与 OpenKB §5 那条"删内容页谁来定"未决（同 [P3.7 raw_digest 写入主体未决](p3.7-语义审计-raw_digest写入主体未决.md) 一类）**一并拍**。本笔记不新开排期。

## 6. 🟡 检索质量回放 + 成本闸（借纪律 / 存疑借）

- **检索质量回放评测**（借纪律，非运行时命令）→ **已实现** `tests/test_search_quality.py`：gbrain `eval capture` 录真实 query+结果到 ndjson、`eval replay --against base` 用来 A/B 测代码改动是否回退检索质量（配 P@k / nDCG）。观澜现有 `tests/test_search.py` 是单测、**无检索质量回归闸**。落地形态：一个 16 页大语言模型领域微 wiki（committed `CORPUS`，页间真实 `[[wikilink]]` 喂 backlink、aliases 喂字段加权）+「query→期望命中页」黄金集（`GOLDEN`），跑产线冷算 `search_pages`（**带 P5.3 backlink 重排**），断言**确定性 P@1/recall@3/MRR 不回退** + 枢纽页 boost 上浮 + 「重排不把对的页挤下去」聚合护栏（boost 路径 primary-rank-1 命中数 ≥ 纯 BM25）+ 字节稳定 + 语料/黄金集自洽体检。**只借纪律**:确定性、零-LLM、零写盘、可 CI，**不**做 gbrain 那套运行时 `eval` 子命令 + 持久 capture（过重）。
- **wrapper 侧成本闸**（存疑借）：gbrain 每个 minion job 带 per-job USD `budget_cap`，超预算 abort（`--max-usd`、`--target-score` 配套）。观澜 `ingest`/`query`/`backfill` 走 agentao 子进程**无成本上限**。一道 wrapper 侧预算闸有用，但**成本归 agentao 托管**——**须先 1 行确认** agentao 是否已暴露 budget cap（同 [openkb §7](openkb-反向评审结论.md) invalid-YAML 那种"待 1 行确认"）：若已有则别重复；若无，wrapper 侧 `--max-usd` 是合法零-LLM 加固。低优先。

## 7. 🔵 已在路上 / 已 park（gbrain 仅佐证）

- **矛盾检测 → P3.7**：gbrain `eval suspected-contradictions` 用 LLM judge 扫页对、**持久缓存判过的对**（不重判）、并入每日 dream。这正是观澜 [`P3.7-语义审计.md`](../../P3.7-语义审计.md)（借自 swarmvault 双层分诊）。gbrain 唯一可加的实现点：**缓存已判页对**省重复 LLM 花费——记进 P3.7.x。其余（gbrain 单 LLM agent 自由漫游、报告 only 不写回）同 OpenKB linter，**再次佐证 P3.7"确定性 worklist → 门禁内复核 → 就地标注"更克制的方向**。
- **autopilot / 夜间富化 → E3**：gbrain autopilot daemon + 22 相位 dream cycle，是观澜 DESIGN §8 "nightly enrichment remain post-P5"的样板。但它也是**带刺的反面参照**：把零-LLM 结构修复（lint --fix / extract）和 LLM 合成（synthesize / consolidate / propose_takes）**揉进同一相位链**，正是观澜要避免的（零-LLM/LLM 干净分档）。E3 排期时参照其相位编排（"先修文件后建索引"的依赖序），但**保持分档**。
- **SkillOpt → P6**：gbrain SkillOpt 把 `SKILL.md` 当**可训练参数**、按 eval benchmark **自变异** skill markdown；bundled skill 安全：**propose-only、绝不自动变异**。与观澜 [`P6-技能蒸馏-草案.md`](../../P6-技能蒸馏-草案.md) **方向相反**（P6 = 把 wiki 子集蒸馏成可分发 skill；SkillOpt = 优化维护 skill 自身）。自变异有风险，**只值得借那条 propose-only 安全纪律**（自动改 skill 前先产 propose 供人审）。其余 park，记一笔。

## 8. 别借 · 分档（附理由）

- **整个 Postgres/PGLite + 持久向量索引 + hybrid 向量检索 + query cache + RRF 融合** 🅴：哲学其实**贴合**（gbrain "markdown 是真相、DB 是可重建索引"跟观澜一字不差，删源 → 软删 → 重 sync 收敛），但量级是另一回事——观澜 P5.0 故意 stateless、**无落盘派生物**。这整块 = E1（存储与检索升级）。记入 E1 选型（向量/rerank/持久索引作为可重建派生物），现在别动。
- **durable minion 队列 / OAuth / HTTP MCP / 多脑租户** 🅴：= E2（多租户/远程）。观澜 P4.9 reader + P4.10 MCP 是本地只读前驱，够了。gbrain 的 Postgres 后台任务队列（crash-safe、rate-lease、stall 检测）远超观澜单用户本地 in-memory FIFO 的需求。
- **多 provider embedding（16 recipes） / 跨模型方差评测（Opus/Sonnet/Haiku）** 🔴：撞 agentao 绑定红线（同 [openkb §7](openkb-反向评审结论.md) LiteLLM）。模型选择/成本归 agentao。
- **schema mutate/migrate/retype 回填** 🔴：过重 + 绑 DB + mutate 改内容越"wrapper 不携带业务智能"线（只借 §4 的零-LLM detect 半）。
- **calibration / takes / Brier 评分 / hindsight 叙事** 🔴：gbrain 是"个人预测打分"域（声明随时间被评判、算 Brier 分），观澜是**通用知识 wiki**，用例不可借。

## 9. 观澜已领先（不该反向借，作对照）

- **无状态图重建**：观澜 `graph` 每次从 markdown 全量重建，**无残留陈旧边**；gbrain add-only 提取（`ON CONFLICT DO NOTHING`），删链不删边，**自认留 stale edge 是缺陷**（TODOS v0.42.7 follow-up）。观澜**结构上更干净**。
- **拓扑 lint**：观澜 graphstats 做 Louvain 社区 + 图论桥/割点 + hub/silo 健康 finding；gbrain 把图**只用于检索加权**（backlink/relational boost），**不做拓扑健康度**。两者图能力在**不同轴**——观澜领先在"图谱健康分析"轴。
- **`raw/` 不可变 + 快照门禁**：观澜 `gate.snapshot_raw` → `EXIT_RAW_MUTATED` 确定性拦死任何源篡改；gbrain 有软删恢复窗但**无等价的源不可变闸**。
- **零-LLM / LLM 干净分档**：观澜 `init`/`check`/`health`/`lint`/`graph`/`reindex`/`convert` 全零-LLM，LLM 只在 `ingest`/`query`；gbrain 22 相位 dream **混编**结构修复与 LLM 合成。
- **薄壳**：观澜 `guanlan/` 不携带业务智能（业务住 skill + agentao）；gbrain **本身就是**那坨业务智能（大型 TS 代码库）。反向评审取其形、不取其重。

## 10. 建议排期顺序（park 后再排）—— ✅ 四条均已实现

1. **检索 backlink 重排（§2）** —— ✅ **已实现 = P5.3**（[`P5.3-检索backlink重排.md`](../../P5.3-检索backlink重排.md)），并入 [`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md) 第④档。
2. **finding 因果排序（§3）** —— ✅ **已实现**（`pages.order_findings` 归口，[`finding-因果排序.md`](../../finding-因果排序.md)）。
3. **`schema detect` 漂移侦测（§4）** —— ✅ **已实现 = P3.10**（[`P3.10-页型目录一致性.md`](../../P3.10-页型目录一致性.md)，health advisory）。
4. **源撤回恢复窗（§5）** —— ✅ **已实现 = P3.9**（[`P3.9-源撤回.md`](../../P3.9-源撤回.md)，`guanlan remove` 移 `.trash/` 恢复窗）。

其余（§6 成本闸 实证驱动 / §7 已在路上[P3.7 已实现]或已 park[E3/P6] / §8 别借[E1/E2]）不主动排。

## 11. 增量评审：v0.42.43–v0.42.51（2026-06-18 pull）

> 范围：本次 pull `4ee530f3→9bf96db8`，9 版本 ~13K 行。绝大部分是 gbrain 的 Postgres/jobs/minions/sync 重型管线（撞 E1/E2，无 DB 映射不过来）。过红线后净新增可借两条（§11.1/§11.2）+ 一条基建快赢（§11.3），其余佐证或归并既有结论。

| 条款（gbrain 本次） | 结论 | 落点 |
|---|---|---|
| **push-based context / `volunteer_context`**（v0.42.43，零-LLM 主动献页） | 🟢 **强借**（零-LLM、查询期算、不落派生物、复用 index/aliases + 现成只读 MCP） | §11.1 → 候选 P6.x，并入检索增强线 |
| **`gbrain advisor`**（v0.42.47，只读排序"接下来做什么"+ fix 命令） | 🟢 **借**（展示/聚合层，零-LLM，聚合 check/health/lint/audit） | §11.2 → health/lint 家族聚合，承 §3 |
| **CI 卫生**（v0.42.50，concurrency 取消旧跑 / job timeout / actionlint） | 🟢 **快赢**（纯仓库基建，已有 ci.yml/release.yml） | §11.3 |
| **brain-resident skillpack**（v0.42.47，脑库自带 skill 包） | 🔵 **关联 P6**（与"引擎装一次"张力，只借"脑带操作手册"形状） | §11.4 → P6 |
| **spend controls / delta-aware 估价**（v0.42.45） | 🟡 **并入 §6 成本闸**（新增可借：自描述闸消息 + 非交互 auto-defer；仍待 agentao 1 行确认） | §6 |
| **honest freshness / checkpoint**（v0.42.51） | 🔵 **观澜已领先**（`raw_digest`+`audit` 更干净；gbrain 新意仅"live lock 区分跑/卡"= 并发长 sync，同步 CLI 无此场景） | §9 |
| git durability 自托管（v0.42.48）/ federated read scope（v0.42.46）/ DB-contention pacing（v0.42.49） | 🅴 **别借** | 越界（不替用户管 git）/ E2（单本地用户无授权）/ E1（无 DB） |

### 11.1 🟢 push-based context / `volunteer_context`（强借，零-LLM）

- **形状**：gbrain 把检索从 pull-only 反转——零-LLM、确定性地从近 N 轮对话抽实体（大写串 / `@handle` / 近期+频次显著性）→ 经 alias 表 / 精确标题 / slug 后缀解析 → 带**诚实置信度**（alias 0.9 / 精确标题 0.8 / slug 后缀 0.6，多轮或最新轮 +0.05）主动"献出"页指针 + 一句模板 rationale；门槛 0.7、最多 3 页（硬顶 5）、去重已出现 slug。三通道（reflex 引擎内自动 / `volunteer_context` op / `watch` 流）共享同一零-LLM 核。
- **真实缺口**：观澜 `query` 仍是纯 pull——Agent 得"想到去问"才读 index；这恰是 Karpathy wiki"别每次重 RAG"卖点里观澜尚缺的主动面。
- **只借形状的落法**：零-LLM `guanlan volunteer-context` + **挂到 P4.10 已有的只读 MCP server**（加 `volunteer_context` 工具，几乎零架构成本）。解析表**直接复用 `index.md` + 各页 `aliases`**（观澜天然就有 gbrain 的 alias 表 / 标题表）；置信度刻度照抄起步。**全程查询期算、不落任何派生物**（守 P5.0 边界）；reader/MCP 皆可用（符 P5.1 `is_read_only`）；byte-stable 输出。
- **暂缓**：gbrain 的 volunteered-vs-used 反馈日志依赖 DB——观澜无库，先不做或轻量文件日志，低优先；privacy fence strip 观澜无对应物，N/A。
- **决策**：值得做，是本次 pull **最干净的净新增**（同 §2 backlink 重排的成色）。候选并入 [`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md) 检索增强线 / 候选 P6.x。

### 11.2 🟢 `gbrain advisor`（借，展示/聚合层，零-LLM）

- **形状**：`gbrain advisor` 读脑库状态，回**排序的只读"接下来该做什么"**——每条带 severity + 现成 `fix.command_argv`；`--json` + 按严重度 exit code；配薄 SKILL + weekly cron recipe（只报"自上次以来新增"、动手前必问）。严格只读、`--apply` 也要显式确认。
- **真实缺口**：观澜 `check` / `health` / `lint` / `audit` 是**四个各自独立**的诊断命令，各吐平铺 findings，**无一个跨命令、按杠杆排序的"这本 KB 现在最该做的 1-3 件事"聚合器 + 可粘贴 fix 命令**。这是 §3 finding 因果排序（已实现，单命令内排序）的**跨命令上卷**。
- **只借形状的落法**：零-LLM 聚合器，归并已有信号并按严重度排序，每条配现成命令——stub/孤儿/断链（health/lint）、missing-entity≥N→`heal`、index 漂移→`reindex`、source-drift（audit `raw_digest` 对不上）→ re-ingest、未 convert 的 `raw/`、未 resolved 的 `## ⚠️ 矛盾与存疑`。复用 `--strict` exit 6 口径。薄 SKILL + cron"新增 delta"。只读、动手前问——对齐 P4.15 `ask_user` 人在环。
- **决策**：值得做，落在 health/lint 家族（聚合命令或 `health --advise`），非重活。承 §3 因果排序。

### 11.3 🟢 CI 卫生（快赢，纯基建）

观澜有 `.github/workflows/{ci,release}.yml`。直接抄三样低成本：`concurrency` 按 PR 取消被取代的旧跑、每 job `timeout-minutes`（避免卡死 job 跑满 6h）、`actionlint`(SHA-pin) 在改 workflow 时校验 YAML。十几行纯收益，与里程碑无关、随手可落。

### 11.4 🔵 brain-resident skillpack（关联 P6）

gbrain 让脑库**自带 skillpack**（随该库版本化、连入的 harness 即被 offer）。与观澜"引擎装一次、不随每个库复制"（`SCHEMA.md` 才是每库配置）**有张力**，不照搬。但"脑库随身带操作手册"的**形状**与 [`../../P6-技能蒸馏-草案.md`](../../P6-技能蒸馏-草案.md)（把 wiki 子集蒸馏成可分发 skill）邻接——记一笔，park 到 P6 选型时连同 [`p6-技能蒸馏-工作法归属未决.md`](p6-技能蒸馏-工作法归属未决.md) 一并拍。
