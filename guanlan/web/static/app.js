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

// ── Wiki：合并视图（Concept 列表 ⇄ 单页）+ 回退/往前历史 ────────────────────
//
// 右栏只有一个 #wiki-view：启动显示 Concept 列表；点页进单页内容；正文 [[链接]] 续进。
// 历史栈像浏览器：navigate 压栈、回退/往前移指针。视图分两种：
//   {kind:"index"}        —— 按搜索过滤的页面列表（"首页"）
//   {kind:"page", path}   —— 单页渲染

let allPages = [];          // /api/pages 缓存
let pagesError = null;      // /api/pages 加载错误（与"空库"区分，避免误显"暂无页面"）
let history = [];           // 视图历史栈；index 条目自带 query 快照，回退/往前可还原过滤态
let histPos = -1;           // 当前视图在栈中的下标
let renderToken = 0;        // 每次渲染自增；单页异步响应回来若 token 已变则丢弃（防迟到覆盖）

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
  // 还原视图时同步搜索框（回退/往前到某个 index 条目要回到它当时的过滤词）。
  if (view.kind === "index") {
    $("#wiki-search").value = view.query || "";
    renderIndex(view);
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
  const view = $("#wiki-view");
  if (pagesError) {
    view.innerHTML = `<div class="empty">加载页面失败：${escapeHtml(pagesError)}（请重试或检查服务端）</div>`;
    return;
  }
  const q = ((entry && entry.query) || "").trim().toLowerCase();
  const matched = q
    ? allPages.filter((p) => p.title.toLowerCase().includes(q) || p.path.toLowerCase().includes(q))
    : allPages;
  if (!matched.length) {
    view.innerHTML = `<div class="empty">${allPages.length ? "无匹配页面" : "暂无页面"}</div>`;
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

async function renderPage(path) {
  const tok = ++renderToken; // 本次渲染的序号
  const view = $("#wiki-view");
  view.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const data = await getJSON(`/api/page?path=${encodeURIComponent(path)}`);
    if (tok !== renderToken) return; // 已导航走（回退/搜索/续进），丢弃迟到响应
    let meta;
    if (data.meta) {
      const t = data.meta.type ? `<span class="ptype">${escapeHtml(String(data.meta.type))}</span>` : "";
      const lu = data.meta.last_updated ? `更新于 ${escapeHtml(String(data.meta.last_updated))}` : "";
      meta = `${t}<span>${escapeHtml(path)}</span> · <span>${lu}</span>`;
    } else {
      meta = `<span>${escapeHtml(path)}</span> · <span class="muted">无 frontmatter</span>`;
    }
    view.innerHTML = `<div class="page-meta">${meta}</div>` + data.html;
  } catch (e) {
    if (tok !== renderToken) return;
    view.innerHTML = `<p class="muted">打开失败：${escapeHtml(e.message)}</p>`;
  }
}

$("#wiki-home").addEventListener("click", () => navigate({ kind: "index", query: "" }));
$("#wiki-back").addEventListener("click", goBack);
$("#wiki-fwd").addEventListener("click", goForward);

$("#wiki-search").addEventListener("input", (e) => {
  const q = e.target.value;
  const cur = currentView();
  if (cur && cur.kind === "index") {
    cur.query = q; // 就地更新当前 index 条目的过滤词（不压历史，保住回退还原）
    renderIndex(cur);
  } else {
    navigate({ kind: "index", query: q }); // 在单页时回到列表态看结果（只压一次 index）
  }
});

// 站内 wikilink 导航：事件委托到合并视图，续进单页历史。
$("#wiki-view").addEventListener("click", (e) => {
  const a = e.target.closest("a.wikilink[data-page]");
  if (a) { e.preventDefault(); navigate({ kind: "page", path: a.dataset.page }); }
});

// ── 浮层 ──────────────────────────────────────────────────────────────────────

function showOverlay(title, html) {
  stagingOpen = false; // 任何其它浮层（报告/投喂/ingest/heal/历史）打开即清——openStaging 之后再置回，
  // 防 done/stopped 误把 renderStaging 灌进别的浮层 body（暂存区 P4.6）。
  $("#overlay-title").textContent = title;
  $("#overlay-body").innerHTML = html;
  $("#overlay").classList.remove("hidden");
}
$("#overlay-close").addEventListener("click", () => $("#overlay").classList.add("hidden"));
$("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay") $("#overlay").classList.add("hidden"); });

// ── 零 LLM 报告 / graph ─────────────────────────────────────────────────────

function renderReport(name, data) {
  const itemsKey = "violations" in data ? "violations" : "findings";
  const items = data[itemsKey] || [];
  const head = data.ok
    ? `<p class="report-ok">✓ ${name} 通过（${data.pages_checked} 页，无${itemsKey === "violations" ? "违规" : "建议"}）</p>`
    : `<p class="report-bad">✗ ${name}：${data.pages_checked} 页，${items.length} 条</p>`;
  const body = items.map((it) =>
    `<div class="finding"><span class="kind">[${escapeHtml(it.kind)}]</span> ${escapeHtml(it.page || "(全局)")}: ${escapeHtml(it.detail)}</div>`
  ).join("");
  return head + body;
}

