# Web 宿主

`guanlan web` 是 **MVP 之后的可选叠加层**:把 CLI 命令搬进浏览器。不装、不起,整套东西照旧用 CLI 跑通;markdown 仍是唯一事实来源,Web 只是 ingest 与问答的另一个入口、wiki 的只读浏览器。

> 前置:`pip install 'guanlan-wiki[web]'`(带入 fastapi / uvicorn / markdown / python-multipart / anyio)。未装时 `guanlan web` 优雅降级、提示安装(退出码 `1`)。

## 启动

```bash
guanlan -C my-wiki web                          # 默认 127.0.0.1:8765,自动开浏览器
guanlan -C my-wiki web --port 9000 --no-browser # 换端口 / 不开浏览器
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--port` | `8765` | 监听端口(**仅 127.0.0.1**) |
| `--no-browser` | — | 起服后不自动打开浏览器 |
| `--model` | — | 覆盖 Agentao 模型(透传写作业与会话) |
| `--reader` | 关 | 只读多会话部署(见下) |
| `--agent-log` / `--no-agent-log` | 非 reader 开 / reader 关 | 是否把会话 agent 日志写入 `<库>/agentao.log` |
| `--max-conversations` | `100` | 内存会话硬上限(须 ≥ 1) |
| `--no-session-persist` | 关(默认落盘) | 不把只读问答会话落盘 `<库>/.agentao/sessions/`;关时等价纯内存(隐私/临时场景) |
| `--mode` | `read-only` | 新会话开局姿态;`workspace-write` 起即可让 Agent 写 `wiki`/`workspace`,浏览器内可 `/mode` 切换 |
| `--confirm` | `ask` | workspace-write 下 ASK 操作(带操作符 shell / 需确认工具)是否**弹给人确认**;`auto` 沿用静默放行(见「工具确认」) |
| `--confirm-timeout` | `120` | confirm / 提问等用户应答的超时秒数;无人应答即**默认拒绝** |

## 能做什么

- **浏览 wiki**,跟随 `[[wikilink]]` 点击导航
- 跑 **check·health·lint** 看报告、看 **graph**
- 从 `raw/` 选一篇**触发 ingest**(单 worker 串行,轮询结果)
- 与 agent **只读多轮对话**(token 流式)
- **全文检索**(`/api/search`),输入框防抖召回
- **投喂 / 上传 / 晋级**:粘贴存稿(`POST /api/raw`)、文件上传到暂存区、解析→人审→晋级为 `raw/` 源
- **回填**(`query --backfill`):把问答沉淀回 wiki(走门禁)
- **语义审计**(`audit`):复核 `raw/` 已变但 wiki 未重综合的漂移源(顶栏「审计」按钮:预览漂移源组 → 一键复核 → 轮询结构化回执;走门禁)
- **斜杠命令与只读自省**:`/status` `/context` `/skills` `/tools` `/mode`,停止按钮
- **可写工作会话** `/mode workspace-write`:Agent 可写 `workspace/`(`raw/` 仍硬只读),带三层写守卫 + 单写者 + undo
- **外部 MCP 配置诊断**(顶栏「MCP」按钮):看清本库实际会被注入哪些外部 MCP server,并可显式做一次连接检查(只读、不执行任何外部工具)
- **界面中英双语**:右上角 中文 ⇄ English 切换(纯前端 i18n,只翻界面 chrome;wiki 内容/agent 回答/报告体保持源语言)

## 富渲染(浏览器内,纯前端)

wiki 页与对话答案里的几类标记会在浏览器内渲染成富呈现——**markdown 始终是唯一事实来源**,渲染只是叠加增强,CLI / 纯文本回退里照旧是 honest 源码:

