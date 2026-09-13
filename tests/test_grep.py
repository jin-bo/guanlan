"""P5.5 grep 字面检索测试（零 LLM，见 docs/P5.5-字面检索.md §8）。

本路的全部承诺就一句：**你打的那个串，字面出现在这一页的这一行**。故用例围着两件事转——
口径钉得死不死（正则/归一/大小写/扫描面），以及**触顶时说没说**（截断静默是本路最不能有的缺陷）。
"""

import json
from pathlib import Path

import pytest

from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.grep import (
    DEFAULT_LIMIT,
    MAX_MATCHES_PER_PAGE,
    SNIPPET_WIDTH,
    grep_entrypoint,
    grep_pages,
    grep_result_dict,
)

FM = "---\ntitle: '{title}'\ntype: 'entity'\ntags: []\nsources: []\nlast_updated: '2026-09-12'\n---\n"


def _page(wiki: Path, rel: str, body: str, *, title: str = "T", raw: str | None = None) -> Path:
    """在 wiki/ 下落一页。`raw` 给定则整页逐字写入（用于无 frontmatter / 畸形页）。"""
    path = wiki / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else FM.format(title=title) + "\n" + body, encoding="utf-8")
    return path


# ── 基本命中：页 / 行号 / 片段 ────────────────────────────────────────────────────
def test_hit_reports_page_line_and_snippet(kb: Path):
    _page(kb / "wiki", "entities/A.md", "第一行无关。\nbge-m3 是嵌入模型。\n")
    r = grep_pages(kb / "wiki", "bge-m3")

    assert len(r.hits) == 1
    h = r.hits[0]
    assert h.page == "wiki/entities/A.md"
    assert h.snippet == "bge-m3 是嵌入模型。"
    assert not r.truncated


def test_line_number_is_of_the_original_file_not_the_body(kb: Path):
    """**行号按原文件计**：frontmatter 不参与匹配，但计入行号——否则照着行号跳过去是错行。"""
    _page(kb / "wiki", "entities/A.md", "命中在这一行。\n")  # FM 7 行 + 空行 + 正文首行 = 第 9 行
    (r,) = grep_pages(kb / "wiki", "命中在这一行").hits
    assert r.line == 9

    text = (kb / "wiki" / "entities" / "A.md").read_text(encoding="utf-8")
    assert text.splitlines()[r.line - 1] == "命中在这一行。"  # 回指原文件确认不偏行


def test_line_number_without_frontmatter_starts_at_one(kb: Path):
    _page(kb / "wiki", "entities/A.md", "", raw="裸正文首行命中。\n第二行。\n")
    (r,) = grep_pages(kb / "wiki", "裸正文").hits
    assert r.line == 1


def test_unclosed_frontmatter_counts_as_no_block(kb: Path):
    """起始 `---` 但无闭合 → `split_frontmatter` 视作无块、全文皆正文，行号从 1 起（口径继承，不另立）。"""
    _page(kb / "wiki", "entities/A.md", "", raw="---\ntitle: 'x'\n命中串在这里。\n")
    (r,) = grep_pages(kb / "wiki", "命中串").hits
    assert r.line == 3


# ── 口径钉死：大小写 / 字面 / 不归一 ──────────────────────────────────────────────
def test_match_is_case_insensitive(kb: Path):
    _page(kb / "wiki", "entities/A.md", "这里是 BGE-M3 大写形式。\n")
    assert len(grep_pages(kb / "wiki", "bge-m3").hits) == 1


def test_pattern_is_literal_not_a_regex(kb: Path):
    """输入**永不当正则**：`.`/`*`/`[` 都是普通字符，否则"你打的串字面出现"这句承诺就是假的。"""
    _page(kb / "wiki", "entities/A.md", "abc 与 a.c 都在这页。\n")
    assert len(grep_pages(kb / "wiki", "a.c").hits) == 1  # 命中字面 a.c，不是 abc
    assert grep_pages(kb / "wiki", ".*").hits == []  # 正则元字符不匹配任意内容
    assert grep_pages(kb / "wiki", "[abc").hits == []  # 非法正则也不报错、只是无命中

    # **正对照**：上面三条都是"查不到"，单看证明不了"当字面"——再钉一条"字面就查得到"。
    _page(kb / "wiki", "entities/B.md", "这页真有 .* 与 [abc 两个串。\n")
    assert len(grep_pages(kb / "wiki", ".*").hits) == 1
    assert len(grep_pages(kb / "wiki", "[abc").hits) == 1


def test_wikilink_brackets_are_searchable(kb: Path):
    """`[[Foo]]` 这种形状是本路最常见的查询之一（正则路径下 `[` 会炸/乱匹配）。"""
    _page(kb / "wiki", "entities/A.md", "正文里提到 [[某实体]] 一次。\n")
    assert len(grep_pages(kb / "wiki", "[[某实体]]").hits) == 1


