"""P4.32 目录批量入库：扫描、准备产物撞车、逐份准备并归并、整批预检接入确认 / 续跑、出处记录、CLI。"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from pathlib import Path

import pytest
import yaml

from guanlan import oneclick
from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.imageio import ConvertError, ConvertResult
from guanlan.kblock import acquire
from guanlan.oneclick import (
    OneClickError,
    active_task,
    confirm,
    doc_sample,
    load_task,
    precheck,
    prepare,
    prepare_batch,
    recover_stale,
    resume,
    scan_dir,
)
from guanlan.pages import split_frontmatter
from guanlan.provenance import compute_raw_digest, format_digest_value
from guanlan.rawio import raw_slug
from guanlan.stopscope import StopRequested


def _para(n: int, tag: str = "文") -> str:
    sent = (tag * 49) + "。"
    k, r = divmod(n, 50)
    return sent * k + tag * r


def _book(title: str, chapters: int = 3, size: int = 15_000) -> str:
    """3 章 × 1.5 万字 = 4.5 万字：超过 4 万，按章拆成 3 份。"""
    out = [f"# {title}", ""]
    for i in range(1, chapters + 1):
        out += [f"## 第{i}章 题{i}", ""]
        left = size
        while left > 0:
            out += [_para(min(2_000, left), title[0]), ""]
            left -= 2_000
    return "\n".join(out)


def _short(title: str) -> str:
    return f"# {title}\n\n" + _para(3_000, title[0]) + "\n"


class FakeIngest:
    """假 ingest：记调用、建 source 页并 stamp raw_digest（同 test_oneclick）。"""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail = set(fail or ())

    def __call__(self, raw_rel: str, root: Path) -> int:
        self.calls.append(raw_rel)
        if raw_rel in self.fail:
            return 5
        stem = raw_slug(Path(raw_rel).stem)
        page = root / "wiki" / "sources" / f"{stem}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        digest = format_digest_value(raw_rel, compute_raw_digest(root / raw_rel))
        page.write_text(
            f"---\ntitle: {stem}\ntype: source\ntags: []\nsources: []\nlast_updated: 2026-11-12\n"
            f"raw_digest: '{digest}'\n---\n\n摘要\n",
            encoding="utf-8",
        )
        return 0


@pytest.fixture
def batch_dir(tmp_path: Path) -> Path:
    d = tmp_path / "src" / "报告集"
    d.mkdir(parents=True)
    (d / "上册.md").write_text(_book("上册"), encoding="utf-8")  # 拆 3 份
    (d / "下篇.md").write_text(_short("下篇"), encoding="utf-8")  # 不拆
    return d


def _prep(kb: Path, d: Path, **kw) -> dict:
    with acquire(kb, "test"):
        return prepare_batch(kb, d, origin_kind="cli", **kw)


def _confirm(kb: Path, task: dict, ingest, **kw):
    with acquire(kb, "test"):
        return confirm(kb, task["task_id"], ingest=ingest, **kw)


def _resume(kb: Path, task: dict, ingest, **kw):
    with acquire(kb, "test"):
        recover_stale(kb)
        return resume(kb, task["task_id"], ingest=ingest, **kw)


# ── 扫描（§2） ───────────────────────────────────────────────────────────────


def test_scan_keeps_supported_files_and_explains_skips(tmp_path: Path):
    d = tmp_path / "in"
    d.mkdir()
    for name in ("b.md", "a.PDF", "c.docx", "notes.txt", ".hidden.md", ".b.md.source.json", ".DS_Store"):
        (d / name).write_bytes(b"x")
    (d / "empty.md").write_bytes(b"")
    (d / "sub").mkdir()
    (d / "sub" / "deep.md").write_bytes(b"x")
    (d / "link.md").symlink_to(d / "b.md")
    files, skipped = scan_dir(d)
    assert [p.name for p in files] == ["a.PDF", "b.md", "c.docx"]  # 码位排序；扩展名不分大小写
    joined = "\n".join(skipped)
    for frag in ("notes.txt（不支持的格式）", "empty.md（空文件）", "sub/（子目录，不递归）", "link.md（符号链接"):
        assert frag in joined
    assert ".hidden" not in joined and "source.json" not in joined and "DS_Store" not in joined  # 隐藏文件静默跳过
    assert "deep.md" not in joined


def test_scan_markdown_suffix_is_not_collected(tmp_path: Path):
    """`.markdown` 不收：内核只按 `.md` 认 Markdown，收进来会被送去转换器（formats.BATCH_IMPORT_EXTS）。"""
    d = tmp_path / "in"
    d.mkdir()
    (d / "a.md").write_bytes(b"x")
    (d / "b.markdown").write_bytes(b"x")
    files, skipped = scan_dir(d)
    assert [p.name for p in files] == ["a.md"] and any("b.markdown" in s for s in skipped)


def test_scan_rejects_empty_and_oversized(tmp_path: Path, monkeypatch):
    d = tmp_path / "in"
    d.mkdir()
    (d / "notes.txt").write_bytes(b"x")
    with pytest.raises(OneClickError, match="没有可导入"):
        scan_dir(d)
    for i in range(3):
        (d / f"{i}.md").write_bytes(b"x")
    monkeypatch.setattr(oneclick, "BATCH_MAX_FILES", 2)
    with pytest.raises(OneClickError, match="超过单批上限 2 份"):
        scan_dir(d)


@pytest.mark.parametrize(
    ("names", "hit"),
    [
        (("报告.pdf", "报告.md"), "workspace/parsed/报告.md"),  # 样本名相同
        (("A.pdf", "A.origin.pdf"), "workspace/parsed/A.origin.md"),  # 漏报用例：样本名不同，只撞在附属产物上
        (("images.pdf", "images-doc.pdf"), "workspace/parsed/images-doc.md"),  # `images` 改名后撞上
        (("A.pdf", "A.origin.md.pdf"), "workspace/parsed/A.origin.md"),  # 文件撞上另一份的目录
    ],
)
def test_scan_rejects_prepared_path_clash(tmp_path: Path, names, hit):
    d = tmp_path / "in"
    d.mkdir()
    for n in names:
        (d / n).write_bytes(b"x")
    with pytest.raises(OneClickError) as ei:
        scan_dir(d)
    assert hit in str(ei.value), str(ei.value)
    assert not (tmp_path / "workspace").exists()


def test_scan_does_not_flag_toc_lookalike(tmp_path: Path):
    """`A.toc.pdf` 的整本产物是 `A.toc.md`，与 `A` 的 `A.toc.json` 不同名：不误报。"""
    d = tmp_path / "in"
    d.mkdir()
    for n in ("A.pdf", "A.toc.pdf", "A-01.pdf"):
        (d / n).write_bytes(b"x")
    assert len(scan_dir(d)[0]) == 3


def test_footprint_clash_ignores_case():
    """macOS 默认文件系统不分大小写：`A` 与 `a` 同盘位置（同一目录里建不出两者，直接测判定）。"""
    assert oneclick._footprint_clash("A", "a") == "workspace/parsed/A.md"
    assert oneclick._footprint_clash("A", "B") is None


_NFC = unicodedata.normalize("NFC", "한글보고서")
_NFD = unicodedata.normalize("NFD", "한글보고서")  # 音节拆成字母（仍是 `\w`），`raw_slug` 照样保留，与 NFC 只差写法


def test_footprint_clash_ignores_nfc_nfd():
    """#197：只差 NFC / NFD 写法的样本名在 macOS 上是同一批文件，须判相撞（修复前漏报）。

    #201 起 `raw_slug` 先做 NFC，两种写法的样本名本身就相同；比较键仍按 NFC，未规范化的名字传进来也相撞。
    """
    assert _NFC != _NFD and doc_sample(_NFC) == doc_sample(_NFD) == _NFC
    assert oneclick._footprint_clash(doc_sample(_NFC), doc_sample(_NFD)) == f"workspace/parsed/{_NFC}.md"
    assert oneclick._footprint_clash(_NFC, _NFD) == f"workspace/parsed/{_NFC}.md"


def test_batch_slug_clash_ignores_nfc_nfd():
    clashes = oneclick._batch_slug_clashes([{"raw": f"raw/{_NFC}.md"}, {"raw": f"raw/{_NFD}/{_NFD}-01.md"}, {"raw": f"raw/x/{_NFD}.md"}])
    assert len(clashes) == 1 and clashes[0].startswith("批内：")


def test_precheck_slug_clash_ignores_nfc_nfd(kb: Path, tmp_path: Path):
    """盘上已有 NFD 名的 raw（放子目录：macOS 同目录建不出两者），单份导入 NFC 同名 → 预检报冲突。"""
    (kb / "raw" / "旧").mkdir()
    (kb / "raw" / "旧" / f"{_NFD}.md").write_text("旧\n", encoding="utf-8")
    src = tmp_path / "src" / f"{_NFC}.md"
    src.parent.mkdir()
    src.write_text(_short("한글"), encoding="utf-8")
    with acquire(kb, "test"):
        task = prepare(kb, src, origin_kind="cli", dry_run=True)
    assert task["precheck"]["status"] == "conflict"
    assert any("source 页 slug 相同" in d for d in task["precheck"]["details"])


def test_precheck_does_not_flag_target_typed_in_other_form(kb: Path, tmp_path: Path):
    """macOS：盘上 `raw/` 存 NFD 名、本次目标是 NFC 名——是同一个文件，不能报「与自己 slug 相同」（#197 审查发现的回归）。"""
    (kb / "raw" / f"{_NFD}.md").write_text("旧\n", encoding="utf-8")
    if not (kb / "raw" / f"{_NFC}.md").exists():  # 区分写法的文件系统（Linux CI）：硬链接造出「两个路径串、同一个文件」，CI 不许跳过
        os.link(kb / "raw" / f"{_NFD}.md", kb / "raw" / f"{_NFC}.md")
    src = tmp_path / "src" / f"{_NFC}.md"
    src.parent.mkdir()
    src.write_text(_short("한글"), encoding="utf-8")
    with acquire(kb, "test"):
        task = prepare(kb, src, origin_kind="cli", dry_run=True)
    assert not any("slug 相同" in d for d in task["precheck"]["details"]), task["precheck"]


