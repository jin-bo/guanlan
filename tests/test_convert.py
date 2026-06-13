"""P5.2 多格式摄入 convert 测试（脚本零 LLM，见 docs/P5.2-多格式摄入.md §8）。

主路径以 **mock `convert_to_markdown` 返回固定 md** 测落源逻辑（CI 通常无重型后端）；
子进程层（cwd / argv / env / temp 暂存）以 **mock `subprocess.run`** 核验，不真打后端。
"""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml

import guanlan.convert as convmod
from guanlan.convert import (
    ConvertedImage,
    ConvertError,
    ConvertResult,
    _admit_image_ref,
    _collect_and_rewrite_images,
    _skill_convert_script,
    convert_entrypoint,
    convert_to_markdown,
    run_convert,
)
from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.pages import split_frontmatter
from guanlan.rawio import MAX_RAW_BYTES, apply_origin


def _mock_convert(md: str, images=()):
    """造 `convert_to_markdown` 的 mock：返回固定 `ConvertResult`（决策P5.2.1-4 适配）。"""
    return lambda *a, **k: ConvertResult(markdown=md, images=tuple(images))


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

    result = convert_to_markdown(src, stem="报告", backend="auto", cwd=kb)
    assert result.markdown == "# 转换产物\n\n正文。\n"
    assert result.images == ()
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
    convert_to_markdown(src, stem="报告", backend="marker", cwd=kb)
    cmd = fake.captured["cmd"]
    assert "--backend" in cmd and "marker" in cmd
    assert "--model" not in cmd
    # 透传调用方环境：不传 env=（不剔除/不注入 GEMINI_*）。
    assert "env" not in fake.captured["kwargs"]


def test_temp_staging_leaves_no_residue(tmp_path, monkeypatch, kb):
    """temp 暂存：用户输入文件同目录零残留（无 <stem>/ 产物树，决策P5.2-10）。"""
    src = _src(tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_run("# 产物\n"))
    convert_to_markdown(src, stem="报告", backend="auto", cwd=kb)
    # 输入目录里只剩原件，没有 skill 的嵌套产物目录。
    assert sorted(p.name for p in src.parent.iterdir()) == [src.name]


def test_convert_to_markdown_backend_exhausted_raises(tmp_path, monkeypatch, kb):
    """全后端耗尽（skill exit 1）→ ConvertError（带 skill stderr，决策P5.2-5）。"""
    src = _src(tmp_path)
    monkeypatch.setattr(
        subprocess, "run", _fake_run("", returncode=1, stderr="all backends exhausted")
    )
    with pytest.raises(ConvertError, match="all backends exhausted"):
        convert_to_markdown(src, stem="报告", backend="auto", cwd=kb)


# ── 落源（决策P5.2-2/3）──────────────────────────────────────────────────────────
def test_convert_lands_raw_with_origin(tmp_path, monkeypatch, kb):
    """mock convert_to_markdown → 写 raw/<slug>.md，body == 转换文本，frontmatter 含 origin。"""
    src = _src(tmp_path)
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown", _mock_convert("# 季度报告\n\n正文。\n")
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
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert("正文。\n"))
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
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert(big))
    assert run_convert(src, root=kb) == EXIT_USAGE
    assert not (kb / "raw" / "报告.md").exists()


def test_admission_control_chars_rejected(tmp_path, monkeypatch, kb):
    src = _src(tmp_path)
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown", _mock_convert("坏\x07字符\n")
    )
    assert run_convert(src, root=kb) == EXIT_USAGE
    assert not (kb / "raw" / "报告.md").exists()


# ── provenance（决策P4.6-10 复用 + 决策P5.2-11 默认口径）──────────────────────────
@pytest.mark.parametrize("origin", ['https://a?x=1', "A: B", '含"引号"', "多\n行"])
def test_origin_yaml_safe(tmp_path, monkeypatch, kb, origin):
    """`--origin` 含 :/引号/换行 → 经 apply_origin 写出合法可重解析 frontmatter（非裸拼）。"""
    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert("正文。\n"))
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
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert("正文。\n"))
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
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert("正文。\n"))
    run_convert(inside, root=kb)
    block, _ = split_frontmatter((kb / "raw" / "报告.md").read_text("utf-8"))
    assert yaml.safe_load(block)["origin"] == "incoming/报告.pdf"


