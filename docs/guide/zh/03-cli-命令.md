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

## `guanlan grep <pattern>`

确定性**字面**检索:在 `wiki/` 正文里找**字面出现**的那个串,打印 `页:行号` + 片段。**零 LLM、零索引、零写盘。**

```bash
guanlan -C my-wiki grep "bge-m3"
guanlan -C my-wiki grep "决策P4.22-14" --json
guanlan -C my-wiki grep "[[某实体]]" --limit 200
```

| 参数 | 说明 |
|---|---|
| `pattern` | 要找的**字面**串(**不是正则**) |
| `--limit` | 全库命中上限(默认 50,须 ≥ 1;单页上限固定 5 条) |
| `--json` | 输出 JSON 契约 |

**什么时候用它、什么时候用 `search`**:`search` 按**含义**找,适合自然语言问题、概念、模糊表述;`grep` 按**原文字面**找,适合**专名、编号、版本号、标识符、固定短语**。两条路扫的是同一批页(`wiki/` 非 config 页),互不替代。

理由是 `search` 的分词把非 CJK 段切成 `[a-z0-9]+`,于是 `bge-m3` 成 `bge`+`m3`、`P4.22` 成 `p4`+`22`。召回不丢(查询与文档同一套分词),丢的是**精度**——一条标识符查询实际退化成"其中最稀有的那一段",数字段是高频噪声。想精确定位一个编号或版本号时,用 `grep`。

几条钉死的口径,**没有旗标可调**:输入**永不当正则**(`.*` 只匹配字面的 `.*`);**大小写不敏感**;**不做全半角/NFKC 归一**(归一过的"命中"在文件里并不字面存在,会让行号这条证据失真);frontmatter **不参与匹配**,但行号按**原文件**计,照着能直接跳过去。命中超限时回执会**明说截断**,不静默丢。

---

## 其余命令

- **维护类** `health` / `lint` / `graph` / `reindex` / `heal` → [维护:体检与图谱](04-维护-体检-图谱.md)
- **多格式** `convert` → [多格式转换](07-多格式转换.md)
- **宿主** `web` / `mcp` / `im`(连带 `im-login` / `im-identify`) → [Web 宿主](05-web-宿主.md) / [MCP 宿主](06-mcp-宿主.md) / [IM 宿主](08-im-宿主.md)
- **`install-skill`**:把随包的三个 skill 装入 `~/.agentao/skills/`(外部真实库用;开发期免装,见 [安装](01-安装.md#从源码开发安装))。`--force` 覆盖重装。
  - `guanlan-wiki` —— 维护引擎(`ingest`/`query`/Web 问答都激活它)。
  - `pdf-to-markdown` —— 把上传的 PDF/DOCX/… 解析成 markdown 暂存物(`convert` 与 Web 上传走它)。
  - `flint-chart-author` —— 把结构化数据写成可被 Web 宿主渲染的 ` ```flint ` 图表块(见 [Web 宿主 · 富渲染](05-web-宿主.md))。
  后两个由 Agent **按需自行激活**,与 wiki 维护约定正交。
