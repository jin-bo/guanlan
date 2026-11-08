"""P3.14 引语核对测试（零 LLM，见 docs/P3.14-引语核对.md §9）。

文件名一律用短 ASCII：长中文文件名在 Linux 上按字节计长，CI 会失败。
"""

import json
from pathlib import Path

from guanlan.quotes import (
    is_label_like,
    is_sequence_label,
    norm,
    pair_quotes,
    quotes_entrypoint,
    run_quotes,
    segments,
)

RAW_A = "第一句是甲乙丙丁戊己庚辛壬癸子丑寅卯辰。第二句是天地玄黄宇宙洪荒日月盈昃辰宿列张。第三句是寒来暑往秋收冬藏闰余成岁律吕调阳。\n"
Q_OK = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰"  # 15 字，逐字出自 RAW_A
Q_SPLICE = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰天地玄黄宇宙洪荒"  # 跨句拼接：两段都在 RAW_A，但中间隔着别的字


def _kb(tmp: Path, raws: dict[str, str], pages: dict[str, str]) -> Path:
    (tmp / "wiki").mkdir(parents=True, exist_ok=True)
    for name in ("index.md", "log.md", "overview.md"):
        (tmp / "wiki" / name).write_text("# x\n", encoding="utf-8")
    for rel, text in raws.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    for rel, text in pages.items():
        p = tmp / "wiki" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp


def _src(raw_rel: str, body: str = "", *, declare: bool = True, digest: str | None = None) -> str:
    fm = "---\ntitle: 'S'\ntype: source\ntags: []\nsources: []\nlast_updated: '2026-11-07'\n"
    if digest:
        fm += f"raw_digest: '{digest}'\n"
    head = f"\n> 原始资料：`{raw_rel}`\n" if declare else "\n"
    return fm + "---\n\n# S\n" + head + "\n" + body + "\n"


def _concept(body: str, sources: list[str]) -> str:
    srcs = ", ".join(f"'{s}'" for s in sources)
    return (
        f"---\ntitle: 'C'\ntype: concept\ntags: []\nsources: [{srcs}]\nlast_updated: '2026-11-07'\n---\n\n# C\n\n"
        + body
        + "\n"
    )


def _kinds(report):
    return [f.kind for f in report.findings]


# ---------- 纯函数：配对、归一、标签 ----------


def test_pair_quotes_resyncs_on_repeated_open_quote():
    quotes, unclosed = pair_quotes("“未闭合文字；“这里是一条完整的正常引语……”")
    assert [q for q, _, _ in quotes] == ["这里是一条完整的正常引语……"]
    assert unclosed == 1


def test_pair_quotes_nested_curly_inside_straight():
    quotes, unclosed = pair_quotes('甲说"把“涌现”能力写进摘要"，乙说"好"')
    assert [q for q, _, _ in quotes] == ["把“涌现”能力写进摘要", "好"]
    assert unclosed == 0


def test_pair_quotes_stray_close_does_not_swallow_later_quote():
    quotes, unclosed = pair_quotes("前文”落单；“正常引语一”和“正常引语二”")
    assert [q for q, _, _ in quotes] == ["正常引语一", "正常引语二"]
    assert unclosed == 1


def test_norm_is_symmetric_for_links_emphasis_footnotes_and_unverified_mark():
    assert norm("[[目标|显示文本]]**强调**句中[4]，(未验证)。") == norm("显示文本强调句中")


def test_segments_split_on_ellipsis_before_punctuation_removal():
    assert segments("甲乙丙……丁戊。") == ["甲乙丙", "丁戊"]


def test_sequence_label_requires_three_short_segments():
    assert is_sequence_label("统一领导—顶层设计—需求统合")
    # 反例：含同样分隔符，但有一段是完整句子
    assert not is_sequence_label("坚持长期主义——这是根本，是关键——不能动摇")


def test_label_like_marks_symbols_and_standalone_placeholder_only():
    assert is_label_like("第 X 次会议")
    assert is_label_like("甲＋乙")
    assert not is_label_like("XML 格式的说明文字")  # X 是单词的一部分，不是占位符


# ---------- 命中、分类 ----------


def test_verbatim_quote_passes(tmp_path):
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f'正文说"{Q_OK}"。')})
    assert run_quotes(kb).findings == []


def test_altered_one_character_is_reported_and_counts_for_strict(tmp_path):
    q = Q_OK.replace("丙", "甲")
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f'正文说"{q}"。')})
    (f,) = run_quotes(kb).findings
    assert f.kind == "altered" and f.strict and f.best_raw == "raw/a.md"


