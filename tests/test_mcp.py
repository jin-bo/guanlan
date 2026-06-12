"""P4.10 MCP 宿主测试（见 docs/P4.10-MCP宿主.md §7）。**`ask` 的 LLM 打桩，不打真实 LLM。**

主体在装有 `guanlan-wiki[mcp]` 时跑，缺 extra 整体 skip（镜像 test_web 的 `importorskip`）。依赖门控
本身（缺 extra → CLI 优雅降级）在 `tests/test_cli.py::test_mcp_missing_extra_degrades`（不随本组 skip）。

测试经官方 SDK 的 **in-memory client/server 会话**（`create_connected_server_and_client_session`）做
真实 JSON-RPC 往返：验 `structuredContent` + 文本块 parsed-equal、in-band error、tools/list 等；坏类型
防御（决策P4.10-12）直接调 `tools.tool_search` 同步核（绕过 FastMCP 入参校验，证防御逻辑本身）。
"""

import asyncio
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from guanlan.runtime import AgentRunResult

pytest.importorskip("mcp")

from mcp.shared.memory import (  # noqa: E402
    create_connected_server_and_client_session as connect,
)

from guanlan.mcp import server as mcp_server  # noqa: E402
from guanlan.mcp import tools as mcp_tools  # noqa: E402
from guanlan.mcp.server import build_mcp, serve_mcp  # noqa: E402

# ───────────────────────── 夹具 ─────────────────────────

_FM = "---\ntitle: {title}\ntype: {type}\n{aliases}---\n\n{body}\n"


def _write(wiki: Path, rel: str, *, title: str, type="entity", aliases="", body="正文内容。") -> Path:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    al = f"aliases: [{aliases}]\n" if aliases else ""
    p.write_text(_FM.format(title=title, type=type, aliases=al, body=body), encoding="utf-8")
    return p


@pytest.fixture
def kb_mcp(tmp_path: Path) -> Path:
    """带两张内容页 + 一条断链的最小知识库（满足只读 require_kb_root + 有可检索内容）。"""
    (tmp_path / "AGENTAO.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "SCHEMA.md").write_text("# S\n", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n- [DeFi](entities/DeFi.md)\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    _write(
        wiki,
        "entities/DeFi.md",
        title="DeFi",
        aliases="去中心化金融",
        body="DeFi 是链上借贷与交易的核心，引用 [[流动性挖矿]] 与 [[流动性挖矿]]。",
    )
    _write(wiki, "concepts/Liquidity.md", title="流动性", type="concept", body="流动性是市场深度的度量。")
    return tmp_path


def _ok_runner(prompt, **kw):
    return AgentRunResult(ok=True, final_text="DeFi 是去中心化金融，见 [[DeFi]]。")


def _run(mcp, coro_fn):
    """开一个 in-memory client/server 会话、跑 `coro_fn(client)`、返回其结果。"""

    async def _main():
        async with connect(mcp) as client:
            return await coro_fn(client)

    return asyncio.run(_main())


# ───────────────────────── 工具集形态 / 写工具不存在 ─────────────────────────


