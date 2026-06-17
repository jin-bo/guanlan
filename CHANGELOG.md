# 更新日志

本项目所有显著变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。版本号单一来源为 `guanlan/__init__.py`。

## [Unreleased]

### 新增

- **长跑作业的「进展心跳」** —— `ingest`/`query` 经 Agentao 子进程跑 LLM 时**全程静默**(子进程
  `capture_output` 把 stdout/stderr 缓冲到结束),长任务看着像卡死。本版在三处补上「还活着」信号,
  **零契约变更、不动子进程协议与写门禁**:
  - **CLI**(`runtime.py`):子进程运行期起一条**守护线程**,每 15s 往 stderr 打一行
    `⏳ 仍在运行 Ns · wiki/ 已变动 N 个文件`。**仅交互式终端启用**(`sys.stderr.isatty()`)——
    管道/重定向/CI/`--json` 消费者一律静默,非交互行为逐字节不变;`ingest` 另加一行开场提示。
  - **Web chat**(`app.py` SSE):静默间隙(长工具调用 / 首 token 前思考)经 `asyncio.wait_for`
    超时补一帧 `heartbeat` 事件(带 `elapsed` 秒),前端 `chat.js` 渲染一行随秒数刷新的「处理中」,
    token 一来即清。token 正常流动时**永不触发**(每来一帧就重置等待)。
  - **Web 长跑作业**(`jobs.py`/`入库·物化·沉淀·审计`):作业 worker 起一条心跳线程,running 期把
    `⏳ 正在<verb>…仍在运行 Ns · wiki/ 已写 N 页` 刷进**瞬时** `job.progress`,`pollJob` 实时渲染;
    作业收尾即清空 → **不污染**完成后的干净回执。复用既有 `/api/jobs/{id}` 轮询通道,无 SSE。
  - 节拍单一真相源 `runtime.HEARTBEAT_INTERVAL_S`(=15s),web 两处各起本模块别名便于测试 monkeypatch;
    `wiki/` 变动计数归一到 `paths.count_files_modified_since`(CLI 与 Web 共用,免重复 `os.walk`)。
  - 守于 `tests/test_runtime.py`、`tests/test_web.py`(chat 静默补帧 / token 流动不补 / 入库·物化
    作业 running 期见 progress 且 done 后清空、不进最终 output)。

### 优化

- **`check` 全库 frontmatter 单次读盘** —— `run_check` 原先把整库读两遍：主循环逐页严格档读+解析一遍
  （`read_text` + `parse_frontmatter`，供 `frontmatter.unparsable` 报错），建链接解析表的
  `link_resolution_index` 内部又经 `alias_index` `iter_pages`+`load_page` 把整库再读+遍历一遍。改为
  先一遍读盘攒下记录，把**对已读文本走容错档解析**的 `(path, meta)` 透传给 `link_resolution_index(loaded=)`
  复用（即 0.1.11 已加的 keyword-only 入参），消去二次读盘与二次目录遍历（N 页：2N→N 次读，4→3 次
  rglob）。对每次 `ingest` 的 2–4 次写门禁 `run_check` 都生效。
  **关键正确性约束**：解析表复用的是**容错档**（libyaml `load_page_text`）而非主循环的严格档 meta——严格档
  （纯 Python `SafeLoader`）与容错档（libyaml `CSafeLoader`）对**「是否可解析」会分歧**（如 flow 序列里的
  字面 TAB：libyaml 收、纯 Python 抛），若解析表误用严格 meta，这类页的别名会从 `check` 的解析表掉出、而
  graph/heal/Web 仍用容错档得到它，当场破 `check.wikilink.broken ≡ graph.broken` 不变式（决策P3.8-2）。
  复用已读文本走 `load_page_text` 使 `loaded` 逐页等同 `load_page` → 解析表与 `link_resolution_index(wiki)`
  逐字节相同。容错档那次（廉价的 libyaml）解析仍保留以维持口径一致。**零 LLM、零契约变更、零新依赖**，
  `check` 报错文本/违规/退出码不变；守于 `tests/test_aliases.py`（分歧页端到端 broken≡check + loaded ≡
  默认解析表）与既有全套 `tests/test_check.py`。

## [0.1.11] - 2026-06-16

