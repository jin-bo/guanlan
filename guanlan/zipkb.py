"""知识库打包分享（P3.12，见 docs/P3.12-知识库打包分享.md）。**零 LLM，只读知识库。**

`guanlan zip` 把一个知识库打成可分享的 zip。核心是**白名单**：只收 `AGENTAO.md`、`SCHEMA.md`、
`wiki/`、`raw/`（`--no-raw` 不收）与可选的 `graph/`（`--with-graph`）；库根其余一切——`.env`、
`agentao.log*`、`.agentao/`（会话、记忆库、权限设置、工具输出）、`workspace/`、`.trash/`、用户随手放的脚本与
笔记——一律不收。黑名单只能挡住已知的私有文件，白名单对将来新出现的私有文件也默认不外泄。

白名单目录内再滤：路径任一段以 `.` 开头的（`.DS_Store`、`._*`、`.git/`…）、操作系统杂项
（`Thumbs.db`、`desktop.ini`、`__MACOSX/`、Office 锁文件 `~$*`、编辑器备份 `*.swp` / `*~`）、
符号链接（防把库外文件打进去；白名单目录**本身**是符号链接时照常跟进，如 `raw/` 指向库外存储）、
非普通文件。只按路径过滤，**不检查文件内容**。

zip 内顶层是一个以库名命名的目录；非 ASCII 文件名由 `zipfile` 自动置 UTF-8 标记（Windows 解压不乱码）；
条目按路径排序。先写同目录临时文件再改名，中断不留半个 zip；输出落在库内（含 `raw/` 指向的库外存储）即拒绝。
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .paths import require_kb_root

# 白名单：库根下只收这些。顺序即报告顺序。
INCLUDE_FILES = ("AGENTAO.md", "SCHEMA.md")
WIKI_DIR = "wiki"
RAW_DIR = "raw"
GRAPH_DIR = "graph"

# 操作系统 / 编辑器杂项（按小写文件名比对）。隐藏文件另由「以 . 开头」统一处理。
_JUNK_NAMES = frozenset({"thumbs.db", "ehthumbs.db", "desktop.ini", "__macosx", "icon\r"})
_JUNK_SUFFIXES = (".swp", ".swo", "~")

# 路径长度提醒线：zip 内路径超过它时，解压到 Windows 较深目录易超 260 字符上限。
LONG_PATH_WARN = 200

_CONTENT_NOTICE = (
    "ℹ 只按路径过滤，不检查文件内容：分享前请自行确认 AGENTAO.md、SCHEMA.md 与 wiki 正文不含不宜外传的信息。"
)


@dataclass
class ZipPlan:
    """打包清单（纯计算结果，未写盘）。路径均为相对库根的 posix 串。"""

    root: Path
    top: str  # zip 内顶层目录名 = 库名
    files: list[tuple[Path, str]] = field(default_factory=list)  # (磁盘路径, 相对库根路径)
    dirs: list[str] = field(default_factory=list)  # 收入的目录（相对库根），含空目录
    total_bytes: int = 0
    per_dir: dict[str, int] = field(default_factory=dict)  # 白名单项 → 收入文件数
    excluded_top: list[str] = field(default_factory=list)  # 库根下不在白名单的项（目录带 /）
    hidden: list[str] = field(default_factory=list)
    junk: list[str] = field(default_factory=list)
    symlinks: list[str] = field(default_factory=list)
    special: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)  # os.walk 读不了的目录（无权限等）：不报会沉默漏收

    def arcname(self, rel: str) -> str:
        return f"{self.top}/{rel}"

    @property
    def longest(self) -> str:
        names = [self.arcname(rel) for _, rel in self.files]
        return max(names, key=len) if names else ""


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _is_junk(name: str) -> bool:
    low = name.lower()
    return low in _JUNK_NAMES or name.startswith("~$") or low.endswith(_JUNK_SUFFIXES)


def build_plan(root: Path, *, include_raw: bool = True, include_graph: bool = False) -> ZipPlan:
    """按白名单 + 路径过滤算出打包清单。只读、不跟随（白名单目录本身以外的）符号链接。"""
    plan = ZipPlan(root=root, top=root.name or "kb")
    dirs = [WIKI_DIR] + ([RAW_DIR] if include_raw else []) + ([GRAPH_DIR] if include_graph else [])
    allowed = set(INCLUDE_FILES) | set(dirs)

    for entry in sorted(os.listdir(root)):
        if entry not in allowed:
            plan.excluded_top.append(entry + ("/" if (root / entry).is_dir() else ""))

    for name in INCLUDE_FILES:
        p = root / name
        if p.is_symlink():
            plan.symlinks.append(name)
        elif p.is_file():
            plan.files.append((p, name))
            plan.total_bytes += p.stat().st_size
            plan.per_dir[name] = 1

    for top in dirs:
        base = root / top
        if not base.is_dir():  # 不存在（或是文件）→ 不收；raw/ 为空也会作为空目录收入
            continue
        count = 0
        plan.dirs.append(top)
        # 白名单目录本身是符号链接（如 raw/ 指向库外存储）时 os.walk 照常进入；其内的符号链接一律不跟。
        def _onerror(err: OSError, _base: Path = base, _top: str = top) -> None:
            rel = Path(_top, os.path.relpath(err.filename or str(_base), _base)).as_posix()
            plan.unreadable.append(rel + "/")

        for dirpath, dirnames, filenames in os.walk(base, onerror=_onerror, followlinks=False):
            rel_dir = Path(top, os.path.relpath(dirpath, base)).as_posix()  # pathlib 自动去掉 "."
            keep = []
            for d in sorted(dirnames):
                rel = f"{rel_dir}/{d}"
                if os.path.islink(os.path.join(dirpath, d)):
                    plan.symlinks.append(rel + "/")
                elif _is_hidden(d):
                    plan.hidden.append(rel + "/")
                elif _is_junk(d):
                    plan.junk.append(rel + "/")
                else:
                    keep.append(d)
                    plan.dirs.append(rel)
            dirnames[:] = keep
            for f in sorted(filenames):
                rel = f"{rel_dir}/{f}"
                path = Path(dirpath, f)
                st = os.lstat(path)
                if stat.S_ISLNK(st.st_mode):
                    plan.symlinks.append(rel)
                elif not stat.S_ISREG(st.st_mode):
                    plan.special.append(rel)
                elif _is_hidden(f):
                    plan.hidden.append(rel)
                elif _is_junk(f):
                    plan.junk.append(rel)
                else:
                    plan.files.append((path, rel))
                    plan.total_bytes += st.st_size
                    count += 1
        plan.per_dir[top + "/"] = count

    plan.files.sort(key=lambda x: x[1])
    plan.dirs.sort()
    return plan


def default_output(root: Path, cwd: Path | None = None) -> Path:
    """默认输出：当前目录下 `<库名>-YYYYMMDD.zip`；当前目录在库内（常见：先 cd 进库再跑）时改放库的上一级目录。"""
    here = (cwd or Path.cwd()).resolve()
    if here == root or root in here.parents:
        here = root.parent
    return here / f"{root.name or 'kb'}-{time.strftime('%Y%m%d')}.zip"


def _check_output(out: Path, root: Path, *, force: bool, dry_run: bool = False) -> Path:
    """输出路径准入：不得落在库内（含白名单目录经符号链接指向的库外存储），父目录须存在，已存在须 --force（预览不查）。"""
    out = out.expanduser()
    resolved = out.resolve() if out.exists() else out.parent.resolve() / out.name
    forbidden = [root]
    for name in (WIKI_DIR, RAW_DIR, GRAPH_DIR):
        d = root / name
        if d.is_dir():
            forbidden.append(d.resolve())
    for base in forbidden:
        if resolved == base or base in resolved.parents:
            raise GuanlanError(
                f"输出路径 {resolved} 位于知识库内，请输出到库外（否则会把 zip 自己或半成品打进库里）。",
                exit_code=EXIT_USAGE,
            )
    if not resolved.parent.is_dir():
        raise GuanlanError(f"输出目录不存在：{resolved.parent}", exit_code=EXIT_USAGE)
    if resolved.is_dir():
        raise GuanlanError(f"{resolved} 是目录，请给出 zip 文件路径。", exit_code=EXIT_USAGE)
    if resolved.exists() and not force and not dry_run:
        raise GuanlanError(f"{resolved} 已存在；如要覆盖请加 --force。", exit_code=EXIT_USAGE)
    return resolved


def _dir_info(arc: str, mtime: float) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(arc.rstrip("/") + "/", date_time=_zip_time(mtime))
    zi.external_attr = (0o40755 << 16) | 0x10  # unix 目录权限 + MS-DOS 目录位
    return zi


def _zip_time(mtime: float) -> tuple[int, int, int, int, int, int]:
    t = time.localtime(mtime)
    stamp = (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
    return min(max(stamp, (1980, 1, 1, 0, 0, 0)), (2107, 12, 31, 23, 59, 59))  # zip 时间戳只能表示 1980–2107


def write_zip(plan: ZipPlan, out: Path) -> int:
    """写 zip（同目录临时文件 → 原子改名）。返回压缩后字节数。"""
    tmp = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
            zf.writestr(_dir_info(plan.top, plan.root.stat().st_mtime), b"")
            entries = [(rel, None) for rel in plan.dirs] + [(rel, path) for path, rel in plan.files]
            for rel, path in sorted(entries, key=lambda x: x[0]):
                if path is None:
                    zf.writestr(_dir_info(plan.arcname(rel), (plan.root / rel).stat().st_mtime), b"")
                else:
                    zf.write(path, plan.arcname(rel))
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return out.stat().st_size


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _sample(items: list[str], k: int = 5) -> str:
    head = "、".join(items[:k])
    return head + (f" 等 {len(items)} 项" if len(items) > k else "")


def format_report(plan: ZipPlan, out: Path | None, compressed: int | None) -> str:
    n = len(plan.files)
    if out is None:
        lines = [f"预览（未写入）：将打包 {n} 个文件，{_fmt_size(plan.total_bytes)}。"]
    else:
        lines = [f"✓ 已打包 {out}：{n} 个文件，{_fmt_size(plan.total_bytes)}（压缩后 {_fmt_size(compressed or 0)}）。"]
    included = "、".join(f"{k}（{v} 个文件）" if k.endswith("/") else k for k, v in plan.per_dir.items())
    lines.append(f"  收入：{included or '（无）'}")
    if plan.excluded_top:
        lines.append(f"  排除（不在分享白名单内）：{'、'.join(plan.excluded_top)}")
    for label, items in (
        ("跳过隐藏文件", plan.hidden),
        ("跳过系统杂项", plan.junk),
        ("跳过符号链接（不跟随，防把库外文件打进去）", plan.symlinks),
        ("跳过非普通文件", plan.special),
        ("⚠ 无法读取、未收入的目录", plan.unreadable),
    ):
        if items:
            lines.append(f"  {label} {len(items)} 个：{_sample(items)}")
    longest = plan.longest
    if len(longest) > LONG_PATH_WARN:
        lines.append(
            f"  ⚠ 最长路径 {len(longest)} 字符：{longest}\n"
            "    在 Windows 上解压到较深目录可能超过 260 字符上限，建议解压到 C:\\kbs 这类短路径。"
        )
    lines.append(_CONTENT_NOTICE)
    return "\n".join(lines)


def format_json(plan: ZipPlan, out: Path | None, compressed: int | None, *, dry_run: bool) -> str:
    longest = plan.longest
    data = {
        "output": str(out) if out else None,
        "dry_run": dry_run,
        "files": len(plan.files),
        "bytes": plan.total_bytes,
        "compressed_bytes": compressed,
        "included": plan.per_dir,
        "excluded_top": plan.excluded_top,
        "hidden": plan.hidden,
        "junk": plan.junk,
        "symlinks": plan.symlinks,
        "special": plan.special,
        "unreadable": plan.unreadable,
        "longest_path": {"chars": len(longest), "path": longest},
        "entries": [plan.arcname(rel) for _, rel in plan.files],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def zip_entrypoint(
    root_dir: str | Path,
    *,
    output: str | Path | None = None,
    include_raw: bool = True,
    include_graph: bool = False,
    dry_run: bool = False,
    force: bool = False,
    json_output: bool = False,
) -> int:
    """`guanlan zip` 的单一落地：校验 → 算清单 →（非 dry-run 则写）→ 报告。"""
    try:
        root = require_kb_root(root_dir, writable=False)
        out = _check_output(Path(output) if output else default_output(root), root, force=force, dry_run=dry_run)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    plan = build_plan(root, include_raw=include_raw, include_graph=include_graph)
    try:
        compressed = None if dry_run else write_zip(plan, out)
    except OSError as exc:  # 文件无读权限、清单算出后被删改等：给可读报错，临时文件已由 write_zip 清掉
        print(f"打包失败：{exc}", file=sys.stderr)
        return EXIT_USAGE
    shown = None if dry_run else out
    print(format_json(plan, shown, compressed, dry_run=dry_run) if json_output else format_report(plan, shown, compressed))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.zipkb` 入口（与 `guanlan zip` 共享 zip_entrypoint）。"""
    parser = argparse.ArgumentParser(prog="python -m guanlan.zipkb", description="把知识库打包成可分享的 zip（零 LLM）。")
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    add_zip_arguments(parser)
    args = parser.parse_args(argv)
    return run_from_args(args)


def add_zip_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "-o",
        "--output",
        help="输出 zip 路径（默认 <库名>-YYYYMMDD.zip，放当前目录；当前目录在库内时放库的上一级目录；不得在库内）",
    )
    p.add_argument("--no-raw", action="store_true", help="不收 raw/（只分享 wiki，如原始资料有版权顾虑）")
    p.add_argument("--with-graph", action="store_true", help="同时收入 graph/（可由 `guanlan graph` 重新生成，默认不收）")
    p.add_argument("--dry-run", action="store_true", help="只预览将收入与排除的内容，不写 zip")
    p.add_argument("--force", action="store_true", help="输出文件已存在时覆盖")
    p.add_argument("--json", action="store_true", help="输出结构化 JSON（含完整文件清单）")


def run_from_args(args: argparse.Namespace) -> int:
    return zip_entrypoint(
        args.dir,
        output=args.output,
        include_raw=not args.no_raw,
        include_graph=args.with_graph,
        dry_run=args.dry_run,
        force=args.force,
        json_output=args.json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
