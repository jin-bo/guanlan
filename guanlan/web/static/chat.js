"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 问答/多轮会话/历史/SSE/斜杠命令/写回执。
// ── 问答 / 多轮会话（fetch 流式读 SSE）─────────────────────────────────────

let conversationId = null;
let chatLoadToken = 0; // 每次切换/新建自增；历史气泡异步回放回来若 token 已变则丢弃（防迟到覆盖）
let chatStreaming = false; // 一轮在飞时为 true：发送按钮切「停止」、回车不再发新轮
let pendingStop = false; // 首轮 id 未回时点了「停止」：标记待停，start 帧回填 id 后立即补发
let currentMode = "read-only"; // 当前会话姿态（P4.5）：徽标显示、/mode 切换更新
let defaultMode = "read-only"; // 进程 --mode 默认（启动 /api/info 拉取）：新会话开局姿态
let readerMode = false; // 只读多会话部署（P4.9）：启动 /api/info 的 reader 字段；驱动隐藏写/历史/维护 chrome
                        // 与 ?c= 按-id 恢复（决策P4.9-9/12）。安全边界是端点 404，这只是 UX。

// 会话 id ↔ 浏览器 URL 同步：把当前会话写进 ?c=<id>，刷新即据此懒恢复续聊（startup 读取）。
// 用 replaceState（不入浏览器历史栈）——右栏 wiki 走自家 history 数组，与浏览器前进/后退互不干扰。
function syncConversationUrl() {
  const url = new URL(window.location.href);
  if (conversationId) url.searchParams.set("c", conversationId);
  else url.searchParams.delete("c");
  // 注意：wiki.js 有模块级 `let history = []`（右栏 wiki 视图栈）。经典脚本共享全局词法作用域，
  // 该 `let` 在本文件同样遮蔽 window.history，故这里必须显式走 window.history，否则
  // history.replaceState 落到那个数组上、抛 not a function。
  window.history.replaceState(null, "", url);
}

// 唯一改写 conversationId 的入口：改值即同步 URL（含置 null → 抹掉 ?c=）。
function setConversation(id) {
  conversationId = id;
  syncConversationUrl();
}

// 姿态徽标（P4.5）：随 /mode 翻转更新；workspace-write 用醒目色提示「Agent 可写」。
function setModeBadge(mode) {
  currentMode = mode || "read-only";
  const el = $("#mode-badge");
  if (!el) return;
  el.textContent = currentMode;
  el.classList.toggle("writable", currentMode === "workspace-write");
  el.title = currentMode === "workspace-write" ? t("mode.writeTip") : t("mode.readonlyTip");
}

// 只读多会话部署（P4.9）：隐藏写 / 历史枚举 / 维护诊断 chrome——纯 UX（安全边界是端点 404、不是隐藏）。
// 保留：新会话 / 发送 / Wiki 导航 / 语言。用 inline display:none（稳胜 .btn 的 display 规则，
// [hidden] 属性会被 .btn 的 display 覆盖、未必生效）。决策P4.9-9/10/11/17。
function applyReaderMode() {
  const hideIds = [
    "history-btn",                                          // 枚举他人会话 → 破隔离（决策P4.9-3）
    "feed-btn", "ingest-btn", "heal-btn", "backfill-btn", "audit-btn", "staging-btn", // 写端点（reader 下 404）
    "attach-btn",                                           // 上传是写（404）
    "graph-btn",                                            // 重建是写者（404）；不给死链（决策P4.9-11）
    "mode-badge",                                           // 姿态恒只读、/mode 已禁可写 → 冻结隐藏（决策P4.9-4）
  ];
  for (const id of hideIds) {
    const el = document.getElementById(id);
    if (el) el.style.display = "none";
  }
  // check/health/lint 维护诊断按钮（决策P4.9-10）：端点只读保留、仅隐藏按钮。
  for (const el of document.querySelectorAll("[data-report]")) el.style.display = "none";
  // 收掉顶栏分隔符，避免隐藏后留连续竖线。
  for (const el of document.querySelectorAll(".topbar .act-sep")) el.style.display = "none";
}

function startNewConversation() {
  // 仅清本地视图、开启新会话——**绝不** DELETE 旧会话：P4.2 起 DELETE 会级联删盘，删的是
  // 持久历史，普通"切换/新建"不该销毁记录。旧会话持久化开时已落盘、可从"历史会话"再开；
  // 持久化关时它仍是 live、列在历史里（进程退出即清）。要永久删除走历史列表的"删除"。
  chatLoadToken++; // 作废任何在飞的历史气泡回放（用户已开新会话）
  setConversation(null);
  $("#chat-log").innerHTML = "";
  clearPendingInteractions(); // P4.15：清掉旧会话残留的确认/提问气泡 + 其倒计时 setInterval（防泄漏）
  clearAttachments(); // 弃掉未发送的附件徽章（新会话不继承上一会话的待发附件）
  setModeBadge(defaultMode); // 新会话开局 = 进程默认姿态（决策P4.5-8）
}
$("#chat-new").addEventListener("click", startNewConversation);

// ── 历史会话（P4.2）：列出内存 ∪ 盘上会话，点开续聊、删除级联删盘 ────────────────
$("#history-btn").addEventListener("click", openHistory);

