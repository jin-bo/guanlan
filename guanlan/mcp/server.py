"""`serve_mcp(...)`：起只读 MCP 服务（P4.10 stdio + P4.17 Streamable HTTP，见 docs/P4.10-MCP宿主.md
§1/§2、docs/P4.17-MCP远程传输.md）。

P4「可选宿主层」的**第二种传输**（stdio ⇄ Web 的 HTTP+SSE）：把同一套只读核心暴露给任意 MCP
客户端（Claude Code / Codex / Cursor）。**方向澄清（决策P4.10-6）**：guanlan 在此作 MCP **服务端**，
把 wiki 只读暴露给外部 Agent；与 DESIGN §1.22「Tool 注入」（Agentao 作 MCP **客户端**消费外部工具）
同名、**方向相反**。

P4.17 在此之上加**第二种 MCP 传输**——官方 **Streamable HTTP**（`--transport http`），让 wiki 能被
跨进程/跨机 MCP 客户端消费，而不必与调用方同机、由其作子进程拉起。**同一套只读工具逻辑、同一零写契约、
只换传输**：`tools.py` 零改、`build_mcp` 仅加一个 `allow_ask` 注册门（http 默认不暴露昂贵的 `ask`），
传输/绑定/鉴权全部接线收在 `serve_mcp`（见下 §http 姿态）。**安全默认**：仍绑 `127.0.0.1`（决策P4.17-2，
沿用决策P4-4 红线）、非环回强制 bearer token 否则拒启、无状态 `stateless_http` + DNS-rebinding 防护、
TLS 外置。完整 OAuth / 多租户 source 级 scoping 显式推 E2（本文不做）。

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
import hmac
import os
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..errors import EXIT_OK, GuanlanError
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
    allow_ask: bool = True,
) -> FastMCP:
    """构造注册了只读工具的 FastMCP server（不跑事件循环，供测试直接 `call_tool`）。

    `search_cache` 缺省新建一个长驻 `CorpusCache`（决策P4.10-11）——`search` 工具的多次调用复用它、
    只重建变更页。`model` 仅 `ask` 用（覆盖其 Agentao 模型）；`runner` 注入点供测试打桩、不打真实 LLM。

    `allow_ask`（P4.17 唯一增量，决策P4.17-11）：六个零 LLM 只读工具**无条件注册**（与 P4.10 逐字节同）；
    仅 `ask` 的注册包进 `if allow_ask:`。stdio 传输恒传 `True`（七工具，与 P4.10 等价）；http 传输默认
    `False`（六工具），须 `--allow-ask` 显式补 `ask`（决策P4.17-6：不把昂贵 LLM 工具默认放上网络面）。
    传输/绑定/鉴权一律不进 build_mcp——那些全在 `serve_mcp`（保 build_mcp 单一职责，决策P4.17-11）。
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

    # `ask` 是唯一 LLM 工具：stdio 恒注册（allow_ask=True）；http 默认不注册（决策P4.17-6）——
    # 任意能连到端口的客户端都能触发付费 LLM 子进程 = 成本/DoS 放大 + 子进程写探问放大，故须
    # `--allow-ask` 显式承担。六个零 LLM 工具已无条件注册在上、与此门无关。
    if allow_ask:

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


# ───────────────────────── P4.17：Streamable HTTP 传输接线 ─────────────────────────

# 环回三件套：`serve_mcp` 用它判「非环回强制 token」（决策P4.17-2），`_http_security` 用它恒放行同机
# 客户端的 Host 头。`0.0.0.0`/`::` 是**绑定通配**、不是环回，故不在此集合（绑它们须显式 token）。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# DNS-rebinding 白名单的环回默认档（与 mcp SDK 默认一致）：同机客户端恒可连（Host 头校验层面）。
_LOCALHOST_HOST_PATTERNS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOCALHOST_ORIGIN_PATTERNS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


def _host_label(host: str) -> str:
    """把裸 IPv6 字面量（绑定地址）包成 Host 头形态 `[addr]`；IPv4/域名/已加括的原样返回。

    IPv6 客户端发的 `Host` 是 `[fe80::1]:port`（带方括号），故派生白名单也须用括号形，否则 SDK 的
    `startswith` 通配匹配对不上、每个请求 421（决策P4.17-5 修正）。含 `:` 且未加括即判为 IPv6 字面量。
    """
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _allowed_host_patterns(entry: str) -> list[str]:
    """一个 `--allowed-host` 值 → 白名单里的 Host 档：精确 + 任意端口通配，大小写归一为小写。

    DNS 名大小写不敏感、SDK 却按字节精确匹配，故统一小写（决策P4.17-5 修正）。已带 `:*` 的原样收；否则
    额外补一档任意端口通配（剥掉显式端口后加 `:*`），兼容『反代 TLS 终止后裸域名』与『域名:端口』两形。
    """
    entry = entry.strip().lower()
    if not entry:
        return []
    pats = [entry]
    if entry.endswith(":*"):
        return pats
    if entry.startswith("["):  # IPv6 字面量 [addr] 或 [addr]:port
        base = entry.split("]", 1)[0] + "]"
    else:  # 域名/IPv4：仅当末段是纯数字端口才剥
        head, sep, tail = entry.rpartition(":")
        base = head if (sep and tail.isdigit()) else entry
    wildcard = f"{base}:*"
    if wildcard != entry:
        pats.append(wildcard)
    return pats


