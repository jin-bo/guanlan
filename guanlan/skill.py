"""保证 `guanlan-wiki` skill 对 Agentao 可发现（修复"安装后外部库找不到 skill"）。

Agentao 的 skill 发现路径（见 `agentao/skills/manager.py`）：

1. `~/.agentao/skills/`            —— 全局，cwd 无关
2. `<working_directory>/.agentao/skills`
3. `<working_directory>/skills`    —— 仓库根（开发期）

开发期 `working_directory` = 观澜仓库根，`skills/guanlan-wiki/` 命中 (3)，免安装。
但**安装态**（wheel）下 `working_directory` 是用户库，三条路径都没有 skill，于是
`agentao run --skill guanlan-wiki` 会立刻失败。本模块把随包携带的 skill 在首次需要时
**幂等拷贝到全局 (1)**（与 Agentao 自带 skill 的 bootstrap 行为一致），并提供
`guanlan install-skill` 做显式安装/重装。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agentao.paths import user_root

SKILL_NAME = "guanlan-wiki"


def bundled_skill_dir() -> Path:
    """定位随包携带的 skill 源目录（与 init 模板同样的双路径，按优先级）。

      1. 安装后：`guanlan/_skill/guanlan-wiki/`（wheel force-include 自仓库 `skills/`）。
      2. 开发期：仓库根 `skills/guanlan-wiki/`（本文件在 `<repo>/guanlan/skill.py`）。
    """
    bundled = Path(__file__).parent / "_skill" / SKILL_NAME
    if bundled.is_dir():
        return bundled
    repo_skill = Path(__file__).parent.parent / "skills" / SKILL_NAME
    if repo_skill.is_dir():
        return repo_skill
    raise FileNotFoundError(
        f"找不到随包携带的 {SKILL_NAME} skill（既无 guanlan/_skill/，也无仓库根 skills/）。"
    )


def global_skill_dir() -> Path:
    """全局安装目标：`~/.agentao/skills/guanlan-wiki/`（每次按 user_root() 惰性解析）。"""
    return user_root() / "skills" / SKILL_NAME


def _discovery_candidates(working_directory: Path) -> tuple[Path, ...]:
    wd = Path(working_directory)
    return (
        wd / "skills" / SKILL_NAME,
        wd / ".agentao" / "skills" / SKILL_NAME,
        global_skill_dir(),
    )


def is_discoverable(working_directory: Path) -> bool:
    """skill 是否已能被 Agentao 在该 working_directory 下发现。

    Agentao 只发现**含 `SKILL.md`** 的目录，故空/半成品目录（被打断的安装、用户建的桩）
    不算数——否则会误判"已装"而跳过拷贝，`agentao run --skill` 仍会失败。
    """
    return any((c / "SKILL.md").is_file() for c in _discovery_candidates(working_directory))


def ensure_skill_available(working_directory: Path) -> Path | None:
    """若 skill 在该 working_directory 下不可发现，则幂等拷贝到全局目录。

    返回拷贝落地路径；已可发现则返回 None（不动磁盘）。best-effort：拷贝失败不抛，
    让 `agentao run` 自己暴露"skill 缺失"错误，而非在 wrapper 里二次崩溃。
    """
    if is_discoverable(working_directory):
        return None
    try:
        return install_skill()
    except Exception:
        return None


def install_skill(*, force: bool = False) -> Path:
    """把随包 skill 安装到全局 `~/.agentao/skills/guanlan-wiki/`。

    已**完整**安装（含 `SKILL.md`）且非 `force` → 原样保留（不覆盖用户改动，与 Agentao
    bootstrap 一致）。`force=True` 或目标是半成品桩（缺 `SKILL.md`）→ 删除后重装。返回目标路径。
    """
    src = bundled_skill_dir()
    dest = global_skill_dir()
    complete = (dest / "SKILL.md").is_file()
    if complete and not force:
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    return dest
