"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 写作业（ingest / parse / heal / audit / backfill）。
// ── 写：ingest（从 raw/ 选一篇 → 入队 → 轮询）────────────────────────────────

$("#ingest-btn").addEventListener("click", openIngestPicker);

let rawShowIngested = false; // ingest 选单过滤态：默认只看未收录，按钮可切看已收录

async function openIngestPicker() {
  rawShowIngested = false; // 每次开选单复位到默认「未收录」视图
  showOverlay("overlay.ingest", `<p class="muted">${escapeHtml(t("ingest.loadingRaw"))}</p>`);
  let files;
  try {
    ({ files } = await getJSON("/api/raw"));
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("ingest.loadRawFail", e.message))}</p>`;
    return;
  }
  renderRawPicker(files);
  overlayRepaint = () => renderRawPicker(files); // 语言切换纯重渲染（吃缓存 files + 当前过滤态）
}

// 纯渲染 raw/ 选单（文件名是内容、按字面显示；过滤条/空态/角标/按钮是 chrome）。
// 默认只显未收录（`ingested:false`），顶部一颗按钮在 未收录 ⇄ 已收录 间切换（非破坏：
// 已收录文件不从磁盘删、仍可预览/重投，只是默认收起）。
function renderRawPicker(files) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  if (!files.length) {
    box.innerHTML = `<p class="muted">${escapeHtml(t("ingest.rawEmpty"))}</p>`;
    return;
  }
  const pending = files.filter((f) => !f.ingested);
  const done = files.filter((f) => f.ingested);

  const bar = document.createElement("div"); // 过滤切换条：左计数、右切换按钮
  bar.className = "raw-filter";
  const toggle = document.createElement("button");
  toggle.className = "stage-act";
  toggle.textContent = rawShowIngested
    ? t("ingest.showPending", pending.length)
    : t("ingest.showIngested", done.length);
  toggle.addEventListener("click", () => {
    rawShowIngested = !rawShowIngested;
    renderRawPicker(files);
  });
  bar.appendChild(toggle);
  box.appendChild(bar);

  const shown = rawShowIngested ? done : pending;
  if (!shown.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = rawShowIngested ? t("ingest.noIngested") : t("ingest.allIngested");
    box.appendChild(empty);
    return;
  }
  for (const f of shown) {
    const row = document.createElement("div");
    row.className = "raw-pick";
    const name = document.createElement("button"); // 文件名即预览入口：点名预览，省去独立按钮
    name.className = "raw-name";
    name.textContent = `${f.name} (${(f.size / 1024).toFixed(1)} KB)`; // textContent：文件名按字面显示，无注入；体积按 KB
    name.addEventListener("click", () => previewRawFile(f.name, files));
    row.appendChild(name);
    if (f.ingested) {
      const badge = document.createElement("span"); // 「已收录」角标（已收录视图下标注）
      badge.className = "raw-badge";
      badge.textContent = t("ingest.ingestedBadge");
      row.appendChild(badge);
    }
    const btn = document.createElement("button");
    btn.textContent = "ingest"; // 命令名，不译
    btn.addEventListener("click", () => triggerIngest(`raw/${f.name}`));
    row.appendChild(btn);
    box.appendChild(row);
  }
}

// raw 源预览（读，ingest 前看清正文）：渲染 markdown，与 workspace 暂存区预览同款 overlay + 返回。
// `files` 透传供「返回」纯重渲染选单（吃缓存，不重拉 /api/raw）。
async function previewRawFile(name, files) {
  overlayRepaint = null; // 预览态不挂重画闭包：避免语言切换把预览刷回选单（返回后由 renderRawPicker 重置）
  const box = $("#overlay-body");
  box.innerHTML = `<p class="muted">${escapeHtml(t("ingest.loadingPreview"))}</p>`;
  const loading = box.firstChild; // 哨兵：迟到响应回来时若浮层已被别的 opener（showOverlay）换走则丢弃（防 stale-overwrite，与 searchToken 同纪律）
  const back = document.createElement("button");
  back.className = "stage-act";
  back.textContent = t("ingest.backToList");
  back.addEventListener("click", () => {
    renderRawPicker(files);
    overlayRepaint = () => renderRawPicker(files); // 复位重画闭包（同 openIngestPicker）
  });
  let data;
  try {
    data = await getJSON(`/api/raw/file?name=${encodeURIComponent(name)}`);
  } catch (e) {
    if (!box.contains(loading)) return; // 浮层已切走 → 不抢渲染（防覆盖新浮层）
    box.innerHTML = "";
    const err = document.createElement("p");
    err.className = "report-bad";
    err.textContent = t("ingest.previewFail", e.message);
    box.append(back, err);
    return;
  }
  if (!box.contains(loading)) return; // 同上：成功响应迟到亦不得覆盖新浮层
  box.innerHTML = "";
  const title = document.createElement("div");
  title.className = "stage-head";
  title.textContent = `raw/${name}`; // textContent：路径按字面显示
  const view = document.createElement("div");
  view.className = "stage-preview rendered";
  view.innerHTML = data.html; // render_page 已 sanitize（同 /api/page）
  enhanceContent(view); // raw 预览：富渲染源 ```mermaid→图 / ```X→高亮 / $…$→公式（决策P4.14-9，泛化 P4.13-8）
  box.append(back, title, view);
}

async function triggerIngest(target) {
  showOverlay("overlay.ingest", `<p class="muted">${escapeHtml(t("ingest.submitted", target))}</p>`);
  try {
    const { job_id } = await postJSON("/api/ingest", { target });
    await pollJob(job_id, target);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.submitFail", e.message))}</p>`;
  }
}

