"use strict";
// 载入顺序见 index.html：须在 content_enhance.js（编排器）**之前**载入，供其调用全局 enhanceFlint()。
// 把 ```flint 围栏块（fenced_code emit 的 pre>code.language-flint，服务端零改、规格为转义 JSON 文本）
// 在浏览器内**编译成 ECharts option 并渲染成 SVG 图表**。设计见 docs/P4.20-Web-flint图表渲染.md。
//
// 安全姿态（决策P4.20-4/-5）：**入口白名单 + flint 生成式编译 + 键域出口断言 + svg/richText + 钉版 +
// 失败保留源码**。三点与前三类渲染器不同：①块内容只经 JSON.parse、**JSON 里没有函数**，用户造不出
// ECharts 的回调；②只放行五个顶层键、`data` 只留 `values`（**拒 data.url**，渲染器永不发起网络请求）；
// ③出口断言**按键域不按值域**——合法数据里就可能有 URL 串，扫值域会把正常图整张否掉。
// **不承诺产物绝对可信**；任何失败一律退回 <pre> 源码 + 徽标。
//
// 资源姿态（决策P4.20-6）：块源 64 K **字符**、data.values 1000 行、画布尺寸有限且不过大。超限不渲染、
// 保留源码 + 「数据过大」徽标，**不静默截断**（截断出的图会说谎，比裸 JSON 更坏）。同一条红线也适用于
// **flint 自己的截断**：它超预算时会悄悄丢掉整批类别/分面，那份 `_warnings` 必须被读、不能随手删掉
// （决策P4.20-18）。
//
// 改写 flint 产物时的总原则（决策P4.20-19，评审后收紧）：**能让 flint 自己算的，绝不在事后改**。
// flint 把图例/分面/内边距按它自己算出的 `_width` 烤成**绝对像素**，事后改画布尺寸会把图例挤出视口；
// 它的色板槽位还可能承载**语义**（涨/跌）。故本文件只做三类事后改写，且每一类都限定得能讲清楚：
// ①色板按 `opt.color` **自身**逐槽换色（语义色图型整张跳过）；②tooltip 的 HTML 串压成纯文本；
// ③补 `viewBox`。尺寸这件事**回到 flint 手里**（`chart_spec.canvasSize` 重编译）。

// —— 资源闸常量（决策P4.20-6）——
const FLINT_MAX_CHARS = 64 * 1024; // 围栏块源上限，**字符数非字节**（src.length 是 UTF-16 码元；
                                   // CJK 一字符 3 字节，叫 BYTES 会让人以为闸在 64 KB、实则放行约 192 KB）
const FLINT_MAX_ROWS = 1000;       // data.values 行上限（代价按图元数走：实测散点 1000 行 ≈ 1050 个 SVG 节点）
// 画布**上界**（决策P4.20-20）：闸子只防「大到打死浏览器」。**故意没有下界**——一张小图不消耗资源，
// 而 flint 按语义定尺寸时小图很常见（实测两周的 Calendar Heatmap 只有 114px 宽）。曾经的 minW/minH
// 把这类合法小图判成「数据过大」，还叫作者去删数据——那只会让画布更小、永远修不好。
const FLINT_SIZE = { maxW: 1600, maxH: 1200 };
// 按栏宽重编译时逐档收窄的比例（决策P4.20-19）：flint 的 canvasSize 是**布局提示**不是硬钳位
// （实测请求 377 得 492、请求 260 得 375），故取第一个真正 ≤ 栏宽的档；全不中就用自然尺寸 + 横向滚动。
const FLINT_FIT_RATIOS = [1, 0.75, 0.55, 0.4];

