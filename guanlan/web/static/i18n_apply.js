"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 界面双语落地层（P4.7）。
// ── 界面双语（P4.7，见 docs/P4.7-中英双语.md）──────────────────────────────────
//
// 只翻界面 chrome（按钮/tooltip/占位符/弹层标题/前端生成的提示·错误），内容真相（wiki 正文 /
// agent 回答 / 报告·自省正文 / 文件名）一律不译。词表与 t()/setLang()/currentLang() 在 i18n.js。
// 切换=刷静态 data-i18n + 用「已取回数据」纯渲染少数动态面，绝不重载页面、绝不重发网络请求（决策P4.7-6）。

// 声明式回填：把静态节点上的 data-i18n* 据当前语言写回 textContent / title / placeholder / aria-label。
// 注：这四处 t(el.dataset.*) 是动态 key（由 HTML data-i18n* 喂入），决策P4.7-8 唯一白名单。
function applyI18n(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  root.querySelectorAll("[data-i18n-aria]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
}

// 切换时重渲染开着的动态面（纯渲染、吃已缓存数据，不取数）。C2 各动态面注册自己的重画函数。
function rerenderDynamic() {
  // 右栏 Wiki：列表态就地重算空态/分组；单页态用缓存数据重绘外围 chrome（不重拉 /api/page）。
  const cur = currentView();
  if (cur && cur.kind === "index") renderIndex(cur);
  else if (cur && cur.kind === "page") repaintPageChrome();
  else if (cur && cur.kind === "raw") repaintRawChrome(); // raw 源态：纯重绘 banner（吃缓存，不重拉）
  // 搜索态：用缓存结果纯重绘头部（命中/检索计数）+ 空/失败态文案（吃 view.results，不重拉 /api/search，
  // P5.1）。在飞搜索（尚无 results）无需在此重绘——其 doSearch 回调落地时按当时语言 t() 自然出新语言。
  else if (cur && cur.kind === "search" && cur.results) paintSearch(cur);
  // 打开着的浮层：跑 opener 留下的纯渲染闭包（吃缓存数据，不重发请求）。
  if (overlayRepaint && !$("#overlay").classList.contains("hidden")) overlayRepaint();
  // 姿态徽标 tooltip（随 currentMode）+ 发送/停止按钮文案（随 chatStreaming）。
  setModeBadge(currentMode);
  setChatSending(chatStreaming);
}

function applyLang(lang) {
  setLang(lang);                                              // i18n.js 内部 _lang
  try { localStorage.setItem("guanlan.lang", lang); } catch (_e) { /* 隐私模式可能禁写，忽略 */ }
  document.documentElement.lang = lang;                       // <html lang> 同步（a11y）
  const label = $("#lang-label");
  if (label) label.textContent = (lang === "zh") ? "EN" : "中"; // 按钮显「目标语言」码
  applyI18n(document);                                        // 刷所有静态 data-i18n 节点
  rerenderDynamic();                                          // 重渲染开着的动态面
}
function toggleLang() { applyLang(currentLang() === "zh" ? "en" : "zh"); }

// 用「最后一次语言设置」初始化；首访/无记忆默认 zh，不做 navigator 探测（决策P4.7-5）。
function initLang() {
  let saved = null;
  try { saved = localStorage.getItem("guanlan.lang"); } catch (_e) { /* 读失败 → 默认 zh */ }
  applyLang(saved === "zh" || saved === "en" ? saved : "zh");
}
