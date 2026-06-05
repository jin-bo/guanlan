# P4 — Web 宿主（可选图形入口）实现设计

> 本文是 [`DESIGN.md`](DESIGN.md) §1 / §5.2 / §7-P4 / 附录 A 的**实现级细化**，是 P4 编码的落地依据。
> 高层理念与原则以 `DESIGN.md` 为准；本文只在 P4 范围内把"怎么做"钉死到模块、数据结构、契约与测试。
> P4 不引入任何新业务逻辑——它只是把 P2/P3 已交付的命令搬到浏览器里，故请先读 [`P2-最小闭环.md`](P2-最小闭环.md) 与 [`P3-健康与图谱.md`](P3-健康与图谱.md)。
> 文中标注的 **\[决策P4-N]** 是对 `DESIGN.md` 的具体化或受控偏离（含 §5.2/附录 A 把 P4 定为"进程内嵌入"的那条），集中记录在 §9。

---

## 1. 目标与范围

**P4 = 一个本地、单用户、单进程的 Web 宿主，复用同一文件库，把 CLI 的浏览 / ingest / query / check / health / lint / graph 搬进浏览器。**

它是 **MVP 之后的可选叠加层**：不装、不起 `guanlan web`，整套东西照旧用 CLI 跑通（DESIGN §1）。Web 宿主不改变"纯文件"的本质——markdown 仍是唯一事实来源，Web 只是 ingest/query 的**另一个入口**与 wiki 的**只读浏览器**。

纳入 P4：

- `guanlan web [--port N] [--no-browser] [--model M]` —— 起一个仅监听 `127.0.0.1` 的本地 Web 宿主。
- **浏览**：列页面 / 读单页（markdown 渲染 + `[[wikilink]]` 可点击导航）/ 看 `graph.html`。
- **唯一写入口（复用 P2 子进程）**：从 `raw/` 选一篇已存在的 `.md` 触发 `ingest`。经后台单 worker 串行执行、**轮询**拿结果（v1 不做写作业的 SSE）。
- **问答 / 多轮会话（进程内嵌入，read-only）**：与 agent **就同一知识库对话**——一次性提问，或追问、"展开第 2 点"、"刚发现的矛盾再说细些"，跨轮保留上下文。**一次性 query 即单轮会话**，与多轮**共用同一条只读嵌入路径**（不再单设子进程 query）。**默认只读**（read-only 姿态，不写 `wiki/`、不过门禁），答案经 `fetch` 流式（最小事件集 `token`/`done`/`error`）。见 §4.4。
- **零 LLM 审计（复用 P3/P2）**：在页面里跑 `check` / `health` / `lint`，按既有 JSON 契约展示。
- **raw 浏览**：列 `raw/*.md` 并选其一触发 ingest（**只列、不经 Web 写 raw**）。

**不**纳入 P4 v1（推后，见 §10）：**Web 端写 `raw/`**（粘贴存盘）、**`query --backfill`**（gated 写 synthesis）、**可写的多轮"工作会话"**（对话中逐轮 gated 写 `wiki/`）、**工具进度流式 / 写作业 SSE**、**会话落盘与跨重启恢复 / LRU 淘汰**、把 CLI 一次性操作迁到嵌入、自定义 Tool 注入、Web 端直接编辑 wiki、鉴权 / 多租户 / 远程暴露 / TLS、多格式上传摄入、交互式图形化 graph、ACP server 模式。

> **架构后果（重要）**：多轮会话**无法**用 `agentao run` 子进程实现——已核对 agentao 源码：`cli/run.py` 无 `--session/--resume/--continue`，每次 run 都新起一个内部 `_session_id`、互不连续。多轮**必须**进程内嵌入 `Agentao(...)`（`.chat()`/`.arun()` 让 `self.messages` 跨轮累积）。故 P4 按**读写分线**：**写**（仅 `ingest`）走子进程 + 单写者 worker（P2 不动、已验证、gated）；**所有读/问答**（一次性 + 多轮）走只读嵌入路径（无状态捕获、可并发、不被 ingest 阻塞）。这修订了决策P4-1（见 §9）。

---

## 2. 数据流总览

