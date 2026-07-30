# nashsu/llm_wiki 反向评审 v0.6.4（增量·backlog）

> 状态：**反向评审结论，非排期项**。承 [`llm_wiki-反向评审-v0.6.md`](llm_wiki-反向评审-v0.6.md)（至 v0.6.3=`9b71ade`），本篇键定 **2026-07-15 pull**（`9b71ade→38f4cb1`，v0.6.3→**v0.6.4**，79 文件 +3017/-308）。**不改变现状，只借形状、不借实现。**
>
> **方法**：把 llm_wiki 本次增量的每一条修复当**探针**，逐条到 guanlan 里对抗性地找同款潜藏缺陷，读真实代码验证后再定级。三个只读探针覆盖：外链/`sources` 处理、`raw/` 泄漏面（search·web·mcp）、imageio 扩展名信任 + convert 路径撞名 + heal 去重。
>
> **一句话结论**：**今天 llm_wiki 的整批修复，绝大多数 guanlan 架构上已免疫**（§4）——本次增量是"别人踩坑、我方设计经受住"的**正向验证**，而非新缺口。真正的新面只有 **1 个可选立即小动作（§2）+ 3 项仅观察（§3）**。
>
> 关联：[`llm_wiki-反向评审-v0.6.md`](llm_wiki-反向评审-v0.6.md)、[`nashsu-llm_wiki-反向评审结论.md`](nashsu-llm_wiki-反向评审结论.md)、[`../../P2.1-摄入写入纪律.md`](../../P2.1-摄入写入纪律.md)（决策P2.1-4：代码绝不改写正文——本篇 §2.1 的红线约束）、[`../../P5.2-多格式摄入.md`](../../P5.2-多格式摄入.md)（convert 面）、[`../../P5.2.1-图片落盘.md`](../../P5.2.1-图片落盘.md)（imageio 扩展名/撞名/落盘面）。

## 0. 本次增量主线

frontmatter/摄入健壮性硬化（剥代码围栏、补开栏、修 wikilink 列表、CRLF 字节偏移、frontmatter 越界抽取）+ EPUB/MOBI 源 + 官方本地 MinerU API + 项目维护/归档 + 外链凭证/敏感源过滤 + missing-page 判定收紧。

**架构分歧（决定"怎么借"）**：llm_wiki 走 **sanitize-on-write**（写盘前主动清洗 LLM 输出常见瑕疵，`sanitizeIngestedFileContent` 4 步流水线：剥围栏 / 剥 `frontmatter:` 前缀 / 补缺失开栏 / 修 frontmatter wikilink 列表）+ 读时兜底。guanlan 走 **fail-closed 门禁 + 窄确定性预修复**（`fmrepair` 仅修引号，其余坏 frontmatter → 拒并回喂一轮 LLM 自愈）。故借鉴只能借**形状**（往 `fmrepair` 加确定性修复项），**不能照抄 sanitize-on-write**——后者对整文档动刀，撞 决策P2.1-4。

## 1. 新增缺口（真实的）

- **A. `fmrepair` 只修引号，不修"围栏包裹 / 缺失开栏"的 frontmatter**（对应 `8892978` #575 / `a75e866` / `df37a51`）。当 LLM 把 frontmatter 包进 ```` ```yaml … ``` ````、或写了正文+闭合 `---` 却漏开栏 `---` 时，`pages.split_frontmatter`（`pages.py:120-133`）首行非 `---` → 返回 `None` → `check` 报 `frontmatter.block_missing`。而 `fmrepair.repair_page_frontmatter` **只在违规为 `frontmatter.unparsable` 时才动**（`fmrepair.py:118`），`block_missing` 直接落到**一整轮 LLM 自愈**（冷启动+推理）或被门禁拒。guanlan 全库**无任何代码围栏剥离逻辑**（已 grep 确认）。→ §2.1（**唯一站得住、且与 `fmrepair` 立身之本"省一轮自愈"同轴**的确定性可优化面）。
- **B. `web/render.py` 链接消毒器不拒 URL 内嵌凭证**（对应 `40932fc`）。`_is_safe_url`（`render.py:41-51`）查了 scheme 白名单 `{http,https,mailto}`、剥了控制字符，但**不查 userinfo**——`http://legit.com@evil.com`（scheme=http）放行，渲染进本地 Web 预览、点击后导航到 evil.com。**低危**：Web 宿主仅 127.0.0.1 单用户（钓鱼自己），但形状正是 llm_wiki 本次拒 `url.username/password` 那条。→ §2.2（一行可加）。