document.querySelectorAll(".actions button[data-report]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const name = btn.dataset.report;
    showOverlay(name, '<p class="muted">运行中…</p>');
    try {
      showOverlay(name, renderReport(name, await getJSON(`/api/report/${name}`)));
    } catch (e) {
      showOverlay(name, `<p class="report-bad">失败：${escapeHtml(e.message)}</p>`);
    }
  });
});

$("#graph-btn").addEventListener("click", () => window.open("/graph", "_blank"));

// ── 写：ingest（从 raw/ 选一篇 → 入队 → 轮询）────────────────────────────────

$("#ingest-btn").addEventListener("click", openIngestPicker);

async function openIngestPicker() {
  showOverlay("ingest", '<p class="muted">加载 raw/…</p>');
  let files;
  try {
    ({ files } = await getJSON("/api/raw"));
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">加载 raw/ 失败：${escapeHtml(e.message)}</p>`;
    return;
  }
  const box = $("#overlay-body");
  box.innerHTML = "";
  if (!files.length) {
    box.innerHTML = '<p class="muted">raw/ 为空：把 .md 放进 raw/ 再来。</p>';
    return;
  }
  for (const f of files) {
    const row = document.createElement("div");
    row.className = "raw-pick";
    const name = document.createElement("span");
    name.textContent = `${f.name} (${f.size}B)`; // textContent：文件名按字面显示，无注入
    const btn = document.createElement("button");
    btn.textContent = "ingest";
    btn.addEventListener("click", () => triggerIngest(`raw/${f.name}`));
    row.append(name, btn);
    box.appendChild(row);
  }
}

async function triggerIngest(target) {
  showOverlay("ingest", `<p class="muted">已提交 <code>${escapeHtml(target)}</code>，排队中…</p>`);
  try {
    const { job_id } = await postJSON("/api/ingest", { target });
    await pollJob(job_id, target);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">提交失败：${escapeHtml(e.message)}</p>`;
  }
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
        const badge = ok ? '<span class="report-ok">✓ 通过</span>' : `<span class="report-bad">✗ 退出码 ${job.exit_code}</span>`;
        $("#overlay-body").innerHTML = `<p>${escapeHtml(label)} ${badge}</p><pre>${escapeHtml(job.output || "(无输出)")}</pre>`;
      }
      await loadPages(); // 写后刷新 wiki 搜索列表（heal 新建了页）
      return;
    }
    $("#overlay-body").innerHTML = `<p class="muted">${escapeHtml(label)} · ${job.state}…</p>`;
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
  showOverlay("heal", '<p class="muted">计算 worklist（零 LLM）…</p>');
  const qs = new URLSearchParams();
  if (limit) qs.set("limit", limit);
  if (minRefs) qs.set("min_refs", minRefs);
  const suffix = qs.toString() ? `?${qs}` : "";
  let data;
  try {
    data = await getJSON(`/api/heal/preview${suffix}`);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">预览失败：${escapeHtml(e.message)}</p>`;
    return;
  }
  renderHealPreview(data, limit, minRefs);
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
    go.textContent = selected.size ? `物化 ${selected.size} 个目标` : "未选择目标";
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
  refresh.textContent = "预览";
  refresh.addEventListener("click", () => openHealPreview(limitIn.value || "", minIn.value || ""));
  ctrl.append(limitIn, minIn, refresh);
  top.appendChild(ctrl);
  box.appendChild(top);

  if (!worklist.length) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = postponed.length
      ? `本批无目标；${postponed.length} 个高频缺失实体因 limit 全部推迟（提高 limit 续补）。`
      : "✓ 无可物化的缺失实体（图已充分连通或均低于阈值）。";
    box.appendChild(note);
    if (!postponed.length) return;
  } else {
    const head = document.createElement("p");
    head.textContent = `勾选要物化的高频缺失实体（默认全选，共 ${worklist.length} 个）：`;
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
      const t = document.createElement("span");
      t.className = "heal-target";
      t.textContent = `+ ${w.target}`; // textContent：目标名/文件名按字面显示，无注入
      const meta = document.createElement("span");
      meta.className = "heal-meta";
      meta.textContent = `${w.ref_count} 页引用：${(w.ref_pages || []).join("、")}`;
      bodyEl.append(t, meta);
      row.append(cb, bodyEl);
      box.appendChild(row);
    }
  }

  if (postponed.length) {
    const ph = document.createElement("p");
    ph.className = "muted";
    ph.textContent = `另有 ${postponed.length} 个因 limit 本次推迟（不可勾选，提高 limit 续补）：`;
    box.appendChild(ph);
    for (const w of postponed) {
      const row = document.createElement("div");
      row.className = "heal-item postponed";
      row.textContent = `· ${w.target}（${w.ref_count} 页引用）`;
      box.appendChild(row);
    }
  }
}

