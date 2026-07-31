"""`atomic_write_text` 原子覆盖写测试（wiki/ + .trash/ 确定性写去半写）。

见 `docs/backlog/notes/openkb-2026-07-反向评审.md` §2：三类——① 成功覆盖、② 写失败时旧文件
不变、③ 失败路径不残留 `.tmp`；外加 `newline=""` 逐字保真，以及经 `remove._drop_slug_from_page`
的集成证明（写崩不把内容页截成半截）。**只避免半写，不做 crash recovery**。
"""

import os
from pathlib import Path

import pytest
import yaml

from guanlan.rawio import (
    atomic_write_bytes,
    atomic_write_text,
    detect_eol,
    dump_frontmatter,
    read_text_verbatim,
    split_eol_lines,
)


def _boom(*_args, **_kwargs):
    raise OSError("replace failed")


def test_creates_new_file(tmp_path: Path) -> None:
    """① 新建：内容如期落盘。"""
    target = tmp_path / "new.md"
    atomic_write_text(target, "内容\n")
    assert target.read_text(encoding="utf-8") == "内容\n"


def test_overwrites_existing(tmp_path: Path) -> None:
    """① 覆盖：旧内容被新内容原子替换。"""
    target = tmp_path / "p.md"
    target.write_text("旧内容", encoding="utf-8")
    atomic_write_text(target, "新内容")
    assert target.read_text(encoding="utf-8") == "新内容"


def test_verbatim_no_eol_translation(tmp_path: Path) -> None:
    """`newline=""` 逐字写：自管 EOL 的调用方（reindex._join_lines）CRLF 不被翻译/双写。"""
    target = tmp_path / "crlf.md"
    atomic_write_text(target, "a\r\nb\r\n")
    assert target.read_bytes() == b"a\r\nb\r\n"


def test_overwrite_preserves_existing_mode(tmp_path: Path) -> None:
    """覆盖既有文件保留原权限位：不把 0644 页无声窄化到 mkstemp 的 0600（code-review 发现①）。"""
    target = tmp_path / "p.md"
    target.write_text("旧", encoding="utf-8")
    os.chmod(target, 0o644)
    atomic_write_text(target, "新")
    assert oct(target.stat().st_mode & 0o777) == oct(0o644)


def test_overwrite_preserves_ownership_best_effort(tmp_path: Path, monkeypatch) -> None:
    """覆盖既有文件时 `chown(tmp, 目标 uid/gid)`——root 改用户 KB 场景不把页改成写进程所有（Codex P2）。

    非 root 无法真把 tmp chown 成他人属主，故只断言"用目标现属主发起了 chown"（保留路径已执行）。"""
    if not hasattr(os, "chown"):
        pytest.skip("平台无 os.chown")
    target = tmp_path / "p.md"
    target.write_text("旧", encoding="utf-8")
    st = target.stat()
    calls: list[tuple[int, int]] = []
    real_chown = os.chown

    def spy_chown(path, uid, gid):
        calls.append((uid, gid))
        return real_chown(path, uid, gid)

    monkeypatch.setattr("guanlan.rawio.os.chown", spy_chown)
    atomic_write_text(target, "新")
    assert calls and calls[-1] == (st.st_uid, st.st_gid)  # 以目标现 uid/gid chown tmp
    assert target.read_text(encoding="utf-8") == "新"


def test_new_file_skips_metadata_preserve(tmp_path: Path, monkeypatch) -> None:
    """新建文件（目标不存在）不尝试 chmod/chown：保持 mkstemp 默认 + 写进程属主。"""
    called = {"chmod": False, "chown": False}
    monkeypatch.setattr("guanlan.rawio.os.chmod", lambda *a, **k: called.__setitem__("chmod", True))
    if hasattr(os, "chown"):
        monkeypatch.setattr("guanlan.rawio.os.chown", lambda *a, **k: called.__setitem__("chown", True))
    atomic_write_text(tmp_path / "new.md", "内容")
    assert called == {"chmod": False, "chown": False}


def test_replace_failure_leaves_old_file_and_no_tmp(tmp_path: Path, monkeypatch) -> None:
    """② + ③：`os.replace` 崩 → 旧文件原封不动、目录不残留 `.tmp`。"""
    target = tmp_path / "p.md"
    target.write_text("旧内容", encoding="utf-8")
    monkeypatch.setattr("guanlan.rawio.os.replace", _boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "新内容")
    assert target.read_text(encoding="utf-8") == "旧内容"  # ② 旧文件不变
    assert list(tmp_path.glob("*.tmp")) == []  # ③ tmp 已清理


