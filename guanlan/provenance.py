"""`raw_digest` provenance 归口（P3.7，见 docs/P3.7-语义审计.md §4）。**零 LLM。**

`raw_digest` 是 `source` 摘要页（`wiki/sources/<slug>.md`）frontmatter 上的可选单标量
`'<raw相对库根路径>@sha256:<hex>'`，记「本摘要页建自该 raw 的这个版本」——source-drift
可判定的拱心石（决策P3.7-3）。它**对 `check` 不可见**（不校验、不阻断，决策P3.7-3b），合法性
由 audit 宽松自读（缺/坏 → 当无信号跳过）。

本模块是两处写入主体（ingest 后初 stamp / audit 后刷新，均由 **wrapper** 确定性执行，决策
P3.7-3a Option A）共用的**无状态原语**，杜绝两处漂移：

- `compute_raw_digest` / `format_digest_value` / `parse_digest_value`：算/拼/解 digest 标量；
- `admit_raw_path`：raw 路径**严格准入**（相对 + `raw/` 前缀 + realpath 不越界，决策P3.7-12）——
  `check` 不校验 `raw_digest`，手写/损坏字段可能含越界路径，比 hash / 喂 Agent 前必须自挡穿越；
- `stamp_raw_digest`：**YAML-safe** 写入（`split_frontmatter` → 设键 → `yaml.safe_dump`，**绝不
  裸拼**，复用 `rawio.apply_origin` 同款做法，决策P3.7-9）+ **写后 check**（重解析确认 frontmatter
  仍合法、值往返一致）+ 失败**回滚**到写前字节——「宁可不 stamp 也绝不留坏页」。
"""

from __future__ import annotations

import hashlib
import posixpath
from pathlib import Path

import yaml

from .pages import split_frontmatter

__all__ = [
    "RAW_DIGEST_KEY",
    "DIGEST_MARKER",
    "compute_raw_digest",
    "format_digest_value",
    "parse_digest_value",
    "admit_raw_path",
    "stamp_raw_digest",
]

# source 页 frontmatter 上的 provenance 字段名（wrapper 托管、请勿手改）。
RAW_DIGEST_KEY = "raw_digest"
# digest 标量分隔符：`<raw路径>@sha256:<hex>`。路径含 `@sha256:` 字面量的概率极低，且解析用
# rfind 取最后一处分隔，故仍可正确切出。
DIGEST_MARKER = "@sha256:"
_HEX = frozenset("0123456789abcdef")


def compute_raw_digest(raw_file: Path) -> str:
    """raw 文件现字节的 sha256 十六进制摘要（小写）。"""
    return hashlib.sha256(raw_file.read_bytes()).hexdigest()


def format_digest_value(raw_rel: str, sha_hex: str) -> str:
    """拼 `raw_digest` 标量值 `'<raw_rel>@sha256:<sha_hex>'`（决策P3.7-3 字段形态）。"""
    return f"{raw_rel}{DIGEST_MARKER}{sha_hex}"


def parse_digest_value(value: object) -> tuple[str, str] | None:
    """解 `raw_digest` 标量 → `(raw相对路径, sha256_hex)`；非串/格式坏/hex 非法 → None。

    宽松读：解析失败一律返回 None（audit 据此「当无信号跳过」，绝不抛、不报错，决策P3.7-12）。
    """
    if not isinstance(value, str):
        return None
    idx = value.rfind(DIGEST_MARKER)
    if idx <= 0:  # 无分隔符 / 路径段为空
        return None
    raw_rel = value[:idx]
    sha_hex = value[idx + len(DIGEST_MARKER):]
    if not raw_rel or len(sha_hex) != 64 or any(c not in _HEX for c in sha_hex):
        return None
    return raw_rel, sha_hex


def admit_raw_path(kb: Path, raw_rel: str) -> Path | None:
    """raw 路径准入（决策P3.7-12，形状仿 P5.2.1 `_admit_image_ref`）：通过 → 落盘绝对路径，否则 None。

    仅接受「相对库根、以 `raw/` 开头、`realpath` 解析后仍在 `<kb>/raw/` 下」的路径——绝对路径 /
    反斜杠 / `../` 越界 / 不以 `raw/` 起 / symlink 逃逸一律 None（当无信号跳过，不抢 `check` 缺源口径）。
    边界取 `(kb/"raw").resolve()`：raw/ 本身可为指向库外存储的符号链接，故按其 realpath 判越界。
    """
    if not isinstance(raw_rel, str) or not raw_rel:
        return None
    if raw_rel.startswith("/") or "\\" in raw_rel:
        return None  # 绝对路径 / 反斜杠（Windows 风/穿越变体）
    norm = posixpath.normpath(raw_rel)
    if norm != "raw" and not norm.startswith("raw/"):
        return None  # normpath 归一后必须仍以 raw/ 起（`raw/../x` 会塌成 `x` 被此挡下）
    if norm == "raw":
        return None  # 指向 raw/ 目录本身、非文件
    raw_root = (kb / "raw").resolve()
    candidate = (kb / norm).resolve()  # 解 symlink，捕获 raw/link-to-outside/secret 逃逸
    try:
        candidate.relative_to(raw_root)
    except ValueError:
        return None
    return candidate


def stamp_raw_digest(source_page: Path, digest_value: str) -> bool:
    """把 `raw_digest` 写进 source 页 frontmatter（YAML-safe + 写后 check + 回滚）。返回是否成功落值。

    决策P3.7-9：**绝不裸拼**——`split_frontmatter` → 设 `raw_digest` 键 → `yaml.safe_dump`
    （`allow_unicode=True, sort_keys=False`，复用 `rawio.apply_origin` 同款做法）；写后重解析确认
    frontmatter 仍是合法映射、`raw_digest` 值往返一致，不过则回滚到写前字节、返回 False。

    跳过（返回 False、不写盘）：页不存在 / 是符号链接 / 无 frontmatter 块 / 块本就不可解析或非映射
    —— 「定位不到 / 该页 frontmatter 本就不可解析 → 跳过」（决策P3.7-9，调用方据此记降级、不阻断）。
    已是现值 → 幂等返回 True、不写盘。
    """
    if source_page.is_symlink() or not source_page.is_file():
        return False
    try:
        original = source_page.read_text(encoding="utf-8")
    except OSError:
        return False
    block, body = split_frontmatter(original)
    if block is None:  # 无 frontmatter 块（含未闭合）→ 不 stamp
        return False
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError:
        return False
    if not isinstance(meta, dict):  # 非键值映射 → 不 stamp（本就不可解析）
        return False
    if meta.get(RAW_DIGEST_KEY) == digest_value:
        return True  # 幂等：已是现值，零写盘
    meta[RAW_DIGEST_KEY] = digest_value
    dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    new_text = f"---\n{dumped}---\n{body}"
    try:
        source_page.write_text(new_text, encoding="utf-8")
    except OSError:
        return False
    if _verify_stamp(source_page, digest_value):
        return True
    # 写后 check 不过 → 回滚到写前字节（宁可不 stamp 也绝不留坏页）。
    try:
        source_page.write_text(original, encoding="utf-8")
    except OSError:
        pass
    return False


def _verify_stamp(source_page: Path, digest_value: str) -> bool:
    """写后 check：重读该页，确认 frontmatter 仍是合法映射且 `raw_digest` 值往返一致。"""
    try:
        block, _body = split_frontmatter(source_page.read_text(encoding="utf-8"))
    except OSError:
        return False
    if block is None:
        return False
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError:
        return False
    return isinstance(meta, dict) and meta.get(RAW_DIGEST_KEY) == digest_value
