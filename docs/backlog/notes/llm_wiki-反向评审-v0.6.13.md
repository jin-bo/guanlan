# nashsu/llm_wiki 反向评审 v0.6.13（增量·backlog）

> **状态：§2.1 已实现（`health.source_without_raw`，见 CHANGELOG `[Unreleased]`）；§2.2 / §2.3 仍在 backlog，可选。**
> 接上一篇 [`llm_wiki-反向评审-v0.6.11.md`](llm_wiki-反向评审-v0.6.11.md)（停在 `e808211` / v0.6.11），
> 本篇覆盖 2026-10-31 pull 的 `e808211..e08cfce`（v0.6.11 → v0.6.13）。
>
> 方法同前：**只借形状、不借实现**，按观澜教义过滤（零-LLM vs LLM 分档、`raw/` 不可变、markdown 唯一真相、
> 薄壳不携带业务智能、低噪 advisory、中文优先）。分档：强借 / 战略借 / 有张力 / 已在路上 / 已领先 / 别借。
>
> 关联：[`nashsu-llm_wiki-反向评审结论.md`](nashsu-llm_wiki-反向评审结论.md)、[`../../P3-健康与图谱.md`](../../P3-健康与图谱.md)、
> [`../../P3.7-语义审计.md`](../../P3.7-语义审计.md)、[`../../finding-因果排序.md`](../../finding-因果排序.md)。

## 0. 本次增量主线

| 项 | 数 |
|---|---|
| 提交 | 51 |
| 改动文件 | 128 |
| 增 / 删行 | 8142 / 829 |

上游三条主线：

1. **摄入产物的边界**（`b03508f` 一次摄入只认一张 source 页、`773587d` 实体页先立身份、`8f2cf9e` 合并不漏脚手架、
   `cc7573d` 规整写坏的 wikilink）。
2. **摄入的韧性**（`6693da1` / `e380374` 截断检测与输出上界、`cbd1717` 中断续跑、`527bdc2` index 入 prompt 截头去尾）。
3. **外围**：MinerU 4 V1 API（`7fc668b`）、经 MCP 写页并逐字节回读校验（`4ffe414`）、overview「最近更新」确定性维护（`f649673`）、
   队列与预览面板的 UI 细节。

## 1. 新增缺口（真实的）

### A. source 页没有原文也没人报（对应 `b03508f`）—— **强借**

上游 issue：摄入一篇资料时，模型顺手给资料里**引用的其他文献**也各吐了一个 `wiki/sources/` 块，多出的块有时还覆盖了
真正的摘要页。上游修法：一次摄入只认一张 source 页（`sourceSummaryBlockWarning`），多出的丢弃并警告；prompt 里补一句
「不要为被引文献生成 source 页」。

观澜的 `sources.unresolved` 只查「`wiki/sources/<slug>.md` 在不在」，**不查这张页本身有没有原文**。最小库实测：
在 `wiki/sources/` 手放一张 `cited-paper.md`，`raw/` 下无对应：

```
check  → ✓ 通过（rc=0）
health → 只报 stub_page（因正文短）
lint   → 只报 orphan
audit --dry-run → ✓ 无漂移源
```

于是来源链可以断在 wiki 层：别页写 `sources: [cited-paper]` 照样过门禁，而 `cited-paper` 背后没有任何 raw。
`audit` 也追不到它——没 `raw_digest`、文件名又对不上，source-drift 永远判不出来。

**真实库计数（实现后）**：库甲（约 1500 张 source 页）、库乙（约 70）、库丙（约 290）都是 0；库丁（约 70）报 16 条，
逐条核过**全是真的来源链断裂、无一误报**，分三类：

| 成因 | 条数 | 例 |
|---|---|---|
| 原文改过名，页内既无 `raw_digest` 也无「原始资料」声明 | 4 | 页 `示例目录2025版` ↔ raw `示例目录（2025年版）(1).md` |
| 多篇聚合页放进了 `sources/` | 12 | 6 张在 `sources:` 里直接列 5–14 篇原文的文件名（这些条目 `check` 已报 `sources.unresolved`）；另 6 张不列来源、正文自述各汇集数百篇 |
| 被引文献误建（上游那一类） | 0 | — |

上游那一类在观澜库里尚未出现，但检测面一并覆盖了另外两类此前沉默的问题——都让 `audit` 失明。

### B. 实体页的「身份先行」与禁止猜译名（对应 `773587d`）—— **可选小借**

上游给实体分析与生成各加了一组规则，两条对观澜有用：

- **首段回答「它是什么」**，不是「它在这篇资料里干了什么」。观澜 conventions 的实体模板只写了「一段定义/概述」，
  没有把这条写成禁止项。从单篇资料建的实体页若以「它在本文中的作用」开头，换一篇资料来看就不成立（未在真实库上计数）。
