"""ingest 工作流（P2，见 docs/P2-最小闭环.md §7）。

`guanlan ingest raw/<file>.md`：前置校验 → 取 raw 快照 → Agentao + skill 摄入 → 收尾门禁。
真正的建页步骤在 `guanlan-wiki` skill 里；本模块只做编排与确定性门禁。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .gate import run_guarded_write
from .paths import require_kb_root
from .provenance import compute_raw_digest, format_digest_value, stamp_raw_digest
from .runtime import AgentRunner

# 薄 prompt：真正步骤在 skill。{rel} = 相对 raw/ 的 posix 路径。
INGEST_PROMPT = (
    "请按 `guanlan-wiki` skill 的 ingest 工作流摄入资料 `raw/{rel}`："
    "该路径已由 wrapper 校验存在，必须按原样读取，不要替换其中的引号、空格或 CJK 字符；"
    "读该 `.md` 源与 `wiki/index.md`、`wiki/overview.md` 建上下文；"
    "写/更新 source·entity·concept 页（frontmatter 齐全、术语转 `[[wikilink]]`）；"
    "更新 `index.md` 与 `overview.md`；发现矛盾就地标 `## ⚠️ 矛盾与存疑`；"
    "向 `log.md` 追加一条 `## [<日期>] ingest | <标题>`。"
    "遵循 `AGENTAO.md` 硬约束与 conventions 默认。**永不修改 `raw/`。** "
    "**不要运行 shell 命令；读写文件必须使用内置文件工具。不要自行执行 `guanlan check`，wrapper 会在你返回后强制校验。** "
    "完成后用一两句说明触及了哪些页面。"
)


def _resolve_raw_target(root: Path, target: str) -> Path:
    """把 target 解析为 `<root>/raw/` 下存在的 `.md` 文件，否则抛 GuanlanError(EXIT_USAGE)。"""
    tpath = Path(target).expanduser()
    if not tpath.is_absolute():
        tpath = root / tpath
    tpath = tpath.resolve()

    # raw_dir 也 resolve：raw/ 本身可能是符号链接（指向库外存储），否则 relative_to 永远失败。
    raw_dir = (root / "raw").resolve()
    try:
        tpath.relative_to(raw_dir)
    except ValueError:
        raise GuanlanError(
            f"摄入目标必须位于 raw/ 下：{target}", exit_code=EXIT_USAGE
        ) from None
    if tpath.suffix.lower() != ".md":
        raise GuanlanError(
            f"ingest 只吃 `.md`；多格式请先 `guanlan convert {target}` 转成 raw/<name>.md 再 ingest。",
            exit_code=EXIT_USAGE,
        )
    if not tpath.is_file():
        raise GuanlanError(f"文件不存在：{target}", exit_code=EXIT_USAGE)
    return tpath


def run_ingest(
    target: str,
    *,
    root: str | Path = ".",
    model: str | None = None,
    runner: AgentRunner | None = None,
) -> int:
    """摄入一篇 `.md`，返回退出码（见 errors.py）。"""
    try:
        kb = require_kb_root(root, writable=True)
        tpath = _resolve_raw_target(kb, target)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    rel = tpath.relative_to((kb / "raw").resolve()).as_posix()
    rc = run_guarded_write(
        kb, INGEST_PROMPT.format(rel=rel), model=model, runner=runner
    )
    if rc == EXIT_OK:
        _stamp_source_digest(kb, tpath, rel)
    return rc


def _stamp_source_digest(kb: Path, raw_file: Path, rel: str) -> None:
    """门禁后由 wrapper 把本次摄入 raw 的内容指纹 stamp 进对应 source 摘要页（P3.7 §4.2，决策P3.7-3a）。

    **只 stamp 本次那一张** `wiki/sources/<stem>.md`（按 conventions slug==rawname 约定定位），绝不
    顺手刷别页（否则抹平别处真实漂移，是致命 bug）。定位不到 / 该页 frontmatter 本就坏 / 写后 check
    不过 → `stamp_raw_digest` 跳过并回滚、记一句降级到 stderr，**绝不阻断 ingest**（决策P3.7-9）。
    指纹计算与写入是 wrapper 确定性代码、不持 LLM client（红线）。
    """
    source_page = kb / "wiki" / "sources" / f"{Path(rel).stem}.md"
    if not source_page.is_file():
        return  # 无对应 source 页（slug 不符约定）→ 无声跳过（决策P3.7-5 安全退化、下次自然补）
    try:
        # raw 字节读取也可能抛 OSError（极小窗口：门禁后被删/权限变）；与 audit `_refresh_one` 同口径
        # 兜底——**绝不让 stamp 在门禁已过后崩掉 ingest**（决策P3.7-9）。
        value = format_digest_value(f"raw/{rel}", compute_raw_digest(raw_file))
        ok = stamp_raw_digest(source_page, value)
    except OSError:
        ok = False
    if not ok:
        print(
            f"ℹ raw_digest 未写入 {source_page.relative_to(kb).as_posix()}"
            "（frontmatter 不可解析或写后校验未过，已回滚；不影响摄入）。",
            file=sys.stderr,
        )