def test_scan_rejects_nfc_nfd_twins_on_disk(tmp_path: Path):
    """真文件：同一目录里只差 NFC / NFD 的两个名字。两种平台都真跑（CI 不许跳过）：
    区分写法的文件系统（Linux）建得出两者 → 扫描拒绝；macOS 建不出（第二个就是第一个）→ 只有一份、照常收下。
    """
    d = tmp_path / "in"
    d.mkdir()
    (d / f"{_NFC}.md").write_bytes(b"x")
    twin = d / f"{_NFD}.md"
    if twin.exists():
        assert [p.name for p in scan_dir(d)[0]] and len(scan_dir(d)[0]) == 1
        return
    twin.write_bytes(b"x")
    with pytest.raises(OneClickError, match="写到同一位置"):
        scan_dir(d)


def test_scan_rejects_names_that_clash_only_after_byte_truncation(tmp_path: Path):
    """漏报用例：两个长中文文件名按 UTF-8 字节截短（NAME_STEM_MAX_BYTES）后才相同。文件名 ≤ 255 字节，Linux 上也建得出。"""
    d = tmp_path / "in"
    d.mkdir()
    a, b = "长" * 65 + "甲", "长" * 65 + "乙"
    assert len((a + ".md").encode()) <= 255 and doc_sample(a) == doc_sample(b)
    (d / f"{a}.md").write_bytes(b"x")
    (d / f"{b}.md").write_bytes(b"x")
    with pytest.raises(OneClickError, match="写到同一位置"):
        scan_dir(d)


