# nashsu/llm_wiki 反向评审 v0.6.11（增量·backlog）

> **状态：§2.1 已实现（见 CHANGELOG `[未发布]`）；§2.2 / §2.3 仍在 backlog，属低成本增强，可选。**
> 接上一篇 [`llm_wiki-反向评审-v0.6.4.md`](llm_wiki-反向评审-v0.6.4.md)（停在 `38f4cb1` / v0.6.4），
> 本篇覆盖 2026-09-11 pull 的 `38f4cb1..e808211`（v0.6.4 → v0.6.11）。
>
> 方法同前：**只借形状、不借实现**，按观澜教义过滤（零-LLM vs LLM 分档、`raw/` 不可变、markdown 唯一真相、
> 薄壳不携带业务智能、低噪 advisory、中文优先）。分档：强借 / 战略借 / 有张力 / 已在路上 / 已领先 / 别借。
>
> 关联：[`nashsu-llm_wiki-反向评审结论.md`](nashsu-llm_wiki-反向评审结论.md)、[`llm_wiki-反向评审-v0.6.md`](llm_wiki-反向评审-v0.6.md)、
> [`finding-持久抑制-未排期.md`](finding-持久抑制-未排期.md)、[`../../P3.11-断链最近页建议.md`](../../P3.11-断链最近页建议.md)、
> [`../../P3.8-链接归一.md`](../../P3.8-链接归一.md)、[`../../P3.9-源撤回.md`](../../P3.9-源撤回.md)。

## 0. 本次增量主线

| 项 | 数 |
|---|---|
| 提交（去 merge） | 122 |
| 改动文件 | 184 |
| 增 / 删行 | 19368 / 2115 |

上游三条主线：

1. **摄入完整性**（`e82845a` 截断 FILE 块定向重生、`07e0f36` 半成品不算成功摄入且不入缓存、`bb601cf` 准备/提交分离）。
2. **检索姿态第三档**（`3ab48b7` faithful：证据只取 raw 原文片段，`3629e8e` 其上下文隔离）。
3. **大库可用性**（`6661b81` 断链建议改倒排 + worker、`e33e8ac` lint 抑制开关、`a96a898` 并发摄入池、`714b33d` 去重分批）。

其中 6 条带 `Co-Authored-By: Claude Opus 5 (1M context)`，是你自己提上去的（`fd9c953` 跳过文件上报、
`fe70eb9` 图注语言、四条 Windows 路径大小写），本篇不重复评审。

## 1. 新增缺口（真实的）

### A. `[[…]]` 扫描不跳围栏代码块与行内 code —— **与渲染器自陈的语义相反**（对应 `0013ca3`）

上游 `0013ca3 fix: harden merged wikilink normalization` 给 wikilink 改写加了四道跳过：围栏块（三个及以上反引号或波浪号，
闭合栏长度须 ≥ 开栏）、缩进代码块、行内 code（按反引号 run 配对）、反斜杠转义与 `![[…]]` 嵌入式引用。

观澜的扫描口径只抹了 HTML 注释：`WIKILINK_RE`（`pages.py:87`）在 `check.py:108` / `graph.py:140` /
`im/reply.py:163` 三处都只套 `strip_html_comments`，**没有任何围栏或 code 跟踪**。

**这不是可争论的口径选择——仓库里已经写明哪边是对的。** `render.py:378` 的注释原话：

> 且链接语义上并无分歧（围栏内本就不成链，扫描器也不该把代码示例算作引用）。

渲染器靠 python-markdown 的占位符天然免疫（`render.py:9`），扫描器没有。于是同一张页在 Web 上不出链接，
`guanlan check` 却报它断链。

**实证（最小库，两张 concept 页各含一段围栏 python）**：

