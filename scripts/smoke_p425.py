#!/usr/bin/env python3
"""P4.25 真浏览器冒烟（headless Chromium / Playwright，docs/P4.25-Web思考过程.md §5）。

`test_web.py` 只验服务端帧（`thinking{text}` 的映射与保序）；折叠块、心跳撤/建、字面显示、64 K 截断、
`reasoning_only` 自动展开、顶栏开关这些都是前端行为，只能在真浏览器里看。本脚本起一个**真 socket** 的
Web 宿主，把手写的 SSE 帧文本喂给**真的** `handleSSE` / `handleGoalSSE`（经同一个 `parseSSE` 解析），
**不打真实 LLM**、生产代码里不留测试钩子。

**不入 pytest**（需浏览器 + 真 socket）。手动跑：
    uv run --extra web python scripts/smoke_p425.py
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


def _make_kb(root: Path) -> Path:
    (root / "AGENTAO.md").write_text("# AGENTAO\n", encoding="utf-8")
    (root / "SCHEMA.md").write_text("# SCHEMA\n", encoding="utf-8")
    (root / "raw").mkdir()
    wiki = root / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
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


def _frame(event: str, payload) -> str:
    """一帧 SSE 文本（不含帧尾空行）——与服务端 `_sse` 同形，交给真 `parseSSE`。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}"


# 新建一个 bot 气泡并把若干帧依次喂给真 handleSSE；返回该气泡在 #chat-log 中的下标（供后续定位）。
_FEED = """
([frames]) => {
  const botEl = addMsg("bot", "");
  for (const f of frames) handleSSE(f, botEl);
  window.__bot = botEl;
}
"""
_MORE = "([frames]) => { for (const f of frames) handleSSE(f, window.__bot); }"
# 块是气泡的前一个兄弟（botEl.before）；查询都相对当前气泡，免受前面场景残留的影响。
_STATE = """
() => {
  const bot = window.__bot;
  const prev = bot.previousElementSibling;
  const blk = prev && prev.classList.contains("chat-think") ? prev : null;
  const hb = bot.nextElementSibling && bot.nextElementSibling.classList.contains("chat-hb");
  return {
    hasBlock: !!blk,
    open: blk ? blk.open : null,
    summary: blk ? blk.querySelector("summary").textContent : null,
    body: blk ? blk.querySelector(".chat-think-body").textContent : null,
    bodyLen: blk ? blk.querySelector(".chat-think-body").textContent.length : 0,
    bodyHtmlTags: blk ? blk.querySelector(".chat-think-body").children.length : 0,
    wikilinks: blk ? blk.querySelectorAll("a").length : 0,
    elidedShown: blk ? !blk.querySelector(".chat-think-elided").hidden : null,
    heartbeat: !!hb,
    displayed: blk ? getComputedStyle(blk).display !== "none" : null,
  };
}
"""