def test_replace_failure_on_new_file_leaves_nothing(tmp_path: Path, monkeypatch) -> None:
    """新建文件写失败（manifest 242 场景）：目标不存在、不留半截、无 `.tmp`。"""
    target = tmp_path / "manifest.json"
    monkeypatch.setattr("guanlan.rawio.os.replace", _boom)
    with pytest.raises(OSError):
        atomic_write_text(target, '{"k": "v"}\n')
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_drop_slug_from_page_write_is_atomic(tmp_path: Path, monkeypatch) -> None:
    """集成证明：`remove._drop_slug_from_page` 经 `atomic_write_text`——写崩则内容页不被截断。

    若该写点仍是裸 `Path.write_text`，patch `os.replace` 不会影响它、页会被改写；走原子写则
    `os.replace` 崩使整页保持原字节。"""
    from guanlan.remove import _drop_slug_from_page

    page = tmp_path / "s.md"
    original = "---\nsources:\n- a\n- b\n---\n正文一字不改。\n"
    page.write_text(original, encoding="utf-8")
    monkeypatch.setattr("guanlan.rawio.os.replace", _boom)
    with pytest.raises(OSError):
        _drop_slug_from_page(page, "a")
    assert page.read_text(encoding="utf-8") == original  # 内容页未被截成半截


# --- atomic_write_bytes：逐字节底座（fmrepair CRLF 保真 + gate 原字节回滚走它）---


def test_bytes_writes_verbatim(tmp_path: Path) -> None:
    """逐字节写：CRLF / NUL / 非文本字节一律原样落盘（不做编码/行尾翻译）。"""
    target = tmp_path / "b.bin"
    data = b"a\r\nb\x00\xf0\x9f\x98\x80\r\n"
    atomic_write_bytes(target, data)
    assert target.read_bytes() == data


def test_bytes_overwrite_preserves_mode(tmp_path: Path) -> None:
    """字节底座覆盖同样保留原权限位（fmrepair/gate 回滚不窄化页权限）。"""
    target = tmp_path / "b.md"
    target.write_bytes(b"old")
    os.chmod(target, 0o644)
    atomic_write_bytes(target, b"new")
    assert oct(target.stat().st_mode & 0o777) == oct(0o644)


def test_bytes_replace_failure_leaves_old_and_no_tmp(tmp_path: Path, monkeypatch) -> None:
    """② + ③（字节路径）：`os.replace` 崩 → 旧文件不变、无残留 `.tmp`。"""
    target = tmp_path / "b.md"
    target.write_bytes(b"old")
    monkeypatch.setattr("guanlan.rawio.os.replace", _boom)
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob("*.tmp")) == []


def test_fmrepair_atomic_and_crlf_verbatim(tmp_path: Path, monkeypatch) -> None:
    """集成证明：`fmrepair.repair_page_frontmatter` 经 `atomic_write_bytes`——修好仍 CRLF 逐字，
    且 `os.replace` 崩则坏页保持原字节（不留半写）。"""
    from guanlan.fmrepair import repair_page_frontmatter
    from guanlan.pages import split_frontmatter

    concepts = tmp_path / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    page = concepts / "Foo.md"
    original = (
        '---\r\ntitle: "他说"你好""\r\ntype: concept\r\ntags: []\r\n'
        "sources: []\r\nlast_updated: 2026-06-03\r\n---\r\n正文行。\r\n"
    ).encode("utf-8")

    # ① 正常修复：返回原字节；frontmatter 现可解析为映射；CRLF 与正文逐字保留。
    page.write_bytes(original)
    assert repair_page_frontmatter(page, tmp_path / "wiki") == original
    fixed = page.read_bytes()
    assert "\r\n正文行。\r\n".encode("utf-8") in fixed  # body + CRLF 逐字未动
    block, _body = split_frontmatter(fixed.decode("utf-8"))
    assert isinstance(yaml.safe_load(block), dict)  # 引号已修好、块可解析

    # ② os.replace 崩 → 坏页保持原字节（atomicity）。
    page.write_bytes(original)
    monkeypatch.setattr("guanlan.rawio.os.replace", _boom)
    with pytest.raises(OSError):
        repair_page_frontmatter(page, tmp_path / "wiki")
    assert page.read_bytes() == original


def test_provenance_stamp_is_atomic(tmp_path: Path, monkeypatch) -> None:
    """集成证明：`provenance.stamp_raw_digest` 经 `atomic_write_text`——`os.replace` 崩 →
    捕获 OSError 返回 False，且 source 页未被半写。"""
    from guanlan.provenance import stamp_raw_digest

    page = tmp_path / "wiki" / "sources" / "s.md"
    page.parent.mkdir(parents=True)
    original = "---\ntitle: 源\ntype: source\nsources: []\n---\n摘要正文够长。\n"
    page.write_text(original, encoding="utf-8")
    monkeypatch.setattr("guanlan.rawio.os.replace", _boom)
    assert stamp_raw_digest(page, "sha256:deadbeef") is False  # 写崩被吞 → False
    assert page.read_text(encoding="utf-8") == original  # 页未被半写


