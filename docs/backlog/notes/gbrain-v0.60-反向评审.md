# gbrain v0.50.1–v0.60.526 反向评审（backlog）

> 状态：**反向评审结论，非排期项**。键定 **2026-10-31 pull**（fast-forward `a6be012a→23b28ebe`，**v0.50.0 → v0.60.526.0**，7865 个非合并提交 / 11355 文件 / +155 万行 −13 万行，跨 2026-09-10…10-31 共 920 个发布）。**只借形状、不借实现。**
>
> 方法：CHANGELOG 分六段逐版读完，每段对照观澜代码与 `docs/` 核实（先查「已有 / 已显式推后」再断言缺口，见上一轮方法论）。§1 三条均已在临时库实测命中，修复随本笔记同分支落地。
>
> 关联：[`gbrain-反向评审结论.md`](gbrain-反向评审结论.md)（§8 别借分档）、[`gbrain-v0.50-反向评审.md`](gbrain-v0.50-反向评审.md)（上一轮）、[`gbrain-v0.42.53-反向审计-guanlan缺陷.md`](gbrain-v0.42.53-反向审计-guanlan缺陷.md)（「拿对方修的 bug 当探针审己」的先例）、[`llm_wiki-反向评审-v0.6.13.md`](llm_wiki-反向评审-v0.6.13.md) §5（raw 遍历不完整的推后记录）。

## 0. 一句话

七周 920 个发布，约八成篇幅是托管同步 / 写入归属 / receipts / Postgres 锁与驱动 / 分段存储 / CI 分片这类 DB 基建，其余落在 E1（向量、rerank、名字变体）、E2（OAuth、远程 MCP、连接器）、E3（dream、belief ledger、takes、trust tier）——全在 §8 既有分档里。**功能面没有可借的**。本轮收获与上一轮同型：**把 gbrain 修的 bug 当探针，照出观澜三处真缺陷**（§1），外加几条零状态的小补强（§2）。

## 1. 🔴 审己探针命中（已修，本分支）

### 1.1 YAML 别名炸弹撑爆 Web 宿主（gbrain v0.60.305，S-47）

**gbrain 的事实。** 几百字节、层层嵌套 alias 的 frontmatter 能把进程内存撑爆；改为先估展开体积、超限即拒。

**观澜对照。** `pages._load_yaml_mapping` 用 SafeLoader 解析，别名只共享引用、当场不展开——所以 CLI 的 check / health / lint / graph / search 都不受影响；但 Web `render_page` 把整个 `meta` 交给 `/api/page`、`/api/raw/file`，FastAPI 序列化成 JSON 时逐层展开。实测 228 字节 → 3 MB JSON，每多一层 ×9；6 层 312 字节时 RSS 209 MB、耗时 2 秒。`raw/` 预览解析的是**第三方原文**的 frontmatter，不只 Agent 写的页才有风险。代码与 docs 均无记录。

**修法。** loader 拒绝任何别名（约定里的 frontmatter 从不需要锚点），按无法解析处理：严格档报 `frontmatter.unparsable`，容错档得 `None`。只在块里含 `*` 时多走一遍事件流，热路零开销。**一个别名也拒**，不设展开阈值——阈值式判断会留下「刚好不到线」的口子，且观澜没有合法用例需要它。

### 1.2 一张非 UTF-8 页让 `check` 与写门禁整体崩溃（gbrain v0.60.298，GBRA-130 坏项隔离）

**gbrain 的事实。** 20 个逐项循环改成一项坏了只隔离这一项、记类型码、其余照跑。

**观澜对照。** `check.py` 用严格 `read_text(encoding="utf-8")` 读页：放一张 GBK 页，`check` 抛 `UnicodeDecodeError`、exit 1，其余页一条违规都不报。同一张页 health / lint / graph 走 `load_page` 的 `errors="replace"` 能正常跑——两边口径不一致。写门禁写前、写后都调 `run_check`，ingest 会崩在门禁上。`load_page` 的 docstring 写着「编码硬错仍可由 check 暴露」——意图是暴露，但暴露成 traceback 不是报告。