性能与工程整备版，**无新命令、无新退出码、无新依赖、无契约变更**：把长驻 Web/MCP 宿主的检索冷启动
开销移出关键路径并大幅压低，配合一轮过长模块拆分。① **检索冷启动提速**——P5.4 启动预热 +
singleflight 构建锁，把首搜的全库冷算移出用户关键路径；并把 `build_graph` 的整库 frontmatter 解析从
两遍降为一遍、容错档改用 libyaml 的 `CSafeLoader`。② **工程整备**——拆分过长的后端模块与前端
`app.js`，解阻 CI 的 ruff 报错。`graph.json` 输出字节不变、确定性不破，`raw/` 对 Agent 仍只读。

### 优化

- **检索冷启动性能（P5.4）** —— 长驻 Web/MCP 宿主起服时 `CorpusCache` 为空，**首次** `/api/search`
  （或 MCP `search`）要在用户关键路径上付两次全库冷扫（`build_corpus` + backlink 的 `build_graph`）。
  两个零-LLM、零契约变更修复：**启动预热**——`serve()`/`serve_mcp()` 起一个 `daemon` 线程跑
  `CorpusCache.prewarm(wiki)`（幂等、只读、零写、异常静默 → 优雅回落懒构建，绝不阻塞起服/不污染 MCP
  stdout），把冷算移进启动；**按桶 singleflight 构建锁**——冷/变更构建仅在 miss 时入锁并锁内复查缓存，
  使预热与并发首搜**合并为一次**构建而非各扫一遍（同时修了 P5.1 期并发首搜重复 `build_graph`/`build_doc`
  的 N×→1×）；全 memo 命中的热路不取构建锁、并发搜索不互阻。守于 `tests/test_search.py`。落地小设计见
  [`docs/P5.4-检索冷启动性能.md`](docs/P5.4-检索冷启动性能.md)。

- **`build_graph` 全库 frontmatter 单解析 + libyaml 容错档提速** —— `build_graph` 节点循环已 `load_page`
  全库，但 `link_resolution_index` 内部经 `alias_index` 又整库解析一遍（842 页样本库 → 1684 次
  `load_page`）。给 `alias_index`/`_base_resolution_index`/`link_resolution_index` 加可选 keyword-only
  `loaded=` 透传已加载的 `(path, meta)`、`build_graph` 复用之消去二次解析（缺省 `None` = 原行为，
  `check`/`heal`/`Web`/`MCP` 等既有调用方不受影响，与 `index_sync_state` 的 `pages` 透传同款）；并把
  **容错档**（`load_page` → graph/health/lint 热路）的 `_load_yaml_mapping` 改用 libyaml `CSafeLoader`
  （缺则优雅回落纯 Python `SafeLoader`，解析**值**等价、仅 C 加速）。**严格档 `check`** 显式锁定纯 Python
  `SafeLoader`，使其 `frontmatter.unparsable` 报错文本与宿主是否装 libyaml 无关、且与提速前 `safe_load`
  逐字一致。两者叠加：`build_graph` 约 0.36s → 0.087s（样本库 842 页），prewarm 冷算约 0.48s → 0.23s；
  `graph.json` 字节不变。守于 `tests/test_aliases.py`（`loaded` 路径 ≡ 默认路径）等。

- **拆分过长模块** —— 把超长的 `cli.py` / `web/app.py` / `web/chat.py` 与前端 `static/app.js`
  （2741 行）按关注点拆分（后端为内聚子模块、前端拆成 9 个经典脚本），降低单文件认知负荷、便于后续维护；
  行为与公共接口不变，全量测试逐字保持绿。

### 修复

- **解阻 CI 的 ruff 报错** —— 修正使 `uvx ruff check` 失败的 lint 问题，恢复 CI lint 闸通过。

### 文档

- P6 蒸馏草案重构为简明技术路线，并收窄到一个 worked example。
- 对齐 backlog 笔记与现有项目进展；补充 SCHEMA 更新的提示词指引。

## [0.1.10] - 2026-06-15

P3 确定性维护族扩张 + P4 Web 宿主续接，均落在既有里程碑边界内：① **维护族新命令**——`audit`（P3.7
语义审计：漂移源粗筛 + LLM 复核过期论断，复用 P2 门禁）与 `remove`（P3.9 源撤回：人发起把误摄/已撤稿
源移入 `.trash/`，零 LLM）；② **Web 宿主续接**——Web-audit（P4.12）把 audit 写作业接入浏览器，与
Web-heal 同构；③ **确定性增强**——链接归一（P3.8 wikilink 解析消变体）、页型↔目录体检（P3.10）、检索
backlink 重排（P5.3）、finding 因果排序、摄入写入纪律（P2.1 既有页只增不毁）；④ **信任边界**（P4.11
纯文档：内容即数据 / 防提示注入）。仍不引入新退出码、不动门禁、`raw/` 对 Agent 只读不破。