```
guanlan web                require_kb_root(root, writable=True)  # 需完整库（含写入口）
  └─ 起 uvicorn(FastAPI)，仅 bind 127.0.0.1:<port>、workers=1   # web extra；单进程单事件循环
     ├─ 单后台 worker 线程：FIFO 串行执行**唯一写作业** ingest      # 保单写者假设（决策P4-5）
     ├─ 会话表 dict[cid→Agentao]：所有问答，进程内嵌入、每会话一对象 + Lock # 只读、可并发（决策P4-8）
     └─ async 端点：静态资源 + 零 LLM 读/报告（经 to_thread 卸载，不堵事件循环）

浏览（读，async 端点经 to_thread 卸载）
  GET /api/pages           iter_pages(wiki) + load_page.meta            → 页面树 JSON
  GET /api/page?path=…     load_page → markdown 渲染 + [[wikilink]] 重写 → {meta, html}
  GET /graph               build_graph + 写 graph/ → 302 → /graph/graph.html（P3 静态文件）
  GET /api/raw             列 raw/*.md（只列，不经 Web 写 raw）

审计（零 LLM，async 端点经 to_thread 卸载）
  GET /api/report/check    run_check(wiki)   → 既有 JSON 契约（P2 §5.2）
  GET /api/report/health   run_health(wiki)  → 既有 JSON 契约（P3 §4.3）
  GET /api/report/lint     run_lint(wiki)    → 既有 JSON 契约（P3 §5.4）

写（仅 ingest；LLM 子进程，入 worker 队列 → 轮询，无 SSE）
  POST /api/ingest {target}    → enqueue(run_ingest)   → {job_id}
  GET  /api/jobs/<id>          → {state, exit_code, output}（轮询）

问答 / 多轮会话（LLM 嵌入，read-only，await arun → fetch 流式）
  POST /api/chat {conversation_id?, message}  → Conversation.turn
       └─ 响应 text/event-stream；前端 fetch 读 body；事件 token / done / error（决策P4-6）
          省略 conversation_id → 新建会话，id 随首个事件/done 回传（一次性=单轮）
  GET/DELETE /api/conversations[/<id>]        → 列/丢内存会话（只读、不过门禁、进程退出即清）
```

Web 宿主自身**不做任何确定性/语义判断**：所有结论都来自被复用的包内函数，宿主只负责"收 HTTP 请求 → 调既有函数 → 序列化结果"。这是 DESIGN"wrapper 不承载业务智能"在 Web 层的延续。

---

## 3. 模块落点（交付清单）

新增一个 `guanlan/web/` 子包；**不改 P2/P3 任何既有模块的行为**（仅可能加一处可选的结构化返回，见决策P4-7）：

| 文件 | 职责 | 备注 |
|------|------|------|
| `guanlan/web/__init__.py` | 子包入口；导出 `serve(root, *, port, open_browser, model, runner)` | |
| `guanlan/web/app.py` | FastAPI app + 路由（`async def` 端点）；零 LLM 报告经 `anyio.to_thread.run_sync` 卸载；pydantic 请求体模型；路径穿越校验；chat 的 `text/event-stream` 响应 | §4 / §5 **\[决策P4-2/5/6]** |
| `guanlan/web/jobs.py` | 单后台 worker 线程 + `queue.Queue` 作业表（与事件循环并存，FIFO 串行**唯一写作业 ingest**）；`enqueue`/`get_job`（轮询，无 SSE） | §4.2 **\[决策P4-5]** |
| `guanlan/web/chat.py` | **问答 / 多轮会话**：只读进程内嵌入 `Agentao`（`build_from_environment` + `arun`），一会话一对象 + `asyncio.Lock`，单文本回调→`token` 流；**仅内存、v1 不落盘** | §4.4 **\[决策P4-8]** |
| `guanlan/web/server.py` | `serve(...)`：编程式起 `uvicorn`（`host=127.0.0.1`、`workers=1`）、`--no-browser` 外按需 `webbrowser.open` | §7 **\[决策P4-2]** |
| `guanlan/web/render.py` | 单页渲染：`load_page` → markdown→html（有 `markdown` 则用，无则回退 `<pre>`）+ `[[wikilink]]` 经 `pages.link_stem` 重写为站内锚链 | §6 |
| `guanlan/web/static/index.html` | 单页前端（vanilla JS + fetch/EventSource，无 npm/无构建/无 CDN）；侧栏页面树 + 主区单页 + 顶栏动作（ingest/query/check/health/lint/graph） | §6 **\[决策P4-3]** |
| `guanlan/web/static/app.js` `app.css` | 前端逻辑与样式（随包静态资源） | §6 |
| `guanlan/cli.py` | 新增 `web` 子命令（沿用 `-C/--dir`；`--port`/`--no-browser`/`--model`） | §7 |
| `pyproject.toml` | 新增可选 extra `[project.optional-dependencies] web = ["fastapi>=0.110", "uvicorn>=0.29", "markdown>=3"]`；静态资源 `force-include` 进 wheel | **\[决策P4-2]** |
| `tests/test_web.py` | 见 §11 | fake runner 注入 + `fastapi.testclient.TestClient`（进程内、无 socket）；**不打真实 LLM** |

> 仍**不**新增 skill `scripts/`，**不**改 `skills/guanlan-wiki/`：Web 宿主不改变 agent 工作流，ingest/query 仍走同一 skill 与同一门禁。
> 复用面（只 import、不复制逻辑）：`ingest.run_ingest` / `query.run_query` / `check.run_check` / `health.run_health` / `lint.run_lint` / `graph.build_graph` · `graph.graph_entrypoint` / `pages.{iter_pages, load_page, link_stem, WIKILINK_RE}` / `paths.require_kb_root` / `errors.*`。
> **ASGI ≠ 重写业务**：FastAPI/uvicorn 只是表现层；一次性路径的结论仍来自上述同步函数。端点是 `async def`，但**阻塞调用一律卸到线程**（零 LLM 报告走 `anyio.to_thread.run_sync`；一次性 LLM 作业走单 worker 线程），绝不在事件循环里直接跑阻塞代码（决策P4-2）。
> **多轮会话另用嵌入面**：`agentao.embedding.build_from_environment` + `Agentao.arun` + 回调 + `embedding.sessions.{persist_agent_session, load_session}`——这是 agentao 文档化的嵌入契约，非内部 API（决策P4-8）。`agentao` 已是核心依赖（`agentao[cli]`），嵌入**不引新依赖**。

