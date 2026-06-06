# P4 — Web 宿主（可选图形入口）实现设计

> 本文是 [`DESIGN.md`](DESIGN.md) §1 / §5.2 / §7-P4 / 附录 A 的**实现级细化**，是 P4 编码的落地依据。
> 高层理念与原则以 `DESIGN.md` 为准；本文只在 P4 范围内把"怎么做"钉死到模块、数据结构、契约与测试。
> P4 不引入任何新业务逻辑——它只是把 P2/P3 已交付的命令搬到浏览器里，故请先读 [`P2-最小闭环.md`](P2-最小闭环.md) 与 [`P3-健康与图谱.md`](P3-健康与图谱.md)。
> 文中标注的 **\[决策P4-N]** 是对 `DESIGN.md` 的具体化或受控偏离（含 §5.2/附录 A 把 P4 定为"进程内嵌入"的那条），集中记录在 §9。

---

## 1. 目标与范围

**P4 = 一个本地、单用户、单进程的 Web 宿主，复用同一文件库，把 CLI 的浏览 / ingest / 问答 / check / health / lint / graph 搬进浏览器。**（CLI 的一次性 `query` 在 Web 中等价为**单轮 chat**，不另设 `/api/query`。）

它是 **MVP 之后的可选叠加层**：不装、不起 `guanlan web`，整套东西照旧用 CLI 跑通（DESIGN §1）。Web 宿主不改变"纯文件"的本质——markdown 仍是唯一事实来源，Web 只是 `ingest` 与问答的**另一个入口**、wiki 的**只读浏览器**。

纳入 P4：

- `guanlan web [--port N] [--no-browser] [--model M] [--no-agent-log]` —— 起一个仅监听 `127.0.0.1` 的本地 Web 宿主（默认端口 `8765`）。
- **浏览**：列页面 / 读单页（markdown 渲染 + `[[wikilink]]` 可点击导航）/ 看 `graph.html`。
- **唯一写入口（复用 P2 子进程）**：从 `raw/` 选一篇已存在的 `.md` 触发 `ingest`。经后台单 worker 串行执行、**轮询**拿结果（v1 不做写作业的 SSE）。
- **问答 / 多轮会话（进程内嵌入，read-only）**：与 agent **就同一知识库对话**——一次性提问，或追问、"展开第 2 点"、"刚发现的矛盾再说细些"，跨轮保留上下文。**一次性 query 即单轮会话**，与多轮**共用同一条只读嵌入路径**（不再单设子进程 query）。**默认只读**（read-only 姿态，不写 `wiki/`、不过门禁），答案经 `fetch` 流式（最小事件集 `token`/`done`/`error`）。见 §4.4。
- **零 LLM 审计（复用 P3/P2）**：在页面里跑 `check` / `health` / `lint`，按既有 JSON 契约展示。
- **raw 浏览**：列 `raw/*.md` 并选其一触发 ingest（**只列、不经 Web 写 raw**）。
- **观澜气质**：UI 配色承"观水有术，必观其澜"意境（水墨底 + 深澜 + 澜纹点睛，纯 CSS、不靠动效）。见 §6.1。