def test_spliced_quote_is_reported(tmp_path):
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f'正文说"{Q_SPLICE}"。')})
    assert _kinds(run_quotes(kb)) == ["altered"]


def test_ellipsis_segments_in_order_pass_but_reversed_do_not(tmp_path):
    ok = "甲乙丙丁戊己庚辛……天地玄黄宇宙洪荒"
    rev = "天地玄黄宇宙洪荒……甲乙丙丁戊己庚辛"
    body = f'顺序"{ok}"。倒序"{rev}"。'
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", body)})
    assert [f.quote for f in run_quotes(kb).findings] == [rev]


def test_segments_split_across_two_raws_do_not_hit(tmp_path):
    raws = {"raw/a.md": "甲乙丙丁戊己庚辛壬癸。\n", "raw/b.md": "天地玄黄宇宙洪荒日月。\n"}
    pages = {
        "sources/a.md": _src("raw/a.md"),
        "sources/b.md": _src("raw/b.md"),
        "concepts/c.md": _concept('合称"甲乙丙丁戊己庚辛……天地玄黄宇宙洪荒"（[[a]]:1）。', ["a", "b"]),
    }
    report = run_quotes(_kb(tmp_path, raws, pages))
    # 两段各在一份 raw：不算命中（passed=0），也不是「别处逐字」（misattributed=0），只能是不符
    assert report.counts.get("passed", 0) == 0 and report.counts.get("misattributed", 0) == 0
    assert _kinds(report) in (["altered"], ["unsupported"])


def test_quote_across_raw_line_break_passes(tmp_path):
    raw = "甲乙丙丁戊己庚\n辛壬癸子丑寅卯辰。\n"
    kb = _kb(tmp_path, {"raw/a.md": raw}, {"sources/a.md": _src("raw/a.md", f'正文说"{Q_OK}"。')})
    assert run_quotes(kb).findings == []


def test_footnote_in_raw_and_removed_in_quote_passes(tmp_path):
    raw = "甲乙丙丁戊己庚辛壬癸[4]子丑寅卯辰。\n"
    kb = _kb(tmp_path, {"raw/a.md": raw}, {"sources/a.md": _src("raw/a.md", f'正文说"{Q_OK}"。')})
    assert run_quotes(kb).findings == []


def test_link_on_either_side_passes(tmp_path):
    raw = "甲乙丙丁[[戊己庚辛|戊己庚辛]]壬癸子丑寅卯辰。\n"
    quote = "甲乙丙丁戊己庚辛[[壬癸页|壬癸]]子丑寅卯辰"
    kb = _kb(tmp_path, {"raw/a.md": raw}, {"sources/a.md": _src("raw/a.md", f'正文说"{quote}"。')})
    assert run_quotes(kb).findings == []


def test_layout_noise_marked_with_ellipsis_passes_unmarked_is_altered(tmp_path):
    raw = "甲乙丙丁戊己庚辛\n学习时报 第3版\n壬癸子丑寅卯辰。\n"
    body = '标出"甲乙丙丁戊己庚辛……壬癸子丑寅卯辰"。未标"甲乙丙丁戊己庚辛壬癸子丑寅卯辰"。'
    kb = _kb(tmp_path, {"raw/a.md": raw}, {"sources/a.md": _src("raw/a.md", body)})
    # 已知限制：没用省略号标出版面噪声的，报 altered（用例写明预期，防止被当缺陷「修掉」）
    assert _kinds(run_quotes(kb)) == ["altered"]


def test_raw_frontmatter_is_stripped_before_matching(tmp_path):
    """防漏报：raw frontmatter 里有与引语相同的值，若不剥就会假性通过。"""
    raw = f"---\ntitle: '{Q_OK}'\n---\n\n完全无关的正文内容在这里写着。\n"
    kb = _kb(tmp_path, {"raw/a.md": raw}, {"sources/a.md": _src("raw/a.md", f'正文说"{Q_OK}"。')})
    assert _kinds(run_quotes(kb)) == ["unsupported"]


def test_fenced_code_and_short_terms_and_config_pages_are_skipped(tmp_path):
    body = '```mermaid\nA["一二三四五六七八九十一二三四五六"]\n```\n短语"两个短词"。\n'
    pages = {"sources/a.md": _src("raw/a.md", body)}
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, pages)
    (kb / "wiki" / "overview.md").write_text('综述"一二三四五六七八九十一二三四五六"\n', encoding="utf-8")
    assert run_quotes(kb).counts.get("candidates", 0) == 0


