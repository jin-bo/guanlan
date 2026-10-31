"""文件级结构健康检查（P3，见 docs/P3-健康与图谱.md §4）。**零 LLM。**

`health` 关注"知识库作为记账产物是否内部自洽"，与 `check`（frontmatter/链接是否合法）正交：

- **桩页 / 空页**（`health.stub_page`）：正文几乎没有内容的建库残留空壳。
- **index 与磁盘双向同步**（`health.index_missing_page` / `health.index_dangling`）。
- **source 页无原文**（`health.source_without_raw`）：`wiki/sources/` 下的页在 `raw/` 里找不到对应原文——
  文件名（与 `rawio.find_source_page` 同一套容错）、`raw_digest`、`> 原始资料：` 声明行三路都没指向现存
  raw 文件。成因实测三类：原文改过名却没留声明、多篇聚合页放进了 sources/、摄入时模型给资料里**被引用的
  文献**另建了 source 页（llm_wiki v0.6.13 反向评审 §1.A）。三类都让 `audit` 追不到原文。
- **页型↔目录一致性**（`health.type_dir_mismatch` / `health.uncharted_page`，docs/P3.10）：合法
  `type` 落错目录、或内容页游离于四规范目录之外——schema 漂移的零-LLM advisory。
- **overview 体积**（`health.overview_large`）：`overview.md` 超过 `OVERVIEW_ADVISORY_CHARS`。它没有任何
  体积约束、只随入库增长；ingest 已改为按标题结构分章节读，但综述本身越长越难维护。**只提示、绝不
  自动压缩或改写**——拆分与沉淀是内容判断（docs/backlog/notes/ingest-performance-research.md）。

findings 是**建议性**（决策P3-4）：默认退 0，`--strict` 下有 findings → `EXIT_LINT_FINDINGS(6)`。
frontmatter 走容错档（`pages.load_page`），坏数据不中断体检——正确性归口 `check`（决策P3-8）。

输出按 `pages.order_findings` 做**因果排序**（纯展示层、零 LLM、确定性）：数据完整性/结构同步类
（index 同步）先于内容/组织建议（桩页/页型漂移）——不改 finding 集合/退出码，只改顺序
（gbrain 反向评审 §3 借形状，见 docs/finding-因果排序.md）。
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .errors import EXIT_LINT_FINDINGS, EXIT_OK, GuanlanError
from .pages import (
    DIR_TO_TYPE,
    VALID_TYPES,
    Finding,
    index_sync_state,
    iter_pages,
    load_page,
    order_findings,
    report_json,
)
from .paths import require_kb_root
from .rawio import source_page_stems
from .sourcepage import claimed_raw_paths

__all__ = ["HealthReport", "run_health", "format_report", "health_entrypoint", "main"]

# 桩页阈值：实质正文（剥标题/空白后）字符数低于此值即疑为桩页。30 是"一两句完整中文"的下限——
# 比它更短的多是建库残留空壳。按 CJK 字符计（Python len 对每个汉字计 1）。SCHEMA 覆盖列为 P3 之后。
STUB_MIN_CHARS = 30

# overview 体积提示线（字符数，CJK 一字计 1）：约一篇长文。超出只报 advisory，不阻断、不改写。
OVERVIEW_ADVISORY_CHARS = 30_000

# ATX 标题行：1–6 个 # 后接空白或行尾（CommonMark）。`#无空格` 这类不是标题、算正文。
_ATX_HEADING_RE = re.compile(r"#{1,6}(\s|$)")


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


def _check_uncharted(page: str, dir_name: str | None) -> Finding | None:
    """游离目录检查（`health.uncharted_page`，决策P3.10-5）：内容页不在四规范目录之一。

    含直接躺 `wiki/` 根的非 config 页（`dir_name=None`）与 `wiki/misc/…` 这类杂目录。
    这正是 `reindex` 静默跳过的那批页——`health` 把它们显式列出，让人决定归位还是扩约定。
    """
    if dir_name is not None and dir_name in DIR_TO_TYPE:
        return None
    return Finding(
        page,
        "health.uncharted_page",
        "页不在 sources/entities/concepts/syntheses 任一目录，无法按约定归类",
    )


def _check_type_dir(page: str, dir_name: str | None, meta: dict | None) -> Finding | None:
    """页型↔目录一致性检查（`health.type_dir_mismatch`，决策P3.10-4/6）。

    **仅当** type 合法（∈ `VALID_TYPES`）且与目录约定不符才报。坏/缺 frontmatter（`meta=None`）、
    缺失/非法/非字符串 `type` 一律跳过、交 `check` 阻断（`missing_key`/`bad_type`）——不重复其职责。
    **直接读 `meta["type"]`**：`pages.page_type` 对非法字符串原样返回，拿来比对会把非法 type 误报成
    mismatch。游离目录（`dir_name` 非规范目录）的活归 `_check_uncharted`，此处放行。
    """
    if dir_name is None:
        return None
    expected = DIR_TO_TYPE.get(dir_name)
    if expected is None:
        return None  # 非规范目录 → uncharted 的活，不在此报。
    if not isinstance(meta, dict):
        return None  # 坏/缺 frontmatter 交 check。
    type_ = meta.get("type")
    if not isinstance(type_, str) or type_ not in VALID_TYPES:
        return None  # 缺失/非法/非字符串 type 交 check。
    if type_ == expected:
        return None
    return Finding(
        page,
        "health.type_dir_mismatch",
        f"页在 {dir_name}/ 目录但 type={type_}，与目录约定 type={expected} 不符",
    )


def _check_index_sync(wiki: Path, root: Path, content_pages: list[Path]) -> list[Finding]:
    """index 与磁盘双向存在性同步（§4.2），把 `pages.index_sync_state` 的结果包成 `Finding`。

    判定下沉到 `pages.index_sync_state` 单一归口（决策P3.4-4），与 `reindex` 共用、不分叉：
    - 磁盘有、index 无 → `health.index_missing_page`（记在该磁盘页上）；
    - index 有、磁盘无 → `health.index_dangling`（记在 `index.md` 上）。

    复用 `run_health` 已遍历的 `content_pages`（桩页检查同一份快照）：免二次 walk，且 missing
    判定与桩页检查用同一页集——不因两次 walk 之间的磁盘变动而内部不一致。
    """
    missing, dangling = index_sync_state(wiki, content_pages)

    findings: list[Finding] = [
        Finding(
            path.relative_to(root).as_posix(),
            "health.index_missing_page",
            "磁盘存在但未收录进 index.md",
        )
        for path in missing
    ]
    index_page = (wiki / "index.md").relative_to(root).as_posix()
    findings.extend(
        Finding(index_page, "health.index_dangling", f"index 链接 {target} 无对应文件")
        for target in dangling
    )
    return findings


def _raw_anchor_stems(root: Path) -> frozenset[str]:
    """全体 raw 文件可能对应的 source 页 stem（NFC 归一）。raw/ 缺失 → 空集。

    递归、跳过任一段以 `.` 开头的路径（隐藏文件、Finder `._*`、`.assets` 之类的旁挂目录）。不按扩展名
    过滤：`raw/` 里放 PDF 原件、`convert` 产物放 `.md` 都是合法形态，多认一个 stem 只会少报、不会误报。
    NFC 归一两边一起做：macOS 上 Finder 落盘的文件名可能是 NFD，逐字比会把已收录的页误报成无原文。
    跟随指向目录的符号链接（与 `gate.snapshot_raw` 同口径：`raw/` 下的链接目录是支持的配置；`rglob`
    不下降进链接目录，会把其中原文的页全误报），按真实路径防环；隐藏目录就地剪枝、不白遍历。
    """
    raw = root / "raw"
    if not raw.is_dir():
        return frozenset()
    stems: set[str] = set()
    seen: set[str] = set()
    # 能力上限：读不了的子目录（权限、链接指向未挂载的盘）被 os.walk 静默跳过，其中原文对应的页会误报；
    # 要提示须另立「raw 遍历不完整」一类 finding，未做（llm_wiki v0.6.13 反向评审 §5）。
    for dirpath, dirnames, filenames in os.walk(raw, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or not os.path.isfile(os.path.join(dirpath, name)):
                continue
            stem = Path(name).stem
            # 原名与其 NFC 形各推一遍：`raw_slug` 会把 NFD 的组合记号（`é` 拆出的 U+0301）当非词字符折成
            # `-`，故 NFD 原名推出的是 `Cafe-…`、NFC 原名推出的是 `Café-…`，两种页名都可能存在。
            for form in {stem, unicodedata.normalize("NFC", stem)}:
                stems.update(unicodedata.normalize("NFC", s) for s in source_page_stems(form))
    return frozenset(stems)


def _claim_hits_raw(root: Path, claimed: str) -> bool:
    """页内声明 / `raw_digest` 的路径是否指向现存的、非隐藏的 raw 文件。

    **只做字面准入、跟随符号链接**（与文件名一路、`gate.snapshot_raw` 同口径）：须是相对路径、以 `raw/`
    起、归一后不越出 `raw/`；`raw/` 下指向库外存储的链接照样算原文。不用 `provenance.admit_raw_path` 的
    realpath 准入——那是 `audit` 要**读内容喂 Agent** 时防穿越用的，health 只判存在、不读内容，没有这个面；
    沿用它反而让同一份原文经文件名算有、经声明算无。

    与文件名一路同口径：任一段以 `.` 开头不算原文（否则一行声明就能把 `raw/.cache/` 里的文件认成原文，
    吞掉真孤儿）。逐字找不到时再按**目录里实际文件名的 NFC 形**逐段比一次：Linux 不做规范化，NFD 落盘的
    原文与 NFC 写的声明逐字对不上；macOS APFS 查找本就不分规范化，规范化差异由此两平台一致。
    **大小写不在此列**：逐字那一步在 macOS 上不分大小写、Linux 上分，声明 `raw/Report.md` 对盘上
    `raw/report.md` 两平台结论不同（与文件名一路的「不折大小写」同一能力上限）。
    """
    if not isinstance(claimed, str) or claimed.startswith("/") or "\\" in claimed:
        return False
    norm = posixpath.normpath(claimed)
    if not norm.startswith("raw/"):
        return False  # `raw/../x` 归一后塌成 `x`，在此挡下
    parts = norm.split("/")[1:]
    if any(part.startswith(".") for part in parts):
        return False
    try:
        # 3.10/3.11 的 `Path.is_file` 对 ENAMETOOLONG 等会抛（模型臆造的超长声明路径），当没命中。
        if (root / norm).is_file():
            return True
        current = root / "raw"
        for part in parts:
            want = unicodedata.normalize("NFC", part)
            match = next(
                (c for c in current.iterdir() if unicodedata.normalize("NFC", c.name) == want),
                None,
            )
            if match is None:
                return False
            current = match
        return current.is_file()
    except OSError:
        return False


def _check_source_without_raw(
    root: Path,
    page: str,
    page_path: Path,
    meta: dict | None,
    body: str,
    anchors: frozenset[str],
    *,
    top_level: bool,
) -> Finding | None:
    """source 页无原文检查：文件名 / `raw_digest` / 原始资料声明三路都没指向现存 raw 文件即报。

    **能力上限**：文件名一路按 `source_page_stems` 的三种形精确比（NFC 后），不折大小写——Linux 上
    `Report.md` 与 `raw/report.md` 本就互不认领，macOS 大小写不敏感 FS 上 ingest 会认而这里会报，
    这类页手动改名或补一行声明即可。只扫 `wiki/sources/` 目录，不看 `type`：`type: source` 却放错
    目录的页归 `type_dir_mismatch`。

    文件名一路**只认 `sources/` 第一层**：`find_source_page` 只找 `sources/<stem>.md`，子目录里的同名页
    ingest 与 Web 都判「未收录」，这里若按 stem 认就成了反向漏报。子目录页仍可经声明 / `raw_digest` 认领。
    """
    if top_level and unicodedata.normalize("NFC", page_path.stem) in anchors:
        return None
    if any(_claim_hits_raw(root, claimed) for claimed in claimed_raw_paths(meta, body)):
        return None
    return Finding(
        page,
        "health.source_without_raw",
        "raw/ 中找不到对应原文（文件名、raw_digest、「原始资料」声明都未命中）。"
        "原文改过名 → 页内补 `> 原始资料：` 声明行；多篇聚合 → 移到 syntheses/；"
        "被引文献误建 → 并入引用它的摘要页后删除",
    )


def _check_overview_size(wiki: Path, root: Path) -> Finding | None:
    path = wiki / "overview.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None  # 缺 overview 不归本检查管
    if len(text) <= OVERVIEW_ADVISORY_CHARS:
        return None
    return Finding(
        path.relative_to(root).as_posix(),
        "health.overview_large",
        f"overview.md {len(text):,} 字，超过 {OVERVIEW_ADVISORY_CHARS:,} 字提示线："
        "可考虑把成熟专题沉淀为 syntheses/ 页、overview 只留主线（不会自动改写）",
    )


def run_health(wiki: Path) -> HealthReport:
    """对 `wiki/` 下非 config 页跑文件级体检，返回 `HealthReport`。

    扫描范围同 `check`：非 config 页参与桩页检查；`index.md` 仅作 index 同步的参照物被读取。
    """
    wiki = Path(wiki)
    root = wiki.parent
    content_pages = list(iter_pages(wiki))
    anchors: frozenset[str] | None = None  # 惰性：库里没有 source 页就不遍历 raw/

    findings: list[Finding] = []
    for path in content_pages:
        page = path.relative_to(root).as_posix()
        meta, body = load_page(path)  # 容错档：坏 frontmatter 不抛。
        # 一级目录名；不足两段（直接躺 wiki/ 根）→ None，交 uncharted 检查。
        parts = path.relative_to(wiki).parts
        dir_name = parts[0] if len(parts) >= 2 else None
        stub = _check_stub(page, body)
        if stub is not None:
            findings.append(stub)
        if dir_name == "sources":
            if anchors is None:
                anchors = _raw_anchor_stems(root)
            orphan_source = _check_source_without_raw(
                root, page, path, meta, body, anchors, top_level=len(parts) == 2
            )
            if orphan_source is not None:
                findings.append(orphan_source)
        # uncharted 与 type_dir_mismatch 互斥：前者只在非规范目录报、后者只在规范目录报。
        uncharted = _check_uncharted(page, dir_name)
        if uncharted is not None:
            findings.append(uncharted)
        else:
            mismatch = _check_type_dir(page, dir_name, meta)
            if mismatch is not None:
                findings.append(mismatch)
    findings.extend(_check_index_sync(wiki, root, content_pages))
    overview = _check_overview_size(wiki, root)
    if overview is not None:
        findings.append(overview)

    # finding 因果排序（纯展示层、零 LLM）：数据完整性/结构同步类先于内容/组织建议。稳定排序保
    # 既有逐页/逐项确定性次序不变（gbrain §3，见 pages.order_findings）。
    return HealthReport(
        ok=not findings,
        pages_checked=len(content_pages),
        findings=order_findings(findings),
    )


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
        description="文件级结构体检：桩页 + index↔磁盘同步 + source 页原文（零 LLM，建议非门禁）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 契约")
    parser.add_argument("--strict", action="store_true", help="有建议则以退出码 6 失败（供 CI/nightly）")
    args = parser.parse_args(argv)
    return health_entrypoint(args.dir, json_output=args.json, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
