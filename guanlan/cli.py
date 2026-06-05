"""`guanlan` CLI —— 薄包装器。

`init` 确定性、零 LLM；`ingest` / `query` 经 `guanlan-wiki` skill 驱动 Agentao 完成并强制
确定性门禁；`check` 是独立的零 LLM 校验。业务智能在 skill 与门禁里，本文件只做 argparse 接线。
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .check import check_entrypoint
from .errors import EXIT_USAGE, GuanlanError
from .graph import graph_entrypoint
from .health import health_entrypoint
from .ingest import run_ingest
from .init import run_init
from .lint import lint_entrypoint
from .query import run_query
from .skill import install_skill


def _cmd_init(args: argparse.Namespace) -> int:
    # 目标目录：显式位置参数 path 优先；否则用全局 -C/--dir；都没有则当前目录。
    # 这样 `guanlan -C /kb init` / `guanlan init -C /kb` / `guanlan init /kb` 都生效，
    # 不会出现"给了 -C 却静默初始化当前目录"。
    target = args.path if args.path is not None else getattr(args, "dir", ".")
    result = run_init(target)
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
    print("\n下一步：把 .md 资料放进 raw/，然后 `guanlan ingest raw/<file>.md`。")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    return run_ingest(args.target, root=args.dir, model=args.model)


def _cmd_query(args: argparse.Namespace) -> int:
    return run_query(
        args.question, root=args.dir, backfill=args.backfill, model=args.model
    )


def _cmd_check(args: argparse.Namespace) -> int:
    return check_entrypoint(args.dir, json_output=args.json)


def _cmd_health(args: argparse.Namespace) -> int:
    return health_entrypoint(args.dir, json_output=args.json, strict=args.strict)


def _cmd_lint(args: argparse.Namespace) -> int:
    return lint_entrypoint(args.dir, json_output=args.json, strict=args.strict)


def _cmd_graph(args: argparse.Namespace) -> int:
    return graph_entrypoint(args.dir, json_only=args.json_only)


def _cmd_web(args: argparse.Namespace) -> int:
    # web 是可选叠加层：缺 `guanlan[web]`（fastapi/uvicorn 导入失败）时优雅降级、引导安装，
    # 不让 CLI 抛 traceback（决策P4-2）。导入收在函数内，核心命令不为 Web 背 import 成本。
    try:
        from .web import serve
    except ImportError:
        print(
            "`guanlan web` 需要可选依赖：请先 `pip install 'guanlan[web]'`。",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        return serve(
            args.dir,
            port=args.port,
            open_browser=not args.no_browser,
            model=args.model,
        )
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code


def _cmd_install_skill(args: argparse.Namespace) -> int:
    dest = install_skill(force=args.force)
    print(f"✓ guanlan-wiki skill 已就位：{dest}")
    print("  （ingest/query 在安装态下也会按需自动装入此处。)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="guanlan",
        description="观澜 (GuānLán) —— 增量构建并维护结构化、互链的 markdown 知识 wiki。",
    )
    parser.add_argument("--version", action="version", version=f"guanlan {__version__}")

    # -C/--dir 是全局选项，但 argparse 默认只在子命令**前**接受顶层选项。为同时支持
    # `guanlan -C /kb check`（git 风格）与 `guanlan check -C /kb`（更自然），把它放到一个
    # 共享父解析器，顶层与各子命令都继承；默认用 SUPPRESS 避免子解析器覆盖顶层已解析的值，
    # 最终在 main 里统一回落到当前目录。init 也继承，作为 path 之外的目标来源（见 _cmd_init）。
    dir_parent = argparse.ArgumentParser(add_help=False)
    dir_parent.add_argument(
        "-C",
        "--dir",
        default=argparse.SUPPRESS,
        help="知识库根目录（默认当前目录），便于在库外调用。",
    )
    parser.add_argument(
        "-C",
        "--dir",
        default=argparse.SUPPRESS,
        help="知识库根目录（默认当前目录）；亦可置于子命令之后。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init", parents=[dir_parent], help="在目录生成最小知识库模板（确定性，零 LLM）"
    )
    p_init.add_argument(
        "path", nargs="?", default=None, help="目标目录（默认 -C/--dir 或当前目录）"
    )
    p_init.set_defaults(func=_cmd_init)

    p_ingest = sub.add_parser("ingest", parents=[dir_parent], help="摄入一篇 .md 资料")
    p_ingest.add_argument("target", help="raw/ 下的 .md 文件，如 raw/x.md")
    p_ingest.add_argument("--model", default=None, help="覆盖 Agentao 模型")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_query = sub.add_parser(
        "query", parents=[dir_parent], help="对知识库提问（默认只读）"
    )
    p_query.add_argument("question", help="问题文本")
    p_query.add_argument(
        "--backfill", action="store_true", help="把好答案回填 wiki/syntheses/（走完整门禁）"
    )
    p_query.add_argument("--model", default=None, help="覆盖 Agentao 模型")
    p_query.set_defaults(func=_cmd_query)

    p_check = sub.add_parser(
        "check", parents=[dir_parent], help="确定性基础校验：frontmatter + 断链 + sources（零 LLM）"
    )
    p_check.add_argument("--json", action="store_true", help="输出 JSON 契约")
    p_check.set_defaults(func=_cmd_check)

    p_health = sub.add_parser(
        "health", parents=[dir_parent], help="文件级结构体检：桩页 + index↔磁盘同步（零 LLM，建议非门禁）"
    )
    p_health.add_argument("--json", action="store_true", help="输出 JSON 契约")
    p_health.add_argument("--strict", action="store_true", help="有建议则以退出码 6 失败（供 CI/nightly）")
    p_health.set_defaults(func=_cmd_health)

    p_lint = sub.add_parser(
        "lint", parents=[dir_parent], help="图感知结构 lint：孤儿 / 断链 / 缺失实体（零 LLM，建议非门禁）"
    )
    p_lint.add_argument("--json", action="store_true", help="输出 JSON 契约")
    p_lint.add_argument("--strict", action="store_true", help="有建议则以退出码 6 失败（供 CI/nightly）")
    p_lint.set_defaults(func=_cmd_lint)

    # allow_abbrev=False：graph 无 stdout JSON 概念，故不让 `--json` 前缀静默命中 `--json-only`
    # （在 health/lint 里 `--json` 是"机器输出"，语义不同；与其静默别名不如直接报未知参数）。
    p_graph = sub.add_parser(
        "graph",
        parents=[dir_parent],
        allow_abbrev=False,
        help="确定性建图：[[wikilink]] → graph/graph.json + graph.html（零 LLM）",
    )
    p_graph.add_argument(
        "--json-only", action="store_true", help="只写 graph.json，跳过 graph.html"
    )
    p_graph.set_defaults(func=_cmd_graph)

    p_web = sub.add_parser(
        "web",
        parents=[dir_parent],
        help="起本地 Web 宿主（可选叠加层，需 `pip install 'guanlan[web]'`）",
    )
    p_web.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765，仅 127.0.0.1）")
    p_web.add_argument(
        "--no-browser", action="store_true", help="起服后不自动打开浏览器"
    )
    p_web.add_argument("--model", default=None, help="覆盖 Agentao 模型（透传写作业与会话）")
    p_web.set_defaults(func=_cmd_web)

    p_skill = sub.add_parser(
        "install-skill", help="把随包 guanlan-wiki skill 装入 ~/.agentao/skills/（外部库用）"
    )
    p_skill.add_argument(
        "--force", action="store_true", help="已存在也覆盖重装（默认保留用户改动）"
    )
    p_skill.set_defaults(func=_cmd_install_skill)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # -C/--dir 用 SUPPRESS 默认（顶层与子命令共享），未给时统一回落到当前目录。
    if not hasattr(args, "dir"):
        args.dir = "."
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