# ── 覆盖 / dry-run / 串联（决策P5.2-8/1）─────────────────────────────────────────
def test_default_no_overwrite(tmp_path, monkeypatch, kb):
    """同名 raw/ 已存在且无 --overwrite → EXIT_USAGE；--overwrite → 覆盖成功。"""
    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert("新正文。\n"))
    (kb / "raw" / "报告.md").write_text("旧\n", encoding="utf-8")
    assert run_convert(src, root=kb) == EXIT_USAGE
    assert (kb / "raw" / "报告.md").read_text("utf-8") == "旧\n"  # 未动
    assert run_convert(src, root=kb, overwrite=True) == EXIT_OK
    assert "新正文。" in (kb / "raw" / "报告.md").read_text("utf-8")


def test_dry_run_prints_and_zero_write(tmp_path, monkeypatch, kb, capsys):
    """--dry-run：转换 + 准入照跑，stdout 是归一 markdown（含 provenance），raw/ 零写。"""
    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert("预览正文。\n"))
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
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert("正文。\n"))
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
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert("正文。\n"))

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
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert(text))
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
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _mock_convert("资料正文。\n"))
    assert run_convert(src, root=kb) == EXIT_OK
    from guanlan.ingest import run_ingest as real_ingest

    def action(root: Path):
        write_page(root, "wiki/sources/报告.md", type="source", sources='["报告"]')

    rc = real_ingest("raw/报告.md", root=kb, runner=make_runner(action))
    assert rc == EXIT_OK
    assert (kb / "wiki" / "sources" / "报告.md").is_file()


# ══════════════════════════════════════════════════════════════════════════════
# P5.2.1 图片随源落盘（见 docs/P5.2.1-图片落盘.md §8）
# ══════════════════════════════════════════════════════════════════════════════
def _collect(tmp_path, md_name, body, images, *, stem="报告"):
    """在 resolved tmp_root 内造产物布局（md + 相对图），跑 `_collect_and_rewrite_images`。"""
    tmp_root = tmp_path.resolve()
    produced = tmp_root / md_name
    produced.parent.mkdir(parents=True, exist_ok=True)
    produced.write_text(body, encoding="utf-8")
    for rel, data in images.items():
        p = produced.parent / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return _collect_and_rewrite_images(
        body, produced_md=produced, tmp_root=tmp_root, stem=stem
    )


def _fake_run_with_images(md_text, images: dict, *, subdir=""):
    """mock subprocess.run：在 staged.parent/<stem>/[subdir]/ 造 <stem>.md + 图片文件。"""

    def fake(cmd, cwd=None, capture_output=False, text=False, **kwargs):
        staged = Path(cmd[2])
        outdir = staged.parent / staged.stem
        if subdir:
            outdir = outdir / subdir
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / f"{staged.stem}.md"
        out.write_text(md_text, encoding="utf-8")
        for rel, data in images.items():
            p = outdir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{out}\n", stderr="")

    return fake


# ── 引擎无关收集（决策P5.2.1-3）：mineru `images/` 子目录 & marker 平级 ───────────────
def test_collect_mineru_layout(tmp_path):
    """mineru 形态：`<stem>/<method>/<stem>.md` + `images/<hash>.jpg` → 编号、重写。"""
    body = "见图一\n\n![](images/abc.jpg)\n\n![说明](images/def.png)\n"
    res = _collect(tmp_path, "报告/hybrid_auto/报告.md", body,
                   {"images/abc.jpg": b"JPG1", "images/def.png": b"PNG2"})
    assert [i.name for i in res.images] == ["报告-1.jpg", "报告-2.png"]
    assert [i.data for i in res.images] == [b"JPG1", b"PNG2"]
    assert "![](images/报告/报告-1.jpg)" in res.markdown
    assert "![说明](images/报告/报告-2.png)" in res.markdown
    assert res.skipped == 0