// —— 安全闸常量（决策P4.20-4）——
const FLINT_TOP_KEYS = ["data", "semantic_types", "chart_spec", "field_display_names", "options"];
// 注：`field_display_names` 留在白名单里只为**向前兼容**——flint 0.4.1 的 dist 里**根本没有这个键**
// （实测 grep 计数为 0，assembleECharts 只读 chart_spec/semantic_types/data.values/options），
// 写了等于没写。作者侧文档已据此改口，不再教人用它（决策P4.20-21）。
// 危险**键**清单：值一律被 ECharts 当文本画进 <text>，是**键**才决定它是不是链接/图片/HTML。
// **不含 `target`**（决策P4.20-16）：ECharts 的 `target` 只在与 `link` 配对时才有意义（`'blank'`/`'self'`），
// 而 `link` 已被无条件拒——单独的 `target` 是惰性的。实测 `Sankey Diagram`/`Network Graph` 的
// `series.links[].target` 是**节点名**（`{source,target,value}`），禁它等于禁掉这两个图型。
// **不含 `renderItem`**：它另走「必须是函数」的规则（见 assertSafeOption）。
const FLINT_BAD_KEYS = new Set(["link", "toolbox", "dataView"]);

// 色板槽位**承载语义**的图型（决策P4.20-22）：这些模板把颜色当含义用，逐槽换色会把含义改掉。
// 名单由**对 vendored dist 做一次穷举**得出——搜「非色板数组里的裸 hex 语义常量」，`flint-chart@0.4.1`
// 全包**只有一处**：`const COLOR = { startEnd: "#5470c6", increase: "#91cc75", decrease: "#ee6666" }`
// （waterfall 模板）。曾经的按值替换把 `#91cc75`(涨) 换成澜青、`#ee6666`(跌) 换成海沫**绿**——
// 把「跌」画成绿的，比配色不统一坏得多。升级钉版时**必须重跑那次 grep**（见 vendor/README.md）。
const FLINT_SEMANTIC_COLOR_CHARTS = new Set(["Waterfall Chart"]);

