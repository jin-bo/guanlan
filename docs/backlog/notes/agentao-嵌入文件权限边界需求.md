# 对 agentao 嵌入边界的「文件权限域」需求（backlog · 上游需求）

> 状态：**部分已落地、剩余为上游需求**。记录观澜（作为 agentao 的嵌入宿主）在「可写 Web 工作会话」
> （[`../../P4.5-可写Web工作会话.md`](../../P4.5-可写Web工作会话.md)）里需要的确定性文件权限边界。
> **上游 design（[`.../host-fs-policy.md`](../../../../agentao/docs/design/host-fs-policy.md)）评估后给出 interim wrapper 路径：
> 观澜命中的「cwd 内只读子集」facet 今天零 agentao 改动即可落（§2.5），P4.5 §2.2 已采纳。** 本 note 余下记录的是：
> (i) 仍值得提给上游的小 helper（`PathPolicy.contain_any`）、(ii) `filesystem=` 透传确认、(iii) shell enforcement（(c)，P2/可能永不落地）、
> 以及完整 `fs_policy=` lifecycle API（demand-gated、对观澜非必需）。
> 关联：[`../../P4.5-可写Web工作会话.md`](../../P4.5-可写Web工作会话.md) §2/§9、[`../../P4-Web宿主.md`](../../P4-Web宿主.md) §8、`DESIGN.md` 原则 1/2。

## 0. 为什么记这个

P4.5 要让浏览器里的嵌入 Agent 可写（`workspace/`/`wiki/`）、同时 `raw/` 与 `AGENTAO.md`/`SCHEMA.md`
**确定性硬只读**。本 note 初版的判断是「agentao 嵌入面没有按路径域声明可写/只读的原语，故 P4.5 被迫在宿主侧
拼脆弱三件套（正则 deny-rules + 快照 + 写锁）」。**上游评估后纠正了一半**：agentao 的 `FileSystem` capability
（`Agentao(filesystem=…)`、经 `_bind_and_register` 绑到每个工具）**本就是可注入的宿主面**，宿主用一个
`PolicyFileSystem` wrapper 即可对结构化写做确定性包含判定——**无须正则、无须等新 API**（详见 §2.5）。于是层①
从脆弱正则升级为安全 wrapper、`set_mode` hack 消失；真正够不着的只剩 **shell**（走 `shell` capability、非
`filesystem`，= (c)，由层②快照兜）。本 note 余下把「仍该提给上游的最小集」与「完整原语的可选最终态」分清。

## 1. agentao 现状的三处缺口（已核对源码）

| 缺口 | 现状 | 后果（宿主被迫补什么） |
|------|------|------|
| **写边界只有「单根 contain」，无路径域** | `security/path_policy.py` 的 `PathPolicy.contain_file` 只判 `is_relative_to(project_root)`（= `working_directory`）；无「可写根集 / 只读子集」概念 | 宿主无法声明「`raw/` 只读、`wiki/`+`workspace/` 可写」；只能靠 permission 正则 deny-rules 逐路径补（层①） |
| **shell 写不被路径门禁覆盖** | `PathPolicy` docstring 明示「only the cwd is contained」，**shell 命令参数不查**；`echo>`/`mv`/`python` 可旁路结构化工具的路径门禁 | 宿主只能每轮套前后快照（层②）做 shell-proof 兜底 |
| **per-run 规则只增不可删、且按 arg 正则匹配** | `PermissionEngine.add_run_rules(deny=…)` 写进 `_run_scope_rules`，**无公开 remove/clear**；`_matches` 按 `re.search(pattern, str(args[key]))` 匹配（须知道精确 arg 名如 `file_path`、且是正则非路径包含语义） | 宿主只能「构造期永久注入、`set_mode` 不装卸」绕开无 remove；且把路径安全降级成脆弱正则（写错 arg 名即静默漏拦，P4.4 评审 #1 教训） |

