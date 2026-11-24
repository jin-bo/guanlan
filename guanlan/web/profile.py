"""Web 问答画像的来源与读取（P5.5.2，见 docs/P5.5.2-Web问答画像.md）。

来源只在 `guanlan web` 入口解析一次（决策W1/W2）：显式 `--profile <路径>`，或默认路径 `~/.guanlan/USER.md`。
之后每个会话构造时按同一来源**重新读一次**（决策W3）：默认路径启动后才建、或坏了又修好，都对新会话生效。
读取与校验复用 CLI 的 `load_profile`（P5.5）、`build_profile_instructions`；这里不另写判定。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import GuanlanError
from ..query import DEFAULT_PROFILE_RELPATH, _display_path, _home, load_profile


@dataclass(frozen=True)
class ProfileSource:
    """画像来源。`explicit` 为 None 表示默认路径 `~/.guanlan/USER.md`。"""

    explicit: Path | None = None

    @property
    def kind(self) -> str:
        return "default" if self.explicit is None else "explicit"

    def current_path(self) -> Path | None:
        """此刻要读的路径；默认路径不存在（或主目录拿不到）时为 None。

        与 CLI 的 `resolve_default_profile` 同口径（悬空链接、无权限都算存在，交给 `load_profile` 报原因），
        但不打「找不到主目录」警告：会话构造时每次都打会刷屏，状态经 `/profile` 可见。
        """
        if self.explicit is not None:
            return self.explicit
        home = _home()
        if home is None:
            return None
        p = home / DEFAULT_PROFILE_RELPATH
        try:
            os.lstat(p)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError:
            return p
        return p

    def display(self, path: Path | None = None) -> str:
        """给人看的路径（主目录下显示成 `~/…`）。"""
        target = path if path is not None else self.current_path()
        if target is None:
            target = (_home() or Path("~")) / DEFAULT_PROFILE_RELPATH
        return _display_path(Path(target).expanduser(), _home())


@dataclass(frozen=True)
class LoadedProfile:
    """一次读取的结果：`text` 与 `warning` 至多一个非空；两者都空表示此刻没有画像。"""

    path: str | None = None
    text: str | None = None
    warning: str | None = None


def load_for_session(source: ProfileSource | None, kb: Path) -> LoadedProfile:
    """会话构造时读一次画像（决策W3）。不合格不抛，只把原因放进 `warning`（会话照建、不带画像）。"""
    if source is None:
        return LoadedProfile()
    path = source.current_path()
    if path is None:
        return LoadedProfile()
    shown = source.display(path)
    try:
        return LoadedProfile(path=shown, text=load_profile(shown if source.explicit is None else path, kb))
    except GuanlanError as exc:
        return LoadedProfile(path=shown, warning=str(exc))


def startup_line(source: ProfileSource | None, kb: Path) -> str | None:
    """启动信息里的一行（决策W2）。显式路径不合格抛 `GuanlanError`（EXIT_USAGE，拒绝启动）；
    默认路径不合格只返回警告行；没有画像返回 None。
    """
    if source is None:
        return None
    if source.explicit is not None:
        load_profile(source.explicit, kb)  # 不合格即抛，拒绝启动
        shown = source.display(source.explicit)
    else:
        if _home() is None:
            return "⚠ 找不到主目录，未查找默认画像。"
        loaded = load_for_session(source, kb)
        if loaded.warning is not None:
            return f"⚠ 默认画像未使用：{loaded.warning}（新会话不带画像；修正后新会话会自动带上）"
        if loaded.text is None:
            return None
        shown = loaded.path
    return (
        f"提问者画像：{shown}（新会话带上；会话内 /profile 查看或关闭）。"
        "画像会发给模型提供方，并以明文记入库根 agentao.log；按画像写的回答会随会话快照存进库内 "
        ".agentao/sessions/（可用 --no-agent-log、--no-session-persist 关闭）"
    )