// 昼澜主题（决策P4.13-5 沿用 / 决策P4.20-13）：取值为 app.css :root 既有昼澜调色（注释标源变量），
// 使图与界面同水墨澜色调；仅一档常量、不预留切换、不 registerTheme 注册全局主题。
// 经 echarts.init(dom, THEME, opts) **第二参**传入——ECharts 主题 schema 用的是**按轴类型分档**的
// categoryAxis/valueAxis/timeAxis/logAxis/singleAxis，与 flint 产物的 xAxis/yAxis 天然不撞键，
// 故不必自己深合并、也不会盖掉 flint 算好的轴字段与刻度。**五档必须齐**（决策P4.20-23）：
// 只给前两档时，flint 对 temporal x 产出的 `xAxis.type:"time"` 会走 theme.timeAxis 这一空档、
// 退回 ECharts 原厂灰黑，于是同一张图两条轴不是一套色（Streamgraph 的 singleAxis 同理）。
const GUANLAN_FLINT_AXIS = {
  axisLine: { lineStyle: { color: "#D2E0E3" } },  // --lan-mist：轴线
  axisTick: { lineStyle: { color: "#D2E0E3" } },
  axisLabel: { color: "#52666B" },                // --lan-ink-soft：刻度文字
  splitLine: { lineStyle: { color: "#EEF3F4" } }, // --lan-paper：网格
};
const GUANLAN_FLINT_THEME = {
  backgroundColor: "transparent",
  // 图内文字：--lan-ink + 与 body 同一套字体栈（ECharts SVG 渲染器把它写成 `style="font-family:…"`
  // 的**独立属性**而非 `font:` 简写，故多字体栈安全；不用 "inherit"——那在 font 简写里是非法值）。
  textStyle: { color: "#1B2A2E", fontFamily: 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif' },
  title: { textStyle: { color: "#0F4C5C" } }, // --lan-deep
  legend: { textStyle: { color: "#52666B" } }, // --lan-ink-soft
  categoryAxis: GUANLAN_FLINT_AXIS,
  valueAxis: GUANLAN_FLINT_AXIS,
  timeAxis: GUANLAN_FLINT_AXIS,   // flint 对 temporal x 产出 xAxis.type:"time"，走的是这一档
  logAxis: GUANLAN_FLINT_AXIS,
  singleAxis: GUANLAN_FLINT_AXIS, // Streamgraph
};

// 序列色板：由浅及深的水色 + 几抹暖砂（同 app.css 的 .hljs 数字色），相邻两档刻意拉开明度以便区分。
// **20 档**（决策P4.20-24）：flint 的类别数 > 10 时会从 cat10 切到 **cat20**，只备 10 档会让第 11 个
// 序列之后原样留着 ECharts 原厂的橄榄/芥末/灰——一半图例昼澜、一半原厂，正是要避免的那种混搭。
const GUANLAN_FLINT_PALETTE = [
  "#0F4C5C", // --lan-deep：深澜
  "#3F9DB3", // --lan-ripple：澜纹青
  "#D3B78A", // 暖砂：深浅澜之间的暖调对比
  "#7FBFB4", // 海沫绿
  "#8FD0DE", // 亮澜青
  "#06303B", // --lan-abyss：渊面
  "#A98F6B", // 深砂
  "#5A8792", // 雾澜灰青
  "#B9D9DD", // 浅澜
  "#2C6E7F", // 中澜
  "#1E6472", // 墨澜
  "#63B2C4", // 淡澜青
  "#C4A176", // 陈砂
  "#96CFC6", // 浅沫绿
  "#A8DDE8", // 霜澜
  "#0A3D49", // 深渊澜
  "#8C7350", // 褐砂
  "#7899A2", // 灰澜
  "#D6E7EA", // 雾白澜
  "#3A8090", // 深涟青
];

// 逐槽换色（决策P4.20-24）：映射表**从 `opt.color` 自身现造**，不硬编码 flint 的色板常量——
// 这样 cat10 / cat20 / 平行坐标那套变体（第 10 槽与 cat10 不同）全都自动覆盖，也不会随上游升级漂移。
// 只在**颜色键**上按精确串替换：既保住 flint 算好的「谁配哪一档」的配色分派，又不可能碰到数据文本
// （类别名/标签走的是别的键）。非色板里的颜色（渐变、连续色映射、语义色）原样不动。
const FLINT_COLOR_KEYS = new Set(["color", "borderColor", "shadowColor", "backgroundColor", "areaColor"]);

function applyGuanlanPalette(opt, chartType) {
  if (FLINT_SEMANTIC_COLOR_CHARTS.has(chartType)) return; // 语义色图型整张跳过（决策P4.20-22）
  const source = Array.isArray(opt.color) ? opt.color : null;
  if (!source || !source.length) return;
  const map = new Map();
  source.forEach((c, i) => {
    if (typeof c === "string") map.set(c.toLowerCase(), GUANLAN_FLINT_PALETTE[i % GUANLAN_FLINT_PALETTE.length]);
  });
  (function walk(node, inColorKey) {
    if (Array.isArray(node)) {
      for (let i = 0; i < node.length; i++) {
        const v = node[i];
        if (inColorKey && typeof v === "string" && map.has(v.toLowerCase())) node[i] = map.get(v.toLowerCase());
        else walk(v, inColorKey);
      }
      return;
    }
    if (!node || typeof node !== "object") return;
    for (const [k, v] of Object.entries(node)) {
      const isColorKey = FLINT_COLOR_KEYS.has(k);
      if (isColorKey && typeof v === "string" && map.has(v.toLowerCase())) node[k] = map.get(v.toLowerCase());
      else walk(v, isColorKey);
    }
  })(opt, false);
}

// tooltip / 标签的 formatter 压成纯文本（决策P4.20-25）。
// 为何必须做：flint 给几乎每张图装的是一个**函数** formatter，返回的是 HTML 串（`<br/>` 分行，
// 部分模板还带 `<b>`/`<span style=…>`）。而本相位把 tooltip 钉在 `renderMode:'richText'`——
// richText **不解析 HTML**，于是标签被原样画成文字：hover 一张最普通的柱图会看到
// 「模型: model-a<br/>评测得分: 72.4」这一行字面量。richText 这条安全铰链不能撤（决策P4.20-4），
// 所以改的是**喂给它的东西**：把 `<br/>` 变成换行（richText 认 `\n`）、其余标签剥成文本。
// 这一步纯属呈现修正，**不放宽任何安全性质**——产物仍然只作为文本绘制，全程没有 HTML 解析。
function htmlToPlainText(s) {
  return String(s)
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(?:p|div|li|tr)>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'")
    .replace(/&amp;/g, "&"); // 最后解 &amp;，免得把 `&amp;lt;` 二次解成 `<`
}

function plainTextFormatters(node) {
  if (Array.isArray(node)) return node.forEach(plainTextFormatters);
  if (!node || typeof node !== "object") return;
  for (const [k, v] of Object.entries(node)) {
    if (k === "formatter" && typeof v === "function") {
      node[k] = function (...args) {
        const out = v.apply(this, args);
        return typeof out === "string" ? htmlToPlainText(out) : out; // 非字符串（富文本对象等）原样放行
      };
      continue;
    }
    plainTextFormatters(v);
  }
}

// —— 实例登记簿（决策P4.20-7）——
// ECharts 把实例挂在自己的**全局注册表**上，而 paintPage（切语言）/ staging paint()（切 md↔源码）
// 会重设整页 innerHTML 使旧 <figure> 脱树——不 dispose 就是每切一次泄漏一份实例（含其事件与定时器）。
// **flint+ECharts 是前端第一个有状态渲染器**：mermaid/KaTeX/hljs 都是一次性 DOM 变换、无此义务。
const _flintCharts = new Set();

function sweepFlintCharts() {
  for (const c of [..._flintCharts]) {
    let dom = null;
    try { dom = c.getDom && c.getDom(); } catch { /* 已 dispose → 当作脱树 */ }
    if (!dom || !dom.isConnected) {
      try { c.dispose(); } catch { /* 已 dispose → 忽略 */ }
      _flintCharts.delete(c);
    }
  }
}

// —— 懒加载：两个运行时并发、各自单例、失败可重试（沿 P4.13/P4.14 范式）——
// ECharts 走注入 <script> 读 window.echarts（同 mermaid 姿态）；flint dist 是**自包含单文件 ESM**
// （不出 UMD），走动态 import()——**仍无 npm、无打包链**，决策P4-3 的「无构建」底线不破（决策P4.20-3）。
let _echartsPromise = null;
function loadECharts() {
  if (_echartsPromise) return _echartsPromise;
  _echartsPromise = new Promise((resolve, reject) => {
    if (window.echarts) return resolve(window.echarts); // 容错：已被别处加载
    const s = document.createElement("script");
    s.src = "/static/vendor/echarts.min.js";
    s.onload = () => (window.echarts ? resolve(window.echarts) : (s.remove(), reject(new Error("no echarts"))));
    // 失败即移除本 <script>：配合失败不缓存 promise，让重试从干净状态重来、不在 <head> 堆死标签。
    s.onerror = () => { s.remove(); reject(new Error("echarts 运行时加载失败")); };
    document.head.appendChild(s);
  }).catch((e) => { _echartsPromise = null; throw e; });
  return _echartsPromise;
}

// 重试计数（决策P4.20-26）：**光把 promise 置空不够**——按 HTML 的 module map 语义，一个失败的
// 动态 import() 会把该 URL 的失败结果留在文档存活期内，重发同一个 URL 会**立刻**再 reject、根本不重取。
// （注入 <script> 的那三个渲染器没这问题，它们每次新造一个标签。）故重试时换个 query，
// 让它成为 module map 里的新条目、真的回服务端取一次；StaticFiles 会忽略这个 query。
let _flintImportAttempt = 0;
let _flintPromise = null;
function loadFlint() {
  if (_flintPromise) return _flintPromise;
  const bust = _flintImportAttempt ? `?retry=${_flintImportAttempt}` : "";
  _flintPromise = Promise.all([
    import(`/static/vendor/flint/echarts.js${bust}`), // 自包含单文件 ESM → 具名导出，不挂 window
    loadECharts(),
  ])
    .then(([flint, echarts]) => {
      if (typeof flint.assembleECharts !== "function") throw new Error("no assembleECharts");
      return { assemble: flint.assembleECharts, echarts };
    })
    .catch((e) => { _flintPromise = null; _flintImportAttempt++; throw e; }); // 下次 enhanceFlint 可真重试
  return _flintPromise;
}

// —— 失败分档（决策P4.20-20）——
// 三种终态各自一句人话：tooLarge（资源闸）/ truncated（flint 丢了数据）/ renderFail（其余一切）。
// 用显式的 `flintKind` 而非 `instanceof RangeError`——后者曾把「画布非有限数」和「画布过小」
// 一并说成「数据过大」，把作者引向删数据这条修不好的路。
function flintError(kind, message) {
  const e = new Error(message);
  e.flintKind = kind;
  return e;
}

// —— 入口白名单（决策P4.20-4/-5）：只放行五个顶层键；拒 data.url，只认内联 data.values ——
function sanitizeFlintInput(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("bad-input");
  const inp = {};
  for (const k of FLINT_TOP_KEYS) if (k in raw) inp[k] = raw[k];
  const values = inp.data && inp.data.values;
  if (!Array.isArray(values) || !values.length) throw new Error("no-inline-values"); // data.url / 远端引用一律拒
  if (values.length > FLINT_MAX_ROWS) throw flintError("tooLarge", "too-many-rows");
  inp.data = { values }; // data 只留 values，其余键（url/format/…）丢弃
  // 列名存在性（决策P4.20-14 上半）：flint 对不存在的列**不抛错**，静默产出空 series → 画出一张空图。
  // 列集取**全表并集**而非 values[0]：结构化库出来的行常常列不齐（NULL 列被整行省略），
  // 只看首行会把合法图误判成 unknown-field。行数已 ≤ 1000，全表并集是廉价的。
  const cols = new Set();
  for (const row of values) if (row && typeof row === "object") for (const k of Object.keys(row)) cols.add(k);
  for (const enc of Object.values((inp.chart_spec || {}).encodings || {})) {
    const f = typeof enc === "string" ? enc : enc && enc.field;
    if (f && !cols.has(f)) throw new Error("unknown-field");
  }
  return inp;
}

// —— 画布尺寸闸（决策P4.20-6/-20）：只有上界 + 有限性 ——
// flint 对 baseSize **不做任何钳制**（实测 {width:1e8} 直通 _width=100000123、{width:-5,height:NaN}
// 直通 NaN），故这两条必须自己守：NaN 会让 echarts.init 拿到脏尺寸，1e8 会当场打死浏览器。
// **没有下界**：小画布不消耗资源，拒它只会误伤按语义定尺寸的小图（见 FLINT_SIZE 注释）。
function checkedSize(w, h) {
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) throw new Error("bad-size"); // → renderFail
  if (w > FLINT_SIZE.maxW || h > FLINT_SIZE.maxH) throw flintError("tooLarge", "size-too-large");
  return [w, h];
}