def test_collect_marker_layout_same_result(tmp_path):
    """marker 形态：平级 `_page_X_Figure_Y.jpeg` → 同一套引擎无关收集（不写引擎分支）。"""
    body = "前言\n\n![图](_page_1_Figure_2.jpeg)\n"
    res = _collect(tmp_path, "报告/报告.md", body, {"_page_1_Figure_2.jpeg": b"FIG"})
    assert [i.name for i in res.images] == ["报告-1.jpeg"]
    assert "![图](images/报告/报告-1.jpeg)" in res.markdown


# ── 编号 / 去重 / ext（决策P5.2.1-5/8）──────────────────────────────────────────────
def test_collect_dedup_reuses_same_number(tmp_path):
    """同一图片被多次引用 → 只落一份、复用同 `<slug>-n`（按 realpath 去重）。"""
    body = "![a](images/p.png)\n\n中间\n\n![b](images/p.png)\n\n![c](images/q.png)\n"
    res = _collect(tmp_path, "报告/auto/报告.md", body,
                   {"images/p.png": b"P", "images/q.png": b"Q"})
    assert [i.name for i in res.images] == ["报告-1.png", "报告-2.png"]
    assert res.markdown.count("images/报告/报告-1.png") == 2  # 两处引用同一序号
    assert res.markdown.count("images/报告/报告-2.png") == 1


def test_collect_ext_lowercased_not_normalized(tmp_path):
    """ext 取原后缀小写、不归一（.JPG→.jpg、.jpeg 保持 .jpeg，不强归 .jpg）。"""
    body = "![](A.JPG)\n\n![](B.jpeg)\n"
    res = _collect(tmp_path, "报告/报告.md", body, {"A.JPG": b"a", "B.jpeg": b"b"})
    assert [i.name for i in res.images] == ["报告-1.jpg", "报告-2.jpeg"]


# ── 远程 / data: / 未准入引用原样保留（决策P5.2.1-5）─────────────────────────────────
def test_collect_remote_and_missing_preserved(tmp_path):
    """`https://`/`data:`/缺失引用 → 整条不动、不编号，skipped 计数。"""
    body = "![](https://x/a.png)\n\n![](data:image/png;base64,QUJD)\n\n![](缺失.png)\n"
    res = _collect(tmp_path, "报告/报告.md", body, {})
    assert res.images == ()
    assert res.skipped == 3
    assert res.markdown == body  # 逐字不动


# ── 路径准入安全闸（决策P5.2.1-5，安全硬门）─────────────────────────────────────────
def test_collect_path_admission_security(tmp_path):
    """越界/绝对/编码穿越/symlink-逃逸/非白名单扩展一律拒收；合法相对图仍正常收集（不误伤）。"""
    outside = tmp_path / "库外secret.png"
    outside.write_bytes(b"SECRET")
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()
    # tmp 内造一个逃逸 symlink 指向库外真实文件。
    evil = tmp_root / "报告" / "evil.png"
    evil.parent.mkdir(parents=True)
    os.symlink(outside, evil)
    body = (
        "![](/etc/passwd)\n\n"  # 绝对路径
        "![](../../库外secret.png)\n\n"  # 越界穿越
        "![](%2e%2e/%2e%2e/库外secret.png)\n\n"  # 编码穿越
        "![](evil.png)\n\n"  # symlink 逃逸
        "![](note.txt)\n\n"  # 非白名单扩展（即便存在）
        "![](good.png)\n"  # 合法相对图（必须仍收）
    )
    (tmp_root / "报告" / "note.txt").write_bytes(b"text")
    (tmp_root / "报告" / "good.png").write_bytes(b"GOOD")
    res = _collect_and_rewrite_images(
        body, produced_md=tmp_root / "报告" / "报告.md",
        tmp_root=tmp_root.resolve(), stem="报告",
    )
    assert [i.name for i in res.images] == ["报告-1.png"]
    assert res.images[0].data == b"GOOD"
    assert res.skipped == 5
    assert b"SECRET" not in b"".join(i.data for i in res.images)


