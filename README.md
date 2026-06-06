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

Web 端写 `raw/`、`query --backfill`、可写多轮工作会话、会话落盘、多格式 ingest 留待 P4 之后（见 DESIGN §8 与 `docs/P4-Web宿主.md` §10）。别名自动物化建页（`heal`）、同义词表、向量检索按需驱动、另开方案。

## 安装

```bash
pip install guanlan-wiki
```

> PyPI 发布名是 `guanlan-wiki`（裸名 `guanlan` 已被一个无关项目占用）；安装后命令行与导入名仍是 `guanlan`。需 Python 3.12+。`ingest` / `query` / Web 端问答需配置模型（经 Agentao 运行时）；`init` / `check` / `health` / `lint` / `graph` 零-LLM、可离线运行。

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
