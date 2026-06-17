"use strict";
// 载入顺序见 index.html：须在 content_enhance.js（编排器）**之前**载入，供其调用全局 highlightCode()。
// 把 ```X 围栏块（fenced_code emit 的 pre>code.language-X，服务端零改、源为转义文本）在浏览器内
// 语法高亮。与 mermaid 同载体（决策P4.14-5）——故拾取逻辑几乎复刻 mermaid_enhance.js。设计见
// docs/P4.14-Web数学化学代码渲染.md §3.1。
//
// 安全姿态（决策P4.14-4）：highlight.js v11 默认即安全——highlightElement 读 .textContent（已转义源）、
// 产物只是 <span class="hljs-*">转义文本</span>、剥一切未转义 HTML 并告警（v11 移除 HTML 透传）。喂转义
// textContent + 每块单次高亮（.hljs 跳重）即合规；**不承诺产物绝对可信**（钉版 + 失败保留源码兜底，§4）。

let _hljsPromise = null; // 懒加载单例（同 mermaid_enhance.js 范式）
function loadHljs() {
  if (_hljsPromise) return _hljsPromise;
  _hljsPromise = new Promise((resolve, reject) => {
    if (window.hljs) return resolve(window.hljs); // 容错：已被别处加载
    const s = document.createElement("script");
    s.src = "/static/vendor/highlight/highlight.min.js";
    s.onload = () => (window.hljs ? resolve(window.hljs) : (s.remove(), reject(new Error("no hljs"))));
    // 失败即移除本 <script>：配合下方失败不缓存 promise，让重试从干净状态重来。
    s.onerror = () => { s.remove(); reject(new Error("hljs 加载失败")); };
    document.head.appendChild(s);
  }).catch((e) => { _hljsPromise = null; throw e; }); // 失败不缓存、可重试（同 P4.13）
  return _hljsPromise;
}

// 把 container 内 ```X 代码块就地高亮。须在每个 innerHTML 注入点之后调用（经 enhanceContent）。
// 幂等可重入：已高亮块带 .hljs、重复调跳过（highlight.js v11 重复高亮会告警「unescaped HTML」）。
async function highlightCode(container) {
  if (!container) return;
  const blocks = [...container.querySelectorAll('pre > code[class*="language-"]')].filter(
    (c) => !c.classList.contains("language-mermaid") && !c.classList.contains("hljs") // 跳 mermaid / 已高亮
  );
  if (!blocks.length) return; // 早退：无可高亮块零加载（懒，决策P4.14-1）
  let hljs;
  try { hljs = await loadHljs(); } catch { return; } // 加载失败 → 保留纯文本代码（决策P4.14-7/-8）
  for (const code of blocks) {
    if (!code.isConnected || code.classList.contains("hljs")) continue; // await 期间可能被重绘/已高亮
    const lang = (code.className.match(/language-([\w-]+)/) || [])[1];
    if (!lang || !hljs.getLanguage(lang)) continue; // 未注册语言 → 纯文本、不猜不报错（决策P4.14-7）
    try { hljs.highlightElement(code); } catch { /* 单块异常 → 保留纯文本（决策P4.14-8） */ }
  }
}
window.highlightCode = highlightCode;
