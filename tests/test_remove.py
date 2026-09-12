"""P3.9 源撤回 remove 测试（零 LLM，见 docs/P3.9-源撤回.md §9）。"""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import guanlan.remove as rmmod
from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.lint import run_lint
from guanlan.pages import iter_pages, split_frontmatter
from guanlan.remove import _resolve_slug, remove_entrypoint, run_remove_result

FM = "---\ntitle: '{title}'\ntype: {type}\ntags: []\nsources: {sources}\nlast_updated: 2026-06-03\n---\n\n{body}\n"


def _kb(tmp_path: Path) -> Path:
    """最小知识库根（满足 require_kb_root(writable=True)）。"""
    (tmp_path / "AGENTAO.md").write_text("# AGENTAO\n", encoding="utf-8")
    (tmp_path / "SCHEMA.md").write_text("# SCHEMA\n", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述\n", encoding="utf-8")
    return tmp_path


def _page(root: Path, rel: str, *, title="T", type="concept", sources="[]", body="正文内容。") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FM.format(title=title, type=type, sources=sources, body=body), encoding="utf-8")
    return p


def _raw(root: Path, slug: str, body="原始素材正文。") -> Path:
    p = root / "raw" / f"{slug}.md"
    p.write_text(body, encoding="utf-8")
    return p


def _source_page(root: Path, slug: str, **kw) -> Path:
    return _page(root, f"wiki/sources/{slug}.md", type="source", **kw)


def _body_of(path: Path) -> str:
    _block, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return body


def _sources_of(path: Path) -> list:
    block, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    return yaml.safe_load(block).get("sources")


def _trash_entries(root: Path) -> list[Path]:
    trash = root / ".trash"
    return sorted(trash.iterdir()) if trash.is_dir() else []


def _manifest(root: Path) -> dict:
    [entry] = _trash_entries(root)
    return json.loads((entry / "manifest.json").read_text(encoding="utf-8"))


# ---------- slug 归一 ----------


def test_resolve_slug_three_forms_normalize_same():
    assert _resolve_slug("foo") == "foo"
    assert _resolve_slug("raw/foo.md") == "foo"
    assert _resolve_slug("wiki/sources/foo.md") == "foo"
    assert _resolve_slug("raw/v1.2.md") == "v1.2"  # 只剥 .md，内部点保留


@pytest.mark.parametrize("bad", ["", "   ", ".", ".."])
def test_resolve_slug_rejects_bad(bad):
    with pytest.raises(ValueError):
        _resolve_slug(bad)


def test_resolve_slug_strips_traversal():
    # 取 basename 剥目录成分：`../../etc/passwd` → `passwd`，落点永远库内、不越界
    assert _resolve_slug("../../etc/passwd.md") == "passwd"


# ---------- 定位 ----------


def test_missing_source_exits_usage_zero_write(tmp_path, capsys):
    root = _kb(tmp_path)
    rc = remove_entrypoint(root, src="ghost", yes=True, json_output=False)
    assert rc == EXIT_USAGE
    assert not (root / ".trash").exists()  # 全缺 → 零写盘


def test_locate_only_raw_no_summary(tmp_path):
    root = _kb(tmp_path)
    _raw(root, "foo")
    plan = run_remove_result(root, "foo")
    assert plan.ok and plan.relocate == ["raw/foo.md"]
    assert plan.drop_slug == [] and plan.orphans == []


# ---------- 预览默认零写 ----------


def test_preview_default_zero_write(tmp_path, capsys):
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    _page(root, "wiki/concepts/x.md", sources="['foo', 'bar']")
    before = {p: p.read_bytes() for p in root.rglob("*.md")}

    rc = remove_entrypoint(root, src="foo", yes=False, json_output=False)

    assert rc == EXIT_OK
    assert not (root / ".trash").exists()
    assert {p: p.read_bytes() for p in root.rglob("*.md")} == before  # 一字未改
    assert "将撤回" in capsys.readouterr().out


