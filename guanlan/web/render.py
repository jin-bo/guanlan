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

from ..pages import WIKILINK_RE, link_stem, load_page

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


def _stem_to_path(wiki: Path) -> dict[str, str]:
    """全页面 stem（小写）→ 相对知识库根的 posix 路径，供 `[[wikilink]]` 解析为站内导航目标。

    解析集含 config 页（与 `check`/`graph` 同口径）。stem 全库唯一是 wikilink 按名解析的固有
    前提（见 graph.py）；万一重名，按排序取第一个，保持确定性。

    **故意每次渲染重建**（不缓存）：ingest 随时增删页面，每请求重扫保证新页立刻被解析为可点
    链接、删页立刻标灰——本地单用户工具下 O(N) 重扫的代价远小于缓存失效导致的"链接对不上"。
    """
    mapping: dict[str, str] = {}
    root = wiki.parent
    for path in sorted(wiki.rglob("*.md")):
        if path.is_file():
            mapping.setdefault(path.stem.lower(), path.relative_to(root).as_posix())
    return mapping


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
            raw = m.group(1)
            display = _wikilink_display(raw) or raw.strip()
            stem = link_stem(raw)
            target = self._stem_to_path.get(stem) if stem else None
            if target is not None:
                el = _etree.Element("a")
                el.set("class", "wikilink")
                el.set("data-page", target)  # 前端据此切到目标页（站内导航，无 href 跳转）
            else:
                el = _etree.Element("span")
                el.set("class", "wikilink broken")
                el.set("title", "无对应页面")
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


def render_markdown(text: str, wiki: Path | None = None) -> str:
    """把 markdown 文本渲染为**安全** HTML（供单页与对话输出共用）。

    始终带两道安全闸：`_EscapeHtmlExtension`（关原始 HTML 透传）+ `_SafeLinkExtension`
    （中和 javascript:/data: 链接）。给了 `wiki` 才挂 `[[wikilink]]` 重写（解析到该库页面）。
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
        extensions.append(_WikiLinkExtension(_stem_to_path(wiki)))
    return _markdown.Markdown(extensions=extensions).convert(text)


def render_page(wiki: Path, page_path: Path) -> dict:
    """渲染单页：返回 `{meta, html}`。

    坏/缺 frontmatter 时 `meta=None` 仍渲染正文（容错档，同 P3 决策P3-8）。装了 markdown 走
    富渲染 + `[[wikilink]]` 重写；否则回退转义 `<pre>` 源码视图。
    """
    meta, body = load_page(page_path)
    return {"meta": meta, "html": render_markdown(body, wiki)}
