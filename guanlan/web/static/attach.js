"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 输入框自增高 + 文件上传/附件。
// ── 输入框自增高 + 文件上传/附件（OpenAI 风格 composer，P4.6 Web 文件上传）──────────────
//
// 「附件」语义（agentao 附件约定，镜像 chahua）：上传文件先经 POST /api/upload 落
// workspace/uploads/，发送时服务端把每个附件以 <attachment uri="…" [mimetype="…"]/> 标签追加进
// 发给 agent 的消息；**图像**附件额外走 arun(images=) 视觉通道（base64），模型不支持视觉时由
// agentao 自动以同格式标签降级重试（宿主/前端不做能力探测）。徽章在 composer 内可移除（图像
// 显示本地缩略图）；发送后清空、用户气泡下回显。

// 文本域随内容自增高（rows=1 起，至 200px 封顶后内部滚动）。
function autoGrowInput() {
  const ta = $("#chat-input");
  ta.style.height = "auto";
  const max = 200;
  ta.style.height = Math.min(ta.scrollHeight, max) + "px";
  ta.style.overflowY = ta.scrollHeight > max ? "auto" : "hidden";
}
$("#chat-input").addEventListener("input", autoGrowInput);
autoGrowInput(); // 初始同步一次高度

// 待发送附件（仅上传成功者在册）：{rel, name, kind, thumb, visionOversize}。
// rel = workspace/uploads/<名>；thumb = 图像的本地 blob URL（缩略图，镜像 chahua）。
const pendingAttachments = [];

// 图像扩展名白名单（与服务端 _IMAGE_EXT_TO_MIME / agentao 视觉通道同口径）。
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp"]);
// 视觉单图上限（同 agentao.media_limits.MAX_IMAGE_BYTES）：超出仍可上传（≤50MiB），但不走
// 视觉通道、agent 只看 <attachment> 文本引用——徽章上预先提示（镜像 chahua visionOversize）。
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;

function isImageName(name) {
  const i = name.lastIndexOf(".");
  return i > 0 && IMAGE_EXTS.has(name.slice(i + 1).toLowerCase());
}

// ── 文件类型图标（统一：图像→缩略图，其它→彩色文件类型角标，三处共用：暂存区 / 上传徽章 / 气泡）──
//
// 扩展名 → 类别（决定角标配色），角标文字取扩展名大写（≤4 字）。常见办公/压缩/代码/媒体各成一色。
const FILE_CAT = {
  pdf: "pdf",
  doc: "doc", docx: "doc", rtf: "doc", odt: "doc", pages: "doc",
  xls: "xls", xlsx: "xls", csv: "xls", tsv: "xls", ods: "xls",
  ppt: "ppt", pptx: "ppt", key: "ppt", odp: "ppt",
  zip: "zip", tar: "zip", gz: "zip", tgz: "zip", "7z": "zip", rar: "zip", bz2: "zip", xz: "zip",
  md: "md", markdown: "md",
  txt: "txt", text: "txt", log: "txt", rst: "txt", org: "txt",
  json: "code", yaml: "code", yml: "code", toml: "code", xml: "code", html: "code", htm: "code",
  css: "code", scss: "code", js: "code", mjs: "code", ts: "code", tsx: "code", jsx: "code",
  py: "code", sh: "code", bash: "code", c: "code", h: "code", cpp: "code", go: "code",
  rs: "code", java: "code", rb: "code", php: "code", lua: "code", sql: "code",
  png: "img", jpg: "img", jpeg: "img", gif: "img", webp: "img", svg: "img", bmp: "img",
  tiff: "img", tif: "img", ico: "img", heic: "img",
  mp3: "audio", wav: "audio", flac: "audio", m4a: "audio", ogg: "audio", aac: "audio",
  mp4: "video", mov: "video", mkv: "video", webm: "video", avi: "video", m4v: "video",
};

function fileKindInfo(name) {
  const base = name.slice(name.lastIndexOf("/") + 1); // 取 basename：name 可能含子目录（暂存区嵌套）
  const dot = base.lastIndexOf(".");
  const ext = dot > 0 ? base.slice(dot + 1).toLowerCase() : "";
  return { ext, cat: FILE_CAT[ext] || "file", label: ext ? ext.toUpperCase().slice(0, 4) : "FILE" };
}

