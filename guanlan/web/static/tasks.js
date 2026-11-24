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

// 持写锁在写的：运行中、取消后回滚中、中断任务回滚中（一键入库的「准备中」同列）。
const TASK_RUNNING_STATES = ["running", "cancelling", "rolling_back", "preparing"];

// 回滚作业还没开始执行：排在别的写任务后面，或在等命令行释放库（服务端 rollback_queued 两种都算）。
function taskIsWaitingRollback(row) {
  return row && row.state === "rolling_back" && !!row.rollback_queued;
}

function taskIsRunning(row) {
  return row && TASK_RUNNING_STATES.includes(row.state) && !taskIsWaitingRollback(row);
}

// 需要快轮询的状态：真在推进中。一键入库的「待确认 / 已暂停」也归「进行中」组，但可能一停数天，不该 1s 轮询。
function taskIsMoving(row) {
  // 等待写锁（命令行占着库）可能长达 30 分钟、期间没有进展：按空闲间隔轮询即可。
  // 回滚在等命令行释放库（带占用者）时同理。
  if (taskIsWaitingRollback(row)) return false;
  return row && (row.state === "queued" || TASK_RUNNING_STATES.includes(row.state));
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
    case "reindex": return p.prune ? t("task.title.reindexPrune") : t("task.title.reindex");
    case "dropsrc": return t("task.title.dropSources");
    case "prune": return t("task.title.prune", (p.pages || []).length);
    case "remove": return p.scope === "all" ? t("task.title.removeAll", p.raw_dir || "") : t("task.title.remove", p.source || "");
    case "restore": return t("task.title.restore", p.name || "");
    case "import": return t("task.title.import", row.title || row.id);
    default: return (row && (row.title || row.id)) || "";
  }
}

function taskStateLabel(state) {
  switch (state) {
    case "queued": return t("task.state.queued");
    case "waiting_lock": return t("task.state.waitingLock");
    case "running": return t("task.state.running");
    case "succeeded": return t("task.state.succeeded");
    case "failed": return t("task.state.failed");
    case "cancelled": return t("task.state.cancelled");
    case "interrupted": return t("task.state.interrupted");
    case "cancelling": return t("task.state.cancelling");
    case "rolling_back": return t("task.state.rollingBack");
    case "rollback_failed": return t("task.state.rollbackFailed");
    case "abandoned": return t("task.state.abandoned");
    case "preparing": return t("task.state.preparing");
    case "awaiting_confirm": return t("task.state.awaitingConfirm");
    case "paused": return t("task.state.paused");
    case "done": return t("task.state.done");
    default: return state || "";
  }
}

function taskStateClass(state) {
  if (state === "succeeded" || state === "done") return "ok";
  if (state === "failed" || state === "interrupted" || state === "rollback_failed") return "bad";
  if (TASK_RUNNING_STATES.includes(state)) return "run";
  return "idle";
}

// ── 提交入口用：登记一个刚提交的任务，立即切到快轮询 ─────────────────────────────

