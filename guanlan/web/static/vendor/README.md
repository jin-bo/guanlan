# vendored 第三方前端资产

本目录存放**随包打入 wheel** 的第三方前端运行时（非 CDN、离线可用）。`packages = ["guanlan"]`
（`pyproject.toml`）令 hatchling 自动携带包内非 `.py` 资源，此目录与 `static/*` 同机制入 wheel——
**无需** `force-include`。

收录原则与 P4 决策P4-3（前端无 npm/构建/CDN/第三方运行时）的有界破例理由见
[`docs/P4.13-Web-mermaid渲染.md`](../../../../docs/P4.13-Web-mermaid渲染.md) §6 决策P4.13-1：
仅放行**内容渲染器**（把一种标记渲染成富呈现，与服务端已 vendored 的 `markdown` 同位阶），
不放行 UI 应用框架。

## mermaid.min.js

| 项 | 值 |
|----|----|
| 版本 | `11.15.0`（钉版；升级走显式 PR 复核，见决策P4.13-3） |
| 上游 | https://cdn.jsdelivr.net/npm/mermaid@11.15.0/dist/mermaid.min.js |
| 包 | [mermaid](https://www.npmjs.com/package/mermaid)（npm，MIT License） |
| 字节 | 3312967 |
| SHA256 | `70137e77bb273bb2ef972b86e8b0400cca8be53cb25bfc45911a186dc98665de` |
| 形态 | **自包含 UMD 单文件**——加载后 `globalThis.mermaid` 就绪；**零运行时动态 `import()`**（核心图型 flowchart/sequence/class/state/er 全内联），故离线自洽 |

**为何用 UMD 而非 ESM**：mermaid v11 的 `mermaid.esm.min.mjs` 是**代码分割**构建、运行时动态 import
子 chunk，单文件不离线自洽；UMD `mermaid.min.js` 是经 esbuild `--bundle` 的单文件、无动态 import，
适合离线 vendored。前端经注入 `<script>` 加载、读 `window.mermaid`（见 `static/mermaid_enhance.js`）。

### 校验

```bash
shasum -a 256 guanlan/web/static/vendor/mermaid.min.js
# 应得 70137e77bb273bb2ef972b86e8b0400cca8be53cb25bfc45911a186dc98665de
```

### 升级步骤

1. `curl -o guanlan/web/static/vendor/mermaid.min.js https://cdn.jsdelivr.net/npm/mermaid@<新版>/dist/mermaid.min.js`
2. 确认仍是自包含 UMD（`tail` 见 `globalThis["mermaid"]=…`）、无新增动态 `import(`（`grep '[^a-zA-Z]import(' …` 应空）。
3. 更新本表版本 + 字节 + SHA256；浏览器手测 §7 验收串（含注入测试 `securityLevel:'strict'` 仍消毒）。
4. 经显式 PR 复核（决策P4.13-3：mermaid 历史有 XSS CVE，升级须复核 strict 模式无回归）。

---

## katex/（KaTeX 资产树，P4.14）

数学/化学渲染器。设计见 [`docs/P4.14-Web数学化学代码渲染.md`](../../../../docs/P4.14-Web数学化学代码渲染.md)
§6 决策P4.14-2（取 KaTeX 而否 MathJax：`trust:false` 是**默认即安全、单旗、文档明载**的白名单铰链）。
前端经 `static/math_enhance.js` 按序注入 `<link>`(css) + `katex`→`mhchem`→`auto-render` 三 `<script>`，
读 `window.renderMathInElement` 排版。

| 文件 | 字节 | SHA256 |
|------|------|--------|
| `katex/katex.min.js` | 276701 | `e8d885505949f3a5f4abdd5dd0d53696bd1371ad26ffbf4f310dcd77c8cdae89` |
| `katex/katex.min.css` | 23352 | `19095127357ed6d29fe0a63a6b000c913a89f7f1963b765dd3715e97c9852e75` |
| `katex/contrib/mhchem.min.js` | 33712 | `9f87e5e9c384a160472d0045035a8641f6013358eddb3ece708634a50f946a40` |
| `katex/contrib/auto-render.min.js` | 3467 | `bb53eb953394531aae36fdd537065c4244eb8542901a3ce914601d932675b8ac` |
| `katex/fonts/KaTeX_*.woff2`（20 枚） | ~296KB | 见下「字体清单 manifest」 |

| 项 | 值 |
|----|----|
| 版本 | `0.16.22`（钉版；升级走显式 PR 复核，见决策P4.14-2） |
| 上游 | https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/{katex.min.js,katex.min.css,contrib/mhchem.min.js,contrib/auto-render.min.js,fonts/KaTeX_*.woff2} |
| 包 | [katex](https://www.npmjs.com/package/katex)（npm，MIT License；mhchem/auto-render 为其 dist 内 contrib） |
| 形态 | **自包含 UMD**——`katex.min.js`→`window.katex`、`auto-render.min.js`→`window.renderMathInElement`（读 `window.katex`，**须后于** katex 加载）、`mhchem.min.js` 载入后向 katex 注册 `\ce`/`\pu`（**须后于** katex）。无运行时动态 `import()`，离线自洽 |
| 安全 | `renderMathInElement` 选项硬编码 **`trust:false`**（KaTeX 默认值）——禁 `\href`/`\url`/`\includegraphics`/`\html*`，渲成错误色而非生效；**不传共享 `macros`** → auto-render 每次调用自造一份默认 `macros:{}`、**跨容器隔离**（决策P4.14-4，见 `math_enhance.js`） |

**为何 css 与 `fonts/` 须同级**（决策P4.14-5）：`katex.min.css` 内以**相对路径** `url(fonts/KaTeX_*.woff2)`
引字体，浏览器据 css 的 URL（`/static/vendor/katex/katex.min.css`）解析出 `/static/vendor/katex/fonts/…`，
故 css 与 `fonts/` 须同置 `vendor/katex/`。**只 vendored `.woff2`**（现代浏览器全支持；css 的 `@font-face`
首选 woff2，命中即不回退 woff/ttf，故无须 vendored 后两者、亦不会触发缺失请求）。字体经 `@font-face` 由浏览器
**同源**拉取、无运行时 JS 动态加载，断网照渲。

**字体清单 manifest**（20 枚 `.woff2`）：

```text
KaTeX_AMS-Regular  KaTeX_Caligraphic-{Bold,Regular}  KaTeX_Fraktur-{Bold,Regular}
KaTeX_Main-{Regular,Bold,Italic,BoldItalic}  KaTeX_Math-{Italic,BoldItalic}
KaTeX_SansSerif-{Regular,Bold,Italic}  KaTeX_Script-Regular
KaTeX_Size{1,2,3,4}-Regular  KaTeX_Typewriter-Regular
```

```bash
# 单文件校验（任一）：
shasum -a 256 guanlan/web/static/vendor/katex/fonts/KaTeX_Main-Regular.woff2
#   c2342cd8b869e01752a9321dc17213fc40d4d04c79688c1d43f2cf316abd7866
# 全 20 枚 manifest 校验（sha256 of 排序后的 "name sha" 行）：
( cd guanlan/web/static/vendor/katex && shasum -a 256 fonts/*.woff2 \
  | awk '{print $2" "$1}' | sort | shasum -a 256 )
#   167f257b6e878105500824aea4440992b84667987dcb7ebea3a12f682fa4f107
```

### 校验

```bash
cd guanlan/web/static/vendor/katex
shasum -a 256 katex.min.js katex.min.css contrib/mhchem.min.js contrib/auto-render.min.js
# 对照上表
```

### 升级步骤

1. `KV=<新版>; base=https://cdn.jsdelivr.net/npm/katex@$KV/dist` 重新 `curl` 上述四文件 + css 引用的全部
   `fonts/KaTeX_*.woff2`（以 `grep -oE 'fonts/KaTeX_[A-Za-z0-9_-]+\.woff2' katex.min.css | sort -u` 取清单）。
2. 确认 `katex.min.js`→`window.katex`、`auto-render.min.js`→`window.renderMathInElement`（UMD 头 `e.renderMathInElement=t(e.katex)`），
   `auto-render` 默认 `ignoredTags` 仍含 `pre/code/script/...`、默认 `macros` 仍是 `n.macros||{}`（每调用一份、跨容器隔离）。
3. 更新本表字节 + SHA256 + 字体 manifest；浏览器手测 §7 验收（**11/12 是安全闸运行时验证**：`\href`/`\htmlData` 不生效、宏不跨容器泄漏）。
4. 经显式 PR 复核（决策P4.14-2：KaTeX 历史 XSS 多发于 `trust:true`/旧版本，升级须复核 `trust:false` 无回归）。

---

## highlight/highlight.min.js（代码语法高亮，P4.14）

代码块高亮器。前端经 `static/code_enhance.js` 注入 `<script>` 加载、读 `window.hljs`、对
`pre>code.language-X` 调 `highlightElement`。设计见 §6 决策P4.14-1/-7。

| 项 | 值 |
|----|----|
| 版本 | `11.10.0` **common 构建**（钉版；升级走显式 PR 复核） |
| 上游 | https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.10.0/highlight.min.js |
| 包 | [highlight.js](https://www.npmjs.com/package/highlight.js)（npm，BSD-3-Clause License） |
| 字节 | 124980 |
| SHA256 | `471ef9ae90c407af440fcdc48edfeeb562106b3267bd12d99071c162fb52ed32` |
| 形态 | **自包含 UMD 单文件**——加载后 `window.hljs` 就绪，无动态 `import()`，离线自洽 |
| 安全 | v11 **默认即安全**：`highlightElement` 读 `.textContent`（已转义源）、产物**只**是 `<span class="hljs-*">转义文本</span>`、**剥一切未转义 HTML 并告警**（v11 移除 HTML 透传）。喂转义 textContent + **每块单次高亮**（`.hljs` 跳重）即合规（决策P4.14-4） |

**覆盖语言集（common 构建，36 种）**：`bash c cpp csharp css diff go graphql ini java javascript json
kotlin less lua makefile markdown objectivec perl php php-template plaintext python python-repl r ruby
rust scss shell sql swift typescript vbnet wasm xml yaml`。**不在此集者** → `code_enhance.js` 经
`hljs.getLanguage(lang)` 守门、**保留可读纯文本代码**（不猜、不报错、不动态拉子包破离线；**非静默**，
决策P4.14-7）。

### 校验

```bash
shasum -a 256 guanlan/web/static/vendor/highlight/highlight.min.js
# 应得 471ef9ae90c407af440fcdc48edfeeb562106b3267bd12d99071c162fb52ed32
```

### 升级步骤

1. `curl -o guanlan/web/static/vendor/highlight/highlight.min.js https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@<新版>/highlight.min.js`
2. 确认仍是 UMD 单文件（`window.hljs` 就绪）、无动态 `import(`；用 `node -e 'console.log(require("./highlight.min.js").listLanguages().sort().join(" "))'` 取覆盖语言集、更新上文。
3. 更新本表版本 + 字节 + SHA256 + 覆盖语言集。
4. 经显式 PR 复核（决策P4.14-4：highlight 历史 XSS 多发于 HTML 透传/旧版本，升级须复核 v11「输入须转义、输出全转义」契约无回归）。

---

## flint/echarts.js（flint 图表编译器，P4.20）

把 ` ```flint ` 块里的「数据 + 语义类型 + 紧凑图规格」**编译**成 ECharts option 的语义可视化中间语言。
前端经 `static/flint_enhance.js` 动态 `import()` 加载、读具名导出 `assembleECharts`。设计见
[`docs/P4.20-Web-flint图表渲染.md`](../../../../docs/P4.20-Web-flint图表渲染.md) §6 决策P4.20-1/-3/-4。

| 项 | 值 |
|----|----|
| 版本 | `0.4.1`（钉版；升级走显式 PR 复核，见决策P4.20-4） |
| 上游 | https://cdn.jsdelivr.net/npm/flint-chart@0.4.1/dist/echarts/index.js |
| 包 | [flint-chart](https://www.npmjs.com/package/flint-chart)（npm，MIT License；Microsoft Research + 人大 IDEAS Lab） |
| 字节 | 531443 |
| SHA256 | `fc3895f0e755fbd62accb3aa12477d1ffab6ae1979de1fb0556e6162f468734e` |
| 形态 | **自包含单文件 ESM**——**零 bare import、零动态 `import(`**，浏览器可直接 `import()`；具名导出 `assembleECharts` / `ecAllTemplateDefs` / `ecGetTemplateDef` / `ecGetTemplateChannels` / `ecTemplateDefs`。**不挂 `window`** |
| 安全 | flint 是**生成式白名单**：按 `chartType`/`encodings` 从模板**生成** option，**不 merge 用户私货**——实测三条走私路径（`chartProperties` 塞 `backgroundColor.image` / `graphic[type=image]`、顶层 `options` 塞带尖括号 `formatter`、`symbol:'image://…'`）产物与干净输入逐键相同。前端另有**入口白名单**（五个顶层键、拒 `data.url`）与**键域出口断言**兜底（决策P4.20-4/-5） |

**为何是本仓唯一走 ESM `import()` 的 vendored 资产**（决策P4.20-3）：flint-js 用 tsup 出 ESM+CJS，
**不出 UMD、不出 `.min`**；但其 `dist/echarts/index.js` 实测是自包含单文件，故 `import()` 即可，
**仍无 npm、无打包链**，决策P4-3 的「无构建」底线不破。连带后果是**升级校验步骤与其余三件不同**（见下）。

### 校验

```bash
shasum -a 256 guanlan/web/static/vendor/flint/echarts.js
# 应得 fc3895f0e755fbd62accb3aa12477d1ffab6ae1979de1fb0556e6162f468734e
```

### 升级步骤（**与 UMD 三件不同**）

1. `curl -o guanlan/web/static/vendor/flint/echarts.js https://cdn.jsdelivr.net/npm/flint-chart@<新版>/dist/echarts/index.js`
2. 确认**仍自包含**——不是 `tail` 看 UMD 尾巴，而是：

   ```bash
   cd guanlan/web/static/vendor/flint
   # 关键字与引号间**允许空白**：dist 不压缩，真有外部依赖时写的是 `from "echarts";`
   grep -oE '(from|require[[:space:]]*\()[[:space:]]*["'"'"'][a-zA-Z@]' echarts.js   # 应空（无 bare import / require）
   grep -c 'import(' echarts.js                              # 应为 0（无运行时动态 import）
   grep -oE 'export ?\{[^}]*\}' echarts.js | grep assembleECharts   # assembleECharts 仍为具名导出
   ```

3. 更新本表版本 + 字节 + SHA256；重跑 `flint-chart-author` 的图型/语义类型表生成命令
   （见 [`skills/flint-chart-author/references/flint-spec.md`](../../../../skills/flint-chart-author/references/flint-spec.md) §7），
   随附于同一 PR——**表不重跑就会与钉版漂移**。
4. 经显式 PR 复核（决策P4.20-4），复核项**固定为五条**：
   - 三条走私路径仍**零残留**（无 `image://` / 无 `http(s):` / 无 `formatter` / 无 `graphic`）；
   - 注入 payload（`<img src=x onerror=alert(1)>` 当类别名）仍落成**转义文本**；
   - **语义色常量仍只有 waterfall 一处**：`grep -nE '(startEnd|increase|decrease|positive|negative)[[:space:]]*:[[:space:]]*"#' echarts.js`
     —— 多出来的模板必须补进 `flint_enhance.js` 的 `FLINT_SEMANTIC_COLOR_CHARTS`，否则逐槽换色会改掉它的含义（决策P4.20-22）；
   - `chart_spec.canvasSize` 仍能让 flint **重新布局**（而不仅是缩画布）——按栏宽重编译靠这条（决策P4.20-19）；
   - `baseSize` 仍**不被上游钳制**（若上游开始钳制，尺寸闸不必撤、但复核结论要更新）；
   - **37 个图型逐个走一遍前端整链**（入口白名单 → 编译 → 尺寸闸 → 出口断言），确认没有新图型被
     `FLINT_BAD_KEYS` / `bad-renderItem` / `not-echarts-option` **误杀**（决策P4.20-16：这三条正是
     首版实测揪出 `Waterfall`/`Sankey`/`Network Graph`/`Sunburst` 四例误杀的地方）。

---

## echarts.min.js（图表渲染后端，P4.20）

flint 编译产物的渲染后端。前端经 `static/flint_enhance.js` 注入 `<script>` 加载、读 `window.echarts`，
以 `init(dom, 昼澜主题, {renderer:'svg'})` + `setOption({… tooltip.renderMode:'richText'})` 渲染。

| 项 | 值 |
|----|----|
| 版本 | `6.1.0`（钉版；升级走显式 PR 复核） |
| 上游 | https://cdn.jsdelivr.net/npm/echarts@6.1.0/dist/echarts.min.js |
| 包 | [echarts](https://www.npmjs.com/package/echarts)（npm，Apache-2.0 License） |
| 字节 | 1121883 |
| SHA256 | `b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0` |
| 形态 | **自包含 UMD 单文件**——加载后 `window.echarts` 就绪（尾部 `t.version="6.1.0"`），**零动态 `import(`**，离线自洽 |
| 注意 | **浏览器端的 SVG 渲染器不写 `viewBox`**（只有 SSR 的 `renderToSVGString` 写），且它把 `<svg>` 以 `position:absolute` 放进固定尺寸容器 —— 故 `max-width:100%` 缩不动它，自适应宽靠**渲染前定尺寸**（决策P4.20-17/-19） |
| 安全 | 渲染姿态硬编码两条：`renderer:'svg'`（产物是 SVG DOM，非 canvas）+ `tooltip.renderMode:'richText'`（tooltip 作为**图元**绘制、不走 HTML 字符串拼接）。ECharts 历史 XSS 多发于 HTML tooltip + 自定义 formatter，本相位把这条路**整条关掉**（决策P4.20-4） |

**为何取 ECharts 而非 Vega-Lite / Chart.js**（决策P4.20-2）：单文件自包含 UMD、SVG 产物（可缩放矢量、
无需 `ResizeObserver`）、时间轴原生、`renderMode:'richText'` 提供一个「tooltip 彻底不走 HTML」
的**单旗铰链**。Vega-Lite 要四个文件且需 `ast:true`+解释器才免 `new Function`；Chart.js 时间轴要再配 date
adapter 且只有 canvas。**只 vendored 一个后端**——多一个 = 多一份体积 + 多一片安全面。

### 校验

```bash
shasum -a 256 guanlan/web/static/vendor/echarts.min.js
# 应得 b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0
```

### 升级步骤

1. `curl -o guanlan/web/static/vendor/echarts.min.js https://cdn.jsdelivr.net/npm/echarts@<新版>/dist/echarts.min.js`
2. 确认仍是自包含 UMD（`tail` 见 `t.version="<新版>"` + `Object.defineProperty(t,"__esModule"…)`）、
   无新增动态 `import(`（`grep -c '[^a-zA-Z.]import(' …` 应为 0）。
3. 更新本表版本 + 字节 + SHA256。
4. 经显式 PR 复核：`init` 仍接受**主题对象**作第二参、且主题 schema 的**五档轴**
   （`categoryAxis`/`valueAxis`/`timeAxis`/`logAxis`/`singleAxis`）键名未变（决策P4.20-13/-23 靠这条）；
   `renderMode:'richText'` 仍不解析 HTML（故 flint 的 HTML formatter 仍须压成纯文本，决策P4.20-25）；
   离树元素 + 显式 `width/height` 仍可 `init`（决策P4.20-15 的事务式替换靠这条）。
