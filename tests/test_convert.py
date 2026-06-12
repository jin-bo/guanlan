"""P5.2 多格式摄入 convert 测试（脚本零 LLM，见 docs/P5.2-多格式摄入.md §8）。

主路径以 **mock `convert_to_markdown` 返回固定 md** 测落源逻辑（CI 通常无重型后端）；
子进程层（cwd / argv / env / temp 暂存）以 **mock `subprocess.run`** 核验，不真打后端。
"""

import subprocess
from pathlib import Path

import pytest
import yaml

from guanlan.convert import (
    ConvertError,
    _skill_convert_script,
    convert_entrypoint,
    convert_to_markdown,
    run_convert,
)
from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.pages import split_frontmatter
from guanlan.rawio import MAX_RAW_BYTES, apply_origin


def _src(tmp_path: Path, name: str = "报告.pdf", data: bytes = b"%PDF-1.4 fake") -> Path:
    """造一个待转换的（伪）多格式输入文件。"""
    d = tmp_path / "外部目录"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(data)
    return p


# ── 定位 skill convert.py（决策P5.2-5）────────────────────────────────────────────
def test_skill_convert_script_resolves_to_real_file():
    """`_skill_convert_script()` 命中开发期仓库根 skills/pdf-to-markdown/scripts/convert.py。"""
    script = _skill_convert_script()
    assert script.is_file()
    assert script.name == "convert.py"
    assert script.parent.parent.name == "pdf-to-markdown"


# ── guanlan 自身不携带 LLM / 不改 env（决策P5.2-4）─────────────────────────────────
def test_convert_module_carries_no_llm_client():
    """断言 guanlan/convert.py 不 import 任何 LLM 客户端、不读 API key。"""
    src = (Path(__file__).parent.parent / "guanlan" / "convert.py").read_text("utf-8")
    for needle in ("litellm", "anthropic", "openai", "API_KEY", "api_key"):
        assert needle not in src, f"convert.py 不应出现 {needle!r}"


def _fake_run(md_text: str, *, returncode: int = 0, stderr: str = ""):
    """造 mock `subprocess.run`：成功时把产物写进 staged.parent（=tmpdir），返回路径行。"""
    captured: dict = {}

    def fake(cmd, cwd=None, capture_output=False, text=False, **kwargs):
        captured["cmd"] = list(cmd)
        captured["cwd"] = cwd
        captured["kwargs"] = kwargs
        staged = Path(cmd[2])
        captured["staged"] = staged
        if returncode == 0:
            outdir = staged.parent / staged.stem  # 模拟 skill 的 <stem>/<stem>.md 嵌套
            outdir.mkdir(parents=True, exist_ok=True)
            out = outdir / f"{staged.stem}.md"
            out.write_text(md_text, encoding="utf-8")
            stdout = f"{out}\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    fake.captured = captured
    return fake


def test_subprocess_cwd_is_kb_root_not_tmpdir(tmp_path, monkeypatch, kb):
    """子进程 cwd == KB root（绝非 tmpdir），保 skill `.env` 发现（决策P5.2-12）。"""
    src = _src(tmp_path)
    fake = _fake_run("# 转换产物\n\n正文。\n")
    monkeypatch.setattr(subprocess, "run", fake)

    text = convert_to_markdown(src, backend="auto", cwd=kb)
    assert text == "# 转换产物\n\n正文。\n"
    cap = fake.captured
    assert cap["cwd"] == str(kb)  # cwd=root
    assert cap["staged"].is_absolute() and cap["staged"] != kb  # staged 在 tmpdir、绝对路径
    # 产物落 tmpdir（staged.parent 内），与 cwd 无关。
    assert cap["staged"].parent != src.parent


def test_subprocess_argv_has_backend_not_model(tmp_path, monkeypatch, kb):
    """调 skill 的 argv 只含 `--backend`、不含 `--model`（决策P5.2-4③）；env 不改写。"""
    src = _src(tmp_path)
    fake = _fake_run("x\n")
    monkeypatch.setattr(subprocess, "run", fake)
    convert_to_markdown(src, backend="marker", cwd=kb)
    cmd = fake.captured["cmd"]
    assert "--backend" in cmd and "marker" in cmd
    assert "--model" not in cmd
    # 透传调用方环境：不传 env=（不剔除/不注入 GEMINI_*）。
    assert "env" not in fake.captured["kwargs"]


def test_temp_staging_leaves_no_residue(tmp_path, monkeypatch, kb):
    """temp 暂存：用户输入文件同目录零残留（无 <stem>/ 产物树，决策P5.2-10）。"""
    src = _src(tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_run("# 产物\n"))
    convert_to_markdown(src, backend="auto", cwd=kb)
    # 输入目录里只剩原件，没有 skill 的嵌套产物目录。
    assert sorted(p.name for p in src.parent.iterdir()) == [src.name]