def test_no_width_or_nfkc_normalization(kb: Path):
    """**不做 NFKC / 全半角归一**（决策P5.5-2）：归一过的"命中"在文件里并不字面存在，会让行号+片段这条证据链失真。

    **带正对照**：光断言"半角查不到"证明不了口径——一页什么都查不到时它同样成立。故同时断言
    全角原样能查到，两条合起来才钉住"只差在归一这一步"。
    """
    _page(kb / "wiki", "entities/A.md", "全角写法 ＧＰＴ 在此。\n")
    assert grep_pages(kb / "wiki", "GPT").hits == []  # 半角查全角：不归一 → 无命中
    assert len(grep_pages(kb / "wiki", "ＧＰＴ").hits) == 1  # 正对照：全角原样查得到


# ── 扫描面：config 页与 frontmatter 都不参与 ──────────────────────────────────────
def test_config_pages_are_never_scanned(kb: Path):
    (kb / "wiki" / "index.md").write_text("# 索引\n这里有 稀有串 但不该被扫到。\n", encoding="utf-8")
    (kb / "wiki" / "log.md").write_text("稀有串 也在时间线里。\n", encoding="utf-8")
    (kb / "wiki" / "overview.md").write_text("稀有串 也在综述里。\n", encoding="utf-8")
    r = grep_pages(kb / "wiki", "稀有串")
    assert r.hits == [] and r.pages_searched == 0


def test_frontmatter_is_not_matched(kb: Path):
    """frontmatter 只是元数据，命中它会让"第 N 行有这个串"指到一行配置上。"""
    _page(kb / "wiki", "entities/A.md", "正文与标题无关。\n", title="稀有标题串")
    assert grep_pages(kb / "wiki", "稀有标题串").hits == []


def test_raw_is_not_scanned(kb: Path):
    """扫描面与 `search` 同为 `wiki/` 非 config 页；`raw/` 明确不在范围内（决策P5.5-3）。"""
    (kb / "raw" / "src.md").write_text("源里有 只在raw的串。\n", encoding="utf-8")
    assert grep_pages(kb / "wiki", "只在raw的串").hits == []


# ── 双封顶：触顶必须说出来 ───────────────────────────────────────────────────────
def test_per_page_cap_truncates_and_says_so(kb: Path):
    """单页命中超上限 → 只列前 N 条，**且 `truncated` 为真**。静默丢是本路最不能有的缺陷。"""
    body = "".join(f"第{i}次出现 稀有串。\n" for i in range(MAX_MATCHES_PER_PAGE + 3))
    _page(kb / "wiki", "entities/A.md", body)
    r = grep_pages(kb / "wiki", "稀有串")
    assert len(r.hits) == MAX_MATCHES_PER_PAGE
    assert r.truncated is True


def test_total_limit_truncates_and_says_so(kb: Path):
    for i in range(4):
        _page(kb / "wiki", f"entities/P{i}.md", "稀有串 在此。\n")
    r = grep_pages(kb / "wiki", "稀有串", limit=2)
    assert len(r.hits) == 2 and r.truncated is True


def test_pages_searched_is_not_distorted_by_truncation(kb: Path):
    """截断只截**命中列表**，"扫了几页"必须照实报——否则回执自己先失真了。"""
    for i in range(5):
        _page(kb / "wiki", f"entities/P{i}.md", "稀有串 在此。\n")
    r = grep_pages(kb / "wiki", "稀有串", limit=1)
    assert r.pages_searched == 5 and len(r.hits) == 1 and r.truncated is True


def test_not_truncated_when_everything_fits(kb: Path):
    """反向用例：没触顶就**不许**报 truncated（否则这面旗子失去意义、用户学会无视它）。"""
    _page(kb / "wiki", "entities/A.md", "稀有串 只出现一次。\n")
    r = grep_pages(kb / "wiki", "稀有串")
    assert r.truncated is False and len(r.hits) == 1


def test_page_at_cap_exactly_is_not_marked_truncated(kb: Path):
    """恰好等于单页上限 → 一条不丢、也**不**报截断（边界不该多报）。"""
    body = "".join(f"第{i}次 稀有串。\n" for i in range(MAX_MATCHES_PER_PAGE))
    _page(kb / "wiki", "entities/A.md", body)
    r = grep_pages(kb / "wiki", "稀有串")
    assert len(r.hits) == MAX_MATCHES_PER_PAGE and r.truncated is False


# ── 入参校验 ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
def test_blank_pattern_raises(kb: Path, bad: str):
    """空串在任何页上都"命中"，不是一个有意义的查询。"""
    with pytest.raises(ValueError, match="为空或纯空白"):
        grep_pages(kb / "wiki", bad)


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_limit_raises(kb: Path, bad: int):
    with pytest.raises(ValueError, match="limit 必须 ≥ 1"):
        grep_pages(kb / "wiki", "x", limit=bad)


# ── 确定性与契约 ────────────────────────────────────────────────────────────────
def test_result_is_byte_stable_across_calls(kb: Path):
    for i in range(3):
        _page(kb / "wiki", f"entities/P{i}.md", "稀有串 甲。\n无关。\n稀有串 乙。\n")
    a = json.dumps(grep_result_dict(grep_pages(kb / "wiki", "稀有串")), ensure_ascii=False)
    b = json.dumps(grep_result_dict(grep_pages(kb / "wiki", "稀有串")), ensure_ascii=False)
    assert a == b


