# gbrain v0.42.52/53 反向审计：观澜自身缺陷（backlog）

> 状态：**反向「审计」结论** —— 与既有 feature-borrow 评审（借不借功能）**性质不同**：这次把 gbrain
> 今天 pull 修的每一类 bug 当**探针**，反向去观澜自己的代码里查同构缺陷，钓出**真实潜在缺陷**。
> 键定 pull `9bf96db8→814258dd`（**v0.42.52/53**，仅 2 commit：autopilot 死任务风暴 / supervisor 楔死 /
> sync·status·minion 可靠性 + `op_checkpoints` jsonb 双编码致每次 sync 中止 + 全仓同类清扫 + CI 静态守卫）。
> 本次 pull **90% 是 gbrain 重型架构（Postgres / 守护进程 supervisor·minion 队列 / autopilot 扇出）的运维
> 修复，对观澜（薄壳 CLI + markdown，无 DB / 无 daemon / 无队列 / 无扇出）架构上不适用**；探针价值不在
> 借功能，而在审出观澜同构缺陷。
>
> **方法**：4 个对抗式审计 agent 各盯一类 gbrain bug（续跑循环死任务·计次 / convert 真后端契约 /
> 原子写·毒值容错 / 诚实状态·路径作用域），只认 `file:line` 代码证据 + 可复现场景。
>
> 关联：[`gbrain-反向评审结论.md`](gbrain-反向评审结论.md)（feature-borrow 主线，§11 为上一次增量评审 `4ee530f3→9bf96db8`；本篇为其 §12 后继，但转入「审己」视角）、同类反向评审先例 [`openkb-反向评审结论.md`](openkb-反向评审结论.md) / [`sag-反向评审结论.md`](sag-反向评审结论.md) / [`nashsu-llm_wiki-反向评审结论.md`](nashsu-llm_wiki-反向评审结论.md)、P4.16（续跑循环）、P5.2（convert）。

> **code-review 跟进（同会话 `/code-review --fix` xhigh）**：对本轮 ②③ 改动跑了一遍 workflow 版多 agent
> 审查 + 对抗验证。结论:**③ 方向错误已回退**（强制 UTF-8 反而打断 matched-locale 中文路径往返 + stderr
> surrogateescape 漏孤 surrogate 把 Web 端点 500，§1.③）;**② 不完整已加固**——同类毒值在**会话快照读路径**
> （`ConversationStore.list`/`restore`/`messages_for` → agentao `list_sessions`/`load_session`）仍 500 整个
> 侧栏（新 `_safe_list_sessions` 容毒），且 `_prune` 还漏**坏 UTF-8 字节**（`UnicodeDecodeError`，已并入 catch）。

## 0. 处置总览

| # | 缺陷 | 命中的 gbrain 类 | 证据 | 触发频率 | 处置 |
|---|---|---|---|---|---|
| **②** | 毒会话状态文件（非对象 JSON / 坏 UTF-8 字节）漏 `AttributeError`/`UnicodeDecodeError` → 会话或**整个侧栏**永久 500 | #2339 毒值崩整循环 | `goal_io.py` + `chat_support.py` + `conversation_store.py` | 需手改/半写状态文件，impact 高 | ✅ **本次已修 + 回归测试**（code-review 加固 3 条读路径，见 §1.②） |
| **③** | convert 子进程**强制 UTF-8 解码** | —（自伤） | `convert.py` | — | ↩️ **code-review 判错、已回退**；真修法 → backlog §2.4b |
| **①** | 续跑循环**零前向进展感知**：只答不做的空转 agent 烧满 25 轮/120min | #1950 进展感知停止 + #2194 死任务风暴 | `conversation.py:1017-1085` | **常态**（LLM 天然"我还在看…"） | 🅿️ backlog【最高优先】 |
| **④** | convert 子进程**无超时** → 大/卡死后端无限阻塞 | robustness（同类） | `convert.py:104,126` | 后端卡死 | 🅿️ backlog |
| **⑤** | 续跑**路径 D（轮内异常）漏计墙钟时间** | #1737 计次一致性 | `conversation.py:1086-1088` | 异常→resume 循环 | 🅿️ backlog |
| **⑥** | convert **真后端 parity 测试缺失**（pypdf 真子进程 + `LANG` 变体） | v0.42.53 测试替身盲区 | `tests/test_convert.py`（两层全 mock） | —（测试覆盖） | 🅿️ backlog（③ 的真验证） |
| **⑦** | 低危一致性批（非原子写 / `goal_io` 固定 tmp 名 / index·log 读缺 `errors=replace`） | #2339 边缘 | 见 §2.5 | 窄 | 🅿️ park |

