"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 投喂 + 暂存区（workspace 浏览 + 一键入库，P4.30）。
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

// ── 暂存区（P4.6 → P4.30）：上传文件 → 一键入库（解析·整理·拆分 → 确认 → 落 raw/ → 依次入库）──────
//
// 顶栏「暂存区」→ 浮层：顶部是一键入库任务卡（等待确认 / 入库中 / 已暂停，可开始、暂停、继续、放弃，
// 进展日志实时刷新）；下面 ① 上传文件 uploads/（[一键入库] / 🗑）+ ② 已解析 parsed/（只读[预览] / 🗑）。
// P4.6.1 的「开始解析 / 晋级为源 / 断链检查 / 重整 / 让 agent 修订 / 合并」随 P4.30 下线（决策P4.30-2）。
// 一键入库/删除是宿主写：可写 turn 跑动期置灰，对应后端层③ 423（§7.3）。


function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// 精确到秒的时长（进度用）：<1 分钟显示秒，<1 小时显示分秒，再长显示时分。
function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  if (s < 60) return t("oc.durSec", s);
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? t("oc.durMinSec", m, s % 60) : t("oc.minutes", m);
  return t("oc.hours", Math.floor(m / 60), m % 60);
}

function fmtChars(n) {
  n = n || 0;
  if (currentLang() !== "zh") return t("oc.chars", n.toLocaleString("en"));
  return n >= 10000 ? t("oc.charsWan", (n / 10000).toFixed(1)) : t("oc.chars", n);
}

function fmtMinutes(seconds) {
  const m = Math.max(1, Math.round((seconds || 0) / 60));
  return m < 60 ? t("oc.minutes", m) : t("oc.hours", Math.floor(m / 60), m % 60);
}

// 宿主写动作在可写 turn 跑动期是否应置灰（一键入库/删除）——对应后端层③ 423（§7.3）。
function hostWriteBlocked() {
  return chatStreaming && currentMode === "workspace-write";
}

$("#staging-btn").addEventListener("click", openStaging);

async function openStaging() {
  stagingPath = null; // 每次从顶栏打开都回到根视图
  uploadNotice = null; // 上次上传的结果提示不跨次打开保留
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

// 会话 turn 收尾（done/stopped）后，若暂存区仍开着则自动重拉刷新当前目录（决策P4.6-9）。
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
    // 根视图：一键入库任务卡 + 两段（uploads / parsed）。每段列其**直接子项**（子目录可点入、文件带操作）。
    const panel = document.createElement("div");
    panel.id = "oneclick-panel";
    box.appendChild(panel);
    loadOneclickPanel();
    renderSection(box, t("staging.uploadsHead"), "uploads",
      data.uploads || [], blocked, t("staging.uploadsEmpty"), undefined, uploadBar(blocked));
    renderSection(box, t("staging.parsedHead"), "parsed",
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
    renderSection(box, "", data.base, data.items || [], blocked, t("staging.dirEmpty"));
  }

  const flow = document.createElement("p");
  flow.className = "stage-flow muted";
  flow.textContent = t("staging.flow");
  box.appendChild(flow);
}

// 渲染一段目录列表：子目录行（可点入 + 整删）在前，文件行（按 base 决定 uploads/parsed 操作）在后。
function renderSection(box, headerText, base, items, blocked, emptyText, sub, extra) {
  if (headerText) box.appendChild(stagingHeader(headerText, sub));
  if (extra) box.appendChild(extra);
  if (!items.length) { box.appendChild(stagingEmpty(t("staging.emptyWrap", emptyText))); return; }
  for (const it of items) {
    if (it.is_dir) box.appendChild(renderFolderRow(it, blocked));
    else if (base === "uploads") box.appendChild(renderUploadRow(it, blocked));
    else box.appendChild(renderParsedRow(it, blocked));
  }
}

// 「上传文件…」：选原件（可多选）→ 逐个 POST /api/upload 落 workspace/uploads/ → 重拉列表。
// 与对话框附件共用同一端点（单写者、层③ 423、库被 CLI 占用 409）；这里只上传、不当附件随消息发。
// 刻意串行（同 attach.js）：避免并发持多份大文件 body。结果提示存 uploadNotice，重拉后仍显示。
let uploadNotice = null; // { ok, errors: [[文件名, 原因]] }：最近一次上传的结果（渲染时再翻译，切语言跟着变）
let uploadBusy = false;

