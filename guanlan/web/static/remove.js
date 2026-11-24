"use strict";
// 撤回源（P4.35，见 docs/P4.35-Web撤回源.md）。经典脚本，共享全局作用域。
//
// - 入口：摘要页（wiki/sources/*.md）页头的「撤回此源」；任务中心里撤回任务的「重新预览」。
// - 预览：GET /api/remove/preview（零 LLM、不写盘）。分卷摘要页可在「此卷 / 原件全部分卷」间切换，切换即重新加载清单。
// - 确认：POST /api/remove（带预览给的 plan_digest）→ 任务中心。作业取得写锁后重算、核对摘要，不一致零写入。
// - 预览本身就是确认面：逐项列出将移入回收区的文件、将摘除的来源、将删除的 index 行、失源页与悬链页，不截断。

let removeState = null; // {source, scope, task, data, error, seq}

function removeDetailText(detail, status) {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  if (Array.isArray(detail)) return detail.map((x) => (x && x.msg) || String(x)).join("；");
  return `HTTP ${status}`;
}

// 摘要页才给入口；reader 下端点未注册，不显示。
function canRemoveFromPage(path) {
  return !readerMode && /^wiki\/sources\/[^/]+\.md$/.test(path); // 只认顶层：服务端按文件名归一 slug，子目录页会对不上
}

// opts：{source, scope} 从摘要页进；{task} 从任务中心续跑整批。
async function openRemovePreview(opts) {
  removeState = { source: opts.source || null, scope: opts.task ? "all" : opts.scope || "one", task: opts.task || null, data: null, error: null };
  showOverlay("overlay.remove", `<p class="muted">${escapeHtml(t("common.running"))}</p>`);
  const st = removeState;
  st.repaint = () => renderRemovePreview(st); // 语言切换纯重渲染（吃缓存，不重拉）
  overlayRepaint = st.repaint;
  await loadRemovePreview(st);
}

async function loadRemovePreview(st) {
  st.data = null;
  st.error = null;
  const seq = (st.seq = (st.seq || 0) + 1); // 切换范围会再发一次：较早发出、较晚返回的旧清单不能覆盖新的
  const q = st.task
    ? `task=${encodeURIComponent(st.task)}`
    : `source=${encodeURIComponent(st.source)}&scope=${encodeURIComponent(st.scope)}`;
  try {
    const res = await fetch(`/api/remove/preview?${q}`);
    const data = await res.json().catch(() => ({}));
    // 期间又开了别的预览、切换了范围，或浮层换成了别的面板（overlayRepaint 被替换）。只是关掉浮层不清 overlayRepaint，迟到的清单画进隐藏的浮层，无害
    if (removeState !== st || st.seq !== seq || overlayRepaint !== st.repaint) return;
    if (res.ok) st.data = data;
    else st.error = { status: res.status, detail: data.detail };
  } catch (e) {
    if (removeState !== st || st.seq !== seq || overlayRepaint !== st.repaint) return;
    st.error = { status: 0, detail: e.message };
  }
  renderRemovePreview(st);
}

function removeList(box, head, items, render, cls = "") {
  if (!items || !items.length) return;
  const h = document.createElement("p");
  h.textContent = head;
  if (cls) h.className = cls;
  box.appendChild(h);
  for (const it of items) {
    const row = document.createElement("div");
    row.className = "heal-item postponed";
    row.textContent = render(it); // textContent：路径、页名按字面显示，无注入
    box.appendChild(row);
  }
}

function renderRemoveScope(st, box) {
  // 从任务中心续跑只有整批一种；单卷预览里，raw 是分卷时才给「原件全部分卷」。
  if (st.task) return;
  const canAll = st.scope === "all" || (st.data && st.data.group_available);
  if (!canAll) return;
  const bar = document.createElement("div");
  bar.className = "remove-scope";
  for (const [value, label] of [["one", t("remove.scopeOne")], ["all", t("remove.scopeAll")]]) {
    const lab = document.createElement("label");
    const rb = document.createElement("input");
    rb.type = "radio";
    rb.name = "remove-scope";
    rb.checked = st.scope === value;
    rb.addEventListener("change", () => {
      if (!rb.checked) return;
      st.scope = value;
      $("#overlay-body").innerHTML = `<p class="muted">${escapeHtml(t("common.running"))}</p>`;
      loadRemovePreview(st);
    });
    lab.append(rb, document.createTextNode(" " + label));
    bar.appendChild(lab);
  }
  box.appendChild(bar);
}

function renderRemoveError(st, box) {
  const { status, detail } = st.error;
  const p = document.createElement("p");
  p.className = "report-bad";
  p.textContent = t("remove.previewFail", removeDetailText(detail, status));
  box.appendChild(p);
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    if (detail.reason === "pending_task" && detail.task_id) {
      const go = document.createElement("button");
      go.type = "button";
      go.textContent = t("remove.resumeFromTask");
      go.addEventListener("click", () => openRemovePreview({ task: detail.task_id }));
      box.appendChild(go);
    }
    for (const d of detail.details || []) {
      if (d.pages) removeList(box, d.raw, d.pages, (x) => `· ${x.page}（${(x.basis || []).join("、")}）`);
      else if (d.page) removeList(box, d.page, d.raws || [], (r) => `· ${r}`);
      else if (d.task_id) removeList(box, t("remove.importTask"), [d.task_id], (x) => `· ${x}`);
    }
  }
}

