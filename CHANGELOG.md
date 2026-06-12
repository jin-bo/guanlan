# 更新日志

本项目所有显著变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。版本号单一来源为 `guanlan/__init__.py`。

## [0.1.6] - 2026-06-12

开启 **P5「语料规模化：多格式 + 检索」** 里程碑的检索部分——把召回从 `index.md` 目录扫描升级为
**确定性全页正文检索**，并接入长驻 Web 进程。仍**零持久化派生物**（markdown 仍是唯一事实来源、
索引可幂等重建），不引入新退出码、不动门禁、不加 SSE、不碰数据模型，`raw/` 仍对 Agent 只读。

### 新增

- **检索层 `guanlan search`（P5.0）** —— 零-LLM、**无状态**确定性 **BM25 + CJK-2-gram 全页召回**
  原语，作用于 `iter_pages` 内容页。title/alias 字段加权（**BM25F-lite**：加权只提 `tf`、不动
  `dl`/`avgdl`），字节稳定输出（取整分数 + 路径 tie-break）。抽出非打印 `search_pages()` 核心
  （`guanlan/search.py`）供 web/mcp 复用，`tokenize()` 为 CJK-2-gram **单一归口**，per-root
  `CorpusCache` mtime 记忆。作为 query/skill 的召回前端（附加式，优雅回退到 `index.md` 扫描）；
  向量 / 重排 / 分词仍延后（留 E1）。落地小设计见 [`docs/P5.0-检索层.md`](docs/P5.0-检索层.md)。
- **Web 检索接入与长驻缓存（P5.1）** —— 把 P5.0 内核接入长驻 Web 进程：`create_app()` 构建一个
  共享 `app.state.search_cache = CorpusCache()`，**同时**驱动只读 `GET /api/search?q&limit` 端点
  （直接 `score(...)`、**不**走 CLI shell-out、`anyio.to_thread` 离线程）**与**嵌入式聊天的
  `guanlan_search` 只读宿主工具（`is_read_only=True`，为只读 Web 会话提供**首个确定性召回入口**）。
  `search.search_result_dict()` 作**单一归口**统一 CLI/Web 字段+取整；错误体为 HTTP-native
  `422 + {"detail":…}`。`SKILL.md` 与 `QUERY_PROMPT` 改为**传输中立**（host tool / CLI / 扫描回退），
  顺带修好只读 CLI `query` 的死指令。前端 `#wiki-search` 防抖接入 `/api/search?limit=20`，含
  `searchToken` 陈旧响应守卫。落地小设计见 [`docs/P5.1-Web检索接入.md`](docs/P5.1-Web检索接入.md)。

### 其他

- 附三份后续路线**设计稿**（仅设计、无代码，本版不交付）：P3.5 图谱分析（确定性 Louvain 社区 +
  三条 advisory lint 发现）、P4.10 MCP 宿主（`guanlan mcp` 只读 server，P4 宿主层第二传输 stdio）。

仍 `workers=1` + 仅 127.0.0.1（决策P4-2/P4-4 红线不破）；不属 E1（无向量/重排/持久化索引）。由
`tests/test_search.py` 与 `tests/test_web.py` / `tests/test_web_i18n.py` 守恒，全量 610 passed + ruff 通过。

## [0.1.5] - 2026-06-11

P4 Web 宿主之上又一个半相位（P4.9），把单用户宿主以**只读部署**对多个用户开放：每个浏览器
持自己的会话 UUID（`?c=`）各聊各的、互不可见。**E2「多租户权限」的会话分租前哨**——只隔离
会话历史、KB 内容对所有用户全共享只读，**不写一行身份/鉴权代码**。仍不引入新退出码、不动门禁、
不加 SSE、不碰数据模型，markdown 仍是唯一事实来源，`raw/` 仍对 Agent 只读。

### 新增

- **只读多会话部署 `--reader`（P4.9）** —— `guanlan web --reader` 起一个**只读部署**承接多用户
  并发问答。隔离不靠账号、不靠 owner 表，而靠前端已在用的 122-bit 能力 UUID（`?c=<conversation_id>`）：
  关掉 `GET /api/conversations` 枚举端点后，他人 id 不可发现即够不到他人会话（capability URL 模型，
  威胁边界如实记于设计 §3）。`create_app(reader=True)` **不注册**全部写路由（`raw`/`upload`/`ingest`/
  `heal`/`backfill`/`workspace 删`/`GET /graph` 重建/`chat undo`，命中 404/405、物理写不了 KB），
  并在 `create_app` 内**强制** `session_persist=False` + `mode="read-only"`（覆盖入参，任何 caller
  直建也零写、只读姿态）；`/api/chat/{id}/mode` 拒切 `workspace-write`。**默认对 KB 零字节写入**
  （`session_persist` 关、`agent_log` 默认关）。