def test_admit_image_ref_rejects_schemes(tmp_path):
    """`_admit_image_ref` 直测：scheme/协议相对/绝对/~ 一律 None。"""
    root = tmp_path.resolve()
    for url in ("http://x/a.png", "https://x/a.png", "file:///a.png",
                "data:image/png;base64,x", "//host/a.png", "/abs/a.png", "~/a.png"):
        assert _admit_image_ref(url, root, root) is None


# ── 容量上限三道闸（决策P5.2.1-11）──────────────────────────────────────────────────
# 容量常量与闸归口在 imageio（P4.6.1-4 抽出）；monkeypatch 须指 imageio 模块（闸读其全局）。
import guanlan.imageio as imgmod  # noqa: E402


def test_capacity_single_image_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(imgmod, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(ConvertError, match="单图"):
        _collect(tmp_path, "报告/报告.md", "![](big.png)\n", {"big.png": b"toolong"})


def test_capacity_total_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(imgmod, "MAX_IMAGES_TOTAL_BYTES", 6)
    with pytest.raises(ConvertError, match="累计"):
        _collect(tmp_path, "报告/报告.md", "![](a.png)\n\n![](b.png)\n",
                 {"a.png": b"aaaa", "b.png": b"bbbb"})


def test_capacity_count_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(imgmod, "MAX_IMAGE_COUNT", 1)
    with pytest.raises(ConvertError, match="张数"):
        _collect(tmp_path, "报告/报告.md", "![](a.png)\n\n![](b.png)\n",
                 {"a.png": b"a", "b.png": b"b"})


# ── 无图向后兼容（决策P5.2.1-4）─────────────────────────────────────────────────────
def test_collect_no_images_backward_compatible(tmp_path):
    body = "# 纯文本\n\n没有任何图片。\n"
    res = _collect(tmp_path, "报告/报告.md", body, {})
    assert res.images == ()
    assert res.skipped == 0
    assert res.markdown == body


# ── 内核端到端（真 TemporaryDirectory，证 in-with-block 收集 + 字节逐字）──────────────
def test_kernel_collects_in_with_block_mineru(tmp_path, monkeypatch, kb):
    """convert_to_markdown 真跑：mineru 布局 → ConvertResult，图字节逐字（sha256 相等）。"""
    src = _src(tmp_path)
    raw = b"\x89PNG-bytes-rawdata"
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run_with_images("![](images/x.png)\n", {"images/x.png": raw}, subdir="hybrid_auto"),
    )
    res = convert_to_markdown(src, stem="报告", cwd=kb)
    assert [i.name for i in res.images] == ["报告-1.png"]
    assert hashlib.sha256(res.images[0].data).digest() == hashlib.sha256(raw).digest()
    assert "images/报告/报告-1.png" in res.markdown


def test_kernel_collects_in_with_block_marker(tmp_path, monkeypatch, kb):
    """convert_to_markdown 真跑：marker 平级布局 → 同样收集（引擎无关）。"""
    src = _src(tmp_path)
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run_with_images("![](_page_2_Figure_1.jpeg)\n", {"_page_2_Figure_1.jpeg": b"J"}),
    )
    res = convert_to_markdown(src, stem="报告", cwd=kb)
    assert [i.name for i in res.images] == ["报告-1.jpeg"]


# ── run_convert 落盘：图锚在 raw/、不在 <kb>/images（决策P5.2.1-2，评审 #1 回归）──────
def _img_result(md, *images):
    return lambda *a, **k: ConvertResult(
        markdown=md, images=tuple(ConvertedImage(n, d) for n, d in images)
    )