def test_sequence_label_skipped_but_label_like_still_checked_and_not_strict(tmp_path):
    body = '标签"统一领导—顶层设计—需求统合—组织实施"。句子"甲乙丙丁戊己庚—辛壬癸子丑寅卯丙"。'
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", body)})
    (f,) = run_quotes(kb).findings
    assert f.label_like and f.kind == "altered" and not f.strict


# ---------- 出处范围 ----------


def test_declaration_only_source_is_checked(tmp_path):
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {
        "sources/a.md": _src("raw/a.md"),
        "concepts/c.md": _concept(f'讲话称"{Q_OK}"（[[a]]:1）。', ["a"]),
    })
    report = run_quotes(kb)
    assert report.findings == [] and report.counts["passed"] == 1


def test_digest_only_source_is_checked(tmp_path):
    digest = "raw/a.md@sha256:" + "0" * 64
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {
        "sources/a.md": _src("raw/a.md", declare=False, digest=digest),
        "concepts/c.md": _concept(f'讲话称"{Q_OK}"（[[a]]:1）。', ["a"]),
    })
    assert run_quotes(kb).counts["passed"] == 1


def test_named_source_without_any_raw_is_unreadable_and_not_widened(tmp_path):
    kb = _kb(tmp_path, {"raw/a.md": RAW_A, "raw/b.md": RAW_A}, {
        "sources/a.md": _src("raw/a.md", declare=False),  # 既无声明也无 digest
        "sources/b.md": _src("raw/b.md"),
        "concepts/c.md": _concept(f'讲话称"{Q_OK}"（[[a]]:1）。', ["b"]),
    })
    (f,) = run_quotes(kb).findings
    assert f.kind == "source_unreadable" and f.checked_raw == []  # 不回退到页级 sources(b)


def test_incidental_link_is_not_a_source_and_bare_line_inherits(tmp_path):
    raws = {"raw/a.md": RAW_A, "raw/b.md": "完全不同的另一份原文写在这里供对照使用。\n"}
    body = f'讲话见[[a]]:3；另见[[b]]的对照，又称"{Q_OK}"（:1）。'
    kb = _kb(tmp_path, raws, {
        "sources/a.md": _src("raw/a.md"),
        "sources/b.md": _src("raw/b.md"),
        "concepts/c.md": _concept(body, ["a", "b"]),
    })
    assert run_quotes(kb).counts["passed"] == 1


def test_verbatim_elsewhere_is_counted_not_listed_and_never_strict(tmp_path):
    raws = {"raw/a.md": RAW_A, "raw/b.md": "另一份原文与本句无关的内容写在这里。\n"}
    pages = {
        "sources/a.md": _src("raw/a.md"),
        "sources/b.md": _src("raw/b.md"),
        "concepts/c.md": _concept(f'讲话称"{Q_OK}"（[[b]]:1）。', ["b"]),
    }
    kb = _kb(tmp_path, raws, pages)
    report = run_quotes(kb)
    assert report.findings == [] and report.counts["misattributed"] == 1
    shown = run_quotes(kb, show_misattributed=True).findings
    assert [(f.kind, f.hit_raw, f.strict) for f in shown] == [("misattributed", "raw/a.md", False)]


def test_unreachable_raw_turns_misses_into_scan_incomplete(tmp_path):
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", '正文说"完全不在原文里的一段十五个字以上的话"。')})
    (kb / "raw" / "gone.md").symlink_to(kb / "raw" / "missing-target.md")
    kinds = _kinds(run_quotes(kb))
    assert kinds == ["scan_incomplete"] and "unsupported" not in kinds


def test_broken_dir_link_without_md_suffix_still_marks_scan_incomplete(tmp_path):
    # 指向未挂载盘的目录链接没有 .md 后缀：不能先按扩展名筛掉，否则漏报遍历不完整、误判 unsupported。
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", '正文说"完全不在原文里的一段十五个字以上的话"。')})
    (kb / "raw" / "archive").symlink_to(tmp_path / "unmounted-disk")
    report = run_quotes(kb)
    assert _kinds(report) == ["scan_incomplete"] and report.raw_unreachable == ["raw/archive"]


