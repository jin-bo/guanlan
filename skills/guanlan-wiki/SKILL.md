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

摄入一篇 `.md` 资料。**单轮优先，必要时分步**（一篇资料可能触及 10–15 个页面）。**长资料**（长论文/算法手册等单轮易截断的）按**标题/段落语义边界**切块、逐块处理，块间保留上一块尾部上下文以免割裂论断与矛盾；各块写入**同一组**页面（增量更新而非每块另起新页）：

1. 读 `raw/` 里的 `.md` 源；路径必须按用户/wrapper 给出的原样使用，不要替换其中的引号、空格或 CJK 字符；读 `wiki/index.md` 与 `wiki/overview.md` 建立上下文；**先查根级 `SCHEMA.md`**——本库若定义了自定义页面类型或目录（如某域的 `论文/` `模型/` `数据集/`），**优先按它路由**，仅当 SCHEMA.md 未给更具体去处时才回落到默认 `entities/` `concepts/`（SCHEMA.md 是路由权威）。
2. 在 `wiki/sources/<slug>.md` 写**摘要页**（slug = 同源文件名 kebab-case；frontmatter `type: source`）。
3. 抽取实体/概念，**建或更新** `wiki/entities/<Name>.md`、`wiki/concepts/<Name>.md`；正文术语一律转 `[[wikilink]]`。**建页前先去重消歧、再按重要度决定建不建**：
   - **先查重**：建新页前扫 `index.md` 与现有页的 `aliases` frontmatter，找是否已有同一实体的**变体命名页**（AI 领域高发：「ViT」/「Vision Transformer」、「自注意力」/「self-attention」、中英缩写混用）。命中就**更新既有页**（把新变体追加进该页 `aliases`——结构化声明，解析器/召回都吃，见 conventions §别名），**绝不新建重复页**。别名须全局唯一、不与任何页名同名（`check` 会阻断撞名/重复）。
   - **再判建页阈值**：核心/被多篇资料反复提及的术语才建专页；**偶现术语只写裸 `[[X]]` 前向引用、不提前造空桩页**——它会随后续资料自然达到「被 ≥2 页引用」而由 `lint.missing_entity` 提示补页（与 §确定性脚本同口径）。这把"不要造桩页"从禁令落成正向操作：宁可暂留断链，不可造空页。

> **frontmatter 必须是合法 YAML**：字符串值（尤其 `title`）**一律用单引号**包裹，值内单引号翻倍为 `''`；**绝不在双引号里再套双引号**（标题含 `"…"` 时 `title: "…"` 会解析失败——见 conventions §frontmatter）。这是写门禁**会阻断**的硬错误，务必避免。
> **断链不必强求**：尽量为重要术语建页，但正文里指向尚未建页的 `[[X]]` 是建库期的正常前向引用，会随后续资料加入自然消除——写门禁把断链当**警告**、不阻断，不要为了消链接而提前造空桩页或删链接。
4. 更新 `wiki/index.md`（对应分区追加/修订一行）。`wiki/overview.md`（活体综述）**仅当本次引入了新角度、新论断或新矛盾时才改**；纯增量细节由 entity/concept 页吸收，不必动 overview——避免多轮 ingest 让综述频繁抖动、扩大写门禁冲突面。
5. 若新资料与既有页**冲突**，就地在相关页维护 `## ⚠️ 矛盾与存疑` 节（格式见 conventions）。
6. 向 `wiki/log.md` 追加一条：`## [YYYY-MM-DD] ingest | <title>`。
7. **收尾**：不要自行运行 shell 命令或 `guanlan check`；读写文件只用内置文件工具；只返回简短完成说明。wrapper 会在你返回后强制门禁：比对 `raw/` 前后快照（任何改动即失败）+ 跑 `guanlan check`，但**只追究本次新引入的阻断性违规**（frontmatter / `sources`，断链只记警告）。若有新阻断性违规，wrapper 会把清单回喂你自动修复（最多两轮）——所以把 frontmatter 一次写对最省事。

### query（P2）— `guanlan query "…"` / `--backfill`