```
$ guanlan -C <kb> check
✗ check 失败：2 页，3 条违规：
    [wikilink.broken] wiki/concepts/DataFrame.md: [["date","value"]] 无对应页面
    [wikilink.broken] wiki/concepts/Pandas.md: [["date","value"]] 无对应页面
    [wikilink.broken] wiki/concepts/Pandas.md: [["x"]] 无对应页面
exit=3

$ guanlan -C <kb> lint
    [lint.missing_entity] (全局): [["date","value"]] 被 2 页引用却无页面，建议建 entities/"date","value".md

$ guanlan -C <kb> graph --json-only   # 幻影未解析边进图
edges: [('dataframe', '"date","value"', False), ('pandas', '"date","value"', False), ('pandas', '"x"', False)]

$ guanlan -C <kb> heal --dry-run      # 幻影进了 LLM 写路径的 worklist
· heal 预览（dry-run，零 LLM）：本批将物化 1 个高频缺失实体：
    + "date","value"（2 页引用：wiki/concepts/DataFrame.md, wiki/concepts/Pandas.md）
```

**触发面不窄，且全是观澜自己鼓励写的内容**：

| 形状 | 例 | 扫出的幻影目标 |
|---|---|---|
| pandas 双括号选列 | `df[["date","value"]]` | `"date","value"` |
| 行内 code | `` `df[["x"]]` `` | `"x"` |
| flint 图表规格里的嵌套数组（P4.20） | `{"values": [[1,2]]}` | `1,2` |
| KaTeX 下标（P4.14） | `$$x_{[[i]]}$$` | `i` |

mermaid 块不触发（无 `[[`）。缩进代码块同理触发。

**危害分级**：

- `guanlan check` 退 3，把一张合法页判成失败——**这一条最重**，`check` 是确定性校验的地基。
- `graph` 落幻影未解析边；`lint` 报幻影断链，且 `MISSING_ENTITY_MIN_REFS = 2`（`lint.py:61`）门槛很低，
  同一个惯用写法出现在两张页上就升级成 `missing_entity`。
- `heal` 把幻影**喂进 LLM 写路径**——零-LLM 检测器往 LLM 写入口灌垃圾，是分档纪律里最不该发生的方向。
- 写门禁不受影响：决策8 把 `wikilink.broken` 定为警告不阻断（`gate.py:51`），幻影只污染告警行。
- P4.22 可点引用降级安全：`im/reply.py` 抽出的名字解析不到页就不出按钮，但幻影仍占 `max_actions` 预算前的候选位。

### B. 缺「结构化制品逐字保留」规则（对应 `4fe686d` + `09ace0c`）

上游发现「先摘要、再生成」的两段流水线会把 SQL DDL、表结构、API 签名、配置压成散文——导入 schema 的用户
拿到的是对 schema 的描述而不是 schema（其 issue #507）。修法**全在 prompt**：分析阶段与生成阶段各加一条
规则，要求结构化数据原样抄进围栏代码块或 markdown 表格，字段名 / 类型 / 约束 / 键 / 索引一个不许降成散文。

观澜没有这条。`conventions.md` 里唯一沾边的是图片取舍规则中的「关键表格或图表截图」（第 56 行），管的是
**嵌图去留**，不管**正文改写**；`SKILL.md` 摄入步骤 2 也只说写摘要页。

对法律语料这是直接损失：条文、罚则档次、生效日期表被摘成散文即失真，而 wiki 页正是 query 的主召回面。

**边界必须收窄，不能抄成通用的「逐字保留」**：`SKILL.md` 明确 wiki 是摘要层（「wiki 是摘要层，不是图床」，
`conventions.md:56`）。规则只能覆盖**改写即失真的制品**（条文 / 表格 / 签名 / 配置 / 命令行 / 数据字典），
散文照旧摘要。这是 B 与「全文库」的分界线，写规则时要显式写进去。

### C. lint 无任何抑制开关（对应 `e33e8ac`）

上游给 lint 加了每库一个 sidecar（`.llm-wiki/lint-config.json`），三个开关：`ignoreOrphan`、
`ignoreNoOutlinks`、`ignorePages`（slug 列表）。**没有稳定 finding id，没有 baseline。**

观澜 `lint.py` 全文无 ignore / config 路径（已 grep）；八类 finding 每次全量重算重报。
backlog 里 [`finding-持久抑制-未排期.md`](finding-持久抑制-未排期.md) 设计的是「稳定 key + overlay」，当时判定
「明显偏重（改 CLI 操作面 + 引持久状态）」而搁置。

