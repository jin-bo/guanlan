"use strict";
// 图谱面板（P4.27，见 docs/P4.27-Web图可视化.md）。三个视图：社区总览 → 社区展开 → 单页一跳邻域。
// 数据来自只读 GET /api/graph（服务端已裁剪、聚合，前端拿到的永远是一个视图）；渲染复用 P4.20 已内置的
// ECharts（经 echarts_loader.js 的公共 loadECharts()，决策P4.27-4），graph 系列 + force 布局。
//
// 信任边界（决策P4.27-9，沿用决策P4.20-4/-25）：节点标题来自 frontmatter——LLM 写的、可能抄自第三方原文。
// 提示框钉 renderMode:"richText"（作为图元绘制、不解析 HTML），所有 formatter 只返回我们拼的纯文本；
// 面板 chrome（面包屑 / 提示）里的标题一律 escapeHtml。布局是表现、不是数据：不存坐标（决策P4.27-12）。

let graphChart = null;   // 当前 ECharts 实例（单例：换视图 / 关面板 / 开别的浮层即 dispose）
let graphState = null;   // {params, data}：当前视图，供切语言纯重绘 chrome（不重拉、不重画画布）
let graphNote = null;    // {kind, args}：加载中 / 失败等一次性提示；null = 按 graphState 显示摘要
let graphToken = 0;      // 每次取数自增；迟到响应（已切到别的视图）丢弃

// 页型配色（社区视图 / 邻域视图）。取自 app.css 的昼澜色系；未知页型统一淡墨。
const GRAPH_TYPE_COLORS = { source: "#8AA6AD", entity: "#3F9DB3", concept: "#0F4C5C", synthesis: "#C9873A" };
// 总览按社区着色：彼此可区分、与昼澜色系协调的一组色，按社区在列表中的位置轮换。
const GRAPH_COMMUNITY_COLORS = [
  "#0F4C5C", "#3F9DB3", "#C9873A", "#6B8F5E", "#8E5B8A",
  "#B5534A", "#4F6D9A", "#A38B3C", "#2E7D72", "#7A6A58",
];
const GRAPH_LABEL_MAX = 16; // 标签截断字数（提示框里给全名）

function disposeGraphChart() {
  if (graphChart) {
    try { graphChart.dispose(); } catch { /* 已 dispose → 忽略 */ }
    graphChart = null;
  }
}

function graphCssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function graphShort(s) {
  const str = String(s || "");
  return str.length > GRAPH_LABEL_MAX ? str.slice(0, GRAPH_LABEL_MAX - 1) + "…" : str;
}

function graphUrl(params) {
  const q = new URLSearchParams({ view: params.view });
  if (params.anchor) q.set("anchor", params.anchor);
  if (params.name) q.set("name", params.name);
  return `/api/graph?${q}`;
}

// ── 面板骨架与 chrome（面包屑 / 摘要 / 提示）──────────────────────────────────

function graphSkeleton() {
  // 「静态列表」指向写者专属的 /graph（重建 graph/ 后打开零 JS 的 graph.html）；reader 下不注册，不给死链。
  const staticLink = readerMode ? ""
    : `<a class="graph-static" href="/graph" target="_blank" rel="noopener" data-i18n="graph.staticList">${escapeHtml(t("graph.staticList"))}</a>`;
  return `<div class="graph-bar"><nav id="graph-crumbs" class="graph-crumbs"></nav>${staticLink}</div>`
    + `<p id="graph-note" class="graph-note muted"></p>`
    + `<div id="graph-canvas" class="graph-canvas"></div>`
    + `<p id="graph-hint" class="graph-hint muted"></p>`;
}

function graphCrumbs() {
  const items = [];
  const st = graphState;
  const view = st ? st.params.view : "overview";
  const crumb = (label, params) => params
    ? `<button type="button" class="graph-crumb" data-params="${escapeHtml(JSON.stringify(params))}">${escapeHtml(label)}</button>`
    : `<span class="graph-crumb-current">${escapeHtml(label)}</span>`;
  items.push(crumb(t("graph.overview"), view === "overview" ? null : { view: "overview" }));
  if (st && view !== "overview") {
    const comm = st.data.community;
    if (comm) {
      const label = t("graph.communityCrumb", graphShort(comm.anchor_title));
      items.push(crumb(label, view === "community" ? null : { view: "community", anchor: comm.anchor }));
    }
    if (view === "page") {
      const center = st.data.nodes.find((n) => n.id === st.data.center);
      items.push(crumb(graphShort(center ? center.title : st.data.center), null));
    }
  }
  return items.join(`<span class="graph-sep" aria-hidden="true">›</span>`);
}

