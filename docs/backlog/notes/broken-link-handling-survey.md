# 断链处理调研：三个参考实现的取法（backlog）

> 状态：**调研笔记，非排期项**。记录三个同源参考项目在「断链（broken wikilink / 指向不存在页面的 `[[…]]`）」上的取法与取舍，供观澜后续（DESIGN §8 自动富化、`heal` 类工具、向量检索）评估时参照。**本笔记不改变现状**：观澜当前对断链的处理见 DESIGN §4.6 / P3 决策 P3-4、P3-6，结论稳定。
> 关联：`docs/DESIGN.md` §3「参考实现对比」、§4.6、§8；`docs/P3-健康与图谱.md` 决策 P3-2 / P3-4 / P3-6。

## 0. 为什么记这个

断链在 Karpathy LLM Wiki 模式里不是「错误」而是**生长信号**——前向引用先于页面存在是常态。但「发现后做什么」三个参考项目分歧很大，构成一条从**积极物化补页**到**严格拒绝悬空**的光谱。观澜选了中间偏保守的一档（报告不阻断、建议非门禁、不自动修复），这条光谱是当时取舍的依据，值得留档。

## 1. 一表总览

| 项目 | 检测者 | 断链怎么处理 | 自动修复 | 哲学 |
|------|--------|------------|---------|------|
| **llm-wiki-agent**（个人版蓝本） | 确定性 Python 脚本 | 报告 + **高频断链 LLM 自动建页** | ✅ `heal.py` 物化 | **积极补页**：断链 = 该建页 |
| **nashsu/llm_wiki**（桌面应用） | 前端 TS `lint.ts` + Rust | 图里**直接忽略**；Lint UI 列警告 + 人工选项 | ⚠️ 仅 orphan 自动加 index 链接 | **图洁净**：不污染图，交人决策 |
| **gbrain**（企业版蓝本） | 确定性解析器 + **DB 外键约束** | **插入时即拒绝**悬空边；记 `unresolved` 审计 | ❌ 只报不修 | **严格保守**：宁缺勿悬空 |
| **观澜（现状）** | 确定性脚本 check/lint/graph | 报告但不阻断；≥2 页引用 → 建议建页 | ❌ 交 Agent 在 ingest 时自然补 | **建议非门禁** |

共性：**检测全部是确定性零 LLM**（正则提 `[[…]]` + 名称→页面解析）；分歧只在「发现后」。

## 2. llm-wiki-agent —— 断链分级，高频者自动物化

**最积极的一档。** 把断链按引用频次分级，越被多页引用越值得自动补。

- **检测**（`tools/lint.py:87-94`）：`find_broken_links()` 逐页提 `[[...]]`，`page_name_to_path()` 解析为空即断链。纯确定性。
- **分级处理**（独特设计）：
  - `[[X]]` 被 **≥3 页**引用却无页 → **Missing Entity** → `tools/heal.py:54-97` 调 **LLM** 从引用上下文生成完整实体页（自带 frontmatter）。
  - `[[X]]` 被 **≥2 页**引用 → **Phantom Hub**（`tools/build_graph.py:413-441`），按引用数排序写进 `graph-report.md`，作为「优先建页」清单。
  - 普通断链 → 仅报告，人工修。
- **要点**：检测确定性、修复用 LLM，但**只对高价值（高频）断链自动补**，以频次阈值压制 LLM 幻觉面。

## 3. nashsu/llm_wiki —— 图里看不到断链，交人决策

**把断链挡在图之外。**

- **图构建忽略断链**（`src/lib/wiki-graph.ts:288-304`；Rust 侧 `src-tauri/src/api_server.rs:1147-1155`）：`resolveTarget()` 解析失败返回 `null` → `continue`，**既不建占位节点也不建悬空边**。前端 TS 与 Rust 后端两处逻辑刻意一致。与 Obsidian 的「幽灵节点」相反。
- **Lint UI 暴露**（`src/components/lint/lint-view.tsx`）：断链以 `warning` 卡片列出（`Link2Off` 图标），给三选项——打开编辑 / 删除该页（级联清 embedding + 反链）/ 跳过。**无自动修复**。
- **对照 orphan**：入度 0 的页**会**自动加 `[[链接]]` 到 `index.md`——可见它对「断链」与「孤儿」采取了相反力度（孤儿自动接、断链交人）。

## 4. gbrain —— 数据库层根除悬空

**最严格。** 生产级 daemon，靠存储层让悬空边根本不可能存在。