# ── 逐份准备并归并（§3、§4） ─────────────────────────────────────────────────


def test_prepare_batch_merges_docs_and_items(kb: Path, batch_dir: Path):
    task = _prep(kb, batch_dir)
    assert task["state"] == "awaiting_confirm" and task["kind"] == "batch"
    # 顶层字段没被逐份字典覆盖
    assert task["sample"] == doc_sample("报告集") and task["upload"] == "src/报告集"
    assert set(task["stages"]) == {"confirm", "land"} and "upload_sha256" not in task
    assert [d["sample"] for d in task["docs"]] == ["上册", "下篇"]
    assert [d["upload"] for d in task["docs"]] == ["src/报告集/上册.md", "src/报告集/下篇.md"]
    assert task["docs"][0]["split"] is True and task["docs"][1]["split"] is False
    assert all(d["stages"] == {"parse": "done", "normalize": "done", "split": "done"} for d in task["docs"])
    assert all(len(d["upload_sha256"]) == 64 for d in task["docs"])
    assert [it["doc"] for it in task["items"]] == [0, 0, 0, 1]
    assert task["items"][3]["raw"] == "raw/下篇.md"
    assert set(task["prepared_sha256"]) == {it["parsed"] for it in task["items"]}
    assert task["precheck"]["status"] == "ok" and [d["status"] for d in task["precheck"]["docs"]] == ["ok", "ok"]
    assert task["estimate_seconds"] == 4 * oneclick.DEFAULT_ITEM_SECONDS
    assert list((kb / "raw").iterdir()) == []
    assert load_task(kb, task["task_id"])["docs"] == task["docs"]


