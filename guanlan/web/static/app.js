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
let overlayRepaint = null; // 打开浮层的 opener 取数后存一个「纯渲染」闭包；切换/关闭时用/清
function rerenderDynamic() {
  // 右栏 Wiki：列表态就地重算空态/分组；单页态用缓存数据重绘外围 chrome（不重拉 /api/page）。
  const cur = currentView();
  if (cur && cur.kind === "index") renderIndex(cur);
  else if (cur && cur.kind === "page") repaintPageChrome();
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

// ── Wiki：合并视图（Concept 列表 ⇄ 单页）+ 回退/往前历史 ────────────────────
//
// 右栏只有一个 #wiki-view：启动显示 Concept 列表；点页进单页内容；正文 [[链接]] 续进。
// 历史栈像浏览器：navigate 压栈、回退/往前移指针。视图分三种：
//   {kind:"index"}                       —— 空输入：按 type 分组的页面目录（"首页"）
//   {kind:"search", query, results?}     —— 非空输入：/api/search 正文 BM25 召回（P5.1）；
//                                            results 取回后缓存在条目上，back/forward 直接重绘不重拉
//   {kind:"page", path}                  —— 单页渲染

let allPages = [];          // /api/pages 缓存
let pagesError = null;      // /api/pages 加载错误（与"空库"区分，避免误显"暂无页面"）
let history = [];           // 视图历史栈；index/search 条目自带 query 快照，回退/往前可还原
let histPos = -1;           // 当前视图在栈中的下标
let renderToken = 0;        // 每次渲染自增；单页异步响应回来若 token 已变则丢弃（防迟到覆盖）
let searchToken = 0;        // 远端搜索自增 token；迟到响应回来若已变则丢弃（防 stale-overwrite，与 renderToken 同纪律，P5.1）
let searchTimer = null;     // 搜索 debounce 句柄（只压频）；token 才防乱序
const SEARCH_DEBOUNCE_MS = 200;
const SEARCH_LIMIT = 20;

const currentView = () => (histPos >= 0 ? history[histPos] : null);

async function loadPages() {
  try {
    ({ pages: allPages } = await getJSON("/api/pages"));
    pagesError = null;
  } catch (e) {
    allPages = [];
    pagesError = e.message;
  }
  const cur = currentView();
  if (cur && cur.kind === "index") renderIndex(cur); // 已在列表态则就地刷新
}

function navigate(view) {
  history = history.slice(0, histPos + 1); // 丢弃前进分支
  history.push(view);
  histPos = history.length - 1;
  renderView(view);
  updateNav();
}

function renderView(view) {
  // 还原视图时同步搜索框（回退/往前到某个 index/search 条目要回到它当时的查询词）。
  if (view.kind === "index") {
    $("#wiki-search").value = view.query || "";
    renderIndex(view);
  } else if (view.kind === "search") {
    $("#wiki-search").value = view.query || "";
    renderSearch(view);
  } else {
    renderPage(view.path);
  }
}

function goBack() {
  if (histPos > 0) { histPos--; renderView(history[histPos]); updateNav(); }
}
function goForward() {
  if (histPos < history.length - 1) { histPos++; renderView(history[histPos]); updateNav(); }
}
function updateNav() {
  $("#wiki-back").disabled = histPos <= 0;
  $("#wiki-fwd").disabled = histPos >= history.length - 1;
}

function renderIndex(entry) {
  renderToken++; // 作废任何在飞的单页渲染（用户已切回列表）
  searchToken++; // 同时作废在飞的远端搜索（已切到目录态，迟到响应不得覆盖，P5.1）
  const view = $("#wiki-view");
  if (pagesError) {
    view.innerHTML = `<div class="empty">${escapeHtml(t("wiki.loadFail", pagesError))}</div>`;
    return;
  }
  const q = ((entry && entry.query) || "").trim().toLowerCase();
  const matched = q
    ? allPages.filter((p) => p.title.toLowerCase().includes(q) || p.path.toLowerCase().includes(q))
    : allPages;
  if (!matched.length) {
    view.innerHTML = `<div class="empty">${allPages.length ? t("wiki.emptyMatch") : t("wiki.emptyAll")}</div>`;
    return;
  }
  const groups = Object.create(null); // null 原型：type 是容错用户值，防 __proto__/constructor 污染
  for (const p of matched) (groups[p.type] ||= []).push(p);
  view.innerHTML = "";
  for (const type of Object.keys(groups).sort()) {
    const title = document.createElement("div");
    title.className = "group-title";
    title.textContent = `${type} (${groups[type].length})`;
    view.appendChild(title);
    for (const p of groups[type]) {
      const a = document.createElement("a");
      a.className = "page-link";
      a.textContent = p.title;
      a.href = "#";
      a.dataset.path = p.path;
      a.addEventListener("click", (e) => { e.preventDefault(); navigate({ kind: "page", path: p.path }); });
      view.appendChild(a);
    }
  }
}

// 远端正文 BM25 搜索（P5.1）：非空输入走 /api/search、展示标题/路径/type/score/snippet。
//   - back/forward 恢复：条目已缓存 results → 直接重绘、不重拉（与单页 lastPage 同精神）；
//   - live 输入：debounce 压频 + 单调 searchToken 防乱序（"先搜 A 再搜 AB、A 后到"不得盖掉 AB）。
function renderSearch(view) {
  if (view.results) {
    searchToken++; // 恢复态吃缓存：顺带作废任何在飞的旧搜索
    paintSearch(view);
    return;
  }
  doSearch(view);
}

function doSearch(view) {
  const tok = ++searchToken; // 本次搜索序号（迟到响应据此丢弃）
  if (searchTimer) clearTimeout(searchTimer); // 取消上一个尚未发出的搜索（debounce 压频）
  // 「检索中…」占位**放进 debounce 回调**：连打字时旧结果留屏不闪，待停顿满 debounce、真要发请求前才置位
  // （否则每个 keystroke 同步抹成「检索中…」、连打字时一直卡在占位、相对旧的本地即时过滤是可见回退）。
  searchTimer = setTimeout(async () => {
    // debounce 期间用户可能清空搜索框/导航离开（renderIndex/renderPage 只自增 searchToken、不清 timer），
    // 故置「检索中…」占位前先核对 token 与当前视图：已被取代就直接退出，绝不抹掉用户已切到的目录/单页。
    if (tok !== searchToken || currentView() !== view) return;
    const wikiView = $("#wiki-view");
    wikiView.innerHTML = `<p class="muted">${escapeHtml(t("search.searching"))}</p>`;
    try {
      const data = await getJSON(
        `/api/search?q=${encodeURIComponent(view.query)}&limit=${SEARCH_LIMIT}`
      );
      if (tok !== searchToken) return; // 已发起更新搜索 / 导航离开 → 丢弃迟到响应（防 stale-overwrite）
      view.results = data.results || []; // 缓存到条目：back/forward 可恢复、不重拉
      view.pagesSearched = data.pages_searched;
      if (currentView() === view) paintSearch(view); // 纵深防御：仅当该条目仍是当前视图才绘
    } catch (e) {
      if (tok !== searchToken) return;
      if (currentView() === view)
        wikiView.innerHTML = `<div class="empty">${escapeHtml(t("search.fail", e.message))}</div>`;
    }
  }, SEARCH_DEBOUNCE_MS);
}

// 纯渲染搜索结果（标题/路径/type/score 是数据、片段是正文——一律 textContent，无注入）。
function paintSearch(view) {
  const wikiView = $("#wiki-view");
  const results = view.results || [];
  if (!results.length) {
    wikiView.innerHTML = `<div class="empty">${escapeHtml(t("search.empty", view.query))}</div>`;
    return;
  }
  wikiView.innerHTML = "";
  const head = document.createElement("div");
  head.className = "group-title";
  head.textContent = t("search.head", results.length, view.pagesSearched ?? "?");
  wikiView.appendChild(head);
  for (const r of results) {
    const a = document.createElement("a");
    a.className = "search-hit";
    a.href = "#";
    a.dataset.path = r.page;
    const title = document.createElement("div");
    title.className = "hit-title";
    title.textContent = r.title;
    const meta = document.createElement("div");
    meta.className = "hit-meta";
    meta.textContent = `${r.type} · ${r.page} · ${r.score}`;
    a.append(title, meta);
    if (r.snippet) {
      const sn = document.createElement("div");
      sn.className = "hit-snippet";
      sn.textContent = r.snippet;
      a.appendChild(sn);
    }
    a.addEventListener("click", (e) => {
      e.preventDefault();
      navigate({ kind: "page", path: r.page });
    });
    wikiView.appendChild(a);
  }
}

let lastPage = null; // {path, data}：当前单页态缓存，供语言切换时纯重绘外围 chrome（不重拉 /api/page）

// 纯渲染：用已取回的页数据画「外围 chrome（page-meta）+ 正文 HTML」。chrome 走 t()；正文是内容、原样。
function paintPage(data, path) {
  const view = $("#wiki-view");
  let meta;
  if (data.meta) {
    const typeTag = data.meta.type ? `<span class="ptype">${escapeHtml(String(data.meta.type))}</span>` : "";
    const lu = data.meta.last_updated ? escapeHtml(t("wiki.updatedAt", String(data.meta.last_updated))) : "";
    meta = `${typeTag}<span>${escapeHtml(path)}</span> · <span>${lu}</span>`;
  } else {
    meta = `<span>${escapeHtml(path)}</span> · <span class="muted">${escapeHtml(t("wiki.noFrontmatter"))}</span>`;
  }
  view.innerHTML = `<div class="page-meta">${meta}</div>` + data.html;
}

// 语言切换时纯重绘当前单页的外围 chrome（吃缓存数据，绝不重拉 /api/page，决策P4.7-6）。
function repaintPageChrome() {
  const cur = currentView();
  if (cur && cur.kind === "page" && lastPage && lastPage.path === cur.path) paintPage(lastPage.data, cur.path);
}

async function renderPage(path) {
  const tok = ++renderToken; // 本次渲染的序号
  searchToken++; // 导航离开搜索 → 作废在飞的远端搜索响应（P5.1，防 stale-overwrite）
  const view = $("#wiki-view");
  view.innerHTML = `<p class="muted">${escapeHtml(t("wiki.loading"))}</p>`;
  try {
    const data = await getJSON(`/api/page?path=${encodeURIComponent(path)}`);
    if (tok !== renderToken) return; // 已导航走（回退/搜索/续进），丢弃迟到响应
    lastPage = { path, data }; // 缓存供语言切换纯重绘
    paintPage(data, path);
  } catch (e) {
    if (tok !== renderToken) return;
    view.innerHTML = `<p class="muted">${escapeHtml(t("wiki.openFail", e.message))}</p>`;
  }
}

$("#wiki-home").addEventListener("click", () => navigate({ kind: "index", query: "" }));
$("#wiki-back").addEventListener("click", goBack);
$("#wiki-fwd").addEventListener("click", goForward);

// 非空输入一律走 /api/search 正文召回；空输入回 index 目录（决策P5.1-5：本地过滤只服务空输入，
// 不再对非空输入做本地 title/path 过滤——消除"搜索框找不到正文"的错觉）。在列表态（index/search）
// 就地替换当前条目、不压历史（保回退还原）；在单页态压一次新条目回到列表态。
$("#wiki-search").addEventListener("input", (e) => {
  const q = e.target.value;
  const view = q.trim() ? { kind: "search", query: q } : { kind: "index", query: q };
  const cur = currentView();
  if (cur && (cur.kind === "index" || cur.kind === "search")) {
    history[histPos] = view; // 就地替换当前列表态条目（不压历史）
    renderView(view);
  } else {
    navigate(view); // 单页态 → 回列表态（只压一次）
  }
});

// 站内 wikilink 导航：事件委托到合并视图，续进单页历史。
$("#wiki-view").addEventListener("click", (e) => {
  const a = e.target.closest("a.wikilink[data-page]");
  if (a) { e.preventDefault(); navigate({ kind: "page", path: a.dataset.page }); }
});

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

// ── 零 LLM 报告 / graph ─────────────────────────────────────────────────────

function renderReport(name, data) {
  const itemsKey = "violations" in data ? "violations" : "findings";
  const items = data[itemsKey] || [];
  const word = itemsKey === "violations" ? t("report.violation") : t("report.finding");
  const head = data.ok
    ? `<p class="report-ok">${escapeHtml(t("report.ok", name, data.pages_checked, word))}</p>`
    : `<p class="report-bad">${escapeHtml(t("report.bad", name, data.pages_checked, items.length))}</p>`;
  const body = items.map((it) =>
    `<div class="finding"><span class="kind">[${escapeHtml(it.kind)}]</span> ${escapeHtml(it.page || t("report.global"))}: ${escapeHtml(it.detail)}</div>`
  ).join("");
  return head + body;
}

document.querySelectorAll(".actions button[data-report]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const name = btn.dataset.report; // 命令名（check/health/lint）：标题用双语 key overlay.<name>，报告正文仍用命令名
    showOverlay("overlay." + name, `<p class="muted">${escapeHtml(t("common.running"))}</p>`);
    try {
      const data = await getJSON(`/api/report/${name}`);
      $("#overlay-body").innerHTML = renderReport(name, data);
      overlayRepaint = () => { $("#overlay-body").innerHTML = renderReport(name, data); }; // 语言切换纯重渲染（吃缓存 data）
    } catch (e) {
      $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.fail", e.message))}</p>`;
    }
  });
});

$("#graph-btn").addEventListener("click", () => window.open("/graph", "_blank"));

// ── 写：ingest（从 raw/ 选一篇 → 入队 → 轮询）────────────────────────────────

$("#ingest-btn").addEventListener("click", openIngestPicker);

let rawShowIngested = false; // ingest 选单过滤态：默认只看未收录，按钮可切看已收录