> 注：agentao **已有** `sandbox/`（`SandboxProfile` + `workspace_root` + macOS `sandbox-exec`），但 `workspace_root`
> 默认 `None`、且**未经嵌入工厂统一暴露/接线**到「可写域」语义；它是 shell 强隔离的现成底座，却没和上面的
> 文件权限域打通。

## 2. 需求：嵌入边界的「文件权限域」原语

向 agentao 提一条**声明式、按路径域、覆盖全部写路径（含 shell）、可热切换**的 FS 策略原语。建议形态：

```python
# 构造期声明（embedding/factory.build_from_environment 新增 kwarg）：
agent = build_from_environment(
    working_directory=kb,                            # 隐式可写（augment 语义）：cwd 下
                                                     # wiki/workspace/graph 不必再枚举
    fs_policy=FsPolicy(
        immutable=["raw", "AGENTAO.md", "SCHEMA.md"], # 只读子集（优先级高于 writable）
    ),
)
# 运行时热切换（供宿主 /mode 切「写到哪」用，替代 add_run_rules 无 remove 的 hack）：
agent.set_fs_policy(FsPolicy(immutable=["raw"]))     # 只改 immutable/writable 集合
```

> 与上游 design（[`.../agentao/docs/design/host-fs-policy.md`](../../../../agentao/docs/design/host-fs-policy.md)）对齐后的两处收窄：
> 1. **丢掉 `deny_outside_root`**——写边界永远是闭合 allow-set，这个开关既冗余、一旦允许外部根又名实自相矛盾（design (a)）。
> 2. **靠 augment 语义只声明 `immutable`**——`working_directory` 隐式可写，cwd 下 `wiki/workspace/graph` 不必枚举，且避免「漏列某子目录即静默不可写」的 footgun（design (a)）。
> 3. **`set_fs_policy` 不表达整体 read-only**——read-only 是 `PermissionMode`/`readonly_mode` 的职责，`FsPolicy` 只回答「写到哪」、不回答「能不能写」（design (b) 正交段）。所以观澜 `/mode` 翻**只读**仍走既有「两点姿态」（`permission_engine.set_mode(READ_ONLY)` + `tool_runner.set_readonly_mode(True)`），不靠 FsPolicy；`set_fs_policy` 仅用于切 workspace-write 时改 immutable/writable **集合**。

**语义要求**：

1. **单一 chokepoint、覆盖所有写工具**：`write_file`/`replace`/未来任何文件写工具，**以及 shell**，都经同一
   `FsPolicy` 判定——宿主不必知道每个工具的 arg 名、不必逐工具加正则。
2. **路径包含语义、非正则**：复用 `PathPolicy` 现有的 resolve + 跟随 symlink + `..`-safe 判定，扩展为「可写根集
   ∪ 只读子集」；`immutable` 优先于 `writable`（`raw/` 即便在可写根内也只读）。
3. **shell 也被 enforce**：这是最硬的一环。要求 agentao 把 `FsPolicy` 接到 shell 执行——
   - 优先：经 `sandbox/`（`workspace_root` ← `FsPolicy.writable`、`immutable` 设只读）在 OS 层挡（macOS `sandbox-exec`，Linux landlock/bubblewrap）；
   - 跨平台兜底：无 OS 沙箱时，agentao 提供**确定性 fallback**——要么对声明了 `immutable` 的会话**拒绝 shell 写到只读域**（执行前/后路径校验或 diff），要么至少暴露一个 host 可订阅的「写越界」信号。
4. **可热切换、可查询**：`set_fs_policy` / `agent.fs_policy`，干净替代「`add_run_rules` 永久注入 + 无 remove」，让宿主 `/mode` 翻姿态只改策略、不留残规则。
5. **与 PermissionMode 正交**：`FsPolicy` 管「写到哪」，`PermissionMode`/`readonly_mode` 管「能不能写/跑 shell」；二者组合，宿主声明一次即可。

## 2.5 上游已给出 interim 路径：wrapper 今天零改可用（P4.5 已采纳）

