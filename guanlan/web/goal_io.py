"""per-conversation goal sidecar 持久化（P4.16，见 docs/P4.16-Web目标续跑.md §10）。

agentao 的 `save_goal`/`load_goal`/`goal_path` 硬编码**单文件** `<root>/.agentao/goal.json`
（CLI 单会话）；guanlan Web 多会话（`ConversationStore` 可并发多个 goal），故**不能**复用那套
单文件 IO，须 **per-conversation**：`<kb>/.agentao/goals/<conversation_id>.json`（与
`.agentao/sessions/` 并列，已 `.gitignore`、扫描只看 `*.md`）。

仍**复用** `GoalState.to_dict`/`from_dict`（状态机单一真相源在 agentao，§1.3 复用矩阵）——本模块
只提供纯路径 helper + 原子读写：`write_goal_atomic` 用「同目录临时文件 + `os.replace`」原子替换，
reader（懒恢复/cold_info、甚至跨进程）永不见半写文件（§10 不变量⑩）。**锁纪律**（`_goal_lock`
快照、`_goal_io_lock` 串行写盘）由 `Conversation` 持有——本模块刻意无锁、无业务态，只管「写对、原子」。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from agentao.cli.goal_state import GoalState


def goals_dir(kb: Path) -> Path:
    """goal sidecar 目录 `<kb>/.agentao/goals/`。"""
    return Path(kb) / ".agentao" / "goals"


def goal_sidecar_path(kb: Path, conversation_id: str) -> Path:
    """某会话的 goal sidecar 路径。conversation_id 即会话稳定 UUID（=session_id）。"""
    return goals_dir(kb) / f"{conversation_id}.json"


def read_goal(path: Path) -> GoalState | None:
    """读 sidecar → `GoalState`；缺失/坏 JSON/坏字段一律 `None`（同 agentao `load_goal` 容错——
    手改/半写的 goal.json 退化为「无目标」，绝不载入毒值让续跑循环后续崩在 budget_tripped）。"""
    if not path.exists():
        return None
    try:
        return GoalState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def write_goal_atomic(path: Path, data: dict) -> None:
    """原子写盘：同目录临时文件 + `os.replace`（须同文件系统）。

    调用方（`Conversation._persist_goal`）已持 `_goal_io_lock` 串行两写者、且 `data` 是 `_goal_lock`
    内取的 `to_dict()` 快照——本函数只管写对、原子，不碰锁。OSError（只读目录/满盘）上抛，由调用方
    降级（落盘失败仅记日志、不毁本轮，§4.4 降级精神）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")  # 避开 with_suffix 多点歧义
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # 原子替换：reader 永不见半写文件


def clear_goal_sidecar(path: Path) -> bool:
    """删 sidecar（clear/替换非活动目标用）。返回是否有文件被删。"""
    try:
        path.unlink()
        return True
    except (FileNotFoundError, OSError):
        return False