def test_tools_listed(kb_mcp):
    """tools/list = 七个只读工具，且**无**任何写工具（决策P4.10-3/5/§7 写工具不存在）。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    res = _run(mcp, lambda c: c.list_tools())
    names = {t.name for t in res.tools}
    assert names == {"search", "read_page", "list_pages", "graph", "health", "lint", "ask"}
    # 写工具不是「注册后拒绝」，是**根本不注册**。
    for forbidden in ("ingest", "heal", "backfill", "raw", "upload", "write_file"):
        assert forbidden not in names


def test_every_tool_has_output_schema(kb_mcp):
    """每个工具据返回类型注解（TypedDict）自动生成 output schema（决策P4.10-10/14）。

    漏注解会退化为纯文本串、失对象契约——本断言即捕获该退化。
    """
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    res = _run(mcp, lambda c: c.list_tools())
    for t in res.tools:
        assert t.outputSchema, f"{t.name} 缺 output schema（漏返回类型注解？）"


# ───────────────────────── 结构化输出契约 + 零 LLM 工具正确性 ─────────────────────────


def test_search_structured_and_text_parsed_equal(kb_mcp):
    """search 回 structuredContent + JSON 文本块，两路 parsed-equal（决策P4.10-10），
    且与 CLI/Web `search_result_dict` 字段同形。"""
    from guanlan.search import search_pages, search_result_dict

    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    r = _run(mcp, lambda c: c.call_tool("search", {"query": "去中心化金融", "limit": 5}))
    assert r.isError is False
    assert r.structuredContent  # 结构化对象
    assert json.loads(r.content[0].text) == r.structuredContent  # 文本块 parsed-equal
    # 与冷算 search_pages 经 search_result_dict 同形（薄壳、无逻辑分叉）。
    cold = search_result_dict(search_pages(kb_mcp / "wiki", "去中心化金融", limit=5))
    assert r.structuredContent == cold
    assert r.structuredContent["results"][0]["page"] == "wiki/entities/DeFi.md"


def test_read_page_matches_core(kb_mcp):
    """read_page 与 load_page 容错档同源：path/title/content 一致（薄壳）。"""
    from guanlan.pages import load_page, page_title

    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    r = _run(mcp, lambda c: c.call_tool("read_page", {"path": "wiki/entities/DeFi.md"}))
    meta, body = load_page(kb_mcp / "wiki/entities/DeFi.md")
    assert r.structuredContent == {
        "path": "wiki/entities/DeFi.md",
        "title": page_title(meta, "DeFi"),
        "content": body,
    }


def test_list_pages_matches_core(kb_mcp):
    """list_pages 与 iter_pages + page_title/page_type 同源（非 config 页、带 wiki/ 前缀）。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    r = _run(mcp, lambda c: c.call_tool("list_pages", {}))
    paths = {p["path"] for p in r.structuredContent["pages"]}
    assert paths == {"wiki/entities/DeFi.md", "wiki/concepts/Liquidity.md"}
    # config 页不在清单。
    assert not any("index.md" in p for p in paths)


def test_graph_matches_core(kb_mcp):
    """graph 与 graph_to_dict(build_graph) 同源（节点/边/stats）。"""
    from guanlan.graph import build_graph, graph_to_dict

    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    r = _run(mcp, lambda c: c.call_tool("graph", {}))
    assert r.structuredContent == graph_to_dict(build_graph(kb_mcp / "wiki"))


def test_health_lint_match_core_and_report_dict(kb_mcp):
    """health/lint 经 pages.report_dict 直接产 dict，与 run_health/run_lint 同源（决策P4.10-10）。"""
    from guanlan.health import run_health
    from guanlan.lint import run_lint
    from guanlan.pages import report_dict

    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    rh = _run(mcp, lambda c: c.call_tool("health", {}))
    rl = _run(mcp, lambda c: c.call_tool("lint", {}))
    hr = run_health(kb_mcp / "wiki")
    lr = run_lint(kb_mcp / "wiki")
    assert rh.structuredContent == report_dict(
        ok=hr.ok, pages_checked=hr.pages_checked, items_key="findings", items=hr.findings
    )
    assert rl.structuredContent == report_dict(
        ok=lr.ok, pages_checked=lr.pages_checked, items_key="findings", items=lr.findings
    )


def test_report_dict_split_preserves_report_json_bytes(kb_mcp):
    """`report_json` 拆出 `report_dict` 后对 CLI/Web 既有 JSON 字节契约零变更（无尾随换行）。"""
    from guanlan.health import run_health
    from guanlan.pages import report_dict, report_json

    hr = run_health(kb_mcp / "wiki")
    rj = report_json(
        ok=hr.ok, pages_checked=hr.pages_checked, items_key="findings", items=hr.findings
    )
    assert rj == json.dumps(
        report_dict(ok=hr.ok, pages_checked=hr.pages_checked, items_key="findings", items=hr.findings),
        ensure_ascii=False,
        indent=2,
    )
    assert not rj.endswith("\n")  # 无尾随换行


# ───────────────────────── path 单一口径 + search→read 链路 ─────────────────────────


