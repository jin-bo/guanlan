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

// ── Wiki：搜索 + 单页内容 ────────────────────────────────────────────────────

let allPages = [];      // /api/pages 缓存
let activePath = null;  // 当前查看页

async function loadPages() {
  try {
    const { pages } = await getJSON("/api/pages");
    allPages = pages;
  } catch (e) {
    allPages = [];
    $("#wiki-results").innerHTML = `<div class="empty">加载页面失败：${escapeHtml(e.message)}</div>`;
    return;
  }
  renderResults($("#wiki-search").value);
}

function renderResults(query) {
  const results = $("#wiki-results");
  const q = query.trim().toLowerCase();
  const matched = q
    ? allPages.filter((p) => p.title.toLowerCase().includes(q) || p.path.toLowerCase().includes(q))
    : allPages;
  if (!matched.length) {
    results.innerHTML = `<div class="empty">${allPages.length ? "无匹配页面" : "暂无页面"}</div>`;
    return;
  }
  const groups = Object.create(null); // null 原型：type 是容错用户值，防 __proto__/constructor 污染
  for (const p of matched) (groups[p.type] ||= []).push(p);
  results.innerHTML = "";
  for (const type of Object.keys(groups).sort()) {
    const title = document.createElement("div");
    title.className = "group-title";
    title.textContent = `${type} (${groups[type].length})`;
    results.appendChild(title);
    for (const p of groups[type]) {
      const a = document.createElement("a");
      a.textContent = p.title;
      a.href = "#";
      a.dataset.path = p.path;
      a.classList.toggle("active", p.path === activePath);
      a.addEventListener("click", (e) => { e.preventDefault(); openPage(p.path); });
      results.appendChild(a);
    }
  }
}

async function openPage(path) {
  activePath = path;
  document.querySelectorAll("#wiki-results a").forEach((a) =>
    a.classList.toggle("active", a.dataset.path === path));
  const body = $("#wiki-body");
  body.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const data = await getJSON(`/api/page?path=${encodeURIComponent(path)}`);
    let meta;
    if (data.meta) {
      const t = data.meta.type ? `<span class="ptype">${escapeHtml(String(data.meta.type))}</span>` : "";
      const lu = data.meta.last_updated ? `更新于 ${escapeHtml(String(data.meta.last_updated))}` : "";
      meta = `${t}<span>${escapeHtml(path)}</span> · <span>${lu}</span>`;
    } else {
      meta = `<span>${escapeHtml(path)}</span> · <span class="muted">无 frontmatter</span>`;
    }
    body.innerHTML = `<div class="page-meta">${meta}</div>` + data.html;
  } catch (e) {
    body.innerHTML = `<p class="muted">打开失败：${escapeHtml(e.message)}</p>`;
  }
}

$("#wiki-search").addEventListener("input", (e) => renderResults(e.target.value));

// 站内 wikilink 导航：事件委托到 wiki 正文。
$("#wiki-body").addEventListener("click", (e) => {
  const a = e.target.closest("a.wikilink[data-page]");
  if (a) { e.preventDefault(); openPage(a.dataset.page); }
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

async function pollJob(jobId, target) {
  for (;;) {
    const job = await getJSON(`/api/jobs/${jobId}`);
    if (job.state === "done") {
      const ok = job.exit_code === 0;
      const badge = ok ? '<span class="report-ok">✓ 通过</span>' : `<span class="report-bad">✗ 退出码 ${job.exit_code}</span>`;
      $("#overlay-body").innerHTML = `<p>${escapeHtml(target)} ${badge}</p><pre>${escapeHtml(job.output || "(无输出)")}</pre>`;
      await loadPages(); // 写后刷新 wiki 搜索列表
      return;
    }
    $("#overlay-body").innerHTML = `<p class="muted">${escapeHtml(target)} · ${job.state}…</p>`;
    await sleep(400);
  }
}

// ── 问答 / 多轮会话（fetch 流式读 SSE）─────────────────────────────────────

let conversationId = null;

$("#chat-new").addEventListener("click", () => {
  // 丢弃服务端旧会话，避免反复"新会话"在内存里堆积到 MAX_CONVERSATIONS 触 503（best-effort）。
  if (conversationId) {
    fetch(`/api/conversations/${conversationId}`, { method: "DELETE" }).catch(() => {});
  }
  conversationId = null;
  $("#chat-log").innerHTML = "";
});

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#chat-input");
  const msg = input.value.trim();
  if (msg) { input.value = ""; sendChat(msg); }
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

async function sendChat(message) {
  addMsg("user", message);
  const botEl = addMsg("bot", "");
  const sendBtn = $("#chat-send");
  sendBtn.disabled = true;
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
    sendBtn.disabled = false;
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
  if (event === "token") {
    botEl.textContent += payload;
    log.scrollTop = log.scrollHeight;
  } else if (event === "done") {
    conversationId = payload.conversation_id;
    if (payload.answer) botEl.textContent = payload.answer; // 以完整答案收尾，避免增量拼接误差
  } else if (event === "error") {
    botEl.classList.replace("bot", "err");
    botEl.textContent = `错误：${payload.message}`;
  }
}

// ── 启动 ────────────────────────────────────────────────────────────────────
loadPages();
