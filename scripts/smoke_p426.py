#!/usr/bin/env python3
"""P4.26 真浏览器冒烟（headless Chromium / Playwright，docs/P4.26-Web子Agent进展跟踪.md §5）。

`test_web.py` 只验服务端帧（`subagent` 的白名单投影与保序）；按 `id` 配对、行的建立/更新/终态、行数上限、
四种收尾出口与 `finally` 的幂等，都是前端行为，只能在真浏览器里看。本脚本起一个**真 socket** 的 Web 宿主：
前几个场景把手写帧喂给**真的** `handleSSE`；收尾场景用 Playwright 拦截 `/api/chat` 与 `/goal/run`、回一段
手写 SSE 流，驱动**真的** `sendChat` / `sendGoalRun`（含它们的 `finally`）。**不打真实 LLM**、生产代码里不留测试钩子。

**不入 pytest**（需浏览器 + 真 socket）。手动跑：
    uv run --extra web python scripts/smoke_p426.py
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


CID = "00000000-0000-4000-8000-000000000001"


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


def _start(i: str, agent: str = "investigator") -> str:
    return _frame("subagent", {"phase": "start", "id": i, "agent": agent, "max_turns": 15})


def _tool(i: str, turn: int, tool: str) -> str:
    return _frame("subagent", {"phase": "tool", "id": i, "turn": turn, "max_turns": 15, "tool": tool})


def _end(i: str, state: str = "completed", **kw) -> str:
    return _frame("subagent", {"phase": "end", "id": i, "state": state, **kw})


_FEED = """
([frames]) => {
  const botEl = addMsg("bot", "");
  for (const f of frames) handleSSE(f, botEl);
  window.__bot = botEl;
}
"""
_MORE = "([frames]) => { for (const f of frames) handleSSE(f, window.__bot); }"
# 行是气泡之前的兄弟节点（botEl.before）：从当前气泡往前收，直到碰到非子 Agent 行。
_ROWS_OF = """
(bot) => {
  const rows = [];
  let el = bot.previousElementSibling;
  while (el && el.classList.contains("chat-subagent")) { rows.unshift(el.textContent); el = el.previousElementSibling; }
  return {
    rows,
    open: bot._subagents ? bot._subagents.size : 0,
    heartbeat: !!(bot.nextElementSibling && bot.nextElementSibling.classList.contains("chat-hb")),
    nameTags: bot.parentNode.querySelectorAll(".chat-subagent b").length,
  };
}
"""
_STATE = f"() => ({_ROWS_OF})(window.__bot)"
# 最后一个气泡（sendChat / sendGoalRun 自己建的）：bot 或出错后换成 err 的。
_LAST = f"() => ({_ROWS_OF})([...document.querySelectorAll('#chat-log .msg.bot, #chat-log .msg.err')].pop())"


class Smoke:
    def __init__(self, page) -> None:
        self.page = page

    def feed(self, *frames: str) -> dict:
        self.page.evaluate(_FEED, [list(frames)])
        return self.page.evaluate(_STATE)

    def more(self, *frames: str) -> dict:
        self.page.evaluate(_MORE, [list(frames)])
        return self.page.evaluate(_STATE)

    def lifecycle(self) -> str:
        st = self.feed(_start("a"))
        assert st["rows"] == ["⚙ investigator · 运行中"], st["rows"]
        st = self.more(_tool("a", 2, "read_file"))
        assert st["rows"] == ["⚙ investigator · 运行中 · 第 2/15 轮 · read_file"], st["rows"]
        st = self.more(_end("a", turns=3, tool_calls=5, duration_ms=12400))
        assert st["rows"] == ["⚙ investigator · 已完成 · 3 轮 · 5 次工具 · 12s"], st["rows"]
        assert st["open"] == 0
        st = self.more(_tool("a", 9, "late"), _end("zz"))  # 已结束 / 未登记的 id：忽略，不凭空建行
        assert len(st["rows"]) == 1 and "late" not in st["rows"][0]
        return st["rows"][0]

    def same_name_interleaved(self) -> str:
        st = self.feed(
            _start("a"), _start("b"), _tool("b", 1, "grep"), _tool("a", 1, "read_file"),
            _end("b", "failed", duration_ms=4000), _end("a", "cancelled"), _end("c", "weird"),
        )
        assert st["rows"] == ["⚙ investigator · 已取消", "⚙ investigator · 失败 · 4s"], st["rows"]
        return "两行各收各的终态"

    def literal_and_unknown_state(self) -> str:
        st = self.feed(_start("a", "<b>x</b>"), _end("a", "weird"))
        assert st["nameTags"] == 0, "名称被当 HTML 解析了"
        assert st["rows"] == ["⚙ <b>x</b> · weird"], st["rows"]
        return "名称字面显示、未知终态显示原值"

    def heartbeat_cleared(self) -> str:
        st = self.feed(_frame("heartbeat", {"elapsed": 15}))
        assert st["heartbeat"]
        st = self.more(_start("a"))
        assert not st["heartbeat"], "subagent 帧没撤心跳（旧秒数会冻屏）"
        return "撤心跳"

    def row_cap(self) -> str:
        frames = [_start(f"r{i}") for i in range(25)] + [_end("r0"), _end("r24")]
        st = self.feed(*frames)
        assert len(st["rows"]) == 21, len(st["rows"])  # 20 行 + 1 行省略计数
        assert st["rows"][-1] == "⚙ 另有 5 次子 Agent 调用未显示", st["rows"][-1]
        assert st["rows"][0].endswith("已完成"), "已显示行收不到终态"
        assert st["open"] == 19
        return "20 行 + 省略计数、已显示行仍收终态"

    # ── 收尾出口：拦截流、驱动真 sendChat / sendGoalRun（含 finally）──

    def _route(self, pattern: str, frames: list[str]) -> None:
        body = "".join(f + "\n\n" for f in frames)
        self.page.unroute(pattern)
        self.page.route(pattern, lambda r: r.fulfill(status=200, content_type="text/event-stream", body=body))

    def _check_exit(self, st: dict, label: str) -> None:
        assert st["rows"] == ["⚙ investigator · 已完成", "⚙ investigator · 未收到结束"], (label, st["rows"])
        assert st["open"] == 0, (label, "收尾后映射未清空")

    def chat_exits(self) -> str:
        head = [_frame("start", {"conversation_id": CID}), _start("a"), _start("b"), _end("a")]
        exits = {
            "done": [_frame("done", {"conversation_id": CID, "answer": "答"})],
            "stopped": [_frame("stopped", {"conversation_id": CID})],
            "error": [_frame("error", {"conversation_id": CID, "message": "坏了"})],
            "断流": [],
        }
        for label, tail in exits.items():
            self._route("**/api/chat", head + tail)
            self.page.evaluate("() => sendChat('问')")
            self._check_exit(self.page.evaluate(_LAST), label)
        return "done / stopped / error / 断流"

    def goal_exits(self) -> str:
        self.page.evaluate(f"() => setConversation('{CID}')")
        head = [_frame("goal_start", {}), _frame("turn_start", {"idx": 0}), _start("a"), _start("b"), _end("a")]
        exits = {
            "turn_done": [_frame("turn_done", {"answer": "答"}), _frame("goal_done", {"status": "complete"})],
            "停止": [_frame("turn_done", {"answer": "", "stopped": True}), _frame("goal_done", {"status": "paused"})],
            "error": [_frame("error", {"message": "坏了"})],
            "断流": [],
        }
        for label, tail in exits.items():
            self._route("**/goal/run", head + tail)
            self.page.evaluate("() => sendGoalRun()")
            self._check_exit(self.page.evaluate(_LAST), label)
        return "turn_done / 停止 / error / 断流"


SCENARIOS = [
    ("1. 建立 → 更新 → 终态；迟到与未登记帧忽略", "lifecycle"),
    ("2. 两次同名运行交错，按 id 各占一行", "same_name_interleaved"),
    ("3. 名称字面显示 + 未知终态原值", "literal_and_unknown_state"),
    ("4. subagent 帧撤心跳", "heartbeat_cleared"),
    ("5. 每气泡 20 行上限 + 省略计数", "row_cap"),
    ("6. 普通流四种收尾（含 finally 幂等）", "chat_exits"),
    ("7. /goal 流四种收尾（含 finally 幂等）", "goal_exits"),
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
                page.wait_for_function("typeof handleSubagent === 'function'")
                smoke = Smoke(page)
                for label, meth in SCENARIOS:
                    try:
                        detail = getattr(smoke, meth)()
                        results.append((label, True, detail))
                        print(f"  ✓ {label} — {detail}")
                    except Exception as exc:  # noqa: BLE001 — 单场景失败只记录
                        msg = str(exc).splitlines()[0][:200] if str(exc) else ""
                        results.append((label, False, f"{type(exc).__name__}: {msg}"))
                        print(f"  ✗ {label} — {type(exc).__name__}: {msg}")
                label = "8. 全程无页面脚本异常"
                results.append((label, not errors, "无" if not errors else str(errors[:2])))
                print(("  ✓ " if not errors else "  ✗ ") + f"{label} — {results[-1][2]}")
                browser.close()
        finally:
            server.should_exit = True
            time.sleep(0.2)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\nP4.26 真浏览器冒烟：{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
