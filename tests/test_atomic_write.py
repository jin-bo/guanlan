"""`atomic_write_text` 原子覆盖写测试（wiki/ + .trash/ 确定性写去半写）。

见 `docs/backlog/notes/openkb-2026-07-反向评审.md` §2：三类——① 成功覆盖、② 写失败时旧文件
不变、③ 失败路径不残留 `.tmp`；外加 `newline=""` 逐字保真，以及经 `remove._drop_slug_from_page`
的集成证明（写崩不把内容页截成半截）。**只避免半写，不做 crash recovery**。
"""

import os
from pathlib import Path

import pytest

from guanlan.rawio import atomic_write_bytes, atomic_write_text


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
    import yaml

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
