# astro-han/karpathy-llm-wiki 反向评审（backlog）

> **状态：backlog，未排期。§1.A 是观澜确定性层最大的一块空白，但它有一条必须先回答的前置张力（§1.A.4），
> 回答之前不排期。§1.B / §1.D 低成本、随时可做；§1.C 有门禁耦合，须设计。**
>
> 覆盖 2026-09-11 pull 的 `9e8c4f4..eafcc77`（14 提交）。方法同前：**只借形状、不借实现**，
> 按观澜教义过滤（零-LLM vs LLM 分档、`raw/` 不可变、markdown 唯一真相、薄壳不携带业务智能、
> 低噪 advisory、中文优先）。
>
> 关联：[`llm_wiki-反向评审-v0.6.11.md`](llm_wiki-反向评审-v0.6.11.md)（§2.2 结构化制品逐字保留是本篇 §1.A 的**前置增效项**）、
> [`../../P3.7-语义审计.md`](../../P3.7-语义审计.md)（`raw_digest` 与 source-drift 的边界）、
> [`../../P3-健康与图谱.md`](../../P3-健康与图谱.md)（advisory 分档）、[`openkb-反向评审结论.md`](openkb-反向评审结论.md)。

## 0. 为什么这个兄弟项目比 llm_wiki 更可比

`llm_wiki` 是 Tauri 桌面应用，借鉴时得先把它的 UI/状态层剥掉。**karpathy-llm-wiki 与观澜的 skill 层同构**：
`SKILL.md` + `references/*-template.md` + `scripts/*.py` + `tests/`，且明确分「LLM 判断」与「确定性脚本」两档——
和观澜「判断在 skill、确定性在 `guanlan/` 包」是同一条分界线。**故它的设计决策可以近乎一比一地映射。**

| 项 | 数 |
|---|---|
| 提交 | 14 |
| 增 / 删行 | 1327 / 138 |
| 新增 `scripts/check_evidence.py` | 431 行 |
| 新增 `tests/test_check_evidence.py` | 765 行 |

本轮主线是**立一条接地不变量并给它配机械验证**（`959bbb3`），随后连着五次「收窄」提交
（`3556cf4` / `9340c1f` / `e3b7c68` / `36b1eb7` / `ad9a5a7`）才把误报/漏报两面都压住。
配套的是摄入处置分流（`01898a6`）、假性缺失闸（`dcc4ec4`）、固定格式 Status 块（`aa4d359`）。

## 1. 新增缺口（真实的）

### A. 观澜**没有任何**确定性检查在问「这页写的数字，raw 里有过吗」（对应 `959bbb3` 全家）

上游立的不变量一句话：**文章里每个承重事实（数字、ISO 日期、直接引语）必须逐字出现在它 Raw 字段所链的
raw 文件正文里**。编译期负责「先定位再落笔」，lint 期由 `scripts/check_evidence.py`（零 LLM、**只报不改**）
抽候选字面量 grep 验证。

**观澜的确定性层逐条数下来，二十个 finding kind 没有一个碰 raw 正文**：

| 归口 | kind | 管什么 |
|---|---|---|
| `check`（7） | `frontmatter.missing_key` / `bad_type` / `wikilink.broken` / `sources.unresolved` / `wiki.missing` / `aliases.duplicate` / `aliases.collides_stem` | **形式**：frontmatter 合法性、链接可解析、slug 有页 |
| `lint`（8） | `orphan` / `broken_link` / `missing_entity` / `hub_node` / `thin_intercommunity_link` / `isolated_community` / `bridge_edge` / `cut_vertex` | **图结构** |
| `health`（5） | `stub_page` / `index_missing_page` / `index_dangling` / `type_dir_mismatch` / `uncharted_page` | **记账自洽** |
| `audit`（2 reason） | `source-drift` / `cites-drifted-source` | **raw 变了没变**（`raw_digest` 指纹比对） |

`audit` 最接近，但它比的是**指纹**：raw 字节变没变。**它不看页里写了什么。** 一页凭空多出一个百分比、
一个日期、一句并不存在的引语——只要 raw 没动过，`audit` 永远是绿的。今天挡这类捏造的只有 ingest 那一次 LLM。

#### A.1 接线现成，不需要新数据模型

上游要在正文里手写 `> Raw: [...](...)` 字段，还得费劲防「正文里同形的行被当成字段」（`raw_links_of` 的注释）。
**观澜早就有这条边且是结构化的**：每张内容页 frontmatter 的 `sources: ['<slug>']` → `raw/<slug>.md`，
解析归口是 `rawio.raw_slug` + `rawio.find_source_page`（后者还处理了 `.`↔`-` 的既有歧义，`rawio.py:87`）。
**必须复用它，不能另写第五套按名查找**——同 P4.22 对 `/page` 的纪律。

