"""P4.33 T2 前像（`guanlan/preimage.py`）测试：拍前像 → 任意改动 → 回滚 → 范围内逐字节一致。零 LLM，不依赖 web extra。"""

import json
import os
import sys
from pathlib import Path

import pytest

import guanlan.preimage as pre_mod
from guanlan.preimage import PreimageError, is_ready, load, read_trash_log, rollback, take, verify

from conftest import write_page


def _state(root: Path) -> dict:
    """可写范围的完整状态：文件字节、目录、符号链接（.DS_Store 忽略，同前像）。"""
    files, dirs, links = {}, set(), {}
    wiki = root / "wiki"
    for dirpath, dirnames, filenames in os.walk(wiki):
        base = Path(dirpath)
        dirs.add(base.relative_to(root).as_posix())
        for name in list(dirnames):
            p = base / name
            if p.is_symlink():
                links[p.relative_to(root).as_posix()] = os.readlink(p)
                dirnames.remove(name)
        for name in filenames:
            p = base / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                links[rel] = os.readlink(p)
            elif name != ".DS_Store":
                files[rel] = p.read_bytes()
    for name in ("SCHEMA.md", "AGENTAO.md"):
        if (root / name).is_file():
            files[name] = (root / name).read_bytes()
    return {"files": files, "dirs": dirs, "links": links}


@pytest.fixture
def base(kb):
    write_page(kb, "wiki/concepts/甲.md", body="甲的正文")
    write_page(kb, "wiki/entities/乙.md", body="乙的正文")
    write_page(kb, "wiki/sources/doc.md", type="source", sources='["doc"]')
    (kb / "wiki" / "syntheses").mkdir()  # 本来就空的目录：回滚后要还在
    (kb / "raw" / "doc.md").write_text("# 资料\n", encoding="utf-8")
    return kb


def _take(kb):
    dest = kb / "workspace" / "jobs" / "t1" / "before"
    take(kb, dest)
    return dest


def _trash(kb):
    return kb / ".trash" / "job-t1@20261114T100000"


def test_take_writes_ready_last_and_complete(base) -> None:
    dest = _take(base)
    assert is_ready(dest)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert "wiki/concepts/甲.md" in manifest["files"] and "SCHEMA.md" in manifest["files"]
    assert "wiki/syntheses" in manifest["dirs"]
    assert (dest / "files" / "wiki" / "concepts" / "甲.md").read_bytes() == (base / "wiki/concepts/甲.md").read_bytes()
    raw = json.loads((dest / "raw.json").read_text(encoding="utf-8"))
    assert "doc.md" in raw
    pre = load(dest)
    assert verify(pre) == []


def test_rollback_restores_every_kind_of_change(base) -> None:
    before = _state(base)
    dest = _take(base)
    # 改、删、新建、改名、新目录、改根文件、新建根文件
    (base / "wiki/concepts/甲.md").write_text("被 Agent 改坏了\n", encoding="utf-8")
    (base / "wiki/entities/乙.md").unlink()
    write_page(base, "wiki/concepts/丙.md")
    os.replace(base / "wiki/sources/doc.md", base / "wiki/sources/doc-改名.md")
    write_page(base, "wiki/新目录/子目录/丁.md")
    (base / "wiki/空的新目录").mkdir()
    (base / "wiki/syntheses").rmdir()
    (base / "SCHEMA.md").write_text("# 被改了\n", encoding="utf-8")
    (base / "AGENTAO.md").write_text("# 也被改了\n", encoding="utf-8")

    res = rollback(base, load(dest), _trash(base), meta={"task": "t1"})

    assert _state(base) == before
    assert res.ok and res.raw_ok and res.check_ok
    assert set(res.removed) == {"wiki/concepts/丙.md", "wiki/sources/doc-改名.md", "wiki/新目录/子目录/丁.md"}
    assert {"wiki/concepts/甲.md", "wiki/entities/乙.md", "wiki/sources/doc.md", "SCHEMA.md", "AGENTAO.md"} <= set(res.restored)
    # 回滚不丢字节：被覆盖 / 移走的当前版本都在 trash 里，且有逐项记录
    trash = _trash(base)
    assert (trash / "wiki/concepts/甲.md").read_text(encoding="utf-8") == "被 Agent 改坏了\n"
    assert (trash / "wiki/concepts/丙.md").is_file()
    meta, actions = read_trash_log(trash)
    assert meta["task"] == "t1"
    assert {a["path"] for a in actions} >= {"wiki/concepts/甲.md", "wiki/concepts/丙.md", "SCHEMA.md"}
    assert res.trash == ".trash/job-t1@20261114T100000"


