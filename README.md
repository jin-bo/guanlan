# 观澜 (GuānLán)

> 《孟子·尽心上》"观水有术，必观其澜"——在信息的汪洋中洞察脉络与趋势。

观澜是 [Karpathy LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 的一个实现：让 Agent **增量地构建并持续维护一个结构化、互相链接的知识 wiki**，而不是每次提问都从原始文档临时检索（传统 RAG）。知识被"编译"一次后持续保鲜，随每篇新资料、每次提问而复利增长。

- **markdown 始终是唯一事实来源**——整个知识库就是一组本地 markdown 文件，任何索引/图谱/缓存都是可幂等重建的派生物。
- **Agent 全权拥有 wiki 层，人不直接写**——人负责投喂资料、提问、给方向；摘要、交叉引用、归档全交给 Agent。
- **`raw/` 只读不可变**——Agent 只读原始资料，永不修改，保证事实可追溯。
- **确定性优先**——结构检查、断链、frontmatter 校验走脚本（零 LLM）；需 LLM 的 ingest/query 统一经 Agentao 运行时治理。

完整设计见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## 状态

🚀 **P4（Web 宿主，可选叠加层）** —— 在 P2 最小闭环（`guanlan init` / `ingest` / `query` / `check` / `install-skill`）与 P3 维护工具（`health` / `lint` / `graph`）之上，新增一个**可选**的本地 Web 宿主，把上述命令搬进浏览器：

- `guanlan web` —— 起一个仅监听 `127.0.0.1` 的本地 Web 宿主（需 `pip install 'guanlan-wiki[web]'`）。浏览 wiki（`[[wikilink]]` 可点击导航）/ 跑 check·health·lint 看报告 / 看 graph / 从 `raw/` 选一篇触发 ingest（单 worker 串行，轮询结果）/ 与 agent **只读多轮对话**（token 流式）。
- 它是 **MVP 之后的可选叠加层**：不装 `guanlan-wiki[web]`、不起 `guanlan web`，整套东西照旧用 CLI 跑通。markdown 仍是唯一事实来源，Web 只是 ingest 与问答的另一个入口、wiki 的只读浏览器。
- **读写分线**：唯一写作业 `ingest` 复用 P2 子进程 + 单写者门禁；所有问答（一次性单轮 + 多轮）走只读进程内嵌入 `Agentao`（默认只读、不过门禁、仅内存）。

P3 三个零-LLM 维护工具（advisory）：

- `guanlan health` —— stub 页面 + index↔disk 同步（`--strict` → 退出码 6）。
- `guanlan lint` —— 孤儿页 / 断链 / 缺失实体。
- `guanlan graph` —— 确定性 `[[wikilink]]` 图谱 → `graph/graph.json` + 自包含 `graph/graph.html`（`--json-only` 跳过 html）。

**P3.1 别名解析（零-LLM 增强）** —— entity/concept 页可在 frontmatter 声明可选 `aliases`，让别名进入 `[[wikilink]]` 解析命名空间（与页名同口径、大小写不敏感）：`[[大模型]]` / `[[LLM]]` 都解析到声明它们的页，**消假断链**（check / lint / graph / Web 一致）、**补 CJK 同义召回**（别名纳入 query 2-gram 与 ingest 去重）。别名全局唯一由 `check` 确定性校验（撞页名 / 重复 → 阻断写门禁）。这不是新里程碑，P5 仍是多格式与自动化。细化见 [`docs/P3.1-别名解析.md`](docs/P3.1-别名解析.md)。

P4 之上已落地一批 **Web 宿主半相位**（仍在 P4 边界内，不引入新退出码）：缺失实体物化 `guanlan heal`（[P3.2](docs/P3.2-缺失实体物化.md)/[P3.3](docs/P3.3-规范标题页与别名收编.md)）、索引回填 `guanlan reindex`（[P3.4](docs/P3.4-索引回填.md)）、Web 投喂 `POST /api/raw`（[P4.1](docs/P4.1-Web投喂.md)）、会话落盘与恢复（[P4.2](docs/P4.2-会话落盘.md)）、Web-heal（[P4.3](docs/P4.3-Web-heal.md)）、Web 斜杠命令与只读自省（[P4.4](docs/P4.4-Web斜杠命令.md)）、可写 Web 工作会话（[P4.5](docs/P4.5-可写Web工作会话.md)）、Web 文件上传与派生物晋级为源（[P4.6](docs/P4.6-Web上传与晋级.md)）、界面中英双语切换（[P4.7](docs/P4.7-中英双语.md)）、Web 端问答回填 `query --backfill`（[P4.8](docs/P4.8-Web回填.md)）、只读多会话部署 `guanlan web --reader`（[P4.9](docs/P4.9-只读多会话.md)：多用户各持能力 UUID 各聊各的、KB 全共享只读、无用户管理，作 E2 会话分租前哨）。

**P5「语料规模化：多格式 + 检索」**里程碑的检索部分已落地：**检索层 `guanlan search`**（[P5.0](docs/P5.0-检索层.md)：零-LLM 确定性 BM25 + CJK-2-gram 全页召回，title/alias 字段加权，无持久化派生物；作 query/skill 召回前端、E1 检索升级的零基建先行片）与 **Web 检索接入**（[P5.1](docs/P5.1-Web检索接入.md)：把 P5.0 内核接入长驻 Web 进程——只读 `GET /api/search` 端点 + 嵌入式聊天的只读 `guanlan_search` 宿主工具 + 共享长驻 `CorpusCache`）。

**P4「可选宿主层」**也从「仅 Web」扩为「**Web + MCP** 两种传输」：**MCP 宿主 `guanlan mcp`**（[P4.10](docs/P4.10-MCP宿主.md)：一个**只读 MCP 服务端**（stdio，需 `pip install 'guanlan-wiki[mcp]'`），把 wiki 的检索/读页/图谱/体检/`ask` 暴露为七个只读工具给任意 MCP 客户端——Claude Code / Codex / Cursor；与 Web 宿主同构、同零写契约、不碰服务端会话状态，作 E2「远程 / scoped MCP」前哨。**方向区别于** DESIGN §1.22 的「Tool 注入」（那是 Agentao 作 MCP 客户端、反向））。

多格式自动 ingest 仍留待 P5 后续（见 DESIGN §8 与 `docs/P4-Web宿主.md` §10）。向量 / 重排 / 持久化索引留 E1，别名自动物化建页（`heal`）、同义词表按需驱动、另开方案。

## 安装

```bash
pip install guanlan-wiki
```

> PyPI 发布名是 `guanlan-wiki`（裸名 `guanlan` 已被一个无关项目占用）；安装后命令行与导入名仍是 `guanlan`。需 Python 3.10+。`ingest` / `query` / Web 端问答需配置模型（经 Agentao 运行时）；`init` / `check` / `health` / `lint` / `graph` / `search` 零-LLM、可离线运行。

## 快速开始

```bash
# 在空目录初始化一个知识库（生成 AGENTAO.md / SCHEMA.md / raw/ / wiki/）
guanlan init my-wiki

# 或就地初始化当前目录
guanlan init
```

`init` 是确定性的（零 LLM），已存在的文件不会被覆盖，可安全重复运行。

投喂资料、提问、维护：

```bash
# 投喂资料 / 提问（需配置模型，经 Agentao 运行时）
guanlan -C my-wiki ingest path/to/source.md
guanlan -C my-wiki query "..."

# 零-LLM、可离线运行的确定性工具
guanlan -C my-wiki check     # frontmatter / 断链 / 来源校验
guanlan -C my-wiki health    # stub 页面 + index↔disk 同步（--strict → exit 6）
guanlan -C my-wiki lint      # 孤儿页 / 断链 / 缺失实体
guanlan -C my-wiki graph     # 写出 graph/graph.json + graph.html（--json-only 跳过 html）
```

可选 Web 宿主（叠加层，需先装 `guanlan-wiki[web]`）：

```bash
pip install 'guanlan-wiki[web]'     # 装可选依赖（fastapi / uvicorn / markdown）
guanlan -C my-wiki web              # 起本地 Web 宿主，仅监听 127.0.0.1，默认开浏览器
guanlan -C my-wiki web --port 9000 --no-browser   # 换端口 / 不开浏览器
```

可选 MCP 宿主（叠加层，需先装 `guanlan-wiki[mcp]`）：把 wiki 只读暴露给任意 MCP 客户端（stdio 服务端）：

```bash
pip install 'guanlan-wiki[mcp]'     # 装可选依赖（mcp SDK）
guanlan -C my-wiki mcp              # 在 stdio 上起只读 MCP 服务端（由调用方 Agent 作子进程拉起）
```

调用方（如 Claude Code）在其 MCP 配置里把 `guanlan -C my-wiki mcp` 注册为一个 stdio server 即可：

```jsonc
{ "mcpServers": { "guanlan": { "command": "guanlan", "args": ["-C", "my-wiki", "mcp"] } } }
```

浏览器里可：浏览 wiki 并跟随 `[[wikilink]]` 导航、跑 check·health·lint 看报告、看 graph、
从 `raw/` 选一篇触发 ingest（轮询结果）、与 agent 只读多轮对话（token 流式）。
**仅供本机单用户**——绝不要把该端口暴露到网络。

生成结构：

```
my-wiki/
├── AGENTAO.md       # Agent 行为约束 + 指针
├── SCHEMA.md        # 本库 Schema：领域 / 启用页面类型 / 自定义规则
├── raw/             # 原始资料（只读，事实来源）
└── wiki/            # Agent 全权生成的知识层
    ├── index.md     # 全量页面目录
    ├── log.md       # append-only 时间线
    └── overview.md  # 跨资料活体综述
```

## 开发

```bash
uv run guanlan init /tmp/demo   # 跑 CLI
uv run pytest                   # 跑测试
```

维护引擎是 `skills/guanlan-wiki/`（`SKILL.md` + `references/conventions.md` + 脚本），
开发期命中 Agentao 的 repo-root skill 发现路径（`<工作目录>/skills/`），免安装。

## 许可证

[Apache License 2.0](LICENSE)
