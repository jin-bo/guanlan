"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 体检（P4.34，合并原 check/health/lint）+ MCP 诊断。
// ── 体检（P4.34，见 docs/P4.34-Web体检.md）──────────────────────────────────
// 合并原「校验 / 健康 / 巡检」：GET /api/doctor 一次取回分组清单（分组、去重、计数都在服务端），这里只渲染。
// 每类一行「类别 · 条数 · 修复入口」，点开看明细；修复入口只打开既有浮层，不在这里直接提交写任务。
// 全部外部文本（页名、目标、detail）一律 escapeHtml。

const DOCTOR_SHOW = 50; // 每类明细最多渲染这么多条，余下只报数（几千条一次渲染会卡）
let doctorData = null;

// t() 首参须为字面量（决策P4.7-8）：类别 / 段 / 入口的文案按 key 分支取。
function doctorCatLabel(key) {
  switch (key) {
    case "blocking": return t("doctor.cat.blocking");
    case "missing_entity": return t("doctor.cat.missing_entity");
    case "broken_link": return t("doctor.cat.broken_link");
    case "pending_raw": return t("doctor.cat.pending_raw");
    case "drift": return t("doctor.cat.drift");
    case "index": return t("doctor.cat.index");
    case "quality": return t("doctor.cat.quality");
    case "topology": return t("doctor.cat.topology");
    default: return key;
  }
}

function doctorSectionLabel(key) {
  if (key === "blocking") return t("doctor.section.blocking");
  if (key === "structure") return t("doctor.section.structure");
  return t("doctor.section.suggest");
}

function doctorActionLabel(action) {
  switch (action) {
    case "heal": return t("doctor.action.heal");
    case "ingest": return t("doctor.action.ingest");
    case "audit": return t("doctor.action.audit");
    case "reindex": return t("doctor.action.reindex");
    case "dropsrc": return t("doctor.action.dropSources");
    case "graph": return t("doctor.action.graph");
    default: return "";
  }
}

// 修复入口：只打开既有浮层（预览与确认仍在那里）。reader 下体检按钮已隐藏（决策P4.9-10）；这里再兜一层：写入口不显示（端点未注册）。
function doctorActionAllowed(action) {
  return !!action && (action === "graph" || !readerMode);
}

// 「其余断链」也给补全入口，以 min-refs=1 打开 heal 预览：顶栏 heal 按钮收进体检后（D10），
// 只有单页引用的断链时「高频缺页」一行不显示，不留这个入口就到不了 min-refs=1。
function runDoctorAction(action, key) {
  if (action === "heal") openHealPreview(undefined, key === "broken_link" ? 1 : undefined);
  else if (action === "ingest") openIngestPicker();
  else if (action === "audit") openAuditPreview();
  else if (action === "reindex") openReindexPreview();
  else if (action === "dropsrc") openDropSourcesPreview();
  else if (action === "graph") openGraph({ view: "overview" });
}

function doctorPageLink(path) {
  if (!path) return escapeHtml(t("doctor.global"));
  if (path.startsWith("raw/")) return `<a href="#" class="doctor-raw" data-raw="${escapeHtml(path.slice(4))}">${escapeHtml(path)}</a>`;
  if (path.startsWith("wiki/")) return `<a href="#" class="doctor-page" data-page="${escapeHtml(path)}">${escapeHtml(path)}</a>`;
  return escapeHtml(path);
}

function doctorItemHtml(cat, it) {
  if (it.target !== undefined) {
    // 按目标聚合的断链 / 缺页：「[[X]] ← N 页引用」+ 前几页链接。
    const refs = it.ref_pages || [];
    const shown = refs.slice(0, 5).map(doctorPageLink).join(escapeHtml(t("doctor.sep")));
    const more = refs.length > 5 ? ` …` : "";
    return `<div class="finding"><span class="kind">[[${escapeHtml(it.target)}]]</span> ${escapeHtml(t("doctor.refs", refs.length))}${shown}${more}` +
      (it.suggestion ? `<div class="muted">${escapeHtml(it.detail)}</div>` : "") + `</div>`;
  }
  if (cat.key === "drift") {
    return `<div class="finding"><span class="kind">${escapeHtml(it.slug || "")}</span> ${doctorPageLink(it.source_page)}</div>`;
  }
  const kind = it.kind ? `<span class="kind">[${escapeHtml(it.kind)}]</span> ` : "";
  return `<div class="finding">${kind}${doctorPageLink(it.page)}${it.detail ? ": " + escapeHtml(it.detail) : ""}</div>`;
}

