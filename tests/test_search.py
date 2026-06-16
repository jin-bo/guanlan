"""P5.0 search 检索层测试（零 LLM，见 docs/P5.0-检索层.md §8）。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from guanlan.errors import EXIT_OK, EXIT_USAGE
from guanlan.graph import build_graph, graph_to_dict
from guanlan.search import (
    CorpusCache,
    build_corpus,
    build_doc,
    score,
    search_entrypoint,
    search_pages,
    search_result_dict,
    tokenize,
)


def _kb(tmp_path: Path) -> Path:
    """搭最小知识库根（wiki/ + config 三件套），返回根目录。"""
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text(
        "# 索引\n\n## Entities\n\n- [去中心化金融](entities/DeFi.md) — 占位\n",
        encoding="utf-8",
    )
    (wiki / "log.md").write_text("# 时间线\n去中心化金融 不该被检索到。\n", encoding="utf-8")
    (wiki / "overview.md").write_text("综述 去中心化金融 也不该被检索到。\n", encoding="utf-8")
    return tmp_path


def _page(
    wiki: Path,
    rel: str,
    *,
    title: str = "T",
    type: str = "entity",
    aliases: list[str] | None = None,
    body: str = "实质正文内容。",
    raw: str | None = None,
) -> Path:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        p.write_text(raw, encoding="utf-8")
        return p
    alias_line = ""
    if aliases is not None:
        alias_line = "aliases: [" + ", ".join(f"'{a}'" for a in aliases) + "]\n"
    fm = f"---\ntitle: '{title}'\ntype: {type}\n{alias_line}---\n\n{body}\n"
    p.write_text(fm, encoding="utf-8")
    return p


def _pages(result) -> list[str]:
    return [h.page for h in result.hits]


# ---------- 分词归口 ----------


def test_tokenize_cjk_bigram_and_words():
    assert tokenize("去中心化") == ["去中", "中心", "心化"]
    assert tokenize("李") == ["李"]  # 单字退化 1-gram
    assert tokenize("DeFi GPT-4") == ["defi", "gpt", "4"]
    assert tokenize("L2 扩容") == ["l2", "扩容"]  # 混排两侧切法一致


def test_tokenize_cjk_predicate_boundary():
    # 假名/全角符号不进 CJK 段（按非 CJK 逻辑走、标点被丢）；café 重音被丢。
    assert tokenize("café") == ["caf"]
    assert tokenize("ＡＢ。、！") == []  # 全角拉丁/标点：非 CJK 谓词、且 [a-z0-9] 不含全角
    # 基本区/扩展 A/兼容表意都算 CJK：兼容表意 U+F900「豈」与基本区「一」相邻 2-gram。
    assert tokenize("一豈") == ["一豈"]


def test_tokenize_deterministic():
    assert tokenize("去中心化金融 DeFi") == tokenize("去中心化金融 DeFi")


# ---------- 召回正确性 ----------


def test_recall_basic(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/DeFi.md", title="DeFi", body="DeFi（去中心化金融）指建立在公链上的金融。")
    _page(wiki, "concepts/Other.md", title="无关", body="这页讲别的主题，毫不相干。")

    r = search_pages(wiki, "去中心化金融")
    assert r.hits, "应有命中"
    assert r.hits[0].page == "wiki/entities/DeFi.md"
    assert "wiki/concepts/Other.md" not in _pages(r)
    # 英文 query 同样命中。
    assert search_pages(wiki, "DeFi").hits[0].page == "wiki/entities/DeFi.md"
    # 单字 query 退化 1-gram 仍召回（正文含**孤立**单字「李」；2-gram 不召回嵌于词中的单字）。
    _page(wiki, "entities/Li.md", title="作者", body="李 是一位作者。")
    assert "wiki/entities/Li.md" in _pages(search_pages(wiki, "李"))


def test_config_pages_excluded(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/DeFi.md", title="DeFi", body="去中心化金融 正文。")
    r = search_pages(wiki, "去中心化金融")
    paths = _pages(r)
    assert all("index.md" not in p and "log.md" not in p and "overview.md" not in p for p in paths)


# ---------- 字段加权 ----------


def test_field_boost_ranks_title_above_body_mention(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    # A：query 在标题；B：query 仅正文边缘一次提及。
    _page(wiki, "entities/A.md", title="区块链", body="无关填充内容。" * 5)
    _page(wiki, "entities/B.md", title="杂项", body="一段很长的无关内容。" * 20 + " 区块链 偶尔提到。")
    r = search_pages(wiki, "区块链")
    assert _pages(r)[0] == "wiki/entities/A.md"


def test_field_boost_not_punished_by_length_norm(tmp_path: Path):
    """决策P5.0-17：标题/别名长（boost token 多）不应因 dl 被撑大而排到正文同样命中页之后。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    # 长标题 + 长别名都含 query 词「向量」，body 也确含；boost 进 tf 不进 dl。
    _page(
        wiki,
        "entities/Long.md",
        title="向量 检索 嵌入 重排 召回 综述 长标题",
        aliases=["向量数据库", "向量索引", "向量召回"],
        body="向量 是一种表示。",
    )
    # 短标题、正文同样命中一次。
    _page(wiki, "entities/Short.md", title="短", body="向量 也在这里出现一次。")
    r = search_pages(wiki, "向量")
    # Long 页字段命中远多，boost 只抬 tf，不应被长度归一化反噬到 Short 之后。
    assert _pages(r)[0] == "wiki/entities/Long.md"
    # 验证 dl 只按 body 算：Long 的 body 短，dl 不被标题/别名撑大。
    docs = {d.page: d for d in build_corpus(wiki)}
    assert docs["wiki/entities/Long.md"].dl == len(tokenize("向量 是一种表示。"))