// —— 按栏宽重编译（决策P4.20-19）——
// **不能**编译完再改画布尺寸：flint 把 `legend.left`/`graphic.left`/`grid` 按它自己算出的 `_width`
// 烤成**绝对像素**（实测一张带 color 通道的折线图 _width=562、legend.left=464），事后把画布改成 377
// 会让整个图例画在视口之外、而 grid.right 仍占着位——图看着还在，读者却没法分辨哪条线是哪个模型。
// 正解是把尺寸这件事**交还给 flint**：拿 `chart_spec.canvasSize` 逐档收窄重编译，取第一个真正 ≤ 栏宽
// 的结果（实测每一档产物内部都自洽：legend.left 始终 < _width）。栏太窄时**放弃收窄**、用自然尺寸，
// 由 .flint-rendered 的 overflow-x 横向滚动兜底——宁可滚动，也不要一张图例被裁掉的图。
function assembleFitted(assemble, input, avail) {
  const base = assemble(input);
  if (!avail || !base || typeof base !== "object" || !Number.isFinite(base._width) || base._width <= avail) return base;
  const ratio = base._height / base._width;
  for (const r of FLINT_FIT_RATIOS) {
    const width = Math.round(avail * r);
    if (width < 1) break;
    const canvasSize = { width, height: Math.max(1, Math.round(width * ratio)) };
    let candidate;
    try {
      candidate = assemble({ ...input, chart_spec: { ...(input.chart_spec || {}), canvasSize } });
    } catch {
      break; // 收窄这条路走不通就用自然尺寸，别把一张本来能画的图弄丢
    }
    if (candidate && Number.isFinite(candidate._width) && candidate._width <= avail) return candidate;
  }
  return base;
}

