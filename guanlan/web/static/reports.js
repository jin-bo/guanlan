"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 零-LLM 报告（check/health/lint）+ graph 入口。
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

// ── 外部 MCP 配置诊断（P4.19，见 docs/P4.19-Web-MCP诊断.md）────────────────────
// 展示本库实际会被注入的外部 MCP server 配置 + 用户显式点一次连接检查，两件事。
// **全部外部文本（工具 description / 连接错误）一律 escapeHtml**（决策P4.19-11，P4.11 信任边界口径）。
let mcpData = null;       // GET /api/mcp 结果缓存（切语言时纯重渲染，不重拉）
let mcpResults = null;    // 最近一次连接检查结果（null=本次浮层还没检查过）
let mcpNote = "";         // 检查中/失败的一行提示，存**已解析文本**外的 kind，见 setMcpNote
let mcpNoteArg = "";
let mcpChecking = false;  // 前端单飞：检查在飞时按钮渲染成 disabled，见 runMcpCheck

// 提示存 kind 而非成品文案：切语言时 renderMcp 重新按当前语言解析（P4.7 纯重渲染约定）。
// arg 是服务端 detail 之类的**原样文本**（不翻译，如实转述）。
function setMcpNote(kind, arg) { mcpNote = kind; mcpNoteArg = arg || ""; }

function mcpNoteHtml() {
  if (!mcpNote) return "";
  if (mcpNote === "checking") return `<p class="muted">${escapeHtml(t("mcp.checking"))}</p>`;
  if (mcpNote === "busy") return `<p class="report-bad">${escapeHtml(t("mcp.checkBusy"))}</p>`;
  if (mcpNote === "extra") {
    // 附上服务端 detail：缺 extra 的**真实原因**（如装的是 mcp 1.x、原始 ImportError）就在里面，
    // 丢了它只会让用户去重装一个他明明已经装了的包、症状不变。
    const why = mcpNoteArg ? ` ${escapeHtml(mcpNoteArg)}` : "";
    return `<p class="report-bad">${escapeHtml(t("mcp.checkNeedExtra"))}${why}</p>`;
  }
  return `<p class="report-bad">${escapeHtml(t("mcp.checkFail", mcpNoteArg))}</p>`;
}

function mcpToolHtml(tool) {
  const ann = tool.annotations || {};
  const keys = Object.keys(ann);
  // annotations 只存在于连接后的 tools/list 响应里（决策P4.19-2）：缺就如实显示"无"，**不替它推断**。
  const chips = keys.length
    ? keys.map((k) => `<span class="raw-badge">${escapeHtml(k)}=${escapeHtml(String(ann[k]))}</span>`).join("")
    : `<span class="raw-badge">${escapeHtml(t("mcp.noAnnotations"))}</span>`;
  return `<div class="mcp-tool"><code>${escapeHtml(tool.name)}</code>${chips}`
    + `<div class="muted">${escapeHtml(tool.description || "")}</div></div>`;
}

function mcpResultHtml(r) {
  const ok = r.status === "connected";
  // 状态词双语固定两档（connected / error），其余上游状态原样显示（t() 首参恒字面量，决策P4.7-8）。
  let label = r.status;
  if (ok) label = t("mcp.statusConnected");
  else if (r.status === "error") label = t("mcp.statusError");
  const head = `<div class="${ok ? "report-ok" : "report-bad"}">`
    + `${escapeHtml(r.name)} · ${escapeHtml(label)} · ${escapeHtml(r.transport)}`
    + (ok ? ` · ${escapeHtml(t("mcp.toolCount", r.tools.length))}` : "") + `</div>`;
  const err = r.error ? `<div class="finding">${escapeHtml(r.error)}</div>` : "";
  const tools = r.tools.length
    ? r.tools.map(mcpToolHtml).join("")
    : (ok ? `<p class="muted">${escapeHtml(t("mcp.noTools"))}</p>` : "");
  return `<div class="mcp-result">${head}${err}${tools}</div>`;
}