def test_alias_recall(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/LLM.md", title="大语言模型", aliases=["大模型", "LLM"], body="一种模型。")
    r = search_pages(wiki, "大模型")
    assert "wiki/entities/LLM.md" in _pages(r)


# ---------- 字段进召回面、snippet 只取 body ----------


def test_field_only_hit_recalled_snippet_from_body(tmp_path: Path):
    """决策P5.0-23/24：query 仅在标题/别名、正文完全不含 → 仍召回，snippet 退化正文首窗。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/X.md", title="量子纠缠", aliases=["量子比特"], body="这段正文不含查询词，只是普通描述。")
    r = search_pages(wiki, "量子纠缠")
    assert "wiki/entities/X.md" in _pages(r)
    hit = next(h for h in r.hits if h.page == "wiki/entities/X.md")
    # snippet 只从 body 取：body 非空 → 正文首窗；绝不含标题/别名串。
    assert "量子纠缠" not in hit.snippet
    assert "量子比特" not in hit.snippet
    assert hit.snippet.startswith("这段正文")


# ---------- page 字段口径 ----------


def test_page_field_matches_graph_node_path(tmp_path: Path, monkeypatch):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/DeFi.md", title="DeFi", body="去中心化金融 [[DeFi]] 正文。")
    # 从不同 cwd 跑，page 恒为相对库根 posix。
    monkeypatch.chdir(tmp_path)
    r1 = search_pages(wiki, "去中心化金融")
    monkeypatch.chdir(Path(tmp_path).parent)
    r2 = search_pages(wiki, "去中心化金融")
    assert _pages(r1) == _pages(r2) == ["wiki/entities/DeFi.md"]
    # 与 graph.json 的 Node.path 逐字一致。
    g_paths = {n["path"] for n in graph_to_dict(build_graph(wiki))["nodes"]}
    assert "wiki/entities/DeFi.md" in g_paths
    assert r1.hits[0].page in g_paths


# ---------- 确定性 / 幂等 ----------


def test_deterministic_byte_stable(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="区块链", body="区块链 技术 去中心化 账本。")
    _page(wiki, "entities/B.md", title="智能合约", body="区块链 上的 智能合约。")
    out1 = json.dumps([(h.page, h.score, h.snippet) for h in search_pages(wiki, "区块链").hits])
    out2 = json.dumps([(h.page, h.score, h.snippet) for h in search_pages(wiki, "区块链").hits])
    assert out1 == out2


# ---------- 容错 ----------


def test_tolerates_bad_and_missing_frontmatter(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    # 完全无 frontmatter → title 退化 stem、body 即全文。
    _page(wiki, "entities/NoFm.md", raw="去中心化金融 纯正文无 frontmatter。\n")
    # 坏 YAML frontmatter——仍按 body + stem 索引、绝不抛。
    _page(wiki, "entities/Bad.md", raw="---\n: [unclosed\n---\n\n去中心化金融 正文在此。\n")
    r = search_pages(wiki, "去中心化金融")
    pages = _pages(r)
    assert "wiki/entities/NoFm.md" in pages
    assert "wiki/entities/Bad.md" in pages
    nofm = next(h for h in r.hits if h.page == "wiki/entities/NoFm.md")
    assert nofm.title == "NoFm"  # 无合法 frontmatter title → 退化 stem


def test_tolerates_non_utf8(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    p = wiki / "entities/GBK.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes("---\ntitle: GBK页\n---\n\n去中心化金融 正文。\n".encode("gbk"))
    # errors="replace" 兜底、不崩 UnicodeDecodeError。
    r = search_pages(wiki, "GBK")  # 非 CJK 词命中标题
    assert any(h.page == "wiki/entities/GBK.md" for h in r.hits) or r.pages_searched >= 1
    # 关键：不抛即通过。
    assert build_doc(p, root=wiki.parent).page == "wiki/entities/GBK.md"


# ---------- 轻量 frontmatter 行扫 ----------


def test_lightweight_frontmatter_scan(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    # 块列表 aliases。
    _page(
        wiki,
        "entities/Block.md",
        raw="---\ntitle: 块标量标题\ntype: concept\naliases:\n  - 别名甲\n  - 别名乙\n---\n\n正文。\n",
    )
    doc = build_doc(wiki / "entities/Block.md", root=wiki.parent)
    assert doc.title == "块标量标题"
    assert doc.type == "concept"
    # 别名进 tf（验证块列表被扫到）。
    assert tokenize("别名甲")[0] in doc.tf
    assert "wiki/entities/Block.md" in _pages(search_pages(wiki, "别名乙"))


def test_alias_block_list_robust(tmp_path: Path):
    """块列表中**空项不截断**、**不跨空行误并入别的字段列表**（review 加固）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    # 空列表项夹在中间：甲、乙都该被扫到（不因 `- ` 空项在丙处提前中断）。
    _page(
        wiki,
        "entities/A.md",
        raw="---\ntitle: A\naliases:\n  - 甲名\n  - \n  - 乙名\n---\n\n正文。\n",
    )
    # 空行后另一字段的列表项**不该**并入 aliases。
    _page(
        wiki,
        "entities/B.md",
        raw="---\ntitle: B\naliases:\n  - 丙名\n\ntags:\n  - 丁标签\n---\n\n正文。\n",
    )
    da = build_doc(wiki / "entities/A.md", root=wiki.parent)
    assert tokenize("甲名")[0] in da.tf and tokenize("乙名")[0] in da.tf
    db = build_doc(wiki / "entities/B.md", root=wiki.parent)
    assert tokenize("丙名")[0] in db.tf
    assert tokenize("丁标签")[0] not in db.tf  # tags 的项不该被并入别名匹配面


def test_frontmatter_inline_comments_ignored(tmp_path: Path):
    """约定模板的行内注释（`aliases: [] # 可选` / `type: entity # 或 concept`）不入 tf（Codex P3）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(
        wiki,
        "entities/A.md",
        raw="---\ntitle: 甲页 # 标题注释\ntype: entity   # 或 concept\n"
        "aliases: []           # 可选：常用别名\n---\n\n正文内容。\n",
    )
    doc = build_doc(wiki / "entities/A.md", root=wiki.parent)
    assert doc.title == "甲页"  # 注释不进标题
    assert doc.type == "entity"
    # 空别名 + 注释词都不该可召回该页。
    for comment_word in ("可选", "concept", "常用别名", "标题注释"):
        assert "wiki/entities/A.md" not in _pages(search_pages(wiki, comment_word)), comment_word
    # flow 列表带注释仍正常取别名。
    _page(wiki, "entities/B.md", raw="---\ntitle: B\naliases: [大模型] # 注释词\n---\n\n正文。\n")
    assert "wiki/entities/B.md" in _pages(search_pages(wiki, "大模型"))
    assert "wiki/entities/B.md" not in _pages(search_pages(wiki, "注释词"))


def test_body_not_parsed_by_yaml(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    # 注入坏 YAML frontmatter，body 仍正常按文本索引（不经 yaml.safe_load）。
    _page(wiki, "entities/Y.md", raw="---\n: [unclosed\n---\n\n向量 检索 正文。\n")
    assert "wiki/entities/Y.md" in _pages(search_pages(wiki, "向量"))


# ---------- 进程内 mtime memo ----------


def test_corpus_cache_reuse_and_invalidate(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    p = _page(wiki, "entities/A.md", title="A", body="去中心化 一。")
    cache = CorpusCache()
    c1 = cache.corpus(wiki)
    c2 = cache.corpus(wiki)
    # 未变 → 复用同一 DocBag 对象（is）。
    assert c1[0] is c2[0]
    # 改一页 → 该页重建、且与冷算字节等价。
    import os

    p.write_text("---\ntitle: A\n---\n\n去中心化 二 三。\n", encoding="utf-8")
    os.utime(p, ns=(c1[0].mtime_ns + 10**9, c1[0].mtime_ns + 10**9))
    cold = {d.page: d for d in build_corpus(wiki)}
    warm = {d.page: d for d in cache.corpus(wiki)}
    assert warm["wiki/entities/A.md"].tf == cold["wiki/entities/A.md"].tf
    # 删页 → 移除。
    p.unlink()
    assert all(d.page != "wiki/entities/A.md" for d in cache.corpus(wiki))


def test_corpus_cache_isolates_roots(tmp_path: Path):
    """一个 cache 服务两库时，同相对路径 + 偶合 (mtime,size) 不串用对方 DocBag（Codex P2）。"""
    import os

    rootA = _kb(tmp_path / "A")
    rootB = _kb(tmp_path / "B")
    # 两库同相对路径、**同字节长度**的不同内容（构造 size 相同）。
    pa = _page(rootA / "wiki", "entities/A.md", raw="正文甲甲甲甲甲。\n")
    pb = _page(rootB / "wiki", "entities/A.md", raw="正文乙乙乙乙乙。\n")
    assert pa.stat().st_size == pb.stat().st_size  # 同 size
    same_ns = pa.stat().st_mtime_ns
    os.utime(pa, ns=(same_ns, same_ns))
    os.utime(pb, ns=(same_ns, same_ns))  # 偶合 mtime_ns
    cache = CorpusCache()
    da = {d.page: d for d in cache.corpus(rootA / "wiki")}["wiki/entities/A.md"]
    db = {d.page: d for d in cache.corpus(rootB / "wiki")}["wiki/entities/A.md"]
    assert "甲" in da.body and "乙" not in da.body
    assert "乙" in db.body and "甲" not in db.body  # 不串用 A 的 DocBag
    # 交替访问不互相剪枝：再取 A 仍复用同一对象。
    da2 = {d.page: d for d in cache.corpus(rootA / "wiki")}["wiki/entities/A.md"]
    assert da2 is da


def test_corpus_cache_isolates_relative_roots(tmp_path: Path, monkeypatch):
    """相对 `wiki` 路径在不同 cwd 下不撞同一桶（Codex P2 跟进：先 resolve 再分桶）。"""
    import os

    rootA = _kb(tmp_path / "A")
    rootB = _kb(tmp_path / "B")
    pa = _page(rootA / "wiki", "entities/A.md", raw="正文甲甲甲甲甲。\n")
    pb = _page(rootB / "wiki", "entities/A.md", raw="正文乙乙乙乙乙。\n")
    same_ns = pa.stat().st_mtime_ns
    os.utime(pa, ns=(same_ns, same_ns))
    os.utime(pb, ns=(same_ns, same_ns))
    cache = CorpusCache()
    monkeypatch.chdir(rootA)  # 传相对路径 "wiki"，parent 文本同为 "."。
    da = {d.page: d for d in cache.corpus(Path("wiki"))}["wiki/entities/A.md"]
    monkeypatch.chdir(rootB)
    db = {d.page: d for d in cache.corpus(Path("wiki"))}["wiki/entities/A.md"]
    assert "甲" in da.body and "乙" not in da.body
    assert "乙" in db.body and "甲" not in db.body  # 不撞 "." 同桶、不串用 A


def test_corpus_cache_keys_match_cold_nested_dirs(tmp_path: Path):
    """决策P5.1-8：corpus() 的字符串切片 key 在多级目录下与冷算 `relative_to().as_posix()` 字节一致。

    锁收窄后 corpus() 不再用 `relative_to(root).as_posix()` 算 key，改用 `os.fspath` 切片。这里铺多级
    嵌套页，断言 corpus() 返回的 `page` 集合与冷算 `build_corpus` **逐字节相同**（切片若错会漏页/串 key）。
    """
    root = _kb(tmp_path)
    wiki = root / "wiki"
    for rel in (
        "entities/DeFi.md",
        "concepts/sub/deep/Liquidity.md",
        "syntheses/a/b/c/Note.md",
        "sources/S1.md",
    ):
        _page(wiki, rel, body="去中心化金融 流动性 liquidity。")
    cold = sorted(d.page for d in build_corpus(wiki))
    warm = sorted(d.page for d in CorpusCache().corpus(wiki))
    assert warm == cold
    assert "wiki/concepts/sub/deep/Liquidity.md" in warm  # 多级相对 posix 正确


def test_corpus_cache_thread_safe(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    for i in range(20):
        _page(wiki, f"entities/P{i}.md", title=f"P{i}", body=f"去中心化金融 第{i}页。")
    cache = CorpusCache()
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda _: len(cache.corpus(wiki)), range(40)))
    assert all(n == 20 for n in results)
    cold = {d.page for d in build_corpus(wiki)}
    warm = {d.page for d in cache.corpus(wiki)}
    assert cold == warm


# ---------- 边界 / 零除 ----------


def test_limit_validation_in_core(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", body="去中心化金融。")
    with pytest.raises(ValueError):
        score(build_corpus(wiki), "去中心化金融", limit=0)
    with pytest.raises(ValueError):
        search_pages(wiki, "去中心化金融", limit=-1)


def test_limit_truncation(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    for i in range(5):
        _page(wiki, f"entities/P{i}.md", title=f"P{i}", body="去中心化金融 内容。")
    assert len(search_pages(wiki, "去中心化金融", limit=2).hits) == 2


def test_empty_corpus(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"  # 仅 config 页、无 content。
    r = search_pages(wiki, "去中心化金融")
    assert r.hits == []
    assert r.pages_searched == 0


def test_avgdl_zero_no_field_token(tmp_path: Path):
    """body 全空且无字段 token → 空结果、pages_searched=页数、无除零。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/Empty.md", raw="---\ntitle: \"\"\n---\n\n")
    r = search_pages(wiki, "去中心化金融")
    assert r.hits == []
    assert r.pages_searched == 1