## 2. 最小动作（可选，两项，均低成本）

1. **`fmrepair` 扩一档：确定性剥/补 frontmatter 框（作用于 `block_missing`）**。参照 llm_wiki `stripOuterCodeFence` + `addMissingOpeningFrontmatterFence` 的**已验证边角**（直接抄，避免二次踩坑）：BOM 前缀、围栏前导空行、`(?:yaml|md|markdown)?` 语言标签、"围栏只包 frontmatter、正文在栏外"变体、`^---$` **多行锚点**匹配多行 frontmatter。**红线约束（决策P2.1-4）**：
   - "缺开栏"（仅 prepend `---\n`）与"围栏只包 frontmatter"（仅删 frontmatter 两侧栏行）是**纯 frontmatter 区编辑、body 字节不变** → 干净可借。
   - "围栏包整文档"变体会动到 body 首尾框行，与 `fmrepair` 现有"正文与 `---` 分隔行逐字节不变"纪律有张力 → **须谨慎裁剪，或先只做前两个变体**。
   - 复用现有三道安全闸（**修完严格档必须复验为 mapping，否则整页放弃、回落 LLM 自愈**）——"最差=现状"由门禁回滚兜底，零回归。
   - **前置存疑：是否真高频？** guanlan 是 skill 按 `conventions.md` 经 `write_file` **逐块写页**，不像 llm_wiki **整文件生成**，围栏/漏栏本就更少见。**建议先在真实 ingest/heal 运行里数一数 `block_missing` 自愈轮的实际触发频次**——高频才做，低频则降 §3 观察。属"省成本"优化，非正确性缺口（门禁已 fail-closed 兜住正确性）。
2. **`render._is_safe_url` 增补 userinfo 拒绝**：对 `http/https` 链接，若含 `@`（userinfo）则失活为 `href="#"`（同现有 javascript: 处理）。一行判定，纵深防钓鱼。

## 3. 仅观察（不排期）

- **imageio 按实际字节 MIME 推导扩展名（对应 `df37a51` mineru）**：guanlan 落盘图片名的扩展名取自**源文件后缀** `resolved.suffix.lower()`（`imageio.py:164`），准入闸只做**后缀白名单**（`IMAGE_EXTS`，`imageio.py:36,119`），**不读魔数/真实 MIME**。若 skill 把 PNG 字节命名成 `.jpg`，guanlan 带错扩展名落盘。**与 llm_wiki 弱**：其来源是**本地 skill 产物**（非不可信远程 data-URI 服务器），后果是**预览/下游 MIME 不符（非安全）**。其余防护完备（拒 scheme/`//`/`~` 外链、`%2e` 解码防穿越、realpath+`relative_to` 越界拒、symlink 拒、`<stem>-<n>` 确定性命名天然免疫撞名与 Windows 保留名）。**不排期**：低收益、非安全。
- **`rawio` raw 目标名无 Windows 保留设备名防护（对应 `df37a51` mineru 注释）**：`safe_raw_target`（`rawio.py:116-143`）做了剥目录/NFKC/混淆折叠/强制 `.md`/越界校验，但**无 `CON/PRN/AUX/NUL/COM1..` 拒绝**——slug 恰为 `con` → 落成 `con.md`，Windows 上撞保留设备名。图片名因恒带 `-<n>` 后缀免疫，唯 `raw/<slug>.md` 缺此护。**Windows-only、理论边角**，不排期；若未来正式支持 Windows 宿主再收。
- **Web reader 模式服务任意 `raw/**.md` 内容（承 `d09f8f0` 的纵深精神）**：`GET /api/raw/file`（`app.py:570-584` + `helpers.py:49-64`，`.md`-only + `relative_to(raw)` 容器化）在匿名 `reader` 模式**仍注册**（是读、不被 `_writer_only` 包裹）。这是与 llm_wiki"保留 `.md`"**同姿态、平价**（非回归），但意味着粘进 `raw/` 某 `.md` 的密钥可被本地 Web 浏览。**观察**：是否给 reader 模式加一道"raw/ 仅 writer 可见"的纵深开关。MCP 无此面（锁死 `wiki/`，`tools.py:179-210`）。

