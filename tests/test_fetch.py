"""P4.31 网址下载器 `guanlan/fetch.py`：零网络（httpx.MockTransport + 假解析器）。"""

from __future__ import annotations

import io
import os
import json
import threading
import zipfile
from pathlib import Path

import httpx
import pytest

from guanlan import fetch
from guanlan.fetch import FetchCancelled, FetchError, check_url, fetch_url
from guanlan.sourcerec import read_source_record, source_record_path

PDF = b"%PDF-1.7\n" + b"x" * 100
HTML = b"<!doctype html><html><head><title>t</title></head><body>hi</body></html>"
PUBLIC = "93.184.216.34"


def _ooxml(main: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", f'<Types><Override ContentType="application/vnd.openxmlformats-officedocument.{main}+xml"/></Types>')
        zf.writestr("word/document.xml", "<x/>")
    return buf.getvalue()


class Site:
    """按 Host 头路由的假网站，记录收到的请求。"""

    def __init__(self, routes: dict[str, httpx.Response | callable]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = f"{request.headers['host']}{request.url.raw_path.decode()}"
        r = self.routes.get(key)
        if r is None:
            return httpx.Response(404)
        return r(request) if callable(r) else r


def _resolver(table: dict[str, list[str]]):
    calls: list[str] = []

    def resolve(host: str, port: int) -> list[str]:
        calls.append(host)
        return table.get(host, [PUBLIC])

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


def _get(url, tmp_path, site, resolver=None, **kw):
    return fetch_url(url, tmp_path, transport=httpx.MockTransport(site), resolver=resolver or _resolver({}), **kw)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path_factory):
    monkeypatch.delenv(fetch.ALLOW_CIDRS_ENV, raising=False)
    for name in _PROXY_NAMES:  # 开发机可能设了系统代理：用例默认直连，需要代理的自己设
        monkeypatch.delenv(name, raising=False)
    # agentao 配置会从当前目录往上找 .env（开发机的 ~/.env 可能配了 jina / 放行网段）：进程环境清掉、当前目录换到
    # 一个上面没有 .env 的临时目录，否则 403 用例会真的把网址发给 r.jina.ai 或起浏览器。需要配置的用例自己设。
    monkeypatch.delenv(fetch.FALLBACK_ENV, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


_PROXY_NAMES = ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY")


def _leftovers(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir())


# ── 网址语法 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    ["ftp://e.org/a.pdf", "file:///etc/passwd", "https://user:pw@e.org/a.pdf", "https://user@e.org/", "https:///a", "", "https://e.org/" + "a" * 3000],
)
def test_bad_urls_rejected(url):
    with pytest.raises(FetchError):
        check_url(url)


# ── 网段检查（§3） ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "addr",
    ["127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1", "2002:7f00:1::", "64:ff9b::a9fe:a9fe", "::7f00:1", "::ffff:0:7f00:1", "64:ff9b:1::a9fe:a9fe"],
)
def test_private_targets_blocked(tmp_path, addr):
    site = Site({"e.org/a.pdf": httpx.Response(200, content=PDF)})
    with pytest.raises(FetchError, match="本机或内网"):
        _get("https://e.org/a.pdf", tmp_path, site, _resolver({"e.org": [addr]}))
    assert site.requests == []  # 连请求都没发
    assert _leftovers(tmp_path) == []


def test_any_private_address_blocks(tmp_path):
    """解析出多个地址时任一非公网即拒：不能挑公网那个连、放过内网那个。"""
    site = Site({"e.org/a.pdf": httpx.Response(200, content=PDF)})
    with pytest.raises(FetchError):
        _get("https://e.org/a.pdf", tmp_path, site, _resolver({"e.org": [PUBLIC, "10.1.1.1"]}))


def test_literal_ip_blocked_without_resolving(tmp_path):
    res = _resolver({})
    with pytest.raises(FetchError):
        _get("http://127.0.0.1:8080/a.pdf", tmp_path, Site({}), res)
    assert res.calls == []


def test_allow_cidrs_env(tmp_path, monkeypatch):
    monkeypatch.setenv(fetch.ALLOW_CIDRS_ENV, "10.0.0.0/8")
    site = Site({"intra.corp/a.pdf": httpx.Response(200, content=PDF)})
    r = _get("http://intra.corp/a.pdf", tmp_path, site, _resolver({"intra.corp": ["10.2.3.4"]}))
    assert r.kind == "pdf"
    r.tmp.unlink()


def test_fake_ip_range_needs_allow_setting(tmp_path, monkeypatch):
    """fake-ip 代理把域名都解析到 198.18.0.0/15：不再写死放行，须在 AGENTAO_WEB_FETCH_ALLOW_CIDRS 里配。"""
    site = Site({"e.org/a.pdf": httpx.Response(200, content=PDF)})
    with pytest.raises(FetchError, match="AGENTAO_WEB_FETCH_ALLOW_CIDRS"):
        _get("https://e.org/a.pdf", tmp_path, site, _resolver({"e.org": ["198.18.0.39"]}))
    monkeypatch.setenv(fetch.ALLOW_CIDRS_ENV, "198.18.0.0/15")
    r = _get("https://e.org/a.pdf", tmp_path, site, _resolver({"e.org": ["198.18.0.39"]}))
    assert r.kind == "pdf"
    r.tmp.unlink()