def test_no_changes_means_no_trash(base) -> None:
    before = _state(base)
    dest = _take(base)
    res = rollback(base, load(dest), _trash(base))
    assert res.restored == [] and res.removed == [] and res.trash is None
    assert not _trash(base).exists()
    assert _state(base) == before


def test_new_root_file_removed(kb) -> None:
    (kb / "AGENTAO.md").unlink()
    before = _state(kb)
    dest = _take(kb)
    (kb / "AGENTAO.md").write_text("# 新建的\n", encoding="utf-8")
    res = rollback(kb, load(dest), _trash(kb))
    assert "AGENTAO.md" in res.removed and not (kb / "AGENTAO.md").exists()
    assert _state(kb) == before


def test_case_only_rename(base) -> None:
    """只改大小写的改名（doc.md → DOC.md）：大小写不敏感（macOS 默认）与敏感（Linux）的文件系统上，回滚后都是原名原内容。
    靠「先移走多余条目、再写回」的顺序：否则不敏感的文件系统上写回 doc.md 会落到 DOC.md 上，再被当成多余条目移走。"""
    before = _state(base)
    dest = _take(base)
    os.replace(base / "wiki/sources/doc.md", base / "wiki/sources/tmp.md")  # 经中间名，两种文件系统上都真改了名
    os.replace(base / "wiki/sources/tmp.md", base / "wiki/sources/DOC.md")
    res = rollback(base, load(dest), _trash(base))
    assert _state(base) == before
    assert sorted(os.listdir(base / "wiki/sources")) == ["doc.md"]
    assert "wiki/sources/DOC.md" in res.removed and "wiki/sources/doc.md" in res.restored


def test_file_and_dir_swapped(base) -> None:
    before = _state(base)
    dest = _take(base)
    (base / "wiki/concepts/甲.md").unlink()
    write_page(base, "wiki/concepts/甲.md/里面.md")  # 文件换成同名目录
    (base / "wiki/syntheses").rmdir()
    (base / "wiki/syntheses").write_text("目录换成了文件\n", encoding="utf-8")
    rollback(base, load(dest), _trash(base))
    assert _state(base) == before


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接要特权")
def test_symlinks(base) -> None:
    os.symlink("../concepts/甲.md", base / "wiki/entities/链接.md")
    before = _state(base)
    dest = _take(base)
    (base / "wiki/entities/链接.md").unlink()
    os.symlink("../../raw/doc.md", base / "wiki/entities/链接.md")  # 改指向
    os.symlink("/etc/hosts", base / "wiki/concepts/新链接.md")  # 新建链接
    res = rollback(base, load(dest), _trash(base))
    assert _state(base) == before
    assert "wiki/concepts/新链接.md" in res.removed
    assert (_trash(base) / "wiki/concepts/新链接.md").is_symlink()  # 移走的是链接本身，没跟随


def test_hash_not_mtime(base) -> None:
    """只碰 mtime 不算改动；内容变了而大小与 mtime 都不变，照样按哈希判定写回。"""
    dest = _take(base)
    p = base / "wiki/concepts/甲.md"
    st = p.stat()
    os.utime(p, (st.st_atime + 100, st.st_mtime + 100))
    res = rollback(base, load(dest), _trash(base))
    assert "wiki/concepts/甲.md" not in res.restored

    original = p.read_bytes()
    st = p.stat()
    p.write_bytes(original.replace("甲".encode(), "乙".encode()))  # 同长度
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    res = rollback(base, load(dest), _trash(base))
    assert "wiki/concepts/甲.md" in res.restored and p.read_bytes() == original