// openKeys：重画时沿用的展开类别（语言切换不该把用户点开的类别收起）；首画为 null，只展开必须修。
function renderDoctor(data, openKeys = null) {
  const cats = data.categories || [];
  const blocking = cats.find((c) => c.key === "blocking");
  const nBlock = blocking ? blocking.count : 0;
  const nSuggest = data.total - nBlock; // 服务端 total = 必须修 + 建议处理（不含结构提示）
  let html = nBlock
    ? `<p class="report-bad">${escapeHtml(t("doctor.bad", nBlock, data.pages_checked))}</p>`
    : `<p class="report-ok">${escapeHtml(t("doctor.ok", data.pages_checked))}</p>`;
  html += `<p class="muted">${escapeHtml(nSuggest ? t("doctor.suggest", nSuggest) : t("doctor.clean"))} · ${escapeHtml(t("doctor.elapsed", fmtElapsed(data.elapsed_s)))}</p>`;
  // 回收区一行（P4.36 §2）：信息，不计入问题数；空时也显示「0 项」。reader 下服务端不给 trash 字段。
  if (data.trash) {
    const tr = data.trash;
    const txt = tr.error ? t("doctor.trashError", tr.error) : t("doctor.trash", tr.count, fmtBytes(tr.size));
    html += `<p class="muted"><a href="#" class="doctor-trash">${escapeHtml(txt)}</a></p>`;
  }
  let section = null;
  for (const cat of cats) {
    // 为零的类别（含必须修）不显示：必须修为零时，上面的总评已说明。
    if (!cat.count) continue;
    if (cat.section !== section) {
      section = cat.section;
      html += `<h4 class="doctor-section">${escapeHtml(doctorSectionLabel(section))}</h4>`;
    }
    const act = doctorActionAllowed(cat.action)
      ? ` <button type="button" class="doctor-act" data-action="${escapeHtml(cat.action)}" data-cat="${escapeHtml(cat.key)}">${escapeHtml(doctorActionLabel(cat.action))}</button>`
      : "";
    const items = (cat.items || []).slice(0, DOCTOR_SHOW).map((it) => doctorItemHtml(cat, it)).join("");
    const more = cat.count > DOCTOR_SHOW ? `<p class="muted">${escapeHtml(t("doctor.more", cat.count - DOCTOR_SHOW))}</p>` : "";
    const open = (openKeys ? openKeys.has(cat.key) : cat.section === "blocking") ? " open" : "";
    html += `<details class="doctor-cat" data-key="${escapeHtml(cat.key)}"${open}><summary><span>${escapeHtml(doctorCatLabel(cat.key))}</span> <b>${cat.count}</b>${act}</summary>${items}${more}</details>`;
  }
  return html;
}

function paintDoctor() {
  const body = $("#overlay-body");
  const shown = body.querySelectorAll("details.doctor-cat");
  const openKeys = shown.length ? new Set([...shown].filter((d) => d.open).map((d) => d.dataset.key)) : null;
  body.innerHTML = renderDoctor(doctorData, openKeys) + `<div id="doctor-quotes"></div>`;
  paintDoctorQuotes(openKeys);
  body.querySelectorAll(".doctor-act").forEach((b) => b.addEventListener("click", (e) => {
    e.preventDefault(); // 按钮在 <summary> 里：不要顺带折叠 / 展开
    runDoctorAction(b.dataset.action, b.dataset.cat);
  }));
  body.querySelectorAll("a.doctor-page").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    $("#overlay").classList.add("hidden");
    navigate({ kind: "page", path: a.dataset.page });
  }));
  body.querySelectorAll("a.doctor-trash").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    $("#overlay").classList.add("hidden");
    openTrashEntry(null);
  }));
  body.querySelectorAll("a.doctor-raw").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    $("#overlay").classList.add("hidden");
    navigate({ kind: "raw", name: a.dataset.raw });
  }));
}

// 用时保留一位小数；不足 0.1 秒显示「<0.1」，免得出现「用时 0 秒」。
function fmtElapsed(s) {
  const n = Number(s) || 0;
  return n < 0.1 ? "<0.1" : n.toFixed(1);
}

let doctorToken = 0; // 每次打开体检递增：关掉再开时，先发的那次迟到响应不得覆盖后发的

// 取数期间用户可能已关浮层、开了别的浮层或重开了体检：不是最新一次、或标题 key 不再是体检就不覆盖。
function doctorOverlayGone(tok) {
  return tok !== doctorToken || $("#overlay").classList.contains("hidden") || $("#overlay-title").dataset.i18n !== "overlay.doctor";
}

