"""退出码常量与观澜异常（P2，见 docs/P2-最小闭环.md §9）。

退出码是 CLI 对外契约，测试与门禁都依赖其稳定取值：

| 码 | 常量 | 含义 |
|----|------|------|
| 0  | EXIT_OK            | 成功，门禁全过 |
| 1  | EXIT_USAGE         | 用法/IO 错误（非知识库根、目标不在 raw/、非 .md、文件不存在） |
| 3  | EXIT_CHECK_FAILED  | 内容校验失败（frontmatter / 断链 / sources） |
| 4  | EXIT_RAW_MUTATED   | raw/ 被改动（调用前后快照 diff 非空） |
| 5  | EXIT_AGENT_ERROR   | Agentao 运行时错误（子进程非零退出 / status==error / stdout 解析失败） |

> 占位码 2（P1 的"未实现"）随三个命令落地退出历史，保留给未来仍未实现的子命令。
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_CHECK_FAILED = 3
EXIT_RAW_MUTATED = 4
EXIT_AGENT_ERROR = 5


class GuanlanError(Exception):
    """带退出码的观澜错误。

    携带一个 `exit_code`，便于 CLI 顶层统一捕获并返回，而无需在各命令里
    重复 try/except + 手动映射。默认 `EXIT_USAGE`（前置/用法类错误最常见）。
    """

    def __init__(self, message: str, *, exit_code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.exit_code = exit_code
