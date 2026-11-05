"""P4.28 Web 内嵌 SVG 渲染（docs/P4.28-Web内嵌SVG渲染.md）。

服务端：块级原始 `<svg>` → 与 ```svg 围栏块同形的**转义**代码块（不放行任何 SVG 标记）；前端 `svg_enhance.js`
以 `<img src="data:image/svg+xml,…">` 渲染。浏览器端行为由 scratch 冒烟验证，这里只守服务端口径与前端静态契约。
"""

from __future__ import annotations

import re

import pytest

from conftest import write_page

pytest.importorskip("markdown")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from guanlan.web.app import STATIC_DIR, create_app  # noqa: E402
from guanlan.web.render import render_markdown  # noqa: E402

SVG = '<svg viewBox="0 0 10 10">\n  <circle cx="5" cy="5" r="4"/>\n</svg>'


@pytest.fixture
def client(kb):
    with TestClient(create_app(kb)) as c:
        yield c


def _svg_blocks(html: str) -> int:
    return html.count('<code class="language-svg">')


# ── 服务端：该转的都转（漏报是沉默的，逐一列出本该命中的写法）────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        SVG,  # 多行
        '<svg width="4" height="4"><rect width="4" height="4"/></svg>',  # 单行
        "   " + SVG,  # 3 空格缩进仍是块级
        SVG.replace("<svg", "<SVG").replace("</svg>", "</SVG>"),  # 大写
        SVG + "   ",  # 收尾标签后的行尾空白
        "正文一段。\n" + SVG + "\n下一段。",  # 紧贴前后文本行（无空行）
    ],
)
def test_block_svg_becomes_escaped_svg_code_block(body) -> None:
    html = render_markdown(body)
    assert _svg_blocks(html) == 1
    assert "<svg" not in html.lower()  # 无任何活的 SVG 标记
    assert "&lt;circle" in html or "&lt;rect" in html  # 源码转义保真，供前端读 textContent


def test_stray_unclosed_svg_line_does_not_swallow_following_content() -> None:
    """行首一句未闭合的 `<svg …` 字样不得一路吞到后文真正的 SVG 块；后者仍须照常命中（防漏报）。"""
    html = render_markdown("<svg 元素很常见，见下文\n\n# 标题\n\n正文\n\n" + SVG)
    assert _svg_blocks(html) == 1
    assert "<h1>标题</h1>" in html and "<p>正文</p>" in html
    assert "&lt;circle" in html  # 真正的块仍被转成 svg 代码块


def test_two_svg_blocks_each_converted() -> None:
    html = render_markdown(SVG + "\n\n中间\n\n" + SVG)
    assert _svg_blocks(html) == 2
    assert "<p>中间</p>" in html


def test_payload_inside_svg_is_escaped() -> None:
    html = render_markdown('<svg onload="alert(1)"><script>alert(2)</script></svg>')
    assert _svg_blocks(html) == 1
    assert "<script" not in html and "&lt;script&gt;" in html
    assert 'onload="' not in html  # 属性引号被转义成 &quot;，不成活属性


def test_fenced_svg_block_is_same_shape() -> None:
    html = render_markdown("```svg\n" + SVG + "\n```")
    assert _svg_blocks(html) == 1


# ── 服务端：不该转的保持原状 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "```\n" + SVG + "\n```",  # 无语言围栏 = 代码示例，保字面
        "```xml\n" + SVG + "\n```",
        "    " + SVG.replace("\n", "\n    "),  # 4 空格 = 缩进代码块
        "行内 `<svg></svg>` 写法",
        "正文里提到 <svg>x</svg> 字样",  # 非行首
        "<svg>未闭合",
    ],
)
def test_non_block_svg_not_converted(body) -> None:
    html = render_markdown(body)
    assert _svg_blocks(html) == 0
    assert "<svg" not in html.lower()  # 原始 HTML 全转义的姿态不变


def test_rest_of_page_keeps_escape_posture() -> None:
    html = render_markdown(SVG + "\n\n正文夹带 <img src=x onerror=alert(1)>。")
    assert _svg_blocks(html) == 1
    assert "<img" not in html and "&lt;img" in html


# ── 各渲染入口都经同一通道 ──────────────────────────────────────────────────


def test_api_page_converts_svg(client, kb) -> None:
    write_page(kb, "wiki/concepts/Figure.md", type="concept", body="示意图：\n\n" + SVG + "\n")
    html = client.get("/api/page", params={"path": "wiki/concepts/Figure.md"}).json()["html"]
    assert _svg_blocks(html) == 1


def test_raw_preview_converts_svg(client, kb) -> None:
    (kb / "raw").mkdir(exist_ok=True)
    (kb / "raw" / "figure.md").write_text("# 资料\n\n" + SVG + "\n", encoding="utf-8")
    resp = client.get("/api/raw/file", params={"name": "figure.md"})
    assert resp.status_code == 200
    assert _svg_blocks(resp.json()["html"]) == 1


# ── 前端静态契约 ────────────────────────────────────────────────────────────


def test_svg_enhance_never_injects_markup() -> None:
    """安全铰链（决策P4.28-2）：只经 <img> data: URL 渲染，不把 SVG 源写进 innerHTML/outerHTML。"""
    src = (STATIC_DIR / "svg_enhance.js").read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", src)  # 只看代码，不看注释里的说明文字
    assert "window.enhanceSvg" in src
    assert "pre > code.language-svg" in src
    assert "data:image/svg+xml" in src and 'createElement("img")' in src
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in code, sink
    assert '"image/svg+xml"' in src  # DOMParser 严格 XML 校验
    assert "SVG_MAX_CHARS" in src  # 单图上限
    assert "markSvgFailed" in src and "textContent" in src  # 失败保留源码 + 徽标（textContent 注入）


def test_svg_enhance_wired_and_load_order() -> None:
    co = (STATIC_DIR / "content_enhance.js").read_text(encoding="utf-8")
    assert "enhanceSvg(container)" in co[co.index("function enhanceContent"):]
    code = (STATIC_DIR / "code_enhance.js").read_text(encoding="utf-8")
    assert "language-svg" in code  # hljs 的 xml 别名含 svg：须显式让给 enhanceSvg
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert index.index('/static/svg_enhance.js"') < index.index('/static/content_enhance.js"')


def test_svg_i18n_keys_in_both_languages() -> None:
    i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    for key in ("svg.renderFail", "svg.alt"):
        assert i18n.count(f'"{key}"') == 2, key


def test_svg_enhance_served(client) -> None:
    resp = client.get("/static/svg_enhance.js")
    assert resp.status_code == 200
    assert "window.enhanceSvg" in resp.text
