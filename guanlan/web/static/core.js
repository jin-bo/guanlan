"use strict";
// 观澜 Web 宿主前端（P4，见 docs/P4-Web宿主.md §6 决策P4-3）。
// vanilla JS + fetch，无 npm/构建/CDN/第三方运行时；流式只用 fetch 读 response.body（不用 EventSource）。
// 布局：对话内容(左) / Wiki 搜索+内容(右) / 对话输入框(底部满宽)。

const $ = (sel) => document.querySelector(sel);

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 终端进度条（tqdm，如 ingest/convert 解析）用回车 `\r` 原地刷新同一行；浏览器 `<pre>` 不识别
// `\r`，会把每次刷新堆成一行，进度日志爆成几十行。还原"原地刷新"：按 `\n` 切行，每行只保留
// 最后一个 `\r` 之后的片段（= 该行进度条的最终/当前态）。纯展示层处理，不动 job.output 原文。
function collapseCR(text) {
  return String(text)
    .replace(/\r\n/g, "\n") // 先归一 CRLF：否则行尾单个 \r 会把整行误清空
    .split("\n")
    .map((line) => {
      const i = line.lastIndexOf("\r");
      return i === -1 ? line : line.slice(i + 1);
    })
    .join("\n");
}

// ── 跨切面共享状态（集中声明于首个载入文件，杜绝跨文件载入期 TDZ）──
let overlayRepaint = null; // 打开浮层的 opener 取数后存一个「纯渲染」闭包；切换/关闭时用/清
let stagingOpen = false; // 暂存区浮层是否打开：修订 turn 收尾（done/stopped）后据此自动重拉刷新
let stagingPath = null;  // 当前浏览目录（相对根，如 workspace/uploads/第一章）；null = 根视图（uploads/parsed 两段）

// ── 浮层 ──────────────────────────────────────────────────────────────────────

// 首参是标题的 i18n key（如 "overlay.ingest"）：在标题节点上标注 data-i18n，借 applyI18n 的白名单路径
// 落地当前语言并在语言切换时自动纯重解析（决策P4.7-8，不新增非字面量 t() 调用点）。缺 key 时 t() 回退
// 到 key 本身，故传入未登记的字符串仍按字面量显示（向后兼容）。
function showOverlay(titleKey, html) {
  stagingOpen = false; // 任何其它浮层（报告/投喂/ingest/heal/历史）打开即清——openStaging 之后再置回，
  // 防 done/stopped 误把 renderStaging 灌进别的浮层 body（暂存区 P4.6）。
  overlayRepaint = null; // 新浮层默认无重画闭包；opener 取数后再按需注册（语言切换纯重渲染，P4.7）
  $("#overlay-title").dataset.i18n = titleKey; // 标注 i18n key → 语言切换时 applyI18n 据此纯重解析标题（P4.7）
  $("#overlay-body").innerHTML = html;
  applyI18n($("#overlay")); // 用当前语言落地标题（含 close aria）；标题文案随之出当前语言
  $("#overlay").classList.remove("hidden");
}
$("#overlay-close").addEventListener("click", () => $("#overlay").classList.add("hidden"));
$("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay") $("#overlay").classList.add("hidden"); });
// 浮层关闭时清 stagingOpen（done/stopped 不再误刷已关的浮层）。挂一次，幂等。
$("#overlay-close").addEventListener("click", () => { stagingOpen = false; });
$("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay") stagingOpen = false; });
