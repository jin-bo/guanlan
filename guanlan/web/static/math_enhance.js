"use strict";
// 载入顺序见 index.html：须在 content_enhance.js（编排器）**之前**载入，供其调用全局 typesetMath()。
// 把页面里的数学公式（$…$ / $$…$$ / \(…\) / \[…\]）与化学表达式（mhchem \ce{}/\pu{}，**须置于数学
// 分隔符内**如 $\ce{H2O}$）在浏览器内排版成富呈现。**新机制：DOM 文本走查**——数学无围栏载体，
// $…$ 作普通文本穿过 render.py、落成转义文本节点，KaTeX auto-render 遍历容器文本节点按分隔符就地排版
// （非靠某个 class，决策P4.14-3）。设计见 docs/P4.14-Web数学化学代码渲染.md §3.2。
//
// 安全姿态（决策P4.14-4 信任/隔离铰链）：硬编码 trust:false（KaTeX 默认即此）禁 \href/\url/
// \includegraphics/\html*（渲成错误色而非生效）；不传共享 macros → auto-render 每次调用自造一份默认
// macros:{}、**跨容器（页/气泡/预览）隔离**，\gdef/\newcommand 不跨容器泄漏。**不承诺产物绝对可信**
// （钉版 + 失败保留源码兜底，§4）。auto-render 读的是文本节点内容（TeX 串），从不当 HTML 解析。

const KATEX_OPTS = { // 硬编码、无放宽旋钮（决策P4.14-4）
  delimiters: [ // 块级在前（$$ 须先于 $ 匹配）
    { left: "$$", right: "$$", display: true },
    { left: "\\[", right: "\\]", display: true },
    { left: "$", right: "$", display: false },
    { left: "\\(", right: "\\)", display: false },
  ],
  ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option"], // 代码块内 $ 不排版、保字面
  ignoredClasses: ["page-meta"], // 跳过 chrome：wiki.js paintPage 把 .page-meta（页路径/更新时间）与正文同放
                                  // view 内（wiki.js），auto-render 扫文本节点会连 chrome 一起扫——令其跳过（P2 修）
  trust: false,        // 安全铰链（KaTeX 默认即此，显式写明）：禁 \href/\url/\includegraphics/\html*（§4）
  strict: "ignore",    // 仅静默告警，非安全项
  throwOnError: false, // 语法错 → 退回错误色源码、不抛（决策P4.14-8）
  // **不设 macros** → auto-render 每次调用自造一份默认 macros:{}，**跨容器（页/气泡/预览）隔离**；
  // 容器内多公式共用那一份 {}，故页内 \gdef 可生效（页内局部 preamble，benign、受 maxExpand 限），
  // 但**不跨容器泄漏**——这正是「页面自洽」要守的边界（决策P4.14-4 隔离铰链，§4-③）。
};

// 返回 Promise 的注入 <script> 小工具：onload→resolve、onerror→remove+reject（同 mermaid_enhance.js 注入范式）。
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => resolve();
    s.onerror = () => { s.remove(); reject(new Error(src + " 加载失败")); };
    document.head.appendChild(s);
  });
}

let _katexPromise = null; // 懒加载单例
function loadKatex() {
  if (_katexPromise) return _katexPromise;
  _katexPromise = new Promise((resolve, reject) => {
    if (window.renderMathInElement) return resolve(window.renderMathInElement); // 容错：已加载
    // css 必需（排版定位 + @font-face）；以 <link> 懒注入（决策P4.14-5）。
    if (!document.getElementById("katex-css")) {
      const l = document.createElement("link");
      l.id = "katex-css"; l.rel = "stylesheet"; l.href = "/static/vendor/katex/katex.min.css";
      document.head.appendChild(l);
    }
    // 按序加载：katex → mhchem（扩展 katex，须在其后）→ auto-render（读 window.katex、暴露 renderMathInElement）。
    loadScript("/static/vendor/katex/katex.min.js")
      .then(() => loadScript("/static/vendor/katex/contrib/mhchem.min.js")) // 注册 \ce/\pu 进 katex
      .then(() => loadScript("/static/vendor/katex/contrib/auto-render.min.js"))
      .then(() => (window.renderMathInElement
        ? resolve(window.renderMathInElement)
        : reject(new Error("no renderMathInElement"))))
      .catch(reject);
  }).catch((e) => { _katexPromise = null; throw e; }); // 失败不缓存、可重试（同 P4.13）
  return _katexPromise;
}

// 把 container 内的数学/化学公式就地排版。须在每个 innerHTML 注入点之后调用（经 enhanceContent）。
// 幂等可重入：auto-render 跳过已含 .katex 的子树。
async function typesetMath(container) {
  if (!container) return;
  // 轻量预判：容器内无「**成对**数学分隔符」则零加载（懒，决策P4.14-1）。四支**都要求闭合**——$$…$$ / \(…\) /
  // \[…\]（块级，跨行）/ $…$（行内，≥1 非 $ 字符、不跨行）——故**孤立的开头符**（单个 $5 / 裸 $$ / 裸 \( / 裸 \[，
  // 无配对闭合）**不触发**（P3 修，比「含开头符」保守、与「成对」口径一致）。化学 \ce 必在 $…$ 对内、已覆盖；
  // 裸 \ce{} 不渲染、不探（决策P4.14-2）。**残留（启发式，benign）**：① $…$ 对内含货币（如「$5 至 $10」）等罕见
  // 情形仍命中、付一次加载（甚至误排版，决策P4.14-3 的 $ 歧义）；② textContent 含**代码块**里的成对分隔符（如 LaTeX
  // 代码样例）也会命中、付一次加载——但 auto-render 的 ignoredTags 随后跳过 pre/code、**不误排版**，仅一次无谓加载
  // （首载后缓存）。两者皆 presentation-only。
  if (!/\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$[^$\n]+\$/.test(container.textContent || "")) return;
  let render;
  try { render = await loadKatex(); } catch { return; } // 加载失败 → $…$ 文本原样留存（决策P4.14-8）
  try { render(container, { ...KATEX_OPTS }); } // 展开新选项对象/调用：auto-render 的默认 macros 每容器一份、
  catch { /* 整体异常 → 文本保留、不注入（决策P4.14-8） */ } // 跨容器隔离（不在 const KATEX_OPTS 上累积宏）
}
window.typesetMath = typesetMath;