// 待替换的 `<pre>` 是块级元素、正好铺满所在栏，故它的边框盒宽就是 <figure> 换上去会拿到的宽度。
// 不量父容器 clientWidth：那含父级 padding（实测 415 vs 真实可用 377）。
function columnWidth(code) {
  const pre = code.parentElement;
  return pre ? Math.floor(pre.getBoundingClientRect().width) : 0;
}

// —— 出口断言（决策P4.20-4）：**按键域**查已知危险键，不扫值域 ——
// 为何不扫值域：合法数据里就可能有 URL 串（AI/LLM 语境把模型卡/论文链接当类别名很常见），实测这类串
// 会原样进入产物 → 一条 /https?:/ 会把一张完全正常的图整张否掉。**危险的从来是键不是值。**
// 这是为**版本漂移**留的守门（钉版之外的运行时铰链），不是在补现存漏洞——官方安全指南列的
// title.link / data.link / toolbox.dataView / dataset.transform 实测在 flint 生成式管线上当前不可达。
// **不要把它扩写成通用 ECharts option 消毒器**：那是 MathJax 式的脆弱黑名单，决策P4.14-2 已否过一次。
function assertSafeOption(opt) {
  (function walk(node) {
    if (Array.isArray(node)) return node.forEach(walk);
    if (!node || typeof node !== "object") return;
    for (const [k, v] of Object.entries(node)) {
      if (FLINT_BAD_KEYS.has(k)) throw new Error("bad-key:" + k); // link/toolbox/dataView
      if (k === "image") throw new Error("image-ref"); // graphic/背景的图片引用
      // renderItem 必须是**函数**（决策P4.20-16）：实测 `Waterfall Chart` 走 `series.type:'custom'`、
      // 由 flint **自己**造一个 renderItem 函数——那是已钉版的 vendored 代码，不是用户可控物
      // （JSON 里造不出函数，flint 也不 eval 字符串）。反过来，一个**字符串** renderItem 只可能来自
      // 版本漂移或注入，一律拒。同理下面的 formatter 只查字符串形态、不碰 flint 自造的函数。
      if (k === "renderItem" && typeof v !== "function") throw new Error("bad-renderItem");
      if (k === "symbol" && typeof v === "string" && v.startsWith("image://")) throw new Error("image-symbol");
      if (k === "formatter" && typeof v === "string" && /[<>]/.test(v)) throw new Error("html-formatter");
      walk(v);
    }
  })(opt);
  for (const g of opt.graphic || []) if (!g || g.type !== "text") throw new Error("non-text-graphic");
  // 空图守门（决策P4.20-14 下半）：列名全对、也不抛错，某些图型仍产出空 series——实测
  // Treemap / Sunburst Chart / Tree 只给 detail+size 时 series[0].data === undefined。
  const series = [].concat(opt.series || []);
  if (!series.length || !series.some((s) => s && (Array.isArray(s.data) ? s.data.length : s.data != null)))
    throw new Error("empty-series");
}

