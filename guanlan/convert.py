"""多格式摄入 `guanlan convert`（P5.2，见 docs/P5.2-多格式摄入.md）。**脚本零 LLM、宿主写 `raw/`。**

`guanlan convert path/to/报告.pdf`：把 PDF/DOCX/PPTX/XLSX/HTML/图片… 经**既有
`pdf-to-markdown` skill 的 `convert.py`**（MinerU→marker→pypdf 分层兜底、已随 wheel 全局安装）
转成 markdown，落成 `raw/<slug>.md` 源（含 `origin` provenance），**复用 `.md` 单格式 ingest
不动、复用 P4.6 的 raw 写入规则**（`rawio` 归口）。这补的是 CLI 那个洞——当前 `ingest` 仍硬拒
非 `.md`，命令行用户没有官方的 `PDF/DOCX → raw/*.md → ingest` 路径（决策P5.2-1）。

要点：
- **两步默认、不自动 ingest**（决策P5.2-1/8）：`convert` 只做「转换 + 落源」；建页那步仍由
  独立的 `guanlan ingest raw/<slug>.md`（走 Agentao + gate）完成。`--ingest` 是可选便利串联。
- **纯宿主写 `raw/`、零 LLM、不起 Agentao、不取 raw 快照**（决策P5.2-3）：是「人投喂源」（与
  P4.1 投喂同性质），不走 ingest 的 `run_guarded_write` 子进程 + 快照门禁。
- **「脚本零 LLM」管 guanlan 自身**（决策P5.2-4）：本模块**不内嵌 LLM 客户端/密钥**，只 shell
  out 到外部转换进程；该进程（如 marker）是否用 LLM 增强、用谁的 key，由**用户环境**决定，
  与 guanlan 正交——故**不做 env-scrub、不暴露 `--model`、不向 skill 透传 model**。
- **无新依赖、无新 extra**（决策P5.2-5）：转换后端复用 skill 既有分层兜底与 graceful degrade。
- **内核 / 命令壳分立**（决策P5.2-9）：`convert_to_markdown`（内核）与 `run_convert`（命令壳）
  分立，供将来 Web 机会性优化直接复用内核（仿 P5.0 `search_pages` 被 P5.1 复用，不单列阶段）。
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .imageio import (
    IMAGE_EXTS,  # noqa: F401 — 历史 re-export（测试 / 文档对齐口径）
    MAX_IMAGE_BYTES,  # noqa: F401
    MAX_IMAGE_COUNT,  # noqa: F401
    MAX_IMAGES_TOTAL_BYTES,  # noqa: F401
    ConvertedImage,  # noqa: F401 — 历史 re-export（tests/test_convert 直接 import）
    ConvertError,
    ConvertResult,
    _admit_image_ref,  # noqa: F401 — 历史 re-export（tests 直接 import）
    _images_dir,  # noqa: F401
    _rollback_images,
    _stage_and_swap_images,
    collect_and_rewrite,
)
from .ingest import run_ingest
from .paths import require_kb_root
from .rawio import apply_origin, atomic_write_raw, check_text_admission, safe_raw_target
from .skill import bundled_skill_dir

# skill `convert.py` 支持的后端（透传给 skill；格式白名单归口在 skill，不在此重复，决策P5.2-2/5）。
_BACKENDS = ("auto", "mineru", "marker", "python")

# 同名已存在文案归口（早预检 + atomic_write_raw TOCTOU 回退两处共用，免改一处漏一处）。
_RAW_EXISTS_MSG = "raw/{name} 已存在；改名（`--name`）或加 `--overwrite` 覆盖。"


def _collect_and_rewrite_images(
    markdown: str, *, produced_md: Path, tmp_root: Path, stem: str
) -> ConvertResult:
    """历史名 + 历史签名（`tmp_root=`）的薄壳，委派 `imageio.collect_and_rewrite`（决策P4.6.1-4）。

    P4.6.1 把图片原语抽到 `guanlan/imageio.py` 归口；本壳保留旧名/旧关键字供 `tests/test_convert`
    与转换内核原样调用，行为字节不变。
    """
    return collect_and_rewrite(markdown, produced_md=produced_md, root=tmp_root, stem=stem)


def _skill_convert_script() -> Path:
    """定位 `pdf-to-markdown` skill 的 `scripts/convert.py`（复用 `skill.py` 的归口）。

    `bundled_skill_dir("pdf-to-markdown")` 解析安装态 `guanlan/_skill/` 或开发期仓库根
    `skills/`，取其 `scripts/convert.py`。找不到 → `ConvertError`（命令层 `EXIT_USAGE`，提示重装）。
    """
    try:
        script = bundled_skill_dir("pdf-to-markdown") / "scripts" / "convert.py"
    except FileNotFoundError as exc:
        raise ConvertError(
            f"找不到随包 pdf-to-markdown skill：{exc}。请 `guanlan install-skill --force` 重装。"
        ) from None
    if not script.is_file():
        raise ConvertError(
            f"pdf-to-markdown skill 缺 scripts/convert.py：{script}。"
            "请 `guanlan install-skill --force` 重装。"
        )
    return script


def _run_converter(
    cmd: list[str], *, cwd: str, progress: Callable[[str], None] | None
) -> tuple[int, str, str]:
    """跑 skill 转换子进程，返回 `(returncode, stdout, stderr)`（决策P4.6.1-10）。

    `progress` 为 None（CLI convert）时走 `subprocess.run(capture_output=True)`——**与原实现字节
    等价**（决策P5.2-4 向后兼容）。给定时（Web 解析）改 `Popen`：**两条管道各起一条 drain 线程
    并发读**（避免一管道写满阻塞另一管道造成死锁），stderr 行实时回调 `progress(line)`（marker/
    mineru 分级回退日志在 stderr）、同时整体累计，stdout 累计到末行取产物路径。stdout 不回调
    （只末行有用），但仍 drain 以防满管道阻塞。
    """
    # `text=True`（不指定 `encoding=`）按 **locale** 解码子进程管道——刻意与 skill 子进程 `print(out)`
    # 的 locale 编码**对齐**：matched-locale（含非 UTF-8 如 `LANG=zh_CN.GBK`）下中文产物路径逐字往返。
    # 注：曾试图强制 `encoding="utf-8"` 修「非 UTF-8 locale」，反而打断这条 matched-locale 往返
    # （skill 裸 print 发 GBK 字节、父强解 UTF-8 → 乱码 → ConvertError），且 stderr surrogateescape 会
    # 漏出孤 surrogate 把 Web 解析端点 500（反向评审 code-review 抓出）——已回退。真正的「子进程两端
    # 协同强制 UTF-8 + stderr errors=replace + pypdf/LANG parity 测试」见 backlog 审计 note。
    if progress is None:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    out_lines: list[str] = []
    err_lines: list[str] = []

    def _drain_stdout() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            out_lines.append(line)

    def _drain_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            err_lines.append(line)
            with contextlib.suppress(Exception):  # 回调失败绝不拖垮 drain / 子进程收尾
                progress(line.rstrip("\n"))

    t_out = threading.Thread(target=_drain_stdout, daemon=True)
    t_err = threading.Thread(target=_drain_stderr, daemon=True)
    t_out.start()
    t_err.start()
    proc.wait()
    t_out.join()
    t_err.join()
    return proc.returncode, "".join(out_lines), "".join(err_lines)


def convert_to_markdown(
    src: Path,
    *,
    stem: str,
    backend: str = "auto",
    cwd: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> ConvertResult:
    """**转换内核**：把一个多格式文件转成 markdown + 随源图片，零 LLM。复用 skill `convert.py` 当引擎。

    `stem` = 最终 raw slug（命令壳传 `target.stem`），用于图片重命名与引用重写口径对齐 md 文件名
    （决策P5.2.1-4）。`cwd` = 子进程工作目录 / skill `.env` 发现锚点（`run_convert` 传 **KB root**；
    None → 回退 `Path.cwd()`）。`progress`（决策P4.6.1-10）：None=CLI，走 `subprocess.run`、行为字节
    不变；给定=Web 解析，走 `Popen` 边读 stderr 行边回调（分级回退日志流式可见，两管道并发 drain
    防死锁）——见 `_run_converter`。

    1. **temp 暂存**：把 src 复制进 `tempfile.TemporaryDirectory`、得**绝对路径** `staged`
       （skill `convert.py` 把产物写在*输入文件同目录*、无 `--output_dir`——`staged` 在 tmpdir 内，
       故产物落 tmpdir、不污染用户目录，决策P5.2-10）；
    2. **子进程调用**：`[sys.executable, convert_py, staged, "--backend", backend]`，
       **cwd 取 KB root、绝不取 tmpdir**（决策P5.2-12）：skill `find_dotenv()` 从 cwd 向上找
       `.env`，cwd=tmpdir 会让它找不到 KB/项目的 `.env`、marker 无法经 `.env` 启用 Gemini。
       `staged` 是绝对路径、产物目录来自 `input.parent`=tmpdir、**与 cwd 无关**，故 cwd≠tmpdir
       **不**重新引入污染。**透传调用方环境**（不做 env-scrub，决策P5.2-4），且**不透传 model**；
    3. **解析产物**：成功（exit 0）→ stdout 末行即产物 `.md` 路径（convert.py 把 backend 日志全
       导 stderr、stdout 只留路径行）→ `read_text(errors="replace")`（容错口径，P5.0-16）；
    4. **收图（P5.2.1）**：在读出产物 md 文本的**同一 with 块内**（temp 销毁前）扫描图片引用、
       按安全闸收集字节、编号、重写引用为 `images/<stem>/<stem>-<n>.<ext>`，返回 `ConvertResult`；
    5. exit≠0（全后端耗尽 / skill 不支持的格式 / 文件不存在）→ raise `ConvertError`（带 skill stderr）。

    **不**承诺字节确定性（决策P5.2-4）：输出随 mineru/marker/pypdf 版本、输入结构、及外部后端
    是否启用 LLM 增强而变——它是**源**、非可重建派生物。
    """
    script = _skill_convert_script()
    run_cwd = str(cwd or Path.cwd())  # Path 永真，None → Path.cwd()（决策P5.2-12：cwd=KB root）。
    with tempfile.TemporaryDirectory(prefix="guanlan-convert-") as tmp:
        tmp_root = Path(tmp).resolve()
        staged = tmp_root / src.name  # 绝对路径；产物落 tmp（决策P5.2-10）。
        shutil.copyfile(src, staged)
        returncode, stdout, stderr = _run_converter(
            [sys.executable, str(script), str(staged), "--backend", backend],
            cwd=run_cwd,  # KB root，绝非 tmpdir（决策P5.2-12）；透传环境、不透传 model（决策P5.2-4）。
            progress=progress,
        )
        if returncode != 0:
            raise ConvertError((stderr or "").strip() or "skill convert.py 非零退出。")
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        if not lines:
            raise ConvertError("skill convert.py 成功退出但未输出产物路径。")
        produced = Path(lines[-1].strip())
        if not produced.is_file():
            raise ConvertError(f"skill 报告的产物路径不存在：{produced}")
        md_text = produced.read_text(encoding="utf-8", errors="replace")
        # 图片字节必须在 TemporaryDirectory 销毁前读入（决策P5.2.1-4）。
        return _collect_and_rewrite_images(
            md_text, produced_md=produced, tmp_root=tmp_root, stem=stem
        )


def _default_origin(src: Path, root: Path) -> str:
    """默认 origin 口径（决策P5.2-11）：取**转换前**用户原始 `src`，绝不取 temp staged / 产物路径。

    `src` resolve 为绝对路径后：**库内→相对库根 posix 路径**（可移植、随库迁移仍有效）、
    **库外（常态）→resolved 绝对路径字符串**。`--origin` 显式给定时覆盖此默认。
    """
    resolved = src.expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def run_convert(
    src: str | Path,
    *,
    root: Path,
    name: str | None = None,
    origin: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    do_ingest: bool = False,
    backend: str = "auto",
) -> int:
    """命令壳 + 退出码：算 slug → 早存在性预检 → 转换+收图 → 文本准入 → provenance → 落 raw/（md +
    `images/`，或 `--dry-run` 打印）→（可选）串联 ingest。所有用法/转换错误 → `EXIT_USAGE`（决策P5.2-7）。

    落盘顺序「图先换、md 末步提交」（决策P5.2.1-9）：先把图写 staging、rename 换 `raw/images/<slug>/`
    （旧图存 `.bak`），**最后**经 `atomic_write_raw` 提交 md——故任何成功落盘的新 md 永不指向缺失图片；
    md 提交失败则从 `.bak`/`rmtree` 回滚图目录。图片/md 落盘 IO 失败均由本壳转 `EXIT_USAGE`（镜像
    `graph_entrypoint`，不外抛 traceback，决策P5.2.1-10）。
    """
    if dry_run and do_ingest:  # 互斥：dry-run 没落源，无可 ingest（决策P5.2-8）。
        print("`--dry-run` 与 `--ingest` 互斥（dry-run 未落源、无可 ingest）。", file=sys.stderr)
        return EXIT_USAGE

    src_path = Path(src).expanduser()
    if not src_path.is_file():
        print(f"输入文件不存在：{src}", file=sys.stderr)
        return EXIT_USAGE
    if src_path.suffix.lower() == ".md":  # 已是 markdown，不必 convert（决策P5.2-2）。
        print(
            f"{src} 已是 .md——直接 `guanlan ingest`（或 Web 投喂/晋级），不必 convert。",
            file=sys.stderr,
        )
        return EXIT_USAGE

    # provenance 必须在 staging 之前就捕获原始 src（决策P5.2-11）。
    origin_value = (origin or "").strip() or _default_origin(src_path, root)

    # 先算 target/slug（与转换无关，可提前；坏名快失败，决策P5.2.1-6）。
    try:
        target = safe_raw_target(root, name if name is not None else src_path.stem)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    stem = target.stem

    # 早存在性预检（默认不覆盖、非 dry-run → 连转换/收集都不跑，快失败，决策P5.2.1-6）。
    if target.exists() and not overwrite and not dry_run:
        print(
            _RAW_EXISTS_MSG.format(name=target.name),
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        result = convert_to_markdown(src_path, stem=stem, backend=backend, cwd=root)
    except ConvertError as exc:
        print(f"转换失败：{exc}", file=sys.stderr)
        print(
            "提示：高保真后端可 `pip install -U 'mineru[core]'` 或 `pip install marker-pdf`；"
            "纯文本兜底 `pip install pypdf`（仅 PDF）。",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        check_text_admission(result.markdown)  # 空/超限/控制字符 → ValueError（决策P5.2-6）。
        content = apply_origin(result.markdown, origin_value)  # provenance（YAML 安全、绝不裸拼）。
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    nimg = len(result.images)

    if dry_run:  # 转换 + 收集 + 重写照跑，但 raw/（含图片）零写：打印重写后 md 供人审（决策P5.2.1-7）。
        print(content, end="" if content.endswith("\n") else "\n")
        if nimg:
            print(
                f"含 {nimg} 张图片，--dry-run 未落盘；引用已重写为 images/{stem}/{stem}-N.ext。",
                file=sys.stderr,
            )
        if result.skipped:
            print(f"另有 {result.skipped} 处图片引用未准入、原样保留。", file=sys.stderr)
        return EXIT_OK

    # 落图（staging-swap），随后 md 末步提交（决策P5.2.1-9）。须动图目录 = 有新图，或 overwrite 且旧图
    # 目录残留——后者是**无图覆盖**：新 md 不再引用本地图，旧 images/<slug>/ 必须一并清掉，否则旧图仍被
    # 服务/进后续快照/ingest（决策P5.2.1-6 整盘替换语义补全）。
    real = _images_dir(target)
    need_swap = bool(result.images) or (overwrite and real.exists())
    bak: Path | None = None
    if need_swap:
        try:
            bak = _stage_and_swap_images(target, result.images)
        except OSError as exc:  # 写 staging 字节失败 → real/旧 md 原封不动（决策P5.2.1-10）。
            print(f"写 raw/images 失败：{exc}", file=sys.stderr)
            return EXIT_USAGE

    try:
        code = atomic_write_raw(target, content, overwrite)  # 末步提交 md（权威写 + 不覆盖门禁）。
    except OSError as exc:  # 落盘 IO 失败 → 回滚图目录、EXIT_USAGE，不外抛 traceback（镜像 graph）。
        if need_swap:
            _rollback_images(target, bak)
        print(f"写 raw/ 失败：{exc}", file=sys.stderr)
        return EXIT_USAGE
    if code != EXIT_OK:  # 罕见 TOCTOU 抢建（早预检已挡常态）→ 回滚图目录，旧态完整复位。
        if need_swap:
            _rollback_images(target, bak)
        if code == EXIT_USAGE:
            print(
                _RAW_EXISTS_MSG.format(name=target.name),
                file=sys.stderr,
            )
        return code
    if bak is not None:  # 提交成功 → 清旧图 .bak。
        shutil.rmtree(bak, ignore_errors=True)
    if need_swap and not result.images:  # 无图覆盖：不留空的 images/<slug>/（仅当确为空才删）。
        with contextlib.suppress(OSError):
            real.rmdir()

    nbytes = target.stat().st_size  # 实际落盘字节（UTF-8），免二次 encode 大字符串。
    receipt = f"✓ 已写 raw/{target.name}（origin: {origin_value}，{nbytes} 字节"
    if nimg:
        receipt += f"，{nimg} 张图片 → raw/images/{stem}/"
    print(receipt + "）。")
    if result.skipped:
        print(
            f"  注：另有 {result.skipped} 处图片引用未准入、原样保留（远程/越界/非图片）。",
            file=sys.stderr,
        )
    if overwrite:
        print(
            "  注：覆盖了已被引用的源可能让 wiki 页失真，建议覆盖后重 `ingest` + `check`。",
            file=sys.stderr,
        )

    if do_ingest:  # 便利串联：用 ingest 自身默认 model（convert 不透传 model，决策P5.2-4）。
        rel = f"raw/{target.name}"
        print(f"→ 串联 ingest {rel}……")
        return run_ingest(rel, root=root)
    return EXIT_OK


def convert_entrypoint(
    root_dir: str | Path,
    *,
    src: str,
    name: str | None,
    origin: str | None,
    overwrite: bool,
    dry_run: bool,
    do_ingest: bool,
    backend: str,
) -> int:
    """`guanlan convert` 的单一落地：校验**可写**库根 → run_convert。零 LLM、宿主写 `raw/`。"""
    try:
        root = require_kb_root(root_dir, writable=True)
    except GuanlanError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code
    return run_convert(
        src,
        root=root,
        name=name,
        origin=origin,
        overwrite=overwrite,
        dry_run=dry_run,
        do_ingest=do_ingest,
        backend=backend,
    )


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.convert` 入口（与 `guanlan convert` 共享 convert_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.convert",
        description="多格式转 markdown 并落 raw/（复用 pdf-to-markdown skill，脚本零 LLM）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("src", help="待转换的文件（PDF/DOCX/PPTX/XLSX/HTML/图片…）")
    parser.add_argument("--name", default=None, help="覆盖目标 slug（默认取原件 stem）")
    parser.add_argument("--origin", default=None, help="显式出处（默认 = 转换前原始 src 路径）")
    parser.add_argument(
        "--overwrite", action="store_true", help="同名 raw/ 已存在时显式覆盖（默认不覆盖）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只把转换结果打到 stdout，raw/ 零写（人审预览）"
    )
    parser.add_argument(
        "--ingest", action="store_true", help="转换成功后串联 `ingest raw/<slug>.md`（默认关）"
    )
    parser.add_argument(
        "--backend", choices=_BACKENDS, default="auto", help="转换后端（透传 skill，默认 auto）"
    )
    args = parser.parse_args(argv)
    return convert_entrypoint(
        args.dir,
        src=args.src,
        name=args.name,
        origin=args.origin,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        do_ingest=args.ingest,
        backend=args.backend,
    )


if __name__ == "__main__":
    raise SystemExit(main())