async function triggerHeal(limit, minRefs, targets) {
  showOverlay("heal", '<p class="muted">已提交，排队中（与 ingest/投喂同写者串行）…</p>');
  const body = {};
  if (limit) body.limit = Number(limit);
  if (minRefs) body.min_refs = Number(minRefs);
  // 勾选子集随请求发出；服务端仍重算 worklist 再取交集（陈旧/越界目标被丢弃，决策P4.3-3 修订）。
  if (targets && targets.length) body.targets = targets;
  try {
    const { job_id } = await postJSON("/api/heal", body);
    await pollJob(job_id, "heal", renderHealDone);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">提交失败：${escapeHtml(e.message)}</p>`;
  }
}

function renderHealDone(job) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const ok = job.exit_code === 0;
  const badge = document.createElement("p");
  badge.innerHTML = ok
    ? '<span class="report-ok">✓ 通过</span>'
    : `<span class="report-bad">✗ 退出码 ${escapeHtml(String(job.exit_code))}</span>`;
  box.appendChild(badge);

  const result = job.result;
  if (result) {
    const receipts = result.receipts || [];
    const resolved = receipts.filter((r) => r.status === "resolved");
    const still = receipts.filter((r) => r.status === "still_broken");
    const summary = document.createElement("p");
    summary.textContent = `物化 ${receipts.length} 个目标：${resolved.length} 个已解析，${still.length} 个仍断。`;
    box.appendChild(summary);
    for (const r of resolved) {
      const div = document.createElement("div");
      div.className = "heal-receipt resolved";
      div.textContent = `✓ ${r.target} → ${r.resolved_to}`; // textContent：路径无注入
      box.appendChild(div);
    }
    for (const r of still) {
      const div = document.createElement("div");
      div.className = "heal-receipt broken";
      div.textContent = `· ${r.target}（仍断：${r.reason}）`;
      box.appendChild(div);
    }
    if ((result.unexpected_writes || []).length) {
      const h = document.createElement("div");
      h.className = "heal-warn";
      h.textContent = "⚠ 非预期 wiki 写入（人工审计）：";
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
      d.textContent = `另有 ${result.postponed.length} 个缺失实体因 limit 推迟，重跑续补。`;
      box.appendChild(d);
    }
  }

  if (job.output) {
    const pre = document.createElement("pre");
    pre.textContent = job.output; // agent 散文（同 ingest）
    box.appendChild(pre);
  }
}

// ── 投喂：粘贴正文 + 命名 → POST /api/raw → 一键 ingest（P4.1）──────────────────
//
// 顶栏「投喂」→ 浮层（文件名输入 + 正文 textarea + 存盘）。存盘期间按钮置「保存中…」并禁用
// （响应会等队列前序写作业，如在飞 ingest，可能数十秒——见 P4.1 §3.1）。409 就地给「改名/覆盖」；
// 成功后提示 + 一颗「立即 ingest」复用既有 triggerIngest。

$("#feed-btn").addEventListener("click", openFeed);

function openFeed() {
  showOverlay("投喂", "");
  const box = $("#overlay-body");
  box.innerHTML = "";
  const nameInput = document.createElement("input");
  nameInput.id = "feed-name";
  nameInput.className = "feed-name";
  nameInput.type = "text";
  nameInput.placeholder = "文件名或标题（自动 slug 化 + 补 .md）";
  nameInput.autocomplete = "off";
  const textarea = document.createElement("textarea");
  textarea.id = "feed-content";
  textarea.className = "feed-content";
  textarea.rows = 12;
  textarea.placeholder = "粘贴素材正文（Markdown 文本，原样存进 raw/）…";
  const saveBtn = document.createElement("button");
  saveBtn.id = "feed-save";
  saveBtn.className = "feed-save";
  saveBtn.textContent = "存盘";
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
    saveBtn.textContent = "存盘";
  };
  if (!name || !content.trim()) {
    status.innerHTML = '<span class="report-bad">文件名与正文都不能为空。</span>';
    return;
  }
  saveBtn.disabled = true;
  saveBtn.textContent = "保存中…"; // 会等队列前序写作业（如在飞 ingest），不可让用户以为卡死
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
    status.innerHTML = `<span class="report-bad">存盘失败：${escapeHtml(e.message)}</span>`;
    return;
  }
  if (res.status === 409) {
    // 已存在：就地给「改名 / 覆盖」二选项（覆盖 = 带 overwrite=true 重发）。
    resetSave();
    status.innerHTML = `<span class="report-bad">${escapeHtml(data.detail || "同名文件已存在。")}</span> `;
    const ow = document.createElement("button");
    ow.className = "feed-overwrite";
    ow.textContent = "覆盖";
    ow.addEventListener("click", () => submitFeed(true));
    status.appendChild(ow);
    return;
  }
  if (!res.ok) {
    resetSave();
    status.innerHTML = `<span class="report-bad">存盘失败：${escapeHtml(data.detail || `HTTP ${res.status}`)}</span>`;
    return;
  }
  // 成功：复位按钮（可接着存下一篇）+ 提示 + 一键「立即 ingest」（复用既有 ingest 流）。存盘后
  // raw/ 列表已更新，下次开 ingest 选单即见新文件（无常驻列表，无需显式刷新）。
  resetSave();
  status.innerHTML = `<span class="report-ok">✓ 已存 ${escapeHtml(data.saved)}</span> `;
  const ingestBtn = document.createElement("button");
  ingestBtn.className = "feed-ingest";
  ingestBtn.textContent = "立即 ingest";
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
    addMsg("note", "提示：解析/修订需可写会话，请先 /mode workspace-write 再发送。");
  }
}