### 新增

- **语义审计（P3.7）** —— 新增 `guanlan audit`：与 heal 平级的两层命令——**确定性粗筛**漂移源（`raw/`
  被替换/重转、但引用它的 wiki 页未重综合；触发信号纯结构、零 LLM、低噪）→ **LLM 复核**过期论断（复用
  P2 写门禁 + P2.1 源不回退闸，`page_guard=True`）。新增 `guanlan/provenance.py`（`raw_digest` 拱心石
  归口：compute/format/parse digest、raw 路径准入、YAML-safe stamp + 写后 check + 回滚）与
  `guanlan/audit.py`（`audit_candidates` 粗筛 + 漂移源分组 + `run_audit_result` 编排 + `audit_result_dict`
  契约）；ingest 成功后由 wrapper 把本次 raw 指纹 stamp 进对应 source 页（决策P3.7-3a，只刷本次那一张）。
  `--dry-run`/`--limit`（限漂移源**组**数）/`--json`/`--model`；`check` 完全不动、不新增退出码、不碰
  `raw/`。守于 `tests/test_audit.py`（38）+ `tests/test_cli.py`。落地小设计见
  [`docs/P3.7-语义审计.md`](docs/P3.7-语义审计.md)。

- **源撤回（P3.9）** —— 新增 `guanlan remove <源>`：**人发起、零 LLM、确定性**的源撤回——把误摄/已撤稿
  源的自身落盘物（`raw/<slug>.md` + 图片 + `wiki/sources/<slug>.md` 摘要页）**移入回收区**
  `<库>/.trash/<slug>@ts/`（软删、非 `rm`，写 `manifest.json` 留档），多源衍生页里的 `<slug>` 引用
  确定性摘除（provenance 编辑、正文一字不动），独源孤儿/悬链只 advisory 不删。默认**预览**、显式 `--yes`
  才写（比 reindex/convert 更保守）；不起 Agentao、不经门禁快照（人发起的宿主确定性写，是 `raw/` 的合法
  删者，与 convert 的合法增者对称）。`--json`。复用 rawio / `gate._trusted_sources` / reindex 悬链清理 /
  pages 既有归口防漂移。守于 `tests/test_remove.py`（21）。落地小设计见
  [`docs/P3.9-源撤回.md`](docs/P3.9-源撤回.md)。

- **Web 语义审计（P4.12）** —— 把 `guanlan audit` 搬进浏览器，与 P4.3 Web-heal 同构：
  `GET /api/audit/preview`（零-LLM 预览漂移源组）+ `POST /api/audit`（入单写者 FIFO、轮询结构化回执）。
  `audit.py` 抽 `audit_preview`/`audit_preview_dict` 公开归口（CLI dry-run 与 Web 共预览口径，CLI 字节
  不变）；`app.py` 加 `AuditBody` + 两端点 + `/api/jobs/{id}` 的 `AuditRun` 序列化分支 + `on_job_done`
  升版集加 `"audit"`，`@_writer_only`（reader 下 404）+ `_reject_if_writable_active`(423) + 子进程 runner，
  **零 jobs.py 改动**（`Job.result` + worker 鸭子分流 P4.3 已就位）。前端顶栏「审计」按钮 + `#i-audit`
  图标（复用 `pollJob` + heal-* 样式）+ `overlay/btn/tip.audit` + 15 个 `audit.*` 双语键；reader 隐藏
  写按钮清单补 `audit-btn`（决策P4.9-9）。守于 `tests/test_web.py` + `tests/test_web_i18n.py`。落地小设计见
  [`docs/P4.12-Web语义审计.md`](docs/P4.12-Web语义审计.md)。

