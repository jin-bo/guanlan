"""保证随包携带的 skill 对 Agentao 可发现（修复"安装后外部库找不到 skill"）。

Agentao 的 skill 发现路径（见 `agentao/skills/manager.py`）：

1. `~/.agentao/skills/`            —— 全局，cwd 无关
2. `<working_directory>/.agentao/skills`
3. `<working_directory>/skills`    —— 仓库根（开发期）

开发期 `working_directory` = 观澜仓库根，`skills/guanlan-wiki/` 命中 (3)，免安装。
但**安装态**（wheel）下 `working_directory` 是用户库，三条路径都没有 skill，于是
`agentao run --skill guanlan-wiki` 会立刻失败。本模块把随包携带的 skill 在首次需要时
**幂等拷贝到全局 (1)**（与 Agentao 自带 skill 的 bootstrap 行为一致），并提供
`guanlan install-skill` 做显式安装/重装。

随包携带两个 skill：
- `guanlan-wiki` —— 维护引擎（**决定可发现性门禁**：`ingest`/`query`/Web 问答都激活它）。
- `pdf-to-markdown` —— 辅助 skill（P4.6）：可写会话里 Agent 把上传的 PDF/DOCX/… 解析成
  `workspace/parsed/` 暂存物（再经人审晋级为 `raw/` 源）。它由 Agent **按需自行激活**
  （非构造期强激活），故只需保证「可发现」即可。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agentao.paths import user_root

# 维护引擎：可发现性门禁与构造期强激活都以它为准（见 web/chat.py、runtime.py）。
SKILL_NAME = "guanlan-wiki"
# 辅助 skill：随包发布、保证可发现，由 Agent 按需激活（P4.6 多格式解析）。
AUX_SKILL_NAMES: tuple[str, ...] = ("pdf-to-markdown",)
# 全部随包 skill：install-skill / ensure 一并铺到全局。
BUNDLED_SKILL_NAMES: tuple[str, ...] = (SKILL_NAME, *AUX_SKILL_NAMES)


def bundled_skill_dir(name: str = SKILL_NAME) -> Path:
    """定位随包携带的某个 skill 源目录（与 init 模板同样的双路径，按优先级）。

      1. 安装后：`guanlan/_skill/<name>/`（wheel force-include 自仓库 `skills/`）。
      2. 开发期：仓库根 `skills/<name>/`（本文件在 `<repo>/guanlan/skill.py`）。
    """
    bundled = Path(__file__).parent / "_skill" / name
    if bundled.is_dir():
        return bundled
    repo_skill = Path(__file__).parent.parent / "skills" / name
    if repo_skill.is_dir():
        return repo_skill
    raise FileNotFoundError(
        f"找不到随包携带的 {name} skill（既无 guanlan/_skill/，也无仓库根 skills/）。"
    )


def global_skill_dir(name: str = SKILL_NAME) -> Path:
    """全局安装目标：`~/.agentao/skills/<name>/`（每次按 user_root() 惰性解析）。"""
    return user_root() / "skills" / name


def _candidate_skill_roots(working_directory: Path) -> tuple[Path, ...]:
    """Agentao 三条 skill 发现根目录（顺序无关，存在即可发现）。"""
    wd = Path(working_directory)
    return (wd / "skills", wd / ".agentao" / "skills", user_root() / "skills")


def _skill_discoverable(working_directory: Path, name: str) -> bool:
    """某 skill 是否已能被 Agentao 在该 working_directory 下发现。

    Agentao 只发现**含 `SKILL.md`** 的目录，故空/半成品目录（被打断的安装、用户建的桩）
    不算数——否则会误判"已装"而跳过拷贝，`agentao run --skill` 仍会失败。
    """
    return any((root / name / "SKILL.md").is_file() for root in _candidate_skill_roots(working_directory))


def is_discoverable(working_directory: Path) -> bool:
    """维护引擎 skill 是否已可发现（门禁口径；辅助 skill 不参与此判断）。"""
    return _skill_discoverable(working_directory, SKILL_NAME)


def _install_one(name: str, *, force: bool = False) -> Path:
    """把单个随包 skill 幂等装到全局 `~/.agentao/skills/<name>/`，返回目标路径。

    已**完整**安装（含 `SKILL.md`）且非 `force` → 原样保留（不覆盖用户改动，与 Agentao
    bootstrap 一致）。`force=True` 或目标是半成品桩（缺 `SKILL.md`）→ 删除后重装。
    """
    src = bundled_skill_dir(name)
    dest = global_skill_dir(name)
    complete = (dest / "SKILL.md").is_file()
    if complete and not force:
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    return dest


def ensure_skill_available(working_directory: Path) -> Path | None:
    """保证随包 skill 在该 working_directory 下可发现，缺则幂等装入全局。

    辅助 skill（pdf-to-markdown）独立保证：即便引擎已可发现也要补齐它（best-effort，
    失败不抛）。返回**引擎** skill 的拷贝落地路径；引擎已可发现则返回 None（不动引擎磁盘）。
    """
    for name in AUX_SKILL_NAMES:
        if not _skill_discoverable(working_directory, name):
            try:
                _install_one(name)
            except Exception:
                pass
    if is_discoverable(working_directory):
        return None
    try:
        return install_skill()
    except Exception:
        return None


def install_skill(*, force: bool = False) -> Path:
    """把全部随包 skill 安装到全局 `~/.agentao/skills/`，返回**引擎** skill 的目标路径。

    引擎与辅助 skill 都各自幂等（见 `_install_one`）。返回引擎路径以兼容
    `guanlan install-skill` 的回显与既有调用方。
    """
    dest = _install_one(SKILL_NAME, force=force)
    for name in AUX_SKILL_NAMES:
        _install_one(name, force=force)
    return dest
