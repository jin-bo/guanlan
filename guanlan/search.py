"""确定性整页召回 search（P5.0，见 docs/P5.0-检索层.md）。**零 LLM、零依赖、零写盘。**

`guanlan search "<query>"`：对 `wiki/` 下 content 页做 BM25 + CJK 2-gram 召回，按分数降序打印
top-N 候选页 + 确定性片段。是 query / skill 的**召回前端**（给候选 + 片段），**不综合、不引 LLM**
——综合仍由 Agentao 走带 `[[引用]]` 的 query（决策P5.0-8）。

设计要点：

- **分词单一归口 `tokenize`**：CJK 2-gram（钉死码点谓词，决策P5.0-18）+ 非 CJK `[a-z0-9]+` 词，
  query 与文档同函数（决策P5.0-3）。
- **BM25F-lite 字段加权**：title/alias 命中只抬 `tf`（×3 / ×2），**不进 `dl`/`avgdl`**——避免长度
  归一化反噬核心实体页（决策P5.0-17）；字段进**召回面**、参与得分，非仅排序（决策P5.0-23）。
- **绕开 PyYAML**：body 走 `split_frontmatter`（零 YAML），title/aliases/type 走轻量行扫
  （最大努力、可近似，决策P5.0-13）；读取沿用 `errors="replace"` 容错（决策P5.0-16）。
- **确定性**：排序键 `(-round(score,6), page)`，无时间戳/随机/盘上派生——同 query 同 wiki 字节稳定
  （决策P5.0-5）。CLI 文本 6 位定点补零、JSON `score` 保 number 只承诺 round 到 6 位（决策P5.0-20）。
- **「建语料 / 打分」分立**：`build_corpus`（昂贵：读盘+分词）与 `score`（廉价）分立，长驻进程可经
  `CorpusCache`（进程内 mtime memo，线程安全，决策P5.0-14/19）只重建变更页；CLI 走冷算
  `search_pages`，恒为等价权威。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, GuanlanError
from .pages import iter_pages, split_frontmatter
from .paths import require_kb_root

__all__ = [
    "SearchHit",
    "SearchResult",
    "DocBag",
    "tokenize",
    "build_doc",
    "build_corpus",
    "score",
    "search_pages",
    "search_result_dict",
    "CorpusCache",
    "run_search",
    "search_entrypoint",
    "main",
]

# BM25 参数（标准取值）。
K1 = 1.5
B = 0.75
# 字段 boost：title/alias 命中按倍数计入 tf，但**不**进 dl/avgdl（决策P5.0-17）。
TITLE_BOOST = 3
ALIAS_BOOST = 2
# 片段窗口宽度（约 140 字符，决策内 §3.4）。
SNIPPET_WIDTH = 140

# CJK 码点谓词（钉死最小够用集，决策P5.0-18）：统一表意基本区 + 扩展 A + 兼容表意。
# **不含**假名/全角符号/Bopomofo；扩展 B+ 星文字按需不纳入（命中极罕见）。
_CJK = r"一-鿿㐀-䶿豈-﫿"
_CJK_RUN_RE = re.compile(f"[{_CJK}]+")
_CJK_CHAR_RE = re.compile(f"[{_CJK}]")
# 非 CJK 段取词：小写化后取 `[a-z0-9]+`（重音拉丁落在外、被丢，正规化属 E1，决策P5.0-18）。
_WORD_RE = re.compile(r"[a-z0-9]+")

# 轻量 frontmatter 行扫（仅 search 用的 recall 近似，决策P5.0-13；不冒充 load_page 归口）。
_TITLE_RE = re.compile(r"^title\s*:\s*(.*)$")
_TYPE_RE = re.compile(r"^type\s*:\s*(.*)$")
_ALIASES_RE = re.compile(r"^aliases\s*:\s*(.*)$")


def positive_int(value: str) -> int:
    """argparse 类型：仅接受 ≥ 1 的整数（`--limit` 用，挡 0/负值的静默截断）。

    本地一份而非从 `heal` 借——search 是零基建、面向 web/mcp 复用的纯检索核，刻意不在 import
    期把 Agentao runtime（heal→runtime）拖进来。CLI 接线（cli.py）仍复用 heal 的同名校验。
    """
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须是整数：{value}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"必须是 ≥ 1 的整数：{value}")
    return n


def tokenize(text: str) -> list[str]:
    """分词**单一归口**：CJK 连续段走 2-gram（段长 1 退化 1-gram），非 CJK 段走 `[a-z0-9]+` 词。

    query 与文档同函数（决策P5.0-3），故 `[[别名]]`、混排（`L2 扩容`）两侧切法一致。对同一输入
    跨调用字节一致（无随机、无状态）。对齐 DESIGN §4.5「2-gram 滑窗」。
    """
    tokens: list[str] = []
    pos = 0
    for m in _CJK_RUN_RE.finditer(text):
        if m.start() > pos:  # CJK 段之前的非 CJK 文本。
            tokens.extend(_WORD_RE.findall(text[pos : m.start()].lower()))
        seg = m.group()
        if len(seg) == 1:
            tokens.append(seg)  # 单字退化 1-gram（保「李」这类单字可召回）。
        else:
            tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))
        pos = m.end()
    if pos < len(text):  # 尾部非 CJK 文本。
        tokens.extend(_WORD_RE.findall(text[pos:].lower()))
    return tokens


def _strip_comment(value: str) -> str:
    """去掉 YAML 行内注释（`#` 前需空白或行首，引号内的 `#` 不算，最大努力）。

    约定模板里 `aliases: [] # 可选…` / `type: entity # 或 concept` 都带行内注释——不剥掉，注释词会
    被当作标量/别名灌进 `tf`、误召回（决策P5.0-13 的行扫近似须挡住这一常见样式）。
    """
    in_single = in_double = False
    for i, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and (i == 0 or value[i - 1].isspace()):
            return value[:i].rstrip()
    return value


def _strip_scalar(value: str) -> str:
    """剥 YAML 标量的行内注释 + 成对引号 + 首尾空白（轻量行扫用，最大努力）。"""
    value = _strip_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _scan_scalar(block: str | None, pattern: re.Pattern[str], default: str) -> str:
    """行扫一个顶层标量字段（`title:` / `type:`）；缺/坏/空 → default。"""
    if block:
        for line in block.splitlines():
            m = pattern.match(line)
            if m:
                value = _strip_scalar(m.group(1))
                if value:
                    return value
    return default


def _scan_aliases(block: str | None) -> list[str]:
    """行扫 frontmatter `aliases`：支持单行 flow `[a, b]` 与块列表 `- a`（决策P5.0-13）。

    最大努力、可近似：罕见花式 YAML（多行折叠标量/锚点）扫不全就退化为空——漏一个别名至多略降
    该页召回（非正确性问题），frontmatter 正确性归口仍是 `check`。
    """
    if not block:
        return []
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = _ALIASES_RE.match(line)
        if not m:
            continue
        rest = _strip_comment(m.group(1)).strip()  # 先剥行内注释（`[] # 可选`）。
        if rest:  # 同行 flow 列表 [a, b] 或单标量。
            if rest.startswith("[") and rest.endswith("]"):
                parts = (_strip_scalar(x) for x in rest[1:-1].split(","))
                return [p for p in parts if p]
            value = _strip_scalar(rest)
            return [value] if value else []
        # 块列表：紧随其后的 `- x` 行。**空行或下一个键即止**（不跨空行误并入别的字段列表）；
        # **空列表项 `- ` 跳过而非中断**（避免漏掉其后的别名，超出"漏一个别名"的容忍）。
        out: list[str] = []
        for follow in lines[i + 1 :]:
            stripped = follow.strip()
            if stripped == "-" or stripped.startswith("- "):
                value = _strip_scalar(stripped[1:].strip())
                if value:
                    out.append(value)
                continue
            break  # 空行 / 下一个键 → 块列表结束。
        return out
    return []


@dataclass(frozen=True)
class SearchHit:
    """一条召回：`page` 相对**库根** posix（与 graph.Node.path 同口径，决策P5.0-22）。"""

    page: str
    title: str
    type: str
    score: float  # 已 round(…,6)；CLI 文本补零、JSON 保 number（决策P5.0-20）。
    snippet: str  # 确定性正文片段；字段-only 命中退化首窗、body 空时 ""（决策P5.0-24）。


@dataclass(frozen=True)
class SearchResult:
    """检索回执：所有消费侧（CLI / JSON / 未来 web·mcp）从同一份生成（决策P5.0-10）。"""

    hits: list[SearchHit]
    pages_searched: int  # 参与打分的 content 页数（N=0 时为 0）。
    query: str


@dataclass(frozen=True)
class DocBag:
    """单页词袋：昂贵部分（读盘+分词）的产物，供 memo 缓存（§3.6）。"""

    page: str
    title: str
    type: str
    tf: dict[str, int] = field(default_factory=dict)  # body_cnt + 3×title + 2×alias（决策P5.0-17）。
    dl: int = 0  # 文档长度 = len(body_tokens)，**仅 body**，boost 不计入（决策P5.0-17）。
    body: str = ""  # 原始正文，供 snippet 原文切窗（§3.4）。
    mtime_ns: int = 0  # memo 失效键（§3.6）。
    size: int = 0


def build_doc(path: Path, *, root: Path) -> DocBag:
    """建单页词袋：`split_frontmatter` 取 body（零 YAML）+ 轻量行扫 title/aliases/type + 分词。

    `page` = `path.relative_to(root).as_posix()`（root 由调用方传入，决策P5.0-22）。读取沿用
    `errors="replace"` 容错（决策P5.0-16），坏/缺 frontmatter 照常按 body + stem 索引、**绝不抛**。

    **先 stat 再读**：memo 失效键 `(mtime_ns,size)` 必须不晚于所读字节——否则"读后被改写"会让
    DocBag 持旧 body 却配新键，下次 `CorpusCache` 比键命中、永久复用陈旧内容。stat 在前则最坏只是
    键偏旧、下次 stat 必不同而重建，绝不永久污染（决策P5.0-14 的"冷算恒权威"在此也是兜底）。
    """
    stat = path.stat()
    text = path.read_text(encoding="utf-8", errors="replace")
    block, body = split_frontmatter(text)
    stem = path.stem
    title = _scan_scalar(block, _TITLE_RE, stem)
    type_ = _scan_scalar(block, _TYPE_RE, "unknown")
    aliases = _scan_aliases(block)

    body_tokens = tokenize(body)
    tf: dict[str, int] = {}
    for tok in body_tokens:
        tf[tok] = tf.get(tok, 0) + 1
    for tok in tokenize(title):
        tf[tok] = tf.get(tok, 0) + TITLE_BOOST
    for alias in aliases:
        for tok in tokenize(alias):
            tf[tok] = tf.get(tok, 0) + ALIAS_BOOST

    return DocBag(
        page=path.relative_to(root).as_posix(),
        title=title,
        type=type_,
        tf=tf,
        dl=len(body_tokens),  # 仅 body，boost 不计入。
        body=body,
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
    )


def build_corpus(wiki: Path) -> list[DocBag]:
    """遍历 `wiki/` 非 config 页建语料（**昂贵**：每页读盘+分词）。`root = wiki.parent`。"""
    wiki = Path(wiki)
    root = wiki.parent
    return [build_doc(p, root=root) for p in iter_pages(wiki)]


def _locate(body: str, token: str) -> int | None:
    """在原文 body 上定位 token 的最小命中下标（大小写不敏感、带词边界，决策P5.0-11）。"""
    if _CJK_CHAR_RE.match(token):  # CJK token：表面即那 1~2 字，直接 find。
        idx = body.find(token)
        return idx if idx >= 0 else None
    # 非 CJK token：带词边界匹配，与 tokenizer `[a-z0-9]+` 整词口径对齐（否则 `ai` 误命中 `said`）。
    m = re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", body, re.IGNORECASE)
    return m.start() if m else None


def _snippet(body: str, query_tokens: list[str]) -> str:
    """确定性片段：取各 query token 在原文最小命中下标处约 140 字符窗，折叠空白（§3.4）。

    字段-only 命中（query 仅中标题/别名、正文无该表面串）→ body 定位不到 → 退化取正文首窗；
    **snippet 永远只从 body 取**，body 为空时首窗即 `""`（合法结果，绝不回填标题/别名，决策P5.0-24）。
    """
    if not body:
        return ""
    min_idx: int | None = None
    for tok in query_tokens:
        idx = _locate(body, tok)
        if idx is not None and (min_idx is None or idx < min_idx):
            min_idx = idx
    start = min_idx if min_idx is not None else 0  # 无表面命中 → 正文首窗。
    return " ".join(body[start : start + SNIPPET_WIDTH].split())


def score(docs: list[DocBag], query: str, *, limit: int) -> SearchResult:
    """BM25 + 字段加权打分 + 稳定排序 + 片段（**廉价**部分）。

    内核契约：`limit < 1` → `ValueError`（决策P5.0-15，web/mcp 直接复用、不靠 CLI 兜底）。
    零除守卫（决策P5.0-12）：N=0 与零-query-token 硬短路；`avgdl=0` 不短路而把长度归一化比值定义
    为 0（保字段召回），绝不除零。
    """
    if limit < 1:
        raise ValueError(f"limit 必须 ≥ 1：{limit}")

    n = len(docs)
    if n == 0:  # 无 content 页 → 无 corpus，自然短路。
        return SearchResult(hits=[], pages_searched=0, query=query)

    query_tokens = tokenize(query)
    if not query_tokens:  # 纯标点/空白 → 无 token，回空（非短路、真无可查）。
        return SearchResult(hits=[], pages_searched=n, query=query)

    # 排序 query 词去重，使浮点求和顺序跨进程稳定（确定性，决策P5.0-5）。
    q_terms = sorted(set(query_tokens))
    df = {t: sum(1 for d in docs if d.tf.get(t, 0) > 0) for t in q_terms}
    idf = {
        t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in q_terms
    }  # 非负 idf。
    avgdl = sum(d.dl for d in docs) / n  # avgdl=0（body 全空）时下方比值定义为 0。

    scored: list[tuple[DocBag, float]] = []
    for d in docs:
        s = 0.0
        for t in q_terms:
            tf = d.tf.get(t, 0)
            if tf <= 0:
                continue
            # avgdl=0 → 比值取 0 → 归一化因子退化为稳定有限值 (1-B)，绝不除零（决策P5.0-12）。
            ratio = d.dl / avgdl if avgdl > 0 else 0.0
            norm = 1 - B + B * ratio
            s += idf[t] * (tf * (K1 + 1)) / (tf + K1 * norm)
        # 用**舍入后**的分数做收录门槛，与排序/输出同口径——避免极小正分（<5e-7）被收录却显示
        # 为 [0.000000]/score:0.0（决策P5.0-20 的"定点舍入"贯穿收录与呈现）。
        rounded = round(s, 6)
        if rounded > 0:
            scored.append((d, rounded))

    scored.sort(key=lambda ds: (-ds[1], ds[0].page))  # 分数降序、path 升序 tie-break。
    hits = [
        SearchHit(
            page=d.page,
            title=d.title,
            type=d.type,
            score=s,
            snippet=_snippet(d.body, query_tokens),
        )
        for d, s in scored[:limit]
    ]
    return SearchResult(hits=hits, pages_searched=n, query=query)


def search_pages(wiki: Path, query: str, *, limit: int = 10) -> SearchResult:
    """无状态冷算（CLI 路径、恒为等价权威）：建语料 → 打分。limit 校验归口在 `score`。"""
    return score(build_corpus(Path(wiki)), query, limit=limit)


def search_result_dict(result: SearchResult) -> dict:
    """成功回执的**字段 + 取整单一归口**（决策P5.1-4）：`SearchResult` → JSON 契约 dict。

    CLI `_render` 的 JSON 分支、Web `/api/search`、未来 MCP `search` 工具**三处共用本函数**，
    杜绝字段名/取整逻辑各写一份后漂移（这正是 P5.0 用 `SearchResult` 单一归口的延伸）。返回
    **dict**——CLI 侧再 `json.dumps(..., indent=2)`、Web 侧交 FastAPI 序列化、工具侧
    `json.dumps(..., ensure_ascii=False)`：**字段/结构同形**，字节因各自序列化参数而异（非字节级）。
    `score` 字段保 number、只承诺 round 到 6 位（决策P5.0-20，不补尾零）。
    """
    return {
        "ok": True,
        "query": result.query,
        "pages_searched": result.pages_searched,
        "results": [
            {
                "page": h.page,
                "title": h.title,
                "type": h.type,
                "score": round(h.score, 6),  # number；JSON 不补尾零（决策P5.0-20）。
                "snippet": h.snippet,
            }
            for h in result.hits
        ],
    }


class CorpusCache:
    """长驻进程（web/mcp）专用**进程内** memo：按 (mtime_ns,size) 复用已建 DocBag、只重建变更页。

    **不落盘**、可幂等重建、与冷算 `search_pages` 字节等价（决策P5.0-14）。线程安全（决策P5.0-19）：
    `corpus()` 把**重活（glob + 全库 stat + build_doc）放在锁外**并发跑，`threading.Lock` 只护两段
    **纯字典**操作——拍桶快照、合并重建 + 剪枝（决策P5.1-8，收窄临界区）。代价是放弃「全程单锁」的
    双重-build 抑制：并发线程可能对同页各 build 一次、合并 last-writer-wins，但 (mtime_ns,size) 键保证
    下次 stat 必重建，最坏多算一次、绝不永久陈旧（与「冷算恒权威」兜底一致）。返回值只取「本调用 stat
    对齐的命中/新建 DocBag」，故仍与冷算字节等价、确定性不变。CLI 单进程单线程不用（新进程吃不到缓存）。

    **按库根分桶**：缓存先按 `root` 分到独立子表，再按相对库根 posix 路径索引。否则单个 cache 实例
    服务多个知识库时，两库同相对路径（如 `wiki/entities/A.md`）若 `(mtime_ns,size)` 偶合，会串用对方
    的 `DocBag`、返回错库内容（破坏冷算等价）；且单层表的剪枝会把别库的页一并误删。分桶后剪枝亦只在
    本库子表内进行，互不波及。
    """

    def __init__(self) -> None:
        # root posix → {相对库根 posix → DocBag}：按库根分桶，避免跨库串用/误剪（见类 docstring）。
        self._caches: dict[str, dict[str, DocBag]] = {}
        self._lock = threading.Lock()

    def corpus(self, wiki: Path) -> list[DocBag]:
        # **先绝对化再分桶**：相对路径（如不同 cwd 下的 `wiki`，其 parent 同为文本 `.`）会撞同一个桶，
        # 重新引入跨库串用；且 `relative_to` 对相对路径也会失败。`resolve` 后桶键稳定、相对键可算
        # （与 require_kb_root 的 `.resolve()` 归一一致；`page` 字段仍是相对库根，与冷算字节等价）。
        wiki = Path(wiki).resolve()
        root = wiki.parent
        bucket = root.as_posix()

        # ① 锁外：glob + 全库 stat（syscall + 算 key），原先全在锁内串行。**真正的热点不是 stat（实测
        # 仅占整次 ~9%）而是按页算相对库根 key**：`path.relative_to(root).as_posix()` 每页 ~11μs、万页
        # 占满整次调用 ~55%（纯 Python、受 GIL 串行，挪出锁也不并行）。故这里改用**去前缀**取 key——
        # path 必在 root 下（rglob of wiki），切掉 `root/` 前缀即相对库根 posix，与 `relative_to().as_posix()`
        # 字节一致但快 ~100×（实测 111ms→1ms，决策P5.1-8）。`os.sep→"/"` 在 POSIX 是空操作，保留以与
        # as_posix 跨平台同形。**前缀须含分隔符且适配锚点**：库根为 `/`（KB 直挂 `/wiki`）或 Windows 盘根
        # `C:/` 时 `root.as_posix()` 自带尾 `/`，不能再补一个——故按是否以 `/` 收尾决定补不补，再用
        # `removeprefix`（前缀不匹配则原样返回，永不像 `len()+1` 那样多吃一字节）。只攒 (key, path,
        # mtime_ns, size)，纯只读文件系统、不碰共享字典。
        root_posix = root.as_posix()
        prefix = root_posix if root_posix.endswith("/") else root_posix + "/"
        entries: list[tuple[str, Path, int, int]] = []
        for path in iter_pages(wiki):
            try:
                st = path.stat()
            except OSError:
                continue  # 遍历与 stat 之间被删 → 跳过。
            key = os.fspath(path).replace(os.sep, "/").removeprefix(prefix)
            entries.append((key, path, st.st_mtime_ns, st.st_size))

        # ② 锁内（短，纯字典）：拍一份本桶快照供锁外比键——浅拷贝，O(N) 引用、无 syscall。
        with self._lock:
            snapshot = dict(self._caches.setdefault(bucket, {}))

        # ③ 锁外：未变页直接复用快照里的 DocBag（保 `is` 复用契约）；miss/变更页 build_doc（O(变更)
        # 读盘+分词）。build 期被删/读失败 → 跳过该页（best-effort，下次自愈，不冒泡崩整次检索）。
        hits: dict[str, DocBag] = {}
        rebuilt: dict[str, DocBag] = {}
        for key, path, mtime_ns, size in entries:
            cached = snapshot.get(key)
            if cached is not None and cached.mtime_ns == mtime_ns and cached.size == size:
                hits[key] = cached
            else:
                try:
                    rebuilt[key] = build_doc(path, root=root)
                except OSError:
                    continue

        # 本次返回 = **本调用视图**：每页取「命中的快照 DocBag」或「本次新建的 DocBag」，二者都对齐本次
        # stat、绝不含陈旧版本——故与冷算 `search_pages` 字节等价（决策P5.0-14 不变）；按 iter_pages 排序
        # 序，确定性。**不**从合并后的共享 cache 取，避免并发线程的覆盖串进本次结果。
        docs = [
            hits[key] if key in hits else rebuilt[key]
            for key, *_ in entries
            if key in hits or key in rebuilt
        ]

        # ④ 锁内（短，纯字典）：把本次重建并入共享缓存 + 剪枝。**只剪本次快照里有、现已不在盘上的页**
        # （`snapshot - seen`），绝不动并发线程在我们拍快照后新加的页（旧版整桶 `set(cache)-seen` 剪枝会
        # 误删它们）。`update` last-writer-wins：与并发同页新建可能互相覆盖，但 (mtime_ns,size) 键保证下次
        # stat 必不同而重建，最坏多算一次、绝不永久陈旧。
        seen = {e[0] for e in entries}
        with self._lock:
            cache = self._caches.setdefault(bucket, {})
            cache.update(rebuilt)
            for stale in set(snapshot) - seen:
                cache.pop(stale, None)
        return docs


def _emit_error(message: str, *, json_output: bool, exit_code: int = EXIT_USAGE) -> int:
    """出错输出：`--json` 吐 `{"ok":false,"error":…}` 到 stdout；否则 stderr 纯文本（决策P5.0-21）。

    `exit_code` 透传调用方的码（如 `GuanlanError.exit_code`），与 reindex/graph 等兄弟命令的
    "返回 `exc.exit_code`"对齐；默认 `EXIT_USAGE`（本命令唯一会用的错误码，决策P5.0-6）。
    """
    if json_output:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    else:
        print(message, file=sys.stderr)
    return exit_code


def _render(result: SearchResult, *, json_output: bool) -> str:
    """渲染成功回执：JSON 契约（决策P5.0-10）或人类可读文本。两侧都从同一 `SearchResult` 出。"""
    if json_output:
        # JSON 体经 `search_result_dict` 单一归口（决策P5.1-4）：CLI/Web/MCP 三处不再各写映射。
        return json.dumps(search_result_dict(result), ensure_ascii=False, indent=2)

    if not result.hits:
        return f"（无命中）query「{result.query}」· 检索 {result.pages_searched} 页。"
    lines = [
        f"query「{result.query}」· 检索 {result.pages_searched} 页 · top {len(result.hits)}："
    ]
    for h in result.hits:
        lines.append(f"  [{h.score:.6f}] {h.page}  «{h.title}»（{h.type}）")  # CLI 文本补零。
        if h.snippet:
            lines.append(f"      {h.snippet}")
    return "\n".join(lines)


def run_search(query: str, *, root: Path, limit: int, json_output: bool) -> int:
    """`guanlan search` 的打印壳：冷算 `search_pages` → 渲染 → 退出码（决策P5.0-6，无新码）。"""
    if not tokenize(query):  # 空/纯空白/纯标点 query → EXIT_USAGE（§1）。
        return _emit_error("query 为空或无可检索词（纯空白/标点）。", json_output=json_output)
    try:
        result = search_pages(root / "wiki", query, limit=limit)
    except ValueError as exc:  # 内核 limit<1（CLI 已有 positive_int 第一道门）。
        return _emit_error(str(exc), json_output=json_output)
    print(_render(result, json_output=json_output))
    return EXIT_OK


def search_entrypoint(
    root_dir: str | Path, *, query: str, limit: int, json_output: bool
) -> int:
    """`guanlan search` 的单一落地：校验库根 → run_search。纯读、零 LLM、零写盘。"""
    try:
        root = require_kb_root(root_dir, writable=False)
    except GuanlanError as exc:
        return _emit_error(str(exc), json_output=json_output, exit_code=exc.exit_code)
    return run_search(query, root=root, limit=limit, json_output=json_output)


def main(argv: list[str] | None = None) -> int:
    """`python -m guanlan.search` 入口（与 `guanlan search` 共享 search_entrypoint）。"""
    parser = argparse.ArgumentParser(
        prog="python -m guanlan.search",
        description="确定性整页召回：BM25 + CJK 2-gram，按分数降序打印 top-N 页（零 LLM）。",
    )
    parser.add_argument("-C", "--dir", default=".", help="知识库根目录（默认当前目录）")
    parser.add_argument("query", help="检索词")
    parser.add_argument(
        "--limit", type=positive_int, default=10, help="召回条数（默认 10，须 ≥ 1）"
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 契约")
    args = parser.parse_args(argv)
    return search_entrypoint(
        args.dir, query=args.query, limit=args.limit, json_output=args.json
    )


if __name__ == "__main__":
    raise SystemExit(main())
