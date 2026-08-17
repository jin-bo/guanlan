"""按名开页：`/page` 的零 LLM 内核（P4.22 §4，**平台无关**）。

本相位的地基，且它与卡片正交（决策P4.22-1）——卡片没通电，手打 `/page 甲实体` 也已经是个
有用的命令。三个纯函数，全部同步读盘，**调用方必须卸线程**（§4.3 卸线程通则）。

**一行都不自己写解析逻辑**：`resolve_owner(name, link_resolution_index(wiki))` 是 P3.8 确立的
**单一 owner 归口**（`check` / `graph` / `heal` / Web 全复用同一张表），自带「精确 stem →
别名 → 安全 fold variant」三段语义。IM 侧另写一套按名查找，等于给这个系统开第五个口径——
P3.8 的整个动机就是消灭这种漂移。

**路径穿越在这个设计里不是"挡住了"，而是结构上不可达**：`resolve_owner` 返回的是**索引表里
的值**（由 `wiki.rglob("*.md")` 生成），`../` 之类根本进不了表。`tests/test_im.py` 仍有一条
`/page ../../etc/passwd` 的用例，守的是"日后有人图省事改成 `wiki / name` 拼路径"。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..pages import (
    link_resolution_index,
    link_stem,
    load_page,
    page_title,
    page_type,
    resolve_owner,
)
from .reply import extract_wikilinks

# 参数长度上限：页面名不可能更长。**这是全仓唯一的一份语义口径**（★ 决策P4.22-18）——
# 手打与点击共用同一次 `validate_page_arg` 调用，适配器那侧只留一个信封级的卫生上界
# （见 `adapters/feishu.MAX_CALLBACK_COMMAND`），量的是整条命令串、与"参数多长算合法"无关。
# 两处各量一次的下场是：页面名落在 194–200 字符时**手打能开、点按钮被丢**。
MAX_PAGE_ARG = 200


def validate_page_arg(arg: str) -> str | None:
    """`/page` 参数的**唯一**语义校验器。返回归一后的名字；不合法 → `None`。

    **输入一律不可信**（决策P4.22-8，P4.11 信任边界的同款口径）：参数有两个来源——用户手打、
    卡片回调回传——**两者走同一条校验**，绝不因为"回调是我们自己发出去的卡片回来的"就开后门。
    回传串经飞书服务端往返，是**外部输入**。
    """
    name = arg.strip()
    if not name or len(name) > MAX_PAGE_ARG:
        return None
    if any(ch < " " or ch == "\x7f" for ch in name):
        # 控制字符与换行：页面名里出现它们只可能是构造出来的。
        return None
    return name


@dataclass(frozen=True)
class PageView:
    """一页的**可发送形态** + 它内部**能解析到页**的引用（决策P4.22-25）。

    `links` 是**解析键 → 拥有页**的映射（键用 `link_stem` 归一），不是名字集合：调用方要拿它去和
    "实际送达的正文里出现了哪些引用"求交集，而两侧的键必须同源（同一个 `link_stem`）才对得上；
    **值**（owner）则用来按页去重——`[[甲实体]]` 与它的别名 `[[甲方]]` 解析键不同、拥有页却是同一张，
    只按键去重会出两个指向同一页的按钮（见 `filter_resolvable`）。

    **为什么把引用算在这里、而不是发完再算一遍**：`link_resolution_index` 要遍历 `wiki/` 把每页
    frontmatter 都 YAML 解析一遍，是全库级开销——P3.8 那个 `loaded=` 参数的存在就是为了消掉
    `build_graph` 里"建节点解析一遍、建解析表又解析一遍"的双解析。`/page` 号称毫秒级零 LLM，
    不该在同一次调用里把这张表建两次。
    """

    text: str  # 已组装、可直接发出的正文
    links: Mapping[str, str]  # 页内可解析引用：解析键 → 拥有页（已排除本页自身）


def open_page(kb: Path, name: str) -> PageView | None:
    """名字 → `PageView`；未命中 → `None`。**同步读盘，调用方须卸线程。**

    输出契约（§4.2）::

        甲实体　（entity）
        wiki/entities/甲实体.md

        <正文，已剥掉 frontmatter>

    - **剥 frontmatter**：`load_page()` 已经把 `(meta, body)` 分好，直接用 `body`。YAML 头对
      IM 用户是噪音。
    - **首行给标题 + 页型 + 相对路径**：与 `/search` 的结果行口径一致（那里已经在显示
      `hit['page']`）。相对路径无害且有用；**绝不显示绝对路径**——那是本机信息，且会经平台
      服务端往返进日志。
    - **一次建表把引用也算完**：解析表建一次，既用来开这一页、也用来判页内引用哪些解析得到。
    """
    idx = link_resolution_index(kb / "wiki")  # ← 本次调用**唯一**的一次全库解析
    rel = resolve_owner(name, idx)
    if rel is None:
        return None
    path = kb / rel
    if not path.is_file():  # 索引与磁盘之间的竞态（`reindex` 的老问题）：当作未命中
        return None
    meta, body = load_page(path)
    head = f"{page_title(meta, path.stem)}　（{page_type(meta)}）\n{rel}"
    body = body.strip()
    links: dict[str, str] = {}
    for cite in extract_wikilinks(body):
        # **排除指向本页自身的引用**（含经别名绕回来的）：一个"打开你正在看的这一页"的按钮
        # 是纯噪音。注意判据是**解析到的 owner**、不是名字——`[[甲方]]` 与 `[[甲实体]]` 可以是
        # 同一页，只比名字会漏掉别名那一路。
        owner = resolve_owner(cite, idx)
        if owner is not None and owner != rel:
            links.setdefault(link_stem(cite), owner)
    return PageView(text=f"{head}\n\n{body}" if body else head, links=links)


def _dedupe_by_owner(pairs: Sequence[tuple[str, str | None]]) -> list[str]:
    """`(名字, 拥有页)` 序列 → 能解析且**每页只留首次出现**的那些名字（保序）。

    ★ **按拥有页去重、不是按名字**：`extract_wikilinks` 的去重键是 `link_stem`，而**同一页有多个
    合法写法**——别名（`[[甲方]]` vs `[[甲实体]]`，P3.1）与 fold variant（`[[multi_head_attention]]`
    vs `[[multi-head-attention]]`，P3.8）的 `link_stem` 各不相同、拥有页却是同一张。只按名字去重
    的下场是：一句同时提到别名与本名的答案，出**两个点开同一页的按钮**——既是噪音，又白占
    `max_actions` 的名额、还把"另有 N 个"的告示算多。
    """
    out: list[str] = []
    seen: set[str] = set()
    for name, owner in pairs:
        if owner is None or owner in seen:
            continue
        seen.add(owner)
        out.append(name)
    return out


def filter_resolvable(names: Sequence[str], links: Mapping[str, str]) -> list[str]:
    """按**已算好的解析键→拥有页表**过滤引用名（保序、按页去重，**零盘 IO**）。

    `/page` 用它替代第二次 `resolve_many`：页内引用在 `open_page` 里就判过了，发完正文之后要做的
    只是"再和**实际送达**的那几片求一次交集"（决策P4.22-13 对页面输出同样成立），那是纯字符串活儿。
    """
    return _dedupe_by_owner([(n, links.get(link_stem(n))) for n in names])


def resolve_many(kb: Path, names: Sequence[str]) -> list[str]:
    """批量解析：回其中**能解析到页**的那些名字（保序、按页去重）。**同步读盘，调用方须卸线程。**

    供 `Delivery._offer_actions` 用——**出按钮前先解析一次**（决策P4.22-3）：断链的
    `[[丙实体]]` 出个按钮、点了回"没有找到"，正是决策P4.21-76 判据里"承诺一个不存在的东西"
    的翻版。一次建表、批量过滤，故代价与开一页相同。
    """
    idx = link_resolution_index(kb / "wiki")
    return _dedupe_by_owner([(n, resolve_owner(n, idx)) for n in names])
