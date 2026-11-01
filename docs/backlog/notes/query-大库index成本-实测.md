# 大库问答读 `index.md` 的成本：实测否决

> 状态：**结论笔记，不立阶段**。日期 2026-10-31，代码基线 `2019e41`。只读统计本机五个真实库的 Web 会话与 `agentao.log`，不调用模型、不改库；库内容与库名不入本笔记，按页数排为 A–E。

## 0. 一句话

假设是「大库上问答要读整份 `index.md`，被 agentao 4 万字落盘规则截断或分段，成本明显」。实测**不成立**：Web 只读问答 66 轮里只有 2 轮读了 index，Agent 实际走 `guanlan_search` → 读候选页。**不立阶段**，只记一处措辞与行为不一致（§3）。

## 1. 起因

- ingest 已有页面名录，不再整读 index（`SKILL.md` ingest 第 2 步）；query 的措辞仍是「读候选页与 `wiki/index.md`」（`SKILL.md` query 第 2 步、`guanlan/query.py` 的 CLI 提示词）。
- 最大两库 index 体积远超 4 万字：

| 库 | content 页 | index 字符 |
|---|---:|---:|
| A | 1,534 | 185,028 |
| B | 773 | 163,980 |
| C | 848 | 63,114 |
| D | 119 | 9,524 |
| E | 43 | 2,577 |

## 2. 实测

### 2.1 Web 会话（`.agentao/sessions/*.json`，每库保留最近 10 个）

按真实 user 消息切轮（剥 `<system-reminder>` 后为空的不算），轮内调用过 `write_file`/`replace` 记为写轮，否则为读轮（问答）。

| 库 | 读轮 | 其中读 index 的轮 | 检索调用 | 读轮请求数中位 |
|---|---:|---:|---:|---:|
| A | 10 | 1（该轮 index 占工具结果 82%，被截断） | 26 | 4 |
| B | 19 | 0 | 27 | 3 |
| C | 14 | 0 | 3 | 3 |
| D | 16 | 1（index 占 22%，未截断） | 21 | 4 |
| E | 7 | 0 | 6 | 6 |
| **合计** | **66** | **2** | 83 | — |

写轮（入库 / 回填）共 11 轮，读 index 多为 `search_file_content` 定位后带 `offset`/`limit` 的局部读，index 占工具结果中位不超过 3.6%，与 `ingest-next-optimizations.md` §2 的结论一致。

### 2.2 `agentao.log`（Web 宿主日志，含已轮转的两份）

全部 `read_file` 679 次，其中目标是 `index.md` 的 23 次（3.4%），16 次带 `offset`/`limit`；另有 11 次 `search_file_content` 在 index 内定位。日志里只有 `guanlan.web.chat` 一个来源。

### 2.3 覆盖缺口（照实写）

- **CLI `guanlan query` 不在样本内**：`agentao run` 子进程不写进库内 `agentao.log`，本机也没有其他留存。CLI 提示词明确要求读 index，模型若照做，库 A 上会整读 18.5 万字。本次没跑真实 CLI query 验证。
- 会话只保留最近 10 个，样本偏近期；读轮判定只看有无写工具，`--backfill` 类回填算写轮。

## 3. 仅记一笔：措辞与行为不一致

`SKILL.md` query 第 2 步与 `query.py` 都要求读 index，实际 Agent 在有检索时基本不读——是模型在帮措辞兜底。可选的小改动：把「读候选页 + `wiki/index.md`」改成「读候选页；需要按分区或标题定位时，用 `search_file_content` 查 index 再读该段」，与 ingest 的写法对齐。

**不现在做的理由**：没有观测到成本问题；改措辞要过一次「skill 改动需重新激活」的发布说明。若日后 CLI query 在大库上实测出整读 index，或检索不可用的会话里出现整读，再改。

## 4. 复现

统计脚本只读会话 JSON 与日志，输出聚合数（不打印内容）：按 `tool_call_id` 把 `role: tool` 的结果配回 assistant 的 `tool_calls`（`tool_calls` 为 Python repr 字符串，需 `ast.literal_eval`），按参数里的 `file_path` / `file_pattern` 认出 index 读取。日志按 `Tool: read_file` 后的 `Arguments` 块匹配 `"file_path": "…index.md"`。