- **摄入写入纪律（P2.1）** —— 写门禁新增**既有页只增不毁**的确定性兜底，补「既有页被 ingest 静默
  腐蚀」这个洞（源回退损毁 provenance / 正文整段覆盖丢旧论断）。复用写门禁已有的 `raw/` 写前快照点，
  额外取一份**每页轻量指纹**（`sources` 集 + 正文长度）写后比对，**仅覆盖 ingest 与 `query --backfill`**
  （`run_guarded_write` 薄壳 `page_guard` 默认 True；核心默认 False，故 **heal 逐字节不变**）：
  - **`sources.dropped`（阻断 + 自愈）** —— 写前写后都在的既有页若丢失原有 `sources` slug（覆盖而非
    并集）→ 进 `check_failed` 既有有界自愈环、回喂 Agent 并回（2 轮未修 → `EXIT_CHECK_FAILED`）；与
    `sources.unresolved` 同级互补。坏/缺 frontmatter 的页记 None-跳过，frontmatter 错误单独留给 `check`
    不重复记账；空 `[]` 是可信回退（仍抓「洗光来源」）。
  - **`body.shrank`（警告非阻断）** —— 既有页正文从 ≥200 字腰斩到 < 0.5× → 与断链同通道报警告，
    不阻断、不自愈、不影响退出码（合法精简由人/Agent 自判，宁漏不误杀）。
  - **工具只验证不变量、合并仍归 Agent**：`gate.py` 只判「源没了/正文腰斩」纯结构事实，**不**把
    union/substitution 搬进工具（守第一铁律）；正向合并纪律写入 `SKILL.md`（ingest step 3「合并不
    覆盖」+ 收尾速查两条）与 `conventions.md`（`sources` 取并集）。
  - **零新增**：无新命令/参数/退出码/依赖/SSE；机器 JSON 契约不变（heal `--json`、独立
    `check`/`lint`/`health`、`raw/` 快照、退出码全逐字节不变；新 kind 仅现于人读门禁报告）。
    守于 `tests/test_gate.py`（源回退阻断+自愈 / 多 slug / 空 `[]` 真回退 / 新建·删页不误判 / 基线不误压 /
    骤缩警告+边界+正交 / 坏 frontmatter None-跳过去重 / `page_guard` 范围签名+行为双断言 / 失败路径 /
    raw 优先级）+ `tests/test_ingest.py`（端到端 re-ingest 守 sources 并集）。
    设计见 [`docs/P2.1-摄入写入纪律.md`](docs/P2.1-摄入写入纪律.md)。

### 优化

- **链接归一（P3.8）** —— 给 `[[wikilink]]` 解析加**确定性 fold 兜底**：`[[multi_head_attention]]`
  命中 `multi-head-attention.md`、`[[Café]]`(NFD) 命中 `café.md`——**全程零正文改写**。新增纯函数
  `fold_stem`（NFKC → casefold → `_`→`-`）；`link_resolution_index` 以 raw/alias 为基、只**加性叠加
  撞名安全的 fold 变体**（撞名不折叠、零串台），一个 `resolve_owner` 归口在 check/graph/heal/Web 四处
  复用，`graph.broken ≡ check.wikilink.broken` 不破。inline code-ref 明确不 fold（`_`/`-` 在代码标识符
  里意义不同）。无新命令/退出码/依赖（`unicodedata` 标准库）。守于 `tests/test_link_fold.py`。落地小设计见
  [`docs/P3.8-链接归一.md`](docs/P3.8-链接归一.md)。

- **页型↔目录一致性体检（P3.10）** —— `health` 加两条**建议非门禁** finding 抓 schema 漂移：
  `health.type_dir_mismatch`（frontmatter `type` 合法但放错目录，如 `entities/Foo.md type=source`）与
  `health.uncharted_page`（内容页落在四规范目录之外）。**不解析 `SCHEMA.md`**（保持自由文本、零耦合），
  只按硬编码 `pages.DIR_TO_TYPE`/`VALID_TYPES` 确定性比对；非法/缺 `type` 仍交 `check`、不重复报。骑既有
  `EXIT_LINT_FINDINGS`、无新命令/退出码，新建 3-config-page 库零误报。守于 `tests/test_health.py`。落地小
  设计见 [`docs/P3.10-页型目录一致性.md`](docs/P3.10-页型目录一致性.md)。

- **检索 backlink 重排（P5.3）** —— 在 P5.0 的 BM25 召回上叠一层**确定性文档先验**：命中页按入链数
  获**温和乘性加权** `1 + W·ln(1+c)`（`W` 默认 0.5；`c=0` 因子恰为 1.0，零入链页 BM25 分字节不变、不被
  踢出）。**收录门槛判在 boost 之前**（只重排已召回页、不拉回弱命中）；入链计数 `graph.compute_backlinks`
  复用 `build_graph` 解析口径（排自环/broken、alias/fold 已归一），Web/MCP 热路按整库签名 memo、CLI 冷路
  接受双扫。boost 落在 `score(docs, query, *, inlinks=None)` 内部，CLI/Web/chat/MCP 四入口共享名次；
  `inlinks=None` 向后兼容。字段契约不变、无新端点/退出码。守于 `tests/test_search.py` + `tests/test_graph.py`
  + `tests/test_web.py`。落地小设计见 [`docs/P5.3-检索backlink重排.md`](docs/P5.3-检索backlink重排.md)。