def test_batch_writes_totals_at_top_level(kb: Path, batch_dir: Path, capsys):
    """#198：整批的整理统计与图片警告在准备结束时写进顶层，`--json` 读同名字段即可，不必再去 `docs[*]` 里拼。"""
    (batch_dir / "下篇.md").write_text(_short("下篇") + "\n![图](不存在.png)\n", encoding="utf-8")
    task = _prep(kb, batch_dir)
    assert task["warnings"] and all("下篇" in w for w in task["warnings"])
    assert task["warnings"] == [w for d in task["docs"] for w in d.get("warnings") or []]
    for k in ("headers_removed", "sentences_joined"):
        assert task["normalized"][k] == sum(d["normalized"][k] for d in task["docs"])
    assert not any(isinstance(v, (bool, str)) for v in task["normalized"].values())  # 只合计计数项
    assert not any(k.startswith("toc_") for k in task["normalized"])  # 目录计数是逐份性质
    assert load_task(kb, task["task_id"])["warnings"] == task["warnings"]  # 落进清单
    assert oneclick.import_entrypoint(kb, src=None, status=True, json_output=True, interactive=False) == EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["warnings"] == task["warnings"] and out["normalized"] == task["normalized"]


def test_scan_paths_match_what_prepare_writes(kb: Path, batch_dir: Path):
    """扫描查撞车用的路径 = 准备实际写下的路径（同一个 `doc_sample`，防两边各算一套）。"""
    task = _prep(kb, batch_dir)
    for d in task["docs"]:
        written = {p for p, is_dir in oneclick._doc_footprint(d["sample"]) if (kb / p).exists()}
        assert f"workspace/parsed/{d['sample']}.md" in written
        assert f"workspace/parsed/{d['sample']}.origin.md" in written
    assert (kb / "workspace" / "parsed" / "上册").is_dir()


def test_single_file_task_shape_unchanged(kb: Path, batch_dir: Path):
    with acquire(kb, "test"):
        task = prepare(kb, batch_dir / "上册.md", origin_kind="cli")
    assert "kind" not in task and "docs" not in task and "doc" not in task["items"][0]
    assert "docs" not in task["precheck"]
    assert oneclick.doc_entries(task) == [
        {"upload": "src/报告集/上册.md", "upload_sha256": task["upload_sha256"], "sample": "上册", "split": True, "source_url": None}
    ]


def test_one_failed_doc_fails_whole_batch(kb: Path, batch_dir: Path, monkeypatch):
    (batch_dir / "丙.pdf").write_bytes(b"%PDF")

    def convert(src, **kw):
        raise ConvertError("全部后端失败")

    monkeypatch.setattr(oneclick, "convert_to_markdown", convert)
    with pytest.raises(OneClickError, match="第 3/3 份 丙.pdf 准备失败"):
        _prep(kb, batch_dir)
    failed = max(oneclick.iter_tasks(kb), key=lambda t: t["created"])
    assert failed["state"] == "failed" and "丙.pdf" in failed["reason"]
    assert active_task(kb) is None and list((kb / "raw").iterdir()) == []
    (batch_dir / "丙.pdf").unlink()
    assert _prep(kb, batch_dir)["state"] == "awaiting_confirm"  # 处理后重新导入


