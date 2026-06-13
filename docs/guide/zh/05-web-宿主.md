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

## 能做什么

- **浏览 wiki**,跟随 `[[wikilink]]` 点击导航
- 跑 **check·health·lint** 看报告、看 **graph**
- 从 `raw/` 选一篇**触发 ingest**(单 worker 串行,轮询结果)
- 与 agent **只读多轮对话**(token 流式)
- **全文检索**(`/api/search`),输入框防抖召回
- **投喂 / 上传 / 晋级**:粘贴存稿(`POST /api/raw`)、文件上传到暂存区、解析→人审→晋级为 `raw/` 源
- **回填**(`query --backfill`):把问答沉淀回 wiki(走门禁)
- **斜杠命令与只读自省**:`/status` `/context` `/skills` `/tools` `/mode`,停止按钮
- **可写工作会话** `/mode workspace-write`:Agent 可写 `workspace/`(`raw/` 仍硬只读),带三层写守卫 + 单写者 + undo
- **界面中英双语**:右上角 中文 ⇄ English 切换(纯前端 i18n,只翻界面 chrome;wiki 内容/agent 回答/报告体保持源语言)

## 读写分线

- 唯一写作业 `ingest`(及 heal/backfill/raw-write)复用 **P2 子进程 + 单写者门禁**(一个后台 worker,FIFO 串行)。
- 所有问答(单轮 + 多轮)走**只读进程内嵌入 Agentao**(默认只读、不过门禁、仅内存)。

## 只读多会话部署 `--reader`

```bash
guanlan -C my-wiki web --reader
```

把单用户宿主开成**只读多用户部署**:

- **不注册任何写路由**(raw/upload/ingest/heal/backfill/workspace-delete/graph 重建/undo → 404/405)
- **内部强制** `session_persist=False` + `mode=read-only`(任何调用方都零写)
- **默认 KB 零字节写入**(持久化关、agent_log 默认关)
- 会话隔离靠现有 122-bit 能力 UUID(`?c=<conversation_id>`):关闭会话枚举端点 → 他人 id 不可发现(能力 URL 模型)
- 配 reader-only 空闲回收(idle TTL 驱逐陈旧会话)、`--max-conversations` 可调高

## ⚠️ 安全

**仅供本机单用户。** 永远 `workers=1` + 仅监听 `127.0.0.1`。**绝不要把该端口暴露到网络**——没有账号/鉴权,`--reader` 的隔离只是能力 URL 模型(诚实威胁边界见设计文档),不是访问控制。

参见:仓库 [`docs/P4-Web宿主.md`](../../P4-Web宿主.md) 及各 `P4.x` 文档([P4.1](../../P4.1-Web投喂.md) / [P4.5](../../P4.5-可写Web工作会话.md) / [P4.6](../../P4.6-Web上传与晋级.md) / [P4.9](../../P4.9-只读多会话.md) 等)。