// 文件类型角标：页形 + 扩展名文字（textContent 安全），按类别上色（见 app.css .file-ico[data-cat]）。
function fileTypeBadge(name) {
  const { cat, label } = fileKindInfo(name);
  const span = document.createElement("span");
  span.className = "file-ico";
  span.dataset.cat = cat;
  span.textContent = label;
  return span;
}

// 统一文件图标：有缩略图 URL 且是图像 → <img> 缩略图（解码失败回退角标）；否则文件类型角标。
function makeFileIcon(name, { thumbUrl = null, isImage = false } = {}) {
  if (isImage && thumbUrl) {
    const img = document.createElement("img");
    img.className = "file-thumb";
    img.src = thumbUrl;
    img.alt = "";
    img.addEventListener("error", () => img.replaceWith(fileTypeBadge(name)), { once: true });
    return img;
  }
  return fileTypeBadge(name);
}

function showAttachList() {
  const ul = $("#attach-list");
  ul.hidden = ul.children.length === 0;
}

// revoke=false 供「发送」路径：附件快照已交给气泡回显，缩略图 blob URL 须继续存活。
function clearAttachments({ revoke = true } = {}) {
  if (revoke) {
    for (const a of pendingAttachments) if (a.thumb) URL.revokeObjectURL(a.thumb);
  }
  pendingAttachments.length = 0;
  const ul = $("#attach-list");
  if (ul) { ul.innerHTML = ""; ul.hidden = true; }
}

// 建一枚徽章 DOM（占位「上传中」态；图像直接显示本地缩略图）。返回各节点引用，供上传成败后改写。
function addAttachChip(name, thumb) {
  const li = document.createElement("li");
  li.className = "attach-chip";
  const icon = document.createElement("span");
  icon.className = "attach-icon";
  if (thumb) {
    const img = document.createElement("img");
    img.className = "attach-thumb";
    img.src = thumb;
    img.alt = "";
    // 解码失败（坏图/不支持的编码）→ 降级为文件类型角标。
    img.addEventListener("error", () => { img.remove(); icon.appendChild(fileTypeBadge(name)); }, { once: true });
    icon.appendChild(img);
  } else {
    icon.textContent = "…";
  }
  const label = document.createElement("span");
  label.className = "attach-name";
  label.textContent = name; // textContent：文件名按字面显示，无注入
  label.title = name;
  const meta = document.createElement("span");
  meta.className = "attach-meta";
  meta.textContent = t("attach.uploading");
  li.append(icon, label, meta);
  $("#attach-list").appendChild(li);
  showAttachList();
  return { li, icon, label, meta, thumb };
}

// 上传成功：占位徽章定型 + 入册 pendingAttachments + 挂「×移除」。
function finalizeAttachChip(chip, att) {
  if (!chip.thumb) { chip.icon.textContent = ""; chip.icon.appendChild(fileTypeBadge(att.name)); }
  chip.meta.textContent =
    att.kind === "image"
      ? (att.visionOversize ? t("attach.visionOversize") : t("attach.image"))
      : att.kind === "binary" ? t("attach.binary") : t("attach.text");
  if (att.visionOversize) chip.meta.title = t("tip.visionOversize");
  chip.li.dataset.rel = att.rel;
  pendingAttachments.push(att);
  const rm = document.createElement("button");
  rm.type = "button";
  rm.className = "attach-remove";
  rm.textContent = "×";
  rm.title = t("tip.attachRemove");
  rm.addEventListener("click", () => {
    const i = pendingAttachments.findIndex((a) => a.rel === att.rel);
    if (i >= 0) pendingAttachments.splice(i, 1);
    if (att.thumb) URL.revokeObjectURL(att.thumb);
    chip.li.remove();
    showAttachList();
  });
  chip.li.appendChild(rm);
}