async function openHistory() {
  showOverlay("overlay.history", `<p class="muted">${escapeHtml(t("history.loading"))}</p>`);
  let conversations;
  try {
    ({ conversations } = await getJSON("/api/conversations"));
  } catch (e) {
    $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("history.loadFail", e.message))}</p>`;
    return;
  }
  renderHistory(conversations);
  overlayRepaint = () => renderHistory(conversations); // 语言切换纯重渲染（吃缓存 conversations）
}

// 纯渲染历史会话列表（标题是内容、按字面显示；空态/徽标/按钮是 chrome）。
function renderHistory(conversations) {
  const box = $("#overlay-body");
  box.innerHTML = "";
  if (!conversations.length) {
    box.innerHTML = `<p class="muted">${escapeHtml(t("history.empty"))}</p>`;
    return;
  }
  for (const c of conversations) {
    const row = document.createElement("div");
    row.className = "conv-row";
    const meta = document.createElement("div");
    meta.className = "conv-meta";
    const title = document.createElement("button"); // 标题即打开入口：点标题续聊，省去独立「打开」按钮
    title.className = "conv-title";
    title.textContent = c.title || t("history.untitled"); // textContent：标题按字面显示，无注入
    title.addEventListener("click", () => openConversation(c));
    const tag = document.createElement("span");
    tag.className = "conv-tag";
    // live 条目报真实轮次，冷条目报消息总数（list_sessions 给 message_count，非轮次）。
    const count = c.live ? t("history.turns", c.turns ?? 0) : t("history.messages", c.messages ?? 0);
    tag.textContent = c.live ? t("history.live", count) : t("history.cold", count);
    meta.append(title, tag);
    const del = document.createElement("button");
    del.className = "conv-del";
    del.textContent = "🗑"; // 图标化：删除该会话（图标不译，title 提示走 i18n）
    del.title = t("history.delete");
    del.setAttribute("aria-label", t("history.delete"));
    del.addEventListener("click", () => deleteConversation(c.id, row));
    row.append(meta, del);
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
  await restoreConversation(c);
}

// 切到某会话并回放其历史气泡：仅切本地 id（下一条提问即触发后端透明 rebuild 懒恢复）。**绝不**
// DELETE 任何会话——它仍要留在历史里，普通切换不该销毁其持久记录（要永久删除走历史列表的"删除"）。
// 抽出供「历史会话」点开与启动时 ?c= 懒恢复共用。
async function restoreConversation(c) {
  const tok = ++chatLoadToken;
  setConversation(c.id);
  const log = $("#chat-log");
  log.innerHTML = "";
  clearPendingInteractions(); // P4.15：清掉上一会话残留的确认/提问气泡 + 倒计时（再据本会话 info.pending 重渲）
  $("#chat-input").focus();
  // 切会话即刷新姿态徽标（评审 P2）：否则徽标停留在上一个会话的姿态——一个 workspace-write 的 live
  // 会话切过去仍显 read-only（或反之），正是徽标该指示写能力时误导。先乐观置进程默认（冷会话恢复即
  // 此姿态），再据 /info 校正：live 会话报真实 _mode、冷会话报 default_mode（均含 mode 字段）。
  // 失败/已切走不阻断回放，徽标退回默认。
  setModeBadge(defaultMode);
  getJSON(`/api/chat/${encodeURIComponent(c.id)}/info`)
    .then((info) => {
      if (tok !== chatLoadToken || !info) return;
      if (info.mode) setModeBadge(info.mode);
      if (info.confirm_mode === "auto") showAutoModeNote(); // P4.15：本会话已自动放行（可恢复）
      rerenderPendingInteractions(info.pending); // P4.15：断线重连重渲染未决确认/提问气泡
    })
    .catch(() => {});
  // 回放历史气泡（user/assistant）：拉该会话的消息，逐条上屏。失败仅降级为一条提示、不阻断续聊。
  try {
    const { messages } = await getJSON(`/api/conversations/${encodeURIComponent(c.id)}/messages`);
    if (tok !== chatLoadToken) return; // 已切走（开了别的会话/新会话），丢弃迟到回放
    if (!messages.length) {
      addMsg("note", t("conv.loadedEmpty", c.title || t("history.untitled")));
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
          enhanceContent(el); // 历史重渲：富渲染答案 ```mermaid→图 / ```X→高亮 / $…$→公式（决策P4.14-9，泛化 P4.13-8）
        } else {
          el.textContent = m.content;
        }
        appendCopyButton(el, m.content); // 历史气泡也挂「复制原始 Markdown」：复制答案源
      }
    }
  } catch (e) {
    if (tok !== chatLoadToken) return;
    addMsg("note", t("conv.loadedErr", c.title || t("history.untitled"), e.message));
  }
}

async function deleteConversation(id, row) {
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
    if (id === conversationId) setConversation(null); // 删的是当前会话则清掉本地引用（连带抹 ?c=）
    row.remove();
    if (!$("#overlay-body").querySelector(".conv-row")) {
      $("#overlay-body").innerHTML = `<p class="muted">${escapeHtml(t("history.empty"))}</p>`;
    }
  } catch (e) {
    // 经 textContent 赋值，浏览器自会转义；勿再 escapeHtml（否则显示字面 &lt; 实体）。
    row.querySelector(".conv-del").textContent = t("history.deleteFail", e.message);
  }
}

function submitChat(override) {
  // 一轮在飞时不重复发送：回车与「发送」共用此守卫，避免在同一会话上叠起并发轮次（服务端
  // conv.lock 会把第二轮挂住、前端则堆出空 bot 气泡）。停止只由按钮点击触发（见下），回车
  // 流式中一律按空操作处理——不让回车误把当前轮停掉。输入保留不清。
  if (chatStreaming) return;
  // override（如「让 Agent 修复」发的 REPAIR_PROMPT）直接作消息发，不读输入框、不走斜杠解析。
  if (typeof override === "string" && override) { sendChat(override); return; }
  const input = $("#chat-input");
  const msg = input.value.trim();
  const atts = pendingAttachments.slice(); // 快照本轮附件（仅已上传成功者在册）
  if (!msg && !atts.length) return; // 空消息且无附件 → 不发
  // 斜杠命令前置解析（决策P4.4-1）：以 `/` 开头**一律**本地处理、**绝不进 /api/chat**——不打 LLM、
  // 不占对话轮次、不入历史。**即便有待发附件**也走本地（绝不把斜杠命令当消息连同附件发给 LLM）；
  // 附件保留 pending、留给下一条真消息。
  if (msg.startsWith("/")) { input.value = ""; autoGrowInput(); handleSlash(msg); return; }
  input.value = "";
  autoGrowInput();
  // 附件已快照，清 composer 徽章行；不回收缩略图 blob URL（气泡回显沿用）。
  clearAttachments({ revoke: false });
  sendChat(msg, atts);
}

// ── 斜杠命令（P4.4）：镜像 agentao 交互 CLI 的 8 条只读命令，全部本地解析、不进 /api/chat ──
//
// 展示类（/help）纯客户端；生命周期类（/new /clear）复用既有逻辑；自省类（/status /context
// /skills /tools /mode）调只读端点（有会话 GET /api/chat/{id}/info，否则 app 级 GET /api/info），
// 按命令取字段渲染成 note 气泡。/compact 与未知命令一律本地「未知命令」提示（决策P4.4-5）。

// /help 列这 8 条（**无** /compact，决策P4.4-5）。函数式：每次按当前语言取词（P4.7）。
function slashHelpLines() {
  return [
    t("slash.help.help"),
    t("slash.help.new"),
    t("slash.help.clear"),
    t("slash.help.status"),
    t("slash.help.context"),
    t("slash.help.skills"),
    t("slash.help.tools"),
    t("slash.help.mode"),
  ];
}

// 命令输出气泡：buildFn 用 textContent 填充各行/项（杜绝注入），落对话流、不开浮层（决策P4.4-2）。
function addNote(buildFn) {
  const div = document.createElement("div");
  div.className = "msg note";
  buildFn(div);
  const log = $("#chat-log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

// 多行纯文本 note：每行一个 <div>（textContent，安全）。
function noteLines(lines) {
  return addNote((div) => {
    for (const ln of lines) {
      const p = document.createElement("div");
      p.textContent = ln;
      div.appendChild(p);
    }
  });
}

async function handleSlash(raw) {
  const body = raw.trim();
  const cmd = (body.slice(1).split(/\s+/)[0] || "").toLowerCase();
  addMsg("cmd", body); // 回显键入的命令（cmd 样式），与其只读输出 note 区分
  switch (cmd) {
    case "help":
      return noteLines(slashHelpLines());
    case "new":
      startNewConversation();
      return noteLines([t("slash.newDone")]);
    case "clear":
      return slashClear();
    case "mode": {
      // 带参 → 切换姿态（POST /api/chat/{id}/mode）；不带参 → 报当前姿态（只读自省）。
      const arg = (body.slice(1).split(/\s+/)[1] || "").toLowerCase();
      if (arg) return slashSetMode(arg);
      return slashInfo(cmd);
    }
    case "status":
    case "context":
    case "skills":
    case "tools":
      return slashInfo(cmd);
    default:  // 含 /compact（决策P4.4-5）与一切未知命令
      return noteLines([t("slash.unknown", body)]);
  }
}

// /clear：有活动会话 → DELETE 级联删盘（== 删当前 + 新建）；无活动会话退化为 /new（决策P4.4-4）。
// **不**触 memory_manager（Web 无记忆 UI、越权，与 CLI /clear 清记忆刻意不对齐）。
async function slashClear() {
  if (!conversationId) {
    startNewConversation();
    return noteLines([t("slash.clearNoSession")]);
  }
  const id = conversationId;
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    return noteLines([t("slash.clearFail", e.message)]);
  }
  startNewConversation(); // 先清屏 + 置 conversationId=null，再把确认 note 落进新会话视图
  noteLines([t("slash.clearDone")]);
}

// /mode <值>：运行时翻姿态（P4.5 决策P4.5-5）。仅 read-only / workspace-write 合法；无活动会话
// 或冷会话先提示续聊（端点 404/409）。成功后更新徽标 + note 回报新姿态。
async function slashSetMode(arg) {
  if (arg !== "read-only" && arg !== "workspace-write") {
    return noteLines([t("slash.modeInvalidArg", arg)]);
  }
  if (!conversationId) {
    return noteLines([t("slash.modeNoSession")]);
  }
  let res;
  try {
    res = await fetch(`/api/chat/${encodeURIComponent(conversationId)}/mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: arg }),
    });
  } catch (e) {
    return noteLines([t("slash.modeFail", e.message)]);
  }
  if (res.status === 409) return noteLines([t("slash.modeInactive")]);
  if (res.status === 422) return noteLines([t("slash.modeIllegal")]);
  if (!res.ok) return noteLines([t("slash.modeFailHttp", res.status)]);
  const { mode } = await res.json();
  setModeBadge(mode);
  return noteLines([
    mode === "workspace-write" ? t("slash.modeToWrite") : t("slash.modeToRead"),
  ]);
}

