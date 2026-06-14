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

摄入一篇 `.md` 资料。

> **非 `.md` 源（PDF/DOCX/…）**：ingest 仍只吃 `.md`。命令行先 `guanlan convert <file>`（宿主侧零 LLM 转换，落成 `raw/*.md` + `origin` provenance，P5.2），再 ingest 那个 `.md`；Web 可写会话里走 `pdf-to-markdown` skill 解析到 `workspace/parsed/` 后由人审晋级为 `raw/` 源（P4.6）。两条路都把原件转成 `.md` 当源，ingest 不读二进制。

**范围与切块**：
- **单轮优先，必要时分步**——一篇资料可能触及 10–15 个页面。
- **长资料**（长论文/算法手册等单轮易截断的）按**标题/段落语义边界**切块、逐块处理。
- 块间**保留上一块尾部上下文**以免割裂论断与矛盾；各块写入**同一组**页面（增量更新，不每块另起新页）。

**步骤**：

1. **读源 + 建上下文 + 查路由**：
   - 读 `raw/` 的 `.md` 源；路径按 wrapper 给出的原样使用，不替换其中的引号/空格/CJK 字符。
   - 读 `wiki/index.md` 与 `wiki/overview.md`。
   - **先查根级 `SCHEMA.md`**：本库若定义了自定义页面类型或目录（如 `论文/` `模型/` `数据集/`），按它路由；未给更具体去处时才回落默认 `entities/` `concepts/`。SCHEMA.md 是路由权威。
2. 在 `wiki/sources/<slug>.md` 写**摘要页**（slug = 同源文件名 kebab-case；`type: source`）。
   - **选择性保留承载知识的嵌图**（流程图/架构图/数据流图、关键表格或图表截图等），丢弃装饰图（logo/分隔线/版式件）——编译而非全量搬运。**只引不拷**、`../../raw/images/<slug>/<文件名>` 路径口径、`alt` 写法（vision 看图 / 无 vision 据上下文推断）详见 conventions §图片引用。实体/概念页同理。
3. **抽取实体/概念，建或更新页**（正文术语一律转 `[[wikilink]]`）。建页前按决定树走：

   ```
   扫 index.md + 现有页 aliases，是否已有该实体的变体页？
     是 → 更新既有页（把新变体追加进它的 aliases），绝不新建重复页
     否 → 该术语被多篇资料反复提及 / 是核心术语？
            是 → 建 entities/<Name>.md 或 concepts/<Name>.md
            否（偶现术语）→ 只写裸 [[X]] 前向引用，不建空桩页
   ```
   - 别名须全局唯一、不与任何页名同名（`check` 阻断撞名/重复）。
   - 偶现术语暂留断链是对的：随后续资料达到「被 ≥2 页引用」会由 `lint.missing_entity` 提示补页。**宁可暂留断链，不可造空页。**
   - **「链得密」与「建得省」是两条独立的轴，别混。** 正文**首次出现**的真实体/概念/方法名**一律转 `[[...]]`——漏链是缺陷，不是谨慎**；建不建页另由上面的决定树定。正因为裸 `[[X]]` 即便暂无对应页也只是合法前向引用（只记警告、不阻断），**链接本身零风险——不要因为怕断链而少链**。作用域限**真实体/概念/方法**：别把普通名词、修饰语、一次性口语都链了（过度链接会催生 `lint.hub_node` 噪声与 `lint.missing_entity` churn）。
   - **实质修改既有页（含追加 aliases）务必把 `last_updated` 改成当天**——否则页面新鲜度失真，后续过期论断复检无从判断。
   - **更新既有页 = 合并、不是覆盖**（门禁确定性兜底，违反即回喂自愈/警告）：
     - `sources`/`tags`/`aliases` 取**并集**（保留原有、追加本次新增）——**绝不**因本次只看一个源就把 `sources` 写成 `[本次源]`，那会丢掉旧来源（门禁 `sources.dropped` 阻断 + 回喂你自愈；ingest **只增不减**来源）。
     - 正文**增补融合、不整段重写**：保留原有论断与 `## ⚠️ 矛盾与存疑` 节，新信息追加或就地融入。把一张富页洗成只反映本次源的薄页是**腐蚀**（门禁 `body.shrank` 警告，请自查是否误删旧内容）。
     - `type` 不随手改（既有页的分类是稳定身份）。

