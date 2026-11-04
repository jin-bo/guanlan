# index / overview / log 与大库检索：参考项目对照（backlog）

> 状态：**对照笔记，不立阶段**。日期 2026-11-03，观澜基线 `be9d85b`。**只借形状、不借实现。**
>
> 范围：`../llm-wiki/` 下七个参考项目怎样维护三份元文件（`index.md` / `overview.md` / `log.md`），以及库变大时怎样检索（关键词、向量、融合、重排、评测）。只读源码，不运行。
>
> 参考项目基线：gbrain `23b28ebe45`（2026-10-31）· swarmvault `815412d`（2026-06-30）· llm_wiki `e08cfce`（2026-10-21）· SAG `e68cdeb`（2026-10-22）· OpenKB `ff54396`（2026-07-22）· llm-wiki-agent `c373f02`（2026-10-26）· karpathy-llm-wiki `eafcc77`（2026-07-24）· Karpathy 原文 `llm-wiki.md`。下文引用的行号都相对这些基线。
>
> 关联：[`query-大库index成本-实测.md`](query-大库index成本-实测.md)（query 读 index 的实测否决）、[`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md)（检索增强线，向量留 E1）、[`ingest-performance-research.md`](ingest-performance-research.md)（名录替代整读 index、入库检索召回评估）、[`未来工作计划-反向评审收口.md`](未来工作计划-反向评审收口.md)（E1 后置）、[`../../P5.0-检索层.md`](../../P5.0-检索层.md)、[`../../P5.3-检索backlink重排.md`](../../P5.3-检索backlink重排.md)、[`../../P5.4-检索冷启动性能.md`](../../P5.4-检索冷启动性能.md)、DESIGN §7（E1）/ §8（CJK 检索质量）。

## 0. 一句话

参考项目在库变大后走向一致：**index 由代码生成或干脆放弃，query 不读 index、改走检索，overview 要么没有、要么不许模型写**。检索都落在「关键词 + 向量 + RRF（k=60）」上，但**中文是所有参考项目的共同短板**——观澜 P5.0 的 2-gram BM25F 在中文关键词召回上是其中最扎实的。真正值得借的是 gbrain 的一条纪律：**先有检索评测，再加机制**（它用评测关掉了 LLM 查询扩展）。E1 的触发条件应是「评测里出现可复现的语义漏召回」，不是页数。

## 1. 三份元文件

### 1.1 index.md

| 项目 | 谁写 | 写法 | query 读不读 | ingest 读不读 | 规模处理 |
|---|---|---|---|---|---|
| Karpathy 原文 | LLM | 每次 ingest 更新 | 先读 index 定位再细读 | — | 原文：中等规模（~100 资料）够用，大了上搜索（`llm-wiki.md:47,53`） |
| karpathy-llm-wiki | LLM | 原地更新，表格带日期（`references/index-template.md`） | 读 index + 全文 grep；**两路都空才能说没有**（`SKILL.md:141`） | 不只靠 index，要搜全库（`SKILL.md:94`） | lint 补登漏页、悬空项标 `[MISSING]`（`SKILL.md:172-175`） |
| llm-wiki-agent | LLM 写条目，代码插入（`tools/ingest.py:89`） | 分区顶部插入 | **index 即召回层**：标题关键词 + 图谱邻居，命中少时整份交快模型挑页（`tools/query.py:30-117`） | 整份 index + overview 塞进 prompt（`ingest.py:63-74`） | 无上限；health 报同步问题（`tools/health.py:82`） |
| OpenKB | **只有代码**（`agent/compiler.py:1506` `_update_index`） | 分区原地编辑、原子写 | prompt 要求整读（`agent/query.py:28`），不截断 | **不读**，查重读页面 frontmatter brief（`compiler.py:1627`） | 无上限；lint 只报告（`lint.py:422`） |
| swarmvault | **只有代码** | **每次编译从图谱整份重新生成**，并按分区拆子 index（`markdown.ts:729-821`） | **不读**，走 FTS5 + 向量 | — | 拆分 + 重新生成，不会漂移 |
| llm_wiki | **只有代码，模型输出的 index 被丢弃**（`src/lib/ingest.ts:2544`） | 新页进有界的 Recently Updated 区（≤200 行，`ingest.ts:1807-1821`）；手动重建命令（`project_maintenance.rs:294`） | **不读**，走关键词扫描 + 向量 | 读，截到 32,000 字、头 60% + 尾（`ingest.ts:66,3010-3024`） | 截断 + 手动重建 |
| gbrain | **不写** | 文档建议扫目录定期重建（`docs/GBRAIN_RECOMMENDED_SCHEMA.md:916-919`），无代码 | **不读**；同步时当元文件跳过（`src/core/sync.ts:566`） | — | 数据库 `pages` 表 + 混合检索取代 |
| **观澜** | LLM | 分区追加；`reindex` 零 LLM 补登 | 先 BM25 召回，**措辞上再读 index 补漏**（实测几乎不读，见 §1.4） | **名录替代**（12 万字预算，超了整份退回读 index、不截断） | `health` 查同步，`reindex` 修 |

SAG 不维护 markdown wiki（RAG + 知识图谱应用），唯一的 `index.md` 是导出快照时一次性生成的目录（`apps/api/sag_api/sag/octx_snapshot.py:183`），不回读，不列入表。

### 1.2 overview.md

- 只有两家维护一篇综述：
  - **llm-wiki-agent**：LLM 每次 ingest 输出全文，代码整份覆盖（`tools/ingest.py:228,263`）；query 每次都带上（`query.py:77-80`）。
  - **llm_wiki**：**不许模型写**（`ingest.ts:2142`）；代码只维护其中一个「最近更新」块，≤50 条（`ingest.ts:1736-1798`），正文留给人；agent 系统提示带上它，截到 8,000 字（`src-tauri/src/agent/context.rs:9`）。
- OpenKB、swarmvault、gbrain、karpathy-llm-wiki 都没有。swarmvault（`wiki/graph/report.md`）和 SAG（`UniverseOverview` 表）有**确定性统计概览**（节点数、边数等），不是 LLM 综述。
- 观澜：LLM 维护，只在引入新角度、新论断、新矛盾时改；ingest prompt 只附标题行与行号，按章节读；`health` 有 `overview_large` advisory。

### 1.3 log.md

- 格式几乎都沿用 Karpathy 的 `## [日期] op | 标题`；OpenKB、swarmvault 精确到秒。
- 多数由代码追加；llm-wiki-agent、llm_wiki 让 LLM 写条目内容，llm_wiki 在模型漏写时代码补一条（`ingest.ts:676-685`）。
- **多数没人读**。有三家拿它做事：
  - llm-wiki-agent：health 反查没有 ingest 记录的 source 页（`tools/health.py:157`）。
  - karpathy-llm-wiki：`ingest | no material` 条目让证据脚本豁免未被引用的 raw（`scripts/check_evidence.py:324`）。
  - gbrain：换成数据库表 `ingest_log`，经 `get_ingest_log` 工具按需查（默认 20、上限 50，`src/core/ops/ingest-log.ts:46-63`）。
- 没有一家处理 log 体积（轮转、截断）。

### 1.4 与既有实测的关系（更正一处推断）

本轮对照前，会话里曾推断「query 与 heal 整读 index 是 index 变长后主要的开销来源」。[`query-大库index成本-实测.md`](query-大库index成本-实测.md) 已用五个真实库的 Web 会话否决了 query 这一半：66 个问答轮只有 2 轮读了 index，Agent 实际走 `guanlan_search` → 读候选页。该笔记的覆盖缺口仍成立：**CLI `guanlan query` 与 heal 不在样本内**。本笔记不重复那条措辞建议（「读候选页；需要定位时用 `search_file_content` 查 index 再读该段」），只补一条参考项目的佐证：swarmvault、llm_wiki、gbrain 三家规模化实现的 query 都不读 index。

## 2. 大库检索机制

### 2.1 总表

| | gbrain | swarmvault | llm_wiki | SAG | **观澜** |
|---|---|---|---|---|---|
| 关键词索引 | Postgres FTS + GIN，持久化（`schema.sql:405,427-445`） | SQLite FTS5 trigram，**每次编译全量重建**（`search.ts:127-150`） | **每次查询全量扫描**，无索引（`commands/search.rs:350-413`） | 在外部包 `zleap-sag` 内，不可见 | 内存 BM25F，`CorpusCache` 按 `(mtime_ns,size)` 增量，不落盘 |
| 打分 | `ts_rank`，字段权重 A/B；另有标题一路 | bm25 无字段权重，且**只是最后一个排序键**，先按状态、页型排（`search.ts:296-318`） | 手调子串加分，非 BM25（`search.rs:928-993`） | 应用层按词面重合加权 | BM25F：标题 tf×3、别名 ×2，再乘反链先验 |
| **中文** | FTS 写死 `english`；中文退回逐词 ILIKE，**只按空格切词**（`core/search/cjk-keyword-sql.ts`、`core/cjk.ts:207-248`） | 中文整段当一个短语；trigram 需 ≥3 字，**两字词查不到**（`tokenize.ts:37-48`） | 2-gram + 单字（`search.rs:995-1027`） | 查询端 jieba 切词后逐词 ILIKE（`services/query_analysis.py:161`） | **2-gram，无词典** |
| 向量单元 | 块：300 词、重叠 50，递归切分（`core/chunkers/recursive.ts:135-137`） | **整页前 1,200 字**，不切块（`embeddings.ts:91-97`） | 块：1,000 字、重叠 200，按 markdown 结构切，保留标题路径（`page_embedding.rs:15-21,510-640`） | 块：1,000 token | — |
| 嵌入附加上下文 | 动态拼 `标题 + 摘要 + 块`，**不存盘**；三档 none/title/per_chunk_synopsis（`core/embedding-context.ts`） | 页型、标题、路径 | 标题 + 标题路径 | — | — |
| 向量存储 / ANN | pgvector **HNSW**（`schema.sql:395`） | 一个 JSON 文件，JS 暴力余弦 | LanceDB，**未建索引，实为暴力扫描**，默认 L2 | LanceDB / ES / pgvector 可选 | — |
| 增量失效 | 嵌入输入哈希（列、模型、维度、档位、原文）；相同块复用旧向量（`core/embedding-input-hash.ts`、`core/import-chunk-reuse.ts`） | 键 `kind:id:sha256(text)`，换模型全部作废（`embeddings.ts:199,241-265`） | 内容 + 嵌入配置哈希（仅 Rust 路径；ingest 走的 TS 路径每次重嵌） | 每篇文档 sha256 | — |
| 融合 | **加权 RRF（k=60）+ 零 LLM 意图调权 + `0.7·RRF + 0.3·cosine`**（`core/search/hybrid.ts:57`、`intent-weights.ts`、`cosine-rescore.ts:121`） | RRF k=60 等权（`search.ts:206-247`） | RRF k=60 等权，再分 15–30% 名额给一跳邻居（`search.rs:497-693`） | 加权线性合并 + 相关性门槛（`services/retrieval_service.py:215,232-235`） | — |
| 重排 | 交叉编码器（voyage rerank），按模式开关 | LLM 重排，默认关；只能重排已截好的 top 5 | 无 | 无，仅启发式 | 反链先验（确定性，只改排序不扩召回） |
| 后处理 | Jaccard>0.85 去重、每页 ≤2 块、单页型 ≤60%（`core/search/dedup.ts`） | — | 块分聚合到页：最高 + 0.3×其余（`search.rs:802-852`） | 门槛 `max(0.35, 0.68·top)` | — |
| 给 LLM 的预算 | 按模式 4k / 12k / 不限 token | top 5，每页 1,200 字；无 token 预算 | top 5–10，片段 | top 8，证据总长 12k 字，引用校验失败退回只给摘录 | top-N 页 + 片段 |
| 无嵌入时 | 降级为关键词 + 标题，结果标 `degraded[]` | 静默退回 FTS | 退回关键词 | — | — |
| **检索评测** | **有**：`eval gate`，qrels → recall@K（`docs/eval-bench.md`） | 只算 token 节省 | **无**；README 的 58.2%→71.4% 无脚本可复现 | 无 | 无 query 侧评测；入库侧有过一轮离线召回评估 |
| 公开规模数据 | 生产库 ~15.6 万页；PGLite 建议 ≤5 万页；HNSW ~7.6 GiB/百万块（1024 维）（`docs/ENGINES.md:188-198`） | 无；`shardSize` 声明了未使用 | 硬上限 1 万文件，**超出部分静默丢弃**（`search.rs:23,364-369`） | 无 | 1,531 页打分 <0.5 秒 |

OpenKB **完全没有** BM25 / 向量：query 由 LLM 读 index → 摘要页 → 原文导航，最多 50 轮（`agent/query.py:20-53`）。PageIndex 只对 ≥20 页的 PDF 生效（`config.py:33`），是长文内部的目录树导航，不是库级检索。

### 2.2 逐层要点

1. **切块**：gbrain、llm_wiki 都按段落 → 行 → 句子递归切，带重叠，代码块、表格不拆。llm_wiki 保留标题路径，更贴 wiki 页。swarmvault 只嵌整页前 1,200 字，长页后半段语义不可见——反面教材。
2. **嵌入时附加上下文**：gbrain 嵌「标题 + 摘要 + 块」，FTS 与片段仍用原始块文本，附加部分不存盘。「只加标题」一档零 LLM，最贴观澜约束。
3. **增量失效**：三家独立走到同一口径——**哈希必须含嵌入配置（模型、维度、切块参数），不能只算内容**。
4. **融合**：三家都用 RRF k=60；只有 gbrain 有评测支撑权重选择。gbrain 的 LLM 查询扩展**在所有模式下都关着**，理由是评测无显著收益：436/470 对 434/470，p=0.75（`core/search/mode.ts:136-140`）。
5. **中文**：gbrain 只按空格切、swarmvault 两字词失效、SAG 只在查询端用 jieba、llm_wiki 与观澜同为 2-gram。gbrain 工程最强，中文却最弱。
6. **ANN**：只有 gbrain 建了 HNSW；另两家暴力扫描。按 1024 维估算，5 万块一次点积在百毫秒内，HNSW 到几十万块才变得必要，且建索引代价不小（PGLite 248,802 块约 11 分钟、1.9 GB）。

## 3. 对观澜的分档

### 已领先（别反向借）

- 中文关键词召回：2-gram BM25F + 标题/别名加权，优于全部参考实现。
- 名录超预算**整份不注入、不截断**，优于 llm_wiki 的头尾截断（截断让模型误以为看到全集）。
- index 有检测 + 修复配对（`health` / `reindex`）；OpenKB、llm-wiki-agent 只报告，llm_wiki 只能手动重建。
- overview 只附标题行号、按章节读；llm_wiki 截到 8,000 字。
- 反链先验只改排序、不扩召回集，比 llm_wiki 的「邻居占名额」保守、可预期。

### 可借（形状，确定性）

- **「两路都空才能说没有」**（karpathy-llm-wiki）：可写进 query 工作流，把读 index / 扫目录的兜底理由从隐含变成规则。只改 skill 措辞；与 [`query-大库index成本-实测.md`](query-大库index成本-实测.md) §3 的措辞调整可一次做完。
- **检索评测先行**（gbrain）：见 §4。

### 记入 E1 选型（现在不做）

- 嵌入缓存键 = 内容 + 嵌入配置的哈希（gbrain / llm_wiki / swarmvault）。
- 按 markdown 结构切块、保留标题路径；嵌入时动态拼标题、不存盘（llm_wiki + gbrain）。
- RRF k=60 作融合基线，权重由评测定。
- 无嵌入时显式降级到关键词并标注 `degraded`，不静默（gbrain）。
- 按块检索时的多样性约束：每页取块上限、单页型占比上限（gbrain）。
- 相关性门槛（相对最高分的比例下限，SAG）。

### 有张力

- **index 由代码生成**（OpenKB / swarmvault / llm_wiki）：与「index 行带模型写的一句话摘要」冲突；[`ingest-performance-research.md`](ingest-performance-research.md) 已评估过改由 wrapper 登记，成本高于收益，不重开。
- **按分区拆子 index**（swarmvault）：要改 conventions、`reindex`、`health` 与各工作流的登记口径；query 既然已基本不读 index，收益不足。

### 别借

- 查询端或索引端用 jieba / 词典分词（决策 P5.0-7）。
- 用硬上限静默截断语料（llm_wiki 1 万文件）。
- 把 bm25 降为次要排序键（swarmvault）。
- 默认开启 LLM 查询扩展或 LLM 重排（gbrain 评测已证无显著收益）。
- log 换数据库表、只存在于数据库的页（gbrain）：违反「markdown 唯一真相」。
- 整份 index + overview 塞进 ingest prompt（llm-wiki-agent）：观澜已优化掉。

### E1 选型必须先回答的三处不变量张力

1. **「脚本不得自带 LLM 客户端和 API key」**：嵌入调用就是模型调用。可选路径：经 agentao 调用、只用本地模型（如 ONNX 跑的多语言嵌入模型）、或为嵌入单独立一条显式例外。三者各有代价，需另文。
2. **「markdown 是唯一事实源」**：向量库只能是可删可重建的派生物；不得出现 gbrain 那种只在数据库里的页面或状态。
3. **P5.0「无盘上派生物」**：P5.4 已定持久化索引须在 E1 整体设计，不零散加。

## 4. 未排期：query 侧检索评测（草案）

**动机**：没有评测，任何检索改动（BM25 参数、同义词、向量、融合权重）都只能凭直觉；gbrain 靠评测砍掉了查询扩展，llm_wiki 的召回数字无从复现。观澜入库侧已有一轮离线召回评估（[`ingest-performance-research.md`](ingest-performance-research.md)〈入库检索接入的召回评估〉），query 侧没有。

**形状**（零 LLM、只读、不进 CI 默认集）：

- **qrels 文件**：每库一份人工标注，`问题 → 应答页 stem 列表`，起步 30–50 题。放在库外（不进 `wiki/`，不受写门禁），例如 `<kb>/.guanlan/eval/qrels.jsonl`；是否落在库内另议，因为它不是可从 markdown 重建的派生物。
- **指标**：recall@K（K=5/10/20）、首个相关命中名次。与 gbrain 一样先不做 MRR / nDCG。
- **入口**：脚本先放 `scripts/`（同 `bench_context.py` 的定位），稳定后再考虑是否进 CLI。
- **题目来源**：可从真实会话里取用户问题与 Agent 最终引用的 `[[页]]` 作候选，人工复核后入 qrels；需遵守公开仓库的中性占位规则，qrels 与结果都不入仓库。

**用途**：

- 给 E1 一个触发条件：只有当评测里出现**可复现的语义漏召回**（同义词、上下位、跨语言），且 BM25 调参与别名补登解决不了时，才进入向量选型。
- 作为 [`query-大库index成本-实测.md`](query-大库index成本-实测.md) §3 措辞调整的回归依据：改措辞后召回不降。

**不做的理由（当前）**：没有观测到 query 召回问题的用户反馈；标注 qrels 需要人工时间。触发条件：出现召回投诉、要动检索参数、或启动 E1 选型时，先做这一步。

## 5. 复现

- 参考项目源码在 `../llm-wiki/<项目>/`，基线见文首；行号随上游变动会漂移，复核时以基线 commit 为准。
- 本笔记结论来自只读阅读，其中约十条关键引用已逐条对照源码核实（llm_wiki `ingest.ts:2544/66`、OpenKB `agent/query.py:28`、gbrain `sync.ts:566`/`hybrid.ts:57`/`mode.ts:136-140`/`cjk-keyword-sql.ts`/`cosine-rescore.ts:121`、karpathy-llm-wiki `SKILL.md:141`、swarmvault `tokenize.ts:36-48`）；其余未逐条核对。SAG 的检索核心在未 vendor 的 `zleap-sag==0.15.0` 里，相关条目只到应用层。