async function openIngestPicker() {
  rawShowIngested = false; // 每次开选单复位到默认「未收录」视图
  showOverlay("overlay.ingest", `<p class="muted">${escapeHtml(t("ingest.loadingRaw"))}</p>`);
  let files;
  try {
    ({ files } = await getJSON("/api/raw"));
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("ingest.loadRawFail", e.message))}</p>`;
    return;
  }
  renderRawPicker(files);
  overlayRepaint = () => renderRawPicker(files); // 语言切换纯重渲染（吃缓存 files + 当前过滤态）
}

// 纯渲染 raw/ 选单（文件名是内容、按字面显示；过滤条/空态/角标/按钮是 chrome）。
// 默认只显未收录（`ingested:false`），顶部一颗按钮在 未收录 ⇄ 已收录 间切换（非破坏：
// 已收录文件不从磁盘删、仍可预览/重投，只是默认收起）。
function renderRawPicker(files) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  if (!files.length) {
    box.innerHTML = `<p class="muted">${escapeHtml(t("ingest.rawEmpty"))}</p>`;
    return;
  }
  const pending = files.filter((f) => !f.ingested);
  const done = files.filter((f) => f.ingested);

  const bar = document.createElement("div"); // 过滤切换条：左计数、右切换按钮
  bar.className = "raw-filter";
  const toggle = document.createElement("button");
  toggle.className = "stage-act";
  toggle.textContent = rawShowIngested
    ? t("ingest.showPending", pending.length)
    : t("ingest.showIngested", done.length);
  toggle.addEventListener("click", () => {
    rawShowIngested = !rawShowIngested;
    renderRawPicker(files);
  });
  bar.appendChild(toggle);
  box.appendChild(bar);

  const shown = rawShowIngested ? done : pending;
  if (!shown.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = rawShowIngested ? t("ingest.noIngested") : t("ingest.allIngested");
    box.appendChild(empty);
    return;
  }
  for (const f of shown) {
    const row = document.createElement("div");
    row.className = "raw-pick";
    const name = document.createElement("button"); // 文件名即预览入口：点名预览，省去独立按钮
    name.className = "raw-name";
    name.textContent = `${f.name} (${(f.size / 1024).toFixed(1)} KB)`; // textContent：文件名按字面显示，无注入；体积按 KB
    name.addEventListener("click", () => previewRawFile(f.name, files));
    row.appendChild(name);
    if (f.ingested) {
      const badge = document.createElement("span"); // 「已收录」角标（已收录视图下标注）
      badge.className = "raw-badge";
      badge.textContent = t("ingest.ingestedBadge");
      row.appendChild(badge);
    }
    const btn = document.createElement("button");
    btn.textContent = "ingest"; // 命令名，不译
    btn.addEventListener("click", () => triggerIngest(`raw/${f.name}`));
    row.appendChild(btn);
    box.appendChild(row);
  }
}

// raw 源预览（读，ingest 前看清正文）：渲染 markdown，与 workspace 暂存区预览同款 overlay + 返回。
// `files` 透传供「返回」纯重渲染选单（吃缓存，不重拉 /api/raw）。
async function previewRawFile(name, files) {
  overlayRepaint = null; // 预览态不挂重画闭包：避免语言切换把预览刷回选单（返回后由 renderRawPicker 重置）
  const box = $("#overlay-body");
  box.innerHTML = `<p class="muted">${escapeHtml(t("ingest.loadingPreview"))}</p>`;
  const loading = box.firstChild; // 哨兵：迟到响应回来时若浮层已被别的 opener（showOverlay）换走则丢弃（防 stale-overwrite，与 searchToken 同纪律）
  const back = document.createElement("button");
  back.className = "stage-act";
  back.textContent = t("ingest.backToList");
  back.addEventListener("click", () => {
    renderRawPicker(files);
    overlayRepaint = () => renderRawPicker(files); // 复位重画闭包（同 openIngestPicker）
  });
  let data;
  try {
    data = await getJSON(`/api/raw/file?name=${encodeURIComponent(name)}`);
  } catch (e) {
    if (!box.contains(loading)) return; // 浮层已切走 → 不抢渲染（防覆盖新浮层）
    box.innerHTML = "";
    const err = document.createElement("p");
    err.className = "report-bad";
    err.textContent = t("ingest.previewFail", e.message);
    box.append(back, err);
    return;
  }
  if (!box.contains(loading)) return; // 同上：成功响应迟到亦不得覆盖新浮层
  box.innerHTML = "";
  const title = document.createElement("div");
  title.className = "stage-head";
  title.textContent = `raw/${name}`; // textContent：路径按字面显示
  const view = document.createElement("div");
  view.className = "stage-preview rendered";
  view.innerHTML = data.html; // render_page 已 sanitize（同 /api/page）
  box.append(back, title, view);
}

async function triggerIngest(target) {
  showOverlay("overlay.ingest", `<p class="muted">${escapeHtml(t("ingest.submitted", target))}</p>`);
  try {
    const { job_id } = await postJSON("/api/ingest", { target });
    await pollJob(job_id, target);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.submitFail", e.message))}</p>`;
  }
}

// 确定性解析（P4.6.1，决策P4.6.1-1/7）：上传文件 → POST /api/parse → 进度窗口（pollJob running 态渲染
// emit 推上的 backend 分级日志）→ 完成显回执 + 返回暂存区。区别于旧「问 agent 解析」（不再回填 composer）。
async function triggerParse(uploadPath) {
  showOverlay("overlay.parse", `<p class="muted">${escapeHtml(t("staging.parseSubmitted", uploadPath))}</p>`);
  stagingOpen = false; // 进度窗期间暂停自动刷新（避免并发 turn 收尾把进度刷掉）
  try {
    // 裸 fetch 以特判 423（可写 turn 活跃）；其余非 2xx → parseFail。
    const res = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ upload: uploadPath }),
    });
    if (res.status === 423) {
      $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("staging.writableRetryDot"))}</p>`;
      return;
    }
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    const { job_id } = await res.json();
    await pollJob(job_id, t("staging.parsing"), renderParseDone);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("staging.parseFail", e.message))}</p>`;
  }
}

function renderParseDone(job) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const ok = job.exit_code === 0;
  const badge = document.createElement("p");
  badge.innerHTML = ok
    ? `<span class="report-ok">${escapeHtml(t("common.passed"))}</span>`
    : `<span class="report-bad">${escapeHtml(t("common.exitCode", job.exit_code))}</span>`;
  box.appendChild(badge);
  if (job.output) {
    const pre = document.createElement("pre");
    pre.textContent = collapseCR(job.output); // backend 分级日志 + 回执（折叠 \r 进度条，纯 textContent 无注入）
    box.appendChild(pre);
  }
  const back = document.createElement("button");
  back.className = "stage-act";
  back.textContent = t("staging.backToStaging");
  back.addEventListener("click", () => { stagingPath = null; stagingOpen = true; loadStaging(); });
  box.appendChild(back);
}

// 通用作业轮询骨架（ingest / heal 共用）。`renderDone(job)` 可选：heal 有结构化 result，传入
// 自定义渲染；省略则走 ingest 默认（退出码徽标 + output 文本）。完成后统一 loadPages 刷新。
async function pollJob(jobId, label, renderDone) {
  for (;;) {
    const job = await getJSON(`/api/jobs/${jobId}`);
    if (job.state === "done") {
      if (renderDone) {
        renderDone(job);
      } else {
        const ok = job.exit_code === 0;
        const badge = ok
          ? `<span class="report-ok">${escapeHtml(t("common.passed"))}</span>`
          : `<span class="report-bad">${escapeHtml(t("common.exitCode", job.exit_code))}</span>`;
        $("#overlay-body").innerHTML = `<p>${escapeHtml(label)} ${badge}</p><pre>${escapeHtml(collapseCR(job.output || t("common.noOutput")))}</pre>`;
      }
      await loadPages(); // 写后刷新 wiki 搜索列表（heal 新建了页）
      return;
    }
    // running 态：渲染已累加的增量 output（决策P4.6.1-7③/-11）——解析作业的 backend 分级日志在此
    // 实时可见（emit 推上）；ingest/heal/backfill 顺带受益。无 output 时仍只显运行徽标。
    const running = `<p class="muted">${escapeHtml(t("job.running", label, job.state))}</p>`;
    $("#overlay-body").innerHTML = job.output
      ? `${running}<pre>${escapeHtml(collapseCR(job.output))}</pre>`
      : running;
    await sleep(400);
  }
}

// ── heal：缺失实体物化（预览 worklist → 一键物化 → 轮询结构化回执，P4.3）─────────────
//
// 顶栏「heal」→ 浮层先拉零-LLM 预览（可调 limit / min-refs），列本批将物化的高频缺页 + 推迟项；
// 「物化」→ POST /api/heal（入单写者队列，与 ingest 同 worker 串行）→ 复用 pollJob 骨架，但因
// heal 有结构化 result，完成分支按 job.result 渲染回执（resolved / 仍断 / 非预期写 / 推迟 /
// 退出码徽标），agent 散文取自 job.output（同 ingest）。

$("#heal-btn").addEventListener("click", () => openHealPreview());

async function openHealPreview(limit, minRefs) {
  showOverlay("overlay.heal", `<p class="muted">${escapeHtml(t("heal.computing"))}</p>`);
  const qs = new URLSearchParams();
  if (limit) qs.set("limit", limit);
  if (minRefs) qs.set("min_refs", minRefs);
  const suffix = qs.toString() ? `?${qs}` : "";
  let data;
  try {
    data = await getJSON(`/api/heal/preview${suffix}`);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("heal.previewFail", e.message))}</p>`;
    return;
  }
  renderHealPreview(data, limit, minRefs);
  overlayRepaint = () => renderHealPreview(data, limit, minRefs); // 语言切换纯重渲染（吃缓存 data）
}

function renderHealPreview(data, limit, minRefs) {
  const box = $("#overlay-body");
  box.innerHTML = "";

  const worklist = data.worklist || [];
  const postponed = data.postponed || [];

  // 勾选集：默认全选（== 旧「整批物化」行为）。顶部物化按钮的文案/禁用随勾选实时更新。
  const selected = new Set(worklist.map((w) => w.target));
  let go = null;
  const updateGo = () => {
    if (!go) return;
    go.textContent = selected.size ? t("heal.materializeN", selected.size) : t("heal.noTarget");
    go.disabled = selected.size === 0;
  };

  // 顶部一行：左「物化 N 个目标」（仅 worklist 非空），右「limit / min-refs / 预览」微调控件。
  const top = document.createElement("div");
  top.className = "heal-top";
  if (worklist.length) {
    go = document.createElement("button");
    go.className = "heal-go";
    go.addEventListener("click", () => {
      // 用**本次预览所用的** limit/min-refs（renderHealPreview 入参），而非输入框现值：勾选项是
      // 按这份 worklist 算出来的，若改了输入框却没点「预览」，用现值会让服务端按不同参数重算出
      // 另一批，交集把勾选项悄悄漏掉（决策P4.3-3）。「预览」按钮才用输入框现值重拉。
      if (selected.size) triggerHeal(limit || "", minRefs || "", [...selected]);
    });
    top.appendChild(go);
    updateGo();
  }
  // 微调控件：limit / min-refs + 重新预览（默认值即常量，留空表示用服务端默认）。
  const ctrl = document.createElement("div");
  ctrl.className = "heal-ctrl";
  const limitIn = document.createElement("input");
  limitIn.type = "number"; limitIn.min = "1"; limitIn.className = "heal-num";
  limitIn.placeholder = "limit"; if (limit) limitIn.value = limit;
  const minIn = document.createElement("input");
  minIn.type = "number"; minIn.min = "1"; minIn.className = "heal-num";
  minIn.placeholder = "min-refs"; if (minRefs) minIn.value = minRefs;
  const refresh = document.createElement("button");
  refresh.textContent = t("heal.preview");
  refresh.addEventListener("click", () => openHealPreview(limitIn.value || "", minIn.value || ""));
  ctrl.append(limitIn, minIn, refresh);
  top.appendChild(ctrl);
  box.appendChild(top);

  if (!worklist.length) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = postponed.length
      ? t("heal.batchEmpty", postponed.length)
      : t("heal.none");
    box.appendChild(note);
    if (!postponed.length) return;
  } else {
    const head = document.createElement("p");
    head.textContent = t("heal.pickHead", worklist.length);
    box.appendChild(head);
    for (const w of worklist) {
      // <label> 包 checkbox：整行可点切换。默认勾选，change 同步 selected + 刷新按钮。
      const row = document.createElement("label");
      row.className = "heal-item";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "heal-check";
      cb.checked = true;
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(w.target);
        else selected.delete(w.target);
        updateGo();
      });
      const bodyEl = document.createElement("span");
      bodyEl.className = "heal-item-body";
      const tgt = document.createElement("span");
      tgt.className = "heal-target";
      tgt.textContent = `+ ${w.target}`; // textContent：目标名/文件名按字面显示，无注入
      const meta = document.createElement("span");
      meta.className = "heal-meta";
      meta.textContent = t("heal.refBy", w.ref_count, (w.ref_pages || []).join("、"));
      bodyEl.append(tgt, meta);
      row.append(cb, bodyEl);
      box.appendChild(row);
    }
  }

  if (postponed.length) {
    const ph = document.createElement("p");
    ph.className = "muted";
    ph.textContent = t("heal.postponedHead", postponed.length);
    box.appendChild(ph);
    for (const w of postponed) {
      const row = document.createElement("div");
      row.className = "heal-item postponed";
      row.textContent = t("heal.postponedItem", w.target, w.ref_count);
      box.appendChild(row);
    }
  }
}

async function triggerHeal(limit, minRefs, targets) {
  showOverlay("overlay.heal", `<p class="muted">${escapeHtml(t("heal.submitted"))}</p>`);
  const body = {};
  if (limit) body.limit = Number(limit);
  if (minRefs) body.min_refs = Number(minRefs);
  // 勾选子集随请求发出；服务端仍重算 worklist 再取交集（陈旧/越界目标被丢弃，决策P4.3-3 修订）。
  if (targets && targets.length) body.targets = targets;
  try {
    const { job_id } = await postJSON("/api/heal", body);
    await pollJob(job_id, "heal", renderHealDone);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.submitFail", e.message))}</p>`;
  }
}

function renderHealDone(job) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const ok = job.exit_code === 0;
  const badge = document.createElement("p");
  badge.innerHTML = ok
    ? `<span class="report-ok">${escapeHtml(t("common.passed"))}</span>`
    : `<span class="report-bad">${escapeHtml(t("common.exitCode", job.exit_code))}</span>`;
  box.appendChild(badge);

  const result = job.result;
  if (result) {
    const receipts = result.receipts || [];
    const resolved = receipts.filter((r) => r.status === "resolved");
    const still = receipts.filter((r) => r.status === "still_broken");
    const summary = document.createElement("p");
    summary.textContent = t("heal.doneSummary", receipts.length, resolved.length, still.length);
    box.appendChild(summary);
    for (const r of resolved) {
      const div = document.createElement("div");
      div.className = "heal-receipt resolved";
      div.textContent = t("heal.resolved", r.target, r.resolved_to); // 路径是内容、按字面显示
      box.appendChild(div);
    }
    for (const r of still) {
      const div = document.createElement("div");
      div.className = "heal-receipt broken";
      div.textContent = t("heal.stillBroken", r.target, r.reason);
      box.appendChild(div);
    }
    if ((result.unexpected_writes || []).length) {
      const h = document.createElement("div");
      h.className = "heal-warn";
      h.textContent = t("heal.unexpected");
      box.appendChild(h);
      for (const p of result.unexpected_writes) {
        const d = document.createElement("div");
        d.className = "heal-warn-item";
        d.textContent = `! ${p}`;
        box.appendChild(d);
      }
    }
    if ((result.postponed || []).length) {
      const d = document.createElement("div");
      d.className = "muted";
      d.textContent = t("heal.donePostponed", result.postponed.length);
      box.appendChild(d);
    }
  }

  if (job.output) {
    const pre = document.createElement("pre");
    pre.textContent = collapseCR(job.output); // agent 散文（折叠 \r 进度条，同 ingest）
    box.appendChild(pre);
  }
}