function trackSubmittedTask(taskId, kind, params, deduplicated = false) {
  if (!taskId) return;
  const row = taskTable.get(taskId) || { id: taskId, source: "jobs", kind, params: params || {}, state: "queued", group: "active" };
  taskTable.set(taskId, row);
  taskTracked.add(taskId);
  // 查重命中（同样的任务还没结束）：不新建，提示已在队列里（P4.33 §4.7）。
  showToast(deduplicated ? t("task.toast.deduplicated", taskTitle(row)) : t("task.toast.submitted", taskTitle(row)), "info");
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
  // 回收区页签不随任务轮询重画（P4.36）：缓存没变，每秒重建 DOM 会吞掉落在重建间隙的「彻底删除」点击。
  if (taskDrawerOpen() && taskDrawerTab !== "trash") {
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
  // 取消后回滚、中断任务回滚：都提示「已回滚」（详情里有回执与 .trash/ 位置）。
  else if (row.state === "cancelled" && row.rollback) showToast(t("task.toast.rolledBack", title), "info");
  else if (row.state === "cancelled") showToast(t("task.toast.cancelled", title), "info");
  else showToast(t("task.toast.failed", title, taskStateLabel(row.state)), "bad");
  loadPages(); // 写任务可能新建/改了页：刷新右栏列表
  // 撤回 / 恢复 / 删页 / 回滚会改回收区（P4.36、P4.34 §6.4）：回收区页签开着就重拉，免得还显示旧状态与「恢复」按钮
  const touchesTrash = ["remove", "restore", "prune"].includes(row.kind) || !!row.rollback;
  if (touchesTrash && taskDrawerOpen() && taskDrawerTab === "trash") loadTrash();
}

// ── 顶栏徽标 ────────────────────────────────────────────────────────────────

function renderTaskBadge() {
  const badge = $("#task-badge");
  if (!badge) return;
  const rows = [...taskView.attention, ...taskView.active];
  // 刚提交、还没出现在列表里的任务也算排队，免得提交后徽标空一拍。
  for (const id of taskTracked) if (!rows.some((r) => r.id === id) && taskTable.get(id)) rows.push(taskTable.get(id));
  const running = rows.filter(taskIsRunning).length;
  const queued = rows.filter((r) => r.state === "queued" || r.state === "waiting_lock" || taskIsWaitingRollback(r)).length;
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

// 从任务相关入口（完成 toast、对话里「查看任务」）打开：总是落在「任务」页签，不停在上次的回收区缓存上（P4.36）。
function openTaskList() {
  if (taskDrawerTab !== "tasks") {
    taskDrawerTab = "tasks";
    renderTaskTabs();
  }
  openTaskDrawer();
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
  if (taskDrawerTab === "trash") { // 回收区页签（P4.36）：只用缓存重画，不随任务轮询重拉
    renderTrashTab(body);
    body.scrollTop = scrollTop;
    return;
  }
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
  if ((row.state === "waiting_lock" || row.state === "rolling_back") && row.holder) bits.push(t("task.waitingHolder", row.holder)); // 谁占着库
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

// 详情顶部的操作按钮（P4.33 T2）：排队 / 等锁 / 运行中的可取消（运行中的取消会回滚）；中断 / 回滚失败的
// 可回滚或保留现状（有前像时）；已结束的可重试；没有前像的可删除记录。
function renderTaskActions(row, box) {
  const noPreimage = row.kind === "remove" || row.kind === "restore"; // 不拍前像（P4.35 / P4.36）
  const actions = noPreimage ? removeTaskActions(row) : [];
  if (!noPreimage) pushTaskActions(row, actions);
  if (!actions.length) return;
  const bar = document.createElement("div");
  bar.className = "task-actions";
  for (const [act, label] of actions) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.addEventListener("click", (e) => { e.stopPropagation(); taskAction(row, act, b); });
    bar.appendChild(b);
  }
  box.appendChild(bar);
}

// 撤回（P4.35）：不拍前像——只能在开始前取消；没有「回滚 / 保留现状」；「重试」换成重新预览（服务端直接重试 409）。
function removeTaskActions(row) {
  const actions = [];
  if (["queued", "waiting_lock"].includes(row.state)) actions.push(["cancel", t("task.action.cancel")]);
  if (["failed", "cancelled", "interrupted"].includes(row.state) && !row.superseded_by) {
    actions.push(["repreview", t("task.action.repreview")]);
  }
  if (["succeeded", "failed", "cancelled", "interrupted", "abandoned"].includes(row.state)) {
    actions.push(["delete", t("task.action.delete")]);
  }
  return actions;
}

function pushTaskActions(row, actions) {
  // 回滚中：只有还没开始的（排队或等命令行释放库）可撤下；已开始执行的不给按钮（服务端也会 409）。
  if (["queued", "waiting_lock", "running"].includes(row.state) || taskIsWaitingRollback(row)) {
    actions.push(["cancel", t("task.action.cancel")]);
  }
  if ((row.state === "interrupted" || row.state === "rollback_failed") && row.has_preimage) {
    actions.push(["rollback", t("task.action.rollback")]);
  }
  if (row.state === "interrupted" || row.state === "rollback_failed") actions.push(["abandon", t("task.action.abandon")]);
  if (["succeeded", "failed", "cancelled", "interrupted", "rollback_failed", "abandoned"].includes(row.state)) {
    // 有前像的先回滚或保留现状：先重试再回滚旧任务，会把重试的写入一并移走。
    if (!row.has_preimage) {
      actions.push(["retry", t("task.action.retry")]);
      actions.push(["delete", t("task.action.delete")]);
    }
  }
}

// 需要确认的操作：运行中取消（会回滚）、回滚、保留现状。
function taskActionConfirm(row, act) {
  if (act === "cancel" && row.state === "running") return window.confirm(t("task.confirm.cancelRunning"));
  if (act === "rollback") return window.confirm(t("task.confirm.rollback"));
  if (act === "abandon") {
    return window.confirm(row.has_preimage ? t("task.confirm.abandon") : t("task.confirm.abandonNoPreimage"));
  }
  return true;
}

async function taskAction(row, act, btn) {
  if (act === "repreview" && row.kind === "restore") { openRestorePreview((row.params || {}).name); return; } // 恢复：回收区重新预览（trash.js）
  if (act === "repreview") { reopenRemoveFromTask(row); return; } // 撤回：重新预览（remove.js）
  if (!taskActionConfirm(row, act)) return;
  btn.disabled = true;
  const id = encodeURIComponent(row.id);
  const url = act === "delete" ? `/api/tasks/${id}` : `/api/tasks/${id}/${act}`;
  try {
    const res = await fetch(url, { method: act === "delete" ? "DELETE" : "POST" });
    const data = await res.json().catch(() => ({}));
    if (res.status === 423) throw new Error(t("task.busyWritable"));
    if (!res.ok) throw new Error(apiDetail(data, res.status));
    if (act === "retry") {
      // 服务端建不了任务记录（workspace 写不了）时作业照跑、只是不进任务中心：也要告诉用户已提交，免得再点一次。
      if (data.task_id) trackSubmittedTask(data.task_id, row.kind, row.params, data.deduplicated);
      else showToast(t("task.toast.submittedUntracked", taskTitle(row)), "info");
    }
    if (act === "delete") {
      taskTable.delete(row.id);
      taskTracked.delete(row.id);
      taskExpanded = null;
      for (const g of ["attention", "active", "recent"]) taskView[g] = taskView[g].filter((r) => r.id !== row.id);
    }
    if (act === "cancel" && data.state === "cancelled") {
      taskTracked.delete(row.id); // 自己取消的：不再弹「任务已取消」toast
      const cur = taskTable.get(row.id);
      if (cur) taskTable.set(row.id, { ...cur, state: "cancelled", group: "recent" });
    }
    // 运行中取消（回滚中）/ 回滚：继续跟踪，结束时按结果弹 toast（回滚失败要让人看到）。
    if (data.state === "cancelling" || data.state === "rolling_back") {
      const cur = taskTable.get(row.id);
      if (cur) taskTable.set(row.id, { ...cur, state: data.state, group: "active" });
      taskTracked.add(row.id);
    }
    if (act === "cancel" && (data.state === "interrupted" || data.state === "rollback_failed")) {
      // 撤下了排队中的回滚：回到「需要处理」。
      const cur = taskTable.get(row.id);
      if (cur) taskTable.set(row.id, { ...cur, state: data.state, group: "attention", holder: null });
      taskTracked.delete(row.id);
    }
    if (act === "abandon") {
      const cur = taskTable.get(row.id);
      if (cur) taskTable.set(row.id, { ...cur, state: "abandoned", group: "recent", has_preimage: false });
      for (const g of ["attention", "active"]) taskView[g] = taskView[g].filter((r) => r.id !== row.id);
    }
    if (act !== "delete" && act !== "retry") taskLog.final = false; // 让展开的详情再拉一次新状态
  } catch (e) {
    showToast(t("task.actionFail", e.message), "bad");
  } finally {
    btn.disabled = false;
  }
  renderTaskDrawer();
  scheduleTaskPoll(0);
}

function taskHint(box, text, cls = "muted") {
  const p = document.createElement("p");
  p.className = cls;
  p.textContent = text;
  box.appendChild(p);
}

// 回滚回执（取消后的回滚、中断任务的回滚）：写回 / 移走多少、trash 在哪、raw/ 与 check 的复核结果。
function renderRollbackReceipt(rb, box) {
  if (!rb) return;
  if (rb.error) { taskHint(box, t("task.rollback.error", rb.error), "report-bad"); return; }
  const restored = (rb.restored || []).length, removed = (rb.removed || []).length;
  taskHint(box, restored + removed ? t("task.rollback.done", restored, removed, rb.trash || "") : t("task.rollback.nothing"));
  if (rb.raw_ok === false) taskHint(box, t("task.rollback.rawChanged", (rb.raw_changes || []).join(", ")), "report-bad");
  if (rb.check_ok === false) taskHint(box, t("task.rollback.newViolations", (rb.new_violations || []).length), "report-bad");
  if ((rb.links_skipped || []).length) taskHint(box, t("task.rollback.linksSkipped", rb.links_skipped.join(", ")), "report-bad");
}

function renderTaskDetail(row, box) {
  if (taskLog.id !== row.id) return;
  renderTaskActions(row, box);
  // 回执里的 `.trash/…`（撤回、回滚都会写）：给「在回收区查看」链接（P4.36）
  renderTrashLinks(box, taskLog.text, row.rollback && row.rollback.trash);
  if (row.state === "interrupted" && row.kind === "remove") {
    // 撤回没有前像（P4.35 §3.5）：不说「检查后重新提交」，引导重新预览；回收区可能已有本次的 manifest
    if (row.superseded_by) taskHint(box, t("task.remove.supersededHint"), "muted");
    else taskHint(box, t("task.remove.interruptedHint"), "report-bad");
  } else if (row.state === "interrupted" && row.kind === "restore") {
    if (row.superseded_by) taskHint(box, t("task.restore.supersededHint"), "muted");
    else taskHint(box, t("task.restore.interruptedHint"), "report-bad");
  } else if (row.state === "cancelled" && row.kind === "restore") {
    taskHint(box, t("task.restore.cancelledHint"));
  } else if (row.state === "cancelled" && row.kind === "remove") {
    taskHint(box, t("task.remove.cancelledHint")); // 撤回没有「重试」：引导重新预览
  } else if (row.state === "interrupted") {
    taskHint(box, t("task.interruptedHint"), "report-bad");
    if (row.has_preimage) taskHint(box, t("task.interruptedRollbackHint"));
  } else if (row.state === "rollback_failed") {
    taskHint(box, t("task.rollbackFailedHint"), "report-bad");
    renderRollbackReceipt(row.rollback, box);
  } else if (row.state === "cancelling") {
    taskHint(box, t("task.cancellingHint"));
  } else if (row.state === "rolling_back") {
    taskHint(box, t("task.rollingBackHint"));
  } else if (row.state === "cancelled" && row.rollback) {
    renderRollbackReceipt(row.rollback, box);
  } else if (row.state === "cancelled") {
    taskHint(box, t("task.cancelledHint"));
  } else if (row.state === "waiting_lock") {
    taskHint(box, t("task.waitingLockHint"));
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
  el.addEventListener("click", () => { el.remove(); openTaskList(); });
  box.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

$("#tasks-btn").addEventListener("click", () => {
  if (taskDrawerOpen()) { closeTaskDrawer(); return; }
  openTaskDrawer();
  if (taskDrawerTab === "trash") loadTrash(); // 上次停在回收区页签：重开时重拉，不显示旧缓存（P4.36）
});
$("#task-drawer-close").addEventListener("click", closeTaskDrawer);