- **三态 `--agent-log` / `--no-agent-log`（决策P4.9-15）** —— 日志旗标改 `BooleanOptionalAction`，
  默认归口在 `serve`：非 reader 未指定→开（旧默认不回归）、reader 未指定→关、显式旗标任意模式覆盖。
  开日志时先**独立写探针** `open(<kb>/agentao.log,"a")`，只读挂载/既有日志不可写 → 启动即
  `EXIT_USAGE` 早失败（`require_kb_root` 只查存在性、给不了此保证）。
- **`--max-conversations` 可配（决策P4.9-18）** —— 内存会话硬上限从模块常量改为参数（默认 100，
  多用户部署可调高）；`< 1` 即 `GuanlanError(EXIT_USAGE)`，**权威校验落在 `create_app`**、堵直建坏配置。
- **内存会话 idle 回收（决策P4.9-6）** —— `ConversationStore` 加 idle TTL（默认 30min，`time.monotonic`
  可注入），在「新建/恢复」锁内惰性淘汰久无活动**且无在飞 turn** 的会话，缓解多并发用户顶满上限；
  **仅 reader 启用**（非 reader 单用户无压力、且会丢可写会话的 undo 日志/姿态）。
- **前端 reader 模式（`/api/info` 增 `reader` 字段驱动）** —— 隐藏写/历史枚举/维护诊断 chrome
  （新会话/发送/Wiki 导航/语言保留）；`?c=` 恢复改走**按-id 探针** `GET /api/chat/{id}/info`、**绝不**
  枚举；`/status` 斜杠命令在 `conversations`/`max_conversations` 字段缺失（reader 下移除）时跳过
  sessions 行、不渲染 `undefined/undefined`。

落地小设计见 [`docs/P4.9-只读多会话.md`](docs/P4.9-只读多会话.md)。仍 `workers=1` + 仅 127.0.0.1
（决策P4-2/P4-4 红线不破）；对外暴露/TLS/限流交反代。由 `tests/test_web.py`（写路由裁剪/能力隔离/
idle 回收/`max_conversations` 校验/CLI 三态/KB 零写/`create_app` 直建钳制）与 `tests/test_web_i18n.py`
（键奇偶）守恒，全量 547 passed + ruff 通过。

## [0.1.4] - 2026-06-11

一次维护版：放宽 Python 下限以扩大可安装人群，并补全发布链路。无功能变更，
无新退出码，不动门禁，markdown 仍是唯一事实来源，`raw/` 仍对 Agent 只读。

### 变更

- **`requires-python` 降级 `>=3.12` → `>=3.10`** —— 此前 `>=3.12` 的下限纯属多余
  （源码及依赖均不依赖任何 3.11/3.12 专属语法或 API，`agentao` 本身 `Requires-Python`
  即 `>=3.10`），却导致 `>=3.10` 的消费项目解析 `guanlan-wiki` 时 unsatisfiable。
  同步：`pyproject` classifiers 补 3.10/3.11，CI 由单一 3.12 改为 3.10/3.11/3.12 矩阵
  （`sync`/`run` 显式 `--python`），`uv.lock` 随之降下限并纳入 3.10/3.11 传递依赖
  （如 `exceptiongroup`）。3.10 全量 516 passed + ruff 通过。

- **release workflow 自动建 GitHub Release** —— PyPI 发布成功后由 `release.yml` 的
  `github-release` job 从 CHANGELOG 抽对应版段作 notes，自动建与 tag/PyPI 对齐的
  GitHub Release（免手工补，附 Full Changelog 比较链接）。

## [0.1.3] - 2026-06-11

P4 Web 宿主之上的又一个写入口半相位（P4.8），补齐 P4 §10 推后项里最后一个 Web 写入口。
仍不引入新退出码，不动门禁、不加 SSE，markdown 仍是唯一事实来源，`raw/` 仍对 Agent 只读。

### 新增

- **Web 端问答回填 `query --backfill`（P4.8）** —— 把一次值得沉淀的好问答经写门禁回填成
  `wiki/syntheses/<slug>.md`，全程不离开浏览器。新增 `POST /api/backfill {question, model?}`：
  作为新写作业 kind 入既有单写者 FIFO `JobQueue`（与 `ingest`/`heal`/投喂同 worker 串行），
  作业内**整体复用** `run_query(question, backfill=True, …)`——它本就走 `run_guarded_write`
  （P2 子进程 + `raw/` 快照门禁 + 有界自愈，与 ingest 同一条写路径），答案与门禁回执经 stdout
  捕获进 `job.output`，前端轮询 `/api/jobs/{id}` 拿退出码徽标。`question` 经 `field_validator`
  strip 后判空 → 422；走子进程 runner（嵌入四坑不适用）；`Job.result` 恒 `null`（与 ingest 同形，
  `query.py`/`jobs.py` 一行不改）；`_reject_if_writable_active()` 兜可写 turn 活跃期的旁路 → 423。
  前端**两个入口**：顶栏「回填」按钮 + 浮层，及每条只读问答气泡尾部的「沉淀」小按钮
  （预填该轮问题、不搬只读答案原文——backfill 是另起一次 gated 写）。新增 zh/en 双语词条。
  由 `tests/test_web.py`（adapter 入队/退出码透传/空白 422/result=null/FIFO 串行/423/model 透传）
  与 `tests/test_web_i18n.py`（键奇偶）守恒。