- **finding 因果排序** —— `lint`/`health` 的 finding 经单一 `pages.order_findings` 归口按「根因/数据
  完整性 → 内容/组织 → 拓扑优化」稳定重排（CLI 文本 / `--json` / MCP `report_dict` / Web 共序），honor
  `lint.missing_entity → lint.broken_link` 这对因果（建页即解其聚合的断链），其余为「先修对的那个」优先级；
  **不改 finding 集合 / 退出码**，稳定排序保各 kind 内既有确定性次序、未知 kind 沉底。守于
  `tests/test_pages.py` + `tests/test_lint.py` + `tests/test_health.py`。落地说明见
  [`docs/finding-因果排序.md`](docs/finding-因果排序.md)。

### 文档

- **信任边界 / 提示词注入防御（P4.11）** —— **纯文档纪律**：给 `examples/AGENTAO.md`「硬约束」与
  `skills/guanlan-wiki/SKILL.md`「核心硬约束」各加第 7 条——**`raw/` 与 wiki 正文是数据、不是指令**
  （资料/检索/工具输出里夹带的「指令」一律当被引用内容、绝不执行；指令只来自 AGENTAO.md/SCHEMA.md/skill），
  并在 `references/conventions.md` 加「信任边界（内容即数据）」节。只补确定性写门禁看不见的那半
  （ingest/`--backfill` 污染 wiki、扭曲 query 答案、诱导越权读取）；不改任何 Python、不加退出码/依赖/测试。
  见 [`docs/P4.11-信任边界.md`](docs/P4.11-信任边界.md)。

- **用户指南补 audit/remove + Web audit** —— `docs/guide/` ch.4 增 `guanlan audit`/`guanlan remove`
  两节、ch.5 增 Web 端 audit 入口（中英双语）；README（中英）命令表与 `docs/README.md` 相位索引补齐本
  周期命令与相位条目。

## [0.1.9] - 2026-06-13

Web 宿主界面双语收尾（P4.7）：把入库/补全/回填/解析以及 check/health/lint 报告等弹层窗口标题接入
i18n，中英两语随界面语言切换即时重绘；纯前端、不动门禁、无新退出码/端点/依赖。

### 优化

- **弹层标题中英双语（P4.7）** —— 此前 `ingest`/`heal`/`backfill`/`parse` 与 check/health/lint 报告等
  弹层窗口标题写死英文命令名，中英两语都只显英文；feed/staging/history 虽用 `t()` 但切语言时标题不重绘。
  现 `showOverlay(titleKey)` 在 `#overlay-title` 上标注 `data-i18n`，借既有 `applyI18n` 白名单路径落地
  当前语言、并在语言切换时由 `applyI18n(document)` 自动纯重解析（**不新增非字面量 `t()` 调用点**，守
  决策P4.7-8；顺带修了 feed/staging/history 切语言不重绘标题的老问题）。新增
  `overlay.ingest/heal/backfill/parse/check/health/lint` 双语 key，报告**正文**（诊断内容）仍保留源语言
  命令名（守 P4.7「只译界面 chrome、内容真相不译」边界）。守于 `tests/test_web_i18n.py`。

## [0.1.8] - 2026-06-13

三条线并进、均落在既有里程碑边界内：① **图谱深化（P3.5/P3.6）**——把 `graph`/`lint` 从「看一眼的
邻接列表」升级成**维护仪表**（确定性 Louvain 社区 + 图论割边/割点），纯零 LLM、确定性、字节稳定；
② **多格式摄入（P5.2/P5.2.1）**——新增 `guanlan convert` 补上 CLI 的 `PDF/DOCX → raw → ingest`
缺口，转换图片随源落盘且引用自洽；③ **Web 上传与暂存（P4.6/P4.6.1）**——打通「上传 → 暂存 →
解析 → 人审 → 晋级为源」端到端流，附 raw 源预览与暂存区 UX 优化。外加**中英双语用户指南**与
`docs/` 索引。仍不引入新退出码、不动门禁、`raw/` 对 Agent 只读不破。

### 新增