def test_convert_to_markdown_backend_exhausted_raises(tmp_path, monkeypatch, kb):
    """全后端耗尽（skill exit 1）→ ConvertError（带 skill stderr，决策P5.2-5）。"""
    src = _src(tmp_path)
    monkeypatch.setattr(
        subprocess, "run", _fake_run("", returncode=1, stderr="all backends exhausted")
    )
    with pytest.raises(ConvertError, match="all backends exhausted"):
        convert_to_markdown(src, backend="auto", cwd=kb)


# ── 落源（决策P5.2-2/3）──────────────────────────────────────────────────────────
def test_convert_lands_raw_with_origin(tmp_path, monkeypatch, kb):
    """mock convert_to_markdown → 写 raw/<slug>.md，body == 转换文本，frontmatter 含 origin。"""
    src = _src(tmp_path)
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown", lambda *a, **k: "# 季度报告\n\n正文。\n"
    )
    rc = run_convert(src, root=kb, origin="https://example.com/q3")
    assert rc == EXIT_OK
    out = kb / "raw" / "报告.md"
    assert out.is_file()
    block, body = split_frontmatter(out.read_text("utf-8"))
    assert yaml.safe_load(block)["origin"] == "https://example.com/q3"
    assert body == "# 季度报告\n\n正文。\n"
    # raw/ 之外零写：不碰 wiki/log.md。
    assert (kb / "wiki" / "log.md").read_text("utf-8") == "# 时间线\n"


def test_convert_zero_write_outside_raw(tmp_path, monkeypatch, kb):
    """convert 只写 raw/：wiki/ 字节不变（不起 Agentao、不取快照、不写 log.md）。"""
    src = _src(tmp_path)
    before = {p: p.read_bytes() for p in (kb / "wiki").rglob("*.md")}
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: "正文。\n")
    run_convert(src, root=kb)
    after = {p: p.read_bytes() for p in (kb / "wiki").rglob("*.md")}
    assert before == after


def test_md_input_early_rejected(tmp_path, kb):
    """`.md` 输入早拒（已是 markdown、不必 convert，决策P5.2-2）→ EXIT_USAGE。"""
    p = tmp_path / "笔记.md"
    p.write_text("# 已是 md\n", encoding="utf-8")
    assert run_convert(p, root=kb) == EXIT_USAGE


def test_missing_input_rejected(tmp_path, kb):
    """输入不存在 → EXIT_USAGE。"""
    assert run_convert(tmp_path / "不存在.pdf", root=kb) == EXIT_USAGE


# ── 文本准入（决策P5.2-6，复用 rawio 闸）─────────────────────────────────────────
def test_admission_oversized_rejected(tmp_path, monkeypatch, kb):
    src = _src(tmp_path)
    big = "a" * (MAX_RAW_BYTES + 1)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: big)
    assert run_convert(src, root=kb) == EXIT_USAGE
    assert not (kb / "raw" / "报告.md").exists()


def test_admission_control_chars_rejected(tmp_path, monkeypatch, kb):
    src = _src(tmp_path)
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown", lambda *a, **k: "坏\x07字符\n"
    )
    assert run_convert(src, root=kb) == EXIT_USAGE
    assert not (kb / "raw" / "报告.md").exists()


# ── provenance（决策P4.6-10 复用 + 决策P5.2-11 默认口径）──────────────────────────
@pytest.mark.parametrize("origin", ['https://a?x=1', "A: B", '含"引号"', "多\n行"])
def test_origin_yaml_safe(tmp_path, monkeypatch, kb, origin):
    """`--origin` 含 :/引号/换行 → 经 apply_origin 写出合法可重解析 frontmatter（非裸拼）。"""
    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: "正文。\n")
    rc = run_convert(src, root=kb, origin=origin, name=f"o-{hash(origin) & 0xfff}")
    assert rc == EXIT_OK
    out = next((kb / "raw").glob("o-*.md"))
    block, _ = split_frontmatter(out.read_text("utf-8"))
    assert yaml.safe_load(block)["origin"] == origin.strip()


def test_default_origin_outside_kb_is_resolved_abs(tmp_path_factory, monkeypatch, kb):
    """省略 --origin、原件在库外 → 默认 origin == resolved 绝对路径（绝不含 temp/产物路径）。"""
    ext = tmp_path_factory.mktemp("库外")  # 真正在 kb 之外（kb 夹具复用 tmp_path）。
    src = _src(ext)
    # 让 staged/产物路径与原始 src 显著不同（mock 内部用 tmpdir）。
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: "正文。\n")
    run_convert(src, root=kb)
    block, _ = split_frontmatter((kb / "raw" / "报告.md").read_text("utf-8"))
    origin = yaml.safe_load(block)["origin"]
    assert origin == str(src.resolve())
    assert "guanlan-convert" not in origin  # 绝不含 temp 目录片段


def test_default_origin_inside_kb_is_relative(tmp_path, monkeypatch, kb):
    """原件在库内 → 默认 origin == 相对库根 posix 路径（可移植）。"""
    inside = kb / "incoming" / "报告.pdf"
    inside.parent.mkdir()
    inside.write_bytes(b"%PDF fake")
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: "正文。\n")
    run_convert(inside, root=kb)
    block, _ = split_frontmatter((kb / "raw" / "报告.md").read_text("utf-8"))
    assert yaml.safe_load(block)["origin"] == "incoming/报告.pdf"


