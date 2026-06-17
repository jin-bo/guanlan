<p align="center">
  <img src="docs/guanlan-origin.png" alt="观澜 GuānLán" width="160">
</p>

<h1 align="center">观澜 (GuānLán)</h1>

**中文** | [English](README.en.md)

[![PyPI](https://img.shields.io/pypi/v/guanlan-wiki)](https://pypi.org/project/guanlan-wiki/) [![Python](https://img.shields.io/pypi/pyversions/guanlan-wiki)](https://pypi.org/project/guanlan-wiki/) [![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE) ![Status](https://img.shields.io/badge/状态-CLI%20闭环%20%2B%20Web%2FMCP%20宿主可用-brightgreen)

> 《孟子·尽心上》"观水有术,必观其澜"——在信息的汪洋中洞察脉络与趋势。

观澜让 **Agent 增量地构建并持续维护一个结构化、互相链接的知识 wiki**,而不是每次提问都从原始文档临时检索(传统 RAG)。你只管投喂资料、提问、给方向;摘要、交叉引用、归档全交给 Agent。知识被"编译"一次后持续保鲜,随每篇新资料、每次提问而复利增长。

这是 [Karpathy LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 的一个实现。

## 核心理念

- **markdown 始终是唯一事实来源**——整个知识库就是一组本地 markdown 文件,任何索引/图谱/缓存都是可幂等重建的派生物。
- **Agent 全权拥有 wiki 层,人不直接写**——人投喂、提问、给方向;生成与维护交给 Agent。
- **`raw/` 只读不可变**——Agent 只读原始资料、永不修改,保证事实可追溯。
- **确定性优先**——结构检查、断链、frontmatter 校验走脚本(零 LLM、可离线);需 LLM 的 `ingest`/`query` 统一经 Agentao 运行时治理。

## 能做什么

| 命令 | 作用 | 需模型? |
|---|---|---|
| `guanlan init` | 初始化一个知识库(确定性模板) | 否 |
| `guanlan ingest` | 投喂一篇资料,Agent 生成/更新 wiki 页 | 是 |
| `guanlan query` | 对知识库提问(`--backfill` 可把答案沉淀回 wiki) | 是 |
| `guanlan search` | 整页全文检索(BM25 + 中文分词) | 否 |
| `guanlan check` / `health` / `lint` | 校验 / 体检 / 结构 lint | 否 |
| `guanlan graph` | 生成可交互的 `[[wikilink]]` 知识图谱 | 否 |
| `guanlan web` | 在浏览器里浏览、问答、维护(可选叠加层) | 部分 |
| `guanlan mcp` | 把 wiki 只读暴露给 MCP 客户端(可选叠加层) | 部分 |

> 还有 `reindex`(索引回填)、`heal`(缺失实体物化)、`audit`(语义审计:复核 `raw/` 已变但 wiki 未重综合的漂移源)、`remove`(源撤回:把误摄/已撤稿源移入 `.trash/`)、`convert`(PDF/DOCX/… 转 markdown)等。逐命令细节见 **[用户指南](docs/guide/)**。

## 安装

```bash
pip install guanlan-wiki
```

> PyPI 发布名是 `guanlan-wiki`(裸名 `guanlan` 已被一个无关项目占用);安装后**命令行与导入名仍是 `guanlan`**。需 **Python 3.10+**。
>
> `init` / `check` / `health` / `lint` / `graph` / `search` 零 LLM、可离线运行;`ingest` / `query` / Web 问答需配置一个模型(经 Agentao 运行时)。

可选宿主(叠加层,按需装):

```bash
pip install 'guanlan-wiki[web]'    # 浏览器宿主 guanlan web
pip install 'guanlan-wiki[mcp]'    # 只读 MCP 服务端 guanlan mcp
```

## 快速开始

```bash
# 1. 初始化一个知识库(确定性、零 LLM,可重复运行不覆盖)
guanlan init my-wiki

# 2. 投喂资料 / 提问(需配置模型)
guanlan -C my-wiki ingest path/to/source.md
guanlan -C my-wiki query "你的问题"

# 3. 维护(零 LLM、可离线)
guanlan -C my-wiki check     # frontmatter / 断链 / 来源校验
guanlan -C my-wiki health    # 桩页 + index↔磁盘同步
guanlan -C my-wiki lint      # 孤儿页 / 断链 / 缺失实体
guanlan -C my-wiki graph     # 写出 graph/graph.json + graph.html
```

在浏览器里用(可选):

```bash
pip install 'guanlan-wiki[web]'
guanlan -C my-wiki web       # 起本地 Web 宿主,仅监听 127.0.0.1,默认开浏览器
```

浏览器里可:浏览 wiki 并跟随 `[[wikilink]]` 导航、跑 check·health·lint 看报告、看 graph、从 `raw/` 触发 ingest 等写作业(含 heal 补全、audit 漂移复核、backfill 回填)、与 agent 只读多轮对话。**仅供本机单用户——绝不要把端口暴露到网络。**

完整上手见 **[用户指南 → 快速上手](docs/guide/zh/02-快速上手.md)**。

## 生成结构

```
my-wiki/
├── AGENTAO.md       # Agent 行为约束 + 指针
├── SCHEMA.md        # 本库 Schema:领域 / 启用页面类型 / 自定义规则
├── raw/             # 原始资料(只读,事实来源)
└── wiki/            # Agent 全权生成的知识层
    ├── index.md     # 全量页面目录
    ├── log.md       # append-only 时间线
    └── overview.md  # 跨资料活体综述
```

> 💡 知识库长起来后,可让 LLM 先分析当前库的真实状态、再据此更新 `SCHEMA.md`(领域边界 / 页面类型用法 / 命名与标签约定 / 演进中的组织偏向);含可直接复用的提示词,见 **[用户指南 → 快速上手 §1.1 让 LLM 辅助更新 `SCHEMA.md`](docs/guide/zh/02-快速上手.md#11-让-llm-辅助更新-schemamd)**。

## 文档

- 📖 **[用户指南 `docs/guide/`](docs/guide/)** —— 安装、上手、各命令、Web/MCP 宿主(中英双语)
- 🏗️ [设计文档 `docs/DESIGN.md`](docs/DESIGN.md) —— 完整设计(面向开发,权威规格)
- 📋 [CHANGELOG.md](CHANGELOG.md) —— 版本与里程碑进展

## 开发

```bash
uv run guanlan init /tmp/demo   # 跑 CLI
uv run pytest                   # 跑测试
```

维护引擎是 `skills/guanlan-wiki/`(`SKILL.md` + `references/conventions.md` + 脚本),开发期命中 Agentao 的 repo-root skill 发现路径(`<工作目录>/skills/`),免安装。详见 [`CLAUDE.md`](CLAUDE.md)。

## 许可证

[Apache License 2.0](LICENSE)
