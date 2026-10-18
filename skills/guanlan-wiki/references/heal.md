# heal 工作流 — `guanlan heal`

`guanlan-wiki` skill 的 heal 工作流。用词与硬约束见 [`../SKILL.md`](../SKILL.md)，页面格式见 [`conventions.md`](conventions.md)。


把高频缺失实体物化成内容页。高频缺失实体是被至少 2 页引用、却没有页的断链（`lint.missing_entity`）。wrapper 已在 prompt 里给出本批目标名和每个目标的引用页清单，你不必自己查找引用。对每个目标：

1. **读上下文**：只读所列引用页与 `wiki/index.md`，不要读全库。目标名已是归一键，可以直接用作文件名。
2. **定目录 `<dir>`**：
   - 实体（人物、组织、模型、系统）→ `entities/`。
   - 概念（方法、理论、术语、算法、架构、训练技巧）→ `concepts/`。
   - 拿不准时当实体。`SCHEMA.md` 只影响实体与概念的划分口径，不能增加第三个目录。
3. **三选一**：上下文足以确认目标值得建页时，按决定树处理。新页一律建在第 2 步定的 `<dir>/`：

   ```
   目标是某既有 entities/∪concepts/ 页的变体？（读 index.md/引用页可认出）
     是 → C：只向该页 aliases 末尾追加原目标名，并把 last_updated 改成当天
            （不新建重复页、不改正文、不动其它 frontmatter）
     否 → 目标名够规范、可直接当文件名？
            是 → A：建 <dir>/<目标>.md（默认最稳，[[原引用]] 经同一归一天然解析）
            否（口语/缩写，且引用上下文给出更规范全称）
                → B：建 <dir>/<规范名>.md + frontmatter aliases 收编原目标名
                     （如 aliases: ['大模型']；否则断链不消、回执 still_broken）
   拿不准 → 一律走 A
   ```

4. **登记 index**：在 `wiki/index.md` 与目录匹配的分区登记一行。
   - 新页：`- [<标题>](<dir>/<stem>.md) — <一句话>`。
   - B 与 C：在句末注明别名，如 `…（别名：大模型/LLM）`。这样查重、2-gram 召回与 `health` 都能看见它。
5. **写 log**：向 `wiki/log.md` 追加一条 `## [YYYY-MM-DD] heal | <目标>`。
6. **只从引用上下文合成**：不得写引用页里没有的事实。引用页有出处时，`sources` 可以沿用。没有出处就留空（合法）。
7. **跳过**：上下文不足、目标更像主题页、或无法判断时，跳过该目标，用一句话说明。不需要特定格式：wrapper 重新计算断链来判定成败，不解析你的说明。

**heal 的写入范围**：只新建 `entities/`、`concepts/` 页，向既有页追加别名，编辑 `index.md`，追加 `log.md`。不得删除或重写已有页正文，不得修改 `raw/`。以下越界写会被 wrapper 的写集审计标为 `unexpected_write`：

- 建页到 `entities/`、`concepts/` 之外。
- 改已有页正文，或改 `aliases`、`last_updated` 以外的 frontmatter。
- 删页，或把页换成符号链接。
- 收编时夹带本批目标以外的别名。

收尾见 SKILL.md〈写工作流收尾（共用）〉。