**不**纳入 P4 v1（推后，见 §10）：**Web 端写 `raw/`**（粘贴存盘）、**`query --backfill`**（gated 写 synthesis）、**可写的多轮"工作会话"**（对话中逐轮 gated 写 `wiki/`）、**工具进度流式 / 写作业 SSE**、**会话落盘与跨重启恢复 / LRU 淘汰**、把写（`ingest`）迁到嵌入、自定义 Tool 注入、Web 端直接编辑 wiki、鉴权 / 多租户 / 远程暴露 / TLS、多格式上传摄入、交互式图形化 graph、ACP server 模式。

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
  GET /api/pages           iter_pages(wiki) + load_page.meta            → 页面列表 JSON（前端右栏搜索/Home 用）
  GET /api/page?path=…     load_page → 安全 markdown 渲染 + [[wikilink]] 重写 → {meta, html}
  GET /graph[?json_only]   build_and_write_graph + 写 graph/ → 302 → /graph/graph.html（或 .json）
  GET /graph/{html|json}   P3 派生静态文件（白名单两项，杜绝穿越）
  GET /api/raw             列 raw/*.md（只列，不经 Web 写 raw）

审计（零 LLM，async 端点经 to_thread 卸载）
  GET /api/report/check    run_check(wiki)   → 既有 JSON 契约（P2 §5.2）
  GET /api/report/health   run_health(wiki)  → 既有 JSON 契约（P3 §4.3）
  GET /api/report/lint     run_lint(wiki)    → 既有 JSON 契约（P3 §5.4）

写（仅 ingest；LLM 子进程，入 worker 队列 → 轮询，无 SSE）
  POST /api/ingest {target}    → enqueue(run_ingest)   → {job_id}
  GET  /api/jobs/<id>          → {state, exit_code, output}（轮询）

问答 / 多轮会话（LLM 嵌入，read-only，await arun → fetch 流式）
  POST /api/chat {conversation_id?, message}  → ConversationStore.create/get → Conversation.turn
       └─ 响应 text/event-stream；前端 fetch 读 body；事件 token / done / error（决策P4-6）
          done 含 {answer, answer_html, conversation_id}（answer_html = 安全渲染 + [[页]]→站内链）
          省略 conversation_id → 新建会话（会话数达上限 MAX_CONVERSATIONS → 503）；一次性=单轮
  GET/DELETE /api/conversations[/<id>]        → 列/丢内存会话（只读、不过门禁、进程退出即清）
```

Web 宿主自身**不做任何确定性/语义判断**：**零 LLM 与写路径**的结论来自被复用的包内函数（`run_check`/`run_health`/`run_lint`/`build_graph`/`run_ingest`），**问答**的结论来自只读嵌入的 `Agentao.arun`——宿主只负责"收 HTTP 请求 → 调既有函数或嵌入 agent → 序列化/转发结果"。这是 DESIGN"wrapper 不承载业务智能"在 Web 层的延续。

---

## 3. 模块落点（交付清单）

新增一个 `guanlan/web/` 子包；**不改 P2/P3 任何既有模块的行为**（仅可能加一处可选的结构化返回，见决策P4-7）：

| 文件 | 职责 | 备注 |
|------|------|------|
| `guanlan/web/__init__.py` | 子包入口；导出 `serve(root, *, port, open_browser, model, runner)` | |
| `guanlan/web/app.py` | FastAPI app + 路由（`async def` 端点）；零 LLM 报告经 `anyio.to_thread.run_sync` 卸载；pydantic 请求体模型；路径穿越校验；chat 的 `text/event-stream` 响应 | §4 / §5 **\[决策P4-2/5/6]** |
| `guanlan/web/jobs.py` | 单后台 worker 线程 + `queue.Queue` 作业表（与事件循环并存，FIFO 串行**唯一写作业 ingest**）；`enqueue`/`get_job`（轮询，无 SSE） | §4.2 **\[决策P4-5]** |
| `guanlan/web/chat.py` | **问答 / 多轮会话**：`ConversationStore`（内存会话表 + `threading.Lock` + `MAX_CONVERSATIONS` 硬上限）持有多个 `Conversation`——只读进程内嵌入 `Agentao`（`build_from_environment` + 构造期 `transport=build_compat_transport(...)` + `arun`），一会话一对象 + `asyncio.Lock` + `closed` 标志，transport 固定回调→当前 turn emit→`token` 流；`configure_agent_log` 把会话日志接到 `<kb>/agentao.log`（`_logger.propagate=False`）；**仅内存、v1 不落盘** | §4.4 **\[决策P4-8]** |
| `guanlan/web/server.py` | `serve(...)`：编程式起 `uvicorn`（`host=127.0.0.1`、`workers=1`）、端口预探测、`--no-browser` 外按需 `webbrowser.open`、`--no-agent-log` 外 `configure_agent_log` | §7 **\[决策P4-2]** |
| `guanlan/web/render.py` | `render_markdown`（单页与对话答案共用）：`load_page` → markdown→**安全** html（有 `markdown` 则用 + 关原始 HTML 透传 + 中和 `javascript:`/`data:` 链接，无则回退 `<pre>`）+ `[[wikilink]]` 经 `pages.link_resolution_index`/`link_stem` 重写为站内锚链 + 行内 `<code>` 整段忠实引用兜底联链；`render_page` 返 `{meta, html}` | §6 |
| `guanlan/web/static/index.html` | 单页前端（vanilla JS + `fetch`，无 npm/无构建/无 CDN）；**两栏 + 底部满宽**：左=对话内容 / 右=Wiki（搜索 + Home/前后导航 + 内容）/ 底部满宽=输入框；可拖动列分隔；顶栏动作（新会话/ingest/check/health/lint/graph，报告与 ingest 选择走浮层） | §6 **\[决策P4-3/P4-9]** |
| `guanlan/web/static/app.js` `app.css` | 前端逻辑与样式（随包静态资源） | §6 |
| `guanlan/cli.py` | 新增 `web` 子命令（沿用 `-C/--dir`；`--port`/`--no-browser`/`--model`/`--no-agent-log`） | §7 |
| `pyproject.toml` | 新增可选 extra `[project.optional-dependencies] web = ["fastapi>=0.110", "uvicorn>=0.29", "markdown>=3"]`；静态资源 `force-include` 进 wheel | **\[决策P4-2]** |
| `tests/test_web.py` | 见 §11 | fake runner 注入 + `fastapi.testclient.TestClient`（进程内、无 socket）；**不打真实 LLM** |

> 仍**不**新增 skill `scripts/`，**不**改 `skills/guanlan-wiki/`：Web 宿主不改变 agent 工作流——**写**仍走 `ingest` 子进程 + skill + 门禁，**问答**走嵌入 agent，二者都带同一 `guanlan-wiki` skill。
> 复用面（只 import、不复制逻辑）：**写**路径 `ingest.run_ingest`；**零 LLM** `check.{run_check, format_report}` / `health.{run_health, format_report}` / `lint.{run_lint, format_report}` / `graph.build_and_write_graph`（**非** `graph_entrypoint`——它会打印，worker 进程级 `redirect_stdout` 期间不引入并发打印者，决策P4-5）/ `pages.{iter_pages, load_page, page_title, page_type, link_stem, link_resolution_index, WIKILINK_RE}` / `paths.require_kb_root` / `skill.{SKILL_NAME, ensure_skill_available}` / `errors.*`。**问答**路径不复用 `query.run_query`，改走 `chat.py` 的 `agentao.embedding` 嵌入（§4.4）。
> **ASGI ≠ 重写业务**：FastAPI/uvicorn 只是表现层；**零 LLM 与写路径**的结论来自上述既有同步函数，**问答**的结论来自只读嵌入 `arun` 的返回值。端点是 `async def`，但**阻塞调用一律卸到线程**（零 LLM 报告走 `anyio.to_thread.run_sync`；写作业 `ingest` 走单 worker 线程），绝不在事件循环里直接跑阻塞代码（决策P4-2）。
> **多轮会话另用嵌入面**：`agentao.embedding.build_from_environment` + `Agentao.arun` + 一个文本回调——这是 agentao 文档化的嵌入契约，非内部 API（决策P4-8）。**v1 不调用** `persist_agent_session`/`load_session`（仅内存）。`agentao` 已是核心依赖（`agentao[cli]`），嵌入**不引新依赖**。

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

### 4.2 单后台 worker，FIFO 串行执行唯一写作业 ingest　**\[决策P4-5]**

`ingest` 会改 `wiki/`，且 P2 门禁的 `raw/` 前后快照**假设单进程单写者**（P2 §11 明确把并发锁推后）。故 v1 的**唯一写作业 `ingest`** 进**一个** `queue.Queue`，由**单条** worker 线程（与 uvicorn 事件循环并存）FIFO 取出执行（**问答全部走 §4.4 嵌入路径、不进此队列**）：

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
            job.exit_code = fn()               # v1 fn 只可能是 lambda: run_ingest(...)（唯一写作业）
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
from agentao.embedding.compat import build_compat_transport
from agentao.permissions import PermissionMode

class Conversation:                                      # 由 ConversationStore（内存表 + threading.Lock + MAX_CONVERSATIONS 硬上限）持有
    def __init__(self, cid: str, kb: Path, model: str | None):
        self.id = cid
        self.lock = asyncio.Lock()                       # 同一会话不并发两轮
        self.closed = False                              # 删除后置位（锁内）；拦下"已接受但尚未起跑"的排队 turn
        self._emit = None                                # 当前 turn 的**线程安全 emit**；transport 固定回调转发到它
        ensure_skill_available(kb)                       # ← 坑②前置：构造前保 guanlan-wiki 可发现
        opts = dict(                                     # ← 化解四坑之①③：工厂读 .env/~/.agentao 取凭据 + 传我们的 logger
            working_directory=kb, logger=_quiet_logger(),#    （不写 <wd>/agentao.log，不污染知识库）
            transport=build_compat_transport(            # ← token 流式的**唯一活线**：transport 在构造期冻结
                llm_text_callback=self._on_token),       #    （EventType.LLM_TEXT→chunk）。事后改 self.agent.
        )                                                #    llm_text_callback 是死代码（只读存档，agent.py:457）。
        if model is not None:                            # ← --model 可选：**仅在给定时**放进 overrides。显式 model=None 会
            opts["model"] = model                        #    覆盖 .env 发现的模型、触发 "model required" ValueError（agent.py:397）
        self.agent = build_from_environment(**opts)      #    注：无 permission_mode 形参（传了转发进 __init__ 会 TypeError）
        # 只读姿态须在**两个**运行时点同步置位（照搬 agentao cli/run.py:523-529）：
        #   engine 的 read-only 预设是空的——真正拦截写/shell 工具的是 ToolRunner.readonly_mode。
        #   只调 set_mode 不调 set_readonly_mode = 没真正只读。
        self.agent.permission_engine.set_mode(PermissionMode.READ_ONLY)
        self.agent.tool_runner.set_readonly_mode(True)
        self.agent.skill_manager.activate_skill(         # ← 化解坑②：嵌入式同样需显式激活 skill
            SKILL_NAME, task_description="观澜 Web 只读问答")

    def _on_token(self, chunk: str) -> None:             # transport 固定回调，**在 arun 的 executor 线程里跑**
        if self._emit:                                   # lock 串行化同会话各 turn，故单槽 _emit 无竞态
            self._emit("token", chunk)                   # _emit 已是 call_soon_threadsafe 包装（见 turn）

    async def turn(self, msg: str, emit) -> str:
        async with self.lock:                            # v1 最小事件集：生成文本作 token 推前端（emit→Queue→流式 body）
            if self.closed:                              # StreamingResponse 在路由返回后才起跑 turn，其间 DELETE 可能已删本会话
                raise RuntimeError("会话已删除")          #   → 锁内复检，拒在已 close 的 agent 上跑（端点据异常发 error）
            loop = asyncio.get_running_loop()            # arun 内部 run_in_executor 跑同步 chat()，故 _on_token 落在
            def thread_safe_emit(kind, data):            # **线程池线程**；emit 碰 asyncio.Queue.put_nowait 是事件循环对象，
                loop.call_soon_threadsafe(emit, kind, data)  # **必须** call_soon_threadsafe 投递回 loop（否则丢 token/卡流/偶发崩）
            self._emit = thread_safe_emit
            try:
                return await self.agent.arun(msg)        # ← arun = chat 的 async 包装（内部 run_in_executor），ASGI 原生 await
            finally:
                self._emit = None
```

**关键设计点**：

- **`arun()` 与 ASGI 天作之合**：`Agentao.arun` 是 `chat` 的 async 包装（内部 `run_in_executor`），FastAPI 端点直接 `await`，无需我们自建线程桥——正好兑现决策P4-2 选 ASGI 的赌注。
- **流式 = 最小事件集 `token` / `done` / `error`**：token 经**构造期冻结的 transport** 流出——`build_compat_transport(llm_text_callback=…)`（或 `SdkTransport(on_event=…)` 过滤 `EventType.LLM_TEXT`）传给构造函数，**不可**事后改 `self.agent.llm_text_callback`（构造后该属性只是只读存档，运行时不重读，token 不会流；见 `agent.py:457`）。每轮 emit 不同 → 固定回调转发到 `Conversation` 上"当前 turn 的 emit"槽（`asyncio.Lock` 串行化同会话各轮，单槽无竞态）。**线程边界**：`arun` 内部 `run_in_executor` 跑同步 `chat()`，故 `_on_token` 在**线程池线程**触发；它要投递的 `asyncio.Queue.put_nowait` 是事件循环对象，**必须** `loop.call_soon_threadsafe(...)` 桥回 server loop（直接跨线程碰会丢 token / 卡流 / 偶发崩）——故"当前 turn 的 emit"槽存的是一个 `call_soon_threadsafe` 包装，不是裸 `emit`。末尾发 `done`（含 `answer` + **`answer_html`** + `conversation_id`——`answer_html` 是端点在 `done` 前把完整答案再过一遍 `render_markdown`（安全 HTML + `[[页]]`→站内链）得到的富排版，渲染失败则省略该键、前端回退用纯文本 `answer`），异常发 `error`（**含 `conversation_id`**，使首轮失败时前端记住已建会话、避免下次另起堆到 503）。**工具/step 进度事件 v1 不做**（transport 虽暴露 `TOOL_*`/`TURN_*` 等事件，但纳入会让契约/测试变重，推后，§10）。
- **客户端断开不打断在飞的 turn**：端点用 `asyncio.shield` 包住 `conv.turn`——agentao 的 `arun` 在取消时只转发 `token.cancel()` 便立刻 re-raise、不等 executor 线程收尾，于是 `lock` 会在后台残线程仍在跑时被释放，下一轮就可能与残线程并发改 `agent.messages`/串错 token。shield 让 turn 始终跑到自然结束（lock 全程持有），代价是断开后该轮仍跑完（本地单用户、轮次有界，可接受）。`DELETE` 端点亦先拿会话锁再 `close()`，与在飞 turn 收尾串行。
- **传输用 `fetch` 流式，不用 `EventSource`**：浏览器 `EventSource` 只能 `GET`、不能带 POST 体；而每轮要 POST `message`。故 `POST /api/chat` 直接返回 `text/event-stream`，前端用 `fetch()` 读 `response.body`（`ReadableStream`）解析事件——见决策P4-6。（备选"POST 建 turn + GET /events 订阅"被否：多一个端点，且 POST 与订阅之间有**事件丢失/竞态窗口**。）
- **v1 仅内存、不落盘**：会话对象活在进程内，进程退出即清。`persist_agent_session`/`load_session`（落盘 `.agentao/sessions/`）、跨重启恢复、LRU 淘汰**全部推后**（§10）——进程存活期内上下文可用即达标。内存设界用一个保守的会话数**硬上限** `MAX_CONVERSATIONS=100`（`ConversationStore.create` 在 `threading.Lock` 内查容量，超出抛 `RuntimeError` → 端点转 **503**；锁全程持有含较慢的 agent 构造，使 cap 是真正硬上限、并发新建无法绕过），不上 LRU。`create` 经 `anyio.to_thread` 卸载（构造慢），故 `ConversationStore` 用 `threading.Lock`（非 asyncio）护表。
- **P2 §4.3 嵌入四坑逐一化解**：① 构造凭据缺失 → 用 `build_from_environment()`（读 `.env`/`~/.agentao`），不裸构造；② skill 不自动激活 → 构造后显式激活 `guanlan-wiki`；③ 日志栈归宿主自管 → 传我们自己的共享 `logger=`（嵌入契约：注入 logger 后 `LLMClient` 不再自挂 handler），默认经 `configure_agent_log` 给它挂**一个** `RotatingFileHandler` 落 `<wd>/agentao.log`（**像 CLI 一样记会话日志**；`agentao.log[.*]` 已 gitignore、扫描只看 `*.md` 故不污染库；`guanlan web --no-agent-log` 可关）。只挂一次很关键——`build_from_environment` 每会话构造一次，LLMClient 对重复 handler 不去重，多挂会把每行写 N 遍。共享 logger 还须 `propagate=False`：否则 `--no-agent-log`（无 handler）下 `WARNING+` 会经 `logging.lastResort` 落 `sys.stderr`，而 ingest worker 正用进程级 `redirect_stderr` 捕获输出，并发会话日志会被串进某个 ingest 作业的结果面板；④ 内部 API 耦合 → 只用 `build_from_environment`/`.arun`/一个文本回调这一组**嵌入契约**面。
- **只读姿态 + 不过门禁**：会话**不**在构造期传 `permission_mode`（`build_from_environment` 无此形参，传了会被转发进 `Agentao.__init__` 而 `TypeError`）；改在构造后**两点同步置位**——`agent.permission_engine.set_mode(PermissionMode.READ_ONLY)` **且** `agent.tool_runner.set_readonly_mode(True)`（照搬 `cli/run.py:523-529`：engine 的 read-only 预设为空，真正拦截写/shell 的是 `ToolRunner.readonly_mode`，缺第二步 = 没真正只读）。纵深防御保证对话不写 `wiki/`/`raw/`；既然不写，就**不取 `raw/` 快照、不跑 check**。可写的多轮"工作会话"（逐轮 gated）**推后**（§10）。
- **并发**：每会话一把 `asyncio.Lock`（同一会话的两轮不并发跑同一 agent 对象）；不同**只读**会话间可并发（都不写、无 `raw/` 竞态），**不**走 §4.2 的写 worker——写 worker 仍只管 `ingest`，单写者假设不被会话破坏。

---

## 5. HTTP API 契约（`guanlan/web/app.py`）

POST 请求体用 **pydantic 模型**（`IngestBody`/`ChatBody`），FastAPI 自动校验/422；响应 `Content-Type` 由返回类型决定。错误经 `HTTPException` 抛（400 用法 / 404 不存在 / 409 越界 / 500 内部）。**读路径参数都过路径穿越校验**：解析后必须 `resolve()` 落在 `<root>/wiki/` 内，否则 409。

| 方法 路径 | 入参 | 复用 | 返回 |
|------|------|------|------|
| `GET /` | — | 静态 | `index.html` |
| `GET /static/*` | — | 静态 | 随包资源（js/css） |
| `GET /api/pages` | — | `iter_pages`+`load_page` | `{pages:[{path,title,type}]}`（按 type 分组，排除 config） |
| `GET /api/page` | `path` | `render.render_page` | `{meta, html}`；坏/缺 frontmatter 时 `meta=null` 仍渲染正文（容错档，同 P3 决策P3-8）；越界 409、缺失 404 |
| `GET /api/report/check` | — | `run_check` | P2 §5.2 JSON（`{ok,pages_checked,violations}`） |
| `GET /api/report/health` | — | `run_health` | P3 §4.3 JSON（`strict` 只影响 CLI 退出码、不改 JSON 体，故 Web 不收该参） |
| `GET /api/report/lint` | — | `run_lint` | P3 §5.4 JSON |
| `GET /graph` | `json_only?` | `build_and_write_graph` | 构建后 302 → `/graph/graph.html`（`json_only` → `/graph/graph.json`）；写失败 500 |
| `GET /graph/{filename}` | — | 静态（派生） | `graph.html`/`graph.json` 白名单（其余 404），未生成 404 |
| `GET /api/raw` | — | 列目录（**只读**） | `{files:[{name,size}]}` |
| `POST /api/ingest` | `{target,model?}` | 入队 `run_ingest` | `{job_id}`（**唯一写入口**） |
| `GET /api/jobs/{id}` | — | 作业表 | `{id,kind,state,exit_code,output}`（**轮询**，404 未知 id） |
| `POST /api/chat` | `{conversation_id?,message,model?}` | `chat.Conversation.turn`（嵌入，read-only） | **`text/event-stream`**；前端 `fetch` 读 body；事件 `token`* → `done`{answer,answer_html?,conversation_id} / `error`{message,conversation_id}；省略 `conversation_id` → 新建会话（达上限 503）；未知 id → 404 |
| `GET /api/conversations` | — | 会话表 | `{conversations:[{id,title,turns}]}`（内存现存会话） |
| `DELETE /api/conversations/{id}` | — | 会话表 | 先取会话锁待在飞 turn 收尾再 `close()` 丢弃（404 未知 id） |

- `target` 仍经 `run_ingest` 内部 `_resolve_raw_target`（必须在 `raw/` 下、是 `.md`、存在）兜底——Web 不旁路 P2 入口校验。
- **ingest 是 v1 唯一写**：经单 worker 串行 + P2 完整门禁（`raw/` 快照 + check + 自愈）。退出码透传进 `job.exit_code`（0/3/4/5），前端据此显示通过/失败徽标。
- **chat 全程只读**：`read-only` 嵌入构造（决策P4-8），不取 `raw/` 快照、不跑 check；流式契约见 §4.4。`POST /api/raw`（写 raw）与 `query --backfill`（gated 写）**v1 不提供**（§10）。

---

## 6. 前端（`guanlan/web/static/`，自包含、无构建）　**\[决策P4-3]**

与 P3 `graph.html` 同精神：**单页静态资源，vanilla JS + `fetch`，无 npm / 无打包 / 无 CDN / 无第三方运行时**（FastAPI/uvicorn 是**服务端**依赖，前端不引任何 JS 框架；流式也只用 `fetch` 读 `response.body`，**不用 `EventSource`**——它不能 POST，见决策P4-6）。**对话为主、Wiki 为辅**的两栏 + 底部满宽输入布局（实现演进自初版"侧栏页面树/主区/对话区"三栏，落地为下式——把对话提到第一性、Wiki 收成可检索的右栏）：

- **左栏「对话内容」**：`#chat-log` 流式问答记录。输入 → `POST /api/chat`，`fetch` 流式读 `token` 逐字上屏、`done` 用 `answer_html` 富排版收尾（渲染失败回退纯文本 `answer`）；气泡里渲染出的 `[[wikilink]]` 点了切右栏对应单页。保留 `conversation_id` 即多轮、`新会话` 清空并 `DELETE` 旧会话（避免堆到 503）。
- **右栏「Wiki」**：一个 `#wiki-view` 承载——启动显示 **Concept 目录**（首页），点页进 `/api/page` 单页内容，正文 `[[wikilink]]` 续进；顶部 `wiki-bar` 有 `⌂ Home`（回 Concept 目录）/ `←` `→`（**视图历史栈**前后导航，每条目自带搜索快照）/ `Wiki Search`（即时过滤 `/api/pages` 缓存）。断链标灰、不可点。
- **底部满宽「对话输入框」**：`textarea`——**回车发送 / Option(Alt)·Shift-Enter 换行**（直接调 `submitChat()`，不走 Safari 16 前缺失的 `requestSubmit()`；放行输入法 `isComposing`/`keyCode 229`）；**in-flight 守卫**（流式中 `#chat-send` 禁用，回车与按钮共用、不叠并发轮次）。
- **可拖动列分隔** `#col-split`：拖动改写 `--wiki-w`（右栏宽），双击复位。
- **顶栏动作**：`新会话` / `ingest`（从 `raw/` 列表选一篇 → `POST /api/ingest` → **轮询** `/api/jobs/{id}`）/ `check`·`health`·`lint`（拉 JSON 报告）/ `graph`（新开 `/graph`）——后四者结果与 ingest 选择都呈现在一个**浮层** `#overlay`（modal），不挤占两栏。

**安全 markdown 渲染（`render.py` 的 `render_markdown`，单页与对话答案共用）**：`load_page` 取正文 → 若装了 `markdown`（`guanlan-wiki[web]` extra）则渲染为 HTML，否则回退转义后的 `<pre>` 源码视图（**缺 extra 也能跑、只是不美观**）。富渲染始终带**两道安全闸**（纵深防御 XSS，决策P4-4）：① `_EscapeHtmlExtension` 关原始 HTML 透传（注销 python-markdown 的两个 HTML 处理器，杜绝投喂资料夹带 `<img onerror=…>` 等载荷经 `/api/page` 同源注入）；② `_SafeLinkExtension` + URL 协议白名单（`http`/`https`/`mailto`/相对链接）中和 `javascript:`/`data:`/`vbscript:` 链接（含 HTML 实体解码 + 剥控制符，防 `java&#x09;script:` 绕过）。`[[wikilink]]` 重写经一个 markdown **行内处理器**（在解析树上跑、不做字符串替换，故代码块内 `[[…]]` 不被误改），复用 `pages.WIKILINK_RE` + `pages.link_resolution_index`/`link_stem`——与 `check`/`graph` **同一口径**（stem ∪ 别名解析、含 config 可链），每次渲染重建不缓存。另有 `_CodePathLinkExtension`：把**整段恰好是对某现有页忠实引用**的行内 `<code>`（`wiki/sources/x.md`、`[[x]]` 套反引号等源出处写法）破例转成站内 wikilink，放行含空格的合法页名、挡住命令/多 token 代码（`cat wiki/x.md`），并跳过 `<pre>`/已在 `<a>` 内的 code（保字面、避嵌套锚）。

> 渲染是唯一可能引入依赖的点，故收敛为**可选 extra**：核心 `guanlan` 安装不因 Web 而背上 markdown 依赖；装了 `guanlan-wiki[web]` 才有富渲染。这呼应"纯文件、薄 wrapper"——Web 是叠加层，其依赖不下沉到核心。

### 6.1 配色与意境——"观水有术，必观其澜"　**\[决策P4-9]**

UI 取**水墨 + 澜**意象：以静水/雾面为底，深澜为纲，澜纹（涟漪青）作点睛——呼应《孟子》"观水有术，必观其澜"与"在信息汪洋中观脉络趋势"的项目立意。**纯 CSS 自定义属性**落在 `app.css`，与 §6 的"无框架、无构建"一致；**配色是主要载体，不靠动效堆砌**（克制，同决策P4-3/P3-7）。

```css
:root {
  /* 水墨底：雾面纸 / 静水微光 */
  --lan-paper:    #EEF3F4;   /* 页面背景：远水雾面 */
  --lan-surface:  #F7FAFB;   /* 主区/卡片：水面微光（略亮于背景）*/
  --lan-mist:     #D2E0E3;   /* 分隔线/边框：远水淡雾（极淡）*/
  /* 澜：由浅及深的水色 */
  --lan-ripple:   #3F9DB3;   /* 澜纹/涟漪青：链接、激活态、强调 */
  --lan-deep:     #0F4C5C;   /* 深澜：标题、主按钮、顶栏 */
  --lan-abyss:    #06303B;   /* 渊面：深色块（备用）*/
  /* 墨：字色由浓及淡 */
  --lan-ink:      #1B2A2E;   /* 浓墨：正文 */
  --lan-ink-soft: #52666B;   /* 淡墨：次要文字/元信息 */
  --lan-foam:     #EAF6F4;   /* 涟漪高光：hover/选中底色微染 */
}
```

- **意象落到组件**：`body` 叠一层**极淡的自上而下渐变**（`--lan-surface`→`--lan-paper`）模拟水面纵深；左对话栏 `--lan-surface`、右 Wiki 栏 `--lan-paper`（一道 `--lan-mist` 左边线分隔）；顶栏深澜 `--lan-deep`；`--lan-abyss` 深色"渊面"留作深色块备用。
- **澜纹点睛**：`[[wikilink]]`、链接、用户气泡的强调色用 `--lan-ripple`，hover/可拖动列分隔条激活下沉到 `--lan-deep`；断链用 `--lan-mist` 标灰（"水静无澜"）。可拖动列宽 `--wiki-w` 由 JS 持久化到 `localStorage`。
- **墨色克制**：正文 `--lan-ink`（非纯黑，存水墨呼吸感），元信息 `--lan-ink-soft`；标题可选衬线字体栈以承水墨古意（非强制，缺字体回退系统衬线/无衬线）。
- **动效仅一处、极淡**：交互色过渡 `transition: color .15s`（涟漪般轻晕），**不做**波纹扩散、视差、背景动画等——避免把"配色意境"做成动效项目（决策P4-3）。
- **深色档"夜澜"（深青底 + 月白字）= 可选增强，非 v1 必需**（推后，§10），先交付浅色"昼澜"一档即可。

---

## 7. CLI 契约与退出码（`guanlan/cli.py` / `errors.py`）

```
guanlan web [--port N] [--no-browser] [--model M] [--no-agent-log]   # 默认 --port 8765
```

- 前置 `require_kb_root(root, writable=True)`（Web 含写入口，要求 `raw/`/`wiki/`/`AGENTAO.md`/`SCHEMA.md` 齐全），失败 → `EXIT_USAGE(1)` 并提示 `guanlan init`。
- `serve(...)` 编程式起 uvicorn：`uvicorn.run(app, host="127.0.0.1", port=port, workers=1, log_level="warning")`。**强制 `workers=1`**（决策P4-2/P4-5：内存作业表 + 单写者）；起服前 `_ensure_port_free` 预探测端口：范围外（非 1–65535）或被占（`OSError`）→ `EXIT_USAGE(1)`，提示换端口（预探测给出明确退出码，胜过 uvicorn 自身 bind 失败只打日志、静默退出）。
- 缺 `web` extra（import `fastapi`/`uvicorn` 失败）→ `EXIT_USAGE(1)`，提示 `pip install 'guanlan-wiki[web]'`。
- 默认起服务后用 `webbrowser.open` 打开 `http://127.0.0.1:<port>/`（后台守护线程轮询端口可连后再开，避免在服务 ready 前打开）；`--no-browser` 跳过。
- `--no-agent-log`（默认开）控制是否经 `configure_agent_log` 把**会话 agent** 日志像 CLI 那样落 `<kb>/agentao.log`（已 gitignore、扫描只看 `*.md` 故不污染库）；关之则共享 logger 无 handler、不写文件。**ingest 子进程**日志不受此开关影响。
- `--model` 透传给 `run_ingest`（写作业）与会话 `build_from_environment`（覆盖 Agentao 模型）；亦可 per-request 用 `model` 字段覆盖。**未给 `--model`/字段省略时**，会话**不得**把 `model=None` 塞进 `build_from_environment`——只在非 `None` 时入 overrides，否则会盖掉 `.env` 自动发现的模型、触发 `agent.py:397` 的 `ValueError`（详见决策P4-8）。
- **P4 不新增退出码**（区别于 P3 加了 6）：`web` 长驻，Ctrl-C 正常停服 → `EXIT_OK(0)`；前置/端口/缺 extra 错误 → `EXIT_USAGE(1)`。作业自身的退出码只进 `job.exit_code`，不影响 `web` 进程退出码。