def test_rollback_is_idempotent_after_crash(base, monkeypatch) -> None:
    """回滚写到一半崩溃（第 2 次写回时抛错），重跑后结果与一次跑完相同，trash 记录合并不覆盖。"""
    before = _state(base)
    dest = _take(base)
    (base / "wiki/concepts/甲.md").write_text("改 1\n", encoding="utf-8")
    (base / "wiki/entities/乙.md").write_text("改 2\n", encoding="utf-8")
    (base / "SCHEMA.md").write_text("改 3\n", encoding="utf-8")
    write_page(base, "wiki/concepts/丙.md")

    real = pre_mod.atomic_write_bytes
    calls = {"n": 0}

    def flaky(target, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("模拟崩溃")
        return real(target, data)

    monkeypatch.setattr(pre_mod, "atomic_write_bytes", flaky)
    with pytest.raises(OSError):
        rollback(base, load(dest), _trash(base))
    monkeypatch.setattr(pre_mod, "atomic_write_bytes", real)

    res = rollback(base, load(dest), _trash(base))
    assert res.ok
    assert _state(base) == before
    # 第一次已移进 trash 的当前版本仍在；第二次移走的也在（同一目录，加序号不覆盖）
    saved = sorted(p.relative_to(_trash(base)).as_posix() for p in _trash(base).rglob("*") if p.is_file())
    assert "wiki/concepts/丙.md" in saved and "wiki/concepts/甲.md" in saved
    assert (_trash(base) / "wiki/concepts/甲.md").read_text(encoding="utf-8") == "改 1\n"
    # 崩溃前移走的也有记录：每项先记后移，记录里的每个 saved_as 都找得到
    _, actions = read_trash_log(_trash(base))
    recorded = {a["saved_as"] for a in actions}
    assert set(saved) - {"manifest.jsonl"} <= recorded


def test_corrupt_preimage_refuses_before_touching_anything(base) -> None:
    dest = _take(base)
    (base / "wiki/concepts/甲.md").write_text("Agent 写的\n", encoding="utf-8")
    write_page(base, "wiki/concepts/丙.md")
    (dest / "files/wiki/entities/乙.md").write_text("前像被篡改\n", encoding="utf-8")
    after_task = _state(base)
    pre = load(dest)
    assert verify(pre) == ["wiki/entities/乙.md"]
    with pytest.raises(PreimageError):
        rollback(base, pre, _trash(base))
    assert _state(base) == after_task  # 一个文件都没动
    assert not _trash(base).exists()


def test_not_ready_cannot_be_loaded(base) -> None:
    dest = _take(base)
    (dest / "READY").unlink()
    assert not is_ready(dest)
    with pytest.raises(PreimageError):
        load(dest)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "raw/doc.md", "wiki/../raw/doc.md", ""])
