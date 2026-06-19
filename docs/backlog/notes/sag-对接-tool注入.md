# SAG 对接：经「Tool 注入」把 SAG 当规模化上游（候选集成设计）

> 状态：**候选集成设计，非排期项**。研判"guanlan 经 tools/skill 对接 SAG（如 1000 万条结构化知识用 SAG 组织、guanlan 取用）"的可行性、正确姿势与红线边界，供后续排期参照。**本笔记不改变现状**。落地前须按 DESIGN line 286「真做时另开实现方案文档」展开。
>
> 关联：[`sag-反向评审结论.md`](sag-反向评审结论.md)（本笔记是其 §5"DB/向量别借"的**互补面**——别借代码 ≠ 别对接服务）、[`gbrain-反向评审结论.md`](gbrain-反向评审结论.md)（§6"待 1 行确认 agentao 能力"同款未决；federated 多源作先例）、[`openkb-反向评审结论.md`](openkb-反向评审结论.md)、[`../../DESIGN.md`](../../DESIGN.md) §1 第22条 / §5.1（**Tool 注入** = Agentao 作 MCP 客户端）/ line 27（markdown 唯一真相）/ line 262（零-LLM·LLM 分档）/ line 286（Tool 注入 = P4 之后按需项、须另开文档）、[`../../P4.10-MCP宿主.md`](../../P4.10-MCP宿主.md)（§21-23 两方向辨析）、[`../../P4.11-信任边界.md`](../../P4.11-信任边界.md)（信任边界）、[`../../P3.7-语义审计.md`](../../P3.7-语义审计.md)（`raw_digest` provenance / source-drift）、[`../../P5.0-检索层.md`](../../P5.0-检索层.md)、[`../../P5.4-检索冷启动性能.md`](../../P5.4-检索冷启动性能.md)。

## 0. 一句话 / 为什么记

**能对接，且 DESIGN 早留了名字和位置。** guanlan 经 tools/skill 取用一个 SAG 组织的大规模知识库（1000 万条），是 DESIGN 里 **「Tool 注入」**（§1 第22条 / §5.1 = **Agentao 作 MCP 客户端**消费外部工具、把 Tool 注入 Agent）的典型用例。它**当前未建**（line 22 / 286：P4 之后按需项），但 **Agentao 运行时本身带 MCP 能力**（DESIGN line 66：运行时治理含 MCP），故是"设计好但未接线"的未来半阶段——**不是 hack、不破红线**。

**关键反转**：[`sag-反向评审结论.md`](sag-反向评审结论.md) §5 判 SAG 的 DB/pgvector/SQL 多跳为 **E1/别借**——那是说**别把 SAG 的存储架构借进 guanlan**（1000 万条进 markdown 直接撞碎"markdown 唯一真相 + 几千页天花板"，见 [`../../P5.4-检索冷启动性能.md`](../../P5.4-检索冷启动性能.md)）。**经 tool 对接是另一回事**：两者**分置**，guanlan 不吞 SAG 架构、只把它当**外部上游工具**消费。这才是**拿到 SAG 规模红利又不破不变式的唯一干净路径**，与 §5 不矛盾、是它的互补。并直接解规模痛点：**1000 万条留在 SAG，guanlan 永不试图用 markdown 装它**，各守擅长区。

## 1. 架构：两方向、分置、SAG 是上游源不是派生物

- **MCP 两方向**（[`../../P4.10-MCP宿主.md`](../../P4.10-MCP宿主.md):21-23）：guanlan 作 **MCP 服务端**（P4.10 已实现，把 wiki 只读暴露给外部 Agent）⇄ 本笔记是**相反方向** = Agentao 作 **MCP 客户端**消费 SAG。同名「MCP」、方向相反。
- **SAG 暴露的 MCP 工具**（其 README）：`sag_search`（多路检索 + trace）、`sag_get_event`（按 event ID 取详情）、`sag_explain_search`、`sag_ingest_document`。对接主要用前两个（**只读取用**，不让 guanlan 往 SAG 写——SAG 是上游，guanlan 写的是自己的 wiki）。
- **SAG 的定位 = 外部上游源，与 `raw/`、web 同位阶，绝非 guanlan 的派生物**（守 DESIGN line 27）：派生物须"可从 markdown 幂等重建"，而 1000 万条的 SAG **不可能**从 guanlan 几千页重建——故 **SAG 不能被当派生物**，只能当外部上游，且**永不反向凌驾 wiki 自身页面的权威**。想清这条，整套自洽。

