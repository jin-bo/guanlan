# gbrain v0.42.55–v0.42.58 反向评审（backlog）

> 状态：**反向评审结论，非排期项**。键定 **2026-07-11 pull**（fast-forward `814258dd→a25209bb`，**v0.42.53→v0.42.58**，4 个 commit / 4 个版本 v0.42.55·56·57·58；v0.42.54 因 #2399 版本号撞车被跳）。**不改变现状，只借形状、不借实现。**
>
> 项目：`garrytan/gbrain`（TypeScript / Bun，Postgres/PGLite + 持久向量索引 + durable 多相位后台 daemon 的生产级"第二大脑"；同 Karpathy LLM-wiki 家族的重路线对照实现）。
>
> 关联：[`gbrain-反向评审结论.md`](gbrain-反向评审结论.md)（feature-borrow 主线，§11 上次增量 `4ee530f3→9bf96db8`）、[`gbrain-v0.42.53-反向审计-guanlan缺陷.md`](gbrain-v0.42.53-反向审计-guanlan缺陷.md)（把 gbrain 修的 bug 当探针审己——本篇 §1/§2 沿用其"审己"视角）、[`swarmvault-反向评审.md`](swarmvault-反向评审.md) / [`llm_wiki-反向评审-v0.6.md`](llm_wiki-反向评审-v0.6.md)（同类同期先例）、[`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md)、[`../../P4.10-MCP宿主.md`](../../P4.10-MCP宿主.md)、[`../../P3.7-语义审计.md`](../../P3.7-语义审计.md)、[`../../P5.0-检索层.md`](../../P5.0-检索层.md)。

## 0. 一句话

四个版本里**唯一穿透到观澜的真缺口**是 v0.42.58 `#1249 空 env 撞空`——而观澜是**在 Claude Code 下开发/运行**的头号受害者，对应**一项立即动作（§2）**。旗舰 feature「Life Chronicle 双时态本体」的核心区分（时序更替 vs 并存对立）**观澜 `conventions.md` 早有等价甚至更细的约定**；其余（slug 封控、RLS/schema-lint 迁移、pglite 活锁、base-url/embedding dims）要么**已被更强覆盖**、要么**架构不同构 N/A**（§4）。

## 1. 新增缺口（真实的，1 个）

**`ANTHROPIC_API_KEY=''` 撞空 —— gbrain `#1249`，在观澜依赖的本地 agentao 上真实成立。**

gbrain 原话：*"Claude Code injects `ANTHROPIC_API_KEY=''` to neuter subprocess LLM calls; an unconditional `process.env` spread let that empty string override a valid config key."* 用它当探针，把链条在观澜侧核到底：

- **观澜自身无撞空点**——LLM 全权委托 agentao（不变量：脚本零 LLM、无 API key）。✅
- 但观澜依赖的**本地 agentao 有该缺陷**，两处叠加：
  - `agentao/embedding/factory.py:97` 收 key 只判 `is not None`（`''` 照收进 `out["api_key"]`），**与它自己 88–90 行 docstring 直接矛盾**（docstring 声称"空/纯空白视作未设、静默跳过"，代码没实现）；
  - `agentao/_env.py:35` 的 `safe_load_dotenv` 用 **`os.environ.setdefault`（no-override）**——注入的 `''` 已在 `os.environ` 里，**`.env` 里的真 key 盖不进去**。
- **观澜两条 LLM 路径都过这个接缝**：
  - `runtime._subprocess_runner`（[`guanlan/runtime.py`](../../../guanlan/runtime.py) `subprocess.run` **不传 `env=`**，全量继承父环境 → `agentao run` 子进程；ACP 侧 agentao `session_set_config_option.py:104` `if not api_key: raise LookupError`）；
  - **Web 聊天进程内** `chat.build_from_environment`（[`guanlan/web/conversation.py`](../../../guanlan/web/conversation.py)`:244`，直接吃观澜自己带毒的 `os.environ`）。
- **触发面精确**：**配置 provider 是 Anthropic**（用 Claude 模型的常态）**且从 Claude Code 会话里跑 `uv run guanlan web / ingest / query`** 时，`.env` 有真 key 却报"无 key / 空 key"失败。OpenAI provider 不受影响（Claude Code 不注入 `OPENAI_API_KEY=''`）。这也是 [`gbrain-v0.42.53-反向审计`](gbrain-v0.42.53-反向审计-guanlan缺陷.md) 那类"毒值容错"探针的直接续篇——只是这次毒值来自**外部注入的空 env**，而非页内容。

## 2. 最小动作（立即做，1 项）

**堵空 env 撞空。首选修 agentao 根因（本地 `../agentao` 可编辑装，改动即时生效），观澜侧接缝 scrub 作可选纵深防御。**

1. **根因修复（agentao，1 行，首选）**：`factory.py:97` 兑现自己的 docstring——
   `if (v := os.getenv(f"{provider}_API_KEY")) is not None and v.strip():`
   一处修好，进程内 `build_from_environment`（Web 聊天）与经它装配的所有面同时受益。
2. **观澜侧纵深防御（可选，不破不变量）**：`_subprocess_runner` 显式传 `env=` 并剔除**毒空串** `ANTHROPIC_API_KEY`/`{PROVIDER}_API_KEY==''`；Web 进程内 `build_from_environment` 前 `os.environ.pop` 同名空串。**硬约束**：只删空串、**绝不注入或读取任何真 key**（守"wrapper 不持 API key"不变量——删一个毒空值 ≠ 持钥）。与该接缝已有的 `stdin=DEVNULL` 同类（"喂给子进程前的环境清洗"），有先例。

> 判断：根因在 agentao、观澜是唯一实际被咬的消费者。只想动一处 → 修 agentao `factory.py:97`；要对**发布版 agentao** 也免疫 → 叠观澜接缝 scrub。

## 3. 仅观察（不排期）

- **矛盾状态流转 lint 的 `valid_until IS NULL` 判据**：gbrain 有个 pre-landing bug——正常前向更替（founder→advisor）被误报成 live conflict，修法是 `findOntologyConflicts` **只看两行都 open（`valid_until IS NULL`）** 才算冲突。观澜现刻意把矛盾放**语义层**、确定性 lint 不统计（`conventions.md:149`）；**若日后做"矛盾 open→resolved 状态流转的语义 lint"**，借这个判据：只有两个 `open` 的对立才算 live，`时序变化`/`resolved` 不再 flag。先不做。
- **确定性 eval 当 North-Star**：gbrain `eval chronicle` 自带内存库、零 LLM、埋一个 supersession + 一个 conflict、6 个 gold 任务、`exit 0 iff all pass` gate CI。观澜可为**检索/摄入质量**建同款合成语料 eval（现只有 pytest 单测 + `tests/test_search_quality.py`，无"埋信号打分"的端到端 eval）。新件、非本次 pull 必做。
- **`orient` 式预算上下文装载**：gbrain `volunteer_chronicle`（Life Chronicle B.12）零 LLM 把"最近时间线 + 当前本体"一次性交给 agent 先定向再动作——正是 Karpathy LLM-wiki"预算上下文替代每次 RAG"论点，也承 [`gbrain-反向评审结论.md`](gbrain-反向评审结论.md) §11.1 的 push-based `volunteer_context`。契合观澜气质，但是 feature 不是 fix。

## 4. 不借 / 已覆盖（附理由）

- **slug/路径封控（v0.42.55 `#419/#245` `validateSlug` + `isWriteTargetContained`）—— 已覆盖，且更强**：`rawio.normalize_basename`（NFKC + 混淆字符折叠 + **剥目录成分防 `../`**）是投喂 / `convert` / `remove` 共用的**写口 chokepoint**——**归一**而非仅拒绝；[`guanlan/imageio.py`](../../../guanlan/imageio.py) `_admit_image_ref` 五条 `realpath`+`relative_to(root)` 闸拒 symlink 逃逸；[`guanlan/check.py`](../../../guanlan/check.py)`:130` 读时 slug 守卫。gbrain 拒 NUL/bidi/URL-encoded-sep，观澜走归一化覆盖同面。**唯一缝**：Web 若收 `%2f` 类 URL 编码 slug，NFKC 不解码——但 CLI slug 走 `.stem` 无 URL 层，风险面仅 web，核 [`guanlan/web/uploads.py`](../../../guanlan/web/uploads.py) 归口即可（**仅观察，不排期**）。
- **双时态 supersession vs contradiction（Life Chronicle B.10/B.11）—— 已有等价，且更细**：`conventions.md:143–147` 早有 `时序变化 / 数据冲突 / 解释分歧` + `open/resolved`——正是 gbrain 那个区分（`时序变化` = gbrain 的前向 supersession，非 live conflict）。**架构不同构**：观澜矛盾在语义层 + markdown，gbrain 在 DB 双时态；观澜"知识变了"的答案是**重建派生物 + `query --backfill`**（`conventions.md:222`），不是 valid-time DB。别照搬 DB 层。
- **DCR consent 默认（v0.42.55 `#1353`）—— N/A**：观澜 `mcp` 只读 stdio/HTTP + 单 token（[`guanlan/mcp/server.py`](../../../guanlan/mcp/server.py)`:365`），无动态客户端注册（DCR）面。
- **Postgres RLS / `search_path` / schema-lint 迁移（v0.42.55 `#171/#1385`）—— N/A 无 DB**：markdown 唯一事实，index/graph 皆可幂等重建的派生物，无 RLS / 视图 / 迁移。
- **pglite 活锁不偷（v0.42.57 `#2348`）—— N/A 无 DB/单写锁**：观澜是文件 / git 可合并模型，无 WASM 单写者；`runtime._progress_heartbeat` 是父侧 stderr 心跳、非锁。（元教训"长同步阻塞让心跳失真→误判 dead"观澜无对应件：无锁可被骗。）
- **provider base-URL `/v1` 归一 + embedding dims trust（v0.42.58 `#1250/#1292/#2271`）—— N/A**：观澜检索是**零 LLM BM25 + CJK 2-gram**（无 embedding/dims，见 [`cjk-retrieval-enhancements.md`](cjk-retrieval-enhancements.md)）；LLM base-url/model 全委托 agentao 子进程（不变量：脚本零 LLM、无 API key）。**唯 `#1249` 的空 env 撞空穿透到观澜**（→ §1/§2）。
- **parse-barrier 部分写 bug（Life Chronicle A.3 + pre-landing 修复）—— 已覆盖**：gbrain 的坑是"长度校验过了、写了 event 页、再在 `::date` cast 抛 → 部分写"。观澜 `convert`「图先换、md 末步 `atomic_write_raw` 提交」（决策P5.2.1-9）+ TOCTOU 复检 + gate raw 快照，多文件写已是全或无形状。

---

**收口**：本次 pull 对观澜的净增量就是 **§2 那一项**（空 env 撞空，且正因在 Claude Code 里跑而现实命中）。Life Chronicle 看着大，可借的"形状"观澜早在 `conventions.md` 以更贴合 markdown 的方式落过了。
