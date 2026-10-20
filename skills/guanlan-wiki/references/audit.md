# audit 工作流 — `guanlan audit`

`guanlan-wiki` skill 的 audit 工作流。用词与硬约束见 [`../SKILL.md`](../SKILL.md)，页面格式见 [`conventions.md`](conventions.md)。


**语义审计**：复核「资料变了、wiki 页还没重新综合」的过期论断（source-drift）。audit 与 heal 一样经过写门禁。

wrapper 已完成确定性粗筛：比对每张 source 页 frontmatter 的 `raw_digest`（资料内容指纹，见 conventions §frontmatter）与资料现在的字节，找出变化的资料，以及沿 `sources` 引用它们的页。prompt 里每行一个目标：`page | reason | drifted_slugs | raw_paths`。你只做语义判断，不必自己找变化的资料或解析指纹。

对每个目标页：

1. **对照现版本**：读该页正文，以及该行 `raw_paths` 列出的资料的**现版本**，判断页中论断是否仍被支持。
   - `reason=source-drift`：该页就是 source 页本身（资料记在它的 `raw_digest` 里，不在 `sources` 里）。对照本页摘要与资料现版本。
   - `reason=cites-drifted-source`：对照本页基于这些资料的综合是否仍成立。
2. **处理正文**：
   - 仍准确：不改正文。`raw_digest` 由 wrapper 在你返回后刷新。
   - 已过期，或现版本与页面冲突：按 conventions §冲突与待核标记 就地标注，或最小化更新该论断。其余正文一字不动，不得整段重写。
3. **逐页留痕（必须，这是唯一凭据）**：在**唯一一条** `## [YYYY-MM-DD] audit | <一句话批次说明>` 标题下，为每个目标页追加一行单行 JSON：

   ```
   - {"page":"<相对库根 posix>","drifted_slugs":["…"],"status":"confirmed|flagged|updated"}
   ```

   - `drifted_slugs` 照抄该目标行给出的值，不增不删。
   - `status`：仍准确写 `confirmed`，标注存疑写 `flagged`，改了论断写 `updated`。
   - wrapper 只凭这些行判定「整组复核完成，可以刷新指纹」。漏写某页、`drifted_slugs` 写错、写成多段 audit 标题、写成非法 JSON，都会让该页所在的整组不刷新，下次重审。
   - 本次只新增这一段 audit 标题，不得修改历史 log。

**audit 的写入范围**：只就地标注或最小更新 `wiki/` 内容页，并追加一段 `log.md`。不得修改 `raw/`，不得整段重写正文。既有 `sources` 只增不减（`sources.dropped` 阻断）。不得写或改 `raw_digest`。收尾见 SKILL.md〈写工作流收尾（共用）〉。
