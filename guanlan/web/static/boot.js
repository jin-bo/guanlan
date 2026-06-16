"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 分栏拖动 + 启动协调（最后载入：此时所有声明就绪）。
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
// 界面语言（P4.7）：用「最后一次设置」初始化（首访默认 zh），刷静态 chrome + 动态面；按钮翻转 zh⇄en。
// 须在此（模块级 let 已初始化后）调用——rerenderDynamic 会读 currentMode/chatStreaming（决策P4.7-5/6）。
$("#lang-btn").addEventListener("click", toggleLang);
initLang();

// 先拉页面再进"列表"视图（首页）——启动即显示 Concept 列表。
// 仅当用户尚未导航时才进首页：否则页面慢加载期间用户已输入的搜索会被这次空首页覆盖
// （loadPages 解析后会就地重渲染当前 index 条目，故用户的搜索结果照常出现）。
loadPages().then(() => { if (histPos < 0) navigate({ kind: "index", query: "" }); });

// 启动协调（P4.9）：先拉一次 /api/info 取进程默认姿态 + reader 标志，**再**按模式恢复 ?c= 会话——
// 二者本是两段独立异步、会赛跑；合一后 reader 标志在恢复策略选择前就绪（reader 走按-id 探针、不枚举）。
(async function bootstrapSession() {
  // 拉进程默认姿态设初始徽标（P4.5）+ reader 标志（P4.9）：失败则保留 HTML/CSS 默认（非 reader / read-only）。
  let info = null;
  try { info = await getJSON("/api/info"); } catch { /* 失败：保留默认 */ }
  if (info) {
    readerMode = !!info.reader;
    if (info.mode) {
      defaultMode = info.mode;
      // 仅当尚无活动会话时才据进程默认置徽标：?c= 恢复会设该会话真实姿态（restoreConversation 的
      // 每会话 /info 校正），此处进程默认晚到一拍会覆盖回去——正是评审指出的「徽标说谎」竞态。
      if (conversationId === null) setModeBadge(info.mode);
    }
    if (readerMode) applyReaderMode(); // 隐藏写/历史/维护 chrome（决策P4.9-9/10/11/17）
  }

  // 按 ?c=<id> 懒恢复会话（刷新保留当前会话）：仅当该 id 仍可达才恢复，否则抹掉 ?c= 另起新会话——
  // 避免握着一个已删 / 进程重启后不存在的 id（下条提问会撞 404 未知会话）。
  const want = new URL(window.location.href).searchParams.get("c");
  if (!want) return;
  if (readerMode) {
    // reader 关了枚举（决策P4.9-3）：改走**按-id 探针** GET /api/chat/{id}/info（决策P4.9-12），
    // **绝不**调 /api/conversations（reader 下 404）。命中→恢复、404→抹掉 ?c= 干净起手。
    let hitInfo = null;
    try { hitInfo = await getJSON(`/api/chat/${encodeURIComponent(want)}/info`); }
    catch { /* 404/网络失败 → 下面据 hitInfo===null 抹掉 ?c= */ }
    // 拉取期间用户可能已自行开聊（SSE start 已置 conversationId / 正在流式）——绝不覆盖其会话或 URL。
    if (conversationId !== null || chatStreaming) return;
    if (hitInfo) await restoreConversation({ id: want, title: hitInfo.title });
    else setConversation(null);
    return;
  }
  // 非 reader：沿用枚举恢复（不回归既有行为）。
  let conversations = null;
  try {
    ({ conversations } = await getJSON("/api/conversations"));
  } catch { /* 列举失败：退回干净起手（除非用户已自行开聊，见下） */ }
  if (conversationId !== null || chatStreaming) return;
  const hit = conversations && conversations.find((c) => c.id === want);
  if (hit) { await restoreConversation(hit); return; }
  setConversation(null); // 失效 / 未知 id / 列举失败 → 抹掉 ?c=，干净起手
})();