上游 design（[`.../agentao/docs/design/host-fs-policy.md`](../../../../agentao/docs/design/host-fs-policy.md)）评估后给出**分阶段结论**：先 ship 一个 host-side `PolicyFileSystem` **wrapper 配方**、`fs_policy=` lifecycle API 按需 demand-gate（不现在建）。关键判断对观澜直接有利：

- **观澜命中的是「cwd 内只读子集」facet**（`raw/`+config 在 kb cwd 内、只读，`wiki/`/`workspace/` 可写）。design 明确这个 facet **今天零 agentao 改动即可满足、原生 `write_file` 直接生效**——注入一个包住 `agent.filesystem` 的 wrapper，对结构化写做**叶解引用包含判定**即可。
- 另一个「cwd 外多根」facet（chahua）才需要 option-2 gate-pushdown（agentao 改码），**观澜不碰**，故不受其阻塞。

因此 **P4.5 §2.2 的层① 已从「`add_run_rules` 正则 deny-rules」改为「`PolicyFileSystem` wrapper」**——这不再是「等上游」，而是**今天可实现**。三点须知：

1. **比正则严格更安全**：包含/叶解引用语义，不靠精确 arg 名（写错即 fail-open 是 P4.4 评审 #1 教训），且挡 `wiki/link→raw/secret` 软链 clobber——正是逐路径正则漏拦的 fail-open 缺口。
2. **顺带消掉 `set_mode` hack**：观澜 immutable 集（raw/+config）两姿态**恒等**，wrapper 构造期装一次、`/mode` 不动它（不需要 (b) 的 `set_fs_policy` 热切换）。`add_run_rules` 无 remove/clear 的麻烦随之消失。
3. **两点须核实**（接线、非 blocker）：① `build_from_environment` 须透传 `filesystem=`（唯一可能要确认/微调 agentao 的点）；② `_effective_target` 叶解引用是安全关键 resolve、勿复用 fail-open 的 `contain_file`。design 建议加 `PathPolicy.contain_any(...)` classmethod 把这段归口（「correct-reuse surface」），**值得顺手推上游**，推成后 wrapper 内核换一行；未推成则按配方自托管（自洽可用）。

净结论：观澜对上游的**剩余**需求收窄为——(i) `PathPolicy.contain_any` 安全 helper（小、值得早落）、(ii) `build_from_environment(filesystem=)` 透传确认、(iii) (c) shell enforcement（P2/可能永不落地，与层②无关）。完整 `fs_policy=` lifecycle API 对观澜**非必需**（wrapper 已覆盖 immutable facet），只在日后想退掉 wrapper 样板时才提。

## 3. P4.5 各补丁的归宿（含 interim wrapper 已落的部分）

> 说明：层① 与 `set_mode` hack **已由 §2.5 的 wrapper 在 P4.5 §2.2 当场解决**，无须等任何 agentao API。本表「最终态」列指完整 `fs_policy=` API 若日后落地的进一步收敛。

| P4.5 补丁 | interim（今天，wrapper） | 最终态（若 `fs_policy=` API 落地） |
|------|------|------|
| 层①（决策P4.5-2） | **已改为 `PolicyFileSystem` wrapper**——包含语义、叶解引用、挡软链 fail-open。零 agentao 改动（§2.5） | 可选：换成 `fs_policy.immutable=[...]` 声明退掉 wrapper 样板（非必需，wrapper 已够） |
| `set_mode` hack（决策P4.5-2/5） | **已消失**——immutable 集两姿态恒等、wrapper 装一次不切；`/mode` 只翻 Mode 两点（read-only 非 FsPolicy 职责） | 不变（read-only 仍走 Mode 两点姿态，见 §2 收窄 3） |
| 层② 每轮 `raw/`+config 快照兜 shell（决策P4.5-3） | **保留**：wrapper 够不着 shell（走 `shell` capability）；层② 是该平台唯一 shell 防线 | **仅在 (c) 落地且当前平台有 OS 沙箱时降级为可选纵深防御**；否则（(c)=P2/可能永不落地，Linux 无 landlock/bubblewrap）**仍是唯一 shell 防线，不删**——见下「现实预期」 |
| 共享进程写锁（决策P4.5-6） | **保留**：单写者并发宿主策略，与 agentao 权限无关 | 不变 |
| `wiki/` 写后 `check`（决策P4.5-4） | **保留**：观澜领域门禁（frontmatter/断链），非通用文件权限 | 不变 |