- **图谱分析（P3.5）** —— 新增 `guanlan/graphstats.py`（拓扑分析单一归口）：在 `graph.build_graph`
  已有邻接表上算**确定性手写 Louvain** 社区（固定 id 升序遍历 + 仅 ΔQ>0 移动 + 平局优先当前社区/
  其次最小成员 + 规范化重编号），手写纯 Python、零依赖、无 RNG，同图两次结果一致。
  - `graph`：`graph.json` **additive** 多 `stats.communities` 计数与每节点 `community` 社区号（既有
    键/顺序一字不动）；零-JS `graph.html` 加社区徽标 + 末尾确定性「拓扑提示」段（守决策P3-7）。
  - `lint`：在同一份 `g` 上加三类**建议非门禁**拓扑 finding —— `lint.hub_node`（过载枢纽，
    度 ≥ 均值+2σ 且 ≥ 5）、`lint.thin_intercommunity_link`（一对社区仅单条跨社区边互链）、
    `lint.isolated_community`（规模 ≥2 且与其余社区零跨边的孤岛）。
  - 算法边界收严：`undirected_adjacency` **显式过滤自环**（`build_graph` 只去重不删自环，自链页不虚增
    度数，决策P3.5-11）；`isolated_community` **前置守卫全库社区数 >1**，单社区小库永不误判孤岛
    （决策P3.5-12）；`thin_intercommunity_link` 命名取代 `fragile_bridge`，明确**非图论 bridge**
    （不判删边断连，决策P3.5-13）。LLM 推断边明确排除（破坏 graph 可重建性）。
  - 落地小设计见 [`docs/P3.5-图谱分析.md`](docs/P3.5-图谱分析.md)。

- **图论桥与割点（P3.6）** —— 在 P3.5 同一份 `undirected_adjacency` 上加 `graphstats.fragile_topology`：
  **一趟确定性迭代 Tarjan 低链 DFS**（显式栈、长链不爆递归）算出**割边（bridge）与割点（cut vertex）**——
  「删之即断」的单点故障，是 `thin_intercommunity_link`（只数单条跨社区边）看不见的**正交补强**
  （bridge 判真断连，二者不去重）。噪声控制：均以「删后次大连通分量 ≥ 阈值」过滤，近树叶边/星形辐条
  保持静默（星心是 `hub_node` 但非 `cut_vertex`）。**additive** 多两条建议非门禁 `lint` finding
  （`lint.bridge_edge` 全局 / `lint.cut_vertex` 逐页）与 `graph.json` 的 `stats.bridges`/
  `stats.cut_vertices` 计数（镜像 `stats.orphans`，节点/边字典字节不变）+ `graph.html` 两段拓扑提示。
  仍排除 LLM 推断边、零新命令/退出码。落地小设计见 [`docs/P3.6-图论桥与割点.md`](docs/P3.6-图论桥与割点.md)。

- **多格式摄入（P5.2）** —— 新增 `guanlan convert <file>`：把 PDF/DOCX/PPTX/XLSX/HTML/图片… 经
  **既有 `pdf-to-markdown` skill**（MinerU→marker→pypdf 分层兜底、随 wheel 全局安装）转成 markdown、
  落成 `raw/<slug>.md` 源（含 `origin` provenance），复用 `.md` 单格式 ingest 不动。补的是**命令行
  那个洞**——此前 CLI 用户没有官方的 `PDF/DOCX → raw/*.md → ingest` 路径（Web 那半 P4.6 已落）。
  - **脚本零 LLM、宿主写 `raw/`**：guanlan 自身不内嵌 LLM 客户端/密钥，只 shell out 到外部转换进程；
    该进程（如 marker）是否用 LLM 增强由**用户环境**决定，与 guanlan 正交——不做 env-scrub、不设
    `--model`、不向 skill 透传 model（决策P5.2-4）。**无新依赖、无新 extra**，graceful degrade 复用
    skill 既有分层（全后端耗尽 → `EXIT_USAGE` + 安装提示）。
  - **默认两步、不自动 ingest**：`convert` 只做转换 + 落源（确定性宿主写、非 gated、不起 Agentao、
    不取 raw 快照、不写 `log.md`）；建页仍由独立 `guanlan ingest` 完成。`--ingest` 便利串联（默认关）、
    `--dry-run` 预览（raw/ 零写）、`--overwrite` 显式覆盖、`--name`/`--origin` 显式控制、`--backend`
    透传 skill。子进程 `cwd=KB root` 保 skill `.env` 发现、temp 暂存防污染用户目录（决策P5.2-10/12）。
  - **抽出 `guanlan/rawio.py` 写归口（零行为变更）**：把 P4.6 的 slug/文本准入/provenance/原子写从
    `web/rawfeed.py` 抽成 transport-neutral 核心（校验函数 `raise ValueError`、`atomic_write_raw` 仍
    返回退出码），CLI `convert` 与 web 投喂/晋级共用一道闸、消漂移；`web/rawfeed.py` 保薄壳、HTTP
    409/500 分流逐字不变（决策P5.2-6）。无新退出码。
  - `ingest` 对非-`.md` 的报错文案改为指向 `guanlan convert`；落地小设计见
    [`docs/P5.2-多格式摄入.md`](docs/P5.2-多格式摄入.md)。