// 自省类：有会话取会话级 info、否则取 app 级 info，按命令渲染。冷会话（live:false）的
// context/skills/tools 为 null → 提示「续聊一轮以恢复」（决策P4.4-7）。
async function slashInfo(cmd) {
  const url = conversationId
    ? `/api/chat/${encodeURIComponent(conversationId)}/info`
    : "/api/info";
  let info;
  try {
    info = await getJSON(url);
  } catch (e) {
    return noteLines([t("slash.infoFail", cmd, e.message)]);
  }
  renderSlashInfo(cmd, info);
}

function renderSlashInfo(cmd, info) {
  const appLevel = info.live === undefined; // /api/info 无 live 字段（无会话/无 agent）
  const cold = info.live === false;         // 盘上-only 冷会话（部分信息）

  if (cmd === "mode") {
    if (!appLevel && info.mode) setModeBadge(info.mode); // 会话级 info 同步徽标
    const hint = info.mode === "workspace-write" ? t("slash.modeHintWrite") : t("slash.modeHintRead");
    return noteLines([t("slash.modeLine", info.mode || "read-only", hint)]);
  }
  if (cmd === "status") {
    const lines = [];
    if (appLevel) {
      lines.push(t("slash.kb", info.kb_name));
      lines.push(t("slash.model", info.model || t("slash.modelUnspecified")));
      lines.push(t("slash.mode", info.mode));
      // reader 下 /api/info 移除 conversations/max_conversations（决策P4.9-9）：字段缺失则跳过本行，
      // 不渲染 `undefined/undefined`。非 reader 下二字段仍在、照常显示（不回归）。
      if (info.conversations !== undefined) {
        lines.push(t("slash.sessions", info.conversations, info.max_conversations));
      }
      lines.push(t("slash.noSessionHint"));
    } else {
      if (info.mode) setModeBadge(info.mode);
      lines.push(t("slash.model", info.model || t("slash.modelUnknown")));
      lines.push(t("slash.mode", info.mode));
      lines.push(t("slash.turnsMessages", info.turns ?? "—", info.messages ?? "—"));
      if (info.context) {
        const c = info.context;
        lines.push(t("slash.contextLine", c.estimated_tokens, c.max_tokens, c.usage_percent));
      } else if (cold) {
        lines.push(t("slash.coldHint"));
      }
    }
    return noteLines(lines);
  }
  // context / skills / tools 是 agent 级事实：无会话 → 提示开会话；冷会话 → 提示续聊恢复。
  if (appLevel) {
    return noteLines([t("slash.needSession", cmd)]);
  }
  if (cold) {
    return noteLines([t("slash.coldNeedResume", cmd)]);
  }
  if (cmd === "context") return renderSlashContext(info.context);
  if (cmd === "skills") return renderSlashSkills(info.skills);
  if (cmd === "tools") return renderSlashTools(info.tools);
}