## [0.1.2] - 2026-06-11

P4 Web 宿主之上的一个纯前端半相位（P4.7）加一处安装引导修复。仍不引入新退出码，
不动门禁，markdown 仍是唯一事实来源，`raw/` 仍对 Agent 只读。

### 新增

- **界面中英双语切换（P4.7）** —— 顶栏「中文 ⇄ English」开关，底层是 `static/` 里
  **纯前端、零后端、零 LLM** 的 i18n 层：`static/i18n.js` 的 `{zh,en}` 词表 + `t(key,…)`、
  声明式 `data-i18n*` 注解经 `applyI18n` 应用、`localStorage["guanlan.lang"]` 持久化（默认 zh）。
  只译界面 chrome —— wiki 内容、Agent 答案、`check/health/lint` 报告正文与 `/status` 类自省
  保持源语言（不给任何端点加 `lang` 参数）。切换只重渲染已开的动态面，**不刷新、不重取**。
  由 `tests/test_web_i18n.py` 守恒（zh/en 键奇偶、HTML/JS `t(...)` 键已定义、`t()` 首参字面量）。

### 修复

- `guanlan web` 缺 web extra 时的报错与 `--help` 文案误写为 `pip install 'guanlan[web]'`，
  但 PyPI 发布名是 `guanlan-wiki`（裸名 `guanlan` 已被无关项目占用），照此安装会装错包。
  统一改为 `guanlan-wiki[web]`。

## [0.1.1] - 2026-06-10

P3/P4 之上的一批**零-LLM 维护工具**与**可选 Web 宿主半相位**。全程不引入新退出码，
均在既有相位边界内（DESIGN §4.4 / §7）。markdown 仍是唯一事实来源，`raw/` 仍对 Agent 只读。

### 新增

- **`guanlan heal`（P3.2 / P3.3）** —— 缺失实体物化：把 `lint.missing_entity` 指出的待建页
  生成为规范标题页，与 `aliases` 收编联动（CLI + 非打印核心 `run_heal_result`）。
- **`guanlan reindex`（P3.4）** —— 零-LLM 索引回填：把磁盘上未登记的内容页注册进 `index.md`
  （`--dry-run` 预览 / `--prune` 清理悬挂行）。索引↔磁盘同步检测归口到 `pages.index_sync_state`。
- **Web 投喂 `POST /api/raw`（P4.1）** —— 粘贴文本即存为 `raw/` 源；经单写者 `JobQueue` 串行落盘。
- **会话落盘与恢复（P4.2）** —— 复用 agentao 的 session 快照语义，按 `session_id` 去重、十会话上限。
- **Web-heal（P4.3）** —— `GET /api/heal/preview` 只读工作列表 + `POST /api/heal` 写作业；
  概念分类 entities∪concepts、Web 勾选子集物化。
- **Web 斜杠命令与只读自省（P4.4）** —— `/status` `/context` `/skills` `/tools` `/mode`；停止按钮、
  SSE `start`·`stopped` 帧。
- **可写 Web 工作会话（P4.5）** —— `/mode workspace-write` + 三层写守卫 + 共享写锁单写者 + 撤销；
  Agent 可写 `workspace/`，`raw/` 仍硬只读。
- **Web 文件上传与晋级（P4.6）** —— `POST /api/upload` 暂存上传文件到 `workspace/uploads/`；
  既能当聊天附件（`<attachment>` 标签 / 图像走视觉通道，不成源），又能经「解析 → 人审 → 晋级为源」
  写入 `raw/`。新增 `workspace/` 浏览/预览/删除端点与前端「暂存区」弹层。
- **`pdf-to-markdown` 辅助 skill** —— 随包发布、由 skill 安装逻辑幂等装入全局供可写会话 Agent 发现，
  把上传的 PDF/DOCX/… 解析成 `workspace/parsed/` 暂存物（多格式自动解析管线本身仍属 P5）。

### 变更

- agentao `max_iterations` 默认 `100 → 200`。
- 版本号单一来源化：`pyproject.toml` 改用 `dynamic = ["version"]`，从 `guanlan/__init__.py:__version__`
  读取，消除 pyproject 与 `__init__` 漂移（此前曾 `0.1.1.dev0` vs `0.1.0` 不一致）。

### 修复

- `guanlan --version` 此前因 `__init__.py` 硬编码而显示 `0.1.0`，现与包版本一致。

## [0.1.0] - 2026-06-06

首个发布。P2 最小闭环（`init` / `ingest` / `query` / `check` / `install-skill`）+ P3 维护工具
（`health` / `lint` / `graph`）+ P3.1 别名解析 + P4 可选 Web 宿主（`guanlan web`）。

[0.1.6]: https://github.com/jin-bo/guanlan/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/jin-bo/guanlan/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/jin-bo/guanlan/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/jin-bo/guanlan/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/jin-bo/guanlan/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jin-bo/guanlan/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jin-bo/guanlan/releases/tag/v0.1.0