上游这版是同一痛点的**十分之一成本版**：大库上真正刷屏的是 `lint.orphan` 与 P3.5/P3.6 那批拓扑建议
（`hub_node` / `isolated_community` / `bridge_edge` / `cut_vertex`），粗档开关就够，不需要 finding 身份。

**一个坑要连注释一起抄**：上游 `lint-structural-core.ts:182` 写明忽略项按 slug 或路径匹配，
**绝不拿裸 basename 去匹配所有同名嵌套页**，否则 `foo` 会连 `archive/foo` 一起吞掉。这正是
「给扫描器加过滤规则必须配一条『本该报出却被吞掉』的用例」那条纪律——漏报是沉默的，正例测不出来。

### D. 无「只认原文」的检索姿态（对应 `3ab48b7`，**有张力**）

上游加了第三种检索姿态 Faithful：证据只能来自 raw 源片段或用户显式附上的源文件，引文逐字保留，
每条断言旁标源路径，证据不足就说不足、不许重构。三处机制值得借形状：

1. **硬拒而非劝阻**：`wiki.search` / `wiki.read_page` / `graph.search` / `web.search` / `anytxt.search`
   在权限层直接返错（`runtime.rs` 的 `require_tool_permission`），不是在 prompt 里劝。
2. **只由用户显式开**：`types.rs` 注释写死 `never inferred from wording or language-specific heuristics`。
3. **检索预算单独调低**：faithful 档比 smart 档更紧。

观澜今天只有 `query` 与 `query --backfill`（`query.py:55-56`），没有「只认原文」姿态。对法律库这条最值钱：
答「这条到底怎么规定的」应当给条文本身，而不是摘要页的二手转述。

**两处张力，决定它是「战略借」而非「强借」**：

- **没有工具黑名单的下沉面**。`run_agent_task`（`runtime.py:53`）只暴露 `permission_mode` 与 `skills`，
  **没有 per-tool allow/deny**。上游那条「硬拒」在观澜今天只能退化成 prompt 纪律，而 prompt 纪律恰恰是
  上游明确放弃的那一档。要真做硬拒，得往 agentao 提能力诉求（同
  [`agentao-嵌入文件权限边界需求.md`](agentao-嵌入文件权限边界需求.md) 的体例）。
- **注入面比 query 更大**。原文模式把 `raw/` 正文直接灌进答案合成，而 `conventions.md:239` 已经写了
  「query 把候选页正文回灌进第二个 LLM，注入效应二次叠加」的告诫，并给的处方是**存疑时优先直读**。
  原文模式等于把「回灌不可信正文」变成默认姿态，与 P4.11 信任边界正面相撞，须单独论证。

**成本比看上去低的那一半**：不必新建 raw 检索索引。`search` 的语料是 `wiki/`-only（`search.py:263-267`，
docstring 明说「不碰 `raw/`」），但 `wiki/sources/<slug>.md` 与 `raw/<slug>.md` 是 1:1 的，
「search 召回摘要页 → 打开它的 raw」两跳就拿到原文，复用 P3.7 已有的 slug 归口。

## 2. 最小动作

### 2.1 给链接扫描补围栏 / 行内 code 跳过（**已实现**，修 §1.A）

**落点**：`pages.py`，与 `strip_html_comments` 并列的第二个公共口径（暂名 `mask_code_spans`），
**同样抹成等长空白**——`strip_html_comments` 的等长契约是 `reindex --prune` / `remove` 的行边界依赖
（`pages.py:145-152`），新口径不许破。一处落地，`check` / `graph` / `im/reply` 三个调用点自动继承
（`im/reply.py:151` 的 docstring 已经写明「不再各写一份，否则两处会漂移」——归口本来就在那儿）。

**做什么**：

- **围栏块**：三个及以上的反引号或波浪号，开栏允许 0–3 空格缩进与 info string，闭合栏须同字符且长度 ≥ 开栏、其后仅空白。
  直接抄上游 `normalizeWikilinksOutsideCode` 已验证的边角，不二次踩坑。
- **行内 code**：按反引号 run 配对；**未配对的 run 是普通文本**，照常参与扫描（上游注释已写明）。
- **反斜杠转义**：`\[[X]]` 不算引用（数前导反斜杠奇偶）。

**不做什么（两条，都要写进注释）**：