// ── audit：语义审计（预览漂移源组 → 一键复核 → 轮询结构化回执，P4.12）──────────────
//
// 顶栏「审计」→ 浮层先拉零-LLM 预览（可调 limit），列本批将复核的漂移源组（raw 已变但 wiki 未重综合）
// + 推迟项；「审计」→ POST /api/audit（入单写者队列，与 ingest/heal/backfill 同 worker 串行）→ 复用
// pollJob 骨架，因 audit 有结构化 result，完成分支按 job.result 渲染回执（已刷新指纹 / 未完成 / 推迟 /
// 退出码徽标），agent 散文取自 job.output（同 heal）。无子集勾选（决策P4.12-3，唯一旋钮是 limit）；
// 复用 heal-* 样式类（决策P4-3 克制，不引新 CSS）。
$("#audit-btn").addEventListener("click", () => openAuditPreview());

async function openAuditPreview(limit) {
  showOverlay("overlay.audit", `<p class="muted">${escapeHtml(t("audit.computing"))}</p>`);
  const suffix = limit ? `?limit=${encodeURIComponent(limit)}` : "";
  let data;
  try {
    data = await getJSON(`/api/audit/preview${suffix}`);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("audit.previewFail", e.message))}</p>`;
    return;
  }
  renderAuditPreview(data, limit);
  overlayRepaint = () => renderAuditPreview(data, limit); // 语言切换纯重渲染（吃缓存 data）
}

function renderAuditPreview(data, limit) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const groups = data.groups || [];
  const postponed = data.postponed || [];

  // 顶部一行：左「审计 N 个漂移源」（仅 groups 非空），右「limit + 预览」微调控件。
  const top = document.createElement("div");
  top.className = "heal-top";
  if (groups.length) {
    const go = document.createElement("button");
    go.className = "heal-go";
    go.textContent = t("audit.auditN", groups.length);
    // 用**本次预览所用的** limit（renderAuditPreview 入参），而非输入框现值（同 heal 决策P4.3-3 的口径）。
    go.addEventListener("click", () => triggerAudit(limit || ""));
    top.appendChild(go);
  }
  const ctrl = document.createElement("div");
  ctrl.className = "heal-ctrl";
  const limitIn = document.createElement("input");
  limitIn.type = "number"; limitIn.min = "1"; limitIn.className = "heal-num";
  limitIn.placeholder = "limit"; if (limit) limitIn.value = limit;
  const refresh = document.createElement("button");
  refresh.textContent = t("audit.preview");
  refresh.addEventListener("click", () => openAuditPreview(limitIn.value || ""));
  ctrl.append(limitIn, refresh);
  top.appendChild(ctrl);
  box.appendChild(top);

  if (!groups.length) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = postponed.length ? t("audit.batchEmpty", postponed.length) : t("audit.none");
    box.appendChild(note);
    if (!postponed.length) return;
  } else {
    const head = document.createElement("p");
    head.textContent = t("audit.head", groups.length);
    box.appendChild(head);
    for (const g of groups) {
      const row = document.createElement("div");
      row.className = "heal-item";
      const bodyEl = document.createElement("span");
      bodyEl.className = "heal-item-body";
      const tgt = document.createElement("span");
      tgt.className = "heal-target";
      tgt.textContent = `⚠ ${g.slug}`; // textContent：slug/文件名按字面显示，无注入
      const meta = document.createElement("span");
      meta.className = "heal-meta";
      meta.textContent = t("audit.groupMeta", g.raw_path, (g.members || []).length, (g.members || []).join("、"));
      bodyEl.append(tgt, meta);
      row.appendChild(bodyEl);
      box.appendChild(row);
    }
  }

  if (postponed.length) {
    const ph = document.createElement("p");
    ph.className = "muted";
    ph.textContent = t("audit.postponedHead", postponed.length);
    box.appendChild(ph);
    for (const g of postponed) {
      const row = document.createElement("div");
      row.className = "heal-item postponed";
      row.textContent = t("audit.postponedItem", g.slug, (g.members || []).length);
      box.appendChild(row);
    }
  }
}

async function triggerAudit(limit) {
  showOverlay("overlay.audit", `<p class="muted">${escapeHtml(t("audit.submitted"))}</p>`);
  const body = {};
  if (limit) body.limit = Number(limit);
  try {
    const { job_id } = await postJSON("/api/audit", body);
    await pollJob(job_id, "audit", renderAuditDone);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.submitFail", e.message))}</p>`;
  }
}

function renderAuditDone(job) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const ok = job.exit_code === 0;
  const badge = document.createElement("p");
  badge.innerHTML = ok
    ? `<span class="report-ok">${escapeHtml(t("common.passed"))}</span>`
    : `<span class="report-bad">${escapeHtml(t("common.exitCode", job.exit_code))}</span>`;
  box.appendChild(badge);

  const result = job.result;
  if (result) {
    const receipts = result.receipts || [];
    const refreshed = receipts.filter((r) => r.status === "refreshed");
    const incomplete = receipts.filter((r) => r.status === "incomplete");
    const summary = document.createElement("p");
    summary.textContent = t("audit.doneSummary", receipts.length, refreshed.length, incomplete.length);
    box.appendChild(summary);
    for (const r of refreshed) {
      const div = document.createElement("div");
      div.className = "heal-receipt resolved";
      div.textContent = t("audit.refreshed", r.slug, (r.members || []).length); // slug 是内容、按字面显示
      box.appendChild(div);
    }
    for (const r of incomplete) {
      const div = document.createElement("div");
      div.className = "heal-receipt broken";
      div.textContent = t("audit.incomplete", r.slug, r.reason);
      box.appendChild(div);
    }
    if ((result.postponed || []).length) {
      const d = document.createElement("div");
      d.className = "muted";
      d.textContent = t("audit.donePostponed", result.postponed.length);
      box.appendChild(d);
    }
  }

  if (job.output) {
    const pre = document.createElement("pre");
    pre.textContent = collapseCR(job.output); // agent 散文（折叠 \r 进度条，同 heal）
    box.appendChild(pre);
  }
}

// ── backfill：把一次好问答沉淀为 wiki/syntheses/ 综合页（P4.8）─────────────────────
//
// 顶栏「回填」或问答气泡的「沉淀」小按钮 → 同一浮层（问题文本域 + 「沉淀」提交）→ POST /api/backfill
// （入单写者队列、与 ingest/heal 同 worker 串行）→ 复用 pollJob 默认渲染（退出码徽标 + job.output：
// 答案 + 门禁回执）。完成后 loadPages() 刷新右栏 wiki，新综合页可搜索·点开核对。入口②只预填问题、
// 不搬只读答案原文（backfill 是另起一次 gated 写，由子进程 Agent 重新综合，决策P4.8-3）。

$("#backfill-btn").addEventListener("click", () => openBackfill());

function openBackfill(prefillQuestion = "") {
  showOverlay("overlay.backfill", ""); // 标题双语 key（同 heal/ingest）
  renderBackfillForm(prefillQuestion);
  // 语言切换纯重渲染：从当前文本域读回实时输入作新 prefill（用户已输入未提交的内容不丢，决策P4.8-9）；
  // 文本域尚未挂载（理论边界）时回退最初 prefill。
  overlayRepaint = () => renderBackfillForm($("#backfill-question")?.value ?? prefillQuestion);
}

function renderBackfillForm(prefillQuestion) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const hint = document.createElement("p");
  hint.className = "muted";
  hint.textContent = t("backfill.hint");
  const ta = document.createElement("textarea");
  ta.id = "backfill-question";
  ta.className = "backfill-question";
  ta.placeholder = t("backfill.placeholder");
  ta.value = prefillQuestion || "";
  const submit = document.createElement("button");
  submit.className = "backfill-go";
  submit.textContent = t("backfill.submit");
  const updateDisabled = () => { submit.disabled = !ta.value.trim(); }; // 空问题禁用（双重兜底，非依赖）
  ta.addEventListener("input", updateDisabled);
  submit.addEventListener("click", () => {
    const q = ta.value.trim();
    if (q) triggerBackfill(q);
  });
  box.append(hint, ta, submit);
  updateDisabled();
}

async function triggerBackfill(question) {
  showOverlay("overlay.backfill", `<p class="muted">${escapeHtml(t("backfill.submitted"))}</p>`);
  try {
    // 不用 postJSON（它只 throw `url → status`、丢 detail，无法分流 423）：用裸 fetch 显式特判
    // 423（可写 turn 活跃）→ 本地化 backfill.busy；其余非 2xx → common.submitFail（带状态码）。
    const res = await fetch("/api/backfill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (res.status === 423) {
      $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("backfill.busy"))}</p>`;
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { job_id } = await res.json();
    await pollJob(job_id, "backfill"); // 复用默认渲染：退出码徽标 + job.output（答案 + 门禁回执）
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.submitFail", e.message))}</p>`;
  }
}

// 给一条问答气泡尾部挂「沉淀」小按钮：点击预填该轮用户问题、打开 backfill 浮层（决策P4.8-3）。
// done / stopped 收尾时调用；read-only / workspace-write 两种姿态都挂（backfill 是独立写端点）。
function appendBackfillButton(botEl) {
  if (readerMode) return; // reader 下 /api/backfill 已裁（404）：不挂「沉淀」写按钮，免无效写入口（评审 codex P3）
  if (botEl.querySelector(".bubble-backfill")) return; // 幂等：done 之后又 stopped 不重复挂
  const question = botEl.dataset.question || "";
  const btn = document.createElement("button");
  btn.className = "bubble-backfill";
  btn.type = "button";
  // 水滴沉入横线图标（#i-backfill，贴「沉淀」意象）+ 文案。
  btn.innerHTML = `<svg class="ico"><use href="#i-backfill"/></svg>`;
  const label = document.createElement("span");
  label.textContent = t("chat.backfill");
  btn.appendChild(label);
  btn.addEventListener("click", () => openBackfill(question)); // 预填该轮问题（不搬答案原文）
  botEl.appendChild(btn);
}

// ── 投喂：粘贴正文 + 命名 → POST /api/raw → 一键 ingest（P4.1）──────────────────
//
// 顶栏「投喂」→ 浮层（文件名输入 + 正文 textarea + 存盘）。存盘期间按钮置「保存中…」并禁用
// （响应会等队列前序写作业，如在飞 ingest，可能数十秒——见 P4.1 §3.1）。409 就地给「改名/覆盖」；
// 成功后提示 + 一颗「立即 ingest」复用既有 triggerIngest。

$("#feed-btn").addEventListener("click", openFeed);

function openFeed() {
  showOverlay("overlay.feed", "");
  const box = $("#overlay-body");
  box.innerHTML = "";
  const nameInput = document.createElement("input");
  nameInput.id = "feed-name";
  nameInput.className = "feed-name";
  nameInput.type = "text";
  nameInput.placeholder = t("feed.namePlaceholder");
  nameInput.autocomplete = "off";
  const textarea = document.createElement("textarea");
  textarea.id = "feed-content";
  textarea.className = "feed-content";
  textarea.rows = 12;
  textarea.placeholder = t("feed.contentPlaceholder");
  const saveBtn = document.createElement("button");
  saveBtn.id = "feed-save";
  saveBtn.className = "feed-save";
  saveBtn.textContent = t("feed.save");
  const status = document.createElement("div");
  status.className = "feed-status";
  box.append(nameInput, textarea, saveBtn, status);

  saveBtn.addEventListener("click", () => submitFeed(false));
  nameInput.focus();
}

async function submitFeed(overwrite) {
  const name = $("#feed-name").value.trim();
  const content = $("#feed-content").value;
  const saveBtn = $("#feed-save");
  const status = $(".feed-status");
  // 复位存盘按钮（可再存下一篇）；所有出口（失败/409/成功）共用，杜绝某分支漏复位（曾因成功分支
  // 不复位、按钮永久禁用，连存多篇要重开浮层）。
  const resetSave = () => {
    saveBtn.disabled = false;
    saveBtn.textContent = t("feed.save");
  };
  if (!name || !content.trim()) {
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("feed.emptyErr"))}</span>`;
    return;
  }
  saveBtn.disabled = true;
  saveBtn.textContent = t("feed.saving"); // 会等队列前序写作业（如在飞 ingest），不可让用户以为卡死
  status.textContent = "";
  let res, data;
  try {
    res = await fetch("/api/raw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, content, overwrite }),
    });
    data = await res.json().catch(() => ({}));
  } catch (e) {
    resetSave();
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("feed.saveFail", e.message))}</span>`;
    return;
  }
  if (res.status === 409) {
    // 已存在：就地给「改名 / 覆盖」二选项（覆盖 = 带 overwrite=true 重发）。
    resetSave();
    status.innerHTML = `<span class="report-bad">${escapeHtml(data.detail || t("feed.conflictDefault"))}</span> `;
    const ow = document.createElement("button");
    ow.className = "feed-overwrite";
    ow.textContent = t("feed.overwrite");
    ow.addEventListener("click", () => submitFeed(true));
    status.appendChild(ow);
    return;
  }
  if (!res.ok) {
    resetSave();
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("feed.saveFail", data.detail || `HTTP ${res.status}`))}</span>`;
    return;
  }
  // 成功：复位按钮（可接着存下一篇）+ 提示 + 一键「立即 ingest」（复用既有 ingest 流）。存盘后
  // raw/ 列表已更新，下次开 ingest 选单即见新文件（无常驻列表，无需显式刷新）。
  resetSave();
  status.innerHTML = `<span class="report-ok">${escapeHtml(t("feed.saved", data.saved))}</span> `;
  const ingestBtn = document.createElement("button");
  ingestBtn.className = "feed-ingest";
  ingestBtn.textContent = t("feed.ingestNow");
  ingestBtn.addEventListener("click", () => triggerIngest(data.saved));
  status.appendChild(ingestBtn);
}

// ── 暂存区（P4.6）：浏览 workspace/ → 审阅/修订/晋级为源/删除 → ingest 串联 ──────────
//
// 顶栏「暂存区」→ 浮层：① 待解析 uploads/（[问 agent 解析] 回填 composer / [晋级为源]（仅 .md）/ 🗑）
// + ② 待晋级 parsed/（[预览]/[让 agent 修订] 回填 composer/[晋级为源]/🗑 + 勾选「合并选中…」）。
// 复用既有 #overlay / triggerIngest / pollJob / 投喂 slug-409 交互，不引新组件、不改两栏（决策P4-3）。
// 晋级/删除是宿主写：可写 turn 跑动期（workspace-write + chatStreaming）置灰，对应后端层③ 423（§7.3）。

let stagingOpen = false; // 暂存区浮层是否打开：修订 turn 收尾（done/stopped）后据此自动重拉刷新
let stagingPath = null;  // 当前浏览目录（相对根，如 workspace/uploads/第一章）；null = 根视图（uploads/parsed 两段）

function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// 宿主写动作在可写 turn 跑动期是否应置灰（晋级/删除/ingest）——对应后端层③ 423（§7.3）。
function hostWriteBlocked() {
  return chatStreaming && currentMode === "workspace-write";
}

// 回填 composer（[问 agent 解析] / [让 agent 修订] / [合并选中] 共用）：把指令塞进输入框、聚焦、收起浮层。
// 当前 read-only 时先提示切 /mode workspace-write（解析/修订都需可写会话，§7.2 ①）。
function fillComposer(text, { needWritable = false } = {}) {
  $("#overlay").classList.add("hidden");
  const ta = $("#chat-input");
  ta.value = text;
  autoGrowInput();
  ta.focus();
  if (needWritable && currentMode !== "workspace-write") {
    addMsg("note", t("staging.needWritableNote"));
  }
}

$("#staging-btn").addEventListener("click", openStaging);

async function openStaging() {
  stagingPath = null; // 每次从顶栏打开都回到根视图
  showOverlay("overlay.staging", `<p class="muted">${escapeHtml(t("staging.loading"))}</p>`);
  stagingOpen = true; // 须在 showOverlay 之后（它会清 stagingOpen）
  loadStaging();
}

// 浮层关闭时清 stagingOpen（done/stopped 不再误刷已关的浮层）。挂一次，幂等。
$("#overlay-close").addEventListener("click", () => { stagingOpen = false; });
$("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay") stagingOpen = false; });

// 拉当前目录（stagingPath）并渲染。目录已被删/失效（4xx）→ 退回根视图重拉一次（避免卡在坏路径）。
async function loadStaging() {
  const qs = stagingPath ? `?path=${encodeURIComponent(stagingPath)}` : "";
  let data;
  try {
    data = await getJSON(`/api/workspace${qs}`);
  } catch (e) {
    if (stagingPath !== null) { stagingPath = null; return loadStaging(); } // 坏路径 → 回根
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("staging.loadFail", e.message))}</p>`;
    return;
  }
  renderStaging(data);
}

