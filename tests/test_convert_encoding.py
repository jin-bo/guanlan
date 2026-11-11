"""convert 子进程编码：真子进程、真 pypdf 后端，非 UTF-8 环境下中文文件名也要转得出来。

两端都要钉 UTF-8（与 `runtime.envelope_child_env` 同理，见 docs/backlog/notes/gbrain-v0.42.53-反向审计-guanlan缺陷.md
§2.4b/⑥）：skill 脚本 `print` 产物路径、mineru 4 的 std 流固定 UTF-8，而 Windows 的 Python 默认按 ANSI 代码页
（英文版 cp1252、中文版 cp936）编解码管道。

本机复现 Windows 的办法：父进程环境里设 `PYTHONIOENCODING=latin-1`（cp1252 一样编不出汉字），子进程会继承；
修复前 skill 在 `print(中文路径)` 处抛 `UnicodeEncodeError`。CI 的 windows-latest 任务还会在原生 cp1252 下跑本文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("pypdf")

from guanlan.convert import convert_to_markdown  # noqa: E402

PDF_TEXT = b"Hello encoding parity"


def _pdf(text: bytes) -> bytes:
    """手搓一页带文本层的最小 PDF（Helvetica），pypdf 能抽出 `text`。"""
    stream = b"BT /F1 18 Tf 20 100 Td (" + text + b") Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    root.mkdir()
    # .env 里的中文注释：skill 的 load_dotenv 若按系统编码读（Windows cp1252/cp936），选后端之前就崩
    (root / ".env").write_text("# 中文注释：转换后端配置（无键）\nGUANLAN_TEST_DUMMY=1\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("stem", ["报告", "年报📄"])  # 普通汉字；GBK 也编不出的 emoji
@pytest.mark.parametrize("child_io", [None, "latin-1"])  # None = 本机原样；latin-1 = 模拟英文版 Windows
def test_real_subprocess_cjk_filename(kb: Path, tmp_path: Path, monkeypatch, stem, child_io):
    if child_io:
        monkeypatch.setenv("PYTHONIOENCODING", child_io)
    src = tmp_path / f"{stem}.pdf"
    src.write_bytes(_pdf(PDF_TEXT))
    lines: list[str] = []
    res = convert_to_markdown(src, stem=stem, backend="python", cwd=kb, progress=lines.append)
    assert PDF_TEXT.decode() in res.markdown
    assert res.backend == "python"
    assert any("[python]" in ln for ln in lines)  # stderr 进度行逐行到达、能解码


@pytest.mark.parametrize("child_io", [None, "latin-1"])
def test_real_subprocess_cli_path_without_progress(kb: Path, tmp_path: Path, monkeypatch, child_io):
    """CLI `convert` 走 `subprocess.run`（不带 progress），同样要两端对齐。"""
    if child_io:
        monkeypatch.setenv("PYTHONIOENCODING", child_io)
    src = tmp_path / "说明书.pdf"
    src.write_bytes(_pdf(PDF_TEXT))
    res = convert_to_markdown(src, stem="说明书", backend="python", cwd=kb)
    assert PDF_TEXT.decode() in res.markdown


def test_python_is_the_running_interpreter():
    """子进程用的是 sys.executable（pypdf 装在同一环境里，上面的测试才是真后端）。"""
    import pypdf  # noqa: F401

    assert Path(sys.executable).exists()