**修法。** 报一条阻断违规 `page.not_utf8`（附首个坏字节偏移），文本按 `errors="replace"` 解码后照常进容错 meta 与链接扫描，保持 `broken≡check` 与 graph 同口径。门禁按「新引入的阻断违规」做差分，库里原有的坏编码页不会挡 ingest，只如实上报。

### 1.3 `test_core_has_no_platform_branch` 永远不会红（gbrain v0.59.20 删掉「改坏代码仍绿」的测试）

**gbrain 的事实。** v0.59.20 证明 42 个测试文件在代码被故意改坏后仍是绿的，随后删除；v0.60.12 修了「静默什么都匹配不到」的守卫。

**观澜对照。** `tests/test_im.py` 的 `_strip_comments_and_strings` 为了不让中文说明误报，把 STRING token 一并剥掉——`== "weixin"` 里的 `"weixin"` 随之消失，断言恒真。CLAUDE.md 说它守着「IM 核心不按平台分支」的不变量（决策P4.21-3），实际零守护。对照：`tests/test_im_feishu.py` 的镜像用例查 NAME token，不受影响。

**修法。** 改走 AST：只看 `Compare` 节点里 `==` / `!=` 两侧的平台名字面量，注释与 docstring 天然不是比较节点；另配一条「植入分支必须被抓到、说明里的同样字样不得误报」的反例用例。——又一例「漏报是沉默的，正例测不出来」。

## 2. 🟡 窄借（未做，零状态或只改 skill 文字）

| # | gbrain 来源 | 观澜落点 | 形状 |
|---|---|---|---|
| 2.1 | v0.60.411 / .511 / .525「判不了就报未知，不当成没有」（一周修三次） | `health._raw_anchor_stems`：`os.walk` 未接 `onerror`；`raw/` 下指向未挂载盘的断链被 `isfile` 静默跳过 | 收集遍历错误与断链，有一项即报一条「raw 遍历不完整」并压下 `source_without_raw`——后者的提示语建议「删除」，误报代价不小。原按「等真实库遇到」推后（llm_wiki v0.6.13 §5），新 finding（PR #133，已合并）改变了代价，**应尽快补上** |
| 2.2 | v0.60.30 / .32 冲突判定 prompt v3/v4（有 1371 对 A/B：同期冲突检出 101→149、误报 11→14/820） | `conventions.md` 「时序变化」只有一句「新数据取代了旧数据」 | 补 2–3 句：须有两个不同日期或明确变更措辞；不得凭数值大小 / 列出顺序推断先后；无日期的冲突一律算冲突；一方未提某属性不构成冲突。零代码 |
| 2.3 | v0.60.94 / .49 相对日期落地、不虚构精度 | SKILL.md ingest 写页规则无相对时间条款（agentao 已每轮注入当前日期，那半不用借） | 「上周 / 去年」按**源文自身日期**换算或保留原词并注明源文日期；只写到已知精度；计划不得写成已发生。LLM 判断，不接门禁 |
| 2.4 | v0.60.174 `keyword_total` | `SearchResult` 只回 `scored[:limit]` 与 `pages_searched` | 加一个命中总数字段，CLI / MCP / Web 共用，Agent 不再把 top-K 当全部命中 |
| 2.5 | v0.60.79 YAML 1.2 规则 | `sources: [2024-05-01, 010]` 被 YAML 1.1 读成 `[date, 8]`；`raw_slug` 会原样产出这类 slug；`check` 只报「须为字符串列表」 | 报错点名哪一项被读成了什么类型并提示加引号；模板补一个带引号示例。**先**用 `raw/2024-05-01.md` 跑一次 ingest 看 Agent 是否真写成不加引号 |

## 3. 🔵 仅记一笔

