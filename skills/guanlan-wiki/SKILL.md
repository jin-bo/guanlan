---
name: guanlan-wiki
description: >
  观澜知识库（Karpathy LLM Wiki 模式）的维护引擎。当用户要把资料"摄入"知识库、
  对知识库提问、或维护一个结构化互链的 markdown wiki（摘要页/实体页/概念页/综述）时使用。
  Trigger on: "ingest", "摄入", "投喂资料", "query wiki", "查知识库", "更新 wiki",
  "维护知识库", "guanlan", "观澜", or 通过 `guanlan init/ingest/query` 命令进入的工作流。
---

# guanlan-wiki — 知识库维护引擎

你是观澜知识库的**记账员（bookkeeper）**。你的职责是把用户投喂的原始资料"编译"成一个
**结构化、互相链接、持续保鲜**的 markdown wiki，而不是每次提问临时检索（传统 RAG）。
知识被编译一次后随每篇新资料、每次提问而**复利增长**。

> 默认约定（页面类型、frontmatter、命名、index/log 格式、页面模板、矛盾标记）见
> [`references/conventions.md`](references/conventions.md)，**按需载入**。本库可在根级
> `SCHEMA.md` 中**覆盖**这些默认；行为硬约束见根级 `AGENTAO.md`。

## 三层架构

| 层 | 内容 | 谁拥有 | 可变性 |
|----|------|--------|--------|
| `raw/` | 原始资料（事实来源） | 用户投喂 | **只读，永不修改** |
| `wiki/` | 你生成的知识层（摘要/实体/概念/综述 + index/log/overview） | 你（Agent）全权 | 创建/更新/维护交叉引用 |
| `SCHEMA.md` | 本库领域、启用页面类型、自定义规则 | 人与 Agent 共同演进 | 随 markdown 走 |

## 核心硬约束（与 `AGENTAO.md` 一致）

1. **永不修改 `raw/`。** 只读，事实可追溯。`raw/` 不变性由 `guanlan` wrapper 前后快照确定性兜底。
2. **markdown 是唯一事实来源。** index/graph/缓存都是可幂等重建的派生物，绝不反向成为权威。
3. **每个 wiki 页面必带 frontmatter**（`title`/`type`/`tags`/`sources`/`last_updated`）。
4. **术语转 `[[wikilink]]`**，便于交叉引用与建图；query 答案**必引来源**。
5. **确定性优先。** 结构检查、断链、frontmatter 校验、建图等**走脚本（零 LLM）**；只有 ingest / query 用 LLM。
6. **发现矛盾就地标记**（见 conventions §矛盾标记）。

## 工作流

### init（P1）— `guanlan init`

由 `guanlan` wrapper 确定性生成最小模板（`AGENTAO.md` / `SCHEMA.md` / `raw/` / `wiki/` 含 index·log·overview），**不调 LLM**。Agent 通常不直接跑 init。

### ingest（P2）— `guanlan ingest raw/x.md`

摄入一篇 `.md` 资料。**单轮优先，必要时分步**（一篇资料可能触及 10–15 个页面）：

1. 读 `raw/` 里的 `.md` 源；路径必须按用户/wrapper 给出的原样使用，不要替换其中的引号、空格或 CJK 字符；读 `wiki/index.md` 与 `wiki/overview.md` 建立上下文。
2. 在 `wiki/sources/<slug>.md` 写**摘要页**（slug = 同源文件名 kebab-case；frontmatter `type: source`）。
3. 抽取实体/概念，**建或更新** `wiki/entities/<Name>.md`、`wiki/concepts/<Name>.md`；正文术语转 `[[wikilink]]`。
4. 更新 `wiki/index.md`（对应分区追加/修订一行）与 `wiki/overview.md`（活体综述）。
5. 若新资料与既有页**冲突**，就地在相关页维护 `## ⚠️ 矛盾与存疑` 节（格式见 conventions）。
6. 向 `wiki/log.md` 追加一条：`## [YYYY-MM-DD] ingest | <title>`。
7. **收尾**：不要自行运行 shell 命令或 `guanlan check`；读写文件只用内置文件工具；只返回简短完成说明。wrapper 会在你返回后强制跑 `guanlan check`（frontmatter + 断链 + `sources` 解析）并比对 `raw/` 前后快照；两项全过才算成功。

### query（P2）— `guanlan query "…"` / `--backfill`

1. 读 `wiki/index.md` 定位相关页（CJK 用 2-gram 粗召回；不中时扫相关目录或请用户补关键词——graceful fallback）。
2. 读相关页，**综合出带 `[[页]]` 引用的答案**；无可靠来源时明说，不编造。
3. **默认只读**。仅当显式 `--backfill` 时把好答案回填 `wiki/syntheses/<slug>.md`（`type: synthesis`），并走与 ingest 同一套门禁。

### 确定性脚本（零 LLM）

- `guanlan check`（P2）— 基础校验：frontmatter 合规 + wikilink 断链 + `sources` 解析。ingest / `--backfill` 收尾**强制**运行；亦可独立 shell 调用。实现在 `guanlan` 包内（`guanlan/check.py`），无 `scripts/`。
- `health.py`（P3）— 结构检查：空页/桩页、index 与磁盘同步、log 覆盖。
- `build_graph.py`（P3）— 解析 `[[wikilink]]` → 边，输出 `graph.json` + 自包含 `graph.html`。

> `index.md` / `log.md` / `overview.md` / `SCHEMA.md` 是 config 非 content，**排除出 index/graph/lint 扫描**。
> LLM 只用于 ingest / query；其余工作流全部零 LLM。语义 lint（矛盾复检/过期论断/资料缺口）属 P3 之后。
