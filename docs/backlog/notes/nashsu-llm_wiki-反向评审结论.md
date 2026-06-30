# nashsu/llm_wiki 反向评审结论（backlog）

> 状态：**反向评审结论，非排期项**。记录对 `../llm-wiki/llm_wiki`（nashsu/llm_wiki，Tauri + React/TS + Rust 桌面应用，本地 markdown 知识库 + 嵌入式 LLM ingest/query + MCP + 浏览器扩展）的逐条款"借不借/怎么落"研判，供后续排期参照。**本笔记不改变现状**，**只借形状、不借实现**贯穿始终。
>
> **特殊性**：nashsu/llm_wiki 是观澜**最直接的架构孪生**（同 Karpathy 模式、同"本地 markdown + 增量 ingest"路线），历史上被**逐条散引**进多篇 P 文档（**P2.1** 整篇即对它的反向评审——re-ingest 合并不覆盖 / 正文不被改写 / 70% 缩水守卫）。本笔记是**首篇合并结论**，键定在 **2026-06-29 这次 pull**（`dda8768→c03c6be`，至 `v0.5.4`，151 文件 +12385/-1510），覆盖整个 delta。
>
> **进展更新（实现后回填）**：§2 lint 断链建议 = **[`../../P3.11-断链最近页建议.md`](../../P3.11-断链最近页建议.md)**（规格，已过两轮评审、可排期，落地清单见其 §9）；§3 finding 持久抑制 = **[`finding-持久抑制-未排期.md`](finding-持久抑制-未排期.md)**（backlog，未排期）。余条未排。
> 关联：[`broken-link-handling-survey.md`](broken-link-handling-survey.md)（断链四参考实现总览，§2 的旧 llm_wiki 数据点在此）、[`../../P2.1-摄入写入纪律.md`](../../P2.1-摄入写入纪律.md)（既有 llm_wiki 反向评审）、[`openkb-反向评审结论.md`](openkb-反向评审结论.md) / [`gbrain-反向评审结论.md`](gbrain-反向评审结论.md) / [`sag-反向评审结论.md`](sag-反向评审结论.md)（同类先例）、DESIGN §3（参考实现对比）/ §8。

## 0. 一句话 / 为什么记

llm_wiki 是观澜的**同路线孪生**，本次 pull 的主线是 **lint 修复建议 + 一键应用、ingest 队列健壮性、CJK/语言处理、桌面应用打包与大项目性能**。逐条过滤观澜红线（`guanlan/` 不携带业务智能 / 零-LLM 脚本 vs LLM 工作流 / `raw/` 不可变 + 源不回退 / **markdown 唯一真相 + 绝不让代码改写正文** / 薄壳 / 故意绑 agentao / Web 仅 127.0.0.1 / CJK 优先）后：**真正不破红线可落的净新增只有 §2 一条（已起草 P3.11），§3/§4/§5 为窄借/拆 backlog/借教训**；其余多是**佐证**、**观澜已领先**、或**撞红线别借**。其中最值得记的是——llm_wiki 这次把断链从"无修复"推到"建议 + **代码改写正文应用**"，而**应用半正是观澜 决策P2.1-4 早已明令不抄的方向**，故本次借鉴严格止于"建议（只读）"。

## 1. 总览表

