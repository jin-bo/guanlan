"""P4.24 MCP Skills 扩展测试（见 docs/P4.24-MCP技能发布.md §9）。**全程零 LLM。**

缺 `guanlan-wiki[mcp]`（SDK v2）时整体 skip，镜像 `test_mcp.py`。夹具与传输辅助（真 stdio 子进程、真 uvicorn）
直接从 `test_mcp` 取，不复制一份。

自定义方法 `skills/list` / `skills/get` 不在 SDK 客户端的高层 API 里，进程内用例经 `client.session._dispatcher.
send_raw_request` 发——两种 `Client` mode 下它都是那条连接上真正发请求的对象（legacy 走真 JSON-RPC 帧，
auto 走 DirectDispatcher）。真帧上的现代 era 覆盖在 stdio / http 两条用例里。
"""

import asyncio
import hashlib
import logging
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.mcpserver")

from mcp import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from test_mcp import (  # noqa: E402
    _ok_runner,
    _running_http,
    _snapshot,
)
import test_mcp  # noqa: E402

kb_mcp = test_mcp.kb_mcp  # 复用同一个夹具（赋值而非 import，免得被当成被参数遮蔽的导入名）

from guanlan.mcp import server as mcp_server  # noqa: E402
from guanlan.mcp import skills as mcp_skills  # noqa: E402
from guanlan.mcp.server import build_mcp  # noqa: E402
from guanlan.pages import split_frontmatter  # noqa: E402

EXT = "io.modelcontextprotocol/skills"
URI = "skill://guanlan-query/SKILL.md"
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601


def _session(mcp, fn, *, mode="auto"):
    async def main():
        async with Client(mcp, mode=mode) as c:
            return await fn(c)

    return asyncio.run(main())


async def _raw(c, method, params=None):
    return await c.session._dispatcher.send_raw_request(method, params or {}, {})


def _error_code(mcp, method, params, *, mode="auto"):
    async def fn(c):
        try:
            await _raw(c, method, params)
        except MCPError as exc:
            return exc.error.code
        return None

    return _session(mcp, fn, mode=mode)


def _frontmatter(text: str) -> dict:
    import yaml

    block, _ = split_frontmatter(text)
    return yaml.safe_load(block)


# ───────────────────────── 协议面（现代 era） ─────────────────────────


def test_capabilities_declare_skills_extension_and_resources(kb_mcp):
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    caps = _session(mcp, lambda c: asyncio.sleep(0, c.server_capabilities))
    assert caps.extensions == {EXT: {}}  # 不声明 directoryRead（决策P4.24-8）
    assert caps.resources is not None  # 规范 MUST：声明扩展必须同时声明 resources


def test_skills_list_shape(kb_mcp):
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    res = _session(mcp, lambda c: _raw(c, "skills/list"))
    assert [s["uri"] for s in res["skills"]] == [URI]
    assert res["ttlMs"] == 3_600_000 and res["cacheScope"] == "private"  # 规范必填
    assert "nextCursor" not in res
    entry = res["skills"][0]
    assert entry["frontmatter"]["name"] == "guanlan-query"
    assert [r["uri"] for r in entry["resources"]] == [URI]  # 清单完整：含 SKILL.md 自身、且仅此一个


def test_skills_list_rejects_any_cursor(kb_mcp):
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    assert _error_code(mcp, "skills/list", {"cursor": "abc"}) == INVALID_PARAMS


def test_skills_get_returns_the_listed_entry(kb_mcp):
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def fn(c):
        listed = (await _raw(c, "skills/list"))["skills"][0]
        got = await _raw(c, "skills/get", {"uri": URI})
        return listed, got

    listed, got = _session(mcp, fn)
    assert got["skill"] == listed
    assert got["ttlMs"] == 3_600_000 and got["cacheScope"] == "private"