// 进入子目录 / 返回上一级。返回到 scratch 根（workspace/uploads|parsed）即回根两段视图。
function stagingEnter(path) { stagingPath = path; loadStaging(); }
function stagingUp() {
  if (!stagingPath) return;
  const parent = stagingPath.slice(0, stagingPath.lastIndexOf("/"));
  stagingPath = (parent === "workspace/uploads" || parent === "workspace/parsed") ? null : parent;
  loadStaging();
}

// 修订 turn 收尾（done/stopped）后，若暂存区仍开着则自动重拉刷新当前目录（决策P4.6-9）。
async function refreshStagingIfOpen() {
  if (!stagingOpen || $("#overlay").classList.contains("hidden")) return;
  loadStaging();
}

function renderStaging(data) {
  overlayRepaint = () => renderStaging(data); // 语言切换纯重渲染（吃缓存 data，不重拉 /api/workspace）
  const box = $("#overlay-body");
  box.innerHTML = "";
  const blocked = hostWriteBlocked();
  if (blocked) {
    const warn = document.createElement("p");
    warn.className = "muted";
    warn.textContent = t("staging.busyBanner");
    box.appendChild(warn);
  }

  if (data.root) {
    // 根视图：两段（uploads / parsed）。每段列其**直接子项**（子目录可点入、文件带操作）。
    renderSection(box, t("staging.uploadsHead"), "uploads", "workspace/uploads",
      data.uploads || [], blocked, t("staging.uploadsEmpty"));
    renderSection(box, t("staging.parsedHead"), "parsed", "workspace/parsed",
      data.parsed || [], blocked, t("staging.parsedEmpty"), t("staging.parsedSub"));
  } else {
    // 目录视图：面包屑 + 返回上一级 + 该目录直接子项（一级一级点）。
    const crumb = document.createElement("div");
    crumb.className = "stage-crumb";
    const up = document.createElement("button");
    up.className = "stage-act";
    up.textContent = t("staging.up");
    up.addEventListener("click", stagingUp);
    const label = document.createElement("span");
    label.className = "stage-crumb-path";
    label.textContent = data.path; // textContent：路径按字面显示
    crumb.append(up, label);
    box.appendChild(crumb);
    renderSection(box, "", data.base, data.path, data.items || [], blocked, t("staging.dirEmpty"));
  }

  const flow = document.createElement("p");
  flow.className = "stage-flow muted";
  flow.textContent = t("staging.flow");
  box.appendChild(flow);
}

// 渲染一段目录列表：子目录行（可点入 + 整删）在前，文件行（按 base 决定 uploads/parsed 操作）在后。
// `base` ∈ {uploads, parsed} 决定文件操作集；`dirPath` 是本段所在目录（合并产物落此目录）。
function renderSection(box, headerText, base, dirPath, items, blocked, emptyText, sub) {
  if (headerText) box.appendChild(stagingHeader(headerText, sub));
  if (!items.length) { box.appendChild(stagingEmpty(t("staging.emptyWrap", emptyText))); return; }
  const selected = new Set();
  const mergeBtn = document.createElement("button");
  mergeBtn.className = "stage-merge";
  mergeBtn.textContent = t("staging.merge");
  mergeBtn.disabled = true;
  const updateMerge = () => { mergeBtn.disabled = selected.size < 2; };
  let parsedFiles = 0;
  for (const it of items) {
    if (it.is_dir) {
      box.appendChild(renderFolderRow(it, blocked));
    } else if (base === "uploads") {
      box.appendChild(renderUploadRow(it, blocked));
    } else {
      parsedFiles++;
      box.appendChild(renderParsedRow(it, blocked, selected, updateMerge));
    }
  }
  if (base === "parsed" && parsedFiles) {
    // 合并（复用 heal 勾选子集骨架，决策P4.6-9）：勾 ≥2 个 → 预填 agent 建议名（可改）→ 回填 composer。
    mergeBtn.addEventListener("click", () => {
      const picks = [...selected];
      if (picks.length < 2) return;
      const suggested = "合并-" + picks.map((p) => p.split("/").pop().replace(/\.md$/i, "")).join("-") + ".md";
      const name = window.prompt(t("staging.mergePrompt"), suggested);
      if (!name) return;
      const list = picks.join("、");
      fillComposer(t("staging.mergeCmd", list, `${dirPath}/${name}`), { needWritable: true });
    });
    const mergeBar = document.createElement("div");
    mergeBar.className = "stage-mergebar";
    mergeBar.appendChild(mergeBtn);
    box.appendChild(mergeBar);
  }
}

// 子目录行：文件夹图标 + 名（点入）+ 🗑（整目录删除，需确认）。
function renderFolderRow(it, blocked) {
  const row = document.createElement("div");
  row.className = "stage-row stage-folder";
  const ico = document.createElement("span");
  ico.className = "stage-folder-ico";
  ico.innerHTML = '<svg class="ico"><use href="#i-folder"/></svg>';
  const nm = document.createElement("button");
  nm.className = "stage-foldername";
  nm.textContent = `${it.name}/`; // textContent：目录名按字面显示
  nm.title = t("tip.enterDir");
  nm.addEventListener("click", () => stagingEnter(it.path));
  const spacer = document.createElement("span");
  spacer.className = "stage-size"; // 占位把 🗑 推到右侧
  row.append(ico, nm, spacer, dirTrashButton(it, blocked));
  return row;
}

// 🗑 整目录删除：DELETE /api/workspace/dir（递归，需确认；单写者 + 层③ 423）。可写 turn 跑动期置灰。
function dirTrashButton(it, blocked) {
  const btn = document.createElement("button");
  btn.className = "stage-trash";
  btn.textContent = "🗑";
  btn.title = blocked ? t("staging.writeBusy") : t("tip.delDir");
  btn.disabled = blocked;
  btn.addEventListener("click", async () => {
    if (!window.confirm(t("staging.delDirConfirm", it.name))) return;
    btn.disabled = true;
    try {
      const res = await fetch(`/api/workspace/dir?path=${encodeURIComponent(it.path)}`, { method: "DELETE" });
      if (res.status === 423) { btn.disabled = false; btn.title = t("staging.writableRetry"); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      loadStaging(); // 重拉刷新（目录连同子项已删）
    } catch (e) {
      btn.disabled = false;
      btn.title = t("staging.delFail", e.message);
    }
  });
  return btn;
}

function stagingHeader(text, sub) {
  const h = document.createElement("div");
  h.className = "stage-head";
  h.textContent = text;
  if (sub) {
    const s = document.createElement("span");
    s.className = "stage-sub";
    s.textContent = `（${sub}）`;
    h.appendChild(s);
  }
  return h;
}

function stagingEmpty(text) {
  const p = document.createElement("p");
  p.className = "muted stage-empty";
  p.textContent = text;
  return p;
}

// 暂存区行内动作按钮图标化：填 svg 图标，`desc` 一句说明走原生 title（hover 悬停显示，
// 浏览器自动定位、不被 .overlay-body 滚动容器裁切）；`label` 短名走 aria-label（无障碍可读名）。
// `blocked` 时把「写作业进行中」并进 title，仍保留说明（满足 hover 显说明的诉求）。
function setStageIcon(btn, iconId, label, desc, blocked) {
  btn.classList.add("stage-iconbtn");
  btn.innerHTML = `<svg class="ico" aria-hidden="true"><use href="#${iconId}"/></svg>`;
  btn.title = blocked ? `${desc}（${t("staging.writeBusy")}）` : desc;
  btn.setAttribute("aria-label", label);
}

// uploads/ 行：徽章 + 文件名 + [问 agent 解析]（+ .md 额外 [晋级为源]）+ 🗑。
function renderUploadRow(it, blocked) {
  const row = document.createElement("div");
  row.className = "stage-row";
  // 图像 → 缩略图（经 /api/workspace/raw 取原字节）；其它 → 文件类型角标。
  const ico = makeFileIcon(it.name, {
    isImage: it.kind === "image",
    thumbUrl: `/api/workspace/raw?path=${encodeURIComponent(it.path)}`,
  });
  ico.classList.add("stage-ico");
  const nm = document.createElement("span");
  nm.className = "stage-name";
  nm.textContent = it.name; // textContent：文件名按字面显示，无注入
  nm.title = it.name;
  const sz = document.createElement("span");
  sz.className = "stage-size muted";
  sz.textContent = fmtBytes(it.bytes);
  const parse = document.createElement("button");
  parse.className = "stage-act";  // P4.6.1：宿主确定性解析（非「问 agent」），点击即建解析作业
  setStageIcon(parse, "i-play", t("staging.parse"), t("tip.parse"), blocked);
  parse.disabled = blocked;
  parse.addEventListener("click", () => triggerParse(it.path));
  row.append(ico, nm, sz, parse);
  // 退化路径（§6 / §7.2 ①）：uploads/*.md 也可直接晋级（source 校验只需 workspace/ + .md + 存在）。
  if (it.kind === "text" && /\.md$/i.test(it.name)) {
    row.appendChild(promoteButton(it, blocked));
  }
  row.appendChild(trashButton(it.path, row, blocked));
  return row;
}

// parsed/ 行：勾选框 + 文件名 + [预览] + [让 agent 修订] + [晋级为源] + 🗑。
function renderParsedRow(it, blocked, selected, updateMerge) {
  const row = document.createElement("div");
  row.className = "stage-row";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.className = "stage-pick";
  cb.title = t("tip.pickMerge");
  cb.addEventListener("change", () => {
    if (cb.checked) selected.add(it.path);
    else selected.delete(it.path);
    updateMerge();
  });
  const ico = makeFileIcon(it.name, {
    isImage: it.kind === "image",
    thumbUrl: `/api/workspace/raw?path=${encodeURIComponent(it.path)}`,
  });
  ico.classList.add("stage-ico");
  const nm = document.createElement("span");
  nm.className = "stage-name";
  nm.textContent = it.name;
  nm.title = it.name;
  const preview = document.createElement("button");
  preview.className = "stage-act";
  setStageIcon(preview, "i-eye", t("staging.preview"), t("tip.preview"));
  preview.addEventListener("click", () => previewWorkspaceFile(it.path));
  const revise = document.createElement("button");
  revise.className = "stage-act";
  setStageIcon(revise, "i-pencil", t("staging.revise"), t("tip.revise"));
  revise.addEventListener("click", () => {
    const what = window.prompt(t("staging.revisePrompt", it.name), "");
    if (what === null || !what.trim()) return;
    fillComposer(t("staging.reviseCmd", it.path, what.trim()), { needWritable: true });
  });
  // 断链检查（P4.6.1，决策P4.6.1-5）：拆分/合并后图片可能悬空/错位，一键检查 + 重整（确定性、宿主写）。
  const relink = document.createElement("button");
  relink.className = "stage-act";
  setStageIcon(relink, "i-link", t("staging.relink"), t("tip.relink"));
  relink.addEventListener("click", () => checkRelink(it, relink));
  row.append(cb, ico, nm, preview, revise, relink, promoteButton(it, blocked), trashButton(it.path, row, blocked));
  return row;
}

// 断链检查（决策P4.6.1-5）：GET image-lint → 行内显示悬空/错位计数；可重整时给「重整图片」按钮。
// 再点收起（与晋级表单同 toggle 行为）。
async function checkRelink(it, anchorBtn) {
  const row = anchorBtn.parentElement;
  const existing = row.querySelector(".stage-relink-status");
  if (existing) { existing.remove(); return; } // 再点收起
  const status = document.createElement("div");
  status.className = "stage-relink-status stage-promote-status";
  status.textContent = t("staging.relinkChecking");
  row.appendChild(status);
  let data;
  try {
    data = await getJSON(`/api/workspace/image-lint?file=${encodeURIComponent(it.path)}`);
  } catch (e) {
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.relinkFail", e.message))}</span>`;
    return;
  }
  status.innerHTML = "";
  const dangling = (data.dangling || []).length;
  const misplaced = (data.misplaced || []).length;
  if (!dangling && !misplaced) {
    status.innerHTML = `<span class="report-ok">${escapeHtml(t("staging.relinkClean"))}</span>`;
    return;
  }
  if (misplaced) {
    const m = document.createElement("span");
    m.className = "muted";
    m.textContent = t("staging.relinkMisplaced", misplaced) + " ";
    status.appendChild(m);
  }
  if (dangling) {
    const d = document.createElement("span");
    d.className = "report-bad";
    d.textContent = t("staging.relinkDangling", dangling) + " ";
    status.appendChild(d);
  }
  if (data.needs_relocalize) { // 仅错位可自动修；悬空（文件缺失）重整无能为力，只提示
    const go = document.createElement("button");
    go.className = "stage-act";
    go.textContent = t("staging.relinkGo");
    go.disabled = hostWriteBlocked();
    if (go.disabled) go.title = t("staging.writeBusy");
    go.addEventListener("click", () => runRelocalize(it, go, status));
    status.appendChild(go);
  }
}

async function runRelocalize(it, go, status) {
  go.disabled = true;
  go.textContent = t("staging.relinking");
  try {
    const res = await fetch("/api/workspace/relocalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file: it.path }),
    });
    if (res.status === 423) {
      go.disabled = false; go.textContent = t("staging.relinkGo");
      status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.writableRetryDot"))}</span>`;
      return;
    }
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    status.innerHTML = `<span class="report-ok">${escapeHtml(t("staging.relinkDone"))}</span>`;
    setTimeout(loadStaging, 600); // 重整改了 md/图目录 → 刷新暂存区
  } catch (e) {
    go.disabled = false; go.textContent = t("staging.relinkGo");
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.relinkFail", e.message))}</span>`;
  }
}

