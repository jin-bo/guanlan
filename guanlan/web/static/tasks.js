"use strict";
// 后台任务中心（P4.33 T1，见 docs/P4.33-Web后台任务.md §7）。经典脚本，共享全局作用域。
//
// - 顶栏「任务」按钮 + 徽标（运行 / 排队数；有需要处理的任务时变色），点击开右侧抽屉（非模态，不遮聊天区）。
// - 单一全局轮询 GET /api/tasks（有活跃任务 1s，否则 10s），取代各浮层自己的 pollJob 循环。
// - 本地按 id 维护任务表：对比上次状态发现「→ 终态」即弹 toast、刷新 wiki 列表。响应总带全部「需要处理」+
//   全部活跃 + 最近完成 20 条；本地跟踪中却从响应里消失的 id（浏览器后台停轮询期间完成了很多任务），逐个用
//   GET /api/tasks/{id} 补查终态，404 = 已被保留策略清掉，静默移除（决策 D7）。
// - 一键入库任务只读展示，点击跳回暂存区原任务卡（决策 D7）。

const TASK_POLL_ACTIVE_MS = 1000;
const TASK_POLL_IDLE_MS = 10000;

let taskPollingOn = false;
let taskPollTimer = null;
let taskPollInFlight = false; // 一轮轮询进行中：期间再要求立即轮询只记一笔，收尾时补跑，不并发两轮
let taskPollAgain = false;
let taskTable = new Map(); // id → 最近一次看到的列表行
let taskTracked = new Set(); // 上次看到时仍未结束的 id（含刚提交、还没出现在列表里的）
let taskView = { attention: [], active: [], recent: [] };
let taskExpanded = null; // 抽屉里展开的任务 id
let taskLog = { id: null, next: 0, text: "" }; // 展开任务的日志增量缓存

function taskIsActive(row) {
  return row && row.group === "active";
}

function taskIsRunning(row) {
  return row && (row.state === "running" || row.state === "preparing");
}

// 需要快轮询的状态：真在推进中。一键入库的「待确认 / 已暂停」也归「进行中」组，但可能一停数天，不该 1s 轮询。
function taskIsMoving(row) {
  return row && (row.state === "queued" || row.state === "running" || row.state === "preparing");
}

// 任务标题：按 kind + params 用当前语言拼（服务端 title 只作 CLI/日志用的中文兜底）。t() 首参须为字面量（决策P4.7-8）。
function taskTitle(row) {
  const p = (row && row.params) || {};
  switch (row && row.kind) {
    case "ingest": return t("task.title.ingest", p.target || "");
    case "backfill": {
      const q = String(p.question || "").trim();
      return t("task.title.backfill", q.length > 40 ? q.slice(0, 40) + "…" : q);
    }
    case "heal":
      return p.targets && p.targets.length
        ? t("task.title.healTargets", p.targets.length)
        : t("task.title.heal", p.limit || "");
    case "audit": return t("task.title.audit", p.limit || "");
    case "import": return t("task.title.import", row.title || row.id);
    default: return (row && (row.title || row.id)) || "";
  }
}

function taskStateLabel(state) {
  switch (state) {
    case "queued": return t("task.state.queued");
    case "running": return t("task.state.running");
    case "succeeded": return t("task.state.succeeded");
    case "failed": return t("task.state.failed");
    case "cancelled": return t("task.state.cancelled");
    case "interrupted": return t("task.state.interrupted");
    case "preparing": return t("task.state.preparing");
    case "awaiting_confirm": return t("task.state.awaitingConfirm");
    case "paused": return t("task.state.paused");
    case "done": return t("task.state.done");
    default: return state || "";
  }
}

function taskStateClass(state) {
  if (state === "succeeded" || state === "done") return "ok";
  if (state === "failed" || state === "interrupted") return "bad";
  if (state === "running" || state === "preparing") return "run";
  return "idle";
}

// ── 提交入口用：登记一个刚提交的任务，立即切到快轮询 ─────────────────────────────

