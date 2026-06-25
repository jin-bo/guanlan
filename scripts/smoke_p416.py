#!/usr/bin/env python3
"""P4.16 真浏览器端到端冒烟（headless Chromium / Playwright，docs/P4.16-Web目标续跑.md）。

`test_web.py` 的 ASGI `TestClient` 能验**后端 + SSE 帧**（goal 套件即如此），但验不了：goal **横幅**与
每内层轮**独立气泡**的真渲染、`/goal` 斜杠支线的前端编排、以及「自省/设目标命令无会话时**自动开空活
会话**」（P4.4 UX 反转）这条纯前端路径。本脚本起一个**真 socket** 的 Web 宿主——注入一个**目标驱动的
fake agent**（不打真实 LLM）：续跑 prompt 里嵌的 objective 带标记位，agent 据此在指定轮经注入的
`update_goal` 工具自报 complete、或慢流便于中途 `/goal pause`。于是 goal 横幅/气泡/续跑/暂停的真实
生命周期可被 Playwright 端到端驱动。

objective 标记位（写进 `/goal <objective>` 即可，会显示在横幅上、仅冒烟用）：
  `[complete@N]`  → agent 在第 N 个 goal 工作轮调 update_goal(status="complete")（提前收）
  `[slow]`        → 每轮慢流（便于在流式中途插 `/goal pause`）
  （无标记）       → 不自报，跑到轮数预算 → 恰好一次 wrap-up 收尾轮 → limit_reached

**不入 pytest**（需浏览器 + 真 socket，CI 装不全）。手动跑：
    uv run --extra web python scripts/smoke_p416.py
首跑前装浏览器：`uv run python -m playwright install chromium`。
全过 → 退出码 0；任一断言失败 → 1；缺 playwright/chromium → 2（跳过、非失败）。
"""

from __future__ import annotations

import asyncio
import re
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


# ── 打桩自省面（让无会话自动开会话后的 /tools 取数不崩；镜像 test_web 的 _FakeAgent 自省面）──
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


class _Ctx:
    """打桩 context_manager：/context·/status 取用量（messages-only headline，对齐 agentao）。"""

    def estimate_tokens_breakdown(self, messages, tools=None) -> dict:
        msg_tok = 10 * len([m for m in messages if m.get("role") != "system"])
        return {"system": 0, "messages": msg_tok, "tools": 30 if tools else 0,
                "total": msg_tok + (30 if tools else 0)}

    def get_usage_stats(self, messages, tools=None) -> dict:
        bd = self.estimate_tokens_breakdown(messages, tools=tools)
        return {"estimated_tokens": bd["total"], "token_count_source": "local",
                "max_tokens": 1000, "usage_percent": round(bd["total"] / 1000 * 100, 1),
                "message_count": len(messages), "token_breakdown": bd}