- **不许音译或猜译名**：只用资料原拼写或已有页名，通行译名放 `aliases`。对中文库是真问题——同一外文人名被译成两三种写法、
  各建一页，`aliases` 撞名检查只能在**已声明**别名时兜住。

上游另一半（「值不值得独立成页」的门槛）观澜的决定树已有（`SKILL.md` 摄入步骤 3），不借。
上游允许「标注为可独立核实的常识背景」写一句身份句——**不借**：观澜的 source 页约定是只陈述资料所载，外部常识入页
会让 `audit` 的「论断是否仍被现源支持」失去判据。

## 2. 最小动作

### 2.1 `health.source_without_raw`（**已实现**，修 §1.A）

**判定**：`wiki/sources/` 下的页，以下三路都未指向现存 raw 文件即报（advisory，默认退 0、`--strict` 退 6）：

1. **文件名**：全体 raw 文件经 `rawio.source_page_stems`（精确 slug → 点折横杠的 kebab 形 → 逐字原名）推出的 stem 集合。
   `source_page_stems` 是从 `find_source_page` 抽出的**正反两向单一归口**——两边各写一套容错，就会出现「ingest 认它已收录、
   health 却说它没原文」的漂移；`test_never_reports_a_page_find_source_page_would_claim` 钉住这条一致性。
2. **`raw_digest`** 里的路径。
3. 围栏外的 **`> 原始资料：\`raw/…\``** 声明行（与 `sourcepage.locate_source_page` 同一组证据，抽成 `claimed_raw_paths`）。

后两路只做**字面准入**（相对路径、以 `raw/` 起、归一后不越出 `raw/`；绝对路径、反斜杠、`..` 一律当没声明），再要求文件存在，
解析时**跟随符号链接**。不用 `provenance.admit_raw_path` 的 realpath 准入：那是 `audit` 读内容喂 Agent 时防穿越用的，health
只判存在、不读内容；沿用它会让 `raw/` 下指向外部存储的同一份原文经文件名算有、经声明算无。与文件名一路同口径，路径任一段
以 `.` 开头不算原文。逐字找不到时，按目录里实际文件名的 NFC 形逐段再比一次（Linux 不做规范化，NFD 落盘的原文与 NFC 写的
声明逐字对不上）。

**取舍**：

- **文件名一路只认 `sources/` 第一层**：`find_source_page` 只找 `sources/<stem>.md`，子目录同名页 ingest 判「未收录」，
  这里按 stem 认就成了反向漏报。子目录页仍可经声明 / `raw_digest` 认领。四个真实库均无子目录页，此规则不新增报告。
- **符号链接目录跟进**：遍历 raw 用 `os.walk(followlinks=True)` 并按真实路径防环，与 `gate.snapshot_raw` 同口径。
- **只扫 `sources/` 目录，不看 `type`**：`type: source` 放错目录归 `type_dir_mismatch`，不重复报。
- **不按扩展名过滤 raw**：`raw/` 里放 PDF 原件、`convert` 产物 `.md` 都合法。多认一个 stem 只会少报。
- **跳过任一段以 `.` 开头的 raw 路径**：`raw/.x.md` 经 `raw_slug` 剥首点会变成 `x`，若算原文就会吞掉真孤儿 `sources/x.md`
  ——这是本条的漏报向用例。
- **NFC 两边一起归一**，且原名与其 NFC 形各推一遍：`raw_slug` 把 NFD 的组合记号（U+0301）当非词字符折成 `-`，
  Finder 落盘的 NFD 文件名推出的 slug 与 NFC 页名对不上。
- **不折大小写**（能力上限）：Linux 上 `Report.md` 与 `raw/report.md` 本就互不认领；macOS 大小写不敏感 FS 上 ingest 会认而
  这里会报，手动改名或补声明即可。
- **不做模糊匹配**：库丁那 4 张改名页（`（2025年版）(1)` vs `2025版`）靠模糊可以认出来，但模糊匹配的误认是沉默的，
  而补一行声明的成本很低。
- **因果排序**放第一档、紧随 `index_dangling`：来源链断裂是数据完整性问题，先于桩页之类的内容建议。

**处置指引**写在 finding 的 detail 里，按三类成因各给一句：补声明 / 移 `syntheses/` / 并入后删除。不自动修——
三种处置都是内容判断。

### 2.2 摄入期门禁警告（未做，backlog）

§2.1 是事后体检。上游的形状是**写时**拦：一次摄入只认一张 source 页。观澜对应落点是写门禁——`gate.snapshot_page_meta`
已有前后快照，ingest 后 `wiki/sources/` 下除本篇 slug（及 `locate_source_page` 认定的那张）外**新增**的页，给一条警告、
不阻断（与决策8「断链只警告」同档）。

