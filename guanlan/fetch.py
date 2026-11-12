"""网址下载器（P4.31，见 docs/P4.31-网址导入.md §2–§3）。零 LLM、与传输无关。

把一个网址的文件下到目标目录里的**隐藏临时文件**，判定类型、给出建议文件名与出处记录内容；
**不选最终文件名、不换名**——提交由调用方做（Web 在事件循环线程里做，决策P4.31-4）。
不依赖 FastAPI：将来 CLI `guanlan import <url>` 直接复用（决策P4.31-11）。

安全（§3，决策P4.31-5）——与 agentao 的 `web_fetch` 同一套配置、同一口径：
- 每一跳：本机解析域名 → 任一地址不是公网即拒（`AGENTAO_WEB_FETCH_ALLOW_CIDRS` 可放行）；网址里直接写的内网 IP
  （含 `127.1` 之类简写）与 `localhost` 同样拒。走不走代理都先查这一步。
- 代理读标准变量 `https_proxy` / `http_proxy` / `all_proxy` / `no_proxy`（与 agentao 的 httpx `trust_env=True` 一致，
  另按 `agentao_setting` 的顺序读 `.env`）。这一跳不走代理时**用解析出的地址建连接**，`Host` 头与 TLS SNI 仍用原主机名
  （证书照常按主机名校验），不让连接时再解析出另一个地址（防 DNS 重绑定）；走代理时由代理去连主机名。
"""

from __future__ import annotations

import codecs
import contextlib
import email.message
import email.utils
import hashlib
import ipaddress
import os
import re
import socket
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx
from agentao.security.url_policy import _LOCAL_HOST_SUFFIXES, _LOCAL_HOSTNAMES
from agentao.security.url_policy import _is_disallowed as _agentao_disallowed

from . import __version__
from .sourcerec import TMP_PREFIX, TMP_SUFFIX, redact_url
from .rawio import NAME_STEM_MAX_BYTES, cap_utf8

__all__ = [
    "FetchCancelled",
    "FetchError",
    "FetchResult",
    "check_url",
    "fetch_url",
]

MAX_BYTES = 256 * 1024 * 1024  # 同上传 `MAX_UPLOAD_BYTES`
CONNECT_TIMEOUT = 10.0
READ_IDLE_TIMEOUT = 60.0
TOTAL_SECONDS = 15 * 60
MAX_REDIRECTS = 5
MAX_URL_LEN = 2048
CHUNK = 1024 * 1024


def _project_url() -> str:
    """项目主页（`pyproject.toml` 的 `[project.urls] Homepage`），读安装元数据；读不到用仓库地址兜底。"""
    try:
        from importlib.metadata import metadata

        for item in metadata("guanlan-wiki").get_all("Project-URL") or []:
            label, _, url = item.partition(",")
            if label.strip().lower() == "homepage" and url.strip():
                return url.strip()
    except Exception:  # noqa: BLE001 — 开发态未安装等：用兜底
        pass
    return "https://github.com/jin-bo/guanlan"


# 按维基媒体等站点的 User-Agent 政策写成「名称/版本 (联系方式) 库/版本」：只有名称与版本的 UA 会被当成
# 不守规矩的爬虫直接 403（实测 zh.wikipedia.org，补上项目主页即 200）。联系方式用项目的公开主页——
# 不带用户、库名等任何本地信息。
USER_AGENT = f"guanlan/{__version__} ({_project_url()}) httpx/{httpx.__version__}"

# 放行网段与 403 后备都读 agentao 的配置项（与 Web 对话里 `web_fetch` 同一个开关）
ALLOW_CIDRS_ENV = "AGENTAO_WEB_FETCH_ALLOW_CIDRS"
FALLBACK_ENV = "AGENTAO_WEB_FETCH_FALLBACK"
# 代理：与 agentao 的 web_fetch 一样读标准变量（它的 httpx 用 trust_env=True）。查找顺序同 agentao_setting
_PROXY_VARS = {
    "https": ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"),
    "http": ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"),
}
_NO_PROXY_VARS = ("no_proxy", "NO_PROXY")


# 本期只认这些格式（决策P4.31-3）：kind → 扩展名
KIND_EXT = {
    "pdf": ".pdf",
    "docx": ".docx",
    "pptx": ".pptx",
    "xlsx": ".xlsx",
    "html": ".html",
    "md": ".md",
    "png": ".png",
    "jpeg": ".jpg",
    "gif": ".gif",
    "webp": ".webp",
    "tiff": ".tiff",
    "bmp": ".bmp",
}
KIND_LABEL = {
    "pdf": "PDF",
    "docx": "Word 文档",
    "pptx": "PowerPoint 演示文稿",
    "xlsx": "Excel 表格",
    "html": "网页",
    "md": "Markdown",
}
_EXT_KIND = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".markdown": "md",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".bmp": "bmp",
}
_MIME_KIND = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/markdown": "md",
    "text/x-markdown": "md",
}
_OOXML_MAIN = {
    "wordprocessingml.document.main": "docx",
    "presentationml.presentation.main": "pptx",
    "spreadsheetml.sheet.main": "xlsx",
}
_FILENAME_STAR_RE = re.compile(r"filename\*\s*=\s*([\w\-]*)'[^']*'([^;]+)", re.IGNORECASE)
_UNSUPPORTED_HINT = "暂不支持从网址下载这种格式{what}；请先下载到本地，再用「上传文件…」上传。"


