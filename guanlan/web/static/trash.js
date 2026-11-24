"use strict";
// 回收区（P4.36 T1，见 docs/P4.36-Web回收区.md）。经典脚本，共享全局作用域。
//
// - 任务中心抽屉顶部「任务 | 回收区」两个页签；回收区页签只在切进来、或删完一条后拉一次 GET /api/trash，
//   任务轮询的整体重画只用缓存，不跟着每秒重拉。
// - 详情点开时 GET /api/trash/{name}；「彻底删除」在详情里二次确认（不弹浏览器 confirm），POST /api/trash/purge。
// - 入口：任务详情里的 `.trash/…` 路径、体检的回收区一行，都调 openTrashEntry(name)。
// - t() 首参须为字面量（决策P4.7-8）：种类 / 状态用 switch 取文案。

let taskDrawerTab = "tasks"; // "tasks" | "trash"
let trashState = { data: null, error: null, loading: false, expanded: null, detail: null, confirming: null, busy: false, msg: null };
let trashLoadSeq = 0; // 只认最后一次 loadTrash 的结果：先发后到的旧响应不能覆盖新列表、也不能把刚展开的条目收起

function trashKindLabel(kind) {
  switch (kind) {
    case "remove": return t("trash.kind.remove");
    case "remove_batch": return t("trash.kind.removeBatch");
    case "rollback": return t("trash.kind.rollback");
    default: return t("trash.kind.unknown");
  }
}

function trashStateLabel(state) {
  switch (state) {
    case "complete": return t("trash.state.complete");
    case "incomplete": return t("trash.state.incomplete");
    case "partial": return t("trash.state.partial");
    case "restoring": return t("trash.state.restoring");
    case "restored": return t("trash.state.restored");
    default: return t("trash.state.unknown");
  }
}

function trashStateClass(state) {
  if (state === "complete" || state === "restored") return "ok";
  if (state === "incomplete" || state === "restoring") return "run";
  if (state === "partial") return "bad";
  return "idle";
}

// fmtBytes 用 staging.js 的全局定义（经典脚本同名函数后者覆盖前者，不在此重复定义）。

// ── 恢复（T2）：预览浮层 → 确认 → POST /api/restore → 任务中心 ─────────────────────

let restoreState = null; // {name, data, error, seq}

async function openRestorePreview(name, notice = null) {
  if (!name) return;
  const st = { name, data: null, error: null, notice };
  restoreState = st;
  showOverlay("overlay.restore", `<p class="muted">${escapeHtml(t("common.running"))}</p>`);
  overlayRepaint = () => renderRestorePreview(st);
  st.repaint = overlayRepaint;
  try {
    const res = await fetch(`/api/trash/restore/preview?name=${encodeURIComponent(name)}`);
    const data = await res.json().catch(() => ({}));
    if (restoreState !== st || overlayRepaint !== st.repaint) return;
    if (res.ok) st.data = data;
    else st.error = apiDetail(data, res.status);
  } catch (e) {
    if (restoreState !== st || overlayRepaint !== st.repaint) return;
    st.error = e.message;
  }
  renderRestorePreview(st);
}

function renderRestorePreview(st) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  if (st.notice) taskHint(box, st.notice, "report-bad");
  if (st.error) { taskHint(box, t("trash.restorePreviewFail", st.error), "report-bad"); return; }
  const d = st.data;
  if (!d) { taskHint(box, t("common.running")); return; }
  if (d.blocked) { taskHint(box, t("trash.restoreBlocked", d.blocked), "report-bad"); return; }
  if (d.ok) {
    const top = document.createElement("div");
    top.className = "heal-top";
    const go = document.createElement("button");
    go.className = "heal-go";
    go.textContent = t("trash.restoreGo");
    go.addEventListener("click", () => submitRestore(st, go));
    top.appendChild(go);
    box.appendChild(top);
  }
  if (d.resume) taskHint(box, t("trash.restoreResume"));
  trashList(box, t("trash.restoreHead.items"), d.items || [], (it) =>
    `${it.status === "done" ? "✓" : "·"} ${it.path}${it.status === "done" ? t("trash.restoreDone") : ""}`);
  const label = (m) => {
    switch (m) {
      case "clean": return t("trash.edit.clean");
      case "append": return t("trash.edit.append");
      case "noop": return t("trash.edit.noop");
      default: return t("trash.edit.skip");
    }
  };
  trashList(box, t("trash.restoreHead.edits"), d.edits || [], (e) =>
    `· ${e.page} ← ${(e.add || []).join("、")}（${label(e.mode)}${e.reason && e.mode === "skip" ? "：" + e.reason : ""}）`);
  trashList(box, t("trash.restoreHead.index"), d.index_add || [], (p) => `+ ${p}`);
  trashList(box, t("trash.restoreHead.orphaned"), d.orphaned || [], (p) => `· ${p}`);
  trashList(box, t("trash.restoreHead.conflicts"), d.conflicts || [], (c) => `✗ ${c}`, "report-bad");
  taskHint(box, t("trash.restoreNote"));
}

