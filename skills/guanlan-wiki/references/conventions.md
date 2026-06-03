# 通用默认约定（conventions）

> `guanlan-wiki` skill 的默认约定，**按需载入**。这些是 wiki-无关、可复用的默认；
> 任何本库可在根级 `SCHEMA.md` 中**覆盖或补充**之（`SCHEMA.md` 优先）。

## 页面类型

| 类型 | 用途 | 目录 | 命名 |
|------|------|------|------|
| `source` | 单篇原始资料的摘要页 | `wiki/sources/` | `kebab-case`（同源文件名） |
| `entity` | 人物/组织/模型/系统等实体 | `wiki/entities/` | `TitleCase.md` |
| `concept` | 方法/理论/术语等概念 | `wiki/concepts/` | `TitleCase.md` |
| `synthesis` | query 回填的跨资料综述 | `wiki/syntheses/` | `kebab-case` |

子目录在首次写入对应类型页面时**自动创建**；空库 init 只生成 `index.md` / `log.md` / `overview.md`。

## frontmatter（统一，每页必带）

```yaml
---
title: "页面标题"
type: source | entity | concept | synthesis
tags: []
sources: []           # 支撑本页的 source slug 列表
last_updated: YYYY-MM-DD
---
```

- `sources` 列 source 页的 slug（不含路径/扩展名），用于追溯与 `check.py` 校验。
- `last_updated` 每次实质修改时更新为当天日期（ISO `YYYY-MM-DD`）。

## wikilink

- 正文用 `[[PageName]]` 链接实体/概念/资料；**大小写不敏感，按文件 stem 解析**。
- 例：`[[Transformer]]`、`[[注意力机制]]`。出现的实体/概念**一律链接**，便于交叉引用与建图。
- ingest / `--backfill` 收尾由 `check.py` 校验断链（指向不存在页面即失败）。

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

- <断言 A>（[[source-a]]）↔ <断言 B>（[[source-b]]）— <open|resolved> · <备注>
```

并向 `log.md` 追加一条。**有无矛盾以 `## ⚠️ 矛盾与存疑` 标题为准**（lint/health 直接 grep 该标题统计，不另存 frontmatter 布尔）。ingest 只做"发现即就地标记"；系统性复检与状态流转（open→resolved）属语义 lint（P3 之后）。

## 页面模板

**source（摘要页）**

```markdown
---
title: "<资料标题>"
type: source
tags: []
sources: ["<本页 slug>"]
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
title: "<名称>"
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
title: "<问题/主题>"
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