> **两条建库期高频错误，记牢即可**（机制详见 conventions）：① frontmatter 字符串值（尤其 `title`）**一律用单引号**、**绝不双引号套双引号**——这是写门禁**会阻断**的硬错误（§frontmatter）；② 正文指向未建页的 `[[X]]` 是正常前向引用，写门禁只当**警告**不阻断——**别为消链接而提前造空桩页或删链接**（§wikilink）。

4. 更新 `wiki/index.md`（对应分区追加/修订一行）。`wiki/overview.md`（活体综述）**仅当本次引入新角度、新论断或新矛盾时才改**；纯增量细节由 entity/concept 页吸收，不必动 overview。
5. 若新资料与既有页**冲突**，就地在相关页维护 `## ⚠️ 矛盾与存疑` 节（格式见 conventions）。
6. 向 `wiki/log.md` 追加一条：`## [YYYY-MM-DD] ingest | <title>`。
7. **收尾**：见〈写工作流收尾（共用）〉——不自行跑 shell/check，wrapper 强制门禁 + 有界自愈。

### query（P2）— `guanlan query "…"` / `--backfill`

1. **先用可用的 search 入口拿 top-N 候选页路径**——都是同一确定性整页 BM25 召回（CJK 走 2-gram、**别名已纳入匹配面**，同义不同名时命中声明该别名的页），按手头能力择一：
   - 只读 Web 会话 → 宿主 `guanlan_search` 工具（无 shell 也能调）；
   - 有 shell → `guanlan search "<关键词>"` CLI；
   - 都不可用或空手而回（生僻/跨义）→ **退回**扫 `index.md` / 相关目录 / 现有页 `aliases`，或请用户补关键词（graceful fallback，原行为保留）。

   读召回到的候选页 + `wiki/index.md` 综合。
2. 读相关页，**综合出带 `[[页]]` 引用的答案**；无可靠来源时明说，不编造。引用页面**一律裸 `[[stem]]`**（从 `index.md` 目录行取材时把 `[标题](dir/stem.md)` 改写成 `[[stem]]`、勿照抄——否则不走站内导航，详见 conventions §wikilink）。
3. **默认只读**。仅当显式 `--backfill` 时把好答案回填 `wiki/syntheses/<slug>.md`（`type: synthesis`），并走与 ingest 同一套门禁。**synthesis 是时点快照**：它存档某次提问在当时的答案，ingest 不级联改写其知识内容（活体综述是 `overview.md` 的活儿）；知识变了就重新 `query --backfill` 一篇新页或显式覆盖，别去改旧答案。详见 conventions §页面模板 synthesis 节。

### heal（P3.2）— `guanlan heal`

把**高频缺失实体**（被 ≥2 页引用却无页的断链，即 `lint.missing_entity`）一次性物化成内容页。wrapper 已把本批**目标名 + 各自引用页清单**算好喂进 prompt（确定性事实，你不必自己找谁引用了它），你只需对每个目标：

1. **只读所列引用页**与 `wiki/index.md` 建上下文（别读全库；目标名已是归一键，可直接作文件名）。
2. **先判实体还是概念**，定目录 `<dir>`：实体（人物/组织/模型/系统）→ `entities/`；概念（方法/理论/术语、**算法/架构/训练技巧**等）→ `concepts/`；拿不准当实体。（本库若 `SCHEMA.md` 给了更具体去处则从它，但 heal 新建只允许落 `entities/`∪`concepts/`，自定义目录请走 ingest。）
3. 若上下文**足以确认该目标值得建页**，按决定树物化（建页一律落第 2 步定的 `<dir>/`）：

   ```
   目标是某既有 entities/∪concepts/ 页的变体？（读 index.md/引用页可认出）
     是 → C：只向该页 aliases 末尾追加原目标名
            （不新建重复页、不改正文、不动其它 frontmatter）
     否 → 目标名够规范、可直接当文件名？
            是 → A：建 <dir>/<目标>.md（默认最稳，[[原引用]] 经同一归一天然解析）
            否（口语/缩写，且引用上下文给出更规范全称）
                → B：建 <dir>/<规范名>.md + frontmatter aliases 收编原目标名
                     （如 aliases: ['大模型']；否则断链不消、回执 still_broken）
   拿不准 → 一律走 A
   ```
4. 建页/收编后**在 `wiki/index.md` 对应分区登记一行**（新页加 `- [<标题>](<dir>/<stem>.md) — <一句话>`，登记到与目录匹配的分区；B/C 在句末注记别名，如 `…（别名：大模型/LLM）`），让去重、2-gram 召回、health 同步都看得见它。
5. 向 `wiki/log.md` 追加一条 `## [YYYY-MM-DD] heal | <目标>`。
6. **只准从引用上下文合成，不臆造引用页里没有的事实**；`sources` 列引用页有出处可顺延、否则留空（合法）。上下文不足、目标更像主题页、或无法判定时，**跳过该目标**并用一句话说明（无需特定格式——wrapper 重算图判定成败，不解析你的状态文本）。