def test_run_convert_lands_images_under_raw(tmp_path, monkeypatch, kb):
    """图实际落 `<kb>/raw/images/<slug>/`；断言 `<kb>/images/<slug>/` 不存在。"""
    src = _src(tmp_path)
    md = "![](images/报告/报告-1.jpg)\n"
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown",
        _img_result(md, ("报告-1.jpg", b"IMG1")),
    )
    assert run_convert(src, root=kb) == EXIT_OK
    assert (kb / "raw" / "images" / "报告" / "报告-1.jpg").read_bytes() == b"IMG1"
    assert not (kb / "images").exists()  # 绝不落到 <kb>/images/
    # md 内引用从 raw/报告.md 解析命中真实图。
    assert (kb / "raw" / "报告.md").parent.joinpath("images/报告/报告-1.jpg").is_file()


def test_run_convert_no_images_skips_images_dir(tmp_path, monkeypatch, kb):
    """无图文档：不建 raw/images/<slug>/、md 原样落盘（向后兼容）。"""
    src = _src(tmp_path)
    monkeypatch.setattr("guanlan.convert.convert_to_markdown", _img_result("纯文本。\n"))
    assert run_convert(src, root=kb) == EXIT_OK
    assert not (kb / "raw" / "images").exists()


# ── --overwrite 整盘替换图目录（决策P5.2.1-6/9）─────────────────────────────────────
def test_overwrite_replaces_whole_image_dir(tmp_path, monkeypatch, kb):
    """先放陈旧 报告-9.png，--overwrite 转出 2 张 → 旧图消失、只剩新 报告-1/2。"""
    src = _src(tmp_path)
    stale = kb / "raw" / "images" / "报告" / "报告-9.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"STALE")
    (kb / "raw" / "报告.md").write_text("旧\n", encoding="utf-8")
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown",
        _img_result("![](a)\n![](b)\n", ("报告-1.png", b"N1"), ("报告-2.png", b"N2")),
    )
    assert run_convert(src, root=kb, overwrite=True) == EXIT_OK
    names = sorted(p.name for p in (kb / "raw" / "images" / "报告").iterdir())
    assert names == ["报告-1.png", "报告-2.png"]
    assert not stale.exists()


def test_overwrite_with_no_images_clears_stale_dir(tmp_path, monkeypatch, kb):
    """旧版有本地图，新版转出**零图** + --overwrite → 旧 images/报告/ 整盘清掉、不留悬空旧图/空目录。"""
    src = _src(tmp_path)
    stale = kb / "raw" / "images" / "报告" / "报告-9.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"STALE")
    (kb / "raw" / "报告.md").write_text("旧\n", encoding="utf-8")
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown", _mock_convert("纯文本、无图。\n")
    )
    assert run_convert(src, root=kb, overwrite=True) == EXIT_OK
    assert not stale.exists()  # 旧图清掉、不再被服务/进快照
    assert not (kb / "raw" / "images" / "报告").exists()  # 不留空目录
    assert "纯文本、无图。" in (kb / "raw" / "报告.md").read_text("utf-8")  # 新 md 已落（带 origin 头）


# ── 落盘顺序 = 图先换、md 末提交 + 失败一致性（决策P5.2.1-9，硬门）────────────────────
def test_staging_write_failure_leaves_everything_untouched(tmp_path, monkeypatch, kb, capsys):
    """写 staging 字节抛 OSError → md 未落、旧图原封不动、EXIT_USAGE、无 traceback。"""
    src = _src(tmp_path)
    stale = kb / "raw" / "images" / "报告" / "报告-9.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"OLD")
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown",
        _img_result("![](a)\n", ("报告-1.png", b"N1")),
    )

    def boom(*a, **k):
        raise OSError("磁盘满")

    monkeypatch.setattr("guanlan.convert._stage_and_swap_images", boom)
    assert run_convert(src, root=kb, overwrite=True) == EXIT_USAGE
    assert stale.read_bytes() == b"OLD"  # 旧图原封不动
    assert not (kb / "raw" / "报告.md").exists()  # md 未落
    assert "写 raw/images 失败" in capsys.readouterr().err