**设计上安全（旁证，不排期）**：状态诚实（`idle-while-running` 已堵死）、`-C` 路径作用域、convert cwd=KB 根、
`raw/` 只读不变量、单写者 single-flight —— gbrain 的坑观澜已结构性做对，详 §3。

---

## 1. ✅ 本次已修（留档）

### ② `read_goal` / `_prune_old_snapshots` 漏 `AttributeError`（毒值崩循环同构）

- **根因**：`goal_io.py:read_goal` 的 catch 元组 `(OSError, JSONDecodeError, ValueError, TypeError)` **漏
  `AttributeError`**。合法但**非对象**的 sidecar（`null`/`[]`/`"x"`/`42`）使 agentao `GoalState.from_dict`
  首行 `data.items()` 抛 `AttributeError`，逃逸 catch。venv 实测：`null/[]/"x"/42 → AttributeError`，dict 正常。
- **崩溃路径**：`Conversation.__init__`(`conversation.py:201`) → `ConversationStore.restore()`
  (`conversation_store.py:296`，构造**无 try/except 兜底**) → 端点**每次 500**，会话永久无法恢复/自省，违
  `read_goal` docstring「退化为无目标，绝不载入毒值」承诺。姊妹缺陷 `_prune_old_snapshots`
  (`chat_support.py:248`，catch 漏 `AttributeError`) 在**每轮 save 前**跑(`conversation.py:1111`)，
  `.agentao/sessions/` 任一兄弟文件是 `[]` 即崩在 save —— 正是 gbrain「整循环崩在 checkpoint 写」的形状。
- **修法（已落）**：`read_goal` + `_prune` 两处加 `isinstance(data, dict)` 守卫（口径同 `runtime.py` 解析 stdout）。
- **code-review 加固（同会话补，原修不完整）**：同类毒值在**会话快照读路径**仍 500——agentao `list_sessions`
  per-file 对非对象做 `.get` 抛 `AttributeError`、对坏 UTF-8 字节抛 `UnicodeDecodeError`，其 `except (IOError,
  JSONDecodeError)` 都不接，逃逸把 `GET /api/conversations`（**整个侧栏**，`conversation_store.list` 裸迭代无
  guard）与冷会话 `restore`/`messages_for`（经 `_disk_session` 同样裸迭代）打成 500。venv 实测 `list_sessions`
  对 `[]` 抛 `AttributeError`。加固：①新增 `ConversationStore._safe_list_sessions` 包装（毒快照→空盘 catalog、
  降级不 500），`list`/`_disk_session` 改走它;②两处 `load_session` catch 补 `ValueError/TypeError/AttributeError`
  做 race 兜底;③`_prune` catch 并入 `UnicodeDecodeError`（坏字节孪生）。降级偏粗（一份坏文件隐去全部冷会话），
  细粒度 per-file 跳过须 agentao 侧支持 → 见 §2.6。
- **测试（已落）**：`test_read_goal_tolerates_non_object_json`、`test_poison_goal_sidecar_degrades_cold_info_not_500`、
  `test_prune_old_snapshots_tolerates_non_dict_session`、`test_prune_old_snapshots_tolerates_bad_utf8_bytes`、
  `test_poison_session_snapshot_degrades_list_not_500`。

### ③ convert 子进程强制 UTF-8 解码 —— ↩️ 已回退（code-review 判错）

- **原以为**：`_run_converter` 用 `text=True` 无 `encoding=` → 非 UTF-8 locale 下中文产物路径解码失败，遂强制
  `encoding="utf-8", errors="surrogateescape"`。
