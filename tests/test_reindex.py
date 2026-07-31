"""P3.4 reindex 索引回填测试（零 LLM，见 docs/P3.4-索引回填.md §7）。"""

import json
from pathlib import Path

from guanlan.health import run_health
from guanlan.reindex import reindex_entrypoint, run_reindex

FM = (
    "---\ntitle: '{title}'\ntype: {type}\ntags: []\n"
    "{aliases}sources: []\nlast_updated: 2026-06-03\n---\n\n{body}\n"
)

INDEX_TEMPLATE = """# 索引 (Index)

## Overview

- [总览](overview.md) — 跨资料的活体综述

## Sources

<!-- ingest 自动追加 -->

## Entities

<!-- ingest 自动追加 -->

## Concepts

<!-- ingest 自动追加 -->

## Syntheses

<!-- query --backfill 自动追加 -->
"""


def _page(wiki: Path, rel: str, *, title="T", type="entity", aliases=None, body="实质正文内容够长。") -> None:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    alias_line = ""
    if aliases is not None:
        alias_line = "aliases: [" + ", ".join(f"'{a}'" for a in aliases) + "]\n"
    p.write_text(FM.format(title=title, type=type, aliases=alias_line, body=body), encoding="utf-8")


def _kb(tmp_path: Path, index: str = INDEX_TEMPLATE) -> Path:
    """搭一个最小知识库根（wiki/ + config 三件套），返回根目录。"""
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text(index, encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    return tmp_path


def _index(tmp_path: Path) -> str:
    return (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")


def _missing_kinds(wiki: Path) -> list[str]:
    return [f.kind for f in run_health(wiki).findings if f.kind == "health.index_missing_page"]


# ---------- 登记 ----------


def test_registers_missing_page_into_correct_section(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/DeFi.md", title="DeFi", aliases=["defi"])

    result, new_text = run_reindex(wiki)
    assert new_text is not None and len(result.added) == 1

    reindex_entrypoint(root, prune=False, dry_run=False, json_output=False)
    idx = _index(tmp_path)
    # 行落在 Entities 分区、格式含别名注记。
    entities_block = idx.split("## Entities", 1)[1].split("## Concepts", 1)[0]
    assert "- [DeFi](entities/DeFi.md) — （别名：defi）" in entities_block
    # health 该项归零。
    assert _missing_kinds(wiki) == []


def test_no_aliases_no_tail(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/图灵.md", title="图灵")

    reindex_entrypoint(root, prune=False, dry_run=False, json_output=False)
    idx = _index(tmp_path)
    assert "- [图灵](entities/图灵.md)\n" in idx
    assert "（别名" not in idx.split("图灵")[1][:20]


def test_bad_frontmatter_falls_back_to_stem(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    # 缺 frontmatter 的页：page_title 退化用 stem，不抛。
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "Foo.md").write_text("没有 frontmatter 的正文。\n", encoding="utf-8")

    result, _ = run_reindex(wiki)
    assert any(e.line == "- [Foo](concepts/Foo.md)" for e in result.added)


# ---------- 幂等 ----------


def test_idempotent_second_run_no_change(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="A")

    reindex_entrypoint(root, prune=False, dry_run=False, json_output=False)
    first = _index(tmp_path)
    _result2, new_text2 = run_reindex(wiki)
    assert new_text2 is None
    assert _index(tmp_path) == first


def test_title_with_bracket_stays_idempotent(tmp_path: Path):
    # 标题含裸 `]` 不得产出无法被 index_md_links 解析的行（否则 health 仍报 missing、重复登记）。
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/Draft.md", title="[草稿]稿")

    reindex_entrypoint(root, prune=False, dry_run=False, json_output=False)
    assert _missing_kinds(wiki) == []  # 登记被 health 看见 → 幂等
    _result, new_text = run_reindex(wiki)
    assert new_text is None  # 第二次零改动


# ---------- dry-run ----------


def test_dry_run_does_not_write(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="A")
    before = _index(tmp_path)

    reindex_entrypoint(root, prune=False, dry_run=True, json_output=False)
    assert _index(tmp_path) == before
    # 但仍报告有待登记。
    result, _ = run_reindex(wiki)
    assert len(result.added) == 1


# ---------- prune ----------


def test_prune_removes_dangling_keeps_valid(tmp_path: Path):
    index = INDEX_TEMPLATE.replace(
        "## Entities\n\n<!-- ingest 自动追加 -->",
        "## Entities\n\n- [活页](entities/Live.md) — ok\n- [死页](entities/Dead.md) — 悬空",
    )
    root = _kb(tmp_path, index=index)
    wiki = root / "wiki"
    _page(wiki, "entities/Live.md", title="活页")  # Dead.md 不存在 → 悬空。

    reindex_entrypoint(root, prune=True, dry_run=False, json_output=False)
    idx = _index(tmp_path)
    assert "entities/Dead.md" not in idx
    assert "- [活页](entities/Live.md) — ok" in idx


def test_default_does_not_prune(tmp_path: Path):
    index = INDEX_TEMPLATE.replace(
        "## Entities\n\n<!-- ingest 自动追加 -->",
        "## Entities\n\n- [死页](entities/Dead.md) — 悬空",
    )
    root = _kb(tmp_path, index=index)
    reindex_entrypoint(root, prune=False, dry_run=False, json_output=False)
    assert "entities/Dead.md" in _index(tmp_path)


# ---------- 缺分区标题 ----------


def test_missing_section_heading_is_created(tmp_path: Path):
    index = "# 索引\n\n## Overview\n\n- [总览](overview.md) — x\n"  # 无 Entities 分区。
    root = _kb(tmp_path, index=index)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="A")

    reindex_entrypoint(root, prune=False, dry_run=False, json_output=False)
    idx = _index(tmp_path)
    assert "## Entities" in idx
    assert "- [A](entities/A.md)" in idx
    assert _missing_kinds(wiki) == []


# ---------- JSON 契约 ----------


def test_json_contract(tmp_path: Path, capsys):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/DeFi.md", title="DeFi", aliases=["defi"])

    reindex_entrypoint(root, prune=False, dry_run=False, json_output=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["pages_checked"] == 1
    assert payload["pruned"] == []
    assert payload["added"][0] == {
        "page": "wiki/entities/DeFi.md",
        "section": "Entities",
        "line": "- [DeFi](entities/DeFi.md) — （别名：defi）",
    }


# ---------- prune 与 HTML 注释 ----------


def test_prune_keeps_template_hint_comments(tmp_path: Path):
    """`--prune` 不得删掉模板里带占位链接的提示注释——那是给 Agent 看的追加格式说明。

    init 模板的四行提示形如 `<!-- ingest 自动追加：- [<名称>](entities/<Name>.md) — … -->`。
    这些占位路径当然"没有对应文件"，一度被整行当悬空剪掉。
    """
    hint = "<!-- ingest 自动追加：- [<名称>](entities/<Name>.md) — <一句话> -->"
    index = INDEX_TEMPLATE.replace(
        "## Entities\n\n<!-- ingest 自动追加 -->",
        f"## Entities\n\n{hint}\n- [死页](entities/Dead.md) — 悬空",
    )
    root = _kb(tmp_path, index=index)

    reindex_entrypoint(root, prune=True, dry_run=False, json_output=False)
    idx = _index(tmp_path)
    assert hint in idx  # 注释行留住
    assert "entities/Dead.md" not in idx  # 真悬空行照删


def test_prune_keeps_multiline_commented_block(tmp_path: Path):
    """跨行注释里的登记行同样不删——单行注释在 index_md_links 里就抹了，跨行只有按全文对齐才认得出。"""
    block = "<!--\n- [注掉的页](entities/Commented.md) — 暂时收起\n-->"
    index = INDEX_TEMPLATE.replace(
        "## Entities\n\n<!-- ingest 自动追加 -->", f"## Entities\n\n{block}"
    )
    root = _kb(tmp_path, index=index)

    reindex_entrypoint(root, prune=True, dry_run=False, json_output=False)
    assert "- [注掉的页](entities/Commented.md) — 暂时收起" in _index(tmp_path)


def test_prune_on_cr_index_normalized_by_universal_newlines(tmp_path: Path):
    """老式裸 `\\r` 断行的 index：照常剪枝、不抛。

    顺带钉住**边界的实际位置**：`Path.read_text` 的通用换行在读入时就把裸 `\\r` 归一成 `\\n`，
    故 `_prune_dangling` 这条路径根本见不到 `\\r`。`strip_html_comments` 自身对裸 `\\r` 也保行数
    （见 `tests/test_pages.py`），但那是纵深防御，不是这条路径的最后防线——别把两者混为一谈。
    """
    index = (
        "# 索引\r\r## Entities\r\r"
        "<!--\r- [注掉的页](entities/Commented.md)\r-->\r"
        "- [死页](entities/Dead.md) — 悬空\r"
    )
    root = _kb(tmp_path, index=index)

    reindex_entrypoint(root, prune=True, dry_run=False, json_output=False)  # 不得抛
    idx = _index(tmp_path)
    assert "entities/Commented.md" in idx  # 注释块留住
    assert "entities/Dead.md" not in idx  # 真悬空行照删


def test_prune_does_not_break_open_a_multiline_comment_block(tmp_path: Path):
    """`--prune` 不得删掉**带注释标记**的行——删了会把注释块拆开、块内内容「转正」。

    评审实测的两步静默丢失（用行尾开块的写法，该写法不被"独占整行"规则识别）：第一轮删掉带
    `<!--` 的首行 → 块内 `- [藏页](…)` 变成生效登记行；第二轮它已是真悬空，被一并删除。
    """
    index = INDEX_TEMPLATE.replace(
        "## Entities\n\n<!-- ingest 自动追加 -->",
        "## Entities\n\n- [死页](entities/Dead.md) — 悬空 <!--\n"
        "- [藏页](entities/Hidden.md) — 暂时收起\n-->",
    )
    root = _kb(tmp_path, index=index)

    for _ in range(2):  # 跑两轮：第二轮不得把上一轮的残留当悬空
        reindex_entrypoint(root, prune=True, dry_run=False, json_output=False)
    idx = _index(tmp_path)
    assert "- [死页](entities/Dead.md) — 悬空 <!--" in idx  # 带标记的行不删（保守闸）
    assert "-->" in idx
    assert "- [藏页](entities/Hidden.md) — 暂时收起" in idx  # 块内内容没被「转正」后删掉


def test_prune_keeps_whole_line_comment_block_verbatim(tmp_path: Path):
    """独占整行的注释块：整块逐字留住，真悬空行照删。"""
    block = "<!--\n- [藏页](entities/Hidden.md) — 暂时收起\n-->"
    index = INDEX_TEMPLATE.replace(
        "## Entities\n\n<!-- ingest 自动追加 -->",
        f"## Entities\n\n- [死页](entities/Dead.md) — 悬空\n{block}",
    )
    root = _kb(tmp_path, index=index)

    for _ in range(2):
        reindex_entrypoint(root, prune=True, dry_run=False, json_output=False)
    idx = _index(tmp_path)
    assert block in idx
    assert "- [死页](entities/Dead.md) — 悬空" not in idx