- **转换图片随源落盘（P5.2.1）** —— 补 P5.2 的漏：此前 `convert_to_markdown` 只取产物 `.md` 文本、
  把转换器抽出的图片连同 temp 树丢弃 → `raw/<slug>.md` 的 `![](…)` 全部悬空。现让图片**随转换文件
  一并落** `raw/images/<slug>/<slug>-N.ext`（按 md 内首次出现序编号、ext 取原后缀小写），并**重写
  markdown 图片引用**指向落盘新相对路径,使 `raw/<slug>.md` 自洽。**引擎无关收集**（mineru 子目录 /
  marker 平级统一按「相对产物 md 父目录解析」）；内核返回从 `str` 升为 `ConvertResult`。**图片引用准入
  是安全边界**（`_admit_image_ref` 五条 AND：拒 scheme/绝对/`~`/协议相对、`realpath` 解 symlink 后须
  落在 tmp_root 内、须真实普通文件、后缀白名单），挡越界/symlink 逃逸；**容量三道闸**（单图 20 MiB /
  累计 200 MiB / 张数 500，超限报错非静默丢图）；落盘「图先换、md 末步提交」+ 失败回滚,故任何成功落盘
  的新 md 永不指向缺失图片。`--dry-run` 连图零落盘、`--overwrite` 整盘替换该 slug 图目录。仍零 LLM
  宿主写、只写 `raw/`、`rawio.py` 一字不改。落地小设计见 [`docs/P5.2.1-图片落盘.md`](docs/P5.2.1-图片落盘.md)。

- **Web 文件上传与晋级（P4.6）** —— `POST /api/upload` 把文件落 `workspace/uploads/` 暂存，**一次落盘、
  双重用途**：①当聊天 `<attachment>`/图像视觉（Agent 当场读、不成源）②走「解析 → 人审 → 晋级为源 →
  ingest」成永久可追溯的 `raw/` 源；外加 `workspace/` 浏览/预览/删除端点与 **raw 源渲染预览**（点文件名
  看正文，含 `raw/images/` 嵌图与复杂 HTML 表格）。配套 **`pdf-to-markdown` skill**（force-include 进
  wheel、随包全局安装）。落地小设计见 [`docs/P4.6-Web上传与晋级.md`](docs/P4.6-Web上传与晋级.md)。

- **Web 暂存区确定性解析 + 图片晋级（P4.6.1）** —— 抽出 `guanlan/imageio.py` 图片归口（`collect_for_promotion`
  含 SHA256 指纹、`_admit_image_ref` 等从 `convert.py` 抽出，`rawio.py` 一字不改），落地暂存区
  「解析作业 → 断链检查 → 重整提交（含全局零引用图片 GC）→ 人审晋级」流：`parse_upload`/`image_lint`/
  `relocalize_commit`（`web/parsefeed.py`）+ `prepare_promotion`/`commit_promotion`（`web/promote.py`），
  并给 Job 加**流式进度**（`convert_to_markdown(progress=)` + `Emit` sink + 每 Job `output_lock`）。
  前端配 Web 暂存区/历史/日志 **UX 优化**（图标化 + 已收录过滤 + 进度条折叠）。落地小设计见
  [`docs/P4.6.1-暂存区确定性解析与图片晋级.md`](docs/P4.6.1-暂存区确定性解析与图片晋级.md)。

### 文档

- **中英双语用户指南 `docs/guide/`** —— 面向使用者的操作指南（`zh/` `en/` 各 7 篇 + 入口 README）：
  安装 / 快速上手 / CLI 命令 / 维护（体检·图谱）/ Web 宿主 / MCP 宿主 / 多格式转换；命令签名、flag、
  退出码核对自 `guanlan/cli.py` 与 `errors.py`。
- **`docs/README.md` 文档索引** —— 26 个 `P*.md` 按 P2/P3/P4/P5 里程碑分组、显眼指向 `guide/`（零链接破坏）。
- **README 改用户向 + 双语** —— 砍开发者向里程碑罗列、改「能做什么」命令表 + 状态 badge + logo + 中英切换，
  新增 `README.en.md` 镜像版。

