# 观澜 (GuānLán) — 设计文档

> 《孟子·尽心上》"观水有术，必观其澜"
>
> 在信息的汪洋中洞察脉络与趋势。

---

## 1. 项目目标

观澜是 [Karpathy LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 的一个实现：让 LLM Agent **增量地构建并持续维护一个结构化、互相链接的知识 wiki**，而不是每次提问都从原始文档里临时检索（传统 RAG）。知识被"编译"一次后持续保鲜，随每一篇新资料、每一次提问而复利增长。

**演进路线**

1. **个人版（先做）** —— 以 **Skills** 的形式接入 Agent，纯文件（Markdown），通过 **Agentao CLI + 极薄的 `guanlan` 包装器** 跑 ingest/query。这是 MVP，也是验证理念的最小闭环，**不依赖任何后端服务**。
   - **MVP 只承诺 Agentao + `guanlan` wrapper。** 设计落点是 Agentao 专有机制（`AGENTAO.md`、skill discovery、agent shell 调用脚本）与观澜自己的确定性门禁。其他 Agent CLI（Claude Code / Codex 等）因 wiki 是纯 markdown 而**可能适配**，但不进入 MVP 设计承诺。
   - Web UI 是 MVP 之后（P4）叠加的**可选宿主层**，不改变"纯文件"的本质——它只是 `ingest` 与问答的另一个入口。无 Web UI 时，整套东西用 CLI 一样跑通。
2. **企业版（后续）** —— 在个人版之上演进，支持**大数据量**、**多租户权限**、**7×24 自动富化**（技术栈不预先绑定，见 §6）。

**技术底座**

- **Powered by Agentao** —— 受治理的 Agent 运行时（Python，可嵌入 / CLI / ACP server）。观澜不自己造 Agent 内核，而是把"知识 wiki 的维护逻辑"做成 Agentao 的 **Skill + Python 脚本**（MVP，CLI 即可跑）；自定义 Tool 注入则留作 P4 之后的按需项。进程内嵌入在 P4 首次落地：**写** `ingest` 仍走 `agentao run` 子进程，**Web 问答**（含单轮与多轮）走只读进程内嵌入（见 [`P4-Web宿主.md`](P4-Web宿主.md)）。
- **观澜 Web 宿主（P4，可选）** —— 一个复用同一文件库的可选本地 Web 宿主，给 CLI 之外加一个图形入口（FastAPI/uvicorn 薄表现层）。**读写分线**：唯一写作业 `ingest` 复用 P2 子进程 + 单写者门禁；**所有问答**（一次性单轮与多轮对话）因 agentao CLI 无 session resume 而统一走进程内嵌入 `Agentao`（默认只读、token 流式，无 `/api/query`）。它不是 MVP 前置依赖；技术细节见 [`P4-Web宿主.md`](P4-Web宿主.md) 与**附录 A**。

### 核心设计原则

1. **markdown 始终是唯一事实来源（source of truth）。** 整个知识库就是一组本地 markdown 文件；任何数据库 / 向量索引 / 图谱 / 缓存都是**可从 markdown 完全、幂等重建的派生物**，绝不反向成为权威。git 只用于观澜项目源码与模板版本管理；用户 wiki 数据默认不纳入观澜 git，是否另行版本化由用户自己决定。
2. **Agent 全权拥有 wiki 层，人不直接写 wiki。** 人负责投喂资料、提问、给方向；记账（摘要、交叉引用、归档）全交给 Agent。
3. **`raw/` 只读不可变。** Agent 只读原始资料，永不修改，保证事实可追溯。
4. **确定性优先，LLM 调用统一走 Agentao。** 能用脚本确定性完成的（结构检查、wikilink 解析、格式转换）不调 LLM；需 LLM 的步骤统一经 Agentao runtime 治理。

---

## 2. 核心理念（Karpathy LLM Wiki）

传统 RAG 在每次查询时检索原始文档片段，LLM 每次都要"从零重新发现"知识，没有积累。观澜的不同之处：**wiki 是一个持久、复利的产物**——交叉引用已经建好，矛盾已经被标记，综述已经反映了你读过的一切。

三层架构（来自原始 pattern，见 [`../llm-wiki/llm-wiki.md`](../llm-wiki/llm-wiki.md)）：

| 层 | 内容 | 谁来写 | 可变性 |
|----|------|--------|--------|
| **Raw sources（原始资料）** | 文章、论文、图片、数据文件 | 用户投喂 | 只读，事实来源，Agent 永不修改 |
| **Wiki（知识层）** | LLM 生成的 markdown 页面：摘要、实体页、概念页、综述 | Agent 全权拥有 | Agent 创建/更新/维护交叉引用 |
| **Schema（配置/规约）** | 告诉 Agent wiki 如何组织、约定、工作流 | 人与 Agent 共同演进 | 本库 schema 在根级 `SCHEMA.md`（跟数据走）+ 维护引擎在 skill；`AGENTAO.md` 为行为约定层（详见 §4.3） |

三类核心操作：

- **Ingest（摄入）**：用户投放新资料 → Agent 阅读、抽取要点、写摘要页、更新 index、修订相关实体/概念页、追加 log。一篇资料可能触及 10–15 个页面。
- **Query（查询）**：对 wiki 提问 → Agent 先读 index 定位相关页 → 综合出带引用的答案。**好的答案可以回填进 wiki**（`syntheses/`），让探索也复利。
- **Lint（体检）**：周期性健康检查 —— 结构类（孤儿页、断链、缺失的实体页/交叉引用）先做；语义类（矛盾、过期论断、可补的资料缺口，需 LLM）后续再加（见 §4.4 / §8）。

> 核心洞察："维护知识库最累的不是阅读或思考，而是记账（bookkeeping）。" LLM 不会厌烦、不会忘记更新交叉引用、能一次改 15 个文件——维护成本趋近于零，wiki 才能持续保鲜。

---

## 3. 参考实现对比

我们研究了三个同源实现，观澜各取所长：

| 项目 | 形态 | 技术栈 | 观澜借鉴什么 |
|------|------|--------|--------------|
| **[SamurAIGPT/llm-wiki-agent](../llm-wiki/llm-wiki-agent)** | 纯 Agent Skill（无后端） | Markdown + `CLAUDE.md` + slash commands + Python `tools/` | **个人版的直接蓝本**：wiki 目录结构、5 个工作流、wikilink graph 构建思路、frontmatter/wikilink 约定、CJK 支持 |
| **[nashsu/llm_wiki](../llm-wiki/llm_wiki)** | 跨平台桌面应用 | Tauri(Rust) + React + LanceDB | 产品化思路：知识图谱可视化、向量+关键词混合检索、Web Clipper、本地 HTTP API + Agent Skill |
| **[garrytan/gbrain](../llm-wiki/gbrain)** | 生产级常驻 daemon | TypeScript/Bun + PGLite/Postgres+pgvector | **企业版蓝本**：类型化知识图谱（零 LLM 调用抽边）、混合检索+重排、OAuth 多租户 source 隔离、7×24 dream cycle |

**观澜的差异化定位**：不重造桌面应用（nashsu），而是把 wiki 维护逻辑做成 **Agentao 原生 Skill**，复用 Agentao 的运行时治理（权限、可观测、MCP）；个人版即 llm-wiki-agent 的"Agentao 化 + 增强"（CLI/Skill 优先），企业版参考 gbrain 的问题域与经验。

---

## 4. 个人版设计（MVP）

### 4.1 形态

**MVP = 纯文件 + Skill + 极薄包装器，无后端服务。** 整个知识库就是一个本地目录里的 markdown 文件，Agentao runtime 通过 Skill 读写它；`guanlan` CLI 只负责 init、调用 Agentao、运行确定性门禁与输出结果，不承载业务智能。

**维护引擎与知识库分离。** 观澜的 skill + 脚本（维护引擎）由**观澜项目/安装包**提供并安装一次，**不复制进每个知识库**；用户知识库目录只放数据与本库配置。`guanlan init` 在空目录生成最小模板。

### 4.2 目录结构

用户知识库目录（`guanlan init` 生成的最小模板；默认不纳入观澜项目 git）：

```
my-wiki/                       # 用户的知识库（普通工作目录；可由用户自行版本化）
├── AGENTAO.md                 # ← Agent 行为约束 + 指针（指向 guanlan-wiki skill 与 SCHEMA.md）
├── SCHEMA.md                  # ← 第三层 Schema：本库领域/主题/启用页面类型/本库约定（与 raw/、wiki/ 平级；排除出 index/graph/lint）
├── raw/                       # 第一层：原始资料（只读，事实来源）
├── wiki/                      # 第二层：Agent 全权生成的知识层（纯页面，无 config 混入）
│   ├── index.md               #   全量页面目录，每次 ingest 更新
│   ├── log.md                 #   append-only 时间线：## [YYYY-MM-DD] <op> | <title>
│   ├── overview.md            #   跨资料的活体综述
│   ├── sources/ entities/ concepts/ syntheses/   # 各类页面（自动创建）
└── graph/                     # 确定性 wikilink graph 产物（graph.json + graph.html，P3）
```

> 维护引擎在**观澜项目仓库**里（不复制进用户库）：`skills/guanlan-wiki/`（`SKILL.md` + `references/conventions.md`）；**确定性校验 `guanlan check` 在 `guanlan` 包内、不在 skill**（§4.6 / 决策1），P3 的 health/lint/graph **同样落包内**（`guanlan health/lint/graph`，见 [`P3-健康与图谱.md`](P3-健康与图谱.md) 决策P3-1）。两种运行方式，**各只对应一条路径，不混用**：
>
> - **开发期 = 仓库根即 sample wiki**：`working_directory` 直接设为观澜仓库根，仓库根同时放一份 sample wiki（`raw/`、`wiki/`、`SCHEMA.md`、`AGENTAO.md`）。此时 `skills/guanlan-wiki/` 正好命中 Agentao 的 repo-root 发现路径（`<wd>/skills/`），wiki 数据也在同一目录——免安装、改完即生效。
> - **外部真实 wiki = 全局安装**：把 skill 装入 `~/.agentao/skills/guanlan-wiki/`（cwd 无关），再以用户库为 `working_directory` 运行。用户库（`guanlan init` 生成）保持最小、只含数据与本库配置。
>
> 不存在"从外部 wiki 目录发现观澜仓库 skills/"的情形——外部 wiki 一律走全局安装，避免引入额外路径机制。

观澜项目仓库结构（引擎源码；开发期仓库根兼作 sample wiki）：

```
guanlan/                       # 观澜项目仓库（本仓库）；开发期 working_directory 即此
├── skills/guanlan-wiki/       # ← 维护引擎（repo-root 路径，开发期自动发现）
│   ├── SKILL.md
│   └── references/conventions.md
│                               # P2 无 scripts/：guanlan check 在包内（决策1）；P3 health/lint/graph 同落包内（决策P3-1）
├── guanlan/                   # ← 包：cli / init / runtime / gate / check / ingest / query（P2）
├── examples/                  # ← guanlan init 模板（入库，供参考）
│   ├── AGENTAO.md
│   └── SCHEMA.md
├── AGENTAO.md  SCHEMA.md       # ← 开发期由 examples/ 拷入的 sample 配置（可能含本机路径/开发者私有设置，.gitignore，不入库）
├── raw/  wiki/  graph/         # ← sample 数据与派生（仅开发用，.gitignore，不提交远程）
└── docs/DESIGN.md
```

### 4.3 Schema / 约定放在哪里

Schema 是 Karpathy 三层之一，与 `raw/`、`wiki/` 平级，因此落为**仓库根的一等文件 `SCHEMA.md`**——不塞进 `wiki/`（会污染 Agent 全权生成的内容层），也不塞进 `AGENTAO.md`（厂商相关、每 session 全量入上下文）。三类约定按职责分家：

- **`SCHEMA.md`（仓库根）** —— **本库约定**：领域/主题、启用的页面类型、本库自定义规则、演进中的论点。每库不同、人与 Agent co-evolve、随 markdown 文件走、自描述。ingest/query 时读，且与 `index.md`/`log.md`/`overview.md` 一样**排除出 index/graph/lint 扫描**（config 非 content）。
- **`AGENTAO.md`（仓库根）** —— **Agent 行为约束**：角色 + 硬约束（永不改 `raw/`、markdown 是事实来源、页面必带 frontmatter、术语转 `[[wikilink]]`、query 答案必引来源）+ 运行时偏好 + 指针（"遵循 `guanlan-wiki` skill；本库 schema 见 `SCHEMA.md`"）。只放精简硬规则（每 session 自动入上下文），大块可复用约定下沉 skill。
- **`guanlan-wiki/` skill（观澜安装包提供，不在用户库内）** —— **维护引擎**：`SKILL.md`（工作流）+ `references/conventions.md`（通用默认约定：页面类型/frontmatter/命名/index·log 格式/模板，按需载入，`SCHEMA.md` 可覆盖）。wiki-无关、可复用、可独立升级。**P2 不含 `scripts/`**：确定性校验经包内 `guanlan check`（§4.6 / `P2-最小闭环.md` 决策1）；skill 只 shell 调它。

一句话：**本库约定 → `SCHEMA.md`；行为约束 → `AGENTAO.md`；工作流 → skill；写操作门禁 → `guanlan` wrapper。** 知识库本体（`SCHEMA.md` + `wiki/`）agent-无关、可移植；MVP 宿主只承诺 Agentao + `guanlan` wrapper，未来可适配其他 Agent CLI（见 §1）。

### 4.4 工作流

**MVP 最小闭环（P1–P2）= init + ingest（仅 `.md`）+ query + 基础校验。** 这是"能在 CLI 里稳定摄入和查询的文件型知识库"的最小集；health / lint / `graph.html` 是 P3 的增强，不是 MVP 核心。

> **P2 的实现级细化见 [`P2-最小闭环.md`](P2-最小闭环.md)**：模块落点、`raw/` 快照与 check 的数据结构/契约、退出码、prompt、Agentao 集成与测试计划，以及对本文若干处的具体化决策（§4.6 check 落点、§4.7 快照算法等）。

| 工作流 | 触发 | 步骤要点 | 阶段 |
|--------|------|----------|------|
| **init** | `guanlan init` | 在空目录生成最小模板：`AGENTAO.md` / `SCHEMA.md` / `raw/` / `wiki/`（含 index·log·overview） | P1 |
| **ingest** | `guanlan ingest raw/x.md` | wrapper 调用 Agentao + skill：读 `.md` 源→读 index/overview→写 source 页→更新 index/overview→建/更新实体&概念页→标记矛盾（格式见 §4.5）→追加 log→wrapper 强制跑门禁。**单轮优先，必要时分步**（不承诺固定调用次数；一篇资料可能触及多页） | P2 |
| **query** | `guanlan query "…"` | wrapper 调用 Agentao + skill：读 index 定位→读相关页→综合带 `[[页]]` 引用的答案；`index.md` + 2-gram 粗召回不中时，Agent 可扫相关目录或请用户补关键词（graceful fallback）。默认只读；如要回填 `syntheses/`，走显式 `guanlan query --backfill "…"` 并同样跑门禁 | P2 |
| **基础校验** | ingest / query backfill 收尾**强制**运行 / `guanlan check` | 独立 `guanlan check` 只做 frontmatter + 断链 + `sources` 解析；ingest / `--backfill` 的 wrapper 门禁在此之上**额外**做 `raw/` 调用前后快照比对（独立 check 无"调用前"基线，故不含此项）。**零 LLM**，见 §4.7 | P2 |
| **health** | `health` | 结构检查：空页/桩页、index 与磁盘同步。**零 LLM** | P3 |
| **lint** | `lint` | **只做结构 lint**：孤儿页、断链、缺失实体页。**零 LLM** | P3 |
| **graph** | `build graph` | **只做确定性**：解析 `[[wikilink]]`→边，输出 `graph.json` + 自包含 `graph.html`。**零 LLM** | P3 |

> LLM 只用于 **ingest / query**（单轮优先、必要时分步）；其余工作流全部零 LLM。
>
> **MVP ingest 只承诺 `.md`**，多格式推到 **P5**。**语义 lint**（矛盾/过期/资料缺口，需 LLM）、**graph 增强**（LLM 推断边、Louvain、增量缓存）、**图感知语义 lint** 均**不进 P3**，作为后续优化项（见 §8）。P3 的 `guanlan lint` 本身就是**图感知的确定性结构 lint**（孤儿/断链/缺失实体，复用 wikilink graph，零 LLM）；先用确定性的 health + 结构 lint + wikilink graph 快速闭环，避免过早引入 LLM 成本。

### 4.5 数据约定

**页面 frontmatter（统一）**

```yaml
---
title: '页面标题'        # 字符串值用单引号（决策7：根除双引号嵌套的 YAML 解析失败）
type: source | entity | concept | synthesis
tags: []
sources: []           # 支撑本页的 source slug 列表
last_updated: YYYY-MM-DD
---
```

- **命名**：source 用 `kebab-case`（同源文件名）；entity/concept 用 `TitleCase.md`。
- **wikilink**：正文用 `[[PageName]]`，大小写不敏感按 stem 解析；ingest 后校验断链。
- **index.md**：按 Overview / Sources / Entities / Concepts / Syntheses 分区，每行 `- [标题](路径) — 一句话`（Overview 区仅链到 `overview.md`，不重复其正文）。
- **log.md**：`## [YYYY-MM-DD] <op> | <title>`，可被 `grep "^## \[" wiki/log.md | tail` 解析。
- **CJK**：query 对中文用 2-gram 滑窗匹配（参考 llm-wiki-agent 的 `examples/cjk-showcase`）；粗召回不足时走上述 query 的目录扫描/补词兜底，按需升级路径见 §8（**不优先上分词**）。
- **矛盾标记（卖点核心，必须有确定格式）**：ingest 时若 LLM 发现新资料与既有页冲突，**就地**在相关 entity/concept 页维护一节 `## ⚠️ 矛盾与存疑`，每条一行：
  `- <断言 A>（[[source-a]]）↔ <断言 B>（[[source-b]]）— <open|resolved> · <备注>`；
  并向 `log.md` 追加一条（有无矛盾以 `## ⚠️ 矛盾与存疑` 标题为准，不另存 frontmatter 布尔）。**ingest 只做"发现即就地标记"**；系统性复检与状态流转（open→resolved、过期论断、跨页矛盾发现）属语义 lint，P3 之后再加（见 §4.4 注 / §8）——P3 的确定性 health/lint **不统计矛盾**。

### 4.6 工具（确定性脚本，Python 3.12；P2 的 check 在 `guanlan` 包内）

- **确定性脚本（零 LLM）**：check（**P2 基础校验**：frontmatter + 断链 + `sources` 解析；`raw/` 不变性由 wrapper 快照比对兜底，见 §4.7，不在脚本里巡检）、health（**P3** 结构检查：空页/桩页、index 同步）、lint（**P3** 图感知结构 lint：孤儿/断链/缺失实体）、graph（**P3** 确定性 wikilink graph + 自包含 `graph.html`）。P2 只交付 check，避免把 P3 的 health/lint 提前塞进 P2；P3 三者落点与契约见 [`P3-健康与图谱.md`](P3-健康与图谱.md)。
  - **check 的唯一实现落在 `guanlan` 包内（`guanlan/check.py`），暴露为 `guanlan check` 子命令与 `python -m guanlan.check`**；wrapper 门禁直接 import 同一函数，skill 指示 agent shell 调 `guanlan check`。不在 skill `scripts/` 下另写一份——否则开发期（repo `skills/`）与安装期（`~/.agentao/skills/`）skill 路径不同会逼 wrapper 解析该路径并引入双实现分叉。理由与契约见 [`P2-最小闭环.md`](P2-最小闭环.md) §10 决策1。
- **ingest / query 的 LLM 部分不写成脚本** —— 由 `guanlan-wiki` skill + `agentao run` 子进程完成（P2 路线，见 §5.1 / `P2-最小闭环.md` §4）；in-process 嵌入（`Agentao.chat()` / `LLMClient`）留待 P4。脚本只做确定性工作。（结构 lint 为零 LLM 的结构检查，属 P3；语义 lint 以后再加，见 §8。）
- **推到 P5**：多格式转换 adapter（统一接口 `convert(file) -> md`），先以 **markitdown 兜底**；P5 再评估**更高质量的 PDF backend**，具体候选选型另开实现方案。MVP ingest 只吃 `.md`。

> 关键决策：**LLM 调用统一走 Agentao runtime**，脚本保持零 LLM。这样 provider、密钥、权限、可观测都由 Agentao 治理，观澜不重复造轮子。

### 4.7 Wrapper 与确定性门禁

需要一个真实但很薄的 `guanlan ingest/query` 包装器。原因是：Agent prompt 只能提示"收尾要校验"，不能保证每次都执行；frontmatter、断链这些约束应由确定性代码兜底。wrapper 是 MVP 的控制边界，不是后端服务，也不替代 Agentao 的 LLM 能力。

- **`guanlan ingest <raw-file>` 与 `guanlan query --backfill` 是唯二写入口。** wrapper 经 `agentao run` + `guanlan-wiki` skill 让 Agent 写 `wiki/`，收尾强制运行 `guanlan check`：通过则保留本次 wiki 改动；失败则报告失败项，**`wiki/` 改动原样留在磁盘供检查**，由用户手动修正或删除（单用户 MVP 可接受）。需要一键撤销者，可自行把 `wiki/` 纳入 git（可选，非 MVP 机制）。
- **`guanlan query` 默认只读。** 普通 query 不写 wiki，只输出答案；只有显式 `--backfill` 才允许写 `wiki/syntheses/`，并与 ingest 使用同一套门禁。
- **`raw/` 不变性由 wrapper 确定性快照兜底，不押在权限配置上。** `ingest` / `--backfill` 在调用 Agentao **前**对 `raw/` 取一次快照（**P2 落地为：按相对路径建键的内容 SHA256**，见 [`P2-最小闭环.md`](P2-最小闭环.md) §5.1 / §10 决策4——个人级数据量上 SHA256 成本可忽略，且不受 mtime 漂移/伪造影响），收尾再比对；任何增、删、改、重命名都判门禁失败并报告。此检查自包含、可测，不依赖 Agentao 权限规则的实参键名细节，也能拦住 shell 里 `mv`/`cp`/`rm`/`python` 等绕过 `write_file` 的写入——而单条用户级 deny 规则做不到这点。`AGENTAO.md` 仍声明"永不改 `raw/`"作为 Agent 软约束；用户若愿意，也可在 `~/.agentao/permissions.json` 额外加 deny 规则作纵深防御，但 MVP 的**硬约束是 wrapper 快照**，不是手工权限配置。
- **门禁内容保持确定性且最小。** P2 门禁 = `guanlan check`（frontmatter + wikilink 断链 + `sources` 解析）+ `raw/` 快照比对两部分；均零 LLM、不做语义判断。
- **不用 git commit 表达原子性。** wiki 数据默认不进观澜项目 git，MVP 也不要求用户知识库是 git 仓库。若用户自行把 wiki 放进 git，`guanlan` 可后续加可选 `--commit`，但这不是 P1–P2 的一致性前提。

> **wiki 数据不进观澜项目 git。** `raw/`、`wiki/`、`graph/` 以及开发期由 `examples/` 拷入仓库根的 sample `AGENTAO.md`/`SCHEMA.md`（可能含本机路径/开发者私有设置）都默认 `.gitignore`、仅本地；观澜项目仓库只版本化引擎源码（`skills/guanlan-wiki/`）、文档（`docs/`）与 init 模板（`examples/AGENTAO.md`、`examples/SCHEMA.md`）。用户如果想给自己的 wiki 建 git 仓库，可以自行开启，但这不是 MVP 的一致性机制。

### 4.8 分发与发布（PyPI / CI）

观澜以 wheel 形式发到 PyPI，安装即得 `guanlan` CLI 与随包携带的维护引擎 skill；细化操作手册见 [`发布到-PyPI.md`](发布到-PyPI.md)。

- **发布名 ≠ 导入名。** PyPI **发布名是 `guanlan-wiki`**（裸名 `guanlan` 已被一个无关项目占用）；**导入名与 CLI 仍是 `guanlan`**（`pyproject.toml` 的 `packages = ["guanlan"]` 与 `[project.scripts] guanlan` 不变）。装：`pip install guanlan-wiki`；可选 Web 宿主：`pip install 'guanlan-wiki[web]'`（`web` extra 带 fastapi/uvicorn/markdown，核心安装不背，见 §5.2 / [`P4-Web宿主.md`](P4-Web宿主.md) 决策P4-2）。
- **构建（hatchling）把模板与引擎一并打进 wheel。** `[tool.hatch.build.targets.wheel.force-include]` 在构建期把 `examples/{AGENTAO.md,SCHEMA.md,wiki}` 拷为 `guanlan/_templates/`、把 `skills/guanlan-wiki/` 拷为 `guanlan/_skill/guanlan-wiki/`——故 **`examples/` 是 init 模板的单一事实源**（开发期 `init` 直接读仓库根 `examples/`，安装态读包内 `_templates/`，见 `guanlan/init.py:_templates_dir`），维护引擎 skill 随 wheel 携带、运行时幂等装入 `~/.agentao/skills/`（与 §8「skill 分发」一致）。Web 前端静态资源（`guanlan/web/static/*`）随 `packages` 自动入包。
- **发布走 GitHub Actions Trusted Publishing（OIDC，零 token）。** 推 `v*` tag 触发 `.github/workflows/release.yml`：`uv build` → `twine check` → `pypa/gh-action-pypi-publish` 经 OIDC 上传（仓库不存任何 API token，附 PyPI 数字签名）；gated 在 `pypi` environment，需先在 PyPI 配 pending publisher。另有 `.github/workflows/ci.yml`：push `main` / 对 `main` 发 PR 时跑 `ruff check` + 全量 `pytest`（全程离线、无需 API key——测试用 fake runner / monkeypatch 不打真实 LLM）。
- **版本遵循 PEP 440。** 两次发布之间 `main` 的 `version` 保持 `X.Y.Z.devN`（标记非发布态、`pip install` 默认装不到）；发布时去掉 `.devN`、打 `vX.Y.Z` tag 触发上传。

---

## 5. Agentao 集成

Agentao 是 Python 的**受治理 Agent 运行时**（约束 / 连接 / 可观测）。MVP 通过 `guanlan` wrapper **以 `agentao run` 子进程**执行 ingest/query（非进程内嵌入）；in-process 库嵌入（`Agentao(...)`）留待 P4。此分界与 `agentao` 的 `docs/EMBED_FOR_AGENTS.md` §0 一致（"跑 prompt、拿退出码"走 `agentao run`，非嵌入），细节见 [`P2-最小闭环.md`](P2-最小闭环.md) §4。

### 5.1 MVP（CLI）集成

MVP 使用 Agentao 的文件约定 + skill discovery，并增加一个只负责编排和门禁的 `guanlan` wrapper：

- **`guanlan-wiki` skill** —— 工作流指令里让 agent 直接 shell 调用确定性脚本（如 `guanlan check`）。**激活方式**：skill 被发现后默认**不自动激活**（实测构造后 active 为空），故 `guanlan` wrapper 经 `agentao run --skill guanlan-wiki` **显式激活**（不依赖 `description` 触发，保证工作流稳定入上下文）；`description` 触发仅用于 wrapper 之外的交互式直接调用（用户在 agent 里自然语言说"摄入…"时）。发现方式（Agentao skill 路径见 `agentao/skills/manager.py`），开发与外部各一条、不混用：
  - **开发期**：`working_directory` = 观澜仓库根，skill 在 `skills/guanlan-wiki/`，命中 repo-root 路径（`<wd>/skills/`，优先级最高）自动发现，**免安装**；仓库根兼作 sample wiki（见 §4.2）。
  - **外部真实 wiki**：skill 装在**全局 `~/.agentao/skills/guanlan-wiki/`**（cwd 无关），以用户库为 `working_directory`。用户库保持最小、不含 skill。**安装态下 wrapper 自动兜底**：skill 随 wheel 携带（`guanlan/_skill/`），ingest/query 前若发现路径里都没有 skill 就**幂等装入全局**（与 Agentao 自带 skill 的 bootstrap 同理），也可显式 `guanlan install-skill [--force]`。落点见 `guanlan/skill.py`。
- **`guanlan` wrapper** —— `init` 生成模板；ingest/query 经 `agentao run --prompt … --skill guanlan-wiki --permission-mode <read-only|workspace-write> --interaction-policy reject --format json` 驱动（显式激活 skill、设权限姿态、非交互、解析 `RunResult.final_text`）。`ingest / query --backfill` 收尾强制门禁：跑 check（frontmatter + 断链 + `sources` 解析）并比对调用前后的 `raw/` 快照，两项全过才算成功；`query` 默认只读（且以 `read-only` 姿态纵深防御）。
- **`AGENTAO.md`**（用户库根，自动读取）：行为约束 + 指针（→ skill 与 `SCHEMA.md`）。
- **`.agentao/*.json`（可选）**：`guanlan init` **默认不生成**；仅当用户需要 MCP、运行模式等再自行添加。
- **权限（可选纵深防御）**：`raw/` 不变性由 wrapper 快照兜底（§4.7），不依赖权限配置；如需额外防御，可在 Agentao **用户级** `~/.agentao/permissions.json` 加 deny（项目级 `.agentao/permissions.json` 会被 Agentao 忽略并打 warning，故仓库内固化权限无效）。

### 5.2 P4 Web 宿主（可选）

P4 给同一文件库加一个**可选的本地 Web 图形入口**。**实现级细化见 [`P4-Web宿主.md`](P4-Web宿主.md)**——它把 P4 收敛为"薄 HTTP（FastAPI/uvicorn）表现层 + 复用既有命令"，并采**读写分线**：唯一写作业 `ingest` 仍走 `agentao run` 子进程 + 单写者门禁（P2 不动）；**所有问答**（一次性单轮与多轮对话）因 agentao CLI 无 session resume，统一进程内嵌入 `Agentao`（`.arun`、默认只读、token 流式，逐一化解 P2 §4.3 嵌入四坑），Web 不设 `/api/query`。仍**不做** Tool 注入、不做可写工作会话、不做会话落盘（见该文决策P4-1 修订 / P4-8 与下文附录 A）。Web UI 与嵌入式细节**另见附录 A**，均不属于 MVP。

---

## 6. 企业版演进（方向，暂不展开）

企业版在个人版跑通后再开**单独的设计文档**详述，并在那时选型。此处只锁定三条方向性原则（参考 [gbrain](../llm-wiki/gbrain) 的问题域与经验，不破坏个人版前提），**不预先绑定任何技术栈或路线**：

1. **markdown 仍是唯一事实来源**——任何派生检索 / 索引层都只是可从 markdown 幂等重建的产物；git 仍只是可选版本化手段。
2. **检索随规模升级、权限按 source 隔离**——index-only 撑不住时再引入更强检索；多租户按 source 作用域隔离 + 信任边界（本地全权 / 远程 scoped）。
3. **维护尽量自动化**——把 health/lint/富化做成后台定时任务，让 wiki 自保鲜。

> 具体技术选型（存储/向量库、检索与重排、权限协议、自动富化等）留待企业版设计文档评估，gbrain 有成熟实现可参考。

---

## 7. 里程碑

**个人版（P）**

| 阶段 | 目标 | 交付 |
|------|------|------|
| **P1 — 骨架** | init + Schema 落地 | 观澜仓库 `skills/guanlan-wiki/`（开发期 repo-root 自动发现）；`guanlan init` 生成最小模板（`AGENTAO.md` 含 skill 指针 / `SCHEMA.md` / `raw/` / `wiki/` 含 index·log·overview）；wiki 数据默认 `.gitignore` |
| **P2 — 最小闭环** | wrapper + ingest + query + 基础校验 | `guanlan ingest/query/check`；`.md` ingest；source/entity/concept 页生成；wikilink + frontmatter + `sources` + 断链校验 + `raw/` 快照比对（见 §4.7）；query 带引用；纯 CLI。**实现级细化见 [`P2-最小闭环.md`](P2-最小闭环.md)** |
| **P3 — 健康与图谱** | health + 结构 lint + 确定性 graph | `guanlan health/lint/graph`：结构健康检查（桩页/index 同步）、结构 lint（孤儿/断链/缺失实体，零 LLM）、确定性 wikilink graph + 自包含 `graph.html`。**实现级细化见 [`P3-健康与图谱.md`](P3-健康与图谱.md)** |
| **P4 — Web 宿主（可选）** | 复用同一文件库的本地 Web 图形入口 | `guanlan web`：浏览 wiki + 触发 `ingest` + **与 agent 单轮/多轮问答** + 跑 check/health/lint + 看 graph；FastAPI/uvicorn 薄表现层，写作业走子进程、所有问答走只读进程内嵌入（默认只读、token 流式）。**实现级细化见 [`P4-Web宿主.md`](P4-Web宿主.md)** |
| **P5 — 多格式与自动化** | 多格式摄入、定时同步 | 多格式转换 adapter（markitdown 兜底，评估更高质量 PDF backend）；docx/web clip 等摄入；nightly health/lint |

**企业版（E，方向；个人版跑通后另立设计文档详述，见 §6）**

| 阶段 | 目标 | 方向 |
|------|------|------|
| **E1 — 存储与检索升级** | 大数据量 | markdown 仍是事实来源；index-only 撑不住时引入更强检索（选型另文） |
| **E2 — 多租户权限** | 权限隔离 | source 级作用域 + 信任边界（本地全权 / 远程 scoped） |
| **E3 — 自动富化** | wiki 自保鲜 | health/lint/富化做成后台定时任务 |

---

## 8. 待定问题 / 风险

- **LLM 调用归口**：脚本保持**零 LLM**；ingest/query 的 LLM 部分由 `guanlan-wiki` skill 驱动 Agentao 完成（不让脚本自带 litellm / 自管密钥，也不把 LLM 能力反向塞进 Tool 接口）。语义 lint 后续再加。
- **wrapper 必要性**：MVP 需要真实的 `guanlan ingest/query`，但只做编排与确定性门禁；不做独立后端，不自管模型，不把 ingest/query 逻辑写成脱离 Agentao 的大脚本。
- **skill 分发**：开发期用观澜仓库 `skills/guanlan-wiki/`（repo-root 自动发现，免安装，见 §5.1）；安装态下 skill 随 wheel 携带，wrapper 在 ingest/query 前**幂等装入全局 `~/.agentao/skills/`**（也可 `guanlan install-skill`），故 P2 既能在仓库内、也能在外部安装库跑通（落点 `guanlan/skill.py`）。分发渠道已落地：以 `guanlan-wiki` 发到 PyPI、skill 随 wheel 携带（见 §4.8）。
- **个人版 → 企业版的数据迁移**：markdown 为事实来源的前提下，索引重建应可幂等、增量（SHA256 缓存）。
- **CJK 检索质量（按需升级，不预先加机制）**：MVP 用 `index.md` + 2-gram 粗召回，召回不中时 Agent 扫目录或请用户补关键词兜底；后续仅在漏召回确有实证时再评估 aliases 或轻量检索，分词不优先（逐级增强备选见 `docs/backlog/notes/cjk-retrieval-enhancements.md`，具体规则待实现增强时再定）。
- **语义 lint（P3 之后）**：矛盾、过期论断、资料缺口等需 LLM 的检查，在结构 lint 跑通后再加；成本较高，应可按需触发而非每次 lint 全量跑。
- **graph 增强（P3 之后）**：在确定性 wikilink graph 之上是否加 LLM 推断边（`INFERRED`/`AMBIGUOUS`）、Louvain 社区检测、图感知**语义** lint、SHA256 增量缓存——成本/收益待评估，需可关闭与采样策略。（P3 的 `guanlan lint` 已是图感知的**确定性结构** lint，不在此推后项内。）

---

## 9. 参考

- Karpathy LLM Wiki 原始 pattern：[`../llm-wiki/llm-wiki.md`](../llm-wiki/llm-wiki.md) · [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- 纯 Skill 实现（个人版蓝本）：[`../llm-wiki/llm-wiki-agent`](../llm-wiki/llm-wiki-agent) · [github](https://github.com/SamurAIGPT/llm-wiki-agent)
- 桌面应用实现：[`../llm-wiki/llm_wiki`](../llm-wiki/llm_wiki) · [github](https://github.com/nashsu/llm_wiki)
- 生产级 daemon（企业版蓝本）：[`../llm-wiki/gbrain`](../llm-wiki/gbrain) · [github](https://github.com/garrytan/gbrain)
- Agentao 运行时：受治理的 Agent 运行时（Python，可嵌入 / CLI / ACP server）；核心 API、ACP 协议、配置与示例见其自带文档

---

## 附录 A — P4 Web 宿主（非 MVP）

P4 可选：一个本地 Web host，复用同一 wiki 目录，给 CLI 之外加图形入口。**实现方案已细化为 [`P4-Web宿主.md`](P4-Web宿主.md)**，关键形态：**FastAPI/uvicorn（ASGI）薄表现层 + 自包含静态前端**把既有命令搬进浏览器，仅监听 `127.0.0.1`、单用户、强制 `workers=1`。**读写分线**：唯一写作业 `ingest` 复用 P2 `agentao run` 子进程 + 单写者门禁；**所有问答**（一次性单轮与多轮对话）因子进程无 session resume 而统一进程内嵌入 `Agentao`（`.arun`、默认只读、回调→token 流式，化解 P2 §4.3 四坑），Web 不设 `/api/query`。选 ASGI 是押注流式/多用户的"向前赌"，并以工程约束补偿 async 与阻塞负载不匹配（决策P4-2）。
**仍不在 P4**：Tool 注入、把写（`ingest`）迁到嵌入、可写多轮工作会话、仓库内权限编程注入、跨会话**写**并发、ACP 备选——作为后续按需项，真要做时另开实现方案文档（参考 Agentao 的示例与嵌入式开发指南）。