def test_stray_quote_in_inline_code_does_not_swallow_real_quote(tmp_path):
    # 行内 code 里落单的 `"` 不得翻转配对状态，把其后真正的引语吞掉（漏报）。
    q = Q_OK.replace("丙", "甲")
    page = _src("raw/a.md", f'用 `--fmt "x` 选项；正文说“{q}”。')
    report = run_quotes(_kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": page}))
    assert _kinds(report) == ["altered"] and report.findings[0].quote == q


def test_ocr_hint_excludes_from_strict_but_plain_parsed_by_does_not(tmp_path):
    q = Q_OK.replace("丙", "甲")
    raws = {
        "raw/a.md": "---\nparsed_by: pdf-ocr\n---\n" + RAW_A,
        "raw/b.md": "---\nparsed_by: pypdf\n---\n" + RAW_A,
    }
    pages = {"sources/a.md": _src("raw/a.md", f'正文说"{q}"。'), "sources/b.md": _src("raw/b.md", f'正文说"{q}"。')}
    by_page = {f.page: f for f in run_quotes(_kb(tmp_path, raws, pages)).findings}
    assert not by_page["wiki/sources/a.md"].strict and by_page["wiki/sources/a.md"].ocr_hint == ["parsed_by: pdf-ocr"]
    assert by_page["wiki/sources/b.md"].strict  # 只有后端名、不含 ocr 字样 → 不当作 OCR


# ---------- 入口：退出码、JSON、--page ----------


def test_entrypoint_strict_exit_and_json_contract(tmp_path, capsys):
    q = Q_OK.replace("丙", "甲")
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f'正文说"{q}"。')})
    assert quotes_entrypoint(kb, json_output=True, strict=False) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["summary"]["strict_findings"] == 1
    assert data["findings"][0]["strict"] is True and data["findings"][0]["strict_reason"]
    assert quotes_entrypoint(kb, json_output=True, strict=True) == 6
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_entrypoint_page_filter_and_unknown_page(tmp_path, capsys):
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f'正文说"{Q_OK}"。')})
    assert quotes_entrypoint(kb, json_output=False, strict=False, page="a") == 0
    assert "1 页" in capsys.readouterr().out
    assert quotes_entrypoint(kb, json_output=False, strict=False, page="nope") != 0


# ---------- 评审回归 ----------
def test_superscript_footnote_stripped_before_nfkc():
    from guanlan.quotes import norm

    assert norm("甲乙丙¹丁") == norm("甲乙丙丁")


def test_named_cite_with_heading_anchor():
    from guanlan.quotes import cite_marks

    assert cite_marks("“x”[[A#某节]]:12") == [(3, "named", "A")]


def test_ocr_hint_ignores_ocr_inside_words():
    from guanlan.quotes import _strict_of

    assert _strict_of("altered", False, ["title: Democracy"])[0]
    assert not _strict_of("altered", False, ["parsed_by: PaddleOCR"])[0]


def test_strict_failure_lists_counted_findings_on_whole_kb_run(tmp_path, capsys):
    q = Q_OK.replace("丙", "甲")
    kb = _kb(tmp_path, {"raw/a.md": RAW_A, "raw/b.md": RAW_A}, {
        "sources/a.md": _src("raw/a.md", f'正文说"{q}"。'),
        "sources/b.md": _src("raw/b.md", f'正文说"{Q_OK}"。'),
    })
    assert quotes_entrypoint(kb, json_output=False, strict=True) == 6
    out = capsys.readouterr().out
    assert "1 条计入 strict" in out and "wiki/sources/a.md" in out and q in out


def test_candidate_containing_citation_mark_is_pairing_suspect_not_checked(tmp_path):
    # 一行里引号数目出错时，可能把「…（:1）。其后叙述」整段当成引语；原文引语不会含出处标记
    body = f'讲话称"{Q_OK}（[[a]]:1）。之后又在另一处谈到相关的问题并展开论述"'
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", body)})
    report = run_quotes(kb)
    assert report.findings == [] and report.counts.get("pairing_suspect") == 1


# ---------- 评审回归 ----------


def test_source_slug_resolves_to_sources_dir_despite_same_stem_entity(tmp_path):
    # 同名 entity 页（health.duplicate_stem）排序在 sources/ 之前：只走 resolve_owner 会把出处解析到
    # entity 页、整条引语误判 no_source / source_unreadable。
    root = _kb(
        tmp_path,
        {"raw/a.md": RAW_A},
        {
            "sources/a.md": _src("raw/a.md"),
            "entities/a.md": "---\ntitle: 'A'\ntype: entity\ntags: []\nsources: []\nlast_updated: '2026-11-07'\n---\n\n# A\n",
            "concepts/c.md": _concept(f"页级：“{Q_SPLICE}”。带名：“{Q_SPLICE}”[[a]]:1。", ["a"]),
        },
    )
    report = run_quotes(root)
    assert _kinds(report) == ["altered", "altered"]
    assert [f.scope for f in report.findings] == ["page", "cite"]