---

## 8. 安全姿态　**\[决策P4-4]**

P4 是**个人版、本地、单用户**工具，安全模型与之匹配、不过度：

- **仅 bind `127.0.0.1`**，绝不 `0.0.0.0`。**无鉴权、无 TLS、无多租户**——它跑在用户自己的机器、以用户自己的文件权限读写自己的库。
- 唯一写入口 `ingest` 触发 `workspace-write` 的 agent 作业与文件写入；**严禁把该端口暴露到网络或反代到公网**——文档明确警示。需要远程/多用户是企业版（DESIGN §6 / E2）的事，不在 P4。
- **多轮会话以 `read-only` 嵌入构造**（决策P4-8）：纵深防御保证对话不写 `wiki/`/`raw/`，即使 prompt 诱导 agent 写也被权限姿态挡下；会话不旁路门禁，因为它压根不写。
- `raw/` 不变性仍由 P2 快照门禁兜底（写作业内）；Web 不放宽、不旁路任何 P2/P3 门禁。
- 路径穿越：所有 `path`/`name` 参数 `resolve()` 后必须落在 `wiki/`（读）或 `raw/`（写）内，否则 409，防止经 HTTP 读到库外文件。

---

## 9. 对 DESIGN.md 的偏离与决策记录

- **\[决策P4-1（修订）] P4 按读写分线：写（仅 `ingest`）走 P2 子进程，所有问答（一次性 + 多轮）走只读进程内嵌入。** 初版 P4-1 整体否掉嵌入，理由是"图形入口用 P2 子进程复用即可、且避免 P2 §4.3 嵌入四坑"。但多轮会话**在子进程上根本无法实现**（已核对 `cli/run.py` 无 `--session/--resume/--continue`、每 run 新起 `_session_id`），而 `Agentao.chat()`/`.arun()` 让 `self.messages` 跨轮累积正是为多轮而设——故必须为会话受控嵌入（化解四坑，§4.4）。既已嵌入，**一次性 query 就是单轮会话**，再单设子进程 query 反而带回 `redirect_stdout` 进程级捕获竞态；故**把所有读/问答收口到只读嵌入**（返回字符串、无捕获、可并发、不被慢 ingest 阻塞），**只把写（`ingest`）留在子进程**（已验证、gated）。**不**把写迁到嵌入（无谓扩面）。Tool 注入仍**不做**——会话 agent 自带 `guanlan-wiki` skill，无需把脚本包成工具。
- **\[决策P4-2] 服务端用 FastAPI + uvicorn（ASGI），收敛进 `guanlan-wiki[web]` 可选 extra；强制 `workers=1`，阻塞调用一律卸到线程。** 取舍：曾在 ① stdlib `http.server`（零依赖、floor）② Flask（同步 WSGI、契合阻塞负载）③ FastAPI/uvicorn（ASGI）三者间权衡——本工作负载是**同步阻塞**（`agentao run` 子进程 + 文件扫描），async 本身不带来并发收益，故 stdlib/Flask 更"对路"。**最终选 ASGI 是一个向前赌**：押注近期要**流式**进度、并为可能的多用户演进留口子；代价是 async 与阻塞负载不匹配，须以工程约束补偿——(a) **`workers=1`**（内存作业表 + 单写者，多进程会破坏二者）；(b) 阻塞调用绝不进事件循环（零 LLM 报告 `anyio.to_thread.run_sync`、LLM 作业进单 worker 线程，§4.2）；(c) `redirect_stdout` 捕获只许单 worker 串行做。依赖（fastapi/uvicorn/markdown）全部收在**可选 extra**，核心 `guanlan` 安装面不变、不装 `web` 不背这些；缺 extra 时 `guanlan web` 明确报错引导安装。**这条偏离了"最简明"的 stdlib/Flask**——记此以便：若流式/多用户的预期落空，应回落到 Flask（WSGI）或 stdlib，去掉 ASGI 的架构税。markdown 渲染仍缺则回退 `<pre>`。
- **\[决策P4-3] 前端是单页静态资源（vanilla JS + fetch，无 npm/构建/CDN/第三方运行时），与 P3 `graph.html` 同精神。** 不引前端工程化：一个 `index.html` + `app.js` + `app.css` 随 wheel 携带，打开即用。避免把"给文件库加个入口"退化成一个前端项目（与 P3 决策P3-7 否决"自写图布局"同一克制）。
- **\[决策P4-9] 配色承"观水有术，必观其澜"意境：水墨底 + 深澜纲 + 澜纹点睛，纯 CSS 变量承载、不靠动效。** 见 §6.1。把项目立意（在信息汪洋观脉络）落进视觉，是低成本的辨识度；但**配色是唯一载体**，动效只保留一处极淡的颜色过渡，深色"夜澜"档推后——既给气质，又不滑向"动效/主题项目"的过度设计（同决策P4-3 克制）。
- **\[决策P4-4] 仅监听 `127.0.0.1`、单用户、无鉴权/TLS/多租户；写端口严禁暴露网络。** 见 §8。鉴权/隔离/远程是企业版 E2，不预先塞进个人版。
- **\[决策P4-5] 唯一写作业 `ingest` 经单后台 worker FIFO 串行；问答与零 LLM 读都不进 worker、可并发。** 保住 P2 门禁的单写者假设（`raw/` 前后快照不容并发写），并使 `redirect_stdout` 捕获 `run_ingest` 人读输出**只在单 worker 串行发生**、无跨线程竞态。问答走嵌入返回字符串（无捕获），零 LLM 报告经 `to_thread` 卸载（返回 dataclass、无捕获），二者都不碰全局 stdout、互不阻塞。P2 §11 已把并发锁推后，这里用"单写者串行 + `workers=1`"作最省的等价物，不引文件锁。
- **\[决策P4-6] v1 流式只给 chat，一种契约：`token`/`done`/`error`，经 `fetch` 读 `text/event-stream`。** 写作业（`ingest`）**不做 SSE**，只 `GET /api/jobs/{id}` 轮询（飞行中无流可推、轮询对单用户足够，少一个端点/契约）。chat（嵌入）挂**一个文本回调**把 token 流出来——子进程 `--format json` 一锤子返回、嵌入回调天然增量，故流式天然只在 chat 路径。**不用 `EventSource`**（它只能 GET，承不了 POST 体），改 `POST /api/chat` 直接返回流、前端 `fetch` 读 `response.body`；备选"POST 建 turn + GET /events 订阅"被否（多端点 + 订阅前事件丢失窗口）。**工具/step 事件、写作业 SSE、WebSocket 均不做。**
- **\[决策P4-7] 复用既有函数、不 fork 业务逻辑：零 LLM 走 report 函数序列化既有 JSON 契约；写（`ingest`）捕获 `run_ingest` 的 stdout+退出码；读/问答走嵌入 `arun` 的返回字符串（无捕获）。** 见 §4.3。可选未来把写路径重构出结构化 `WriteOutcome`，但非 P4 必需——P4 先零改动复用。
- **\[决策P4-8] 所有问答走只读进程内嵌入，默认只读、不过门禁、仅内存。** 嵌入 `Agentao`：① 用 `build_from_environment(working_directory=kb, logger=…)` 而非裸构造（读环境凭据；注入共享 `logger` = 宿主自管日志栈，默认经 `configure_agent_log` 挂一个 file handler 落 `<wd>/agentao.log`，像 CLI 记会话日志，`--no-agent-log` 可关）；② 构造后显式激活 `guanlan-wiki` skill；③ 只用 `build_from_environment`/`.arun`/`transport`（`build_compat_transport` 或 `SdkTransport`）的**文档化嵌入面**，不碰内部 API——三点正对 P2 §4.3 的四坑。**token 流式经构造期冻结的 `transport`**（`EventType.LLM_TEXT`→chunk）：构造后改 `self.agent.llm_text_callback` 是只读存档、运行时不重读（`agent.py:457`），故须在构造时传 `transport=`，每轮用 `Conversation` 上"当前 turn emit"槽转发（`Lock` 串行、单槽无竞态）；又因 `arun` 内部 `run_in_executor` 跑同步 `chat()`，回调落在**线程池线程**，emit 须经 `loop.call_soon_threadsafe` 桥回 server loop 再碰 `asyncio.Queue`（直接跨线程会丢/卡 token）。**`--model` 可选**：仅当非 `None` 才放进 `build_from_environment` 的 overrides——显式 `model=None` 会盖掉 `.env` 发现的模型并触发 `agent.py:397` 的 `ValueError`（默认不带 `--model` 的常路就崩）。**只读姿态在构造后两点同步置位**：`build_from_environment` **无** `permission_mode` 形参（传了会被转发进 `Agentao.__init__` 触发 `TypeError`，C5 直接构造失败），须照搬 `agentao cli/run.py:523-529` 在构造后调 `permission_engine.set_mode(PermissionMode.READ_ONLY)` **且** `tool_runner.set_readonly_mode(True)`——engine 的 read-only 预设为空、真正拦截写/shell 工具的是 `ToolRunner.readonly_mode`，**少调第二步就不是真只读**。结果：只读 wiki、不写、不取 `raw/` 快照、不跑 check。一会话一对象 + `asyncio.Lock`（同会话两轮不并发）；只读会话间可并发、**不**经 §4.2 写 worker，故不破坏单写者假设。**v1 仅内存**：不调 `persist_agent_session`/`load_session`、进程退出即清。可写多轮"工作会话"、会话落盘恢复、把写迁到嵌入都**推后**（§10）。