// 确定性解析（P4.6.1，决策P4.6.1-1/7）：上传文件 → POST /api/parse → 进度窗口（pollJob running 态渲染
// emit 推上的 backend 分级日志）→ 完成显回执 + 返回暂存区。区别于旧「问 agent 解析」（不再回填 composer）。
async function triggerParse(uploadPath) {
  showOverlay("overlay.parse", `<p class="muted">${escapeHtml(t("staging.parseSubmitted", uploadPath))}</p>`);
  stagingOpen = false; // 进度窗期间暂停自动刷新（避免并发 turn 收尾把进度刷掉）
  try {
    // 裸 fetch 以特判 423（可写 turn 活跃）；其余非 2xx → parseFail。
    const res = await fetch("/api/parse", {
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
    await pollJob(job_id, t("staging.parsing"), renderParseDone);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("staging.parseFail", e.message))}</p>`;
  }
}

function renderParseDone(job) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const ok = job.exit_code === 0;
  const badge = document.createElement("p");
  badge.innerHTML = ok
    ? `<span class="report-ok">${escapeHtml(t("common.passed"))}</span>`
    : `<span class="report-bad">${escapeHtml(t("common.exitCode", job.exit_code))}</span>`;
  box.appendChild(badge);
  if (job.output) {
    const pre = document.createElement("pre");
    pre.textContent = collapseCR(job.output); // backend 分级日志 + 回执（折叠 \r 进度条，纯 textContent 无注入）
    box.appendChild(pre);
  }
  const back = document.createElement("button");
  back.className = "stage-act";
  back.textContent = t("staging.backToStaging");
  back.addEventListener("click", () => { stagingPath = null; stagingOpen = true; loadStaging(); });
  box.appendChild(back);
}

// 通用作业轮询骨架（ingest / heal 共用）。`renderDone(job)` 可选：heal 有结构化 result，传入
// 自定义渲染；省略则走 ingest 默认（退出码徽标 + output 文本）。完成后统一 loadPages 刷新。
async function pollJob(jobId, label, renderDone) {
  for (;;) {
    const job = await getJSON(`/api/jobs/${jobId}`);
    if (job.state === "done") {
      if (renderDone) {
        renderDone(job);
      } else {
        const ok = job.exit_code === 0;
        const badge = ok
          ? `<span class="report-ok">${escapeHtml(t("common.passed"))}</span>`
          : `<span class="report-bad">${escapeHtml(t("common.exitCode", job.exit_code))}</span>`;
        $("#overlay-body").innerHTML = `<p>${escapeHtml(label)} ${badge}</p><pre>${escapeHtml(collapseCR(job.output || t("common.noOutput")))}</pre>`;
      }
      await loadPages(); // 写后刷新 wiki 搜索列表（heal 新建了页）
      return;
    }
    // running 态：渲染已累加的增量 output（决策P4.6.1-7③/-11）——解析作业的 backend 分级日志在此
    // 实时可见（emit 推上）；ingest/heal/backfill 顺带受益。无 output 时仍只显运行徽标。
    const running = `<p class="muted">${escapeHtml(t("job.running", label, job.state))}</p>`;
    const out = job.output ? `<pre>${escapeHtml(collapseCR(job.output))}</pre>` : "";
    // 瞬时进度行（A+ 心跳）：ingest 这类静默长跑作业的活跃提示（「⏳ 仍在运行 Ns · wiki/ 已写 N 页」）。
    // 仅 running 期出现，done 后服务端清空 → 最终结果只剩干净摘要。放在 output 之后、轻样式。
    const prog = job.progress
      ? `<p class="muted job-hb">${escapeHtml(job.progress)}</p>`
      : "";
    $("#overlay-body").innerHTML = `${running}${out}${prog}`;
    await sleep(400);
  }
}

// ── heal：缺失实体物化（预览 worklist → 一键物化 → 轮询结构化回执，P4.3）─────────────
//
// 顶栏「heal」→ 浮层先拉零-LLM 预览（可调 limit / min-refs），列本批将物化的高频缺页 + 推迟项；
// 「物化」→ POST /api/heal（入单写者队列，与 ingest 同 worker 串行）→ 复用 pollJob 骨架，但因
// heal 有结构化 result，完成分支按 job.result 渲染回执（resolved / 仍断 / 非预期写 / 推迟 /
// 退出码徽标），agent 散文取自 job.output（同 ingest）。

$("#heal-btn").addEventListener("click", () => openHealPreview());

async function openHealPreview(limit, minRefs) {
  showOverlay("overlay.heal", `<p class="muted">${escapeHtml(t("heal.computing"))}</p>`);
  const qs = new URLSearchParams();
  if (limit) qs.set("limit", limit);
  if (minRefs) qs.set("min_refs", minRefs);
  const suffix = qs.toString() ? `?${qs}` : "";
  let data;
  try {
    data = await getJSON(`/api/heal/preview${suffix}`);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("heal.previewFail", e.message))}</p>`;
    return;
  }
  renderHealPreview(data, limit, minRefs);
  overlayRepaint = () => renderHealPreview(data, limit, minRefs); // 语言切换纯重渲染（吃缓存 data）
}