#### A.2 四条上游踩出来的细节，值得逐字借

1. **封闭候选集，冻结，写在 docstring 里**（`9340c1f`）。只收：15 字符以上引语（双引号跨度 + 正文 blockquote）、
   ISO 日期（`YYYY-MM-DD` / `YYYY-MM`）、有特征的数字（千分位 `10,000` / 带点 `2.1.80` / 带后缀 `42K` `99.9%` /
   四位以上 `2026`）。**裸小整数（`42`、`500`）与异形（符号、货币、中文数字）明确不收。**
   纪律原话：「新的行文形态改 docstring，不改正则」。拿召回率换精确率，且**边界是写下来的、不是隐含的**。
2. **比对前必须剥掉 raw 自己的元数据头**（`source_content`）。否则 raw 里那行 `Collected: 2026-03-19`
   会让文章里任何同一天的日期**假性通过**。观澜的对应物是 raw frontmatter（`rawio.apply_origin` 注入的
   `origin` 键，`rawio.py:165`）——`pages.split_frontmatter` 现成能剥，**不剥就是给自己发免死金牌**。
3. **边界规则两面都踩过**。`e3b7c68` 记的是：先做严了，raw 里的句末标点挡住匹配，报告被**逐字属实**的事实淹没
   （原话 "would have flooded reports with verbatim facts"）；`contains()` 现在的规则是「只拒绝构成**另一个值**的续接」，
   并单独挡「七字符年月不得当完整 ISO 日期的前缀」。**这正是「过滤规则要配漏报用例」的同一课，只是两面都要配。**
4. **不做增量状态**（`19b342b`）。上游主动**删掉**了 lint 的增量状态，理由：脚本几秒就能全库重跑，
   增量反而带来「改动集重建」与「未决 suspect 凭空消失」两个洞。→ 这是给 backlog
   [`finding-持久抑制-未排期.md`](finding-持久抑制-未排期.md) 的**反向证据**：先别急着上持久化。

#### A.3 报告即接口，退出码不携带信息

上游 docstring 原话：「The exit code carries no information; the report is the interface.」
观澜相反：advisory 默认退 0、`--strict` 有 findings 退 6（决策P3-4）。**这条不用改**——观澜的 `--strict`
是给 CI 用的，是真需求。记下来只是提醒：接地检查的 finding **天然含人工判断项**（见 A.4），
若进 `--strict` 就会让 CI 对「合理的转述」红脸。**它应当只进 advisory，且大概率该有自己的 `--strict` 豁免。**

#### A.4 **前置张力（排期前必须回答）：观澜的 wiki 是摘要层，不是文章层**

上游的 wiki 是「文章」，配了编译期的 locate-before-write 规则，所以逐字匹配是**合理期望**。
观澜的 `conventions.md:60` 写死了「**编译非搬运**」「wiki 是摘要层」，SKILL 摄入步骤要的是**摘要页**——
**转述是设计意图，不是缺陷。** 上游自己的 docstring 也承认：「Derived values, product names, and deliberate
paraphrases will show up as suspects; judging them is the reader's job」。

于是同一个脚本在观澜库上的信噪比**可能完全不同**。三条出路，须择一：

- **(a) 只收「不该转述」的那一档**：引语、ISO 日期、带单位/千分位的数字——即上游封闭集本身。
  赌的是「摘要层也不该改写这几类」。**配合
  [`llm_wiki-反向评审-v0.6.11.md`](llm_wiki-反向评审-v0.6.11.md) §2.2（结构化制品逐字保留，仍在 backlog）
  会显著提精确率**——那条规则一旦落地，条文/表格/签名本来就是逐字的，接地检查在它们上面几乎零误报。
  **两条应当捆绑排期，§2.2 先行。**
- **(b) 只查 `type: source` 摘要页，不查 entity/concept/synthesis**。摘要页与单一 raw 一一对应，
  跨源综合页天然要改写。**范围小一个量级，误报也小一个量级。**
- **(c) 先量一次再定**：拿真实库（法律语料最合适——条文本就不许改写）跑一版原型，**数误报率**。
  低于某个阈值才排期。

**倾向 (c) → (b) → 逐步放开到 (a)。** 在拿到真实误报率之前，这条不排期。

### B. 反向盘点缺失：raw 有文件却无摘要页 / 摘要页的 raw 已消失（对应 `959bbb3` sweep 3）

上游第三趟扫描列「没有任何文章的 Raw 字段引用的 raw 文件」。观澜**两个方向都缺**：

