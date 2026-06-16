"""`serve_mcp(...)`：在 stdio 上起只读 MCP 服务（P4.10，见 docs/P4.10-MCP宿主.md §1/§2）。

P4「可选宿主层」的**第二种传输**（stdio ⇄ Web 的 HTTP+SSE）：把同一套只读核心暴露给任意 MCP
客户端（Claude Code / Codex / Cursor）。**方向澄清（决策P4.10-6）**：guanlan 在此作 MCP **服务端**，
把 wiki 只读暴露给外部 Agent；与 DESIGN §1.22「Tool 注入」（Agentao 作 MCP **客户端**消费外部工具）
同名、**方向相反**。

姿态（镜像 P4.9 reader）：

- **只读、KB 零字节写入**（决策P4.10-3）：`require_kb_root(writable=False)`（只需 `wiki/` 在）；
  **不注册任何写工具**。`ask` 走 CLI query 只读子进程路径（与 `guanlan query` 字节同源）。
- **stdio 通道洁净**（决策P4.10-13，最高优先不变量）：stdout 即 JSON-RPC 传输帧——server 路径
  **绝不向 stdout 写非协议字节**（一个杂散 print 即破帧）。故只调**核函数（返对象）**、绝不调
  `*_entrypoint`/`format_report` 等打印壳；FastMCP 日志默认走 stderr。
- **handler 异步姿态**（决策P4.10-15）：MCP 是异步 JSON-RPC、客户端可并发多 in-flight，故每个工具
  注册为 `async def`、阻塞核逻辑经 `anyio.to_thread.run_sync` 卸离事件循环（一次慢 `ask` 不饿死并发
  廉价 `search`）。并发 `search` 对共享 `CorpusCache` 的临界区靠 cache 自持锁兜底。
- **无新退出码**（决策P4.10-7）：正常停服 `EXIT_OK`；非知识库根 / 用法错由 `require_kb_root` 抛
  `GuanlanError(EXIT_USAGE)`、CLI 捕获。工具内错误走 MCP in-band tool error、不映射进程退出码。
"""

from __future__ import annotations

import functools
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP

from ..errors import EXIT_OK
from ..paths import require_kb_root
from ..runtime import AgentRunner
from ..search import CorpusCache
from .tools import (
    AskEnvelope,
    GraphEnvelope,
    PageEnvelope,
    PagesEnvelope,
    ReportEnvelope,
    SearchEnvelope,
    tool_ask,
    tool_graph,
    tool_health,
    tool_list_pages,
    tool_lint,
    tool_read_page,
    tool_search,
)

__all__ = ["build_mcp", "serve_mcp"]

# 帮助客户端区分「服务端」与「Tool 注入」反向（决策P4.10-6，§7 方向不混）。
_INSTRUCTIONS = (
    "观澜（GuānLán）只读 MCP **服务端**：把一个本地 wiki 知识库的检索/读页/图谱/体检能力暴露为只读工具。"
    "本服务是 guanlan 作 MCP 服务端（与 Agentao 作 MCP 客户端的『Tool 注入』方向相反）。"
    "建议工作流：先用 `search`+`read_page` 召回并读取候选页自行综合；仅当需要观澜式带 `[[引用]]` 的"
    "服务端综合时才用较慢、较贵的 `ask`。`list_pages`/`graph` 无分页，大库上先 `search` 收窄。"
)

_SEARCH_DESC = (
    "确定性整页召回（BM25 + 中文 2-gram + 标题/别名加权），按相关度降序返回候选页 + 片段。"
    "**优先用它召回候选页**再 `read_page` 综合，比裸列目录更全（命中正文、别名、跨义）。"
    "page 字段带 `wiki/` 前缀、可直接喂 `read_page`。纯读、不写盘。"
)
_READ_PAGE_DESC = (
    "读取单页正文：path 用 `search` 结果里的 page 字段（相对库根、带 `wiki/` 前缀，如 "
    "`wiki/entities/DeFi.md`）。坏/缺 frontmatter 不报错。纯读、不写盘。"
)
_LIST_PAGES_DESC = (
    "枚举库内全部非 config 内容页（path/title/type）。**无分页**——大库上请先用 `search` 收窄。"
    "纯读、不写盘。"
)
_GRAPH_DESC = (
    "返回 wiki 链接图（节点/边/统计，含社区若已落）。**无分页**，大库上体量可能很大。"
    "只读、**不写** `graph/` 派生物。"
)
_HEALTH_DESC = "文件级结构体检：桩页 + index↔磁盘同步（零 LLM，建议非门禁）。纯读、不写盘。"
_LINT_DESC = "图感知结构 lint：孤儿 / 断链 / 缺失实体（零 LLM，建议非门禁）。纯读、不写盘。"
_ASK_DESC = (
    "把问题交给观澜自己的只读 Agentao，综合出**带 `[[引用]]`** 的答案（重路径：慢 + 费 token）。"
    "**不是检索面的替代**——优先 `search`+`read_page` 自行综合，仅当需要观澜式带引用综合时才用它。"
)