function renderSlashContext(c) {
  if (!c) return noteLines([t("slash.noContext")]);
  const bd = c.token_breakdown || {};
  return noteLines([
    t("slash.ctxEstimate", c.estimated_tokens, c.max_tokens, c.usage_percent),
    t("slash.ctxBreakdown", bd.system ?? 0, bd.messages ?? 0, bd.tools ?? 0, bd.total ?? 0),
  ]);
}

function renderSlashSkills(skills) {
  if (!skills) return noteLines([t("slash.noSkills")]);
  return addNote((div) => {
    const head = document.createElement("div");
    head.textContent = t("slash.skillsHead", skills.active.length, skills.available.length);
    div.appendChild(head);
    for (const s of skills.available) {
      const row = document.createElement("div");
      row.className = "slash-skill" + (s.active ? " active" : "");
      // 名/描述是后端内容、按字面显示；● / ○ 是标记。
      row.textContent = `${s.active ? "● " : "○ "}${s.name}${s.description ? " — " + s.description : ""}`;
      div.appendChild(row);
    }
  });
}

function renderSlashTools(tools) {
  if (!tools) return noteLines([t("slash.noTools")]);
  return addNote((div) => {
    const head = document.createElement("div");
    head.textContent = t("slash.toolsHead", tools.length);
    div.appendChild(head);
    for (const tool of tools) {
      const row = document.createElement("div");
      // blocked ∈ {true,false,"unknown"}：true=只读禁（灰显）、unknown=无法判定（标注）、false=可用。
      row.className = "slash-tool" + (tool.blocked === true ? " blocked" : "");
      let tag = "";
      if (tool.blocked === true) tag = t("slash.toolBlocked");
      else if (tool.blocked === "unknown") tag = t("slash.toolUnknown");
      row.textContent = `${tool.name}${tag ? " " + tag : ""}${tool.description ? " — " + tool.description : ""}`;
      div.appendChild(row);
    }
  });
}

// 发送/停止双态切换：流式中把 #chat-send 文案切「停止」、加 .stopping 样式；data-mode 供点击
// 处理分流。复位时清禁用，让按钮重新可点。
function setChatSending(sending) {
  const btn = $("#chat-send");
  chatStreaming = sending;
  if (!sending) pendingStop = false; // 流结束：清掉未消费的待停标志（防跨轮残留）
  btn.dataset.mode = sending ? "stop" : "send";
  // 发送↑ / 停止■ 的图标切换由 .stopping 类驱动（见 app.css .ico-send/.ico-stop），不改 innerHTML——
  // 否则会抹掉按钮内的 <svg> 图标。
  btn.setAttribute("aria-label", sending ? t("chat.stop") : t("chat.send"));
  btn.title = sending ? t("tip.stop") : t("tip.send");
  btn.classList.toggle("stopping", sending);
  btn.disabled = false;
  $("#attach-btn").disabled = sending; // 流式中禁上传（避免在飞轮里改附件）
  if (sending) refreshStagingIfOpen(); // 可写 turn 起跑：暂存区开着则重渲染、置灰宿主写动作（§7.3）
}

