# gbrain 知识库访问 tools 设计（backlog · 参考）

> 状态：**事实梳理，非排期项**。记录 `../llm-wiki/gbrain`（v0.42.51、commit `9bf96db8`，TypeScript/Bun、Postgres/PGLite，CLI + stdio/HTTP MCP 宿主）对"知识库访问"暴露的工具（operation/tool）形态——**它怎么组织、怎么分级、怎么暴露**，供观澜 tool 注入 / MCP 可写化 / 检索增强排期时作设计参照。**本笔记只描述事实，借不借的研判见 [`gbrain-反向评审结论.md`](gbrain-反向评审结论.md)**，不在此重复。
>
> 关联：[`gbrain-反向评审结论.md`](gbrain-反向评审结论.md)（同源决策笔记；§9 已领先 / §11.1 volunteer_context）、[`sag-对接-tool注入.md`](sag-对接-tool注入.md)（tool 注入对接线）、[`../../P4.10-MCP宿主.md`](../../P4.10-MCP宿主.md)（观澜现有只读 MCP server）、[`../../P5.0-检索层.md`](../../P5.0-检索层.md)、DESIGN §7（E1·E2·E3）/ §8。

## 0. 一句话 / 为什么记

gbrain 把"知识库访问"做成了一套 **92 个声明式 operation 的单一事实源**——`src/core/operations.ts`（5104 行），CLI 命令和 MCP 工具定义全部从它自动生成，每个 op 带 `scope`（read/write/admin）+ 可选 `localOnly` 标记，分发层按调用方信任度（本地 CLI vs 远程 MCP）门控。这正是观澜「tool 注入」「MCP 从只读扩到可写」要面对的形态，值得把它的**工具分组、分级、暴露机制**留底作设计参照。

## 1. 架构总览（设计骨架）

| 维度 | gbrain 做法 | 关键位置 |
|---|---|---|
| **单一事实源** | 全部 92 个 operation 集中定义，不散落各处；描述文案另置以供 LLM 路由 | `src/core/operations.ts`（5104 行）；`src/core/operations-descriptions.ts` |
| **Contract-first 生成** | CLI 命令 + MCP tool schema 都从 operation 定义自动生成，不手写 | `buildToolDefs(ops)` @ `src/mcp/tool-defs.ts:40` |
| **三档 scope 门控** | 每 op 声明 `scope: 'read'\|'write'\|'admin'`（实测计数 **read 51 / write 14 / admin 24**） | `operations.ts` 各 op 字段 |
| **本地专属标记** | `localOnly: true`（7 处）的 op 仅 CLI 可见、HTTP MCP 列表隐藏（如 `purge_deleted_pages`、`get_recent_transcripts`） | `operations.ts` |
| **信任边界** | `OperationContext.remote`：本地可信 CLI（`false`）vs 远程 MCP/agent（`true`）；远程降权——禁 `think --save`/`--take`、禁 localOnly、provenance 字段服务端盖戳忽略客户端值、facts 默认仅 world 可见 | `dispatch.ts` |
| **双传输 MCP 暴露** | stdio（`gbrain serve`，Claude Code/Codex/Cursor）+ HTTP（`--http`，Desktop/Perplexity/ChatGPT）；HTTP 端按 scope + localOnly 过滤工具列表，OAuth token 的 scope/allowedSources 决定可见工具 | `src/mcp/{server,http-transport,dispatch,rate-limit}.ts` |

> 形态要点：**scope 是声明在 op 上的、不是写在路由里的**；门控发生在 dispatch 层；MCP 工具列表本身就是 scope 过滤后的子集——同一套 op 定义，本地 CLI 看到全集、远程按 token scope 看到子集。

## 2. 知识库访问工具清单（按功能分组）

> 行号对 `src/core/operations.ts`（v0.42.51 / `9bf96db8`）。下表只收**与知识库内容读写直接相关**的 op；另有代码智能（`code_callers/def/refs/blast/flow` 等 6 个 read）、schema 分析（`schema_*` 7 个）、作业队列 / 源管理 / 诊断等约 40 个非内容类 op 不在此列。

### ① 读取 / 列举页面（read）

| tool | 行 | 用途 | 关键参数 |
|---|---|---|---|
| `get_page` | 624 | 按 slug 读页面（模糊匹配 + 软删恢复） | slug, fuzzy, include_deleted |
| `list_pages` | 1334 | 按 type/tag/时间过滤列页 | type, tag, limit, updated_after, sort |
| `resolve_slugs` | 2564 | 部分 slug → 候选 slug | partial |
| `get_chunks` | 2576 | 取页面的 embedding 分块 | slug |

