# finding 稳定身份 + 可持久化抑制（backlog，未排期）

> **状态：backlog，未排期。** 从 [`../../P3.11-断链最近页建议.md`](../../P3.11-断链最近页建议.md) 评审中**拆出**——
> 该建议原把「断链最近页提示」与「finding 持久抑制」捆在一篇，评审判定后者明显偏重（改 CLI 操作面 +
> 引持久状态），不是断链提示的必要条件，故拆来此处独立留档，**待 advisory 刷屏成为真实痛点再排**。
>
> 源信号：兄弟项目 nashsu/llm_wiki 2026-06-29 pull（`d691e41 fix: content-stable review ids so
> resolved state survives regeneration`）把 Review 项 id 从模块级自增计数改为内容派生稳定 id。

## 0. 它解什么

`Finding(page, kind, detail)`（`guanlan/pages.py:103`）**无稳定身份、无 resolved 标记**；`run_lint`
（`lint.py:109`）/ `run_health` 每次**全量重算重报**。在大库上，一个**故意**的孤儿页、或某条拓扑建议
（`lint.hub_node` / `isolated_community` / `bridge_edge` …）会每次刷屏，用户无法说「这条我看过了，别再报」。

## 1. nashsu 的配方（只借形状）

- `reviewIdFor = FNV-1a(type + 归一化 title)`（`src/stores/review-store.ts`）——同一逻辑项跨重生成 / 改名 /
  重载得**同一 id**；`addItems` 对**含已解决项在内**的全集去重（resolved 胜）。
- **关键教训**：id 故意**排除可变字段**（`sourcePath`），只取 `type + 归一化 title`，否则一改名 id 就漂、
  「已解决」状态被当成新项复活。

## 2. 落到观澜会是什么样（若排期）

1. **稳定 key**：`key = 短哈希(kind + 结构化身份)`，身份取 `kind + target/page`，**排除易变 `detail`**
   （含「被 N 页引用」计数——纳入则计数一变 key 就变，正是 nashsu 排除 `sourcePath` 的同型坑）。
   落点：lint/health 在**构造 finding 时本就握有结构化身份**（`me.target` / `node.path` / `edge.target`），
   把它压进了 detail 文本；应在 producer 侧算 key，**别**事后正则解析 detail。
2. **抑制 overlay**：opt-in sidecar（如状态目录下 `lint-baseline.json`，体例类比已被接受的
   `.agentao/goals/<id>.json` 目标 sidecar）列被消音的 key；`guanlan lint --suppress <key>` 写入、
   `--show-suppressed` 旁路、文件人可读可手删。
3. **不变量守护**：核心 `run_lint` / `run_health` **保持纯函数**——照旧算全集；抑制只在 entrypoint /
   展示层过滤 + 从 `--strict` 退出码判定里剔除被消音项。markdown 仍唯一真相，sidecar 是可重建 config 态。

## 3. 为什么推后（张力）

- 给「当前纯函数 / 零持久化」的命令引入**持久状态**——与 P3.10 等明确珍视的性质相左；须严格做成
  opt-in 薄 overlay、核心不沾（不开 baseline 文件时行为字节不变），才不破体例。
- 改 CLI 操作面（新增 `--suppress` / `--show-suppressed`）+ 跨 `health`/`lint` 双命令覆盖 + 新状态模型，
  **面**比一条只读 advisory 大得多。
- **触发条件**：等「同一批 advisory 反复刷屏」成为实测痛点（而非臆测）再排；届时另起 `P3.x` 规格。

## 4. 代码位置速查

| 关注点 | 文件:行 |
|------|---------|
| nashsu 稳定 id 配方 | `llm_wiki/src/stores/review-store.ts`（`reviewIdFor`，`d691e41`） |
| 观澜 Finding（无身份/无 resolved） | `guanlan/pages.py:103` |
| 观澜 finding 结构化身份现存处 | `guanlan/lint.py`（`me.target` / `edge.target` / `node.path`） |
| 退出码 | `guanlan/errors.py`（`EXIT_LINT_FINDINGS`） |
| sidecar 体例先例 | `.agentao/goals/<id>.json`（P4.16） |