@pytest.mark.parametrize(
    "uri",
    [
        "skill://guanlan-wiki/SKILL.md",
        "skill://nope/SKILL.md",
        "skill://Guanlan-Query/SKILL.md",
        "skill://guanlan-query/SKILL.md/",
        "skill://guanlan-query/./SKILL.md",
        "skill://x/../guanlan-query/SKILL.md",
        "skill://guanlan-query",
    ],
)
def test_skills_get_unknown_or_variant_uri_is_invalid_params(kb_mcp, uri):
    """不做任何 URI 归一（决策P4.24-11）：变体一律「未发布」。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    assert _error_code(mcp, "skills/get", {"uri": uri}) == INVALID_PARAMS


def test_manifest_matches_served_bytes_and_frontmatter(kb_mcp):
    """**核心用例**：清单里每个文件经 `resources/read` 读回的字节，大小、摘要都与清单一致；独立解析所读文本的
    frontmatter，与条目逐键相等。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def fn(c):
        entry = (await _raw(c, "skills/list"))["skills"][0]
        reads = {r["uri"]: await c.read_resource(r["uri"]) for r in entry["resources"]}
        return entry, reads

    entry, reads = _session(mcp, fn)
    assert URI in reads
    assert entry["uri"].rsplit("/", 2)[-2] == entry["frontmatter"]["name"]
    for item in entry["resources"]:
        (content,) = reads[item["uri"]].contents
        assert content.mime_type == "text/markdown"
        data = content.text.encode("utf-8")
        assert len(data) == item["size"]
        assert "sha256:" + hashlib.sha256(data).hexdigest() == item["digest"]
    assert _frontmatter(reads[URI].contents[0].text) == entry["frontmatter"]


def test_served_bytes_are_the_packaged_file_verbatim(kb_mcp):
    """同一份字节（决策P4.24-6）：所发内容就是包里那个文件的原始字节，没有被 BOM / 行尾处理过。"""
    raw = (mcp_skills.PUBLISHED_SKILLS_DIR / "guanlan-query" / "SKILL.md").read_bytes()
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    read = _session(mcp, lambda c: c.read_resource(URI))
    assert read.contents[0].text.encode("utf-8") == raw


@pytest.mark.parametrize(
    "uri",
    [
        "skill://guanlan-query/../../wiki/index.md",
        "skill://guanlan-query/other.md",
        "skill://guanlan-wiki/SKILL.md",
        "file:///etc/passwd",
        "guanlan://wiki/entities/DeFi.md",
        "wiki/entities/DeFi.md",
    ],
)
def test_arbitrary_resource_uris_are_unreachable(kb_mcp, uri):
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def fn(c):
        try:
            await c.read_resource(uri)
        except MCPError as exc:
            return exc.error.code
        return None

    assert _session(mcp, fn) == INVALID_PARAMS


