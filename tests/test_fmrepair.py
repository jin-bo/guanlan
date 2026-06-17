"""确定性 frontmatter 引号修复测试（零 LLM，见 guanlan/fmrepair.py）。

核心安全契约：修完整块必须严格档解析为 mapping，否则不写盘（最差 = 现状）；只碰引号写坏、
不 mangle 结构性坏值；正文与 `---` 分隔行逐字节不变。
"""

from pathlib import Path

from guanlan.check import Violation
from guanlan.fmrepair import (
    _requote_block,
    _value_parses,
    repair_page_frontmatter,
    repair_unparsable_pages,
)
from guanlan.pages import parse_frontmatter, split_frontmatter

# 完整页面模板：{fm} 是 frontmatter 块内文（不含 --- 分隔行），{body} 是正文。
PAGE = "---\n{fm}---\n\n{body}\n"
# 除 title 外的必备键（全清验收要求页面 frontmatter 完全合规才写盘，故成功修复用例须备齐）。
TAIL = "type: concept\ntags: []\nsources: []\nlast_updated: 2026-06-03\n"


def _write(tmp_path: Path, rel: str, fm: str, body: str = "正文") -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(PAGE.format(fm=fm, body=body), encoding="utf-8")
    return p


# --- _value_parses ---


def test_value_parses_distinguishes_legal_from_broken():
    assert _value_parses("T") and _value_parses("[]") and _value_parses("[a, b]")
    assert _value_parses("2026-06-03")
    assert not _value_parses('"他说"你好""')  # 双引号套双引号
    assert not _value_parses("'it's broken'")  # 坏单引号


# --- _requote_block：作用面与安全边界 ---


def test_requote_double_quote_nesting():
    """双引号套双引号 → 单引号重包，恢复出原意字符串。"""
    block = 'title: "他说"你好""\ntype: concept\n'
    new = _requote_block(block)
    meta, fatal = parse_frontmatter(new)
    assert fatal is None
    assert meta["title"] == '他说"你好"'
    assert meta["type"] == "concept"  # 合法行不动


def test_requote_broken_single_quote():
    new = _requote_block("name: 'it's broken'\n")
    meta, fatal = parse_frontmatter(new)
    assert fatal is None and meta["name"] == "it's broken"


def test_requote_legal_block_returns_none():
    """全合法块 → None（一字不改）。"""
    assert _requote_block("title: T\ntype: concept\ntags: []\nsources: []\n") is None


def test_requote_leaves_structural_breakage_to_selfheal():
    """结构性坏值（不以引号开头）一律不动——避免把列表/含冒号串 mangle 成字符串。"""
    assert _requote_block("title: T\ntags: [a, b\n") is None  # 未闭合行内列表
    assert _requote_block("title: C: The Language\n") is None  # 未加引号含冒号（值单独可解析为 dict）


def test_requote_requires_matched_quote_pair():
    """首尾不成对的引号坏值（未闭合 / 带尾注）一律不动——避免吞尾注或退化恢复。"""
    assert _requote_block('title: "a\n') is None  # 未闭合
    assert _requote_block('title: "a"b" # 注释\n') is None  # 首 " 尾非 " → 不成对，会把 ` # 注释` 吞进值
    assert _requote_block("title: 'a\n") is None  # 单引号未闭合


def test_requote_ignores_indented_and_list_lines():
    """缩进续行 / 列表项 / 注释行不被误改，只动顶层标量值。"""
    block = 'title: "a"b"\n# 注释\nmeta:\n  - x\n  - y\n'
    new = _requote_block(block)
    assert new is not None
    assert "title: 'a\"b'" in new
    assert "# 注释\n" in new and "  - x\n" in new and "  - y\n" in new  # 原样


def test_requote_multiple_bad_fields():
    block = 'title: "a"b"\norigin: "x"y"\ntype: concept\n'
    meta, fatal = parse_frontmatter(_requote_block(block))
    assert fatal is None
    assert meta["title"] == 'a"b' and meta["origin"] == 'x"y'


def test_requote_preserves_crlf_line_endings():
    """保留每行原始行尾（含 CRLF）。"""
    new = _requote_block('title: "a"b"\r\ntype: concept\r\n')
    assert new is not None and new.endswith("\r\n")
    assert "title: 'a\"b'\r\n" in new