def test_search_to_read_page_chain(kb_mcp):
    """search().results[i].page 带 wiki/ 前缀，**直接**喂 read_page 能读到同一页（无拼接/剥前缀）。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def chain(c):
        s = await c.call_tool("search", {"query": "流动性"})
        page = s.structuredContent["results"][0]["page"]
        rp = await c.call_tool("read_page", {"path": page})
        return page, rp

    page, rp = _run(mcp, chain)
    assert page.startswith("wiki/")
    assert rp.isError is False
    assert rp.structuredContent["path"] == page  # 不读成 wiki/wiki/...


def test_list_pages_path_feeds_read_page(kb_mcp):
    """list_pages().pages[i].path 同口径、可直接喂 read_page。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def chain(c):
        lp = await c.call_tool("list_pages", {})
        path = lp.structuredContent["pages"][0]["path"]
        return await c.call_tool("read_page", {"path": path})

    rp = _run(mcp, chain)
    assert rp.isError is False and rp.structuredContent["path"].startswith("wiki/")


# ───────────────────────── 路径越界 / error 总壳 ─────────────────────────


@pytest.mark.parametrize("bad", ["../outside.md", "/etc/passwd", "wiki/../../x.md"])
def test_read_page_traversal_blocked(kb_mcp, bad):
    """read_page 越界（../ / 绝对路径）→ in-band error，不读到 wiki/ 外（决策P4.10-9/16）。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    r = _run(mcp, lambda c: c.call_tool("read_page", {"path": bad}))
    assert r.isError is True


def test_error_total_shell_server_survives(kb_mcp, monkeypatch):
    """核函数抛异常 → in-band tool error、server 不崩、stdio 帧不破（决策P4.10-16）。

    令 `run_health` 注入抛错；断言 health 工具回 in-band 错误，**之后** search 仍正常——证 server 存活。
    """

    def boom(_wiki):
        raise RuntimeError("注入：health 崩了")

    monkeypatch.setattr(mcp_tools, "run_health", boom)
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def seq(c):
        bad = await c.call_tool("health", {})
        good = await c.call_tool("search", {"query": "去中心化金融"})
        return bad, good

    bad, good = _run(mcp, seq)
    assert bad.isError is True
    assert good.isError is False and good.structuredContent["results"]  # server 存活、其余工具可用


# ───────────────────────── limit / query 坏类型自防（决策P4.10-12，直接调核） ─────────────────────────


def test_search_tool_limit_clamp_and_bad_type(kb_mcp):
    """limit=0/负数 → clamp 到 1（至多 1 条）；None/"abc" → 回默认 10，均不 raise。"""
    from guanlan.search import CorpusCache

    cache = CorpusCache()
    wiki = kb_mcp / "wiki"
    # 0 / 负数 → clamp 到 1：结果至多 1 条（fixture 有多页命中"流动性"才稳，故用宽 query）。
    r0 = mcp_tools.tool_search("流动性", 0, search_cache=cache, wiki=wiki)
    assert len(r0["results"]) <= 1
    rneg = mcp_tools.tool_search("流动性", -5, search_cache=cache, wiki=wiki)
    assert len(rneg["results"]) <= 1
    # 坏类型 → 回默认 10，不崩。
    for bad in (None, "abc", [1, 2]):
        rb = mcp_tools.tool_search("流动性", bad, search_cache=cache, wiki=wiki)
        assert rb["ok"] is True  # 不 raise、回正常信封


@pytest.mark.parametrize("bad_query", [None, 123, ["a", "b"]])
def test_search_tool_bad_query_type(kb_mcp, bad_query):
    """query 非 str（None/number/array）→ str() 归一（None→空串），回正常空命中信封、不让 TypeError 冒泡。"""
    from guanlan.search import CorpusCache

    r = mcp_tools.tool_search(bad_query, 10, search_cache=CorpusCache(), wiki=kb_mcp / "wiki")
    assert r["ok"] is True and isinstance(r["results"], list)


# ───────────────────────── search 走长驻 cache（决策P4.10-11/15） ─────────────────────────


def test_search_uses_persistent_cache(kb_mcp, monkeypatch):
    """连续 search 复用同一 server CorpusCache：首搜建库、后续命中 memo 不重建未变页；
    且结果与冷算 search_pages 字节等价（长驻路径不引入逻辑分叉）。"""
    import guanlan.search as search_mod
    from guanlan.search import CorpusCache, search_pages, search_result_dict

    calls = {"build_doc": 0}
    real_build_doc = search_mod.build_doc

    def counting_build_doc(path, *, root):
        calls["build_doc"] += 1
        return real_build_doc(path, root=root)

    monkeypatch.setattr(search_mod, "build_doc", counting_build_doc)

    cache = CorpusCache()
    mcp = build_mcp(kb_mcp, runner=_ok_runner, search_cache=cache)

    async def two_searches(c):
        a = await c.call_tool("search", {"query": "去中心化金融"})
        b = await c.call_tool("search", {"query": "流动性"})
        return a, b

    a, b = _run(mcp, two_searches)
    # 两页内容页，首搜各 build 一次；第二搜全命中 memo → 不再 build。
    assert calls["build_doc"] == 2
    cold = search_result_dict(search_pages(kb_mcp / "wiki", "去中心化金融"))
    assert a.structuredContent == cold


def test_concurrent_searches_consistent(kb_mcp):
    """并发多个 search → 结果均正确、CorpusCache 无错乱重建（决策P4.10-15）。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def many(c):
        tasks = [c.call_tool("search", {"query": "去中心化金融"}) for _ in range(8)]
        return await asyncio.gather(*tasks)

    results = _run(mcp, many)
    first = results[0].structuredContent
    for r in results:
        assert r.isError is False
        assert r.structuredContent == first  # 并发不串、确定性一致


