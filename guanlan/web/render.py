"""单页渲染（P4，见 docs/P4-Web宿主.md §6）。

`load_page` 取正文 → 若装了 `markdown`（`guanlan[web]` extra）渲染为 HTML，否则回退到
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
from pathlib import Path

from ..pages import WIKILINK_RE, link_stem, load_page, page_stem_index

try:  # markdown 是 web extra 的一部分；缺失时回退 <pre> 源码视图（§6）。
    import markdown as _markdown
    from markdown.extensions import Extension as _Extension
    from markdown.inlinepatterns import InlineProcessor as _InlineProcessor
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


def _wikilink_display(raw: str) -> str:
    """`[[target|alias]]` → alias；`[[target#anchor]]` → target（剥锚点）；其余取原文。"""
    if "|" in raw:
        return raw.split("|", 1)[1].strip()
    return raw.split("#", 1)[0].strip()


# 全页面 stem(小写) → 相对库根 posix 路径，供 `[[wikilink]]` 解析为站内导航目标。归口于
# `pages.page_stem_index`（与 check/graph 同口径、含 config 可链）。**故意每次渲染重建**（不缓存）：
# ingest 随时增删页面，每请求重扫保证新页立刻可点、删页立刻标灰——本地单用户下 O(N) 重扫的代价
# 远小于缓存失效导致的"链接对不上"。
_stem_to_path = page_stem_index


def _code_ref_target(content: str, stem_to_path: dict[str, str]) -> str | None:
    """`content` 是【对某现有页的忠实整体引用】才返回其相对路径，否则 None（行内 code 兜底用）。

    忠实 = `content`（规整后）**整体恰好等于**该页的某种合法写法之一：完整相对路径 /
    去 `wiki/` 前缀 / 纯文件名（均可带或不带 `.md`，大小写不敏感）。如此既放行**含空格的
    合法页名**（如 `Smart Tools 模块`），又挡住命令/多 token 代码（如 `cat wiki/x.md`）——后者经
    `link_stem` 取末段虽与某页共享 stem，但整体不等于该页的任何合法写法，故不误判为引用。
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
    断链 → `span.wikilink.broken`。行内 `[[…]]` 与 code 兜底两路共用，杜绝两处样式/类名漂移。"""
    display = _wikilink_display(raw) or raw.strip()
    stem = link_stem(raw)
    target = stem_to_path.get(stem) if stem else None
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


def render_markdown(text: str, wiki: Path | None = None) -> str:
    """把 markdown 文本渲染为**安全** HTML（供单页与对话输出共用）。

    始终带两道安全闸：`_EscapeHtmlExtension`（关原始 HTML 透传）+ `_SafeLinkExtension`
    （中和 javascript:/data: 链接）。给了 `wiki` 才挂 `[[wikilink]]` 重写（解析到该库页面），
    并对【整段精确解析到现有页】的行内 `<code>` 破例联链（兜底 LLM 把源出处写成路径+反引号）。
    缺 markdown extra 时回退转义 `<pre>` 源码视图。
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
    return _markdown.Markdown(extensions=extensions).convert(text)


def render_page(wiki: Path, page_path: Path) -> dict:
    """渲染单页：返回 `{meta, html}`。

    坏/缺 frontmatter 时 `meta=None` 仍渲染正文（容错档，同 P3 决策P3-8）。装了 markdown 走
    富渲染 + `[[wikilink]]` 重写；否则回退转义 `<pre>` 源码视图。
    """
    meta, body = load_page(page_path)
    return {"meta": meta, "html": render_markdown(body, wiki)}
