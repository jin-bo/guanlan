# flint-chart 集成研判

> 状态：**调研笔记**。本笔记保存**核实过的机制事实**与三条候选路径的取舍；所有字节数/行号均为实测
> （2026-08-03，flint-chart `0.4.1`）。
>
> **进展更新**：因「观澜经 MCP 接入用户结构化数据库」这一新前提，§6 的「收益随库里有多少可制表的数字才显现」
> **触发条件已判为满足**，**路径 A 已出落地设计** → [`../../P4.20-Web-flint图表渲染.md`](../../P4.20-Web-flint图表渲染.md)
> （设计稿、未实现）。本笔记 §6 的「不排期」结论**已被取代**，其余各节（选型事实 / 路径 B、C 的阻塞 / §5 辨析）仍有效。
>
> 关联：[`../../P4.13-Web-mermaid渲染.md`](../../P4.13-Web-mermaid渲染.md)（前端内容渲染器的**既有骨架与破例口径**）、
> [`../../P4.14-Web数学化学代码渲染.md`](../../P4.14-Web数学化学代码渲染.md)（同骨架的第二次应用 + 安全闸范式）、
> [`mcp客户端注入-未排期.md`](mcp客户端注入-未排期.md)（外部 MCP server 注入的机制事实；本笔记 §3 是它的一个具体上游实例，并**修正**其一处结论）、
> [`next-milestone-and-graph-viz.md`](next-milestone-and-graph-viz.md)（图可视化的排期判据——**flint 不是那个坑的解**，见 §5）。

## 0. 一句话结论

**路径干净、成本可控，但触发条件未到。** 若做，取 **P4.20 = Web 前端 ` ```flint ` 围栏渲染**，
沿 P4.13/P4.14 已跑通的「服务端零改 + vendored 内容渲染器 + 安全闸 + 失败保留源码」骨架，
后端 **ECharts**、体积 ~1.57 MB（不到现有 mermaid 3.3 MB 的一半）。
**MCP 注入路（`flint-chart-mcp`）现在不通**且要引入观澜至今没有的 Node 依赖（§3）；
**Python 服务端编译路（`flint-py`）不可行**（未发 PyPI，§4）。

## 1. flint 是什么（已核实）

微软研究院 + 人大 IDEAS Lab 的**可视化中间语言**：输入 = `数据 + 语义类型 + 紧凑图规格`，
编译器据数据基数/语义类型自动推导刻度、轴、标签、图例、布局，输出**各后端原生 spec**
（Vega-Lite / ECharts / Chart.js / Plotly / Excel）。MIT 许可。卖点正是「**Agent 能可靠生成、人能直接编辑**」——
与观澜「LLM 写 markdown、markdown 是唯一真相」的姿态天然同向。

三个包：`flint-chart`（TS 编译器，npm）、`flint-chart-mcp`（MCP server，npm）、`flint-py`（Python 移植，**源码预览、未发包**）。

**实测的输入/输出形态**（本机 node v24 离线跑通，含 CJK 字段名）：

```js
assembleECharts({
  data: { values: [ {年份:'2023', 许可:'开源', 模型数:42}, … ] },
  semantic_types: { 年份:'Year', 模型数:'Count', 许可:'Category' },
  chart_spec: { chartType:'Bar Chart',
                encodings:{ x:{field:'年份'}, y:{field:'模型数'}, color:{field:'许可'} },
                baseSize:{ width:420, height:260 } },
})
// → {"tooltip":{"trigger":"axis",…},"xAxis":{…},"series":[…],"_width":…,"_height":…,"_transform":…}
```

三处对观澜要紧的性质：

1. **数据内联在输入里**（`data.values`）。整张图 100% 可从那段 markdown 重建，**不产生任何衍生态**——
   守得住「markdown 唯一真相」。这点比 mermaid 还干净（mermaid 也是纯文本，但不带数据语义）。
2. **输出带私有键** `_width` / `_height` / `_options` / `_transform` / `_pivot`——前端 wrapper 须消费（尺寸）
   并剥离后再交渲染器，不能原样灌进去。
3. **语义类型会显著改写结果**：上例把 `年份:'Year'` 推成了 `type:"temporal"` 且自动补了
   `scale.domain:["2022-07-02T…","2024-07-01T…"]`。对 LLM 友好（少写就得好看），对**人手写有学习成本**
   （0.4.1 的 `SemanticTypes` 枚举 45 个；上游 README 称「70+」）——这与观澜「围栏块是人也要能改的」预期有
   张力，须在文档里明说。**缓解**：`semantic_types` 可整段省略，flint 会自行推断（实测），故它是优化项而非必填。

## 2. 路径 A（推荐形态）：Web 前端 ` ```flint ` 围栏渲染