class FetchError(Exception):
    """下载失败；消息直接给用户看。"""


class FetchCancelled(FetchError):
    def __init__(self) -> None:
        super().__init__("已停止下载。")


class FetchRefused(FetchError):
    """网站拒绝自动下载（401 / 403 / 429）。`url` 是被拒的那一跳（跳转之后）。"""

    def __init__(self, message: str, *, status: int, url: str) -> None:
        super().__init__(message)
        self.status = status
        self.url = url


@dataclass
class FetchResult:
    tmp: Path  # 目标目录里的隐藏临时文件（调用方负责换名或删除）
    suggested_name: str  # 建议文件名（扩展名已按判定结果改正；调用方再规范化）
    kind: str
    size: int
    record: dict = field(default_factory=dict)  # 出处记录内容（§2.5）


# ── 网址与地址检查（§3） ────────────────────────────────────────────────────


def check_url(url: str) -> str:
    """语法检查（不联网）：只收 http(s)、要有主机名、不带用户名密码、不超长。返回去掉首尾空白的网址。"""
    url = (url or "").strip()
    if not url:
        raise FetchError("请填写网址。")
    if len(url) > MAX_URL_LEN:
        raise FetchError(f"网址太长（超过 {MAX_URL_LEN} 个字符）。")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise FetchError("网址格式不对。") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise FetchError("只支持 http:// 或 https:// 开头的网址。")
    if not parts.hostname:
        raise FetchError("网址里没有主机名。")
    if parts.username is not None or parts.password is not None:
        raise FetchError("网址里不能带用户名或密码。")
    if port is not None and not (0 < port < 65536):
        raise FetchError("网址端口不对。")
    return url


def agentao_setting(name: str, env_root: Path | None) -> str:
    """读一个 agentao 配置项，查找顺序与 agentao 一致：进程环境 → `<库>/.env` → 从当前目录往上的第一个 `.env`。

    Web 宿主可能在任何 Agent 构造之前就开始下载，那时 agentao 还没把 `.env` 载入进程环境，只看 `os.environ`
    会漏掉写在 `.env` 里的配置。这里只读值、不改进程环境。
    """
    value = os.environ.get(name)
    if value is not None:
        return value
    try:
        from dotenv import dotenv_values, find_dotenv
    except ImportError:  # python-dotenv 随 agentao 安装；缺了就只认进程环境
        return ""
    candidate = env_root / ".env" if env_root is not None else None
    path = str(candidate) if candidate is not None and candidate.is_file() else find_dotenv(usecwd=True)
    if not path:
        return ""
    try:
        values = _dotenv_cached(path, dotenv_values)
    except (OSError, ValueError):
        return ""
    return (values.get(name) or "").replace("\x00", "")


# 一次下载每一跳都要查好几个变量（代理三四个、no_proxy 两个）：同一份 `.env` 只在内容变了（mtime / 大小）时重新解析
_DOTENV_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}
_DOTENV_LOCK = threading.Lock()


def _dotenv_cached(path: str, loader) -> dict:
    st = os.stat(path)
    stamp = (st.st_mtime_ns, st.st_size)
    with _DOTENV_LOCK:
        hit = _DOTENV_CACHE.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    values = dict(loader(path))
    with _DOTENV_LOCK:
        _DOTENV_CACHE[path] = (stamp, values)
    return values


def allow_networks(env_root: Path | None = None) -> tuple:
    """`AGENTAO_WEB_FETCH_ALLOW_CIDRS`：逗号或空白分隔的网段 / 单个 IP，非法项跳过（与 agentao 的解析一致）。

    默认空：本机与内网地址一律拒。开着 Clash / Surge 等 fake-ip 代理的机器所有域名都解析到 `198.18.0.0/15`，
    须在这里放行这一段（写进 `<库>/.env`；或从启动目录往上能找到的 `.env`，如在家目录下启动时的 `~/.env`）。
    """
    nets = []
    for token in re.split(r"[,\s]+", agentao_setting(ALLOW_CIDRS_ENV, env_root).strip()):
        if not token:
            continue
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            continue
    return tuple(dict.fromkeys(nets))