def _http_security(host: str, allowed_hosts: list[str] | None) -> TransportSecuritySettings:
    """据 `--host` / `--allowed-host` 派生 DNS-rebinding 白名单（决策P4.17-5）。

    环回三件套恒放行（同机客户端）；绑到**具体非环回地址**时补该地址（IPv6 加方括号）；`--allowed-host`
    显式补反代对外域名（否则反代透传的外部 `Host` 会被 rebinding 防护拒掉，见 §4/§5）。`allowed_hosts`
    派生自 `--host`、**不写死 localhost**；只校 `Host`（origins 同步派生以免误杀反代下的浏览器客户端），
    不做复杂 origin 策略。注：`0.0.0.0`/`::` 绑定通配本身不是可用 `Host` 值，须由 `--allowed-host` 声明
    对外 Host（serve_mcp 在缺失时拒启）。
    """
    hosts = list(_LOCALHOST_HOST_PATTERNS)
    origins = list(_LOCALHOST_ORIGIN_PATTERNS)
    # 绑到具体非环回地址（非 0.0.0.0/:: 绑定通配）时，放行该地址自身的任意端口。
    if host not in _LOOPBACK_HOSTS and host not in ("0.0.0.0", "::", ""):
        label = _host_label(host)
        hosts.append(f"{label}:*")
        origins.extend((f"http://{label}:*", f"https://{label}:*"))
    for entry in allowed_hosts or ():
        for pat in _allowed_host_patterns(entry):
            hosts.append(pat)
            origins.extend((f"http://{pat}", f"https://{pat}"))
    # 去重保序（dict 保插入序）。
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(hosts)),
        allowed_origins=list(dict.fromkeys(origins)),
    )


class _BearerTokenMiddleware:
    """最小 ASGI 中间件：仅对 http 请求做 `Authorization: Bearer <token>` **常量时间**比对，失败即 401。

    P4.17 唯一的鉴权代码（决策P4.17-2），约十余行：**明确不引** OAuth / resource-server 元数据 / scope /
    用户模型 / token 轮换（那些是 E2）。非 http scope（lifespan / websocket）**原样透传**——保住
    `streamable_http_app()` 的 lifespan（`session_manager.run()`）不被这层拦掉。
    """

    def __init__(self, app, token: str) -> None:
        self._app = app
        self._token = token.encode()  # serve_mcp 已 strip；此处只存凭据本体、scheme 单独判。

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        presented = b""
        for key, value in scope.get("headers") or ():
            if key == b"authorization":
                presented = value
                break
        # 拆 `Bearer <token>`：scheme 按 RFC 7235 大小写不敏感（`bearer` 亦合法）、容多空格；凭据本体走
        # compare_digest 抗时序侧信道（长度会泄漏、内容不泄漏，标准做法可接受）。
        parts = presented.split(None, 1)
        ok = (
            len(parts) == 2
            and parts[0].lower() == b"bearer"
            and hmac.compare_digest(parts[1].strip(), self._token)
        )
        if not ok:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
        await self._app(scope, receive, send)


def _build_http_app(
    mcp: FastMCP,
    *,
    host: str,
    allowed_hosts: list[str] | None,
    token: str | None,
):
    """配置 http 姿态（无状态 + DNS-rebinding 白名单）并返回（按需裹 token 闸的）ASGI app。

    `streamable_http_app()` 在**首次调用时**读 `settings.stateless_http`/`transport_security` 建 session
    manager，故这些必须先赋值。注：它**不读** `settings.host`/`settings.port`（那些由 `_serve_http` 直接
    传给 uvicorn），故此处不设，免留无效旋钮误导。token 非空则外裹一层 `_BearerTokenMiddleware`（决策P4.17-2）。
    """
    mcp.settings.stateless_http = True  # 决策P4.17-7：无 Mcp-Session-Id、无事件重放
    mcp.settings.transport_security = _http_security(host, allowed_hosts)
    app = mcp.streamable_http_app()
    if token is not None:
        app = _BearerTokenMiddleware(app, token)
    return app


def _serve_http(
    mcp: FastMCP,
    *,
    host: str,
    port: int,
    allowed_hosts: list[str] | None,
    token: str | None,
) -> None:  # pragma: no cover - 真起 uvicorn 阻塞长驻，单测走 _build_http_app + 线程内起服
    import uvicorn

    app = _build_http_app(mcp, host=host, allowed_hosts=allowed_hosts, token=token)
    # log_level=warning：与 stdio 分支 build_mcp 的 FastMCP log_level 同调，压掉 uvicorn 每请求 INFO。
    uvicorn.run(app, host=host, port=port, log_level="warning")