// 「晋级为源」按钮：点开行内晋级表单（名/出处 + 确认）；复用投喂 slug-409 交互。
function promoteButton(it, blocked) {
  const btn = document.createElement("button");
  btn.className = "stage-act stage-promote";
  setStageIcon(btn, "i-arrow-right", t("staging.promote"), t("tip.promote"), blocked);
  btn.disabled = blocked;
  btn.addEventListener("click", () => openPromoteForm(it, btn));
  return btn;
}

// 🗑 删 scratch：DELETE /api/workspace/file（单写者 + 层③ 423）。可写 turn 跑动期置灰。
function trashButton(path, row, blocked) {
  const btn = document.createElement("button");
  btn.className = "stage-trash";
  btn.textContent = "🗑";
  btn.title = blocked ? t("staging.writeBusy") : t("tip.delFile");
  btn.disabled = blocked;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
      if (res.status === 423) { btn.disabled = false; btn.title = t("staging.writableRetry"); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      row.remove();
    } catch (e) {
      btn.disabled = false;
      btn.title = t("staging.delFail", e.message);
    }
  });
  return btn;
}

async function previewWorkspaceFile(path) {
  stagingOpen = false; // 看预览期间暂停自动刷新：否则并发 turn 的 done/stopped 会把预览刷回列表
  overlayRepaint = null; // 预览态不挂重画闭包：避免语言切换把预览刷回暂存区列表（返回后由 renderStaging 重置）
  const box = $("#overlay-body");
  box.innerHTML = `<p class="muted">${escapeHtml(t("staging.loadingPreview"))}</p>`;
  // 「返回暂存区」恢复自动刷新并重拉当前目录——成败两路都挂上，避免预览失败时无路可回、
  // 且 stagingOpen 永久停在 false 令本会话后续 turn 收尾不再刷新（评审）。
  const back = document.createElement("button");
  back.className = "stage-act";
  back.textContent = t("staging.backToStaging");
  back.addEventListener("click", () => { stagingOpen = true; loadStaging(); });
  let data;
  try {
    data = await getJSON(`/api/workspace/file?path=${encodeURIComponent(path)}`);
  } catch (e) {
    box.innerHTML = "";
    const err = document.createElement("p");
    err.className = "report-bad";
    err.textContent = t("staging.previewFail", e.message);
    box.append(back, err);
    return;
  }
  box.innerHTML = "";
  const title = document.createElement("div");
  title.className = "stage-head";
  title.textContent = path; // textContent：路径按字面显示
  // Markdown ⇄ 源码 切换条：默认 Markdown 富渲染；源码视图按 textContent 转义显示原始 md（无注入）。
  const toggle = document.createElement("div");
  toggle.className = "stage-preview-toggle";
  const mdBtn = document.createElement("button");
  mdBtn.className = "stage-act";
  mdBtn.textContent = t("staging.viewMd");
  const srcBtn = document.createElement("button");
  srcBtn.className = "stage-act";
  srcBtn.textContent = t("staging.viewSrc");
  toggle.append(mdBtn, srcBtn);
  const view = document.createElement("div");
  let mode = "markdown";
  const paint = () => {
    mdBtn.classList.toggle("active", mode === "markdown");
    srcBtn.classList.toggle("active", mode === "source");
    if (mode === "markdown") {
      view.className = "stage-preview rendered";
      view.innerHTML = data.html; // render_page 已 sanitize（同 /api/page）
    } else {
      view.className = "stage-preview source";
      view.innerHTML = "";
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = data.source || ""; // textContent：原始 md 逐字、转义、无注入
      pre.appendChild(code);
      view.appendChild(pre);
    }
  };
  mdBtn.addEventListener("click", () => { mode = "markdown"; paint(); });
  srcBtn.addEventListener("click", () => { mode = "source"; paint(); });
  box.append(back, title, toggle, view);
  paint();
}

// 行内晋级表单：名（slug 预填 stem）+ 出处（可选，空则后端回退 source）+ 确认；409 引导改名/覆盖（失真告警）。
function openPromoteForm(it, anchorBtn) {
  const existing = anchorBtn.parentElement.querySelector(".stage-promote-form");
  if (existing) { existing.remove(); return; } // 再点收起
  const form = document.createElement("div");
  form.className = "stage-promote-form";
  const stem = it.name.replace(/\.md$/i, "");
  const nameIn = document.createElement("input");
  nameIn.type = "text";
  nameIn.value = stem;
  nameIn.placeholder = t("staging.promoteNamePh");
  const originIn = document.createElement("input");
  originIn.type = "text";
  originIn.placeholder = t("staging.promoteOriginPh");
  const go = document.createElement("button");
  go.className = "stage-act";
  go.textContent = t("staging.promoteConfirm");
  const status = document.createElement("div");
  status.className = "stage-promote-status";
  go.addEventListener("click", () =>
    submitPromote(it.path, nameIn.value.trim(), originIn.value, false, go, status)
  );
  form.append(nameIn, originIn, go, status);
  anchorBtn.parentElement.appendChild(form);
  nameIn.focus();
}