# ---------- 执行：移源 + manifest + 摘 slug + 修 index ----------


def test_execute_relocates_and_drops_slug_and_prunes_index(tmp_path):
    root = _kb(tmp_path)
    raw = _raw(root, "foo", body="源 foo 正文")
    raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    _source_page(root, "foo")
    multi = _page(root, "wiki/concepts/x.md", sources="['foo', 'bar']", body="X 的正文不该被改。")
    multi_body = _body_of(multi)
    (root / "wiki" / "index.md").write_text(
        "# 索引\n\n## Sources\n\n- [Foo](sources/foo.md) — 摘要\n- [Bar](sources/bar.md) — 摘要\n",
        encoding="utf-8",
    )

    rc = remove_entrypoint(root, src="foo", yes=True, json_output=False)
    assert rc == EXIT_OK

    # 源自身落盘物移走、原位空
    assert not raw.exists()
    assert not (root / "wiki" / "sources" / "foo.md").exists()
    [entry] = _trash_entries(root)
    moved_raw = entry / "raw" / "foo.md"
    assert moved_raw.exists()
    assert hashlib.sha256(moved_raw.read_bytes()).hexdigest() == raw_sha  # 逐字保真
    assert (entry / "wiki" / "sources" / "foo.md").exists()

    # 多源页只摘 slug、正文逐字不变、页保留
    assert multi.exists()
    assert _sources_of(multi) == ["bar"]
    assert _body_of(multi) == multi_body

    # index：精确删 foo 行、保 bar 行
    idx = (root / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "sources/foo.md" not in idx
    assert "sources/bar.md" in idx

    # manifest：含 before_sources（决策P3.9-2/发现2）
    m = _manifest(root)
    assert m["slug"] == "foo"
    assert m["moved"] == ["raw/foo.md", "wiki/sources/foo.md"]
    [drop] = m["slug_dropped_from"]
    assert drop == {"page": "wiki/concepts/x.md", "dropped_slug": "foo", "before_sources": ["bar", "foo"]}
    assert any("sources/foo.md" in ln for ln in m["index_lines_removed"])


def test_orphan_page_advisory_not_deleted(tmp_path):
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    orphan = _page(root, "wiki/concepts/only.md", sources="['foo']")  # 独源
    multi = _page(root, "wiki/concepts/multi.md", sources="['foo', 'bar']")

    plan = run_remove_result(root, "foo")
    assert plan.orphans == ["wiki/concepts/only.md"]
    assert [d.page for d in plan.drop_slug] == ["wiki/concepts/multi.md"]

    rc = remove_entrypoint(root, src="foo", yes=True, json_output=False)
    assert rc == EXIT_OK
    assert orphan.exists()  # 一期不删独源页
    assert _sources_of(orphan) == ["foo"]  # 独源页 sources 也不动
    assert _sources_of(multi) == ["bar"]
    assert _manifest(root)["orphaned"] == ["wiki/concepts/only.md"]  # 入 blast-radius 审计


def test_summary_page_itself_not_slug_edited(tmp_path):
    # 摘要页随源整体移走，不被当作"含 foo 的衍生页"做 slug 编辑
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo", sources="['foo']")  # 自指
    plan = run_remove_result(root, "foo")
    assert plan.drop_slug == [] and plan.orphans == []  # 摘要页被跳过


# ---------- 红线 ----------


def test_no_log_md_write_and_trash_not_scanned(tmp_path):
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    log_before = (root / "wiki" / "log.md").read_bytes()

    remove_entrypoint(root, src="foo", yes=True, json_output=False)

    assert (root / "wiki" / "log.md").read_bytes() == log_before  # 不写 log.md
    # .trash/ 下的摘要页不被 iter_pages 看见（在 wiki/ 之外）
    scanned = list(iter_pages(root / "wiki"))
    assert all(".trash" not in p.parts for p in scanned)
    assert root / "wiki" / "sources" / "foo.md" not in scanned


# ---------- 幂等 / 重跑收敛 ----------


def test_idempotent_when_raw_already_gone(tmp_path):
    # 模拟 partial：raw 已被移走（源位空），但摘要页 + 多源页引用仍在 → 重跑收敛
    root = _kb(tmp_path)
    _source_page(root, "foo")  # raw 缺，摘要页在
    multi = _page(root, "wiki/concepts/x.md", sources="['foo', 'bar']")

    rc = remove_entrypoint(root, src="foo", yes=True, json_output=False)
    assert rc == EXIT_OK
    assert not (root / "wiki" / "sources" / "foo.md").exists()  # 摘要页移走
    assert _sources_of(multi) == ["bar"]  # slug 仍被摘


def test_rerun_after_complete_reports_not_found(tmp_path):
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    assert remove_entrypoint(root, src="foo", yes=True, json_output=False) == EXIT_OK
    # 再撤同 slug：源已不在 → EXIT_USAGE（已撤回），且不产生第二个 trash entry
    assert remove_entrypoint(root, src="foo", yes=True, json_output=False) == EXIT_USAGE
    assert len(_trash_entries(root)) == 1


# ---------- 图片随源 ----------


def test_images_relocated_with_source(tmp_path):
    root = _kb(tmp_path)
    _raw(root, "foo")
    img_dir = root / "raw" / "images" / "foo"
    img_dir.mkdir(parents=True)
    (img_dir / "foo-1.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")
    img_sha = hashlib.sha256((img_dir / "foo-1.png").read_bytes()).hexdigest()

    rc = remove_entrypoint(root, src="foo", yes=True, json_output=False)
    assert rc == EXIT_OK
    assert not img_dir.exists()
    [entry] = _trash_entries(root)
    moved = entry / "raw" / "images" / "foo" / "foo-1.png"
    assert moved.exists()
    assert hashlib.sha256(moved.read_bytes()).hexdigest() == img_sha


# ---------- JSON 契约 ----------


def test_json_contract(tmp_path, capsys):
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    _page(root, "wiki/concepts/x.md", sources="['foo', 'bar']")

    rc = remove_entrypoint(root, src="foo", yes=False, json_output=True)
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["slug"] == "foo"
    assert payload["executed"] is False
    assert payload["relocate"] == ["raw/foo.md", "wiki/sources/foo.md"]
    assert payload["drop_slug"] == [{"page": "wiki/concepts/x.md", "before_sources": ["bar", "foo"]}]


def test_json_source_not_found(tmp_path, capsys):
    root = _kb(tmp_path)
    rc = remove_entrypoint(root, src="ghost", yes=False, json_output=True)
    assert rc == EXIT_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": False, "slug": "ghost", "error": "source_not_found"}


# ---------- 撤回后断链交既有 lint（不 auto-strip，§9） ----------


def test_dangling_after_remove_reported_by_lint_not_stripped(tmp_path):
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    # 一个**引用**摘要页的页（非衍生：sources 不含 foo），正文有 [[foo]]
    referer = _page(root, "wiki/concepts/refers.md", sources="[]", body="见 [[foo]] 的论述。")
    body_before = _body_of(referer)

    assert remove_entrypoint(root, src="foo", yes=True, json_output=False) == EXIT_OK

    # remove 只把入链页列进 `backlinks` advisory、绝不 auto-strip：引用页正文一字不改
    assert referer.exists()
    assert _body_of(referer) == body_before
    # 撤回后断链由既有 lint 如实报出（口径不漂移）
    broken = [f for f in run_lint(root / "wiki").findings if f.kind == "lint.broken_link"]
    assert any("foo" in f.detail for f in broken)


# ---------- provenance 编辑保真（CJK / 冒号 / 引号 / 多键，§9） ----------


def test_provenance_preserves_other_frontmatter_keys(tmp_path):
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    p = root / "wiki" / "concepts" / "x.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\n"
        "title: 'Attention: 含冒号与\"引号\"'\n"
        "type: concept\n"
        "tags: [甲, 乙]\n"
        "aliases: ['别名一', '别名二']\n"
        "sources: ['foo', 'bar', 'baz']\n"
        "last_updated: 2026-06-03\n"
        "---\n\n中文正文，含 [[链接]] 与冒号：不该被破坏。\n",
        encoding="utf-8",
    )
    body_before = _body_of(p)

    assert remove_entrypoint(root, src="foo", yes=True, json_output=False) == EXIT_OK

    block, body = split_frontmatter(p.read_text(encoding="utf-8"))
    meta = yaml.safe_load(block)
    assert meta["sources"] == ["bar", "baz"]  # 仅摘 foo、原序保留
    assert meta["title"] == 'Attention: 含冒号与"引号"'  # 含冒号/引号的键不被破坏
    assert meta["tags"] == ["甲", "乙"]
    assert meta["aliases"] == ["别名一", "别名二"]
    assert body == body_before  # 正文逐字不变