function renderHealPreview(data, limit, minRefs) {
  const box = $("#overlay-body");
  box.innerHTML = "";

  const worklist = data.worklist || [];
  const postponed = data.postponed || [];

  // 勾选集：默认全选（== 旧「整批物化」行为）。顶部物化按钮的文案/禁用随勾选实时更新。
  const selected = new Set(worklist.map((w) => w.target));
  let go = null;
  const updateGo = () => {
    if (!go) return;
    go.textContent = selected.size ? t("heal.materializeN", selected.size) : t("heal.noTarget");
    go.disabled = selected.size === 0;
  };

  // 顶部一行：左「物化 N 个目标」（仅 worklist 非空），右「limit / min-refs / 预览」微调控件。
  const top = document.createElement("div");
  top.className = "heal-top";
  if (worklist.length) {
    go = document.createElement("button");
    go.className = "heal-go";
    go.addEventListener("click", () => {
      // 用**本次预览所用的** limit/min-refs（renderHealPreview 入参），而非输入框现值：勾选项是
      // 按这份 worklist 算出来的，若改了输入框却没点「预览」，用现值会让服务端按不同参数重算出
      // 另一批，交集把勾选项悄悄漏掉（决策P4.3-3）。「预览」按钮才用输入框现值重拉。
      if (selected.size) triggerHeal(limit || "", minRefs || "", [...selected]);
    });
    top.appendChild(go);
    updateGo();
  }
  // 微调控件：limit / min-refs + 重新预览（默认值即常量，留空表示用服务端默认）。
  const ctrl = document.createElement("div");
  ctrl.className = "heal-ctrl";
  const limitIn = document.createElement("input");
  limitIn.type = "number"; limitIn.min = "1"; limitIn.className = "heal-num";
  limitIn.placeholder = "limit"; if (limit) limitIn.value = limit;
  const minIn = document.createElement("input");
  minIn.type = "number"; minIn.min = "1"; minIn.className = "heal-num";
  minIn.placeholder = "min-refs"; if (minRefs) minIn.value = minRefs;
  const refresh = document.createElement("button");
  refresh.textContent = t("heal.preview");
  refresh.addEventListener("click", () => openHealPreview(limitIn.value || "", minIn.value || ""));
  ctrl.append(limitIn, minIn, refresh);
  top.appendChild(ctrl);
  box.appendChild(top);

  if (!worklist.length) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = postponed.length
      ? t("heal.batchEmpty", postponed.length)
      : t("heal.none");
    box.appendChild(note);
    if (!postponed.length) return;
  } else {
    const head = document.createElement("p");
    head.textContent = t("heal.pickHead", worklist.length);
    box.appendChild(head);
    for (const w of worklist) {
      // <label> 包 checkbox：整行可点切换。默认勾选，change 同步 selected + 刷新按钮。
      const row = document.createElement("label");
      row.className = "heal-item";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "heal-check";
      cb.checked = true;
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(w.target);
        else selected.delete(w.target);
        updateGo();
      });
      const bodyEl = document.createElement("span");
      bodyEl.className = "heal-item-body";
      const tgt = document.createElement("span");
      tgt.className = "heal-target";
      tgt.textContent = `+ ${w.target}`; // textContent：目标名/文件名按字面显示，无注入
      const meta = document.createElement("span");
      meta.className = "heal-meta";
      meta.textContent = t("heal.refBy", w.ref_count, (w.ref_pages || []).join("、"));
      bodyEl.append(tgt, meta);
      row.append(cb, bodyEl);
      box.appendChild(row);
    }
  }

  if (postponed.length) {
    const ph = document.createElement("p");
    ph.className = "muted";
    ph.textContent = t("heal.postponedHead", postponed.length);
    box.appendChild(ph);
    for (const w of postponed) {
      const row = document.createElement("div");
      row.className = "heal-item postponed";
      row.textContent = t("heal.postponedItem", w.target, w.ref_count);
      box.appendChild(row);
    }
  }
}

