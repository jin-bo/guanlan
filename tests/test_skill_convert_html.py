"""P4.31 §4：pdf-to-markdown skill 的 HTML 公共预处理（解码 → 正文提取 → 图片改写）与 pandoc 档。

pandoc 真跑（CI 装了 pandoc）；trafilatura 有 / 无两种都测（无 = 让 import 失败）。
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from guanlan.convert import parse_backend_marker

_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pdf-to-markdown" / "scripts"
_SCRIPT = _SCRIPTS / "convert.py"

needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc 未安装（CI 必装）")

PARA = "这是正文段落，讲的是网址导入的设计与实现细节。" * 8


def _load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def prep():
    return _load("htmlprep_under_test", _SCRIPTS / "htmlprep.py")


@pytest.fixture
def no_trafilatura(monkeypatch):
    monkeypatch.setitem(sys.modules, "trafilatura", None)  # import trafilatura → ImportError


def _page(body: str, *, head: str = "<title>示例政策解读</title>", charset: str | None = "utf-8") -> str:
    meta = f'<meta charset="{charset}">' if charset else ""
    return f"<!doctype html><html><head>{meta}{head}</head><body>{body}</body></html>"


NAVPAGE = _page(
    '<nav><a href="/">首页</a> <a href="/about">关于我们</a> <a href="/news">新闻中心</a></nav>'
    f"<article><h2>一、背景</h2><p>{PARA}</p><h2>二、要点</h2><p>{PARA}</p></article>"
    "<footer>版权所有 示例网站 京ICP备00000000号</footer>"
)


def _run(src: Path, *args: str) -> tuple[str, str]:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), str(src), *args], capture_output=True, text=True, encoding="utf-8"
    )
    assert proc.returncode == 0, proc.stderr
    out = Path(proc.stdout.strip().splitlines()[-1])
    return out.read_text(encoding="utf-8"), proc.stderr


# ── 解码（§4.1） ─────────────────────────────────────────────────────────────


def test_decode_gbk_by_meta_http_and_detection(prep):
    text = "第一章 总则：生僻字「堃」与「喆」。" * 20
    gbk_meta = _page(f"<p>{text}</p>", charset="gbk").encode("gb18030")
    decoded, cs, source = prep.decode_html(gbk_meta)
    assert text in decoded and cs == "gb18030" and source == "meta"

    no_decl = _page(f"<p>{text}</p>", charset=None).encode("gb18030")
    decoded, cs, source = prep.decode_html(no_decl, "GBK")  # 只有 HTTP 头声明
    assert text in decoded and source == "http"

    if importlib.util.find_spec("charset_normalizer") is not None:  # 随 [web] 的 trafilatura 安装
        decoded, _cs, source = prep.decode_html(no_decl)  # 什么声明都没有：探测
        assert text in decoded and source == "detected"


def test_stale_charset_meta_dropped(prep):
    """转成 UTF-8 后原来的 `<meta charset=gbk>` 必须去掉，否则后端会按 GBK 再解一遍。"""
    out, _ = prep.prepare_html(_page("<p>正文</p>", charset="gbk").encode("gb18030"), charset=None, base_url=None, extract="off")
    assert 'charset="gbk"' not in out.lower() and "gbk" not in out.lower()
    assert out.startswith('<meta charset="utf-8">')


# ── 正文提取（§4.2） ─────────────────────────────────────────────────────────


def test_extract_removes_nav_footer_and_adds_title(prep):
    pytest.importorskip("trafilatura")
    out, status = prep.extract_main(NAVPAGE, None, "auto")
    assert status == "trafilatura"
    assert "首页" not in out and "京ICP备" not in out
    assert "一、背景" in out and "二、要点" in out
    assert "<h1>示例政策解读</h1>" in out  # 无 <h1> 时用 <title> 补


def test_short_page_falls_back_to_whole(prep):
    """正文很短的页面不能被「提取」掉（漏报类：只测长页测不出）。"""
    pytest.importorskip("trafilatura")
    page = _page('<nav><a href="/">首页</a></nav><p>通知：明天停电。</p>')
    out, status = prep.extract_main(page, None, "auto")
    # 退回整页：页面原样，只在没有 <h1> 时把 <title> 补成 <h1>（整页同样要标题，否则一键入库只能用文件名）
    assert status == "fallback" and "明天停电" in out
    assert out == page.replace("<body>", "<body><h1>示例政策解读</h1>", 1)


def test_extract_off_and_missing_library(prep, no_trafilatura):
    whole = NAVPAGE.replace("<body>", "<body><h1>示例政策解读</h1>", 1)  # 整页不提取，只补标题
    assert prep.extract_main(NAVPAGE, None, "off") == (whole, "off")
    assert prep.extract_main(NAVPAGE, None, "auto") == (whole, "none")


def test_whole_page_without_h1_gets_title(prep, no_trafilatura):
    """整页（off / 未装 trafilatura）同样把 <title> 补成 <h1>：pandoc 丢掉 <head><title>，不补的话标题退成文件名。"""
    page = _page("<p>正文。</p>")
    for mode, status in (("off", "off"), ("auto", "none")):
        out, got = prep.extract_main(page, None, mode)
        assert got == status and "<body><h1>示例政策解读</h1><p>正文。</p>" in out


# ── 图片与链接改写（§4.4） ────────────────────────────────────────────────────


def test_rewrite_images(prep):
    page = (
        '<p><img src="/img/chart.png" alt="增长图" srcset="/img/c2.png 2x"> 见图。</p>'
        '<p><img src="https://cdn.x.org/a.png"></p>'
        '<p><a href="/doc/full.pdf">全文<img src="https://cdn.x.org/icon.png" alt="图标"></a></p>'
        '<picture><source srcset="https://cdn.x.org/b.webp"><img src="data:image/png;base64,AAAA" alt="内嵌"></picture>'
        '<p><img src="local.png" alt="本地" srcset="local2.png 2x"></p>'
    )
    out = prep.rewrite_images(page, "https://www.example.org/news/1.html")
    assert '<a href="https://www.example.org/img/chart.png">图：增长图</a>' in out
    assert '<a href="https://cdn.x.org/a.png">图</a>' in out
    assert '<a href="https://www.example.org/doc/full.pdf">全文图：图标</a>' in out  # 链接里的图只换文字，不嵌套链接
    assert "<source" not in out and "srcset" not in out
    assert 'src="data:image/png;base64,AAAA"' in out  # data: 不动
    assert "<img" in out  # data: 那张
    assert '<a href="https://www.example.org/news/local.png">图：本地</a>' in out


def test_relative_images_kept_without_base(prep):
    out = prep.rewrite_images('<p><img src="local.png" alt="本地"><img src="https://x.org/a.png"></p>', None)
    assert '<img src="local.png" alt="本地">' in out  # 本地另存的网页：相对图片照旧
    assert '<a href="https://x.org/a.png">图</a>' in out


def test_rewrite_preserves_other_markup(prep):
    page = '<!doctype html><!-- c --><p class="a">A &amp; B &#169; <b>粗</b></p><script>if (a<b) x()</script>'
    # 注释与 <script> 丢掉，其余标签、实体、文字照旧
    assert prep.rewrite_images(page, None) == '<!doctype html><p class="a">A &amp; B &#169; <b>粗</b></p>'


def test_rewrite_keeps_bare_ampersands_as_text(prep):
    # 不带分号的 `&T` / `&b` 不是实体：写回成 `&T;` 会改掉正文（AT&T → AT&T;）
    out = prep.rewrite_images("<p>AT&T, R&D, a=1&b=2, &copy 2024</p>", None)
    assert out == "<p>AT&amp;T, R&amp;D, a=1&amp;b=2, &copy; 2024</p>"


def test_rewrite_drops_markup_inside_declarations(prep):
    out = prep.rewrite_images('<?xml <img src="/etc/hosts">?><!DOCTYPE <img src="/etc/hosts">><p>x</p>', None)
    assert "<img" not in out and "<p>x</p>" in out


@pytest.mark.parametrize(
    "page",
    [
        '<p>a</p><img src="/etc/hosts"',  # 文件末尾没闭合的标签：close() 会把它原样当文本冲出去
        '<p>a</p><!--><img src="/etc/hosts">-->',  # HTML5 / pandoc：`<!-->` 即闭合
        '<p>a</p><!-- --!> <img src="/etc/hosts"> -->',  # HTML5 / pandoc：`--!>` 闭合注释
        '<p>a</p><![CDATA[ > <img src="/etc/hosts"> ]]>',  # HTML 里是到第一个 `>` 为止的伪注释
    ],
)
def test_parser_disagreements_cannot_smuggle_img(prep, page):
    """本解析器与 pandoc 对注释 / 未闭合标签的边界判断不一致时，不能让 pandoc 读到一个没经改写的 <img>。"""
    out = prep.rewrite_images(page, None)
    assert "<img" not in out


# ── 端到端（skill 脚本，pandoc 真跑） ─────────────────────────────────────────


@needs_pandoc
def test_pandoc_converts_html_end_to_end(tmp_path):
    pytest.importorskip("trafilatura")
    src = tmp_path / "政策.html"
    src.write_bytes(NAVPAGE.replace('charset="utf-8"', 'charset="gbk"').encode("gb18030"))
    original = src.read_bytes()
    md, err = _run(src, "--html-base-url", "https://www.example.org/news/1.html")
    assert "[done] backend=pandoc" in err and "[html] extract=trafilatura" in err
    assert parse_backend_marker(err) == "pandoc+trafilatura"
    assert md.startswith("# 示例政策解读") and "## 一、背景" in md
    assert "首页" not in md and "京ICP备" not in md
    assert "<div" not in md and "<span" not in md
    assert src.read_bytes() == original  # 用户的文件不被改写
    assert not any(p.name.endswith(".htmlprep") for p in tmp_path.iterdir())  # 预处理目录已清


@needs_pandoc
def test_whole_page_keeps_merged_table_and_drops_wrappers(tmp_path):
    src = tmp_path / "表.html"
    src.write_text(
        _page(
            '<div class="x"><h1>表格</h1><p>正文<span style="color:red">红字</span>。</p>'
            '<table><tr><td rowspan="2">合并</td><td>b</td></tr><tr><td>c</td></tr></table></div>'
        ),
        encoding="utf-8",
    )
    md, err = _run(src, "--html-extract", "off")
    assert "[html] extract=off" in err and parse_backend_marker(err) == "pandoc"
    assert 'rowspan="2"' in md  # 合并单元格保留为 HTML 表格
    assert "<div" not in md and "<span" not in md and "正文红字。" in md


_PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@needs_pandoc
def test_pandoc_keeps_data_uri_image(tmp_path):
    """htmlprep 留下的 `data:` 图片到 pandoc 档仍是图片：Lua 过滤器按扩展名认图，data: 没有扩展名，
    不按 MIME 认的话会被换成说明文字（漏报类：只测带扩展名的图测不出）。"""
    src = tmp_path / "内嵌.html"
    src.write_text(_page(f'<h1>t</h1><p>见图<img alt="点" src="data:image/png;base64,{_PNG_1PX}"></p>'), encoding="utf-8")
    md, _err = _run(src, "--html-extract", "off")
    assert re.search(r"!\[点\]\(media/[0-9a-f]+\.png\)", md), md


def test_fallback_backend_gets_preprocessed_html(tmp_path, monkeypatch, capsys):
    """回退到 MinerU / marker 时送进后端的 HTML 同样已改写：不含远程 <img> 与 srcset（漏报类：只测 pandoc 测不出）。"""
    monkeypatch.syspath_prepend(str(_SCRIPTS))
    conv = _load("skill_convert_html_under_test", _SCRIPT)
    seen: dict = {}

    def fake_backend(input_path, output_dir, args, env_vars):
        seen["text"] = input_path.read_text(encoding="utf-8")
        out = output_dir / input_path.stem / f"{input_path.stem}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# ok\n", encoding="utf-8")
        return out

    monkeypatch.setitem(conv.BACKENDS, "mineru", fake_backend)
    src = tmp_path / "页.html"
    src.write_text(
        _page('<p><img src="https://cdn.x.org/a.png" srcset="https://cdn.x.org/a2.png 2x" alt="甲"></p>'
              '<picture><source srcset="https://cdn.x.org/b.webp"><img src="/b.png"></picture>'),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["convert.py", str(src), "--backend", "mineru", "--html-base-url", "https://e.org/p/1.html", "--html-extract", "off"])
    conv.main()
    text = seen["text"]
    assert "<img" not in text and "srcset" not in text and "<source" not in text
    assert '<a href="https://cdn.x.org/a.png">图：甲</a>' in text
    assert '<a href="https://e.org/b.png">图</a>' in text
    assert "[done] backend=mineru" in capsys.readouterr().err


def test_parsed_by_marker_variants():
    assert parse_backend_marker("[html] extract=none\n[done] backend=pandoc\n") == "pandoc"
    assert parse_backend_marker("[html] extract=fallback\n[done] backend=pandoc\n") == "pandoc"
    assert parse_backend_marker("[html] extract=trafilatura\n[mineru] tier=flash\n[done] backend=mineru\n") == "mineru/flash+trafilatura"
    assert parse_backend_marker("[done] backend=pandoc\n") == "pandoc"  # docx：没有 [html] 行


def test_bom_wins_over_wrong_http_charset(prep):
    """服务器常默认发 `charset=ISO-8859-1`：带 BOM 的 UTF-8 页面按 BOM 解，不能被头部带偏成乱码。"""
    data = b"\xef\xbb\xbf" + _page("<p>中文正文</p>").encode("utf-8")
    text, cs, source = prep.decode_html(data, "ISO-8859-1")
    assert "中文正文" in text and source == "bom"


def test_svg_title_not_used_as_page_title(prep):
    pytest.importorskip("trafilatura")
    page = (
        "<!doctype html><html><head><title>文章标题</title></head><body>"
        '<svg><title>Search</title></svg><svg><title>Menu</title></svg>'
        f"<article><h2>一、背景</h2><p>{PARA}</p><h2>二、要点</h2><p>{PARA}</p></article></body></html>"
    )
    out, status = prep.extract_main(page, None, "auto")
    assert status == "trafilatura" and "<h1>文章标题</h1>" in out and "Search" not in out.split("</h1>")[0]


def test_protocol_relative_image_is_remote_without_base(prep):
    out = prep.rewrite_images('<img src="//cdn.x.org/a.png" alt="甲">', None)
    assert "<img" not in out and "图：甲" in out


def test_local_file_images_and_iframes_neutralized(prep):
    """下载来的网页不能借 pandoc 读本机文件（--extract-media）或替它去抓 <iframe>（绕过地址检查）。"""
    page = (
        '<p><img src="/etc/x.png" alt="绝对"><img src="file:///etc/x.png"><img src="../../x.png">'
        '<img src="ok.png"><iframe src="http://127.0.0.1/s"></iframe></p>'
    )
    out = prep.rewrite_images(page, None)
    assert "/etc/x.png" not in out and "../../x.png" not in out and "<iframe" not in out
    assert "图：绝对" in out
    assert '<img src="ok.png">' in out  # 同目录相对图片照旧
    assert '<a href="http://127.0.0.1/s">' in out


def test_percent_encoded_local_paths_and_base_href_neutralized(prep):
    """pandoc 读本地图片前会做百分号解码、并按 <base href> 解析相对路径：这两条路同样不能读到页面目录之外。"""
    page = (
        '<head><base href="file:///etc/"></head>'
        '<p><img src="%2Fetc%2Fx.png" alt="编码"><img src="%2e%2e/%2e%2e/x.png"><img src="ok.png"></p>'
    )
    out = prep.rewrite_images(page, None)
    assert "<base" not in out
    assert "%2Fetc" not in out and "%2e%2e" not in out
    assert "图：编码" in out
    assert '<img src="ok.png">' in out


@pytest.mark.parametrize("tag", ["iframe", "xmp", "noembed", "noframes", "title", "textarea"])
def test_images_inside_raw_text_elements_rewritten(prep, tag):
    """新版 Python 的 HTMLParser 把这些元素的内容当原文直接交 handle_data，pandoc 却照样解析其中的标签：
    `<iframe><img src="/etc/hosts"></iframe>` 不能借此绕过改写、让 --extract-media 拷走本机文件。"""
    out = prep.rewrite_images(f'<{tag}><img src="/etc/hosts" alt="甲"><img src="http://10.0.0.1/a.png"></{tag}>', None)
    assert "<img" not in out and "/etc/hosts" not in out


@pytest.mark.parametrize(
    "src",
    ["%252Fetc%252Fhosts", "C:/x.png", "C%3A%5Cx.png", "~/x.png", "a/../../x.png", "\\\\srv\\share\\x.png", "x.png?y=1"],
)
def test_local_image_whitelist_rejects_odd_paths(prep, src):
    """本机图片只放行「同目录、简单字符」的相对路径：黑名单总会漏掉某种编码，故用白名单。"""
    out = prep.rewrite_images(f'<p><img src="{src}" alt="甲"></p>', None)
    assert "<img" not in out and "图：甲" in out


@pytest.mark.parametrize("src", ["a.png", "img/图 1.png", "sub/x-y_z.jpeg"])
def test_local_image_whitelist_keeps_plain_paths(prep, src):
    out = prep.rewrite_images(f'<p><img src="{src}" alt="甲"></p>', None)
    assert f'src="{src}"' in out


def test_ratio_check_works_without_closing_head(prep):
    """省略 `</head>` 是合法 HTML：可见字数不能因此算成 0、让 10% 下限失效（漏报类）。"""
    pytest.importorskip("trafilatura")
    body = f"<p>{PARA}</p>" * 40
    page = f'<html><head><meta charset="utf-8"><title>t</title><body><nav>首页</nav>{body}<article><p>{PARA}</p></article></body></html>'
    visible, _title = prep._scan(page)
    assert visible > 1000


@pytest.mark.parametrize("label", ["ISO-8859-1", "latin1", "us-ascii"])
def test_latin1_labels_decoded_as_windows_1252(prep, label):
    """WHATWG（浏览器）把 latin-1 / ascii 标签当 windows-1252：智能引号 0x93/0x94 不能变成看不见的 C1 控制字符。"""
    data = b"<html><body><p>\x93quoted\x94 caf\xe9</p></body></html>"
    text, _cs, _src = prep.decode_html(data, label)
    assert "“quoted” café" in text
    assert not any(0x80 <= ord(c) <= 0x9F for c in text)


@pytest.mark.parametrize("label", ["base64", "rot13", "idna", "punycode", "utf-16", "UTF-32LE"])
def test_bogus_or_utf16_meta_charset_reads_as_utf8(prep, label):
    """`<meta charset>` 写成非文本编码不能让转换崩溃；无 BOM 的 utf-16/32 声明按 UTF-8 读（WHATWG）。"""
    data = f'<html><head><meta charset="{label}"></head><body><p>中文正文</p></body></html>'.encode("utf-8")
    text, cs, _src = prep.decode_html(data)
    assert "中文正文" in text and cs == "utf-8"


@needs_pandoc
def test_sibling_image_kept_when_converting_directly(tmp_path):
    """直接用 skill 转本地另存的网页：同目录图片照样导出（pandoc 的资源路径指向网页所在目录）。"""
    import base64

    (tmp_path / "a.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="))
    src = tmp_path / "p.html"
    src.write_text(_page('<h1>标题</h1><p>正文。</p><p><img src="a.png" alt="甲"></p>'), encoding="utf-8")
    md, _err = _run(src, "--html-extract", "off")
    assert "![甲](media/" in md
    assert not any(p.name.startswith(".htmlprep") for p in tmp_path.iterdir())


def test_stylesheets_css_and_resource_attrs_removed(prep):
    """回退到 marker（WeasyPrint 渲染）时会取 <link> 样式表、CSS url(...)、SVG <image href>：预处理一并去掉。"""
    page = (
        '<html><head><link rel="stylesheet" href="http://169.254.169.254/x.css">'
        "<style>body{background:url(file:///Users/x/secret.png)}</style></head>"
        '<body style="background:url(http://10.0.0.1/a.png)" background="http://10.0.0.1/b.png">'
        '<svg><image href="http://10.0.0.1/c.png"/><use xlink:href="#icon"/></svg>'
        '<video poster="http://10.0.0.1/p.png"><source src="http://10.0.0.1/v.mp4"></video>'
        '<p><a href="https://e.org/doc" style="color:red">链接</a></p></body></html>'
    )
    out = prep.rewrite_images(page, None)
    for bad in ("169.254", "10.0.0.1", "file:", "<link", "<style", "style="):
        assert bad not in out, bad
    assert 'xlink:href="#icon"' in out  # 页内引用保留
    assert '<a href="https://e.org/doc">链接</a>' in out


def test_base_href_used_only_as_http_base(prep):
    out = prep.rewrite_images('<head><base href="https://cdn.example/sub/"></head><img src="a.png">', "https://example.com/page")
    assert '<a href="https://cdn.example/sub/a.png">图</a>' in out and "<base" not in out
    out = prep.rewrite_images('<head><base href="file:///etc/"></head><img src="hosts.png" alt="甲">', None)
    assert "file:" not in out and 'src="hosts.png"' in out


@pytest.mark.parametrize("src", [".env", ".git/config.png", ".x.html.source.json", "sub/.hidden.png"])
def test_dot_segments_not_local_images(prep, src):
    out = prep.rewrite_images(f'<img src="{src}" alt="甲">', None)
    assert "<img" not in out and "图：甲" in out


def test_unclosed_link_does_not_swallow_later_images(prep):
    """`<a>` 不能嵌套：没闭合的 `<a>` 不能让后面所有远程图片都只剩说明文字。"""
    out = prep.rewrite_images('<a href="#x">1<a href="#y">2</a><p><img src="https://x.org/a.png" alt="甲"></p>', None)
    assert '<a href="https://x.org/a.png">图：甲</a>' in out


def test_charset_starting_with_dash_not_parsed_as_option(monkeypatch, tmp_path):
    """服务器给的 charset 以 `-` 开头（如 `--html-extract`）：必须作为参数值、不能被 argparse 当成选项。"""
    from guanlan import convert as conv

    seen = {}

    def fake_run(cmd, *, cwd, progress):
        seen["cmd"] = cmd
        return 1, "", "boom"

    monkeypatch.setattr(conv, "_run_converter", fake_run)
    src = tmp_path / "p.html"
    src.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(conv.ConvertError):
        conv.convert_to_markdown(src, stem="p", cwd=tmp_path, html_charset="--html-extract", html_base_url="-x")
    assert "--html-charset=--html-extract" in seen["cmd"] and "--html-base-url=-x" in seen["cmd"]


@needs_pandoc
def test_works_with_pythonsafepath(tmp_path):
    """用户环境设了 PYTHONSAFEPATH（转换子进程会继承）：脚本目录不在 sys.path 上，HTML 预处理照样能导入。"""
    import os

    src = tmp_path / "p.html"
    src.write_text(_page("<h1>标题</h1><p>正文。</p>"), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), str(src), "--html-extract", "off"],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONSAFEPATH": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "[done] backend=pandoc" in proc.stderr


@needs_pandoc
def test_folder_with_path_separator_does_not_widen_image_search(tmp_path):
    """目录名含 `:`（POSIX 的路径分隔符）：不传 --resource-path，pandoc 不会去别的目录找相对图片。"""
    import os

    weird = tmp_path / f"A{os.pathsep}B"
    weird.mkdir()
    src = weird / "p.html"
    src.write_text(_page('<h1>t</h1><p><img src="a.png" alt="甲"></p>'), encoding="utf-8")
    md, err = _run(src, "--html-extract", "off")
    assert "--resource-path" not in err


def test_output_is_reserialized_not_copied(prep):
    """标签按解析结果重建、文字一律转义：解析器当成文字的 `<` 不能在 pandoc 那里变成标签。"""
    out = prep.rewrite_images("<p>a &lt; b</p><p>x < img src=/etc/hosts></p><P CLASS='q'>y</P>", None)
    assert "<img" not in out and "&lt; img" in out
    assert '<p class="q">y</p>' in out
    assert prep.rewrite_images("<script>var s='<img src=/etc/hosts>'</script><p>ok</p>", None) == "<p>ok</p>"


def test_html_options_ignored_for_non_html(monkeypatch, tmp_path):
    """一键入库总是传 HTML 选项；只对 .html/.htm 生效由转换层判断——PDF 的命令行里不能出现它们。"""
    from guanlan import convert as conv

    seen = {}

    def fake_run(cmd, *, cwd, progress):
        seen["cmd"] = cmd
        return 1, "", "boom"

    monkeypatch.setattr(conv, "_run_converter", fake_run)
    src = tmp_path / "r.pdf"
    src.write_bytes(b"%PDF")
    with pytest.raises(conv.ConvertError):
        conv.convert_to_markdown(src, stem="r", cwd=tmp_path, html_charset="gbk", html_base_url="https://e.org/", html_extract="off")
    assert not any(a.startswith("--html") for a in seen["cmd"])


def test_unknown_marked_section_does_not_abort(prep):
    # Python 3.10–3.12 的 `_markupbase` 对 `<![foo[` 抛 AssertionError：按 HTML5 当伪注释到第一个 `>`，整页照转
    text, _ = prep.prepare_html(
        b'<html><body><![foo[ x ]]><p>hi</p><![if x]><img src="/etc/hosts"><![endif]></body></html>',
        charset=None, base_url=None, extract="off",
    )
    assert "<p>hi</p>" in text and "/etc/hosts" not in text


def test_rewrite_images_drops_repeated_src(prep):
    """重复属性只留第一个：检查只看了第一个 `src`，第二个 `src="/etc/hosts"` 不能写回去给转换器读。"""
    out = prep.rewrite_images('<img src="local.png" src="/etc/hosts" alt="x">', None)
    assert "/etc/hosts" not in out
    assert 'src="local.png"' in out


def test_self_closing_script_and_style_still_hide_their_text(prep):
    """`<script/>` / `<style/>` 在 HTML 里照样开启原始文本元素：其后的脚本 / CSS 不能变成正文，也不能算进正文字数。"""
    page = "<p>正文</p><script/>var secret = 1;</script><style/>body{color:red}</style><p>后文</p>"
    out = prep.rewrite_images(page, None)
    assert "secret" not in out and "color" not in out and "后文" in out
    assert prep._scan("<script/>" + "x" * 300 + "</script><p>hi</p>")[0] == 2
