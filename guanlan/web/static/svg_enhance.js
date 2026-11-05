"use strict";
// 载入顺序见 index.html：须在 content_enhance.js（编排器）**之前**载入，供其调用全局 enhanceSvg()。
// 把 SVG 代码块（pre>code.language-svg——```svg 围栏块，或服务端把块级原始 <svg> 改写成的同形块）在浏览器内
// 渲染成图。纯全局函数、零构建、零第三方依赖。设计见 docs/P4.28-Web内嵌SVG渲染.md。
//
// 安全姿态（决策P4.28-2）：**永不把 SVG 标记注入 DOM**。源码只经 DOMParser 解析做校验（解析出的文档不挂树、
// 不执行脚本），渲染一律走 <img src="data:image/svg+xml,…">——图像上下文里的 SVG 不执行脚本、不加载外部资源
// （外链图片/字体/样式表）、不响应事件，信任边界由浏览器给出，故无需 SVG 消毒器。代价：SVG 内的链接与动画
// 交互不可用（静态图足够，§1）。任何解析/加载失败一律保留 <pre> 源码 + 错误徽标，永不空白。

const SVG_NS = "http://www.w3.org/2000/svg";
const XLINK_NS = "http://www.w3.org/1999/xlink";
// 单图源码上限：超过即保留源码（data: URL 过大拖慢页面；正常手写/生成的示意图远小于此）。
const SVG_MAX_CHARS = 1_000_000;

// 补齐命名空间声明：作为图像加载的 SVG **必须**带 xmlns，而 markdown 里手写的 SVG 常省略（内联 HTML 中可省）。
// 只改根元素开标签，且仅在缺失时补；用了 xlink: 前缀却没声明的同样补上（否则 XML 解析直接报错）。
function normalizeSvgSource(src) {
  const open = src.match(/<svg\b[^>]*>/i);
  if (!open) return src;
  let tag = open[0];
  const add = [];
  if (!/\sxmlns\s*=/.test(tag)) add.push(`xmlns="${SVG_NS}"`);
  if (/\bxlink:/.test(src) && !/\sxmlns:xlink\s*=/.test(tag)) add.push(`xmlns:xlink="${XLINK_NS}"`);
  if (!add.length) return src;
  tag = tag.replace(/^<svg\b/i, "<svg " + add.join(" "));
  return src.slice(0, open.index) + tag + src.slice(open.index + open[0].length);
}

// 校验：严格 XML 解析成功、根元素是 SVG 命名空间的 <svg>。返回根元素（仅读属性，不挂树）或 null。
function parseSvgRoot(src) {
  let doc;
  try {
    doc = new DOMParser().parseFromString(src, "image/svg+xml");
  } catch {
    return null;
  }
  const root = doc && doc.documentElement;
  if (!root || root.getElementsByTagName("parsererror").length) return null;
  if (root.localName !== "svg" || root.namespaceURI !== SVG_NS) return null;
  return root;
}

// 给失败的 SVG 块标错误徽标（保留 <pre> 源码、不替换）。文案 textContent 注入防注入；幂等。
function markSvgFailed(code) {
  const pre = code.parentElement;
  if (!pre || !pre.parentNode) return;
  const prev = pre.previousElementSibling;
  if (prev && prev.classList && prev.classList.contains("svg-error")) return; // 已标过 → 幂等
  const badge = document.createElement("div");
  badge.className = "svg-error";
  badge.textContent = window.t ? window.t("svg.renderFail") : "SVG 渲染失败（已保留源码）";
  pre.parentNode.insertBefore(badge, pre);
}

// 单块：校验 → <img> 解码 → 替换；失败留源码 + 徽标。
async function renderSvgBlock(code) {
  const pre = code.parentElement;
  if (!pre || !pre.isConnected) return;
  const src = normalizeSvgSource((code.textContent || "").trim()); // 已反转义的源码，非 HTML
  const root = src.length <= SVG_MAX_CHARS ? parseSvgRoot(src) : null;
  if (!root) { markSvgFailed(code); return; }
  const img = document.createElement("img");
  img.decoding = "async";
  const title = root.querySelector(":scope > title");
  img.alt = (title && title.textContent.trim()) || (window.t ? window.t("svg.alt") : "SVG 图");
  // 没有绝对宽度（缺 width 或百分比 width）的图没有固有宽度，<img> 会退成 300×150——有 viewBox 时让它
  // 撑满可用宽度、按比例定高。
  const w = root.getAttribute("width");
  if ((w === null || /%\s*$/.test(w)) && root.hasAttribute("viewBox")) img.style.width = "100%";
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(src);
  try {
    await img.decode(); // 浏览器真正能渲染才替换；失败（如引用了不支持的特性致整图无效）→ 留源码
  } catch {
    markSvgFailed(code);
    return;
  }
  if (!pre.isConnected) return; // await 期间可能被重绘替换
  const fig = document.createElement("figure");
  fig.className = "svg-rendered";
  fig.appendChild(img);
  pre.replaceWith(fig);
}

// 把 container 内的 SVG 代码块就地换成图。须在每个 innerHTML 注入点之后调用（经 enhanceContent）。
// 各块并发解码（互不依赖，不逐块串行等 decode）。
// 幂等可重入：替换后 code 已离树；失败块带徽标、重复调只会再判一次失败（徽标幂等）。
async function enhanceSvg(container) {
  if (!container) return;
  const blocks = container.querySelectorAll("pre > code.language-svg");
  await Promise.all(Array.from(blocks, renderSvgBlock));
}
window.enhanceSvg = enhanceSvg;