async function submitPromote(source, name, origin, overwrite, go, status) {
  if (!name) { status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.promoteNameEmpty"))}</span>`; return; }
  go.disabled = true;
  go.textContent = t("staging.promoting");
  status.textContent = "";
  const reset = () => { go.disabled = false; go.textContent = t("staging.promoteConfirm"); };
  let res, data;
  try {
    const body = { name, source, overwrite };
    if (origin.trim()) body.origin = origin;
    res = await fetch("/api/raw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    data = await res.json().catch(() => ({}));
  } catch (e) {
    reset();
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.promoteFail", e.message))}</span>`;
    return;
  }
  if (res.status === 423) {
    reset();
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.writableRetryDot"))}</span>`;
    return;
  }
  if (res.status === 409) {
    // 默认引导改名（开新源）；覆盖须显式确认 + 失真告警（决策P4.6-10）。
    reset();
    status.innerHTML = `<span class="report-bad">${escapeHtml(data.detail || t("staging.promoteConflict"))}</span>${escapeHtml(t("staging.promoteRenameOr"))}`;
    const ow = document.createElement("button");
    ow.className = "stage-act";
    ow.textContent = t("staging.overwriteRisk");
    ow.title = t("tip.overwrite");
    ow.addEventListener("click", () => {
      if (window.confirm(t("staging.overwriteConfirm")))
        submitPromote(source, name, origin, true, go, status);
    });
    status.appendChild(ow);
    return;
  }
  if (!res.ok) {
    reset();
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.promoteFail", data.detail || `HTTP ${res.status}`))}</span>`;
    return;
  }
  // 成功：原地变「✓ 已晋级 + 立即 ingest」。
  status.innerHTML = `<span class="report-ok">${escapeHtml(t("staging.promoted", data.saved))}</span> `;
  go.remove();
  const ingestBtn = document.createElement("button");
  ingestBtn.className = "stage-act";
  ingestBtn.textContent = t("staging.ingestNow");
  ingestBtn.addEventListener("click", () => triggerIngest(data.saved));
  status.appendChild(ingestBtn);
}

// ── 问答 / 多轮会话（fetch 流式读 SSE）─────────────────────────────────────

let conversationId = null;
let chatLoadToken = 0; // 每次切换/新建自增；历史气泡异步回放回来若 token 已变则丢弃（防迟到覆盖）
let chatStreaming = false; // 一轮在飞时为 true：发送按钮切「停止」、回车不再发新轮
let pendingStop = false; // 首轮 id 未回时点了「停止」：标记待停，start 帧回填 id 后立即补发
let currentMode = "read-only"; // 当前会话姿态（P4.5）：徽标显示、/mode 切换更新
let defaultMode = "read-only"; // 进程 --mode 默认（启动 /api/info 拉取）：新会话开局姿态
let readerMode = false; // 只读多会话部署（P4.9）：启动 /api/info 的 reader 字段；驱动隐藏写/历史/维护 chrome
                        // 与 ?c= 按-id 恢复（决策P4.9-9/12）。安全边界是端点 404，这只是 UX。

// 会话 id ↔ 浏览器 URL 同步：把当前会话写进 ?c=<id>，刷新即据此懒恢复续聊（startup 读取）。
// 用 replaceState（不入浏览器历史栈）——右栏 wiki 走自家 history 数组，与浏览器前进/后退互不干扰。
function syncConversationUrl() {
  const url = new URL(window.location.href);
  if (conversationId) url.searchParams.set("c", conversationId);
  else url.searchParams.delete("c");
  // 注意：本文件顶部有模块级 `let history = []`（右栏 wiki 视图栈），遮蔽了 window.history，
  // 故这里必须显式走 window.history，否则 history.replaceState 落到那个数组上、抛 not a function。
  window.history.replaceState(null, "", url);
}

// 唯一改写 conversationId 的入口：改值即同步 URL（含置 null → 抹掉 ?c=）。
function setConversation(id) {
  conversationId = id;
  syncConversationUrl();
}

// 姿态徽标（P4.5）：随 /mode 翻转更新；workspace-write 用醒目色提示「Agent 可写」。
function setModeBadge(mode) {
  currentMode = mode || "read-only";
  const el = $("#mode-badge");
  if (!el) return;
  el.textContent = currentMode;
  el.classList.toggle("writable", currentMode === "workspace-write");
  el.title = currentMode === "workspace-write" ? t("mode.writeTip") : t("mode.readonlyTip");
}

// 只读多会话部署（P4.9）：隐藏写 / 历史枚举 / 维护诊断 chrome——纯 UX（安全边界是端点 404、不是隐藏）。
// 保留：新会话 / 发送 / Wiki 导航 / 语言。用 inline display:none（稳胜 .btn 的 display 规则，
// [hidden] 属性会被 .btn 的 display 覆盖、未必生效）。决策P4.9-9/10/11/17。
function applyReaderMode() {
  const hideIds = [
    "history-btn",                                          // 枚举他人会话 → 破隔离（决策P4.9-3）
    "feed-btn", "ingest-btn", "heal-btn", "backfill-btn", "audit-btn", "staging-btn", // 写端点（reader 下 404）
    "attach-btn",                                           // 上传是写（404）
    "graph-btn",                                            // 重建是写者（404）；不给死链（决策P4.9-11）
    "mode-badge",                                           // 姿态恒只读、/mode 已禁可写 → 冻结隐藏（决策P4.9-4）
  ];
  for (const id of hideIds) {
    const el = document.getElementById(id);
    if (el) el.style.display = "none";
  }
  // check/health/lint 维护诊断按钮（决策P4.9-10）：端点只读保留、仅隐藏按钮。
  for (const el of document.querySelectorAll("[data-report]")) el.style.display = "none";
  // 收掉顶栏分隔符，避免隐藏后留连续竖线。
  for (const el of document.querySelectorAll(".topbar .act-sep")) el.style.display = "none";
}

function startNewConversation() {
  // 仅清本地视图、开启新会话——**绝不** DELETE 旧会话：P4.2 起 DELETE 会级联删盘，删的是
  // 持久历史，普通"切换/新建"不该销毁记录。旧会话持久化开时已落盘、可从"历史会话"再开；
  // 持久化关时它仍是 live、列在历史里（进程退出即清）。要永久删除走历史列表的"删除"。
  chatLoadToken++; // 作废任何在飞的历史气泡回放（用户已开新会话）
  setConversation(null);
  $("#chat-log").innerHTML = "";
  clearAttachments(); // 弃掉未发送的附件徽章（新会话不继承上一会话的待发附件）
  setModeBadge(defaultMode); // 新会话开局 = 进程默认姿态（决策P4.5-8）
}
$("#chat-new").addEventListener("click", startNewConversation);

// ── 历史会话（P4.2）：列出内存 ∪ 盘上会话，点开续聊、删除级联删盘 ────────────────
$("#history-btn").addEventListener("click", openHistory);

async function openHistory() {
  showOverlay("overlay.history", `<p class="muted">${escapeHtml(t("history.loading"))}</p>`);
  let conversations;
  try {
    ({ conversations } = await getJSON("/api/conversations"));
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("history.loadFail", e.message))}</p>`;
    return;
  }
  renderHistory(conversations);
  overlayRepaint = () => renderHistory(conversations); // 语言切换纯重渲染（吃缓存 conversations）
}

// 纯渲染历史会话列表（标题是内容、按字面显示；空态/徽标/按钮是 chrome）。
function renderHistory(conversations) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  if (!conversations.length) {
    box.innerHTML = `<p class="muted">${escapeHtml(t("history.empty"))}</p>`;
    return;
  }
  for (const c of conversations) {
    const row = document.createElement("div");
    row.className = "conv-row";
    const meta = document.createElement("div");
    meta.className = "conv-meta";
    const title = document.createElement("button"); // 标题即打开入口：点标题续聊，省去独立「打开」按钮
    title.className = "conv-title";
    title.textContent = c.title || t("history.untitled"); // textContent：标题按字面显示，无注入
    title.addEventListener("click", () => openConversation(c));
    const tag = document.createElement("span");
    tag.className = "conv-tag";
    // live 条目报真实轮次，冷条目报消息总数（list_sessions 给 message_count，非轮次）。
    const count = c.live ? t("history.turns", c.turns ?? 0) : t("history.messages", c.messages ?? 0);
    tag.textContent = c.live ? t("history.live", count) : t("history.cold", count);
    meta.append(title, tag);
    const del = document.createElement("button");
    del.className = "conv-del";
    del.textContent = "🗑"; // 图标化：删除该会话（图标不译，title 提示走 i18n）
    del.title = t("history.delete");
    del.setAttribute("aria-label", t("history.delete"));
    del.addEventListener("click", () => deleteConversation(c.id, row));
    row.append(meta, del);
    box.appendChild(row);
  }
}

async function openConversation(c) {
  $("#overlay").classList.add("hidden");
  // 点开自己正在的会话：不清屏、不误删、不重拉——否则会把当前可见的对话记录抹掉。
  if (c.id === conversationId) {
    $("#chat-input").focus();
    return;
  }
  await restoreConversation(c);
}

// 切到某会话并回放其历史气泡：仅切本地 id（下一条提问即触发后端透明 rebuild 懒恢复）。**绝不**
// DELETE 任何会话——它仍要留在历史里，普通切换不该销毁其持久记录（要永久删除走历史列表的"删除"）。
// 抽出供「历史会话」点开与启动时 ?c= 懒恢复共用。
async function restoreConversation(c) {
  const tok = ++chatLoadToken;
  setConversation(c.id);
  const log = $("#chat-log");
  log.innerHTML = "";
  $("#chat-input").focus();
  // 切会话即刷新姿态徽标（评审 P2）：否则徽标停留在上一个会话的姿态——一个 workspace-write 的 live
  // 会话切过去仍显 read-only（或反之），正是徽标该指示写能力时误导。先乐观置进程默认（冷会话恢复即
  // 此姿态），再据 /info 校正：live 会话报真实 _mode、冷会话报 default_mode（均含 mode 字段）。
  // 失败/已切走不阻断回放，徽标退回默认。
  setModeBadge(defaultMode);
  getJSON(`/api/chat/${encodeURIComponent(c.id)}/info`)
    .then((info) => { if (tok === chatLoadToken && info && info.mode) setModeBadge(info.mode); })
    .catch(() => {});
  // 回放历史气泡（user/assistant）：拉该会话的消息，逐条上屏。失败仅降级为一条提示、不阻断续聊。
  try {
    const { messages } = await getJSON(`/api/conversations/${encodeURIComponent(c.id)}/messages`);
    if (tok !== chatLoadToken) return; // 已切走（开了别的会话/新会话），丢弃迟到回放
    if (!messages.length) {
      addMsg("note", t("conv.loadedEmpty", c.title || t("history.untitled")));
      return;
    }
    for (const m of messages) {
      if (m.role === "user") {
        addMsg("user", m.content);
      } else {
        const el = addMsg("bot", "");
        if (m.html !== undefined) {  // 富排版（[[页]]→站内链）；缺则回退纯文本
          el.classList.add("rendered");
          el.innerHTML = m.html;
        } else {
          el.textContent = m.content;
        }
      }
    }
  } catch (e) {
    if (tok !== chatLoadToken) return;
    addMsg("note", t("conv.loadedErr", c.title || t("history.untitled"), e.message));
  }
}

async function deleteConversation(id, row) {
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
    if (id === conversationId) setConversation(null); // 删的是当前会话则清掉本地引用（连带抹 ?c=）
    row.remove();
    if (!$("#overlay-body").querySelector(".conv-row")) {
      $("#overlay-body").innerHTML = `<p class="muted">${escapeHtml(t("history.empty"))}</p>`;
    }
  } catch (e) {
    // 经 textContent 赋值，浏览器自会转义；勿再 escapeHtml（否则显示字面 &lt; 实体）。
    row.querySelector(".conv-del").textContent = t("history.deleteFail", e.message);
  }
}

function submitChat(override) {
  // 一轮在飞时不重复发送：回车与「发送」共用此守卫，避免在同一会话上叠起并发轮次（服务端
  // conv.lock 会把第二轮挂住、前端则堆出空 bot 气泡）。停止只由按钮点击触发（见下），回车
  // 流式中一律按空操作处理——不让回车误把当前轮停掉。输入保留不清。
  if (chatStreaming) return;
  // override（如「让 Agent 修复」发的 REPAIR_PROMPT）直接作消息发，不读输入框、不走斜杠解析。
  if (typeof override === "string" && override) { sendChat(override); return; }
  const input = $("#chat-input");
  const msg = input.value.trim();
  const atts = pendingAttachments.slice(); // 快照本轮附件（仅已上传成功者在册）
  if (!msg && !atts.length) return; // 空消息且无附件 → 不发
  // 斜杠命令前置解析（决策P4.4-1）：以 `/` 开头**一律**本地处理、**绝不进 /api/chat**——不打 LLM、
  // 不占对话轮次、不入历史。**即便有待发附件**也走本地（绝不把斜杠命令当消息连同附件发给 LLM）；
  // 附件保留 pending、留给下一条真消息。
  if (msg.startsWith("/")) { input.value = ""; autoGrowInput(); handleSlash(msg); return; }
  input.value = "";
  autoGrowInput();
  // 附件已快照，清 composer 徽章行；不回收缩略图 blob URL（气泡回显沿用）。
  clearAttachments({ revoke: false });
  sendChat(msg, atts);
}

// ── 斜杠命令（P4.4）：镜像 agentao 交互 CLI 的 8 条只读命令，全部本地解析、不进 /api/chat ──
//
// 展示类（/help）纯客户端；生命周期类（/new /clear）复用既有逻辑；自省类（/status /context
// /skills /tools /mode）调只读端点（有会话 GET /api/chat/{id}/info，否则 app 级 GET /api/info），
// 按命令取字段渲染成 note 气泡。/compact 与未知命令一律本地「未知命令」提示（决策P4.4-5）。

// /help 列这 8 条（**无** /compact，决策P4.4-5）。函数式：每次按当前语言取词（P4.7）。
function slashHelpLines() {
  return [
    t("slash.help.help"),
    t("slash.help.new"),
    t("slash.help.clear"),
    t("slash.help.status"),
    t("slash.help.context"),
    t("slash.help.skills"),
    t("slash.help.tools"),
    t("slash.help.mode"),
  ];
}

// 命令输出气泡：buildFn 用 textContent 填充各行/项（杜绝注入），落对话流、不开浮层（决策P4.4-2）。
function addNote(buildFn) {
  const div = document.createElement("div");
  div.className = "msg note";
  buildFn(div);
  const log = $("#chat-log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

// 多行纯文本 note：每行一个 <div>（textContent，安全）。
function noteLines(lines) {
  return addNote((div) => {
    for (const ln of lines) {
      const p = document.createElement("div");
      p.textContent = ln;
      div.appendChild(p);
    }
  });
}

async function handleSlash(raw) {
  const body = raw.trim();
  const cmd = (body.slice(1).split(/\s+/)[0] || "").toLowerCase();
  addMsg("cmd", body); // 回显键入的命令（cmd 样式），与其只读输出 note 区分
  switch (cmd) {
    case "help":
      return noteLines(slashHelpLines());
    case "new":
      startNewConversation();
      return noteLines([t("slash.newDone")]);
    case "clear":
      return slashClear();
    case "mode": {
      // 带参 → 切换姿态（POST /api/chat/{id}/mode）；不带参 → 报当前姿态（只读自省）。
      const arg = (body.slice(1).split(/\s+/)[1] || "").toLowerCase();
      if (arg) return slashSetMode(arg);
      return slashInfo(cmd);
    }
    case "status":
    case "context":
    case "skills":
    case "tools":
      return slashInfo(cmd);
    default:  // 含 /compact（决策P4.4-5）与一切未知命令
      return noteLines([t("slash.unknown", body)]);
  }
}

// /clear：有活动会话 → DELETE 级联删盘（== 删当前 + 新建）；无活动会话退化为 /new（决策P4.4-4）。
// **不**触 memory_manager（Web 无记忆 UI、越权，与 CLI /clear 清记忆刻意不对齐）。
async function slashClear() {
  if (!conversationId) {
    startNewConversation();
    return noteLines([t("slash.clearNoSession")]);
  }
  const id = conversationId;
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    return noteLines([t("slash.clearFail", e.message)]);
  }
  startNewConversation(); // 先清屏 + 置 conversationId=null，再把确认 note 落进新会话视图
  noteLines([t("slash.clearDone")]);
}

// /mode <值>：运行时翻姿态（P4.5 决策P4.5-5）。仅 read-only / workspace-write 合法；无活动会话
// 或冷会话先提示续聊（端点 404/409）。成功后更新徽标 + note 回报新姿态。
async function slashSetMode(arg) {
  if (arg !== "read-only" && arg !== "workspace-write") {
    return noteLines([t("slash.modeInvalidArg", arg)]);
  }
  if (!conversationId) {
    return noteLines([t("slash.modeNoSession")]);
  }
  let res;
  try {
    res = await fetch(`/api/chat/${encodeURIComponent(conversationId)}/mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: arg }),
    });
  } catch (e) {
    return noteLines([t("slash.modeFail", e.message)]);
  }
  if (res.status === 409) return noteLines([t("slash.modeInactive")]);
  if (res.status === 422) return noteLines([t("slash.modeIllegal")]);
  if (!res.ok) return noteLines([t("slash.modeFailHttp", res.status)]);
  const { mode } = await res.json();
  setModeBadge(mode);
  return noteLines([
    mode === "workspace-write" ? t("slash.modeToWrite") : t("slash.modeToRead"),
  ]);
}

// 自省类：有会话取会话级 info、否则取 app 级 info，按命令渲染。冷会话（live:false）的
// context/skills/tools 为 null → 提示「续聊一轮以恢复」（决策P4.4-7）。
async function slashInfo(cmd) {
  const url = conversationId
    ? `/api/chat/${encodeURIComponent(conversationId)}/info`
    : "/api/info";
  let info;
  try {
    info = await getJSON(url);
  } catch (e) {
    return noteLines([t("slash.infoFail", cmd, e.message)]);
  }
  renderSlashInfo(cmd, info);
}

function renderSlashInfo(cmd, info) {
  const appLevel = info.live === undefined; // /api/info 无 live 字段（无会话/无 agent）
  const cold = info.live === false;         // 盘上-only 冷会话（部分信息）

  if (cmd === "mode") {
    if (!appLevel && info.mode) setModeBadge(info.mode); // 会话级 info 同步徽标
    const hint = info.mode === "workspace-write" ? t("slash.modeHintWrite") : t("slash.modeHintRead");
    return noteLines([t("slash.modeLine", info.mode || "read-only", hint)]);
  }
  if (cmd === "status") {
    const lines = [];
    if (appLevel) {
      lines.push(t("slash.kb", info.kb_name));
      lines.push(t("slash.model", info.model || t("slash.modelUnspecified")));
      lines.push(t("slash.mode", info.mode));
      // reader 下 /api/info 移除 conversations/max_conversations（决策P4.9-9）：字段缺失则跳过本行，
      // 不渲染 `undefined/undefined`。非 reader 下二字段仍在、照常显示（不回归）。
      if (info.conversations !== undefined) {
        lines.push(t("slash.sessions", info.conversations, info.max_conversations));
      }
      lines.push(t("slash.noSessionHint"));
    } else {
      if (info.mode) setModeBadge(info.mode);
      lines.push(t("slash.model", info.model || t("slash.modelUnknown")));
      lines.push(t("slash.mode", info.mode));
      lines.push(t("slash.turnsMessages", info.turns ?? "—", info.messages ?? "—"));
      if (info.context) {
        const c = info.context;
        lines.push(t("slash.contextLine", c.estimated_tokens, c.max_tokens, c.usage_percent));
      } else if (cold) {
        lines.push(t("slash.coldHint"));
      }
    }
    return noteLines(lines);
  }
  // context / skills / tools 是 agent 级事实：无会话 → 提示开会话；冷会话 → 提示续聊恢复。
  if (appLevel) {
    return noteLines([t("slash.needSession", cmd)]);
  }
  if (cold) {
    return noteLines([t("slash.coldNeedResume", cmd)]);
  }
  if (cmd === "context") return renderSlashContext(info.context);
  if (cmd === "skills") return renderSlashSkills(info.skills);
  if (cmd === "tools") return renderSlashTools(info.tools);
}

function renderSlashContext(c) {
  if (!c) return noteLines([t("slash.noContext")]);
  const bd = c.token_breakdown || {};
  return noteLines([
    t("slash.ctxEstimate", c.estimated_tokens, c.max_tokens, c.usage_percent),
    t("slash.ctxBreakdown", bd.system ?? 0, bd.messages ?? 0, bd.tools ?? 0, bd.total ?? 0),
  ]);
}

function renderSlashSkills(skills) {
  if (!skills) return noteLines([t("slash.noSkills")]);
  return addNote((div) => {
    const head = document.createElement("div");
    head.textContent = t("slash.skillsHead", skills.active.length, skills.available.length);
    div.appendChild(head);
    for (const s of skills.available) {
      const row = document.createElement("div");
      row.className = "slash-skill" + (s.active ? " active" : "");
      // 名/描述是后端内容、按字面显示；● / ○ 是标记。
      row.textContent = `${s.active ? "● " : "○ "}${s.name}${s.description ? " — " + s.description : ""}`;
      div.appendChild(row);
    }
  });
}

function renderSlashTools(tools) {
  if (!tools) return noteLines([t("slash.noTools")]);
  return addNote((div) => {
    const head = document.createElement("div");
    head.textContent = t("slash.toolsHead", tools.length);
    div.appendChild(head);
    for (const tool of tools) {
      const row = document.createElement("div");
      // blocked ∈ {true,false,"unknown"}：true=只读禁（灰显）、unknown=无法判定（标注）、false=可用。
      row.className = "slash-tool" + (tool.blocked === true ? " blocked" : "");
      let tag = "";
      if (tool.blocked === true) tag = t("slash.toolBlocked");
      else if (tool.blocked === "unknown") tag = t("slash.toolUnknown");
      row.textContent = `${tool.name}${tag ? " " + tag : ""}${tool.description ? " — " + tool.description : ""}`;
      div.appendChild(row);
    }
  });
}

// 发送/停止双态切换：流式中把 #chat-send 文案切「停止」、加 .stopping 样式；data-mode 供点击
// 处理分流。复位时清禁用，让按钮重新可点。
function setChatSending(sending) {
  const btn = $("#chat-send");
  chatStreaming = sending;
  if (!sending) pendingStop = false; // 流结束：清掉未消费的待停标志（防跨轮残留）
  btn.dataset.mode = sending ? "stop" : "send";
  // 发送↑ / 停止■ 的图标切换由 .stopping 类驱动（见 app.css .ico-send/.ico-stop），不改 innerHTML——
  // 否则会抹掉按钮内的 <svg> 图标。
  btn.setAttribute("aria-label", sending ? t("chat.stop") : t("chat.send"));
  btn.title = sending ? t("tip.stop") : t("tip.send");
  btn.classList.toggle("stopping", sending);
  btn.disabled = false;
  $("#attach-btn").disabled = sending; // 流式中禁上传（避免在飞轮里改附件）
  if (sending) refreshStagingIfOpen(); // 可写 turn 起跑：暂存区开着则重渲染、置灰宿主写动作（§7.3）
}