- **`raw_digest` 取门禁后重读的字节**（gbrain #8071）：`ingest` / `audit` 在门禁后重新 `compute_raw_digest`，而门禁快照已有 Agent 读到那份的 sha256。窗口秒级、修法便宜（stamp 直接用快照哈希），等有人碰这块时顺手。
- **长 raw 只读了一部分仍被 stamp**（gbrain v0.60.189 `partial`）：只有 prompt 层「截断续读」纪律；确定性检测要依赖 agentao 工具事件，成本不低。
- **`search` 行扫遇 `title: >-` 把 `>-` 当标题**（gbrain v0.60.105 同类）：`_scan_scalar` 的最大努力豁免只写给了别名；遇 `>` / `|` 开头退回 slug 即可。
- **预览与执行绑定**（gbrain `--apply --expect <hash>`，多处反复出现）：`remove --yes` 重算 plan；有 `.trash/` + manifest 兜底，等 P3.9 二期做 cascade / 批量时再借。
- **冲突条目被改写丢失**（gbrain v0.60.253/254）：可在 `_check_source_regression` 加「`⚠️ 资料冲突与待核` 条目数减少」告警（open→resolved 不算减少）；新规则有维护成本，先抽样量误报。
- **MCP `read_page` 无大小上限**（gbrain v0.60.231 10 MiB 帧）、**扫描中途文件消失**（v0.60.78，`load_page` 未接 `FileNotFoundError`）：观澜页小、并发窗口理论性，不排。
- **对外读面给第三方文本加围栏**（v0.60.507/521）：决策P4.11-1 已拒，等 MCP 用户报客户端误判再议。

## 4. 已核实观澜无同类问题

HTML 注释里的标记被当链接（v0.60.284，`link_scan_text` 先剥注释）· `[[acme.io]]` / `[[x|别名]]` / `[[x#节]]`（v0.60.249）· CJK 与拉丁字母粘连切词（v0.60.160）· 怪形状 `[[…]]` 中止流程（v0.60.106）· doctor 报早已愈合的历史问题（health 无状态重算）· `--help` / `--flag=value`（argparse）· CI 全 skip 报绿（`ci.yml` 已有零 skip 闸）· ghost 页按 `.md` 误删（v0.60.497）· 文档里的 CLI 写法漂移（47 条反引号引用逐条对 argparse 核对，全部有效）· raw 快照看 mtime（观澜全量 SHA256）· MCP `readOnlyHint` · 自环反链。

## 5. 别借（附档）

托管同步 / write-owner / receipts / 分段存储 / PGLite 统计 / 规模压测档 = DB 基建 · 向量 / HNSW / rerank / diverse pool / 名字变体检索（且英文专用）/ facts block = E1 · OAuth / 授权 / `mcp expose` / Gmail·日历·GitHub 连接器 / 托管 gbrain.io = E2 · dream / chronicle / consolidate / belief ledger / `known_as_of` / takes / trust tier / System One = E3 或新增状态 · cost meter / 模型回退链 / 默认模型 = 归 agentao。

## 6. 收口

- ✅ §1 三条已修（同分支）：`pages._load_yaml_mapping` 拒别名、`check` 报 `page.not_utf8`、IM 平台分支守卫改走 AST；每条都配了「旧代码上必红」的用例并实测确认。
- ⬜ §2.1 补到已合并的 `health.source_without_raw`（PR #133）上，误报代价最高，排第一。
- ⬜ §2.2 / §2.3 合并成一次 skill 文本改动。
- ⬜ §2.4 / §2.5 小改，§2.5 先跑一次实测。

**方法论记一笔**：三条命中有两条是「口径分叉」——同一份输入，严格档与容错档（1.2）、解析与序列化（1.1）行为不同。观澜已有「两档共用一个 loader 杜绝分叉」的纪律（`_load_yaml_mapping` docstring），但它只管住了**解析**这一步，读盘解码与下游序列化都在它的覆盖面之外。
