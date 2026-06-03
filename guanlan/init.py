"""`guanlan init` —— 在目标目录确定性生成最小知识库模板（零 LLM）。

生成物（DESIGN §4.2 / §7 P1）：

    <target>/
    ├── AGENTAO.md          # Agent 行为约束 + 指针（拷自模板）
    ├── SCHEMA.md           # 本库 Schema 层（拷自模板）
    ├── raw/                # 原始资料（只读，事实来源；空）
    └── wiki/
        ├── index.md        # 全量页面目录
        ├── log.md          # append-only 时间线（含 init 首条）
        └── overview.md     # 跨资料活体综述（空库占位）

已存在的文件**不覆盖**，只报告并跳过——init 可安全重复运行。
模板源 = `examples/`（开发期仓库根）或打包进 wheel 的 `guanlan/_templates/`（安装后）。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path

# init 时替换为当天日期（ISO）的占位符；只在 wiki/ 种子文件里出现。
_DATE_TOKEN = "__DATE__"

# 顶层文件（逐字拷贝，无占位符替换）。
_TOP_FILES = ("AGENTAO.md", "SCHEMA.md")

# wiki/ 种子文件（拷贝并替换 _DATE_TOKEN）。
_WIKI_SEED_FILES = ("index.md", "log.md", "overview.md")


@dataclass
class InitResult:
    """init 的结果，便于测试与 CLI 报告。"""

    target: Path
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _templates_dir() -> Path:
    """定位模板目录。

    两条路径，按优先级：
      1. 安装后：与本包同级的 `guanlan/_templates/`（wheel force-include 自 examples/）。
      2. 开发期：仓库根的 `examples/`（本文件在 `<repo>/guanlan/init.py`）。
    """
    bundled = Path(__file__).parent / "_templates"
    if bundled.is_dir():
        return bundled
    repo_examples = Path(__file__).parent.parent / "examples"
    if repo_examples.is_dir():
        return repo_examples
    raise FileNotFoundError(
        "找不到 init 模板目录（既无打包的 guanlan/_templates/，也无仓库根 examples/）。"
    )


def _write_if_absent(dst: Path, content: str, result: InitResult) -> None:
    rel = dst.relative_to(result.target).as_posix()
    if dst.exists():
        result.skipped.append(rel)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8")
    result.created.append(rel)


def run_init(target: str | Path = ".", *, today: str | None = None) -> InitResult:
    """在 `target` 生成最小知识库模板，返回 InitResult。

    Args:
        target: 目标目录（不存在则创建）。
        today: 覆盖 init 日期（ISO `YYYY-MM-DD`）；默认取当天，主要用于测试。
    """
    target = Path(target).expanduser().resolve()
    date_str = today or datetime.date.today().isoformat()
    templates = _templates_dir()
    result = InitResult(target=target)

    target.mkdir(parents=True, exist_ok=True)

    # 顶层文件：逐字拷贝。
    for name in _TOP_FILES:
        src = templates / name
        _write_if_absent(target / name, src.read_text(encoding="utf-8"), result)

    # raw/：空目录（只读，事实来源）。用 .gitkeep 让空目录可被纳管/可见。
    _write_if_absent(
        target / "raw" / ".gitkeep",
        "# 原始资料放这里（只读，Agent 永不修改）。投喂后用 `guanlan ingest raw/<file>.md`。\n",
        result,
    )

    # wiki/ 种子文件：替换日期占位符。
    wiki_templates = templates / "wiki"
    for name in _WIKI_SEED_FILES:
        content = (wiki_templates / name).read_text(encoding="utf-8")
        content = content.replace(_DATE_TOKEN, date_str)
        _write_if_absent(target / "wiki" / name, content, result)

    return result
