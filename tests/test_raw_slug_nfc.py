"""#201：`raw_slug` 先做 NFC；按旧算法（不做 NFC）建的 source 页经兼容候选认回，认领须 `raw_digest` 确证。

验收：NFC / NFD 新页名一致；旧页仍能识别并写入 `raw_digest`；health 不误报；新旧页并存、旧页被另一篇的
新名命中时归属与撞名判定正确。NFD 文件都放在子目录，macOS 与 Linux 上都建得出（CI 不许跳过）。
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from conftest import make_runner, write_page

from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.health import run_health
from guanlan.ingest import _reject_source_slug_collision, run_ingest
from guanlan.kblock import acquire
from guanlan.oneclick import prepare
from guanlan.pages import load_page
from guanlan.provenance import RAW_DIGEST_KEY, compute_raw_digest, format_digest_value, parse_digest_value, stamp_raw_digest
from guanlan.rawio import find_source_page, legacy_raw_slug, raw_slug, source_identity, source_page_stems
from guanlan.sourcepage import locate_source_page


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _raw(kb: Path, rel: str, text: str = "原文\n") -> Path:
    p = kb / "raw" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _source_page(kb: Path, stem: str, owner: str | None = None, body: str = "实质内容" * 10) -> Path:
    """建 `wiki/sources/<stem>.md`；给 `owner`（相对库根的 raw 路径）则 stamp 指向它的 `raw_digest`。"""
    write_page(kb, f"wiki/sources/{stem}.md", type="source", sources=f'["{stem}"]', body=body)
    page = kb / "wiki" / "sources" / f"{stem}.md"
    if owner is not None:
        assert stamp_raw_digest(page, format_digest_value(owner, compute_raw_digest(kb / owner)))
    return page


@pytest.mark.parametrize(
    ("word", "old_nfd"),
    [("résumé", "re-sume"), ("café", "cafe"), ("がっこう", "か-っこう"), ("Ångström", "A-ngstro-m")],
)
def test_raw_slug_same_for_nfc_and_nfd(word: str, old_nfd: str):
    assert raw_slug(nfd(word)) == raw_slug(nfc(word)) == nfc(word)
    assert legacy_raw_slug(nfd(word)) == old_nfd  # 旧算法的残缺形，只留作兼容候选


def test_page_stems_put_legacy_form_last():
    stems = source_page_stems(nfd("résumé"))
    assert stems[0] == nfc("résumé") and stems[-1] == "re-sume"
    assert source_page_stems(nfc("résumé")) == (nfc("résumé"),)  # NFC 原名没有旧形


# ── 旧页认回（须 raw_digest 确证） ───────────────────────────────────────────


def test_legacy_page_found_when_digest_names_this_raw(kb: Path):
    rel = f"raw/x/{nfd('résumé')}.md"
    _raw(kb, f"x/{nfd('résumé')}.md")
    page = _source_page(kb, "re-sume", owner=rel)
    sources = kb / "wiki" / "sources"
    assert find_source_page(sources, nfd("résumé")) == page
    loc = locate_source_page(kb, rel[len("raw/"):])
    assert loc.page == page and loc.basis == "文件名"
    assert source_identity(sources, nfd("résumé")) == "re-sume"


def test_legacy_page_without_digest_is_not_claimed_by_name(kb: Path):
    """旧形丢了组合符号，没有 `raw_digest` 确证就不按文件名认（宁可显示未收录，也不认错页）。"""
    _raw(kb, f"x/{nfd('résumé')}.md")
    _source_page(kb, "re-sume")
    assert find_source_page(kb / "wiki" / "sources", nfd("résumé")) is None


def test_legacy_form_owned_by_another_raw_is_not_claimed(kb: Path):
    """NFD `café` 的旧形 `cafe` 恰是 `cafe.md` 的页：归属另一篇，不能认成 NFD `café` 的。"""
    _raw(kb, "cafe.md")
    _raw(kb, f"x/{nfd('café')}.md")
    page = _source_page(kb, "cafe", owner="raw/cafe.md")
    sources = kb / "wiki" / "sources"
    assert find_source_page(sources, nfd("café")) is None
    assert find_source_page(sources, "cafe") == page
    assert source_identity(sources, nfd("café")) == nfc("café")


def test_new_name_page_wins_over_legacy(kb: Path):
    """新旧页并存：新名页优先（新算法建的页），旧页不再被认领。"""
    rel = f"raw/x/{nfd('résumé')}.md"
    _raw(kb, f"x/{nfd('résumé')}.md")
    _source_page(kb, "re-sume", owner=rel)
    new = _source_page(kb, nfc("résumé"))
    assert find_source_page(kb / "wiki" / "sources", nfd("résumé")) == new
    assert source_identity(kb / "wiki" / "sources", nfd("résumé")) == nfc("résumé")


def test_ingest_restamps_legacy_page(kb: Path):
    """旧页照常走 ingest：wrapper 告诉 Agent 本篇页是旧页，摄入后把新的 `raw_digest` 写进它，不另建新名页。"""
    rel = f"raw/x/{nfd('résumé')}.md"
    raw = _raw(kb, f"x/{nfd('résumé')}.md")
    page = _source_page(kb, "re-sume", owner=rel)
    raw.write_text("原文（修订）\n", encoding="utf-8")  # 资料更新，旧指纹过期

    def action(root: Path):  # Agent 更新既有页（保留 frontmatter）
        with page.open("a", encoding="utf-8") as fh:
            fh.write("\n补充一段。\n")

    assert run_ingest(rel, root=kb, runner=make_runner(action)) == EXIT_OK
    meta, _ = load_page(page)
    assert parse_digest_value(meta[RAW_DIGEST_KEY]) == (rel, compute_raw_digest(raw))
    assert not (kb / "wiki" / "sources" / f"{nfc('résumé')}.md").exists()


# ── health ──────────────────────────────────────────────────────────────────


def _without_raw(kb: Path) -> list[str]:
    return sorted(f.page for f in run_health(kb / "wiki").findings if f.kind == "health.source_without_raw")


def test_health_accepts_legacy_and_new_page_names(kb: Path):
    """health 只问「这张页有没有原文」：旧形页（即便没有 raw_digest）与新名页都不报无原文。"""
    _raw(kb, f"x/{nfd('résumé')}.md")
    _source_page(kb, "re-sume")
    _source_page(kb, nfc("résumé"))
    _source_page(kb, "孤页")  # 对照：真没有原文的照报
    assert _without_raw(kb) == ["wiki/sources/孤页.md"]


# ── 撞名：旧页被另一篇的新名命中 ─────────────────────────────────────────────


def test_other_raw_hitting_legacy_page_is_rejected(kb: Path, capsys):
    """`cafe.md` 的新名 `cafe` 撞上 NFD `café` 的旧页：摄入 `cafe.md` 被拒（页归属 NFD `café`）；
    NFD `café` 重摄是合法的属主重摄，放行。"""
    _raw(kb, "cafe.md")
    owner = f"raw/x/{nfd('café')}.md"
    _raw(kb, f"x/{nfd('café')}.md")
    _source_page(kb, "cafe", owner=owner)

    def boom(root: Path):
        raise AssertionError("撞页应在跑 agent 前被拒绝")

    assert run_ingest("raw/cafe.md", root=kb, runner=make_runner(boom)) == EXIT_USAGE
    assert "raw/cafe.md" in capsys.readouterr().err
    sources = kb / "wiki" / "sources"
    _reject_source_slug_collision(kb / "raw", (kb / owner).resolve(), sources)  # 属主：不抛


def test_no_legacy_page_means_no_false_collision(kb: Path):
    """新库（没有旧页）：NFD `café` 与 `cafe.md` 新名不同，各自摄入，不误判撞名。"""
    _raw(kb, "cafe.md")
    _raw(kb, f"x/{nfd('café')}.md")
    sources = kb / "wiki" / "sources"
    _reject_source_slug_collision(kb / "raw", (kb / "raw" / "cafe.md").resolve(), sources)
    _reject_source_slug_collision(kb / "raw", (kb / "raw" / "x" / f"{nfd('café')}.md").resolve(), sources)


def test_import_precheck_sees_legacy_page_hit(kb: Path, tmp_path: Path):
    """一键入库：要导入的 `cafe` 撞上 NFD `café` 的旧页，预检就报冲突，不留到逐篇 ingest 才停。"""
    owner = f"raw/x/{nfd('café')}.md"
    _raw(kb, f"x/{nfd('café')}.md")
    _source_page(kb, "cafe", owner=owner)
    src = tmp_path / "src" / "cafe.md"
    src.parent.mkdir()
    src.write_text("# cafe\n\n" + "正文。" * 200 + "\n", encoding="utf-8")
    with acquire(kb, "test"):
        task = prepare(kb, src, origin_kind="cli", dry_run=True)
    assert task["precheck"]["status"] == "conflict"
    assert any("source 页 slug 相同（`cafe`）" in d for d in task["precheck"]["details"]), task["precheck"]


def test_legacy_owner_still_clashes_with_same_new_name(kb: Path):
    """NFD `café` 的身份是旧名 `cafe`，另一篇 NFC `café` 的新名与它的 `raw_slug` 相同：两篇会抢 `café.md`，须判撞。"""
    owner = f"raw/x/{nfd('café')}.md"
    _raw(kb, f"x/{nfd('café')}.md")
    _raw(kb, f"y/{nfc('café')}.md")
    _source_page(kb, "cafe", owner=owner)
    sources = kb / "wiki" / "sources"
    from guanlan.errors import GuanlanError

    with pytest.raises(GuanlanError):
        _reject_source_slug_collision(kb / "raw", (kb / "raw" / "y" / f"{nfc('café')}.md").resolve(), sources)


def test_legacy_digest_matches_across_nfc_nfd(kb: Path):
    """`raw_digest` 里记的是 NFC 写法、盘上名是 NFD：归属确证照样成立。"""
    _raw(kb, f"x/{nfd('résumé')}.md")
    page = _source_page(kb, "re-sume")
    raw = kb / "raw" / "x" / f"{nfd('résumé')}.md"
    assert stamp_raw_digest(page, format_digest_value(f"raw/x/{nfc('résumé')}.md", compute_raw_digest(raw)))
    assert find_source_page(kb / "wiki" / "sources", nfd("résumé")) == page


def test_nfc_typed_stem_still_finds_legacy_page(kb: Path):
    """macOS 上按 NFC 键入、盘上存 NFD：键入形推不出旧名，按 NFD 再推一遍认回旧页（仍须 raw_digest 确证）。"""
    rel = f"raw/x/{nfd('résumé')}.md"
    _raw(kb, f"x/{nfd('résumé')}.md")
    page = _source_page(kb, "re-sume", owner=rel)
    sources = kb / "wiki" / "sources"
    assert find_source_page(sources, nfc("résumé")) == page
    assert source_identity(sources, nfc("résumé")) == "re-sume"
    assert "re-sume" not in source_page_stems(nfc("résumé"))  # health 读盘上真名，不加这一形


def test_nfc_typed_stem_does_not_claim_others_legacy_page(kb: Path):
    _raw(kb, "re-sume.md")
    _source_page(kb, "re-sume", owner="raw/re-sume.md")  # 属于另一篇（真叫 re-sume 的资料）
    assert find_source_page(kb / "wiki" / "sources", nfc("résumé")) is None
