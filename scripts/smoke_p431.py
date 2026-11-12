#!/usr/bin/env python3
"""P4.31 网址导入真浏览器冒烟（headless Chromium / Playwright，docs/P4.31-网址导入.md §10）。

`tests/test_web_fetch.py` 用 TestClient + 假下载器只能验端点；这里验真的前端接线：
「从网址下载…」表单、进度与「停止」、🔗 标记、HTML 行的「整页一键入库」、勾选「直接一键入库」后切到解析进度浮层。

起两个真 socket 服务：一个假网站（本机 127.0.0.1，经 `AGENTAO_WEB_FETCH_ALLOW_CIDRS` 放行）、一个可写 Web 宿主。
**不打真实 LLM**：一键入库的准备阶段零 LLM（测试网页没有目录，不触发目录审校）。HTML 转换真跑 pandoc。

**不入 pytest**（需浏览器 + 真 socket）。手动跑：
    uv run --extra web python scripts/smoke_p431.py
首跑前装浏览器：`uv run python -m playwright install chromium`。
全过 → 退出码 0；任一断言失败 → 1；缺 playwright/chromium/web extra/pandoc → 2（跳过、非失败）。
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import uvicorn

    from guanlan.web.app import create_app
except ImportError as exc:  # noqa: BLE001
    print(f"[skip] 缺依赖（需 guanlan-wiki[web]）：{exc}")
    sys.exit(2)
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[skip] 未装 playwright")
    sys.exit(2)
if shutil.which("pandoc") is None:
    print("[skip] 未装 pandoc")
    sys.exit(2)

PARA = "这是正文段落，讲的是网址导入的设计与实现细节。" * 8
PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"><title>示例政策解读</title></head><body>'
    '<nav><a href="/">首页</a> <a href="/about">关于我们</a></nav>'
    f"<article><h2>一、背景</h2><p>{PARA}</p><h2>二、要点</h2><p>{PARA}</p></article>"
    "<footer>版权所有 示例网站</footer></body></html>"
).encode("utf-8")
PDF = b"%PDF-1.4\n%smoke\n" + b"0" * 2048
SHELL = (  # 靠 JS 渲染的空壳页（决策P4.31-15）
    '<!doctype html><html><head><meta charset="utf-8"><title>应用</title></head>'
    '<body><noscript>请开启 JavaScript 以继续。</noscript><div id="root"></div></body></html>'
).encode("utf-8")


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 安静
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/report.pdf":
            self._send(PDF, "application/pdf")
        elif self.path == "/app/shell.html":
            self._send(SHELL, "text/html; charset=utf-8")
        elif self.path == "/news/policy.html":
            self._send(PAGE, "text/html; charset=utf-8")
        elif self.path == "/slow.pdf":  # 慢速：给「停止」留时间
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(10 * 1024 * 1024))
            self.end_headers()
            try:
                self.wfile.write(b"%PDF-1.4\n")
                for _ in range(200):
                    time.sleep(0.1)
                    self.wfile.write(b"0" * 1024)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(404)
            self.end_headers()

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kb(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "AGENTAO.md").write_text("# AGENTAO\n", encoding="utf-8")
    (root / "SCHEMA.md").write_text("# SCHEMA\n", encoding="utf-8")
    (root / "raw").mkdir()
    for sub in ("sources", "entities", "concepts", "syntheses"):
        (root / "wiki" / sub).mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# 索引\n", encoding="utf-8")
    (root / "wiki" / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (root / "wiki" / "overview.md").write_text("# 概览\n", encoding="utf-8")
    return root


def main() -> int:
    os.environ["AGENTAO_WEB_FETCH_ALLOW_CIDRS"] = "127.0.0.0/8"  # 假网站在本机
    site_port = _free_port()
    site = ThreadingHTTPServer(("127.0.0.1", site_port), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{site_port}"

    tmp = Path(tempfile.mkdtemp(prefix="guanlan-smoke-p431-"))
    kb = _kb(tmp / "kb")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(kb), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  ✓ " if cond else "  ✗ ") + msg)
        if not cond:
            failures.append(msg)

    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                print(f"[skip] 起不了 chromium：{exc}")
                return 2
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/")
            page.click("#staging-btn")

            print("① 下载 PDF")
            page.get_by_role("button", name="从网址下载…").click()
            page.fill(".stage-fetch input[type=url]", f"{base}/report.pdf")
            page.evaluate("loadStaging()")  # 暂存区重画（切语言、刷新列表同理）：输入到一半的网址不能丢
            page.wait_for_timeout(500)
            check(page.input_value(".stage-fetch input[type=url]") == f"{base}/report.pdf", "重画后表单里的网址还在")
            page.click(".stage-fetch button[type=submit]")
            page.wait_for_selector(".stage-row:has-text('report.pdf')", timeout=10000)
            mark = page.locator(".stage-row:has-text('report.pdf') .stage-link-mark")
            check(mark.count() == 1, "下载的文件出现在列表、带 🔗")
            check(f"{base}/report.pdf" in (mark.first.get_attribute("title") or ""), "🔗 悬停显示原网址")
            check(page.locator(".stage-fetch-status:has-text('已下载')").count() == 1, "显示「已下载」")
            check((kb / "workspace" / "uploads" / ".report.pdf.source.json").is_file(), "出处记录已写")

            print("② 停止慢速下载")
            page.get_by_role("button", name="从网址下载…").click()
            page.fill(".stage-fetch input[type=url]", f"{base}/slow.pdf")
            page.click(".stage-fetch button[type=submit]")
            page.wait_for_selector(".stage-fetch-row:has-text('正在下载')", timeout=10000)
            page.get_by_role("button", name="停止").click()
            page.wait_for_selector(".stage-fetch-status:has-text('已停止')", timeout=10000)
            check(True, "停止后显示「已停止下载」")
            leftovers = [p.name for p in (kb / "workspace" / "uploads").iterdir() if p.name.startswith(".upload-")]
            check(leftovers == [], "停止后没有临时文件残留")

            print("②½ 下载失败：红字、小两号")
            page.get_by_role("button", name="从网址下载…").click()
            page.fill(".stage-fetch input[type=url]", f"{base}/missing.pdf")
            page.click(".stage-fetch button[type=submit]")
            fail = page.locator(".stage-fetch-status.stage-fetch-error")
            fail.wait_for(timeout=10000)
            color, size = fail.evaluate("e => [getComputedStyle(e).color, getComputedStyle(e).fontSize]")
            check(color == "rgb(192, 57, 43)", f"失败信息是红色（{color}）")
            check(float(size.rstrip("px")) <= 11.5, f"失败信息字号小两号（{size}）")

            print("②¾ 空壳网页：准备失败、红字")
            page.get_by_role("button", name="从网址下载…").click()
            page.fill(".stage-fetch input[type=url]", f"{base}/app/shell.html")
            page.check(".stage-fetch label:has-text('直接一键入库') input")
            page.click(".stage-fetch button[type=submit]")
            bad = page.locator(".log-bad:has-text('准备失败')")
            bad.wait_for(timeout=60000)
            check("空壳页" in bad.inner_text(), "日志说明是空壳页")
            check(bad.evaluate("e => getComputedStyle(e).color") == "rgb(192, 57, 43)", "「✗ 准备失败」是红字")
            check(not list((kb / "raw").rglob("*.md")), "空壳没进 raw/")
            page.get_by_role("button", name="返回导入").click()

            print("③ 网页 + 直接一键入库")
            page.get_by_role("button", name="从网址下载…").click()
            page.fill(".stage-fetch input[type=url]", f"{base}/news/policy.html")
            page.check(".stage-fetch label:has-text('直接一键入库') input")
            page.click(".stage-fetch button[type=submit]")
            page.wait_for_selector(".oc-card:has-text('等待确认')", timeout=60000)
            check(True, "下载后自动开始准备，停在「等待确认」")
            parsed = (kb / "workspace" / "parsed" / "policy.md").read_text(encoding="utf-8")
            check(f"origin: {base}/news/policy.html" in parsed, "整理稿 origin 是网址")
            check("首页" not in parsed and "版权所有" not in parsed, "导航与页脚已被正文提取去掉")
            check("parsed_by: pandoc+trafilatura" in parsed, "parsed_by 记 pandoc+trafilatura")
            row = page.locator(".stage-row:has-text('policy.html')")
            check(row.locator("button[aria-label='整页一键入库']").count() == 1, "HTML 行有「整页一键入库」")
            check(page.locator(".stage-row:has-text('report.pdf') button[aria-label='整页一键入库']").count() == 0, "PDF 行没有")

            check(errors == [], f"页面无 JS 错误 {errors}")
            browser.close()
    finally:
        server.should_exit = True
        site.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print("全部通过" if not failures else f"{len(failures)} 项失败")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
