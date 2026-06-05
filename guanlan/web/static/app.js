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
let chatLoadToken = 0; // 每次切换/新建自增；历史气泡异步回放回来若 token 已变则丢弃（防迟到覆盖）

$("#chat-new").addEventListener("click", () => {
  // 仅清本地视图、开启新会话——**绝不** DELETE 旧会话：P4.2 起 DELETE 会级联删盘，删的是
  // 持久历史，普通"切换/新建"不该销毁记录。旧会话持久化开时已落盘、可从"历史会话"再开；
  // 持久化关时它仍是 live、列在历史里（进程退出即清）。要永久删除走历史列表的"删除"。
  chatLoadToken++; // 作废任何在飞的历史气泡回放（用户已开新会话）
  conversationId = null;
  $("#chat-log").innerHTML = "";
});

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
  // 一轮在飞时（流式中 #chat-send 被禁用）不重复发送：回车与按钮共用此守卫，避免在同一会话
  // 上叠起并发轮次（服务端 conv.lock 会把第二轮挂住、前端则堆出空 bot 气泡）。输入保留不清。
  if ($("#chat-send").disabled) return;
  const input = $("#chat-input");
  const msg = input.value.trim();
  if (msg) { input.value = ""; sendChat(msg); }
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  submitChat();
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
