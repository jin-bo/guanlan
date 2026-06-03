"""`guanlan` CLI —— 薄包装器。

P1 只实现 `init`（确定性，零 LLM）。`ingest` / `query` / `check` 是 P2+，
由 `guanlan-wiki` skill 驱动 Agentao 完成，本文件先占位以给出清晰提示。
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .init import run_init


def _cmd_init(args: argparse.Namespace) -> int:
    result = run_init(args.path)
    rel = result.target
    if result.created:
        print(f"✓ 已在 {rel} 初始化观澜知识库：")
        for name in result.created:
            print(f"    + {name}")
    if result.skipped:
        print("  以下已存在，跳过（未覆盖）：")
        for name in result.skipped:
            print(f"    = {name}")
    if not result.created and result.skipped:
        print("知识库已存在，无新增文件。")
    print("\n下一步：把 .md 资料放进 raw/，然后 `guanlan ingest raw/<file>.md`（P2）。")
    return 0


def _cmd_todo(args: argparse.Namespace) -> int:
    print(
        f"`guanlan {args._name}` 属于 P2+，由 guanlan-wiki skill 驱动 Agentao 完成，"
        "当前 P1 骨架尚未实现。",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="guanlan",
        description="观澜 (GuānLán) —— 增量构建并维护结构化、互链的 markdown 知识 wiki。",
    )
    parser.add_argument("--version", action="version", version=f"guanlan {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="在目录生成最小知识库模板（确定性，零 LLM）")
    p_init.add_argument(
        "path", nargs="?", default=".", help="目标目录（默认当前目录）"
    )
    p_init.set_defaults(func=_cmd_init)

    # P2+ 占位：给出明确提示，而非 argparse 的未知命令报错。
    for name, help_text in (
        ("ingest", "摄入一篇 .md 资料（P2）"),
        ("query", "对知识库提问（P2）"),
        ("check", "确定性基础校验：frontmatter + 断链（P2）"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=_cmd_todo, _name=name)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