async function triggerHeal(limit, minRefs, targets) {
  showOverlay("overlay.heal", `<p class="muted">${escapeHtml(t("heal.submitted"))}</p>`);
  const body = {};
  if (limit) body.limit = Number(limit);
  if (minRefs) body.min_refs = Number(minRefs);
  // 勾选子集随请求发出；服务端仍重算 worklist 再取交集（陈旧/越界目标被丢弃，决策P4.3-3 修订）。
  if (targets && targets.length) body.targets = targets;
  try {
    const { job_id } = await postJSON("/api/heal", body);
    await pollJob(job_id, "heal", renderHealDone);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.submitFail", e.message))}</p>`;
  }
}

function renderHealDone(job) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const ok = job.exit_code === 0;
  const badge = document.createElement("p");
  badge.innerHTML = ok
    ? `<span class="report-ok">${escapeHtml(t("common.passed"))}</span>`
    : `<span class="report-bad">${escapeHtml(t("common.exitCode", job.exit_code))}</span>`;
  box.appendChild(badge);

  const result = job.result;
  if (result) {
    const receipts = result.receipts || [];
    const resolved = receipts.filter((r) => r.status === "resolved");
    const still = receipts.filter((r) => r.status === "still_broken");
    const summary = document.createElement("p");
    summary.textContent = t("heal.doneSummary", receipts.length, resolved.length, still.length);
    box.appendChild(summary);
    for (const r of resolved) {
      const div = document.createElement("div");
      div.className = "heal-receipt resolved";
      div.textContent = t("heal.resolved", r.target, r.resolved_to); // 路径是内容、按字面显示
      box.appendChild(div);
    }
    for (const r of still) {
      const div = document.createElement("div");
      div.className = "heal-receipt broken";
      div.textContent = t("heal.stillBroken", r.target, r.reason);
      box.appendChild(div);
    }
    if ((result.unexpected_writes || []).length) {
      const h = document.createElement("div");
      h.className = "heal-warn";
      h.textContent = t("heal.unexpected");
      box.appendChild(h);
      for (const p of result.unexpected_writes) {
        const d = document.createElement("div");
        d.className = "heal-warn-item";
        d.textContent = `! ${p}`;
        box.appendChild(d);
      }
    }
    if ((result.postponed || []).length) {
      const d = document.createElement("div");
      d.className = "muted";
      d.textContent = t("heal.donePostponed", result.postponed.length);
      box.appendChild(d);
    }
  }

  if (job.output) {
    const pre = document.createElement("pre");
    pre.textContent = collapseCR(job.output); // agent 散文（折叠 \r 进度条，同 ingest）
    box.appendChild(pre);
  }
}

