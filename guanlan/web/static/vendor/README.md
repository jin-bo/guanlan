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
