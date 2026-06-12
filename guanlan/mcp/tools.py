"""MCP 只读工具：薄壳，直接调既有核心函数（P4.10，见 docs/P4.10-MCP宿主.md §2/§3）。

**零业务智能**——每个工具只把 `search`/`pages`/`graph`/`health`/`lint`/`runtime` 的只读能力包成
一条 MCP 工具，绝不复制业务逻辑。所有工具：

- **带返回类型注解（`TypedDict`）**：FastMCP 据此自动生成 output schema，使结果落 `structuredContent`
  + JSON 文本块兜底（决策P4.10-10）；缺注解会退化为纯文本串、失对象契约。
- **统一 in-band error 总壳**（决策P4.10-16）：`@_guard` 把任何核函数意外抛出收敛为 `ToolError`
  ——FastMCP/lowlevel 把它转成 MCP in-band tool error 返回调用方，**绝不冒泡杀 server / 破 stdio 帧**。
- **路径单一口径**（决策P4.10-9）：`page`/`path` 字段 = 相对库根、带 `wiki/` 前缀（如
  `wiki/entities/DeFi.md`），与 P5.0 `SearchHit.page` / Web 端点同口径；`read_page` 直接吃
  `search().results[i].page`，链路无拼接/剥前缀。

阻塞（读盘/分词/打分、`ask` 的子进程）由 `server.py` 的 async 包装经 `anyio.to_thread.run_sync`
卸离事件循环（决策P4.10-15）；本模块只放同步核逻辑。
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

# pydantic 在 Python < 3.12 上要求 `typing_extensions.TypedDict`（非 `typing.TypedDict`）才能为
# TypedDict 生成 core schema——FastMCP 据返回类型注解生成 output schema 时正走这条路（决策P4.10-10/14）。
# 用 typing.TypedDict 会在 3.10/3.11 上 `PydanticUserError`、令全部工具注册失败（CI 下限 3.10）。
# typing_extensions 由 mcp→pydantic 必带，mcp extra 装上即在；不引新直接依赖。
from typing_extensions import TypedDict

from mcp.server.fastmcp.exceptions import ToolError

from ..graph import build_graph, graph_to_dict
from ..health import run_health
from ..lint import run_lint
from ..pages import iter_pages, load_page, page_title, page_type, report_dict
from ..query import QUERY_PROMPT
from ..runtime import AgentRunner, run_agent_task
from ..search import CorpusCache, score, search_result_dict

__all__ = [
    "SearchEnvelope",
    "PageEnvelope",
    "PagesEnvelope",
    "GraphEnvelope",
    "ReportEnvelope",
    "AskEnvelope",
    "tool_search",
    "tool_read_page",
    "tool_list_pages",
    "tool_graph",
    "tool_health",
    "tool_lint",
    "tool_ask",
]

_F = TypeVar("_F", bound=Callable[..., Any])


def _guard(name: str) -> Callable[[_F], _F]:
    """统一 in-band error 总壳（决策P4.10-16）：核函数任何意外抛出 → `ToolError`。

    FastMCP 的 lowlevel `call_tool` handler 把 `ToolError`（及任何异常）转成 MCP in-band tool error
    （`isError=True`）返回调用方、server 不崩、stdio 帧不破（已核对 SDK 1.27）。这层总壳只为给出
    **受控的中文消息**（不泄漏 traceback 到通道）、并显式兑现决策P4.10-16；`search` 的 limit/query
    坏类型防御（决策P4.10-12）是它之内的细化、不替代它。
    """

    def deco(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except ToolError:
                raise  # 已是受控错误，原样上抛（避免二次包裹）。
            except Exception as exc:  # noqa: BLE001 — 总壳刻意吞一切，转 in-band
                raise ToolError(f"{name} 失败：{exc}") from exc

        return wrapper  # type: ignore[return-value]

    return deco


# ───────────────────────── 返回类型（驱动 FastMCP output schema） ─────────────────────────


class _SearchHit(TypedDict):
    page: str
    title: str
    type: str
    score: float
    snippet: str


class SearchEnvelope(TypedDict):
    """`search` 信封：与 CLI `--json` / Web `/api/search` 经 `search_result_dict` 同形。"""

    ok: bool
    query: str
    pages_searched: int
    results: list[_SearchHit]


class PageEnvelope(TypedDict):
    """`read_page` 信封：单页正文（容错档，坏 frontmatter 不崩）。"""

    path: str
    title: str
    content: str


class _PageRef(TypedDict):
    path: str
    title: str
    type: str


class PagesEnvelope(TypedDict):
    """`list_pages` 信封：非 config 页清单（从磁盘实时枚举）。"""

    pages: list[_PageRef]


class GraphEnvelope(TypedDict):
    """`graph` 信封：与 `graph_to_dict` 同形（节点/边/stats，含 P3.5 社区若已落）。"""

    generated_from: str
    stats: dict[str, int]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class ReportEnvelope(TypedDict):
    """`health` / `lint` 信封：经 `pages.report_dict` 直接产出（不绕 `format_report` 字符串往返）。"""

    ok: bool
    pages_checked: int
    findings: list[dict[str, Any]]


class AskEnvelope(TypedDict):
    """`ask` 信封：观澜只读 Agentao 综合出的带 `[[引用]]` 答案。"""

    answer: str


# ───────────────────────── 零 LLM 检索工具（无模型、可离线） ─────────────────────────


@_guard("search")
def tool_search(
    query: object, limit: object, *, search_cache: CorpusCache, wiki: Path
) -> SearchEnvelope:
    """确定性整页召回（BM25 + CJK 2-gram + 标题/别名加权），复用 P5.0 内核 + P5.1 长驻 cache。

    `score(search_cache.corpus(wiki), q, limit=lim)`——走 server 级 `CorpusCache`（与 P5.1 Web 同款
    性能路径，按 (mtime_ns,size) 增量重建），**不**冷算 `search_pages`（决策P4.10-11）。并发临界区靠
    `CorpusCache` 自持锁（决策P4.10-15）。`page` 带 `wiki/` 前缀、可直接喂 `read_page`。

    坏参数自防（决策P4.10-12，无 HTTP `Query(ge=1)` 兜底）：`limit` 经 `try/except (TypeError,
    ValueError)`——0/负值 clamp 到 1、坏类型（`None`/`"abc"`）回默认 10，绝不让 `score` 的 `limit<1`
    `ValueError` 冒泡；`query` 非 str 一律 `str()` 归一（`None`→空串），否则 `tokenize`→`re.finditer`
    抛 `TypeError`。空/纯标点 query 不必额外管——`score` 对零 query-token 短路回空命中、安全。
    """
    try:
        lim = max(1, int(limit))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        lim = 10
    if not isinstance(query, str):
        query = "" if query is None else str(query)
    result = score(search_cache.corpus(wiki), query, limit=lim)
    # search_result_dict 是 CLI/Web/MCP 三处字段 + 取整单一归口（决策P5.1-4 / P4.10-10）。
    return search_result_dict(result)  # type: ignore[return-value]


def _safe_wiki_file(root: Path, rel: str) -> Path:
    """把请求 path 解析为 `wiki/` 内存在的文件；越界/缺失 → `ToolError`（与 Web `_safe_wiki_file` 同款）。

    `rel` 是相对库根带 `wiki/` 前缀的 posix 路径（即 `search().results[i].page`，决策P4.10-9）。
    绝对路径 / `..` 越界经 `resolve()` + `relative_to(wiki)` 夹断（与 Web 同口径，但不依赖 fastapi
    的 HTTPException——MCP 子包不 import web；越界/缺失抛 `ToolError`、由调用方收 in-band）。
    """
    wiki = (root / "wiki").resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(wiki)
    except ValueError:
        raise ToolError(f"路径越界（须在 wiki/ 内）：{rel}") from None
    if not candidate.is_file():
        raise ToolError(f"页面不存在：{rel}")
    return candidate


@_guard("read_page")
def tool_read_page(path: str, *, root: Path) -> PageEnvelope:
    """读单页正文（容错档：坏/缺 frontmatter 不崩，`page_title` 回退 stem）。

    `path` = 相对库根带 `wiki/` 前缀（即 `search().results[i].page`），经 `_safe_wiki_file` 防越界
    到 `wiki/` 外（决策P4.10-9）。`content` = 正文 body（剥 frontmatter）。
    """
    page_file = _safe_wiki_file(root, path)
    meta, body = load_page(page_file)
    return {
        "path": page_file.relative_to(root).as_posix(),
        "title": page_title(meta, page_file.stem),
        "content": body,
    }


@_guard("list_pages")
def tool_list_pages(*, root: Path) -> PagesEnvelope:
    """非 config 页清单（≈ index.md 目录，但从磁盘 `iter_pages` 实时枚举，与 check/graph 同口径）。

    **无分页/无上限**（决策P4.10-10 附）：大 wiki 上全量枚举可能很长，调用方可先 `search` 收窄。
    """
    wiki = root / "wiki"
    pages: list[_PageRef] = []
    for page_path in iter_pages(wiki):
        meta, _body = load_page(page_path)  # 容错档：坏 frontmatter 不抛。
        pages.append(
            {
                "path": page_path.relative_to(root).as_posix(),
                "title": page_title(meta, page_path.stem),
                "type": page_type(meta),
            }
        )
    return {"pages": pages}


@_guard("graph")
def tool_graph(*, root: Path) -> GraphEnvelope:
    """节点/边/stats（含 P3.5 社区若已落），复用 `graph_to_dict(build_graph(wiki))`。**只读，不写 `graph/`。**

    **无上限**（决策P4.10-10 附）：大 wiki 上 `graph()` 倾倒全图，调用方自行决定是否先 `search` 收窄。
    """
    return graph_to_dict(build_graph(root / "wiki"))  # type: ignore[return-value]


@_guard("health")
def tool_health(*, root: Path) -> ReportEnvelope:
    """文件级结构体检（桩页 + index↔磁盘同步），经 `pages.report_dict` 直接产 dict（决策P4.10-10）。"""
    report = run_health(root / "wiki")
    return report_dict(  # type: ignore[return-value]
        ok=report.ok,
        pages_checked=report.pages_checked,
        items_key="findings",
        items=report.findings,
    )


@_guard("lint")
def tool_lint(*, root: Path) -> ReportEnvelope:
    """图感知结构 lint（孤儿/断链/缺失实体），经 `pages.report_dict` 直接产 dict（决策P4.10-10）。"""
    report = run_lint(root / "wiki")
    return report_dict(  # type: ignore[return-value]
        ok=report.ok,
        pages_checked=report.pages_checked,
        items_key="findings",
        items=report.findings,
    )


# ───────────────────────── 唯一 LLM 工具 ask（复用 CLI query 只读子进程） ─────────────────────────


@_guard("ask")
def tool_ask(
    question: str,
    model: str | None,
    *,
    root: Path,
    startup_model: str | None,
    runner: AgentRunner | None,
) -> AskEnvelope:
    """把问题交给观澜只读 Agentao 综合出**带 `[[引用]]`** 的答案（决策P4.10-5/15）。

    复用 CLI query 只读子进程路径（`run_agent_task(QUERY_PROMPT.format(...),
    permission_mode="read-only")`，query.py 同款）：故 **P4-8 嵌入四坑不适用**（走子进程，与
    heal/backfill 同理）、**无 session 状态**（单次无状态调用），与 `guanlan query` 字节同源。

    `model` = 工具入参 `or` 启动 `--model` `or None`（可解析到 None，由 Agentao 兜底；MCP 无 HTTP
    body，**不**照搬 Web 的 `body.model` 口径）。`run_agent_task` 把**所有**失败归一为
    `AgentRunResult(ok=False)`（无模型 / agentao 不在 PATH / 输出不可解析 / LLM API marker 等同列），
    故 `ok=False` 一律 → in-band tool error（`ToolError`），不影响零 LLM 工具可用（决策P4.10-7）。

    空/纯空白 question 先就地拒（in-band error），与 Web backfill 的 `field_validator` strip-非空同口径
    （app.py `RawBody`/backfill）——否则把空问题灌进 `QUERY_PROMPT` 白白拉起一次昂贵的 agentao 子进程。
    """
    if not isinstance(question, str) or not question.strip():
        raise ToolError("question 不能为空（含纯空白）。")
    run_result = run_agent_task(
        QUERY_PROMPT.format(question=question),
        working_directory=root,
        permission_mode="read-only",
        model=model or startup_model,
        runner=runner,
    )
    if not run_result.ok:
        raise ToolError(run_result.final_text or "Agentao 运行失败。")
    return {"answer": run_result.final_text}
