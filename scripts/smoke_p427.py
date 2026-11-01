#!/usr/bin/env python3
"""P4.27 真浏览器冒烟（headless Chromium / Playwright，docs/P4.27-Web图可视化.md §5）。

`tests/test_web_graph.py` 只验服务端 `/api/graph` 的视图形状；画布是否出图、点节点是否真的展开 / 打开页面、
标题是否按字面显示、ECharts 是否只注入一次、reader 下的入口与链接，都只能在真浏览器里看。本脚本起一个
**真 socket** 的 Web 宿主、用**真的**按钮与 ECharts 实例驱动，节点点击按布局坐标落成真实鼠标点击。
**不打真实 LLM**、生产代码里不留测试钩子（只读页面已有的全局 `graphChart` / `graphState`）。

**不入 pytest**（需浏览器 + 真 socket）。手动跑：
    uv run --extra web python scripts/smoke_p427.py
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

try:
    import uvicorn

    from guanlan.web import graphview
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


XSS_TITLE = "<img src=x onerror=window.__pwned=1>"
BAR_OK = {
    "data": {"values": [{"模型": "model-a", "评测得分": 72.4}, {"模型": "model-b", "评测得分": 81.9}]},
    "semantic_types": {"模型": "Category", "评测得分": "Score"},
    "chart_spec": {"chartType": "Bar Chart",
                   "encodings": {"x": {"field": "模型"}, "y": {"field": "评测得分"}},
                   "baseSize": {"width": 420, "height": 260}},
}


def _page(root: Path, stem: str, links=(), *, title: str | None = None, body: str = "") -> None:
    p = root / "wiki" / "concepts" / f"{stem}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    t = json.dumps(title or stem, ensure_ascii=False)  # JSON 串即合法的 YAML 双引号串
    links_md = " ".join(f"[[{x}]]" for x in links)
    p.write_text(f"---\ntitle: {t}\ntype: concept\nsources: []\nlast_updated: 2026-10-31\n---\n\n"
                 f"{links_md}\n\n{body}", encoding="utf-8")


def _make_kb(root: Path) -> Path:
    (root / "AGENTAO.md").write_text("# AGENTAO\n", encoding="utf-8")
    (root / "SCHEMA.md").write_text("# SCHEMA\n", encoding="utf-8")
    (root / "raw").mkdir()
    wiki = root / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    # 两个五页团（a 团含一张注入标题页），a1→b1 一条边相连；外加一张孤立页与一张带 flint 图的页。
    a = ["a1", "a2", "a3", "a4", "axss"]
    b = ["b1", "b2", "b3", "b4", "b5"]
    for grp in (a, b):
        for s in grp:
            extra = ["b1"] if s == "a1" else []
            _page(root, s, [x for x in grp if x != s] + extra, title=XSS_TITLE if s == "axss" else None)
    _page(root, "lone")
    _page(root, "chart", body=f"```flint\n{json.dumps(BAR_OK, ensure_ascii=False)}\n```\n")
    return root


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app, port: int) -> uvicorn.Server:
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("uvicorn 未在 10s 内就绪")


# 当前视图里某节点的屏幕坐标：取 force 布局给的坐标，经 series 的坐标系换成画布像素，再加画布偏移。
_NODE_XY = """
(id) => {
  const s = graphChart.getModel().getSeriesByIndex(0);
  const data = s.getData();
  const idx = data.indexOfName(id);
  if (idx < 0) return null;
  const pt = graphChart.convertToPixel({ seriesIndex: 0 }, data.getItemLayout(idx));
  const r = document.querySelector("#graph-canvas").getBoundingClientRect();
  return { x: r.left + pt[0], y: r.top + pt[1] };
}
"""


class Smoke:
    def __init__(self, page, base: str) -> None:
        self.page = page
        self.base = base

    def _wait_view(self, view: str) -> None:
        self.page.wait_for_function(
            "(v) => graphState && graphState.params.view === v && graphChart && "
            "document.querySelector('#graph-canvas canvas')", arg=view, timeout=15000)

    def _note(self) -> str:
        return self.page.locator("#graph-note").inner_text()

    def _crumbs(self) -> str:
        return self.page.locator("#graph-crumbs").inner_text()

    def _click_node(self, node_id: str) -> None:
        time.sleep(2.5)  # 等 force 布局大体收敛，否则点下去时节点已移走
        xy = self.page.evaluate(_NODE_XY, node_id)
        assert xy, f"视图里没有节点 {node_id}"
        self.page.mouse.click(xy["x"], xy["y"])

    def _open_overview(self) -> None:
        if self.page.locator("#overlay").is_visible():
            self.page.click("#overlay-close")
        self.page.click("#graph-btn")
        self._wait_view("overview")

    def overview(self) -> str:
        self._open_overview()
        note = self._note()
        assert "2 个社区" in note and "共 12 页" in note and "2 张孤立页" in note, note
        assert self._crumbs() == "总览", self._crumbs()
        assert self.page.locator("#overlay-body .graph-static").count() == 1, "写者模式应有静态列表链接"
        return note

    def click_community(self) -> str:
        self._open_overview()
        self._click_node("b1")
        self._wait_view("community")
        assert [n["id"] for n in self.page.evaluate("graphState.data.nodes")] == ["b1", "b2", "b3", "b4", "b5"]
        assert "社区：b1" in self._crumbs(), self._crumbs()
        return self._crumbs().replace("\n", " ")

    def click_page_opens_wiki(self) -> str:
        self._click_node("b3")  # 接上一场景：社区视图里点页节点
        self.page.wait_for_function("() => document.querySelector('#overlay').classList.contains('hidden')")
        self.page.wait_for_selector("#wiki-view .page-meta", timeout=10000)
        meta = self.page.locator("#wiki-view .page-meta").inner_text()
        assert "wiki/concepts/b3.md" in meta, meta
        assert self.page.evaluate("graphChart") is None, "关面板后画布未释放"
        return "打开 wiki/concepts/b3.md、画布已释放"

    def view_in_graph(self) -> str:
        self.page.click("#wiki-view .page-meta .graph-here")  # 接上一场景：b3 页上的入口
        self._wait_view("page")
        ids = [n["id"] for n in self.page.evaluate("graphState.data.nodes")]
        assert ids == ["b1", "b2", "b3", "b4", "b5"], ids
        crumbs = self._crumbs()
        assert "总览" in crumbs and "社区：b1" in crumbs and crumbs.strip().endswith("b3"), crumbs
        return crumbs.replace("\n", " ")

    def crumb_back(self) -> str:
        self.page.click("#graph-crumbs button.graph-crumb >> nth=1")  # 「社区：b1」
        self._wait_view("community")
        self.page.click("#graph-crumbs button.graph-crumb >> nth=0")  # 「总览」
        self._wait_view("overview")
        return "页 → 社区 → 总览"

    def literal_title(self) -> str:
        self.page.evaluate("() => openGraph({ view: 'page', name: 'axss' })")
        self._wait_view("page")
        crumbs = self.page.locator("#graph-crumbs")
        assert crumbs.locator("img").count() == 0, "标题被当 HTML 解析了"
        assert "<img" in crumbs.inner_text(), crumbs.inner_text()
        # 悬停注入页节点：提示框是 richText（画在画布上），DOM 里不得出现任何 <img>。
        time.sleep(2.5)
        xy = self.page.evaluate(_NODE_XY, "axss")
        self.page.mouse.move(xy["x"], xy["y"])
        time.sleep(0.5)
        assert self.page.locator("img[src='x']").count() == 0, "提示框 / 标签产生了 <img>"
        assert self.page.evaluate("window.__pwned") is None, "注入脚本被执行了"
        return "面包屑与提示框均按字面显示、未执行"

    def truncation_notice(self) -> str:
        graphview.GRAPH_VIEW_MAX_NODES = 3  # 服务端同进程：调小上限，看前端告示
        try:
            self.page.evaluate("() => openGraph({ view: 'community', anchor: 'a1' })")
            self._wait_view("community")
            note = self._note()
            assert "只显示连接最多的 3 个（共 5 个）" in note, note
            assert len(self.page.evaluate("graphState.data.nodes")) == 3
        finally:
            graphview.GRAPH_VIEW_MAX_NODES = 300
        return note

    def gone_falls_back(self) -> str:
        self.page.evaluate("() => openGraph({ view: 'community', anchor: '已经删掉的页' })")
        self._wait_view("overview")
        self.page.wait_for_function("() => document.querySelector('#graph-note').textContent.includes('回到总览')")
        return self._note()

    def language_switch(self) -> str:
        self._open_overview()
        # 浮层罩住了顶栏，鼠标点不到语言按钮；直接触发它的 click（等价于键盘操作），看面板是否随之重绘。
        self.page.evaluate("() => document.querySelector('#lang-btn').click()")
        try:
            self.page.wait_for_function("() => document.querySelector('#graph-crumbs').textContent === 'Overview'")
            note = self._note()
            assert "2 communities" in note, note
            assert self.page.evaluate("graphChart") is not None, "切语言不该释放画布"
        finally:
            self.page.evaluate("() => document.querySelector('#lang-btn').click()")
        return note

    def other_overlay_disposes(self) -> str:
        self._open_overview()
        # 别的 opener 在图谱打开时接管浮层（如作业收尾自动开报告）：直接触发其按钮的 click。
        self.page.evaluate("() => document.querySelector(\"button[data-report='check']\").click()")
        self.page.wait_for_function("() => !document.querySelector('#graph-canvas')")
        assert self.page.evaluate("graphChart") is None, "开别的浮层后画布未释放"
        wide = self.page.evaluate("document.querySelector('#overlay .overlay-card').classList.contains('overlay-graph')")
        assert not wide, "别的浮层沿用了图谱的宽卡片"
        self.page.click("#overlay-close")
        return "画布释放、卡片宽度复位"

    def echarts_once(self) -> str:
        self.page.evaluate("() => navigate({ kind: 'page', path: 'wiki/concepts/chart.md' })")
        self.page.wait_for_selector("#wiki-view figure.flint-rendered", timeout=15000)
        self._open_overview()
        n = self.page.locator("script[src='/static/vendor/echarts.min.js']").count()
        assert n == 1, f"ECharts 注入了 {n} 次"
        self.page.click("#overlay-close")
        return "flint 图与图谱共用一次注入"


SCENARIOS = [
    ("1. 总览出图：社区数、页数、孤立页计数、静态列表链接", "overview"),
    ("2. 点社区节点展开社区", "click_community"),
    ("3. 点页节点打开 wiki 页并释放画布", "click_page_opens_wiki"),
    ("4. 页上「在图中查看」进一跳邻域", "view_in_graph"),
    ("5. 面包屑逐级返回", "crumb_back"),
    ("6. 注入标题按字面显示（面包屑 + 提示框）", "literal_title"),
    ("7. 截断显式告示", "truncation_notice"),
    ("8. 锚页已不存在 → 回总览并说明", "gone_falls_back"),
    ("9. 切语言只重绘 chrome", "language_switch"),
    ("10. 打开别的浮层释放画布、复位卡片", "other_overlay_disposes"),
    ("11. flint 与图谱共用一次 ECharts 注入", "echarts_once"),
]


def _reader_check(pw, kb: Path) -> tuple[bool, str]:
    """reader 模式：图谱按钮可见、面板能出总览、不给写者专属的静态列表链接。"""
    server = _serve(create_app(kb, reader=True), _free_port())
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"http://127.0.0.1:{server.config.port}/")
        page.wait_for_function("() => readerMode === true")
        assert page.locator("#graph-btn").is_visible(), "reader 下图谱按钮被隐藏了"
        page.click("#graph-btn")
        page.wait_for_function("() => graphState && document.querySelector('#graph-canvas canvas')", timeout=15000)
        assert page.locator("#overlay-body .graph-static").count() == 0, "reader 下给了指向 /graph 的死链"
        browser.close()
        return True, "按钮可见、总览出图、无静态列表链接"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"
    finally:
        server.should_exit = True
        time.sleep(0.2)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as td:
        kb = _make_kb(Path(td))
        server = _serve(create_app(kb), _free_port())
        base = f"http://127.0.0.1:{server.config.port}/"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1400, "height": 1000})
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base)
                page.wait_for_function("typeof openGraph === 'function'")
                smoke = Smoke(page, base)
                for label, meth in SCENARIOS:
                    try:
                        detail = getattr(smoke, meth)()
                        results.append((label, True, detail))
                        print(f"  ✓ {label} — {detail}")
                    except Exception as exc:  # noqa: BLE001 — 单场景失败只记录
                        msg = str(exc).splitlines()[0][:200] if str(exc) else ""
                        results.append((label, False, f"{type(exc).__name__}: {msg}"))
                        print(f"  ✗ {label} — {type(exc).__name__}: {msg}")
                label = "12. 全程无页面脚本异常"
                results.append((label, not errors, "无" if not errors else str(errors[:2])))
                print(("  ✓ " if not errors else "  ✗ ") + f"{label} — {results[-1][2]}")
                browser.close()

                ok, detail = _reader_check(pw, kb)
                label = "13. reader 模式"
                results.append((label, ok, detail))
                print(("  ✓ " if ok else "  ✗ ") + f"{label} — {detail}")
        finally:
            server.should_exit = True
            time.sleep(0.2)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nP4.27 真浏览器冒烟：{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