def test_avgdl_zero_field_hit_recalled(tmp_path: Path):
    """body 全空但 aliases 含 query token（avgdl=0 仍成立）→ 该页被字段命中召回、不被误短路。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", raw="---\ntitle: \"\"\naliases:\n  - 向量\n---\n\n")
    r = search_pages(wiki, "向量")
    assert "wiki/entities/A.md" in _pages(r)
    hit = r.hits[0]
    assert hit.snippet == ""  # body 空 → snippet 合法地为 ""，不回填别名串
    assert hit.score > 0


def test_query_no_token_returns_empty(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", body="去中心化金融。")
    # 直接调内核：纯标点 query → 空 hits（非短路、pages_searched 如实）。
    r = search_pages(wiki, "。、！ ")
    assert r.hits == []
    assert r.pages_searched == 1


# ---------- 片段 ----------


def test_snippet_word_boundary(tmp_path: Path):
    """决策P5.0-11：query `ai` 不因正文有 said/chain 而把窗口拽偏。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="主题", body="he said the blockchain is good. ai matters here.")
    r = search_pages(wiki, "ai")
    hit = r.hits[0]
    assert hit.snippet.startswith("ai matters")  # 切到真正的 ai 整词，不是 said/chain


def test_snippet_case_insensitive_no_drift(tmp_path: Path):
    """决策P5.0-11：大小写不敏感且改长字符前置时下标不漂移。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="主题", body="prefix ß more text DeFi appears here now.")
    hit = search_pages(wiki, "defi").hits[0]
    assert "DeFi appears" in hit.snippet  # 切到含 DeFi 的真实窗口，不误退化首窗


# ---------- 内核元数据 ----------


def test_pages_searched_metadata(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    for i in range(3):
        _page(wiki, f"entities/P{i}.md", body=f"内容 {i}。")
    r = search_pages(wiki, "内容")
    assert r.pages_searched == 3


# ---------- 分数口径（CLI 补零 vs JSON number） ----------


def test_score_format_cli_vs_json(tmp_path: Path, capsys):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="区块链", body="区块链 内容。")
    # JSON：score 是 number，不补尾零。
    rc = search_entrypoint(root, query="区块链", limit=10, json_output=True)
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    score_val = payload["results"][0]["score"]
    assert isinstance(score_val, (int, float))
    assert score_val == round(score_val, 6)
    # CLI 文本：6 位定点补零。
    search_entrypoint(root, query="区块链", limit=10, json_output=False)
    text_out = capsys.readouterr().out
    assert f"{score_val:.6f}" in text_out


# ---------- search_result_dict 单一归口（P5.1 决策P5.1-4）----------


def test_search_result_dict_matches_render_json(tmp_path: Path, capsys):
    """`search_result_dict(result)` 与 CLI `--json` 体**解析后相等**：同一归口产出（P5.1-4）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="区块链", body="区块链 内容。")
    result = search_pages(wiki, "区块链", limit=10)
    d = search_result_dict(result)
    # 字段齐备
    assert set(d) == {"ok", "query", "pages_searched", "results"}
    assert d["ok"] is True and d["query"] == "区块链"
    assert set(d["results"][0]) == {"page", "title", "type", "score", "snippet"}
    # 与 CLI --json 分支同源（_render 已改调 search_result_dict）
    search_entrypoint(root, query="区块链", limit=10, json_output=True)
    cli = json.loads(capsys.readouterr().out)
    assert cli == d


