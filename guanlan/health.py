"""文件级结构健康检查（P3，见 docs/P3-健康与图谱.md §4）。**零 LLM。**

`health` 关注"知识库作为记账产物是否内部自洽"，与 `check`（frontmatter/链接是否合法）正交：

- **桩页 / 空页**（`health.stub_page`）：正文几乎没有内容的建库残留空壳。
- **index 与磁盘双向同步**（`health.index_missing_page` / `health.index_dangling`）。

findings 是**建议性**（决策P3-4）：默认退 0，`--strict` 下有 findings → `EXIT_LINT_FINDINGS(6)`。
frontmatter 走容错档（`pages.load_page`），坏数据不中断体检——正确性归口 `check`（决策P3-8）。
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import EXIT_LINT_FINDINGS, EXIT_OK, GuanlanError
from .pages import Finding, index_md_links, iter_pages, load_page, report_json
from .paths import require_kb_root

__all__ = ["HealthReport", "run_health", "format_report", "health_entrypoint", "main"]

# 桩页阈值：实质正文（剥标题/空白后）字符数低于此值即疑为桩页。30 是"一两句完整中文"的下限——
# 比它更短的多是建库残留空壳。按 CJK 字符计（Python len 对每个汉字计 1）。SCHEMA 覆盖列为 P3 之后。
STUB_MIN_CHARS = 30

# ATX 标题行：1–6 个 # 后接空白或行尾（CommonMark）。`#无空格` 这类不是标题、算正文。
_ATX_HEADING_RE = re.compile(r"#{1,6}(\s|$)")


def _target_within_wiki(target: str) -> bool:
    """index 链接目标是否安全落在 `wiki/` 内（拒绝绝对路径与 `..` 越界）。

    与 `check` 的 sources 防越界一致：否则 `../outside.md` / `/tmp/x.md` 会让 `(wiki/target)`
    解析到库外、若该文件恰好存在则误判 index 链接"有对应文件"、漏报 `index_dangling`。
    """
    if target.startswith("/"):
        return False
    norm = posixpath.normpath(target)
    return norm != ".." and not norm.startswith("../")


@dataclass
class HealthReport:
    ok: bool
    pages_checked: int
    findings: list[Finding]


def _substantive_char_count(body: str) -> int:
    """正文剥去标题行（`#`/`##` 开头）与所有空白后的实质字符数。"""
    chars = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or _ATX_HEADING_RE.match(stripped):
            continue  # 空行与 ATX 标题行不计入实质正文。
        chars += len("".join(stripped.split()))  # 去掉行内空白后计数。
    return chars


def _check_stub(page: str, body: str) -> Finding | None:
    """桩页 / 空页检查：实质正文 < STUB_MIN_CHARS（含空/仅标题）即记一条建议。"""
    n = _substantive_char_count(body)
    if n >= STUB_MIN_CHARS:
        return None
    detail = (
        "正文为空或仅含标题，疑为桩页"
        if n == 0
        else f"正文仅 {n} 字（< {STUB_MIN_CHARS}），疑为桩页"
    )
    return Finding(page, "health.stub_page", detail)


def _check_index_sync(wiki: Path, root: Path, content_pages: list[Path]) -> list[Finding]:
    """index 与磁盘双向存在性同步（§4.2）。**只做存在性，不校验分区/type 匹配。**

    解析 `index.md` 的 markdown 链接目标（相对 `wiki/` 的 posix 路径）：
    - 磁盘有、index 无 → `health.index_missing_page`（记在该磁盘页上）；
    - index 有、磁盘无 → `health.index_dangling`（记在 `index.md` 上）。
    """
    index_path = wiki / "index.md"
    index_text = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    linked = index_md_links(index_text)  # index 收录的目标集合（相对 wiki/）。

    findings: list[Finding] = []
    for path in content_pages:
        rel = path.relative_to(wiki).as_posix()
        if rel not in linked:
            findings.append(
                Finding(
                    path.relative_to(root).as_posix(),
                    "health.index_missing_page",
                    "磁盘存在但未收录进 index.md",
                )
            )
    index_page = index_path.relative_to(root).as_posix()
    for target in sorted(linked):
        # 越界/绝对路径目标视作悬空（不让 is_file 跟随 .. 命中库外文件而漏报）。
        if not _target_within_wiki(target) or not (wiki / target).is_file():
            findings.append(
                Finding(index_page, "health.index_dangling", f"index 链接 {target} 无对应文件")
            )
    return findings


def run_health(wiki: Path) -> HealthReport:
    """对 `wiki/` 下非 config 页跑文件级体检，返回 `HealthReport`。

    扫描范围同 `check`：非 config 页参与桩页检查；`index.md` 仅作 index 同步的参照物被读取。
    """
    wiki = Path(wiki)
    root = wiki.parent
    content_pages = list(iter_pages(wiki))

    findings: list[Finding] = []
    for path in content_pages:
        page = path.relative_to(root).as_posix()
        _meta, body = load_page(path)  # 容错档：坏 frontmatter 不抛。
        stub = _check_stub(page, body)
        if stub is not None:
            findings.append(stub)
    findings.extend(_check_index_sync(wiki, root, content_pages))

    return HealthReport(ok=not findings, pages_checked=len(content_pages), findings=findings)


def format_report(report: HealthReport, *, json_output: bool) -> str:
    """渲染体检结果：`--json` 走稳定契约；否则人类可读逐行报告。"""
    if json_output:
        return report_json(
            ok=report.ok,
            pages_checked=report.pages_checked,
            items_key="findings",
            items=report.findings,
        )

    if report.ok:
        return f"✓ health 通过：{report.pages_checked} 页，无结构建议。"
    lines = [f"· health 体检：{report.pages_checked} 页，{len(report.findings)} 条建议："]
    for f in report.findings:
        where = f.page or "(全局)"
        lines.append(f"    [{f.kind}] {where}: {f.detail}")
    return "\n".join(lines)


def health_entrypoint(root_dir: str | Path, *, json_output: bool, strict: bool) -> int:
    """`guanlan health` 的单一落地：体检 → 渲染 → 退出码。

    默认退 `EXIT_OK`（findings 是建议非门禁，决策P3-4）；`--strict` 且有 findings → `EXIT_LINT_FINDINGS`。
    """
    try:
        root = require_kb_root(root_dir, writable=False)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code

    report = run_health(root / "wiki")
    print(format_report(report, json_output=json_output))
    if strict and not report.ok:
        return EXIT_LINT_FINDINGS
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.health` 入口（与 `guanlan health` 共享 health_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.health",
        description="文件级结构体检：桩页 + index↔磁盘同步（零 LLM，建议非门禁）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 契约")
    parser.add_argument("--strict", action="store_true", help="有建议则以退出码 6 失败（供 CI/nightly）")
    args = parser.parse_args(argv)
    return health_entrypoint(args.dir, json_output=args.json, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
