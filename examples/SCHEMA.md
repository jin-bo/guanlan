# SCHEMA — 本库约定

> `guanlan init` 生成的模板。这是 Karpathy 三层中的 **Schema 层**，与 `raw/`、`wiki/` 平级。
> 描述**本库**的领域/主题、启用的页面类型、自定义规则与演进论点；人与 Agent 共同演进，随 markdown 文件走。
> 通用默认约定（frontmatter/命名/index·log 格式/模板）在 `guanlan-wiki` skill 的 `references/conventions.md`，本文件可**覆盖**之。
> 本文件与 `index.md`/`log.md`/`overview.md` 一样**排除出 index/graph/lint 扫描**（config 非 content）。

## 领域 / 主题

<!-- 一两句话说明本库收什么、为谁服务。例： -->
本库聚焦 **<你的领域，如：大模型推理优化>**，沉淀论文要点、关键实体与概念，供长期复利式查询。

## 启用的页面类型

| 类型 | 用途 | 命名 |
|------|------|------|
| `source` | 单篇原始资料的摘要页 | `kebab-case`（同源文件名）|
| `entity` | 人物/组织/产品/系统等实体 | `TitleCase.md` |
| `concept` | 方法/理论/术语等概念 | `TitleCase.md` |
| `synthesis` | query 回填的跨资料综述 | `kebab-case` |

<!-- 删掉本库用不到的类型；新增类型也在此声明。 -->

## 本库自定义规则

<!-- 覆盖或补充 skill 默认约定的地方。例： -->
- （示例）实体页必须包含 `## 关键事实` 与 `## 相关` 两节。
- （示例）标签从受控词表取：`#方法` `#评测` `#数据集` `#系统`。

## 演进中的论点 / 偏向

<!-- 本库正在形成的判断、暂定结论、待验证假设。Agent 在 ingest/query 时参考。 -->
- （示例）暂以「<某假设>」为工作前提，遇反例需在相关页 `## ⚠️ 矛盾与存疑` 标记。