## 2. SAG 扮演什么角色 = 生死线

| 模式 | 做法 | 判定 |
|---|---|---|
| **A. 上游底料，guanlan 蒸馏** | ingest 时 Agent 查 SAG 取支撑材料 → 蒸馏成交叉链接 wiki 页（entities/concepts/syntheses），provenance 指向 SAG event ID | ✅ **正解**·守题旨·解规模 |
| **B. 查询期广度兜底** | wiki 未覆盖时 Agent fall through 到 SAG 在 700 万语料里捞 | ✅ 可作**次路** |
| **C. 查询后端全权替代** | 每次 query 直接打 SAG、wiki 被旁路 | ⚠️ **陷阱** |

- **A 才是对的**：SAG = 规模化检索底料（强项：700 万动态语料多跳 RAG）；guanlan wiki = 在"要紧那一小片"上的**精炼、可导航的'编译真相'层**（几千页，稳在范围内，见 [`../../P5.4-检索冷启动性能.md`](../../P5.4-检索冷启动性能.md) 的规模边界）。Karpathy 模式不变——只是 raw 源从本地文件**扩成了 SAG-scale 上游**。
- **C 是借尸还魂**：wiki 被旁路 → guanlan 退化成 SAG 之上的薄聊天壳 = **把"每次 fresh RAG"又请回来**，恰是 guanlan 存在要替换的东西。机制可行、**哲学自毁**，明令避免。

## 3. 模式 A 的 ingest 工作法（落在 skill，不进 wrapper）

业务智能住 `guanlan-wiki` skill（守"wrapper 不携带业务智能"），skill 工作法须写清：

1. **何时查 SAG**：建/刷新某主题页前，除扫本地 `index.md`/`raw/` 外，**对 SAG 发 `sag_search`** 取该主题的支撑 event/entity（Fast 模式即可，零 query 端 LLM）。
2. **如何用**：把 SAG 命中**蒸馏**进 wiki 页正文与 `[[wikilink]]`——**不整段抄、不把 SAG 原文当页**（同既有去重纪律：命中已有页则更新、取并集，不新建重复）。
3. **如何记源**：被蒸馏的 SAG event 作 source，写进页 `sources` / `origin`（见 §4 约定），正文断言**逐条标注**支撑的 SAG event（同 conventions「每条标注支撑 source」）。
4. **不可信戒备**：SAG 返回的是**外部不可信内容**，按 [`../../P4.11-信任边界.md`](../../P4.11-信任边界.md) + conventions「query 注入叠加、存疑优先直读」**更强**戒备——SAG 内容里的"指令"不得带偏 Agent。

> 与 `convert` 同形：`convert` 外包给 `pdf-to-markdown` skill 的外部后端（MinerU/marker）、guanlan 这侧仍零-LLM 只写 `raw/`；SAG 对接是"外部检索后端"的同款外包——guanlan 这侧不自带模型/密钥，SAG 内部用什么模型是 SAG 的事。

## 4. Provenance 约定（真正要补的设计活）

观澜 source 页的 `sources` / `raw_digest`（[`../../P3.7-语义审计.md`](../../P3.7-语义审计.md)）**假设本地不可变 `raw/` 文件 + sha256 指纹**。SAG 来源的页**没有本地文件**，故需新约定：