| 条款（nashsu/llm_wiki，本次 pull） | 结论 | 落点 / 去向 |
|---|---|---|
| **lint 断链「最近页」建议计算**（`lint.ts:194 suggestBrokenTarget`，`25df662`） | 🟢 **借（已起草，改技术路线）** | §2 → [`P3.11-断链最近页建议.md`](../../P3.11-断链最近页建议.md) |
| **lint 一键应用层**（`lint-fixes.ts` `rewriteWikilinkTarget` 改写正文 / `ensureBrokenLinkStub` 建页） | 🔴 **别借** | §6 → 撞 决策P2.1-4；建页 ≈ 已有 `heal` |
| **content-stable review ids + resolved 持久化**（`reviewIdFor` FNV-1a，`d691e41`） | 🟡 **借形状（拆 backlog）** | §3 → [`finding-持久抑制-未排期.md`](finding-持久抑制-未排期.md) |
| **schema 注入分析阶段**（`f714076`，含 no-invention 护栏） | 🟡 **借教训（不照搬注入）** | §4 → SKILL.md ingest 步硬化 |
| **ingest 队列限流自动暂停**（429/quota → pause + 15min auto-resume，`3b92724`） | 🟡 **窄借** | §5 → P4.16 goal loop 限流自愈 |
| **取消后停止写入**（`006327d`） | 🔵 **观澜模型下 N/A** | §5 → 子进程 + 快照模型不交错写 |
| **CJK ingest 文件名保目标语言 / 保技术名**（`50ec6a3`/`69fe431`） | 🔵 **观澜已领先** | §7 → `raw_slug` 已保 CJK + ASCII 技术名 |
| **缺失页 review → 建实页**（`review-create-page.ts` 双语抽取 + type→dir，`3d66f76`） | ⚪ **小可借（大半已覆盖）** | §6 → `heal` 已做；只 type→dir 表是糖 |
| **re-ingest 合并 / 正文缩水守卫**（`page-merge.ts`） | 🔵 **已在路上 = P2.1** | §7 → 观澜走门禁路线、不抄代码 union |
| **大项目启动性能**（path resolver index / 去重目录列举 / 懒树 / 延迟 hydration） | ⚪ **watch-item，低优先** | §6 → 当前 KB 小；加缓存须守"派生可重建" |
| **explicit ingest 队列 pause/resume / batch actions**（`c3f66b9`/`3fb5cbf`） | 🔵 **已有等价** | §6 → web JobQueue 串行 + goal pause/resume |
| **configurable bind host / LAN access**（`fda0cae`/`21dd78d`） | 🔴 **别借** | §6 → 撞 web 仅 127.0.0.1 信任边界 |
| 多 web-search provider（Brave/Firecrawl/Atlas，`f6a9413`/`c5f9004`/`3eb6925`） | 🔴 **别借** | §6 → 检索走 agentao web fetch |
| 多 provider LLM 配置 / keyless 开关 / chat agent routing / edit-frontmatter-raw | 🔴/⚪ **别借·UI 糖** | §6 |
| Windows portable / Intel macOS / cargo CI 硬化 | 🔴 **N/A** | §6 → Tauri 打包，观澜是 Python wheel |

> **观澜已领先**、不该反向借（详 §7）：确定性写门禁 + `raw/` 快照（llm_wiki 有项目锁但无源不可变闸）、零-LLM/LLM 干净分档（llm_wiki 把 lint 建议+应用混、用代码 union frontmatter）、图谱拓扑 lint（桥/割点/社区，llm_wiki lint 无）、finding 因果排序（`order_findings` 已有）、CJK slug 保留、故意绑 agentao（vs 多 provider）、Web 仅 127.0.0.1（vs LAN access）。

## 2. 🟢 lint 断链「最近页」建议（借，已起草 P3.11）

- **形状**：本次 pull `lint.ts:194 suggestBrokenTarget` 为断链算最近既有页（`lint.ts:111 stringSimilarity` = 同 basename / 包含 / `lint.ts:91 levenshtein`），把 survey §3 记的"llm_wiki 无自动修复"旧姿态推进到"建议"。
- **真实缺口**：观澜 `lint.broken_link`（`lint.py:127`）/ `lint.missing_entity`（`lint.py:137`）只说"无对应页面"，不提示"是不是已有近似页"。
- **只借形状的落法（且换技术路线）**：**不照搬** `search_pages`（BM25-over-正文会顶出含 `[[T]]` 的引用页）、**不照搬** Levenshtein（英文 typo 非检索能力）。改为：v1 候选**只取既有页 stem/title**（`run_lint` 已建图的 `g.nodes` 现成 `Node.id`/`Node.title`，**真零额外扫描**；`Node` 无 aliases、`link_resolution_index` 是 build_graph 局部变量不外露，故 aliases 须另跑 `alias_index()` 一次扫描、留可选后续），复用 `search.tokenize` 算**共享 token / CJK-2-gram overlap / 包含**，只产**只读 advisory**；JSON 靠 `report_dict` 丢 None 保字节稳定。详见 [`P3.11`](../../P3.11-断链最近页建议.md) §0.1 + §2 复审修订。
- **决策建议**：值得做、小、纯加法、零新持久状态——已起草，可立即排期。