- **code-review 推翻（CONFIRMED）**：skill 子进程 `print(out)`（`skills/.../convert.py:341`）**裸 print、随 locale
  编码**。matched-locale（父子同 `LANG=zh_CN.GBK`）下，旧 `text=True` 父子同用 GBK → 中文路径**逐字往返、本来
  就对**;强制父端 UTF-8 反而把子进程发来的 GBK 字节强解成乱码 → `produced.is_file()` False → `ConvertError`，
  **回归了它声称要修的那个 locale**。且 stderr 用 `errors="surrogateescape"` 会把后端日志的非法字节解成**孤
  surrogate**，经 `progress→job.output→JSONResponse` 的 `json.dumps(...).encode("utf-8")` 抛 `UnicodeEncodeError`
  → Web 解析端点 500。另:原注释「与下游 `produced.read_text` 口径一致」**事实错误**——`read_text` 用的是
  `errors="replace"` 不是 `surrogateescape`。
- **已回退**：`_run_converter` 维持 `text=True` locale-faithful;原 ③ 那条真子进程测试（locale-coupled、§5 指其
  自相矛盾）一并删除。
- **真正的修法（→ backlog §2.4b）**:见下。原 agent 设想的「误判失败」其实只在「文件名含 locale 编码**无法表示**的
  字符」（emoji/生僻字）时触发,且失败在**子进程 encode 端**,父端改 encoding 根本够不着——须两端协同。

---

## 2. 🅿️ Backlog（按优先级）

### ① 续跑循环加无前向进展探测【最高优先 —— 真缺口，常态触发】

- **缺陷**：`run_goal`(`conversation.py:1017-1085`) 的**全部**停止条件只有：会话被删 / `not is_active` /
  `budget_tripped()`（时间或轮数撞顶）/ `done`（agent 自调 `update_goal`）。`conversation.py:1065` 拿到
  `answer` 后**对内容零检查**。一个「每轮答非空、从不调 `update_goal`、零 wiki 改动」的空转 agent 会**烧满
  默认 25 轮真实 LLM 往返**（或 120min 墙钟）才停 —— gbrain「楔死循环只能人工杀」的同构。
- **信号已在手却被弃用**：判无进展所需信号（每可写轮收尾的写日志 / `turn_meta` 里 `undo` / `check.resolved`，
  `conversation.py:617-703` 已算）在 `conversation.py:1073` 只被摊进 SSE 帧、**从不回读**。
- **落法（小，纯加法）**：用收尾的 `turn_meta` 维护 `_stall_count` —— 形如
  `if not turn_meta.get("undo") and turn_meta.get("check",{}).get("resolved",0)==0: stall+=1 else stall=0`；
  连续 N（如 3）轮零进展 → `g.mark_blocked()`（带「无前向进展」摘要）+ break。
  注意 read-only 分析型目标 `guarded=False`、不进 `_finalize_writable`，无写信号，只能靠 update_goal，更脆 ——
  对其可退而用「答案 hash 不变」判稳态。
- **为何不本次做**：改续跑核心控制流 + 引一个新计数状态 + 须配 N 的阈值取舍与测试，面比 ②③ 大；值得单独排
  一个小 P4.x 半相。

### ④ convert 子进程加超时

- **缺陷**：`convert.py:104` `subprocess.run`、`:126` `proc.wait()` 均**无 `timeout=`**。真 mineru 跑大 PDF /
  卡死 → `guanlan convert` 无限阻塞；mock 秒返回完全掩盖。
- **落法**：给 `subprocess.run` 加 `timeout=`、给 Popen 路径用 `proc.wait(timeout=...)`，捕 `TimeoutExpired` →
  `ConvertError`（`with tempfile.TemporaryDirectory` 块仍保证 tmp 清理）。超时值可给保守默认 + 可配。

### ④b convert 编码的**正确**修法（接替已回退的 ③）

- **真实缺陷范围**（比原 ③ 窄）：仅当**文件名含当前 locale 编码无法表示的字符**（emoji / 生僻字，GBK 下）时，
  skill 子进程 `print(out)` 在 **encode 端**抛 `UnicodeEncodeError` → 子进程崩 → `ConvertError`（fail-closed
  但把可转文件误判失败）。matched-locale 下的普通中文路径**本来就对**（旧 `text=True` 父子同 locale 往返）。
- **为何父端改 encoding 不行**（已被 code-review 证伪）：失败在子进程 encode、父端 decode 够不着;且父端强制
  UTF-8 会打断 matched-locale 往返 + stderr surrogateescape 漏孤 surrogate 500 Web 端点。