def test_manifest_paths_must_stay_in_scope(base, bad) -> None:
    dest = _take(base)
    m = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    m["files"][bad] = "0" * 64
    (dest / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(PreimageError):
        load(dest)


def test_raw_change_reported_not_restored(base) -> None:
    dest = _take(base)
    (base / "raw/doc.md").write_text("# 被改了\n", encoding="utf-8")
    (base / "raw/新资料.md").write_text("# 新\n", encoding="utf-8")
    res = rollback(base, load(dest), _trash(base))
    assert not res.raw_ok and not res.ok
    assert {"doc.md", "新资料.md"} <= set(res.raw_changes)
    assert (base / "raw/doc.md").read_text(encoding="utf-8") == "# 被改了\n"  # raw/ 只核对、不恢复


def test_existing_violations_do_not_fail_check(base) -> None:
    """任务开始前就有的阻断违规（在基线里）回滚后照旧存在，不算回滚失败。"""
    (base / "wiki/concepts/坏页.md").write_text("没有 frontmatter\n", encoding="utf-8")
    dest = _take(base)
    assert load(dest).baseline  # 基线里确有违规
    write_page(base, "wiki/concepts/丙.md")
    res = rollback(base, load(dest), _trash(base))
    assert res.check_ok and res.new_violations == []


def test_ds_store_ignored(base) -> None:
    dest = _take(base)
    (base / "wiki/concepts/.DS_Store").write_bytes(b"finder")
    res = rollback(base, load(dest), _trash(base))
    assert res.removed == [] and (base / "wiki/concepts/.DS_Store").exists()


def test_ds_store_in_extra_dir_does_not_block_rollback(base) -> None:
    """多出来的目录里只剩被忽略的 .DS_Store：照样删掉目录，同名文件写得回去（不留残目录、不崩）。"""
    before = _state(base)
    dest = _take(base)
    (base / "wiki/concepts/甲.md").unlink()
    write_page(base, "wiki/concepts/甲.md/里面.md")  # 文件换成同名目录
    (base / "wiki/concepts/甲.md/.DS_Store").write_bytes(b"finder")
    write_page(base, "wiki/新目录/页.md")
    (base / "wiki/新目录/.DS_Store").write_bytes(b"finder")
    res = rollback(base, load(dest), _trash(base))
    assert _state(base) == before
    assert not (base / "wiki/新目录").exists()
    assert "wiki/新目录/.DS_Store" in res.removed  # 进了 trash，不丢字节


def test_long_cjk_names(base) -> None:
    """中文长文件名：Linux 按字节算 255 上限，这里取 60 个汉字（180 字节）以内的名字，两平台都合法。"""
    name = "长" * 60 + ".md"
    before = _state(base)
    dest = _take(base)
    write_page(base, f"wiki/concepts/{name}")
    res = rollback(base, load(dest), _trash(base))
    assert f"wiki/concepts/{name}" in res.removed
    assert _state(base) == before


def test_take_replaces_stale_partial(base) -> None:
    dest = base / "workspace/jobs/t1/before"
    (dest / "files/wiki").mkdir(parents=True)
    (dest / "files/wiki/残留.md").write_text("上次没拍完\n", encoding="utf-8")
    take(base, dest)
    assert not (dest / "files/wiki/残留.md").exists() and is_ready(dest)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 权限位")
def test_restored_file_keeps_mode(base) -> None:
    """被删的页写回后权限位与原来一致（原子写新建的文件默认 0600，组共享的库会读不到）。"""
    p = base / "wiki/entities/乙.md"
    os.chmod(p, 0o644)
    dest = _take(base)
    p.unlink()
    rollback(base, load(dest), _trash(base))
    assert (p.stat().st_mode & 0o777) == 0o644


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接要特权")
def test_links_pointing_outside_are_not_recreated(base) -> None:
    """前像在 workspace/ 下、清单可能被改：目标在库外的符号链接回滚时不补回，列进回执；库内的照常补回。"""
    os.symlink("/etc/hosts", base / "wiki/concepts/外链.md")
    os.symlink("../concepts/甲.md", base / "wiki/entities/内链.md")
    dest = _take(base)
    (base / "wiki/concepts/外链.md").unlink()
    (base / "wiki/entities/内链.md").unlink()
    res = rollback(base, load(dest), _trash(base))
    assert res.links_skipped == ["wiki/concepts/外链.md"]
    assert not (base / "wiki/concepts/外链.md").is_symlink()
    assert os.readlink(base / "wiki/entities/内链.md") == "../concepts/甲.md"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接要特权")
def test_symlinked_wiki_root_is_followed(base, tmp_path_factory) -> None:
    """wiki/ 整个链接到别处（常见做法）：前像跟进去拍内容，回滚经由链接恢复，链接本身保持不变。"""
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "wiki"
    os.replace(base / "wiki", elsewhere)
    os.symlink(elsewhere, base / "wiki")
    before = _state(base)
    dest = _take(base)
    assert "wiki/concepts/甲.md" in load(dest).files
    (base / "wiki/concepts/甲.md").write_text("改坏\n", encoding="utf-8")
    write_page(base, "wiki/concepts/丙.md")
    res = rollback(base, load(dest), _trash(base))
    assert res.ok and _state(base) == before
    assert (base / "wiki").is_symlink() and not (elsewhere / "concepts/丙.md").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接要特权")
def test_symlinked_subdir_refused(base, tmp_path_factory) -> None:
    """wiki/ 里链接进来的子目录：其中的页面不在前像里、回滚恢复不了——拒绝拍前像（调用方据此不启动任务）。"""
    other = tmp_path_factory.mktemp("other")
    os.symlink(other, base / "wiki/外部目录")
    with pytest.raises(PreimageError, match="外部目录"):
        _take(base)
    assert not is_ready(base / "workspace/jobs/t1/before")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接要特权")
def test_wiki_swapped_for_outside_link_refused(base, tmp_path_factory) -> None:
    """拍前像时 wiki/ 是普通目录、回滚时被换成指向库外的链接：遍历会跟进去，回滚会在库外移走、写入——拒绝，库外不动。"""
    dest = _take(base)
    outside = tmp_path_factory.mktemp("outside")
    (outside / "别人的.md").write_text("不是知识库的东西\n", encoding="utf-8")
    os.replace(base / "wiki", base / "wiki.bak")
    os.symlink(outside, base / "wiki")
    with pytest.raises(PreimageError, match="链接状态"):
        rollback(base, load(dest), _trash(base))
    assert (outside / "别人的.md").read_text(encoding="utf-8") == "不是知识库的东西\n"
    assert sorted(p.name for p in outside.iterdir()) == ["别人的.md"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接要特权")
def test_wiki_link_removed_refused(base, tmp_path_factory) -> None:
    """拍前像时 wiki/ 是链接、回滚时链接被删了：照常回滚会在库根新建真目录、与链接目标分叉——拒绝，库根不多出 wiki/。"""
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "wiki"
    os.replace(base / "wiki", elsewhere)
    os.symlink(elsewhere, base / "wiki")
    dest = _take(base)
    (base / "wiki").unlink()
    with pytest.raises(PreimageError, match="链接状态"):
        rollback(base, load(dest), _trash(base))
    assert not (base / "wiki").exists()


def test_relative_paths_resolve_against_root(base, monkeypatch, tmp_path_factory) -> None:
    """`dest` / `trash_dir` 给相对路径时按库根解释，不按进程 cwd（Web 宿主 -C 启动时 cwd 不是库根）。"""
    elsewhere = tmp_path_factory.mktemp("cwd")
    monkeypatch.chdir(elsewhere)
    take(base, Path("workspace/jobs/t1/before"))
    assert is_ready(base / "workspace/jobs/t1/before")
    write_page(base, "wiki/concepts/丙.md")
    res = rollback(base, load(base / "workspace/jobs/t1/before"), Path(".trash/job-t1@20261114T100000"))
    assert res.trash == ".trash/job-t1@20261114T100000"
    assert (_trash(base) / "wiki/concepts/丙.md").is_file()
    assert not any(elsewhere.iterdir())


def test_load_and_discard_resolve_against_root(base, monkeypatch, tmp_path_factory) -> None:
    """cwd 不是库根时，带 root 的 is_ready / load / discard 按库根解释相对路径（同 take / rollback）。"""
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    take(base, Path("workspace/jobs/t1/before"))
    rel = Path("workspace/jobs/t1/before")
    assert is_ready(rel, root=base) and not is_ready(rel)
    assert load(rel, root=base).files
    pre_mod.discard(rel, root=base)
    assert not (base / rel).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接要特权")
def test_inner_links_restored_when_wiki_is_symlinked(base, tmp_path_factory) -> None:
    """wiki/ 链接到库外时，wiki 内部的相对链接仍算库内，回滚照常补回。"""
    elsewhere = tmp_path_factory.mktemp("vol") / "wiki"
    os.replace(base / "wiki", elsewhere)
    os.symlink(elsewhere, base / "wiki")
    os.symlink("../concepts/甲.md", base / "wiki/entities/内链.md")
    dest = _take(base)
    (base / "wiki/entities/内链.md").unlink()
    res = rollback(base, load(dest), _trash(base))
    assert res.links_skipped == []
    assert os.readlink(base / "wiki/entities/内链.md") == "../concepts/甲.md"
