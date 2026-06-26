"""单页渲染（P4，见 docs/P4-Web宿主.md §6）。

`load_page` 取正文 → 若装了 `markdown`（`guanlan-wiki[web]` extra）渲染为 HTML，否则回退到
转义后的 `<pre>` 源码视图（**缺 extra 也能跑、只是不美观**）。`[[wikilink]]` 重写复用
`pages.WIKILINK_RE` + `pages.link_stem` + 全页面 stem 解析集——与 `check`/`graph` **同一口径**，
不另写解析。

重写经一个 markdown **行内处理器**实现（而非在源码上做字符串替换）：行内处理器在解析树上
运行，python-markdown 已用占位符保护了代码块/行内代码，故 `[[…]]` 出现在 ```code``` 里时
**不会**被改写——这正是字符串替换难以做对的地方（决策P4-3：复用既有口径、落到正确深度）。
"""

from __future__ import annotations

import html as html_lib
import re
import xml.etree.ElementTree as _etree  # stdlib，始终可用；只有 markdown 是可选 extra。
from collections import defaultdict
from html.parser import HTMLParser as _HTMLParser
from pathlib import Path

from ..pages import WIKILINK_RE, link_resolution_index, link_stem, load_page, resolve_owner

try:  # markdown 是 web extra 的一部分；缺失时回退 <pre> 源码视图（§6）。
    import markdown as _markdown
    from markdown.extensions import Extension as _Extension
    from markdown.inlinepatterns import InlineProcessor as _InlineProcessor
    from markdown.preprocessors import Preprocessor as _Preprocessor
    from markdown.treeprocessors import Treeprocessor as _Treeprocessor

    _HAS_MARKDOWN = True
except ImportError:  # pragma: no cover - 仅在未装 markdown 时走到
    _HAS_MARKDOWN = False

# URL 协议白名单：markdown 链接/图片即便原始 HTML 已被转义，仍可写出 [x](javascript:…) —— 渲染成
# <a href="javascript:…"> 注入 UI 后点击即同源执行脚本、能打本地写 API。只放行安全协议与相对链接。
_URL_SCHEME_RE = re.compile(r"^\s*([a-z][a-z0-9+.\-]*):", re.IGNORECASE)
_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})


def _is_safe_url(url: str) -> bool:
    """无协议（相对/锚点/路径）放行；有协议须 ∈ 白名单（拦下 javascript:/data:/vbscript: 等）。

    依次：① HTML 实体解码（markdown 会把 `&#106;avascript:` 原样留在 href，浏览器导航前会解码
    成 `javascript:`）；② 剥除所有 ASCII 控制符与空白（浏览器同样会去掉 URL 里的 Tab/换行）；
    ③ 再判协议——任一步缺失都会让 `java&#x09;script:` 之类绕过 scheme 检测、在浏览器里复原执行。
    """
    decoded = html_lib.unescape(url)
    cleaned = re.sub(r"[\x00-\x20]", "", decoded)
    match = _URL_SCHEME_RE.match(cleaned)
    return match is None or match.group(1).lower() in _ALLOWED_URL_SCHEMES


def _is_relative_local(url: str) -> bool:
    """`url` 是**库内相对路径**（可被改写指向本地图片端点）才 True：无协议、非绝对(`/`)、非协议相对
    (`//`)、非锚点(`#`)、非空。`data:`/`http(s):` 等带协议的（含被 safelink 失活成空串的）一律 False。
    """
    cleaned = re.sub(r"[\x00-\x20]", "", html_lib.unescape(url))
    if not cleaned or cleaned.startswith(("/", "#")):
        return False
    return _URL_SCHEME_RE.match(cleaned) is None


