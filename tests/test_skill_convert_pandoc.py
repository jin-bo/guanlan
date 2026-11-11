"""pdf-to-markdown skill `convert.py` 的 pandoc 后端：.docx 按文档结构转（标题样式、表格、脚注、图片）。

MinerU 4.0.12 第一次见到一份 docx 时只出第 1 页（页标记却写「page 1 of N」），而 docx 本是结构化 XML，
无需版面分析；auto 对 .docx 先用 pandoc，没装或失败再退 MinerU。测试 docx 用 pandoc 自己从 markdown 生成，
所以真跑 pandoc（CI 装了 pandoc，不许跳过）。
"""

from __future__ import annotations

import base64
import re
import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "pdf-to-markdown" / "scripts" / "convert.py"

# 1×1 透明 PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc 未安装（CI 必装）")


@pytest.fixture(scope="module")
def conv():
    spec = importlib.util.spec_from_file_location("skill_convert_pandoc_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**kw):
    base = dict(start=None, end=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _make_docx(tmp_path: Path, name: str = "示例文档") -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "pic.png").write_bytes(_PNG)
    (src / "doc.md").write_text(
        "# 第一章 总则\n\n正文一段。[^1]\n\n![示意图](pic.png){width=3in}\n\n"
        "## 1.1 范围\n\n| 项目 | 说明 |\n|---|---|\n| 甲 | 乙 |\n\n"
        "# 第二章 附则\n\n最后一段。\n\n[^1]: 一条脚注。\n",
        encoding="utf-8",
    )
    out = tmp_path / f"{name}.docx"
    subprocess.run(["pandoc", "doc.md", "-o", str(out)], cwd=src, check=True)
    return out


@needs_pandoc
def test_docx_keeps_structure_and_markdown_images(conv, tmp_path):
    docx = _make_docx(tmp_path)
    out = conv.run_pandoc(docx, tmp_path, _args(), {})
    md = out.read_text(encoding="utf-8")
    assert out == tmp_path / "示例文档" / "示例文档.md"
    assert "# 第一章 总则" in md and "## 1.1 范围" in md and "# 第二章 附则" in md  # 标题层级来自 Word 样式
    assert re.search(r"^\| 甲 +\| 乙 +\|$", md, re.M)  # 表格（pandoc 会补齐列宽）
    assert "一条脚注" in md
    # 图片：markdown 语法（观澜只收 ![](…) 引用），不带尺寸属性，路径相对 md 自身、文件真在
    assert "<img" not in md and "<figure" not in md
    refs = [ln for ln in md.splitlines() if ln.startswith("![")]
    assert len(refs) == 1
    rel = refs[0][refs[0].index("(") + 1 : -1]
    assert not Path(rel).is_absolute() and (out.parent / rel).is_file()
    assert not list(out.parent.glob("*.lua"))  # 临时 lua 过滤器已清理


@needs_pandoc
def test_non_web_images_become_alt_text(conv, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "pic.emf").write_bytes(b"\x01\x00\x00\x00" + b"\x00" * 80)  # 浏览器显示不了的格式
    (src / "doc.md").write_text("段落。\n\n![矢量图说明](pic.emf)\n", encoding="utf-8")
    docx = tmp_path / "emf.docx"
    subprocess.run(["pandoc", "doc.md", "-o", str(docx)], cwd=src, check=True)
    md = conv.run_pandoc(docx, tmp_path, _args(), {}).read_text(encoding="utf-8")
    assert "矢量图说明" in md
    assert "![" not in md and ".emf" not in md


def test_pandoc_only_takes_docx(conv, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(conv.BackendUnavailable, match="only handles"):
        conv.run_pandoc(pdf, tmp_path, _args(), {})


def test_pandoc_missing_is_unavailable(conv, tmp_path, monkeypatch):
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"PK")
    monkeypatch.setattr(conv.shutil, "which", lambda name: None)
    with pytest.raises(conv.BackendUnavailable, match="not found"):
        conv.run_pandoc(docx, tmp_path, _args(), {})


@pytest.mark.parametrize(
    ("backend", "suffix", "order"),
    [
        ("auto", ".docx", ["pandoc", "mineru", "marker", "python"]),
        ("auto", ".DOCX", ["pandoc", "mineru", "marker", "python"]),
        ("auto", ".pdf", ["mineru", "marker", "python"]),  # 别的格式不试 pandoc，日志里不多一行 unavailable
        ("auto", ".pptx", ["mineru", "marker", "python"]),
        ("mineru", ".docx", ["mineru"]),  # 显式指定后端照旧
        ("pandoc", ".docx", ["pandoc"]),
    ],
)
def test_backend_order(conv, backend, suffix, order):
    assert conv.backend_order(backend, suffix) == order
