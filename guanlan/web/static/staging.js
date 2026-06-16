"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 投喂 + 暂存区（workspace 浏览/晋级/重整）。
// ── 投喂：粘贴正文 + 命名 → POST /api/raw → 一键 ingest（P4.1）──────────────────
//
// 顶栏「投喂」→ 浮层（文件名输入 + 正文 textarea + 存盘）。存盘期间按钮置「保存中…」并禁用
// （响应会等队列前序写作业，如在飞 ingest，可能数十秒——见 P4.1 §3.1）。409 就地给「改名/覆盖」；
// 成功后提示 + 一颗「立即 ingest」复用既有 triggerIngest。

$("#feed-btn").addEventListener("click", openFeed);

function openFeed() {
  showOverlay("overlay.feed", "");
  const box = $("#overlay-body");
  box.innerHTML = "";
  const nameInput = document.createElement("input");
  nameInput.id = "feed-name";
  nameInput.className = "feed-name";
  nameInput.type = "text";
  nameInput.placeholder = t("feed.namePlaceholder");
  nameInput.autocomplete = "off";
  const textarea = document.createElement("textarea");
  textarea.id = "feed-content";
  textarea.className = "feed-content";
  textarea.rows = 12;
  textarea.placeholder = t("feed.contentPlaceholder");
  const saveBtn = document.createElement("button");
  saveBtn.id = "feed-save";
  saveBtn.className = "feed-save";
  saveBtn.textContent = t("feed.save");
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
    saveBtn.textContent = t("feed.save");
  };
  if (!name || !content.trim()) {
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("feed.emptyErr"))}</span>`;
    return;
  }
  saveBtn.disabled = true;
  saveBtn.textContent = t("feed.saving"); // 会等队列前序写作业（如在飞 ingest），不可让用户以为卡死
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
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("feed.saveFail", e.message))}</span>`;
    return;
  }
  if (res.status === 409) {
    // 已存在：就地给「改名 / 覆盖」二选项（覆盖 = 带 overwrite=true 重发）。
    resetSave();
    status.innerHTML = `<span class="report-bad">${escapeHtml(data.detail || t("feed.conflictDefault"))}</span> `;
    const ow = document.createElement("button");
    ow.className = "feed-overwrite";
    ow.textContent = t("feed.overwrite");
    ow.addEventListener("click", () => submitFeed(true));
    status.appendChild(ow);
    return;
  }
  if (!res.ok) {
    resetSave();
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("feed.saveFail", data.detail || `HTTP ${res.status}`))}</span>`;
    return;
  }
  // 成功：复位按钮（可接着存下一篇）+ 提示 + 一键「立即 ingest」（复用既有 ingest 流）。存盘后
  // raw/ 列表已更新，下次开 ingest 选单即见新文件（无常驻列表，无需显式刷新）。
  resetSave();
  status.innerHTML = `<span class="report-ok">${escapeHtml(t("feed.saved", data.saved))}</span> `;
  const ingestBtn = document.createElement("button");
  ingestBtn.className = "feed-ingest";
  ingestBtn.textContent = t("feed.ingestNow");
  ingestBtn.addEventListener("click", () => triggerIngest(data.saved));
  status.appendChild(ingestBtn);
}

// ── 暂存区（P4.6）：浏览 workspace/ → 审阅/修订/晋级为源/删除 → ingest 串联 ──────────
//
// 顶栏「暂存区」→ 浮层：① 待解析 uploads/（[问 agent 解析] 回填 composer / [晋级为源]（仅 .md）/ 🗑）
// + ② 待晋级 parsed/（[预览]/[让 agent 修订] 回填 composer/[晋级为源]/🗑 + 勾选「合并选中…」）。
// 复用既有 #overlay / triggerIngest / pollJob / 投喂 slug-409 交互，不引新组件、不改两栏（决策P4-3）。
// 晋级/删除是宿主写：可写 turn 跑动期（workspace-write + chatStreaming）置灰，对应后端层③ 423（§7.3）。


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
    addMsg("note", t("staging.needWritableNote"));
  }
}

$("#staging-btn").addEventListener("click", openStaging);

async function openStaging() {
  stagingPath = null; // 每次从顶栏打开都回到根视图
  showOverlay("overlay.staging", `<p class="muted">${escapeHtml(t("staging.loading"))}</p>`);
  stagingOpen = true; // 须在 showOverlay 之后（它会清 stagingOpen）
  loadStaging();
}


// 拉当前目录（stagingPath）并渲染。目录已被删/失效（4xx）→ 退回根视图重拉一次（避免卡在坏路径）。
async function loadStaging() {
  const qs = stagingPath ? `?path=${encodeURIComponent(stagingPath)}` : "";
  let data;
  try {
    data = await getJSON(`/api/workspace${qs}`);
  } catch (e) {
    if (stagingPath !== null) { stagingPath = null; return loadStaging(); } // 坏路径 → 回根
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("staging.loadFail", e.message))}</p>`;
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
  overlayRepaint = () => renderStaging(data); // 语言切换纯重渲染（吃缓存 data，不重拉 /api/workspace）
  const box = $("#overlay-body");
  box.innerHTML = "";
  const blocked = hostWriteBlocked();
  if (blocked) {
    const warn = document.createElement("p");
    warn.className = "muted";
    warn.textContent = t("staging.busyBanner");
    box.appendChild(warn);
  }

  if (data.root) {
    // 根视图：两段（uploads / parsed）。每段列其**直接子项**（子目录可点入、文件带操作）。
    renderSection(box, t("staging.uploadsHead"), "uploads", "workspace/uploads",
      data.uploads || [], blocked, t("staging.uploadsEmpty"));
    renderSection(box, t("staging.parsedHead"), "parsed", "workspace/parsed",
      data.parsed || [], blocked, t("staging.parsedEmpty"), t("staging.parsedSub"));
  } else {
    // 目录视图：面包屑 + 返回上一级 + 该目录直接子项（一级一级点）。
    const crumb = document.createElement("div");
    crumb.className = "stage-crumb";
    const up = document.createElement("button");
    up.className = "stage-act";
    up.textContent = t("staging.up");
    up.addEventListener("click", stagingUp);
    const label = document.createElement("span");
    label.className = "stage-crumb-path";
    label.textContent = data.path; // textContent：路径按字面显示
    crumb.append(up, label);
    box.appendChild(crumb);
    renderSection(box, "", data.base, data.path, data.items || [], blocked, t("staging.dirEmpty"));
  }

  const flow = document.createElement("p");
  flow.className = "stage-flow muted";
  flow.textContent = t("staging.flow");
  box.appendChild(flow);
}