---

## 10. 不纳入 P4（推后）

- **Web 端写 `raw/`（粘贴存盘 `POST /api/raw`）** → 推后：非 Web 宿主核心，却引入 slug/覆盖/路径校验、与 `raw/` 快照窗口的并发。v1 只 `GET /api/raw` 列文件；投喂仍走文件系统把 `.md` 放进 `raw/`（与 CLI 一致）。**落地小设计见 [`P4.1-Web投喂.md`](P4.1-Web投喂.md)**（零-LLM 半相位：经单写者队列串行避开快照窗口、默认不覆盖、slug + `.md`——已出方案、待实现）。
- **`query --backfill`（gated 写 synthesis）** → 推后：它是 gated 写，不属"浏览 + 问 + ingest"的 v1 核心；CLI 仍有。Web 要回填以后再加一个写端点。
- **可写的多轮"工作会话"（对话中逐轮 gated 写 `wiki/`）** → 推后：每个写轮要包 `raw/` 快照 + check + 自愈、且嵌入 agent 需能切 `workspace-write`，复杂度与风险都高。v1 写只由 `ingest` 覆盖。
- **会话落盘与跨重启恢复** → 推后：v1 会话**仅内存、不调 `save_session`/`load_session`**、进程退出即清。落盘恢复（含 LRU 淘汰）以后再做。**落地小设计见 [`P4.2-会话落盘.md`](P4.2-会话落盘.md)**（零-LLM 半相位：复用 `agentao.embedding.{save_session,load_session,list_sessions,delete_session}` 落 `<kb>/.agentao/sessions/`、id 改 UUID、懒恢复且重走只读两点置位、继承 agentao 10 个轮转上限——已出方案、待实现）。
- **工具/step 进度事件、写作业 SSE** → 推后：v1 流式只在 chat、只 `token`/`done`/`error`；写作业只轮询（决策P4-6）。
- **写作业（`ingest` 子进程）的 token 级流式** → 推后：`agentao run --format json` 飞行中无流可推，写作业只轮询（决策P4-6）；问答的流式需求已由 chat 嵌入路径满足。
- **深色档"夜澜"主题 + 字体打包** → 推后：v1 只交付浅色"昼澜"一档（决策P4-9），深色档与衬线字体随包是可选增强，不为美化扩面。
- **Web 端直接编辑 wiki 页** → **不做**：DESIGN 原则 2"人不直接写 wiki，Agent 全权拥有 wiki 层"。Web 只读 wiki + 经 ingest/会话让 agent 改。
- **多用户并发、鉴权、多租户、远程暴露、TLS** → 企业版 E2（DESIGN §6）。P4 是单用户本地：**只读会话间本就可并发**（§4.4），但**写**仍由单 worker 串行挡住，不引入跨用户的鉴权/隔离。
- **多格式上传摄入（docx/pdf/web clip）** → P5；v1 不经 Web 写 `raw/`，多格式更不在内。
- **交互式图形化 graph（力导向/拖拽/缩放）** → **本文有意收窄**：P3 决策P3-7 曾把"图形化展示整体留待 P4 Web UI"。P4 兑现的是"在浏览器里**看** graph"——直接 serve P3 的自包含静态 `graph.html`（它本就是个网页），已满足浏览需求；而**力导向/拖拽/缩放的交互式渲染**正是"不要过度设计"要避开的部分，按需另做（DESIGN §8 graph 增强），不进 P4 硬承诺。
- **ACP server 模式** → 不做（附录 A 列为非 MVP 备选）。
- **作业持久化 / 历史** → 不做：作业表仅内存、单进程，进程退出即清。