function renderMcp() {
  const data = mcpData || { servers: [], config_errors: [] };
  // 顶部一行照抄决策P4.19-8 的时点语义原文，**不得**简写成"当前生效"（已有会话不会跟着变）。
  let html = `<p class="muted">${escapeHtml(t("mcp.timing"))}</p>`;
  for (const e of data.config_errors) {
    // 三档 kind 各自取词（t() 首参恒字面量，决策P4.7-8）：解析不了 / 形状不合法 / 传输定不下来。
    let msg = t("mcp.errTransport");
    if (e.kind === "json_unparsable") msg = t("mcp.errJson");
    else if (e.kind === "config_shape_invalid") msg = t("mcp.errShape");
    const who = e.name ? `${e.path} · ${e.name}` : e.path;
    html += `<div class="finding"><span class="kind">[${escapeHtml(who)}]</span> ${escapeHtml(msg)}</div>`;
  }
  if (!data.servers.length) {
    html += `<p class="muted">${escapeHtml(t("mcp.empty"))}</p>`;
  } else {
    html += `<div class="mcp-row mcp-head"><span>${escapeHtml(t("mcp.colName"))}</span>`
      + `<span>${escapeHtml(t("mcp.colScope"))}</span><span>${escapeHtml(t("mcp.colTransport"))}</span>`
      + `<span>${escapeHtml(t("mcp.colEndpoint"))}</span><span>${escapeHtml(t("mcp.colTrusted"))}</span></div>`;
    for (const s of data.servers) {
      html += `<div class="mcp-row"><span>${escapeHtml(s.name)}</span><span>${escapeHtml(s.scope)}</span>`
        + `<span>${escapeHtml(s.transport)}</span><span class="mcp-endpoint">${escapeHtml(s.endpoint || "—")}</span>`
        + `<span>${escapeHtml(s.trusted ? t("mcp.trustedYes") : t("mcp.trustedNo"))}</span></div>`;
    }
    // disabled 必须由**渲染态**给出：直接 `btn.disabled = true` 会被随后的重画连节点一起丢掉
    // （新节点没有 disabled），防连点等于没做。
    html += `<div class="mcp-actions"><button id="mcp-check" class="feed-save"${mcpChecking ? " disabled" : ""}>`
      + `${escapeHtml(t("mcp.checkBtn"))}</button>`
      + `<span class="muted">${escapeHtml(t("mcp.checkHint"))}</span></div>`;
  }
  html += mcpNoteHtml();
  if (mcpResults && mcpResults.length) {
    html += `<h4 class="mcp-result-head">${escapeHtml(t("mcp.resultHead"))}</h4>`
      + mcpResults.map(mcpResultHtml).join("");
  }
  html += `<p class="muted mcp-disclaimer">${escapeHtml(t("mcp.disclaimer"))}</p>`;
  return html;
}

// 每次重画都重新接线「检查连接」按钮（按钮在 innerHTML 里，重画即换了 DOM 节点）。
// **归属守卫**：`#overlay-body` 是全局共享的一个节点，而一次检查最长可跑到 per-server startup
// 上界（默认 60s）。若期间用户已关掉浮层、改开「巡检」，这里再无条件写 innerHTML 就会把 lint
// 报告的正文替换成 MCP 面板（标题还写着「巡检」）。`overlayRepaint === paintMcp` 恰好就是
// 「当前浮层还是 MCP 面板」——每个 opener 都会把它改写成自己的重画闭包（core.js showOverlay 先清空）。
function paintMcp() {
  if (overlayRepaint !== paintMcp) return; // 浮层已换主人：只更新内存态，不碰别人的 DOM
  $("#overlay-body").innerHTML = renderMcp();
  const btn = $("#mcp-check");
  if (btn) btn.addEventListener("click", runMcpCheck);
}

async function runMcpCheck() {
  if (mcpChecking) return; // 前端单飞：不靠服务端 409 来兜连点（那会白跑一趟并覆盖「连接中…」）
  mcpChecking = true;
  mcpResults = null;
  setMcpNote("checking");
  paintMcp();
  try {
    // 不传任何 server 定义/URL/header：端点只查当前 KB 的实际配置（决策P4.19-3）。
    const res = await fetch("/api/mcp/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (res.status === 409) { setMcpNote("busy"); return; }  // 单飞：不排队、不重试
    // 其余非 2xx 一律把服务端 detail 如实带出来：422（配置坏到解析不了）与 501（缺 extra）
    // 的可执行原因全在 detail 里，丢掉它就只剩一句「HTTP 500」式的空话。
    if (!res.ok) {
      const detail = await res.json().then((d) => (d && d.detail) || "").catch(() => "");
      if (res.status === 501) setMcpNote("extra", detail);
      else setMcpNote("fail", detail || `HTTP ${res.status}`);
      return;
    }
    const data = await res.json();
    mcpResults = data.results || [];
    setMcpNote("");
  } catch (e) {
    setMcpNote("fail", e.message);
  } finally {
    mcpChecking = false;
    paintMcp();
  }
}

$("#mcp-btn").addEventListener("click", async () => {
  mcpData = null;
  mcpResults = null;   // 每次开面板从"未检查"起手：连接结果是瞬时观测，不该看着像常驻状态
  mcpChecking = false;
  setMcpNote("");
  showOverlay("overlay.mcp", `<p class="muted">${escapeHtml(t("mcp.loading"))}</p>`);
  try {
    mcpData = await getJSON("/api/mcp");
    // **取数成功后**才注册重画闭包（与 reports/jobs/staging 各处一致）：提前注册的话，取数失败时
    // 切一次语言就会把错误提示替换成 renderMcp 对空数据的回落——「未配置任何外部 MCP server」，
    // 对一个从未成功的请求给出令人安心的错误结论。
    overlayRepaint = paintMcp; // 切语言纯重渲染（吃缓存，不重连、不重拉）
    paintMcp();
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("mcp.loadFail", e.message))}</p>`;
  }
});