// 停止：POST /api/chat/{id}/stop 置位服务端取消令牌。不在此复位按钮——等流尾的 stopped/done
// 帧由 sendChat 的 finally 统一复位（避免与在飞流抢状态）。停止请求飞行中先禁用防重复点。
async function stopChat() {
  const btn = $("#chat-send");
  btn.disabled = true; // 停止请求飞行中先禁用防重复点（图标保持 ■，禁用态即反馈）
  // 首轮 id 由 start 帧回填，可能尚未到：标记待停，start 处理处一拿到 id 就补发本请求，
  // 而不是静默放弃（否则用户点了停止却毫无反应）。按钮已显示「停止中…」给出反馈。
  if (!conversationId) { pendingStop = true; return; }
  try {
    await fetch(`/api/chat/${encodeURIComponent(conversationId)}/stop`, { method: "POST" });
  } catch (_e) {
    // 停止请求本身失败不致命：当前轮仍会自然跑完，按钮由流尾复位。
  }
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  submitChat();
});

// 「停止」态下点按钮 = 中断当前轮（而非发新轮）：拦掉默认 submit、直接 stopChat。
$("#chat-send").addEventListener("click", (e) => {
  if ($("#chat-send").dataset.mode === "stop") {
    e.preventDefault();
    stopChat();
  }
});

// 回车直接发送；Option(Alt)-Enter / Shift-Enter 插入换行。直接调 submitChat()，不走
// form.requestSubmit()——后者 Safari 16 前不存在，调用即抛、裸回车看似"没反应"（这正是
// Mac 上发不出去的根因）。e.isComposing / keyCode 229（中文输入法回车确认候选词）一律放行
// 默认行为，绝不误发。
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;
  if (e.altKey || e.shiftKey) {
    // 换行：手动在光标处插入 \n（裸回车已被我们接管，不能靠默认行为换行）。
    e.preventDefault();
    const ta = e.target;
    const { selectionStart: s, selectionEnd: t, value } = ta;
    ta.value = value.slice(0, s) + "\n" + value.slice(t);
    ta.selectionStart = ta.selectionEnd = s + 1;
    autoGrowInput(); // 换行后随内容增高
    return;
  }
  e.preventDefault();
  submitChat();
});

// ── 输入框自增高 + 文件上传/附件（OpenAI 风格 composer，P4.6 Web 文件上传）──────────────
//
// 「附件」语义（agentao 附件约定，镜像 chahua）：上传文件先经 POST /api/upload 落
// workspace/uploads/，发送时服务端把每个附件以 <attachment uri="…" [mimetype="…"]/> 标签追加进
// 发给 agent 的消息；**图像**附件额外走 arun(images=) 视觉通道（base64），模型不支持视觉时由
// agentao 自动以同格式标签降级重试（宿主/前端不做能力探测）。徽章在 composer 内可移除（图像
// 显示本地缩略图）；发送后清空、用户气泡下回显。

// 文本域随内容自增高（rows=1 起，至 200px 封顶后内部滚动）。
function autoGrowInput() {
  const ta = $("#chat-input");
  ta.style.height = "auto";
  const max = 200;
  ta.style.height = Math.min(ta.scrollHeight, max) + "px";
  ta.style.overflowY = ta.scrollHeight > max ? "auto" : "hidden";
}
$("#chat-input").addEventListener("input", autoGrowInput);
autoGrowInput(); // 初始同步一次高度

// 待发送附件（仅上传成功者在册）：{rel, name, kind, thumb, visionOversize}。
// rel = workspace/uploads/<名>；thumb = 图像的本地 blob URL（缩略图，镜像 chahua）。
const pendingAttachments = [];

// 图像扩展名白名单（与服务端 _IMAGE_EXT_TO_MIME / agentao 视觉通道同口径）。
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp"]);
// 视觉单图上限（同 agentao.media_limits.MAX_IMAGE_BYTES）：超出仍可上传（≤50MiB），但不走
// 视觉通道、agent 只看 <attachment> 文本引用——徽章上预先提示（镜像 chahua visionOversize）。
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;

function isImageName(name) {
  const i = name.lastIndexOf(".");
  return i > 0 && IMAGE_EXTS.has(name.slice(i + 1).toLowerCase());
}

// ── 文件类型图标（统一：图像→缩略图，其它→彩色文件类型角标，三处共用：暂存区 / 上传徽章 / 气泡）──
//
// 扩展名 → 类别（决定角标配色），角标文字取扩展名大写（≤4 字）。常见办公/压缩/代码/媒体各成一色。
const FILE_CAT = {
  pdf: "pdf",
  doc: "doc", docx: "doc", rtf: "doc", odt: "doc", pages: "doc",
  xls: "xls", xlsx: "xls", csv: "xls", tsv: "xls", ods: "xls",
  ppt: "ppt", pptx: "ppt", key: "ppt", odp: "ppt",
  zip: "zip", tar: "zip", gz: "zip", tgz: "zip", "7z": "zip", rar: "zip", bz2: "zip", xz: "zip",
  md: "md", markdown: "md",
  txt: "txt", text: "txt", log: "txt", rst: "txt", org: "txt",
  json: "code", yaml: "code", yml: "code", toml: "code", xml: "code", html: "code", htm: "code",
  css: "code", scss: "code", js: "code", mjs: "code", ts: "code", tsx: "code", jsx: "code",
  py: "code", sh: "code", bash: "code", c: "code", h: "code", cpp: "code", go: "code",
  rs: "code", java: "code", rb: "code", php: "code", lua: "code", sql: "code",
  png: "img", jpg: "img", jpeg: "img", gif: "img", webp: "img", svg: "img", bmp: "img",
  tiff: "img", tif: "img", ico: "img", heic: "img",
  mp3: "audio", wav: "audio", flac: "audio", m4a: "audio", ogg: "audio", aac: "audio",
  mp4: "video", mov: "video", mkv: "video", webm: "video", avi: "video", m4v: "video",
};

function fileKindInfo(name) {
  const base = name.slice(name.lastIndexOf("/") + 1); // 取 basename：name 可能含子目录（暂存区嵌套）
  const dot = base.lastIndexOf(".");
  const ext = dot > 0 ? base.slice(dot + 1).toLowerCase() : "";
  return { ext, cat: FILE_CAT[ext] || "file", label: ext ? ext.toUpperCase().slice(0, 4) : "FILE" };
}

// 文件类型角标：页形 + 扩展名文字（textContent 安全），按类别上色（见 app.css .file-ico[data-cat]）。
function fileTypeBadge(name) {
  const { cat, label } = fileKindInfo(name);
  const span = document.createElement("span");
  span.className = "file-ico";
  span.dataset.cat = cat;
  span.textContent = label;
  return span;
}

// 统一文件图标：有缩略图 URL 且是图像 → <img> 缩略图（解码失败回退角标）；否则文件类型角标。
function makeFileIcon(name, { thumbUrl = null, isImage = false } = {}) {
  if (isImage && thumbUrl) {
    const img = document.createElement("img");
    img.className = "file-thumb";
    img.src = thumbUrl;
    img.alt = "";
    img.addEventListener("error", () => img.replaceWith(fileTypeBadge(name)), { once: true });
    return img;
  }
  return fileTypeBadge(name);
}

function showAttachList() {
  const ul = $("#attach-list");
  ul.hidden = ul.children.length === 0;
}

// revoke=false 供「发送」路径：附件快照已交给气泡回显，缩略图 blob URL 须继续存活。
function clearAttachments({ revoke = true } = {}) {
  if (revoke) {
    for (const a of pendingAttachments) if (a.thumb) URL.revokeObjectURL(a.thumb);
  }
  pendingAttachments.length = 0;
  const ul = $("#attach-list");
  if (ul) { ul.innerHTML = ""; ul.hidden = true; }
}

// 建一枚徽章 DOM（占位「上传中」态；图像直接显示本地缩略图）。返回各节点引用，供上传成败后改写。
function addAttachChip(name, thumb) {
  const li = document.createElement("li");
  li.className = "attach-chip";
  const icon = document.createElement("span");
  icon.className = "attach-icon";
  if (thumb) {
    const img = document.createElement("img");
    img.className = "attach-thumb";
    img.src = thumb;
    img.alt = "";
    // 解码失败（坏图/不支持的编码）→ 降级为文件类型角标。
    img.addEventListener("error", () => { img.remove(); icon.appendChild(fileTypeBadge(name)); }, { once: true });
    icon.appendChild(img);
  } else {
    icon.textContent = "…";
  }
  const label = document.createElement("span");
  label.className = "attach-name";
  label.textContent = name; // textContent：文件名按字面显示，无注入
  label.title = name;
  const meta = document.createElement("span");
  meta.className = "attach-meta";
  meta.textContent = t("attach.uploading");
  li.append(icon, label, meta);
  $("#attach-list").appendChild(li);
  showAttachList();
  return { li, icon, label, meta, thumb };
}

// 上传成功：占位徽章定型 + 入册 pendingAttachments + 挂「×移除」。
function finalizeAttachChip(chip, att) {
  if (!chip.thumb) { chip.icon.textContent = ""; chip.icon.appendChild(fileTypeBadge(att.name)); }
  chip.meta.textContent =
    att.kind === "image"
      ? (att.visionOversize ? t("attach.visionOversize") : t("attach.image"))
      : att.kind === "binary" ? t("attach.binary") : t("attach.text");
  if (att.visionOversize) chip.meta.title = t("tip.visionOversize");
  chip.li.dataset.rel = att.rel;
  pendingAttachments.push(att);
  const rm = document.createElement("button");
  rm.type = "button";
  rm.className = "attach-remove";
  rm.textContent = "×";
  rm.title = t("tip.attachRemove");
  rm.addEventListener("click", () => {
    const i = pendingAttachments.findIndex((a) => a.rel === att.rel);
    if (i >= 0) pendingAttachments.splice(i, 1);
    if (att.thumb) URL.revokeObjectURL(att.thumb);
    chip.li.remove();
    showAttachList();
  });
  chip.li.appendChild(rm);
}

// 上传失败：徽章转错误态 + 挂「×」手动清（×时回收缩略图 blob URL）。
function errorAttachChip(chip, message) {
  if (!chip.thumb) chip.icon.textContent = "⚠";
  chip.li.classList.add("attach-error");
  chip.meta.textContent = "";
  chip.label.title = message;
  const rm = document.createElement("button");
  rm.type = "button";
  rm.className = "attach-remove";
  rm.textContent = "×";
  rm.title = message;
  rm.addEventListener("click", () => {
    if (chip.thumb) URL.revokeObjectURL(chip.thumb);
    chip.li.remove();
    showAttachList();
  });
  chip.li.appendChild(rm);
}

// 串行上传一批文件（拖拽 / 选择 / 粘贴共用）。每个文件 POST /api/upload。
// 刻意串行（镜像 chahua）：避免并发持多份大文件 body 撑内存。
async function uploadFiles(files) {
  files = Array.from(files || []);
  for (const file of files) await uploadOne(file);
}

async function uploadOne(file) {
  // 图像：上传前先出本地缩略图（blob URL，零网络往返）+ 预判视觉超限（镜像 chahua）。
  const isImage = isImageName(file.name);
  const thumb = isImage ? URL.createObjectURL(file) : null;
  const visionOversize = isImage && file.size > MAX_IMAGE_BYTES;
  const chip = addAttachChip(file.name, thumb);
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (res.status === 423) throw new Error(t("staging.writableRetry"));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    finalizeAttachChip(chip, { rel: data.saved, name: data.name, kind: data.kind, thumb, visionOversize });
  } catch (e) {
    errorAttachChip(chip, e.message);
  }
}

// 用户气泡下回显本轮已发送的附件徽章（不可移除，仅记录；图像沿用缩略图）。
function addUserAttachments(atts) {
  const div = document.createElement("div");
  div.className = "msg user-files";
  for (const a of atts) {
    const chip = document.createElement("span");
    chip.className = "user-file-chip";
    if (a.thumb) {
      const img = document.createElement("img");
      img.className = "attach-thumb";
      img.src = a.thumb;
      img.alt = "";
      chip.appendChild(img);
    } else {
      chip.appendChild(fileTypeBadge(a.name)); // 其它类型 → 文件类型角标
    }
    chip.appendChild(document.createTextNode(` ${a.name}`)); // textContent 安全：文件名按字面显示
    div.appendChild(chip);
  }
  const log = $("#chat-log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// ＋按钮 → 打开文件选择器（流式中禁用）。
$("#attach-btn").addEventListener("click", () => {
  if (chatStreaming) return;
  const fi = $("#file-input");
  fi.value = ""; // 复位，允许重选同一文件
  fi.click();
});
$("#file-input").addEventListener("change", () => { uploadFiles($("#file-input").files); });

// 拖拽上传：计数法消抖嵌套 drag 事件，只对文件拖拽响应。
const composerEl = $("#chat-form");
let dragDepth = 0;
composerEl.addEventListener("dragenter", (e) => {
  if (!e.dataTransfer || !e.dataTransfer.types || !e.dataTransfer.types.includes("Files")) return;
  e.preventDefault(); dragDepth++; composerEl.classList.add("drag-active");
});
composerEl.addEventListener("dragover", (e) => {
  if (!e.dataTransfer || !e.dataTransfer.types || !e.dataTransfer.types.includes("Files")) return;
  e.preventDefault(); e.dataTransfer.dropEffect = "copy";
});
composerEl.addEventListener("dragleave", (e) => {
  if (!e.dataTransfer || !e.dataTransfer.types || !e.dataTransfer.types.includes("Files")) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) composerEl.classList.remove("drag-active");
});
composerEl.addEventListener("drop", (e) => {
  const f = e.dataTransfer && e.dataTransfer.files;
  if (!f || !f.length) return;
  e.preventDefault(); dragDepth = 0; composerEl.classList.remove("drag-active");
  if (!chatStreaming) uploadFiles(f);
});

// 粘贴上传：剪贴板含文件（截图 / 拷贝的文件）则上传；纯文本粘贴照常进文本域。
$("#chat-input").addEventListener("paste", (e) => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  const files = [];
  for (const it of items) {
    if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); }
  }
  if (!files.length) return; // 无文件 → 让文本照常粘贴
  e.preventDefault();
  if (!chatStreaming) uploadFiles(files);
});