def test_failed_batch_keeps_partial_totals(kb: Path, batch_dir: Path, monkeypatch):
    """#198 审查：中途失败的批量，顶层仍有已准备那几份的合计（摘要与 Web 只读顶层）。"""
    (batch_dir / "丙.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(oneclick, "convert_to_markdown", lambda src, **kw: (_ for _ in ()).throw(ConvertError("坏")))
    with pytest.raises(OneClickError):
        _prep(kb, batch_dir)
    failed = max(oneclick.iter_tasks(kb), key=lambda t: t["created"])
    assert failed["state"] == "failed" and "headers_removed" in failed["normalized"]
    assert "整理：" in "\n".join(oneclick.summary_lines(failed, detail=True))


def test_file_removed_after_scan_fails_batch(kb: Path, batch_dir: Path, monkeypatch):
    real = oneclick.scan_dir

    def scan_then_delete(folder):
        files, skipped = real(folder)
        files[1].unlink()
        return files, skipped

    monkeypatch.setattr(oneclick, "scan_dir", scan_then_delete)
    with pytest.raises(OneClickError, match="第 2/2 份 下篇.md 准备失败"):
        _prep(kb, batch_dir)
    assert active_task(kb) is None


def test_stop_during_batch_cancels(kb: Path, batch_dir: Path, monkeypatch):
    real = oneclick._prepare_steps
    calls = []

    def steps(root, src, task, **kw):
        calls.append(src.name)
        if len(calls) == 2:
            raise StopRequested
        return real(root, src, task, **kw)

    monkeypatch.setattr(oneclick, "_prepare_steps", steps)
    with pytest.raises(StopRequested):
        _prep(kb, batch_dir)
    task = max(oneclick.iter_tasks(kb), key=lambda t: t["created"])
    assert task["state"] == "cancelled" and active_task(kb) is None


@pytest.mark.parametrize("rel", [".", "raw", "wiki", "workspace/parsed", "workspace/parsed/sub"])
def test_kb_own_dirs_are_rejected_as_input(kb: Path, rel: str):
    d = kb / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "下篇.md").write_text(_short("下篇"), encoding="utf-8")
    with pytest.raises(OneClickError, match="不能"):
        _prep(kb, d, dry_run=True)


def test_kb_own_dir_symlinked_elsewhere_is_still_rejected(kb: Path, tmp_path_factory):
    """`raw/` 软链到库外：经外部真实路径导入同样被拒（只 resolve 库根时守卫失效）。"""
    outside = tmp_path_factory.mktemp("外盘") / "raw"
    outside.mkdir()
    (outside / "下篇.md").write_text(_short("下篇"), encoding="utf-8")
    (kb / "raw").rmdir()
    (kb / "raw").symlink_to(outside)
    with pytest.raises(OneClickError, match="不能导入库自己的 raw/"):
        _prep(kb, outside, dry_run=True)


def test_uploads_dir_is_still_accepted(kb: Path):
    d = kb / "workspace" / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    (d / "下篇.md").write_text(_short("下篇"), encoding="utf-8")
    task = _prep(kb, d, dry_run=True)
    assert [x["sample"] for x in task["docs"]] == ["下篇"]


def test_empty_scan_error_lists_skipped(tmp_path: Path):
    (tmp_path / "空.md").write_text("", encoding="utf-8")
    with pytest.raises(OneClickError, match="已跳过：空.md（空文件）"):
        scan_dir(tmp_path)


def test_dry_run_batch_writes_parsed_but_no_task(kb: Path, batch_dir: Path):
    task = _prep(kb, batch_dir, dry_run=True)
    assert task["state"] == "awaiting_confirm" and len(task["items"]) == 4
    assert oneclick.iter_tasks(kb) == [] and list((kb / "raw").iterdir()) == []
    assert (kb / "workspace" / "parsed" / "下篇.md").is_file()


# ── 确认、入库、续跑（§5、§7） ────────────────────────────────────────────────


def test_confirm_lands_and_ingests_docs_in_order(kb: Path, batch_dir: Path):
    task = _prep(kb, batch_dir)
    ingest = FakeIngest()
    code, task = _confirm(kb, task, ingest)
    assert code == EXIT_OK and task["state"] == "done"
    assert ingest.calls == [it["raw"] for it in task["items"]]
    assert ingest.calls[-1] == "raw/下篇.md" and ingest.calls[0].startswith("raw/上册/")
    log = (kb / task["log"]).read_text(encoding="utf-8")
    assert "第 1/2 份原件" in log and "第 2/2 份原件" in log
    assert "开始入库（4 篇）" in log and "已落 raw/：4 篇" in log and "一键入库完成：4 篇" in log


def test_confirm_rechecks_every_doc(kb: Path, batch_dir: Path):
    """漏报用例：只按顶层 `sample`（目录名）检查时，第 2 份目标被占用不会被发现。"""
    task = _prep(kb, batch_dir)
    (kb / "raw" / "下篇.md").write_text("别人放的\n", encoding="utf-8")
    with pytest.raises(OneClickError, match="确认前发生了变化"):
        _confirm(kb, task, FakeIngest())
    pc = load_task(kb, task["task_id"])["precheck"]
    assert pc["status"] == "conflict"
    assert [d["status"] for d in pc["docs"]] == ["ok", "conflict"]
    assert any(d.startswith("下篇.md：") for d in pc["details"])
    assert list((kb / "raw").iterdir()) == [kb / "raw" / "下篇.md"]