function uploadBar(blocked) {
  const bar = document.createElement("div");
  bar.className = "stage-upload";
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  input.hidden = true;
  const btn = document.createElement("button");
  btn.className = "stage-act stage-upload-btn";
  btn.textContent = t("staging.uploadBtn");
  btn.title = blocked ? `${t("tip.uploadFiles")}（${t("staging.writeBusy")}）` : t("tip.uploadFiles");
  btn.disabled = blocked || uploadBusy;
  const note = document.createElement("span");
  note.className = "stage-upload-note muted";
  if (uploadNotice) {
    note.textContent = noticeText(uploadNotice); // textContent：文件名与错误按字面显示
    if (uploadNotice.errors.length) note.classList.add("report-bad");
  }
  btn.addEventListener("click", () => { input.value = ""; input.click(); });
  input.addEventListener("change", () => uploadToStaging(Array.from(input.files || []), btn, note));
  bar.append(btn, input, note);
  return bar;
}

async function uploadToStaging(files, btn, note) {
  if (!files.length || uploadBusy) return;
  uploadBusy = true;
  btn.disabled = true;
  note.classList.remove("report-bad");
  const errors = []; // [[文件名, 原因]]
  let ok = 0;
  for (const [i, file] of files.entries()) {
    note.textContent = t("staging.uploading", i + 1, files.length, file.name);
    try {
      const fd = new FormData();
      fd.append("file", file, file.name);
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (res.status === 423) throw new Error(t("staging.writableRetry"));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      ok += 1;
    } catch (e) {
      errors.push([file.name, e.message]);
    }
  }
  uploadBusy = false;
  uploadNotice = { ok, errors };
  if (stagingOpen && !$("#overlay").classList.contains("hidden")) {
    loadStaging(); // 重拉：新文件出现在「上传文件」里（提示随 uploadNotice 保留）
  } else {
    btn.disabled = false;
    note.textContent = noticeText(uploadNotice);
  }
}

function noticeText(n) {
  const parts = n.ok || !n.errors.length ? [t("staging.uploadDone", n.ok)] : [];
  for (const [name, msg] of n.errors) parts.push(t("staging.uploadFail", name, msg));
  return parts.join(" ");
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

function fileRowHead(it) {
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
  return [ico, nm];
}

// uploads/ 行：徽章 + 文件名 + 大小 + [一键入库图标] + 🗑。
function renderUploadRow(it, blocked) {
  const row = document.createElement("div");
  row.className = "stage-row";
  const sz = document.createElement("span");
  sz.className = "stage-size muted";
  sz.textContent = fmtBytes(it.bytes);
  const go = document.createElement("button");
  go.className = "stage-act stage-oneclick";
  setStageIcon(go, "i-oneclick", t("staging.oneclick"), t("tip.oneclick"), blocked);
  go.disabled = blocked;
  go.addEventListener("click", () => triggerOneclick(it.path));
  row.append(...fileRowHead(it), sz, go, trashButton(it.path, row, blocked));
  return row;
}

// parsed/ 行：文件名 + 只读[预览] + 🗑（决策P4.30-2：parsed/ 只保留删除与只读预览）。
function renderParsedRow(it, blocked) {
  const row = document.createElement("div");
  row.className = "stage-row";
  const preview = document.createElement("button");
  preview.className = "stage-act";
  setStageIcon(preview, "i-eye", t("staging.preview"), t("tip.preview"));
  preview.addEventListener("click", () => previewWorkspaceFile(it.path));
  row.append(...fileRowHead(it), preview, trashButton(it.path, row, blocked));
  return row;
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
      enhanceContent(view); // 挂在 paint 内 → 切回 markdown 模式也重增强 ```mermaid→图 / ```X→高亮 / $…$→公式（决策P4.14-9，泛化 P4.13-8）
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

// ── 一键入库（P4.30）：准备作业 → 任务卡（确认 / 进度 / 暂停 / 继续 / 放弃）────────────────
//
// 准备（解析·整理·拆分）是一个写作业：POST /api/oneclick → 进度窗（pollJob 实时显示转换日志）→ 完成后
// 回暂存区，任务卡停在「等待确认」。确认后的执行是长作业（每篇数分钟），任务卡每 2 秒拉一次
// GET /api/oneclick 刷新进度与日志尾；只重画任务卡本身，不重画整个暂存区（不打断滚动/预览）。

let oneclickTimer = null;

// t() 首参须是字符串字面量（i18n 静态守恒，决策P4.7-8）：状态 → 文案用查表，不拼 key。
function ocStateLabel(state) {
  return ({
    preparing: t("oc.statePreparing"),
    awaiting_confirm: t("oc.stateAwaiting"),
    running: t("oc.stateRunning"),
    paused: t("oc.statePaused"),
    done: t("oc.stateDone"),
    failed: t("oc.stateFailed"),
    cancelled: t("oc.stateCancelled"),
  })[state] || state;
}

function ocItemLabel(state) {
  return ({
    pending: t("oc.itemPending"),
    running: t("oc.itemRunning"),
    done: t("oc.itemDone"),
    failed: t("oc.itemFailed"),
  })[state || "pending"] || state;
}

async function triggerOneclick(uploadPath) {
  showOverlay("overlay.oneclick", `<p class="muted">${escapeHtml(t("staging.ocSubmitted", uploadPath))}</p>`);
  try {
    const res = await fetch("/api/oneclick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ upload: uploadPath }),
    });
    if (res.status === 423) {
      $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("staging.writableRetryDot"))}</p>`;
      return;
    }
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    const { job_id } = await res.json();
    await pollJob(job_id, t("staging.ocPreparing"), renderOneclickPrepared, "oc-joblog");
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("staging.ocFail", e.message))}</p>`;
  }
}

