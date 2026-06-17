# 确定性 frontmatter 引号修复（消除最常见的写门禁自愈轮，零 LLM）

> 纯性能微改、**不单列里程碑编号**（同 finding 因果排序、P4.11 那种小条目处理）。源自
> 「ingest 性能优化」摸排：写门禁的**有界自愈**是 ingest 耗时的最大单一杠杆，而触发它的
> 违规绝大多数是确定性可修的引号写坏——把这条最高频触发器零-LLM 消掉。

## 0. 缺口

写门禁（`gate.run_guarded_write_result`）每发现一条**本次新引入的阻断性违规**，就把清单回喂
同一 Agent 就地修，**每轮自愈 = 一整个额外的 Agentao 子进程**（冷启动 + LLM 推理，最多 2 轮
→ 最坏 3× LLM 成本）。而触发自愈的阻断性违规里，绝大多数是 **`frontmatter.unparsable`**——
字符串值（尤其 `title`）把引号写坏（双引号套双引号 `title: "他说"你好""` / 坏单引号）导致整块
YAML 解析失败（`pages.parse_frontmatter` 严格档报 fatal）。

这一类**确定性可修**：坏的只是引号转义，意图明确是字符串、可恢复（剥一层外引号 → 改用单引号
重包、内部 `'` 翻倍）。让宿主在 enforce 后、自愈循环前先确定性修一道，**用一次廉价的 Python
重判换掉一整轮 LLM 自愈**。

## 1. 落法

新模块 `guanlan/fmrepair.py`（仿 `provenance.py`：宿主侧专项确定性 frontmatter 操作）。
`repair_unparsable_pages(root, violations)` 对门禁违规集里 `kind=="frontmatter.unparsable"` 的页逐页
`repair_page_frontmatter` —— 严格档确认是 unparsable → `_requote_block` 把**有成对外引号**（首尾同为
`"` 或 `'`）的解析失败标量值剥外引号后改用单引号重包 → 整块复验为 mapping 后 **逐字节 I/O 落盘** →
**返回写前原字节**（供门禁回滚）。**它只负责「能解析为 mapping 就落盘」，不判定该页是否真的全清**。
复用 `pages.split_frontmatter` / `pages.parse_frontmatter`，不另写 YAML 解析。

**「修完是否真省下自愈轮」由门禁裁定——验收判据就是门禁本身**（决不漏校验，见 §2.1）。接入
`gate.run_guarded_write_result`：首次 `enforce_write_result` 后、有界自愈 `while` 前——

```python
if page_guard and gate.kind == "check_failed":
    written = repair_unparsable_pages(root, gate.violations)          # {页: 原字节}
    if written:
        probe = enforce_write_result(root, before, first_result, page_before=page_before, baseline=baseline)
        stuck = {rel for rel in written if any(v.page == rel for v in probe.violations)}
        for rel in stuck:                                             # 仍阻断 → 回滚到原字节
            (Path(root) / rel).write_bytes(written[rel])
        kept = [rel for rel in sorted(written) if rel not in stuck]
        if kept:
            print(f"✓ 确定性修正 {len(kept)} 页 frontmatter 引号（免去自愈轮）", file=sys.stderr)
        gate = enforce_write_result(...) if stuck else probe          # 有回滚才重判，否则 probe 即终态
```

修不动/回滚的残留照样进下面的自愈 `while`。**仅 `page_guard` 路径（ingest / `query --backfill` / audit）
启用**；`page_guard=False` 的 **heal 跳过**——heal 直调本核心、契约是「逐字节不变 + 越界写审计」，不该
被宿主改页，故不参与修复以保其行为不变。

## 2. 边界与安全闸（硬约束）

这让宿主**首次启发式改写 Agent 拥有的 `wiki/` 内容**。当前不变量是「宿主只校验、Agent 才写」，
唯一先例是 `ingest._stamp_source_digest` 盖 `raw_digest`（**追加键**，非**修内容**）。下列闸把风险
压到「**最差等于现状**」：