# ---------- partial 中断 → 重跑向前收敛（§9，决策P3.9-10） ----------


def test_partial_interrupt_then_rerun_converges(tmp_path, monkeypatch):
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    multi = _page(root, "wiki/concepts/x.md", sources="['foo', 'bar']")

    # 模拟"摘 slug + 删 index + 移 raw 已做，移摘要页时 IO 中断"：第一次移摘要页抛 OSError
    orig_move = rmmod._move_into_trash
    state = {"failed": False}

    def flaky_move(src, root_, trash_dir):
        if src.name == "foo.md" and "sources" in src.parts and not state["failed"]:
            state["failed"] = True
            raise OSError("模拟中断")
        return orig_move(src, root_, trash_dir)

    monkeypatch.setattr(rmmod, "_move_into_trash", flaky_move)
    rc1 = remove_entrypoint(root, src="foo", yes=True, json_output=False)
    assert rc1 == EXIT_USAGE  # 报 partial
    assert _sources_of(multi) == ["bar"]  # slug 已摘（移源前完成）
    assert not (root / "raw" / "foo.md").exists()  # raw 已移
    assert (root / "wiki" / "sources" / "foo.md").exists()  # 摘要页因中断仍在

    # 重跑同命令（移源锚仍在）→ 向前收敛
    monkeypatch.setattr(rmmod, "_move_into_trash", orig_move)
    rc2 = remove_entrypoint(root, src="foo", yes=True, json_output=False)
    assert rc2 == EXIT_OK
    assert not (root / "wiki" / "sources" / "foo.md").exists()  # 摘要页终被移走
    assert _sources_of(multi) == ["bar"]  # 幂等：slug 摘除不重复/不回退


