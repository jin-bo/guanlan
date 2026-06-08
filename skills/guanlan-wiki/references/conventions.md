# 通用默认约定（conventions）

> `guanlan-wiki` skill 的默认约定，**按需载入**。这些是 wiki-无关、可复用的默认；
> 任何本库可在根级 `SCHEMA.md` 中**覆盖或补充**之（`SCHEMA.md` 优先）。

## 页面类型

| 类型 | 用途 | 目录 | 命名 |
|------|------|------|------|
| `source` | 单篇原始资料的摘要页 | `wiki/sources/` | `kebab-case`（同源文件名） |
| `entity` | 人物/组织/产品/系统等实体 | `wiki/entities/` | `TitleCase.md` |
| `concept` | 方法/理论/术语等概念 | `wiki/concepts/` | `TitleCase.md` |
| `synthesis` | query 回填的跨资料综述 | `wiki/syntheses/` | `kebab-case` |

子目录在首次写入对应类型页面时**自动创建**；空库 init 只生成 `index.md` / `log.md` / `overview.md`。

## frontmatter（统一，每页必带）

```yaml
---
title: '页面标题'
type: source | entity | concept | synthesis
tags: []
aliases: []           # 可选：本页的常用别名/变体名（见下「别名」）
sources: []           # 支撑本页的 source slug 列表
last_updated: YYYY-MM-DD
---
```

- **字符串值一律用单引号。** frontmatter 必须是合法 YAML，而标题/标签里常含 `"…"`（如 `"低值高报"型`）、`:`、`#`、`-` 等 YAML 元字符。**单引号** YAML 标量把 `"` 等当字面量、最稳：
  - `title: '"低值高报"型骗取出口退税'` ✅（双引号原样保留）
  - 值内出现**单引号**时翻倍转义：`title: 'it''s a case'` ✅
  - **切勿在双引号里再套双引号**（`title: ""低值高报"型…"` 会让 YAML 在第二个 `"` 处断裂解析失败）。
- `sources` 列 source 页的 slug（不含路径/扩展名），用于追溯与 `guanlan check` 校验。
- `aliases`（可选，仅 entity/concept 常用）列本页的常用别名/变体名（见下「别名」节）；缺省即可。
- `last_updated` 每次实质修改时更新为当天日期（ISO `YYYY-MM-DD`）。

## wikilink

- 正文用 `[[PageName]]` 链接实体/概念/资料；**大小写不敏感，按文件 stem 解析**。
- 例：`[[地下钱庄]]`、`[[POS机境外取现]]`。出现的实体/概念**一律链接**，便于交叉引用与建图。
- 指向尚未建页的 `[[X]]` 是建库期正常的前向引用，会随后续资料加入自然消除：**写门禁把断链当警告、不阻断**（决策8），不要为消链接而提前造空桩页或删链接。独立 `guanlan check` 仍把断链全量报告，供按需审计。
- **引用其它页面的首选写法只有一种：裸 `[[stem]]`。** 它天然不含 `sources/` 前缀与 `.md` 后缀，显示干净、语义明确、解析与否都不破排版。引用一律用页面 stem（路径前缀与 `.md` 后缀**均省略**，大小写不敏感）：写 `[[d8-打击涉虚拟货币网络传销犯罪]]`，而非把它写成 `wiki/sources/d8-….md` 这类路径。模型常把"看起来像文件路径/标识符"的引用（尤其**源出处**）习惯性写成路径或套反引号——这是引用失链的头号原因，务必改用裸 `[[stem]]`。本条对**页面正文与对话答复同样适用**。解析器兼容 `[[target|别名]]`、`[[target#锚点]]`、路径与 `.md` 后缀，但这些只是兼容能力，不是首选写法。
- **从 `index.md` 目录行（或任何 `[标题](dir/stem.md)` 形态）取材引用页面时，必须改写成裸 `[[stem]]`**：如目录行 `- [五查四定](concepts/五查四定.md) — …`，在正文/答复里引用应写 `[[五查四定]]`，而非照抄成 `[五查四定](concepts/五查四定.md)`。目录行的 markdown 链接只服务 `index.md` 自身的目录排版，不是页面引用的首选写法；照抄它会渲染成普通相对链接、**不走站内导航**——这是除"源出处套路径/反引号"外的另一条高发失链路径。
- 渲染器有一道**兜底**（仅救场，勿当首选）：当**整段恰好是单条页面引用**的**行内 `code`**（如 `` `[[d8-打击涉虚拟货币网络传销犯罪]]` ``、`` `wiki/sources/d8-….md` `` 或 `` `d8-打击涉虚拟货币网络传销犯罪` ``）时，会破例转成站内链接或断链标记。但它**只覆盖**这一种情形——下列均**不会**成链，仍须靠裸 `[[stem]]`：
  - **不带反引号的裸路径/裸名**写在正文里（渲染器不猜测普通文字）；
  - **命令或混合文本里的引用**（如 `` `cat x.md` ``、`` `cat [[X]]` ``、`` `git status` ``——整段不等于单条页面引用）；
  - **围栏代码块 / 缩进代码块**——代码块字面语义不破（决策P4-3）。注：路径/stem 兜底**只看整段是否忠实等于某现有页**，故含空格的合法页名（如 `` `Smart Tools分析研判模块` ``）也能成链。