## 3. 🟡 finding 稳定身份 + 持久抑制（借形状，拆 backlog）

- **形状**：`reviewIdFor = FNV-1a(type + 归一化 title)`（`review-store.ts`，`d691e41`）——同一逻辑项跨重生成得同一 id、对含已解决项的全集去重，故"已处理"不被重生成抹掉；**故意排除可变 `sourcePath`**。
- **真实缺口**：观澜 `Finding(page,kind,detail)`（`pages.py:103`）无身份、无 resolved；`run_lint`/`run_health` 每次全量重报，用户无法消音"故意的孤儿页 / 反复刷屏的拓扑建议"。
- **张力（为何拆）**：抑制要 sidecar baseline + `--suppress`/`--show-suppressed` + 跨 `lint`/`health` 覆盖 + 新状态模型——**给当前纯函数命令引持久状态**（与 P3.10 珍视的零持久化相左），面比一条只读 advisory 大得多。
- **决策建议**：**拆出独立留档** = [`finding-持久抑制-未排期.md`](finding-持久抑制-未排期.md)，待"advisory 反复刷屏"成实测痛点再排；届时配方须**身份排除易变 `detail`**（含"被 N 页引用"计数，同 nashsu 排除 `sourcePath` 的同型坑）。

## 4. 🟡 schema 注入分析阶段（借教训，不照搬注入）

- **形状**：`f714076` 把 project schema 喂进 ingest **分析阶段**（原来只进生成阶段，分析对自定义页型失明 → 路由错），并加一句"**只在源材料确实支持时才推荐、绝不臆造**"的护栏。
- **观澜对照**：观澜 ingest **单段**（无 analysis/generation 之分），且 `SCHEMA.md` **不由 host 注入提示**——靠 SKILL.md 指示 Agent"先查根级 SCHEMA.md"（`SKILL.md:59`）自读。直接学它把 SCHEMA 塞进 host 提示会**撞薄壳红线**（wrapper 不携带业务智能、提示是 skill 的事）。
- **只借形状的落法**：借的是**教训**——依赖 Agent"自愿读"schema 脆（llm_wiki 的 bug 即此）。落在 skill 边界内：把 SKILL.md ingest 第 1 步"读 SCHEMA → 再路由"从建议**升为硬步骤**，并补 nashsu 那句**no-invention 护栏**（绝不臆造未在源中出现的页型）。
- **决策建议**：纯文档小硬化（同 P4.11 信任边界档），可随手落；awareness 已有，缺的是 enforcement。优先级中。

## 5. 🟡 ingest 限流自愈（窄借，喂 P4.16 goal loop）

- **形状**：`3b92724` ingest 队列遇 `429/rate.limit/quota/too many requests` → 暂停 + 15min 后 auto-resume（`isUsageLimitError`/`scheduleUsageLimitAutoResume`）；`006327d` 取消后停止后续写入。
- **真实缺口**：观澜 CLI ingest 是单子进程、无限流处理（只识别伪装成成功的 `[LLM API error:`，`runtime.py:202`）。真正会撞限流的是 **P4.16 那个 `while goal.is_active` 多轮续跑循环**——限流时白烧 turn / 直接失败。
- **只借形状的落法**：给 goal loop 接一个 **429 探测器 → 自动 `pause_goal` + 冷却后 resume**（复用 `conversation.py` 已有 `pause_goal`/`resume_goal` 原语，改动小）。
- **🔵 取消后停写对观澜 N/A**：观澜是**子进程 + 前后快照**模型，无法 mid-write 交错取消（子进程要么跑完要么被杀，`raw/` 由 `snapshot_raw` 兜底），不存在 llm_wiki 那种 in-process writer 的"取消后还在写"问题。
- **决策建议**：窄、定向、贴 P4.16；值得做但非刚需，实证（真撞限流）驱动。

## 6. 别借 · 低优先（附理由）