function trackSubmittedTask(taskId, kind, params) {
  if (!taskId) return;
  const row = { id: taskId, source: "jobs", kind, params: params || {}, state: "queued", group: "active" };
  taskTable.set(taskId, row);
  taskTracked.add(taskId);
  showToast(t("task.toast.submitted", taskTitle(row)), "info");
  renderTaskBadge();
  if (taskDrawerOpen()) renderTaskDrawer();
  scheduleTaskPoll(0);
}

// ── 轮询 ────────────────────────────────────────────────────────────────────

function startTaskPolling() {
  if (taskPollingOn) return;
  taskPollingOn = true;
  scheduleTaskPoll(0);
}

function scheduleTaskPoll(ms) {
  if (!taskPollingOn) return;
  // 并发两轮会让较早发出、较晚处理的旧响应把已结束的任务重新记成「跟踪中」，下一轮再弹一次完成 toast。
  if (taskPollInFlight) { if (ms === 0) taskPollAgain = true; return; }
  if (taskPollTimer) clearTimeout(taskPollTimer);
  taskPollTimer = setTimeout(pollTasks, ms);
}

async function pollTasks() {
  taskPollTimer = null;
  taskPollInFlight = true;
  taskPollAgain = false;
  let next = TASK_POLL_IDLE_MS; // 服务暂不可达：慢下来重试
  try {
    next = await pollTasksOnce();
  } finally {
    taskPollInFlight = false;
    scheduleTaskPoll(taskPollAgain ? 0 : next);
  }
}

// 一轮轮询，返回下次间隔（ms）。
async function pollTasksOnce() {
  let data = null;
  try {
    data = await getJSON("/api/tasks");
  } catch {
    return TASK_POLL_IDLE_MS;
  }
  taskView = { attention: data.attention || [], active: data.active || [], recent: data.recent || [] };
  const rows = [...taskView.attention, ...taskView.active, ...taskView.recent];
  const seen = new Set();
  for (const row of rows) {
    seen.add(row.id);
    noteTaskRow(row);
  }
  // 本地还在跟踪、这次却不在响应里：补查详情拿终态（决策 D7）。
  for (const id of [...taskTracked]) {
    if (seen.has(id)) continue;
    try {
      const res = await fetch(`/api/tasks/${encodeURIComponent(id)}`);
      if (res.status === 404) {
        taskTracked.delete(id);
        taskTable.delete(id);
        continue;
      }
      if (res.ok) noteTaskRow(await res.json());
    } catch { /* 网络抖动：下一轮再查 */ }
  }
  renderTaskBadge();
  if (taskDrawerOpen()) {
    await refreshExpandedTask();
    renderTaskDrawer();
  }
  const busy = rows.some(taskIsMoving) || [...taskTracked].some((id) => taskIsMoving(taskTable.get(id)));
  return busy ? TASK_POLL_ACTIVE_MS : TASK_POLL_IDLE_MS;
}

// 记一行；若它从「跟踪中」变成终态，弹 toast + 刷新 wiki 列表。首轮轮询看到的历史终态不弹（不在跟踪集里）。
function noteTaskRow(row) {
  taskTable.set(row.id, row);
  if (taskIsActive(row)) {
    taskTracked.add(row.id);
    return;
  }
  if (!taskTracked.has(row.id)) return;
  taskTracked.delete(row.id);
  onTaskFinished(row);
}

function onTaskFinished(row) {
  const title = taskTitle(row);
  if (row.state === "succeeded" || row.state === "done") showToast(t("task.toast.succeeded", title), "ok");
  else if (row.state === "cancelled") showToast(t("task.toast.cancelled", title), "info");
  else showToast(t("task.toast.failed", title, taskStateLabel(row.state)), "bad");
  loadPages(); // 写任务可能新建/改了页：刷新右栏列表
}

// ── 顶栏徽标 ────────────────────────────────────────────────────────────────

function renderTaskBadge() {
  const badge = $("#task-badge");
  if (!badge) return;
  const rows = [...taskView.attention, ...taskView.active];
  // 刚提交、还没出现在列表里的任务也算排队，免得提交后徽标空一拍。
  for (const id of taskTracked) if (!rows.some((r) => r.id === id) && taskTable.get(id)) rows.push(taskTable.get(id));
  const running = rows.filter(taskIsRunning).length;
  const queued = rows.filter((r) => r.state === "queued").length;
  const attention = taskView.attention.length;
  const parts = [];
  if (running) parts.push(t("task.badge.running", running));
  if (queued) parts.push(t("task.badge.queued", queued));
  if (attention) parts.push(t("task.badge.attention", attention));
  badge.textContent = String(running + queued + attention);
  badge.hidden = running + queued + attention === 0;
  badge.classList.toggle("warn", attention > 0);
  $("#tasks-btn").title = parts.length ? `${t("tip.tasks")} · ${parts.join(" · ")}` : t("tip.tasks");
}

