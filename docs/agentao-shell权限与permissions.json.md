# agentao shell 权限与 `~/.agentao/permissions.json`

**适用场景**：`guanlan ingest`（或 `query --backfill` 等写操作）以 **退出码 5 /
`permission_required`** 失败，`agentao.log` 末尾出现：

```
Tool run_shell_command requires confirmation
Tool run_shell_command execution cancelled by user
```

本文解释为什么会这样、以及如何用一份用户级 `~/.agentao/permissions.json` 一次性修好。

---

## 为什么会失败

观澜的唯一 LLM 写步骤经子进程跑 agentao（`guanlan/runtime.py` 的 `_subprocess_runner`，
由 `gate.py:run_guarded_write_result` 编排）：

```
agentao run --prompt "<task>" --format json \
            --skill guanlan-wiki \
            --permission-mode workspace-write \
            --interaction-policy reject \
            --max-iterations 200
```

两点关键约束：

1. **`--permission-mode workspace-write`**——这是 DESIGN §5.2 的既定姿态：Agent 可写
   `wiki/`/`workspace/`，`raw/` 由 wrapper 的前后快照门禁（`gate.py`）独立保护，所以
   **不**用 `full-access`（那会放开一切 shell/写盘，与「workspace-write + 快照门禁」冲突）。
2. **`--interaction-policy reject`**——子进程模式下没有人能在终端逐次批准，所以任何需要
   交互确认（agentao 里记作 **ASK**）的工具调用都被**当场拒绝并终止整个 run** → 退出码 5。

agentao 在 `workspace-write` 下，对 `run_shell_command` 有一张**只读命令白名单**
（见 agentao `permissions.py` 的 `_PRESET_RULES["workspace-write"]`）：`ls`/`cat`/`grep`/
`head`/`tail`/`wc`/`find`… 这些**本应自动放行**。但白名单正则末尾是