- **lint 一键应用层 `rewriteWikilinkTarget`** 🔴：**代码改写正文** = 决策P2.1-4 明令不抄"llm_wiki 代码落地 link substitution"；survey §4.5#2 同立场。`ensureBrokenLinkStub`（建占位页）≈ 观澜已有 `heal`（LLM + 门禁，刻意不在只读路径建页）。**应用半整体别借**，建议交 Agent/`heal`。
- **缺失页 review → 建实页**（`review-create-page.ts`）⚪：双语标题抽取观澜**用不上**（`missing_entity.target` 已是解析好的 stem，非自由文本）；只 type→dir 路由表（entity→`entities/` 等）是 stub 创建的确定性默认值小糖，大半被 `heal` 的 A/B/C 覆盖。
- **大项目启动性能**（resolver index / 去重列举 / 懒树 / 延迟 hydration）⚪：观澜 check/health/lint/graph 每次全量重扫、无缓存，但当前 KB 小，且加缓存有违"派生物可幂等重建"——**watch-item**，KB 变大再考虑"纯派生、按内容键"的 resolver 索引（那样才相容红线）。
- **configurable bind host / LAN access** 🔴：观澜 web 故意 **127.0.0.1-only**（P4.11 信任边界 + P4.9 reader 模式），开 LAN 撞安全立场。
- **多 web-search provider / 多 LLM provider / keyless 开关** 🔴：检索与 LLM 故意绑 agentao（web fetch 走 agentao + `AGENTAO_WEB_FETCH_ALLOW_CIDRS`），不在观澜这层引 provider 抽象。
- **chat agent routing / edit-frontmatter-raw / batch actions / explicit pause-resume** ⚪🔵：UI 层糖；观澜 web JobQueue 串行 + P4.16 goal pause/resume 已有等价能力，CLI 一次 run 本就全量批处理。
- **Tauri 打包 / Windows portable / Intel macOS / cargo CI** 🔴 N/A：观澜是 Python wheel（`uv`/PyPI），打包面不重叠。

## 7. 观澜已领先 / 已在路上（不该反向借，作对照）

- **CJK slug**（`50ec6a3`/`69fe431`）🔵 **已领先**：llm_wiki 这是**补 bug**——其默认 slug 会罗马化/丢中文，故新增 `rewriteIngestPathFromTitleForTargetLanguage` 在 CJK 目标语言下从 title 重写文件名。观澜 `raw_slug`（`rawio.py:67`，`[^\w.\-]+` 且 Python `\w` 含 CJK）**本就保留中文**，且 ASCII 技术名 + 内部点（`GPT-4.5`）也保留——观澜反而更稳，无需动作。
- **re-ingest 合并 / 正文缩水守卫**（`page-merge.ts`）🔵 **已在路上 = P2.1**：观澜 P2.1 已立"既有页只增不毁"（源不回退阻断+自愈、正文骤缩告警），但**刻意不抄** llm_wiki"代码 union frontmatter / 代码落地 substitution"——门禁只验证不变量、不替 Agent 合并（决策P2.1-4）。
- **确定性写门禁 + `raw/` 快照** 🔵：llm_wiki 有项目锁（`withProjectLock`）但**无源不可变闸**；观澜 `snapshot_raw`/`diff_raw`（`gate.py`）连 shell `mv`/`rm` 旁路写都拦。
- **零-LLM/LLM 干净分档** 🔵：llm_wiki 把 lint 建议**与代码改写应用**揉在一处、用代码做 merge/substitution；观澜确定性脚本（check/lint/graph/reindex/search）与 LLM 工作流（ingest/query/heal/audit）干净分档。
- **图谱拓扑 lint + finding 因果排序** 🔵：观澜有 Louvain 社区 / Tarjan 桥·割点（`graphstats.py`）+ `order_findings` 因果排序（`pages.py:431`）；llm_wiki lint 无拓扑健康度。

## 8. 建议排期顺序（park 后再排）

1. **§2 lint 断链建议** —— 已起草 [`P3.11`](../../P3.11-断链最近页建议.md)，小·只读·零新状态，**可立即排期**。
2. **§4 schema 读取硬化 + no-invention 护栏** —— 纯文档小硬化（SKILL.md ingest 步），可随手落。
3. **§5 P4.16 goal loop 限流自愈** —— 窄、定向，实证（真撞限流）驱动。
4. **§3 finding 持久抑制** —— 拆 [`finding-持久抑制-未排期.md`](finding-持久抑制-未排期.md)，等刷屏成痛点再排（引持久状态，面大）。

其余（§6/§7）佐证 / 已领先 / 撞红线，不主动排。
