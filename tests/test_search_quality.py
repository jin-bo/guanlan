"""检索质量回放 fixture（零 LLM，见 docs/backlog/notes/未来工作计划-反向评审收口.md 轨 A / gbrain §6）。

**这是回归闸，不是单测**：`tests/test_search.py` 逐一钉死检索内核的**机制**（分词、字段加权、
backlink 重排、snippet、容错）；本文件不碰机制，而是用一组**确定性的「query → 期望命中页」黄金集**
跑在一个**固定的、贴近真实领域 wiki 的语料**上，断言**端到端检索质量**（P@1 / recall@k / MRR）不回退。

借的是 gbrain `eval capture/replay` 的**纪律**（录真实 query+期望命中 → A/B 测代码改动是否压低检索
质量），**不**借其运行时 `eval` 子命令 + 持久 capture（过重）：语料是committed 在本模块里的纯数据
（`CORPUS`），黄金集是 `GOLDEN`，跑的是产线冷算路径 `search_pages`（**带 P5.3 backlink 重排**）。
确定性、零 LLM、零写盘、可 CI——任何把「对的页挤下去」的改动（改 BM25 参数 / 分词 / boost 权重）
都会让某条 `GOLDEN` 的 primary 掉出 rank-1 或拉低聚合阈值而被本闸挡住。

**语料**：一个 16 页的「大语言模型」领域微 wiki（concepts/entities/sources/syntheses 四目录 +
config 三件套），页间有真实 `[[wikilink]]` 交叉引用（喂 backlink 文档先验）、aliases（喂字段加权）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from guanlan.search import build_corpus, score, search_pages

# ---------------------------------------------------------------------------
# 语料（committed fixture）：16 页大语言模型领域 wiki。每页 (相对 wiki/ 路径, 标题, 类型,
# 别名, 正文)。正文里的 [[wikilink]] 构成入链图（喂 P5.3 backlink 文档先验），别名喂字段加权。
# 每条 GOLDEN 的 primary 页都把 query 词放进**标题**，故字段加权下 rank-1 稳健。
# ---------------------------------------------------------------------------

CORPUS: list[dict] = [
    {
        "path": "concepts/attention.md",
        "title": "注意力机制",
        "type": "concept",
        "aliases": ["注意力", "Attention"],
        "body": (
            "注意力机制是 [[transformer]] 架构的核心，它让模型在生成每个 token 时"
            "动态地关注输入序列中的相关位置。\n"
            "注意力机制通过 query、key、value 三组向量计算加权和，权重反映 token 之间的相关性。\n"
            "[[multi-head-attention]] 是注意力机制的并行扩展。"
        ),
    },
    {
        "path": "concepts/multi-head-attention.md",
        "title": "多头注意力",
        "type": "concept",
        "aliases": ["多头注意力机制", "MHA"],
        "body": (
            "多头注意力把注意力机制拆成多个并行的注意力头，"
            "每个头在不同的子空间学习不同的关注模式。\n"
            "多头注意力是 [[transformer]] 中 [[attention]] 的标准实现，"
            "最后把各头的输出拼接并做线性变换。"
        ),
    },
    {
        "path": "concepts/transformer.md",
        "title": "Transformer 架构",
        "type": "concept",
        "aliases": ["Transformer", "变换器"],
        "body": (
            "Transformer 架构完全基于 [[attention]]，摒弃了循环与卷积结构。\n"
            "它由编码器与解码器堆叠而成，每层包含 [[multi-head-attention]] 与前馈网络，"
            "并配合 [[positional-encoding]] 注入顺序信息。\n"
            "Transformer 架构是现代大语言模型的基础。"
        ),
    },
    {
        "path": "concepts/positional-encoding.md",
        "title": "位置编码",
        "type": "concept",
        "aliases": ["位置嵌入", "Positional Encoding"],
        "body": (
            "位置编码为 [[transformer]] 注入 token 在序列中的顺序信息，"
            "因为自注意力本身对位置不敏感。\n"
            "常见的位置编码包括正弦位置编码与可学习的位置嵌入。"
        ),
    },
    {
        "path": "concepts/embedding.md",
        "title": "词嵌入",
        "type": "concept",
        "aliases": ["词向量", "Embedding", "嵌入"],
        "body": (
            "词嵌入把离散的词映射为稠密的实数向量，使语义相近的词在向量空间中距离更近。\n"
            "词嵌入是 [[transformer]] 输入层的第一步，把 token 转成向量表示。"
        ),
    },
    {
        "path": "concepts/pretraining.md",
        "title": "预训练",
        "type": "concept",
        "aliases": ["Pretraining"],
        "body": (
            "预训练在大规模无标注语料上以自监督方式训练模型，学习通用的语言表示。\n"
            "预训练得到的模型可以通过 [[finetuning]] 适配到具体的下游任务。\n"
            "常见的预训练目标包括自回归语言建模与掩码语言建模。"
        ),
    },
    {
        "path": "concepts/finetuning.md",
        "title": "微调",
        "type": "concept",
        "aliases": ["Fine-tuning", "精调"],
        "body": (
            "微调在 [[pretraining]] 得到的模型基础上，用下游任务的标注数据继续训练，"
            "使模型适配具体任务。\n"
            "[[rlhf]] 是一种特殊的微调方式，用人类偏好信号对齐模型行为。"
        ),
    },
    {
        "path": "concepts/rlhf.md",
        "title": "基于人类反馈的强化学习",
        "type": "concept",
        "aliases": ["RLHF", "人类反馈强化学习"],
        "body": (
            "基于人类反馈的强化学习先用人类偏好数据训练一个奖励模型，"
            "再用强化学习优化语言模型以最大化奖励。\n"
            "它是 [[finetuning]] 阶段对齐大语言模型价值观与指令遵循能力的关键技术。"
        ),
    },
    {
        "path": "concepts/rag.md",
        "title": "检索增强生成",
        "type": "concept",
        "aliases": ["RAG", "检索增强"],
        "body": (
            "检索增强生成在生成答案之前，先从外部知识库检索与问题相关的文档片段，"
            "再把它们拼进上下文。\n"
            "检索通常依赖 [[embedding]] 把查询与文档映射到同一向量空间做相似度匹配。"
        ),
    },
    {
        "path": "concepts/prompt-engineering.md",
        "title": "提示工程",
        "type": "concept",
        "aliases": ["提示词工程", "Prompt"],
        "body": (
            "提示工程通过精心设计输入提示来引导大语言模型产生期望的输出，"
            "而无需改变模型参数。\n"
            "常见技巧包括少样本示例、思维链提示与角色设定。"
        ),
    },
    {
        "path": "concepts/context-window.md",
        "title": "上下文窗口",
        "type": "concept",
        "aliases": ["上下文长度", "Context Window"],
        "body": (
            "上下文窗口指大语言模型一次推理能够处理的最大 token 数量，"
            "受 [[attention]] 的计算复杂度约束。\n"
            "更长的上下文窗口让模型能容纳更多输入，但显存与计算开销随之增长。"
        ),
    },
    {
        "path": "concepts/tokenization.md",
        "title": "分词",
        "type": "concept",
        "aliases": ["Tokenization", "词元化"],
        "body": (
            "分词把原始文本切分成模型可处理的 token 序列，常用算法包括 BPE 与 WordPiece。\n"
            "分词的粒度直接影响词表大小与序列长度。"
        ),
    },
    {
        "path": "entities/gpt-4.md",
        "title": "GPT-4",
        "type": "entity",
        "aliases": ["GPT4"],
        "body": (
            "GPT-4 是 OpenAI 发布的大语言模型，基于 [[transformer]] 解码器架构，"
            "并通过 [[rlhf]] 进行对齐。\n"
            "GPT-4 支持多模态输入，在多项基准上表现优异。"
        ),
    },
    {
        "path": "entities/bert.md",
        "title": "BERT",
        "type": "entity",
        "aliases": ["Bert"],
        "body": (
            "BERT 是基于 [[transformer]] 编码器的双向语言模型，"
            "通过掩码语言建模进行预训练。\n"
            "BERT 在问答与文本分类等下游任务上通过 [[finetuning]] 微调取得突破。"
        ),
    },
    {
        "path": "sources/attention-paper.md",
        "title": "Attention Is All You Need",
        "type": "source",
        "aliases": [],
        "body": (
            "本文提出了 [[transformer]] 架构，完全依赖 [[attention]] 机制建模序列依赖，"
            "摒弃循环网络。\n"
            "论文展示了自注意力在机器翻译任务上的有效性。"
        ),
    },
    {
        "path": "syntheses/llm-overview.md",
        "title": "大语言模型综述",
        "type": "synthesis",
        "aliases": ["LLM 综述"],
        "body": (
            "本综述系统梳理大语言模型的关键技术：从 [[transformer]] 架构与 [[attention]] 机制，"
            "到 [[pretraining]]、[[finetuning]] 与 [[rlhf]] 的训练流程，"
            "再到 [[rag]] 检索增强与 [[prompt-engineering]] 应用层。\n"
            "我们讨论各技术之间的关系与演进脉络。"
        ),
    },
]


# ---------------------------------------------------------------------------
# 黄金集：每条 (query, primary, relevant)。primary = 该 query **唯一最相关**页，应稳居 rank-1；
# relevant = 该 query 应在 top-k 召回的相关页集合（含 primary），用于算 recall@k。
# ---------------------------------------------------------------------------


class Case:
    __slots__ = ("query", "primary", "relevant")

    def __init__(self, query: str, primary: str, relevant: set[str]):
        self.query = query
        self.primary = "wiki/" + primary
        self.relevant = {"wiki/" + r for r in relevant}


GOLDEN: list[Case] = [
    Case("注意力机制", "concepts/attention.md",
         {"concepts/attention.md", "concepts/multi-head-attention.md"}),
    Case("多头注意力", "concepts/multi-head-attention.md",
         {"concepts/multi-head-attention.md", "concepts/attention.md"}),
    Case("Transformer 架构", "concepts/transformer.md", {"concepts/transformer.md"}),
    Case("位置编码", "concepts/positional-encoding.md", {"concepts/positional-encoding.md"}),
    Case("词嵌入 向量", "concepts/embedding.md", {"concepts/embedding.md"}),
    Case("预训练", "concepts/pretraining.md",
         {"concepts/pretraining.md", "entities/bert.md"}),
    Case("微调", "concepts/finetuning.md", {"concepts/finetuning.md"}),
    Case("人类反馈 强化学习", "concepts/rlhf.md", {"concepts/rlhf.md"}),
    Case("检索增强生成", "concepts/rag.md", {"concepts/rag.md"}),
    Case("提示工程", "concepts/prompt-engineering.md", {"concepts/prompt-engineering.md"}),
    Case("上下文窗口", "concepts/context-window.md", {"concepts/context-window.md"}),
    Case("分词", "concepts/tokenization.md", {"concepts/tokenization.md"}),
    Case("GPT-4", "entities/gpt-4.md", {"entities/gpt-4.md"}),
    Case("BERT", "entities/bert.md", {"entities/bert.md"}),
]


_CONFIG = {"wiki/index.md", "wiki/log.md", "wiki/overview.md"}


def _render_page(spec: dict) -> str:
    alias_line = ""
    if spec["aliases"]:
        alias_line = "aliases: [" + ", ".join(f"'{a}'" for a in spec["aliases"]) + "]\n"
    return (
        f"---\ntitle: '{spec['title']}'\ntype: {spec['type']}\n{alias_line}---\n\n"
        f"{spec['body']}\n"
    )


def materialize(root: Path) -> Path:
    """把 committed `CORPUS` 落成磁盘上的 `wiki/`（含 config 三件套），返回 wiki 目录。"""
    wiki = root / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# 索引\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 时间线\n", encoding="utf-8")
    (wiki / "overview.md").write_text("# 综述\n", encoding="utf-8")
    for spec in CORPUS:
        p = wiki / spec["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_render_page(spec), encoding="utf-8")
    return wiki


# ---------- 指标 ----------


def precision_at_k(hit_pages: list[str], relevant: set[str], k: int) -> float:
    top = hit_pages[:k]
    return sum(1 for p in top if p in relevant) / k if top else 0.0


def recall_at_k(hit_pages: list[str], relevant: set[str], k: int) -> float:
    top = set(hit_pages[:k])
    return len(top & relevant) / len(relevant) if relevant else 0.0


@pytest.fixture(scope="module")
def wiki(tmp_path_factory) -> Path:
    """整张语料只落盘一次（只读跨用例复用）。返回 `wiki/` 目录，喂产线冷算 `search_pages`。"""
    return materialize(tmp_path_factory.mktemp("retrieval_corpus"))


@pytest.fixture(scope="module")
def corpus(wiki: Path):
    """语料 + 全库反链各建一次（只读复用），喂**直接调 `score`** 的对照用例：与 `search_pages`
    内部同口径（决策P5.3-3/4，已由 test_search.py 证字节等价），省掉逐用例重建语料+图。
    返回 `(docs, inlinks)`。需要走产线真入口的用例仍直接调 `search_pages`。"""
    return build_corpus(wiki), _inlinks(wiki)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


# ---------------------------------------------------------------------------
# 回归闸
# ---------------------------------------------------------------------------


def test_every_primary_ranks_first(wiki: Path):
    """核心闸：每条黄金 query 的 primary 页（题面命中、唯一最相关）稳居 **rank-1**。

    这是「重排把对的页挤下去」最直接的护栏——跑的是带 P5.3 backlink 重排的产线路径
    `search_pages`，任何把某个 primary 挤出 rank-1 的改动（BM25 参数 / 分词 / boost 权重）在此即败。
    """
    failures = []
    for c in GOLDEN:
        hits = search_pages(wiki, c.query, limit=10).hits
        if not hits or hits[0].page != c.primary:
            got = hits[0].page if hits else "(无命中)"
            failures.append(f"[{c.query}] 期望 rank-1={c.primary}，实际={got}")
    assert not failures, "primary 掉出 rank-1：\n" + "\n".join(failures)


def test_aggregate_pk_no_regression(wiki: Path):
    """聚合检索质量回放指标 dashboard（gbrain §6 的 P@k 回归闸）：当前语料下三项均满分、不得回退。

    三项各自的覆盖面（**非全冗余**）：`P@1`/`MRR` 与 `test_every_primary_ranks_first` 同源（primary
    rank-1），保留作为「回放指标」一目了然的看板；**`recall@2` 是唯一覆盖二级召回面的断言**——
    多相关页 case（「注意力机制」的多头注意力、「多头注意力」的注意力、「预训练」的 BERT）的次相关页
    当前都稳在 rank-2，故 floor 取 **k=2**（而非 k=3）：次相关页从 rank-2 掉到 rank-3 即被本闸抓住
    （k=3 会放过这一档退化，决策见 review）。
    """
    p_at_1, r_at_2, mrr = [], [], []
    for c in GOLDEN:
        pages = [h.page for h in search_pages(wiki, c.query, limit=10).hits]
        rank = pages.index(c.primary) + 1 if c.primary in pages else None
        p_at_1.append(precision_at_k(pages, {c.primary}, 1))
        r_at_2.append(recall_at_k(pages, c.relevant, 2))
        mrr.append(1.0 / rank if rank else 0.0)
    # 当前语料：三项满分。floor=1.0 即「不得低于当前满分质量」，任何质量回退都会跌破。
    assert _mean(p_at_1) == 1.0, f"mean P@1 回退到 {_mean(p_at_1):.3f}"
    assert _mean(r_at_2) == 1.0, f"mean recall@2 回退到 {_mean(r_at_2):.3f}"
    assert _mean(mrr) == 1.0, f"mean MRR 回退到 {_mean(mrr):.3f}"


def test_config_pages_never_recalled(wiki: Path):
    """config 三件套（index/log/overview）绝不进任何 query 的召回（与 iter_pages 排除相对照）。"""
    for c in GOLDEN:
        pages = {h.page for h in search_pages(wiki, c.query, limit=10).hits}
        assert not (pages & _CONFIG), f"[{c.query}] 召回了 config 页：{pages & _CONFIG}"


def test_backlink_rerank_lifts_hub(wiki: Path):
    """rerank 护栏有牙：枢纽页（多入链）在 boost 路径下被温和上浮，纯 BM25 下则不会。

    用「Transformer 架构」一例钉死：`attention.md`（5 入链）在带 boost 的产线路径排到
    `gpt-4.md`（0 入链）**之前**，而纯 BM25（inlinks=None）下两者次序**相反**——证明 P5.3
    文档先验确实在产线路径生效（移除/削弱 boost 即此断言失败）；同时 primary `transformer.md`
    在两条路径下都仍是 rank-1（boost 上浮枢纽、但绝不挤掉题面最相关页）。
    """
    q = "Transformer 架构"
    primary = "wiki/concepts/transformer.md"
    hub = "wiki/concepts/attention.md"
    leaf = "wiki/entities/gpt-4.md"

    boost = [h.page for h in search_pages(wiki, q, limit=10).hits]
    pure = [h.page for h in score(build_corpus(wiki), q, limit=10, inlinks=None).hits]

    assert boost[0] == primary and pure[0] == primary  # primary 两路都 rank-1
    # boost：枢纽 attention 在 gpt-4 之前；pure：相反。
    assert boost.index(hub) < boost.index(leaf), f"boost 未上浮枢纽：{boost[:4]}"
    assert pure.index(leaf) < pure.index(hub), f"pure 次序应相反：{pure[:4]}"


def test_boost_is_live_and_never_demotes_primary(corpus):
    """boost 方向的双重护栏（替代原「命中数 ≥」聚合断言——后者因所有 primary 题面命中、纯 BM25 下
    已全 rank-1 而恒为 14≥14、近乎空转）：

    (a) **boost 确实在动**：至少一条 query 的名次因 backlink 文档先验而与纯 BM25 不同——把
        `BACKLINK_WEIGHT` 置零 / 传 `inlinks=None` 则全相同、此断言即败（**有牙**，证 P5.3 非 no-op）；
    (b) **boost 绝不把对的页挤下去**：每条 query 的 primary 在 boost 路径名次**不劣于**纯 BM25
        （`rank_boost ≤ rank_pure`）——即便未来某 primary 在纯 BM25 下只到 rank-2，boost 也不再往下压
        （「温和上浮、不喧宾夺主」的诚实表述，决策P5.3-2）。配合 `test_backlink_rerank_lifts_hub`
        （证 boost 把枢纽页上浮到 leaf 之前）三者合围 P5.3 重排行为。
    """
    docs, inlinks = corpus
    reordered = 0
    demoted = []
    for c in GOLDEN:
        boost = [h.page for h in score(docs, c.query, limit=10, inlinks=inlinks).hits]
        pure = [h.page for h in score(docs, c.query, limit=10, inlinks=None).hits]
        if boost != pure:
            reordered += 1
        rb = boost.index(c.primary) + 1 if c.primary in boost else 10**6
        rp = pure.index(c.primary) + 1 if c.primary in pure else 10**6
        if rb > rp:
            demoted.append(f"[{c.query}] boost rank={rb} > pure rank={rp}")
    assert reordered > 0, "boost 对全部 query 都是 no-op：backlink 文档先验未生效"
    assert not demoted, "boost 把 primary 压到纯 BM25 之下：\n" + "\n".join(demoted)


def test_quality_replay_byte_stable(wiki: Path, tmp_path: Path):
    """回放确定性：把同一 `CORPUS` **独立另落一份盘**（不同临时目录、各自重建语料+图），断言两份
    检索的 (页, 分数) 名次逐字节一致（决策P5.0-5 / P5.3-7）。

    比「同进程连跑两次」更有牙：纯函数同进程重放恒等、试不出东西；换独立目录会暴露任何依赖
    `rglob` 文件序 / dict 构建序 / 路径前缀的非确定性（语料须可从 markdown 幂等重建）。
    """
    wiki2 = materialize(tmp_path)  # 同 CORPUS、独立落盘、独立 build_corpus + build_graph。
    for c in GOLDEN:
        r1 = [(h.page, h.score) for h in search_pages(wiki, c.query, limit=10).hits]
        r2 = [(h.page, h.score) for h in search_pages(wiki2, c.query, limit=10).hits]
        assert r1 == r2, f"[{c.query}] 检索不确定/不稳定（独立重建后名次/分数漂移）"


def test_corpus_and_golden_integrity(wiki: Path):
    """语料/黄金集自洽体检（守两件事，**非**"防 fixture 文件被删"——语料是本模块内常量 `CORPUS`、
    删条目两侧同缩、抓不到）：

    1. **落盘↔常量 round-trip**：`build_corpus` 召回的 content 页集 == `CORPUS` 声明集（不多不少、
       无 config 混入）——抓 `materialize` 写了 `CORPUS` 外的页、或某 `CORPUS` 路径落进 config-排除
       位（如顶层 `index.md`）而召回不到的口径错配。
    2. **黄金集引用完整**：每条 `GOLDEN` 的 primary / relevant 都真实存在于 `CORPUS`、且 primary ∈
       relevant——抓黄金集指向被删/改名的页而静默削弱本闸。
    """
    recalled = {d.page for d in build_corpus(wiki)}
    # 落盘的 content 页 == CORPUS 声明集（不多不少、无 config 混入）。
    declared = {"wiki/" + s["path"] for s in CORPUS}
    assert recalled == declared, f"语料漂移：{recalled ^ declared}"
    assert not (recalled & _CONFIG)
    # 每条黄金 case 的 primary/relevant 都真实存在于语料里。
    for c in GOLDEN:
        assert c.primary in declared, f"[{c.query}] primary {c.primary} 不在语料"
        assert c.relevant <= declared, f"[{c.query}] relevant 越界：{c.relevant - declared}"
        assert c.primary in c.relevant  # primary 必属相关集


def _inlinks(wiki: Path) -> dict[str, int]:
    """语料反链（喂 boost 对照）：复用 graph 解析口径，与 search_pages 内部同源（决策P5.3-3）。"""
    from guanlan.graph import build_graph, compute_backlinks

    return compute_backlinks(build_graph(wiki))