---

## 11. 测试计划（`uv run pytest`，不打真实 LLM）

宿主测试用 `fastapi.testclient.TestClient`（**进程内、无 socket**）+ 临时知识库；**两类 LLM 都打桩，不打真实 LLM**：写路径（`ingest`）**注入 fake runner**（同 P2/P3），问答路径 `monkeypatch` `build_from_environment` 返回一个 **fake agent**，其 `arun` 把 user/assistant 追加进 `messages`，并**经传入的 `transport` 发 `EventType.LLM_TEXT` 事件**（即驱动 `Conversation` 构造时挂的真实 transport 回调链，而非某个事后属性——否则测试会"绿着掩盖" token 不流的真 bug），**且从 `run_in_executor` 的工作线程发**（镜像真 `arun` 的线程模型，逼出"必须 `call_soon_threadsafe` 桥回 loop"——同线程发事件会掩盖跨线程丢/卡 token 的真 bug）。可断言"跨轮累积 + token 流式 + 跨线程不丢"。`web` extra 缺失时整组 `pytest.importorskip("fastapi")` 跳过：

| 测试 | 覆盖 |
|------|------|
| `test_web.py`（静态/浏览） | `GET /` 返回 `index.html`；`/api/pages` 列出非 config 页并按 type 分组；`/api/page` 返回 `{meta,html}`，坏/缺 frontmatter 仍渲染正文（`meta=null`）；`[[wikilink]]` 重写为站内链、断链标灰；**路径穿越**（`?path=../../etc/passwd` 等）→ 409；`/api/raw` 只列文件 |
| `test_web.py`（报告） | `/api/report/check|health|lint` 的 body 与同 fixture 上 `format_report(report, json_output=True)` **字节相等**（端点复用同一序列化器、不漂移；`report_json` 是 `ensure_ascii=False, indent=2`、无尾换行，故须 `Response(..., media_type="application/json")` 而非默认 `JSONResponse`）；`/graph` 触发构建并 302、`graph/graph.html` 落盘；`/graph?json_only` → 302 `/graph/graph.json` 且跳过 html；`/graph/{未知}` → 404；`configure_agent_log` 写 `<kb>/agentao.log` 且**幂等**（重复配置不新增 handler） |
| `test_web.py`（ingest 写作业） | fake runner 下 `POST /api/ingest` → `{job_id}` → **轮询** `GET /api/jobs/{id}` 至 `done`，`exit_code` 与直接 `run_ingest` 一致；写合规 → 0、写阻断性违规 → 3、动 `raw/` → 4、agent 失败 → 5；**两个 ingest 串行完成**（worker FIFO，`raw/` 快照不互踩）；非法请求体 → 422 |
| `test_web.py`（只读多轮 chat） | `POST /api/chat` 无 `conversation_id` → 新建会话并回传 id；**带同一 id 连发两轮 → fake agent 的 `messages` 累积、第二轮能引用第一轮上下文**；流是 `text/event-stream`、事件仅 `token`*→`done`（含 `answer` + 可选 `answer_html` + conversation_id），异常 → `error`（**含 conversation_id**、**无工具事件**）；未知 `conversation_id` → 404；会话数达 `MAX_CONVERSATIONS`（测试 monkeypatch 调小）→ 新建 `503`；token 经**构造期传入的 `transport`** 流出（断言 `build_from_environment` 收到 `transport=`，**非**事后赋 `llm_text_callback`）；**省略 `--model` 时不向 `build_from_environment` 传 `model`**（断言 kwargs 无 `model` 键，绝非 `model=None`——后者会盖掉 `.env` 模型并触发 `agent.py:397` 的 `ValueError`）；fake agent 从工作线程发 `LLM_TEXT`、token 仍完整到达（验跨线程桥）；`build_from_environment` 以 `working_directory=kb`、自带共享 `logger`（日志落点由 `configure_agent_log` 决定）被调用（**不**传 `permission_mode`），构造后 `permission_engine.set_mode(READ_ONLY)` **与** `tool_runner.set_readonly_mode(True)` 均被调用（缺任一断言失败），且 `guanlan-wiki` 被激活；同一会话两轮被 `asyncio.Lock` 串行；会话**不触** `raw/` 快照 / check；**不调** `persist_agent_session`/`load_session`（仅内存）；`DELETE /api/conversations/{id}` 丢弃内存对象 |
| `test_web.py`（安全/形态） | `serve` 仅以 `host="127.0.0.1"` 起 uvicorn；非知识库根 / 缺 `web` extra → `EXIT_USAGE(1)`；**无 `/api/query`、无 `POST /api/raw`、无 `/api/jobs/{id}/events`**（请求 → 404/405） |