### ② 检索 / 查询（read）

| tool | 行 | 用途 | 关键参数 |
|---|---|---|---|
| `search` | 1390 | BM25 关键词全文检索 | query, limit, offset, mode |
| `query` | 1450 | 混合检索（向量+关键词+多查询扩展），3 档成本模式 | query, image, expansion, mode, explain |
| `search_by_image` | 4293 | 图像相似检索 | image |
| `get_recent_salience` | 3227 | 按情绪+活跃度显著性取页（无需关键词） | days, limit, slugPrefix, recency_bias |

### ③ 图谱 / 关系（read + write）

| tool | 行 | scope | 用途 | 关键参数 |
|---|---|---|---|---|
| `get_links` | 1999 | read | 出链 | slug |
| `get_backlinks` | 2015 | read | 入链 | slug |
| `traverse_graph` | 2058 | read | 多跳 BFS 遍历（深度上限 10） | slug, depth, link_type, direction |
| `list_link_sources` | 2031 | read | 列链接来源 provenance | （无） |
| `add_link` | 1932 | write | 建带类型的链接（含 context + provenance） | from, to, link_type, context, link_source |
| `remove_link` | 1972 | write | 删链接 | from, to, link_type, link_source |

> ⚠️ `put_page` 写入时**从 markdown / `[[wikilink]]` 自动抽实体并建边，零 LLM 调用**——手动 `add_link` 是补充而非主路径。

### ④ 写入 / 变更页面（write）

| tool | 行 | 用途 | 关键参数 |
|---|---|---|---|
| `put_page` | 725 | 写/更新页面（markdown+frontmatter），自动分块、embed、建链 | slug, content, source_kind, source_uri, ingested_via |
| `delete_page` | 1253 | 软删（72h 恢复窗） | slug |
| `restore_page` | 1286 | 恢复软删页 | slug |
| `add_tag` / `remove_tag` | 1862 / 1881 | 标签增删 | slug, tag |
| `add_timeline_entry` | 2093 | 加时间线事件 | slug, date(YYYY-MM-DD), summary, detail, source |
| `purge_deleted_pages` | 1313 | 硬删过期软删页（**admin + localOnly**） | older_than_hours |

### ⑤ 合成 / 推理 / 观点（write）

| tool | 行 | 用途 | 关键参数 |
|---|---|---|---|
| `think` | 1792 | 多跳合成（页面+takes+图谱）→ 带引用、冲突与缺口分析的答案；`--save`/`--take` 远程被禁 | question, anchor, rounds, save, take, since, until |
| `takes_list` | 1686 | 列「观点/信念」 | topic, slug, sort, include_forecasts, limit |
| `takes_search` | 1718 | 搜 takes | query, limit |

### ⑥ 热记忆 Facts 表（独立于页面存储）

| tool | 行 | scope | 用途 | 关键参数 |
|---|---|---|---|---|
| `recall` | 3894 | read | 按 entity/session/date 查结构化事实，支持 grep | entity, since, session_id, include_expired, grep, limit |
| `extract_facts` | 3838 | write | 从对话抽事实（事件/偏好/承诺） | turn_text, session_id, entity_hints, visibility |
| `forget_fact` | 4006 | write | 让事实过期（fence 内划线 + valid_until） | id, reason |

### ⑦ 主动上下文 / 原始数据（write）

| tool | 行 | 用途 | 关键参数 |
|---|---|---|---|
| `volunteer_context` | 3268 | agent 主动向 brain 推上下文（v0.43+；见反向评审 §11.1 强借） | context_type, tags, priority, expires_at |
| `put_raw_data` / `get_raw_data` | 2528 / 2547 | 存取原始 JSON 数据 | bucket, key, data |

## 3. 与观澜的设计对照（指引，不重复决策）

下列对照只标"设计形态差异"，**借不借/怎么落已在 [`gbrain-反向评审结论.md`](gbrain-反向评审结论.md) 逐条研判**，此处仅给指针：

