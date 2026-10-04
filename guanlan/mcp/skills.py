"""经 MCP Skills 扩展发布只读 `guanlan-query`（P4.24，见 docs/P4.24-MCP技能发布.md §7）。

`io.modelcontextprotocol/skills`（SEP-2640）让服务端把一个 skill 和它的文件清单发出去，客户端经技能发现
列出、经用户同意激活。本模块只服务**一份静态 skill**，不是通用技能框架（§10）：

- **能力**：`capabilities.extensions["io.modelcontextprotocol/skills"] = {}`（不声明 `directoryRead`，
  决策P4.24-8）。规范要求同时声明的 `resources` 能力由 `MCPServer` 恒声明，零代码。
- **方法**：`skills/list`、`skills/get`，只在 SDK 的现代协议修订上服务（决策P4.24-2）——握手时代的
  `initialize` 里根本没有 `extensions` 字段，那里还应答扩展方法等于一个没声明的能力。
- **资源**：唯一一个 `skill://guanlan-query/SKILL.md`（`TextResource`）。不注册模板、不注册
  `FileResource`：SDK 的资源查找先按 URI 精确匹配，于是任何 `..` / `file://` 变体都落到「未知资源」-32602，
  **不存在**路径解析这一步。

**清单与所发内容是同一份字节**（决策P4.24-6）：起服时 `read_bytes()` 读一次，size / sha256 / 资源文本 /
frontmatter 全由它派生，之后不再碰磁盘。严格 `utf-8` decode（**不是** `utf-8-sig`）、不归一行尾——SDK 的
`FileResource` 会吃掉 BOM、把 CRLF 转成 LF，所发字节与摘要对不上，故不用它。

skill 文件住在包内 `published_skills/` 而不是仓库根 `skills/`：后者是 Agentao 的 skill 发现路径（开发态仓库根
即示例 wiki），放那里会被本地会话当成本地 skill 列出——而它引用的是**本服务端的** `search` / `read_page`，
本地 agent 没有这些工具（决策P4.24-5）。随 `packages = ["guanlan"]` 自动进 wheel。
"""

from __future__ import annotations

import copy
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from mcp.server.extension import Extension, MethodBinding, ResourceBinding
from mcp.server.mcpserver.resources import TextResource
from mcp.shared.exceptions import MCPError
from mcp.types.version import MODERN_PROTOCOL_VERSIONS
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, RequestParams

from ..pages import split_frontmatter

__all__ = [
    "PUBLISHED_SKILLS_DIR",
    "QUERY_SKILL_NAME",
    "SKILLS_EXTENSION",
    "GuanlanSkills",
    "SkillBundle",
    "SkillBundleError",
    "load_query_skill",
    "load_skill_bundle",
]

_logger = logging.getLogger("guanlan.mcp")

SKILLS_EXTENSION = "io.modelcontextprotocol/skills"
QUERY_SKILL_NAME = "guanlan-query"
PUBLISHED_SKILLS_DIR = Path(__file__).parent / "published_skills"

# Agent Skills 规范：name 1–64、[a-z0-9-]、无首尾/连续连字符；description 1–1024 字符。
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NAME_MAX = 64
_DESCRIPTION_MAX = 1024
_SKILL_FILE = "SKILL.md"
_BOM = b"\xef\xbb\xbf"

# 决策P4.24-7：内容在进程生命期内不变，1h 只是给客户端一个复查周期；服务可能挂在 bearer token 后，
# 不鼓励共享缓存层缓存它（`private`），代价为零。`cache_hints` 不收 `skills/*`，故由 handler 自己填。
_TTL_MS = 3_600_000
_CACHE_SCOPE = "private"

# 决策P4.24-2：取 SDK 自己的现代修订集合，而不是写死一个日期——SDK 支持新修订时扩展随之可用，
# 不会悄悄在新修订上消失。
_MODERN = frozenset(MODERN_PROTOCOL_VERSIONS)


class SkillBundleError(ValueError):
    """skill 文件不可发布（缺失 / 编码 / frontmatter / 目录形状）。只可能是打包错误。"""


@dataclass(frozen=True)
class SkillBundle:
    """一份已校验、可发布的单文件 skill。所有字段都派生自同一份原始字节（决策P4.24-6）。"""

    name: str
    text: str
    frontmatter: dict[str, Any] = field(hash=False)
    size: int
    digest: str  # "sha256:<64 位小写十六进制>"

    @property
    def uri(self) -> str:
        return f"skill://{self.name}/{_SKILL_FILE}"

    def entry(self) -> dict[str, Any]:
        """`skills/list` 与 `skills/get` 共用的条目（规范 §Skill）。清单完整：唯一的文件就是 SKILL.md 自身。"""
        return {
            "uri": self.uri,
            "frontmatter": copy.deepcopy(self.frontmatter),
            "resources": [{"uri": self.uri, "digest": self.digest, "size": self.size}],
        }