**验收标准（P4 Done）**：上述测试全绿；`guanlan web` 起服后，浏览器可浏览 wiki / 跟随 `[[wikilink]]` 导航 / 打开 `graph.html` / 跑 `check`·`health`·`lint` 看报告 / 列 `raw/` 并选一篇触发 `ingest`、**轮询**看到触及页摘要与门禁结果 / **与 agent 只读多轮对话**（`POST /api/chat` 用 `fetch` 流式 `token`/`done`/`error`、追问保留上下文，会话进程退出即清）；全程 `wiki/` 仅由 `ingest` 作业按 P2 门禁改动、`raw/` 不被修改（**会话与浏览全只读**）；进程仅监听 `127.0.0.1`；界面呈"水墨 + 澜"配色（决策P4-9）。技术路线即：**FastAPI 薄 HTTP + vanilla 前端（观澜配色）+ ingest 单 worker + chat 只读嵌入**——无 raw 写、无 `/api/query`、无写作业 SSE、无会话持久化、无工具事件。

---

## 12. 提交清单（Commit Checklist）

P4 面比 P2/P3 大（新增整个 `guanlan/web/` 子包 + 写 worker + 只读嵌入 + 前端），故不按仓库"一特性一提交"的旧节奏一锤子落，而**拆成 7 个自底向上、每个都测试全绿、可独立 review 的薄片**。依赖顺序即提交顺序：**先骨架与降级（无 LLM）→ 读 → 零 LLM 报告 → 写作业 → 只读问答 → 前端 → 文档**。每片附**交付物**（对应 §3 模块表）、**验证**（对应 §11 测试组 + 可离线命令）、**门**（合并前必须为真）。提交信息沿用仓库前缀风格（`P4 Web 宿主 (n/7)：…`）。