# ---------- --json 错误形态 ----------


def test_json_error_shape(tmp_path: Path, capsys):
    root = _kb(tmp_path)
    rc = search_entrypoint(root, query="   ", limit=10, json_output=True)
    out = capsys.readouterr()
    assert rc == EXIT_USAGE
    payload = json.loads(out.out)  # 完整对象、非半个
    assert payload["ok"] is False
    assert "error" in payload
    assert out.err == ""  # --json 不污染 stderr


def test_text_error_goes_to_stderr(tmp_path: Path, capsys):
    root = _kb(tmp_path)
    rc = search_entrypoint(root, query="", limit=10, json_output=False)
    out = capsys.readouterr()
    assert rc == EXIT_USAGE
    assert out.out == ""  # stdout 不被污染
    assert out.err.strip()


def test_not_a_kb_root(tmp_path: Path, capsys):
    rc = search_entrypoint(tmp_path, query="x", limit=10, json_output=True)
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_USAGE
    assert payload["ok"] is False


# ---------- 只读保证 ----------


def test_no_writes(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", body="去中心化金融 内容。")
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    search_pages(wiki, "去中心化金融")
    search_entrypoint(root, query="去中心化金融", limit=10, json_output=True)
    after = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    assert before == after  # 无新文件、无改动


def test_no_hit_is_ok(tmp_path: Path):
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", body="完全无关的内容。")
    r = search_pages(wiki, "区块链")
    assert r.hits == []
    assert r.pages_searched == 1


# ---------- P5.3 反链文档先验（backlink 重排）----------


def _count_build_graph(monkeypatch) -> list:
    """patch `guanlan.graph.build_graph`（被 search 函数级 `from .graph import` 在调用期取）计调用次数。"""
    import guanlan.graph as graph_mod

    calls: list = []
    real = graph_mod.build_graph
    monkeypatch.setattr(graph_mod, "build_graph", lambda w: (calls.append(w), real(w))[1])
    return calls


def test_backlink_boost_ranks_higher_inlink_first(tmp_path: Path):
    """同 BM25 两页，有入链者上浮到 path 靠前页之前（log 缩放生效，决策P5.3-2）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    # Aaa/Zzz 正文标题全同 → BM25 相等；Ref 给 Zzz 一条入链、自身不命中 query。
    _page(wiki, "entities/Aaa.md", title="区块链", body="区块链 技术 内容。")
    _page(wiki, "entities/Zzz.md", title="区块链", body="区块链 技术 内容。")
    _page(wiki, "concepts/Ref.md", title="引用页", body="见 [[Zzz]]")
    # 冷算路径已带 boost：Zzz 有入链 → 即使 path 靠后也排到 Aaa 之前。
    assert _pages(search_pages(wiki, "区块链"))[:2] == ["wiki/entities/Zzz.md", "wiki/entities/Aaa.md"]
    # 反证：纯 BM25（inlinks=None）两页同分 → 按 path 升序 → Aaa 在前。
    plain = score(build_corpus(wiki), "区块链", limit=10)
    assert _pages(plain)[:2] == ["wiki/entities/Aaa.md", "wiki/entities/Zzz.md"]


def test_backlink_zero_inlink_not_dropped(tmp_path: Path):
    """零反链页不被归零挤出：仍在结果里、分数 == 纯 BM25（因子 1.0，决策P5.3-2）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/Solo.md", title="孤页", body="去中心化金融 内容。")  # 无任何入链
    r = search_pages(wiki, "去中心化金融")
    assert "wiki/entities/Solo.md" in _pages(r)
    plain = score(build_corpus(wiki), "去中心化金融", limit=10)
    s_boost = next(h.score for h in r.hits if h.page == "wiki/entities/Solo.md")
    s_plain = next(h.score for h in plain.hits if h.page == "wiki/entities/Solo.md")
    assert s_boost == s_plain  # c=0 因子 1.0，原分原样保留


def test_backlink_boost_preserves_recall_set(tmp_path: Path):
    """收录门槛判在 boost 之前：带 boost 的召回集 == 纯 BM25 召回集，boost 只改名次（决策P5.3-7）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    # 多页不同入链、不同命中强度；query 命中全部。
    _page(wiki, "entities/Aaa.md", title="区块链", body="区块链 [[Bbb]] [[Ccc]] 内容。")
    _page(wiki, "entities/Bbb.md", title="区块链", body="区块链 [[Ccc]] 内容。")
    _page(wiki, "entities/Ccc.md", title="区块链", body="区块链 内容。")
    boosted = set(_pages(search_pages(wiki, "区块链")))
    plain = set(_pages(score(build_corpus(wiki), "区块链", limit=10)))
    assert boosted == plain  # 召回集逐页相同（boost 绝不增删召回，只重排）


def test_backlink_boost_byte_stable(tmp_path: Path):
    """带 boost 的检索同库同 query 连跑两次字节稳定（决策P5.3-7）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="区块链", body="区块链 技术。")
    _page(wiki, "entities/B.md", title="智能合约", body="区块链 上的 [[A]] 智能合约。")
    out1 = json.dumps([(h.page, h.score, h.snippet) for h in search_pages(wiki, "区块链").hits])
    out2 = json.dumps([(h.page, h.score, h.snippet) for h in search_pages(wiki, "区块链").hits])
    assert out1 == out2