// ── audit：语义审计（预览漂移源组 → 一键复核 → 轮询结构化回执，P4.12）──────────────
//
// 顶栏「审计」→ 浮层先拉零-LLM 预览（可调 limit），列本批将复核的漂移源组（raw 已变但 wiki 未重综合）
// + 推迟项；「审计」→ POST /api/audit（入单写者队列，与 ingest/heal/backfill 同 worker 串行）→ 复用
// pollJob 骨架，因 audit 有结构化 result，完成分支按 job.result 渲染回执（已刷新指纹 / 未完成 / 推迟 /
// 退出码徽标），agent 散文取自 job.output（同 heal）。无子集勾选（决策P4.12-3，唯一旋钮是 limit）；
// 复用 heal-* 样式类（决策P4-3 克制，不引新 CSS）。
$("#audit-btn").addEventListener("click", () => openAuditPreview());

async function openAuditPreview(limit) {
  showOverlay("overlay.audit", `<p class="muted">${escapeHtml(t("audit.computing"))}</p>`);
  const suffix = limit ? `?limit=${encodeURIComponent(limit)}` : "";
  let data;
  try {
    data = await getJSON(`/api/audit/preview${suffix}`);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("audit.previewFail", e.message))}</p>`;
    return;
  }
  renderAuditPreview(data, limit);
  overlayRepaint = () => renderAuditPreview(data, limit); // 语言切换纯重渲染（吃缓存 data）
}

function renderAuditPreview(data, limit) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const groups = data.groups || [];
  const postponed = data.postponed || [];

  // 顶部一行：左「审计 N 个漂移源」（仅 groups 非空），右「limit + 预览」微调控件。
  const top = document.createElement("div");
  top.className = "heal-top";
  if (groups.length) {
    const go = document.createElement("button");
    go.className = "heal-go";
    go.textContent = t("audit.auditN", groups.length);
    // 用**本次预览所用的** limit（renderAuditPreview 入参），而非输入框现值（同 heal 决策P4.3-3 的口径）。
    go.addEventListener("click", () => triggerAudit(limit || ""));
    top.appendChild(go);
  }
  const ctrl = document.createElement("div");
  ctrl.className = "heal-ctrl";
  const limitIn = document.createElement("input");
  limitIn.type = "number"; limitIn.min = "1"; limitIn.className = "heal-num";
  limitIn.placeholder = "limit"; if (limit) limitIn.value = limit;
  const refresh = document.createElement("button");
  refresh.textContent = t("audit.preview");
  refresh.addEventListener("click", () => openAuditPreview(limitIn.value || ""));
  ctrl.append(limitIn, refresh);
  top.appendChild(ctrl);
  box.appendChild(top);

  if (!groups.length) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = postponed.length ? t("audit.batchEmpty", postponed.length) : t("audit.none");
    box.appendChild(note);
    if (!postponed.length) return;
  } else {
    const head = document.createElement("p");
    head.textContent = t("audit.head", groups.length);
    box.appendChild(head);
    for (const g of groups) {
      const row = document.createElement("div");
      row.className = "heal-item";
      const bodyEl = document.createElement("span");
      bodyEl.className = "heal-item-body";
      const tgt = document.createElement("span");
      tgt.className = "heal-target";
      tgt.textContent = `⚠ ${g.slug}`; // textContent：slug/文件名按字面显示，无注入
      const meta = document.createElement("span");
      meta.className = "heal-meta";
      meta.textContent = t("audit.groupMeta", g.raw_path, (g.members || []).length, (g.members || []).join("、"));
      bodyEl.append(tgt, meta);
      row.appendChild(bodyEl);
      box.appendChild(row);
    }
  }

  if (postponed.length) {
    const ph = document.createElement("p");
    ph.className = "muted";
    ph.textContent = t("audit.postponedHead", postponed.length);
    box.appendChild(ph);
    for (const g of postponed) {
      const row = document.createElement("div");
      row.className = "heal-item postponed";
      row.textContent = t("audit.postponedItem", g.slug, (g.members || []).length);
      box.appendChild(row);
    }
  }
}

async function triggerAudit(limit) {
  showOverlay("overlay.audit", `<p class="muted">${escapeHtml(t("audit.submitted"))}</p>`);
  const body = {};
  if (limit) body.limit = Number(limit);
  try {
    const { job_id } = await postJSON("/api/audit", body);
    await pollJob(job_id, "audit", renderAuditDone);
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.submitFail", e.message))}</p>`;
  }
}

