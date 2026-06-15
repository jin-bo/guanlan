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


def _resolve_wikilink(raw: str, stem_to_path: dict[str, str]) -> tuple[str, dict[str, str], str]:
    """把 `[[…]]` 内部 raw 解析为 `(tag, attrib, display)`：命中现有页 → `a.wikilink[data-page]`，
    断链 → `span.wikilink.broken`。行内 `[[…]]` 与 code 兜底两路共用，杜绝两处样式/类名漂移。

    P3.8：经 `resolve_owner`（精确 `link_stem` + fold 兜底）解析，与 check/graph/heal 同一张表、同
    口径——`[[multi_head_attention]]` 命中 `multi-head-attention.md`。**仅 `[[wikilink]]` 走 fold**；
    行内 code 引用（`_code_ref_target`）按精确路径/文件名判定、**不接 fold**（决策P3.8-7 边界）。
    """
    display = _wikilink_display(raw) or raw.strip()
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
        """把 `[[…]]` 渲染为站内锚链（resolved）或标灰 span（断链）。"""

        def __init__(self, pattern: str, md, stem_to_path: dict[str, str]) -> None:
            super().__init__(pattern, md)
            self._stem_to_path = stem_to_path

        def handleMatch(self, m, data):  # noqa: N802 (markdown API 命名)
            tag, attrib, display = _resolve_wikilink(m.group(1), self._stem_to_path)
            el = _etree.Element(tag, attrib)  # 命中→a[data-page]（前端站内导航，无 href 跳转）
            el.text = display
            return el, m.start(0), m.end(0)

    class _WikiLinkExtension(_Extension):
        def __init__(self, stem_to_path: dict[str, str]) -> None:
            super().__init__()
            self._stem_to_path = stem_to_path

        def extendMarkdown(self, md) -> None:  # noqa: N802 (markdown API 命名)
            # 优先级 175 高于内置 'link'(160)，确保 [[…]] 先于普通 [text](url) 被吃掉。
            md.inlinePatterns.register(
                _WikiLinkInlineProcessor(WIKILINK_RE.pattern, md, self._stem_to_path),
                "guanlan_wikilink",
                175,
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

        def __init__(self, md, stem_to_path: dict[str, str]) -> None:
            super().__init__(md)
            self._stem_to_path = stem_to_path

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
                    # 整段就是 `[[…]]`：与行内 [[…]] 完全同路（_resolve_wikilink），就地改写。
                    _retag(el, *_resolve_wikilink(raw_wikilink, self._stem_to_path))
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
        def __init__(self, stem_to_path: dict[str, str]) -> None:
            super().__init__()
            self._stem_to_path = stem_to_path

        def extendMarkdown(self, md) -> None:  # noqa: N802 (markdown API 命名)
            # 树处理阶段跑（行内 code 节点此时已生成）；data-page 无 href，与 safelink 不冲突。
            md.treeprocessors.register(
                _CodePathLinkTreeprocessor(md, self._stem_to_path),
                "guanlan_codepathlink",
                4,
            )


def render_markdown(
    text: str, wiki: Path | None = None, *, image_src=None, allow_tables: bool = False
) -> str:
    """把 markdown 文本渲染为**安全** HTML（供单页与对话输出共用）。

    始终带两道安全闸：`_EscapeHtmlExtension`（关原始 HTML 透传）+ `_SafeLinkExtension`
    （中和 javascript:/data: 链接）。给了 `wiki` 才挂 `[[wikilink]]` 重写（解析到该库页面），
    并对【整段精确解析到现有页】的行内 `<code>` 破例联链（兜底 LLM 把源出处写成路径+反引号）。
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
        stem_map = _stem_to_path(wiki)  # 单次扫库，两个扩展共享（避免重复 rglob）。
        extensions.append(_WikiLinkExtension(stem_map))
        extensions.append(_CodePathLinkExtension(stem_map))
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