# ───────────────────────── ask 路径（决策P4.10-5/7/15） ─────────────────────────


def test_ask_returns_cited_answer(kb_mcp):
    """有模型（mock runner ok）→ 返回带 [[引用]] 的答案，read-only 姿态透传。"""
    seen = {}

    def runner(prompt, **kw):
        seen.update(kw)
        return AgentRunResult(ok=True, final_text="见 [[DeFi]]。")

    mcp = build_mcp(kb_mcp, runner=runner)
    r = _run(mcp, lambda c: c.call_tool("ask", {"question": "什么是 DeFi?"}))
    assert r.isError is False
    assert r.structuredContent == {"answer": "见 [[DeFi]]。"}
    assert seen["permission_mode"] == "read-only"  # 只读姿态（与 CLI query 同源）


def test_ask_model_resolution(kb_mcp):
    """model = 工具入参 or 启动 --model or None（不照搬 Web body.model 口径）。"""
    seen = []

    def runner(prompt, **kw):
        seen.append(kw.get("model"))
        return AgentRunResult(ok=True, final_text="x")

    mcp = build_mcp(kb_mcp, model="startup-M", runner=runner)
    _run(mcp, lambda c: c.call_tool("ask", {"question": "q"}))  # 无入参 → 用启动 model
    _run(mcp, lambda c: c.call_tool("ask", {"question": "q", "model": "arg-M"}))  # 入参覆盖
    assert seen == ["startup-M", "arg-M"]


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_ask_blank_question_rejected_without_calling_agent(kb_mcp, blank):
    """空/纯空白 question 就地拒（in-band error），**不**拉起 agentao 子进程（与 Web backfill 同口径）。"""
    called = {"n": 0}

    def runner(prompt, **kw):
        called["n"] += 1
        return AgentRunResult(ok=True, final_text="x")

    mcp = build_mcp(kb_mcp, runner=runner)
    r = _run(mcp, lambda c: c.call_tool("ask", {"question": blank}))
    assert r.isError is True
    assert called["n"] == 0  # 未白白拉起昂贵子进程


@pytest.mark.parametrize(
    "error_type",
    ["runtime_error", "permission_denied", "invalid_spec", None],
)
def test_ask_failure_is_in_band(kb_mcp, error_type):
    """任意 AgentRunResult(ok=False) → in-band tool error（无模型只是其一），且不影响零 LLM 工具。"""

    def bad_runner(prompt, **kw):
        return AgentRunResult(ok=False, final_text="失败原因", error_type=error_type)

    mcp = build_mcp(kb_mcp, runner=bad_runner)

    async def seq(c):
        ask = await c.call_tool("ask", {"question": "q"})
        search = await c.call_tool("search", {"query": "去中心化金融"})
        return ask, search

    ask, search = _run(mcp, seq)
    assert ask.isError is True
    assert search.isError is False  # 零 LLM 工具不受 ask 失败影响


