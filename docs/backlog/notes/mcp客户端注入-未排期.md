# MCP 客户端方向（Tool 注入）：已核实的源码结论 · **未排期**

> 状态：**未排期，不立相位**。本笔记只保存核实过的机制事实，供将来真要做「外部工具注入」时直接取用，
> 避免重复调研。**本笔记不主张做任何事。**
>
> 已落地的相邻工作：[`../../P4.19-Web-MCP诊断.md`](../../P4.19-Web-MCP诊断.md)（Web 里如实展示配置 +
> 显式连接检查，纯观测、不改注入行为）。方向辨析见 [`../../P4.10-MCP宿主.md`](../../P4.10-MCP宿主.md) §0.1
> ——那是观澜作**服务端**，本笔记是相反方向。集成用例侧的调研见
> [`sag-对接-tool注入.md`](sag-对接-tool注入.md)。

核实基于 agentao 0.4.17（`.venv` 内实测 + 读源码）。

## 1. 注入现在就是开着的，观澜没有闸

- `embedding/factory.py:244-248`：调用方不传 `mcp_registry=` / `mcp_manager=` 时**默认注入**
  `FileBackedMCPRegistry(project_root=<wd>, user_root=~/.agentao)`。
- 它读 `<kb>/.agentao/mcp.json` + `~/.agentao/mcp.json`（`mcp/config.py:270-331`），每次调用重读盘；
  合并策略是「project 只能新增名、不能覆盖 user 同名 server」（config.py:317-328）。
- 观澜**两条 LLM 路径都吃这个默认**：CLI 子进程 `agentao run`（`cli/run.py:527`，`factory_kwargs` 无 mcp 键）；
  Web 进程内嵌入（`guanlan/web/conversation.py:249`，`opts` 无 mcp 键）。
- `agentao run` **没有任何 MCP 相关旗标**，观澜 `runtime.py:187-205` 无从传开关。

⇒ 用户放一份 `mcp.json`，ingest/query/heal/audit/Web 问答就已被注入外部工具。

## 2. 关闭的陷阱：`mcp_registry=None` **不是关闭**

`factory.py:242-244` 的注释写着 "`mcp_registry=None` to opt out of file discovery entirely"，
**与实现不符**：`agent.py:453-463` 把 `None` 的语义定为「用 legacy 文件源」，`tooling/mcp_tools.py:74-91`
据此走 `else` 分支重新 `load_mcp_config(project_root=wd, user_root=user_root())` **读盘**。

**正确的关闭姿态**：传**非空哨兵** `InMemoryMCPRegistry({})` → `configs = {}` →
`if not configs: return None`（mcp_tools.py:108），一个 server 都不连。任何将来的上游开关也**不能透传 `None`**。

## 3. 未 trust 的外部工具在观澜的两种姿态下都不可用

`mcp/tool.py:96-121`：未标 `trust: true` 时 `is_read_only` **恒 False**、`requires_confirmation` **恒 True**；
标了 trust 才看 server 自报的 `readOnlyHint` / `destructiveHint`。于是：

- **只读路径全灭**：`query` / `guanlan mcp` 的 `ask` 走 `permission_mode="read-only"`，
  `runtime/tool_planning.py:381` 的 `readonly_mode and not tool.is_read_only → DENY` 全部拒掉。
- **可写路径整轮中断**：`ingest` 下 `requires_confirmation → ASK`（tool_planning.py:404），撞上观澜固定的
  `--interaction-policy reject`（runtime.py:196；agentao `cli/run.py:454` 只接受 `'reject'`），
  `transport/non_interactive.py:36-48` 的 `confirm_tool` **cancel 掉 CancellationToken** → 整个 run 被取消
  → 观澜 `EXIT_AGENT_ERROR`(5)，且 `gate.py:441-443` 对 `agent_error` **不自愈** → 可能停在写了一半的 `wiki/` 上。

⇒ 「不 trust 也能用」不存在；而 `readOnlyHint` 是**协议侧必要条件**，观澜侧的任何白名单都替代不了它。
（本仓自己的 `guanlan mcp` 七个工具也没设 `annotations`，`guanlan/mcp/server.py:147+`；SDK v2
`MCPServer.tool()` 支持 `annotations=` 参数——若哪天想让它被别的 agent 在只读姿态下用，补上即可，
是一处独立小改，与本方向无依赖。）

## 4. 写守卫的覆盖缺口（若将来允许本地 stdio 上游，这条是关键）

- 外部 MCP 工具的写发生在**它自己的服务端进程**里，既不经 agentao `write_file`，也不经 P4.5 层①
  `PolicyFileSystem`（`conversation.py:216` 只包住 `agent.filesystem`）→ **层① 被绕过**。
- P2 的 `raw/` 快照**只在写路径存在**：`query` 的只读路径明确「不取 raw 快照、不跑 check」
  （`guanlan/query.py:62`），`guanlan mcp` 的 `ask` 复用同一条路径，Web 只读会话同样无快照。
- 且 `gate.snapshot_raw` 是**逐文件 sha256 全档**（`gate.py:110`），不是 stat 档——给只读路径补快照不是免费的。

⇒ 一个本地 stdio server 以用户权限运行，具备完整磁盘能力。真要做注入，**能力约束（只接受远端 URL 上游）
比任何检测都靠谱**；快照只能检测、不能阻止，且永远看不见库外副作用。

## 5. 静态体检做不到只靠 `load_mcp_config`

`_load_json_file`（config.py:259-266）对坏 JSON **静默返回 `{}`**；`_expand_config_env` 就地展开环境变量、
**原文丢失**；返回值是**已合并、无 scope 标记**的字典。故任何要报「配置写坏了 / 这条来自用户级」的诊断，
都必须**另读原文**（P4.19 §2.1 即按此实现：诊断读原文，生效集合仍取上游归一结果）。

展开范围要看准（P4.19 一稿曾在此栽过）：`_expand_config_env`（config.py:241-250）**只展开 `env` 值、
`headers` 值和 `args`**，`command` / `url` **不展开**——在 `url` 里写 `$TOKEN` 会原样发出去。