1. **事务性落盘 + 门禁重判 + 回滚（worst = status quo 的核心闸）**：修引号落盘后，用**真
   `enforce_write_result`**（= 门禁自身）重判，**仍出现在其新阻断违规里的修复页一律回滚到原字节**，
   只保留**真省下自愈轮**的修复、决不留半成品写。**为何让门禁判而非写前逐项预判 / 也非自跑 `run_check`**：
   原页 unparsable 时 meta=None 使首轮门禁**跳过本页的逐页与跨页校验**，被掩盖的不止 `bad_type`
   （`tags: "a"b"`）、`sources.unresolved`、**跨页** `aliases.collides_stem`/`aliases.duplicate`，**还有
   `run_check` 根本看不见的 page_guard 专属 `sources.dropped`（源不回退）**——逐项枚举校验器必漏，连
   `run_check` 都不够（它不含源不回退）。**唯一不漏的判据就是门禁本身**，故验收直接复用 `enforce_write_result`
   的结论（`probe.violations` 已是阻断+增量过滤面，断链作警告不入内、不会误回滚含前向引用 `[[X]]` 的新页）。
   `probe` 反映回滚前状态，有回滚则再 `enforce` 一次得终态、无回滚 `probe` 即终态（省一次门禁扫描）。
2. **只修真正的引号转义 bug（须成对外引号）**：`_requote_block` 仅重写**首尾成对引号**的失败值
   （`"…"` / `'…'`）——剥一层外引号后单引号重包（内部 `'` 翻倍）。首字符是引号但**首尾不成对**
   （`"a` 未闭合、`"a"b" # 注释` 带尾注）不是干净引号 bug，强行重包会把尾注/残文吞进值或退化恢复，
   **留给自愈**；结构性坏值（未闭合 `[a, b`、含冒号串）也不动。已合法的值（`_value_parses` 为真）
   一律不碰——守住「合法行逐字节不变」。与门禁重判回滚双保险。
3. **作用面只取本次新引入的 unparsable 页**：违规集来自门禁结论（已是「本次新引入的阻断性」违规），
   baseline 里早就坏的页不动；缺键/坏类型/缺块等**非引号**问题（`fatal.kind != frontmatter.unparsable`）
   不在此修。
4. **正文与 `---` 分隔行逐字节不变**：用 `read_bytes`/`write_bytes` 做 I/O，**避开 `read_text` 的通用
   换行翻译**（否则 CRLF 页会被静默改成 LF、连带改动正文与分隔行）；只替换 frontmatter 块段、只重写
   解析失败的标量值行；合法/缩进/列表/注释行原样保留（含原始行尾 CRLF）。
5. **不跟随符号链接、不写出 `wiki/` 之外**：`write_bytes` 会跟随符号链接，而 `run_check`/`iter_pages`
   经 `is_file()` 会把指向库外的 `wiki/` 符号链接页纳入校验——若不挡，宿主代码可改动 KB 之外的文件。
   故**页本身是符号链接、或 `path.resolve()` 逃出 `wiki.resolve()`（父目录符号链接）一律跳过**、交自愈。
6. **仅 page_guard 路径**：ingest / `query --backfill` / audit 启用；heal（`page_guard=False`）跳过，
   保其「逐字节不变 + 越界写审计」契约。
7. **stderr 透明**：修了打印 `✓ 确定性修正 N 页 frontmatter 引号（免去自愈轮）`，不静默改内容；
   该行属编排核心、走 stderr，不污染 `--json` stdout。

## 3. 不变量

- **零 LLM、零新依赖**（`yaml` 已在依赖内）、**无新命令 / 无新退出码 / 无新 SSE**。
- **门禁结论不变**：修复只是在自愈前多消一类违规；修不掉的一切照旧由有界自愈处理。
- 测试：`tests/test_fmrepair.py`（引号修复 / 成对外引号才修 / 结构性坏值不碰 / 任何可解析 mapping
  即落盘并返回原字节 / 拒符号链接页 + 符号链接父目录越界 / CRLF 逐字节 / 非 UTF-8 不崩 / 作用面按
  kind 过滤、返回 `{页:原字节}`）+ `tests/test_gate.py`（坏引号页**零自愈轮**通过、非引号违规仍照常
  2 轮自愈、合法页零触碰、heal 路 `page_guard=False` 不修复、**门禁重判回滚源不回退 `sources.dropped`
  与跨页 alias 撞名**——含 `run_check` 看不见的 page_guard 专属校验）。