def test_score_inlinks_none_is_pure_bm25(tmp_path: Path):
    """`inlinks=None` 与省略一致，且 == 既有纯 BM25 行为（向后兼容，决策P5.3-4）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="区块链", body="区块链 [[A]] 自指 技术。")
    docs = build_corpus(wiki)
    omit = [(h.page, h.score) for h in score(docs, "区块链", limit=10).hits]
    explicit = [(h.page, h.score) for h in score(docs, "区块链", limit=10, inlinks=None).hits]
    assert omit == explicit


def test_backlink_cold_and_cached_ranks_match(tmp_path: Path):
    """四处接入名次一致的根因：冷算（CLI）与 cache 路径（Web/MCP/chat）产出**逐字节相同**的分数+名次。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/Aaa.md", title="区块链", body="区块链 技术。")
    _page(wiki, "entities/Zzz.md", title="区块链", body="区块链 技术。")
    _page(wiki, "concepts/Ref.md", title="引用", body="见 [[Zzz]]")
    cold = search_pages(wiki, "区块链")
    cache = CorpusCache()
    docs = cache.corpus(wiki)
    cached = score(docs, "区块链", limit=10, inlinks=cache.backlinks(wiki, docs))
    assert [(h.page, h.score) for h in cold.hits] == [(h.page, h.score) for h in cached.hits]


