# CLI 命令

核心命令的逐条参考。所有命令(除 `init`)都接受全局 `-C/--dir` 指定知识库根,且 `-C` 可放在子命令前或后:

```bash
guanlan -C my-wiki check     # git 风格
guanlan check -C my-wiki     # 等价
cd my-wiki && guanlan check  # 省略 -C(默认当前目录)
```

`init` / `check` / `search` 零 LLM、可离线;`ingest` / `query` 需配置模型(经 Agentao)。退出码语义见 [维护:体检与图谱](04-维护-体检-图谱.md#退出码)。

---

## `guanlan init [path]`

在目录生成最小知识库模板。**确定性、零 LLM**;已存在文件不覆盖,可安全重复运行。

```bash
guanlan init my-wiki     # 在新目录初始化
guanlan init             # 就地初始化当前目录
guanlan init -C my-wiki  # 与位置参数等价
```

目标目录优先级:位置参数 `path` > 全局 `-C/--dir` > 当前目录。生成 `AGENTAO.md` / `SCHEMA.md` / `raw/` / `wiki/`(结构见 [快速上手](02-快速上手.md#1-初始化一个知识库))。

---

## `guanlan ingest <target>`

摄入一篇 **`.md`** 资料:Agent 读 `raw/` 下的源、生成或更新 `wiki/` 页面。**需配置模型。**

```bash
guanlan -C my-wiki ingest raw/source.md
guanlan -C my-wiki ingest raw/source.md --model <model-id>   # 覆盖默认模型
```

| 参数 | 说明 |
|---|---|
| `target` | `raw/` 下的 `.md` 文件,如 `raw/x.md`(**只接受 `.md`**) |
| `--model` | 覆盖 Agentao 默认模型 |

要点:

- **只接受 `.md`**。非 `.md`(PDF/DOCX/…)先用 [`guanlan convert`](07-多格式转换.md) 转成 `raw/<slug>.md` 再 ingest。
- **`raw/` 只读不可变**:写门禁在 Agentao 调用前后对 `raw/` 做快照(文件名+大小+mtime,必要时 SHA256)。若 Agent 误改/误删 `raw/`,退出码 `4`(`EXIT_RAW_MUTATED`)。
- 这是受治理的写操作:经 Agentao 子进程 + 单写者门禁。

---

## `guanlan query <question>`

对知识库提问。**默认只读**(基于已建好的 wiki,不写盘)。**需配置模型。**

```bash
guanlan -C my-wiki query "什么是 X?"
guanlan -C my-wiki query "什么是 X?" --backfill        # 把好答案沉淀回 wiki(走门禁)
guanlan -C my-wiki query "什么是 X?" --model <model-id>
```

| 参数 | 说明 |
|---|---|
| `question` | 问题文本 |
| `--backfill` | 把这次综合回填到 `wiki/syntheses/`,走**完整写门禁**(同 ingest 的子进程 + `raw/` 快照路径) |
| `--model` | 覆盖 Agentao 默认模型 |

`--backfill` 把一次性问答升级为一次受治理的写入;不加则纯只读、零写盘。

---

## `guanlan check`

确定性基础校验:**frontmatter + 断链 + sources**。**零 LLM。**

```bash
guanlan -C my-wiki check
guanlan -C my-wiki check --json     # 输出 JSON 契约(供脚本/CI)
```

校验失败退出码 `3`(`EXIT_CHECK_FAILED`)。这是**写门禁的把关项**——别名撞名/重复等也在此阻断。

---

## `guanlan search <query>`

确定性整页召回:**BM25 + CJK 2-gram**,title/alias 字段加权,按分数降序打印 top-N 页。**零 LLM、无持久化派生物。**

```bash
guanlan -C my-wiki search "关键词"
guanlan -C my-wiki search "关键词" --limit 20
guanlan -C my-wiki search "关键词" --json
```

| 参数 | 说明 |
|---|---|
| `query` | 检索词 |
| `--limit` | 召回条数(默认 10,须 ≥ 1) |
| `--json` | 输出 JSON 契约 |

它是 `query`/skill 的召回前端,也被 Web 的 `/api/search` 与嵌入式聊天的 `guanlan_search` 工具复用(同一内核)。

---

## 其余命令

- **维护类** `health` / `lint` / `graph` / `reindex` / `heal` → [维护:体检与图谱](04-维护-体检-图谱.md)
- **多格式** `convert` → [多格式转换](07-多格式转换.md)
- **宿主** `web` / `mcp` → [Web 宿主](05-web-宿主.md) / [MCP 宿主](06-mcp-宿主.md)
- **`install-skill`**:把随包 `guanlan-wiki` skill 装入 `~/.agentao/skills/`(外部真实库用;开发期免装,见 [安装](01-安装.md#从源码开发安装))。`--force` 覆盖重装。