> **交付状态**：7 片已全部落地并合入（PR #2）。C7 之后又有一轮**不扩范围**的细化（仍在 P4 边界内、本文 §2–§11 已据此更新）：① 前端布局由初版"侧栏页面树/主区/对话区"三栏重排为**对话(左)/Wiki 搜索+内容(右)/底部满宽输入**两栏 + 可拖动列分隔，右栏并入 Home/前后导航与 Wiki Search（合并视图）；② 对话答案改走**安全 markdown 富渲染**（`done.answer_html`），并对源出处的行内 `<code>`/`[[别名]]` 做兜底联链；③ 会话日志落 `<kb>/agentao.log`（`--no-agent-log` 可关）+ 日志栈隔离（`propagate=False`，防串进 ingest 结果面板）；④ 前端交互补回车发送 / Option-Enter 换行 / in-flight 守卫；⑤ 后端修复（渲染降级不毁答案、graph 错误归因 500、断开 turn 用 `asyncio.shield` 收尾、会话去重/`closed` 复检）。下表的 C1–C7 为初版交付留档，**当前实现以 §2–§11 为准**。

| # | 提交（subject） | 交付物（§3） | 验证（§11 / 命令） | 门 |
|---|---|---|---|---|
| **C1** | `P4 Web 宿主 (1/7)：web extra + CLI 骨架 + serve` | `pyproject.toml`（`[web]` extra + 静态 `force-include`）、`cli.py`（`web` 子命令）、`web/server.py`（`serve`）、`web/__init__.py`、`web/app.py`（仅 `GET /` + 静态挂载）、**`web/static/index.html` 最小占位**（仅为让 `StaticFiles(directory=…)` 挂载成立、`force-include` 源路径存在；C6 整体替换为完整前端） | `test_web.py（安全/形态）`：`serve` 仅 `host="127.0.0.1"`/`workers=1`；非库根→`EXIT_USAGE(1)`；缺 extra（monkeypatch import 失败）→`EXIT_USAGE(1)` 引导 `pip install 'guanlan-wiki[web]'`；端口占用→`EXIT_USAGE(1)`。`uv run guanlan init /tmp/demo && uv run guanlan -C /tmp/demo web --no-browser` 能起停（须**先 init**——`require_kb_root(writable=True)` 前置，未初始化的目录起服应 `EXIT_USAGE(1)`） | 缺 extra 优雅降级；`require_kb_root(writable=True)` 前置；**未引前端框架**；`workers=1` 硬编码；**静态目录非空即可挂载**（C1 不留空目录，否则 `StaticFiles`/打包失败）（决策P4-2/P4-5） |
| **C2** | `P4 Web 宿主 (2/7)：render + 浏览读端点` | `web/render.py`（`render_page` + `[[wikilink]]` 重写复用 `pages.WIKILINK_RE`/`link_stem`）、`app.py` 加 `GET /api/pages`·`/api/page`·`/api/raw` + 路径穿越校验 | `test_web.py（静态/浏览）`：`/api/pages` 排除 config 并按 type 分组；`/api/page` 返 `{meta,html}`，坏/缺 frontmatter→`meta=null` 仍渲染正文；`[[wikilink]]`→站内链、断链标灰；**`?path=../../etc/passwd`→409**；`/api/raw` 只列不写 | 读参数一律 `resolve()` 落 `wiki/` 内否则 409；缺 `markdown` 回退 `<pre>`（**不**硬依赖）；wikilink 解析与 `check`/`graph` **同一口径**（决策P4-3） |
| **C3** | `P4 Web 宿主 (3/7)：零 LLM 报告端点` | `app.py` 加 `GET /api/report/{check,health,lint}` + `GET /graph`（`to_thread` 卸载） | `test_web.py（报告）`：三报告 body 与同 fixture 上 `format_report(report, json_output=True)` **字节相等**；`/graph`→302 且 `graph/graph.html` 落盘。`curl` 比对 CLI `--json` | 阻塞调用经 `anyio.to_thread.run_sync` 卸离事件循环；**字节对齐靠复用既有序列化器**——端点须 `Response(format_report(r, json_output=True), media_type="application/json")`（既有 `report_json` 是 `ensure_ascii=False, indent=2`、**无尾换行**），**不可**返回 `dict`/默认 `JSONResponse`（会变 compact + `ensure_ascii=True`、字节不等）；**只序列化既有 `*Report`/`Graph`，零 fork 业务**（决策P4-7） |
| **C4** | `P4 Web 宿主 (4/7)：ingest 单 worker 写作业` | `web/jobs.py`（`queue.Queue` + 单 worker 线程 + `Job`/`enqueue`/`get_job`）、`app.py` 加 `POST /api/ingest` + `GET /api/jobs/{id}` | `test_web.py（ingest 写作业）`：fake runner 下入队→轮询至 `done`，`exit_code` 与直跑 `run_ingest` 一致（0/3/4/5）；**两个 ingest 串行**（FIFO、`raw/` 快照不互踩）；非法体→422；未知 id→404 | 单写者：`redirect_stdout` 捕获**只在单 worker 串行**；`target` 仍过 `_resolve_raw_target`；**无写作业 SSE**（决策P4-5/P4-6） |
| **C5** | `P4 Web 宿主 (5/7)：chat 只读嵌入 + 多轮会话` | `web/chat.py`（`Conversation`：`build_from_environment(transport=build_compat_transport(llm_text_callback=self._on_token))`，`model` **仅在给定时**入 overrides→**构造后** `permission_engine.set_mode(READ_ONLY)`+`tool_runner.set_readonly_mode(True)`；`_on_token`→经 `call_soon_threadsafe` 桥到当前 turn emit 槽；`arun`+`asyncio.Lock`）、`app.py` 加 `POST /api/chat`（`text/event-stream`）+ `GET/DELETE /api/conversations[/{id}]` | `test_web.py（只读多轮 chat）`：无 id→新建并回传；同 id 两轮→fake agent `messages` 累积、第二轮引用第一轮；流仅 `token`*→`done{answer,conversation_id}`/`error`（**无工具事件**）；token 经构造期 `transport` 流出（断言收到 `transport=`、**非**事后赋 `llm_text_callback`）；**省略 `--model` → kwargs 无 `model` 键**（非 `model=None`）；fake agent 从工作线程发 `LLM_TEXT`、token 仍完整到达；构造后 `set_mode(READ_ONLY)` **与** `set_readonly_mode(True)` 均被调用，且激活 `guanlan-wiki`；同会话两轮被 `Lock` 串行；**不触** `raw/` 快照/check；**不调** `persist/load_session`；`DELETE` 丢内存对象 | **token 流式靠构造期 transport**（`build_compat_transport`/`SdkTransport`），事后改 `self.agent.llm_text_callback` 是死代码（`agent.py:457` 只读存档）；**跨线程投递**：`_on_token` 在 `arun` 的 executor 线程跑，须 `loop.call_soon_threadsafe` 桥回 server loop（直接碰 `asyncio.Queue` 丢/卡 token）；**`model` 可选**，仅非 `None` 才入 overrides（`model=None` 会盖 `.env` 模型 → `agent.py:397` `ValueError`）；只读姿态**两点同步置位**（engine 预设为空，真正拦截在 `ToolRunner.readonly_mode`，照搬 `cli/run.py:523-529`）、**不传** `permission_mode` 形参；不过门禁、**仅内存**；只用文档化嵌入面（化解 P2 §4.3 四坑）；`fetch` 流式**不用 `EventSource`**（决策P4-6/P4-8） |
| **C6** | `P4 Web 宿主 (6/7)：vanilla 前端 + 观澜配色` | `web/static/{index.html,app.js,app.css}`（三栏：页面树/单页/对话 + 顶栏动作；`app.css` 落 §6.1 调色板） | 手测验收串（见 §11 验收标准）；`test_web.py（静态/浏览）`断言 `GET /` 返 `index.html`、`/static/*` 命中随包资源 | **无 npm/构建/CDN/第三方运行时**；流式只 `fetch` 读 `response.body`；动效仅一处颜色过渡；先交付浅色"昼澜"一档（决策P4-3/P4-9） |
| **C7** | `docs: README 更新至 P4 状态 + CLAUDE.md 命令` | `README.md`、`CLAUDE.md`（补 `guanlan web` 与状态行）、本文如有微调 | `uv run pytest` 全绿；README 命令可照抄跑通 | 文档与代码一致；标注 web 为**可选叠加层**、需 `guanlan-wiki[web]` |