---

## 4. Web 宿主与 Agentao 集成（`guanlan/web/{app,jobs,chat,server}.py`）

### 4.1 读写分线：写走子进程，所有问答走只读嵌入　**\[决策P4-1（修订）]**

P4 按"是否写库"分两条路，而非按"是否多轮"：

| | **写**（仅 `ingest`） | **所有问答**（一次性 + 多轮） |
|--|--|--|
| 集成方式 | `agentao run` 子进程（`run_ingest`） | 进程内嵌入 `Agentao`，每轮 `await agent.arun(msg)` |
| 状态 | 无状态、一次性 | `self.messages` 跨轮累积（一会话一对象；一次性=单轮） |
| 写 wiki | 经 `raw/` 快照 + check 门禁 + 自愈 | **read-only、不写、不取快照、不跑 check** |
| 并发 | 单 worker FIFO 串行（单写者） | 每会话 `asyncio.Lock`；会话间可并发，**不**被 ingest 阻塞 |
| 为何这样 | P2 已验证、零新业务代码 | 多轮子进程**做不到**（CLI 无 resume）；一次性走同路免去 stdout 捕获竞态 |

**写路径不变**：Web 唯一写入口 `POST /api/ingest` 直接调 `run_ingest`——已封装好 `agentao run` 子进程 + `raw/` 快照门禁 + 有界自愈，Web 层一行 LLM 集成代码都不写。`query --backfill`（gated 写 synthesis）推后到 v1 之后（§10），CLI 仍有。

**读路径只此一条嵌入**：原决策P4-1 以"避免 P2 §4.3 嵌入四坑"整体否掉嵌入；但多轮在子进程上**根本无法实现**（已核对 `cli/run.py`），既已为多轮受控嵌入（逐一化解四坑，§4.4），**一次性 query 就是单轮会话**，没有理由再单设一条子进程 query——那条还会带回 `redirect_stdout` 的进程级捕获竞态。故**把所有读/问答收口到只读嵌入**：返回值即字符串、无 stdout 捕获、天然可并发、不被慢 ingest 卡住。这比"读也串行进 worker"更省、也更对（见 §9 决策P4-1/P4-5/P4-8）。**不**把写也迁到嵌入（写的 P2 子进程路径已验证、且 gated，无必要动）。

### 4.2 单后台 worker，FIFO 串行执行 LLM 作业　**\[决策P4-5]**

`ingest` / `query --backfill` 会改 `wiki/`，且 P2 门禁的 `raw/` 前后快照**假设单进程单写者**（P2 §11 明确把并发锁推后）。故所有 LLM 作业（含只读 `query`）都进**一个** `queue.Queue`，由**单条** worker 线程（与 uvicorn 事件循环并存）FIFO 取出执行：

```python
# jobs.py（骨架）—— 与事件循环无关的纯线程 + 队列
@dataclass
class Job:
    id: str; kind: str; state: str          # queued | running | done
    exit_code: int | None = None
    output: str = ""                          # 捕获的人读输出（见决策P4-7）

def _worker():
    while True:
        job, fn = _q.get()
        job.state = "running"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            job.exit_code = fn()               # fn = lambda: run_ingest(...) / run_query(...)
        job.output = buf.getvalue()
        job.state = "done"

# app.py：唯一写作业 = ingest。async 端点即时入队、立刻返回 job_id
@app.post("/api/ingest")
async def ingest(req: IngestBody) -> dict:
    return {"job_id": enqueue("ingest", lambda: run_ingest(req.target, root=ROOT, model=req.model or MODEL))}
```

- **必须 `uvicorn workers=1`（决策P4-2）**：内存作业表 + 会话表 + 单写者假设要求**单进程单事件循环**；多 worker = 多进程 = 状态分裂 + `raw/` 快照被并发写互踩。
- **串行只为写**：worker 队列里 v1 只有 `ingest` 一种作业。串行保两件事：① 至多一个写作业，`raw/` 快照不被并发写互踩；② `run_ingest` 用模块级 `print` 输出人读文本，串行 + `redirect_stdout` 捕获**无跨线程竞态**。**红线：`redirect_stdout` 捕获只许单 worker 做。**
- **读不进 worker**：零 LLM 报告经 `anyio.to_thread.run_sync` 卸载（返回 dataclass、无 stdout 捕获）；所有问答走嵌入路径（§4.4，返回字符串、无 stdout 捕获）。二者都可并发、都不被慢 ingest 阻塞——这正是把读收口到嵌入、不再单设子进程 query 的理由（决策P4-1/P4-5）。
- 作业表只在内存（单进程、单用户，进程退出即清）；`job_id` 用自增计数器 + 进程内字典，不引外部存储。

### 4.3 复用层级：零 LLM 走 report 函数，写走 run_ingest 捕获，读走嵌入　**\[决策P4-7]**