def test_quotes_in_whole_line_html_comment_are_skipped(tmp_path):
    root = _kb(
        tmp_path,
        {"raw/a.md": RAW_A},
        {"sources/a.md": _src("raw/a.md"), "concepts/c.md": _concept(f"<!-- “{Q_SPLICE}” -->", ["a"])},
    )
    report = run_quotes(root)
    assert report.findings == [] and report.counts.get("candidates", 0) == 0


def test_ellipsis_variants_split_segments():
    # `⋯` 与全角 `．．．` 在 NFKC 之前切分：不认的话两段被拼成一段，不相连的原文引语误判 altered。
    assert segments("甲乙丙丁戊⋯⋯己庚辛壬癸") == ["甲乙丙丁戊", "己庚辛壬癸"]
    assert segments("甲乙丙丁戊．．．己庚辛壬癸") == ["甲乙丙丁戊", "己庚辛壬癸"]


def test_show_misattributed_lists_entries_in_full_text_output(tmp_path, capsys):
    raws = {"raw/a.md": RAW_A, "raw/b.md": "另一份原文与本句无关的内容写在这里。\n"}
    pages = {
        "sources/a.md": _src("raw/a.md"),
        "sources/b.md": _src("raw/b.md"),
        "concepts/c.md": _concept(f'讲话称"{Q_OK}"（[[b]]:1）。', ["b"]),
    }
    kb = _kb(tmp_path, raws, pages)
    assert quotes_entrypoint(kb, json_output=False, strict=False, show_misattributed=True) == 0
    out = capsys.readouterr().out
    assert "wiki/concepts/c.md:" in out and "raw/a.md" in out


def test_strict_fails_when_raw_walk_incomplete_even_without_strict_findings(tmp_path, capsys):
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f'正文说"{Q_OK}"。')})
    (kb / "raw" / "gone.md").symlink_to(kb / "raw" / "missing-target.md")
    assert quotes_entrypoint(kb, json_output=False, strict=False) == 0  # 不加 --strict 仍只是建议
    capsys.readouterr()
    assert quotes_entrypoint(kb, json_output=False, strict=True) == 6
    assert "raw 遍历不完整" in capsys.readouterr().out


def test_page_lookup_prefers_source_page_on_duplicate_name(tmp_path, capsys):
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {
        "sources/dup.md": _src("raw/a.md", f'正文说"{Q_OK}"。'),
        "entities/dup.md": "---\ntitle: 'E'\ntype: entity\ntags: []\nsources: []\nlast_updated: '2026-11-07'\n---\n\n# E\n",
    })
    report = run_quotes(kb, page="dup")
    assert report.counts.get("candidates") == 1 and report.counts.get("passed") == 1


def test_nfd_raw_filename_matches_nfc_declaration(tmp_path):
    # 盘上 NFD（macOS 拷来的 `é`）、页内 NFC 声明：逐字比会误判 no_source（同 health 的 NFC 口径）。
    import unicodedata

    nfd = unicodedata.normalize("NFD", "raw/café.md")
    root = _kb(tmp_path, {nfd: RAW_A}, {"sources/cafe.md": _src("raw/café.md", f"“{Q_OK}”")})
    report = run_quotes(root)
    assert report.counts.get("passed") == 1 and not report.findings


def test_overlong_cite_name_does_not_crash(tmp_path):
    # Linux 上超长名让 `Path.is_file` 抛 ENAMETOOLONG；须当没命中（source_unreadable），不能整条命令崩。
    long = "名" * 120
    root = _kb(tmp_path, {"raw/a.md": RAW_A}, {"concepts/c.md": _concept(f"“{Q_OK}”[[{long}]]:3", [])})
    assert _kinds(run_quotes(root)) == ["source_unreadable"]


def test_heading_anchor_in_quoted_link_is_not_wording():
    # `[[x#小节]]` 显示的是 `x`：锚点不是措辞，不得被拼进归一后的引语。
    assert norm("[[碳中和#目标]]") == norm("碳中和")
    assert norm("[[碳中和#目标|实现碳中和]]") == norm("实现碳中和")