def serve_mcp(
    root: str | Path,
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8766,
    auth_token_env: str | None = None,
    allowed_host: list[str] | None = None,
    allow_ask: bool = False,
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> int:
    """起只读 MCP 服务，前台长驻直到客户端断开；正常停服返回 `EXIT_OK`。

    `transport="stdio"`（默认，与 P4.10 字节等价）：`mcp.run("stdio")` 阻塞跑事件循环，stdout 仅承载
    JSON-RPC 帧（决策P4.10-13）。`transport="http"`（P4.17）：Streamable HTTP，默认绑 `127.0.0.1:8766`。

    前置 `require_kb_root(writable=False)`（只需 `wiki/` 在）可能抛 `GuanlanError(EXIT_USAGE)`、由 CLI
    捕获转退出码（决策P4.10-3/7）。**绑定红线（决策P4.17-2）**：`--host` 非环回时**必须**经
    `--auth-token-env <ENVVAR>` 提供 bearer token（从环境变量读、绝不命令行明文/落盘），否则拒启
    `EXIT_USAGE`、不监听任何端口。`--allow-ask` 默认 `False`：stdio 恒七工具，http 默认六、`--allow-ask`
    才补 `ask`（决策P4.17-6）。
    """
    kb = require_kb_root(root, writable=False)
    if transport not in ("stdio", "http"):  # argparse 已 choices 约束；直建调用兜底。
        raise GuanlanError(f"未知 --transport：{transport}（应为 stdio 或 http）")

    # ── 绑定红线 + token 闸（决策P4.17-2），须在预热/建 server/绑端口**之前**校验、拒则零副作用 ──
    token: str | None = None
    if transport == "http":
        # 端口范围就地校验（否则越界端口会到 uvicorn.run 抛 OverflowError、_cmd_mcp 只捕 GuanlanError
        # → 裸 traceback）；与 web 宿主 `_ensure_port_free` 首检同口径。
        if not 1 <= port <= 65535:
            raise GuanlanError(
                f"端口须在 1–65535 之间：{port}。换一个端口：`guanlan mcp --transport http --port <N>`。"
            )
        if auth_token_env:
            # strip：空白-only（如误设 ' ' 或 `$(cat)` 带尾换行）不得被当作有效 token（否则弱鉴权裸暴露）。
            token = os.environ.get(auth_token_env, "").strip()
            if not token:
                raise GuanlanError(
                    f"--auth-token-env {auth_token_env} 指向的环境变量为空、纯空白或未设置；"
                    f"请先 `export {auth_token_env}=<token>` 再起服。"
                )
        if host not in _LOOPBACK_HOSTS and token is None:
            raise GuanlanError(
                f"--host {host} 绑定到非环回地址，必须经 --auth-token-env <ENVVAR> 提供 bearer token"
                "（决策P4.17-2）：拒绝把无鉴权的 wiki 裸暴露到网络。"
            )
        # 0.0.0.0/:: 是绑定通配符、不是客户端可用的 Host 值：不配 --allowed-host 则 DNS-rebinding 白名单
        # 只含环回、每个远程请求都 421（起而不可达）。拒启并明示，替代『静默不可达』（决策P4.17-5）。
        if host in ("0.0.0.0", "::") and not allowed_host:
            raise GuanlanError(
                f"--host {host} 是绑定通配符、不是客户端可用的 Host 值；请用 --allowed-host "
                "<对外域名或 IP[:端口]>（可重复）声明客户端连接用的 Host，否则请求会被 DNS-rebinding "
                "防护全部拒掉（决策P4.17-5）。"
            )

    # P5.4 候选①（决策P5.4-1）：MCP（stdio/http 皆）长驻，首个 `search` 工具调用同样付冷全扫。起服前
    # 自建 `CorpusCache` 并后台预热（daemon、失败静默，归口 `prewarm_async`），把冷算移出客户端首搜关键
    # 路径；预热只读 wiki/、零写盘、不碰 stdout（stdio 帧洁净不破，决策P4.10-13）。http 同样受益。
    cache = CorpusCache()
    cache.prewarm_async(kb / "wiki")
    # ask 门控（决策P4.17-6）：stdio 永远七工具；http 默认六工具，--allow-ask 才七。
    mcp = build_mcp(
        kb,
        model=model,
        runner=runner,
        search_cache=cache,
        allow_ask=(transport == "stdio" or allow_ask),
    )
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:  # http：全部传输/绑定/鉴权接线在 serve_mcp（§4），build_mcp 不参与。
        _serve_http(mcp, host=host, port=port, allowed_hosts=allowed_host, token=token)
    return EXIT_OK