## 4. 不做什么（已免疫 / 已覆盖，附验证）

本次 llm_wiki 修的核心 bug 类，guanlan **架构上已免疫**——逐条读码验证：

- **frontmatter 越界抽取**（`9c7b622`/`6984e55`：`/^---\n[\s\S]*?^title:…/m` 不限定闭合 `---`，正文一行 `type: query` 被误读为 frontmatter → 整页从图静默丢弃 / 污染 embedding）→ **免疫**。`split_frontmatter` 强制闭合 `---`（`pages.py:130`，无闭合即 `None`）；`search._scan_scalar` 只在**已隔离 block** 内行扫（`search.py:142-151`，block 来自 `build_doc` 的 `split_frontmatter`，`search.py:235`）；check/graph/health 全部经 `pages.py` 归口、无旁路整文件正则。这正是 llm_wiki 本次才收敛到的做法。
- **missing-page 子串过匹配**（`11c7553`：`norm.includes(name)` 短标题误命中 → 改精确归一名）→ **免疫**。撞名守卫用**精确 slug 键** `raw_slug(p.stem)==target_slug`（`ingest.py:108-118`）；`lint.missing_entities` 从**已建好的图**聚合未解析引用（dict 键相等，`lint.py:82-98`），不走 LLM 模糊标题；`heal` 全程 `resolve_owner` 精确键 + 安全 fold 兜底（`pages.py:388-400`），撞则不折叠、歧义保持断链；唯一模糊匹配 `_suggest_nearest`（Jaccard）**仅 lint advisory 展示**，不进物化决策。
- **frontmatter 改写字节偏移 / CRLF 腐蚀**（`df37a51`：硬编码 `+4` 假设开栏 `---\n` 四字节，CRLF `---\r\n` 五字节 → 腐蚀边界）→ **免疫**。`fmrepair` 用 `splitlines(keepends=True)` 行级重建 `lines[0]+new_block+lines[close]+body`（`fmrepair.py:127-129`），`_requote_block` 逐行保原始 EOL（`eol=raw_line[len(stripped):]`）——CRLF 逐字节保真，从不用字节偏移。
- **graph frontmatter 解析分叉**（`d12b830` 统一）→ **免疫**。guanlan 早已全部归口 `pages.py`。
- **敏感非-`.md` 文件泄漏**（`d09f8f0`：滤 `raw/sources/.claude/settings.json`）→ **免疫，且实现更稳（白名单 vs 黑名单）**。`search` 语料 `wiki/`-only + `.md`-only（`iter_pages` `pages.py:255-262`，`run_search` `search.py:683`），raw/ 根本不入语料；Web 三个 raw 端点全要求 `.md`（`/api/raw/file`）或图片扩展白名单（`/api/raw/image`），KB 根/raw/ **从不**作静态目录挂载；MCP 锁死 `wiki/`。`.env`/`.claude/settings.json`/密钥文件均不可达。
- **rebuild index from frontmatter / 项目归档导出**（`b509a09`：`rebuild_wiki_index` + `export/import_project_archive`）→ **已有 / 范围外**。`rebuild_wiki_index` = guanlan `reindex`（P3.4，从盘上页 frontmatter 补 `index.md`）；库归档 = 目录 tar/git，guanlan 无需内建（"markdown 唯一真相"下 git 即归档）。
- **EPUB/MOBI 源 + 官方本地 MinerU API**（`9d95f79`/`fdf5a69`）→ **归 skill 侧、观察**。guanlan `convert` 委托 `pdf-to-markdown` skill（MinerU/marker/pypdf），格式覆盖是 skill 的事、非 guanlan 本体。**观察项**：确认该 skill 是否已覆盖电子书；若要 EPUB/MOBI，在 skill 层加后端，guanlan 侧的 `IMAGE_EXTS`/`SUPPORTED_EXTENSIONS` 对齐即可。
- **provider controls / 混合检索加权 / 桌面 UI**：Provider 抽象刻意下沉 agentao（无观澜层 provider）；检索加权见 v0.6 篇 §4 park；桌面 UI 与 CLI + 只读 Web 面不重叠。
