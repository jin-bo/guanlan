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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .ingest import run_ingest
from .paths import require_kb_root
from .rawio import apply_origin, atomic_write_raw, check_text_admission, safe_raw_target
from .skill import bundled_skill_dir

# skill `convert.py` 支持的后端（透传给 skill；格式白名单归口在 skill，不在此重复，决策P5.2-2/5）。
_BACKENDS = ("auto", "mineru", "marker", "python")


class ConvertError(Exception):
    """转换失败（skill 缺失 / 不支持的格式 / 全后端耗尽）。命令层映射 `EXIT_USAGE`。"""


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


def convert_to_markdown(
    src: Path, *, backend: str = "auto", cwd: Path | None = None
) -> str:
    """**转换内核**：把一个多格式文件转成 markdown 文本字符串，零 LLM。复用 skill `convert.py` 当引擎。

    `cwd` = 子进程工作目录 / skill `.env` 发现锚点（`run_convert` 传 **KB root**；None → 回退
    `Path.cwd()`）：

    1. **temp 暂存**：把 src 复制进 `tempfile.TemporaryDirectory`、得**绝对路径** `staged`
       （skill `convert.py` 把产物写在*输入文件同目录*、无 `--output_dir`——`staged` 在 tmpdir 内，
       故产物落 tmpdir、不污染用户目录，决策P5.2-10）；
    2. **子进程调用**：`[sys.executable, convert_py, staged, "--backend", backend]`，
       **cwd 取 KB root、绝不取 tmpdir**（决策P5.2-12）：skill `find_dotenv()` 从 cwd 向上找
       `.env`，cwd=tmpdir 会让它找不到 KB/项目的 `.env`、marker 无法经 `.env` 启用 Gemini。
       `staged` 是绝对路径、产物目录来自 `input.parent`=tmpdir、**与 cwd 无关**，故 cwd≠tmpdir
       **不**重新引入污染。**透传调用方环境**（不做 env-scrub，决策P5.2-4），且**不透传 model**；
    3. **解析产物**：成功（exit 0）→ stdout 末行即产物 `.md` 路径（convert.py 把 backend 日志全
       导 stderr、stdout 只留路径行）→ `read_text(errors="replace")`（容错口径，P5.0-16）返回文本；
    4. exit≠0（全后端耗尽 / skill 不支持的格式 / 文件不存在）→ raise `ConvertError`（带 skill stderr）。

    **不**承诺字节确定性（决策P5.2-4）：输出随 mineru/marker/pypdf 版本、输入结构、及外部后端
    是否启用 LLM 增强而变——它是**源**、非可重建派生物。
    """
    script = _skill_convert_script()
    run_cwd = str(cwd or Path.cwd())  # Path 永真，None → Path.cwd()（决策P5.2-12：cwd=KB root）。
    with tempfile.TemporaryDirectory(prefix="guanlan-convert-") as tmp:
        staged = Path(tmp).resolve() / src.name  # 绝对路径；产物落 tmp（决策P5.2-10）。
        shutil.copyfile(src, staged)
        proc = subprocess.run(
            [sys.executable, str(script), str(staged), "--backend", backend],
            cwd=run_cwd,  # KB root，绝非 tmpdir（决策P5.2-12）；透传环境、不透传 model（决策P5.2-4）。
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ConvertError((proc.stderr or "").strip() or "skill convert.py 非零退出。")
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if not lines:
            raise ConvertError("skill convert.py 成功退出但未输出产物路径。")
        produced = Path(lines[-1].strip())
        if not produced.is_file():
            raise ConvertError(f"skill 报告的产物路径不存在：{produced}")
        return produced.read_text(encoding="utf-8", errors="replace")


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
    """命令壳 + 退出码：解析输入 → 转换 → 文本准入 → provenance → 写 raw/（或 `--dry-run` 打印）
    →（可选）串联 ingest。所有用法/转换错误 → `EXIT_USAGE`（决策P5.2-7）。

    落盘 IO 失败：`atomic_write_raw` 上抛的 `OSError` 由本壳 `except OSError` 捕获 → 打印
    「写 raw/ 失败」、返回 `EXIT_USAGE`（镜像 `graph_entrypoint`，不外抛 traceback，决策P5.2-6）。
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

    try:
        text = convert_to_markdown(src_path, backend=backend, cwd=root)
    except ConvertError as exc:
        print(f"转换失败：{exc}", file=sys.stderr)
        print(
            "提示：高保真后端可 `pip install -U 'mineru[core]'` 或 `pip install marker-pdf`；"
            "纯文本兜底 `pip install pypdf`（仅 PDF）。",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        check_text_admission(text)  # 空 / 超 MAX_RAW_BYTES / 控制字符 → ValueError（决策P5.2-6）。
        content = apply_origin(text, origin_value)  # provenance（YAML 安全、绝不裸拼）。
        target = safe_raw_target(root, name if name is not None else src_path.stem)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    if dry_run:  # 转换 + 准入照跑，但 raw/ 零写：把归一后 markdown 打 stdout 供人审（决策P5.2-8）。
        print(content, end="" if content.endswith("\n") else "\n")
        return EXIT_OK

    try:
        code = atomic_write_raw(target, content, overwrite)
    except OSError as exc:  # 落盘 IO 失败（磁盘满/权限等）→ EXIT_USAGE，不外抛 traceback（镜像 graph）。
        print(f"写 raw/ 失败：{exc}", file=sys.stderr)
        return EXIT_USAGE
    if code == EXIT_USAGE:  # 同名已存在且无 --overwrite（决策P5.2-8）。
        print(
            f"raw/{target.name} 已存在；改名（`--name`）或加 `--overwrite` 覆盖。",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if code != EXIT_OK:
        return code

    nbytes = target.stat().st_size  # 实际落盘字节（UTF-8），免二次 encode 大字符串。
    print(f"✓ 已写 raw/{target.name}（origin: {origin_value}，{nbytes} 字节）。")
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