def test_allow_setting_read_from_kb_dotenv(tmp_path, monkeypatch):
    """Web 宿主可能在任何 Agent 构造之前就下载：`<库>/.env` 里的配置要直接读到（与 agentao 同一查找顺序）。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".env").write_text("AGENTAO_WEB_FETCH_ALLOW_CIDRS=198.18.0.0/15, 10.0.0.0/8\nAGENTAO_WEB_FETCH_FALLBACK=Jina\n", encoding="utf-8")
    nets = {str(n) for n in fetch.allow_networks(kb)}
    assert nets == {"198.18.0.0/15", "10.0.0.0/8"}
    assert fetch.fallback_mode(kb) == "jina"
    site = Site({"e.org/a.pdf": httpx.Response(200, content=PDF)})
    r = fetch_url("https://e.org/a.pdf", tmp_path, transport=httpx.MockTransport(site),
                  resolver=_resolver({"e.org": ["198.18.0.39"]}), env_root=kb)
    assert r.kind == "pdf"
    r.tmp.unlink()


def test_process_env_wins_over_dotenv(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".env").write_text("AGENTAO_WEB_FETCH_FALLBACK=jina\n", encoding="utf-8")
    monkeypatch.setenv(fetch.FALLBACK_ENV, "none")
    assert fetch.fallback_mode(kb) == "none"


def test_dotenv_found_by_walking_up_from_cwd(tmp_path, monkeypatch):
    """库里没有 .env：与 agentao 一样从当前目录往上找（如 ~/.env）。"""
    (tmp_path / ".env").write_text("AGENTAO_WEB_FETCH_ALLOW_CIDRS=198.18.0.0/15\n", encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    kb = tmp_path / "kb-without-env"
    kb.mkdir()
    assert [str(n) for n in fetch.allow_networks(kb)] == ["198.18.0.0/15"]


def test_redirect_to_private_blocked(tmp_path):
    """第一跳公网、跳转到内网：逐跳检查必须拦下（只测正例测不出这条漏报）。"""
    site = Site({"e.org/a.pdf": httpx.Response(302, headers={"location": "http://internal.example/secret"})})
    with pytest.raises(FetchError, match="本机或内网"):
        _get("https://e.org/a.pdf", tmp_path, site, _resolver({"internal.example": ["127.0.0.1"]}))
    assert [r.headers["host"] for r in site.requests] == ["e.org"]
    assert _leftovers(tmp_path) == []


def test_connection_pinned_to_resolved_address(tmp_path):
    """用解析出的地址建连接（防 DNS 重绑定），Host 头与 SNI 仍是原主机名；每主机只解析一次。"""
    site = Site({"e.org/a.pdf": httpx.Response(200, content=PDF)})
    res = _resolver({"e.org": [PUBLIC]})
    r = _get("https://e.org/a.pdf", tmp_path, site, res)
    (req,) = site.requests
    assert req.url.host == PUBLIC
    assert req.headers["host"] == "e.org"
    assert req.extensions.get("sni_hostname") == "e.org"
    assert res.calls == ["e.org"]
    assert "guanlan/" in req.headers["user-agent"] and "cookie" not in req.headers
    r.tmp.unlink()


@pytest.fixture
def spy_proxy(monkeypatch):
    """记下每一跳选的代理（测试注入了 transport，真实连接仍走 MockTransport）。"""
    seen: list = []
    real = fetch._client

    def spy(proxy, transport):
        seen.append(proxy)
        return real(proxy, transport)

    monkeypatch.setattr(fetch, "_client", spy)
    return seen


def test_standard_proxy_vars_used_like_agentao(tmp_path, monkeypatch, spy_proxy):
    """与 agentao 的 web_fetch 一样读标准代理变量：走代理时由代理连主机名，不再固定到解析出的 IP。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
    site = Site({"e.org/a.pdf": httpx.Response(200, content=PDF)})
    r = _get("https://e.org/a.pdf", tmp_path, site)
    assert spy_proxy == ["http://proxy.corp:3128"]
    (req,) = site.requests
    assert req.url.host == "e.org" and "sni_hostname" not in req.extensions
    r.tmp.unlink()


def test_no_proxy_hosts_connect_directly_and_pinned(tmp_path, monkeypatch, spy_proxy):
    monkeypatch.setenv("https_proxy", "http://proxy.corp:3128")
    monkeypatch.setenv("no_proxy", "localhost, .e.org")
    site = Site({"cdn.e.org/a.pdf": httpx.Response(200, content=PDF)})
    r = _get("https://cdn.e.org/a.pdf", tmp_path, site)
    assert spy_proxy == [None]
    assert site.requests[0].url.host == PUBLIC  # 直连照旧固定到解析出的地址
    r.tmp.unlink()