def test_batch_slug_clash_only_visible_across_docs(kb: Path, tmp_path: Path):
    """漏报用例：逐份预检各自都是 ok，只有批内检查能发现 `上册` 的分卷与另一份原件同 slug。"""
    d = tmp_path / "src" / "撞名"
    d.mkdir(parents=True)
    (d / "上册.md").write_text(_book("上册"), encoding="utf-8")
    (d / "上册-01-第1章-题1.md").write_text(_short("另一份"), encoding="utf-8")
    task = _prep(kb, d)
    pc = task["precheck"]
    assert [x["status"] for x in pc["docs"]] == ["ok", "ok"]
    assert pc["status"] == "conflict" and any(x.startswith("批内：") for x in pc["details"])
    with pytest.raises(OneClickError, match="不可覆盖"):
        _confirm(kb, task, FakeIngest())


def test_failure_in_second_doc_pauses_then_resumes(kb: Path, batch_dir: Path):
    task = _prep(kb, batch_dir)
    code, task = _confirm(kb, task, FakeIngest(fail={"raw/下篇.md"}))
    assert code == 5 and task["state"] == "paused"
    # 第 1 份已写入、已入库的文件在续跑复检时不算第 2 份的冲突
    assert precheck(kb, task)["status"] == "ok"
    ingest = FakeIngest()
    code, task = _resume(kb, task, ingest)
    assert code == EXIT_OK and task["state"] == "done"
    assert ingest.calls == ["raw/下篇.md"]


def test_recheck_reports_the_doc_that_was_modified(kb: Path, batch_dir: Path):
    task = _prep(kb, batch_dir)
    code, task = _confirm(kb, task, FakeIngest(fail={"raw/下篇.md"}))
    first = task["items"][0]["raw"]
    (kb / first).write_text("手工改过\n", encoding="utf-8")
    pc = precheck(kb, task)
    assert [d["status"] for d in pc["docs"]] == ["conflict", "ok"]
    assert any(first in x and x.startswith("上册.md：") for x in pc["details"])


def test_overwrite_applies_to_whole_batch(kb: Path, batch_dir: Path):
    task = _prep(kb, batch_dir)
    assert _confirm(kb, task, FakeIngest())[0] == EXIT_OK
    again = _prep(kb, batch_dir)
    assert again["precheck"]["status"] == "overwritable"
    with pytest.raises(OneClickError, match="确认覆盖"):
        _confirm(kb, again, FakeIngest())
    code, again = _confirm(kb, again, FakeIngest(), overwrite=True)
    assert code == EXIT_OK and again["state"] == "done"


# ── 出处记录（复审） ─────────────────────────────────────────────────────────


def _url_markdown(d: Path, *, stale: bool = False) -> Path:
    (d / "img").mkdir()
    (d / "img" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)  # 碰巧同名的本地图（子目录不扫描）
    src = d / "readme.md"
    src.write_text("# 说明\n\n![](img/shot.png)\n\n" + "正文。" * 100 + "\n", encoding="utf-8")
    rec = {
        "url": "https://e.org/readme.md?token=secret",
        "final_url": "https://e.org/readme.md",
        "sha256": "0" * 64 if stale else hashlib.sha256(src.read_bytes()).hexdigest(),
        "fetched_at": "2026-11-11T00:00:00+08:00",
    }
    (d / ".readme.md.source.json").write_text(json.dumps(rec), encoding="utf-8")
    return src


def test_batch_reads_source_record_per_doc(kb: Path, tmp_path: Path):
    d = tmp_path / "src" / "网页"
    d.mkdir(parents=True)
    _url_markdown(d)
    (d / "本地.md").write_text(_short("本地"), encoding="utf-8")
    task = _prep(kb, d)
    readme = next(x for x in task["docs"] if x["sample"] == "readme")
    assert readme["source_url"] == "https://e.org/readme.md"  # 去掉凭据参数
    assert "source_url" not in next(x for x in task["docs"] if x["sample"] == "本地")
    parsed = kb / "workspace" / "parsed"
    assert not list(parsed.rglob("*.png"))
    text = (parsed / "readme.md").read_text(encoding="utf-8")
    assert "[图](https://e.org/img/shot.png)" in text and "![" not in text
    assert "🔗 https://e.org/readme.md" in "\n".join(oneclick.summary_lines(task))
    assert _confirm(kb, task, FakeIngest())[0] == EXIT_OK
    meta = yaml.safe_load(split_frontmatter((kb / "raw" / "readme.md").read_text(encoding="utf-8"))[0])
    assert meta["origin"] == "https://e.org/readme.md"