def test_hits_are_ordered_by_page_then_line(kb: Path):
    _page(kb / "wiki", "entities/B.md", "稀有串 在 B。\n")
    _page(kb / "wiki", "entities/A.md", "稀有串 甲。\n无关行。\n稀有串 乙。\n")
    r = grep_pages(kb / "wiki", "稀有串")
    assert [(h.page, h.line) for h in r.hits] == [
        ("wiki/entities/A.md", 9),
        ("wiki/entities/A.md", 11),
        ("wiki/entities/B.md", 9),
    ]


def test_json_contract_shape(kb: Path):
    _page(kb / "wiki", "entities/A.md", "稀有串 在此。\n")
    d = grep_result_dict(grep_pages(kb / "wiki", "稀有串"))
    assert set(d) == {
        "ok", "pattern", "pages_searched", "pages_matched", "truncated", "limit", "results",
    }
    assert d["ok"] is True and d["pattern"] == "稀有串"
    assert set(d["results"][0]) == {"page", "line", "snippet"}


def test_truncation_message_names_both_caps_not_the_hit_count(kb: Path, capsys):
    """单页闸先拦住时，文案必须报 `--limit` 的真实值——报 `len(hits)` 会让人以为上限就是 5。"""
    body = "".join(f"第{i}次 稀有串。\n" for i in range(MAX_MATCHES_PER_PAGE + 3))
    _page(kb / "wiki", "entities/A.md", body)
    grep_entrypoint(kb, pattern="稀有串", limit=50, json_output=False)
    out = capsys.readouterr().out
    assert f"单页至多 {MAX_MATCHES_PER_PAGE} 条" in out and "全库至多 50 条" in out


def test_pages_matched_counts_pages_not_hits(kb: Path):
    _page(kb / "wiki", "entities/A.md", "稀有串 甲。\n稀有串 乙。\n")
    _page(kb / "wiki", "entities/B.md", "稀有串 丙。\n")
    r = grep_pages(kb / "wiki", "稀有串")
    assert len(r.hits) == 3 and r.pages_matched == 2


def test_snippet_folds_whitespace_and_is_bounded(kb: Path):
    _page(kb / "wiki", "entities/A.md", "稀有串" + "  啊" * 400 + "\n")
    (h,) = grep_pages(kb / "wiki", "稀有串").hits
    assert "  " not in h.snippet and len(h.snippet) <= SNIPPET_WIDTH  # 折叠空白 + 有界


def test_unreadable_bytes_do_not_raise(kb: Path):
    """坏字节按 `errors="replace"` 容错（决策P5.0-16 同口径），照扫不抛。"""
    (kb / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
    (kb / "wiki" / "entities" / "bad.md").write_bytes(b"\xff\xfe \xe7\xa8\x80\xe6\x9c\x89\xe4\xb8\xb2\n")
    assert len(grep_pages(kb / "wiki", "稀有串").hits) == 1


# ── CLI 壳 ──────────────────────────────────────────────────────────────────────
def test_entrypoint_prints_hits_and_returns_ok(kb: Path, capsys):
    _page(kb / "wiki", "entities/A.md", "稀有串 在此。\n")
    assert grep_entrypoint(kb, pattern="稀有串", limit=DEFAULT_LIMIT, json_output=False) == EXIT_OK
    out = capsys.readouterr().out
    assert "wiki/entities/A.md:9" in out and "稀有串" in out


def test_entrypoint_json_ok_shape(kb: Path, capsys):
    _page(kb / "wiki", "entities/A.md", "稀有串 在此。\n")
    assert grep_entrypoint(kb, pattern="稀有串", limit=DEFAULT_LIMIT, json_output=True) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["results"][0]["line"] == 9


def test_entrypoint_blank_pattern_is_usage_error(kb: Path, capsys):
    assert grep_entrypoint(kb, pattern="  ", limit=DEFAULT_LIMIT, json_output=False) == EXIT_USAGE
    assert "为空或纯空白" in capsys.readouterr().err


def test_entrypoint_json_error_shape(kb: Path, capsys):
    assert grep_entrypoint(kb, pattern="", limit=DEFAULT_LIMIT, json_output=True) == EXIT_USAGE
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] is False and "error" in d


def test_entrypoint_rejects_non_kb_root(tmp_path: Path, capsys):
    assert grep_entrypoint(tmp_path, pattern="x", limit=DEFAULT_LIMIT, json_output=False) == EXIT_USAGE


def test_no_hit_message(kb: Path, capsys):
    _page(kb / "wiki", "entities/A.md", "无关内容。\n")
    assert grep_entrypoint(kb, pattern="找不到的串", limit=DEFAULT_LIMIT, json_output=False) == EXIT_OK
    assert "（无命中）" in capsys.readouterr().out
