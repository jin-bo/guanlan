# 观澜 (GuānLán)

> 《孟子·尽心上》"观水有术，必观其澜"——在信息的汪洋中洞察脉络与趋势。

观澜是 [Karpathy LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 的一个实现：让 Agent **增量地构建并持续维护一个结构化、互相链接的知识 wiki**，而不是每次提问都从原始文档临时检索（传统 RAG）。知识被"编译"一次后持续保鲜，随每篇新资料、每次提问而复利增长。

- **markdown 始终是唯一事实来源**——整个知识库就是一组本地 markdown 文件，任何索引/图谱/缓存都是可幂等重建的派生物。
- **Agent 全权拥有 wiki 层，人不直接写**——人负责投喂资料、提问、给方向；摘要、交叉引用、归档全交给 Agent。
- **`raw/` 只读不可变**——Agent 只读原始资料，永不修改，保证事实可追溯。
- **确定性优先**——结构检查、断链、frontmatter 校验走脚本（零 LLM）；需 LLM 的 ingest/query 统一经 Agentao 运行时治理。

完整设计见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## 状态

🚀 **P3（健康与图谱）** —— 在 P2 最小闭环（`guanlan init` / `ingest` / `query` / `check` / `install-skill`，经 Agentao 运行时治理）之上，新增三个按需、零-LLM 的维护工具：

- `guanlan health` —— stub 页面 + index↔disk 同步（advisory；`--strict` → 退出码 6）。
- `guanlan lint` —— 孤儿页 / 断链 / 缺失实体（advisory）。
- `guanlan graph` —— 确定性 `[[wikilink]]` 图谱 → `graph/graph.json` + 自包含 `graph/graph.html`（`--json-only` 跳过 html）。

语义 lint、推断图谱边、Web UI、多格式 ingest 留待 P3 之后（见 DESIGN §8）。

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