$("#staging-btn").addEventListener("click", openStaging);

async function openStaging() {
  stagingPath = null; // 每次从顶栏打开都回到根视图
  showOverlay("暂存区 · workspace", '<p class="muted">加载 workspace…</p>');
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
    $("#overlay-body").innerHTML = `<p class="report-bad">加载 workspace 失败：${escapeHtml(e.message)}</p>`;
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
  const box = $("#overlay-body");
  box.innerHTML = "";
  const blocked = hostWriteBlocked();
  if (blocked) {
    const warn = document.createElement("p");
    warn.className = "muted";
    warn.textContent = "会话写作业进行中…晋级/删除/ingest 暂不可用（稍后重试）。";
    box.appendChild(warn);
  }

  if (data.root) {
    // 根视图：两段（uploads / parsed）。每段列其**直接子项**（子目录可点入、文件带操作）。
    renderSection(box, `① 待解析 · uploads/`, "uploads", "workspace/uploads",
      data.uploads || [], blocked, "上传文件即在此暂存。");
    renderSection(box, `② 待晋级 · parsed/`, "parsed", "workspace/parsed",
      data.parsed || [], blocked, "让 agent 把 uploads/ 解析成 parsed/*.md。", "agent 解析产物，人审/修订后升源");
  } else {
    // 目录视图：面包屑 + 返回上一级 + 该目录直接子项（一级一级点）。
    const crumb = document.createElement("div");
    crumb.className = "stage-crumb";
    const up = document.createElement("button");
    up.className = "stage-act";
    up.textContent = "← 返回上一级";
    up.addEventListener("click", stagingUp);
    const label = document.createElement("span");
    label.className = "stage-crumb-path";
    label.textContent = data.path; // textContent：路径按字面显示
    crumb.append(up, label);
    box.appendChild(crumb);
    renderSection(box, "", data.base, data.path, data.items || [], blocked, "空目录。");
  }

  const flow = document.createElement("p");
  flow.className = "stage-flow muted";
  flow.textContent = "流程：上传 → 解析 →（预览/修订·拆分·合并/删冗余）→ 晋级 → ingest";
  box.appendChild(flow);
}