def test_batch_stale_source_record_is_ignored(kb: Path, tmp_path: Path):
    d = tmp_path / "src" / "网页"
    d.mkdir(parents=True)
    _url_markdown(d, stale=True)
    task = _prep(kb, d)
    assert "source_url" not in task["docs"][0]
    meta = yaml.safe_load(split_frontmatter((kb / "workspace" / "parsed" / "readme.md").read_text(encoding="utf-8"))[0])
    assert meta["origin"] == "src/网页/readme.md"


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_import_dir_end_to_end(kb: Path, batch_dir: Path, capsys):
    ingest = FakeIngest()
    code = oneclick.import_entrypoint(kb, src=str(batch_dir), yes=True, json_output=True, ingest=ingest, interactive=False)
    assert code == EXIT_OK
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["kind"] == "batch" and out["state"] == "done"
    assert [d["sample"] for d in out["docs"]] == ["上册", "下篇"] and [it["doc"] for it in out["items"]] == [0, 0, 0, 1]
    assert "[1/2] 上册.md → 拆 3 份" in cap.err and "[2/2] 下篇.md → 不拆" in cap.err
    assert len(ingest.calls) == 4


def test_cli_import_dir_dry_run_and_status(kb: Path, batch_dir: Path, capsys):
    (batch_dir / "notes.txt").write_text("x", encoding="utf-8")
    assert oneclick.import_entrypoint(kb, src=str(batch_dir), dry_run=True, interactive=False) == EXIT_OK
    out = capsys.readouterr().out
    assert "目录：src/报告集（2 份原件，跳过 1 项）" in out and "已跳过：notes.txt（不支持的格式）" in out
    assert "合计：4 篇" in out and oneclick.iter_tasks(kb) == []
    _prep(kb, batch_dir)
    assert oneclick.import_entrypoint(kb, src=None, status=True, interactive=False) == EXIT_OK
    assert "等待确认" in capsys.readouterr().out
    code = oneclick.import_entrypoint(kb, src=None, do_resume=True, yes=True, ingest=FakeIngest(), interactive=False)
    assert code == EXIT_OK and active_task(kb) is None


def test_cli_import_dir_clash_is_usage_error(kb: Path, tmp_path: Path, capsys):
    d = tmp_path / "src" / "撞"
    d.mkdir(parents=True)
    (d / "报告.pdf").write_bytes(b"%PDF")
    (d / "报告.md").write_text("x", encoding="utf-8")
    assert oneclick.import_entrypoint(kb, src=str(d), yes=True, ingest=FakeIngest(), interactive=False) == EXIT_USAGE
    assert "写到同一位置" in capsys.readouterr().err
    assert oneclick.iter_tasks(kb) == []


def test_batch_blocks_new_import_until_finished(kb: Path, batch_dir: Path):
    _prep(kb, batch_dir)
    with pytest.raises(OneClickError, match="已有未完成"):
        _prep(kb, batch_dir)
    with acquire(kb, "test"), pytest.raises(OneClickError, match="已有未完成"):
        prepare(kb, batch_dir / "下篇.md", origin_kind="cli")


def test_convert_result_used_for_non_markdown(kb: Path, tmp_path: Path, monkeypatch):
    """非 Markdown 原件走转换器，每份用自己的样本名作 stem。"""
    d = tmp_path / "src" / "pdf"
    d.mkdir(parents=True)
    (d / "一.pdf").write_bytes(b"%PDF")
    (d / "二.pdf").write_bytes(b"%PDF")
    stems = []

    def convert(src, *, stem, **kw):
        stems.append(stem)
        return ConvertResult(markdown=_short(stem), backend="pypdf")

    monkeypatch.setattr(oneclick, "convert_to_markdown", convert)
    task = _prep(kb, d)
    assert stems == ["一", "二"] and [it["raw"] for it in task["items"]] == ["raw/一.md", "raw/二.md"]
