"""P4.19 外部 MCP 配置诊断（只读叠加层，见 docs/P4.19-Web-MCP诊断.md）。

**它解决什么**：agentao 的 `build_from_environment` 在调用方不传 `mcp_registry=` 时**默认注入**
`FileBackedMCPRegistry`（`embedding/factory.py:244`），读 `<kb>/.agentao/mcp.json` +
`~/.agentao/mcp.json`。观澜的 CLI 子进程与 Web 嵌入**都吃这个默认**——也就是说外部 MCP 工具已经在
注入 ingest/query/heal/audit/Web 问答，而用户在观澜里看不到任何痕迹。本模块补的就是这份可见性，
**不改变**这一行为：零 LLM、零策略、零管理、不写盘、不执行任何外部工具。

**时点语义（决策P4.19-8）**：MCP 是**构造期**加载的（`web/conversation.py` 建 `Agentao` 时一次性
`init_mcp`），故这里给出的是**当前磁盘配置的解析结果**——新建 Web 会话或下一次 CLI 作业将使用它，
已有会话需新建后生效。措辞含糊（"当前实际生效"）会让用户以为改完 `mcp.json` 就对手上会话生效。

**两条轨（决策P4.19-1）**：
  ① **生效集合**取上游 `load_mcp_config` 的归一结果——它就是 agent 实际会连的那一份，不另立口径；
  ② **诊断**必须**另读原文**：`_load_json_file` 对坏 JSON **静默返回 `{}`**（`mcp/config.py:259-266`），
     只信上游就只会显示"没有配置"，而不是"配置写坏了"；且返回值已合并、无 scope 标记。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from agentao.mcp.config import (
    McpTransportConfigError,
    load_mcp_config,
    resolve_transport,
)
from agentao.paths import user_root

# 两份配置文件的**展示名**（不外泄绝对路径 / 用户名）。与 agentao 的读取位置一一对应。
PROJECT_LABEL = ".agentao/mcp.json"
USER_LABEL = "~/.agentao/mcp.json"

# 定向擦除的最短值长度：`"true"` / `"user"` / `"1"` 这类短值出现在错误文本里不是凭据，全局替换
# 只会把正文打成马赛克、毁掉本面板唯一要展示的那段文本（评审实证："could not find the user manual"
# → "could not find the *** manual"）。真实 MCP 凭据远长于 8。**下限对 userinfo / query 一视同仁**
# ——最初只护住 headers/env 是不对称的口子。代价是 <8 字符的凭据不被擦；那种长度在 MCP 生态里不存在。
_MIN_SECRET_LEN = 8

# URL **路径**里被当作凭据的段长阈值。`_mask_url` 只去 userinfo/query 是不够的：Zapier / Composio /
# Smithery 这类托管 MCP 的标准形态就是把 key 放在路径里（`/api/mcp/s/<token>/mcp`、UUID 段），
# 于是"以脱敏为职责的面板"把活密钥打在屏幕上。长度阈值宁可**多遮**——遮错一段只损失展示细节，
# 漏一段就是泄漏凭据。
_SECRET_PATH_SEG_LEN = 16

# 兜底正则：文本里任何 URL 形状的片段都按 `_mask_url` 收敛（去 userinfo / query / fragment）。
# 定向擦除已覆盖"我们手里有的值"，这条兜住"上游拼进错误串的变体"（如加了默认 path 的 URL）。
_URL_IN_TEXT_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>]+")

# URL 片段末尾常见的句读：正则会把它们一起吞进 URL，需摘出来原样放回，否则错误文本读起来断句全丢。
_TRAILING_PUNCT = ".,;:!?)]}。，）”"


class McpSdkUnavailable(RuntimeError):
    """连接检查需要官方 `mcp` SDK，但当前环境没有。

    `guanlan web` 只装 `[web]` extra，MCP **客户端**栈（`agentao.mcp.client` → `from mcp import
    ClientSession`）随 `[mcp]` extra 才到位。故 `GET /api/mcp`（纯读盘）恒可用，而连接检查在缺
    SDK 时须给出可执行的安装指引，而不是一个 500。
    """


class UnknownServer(LookupError):
    """请求检查了一个当前配置里不存在的 server 名（端点据此 404）。"""


class McpConfigUnreadable(ValueError):
    """`load_mcp_config` 自己抛了——配置文件形状坏到上游归一不了（端点据此 422）。

    `read_config` 能容忍这种输入（它另读原文、如实记 `config_errors`），`check_servers` 不行：
    连都不知道要连谁。此前这里没守卫，同一份坏配置在展示端好好的、在检查端 500，且前端只显示
    一句「HTTP 500」、完全不提配置文件才是原因。
    """


# ────────────────────────────── 脱敏（§3） ──────────────────────────────


def _split_url(raw: str):
    """`urlsplit` + 端口合法性探测；解析不出结构返回 `None`（调用方一律按"整串遮掉"处理）。"""
    try:
        parts = urlsplit(raw)
        parts.port  # 端口非法时才抛 ValueError，必须在这里触发一次
    except ValueError:
        return None
    return parts if parts.scheme and parts.hostname else None


def _secret_path_segments(path: str) -> list[str]:
    """URL 路径里"看着像凭据"的段：长度 ≥ `_SECRET_PATH_SEG_LEN`。

    纯长度判据，不猜熵值——遮错一段只是少显示几个字符，漏一段是泄漏活密钥（见该常量的注释）。
    """
    return [seg for seg in path.split("/") if len(seg) >= _SECRET_PATH_SEG_LEN]


def _mask_url(raw: str) -> str:
    """URL → `scheme://host[:port]/path`：去 userinfo / query / fragment，**并遮掉路径里的长段**。

    `?token=` 是最常见的凭据位；userinfo（`https://user:pass@host/`）次之；**路径段是第三处**
    ——托管 MCP 服务普遍把 key 放在 `/s/<token>/mcp` 这类位置上。解析不出结构的串**整串遮掉**
    ——宁可少显示，也不把一个不认识的串原样回显出去。
    """
    parts = _split_url(raw)
    if parts is None:
        return "***"
    host = parts.hostname or ""
    if ":" in host:  # IPv6 字面量：urlsplit 已剥掉方括号，回填后才是合法 URL
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    path = parts.path
    for seg in _secret_path_segments(path):
        path = path.replace(seg, "***")
    return urlunsplit((parts.scheme, netloc, path, "", ""))


def _basename(command: str) -> str:
    """stdio `command` → 只留最后一段（路径常含用户名 / 本机目录结构，不外泄）。"""
    tail = re.split(r"[\\/]", command.strip())[-1]
    return tail or command.strip()


def endpoint_of(config: dict[str, Any]) -> str | None:
    """一条 server 配置的**可展示端点**：URL 去凭据 / stdio 只留 command 的 basename。

    刻意**不按已解析的 transport 分支**：`type` 写坏时 transport 是 `unknown`，但配置里的 `url`
    照样带着 token——按"配置里有什么"分支才不会在坏配置上漏脱敏。`args` 一律不回（常含 token
    与本机路径）。
    """
    url = config.get("url")
    if isinstance(url, str) and url.strip():
        return _mask_url(url.strip())
    command = config.get("command")
    if isinstance(command, str) and command.strip():
        return _basename(command)
    return None


def _redactions(config: dict[str, Any]) -> list[tuple[str, str]]:
    """该 server 配置里的敏感原文 → 替换文本，**长的排前面**。

    先替换长串：否则短串先命中会把长串打碎，长串随后再也匹配不上，反而漏出残片。

    替换文本不一律是 `***`——**能保住可诊断性就保**：
    - `url` → 脱敏后的同一条 URL（读者仍看得出是打到哪个 host 的哪条路径失败了）；
    - `command` → basename（`[Errno 2] No such file: 'no-such-tool'` 仍然可读，只是不再带
      `/Users/<人名>/…` 这段本机目录结构——`endpoint_of` 早就这么做了，错误文本此前却没跟上）。
    """
    pairs: list[tuple[str, str]] = []

    url = config.get("url")
    if isinstance(url, str) and url.strip():
        raw = url.strip()
        pairs.append((raw, _mask_url(raw)))
        parts = _split_url(raw)
        if parts is not None:
            if len(parts.query) >= _MIN_SECRET_LEN:
                pairs.append((parts.query, "***"))
            # 逐个 query 值单独入表：错误串常只引**裸 token**（server 的 401 正文），
            # 既不带 `k=` 也不在 URL 里，整串规则与 URL 正则都够不着它。
            for _, value in parse_qsl(parts.query, keep_blank_values=True):
                if len(value) >= _MIN_SECRET_LEN:
                    pairs.append((value, "***"))
            userinfo = parts.netloc.rsplit("@", 1)[0] if "@" in parts.netloc else ""
            if len(userinfo) >= _MIN_SECRET_LEN:
                pairs.append((userinfo, "***"))
            # 只取密码那一半：用户名不是凭据，而 `user` / `admin` 这类词一旦入表就会把正文打碎。
            if ":" in userinfo:
                password = userinfo.split(":", 1)[1]
                if len(password) >= _MIN_SECRET_LEN:
                    pairs.append((password, "***"))
            for seg in _secret_path_segments(parts.path):
                pairs.append((seg, "***"))

    command = config.get("command")
    if isinstance(command, str) and command.strip():
        raw_cmd = command.strip()
        base = _basename(raw_cmd)
        if base != raw_cmd:
            pairs.append((raw_cmd, base))
    cwd = config.get("cwd")
    if isinstance(cwd, str) and len(cwd.strip()) >= _MIN_SECRET_LEN:
        pairs.append((cwd.strip(), "***"))

    # headers / env 的**每个值**（token 的正位）。args 不入表：它多是模块名/路径，擦掉会毁掉
    # stdio 最常见错误（找不到命令）的可诊断性；`args` 里塞 token 属已知 footgun，同「url 里写
    # $TOKEN 不会被展开」，记在 docs/backlog/notes/mcp客户端注入-未排期.md。
    for key in ("headers", "env"):
        section = config.get(key)
        if isinstance(section, dict):
            for value in section.values():
                if isinstance(value, str) and len(value) >= _MIN_SECRET_LEN:
                    pairs.append((value, "***"))
                    # `Authorization: Bearer <token>` 的裸 token 部分同样单独入表（同 query 值的理由）。
                    tail = value.rsplit(" ", 1)[-1] if " " in value else ""
                    if len(tail) >= _MIN_SECRET_LEN:
                        pairs.append((tail, "***"))

    return sorted(set(pairs), key=lambda pair: len(pair[0]), reverse=True)


def _mask_url_token(match: re.Match) -> str:
    """正则兜底的单个 URL 片段：摘掉尾随句读再脱敏，免得把断句一起吞掉。"""
    token = match.group(0)
    trail = ""
    while token and token[-1] in _TRAILING_PUNCT:
        trail = token[-1] + trail
        token = token[:-1]
    return _mask_url(token) + trail


def redact(text: str, config: dict[str, Any]) -> str:
    """错误文本脱敏（决策P4.19-5）：**已知敏感值定向擦除 + URL 正则兜底**。

    必须做：agentao 的错误串会带**完整 URL**——`NonMcpEndpointError` 把 `url` 原样写进消息
    （`mcp/client.py:381-388`），query 里的 token 会经 `error` 漏出去。定向擦除比通用正则可靠
    （这些值我们手里正好有），正则只作兜底。
    """
    for needle, replacement in _redactions(config):
        text = text.replace(needle, replacement)
    return _URL_IN_TEXT_RE.sub(_mask_url_token, text)


def redact_all(text: str, configs: dict[str, dict[str, Any]]) -> str:
    """按**全部** server 的已知敏感值脱敏一段文本（日志过滤用：不知道是谁写的那行）。"""
    for config in configs.values():
        for needle, replacement in _redactions(config):
            text = text.replace(needle, replacement)
    return _URL_IN_TEXT_RE.sub(_mask_url_token, text)


class _RedactingLogFilter(logging.Filter):
    """把 `agentao.mcp` 这一次连接检查期间的日志记录**就地脱敏**后再放行。

    非杞人忧天：`McpClient.connect` 的失败分支 `logger.error("Failed to connect to MCP server
    '%s': %s")` 会把**未脱敏的完整 URL**（含 `?token=`）写进 stderr（实测）。响应体脱敏得再干净，
    浏览器里点一次「检查连接」照样把凭据落到跑 `guanlan web` 的终端与它的日志文件里——决策P4.19-5
    要的是"不出进程"，不是"不出响应体"。

    只在本次检查期间挂在该 logger 上（filter 只作用于经它记录的 record、不吃子 logger 传播上来的），
    用完即摘。并发的别处 MCP 日志顺带也过一遍脱敏，不会更差。
    """

    def __init__(self, configs: dict[str, dict[str, Any]]) -> None:
        super().__init__()
        self._configs = configs

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — 基类方法名
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 — 格式化失败不该把日志整条弄丢
            return True
        cleaned = redact_all(text, self._configs)
        if cleaned != text:
            record.msg = cleaned
            record.args = ()
        return True


# ─────────────────────── GET /api/mcp：纯读盘、零网络 ───────────────────────


def _entry_shape_ok(entry: Any) -> bool:
    """一条 server 条目的形状是否**过得了上游的归一**（`_expand_config_env`，config.py:241-257）。

    上游对形状不设防：条目不是对象时 `dict(entry)` 抛 `ValueError`，`env`/`headers` 的非字符串值
    或 `args` 的非字符串元素让 `expand_env_vars` 抛 `TypeError`——**一条坏条目会让整份配置炸掉**
    （展示端整表清空、检查端 500）。这里按同一套规则先验一遍，好把错**点名到具体条目**。
    """
    if not isinstance(entry, dict):
        return False
    for key in ("env", "headers"):
        section = entry.get(key)
        if section is not None:
            if not isinstance(section, dict):
                return False
            if not all(isinstance(v, str) for v in section.values()):
                return False
    args = entry.get("args")
    if args is not None and (
        not isinstance(args, list) or not all(isinstance(a, str) for a in args)
    ):
        return False
    return True


def _raw_server_names(path: Path, label: str, errors: list[dict]) -> set[str]:
    """读**原文**取该文件声明的 server 名集合，并把"这份文件没被吃进去"的各种形态记进 `errors`。

    上游 `_load_json_file` 对坏 JSON **静默返回 `{}`**、对非 dict 的 `mcpServers` 同样静默归一成
    `{}`——那正是"我明明写了配置却什么都没有"的根因，所以诊断这一轨必须自己读原文。

    刻意**不**把「文件里没有 `mcpServers` 键」当错：`save_mcp_config` 会保留同文件里的其它键，
    一份只放别的 agentao 配置的 `mcp.json` 是合法的。
    """
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        errors.append({"path": label, "name": None, "kind": "json_unparsable"})
        return set()
    if not isinstance(data, dict):
        # 顶层不是对象：上游随后 `.get("mcpServers")` 会直接 AttributeError。对用户而言与坏 JSON
        # 同一件事——这份文件没被吃进去。
        errors.append({"path": label, "name": None, "kind": "json_unparsable"})
        return set()
    servers = data.get("mcpServers")
    if servers is None:
        return set()
    if not isinstance(servers, dict):
        # 写成数组（别的工具接受的形态）或别的类型：上游默默换成 `{}`，用户看到的是"文件是空的"。
        errors.append({"path": label, "name": None, "kind": "config_shape_invalid"})
        return set()
    names: set[str] = set()
    for name, entry in servers.items():
        if _entry_shape_ok(entry):
            names.add(name)
        else:
            errors.append({"path": label, "name": str(name), "kind": "config_shape_invalid"})
    return names


def read_config(root: Path) -> dict:
    """`GET /api/mcp` 的响应体：`{servers: [...], config_errors: [...]}`（阻塞，宿主经线程调）。"""
    user_dir = user_root()  # 每次现取：与 agentao 一样惰性解析（测试可 monkeypatch HOME）
    errors: list[dict] = []
    # 先读原文（定 scope + 报解析失败），再取上游归一结果（定生效集合）。
    project_path = root / ".agentao" / "mcp.json"
    user_path = user_dir / "mcp.json"
    _raw_server_names(project_path, PROJECT_LABEL, errors)
    user_names = _raw_server_names(user_path, USER_LABEL, errors)
    try:
        merged = load_mcp_config(project_root=root, user_root=user_dir)
    except Exception:  # noqa: BLE001 — 不因坏配置 500 掉整个面板，但**必须**留下痕迹
        merged = {}
        # 上一句吞掉异常是对的，"顺手把 errors 留空"就不是——那正好把坏配置渲染成
        # 「未配置任何外部 MCP server」，与真空配置一字不差，等于本模块要消灭的那种沉默。
        # 上面的原文校验已覆盖已知的炸点（条目/env/headers/args 形状），故这里只兜"还没见过的"
        # 上游失败：无法归因到具体文件时，对**存在的每一份**都记一条，宁可多说不可不说。
        if not errors:
            for path, label in ((project_path, PROJECT_LABEL), (user_path, USER_LABEL)):
                if path.is_file():
                    errors.append({"path": label, "name": None, "kind": "config_shape_invalid"})

    servers: list[dict] = []
    for name, config in merged.items():
        # 同名冲突时上游把 project 条目整条 `continue` 忽略、user 胜（`mcp/config.py:317-328`），
        # 故名字只要出现在用户级文件里就标 user——那才是实情（决策P4.19-9）。
        scope = "user" if name in user_names else "project"
        label = USER_LABEL if scope == "user" else PROJECT_LABEL
        try:
            transport = resolve_transport(config)
        except McpTransportConfigError:
            # fail-closed 的配置错（坏 `type` / 缺 command·url）。与 agentao 自己的展示口径一致
            # （`McpClient.transport_type` 同样回落 "unknown"），另在 config_errors 里点名。
            transport = "unknown"
            errors.append({"path": label, "name": name, "kind": "transport_unresolvable"})
        else:
            if transport == "unknown":
                # 「既无 type、又无 command、又无 url」上游**不抛**、直接返回 "unknown"
                # （config.py:217）——`command` 拼成 `comand` 这类最常见的错配就落在这里。
                # 不补这一条，面板上只有一行孤零零的 unknown，用户看不出哪儿写错了；
                # 而上游 `connect()` 随后会把它变成 "No transport configured"，它确实是个错。
                errors.append({"path": label, "name": name, "kind": "transport_unresolvable"})
        servers.append(
            {
                "name": name,
                "scope": scope,
                "transport": transport,
                "endpoint": endpoint_of(config),
                "trusted": bool(config.get("trust", False)),
            }
        )
    return {"servers": servers, "config_errors": errors}


# ──────────────── POST /api/mcp/check：用户显式触发的一次连接检查 ────────────────


def _annotations_of(tool: Any) -> dict:
    """server 自报的 `ToolAnnotations` → 纯 dict（camelCase 键，`readOnlyHint` 等）。

    `by_alias=True` 在 mcp 1.x 上是 no-op（字段名本就是 camelCase），在 2.x 上把 snake_case 还原
    成协议定义的线上名——两个大版本读法一致。server 没给 annotations 时是 `{}`（如实显示为空，
    **不**替它推断：annotations 只存在于连接后的 `tools/list` 响应里，决策P4.19-2）。
    """
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return {}
    dump = getattr(ann, "model_dump", None)
    if dump is None:
        return dict(ann) if isinstance(ann, dict) else {}
    try:
        return dump(exclude_none=True, by_alias=True)
    except TypeError:  # pragma: no cover — 非 pydantic 的 annotations 实现
        return {}


def check_servers(root: Path, *, name: str | None = None) -> list[dict]:
    """连接一次当前配置里的 MCP server，回状态 + 工具清单（阻塞，宿主经线程调）。

    **只 `initialize` + `tools/list`，不执行任何工具**——故不需要 trust / 只读判定，也不改变任何
    注入行为。`name=None` 查全部；给了名字则必须已在配置里（否则 `UnknownServer` → 404）。端点
    **不接受**前端传入的 server 定义 / URL / header（决策P4.19-3），否则这个面板就成了一个任意
    MCP / SSRF 客户端。

    两个必须遵守的运行时事实（决策P4.19-4）：
      ① `McpClientManager._run` 是 `loop.run_until_complete`（`mcp/client.py:626-629`），在 FastAPI
         的运行中事件循环里直接调会崩 → 整个调用由宿主经 `anyio.to_thread.run_sync` 卸离事件循环；
      ② `disconnect_all()` 会 `close()` 掉那个 loop → **manager 是单次使用对象**：每次检查新建、
         用完即弃、不长驻不复用（与 P5.1 长驻 `CorpusCache` 正相反，别照抄那个模式）。

    超时不在这里加端点级总墙钟（决策P4.19-10）：`to_thread` 的线程不可取消，外层 async 超时要么
    仍等线程结束、要么把线程与它拉起的 stdio 子进程留在后台跑。上游
    `asyncio.wait_for(self._handshake(), timeout=startup_timeout)` 已**统一兜住所有传输、含 stdio**
    （`mcp/client.py:246-256`，默认 60s），`connect_all()` 并发连接，故一次检查的上界 ≈ 各 server
    startup 的最大值。
    """
    try:
        configs = load_mcp_config(project_root=root, user_root=user_root())
    except Exception as exc:  # noqa: BLE001 — 上游对形状不设防，一条坏条目就整份抛
        # 展示端容忍这种输入（它另读原文、如实记 config_errors），检查端不行：连都不知道要连谁。
        # 但也不该是 500——那让前端只显示「HTTP 500」，一个字都不提配置文件才是原因。
        raise McpConfigUnreadable(
            "MCP 配置无法解析，检查未执行：请先修好 .agentao/mcp.json / ~/.agentao/mcp.json"
            f"（配置表里已标出问题条目；原始错误：{exc}）。"
        ) from exc
    if name is not None:
        if name not in configs:
            raise UnknownServer(name)
        configs = {name: configs[name]}
    if not configs:
        return []
    try:
        from agentao.mcp import McpClientManager  # 惰性：mcp SDK 重且属 `[mcp]` extra
    except ImportError as exc:  # SDK 缺席 → 501 + 安装指引，不是 500
        raise McpSdkUnavailable(
            "连接检查需要官方 MCP SDK：请先 `pip install 'guanlan-wiki[mcp]'`"
            f"（原始错误：{exc}）。配置展示（GET /api/mcp）不受影响。"
        ) from exc

    manager = McpClientManager(configs)
    # 连接期把 agentao 自己那条「Failed to connect … at <完整 URL>」的 ERROR 也过一道脱敏：
    # 响应体擦干净、终端里却留着 `?token=` 不叫脱敏（见 _RedactingLogFilter）。
    agentao_mcp_logger = logging.getLogger("agentao.mcp")
    log_filter = _RedactingLogFilter(configs)
    agentao_mcp_logger.addFilter(log_filter)
    try:
        manager.connect_all()
        results: list[dict] = []
        for row in manager.get_server_status():
            server_name = row["name"]
            config = configs.get(server_name, {})
            client = manager.get_client(server_name)
            error = row.get("error")
            results.append(
                {
                    "name": server_name,
                    "status": row["status"],
                    "transport": row["transport"],
                    # 错误文本与配置展示走**同一道**脱敏：只脱敏 endpoint 而放过 error，等于没脱敏。
                    "error": redact(error, config) if error else None,
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description or "",
                            "annotations": _annotations_of(tool),
                        }
                        for tool in (client.tools if client is not None else [])
                    ],
                }
            )
        return results
    finally:
        # 无论成败都断：stdio 上游是我们拉起的**子进程**，漏断即留守护进程。
        # 注：agentao 的 `disconnect` 会吞掉一条 anyio "exit cancel scope in a different task" 警告
        # ——connect 在 `gather` 的子任务里进栈、disconnect 在外层任务出栈，属上游既有行为（正常
        # agent 跑也一样），不是本模块的问题。子进程仍会退出，`tests/test_web_mcpdiag.py` 有断言兜底。
        manager.disconnect_all()
        agentao_mcp_logger.removeFilter(log_filter)  # 只在本次检查期间挂，用完即摘
