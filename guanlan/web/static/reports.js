"use strict";
// 由原 app.js 按关注点拆分（经典脚本，共享全局作用域；非 ES module）。
// 载入顺序见 index.html；boot.js 最后载入。 零-LLM 报告（check/health/lint）+ graph 入口。
// ── 零 LLM 报告 / graph ─────────────────────────────────────────────────────

function renderReport(name, data) {
  const itemsKey = "violations" in data ? "violations" : "findings";
  const items = data[itemsKey] || [];
  const word = itemsKey === "violations" ? t("report.violation") : t("report.finding");
  const head = data.ok
    ? `<p class="report-ok">${escapeHtml(t("report.ok", name, data.pages_checked, word))}</p>`
    : `<p class="report-bad">${escapeHtml(t("report.bad", name, data.pages_checked, items.length))}</p>`;
  const body = items.map((it) =>
    `<div class="finding"><span class="kind">[${escapeHtml(it.kind)}]</span> ${escapeHtml(it.page || t("report.global"))}: ${escapeHtml(it.detail)}</div>`
  ).join("");
  return head + body;
}

document.querySelectorAll(".actions button[data-report]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const name = btn.dataset.report; // 命令名（check/health/lint）：标题用双语 key overlay.<name>，报告正文仍用命令名
    showOverlay("overlay." + name, `<p class="muted">${escapeHtml(t("common.running"))}</p>`);
    try {
      const data = await getJSON(`/api/report/${name}`);
      $("#overlay-body").innerHTML = renderReport(name, data);
      overlayRepaint = () => { $("#overlay-body").innerHTML = renderReport(name, data); }; // 语言切换纯重渲染（吃缓存 data）
    } catch (e) {
      $("#overlay-body").innerHTML = `<p class="report-bad">${escapeHtml(t("common.fail", e.message))}</p>`;
    }
  });
});

$("#graph-btn").addEventListener("click", () => window.open("/graph", "_blank"));