```
(?:[^;&|`$<>\n\r])*$
```

即 **命令里不允许出现任何 shell 操作符**（管道 `|`、`;`、`&&`、重定向 `>`、`$(...)`、反引号…）。

于是当模型跑出这种带管道的只读命令时：

```bash
grep -n "^## \|^### " wiki/overview.md | head -40
```

`grep` 虽在白名单，但那个 `| head -40` 让它**匹配不上白名单规则**，落到兜底规则
`{"tool": "run_shell_command", "action": "ask"}` → **ASK** → 被 `reject` 策略终止 →
ingest 整体失败。

> 同一条命令去掉 `| head -40`（`grep -n "..." wiki/overview.md`）本会被自动放行。
> **是管道把它踢出了白名单**——不是 grep 本身的问题。

---

## 修复：加一份用户级 `~/.agentao/permissions.json`

agentao 的 `run` 子命令 CLI **不暴露**「逐次批准」或「按 run 注入 allow 规则」的入口
（只有 `--permission-mode` 那 4 个挡位）。能在不破坏 workspace-write 姿态的前提下放行
带管道只读 shell 的官方扩展点，就是**用户级**权限文件：

- 路径固定为 `~/.agentao/permissions.json`（user-scope）。
- **项目级 `<repo>/.agentao/permissions.json` 会被 agentao 故意忽略**（带告警）——
  签进仓库的规则可能在克隆者不知情时放大 Agent 权限，权限是 user/host 的事，不是 cwd 的事。
- 在 `workspace-write` 下，**用户规则先于模式预设求值**（first-match-wins），所以用户规则
  能补上「预设白名单不准带管道」这个洞。
- agentao 的 **hardline 底线**（`permissions_hardline.py`：`rm -rf /`、`mkfs`、`dd` 到裸
  块设备、fork bomb、`shutdown`/`reboot`…）在所有规则**之前**独立拦截，用户规则放开不了它。

### 示例文件

```json
{
  "rules": [
    {
      "tool": "run_shell_command",
      "args": {
        "command": "(\\brm\\b|\\bmv\\b|\\bcp\\b|\\bdd\\b|\\bmkfs\\b|\\bsudo\\b|\\bchmod\\b|\\bchown\\b|\\bln\\b|\\btee\\b|\\btruncate\\b|\\bshred\\b|\\bshutdown\\b|\\breboot\\b|\\bkill(all)?\\b|sed\\s+-i|-delete\\b|-exec\\b|>)"
      },
      "action": "deny"
    },
    {
      "tool": "run_shell_command",
      "args": {
        "command": "^\\s*(grep|egrep|fgrep|rg|cat|bat|head|tail|ls|wc|sort|uniq|cut|tr|awk|sed|find|tree|file|stat|pwd|echo|which|type|du|df|date|env|printenv|jq|column|nl|tac|comm|join|paste|fold|diff|cmp|basename|dirname|realpath|readlink|git\\s+(status|log|diff|show|blame|ls-files|ls-tree|rev-parse|describe|shortlog|config\\s+--get))\\b"
      },
      "action": "allow"
    }
  ]
}
```

### 两条规则的设计

规则按**数组顺序**求值，所以 **deny 必须排在 allow 前面**：

1. **deny（安全网）**——正则用 `re.search` 扫**整条命令字符串**，任意位置出现写盘/破坏性
   token（`rm`/`mv`/`cp`/`dd`/`sudo`/`chmod`/`tee`/重定向 `>`/`sed -i`/`find -delete`/
   `-exec` …）即拒绝，**即使被管道夹带**（如 `grep x f | rm -rf foo` 也会被 deny 命中）。
2. **allow（放行只读）**——`^` 锚定命令必须**以只读命令开头**（含 `git status|log|diff…`
   这些不改状态的子命令），放行后**允许管道**，于是 `grep … | head`、`cat … | grep`、
   `find … | wc -l`、`sort | uniq -c | sort -rn` 这类只读流水线全部可用。

> 偏保守取舍：若 `>` 等 token 出现在 grep 的**引号模式串**里（如 `grep "a > b" f`），
> 也会被 deny。这只是让 Agent 换一种写法，不会误放写操作——**宁紧勿松**。

### 验证（可选）

```bash
cd <repo> && uv run python - <<'PY'
from pathlib import Path
from agentao.permissions import PermissionEngine, PermissionMode
eng = PermissionEngine(project_root=Path.cwd(), user_root=Path.home()/".agentao")
eng.set_mode(PermissionMode.WORKSPACE_WRITE)
for c in [
    'grep -n "^## \\|^### " wiki/overview.md | head -40',  # 当初失败的命令 → 现在 ALLOW
    'find wiki -name "*.md" | wc -l',                       # ALLOW
    'echo hi > raw/evil.md',                               # DENY（重定向）
    'grep x f | rm -rf foo',                               # DENY（夹带 rm）
    'sed -i s/a/b/ wiki/index.md',                          # DENY（in-place 改写）
]:
    print(eng.decide("run_shell_command", {"command": c}), "|", c)
PY
```

期望：前两条 `ALLOW`，后三条 `DENY`。

---

## 注意事项

- **全局生效**：这份文件影响**本机所有 agentao 调用**（不仅 guanlan）。它放宽的只是
  *带管道的只读 shell*；写操作仍走 deny + hardline 双重拦截。
- **不改 guanlan 代码**：观澜继续用 `workspace-write` + `--interaction-policy reject` +
  `raw/` 快照门禁，姿态不变；本文档纯属**用户环境配置**。
- **改完无需重装**：agentao 每次 run 时读取该文件，存盘后**直接重跑 ingest 即可**。
- 仍然失败时，看 `agentao.log` 末尾的 `run_shell_command` 参数：若 Agent 跑的是**真正的
  写命令**，那是它该换工具（用 `write_file`/`replace`），**不应**靠放宽权限来过——
  本配置只为放行*只读检视*。