def load_skill_bundle(skill_dir: Path) -> SkillBundle:
    """读一个单文件 skill 目录并校验；不合规 → `SkillBundleError`。

    **目录里只许有 `SKILL.md`**：清单按规范必须完整，而本模块只发这一个文件。日后若给 skill 加参考文件，
    这里会拒绝，逼着改的人把清单与资源一起扩展——否则清单会悄悄漏文件、违反规范。
    """
    skill_dir = Path(skill_dir)
    skill_file = skill_dir / _SKILL_FILE
    try:
        raw = skill_file.read_bytes()
    except OSError as exc:
        raise SkillBundleError(f"读不到 {skill_file}：{exc}") from exc
    # 点文件（macOS Finder 的 `.DS_Store` 等）不是 skill 内容、也不进 wheel（.gitignore），不计入；否则开发态
    # 在 Finder 里打开过该目录就会让 skill 静默不发布。
    extras = sorted(
        p.name for p in skill_dir.iterdir() if p.name != _SKILL_FILE and not p.name.startswith(".")
    )
    if extras:
        raise SkillBundleError(f"{skill_dir} 里除 SKILL.md 外还有 {extras}：清单只含 SKILL.md，会不完整")
    if raw.startswith(_BOM):
        raise SkillBundleError(f"{skill_file} 以 BOM 开头：客户端按原始字节校验摘要，BOM 会被不同解析器区别对待")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillBundleError(f"{skill_file} 不是合法 UTF-8：{exc}") from exc
    if text.encode("utf-8") != raw:  # 严格 utf-8 下恒成立；留作「同一份字节」的显式断言
        raise SkillBundleError(f"{skill_file} 解码后无法逐字节还原")

    block, _body = split_frontmatter(text)
    if block is None:
        raise SkillBundleError(f"{skill_file} 缺少 YAML frontmatter")
    try:
        frontmatter = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise SkillBundleError(f"{skill_file} frontmatter 解析失败：{exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillBundleError(f"{skill_file} frontmatter 不是映射")
    # 只用字符串标量：`safe_load` 会把日期/数字转型，与客户端各自的 YAML 解析器未必一致，而客户端会把
    # 读到的 frontmatter 与条目逐字段比对——不一致即加载失败。
    for key, value in frontmatter.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise SkillBundleError(f"{skill_file} frontmatter 只许字符串键值：{key!r}")

    name = frontmatter.get("name", "")
    if not (0 < len(name) <= _NAME_MAX and _NAME_RE.fullmatch(name)):
        raise SkillBundleError(f"{skill_file} 的 name 不合规：{name!r}")
    if name != skill_dir.name:
        raise SkillBundleError(f"{skill_file} 的 name {name!r} 与目录名 {skill_dir.name!r} 不一致")
    description = frontmatter.get("description", "")
    if not (0 < len(description.strip()) and len(description) <= _DESCRIPTION_MAX):
        raise SkillBundleError(f"{skill_file} 的 description 须 1–{_DESCRIPTION_MAX} 字符")

    return SkillBundle(
        name=name,
        text=text,
        frontmatter=frontmatter,
        size=len(raw),
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
    )


def load_query_skill() -> SkillBundle | None:
    """加载随包的 `guanlan-query`；失败 → 记 WARNING、返回 None（决策P4.24-10：降级而不拒启）。

    这些失败只可能是打包错误，不该连累既有工具：调用方据 None 不挂扩展、工具照常。WARNING 走 logging
    （默认 stderr），绝不碰 stdout——stdio 传输下 stdout 即 JSON-RPC 帧（决策P4.10-13）。
    """
    try:
        return load_skill_bundle(PUBLISHED_SKILLS_DIR / QUERY_SKILL_NAME)
    except SkillBundleError as exc:
        _logger.warning("未发布 MCP skill %s（工具不受影响）：%s", QUERY_SKILL_NAME, exc)
        return None


class GetSkillParams(RequestParams):
    """`skills/get` 入参：`uri` 即 SKILL.md 的 URI。"""

    uri: str


class GuanlanSkills(Extension):
    """把一份 `SkillBundle` 经 Skills 扩展发布出去（能力 + 两个方法 + 一个资源）。"""

    identifier = SKILLS_EXTENSION

    def __init__(self, bundle: SkillBundle) -> None:
        self._bundle = bundle
        self._entry = bundle.entry()

    def settings(self) -> dict[str, Any]:
        return {}  # 不声明 directoryRead：声明了就必须实现 resources/directory/read（决策P4.24-8）

    def resources(self) -> list[ResourceBinding]:
        b = self._bundle
        return [
            ResourceBinding(
                TextResource(
                    uri=b.uri,
                    name=b.frontmatter["name"],
                    description=b.frontmatter["description"],
                    mime_type="text/markdown",
                    text=b.text,
                )
            )
        ]

    def methods(self) -> list[MethodBinding]:
        return [
            MethodBinding("skills/list", PaginatedRequestParams, self._list, protocol_versions=_MODERN),
            MethodBinding("skills/get", GetSkillParams, self._get, protocol_versions=_MODERN),
        ]

    def _result(self, key: str, value: Any) -> dict[str, Any]:
        return {key: value, "ttlMs": _TTL_MS, "cacheScope": _CACHE_SCOPE}

    async def _list(self, ctx: Any, params: PaginatedRequestParams) -> dict[str, Any]:
        # 一页回完、从不发 nextCursor；故任何非空 cursor 都不是我们发的，按规范视为无效参数。
        if params.cursor:
            raise MCPError(code=INVALID_PARAMS, message=f"Invalid cursor: {params.cursor}")
        return self._result("skills", [copy.deepcopy(self._entry)])

    async def _get(self, ctx: Any, params: GetSkillParams) -> dict[str, Any]:
        # 精确匹配、不做大小写/尾斜杠/编码归一（决策P4.24-11）：任何变体都是「未发布」。
        if params.uri != self._bundle.uri:
            raise MCPError(code=INVALID_PARAMS, message=f"No skill is served at {params.uri}")
        return self._result("skill", copy.deepcopy(self._entry))
