#!/usr/bin/env python3
"""P4.15 真浏览器端到端冒烟（headless Chromium / Playwright，docs/P4.15-Web工具确认.md）。

`test_web.py` 的 ASGI `TestClient` 只能验**后端 + SSE 帧**，验不了气泡真渲染 / 点击 / 倒计时 /
注入安全（同 P4.13/P4.14：渲染交互走真浏览器冒烟）。本脚本起一个**真 socket** 的 Web 宿主——
注入一个**消息驱动的 fake agent**（不打真实 LLM）：浏览器发什么消息，fake agent 就经 transport
调对应的 `confirm_tool`/`ask_user`，于是确认/提问气泡的真实生命周期可被 Playwright 端到端驱动。

消息协议（fake agent 据用户消息触发）：
  `shell: <命令>`   → confirm_tool("run_shell_command", …, {"command": <命令>})
  `ask: <问题>`     → ask_user(<问题>, options=["甲","乙"], allow_custom=True)
  `askfree: <问题>` → ask_user(<问题>)（无选项，纯自由文本）
  其它              → 直接作答（不弹）

**不入 pytest**（需浏览器 + 真 socket，CI 装不全）。手动跑：
    uv run --extra web python scripts/smoke_p415.py
首跑前装浏览器：`uv run python -m playwright install chromium`。
全过 → 退出码 0；任一断言失败 → 1；缺 playwright/chromium → 2（跳过、非失败）。
"""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

# ── 环境探测：缺 web extra / playwright / chromium 一律「跳过」（退出码 2），非失败 ──
try:
    import uvicorn
    from agentao.cancellation import AgentCancelledError
    from agentao.transport.events import AgentEvent, EventType

    import guanlan.web.chat as chat_mod
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


# 交互场景给宽裕超时（点击绝不抢在服务端自动拒绝之前——否则按钮被 disable，点击报「not enabled」）；
# 「超时自动拒绝」场景另起一个**短超时**的 app 单独验，几秒内出结果。
INTERACTIVE_TIMEOUT = 30.0
TIMEOUT_SCENARIO_TIMEOUT = 2.0


# ── 消息驱动的 fake agent（镜像 test_web.py 的线程模型：arun 把工作甩到 executor 线程，
#    从那里经 transport 发 LLM_TEXT / 调 confirm_tool / ask_user——逼出真实线程桥）──
class _Recorder:
    def __init__(self) -> None:
        self.calls: list = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))

        return rec


class _SkillMgr:
    def activate_skill(self, *a, **k) -> None:
        pass

    def get_active_skills(self) -> dict:
        return {}

    def list_available_skills(self) -> list:
        return []

    def get_skill_description(self, n: str) -> str:
        return ""


class _SmokeAgent:
    """据用户消息触发 confirm_tool/ask_user 的打桩 agent（不打 LLM）。"""

    def __init__(self, **opts) -> None:
        self.transport = opts["transport"]
        self.filesystem = opts.get("filesystem")
        self.messages: list[dict] = []
        self.permission_engine = _Recorder()
        self.tool_runner = _Recorder()
        self.skill_manager = _SkillMgr()
        self._model = opts.get("model")

    def get_current_model(self) -> str:
        return self._model or "smoke-model"

    def close(self) -> None:
        pass

    async def arun(self, msg: str, cancellation_token=None, images=None, **_kw) -> str:
        self.messages.append({"role": "user", "content": msg})
        loop = asyncio.get_running_loop()

        def work() -> str:
            for ch in "处理中… ":
                self.transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": ch}))
            body = msg.split(":", 1)[1].strip() if ":" in msg else ""
            if msg.startswith("shell:"):
                ok = self.transport.confirm_tool(
                    "run_shell_command", "运行 shell 命令", {"command": body}
                )
                return f"已{'执行' if ok else '取消'}：{body}"
            if msg.startswith("ask:"):
                ans = self.transport.ask_user(
                    body, options=["甲", "乙"], multiple=False, allow_custom=True
                )
                return f"你选了：{ans}"
            if msg.startswith("askfree:"):
                ans = self.transport.ask_user(body)
                return f"你答了：{ans}"
            return f"收到：{msg}"

        answer = await loop.run_in_executor(None, work)
        if cancellation_token is not None and cancellation_token.is_cancelled:
            raise AgentCancelledError(cancellation_token.reason)
        self.messages.append({"role": "assistant", "content": answer})
        return answer