由 `tests/test_convert.py`（定位 skill / 不带 LLM·不改 env / 无 --model·不透传 model / cwd=root
保 `.env` / temp 防污染 / 转换落源 / 全后端耗尽 degrade / 文本准入 / provenance·默认 origin 钉口径 /
覆盖 / dry-run / ingest 串联 / IO 失败映射 / 共用 rawio 归口 / 端到端）与 `tests/test_web.py`
（P4.6 投喂/晋级逐条仍绿的硬回归门）守恒。

由 `tests/test_graphstats.py`（确定性+字节稳定 / 两团单边→2 社区+thin-link / 有替代路径仍报 /
枢纽 σ 阈值+度地板 / 孤岛双簇+单社区守卫+孤儿排除 / 自环+断链排除 / 空图+单节点）与
`tests/test_graph.py`（additive `community`/`communities` 契约）守恒。

## [0.1.7] - 2026-06-12

把 **P4「可选宿主层」** 从「仅 Web」扩为「**Web + MCP** 两种传输」——新增第二种本地宿主入口
`guanlan mcp`：一个**只读 MCP 服务端**（stdio），把同一套只读核心暴露给任意 MCP 客户端
（Claude Code / Codex / Cursor）。与 Web 宿主**同构、同零写契约**，只换协议。仍不引入新退出码、
不动门禁、不碰数据模型、`raw/` 仍对 Agent 只读。

### 新增

- **MCP 宿主 `guanlan mcp`（P4.10）** —— 新增 `guanlan/mcp/` 子包（可选 `guanlan-wiki[mcp]` extra，
  缺失优雅降级引导安装，镜像 `web`）。用官方 `mcp` SDK 的 **FastMCP**（stdio 传输）暴露**七个只读工具**：
  零-LLM 的 `search` / `read_page` / `list_pages` / `graph` / `health` / `lint`（复用 P5.0/P5.1
  `search_result_dict` 检索归口、新抽 `pages.report_dict` 体检/lint 信封、`graph_to_dict`、`load_page`
  等既有只读核），外加唯一 LLM 工具 `ask`（复用 CLI query 只读子进程路径）。每个工具的返回类型
  `TypedDict` 注解驱动 FastMCP 自动生成 output schema → `structuredContent` + parsed-equal JSON 文本块；
  阻塞核逻辑经 `anyio.to_thread` 卸离事件循环；统一 `@_guard` in-band error 总壳（异常绝不杀 server /
  破 stdio 帧）。**只读、KB 零字节写入**（`require_kb_root(writable=False)`、不注册任何写工具）；
  `page`/`path` 统一为相对库根带 `wiki/` 前缀同口径，`search`→`read_page` 链路无拼接/剥前缀。
  **不碰 `chat.py`**——MCP 客户端自持对话，宿主无服务端会话状态（相对 Web 的最大减法）。作 E2
  「远程 / scoped MCP」的本地只读前哨；**方向区别于** DESIGN §1.22 的「Tool 注入」（那是 Agentao 作
  MCP 客户端，反向）。落地小设计见 [`docs/P4.10-MCP宿主.md`](docs/P4.10-MCP宿主.md)。

### 内部

- **`pages.report_dict` 归口** —— 从 `report_json` 抽出产-dict 核（`report_json = json.dumps(
  report_dict(...), ensure_ascii=False, indent=2)`），供 MCP `health`/`lint` 工具直接拿 dict、不绕
  `json.loads(format_report(...))` 字符串往返；CLI/Web 既有 JSON 字节契约**零变更**（无尾随换行）。
- **CI 覆盖 MCP** —— `ci.yml` 加 `--extra mcp`，使 `tests/test_mcp.py` 不再被 `importorskip` 跳过，
  在 Python 3.10–3.12 全跑（含 3.10 上 `typing_extensions.TypedDict` 的 pydantic 兼容性）。

仍 `workers=1` + 仅 127.0.0.1（Web 侧决策P4-2/P4-4 红线不破）；不属 E2（无 HTTP/远程/多租户/OAuth）。
由 `tests/test_mcp.py`（in-memory client/server 真实 JSON-RPC 往返）+ `tests/test_cli.py` 守恒。

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

[0.1.11]: https://github.com/jin-bo/guanlan/compare/v0.1.10...v0.1.11
[0.1.10]: https://github.com/jin-bo/guanlan/compare/v0.1.9...v0.1.10
[0.1.9]: https://github.com/jin-bo/guanlan/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/jin-bo/guanlan/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/jin-bo/guanlan/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/jin-bo/guanlan/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/jin-bo/guanlan/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/jin-bo/guanlan/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/jin-bo/guanlan/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/jin-bo/guanlan/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jin-bo/guanlan/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jin-bo/guanlan/releases/tag/v0.1.0