# ───────────────────────── stdout 通道洁净（决策P4.10-13） ─────────────────────────


def test_stdout_clean_during_full_suite(kb_mcp):
    """跑完整套工具（含 ask 的 mock 路径）后，**进程 stdout 无任何非协议字节**（决策P4.10-13）。

    in-memory 传输不经真实 stdout，故任何落到 sys.stdout 的字节都是核函数/降级文案泄漏（破帧元凶）。
    """
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    buf = io.StringIO()

    async def all_tools(c):
        await c.list_tools()
        await c.call_tool("search", {"query": "去中心化金融"})
        await c.call_tool("read_page", {"path": "wiki/entities/DeFi.md"})
        await c.call_tool("list_pages", {})
        await c.call_tool("graph", {})
        await c.call_tool("health", {})
        await c.call_tool("lint", {})
        await c.call_tool("ask", {"question": "q"})
        await c.call_tool("read_page", {"path": "../bad.md"})  # 触发 in-band 错误路径

    with redirect_stdout(buf):
        _run(mcp, all_tools)
    assert buf.getvalue() == ""  # stdout 全程零字节


# ───────────────────────── 只读保证：KB 零字节写入（决策P4.10-3） ─────────────────────────


def _snapshot(root: Path) -> dict:
    """库内全文件 (相对路径 → (size, mtime_ns))，用于断言零字节变动。"""
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_full_suite_zero_kb_write(kb_mcp):
    """跑完整套工具**包括 ask**（mock runner）后，KB 字节零变动：无新文件、无 agentao.log、
    无 .agentao/sessions/（决策P4.10-3）。

    注：`ask` 真实子进程的零写须实测（设计 §决策P4.10-3 标为待实证）；本用例用注入 runner 覆盖
    宿主自身零写——宿主不落 session、不开 agent_log，与 P4.9 reader 零写契约同姿态。
    """
    before = _snapshot(kb_mcp)
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def all_tools(c):
        await c.call_tool("search", {"query": "去中心化金融"})
        await c.call_tool("read_page", {"path": "wiki/entities/DeFi.md"})
        await c.call_tool("list_pages", {})
        await c.call_tool("graph", {})  # 只读、**不写** graph/
        await c.call_tool("health", {})
        await c.call_tool("lint", {})
        await c.call_tool("ask", {"question": "q"})

    _run(mcp, all_tools)
    assert _snapshot(kb_mcp) == before  # 零字节变动
    assert not (kb_mcp / "agentao.log").exists()
    assert not (kb_mcp / ".agentao").exists()
    assert not (kb_mcp / "graph").exists()  # graph 工具不落派生物


# ───────────────────────── serve_mcp 前置校验 / 方向不混 ─────────────────────────


def test_serve_mcp_rejects_non_kb(tmp_path):
    """非知识库根（缺 wiki/）→ GuanlanError(EXIT_USAGE)，由 CLI 捕获（决策P4.10-3/7）。"""
    from guanlan.errors import EXIT_USAGE, GuanlanError

    with pytest.raises(GuanlanError) as excinfo:
        serve_mcp(tmp_path)  # 空目录，无 wiki/
    assert excinfo.value.exit_code == EXIT_USAGE


def test_serve_mcp_readonly_require(kb_mcp, monkeypatch):
    """serve_mcp 只需 wiki/（writable=False）：删 raw//AGENTAO.md 仍能起（不跑事件循环，打桩 run）。"""
    (kb_mcp / "AGENTAO.md").unlink()
    (kb_mcp / "SCHEMA.md").unlink()
    ran = {}
    monkeypatch.setattr(
        mcp_server.FastMCP, "run", lambda self, transport="stdio": ran.update(t=transport)
    )
    from guanlan.errors import EXIT_OK

    assert serve_mcp(kb_mcp) == EXIT_OK
    assert ran["t"] == "stdio"


def test_direction_clarified_as_server():
    """方向不混（决策P4.10-6）：server instructions 写明『服务端』、区分『Tool 注入』反向。"""
    assert "服务端" in mcp_server._INSTRUCTIONS
    assert "Tool 注入" in mcp_server._INSTRUCTIONS
