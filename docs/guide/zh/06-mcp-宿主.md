# MCP 宿主

`guanlan mcp` 是可选叠加层:一个**只读 MCP 服务端**,把 wiki 的检索/读页/图谱/体检/问答暴露为工具,给任意 MCP 客户端(Claude Code / Codex / Cursor …)使用。它是 P4 宿主层的**第二种传输**,支持两种通道:**stdio**(默认,由调用方作子进程拉起)与 **Streamable HTTP**(`--transport http`,跨进程 / 跨机)。

> 前置:`pip install 'guanlan-wiki[mcp]'`(带入官方 `mcp` SDK + anyio;HTTP 所需 uvicorn/starlette 随 SDK 一并到位,无额外依赖)。未装时 `guanlan mcp` 优雅降级、提示安装(退出码 `1`)。

## 启动(stdio,默认)

通常**不直接手跑**——由调用方 Agent 作子进程拉起。手动验证:

```bash
guanlan -C my-wiki mcp                    # 在 stdio 上起只读 MCP 服务端(默认)
guanlan -C my-wiki mcp --transport stdio  # 等价的显式写法
guanlan -C my-wiki mcp --model <id>       # 覆盖 ask 工具的模型(仅 ask 用)
```

## 启动(HTTP,跨进程 / 跨机)

`--transport http` 起 Streamable HTTP 传输,默认绑 `127.0.0.1:8766`,让**不同进程 / 不同机器**的 MCP 客户端连它:

```bash
# 同机跨进程(环回,免 token)
guanlan -C my-wiki mcp --transport http                 # 绑 127.0.0.1:8766
guanlan -C my-wiki mcp --transport http --port 9000

# 跨机(非环回:强制 token + 声明对外 Host)
GUANLAN_MCP_TOKEN=<你的密钥> \
guanlan -C my-wiki mcp --transport http \
  --host 0.0.0.0 --allowed-host kb.example.internal \
  --auth-token-env GUANLAN_MCP_TOKEN
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--transport {stdio,http}` | `stdio` | 传输通道;不带即 stdio、与旧行为字节等价 |
| `--host` | `127.0.0.1` | HTTP 绑定地址;非环回**必须**配 `--auth-token-env` |
| `--port` | `8766` | HTTP 端口(与 Web 的 8765 错开;须在 1–65535) |
| `--auth-token-env ENVVAR` | — | 从该环境变量读 bearer token(**绝不**命令行明文 / 落盘) |
| `--allowed-host HOST[:PORT]` | — | 额外放行的 `Host` 头(可重复);反代对外域名须显式补 |
| `--allow-ask` | 关 | HTTP 下显式暴露昂贵的 `ask` 工具(stdio 恒暴露) |
| `--model` | — | 覆盖 `ask` 工具的 Agentao 模型(**仅 `ask` 用**) |

### HTTP 安全默认(记住这几条红线)

- **默认只绑 `127.0.0.1`**:与 Web 宿主同姿态,靠环回作信任边界。
- **非环回强制 token**:`--host` 非环回(如 `0.0.0.0`)时**必须**经 `--auth-token-env` 提供 bearer token,否则**拒绝启动**——绝不把无鉴权的 wiki 裸暴露到网络。token 只从环境变量读(空白 / 未设置一律拒启)。
- **绑通配符须声明对外 Host**:绑 `0.0.0.0`/`::` 时必须用 `--allowed-host` 指明客户端连接用的域名 / IP,否则 DNS-rebinding 防护会拒掉所有远程请求(此情形直接拒启并提示,替代"起而不可达")。
- **`ask` 默认不上网**:HTTP 下默认只暴露**六个零-LLM 工具**;`ask` 会拉起付费 LLM 子进程(成本 / DoS 面),须 `--allow-ask` 显式开。
- **无状态**:HTTP 走 stateless 模式(无 `Mcp-Session-Id`、无事件重放)。
- **TLS 外置**:本体只出明文 HTTP;跨机加密请前置反代(caddy/nginx)或 SSH 隧道终止 TLS,转发到 `127.0.0.1:8766`。

## 在客户端注册

**stdio**——登记为 stdio server:

```jsonc
{ "mcpServers": {
    "guanlan": { "command": "guanlan", "args": ["-C", "my-wiki", "mcp"] }
} }
```

**HTTP(同机,免 token):**

```jsonc
{ "mcpServers": {
    "guanlan-http": { "type": "streamable-http", "url": "http://127.0.0.1:8766/mcp" }
} }
```

**HTTP(跨机,经反代 + token):**

```jsonc
{ "mcpServers": {
    "guanlan-remote": {
      "type": "streamable-http",
      "url": "https://kb.example.internal/mcp",
      "headers": { "Authorization": "Bearer ${GUANLAN_MCP_TOKEN}" }
} } }
```

服务端侧配合 caddy/nginx 在前面终止 TLS 并转发到 `127.0.0.1:8766`。`--allowed-host kb.example.internal` **不可省**——反代通常透传原始 `Host: kb.example.internal`,不补进白名单会被 DNS-rebinding 防护拒掉。

## 只读工具

| 工具 | LLM? | 说明 |
|---|---|---|
| `search` | 否 | 整页召回(复用 `guanlan search` 内核) |
| `read_page` | 否 | 读一篇 wiki 页(带路径穿越防护) |
| `list_pages` | 否 | 列出内容页 |
| `graph` | 否 | 图谱(节点/边/社区/拓扑统计) |
| `health` | 否 | 体检报告 |
| `lint` | 否 | lint 报告 |
| `ask` | **是** | 对知识库提问(复用 CLI-query 只读子进程路径) |

> **stdio 暴露全部七个**;**HTTP 默认只六个零-LLM 工具**,`ask` 需 `--allow-ask` 才出现。六个零-LLM 工具复用与 Web 读端点相同的内核;`ask` 走只读子进程(故 P4-8 嵌入坑不适用)。

## 设计要点

- **零写契约**:镜像 `--reader` 的零字节 KB 写入姿态——MCP **不做 convert**(写 `raw/` 与只读姿态冲突)。
- **不碰服务端会话状态**:MCP 客户端自持会话,故宿主无需服务端 session/conversation 状态(相比 Web 的最大减法)。
- **方向区别于 DESIGN 的「Tool 注入」**:这里是**观澜作 MCP 服务端**(把 wiki 暴露出去);DESIGN 的 Tool 注入是反方向——**Agentao 作 MCP 客户端**消费外部工具。
- **HTTP 的网络信任边界 ≠ 注入信任边界**:`--auth-token-env`/`--allowed-host` 管"谁能连、绑哪个地址",与 P4.11 的提示词注入防御是两条正交的信任线,不互相顶替。
- 是 **E2「远程 / scoped MCP」的前哨**;完整 OAuth / 多租户 source 级作用域留给 E2。

参见:仓库 [`docs/P4.10-MCP宿主.md`](../../P4.10-MCP宿主.md)、[`docs/P4.17-MCP远程传输.md`](../../P4.17-MCP远程传输.md)。
