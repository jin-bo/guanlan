"""P3.12 `guanlan zip`：白名单打包、私有与隐藏文件排除、符号链接、输出准入（docs/P3.12-知识库打包分享.md）。"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from guanlan.cli import main
from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.zipkb import default_output, zip_entrypoint


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """库根放在 tmp_path/alpha，输出可落在库外的 tmp_path/out。"""
    root = tmp_path / "alpha"
    (root / "raw").mkdir(parents=True)
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "AGENTAO.md").write_text("# AGENTAO\n", encoding="utf-8")
    (root / "SCHEMA.md").write_text("# SCHEMA\n", encoding="utf-8")
    (root / "wiki" / "index.md").write_text("# 索引\n", encoding="utf-8")
    (root / "wiki" / "concepts" / "Foo.md").write_text("正文\n", encoding="utf-8")
    (root / "raw" / "doc.md").write_text("资料\n", encoding="utf-8")
    (tmp_path / "out").mkdir()
    return root


def _zip(kb: Path, out: Path, *extra: str) -> int:
    return main(["-C", str(kb), "zip", "-o", str(out), *extra])


def _names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def _files(path: Path) -> set[str]:
    return {n for n in _names(path) if not n.endswith("/")}


def test_allowlist_only_and_private_data_excluded(kb: Path, capsys):
    """库根只收 AGENTAO.md / SCHEMA.md / wiki/ / raw/；私有配置、日志、会话、工作数据一个都不进。"""
    (kb / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (kb / ".env.local").write_text("SECRET=2\n", encoding="utf-8")
    (kb / "agentao.log").write_text("prompt\n", encoding="utf-8")
    (kb / "agentao.log.1").write_text("prompt\n", encoding="utf-8")
    (kb / ".agentao" / "sessions").mkdir(parents=True)
    (kb / ".agentao" / "sessions" / "s.json").write_text("{}", encoding="utf-8")
    (kb / "workspace" / "uploads").mkdir(parents=True)
    (kb / "workspace" / "uploads" / "a.md").write_text("x\n", encoding="utf-8")
    (kb / ".trash" / "old").mkdir(parents=True)
    (kb / ".trash" / "old" / "p.md").write_text("x\n", encoding="utf-8")
    (kb / "notes.txt").write_text("私人笔记\n", encoding="utf-8")  # 用户随手放的文件：白名单外即不收
    (kb / "graph").mkdir()
    (kb / "graph" / "graph.json").write_text("{}", encoding="utf-8")
    out = kb.parent / "out" / "alpha.zip"

    assert _zip(kb, out) == EXIT_OK
    assert _files(out) == {
        "alpha/AGENTAO.md",
        "alpha/SCHEMA.md",
        "alpha/wiki/index.md",
        "alpha/wiki/concepts/Foo.md",
        "alpha/raw/doc.md",
    }
    report = capsys.readouterr().out
    for name in (".env", ".env.local", "agentao.log", "agentao.log.1", ".agentao/", "workspace/", ".trash/", "notes.txt", "graph/"):
        assert name in report, name
    assert "不检查文件内容" in report


def test_hidden_and_os_junk_inside_allowlisted_dirs(kb: Path, capsys):
    """白名单目录内：以 . 开头的任一路径段、操作系统与编辑器杂项都滤掉并报告（漏报是沉默的，逐一列出）。"""
    w = kb / "wiki"
    for rel in (
        ".DS_Store",
        "._Foo.md",
        "concepts/.hidden.md",
        "Thumbs.db",
        "desktop.ini",
        "~$draft.docx",
        "Foo.md.swp",
        "Foo.md~",
    ):
        (w / rel).write_text("x", encoding="utf-8")
    junk = 6
    if os.name != "nt":  # macOS 文件夹图标文件名带回车，Windows 上不是合法文件名
        (w / "Icon\r").write_text("x", encoding="utf-8")
        junk += 1
    for rel in (".git/config", ".obsidian/app.json", "__MACOSX/x.md"):
        (w / rel).parent.mkdir(parents=True, exist_ok=True)
        (w / rel).write_text("x", encoding="utf-8")
    out = kb.parent / "out" / "a.zip"

    assert _zip(kb, out, "--no-raw") == EXIT_OK
    assert _files(out) == {"alpha/AGENTAO.md", "alpha/SCHEMA.md", "alpha/wiki/index.md", "alpha/wiki/concepts/Foo.md"}
    report = capsys.readouterr().out
    assert "跳过隐藏文件 5 个" in report  # .DS_Store、._Foo.md、concepts/.hidden.md、.git/、.obsidian/
    assert f"跳过系统杂项 {junk} 个" in report  # Thumbs.db、desktop.ini、~$、.swp、~、__MACOSX/（+ Icon\r）


def test_symlink_inside_is_skipped_but_symlinked_raw_root_is_followed(kb: Path, tmp_path: Path, capsys):
    """库内符号链接不跟随（防把库外文件打进去）；raw/ 本身指向库外存储时照常收入其内容。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("key\n", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    (store / "ext.md").write_text("外部资料\n", encoding="utf-8")
    try:
        (kb / "wiki" / "leak.md").symlink_to(secret)
        (kb / "wiki" / "linkdir").symlink_to(store, target_is_directory=True)
        (kb / "raw" / "doc.md").unlink()
        (kb / "raw").rmdir()
        (kb / "raw").symlink_to(store, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本平台不允许创建符号链接")
    out = tmp_path / "out" / "a.zip"

    assert _zip(kb, out) == EXIT_OK
    files = _files(out)
    assert "alpha/raw/ext.md" in files
    assert not any("leak" in n or "linkdir" in n for n in files)
    report = capsys.readouterr().out
    assert "跳过符号链接" in report and "wiki/leak.md" in report and "wiki/linkdir/" in report


def test_output_inside_kb_is_refused(kb: Path, capsys):
    for out in (kb / "a.zip", kb / "workspace" / "a.zip", kb / "raw" / "a.zip"):
        out.parent.mkdir(exist_ok=True)
        assert _zip(kb, out) == EXIT_USAGE
        assert not out.exists()
    assert "位于知识库内" in capsys.readouterr().err


def test_output_inside_symlinked_raw_store_is_refused(kb: Path, tmp_path: Path):
    """raw/ 指向库外存储时，输出写进那个存储同样等于写进 raw/，拒绝。"""
    store = tmp_path / "store"
    store.mkdir()
    try:
        (kb / "raw" / "doc.md").unlink()
        (kb / "raw").rmdir()
        (kb / "raw").symlink_to(store, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本平台不允许创建符号链接")
    assert _zip(kb, store / "a.zip") == EXIT_USAGE
    assert not (store / "a.zip").exists()


def test_existing_output_needs_force(kb: Path, capsys):
    out = kb.parent / "out" / "a.zip"
    out.write_bytes(b"old")
    assert _zip(kb, out) == EXIT_USAGE
    assert out.read_bytes() == b"old"
    assert "--force" in capsys.readouterr().err
    assert _zip(kb, out, "--force") == EXIT_OK
    assert "alpha/wiki/index.md" in _names(out)


def test_missing_output_dir_is_usage_error(kb: Path):
    assert _zip(kb, kb.parent / "nope" / "a.zip") == EXIT_USAGE


def test_default_output_moves_out_of_kb_when_cwd_inside(kb: Path):
    """先 cd 进库再跑是常见用法：默认输出改放库的上一级目录，而不是落在库内被拒。"""
    assert default_output(kb, cwd=kb / "wiki").parent == kb.parent
    elsewhere = kb.parent / "out"
    assert default_output(kb, cwd=elsewhere).parent == elsewhere
    assert default_output(kb, cwd=elsewhere).name.startswith("alpha-")


def test_default_output_from_inside_kb_writes_beside_it(kb: Path, monkeypatch):
    monkeypatch.chdir(kb)
    assert zip_entrypoint(kb) == EXIT_OK
    made = list(kb.parent.glob("alpha-*.zip"))
    assert len(made) == 1
    assert not list(kb.rglob("*.zip"))


def test_zip_layout_utf8_names_and_empty_dirs(kb: Path):
    """顶层是库名目录；非 ASCII 文件名带 UTF-8 标记（Windows 解压不乱码）；空的 raw/ 也作为目录收入。"""
    (kb / "wiki" / "资料.md").write_text("中文\n", encoding="utf-8")
    (kb / "raw" / "doc.md").unlink()
    out = kb.parent / "out" / "a.zip"
    assert _zip(kb, out) == EXIT_OK
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert all(n == "alpha/" or n.startswith("alpha/") for n in names)
        assert "alpha/raw/" in names  # 空目录保留，解压后仍是完整的库骨架
        info = zf.getinfo("alpha/wiki/资料.md")
        assert info.flag_bits & 0x800
        assert zf.read("alpha/wiki/资料.md").decode("utf-8") == "中文\n"
        assert names == sorted(names)
    assert not list(out.parent.glob(".a.zip.tmp-*"))  # 临时文件已改名，不留残留


def test_dry_run_writes_nothing(kb: Path, capsys):
    out = kb.parent / "out" / "a.zip"
    assert _zip(kb, out, "--dry-run") == EXIT_OK
    assert not out.exists()
    assert "预览（未写入）" in capsys.readouterr().out


def test_no_raw_and_with_graph(kb: Path):
    (kb / "graph").mkdir()
    (kb / "graph" / "graph.json").write_text("{}", encoding="utf-8")
    out = kb.parent / "out" / "a.zip"
    assert _zip(kb, out, "--no-raw", "--with-graph") == EXIT_OK
    files = _files(out)
    assert "alpha/graph/graph.json" in files
    assert not any(n.startswith("alpha/raw/") for n in _names(out))


def test_json_contract(kb: Path, capsys):
    (kb / ".env").write_text("SECRET=1\n", encoding="utf-8")
    out = kb.parent / "out" / "a.zip"
    assert _zip(kb, out, "--json") == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["output"] == str(out.resolve())
    assert data["files"] == 5 and data["dry_run"] is False
    assert data["included"] == {"AGENTAO.md": 1, "SCHEMA.md": 1, "wiki/": 2, "raw/": 1}
    assert ".env" in data["excluded_top"]
    assert "alpha/raw/doc.md" in data["entries"]
    assert data["compressed_bytes"] == out.stat().st_size


def test_not_a_kb_is_usage_error(tmp_path: Path):
    (tmp_path / "x").mkdir()
    assert main(["-C", str(tmp_path / "x"), "zip", "-o", str(tmp_path / "a.zip")]) == EXIT_USAGE


def test_kb_is_never_written(kb: Path):
    """只读知识库：打包前后库内文件清单与内容不变。"""
    before = {p: p.read_bytes() for p in kb.rglob("*") if p.is_file()}
    assert _zip(kb, kb.parent / "out" / "a.zip") == EXIT_OK
    after = {p: p.read_bytes() for p in kb.rglob("*") if p.is_file()}
    assert before == after
    assert os.listdir(kb)  # 未被清空


def test_dry_run_ignores_existing_output(kb: Path):
    """预览不写盘，目标已存在不应拦住预览（同一天第二次 --dry-run 是常见用法）。"""
    out = kb.parent / "out" / "alpha.zip"
    out.write_bytes(b"old")
    assert _zip(kb, out, "--dry-run") == EXIT_OK
    assert out.read_bytes() == b"old"


def test_output_inside_symlinked_wiki_storage_rejected(kb: Path, tmp_path: Path):
    """wiki/ 是指向库外存储的符号链接时，输出落进该存储同样算库内，否则下次打包会把 zip 收进去。"""
    store = tmp_path / "wiki-store"
    (kb / "wiki").rename(store)
    (kb / "wiki").symlink_to(store, target_is_directory=True)
    assert _zip(kb, store / "x.zip") == EXIT_USAGE
    assert not (store / "x.zip").exists()


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root 无视目录权限")
def test_unreadable_dir_reported_not_silently_dropped(kb: Path, capsys):
    """读不了的子目录不能被 os.walk 默默吞掉：必须出现在报告里。"""
    locked = kb / "wiki" / "locked"
    locked.mkdir()
    (locked / "p.md").write_text("x\n", encoding="utf-8")
    locked.chmod(0)
    try:
        assert _zip(kb, kb.parent / "out" / "alpha.zip", "--json") == EXIT_OK
    finally:
        locked.chmod(0o755)
    data = json.loads(capsys.readouterr().out)
    assert data["unreadable"] == ["wiki/locked/"]