净效果：观澜的 filesystem 权限**今天**就从「正则 + 快照 + `set_mode` hack 三件套」收敛为「**wrapper（结构化写）+ 快照（shell）**两件套」——层①+hack 由 §2.5 的 wrapper 当场解决、零 agentao 改动；剩余对上游只需 (i) `PathPolicy.contain_any` 安全 helper、(ii) `filesystem=` 透传确认、(iii) (c) shell（P2/可能永不落地）。完整 `fs_policy=` API 对观澜非必需。

> **现实预期（今天可落 + 只 bank (a)+(b)）**：上游 design 已给 **interim wrapper 路径——观澜命中的 immutable facet 今天零改即可落**（§2.5），不必等任何 agentao API。后续若推完整原语，design 把它切成 (a) 路径域泛化 / (b) 热切换 / (c) shell enforcement，且 (a)+(b) 不依赖 (c)。**(c) 被标为 roadmap P2、design 自陈「可能永不落地」**（Linux 无 landlock/bubblewrap，macOS 还需新 SBPL deny-after-allow profile）。因此：① 层①+`set_mode` hack 已用 wrapper 当场清掉，不依赖排期；② 层② 快照在**无 OS 沙箱的平台（含 Linux）长期是唯一 shell 防线**，不删；③ 完整 `fs_policy=` 原语在上游是 **demand-gated 的 P1-adjacent 候选、非已排期项**，但观澜**已不阻塞**于它（wrapper 已覆盖）。

## 4. 观澜无论如何保留的部分（不该上游）

- **策略决策**（哪些路径只读/可写、`raw`∪config 的具体集合）—— 这是观澜知识库的语义，由观澜声明，不是 agentao 该内置的。
- **领域门禁** `wiki/` 写后 `check`、ingest 的 `raw/` 快照 gate —— 业务校验，归观澜。
- **单写者并发**（写锁）—— 宿主进程模型，归观澜。
- **不变量哨兵快照（可选）** —— `raw/` 不可变是 DESIGN 原则 1，留一个零成本的 best-effort 哨兵作纵深防御合理，但不再是主防线。

## 5. 行动建议

1. **按现状（wrapper + 快照两件套）落地 P4.5，今天即可、不阻塞 agentao**——层① 用 §2.5 的 `PolicyFileSystem` wrapper（P4.5 §2.2 已写死）、层② 沿用快照，两层自洽。落地时核实 `build_from_environment` 透传 `filesystem=`（唯一可能要确认的 agentao 接线）。
2. **给 agentao 提一条小 issue：`PathPolicy.contain_any(raw, writable=[…], immutable=[…])` 安全 helper**——把每个宿主都要手写的叶解引用 resolve 归口（design「correct-reuse surface」），是「正确 wrapper」与「fail-open wrapper」之差，值得早落；完整 `fs_policy=` lifecycle API 保持 demand-gated、对观澜非必需。
3. **(c) shell enforcement 单独走 P2 track**：观澜不指望，层② 在无 OS 沙箱平台长期是唯一 shell 防线（§3「现实预期」）。若上游日后落完整 `fs_policy=`，再按 §3「最终态」列考虑退掉 wrapper 样板，并在 P4.5 决策追加一条。

> 一句话：**`raw/` 只读不可变是 DESIGN 原则 1——上游已确认它今天就能用一个 host-side `PolicyFileSystem` wrapper 确定性守住（结构化写），观澜无须再用脆弱正则；剩下够不着的只有 shell（(c)，P2），由快照哨兵兜。**