def test_md_commit_failure_rolls_back_fresh_images(tmp_path, monkeypatch, kb):
    """fresh：图已换上、md 提交 OSError → real 被回滚删除、不留「新 md + 图」。"""
    src = _src(tmp_path)
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown",
        _img_result("![](a)\n", ("报告-1.png", b"N1")),
    )

    def boom(*a, **k):
        raise OSError("磁盘满")

    monkeypatch.setattr("guanlan.convert.atomic_write_raw", boom)
    assert run_convert(src, root=kb) == EXIT_USAGE
    assert not (kb / "raw" / "images" / "报告").exists()  # 图目录回滚
    assert not (kb / "raw" / "报告.md").exists()  # md 未落


def test_md_commit_failure_restores_old_images_on_overwrite(tmp_path, monkeypatch, kb):
    """overwrite：md 提交 OSError → 从 .bak 复位旧图（旧 报告-9 回来、新图消失）。"""
    src = _src(tmp_path)
    stale = kb / "raw" / "images" / "报告" / "报告-9.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"OLD")
    (kb / "raw" / "报告.md").write_text("旧 md\n", encoding="utf-8")
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown",
        _img_result("![](a)\n", ("报告-1.png", b"N1")),
    )

    def boom(*a, **k):
        raise OSError("磁盘满")

    monkeypatch.setattr("guanlan.convert.atomic_write_raw", boom)
    assert run_convert(src, root=kb, overwrite=True) == EXIT_USAGE
    assert stale.read_bytes() == b"OLD"  # 旧图复位
    assert not (kb / "raw" / "images" / "报告" / "报告-1.png").exists()  # 新图消失
    # 复位后无残留 .bak / .staging-*
    leftovers = [p.name for p in (kb / "raw" / "images").iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_md_commit_success_implies_images_present(tmp_path, monkeypatch, kb):
    """承诺正向断言：atomic_write_raw 成功（md 落盘）⟹ md 引用的每张图实际存在。"""
    src = _src(tmp_path)
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown",
        _img_result("![](images/报告/报告-1.jpg)\n![](images/报告/报告-2.png)\n",
                    ("报告-1.jpg", b"A"), ("报告-2.png", b"B")),
    )
    assert run_convert(src, root=kb) == EXIT_OK
    assert (kb / "raw" / "报告.md").is_file()
    for n in ("报告-1.jpg", "报告-2.png"):
        assert (kb / "raw" / "images" / "报告" / n).is_file()


# ── --dry-run 连图零落盘（决策P5.2.1-7）─────────────────────────────────────────────
def test_dry_run_zero_image_write(tmp_path, monkeypatch, kb, capsys):
    """--dry-run：stdout 是重写后 md，raw/ 字节零变动（无 md、无 images/<slug>/），stderr 注记。"""
    src = _src(tmp_path)
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown",
        _img_result("![](images/报告/报告-1.png)\n", ("报告-1.png", b"X")),
    )
    assert run_convert(src, root=kb, dry_run=True) == EXIT_OK
    out = capsys.readouterr()
    assert "images/报告/报告-1.png" in out.out
    assert not (kb / "raw" / "报告.md").exists()
    assert not (kb / "raw" / "images").exists()
    assert "含 1 张图片" in out.err


# ── 回执含图片数 ────────────────────────────────────────────────────────────────────
def test_receipt_includes_image_count(tmp_path, monkeypatch, kb, capsys):
    src = _src(tmp_path)
    monkeypatch.setattr(
        "guanlan.convert.convert_to_markdown",
        _img_result("![](a)\n![](b)\n", ("报告-1.png", b"1"), ("报告-2.png", b"2")),
    )
    run_convert(src, root=kb)
    assert "2 张图片 → raw/images/报告/" in capsys.readouterr().out