// —— flint 自己的截断也不许静默（决策P4.20-18）——
// flint 的 filterOverflow 在类别/分面数超预算时会**整批丢掉**取值，并记进 `ecOption._warnings`
// （`code:"overflow"`）。它只对 x/y 轴画出可见的「...N items omitted」占位；**color / column / row
// 三个通道是无标记地丢**——12 个地区的分面图只画 6 个，读者看到的是一张「看起来完整」的图。
// 那正是决策P4.20-6 拒绝静默截断的同一件事，只不过截断发生在 flint 里而不是我们这儿。
// 故：产物剥 `_` 私有键**之前**先读它，见到 overflow 就不渲染、保留源码 + 「数据被截断」徽标。
// 其余 severity:"info" 的提示（如选项标签被归一）不丢数据，放行。
function assertNoSilentTruncation(warnings) {
  if (!Array.isArray(warnings)) return;
  if (warnings.some((w) => w && w.code === "overflow"))
    throw flintError("truncated", "flint-overflow");
}

// 给一个失败的 flint 块标错误徽标（保留 <pre> 源码、不替换）。文案 textContent 注入防注入；幂等。
// 三种文案分开（决策P4.20-6/-10/-18）：用户要能分辨「图画不出来」「数据太大所以没画」
// 「数据被 flint 丢了一部分所以没画」——修法完全不同。
// 入参是**枚举串**而非 i18n key：三个 t() 调用都写成字面量，key 集合才静态可查（决策P4.7-8——
// `test_web_i18n.py` 机械断言全前端的 t() 首参均为字面量；传变量会让漏翻译在扫描里隐形）。
function markFlintFailed(code, kind) {
  const pre = code.parentElement;
  if (!pre || !pre.parentNode) return;
  const prev = pre.previousElementSibling;
  if (prev && prev.classList && prev.classList.contains("flint-error")) return; // 已标过 → 幂等
  const badge = document.createElement("div");
  badge.className = "flint-error";
  let text;
  if (kind === "tooLarge") text = window.t ? window.t("flint.tooLarge") : "数据过大，未渲染（已保留源码）";
  else if (kind === "truncated") text = window.t ? window.t("flint.truncated") : "数据超出图表容量、已被截断，未渲染（已保留源码）";
  else text = window.t ? window.t("flint.renderFail") : "图表渲染失败（已保留源码）";
  badge.textContent = text;
  pre.parentNode.insertBefore(badge, pre);
}