**每片合并前的提交前自检（invariant 红线，逐项为真才提交）**：

- [ ] `uv run pytest` 全绿；新端点都有对应 §11 测试组覆盖（无"裸提交未测端点"）。
- [ ] **`workers=1` + 仅 `127.0.0.1`**：无 `0.0.0.0`、无多 worker（否则内存作业/会话表分裂 + `raw/` 快照被并发写互踩，决策P4-2/P4-5）。
- [ ] **`raw/` 全程未被 Web 写**；`wiki/` 只可能被 `ingest` 作业按 P2 门禁改动——浏览/报告/chat **全只读**。
- [ ] **零业务 fork**：报告只序列化既有 `*Report`/`Graph`；写只捕获 `run_ingest` stdout+退出码；读/问答只取 `arun` 返回值（决策P4-7）。
- [ ] **阻塞不入事件循环**：零 LLM 报告走 `to_thread`，写作业走单 worker 线程，`redirect_stdout` 捕获只单 worker 串行。
- [ ] **缺 `web` extra 优雅降级**：`guanlan web` 明确报错引导安装；缺 `markdown` 回退 `<pre>` 仍可跑。
- [ ] **路径穿越**：`path`/`name` `resolve()` 后落 `wiki/`（读）或 `raw/`（写）内，否则 409。
- [ ] **嵌入只碰文档化面**：`build_from_environment`/`.arun`/**构造期 `transport`**（`build_compat_transport`/`SdkTransport`）；token 流靠该 transport（`EventType.LLM_TEXT`），**不**事后改 `self.agent.llm_text_callback`（只读存档、不会流）；显式激活 `guanlan-wiki`；自带共享 `logger`（默认 `configure_agent_log` 挂一个 file handler 落 `<wd>/agentao.log`，像 CLI；`--no-agent-log` 关）；**仅内存、不调** `persist/load_session`。
- [ ] **只读姿态两点同步**：**不**传 `permission_mode`（非 `build_from_environment` 形参）；构造后 `permission_engine.set_mode(READ_ONLY)` **且** `tool_runner.set_readonly_mode(True)` 均置位（engine 预设为空、真拦截在 `ToolRunner.readonly_mode`，照搬 `cli/run.py:523-529`）。
- [ ] **`model` 可选不塞 `None`**：仅当 `--model`/字段非 `None` 才入 `build_from_environment` overrides（`model=None` 盖掉 `.env` 模型 → `agent.py:397` `ValueError`，默认路径必崩）。
- [ ] **token 跨线程投递**：`_on_token` 在 `arun` 的 executor 线程跑，emit 经 `loop.call_soon_threadsafe` 桥回 server loop 再碰 `asyncio.Queue`（绝不在线程池线程直接 `put_nowait`）。
- [ ] **未引前端框架**（无 npm/构建/CDN）；未新增退出码（P4 只用 `EXIT_OK`/`EXIT_USAGE`，决策P4 §7）。
- [ ] **未改 P2/P3 既有模块行为**（§3：至多加一处可选结构化返回，且 v1 不必需）。
- [ ] 范围未越界：**无 `POST /api/raw`、无 `/api/query`、无写作业 SSE、无 `/events` 订阅端点、无会话落盘、无工具/step 事件**（§10）。
