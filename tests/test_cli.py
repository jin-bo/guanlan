"""P2 CLI 接线测试：-C/--dir 在子命令前后都可用；各子命令解析正确。"""

import pytest

from guanlan.cli import build_parser


def _parse(argv):
    return build_parser().parse_args(argv)


def test_dir_before_subcommand():
    args = _parse(["-C", "/kb", "check"])
    assert args.dir == "/kb"
    assert args.command == "check"


def test_dir_after_subcommand():
    args = _parse(["check", "-C", "/kb", "--json"])
    assert args.dir == "/kb"
    assert args.json is True


def test_dir_after_subcommand_ingest():
    args = _parse(["ingest", "raw/x.md", "-C", "/kb"])
    assert args.dir == "/kb"
    assert args.target == "raw/x.md"


def test_dir_absent_is_suppressed():
    # 未给 -C 时不应在 namespace 留下 dir（main 再统一回落到当前目录）。
    args = _parse(["check"])
    assert not hasattr(args, "dir")


def test_query_backfill_flag():
    args = _parse(["query", "问题", "--backfill"])
    assert args.backfill is True
    assert args.question == "问题"


def test_init_uses_positional_path():
    args = _parse(["init", "/tmp/x"])
    assert args.path == "/tmp/x"


def test_init_honors_dir_when_no_positional(tmp_path):
    from guanlan.cli import _cmd_init

    # `guanlan -C <dir> init`：无位置参数时落到 -C 指定的目录，而非当前目录。
    args = _parse(["-C", str(tmp_path), "init"])
    assert _cmd_init(args) == 0
    assert (tmp_path / "wiki" / "index.md").is_file()


def test_init_positional_wins_over_dir(tmp_path):
    from guanlan.cli import _cmd_init

    target = tmp_path / "explicit"
    args = _parse(["-C", str(tmp_path / "ignored"), "init", str(target)])
    assert _cmd_init(args) == 0
    assert (target / "wiki" / "index.md").is_file()
    assert not (tmp_path / "ignored").exists()


def test_init_after_subcommand_dir(tmp_path):
    args = _parse(["init", "-C", str(tmp_path)])
    assert args.dir == str(tmp_path)
    assert args.path is None


def test_missing_subcommand_errors():
    with pytest.raises(SystemExit):
        _parse([])


# ---------- P3 子命令接线 ----------


def test_health_flags():
    args = _parse(["health", "-C", "/kb", "--json", "--strict"])
    assert args.command == "health"
    assert args.dir == "/kb" and args.json is True and args.strict is True


def test_lint_flags_default_false():
    args = _parse(["lint"])
    assert args.command == "lint"
    assert args.json is False and args.strict is False


def test_graph_json_only_flag():
    args = _parse(["graph", "--json-only"])
    assert args.command == "graph"
    assert args.json_only is True


def test_graph_dir_before_subcommand():
    args = _parse(["-C", "/kb", "graph"])
    assert args.dir == "/kb" and args.json_only is False


def test_graph_rejects_json_prefix_abbrev():
    # graph 无 stdout JSON 概念：`--json` 不该静默前缀命中 `--json-only`（allow_abbrev=False）。
    with pytest.raises(SystemExit):
        _parse(["graph", "--json"])


def test_heal_flags_default():
    args = _parse(["heal"])
    assert args.command == "heal"
    assert args.limit == 10 and args.min_refs == 2
    assert args.dry_run is False and args.json is False


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_heal_rejects_non_positive_limit(bad):
    """--limit 0/负数/非整数都报错，挡静默无操作（Codex 评审）。"""
    with pytest.raises(SystemExit):
        _parse(["heal", "--limit", bad])
    with pytest.raises(SystemExit):
        _parse(["heal", "--min-refs", bad])