暂不做的理由：真实库里「被引文献误建」计数为 0，`health` 已能事后发现；先观察 §2.1 上线后这一类是否真的出现。
若做，注意 `ingest` 批量模式（一次多篇）下「本篇 slug」是一个集合。

### 2.3 `conventions.md` 实体页两条措辞（未做，可选，修 §1.B）

纯 skill 侧 prompt，零代码：实体模板的「定义/概述」段补「首句回答它是什么，不写它在某篇资料里的作用」；命名小节补
「不音译、不猜译名，用资料原拼写；通行译名进 `aliases`」。改 `SKILL.md` / conventions 要新开会话才生效（agentao 0.5.18 起技能正文入史一次）。

## 3. 仅观察（不排期）

- **经 MCP 写页 + 写后逐字节回读、不符即回滚**（`4ffe414`）。观澜 MCP 刻意只读，**别借**写入口；但「写后回读比对、
  不符回滚到原字节」的形状观澜已有（`provenance.stamp_raw_digest` 的写后 check、`gate` 回滚），无新增。
- **合并时删掉模型编出来的 `example.com` 网址**（`8f2cf9e`）。观澜没有「合并」这一步，页由 Agent 直接写；
  编造链接是一般性幻觉，不为单一域名立规则。
- **截断检测 / 输出上界**（`6693da1` / `e380374`）→ **已有**：P4.23 起 `Conversation.turn()` 靠 `agent.last_turn` 区分
  未完成的一轮，门禁对 `agent_error` 不盖 `raw_digest`。
- **中断续跑**（`cbd1717`）→ **架构不同**：观澜一次一篇、门禁 fail-closed，未过门禁不 stamp，重投一次即可；
  续跑需要逐页完成清单，等于引入新的持久状态。

## 4. 不做什么（已免疫 / 已领先，附验证）

- **index 入 prompt 截头去尾**（`527bdc2`，32K 字、头 60% 尾 40%）→ **已领先**。观澜 ingest 给的是〈页面名录〉，超预算时
  wrapper 明说并要求按 `offset` 读完 `index.md`（`SKILL.md:75-78`）——不会静默丢中段，上游这刀恰好砍掉查重最需要的中段。
- **路径式 `[[concepts/foo]]` 解析**（`1fac908`）→ **已免疫**：`pages.link_stem` 取末段（`pages.py:568`）。
- **source 页文件名按 UTF-8 字节截断**（`065ee13`）→ **基本免疫**：source 页名由 raw 文件名推出，换 `.md` 后缀一般不变长；
  唯一例外是无扩展名且名字本身已逼近 255 字节的 raw，不处理。
- **MinerU 4 V1 API**（`7fc668b`）→ **已支持**：`pdf-to-markdown` 已走 MinerU 4 的 `mineru parse`（`--tier`），3.x 的
  `mineru -p` 保留；本机 4.0.12。
- **规整写坏的 wikilink**（`cc7573d`：`[[1], [2]]` → `[1], [2]`、`[[X|Y]` 补括号）→ **别借**：薄壳不改写 Agent 产物；
  写坏的形状由 `check` 报断链（警告），由 Agent 下次修订时修。
- **overview「最近更新」确定性维护**（`f649673`）→ **别借**：观澜 overview 是 Agent 维护的活体综述、属配置页；
  「最近更新」由 `log.md` 承担，往 overview 混入程序写的块会与 `health.overview_large` 的体积口径打架。
- **graph 收 `related` frontmatter 边**（`b726bd1`）→ **无此面**：观澜 frontmatter 无 `related`，关系只由 `[[wikilink]]` 表达。
- **队列移除 / 队列续跑 / 预览前进后退 / 按模型能力调请求**（`99eb3f5` / `4d1bae0` / `84b6d3a` / `6aea0b2`）→ 无此面或已归 agentao。

## 5. 未决项登记

1. **§2.2 摄入期警告**：等 §2.1 在真实库上观察一段时间，「被引文献误建」这一类出现过再排。
2. **库丁的 16 条**：12 张聚合页移 `syntheses/` 还是拆回逐篇 source 页，是该库的内容决定；4 张改名页补声明即可。
   不由本改动代做。
3. **`find_source_page` 自身不做 NFC**：NFD 原名在 ingest 侧推出 `Cafe-…` 形 slug，Agent 照此建页，两边仍自洽，
   故未改；若日后发现 NFC 页名的已收录源被 Web 标成「未收录」，再在 `source_page_stems` 入口统一归一。
4. **raw 遍历不完整时静默**：`raw/` 下读不了的子目录（权限、链接指向未挂载的盘）被 `os.walk` 跳过，其中原文对应的 source 页
   全部误报为无原文，且不提示遍历没走完。要处理应另立一类「raw 遍历不完整」的 finding，而不是猜作有原文；新增 finding 类型
   有维护成本，等真实库遇到再排。