function graphNoteText() {
  if (graphNote) {
    if (graphNote.kind === "loading") return t("graph.loading");
    if (graphNote.kind === "gone") return t("graph.gone");
    return t("graph.loadFail", graphNote.arg || "");
  }
  const st = graphState;
  if (!st) return "";
  const d = st.data;
  let text = "";
  if (d.view === "overview") {
    if (!d.nodes.length) text = t("graph.empty");
    else text = t("graph.summary", d.nodes.length, d.pages);
    if (d.isolated) text += t("graph.isolated", d.isolated);
  } else if (d.view === "community") {
    text = t("graph.communitySize", d.community.size);
  } else {
    text = t("graph.pageSize", d.truncated ? d.truncated.total - 1 : d.nodes.length - 1);
  }
  // 截断显式告示（决策P4.27-7）：不静默截断。
  if (d.truncated) text += t("graph.truncated", d.truncated.shown, d.truncated.total);
  return text;
}

// 纯重绘 chrome：切语言（overlayRepaint）与每次取数后都走这里；画布不动。
function paintGraphChrome() {
  const crumbs = $("#graph-crumbs");
  if (!crumbs) return; // 浮层已换主人
  crumbs.innerHTML = graphCrumbs();
  for (const b of crumbs.querySelectorAll("button.graph-crumb")) {
    b.addEventListener("click", () => loadGraphView(JSON.parse(b.dataset.params)));
  }
  $("#graph-note").textContent = graphNoteText();
  const view = graphState ? graphState.params.view : "overview";
  $("#graph-hint").textContent = view === "overview" ? t("graph.hintOverview") : t("graph.hintPage");
  const link = $("#overlay-body .graph-static");
  if (link) link.textContent = t("graph.staticList");
}

// ── 画布 ──────────────────────────────────────────────────────────────────────

function graphTooltip(p) {
  // 只返回纯文本（richText 不解析 HTML，决策P4.27-9）。
  if (p.dataType === "edge") {
    const e = p.data._g;
    if (e.weight !== undefined) return t("graph.tipLinks", e.weight);
    return e.mutual ? t("graph.tipMutual") : t("graph.tipOneWay");
  }
  const n = p.data._g;
  if (n.size !== undefined) return `${n.anchor_title}\n${t("graph.tipPages", n.size)}`;
  let text = `${n.title}\n${n.type || "?"} · ${t("graph.tipDegree", n.degree)}`;
  if (n.external !== undefined) text += `\n${t("graph.tipExternal", n.external)}`;
  return text;
}

function graphSeriesData(d) {
  if (d.view === "overview") {
    const maxSize = Math.max(1, ...d.nodes.map((n) => n.size));
    const maxW = Math.max(1, ...d.edges.map((e) => e.weight));
    // 配色按页数排位而非 id 序：最大的几个社区拿到最易区分的前几种颜色。
    const order = [...d.nodes].sort((a, b) => b.size - a.size || (a.id < b.id ? -1 : 1)).map((n) => n.id);
    const nodes = d.nodes.map((n) => ({
      name: n.id,
      symbolSize: 16 + 40 * Math.sqrt(n.size / maxSize),
      itemStyle: { color: GRAPH_COMMUNITY_COLORS[order.indexOf(n.id) % GRAPH_COMMUNITY_COLORS.length] },
      _g: n,
    }));
    const links = d.edges.map((e) => ({
      source: e.source, target: e.target,
      lineStyle: { width: 1 + 5 * Math.sqrt(e.weight / maxW) },
      _g: e,
    }));
    return { nodes, links };
  }
  const maxDeg = Math.max(1, ...d.nodes.map((n) => n.degree));
  const nodes = d.nodes.map((n) => {
    const isCenter = d.view === "page" && n.id === d.center;
    return {
      name: n.id,
      symbolSize: (isCenter ? 14 : 6) + 22 * Math.sqrt(n.degree / maxDeg),
      itemStyle: {
        color: GRAPH_TYPE_COLORS[n.type] || graphCssVar("--lan-ink-soft", "#52666B"),
        borderColor: isCenter ? graphCssVar("--lan-ink", "#1B2A2E") : undefined,
        borderWidth: isCenter ? 3 : 0,
      },
      _g: n,
    };
  });
  const links = d.edges.map((e) => ({
    source: e.source, target: e.target,
    lineStyle: { width: e.mutual ? 2 : 0.8 },
    _g: e,
  }));
  return { nodes, links };
}

