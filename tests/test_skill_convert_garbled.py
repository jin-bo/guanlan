"""pdf-to-markdown skill `convert.py`：错乱文字层检测（P4.30.1 §3）。

部分 PDF 的字体用私有编码：文字层里「ICS」读成「犐犆犛」、「GB/T」读成「犌犅／犜」，或是 `/G21` 这样的裸字形名。
只读文字层的后端照抄这层文字，产物不可读——判为失败、换下一个后端，不能当成功交出去。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "pdf-to-markdown" / "scripts" / "convert.py"

GARBLED = "书 书 书 犐犆犛 １３ ． ３１０ 犃 ９２ 中 华 人 民 共 和 国 国 家 标 准 犌犅 ／ 犜 １２３４５ — ２０２０\n" + (
    "犘犺狔狊犻犮犪犾犪狀犱犮犺犲犿犻犮犪犾 示例标准的名称\n" * 3
)
GLYPHS = "书 书 书 " + " ".join(f"/G{n:02X}" for n in range(0x21, 0x40)) + "\n正文"
NORMAL = "# 示例实验室能力通用要求\n\n" + "本标准规定了示例实验室的通用要求，适用于各类示例活动。犯罪现场勘查。\n" * 20


@pytest.fixture(scope="module")
def conv():
    spec = importlib.util.spec_from_file_location("skill_convert_garbled_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_detects_shifted_cjk(conv):
    assert conv.garbled_text_layer(GARBLED)


def test_detects_glyph_names(conv):
    assert conv.garbled_text_layer(GLYPHS)


def test_normal_chinese_not_flagged(conv):
    """漏报的反面：正常中文（含个别落在 U+7280–72FF 的常用字，如「犯」「状」「独」）不触发。"""
    assert conv.garbled_text_layer(NORMAL) is None
    assert conv.garbled_text_layer("状态独立，狗与猫。" * 30) is None


def test_traditional_chinese_not_flagged(conv):
    """繁体正文里落在 U+7280–72FF、又不在 GB2312 的常用字（狀 犧 狹）单个出现，不是错乱文字层。"""
    assert conv.garbled_text_layer("狀況良好，犧牲狹隘的利益，狀態穩定。" * 30) is None


def test_short_text_not_judged(conv):
    assert conv.garbled_text_layer("犌犅") is None


def test_garbled_backend_result_falls_through(conv, tmp_path, capsys):
    """文字层错乱的产物：判失败、删掉产物、换下一个后端。"""
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def bad(input_path, output_dir, args, env):
        out = tmp_path / "bad.md"
        out.write_text(GARBLED, encoding="utf-8")
        return out

    def good(input_path, output_dir, args, env):
        out = tmp_path / "good.md"
        out.write_text(NORMAL, encoding="utf-8")
        return out

    saved = dict(conv.BACKENDS)
    conv.BACKENDS.update({"bad": bad, "good": good})
    try:
        conv._run_backends(["bad", "good"], pdf, tmp_path, SimpleNamespace(), {})
    finally:
        conv.BACKENDS.clear()
        conv.BACKENDS.update(saved)
    cap = capsys.readouterr()
    assert cap.out.strip() == str(tmp_path / "good.md")
    assert "garbled text layer" in cap.err
    assert not (tmp_path / "bad.md").exists()


def test_all_garbled_exits_nonzero(conv, tmp_path):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def bad(input_path, output_dir, args, env):
        out = tmp_path / "bad.md"
        out.write_text(GLYPHS, encoding="utf-8")
        return out

    saved = dict(conv.BACKENDS)
    conv.BACKENDS["bad"] = bad
    try:
        with pytest.raises(SystemExit) as exc:
            conv._run_backends(["bad"], pdf, tmp_path, SimpleNamespace(), {})
    finally:
        conv.BACKENDS.clear()
        conv.BACKENDS.update(saved)
    assert exc.value.code == 1
