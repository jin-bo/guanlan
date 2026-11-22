"""pdf-to-markdown skill `convert.py`：导出 MinerU 4 文档库图片（P5.2.2）。

MinerU 4 把插图存在自己的文档库里，markdown 只写定位符 `![Image block](doc:<id>/tier:<tier>/page:<n>/block:<m>)`。
skill 在错乱文字层检测通过后、源文件还在时用 `mineru read -f image` 导出成 `images/p<n>-b<m>.png` 并改写引用。
这里用假 `subprocess.run` 扮演 `mineru read`，不需要真装 MinerU。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "pdf-to-markdown" / "scripts" / "convert.py"

SHA = "060dfdd66867dba5ddf4b879845c39f5b969d7fae4a5d07ca17e8f20be414b71"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
L1 = "doc:060dfdd/tier:standard/page:8/block:3"
L2 = "doc:060dfdd/tier:standard/page:14/block:5"


@pytest.fixture
def conv():
    spec = importlib.util.spec_from_file_location("skill_convert_doc_images_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeMineruRead:
    """扮演 `mineru read <loc> -f image -o <path> --json`；记下每次调用的定位符。"""

    def __init__(self, *, fail=(), timeout=(), not_png=()):
        self.calls: list[str] = []
        self.fail, self.timeout, self.not_png = set(fail), set(timeout), set(not_png)

    def __call__(self, cmd, **kwargs):
        assert cmd[:2] == ["mineru", "read"], cmd
        loc, out = cmd[2], Path(cmd[cmd.index("-o") + 1])
        self.calls.append(loc)
        assert kwargs.get("timeout"), "每次 mineru read 都要有超时"
        if loc in self.timeout:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if loc in self.fail:
            err = {"error": {"code": "no_accessible_file", "message": "x"}}
            return subprocess.CompletedProcess(cmd, 1, stdout=json.dumps(err))
        out.write_bytes(b"GIF89a" if loc in self.not_png else PNG)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"output": {"status": "written"}}))


def _md(tmp_path, conv, body, *, sha=SHA, tier="standard"):
    md = tmp_path / "g" / "g.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(body, encoding="utf-8")
    if sha:
        conv.MINERU_DOCS[str(md)] = {"sha256": sha, "tier": tier}
    return md


def test_exports_and_rewrites_distinct_locators_once(conv, tmp_path, monkeypatch):
    body = f"图一\n\n![Image block]({L1})\n\n![Chart block]({L2})\n\n再引一次 ![Image block]({L1})\n"
    md = _md(tmp_path, conv, body)
    fake = FakeMineruRead()
    monkeypatch.setattr(conv.subprocess, "run", fake)
    assert conv.export_doc_images(md) == (2, 2)
    assert fake.calls == [L1, L2]  # 同一定位符只导一次
    text = md.read_text("utf-8")
    assert "doc:" not in text
    assert text.count("![Image block](images/p8-b3.png)") == 2  # alt 不动
    assert "![Chart block](images/p14-b5.png)" in text
    assert (md.parent / "images" / "p8-b3.png").read_bytes() == PNG


def test_failed_block_keeps_doc_link_and_summarizes(conv, tmp_path, monkeypatch, capsys):
    md = _md(tmp_path, conv, f"![a]({L1})\n\n![b]({L2})\n")
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead(fail={L2}))
    assert conv.export_doc_images(md) == (1, 2)
    text = md.read_text("utf-8")
    assert "![a](images/p8-b3.png)" in text
    assert f"![b]({L2})" in text  # 失败的原样保留
    assert "exported 1 of 2 image block(s); 1 kept as doc: links" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["timeout", "not_png"])
def test_timeout_or_bad_output_is_a_failure(conv, tmp_path, monkeypatch, kind):
    md = _md(tmp_path, conv, f"![a]({L1})\n")
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead(**{kind: {L1}}))
    assert conv.export_doc_images(md) == (0, 1)
    assert f"![a]({L1})" in md.read_text("utf-8")
    assert not (md.parent / "images").exists()  # 没导出任何图：不留空目录、不留半成品


def test_stale_file_from_earlier_run_does_not_count(conv, tmp_path, monkeypatch):
    """目标路径上已有旧图、本次导出失败 → 不能拿旧图当成功。"""
    md = _md(tmp_path, conv, f"![a]({L1})\n")
    (md.parent / "images").mkdir()
    (md.parent / "images" / "p8-b3.png").write_bytes(PNG)
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead(fail={L1}))
    assert conv.export_doc_images(md) == (0, 1)
    assert f"![a]({L1})" in md.read_text("utf-8")


@pytest.mark.parametrize(
    ("locator", "why"),
    [
        ("doc:abc1234/tier:standard/page:8/block:3", "别的文档"),
        ("doc:060dfdd/tier:flash/page:8/block:3", "别的解析档位"),
    ],
)
def test_locator_not_from_this_parse_is_not_exported(conv, tmp_path, monkeypatch, locator, why):
    md = _md(tmp_path, conv, f"![a]({locator})\n")
    fake = FakeMineruRead()
    monkeypatch.setattr(conv.subprocess, "run", fake)
    assert conv.export_doc_images(md) == (0, 1), why
    assert fake.calls == []
    assert f"![a]({locator})" in md.read_text("utf-8")


def test_unknown_parse_exports_nothing(conv, tmp_path, monkeypatch):
    """没有本次解析的文档身份（不是经 run_mineru_v4 产出的）→ 一张都不导。"""
    md = _md(tmp_path, conv, f"![a]({L1})\n", sha=None)
    fake = FakeMineruRead()
    monkeypatch.setattr(conv.subprocess, "run", fake)
    assert conv.export_doc_images(md) == (0, 1)
    assert fake.calls == []


def test_same_page_block_different_locators_never_share_a_file(conv, tmp_path, monkeypatch):
    """两个定位符都属于本文档、页码块号相同（短 id 长度不同）→ 第二张加后缀，不串图。"""
    other = "doc:060dfdd66/tier:standard/page:8/block:3"
    md = _md(tmp_path, conv, f"![a]({L1})\n\n![b]({other})\n")
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead())
    assert conv.export_doc_images(md) == (2, 2)
    text = md.read_text("utf-8")
    assert "![a](images/p8-b3.png)" in text and "![b](images/p8-b3-2.png)" in text


def test_no_locators_no_calls_bytes_unchanged(conv, tmp_path, monkeypatch):
    body = "![](https://x/a.png)\n\n![](data:image/png;base64,QUJD)\n\n![](images/本地.png)\n"
    md = _md(tmp_path, conv, body)
    fake = FakeMineruRead()
    monkeypatch.setattr(conv.subprocess, "run", fake)
    assert conv.export_doc_images(md) == (0, 0)
    assert fake.calls == []
    assert md.read_text("utf-8") == body


# ── 接入 _run_backends：先过错乱文字层检测，再导图 ──────────────────────────────────
GARBLED = "犌犅／犜 犐犆犛 " * 20


def _run(conv, tmp_path, monkeypatch, body, *, export=None):
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    calls = []

    def mineru(input_path, output_dir, args, env):
        out = tmp_path / "m" / "m.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text(body, encoding="utf-8")
        return out

    def python(input_path, output_dir, args, env):
        out = tmp_path / "py.md"
        out.write_text("正文", encoding="utf-8")
        return out

    def fake_export(md):
        calls.append(md)
        if export:
            raise export
        return 0, 0

    monkeypatch.setattr(conv, "export_doc_images", fake_export)
    monkeypatch.setitem(conv.BACKENDS, "mineru", mineru)
    monkeypatch.setitem(conv.BACKENDS, "python", python)
    conv._run_backends(["mineru", "python"], pdf, tmp_path, SimpleNamespace(), {})
    return calls


def test_garbled_output_exports_nothing(conv, tmp_path, monkeypatch, capsys):
    calls = _run(conv, tmp_path, monkeypatch, GARBLED + f"\n![a]({L1})\n")
    assert calls == []  # 被拒的产物不导图
    assert capsys.readouterr().out.strip() == str(tmp_path / "py.md")


def test_accepted_mineru_output_exports_then_returns(conv, tmp_path, monkeypatch, capsys):
    calls = _run(conv, tmp_path, monkeypatch, f"正文\n\n![a]({L1})\n")
    assert calls == [tmp_path / "m" / "m.md"]
    assert capsys.readouterr().out.strip() == str(tmp_path / "m" / "m.md")


def test_export_error_never_fails_the_conversion(conv, tmp_path, monkeypatch, capsys):
    _run(conv, tmp_path, monkeypatch, f"正文\n\n![a]({L1})\n", export=OSError("disk full"))
    cap = capsys.readouterr()
    assert cap.out.strip() == str(tmp_path / "m" / "m.md")
    assert "image export skipped: disk full" in cap.err


def test_run_mineru_v4_records_parse_identity(conv, tmp_path, monkeypatch):
    """run_mineru_v4 把本次解析的 sha256 / tier 记到 MINERU_DOCS，导图时据此核对定位符。"""
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    out = tmp_path / "s" / "s.md"

    def fake(cmd, **kwargs):
        out.parent.mkdir(exist_ok=True)
        out.write_text("正文", encoding="utf-8")
        payload = {"parse": {"sha256": SHA, "short_id": "060dfdd", "tier": "standard", "status": "done"},
                   "output": {"status": "written", "path": str(out)}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload))

    monkeypatch.setattr(conv.subprocess, "run", fake)
    args = SimpleNamespace(method=None, lang=None, mineru_backend=None, wait=10, start=None, end=None,
                           tier="standard", force=False, remote=False, engine_wait=0)
    assert conv.run_mineru_v4(pdf, tmp_path, args) == out
    assert conv.MINERU_DOCS[str(out)] == {"sha256": SHA, "tier": "standard"}


# ── 上限（P5.2.2 §5-1 补充：skill 侧限量，沿用 P5.2.1 三道上限）──────────────────────
L3 = "doc:060dfdd/tier:standard/page:20/block:1"


def test_caps_match_guanlan_imageio(conv):
    """skill 不能 import guanlan，三道上限各写一份；两边必须一致，否则 guanlan 仍会整份拒收。"""
    from guanlan import imageio

    assert conv.MAX_IMAGE_COUNT == imageio.MAX_IMAGE_COUNT
    assert conv.MAX_IMAGE_BYTES == imageio.MAX_IMAGE_BYTES
    assert conv.MAX_IMAGES_TOTAL_BYTES == imageio.MAX_IMAGES_TOTAL_BYTES


def test_count_cap_keeps_the_rest_without_asking(conv, tmp_path, monkeypatch, capsys):
    md = _md(tmp_path, conv, f"![a]({L1})\n\n![b]({L2})\n\n![c]({L3})\n\n![a]({L1})\n")
    fake = FakeMineruRead()
    monkeypatch.setattr(conv.subprocess, "run", fake)
    monkeypatch.setattr(conv, "MAX_IMAGE_COUNT", 2)
    assert conv.export_doc_images(md) == (2, 3)
    assert fake.calls == [L1, L2]  # 首次出现顺序；重复的 L1 只算一张
    text = md.read_text("utf-8")
    assert text.count("images/p8-b3.png") == 2 and f"![c]({L3})" in text
    assert "1 over cap" in capsys.readouterr().err


def test_local_images_share_the_budget(conv, tmp_path, monkeypatch):
    """产物里已有可收集的本地图：它也占预算。"""
    md = _md(tmp_path, conv, f"![本地](images/old.png)\n\n![a]({L1})\n\n![b]({L2})\n")
    (md.parent / "images").mkdir()
    (md.parent / "images" / "old.png").write_bytes(PNG)
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead())
    monkeypatch.setattr(conv, "MAX_IMAGE_COUNT", 2)
    assert conv.export_doc_images(md) == (1, 2)
    assert f"![b]({L2})" in md.read_text("utf-8")


def test_single_image_over_size_cap_is_dropped_and_export_goes_on(conv, tmp_path, monkeypatch):
    md = _md(tmp_path, conv, f"![a]({L1})\n\n![b]({L2})\n")
    fake = FakeMineruRead()
    monkeypatch.setattr(conv.subprocess, "run", fake)
    monkeypatch.setattr(conv, "MAX_IMAGE_BYTES", len(PNG) - 1)
    assert conv.export_doc_images(md) == (0, 2)
    assert fake.calls == [L1, L2]  # 单图超限只跳过它自己
    assert not (md.parent / "images").exists()  # 超限产物已清理


def test_total_cap_stops_the_export(conv, tmp_path, monkeypatch):
    md = _md(tmp_path, conv, f"![a]({L1})\n\n![b]({L2})\n\n![c]({L3})\n")
    fake = FakeMineruRead()
    monkeypatch.setattr(conv.subprocess, "run", fake)
    monkeypatch.setattr(conv, "MAX_IMAGES_TOTAL_BYTES", len(PNG) + 1)
    assert conv.export_doc_images(md) == (1, 3)
    assert fake.calls == [L1, L2]  # 第二张让累计超限 → 删掉它、后面的不再导
    assert not (md.parent / "images" / "p14-b5.png").exists()
    text = md.read_text("utf-8")
    assert "![a](images/p8-b3.png)" in text and f"![b]({L2})" in text and f"![c]({L3})" in text


def test_over_cap_text_still_lands(conv, tmp_path, monkeypatch, capsys):
    """验收：超限后正文照常转换、产物照常交出（不像 guanlan 硬上限那样整份失败）。"""
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    body = f"第一章 正文。\n\n![a]({L1})\n\n![b]({L2})\n"

    def mineru(input_path, output_dir, args, env):
        out = tmp_path / "m" / "m.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text(body, encoding="utf-8")
        conv.MINERU_DOCS[str(out)] = {"sha256": SHA, "tier": "standard"}
        return out

    monkeypatch.setitem(conv.BACKENDS, "mineru", mineru)
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead())
    monkeypatch.setattr(conv, "MAX_IMAGE_COUNT", 1)
    conv._run_backends(["mineru"], pdf, tmp_path, SimpleNamespace(), {})
    out = tmp_path / "m" / "m.md"
    assert capsys.readouterr().out.strip() == str(out)
    text = out.read_text("utf-8")
    assert "第一章 正文。" in text and "images/p8-b3.png" in text and f"![b]({L2})" in text


# ── 首次超时即停（P5.2.2 §5-1 补充 / §2）─────────────────────────────────────────────
def test_first_timeout_stops_further_reads(conv, tmp_path, monkeypatch, capsys):
    md = _md(tmp_path, conv, f"![a]({L1})\n\n![b]({L2})\n\n![c]({L3})\n")
    fake = FakeMineruRead(timeout={L2})
    monkeypatch.setattr(conv.subprocess, "run", fake)
    assert conv.export_doc_images(md) == (1, 3)
    assert fake.calls == [L1, L2]  # 验收：超时之后没有任何 mineru read 调用
    text = md.read_text("utf-8")
    assert "![a](images/p8-b3.png)" in text and f"![b]({L2})" in text and f"![c]({L3})" in text
    assert "stopped early (timed out after" in capsys.readouterr().err


def test_ordinary_failure_does_not_stop(conv, tmp_path, monkeypatch):
    md = _md(tmp_path, conv, f"![a]({L1})\n\n![b]({L2})\n\n![c]({L3})\n")
    fake = FakeMineruRead(fail={L2})
    monkeypatch.setattr(conv.subprocess, "run", fake)
    assert conv.export_doc_images(md) == (2, 3)
    assert fake.calls == [L1, L2, L3]


# ── 改写只动图片 URL（P5.2.2 §5-3 补充）──────────────────────────────────────────────
def test_rewrite_keeps_crlf_and_invalid_utf8(conv, tmp_path, monkeypatch):
    md = _md(tmp_path, conv, "")
    raw = "标题\r\n\r\n".encode() + b"\xff\xfe stray\r\n\r\n" + f"![a]({L1})\r\n尾\r\n".encode()
    md.write_bytes(raw)
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead())
    assert conv.export_doc_images(md) == (1, 1)
    assert md.read_bytes() == raw.replace(L1.encode(), b"images/p8-b3.png")


# ── 解析错误的类型（P5.2.2 审查第 4 条）──────────────────────────────────────────────
def test_string_error_falls_through_to_next_backend(conv, tmp_path, monkeypatch, capsys):
    """`mineru parse` 回 `{"error": "boom"}`（字符串）→ 当诊断消息，mineru 档失败、换下一个后端。"""
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def fake(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout=json.dumps({"error": "boom"}))

    def python(input_path, output_dir, args, env):
        out = tmp_path / "py.md"
        out.write_text("正文", encoding="utf-8")
        return out

    monkeypatch.setattr(conv.subprocess, "run", fake)
    monkeypatch.setattr(conv, "mineru_major_version", lambda: 4)
    monkeypatch.setattr(conv.shutil, "which", lambda name: f"/usr/bin/{name}")  # CI 机器上没装 mineru
    monkeypatch.setitem(conv.BACKENDS, "python", python)
    args = SimpleNamespace(method=None, lang=None, mineru_backend=None, wait=10, start=None, end=None,
                           tier="standard", force=False, remote=False, engine_wait=0)
    conv._run_backends(["mineru", "python"], pdf, tmp_path, args, {})
    cap = capsys.readouterr()
    assert cap.out.strip() == str(tmp_path / "py.md")
    assert "mineru parse failed (boom)" in cap.err


# ── 第二轮审查修正 ─────────────────────────────────────────────────────────────────
def test_failed_rewrite_leaves_output_intact(conv, tmp_path, monkeypatch):
    """改写写盘失败（如磁盘满）→ 原产物逐字节不变、不留临时文件；错误交给 _run_backends 记日志。"""
    md = _md(tmp_path, conv, f"正文\n\n![a]({L1})\n")
    before = md.read_bytes()
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead())

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(conv.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        conv.export_doc_images(md)
    assert md.read_bytes() == before
    assert not list(md.parent.glob(".imgexport-*"))


def test_rewrite_keeps_file_mode(conv, tmp_path, monkeypatch):
    md = _md(tmp_path, conv, f"![a]({L1})\n")
    md.chmod(0o644)
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead())
    conv.export_doc_images(md)
    assert md.stat().st_mode & 0o777 == 0o644


def test_percent_escaped_local_image_counts_like_guanlan(conv, tmp_path, monkeypatch):
    """guanlan 收图时先解 %-转义；skill 算预算也得这样，否则少算一张、guanlan 仍会整份拒收。"""
    md = _md(tmp_path, conv, f"![本地](images/a%20b.png)\n\n![a]({L1})\n")
    (md.parent / "images").mkdir()
    (md.parent / "images" / "a b.png").write_bytes(PNG)
    fake = FakeMineruRead()
    monkeypatch.setattr(conv.subprocess, "run", fake)
    monkeypatch.setattr(conv, "MAX_IMAGE_COUNT", 1)
    assert conv.export_doc_images(md) == (0, 1)
    assert fake.calls == []


def test_summary_counts_foreign_blocks_as_foreign_after_stop(conv, tmp_path, monkeypatch, capsys):
    other = "doc:abc1234/tier:standard/page:9/block:1"
    md = _md(tmp_path, conv, f"![a]({L1})\n\n![b]({L2})\n\n![c]({other})\n\n![d]({L3})\n")
    monkeypatch.setattr(conv.subprocess, "run", FakeMineruRead(timeout={L2}))
    assert conv.export_doc_images(md) == (1, 4)
    assert "3 kept as doc: links (1 foreign, 2 after timeout)" in capsys.readouterr().err
