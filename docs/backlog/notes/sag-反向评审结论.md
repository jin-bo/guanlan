# SAG 反向评审结论（backlog）

> 状态：**反向评审结论，非排期项**。记录对 `../llm-wiki/SAG`（[Zleap-AI/SAG](https://github.com/Zleap-AI/SAG)，arXiv [2606.15971](https://arxiv.org/abs/2606.15971)；TypeScript 全栈 + React/Vite/Tailwind WebUI + Fastify + PostgreSQL/pgvector/全文 + SQL 多跳；2026-06 由旧 Next.js 仓库整体替换为 SAG workspace）的逐条"借不借/怎么落"研判，供后续排期参照。**本笔记不改变现状**。
>
> 关联：[`sag-对接-tool注入.md`](sag-对接-tool注入.md)（**本笔记 §5"DB/向量别借"的互补面**：别借 SAG 代码 ≠ 别对接 SAG 服务——经「Tool 注入」把 SAG 当规模化上游、guanlan 蒸馏成 wiki）、[`gbrain-反向评审结论.md`](gbrain-反向评审结论.md)（§2 backlink 重排借点先例；其 typed-edge relational retrieval 与本笔记 §2 同直觉）、[`openkb-反向评审结论.md`](openkb-反向评审结论.md)、[`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md)（检索增强线，本笔记 §2 并入此线）、[`../../P5.0-检索层.md`](../../P5.0-检索层.md)、[`../../P5.3-检索backlink重排.md`](../../P5.3-检索backlink重排.md)、[`../../P5.4-检索冷启动性能.md`](../../P5.4-检索冷启动性能.md)、[`../../P3.5-图谱分析.md`](../../P3.5-图谱分析.md)（`build_graph` 邻接归口）、DESIGN §8（CJK 检索增强）/ §7（E1 检索升级）。

## 0. 一句话 / 为什么记

SAG 与观澜**哲学上对立**：SAG 是一套面向 agent 的**新型 RAG 技术**（它就是 Karpathy/观澜要绕开的"每次 fresh RAG"那一面），用 `chunk→event` + `chunk→entities` + `event↔entities` 的轻结构替代"往模型塞更多 chunk"，检索从命中 event 沿实体**多跳召回**。但它的**基准发现反过来给观澜两样东西**：(1) 一条干净的零-LLM 净新增——**查询期多跳图扩展召回**（§2）；(2) 对观澜"结构化知识层 > 暴力 chunk-stuffing RAG"这一**核心赌注的外部实证背书**（§3）。其余（DB/pgvector/全文/SQL 多跳存储、embedding/rerank 模型管线、chunk 切块、LLM 抽 query 实体）一律过红线 → E1/别借（§5）。**只借形状、不借实现**。

SAG 关键基准（README）：HotpotQA / 2WikiMultiHop / MuSiQue 上 vs HippoRAG 2，**Recall@2 68.14%→79.30%（+11.16pp，~16.4% 相对）**；MuSiQue Recall@5 65.13%→80.04%；且明说"**收益主要来自结构，而非更强的 embedding**"（换 NV-Embed-v2 仅再 +1.67pp）。两模式：**Fast**（query→BM25 打实体库→多跳扩展→rerank，**查询期零 LLM**）/ **Standard**（LLM 抽 query 实体 + LLM 重排）。

## 1. 总览表

| 条款（SAG） | 结论 | 落点 / 去向 |
|---|---|---|
| **查询期多跳图扩展召回**（命中页沿实体边多跳拉进 BM25 漏掉的邻居） | 🟢 **强借**（零-LLM、查询期算、不落派生物、复用 `build_graph` 邻接） | §2 → 候选 P5.x，并入 [`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md) 检索增强线 |
| **"结构 > embedding"基准发现** | 🔵 **佐证**（外部实证背书观澜结构化赌注，非代码） | §3 → DESIGN §8 记一笔 |
| **检索 trace 可解释面板**（实时显示召回走了哪几步/哪几跳/分数/延迟） | 🟡 **可选借**（若 §2 多跳落地则更需要；调试+解释，零-LLM） | §4 → Web/MCP 搜索面板 |
| **Fast 模式查询期零 LLM** | 🔵 **观澜已是默认**（P5.0 BM25 本就零-LLM；rerank 用 P5.3 确定性 backlink 先验代替模型） | §5 / §6 |
| Postgres/pgvector/全文/SQL 多跳存储 | 🅴 **E1，别借** | §5 |
| embedding/rerank 模型管线 + Standard 模式 LLM 抽 query 实体 | 🔴 **别借**（撞"检索路径零-LLM"+ 模型归 agentao） | §5 |
| chunk→event 切块（子页粒度语义单元） | 🔴 **别借**（观澜页即语义单元；引切块要新增 LLM 抽取+存储层，越红线） | §5 |

> 观澜**已领先**、不该反向借（详 §6）：结构层作为**耐久交叉链接 markdown 增量维护**（SAG 每次 ingest 用 LLM 重抽 event/entity；观澜不重抽，markdown 唯一真相）、**整页召回** = SAG"命中完整语义单元不是碎片"目标、检索路径**零-LLM 无需 rerank 模型**、无状态图重建。

## 2. 🟢 查询期多跳图扩展召回（强借，零-LLM）

- **SAG 的形状**：BM25/全文命中实体只是**入口**，真正提召回的是**沿实体边多跳（SQL multi-hop）把 BM25 漏掉的强关联邻居拉进候选**，再 rerank 选 top-k。基准证明**这一步（而非 embedding）**是多跳 QA 召回提升的来源。
- **观澜的真实缺口**：P5.0 BM25 + **P5.3 backlink 重排**——但 P5.3 只**对已命中页按入链数重排，召回集与纯 BM25 逐页一致**（决策：收录门槛判在 boost 之前）。即观澜**从不把 BM25 漏掉的 `[[wikilink]]` 邻居拉进来**。这正是 SAG 多跳干的事，而观澜邻接表 `build_graph` 早已算好（P3.5/P5.3 同一归口）。多跳问题（"A 投了什么？B 和 A 什么关系？"）下，命中 A 页却漏掉只在 A 页被 `[[链接]]` 提及的 B 页，是 BM25 字面召回的结构性盲区。
- **只借形状的落法**：query BM25 命中页后，**沿 `[[wikilink]]` 邻接 BFS 扩 1–2 跳**，把强关联 entity/concept 邻居页并入候选，再用 P5.3 已有 backlink 先验排序。**零-LLM、查询期算、不落任何派生物、复用同一 `build_graph` 归口**（守 P5.0/P5.3 全部红线）。与 §2-gbrain backlink 重排同成色，只是**召回扩展**而非重排——两者正交可叠（先扩召回、再 backlink 重排）。
- **⚠️ 须钉死**：跳数硬上限（1–2）+ 候选数封顶（防邻居爆炸）；确定性 + byte-stable（hop 序 + path tie-break）；邻接取自与 P5.3/lint **同一 `build_graph`**，不另起第二套图；reader/MCP 都可用（非写，符 P5.1 `is_read_only`）；热路按语料签名 memo（同 P5.3/P5.4，注意 §P5.4 整库 backlink 签名失效问题同样适用）。
- **精度护栏（照搬 SAG 基准纪律）**：扩召回**最易压垮精度**。给 `tests/test_search_quality.py` 加一组**多跳黄金集**（query→期望经 N 跳命中的页），断言 **recall@k 上升而 P@1 不退**；扩展前后命中集对比留作回归闸。SAG 有独立 `SAG-Benchmark` 仓库 + 报 Recall@k——观澜对应的纪律就是这组确定性黄金集。
- **决策建议**：值得做，候选 `docs/P5.x`，并入 [`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md) 检索增强线（与"backlink 重排[已 P5.3]/同义词表/向量[E1]"并列）。**先于 E1 向量**——零基建、贴 P5.0 stateless 边界、复用现成邻接。

## 3. 🔵 "结构 > embedding"——外部实证背书观澜赌注（佐证，非代码）

- SAG 头条发现：**良好的 event/entity 结构层胜过暴力塞 chunk 的 RAG，且收益来自结构而非模型**（换更强 embedding 仅边际提升）。这正是观澜的命题——维护一层结构化、交叉链接的知识层（wiki）优于每次 fresh RAG。
- 差别只在**重抽 vs 维护**：SAG 每次 ingest 用 LLM 重抽 event/entity；观澜把这层作为 **markdown 增量维护、永不重抽**（markdown 唯一真相 + 派生可重建）。观澜的 page ≈ SAG 的 event（完整语义单元），观澜的 entities/`[[links]]` ≈ SAG 的 entity 索引——**同构，只是观澜更克制**。
- **去向**：DESIGN §8（检索增强）记一笔——**SAG 从 RAG 基准侧、独立验证了观澜"结构化知识层 > chunk-stuffing"的设计前提**。不产生排期项，仅作信心背书 + §2 借点的理论依据。

## 4. 🟡 检索 trace 可解释面板（可选借）

SAG WebUI 右栏实时显示内部召回步骤/分数/延迟。若 §2 多跳扩展落地，观澜 Web/MCP 搜索会很需要一个**"这页为何浮现、经哪一跳、什么分数"**的 trace（调试 + 用户解释），尤其多跳后召回链路变长、不可见。零-LLM（纯展示内核已算出的 `SearchResult` 元数据 + hop 路径）。与 gbrain advisor 的 explain 同类。**非刚需**，待 §2 落地后按需评估；可借 P5.0 内核已暴露的 `pages_searched` 等元数据归口扩展，不二次扫描。

## 5. 别借 · 分档（附理由）

- **Postgres/pgvector/全文/SQL 多跳存储** 🅴：= E1（存储与检索升级）。观澜 P5.0 故意 stateless、无落盘派生物；SAG 多跳是 SQL recursive over 实体表，观澜对应物是**内存 `build_graph` 邻接 BFS**（§2），不需要 DB。记入 E1 选型，现在别动。
- **embedding/rerank 模型管线 + Standard 模式 LLM 抽 query 实体** 🔴：撞两条红线——检索路径零-LLM（观澜 search 不调模型）+ 模型选择/成本归 agentao（同 [openkb LiteLLM](openkb-反向评审结论.md) / [gbrain 多 provider embedding](gbrain-反向评审结论.md)）。观澜默认搜索**本就等价 SAG Fast 模式**，用 P5.3 确定性 backlink 先验代替 rerank 模型。
- **chunk→event 切块（子页语义单元）** 🔴：观澜**页即语义单元**，P5.0 返回整页正文，本就实现 SAG"检索命中完整语义单元、不是碎片"的目标。引 chunk-event 粒度要新增 LLM 抽取 + 存储层，越"零-LLM 派生物 + markdown 唯一真相"红线。

## 6. 观澜已领先（不该反向借，作对照）

- **结构层耐久维护 vs 每次重抽**：SAG 每次 ingest 用 LLM 重抽 event/entity 入库；观澜把 entity/concept/`[[link]]` 作为**耐久交叉链接 markdown 增量维护**，ingest 只增不整段重写（去重纪律 + sources/tags/aliases 取并集），**markdown 唯一真相、永不重抽**。
- **整页召回**：观澜 P5.0 返回整页正文 = SAG 力争的"命中完整语义单元不是碎片"，且无需切块层。
- **检索路径零-LLM**：观澜 search 全程零模型调用（BM25 + 确定性 backlink 先验），SAG Fast 模式也去 LLM 但仍依赖 rerank 模型；观澜更纯。
- **无状态图重建 + 拓扑健康**：观澜 `graph` 全量从 markdown 重建无残留陈旧边、并做桥/割点/社区健康 lint（P3.5/P3.6）；SAG 把图用于检索多跳与可视化，不做拓扑健康度。

## 7. 建议排期顺序（park 后再排）

1. **查询期多跳图扩展召回（§2）** —— 候选 `docs/P5.x`，并入 [`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md)；先于 E1 向量。落地**必须**同时加多跳黄金集精度护栏（§2）。
2. **检索 trace 面板（§4）** —— 待 §2 落地后按需，非独立里程碑。
3. §3 仅 DESIGN §8 记一笔背书，不排期；§5 别借（E1/红线）不主动排。