def test_raw_reached_through_alias_dir_link_still_matches_canonical_declaration(tmp_path):
    # `raw/alias -> sub` 排序在前、先被遍历，防环随后跳过 `raw/sub`：声明写的是 `raw/sub/a.md`，
    # 仍是同一份原文，不能判 no_source。
    root = _kb(tmp_path, {"raw/sub/a.md": RAW_A}, {"sources/a.md": _src("raw/sub/a.md", f"“{Q_OK}”")})
    (root / "raw" / "alias").symlink_to("sub", target_is_directory=True)
    report = run_quotes(root)
    assert report.counts.get("passed") == 1 and not report.findings


def test_unclosed_nested_curly_does_not_swallow_rest_of_line():
    quotes, unclosed = pair_quotes('"一个“例子" 然后 "另一段完整的引语"')
    assert [q for q, _, _ in quotes] == ["一个“例子", "另一段完整的引语"]
    assert unclosed == 1


def test_numbered_heading_inside_quote_is_not_pairing_suspect(tmp_path):
    raw = "（二）：1. 甲乙丙丁戊己庚辛壬癸子丑寅卯辰。\n"
    q = "（二）：1. 甲乙丙丁戊己庚辛壬癸子丑寅卯辰"
    kb = _kb(tmp_path, {"raw/a.md": raw}, {"sources/a.md": _src("raw/a.md", f'正文说"{q}"。')})
    report = run_quotes(kb)
    assert report.counts.get("pairing_suspect", 0) == 0 and report.counts.get("passed") == 1


def test_sentence_end_inside_later_quote_does_not_cut_off_named_cite(tmp_path):
    # 后一条引语自带的 `。` 不是本句句界：否则前一条够不着句末的 [[a]]:1，降成 page 范围（核 b），错判 unsupported。
    raws = {"raw/a.md": RAW_A, "raw/b.md": "另一份原文与本句无关的内容写在这里。\n"}
    pages = {
        "sources/a.md": _src("raw/a.md"),
        "sources/b.md": _src("raw/b.md"),
        "concepts/c.md": _concept(f"他说“{Q_SPLICE}”，又说“这是另一句话。”（[[a]]:1）", ["b"]),
    }
    (f,) = run_quotes(_kb(tmp_path, raws, pages)).findings
    assert (f.kind, f.scope, f.checked_raw) == ("altered", "cite", ["raw/a.md"])


def test_coverage_counts_grams_within_segments_only():
    from guanlan.quotes import coverage
    raw = norm("甲乙丙丁戊己庚辛。中间隔开的别的话。天地玄黄宇宙洪荒。")
    # 一字改动 + 省略号：段内覆盖应与不带省略号时相当，不因跨段片段被压低
    assert coverage(segments("甲乙丙甲戊己庚辛……天地玄黄宇宙洪荒"), raw) >= 0.6


def test_corner_wrapping_curly_quote_is_counted_once(tmp_path):
    q = Q_OK.replace("丙", "甲")
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f"正文「他说“{q}”」。")})
    assert run_quotes(kb, include_corner=True).counts.get("candidates") == 1


def test_corner_quote_is_checked_by_default(tmp_path):
    """模型会用「」写直接引语；默认不收时这些引语整批漏查（P3.14 §14）。默认收，`--no-corner` 才排除。"""
    from guanlan.quotes import main

    bad = Q_OK.replace("丙", "甲")
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f"讲话说「{bad}」。")})
    assert _kinds(run_quotes(kb)) == ["altered"]
    assert run_quotes(kb, include_corner=False).counts.get("candidates", 0) == 0
    assert main(["-C", str(kb), "--strict"]) == 6
    assert main(["-C", str(kb), "--strict", "--no-corner"]) == 0
    assert main(["-C", str(kb), "--strict", "--include-corner"]) == 6  # 旧开关保留为无操作，不报错


