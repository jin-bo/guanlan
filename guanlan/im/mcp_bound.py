"""给外部 MCP server 落一个有限的每次请求上界（P4.21 §4.7，决策P4.21-60/63/65）。

**为什么需要**——两条已核实的 agentao 源码事实：

1. `build_from_environment` 在调用方未传 `mcp_registry` / `mcp_manager` 时**默认装上**
   `FileBackedMCPRegistry`（`embedding/factory.py`），即**读盘上 `.agentao/mcp.json` 的 MCP
   文件发现是默认开的**——IM 宿主会原样继承用户为这个库配的所有 MCP server。
2. MCP 的超时解析：`timeout` 缺省（或显式 `null`）→ `(默认启动超时, request=None)`
   ——**单次工具调用无限等待**（`mcp/config.py` 的 `resolve_timeouts`）。

于是就算模型 HTTP 请求有超时，一轮也可能永远卡在某个 MCP 工具里。

**合并规则只有一条（决策P4.21-63/65）**：看 **agentao 解析之后**的 `request` 是不是有限正数
——不是就用 `T`；是就取 `min(它, T)`。

- 判据是"解析结果"而**不是"配置形态"**：`request` 写成 `0` / `-1` / `"bad"` / `NaN` /
  `Infinity` 时 `_coerce_timeout` 只打一条 WARNING 就退回 default，而 request 槽的 default
  正是 `None` ＝ 又变回无限等待。按形态分类会整类漏掉它们。
- 取 `min` 而非"显式值优先"：**一个任何配置文件都能绕过的上界不是上界**。被收紧时记 WARNING
  ——改变用户显式配置的行为不能悄悄发生。

**T 的口径必须说准（决策P4.21-72/73/74/75，见 §4.7 定稿框）**：

> T 约束的是「请求完成**出站写入之后**、等待 `tools/call` 响应」这一段；出站写入本身不在该
> 计时器内（SDK 先 `await self._write(...)`、之后才 `with anyio.fail_after(...)`）。
> 握手与 Streamable HTTP 的传输往返有各自的阶段超时；legacy SSE 的传输进入、stdio 传输进入、
> 出站写入、以及 exit-stack 清理仍可能无界。**因此完整单次 `tools/call` 请求与完整 MCP 执行
> 都没有严格总墙钟上界。** 进程一定退得掉的唯一保证是第二次中断信号（§4.4）。

**"不是总上界"不等于"没用"**：它把最常见也最长的那一段（工具真正干活、等它回话）收住了，
并堵掉"默认无限等待"这个最容易踩的坑。
"""

from __future__ import annotations

import logging
import math
from typing import Any

_logger = logging.getLogger("guanlan.im")

# `timeout` 为标量（legacy 形态）时它表达的是 **startup**（agentao `resolve_timeouts` 的文档
# 明写此语义，且刻意与 opencode 相反）。包装后改写成 dict 时必须把它放回 `startup` 槽，
# 否则会把用户的连接超时静默改成请求超时。
_TIMEOUT_KEY = "timeout"


def _finite_positive(value: Any) -> bool:
    """agentao `_positive_or` 的判据镜像：能转成 float、> 0、且有限。

    `bool` 显式排除（`True` 会被 `float()` 接受成 1.0，但配置里写 `true` 显然是笔误而非 1 秒）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(num) and num > 0


def bounded_request_timeout(cfg: dict, t: float, *, name: str = "<server>") -> dict:
    """返回 `cfg` 的**副本**，其 `timeout.request` 不超过 `t`（`startup` 原样保留）。

    **不就地改** `cfg`：`FileBackedMCPRegistry.list_servers()` 每次重读盘，但
    `InMemoryMCPRegistry` 返回的是自己那份的浅拷贝——就地改 `timeout` 子 dict 会顺着浅拷贝
    改到源里（§10.1 用例④断言这一点）。
    """
    raw = cfg.get(_TIMEOUT_KEY)
    if isinstance(raw, dict):
        startup = raw.get("startup")
        req = raw.get("request")
    else:
        # 标量 / None / 垃圾：它整体是 **startup** 语义（见 `_TIMEOUT_KEY` 注释），
        # request 槽此时恒为"未配置"。垃圾值原样带回去让 agentao 自己 WARNING + 退默认——
        # 我们只管 request 这一槽，绝不代它对 startup 做主。
        startup = raw
        req = None
    if _finite_positive(req):
        eff = min(float(req), t)
        if float(req) > t:
            _logger.warning(
                "MCP server %s 的 timeout.request=%s 超过 --mcp-request-timeout=%s，已收紧到后者"
                "（它是宿主级天花板；真需要更长请调大该旗标）。",
                name,
                req,
                t,
            )
    else:
        eff = t
    return {**cfg, _TIMEOUT_KEY: {"startup": startup, "request": eff}}


class BoundedTimeoutRegistry:
    """包一层 MCP registry：给**每个** server 落一个**不超过 T** 的 `timeout.request` 上界。

    只改这次读到的**副本**，不动盘上文件、也不动被包装 registry 的内部状态。
    registry 的文档化契约只要求一个 `list_servers() -> dict[str, McpServerConfig]`
    （见 `agentao/mcp/registry.py`），故本类无需继承任何东西。
    """

    def __init__(self, inner: Any, *, request_timeout: float) -> None:
        if not _finite_positive(request_timeout):
            # 兜底：入口（`serve_im`）已校验过，这里再挡一次——T 非法会让上面的合并规则算出一个
            # 没有意义的值，**兜底自己先破了**（决策P4.21-63）。
            raise ValueError(f"request_timeout 须为有限正数，收到 {request_timeout!r}")
        self._inner = inner
        self._t = float(request_timeout)

    @property
    def request_timeout(self) -> float:
        """本包装施加的上界 T（秒）。供日志/测试读取。"""
        return self._t

    def list_servers(self) -> dict[str, dict]:
        return {
            name: bounded_request_timeout(cfg, self._t, name=name)
            for name, cfg in self._inner.list_servers().items()
        }