- **mermaid 图**:` ```mermaid ` 围栏块 → 流程 / 时序 / 类 / 状态图(P4.13)
- **数学公式**:`$…$` / `$$…$$` / `\(…\)` / `\[…\]` → KaTeX 排版(P4.14)
- **化学表达式**:mhchem `\ce{}` / `\pu{}`,**须写在数学分隔符内**(如 `$\ce{2H2 + O2 -> 2H2O}$`);裸 `\ce{}` 按字面留存(P4.14)
- **代码高亮**:` ```python ` 等带语言围栏块 → 语法高亮(highlight.js common,~36 语言;未覆盖语言保留纯文本)(P4.14)
- **数据图表**:` ```flint ` 围栏块([flint](https://github.com/microsoft/flint-chart) 图表规格 JSON)→ 柱 / 线 / 散点 / 热力等 37 种图型(P4.20)

**关于数据图表**:数据**整体内联**在那段 markdown 里(渲染器**只认内联 `data.values`、拒 `data.url`**,浏览器永不为取数出网)——所以一张图 100% 可从那段 markdown 重建,`check`/`graph`/`search` 也照旧适用。它是**取数那一刻的快照**,不会自己刷新;要更新就重新问一次、产出新块。图**不做交互下钻**(只读呈现,不是 BI 面板)。三种失败各有各的说法,修法不同:「数据过大」减行、「数据被截断」减**类别数**、「渲染失败」改规格。要让 Agent 写得出合法的块,装一下随包的 `flint-chart-author` 技能(见 [CLI 命令 · install-skill](03-cli-命令.md))。

渲染器全部 **vendored、随包打入、非 CDN、离线可用**,且**懒加载**(无对应内容的页零加载);任何加载 / 语法失败一律**保留源码**、页面不空白。安全上 KaTeX `trust:false`(禁 `\href`/`\html*`)、mermaid `securityLevel:'strict'`、highlight 喂转义文本、flint 走「入口白名单 + 键域出口断言 + SVG 渲染 + tooltip 不走 HTML 通道」——**不承诺产物绝对可信**,信任边界见各相位设计文档。CLI / MCP 文本通道不渲染(回字面源码)。

> **复制原始 Markdown**:每个回答气泡右下角有一枚剪贴板图标钮,点击把该轮答案的 **markdown 源**(非渲染后文本——公式 / 代码 / `[[链接]]` 原样可粘回)复制进剪贴板,成功短暂回显「已复制」。

## 工具确认 / 人在环(可写会话,P4.15)

可写会话(`workspace-write`)里,凡 Agent 要跑**带操作符 / 管道的 shell**或**带"需确认"标记的工具**(agentao 判为 `ASK` 的那类),默认不再静默放行,而是**经浏览器弹一帧确认请求**:气泡里**字面显示将要执行的命令全文**(不渲染、不执行),你点:

- **允许**——只放行这一个工具;
- **本会话起自动放行**——放行这一个,且本会话后续 ASK 不再逐次问(可一键「恢复逐次确认」切回);
- **拒绝**——这一个不跑(Agent 收到「工具被拒」,本轮继续、自行改道)。

不点则 `--confirm-timeout`(默认 120s)后**自动拒绝**;按「停止」或关标签页(断线)同样默认拒绝——绝不让"人走开"把写锁永占。模型也可经此机制**主动提问**(带选项 / 自由文本),你填答案回传。

**关键边界**:「人点允许」**≠**「绕过只读硬墙」。确认只决定一个 ASK 工具**跑不跑**,**不**给 Agent 开任何新写路径——`raw/` 与 `AGENTAO.md` 的只读始终由确定性写守卫(层①②)扛,即便你对某条 shell 点了允许,它随后想写 `raw/` 仍被拒。「本会话起自动放行」也只松「问不问」这一档,**姿态仍 workspace-write、所有写守卫一个不少**,**不是** CLI 那种 full-access。

不想被逐次打断的批量维护场景:`--confirm auto` 起服(等价旧的静默放行),或会话内点「本会话起自动放行」。

## 外部 MCP 配置诊断(顶栏「MCP」按钮,P4.19)

如果你在 `<库>/.agentao/mcp.json` 或 `~/.agentao/mcp.json` 里配过 MCP server,**那些外部工具早已被注入观澜的每一次 ingest / query / heal / audit / Web 问答**——这是 agentao 的默认行为(不传 `mcp_registry=` 即默认按这两份文件发现),此前观澜里看不到任何痕迹。顶栏「MCP」按钮就是补这份**可见性**:

- 列出**本库实际会被注入**的外部 server:名称 / 来源(project ⇄ user)/ 传输 / 端点 / 是否 `trust`;
- 点「**检查连接**」显式连一次,看每个 server 连不连得上、报了哪些工具与 annotations。

三条要记住的边界:

- **时点**:面板显示的是「**当前磁盘配置的解析结果**;新建 Web 会话或下一次 CLI 作业将使用这些配置。**已有会话需新建后生效**」——MCP 是会话开始时一次性加载的,改完 `mcp.json` **不会**让你手上这个会话跟着变。
- **它不是闸**:只反映配置与连通性,**不保证**这些 server 安全、只读或可信;本相位不做准入 / 白名单 / 策略,也**不改变**注入行为。诊断本身只做 `initialize` + `tools/list`,**不执行任何外部工具**。
- **不自动探测**:连接会真的起 stdio 子进程 / 发网络请求,故只在你点的时候连;同一时刻只跑一次(并发点会得到「已有检查在进行中」)。端点 / 错误文本里的 token、`headers`、`env`、URL 里的凭据一律**不外泄**。

连接检查需要 `pip install 'guanlan-wiki[mcp]'`(那个 extra 带 MCP 客户端栈);没装时**只是检查按钮报缺依赖**,配置展示照常可用。`--reader` 只读部署下这两个端点**不注册**、按钮隐藏——理由不是"怕写 KB",而是连接检查**有外部副作用**(发请求、拉子进程)。

> 方向辨析:这里诊断的是**别人的 MCP server 被注入进观澜**;反方向——把观澜自己的 wiki 作只读 MCP 服务端暴露给 Claude Code / Codex / Cursor——是 [`guanlan mcp`](06-mcp-宿主.md)。

## 读写分线

- 唯一写作业 `ingest`(及 heal/backfill/audit/raw-write)复用 **P2 子进程 + 单写者门禁**(一个后台 worker,FIFO 串行)。
- 所有问答(单轮 + 多轮)走**只读进程内嵌入 Agentao**(默认只读、不过门禁、仅内存)。

## 只读多会话部署 `--reader`

```bash
guanlan -C my-wiki web --reader
```

把单用户宿主开成**只读多用户部署**:

- **不注册任何写路由**(raw/upload/ingest/heal/backfill/audit/workspace-delete/graph 重建/undo → 404/405)
- **内部强制** `session_persist=False` + `mode=read-only`(任何调用方都零写)
- **默认 KB 零字节写入**(持久化关、agent_log 默认关)
- 会话隔离靠现有 122-bit 能力 UUID(`?c=<conversation_id>`):关闭会话枚举端点 → 他人 id 不可发现(能力 URL 模型)
- 配 reader-only 空闲回收(idle TTL 驱逐陈旧会话)、`--max-conversations` 可调高

## ⚠️ 安全

**仅供本机单用户。** 永远 `workers=1` + 仅监听 `127.0.0.1`。**绝不要把该端口暴露到网络**——没有账号/鉴权,`--reader` 的隔离只是能力 URL 模型(诚实威胁边界见设计文档),不是访问控制。

参见:仓库 [`docs/P4-Web宿主.md`](../../P4-Web宿主.md) 及各 `P4.x` 文档([P4.1](../../P4.1-Web投喂.md) / [P4.5](../../P4.5-可写Web工作会话.md) / [P4.6](../../P4.6-Web上传与晋级.md) / [P4.9](../../P4.9-只读多会话.md) / [P4.13](../../P4.13-Web-mermaid渲染.md) / [P4.14](../../P4.14-Web数学化学代码渲染.md) / [P4.15](../../P4.15-Web工具确认.md) / [P4.19](../../P4.19-Web-MCP诊断.md) / [P4.20](../../P4.20-Web-flint图表渲染.md) 等)。
