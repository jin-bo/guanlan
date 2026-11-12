"""网址导入的出处记录（P4.31 §2.5，决策P4.31-6）与签名网址脱敏。

从 `fetch.py` 分出来：一键入库、暂存区列表与上传提交只用到这些，不该为此加载 httpx 与下载器。
只依赖标准库与 `rawio`。
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from .rawio import sha256_file

# 隐藏临时文件与上传同前缀：中途被杀留下的残留由 Web 启动时的 `sweep_stale_upload_tmps` 一并清掉。
TMP_PREFIX = ".upload-"
TMP_SUFFIX = ".tmp"

# 签名网址（S3 / OSS / GCS / Azure SAS、带 token 的下载链接）的凭据参数：进 `raw/` 前去掉（P4.31 §5 实现补充）。
_SECRET_PARAM_EXACT = {"sig", "auth", "key", "apikey", "api_key", "access_key", "accesskeyid", "ossaccesskeyid", "se", "st"}
_SECRET_PARAM_PARTS = ("signature", "token", "credential", "secret", "password", "passwd")


def redact_url(url: str) -> str:
    """去掉网址里像凭据的查询参数（按参数名判断，值不看）；其余参数、路径与锚点原样保留。

    `raw/` 的 frontmatter 会随 `guanlan zip` 分享出去，预签名链接里的签名、令牌不能跟着走。出处记录写入时
    就脱敏，盘上任何地方都不存签名；完整网址只在那一次下载的内存里。宁可多删：出处的作用是认出原文在哪，删掉签名不影响这一点。
    """
    parts = urlsplit(url)
    if not parts.query and "=" not in parts.fragment:
        return url
    # 锚点若是 `k=v&…` 形式（OAuth 隐式授权的 `#access_token=…`）同样按参数过滤；普通锚点 `#p3` 原样
    fragment = _drop_secret_params(parts.fragment) if "=" in parts.fragment else parts.fragment
    return urlunsplit((parts.scheme, parts.netloc, parts.path, _drop_secret_params(parts.query), fragment))


def _drop_secret_params(qs: str) -> str:
    kept = []
    for item in qs.split("&") if qs else ():
        name = unquote(item.split("=", 1)[0]).strip().lower()
        if name in _SECRET_PARAM_EXACT or any(p in name for p in _SECRET_PARAM_PARTS):
            continue
        kept.append(item)
    return "&".join(kept)


# 圆括号算进网址（维基百科等路径常带 `(…)`，在括号处截断会让后面查询串里的签名漏掉脱敏）；
# 末尾多出来的 `)` 是外层文字的括号，见 `_redact_match`
# 全角标点同样截断：中文报错里网址后面紧跟「，请…」，并进网址的话会随末尾的签名参数一起被删掉
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s'\"<>\[\]，。；：、！？（）「」『』【】《》]+")
_TRAILING_PUNCT = ".,;:!?"


def _redact_match(m: re.Match) -> str:
    url, tail = m.group(0), ""
    while url:
        if url[-1] in _TRAILING_PUNCT:  # 句末的 `.` `,` `;` 不属于网址（否则「…?token=x; retry」连分号一起删）
            url, tail = url[:-1], url[-1] + tail
        elif url.endswith(")") and url.count(")") > url.count("("):  # `(见 https://…)` 的右括号不属于网址
            url, tail = url[:-1], ")" + tail
        else:
            break
    return redact_url(url) + tail


def redact_text(text: str) -> str:
    """文字里出现的每个网址都过一遍 `redact_url`：报错原文（如浏览器的 `net::ERR_… at https://…?X-Amz-Signature=…`）
    常把完整网址带出来，只对 `url` 字段脱敏挡不住它上屏。"""
    return _URL_IN_TEXT_RE.sub(_redact_match, text) if text else text


def source_record_path(file: Path) -> Path:
    """`uploads/<名>` 的出处记录：同目录隐藏文件 `.<名>.source.json`（暂存区列表跳过点文件）。"""
    return file.with_name(f".{file.name}.source.json")


def load_source_record(file: Path) -> dict | None:
    """只解析出处记录（不核对文件内容）；没有、读不出或不是 JSON 对象 → None。核对由调用方按需做。"""
    rec_path = source_record_path(file)
    try:
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def read_source_record(file: Path, *, sha256: str | None = None, discard_stale: bool = True) -> dict | None:
    """读出处记录；`sha256` 与文件当前内容不符（被同名上传覆盖等）→ 作废、删掉记录，返回 None。

    `discard_stale=False`：只判、不删（`import --dry-run` 不改盘上状态）。

    调用方已算过文件哈希时经 `sha256` 传入，免得再整读一遍（上传件可达 256 MiB）。
    """
    rec_path = source_record_path(file)
    rec = load_source_record(file)
    # 只认字符串：手改 / 截断的记录不能让调用方在 `redact_url` 等处抛 TypeError
    if rec is None or not isinstance(rec.get("final_url"), str) or not isinstance(rec.get("sha256"), str):
        return None
    if not rec["final_url"] or (rec.get("url") is not None and not isinstance(rec["url"], str)):
        return None
    for key in ("fetched_at", "charset", "fetched_via"):  # 可选字段：类型不对就当没有（`fetched_at[:10]` 等不能抛）
        if rec.get(key) is not None and not isinstance(rec[key], str):
            rec[key] = None
    if sha256 is None:
        sha256 = sha256_file(file)
        if sha256 is None:
            return None
    if sha256 != rec["sha256"]:
        if discard_stale:
            with contextlib.suppress(OSError):  # 删不掉只留一个作废记录（下次照样按哈希判作废），不能让入库因此失败
                rec_path.unlink(missing_ok=True)
        return None
    return rec
