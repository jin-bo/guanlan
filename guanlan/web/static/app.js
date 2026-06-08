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

// ── 问答 / 多轮会话（fetch 流式读 SSE）─────────────────────────────────────

let conversationId = null;
let chatLoadToken = 0; // 每次切换/新建自增；历史气泡异步回放回来若 token 已变则丢弃（防迟到覆盖）
let chatStreaming = false; // 一轮在飞时为 true：发送按钮切「停止」、回车不再发新轮
let pendingStop = false; // 首轮 id 未回时点了「停止」：标记待停，start 帧回填 id 后立即补发

function startNewConversation() {
  // 仅清本地视图、开启新会话——**绝不** DELETE 旧会话：P4.2 起 DELETE 会级联删盘，删的是
  // 持久历史，普通"切换/新建"不该销毁记录。旧会话持久化开时已落盘、可从"历史会话"再开；
  // 持久化关时它仍是 live、列在历史里（进程退出即清）。要永久删除走历史列表的"删除"。
  chatLoadToken++; // 作废任何在飞的历史气泡回放（用户已开新会话）
  conversationId = null;
  $("#chat-log").innerHTML = "";
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
  // 切到历史会话：仅切本地 id（下一条提问即触发后端透明 rebuild 懒恢复）。**绝不** DELETE
  // 当前会话——它仍要留在历史里，普通切换不该销毁其持久记录（要永久删除走历史列表的"删除"）。
  const tok = ++chatLoadToken;
  conversationId = c.id;
  const log = $("#chat-log");
  log.innerHTML = "";
  $("#chat-input").focus();
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
    const res = await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
    if (id === conversationId) conversationId = null; // 删的是当前会话则清掉本地引用
    row.remove();
    if (!$("#overlay-body").querySelector(".conv-row")) {
      $("#overlay-body").innerHTML = '<p class="muted">暂无会话：在左侧提问即开启一个。</p>';
    }
  } catch (e) {
    // 经 textContent 赋值，浏览器自会转义；勿再 escapeHtml（否则显示字面 &lt; 实体）。
    row.querySelector(".conv-del").textContent = `删除失败（${e.message}）`;
  }
}

function submitChat() {
  // 一轮在飞时不重复发送：回车与「发送」共用此守卫，避免在同一会话上叠起并发轮次（服务端
  // conv.lock 会把第二轮挂住、前端则堆出空 bot 气泡）。停止只由按钮点击触发（见下），回车
  // 流式中一律按空操作处理——不让回车误把当前轮停掉。输入保留不清。
  if (chatStreaming) return;
  const input = $("#chat-input");
  const msg = input.value.trim();
  if (!msg) return;
  input.value = "";
  // 斜杠命令前置解析（决策P4.4-1）：以 `/` 开头一律本地处理、**绝不进 /api/chat**——不打 LLM、
  // 不占对话轮次、不入历史。展示/自省/生命周期分流见 handleSlash。
  if (msg.startsWith("/")) { handleSlash(msg); return; }
  sendChat(msg);
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
  "/mode — 当前姿态（只读；可写见 P4.5）",
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
    case "status":
    case "context":
    case "skills":
    case "tools":
    case "mode":
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
    return noteLines([`姿态：${info.mode || "read-only"}（Web 问答只读；可写会话见 P4.5）`]);
  }
  if (cmd === "status") {
    const lines = [];
    if (appLevel) {
      lines.push(`知识库：${info.kb_name}`);
      lines.push(`模型：${info.model || "（未指定，由会话构造期发现）"}`);
      lines.push(`姿态：${info.mode}（只读）`);
      lines.push(`会话：${info.conversations} / ${info.max_conversations}`);
      lines.push("（尚无活动会话：提一个问题以查看上下文用量 / 技能 / 工具。）");
    } else {
      lines.push(`模型：${info.model || "未知"}`);
      lines.push(`姿态：${info.mode}（只读）`);
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
  btn.textContent = sending ? "停止" : "发送";
  btn.classList.toggle("stopping", sending);
  btn.disabled = false;
}

// 停止：POST /api/chat/{id}/stop 置位服务端取消令牌。不在此复位按钮——等流尾的 stopped/done
// 帧由 sendChat 的 finally 统一复位（避免与在飞流抢状态）。停止请求飞行中先禁用防重复点。
async function stopChat() {
  const btn = $("#chat-send");
  btn.disabled = true;
  btn.textContent = "停止中…";
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
    return;
  }
  e.preventDefault();
  submitChat();
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

async function sendChat(message) {
  addMsg("user", message);
  const botEl = addMsg("bot", "");
  setChatSending(true); // 按钮切「停止」、置 chatStreaming，整轮可中断
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, conversation_id: conversationId }),
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
    if (payload.conversation_id) conversationId = payload.conversation_id;
    if (pendingStop) { pendingStop = false; stopChat(); } // 首轮 id 未回时点过停止 → 现在补发
  } else if (event === "token") {
    botEl.textContent += payload;
    log.scrollTop = log.scrollHeight;
  } else if (event === "stopped") {
    // 用户主动停止：保留已流出的纯文本（不再渲染 markdown），加一行轻提示。
    if (payload.conversation_id) conversationId = payload.conversation_id;
    const note = document.createElement("span");
    note.className = "stop-note";
    note.textContent = "（已停止）";
    botEl.appendChild(note);
    log.scrollTop = log.scrollHeight;
  } else if (event === "done") {
    conversationId = payload.conversation_id;
    // 收尾：用服务端渲染的安全 markdown HTML 替换流式纯文本（含 [[页]]→站内链接）。
    if (payload.answer_html !== undefined) {
      botEl.classList.add("rendered"); // 切到正常空白模型 + 富排版（见 app.css）
      botEl.innerHTML = payload.answer_html;
    } else if (payload.answer) {
      botEl.textContent = payload.answer;
    }
    log.scrollTop = log.scrollHeight;
  } else if (event === "error") {
    // 记下服务端已建会话（即便本轮失败）：否则首轮失败时下次又以 null 另起新会话，堆到 503。
    if (payload.conversation_id) conversationId = payload.conversation_id;
    botEl.classList.replace("bot", "err");
    botEl.textContent = `错误：${payload.message}`;
  }
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