- **外键 CASCADE**（`src/core/pglite-schema.ts:252-275`）：`links` 表 `from/to_page_id` 均 `REFERENCES pages(id) ON DELETE CASCADE`——DB 层面不可能有悬空边，删页即级联删边。
- **JOIN 式插入**（`src/core/pglite-engine.ts:2403-2470`）：建边 SQL 用 JOIN 要求两端页面都在，目标缺失 → 插入 0 行，边被静默跳过，绝不建占位节点。
- **unresolved 审计**（`src/core/link-extraction.ts:1052-1056`）：frontmatter 引用解析失败的名字记入 `unresolved[]` 上报，不建边。
- **dream cycle 不自动修**（`src/core/cycle.ts`）：backlinks 阶段是 `action:'check'` 非 `'fix'`；orphans 阶段只计数报警（>50% warn / >80% fail）；dangling alias 只给手工 GC SQL。**全靠人工/显式命令收尾**。

## 5. 观澜现状（对照，非本笔记改动）

观澜立场最接近「gbrain 的报告不阻断」叠加「llm-wiki-agent 的 ≥N 页引用 → 建议建页」，但**刻意不自动修复**：

- 检测在确定性脚本：`check.py:90-100`（`wikilink.broken`）、`lint.py:54-74`（`lint.broken_link` + `lint.missing_entity`，阈值 `MISSING_ENTITY_MIN_REFS=2`）、`graph.py:72-134`（建图时分类）。解析口径单点定义在 `pages.py`（`WIKILINK_RE` / `link_stem`），三命令复用。
- **断链不阻断写门禁**（决策 8）：`check` 记 violation 但不决定 `ok`，退出码只看 frontmatter/sources。
- **建议非门禁**（决策 P3-4）：`lint` 默认 `EXIT_OK(0)`，仅 `--strict` 下有 findings 才 `EXIT_LINT_FINDINGS(6)`。
- **同源同口径**（决策 P3-6）：`graph.broken ≡ check.wikilink.broken`，均排除指向 config 页（`index`/`log`/`overview`）的链接。
- **修复留给 Agent**：断链由 ingest 时 Agent 自然补页，无独立 `heal`。

## 6. 给观澜后续的备选信号

真要在断链上加力时（**均非 MVP，需实证驱动**），三个参考项目提供了现成路径：

1. **`heal` 类工具（参考 llm-wiki-agent `heal.py`）** —— 把 `lint.missing_entity`（≥2 页引用）从「建议」升级为「LLM 自动物化实体页」。这正是观澜有意推迟的写/读分离边界：自动建页是**写**操作，需走 P2 子进程 + 单写者门禁，不能塞进读路径。落地前另开实现方案文档。
2. **别名解析消解假断链** —— 与 `cjk-retrieval-enhancements.md` 第 1 条耦合：entity/concept 页加 `aliases` frontmatter 后，`[[别名]]` 不再误报断链。属断链「降噪」而非「修复」。
3. **向量检索补语义召回** —— nashsu/gbrain 均用向量 + 混合检索；断链中相当一部分是「同义不同名」，向量检索可在解析层先消解一批，再谈物化。
4. **不采纳的方向**：gbrain 的 DB 外键「宁缺勿悬空」**不适用**观澜——纯 markdown 无存储约束层，且 Karpathy 模式明确要让前向引用断链作为生长信号存在，强一致会扼杀生长。

## 7. 代码位置速查

| 项目 | 功能 | 文件:行 |
|------|------|---------|
| llm-wiki-agent | 断链检测 | `tools/lint.py:87-94` |
| llm-wiki-agent | 缺失实体阈值（≥3） | `tools/lint.py:97-107` |
| llm-wiki-agent | LLM 自动建页 | `tools/heal.py:54-97` |
| llm-wiki-agent | Phantom Hub（≥2 排序） | `tools/build_graph.py:413-441` |
| nashsu/llm_wiki | 图构建忽略断链 | `src/lib/wiki-graph.ts:288-304` |
| nashsu/llm_wiki | Rust 侧链接解析 | `src-tauri/src/api_server.rs:1147-1155` |
| nashsu/llm_wiki | Lint UI + 人工修复 | `src/components/lint/lint-view.tsx` |
| gbrain | 外键 CASCADE | `src/core/pglite-schema.ts:252-275` |
| gbrain | JOIN 式拒绝悬空边 | `src/core/pglite-engine.ts:2403-2470` |
| gbrain | unresolved 审计 | `src/core/link-extraction.ts:1052-1056` |
| gbrain | dream cycle 只检不修 | `src/core/cycle.ts` |
| 观澜 | 断链检测（check） | `guanlan/check.py:90-100` |
| 观澜 | 断链 + 缺失实体（lint） | `guanlan/lint.py:54-74` |
| 观澜 | 建图分类断链 | `guanlan/graph.py:72-134` |
| 观澜 | 解析口径单点 | `guanlan/pages.py`（`WIKILINK_RE` / `link_stem`） |