function renderAuditDone(job) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const ok = job.exit_code === 0;
  const badge = document.createElement("p");
  badge.innerHTML = ok
    ? `<span class="report-ok">${escapeHtml(t("common.passed"))}</span>`
    : `<span class="report-bad">${escapeHtml(t("common.exitCode", job.exit_code))}</span>`;
  box.appendChild(badge);

  const result = job.result;
  if (result) {
    const receipts = result.receipts || [];
    const refreshed = receipts.filter((r) => r.status === "refreshed");
    const incomplete = receipts.filter((r) => r.status === "incomplete");
    const summary = document.createElement("p");
    summary.textContent = t("audit.doneSummary", receipts.length, refreshed.length, incomplete.length);
    box.appendChild(summary);
    for (const r of refreshed) {
      const div = document.createElement("div");
      div.className = "heal-receipt resolved";
      div.textContent = t("audit.refreshed", r.slug, (r.members || []).length); // slug 是内容、按字面显示
      box.appendChild(div);
    }
    for (const r of incomplete) {
      const div = document.createElement("div");
      div.className = "heal-receipt broken";
      div.textContent = t("audit.incomplete", r.slug, r.reason);
      box.appendChild(div);
    }
    if ((result.postponed || []).length) {
      const d = document.createElement("div");
      d.className = "muted";
      d.textContent = t("audit.donePostponed", result.postponed.length);
      box.appendChild(d);
    }
  }

  if (job.output) {
    const pre = document.createElement("pre");
    pre.textContent = collapseCR(job.output); // agent 散文（折叠 \r 进度条，同 heal）
    box.appendChild(pre);
  }
}