function renderRemovePreview(st) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  renderRemoveScope(st, box);
  if (st.error) { renderRemoveError(st, box); return; }
  const d = st.data;
  if (!d) { box.insertAdjacentHTML("beforeend", `<p class="muted">${escapeHtml(t("common.running"))}</p>`); return; }
  const all = d.scope === "all";

  const top = document.createElement("div");
  top.className = "heal-top";
  const go = document.createElement("button");
  go.className = "heal-go";
  go.textContent = t("remove.go");
  go.addEventListener("click", () => submitRemove(st));
  top.appendChild(go);
  box.appendChild(top);

  if (d.requested && d.slug && d.requested !== d.slug) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = t("remove.legacyNote", d.slug, d.requested);
    box.appendChild(note);
  }
  if (all) {
    const g = d.group || {};
    const members = g.members || [];
    const head = document.createElement("p");
    head.textContent = t("remove.groupHead", g.raw_dir || "", members.length);
    box.appendChild(head);
    if (st.task && d.done_members) {
      const prog = document.createElement("p");
      prog.className = "muted";
      prog.textContent = t("remove.progress", d.done_members, members.length);
      box.appendChild(prog);
    }
    removeList(box, t("remove.relocateHead", (d.relocate || []).length), members, (m) =>
      `· ${t("remove.member", m.index)}${m.raw}` + (m.page ? ` · ${m.page}` : "") + (m.images ? ` · ${m.images}/` : ""));
    removeList(box, t("remove.sharedHead"), g.shared_pages || [], (s) => `· ${s.page} ← ${(s.raws || []).join("、")}`);
    removeList(box, t("remove.dropHead"), d.drop_slug || [], (x) =>
      `· ${x.page}：${t("remove.dropItem", (x.dropped || []).join("、"), (x.before_sources || []).length - (x.dropped || []).length)}`);
  } else {
    removeList(box, t("remove.relocateHead", (d.relocate || []).length), d.relocate || [], (r) => `· ${r}`);
    removeList(box, t("remove.dropHead"), d.drop_slug || [], (x) =>
      `· ${x.page}：${t("remove.dropItem", d.slug, (x.before_sources || []).filter((s) => s !== d.slug).length)}`);
  }
  removeList(box, t("remove.indexHead"), d.index_lines || [], (ln) => `− ${ln}`);
  removeList(box, t("remove.orphansHead"), d.orphans || [], (p) => `⚠ ${p}`, "report-bad");
  removeList(box, t("remove.backlinksHead"), d.backlinks || [], (p) => `⚠ ${p}`, "report-bad");
  removeList(box, t("remove.hintsHead"), d.hints || [], (h) => `· ${h.raw}：${(h.pages || []).join("、")}`);
  const note = document.createElement("p");
  note.className = "muted";
  note.textContent = t("remove.note");
  box.appendChild(note);
}

async function submitRemove(st) {
  const d = st.data;
  if (!d) return;
  const body = st.task
    ? { from_task: st.task, scope: "all", plan_digest: d.plan_digest }
    : { source: st.source, scope: st.scope, plan_digest: d.plan_digest };
  const btns = [...$("#overlay-body").querySelectorAll("button, input")].map((b) => [b, b.disabled]);
  for (const [b] of btns) b.disabled = true;
  let res, data;
  try {
    res = await fetch("/api/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    data = await res.json().catch(() => ({}));
  } catch (e) {
    for (const [b, was] of btns) b.disabled = was;
    if (removeState === st && overlayRepaint === st.repaint) showOverlayError(t("common.submitFail", e.message));
    return;
  }
  // 提交期间浮层可能已换成别的（overlayRepaint 随之替换）：不往别人的浮层里写错误、不把它关掉。
  const live = removeState === st && overlayRepaint === st.repaint;
  if (!res.ok) {
    for (const [b, was] of btns) b.disabled = was;
    if (!live) return;
    if (res.status === 423) showOverlayError(t("task.busyWritable"));
    else if (res.status === 429) showOverlayError(t("task.limit"));
    else if (data.detail && typeof data.detail === "object" && data.detail.reason) {
      // 结构化 409（待续任务 / 归属冲突 / 导入未完成）：与预览同一套错误视图，带「从任务中心继续」与依据清单
      st.error = { status: res.status, detail: data.detail };
      renderRemovePreview(st);
    }
    else showOverlayError(t("common.submitFail", removeDetailText(data.detail, res.status)));
    return;
  }
  if (live) $("#overlay").classList.add("hidden");
  const g = d.group || {};
  const params = d.scope === "all" ? { scope: "all", raw_dir: g.raw_dir } : { scope: "one", source: d.requested || d.slug }; // 同服务端信封 params.source（输入归一后的 slug）
  trackSubmittedTask(data.task_id, "remove", params, data.deduplicated);
}

// 任务中心「重新预览」：整批且确实进入过执行的走任务入口（旧身份只在信封里）；从任务入口续跑、却没进入执行的
// 回到它续跑的那条旧任务（自身不满足待续资格，服务端会 409）；其余按原入口重新预览。
function reopenRemoveFromTask(row) {
  const p = row.params || {};
  const entry = row.entry || {};
  if (p.scope === "all" && row.remove_writes_began) {
    openRemovePreview({ task: row.id });
  } else if (p.scope === "all" && !entry.source && entry.from_task) {
    openRemovePreview({ task: entry.from_task });
  } else if (p.scope === "all" && !entry.source) {
    openRemovePreview({ task: row.id });
  } else {
    openRemovePreview({ source: entry.source || p.source, scope: p.scope || "one" });
  }
}