- **scope 声明式门控 + 信任边界（remote）** → 观澜当前是「快照 diff 护 `raw/` 只读」+「P4.15 `ask_user` 写确认」。gbrain 把它做成每 op 的 `scope` 字段 + dispatch 层 remote 降权——这是观澜「[tool 注入对接](sag-对接-tool注入.md)」「[P4.10 MCP](../../P4.10-MCP宿主.md) 从只读扩到可写」**最直接的权限模型参照**。
- **`put_page` 写入即自动建链（零 LLM）** → 印证观澜「markdown 唯一真相、索引/图谱可幂等重建」invariant，且证明 wikilink 抽取可纯确定性完成（反向评审 §9：观澜无状态图重建反而更干净，无残留 stale edge）。
- **热记忆 Facts 表与页面存储分离** → 观澜当前没有的维度（结构化事实 + supersession 审计 + visibility）；`volunteer_context` 已在反向评审 §11.1 列为强借，但完整 Facts 表（`recall`/`extract_facts`/`forget_fact`）是否值得是**未研判的潜在增量议题**——记一笔，park。
- **单一事实源 + contract-first 生成 CLI/MCP** → 观澜 CLI 是 argparse 手写（`cli.py`），MCP 是 P4.10 单独实现。gbrain「一处定义 op → 自动生成 CLI + 双传输 MCP」的形态，若观澜未来 MCP 工具面扩大值得参考，但与观澜「薄壳、业务住 skill」红线有张力（反向评审 §9：gbrain 本身就是那坨业务智能），**只取形不取重**。

## 4. guanlan skill 操作 ↔ gbrain tools 对应表

> 观澜操作面分三层：**确定性 CLI 动词**（init/check/health/lint/graph/reindex/remove/convert + heal/audit/ingest/query 的 wrapper 壳）、**LLM 工作流**（ingest / query`--backfill` / heal / audit，skill+agentao）、**P4.10 MCP 只读工具**（`search`/`read_page`/`list_pages`/`graph`/`health`/`lint`/`ask`）。下表把 gbrain 的内容类 op 逐条映到观澜对应物。
>
> 性质标记：🟢 有等价工具（CLI/MCP）· 🟡 无专用工具，写 markdown + 约定 + 门禁 · 🔧 确定性派生（从 markdown 幂等重建）· ⚪ 无对应·候选/后续项 · 🔴 无对应·别借（越界/E1/E2/域不符）· 🔵 观澜已领先。

### 4.1 gbrain → guanlan（按功能组）

| gbrain tool | guanlan 对应 | 性质 |
|---|---|---|
| **① 读取/列举** | | |
| `get_page` | MCP `read_page` / agentao `read_file` 直读 `wiki/*.md` | 🟢 |
| `list_pages` | MCP `list_pages` / 读 `wiki/index.md` | 🟢 |
| `resolve_slugs` | `search`（CJK 2-gram + aliases 召回）/ 扫 index aliases | 🟡 部分 |
| `get_chunks` | 无——观澜整页 BM25 不分块 | 🔴 E1 |
| **② 检索/查询** | | |
| `search` | CLI/MCP `search`（整页 BM25 + CJK 2-gram + aliases，**P5.3 backlink 重排**） | 🟢 |
| `query` | CLI `query` / MCP `ask`（agentao 驱动，答案带 `[[页]]` 引用） | 🟢 LLM |
| `search_by_image` | 无 | 🔴 |
| `get_recent_salience` | 无（无显著性信号） | ⚪ |
| **③ 图谱/关系** | | |
| `get_links`/`get_backlinks` | `graph`（graph.json 邻接）派生；无逐页链接工具 | 🔧 派生 |
| `traverse_graph` | `graph` + `graphstats`（Louvain/桥/割点）；无 BFS 工具 | 🔧 部分 |
| `list_link_sources` | 无（`[[wikilink]]` 无类型/provenance） | ⚪ |
| `add_link`/`remove_link` | 无工具——正文写/删 `[[wikilink]]`（write_file），`graph`/`reindex` 重建 | 🟡 写 md |
| **④ 写入/变更** | | |
| `put_page` | **核心映射**：无专用工具——ingest 工作流 = agentao `write_file` 写 `wiki/*.md`(frontmatter+正文) + wrapper 门禁(raw 快照 + `check` + 有界自愈) + `graph`/`reindex` 派生链接/索引。gbrain 工具内含的 auto-chunk/embed/link 在观澜拆成「写 md + 确定性脚本派生」 | 🟡 写 md + 门禁 |
| `delete_page` | `remove`（P3.9，**源级**撤回：移 `.trash/@ts` + manifest 恢复窗）；语义是「源撤回」非「页删」，页级删除无工具 | 🟡 近似 |
| `restore_page` | 无（`.trash/` 留档可手动恢复，`--restore` 是 P3.9 后续项） | ⚪ 后续 |
| `add_tag`/`remove_tag` | 无工具——编辑 frontmatter `tags:`（write_file），`sources`/`tags`/`aliases` 取并集 | 🟡 写 md |
| `add_timeline_entry` | 无（无 per-page timeline；`log.md` 是库级 ingest 流水，非页时间线） | 🔴 |
| `purge_deleted_pages` | 无（`--purge` 是 P3.9 后续项） | ⚪ 后续 |
| **⑤ 合成/观点** | | |
| `think` | `query --backfill`（写 `wiki/syntheses/<slug>.md` 时点快照，走完整门禁） | 🟢 LLM 近似 |
| `takes_list`/`takes_search` | 无（takes/预测打分是 gbrain 专属域，反向评审 §8 别借） | 🔴 域不符 |
| **⑥ 热记忆 Facts 表** | | |
| `recall`/`extract_facts`/`forget_fact` | 无——观澜一切皆 markdown 页，无独立结构化事实表（潜在增量，§3 已 park） | ⚪ 候选 |
| **⑦ 主动上下文/原始数据** | | |
| `volunteer_context` | 无——反向评审 §11.1 **强借候选**（拟挂 P4.10 MCP） | ⚪ 候选 |
| `put_raw_data`/`get_raw_data` | 无（无 KV store；`raw/` 是只读源文件非 KV bucket） | 🔴 |
| **系统/诊断（选列）** | | |
| `run_doctor`/`get_health`/`get_stats` | `check` + `health`（确定性，pre-write 门禁内跑） | 🟢 |
| `sync_brain` | 无需——markdown 即真相，无 DB 待同步 | 🔵 更简 |
| `get_versions`/`revert_version` | 无（依赖 git；观澜不替用户管 git） | 🔴 越界 |