// ── 抽屉 ────────────────────────────────────────────────────────────────────

function taskDrawerOpen() {
  const d = $("#task-drawer");
  return d && !d.classList.contains("hidden");
}

// 抽屉底边停在输入框上沿之上：输入框满宽、随输入增高，抽屉不能盖住发送按钮（P4.33：非模态、不遮挡聊天）。
function fitTaskDrawer() {
  const drawer = $("#task-drawer"), form = $("#chat-form");
  if (!drawer || !form || drawer.classList.contains("hidden")) return;
  const top = form.getBoundingClientRect().top;
  drawer.style.bottom = `${Math.max(0, window.innerHeight - top + 8)}px`;
}
window.addEventListener("resize", fitTaskDrawer);
if (typeof ResizeObserver === "function" && $("#chat-form")) new ResizeObserver(fitTaskDrawer).observe($("#chat-form"));

function openTaskDrawer() {
  $("#task-drawer").classList.remove("hidden");
  fitTaskDrawer();
  renderTaskDrawer();
  scheduleTaskPoll(0);
}

function closeTaskDrawer() {
  $("#task-drawer").classList.add("hidden");
}

function renderTaskDrawer() {
  const body = $("#task-drawer-body");
  if (!body) return;
  // 每轮轮询整体重画：先记下抽屉与展开日志的滚动位置，重画后还原（日志原本贴底的继续贴底），
  // 否则运行中的日志每秒被拉回顶部、没法往上翻。
  const scrollTop = body.scrollTop;
  const oldPre = body.querySelector(".task-detail pre");
  const preTop = oldPre ? oldPre.scrollTop : 0;
  const preAtBottom = oldPre ? oldPre.scrollHeight - oldPre.scrollTop - oldPre.clientHeight < 4 : false;
  const preOwner = oldPre ? oldPre.closest(".task-row").dataset.id : null;
  body.innerHTML = "";
  // 刚提交、尚未出现在列表里的任务并进「进行中」组，免得抽屉里空一拍。
  const active = [...taskView.active];
  for (const id of taskTracked) {
    const row = taskTable.get(id);
    if (row && !active.some((r) => r.id === id) && !taskView.attention.some((r) => r.id === id)) active.push(row);
  }
  const groups = [
    ["attention", t("task.group.attention"), taskView.attention],
    ["active", t("task.group.active"), active],
    ["recent", t("task.group.recent"), taskView.recent],
  ];
  let any = false;
  for (const [key, label, rows] of groups) {
    if (!rows.length) continue;
    any = true;
    const h = document.createElement("div");
    h.className = `task-group task-group-${key}`;
    h.textContent = label;
    body.appendChild(h);
    for (const row of rows) body.appendChild(renderTaskRow(row));
  }
  if (!any) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = t("task.empty");
    body.appendChild(p);
  }
  body.scrollTop = scrollTop;
  const pre = body.querySelector(".task-detail pre");
  if (pre && preOwner === pre.closest(".task-row").dataset.id) pre.scrollTop = preAtBottom ? pre.scrollHeight : preTop;
}