def build_mcp(
    root: Path,
    *,
    model: str | None = None,
    runner: AgentRunner | None = None,
    search_cache: CorpusCache | None = None,
) -> FastMCP:
    """构造注册了七个只读工具的 FastMCP server（不跑事件循环，供测试直接 `call_tool`）。

    `search_cache` 缺省新建一个长驻 `CorpusCache`（决策P4.10-11）——`search` 工具的多次调用复用它、
    只重建变更页。`model` 仅 `ask` 用（覆盖其 Agentao 模型）；`runner` 注入点供测试打桩、不打真实 LLM。
    """
    # 归一化根（决策P4.10-9）：`_safe_wiki_file` 内部 `.resolve()` 后做 `relative_to(root)`——root
    # 未 resolve 时（如 macOS /var→/private/var 符号链接、或直建测试传相对路径）会 ValueError。
    # serve_mcp 经 require_kb_root 已 resolve，这里对**直建调用**再兜一层，保 read_page 路径口径稳定。
    root = Path(root).resolve()
    cache = search_cache if search_cache is not None else CorpusCache()
    wiki = root / "wiki"
    startup_model = model  # 闭包捕获启动 --model：ask 工具的 `model` 入参会同名遮蔽，须先存别名。
    # log_level=WARNING：压掉 FastMCP 每请求一行的 INFO（"Processing request…"）。这些走 **stderr**
    # （非协议通道，不破 stdio 帧），但对直接拉起本 server 的客户端是噪声；降到 WARNING 更像个好公民。
    mcp = FastMCP("guanlan", instructions=_INSTRUCTIONS, log_level="WARNING")

    # 七个工具一律 async：阻塞核逻辑卸 to_thread（决策P4.10-15）；返回类型注解（TypedDict）驱动
    # FastMCP 自动生成 output schema → structuredContent + JSON 文本块兜底（决策P4.10-10）。

    @mcp.tool(name="search", description=_SEARCH_DESC)
    async def search(query: str, limit: int = 10) -> SearchEnvelope:
        return await anyio.to_thread.run_sync(
            functools.partial(tool_search, query, limit, search_cache=cache, wiki=wiki)
        )

    @mcp.tool(name="read_page", description=_READ_PAGE_DESC)
    async def read_page(path: str) -> PageEnvelope:
        return await anyio.to_thread.run_sync(
            functools.partial(tool_read_page, path, root=root)
        )

    @mcp.tool(name="list_pages", description=_LIST_PAGES_DESC)
    async def list_pages() -> PagesEnvelope:
        return await anyio.to_thread.run_sync(functools.partial(tool_list_pages, root=root))

    @mcp.tool(name="graph", description=_GRAPH_DESC)
    async def graph() -> GraphEnvelope:
        return await anyio.to_thread.run_sync(functools.partial(tool_graph, root=root))

    @mcp.tool(name="health", description=_HEALTH_DESC)
    async def health() -> ReportEnvelope:
        return await anyio.to_thread.run_sync(functools.partial(tool_health, root=root))

    @mcp.tool(name="lint", description=_LINT_DESC)
    async def lint() -> ReportEnvelope:
        return await anyio.to_thread.run_sync(functools.partial(tool_lint, root=root))

    @mcp.tool(name="ask", description=_ASK_DESC)
    async def ask(question: str, model: str | None = None) -> AskEnvelope:
        # ask 子进程可数十秒，须卸 to_thread——否则一次 ask 饿死并发廉价 search（决策P4.10-15）。
        # model（工具入参）or startup_model（启动 --model）or None，在 tool_ask 内解析（决策P4.10-5）。
        return await anyio.to_thread.run_sync(
            functools.partial(
                tool_ask, question, model, root=root, startup_model=startup_model, runner=runner
            )
        )

    return mcp


def serve_mcp(
    root: str | Path,
    *,
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> int:
    """在 stdio 上起只读 MCP 服务，前台长驻直到客户端断开；正常停服返回 `EXIT_OK`。

    前置 `require_kb_root(writable=False)`（只需 `wiki/` 在）可能抛 `GuanlanError(EXIT_USAGE)`、
    由 CLI 捕获转退出码（决策P4.10-3/7）。`mcp.run("stdio")` 阻塞跑事件循环，stdout 仅承载
    JSON-RPC 帧（决策P4.10-13）。
    """
    kb = require_kb_root(root, writable=False)
    # P5.4 候选①（决策P5.4-1）：MCP 同为长驻进程，首个 `search` 工具调用同样付冷全扫。起服前自建
    # `CorpusCache` 并后台预热（daemon、失败静默，归口 `prewarm_async`），把冷算移出客户端首搜关键路径；
    # 预热只读 wiki/、零写盘、不碰 stdout（MCP 帧洁净不破，决策P4.10-13）。
    cache = CorpusCache()
    cache.prewarm_async(kb / "wiki")
    mcp = build_mcp(kb, model=model, runner=runner, search_cache=cache)
    mcp.run(transport="stdio")
    return EXIT_OK