- **正确修法（两端协同）**：①给子进程 `env` 注入 `PYTHONIOENCODING=utf-8`（或 `PYTHONUTF8=1`），令 skill
  `print(out)` 无论 locale 都发 **UTF-8** 字节（顺带消灭 encode 端 `UnicodeEncodeError`）;②父端 stdout 配
  `encoding="utf-8"` 对齐;③父端 **stderr 单独用 `errors="replace"`**（日志只供显示，绝不能 surrogateescape
  漏孤 surrogate 进 `job.output`）——因 `subprocess.run(capture_output=True)` 两管道共用一套 `errors`，须改用
  Popen + 各自 `TextIOWrapper`，或 stderr 走二进制后 `decode(errors="replace")`。注意 env 注入须保留全量继承
  （决策P5.2-4 不 env-scrub，`test:136` 校验）→ 用 `{**os.environ, "PYTHONIOENCODING": "utf-8"}`。
- **验证**：必须配 ⑥ 的 `LANG=zh_CN.GBK` 整进程 parity 测试才能真证（进程内 monkeypatch 不触发 C 层 locale）。
  **没有该测试前不要再改**——本轮正是无此验证才误判方向。

### ⑤ 续跑路径 D 计时对齐

- **缺陷**：正常 / `AgentCancelledError` / 断线三条路径都计了墙钟（`conversation.py:1067/1048/1056`，后两条注释
  明写「否则反复 stop/resume 让 `--for` 永不 trip」），**唯独轮内异常路径** `conversation.py:1086-1088` 只
  `_pause_active_goal()`、丢弃 `clock()-t0`。某工具每轮稳定抛异常 → `/goal resume` 循环系统性低估 `--for`。
- **落法**：`except Exception` 里先 `with self._goal_lock: g.time_used_seconds += self._clock()-t0` 再
  `_pause_active_goal()`，与 B/C 对齐。次要（默认 120min 时间轴仍兜底），但口径应一致。

### ⑥ convert 真后端 parity 测试（③ 的真验证 + 一网打尽 mock 盲区）

- gbrain v0.42.53 的标准回应是「`DATABASE_URL` 门控的真后端 parity 测试 + 专门 CI job」。观澜对应物：一个
  `pytest.importorskip("pypdf")` 的**真子进程**测试（pypdf 是 CI 唯一可装的确定性后端），转一个**中文文件名**
  的单页真 PDF，断言 `raw/报告.md` 落地 + 正文正确 + tmp 零残留。**一次盖住**四个 mock 盲区：真 argv 编码 /
  真 stdout 管道 locale 解码 / skill `find_markdown` 真嵌套定位 / `run_backend` stdout→stderr 分离契约。
- **再加 `LANG=zh_CN.GBK`/`LANG=C` 参数化变体**（`subprocess` 起 pytest 子进程或 `monkeypatch.setenv` +
  真子进程），**直接复现并钉死 ③** 的 locale 解码缺陷回归（§1.③ 已说明进程内 monkeypatch 不足以触发）。

### ⑦ 低危一致性（park，KB 变大/实测痛点再排）

- **非原子写**：`graph.json`/`graph.html`(`graph.py:429`)、`manifest.json`(`remove.py:242`)、
  `index.md`/frontmatter(`reindex.py:252`/`remove.py:184/195`) 裸 `write_text` —— 与 `raw/`、goal sidecar 的
  `os.replace` 原子写**两套口径**。但都「派生物观澜自身不回读」或「reader 按行/`load_page` 降级 + 命令幂等」，
  低危。最值得盯的是 `index.md` torn write（它是被全家回读的 markdown 真相源），但 reader 已降级。
- **`goal_io` 固定 tmp 名** `<cid>.json.tmp`(`goal_io.py:52`)：跨进程同-cid 窄窗（两个 `guanlan web` 跑同一 kb
  且恰好驱动同一会话 goal）。127.0.0.1 单用户基本不达。改 `tempfile.mkstemp(dir=...)` 唯一名即与
  `rawio`/`uploads`/`policy_fs` 一致。