def test_audit_flags_default():
    """`audit` 子命令（P3.7）：默认 --limit 10、dry_run/json False、-C 透传。"""
    args = _parse(["audit", "-C", "/kb"])
    assert args.command == "audit" and args.dir == "/kb"
    assert args.limit == 10
    assert args.dry_run is False and args.json is False and args.model is None


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_audit_rejects_non_positive_limit(bad):
    with pytest.raises(SystemExit):
        _parse(["audit", "--limit", bad])


def test_mcp_parser_defaults():
    """`mcp` 子命令：-C 透传、--model 默认 None（P4.10）；P4.17 http 旗标默认值（向后兼容，决策P4.17-1）。"""
    args = _parse(["-C", "/kb", "mcp"])
    assert args.command == "mcp" and args.dir == "/kb" and args.model is None
    # P4.17 默认：stdio、绑 127.0.0.1:8766、无 token、无额外 host、ask 关（http 下）。
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1" and args.port == 8766
    assert args.auth_token_env is None and args.allowed_host is None and args.allow_ask is False
    args2 = _parse(["mcp", "--model", "M", "-C", "/kb"])
    assert args2.dir == "/kb" and args2.model == "M"


def test_mcp_parser_http_flags():
    """P4.17 http 旗标解析：--transport/--host/--port/--auth-token-env/--allowed-host(可重复)/--allow-ask。"""
    args = _parse(
        [
            "-C", "/kb", "mcp",
            "--transport", "http",
            "--host", "0.0.0.0",
            "--port", "9000",
            "--auth-token-env", "GUANLAN_MCP_TOKEN",
            "--allowed-host", "kb.example.internal",
            "--allowed-host", "kb2.example.internal:8443",
            "--allow-ask",
        ]
    )
    assert args.transport == "http" and args.host == "0.0.0.0" and args.port == 9000
    assert args.auth_token_env == "GUANLAN_MCP_TOKEN"
    assert args.allowed_host == ["kb.example.internal", "kb2.example.internal:8443"]  # append 累积
    assert args.allow_ask is True


def test_mcp_parser_rejects_unknown_transport():
    """--transport 只接受 stdio/http（argparse choices）；其余用法错、非零退出。"""
    import pytest

    with pytest.raises(SystemExit):
        _parse(["-C", "/kb", "mcp", "--transport", "sse"])


def test_mcp_missing_extra_degrades(tmp_path, monkeypatch, capsys):
    """缺 mcp extra（mcp SDK 导入失败）→ EXIT_USAGE 并引导 `pip install 'guanlan-wiki[mcp]'`。

    在**装有** mcp 的 CI 也覆盖此路径：monkeypatch 令 `import mcp...` 抛 ImportError（决策P4.10-2/§7
    依赖门控；不能只靠『实际缺 SDK 的环境』，否则该环境整组 skip、降级路径永不被测）。
    """
    import sys

    from guanlan.cli import main

    # 同时清掉 guanlan.mcp* 与已缓存的 mcp.*：否则 `from mcp.server.fastmcp import FastMCP` 命中
    # 缓存的子模块、不重经父包 `mcp`（被打桩为 None）→ 降级路径不触发。monkeypatch 在 teardown 复原。
    for name in list(sys.modules):
        if (
            name in ("guanlan.mcp", "mcp")
            or name.startswith("guanlan.mcp.")
            or name.startswith("mcp.")
        ):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "mcp", None)  # `import mcp` → ImportError

    rc = main(["-C", str(tmp_path), "mcp"])
    assert rc == 1
    assert "guanlan-wiki[mcp]" in capsys.readouterr().err


def test_p3_dispatch_end_to_end(tmp_path):
    """三命令经 main 真正分发到各 entrypoint：在 init 出的库上各自退 0。"""
    from guanlan.cli import main

    assert main(["-C", str(tmp_path), "init"]) == 0
    assert main(["-C", str(tmp_path), "health"]) == 0
    assert main(["-C", str(tmp_path), "lint"]) == 0
    assert main(["-C", str(tmp_path), "graph", "--json-only"]) == 0
    assert (tmp_path / "graph" / "graph.json").is_file()