# ── 覆盖 / dry-run / 串联（决策P5.2-8/1）─────────────────────────────────────────
def test_default_no_overwrite(tmp_path, monkeypatch, kb):
    """同名 raw/ 已存在且无 --overwrite → EXIT_USAGE；--overwrite → 覆盖成功。"""
    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: "新正文。\n")
    (kb / "raw" / "报告.md").write_text("旧\n", encoding="utf-8")
    assert run_convert(src, root=kb) == EXIT_USAGE
    assert (kb / "raw" / "报告.md").read_text("utf-8") == "旧\n"  # 未动
    assert run_convert(src, root=kb, overwrite=True) == EXIT_OK
    assert "新正文。" in (kb / "raw" / "报告.md").read_text("utf-8")


def test_dry_run_prints_and_zero_write(tmp_path, monkeypatch, kb, capsys):
    """--dry-run：转换 + 准入照跑，stdout 是归一 markdown（含 provenance），raw/ 零写。"""
    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: "预览正文。\n")
    rc = run_convert(src, root=kb, dry_run=True, origin="出处X")
    assert rc == EXIT_OK
    captured = capsys.readouterr().out
    assert "origin: 出处X" in captured and "预览正文。" in captured
    assert not (kb / "raw" / "报告.md").exists()


def test_dry_run_ingest_mutually_exclusive(tmp_path, kb):
    """--dry-run --ingest 互斥 → EXIT_USAGE。"""
    src = _src(tmp_path)
    assert run_convert(src, root=kb, dry_run=True, do_ingest=True) == EXIT_USAGE


def test_ingest_chains_run_ingest_once(tmp_path, monkeypatch, kb):
    """--ingest 落源后调 run_ingest 一次（实参 raw/<slug>.md）、透传退出码、不传 model。"""
    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: "正文。\n")
    calls: list = []

    def fake_ingest(target, *, root, **kwargs):
        calls.append({"target": target, "root": root, **kwargs})
        return EXIT_OK

    monkeypatch.setattr("guanlan.convert.run_ingest", fake_ingest)
    rc = run_convert(src, root=kb, do_ingest=True)
    assert rc == EXIT_OK
    assert len(calls) == 1
    assert calls[0]["target"] == "raw/报告.md"
    assert "model" not in calls[0]  # convert 不透传 model（决策P5.2-4③）


# ── 落盘 IO 失败（决策P5.2-6，镜像 graph）─────────────────────────────────────────
def test_raw_write_oserror_maps_to_exit_usage(tmp_path, monkeypatch, kb, capsys):
    """atomic_write_raw 抛 OSError → run_convert 的 except OSError 转 EXIT_USAGE，不 traceback。"""
    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: "正文。\n")

    def boom(*a, **k):
        raise OSError("磁盘满")

    monkeypatch.setattr("guanlan.convert.atomic_write_raw", boom)
    rc = run_convert(src, root=kb)
    assert rc == EXIT_USAGE
    assert "写 raw/ 失败" in capsys.readouterr().err


# ── convert 与 web 晋级共用 rawio 归口（决策P5.2-6，硬回归门）──────────────────────
def test_convert_uses_same_rawio_core_as_web(tmp_path, monkeypatch, kb):
    """convert 落源内容 == 直接 apply_origin（同 rawio 核心），与 web 晋级字节一致。"""
    src = _src(tmp_path)
    text = "# 报告\n\n实质正文。\n"
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: text)
    run_convert(src, root=kb, origin="src://x")
    got = (kb / "raw" / "报告.md").read_text("utf-8")
    assert got == apply_origin(text, "src://x")


# ── 库根校验 + 退出码（决策P5.2-7）────────────────────────────────────────────────
def test_entrypoint_rejects_non_kb(tmp_path):
    """非知识库根 → EXIT_USAGE（require_kb_root writable=True）。"""
    rc = convert_entrypoint(
        tmp_path,
        src=str(tmp_path / "x.pdf"),
        name=None,
        origin=None,
        overwrite=False,
        dry_run=False,
        do_ingest=False,
        backend="auto",
    )
    assert rc == EXIT_USAGE


def test_end_to_end_convert_then_ingest(tmp_path, monkeypatch, kb):
    """convert 出的 raw/x.md 能被既有 ingest（mock runner）正常吃（.md 单格式 ingest 不动）。"""
    from conftest import make_runner, write_page

    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", lambda *a, **k: "资料正文。\n")
    assert run_convert(src, root=kb) == EXIT_OK
    from guanlan.ingest import run_ingest as real_ingest

    def action(root: Path):
        write_page(root, "wiki/sources/报告.md", type="source", sources='["报告"]')

    rc = real_ingest("raw/报告.md", root=kb, runner=make_runner(action))
    assert rc == EXIT_OK
    assert (kb / "wiki" / "sources" / "报告.md").is_file()