### 2.1 为什么它能套现有骨架

`render.py` **零改动**：`fenced_code` 已把 ` ```flint ` emit 成 `<pre><code class="language-flint">转义 JSON</code></pre>`，
与 mermaid/代码块**同一载体**（决策P4.13-4 的转义姿态一字不动）。前端加一个 `flint_enhance.js`，
挂进 P4.14 已泛化出来的 `enhanceContent` 编排器 + 四个注入点，失败退回 `<pre>` 源码 + 错误徽标——
**这条路上没有一处是新机制**，除了下面 2.3 的 ESM 加载姿态。

### 2.2 vendored 体积与后端取舍（实测字节）

flint **只出 spec、不渲染**，所以要 vendored **两层**：编译器 + 渲染器运行时。

| 方案 | 编译器（flint dist） | 渲染器运行时 | 合计 | 备注 |
|---|---|---|---|---|
| **ECharts** ⭐ | `dist/echarts/index.js` 531,443 B | `echarts.min.js` 1,034,102 B（**UMD 单文件**，Apache-2.0） | **~1.57 MB** | 单文件、时间轴原生、无额外 adapter |
| Vega-Lite | `dist/vegalite/index.js` 426,506 B | `vega.min.js` 515,242 + `vega-lite.min.js` 252,198 + `vega-embed.min.js` 60,630 + `vega-interpreter.min.js` 5,709（BSD-3） | ~1.26 MB | **四**文件、须 `ast:true` 走解释器免 `new Function` |
| Chart.js | `dist/chartjs/index.js` 306,776 B | `chart.umd.js` 208,518 B | ~0.5 MB | 最小，但时间轴须再配 date adapter（+依赖） |

对照系：现有 `mermaid.min.js` **3,312,967 B**、katex 家族 ~632 KB。**ECharts 路在既有量级之内**，
且「一个后端一套 vendored」——不要为了多后端把三套都塞进 wheel。

### 2.3 唯一的新机制：**flint dist 是 ESM，不是 UMD**

`flint-js` 用 tsup 出 **ESM + CJS**，**没有 UMD/IIFE 构建**，也不发 `.min`。但实测
`dist/{vegalite,echarts}/index.js` **零 bare import、零动态 `import(`**——是**自包含单文件 ESM**，
可直接 `await import('/static/vendor/flint/echarts.js')` 加载，**不需要 npm、不需要打包步**，
因此**仍然落在决策P4-3「前端无 npm/构建/CDN」的既有破例口径内**（决策P4.13-1：只放行「内容渲染器」）。

但姿态与 mermaid/katex/hljs 三件既有 vendored 资产**不同**：它们是注入 `<script>` + 读 `window.X`，
flint 得走动态 `import()` + 具名导出、不挂 window。这是一处**须单列决策**的差异（升级校验步骤也要改：
不是 `tail` 看 UMD 尾巴，而是 `grep` 确认无 bare/动态 import）。渲染器那层（echarts UMD）仍走老姿态。

### 2.4 安全闸（本相位的实质，同 P4.13 §4 / P4.14）

图源来自 wiki markdown → 而 wiki 由 LLM 从 `raw/` 生成 → **本质是「内容→客户端」的第三次破例**，
须有一个「默认即安全、单旗、文档明载」的铰链（决策P4.14-2 立的标准）。候选：

- **ECharts**：`renderer:'svg'`（产物是 SVG DOM，不是位图）+ **`tooltip.renderMode:'richText'`**
  ——后者让 tooltip 彻底不走 HTML 通道，是与 `securityLevel:'strict'` / `trust:false` 同位阶的单旗铰链。
  **须实测确认**：richText 与 svg renderer 组合下 tooltip 仍正常，且 flint 产出的 option 里
  没有会被覆盖掉的 `tooltip.formatter`（本笔记那个柱状图实测**没有** formatter，但不能推广到全部图型）。
- **Vega**：`ast:true` + `vega-interpreter`（免 `new Function`，对严格 CSP 友好）。
  旁证：`flint-chart-mcp` 自己的依赖里就有 `vega-interpreter@^2.2.1`。

另有一条**观澜特有**的闸：flint 输入是 JSON，**没有函数**——所以「围栏块里能不能藏可执行回调」这个
mermaid 式的问题在这里天然不存在，只剩「渲染器把字符串当 HTML 插」这一个面。

### 2.5 验收

沿 P4.13/P4.14/P4.15 的既定做法：`tests/test_web.py` 只能验后端字节不变，**渲染/交互/注入必须走真浏览器冒烟**
（`scripts/smoke_p4xx.py`，headless Chromium/Playwright，harness 已现成）。至少覆盖：
懒加载（无 flint 块的页零加载）、CJK 字段名、失败三态（运行时加载失败 / JSON 语法错 / flint 抛错）均保留源码 + 徽标、
tooltip 注入用例（数据里塞 `<img onerror>` 后确认不执行）、切语言/切预览模式后重增强。

### 2.6 一个**不依赖用户语料**的自用场景（若做，值得一并想）

观澜自己就有天然表格数据：`graph.json` 的度分布 / 社区规模、health 的 stub 计数、lint finding 分类、
ingest 日志时间线。这些现在只以**文字 finding + 零-JS 静态 html** 呈现（决策P3-7）。
一个**确定性零-LLM 的 flint 块生成器**（Python 侧拼 JSON，不需要 flint 编译器）可以让报告面板一眼可读，
且**完全不依赖用户库里有没有表格**——这是路径 A 最不看运气的那部分收益。

## 3. 路径 B：注入 `flint-chart-mcp`（**现在不通**）

对应 [`mcp客户端注入-未排期.md`](mcp客户端注入-未排期.md) 与已落地的 P4.19 诊断。核实结果：

- **形态**：`npx -y flint-chart-mcp`，stdio；五工具 `render_chart` / `compile_chart` / `validate_chart` /
  `list_chart_types` / `create_chart_view`。README 明载**从不写盘**、**不需要网络**（远程 URL 被禁以防 SSRF），
  默认可读本地 `.json/.csv/.tsv`（`--disable-file-reference` 可关）。
- **硬阻塞①：五个工具都没声明 `ToolAnnotations`**（`packages/flint-mcp/src/server.ts` 的
  `registerTool` 调用里**无 `annotations` 字段**；文件里唯一的 `annotations:` 是 `audience:['assistant']`，
  那是 content 注解不是工具注解）。对上 agentao `mcp/tool.py`：`is_read_only` 要求
  `trust=true` **且** `readOnlyHint is True` → **只读路径（`query` / `guanlan mcp` 的 `ask` / Web 只读问答）恒被 DENY**。
  - **修正** [`mcp客户端注入-未排期.md`](mcp客户端注入-未排期.md) §3 的一处推论：`requires_confirmation`
    在 trust 后**只**看 `destructiveHint is True`，缺注解 → False。所以**标了 `trust:true` 的 flint-mcp
    在可写路径（`ingest`/`heal`/`audit`/P4.5 可写会话）是能用的**，「不 trust 也能用不存在」成立，
    但「trust 了也全灭」不成立——只读路径灭，可写路径通。
- **硬阻塞②：位图会被丢**。`render_chart` 的 PNG 分支回 `image` content block，而 agentao
  `mcp/client.py:563` 把它折成字符串 `[image: image/png]`——**字节丢失**。`format:'svg'` 走 `text` block，
  SVG 源文本能完整穿过（`server.ts` 实测两分支不同）。⇒ 真要走这条，**只能用 svg**。
- **硬阻塞③：Node 依赖**。观澜至今**零 Node**（`convert` 也只 shell 到 Python 侧 backend）。
  且 `flint-chart-mcp` 依赖 `@napi-rs/canvas` / `@resvg/resvg-js` 两个**原生模块** + `echarts`/`vega`/`chart.js` 全家，
  `npx -y` 首跑要拉原生二进制——这与观澜「pip 装完即可用」的分发承诺相冲。

**最省力的上游解锁点**：给 flint 提 PR/issue，让这五个只读工具补上 `annotations:{readOnlyHint:true}`。
它们确实全部只读、不写盘，是上游一行的事；补上之后只读路径才谈得上。**这不构成观澜排期理由**，
但如果哪天做外部工具注入的闸，flint-mcp 是个干净的样本上游。

## 4. 路径 C：`flint-py` 服务端编译（**不可行，但值得设触发条件**）

`packages/flint-py`：**source-only preview，明确写着 PyPI 发布推迟**（等兼容性测试收口），
且**只有 `assemble_vegalite()` 一个入口**（无 echarts/chartjs/plotly），以 TS 源为 ground truth、靠录制比对保持 parity。

⇒ 现在引它只能钉 git 源，违反观澜的依赖纪律，且拿不到 ECharts 后端。

**但这条路一旦通了反而最契合观澜**：Python 侧把 flint 编译成 Vega-Lite（可放进一个 `[chart]` extra），
前端只 vendored 渲染器那层（vega 家族 ~830 KB），**wheel 里不放编译器**、升级也走 pip 而非手抄 SHA256。
⇒ **触发条件：`flint-py` 上 PyPI 且覆盖到需要的后端时，重新评估路径 A vs C。**

## 5. 辨析：flint **不是**「图可视化那个坑」的解

[`next-milestone-and-graph-viz.md`](next-milestone-and-graph-viz.md) §3 记的那个被推迟两次的空位，
要的是 **node-link 力导向图**（按社区着色、高亮割边割点，喂 `graph.json`）。flint 是**统计图表**语言
（柱/线/散点/热力…），**不做网络图**。两件事互不替代，也不该合并成一个相位。

## 6. 排期判断　**\[已被取代，见抬头「进展更新」]**

> 下文是**接入结构化数据库之前**的判断，保留以记录当时的理由。前提变化后结论翻转：数据源不再取决于
> 「用户的 `raw/` 里碰巧有没有表格」，而是由 MCP 直连的结构化库稳定供给 → 触发条件满足 → 见
> [`../../P4.20-Web-flint图表渲染.md`](../../P4.20-Web-flint图表渲染.md)。


与图可视化同一个判据：**收益随「库里有多少可制表的数字」才显现**，而观澜的 KB 内容偏「关系型知识」
（实体/概念/综述）而非表格数据——除非用户的 `raw/` 里大量是统计报表，否则**读者侧收益低于 1.57 MB
vendored + 一套新安全闸的成本**。

故：**不排期**。若排，形态锁死为 §2（P4.20、ECharts 单后端、服务端零改），
且优先做 §2.6 那半（观澜自己的诊断数据出图）——那半不看用户语料的运气。

## 7. 复核用命令（本笔记事实的来源）

```bash
# flint dist 自包含性（应无输出 = 无 bare import；计数应为 0）
curl -sO https://cdn.jsdelivr.net/npm/flint-chart@0.4.1/dist/echarts/index.js
grep -oE '(from|require\()["'"'"'][a-zA-Z@][^"'"'"']*' index.js | sort -u ; grep -c 'import(' index.js

# 渲染器运行时体积
for u in echarts@5/dist/echarts.min.js vega@5/build/vega.min.js vega-lite@5/build/vega-lite.min.js \
         vega-embed@6/build/vega-embed.min.js chart.js@4/dist/chart.umd.js; do
  curl -sSL -o /dev/null -w "$u %{size_download}\n" "https://cdn.jsdelivr.net/npm/$u"; done

# flint-mcp 是否声明工具注解（应只见 audience 那一处）
curl -s https://raw.githubusercontent.com/microsoft/flint-chart/main/packages/flint-mcp/src/server.ts \
  | grep -nE 'registerTool|annotations'

# agentao 侧的信任语义与 image 丢弃
grep -n 'is_read_only\|requires_confirmation' .venv/lib/python3.12/site-packages/agentao/mcp/tool.py
grep -n 'image:' .venv/lib/python3.12/site-packages/agentao/mcp/client.py
```