async function openDoctor() {
  const tok = ++doctorToken;
  showOverlay("overlay.doctor", `<p class="muted">${escapeHtml(t("common.running"))}</p>`);
  try {
    // 深查的上次状态一并取回：关掉体检再打开，进行中的核对接着显示进度、做完的直接显示结果。
    // reader 下端点未注册（体检按钮本也隐藏），不去取；取不到就当没跑过，不影响体检本身。
    const [data, quotes] = await Promise.all([
      getJSON("/api/doctor"),
      readerMode ? Promise.resolve(null) : getJSON("/api/doctor/quotes").catch(() => null),
    ]);
    if (doctorOverlayGone(tok)) return;
    doctorData = data;
    doctorQuotes = quotes;
    // 先注册再画：深查在跑时 paintDoctor 要据「浮层归体检」接着轮询（取数已成功，不违背「成功后才注册」）。
    overlayRepaint = paintDoctor; // 语言切换纯重渲染（吃缓存 data）
    paintDoctor();
  } catch (e) {
    if (doctorOverlayGone(tok)) return;
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.fail", e.message))}</p>`;
  }
}

$("#doctor-btn").addEventListener("click", openDoctor);

// ── 体检深查 · 引文核对（P4.34 §6.1）────────────────────────────────────────
// 点击才跑：POST /api/doctor/quotes 在服务端起一个单飞的只读作业（不进任务中心、不取写锁），这里每秒轮询
// GET 直到做完。结果只留服务端最近一次：关掉浮层再开仍在，重开体检时接着轮询。分组在服务端做，这里只渲染。
// 引语、页名、raw 路径都是库内文本，一律 escapeHtml。

const DOCTOR_QUOTES_POLL_MS = 1000;
const DOCTOR_QUOTE_CHARS = 80; // 明细里每条引语最多显示这么多字
let doctorQuotes = null; // 服务端最近一次状态 {state, started_at, finished_at, running_s, result, error}；null = 没取到
let doctorQuotesTimer = null;

function doctorQuotesCatLabel(key) {
  switch (key) {
    case "strict": return t("doctor.quotes.cat.strict");
    case "review": return t("doctor.quotes.cat.review");
    case "unchecked": return t("doctor.quotes.cat.unchecked");
    default: return key;
  }
}

// 原因代号 → 文案（t() 首参须为字面量，决策P4.7-8）。代号与 quotes._REASONS 的键一致。
function doctorQuoteReason(r) {
  switch (r) {
    case "strict": return t("doctor.quotes.reason.strict");
    case "label_like": return t("doctor.quotes.reason.label_like");
    case "ocr": return t("doctor.quotes.reason.ocr");
    case "unsupported": return t("doctor.quotes.reason.unsupported");
    case "misattributed": return t("doctor.quotes.reason.misattributed");
    default: return t("doctor.quotes.reason.unchecked");
  }
}

function doctorQuoteItemHtml(it) {
  const quote = it.quote.length > DOCTOR_QUOTE_CHARS ? it.quote.slice(0, DOCTOR_QUOTE_CHARS) + "…" : it.quote;
  const where = it.best_raw || (it.checked_raw || []).join(t("doctor.sep"));
  let note = where ? t("doctor.quotes.checked", where) : t("doctor.quotes.noRaw");
  if (it.coverage !== null && it.coverage !== undefined) note += t("doctor.quotes.coverage", it.coverage);
  return `<div class="finding"><span class="kind">[${escapeHtml(it.kind)}]</span> ${doctorPageLink(it.page)}:${escapeHtml(String(it.line))}` +
    ` 「${escapeHtml(quote)}」<div class="muted">${escapeHtml(note)} — ${escapeHtml(doctorQuoteReason(it.reason))}</div></div>`;
}

function fmtClock(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d) ? d.toLocaleTimeString() : "";
}