# ---------- 读侧对偶：read_text_verbatim / detect_eol / split_eol_lines / dump_frontmatter ----------


def test_read_text_verbatim_does_not_translate_newlines(tmp_path: Path) -> None:
    """逐字读：CRLF / 裸 CR 原样进内存。对照 `Path.read_text`——它把三种行尾一律抹成 `\\n`，
    与逐字写一凑就是「CRLF 静默转 LF」的根因。"""
    target = tmp_path / "mixed.md"
    target.write_bytes(b"a\r\nb\rc\n")
    assert read_text_verbatim(target) == "a\r\nb\rc\n"
    assert target.read_text(encoding="utf-8") == "a\nb\nc\n"  # 对照：标准读归一


def test_read_verbatim_write_roundtrip_is_byte_identical(tmp_path: Path) -> None:
    """读→原样写回 = 逐字节不变：`reindex`/`remove`/`provenance`「只改该改的行」的地基。"""
    target = tmp_path / "p.md"
    original = "---\r\ntitle: x\r\n---\r\n\r\n正文\r\n"
    target.write_bytes(original.encode("utf-8"))
    atomic_write_text(target, read_text_verbatim(target))
    assert target.read_bytes() == original.encode("utf-8")


def test_read_text_verbatim_error_shape_matches_strict_read_text(tmp_path: Path) -> None:
    """异常形状与 `read_text(encoding='utf-8')` 严格档一致（非 UTF-8 → UnicodeDecodeError、
    文件不存在 → OSError），故调用方原有的 except 分支无需跟着改。"""
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(UnicodeDecodeError):
        read_text_verbatim(bad)
    with pytest.raises(OSError):
        read_text_verbatim(tmp_path / "缺.md")


def test_detect_eol_takes_first_occurrence(tmp_path: Path) -> None:
    """取**首个**行尾，而不是「含 CRLF 即判 CRLF」：否则 LF 文件里混进一个 CRLF，
    整份会被重写成 CRLF——修 CRLF 丢失反而制造 LF 丢失。"""
    assert detect_eol("a\r\nb\r\n") == "\r\n"
    assert detect_eol("a\nb\n") == "\n"
    assert detect_eol("a\rb\r") == "\r"
    assert detect_eol("没有换行") == "\n"
    assert detect_eol("a\nb\r\nc\n") == "\n"  # LF 为主、混进一个 CRLF → 仍判 LF


def test_split_eol_lines_only_splits_real_terminators() -> None:
    """只按 CRLF/CR/LF 切。`str.splitlines` 还会切 `\\v`/`\\f`/`\\x1c`/`\\u2028` 等——那些在
    markdown 里是行内字符，切开再按统一 EOL 拼回去等于把它们静默换成换行。"""
    assert split_eol_lines("a\r\nb\rc\nd") == ["a", "b", "c", "d"]
    assert split_eol_lines("") == []
    assert split_eol_lines("a\n") == ["a"]  # 末尾换行不产出幽灵空行
    assert split_eol_lines("a\n\n") == ["a", ""]
    for exotic in "\v\f\x1c\x1d\x1e\x85\u2028\u2029":
        assert split_eol_lines(f"a{exotic}b") == [f"a{exotic}b"]
        assert "\n".join(split_eol_lines(f"a{exotic}b")) == f"a{exotic}b"  # 拼回不变形


def test_dump_frontmatter_default_matches_legacy_literal() -> None:
    """默认 `eol='\\n'` 与被它替换掉的 `f\"---\\n{dumped}---\\n{body}\"` 逐字相同（零行为变更）。"""
    meta = {"title": "含: 冒号", "sources": ["a", "b"]}
    body = "\n正文一\n正文二\n"
    dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    assert dump_frontmatter(meta, body) == f"---\n{dumped}---\n{body}"


def test_dump_frontmatter_crlf_translates_block_only() -> None:
    """`eol='\\r\\n'`：块与两条 `---` 出 CRLF，`body` **逐字不动**（body 自带原行尾）。
    重解析后值往返一致——YAML 把 `\\r\\n` 视作合法换行。"""
    meta = {"title": "x", "sources": ["a"]}
    body = "\r\n正文\r\n"
    out = dump_frontmatter(meta, body, eol="\r\n")
    assert out.startswith("---\r\n") and "---\r\n\r\n正文\r\n" in out
    assert "\n" not in out.replace("\r\n", "")  # 全文无落单 LF
    assert out.endswith(body)
    block = out.split("---\r\n", 1)[1].rsplit("---\r\n", 1)[0]
    assert yaml.safe_load(block) == meta  # CRLF 块仍可解析、值不变