- **零 LLM** → 调 **report 级函数**（`run_check`/`run_health`/`run_lint` 返回 `*Report` dataclass、`build_graph` 返回 `Graph`），直接序列化为 P2/P3 既有 JSON 契约。干净、结构化、与 CLI `--json` 字节对齐。
- **写（ingest）** → 调 `run_ingest`，**捕获其 stdout+退出码**作为作业结果。输出本就是散文（"触及了哪些页面" + 门禁报告），捕获文本足够前端展示，**不必把 P2 写路径 fork 出结构化返回**（可选未来重构出 `WriteOutcome`，非 v1 必需）。
- **读 / 问答** → 走 §4.4 嵌入路径，`arun()` 直接返回答案字符串：**无 stdout 捕获、无退出码语义**，故可并发、不与写作业争 stdout。这是把读从"子进程 + 捕获"换成"嵌入 + 返回值"的关键收益。

### 4.4 问答 / 多轮会话 = 只读进程内嵌入（`guanlan/web/chat.py`）　**\[决策P4-8]**

**为什么必须嵌入**：已核对 agentao 源码——`agentao run` 无 session resume，多轮**只能**靠在内存里持有一个 `Agentao` 对象、反复 `chat()` 让 `self.messages` 累积。一次性提问就是"新建会话只跑一轮"。用 agentao **文档化的嵌入面**，不碰内部 API：

```python
# chat.py（骨架）—— 一会话一 agent 对象，仅存活于 server 内存（v1 不落盘）
from agentao.embedding import build_from_environment

class Conversation:
    def __init__(self, kb: Path, model: str | None):
        self.lock = asyncio.Lock()                       # 同一会话不并发两轮
        self.agent = build_from_environment(             # ← 化解四坑之①③：工厂读 .env/~/.agentao
            working_directory=kb,                        #    取 LLM 凭据，且可传我们自己的 logger
            model=model, logger=_quiet_logger(),         #    （不写 <wd>/agentao.log，不污染知识库）
            permission_mode="read-only",                 # ← 默认只读：纵深防御，对话不写 wiki
        )
        _activate_skill(self.agent, "guanlan-wiki")      # ← 化解坑②：嵌入式同样需显式激活 skill

    async def turn(self, msg: str, emit) -> str:
        async with self.lock:
            # v1 最小事件集：只把生成的文本作为 token 推给前端（emit → asyncio.Queue → 流式 body）
            self.agent.llm_text_callback = lambda t: emit("token", t)
            return await self.agent.arun(msg)            # ← arun = chat 的 async 包装，ASGI 原生 await
```

**关键设计点**：

- **`arun()` 与 ASGI 天作之合**：`Agentao.arun` 是 `chat` 的 async 包装（内部 `run_in_executor`），FastAPI 端点直接 `await`，无需我们自建线程桥——正好兑现决策P4-2 选 ASGI 的赌注。
- **流式 = 最小事件集 `token` / `done` / `error`**：只挂**一个**文本回调把生成 token 推出去，末尾发 `done`（含完整答案 + `conversation_id`），异常发 `error`。**工具/step 进度事件 v1 不做**（嵌入虽暴露 `tool_complete_callback`/`step_callback`/`cancellation_token`，但纳入会让契约/测试变重，推后，§10）。
- **传输用 `fetch` 流式，不用 `EventSource`**：浏览器 `EventSource` 只能 `GET`、不能带 POST 体；而每轮要 POST `message`。故 `POST /api/chat` 直接返回 `text/event-stream`，前端用 `fetch()` 读 `response.body`（`ReadableStream`）解析事件——见决策P4-6。（备选"POST 建 turn + GET /events 订阅"被否：多一个端点，且 POST 与订阅之间有**事件丢失/竞态窗口**。）
- **v1 仅内存、不落盘**：会话对象活在进程内，进程退出即清。`persist_agent_session`/`load_session`（落盘 `.agentao/sessions/`）、跨重启恢复、LRU 淘汰**全部推后**（§10）——进程存活期内上下文可用即达标。需要给内存设界时，用一个保守的会话数硬上限（超出拒新建）即可，不上 LRU。
- **P2 §4.3 嵌入四坑逐一化解**：① 构造凭据缺失 → 用 `build_from_environment()`（读 `.env`/`~/.agentao`），不裸构造；② skill 不自动激活 → 构造后显式激活 `guanlan-wiki`；③ `<wd>/agentao.log` 污染 → 传我们自己的 `logger=`，不落库内；④ 内部 API 耦合 → 只用 `build_from_environment`/`.arun`/回调/`load_session`/`persist_agent_session` 这一组**嵌入契约**面。
- **只读姿态 + 不过门禁**：会话以 `read-only` 构造，纵深防御保证对话不写 `wiki/`/`raw/`；既然不写，就**不取 `raw/` 快照、不跑 check**——与 P2 plain `query` 同理。可写的多轮"工作会话"（逐轮 gated）**推后**（§10）。
- **并发**：每会话一把 `asyncio.Lock`（同一会话的两轮不并发跑同一 agent 对象）；不同**只读**会话间可并发（都不写、无 `raw/` 竞态），**不**走 §4.2 的写 worker。写 worker 仍只管一次性 ingest/`--backfill`，单写者假设不被会话破坏。
- **生命周期**：会话表 `dict[conversation_id, Conversation]` 在内存，进程退出即清；每轮 `persist_agent_session` 落盘，故历史不丢、重启可经 `load_session` 重建（重建为可选增强，非 P4 必需）。空闲会话可按数量上限 LRU 淘汰（单用户量小，先设个保守上限即可）。