function renderDoctorQuotes(openKeys) {
  const q = doctorQuotes || { state: "idle" };
  const running = q.state === "running";
  const btnLabel = q.state === "idle" ? t("doctor.quotes.run") : t("doctor.quotes.rerun");
  let html = `<h4 class="doctor-section">${escapeHtml(t("doctor.section.deep"))}</h4>`;
  html += `<div class="doctor-deep"><span>${escapeHtml(t("doctor.quotes.title"))}</span>` +
    `<button type="button" id="doctor-quotes-run" class="doctor-act"${running ? " disabled" : ""}>${escapeHtml(btnLabel)}</button></div>`;
  if (q.state === "idle") return html + `<p class="muted">${escapeHtml(t("doctor.quotes.hint"))}</p>`;
  if (running) return html + `<p class="muted">${escapeHtml(t("doctor.quotes.running", fmtElapsed(q.running_s)))}</p>`;
  if (q.state === "failed") return html + `<p class="report-bad">${escapeHtml(t("common.fail", q.error || ""))}</p>`;
  const r = q.result || {};
  const cats = r.categories || [];
  const nStrict = (cats.find((c) => c.key === "strict") || { count: 0 }).count;
  const pct = r.candidates ? ((100 * r.passed) / r.candidates).toFixed(1) : "100.0";
  html += `<p class="${nStrict ? "report-bad" : "report-ok"}">${escapeHtml(t("doctor.quotes.summary", r.pages_checked, r.candidates, r.passed, pct))}</p>`;
  html += `<p class="muted">${escapeHtml(t("doctor.quotes.meta", fmtClock(q.finished_at), fmtElapsed(r.elapsed_s), r.misattributed || 0))}</p>`;
  if ((r.raw_unreachable || []).length) {
    html += `<p class="report-bad">${escapeHtml(t("doctor.quotes.unreachable", r.raw_unreachable.length, r.raw_unreachable.slice(0, 5).join(t("doctor.sep"))))}</p>`;
  }
  for (const cat of cats) {
    if (!cat.count) continue;
    const key = `quotes_${cat.key}`;
    const items = (cat.items || []).slice(0, DOCTOR_SHOW).map(doctorQuoteItemHtml).join("");
    // 服务端每类只带回前 50 条（count 是全量）：其余去命令行看完整清单。
    const more = cat.count > DOCTOR_SHOW ? `<p class="muted">${escapeHtml(t("doctor.quotes.more", cat.count - DOCTOR_SHOW))}</p>` : "";
    const open = (openKeys ? openKeys.has(key) : cat.key === "strict") ? " open" : "";
    html += `<details class="doctor-cat" data-key="${escapeHtml(key)}"${open}><summary><span>${escapeHtml(doctorQuotesCatLabel(cat.key))}</span> <b>${cat.count}</b></summary>${items}${more}</details>`;
  }
  return html;
}

// 只重画深查这一段（轮询每秒一次，不动上面的体检清单与其展开状态）。
function paintDoctorQuotes(openKeys = null) {
  const box = $("#doctor-quotes");
  if (!box) return;
  if (readerMode) { box.innerHTML = ""; return; }
  if (openKeys === null) {
    const shown = box.querySelectorAll("details.doctor-cat");
    if (shown.length) openKeys = new Set([...shown].filter((d) => d.open).map((d) => d.dataset.key));
  }
  box.innerHTML = renderDoctorQuotes(openKeys);
  const btn = $("#doctor-quotes-run");
  if (btn) btn.addEventListener("click", startDoctorQuotes);
  box.querySelectorAll("a.doctor-page").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    $("#overlay").classList.add("hidden");
    navigate({ kind: "page", path: a.dataset.page });
  }));
  scheduleDoctorQuotesPoll();
}

// 浮层已换主人（关掉、开了别的浮层）就停轮询：重开体检时 openDoctor 取回状态、paintDoctorQuotes 再接上。
function scheduleDoctorQuotesPoll() {
  clearTimeout(doctorQuotesTimer);
  doctorQuotesTimer = null;
  if (!doctorQuotes || doctorQuotes.state !== "running" || overlayRepaint !== paintDoctor) return;
  doctorQuotesTimer = setTimeout(async () => {
    doctorQuotesTimer = null;
    if (overlayRepaint !== paintDoctor) return;
    try {
      doctorQuotes = await getJSON("/api/doctor/quotes");
    } catch (e) {
      doctorQuotes = { state: "failed", error: e.message };
    }
    if (overlayRepaint === paintDoctor) paintDoctorQuotes();
  }, DOCTOR_QUOTES_POLL_MS);
}

async function startDoctorQuotes() {
  try {
    doctorQuotes = await postJSON("/api/doctor/quotes", {});
  } catch (e) {
    doctorQuotes = { state: "failed", error: e.message };
  }
  if (overlayRepaint === paintDoctor) paintDoctorQuotes();
}

// 图谱按钮自 P4.27 起打开 Web 内图谱面板，接线见 graph_view.js。

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
  // 状态词双语固定三档（connected / error / needs_auth），其余上游状态原样显示（t() 首参恒字面量，
  // 决策P4.7-8）。needs_auth：agentao 0.5.10 起 URL 型 server 无 Authorization 头即走 OAuth，401 后
  // 落此态——不是连接坏了，是该登录了（检查不会替用户发起登录）。
  let label = r.status;
  if (ok) label = t("mcp.statusConnected");
  else if (r.status === "error") label = t("mcp.statusError");
  else if (r.status === "needs_auth") label = t("mcp.statusNeedsAuth", r.name);
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