def test_cite_mark_inside_later_quote_is_not_earlier_quotes_source(tmp_path):
    """前一条引语不得借后一条引语内文里的 `[[y]]:N` 作出处（那条是配对存疑，自身已被剔除）。"""
    from guanlan.quotes import run_quotes

    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "x.md").write_text("甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲\n", encoding="utf-8")
    (tmp_path / "raw" / "y.md").write_text("无关正文\n", encoding="utf-8")
    src = tmp_path / "wiki" / "sources"
    src.mkdir(parents=True)
    (src / "x.md").write_text("---\ntype: source\n---\n> 原始资料：`raw/x.md`\n", encoding="utf-8")
    (src / "y.md").write_text("---\ntype: source\n---\n> 原始资料：`raw/y.md`\n", encoding="utf-8")
    (tmp_path / "wiki" / "p.md").write_text(
        "---\nsources: [x]\n---\n他说“甲甲甲甲甲甲甲甲甲甲甲甲甲甲甲”又说“乙乙乙乙[[y]]:2乙乙乙乙乙乙乙乙乙乙乙”。\n",
        encoding="utf-8",
    )
    report = run_quotes(tmp_path)
    assert report.counts.get("passed") == 1
    assert not report.findings


def test_dir_qualified_cite_and_page_resolve_by_path_despite_same_stem(tmp_path):
    # 同名 concept 页排序最前：`[[sources/a]]:1` 与 `--page entities/a` 若只按末段解析，会被换成 concepts/a。
    ent = "---\ntitle: 'E'\ntype: entity\ntags: []\nsources: []\nlast_updated: '2026-11-07'\n---\n\n# E\n"
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {
        "sources/a.md": _src("raw/a.md"),
        "concepts/a.md": _concept(f"带目录：“{Q_OK}”[[sources/a]]:1。", []),
        "entities/a.md": ent + f'实体页"{Q_SPLICE}"。\n',
    })
    report = run_quotes(kb, page="concepts/a")
    assert report.counts.get("passed") == 1 and not report.findings
    report = run_quotes(kb, page="entities/a")
    assert [f.page for f in report.findings] == ["wiki/entities/a.md"]


def test_ime_style_ellipsis_variants_split_segments():
    assert segments("甲乙丙丁戊···己庚辛壬癸") == ["甲乙丙丁戊", "己庚辛壬癸"]
    assert segments("甲乙丙丁戊。。。己庚辛壬癸") == ["甲乙丙丁戊", "己庚辛壬癸"]


def test_bom_raw_keeps_frontmatter_ocr_hint(tmp_path):
    q = Q_OK.replace("丙", "甲")
    kb = _kb(tmp_path, {"raw/a.md": "﻿---\nparsed_by: pdf-ocr\n---\n" + RAW_A},
             {"sources/a.md": _src("raw/a.md", f'正文说"{q}"。')})
    (f,) = run_quotes(kb).findings
    assert f.kind == "altered" and not f.strict and f.ocr_hint == ["parsed_by: pdf-ocr"]


def test_bom_page_keeps_frontmatter_sources(tmp_path):
    kb = _kb(tmp_path, {"raw/a.md": RAW_A},
             {"sources/a.md": _src("raw/a.md"), "concepts/c.md": "﻿" + _concept(f'讲话称"{Q_OK}"。', ["a"])})
    report = run_quotes(kb)
    assert report.findings == [] and report.counts["passed"] == 1


def test_cite_syntax_inside_inline_code_is_not_a_source(tmp_path):
    raws = {"raw/a.md": RAW_A, "raw/b.md": "完全不同的另一份原文写在这里供对照使用。\n"}
    kb = _kb(tmp_path, raws, {
        "sources/a.md": _src("raw/a.md"),
        "sources/b.md": _src("raw/b.md"),
        "concepts/c.md": _concept(f'讲话称"{Q_OK}"，写法如 `[[b]]:1`。', ["a"]),
    })
    report = run_quotes(kb)
    assert report.findings == [] and report.counts["passed"] == 1


def test_bom_source_page_keeps_raw_digest_when_cited_from_other_page(tmp_path):
    # 先经 claims_of（概念页的 frontmatter sources）读到带 BOM 的 source 页：须剥 BOM，否则 raw_digest 丢失、误判 no_source。
    digest = "raw/a.md@sha256:" + "0" * 64
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {
        "sources/a.md": "﻿" + _src("raw/a.md", declare=False, digest=digest),
        "concepts/c.md": _concept(f'讲话称"{Q_OK}"。', ["a"]),
    })
    report = run_quotes(kb)
    assert report.findings == [] and report.counts["passed"] == 1


def test_markdown_link_in_raw_is_reduced_to_text():
    assert norm("见[文字说明](http://example.com/x)一文") == norm("见文字说明一文")
    assert norm("图![示意](a.png)后") == norm("图示意后")


def test_colon_number_after_close_quote_is_not_bare_cite():
    from guanlan.quotes import cite_marks
    assert [m[1] for m in cite_marks("称“甲乙”：2024 年起施行；见（:12）")] == ["bare"]
    assert cite_marks("（二）：1. 坚持") == []