- **不做缩进代码块**。上游用的是裸 `/^(?: {4}|\t)/`，会把列表项的缩进续行一并当代码。观澜纪律是
  「漏报比误报危险」（`conventions.md:47` 行内注释那条的同款理由）——把真链接抹掉是沉默失败，
  把代码示例算成链接只是噪音。故缩进块**保持现状**，并在注释里写明这是有意接受的残余。
- **行内 code 必须给「整段恰好是单条页面引用」留缺口**。`render.py:349` 的 `_code_wikilink_raw` 是
  **有意**的兜底：`` `[[Foo]]` `` 在 Web 上渲染成链接，`conventions.md:46` 也这么写的。若扫描器把
  行内 code 全抹，就造出**反向**漂移——Web 上是链接、`check` 不校验它。故行内 code 的跳过必须排除
  「整段规整后恰好是一条 `[[…]]`」这一形状，与渲染器对齐。**这是本条最容易写错的地方。**

**测试面**：§1.A 表格四行各一例（pandas 选列 / 行内 code / flint 嵌套数组 / KaTeX 下标）+ 未配对反引号
+ 围栏内真链接不入图 + `` `[[Foo]]` `` 仍算引用（对齐渲染器）+ 等长空白不改行数（护 `reindex --prune`）。
**再加一条漏报向用例**：围栏外的正常 `[[X]]` 在含围栏的页里仍被扫出——过滤规则的漏报用例。

**回归风险**：既有库里若有页正因幻影链接而挂着 `check` 失败，修完会由失败转通过，这是意料内的收敛。
反向风险是把真链接抹掉，由上一段的缺口规则与漏报用例兜。

**实现落点（已完成）**：`pages.mask_code_spans` + `pages.link_scan_text`（单一预处理归口），
`check.py` / `graph.py` / `im/reply.py` 三处改调 `link_scan_text`。修后同一最小库：`check` 退 0、
`lint` 只剩三条真 `orphan`、`heal --dry-run` worklist 空。

**实现期改掉的两处设计**（都是实测推翻了草案的猜测，草案正文保留原样以留痕）：

1. **闭栏规则由「长度 ≥ 开栏」改成「逐字对称」。** 草案照抄了 CommonMark。实测 python-markdown
   **不认更长的闭栏**（```` ``` ```` 开、`````` ````` `````` 闭 → 不闭合、照常成链）。按 CommonMark 抹就会
   漏报。两边分歧处一律取更严的那档。
2. **`![[X]]` 的 `!` 跳过不借。** 草案原样列了上游那条。实测观澜渲染器**会**把 `![[Foo]]` 渲成链接
   （观澜没有 Obsidian 嵌入语义，嵌图走 `![](路径)`），照抄就是凭空造一类漏报。

**实现后代码评审又揪出三条「错位型」漏报（都已修）**——它们比草案预想的那类更阴险，值得记下形状：
扫描器与渲染器对"哪一行是开栏"意见不合时，**奇偶整体错位一格**，于是扫描器把渲染器眼里的**正文**
当成代码抹掉。不是多报，是**漏报**——真断链被 `check` 静默吃掉、退 0。

1. **开栏标记后跟 Tab**。渲染器的 `normalize_whitespace`(30) 先于 `fenced_code`(25) 把 Tab 展成空格，
   故 ```` ```\t ```` 在它眼里是合法开栏；初版只认半角空格。
2. **info string 写得太宽**。初版用 `[^\r\n]*` 接受任意 info string，但 python-markdown 只认
   `{attrs}` 或 `.?lang`——`~~~json 示例`、```` ```py(3) ```` 在渲染器眼里**根本不是代码块**，初版却把它们抹了。
3. **行分隔符集合取宽了**。初版用 `str.splitlines()`（认 `\v \f \x1c \x1d \x1e \x85 \u2028 \u2029`），
   markdown 只认 `\r\n|\r|\n`。`\u2028` 在 P5.2 `convert` 的 PDF 产物里并不罕见。

**方法论教训（比这三条本身更值钱）**：初版的对齐表只比"**有没有**链接"，而错位型漏报两边**都有**
链接、只是**不是同一条**——表是绿的。现已改成比**链接名的序列**。凡是"两边口径必须一致"的护栏，
比布尔一律不够。