def test_index_comment_mentioning_slug_is_not_pruned(tmp_path):
    """撤回只删该源的**登记行**；注释里提到同一路径的行不动（注释不是收录项）。"""
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    comment = "<!-- 历史：[Foo](sources/foo.md) 曾收录于此 -->"
    (root / "wiki" / "index.md").write_text(
        f"# 索引\n\n## Sources\n\n- [Foo](sources/foo.md) — 摘要\n{comment}\n",
        encoding="utf-8",
    )

    assert remove_entrypoint(root, src="foo", yes=True, json_output=False) == EXIT_OK
    idx = (root / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "- [Foo](sources/foo.md) — 摘要" not in idx  # 真登记行删掉
    assert comment in idx  # 注释行留住


def test_crlf_index_and_page_keep_crlf_through_remove(tmp_path):
    """**行尾保真**：CRLF 的 index.md 与 CRLF 的多源衍生页，撤回后仍是 CRLF。

    `remove` 会重写两类文件——index.md（删登记行）与多源页（摘 `sources` 里的 slug）。旧实现两处
    都是「`Path.read_text` 归一读 + `atomic_write_text` 逐字写」，净效果是每撤回一个源就把这两类
    文件整份的行尾静默改掉。
    """
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    page = _page(root, "wiki/concepts/多源.md", sources="[foo, bar]")
    page.write_bytes(page.read_bytes().replace(b"\n", b"\r\n"))
    index = "# 索引\n\n## Sources\n\n- [Foo](sources/foo.md) — 摘要\n- [别的](sources/bar.md) — 摘要\n"
    (root / "wiki" / "index.md").write_bytes(index.replace("\n", "\r\n").encode("utf-8"))

    assert remove_entrypoint(root, src="foo", yes=True, json_output=False) == EXIT_OK

    idx = (root / "wiki" / "index.md").read_bytes()
    assert idx.count(b"\n") == idx.count(b"\r\n")  # 无落单 LF
    assert b"sources/foo.md" not in idx and b"sources/bar.md" in idx  # 该删的删了、别的没动
    body = page.read_bytes()
    assert body.count(b"\n") == body.count(b"\r\n")  # 衍生页整份仍 CRLF（含重出的 frontmatter 块）
    assert _sources_of(page) == ["bar"]  # slug 已摘掉，值仍可解析
    assert "正文内容。\r\n".encode("utf-8") in body  # 正文逐字未动


# ---------- 入链预览（撤回后会悬链的页，advisory、不改盘） ----------


def test_preview_lists_pages_linking_to_the_source_page(tmp_path, capsys):
    """预览列出「谁 `[[链向]]` 这张摘要页」——它们**不在** drop_slug/orphans 里，此前完全不可见。

    链者的 `sources` 不含 foo（故不摘 slug、也不是独源孤儿），只是正文里引了 `[[foo]]`：
    撤回后这条链就悬空。这正是决定撤不撤所需、而旧预览只肯转嫁给事后 `lint` 的信息。
    """
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    linker = _page(root, "wiki/concepts/引用者.md", sources="['bar']", body="见 [[foo]] 的结论。")
    before = linker.read_bytes()

    plan = run_remove_result(root, "foo")
    assert plan.backlinks == ["wiki/concepts/引用者.md"]
    assert plan.drop_slug == [] and plan.orphans == []  # 既不摘 slug、也不是孤儿

    assert remove_entrypoint(root, src="foo", yes=False, json_output=False) == EXIT_OK
    out = capsys.readouterr().out
    assert "入链页" in out and "wiki/concepts/引用者.md" in out
    assert linker.read_bytes() == before  # advisory：入链页一字不改


def test_backlinks_go_through_the_resolution_table_not_a_string_match(tmp_path):
    """入链认定复用 `build_graph` 的解析表：大小写变体算数、自环不算。

    `[[FOO]]` 经 `link_stem` 归一后命中 `sources/foo.md`——若这里改成字面匹配 slug，就会漏掉
    它（也会与 check/lint/heal 的口径分叉，正是 P3.8 要消的那类漂移）。
    """
    root = _kb(tmp_path)
    _raw(root, "foo")
    # 摘要页自己引自己：自环不该算入链（同 graph._inlink_counts 排自环的口径）。
    _source_page(root, "foo", body="本页见 [[foo]]。")
    _page(root, "wiki/entities/Bar.md", sources="['bar']", body="参见 [[FOO]]。")

    assert run_remove_result(root, "foo").backlinks == ["wiki/entities/Bar.md"]


def test_backlinks_ignore_wikilinks_inside_code_blocks(tmp_path):
    """代码块里的 `[[foo]]` 不算入链（继承 #59 的 `link_scan_text` 口径，不新造第二套扫描）。"""
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    _page(
        root,
        "wiki/concepts/示例.md",
        sources="['bar']",
        body="写法示例：\n\n```markdown\n[[foo]]\n```\n",
    )

    assert run_remove_result(root, "foo").backlinks == []


def test_backlinks_empty_when_only_raw_survives(tmp_path):
    """摘要页不在盘上（只剩 `raw/<slug>.md`）→ 图里无此节点 → 空清单，且不炸。"""
    root = _kb(tmp_path)
    _raw(root, "foo")
    _page(root, "wiki/concepts/引用者.md", sources="['bar']", body="见 [[foo]]。")  # 悬链，早已存在

    plan = run_remove_result(root, "foo")
    assert plan.ok is True and plan.relocate == ["raw/foo.md"]
    assert plan.backlinks == []


def test_backlinks_count_alias_links(tmp_path):
    """别名链也算入链——docstring/CHANGELOG 都宣称"复用解析表故别名算数"，这条把它钉住。

    只有大小写用例守着的话，把 `resolve_owner` 换成朴素 `link_stem` 相等仍会全绿，而别名链
    （`[[富富报告]]` → `sources/foo.md`）会静静漏掉：那正是撤回后真会悬空的一条。
    """
    root = _kb(tmp_path)
    _raw(root, "foo")
    p = _source_page(root, "foo")
    p.write_text(
        p.read_text(encoding="utf-8").replace("tags: []", "tags: []\naliases: ['富富报告']"),
        encoding="utf-8",
    )
    _page(root, "wiki/concepts/引用者.md", sources="['bar']", body="见 [[富富报告]]。")

    assert run_remove_result(root, "foo").backlinks == ["wiki/concepts/引用者.md"]


def test_backlinks_bail_out_on_same_stem_ambiguity(tmp_path):
    """同-stem（`sources/foo` 与 `entities/foo`）→ 退空清单，不报假告警。

    二者共用 `Node.id`（小写 stem），入链归不到具体一页；且此时撤走摘要页后 `[[foo]]` 仍被
    实体页兜住、**根本不会悬链**。这正是 docs/P3.9 §7 评审发现4 点名的那道歧义——按路径找到
    target 再拿 id 收边，会把本不悬链的页当成"将悬空"报出来。
    """
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    _page(root, "wiki/entities/foo.md", type="entity", body="同 stem 的实体页。")
    _page(root, "wiki/concepts/引用者.md", sources="['bar']", body="见 [[foo]]，我指的是实体页。")

    plan = run_remove_result(root, "foo")
    assert plan.ok is True and plan.backlinks == []  # 退空：全库断链仍由末尾那句 lint 兜底


def test_backlinks_list_every_same_stem_linker_not_just_one(tmp_path):
    """两张**链者**页同 stem（共用 `Node.id`）→ 两张都列出，绝不只留一张。

    目标侧同-stem 退空是因为"那种情形下本就不会悬链"确定成立；来源侧没有这条护身符——
    `{id: path}` 收边会让 `concepts/bar` 与 `entities/bar` 只剩其一，另一条**静默漏报**，
    而漏报恰好瞒掉本功能唯一要说的事。宁可多列一页（人一眼可辨），不可少列一页。
    """
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    _page(root, "wiki/concepts/bar.md", sources="['x']", body="见 [[foo]]。")
    _page(root, "wiki/entities/bar.md", type="entity", sources="['x']", body="也见 [[foo]]。")

    assert run_remove_result(root, "foo").backlinks == [
        "wiki/concepts/bar.md",
        "wiki/entities/bar.md",
    ]


def test_manifest_records_backlinks_as_blast_radius(tmp_path):
    """`manifest.json` 记下入链页——与 `orphaned` 同属 blast-radius 审计面，不该只进屏幕。"""
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    _page(root, "wiki/concepts/引用者.md", sources="['bar']", body="见 [[foo]]。")

    assert remove_entrypoint(root, src="foo", yes=True, json_output=False) == EXIT_OK
    assert _manifest(root)["backlinks"] == ["wiki/concepts/引用者.md"]


def test_json_contract_carries_backlinks(tmp_path, capsys):
    """`--json` 契约加法：新增 `backlinks` 键，既有键不动。"""
    root = _kb(tmp_path)
    _raw(root, "foo")
    _source_page(root, "foo")
    _page(root, "wiki/concepts/引用者.md", sources="['bar']", body="见 [[foo]]。")

    assert remove_entrypoint(root, src="foo", yes=False, json_output=True) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["backlinks"] == ["wiki/concepts/引用者.md"]
    assert payload["orphans"] == [] and payload["drop_slug"] == []