# 低 32 位直接内嵌 IPv4 的写法：NAT64 公用前缀、IPv4 兼容（::/96，已废弃）、SIIT 的 IPv4 转换地址（::ffff:0:0/96）
_EMBED_LOW32 = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("::/96"),
    ipaddress.ip_network("::ffff:0:0:0/96"),
)
# RFC 8215 本地 NAT64 前缀：内嵌位置随前缀长度变，拆不出确定的 IPv4，一律拒（可经 ALLOW_CIDRS 放行）
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


def _blocked(ip: ipaddress._BaseAddress, allow: tuple) -> bool:
    # 先过 agentao `web_fetch` 的同一判定（同一口径，§3）：它另拆 Teredo / ISATAP / 各位置的 NAT64 内嵌地址，
    # 云元数据地址只有放行网段**恰好写明**它时才放（放行 169.254.0.0/16 这类大段不算）。下面的自查是再加一道
    if _agentao_disallowed(ip, allow):
        return True
    if any(ip in net for net in allow):
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # 内嵌 IPv4 的 IPv6 写法按内嵌地址判：`is_global` 对 6to4（2002::/16）与 NAT64（64:ff9b::/96）、
        # IPv4 兼容 / 转换地址一律给 True，不拆开的话 `2002:7f00:1::`、`64:ff9b::a9fe:a9fe`、`::7f00:1`
        # 能绕过回环 / 云元数据检查
        if ip in _NAT64_LOCAL:
            return True
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif any(ip in net for net in _EMBED_LOW32):
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if any(ip in net for net in allow):
        return False
    return not ip.is_global or ip.is_multicast


Resolver = Callable[[str, int], list[str]]


