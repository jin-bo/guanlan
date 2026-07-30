# swarmvault v3.21.0 反向评审（backlog）

> 状态：**反向评审结论，非排期项**。键定 **2026-07-11 pull**（fast-forward `4ce0c7c→815412d`，**v3.20.0→v3.21.0**，3 个 commit）。**不改变现状，只借形状、不借实现。**
>
> 项目：`swarmclawai/swarmvault`（TypeScript / pnpm 单仓，SQLite FTS5 检索 + Obsidian 插件 + MCP + 桌面 viewer；与观澜同属 Karpathy LLM-wiki 家族的对照实现）。本篇是**首次**为 swarmvault 建评审 note——但本次 pull 面很小（3 commit），故只键定增量、不回溯全史。
>
> 关联：[`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md)（2-gram vs 分词/trigram，决策P5.0-7/18）、[`../../P5.0-检索层.md`](../../P5.0-检索层.md)、[`../../P5.3-检索backlink重排.md`](../../P5.3-检索backlink重排.md)（`CorpusCache` 静默降级点）、[`../../P3-健康与图谱.md`](../../P3-健康与图谱.md)（`health.index_dangling`）、[`llm_wiki-反向评审-v0.6.md`](llm_wiki-反向评审-v0.6.md)、[`gbrain-反向评审结论.md`](gbrain-反向评审结论.md)。

## 0. 一句话

本次 pull 三件事：① CJK trigram 分词接入 FTS5（`42aa667`）；② provider `maxOutputTokens` 可配 + 默认 1200→4096，外加一次「让静默降级发声」的可观测性整改（`eb61020`）；③ release 3.21.0（`815412d`，纯版本号）。过观澜红线后，**唯一站得住的可借点是②的可观测性主题**——对应**一项立即小动作（§2）**；CJK trigram 是观澜 2-gram 的**反例而非缺口**，`maxOutputTokens` 与 `missing_page_file` lint 均 N/A 或已覆盖（§4）。

## 1. 新增缺口（真实的）

- **A. 长驻宿主 search 的静默降级零信号**：`CorpusCache` 里两处 `except OSError` 会**悄悄降级、不留任何信号**——
  - `backlinks()`（[`guanlan/search.py`](../../../guanlan/search.py) `except OSError: return {}`）：某页在建图期突然不可读 → **退回纯 BM25**、丢掉反链先验，名次无声改变；
  - `corpus()` 的 `build_doc`（同文件 `except OSError: continue`）：不可读页被**悄悄踢出语料**。

  这正是 swarmvault `eb61020` 修的同款模式（provider 分析失败 → 静默走 heuristic，用户不知为何结果变弱/变慢），它的解法是加 `warnHeuristicFallback` 往 stderr 发一条告警。**观澜的确定性/显式性气质本就该讨厌这种无声降级**。→ §2。
  - 注意**仅长驻宿主（web/mcp）路径有此缺口**：CLI 冷算 `search_pages()` 的 `compute_backlinks(build_graph(...))` **无** try/except，OSError 会照常冒泡（有栈可查），不在本缺口内。

## 2. 最小动作（立即做，一项）

1. **`CorpusCache` 两处 OSError 降级补一条 stderr 告警**：`backlinks()` 退纯 BM25、`corpus()` 丢页时各发一行 `[guanlan] 警告：… 降级为…（下次冷算自愈）`。**行为绝不改**（best-effort + 冷算恒权威的 P5.0/P5.3 设计要守，决策P5.0-14/P5.3-6）——只补可观测性。**硬约束**：只写 **stderr**、绝不碰 stdout（MCP 走 stdio，stdout 必须洁净，见 [`../../P4.10-MCP宿主.md`](../../P4.10-MCP宿主.md)）；措辞含**页路径 + 降级去向 + 底层错误**，对齐 swarmvault `warnHeuristicFallback` 的三要素。**不引** logging 框架 / 不加配置开关 / 不改 CLI 冷路（冷路已有栈）——只堵这一个最小可观测性面。

## 3. 仅观察（不排期）

**`GUANLAN_DEBUG=1` 全栈逃生口——待实证再议**。swarmvault 的 `SWARMVAULT_DEBUG=1`（顶层 catch 印完整 stack + `cause` 链，平时印一行发现性提示）看似诱人，但**观澜适配面比它窄**：观澜 `cli.main()`（[`guanlan/cli.py`](../../../guanlan/cli.py)）**无顶层 try/except**，`GuanlanError` 各子命令自 catch 映射退出码、**意外异常本就走 Python 默认 traceback**（已有栈）。真被吞栈的只有**局部有意点**——`convert.py`（IO 失败 → 回滚 + EXIT_USAGE，不外抛栈）、`web/app.py`（异常转 error 帧，不泄栈到流）、`runtime.py`（skill 缺失提示）。这些是**刻意的干净化**，目前**无实证被咬**。→ 若日后出现「convert/web 报 EXIT_USAGE/error 帧但查不出根因」的真实报障，再考虑在**那几个局部吞点**加 `GUANLAN_DEBUG` 门（而非照抄 swarmvault 的单一顶层门——观澜顶层根本不吞）。**先不做。**

## 4. 不做什么（park / 别借，附理由）

- **CJK trigram 分词（`42aa667`）—— 反例，别借，反证观澜 2-gram**：swarmvault 给 FTS5 建表加 `tokenize='trigram'`，把连续 CJK 段**整段**留作 token。**硬伤：FTS5 trigram 要求 ≥3 字才能匹配**（其自身注释 `trigram requires >=3 chars`）——对中文**双字词占绝对多数**的语料（`物权`/`扩容`/`合同`/`李明`）**一个都召不回**。观澜 `search.tokenize` 走 **CJK 2-gram（单字退化 1-gram）**（决策P5.0-18），恰好覆盖双字词。**故这是观澜 2-gram 选择的反面印证，不是缺口**（另：swarmvault 的 CJK 码点集含假名+谚文 `぀-ヿ가-힯`，观澜刻意只留统一表意区（决策P5.0-18「不含假名」）——领域裁剪，非漏项）。补记 [`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md) §「分词为何不优先」的旁证。
- **`missing_page_file` structural lint（`eb61020`）—— 已等价覆盖**：swarmvault 新增「页在图里、盘上无 md 文件 → `missing_page_file` finding」。观澜 [`health.py`](../../../guanlan/health.py) 早有 **`health.index_dangling`**（「index 有、磁盘无 → index 链接 X 无对应文件」）与其对偶 `health.index_missing_page`，且 `check.wikilink.broken`/`lint.broken_link` 覆盖断链面。**别重复造。**（swarmvault 的图节点可脱离文件存在，因其图是独立派生物；观澜图**从盘上文件构建**，节点必有文件，故本缺口在观澜天然不成立——它需要的是 index↔盘同步，观澜已有。）
- **provider `maxOutputTokens` 可配 + 默认 1200→4096（`eb61020`）—— N/A**：观澜把 LLM **全权委托 agentao 子进程**，wrapper 不自持 LLM client、不自截 output token（不变量：脚本零 LLM、无 API key）。故「输出上限太小 → JSON 截断 → 静默降级」这条**具体故障链在观澜不存在**。其**元教训**（截断/失败 → 静默降级 → 用户无从解释）**已并入 §2 的可观测性动作**——观澜对应面是 `EXIT_AGENT_ERROR`（子进程非零/status==error/stdout 解析失败，[`errors.py`](../../../guanlan/errors.py)）已把 agentao 失败显式化，无需再借。
- **deep-lint 读页失败改发 stderr 告警（`eb61020` 的一半）—— 观澜无对应件**：swarmvault 的 deep-lint 是 **LLM 矛盾检查**读页 `catch(()=>"")`。观澜的 `check`/`lint`/`health` 是**确定性零 LLM 脚本**，读页失败路径与它不同构（`errors="replace"` 容错、或直接冒泡）；其可借的「别静默吞读错」精神**已由 §2 在 search 侧承接**，不必在 lint 侧再复制一份。