> **永不删除或覆盖重写已有页正文、永不修改 `raw/`**：heal 只**新建** `entities/`/`concepts/` 页、**向既有页纯追加别名收编本批目标**、编辑 `index.md`、追加 `log.md`。其余越界写（建到 `entities/`∪`concepts/` 之外 / 改正文 / 改非 aliases 字段 / 删页 / 在收编里夹带无关别名 / 把页换成符号链接）会被 wrapper 的写集审计标为 `unexpected_write`。其余收尾见〈写工作流收尾（共用）〉。

### 写工作流收尾（共用）

ingest / heal / `query --backfill` 等会写 `wiki/` 的工作流，收尾一致，记三条即可：

- **不自行运行 shell 或 `guanlan check`**；读写文件只用内置文件工具；只返回简短完成说明。
- **返回前回扫你写/改的每页正文**：首次出现的真实体/概念/方法名是否都已 `[[...]]`？漏的补上——这是离开前的最后一道自查（漏链是缺陷）。
- wrapper 会**强制门禁**：比对 `raw/` 前后快照（任何改动即失败）+ 跑 `guanlan check`，但只追究**本次新引入**的阻断性违规（frontmatter / `sources`；断链只记警告）。
- 有新阻断违规时 wrapper 把清单回喂你**自动修复（最多两轮）**——把 frontmatter 一次写对最省事。

**会让本轮失败的硬错误（速查，每条都有原因）**：

- **frontmatter 非法 YAML**（双引号套双引号 / 字符串没用单引号）→ `check` 阻断：YAML 解析失败，页面无法入库。
- **`sources` 缺失或写错 slug** → `check` 阻断：知识失去可追溯来源，违反「query 必引来源」。
- **既有页丢失原有 `sources` slug**（覆盖而非合并）→ `sources.dropped` 阻断 + 回喂自愈：ingest **只增不减**来源；本次只看一个源也不能把既有页 `sources` 写成 `[本次源]`，须并回旧来源（union）。
- **`aliases` 撞页名 / 库内重复** → `check` 阻断：`[[wikilink]]` 解析出现歧义（确定性危害，须即时修，不同于断链的警告）。
- **改动 `raw/` 任何字节**（含用 `mv`/`rm`/脚本绕过文件工具）→ `raw/` 前后快照失败：事实来源必须逐字不变。
- **heal 越界写**（建到 `entities/`∪`concepts/` 之外 / 改已有页正文 / 改非 `aliases` 字段 / 删页 / 用 symlink 换页）→ 写集审计标 `unexpected_write`：heal 只准新建+纯追加别名。
- 反面提醒：**断链只是警告、不阻断**——别为消链接而造空桩页或删 `[[X]]`，那才是真错误（前向引用会随后续资料自然消除）。
- （警告非阻断）**既有页正文骤缩** → `body.shrank`：疑似覆盖重写而非增补合并，自查是否误删旧论断（确属有意精简则放行）。

### 确定性脚本（零 LLM）

- `guanlan check`（P2）— 基础校验：frontmatter 合规 + wikilink 断链 + `sources` 解析。ingest / `--backfill` 收尾**强制**运行；亦可独立 shell 调用。实现在 `guanlan` 包内（`guanlan/check.py`），无 `scripts/`。
- `guanlan health`（P3）— 结构检查：空页/桩页、index 与磁盘同步。
- `guanlan reindex`（P3.4）— 与 `health.index_missing_page` 配对的零-LLM 修复器：把磁盘上漏登记进 `index.md` 的内容页自动补登记（`--dry-run` 预览 / `--prune` 清理悬空行）。只写 `index.md`，不碰 `raw/`、不调 LLM。
- `guanlan lint`（P3）— 图感知结构 lint：孤儿页、断链、缺失实体页。
- `guanlan graph`（P3）— 解析 `[[wikilink]]` → 边，输出 `graph.json` + 自包含静态 `graph.html`。

> `index.md` / `log.md` / `overview.md` / `SCHEMA.md` 是 config 非 content，**排除出 index/graph/lint 扫描**。
> LLM 只用于 ingest / query；其余工作流全部零 LLM。语义 lint（矛盾复检/过期论断/资料缺口）属 P3 之后。