# --- repair_page_frontmatter：requote + 落盘，返回原字节 | None（全清裁定归门禁） ---
# 注：本函数**不判定该页是否全清**——它只确保「重写后能解析为 mapping」即落盘并返回原字节；
# 「修完是否真省下自愈轮（类型/跨页 alias/源不回退）」由门禁重判 + 回滚裁定，见 tests/test_gate.py。


def test_repair_page_fixes_quote_and_preserves_body(tmp_path: Path):
    body = "## 标题\n\n[[Foo]] 正文行\n含 --- 分隔样式与 `code`\n"
    p = _write(tmp_path, "wiki/concepts/Foo.md", 'title: "他说"你好""\n' + TAIL, body)
    _block_before, body_before = split_frontmatter(p.read_text(encoding="utf-8"))
    assert repair_page_frontmatter(p, tmp_path / "wiki") is not None  # 写了（返回原字节供门禁回滚）
    block_after, body_after = split_frontmatter(p.read_text(encoding="utf-8"))
    parsed, fatal = parse_frontmatter(block_after)
    assert fatal is None and parsed["title"] == '他说"你好"'
    assert body_after == body_before  # 正文逐字节不变（含 `---` 分隔样式行）


def test_repair_page_returns_original_bytes_for_rollback(tmp_path: Path):
    """落盘成功 → 返回**写前原字节**（供门禁回滚），且已确实改盘；该页全清与否由门禁裁定。"""
    p = _write(tmp_path, "wiki/concepts/Foo.md", 'title: "a"b"\n' + TAIL)
    before = p.read_bytes()
    original = repair_page_frontmatter(p, tmp_path / "wiki")
    assert original == before  # 返回的是写前原字节
    assert p.read_bytes() != before  # 已落盘修复


def test_repair_page_writes_any_parse_valid_requote(tmp_path: Path):
    """只要「重写后能解析为 mapping」即落盘——纵使 tags 被重包成字符串（bad_type）也写、留门禁回滚。"""
    p = _write(tmp_path, "wiki/concepts/T.md", 'title: T\ntags: "a"b"\ntype: concept\nsources: []\nlast_updated: 2026-06-03\n')
    before = p.read_bytes()
    assert repair_page_frontmatter(p, tmp_path / "wiki") == before
    meta, fatal = parse_frontmatter(split_frontmatter(p.read_text(encoding="utf-8"))[0])
    assert fatal is None and meta["tags"] == 'a"b'  # 已成合法 mapping（tags 是字符串，全清判给门禁）


def test_repair_page_rejects_symlink_page(tmp_path: Path):
    """符号链接页一律不修——否则 write_bytes 跟随符号链接改动 KB 之外的文件（回归 Codex P2）。"""
    outside = tmp_path / "outside.md"
    outside.write_text(PAGE.format(fm='title: "他说"你好""\n' + TAIL, body="库外内容"), encoding="utf-8")
    before = outside.read_bytes()
    link = tmp_path / "wiki" / "concepts" / "link.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)  # wiki 页是指向库外文件的符号链接
    assert repair_page_frontmatter(link, tmp_path / "wiki") is None
    assert outside.read_bytes() == before  # 库外文件一字未动


def test_repair_page_rejects_symlinked_parent_escape(tmp_path: Path):
    """页本身非符号链接、但父目录是指向库外的符号链接 → 解析越界、不修（覆盖 resolve 闸）。"""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    target = outside_dir / "Foo.md"
    target.write_text(PAGE.format(fm='title: "a"b"\n' + TAIL, body="x"), encoding="utf-8")
    before = target.read_bytes()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "concepts").symlink_to(outside_dir, target_is_directory=True)
    page = wiki / "concepts" / "Foo.md"  # Foo.md 自身非符号链接，但经符号链接父目录解析到库外
    assert page.is_symlink() is False
    assert repair_page_frontmatter(page, wiki) is None
    assert target.read_bytes() == before