def test_resources_list_holds_only_the_skill(kb_mcp):
    """wiki 页不作为资源暴露（P4.10 §3.3 仍未排期）；资源面只有这一个 skill 文件。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    listed = _session(mcp, lambda c: c.list_resources())
    assert [str(r.uri) for r in listed.resources] == [URI]


def test_entry_passes_agentaos_own_client_verification(kb_mcp):
    """客户端兼容：用 agentao 0.5.10 激活时**实际走的**校验——条目校验、读后大小/摘要、frontmatter 逐字段。"""
    skills = pytest.importorskip("agentao.mcp.skills")
    if not hasattr(skills, "validate_entry"):  # pragma: no cover - 锁文件已是 0.5.10
        pytest.skip("agentao < 0.5.10：无 Skills 客户端")

    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def fn(c):
        raw_entry = (await _raw(c, "skills/list"))["skills"][0]
        return raw_entry, await c.read_resource(URI)

    raw_entry, read = _session(mcp, fn)
    entry = skills.validate_entry("guanlan", raw_entry)
    assert entry.unavailable is None  # 没超任何上限（含 agentao 自加的 SKILL.md ≤100,000 字节）
    (only,) = entry.files
    text = read.contents[0].text
    skills.verify(text.encode("utf-8"), only)
    skills.check_frontmatter(text, entry)


# ───────────────────────── 兼容面 ─────────────────────────


def test_legacy_era_has_no_extension_and_no_skill_methods(kb_mcp):
    """握手时代：能力里没有 `extensions`，扩展方法也不应答（决策P4.24-2），工具照常。"""
    mcp = build_mcp(kb_mcp, runner=_ok_runner)
    caps = _session(mcp, lambda c: asyncio.sleep(0, c.server_capabilities), mode="legacy")
    assert not caps.extensions
    assert _error_code(mcp, "skills/list", {}, mode="legacy") == METHOD_NOT_FOUND
    assert _error_code(mcp, "skills/get", {"uri": URI}, mode="legacy") == METHOD_NOT_FOUND
    res = _session(mcp, lambda c: c.call_tool("read_page", {"name": "DeFi"}), mode="legacy")
    assert res.is_error is False


def test_extension_adds_no_tools(kb_mcp, monkeypatch):
    with_skill = build_mcp(kb_mcp, runner=_ok_runner)
    monkeypatch.setattr(mcp_server, "load_query_skill", lambda: None)
    without = build_mcp(kb_mcp, runner=_ok_runner)
    names = lambda m: {t.name for t in _session(m, lambda c: c.list_tools()).tools}  # noqa: E731
    assert names(with_skill) == names(without)


def test_skill_methods_are_gated_to_the_sdks_modern_revisions():
    """跟 SDK 的现代修订集合走，而不是写死日期——SDK 支持新修订时扩展不会悄悄消失。"""
    from mcp.types.version import MODERN_PROTOCOL_VERSIONS

    bundle = mcp_skills.load_query_skill()
    for binding in mcp_skills.GuanlanSkills(bundle).methods():
        assert binding.protocol_versions == frozenset(MODERN_PROTOCOL_VERSIONS)


# ───────────────────────── 传输面 ─────────────────────────


async def _client_side_roundtrip(transport):
    """以**真实客户端的方式**走一遍：SDK `Client` 在现代 era 连上（discover），再用 agentao 0.5.10 自己的
    `skills/list` / `skills/get` 请求模型经 `session.send_request` 发——它会盖上现代 era 必需的 per-request
    `_meta` 信封（协议版本 / 客户端能力），这正是 agentao 作客户端时线上实际发的帧。

    ⚠️ 不能手写 JSON 帧了事：http 上不带 `Mcp-Protocol-Version` 头的请求被当成**握手时代**（实测落到
    `2025-03-26`），扩展方法按决策P4.24-2 回 -32601；带了头又必须带齐信封键。手写帧很容易「以为在测现代
    era、其实在测旧 era」。
    """
    from agentao.mcp import skills as client_skills

    adapter = client_skills.result_adapter()
    async with Client(transport) as c:
        listed = await c.session.send_request(client_skills.list_skills_request(None), adapter)
        got = await c.session.send_request(client_skills.get_skill_request(URI), adapter)
        read = await c.read_resource(URI)
        return c.protocol_version, c.server_capabilities, listed, got, read


def _assert_client_roundtrip(result):
    from mcp.types.version import MODERN_PROTOCOL_VERSIONS

    version, caps, listed, got, read = result
    assert version in MODERN_PROTOCOL_VERSIONS  # 确实在现代 era 上
    assert caps.extensions == {EXT: {}}
    (entry,) = listed["skills"]
    assert got["skill"] == entry
    data = read.contents[0].text.encode("utf-8")
    (item,) = entry["resources"]
    assert len(data) == item["size"]
    assert "sha256:" + hashlib.sha256(data).hexdigest() == item["digest"]


def test_stdio_subprocess_serves_skills_to_a_modern_client(kb_mcp):
    """真 stdio 子进程（`guanlan -C <kb> mcp`）+ SDK 现代 era 客户端：扩展、两个方法、资源读取全通。
    stdout 只承载 JSON-RPC 帧由 `test_mcp.py::test_stdio_subprocess_emits_only_jsonrpc_frames` 守。"""
    import sys

    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "guanlan.cli", "-C", str(kb_mcp), "mcp"]
    )
    _assert_client_roundtrip(asyncio.run(_client_side_roundtrip(stdio_client(params))))


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_http_serves_skills_behind_the_token_gate(kb_mcp):
    """真 Streamable HTTP：带 token 的现代 era 客户端全通；不带 token → 401，扩展方法不绕过闸。"""
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    mcp = build_mcp(kb_mcp, runner=_ok_runner, allow_ask=False)
    app = mcp_server._build_http_app(mcp, host="127.0.0.1", allowed_hosts=None, token="s3cret")

    with _running_http(app) as base:
        authed = httpx2.AsyncClient(headers={"authorization": "Bearer s3cret"})
        result = asyncio.run(
            _client_side_roundtrip(streamable_http_client(f"{base}/mcp", http_client=authed))
        )
        unauth = [
            httpx2.post(
                f"{base}/mcp",
                headers={"content-type": "application/json", "accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p},
            ).status_code
            for m, p in [("skills/list", {}), ("skills/get", {"uri": URI}), ("resources/read", {"uri": URI})]
        ]

    _assert_client_roundtrip(result)
    assert unauth == [401, 401, 401]


# ───────────────────────── 打包与降级 ─────────────────────────


def test_packaged_skill_constraints():
    """打包期守卫：文件位置、大小、frontmatter 形状、正文里的关键约束。"""
    from guanlan import skill as local_skills

    skill_dir = mcp_skills.PUBLISHED_SKILLS_DIR / "guanlan-query"
    raw = (skill_dir / "SKILL.md").read_bytes()
    # 单文件（决策P4.24-4）；点文件（如 Finder 的 .DS_Store）不算 skill 内容，与加载器同口径。
    assert sorted(p.name for p in skill_dir.iterdir() if not p.name.startswith(".")) == ["SKILL.md"]
    assert len(raw) < 100_000  # agentao 对 SKILL.md 的上限（激活后每轮随请求发送）
    fm = _frontmatter(raw.decode("utf-8"))
    assert set(fm) == {"name", "description"} and all(isinstance(v, str) for v in fm.values())
    assert fm["name"] == skill_dir.name and 0 < len(fm["description"]) <= 1024
    text = raw.decode("utf-8")
    assert "检索、读页和顺链始终使用同一服务端" in text  # 决策P4.24-14：多库并连不串库
    assert 'read_page(name="名字")' in text  # 顺链走按名读页（P4.24 §4）
    # 不进本地 skill 安装面、也不在 Agentao 的开发态发现路径上（决策P4.24-5）。
    assert "guanlan-query" not in local_skills.BUNDLED_SKILL_NAMES
    repo_skills = Path(local_skills.__file__).parent.parent / "skills"
    assert not (repo_skills / "guanlan-query").exists()


def test_skill_ships_inside_the_package():
    """随 `packages = ["guanlan"]` 进 wheel：文件必须在包目录内，且不被 .gitignore 排除（hatch 按它过滤）。"""
    import subprocess

    import guanlan

    skill_file = mcp_skills.PUBLISHED_SKILLS_DIR / "guanlan-query" / "SKILL.md"
    pkg = Path(guanlan.__file__).parent
    assert skill_file.resolve().is_relative_to(pkg.resolve())
    repo = pkg.parent
    if (repo / ".git").exists():
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(skill_file)], cwd=repo, check=False
        ).returncode
        assert ignored == 1  # 1 = 未被忽略


def _write_skill(tmp_path: Path, name: str, content: bytes, *, extra: str | None = None) -> Path:
    d = tmp_path / "published" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(content)
    if extra:
        (d / extra).write_text("x", encoding="utf-8")
    return d.parent


_GOOD = "---\nname: guanlan-query\ndescription: 测试用描述\n---\n\n# 正文\n"


@pytest.mark.parametrize(
    "content, extra, reason",
    [
        (None, None, "读不到"),
        (b"\xef\xbb\xbf" + _GOOD.encode(), None, "BOM"),
        (b"\xff\xfe not utf8", None, "UTF-8"),
        (b"# no frontmatter\n", None, "frontmatter"),
        (b"---\nname: [unclosed\n---\n", None, "frontmatter"),
        (b"---\nname: guanlan-query\ndescription: d\nversion: 1\n---\n", None, "字符串"),
        (b"---\nname: other-name\ndescription: d\n---\n", None, "不一致"),
        (b"---\nname: Bad_Name\ndescription: d\n---\n", None, "name"),
        (b"---\nname: guanlan-query\ndescription: '  '\n---\n", None, "description"),
        (_GOOD.encode(), "notes.md", "除 SKILL.md 外"),
    ],
)
def test_broken_skill_degrades_without_breaking_tools(
    kb_mcp, tmp_path, monkeypatch, caplog, capsys, content, extra, reason
):
    """打包错误 → 不挂扩展、工具照常、WARNING 走 logging 不进 stdout（决策P4.24-10 / 决策P4.10-13）。"""
    if content is None:
        root = tmp_path / "published"
        (root / "guanlan-query").mkdir(parents=True)
    else:
        root = _write_skill(tmp_path, "guanlan-query", content, extra=extra)
    monkeypatch.setattr(mcp_skills, "PUBLISHED_SKILLS_DIR", root)

    with caplog.at_level(logging.WARNING, logger="guanlan.mcp"):
        mcp = build_mcp(kb_mcp, runner=_ok_runner)
    assert any(reason in r.getMessage() for r in caplog.records), [r.getMessage() for r in caplog.records]
    assert capsys.readouterr().out == ""

    caps = _session(mcp, lambda c: asyncio.sleep(0, c.server_capabilities))
    assert not caps.extensions
    assert _error_code(mcp, "skills/list", {}) == METHOD_NOT_FOUND
    assert _session(mcp, lambda c: c.call_tool("read_page", {"name": "DeFi"})).is_error is False


def test_dotfiles_do_not_block_publishing_but_real_extras_still_do(tmp_path):
    """macOS Finder 在目录里留的 `.DS_Store` 不能让 skill 静默不发布；但点文件之外的额外文件仍须被拒——
    这条过滤规则只放过点文件，不能顺手把真正会让清单不完整的文件也吞掉。"""
    root = _write_skill(tmp_path, "guanlan-query", _GOOD.encode(), extra=".DS_Store")
    bundle = mcp_skills.load_skill_bundle(root / "guanlan-query")
    assert [r["uri"] for r in bundle.entry()["resources"]] == [URI]

    (root / "guanlan-query" / "references.md").write_text("x", encoding="utf-8")
    with pytest.raises(mcp_skills.SkillBundleError, match="references.md"):
        mcp_skills.load_skill_bundle(root / "guanlan-query")


def test_crlf_skill_is_served_byte_exact(kb_mcp, tmp_path, monkeypatch):
    """行尾不归一：CRLF 的 skill（如 Windows 上 git autocrlf 检出）照样发、摘要算的就是所发的那份。"""
    raw = _GOOD.replace("\n", "\r\n").encode()
    monkeypatch.setattr(mcp_skills, "PUBLISHED_SKILLS_DIR", _write_skill(tmp_path, "guanlan-query", raw))
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def fn(c):
        return (await _raw(c, "skills/list"))["skills"][0], await c.read_resource(URI)

    entry, read = _session(mcp, fn)
    data = read.contents[0].text.encode("utf-8")
    assert data == raw
    assert entry["resources"][0]["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_full_skill_surface_zero_kb_write(kb_mcp):
    before = _snapshot(kb_mcp)
    mcp = build_mcp(kb_mcp, runner=_ok_runner)

    async def fn(c):
        await _raw(c, "skills/list")
        await _raw(c, "skills/get", {"uri": URI})
        await c.read_resource(URI)
        await c.list_resources()

    _session(mcp, fn)
    assert _snapshot(kb_mcp) == before
