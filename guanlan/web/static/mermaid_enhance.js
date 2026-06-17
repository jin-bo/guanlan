"use strict";
// 载入顺序见 index.html；须在 wiki.js/chat.js/jobs.js/staging.js **之前**载入，供它们在每个 markdown
// 注入点之后调用全局 enhanceMermaid()。把 ```mermaid 围栏块（fenced_code emit 的 pre>code.language-mermaid，
// 服务端零改、图源为转义文本）在浏览器内渲染成图。纯全局函数、零构建（同前端既有风格）。设计见
// docs/P4.13-Web-mermaid渲染.md。
//
// 安全姿态（决策P4.13-3）：mermaid 以 securityLevel:'strict' + htmlLabels:false 初始化——内置 DOMPurify
// 消毒标签、禁 click/script 指令。本期信任边界 = strict + htmlLabels:false + 钉版 + 失败保留源码；**不承诺
// mermaid 产物绝对可信**（§4）。任何渲染/加载/语法失败一律退回 <pre> 源码 + 错误徽标，永不空白、零未消毒注入。

// 昼澜主题（决策P4.13-5）：themeVariables 取值为 app.css :root 既有昼澜调色（注释标源变量），使图与界面同
// 水墨澜色调；仅一档常量、不预留切换。硬编码避免运行时读 CSS 变量（可测、与设计稿一致）。
const GUANLAN_MERMAID_THEME = {
  primaryColor: "#EAF6F4", // --lan-foam：节点填充
  primaryBorderColor: "#0F4C5C", // --lan-deep：节点描边
  primaryTextColor: "#1B2A2E", // --lan-ink：节点文字
  lineColor: "#0F4C5C", // --lan-deep：连线
  textColor: "#1B2A2E", // --lan-ink：图内文字
  secondaryColor: "#EEF3F4", // --lan-paper
  tertiaryColor: "#F7FAFB", // --lan-surface
  fontFamily: "inherit",
};

// 懒加载单例：首个 mermaid 块出现才注入 vendored 运行时 <script>（自包含 UMD，加载后 window.mermaid 就绪，
// 见 vendor/README.md）。不含图的页/答案零加载（决策P4.13-1）。注入 <script> 而非 import()：UMD 单文件、
// 零运行时动态 import、离线自洽。失败的 promise 不缓存，留待下次重试（避免一次网络抖动后永久失效）。
let _mermaidPromise = null;
function loadMermaid() {
  if (_mermaidPromise) return _mermaidPromise;
  _mermaidPromise = new Promise((resolve, reject) => {
    if (window.mermaid) return resolve(window.mermaid); // 容错：已被别处加载
    const s = document.createElement("script");
    s.src = "/static/vendor/mermaid.min.js";
    s.onload = () => {
      if (!window.mermaid) { s.remove(); return reject(new Error("mermaid 未导出 window.mermaid")); }
      window.mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict", // 信任铰链：内置 DOMPurify 消毒 + 禁 click/script（决策P4.13-3）
        htmlLabels: false, // 标签走纯 SVG <text>、不渲染 foreignObject-HTML
        theme: "base",
        themeVariables: GUANLAN_MERMAID_THEME,
      });
      resolve(window.mermaid);
    };
    // 失败即移除本 <script>：配合下方失败不缓存 promise，让重试从干净状态重来、不在 <head> 堆死标签。
    s.onerror = () => { s.remove(); reject(new Error("mermaid 运行时加载失败")); };
    document.head.appendChild(s);
  }).catch((e) => {
    _mermaidPromise = null; // 失败不缓存，下次 enhanceMermaid 可重试
    throw e;
  });
  return _mermaidPromise;
}

let _mermaidSeq = 0; // 单调自增容器 id（mermaid 内部需唯一 id）；不依赖 Math.random（可测、与前端惯例一致）。

// 给一个含失败 mermaid 块的 <code> 标错误徽标（保留 <pre> 源码、不替换）。文案 textContent 注入防注入；幂等。
function markMermaidFailed(code) {
  const pre = code.parentElement;
  if (!pre || !pre.parentNode) return;
  const prev = pre.previousElementSibling;
  if (prev && prev.classList && prev.classList.contains("mermaid-error")) return; // 已标过 → 幂等
  const badge = document.createElement("div");
  badge.className = "mermaid-error";
  badge.textContent = window.t ? window.t("mermaid.renderFail") : "图渲染失败";
  pre.parentNode.insertBefore(badge, pre);
}

// 把 container 内的 ```mermaid 围栏块渲染成图就地替换。**须在每个 innerHTML 注入点之后调用，且挂在重绘
// 函数内部**（wiki.js paintPage / staging.js paint），令切语言 / 切预览模式重设 innerHTML 后重新增强
// （决策P4.13-8）。幂等可重入：替换后 code 已离树，重复调不二次渲染。
async function enhanceMermaid(container) {
  if (!container) return;
  const blocks = container.querySelectorAll("pre > code.language-mermaid");
  if (!blocks.length) return; // 早退：无图块零加载（懒，决策P4.13-1）
  let mermaid;
  try {
    mermaid = await loadMermaid();
  } catch {
    blocks.forEach(markMermaidFailed); // 运行时加载失败 → 源码留存 + 徽标（决策P4.13-6）
    return;
  }
  for (const code of blocks) {
    const pre = code.parentElement;
    if (!pre || !pre.isConnected) continue; // 已被别的重绘替换/移除 → 跳过
    const src = code.textContent; // 图 DSL（浏览器已反转义），非 HTML
    try {
      await mermaid.parse(src); // 先校验语法（不注入 DOM）→ 抛错即语法错、不留孤儿节点
      const out = await mermaid.render("mermaid-" + ++_mermaidSeq, src);
      if (!pre.isConnected) continue; // await 期间可能被重绘替换
      const fig = document.createElement("figure");
      fig.className = "mermaid-rendered";
      fig.innerHTML = out.svg; // 唯一 mermaid 产物注入点（信任 strict + 内置 DOMPurify，§4）
      pre.replaceWith(fig);
    } catch {
      markMermaidFailed(code); // 语法错/渲染失败 → 保留 <pre> 源 + 徽标（决策P4.13-6）
    }
  }
}
window.enhanceMermaid = enhanceMermaid;