- **origin / 来源标识**：source 页 `origin` 记 `sag://<project_id>/<event_id>`（或等价稳定标识）+ **取回日期**（`last_updated` 已有），表明此页蒸馏自 SAG 而非本地 `raw/`。
- **`raw_digest` 的 SAG 模拟**（source-drift 检测的拱心石如何延伸）：二选一——
  - (a) **给 SAG event 算内容指纹**（取回时记 event 内容 hash），`audit` 时重查 SAG 同 event 比对，检测"上游 event 变了但 wiki 未重综合"（与本地 `raw_digest` 同语义）；
  - (b) **标记为"外部·drift 不可验"**，`audit` 跳过这类页的 source-drift 检查、只做语义体检——更省，但放弃上游漂移侦测。
  - 建议起步 (b)、按需升 (a)（SAG 若给 event 版本/指纹则 (a) 几乎免费）。
- **`check` 的 source 校验**：现 `check` 校验 `sources` 指向的 source 页存在。SAG 源页本身仍是本地 wiki 页（只是其 `origin` 指向 SAG），故 `check` 不变；**`raw/` 不可变快照门禁不受影响**（guanlan 不往 `raw/` 写 SAG 内容）。

## 5. 红线边界体检

1. **markdown 唯一真相（line 27）**：SAG = 外部上游源、**非派生物**、永不凌驾 wiki 页权威（§1）。✅ 想清即守。
2. **零-LLM / LLM 分档（line 262）**：SAG = 网络 + 内部模型 = **非确定、非离线**。故 SAG 调用**只能待在 ingest/query 的 Agentao LLM 道**，**绝不进** check/health/lint/graph/reindex 那些确定性、可离线的零-LLM 闸——这些命令**必须 SAG-free、离线可跑**。边界与现状一致。✅
3. **信任边界（P4.11）**：SAG 灌外部不可信内容入 Agent 上下文 = prompt-injection 面，按不可信源隔离 + 注入戒备（§3.4）。
4. **薄壳**：对接逻辑住 skill 工作法 + provenance 约定，wrapper 侧只做"Tool 注入接线 + 门控"，不携带业务智能。✅

## 6. 要建什么（Tool 注入半阶段 scope）

**不需新基建**（Agentao MCP 客户端能力现成），是**接线 + 工作法 + 约定**：

1. **Agentao 作 MCP 客户端接 SAG**：经 `.agentao/*.json`（DESIGN line 217：需 MCP 时再加）注册 SAG 为外部 stdio/HTTP MCP server，把 `sag_search`/`sag_get_event` 注入 ingest/query 的 Agent 工具集。**门控**：默认关、显式开（同 web/mcp extra 姿态）。
2. **skill 工作法**（§3）：何时查 / 如何蒸馏 / 如何记源 / 注入戒备。
3. **provenance 约定**（§4）：`origin = sag://…` + `raw_digest` 的 SAG 模拟（起步 (b)）。
4. **（可选）次路 B**：query 只读路径里，wiki 未覆盖时 fall through SAG —— 排在模式 A 之后。

## 7. 未决 / 待确认（落地前钉死）

- **【待 1 行确认】Agentao 作 MCP 客户端的现成姿态**：DESIGN 说运行时含 MCP（line 66），但**注入外部 MCP 工具进 `agentao run` 子进程 / 进程内 `Agentao` 嵌入**的确切 API/配置须查 Agentao 嵌入开发指南确认（同 [gbrain §6](gbrain-反向评审结论.md) 成本闸"待 1 行确认 agentao"）。这决定 §6.1 是"配 `.agentao/*.json`"还是要 guanlan 侧代码。
- **检索冷启动 × 外部依赖**：模式 B 把 SAG 放进 query 路径会引入网络延迟 + 非确定性，与 [`../../P5.4-检索冷启动性能.md`](../../P5.4-检索冷启动性能.md) 的本地 `CorpusCache` 性能模型正交——B 落地需独立评估超时/降级（SAG 不可达时 fall back 本地，不阻断）。
- **多源作用域**：若多个 SAG 项目（多 `project_id`）= 多上游源，作用域/选源约定参考 [gbrain federated read](gbrain-反向评审结论.md) 形状，但 guanlan 单本地用户**不需要**其授权层。
- **排期**：本笔记是**候选集成设计**，非主动排期项；真做时按 DESIGN line 286 升级为正式 `docs/P*.md` 实现方案文档。