- **raw → 摘要页：无人查。** `raw/x.md` 摄入失败或被忘了，盘上躺着没人提醒。
  `rawio.find_source_page` 这个原语**早就有**（`rawio.py:87`），缺的只是一次扫 `raw/` 的 advisory。
- **摘要页 → raw：也无人查。** `check._check_sources`（`check.py:120`）只校验
  `wiki/sources/<slug>.md` **存在**，**不校验 `raw/<slug>.md` 存在**。而 `audit` 遇到 raw 已不存在时
  明确「当无信号跳过」，注释写的理由是「**不抢 check 缺源口径**」（`audit.py:169`）——
  **但 check 的「缺源」指的是缺 wiki 摘要页，不是缺 raw。** 两个模块各自假设对方管，结果没人管：
  手工删掉一个 raw，全套确定性检查一片绿。

这是**纯记账缺口**，与 §1.A 的语义判断无关，**成本低、零张力**，可独立于 A 排期。

### C. 摄入没有「无实质内容」的停止路径（对应 `01898a6`）

上游把「无条件抓取 + 编译」换成**先搜库、再声明处置**：New / Update / Disputed / No material。
最后一种**留在 raw/、记进日志**，于是薄资料不再被硬塞成文章，**且盘点扫描能分清「已处置的存货」与
「真没人引用的 raw」**（`no_material_paths` 读 log 的 ingest 条目标题，`19b342b` 还把它收窄成匹配**全路径**，
免得同名文件在别处被误豁免）。

观澜 `SKILL.md` 摄入步骤 2 是**无条件**的：「在 `wiki/sources/<slug>.md` 写摘要页」。没有停止路径。

**耦合点（必须先设计）**：观澜的 `ingest` 门禁后会由 wrapper 盖 `raw_digest`
（`ingest.py:163` → `_stamp_source_digest`），而 `find_source_page` 找不到页就**无声跳过**。
于是「无实质内容」路径会落成：无摘要页 → 无指纹 → 下次 `ingest` 完全重来。
**处置必须记在 durable 的地方**，`log.md` 是天然落点（观澜已有），且正好喂 §1.B 的盘点扫描做豁免。
**B 与 C 天然配对，一起做比分开做便宜。**

### D. 没有「假性缺失」闸（对应 `dcc4ec4`）

上游：**只有在索引与同义词感知的全文检索双双落空之后，才可以声称 wiki 缺少相关内容。**

观澜 `SKILL.md` query 步骤 1 有完整的降级梯子（`guanlan_search` 工具 → CLI → 扫 index/目录/aliases →
请用户补关键词），步骤 2 也有「无可靠来源时明说，不编造」。**但没有一条规则管「什么时候才算可以说没有」**——
「search 空手」与「库里确实没有」之间那一步，今天是 LLM 自己迈的。

观澜的 aliases（P3.1）就是它的同义词层，且已进检索匹配面（P5.0）。**一句 prompt 规则，零代码。**

## 2. 最小动作

### 2.1 反向盘点 + raw 存在性（修 §1.B，**低成本、零张力，建议先做**）

两条 advisory，落 `health`（它本就是「记账是否自洽」的归口）：

- `health.raw_unreferenced`：`raw/**.md` 有文件但 `find_source_page` 找不到摘要页。
- `health.source_raw_missing`：`wiki/sources/<slug>.md` 存在但 `raw/<slug>.md` 不在。
  顺带把 `audit.py:169` 那条「不抢 check 缺源口径」的注释改对——口径现在真的有人管了。

图片与非 `.md` 落盘物（`raw/images/`）**不进盘点**，它们本就由摘要页间接引用。

### 2.2 「假性缺失」一句话规则（修 §1.D，纯 skill、零代码）

`SKILL.md` query 步骤 2 加一条：**声称「库里没有」之前，search 与 index/aliases 两路都必须落空过**；
只有一路落空时，说法应当是「这一路没找到」而不是「库里没有」。与既有「无可靠来源时明说，不编造」并列。

### 2.3 接地检查：先做**度量原型**，不是先做功能（修 §1.A）

**不要直接实现。** 按 §1.A.4 的 (c)：写一个**一次性**脚本（不进 `guanlan/` 包、不进 CLI），
按上游封闭候选集抽字面量，只跑 `type: source` 摘要页，在**真实库**上数误报率。
法律语料是最好的靶场——条文本就不许改写，那里的误报率接近该方案的下限。

度量结果决定三件事：进不进包、范围取 (a)/(b)、要不要先补
[`llm_wiki-反向评审-v0.6.11.md`](llm_wiki-反向评审-v0.6.11.md) §2.2。**在有数之前不写设计文档。**

## 3. 仅观察（不排期）