---

## 5. HTTP API 契约（`guanlan/web/app.py`）

POST 请求体用 **pydantic 模型**（`IngestBody`/`ChatBody`），FastAPI 自动校验/422；响应 `Content-Type` 由返回类型决定。错误经 `HTTPException` 抛（400 用法 / 404 不存在 / 409 越界 / 500 内部）。**读路径参数都过路径穿越校验**：解析后必须 `resolve()` 落在 `<root>/wiki/` 内，否则 409。

| 方法 路径 | 入参 | 复用 | 返回 |
|------|------|------|------|
| `GET /` | — | 静态 | `index.html` |
| `GET /static/*` | — | 静态 | 随包资源（js/css） |
| `GET /api/pages` | — | `iter_pages`+`load_page` | `{pages:[{path,title,type}]}`（按 type 分组，排除 config） |
| `GET /api/page` | `path` | `render.render_page` | `{meta, html}`；坏/缺 frontmatter 时 `meta=null` 仍渲染正文（容错档，同 P3 决策P3-8） |
| `GET /api/report/check` | — | `run_check` | P2 §5.2 JSON（`{ok,pages_checked,violations}`） |
| `GET /api/report/health` | `strict?` | `run_health` | P3 §4.3 JSON |
| `GET /api/report/lint` | — | `run_lint` | P3 §5.4 JSON |
| `GET /graph` | `json_only?` | `graph_entrypoint` | 构建后 302 → `/graph/graph.html` |
| `GET /graph/graph.html` | — | 静态（派生） | P3 自包含 `graph.html` |
| `GET /api/raw` | — | 列目录（**只读**） | `{files:[{name,size}]}` |
| `POST /api/ingest` | `{target,model?}` | 入队 `run_ingest` | `{job_id}`（**唯一写入口**） |
| `GET /api/jobs/{id}` | — | 作业表 | `{id,kind,state,exit_code,output}`（**轮询**，404 未知 id） |
| `POST /api/chat` | `{conversation_id?,message,model?}` | `chat.Conversation.turn`（嵌入，read-only） | **`text/event-stream`**；前端 `fetch` 读 body；事件 `token`* → `done`{answer,conversation_id} / `error`{message}；省略 `conversation_id` → 新建会话 |
| `GET /api/conversations` | — | 会话表 | `{conversations:[{id,title,turns}]}`（内存现存会话） |
| `DELETE /api/conversations/{id}` | — | 会话表 | 丢弃内存会话对象 |

- `target` 仍经 `run_ingest` 内部 `_resolve_raw_target`（必须在 `raw/` 下、是 `.md`、存在）兜底——Web 不旁路 P2 入口校验。
- **ingest 是 v1 唯一写**：经单 worker 串行 + P2 完整门禁（`raw/` 快照 + check + 自愈）。退出码透传进 `job.exit_code`（0/3/4/5），前端据此显示通过/失败徽标。
- **chat 全程只读**：`read-only` 嵌入构造（决策P4-8），不取 `raw/` 快照、不跑 check；流式契约见 §4.4。`POST /api/raw`（写 raw）与 `query --backfill`（gated 写）**v1 不提供**（§10）。

---

## 6. 前端（`guanlan/web/static/`，自包含、无构建）　**\[决策P4-3]**

与 P3 `graph.html` 同精神：**单页静态资源，vanilla JS + `fetch`，无 npm / 无打包 / 无 CDN / 无第三方运行时**（FastAPI/uvicorn 是**服务端**依赖，前端不引任何 JS 框架；流式也只用 `fetch` 读 `response.body`，**不用 `EventSource`**——它不能 POST，见决策P4-6）。三栏极简布局：

- **侧栏**：`/api/pages` 拉来的页面树（按 type 分组）+ `/api/raw` 的 raw 文件列表；点击载入单页。
- **主区**：`/api/page` 渲染的单页 HTML；正文里的 `[[wikilink]]` 由 `render.py` 重写为站内锚链（点了切到目标页，断链标灰、不可点）。
- **对话区**：输入框 → `POST /api/chat`，`fetch` 流式读 `token` 逐字上屏、`done` 收尾；保留 `conversation_id` 即多轮、清空即新开（一次性问答 = 不续 id）。
- **顶栏动作**：`ingest`（从 `raw/` 列表选一篇 → `POST /api/ingest` → **轮询** `/api/jobs/{id}` 取结果）/ `check`·`health`·`lint`（拉 JSON 报告渲染成清单）/ `graph`（新开 `/graph`）。

**markdown 渲染（`render.py`）**：`load_page` 取正文 → 若安装了 `markdown`（`guanlan[web]` extra）则渲染为 HTML，否则回退到转义后的 `<pre>` 源码视图（**缺 extra 也能跑、只是不美观**）。`[[wikilink]]` 重写复用 `pages.WIKILINK_RE` + `pages.link_stem` + 全页面 stem 解析集——与 `check`/`graph` **同一口径**，不另写解析。