function renderOneclickPrepared(job) {
  if (job.exit_code === 0) { openStaging(); return; } // 回暂存区（标题复位），任务卡停在「等待确认」
  const box = $("#overlay-body");
  box.innerHTML = `<p><span class="report-bad">${escapeHtml(t("common.exitCode", job.exit_code))}</span></p>`;
  const pre = document.createElement("pre");
  pre.className = "oc-joblog";
  pre.textContent = collapseCR(job.output || t("common.noOutput"));
  const back = document.createElement("button");
  back.className = "stage-act";
  back.textContent = t("staging.backToStaging");
  back.addEventListener("click", openStaging);
  box.append(pre, back);
}

async function loadOneclickPanel() {
  clearTimeout(oneclickTimer);
  const panel = document.getElementById("oneclick-panel");
  if (!panel || !stagingOpen) return;
  let data;
  try {
    data = await getJSON("/api/oneclick");
  } catch (e) {
    panel.innerHTML = "";
    return;
  }
  if (document.getElementById("oneclick-panel") !== panel) return; // 期间暂存区已重画
  renderOneclickPanel(panel, data);
  const task = data.active;
  if (task && (task.job_id || task.state === "running" || task.state === "preparing")) {
    oneclickTimer = setTimeout(loadOneclickPanel, 2000);
  }
}

function ocButton(label, onClick, { primary = true, disabled = false } = {}) {
  const b = document.createElement("button");
  b.className = primary ? "stage-act" : "stage-act stage-act-quiet";
  b.textContent = label;
  b.disabled = disabled;
  b.addEventListener("click", onClick);
  return b;
}

async function ocPost(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const d = await res.json().catch(() => ({}));
  if (res.status === 423) throw new Error(t("staging.writableRetryDot"));
  if (!res.ok) throw new Error(d.detail || `HTTP ${res.status}`);
  return d;
}