# ── HTML 表格白名单消毒（仅 raw 预览启用）─────────────────────────────────────────────
# mineru/marker 把合并单元格/多级表头的复杂表常emit成**原始 `<table>` HTML**（markdown pipe 表
# 表达不了）。默认 `_EscapeHtmlExtension` 把一切原始 HTML 转义成文本（决策P4-4 防 XSS），故这类表在
# 预览里成 `&lt;table&gt;…` 文本汤。这里**只对 `<table>…</table>` 片段**破例：用 stdlib `html.parser`
# 按 allowlist 重建——仅放行表格标签子集 + 经校验的安全属性，`script`/`style`/`on*`/`style=`/任意其它
# 标签一律剥除或转义（**不引第三方 sanitizer**，零新依赖）。其余原始 HTML 仍全转义、姿态不变。
_TABLE_BLOCK_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_TABLE_ALLOWED_TAGS = frozenset(
    {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col"}
)
_TABLE_VOID_TAGS = frozenset({"col"})  # 自闭合、无 </col>
_TABLE_DROP_SUBTREE = frozenset({"script", "style", "template"})  # 标签+其文本全丢
_TABLE_ATTR_ENUM = {  # 枚举型属性的合法值（其余值整条丢弃）
    "align": frozenset({"left", "right", "center", "justify", "char"}),
    "valign": frozenset({"top", "middle", "bottom", "baseline"}),
    "scope": frozenset({"row", "col", "rowgroup", "colgroup"}),
}
_TABLE_ATTR_NUM = frozenset({"colspan", "rowspan"})  # 仅数字
_TABLE_ALLOWED_ATTRS = frozenset(_TABLE_ATTR_ENUM) | _TABLE_ATTR_NUM


class _TableSanitizer(_HTMLParser):
    """把一段 `<table>` HTML 按 allowlist 重建为安全 HTML 串（丢弃一切非表格标签/危险属性）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # charref 自动解码进 data，由本类统一转义输出
        self.parts: list[str] = []
        self._drop_depth = 0  # >0 → 处于 script/style/template 子树，丢弃其 data
        self.emitted_table = False

    def _safe_attrs(self, attrs) -> str:
        out = []
        for key, value in attrs:
            key = key.lower()
            if value is None or key not in _TABLE_ALLOWED_ATTRS:
                continue  # 剥 on*/style/任意其它属性
            value = value.strip()
            if key in _TABLE_ATTR_NUM:
                if not value.isdigit():
                    continue
            elif value.lower() not in _TABLE_ATTR_ENUM[key]:
                continue
            out.append(f' {key}="{html_lib.escape(value, quote=True)}"')
        return "".join(out)

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        if self._drop_depth:
            if tag in _TABLE_DROP_SUBTREE:
                self._drop_depth += 1
            return
        if tag in _TABLE_DROP_SUBTREE:
            self._drop_depth = 1
            return
        if tag in _TABLE_ALLOWED_TAGS:
            self.parts.append(f"<{tag}{self._safe_attrs(attrs)}>")
            if tag == "table":
                self.emitted_table = True
        # 其它标签（含 <img>/<a>/<b>…）：丢弃标签本身、保留其子文本（递归继续）。

    def handle_startendtag(self, tag, attrs) -> None:
        tag = tag.lower()
        if not self._drop_depth and tag in _TABLE_ALLOWED_TAGS:
            self.parts.append(f"<{tag}{self._safe_attrs(attrs)}>")

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        if self._drop_depth:
            if tag in _TABLE_DROP_SUBTREE:
                self._drop_depth -= 1
            return
        if tag in _TABLE_ALLOWED_TAGS and tag not in _TABLE_VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data) -> None:
        if not self._drop_depth:
            self.parts.append(html_lib.escape(data))

    def handle_comment(self, data) -> None:
        pass  # 丢弃注释（杜绝 `<!--[if]>` 条件注释等绕过）


def _sanitize_table_html(raw: str) -> str | None:
    """把一段原始 `<table>` HTML 消毒为安全串；非法/未含 `<table>` → None（调用方原样保留→被转义）。"""
    parser = _TableSanitizer()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # html.parser 极少抛；防御性兜底，坏输入退回转义
        return None
    return "".join(parser.parts) if parser.emitted_table else None


def _wikilink_display(raw: str) -> str:
    """`[[target|alias]]` → alias；`[[target#anchor]]` → target（剥锚点）；其余取原文。"""
    if "|" in raw:
        return raw.split("|", 1)[1].strip()
    return raw.split("#", 1)[0].strip()


# 解析键(stem | 别名, 小写) → 相对库根 posix 路径，供 `[[wikilink]]` 解析为站内导航目标。归口于
# `pages.link_resolution_index`（stem→path ∪ 别名→拥有页 path，stem 优先；与 check/graph 同口径、
# 含 config 可链、别名可链，决策P3.1-6）。**故意每次渲染重建**（不缓存）：ingest 随时增删页面/别名，
# 每请求重扫保证新页/新别名立刻可点、删页立刻标灰——本地单用户下 O(N) 重扫的代价远小于缓存失效。
_stem_to_path = link_resolution_index


# ── raw/ 源引用 → 只读 raw 查看链接 ──────────────────────────────────────────────
# 正文里指向 `raw/<slug>.md` 的引用，联成前端 `a.rawlink[data-raw]`（点 → 右栏调 `/api/raw/file`
# 只读内联渲染那篇 raw 源）。与 `[[wikilink]]` 同纪律：**仅真实存在**的 raw 文件联链、其余标灰
# `span.rawlink.broken`（决策：raw/ 是只读不可变源，引用它的多是 `wiki/sources/` 摘要页 frontmatter/
# 正文，点链即回看原始素材）。四种写法共用下面的解析归口：
#   ① 裸路径串（plain text 行内，如「材料见 raw/某案例.md」）—— `_RawPathTreeprocessor` ②路
#   ② 行内 code（整段恰好是 `raw/<slug>.md`）—— `_CodePathLinkTreeprocessor` 内新分支
#   ③ `[[raw/<slug>]]`（wikilink 写法，可带/不带 .md）—— `_resolve_wikilink` 内 raw 前缀拦截
#   ④ markdown 链接 `[文字](raw/<slug>.md)` —— `_RawPathTreeprocessor` ①路（改写 <a href>）
# 裸路径串正则：`raw/` 须前邻非词/非斜杠（杜绝 `wiki/sources/raw/x.md`、`araw/x.md` 误命中）；
# slug 字符集对齐 rawio.raw_slug 的 `[\w.\-]`（`\w` 含 CJK）；尾界 `(?![\w\-])` 只挡「`.md` 后接更多
# 词字/连字符」（防把 `notes.mdx`/`x.mdown` 误截成 `.md`），但**允许后随 `.`**——故句末紧跟 ASCII 句号
# 的 `raw/x.md.` 仍联链、只把句号留在外面（评审修复：旧的 `(?![\w.\-])` 把句末句号也挡了）。
# `_RAW_REF_FULL_RE` 供「整段 code / 整段路径」fullmatch（无需边界，靠整体消费定界）。
_RAW_PATH_RE = re.compile(r"(?<![\w/])raw/[\w.\-]+?\.md(?![\w\-])")
_RAW_REF_FULL_RE = re.compile(r"raw/([\w.\-]+\.md)")

# rawlink 命中/缺失的样式归口（单点定义，杜绝多处 class/title 字面漂移，评审 reuse）。
_RAWLINK_BROKEN_TITLE = "raw/ 源不存在"


def _raw_name_index(root: Path) -> dict[str, str]:
    """`raw/*.md` 真实文件名解析表：**精确文件名键**（保大小写）→ 真实名，叠加**无冲突的小写兜底键**
    （大小写不敏感匹配，但精确优先、撞则不折叠——镜像 `pages.link_resolution_index` 的 fold 纪律）。

    **每次渲染重建**（同 `_stem_to_path` 纪律，不缓存）：投喂/晋级随时增删 raw 源，重扫保新源即时可点、
    删则标灰。只 `glob("*.md")` 取 raw/ **顶层**（`raw/images/` 等子树非源、天然不入），不读内容。
    精确优先使「`raw/Foo.md` 与 `raw/foo.md` 在大小写敏感盘上并存」时各按真实名解析、零串台（评审修复
    旧的纯小写键把二者撞成一个、`raw/foo.md` 误开 `Foo.md`）；小写兜底使「误写大小写」在大小写不敏感盘
    上仍命中（与 `_safe_raw_file` 行为一致）。
    """
    raw = root / "raw"
    exact: dict[str, str] = {}
    if raw.is_dir():
        for path in sorted(raw.glob("*.md")):
            if path.is_file():
                exact.setdefault(path.name, path.name)
    index = dict(exact)
    groups: dict[str, set[str]] = defaultdict(set)
    for name in exact:
        lower = name.lower()
        if lower not in exact:  # 小写键撞任意精确键 → 不新增（精确优先、撞则不折叠）
            groups[lower].add(name)
    for lower, names in groups.items():
        if len(names) == 1:  # 小写组唯一拥有者才作兜底键；≥2（真撞名）→ 丢弃、保歧义断链不猜
            index[lower] = next(iter(names))
    return index


def _raw_ref_basename(target: str) -> str | None:
    """把引用串解析为 `raw/` 顶层文件名 basename（**保大小写、保原扩展名**），非 raw/ 前缀 → None。

    剥 `|别名`/`#锚点`（兼容 `[[raw/x|看案例]]`）、归一斜杠；须 `raw/` 前缀（大小写不敏感）+ 非空尾段；
    取 basename（raw/ 顶层平铺，`raw/a/b.md` 退化取 `b.md`）。**不**补 `.md`、**不**小写——补 `.md` 与
    大小写兜底都交给 `_lookup_raw`（按存在性试 `<名>` 与 `<名>.md`）。如此含内部点的 stem（`raw/1.示例报告`）
    与非 `.md` 资产（`raw/images/x.png`）能被正确区分：前者补 `.md` 命中、后者两试皆空 → 不认领（评审修复
    旧的「无条件补 `.md`」把 `raw/report.pdf` 之类合法资产链接毁成断链 span）。
    """
    head = target.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/")
    prefix, sep, rest = head.partition("/")
    if not sep or prefix.lower() != "raw" or not rest:
        return None
    return rest.rsplit("/", 1)[-1] or None


def _lookup_raw(basename: str, raw_index: dict[str, str]) -> str | None:
    """basename（保大小写、可能带/不带 `.md`）→ 现存 raw 真实文件名 or None。

    依次试：精确 → 小写兜底；若不以 `.md` 结尾，再补 `.md` 试 精确 → 小写。覆盖「含内部点的 stem 省略
    `.md`」（`1.示例报告`→`1.示例报告.md`）与大小写不敏感匹配；非 `.md` 资产（`x.png`）两试皆空 → None。
    """
    cands = [basename] if basename.lower().endswith(".md") else [basename, basename + ".md"]
    for cand in cands:
        hit = raw_index.get(cand) or raw_index.get(cand.lower())
        if hit is not None:
            return hit
    return None


def _is_raw_md_form(basename: str) -> bool:
    """basename 形如 raw `.md` 源引用（以 `.md` 结尾、或无扩展名纯 stem）→ True；非 `.md` 扩展名
    （`.png`/`.pdf` 等资产）→ False。markdown 链接据此决定缺失时「标灰」还是「原样保留 href」——
    避免把指向 `raw/images/x.png` 之类合法资产链接误毁成断链 span。
    """
    return basename.lower().endswith(".md") or "." not in basename


def _raw_ref_in_code(content: str) -> str | None:
    """行内 code 整段（规整后）恰好是 `raw/<slug>.md` → 返回 basename（保大小写），否则 None。

    与裸路径串同口径但用 fullmatch（整段消费定界，无需前后边界）；命令/多 token 代码（`cat raw/x.md`）
    整体不等于 `raw/<slug>.md` 形 → 不误判（同 `_code_ref_target` 末步整体相等的安全纪律）。
    """
    m = _RAW_REF_FULL_RE.fullmatch(content.strip().replace("\\", "/"))
    return m.group(1) if m is not None else None


def _resolve_raw_link(
    basename: str | None, raw_index: dict[str, str], display: str
) -> tuple[str, dict[str, str], str] | None:
    """raw basename（保大小写）→ `(tag, attrib, display)`：命中现存源 `a.rawlink[data-raw]`、缺失
    `span.rawlink.broken`。`basename` 为 None（非 raw/ 前缀）→ None，交回调用方续判（不拦截）。
    """
    if basename is None:
        return None
    actual = _lookup_raw(basename, raw_index)
    if actual is not None:
        return "a", {"class": "rawlink", "data-raw": actual}, display
    return "span", {"class": "rawlink broken", "title": _RAWLINK_BROKEN_TITLE}, display


def _code_ref_target(content: str, stem_to_path: dict[str, str]) -> str | None:
    """`content` 是【对某现有页的忠实整体引用】才返回其相对路径，否则 None（行内 code 兜底用）。

    忠实 = `content`（规整后）**整体恰好等于**该页的某种合法写法之一：完整相对路径 /
    去 `wiki/` 前缀 / 纯文件名（均可带或不带 `.md`，大小写不敏感）。如此既放行**含空格的
    合法页名**（如 `Smart Tools 模块`），又挡住命令/多 token 代码（如 `cat wiki/x.md`）——后者经
    `link_stem` 取末段虽与某页共享 stem，但整体不等于该页的任何合法写法，故不误判为引用。

    **故意不接 `fold_stem`**（决策P3.8-7 边界，**只有 `[[wikilink]]` 走 fold**）：代码标识符里 `_`/`-`
    语义不同，折叠会把普通代码误链成页面引用。此处仍按精确 `link_stem` 查表 + **末步整体相等**判定。
    注意 `stem_to_path`（=`link_resolution_index`）现含 fold variant 键，故 `stem_to_path.get(stem)`
    **可能命中** variant——库内有 `foo_bar.md` 时它生成 `foo-bar` variant 键，行内 code `` `foo-bar` ``
    的 `stem` 恰是 `foo-bar`、会在 `.get` 命中那张页。真正拦住它的是**末步与页面真实写法（未折叠）整体
    相等**：`foo-bar`(连字符) 不等于 `foo_bar.md` 的任何合法形态 → 返回 None、不联链。**勿删末步相等
    判定**——它不是 `.get` 顺带兜住的，是本函数不 fold 的唯一安全闸。
    """
    norm = content.strip().replace("\\", "/")
    stem = link_stem(norm) if norm else ""
    target = stem_to_path.get(stem) if stem else None
    if target is None:
        return None
    # 两边各去掉可选 .md 再比：content 整体须恰好等于 target 的某种合法写法之一。
    want = norm[:-3].lower() if norm.lower().endswith(".md") else norm.lower()
    base = target[:-3].lower() if target.lower().endswith(".md") else target.lower()
    return target if want in (base, base.removeprefix("wiki/"), base.rsplit("/", 1)[-1]) else None


def _resolve_wikilink(
    raw: str, stem_to_path: dict[str, str], raw_index: dict[str, str]
) -> tuple[str, dict[str, str], str]:
    """把 `[[…]]` 内部 raw 解析为 `(tag, attrib, display)`：命中现有页 → `a.wikilink[data-page]`，
    断链 → `span.wikilink.broken`。行内 `[[…]]` 与 code 兜底两路共用，杜绝两处样式/类名漂移。

    **`raw/` 前缀优先拦截**：`[[raw/<slug>]]`（可带/不带 .md）指向 raw/ 顶层源——存在 →
    `a.rawlink[data-raw]`、缺失 → `span.rawlink.broken`（决策：raw/ 只读源引用单列一档、不落到 wiki
    页解析；`raw/` 是不会与 wiki 页 stem 撞的明确前缀，故拦截无歧义）。非 raw/ 前缀才续走 wiki 解析。

    P3.8：经 `resolve_owner`（精确 `link_stem` + fold 兜底）解析，与 check/graph/heal 同一张表、同
    口径——`[[multi_head_attention]]` 命中 `multi-head-attention.md`。**仅 `[[wikilink]]` 走 fold**；
    行内 code 引用（`_code_ref_target`）按精确路径/文件名判定、**不接 fold**（决策P3.8-7 边界）。
    """
    display = _wikilink_display(raw) or raw.strip()
    raw_link = _resolve_raw_link(_raw_ref_basename(raw), raw_index, display)
    if raw_link is not None:  # `[[raw/…]]`：raw 源引用单列一档（命中链 / 缺失标灰），不落 wiki 解析
        return raw_link
    target = resolve_owner(raw, stem_to_path)
    if target is not None:
        return "a", {"class": "wikilink", "data-page": target}, display
    return "span", {"class": "wikilink broken", "title": "无对应页面"}, display


def _retag(el, tag: str, attrib: dict[str, str], text: str) -> None:
    """就地把树上某元素改写成 `(tag, attrib, text)`（清掉旧属性，避免 copy-paste 漏 clear）。"""
    el.tag = tag
    el.attrib.clear()
    for key, value in attrib.items():
        el.set(key, value)
    el.text = text


def _code_wikilink_raw(content: str) -> str | None:
    """行内 code 的整段内容恰好是 `[[...]]` 时返回内部 raw，否则 None。"""
    match = WIKILINK_RE.fullmatch(content.strip())
    return match.group(1) if match is not None else None


if _HAS_MARKDOWN:

    class _EscapeHtmlExtension(_Extension):
        """禁用原始 HTML 透传——把页面里的 `<…>` 转义为文本，杜绝 XSS（决策P4-4 纵深防御）。

        wiki 页是 agent 生成的 **markdown**，本不该含原始 HTML；而被投喂的资料（未来 P5 的
        web clip 尤甚）可能夹带 `<img onerror=…>` 等载荷，经 `/api/page` 注入 UI 后能以同源
        身份调本地写/读 API。这是 python-markdown 官方推荐的"关原始 HTML"做法（注销其两个
        HTML 处理器），不引第三方 sanitizer。
        """

        def extendMarkdown(self, md) -> None:  # noqa: N802 (markdown API 命名)
            md.preprocessors.deregister("html_block")
            md.inlinePatterns.deregister("html")

    class _SafeLinkTreeprocessor(_Treeprocessor):
        """中和危险协议的 <a href>/<img src>（javascript:/data: 等 → 失活），纵深防御 XSS。"""

        def run(self, root):  # noqa: N802 (markdown API 命名)
            for el in root.iter("a"):
                href = el.get("href")
                if href is not None and not _is_safe_url(href):
                    el.set("href", "#")
            for el in root.iter("img"):
                src = el.get("src")
                if src is not None and not _is_safe_url(src):
                    el.set("src", "")
            return None

    class _SafeLinkExtension(_Extension):
        def extendMarkdown(self, md) -> None:  # noqa: N802 (markdown API 命名)
            # 优先级低 → 在内联/wikilink 处理之后跑，覆盖所有已生成的 a/img。
            md.treeprocessors.register(_SafeLinkTreeprocessor(md), "guanlan_safelink", 5)

    class _RawImageTreeprocessor(_Treeprocessor):
        """把**库内相对** `<img src>` 改写为本地图片端点 URL（供 raw 预览显示 `raw/images/` 嵌图）。

        仅作用于相对路径（`_is_relative_local`）：`http(s):` 外链原样保留、`data:`/`javascript:` 已被
        safelink 失活成空串而被跳过。改写后是根相对 URL（无协议），safelink 复跑也视为安全。
        """

        def __init__(self, md, image_src) -> None:
            super().__init__(md)
            self._image_src = image_src

        def run(self, root):  # noqa: N802 (markdown API 命名)
            for el in root.iter("img"):
                src = el.get("src")
                if src and _is_relative_local(src):
                    el.set("src", self._image_src(src))
            return None

    class _RawImageExtension(_Extension):
        def __init__(self, image_src) -> None:
            super().__init__()
            self._image_src = image_src

        def extendMarkdown(self, md) -> None:  # noqa: N802 (markdown API 命名)
            # 优先级 4 < safelink(5)：safelink 先失活危险 src（→ 空串被本处跳过），本处再改写相对图。
            md.treeprocessors.register(
                _RawImageTreeprocessor(md, self._image_src), "guanlan_rawimage", 4
            )

    class _TableHtmlPreprocessor(_Preprocessor):
        """把原始 `<table>…</table>` 片段消毒后存入 `htmlStash`，使其绕过转义、以安全 HTML 还原。

        `_EscapeHtmlExtension` 只注销了 `html_block`/`html`，**未动 `raw_html` 后处理器**——故
        stash 里的占位符仍会在末尾被原样还原。本预处理器据此只把**消毒后**的表格 HTML 存进 stash，
        其余原始 HTML 仍因 `html_block` 缺席而被转义。优先级 < `fenced_code`(25)：围栏代码已先被
        stash 成占位符，故 `<table>` 写在 ```…``` 内时不会被误渲染（保字面，决策P4-3 一致）。
        """

        def run(self, lines):  # noqa: N802 (markdown API 命名)
            def repl(m):
                clean = _sanitize_table_html(m.group(0))
                if clean is None:
                    return m.group(0)  # 非法/无 table → 原样（后续被转义）
                # 独立成块（前后空行）→ raw_html 后处理器按块级还原、不裹进 <p>。
                return "\n\n" + self.md.htmlStash.store(clean) + "\n\n"

            return _TABLE_BLOCK_RE.sub(repl, "\n".join(lines)).split("\n")

    class _TableHtmlExtension(_Extension):
        def extendMarkdown(self, md) -> None:  # noqa: N802 (markdown API 命名)
            md.preprocessors.register(_TableHtmlPreprocessor(md), "guanlan_table_html", 24)

    class _WikiLinkInlineProcessor(_InlineProcessor):
        """把 `[[…]]` 渲染为站内锚链（resolved）/ raw 源链（`[[raw/…]]`）/ 标灰 span（断链）。"""

        def __init__(
            self, pattern: str, md, stem_to_path: dict[str, str], raw_index: dict[str, str]
        ) -> None:
            super().__init__(pattern, md)
            self._stem_to_path = stem_to_path
            self._raw_index = raw_index

        def handleMatch(self, m, data):  # noqa: N802 (markdown API 命名)
            tag, attrib, display = _resolve_wikilink(
                m.group(1), self._stem_to_path, self._raw_index
            )
            el = _etree.Element(tag, attrib)  # 命中→a[data-page|data-raw]（前端站内导航，无 href 跳转）
            # display 不包 AtomicString：让 `[[页|**别名**]]` 的内联格式（强调/code 等）照常渲染（评审修复
            # 旧的 AtomicString 把别名 markdown 渲成字面）。无嵌套之忧——裸 raw/ 路径串已改走跳过 `<a>` 子树的
            # `_RawPathTreeprocessor`（树阶段），inline 阶段已无任何模式会回灌 display 套出嵌套 `<a>`。
            el.text = display
            return el, m.start(0), m.end(0)

    class _WikiLinkExtension(_Extension):
        def __init__(self, stem_to_path: dict[str, str], raw_index: dict[str, str]) -> None:
            super().__init__()
            self._stem_to_path = stem_to_path
            self._raw_index = raw_index

        def extendMarkdown(self, md) -> None:  # noqa: N802 (markdown API 命名)
            # 优先级 175 高于内置 'link'(160)，确保 [[…]] 先于普通 [text](url) 被吃掉。
            md.inlinePatterns.register(
                _WikiLinkInlineProcessor(
                    WIKILINK_RE.pattern, md, self._stem_to_path, self._raw_index
                ),
                "guanlan_wikilink",
                175,
            )

    # 不在这些标签的子树里联裸路径串：`a`（链接文字——否则 `[raw/x.md](url)` 会在链接内套出嵌套锚）、
    # `code`/`pre`（行内代码/代码块——保字面，同决策P4-3）、`span`（断链/rawlink.broken 标灰节点——
    # 否则 `[raw/exists.md](raw/missing.md)` 经 ① 成 broken span 后，② 会在其内再联出**可点 rawlink 嵌在
    # 灰 span 里**、且指向另一个源；`[[raw/ghost.md]]` 的灰 span 同理，评审修复）。
    _RAW_SKIP_SUBTREE = frozenset({"a", "code", "pre", "span"})

    class _RawPathTreeprocessor(_Treeprocessor):
        """把指向 raw/ 源的引用联成 raw 源链——**两路在树上处理**（非 inline），故可按祖先精确避让：

        ① **markdown 链接** `[文字](raw/<slug>.md)`：命中现存 raw 源 → 改写 `<a href>` 为 `a.rawlink[data-raw]`
           （**保留链接文字、内联格式如 `<strong>`、作者 title**）；`.md` 形但缺失 → `span.rawlink.broken`；
           **非 `.md` 扩展名资产**（`raw/images/x.png` 等）→ **原样保留**（不毁合法资产链接）。外链 / 绝对
           路径 / 非 raw/ 前缀一律不动。
        ② **裸路径串** `raw/<slug>.md`（正文 plain text）：切文本节点联链，但**跳过 `a`/`code`/`pre`/`span`
           子树**——这是改用 treeprocessor（而非 inline）的关键：inline 处理器看不到祖先，链接文字
           `[raw/x.md](url)` 会被回灌再联、套出非法嵌套 `<a>`；树上按祖先跳过则从根上杜绝（含跳过 ① 产出的
           断链 `span`，否则会在灰 span 里再联出可点 rawlink）。

        ① 先于 ② 跑：① 把命中的 raw/ 链接转成 `a.rawlink`（tag 仍 `a`）、缺失的转成 `span`，② 随即把它们
        当 `a`/`span` 子树跳过，不会再进去联其链接文字。
        """

        def __init__(self, md, raw_index: dict[str, str]) -> None:
            super().__init__(md)
            self._raw_index = raw_index

        def _split(self, text):
            """把一段文本按裸 `raw/<slug>.md` 切成 `[str, <a|span>, str, …]`（首尾恒为 str）；无命中 → None。"""
            if not text or "raw/" not in text:
                return None
            matches = list(_RAW_PATH_RE.finditer(text))
            if not matches:
                return None
            frags: list = []
            last = 0
            for m in matches:
                frags.append(text[last : m.start()])
                tag, attrib, _ = _resolve_raw_link(
                    _raw_ref_basename(m.group(0)), self._raw_index, m.group(0)
                )
                node = _etree.Element(tag, attrib)
                node.text = m.group(0)
                frags.append(node)
                last = m.end()
            frags.append(text[last:])
            return frags

        @staticmethod
        def _insert_nodes(parent, frags, at):
            """把 `_split` 的 frags 的**元素段**（frags[1],frags[3],…，各以其后字符串作 tail）依次插到
            `parent` 的 `at` 位置起；首段字符串（frags[0]）由调用方自行安置到 el.text 或前邻 tail。
            返回插入的元素数（供调用方推进插入锚点，避免 list.index 的 O(n²) 再扫）。"""
            count = 0
            i = 1
            while i < len(frags):
                node = frags[i]
                node.tail = frags[i + 1]
                parent.insert(at + count, node)
                count += 1
                i += 2
            return count

        def _linkify(self, el):
            # Comment/PI 节点 .tag 是 callable（非字符串）→ 显式跳过（不进其文本联链，纵深防御）。
            if callable(el.tag) or el.tag in _RAW_SKIP_SUBTREE:
                return  # 整个子树跳过（链接文字 / 行内代码 / 代码块 / 断链 span）
            original = list(el)
            for child in original:  # 先递归子元素（其内部 text）
                self._linkify(child)
            # ① 联本元素 .text（新元素插到所有原子元素之前）。
            inserted = 0
            frags = self._split(el.text)
            if frags is not None:
                el.text = frags[0]
                inserted = self._insert_nodes(el, frags, 0)
            # ② 联每个原子元素的 .tail（插到该元素之后）。pos 跟踪当前原 child 在 el 中的实时下标
            # （= 原序 + 已插入数），免每轮 list(el).index(child) 的 O(n²) 线性再扫（评审 efficiency 修复）。
            pos = inserted
            for child in original:
                frags = self._split(child.tail)
                if frags is not None:
                    child.tail = frags[0]
                    pos += self._insert_nodes(el, frags, pos + 1)
                pos += 1  # 跳过 child 本身，下一个原 child 紧随其后（含其间已插入的锚）

        def run(self, root):  # noqa: N802 (markdown API 命名)
            # ① markdown 链接 href 指向 raw/ 源 → rawlink（命中）/ broken（.md 形但缺失）；先于 ② 跑。
            # list(...) 物化：① 内把 broken 链接的 tag 由 `a`→`span`，避免边迭代边改 iter("a") 匹配标签。
            for el in list(root.iter("a")):
                href = el.get("href")
                if not href:
                    continue
                norm = href[2:] if href.startswith("./") else href  # 容 `./raw/x.md`
                basename = _raw_ref_basename(norm)
                if basename is None:
                    continue  # 外链 / 绝对 / 非 raw/ 前缀 → 不动
                actual = _lookup_raw(basename, self._raw_index)
                if actual is not None:  # 命中现存 raw 源 → rawlink（保留链接文字/格式与作者 title）
                    title = el.get("title")
                    el.attrib.clear()  # 丢 href（改站内导航，无 href 跳转）
                    el.set("class", "rawlink")
                    el.set("data-raw", actual)
                    if title:  # 保留作者 title 提示（评审修复：旧的 attrib.clear 把 title 一并丢了）
                        el.set("title", title)
                elif _is_raw_md_form(basename):  # `.md` 形（或无扩展名 stem）但不存在 → 标灰
                    el.attrib.clear()
                    el.tag = "span"
                    el.set("class", "rawlink broken")
                    el.set("title", _RAWLINK_BROKEN_TITLE)
                # else: 非 `.md` 扩展名资产（raw/images/x.png 等）→ 链接原样保留，不毁 href（评审修复）。
            # ② 裸路径串 → rawlink/broken（按祖先跳过 a/code/pre/span，杜绝在链接文字/代码/断链 span 里联）。
            self._linkify(root)

    class _RawPathExtension(_Extension):
        def __init__(self, raw_index: dict[str, str]) -> None:
            super().__init__()
            self._raw_index = raw_index

        def extendMarkdown(self, md) -> None:  # noqa: N802 (markdown API 命名)
            # 优先级 6 > safelink(5)：在 safelink 前把 raw/ href 转成无 href 的 rawlink（raw/ 本就安全，
            # 顺序其实无碍，取 6 只为在同族 treeprocessor 中先跑、语义清晰）。核心 inline(20) 早已建好 <a>。
            md.treeprocessors.register(
                _RawPathTreeprocessor(md, self._raw_index), "guanlan_rawpath", 6
            )

    class _CodePathLinkTreeprocessor(_Treeprocessor):
        """把【整段恰好是页面引用】的**行内** `<code>` 破例转成站内 wikilink。

        源出处常被 LLM 写成 `wiki/sources/x.md` 或 `[[x]]` 并套反引号，渲染成 `<code>` 后
        默认不联链。这里只对两类**整段忠实引用**破例：① 整段就是 `[[...]]`；② 整段精确等于
        某现有页的合法路径 / 文件名 / stem 写法。后者与 `[[wikilink]]` 完全同口径
        （`link_stem` + stem 表），不是正则猜路径，假阳性极低（除非把恰好等于某页名的串当
        代码示例写）。
        仅作用于行内 code：`<pre>` 下的缩进代码块跳过，围栏代码已被 fenced_code 预处理器
        搬进 htmlStash、本就不在树里——故代码块的字面语义（决策P4-3）不受影响。**已在
        `<a>` 内的 code 也跳过**：`[`wiki/sources/x.md`](url)` 这种 code 当链接文字的情形，
        若再把内层 code 转成 `<a>` 会产生嵌套锚（非法 HTML，浏览器会拆链、连累内外两个链接）。
        """

        def __init__(self, md, stem_to_path: dict[str, str], raw_index: dict[str, str]) -> None:
            super().__init__(md)
            self._stem_to_path = stem_to_path
            self._raw_index = raw_index

        def run(self, root):  # noqa: N802 (markdown API 命名)
            # 排除已在 <pre>（缩进代码块）或 <a>（code 作链接文字）内的 code：前者保字面，
            # 后者避免嵌套锚。围栏代码已进 htmlStash、本就不在树里。
            skip = {
                id(code)
                for parent in (*root.iter("pre"), *root.iter("a"))
                for code in parent.iter("code")
            }
            for el in root.iter("code"):
                if id(el) in skip or len(el) or el.text is None:
                    continue  # 代码块内 / 链接内 / 含子元素 / 空 → 不碰
                raw_wikilink = _code_wikilink_raw(el.text)
                if raw_wikilink is not None:
                    # 整段就是 `[[…]]`：与行内 [[…]] 完全同路（_resolve_wikilink，含 `[[raw/…]]` 拦截）。
                    _retag(
                        el, *_resolve_wikilink(raw_wikilink, self._stem_to_path, self._raw_index)
                    )
                    continue

                # 整段恰好是 `raw/<slug>.md`：raw 源引用（存在链 / 缺失标灰），**先于** wiki 页解析判定——
                # 否则 `raw/x.md` 的 link_stem=`x` 可能误命中同名 wiki 页 `x.md`，把 raw 引用错链到 wiki。
                raw_key = _raw_ref_in_code(el.text)
                if raw_key is not None:
                    _retag(el, *_resolve_raw_link(raw_key, self._raw_index, el.text.strip()))
                    continue

                # 仅当整段是【对某现有页的忠实引用】才联链：放行含空格的合法页名，
                # 同时挡住命令/多 token 代码（如 `cat wiki/x.md`、`git status`）。
                target = _code_ref_target(el.text, self._stem_to_path)
                if target is None:
                    continue  # 非忠实引用 / 解析不到现有页 → 保持字面 code
                # 与 [[…]] 命中同形；显示干净 stem（去 sources/ 前缀与 .md 后缀）。
                _retag(el, "a", {"class": "wikilink", "data-page": target}, Path(target).stem)
            return None

    class _CodePathLinkExtension(_Extension):
        def __init__(self, stem_to_path: dict[str, str], raw_index: dict[str, str]) -> None:
            super().__init__()
            self._stem_to_path = stem_to_path
            self._raw_index = raw_index

        def extendMarkdown(self, md) -> None:  # noqa: N802 (markdown API 命名)
            # 树处理阶段跑（行内 code 节点此时已生成）；data-page/data-raw 无 href，与 safelink 不冲突。
            md.treeprocessors.register(
                _CodePathLinkTreeprocessor(md, self._stem_to_path, self._raw_index),
                "guanlan_codepathlink",
                4,
            )


def render_markdown(
    text: str, wiki: Path | None = None, *, image_src=None, allow_tables: bool = False
) -> str:
    """把 markdown 文本渲染为**安全** HTML（供单页与对话输出共用）。

    始终带两道安全闸：`_EscapeHtmlExtension`（关原始 HTML 透传）+ `_SafeLinkExtension`
    （中和 javascript:/data: 链接）。给了 `wiki` 才挂 `[[wikilink]]` 重写（解析到该库页面），
    并对【整段精确解析到现有页】的行内 `<code>` 破例联链（兜底 LLM 把源出处写成路径+反引号）；
    同时把指向 `raw/<slug>.md` 的引用（裸路径串 / 行内 code / `[[raw/…]]` 三写法）联成只读 raw 源链
    `a.rawlink[data-raw]`（仅真实存在的 raw 文件，缺失标灰），raw 目录由 `wiki.parent/raw` 推出。
    给了 `image_src`（`(相对路径) -> URL` 可调用）才挂库内相对 `<img>` 改写——供 raw 预览把
    `images/<slug>/…` 指向本地图片端点。`allow_tables=True` 才对原始 `<table>` HTML 破例
    （allowlist 消毒后还原，供 raw 预览显示 mineru/marker 的复杂表）——两者都默认关，wiki/chat
    渲染姿态不变（原始 HTML 仍全转义）。缺 markdown extra 时回退转义 `<pre>` 源码视图。
    """
    if not _HAS_MARKDOWN:
        return "<pre>" + html_lib.escape(text) + "</pre>"
    extensions = [
        "fenced_code",
        "tables",
        _EscapeHtmlExtension(),  # 安全：关原始 HTML 透传。
        _SafeLinkExtension(),  # 安全：中和 javascript:/data: 链接。
    ]
    if wiki is not None:
        stem_map = _stem_to_path(wiki)  # 单次扫库，wiki/code 两扩展共享（避免重复 rglob）。
        raw_index = _raw_name_index(wiki.parent)  # raw/ 顶层 .md 真实文件名集（每渲染重扫，同 stem_map 纪律）。
        extensions.append(_WikiLinkExtension(stem_map, raw_index))
        extensions.append(_CodePathLinkExtension(stem_map, raw_index))
        extensions.append(_RawPathExtension(raw_index))  # 裸 raw/<slug>.md 路径串 + markdown 链接 href→rawlink。
    if image_src is not None:
        extensions.append(_RawImageExtension(image_src))
    if allow_tables:
        extensions.append(_TableHtmlExtension())  # 仅放行消毒后的 <table> HTML（raw 预览）。
    return _markdown.Markdown(extensions=extensions).convert(text)


def render_page(wiki: Path, page_path: Path, *, image_src=None, allow_tables: bool = False) -> dict:
    """渲染单页：返回 `{meta, html}`。

    坏/缺 frontmatter 时 `meta=None` 仍渲染正文（容错档，同 P3 决策P3-8）。装了 markdown 走
    富渲染 + `[[wikilink]]` 重写；否则回退转义 `<pre>` 源码视图。`image_src`/`allow_tables` 透传给
    `render_markdown`（raw 预览传入以显示 `raw/images/` 嵌图 + 复杂 `<table>`，其余调用默认关、行为不变）。
    """
    meta, body = load_page(page_path)
    return {
        "meta": meta,
        "html": render_markdown(body, wiki, image_src=image_src, allow_tables=allow_tables),
    }
