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

- **只接受 `.md`**。非 `.md`(PDF/DOCX/…)先用 [`guanlan convert`](07-多格式转换.md) 转成 `raw/<slug>.md` 再 ingest;整本书或长报告用 [`guanlan import`](#guanlan-import-src) 一步完成。
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

## `guanlan zip`

把知识库打包成可以直接交给别人的 zip。**零 LLM、只读知识库**,默认排除私有数据与隐藏文件。

```bash
guanlan -C my-wiki zip                    # 输出 ./my-wiki-YYYYMMDD.zip(在库内运行则放到库的上一级目录)
guanlan -C my-wiki zip -o share/kb.zip    # 指定输出路径
guanlan -C my-wiki zip --dry-run          # 只看会收入与排除什么
```

| 参数 | 说明 |
|---|---|
| `-o` / `--output` | 输出路径(不得在库内) |
| `--no-raw` | 不收 `raw/`,只分享 wiki |
| `--with-graph` | 同时收入 `graph/`(默认不收,接收方可用 `guanlan graph` 重建) |
| `--dry-run` | 只预览,不写 zip |
| `--force` | 输出文件已存在时覆盖 |
| `--json` | 输出 JSON 契约(含完整文件清单) |

只收 `AGENTAO.md`、`SCHEMA.md`、`wiki/`、`raw/`(可选 `graph/`);`.env`、`agentao.log*`、`.agentao/`(会话等)、
`workspace/`、`.trash/` 以及库根的其他文件一律不收。白名单目录内的隐藏文件、`Thumbs.db` 之类的系统杂项和符号链接也会跳过。
zip 顶层是库名目录,接收方解压到父目录即可使用。**只按路径过滤、不检查内容**,分享前请自行确认配置与正文不含不宜外传的信息。
详见 [`P3.12-知识库打包分享.md`](../../P3.12-知识库打包分享.md)。

---

## `guanlan import [src]`

一键入库:把一份原件(PDF/DOCX/… 或已是 `.md`)从解析一路做到摄入。适合整本书、长报告——它们转换后带着页眉页码、被分页切断的句子,篇幅也超出单次 `ingest` 的合适范围。**需配置模型**(逐份 ingest;有目录时还会审校一次目录)。

```bash
guanlan -C my-wiki import 书.pdf --dry-run   # 只解析、整理、拆分并打印摘要;不写 raw/、不入库
guanlan -C my-wiki import 书.pdf             # 打印摘要 → 交互确认 → 落 raw/ → 逐份 ingest
guanlan -C my-wiki import --status           # 查看未完成任务的进度(零 LLM)
guanlan -C my-wiki import --resume           # 从断点继续
guanlan -C my-wiki import --cancel           # 放弃等待确认或已暂停的任务
```

依次执行:

1. **解析**:与 `convert` 同一后端(`.docx` 先用 pandoc,其余按 MinerU → marker → pypdf 回退),原样结果留在 `workspace/parsed/<名称>.origin.md`。
2. **整理**(零 LLM):删页眉、页脚、页码和明确模式的水印,接回被分页切断的句子;书带目录时按目录定标题层级。
3. **拆分**(零 LLM):正文 ≤ 约 4 万字不拆;否则按章节标题切,每份 ≤ 约 4 万字,相邻小章合并。
4. **确认**:打印份数、每份字数、预计耗时和 `raw/` 冲突预检结果。
5. **落 `raw/` 并逐份 ingest**:不拆时落 `raw/<名称>.md`;拆分时落 `raw/<名称>/<名称>-NN-<章名>.md`,不再落整本。之后串行 ingest。

**确认之前任何一步失败,`raw/` 都不被触碰。**

| 参数 | 说明 |
|---|---|
| `src` | 待导入的文件,可在任意路径(不必先放进库里) |
| `--dry-run` | 只跑解析、整理、拆分并打印摘要;不建任务、不写 `raw/`、不 ingest。有目录时仍交模型审校一次 |
| `--yes` | 跳过交互确认(非交互终端必须给) |
| `--overwrite` | 允许覆盖**旧一键入库任务写下的同一组** `raw/` 文件 |
| `--resume` / `--status` / `--cancel` | 继续 / 查看 / 放弃本库未完成的任务(Web 发起的也可以) |
| `--json` | 结构化输出确认摘要 / 回执 |
| `--model` / `--thinking` | 透传给每份 ingest |
| `--backend` | 转换后端(同 `convert`,默认 `auto`) |
| `--no-toc-review` | 目录只用规则解析,不交模型审校(这样 `--dry-run` 就是零 LLM) |

要点:

- **暂停与继续**:准备阶段(解析 · 整理 · 拆分)按 Ctrl+C 立即停止,任务记为已放弃,重新导入即可(MinerU 有解析缓存,同一文件再解析很快)。入库阶段第一次 Ctrl+C = 当前这份入完后暂停;第二次 = 立即中止(当前这份可能只写了一半,之后先跑 `guanlan check`)。任意一份 ingest 失败也会暂停,修好后 `--resume` 从这一份重试。进度与日志在 `workspace/tasks/`,重启后照样能续。
- **一个库同时只有一个导入任务**,新任务须先完成或放弃旧任务。CLI 与 Web「导入」共用任务清单,可以互相续跑。
- **库级写锁**:准备阶段与落盘/入库阶段持有库根 `.guanlan.lock`(操作系统文件锁,进程退出自动释放);这期间其它写操作(`ingest`、`heal`、Web 写作业等)立即失败并显示谁在写。等待确认和暂停期间不持锁。
- **覆盖规则**:`raw/<名称>.md` 与 `raw/<名称>/` 一起检查。只有旧一键入库任务写下、且文件集合与本次完全相同时,才可加 `--overwrite` 覆盖;集合不同或有不属于任何导入任务的文件,一律报冲突、只能放弃,需先自行清理 `raw/`。
- 全部完成后报告 wiki 断链数的变化;断链多时可跑 `guanlan heal --dry-run` 看可补的页面。**不会自动 heal。**
- 退出码:全部成功 `0`;用法错误、冲突或库被占用 `1`;某份失败而暂停时,以那一份的退出码退出;用户 Ctrl+C 主动暂停记为成功(`0`)。

设计细节见 [`P4.30-一键入库.md`](../../P4.30-一键入库.md)。

---

## 其余命令

- **维护类** `health` / `lint` / `graph` / `reindex` / `heal` → [维护:体检与图谱](04-维护-体检-图谱.md)
- **多格式** `convert` → [多格式转换](07-多格式转换.md)(整本导入见上方 [`guanlan import`](#guanlan-import-src))
- **宿主** `web` / `mcp` / `im`(连带 `im-login` / `im-identify`) → [Web 宿主](05-web-宿主.md) / [MCP 宿主](06-mcp-宿主.md) / [IM 宿主](08-im-宿主.md)
- **`install-skill`**:把随包的三个 skill 装入 `~/.agentao/skills/`(外部真实库用;开发期免装,见 [安装](01-安装.md#从源码开发安装))。`--force` 覆盖重装。
  - `guanlan-wiki` —— 维护引擎(`ingest`/`query`/Web 问答都激活它)。
  - `pdf-to-markdown` —— 把上传的 PDF/DOCX/… 解析成 markdown 暂存物(`convert` 与 Web 上传走它)。
  - `flint-chart-author` —— 把结构化数据写成可被 Web 宿主渲染的 ` ```flint ` 图表块(见 [Web 宿主 · 富渲染](05-web-宿主.md))。
  后两个由 Agent **按需自行激活**,与 wiki 维护约定正交。