### 4.2 反向：guanlan 独有 / 领先（gbrain 无专用工具）

| guanlan 操作 | gbrain 侧 | 性质 |
|---|---|---|
| `check`（frontmatter+断链+sources 的 **pre-write 确定性门禁**） | 无等价写前确定性闸；写入即落 DB | 🔵 领先 |
| `reindex`（index.md↔磁盘同步修复，零-LLM） | DB 自动同步；观澜的 markdown-真相等价物 | 🔧 |
| `lint`（孤儿/断链/缺失实体）+ `graphstats`（Louvain/桥/割点拓扑健康） | 图只用于检索加权，**不做拓扑健康度** | 🔵 领先（§9） |
| `heal`（高频缺失实体物化成页） | 无直接对应（近似 dream-cycle 富化相位） | 🔵 |
| `audit`（`raw_digest` 比对的 source-drift 语义复检） | `eval suspected-contradictions`（不同切法） | 🟡 |
| `convert`（多格式→`raw/*.md`，宿主零-LLM） | `file_upload` 等摄入管线 | 🟡 |
| `graph` → `graph.html` 可视化 | 图仅检索用、无可视化 | 🔵 |
| `gate.snapshot_raw`→`EXIT_RAW_MUTATED`（`raw/` 不可变闸） | 有软删恢复窗，无等价源不可变闸 | 🔵 领先 |

### 4.3 本质差异（为什么大半 gbrain 工具落到「🟡 写 markdown + 派生」）

- **gbrain = DB 之上的细粒度 typed tools**：92 个原子工具，Agent 靠一串细粒度 tool-call 构建 KB；`put_page` 一步内含分块/embed/建链——**业务智能住在工具里**。
- **guanlan = 通用文件工具写 markdown + 确定性脚本派生结构**：Agent 用 agentao 通用 `read_file`/`write_file`/`grep` 直接读写 `wiki/*.md`，结构（链接/索引/图谱）不靠专用工具、而由 `graph`/`reindex`/`check` 从 markdown **确定性幂等重建**。所以 gbrain 的 `put_page`/`add_link`/`add_tag` 在观澜**没有对应"工具"**——它们就是「写一个 md 文件 + 一条 `[[wikilink]]`」，**业务智能住在 skill 工作流 + 零-LLM 脚本，不住工具里**。这正是观澜「薄壳 / 零-LLM·LLM 干净分档 / markdown 唯一真相·派生物可重建」三条 invariant 的直接后果——也解释了 tool 注入对接（[`sag-对接-tool注入.md`](sag-对接-tool注入.md)）若要给观澜加"写工具"，须当心别把业务智能从脚本搬进工具、破坏薄壳。