// 渲染一段目录列表：子目录行（可点入 + 整删）在前，文件行（按 base 决定 uploads/parsed 操作）在后。
// `base` ∈ {uploads, parsed} 决定文件操作集；`dirPath` 是本段所在目录（合并产物落此目录）。
function renderSection(box, headerText, base, dirPath, items, blocked, emptyText, sub) {
  if (headerText) box.appendChild(stagingHeader(headerText, sub));
  if (!items.length) { box.appendChild(stagingEmpty(`（空）${emptyText}`)); return; }
  const selected = new Set();
  const mergeBtn = document.createElement("button");
  mergeBtn.className = "stage-merge";
  mergeBtn.textContent = "合并选中…";
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
      const name = window.prompt("合并后文件名（agent 建议，可改；人最终定）：", suggested);
      if (!name) return;
      const list = picks.join(" 与 ");
      fillComposer(`把 ${list} 合并成一篇，写到 ${dirPath}/${name}。`, { needWritable: true });
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
  nm.title = "进入目录";
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
  btn.title = blocked ? "会话写作业进行中…" : "删除整个目录及其内容";
  btn.disabled = blocked;
  btn.addEventListener("click", async () => {
    if (!window.confirm(`删除整个目录「${it.name}/」及其全部内容？此操作不可撤销。`)) return;
    btn.disabled = true;
    try {
      const res = await fetch(`/api/workspace/dir?path=${encodeURIComponent(it.path)}`, { method: "DELETE" });
      if (res.status === 423) { btn.disabled = false; btn.title = "可写会话进行中，稍后重试"; return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      loadStaging(); // 重拉刷新（目录连同子项已删）
    } catch (e) {
      btn.disabled = false;
      btn.title = `删除失败：${e.message}`;
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
  parse.className = "stage-act";
  parse.textContent = "问 agent 解析";
  parse.addEventListener("click", () =>
    fillComposer(`把 ${it.path} 解析成 Markdown，写到 workspace/parsed/。`, { needWritable: true })
  );
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
  cb.title = "勾选以合并（≥2 个）";
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
  preview.textContent = "预览";
  preview.addEventListener("click", () => previewWorkspaceFile(it.path));
  const revise = document.createElement("button");
  revise.className = "stage-act";
  revise.textContent = "让 agent 修订";
  revise.addEventListener("click", () => {
    const what = window.prompt(`让 agent 修订 ${it.name}（如「拆成算法/范例两篇」「删第3节补小结」）：`, "");
    if (what === null || !what.trim()) return;
    fillComposer(`针对 ${it.path}：${what.trim()}（产物写回 workspace/parsed/）。`, { needWritable: true });
  });
  row.append(cb, ico, nm, preview, revise, promoteButton(it, blocked), trashButton(it.path, row, blocked));
  return row;
}

// 「晋级为源」按钮：点开行内晋级表单（名/出处 + 确认）；复用投喂 slug-409 交互。
function promoteButton(it, blocked) {
  const btn = document.createElement("button");
  btn.className = "stage-act stage-promote";
  btn.textContent = "晋级为源 →";
  btn.disabled = blocked;
  if (blocked) btn.title = "会话写作业进行中…";
  btn.addEventListener("click", () => openPromoteForm(it, btn));
  return btn;
}

// 🗑 删 scratch：DELETE /api/workspace/file（单写者 + 层③ 423）。可写 turn 跑动期置灰。
function trashButton(path, row, blocked) {
  const btn = document.createElement("button");
  btn.className = "stage-trash";
  btn.textContent = "🗑";
  btn.title = blocked ? "会话写作业进行中…" : "删除该 scratch 文件";
  btn.disabled = blocked;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
      if (res.status === 423) { btn.disabled = false; btn.title = "可写会话进行中，稍后重试"; return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      row.remove();
    } catch (e) {
      btn.disabled = false;
      btn.title = `删除失败：${e.message}`;
    }
  });
  return btn;
}

async function previewWorkspaceFile(path) {
  stagingOpen = false; // 看预览期间暂停自动刷新：否则并发 turn 的 done/stopped 会把预览刷回列表
  const box = $("#overlay-body");
  box.innerHTML = '<p class="muted">加载预览…</p>';
  // 「返回暂存区」恢复自动刷新并重拉当前目录——成败两路都挂上，避免预览失败时无路可回、
  // 且 stagingOpen 永久停在 false 令本会话后续 turn 收尾不再刷新（评审）。
  const back = document.createElement("button");
  back.className = "stage-act";
  back.textContent = "← 返回暂存区";
  back.addEventListener("click", () => { stagingOpen = true; loadStaging(); });
  let data;
  try {
    data = await getJSON(`/api/workspace/file?path=${encodeURIComponent(path)}`);
  } catch (e) {
    box.innerHTML = "";
    const err = document.createElement("p");
    err.className = "report-bad";
    err.textContent = `预览失败：${e.message}`;
    box.append(back, err);
    return;
  }
  box.innerHTML = "";
  const title = document.createElement("div");
  title.className = "stage-head";
  title.textContent = path; // textContent：路径按字面显示
  const view = document.createElement("div");
  view.className = "stage-preview rendered";
  view.innerHTML = data.html; // render_page 已 sanitize（同 /api/page）
  box.append(back, title, view);
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
  nameIn.placeholder = "源文件名（自动 slug + .md）";
  const originIn = document.createElement("input");
  originIn.type = "text";
  originIn.placeholder = "出处 origin（可选；空则回退 source 路径）";
  const go = document.createElement("button");
  go.className = "stage-act";
  go.textContent = "确认晋级";
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
  if (!name) { status.innerHTML = '<span class="report-bad">源文件名不能为空。</span>'; return; }
  go.disabled = true;
  go.textContent = "晋级中…";
  status.textContent = "";
  const reset = () => { go.disabled = false; go.textContent = "确认晋级"; };
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
    status.innerHTML = `<span class="report-bad">晋级失败：${escapeHtml(e.message)}</span>`;
    return;
  }
  if (res.status === 423) {
    reset();
    status.innerHTML = '<span class="report-bad">可写会话进行中，稍后重试。</span>';
    return;
  }
  if (res.status === 409) {
    // 默认引导改名（开新源）；覆盖须显式确认 + 失真告警（决策P4.6-10）。
    reset();
    status.innerHTML = `<span class="report-bad">${escapeHtml(data.detail || "同名源已存在。")}</span> 改名开新源，或 `;
    const ow = document.createElement("button");
    ow.className = "stage-act";
    ow.textContent = "覆盖（有失真风险）";
    ow.title = "覆盖会改写已有源：引用此源的 wiki 页可能失真，建议覆盖后重 ingest + check";
    ow.addEventListener("click", () => {
      if (window.confirm("覆盖已有源？引用此源的 wiki 页可能失真，建议覆盖后重 ingest + check。"))
        submitPromote(source, name, origin, true, go, status);
    });
    status.appendChild(ow);
    return;
  }
  if (!res.ok) {
    reset();
    status.innerHTML = `<span class="report-bad">晋级失败：${escapeHtml(data.detail || `HTTP ${res.status}`)}</span>`;
    return;
  }
  // 成功：原地变「✓ 已晋级 + 立即 ingest」。
  status.innerHTML = `<span class="report-ok">✓ 已晋级 ${escapeHtml(data.saved)}</span> `;
  go.remove();
  const ingestBtn = document.createElement("button");
  ingestBtn.className = "stage-act";
  ingestBtn.textContent = "立即 ingest →";
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
  el.title = currentMode === "workspace-write"
    ? "可写会话：Agent 可写 wiki/workspace（raw/ 与 AGENTAO.md 仍只读）。/mode read-only 切回"
    : "只读会话：Agent 仅问答。/mode workspace-write 切到可写";
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
  showOverlay("历史会话", '<p class="muted">加载会话…</p>');
  let conversations;
  try {
    ({ conversations } = await getJSON("/api/conversations"));
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">加载会话失败：${escapeHtml(e.message)}</p>`;
    return;
  }
  const box = $("#overlay-body");
  box.innerHTML = "";
  if (!conversations.length) {
    box.innerHTML = '<p class="muted">暂无会话：在左侧提问即开启一个。</p>';
    return;
  }
  for (const c of conversations) {
    const row = document.createElement("div");
    row.className = "conv-row";
    const meta = document.createElement("div");
    meta.className = "conv-meta";
    const title = document.createElement("span");
    title.className = "conv-title";
    title.textContent = c.title || "（空）"; // textContent：标题按字面显示，无注入
    const tag = document.createElement("span");
    tag.className = "conv-tag";
    // live 条目报真实轮次，冷条目报消息总数（list_sessions 给 message_count，非轮次）。
    const count = c.live ? `${c.turns ?? 0} 轮` : `${c.messages ?? 0} 条`;
    tag.textContent = c.live ? `内存 · ${count}` : `落盘 · ${count}`;
    meta.append(title, tag);
    const open = document.createElement("button");
    open.textContent = "打开";
    open.addEventListener("click", () => openConversation(c));
    const del = document.createElement("button");
    del.className = "conv-del";
    del.textContent = "删除";
    del.addEventListener("click", () => deleteConversation(c.id, row));
    row.append(meta, open, del);
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
      addMsg("note", `已载入会话「${c.title || "（空）"}」（暂无历史消息），继续提问以续聊。`);
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
    addMsg("note", `已载入会话「${c.title || "（空）"}」，但历史消息加载失败（${e.message}）；仍可继续提问。`);
  }
}

async function deleteConversation(id, row) {
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
    if (id === conversationId) setConversation(null); // 删的是当前会话则清掉本地引用（连带抹 ?c=）
    row.remove();
    if (!$("#overlay-body").querySelector(".conv-row")) {
      $("#overlay-body").innerHTML = '<p class="muted">暂无会话：在左侧提问即开启一个。</p>';
    }
  } catch (e) {
    // 经 textContent 赋值，浏览器自会转义；勿再 escapeHtml（否则显示字面 &lt; 实体）。
    row.querySelector(".conv-del").textContent = `删除失败（${e.message}）`;
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

const SLASH_HELP = [  // /help 列这 8 条（**无** /compact，决策P4.4-5）
  "/help — 显示本命令列表",
  "/new — 开启新会话（保留盘上历史）",
  "/clear — 删除当前会话并开新会话",
  "/status — 模型 / 姿态 / 上下文用量",
  "/context — 上下文 token 用量明细",
  "/skills — 列技能（可用 / 已激活）",
  "/tools — 列工具（只读下被禁的标注）",
  "/mode [read-only|workspace-write] — 查看 / 切换姿态",
];

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
      return noteLines(SLASH_HELP);
    case "new":
      startNewConversation();
      return noteLines(["已开启新会话（旧会话保留在「历史会话」）。"]);
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
      return noteLines([`未知命令：${body}。输入 /help 看支持的命令。`]);
  }
}