async function submitRestore(st, btn) {
  btn.disabled = true;
  let res, data;
  try {
    res = await fetch("/api/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: st.name, plan_digest: st.data.plan_digest || st.data.digest }),
    });
    data = await res.json().catch(() => ({}));
  } catch (e) {
    btn.disabled = false;
    if (restoreState === st && overlayRepaint === st.repaint) showOverlayError(t("common.submitFail", e.message));
    return;
  }
  if (restoreState !== st || overlayRepaint !== st.repaint) return;
  if (!res.ok) {
    if (res.status === 409 && data.detail && data.detail.reason === "stale") {
      // 预览之后有变化：旧摘要再点也只会再 409，直接按当前盘面重新预览
      openRestorePreview(st.name, t("trash.restoreStale"));
      return;
    }
    btn.disabled = false;
    if (res.status === 423) showOverlayError(t("task.busyWritable"));
    else if (res.status === 429) showOverlayError(t("task.limit"));
    else showOverlayError(t("common.submitFail", apiDetail(data, res.status)));
    return;
  }
  $("#overlay").classList.add("hidden");
  trackSubmittedTask(data.task_id, "restore", { name: st.name }, data.deduplicated);
}

function renderTaskTabs() {
  document.querySelectorAll(".task-tab").forEach((b) => {
    const on = b.dataset.tab === taskDrawerTab;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
}

function setTaskDrawerTab(tab) {
  taskDrawerTab = tab === "trash" ? "trash" : "tasks";
  trashState.msg = null; // 上一次删除的结果提示只在当次显示
  trashState.confirming = null; // 切走再切回不停在「确认删除」上
  renderTaskTabs();
  if (taskDrawerTab === "trash") loadTrash();
  else renderTaskDrawer();
}

// 外部入口：打开抽屉、切到回收区，name 给了就展开那一条（找不到时列表照常显示）。
function openTrashEntry(name) {
  taskDrawerTab = "trash";
  trashState.expanded = name || null;
  trashState.detail = null;
  trashState.confirming = null;
  trashState.msg = null;
  if (!taskDrawerOpen()) openTaskDrawer();
  renderTaskTabs();
  loadTrash();
}

async function loadTrash() {
  const seq = ++trashLoadSeq;
  trashState.loading = true;
  trashState.error = null;
  renderTaskDrawer();
  let data = null, error = null;
  try {
    const res = await fetch("/api/trash");
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(apiDetail(body, res.status));
    data = body;
  } catch (e) {
    error = e.message;
  }
  if (seq !== trashLoadSeq) return; // 已有更新的一次加载在路上
  if (data) trashState.data = data;
  else { trashState.data = null; trashState.error = error; }
  trashState.loading = false;
  if (trashState.expanded && trashState.data && trashState.data.entries.some((x) => x.name === trashState.expanded)) {
    await loadTrashDetail(trashState.expanded);
  } else {
    trashState.expanded = null;
  }
  if (taskDrawerTab === "trash") renderTaskDrawer();
}

async function loadTrashDetail(name) {
  trashState.detail = null;
  try {
    const res = await fetch(`/api/trash/${encodeURIComponent(name)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(apiDetail(data, res.status));
    if (trashState.expanded === name) trashState.detail = data;
  } catch (e) {
    if (trashState.expanded === name) trashState.detail = { error: e.message };
  }
}

async function toggleTrashDetail(name) {
  trashState.confirming = null;
  trashState.msg = null;
  if (trashState.expanded === name) {
    trashState.expanded = null;
    trashState.detail = null;
    renderTaskDrawer();
    return;
  }
  trashState.expanded = name;
  trashState.detail = null;
  renderTaskDrawer();
  await loadTrashDetail(name);
  if (taskDrawerTab === "trash") renderTaskDrawer();
}

function renderTrashTab(body) {
  const st = trashState;
  if (st.msg) taskHint(body, st.msg.text, st.msg.cls);
  if (st.error) { taskHint(body, t("trash.loadFail", st.error), "report-bad"); return; }
  if (st.loading && st.data) taskHint(body, t("common.running")); // 重拉中：下面是上次的缓存
  if (!st.data) { taskHint(body, t("common.running")); return; }
  const entries = st.data.entries || [];
  if (!entries.length) { taskHint(body, t("trash.empty")); return; }
  taskHint(body, t("trash.summary", entries.length, fmtBytes(st.data.size)));
  for (const e of entries) body.appendChild(renderTrashRow(e));
}

function renderTrashRow(e) {
  const el = document.createElement("div");
  el.className = "task-row clickable";
  el.dataset.name = e.name;
  const head = document.createElement("div");
  head.className = "task-head";
  const title = document.createElement("span");
  title.className = "task-title";
  title.textContent = e.name; // 目录名按字面显示
  const state = document.createElement("span");
  state.className = `task-state ${trashStateClass(e.state)}`;
  state.textContent = trashStateLabel(e.state);
  head.append(title, state);
  head.addEventListener("click", () => toggleTrashDetail(e.name));
  el.appendChild(head);
  const meta = document.createElement("div");
  meta.className = "task-meta";
  const bits = [trashKindLabel(e.kind)];
  if (e.subject) bits.push(e.subject);
  bits.push(e.when || "", fmtBytes(e.size), t("trash.files", e.files));
  if (e.protected && e.protected.length) bits.push(t("trash.protected"));
  if (e.invalid) bits.push(t("trash.invalid"));
  meta.textContent = bits.filter(Boolean).join(" · ");
  el.appendChild(meta);
  if (trashState.expanded === e.name) {
    const box = document.createElement("div");
    box.className = "task-detail";
    renderTrashDetail(e, box);
    el.appendChild(box);
  }
  return el;
}

function trashList(box, head, items, render, cls = "") {
  if (!items || !items.length) return;
  const h = document.createElement("p");
  h.textContent = head;
  if (cls) h.className = cls;
  box.appendChild(h);
  const pre = document.createElement("pre");
  pre.textContent = items.map(render).join("\n"); // textContent：路径按字面显示
  box.appendChild(pre);
}

function renderTrashDetail(e, box) {
  const d = trashState.detail;
  renderTrashActions(e, box);
  if (!d) { taskHint(box, t("common.running")); return; }
  if (d.error) { taskHint(box, t("trash.loadFail", d.error), "report-bad"); return; }
  if (d.note) taskHint(box, d.note);
  if (d.state === "incomplete") taskHint(box, t("trash.hint.incomplete"), "report-bad");
  if (d.state === "partial") taskHint(box, t("trash.hint.partial"));
  for (const r of d.protected || []) taskHint(box, t("trash.protectedReason", r), "report-bad");
  if (d.group && d.group.raw_dir) taskHint(box, t("trash.group", d.group.raw_dir, (d.group.members || []).length));
  const itemsHead = d.kind === "rollback" ? t("trash.head.rollbackItems") : t("trash.head.items");
  // saved_as 为空的是不合规的来源编辑记录（slug_dropped_from 的页面路径），不是移进来的文件：单列，不混进文件清单
  const allItems = d.items || [];
  for (const it of allItems.filter((x) => !x.saved_as)) taskHint(box, `✗ ${it.path}（${it.invalid || ""}）`, "report-bad");
  trashList(box, itemsHead, allItems.filter((x) => x.saved_as), (it) => {
    if (it.invalid) return `✗ ${it.path}（${it.invalid}）`;
    return `${it.archived ? "·" : "?"} ${it.path}${it.archived ? "" : t("trash.notArchived")}`;
  });
  trashList(box, t("trash.head.dropped"), d.slug_dropped_from || [], (x) => `· ${x.page} ← ${x.dropped_slug}`);
  trashList(box, t("trash.head.index"), d.index_lines_removed || [], (ln) => `− ${ln}`);
  if (d.restore) taskHint(box, d.restore.finished_at ? t("trash.restoredAt", d.restore.finished_at) : t("trash.restoringHint"));
  if (d.restore_blocked && (d.kind === "remove" || d.kind === "remove_batch") && d.state !== "restored") {
    taskHint(box, t("trash.restoreBlocked", d.restore_blocked));
  }
}

function renderTrashActions(e, box) {
  const bar = document.createElement("div");
  bar.className = "task-actions";
  const protectedNow = (e.protected && e.protected.length) || (trashState.detail && (trashState.detail.protected || []).length);
  if (trashState.confirming === e.name) {
    const warn = document.createElement("p");
    warn.className = "report-bad";
    // 已恢复的条目里只剩记录文件：说清楚删的只是留档，已恢复的文件不受影响
    warn.textContent = e.state === "restored"
      ? t("trash.purgeRecordConfirm", e.name)
      : t("trash.purgeConfirm", e.name, fmtBytes(e.size), e.files);
    box.appendChild(warn);
    const yes = document.createElement("button");
    yes.type = "button";
    yes.textContent = e.state === "restored" ? t("trash.purgeRecordYes") : t("trash.purgeYes");
    yes.disabled = trashState.busy;
    yes.addEventListener("click", () => purgeTrash(e.name));
    const no = document.createElement("button");
    no.type = "button";
    no.textContent = t("trash.purgeNo");
    no.disabled = trashState.busy;
    no.addEventListener("click", () => { trashState.confirming = null; renderTaskDrawer(); });
    bar.append(yes, no);
  } else {
    // 恢复（T2）：可恢复的撤回条目给「恢复」，点开预览浮层。
    if (e.restorable) {
      const rs = document.createElement("button");
      rs.type = "button";
      rs.textContent = t("trash.restore");
      rs.addEventListener("click", () => openRestorePreview(e.name));
      bar.appendChild(rs);
    }
    const del = document.createElement("button");
    del.type = "button";
    del.textContent = e.state === "restored" ? t("trash.purgeRecord") : t("trash.purge");
    const ready = trashState.detail && !trashState.detail.error; // 详情没加载好（或失败）前不给删
    del.disabled = !!protectedNow || !ready;
    if (protectedNow) del.title = t("trash.protected");
    del.addEventListener("click", () => { trashState.confirming = e.name; trashState.msg = null; renderTaskDrawer(); });
    bar.appendChild(del);
  }
  box.appendChild(bar);
}

async function purgeTrash(name) {
  trashState.busy = true;
  renderTaskDrawer();
  let res, data;
  try {
    res = await fetch("/api/trash/purge", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    data = await res.json().catch(() => ({}));
  } catch (e) {
    res = null;
    data = { detail: e.message };
  }
  trashState.busy = false;
  trashState.confirming = null;
  if (res && res.ok) {
    trashState.msg = { text: t("trash.purged", data.purged || name, fmtBytes(data.size)), cls: "report-ok" };
    trashState.expanded = null;
    trashState.detail = null;
    // 先从缓存里去掉已删的一条：重拉期间的重画不再显示它（否则还能点开、再点删除只得到 404）
    if (trashState.data && Array.isArray(trashState.data.entries)) {
      const gone = data.purged || name;
      trashState.data.entries = trashState.data.entries.filter((x) => x.name !== gone && x.name !== name);
    }
  } else if (res && res.status === 423) {
    trashState.msg = { text: t("task.busyWritable"), cls: "report-bad" };
  } else {
    trashState.msg = { text: t("trash.purgeFail", apiDetail(data, res ? res.status : 0)), cls: "report-bad" };
  }
  await loadTrash();
}

// 任务详情里出现的 `.trash/<条目>` 路径：每个给一个「在回收区查看」链接（撤回回执、回滚回执都会写这个路径）。
function trashNamesIn(text) {
  const out = new Set();
  // 只认系统条目名的形态（`<名字>@<YYYYmmddTHHMMSS>[_微秒][-n]`）：整批撤回的目录名取 raw 目录名，可能含空格。
  const re = /\.trash\/([^/\n]+?@\d{8}T\d{6}(?:_\d{6})?(?:-\d+)?)(?![\d_-])/g;
  let m;
  while ((m = re.exec(String(text || "")))) out.add(m[1]);
  return [...out];
}

function renderTrashLinks(box, ...texts) {
  const names = new Set();
  for (const s of texts) for (const n of trashNamesIn(s)) names.add(n);
  if (!names.size) return;
  const bar = document.createElement("div");
  bar.className = "task-actions";
  for (const n of names) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = t("trash.openLink", n);
    b.addEventListener("click", () => openTrashEntry(n));
    bar.appendChild(b);
  }
  box.appendChild(bar);
}

document.querySelectorAll(".task-tab").forEach((b) => b.addEventListener("click", () => setTaskDrawerTab(b.dataset.tab)));