def _system_resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def _literal_ip(host: str) -> ipaddress._BaseAddress | None:
    """网址里直接写的 IP。`127.1`、`2130706433`、`0x7f.0.0.1` 这类 IPv4 简写 ipaddress 不认，系统解析器与代理却照样
    当成回环地址：按 inet_aton 的口径再认一次。"""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if re.fullmatch(r"[0-9a-fx.]+", host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def _ascii_host_url(url: str) -> str:
    """国际化域名换成 A-label（punycode）后的网址。用 httpx 同一套 IDNA 2008 / UTS 46 编码：标准库的 `idna` 编解码器
    是 IDNA 2003，`straße.de` 会编成 `strasse.de`——本机检查、Host 头、SNI 指向一个域名，走代理时 httpx 却连另一个。
    之后的检查、解析、Host 与 SNI 都只见 ASCII 主机名。"""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if host.isascii():
        return url
    try:
        ascii_host = httpx.URL(url).raw_host.decode("ascii")
    except (httpx.InvalidURL, UnicodeError, ValueError):  # 标签超长 / IDNA 不收的字符
        raise FetchError(f"无法处理这个国际化域名：{host}") from None
    netloc = ascii_host if parts.port is None else f"{ascii_host}:{parts.port}"  # check_url 已拒用户名密码
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _pin(url: str, resolver: Resolver, allow: tuple) -> tuple[list[str], str]:
    """解析并检查主机；返回 (用各个解析地址改写主机部分的网址, 原主机名)。"""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    bare = host.lower().rstrip(".")  # `localhost.`（末尾点）同样是本机
    if bare == "localhost" or bare.endswith(".localhost"):
        raise FetchError("不能下载本机地址（localhost）。")
    literal = _literal_ip(bare)
    if literal is not None:
        addrs = [str(literal)]
    else:
        # 主机名形状的检查与 agentao 同口径：内部名（`.internal` / `.local` / 云元数据名）与单段名不看解析结果直接拒。
        # 开着 fake-ip 代理、放行 198.18.0.0/15 时所有域名都解析进放行段，地址检查形同虚设，内网名全靠这一步挡
        if bare in _LOCAL_HOSTNAMES or bare.endswith(_LOCAL_HOST_SUFFIXES):
            raise FetchError(f"不能下载内部主机名（{host}）。")
        if "." not in bare:
            raise FetchError(f"不能下载单段主机名（{host}）：请写完整域名。")
        try:
            addrs = resolver(host, port)
        except UnicodeError:  # 国际化域名标签超长 / IDNA 不收的字符：getaddrinfo 抛的是 UnicodeError 而非 OSError
            raise FetchError(f"无法处理这个国际化域名：{host}") from None
        except OSError as exc:
            raise FetchError(f"无法解析主机名 {host}：{exc}") from None
        if not addrs:
            raise FetchError(f"无法解析主机名 {host}。")
    ips = []
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a.split("%", 1)[0])
        except ValueError:
            raise FetchError(f"{host} 解析出无法识别的地址：{a}") from None
        if _blocked(ip, allow):
            raise FetchError(
                f"{host} 指向本机或内网地址（{ip}），不能下载。"
                f"如确需访问（含 fake-ip 代理的 198.18.0.0/15），请在 .env 里设置 {ALLOW_CIDRS_ENV}。"
            )
        ips.append(ip)
    urls = []
    for ip in ips:  # 每个地址都已过检查；连不上第一个（如本机没有 IPv6 路由）时逐个退到下一个
        ip_host = f"[{ip}]" if ip.version == 6 else str(ip)
        netloc = ip_host if parts.port is None else f"{ip_host}:{parts.port}"
        urls.append(urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")))
    return urls, host


# ── 类型判定与命名（§2.3–§2.4） ─────────────────────────────────────────────


def _mime(content_type: str | None) -> tuple[str, str | None]:
    msg = email.message.Message()
    msg["content-type"] = content_type or ""
    # RFC 2231 写法（`charset*=utf-8''gbk`）get_param 返回三元组：折叠成字符串，不把 "('utf-8', …)" 记进出处
    charset = msg.get_param("charset")
    if isinstance(charset, tuple):
        charset = email.utils.collapse_rfc2231_value(charset)
    # 不带 `/` 的畸形值（如 `html`）get_content_type 会编成 text/plain：当作「没说」，交给扩展名与嗅探
    mime = msg.get_content_type().lower() if content_type and "/" in content_type.split(";", 1)[0] else ""
    return mime, (str(charset) if charset else None)


def _disposition_name(value: str | None) -> str | None:
    if not value:
        return None
    name = None
    # RFC 6266：`filename*`（RFC 5987 编码，可带中文）优先于 `filename`；email 的 get_filename 取的是后者
    m = _FILENAME_STAR_RE.search(value)
    if m:
        try:
            name = unquote(m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8", errors="strict")
        except (LookupError, UnicodeDecodeError):
            name = None
    if not name:
        msg = email.message.Message()
        msg["content-disposition"] = value
        name = msg.get_filename()
    if not name:
        return None
    return name.replace("\\", "/").rsplit("/", 1)[-1].strip() or None


def _url_name(url: str) -> str | None:
    seg = unquote(urlsplit(url).path.rsplit("/", 1)[-1]).strip()
    return seg or None


def _ext_kind(name: str | None) -> str | None:
    return _EXT_KIND.get(Path(name).suffix.lower()) if name else None


def _sniff(path: Path, head: bytes) -> str | None:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as zf, zf.open("[Content_Types].xml") as fh:
                # 只读前 1 MiB：这是下载来的不可信文件，整读可被压缩炸弹撑爆内存
                types = fh.read(1024 * 1024).decode("utf-8", "replace")
        except (zipfile.BadZipFile, KeyError, OSError):
            return None
        for marker, kind in _OOXML_MAIN.items():
            if marker in types:
                return kind
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    # `BM` 两字节太弱（「BMW…」开头的 Markdown 会被认成图片）：再核 BITMAPINFOHEADER 系列的头长度
    if head[:2] == b"BM" and len(head) >= 18 and int.from_bytes(head[14:18], "little") in (12, 40, 52, 56, 64, 108, 124):
        return "bmp"
    lowered = _text_head(head)
    if _strong_html(lowered):
        return "html"
    if b"\x00" not in head[:512] and re.match(rb"\s*<(!--|head|meta|title|body)", lowered):
        return "html"  # 省略 <html> 的页面
    return None


def _text_head(head: bytes) -> bytes:
    if head.startswith(codecs.BOM_UTF8):
        head = head[len(codecs.BOM_UTF8):]
    else:
        for bom, enc in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            if head.startswith(bom):
                # UTF-16 每个 ASCII 字符夹一个 \x00：转成 UTF-8 再比，否则 `<!doctype html` 永远对不上、整页被拒
                head = head[len(bom):].decode(enc, "ignore").encode("utf-8")
                break
    return head.lstrip().lower()


def _strong_html(lowered: bytes) -> bool:
    """确凿是网页：doctype / `<html>` 开头，或 XHTML 的 `<?xml …?>` 声明后紧跟它们。"""
    if lowered.startswith(b"<?xml"):
        end = lowered.find(b"?>")
        lowered = lowered[end + 2:].lstrip() if end >= 0 else b""
    return lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html")


def _is_utf8_text(path: Path) -> bool:
    """分块增量解码：下载件可达 256 MiB，不整读进内存。"""
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                if b"\x00" in chunk:
                    return False
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except (OSError, UnicodeDecodeError):
        return False
    return True


def _decide_kind(path: Path, head: bytes, expected: str | None, label_hint: str) -> str:
    sniffed = _sniff(path, head)
    if sniffed == "html" and expected == "md" and not _strong_html(_text_head(head)) and _is_utf8_text(path):
        return "md"  # README 常以 `<!-- … -->` / `<meta>` 开头：只有弱特征时按扩展名 / MIME 认作 Markdown
    if sniffed is None and expected == "html" and b"\x00" not in head[:512] and b"<" in head:
        return "html"  # 服务器说是网页、内容是文本：以 <script> / <div> / <link> 等开头的页面
    if sniffed == "html" and expected not in (None, "html"):
        # 图片类没有 KIND_LABEL；label_hint 此时多半是服务器给的 `text/html`，不能拿来当「期望的格式」
        what = KIND_LABEL.get(expected) or ("图片" if expected in KIND_EXT else label_hint)
        hint = "；若是 GitHub 等代码托管页面，请用文件的 Raw 链接" if expected == "md" else ""
        raise FetchError(f"返回的是网页而不是 {what}，可能需要登录或链接已失效{hint}。")
    if sniffed is not None:
        return sniffed
    if expected == "md":
        if _is_utf8_text(path):
            return "md"
        # 说的是 Markdown、内容却不是 UTF-8 文本（GBK 等）：直说原因，别报成「格式不支持」
        raise FetchError("这个 Markdown 文件不是 UTF-8 编码（或含二进制内容）；请转成 UTF-8 后再上传。")
    if expected in KIND_EXT:
        # 扩展名 / MIME 说的是支持的格式、内容却对不上（过期链接返回的 JSON 报错、截断的文件）：不能报成「格式不支持」。
        # 网页同理：服务器说 text/html、内容却没有标记（JSON / 纯文本报错）
        what = KIND_LABEL.get(expected) or "图片"
        raise FetchError(f"返回的内容不是 {what}（{label_hint or '未知类型'}），链接可能已失效或需要登录。")
    what = f"（{label_hint}）" if label_hint else ""
    raise FetchError(_UNSUPPORTED_HINT.format(what=what))


def _suggest_name(disp: str | None, final_url: str, kind: str) -> str:
    # 先做与上传命名同样的 NFKC：「㍿」这类字符规范化后会变长，截短必须在它之后，否则落盘名会超 255 字节
    name = unicodedata.normalize("NFKC", disp or _url_name(final_url) or "")
    if not name or name in (".", ".."):
        parts = urlsplit(final_url)
        slug = re.sub(r"[^\w\-]+", "-", f"{parts.hostname or 'download'}{parts.path}").strip("-")
        name = slug or "download"
    stem, ext = os.path.splitext(name)
    if not ext and stem.startswith(".") and _EXT_KIND.get(stem.lower()):  # 只有扩展名（`filename=".pdf"`）：不拼成 `.pdf.pdf`
        stem, ext = "", stem
    # 认得的扩展名一律换成判定结果的标准写法（`.htm` → `.html`、`.jpeg` → `.jpg`）；认不得的保留、在后面补
    if _EXT_KIND.get(ext.lower()) is None:
        stem = name
    # Linux 文件名按字节计 255：还要拼 `-<序号>` 与出处记录 `.<名>.source.json`，按 UTF-8 字节截短（不切坏字符）
    return (cap_utf8(stem, NAME_STEM_MAX_BYTES) or "download") + KIND_EXT[kind]


# ── 下载 ────────────────────────────────────────────────────────────────────


def _client(proxy: str | None, transport: httpx.BaseTransport | None) -> httpx.Client:
    timeout = httpx.Timeout(READ_IDLE_TIMEOUT, connect=CONNECT_TIMEOUT)
    # 不复用连接：固定地址时连接池按 IP 归档，跳到同一 IP 上的另一个主机名会沿用为前一个主机名建的 TLS 连接
    # （SNI 与证书校验都是前一个的）。每跳新建连接，多一次握手，换来每个主机名都按自己校验证书。
    kw: dict = {
        "timeout": timeout,
        "trust_env": False,
        "follow_redirects": False,
        "limits": httpx.Limits(max_keepalive_connections=0),
    }
    if transport is not None:
        kw["transport"] = transport  # 测试注入：零网络（代理是否选对另由 proxy_for 的用例验）
    elif proxy:
        kw["proxy"] = proxy
    return httpx.Client(**kw)


def proxy_for(url: str, env_root: Path | None = None) -> str | None:
    """这一跳该走的代理（与 agentao `web_fetch` 同一套标准变量）：`https_proxy` / `http_proxy` / `all_proxy`
    （大小写都认，小写优先），`no_proxy` 里列的主机直连。查找顺序同 `agentao_setting`（进程环境 → `.env`）。"""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    for name in _NO_PROXY_VARS:
        raw = agentao_setting(name, env_root).strip()
        if raw:
            if _no_proxy_match(host, raw):
                return None
            break
    for name in _PROXY_VARS.get(scheme, ()):
        value = agentao_setting(name, env_root).strip()
        if value:
            # 同 httpx 读环境变量的口径（agentao 的 web_fetch 走的就是它）：`127.0.0.1:7890` 这种不带协议的写法按 http:// 补全
            return value if "://" in value else f"http://{value}"
    return None


def _no_proxy_match(host: str, raw: str) -> bool:
    ip = _literal_ip(host)
    for entry in re.split(r"[,\s]+", raw.lower()):
        entry = entry.strip().lstrip(".")
        if not entry:
            continue
        if entry == "*":
            return True
        if entry.startswith("["):  # `[::1]` / `[::1]:8080`：IPv6 写法带方括号，去掉括号与端口再比
            entry = entry[1:].split("]", 1)[0]
        else:
            entry = entry.rsplit(":", 1)[0] if entry.count(":") == 1 else entry  # `host:port` 只比主机
        if ip is not None:
            try:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                pass
        if host == entry or host.endswith("." + entry):
            return True
    return False


def fetch_url(
    url: str,
    dest_dir: Path,
    *,
    progress: Callable[[int, int | None], None] | None = None,
    cancel: threading.Event | None = None,
    resolver: Resolver | None = None,
    transport: httpx.BaseTransport | None = None,
    env_root: Path | None = None,
) -> FetchResult:
    """下载 `url` 到 `dest_dir` 下的隐藏临时文件。失败抛 `FetchError`（停止抛 `FetchCancelled`），不留临时文件。

    `resolver` / `transport` 供测试注入（零网络）。
    `env_root`：知识库根——`AGENTAO_WEB_FETCH_ALLOW_CIDRS` / `AGENTAO_WEB_FETCH_FALLBACK` / 标准代理变量先找 `<库>/.env`
    （`agentao_setting`）。
    """
    url = check_url(url)
    allow = allow_networks(env_root)
    resolver = resolver or _system_resolve
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest_dir), prefix=TMP_PREFIX, suffix=TMP_SUFFIX)
    tmp = Path(tmp_name)
    try:
        try:
            with os.fdopen(fd, "wb") as out:
                final_url, resp_headers, size, sha, head = _download(
                    url, out, transport=transport, env_root=env_root, resolver=resolver,
                    progress=progress, cancel=cancel, allow=allow,
                )
        except FetchRefused as refused:
            mode = fallback_mode(env_root)
            if refused.status != 403 or mode == "none":
                raise
            if cancel is not None and cancel.is_set():
                raise FetchCancelled from None
            return _fetch_with_fallback(
                url, refused, tmp, mode, progress=progress, allow=allow, cancel=cancel, env_root=env_root
            )
        content_type, charset = _mime(resp_headers.get("content-type"))
        disp = _disposition_name(resp_headers.get("content-disposition"))
        expected = _ext_kind(disp) or _ext_kind(_url_name(final_url)) or _MIME_KIND.get(content_type)
        hint = content_type or (Path(disp or _url_name(final_url) or "").suffix.lstrip(".") or "")
        kind = _decide_kind(tmp, head, expected, hint)
        record = _record(url, final_url, sha, content_type or None, charset if kind == "html" else None, size)
        return FetchResult(tmp=tmp, suggested_name=_suggest_name(disp, final_url, kind), kind=kind, size=size, record=record)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _record(url: str, final_url: str, sha: str, content_type: str | None, charset: str | None, size: int) -> dict:
    """出处记录内容（§2.5）。写入时就去掉凭据参数（签名链接）：完整网址只在这次下载的内存里，记录里不存秘密。"""
    return {
        "url": redact_url(url),
        "final_url": redact_url(final_url),
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sha256": sha,
        "content_type": content_type,
        "charset": charset,
        "bytes": size,
    }