// 停止：POST /api/chat/{id}/stop 置位服务端取消令牌。不在此复位按钮——等流尾的 stopped/done
// 帧由 sendChat 的 finally 统一复位（避免与在飞流抢状态）。停止请求飞行中先禁用防重复点。
async function stopChat() {
  const btn = $("#chat-send");
  btn.disabled = true; // 停止请求飞行中先禁用防重复点（图标保持 ■，禁用态即反馈）
  // 首轮 id 由 start 帧回填，可能尚未到：标记待停，start 处理处一拿到 id 就补发本请求，
  // 而不是静默放弃（否则用户点了停止却毫无反应）。按钮已显示「停止中…」给出反馈。
  if (!conversationId) { pendingStop = true; return; }
  try {
    await fetch(`/api/chat/${encodeURIComponent(conversationId)}/stop`, { method: "POST" });
  } catch (_e) {
    // 停止请求本身失败不致命：当前轮仍会自然跑完，按钮由流尾复位。
  }
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  submitChat();
});

// 「停止」态下点按钮 = 中断当前轮（而非发新轮）：拦掉默认 submit、直接 stopChat。
$("#chat-send").addEventListener("click", (e) => {
  if ($("#chat-send").dataset.mode === "stop") {
    e.preventDefault();
    stopChat();
  }
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
    autoGrowInput(); // 换行后随内容增高
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

// 复制纯文本到剪贴板：优先 Clipboard API（http://127.0.0.1 浏览器视作安全上下文、可用），
// 失败回退隐藏 <textarea> + execCommand('copy')（老接口/非安全上下文兜底）。返回是否成功。
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* 落到下方 legacy 兜底 */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus(); // 部分浏览器 execCommand('copy') 要求选区元素先获焦（fixed 离屏，不滚动文档）
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

// 给输出气泡右下角挂「复制原始 Markdown」图标钮（决策：复制**源**而非渲染后文本——markdown 是唯一事实来源）。
// raw = 该轮答案的 markdown 源（done 用 payload.answer、历史用 m.content）。幂等；空源不挂。复制成功短暂回显「已复制」。
function appendCopyButton(botEl, raw) {
  if (!botEl || !raw) return;
  if (botEl.querySelector(".bubble-copy")) return; // 幂等：done 后又触发不重复挂
  botEl.classList.add("has-copy"); // 预留右下角空间，绝对定位的钮不压正文（见 app.css）
  const btn = document.createElement("button");
  btn.className = "bubble-copy";
  btn.type = "button";
  btn.title = t("chat.copy");
  btn.setAttribute("aria-label", t("chat.copy"));
  btn.innerHTML = `<svg class="ico"><use href="#i-copy"/></svg>`;
  btn.addEventListener("click", async () => {
    const ok = await copyText(raw);
    const use = btn.querySelector("use");
    btn.classList.add(ok ? "copied" : "copy-fail");
    if (use) use.setAttribute("href", ok ? "#i-check" : "#i-copy"); // 成功短暂换勾
    btn.title = ok ? t("chat.copied") : t("chat.copyFail"); // 字面 key（i18n linter 决策P4.7-8）
    clearTimeout(btn._t);
    btn._t = setTimeout(() => {
      btn.classList.remove("copied", "copy-fail");
      if (use) use.setAttribute("href", "#i-copy");
      btn.title = t("chat.copy");
    }, 1400);
  });
  botEl.appendChild(btn);
}

// 心跳指示器：作为气泡的**兄弟**节点（非子节点）插在其后——否则 token 的
// `botEl.textContent +=` 会把它的文本一并吞进答案、done 的 innerHTML 重渲也会冲突。
// 一来 token / 收尾即清；下一段静默间隙再触发会重建。
function clearHeartbeat(botEl) {
  if (botEl && botEl._hb) {
    botEl._hb.remove();
    botEl._hb = null;
  }
}

// 对话气泡里的 [[wikilink]]（来自渲染后的答案）点了切到右栏 wiki 单页。
$("#chat-log").addEventListener("click", (e) => {
  const a = e.target.closest("a.wikilink[data-page]");
  if (a) { e.preventDefault(); navigate({ kind: "page", path: a.dataset.page }); }
});

async function sendChat(message, attachments) {
  if (message) addMsg("user", message);
  if (attachments && attachments.length) addUserAttachments(attachments); // 用户气泡下回显附件徽章
  const botEl = addMsg("bot", "");
  botEl.dataset.question = message || ""; // 记住该轮用户问题，供气泡「沉淀」按钮预填（P4.8）
  setChatSending(true); // 按钮切「停止」、置 chatStreaming，整轮可中断
  try {
    const body = { message, conversation_id: conversationId };
    if (attachments && attachments.length) body.attachments = attachments.map((a) => a.rel);
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
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
    clearHeartbeat(botEl);
    botEl.classList.replace("bot", "err");
    botEl.textContent = t("chat.fail", e.message);
  } finally {
    clearHeartbeat(botEl); // 兜底：任何退出路径都不留下「处理中」残影
    setChatSending(false); // 复位：按钮切回「发送」、清 chatStreaming/禁用
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
  if (event === "start") {
    // 服务端尽早回填会话 id：首轮请求里 id 为 null，拿到它「停止」按钮才能在首轮就生效。
    if (payload.conversation_id) setConversation(payload.conversation_id); // 连带写进 ?c=（刷新可恢复）
    if (pendingStop) { pendingStop = false; stopChat(); } // 首轮 id 未回时点过停止 → 现在补发
  } else if (event === "token") {
    clearHeartbeat(botEl); // 有内容流出了，撤掉「处理中」
    botEl.textContent += payload;
    log.scrollTop = log.scrollHeight;
  } else if (event === "heartbeat") {
    // 静默间隙（长工具调用 / 首 token 前思考）的存活提示，随 elapsed 刷新；token 一来即清。
    if (!botEl._hb) {
      botEl._hb = document.createElement("div");
      botEl._hb.className = "chat-hb";
      botEl.after(botEl._hb);
    }
    botEl._hb.textContent = t("chat.working", payload.elapsed || 0);
    log.scrollTop = log.scrollHeight;
  } else if (event === "stopped") {
    clearHeartbeat(botEl);
    // 用户主动停止：保留已流出的纯文本（不再渲染 markdown），加一行轻提示。
    if (payload.conversation_id) setConversation(payload.conversation_id);
    const note = document.createElement("span");
    note.className = "stop-note";
    note.textContent = t("chat.stopped");
    botEl.appendChild(note);
    renderWritableReceipts(payload); // 可写 turn 被停也可能已写盘：照常出 check/撤销/告警
    appendBackfillButton(botEl); // 气泡尾部挂「沉淀」按钮：预填该轮问题（P4.8）
    refreshStagingIfOpen(); // 修订 turn 收尾：暂存区开着则重拉刷新池（决策P4.6-9）
    log.scrollTop = log.scrollHeight;
  } else if (event === "done") {
    clearHeartbeat(botEl);
    setConversation(payload.conversation_id);
    // 收尾：用服务端渲染的安全 markdown HTML 替换流式纯文本（含 [[页]]→站内链接）。
    if (payload.answer_html !== undefined) {
      botEl.classList.add("rendered"); // 切到正常空白模型 + 富排版（见 app.css）
      botEl.innerHTML = payload.answer_html;
      enhanceContent(botEl); // 流式收尾：富渲染答案 ```mermaid→图 / ```X→高亮 / $…$→公式（决策P4.14-9，泛化 P4.13-8）
    } else if (payload.answer) {
      botEl.textContent = payload.answer;
    }
    appendCopyButton(botEl, payload.answer); // 右下角「复制原始 Markdown」：复制答案源（非渲染后文本）
    renderWritableReceipts(payload); // 可写 turn 收尾：check 回执 / 撤销 / immutable 告警
    appendBackfillButton(botEl); // 气泡尾部挂「沉淀」按钮：预填该轮问题（P4.8）
    refreshStagingIfOpen(); // 修订 turn 收尾：暂存区开着则重拉刷新池（决策P4.6-9）
    log.scrollTop = log.scrollHeight;
  } else if (event === "confirm_request") {
    // P4.15a：Agent 的 ASK 决策弹给人——审阅命令全文，点 允许/本会话起自动放行/拒绝（docs/P4.15）。
    clearHeartbeat(botEl); // 不是「思考中」，是「等你拍板」
    renderConfirmRequest(payload);
  } else if (event === "ask_request") {
    // P4.15b：Agent 主动提问，弹一帧（可带选项/自由文本），收回答案串。
    clearHeartbeat(botEl);
    renderAskRequest(payload);
  } else if (event === "interaction_resolved") {
    // 确认/提问落定（人点了 / 超时 / 停止）：收掉气泡、标记结局。
    resolveInteractionUI(payload);
  } else if (event === "error") {
    clearHeartbeat(botEl);
    // 记下服务端已建会话（即便本轮失败）：否则首轮失败时下次又以 null 另起新会话，堆到 503。
    if (payload.conversation_id) setConversation(payload.conversation_id);
    botEl.classList.replace("bot", "err");
    botEl.textContent = t("chat.error", payload.message);
    renderWritableReceipts(payload); // 可写 turn 抛错前的写已被服务端收尾捕获
  }
}

// 可写 turn 收尾的三类回执，**各看各的字段、相互独立**（P4.5 §8 评审 Medium）：
//   · 撤销键看 undo.available（写日志非空，独立于 check）——SCHEMA-only / check 通过的 wiki 改都能撤销
//   · 修复键看 check.violations
//   · 告警看 immutable_mutated（AGENTAO.md 被旁路改、已自动还原）
function renderWritableReceipts(payload) {
  const { check, undo, immutable_mutated: mutated } = payload || {};
  if (!check && !undo && !mutated) return; // read-only turn：无这些字段
  let refreshed = false;
  // ① immutable 告警（最严重，置顶）：AGENTAO.md 被旁路改、已自动还原。
  if (Array.isArray(mutated) && mutated.length) {
    addNote((div) => {
      div.classList.add("receipt-alert");
      const p = document.createElement("div");
      p.textContent = t("receipt.mutated", mutated.join("、"));
      div.appendChild(p);
    });
  }
  // ② check 回执 + 「让 Agent 修复」。口径＝**本轮新增**（决策P4.5-4 修订）：ok = 本轮未新增、
  // violations 只装新增；库存量（total）只作旁注（拼进文案的全是数字，无注入面）。
  // **本轮无新增（check.ok）时不出回执**（用户要求）——只静默刷新右栏页面列表，不刷屏。
  if (check) {
    const legacyNote = (n) => (n > 0 ? t("receipt.legacy", n) : "");
    if (check.ok) {
      loadPages(); refreshed = true; // 写后静默刷新右栏 wiki 列表（无回执）
    } else {
      addNote((div) => {
        div.classList.add("receipt-bad");
        const head = document.createElement("div");
        head.innerHTML =
          `<span class="report-bad">${escapeHtml(t("receipt.checkBad", check.violations.length))}</span>` +
          escapeHtml(legacyNote(check.total - check.violations.length));
        div.appendChild(head);
        for (const v of check.violations.slice(0, 20)) {
          const li = document.createElement("div");
          li.className = "violation";
          li.textContent = `· [${v.kind}] ${v.page}: ${v.detail}`;
          div.appendChild(li);
        }
        if (check.repair_prompt) {
          const btn = document.createElement("button");
          btn.className = "receipt-btn";
          btn.textContent = t("receipt.repair");
          btn.addEventListener("click", () => {
            btn.disabled = true;
            submitChat(check.repair_prompt); // 以 REPAIR_PROMPT 作下一轮消息驱动修复
          });
          div.appendChild(btn);
        }
      });
    }
  }
  // ③ 撤销键（看 undo.available、不挂 check.violations 分支，评审 Medium）。
  if (undo && undo.available) {
    addNote((div) => {
      const p = document.createElement("div");
      p.textContent = t("receipt.undoInfo", undo.paths.length, undo.paths.join("、"));
      div.appendChild(p);
      const btn = document.createElement("button");
      btn.className = "receipt-btn";
      btn.textContent = t("receipt.undo");
      btn.addEventListener("click", () => undoTurn(undo.token, btn, div));
      div.appendChild(btn);
    });
  }
  if (refreshed === false && undo && undo.available) loadPages(); // 撤销可用＝有写，刷新列表
}

// 撤销本轮写（P4.5 决策P4.5-13）：POST /api/chat/{id}/undo。409＝某文件已被后续写改动（标红）。
async function undoTurn(token, btn, box) {
  btn.disabled = true;
  if (!conversationId) { box.classList.add("receipt-bad"); return; }
  let res;
  try {
    res = await fetch(`/api/chat/${encodeURIComponent(conversationId)}/undo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch (e) {
    box.classList.add("receipt-bad");
    const p = document.createElement("div");
    p.textContent = t("undo.fail", e.message);
    box.appendChild(p);
    return;
  }
  let data = {};
  try { data = await res.json(); } catch { /* 空体 */ }
  const note = document.createElement("div");
  if (res.status === 409 && data.conflicts) {
    box.classList.add("receipt-bad");
    note.textContent = t("undo.partial", (data.conflicts || []).join("、"), (data.undone || []).join("、") || t("undo.none"));
  } else if (res.status === 409) {
    box.classList.add("receipt-bad");
    note.textContent = t("undo.stale");
  } else if (res.ok) {
    note.textContent = t("undo.done", (data.undone || []).join("、") || t("undo.none"));
  } else {
    box.classList.add("receipt-bad");
    note.textContent = t("undo.failHttp", res.status);
  }
  box.appendChild(note);
  loadPages(); // 撤销后刷新右栏 wiki 列表
}

// ── P4.15 工具确认 / ask_user 人在环（docs/P4.15）─────────────────────────────
// 服务端 SSE 推 confirm_request / ask_request → 渲染一个交互气泡（命令/问题用 textContent 字面
// 显示、绝不渲染或执行）；用户点选 → POST /confirm·/answer；interaction_resolved → 收掉气泡。
// 气泡端绝不取任何锁（端点瞬返），等待期间内层 turn 在服务端阻塞、持写锁（§4）。

const pendingInteractions = {}; // interaction_id -> { el }（供 interaction_resolved 找回气泡收尾）

// 切换/新建会话时调：停掉所有未决气泡的倒计时 setInterval 并清空登记表——否则切走后 interval
// 仍每秒在已脱离 DOM 的节点上空转、map 也越积越多（评审泄漏修复）。气泡 DOM 由调用方清 log 清掉。
function clearPendingInteractions() {
  for (const id of Object.keys(pendingInteractions)) {
    stopCountdown(pendingInteractions[id].el);
    delete pendingInteractions[id];
  }
}

// 交互气泡的 POST 归口：成功 onOk；409（陈旧/已超时/已停止/打错端点）或网络失败 → 标记气泡失效。
function interactionPost(path, body, box, onOk) {
  if (!conversationId) { markInteractionStale(box, 0); return; }
  return fetch(`/api/chat/${encodeURIComponent(conversationId)}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
    .then((res) => {
      if (res.ok) { if (onOk) onOk(); }
      else markInteractionStale(box, res.status);
      return res;
    })
    .catch(() => markInteractionStale(box, 0));
}

function disableInteraction(div) {
  for (const b of div.querySelectorAll("button")) b.disabled = true;
  for (const inp of div.querySelectorAll("input")) inp.disabled = true;
}

function markInteractionStale(div, status) {
  disableInteraction(div);
  const note = document.createElement("div");
  note.className = "interaction-status stale";
  note.textContent = status === 409 ? t("interaction.expired") : t("interaction.failHttp", status || 0);
  div.appendChild(note);
}

// 倒计时（纯前端提示，到点服务端会自送 interaction_resolved{timeout}）。deadline_epoch 是墙钟秒。
function startCountdown(el, deadlineEpoch) {
  if (!deadlineEpoch) return;
  const tick = () => {
    const remain = Math.ceil((deadlineEpoch * 1000 - Date.now()) / 1000);
    el.textContent = t("interaction.countdown", Math.max(0, remain));
    if (remain <= 0 && el._cd) { clearInterval(el._cd); el._cd = null; }
  };
  tick();
  el._cd = setInterval(tick, 1000);
}

function stopCountdown(div) {
  const cd = div.querySelector(".interaction-cd");
  if (cd && cd._cd) { clearInterval(cd._cd); cd._cd = null; }
}

// 新建一个交互气泡骨架（head + 倒计时槽），登记进 pendingInteractions。
function newInteractionBubble(p, cls, title) {
  const div = addNote(() => {});
  div.classList.add("interaction", cls);
  div.dataset.iid = p.interaction_id;
  const head = document.createElement("div");
  head.className = "interaction-head";
  head.textContent = title;
  div.appendChild(head);
  pendingInteractions[p.interaction_id] = { el: div };
  return div;
}

function renderConfirmRequest(p) {
  const div = newInteractionBubble(p, "confirm", t("interaction.confirmTitle"));
  const toolLine = document.createElement("div");
  toolLine.className = "interaction-tool";
  toolLine.textContent = t("interaction.tool", p.tool || "");
  div.appendChild(toolLine);
  // 命令/参数全文，字面显示（不渲染、不执行）——这是「人在环」相对「静默放行」的全部信息增益。
  const cmd = p.args && p.args.command !== undefined ? p.args.command : p.args;
  const pre = document.createElement("pre");
  pre.className = "interaction-cmd";
  pre.textContent = typeof cmd === "string" ? cmd : JSON.stringify(cmd, null, 2);
  div.appendChild(pre);
  const cd = document.createElement("div");
  cd.className = "interaction-cd";
  div.appendChild(cd);
  startCountdown(cd, p.deadline_epoch);
  const actions = document.createElement("div");
  actions.className = "interaction-actions";
  const mk = (label, btnCls, decision) => {
    const b = document.createElement("button");
    b.className = "interaction-btn " + btnCls;
    b.textContent = label;
    b.addEventListener("click", () => {
      disableInteraction(div);
      interactionPost("/confirm", { interaction_id: p.interaction_id, decision }, div);
    });
    return b;
  };
  actions.append(
    mk(t("interaction.allow"), "allow", "allow"),
    mk(t("interaction.allowSession"), "allow-session", "allow_session"),
    mk(t("interaction.deny"), "deny", "deny"),
  );
  div.appendChild(actions);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
}

function renderAskRequest(p) {
  const div = newInteractionBubble(p, "ask", t("interaction.askTitle"));
  const q = document.createElement("div");
  q.className = "interaction-q";
  q.textContent = p.question || ""; // 问题是内容，字面显示
  div.appendChild(q);
  const cd = document.createElement("div");
  cd.className = "interaction-cd";
  div.appendChild(cd);
  startCountdown(cd, p.deadline_epoch);
  const opts = Array.isArray(p.options) ? p.options : [];
  const inputs = [];
  if (opts.length) {
    const inputType = p.multiple ? "checkbox" : "radio";
    const groupName = "ask-" + p.interaction_id;
    for (const opt of opts) {
      const lab = document.createElement("label");
      lab.className = "interaction-opt";
      const inp = document.createElement("input");
      inp.type = inputType;
      inp.name = groupName;
      inp.value = opt;
      const span = document.createElement("span");
      span.textContent = opt; // 选项是内容，字面显示
      lab.append(inp, span);
      div.appendChild(lab);
      inputs.push(inp);
    }
  }
  // 无选项、或允许自由文本时给输入框（答案最终是单串，base.py 契约）。
  let freeInput = null;
  if (!opts.length || p.allow_custom) {
    freeInput = document.createElement("input");
    freeInput.type = "text";
    freeInput.className = "interaction-free";
    freeInput.placeholder = t("interaction.answerPlaceholder");
    div.appendChild(freeInput);
  }
  const actions = document.createElement("div");
  actions.className = "interaction-actions";
  const submit = document.createElement("button");
  submit.className = "interaction-btn allow";
  submit.textContent = t("interaction.submit");
  submit.addEventListener("click", () => {
    const chosen = inputs.filter((inp) => inp.checked).map((inp) => inp.value);
    let answer = chosen.join(", ");
    if (freeInput && freeInput.value.trim()) {
      answer = answer ? answer + ", " + freeInput.value.trim() : freeInput.value.trim();
    }
    if (!answer) return; // 空答不提交
    disableInteraction(div);
    interactionPost("/answer", { interaction_id: p.interaction_id, answer }, div);
  });
  actions.appendChild(submit);
  div.appendChild(actions);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
}

function resolveInteractionUI(p) {
  const rec = pendingInteractions[p.interaction_id];
  if (!rec) return;
  delete pendingInteractions[p.interaction_id];
  finalizeInteraction(rec.el, p.decision);
}

function finalizeInteraction(div, decision) {
  stopCountdown(div);
  disableInteraction(div);
  const status = document.createElement("div");
  status.className = "interaction-status";
  const labels = {
    allow: t("interaction.allowed"),
    allow_session: t("interaction.allowedSession"),
    deny: t("interaction.denied"),
    timeout: t("interaction.timedOut"),
    stopped: t("interaction.stopped"),
    answered: t("interaction.answeredOk"),
  };
  status.textContent = labels[decision] || decision;
  div.appendChild(status);
  if (decision === "allow_session") showAutoModeNote(); // 翻 auto：给「恢复逐次确认」一键
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
}

// 本会话起自动放行后的提示 + 「恢复逐次确认」（/confirm-mode {ask}）。镜像 CLI 第 2 项的安全版：
// 仅松「问不问」、姿态仍 workspace-write、层①②③ 不动、可逆（§6）。
function showAutoModeNote() {
  addNote((div) => {
    div.classList.add("interaction-automode");
    const p = document.createElement("div");
    p.textContent = t("interaction.autoNote");
    div.appendChild(p);
    const b = document.createElement("button");
    b.className = "interaction-btn restore";
    b.textContent = t("interaction.restoreConfirm");
    b.addEventListener("click", () => {
      b.disabled = true;
      interactionPost("/confirm-mode", { confirm_mode: "ask" }, div, () => {
        const done = document.createElement("div");
        done.textContent = t("interaction.restored");
        div.appendChild(done);
      });
    });
    div.appendChild(b);
  });
}

// 断线重连重渲染（§5.3）：切到/恢复会话时若服务端报有未决 pending，按 envelope 重建气泡让用户决策。
function rerenderPendingInteractions(pending) {
  if (!Array.isArray(pending)) return;
  for (const p of pending) {
    if (p.kind === "confirm") renderConfirmRequest(p);
    else if (p.kind === "ask") renderAskRequest(p);
  }
}
