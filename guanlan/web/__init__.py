"""观澜 Web 宿主子包（P4，可选叠加层，见 docs/P4-Web宿主.md）。

本子包是 MVP 之后的**可选** Web 入口：不装 `guanlan-wiki[web]`、不起 `guanlan web`，
整套东西照旧用 CLI 跑通（DESIGN §1）。它**不承载业务智能**——只把 P2/P3 已交付的命令
搬进浏览器：浏览只读、零 LLM 报告复用既有 `*Report`/`Graph` 序列化、写仅经 `ingest`
单 worker 串行（P2 门禁不动）、问答走只读进程内嵌入。

> 导入本模块（经 `from .server import serve`）会触发 `fastapi`/`uvicorn` 导入；缺 web extra
> 时 `from guanlan.web import serve` 抛 `ImportError`，由 `guanlan/cli.py` 捕获并优雅引导安装。
"""

from __future__ import annotations

from .server import serve

__all__ = ["serve"]