def _download(url, out, *, transport, env_root, resolver, progress, cancel, allow=()) -> tuple[str, httpx.Headers, int, str, bytes]:
    """返回 (最终网址, 响应头, 字节数, sha256, 前 8 KiB)。前 8 KiB 供类型嗅探，边下边留，不再回读临时文件。"""
    deadline = time.monotonic() + TOTAL_SECONDS
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        if cancel is not None and cancel.is_set():
            raise FetchCancelled
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        extensions: dict = {}
        # 国际化域名先转 A-label：检查、解析、Host、SNI 与经代理时 httpx 连的是同一个主机名
        current = _ascii_host_url(current)
        # 每一跳先在本机解析并检查地址（走不走代理都查，与 agentao 的 guarded_get 同口径），再决定怎么连
        req_urls, host = _pin(current, resolver, allow)
        proxy = proxy_for(current, env_root)
        if proxy:
            # 经代理：由代理去连主机名（连接固定不到解析出的地址上；本机的检查挡住的是写明的内网地址与本机解析到内网的域名）
            req_urls = [current]
        else:
            parts = urlsplit(current)
            hostname = parts.hostname or ""
            if ":" in hostname:  # IPv6 字面量
                hostname = f"[{hostname}]"
            headers["Host"] = hostname if parts.port is None else f"{hostname}:{parts.port}"
            if parts.scheme.lower() == "https":
                extensions["sni_hostname"] = host
        # 每跳一个客户端：这一跳走不走代理各不相同；连接本就不复用（见 _client）
        try:
            client = _client(proxy, transport)
        except ImportError:  # httpx 的 SOCKS 支持是可选依赖
            raise FetchError("代理是 SOCKS 协议，需要安装 httpx[socks]（socksio）才能使用。") from None
        except ValueError:  # 不认得的代理协议；不回显代理地址（可能带用户名密码）
            raise FetchError("代理地址格式不对，请检查 https_proxy / http_proxy / all_proxy。") from None
        try:
            for i, req_url in enumerate(req_urls):
                if i and cancel is not None and cancel.is_set():  # 逐个地址重试时每个都可能等满连接超时：之间看一次停止
                    raise FetchCancelled
                request = client.build_request("GET", req_url, headers=headers, extensions=extensions)
                try:
                    # 「不带 Cookie」（§2.2）：每跳新建、用完即关的客户端，响应的 Set-Cookie 带不到下一跳
                    resp = client.send(request, stream=True)
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    if i + 1 < len(req_urls):
                        continue
                    if isinstance(exc, httpx.TimeoutException):
                        raise FetchError("连接超时。") from None
                    raise FetchError(f"连接失败：{exc}") from None
                except httpx.TimeoutException:
                    raise FetchError("连接超时。") from None
                except httpx.HTTPError as exc:
                    raise FetchError(f"连接失败：{exc}") from None
            try:
                if 300 <= resp.status_code < 400:  # `is_redirect` 要求带 Location：没带的 3xx 不能当正文存下
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError(f"服务器返回跳转（HTTP {resp.status_code}）却没给新地址。")
                    current = check_url(urljoin(current, location))
                    continue
                if resp.status_code in (401, 403, 429):
                    # 网站拒绝自动下载：要登录、要在浏览器里跑 JS 验证（如知乎的 zse-ck 验证页），或限流。观澜自己不伪装浏览器、
                    # 不执行网页脚本（决策P4.31-2）；用户在 agentao 里配了 AGENTAO_WEB_FETCH_FALLBACK 时，403 由 fetch_url
                    # 按那个配置再取一次（决策P4.31-14），否则直接告诉用户出路
                    raise FetchRefused(
                        f"网站拒绝了自动下载（HTTP {resp.status_code}：需要登录、浏览器验证或访问过于频繁）。"
                        "请在浏览器里打开该页面，「另存为」网页（.html）或打印为 PDF，再用「上传文件…」上传。",
                        status=resp.status_code,
                        url=current,
                    )
                if resp.status_code >= 400:
                    raise FetchError(f"服务器返回 HTTP {resp.status_code}。")
                length = resp.headers.get("content-length")
                # `isascii()`：`str.isdigit` 也认「²」「١」等非 ASCII 数字，int() 却会抛 ValueError
                total = int(length) if length and length.isascii() and length.isdigit() else None
                if total is not None and total > MAX_BYTES:
                    raise FetchError(f"文件太大（{total // (1024 * 1024)} MiB，上限 {MAX_BYTES // (1024 * 1024)} MiB）。")
                if resp.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
                    # 压缩传输时 Content-Length 是压缩后的字节数，而进度按解压后计：不当总量给，免得「300 MiB / 50 MiB」
                    total = None
                sha = hashlib.sha256()
                size = 0
                head = bytearray()
                try:
                    # 不给 chunk_size：收到多少交多少。给了 1 MiB 的话 httpx 会攒满才交，慢速连接上停止、进度、
                    # 总时长检查都要等一整 MiB 才轮到一次（真浏览器冒烟里「停止」因此迟迟不生效）。
                    for chunk in resp.iter_bytes():
                        if cancel is not None and cancel.is_set():
                            raise FetchCancelled
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise FetchError(f"文件超过上限 {MAX_BYTES // (1024 * 1024)} MiB，已停止下载。")
                        if time.monotonic() > deadline:
                            raise FetchError(f"下载超过 {TOTAL_SECONDS // 60} 分钟，已停止。")
                        out.write(chunk)
                        sha.update(chunk)
                        if len(head) < 8192:
                            head += chunk[: 8192 - len(head)]
                        if progress is not None:
                            progress(size, total)
                except httpx.TimeoutException:
                    raise FetchError(f"超过 {int(READ_IDLE_TIMEOUT)} 秒没有收到数据，已停止。") from None
                except httpx.HTTPError as exc:
                    raise FetchError(f"下载中断：{exc}") from None
                if size == 0:
                    raise FetchError("服务器返回了空文件。")
                return current, resp.headers, size, sha.hexdigest(), bytes(head)
            finally:
                resp.close()
        finally:
            client.close()
    raise FetchError(f"跳转超过 {MAX_REDIRECTS} 次。")