def test_repair_page_preserves_crlf_bytes(tmp_path: Path):
    """CRLF 页：逐字节 I/O 不把 \\r\\n 翻成 \\n——正文/分隔行字节不变，只 title 行被修。"""
    p = tmp_path / "wiki" / "concepts" / "C.md"
    p.parent.mkdir(parents=True)
    raw = (
        '---\r\ntitle: "a"b"\r\ntype: concept\r\ntags: []\r\nsources: []\r\n'
        "last_updated: 2026-06-03\r\n---\r\n\r\nbody 行\r\n"
    )
    p.write_bytes(raw.encode("utf-8"))
    assert repair_page_frontmatter(p, tmp_path / "wiki") is not None
    out = p.read_bytes()
    assert b"title: 'a\"b'\r\n" in out  # title 修好、仍 CRLF
    assert b"\r\nbody \xe8\xa1\x8c\r\n" in out  # 正文 CRLF 逐字节保留
    assert b"\n\n" not in out.replace(b"\r\n", b"")  # 无被翻译出的裸 LF


def test_repair_page_skips_when_legal(tmp_path: Path):
    """已合法页 → 不写盘、文件字节不变、返回 None。"""
    p = _write(tmp_path, "wiki/concepts/Ok.md", "title: T\ntype: concept\ntags: []\nsources: []\n")
    before = p.read_bytes()
    assert repair_page_frontmatter(p, tmp_path / "wiki") is None
    assert p.read_bytes() == before


def test_repair_page_bails_on_unfixable(tmp_path: Path):
    """不可修复（结构性坏值）→ 重写后仍解析失败 → 不写盘、字节不变、返回 None。"""
    p = _write(tmp_path, "wiki/concepts/Bad.md", "title: T\ntags: [a, b\n")
    before = p.read_bytes()
    assert repair_page_frontmatter(p, tmp_path / "wiki") is None
    assert p.read_bytes() == before


def test_repair_page_skips_non_unparsable(tmp_path: Path):
    """缺键/坏类型等非引号问题不在此修（fatal.kind != frontmatter.unparsable 或无 fatal）。"""
    # 坏类型但可解析（type=bogus）：parse 无 fatal → 不碰。
    p = _write(tmp_path, "wiki/concepts/T.md", "title: T\ntype: bogus\ntags: []\nsources: []\n")
    before = p.read_bytes()
    assert repair_page_frontmatter(p, tmp_path / "wiki") is None
    assert p.read_bytes() == before


def test_repair_page_no_frontmatter(tmp_path: Path):
    """无 frontmatter 块 → None。"""
    p = tmp_path / "wiki" / "x.md"
    p.parent.mkdir(parents=True)
    p.write_text("没有 frontmatter 的正文\n", encoding="utf-8")
    assert repair_page_frontmatter(p, tmp_path / "wiki") is None


# --- repair_unparsable_pages：作用面取自违规集，返回 {页: 原字节} ---


def test_repair_unparsable_pages_filters_by_kind(tmp_path: Path):
    """只修 frontmatter.unparsable 违规列出的页；返回 {相对库根 posix: 写前原字节}。"""
    a_before = _write(tmp_path, "wiki/concepts/A.md", 'title: "x"y""\n' + TAIL).read_bytes()
    _write(tmp_path, "wiki/concepts/B.md", "title: T\n" + TAIL)
    violations = [
        Violation("wiki/concepts/A.md", "frontmatter.unparsable", "…"),
        Violation("wiki/concepts/B.md", "wikilink.broken", "…"),  # 非本类，不碰
    ]
    written = repair_unparsable_pages(tmp_path, violations)
    assert set(written) == {"wiki/concepts/A.md"}
    assert written["wiki/concepts/A.md"] == a_before  # 值是写前原字节（供门禁回滚）
    # A 现已可解析
    assert parse_frontmatter(split_frontmatter((tmp_path / "wiki/concepts/A.md").read_text())[0])[1] is None


def test_repair_unparsable_pages_empty_when_no_unparsable(tmp_path: Path):
    assert repair_unparsable_pages(tmp_path, [Violation("p", "sources.dropped", "…")]) == {}


def test_repair_unparsable_pages_handles_non_utf8(tmp_path: Path):
    """非 UTF-8 页解码失败被吞掉、不连累其余页、不抛（UnicodeDecodeError 非 OSError）。"""
    p = tmp_path / "wiki" / "concepts" / "Bad.md"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"---\ntitle: \xff\xfe \n---\nbody\n")  # 非法 UTF-8 字节
    written = repair_unparsable_pages(
        tmp_path, [Violation("wiki/concepts/Bad.md", "frontmatter.unparsable", "…")]
    )
    assert written == {}  # 解码失败 → 跳过、不崩