// ── backfill：把一次好问答沉淀为 wiki/syntheses/ 综合页（P4.8）─────────────────────
//
// 顶栏「回填」或问答气泡的「沉淀」小按钮 → 同一浮层（问题文本域 + 「沉淀」提交）→ POST /api/backfill
// （入单写者队列、与 ingest/heal 同 worker 串行）→ 复用 pollJob 默认渲染（退出码徽标 + job.output：
// 答案 + 门禁回执）。完成后 loadPages() 刷新右栏 wiki，新综合页可搜索·点开核对。入口②只预填问题、
// 不搬只读答案原文（backfill 是另起一次 gated 写，由子进程 Agent 重新综合，决策P4.8-3）。

$("#backfill-btn").addEventListener("click", () => openBackfill());

function openBackfill(prefillQuestion = "") {
  showOverlay("overlay.backfill", ""); // 标题双语 key（同 heal/ingest）
  renderBackfillForm(prefillQuestion);
  // 语言切换纯重渲染：从当前文本域读回实时输入作新 prefill（用户已输入未提交的内容不丢，决策P4.8-9）；
  // 文本域尚未挂载（理论边界）时回退最初 prefill。
  overlayRepaint = () => renderBackfillForm($("#backfill-question")?.value ?? prefillQuestion);
}

function renderBackfillForm(prefillQuestion) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  const hint = document.createElement("p");
  hint.className = "muted";
  hint.textContent = t("backfill.hint");
  const ta = document.createElement("textarea");
  ta.id = "backfill-question";
  ta.className = "backfill-question";
  ta.placeholder = t("backfill.placeholder");
  ta.value = prefillQuestion || "";
  const submit = document.createElement("button");
  submit.className = "backfill-go";
  submit.textContent = t("backfill.submit");
  const updateDisabled = () => { submit.disabled = !ta.value.trim(); }; // 空问题禁用（双重兜底，非依赖）
  ta.addEventListener("input", updateDisabled);
  submit.addEventListener("click", () => {
    const q = ta.value.trim();
    if (q) triggerBackfill(q);
  });
  box.append(hint, ta, submit);
  updateDisabled();
}

async function triggerBackfill(question) {
  showOverlay("overlay.backfill", `<p class="muted">${escapeHtml(t("backfill.submitted"))}</p>`);
  try {
    // 不用 postJSON（它只 throw `url → status`、丢 detail，无法分流 423）：用裸 fetch 显式特判
    // 423（可写 turn 活跃）→ 本地化 backfill.busy；其余非 2xx → common.submitFail（带状态码）。
    const res = await fetch("/api/backfill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (res.status === 423) {
      $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("backfill.busy"))}</p>`;
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { job_id } = await res.json();
    await pollJob(job_id, "backfill"); // 复用默认渲染：退出码徽标 + job.output（答案 + 门禁回执）
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.submitFail", e.message))}</p>`;
  }
}

// 给一条问答气泡尾部挂「沉淀」小按钮：点击预填该轮用户问题、打开 backfill 浮层（决策P4.8-3）。
// done / stopped 收尾时调用；read-only / workspace-write 两种姿态都挂（backfill 是独立写端点）。
function appendBackfillButton(botEl) {
  if (readerMode) return; // reader 下 /api/backfill 已裁（404）：不挂「沉淀」写按钮，免无效写入口（评审 codex P3）
  if (botEl.querySelector(".bubble-backfill")) return; // 幂等：done 之后又 stopped 不重复挂
  const question = botEl.dataset.question || "";
  const btn = document.createElement("button");
  btn.className = "bubble-backfill";
  btn.type = "button";
  // 水滴沉入横线图标（#i-backfill，贴「沉淀」意象）+ 文案。
  btn.innerHTML = `<svg class="ico"><use href="#i-backfill"/></svg>`;
  const label = document.createElement("span");
  label.textContent = t("chat.backfill");
  btn.appendChild(label);
  btn.addEventListener("click", () => openBackfill(question)); // 预填该轮问题（不搬答案原文）
  botEl.appendChild(btn);
}