def _make_kb(root: Path) -> Path:
    """最小合法知识库（满足 require_kb_root 写入口）。"""
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


# ── 冒烟场景（每个返回 (名称, 是否过, 详情)）。同一 page，按需点「新会话」隔离 ──
class Smoke:
    def __init__(self, page, base: str, confirm_timeout: float) -> None:
        self.page = page
        self.base = base
        self.confirm_timeout = confirm_timeout
        self.dialogs: list[str] = []
        page.on("dialog", lambda d: (self.dialogs.append(d.message), d.dismiss()))

    # —— 基础动作 ——
    def new_chat(self) -> None:
        self.page.click("#chat-new")

    def wait_idle(self) -> None:
        # 一轮收尾后 chatStreaming 回 false（流结束 setChatSending(false)）。chatStreaming 是
        # chat.js 顶层 let（全局词法绑定），可在页面上下文按裸名求值。
        self.page.wait_for_function(
            "typeof chatStreaming !== 'undefined' && chatStreaming === false", timeout=15000
        )

    def send(self, text: str) -> None:
        self.wait_idle()
        self.page.fill("#chat-input", text)
        self.page.press("#chat-input", "Enter")

    def last_confirm(self):
        return self.page.locator(".msg.interaction.confirm").last

    def last_ask(self):
        return self.page.locator(".msg.interaction.ask").last

    def wait_confirm(self, timeout=8000):
        loc = self.last_confirm()
        loc.wait_for(state="visible", timeout=timeout)
        return loc

    # —— 各场景 ——
    def confirm_allow(self):
        self.new_chat()
        self.send("shell: ls | wc -l")
        b = self.wait_confirm()
        cmd = b.locator(".interaction-cmd").inner_text()
        assert "ls | wc -l" in cmd, f"命令未原样显示：{cmd!r}"
        assert b.locator(".interaction-btn.allow").count() == 1
        assert b.locator(".interaction-btn.allow-session").count() == 1
        assert b.locator(".interaction-btn.deny").count() == 1
        b.locator(".interaction-btn.allow").click()
        b.locator(".interaction-status").wait_for(state="visible", timeout=8000)
        status = b.locator(".interaction-status").inner_text()
        assert "允许" in status, f"resolved 状态异常：{status!r}"
        self.wait_idle()
        return "命令原样显示 + 三按钮 + 点允许→已允许"

    def confirm_deny(self):
        self.new_chat()
        self.send("shell: rm -rf /")
        b = self.wait_confirm()
        b.locator(".interaction-btn.deny").click()
        b.locator(".interaction-status").wait_for(state="visible", timeout=8000)
        assert "拒绝" in b.locator(".interaction-status").inner_text()
        self.wait_idle()
        return "点拒绝→已拒绝、turn 仍收尾"

    def confirm_countdown(self):
        self.new_chat()
        self.send("shell: du -sh .")
        b = self.wait_confirm()
        # 倒计时槽几拍内出现「<n>s」文案（startCountdown 的 setInterval 在跑）。
        deadline = time.monotonic() + 3
        cd = ""
        while time.monotonic() < deadline:
            cd = b.locator(".interaction-cd").inner_text()
            if any(c.isdigit() for c in cd) and "s" in cd:
                break
            time.sleep(0.1)
        assert any(c.isdigit() for c in cd) and "s" in cd, f"倒计时未渲染：{cd!r}"
        b.locator(".interaction-btn.deny").click()
        self.wait_idle()
        return f"倒计时渲染（{cd!r}）"

    def injection_safe(self):
        self.new_chat()
        evil = "echo <img src=x onerror=alert('XSS')> <script>alert('XSS2')</script>"
        self.send(f"shell: {evil}")
        b = self.wait_confirm()
        cmd = b.locator(".interaction-cmd").inner_text()
        assert "<img" in cmd and "<script>" in cmd, f"命令未字面显示：{cmd!r}"
        # 气泡里绝不能真造出 img/script 元素（textContent 字面显示、非 innerHTML）。
        assert b.locator("img").count() == 0, "气泡里冒出了真 <img>（注入未防住）"
        assert b.locator("script").count() == 0, "气泡里冒出了真 <script>"
        b.locator(".interaction-btn.deny").click()
        self.wait_idle()
        assert not self.dialogs, f"触发了弹窗（注入执行了）：{self.dialogs}"
        return "命令含 <img onerror>/<script> 仅字面显示、无元素、无 alert"

    def allow_session_and_restore(self):
        self.new_chat()
        # 第一条：点「本会话起自动放行」
        self.send("shell: cat a | grep b")
        b1 = self.wait_confirm()
        b1.locator(".interaction-btn.allow-session").click()
        b1.locator(".interaction-status").wait_for(state="visible", timeout=8000)
        # 翻 auto：出现自动放行提示 + 「恢复逐次确认」钮
        self.page.locator(".interaction-automode").last.wait_for(state="visible", timeout=8000)
        self.wait_idle()
        # 第二条：auto 下静默放行——不该再弹 confirm 气泡
        before = self.page.locator(".msg.interaction.confirm").count()
        self.send("shell: echo 2")
        self.wait_idle()
        after = self.page.locator(".msg.interaction.confirm").count()
        assert after == before, f"auto 模式仍弹了确认气泡（{before}→{after}）"
        # 点「恢复逐次确认」，**等 POST 落定**（出现“已恢复”提示）再发下一条——否则 pwd 可能在
        # confirm_mode 翻回 ask 之前到达、被 auto 静默放行，导致下方等不到新气泡。
        self.page.locator(".interaction-btn.restore").last.click()
        self.page.locator("text=已恢复").wait_for(state="visible", timeout=8000)
        # 第三条：又该弹气泡了——**等一个新的** confirm 气泡（index == 之前的总数），别点到旧的禁用气泡
        n = self.page.locator(".msg.interaction.confirm").count()
        self.send("shell: pwd")
        self.page.locator(".msg.interaction.confirm").nth(n).wait_for(state="visible", timeout=8000)
        self.page.locator(".msg.interaction.confirm").last.locator(".interaction-btn.deny").click()
        self.wait_idle()
        return "②翻 auto→下条静默放行无气泡→恢复后又弹"

    def ask_options(self):
        self.new_chat()
        self.send("ask: 选甲还是乙？")
        b = self.last_ask()
        b.wait_for(state="visible", timeout=8000)
        assert "选甲还是乙" in b.locator(".interaction-q").inner_text()
        assert b.locator(".interaction-opt").count() == 2, "选项未渲染成两项"
        b.locator(".interaction-opt input").first.check()  # 选「甲」
        b.locator(".interaction-btn.allow").click()  # 提交
        b.locator(".interaction-status").wait_for(state="visible", timeout=8000)
        self.wait_idle()
        bot = self.page.locator(".msg.bot").last.inner_text()
        assert "甲" in bot, f"答案未回传模型：{bot!r}"
        return "提问带选项→选甲→提交→模型收到甲"

    def ask_free_text(self):
        self.new_chat()
        self.send("askfree: 你叫什么？")
        b = self.last_ask()
        b.wait_for(state="visible", timeout=8000)
        free = b.locator(".interaction-free")
        assert free.count() == 1, "自由文本框未渲染"
        free.fill("观澜")
        b.locator(".interaction-btn.allow").click()
        b.locator(".interaction-status").wait_for(state="visible", timeout=8000)
        self.wait_idle()
        assert "观澜" in self.page.locator(".msg.bot").last.inner_text()
        return "纯自由文本提问→输入→回传"

    def timeout_auto_deny(self):
        self.new_chat()
        self.send("shell: sleep 9")
        b = self.wait_confirm()
        # 不点，等超时（confirm_timeout + buffer）→ 状态标超时拒绝
        b.locator(".interaction-status").wait_for(
            state="visible", timeout=int((self.confirm_timeout + 4) * 1000)
        )
        assert "超时" in b.locator(".interaction-status").inner_text()
        self.wait_idle()
        return f"无人应答 {self.confirm_timeout}s →超时已拒绝"

    def switch_clears_pending(self):
        self.new_chat()
        self.send("shell: top")
        self.wait_confirm()  # 气泡在、未决
        # 切「新会话」：应清掉气泡 + 清空 pendingInteractions 登记表（停掉倒计时 interval）
        self.new_chat()
        self.page.wait_for_timeout(200)
        left = self.page.evaluate("Object.keys(pendingInteractions).length")
        assert left == 0, f"切会话后 pendingInteractions 未清（剩 {left}）—— 倒计时 interval 泄漏"
        assert self.page.locator(".msg.interaction.confirm").count() == 0, "切会话后气泡残留"
        return "切会话清掉未决气泡 + 登记表（无 interval 泄漏）"