function renderOneclickPanel(panel, data) {
  // 每 2 秒重画：沿用用户上次对「详情」的展开/收起（首次渲染才按状态给默认值）。
  const prevDet = panel.querySelector("details");
  const prevOpen = prevDet ? prevDet.open : null;
  panel.innerHTML = "";
  const task = data.active;
  if (!task) {
    if (data.holder) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = t("oc.busy", data.holder);
      panel.appendChild(p);
    }
    return;
  }
  const blocked = hostWriteBlocked();
  const card = document.createElement("div");
  card.className = "oc-card";
  const head = document.createElement("div");
  head.className = "stage-head";
  head.textContent = `${t("oc.title")} · ${ocStateLabel(task.state)}`;
  const name = (task.upload || "").split("/").pop();
  const summary = document.createElement("p");
  summary.textContent = t("oc.summary", name, task.items.length, fmtMinutes(task.estimate_seconds));
  card.append(head, summary);

  const status = document.createElement("div");
  status.className = "stage-promote-status";
  const fail = (e) => { status.innerHTML = `<span class="report-bad">${escapeHtml(e.message)}</span>`; };
  const actions = document.createElement("div");
  actions.className = "oc-actions";
  const pc = task.precheck || {};

  if (task.state === "awaiting_confirm" && !task.job_id) {
    let overwrite = null;
    if (pc.status === "conflict") {
      const c = document.createElement("div");
      c.className = "report-bad";
      c.textContent = t("oc.conflict");
      const ul = document.createElement("ul");
      for (const d of pc.details || []) { const li = document.createElement("li"); li.textContent = d; ul.appendChild(li); }
      card.append(c, ul);
    } else {
      if (pc.status === "overwritable") {
        const label = document.createElement("label");
        label.className = "oc-overwrite";
        overwrite = document.createElement("input");
        overwrite.type = "checkbox";
        label.append(overwrite, document.createTextNode(" " + t("oc.overwrite")));
        card.appendChild(label);
      }
      actions.appendChild(ocButton(t("oc.start"), async () => {
        if (overwrite && !overwrite.checked) { status.textContent = t("oc.overwriteNeeded"); return; }
        try {
          await ocPost(`/api/oneclick/${encodeURIComponent(task.task_id)}/confirm`, { overwrite: !!(overwrite && overwrite.checked) });
          loadOneclickPanel();
        } catch (e) { fail(e); }
      }, { disabled: blocked }));
    }
    actions.appendChild(ocCancelButton(task, fail, blocked));
  } else if (task.state === "running" || task.job_id) {
    card.appendChild(ocProgressBlock(task));
    if (task.pausing) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = t("oc.pausing");
      card.appendChild(p);
    } else if (task.job_id) {
      actions.appendChild(ocButton(t("oc.pause"), async () => {
        try { await ocPost(`/api/oneclick/${encodeURIComponent(task.task_id)}/pause`); loadOneclickPanel(); } catch (e) { fail(e); }
      }));
    } else if (!data.holder) {
      // 「入库中」却没有本进程作业、也没有别的写者在持锁：执行者已退出（作业异常等）。后端 resume
      // 会先把它恢复成已暂停再继续，给出口，不让任务卡在这里只能等重启。
      actions.appendChild(ocButton(t("oc.resume"), async () => {
        try { await ocPost(`/api/oneclick/${encodeURIComponent(task.task_id)}/resume`); loadOneclickPanel(); } catch (e) { fail(e); }
      }, { disabled: blocked }));
      actions.appendChild(ocCancelButton(task, fail, blocked)); // 后端放弃同样先恢复：不必先继续入库才能放弃
    }
  } else if (task.state === "paused") {
    card.appendChild(ocProgressBlock(task));
    // 暂停原因是「复检发现可覆盖的旧文件」：继续时可补给覆盖确认（「冲突」不给这个选项）
    let pOverwrite = null;
    if (task.precheck && task.precheck.status === "overwritable" && !task.overwrite) {
      const label = document.createElement("label");
      label.className = "oc-overwrite";
      pOverwrite = document.createElement("input");
      pOverwrite.type = "checkbox";
      label.append(pOverwrite, document.createTextNode(" " + t("oc.overwrite")));
      card.appendChild(label);
    }
    actions.appendChild(ocButton(t("oc.resume"), async () => {
      if (pOverwrite && !pOverwrite.checked) { status.textContent = t("oc.overwriteNeeded"); return; }
      try {
        await ocPost(`/api/oneclick/${encodeURIComponent(task.task_id)}/resume`, { overwrite: !!(pOverwrite && pOverwrite.checked) });
        loadOneclickPanel();
      } catch (e) { fail(e); }
    }, { disabled: blocked }));
    actions.appendChild(ocCancelButton(task, fail, blocked));
  }
  if (task.reason) {
    const r = document.createElement("p");
    r.className = "muted";
    r.textContent = t("oc.reason", task.reason);
    card.appendChild(r);
  }
  if (task.last_error && !task.job_id) {
    // 上一次确认/继续作业失败（库被占用、复检被拒等）：任务退回原状态，原因只在作业输出里，在此透出。
    const e = document.createElement("p");
    e.className = "report-bad";
    e.style.whiteSpace = "pre-wrap";
    e.textContent = task.last_error;
    card.appendChild(e);
  }
  card.append(actions, status);

  // 详情（可展开，只供查看）：整理摘要、图片警告、逐份文件、进展日志尾。
  const det = document.createElement("details");
  const sum = document.createElement("summary");
  sum.textContent = t("oc.details");
  det.appendChild(sum);
  const n = task.normalized || {};
  const norm = document.createElement("p");
  norm.className = "muted";
  norm.textContent = t("oc.normalized", n.headers_removed || 0, n.page_numbers_removed || 0, n.watermarks_removed || 0, n.sentences_joined || 0);
  det.appendChild(norm);
  if ((task.warnings || []).length) {
    const w = document.createElement("p");
    w.className = "muted";
    w.textContent = t("oc.imageWarn", task.warnings.length);
    det.appendChild(w);
  }
  const list = document.createElement("ol");
  list.className = "oc-items";
  for (const it of task.items) {
    const li = document.createElement("li");
    const pages = it.pages ? ` · p.${it.pages[0]}–${it.pages[1]}` : "";
    li.textContent = `${it.raw.split("/").pop()} · ${it.chars}${pages} · ${ocItemLabel(it.state)}`;
    if (it.state === "running") li.className = "oc-item-current";
    li.title = (it.path || []).join(" / ");
    list.appendChild(li);
  }
  det.appendChild(list);
  if ((task.log_tail || []).length) {
    const pre = document.createElement("pre");
    pre.className = "oc-log";
    pre.textContent = task.log_tail.slice(-40).join("\n");
    det.appendChild(pre);
  }
  det.open = prevOpen !== null ? prevOpen : !!(task.job_id || task.state === "running"); // 入库中默认展开日志
  card.appendChild(det);
  panel.appendChild(card);
}

