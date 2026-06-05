"use strict";
// 观澜 Web 宿主前端（P4，见 docs/P4-Web宿主.md §6 决策P4-3）。
// vanilla JS + fetch，无 npm/构建/CDN/第三方运行时；流式只用 fetch 读 response.body（不用 EventSource）。

const $ = (sel) => document.querySelector(sel);

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} → ${res.status}`);
  return res.json();
}

// ── 浏览：页面树 / raw 列表 / 单页 ──────────────────────────────────────────

async function loadPages() {
  const tree = $("#page-tree");
  try {
    const { pages } = await getJSON("/api/pages");
    if (!pages.length) { tree.innerHTML = '<p class="muted">暂无页面。</p>'; return; }
    // null 原型：type 是容错的用户值，可能是 "__proto__"/"constructor"，普通对象会命中
    // 继承属性而非新建数组、令 .push 抛错、整棵树崩。
    const groups = Object.create(null);
    for (const p of pages) (groups[p.type] ||= []).push(p);
    tree.innerHTML = "";
    for (const type of Object.keys(groups).sort()) {
      const g = document.createElement("div");
      g.className = "group";
      const title = document.createElement("div");
      title.className = "group-title";
      title.textContent = `${type} (${groups[type].length})`;
      g.appendChild(title);
      for (const p of groups[type]) {
        const a = document.createElement("a");
        a.textContent = p.title;
        a.dataset.path = p.path;
        a.href = "#";
        a.addEventListener("click", (e) => { e.preventDefault(); openPage(p.path); });
        g.appendChild(a);
      }
      tree.appendChild(g);
    }
  } catch (e) {
    tree.innerHTML = `<p class="muted">加载页面失败：${e.message}</p>`;
  }
}

async function loadRaw() {
  const list = $("#raw-list");
  try {
    const { files } = await getJSON("/api/raw");
    if (!files.length) { list.innerHTML = '<li class="muted">raw/ 为空</li>'; return; }
    list.innerHTML = "";
    for (const f of files) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = f.name;
      const btn = document.createElement("button");
      btn.className = "ingest-btn";
      btn.textContent = "ingest";
      btn.addEventListener("click", () => triggerIngest(`raw/${f.name}`));
      li.append(name, btn);
      list.appendChild(li);
    }
  } catch (e) {
    list.innerHTML = `<li class="muted">加载 raw 失败：${e.message}</li>`;
  }
}

async function openPage(path) {
  document.querySelectorAll(".page-tree a").forEach((a) =>
    a.classList.toggle("active", a.dataset.path === path));
  const meta = $("#page-meta");
  const body = $("#page-body");
  body.innerHTML = '<p class="muted">加载中…</p>';
  meta.innerHTML = "";
  try {
    const data = await getJSON(`/api/page?path=${encodeURIComponent(path)}`);
    if (data.meta) {
      const t = data.meta.type ? `<span class="ptype">${escapeHtml(String(data.meta.type))}</span>` : "";
      const lu = data.meta.last_updated ? `更新于 ${escapeHtml(String(data.meta.last_updated))}` : "";
      meta.innerHTML = `${t}<span>${escapeHtml(path)}</span> · <span>${lu}</span>`;
    } else {
      meta.innerHTML = `<span>${escapeHtml(path)}</span> · <span class="muted">无 frontmatter</span>`;
    }
    body.innerHTML = data.html;
  } catch (e) {
    body.innerHTML = `<p class="muted">打开失败：${e.message}</p>`;
  }
}

// 站内 wikilink 导航：事件委托到正文，点 .wikilink[data-page] 切页。
$("#page-body").addEventListener("click", (e) => {
  const a = e.target.closest("a.wikilink[data-page]");
  if (a) { e.preventDefault(); openPage(a.dataset.page); }
});

// ── 零 LLM 报告 / graph ─────────────────────────────────────────────────────

function showOverlay(title, html) {
  $("#overlay-title").textContent = title;
  $("#overlay-body").innerHTML = html;
  $("#overlay").classList.remove("hidden");
}
$("#overlay-close").addEventListener("click", () => $("#overlay").classList.add("hidden"));
$("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay") $("#overlay").classList.add("hidden"); });

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
      const data = await getJSON(`/api/report/${name}`);
      showOverlay(name, renderReport(name, data));
    } catch (e) {
      showOverlay(name, `<p class="report-bad">失败：${escapeHtml(e.message)}</p>`);
    }
  });
});

$("#graph-btn").addEventListener("click", () => window.open("/graph", "_blank"));

// ── 写：ingest（入队 → 轮询）────────────────────────────────────────────────

async function triggerIngest(target) {
  showOverlay("ingest", `<p class="muted">已提交 <code>${escapeHtml(target)}</code>，排队中…</p>`);
  try {
    const { job_id } = await postJSON("/api/ingest", { target });
    await pollJob(job_id, target);
  } catch (e) {
    showOverlay("ingest", `<p class="report-bad">提交失败：${escapeHtml(e.message)}</p>`);
  }
}

async function pollJob(jobId, target) {
  for (;;) {
    const job = await getJSON(`/api/jobs/${jobId}`);
    if (job.state === "done") {
      const ok = job.exit_code === 0;
      const badge = ok ? '<span class="report-ok">✓ 通过</span>' : `<span class="report-bad">✗ 退出码 ${job.exit_code}</span>`;
      showOverlay("ingest", `<p>${escapeHtml(target)} ${badge}</p><pre>${escapeHtml(job.output || "(无输出)")}</pre>`);
      await Promise.all([loadPages(), loadRaw()]); // 写后刷新页面树
      return;
    }
    showOverlay("ingest", `<p class="muted">${escapeHtml(target)} · ${job.state}…</p>`);
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
  $("#chat-log").appendChild(div);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
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
  if (event === "token") {
    botEl.textContent += payload;
    $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  } else if (event === "done") {
    conversationId = payload.conversation_id;
    if (payload.answer) botEl.textContent = payload.answer; // 以完整答案收尾，避免增量拼接误差
  } else if (event === "error") {
    botEl.classList.replace("bot", "err");
    botEl.textContent = `错误：${payload.message}`;
  }
}

// ── 工具 ────────────────────────────────────────────────────────────────────

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

// ── 启动 ────────────────────────────────────────────────────────────────────
loadPages();
loadRaw();