def test_uppercase_md_raw_is_read(tmp_path):
    # ingest 按 suffix.lower() 收 `.MD`：只认小写会把整页引语误判 no_source。
    kb = _kb(tmp_path, {"raw/a.MD": RAW_A}, {"sources/a.md": _src("raw/a.MD", f"他说“{Q_OK}”。")})
    rep = run_quotes(kb)
    assert rep.counts.get("passed") == 1 and not rep.findings


def test_non_string_frontmatter_sources_entry_is_skipped(tmp_path):
    # `sources: [null]` 不得被 str() 成 "None" 去解析同名 source 页。
    page = _concept(f"他说“{Q_OK}”。", []).replace("sources: []", "sources: [null]")
    kb = _kb(tmp_path, {"raw/n.md": RAW_A}, {"sources/None.md": _src("raw/n.md"), "concepts/c.md": page})
    assert _kinds(run_quotes(kb, page="c")) == ["no_source"]


def test_page_arg_path_form_does_not_fall_back_to_other_dir(tmp_path):
    # `--page entities/a` 不存在时不得按末段静默换成 concepts/a。
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"concepts/a.md": _concept(f"他说“{Q_OK}”。", [])})
    assert quotes_entrypoint(kb, json_output=False, strict=False, page="entities/a") != 0


def test_bare_mark_after_tongshang_is_recognized():
    from guanlan.quotes import cite_marks

    assert [m[1] for m in cite_marks("见[[a]]:3，又“甲”（同上:12）")] == ["named", "bare"]
    assert [m[1] for m in cite_marks("“甲”（同篇：12）")] == ["bare"]


def test_page_arg_with_wrong_dir_case_still_treated_as_source(tmp_path):
    # 大小写不敏感的盘上 `Sources/a` 也 is_file；不得以错大小写的路径当成非 source 页。
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f"他说“{Q_OK}”。")})
    rep = run_quotes(kb, page="Sources/a")
    assert rep.counts.get("passed") == 1 and not rep.findings


# ---------- 性能：raw 按需归一 ----------


def test_page_run_normalizes_only_raws_it_needs(tmp_path, monkeypatch):
    import guanlan.quotes as q

    unrelated = "无关原文的独有标记甲甲甲乙乙乙丙丙丙丁丁丁。\n"
    kb = _kb(tmp_path, {"raw/a.md": RAW_A, "raw/z.md": unrelated},
             {"sources/a.md": _src("raw/a.md", f'正文说"{Q_OK}"。'), "sources/z.md": _src("raw/z.md")})
    seen: list[str] = []
    real_norm = q.norm
    monkeypatch.setattr(q, "norm", lambda s: (seen.append(s), real_norm(s))[1])
    report = run_quotes(kb, page="a")
    assert report.counts["passed"] == 1
    # 引语在本页范围内命中：别的 raw 不该被归一（全库 raw 归一是 --page 的主要耗时）
    assert not any("独有标记" in s for s in seen)


def test_page_run_still_scans_all_raws_for_verbatim_elsewhere(tmp_path):
    # 按需归一不能漏掉「别处逐字」：范围内不中时仍须逐份 raw 核对
    raws = {"raw/a.md": RAW_A, "raw/b.md": "另一份原文与本句无关的内容写在这里。\n"}
    pages = {
        "sources/a.md": _src("raw/a.md"),
        "sources/b.md": _src("raw/b.md"),
        "concepts/c.md": _concept(f'讲话称"{Q_OK}"（[[b]]:1）。', ["b"]),
    }
    report = run_quotes(_kb(tmp_path, raws, pages), page="c", show_misattributed=True)
    assert [(f.kind, f.hit_raw) for f in report.findings] == [("misattributed", "raw/a.md")]


def test_page_run_still_reports_undecodable_raw_and_fails_strict(tmp_path):
    # 解码仍在遍历时做：--page 下与本页无关的坏 raw 也要记入遍历不完整，--strict 照旧失败（P3.14-7）
    kb = _kb(tmp_path, {"raw/a.md": RAW_A}, {"sources/a.md": _src("raw/a.md", f'正文说"{Q_OK}"。')})
    (kb / "raw" / "bad.md").write_bytes(b"\xff\xfe\x00bad")
    report = run_quotes(kb, page="a", strict=True)
    assert report.raw_unreachable == ["raw/bad.md"] and report.ok is False
