# nashsu/llm_wiki 反向评审 v0.6（增量·backlog）

> 状态：**反向评审结论，非排期项**。承 [`nashsu-llm_wiki-反向评审结论.md`](nashsu-llm_wiki-反向评审结论.md)（至 v0.5.4=`c03c6be`），本篇键定 **2026-07-11 pull**（`c03c6be→9b71ade`，v0.5.4→**v0.6.3**，102 文件 +22365/-4995）。**不改变现状，只借形状、不借实现。**
>
> **评审修订（2026-07-11，收敛）**：初稿把 §2 的 4 信号关联评分定为"强借 + 候选 P3.12"，经评审**降级**——权重无观澜语料 precision 支撑（**未知类型默认亲和 0.5 → 几乎任意页对得正分**，配"全库打分"即 O(N²)、大量弱建议；**"零基建"≠低成本低噪声**），改为 **park + 一个离线小实验**；"图谱洞察（惊喜连接 / 知识缺口）"与现有拓扑 health/lint 重叠，**删除落地建议**。本篇收敛为 **两项立即小动作（§2）+ 一项仅观察（§3）+ 其余不做（§4）**。
>
> **进展更新（实现后回填，2026-07-11）**：§2 **两项立即动作均已实现**（见 [`CHANGELOG.md`](../../../CHANGELOG.md) 未发布段）——§2.1 query/Web 收敛提示 = `query.QUERY_PROMPT` + `web/conversation._continuation_prompt` 各补「不要重复等价检索；证据足够即回答」；§2.2 ingest 撞名守卫 = `ingest._reject_source_slug_collision`，经 xhigh code-review 三轮收敛：① 从「按 basename 判」**收正为按 `raw_slug(stem)`（页身份归口）判、只比 `.md`**（review §1「basename 太窄」漏 `annual report`↔`annual-report` 等 slug-fold 撞页；只比 `.md` 避 convert `pdf`+`md` 误伤）；② **合法重摄豁免**（review §2）——目标页 `raw_digest` 确证归属本文件时放行（`_target_page_owned_by`），真撞改在摄入非属主旁支时当场拒，消假阳；③ 全树 `rglob` 为**已接受代价**（review §3，`gate.snapshot_raw` 本就同量级遍历 `raw/`）。§3 仅观察、§4 不做 **均未动**。
>
> 关联：[`nashsu-llm_wiki-反向评审结论.md`](nashsu-llm_wiki-反向评审结论.md)、[`../../P2.1-摄入写入纪律.md`](../../P2.1-摄入写入纪律.md)（决策P2.1-4：绝不让代码改写正文）、[`../../P5.0-检索层.md`](../../P5.0-检索层.md)（E1 边界 + 实证②）、[`../../P3.11-断链最近页建议.md`](../../P3.11-断链最近页建议.md)（"断链找近似页"，与 §1-A **不同轴**）、[`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md)、[`gbrain-反向评审结论.md`](gbrain-反向评审结论.md)。

## 0. 一句话

本次 pull 主线：Rust 后端 chat agent 重写 + 智能/混合检索（keyword+vector+graph 融合）+ 4 信号图关联 + agent 循环收敛护栏 + 源生命周期硬化 + 页面历史/守卫撤销。过观澜红线后，**真正的新缺口只有三个**（§1），对应 **两项立即小动作 + 一项仅观察**；4 信号加权、图谱洞察、向量/RRF 全部**不排期**（§4）。

## 1. 新增缺口（真实的）

- **A. 未互链的相关页面对**：观澜 `lint` 只为**断链**找近似页（P3.11），对**两张都已存在、共享同一批 `sources:` 却互不 `[[链接]]`** 的页零建议。这是本次唯一站得住的新缺口——但 llm_wiki 那套 4 信号加权（源重叠×4 / 直接链接×3 / Adamic-Adar×1.5 / 类型亲和×1，未知默认 0.5）**证据不足、噪声不明**，观澜**只认最硬的一个信号：共享 source**。→ §3 仅观察。
- **B. query 循环无收敛纪律**：CLI `QUERY_PROMPT` 与 Web chat 提示都没说"别重复等价检索 / 证据足够即答"，可能空转、重复召回。→ §2.1。
- **C. source basename 撞名误关联风险**：`rawio.find_source_page` 只按 `Path(rel).stem`（**纯 basename**）定位摘要页，而 `provenance.raw_digest` 用**完整相对路径**——`raw/a/report.pdf` 与 `raw/b/report.pdf` 存在**误关联到同一 `wiki/sources/report.md` 的风险**。**是否真覆盖取决于 Agent 实际写页行为，不能仅由 wrapper 代码断定**（故只堵、不重构）。→ §2.2。

## 2. 最小动作（立即做，两项）

1. **query / Web 各加一句收敛提示**：在 **CLI `QUERY_PROMPT`** 与 **Web chat 提示** 各补一句"**不要重复等价检索；证据足够即回答。**"。**不写进 `AGENTAO.md`**（会波及 ingest 等所有工作流，太宽）、**不调查 agentao 预算参数**、不升级成全局宪法。零成本、两处各一行。
2. **ingest 前拒绝 raw 子目录重复 basename**：摄入前检查 `raw/` 下是否已有同 basename 的其他文件；有则**明确拒绝并要求改名**。**不引**新 slug 算法 / 哈希后缀 / collision 迁移 / 约定重写——只堵 §1-C 那个最小故障面。

## 3. 仅观察（不排期）

**共享 source 的未互链页对——离线小实验**：用**倒排表**枚举"共享 `sources:` 的页面对"，输出 **top-N 做人工抽样**看 precision。**暂不**接 `lint`/MCP/reader，**不引** Adamic-Adar / 类型矩阵 / 社区信号。先证伪"共享 source 这一个信号是否够强"——**有明确 precision 后**，再决定是否评分、是否产品化（届时才谈 P3.12）。

## 4. 不做什么（park / 别借，附理由）

- **4 信号加权（类型亲和 / Adamic-Adar / 全库打分）**：park，待 §3 precision。未知类型默认 0.5 会让几乎任意页对得正分，O(N²) 噪声大——照抄权重前须先有观澜语料样本。
- **图谱洞察（惊喜连接 / 知识缺口）**：其"孤立 / 稀疏社区 / 桥节点"与现有拓扑 health/lint（`graphstats` P3.5/P3.6）**大量重叠**，非新缺口，**删落地建议**。
- **混合向量 / RRF / 图一跳融合**：= **E1**，别现动。可抄常数已录备 E1 选型：RRF `k=60`、图混入占最终窗 15–30%、分块 1000/overlap 200、维度动态存 LanceDB、距离转分 `1/(1+d)`。
- **enrich-wikilinks 应用半**（LLM 出 `{term,target}` JSON → **代码把 `[[]]` 落进正文**）：撞 决策P2.1-4，别借（前篇 §6 同结论）；其确定性半还只护 frontmatter + 已有 `[[]]`、代码块/标题避让全靠 LLM 不提。
- **持久文件历史 + 守卫式撤销**：别借——**当前产品范围不提供应用内版本历史，依赖用户自行使用 git；收益不足以引入持久状态**（`.llm-wiki/*.json` 持久派生物与"markdown 唯一真相"张力）。**订正**：`gate` 快照 / `.trash/` / git 与通用页面历史·undo **并不等价**，git 也非强制前提——这里是"**范围外**"，不是"已覆盖"。
- **源自动监视 → 移动迁移**：观澜是批处理 CLI、**无常驻 daemon**，N/A；其删除决策（sole source → 删页）已由 `remove`（P3.9）等价覆盖。
- **Louvain 社区 / 源指纹漂移检测**：观澜已有——`graphstats.detect_communities`（手写确定性 Louvain，且另做 Tarjan 桥/割点健康度；llm_wiki 本次才补社区、且只用于检索加权）/ `provenance.raw_digest` + `audit`（P3.7，早于本次 pull）。
- **in-page UI / 页面链接面板 / Mermaid / 生成物预览 / Rust runtime 重写 / linux webkit**：桌面 UI 与架构，观澜是 CLI + 只读 Web + agentao 子进程，面不重叠。