- **index/log 读缺 `errors="replace"`**：`reindex.py:178`/`remove.py:138`/`audit.py:285`/`heal.py:256` 读
  `index.md`/`log.md` 无 `errors="replace"`，非 UTF-8 配置文件 → `UnicodeDecodeError` traceback 而非干净退出
  （触发面窄：这些是正常 UTF-8 配置）。与 `pages.load_page` 容错口径不一致，可顺手统一。
- **convert produced 越界校验**：`convert.py:182` 只校验 `produced.is_file()`，未校验落在 tmp_root 内（skill 是
  第一方可信，低）。可加 `produced.resolve().relative_to(tmp_root)` 收紧信任边界。

### ⑥b agentao `list_sessions` per-file 容错（上游，细粒度降级前置条件）

- 本轮 `_safe_list_sessions`（§1.②）的降级**偏粗**：一份坏会话快照让**整盘 catalog 归空**（隐去全部冷会话），
  因 agentao `list_sessions`（`agentao/embedding/__init__.py`）的 per-file `except (IOError, json.JSONDecodeError)`
  不接 `AttributeError`/`UnicodeDecodeError`，一份坏文件即中断整个 eager 构建。**细粒度「跳过坏文件、保留其余」**
  须 agentao 侧把 per-file except 拓宽到含非对象/坏字节（或 guanlan 不经 `list_sessions`、自己逐文件读 + 容错）。
  前者是本地可编辑 `../agentao` 的一行改（连同 `load_session`），后者重复造轮子。park 到「冷会话多 + 真出坏
  文件」成痛点再排;当前粗降级已消除 500，够用。

---

## 3. 设计上安全（旁证 —— gbrain 的坑观澜已堵死，不排期）

- **状态诚实**（gbrain `idle-while-running`，#1950a）：`goal_snapshot()` 持锁直读状态机、`_in_goal` 跨整段
  `run_goal` 持有 + `has_active_writable_goal` 专盖「轮间 `active_writable_turns` 归 0」窄缝
  （`app.py:538`、`conversation.py:801/1009/1090`）。两方向都诚实，代码注释逐条点名同构窗口。
- **`-C` 路径作用域**（gbrain 绑错树，#2194）：所有子命令统一归口 `require_kb_root`（`paths.py:56`），无
  `cwd`/`-C` 混用；观澜单根、无 per-source 二元，**结构上无错树可绑**。
- **convert cwd = KB 根**（`convert.py:166,258`，决策P5.2-12）：刻意绑对象自己的根，**正是 gbrain 修复的方向**。
- **`raw/` 只读不变量**：`safe_raw_target`（剥目录 + NFKC + `relative_to` 越界校验）+ `atomic_write_raw`
  overwrite 闸 + `check_text_admission`（空正文拒落）层层兜底，**无 fail-open**。
- **单写者 single-flight**（gbrain cycle-split `idempotency_key + maxWaiting:1`）：`write_lock` + 进程级至多一个
  活跃可写 goal —— 观澜已独立学会这课。

---

## 4. 代码位置速查

| 关注点 | 文件:行 |
|---|---|
| ② read_goal 漏 catch（已修：isinstance 守卫） | `guanlan/web/goal_io.py` |
| ② _prune 漏 catch（已修：isinstance + UnicodeDecodeError） | `guanlan/web/chat_support.py` |
| ② 会话快照读路径容毒（code-review 加固） | `guanlan/web/conversation_store.py`（`_safe_list_sessions` / `list` / `_disk_session` / 两处 `load_session` catch） |
| ③ convert 子进程编码（**已回退** —— 维持 `text=True`；真修法见 §2.4b） | `guanlan/convert.py`（`_run_converter`） |
| ① 续跑停止条件（缺进展感知） | `guanlan/web/conversation.py:1017-1085` |
| ① 现成进展信号（被弃用） | `guanlan/web/conversation.py:617-703`（journal/undo）、`:1073`（弃用点） |
| ④ convert 无超时 | `guanlan/convert.py:104,126` |
| ⑤ 路径 D 漏计时间 | `guanlan/web/conversation.py:1086-1088`（对照 `:1048/1056/1067`） |
| ⑥ convert 测试两层 mock | `tests/test_convert.py:1-4`（_mock_convert / _fake_run） |
| 退出码 | `guanlan/errors.py` |