def test_backlinks_cache_reuse_and_content_invalidate(tmp_path: Path, monkeypatch):
    """memo：(a) 签名命中不再 build_graph；(b) 改 content 页链接 → 签名变 → 重算、入链更新（决策P5.3-5）。"""
    import os

    root = _kb(tmp_path)
    wiki = root / "wiki"
    p = _page(wiki, "entities/A.md", title="A", body="见 [[Hub]]")
    _page(wiki, "entities/Hub.md", title="Hub", body="枢纽。")
    cache = CorpusCache()
    calls = _count_build_graph(monkeypatch)

    bl1 = cache.backlinks(wiki, cache.corpus(wiki))
    assert bl1["wiki/entities/Hub.md"] == 1 and len(calls) == 1
    # (a) 同库再调 → 签名命中、不再 build_graph。
    bl2 = cache.backlinks(wiki, cache.corpus(wiki))
    assert bl2 == bl1 and len(calls) == 1
    # (b) A 不再链 Hub → 签名变 → 重算。
    mt = p.stat().st_mtime_ns + 10**9
    p.write_text("---\ntitle: A\n---\n\n不再链接。\n", encoding="utf-8")
    os.utime(p, ns=(mt, mt))
    bl3 = cache.backlinks(wiki, cache.corpus(wiki))
    assert len(calls) == 2 and bl3["wiki/entities/Hub.md"] == 0


