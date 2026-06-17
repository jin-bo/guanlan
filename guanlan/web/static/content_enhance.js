"use strict";
// 载入顺序见 index.html：须在 mermaid_enhance.js / math_enhance.js / code_enhance.js（定义三个全局）**之后**、
// 在调用者 wiki.js/chat.js/jobs.js/staging.js **之前**载入。把 P4.13 的单一 enhanceMermaid 钩子**泛化为内容
// 增强编排器**（决策P4.14-9）：四类 markdown 注入点统一调此一个 enhanceContent（替换原 enhanceMermaid 调用，
// 一标识符之改、不增删注入点）。设计见 docs/P4.14-Web数学化学代码渲染.md §3.3。

// 三增强彼此**区域不相交**（代码在 pre>code、数学跳过 pre/code 的文本节点、mermaid 在 language-mermaid），
// 故顺序仅为整洁；各自幂等可重入、独立懒加载、独立失败封闭。不 await（互不依赖、并发更快）。
async function enhanceContent(container) {
  if (!container) return;
  enhanceMermaid(container); // P4.13：```mermaid → SVG（保持不动）
  highlightCode(container);  // P4.14：```X → 高亮（区域：pre>code）
  typesetMath(container);    // P4.14：$…$（含其内 \ce{}）→ 排版（区域：跳 pre/code 的文本节点）
}
window.enhanceContent = enhanceContent;