> 渲染是唯一可能引入依赖的点，故收敛为**可选 extra**：核心 `guanlan` 安装不因 Web 而背上 markdown 依赖；装了 `guanlan[web]` 才有富渲染。这呼应"纯文件、薄 wrapper"——Web 是叠加层，其依赖不下沉到核心。

---

## 7. CLI 契约与退出码（`guanlan/cli.py` / `errors.py`）

```
guanlan web [--port N] [--no-browser] [--model M]
```

- 前置 `require_kb_root(root, writable=True)`（Web 含写入口，要求 `raw/`/`wiki/`/`AGENTAO.md`/`SCHEMA.md` 齐全），失败 → `EXIT_USAGE(1)` 并提示 `guanlan init`。
- `serve(...)` 编程式起 uvicorn：`uvicorn.run(app, host="127.0.0.1", port=port, workers=1, log_level="warning")`。**强制 `workers=1`**（决策P4-2/P4-5：内存作业表 + 单写者）；端口被占（`OSError`）→ `EXIT_USAGE(1)`，提示换端口。
- 缺 `web` extra（import `fastapi`/`uvicorn` 失败）→ `EXIT_USAGE(1)`，提示 `pip install 'guanlan[web]'`。
- 默认起服务后用 `webbrowser.open` 打开 `http://127.0.0.1:<port>/`（用 uvicorn 启动后回调 / 短延时线程，避免在服务 ready 前打开）；`--no-browser` 跳过。
- `--model` 透传给 `run_ingest`（写作业）与会话 `build_from_environment`（覆盖 Agentao 模型）；亦可 per-request 用 `model` 字段覆盖。
- **P4 不新增退出码**（区别于 P3 加了 6）：`web` 长驻，Ctrl-C 正常停服 → `EXIT_OK(0)`；前置/端口/缺 extra 错误 → `EXIT_USAGE(1)`。作业自身的退出码只进 `job.exit_code`，不影响 `web` 进程退出码。

---

## 8. 安全姿态　**\[决策P4-4]**

P4 是**个人版、本地、单用户**工具，安全模型与之匹配、不过度：

- **仅 bind `127.0.0.1`**，绝不 `0.0.0.0`。**无鉴权、无 TLS、无多租户**——它跑在用户自己的机器、以用户自己的文件权限读写自己的库。
- 写入口（`ingest`/`query --backfill`）触发 `workspace-write` 的 agent 作业与文件写入；**严禁把该端口暴露到网络或反代到公网**——文档明确警示。需要远程/多用户是企业版（DESIGN §6 / E2）的事，不在 P4。
- **多轮会话以 `read-only` 嵌入构造**（决策P4-8）：纵深防御保证对话不写 `wiki/`/`raw/`，即使 prompt 诱导 agent 写也被权限姿态挡下；会话不旁路门禁，因为它压根不写。
- `raw/` 不变性仍由 P2 快照门禁兜底（写作业内）；Web 不放宽、不旁路任何 P2/P3 门禁。
- 路径穿越：所有 `path`/`name` 参数 `resolve()` 后必须落在 `wiki/`（读）或 `raw/`（写）内，否则 409，防止经 HTTP 读到库外文件。

---

## 9. 对 DESIGN.md 的偏离与决策记录