// 上传失败：徽章转错误态 + 挂「×」手动清（×时回收缩略图 blob URL）。
function errorAttachChip(chip, message) {
  if (!chip.thumb) chip.icon.textContent = "⚠";
  chip.li.classList.add("attach-error");
  chip.meta.textContent = "";
  chip.label.title = message;
  const rm = document.createElement("button");
  rm.type = "button";
  rm.className = "attach-remove";
  rm.textContent = "×";
  rm.title = message;
  rm.addEventListener("click", () => {
    if (chip.thumb) URL.revokeObjectURL(chip.thumb);
    chip.li.remove();
    showAttachList();
  });
  chip.li.appendChild(rm);
}

// 串行上传一批文件（拖拽 / 选择 / 粘贴共用）。每个文件 POST /api/upload。
// 刻意串行（镜像 chahua）：避免并发持多份大文件 body 撑内存。
async function uploadFiles(files) {
  files = Array.from(files || []);
  for (const file of files) await uploadOne(file);
}

async function uploadOne(file) {
  // 图像：上传前先出本地缩略图（blob URL，零网络往返）+ 预判视觉超限（镜像 chahua）。
  const isImage = isImageName(file.name);
  const thumb = isImage ? URL.createObjectURL(file) : null;
  const visionOversize = isImage && file.size > MAX_IMAGE_BYTES;
  const chip = addAttachChip(file.name, thumb);
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (res.status === 423) throw new Error(t("staging.writableRetry"));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    finalizeAttachChip(chip, { rel: data.saved, name: data.name, kind: data.kind, thumb, visionOversize });
  } catch (e) {
    errorAttachChip(chip, e.message);
  }
}

// 用户气泡下回显本轮已发送的附件徽章（不可移除，仅记录；图像沿用缩略图）。
function addUserAttachments(atts) {
  const div = document.createElement("div");
  div.className = "msg user-files";
  for (const a of atts) {
    const chip = document.createElement("span");
    chip.className = "user-file-chip";
    if (a.thumb) {
      const img = document.createElement("img");
      img.className = "attach-thumb";
      img.src = a.thumb;
      img.alt = "";
      chip.appendChild(img);
    } else {
      chip.appendChild(fileTypeBadge(a.name)); // 其它类型 → 文件类型角标
    }
    chip.appendChild(document.createTextNode(` ${a.name}`)); // textContent 安全：文件名按字面显示
    div.appendChild(chip);
  }
  const log = $("#chat-log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// ＋按钮 → 打开文件选择器（流式中禁用）。
$("#attach-btn").addEventListener("click", () => {
  if (chatStreaming) return;
  const fi = $("#file-input");
  fi.value = ""; // 复位，允许重选同一文件
  fi.click();
});
$("#file-input").addEventListener("change", () => { uploadFiles($("#file-input").files); });

// 拖拽上传：计数法消抖嵌套 drag 事件，只对文件拖拽响应。
const composerEl = $("#chat-form");
let dragDepth = 0;
composerEl.addEventListener("dragenter", (e) => {
  if (!e.dataTransfer || !e.dataTransfer.types || !e.dataTransfer.types.includes("Files")) return;
  e.preventDefault(); dragDepth++; composerEl.classList.add("drag-active");
});
composerEl.addEventListener("dragover", (e) => {
  if (!e.dataTransfer || !e.dataTransfer.types || !e.dataTransfer.types.includes("Files")) return;
  e.preventDefault(); e.dataTransfer.dropEffect = "copy";
});
composerEl.addEventListener("dragleave", (e) => {
  if (!e.dataTransfer || !e.dataTransfer.types || !e.dataTransfer.types.includes("Files")) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) composerEl.classList.remove("drag-active");
});
composerEl.addEventListener("drop", (e) => {
  const f = e.dataTransfer && e.dataTransfer.files;
  if (!f || !f.length) return;
  e.preventDefault(); dragDepth = 0; composerEl.classList.remove("drag-active");
  if (!chatStreaming) uploadFiles(f);
});

// 粘贴上传：剪贴板含文件（截图 / 拷贝的文件）则上传；纯文本粘贴照常进文本域。
$("#chat-input").addEventListener("paste", (e) => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  const files = [];
  for (const it of items) {
    if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); }
  }
  if (!files.length) return; // 无文件 → 让文本照常粘贴
  e.preventDefault();
  if (!chatStreaming) uploadFiles(files);
});