function drawGraph(echarts, d) {
  disposeGraphChart();
  const el = $("#graph-canvas");
  if (!el || !d.nodes.length) return;
  graphChart = echarts.init(el, null, { renderer: "canvas" });
  const { nodes, links } = graphSeriesData(d);
  const big = links.length > 1500; // 大图关动画，力导向收敛更快（U1 实测口径，docs §7）
  graphChart.setOption({
    animation: !big,
    tooltip: { trigger: "item", renderMode: "richText", confine: true, formatter: graphTooltip },
    series: [{
      type: "graph",
      layout: "force",
      roam: true,
      draggable: true,
      data: nodes,
      links,
      label: {
        show: true,
        position: "right",
        fontSize: 11,
        color: graphCssVar("--lan-ink", "#1B2A2E"),
        formatter: (p) => graphShort(p.data._g.anchor_title || p.data._g.title),
      },
      labelLayout: { hideOverlap: true },
      lineStyle: { color: graphCssVar("--lan-ink-soft", "#52666B"), opacity: 0.35 },
      emphasis: { focus: "adjacency", lineStyle: { opacity: 0.9 } },
      force: d.view === "overview"
        ? { repulsion: 600, edgeLength: [80, 220], gravity: 0.08 }
        : { repulsion: 90, edgeLength: [30, 110], gravity: 0.12, layoutAnimation: !big },
    }],
  });
  graphChart.on("click", (p) => {
    if (p.dataType !== "node") return;
    const g = p.data._g;
    if (d.view === "overview") {
      loadGraphView({ view: "community", anchor: g.id });
    } else {
      $("#overlay").classList.add("hidden");
      disposeGraphChart();
      navigate({ kind: "page", path: g.path });
    }
  });
}

// ── 取数与入口 ────────────────────────────────────────────────────────────────

async function loadGraphView(params) {
  const tok = ++graphToken;
  graphNote = { kind: "loading" };
  paintGraphChrome();
  try {
    const [data, echarts] = await Promise.all([getJSON(graphUrl(params)), loadECharts()]);
    if (tok !== graphToken || !$("#graph-canvas")) return; // 已切走 / 浮层已换主人
    graphState = { params, data };
    graphNote = null;
    paintGraphChrome();
    drawGraph(echarts, data);
  } catch (e) {
    if (tok !== graphToken || !$("#graph-canvas")) return;
    // 锚页 / 页名已不在库里（入库改名、撤回）：社区号不作地址的代价就是这里——回总览重来（决策P4.27-6）。
    if (params.view !== "overview" && /→ 404$/.test(e.message)) {
      await loadGraphView({ view: "overview" });
      if (tok + 1 === graphToken) { graphNote = { kind: "gone" }; paintGraphChrome(); }
      return;
    }
    disposeGraphChart();
    graphNote = { kind: "fail", arg: e.message };
    paintGraphChrome();
  }
}

function openGraph(params) {
  showOverlay("overlay.graph", graphSkeleton());
  $("#overlay .overlay-card").classList.add("overlay-graph");
  graphState = null;
  overlayRepaint = paintGraphChrome; // 切语言纯重绘 chrome（吃缓存，不重拉、不重画画布）
  loadGraphView(params);
}

$("#graph-btn").addEventListener("click", () => openGraph({ view: "overview" }));
// 关浮层即释放画布（ECharts 实例持有 canvas 与事件）；开别的浮层时由 core.js showOverlay 释放。
$("#overlay-close").addEventListener("click", disposeGraphChart);
$("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay") disposeGraphChart(); });
window.addEventListener("resize", () => { if (graphChart) graphChart.resize(); });