## 别名（aliases）

entity/concept 页可在 frontmatter 声明 `aliases`，把「同义不同名」的变体收敛到同一页（中文经侦域高发：「大模型」/「LLM」/「大语言模型」、「地钱」/「地下钱庄」、中英缩写混用）。**别名进入 `[[wikilink]]` 解析命名空间**（与页面 stem 同口径、大小写不敏感、零 LLM）：

```yaml
aliases: ['LLM', '大模型', 'large language model']
```

- **作用一：消假断链。** 声明后 `[[LLM]]`、`[[大模型]]` 都解析到该页，不再被 `check`/`lint`/`graph` 误报断链；Web 里也成站内链接。
- **作用二：补 CJK 召回。** query 的 2-gram 粗召回把别名串也纳入匹配面——`index.md` 对应行可在句末附常用别名（如 `… — 一句话（别名：大模型/LLM）`），让别名词命中召回。
- **去重纪律（ingest）**：建新 entity/concept 页**前**，先扫 `index.md` 与现有页 `aliases` 看是否已是某页的变体；命中就**更新既有页**（必要时把新变体追加进其 `aliases`），**绝不新建重复页**。这把过去「在正文补一行常用别名」升级为结构化、可被解析器消费的声明。
- **全局唯一（`check` 校验，阻断）**：归一别名不得与任何页面 stem 同名（`aliases.collides_stem`）、不得在库内重复声明（`aliases.duplicate`）、须为非空字符串列表（`frontmatter.bad_type`）。撞名/重复**阻断写门禁**（与断链「警告非阻断」相反——它是解析歧义、确定性危害，须即时修）。
- 键名对齐 Obsidian `aliases`，便于用户直接用 Obsidian 打开同一目录。实现细化见 `docs/P3.1-别名解析.md`。

## heal 建页（`guanlan heal`）

`heal` 把高频缺失实体（`lint.missing_entity`）物化成页，纪律是 ingest 建页的子集，额外几条硬线（先分类定目录、A/B 见命名、C 见收编）：

- **先分类定目录 `<dir>`**：判目标是**实体**（人物/组织/产品/系统 → `entities/`）还是**概念**（方法/理论/术语、**战法/办案手法**等 → `concepts/`），A/B 即建到 `wiki/<dir>/`；拿不准当实体。heal 新建只允许落 `entities/`∪`concepts/`，越界目录会被写集审计标 `unexpected_write`（库定义的自定义目录请走 ingest，不走 heal）。
- **A 文件名 = 目标归一键**（默认最稳）：wrapper 给的目标名已是 `link_stem` 归一键（剥 `|别名`/`#锚点`/`.md`、小写）。**直接用它作文件名** `<dir>/<目标>.md`，则原引用 `[[X]]` 经同一归一必然解析。
- **B 改名必收编别名**：若判断目标名口语化、想用更规范标题当 stem（`大模型` → `大语言模型.md`），**必须**在 frontmatter `aliases` 收编原目标名（`aliases: ['大模型']`），否则 `[[大模型]]` 仍断、wrapper 回执报 `still_broken`。拿不准就走 A。
- **C 收编到既有页**：目标其实是某**已有** `entities/`/`concepts/` 页的变体时，**只向该页 `aliases` 末尾追加原目标名**，**不新建重复页**。这是 wrapper 唯一容许 heal「碰已有页」的窄缝，必须满足全部：**只在 aliases 末尾追加**（原有别名原序原次保留、一个不删不重排）、**正文与其它 frontmatter（title/type/tags/sources）一字不动**（`last_updated` 可改）、**新增别名只收编本批目标**（不夹带无关别名）。任一不满足都会被写集审计标 `unexpected_write`。
- **登记 index.md**：建页/收编后在 `index.md` 对应分区登记一行（B/C 句末注记别名），让去重 / 2-gram 召回 / health 同步看得见——遗漏不当轮失败，但 `health` 会报 `index_missing_page`。
- **只新建/纯追加、不臆造**：heal 只**新建** `entities/`/`concepts/` 页、**向既有页纯追加别名**、编辑 `index.md`、追加 `log.md`，**绝不删除/覆盖重写已有页正文、不碰 `raw/`**。正文只准从所列引用页合成，缺则跳过或写最小桩页（`health` 另行标记桩页），不得编造 `sources`。
- **跳过无需格式**：上下文不足 / 目标更像主题页 / 无法判定时，跳过并口头说明即可——正确性由 wrapper 重算图判定，不读你的状态文本。