# ══════════════════════════════════════════════════════════════════════════════
# P4.6.1 转换内核 progress= 流式契约（决策P4.6.1-10）
# ══════════════════════════════════════════════════════════════════════════════
def test_run_converter_none_uses_subprocess_run(monkeypatch):
    """progress=None（CLI）走 subprocess.run、行为字节不变（不起 Popen）。"""
    calls = {"run": 0, "popen": 0}

    def fake_run(cmd, cwd=None, capture_output=False, text=False, **k):
        calls["run"] += 1
        return subprocess.CompletedProcess(cmd, 0, stdout="o\n", stderr="e\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.__setitem__("popen", 1))
    rc, out, err = convmod._run_converter(["x"], cwd=".", progress=None)
    assert (rc, out, err) == (0, "o\n", "e\n")
    assert calls == {"run": 1, "popen": 0}  # 只走 run、绝不起 Popen


def test_run_converter_progress_streams_stderr_lines(monkeypatch):
    """progress 给定 → Popen 两管道并发 drain，stderr 行实时回调；stdout 末行 = 产物路径。"""

    class FakePopen:
        def __init__(self, *a, **k):
            self.stdout = iter(["不重要\n", "/tmp/产物.md\n"])
            self.stderr = iter(["[mineru] 尝试\n", "[marker] 回退\n"])
            self.returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    seen: list[str] = []
    rc, out, err = convmod._run_converter(["x"], cwd=".", progress=seen.append)
    assert rc == 0
    assert out.splitlines()[-1] == "/tmp/产物.md"  # 末行取产物路径不变
    assert seen == ["[mineru] 尝试", "[marker] 回退"]  # 每条 stderr 行实时回调（去尾换行）
    assert "回退" in err  # 同时整体累计


# ══════════════════════════════════════════════════════════════════════════════
# P4.6.1 collect_for_promotion（严格收集 + 指纹，决策P4.6.1-15/16）
# ══════════════════════════════════════════════════════════════════════════════
def test_collect_for_promotion_relative_collected_with_fingerprint(tmp_path):
    """相对引用准入+存在 → 收集、按 target stem 归一重写、记 SHA256 指纹。"""
    root = tmp_path.resolve()
    (root / "images" / "x").mkdir(parents=True)
    (root / "images" / "x" / "a.png").write_bytes(b"A")
    src = root / "x.md"
    body = "![](images/x/a.png)\n"
    src.write_text(body, encoding="utf-8")
    res = imgmod.collect_for_promotion(body, source_md=src, root=root, stem="y")
    assert [i.name for i in res.images] == ["y-1.png"]
    assert "images/y/y-1.png" in res.markdown
    assert res.images[0].sha256 == hashlib.sha256(b"A").hexdigest()
    assert res.images[0].source == (root / "images" / "x" / "a.png")


def test_collect_for_promotion_external_preserved(tmp_path):
    """外链（带 scheme）原样保留、计 skipped、不报错（决策P4.6.1-15）。"""
    root = tmp_path.resolve()
    src = root / "x.md"
    body = "![](https://x/a.png)\n\n![](data:image/png;base64,QQ==)\n"
    src.write_text(body, encoding="utf-8")
    res = imgmod.collect_for_promotion(body, source_md=src, root=root, stem="y")
    assert res.images == () and res.skipped == 2 and res.markdown == body


def test_collect_for_promotion_dangling_raises(tmp_path):
    """相对引用悬空（文件缺失）→ ValueError（端点转 400「先 relocalize」，决策P4.6.1-15）。"""
    root = tmp_path.resolve()
    src = root / "x.md"
    body = "![](images/x/missing.png)\n"
    src.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="无法解析或越界"):
        imgmod.collect_for_promotion(body, source_md=src, root=root, stem="y")


def test_collect_for_promotion_traversal_raises(tmp_path):
    """相对引用越界（穿越出 root）→ ValueError（安全闸，决策P4.6.1-8/15）。"""
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    (tmp_path / "secret.png").write_bytes(b"SECRET")
    src = root / "x.md"
    body = "![](../secret.png)\n"
    src.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        imgmod.collect_for_promotion(body, source_md=src, root=root, stem="y")