**新增残余（已在代码注释与 conventions 里写明）**：

- **缩进 1–3 的围栏不抹**（多报）。渲染器在这一档**行为随 info string 而变**——实测 `   ```yaml` 当代码、
  `   ``` `（无 info string）不当代码。既然无法对齐就不抹，并在 `tests/test_web.py` 里把这条分歧
  钉成「只许在多报方向」的用例。
- **数学不在覆盖面内**。`$$x_{[[i]]}$$` 仍会扫出幽灵 `i`——它不是代码，P4.14 的数学是**作普通文本
  穿过服务端、客户端再排版**的，渲染器同样会把它渲成链接。两边一致，故不算本条的回归；真要收
  需要 P4.14 侧的数学感知，另立。

### 2.2 `conventions.md` + `SKILL.md` 各加一条「结构化制品逐字保留」（修 §1.B）

纯 skill 侧 prompt 规则，零代码。落点两处（与上游对称：分析侧 + 生成侧）：

- `conventions.md` 新增一小节，列**枚举式**的制品清单（法条条文 / 表格 / 数据字典 / DDL / API 签名 /
  配置 / 命令行），要求原样进围栏块或 markdown 表格，并显式写清**它不推翻「wiki 是摘要层」**：
  只有「改写即失真」的制品逐字留，叙述性内容照旧摘要。
- `SKILL.md` 摄入步骤 2 的摘要页那条加一句指针，指到新小节。

配 P4.14 已落的代码高亮与 P4.20 的表格渲染，逐字块在 Web 上本就能好好显示，无额外宿主工作。

### 2.3 lint 粗档抑制 MVP（修 §1.C，可选）

按上游形状做最小版：每库一个 sidecar，只放**粗档开关 + 页面忽略列表**，不引 finding 稳定 id、不引 baseline、
不加 `--suppress` 写入子命令（那套仍留在 [`finding-持久抑制-未排期.md`](finding-持久抑制-未排期.md)）。
`ignorePages` 按 slug / 相对路径匹配，**绝不裸 basename**，并配漏报用例。

排期前先确认痛点是真的：在你的真实库上跑一次 `guanlan lint`，数 `orphan` 与四类拓扑建议的实际条数。
不刷屏就不做——这与 P3.11 当时拆掉抑制那半的判断一致。

## 3. 仅观察（不排期）

- **`.trash/` 无界**（对应 `d81949f feat: make file history opt-in and bounded`）。P3.9 决策-10 已把
  回收区 GC 显式降为后续 `trash purge`（`P3.9-源撤回.md:117`），一期靠人工清。上游补的是**形状**：
  做成 opt-in + **条数与体积双上限**，而不是 TTL。将来排 `trash purge` 时按这个形状写，别上时间维度
  （墙钟语义会引入「多久算旧」的口径争论，条数/体积是确定性的）。
- **MinerU backend 名随服务端版本漂**（对应 `1fd8d5a` + `3a84732`）。上游发现 MinerU 3.0–3.2 把
  `vlm-engine` / `hybrid-engine` 叫作 `vlm-auto-engine` / `hybrid-auto-engine`，于是先打 `/health` 探版本
  再决定表单里填哪个名。观澜的 `pdf-to-markdown` 走 CLI（`convert.py:137-143`），**只在用户显式给
  `--mineru-backend` 时才传 `-b`**，默认让 MinerU 自己选——所以**没有踩这个坑的面**。唯一残留是
  help 文本的举例 `'vlm-auto-engine'`（`convert.py:300`）是 3.0–3.2 的拼写，在 3.3+ 上会是个坏例子。
  纯文档 nit，随手改，不单独排期。
- **MCP 会话绑库**（`8eebc68 feat: bind MCP sessions to projects`）。观澜 P4.17 的 http 传输是
  `stateless_http` + 零服务端会话、单库绑定，**当前无此需求**；若将来做多租户 / 多库 scoping（已推 E2），
  这条是现成的参考形状。
- **路由诊断不入回复**（`b618712 fix: hide agent router diagnostics from replies`）。观澜 IM/Web 的回复
  组装未见此类泄漏面，但 P4.21 的分片与截断告示逻辑是同一段代码的邻居，日后加诊断字段时记得这条。