def test_backlinks_cache_config_change_no_rebuild(tmp_path: Path, monkeypatch):
    """config 页（index.md）变更不进 docs 签名 → 不触发反链重算（content-only 失效，决策P5.3-5）。"""
    import os

    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", body="见 [[Hub]]")
    _page(wiki, "entities/Hub.md", body="枢纽。")
    cache = CorpusCache()
    calls = _count_build_graph(monkeypatch)

    cache.backlinks(wiki, cache.corpus(wiki))
    assert len(calls) == 1
    idx = wiki / "index.md"
    mt = idx.stat().st_mtime_ns + 10**9
    idx.write_text("# 索引 改了内容\n", encoding="utf-8")
    os.utime(idx, ns=(mt, mt))
    cache.backlinks(wiki, cache.corpus(wiki))
    assert len(calls) == 1  # config 变更不触发反链重算


def test_backlinks_cache_reordered_docs_safe_rebuild(tmp_path: Path, monkeypatch):
    """传入乱序 docs → 签名不匹配 → 安全多余重算（决不误命中陈旧），结果仍正确（决策P5.3-5 契约）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", body="见 [[Hub]]")
    _page(wiki, "entities/Hub.md", body="枢纽。")
    cache = CorpusCache()
    calls = _count_build_graph(monkeypatch)

    docs = cache.corpus(wiki)
    bl1 = cache.backlinks(wiki, docs)
    assert len(calls) == 1
    bl2 = cache.backlinks(wiki, list(reversed(docs)))  # 乱序 → 签名不命中
    assert len(calls) == 2  # 多算一次（安全降级）
    assert bl2 == bl1  # 结果正确、绝不返回陈旧


def test_backlinks_cache_isolates_roots(tmp_path: Path):
    """一个 cache 服务两库：反链 memo 按库根分桶、不串用对方入链（决策P5.3-5）。"""
    rootA = _kb(tmp_path / "A")
    rootB = _kb(tmp_path / "B")
    wA, wB = rootA / "wiki", rootB / "wiki"
    _page(wA, "entities/A.md", body="见 [[Hub]]")
    _page(wA, "entities/Hub.md", body="枢纽 A。")
    _page(wB, "entities/Hub.md", body="枢纽 B 无人链入。")
    cache = CorpusCache()
    blA = cache.backlinks(wA, cache.corpus(wA))
    blB = cache.backlinks(wB, cache.corpus(wB))
    assert blA["wiki/entities/Hub.md"] == 1
    assert blB["wiki/entities/Hub.md"] == 0  # 不串用 A 的反链


def test_backlinks_cache_no_writes(tmp_path: Path):
    """反链路径只读：`build_graph` 不写 `graph/`，整库零字节写盘（决策P5.3-6）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", body="见 [[Hub]]")
    _page(wiki, "entities/Hub.md", body="枢纽。")
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    cache = CorpusCache()
    bl = cache.backlinks(wiki, cache.corpus(wiki))
    assert bl["wiki/entities/Hub.md"] == 1  # 反链确实算了（非空操作蒙混）
    assert not (root / "graph").exists()  # 没建派生 graph/
    after = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_backlinks_cache_oserror_degrades_to_no_boost(tmp_path: Path, monkeypatch):
    """build_graph 抛 OSError（页删/不可读 race）→ backlinks 返回 {}（纯 BM25 降级）、不 memo 失败、下次重试。"""
    import guanlan.graph as graph_mod

    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", body="见 [[Hub]]")
    _page(wiki, "entities/Hub.md", body="枢纽。")
    cache = CorpusCache()
    docs = cache.corpus(wiki)

    monkeypatch.setattr(graph_mod, "build_graph", lambda w: (_ for _ in ()).throw(OSError("gone")))
    assert cache.backlinks(wiki, docs) == {}  # 降级为无 boost，不 500/不抛
    # 不 memo 失败：恢复后下次重试出真值。
    monkeypatch.undo()
    assert cache.backlinks(wiki, cache.corpus(wiki))["wiki/entities/Hub.md"] == 1


def test_cache_search_equals_cold(tmp_path: Path):
    """`CorpusCache.search` 单一入口与冷算 `search_pages` 逐字段一致（决策P5.3-4 收口、不破等价）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/Aaa.md", title="区块链", body="区块链 技术。")
    _page(wiki, "entities/Zzz.md", title="区块链", body="区块链 技术。")
    _page(wiki, "concepts/Ref.md", title="引用", body="见 [[Zzz]]")
    cold = search_result_dict(search_pages(wiki, "区块链", limit=10))
    warm = search_result_dict(CorpusCache().search(wiki, "区块链", limit=10))
    assert warm == cold


# ---------- P5.4 检索冷启动性能（启动预热 + singleflight 构建锁）----------


def test_prewarm_then_search_no_rebuild(tmp_path: Path, monkeypatch):
    """预热付清两笔冷算后，后续 search 全 memo 命中：corpus 不再 build_doc、backlinks 不再 build_graph。"""
    import guanlan.search as search_mod

    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="区块链", body="区块链 [[Hub]] 技术。")
    _page(wiki, "entities/Hub.md", title="Hub", body="枢纽。")
    cache = CorpusCache()
    assert cache.prewarm(wiki) is True  # 预热成功
    calls = _count_build_graph(monkeypatch)
    builds: list = []
    real_build = search_mod.build_doc
    monkeypatch.setattr(
        search_mod, "build_doc", lambda p, *, root: (builds.append(p), real_build(p, root=root))[1]
    )
    # 预热后未改盘 → search 走纯 memo：零 build_doc、零 build_graph。
    cache.search(wiki, "区块链", limit=10)
    assert builds == [] and calls == []


def test_prewarm_equivalent_to_cold(tmp_path: Path):
    """预热后的 search 结果与冷算 `search_pages` 逐字段一致（预热只搬运冷算、不改语义，决策P5.4-1）。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/Aaa.md", title="区块链", body="区块链 技术。")
    _page(wiki, "entities/Zzz.md", title="区块链", body="区块链 技术。")
    _page(wiki, "concepts/Ref.md", title="引用", body="见 [[Zzz]]")
    cache = CorpusCache()
    cache.prewarm(wiki)
    cold = search_result_dict(search_pages(wiki, "区块链", limit=10))
    warm = search_result_dict(cache.search(wiki, "区块链", limit=10))
    assert warm == cold


