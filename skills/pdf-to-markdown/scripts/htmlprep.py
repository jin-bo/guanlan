"""Shared HTML preprocessing for convert.py (guanlan P4.31 §4).

Runs once, before any backend (pandoc / MinerU / marker), so every backend gets the
same input:

  1. decode the bytes to text (BOM > HTTP charset > <meta> > UTF-8 > charset_normalizer);
  2. extract the main content with trafilatura (optional dependency; falls back to the
     whole page when it is missing, switched off, or keeps too little text);
  3. rewrite images and links: resolve relative URLs against the page URL, turn remote
     <img> into plain links, drop srcset / <picture><source>.

Only the standard library is required. trafilatura (and charset_normalizer, which it
pulls in) are used when importable.
"""

from __future__ import annotations

import codecs
import html
import html.entities
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

# Extraction that keeps less than this is treated as a failed extraction (whole page used).
MIN_EXTRACT_CHARS = 200
MIN_EXTRACT_RATIO = 0.10

# GB2312 / GBK pages are decoded as GB18030 (a superset), so rare characters survive.
_CHARSET_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "cp936": "gb18030"}

_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", re.IGNORECASE)
_H1_RE = re.compile(r"<h1[\s>]", re.IGNORECASE)
_BODY_OPEN_RE = re.compile(r"<body[^>]*>", re.IGNORECASE)

# Embedded documents: pandoc fetches them; replaced by a link (see _ImageRewriter._tag).
_EMBED_TAGS = {"iframe", "frame", "embed", "object"}

