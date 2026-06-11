# 更新日志

本项目所有显著变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。版本号单一来源为 `guanlan/__init__.py`。

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

[0.1.3]: https://github.com/jin-bo/guanlan/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/jin-bo/guanlan/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jin-bo/guanlan/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jin-bo/guanlan/releases/tag/v0.1.0