## index.md

按固定分区组织，每行 `- [标题](相对路径) — 一句话`：

```
## Overview
- [总览](overview.md) — 跨资料的活体综述

## Sources
- [<标题>](sources/<slug>.md) — <一句话>

## Entities
- [<名称>](entities/<Name>.md) — <一句话>

## Concepts
- [<名称>](concepts/<Name>.md) — <一句话>

## Syntheses
- [<标题>](syntheses/<slug>.md) — <一句话>
```

Overview 区**仅链到 `overview.md`，不重复其正文**。每次 ingest 在对应分区追加/修订一行。

## log.md

append-only 时间线，每条一行标题，可被 `grep "^## \[" wiki/log.md | tail` 解析：

```
## [YYYY-MM-DD] <op> | <title>
```

`<op>` ∈ `init` / `ingest` / `query` / `backfill` / `check` …；`<title>` 取资料或操作标题。

## 矛盾标记（卖点核心，固定格式）

ingest 时若发现新资料与既有页冲突，**就地**在相关 entity/concept 页维护一节：

```
## ⚠️ 矛盾与存疑

- <断言 A>（[[source-a]]）↔ <断言 B>（[[source-b]]）— <open|resolved> · <时序变化|数据冲突|解释分歧> · <备注>
```

- **状态**：`open`（未决）/ `resolved`（已厘清）。
- **类型**（区分"为什么矛盾"，帮助后续处置）：`时序变化`（旧数据被新数据取代，如某指标随时间更新）/ `数据冲突`（两个来源对同一事实给出不相容值）/ `解释分歧`（事实一致但定性/解读不同）。类型可省略时默认按 `数据冲突` 处理。

并向 `log.md` 追加一条。**有无矛盾以 `## ⚠️ 矛盾与存疑` 标题为准**（不另存 frontmatter 布尔）。ingest 只做"发现即就地标记"（含打上述类型标签）；系统性复检与状态流转（open→resolved）属语义 lint（P3 之后），确定性 health/lint 不统计矛盾。

## 页面模板

**source（摘要页）**

```markdown
---
title: '<资料标题>'
type: source
tags: []
sources: ['<本页 slug>']
last_updated: YYYY-MM-DD
---

# <资料标题>

> 原始资料：`raw/<原文件名>`

## 要点

- …

## 关联

- 实体：[[…]]
- 概念：[[…]]
```

**entity / concept**

```markdown
---
title: '<名称>'
type: entity   # 或 concept
tags: []
sources: []
last_updated: YYYY-MM-DD
---

# <名称>

<一段定义/概述，正文术语转 [[wikilink]]>

## 关键事实

- …（每条标注支撑 source，如 ([[source-slug]])）

## 相关

- [[…]]
```

**synthesis（query 回填）**

```markdown
---
title: '<问题/主题>'
type: synthesis
tags: []
sources: []
last_updated: YYYY-MM-DD
---

# <问题/主题>

<带 [[页]] 引用的综合答案>
```

## CJK 检索

query 对中文用 **2-gram 滑窗**匹配 `index.md` 标题/摘要做粗召回；粗召回不足时扫相关目录或请用户补关键词（graceful fallback）。**不优先上分词**；逐级增强备选见 `docs/backlog/notes/cjk-retrieval-enhancements.md`。

## 不纳入扫描的 config 文件

`index.md` / `log.md` / `overview.md` / `SCHEMA.md` 是 config 非 content，**排除出 index/graph/lint 扫描**。`raw/` 只读，不参与 wiki 页面校验。