# 两组：交互组（宽超时，绝不让点击抢超时）+ 超时组（短超时，单独验自动拒绝）。
GROUPS = [
    (INTERACTIVE_TIMEOUT, [
        ("confirm 允许", "confirm_allow"),
        ("confirm 拒绝", "confirm_deny"),
        ("倒计时渲染", "confirm_countdown"),
        ("注入安全", "injection_safe"),
        ("②本会话起自动放行 + 可逆", "allow_session_and_restore"),
        ("ask 选项往返", "ask_options"),
        ("ask 自由文本往返", "ask_free_text"),
        ("切会话清未决（防泄漏）", "switch_clears_pending"),
    ]),
    (TIMEOUT_SCENARIO_TIMEOUT, [
        ("超时自动拒绝", "timeout_auto_deny"),
    ]),
]


def _run_group(pw, kb: Path, confirm_timeout: float, scenarios) -> list[tuple[str, bool, str]]:
    """起一个该超时的 app（真 socket）+ 一个浏览器页，按序跑该组场景。"""
    app = create_app(
        kb, mode="workspace-write", confirm="ask", confirm_timeout=confirm_timeout
    )
    server = _serve(app, _free_port())
    base = f"http://127.0.0.1:{server.config.port}/"
    results: list[tuple[str, bool, str]] = []
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        console_errors: list[str] = []
        page.on("console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.goto(base, wait_until="networkidle")
        page.wait_for_selector("#chat-input", timeout=10000)
        smoke = Smoke(page, base, confirm_timeout)
        for label, meth in scenarios:
            try:
                detail = getattr(smoke, meth)()
                results.append((label, True, detail))
                print(f"  ✓ {label} — {detail}")
            except Exception as exc:  # noqa: BLE001 — 任一场景失败只记录、不中断后续
                msg = str(exc).splitlines()[0][:120]  # Playwright 异常含巨长 call log，只取首行
                results.append((label, False, f"{type(exc).__name__}: {msg}"))
                print(f"  ✗ {label} — {type(exc).__name__}: {msg}")
        browser.close()
        if console_errors:
            print(f"  ⚠ 浏览器 console error（{len(console_errors)}）：{console_errors[:3]}")
    finally:
        server.should_exit = True
        time.sleep(0.2)
    return results


def main() -> int:
    chat_mod.build_from_environment = lambda **opts: _SmokeAgent(**opts)
    chat_mod.ensure_skill_available = lambda _kb: None

    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as td:
        kb = _make_kb(Path(td))
        with sync_playwright() as pw:
            for confirm_timeout, scenarios in GROUPS:
                results += _run_group(pw, kb, confirm_timeout, scenarios)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\nP4.15 真浏览器冒烟：{passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