- **固定格式 Status 块的机械消费（`aa4d359` → `19b342b` 又撤回）**。上游先把「过时/有争议」从散文改成固定格式
  Status 块「so readers **and lint** can recognize them mechanically」，随后又把 Status 检查**挪回判断类报告**，
  理由原话：「nothing consumes them mechanically yet, so a mechanical sweep would guard a hypothetical」。
  观澜的 `## ⚠️ 矛盾与存疑` **已经是固定格式**（conventions.md:149，含 status/类型标签），
  而 `DESIGN.md:170` 明确写了「P3 的确定性 health/lint **不统计矛盾**」。**上游这次往返恰好证明了这个决定是对的**——
  记下来是为了日后有人提「给矛盾加机械扫描」时，手里有现成的反例。
- **全库级联搜索（`aa4d359`）**。上游把级联更新从「只扫同主题目录 + index 条目」改成 grep 全库的实体/别名，
  理由是过时论断就藏在那里。观澜的 `audit` 已经沿 `sources:` slug 图传播到引用页（P3.7 Layer-1），
  **是结构化的、比 grep 强**。但观澜传播只沿 `sources`，不沿 `[[wikilink]]`/aliases——
  「引用了某实体但不引用其源」的页不在传播面内。**是否该扩到图传播，是 P3.7 之后的事，先记着。**
- **必填 `Updated` 字段与 index 日期的机械交叉校验（`ad9a5a7`）**。观澜有 `last_updated`（必备键，`check` 校验类型），
  但 `index.md` 不记日期，**没有可交叉的第二处**，故此条今天无对应面。

## 4. 不做什么（已领先 / 不适用，附验证）

- **围栏感知的文档结构（`2e88ef6` "centralize fence-aware document structure"）→ 已领先。**
  上游本轮也在修同一类问题，但作用面是**单个脚本内部**的集中化。观澜 2026-09-12 合并的
  `pages.link_scan_text` 是**跨模块单一归口**（`check`/`graph`/`im/reply` 共用），且是**对着真实渲染器
  逐形状实测**出来的（`tests/test_web.py` 的对齐表 + 「已知分歧只许在多报方向」）。
  上游的闭栏规则是 `len >= length`（CommonMark），观澜实测 python-markdown 不认更长闭栏、故取**逐字对称**——
  **两边选择不同，观澜的有实测支撑。**
- **正文 blockquote 里的元数据字段格式 → 不适用，观澜更好。**
  上游的 `> Raw:` / `> Updated:` 放在 H1 后的引用块里，所以 `parse_document` 必须费力区分
  「H1 后紧邻的那一段引用块」与正文里同形的行，还要防围栏把后面的引用块顶上来。
  观澜用 YAML frontmatter，**结构化、位置唯一、`split_frontmatter` 强制闭合 `---`**，这一整类坑不存在。
- **英文引语正则与 15 字符阈值 → 必须重调，不能照抄。**
  上游只认 `"…"` 与 `“…”`。中文要认 `「…」`『…』，且**15 个汉字已经是一个长句**——
  按字符数一刀切会让中文侧几乎抽不到引语候选。阈值须按 CJK 重定（可能按「是否含标点」而非纯长度）。
- **`SKIP_FILES = {index.md, log.md}` → 观澜已有更严的等价物。** 观澜的 config 页豁免是
  `SCHEMA.md`/`AGENTAO.md`/`index.md`/`log.md`/`overview.md`，且由 `pages.iter_pages` 统一归口，
  不是每个脚本自带一份集合。
- **`raw/` 不可变作为不变量的地基 → 已领先。** 上游说「Raw links must resolve inside raw/；
  the invariant's permanence rests on raw immutability」，但它的 `raw/` 不可变**只是约定**。
  观澜是**确定性门禁**：每次写入口前后按内容 SHA256 快照全 `raw/`（`gate.py`），
  shell `mv`/`rm` 也逃不掉。**同一条不变量，观澜的地基硬一个量级。**

## 5. 未决项登记

1. **§1.A.4 的三选一**：在真实库误报率量出来之前，接地检查不排期、不写设计文档。靶场选法律语料。
2. **§1.A 与 llm_wiki 篇 §2.2（结构化制品逐字保留）的捆绑关系**：后者先行会显著抬高前者的精确率，
   但后者自己也还没排期。两条的先后须一起定。
3. **§1.C 的处置落点**：「无实质内容」记在 `log.md` 的具体格式，以及它与 `raw_digest`
   「无页即无指纹、下次全量重来」的关系，须设计后再动。
4. **§3 的 audit 传播面**：是否从「只沿 `sources:` slug」扩到图传播（wikilink/aliases），属 P3.7 之后。