1. 读 `wiki/index.md` 定位相关页（CJK 用 2-gram 粗召回，**别名串一并纳入匹配面**——同义不同名时命中声明该别名的页；不中时扫相关目录或现有页 `aliases`，或请用户补关键词——graceful fallback）。
2. 读相关页，**综合出带 `[[页]]` 引用的答案**；无可靠来源时明说，不编造。
3. **默认只读**。仅当显式 `--backfill` 时把好答案回填 `wiki/syntheses/<slug>.md`（`type: synthesis`），并走与 ingest 同一套门禁。

### heal（P3.2）— `guanlan heal`

把**高频缺失实体**（被 ≥2 页引用却无页的断链，即 `lint.missing_entity`）一次性物化成 entity 页。wrapper 已把本批**目标名 + 各自引用页清单**算好喂进 prompt（确定性事实，你不必自己找谁引用了它），你只需对每个目标：

1. **只读所列引用页**与 `wiki/index.md` 建上下文（别读全库；目标名已是归一键，可直接作文件名）。
2. 若上下文**足以确认其为实体**，**三选一物化（拿不准走 A）**：
   - **A 直接用目标名**（默认最稳）：建 `wiki/entities/<目标>.md`，`[[原引用]]` 经同一归一天然解析。
   - **B 规范标题 + 收编**：目标口语/缩写、且引用上下文给出更规范全称时，建 `wiki/entities/<规范名>.md`（如 `大语言模型.md`），并在 frontmatter `aliases` **收编原目标名**（`aliases: ['大模型']`），否则断链不消、回执 `still_broken`。
   - **C 收编到既有页**：目标其实是某**已有** `entities/`/`concepts/` 页的变体（读 `index.md`/引用页可认出）时，**只向该页 `aliases` 末尾追加原目标名**，**不新建重复页、不改正文、不动其它 frontmatter**（这是消假断链，不是改内容；见 conventions §heal 建页）。
3. 建页/收编后**在 `wiki/index.md` 对应分区登记一行**（新页加 `- [<标题>](entities/<stem>.md) — <一句话>`；B/C 在句末注记别名，如 `…（别名：大模型/LLM）`），让去重、2-gram 召回、health 同步都看得见它。
4. 向 `wiki/log.md` 追加一条 `## [YYYY-MM-DD] heal | <目标>`。
5. **只准从引用上下文合成，不臆造引用页里没有的事实**；`sources` 列引用页有出处可顺延、否则留空（合法）。上下文不足、目标更像主题页、或无法判定时，**跳过该目标**并用一句话说明（无需特定格式——wrapper 重算图判定成败，不解析你的状态文本）。

> **永不删除或覆盖重写已有页正文、永不修改 `raw/`**：heal 只**新建** entity 页、**向既有页纯追加别名收编本批目标**、编辑 `index.md`、追加 `log.md`。其余越界写（改正文 / 改非 aliases 字段 / 删页 / 在收编里夹带无关别名 / 把页换成符号链接）会被 wrapper 的写集审计标为 `unexpected_write`。其余收尾同 ingest（不运行 shell / `guanlan check`，wrapper 强制门禁 + 有界自愈）。

### 确定性脚本（零 LLM）

- `guanlan check`（P2）— 基础校验：frontmatter 合规 + wikilink 断链 + `sources` 解析。ingest / `--backfill` 收尾**强制**运行；亦可独立 shell 调用。实现在 `guanlan` 包内（`guanlan/check.py`），无 `scripts/`。
- `guanlan health`（P3）— 结构检查：空页/桩页、index 与磁盘同步。
- `guanlan lint`（P3）— 图感知结构 lint：孤儿页、断链、缺失实体页。
- `guanlan graph`（P3）— 解析 `[[wikilink]]` → 边，输出 `graph.json` + 自包含静态 `graph.html`。

> `index.md` / `log.md` / `overview.md` / `SCHEMA.md` 是 config 非 content，**排除出 index/graph/lint 扫描**。
> LLM 只用于 ingest / query；其余工作流全部零 LLM。语义 lint（矛盾复检/过期论断/资料缺口）属 P3 之后。