class _Tool:
    """打桩工具：带 /tools 自省所需的 name/description/is_read_only（只读放行、不被标灰）。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"打桩工具 {name}"
        self.requires_confirmation = False
        self.is_read_only = True


class _Reg:
    """镜像真 agentao ToolRegistry（name→tool dict + add(replace=)/remove/list_tools）：
    P4.16 loop 作用域注入/移除 update_goal 即经此（_snapshot_prior_update_goal 读 `.tools` dict）。"""

    def __init__(self, tools) -> None:
        self._tools = list(tools)

    @property
    def tools(self) -> dict:
        return {t.name: t for t in self._tools}

    def get(self, name):
        return self.tools.get(name)

    def add(self, tool, *, replace: bool = False) -> None:
        if any(t.name == tool.name for t in self._tools):
            if not replace:
                raise ValueError(f"tool {tool.name!r} already registered")
            self._tools = [t for t in self._tools if t.name != tool.name]
        self._tools.append(tool)

    def remove(self, name: str) -> bool:
        before = len(self._tools)
        self._tools = [t for t in self._tools if t.name != name]
        return len(self._tools) != before

    def list_tools(self) -> list:
        return list(self._tools)

    def to_openai_format(self, plan_mode=False) -> list:
        return [{"type": "function", "function": {"name": t.name}} for t in self._tools]


def _extract_objective(msg: str) -> str:
    """从续跑/收尾 prompt 抠出 objective（嵌在 `<goal>…</goal>`），首轮 prompt=裸 objective。"""
    if "<goal>" in msg and "</goal>" in msg:
        return msg.split("<goal>", 1)[1].split("</goal>", 1)[0].strip()
    return msg.strip()


class _GoalAgent:
    """目标驱动的打桩 agent（不打 LLM）。arun 从 executor 线程经 transport 慢/快流（镜像真
    arun 的 run_in_executor 线程模型）；据续跑 prompt 里 objective 的 `[complete@N]`/`[slow]`
    标记，在第 N 个 goal 工作轮经注入的 update_goal 自报 complete、或慢流便于中途 pause。"""

    def __init__(self, **opts) -> None:
        self.transport = opts["transport"]
        self.filesystem = opts.get("filesystem")
        self.messages: list[dict] = []
        self.permission_engine = _Recorder()
        self.tool_runner = _Recorder()
        self.skill_manager = _SkillMgr()
        self.context_manager = _Ctx()
        self.tools = _Reg([_Tool("read_file"), _Tool("guanlan_search")])
        self._model = opts.get("model")
        self._plan_mode = False
        self._goal_turn = 0  # 本会话已跑的 goal 工作轮数（不含 wrap-up）

    def get_current_model(self) -> str:
        return self._model or "smoke-model"

    def _build_system_prompt(self) -> str:
        return "SYS"

    def add_tool(self, tool, *, replace: bool = False) -> None:
        self.tools.add(tool, replace=replace)

    def remove_tool(self, name: str) -> bool:
        return self.tools.remove(name)

    def close(self) -> None:
        pass

    async def arun(self, msg: str, cancellation_token=None, images=None, **_kw) -> str:
        self.messages.append({"role": "user", "content": msg})
        loop = asyncio.get_running_loop()
        obj = _extract_objective(msg)
        is_wrapup = "reached this goal's time/turn budget" in msg
        slow = "[slow]" in obj

        def work() -> str:
            text = ("推进中…………………… " if slow else "推进：") + obj
            for ch in text:
                # 镜像真 agent 的检查点：令牌被 cancel（/goal pause→request_stop）即时抛，
                # 让中途 pause 在当前字符粒度内打断本轮（run_goal 收为 paused）。
                if cancellation_token is not None and cancellation_token.is_cancelled:
                    raise AgentCancelledError(cancellation_token.reason)
                self.transport.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": ch}))
                if slow:
                    time.sleep(0.12)
            # goal 工作轮（注入了 update_goal、且非 wrap-up）：到 [complete@N] 即自报完成。
            if not is_wrapup and self.tools.get("update_goal") is not None:
                self._goal_turn += 1
                m = re.search(r"\[complete@(\d+)\]", obj)
                if m and self._goal_turn >= int(m.group(1)):
                    self.tools.get("update_goal").execute("complete")
            return text

        answer = await loop.run_in_executor(None, work)
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


class Smoke:
    """每个场景：page.goto 重载到无会话洁净态 → 跑 → 返回详情串。横幅/气泡/横幅终态用语言
    无关的 class 断言（goal-complete/goal-limit_reached/goal-paused），不依赖 i18n 文案。"""

    def __init__(self, page, base: str) -> None:
        self.page = page
        self.base = base

    # —— 基础动作 ——
    def fresh(self) -> None:
        """重载到无会话洁净态（无 ?c=）：conversationId=null，验「自动开会话」从零起。"""
        self.page.goto(self.base, wait_until="networkidle")
        self.page.wait_for_selector("#chat-input", timeout=10000)
        assert self.page.evaluate("conversationId") in (None, ""), "重载后仍有活动会话"

    def type_now(self, text: str) -> None:
        """不等流结束就发（流式中途插 /goal pause 用）。"""
        self.page.fill("#chat-input", text)
        self.page.press("#chat-input", "Enter")

    def send(self, text: str) -> None:
        self.page.wait_for_function(
            "typeof chatStreaming !== 'undefined' && chatStreaming === false", timeout=15000
        )
        self.type_now(text)

    def wait_idle(self, timeout=20000) -> None:
        self.page.wait_for_function(
            "typeof chatStreaming !== 'undefined' && chatStreaming === false", timeout=timeout
        )

    def banner(self):
        return self.page.locator(".goal-banner").last

    # —— 各场景 ——
    def goal_autoopen_complete(self):
        """无会话设目标 → 自动开空活会话（采纳为当前会话、写 ?c=）→ 横幅 + 多气泡续跑 →
        agent 第 2 轮自报 complete → 提前收、无 wrap-up、横幅终态 goal-complete。"""
        self.fresh()
        self.send("/goal [complete@2] 攻克X --turns 5")
        b = self.banner()
        b.wait_for(state="visible", timeout=8000)
        # 自动开会话：conversationId 被采纳 + URL 写了 ?c=
        cid = self.page.evaluate("conversationId")
        assert cid, "设目标未自动开会话（conversationId 仍空）"
        assert "c=" in self.page.url, f"未写 ?c= 续聊参数：{self.page.url}"
        assert "攻克X" in b.locator(".goal-banner-obj").inner_text()
        self.wait_idle()
        labels = self.page.locator(".goal-turn-label").count()
        assert labels >= 2, f"goal 工作轮气泡 < 2（{labels}）"
        b.locator("xpath=self::*[contains(@class,'goal-complete')]").wait_for(timeout=8000)
        assert self.page.locator('.msg.bot[data-kind="wrap_up"]').count() == 0, \
            "提前 complete 不该有 wrap-up 收尾轮"
        return f"自动开会话(cid={cid[:8]}…)+横幅+{labels}轮+complete 提前收、无 wrap-up"

    def goal_limit_wrapup(self):
        """不自报 → 撞轮数预算 → 恰好一次 wrap-up 收尾轮（气泡带 data-kind=wrap_up）→
        横幅终态 goal-limit_reached。"""
        self.fresh()
        self.send("/goal 攻克Y --turns 2")
        b = self.banner()
        b.wait_for(state="visible", timeout=8000)
        self.wait_idle()
        labels = self.page.locator(".goal-turn-label").count()
        assert labels == 3, f"应 2 工作轮 + 1 wrap-up = 3 轮标签，实得 {labels}"
        wrap = self.page.locator('.msg.bot[data-kind="wrap_up"]')
        assert wrap.count() == 1, f"wrap-up 气泡数应为 1，实得 {wrap.count()}"
        b.locator("xpath=self::*[contains(@class,'goal-limit_reached')]").wait_for(timeout=8000)
        return "2 工作轮 + 1 wrap-up（data-kind=wrap_up）+ 横幅 limit_reached"

    def goal_pause_midrun(self):
        """慢流续跑中途插 `/goal pause`（流式中仍可输入瞬时命令）→ 打断在飞轮 → 横幅终态
        goal-paused。"""
        self.fresh()
        self.send("/goal [slow] 攻克Z --turns 8")
        b = self.banner()
        b.wait_for(state="visible", timeout=8000)
        # 等第一轮气泡开始流（出现轮标签即在飞），随即插 pause（不等流结束）
        self.page.locator(".goal-turn-label").first.wait_for(state="visible", timeout=8000)
        self.page.wait_for_function(
            "typeof chatStreaming !== 'undefined' && chatStreaming === true", timeout=8000
        )
        self.type_now("/goal pause")
        b.locator("xpath=self::*[contains(@class,'goal-paused')]").wait_for(timeout=12000)
        self.wait_idle()
        return "慢流中途 /goal pause → 打断在飞轮、横幅终态 paused"

    def goal_show_no_autoopen(self):
        """`/goal show`（对既有目标的操作）无会话时**不**自动开会话——落 goal.needSession 提示、
        conversationId 仍空（对照 set 才开会话，决策P4.4-7）。"""
        self.fresh()
        self.type_now("/goal show")
        note = self.page.locator(".msg.note").last
        note.wait_for(state="visible", timeout=8000)
        self.page.wait_for_timeout(300)
        assert self.page.evaluate("conversationId") in (None, ""), \
            "/goal show 不应自动开会话"
        txt = note.inner_text()
        assert ("先提一个问题" in txt or "ask a question" in txt), \
            f"未落「需先开会话」提示：{txt!r}"
        return "/goal show 无会话→提示、不自动开会话"

    def tools_autoopen(self):
        """`/tools`（自省、需 agent）无会话时**自动开空活会话**再渲染工具表——不再落「需要活动
        会话」死提示（P4.4 UX 反转）。验另一条 shipping 路径（slashInfo→ensureActiveConversation）。"""
        self.fresh()
        self.type_now("/tools")
        # 自动开会话后渲染自省 note；conversationId 被采纳
        self.page.wait_for_function("!!conversationId", timeout=8000)
        note = self.page.locator(".msg.note").last
        note.wait_for(state="visible", timeout=8000)
        txt = note.inner_text()
        assert "需要活动会话" not in txt and "needs an active session" not in txt, \
            f"/tools 仍落死提示、未自动开会话：{txt!r}"
        return "/tools 无会话→自动开会话+渲染工具自省、无死提示"


SCENARIOS = [
    ("设目标自动开会话 + 续跑 + 提前 complete", "goal_autoopen_complete"),
    ("撞轮数预算 + wrap-up 收尾轮", "goal_limit_wrapup"),
    ("续跑中途 /goal pause", "goal_pause_midrun"),
    ("/goal show 无会话不自动开会话", "goal_show_no_autoopen"),
    ("/tools 无会话自动开会话", "tools_autoopen"),
]


def _run(pw, kb: Path) -> list[tuple[str, bool, str]]:
    """起一个 goal-on 的 read-only app（真 socket）+ 一个浏览器页，按序跑各场景。"""
    app = create_app(kb, goal_enabled=True)  # read-only 默认；goal 续跑不需可写姿态
    server = _serve(app, _free_port())
    base = f"http://127.0.0.1:{server.config.port}/"
    results: list[tuple[str, bool, str]] = []
    try:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        console_errors: list[str] = []
        page.on("console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None)
        smoke = Smoke(page, base)
        for label, meth in SCENARIOS:
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
    chat_mod.build_from_environment = lambda **opts: _GoalAgent(**opts)
    chat_mod.ensure_skill_available = lambda _kb: None

    with tempfile.TemporaryDirectory() as td:
        kb = _make_kb(Path(td))
        with sync_playwright() as pw:
            results = _run(pw, kb)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\nP4.16 真浏览器冒烟：{passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