def test_prewarm_idempotent(tmp_path: Path):
    """预热幂等：连跑两次都成功、互不破坏；与未预热 search 结果一致。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="区块链", body="区块链 技术。")
    cache = CorpusCache()
    assert cache.prewarm(wiki) is True
    assert cache.prewarm(wiki) is True
    warm = search_result_dict(cache.search(wiki, "区块链", limit=10))
    assert warm == search_result_dict(search_pages(wiki, "区块链", limit=10))


def test_prewarm_swallows_failure(tmp_path: Path, monkeypatch):
    """预热容错：底层抛异常 → prewarm 返回 False、**不抛**（守 serve 不被预热拖垮，决策P5.4-1）。"""
    import guanlan.search as search_mod

    root = _kb(tmp_path)
    wiki = root / "wiki"
    _page(wiki, "entities/A.md", title="区块链", body="区块链 技术。")
    cache = CorpusCache()
    monkeypatch.setattr(
        search_mod, "iter_pages", lambda w: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert cache.prewarm(wiki) is False  # 静默吞掉、不冒泡


def test_singleflight_concurrent_cold_builds_graph_once(tmp_path: Path, monkeypatch):
    """singleflight：N 个并发首搜（含模拟预热）冷启同库 → build_graph 只跑**一次**（决策P5.4-2 合流去重）。

    没有构建锁时，并发 miss 会各自 build_graph（last-writer-wins，N 次）；加锁后首个建、余者复查命中。
    """
    root = _kb(tmp_path)
    wiki = root / "wiki"
    for i in range(12):
        _page(wiki, f"entities/P{i}.md", title=f"P{i}", body=f"区块链 [[P0]] 第{i}页。")
    cache = CorpusCache()
    calls = _count_build_graph(monkeypatch)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda _: cache.search(wiki, "区块链", limit=10), range(8)))
    assert len(calls) == 1  # 8 路并发冷启，build_graph 仅一次（singleflight 合流）
    # 全部返回一致名次（与冷算等价）。
    cold = [(h.page, h.score) for h in search_pages(wiki, "区块链", limit=10).hits]
    for r in results:
        assert [(h.page, h.score) for h in r.hits] == cold


def test_corpus_does_not_evict_concurrently_added_page(tmp_path: Path, monkeypatch):
    """修 P5.4 评审 over-prune：构建锁内的剪枝以**锁外首次 snapshot** 为基准（非复查 re-snapshot），
    故并发线程在首次快照之后新加的页（不在本次 entries/seen）不被误删-重建。"""
    root = _kb(tmp_path)
    wiki = root / "wiki"
    p = _page(wiki, "entities/A.md", body="内容。")
    cache = CorpusCache()
    bucket = wiki.parent.as_posix()
    ghost_key = "wiki/entities/ghost.md"
    real_classify = cache._classify
    injected: list = []

    def classify_then_inject(entries, snapshot):
        result = real_classify(entries, snapshot)
        if not injected:  # 仅锁外首次分类后：模拟并发 builder 在我们首次快照之后提交 ghost。
            injected.append(True)
            cache._caches.setdefault(bucket, {})[ghost_key] = build_doc(p, root=wiki.parent)
        return result

    monkeypatch.setattr(cache, "_classify", classify_then_inject)
    cache.corpus(wiki)  # A 是 miss → 走构建锁分支 → 合并剪枝（旧实现会以 re-snapshot 误删 ghost）。
    assert ghost_key in cache._caches[bucket]  # 并发新加页未被误删


def test_singleflight_concurrent_corpus_builds_each_page_once(tmp_path: Path, monkeypatch):
    """singleflight：并发冷启 corpus → 每页 build_doc 只跑一次（无锁时会 N×，决策P5.4-2）。"""
    import guanlan.search as search_mod

    root = _kb(tmp_path)
    wiki = root / "wiki"
    for i in range(15):
        _page(wiki, f"entities/P{i}.md", title=f"P{i}", body=f"去中心化金融 第{i}页。")
    cache = CorpusCache()
    builds: list = []
    real_build = search_mod.build_doc
    monkeypatch.setattr(
        search_mod, "build_doc", lambda p, *, root: (builds.append(p), real_build(p, root=root))[1]
    )
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda _: cache.corpus(wiki), range(8)))
    # 每页恰建一次（共 15 页）；无锁时 8 路并发会重复 build。
    assert len(builds) == 15 and len(set(builds)) == 15
