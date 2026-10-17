"""ingest 页面名录与 overview 结构（`guanlan/catalog.py`）。**零 LLM。**

名录是 ingest 语义查重的依据（不再整读 index），漏一页、丢一个别名都是**沉默的**——模型会以为名录是
全集、照常建出重复页。所以这里的主断言是**完整性**：把名录按 `CATALOG_FORMAT` 解析回来，逐页对照
磁盘枚举与 frontmatter，一页不多、一页不少、别名逐字一致。唯一的有意省略是 `wiki/sources/`（按路径，
不按 type），其页数如实计入 `omitted_sources`、写进 prompt。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_runner, write_page

from guanlan import catalog as catalog_mod
from guanlan.catalog import build_catalog, overview_outline
from guanlan.errors import EXIT_OK
from guanlan.ingest import build_ingest_prompt, run_ingest
from guanlan.pages import alias_index, iter_pages, link_stem, load_page

SOURCES = "wiki/sources/"


def _parse(text: str) -> dict[str, list]:
    """按 `CATALOG_FORMAT` 把名录解析回 {页路径: [标题, 别名, 标记]}——测试侧的独立实现。"""
    out: dict[str, list] = {}
    current: str | None = None
    for line in text.splitlines():
        rec = json.loads(line)
        if isinstance(rec, dict):
            current = rec["dir"]
            continue
        assert current is not None, "页记录出现在任何节头之前"
        name, *rest = rec
        rest += [None] * (3 - len(rest))
        path = f"{current}/{name}.md"
        assert path not in out, f"名录重复列出 {path}"
        out[path] = rest
    return out


def _page(kb: Path, rel: str, frontmatter: str, body: str = "正文") -> None:
    p = kb / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")


def _assert_complete(kb: Path) -> dict[str, list]:
    """名录 ⇔ 磁盘（除 `wiki/sources/`）：页集合相等、省略数如实；标题、别名与 frontmatter 逐字对应；
    非 sources 页在别名解析表里的每个键都能在名录里找到。"""
    wiki = kb / "wiki"
    cat = build_catalog(wiki)
    parsed = _parse(cat.text)
    every = {p.relative_to(kb).as_posix(): p for p in iter_pages(wiki)}
    disk = {rel: p for rel, p in every.items() if not rel.startswith(SOURCES)}
    assert set(parsed) == set(disk)
    assert cat.pages == len(disk)
    assert cat.omitted_sources == len(every) - len(disk)
    for rel, (title, aliases, flag) in parsed.items():
        meta, _ = load_page(disk[rel])
        if not isinstance(meta, dict):
            assert flag == "frontmatter-unreadable"
            continue
        assert flag is None
        expected_title = meta.get("title")
        if isinstance(expected_title, str) and expected_title.strip() and expected_title != disk[rel].stem:
            assert title == expected_title
        else:
            assert title is None
        expected_aliases = [a for a in (meta.get("aliases") or []) if isinstance(a, str)]
        assert (aliases or []) == expected_aliases
    # 解析口径对照：alias_index 认的每个别名（经 link_stem 归一），名录里都有原文可对上。
    listed = {
        (link_stem(a), Path(rel).stem.lower())
        for rel, (_t, aliases, _f) in parsed.items()
        for a in aliases or []
    }
    source_stems = {Path(rel).stem.lower() for rel in every if rel.startswith(SOURCES)}
    for key, owner in alias_index(wiki).items():
        if owner in source_stems:
            continue  # sources 页的别名不进名录（门禁照常覆盖）
        assert any(a == key and o == owner for a, o in listed), f"别名 {key!r} 未出现在名录"
    return parsed


def test_catalog_lists_every_page_with_aliases_verbatim(kb: Path):
    _page(kb, "wiki/entities/Alpha.md", 'title: "Alpha"\ntype: entity\naliases: ["甲", "A|B", "引号\\"q", "反斜\\\\杠", "换\\n行", "😀", "[[x]]"]')
    _page(kb, "wiki/concepts/beta.md", 'title: "贝塔概念"\ntype: concept\naliases: [Beta, 2024, null, "β"]')  # 非字符串别名不收
    _page(kb, "wiki/concepts/a.b.c.md", 'title: "a.b.c"\ntype: concept')  # 文件名含点
    _page(kb, "wiki/concepts/tab\tq\"uote.md", 'title: "T"\ntype: concept')  # 文件名含 Tab 与引号
    _page(kb, "wiki/sources/beta.md", 'title: "beta"\ntype: source')  # 与 concepts/beta 同名、在 sources 下 → 省略
    (kb / "wiki" / "entities" / "Broken.md").write_text("---\ntitle: [坏\n---\n正文\n", encoding="utf-8")

    parsed = _assert_complete(kb)
    assert parsed["wiki/entities/Alpha.md"][1] == ["甲", "A|B", '引号"q', "反斜\\杠", "换\n行", "😀", "[[x]]"]
    assert parsed["wiki/concepts/beta.md"][:2] == ["贝塔概念", ["Beta", "β"]]
    assert parsed["wiki/entities/Broken.md"] == [None, None, "frontmatter-unreadable"]
    assert "wiki/index.md" not in parsed and "wiki/overview.md" not in parsed  # config 页不列
    assert "wiki/sources/beta.md" not in parsed


def test_catalog_omits_sources_by_path_not_by_type(kb: Path):
    """省略只看路径：type 写成 source 却躺在 concepts/ 的页照列（不被藏起来）；sources/ 下的页
    哪怕 type 写成 concept 也省略并计数；子目录同样算 sources/；根下名为 sources.md 的页不受影响。"""
    _page(kb, "wiki/concepts/误标.md", 'title: "误标"\ntype: source\naliases: ["别名甲"]')
    _page(kb, "wiki/sources/错型.md", 'title: "错型"\ntype: concept')
    _page(kb, "wiki/sources/2024/深层.md", 'title: "深层"\ntype: source')
    _page(kb, "wiki/sources.md", 'title: "sources"\ntype: concept')

    cat = build_catalog(kb / "wiki")
    parsed = _assert_complete(kb)
    assert set(parsed) == {"wiki/concepts/误标.md", "wiki/sources.md"}
    assert parsed["wiki/concepts/误标.md"][1] == ["别名甲"]
    assert cat.omitted_sources == 2


def test_catalog_reopens_section_when_directory_pages_are_not_adjacent(kb: Path):
    """`iter_pages` 按完整路径排序：`a/x` < `a/y/z` < `a/zz`，同目录页被子目录隔开——必须再开一节。"""
    for rel in ("wiki/concepts/x.md", "wiki/concepts/y/z.md", "wiki/concepts/zz.md"):
        write_page(kb, rel)
    parsed = _assert_complete(kb)
    assert set(parsed) == {"wiki/concepts/x.md", "wiki/concepts/y/z.md", "wiki/concepts/zz.md"}


def test_catalog_covers_pages_beyond_index_read_truncation(kb: Path):
    """index 超 2000 行时 `read_file` 默认只回前 2000 行；名录不依赖 index，2,500 页一页不落。"""
    lines = ["# 索引", "", "## Concepts"]
    for i in range(2500):
        write_page(kb, f"wiki/concepts/c{i:04d}.md")
        lines.append(f"- [c{i:04d}](concepts/c{i:04d}.md) — 第 {i} 页")
    (kb / "wiki" / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    parsed = _assert_complete(kb)
    assert len(parsed) == 2500
    prompt = build_ingest_prompt(kb, "doc.md")
    assert '["c2499","T"]' in prompt  # 第 2,500 页（index 第 2,503 行）在 prompt 里


def test_catalog_size_scales_linearly_and_stays_under_index(kb: Path):
    """合成 1,000 / 5,000 页：名录字符数近似线性，且小于同库 index。只比字符/字节，不估 token。"""
    sizes = {}
    for n in (1000, 5000):
        root = kb.parent / f"kb{n}"
        wiki = root / "wiki"
        index = ["# 索引", "", "## Entities"]
        for i in range(n):
            _page(root, f"wiki/entities/实体{i:05d}.md", f'title: "实体{i:05d}"\ntype: entity\naliases: ["别名{i}"]')
            index.append(f"- [实体{i:05d}](entities/实体{i:05d}.md) — 一句话说明第 {i} 个实体（别名：别名{i}）")
        cat = build_catalog(wiki)
        assert cat.pages == n
        index_chars = len("\n".join(index))
        assert cat.chars < index_chars
        sizes[n] = cat.chars
    assert 4.5 < sizes[5000] / sizes[1000] < 5.5


def test_ingest_prompt_carries_catalog_and_outline(kb: Path):
    _page(kb, "wiki/entities/Alpha.md", 'title: "Alpha"\ntype: entity\naliases: ["甲"]')
    (kb / "wiki" / "overview.md").write_text("# 综述\n\n正文\n\n## 角度一\n\n```\n# 不是标题\n```\n", encoding="utf-8")
    (kb / "raw" / "doc.md").write_text("资料\n", encoding="utf-8")
    runner = make_runner(None)

    rc = run_ingest("raw/doc.md", root=kb, runner=runner)
    assert rc == EXIT_OK
    prompt = runner.calls[0]["prompt"]
    assert "不要整读 `wiki/index.md`" in prompt
    assert "`wiki/sources/` 下另有 0 页未逐页列出" in prompt
    assert "wrapper 未定位到本篇 source 页" in prompt
    assert '〈页面名录〉共 1 页：\n{"dir":"wiki/entities"}\n["Alpha",null,["甲"]]\n〈页面名录完〉' in prompt
    assert "L1 # 综述\nL5 ## 角度一\n〈overview 结构完〉" in prompt
    assert "不是标题" not in prompt  # 围栏里的 # 不算标题


def test_over_budget_catalog_is_reported_not_truncated(kb: Path, monkeypatch, capsys):
    """超预算：stderr 明确报告、整份不注入、退回整读 index——绝不只给一部分或只给页名。"""
    for i in range(20):
        write_page(kb, f"wiki/concepts/c{i}.md")
    monkeypatch.setattr(catalog_mod, "CATALOG_BUDGET_CHARS", 50)

    prompt = build_ingest_prompt(kb, "doc.md")
    err = capsys.readouterr().err
    assert "超出预算 50 字" in err and "不截断" in err
    assert "〈页面名录〉" not in prompt and '"c0"' not in prompt
    assert "读该 `.md` 源与 `wiki/index.md` 建上下文" in prompt
    assert "〈overview 结构〉" in prompt  # overview 结构照给


def test_prompt_locates_existing_source_page_including_verbatim_stub(kb: Path):
    """本篇 source 页已存在（含批量导入用原文件名建的桩页）→ prompt 给出准确路径并要求合并、不覆盖。"""
    _page(kb, "wiki/sources/某“示例”提议 第1期.md", 'title: "桩"\ntype: source')
    for i in range(3):
        _page(kb, f"wiki/sources/s{i}.md", 'title: "s"\ntype: source')

    prompt = build_ingest_prompt(kb, "专题/某“示例”提议 第1期.md")
    assert "本篇 source 页**已存在**：`wiki/sources/某“示例”提议 第1期.md`" in prompt
    assert "不整页覆盖，也不另建一张" in prompt
    assert "`wiki/sources/` 下另有 4 页未逐页列出" in prompt
    assert "s0" not in prompt.split("〈页面名录〉")[1]  # source 页名不进名录


def test_empty_kb_catalog_says_so(kb: Path):
    prompt = build_ingest_prompt(kb, "doc.md")
    assert "〈页面名录〉共 0 页：\n（本库尚无页面）" in prompt


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
def test_outline_line_numbers_match_read_file(tmp_path: Path, eol: str):
    """行号必须与 agentao `read_file` 一致（它按 Python 文本模式通用换行切行）——否则 offset 读错章节。

    `\\u2028` / `\\x85` 在 `str.splitlines` 里算换行、在文本模式与 markdown 里不算：放一个在标题前，
    若实现误用 `splitlines`，其后行号全部错位。
    """
    p = tmp_path / "overview.md"
    text = eol.join(["# 总览", "正文 续\x85续", "", "```", "## 围栏里", "```", "## 角度", "### 细分"]) + eol
    p.write_bytes(text.encode("utf-8"))

    with p.open(encoding="utf-8") as fh:  # newline=None：与 agentao open_text 同口径
        file_lines = [line.rstrip("\n") for line in fh]
    outline = overview_outline(p).splitlines()
    assert outline[0].startswith(f"全文 {len(file_lines)} 行")
    for entry in outline[1:]:
        num, heading = entry.split(" ", 1)
        assert file_lines[int(num[1:]) - 1] == heading
    assert [e.split(" ", 1)[1] for e in outline[1:]] == ["# 总览", "## 角度", "### 细分"]


def test_outline_missing_file(tmp_path: Path):
    assert overview_outline(tmp_path / "nope.md") is None


def test_outline_keeps_commonmark_indented_headings(tmp_path: Path):
    """CommonMark 允许 ATX 标题前至多 3 个空格；4 个空格是缩进代码块，不算。"""
    p = tmp_path / "overview.md"
    p.write_text("# 总览\n   ## 缩进三格\n    ## 四格是代码\n", encoding="utf-8")
    assert overview_outline(p).splitlines()[1:] == ["L1 # 总览", "L2    ## 缩进三格"]


def test_outline_skips_frontmatter_comments_but_keeps_line_numbers(tmp_path: Path):
    """frontmatter 里的 YAML `# 注释` 不算标题；其后标题的行号仍按全文计（与 read_file 一致）。"""
    p = tmp_path / "overview.md"
    p.write_text("---\ntitle: 总览\n# 维护说明\n---\n\n# 总览\n\n## 角度\n", encoding="utf-8")
    assert overview_outline(p).splitlines()[1:] == ["L6 # 总览", "L8 ## 角度"]

    # 未闭合的 `---` 不算 frontmatter（与 split_frontmatter 同判）：其后标题照列。
    p.write_text("---\n# 甲\n", encoding="utf-8")
    assert overview_outline(p).splitlines()[1:] == ["L2 # 甲"]