- **\[决策P4-1（修订）] P4 双 LLM 路径：一次性 ingest/query 仍走子进程，多轮会话走进程内嵌入。** 初版 P4-1 整体否掉嵌入，理由是"图形入口的价值用 P2 子进程复用即可达成、且避免 P2 §4.3 的嵌入四坑"——这对**一次性**操作成立，至今不变。但新增的**多轮会话**需求把这条决策劈成两半：已核对 agentao 源码（`cli/run.py` 无 `--session/--resume/--continue`、每 run 新起 `_session_id`），**多轮在子进程上根本无法实现**；而 `Agentao.chat()`/`.arun()` 让 `self.messages` 跨轮累积，正是为多轮而设。故**仅为会话**引入受控嵌入（§4.4 决策P4-8），逐一化解四坑，并**不**把一次性 ingest/query 一起迁过去（无谓扩面）。**附带收益**：嵌入的回调面让会话路径拿到真正的 token/工具**流式**（决策P4-6 之外的升级）；初版 P4-1 留的"升级路径"（先验子进程流式格式）对一次性路径依然适用，对会话路径则由嵌入直接兑现。Tool 注入仍**不做**——skill 早已让 agent shell 调 `guanlan check`，会话 agent 同样带 `guanlan-wiki` skill，无需把脚本包成工具。
- **\[决策P4-2] 服务端用 FastAPI + uvicorn（ASGI），收敛进 `guanlan[web]` 可选 extra；强制 `workers=1`，阻塞调用一律卸到线程。** 取舍：曾在 ① stdlib `http.server`（零依赖、floor）② Flask（同步 WSGI、契合阻塞负载）③ FastAPI/uvicorn（ASGI）三者间权衡——本工作负载是**同步阻塞**（`agentao run` 子进程 + 文件扫描），async 本身不带来并发收益，故 stdlib/Flask 更"对路"。**最终选 ASGI 是一个向前赌**：押注近期要**流式**进度、并为可能的多用户演进留口子；代价是 async 与阻塞负载不匹配，须以工程约束补偿——(a) **`workers=1`**（内存作业表 + 单写者，多进程会破坏二者）；(b) 阻塞调用绝不进事件循环（零 LLM 报告 `anyio.to_thread.run_sync`、LLM 作业进单 worker 线程，§4.2）；(c) `redirect_stdout` 捕获只许单 worker 串行做。依赖（fastapi/uvicorn/markdown）全部收在**可选 extra**，核心 `guanlan` 安装面不变、不装 `web` 不背这些；缺 extra 时 `guanlan web` 明确报错引导安装。**这条偏离了"最简明"的 stdlib/Flask**——记此以便：若流式/多用户的预期落空，应回落到 Flask（WSGI）或 stdlib，去掉 ASGI 的架构税。markdown 渲染仍缺则回退 `<pre>`。
- **\[决策P4-3] 前端是单页静态资源（vanilla JS + fetch，无 npm/构建/CDN/第三方运行时），与 P3 `graph.html` 同精神。** 不引前端工程化：一个 `index.html` + `app.js` + `app.css` 随 wheel 携带，打开即用。避免把"给文件库加个入口"退化成一个前端项目（与 P3 决策P3-7 否决"自写图布局"同一克制）。
- **\[决策P4-4] 仅监听 `127.0.0.1`、单用户、无鉴权/TLS/多租户；写端口严禁暴露网络。** 见 §8。鉴权/隔离/远程是企业版 E2，不预先塞进个人版。
- **\[决策P4-5] 所有 LLM 作业经单后台 worker FIFO 串行；零 LLM 读/报告由 async 端点经 `to_thread` 卸载、可并发。** 保住 P2 门禁的单写者假设（`raw/` 前后快照不容并发写），并使 `redirect_stdout` 捕获 `run_*` 人读输出**只在单 worker 串行发生**、无跨线程竞态（报告类不捕获 stdout，故并发卸载安全）。P2 §11 已把并发锁推后，这里用"单 worker 串行 + `workers=1`"作最省的等价物，不引文件锁。
- **\[决策P4-6] SSE 分两档：一次性作业只流生命周期，多轮会话流 token/工具。** ① 一次性 ingest/query（子进程）→ `GET /api/jobs/{id}/events` 只推 queued→running→done（飞行中无流可推，前端可退化轮询）；② 多轮会话（嵌入）→ `POST /api/chat` 经 agent 回调把 token/工具事件**逐条流式**推送（决策P4-8）。**差别根因在集成方式**：子进程 `--format json` 一锤子返回，嵌入回调天然增量。WebSocket 不引（SSE 单向足够）。
- **\[决策P4-7] 复用既有函数、不 fork 业务逻辑：零 LLM 走 report 函数序列化既有 JSON 契约，ingest/query 捕获 `run_*` 的 stdout+退出码。** 见 §4.3。可选未来把写路径重构出结构化 `WriteOutcome`，但非 P4 必需——P4 先零改动复用。
- **\[决策P4-8] 多轮会话用进程内嵌入，仅此一项、且默认只读、不过门禁。** 多轮无法走子进程（决策P4-1 修订已述），故为它嵌入 `Agentao`：① 用 `build_from_environment(working_directory=kb, logger=…)` 而非裸构造（读环境凭据、避免 `<wd>/agentao.log` 污染）；② 构造后显式激活 `guanlan-wiki` skill；③ 只用 `build_from_environment`/`.arun`/回调/`load_session`/`persist_agent_session` 的**文档化嵌入面**，不碰内部 API——四点正对 P2 §4.3 的四坑。**默认 `read-only`**：对话只读 wiki、不写、不取 `raw/` 快照、不跑 check（与 plain `query` 同理）。一会话一 `Agentao` 对象 + `asyncio.Lock`（同会话两轮不并发）；只读会话间可并发，**不**经 §4.2 写 worker，故不破坏单写者假设。可写的多轮"工作会话"（逐轮 gated）与"把 CLI 一次性操作也迁到嵌入"都**推后**（§10）——只在多轮确有需求处引入嵌入，不扩面。

---

## 10. 不纳入 P4（推后）