// 把 container 内的 ```flint 围栏块编译渲染成图就地替换。**须经 enhanceContent 在每个 innerHTML
// 注入点之后调用**（决策P4.14-9 白拿七个注入点）。幂等可重入：替换后 code 已离树，重复调不二次渲染。
async function enhanceFlint(container) {
  if (!container) return;
  sweepFlintCharts(); // 决策P4.20-7：先清扫脱树实例（放在早退**之前**——切到无图页也要回收）
  const blocks = container.querySelectorAll("pre > code.language-flint");
  if (!blocks.length) return; // 早退：无图块零加载这 1.65 MB（懒，决策P4.20-1）
  let rt;
  try {
    rt = await loadFlint();
  } catch {
    blocks.forEach((c) => markFlintFailed(c, "renderFail")); // 运行时加载失败 → 源码留存 + 徽标
    return;
  }
  for (const code of blocks) {
    if (!code.isConnected) continue; // await 期间可能被重绘替换/移除
    const src = code.textContent; // JSON 文本（浏览器已反转义），非 HTML
    try {
      if (src.length > FLINT_MAX_CHARS) throw flintError("tooLarge", "too-big");
      const input = sanitizeFlintInput(JSON.parse(src));
      // 编译：尺寸这件事交还给 flint（决策P4.20-19），产物内部自洽、图例不会被挤出视口。
      const opt = assembleFitted(rt.assemble, input, columnWidth(code));
      // 产物形态守门（决策P4.20-16）：某些「图型 × 编码」组合下 flint **不装配成 ECharts option**，
      // 而是静默回吐它自己的 Vega-Lite 形中间态（`{mark, encoding}`，无 `series`/`_width`）——
      // 实测 `Sunburst Chart` 缺 `color` 通道时即如此。这一条把它落到**诚实的**「渲染失败」文案上，
      // 而不是让尺寸闸把它误报成「数据过大」（那会把作者引向删数据这条错路）。
      if (!opt || typeof opt !== "object" || !("series" in opt)) throw new Error("not-echarts-option");
      const [w, h] = checkedSize(opt._width, opt._height);
      assertNoSilentTruncation(opt._warnings); // 决策P4.20-18：读完 `_warnings` 再剥 `_` 私有键
      for (const k of Object.keys(opt)) if (k.startsWith("_")) delete opt[k]; // 剥私有键（_width/_transform/_pivot…）
      assertSafeOption(opt);
      applyGuanlanPalette(opt, (input.chart_spec || {}).chartType); // 昼澜逐槽换色（决策P4.20-13/-22/-24）
      plainTextFormatters(opt); // flint 的 HTML tooltip 压成纯文本（决策P4.20-25）
      // 先在**离树**的临时 figure 上 init + setOption，成功了才替换 <pre>（决策P4.20-15）。
      // 反过来做会漏一条失败路径：setOption 抛错时 <pre> 已脱树，markFlintFailed 拿到的 code
      // 其 parentElement.parentNode 为 null → 既贴不上徽标也回不去源码，页面留一个空 figure。
      // init 允许离树元素的前提是**显式给 width/height**——这里正好给了（已过尺寸闸）。
      const fig = document.createElement("figure");
      fig.className = "flint-rendered";
      const chart = rt.echarts.init(fig, GUANLAN_FLINT_THEME, { renderer: "svg", width: w, height: h });
      try {
        // renderMode:'richText' → tooltip 作为图元绘制、**不走 HTML 字符串拼接**（ECharts 历史 XSS
        // 多发于 HTML tooltip + 自定义 formatter，本相位把这条路整条关掉，决策P4.20-4）。
        // 喂进去的 formatter 已被 plainTextFormatters 压成纯文本，故 richText 不会画出字面标签。
        chart.setOption({ ...opt, tooltip: { ...(opt.tooltip || {}), renderMode: "richText" } });
      } catch (e) {
        chart.dispose(); // 临时实例不泄漏，源码仍原样挂在树上
        throw e;
      }
      // 补 viewBox（决策P4.20-17）：ECharts **浏览器端**的 SVG 渲染器只写死 width/height 属性、**不写
      // viewBox**（只有 SSR 的 renderToSVGString 会写）。补上它，窄栏下 CSS 至少能等比收住溢出部分；
      // 真正让图落进栏里的是上面的 assembleFitted，这一行只是兜底。
      const svg = fig.querySelector("svg");
      if (svg && !svg.getAttribute("viewBox")) svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
      code.parentElement.replaceWith(fig); // 只有全程成功才动 DOM
      _flintCharts.add(chart);
    } catch (e) {
      // 分档见 flintError：tooLarge（资源闸）/ truncated（flint 丢了数据）/ renderFail（其余一切）。
      // 三者都保留 <pre> 源码（决策P4.20-10：永不空白、零未消毒注入），只是文案不同。
      markFlintFailed(code, (e && e.flintKind) || "renderFail");
    }
  }
}
window.enhanceFlint = enhanceFlint;
