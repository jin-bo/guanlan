"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 右栏 Wiki 视图（目录/搜索/单页 + 历史栈）。
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
