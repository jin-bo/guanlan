# 观澜文档索引 / Docs Index

> 本目录是**面向开发的设计与规格**。想直接上手用观澜,先看 👉 **[用户指南 `guide/`](guide/)**(中英双语)。

## 入口

| | |
|---|---|
| 📖 [用户指南 `guide/`](guide/) | 安装、上手、各命令、Web/MCP 宿主(中英双语) |
| 🏗️ [`DESIGN.md`](DESIGN.md) | 权威设计文档(完整设计、里程碑表、E1/E2 展望) |
| 📋 [`../CHANGELOG.md`](../CHANGELOG.md) | 版本与里程碑进展 |
| 🗒️ [`backlog/notes/`](backlog/notes/) | 待办/调研笔记(尚未排期) |

## 相位规格(按里程碑)

每篇是一个相位(或半相位)的设计与实现规格,记录当时的决策与契约。命名 `P{里程碑}.{子相位}`。

### P2 —— 最小闭环

- [P2-最小闭环](P2-最小闭环.md) —— `init` / `ingest` / `query` / `check` / `install-skill`,确定性写门禁 + `raw/` 快照
- [P2.1-摄入写入纪律](P2.1-摄入写入纪律.md) —— 写门禁既有页只增不毁:源不回退 `sources.dropped`(阻断+自愈) + 正文骤缩 `body.shrank`(警告)

### P3 —— 健康与图谱(零-LLM 维护)

- [P3-健康与图谱](P3-健康与图谱.md) —— `health` / `lint` / `graph`,`pages.py` 共享原语
- [P3.1-别名解析](P3.1-别名解析.md) —— frontmatter `aliases` 进入 `[[wikilink]]` 解析
- [P3.2-缺失实体物化](P3.2-缺失实体物化.md) —— `heal`:高频断链按需 LLM 建页
- [P3.3-规范标题页与别名收编](P3.3-规范标题页与别名收编.md)
- [P3.4-索引回填](P3.4-索引回填.md) —— `reindex`:磁盘页登记进 `index.md`
- [P3.5-图谱分析](P3.5-图谱分析.md) —— 确定性 Louvain 社区 + 拓扑 lint
- [P3.6-图论桥与割点](P3.6-图论桥与割点.md) —— 确定性 Tarjan 割边/割点

### P4 —— 可选宿主层(Web + MCP)

- [P4-Web宿主](P4-Web宿主.md) —— `guanlan web`,读写分线、嵌入式只读聊天、HTTP/SSE 契约
- [P4.1-Web投喂](P4.1-Web投喂.md) —— `POST /api/raw` 粘贴存稿
- [P4.2-会话落盘](P4.2-会话落盘.md) —— 会话持久化/恢复
- [P4.3-Web-heal](P4.3-Web-heal.md) —— Web 端 heal 写作业
- [P4.4-Web斜杠命令](P4.4-Web斜杠命令.md) —— 斜杠命令 + 只读自省
- [P4.5-可写Web工作会话](P4.5-可写Web工作会话.md) —— `/mode workspace-write` + 三层写守卫
- [P4.6-Web上传与晋级](P4.6-Web上传与晋级.md) —— `POST /api/upload`,解析→人审→晋级
- [P4.6.1-暂存区确定性解析与图片晋级](P4.6.1-暂存区确定性解析与图片晋级.md)
- [P4.7-中英双语](P4.7-中英双语.md) —— 纯前端 i18n 界面切换
- [P4.8-Web回填](P4.8-Web回填.md) —— `query --backfill` 写作业
- [P4.9-只读多会话](P4.9-只读多会话.md) —— `guanlan web --reader` 只读多用户部署
- [P4.10-MCP宿主](P4.10-MCP宿主.md) —— `guanlan mcp` 只读 MCP 服务端(stdio)

### P5 —— 语料规模化(多格式 + 检索)

- [P5.0-检索层](P5.0-检索层.md) —— `guanlan search`:零-LLM BM25 + CJK-2-gram 全页召回
- [P5.1-Web检索接入](P5.1-Web检索接入.md) —— 检索内核接入长驻 Web 进程 + 共享 `CorpusCache`
- [P5.2-多格式摄入](P5.2-多格式摄入.md) —— `guanlan convert`:PDF/DOCX/… → `raw/<slug>.md`
- [P5.2.1-图片落盘](P5.2.1-图片落盘.md) —— 转换图片随源落 `raw/images/<slug>/`

## 其他

- [发布到-PyPI](发布到-PyPI.md) —— 发版操作记录
- [agentao-shell权限与permissions.json](agentao-shell权限与permissions.json.md) —— Agentao shell 权限参考

---

> 相位序号是阅读动线,不是严格依赖序;实际已实现到哪、各相位是否落地,以 [`../CLAUDE.md`](../CLAUDE.md) 顶部状态行与 [`../CHANGELOG.md`](../CHANGELOG.md) 为准。