// /clear：有活动会话 → DELETE 级联删盘（== 删当前 + 新建）；无活动会话退化为 /new（决策P4.4-4）。
// **不**触 memory_manager（Web 无记忆 UI、越权，与 CLI /clear 清记忆刻意不对齐）。
async function slashClear() {
  if (!conversationId) {
    startNewConversation();
    return noteLines(["无活动会话，已开启新会话。"]);
  }
  const id = conversationId;
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    return noteLines([`清除失败：${e.message}`]);
  }
  startNewConversation(); // 先清屏 + 置 conversationId=null，再把确认 note 落进新会话视图
  noteLines(["已删除当前会话并开启新会话。"]);
}

// /mode <值>：运行时翻姿态（P4.5 决策P4.5-5）。仅 read-only / workspace-write 合法；无活动会话
// 或冷会话先提示续聊（端点 404/409）。成功后更新徽标 + note 回报新姿态。
async function slashSetMode(arg) {
  if (arg !== "read-only" && arg !== "workspace-write") {
    return noteLines([`仅支持 read-only / workspace-write，收到：${arg}`]);
  }
  if (!conversationId) {
    return noteLines(["无活动会话：先提一个问题开启会话，再切换姿态。"]);
  }
  let res;
  try {
    res = await fetch(`/api/chat/${encodeURIComponent(conversationId)}/mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: arg }),
    });
  } catch (e) {
    return noteLines([`切换姿态失败：${e.message}`]);
  }
  if (res.status === 409) return noteLines(["会话未激活，先续聊一轮恢复再切。"]);
  if (res.status === 422) return noteLines(["非法姿态：仅支持 read-only / workspace-write。"]);
  if (!res.ok) return noteLines([`切换姿态失败：HTTP ${res.status}`]);
  const { mode } = await res.json();
  setModeBadge(mode);
  return noteLines([
    mode === "workspace-write"
      ? "已切到 workspace-write：Agent 可写 wiki/workspace、跑 shell（raw/ 与 AGENTAO.md 仍只读）。"
      : "已切回 read-only：Agent 仅问答。",
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
    return noteLines([`/${cmd} 获取失败：${e.message}`]);
  }
  renderSlashInfo(cmd, info);
}

function renderSlashInfo(cmd, info) {
  const appLevel = info.live === undefined; // /api/info 无 live 字段（无会话/无 agent）
  const cold = info.live === false;         // 盘上-only 冷会话（部分信息）

  if (cmd === "mode") {
    if (!appLevel && info.mode) setModeBadge(info.mode); // 会话级 info 同步徽标
    const hint = info.mode === "workspace-write"
      ? "（Agent 可写 wiki/workspace；raw/ 与 AGENTAO.md 仍只读）"
      : "（Agent 仅问答；/mode workspace-write 切到可写）";
    return noteLines([`姿态：${info.mode || "read-only"}${hint}`]);
  }
  if (cmd === "status") {
    const lines = [];
    if (appLevel) {
      lines.push(`知识库：${info.kb_name}`);
      lines.push(`模型：${info.model || "（未指定，由会话构造期发现）"}`);
      lines.push(`姿态：${info.mode}`);
      lines.push(`会话：${info.conversations} / ${info.max_conversations}`);
      lines.push("（尚无活动会话：提一个问题以查看上下文用量 / 技能 / 工具。）");
    } else {
      if (info.mode) setModeBadge(info.mode);
      lines.push(`模型：${info.model || "未知"}`);
      lines.push(`姿态：${info.mode}`);
      lines.push(`轮次：${info.turns ?? "—"}　消息：${info.messages ?? "—"}`);
      if (info.context) {
        const c = info.context;
        lines.push(`上下文：${c.estimated_tokens} / ${c.max_tokens} tokens（${c.usage_percent}%）`);
      } else if (cold) {
        lines.push("（冷会话：续聊一轮以恢复完整上下文 / 技能 / 工具。）");
      }
    }
    return noteLines(lines);
  }
  // context / skills / tools 是 agent 级事实：无会话 → 提示开会话；冷会话 → 提示续聊恢复。
  if (appLevel) {
    return noteLines([`/${cmd} 需要活动会话：先提一个问题以开启会话。`]);
  }
  if (cold) {
    return noteLines([`/${cmd}：冷会话尚未恢复——续聊一轮以恢复完整上下文 / 技能 / 工具。`]);
  }
  if (cmd === "context") return renderSlashContext(info.context);
  if (cmd === "skills") return renderSlashSkills(info.skills);
  if (cmd === "tools") return renderSlashTools(info.tools);
}

function renderSlashContext(c) {
  if (!c) return noteLines(["无上下文用量数据。"]);
  const bd = c.token_breakdown || {};
  return noteLines([
    `估算 tokens：${c.estimated_tokens} / ${c.max_tokens}（${c.usage_percent}%）`,
    `分项：system ${bd.system ?? 0} · messages ${bd.messages ?? 0} · tools ${bd.tools ?? 0} · 合计 ${bd.total ?? 0}`,
  ]);
}

function renderSlashSkills(skills) {
  if (!skills) return noteLines(["无技能信息。"]);
  return addNote((div) => {
    const head = document.createElement("div");
    head.textContent = `技能（已激活 ${skills.active.length} / 可用 ${skills.available.length}）：`;
    div.appendChild(head);
    for (const s of skills.available) {
      const row = document.createElement("div");
      row.className = "slash-skill" + (s.active ? " active" : "");
      row.textContent = `${s.active ? "● " : "○ "}${s.name}${s.description ? " — " + s.description : ""}`;
      div.appendChild(row);
    }
  });
}

function renderSlashTools(tools) {
  if (!tools) return noteLines(["无工具信息。"]);
  return addNote((div) => {
    const head = document.createElement("div");
    head.textContent = `工具（${tools.length}，只读下被禁者灰显）：`;
    div.appendChild(head);
    for (const t of tools) {
      const row = document.createElement("div");
      // blocked ∈ {true,false,"unknown"}：true=只读禁（灰显）、unknown=无法判定（标注）、false=可用。
      row.className = "slash-tool" + (t.blocked === true ? " blocked" : "");
      let tag = "";
      if (t.blocked === true) tag = "（只读禁）";
      else if (t.blocked === "unknown") tag = "（未知）";
      row.textContent = `${t.name}${tag ? " " + tag : ""}${t.description ? " — " + t.description : ""}`;
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
  btn.setAttribute("aria-label", sending ? "停止" : "发送");
  btn.title = sending ? "停止（中断当前轮）" : "发送（Enter）";
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
  meta.textContent = "上传中";
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
      ? (att.visionOversize ? "超 20MB·文本引用" : "图像")
      : att.kind === "binary" ? "二进制" : "文本";
  if (att.visionOversize) chip.meta.title = "超过视觉单图上限，agent 只看 <attachment> 文本引用";
  chip.li.dataset.rel = att.rel;
  pendingAttachments.push(att);
  const rm = document.createElement("button");
  rm.type = "button";
  rm.className = "attach-remove";
  rm.textContent = "×";
  rm.title = "移除附件";
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
    if (res.status === 423) throw new Error("可写会话进行中，稍后重试");
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
    botEl.textContent = `对话失败：${e.message}`;
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
    note.textContent = "（已停止）";
    botEl.appendChild(note);
    renderWritableReceipts(payload); // 可写 turn 被停也可能已写盘：照常出 check/撤销/告警
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
    refreshStagingIfOpen(); // 修订 turn 收尾：暂存区开着则重拉刷新池（决策P4.6-9）
    log.scrollTop = log.scrollHeight;
  } else if (event === "error") {
    // 记下服务端已建会话（即便本轮失败）：否则首轮失败时下次又以 null 另起新会话，堆到 503。
    if (payload.conversation_id) setConversation(payload.conversation_id);
    botEl.classList.replace("bot", "err");
    botEl.textContent = `错误：${payload.message}`;
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
      p.textContent = `⚠️ 检测到 ${mutated.join("、")} 被旁路改写，已自动还原（请人工核查 shell 来源）。`;
      div.appendChild(p);
    });
  }
  // ② check 回执 + 「让 Agent 修复」。口径＝**本轮新增**（决策P4.5-4 修订）：ok = 本轮未新增、
  // violations 只装新增；库存量（total）只作旁注（拼进文案的全是数字，无注入面）。
  // **本轮无新增（check.ok）时不出回执**（用户要求）——只静默刷新右栏页面列表，不刷屏。
  if (check) {
    const legacyNote = (n) => (n > 0 ? `（库存量 ${n} 条，与本轮无关）` : "");
    if (check.ok) {
      loadPages(); refreshed = true; // 写后静默刷新右栏 wiki 列表（无回执）
    } else {
      addNote((div) => {
        div.classList.add("receipt-bad");
        const head = document.createElement("div");
        head.innerHTML =
          `<span class="report-bad">✗ check 本轮新增 ${check.violations.length} 条问题</span>` +
          legacyNote(check.total - check.violations.length);
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
          btn.textContent = "让 Agent 修复";
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
      p.textContent = `本轮写了 ${undo.paths.length} 个文件（${undo.paths.join("、")}）。`;
      div.appendChild(p);
      const btn = document.createElement("button");
      btn.className = "receipt-btn";
      btn.textContent = "撤销本轮写";
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
    p.textContent = `撤销失败：${e.message}`;
    box.appendChild(p);
    return;
  }
  let data = {};
  try { data = await res.json(); } catch { /* 空体 */ }
  const note = document.createElement("div");
  if (res.status === 409 && data.conflicts) {
    box.classList.add("receipt-bad");
    note.textContent = `部分未撤销：${(data.conflicts || []).join("、")} 已被后续写改动，请人工核查；其余 ${(data.undone || []).join("、") || "（无）"} 已还原。`;
  } else if (res.status === 409) {
    box.classList.add("receipt-bad");
    note.textContent = "撤销已失效（无本轮写日志或已有后续写）。";
  } else if (res.ok) {
    note.textContent = `已撤销本轮写：${(data.undone || []).join("、") || "（无）"}。`;
  } else {
    box.classList.add("receipt-bad");
    note.textContent = `撤销失败：HTTP ${res.status}`;
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
// 先拉页面再进"列表"视图（首页）——启动即显示 Concept 列表。
// 仅当用户尚未导航时才进首页：否则页面慢加载期间用户已输入的搜索会被这次空首页覆盖
// （loadPages 解析后会就地重渲染当前 index 条目，故用户的搜索结果照常出现）。
loadPages().then(() => { if (histPos < 0) navigate({ kind: "index", query: "" }); });

// 拉进程默认姿态设初始徽标（P4.5）：失败则保留 CSS/HTML 默认 read-only。
getJSON("/api/info").then((info) => {
  if (!info || !info.mode) return;
  defaultMode = info.mode;
  // 仅当尚无活动会话时才据进程默认置徽标：?c= 恢复已设了该会话的真实姿态（restoreConversation 的
  // 每会话 /info 校正），此处的进程默认晚到一拍会把它覆盖回去——正是评审指出的「徽标说谎」竞态。
  if (conversationId === null) setModeBadge(info.mode);
}).catch(() => {});

// 按 ?c=<id> 懒恢复会话（刷新保留当前会话）：仅当该 id 仍在会话目录（内存 ∪ 盘上）里才恢复，
// 否则抹掉 ?c= 另起新会话——避免握着一个已删 / 进程重启后不存在的 id（下条提问会撞 404 未知会话）。
(async function restoreConversationFromUrl() {
  const want = new URL(window.location.href).searchParams.get("c");
  if (!want) return;
  let conversations = null;
  try {
    ({ conversations } = await getJSON("/api/conversations"));
  } catch { /* 列举失败：退回干净起手（除非用户已自行开聊，见下） */ }
  // 拉取期间用户可能已自行开聊（SSE start 已置 conversationId / 正在流式）——此时**绝不**碰其会话或
  // URL：既不回放覆盖、也不 setConversation(null) 抹掉其 id（仿首页 `histPos < 0` 守卫，决策P4.6）。
  if (conversationId !== null || chatStreaming) return;
  const hit = conversations && conversations.find((c) => c.id === want);
  if (hit) { await restoreConversation(hit); return; }
  setConversation(null); // 失效 / 未知 id / 列举失败 → 抹掉 ?c=，干净起手
})();