// 入库进度：进度条 + 共几篇 / 完成几篇 + 正在入哪一篇（已用多久）+ 累计耗时与预计剩余（后端 progress_info 算）。
function ocProgressBlock(task) {
  const pi = task.progress || { total: task.items.length, done: task.done };
  const box = document.createElement("div");
  box.className = "oc-progress";
  const bar = document.createElement("progress");
  bar.max = Math.max(1, pi.total);
  bar.value = pi.done;
  const count = document.createElement("span");
  count.textContent = t("oc.progress", pi.done, pi.total);
  const row = document.createElement("div");
  row.className = "oc-progress-row";
  row.append(bar, count);
  box.appendChild(row);
  if (pi.current) {
    const cur = document.createElement("p");
    cur.className = "oc-current";
    const name = (pi.current.raw || "").split("/").pop();
    cur.textContent = t("oc.current", pi.current.index, pi.total, name, fmtChars(pi.current.chars), fmtDuration(pi.current.elapsed_seconds));
    cur.title = pi.current.raw || "";
    box.appendChild(cur);
  }
  if (pi.remaining) {
    const eta = document.createElement("p");
    eta.className = "muted";
    const basis = pi.eta_basis === "history" ? t("oc.basisHistory") : pi.eta_basis === "default" ? t("oc.basisDefault") : "";
    eta.textContent = t("oc.timing", fmtDuration(pi.elapsed_seconds), fmtDuration(pi.eta_seconds), pi.remaining) + basis;
    box.appendChild(eta);
  }
  return box;
}

function ocCancelButton(task, fail, blocked) {
  return ocButton(t("oc.cancel"), async () => {
    if (!window.confirm(t("oc.cancelConfirm"))) return;
    try { await ocPost(`/api/oneclick/${encodeURIComponent(task.task_id)}/cancel`); loadStaging(); } catch (e) { fail(e); }
  }, { primary: false, disabled: blocked });
}