function renderTaskRow(row) {
  const el = document.createElement("div");
  el.className = "task-row";
  el.dataset.id = row.id;
  const head = document.createElement("div");
  head.className = "task-head";
  const title = document.createElement("span");
  title.className = "task-title";
  title.textContent = taskTitle(row); // 标题含路径/问题原文：按字面显示
  const state = document.createElement("span");
  state.className = `task-state ${taskStateClass(row.state)}`;
  state.textContent = taskStateLabel(row.state);
  head.append(title, state);
  el.appendChild(head);

  const meta = document.createElement("div");
  meta.className = "task-meta";
  const bits = [];
  if (row.kind === "import" && row.total) bits.push(t("task.importProgress", row.done || 0, row.total));
  if (row.progress) bits.push(row.progress); // 心跳行（服务端中文，同既有 pollJob 口径）
  const when = row.finished || row.started || row.created;
  if (when) bits.push(when);
  meta.textContent = bits.join(" · ");
  el.appendChild(meta);

  if (row.source === "oneclick") {
    // 一键入库：只读展示，点击跳回暂存区原任务卡（暂停/续跑/放弃都在那里，决策 D7）。
    el.classList.add("clickable");
    el.title = t("task.openStaging");
    el.addEventListener("click", () => { closeTaskDrawer(); openStaging(); });
    return el;
  }
  el.classList.add("clickable");
  head.addEventListener("click", () => toggleTaskDetail(row.id));
  if (taskExpanded === row.id) {
    const detail = document.createElement("div");
    detail.className = "task-detail";
    renderTaskDetail(row, detail);
    el.appendChild(detail);
  }
  return el;
}

async function toggleTaskDetail(id) {
  if (taskExpanded === id) {
    taskExpanded = null;
    renderTaskDrawer();
    return;
  }
  taskExpanded = id;
  taskLog = { id, next: 0, text: "", summary: null };
  await refreshExpandedTask();
  renderTaskDrawer();
}

// 展开中的任务：按字节偏移增量拉日志；终态后再拉一次即停（next 不再增长）。
async function refreshExpandedTask() {
  const id = taskExpanded;
  if (!id) return;
  const row = taskTable.get(id);
  if (taskLog.id === id && taskLog.final) return;
  const from = taskLog.next;
  try {
    const d = await getJSON(`/api/tasks/${encodeURIComponent(id)}?log_after=${from}`);
    // 并发的两次刷新（轮询与点击展开、或 scheduleTaskPoll(0) 叠出的第二次轮询）会拿同一偏移各拉一段：
    // 只认偏移仍等于发请求时的那一份，后到的丢弃，否则同一段日志会被追加两遍。
    if (taskExpanded !== id || taskLog.id !== id || taskLog.next !== from) return;
    const log = d.log || {};
    if (log.skipped) taskLog.text = ""; // 落后太多、服务端只给了末尾一段：从这段重新开始
    taskLog.text += log.text || "";
    taskLog.next = log.next || taskLog.next;
    taskLog.summary = d.summary || null;
    taskTable.set(id, { ...(row || {}), ...d });
    if (!taskIsActive(d)) taskLog.final = true;
  } catch { /* 网络抖动 / 已被清理：保留已拉到的日志，下一轮再试 */ }
}

function renderTaskDetail(row, box) {
  if (taskLog.id !== row.id) return;
  if (row.state === "interrupted") {
    const p = document.createElement("p");
    p.className = "report-bad";
    p.textContent = t("task.interruptedHint");
    box.appendChild(p);
  } else if (row.state === "cancelled") {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = t("task.cancelledHint");
    box.appendChild(p);
  }
  // 终态：复用既有回执渲染（heal/audit 结构化回执 + 退出码徽标 + agent 散文）。
  if (row.state === "succeeded" || row.state === "failed") {
    const job = { exit_code: row.exit_code, result: taskLog.summary, output: taskLog.text };
    if (row.kind === "heal") renderHealDone(job, box);
    else if (row.kind === "audit") renderAuditDone(job, box);
    else renderJobDefault(job, box);
    return;
  }
  if (taskLog.text) {
    const pre = document.createElement("pre");
    pre.innerHTML = jobLogHtml(taskLog.text);
    box.appendChild(pre);
  } else if (row.state === "queued") {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = t("task.queuedHint");
    box.appendChild(p);
  }
}

// ── toast ───────────────────────────────────────────────────────────────────

function showToast(text, kind = "info") {
  let box = $("#toast-box");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast-box";
    box.className = "toast-box";
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  el.title = t("task.toast.open");
  el.addEventListener("click", () => { el.remove(); openTaskDrawer(); });
  box.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

$("#tasks-btn").addEventListener("click", () => (taskDrawerOpen() ? closeTaskDrawer() : openTaskDrawer()));
$("#task-drawer-close").addEventListener("click", closeTaskDrawer);