class Smoke:
    def __init__(self, page) -> None:
        self.page = page

    def feed(self, *frames: str) -> dict:
        self.page.evaluate(_FEED, [list(frames)])
        return self.page.evaluate(_STATE)

    def more(self, *frames: str) -> dict:
        self.page.evaluate(_MORE, [list(frames)])
        return self.page.evaluate(_STATE)

    def collapsed_by_default(self) -> str:
        st = self.feed(_frame("thinking", {"text": "先查"}), _frame("thinking", {"text": "索引"}))
        assert st["hasBlock"], "没有建块"
        assert st["open"] is False, "默认应折叠"
        assert st["body"] == "先查索引", st["body"]
        assert "4" in st["summary"], st["summary"]  # 字数摘要随追加更新
        return f"折叠、拼接、摘要「{st['summary']}」"

    def literal_text(self) -> str:
        st = self.feed(_frame("thinking", {"text": "<b>粗</b> [[某页]] <img src=x onerror=alert(1)>"}))
        assert st["bodyHtmlTags"] == 0, "推理被当 HTML 解析了"
        assert st["wikilinks"] == 0, "推理里的 [[页]] 被渲染成链接了"
        assert "[[某页]]" in st["body"] and "<b>" in st["body"]
        return "HTML / [[链接]] 原样字面显示"

    def heartbeat_cleared_and_rebuilt(self) -> str:
        st = self.feed(_frame("heartbeat", {"elapsed": 15}))
        assert st["heartbeat"], "心跳行没出来"
        st = self.more(_frame("thinking", {"text": "想"}))
        assert not st["heartbeat"], "thinking 帧没撤心跳（旧秒数会冻屏）"
        st = self.more(_frame("heartbeat", {"elapsed": 30}))
        assert st["heartbeat"], "推理后再静默，心跳没重建"
        return "撤 → 重建"

    def tail_cap(self) -> str:
        st = self.feed(_frame("thinking", {"text": "甲" * 40000}), _frame("thinking", {"text": "乙" * 30000}))
        assert st["bodyLen"] == 64 * 1024, st["bodyLen"]
        assert st["body"].endswith("乙"), "应保留尾部"
        assert st["elidedShown"], "截断提示没显示"
        assert "70000" in st["summary"], st["summary"]  # 摘要报总字数，不是显示字数
        return f"显示 {st['bodyLen']} 字、摘要「{st['summary']}」"

    def reasoning_only_opens(self) -> str:
        st = self.feed(
            _frame("thinking", {"text": "只想不答"}),
            _frame("done", {"conversation_id": "00000000-0000-4000-8000-000000000001",
                            "answer": "", "incomplete": {"reason": "reasoning_only"}}),
        )
        assert st["open"] is True, "reasoning_only 时应自动展开"
        st2 = self.feed(
            _frame("thinking", {"text": "想完了"}),
            _frame("done", {"conversation_id": "00000000-0000-4000-8000-000000000001",
                            "answer": "答案", "answer_html": "<p>答案</p>"}),
        )
        assert st2["open"] is False, "正常答完不应自动展开"
        return "仅 reasoning_only 展开"

    def no_thinking_no_block(self) -> str:
        st = self.feed(_frame("token", "直接答"))
        assert not st["hasBlock"], "没有推理却建了空块"
        return "无推理不建块"

    def pref_off(self) -> str:
        before = self.feed(_frame("thinking", {"text": "旧块"}))
        assert before["displayed"]
        self.page.click("#thinking-btn")
        pressed = self.page.get_attribute("#thinking-btn", "aria-pressed")
        assert pressed == "false", pressed
        st_old = self.page.evaluate(_STATE)
        assert st_old["displayed"] is False, "关掉后已有块没隐藏"
        st = self.feed(_frame("heartbeat", {"elapsed": 15}), _frame("thinking", {"text": "新推理"}))
        assert not st["hasBlock"], "关掉后仍建了新块"
        assert not st["heartbeat"], "关掉后 thinking 帧也须撤心跳"
        self.page.reload()
        self.page.wait_for_function("typeof handleSSE === 'function'")
        assert self.page.get_attribute("#thinking-btn", "aria-pressed") == "false", "偏好没记住"
        self.page.click("#thinking-btn")  # 复位，免得影响后续场景
        assert self.page.get_attribute("#thinking-btn", "aria-pressed") == "true"
        return "隐藏旧块、不建新块、照撤心跳、刷新后记住"

    def goal_stream(self) -> str:
        n = self.page.evaluate(
            """([frames]) => {
                const ctx = { botEl: null, banner: null };
                for (const f of frames) handleGoalSSE(f, ctx);
                const prev = ctx.botEl.previousElementSibling;
                return prev && prev.classList.contains("chat-think")
                  ? prev.querySelector(".chat-think-body").textContent : null;
            }""",
            [[_frame("turn_start", {"idx": 0}), _frame("thinking", {"text": "续跑轮推理"})]],
        )
        assert n == "续跑轮推理", n
        return "续跑流挂在内层轮气泡前"


SCENARIOS = [
    ("1. 默认折叠 + 拼接 + 字数摘要", "collapsed_by_default"),
    ("2. 纯文本：HTML 与 [[链接]] 字面显示", "literal_text"),
    ("3. thinking 撤心跳、再静默重建", "heartbeat_cleared_and_rebuilt"),
    ("4. 64 K 尾部上限 + 省略提示", "tail_cap"),
    ("5. reasoning_only 自动展开", "reasoning_only_opens"),
    ("6. 无推理不建块", "no_thinking_no_block"),
    ("7. 顶栏开关", "pref_off"),
    ("8. goal 续跑流", "goal_stream"),
]


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as td:
        kb = _make_kb(Path(td))
        server = _serve(create_app(kb), _free_port())
        base = f"http://127.0.0.1:{server.config.port}/"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base)
                page.wait_for_function("typeof handleSSE === 'function'")
                smoke = Smoke(page)
                for label, meth in SCENARIOS:
                    try:
                        detail = getattr(smoke, meth)()
                        results.append((label, True, detail))
                        print(f"  ✓ {label} — {detail}")
                    except Exception as exc:  # noqa: BLE001 — 单场景失败只记录
                        msg = str(exc).splitlines()[0][:140] if str(exc) else ""
                        results.append((label, False, f"{type(exc).__name__}: {msg}"))
                        print(f"  ✗ {label} — {type(exc).__name__}: {msg}")
                label = "9. 全程无页面脚本异常"
                results.append((label, not errors, "无" if not errors else str(errors[:2])))
                print(("  ✓ " if not errors else "  ✗ ") + f"{label} — {results[-1][2]}")
                browser.close()
        finally:
            server.should_exit = True
            time.sleep(0.2)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nP4.25 真浏览器冒烟：{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
