#!/usr/bin/env python3
"""P4.20 真浏览器端到端冒烟（headless Chromium / Playwright，docs/P4.20-Web-flint图表渲染.md §7）。

`test_web.py` 的 ASGI `TestClient` 只能验**服务端不变量 + 前端接线存在性**，验不了真的编译渲染 /
安全闸 / 资源闸 / 实例生命周期（同 P4.13/P4.14：渲染走真浏览器冒烟）。本脚本起一个**真 socket** 的
只读 Web 宿主 + 一个预置知识库，用 Playwright 驱动真实的 `enhanceContent` 链路。

**不打真实 LLM**（本相位是纯前端 + skill，压根不经 Agent）。两处用 `page.route` 打桩 vendored 资产，
以模拟「上游版本漂移产出危险 option」与「渲染期抛错」——**生产代码里不留任何测试钩子**。

**不入 pytest**（需浏览器 + 真 socket，CI 装不全）。手动跑：
    uv run --extra web python scripts/smoke_p420.py
首跑前装浏览器：`uv run python -m playwright install chromium`。
全过 → 退出码 0；任一断言失败 → 1；缺 playwright/chromium/web extra → 2（跳过、非失败）。
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

# ── 环境探测：缺 web extra / playwright / chromium 一律「跳过」（退出码 2），非失败 ──
try:
    import uvicorn

    from guanlan.web.app import create_app
except ImportError as exc:  # noqa: BLE001
    print(f"[skip] 缺依赖（需 guanlan-wiki[web]）：{exc}")
    sys.exit(2)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[skip] 未装 playwright：`uv run python -m pip install playwright` 后 "
          "`uv run python -m playwright install chromium`")
    sys.exit(2)


# ───────────────────────────── 预置素材 ─────────────────────────────

def _json(spec: dict | str) -> str:
    """一份规格的裸 JSON 文本——**注入路径**用这个：它直接进 code.textContent，等价于浏览器
    对服务端转义 HTML 反转义后前端读到的东西（喂 JSON.parse 的是 JSON、不是围栏块）。"""
    return spec if isinstance(spec, str) else json.dumps(spec, ensure_ascii=False)


def _block(spec: dict | str) -> str:
    """把一份规格包成 ```flint 围栏块——**落盘成 wiki 页**用这个（数据整体内联，决策P4.20-12）。"""
    return f"```flint\n{_json(spec)}\n```\n"


BAR_OK = {
    "data": {"values": [{"模型": "model-a", "评测得分": 72.4}, {"模型": "model-b", "评测得分": 81.9},
                        {"模型": "model-c", "评测得分": 88.3}]},
    "semantic_types": {"模型": "Category", "评测得分": "Score"},
    "chart_spec": {"chartType": "Bar Chart",
                   "encodings": {"x": {"field": "模型"}, "y": {"field": "评测得分"}},
                   "baseSize": {"width": 420, "height": 260}},
}
# 注入载荷当类别名（§7-3）：期望落成**转义文本**，不成活元素。
BAR_XSS = {
    "data": {"values": [{"类别": "<img src=x onerror=alert(1)>", "数值": 1},
                        {"类别": "<script>alert(2)</script>", "数值": 2}]},
    "chart_spec": {"chartType": "Bar Chart",
                   "encodings": {"x": {"field": "类别"}, "y": {"field": "数值"}}},
}

PAGES = {
    # 场景 1/2/11：一张正常图
    "wiki/concepts/Chart.md": "评测对比：\n\n" + _block(BAR_OK),
    # 场景 3：注入载荷
    "wiki/concepts/ChartXss.md": "注入用例：\n\n" + _block(BAR_XSS),
    # 场景 9：不含图的页（懒加载对照）
    "wiki/concepts/Plain.md": "这一页没有任何图表块。\n",
}


def _make_kb(root: Path) -> Path:
    """最小合法知识库 + 预置若干含 flint 块的 wiki 页。"""
    (root / "AGENTAO.md").write_text("# AGENTAO\n", encoding="utf-8")
    (root / "SCHEMA.md").write_text("# SCHEMA\n", encoding="utf-8")
    (root / "raw").mkdir()
    wiki = root / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    for rel, body in PAGES.items():
        title = Path(rel).stem
        (root / rel).write_text(
            f"---\ntype: concept\ntitle: {title}\nlast_updated: 2026-08-03\n---\n\n# {title}\n\n{body}",
            encoding="utf-8")
    return root


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app, port: int) -> uvicorn.Server:
    """后台线程跑 uvicorn（真 socket），返回 Server 句柄供收尾 should_exit。"""
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("uvicorn 未在 10s 内就绪")


# 把若干 flint 块塞进 #wiki-view 并跑一次**真的** enhanceContent（编排器，不是私开后门）。
# textContent 赋值等价于服务端转义 + 浏览器反转义后前端读到的东西。
_INJECT = """
([specs]) => {
  const view = document.querySelector("#wiki-view");
  view.innerHTML = specs.map(() => '<pre><code class="language-flint"></code></pre>').join("");
  const codes = view.querySelectorAll("code.language-flint");
  specs.forEach((s, i) => { codes[i].textContent = s; });
  enhanceContent(view);
}
"""


class Smoke:
    """每个场景自带断言；失败抛异常由 _run 捕获记录。语言无关地断言 DOM class，不依赖 i18n 文案。"""

    def __init__(self, page, base: str, requests: list[str], dialogs: list[str]) -> None:
        self.page = page
        self.base = base
        self.requests = requests
        self.dialogs = dialogs

    # —— 基础动作 ——
    def open_home(self) -> None:
        self.page.goto(self.base, wait_until="networkidle")
        self.page.wait_for_selector("#wiki-view", timeout=10000)

    def open_page(self, path: str) -> None:
        self.page.evaluate("(p) => navigate({ kind: 'page', path: p })", path)
        self.page.wait_for_function(
            "(p) => document.querySelector('.page-meta') && "
            "document.querySelector('.page-meta').textContent.includes(p)", arg=path, timeout=10000)

    def inject(self, specs: list[str]) -> None:
        self.page.evaluate(_INJECT, [specs])

    def settled(self, n: int, timeout: int = 15000) -> None:
        """等 n 个块各自落定（成图 或 贴徽标）——两者都是终态，计数即处理完毕。"""
        self.page.wait_for_function(
            "(n) => document.querySelectorAll('#wiki-view figure.flint-rendered, "
            "#wiki-view .flint-error').length === n", arg=n, timeout=timeout)

    def counts(self) -> tuple[int, int]:
        return (self.page.locator("#wiki-view figure.flint-rendered").count(),
                self.page.locator("#wiki-view .flint-error").count())

    def badge_kind(self) -> str:
        """徽标文案 → 'tooLarge' / 'truncated' / 'renderFail'（三者必须可分辨，修法完全不同）。"""
        txt = self.page.locator("#wiki-view .flint-error").first.inner_text()
        low = txt.lower()
        if "截断" in txt or "truncated" in low:
            return "truncated"
        if "过大" in txt or "too large" in low:
            return "tooLarge"
        return "renderFail"

    # —— 场景 ——
    def s1_render(self):
        """1. 真页渲染：wiki 页里的 flint 块成图（figure.flint-rendered > svg），不是 JSON 源码。"""
        self.open_home()
        self.open_page("wiki/concepts/Chart.md")
        self.page.wait_for_selector("#wiki-view figure.flint-rendered svg", timeout=15000)
        assert self.page.locator("#wiki-view pre > code.language-flint").count() == 0, "源码 <pre> 未被替换"
        svg = self.page.locator("#wiki-view figure.flint-rendered svg").first
        assert svg.get_attribute("viewBox"), "SVG 无 viewBox（纯 CSS 缩放的前提，决策P4.20-2）"
        html = self.page.locator("#wiki-view figure.flint-rendered").inner_html()
        assert "#0F4C5C" in html, "昼澜色板未生效（决策P4.20-13）"
        assert "#5470c6" not in html, "残留 flint 内建色板"
        assert "model-a" in html, "轴标签丢失（主题盖掉了 flint 轴配置？）"
        return "成图 + viewBox + 昼澜色板 + 轴标签在"

    def s2_responsive(self):
        """2. 窄栏：**渲染前按栏宽重编译**（决策P4.20-19）→ 图落在栏内，且 flint 烤死的图例仍在视口内。

        图例这条是 F3 的回归守门：早先做法是编译完再改画布尺寸，而 flint 把 legend.left 烤成
        绝对像素（实测 _width=562 时 legend.left=464），缩到 377 就把整个图例画到视口外了——
        图还在、没有徽标，读者却分不清哪条线是哪个模型。
        """
        self.page.set_viewport_size({"width": 520, "height": 800})
        self.open_home()
        self.open_page("wiki/concepts/Chart.md")
        self.page.wait_for_selector("#wiki-view figure.flint-rendered svg", timeout=15000)
        fig_w, svg_w = self.page.evaluate(
            "() => { const f = document.querySelector('#wiki-view figure.flint-rendered');"
            " return [f.clientWidth, f.querySelector('svg').getBoundingClientRect().width]; }")
        assert svg_w <= fig_w + 1, f"窄栏下渲染的 SVG({svg_w}) 仍宽过容器({fig_w})"
        overflow = self.page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, f"页面横向溢出 {overflow}px"
        # 多序列图（有图例）在同样的窄栏下：每个图元都得落在 SVG 视口内。
        self.inject([_json({
            "data": {"values": [{"月": m, "模型": g, "调用": v}
                                for g, vs in (("model-a", [1.2, 1.9, 2.4]), ("model-b", [0.7, 1.1, 1.6]))
                                for m, v in zip(("2026-01", "2026-02", "2026-03"), vs)]},
            "chart_spec": {"chartType": "Line Chart",
                           "encodings": {"x": {"field": "月"}, "y": {"field": "调用"},
                                         "color": {"field": "模型"}}}})])
        self.settled(1)
        assert self.counts() == (1, 0), "多序列折线图未渲染"
        spill = self.page.evaluate("""() => {
          const svg = document.querySelector('#wiki-view figure.flint-rendered svg');
          const box = svg.getBoundingClientRect();
          return [...svg.querySelectorAll('text')]
            .filter(t => t.textContent.trim())
            .filter(t => { const r = t.getBoundingClientRect();
                           return r.right > box.right + 1 || r.left < box.left - 1; })
            .map(t => t.textContent.trim()).slice(0, 5);
        }""")
        assert not spill, f"有图元被画到 SVG 视口之外（图例被裁？）：{spill}"
        legend = self.page.evaluate(
            "() => [...document.querySelectorAll('#wiki-view figure.flint-rendered svg text')]"
            ".map(t => t.textContent.trim())")
        assert "model-a" in legend and "model-b" in legend, f"图例文字缺失：{legend[:8]}"
        # 渲染后再缩窗口：不重排（不引 ResizeObserver），要求图**不额外**撑宽页面——
        # 与同一视口下的**无图页**比基线（该 App 自身在 <430px 就有固有溢出，与本相位无关）。
        self.page.set_viewport_size({"width": 400, "height": 800})
        self.page.wait_for_timeout(300)
        with_chart = self.page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.open_page("wiki/concepts/Plain.md")
        self.page.wait_for_timeout(300)
        baseline = self.page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert with_chart <= baseline, \
            f"图额外撑宽了页面：含图 {with_chart}px vs 无图基线 {baseline}px（overflow-x 未兜住）"
        self.page.set_viewport_size({"width": 1280, "height": 900})
        return (f"窄栏重编译 svg {svg_w:.0f} ≤ figure {fig_w}，图例在视口内；"
                f"事后缩窗口无额外溢出（含图 {with_chart}px = 无图基线 {baseline}px）")

    def s3_injection(self):
        """3. 注入：载荷当类别名 → 不弹窗、DOM 无活元素、payload 只作 <text> 文本。"""
        self.open_home()
        self.open_page("wiki/concepts/ChartXss.md")
        self.page.wait_for_selector("#wiki-view figure.flint-rendered svg", timeout=15000)
        self.page.wait_for_timeout(300)
        assert not self.dialogs, f"弹窗了：{self.dialogs}"
        fig = self.page.locator("#wiki-view figure.flint-rendered")
        for sel in ("img", "script", "[onerror]", "foreignObject", "image"):
            assert fig.locator(sel).count() == 0, f"产物含活元素 {sel}"
        texts = [t or "" for t in fig.locator("text").all_text_contents()]
        assert any("<img" in t for t in texts), f"payload 未以文本落进 <text>：{texts[:6]}"
        return "不弹窗 + 无 img/script/onerror/foreignObject/image + payload 落成文本"

    def s4_tooltip_no_html(self):
        """4. tooltip 不走 HTML 通道（renderMode:'richText'）：hover 不产生含原始 HTML 的容器。"""
        self.open_home()
        self.open_page("wiki/concepts/ChartXss.md")
        self.page.wait_for_selector("#wiki-view figure.flint-rendered svg", timeout=15000)
        before = self.page.evaluate("() => document.querySelectorAll('body > div').length")
        box = self.page.locator("#wiki-view figure.flint-rendered svg").bounding_box()
        self.page.mouse.move(box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.7)
        self.page.wait_for_timeout(600)
        assert not self.dialogs, f"hover 触发弹窗：{self.dialogs}"
        after = self.page.evaluate("() => document.querySelectorAll('body > div').length")
        html = self.page.evaluate("() => document.body.innerHTML")
        assert "<img src=x" not in html, "tooltip 把 payload 当 HTML 拼进了 DOM"
        return f"hover 后 body>div {before}→{after}，无 HTML tooltip、无弹窗"

    def s5b_url_not_falsely_rejected(self):
        """5b. 断言不误伤（决策P4.20-4 反向用例）：类别名本身是 URL → **照常渲染**。"""
        self.open_home()
        self.inject([_json({
            "data": {"values": [{"类别": "https://example.com/model-card", "数值": 3},
                                {"类别": "https://arxiv.org/abs/2401.00001", "数值": 5}]},
            "chart_spec": {"chartType": "Bar Chart",
                           "encodings": {"x": {"field": "类别"}, "y": {"field": "数值"}}}})])
        self.settled(1)
        rendered, errors = self.counts()
        assert (rendered, errors) == (1, 0), f"含 URL 数据的正常图被误否（渲染 {rendered} / 徽标 {errors}）"
        texts = [t or "" for t in
                 self.page.locator("#wiki-view figure.flint-rendered text").all_text_contents()]
        assert any("example.com" in t for t in texts), f"URL 未以文本出现：{texts[:6]}"
        return "URL 当类别名照常渲染（断言按键域、不扫值域）"

    def s6_reject_data_url(self):
        """6. 拒 data.url：保留源码 + 徽标，**且全程零外部请求**（决策P4.20-5）。"""
        self.open_home()
        self.inject(['{"data":{"url":"http://example.com/rows.csv"},'
                     '"chart_spec":{"chartType":"Bar Chart","encodings":{"x":{"field":"a"}}}}'])
        self.settled(1)
        rendered, errors = self.counts()
        assert (rendered, errors) == (0, 1), f"data.url 未被拒（渲染 {rendered}）"
        assert self.page.locator("#wiki-view pre > code.language-flint").count() == 1, "源码未保留"
        external = [u for u in self.requests if not u.startswith(self.base.rstrip("/"))]
        assert not external, f"发起了外部请求：{external[:3]}"
        return "拒 data.url + 保留源码 + 徽标 + 零外部请求"

    def s7_resource_gates(self):
        """7. 资源闸：只防「大到打死浏览器」，且三类文案各归各的（决策P4.20-6/-19/-20）。

        ①3000 行 → 「数据过大」；②baseSize 非有限数 → **「渲染失败」**（不是「数据过大」——
        画布是坏的，跟数据量无关，说成过大会把作者引向删数据这条修不好的路）；
        ③baseSize 1e8 → 不再一律拒，而是被**按栏宽重编译**成一张正常的图（尺寸这件事归 flint 管）。
        """
        self.open_home()
        big = {"data": {"values": [{"c": f"c{i}", "v": i} for i in range(3000)]},
               "chart_spec": {"chartType": "Bar Chart",
                              "encodings": {"x": {"field": "c"}, "y": {"field": "v"}}}}
        self.inject([_json(big)])
        self.settled(1)
        assert self.counts() == (0, 1), "3000 行未被资源闸拦"
        assert self.badge_kind() == "tooLarge", "行数超限应为「数据过大」"

        # 非有限尺寸：JSON 写不出 NaN，但 1e400 会被 JSON.parse 解成 Infinity。
        inf = json.loads(json.dumps(BAR_OK))
        inf["chart_spec"]["baseSize"] = "__INF__"
        self.inject([_json(inf).replace('"__INF__"', '{"width":1e400,"height":1e400}')])
        self.settled(1)
        assert self.counts() == (0, 1), "非有限画布尺寸未被拦"
        assert self.badge_kind() == "renderFail", "画布坏掉应为「渲染失败」而非「数据过大」"

        # 反向：一张**小**画布是合法的，不该被拒（决策P4.20-20）。flint 会把 baseSize:{-5,null}
        # 归一成 136x61 —— 旧闸的 minW/minH 把这类图判成「数据过大」，还叫作者去删数据，
        # 而删数据只会让画布更小、永远修不好。Calendar Heatmap 按日期跨度定尺寸时天天撞这条。
        small = json.loads(json.dumps(BAR_OK))
        small["chart_spec"]["baseSize"] = {"width": -5, "height": None}
        self.inject([_json(small)])
        self.settled(1)
        assert self.counts() == (1, 0), "合法的小画布被误拒（尺寸闸不该有下界）"

        huge = json.loads(json.dumps(BAR_OK))
        huge["chart_spec"]["baseSize"] = {"width": 1e8, "height": 1e8}
        self.inject([_json(huge)])
        self.settled(1)
        rendered, errors = self.counts()
        assert (rendered, errors) == (1, 0), f"baseSize 1e8 应被按栏宽重编译成正常图（渲染 {rendered} / 徽标 {errors}）"
        fig_w, svg_w = self.page.evaluate(
            "() => { const f = document.querySelector('#wiki-view figure.flint-rendered');"
            " return [f.clientWidth, f.querySelector('svg').getBoundingClientRect().width]; }")
        assert 0 < svg_w <= fig_w + 1, f"重编译后的尺寸不合理：svg {svg_w} vs figure {fig_w}"
        assert self.page.locator("#wiki-view svg").count() == 1  # 没有巨幅/NaN 画布残留
        return (f"行数超限→数据过大；Infinity 画布→渲染失败；小画布照渲；"
                f"baseSize 1e8→重编译成 {svg_w:.0f}px 的正常图")

    def s8_bad_input_and_empty_chart(self):
        """8/8b. 坏 JSON、列名不存在、空 series、大小写错、非 ECharts 产物 → 均保留源码 + **渲染失败**文案。"""
        cases = [
            ("坏 JSON", '{"data":{'),
            ("列名不存在", _json({"data": {"values": [{"a": 1}]},
                                "chart_spec": {"chartType": "Bar Chart",
                                               "encodings": {"x": {"field": "不存在"}}}})),
            ("Treemap 空 series", _json({"data": {"values": [{"n": "a", "v": 1}, {"n": "b", "v": 2}]},
                                          "chart_spec": {"chartType": "Treemap",
                                                         "encodings": {"detail": {"field": "n"},
                                                                       "size": {"field": "v"}}}})),
            ("chartType 大小写错", _json({"data": {"values": [{"a": "x", "b": 1}]},
                                      "chart_spec": {"chartType": "Bar chart",
                                                     "encodings": {"x": {"field": "a"}, "y": {"field": "b"}}}})),
            ("Sunburst 缺 color（非 ECharts 产物）",
             _json({"data": {"values": [{"n": "a", "v": 1}, {"n": "b", "v": 2}]},
                     "chart_spec": {"chartType": "Sunburst Chart",
                                    "encodings": {"detail": {"field": "n"}, "size": {"field": "v"}}}})),
        ]
        self.open_home()
        for label, spec in cases:
            self.inject([spec])
            self.settled(1)
            rendered, errors = self.counts()
            assert (rendered, errors) == (0, 1), f"{label} 未被拦（渲染 {rendered}）"
            assert self.page.locator("#wiki-view pre > code.language-flint").count() == 1, f"{label} 源码未保留"
            assert self.badge_kind() == "renderFail", f"{label} 文案应为「渲染失败」而非「数据过大」"
        assert not self.dialogs, f"弹窗了：{self.dialogs}"
        return "坏 JSON / 未知列 / 空 series / 大小写 / 非 ECharts 产物 —— 五者均保留源码 + 渲染失败文案"

    def s8c_ragged_rows_not_rejected(self):
        """8c. 列不齐不误杀（决策P4.20-14 反向用例）：首行缺列、后续行有 → **照常渲染**。"""
        self.open_home()
        self.inject([_json({"data": {"values": [{"类别": "a"}, {"类别": "b", "数值": 2}, {"类别": "c", "数值": 3}]},
                             "chart_spec": {"chartType": "Bar Chart",
                                            "encodings": {"x": {"field": "类别"}, "y": {"field": "数值"}}}})])
        self.settled(1)
        rendered, errors = self.counts()
        assert (rendered, errors) == (1, 0), f"列不齐被误判成 unknown-field（渲染 {rendered} / 徽标 {errors}）"
        return "首行缺列照常渲染（列集取全表并集）"

    def s_all37(self):
        """新增：**37 个图型逐个走完整链**，确认安全闸没误杀任何一个（决策P4.20-16 的常驻守门）。

        这是「闸子只证明了能拒、没证明不乱拒」的正面守门——首版实测正是在这里揪出
        `Waterfall`(renderItem 函数) / `Sankey`+`Network Graph`(links[].target 是节点名) /
        `Sunburst`(缺 color 不装配) 四例误杀。
        """
        self.open_home()
        specs = [_json(s) for s in _all37_specs()]
        self.inject(specs)
        self.settled(len(specs), timeout=40000)
        rendered, errors = self.counts()
        if errors:
            names = self.page.evaluate(
                "() => [...document.querySelectorAll('#wiki-view .flint-error')]"
                ".map(b => { try { return JSON.parse(b.nextElementSibling.textContent"
                ").chart_spec.chartType; } catch { return b.nextElementSibling.textContent.slice(0, 60); } })")
            raise AssertionError(f"{errors} 个图型被误杀：{names}")
        assert rendered == len(specs), f"只渲出 {rendered}/{len(specs)}"
        return f"{rendered}/{len(specs)} 个图型全渲染，无一被安全闸误杀"

    def s_tooltip_plain_text(self):
        """新增：flint 的 formatter 返回 HTML 串，richText 不解析 HTML —— 须先压成纯文本。

        不压的话，hover 一张最普通的柱图会看到字面量「模型: model-a<br/>评测得分: 72.4」。
        """
        self.open_home()
        self.open_page("wiki/concepts/Chart.md")
        self.page.wait_for_selector("#wiki-view figure.flint-rendered svg", timeout=15000)
        box = self.page.locator("#wiki-view figure.flint-rendered svg").bounding_box()
        self.page.mouse.move(box["x"] + box["width"] * 0.35, box["y"] + box["height"] * 0.75)
        self.page.wait_for_timeout(700)
        texts = [t or "" for t in self.page.locator("#wiki-view figure.flint-rendered text").all_text_contents()]
        joined = "\n".join(texts)
        assert "<br" not in joined.lower(), f"tooltip 画出了字面 HTML 标签：{[t for t in texts if '<br' in t.lower()][:2]}"
        assert "<b>" not in joined.lower() and "</span>" not in joined.lower(), f"tooltip 残留 HTML 标签：{joined[:120]}"
        # tooltip 真出现的证据：多出了带**数值**的文本节点（轴标签里没有 72.4 这种小数）。
        # richText 把多行画成**各自独立**的 <text>，故不能指望一个节点里同时有类别名和数值。
        hit = [t for t in texts if "72.4" in t]
        assert hit, f"tooltip 未出现（hover 没命中？）——本场景要在 tooltip 真出现时才有意义：{texts}"
        return f"tooltip 为纯文本、无字面标签：{hit[0]!r}"

    def s_semantic_colors_kept(self):
        """新增：逐槽换色**不许碰语义色**——Waterfall 的涨/跌颜色必须原样（决策P4.20-22）。

        按值替换曾把 #91cc75(涨) 换成澜青、#ee6666(跌) 换成海沫**绿**：把「跌」画成绿的。
        """
        self.open_home()
        self.inject([_json({
            "data": {"values": [{"月": "1月", "净额": 12}, {"月": "2月", "净额": -7},
                                {"月": "3月", "净额": 9}, {"月": "4月", "净额": -4}]},
            "chart_spec": {"chartType": "Waterfall Chart",
                           "encodings": {"x": {"field": "月"}, "y": {"field": "净额"}}}})])
        self.settled(1)
        assert self.counts() == (1, 0), "Waterfall 未渲染"
        html = self.page.locator("#wiki-view figure.flint-rendered").inner_html()
        assert "#91cc75" in html, "涨（绿）色被改掉了"
        assert "#ee6666" in html, "跌（红）色被改掉了"
        return "Waterfall 的涨绿/跌红原样保留（整张跳过逐槽换色）"

    def s_flint_truncation_not_silent(self):
        """新增：flint 自己超预算丢数据时也不许静默（决策P4.20-18）。

        color 通道 30 个类别 → flint 把大部分整批滤掉、只记进 _warnings(code:overflow)，
        且 color 通道**没有**可见的「...N items omitted」占位。剥 `_` 私有键前必须读它。
        """
        self.open_home()
        rows = [{"k": f"k{i}", "t": t, "v": (i * 7) % 50 + 1} for i in range(30) for t in ("t1", "t2")]
        self.inject([_json({"data": {"values": rows},
                            "chart_spec": {"chartType": "Bar Chart",
                                           "encodings": {"x": {"field": "t"}, "y": {"field": "v"},
                                                         "color": {"field": "k"}}}})])
        self.settled(1)
        rendered, errors = self.counts()
        assert (rendered, errors) == (0, 1), f"flint 静默截断后仍出了图（渲染 {rendered}）"
        assert self.page.locator("#wiki-view pre > code.language-flint").count() == 1, "源码未保留"
        assert self.badge_kind() == "truncated", "文案应为「被截断」——与「数据过大」「渲染失败」都不同"
        return "30 个 color 类别触发 flint overflow → 不渲染 + 「数据被截断」徽标"

    def s9_lazy_load(self):
        """9. 懒加载：不含图的页**不加载**两枚 vendored 资产；含图的页才加载。"""
        self.open_home()
        # **不清空** self.requests：收尾的「10. 全程零外部请求」扫的是同一个 list，clear() 会把
        # 场景 1–★37（含注入用例与 37 图型全集）的记录一并丢掉，那条总守门就只覆盖最后两个场景了。
        # 改为记一个起点下标、只看本场景窗口内的请求。
        mark = len(self.requests)
        self.open_page("wiki/concepts/Plain.md")
        self.page.wait_for_timeout(800)
        def runtime_hits() -> list[str]:
            return [u for u in self.requests[mark:]
                    if "flint/echarts.js" in u or "vendor/echarts.min.js" in u]

        assert not runtime_hits(), f"无图页仍加载了运行时：{runtime_hits()}"
        self.open_page("wiki/concepts/Chart.md")
        self.page.wait_for_selector("#wiki-view figure.flint-rendered svg", timeout=15000)
        loaded = runtime_hits()
        assert len(loaded) == 2, f"含图页应加载两枚资产，实得 {loaded}"
        return "无图页零加载 1.65 MB；含图页两枚都加载"

    def s11_lang_switch_no_leak(self):
        """11. 切语言十次：图仍是图，且旧实例被 dispose（决策P4.20-7，前端第一个有状态渲染器）。"""
        self.open_home()
        self.open_page("wiki/concepts/Chart.md")
        self.page.wait_for_selector("#wiki-view figure.flint-rendered svg", timeout=15000)
        leaked = self.page.evaluate("""async () => {
          const seen = [];
          for (let i = 0; i < 10; i++) {
            const old = document.querySelector('#wiki-view figure.flint-rendered');
            if (old) seen.push(old);
            toggleLang();
            await new Promise((r) => setTimeout(r, 250));
          }
          // 脱树的旧 <figure> 上不应再挂着活实例（dispose 过 → getInstanceByDom 取不到 / isDisposed）
          return seen.filter((el) => {
            if (el.isConnected) return false;
            const inst = window.echarts && window.echarts.getInstanceByDom(el);
            return inst && !inst.isDisposed();
          }).length;
        }""")
        self.page.wait_for_selector("#wiki-view figure.flint-rendered svg", timeout=15000)
        assert self.page.locator("#wiki-view pre > code.language-flint").count() == 0, "切语言后打回源码"
        assert leaked == 0, f"切语言后仍有 {leaked} 个脱树实例未 dispose（泄漏）"
        assert self.page.locator("#wiki-view figure.flint-rendered").count() == 1, "figure 累积"
        return "切 zh↔en 十次：图仍是图、脱树实例全部 dispose、figure 不累积"


def _all37_specs() -> list[dict]:
    """37 个图型 × 各自**正确的数据形状**（见 skills/flint-chart-author/references/flint-spec.md §2）。"""
    num = [{"数值": 100 + (i * 37) % 90, "类别": f"c{i % 4}", "组": f"g{i % 2}"} for i in range(60)]
    cat = [{"类别": "model-a", "数值": 72.4, "组": "甲"}, {"类别": "model-b", "数值": 81.9, "组": "乙"},
           {"类别": "model-c", "数值": 88.3, "组": "甲"}, {"类别": "model-d", "数值": 65.1, "组": "乙"}]
    tim = [{"时点": f"2026-0{m}", "数值": v, "组": g}
           for g, vs in (("甲", [1.2, 1.9, 2.4]), ("乙", [0.7, 1.1, 1.6]))
           for m, v in zip((1, 2, 3), vs)]
    xy = [{"x": i * 3 + 1, "y": (i * 7) % 40 + 5, "类别": f"c{i % 3}", "大小": (i % 5) + 1} for i in range(24)]
    dat = [{"日期": f"2026-0{1 + i % 9}-{1 + i % 27:02d}", "数值": i % 17} for i in range(40)]
    flow = [{"源": "a", "目标": "x", "量": 3}, {"源": "a", "目标": "y", "量": 2}, {"源": "b", "目标": "x", "量": 4}]

    def spec(chart, values, enc, **extra):
        return {"data": {"values": values},
                "chart_spec": {"chartType": chart,
                               "encodings": {k: {"field": v} for k, v in enc.items()}, **extra}}

    xy_enc = {"x": "类别", "y": "数值"}
    return [
        spec("Bar Chart", cat, xy_enc),
        spec("Grouped Bar Chart", cat, {**xy_enc, "group": "组"}),
        spec("Stacked Bar Chart", cat, {**xy_enc, "color": "组"}),
        spec("Lollipop Chart", cat, xy_enc),
        spec("Pyramid Chart", cat, xy_enc),
        spec("Waterfall Chart", cat, xy_enc),
        spec("Bullet Chart", [{**r, "目标": 80} for r in cat], {"y": "类别", "x": "数值", "goal": "目标"}),
        spec("Gauge Chart", [{"占比": 0.62}], {"size": "占比"}),
        spec("Radar Chart", cat, xy_enc),
        spec("Rose Chart", cat, xy_enc),
        spec("Line Chart", tim, {"x": "时点", "y": "数值", "color": "组"}),
        spec("Area Chart", tim, {"x": "时点", "y": "数值", "color": "组"}),
        spec("Streamgraph", tim, {"x": "时点", "y": "数值", "color": "组"}),
        spec("Range Area Chart", [{**r, "上界": r["数值"] + 0.5} for r in tim],
             {"x": "时点", "y": "数值", "y2": "上界", "color": "组"}),
        spec("Bump Chart", [{**r, "名次": i % 3 + 1} for i, r in enumerate(tim)],
             {"x": "时点", "y": "名次", "color": "组"}),
        spec("Slope Chart", [r for r in tim if r["时点"] != "2026-02"], {"x": "时点", "y": "数值", "color": "组"}),
        spec("Candlestick Chart", [{"时点": r["时点"], "开": 10, "高": 14, "低": 9, "收": 12} for r in tim[:3]],
             {"x": "时点", "open": "开", "high": "高", "low": "低", "close": "收"}),
        spec("Calendar Heatmap", dat, {"x": "日期", "color": "数值"}),
        spec("Histogram", num, {"x": "数值"}),
        spec("Density Plot", num, {"x": "数值"}),
        spec("ECDF Plot", num, {"x": "数值"}),
        spec("Boxplot", num, xy_enc),
        spec("Strip Plot", num[:40], xy_enc),
        spec("Ranged Dot Plot", [{**r, "上": r["数值"] + 8} for r in cat], {"x": "数值", "y": "类别"}),
        spec("Scatter Plot", xy, {"x": "x", "y": "y", "color": "类别"}),
        spec("Regression", xy, {"x": "x", "y": "y"}),
        spec("Connected Scatter Plot", [{**r, "序": i} for i, r in enumerate(xy)],
             {"x": "x", "y": "y", "order": "序"}),
        spec("Heatmap", xy, {"x": "类别", "y": "大小", "color": "y"}),
        spec("Parallel Coordinates", [{**r, "名": f"n{i}"} for i, r in enumerate(xy)],
             {"detail": "名", "color": "类别"}),
        spec("Pie Chart", cat, {"size": "数值", "color": "类别"}),
        spec("Funnel Chart", cat, {"y": "类别", "size": "数值"}),
        spec("Treemap", cat, {"detail": "类别", "size": "数值", "color": "组"}),
        spec("Sunburst Chart", cat, {"color": "组", "size": "数值", "detail": "类别"}),  # color 必给
        spec("Tree", cat, {"detail": "类别", "size": "数值", "color": "组"}),
        spec("Sankey Diagram", flow, {"x": "源", "y": "目标", "size": "量"}),
        spec("Network Graph", [{"源": "a", "目标": "b", "量": 3}, {"源": "b", "目标": "c", "量": 2},
                               {"源": "a", "目标": "c", "量": 1}], {"x": "源", "y": "目标", "size": "量"}),
        spec("Gantt Chart", [{"任务": "设计", "起": "2026-01-01", "止": "2026-02-01"},
                             {"任务": "实现", "起": "2026-02-01", "止": "2026-04-01"}],
             {"y": "任务", "x": "起", "x2": "止"}),
    ]


# ── 打桩场景（用 page.route 换掉 vendored 资产；生产代码零测试钩子）──

# 假 flint：产出一份**带危险键**的 option，模拟上游版本漂移（场景 5）。
_STUB_FLINT_BAD = """
export function assembleECharts() {
  return { _width: 400, _height: 300, title: { text: "t", link: "javascript:alert(1)" },
           series: [{ type: "bar", data: [1, 2, 3] }] };
}
"""
# 假 ECharts：init 成功但 setOption 抛错，验事务式替换（场景 8d，决策P4.20-15）。
_STUB_ECHARTS_THROW = """
window.echarts = {
  init(dom) {
    return { getDom: () => dom, isDisposed: () => this._d, dispose() { this._d = true; },
             setOption() { throw new Error("boom"); } };
  },
  getInstanceByDom: () => undefined,
};
"""


def _stub_scenarios(browser, base: str) -> list[tuple[str, bool, str]]:
    """两个需要打桩 vendored 资产的场景，各起独立 context（模块缓存互不污染）。"""
    out: list[tuple[str, bool, str]] = []

    def run(label: str, route_url: str, body: str, ctype: str, check):
        ctx = browser.new_context()
        page = ctx.new_page()
        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.route(route_url, lambda r: r.fulfill(status=200, content_type=ctype, body=body))
        try:
            page.goto(base, wait_until="networkidle")
            page.wait_for_selector("#wiki-view", timeout=10000)
            page.evaluate(_INJECT, [[_json(BAR_OK)]])
            page.wait_for_function(
                "() => document.querySelectorAll('#wiki-view figure.flint-rendered, "
                "#wiki-view .flint-error').length === 1", timeout=15000)
            detail = check(page, dialogs)
            out.append((label, True, detail))
            print(f"  ✓ {label} — {detail}")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).splitlines()[0][:120]
            out.append((label, False, f"{type(exc).__name__}: {msg}"))
            print(f"  ✗ {label} — {type(exc).__name__}: {msg}")
        finally:
            ctx.close()

    def check_exit_assertion(page, dialogs):
        """5. 出口断言（键域）：打桩产物带 title.link → 不渲染、保留源码 + 徽标。"""
        assert page.locator("#wiki-view figure.flint-rendered").count() == 0, "带 link 的产物竟被渲染"
        assert page.locator("#wiki-view .flint-error").count() == 1, "无徽标"
        assert page.locator("#wiki-view pre > code.language-flint").count() == 1, "源码未保留"
        assert not dialogs, f"弹窗了：{dialogs}"
        return "带 title.link 的产物被出口断言拦下、保留源码 + 徽标"

    def check_transactional(page, dialogs):
        """8d. 渲染期失败不留空白（决策P4.20-15）：setOption 抛错 → 仍是 <pre> 源码 + 徽标。"""
        assert page.locator("#wiki-view figure.flint-rendered").count() == 0, \
            "留下了空 figure.flint-rendered（replaceWith 早于 setOption？）"
        assert page.locator("#wiki-view pre > code.language-flint").count() == 1, "源码未保留"
        assert page.locator("#wiki-view .flint-error").count() == 1, "无徽标"
        assert not dialogs, f"弹窗了：{dialogs}"
        return "setOption 抛错 → 源码 + 徽标俱在、无空 figure（事务式替换成立）"

    run("5. 出口断言拦下危险产物（打桩 flint）", "**/static/vendor/flint/echarts.js",
        _STUB_FLINT_BAD, "text/javascript", check_exit_assertion)
    run("8d. 渲染期失败不留空白（打桩 ECharts）", "**/static/vendor/echarts.min.js",
        _STUB_ECHARTS_THROW, "text/javascript", check_transactional)
    return out


SCENARIOS = [
    ("1. 真页成图 + viewBox + 昼澜色板", "s1_render"),
    ("2. 窄栏按栏宽渲染、无横向溢出", "s2_responsive"),
    ("3. 注入载荷不弹窗、无活元素", "s3_injection"),
    ("4. tooltip 不走 HTML 通道", "s4_tooltip_no_html"),
    ("5b. URL 当类别名不被误否", "s5b_url_not_falsely_rejected"),
    ("6. 拒 data.url + 零外部请求", "s6_reject_data_url"),
    ("7. 资源闸：三类文案各归各的", "s7_resource_gates"),
    ("8/8b. 坏输入与空图全部保留源码", "s8_bad_input_and_empty_chart"),
    ("8c. 列不齐不误杀", "s8c_ragged_rows_not_rejected"),
    ("★ 37 个图型无一被误杀", "s_all37"),
    ("★ tooltip 压成纯文本、无字面 HTML", "s_tooltip_plain_text"),
    ("★ 语义色（涨绿/跌红）不被换色", "s_semantic_colors_kept"),
    ("★ flint 自己的截断也不静默", "s_flint_truncation_not_silent"),
    ("9. 懒加载：无图页零加载", "s9_lazy_load"),
    ("11. 切语言十次不泄漏实例", "s11_lang_switch_no_leak"),
]


def _selected(scenarios):
    """可选的场景过滤（`python scripts/smoke_p420.py <子串>`）——调试与**反向对照**用：
    把某条修复临时去掉，只跑它对应的那个场景，确认用例真的会红。"""
    pats = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not pats:
        return scenarios
    return [x for x in scenarios if any(p in x[0] or p in x[1] for p in pats)]


def _run(pw, kb: Path) -> list[tuple[str, bool, str]]:
    app = create_app(kb)  # 只读姿态足矣：本相位纯前端，不经 Agent
    server = _serve(app, _free_port())
    base = f"http://127.0.0.1:{server.config.port}/"
    results: list[tuple[str, bool, str]] = []
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        requests: list[str] = []
        dialogs: list[str] = []
        console_errors: list[str] = []
        page.on("request", lambda r: requests.append(r.url))
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        smoke = Smoke(page, base, requests, dialogs)
        for label, meth in _selected(SCENARIOS):
            try:
                detail = getattr(smoke, meth)()
                results.append((label, True, detail))
                print(f"  ✓ {label} — {detail}")
            except Exception as exc:  # noqa: BLE001 — 任一场景失败只记录、不中断后续
                msg = str(exc).splitlines()[0][:140]
                results.append((label, False, f"{type(exc).__name__}: {msg}"))
                print(f"  ✗ {label} — {type(exc).__name__}: {msg}")
        # 全程零外部请求（决策P4.20-1/-5：无 CDN、离线自洽）
        external = sorted({u for u in requests if not u.startswith(base.rstrip("/"))})
        label = "10. 全程零外部请求（无 CDN）"
        # 过滤模式下这条只覆盖被选中的那几个场景，标题会名不副实——但它仍是有效的断言，保留。
        results.append((label, not external, "零外部请求" if not external else f"外部请求：{external[:3]}"))
        print(("  ✓ " if not external else "  ✗ ") + f"{label} — {results[-1][2]}")
        page.close()
        if len(_selected(SCENARIOS)) == len(SCENARIOS):
            results.extend(_stub_scenarios(browser, base))
        browser.close()
        if console_errors:
            print(f"  ⚠ 浏览器 console error（{len(console_errors)}）：{console_errors[:3]}")
    finally:
        server.should_exit = True
        time.sleep(0.2)
    return results


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        kb = _make_kb(Path(td))
        with sync_playwright() as pw:
            results = _run(pw, kb)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\nP4.20 真浏览器冒烟：{passed}/{total} 通过")
    if passed != total:
        print("失败项：")
        for label, ok, detail in results:
            if not ok:
                print(f"  ✗ {label} — {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