// 渲染一段目录列表：子目录行（可点入 + 整删）在前，文件行（按 base 决定 uploads/parsed 操作）在后。
// `base` ∈ {uploads, parsed} 决定文件操作集；`dirPath` 是本段所在目录（合并产物落此目录）。
function renderSection(box, headerText, base, dirPath, items, blocked, emptyText, sub) {
  if (headerText) box.appendChild(stagingHeader(headerText, sub));
  if (!items.length) { box.appendChild(stagingEmpty(t("staging.emptyWrap", emptyText))); return; }
  const selected = new Set();
  const mergeBtn = document.createElement("button");
  mergeBtn.className = "stage-merge";
  mergeBtn.textContent = t("staging.merge");
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
      const name = window.prompt(t("staging.mergePrompt"), suggested);
      if (!name) return;
      const list = picks.join("、");
      fillComposer(t("staging.mergeCmd", list, `${dirPath}/${name}`), { needWritable: true });
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
  nm.title = t("tip.enterDir");
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
  btn.title = blocked ? t("staging.writeBusy") : t("tip.delDir");
  btn.disabled = blocked;
  btn.addEventListener("click", async () => {
    if (!window.confirm(t("staging.delDirConfirm", it.name))) return;
    btn.disabled = true;
    try {
      const res = await fetch(`/api/workspace/dir?path=${encodeURIComponent(it.path)}`, { method: "DELETE" });
      if (res.status === 423) { btn.disabled = false; btn.title = t("staging.writableRetry"); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      loadStaging(); // 重拉刷新（目录连同子项已删）
    } catch (e) {
      btn.disabled = false;
      btn.title = t("staging.delFail", e.message);
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

// 暂存区行内动作按钮图标化：填 svg 图标，`desc` 一句说明走原生 title（hover 悬停显示，
// 浏览器自动定位、不被 .overlay-body 滚动容器裁切）；`label` 短名走 aria-label（无障碍可读名）。
// `blocked` 时把「写作业进行中」并进 title，仍保留说明（满足 hover 显说明的诉求）。
function setStageIcon(btn, iconId, label, desc, blocked) {
  btn.classList.add("stage-iconbtn");
  btn.innerHTML = `<svg class="ico" aria-hidden="true"><use href="#${iconId}"/></svg>`;
  btn.title = blocked ? `${desc}（${t("staging.writeBusy")}）` : desc;
  btn.setAttribute("aria-label", label);
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
  parse.className = "stage-act";  // P4.6.1：宿主确定性解析（非「问 agent」），点击即建解析作业
  setStageIcon(parse, "i-play", t("staging.parse"), t("tip.parse"), blocked);
  parse.disabled = blocked;
  parse.addEventListener("click", () => triggerParse(it.path));
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
  cb.title = t("tip.pickMerge");
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
  setStageIcon(preview, "i-eye", t("staging.preview"), t("tip.preview"));
  preview.addEventListener("click", () => previewWorkspaceFile(it.path));
  const revise = document.createElement("button");
  revise.className = "stage-act";
  setStageIcon(revise, "i-pencil", t("staging.revise"), t("tip.revise"));
  revise.addEventListener("click", () => {
    const what = window.prompt(t("staging.revisePrompt", it.name), "");
    if (what === null || !what.trim()) return;
    fillComposer(t("staging.reviseCmd", it.path, what.trim()), { needWritable: true });
  });
  // 断链检查（P4.6.1，决策P4.6.1-5）：拆分/合并后图片可能悬空/错位，一键检查 + 重整（确定性、宿主写）。
  const relink = document.createElement("button");
  relink.className = "stage-act";
  setStageIcon(relink, "i-link", t("staging.relink"), t("tip.relink"));
  relink.addEventListener("click", () => checkRelink(it, relink));
  row.append(cb, ico, nm, preview, revise, relink, promoteButton(it, blocked), trashButton(it.path, row, blocked));
  return row;
}

// 断链检查（决策P4.6.1-5）：GET image-lint → 行内显示悬空/错位计数；可重整时给「重整图片」按钮。
// 再点收起（与晋级表单同 toggle 行为）。
async function checkRelink(it, anchorBtn) {
  const row = anchorBtn.parentElement;
  const existing = row.querySelector(".stage-relink-status");
  if (existing) { existing.remove(); return; } // 再点收起
  const status = document.createElement("div");
  status.className = "stage-relink-status stage-promote-status";
  status.textContent = t("staging.relinkChecking");
  row.appendChild(status);
  let data;
  try {
    data = await getJSON(`/api/workspace/image-lint?file=${encodeURIComponent(it.path)}`);
  } catch (e) {
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.relinkFail", e.message))}</span>`;
    return;
  }
  status.innerHTML = "";
  const dangling = (data.dangling || []).length;
  const misplaced = (data.misplaced || []).length;
  if (!dangling && !misplaced) {
    status.innerHTML = `<span class="report-ok">${escapeHtml(t("staging.relinkClean"))}</span>`;
    return;
  }
  if (misplaced) {
    const m = document.createElement("span");
    m.className = "muted";
    m.textContent = t("staging.relinkMisplaced", misplaced) + " ";
    status.appendChild(m);
  }
  if (dangling) {
    const d = document.createElement("span");
    d.className = "report-bad";
    d.textContent = t("staging.relinkDangling", dangling) + " ";
    status.appendChild(d);
  }
  if (data.needs_relocalize) { // 仅错位可自动修；悬空（文件缺失）重整无能为力，只提示
    const go = document.createElement("button");
    go.className = "stage-act";
    go.textContent = t("staging.relinkGo");
    go.disabled = hostWriteBlocked();
    if (go.disabled) go.title = t("staging.writeBusy");
    go.addEventListener("click", () => runRelocalize(it, go, status));
    status.appendChild(go);
  }
}

async function runRelocalize(it, go, status) {
  go.disabled = true;
  go.textContent = t("staging.relinking");
  try {
    const res = await fetch("/api/workspace/relocalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file: it.path }),
    });
    if (res.status === 423) {
      go.disabled = false; go.textContent = t("staging.relinkGo");
      status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.writableRetryDot"))}</span>`;
      return;
    }
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    status.innerHTML = `<span class="report-ok">${escapeHtml(t("staging.relinkDone"))}</span>`;
    setTimeout(loadStaging, 600); // 重整改了 md/图目录 → 刷新暂存区
  } catch (e) {
    go.disabled = false; go.textContent = t("staging.relinkGo");
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.relinkFail", e.message))}</span>`;
  }
}

// 「晋级为源」按钮：点开行内晋级表单（名/出处 + 确认）；复用投喂 slug-409 交互。
function promoteButton(it, blocked) {
  const btn = document.createElement("button");
  btn.className = "stage-act stage-promote";
  setStageIcon(btn, "i-arrow-right", t("staging.promote"), t("tip.promote"), blocked);
  btn.disabled = blocked;
  btn.addEventListener("click", () => openPromoteForm(it, btn));
  return btn;
}

// 🗑 删 scratch：DELETE /api/workspace/file（单写者 + 层③ 423）。可写 turn 跑动期置灰。
function trashButton(path, row, blocked) {
  const btn = document.createElement("button");
  btn.className = "stage-trash";
  btn.textContent = "🗑";
  btn.title = blocked ? t("staging.writeBusy") : t("tip.delFile");
  btn.disabled = blocked;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
      if (res.status === 423) { btn.disabled = false; btn.title = t("staging.writableRetry"); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      row.remove();
    } catch (e) {
      btn.disabled = false;
      btn.title = t("staging.delFail", e.message);
    }
  });
  return btn;
}

async function previewWorkspaceFile(path) {
  stagingOpen = false; // 看预览期间暂停自动刷新：否则并发 turn 的 done/stopped 会把预览刷回列表
  overlayRepaint = null; // 预览态不挂重画闭包：避免语言切换把预览刷回暂存区列表（返回后由 renderStaging 重置）
  const box = $("#overlay-body");
  box.innerHTML = `<p class="muted">${escapeHtml(t("staging.loadingPreview"))}</p>`;
  // 「返回暂存区」恢复自动刷新并重拉当前目录——成败两路都挂上，避免预览失败时无路可回、
  // 且 stagingOpen 永久停在 false 令本会话后续 turn 收尾不再刷新（评审）。
  const back = document.createElement("button");
  back.className = "stage-act";
  back.textContent = t("staging.backToStaging");
  back.addEventListener("click", () => { stagingOpen = true; loadStaging(); });
  let data;
  try {
    data = await getJSON(`/api/workspace/file?path=${encodeURIComponent(path)}`);
  } catch (e) {
    box.innerHTML = "";
    const err = document.createElement("p");
    err.className = "report-bad";
    err.textContent = t("staging.previewFail", e.message);
    box.append(back, err);
    return;
  }
  box.innerHTML = "";
  const title = document.createElement("div");
  title.className = "stage-head";
  title.textContent = path; // textContent：路径按字面显示
  // Markdown ⇄ 源码 切换条：默认 Markdown 富渲染；源码视图按 textContent 转义显示原始 md（无注入）。
  const toggle = document.createElement("div");
  toggle.className = "stage-preview-toggle";
  const mdBtn = document.createElement("button");
  mdBtn.className = "stage-act";
  mdBtn.textContent = t("staging.viewMd");
  const srcBtn = document.createElement("button");
  srcBtn.className = "stage-act";
  srcBtn.textContent = t("staging.viewSrc");
  toggle.append(mdBtn, srcBtn);
  const view = document.createElement("div");
  let mode = "markdown";
  const paint = () => {
    mdBtn.classList.toggle("active", mode === "markdown");
    srcBtn.classList.toggle("active", mode === "source");
    if (mode === "markdown") {
      view.className = "stage-preview rendered";
      view.innerHTML = data.html; // render_page 已 sanitize（同 /api/page）
    } else {
      view.className = "stage-preview source";
      view.innerHTML = "";
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = data.source || ""; // textContent：原始 md 逐字、转义、无注入
      pre.appendChild(code);
      view.appendChild(pre);
    }
  };
  mdBtn.addEventListener("click", () => { mode = "markdown"; paint(); });
  srcBtn.addEventListener("click", () => { mode = "source"; paint(); });
  box.append(back, title, toggle, view);
  paint();
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
  nameIn.placeholder = t("staging.promoteNamePh");
  const originIn = document.createElement("input");
  originIn.type = "text";
  originIn.placeholder = t("staging.promoteOriginPh");
  const go = document.createElement("button");
  go.className = "stage-act";
  go.textContent = t("staging.promoteConfirm");
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
  if (!name) { status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.promoteNameEmpty"))}</span>`; return; }
  go.disabled = true;
  go.textContent = t("staging.promoting");
  status.textContent = "";
  const reset = () => { go.disabled = false; go.textContent = t("staging.promoteConfirm"); };
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
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.promoteFail", e.message))}</span>`;
    return;
  }
  if (res.status === 423) {
    reset();
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.writableRetryDot"))}</span>`;
    return;
  }
  if (res.status === 409) {
    // 默认引导改名（开新源）；覆盖须显式确认 + 失真告警（决策P4.6-10）。
    reset();
    status.innerHTML = `<span class="report-bad">${escapeHtml(data.detail || t("staging.promoteConflict"))}</span>${escapeHtml(t("staging.promoteRenameOr"))}`;
    const ow = document.createElement("button");
    ow.className = "stage-act";
    ow.textContent = t("staging.overwriteRisk");
    ow.title = t("tip.overwrite");
    ow.addEventListener("click", () => {
      if (window.confirm(t("staging.overwriteConfirm")))
        submitPromote(source, name, origin, true, go, status);
    });
    status.appendChild(ow);
    return;
  }
  if (!res.ok) {
    reset();
    status.innerHTML = `<span class="report-bad">${escapeHtml(t("staging.promoteFail", data.detail || `HTTP ${res.status}`))}</span>`;
    return;
  }
  // 成功：原地变「✓ 已晋级 + 立即 ingest」。
  status.innerHTML = `<span class="report-ok">${escapeHtml(t("staging.promoted", data.saved))}</span> `;
  go.remove();
  const ingestBtn = document.createElement("button");
  ingestBtn.className = "stage-act";
  ingestBtn.textContent = t("staging.ingestNow");
  ingestBtn.addEventListener("click", () => triggerIngest(data.saved));
  status.appendChild(ingestBtn);
}