- **可写的多轮"工作会话"（对话中逐轮 gated 写 `wiki/`）** → 推后。P4 的多轮会话**默认只读**（决策P4-8）；让对话逐轮写需要：每个写轮包 `raw/` 快照 + check + 有界自愈、且嵌入 agent 的权限姿态要能切到 `workspace-write`，复杂度与风险都高。一次性写仍由 ingest/`--backfill`（子进程、已 gated）覆盖，故先不做对话内写。
- **把 CLI 一次性 ingest/query 也迁到进程内嵌入** → 不做：P2 子进程路径已验证、零业务改动；只在"多轮"这一子进程做不到的点引入嵌入，不为统一而扩面（决策P4-1 修订）。
- **一次性子进程的 token 级流式** → 仍推后：`agentao run --format json` 飞行中无流可推（决策P4-6①）。多轮会话路径已由嵌入回调拿到 token/工具流式（决策P4-8），故"流式"需求在最该要它的对话场景已满足；一次性路径要流式须先有 agentao 子进程流式格式，届时另说。
- **会话历史的跨重启重建** → P4 每轮 `persist_agent_session` 落盘，但**重启后自动 `load_session` 重建内存会话**列为可选增强、非 P4 必需（进程存活期内会话可用即达标）。
- **Web 端直接编辑 wiki 页** → **不做**：DESIGN 原则 2"人不直接写 wiki，Agent 全权拥有 wiki 层"。Web 只读 wiki + 经 ingest/query/会话让 agent 改。投喂仅限写 `raw/`（事实层、人可投放）。
- **多会话并发、鉴权、多租户、远程暴露、TLS** → 企业版 E2（DESIGN §6）。P4 是单用户本地；只读会话间可并发，但**写**并发仍由单 worker 串行挡住。
- **多格式上传摄入（docx/pdf/web clip）** → P5；P4 的 `POST /api/raw` 仅接受 `.md` 文本。
- **交互式图形化 graph（力导向/拖拽/缩放）** → **本文有意收窄**：P3 决策P3-7 曾把"图形化展示整体留待 P4 Web UI"。P4 兑现的是"在浏览器里**看** graph"——直接 serve P3 的自包含静态 `graph.html`（它本就是个网页），已满足浏览需求；而**力导向/拖拽/缩放的交互式渲染**正是"不要过度设计"要避开的部分，按需另做（DESIGN §8 graph 增强），不进 P4 硬承诺。
- **ACP server 模式** → 不做（附录 A 列为非 MVP 备选）。
- **作业持久化 / 历史** → 不做：作业表仅内存、单进程，进程退出即清。

---

## 11. 测试计划（`uv run pytest`，不打真实 LLM）

宿主测试用 `fastapi.testclient.TestClient`（**进程内、无 socket**）+ 临时知识库；**两类 LLM 都打桩，不打真实 LLM**：一次性路径**注入 fake runner**（同 P2/P3），会话路径 `monkeypatch` `build_from_environment` 返回一个 **fake agent**（`arun` 把 user/assistant 追加进 `messages` 并经回调吐几个 token，可断言"跨轮累积 + 流式"）。`web` extra 缺失时整组 `pytest.importorskip("fastapi")` 跳过：

| 测试 | 覆盖 |
|------|------|
| `test_web.py`（静态/浏览） | `GET /` 返回 `index.html`；`/api/pages` 列出非 config 页并按 type 分组；`/api/page` 返回 `{meta,html}`，坏/缺 frontmatter 仍渲染正文（`meta=null`）；`[[wikilink]]` 重写为站内链、断链标灰；**路径穿越**（`?path=../../etc/passwd` 等）→ 409 |
| `test_web.py`（报告） | `/api/report/check|health|lint` 的 JSON 与对应 `run_check`/`run_health`/`run_lint` 在**同一 fixture** 上字节对齐（复用、不漂移）；`/graph` 触发构建并 302、`graph/graph.html` 落盘 |
| `test_web.py`（一次性写作业） | fake runner 下 `POST /api/ingest` → `{job_id}` → 轮询 `GET /api/jobs/{id}` 至 `done`，`exit_code` 与直接 `run_ingest` 一致；写合规 → 0、写阻断性违规 → 3、动 `raw/` → 4、agent 失败 → 5；`POST /api/query` 只读与 `--backfill` 两路径；**两个写作业串行完成**（worker FIFO，`raw/` 快照不互踩）；非法请求体 → 422 |
| `test_web.py`（多轮会话） | `POST /api/chat` 无 `conversation_id` → 新建会话并回传 id；**带同一 id 连发两轮 → fake agent 的 `messages` 累积、第二轮能引用第一轮上下文**；SSE 流先吐 token/工具事件、末尾 `done` + 完整答案；`build_from_environment` 以 `working_directory=kb`、`permission_mode="read-only"`、自带 `logger`（非库内）被调用，且 `guanlan-wiki` 被激活；同一会话两轮被 `asyncio.Lock` 串行；会话**不触** `raw/` 快照 / check；`DELETE /api/conversations/{id}` 丢弃内存对象；落盘 `.agentao/sessions/*.json`（断言 `persist_agent_session` 被调） |
| `test_web.py`（生命周期 SSE） | `GET /api/jobs/{id}/events` 推 queued→running→done 并闭流；未知 id → 404；该流只含**生命周期**、不含 token（与会话 token 流区分，决策P4-6） |
| `test_web.py`（投喂/安全） | `POST /api/raw` 写 `raw/<slug>.md`、重名 → 409、越界 `name` → 409；`serve` 仅以 `host="127.0.0.1"` 起 uvicorn；非知识库根 / 缺 `web` extra → `EXIT_USAGE(1)` |

**验收标准（P4 Done）**：上述测试全绿；`guanlan web` 起服后，浏览器可浏览 wiki / 跟随 `[[wikilink]]` 导航 / 打开 `graph.html` / 跑 `check`·`health`·`lint` 看报告 / 从 `raw/` 选一篇 `.md` 触发 `ingest` 并看到触及页摘要与门禁结果 / 提一次带引用的一次性 `query` / **与 agent 多轮对话**（追问保留上下文、token 流式可见）；全程**不修改 `raw/`**（除显式 `POST /api/raw` 新增）、`wiki/` 仅由一次性 ingest/`--backfill` 作业按 P2 门禁改动（**会话只读、不写**）；进程仅监听 `127.0.0.1`。