# ── 403 后按 agentao 的后备再取一次（决策P4.31-14） ─────────────────────────


def fallback_mode(env_root: Path | None = None) -> str:
    """用户给 agentao 配的 `AGENTAO_WEB_FETCH_FALLBACK`：`none`（默认）/ `playwright` / `jina`。

    解析规则与 agentao 的 `web_fetch` 一致（大小写不敏感、旧写法 `crawl4ai` → `playwright`、非法值按 `none`；
    `tests/test_agentao_contract.py` 对照着 agentao 自己的解析守着），但按 `agentao_setting` 的顺序找 `.env`。
    """
    raw = agentao_setting(FALLBACK_ENV, env_root).strip().lower()
    if raw == "crawl4ai":
        return "playwright"
    return raw if raw in ("playwright", "jina") else "none"


def _run_async(coro, cancel: threading.Event | None = None):
    """在（可能已有事件循环的）当前线程之外跑一个协程。下载本就在后台线程里，这里给它独立的循环。

    `cancel` 被置位时取消协程、抛 `FetchCancelled`：浏览器渲染 / 代取可能要等到它们自己的超时，
    「停止」不能干等到那时。
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    async def runner():
        task = asyncio.ensure_future(coro)
        while cancel is not None and not task.done():
            if cancel.is_set():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise FetchCancelled
            await asyncio.wait({task}, timeout=0.2)
        return await task

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, runner()).result()


def _fetch_with_fallback(
    url: str,
    refused: FetchRefused,
    tmp: Path,
    mode: str,
    *,
    progress,
    allow: tuple,
    cancel: threading.Event | None = None,
    env_root: Path | None = None,
) -> FetchResult:
    """被 403 拒绝后按用户的 agentao 配置再取**一次**：

    - `playwright`：本机无头 Chromium 渲染（agentao 的实现：每个请求重新过内网检查、子进程环境去掉凭据、禁下载、
      不放宽 TLS），得到渲染后的完整 HTML，按网页入库；
    - `jina`：网址交给第三方 `r.jina.ai` 代取，得到 Markdown，按 `.md` 入库（网址会发给第三方——这是用户在 agentao
      里打开它时已经接受的；但带签名 / 凭据参数的网址不发，直接报错）。

    仍失败 → 抛 `FetchError`，同时说明两次的原因。出处记录多一项 `fetched_via`。
    """
    target = refused.url
    try:
        if mode == "playwright":
            from agentao.tools.web import _render_with_playwright

            # 浏览器守卫用同一份放行网段（AGENTAO_WEB_FETCH_ALLOW_CIDRS，按 .env 顺序读到的）
            status, body = _run_async(_render_with_playwright(target, allow_networks=allow), cancel)
            if status is not None and status >= 400:
                raise FetchError(f"浏览器渲染后仍是 HTTP {status}")
            kind, data = "html", (body or "").encode("utf-8")
        else:
            # 带签名 / 凭据参数的网址（预签名下载链接等）不交给第三方：盘上处处脱敏，不能在这里把签名整个发出去。
            # 本机渲染（playwright）不出本机，不受此限。
            if redact_url(target) != target:
                raise FetchError(
                    f"{refused}（网址带签名或凭据参数，没有交给第三方 r.jina.ai 代取；请在浏览器里另存为网页或打印 PDF 后上传）"
                )
            from agentao.tools.web import _fetch_via_jina

            # agentao 的 jina 只读进程环境里的 JINA_API_KEY；Web 宿主在第一次建 Agent 前还没把 .env 载入进程环境，
            # 写在 .env 里的 key 就带不上。按同一查找顺序读到后不覆盖地补进去——与 agentao 自己 safe_load_dotenv 同姿态
            if not os.environ.get("JINA_API_KEY"):
                key = agentao_setting("JINA_API_KEY", env_root)
                if key:
                    os.environ["JINA_API_KEY"] = key

            body = _run_async(_fetch_via_jina(target), cancel)
            kind, data = "md", (body or "").encode("utf-8")
    except FetchCancelled:
        raise
    except FetchError as exc:
        raise FetchError(f"{refused}（已按 AGENTAO_WEB_FETCH_FALLBACK={mode} 再取一次，仍失败：{exc}）") from None
    except Exception as exc:  # noqa: BLE001 — 没装 Chromium、jina 不可达等：如实报
        raise FetchError(
            f"{refused}（已按 AGENTAO_WEB_FETCH_FALLBACK={mode} 再取一次，仍失败：{type(exc).__name__}: {exc}）"
        ) from None
    if not data.strip():
        raise FetchError(f"{refused}（已按 AGENTAO_WEB_FETCH_FALLBACK={mode} 再取一次，没有取到内容）")
    if len(data) > MAX_BYTES:
        raise FetchError(f"{refused}（AGENTAO_WEB_FETCH_FALLBACK={mode} 取到的内容超过上限）")
    tmp.write_bytes(data)
    if progress is not None:
        progress(len(data), len(data))
    record = _record(
        url,
        target,
        hashlib.sha256(data).hexdigest(),
        "text/html" if kind == "html" else "text/markdown",
        "utf-8" if kind == "html" else None,
        len(data),
    )
    record["fetched_via"] = mode
    return FetchResult(tmp=tmp, suggested_name=_suggest_name(None, target, kind), kind=kind, size=len(data), record=record)