function addMsg(cls, text) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.textContent = text;
  const log = $("#chat-log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

// 对话气泡里的 [[wikilink]]（来自渲染后的答案）点了切到右栏 wiki 单页。
$("#chat-log").addEventListener("click", (e) => {
  const a = e.target.closest("a.wikilink[data-page]");
  if (a) { e.preventDefault(); navigate({ kind: "page", path: a.dataset.page }); }
});

async function sendChat(message, attachments) {
  if (message) addMsg("user", message);
  if (attachments && attachments.length) addUserAttachments(attachments); // 用户气泡下回显附件徽章
  const botEl = addMsg("bot", "");
  botEl.dataset.question = message || ""; // 记住该轮用户问题，供气泡「沉淀」按钮预填（P4.8）
  setChatSending(true); // 按钮切「停止」、置 chatStreaming，整轮可中断
  try {
    const body = { message, conversation_id: conversationId };
    if (attachments && attachments.length) body.attachments = attachments.map((a) => a.rel);
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        handleSSE(frame, botEl);
      }
    }
  } catch (e) {
    botEl.classList.replace("bot", "err");
    botEl.textContent = t("chat.fail", e.message);
  } finally {
    setChatSending(false); // 复位：按钮切回「发送」、清 chatStreaming/禁用
  }
}

function handleSSE(frame, botEl) {
  // 按 SSE 规范：一个事件可有多条 data: 行，按 \n 拼接；每行剥去一个前导空格。
  let event = null;
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!event || dataLines.length === 0) return;
  let payload;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return; // 跳过坏帧，绝不让单帧解析失败中断整条流、抹掉已上屏的答案
  }
  const log = $("#chat-log");
  if (event === "start") {
    // 服务端尽早回填会话 id：首轮请求里 id 为 null，拿到它「停止」按钮才能在首轮就生效。
    if (payload.conversation_id) setConversation(payload.conversation_id); // 连带写进 ?c=（刷新可恢复）
    if (pendingStop) { pendingStop = false; stopChat(); } // 首轮 id 未回时点过停止 → 现在补发
  } else if (event === "token") {
    botEl.textContent += payload;
    log.scrollTop = log.scrollHeight;
  } else if (event === "stopped") {
    // 用户主动停止：保留已流出的纯文本（不再渲染 markdown），加一行轻提示。
    if (payload.conversation_id) setConversation(payload.conversation_id);
    const note = document.createElement("span");
    note.className = "stop-note";
    note.textContent = t("chat.stopped");
    botEl.appendChild(note);
    renderWritableReceipts(payload); // 可写 turn 被停也可能已写盘：照常出 check/撤销/告警
    appendBackfillButton(botEl); // 气泡尾部挂「沉淀」按钮：预填该轮问题（P4.8）
    refreshStagingIfOpen(); // 修订 turn 收尾：暂存区开着则重拉刷新池（决策P4.6-9）
    log.scrollTop = log.scrollHeight;
  } else if (event === "done") {
    setConversation(payload.conversation_id);
    // 收尾：用服务端渲染的安全 markdown HTML 替换流式纯文本（含 [[页]]→站内链接）。
    if (payload.answer_html !== undefined) {
      botEl.classList.add("rendered"); // 切到正常空白模型 + 富排版（见 app.css）
      botEl.innerHTML = payload.answer_html;
    } else if (payload.answer) {
      botEl.textContent = payload.answer;
    }
    renderWritableReceipts(payload); // 可写 turn 收尾：check 回执 / 撤销 / immutable 告警
    appendBackfillButton(botEl); // 气泡尾部挂「沉淀」按钮：预填该轮问题（P4.8）
    refreshStagingIfOpen(); // 修订 turn 收尾：暂存区开着则重拉刷新池（决策P4.6-9）
    log.scrollTop = log.scrollHeight;
  } else if (event === "error") {
    // 记下服务端已建会话（即便本轮失败）：否则首轮失败时下次又以 null 另起新会话，堆到 503。
    if (payload.conversation_id) setConversation(payload.conversation_id);
    botEl.classList.replace("bot", "err");
    botEl.textContent = t("chat.error", payload.message);
    renderWritableReceipts(payload); // 可写 turn 抛错前的写已被服务端收尾捕获
  }
}

// 可写 turn 收尾的三类回执，**各看各的字段、相互独立**（P4.5 §8 评审 Medium）：
//   · 撤销键看 undo.available（写日志非空，独立于 check）——SCHEMA-only / check 通过的 wiki 改都能撤销
//   · 修复键看 check.violations
//   · 告警看 immutable_mutated（AGENTAO.md 被旁路改、已自动还原）
function renderWritableReceipts(payload) {
  const { check, undo, immutable_mutated: mutated } = payload || {};
  if (!check && !undo && !mutated) return; // read-only turn：无这些字段
  let refreshed = false;
  // ① immutable 告警（最严重，置顶）：AGENTAO.md 被旁路改、已自动还原。
  if (Array.isArray(mutated) && mutated.length) {
    addNote((div) => {
      div.classList.add("receipt-alert");
      const p = document.createElement("div");
      p.textContent = t("receipt.mutated", mutated.join("、"));
      div.appendChild(p);
    });
  }
  // ② check 回执 + 「让 Agent 修复」。口径＝**本轮新增**（决策P4.5-4 修订）：ok = 本轮未新增、
  // violations 只装新增；库存量（total）只作旁注（拼进文案的全是数字，无注入面）。
  // **本轮无新增（check.ok）时不出回执**（用户要求）——只静默刷新右栏页面列表，不刷屏。
  if (check) {
    const legacyNote = (n) => (n > 0 ? t("receipt.legacy", n) : "");
    if (check.ok) {
      loadPages(); refreshed = true; // 写后静默刷新右栏 wiki 列表（无回执）
    } else {
      addNote((div) => {
        div.classList.add("receipt-bad");
        const head = document.createElement("div");
        head.innerHTML =
          `<span class="report-bad">${escapeHtml(t("receipt.checkBad", check.violations.length))}</span>` +
          escapeHtml(legacyNote(check.total - check.violations.length));
        div.appendChild(head);
        for (const v of check.violations.slice(0, 20)) {
          const li = document.createElement("div");
          li.className = "violation";
          li.textContent = `· [${v.kind}] ${v.page}: ${v.detail}`;
          div.appendChild(li);
        }
        if (check.repair_prompt) {
          const btn = document.createElement("button");
          btn.className = "receipt-btn";
          btn.textContent = t("receipt.repair");
          btn.addEventListener("click", () => {
            btn.disabled = true;
            submitChat(check.repair_prompt); // 以 REPAIR_PROMPT 作下一轮消息驱动修复
          });
          div.appendChild(btn);
        }
      });
    }
  }
  // ③ 撤销键（看 undo.available、不挂 check.violations 分支，评审 Medium）。
  if (undo && undo.available) {
    addNote((div) => {
      const p = document.createElement("div");
      p.textContent = t("receipt.undoInfo", undo.paths.length, undo.paths.join("、"));
      div.appendChild(p);
      const btn = document.createElement("button");
      btn.className = "receipt-btn";
      btn.textContent = t("receipt.undo");
      btn.addEventListener("click", () => undoTurn(undo.token, btn, div));
      div.appendChild(btn);
    });
  }
  if (refreshed === false && undo && undo.available) loadPages(); // 撤销可用＝有写，刷新列表
}

// 撤销本轮写（P4.5 决策P4.5-13）：POST /api/chat/{id}/undo。409＝某文件已被后续写改动（标红）。
async function undoTurn(token, btn, box) {
  btn.disabled = true;
  if (!conversationId) { box.classList.add("receipt-bad"); return; }
  let res;
  try {
    res = await fetch(`/api/chat/${encodeURIComponent(conversationId)}/undo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch (e) {
    box.classList.add("receipt-bad");
    const p = document.createElement("div");
    p.textContent = t("undo.fail", e.message);
    box.appendChild(p);
    return;
  }
  let data = {};
  try { data = await res.json(); } catch { /* 空体 */ }
  const note = document.createElement("div");
  if (res.status === 409 && data.conflicts) {
    box.classList.add("receipt-bad");
    note.textContent = t("undo.partial", (data.conflicts || []).join("、"), (data.undone || []).join("、") || t("undo.none"));
  } else if (res.status === 409) {
    box.classList.add("receipt-bad");
    note.textContent = t("undo.stale");
  } else if (res.ok) {
    note.textContent = t("undo.done", (data.undone || []).join("、") || t("undo.none"));
  } else {
    box.classList.add("receipt-bad");
    note.textContent = t("undo.failHttp", res.status);
  }
  box.appendChild(note);
  loadPages(); // 撤销后刷新右栏 wiki 列表
}

// ── 左右两栏可拖动分隔 ───────────────────────────────────────────────────────
// 拖 #col-split 改写 .layout 的 --wiki-w（右栏宽 = 窗口右缘到光标距离），夹在
// [右栏最小, 窗口宽-左栏最小] 内；持久化 localStorage、刷新恢复；双击复位默认；方向键微调。
(function setupColumnResize() {
  const layout = $(".layout"), split = $("#col-split"), wiki = $(".wiki");
  if (!layout || !split || !wiki) return;
  const KEY = "guanlan.wikiWidth", MIN_WIKI = 240, MIN_CHAT = 320;
  const clamp = (px) => Math.max(MIN_WIKI, Math.min(px, window.innerWidth - MIN_CHAT));
  const apply = (px) => layout.style.setProperty("--wiki-w", `${clamp(px)}px`);
  const persist = (px) => localStorage.setItem(KEY, String(clamp(px)));
  const widthAt = (clientX) => layout.getBoundingClientRect().right - clientX;
  const curWidth = () => wiki.getBoundingClientRect().width;

  const saved = parseFloat(localStorage.getItem(KEY));
  if (Number.isFinite(saved)) apply(saved);  // 恢复上次；无则保留 CSS 默认 26rem

  const onMove = (e) => apply(widthAt(e.clientX));
  const onUp = (e) => {
    document.body.classList.remove("col-resizing");
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    persist(widthAt(e.clientX));
  };
  split.addEventListener("pointerdown", (e) => {
    e.preventDefault();  // 阻止拖动起手时选中文字
    document.body.classList.add("col-resizing");
    // 监听挂在 window：光标拖出 6px 细条外仍跟手，松手在任意位置都能收尾。
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });

  split.addEventListener("dblclick", () => {  // 复位到 CSS 默认（清掉内联与存储）
    layout.style.removeProperty("--wiki-w");
    localStorage.removeItem(KEY);
  });

  split.addEventListener("keydown", (e) => {  // 方向键微调（无障碍：role=separator 可聚焦）
    const dir = e.key === "ArrowLeft" ? 1 : e.key === "ArrowRight" ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    const next = curWidth() + dir * (e.shiftKey ? 64 : 24);  // ← 加宽右栏 / → 收窄
    apply(next); persist(next);
  });

  window.addEventListener("resize", () => {  // 窗口缩小致越界 → 重夹（仅当用过自定义宽度）
    const inline = parseFloat(layout.style.getPropertyValue("--wiki-w"));
    if (Number.isFinite(inline)) apply(inline);
  });
})();

// ── 启动 ────────────────────────────────────────────────────────────────────
// 界面语言（P4.7）：用「最后一次设置」初始化（首访默认 zh），刷静态 chrome + 动态面；按钮翻转 zh⇄en。
// 须在此（模块级 let 已初始化后）调用——rerenderDynamic 会读 currentMode/chatStreaming（决策P4.7-5/6）。
$("#lang-btn").addEventListener("click", toggleLang);
initLang();

// 先拉页面再进"列表"视图（首页）——启动即显示 Concept 列表。
// 仅当用户尚未导航时才进首页：否则页面慢加载期间用户已输入的搜索会被这次空首页覆盖
// （loadPages 解析后会就地重渲染当前 index 条目，故用户的搜索结果照常出现）。
loadPages().then(() => { if (histPos < 0) navigate({ kind: "index", query: "" }); });

// 启动协调（P4.9）：先拉一次 /api/info 取进程默认姿态 + reader 标志，**再**按模式恢复 ?c= 会话——
// 二者本是两段独立异步、会赛跑；合一后 reader 标志在恢复策略选择前就绪（reader 走按-id 探针、不枚举）。
(async function bootstrapSession() {
  // 拉进程默认姿态设初始徽标（P4.5）+ reader 标志（P4.9）：失败则保留 HTML/CSS 默认（非 reader / read-only）。
  let info = null;
  try { info = await getJSON("/api/info"); } catch { /* 失败：保留默认 */ }
  if (info) {
    readerMode = !!info.reader;
    if (info.mode) {
      defaultMode = info.mode;
      // 仅当尚无活动会话时才据进程默认置徽标：?c= 恢复会设该会话真实姿态（restoreConversation 的
      // 每会话 /info 校正），此处进程默认晚到一拍会覆盖回去——正是评审指出的「徽标说谎」竞态。
      if (conversationId === null) setModeBadge(info.mode);
    }
    if (readerMode) applyReaderMode(); // 隐藏写/历史/维护 chrome（决策P4.9-9/10/11/17）
  }

  // 按 ?c=<id> 懒恢复会话（刷新保留当前会话）：仅当该 id 仍可达才恢复，否则抹掉 ?c= 另起新会话——
  // 避免握着一个已删 / 进程重启后不存在的 id（下条提问会撞 404 未知会话）。
  const want = new URL(window.location.href).searchParams.get("c");
  if (!want) return;
  if (readerMode) {
    // reader 关了枚举（决策P4.9-3）：改走**按-id 探针** GET /api/chat/{id}/info（决策P4.9-12），
    // **绝不**调 /api/conversations（reader 下 404）。命中→恢复、404→抹掉 ?c= 干净起手。
    let hitInfo = null;
    try { hitInfo = await getJSON(`/api/chat/${encodeURIComponent(want)}/info`); }
    catch { /* 404/网络失败 → 下面据 hitInfo===null 抹掉 ?c= */ }
    // 拉取期间用户可能已自行开聊（SSE start 已置 conversationId / 正在流式）——绝不覆盖其会话或 URL。
    if (conversationId !== null || chatStreaming) return;
    if (hitInfo) await restoreConversation({ id: want, title: hitInfo.title });
    else setConversation(null);
    return;
  }
  // 非 reader：沿用枚举恢复（不回归既有行为）。
  let conversations = null;
  try {
    ({ conversations } = await getJSON("/api/conversations"));
  } catch { /* 列举失败：退回干净起手（除非用户已自行开聊，见下） */ }
  if (conversationId !== null || chatStreaming) return;
  const hit = conversations && conversations.find((c) => c.id === want);
  if (hit) { await restoreConversation(hit); return; }
  setConversation(null); // 失效 / 未知 id / 列举失败 → 抹掉 ?c=，干净起手
})();