- **摄入准备与提交分离 + 并发池**（`bb601cf` / `a96a898` / `fa2652e` / `0fd9257`）。观澜 `ingest` 是
  一次一源的子进程 + 全 `raw/` 快照门禁（`gate.py`），**并发会让两次摄入互相看见对方的写**，
  快照语义直接失效。除非先给门禁设计按源隔离的快照，否则不引并发——记为「有架构理由的不做」。

## 4. 不做什么（已免疫 / 已领先，附验证）

- **半成品被冻成「已是最新」**（`07e0f36` 不缓存部分写入）→ **免疫**。`ingest.py:163` 把
  `_stamp_source_digest` 挂在 `rc == EXIT_OK` 之下，门禁不过就不盖 `raw_digest`，`audit` 下次照判 source-drift。
  上游是踩了这个坑（部分结果进缓存、重试只重放成功的那些页）才补的，观澜从 P3.7 决策-3a 起就是对的。
- **截断 FILE 块检测与定向重生**（`e82845a`）→ **架构不适用**。上游一次流式吐一个巨大的 `---FILE:` 流，
  未闭合块要自己认；观澜的 skill 经 `write_file` 逐页写，截断表现为工具调用不完整，直接落成 agent 错误。
  其「部分写入不算成功」的**姿态**观澜早有（fail-closed 门禁 + 自愈有界 + `raw_mutated` / `agent_error` 不自愈，
  `gate.py:36-38`）。
- **断链建议扩容**（`6661b81 fix: scale wiki link repair for large projects`）→ **已领先，别反向借**。
  上游本轮被迫把逐断链全库扫改成倒排索引 + worker。`P3.11-断链最近页建议.md` §0.1 第 3 点在**设计期**就拒了
  「逐断链调 `search_pages`」（理由原文：「`B` 个断链 = `B ×` 全库扫描 + `B ×` 建图」），选了一次建轻量倒排
  （`lint.py:108 _build_suggestion_index`）。评审判断被上游后续验证。
- **社区检测**（新增 `wiki-graph-analysis.ts`，graphology + Louvain + worker）→ **已领先**。
  `graphstats.py` 早有**手写确定性** Louvain（不引 networkx / python-louvain，决策P3.5-3/2），
  且另有割边 / 割点（P3.6 迭代式 Tarjan），上游没有。上游那版依赖第三方库、社区号稳定性未做保证。
- **导出目的地回落库内**（`1b3caa6`：目标路径不存在时 canonicalize 其父目录再判包含）→ **无此面**。
  观澜没有归档导出（`P3.9-源撤回.md:59` 已论证：markdown 唯一真相下 git 即归档）；raw / web 的路径准入一律
  realpath + `relative_to` 兜底（`provenance.admit_raw_path`、上一篇 §4 已逐条验过）。
- **单页向量索引 / embedding 加速 / deep research 批跑**（`5427eb5` / `c583d91` / `3550dae` / `0ff6f31`）
  → **别借**。向量检索早记进 E1，P5.0 选 BM25 + CJK 2-gram 是刻意的确定性选择。
- **多 provider / 按库选模型 / ingest 与 chat 分模型路由 / ingest reasoning 档位**
  （`2fed492` / `40f07da` / `8bcd184` / `c99a26d`）→ **别借**。provider 抽象刻意下沉 agentao，
  观澜层无 provider 概念，上一篇 §4 已结论。

## 5. 未决项登记

1. **§2.1 的缩进代码块残余**：有意不做，但要在 `mask_code_spans` 注释里写明，避免日后被当成漏洞重开。
2. **§1.D 的工具黑名单**：若排原文模式，需先决定是走 prompt 纪律（弱、与上游明确放弃的那档同级）
   还是往 agentao 提 per-tool deny 能力（强、但引上游依赖）。二选一前不动工。
3. **§1.D 与 P4.11 的相撞**：原文模式把 raw 正文变成默认回灌面，与 `conventions.md:239` 的「存疑时优先直读」
   处方方向相反，须单独论证后才能立小设计。
4. **§2.3 的前置计数**：真实库 `lint` 的 advisory 条数未测，测完再定排不排。