def test_proxy_still_checks_addresses_locally(tmp_path, monkeypatch):
    """与 agentao 同口径：走代理也先在本机解析并检查，解析到内网的域名与写明的内网地址都拒。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp:3128")
    site = Site({})
    with pytest.raises(FetchError, match="本机或内网"):
        _get("https://intra.e.org/a.pdf", tmp_path, site, _resolver({"intra.e.org": ["10.1.2.3"]}))
    for bad in ("https://localhost/a.pdf", "http://10.0.0.1/a.pdf"):
        with pytest.raises(FetchError):
            _get(bad, tmp_path, site)
    assert site.requests == []


@pytest.mark.parametrize(
    ("env", "url", "want"),
    [
        ({"https_proxy": "http://lower:1", "HTTPS_PROXY": "http://upper:1"}, "https://e.org/", "http://lower:1"),
        ({"ALL_PROXY": "socks5://all:1"}, "http://e.org/", "socks5://all:1"),
        ({"HTTP_PROXY": "http://h:1"}, "https://e.org/", None),  # https 不走 http_proxy
        ({"HTTPS_PROXY": "http://p:1", "NO_PROXY": "*"}, "https://e.org/", None),
        ({"HTTPS_PROXY": "http://p:1", "NO_PROXY": "10.0.0.0/8"}, "https://10.2.3.4/", None),
        ({"HTTPS_PROXY": "http://p:1", "NO_PROXY": "other.org"}, "https://e.org/", "http://p:1"),
        ({"HTTPS_PROXY": "http://p:1", "NO_PROXY": "e.org:443"}, "https://sub.e.org/", None),
        # 不带协议的写法（Clash 等常见）：与 httpx 读环境变量同口径补 http://，否则 httpx 构造客户端时抛 ValueError
        ({"https_proxy": "127.0.0.1:7890"}, "https://e.org/", "http://127.0.0.1:7890"),
    ],
)
def test_proxy_for_rules(monkeypatch, env, url, want):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert fetch.proxy_for(url) == want


def test_proxy_read_from_kb_dotenv(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".env").write_text("HTTPS_PROXY=http://from-dotenv:3128\n", encoding="utf-8")
    assert fetch.proxy_for("https://e.org/", kb) == "http://from-dotenv:3128"


# ── 限额与失败 ───────────────────────────────────────────────────────────────


def test_too_many_redirects(tmp_path):
    site = Site({"e.org/a": httpx.Response(302, headers={"location": "/a"})})
    with pytest.raises(FetchError, match="跳转超过"):
        _get("https://e.org/a", tmp_path, site)
    assert len(site.requests) == fetch.MAX_REDIRECTS + 1


def test_size_limit_from_header_and_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_BYTES", 50)
    site = Site({"e.org/big.pdf": httpx.Response(200, content=PDF)})  # Content-Length 109 > 50
    with pytest.raises(FetchError, match="太大"):
        _get("https://e.org/big.pdf", tmp_path, site)

    def chunked(request):  # 没有 Content-Length：流式累计超限
        return httpx.Response(200, content=iter([PDF[:40], PDF[40:]]))

    site = Site({"e.org/big.pdf": chunked})
    with pytest.raises(FetchError, match="超过上限"):
        _get("https://e.org/big.pdf", tmp_path, site)
    assert _leftovers(tmp_path) == []


def test_http_error_and_empty(tmp_path):
    with pytest.raises(FetchError, match="HTTP 404"):
        _get("https://e.org/a.pdf", tmp_path, Site({"e.org/a.pdf": httpx.Response(404)}))
    with pytest.raises(FetchError, match="空文件"):
        _get("https://e.org/a.pdf", tmp_path, Site({"e.org/a.pdf": httpx.Response(200, content=b"")}))
    assert _leftovers(tmp_path) == []


def test_cancel_midway_removes_temp(tmp_path):
    ev = threading.Event()

    def body():
        yield PDF[:20]
        ev.set()
        yield PDF[20:]

    site = Site({"e.org/a.pdf": lambda req: httpx.Response(200, content=body())})
    with pytest.raises(FetchCancelled):
        _get("https://e.org/a.pdf", tmp_path, site, cancel=ev)
    assert _leftovers(tmp_path) == []


def test_cancel_takes_effect_per_received_chunk(tmp_path):
    """慢速连接上停止要随收到的每一块生效，不能等攒满 1 MiB（回归：iter_bytes 曾给了 chunk_size）。"""
    ev = threading.Event()
    produced = []

    def body():
        for i in range(10):
            produced.append(i)
            if i == 1:
                ev.set()
            yield b"%PDF-" if i == 0 else b"0" * 10

    site = Site({"e.org/a.pdf": lambda req: httpx.Response(200, content=body())})
    with pytest.raises(FetchCancelled):
        _get("https://e.org/a.pdf", tmp_path, site, cancel=ev)
    assert len(produced) <= 3


def test_progress_reported(tmp_path):
    seen = []
    site = Site({"e.org/a.pdf": httpx.Response(200, content=PDF)})
    r = _get("https://e.org/a.pdf", tmp_path, site, progress=lambda n, total: seen.append((n, total)))
    assert seen[-1] == (len(PDF), len(PDF))
    r.tmp.unlink()


# ── 类型判定（§2.3） ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "body", "ctype", "kind", "name"),
    [
        ("/r/report.pdf", PDF, "application/pdf", "pdf", "report.pdf"),
        ("/d?id=1", _ooxml("wordprocessingml.document.main"), "application/octet-stream", "docx", None),
        ("/s.pptx", _ooxml("presentationml.presentation.main"), None, "pptx", "s.pptx"),
        ("/t.xlsx", _ooxml("spreadsheetml.sheet.main"), None, "xlsx", "t.xlsx"),
        ("/news/1.htm", HTML, "text/html; charset=gbk", "html", "1.html"),
        ("/pic", b"\x89PNG\r\n\x1a\n" + b"0" * 20, "image/png", "png", None),
        ("/a.jpeg", b"\xff\xd8\xff\xe0" + b"0" * 20, None, "jpeg", "a.jpg"),
        ("/README.md", "# 标题\n\n正文。\n".encode(), "text/plain", "md", "README.md"),
        ("/notes", "# 标题\n".encode(), "text/markdown", "md", None),
    ],
)
def test_kind_detection(tmp_path, path, body, ctype, kind, name):
    headers = {"content-type": ctype} if ctype else {}
    site = Site({f"e.org{path}": httpx.Response(200, content=body, headers=headers)})
    r = _get(f"https://e.org{path}", tmp_path, site)
    assert r.kind == kind
    assert r.suggested_name.endswith(fetch.KIND_EXT[kind])
    if name:
        assert r.suggested_name == name
    r.tmp.unlink()


def test_html_records_http_charset(tmp_path):
    site = Site({"e.org/1.html": httpx.Response(200, content=HTML, headers={"content-type": "text/html; charset=GBK"})})
    r = _get("https://e.org/1.html", tmp_path, site)
    assert r.record["charset"] == "GBK" and r.record["content_type"] == "text/html"
    r.tmp.unlink()


@pytest.mark.parametrize("path", ["/report.pdf", "/report.docx"])
def test_login_page_instead_of_document_rejected(tmp_path, path):
    """网址说是 PDF / Word，实际返回网页（登录页、软 404）：拒绝，不让它以文档之名进管线。"""
    site = Site({f"e.org{path}": httpx.Response(200, content=HTML, headers={"content-type": "text/html"})})
    with pytest.raises(FetchError, match="返回的是网页"):
        _get(f"https://e.org{path}", tmp_path, site)
    assert _leftovers(tmp_path) == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/book.epub", None),  # zip 但不是 OOXML
        ("/data.csv", b"a,b\n1,2\n"),
        ("/old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 50),
        ("/x.zip", None),
        ("/a.rtf", b"{\\rtf1 hello}"),
    ],
)
def test_unsupported_formats_point_to_upload(tmp_path, path, body):
    if body is None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
        body = buf.getvalue()
    site = Site({f"e.org{path}": httpx.Response(200, content=body)})
    with pytest.raises(FetchError, match="先下载到本地"):
        _get(f"https://e.org{path}", tmp_path, site)
    assert _leftovers(tmp_path) == []


# ── 文件名（§2.4） ───────────────────────────────────────────────────────────


def test_name_from_content_disposition_rfc5987(tmp_path):
    cd = "attachment; filename=\"x.pdf\"; filename*=UTF-8''%E5%B9%B4%E5%BA%A6%E6%8A%A5%E5%91%8A.pdf"
    site = Site({"e.org/dl": httpx.Response(200, content=PDF, headers={"content-disposition": cd})})
    r = _get("https://e.org/dl", tmp_path, site)
    assert r.suggested_name == "年度报告.pdf"
    r.tmp.unlink()


def test_name_from_url_percent_decoded_and_fallback(tmp_path):
    site = Site({"e.org/files/%E6%94%BF%E7%AD%96.pdf": httpx.Response(200, content=PDF)})
    r = _get("https://e.org/files/%E6%94%BF%E7%AD%96.pdf", tmp_path, site)
    assert r.suggested_name == "政策.pdf"
    r.tmp.unlink()
    site = Site({"e.org/": httpx.Response(200, content=HTML)})
    r = _get("https://e.org/", tmp_path, site)
    assert r.suggested_name == "e-org.html"
    r.tmp.unlink()


def test_redirect_final_url_recorded(tmp_path):
    site = Site(
        {
            "e.org/r": httpx.Response(301, headers={"location": "https://cdn.e.org/f/report.pdf"}),
            "cdn.e.org/f/report.pdf": httpx.Response(200, content=PDF),
        }
    )
    r = _get("https://e.org/r", tmp_path, site)
    assert r.record["url"] == "https://e.org/r"
    assert r.record["final_url"] == "https://cdn.e.org/f/report.pdf"
    assert r.suggested_name == "report.pdf"
    assert r.record["bytes"] == len(PDF) and len(r.record["sha256"]) == 64
    r.tmp.unlink()


# ── 出处记录（§2.5） ─────────────────────────────────────────────────────────


def test_source_record_invalidated_when_file_replaced(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(PDF)
    import hashlib

    rec = {"url": "https://e.org/a.pdf", "final_url": "https://e.org/a.pdf", "sha256": hashlib.sha256(PDF).hexdigest(), "bytes": len(PDF)}
    source_record_path(f).write_text(json.dumps(rec), encoding="utf-8")
    assert read_source_record(f)["url"] == "https://e.org/a.pdf"
    f.write_bytes(b"%PDF-other")  # 被同名上传覆盖
    assert read_source_record(f) is None
    assert not source_record_path(f).exists()  # 作废即删


# ── 评审修复的回归用例 ───────────────────────────────────────────────────────


def test_falls_back_to_next_address_on_connect_error(tmp_path):
    """解析出多个（都合规的）地址：第一个连不上（如无 IPv6 路由）就换下一个，不直接判失败。"""
    tried = []

    def handler(request):
        tried.append(request.url.host)
        if request.url.host == "2606:2800:220:1:248:1893:25c8:1946":
            raise httpx.ConnectError("no route")
        return httpx.Response(200, content=PDF)

    r = fetch_url(
        "https://e.org/a.pdf", tmp_path, transport=httpx.MockTransport(handler),
        resolver=_resolver({"e.org": ["2606:2800:220:1:248:1893:25c8:1946", PUBLIC]}),
    )
    assert r.kind == "pdf" and tried == ["2606:2800:220:1:248:1893:25c8:1946", PUBLIC]
    r.tmp.unlink()


@pytest.mark.parametrize(
    ("url", "host"),
    [("https://例子.中国/a.pdf", "xn--fsqu00a.xn--fiqs8s"), ("https://例子.中国:8443/a.pdf", "xn--fsqu00a.xn--fiqs8s:8443")],
)
def test_idn_host_header_is_punycode(tmp_path, url, host):
    site = Site({f"{host}/a.pdf": httpx.Response(200, content=PDF)})
    r = _get(url, tmp_path, site)
    assert site.requests[0].headers["host"] == host  # 端口不被当成域名的一部分去编码
    r.tmp.unlink()


def test_redirect_without_location_is_error_not_body(tmp_path):
    site = Site({"e.org/a.pdf": httpx.Response(302, content=b"%PDF-moved")})
    with pytest.raises(FetchError, match="没给新地址"):
        _get("https://e.org/a.pdf", tmp_path, site)
    assert _leftovers(tmp_path) == []


def test_long_name_capped_in_bytes(tmp_path):
    """Linux 文件名按字节限 255：长中文名截到 180 字节，出处记录 `.<名>.source.json` 也放得下。"""
    long = "很长的报告标题" * 20 + ".pdf"  # 远超 255 字节
    cd = "attachment; filename*=UTF-8''" + __import__("urllib.parse").parse.quote(long)
    site = Site({"e.org/dl": httpx.Response(200, content=PDF, headers={"content-disposition": cd})})
    r = _get("https://e.org/dl", tmp_path, site)
    assert r.suggested_name.endswith(".pdf")
    assert len(r.suggested_name.encode("utf-8")) <= 184
    assert len(f".{r.suggested_name}.source.json".encode("utf-8")) <= 255
    r.suggested_name.encode("utf-8").decode("utf-8")  # 没切坏字符
    r.tmp.unlink()


def test_read_source_record_accepts_known_hash(tmp_path):
    import hashlib

    f = tmp_path / "a.pdf"
    f.write_bytes(PDF)
    sha = hashlib.sha256(PDF).hexdigest()
    source_record_path(f).write_text(json.dumps({"url": "u", "final_url": "u", "sha256": sha}), encoding="utf-8")
    assert read_source_record(f, sha256=sha) is not None
    assert read_source_record(f, sha256="0" * 64) is None


@pytest.mark.parametrize(
    ("url", "want"),
    [
        (
            "https://b.s3.amazonaws.com/r.pdf?X-Amz-Algorithm=AWS4&X-Amz-Credential=AK%2F1&X-Amz-Date=1&X-Amz-Expires=600&X-Amz-Signature=abc&X-Amz-Security-Token=t",
            "https://b.s3.amazonaws.com/r.pdf?X-Amz-Algorithm=AWS4&X-Amz-Date=1&X-Amz-Expires=600",
        ),
        ("https://oss.example.com/r.pdf?OSSAccessKeyId=AK&Expires=1&Signature=s%3D", "https://oss.example.com/r.pdf?Expires=1"),
        ("https://acct.blob.core.windows.net/c/r.pdf?sv=2024&se=2026&sp=r&sig=abc", "https://acct.blob.core.windows.net/c/r.pdf?sv=2024&sp=r"),
        ("https://e.org/dl?id=42&access_token=xyz&page=2#p3", "https://e.org/dl?id=42&page=2#p3"),
        ("https://e.org/news/1.html", "https://e.org/news/1.html"),
        # OAuth 隐式授权把令牌放在锚点里：`k=v` 形式的锚点同样过滤，普通锚点不动
        ("https://e.org/doc#access_token=xyz&state=1", "https://e.org/doc#state=1"),
    ],
)
def test_redact_url_drops_credentials_only(url, want):
    from guanlan.sourcerec import redact_url

    assert redact_url(url) == want


def test_stale_record_kept_when_not_discarding(tmp_path):
    """`import --dry-run` 只判不删：过期的出处记录留在盘上。"""
    f = tmp_path / "a.pdf"
    f.write_bytes(PDF)
    source_record_path(f).write_text(json.dumps({"url": "u", "final_url": "u", "sha256": "0" * 64}), encoding="utf-8")
    assert read_source_record(f, discard_stale=False) is None
    assert source_record_path(f).exists()
    assert read_source_record(f) is None and not source_record_path(f).exists()


@pytest.mark.parametrize("url", ["http://127.1:8765/a", "http://2130706433/a", "http://0x7f.0.0.1/a", "http://localhost./a", "http://LOCALHOST/a", "http://10.1/a"])
@pytest.mark.parametrize("proxied", [False, True])
def test_short_and_dotted_local_forms_rejected(tmp_path, monkeypatch, url, proxied):
    """网址里写的 `127.1`、十进制 / 十六进制 IPv4、带尾点的 localhost 都要认出来——直连、走代理都一样。"""
    if proxied:
        monkeypatch.setenv("HTTP_PROXY", "http://p:1")
    site = Site({})
    with pytest.raises(FetchError):
        _get(url, tmp_path, site)
    assert site.requests == []


@pytest.mark.parametrize("url", ["https://example.com/a.pdf", "https://8.8.8.8/a.pdf"])
def test_proxy_mode_allows_public(tmp_path, monkeypatch, url):
    monkeypatch.setenv("HTTPS_PROXY", "http://p:1")
    r = fetch_url(url, tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, content=PDF)), resolver=_resolver({}))
    assert r.kind == "pdf"
    r.tmp.unlink()


def test_name_capped_after_nfkc(tmp_path):
    """「㍿」NFKC 后从 3 字节变 12 字节：截短必须在规范化之后。"""
    cd = "attachment; filename*=UTF-8''" + __import__("urllib.parse").parse.quote("㍿" * 60 + ".pdf")
    site = Site({"e.org/dl": httpx.Response(200, content=PDF, headers={"content-disposition": cd})})
    r = _get("https://e.org/dl", tmp_path, site)
    assert len(r.suggested_name.encode("utf-8")) <= 184
    r.tmp.unlink()


def test_markdown_url_returning_page_hints_raw_link(tmp_path):
    """不认识的托管站（自建 Gitea 等）文件页仍返回网页：报错提示改用 Raw 链接。"""
    site = Site({"git.example.org/x/y/src/branch/main/README.md": httpx.Response(200, content=HTML, headers={"content-type": "text/html"})})
    with pytest.raises(FetchError, match="Raw 链接"):
        _get("https://git.example.org/x/y/src/branch/main/README.md", tmp_path, site)


@pytest.mark.parametrize(
    ("url", "raw"),
    [
        ("https://github.com/o/r/blob/main/README.md", "https://raw.githubusercontent.com/o/r/main/README.md"),
        ("https://github.com/o/r/blob/v1.2/docs/a%20b.md?plain=1#L3", "https://raw.githubusercontent.com/o/r/v1.2/docs/a%20b.md"),
        ("https://gitlab.com/g/sub/r/-/blob/main/x.pdf", "https://gitlab.com/g/sub/r/-/raw/main/x.pdf"),
        ("https://gitee.com/o/r/blob/master/README.md", "https://gitee.com/o/r/raw/master/README.md"),
        ("https://github.com/o/r", "https://github.com/o/r"),  # 仓库首页：不是文件页，原样
        ("https://github.com/o/r/tree/main/docs", "https://github.com/o/r/tree/main/docs"),
        ("https://e.org/o/r/blob/main/README.md", "https://e.org/o/r/blob/main/README.md"),  # 别家同形路径不动
    ],
)
def test_raw_url(url, raw):
    assert fetch.raw_url(url) == raw


def test_github_blob_link_downloads_raw_markdown(tmp_path):
    """粘贴 GitHub 文件页链接：实际下 Raw 文件，出处记原链接与 Raw 地址（用户实测 README 链接被拒）。"""
    site = Site({
        "raw.githubusercontent.com/o/r/main/README.md": httpx.Response(
            200, content="# 插画\n\n正文。\n".encode(), headers={"content-type": "text/plain; charset=utf-8"}
        ),
    })
    r = _get("https://github.com/o/r/blob/main/README.md", tmp_path, site)
    assert r.kind == "md" and r.suggested_name == "README.md"
    assert r.record["url"] == "https://github.com/o/r/blob/main/README.md"
    assert r.record["final_url"] == "https://raw.githubusercontent.com/o/r/main/README.md"
    assert [req.headers["host"] for req in site.requests] == ["raw.githubusercontent.com"]
    r.tmp.unlink()


def test_user_agent_follows_robot_policy_format():
    """「名称/版本 (联系方式) 库/版本」：只有名称与版本的 UA 会被维基媒体等站点 403（实测 zh.wikipedia.org）。"""
    import re as _re

    from guanlan.fetch import USER_AGENT

    assert _re.fullmatch(r"guanlan/\S+ \(https://\S+\) httpx/\S+", USER_AGENT)


def test_malformed_content_type_treated_as_unknown(tmp_path):
    """`Content-Type: html`（不带斜杠）不能被编成 text/plain：当作没说，交给内容嗅探。"""
    site = Site({"e.org/page": httpx.Response(200, content=b"<div>hello</div>", headers={"content-type": "html"})})
    with pytest.raises(FetchError, match="先下载到本地"):
        _get("https://e.org/page", tmp_path, site)
    from guanlan.fetch import _mime

    assert _mime("html") == ("", None)
    assert _mime("text/html; charset=gbk") == ("text/html", "gbk")


def test_unresolvable_idn_label_gives_readable_error(tmp_path):
    # 系统解析器对超长国际化域名标签抛 UnicodeError（不是 OSError）：要落成看得懂的 FetchError
    def res(host, port):
        raise UnicodeError("label empty or too long")

    with pytest.raises(FetchError, match="国际化域名"):
        _get("https://例子.org/a.pdf", tmp_path, lambda req: httpx.Response(200), resolver=res)
    assert not list(tmp_path.glob(".upload-*"))


def test_idn_host_uses_idna2008_everywhere(tmp_path):
    """`straße.de`：标准库 idna（IDNA 2003）会编成 `strasse.de`——另一个域名。检查、解析与 Host 头都要用
    httpx 同一套 IDNA 2008 编码（`xn--strae-oqa.de`），走代理时 httpx 连的也是它。"""
    resolver = _resolver({})
    site = Site({"xn--strae-oqa.de/a.pdf": httpx.Response(200, content=PDF)})
    r = _get("https://straße.de/a.pdf", tmp_path, site, resolver=resolver)
    assert resolver.calls == ["xn--strae-oqa.de"]
    assert site.requests[0].headers["host"] == "xn--strae-oqa.de"
    r.tmp.unlink()


def test_bad_proxy_setting_gives_readable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "ftp://p:1")  # httpx 不认得的代理协议
    with pytest.raises(FetchError, match="代理地址格式不对"):
        fetch_url("https://e.org/a.pdf", tmp_path, resolver=_resolver({}))
    assert not list(tmp_path.glob(".upload-*"))


def test_cookies_never_forwarded_across_hops(tmp_path):
    """「不带 Cookie」：第一跳 Set-Cookie，跳转后的请求不能带上（固定 IP 时 Cookie 罐还会按 IP 串站）。"""
    site = Site(
        {
            "a.example/r": httpx.Response(302, headers={"location": "https://b.example/f.pdf", "set-cookie": "sid=SECRET; Path=/"}),
            "b.example/f.pdf": httpx.Response(200, content=PDF),
        }
    )
    r = _get("https://a.example/r", tmp_path, site)
    assert all("cookie" not in req.headers for req in site.requests)
    r.tmp.unlink()


def test_no_connection_reuse_across_hops(monkeypatch):
    """连接固定在 IP 上：同一 CDN IP 上跳到另一个域名不能复用上一跳的 TLS 连接（证书按上一个域名验过）。"""
    seen = {}
    real = httpx.Client

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(fetch.httpx, "Client", spy)
    fetch._client(None, None).close()
    assert seen["limits"].max_keepalive_connections == 0


def test_image_url_returning_page_names_image(tmp_path):
    site = Site({"e.org/a.png": httpx.Response(200, content=HTML, headers={"content-type": "text/html"})})
    with pytest.raises(FetchError, match="不是 图片"):
        _get("https://e.org/a.png", tmp_path, site)


def test_source_helpers_do_not_load_httpx():
    """一键入库 / 暂存区列表 / 上传只用出处记录：不该为此加载 httpx 与下载器。"""
    import subprocess
    import sys

    code = "import sys, guanlan.oneclick, guanlan.sourcerec; print('httpx' in sys.modules, 'guanlan.fetch' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["False", "False"]


def test_utf16_bom_html_is_sniffed(tmp_path):
    import codecs

    data = codecs.BOM_UTF16_LE + "<!DOCTYPE html><html><body>x</body></html>".encode("utf-16-le")
    p = tmp_path / "p"
    p.write_bytes(data)
    assert fetch._sniff(p, data[:8192]) == "html"


def test_non_utf8_markdown_says_encoding(tmp_path):
    site = Site({"e.org/r.md": httpx.Response(200, content="# 标题\n".encode("gbk"), headers={"content-type": "text/markdown; charset=gbk"})})
    with pytest.raises(FetchError, match="UTF-8"):
        _get("https://e.org/r.md", tmp_path, site)


def test_rfc2231_charset_collapsed():
    from guanlan.fetch import _mime

    assert _mime("text/html; charset*=utf-8''gbk") == ("text/html", "gbk")


@pytest.mark.parametrize("rec", [{"final_url": 1, "sha256": "x"}, {"final_url": "u", "sha256": None}, {"final_url": "u", "sha256": "x", "url": 5}])
def test_malformed_source_record_ignored(tmp_path, rec):
    f = tmp_path / "a.pdf"
    f.write_bytes(PDF)
    source_record_path(f).write_text(json.dumps(rec), encoding="utf-8")
    assert read_source_record(f) is None


@pytest.mark.parametrize("code", [401, 403, 429])
def test_refused_download_points_to_browser_save(tmp_path, code):
    """网站拒绝自动下载（登录 / JS 验证 / 限流，如知乎的验证页）：不绕过，告诉用户在浏览器里另存后上传。"""
    site = Site({"e.org/p/1": httpx.Response(code, content=b"<html>challenge</html>", headers={"content-type": "text/html"})})
    with pytest.raises(FetchError, match="另存为") as ei:
        _get("https://e.org/p/1", tmp_path, site)
    assert f"HTTP {code}" in str(ei.value)
    assert _leftovers(tmp_path) == []


# ── 403 后按 AGENTAO_WEB_FETCH_FALLBACK 再取一次（决策P4.31-14） ─────────────


def _refusing_site():
    return Site({"e.org/p/1": httpx.Response(403, content=b"<html>challenge</html>", headers={"content-type": "text/html"})})


def test_fallback_none_keeps_refusal(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)
    with pytest.raises(fetch.FetchRefused, match="另存为"):
        _get("https://e.org/p/1", tmp_path, _refusing_site())


def test_fallback_playwright_renders_once(tmp_path, monkeypatch):
    import agentao.tools.web as web

    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "playwright")
    monkeypatch.setenv("AGENTAO_WEB_FETCH_ALLOW_CIDRS", "198.18.0.0/15")
    calls = []

    async def fake_render(url, *, allow_networks=()):
        calls.append((url, allow_networks))
        return 200, "<html><body><h1>标题</h1><p>正文</p></body></html>"

    monkeypatch.setattr(web, "_render_with_playwright", fake_render)
    r = _get("https://e.org/p/1", tmp_path, _refusing_site())
    assert r.kind == "html" and r.record["fetched_via"] == "playwright" and r.record["charset"] == "utf-8"
    assert r.tmp.read_text(encoding="utf-8").startswith("<html>")
    (url, nets), = calls  # 只试一次
    assert url == "https://e.org/p/1"
    assert [str(n) for n in nets] == ["198.18.0.0/15"]  # 同一份 AGENTAO_WEB_FETCH_ALLOW_CIDRS 交给浏览器守卫
    r.tmp.unlink()


def test_fallback_jina_gives_markdown(tmp_path, monkeypatch):
    import agentao.tools.web as web

    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "jina")

    async def fake_jina(url):
        return "# 标题\n\n正文。\n"

    monkeypatch.setattr(web, "_fetch_via_jina", fake_jina)
    r = _get("https://e.org/p/1", tmp_path, _refusing_site())
    assert r.kind == "md" and r.suggested_name.endswith(".md") and r.record["fetched_via"] == "jina"
    r.tmp.unlink()


def test_fallback_jina_picks_up_key_from_kb_env(tmp_path, monkeypatch):
    """JINA_API_KEY 只写在 <库>/.env 时，jina 后备也要带上它（代码评审发现：agentao 只读进程环境）。"""
    import agentao.tools.web as web

    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "jina")
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".env").write_text("JINA_API_KEY=k-from-kb\n", encoding="utf-8")
    seen: list[str | None] = []

    async def fake_jina(url):
        seen.append(os.environ.get("JINA_API_KEY"))
        return "# 标题\n\n正文。\n"

    monkeypatch.setattr(web, "_fetch_via_jina", fake_jina)
    r = _get("https://e.org/p/1", tmp_path, _refusing_site(), env_root=kb)
    assert seen == ["k-from-kb"]
    r.tmp.unlink()
    monkeypatch.delenv("JINA_API_KEY", raising=False)


def test_fallback_jina_refuses_signed_url(tmp_path, monkeypatch):
    """预签名链接 403 时不把签名发给 r.jina.ai（代码评审发现）：盘上处处脱敏，这里也不能整个发出去。"""
    import agentao.tools.web as web

    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "jina")
    sent: list[str] = []

    async def fake_jina(url):
        sent.append(url)
        return "# 标题\n\n正文。\n"

    monkeypatch.setattr(web, "_fetch_via_jina", fake_jina)
    with pytest.raises(FetchError, match="没有交给第三方"):
        signed = "/p/1?X-Amz-Signature=SECRET&X-Amz-Credential=AK"
        site = Site({f"e.org{signed}": httpx.Response(403, content=b"<html>challenge</html>", headers={"content-type": "text/html"})})
        _get(f"https://e.org{signed}", tmp_path, site)
    assert sent == [] and _leftovers(tmp_path) == []


def test_fallback_failure_reports_both(tmp_path, monkeypatch):
    import agentao.tools.web as web

    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "playwright")

    async def still_403(url, *, allow_networks=()):
        return 403, "<html>challenge</html>"

    monkeypatch.setattr(web, "_render_with_playwright", still_403)
    with pytest.raises(FetchError, match="再取一次，仍失败：浏览器渲染后仍是 HTTP 403"):
        _get("https://e.org/p/1", tmp_path, _refusing_site())
    assert _leftovers(tmp_path) == []


@pytest.mark.parametrize("code", [401, 429])
def test_fallback_only_on_403(tmp_path, monkeypatch, code):
    import agentao.tools.web as web

    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "playwright")

    async def boom(url, *, allow_networks=()):  # pragma: no cover - 不该被调用
        raise AssertionError("只有 403 才再取")

    monkeypatch.setattr(web, "_render_with_playwright", boom)
    site = Site({"e.org/p/1": httpx.Response(code, content=b"x")})
    with pytest.raises(fetch.FetchRefused):
        _get("https://e.org/p/1", tmp_path, site)


def test_fallback_mode_follows_agentao_parsing(monkeypatch):
    for raw, want in [("", "none"), ("none", "none"), ("playwright", "playwright"), ("JINA", "jina"), ("crawl4ai", "playwright"), ("bogus", "none")]:
        monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", raw)
        assert fetch.fallback_mode() == want, raw


def test_redact_text_covers_every_url_in_message():
    from guanlan.sourcerec import redact_text

    msg = "failed at https://a.org/x?sig=S1&id=2 then (https://b.org/y?token=T2)"
    out = redact_text(msg)
    assert "S1" not in out and "T2" not in out and "id=2" in out and out.endswith(")")


def test_redact_text_url_with_parentheses_in_path():
    """路径带圆括号（维基百科条目等）：不能在括号处截断网址，否则后面查询串里的签名漏掉脱敏。"""
    from guanlan.sourcerec import redact_text

    out = redact_text("见 (https://a.org/Foo_(bar)?sig=S1&v=2)")
    assert "S1" not in out and "Foo_(bar)" in out and "v=2" in out and out.endswith(")")


def test_record_written_redacted(tmp_path):
    site = Site({"e.org/r.pdf?X-Amz-Signature=SECRET&v=1": httpx.Response(200, content=PDF)})
    r = _get("https://e.org/r.pdf?X-Amz-Signature=SECRET&v=1", tmp_path, site)
    assert "SECRET" not in r.record["url"] and "SECRET" not in r.record["final_url"]
    assert r.record["url"].endswith("?v=1")
    r.tmp.unlink()


def test_text_starting_with_bm_is_not_bmp(tmp_path):
    # 「BM」两字节太弱：以「BMW」开头的 Markdown 不能被认成 .bmp（漏报正例：真 BMP 头仍认）
    p = tmp_path / "p"
    assert fetch._sniff(p, b"BMW 3 series review\n# heading\n") is None
    bmp = b"BM" + (70).to_bytes(4, "little") + b"\x00" * 8 + (40).to_bytes(4, "little") + b"\x00" * 40
    assert fetch._sniff(p, bmp) == "bmp"


def test_source_record_bad_optional_field_types(tmp_path):
    f = tmp_path / "a.html"
    f.write_bytes(b"<p>x</p>")
    import hashlib

    rec = {"final_url": "https://e.org/a", "sha256": hashlib.sha256(b"<p>x</p>").hexdigest(), "fetched_at": 5, "charset": ["x"]}
    source_record_path(f).write_text(json.dumps(rec), encoding="utf-8")
    got = read_source_record(f)
    assert got is not None and got["fetched_at"] is None and got["charset"] is None


def test_no_proxy_bracketed_ipv6_entry():
    assert fetch._no_proxy_match("::1", "[::1]")
    assert fetch._no_proxy_match("::1", "[::1]:8080")
    assert not fetch._no_proxy_match("::2", "[::1]")


def test_redact_text_keeps_trailing_prose():
    """脱敏只删网址里的签名，不吞网址后面的全角标点和说明（代码评审发现）。"""
    from guanlan.sourcerec import redact_text

    assert redact_text("下载失败：https://h/p?X-Amz-Signature=abc，请在浏览器里另存为后上传。") == (
        "下载失败：https://h/p，请在浏览器里另存为后上传。"
    )
    out = redact_text("see https://a.org/Foo_(b)?sig=x; retry")
    assert out == "see https://a.org/Foo_(b); retry" and "x;" not in out


def test_supported_extension_with_wrong_content_is_not_reported_as_unsupported(tmp_path):
    """`.pdf` 链接返回 JSON 报错（过期签名链接）：说内容不是 PDF、链接可能失效，不能说「格式不支持」。"""
    p = tmp_path / "x"
    p.write_bytes(b'{"error":"expired"}')
    with pytest.raises(FetchError) as ei:
        fetch._decide_kind(p, p.read_bytes(), "pdf", "application/json")
    assert "不是 PDF" in str(ei.value) and "暂不支持" not in str(ei.value)


@pytest.mark.parametrize("url", ["https://intranet/a.pdf", "https://wiki.corp.internal/a.pdf", "http://metadata.google.internal/x", "https://printer.local/a.pdf"])
def test_internal_hostnames_blocked_even_when_allowlisted(tmp_path, monkeypatch, url):
    """fake-ip 代理放行 198.18.0.0/15 时所有域名都解析进放行段：内部名 / 单段名要按主机名形状直接拒（同 agentao）。"""
    monkeypatch.setenv(fetch.ALLOW_CIDRS_ENV, "198.18.0.0/15")
    site = Site({})
    res = _resolver({h: ["198.18.0.9"] for h in ("intranet", "wiki.corp.internal", "metadata.google.internal", "printer.local")})
    with pytest.raises(FetchError, match="内部主机名|单段主机名"):
        _get(url, tmp_path, site, res)
    assert site.requests == [] and res.calls == []


def test_metadata_address_not_allowed_by_broad_range(tmp_path, monkeypatch):
    """云元数据地址只有放行网段恰好写明它时才放（同 agentao）：放行整个 169.254.0.0/16 不算。"""
    monkeypatch.setenv(fetch.ALLOW_CIDRS_ENV, "169.254.0.0/16")
    with pytest.raises(FetchError, match="本机或内网"):
        _get("https://e.org/a.pdf", tmp_path, Site({}), _resolver({"e.org": ["169.254.169.254"]}))


def test_html_content_type_with_non_html_body_is_not_reported_as_unsupported(tmp_path):
    """服务器说 text/html、内容却是 JSON 报错：网页本是支持的格式，不能说「格式不支持」。"""
    p = tmp_path / "x"
    p.write_bytes(b'{"error":"expired"}')
    with pytest.raises(FetchError) as ei:
        fetch._decide_kind(p, p.read_bytes(), "html", "text/html")
    assert "不是 网页" in str(ei.value) and "暂不支持" not in str(ei.value)