_BOMS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def _norm_charset(name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip().strip("\"'").lower()
    name = _CHARSET_ALIASES.get(name, name)
    try:
        info = codecs.lookup(name)
    except LookupError:
        return None
    # Only real text encodings: `<meta charset="base64">` / `rot13` / `idna` name codecs that
    # bytes.decode(..., errors="replace") rejects, which would abort the whole conversion.
    if not getattr(info, "_is_text_encoding", True) or info.name in ("idna", "punycode"):
        return None
    name = info.name
    # WHATWG Encoding: the labels iso-8859-1 / latin1 / us-ascii all mean windows-1252. Servers often
    # declare them for cp1252 text (smart quotes at 0x93/0x94 would become C1 controls or U+FFFD).
    return "cp1252" if name in ("latin-1", "iso8859-1", "ascii") else name


def decode_html(data: bytes, declared: str | None = None) -> tuple[str, str, str]:
    """Return (text, charset, source). `declared` is the HTTP Content-Type charset."""
    # BOM first (WHATWG encoding sniffing): servers often send a wrong default charset header.
    for bom, name in _BOMS:
        if data.startswith(bom):
            return data[len(bom):].decode(name, errors="replace"), name, "bom"
    cs = _norm_charset(declared)
    if cs:
        return data.decode(cs, errors="replace"), cs, "http"
    m = _META_CHARSET_RE.search(data[:4096])
    cs = _norm_charset(m.group(1).decode("ascii", "replace")) if m else None
    if cs and cs.startswith(("utf-16", "utf-32")):
        # WHATWG: a <meta> that ASCII-matched cannot be in UTF-16 (no BOM here); it means UTF-8.
        cs = "utf-8"
    if cs:
        return data.decode(cs, errors="replace"), cs, "meta"
    try:
        return data.decode("utf-8"), "utf-8", "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        cs = _norm_charset(best.encoding) if best is not None else None
        if cs:
            return data.decode(cs, errors="replace"), cs, "detected"
    except ImportError:
        pass
    return data.decode("utf-8", errors="replace"), "utf-8", "fallback"


class _BogusMarkedSection:
    """Mixin: `<![…` is a bogus comment that ends at the first `>` (HTML5 / pandoc).

    The stdlib parser hands it to `_markupbase.parse_marked_section`, which raises AssertionError on an
    unknown keyword (`<![foo[`) in Python 3.10–3.12 — one odd page would abort the whole conversion.
    """

    def parse_html_declaration(self, i):
        rawdata = self.rawdata
        if rawdata.startswith("<![", i):
            j = rawdata.find(">", i + 3)
            if j < 0:
                return -1
            self.unknown_decl(rawdata[i + 3 : j])
            return j + 1
        return super().parse_html_declaration(i)

    def _raw_text_start(self, tag, attrs) -> bool:
        """`<script/>` / `<style/>`: HTML ignores the slash on these, so they open a raw-text element
        that runs to `</script>` (browsers and pandoc agree). The stdlib parser treats them as empty,
        and the script / CSS text after them would become page text. Open them as start tags instead."""
        if tag not in self.CDATA_CONTENT_ELEMENTS:
            return False
        self.handle_starttag(tag, attrs)
        self.set_cdata_mode(tag)
        return True


class _TextCounter(_BogusMarkedSection, HTMLParser):
    """Visible text: everything outside script/style/noscript/template/title.

    <head> is not in the skip set: `</head>` may be omitted in valid HTML, and this parser does not
    close it implicitly, so the whole page would count as 0 characters and disable the ratio check.
    The only text in <head> is in <title>/<script>/<style>, which are skipped anyway.
    """

    _SKIP = {"script", "style", "noscript", "template", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []
        self.title: list[str] = []
        self._in_title = False
        self._title_seen = False
        self._svg = 0

    @property
    def _in_svg(self) -> bool:
        return self._svg > 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.depth += 1
        if tag == "title" and not self._title_seen and not self._in_svg:
            self._in_title = True  # only the first document <title>; <svg><title> is an icon tooltip
        if tag == "svg":
            self._svg += 1

    def handle_startendtag(self, tag, attrs):
        if not self._raw_text_start(tag, attrs):
            super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self._SKIP and self.depth:
            self.depth -= 1
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_seen = True
        if tag == "svg" and self._svg:
            self._svg -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        elif not self.depth:
            self.parts.append(data)


def _scan(text: str) -> tuple[int, str]:
    """(visible non-space characters, <title> text)."""
    p = _TextCounter()
    p.feed(text)
    p.close()
    visible = "".join(p.parts)
    return len(re.sub(r"\s+", "", visible)), " ".join("".join(p.title).split())


def _add_title_h1(out: str, title: str) -> str:
    """No <h1> in `out`: put the page <title> first (one-click import takes the title from the first heading;
    pandoc drops <head><title>, so without this the title would fall back to the file name)."""
    if not title or _H1_RE.search(out):
        return out
    h1 = f"<h1>{html.escape(title)}</h1>"
    m = _BODY_OPEN_RE.search(out)
    return out[: m.end()] + h1 + out[m.end():] if m else h1 + out


def extract_main(page: str, base_url: str | None, mode: str = "auto") -> tuple[str, str]:
    """Return (html, status). status: trafilatura | off | none | fallback."""
    if mode == "off":
        return _add_title_h1(page, _scan(page)[1]), "off"
    try:
        import trafilatura
    except ImportError:
        return _add_title_h1(page, _scan(page)[1]), "none"
    try:
        out = trafilatura.extract(
            page,
            url=base_url,
            output_format="html",
            include_tables=True,
            include_images=True,
            include_links=True,
            include_formatting=True,
        )
    except Exception:  # noqa: BLE001 — extraction is an improvement only; never fatal
        out = None
    page_chars, title = _scan(page)
    if not out:
        return _add_title_h1(page, title), "fallback"
    kept_chars, _ = _scan(out)
    if kept_chars < MIN_EXTRACT_CHARS or (page_chars and kept_chars < page_chars * MIN_EXTRACT_RATIO):
        return _add_title_h1(page, title), "fallback"
    return _add_title_h1(out, title), "trafilatura"


def _is_remote(url: str) -> bool:
    # protocol-relative `//host/a.png` (left as-is when there is no base URL) is remote too
    return url.startswith("//") or urlsplit(url).scheme.lower() in ("http", "https")


# A local image path we let pandoc read: same folder or below, plain characters only (after decoding).
_SAFE_LOCAL_RE = re.compile(r"^[\w\-. ]+(?:/[\w\-. ]+)*$")


def _is_outside_local(url: str) -> bool:
    """True for any local reference that is not plainly inside the page's own folder.

    A whitelist, not a blacklist: pandoc --extract-media copies whatever local file an <img> names
    into the knowledge base, and blacklists keep missing encodings (`%2Fetc`, `%2e%2e/`, `C:`, `\\`…).
    Only `data:` images and simple relative paths (word characters, `-`, `.`, space; no `..` or other
    dot-leading segment) are kept; everything else is reduced to its alt text.
    """
    if url.startswith("data:") or _is_remote(url):
        return False
    if urlsplit(url).scheme or url.startswith("//"):  # file:, or any other scheme pandoc might try
        return True
    path = url
    for _ in range(3):  # pandoc decodes once; decode until stable so double-encoding cannot slip through
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    if not _SAFE_LOCAL_RE.match(path):
        return True
    # ".." climbs out; hidden names (`.env`, guanlan's `.<name>.source.json` with the full signed URL) are
    # never images a page means to show
    return any(seg.startswith(".") and seg != "." for seg in path.split("/"))


def _attr(attrs: list[tuple[str, str | None]], name: str) -> str | None:
    for k, v in attrs:
        if k == name:
            return v
    return None


# HTMLParser accepts attribute names containing `<`, quotes and other odd characters, and they would
# be written back unescaped: keep only plain names so pandoc tokenizes the tag exactly as we did.
_ATTR_NAME_RE = re.compile(r"^[A-Za-z_:][-A-Za-z0-9_:.]*$")


def _render_tag(tag: str, attrs: list[tuple[str, str | None]], close: bool) -> str:
    parts = [tag]
    seen: set[str] = set()
    for k, v in attrs:
        # A repeated attribute is dropped (HTML keeps the first): `_attr` checked only the first `src`, so a
        # second `src="/etc/hosts"` must not reach a converter that might read the last one.
        if not _ATTR_NAME_RE.match(k) or k in seen:
            continue
        seen.add(k)
        parts.append(k if v is None else f'{k}="{html.escape(v, quote=True)}"')
    return "<" + " ".join(parts) + (" />" if close else ">")


# Attributes through which a renderer can load something (marker renders HTML with WeasyPrint, which
# fetches stylesheets, CSS url(...) and SVG <image href>). Outside <a> and <img> they carry nothing a
# Markdown conversion needs, so they are dropped unless they point inside the page ("#…").
_RESOURCE_ATTRS = {"src", "href", "xlink:href", "poster", "background", "data", "srcset", "lowsrc", "codebase"}


def _strip_resource_attrs(attrs):
    return [
        (k, v)
        for k, v in attrs
        if k != "style" and not (k in _RESOURCE_ATTRS and not (v or "").strip().startswith("#"))
    ]


class _ImageRewriter(_BogusMarkedSection, HTMLParser):
    """Re-serialize the document from what this parser understood, rewriting what could make a
    converter load something: <img>, embeds, <base>, <link>, <style>/<script>, style= and resource
    attributes, plus charset <meta>.

    Every tag is rebuilt from its parsed attributes and every text run is escaped; comments and
    declarations are dropped. Nothing is copied verbatim, so pandoc cannot see a tag this parser
    did not see (the two parsers disagree on comments, CDATA, unclosed tags…)."""

    # Newer Python (3.12.11+ / 3.13.4+ / 3.14) treats iframe / xmp / noembed / noframes as raw text and
    # title / textarea as escapable raw text: their content would reach handle_data untouched. pandoc's
    # HTML reader parses tags inside them, so an <img src="/etc/hosts"> there would bypass the rewrite and
    # be copied by --extract-media. Parse their content as markup, like pandoc does; script/style stay raw.
    CDATA_CONTENT_ELEMENTS = ("script", "style")
    RCDATA_CONTENT_ELEMENTS = ()

    def __init__(self, base_url: str | None) -> None:
        super().__init__(convert_charrefs=False)
        self.base = base_url
        self.out: list[str] = []
        self.in_link = 0
        self.picture = 0
        self.in_style = 0

    def _resolve(self, url: str) -> str:
        url = url.strip()
        # an empty reference resolves to the page URL *with its query*: a signed URL's credentials
        # would land in raw/ (guanlan only redacts the frontmatter). Keep it as-is.
        if self.base and url and not url.startswith(("data:", "#", "mailto:", "javascript:")):
            return urljoin(self.base, url)
        return url

    def _tag(self, tag, attrs, close: bool) -> None:
        if tag in _EMBED_TAGS:
            # pandoc's HTML reader fetches <iframe src> and inlines it: a downloaded page could make it
            # fetch internal URLs (bypassing guanlan's address checks). Keep a plain link instead.
            src = _attr(attrs, "src") or _attr(attrs, "data") or ""
            resolved = self._resolve(src) if src else ""
            if resolved and _is_remote(resolved) and not self.in_link:
                self.out.append(f'<a href="{html.escape(resolved, quote=True)}">{html.escape("嵌入：" + resolved)}</a>')
            return
        if tag == "base":
            # pandoc resolves relative <img src> against <base href> and --extract-media then reads
            # that file / URL (`<base href="file:///etc/">`): drop it, relative paths stay local.
            # A remote base still decides where relative URLs point (as in a browser): adopt it here,
            # so they become correct remote links instead of being resolved against the wrong page.
            href = (_attr(attrs, "href") or "").strip()
            resolved = urljoin(self.base, href) if self.base and href else href
            if resolved and _is_remote(resolved) and not resolved.startswith("//"):
                self.base = resolved
            return
        if tag == "link":
            return  # stylesheets, icons, preloads: a renderer (WeasyPrint under marker) would fetch them
        if tag in ("style", "script"):
            if not close:
                self.in_style += 1  # CSS url(...) would be fetched too; script is useless here. Text dropped.
            return
        if tag == "picture" and not close:
            self.picture += 1
        if tag == "meta" and (
            _attr(attrs, "charset") is not None or (_attr(attrs, "http-equiv") or "").lower() == "content-type"
        ):
            return  # the old declaration (e.g. gbk) would contradict the UTF-8 file we write
        if tag == "source" and self.picture:
            return  # <picture><source srcset> would make the browser fetch remote images
        if tag == "a":
            href = _attr(attrs, "href")
            if not close:
                # <a> cannot nest: a new <a> implicitly closes an open one (as in browsers and pandoc).
                # Counting would leave in_link > 0 after `<a>1<a>2</a>`, and every later remote image
                # and embed would lose its link.
                self.in_link = 1
            kept = [(k, v) for k, v in attrs if k != "style"]
            if href is not None and self.base:
                kept = [(k, self._resolve(v) if k == "href" and v else v) for k, v in kept]
            self.out.append(_render_tag(tag, kept, close))
            return
        if tag == "img":
            self._img(attrs, close)
            return
        self.out.append(_render_tag(tag, _strip_resource_attrs(attrs), close))

    def _img(self, attrs, close: bool) -> None:
        src = _attr(attrs, "src") or ""
        resolved = self._resolve(src) if src else src
        if src and _is_outside_local(resolved):
            # an absolute path, a file: URL or a "../" path: pandoc --extract-media would copy that file
            # from this machine into the knowledge base. Keep only the alt text.
            alt = " ".join((_attr(attrs, "alt") or "").split())
            if alt:
                self.out.append(html.escape(f"图：{alt}"))
            return
        if src and _is_remote(resolved):
            alt = " ".join((_attr(attrs, "alt") or "").split())
            label = html.escape(f"图：{alt}" if alt else "图")
            if self.in_link:  # no nested links: the outer <a> stays
                self.out.append(label)
            else:
                self.out.append(f'<a href="{html.escape(resolved, quote=True)}">{label}</a>')
            return
        self.out.append(_render_tag("img", [(k, v) for k, v in attrs if k not in ("srcset", "sizes", "style")], close))

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        if not self._raw_text_start(tag, attrs):
            self._tag(tag, attrs, True)

    def handle_endtag(self, tag):
        if tag in _EMBED_TAGS or tag == "link":
            return
        if tag in ("style", "script"):
            if self.in_style:
                self.in_style -= 1
            return
        if tag == "a":
            self.in_link = 0
        if tag == "picture" and self.picture:
            self.picture -= 1
        if tag == "source" and self.picture:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        # Text is re-escaped: a literal `<` this parser took as text must not become a tag for pandoc.
        if not self.in_style:
            self.out.append(html.escape(data, quote=False))

    def handle_entityref(self, name):
        # HTMLParser also reports `&T` in `AT&T` and `&b` in `a=1&b=2` (no `;`) as entity refs. Writing them
        # back as `&T;` / `&b;` would change the text: keep only names HTML knows, escape the rest as text.
        if f"{name};" in html.entities.html5:
            self.out.append(f"&{name};")
        else:
            self.out.append(html.escape("&" + name, quote=False))

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_comment(self, data):
        # Dropped, not copied: comments carry nothing a conversion needs, and parsers disagree on where
        # they end (`<!-->`, `--!>` close a comment in HTML5 / pandoc but not in older HTMLParser). Copying
        # the text would hand pandoc an <img> this parser never saw.
        pass

    def handle_decl(self, decl):
        # Only a plain doctype is kept. Like comments, the text is otherwise copied verbatim, so a `<` in it
        # (`<!DOCTYPE <img src="/etc/hosts">`) would hand pandoc a tag this parser never saw.
        if "<" not in decl:
            self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        pass  # `<?xml …?>` and the like carry nothing a conversion needs; dropped for the same reason as comments

    def unknown_decl(self, data):
        # `<![CDATA[…]]>` / `<![if …]>`: in HTML these are bogus comments that end at the first `>`, not
        # at `]]>` — dropped for the same reason as comments.
        pass


def rewrite_images(page: str, base_url: str | None) -> str:
    p = _ImageRewriter(base_url)
    p.feed(page)
    # An unterminated construct at the end (`<img src="/etc/hosts"` with no `>`) stays in rawdata, and
    # close() would flush it through handle_data untouched — pandoc still reads it as an <img>. Keep it
    # as escaped text instead.
    leftover, p.rawdata = p.rawdata, ""
    p.close()
    if leftover and not p.in_style:
        p.out.append(html.escape(leftover, quote=False))
    return "".join(p.out)


def prepare_html(data: bytes, *, charset: str | None, base_url: str | None, extract: str) -> tuple[str, dict]:
    """Bytes in → UTF-8 HTML text ready for any backend, plus a small report for the log."""
    text, used, source = decode_html(data, charset)
    body, status = extract_main(text, base_url, extract)
    body = rewrite_images(body, base_url)
    body = '<meta charset="utf-8">\n' + body  # the file we write is UTF-8; old declarations were dropped
    return body, {"charset": used, "charset_source": source, "extract": status}
