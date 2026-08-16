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


def _evict_mcp_modules(monkeypatch):
    """清掉 guanlan.mcp* 与已缓存的 mcp.*：否则 `import mcp.server.mcpserver` 命中缓存的子模块、
    不重经被打桩的父包 `mcp` → 降级路径不触发。monkeypatch 在 teardown 复原。"""
    import sys

    for name in list(sys.modules):
        if (
            name in ("guanlan.mcp", "mcp")
            or name.startswith("guanlan.mcp.")
            or name.startswith("mcp.")
        ):
            monkeypatch.delitem(sys.modules, name, raising=False)


def test_mcp_missing_extra_degrades(tmp_path, monkeypatch, capsys):
    """完全缺 mcp extra → EXIT_USAGE 并引导 `pip install 'guanlan-wiki[mcp]'`，且报明版本口径。

    在**装有** mcp 的 CI 也覆盖此路径：monkeypatch 令 `import mcp...` 抛 ImportError（决策P4.10-2/§7
    依赖门控；不能只靠『实际缺 SDK 的环境』，否则该环境整组 skip、降级路径永不被测）。
    """
    import sys

    from guanlan.cli import main

    _evict_mcp_modules(monkeypatch)
    monkeypatch.setitem(sys.modules, "mcp", None)  # `import mcp` → ImportError

    rc = main(["-C", str(tmp_path), "mcp"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "guanlan-wiki[mcp]" in err
    # 版本口径必须在文案里（决策P4.18-9）：只提 extra 名字不足以让装着 1.x 的人自救。
    assert "mcp>=2" in err


def test_mcp_v1_installed_degrades_with_version_hint(tmp_path, monkeypatch, capsys):
    """**装着 mcp 1.x** → 同一条降级路径，且提示必须点明「升级到 mcp>=2」（决策P4.18-9）。

    这是 P4.18 硬切引入的**新**失败模式，与「完全没装」不同：pip 认为 `[mcp]` extra 已满足，重装一遍
    毫无效果。用一个只有顶层包、没有 `mcp.server.mcpserver` 的桩模拟 1.x 形状（v2 才有该模块）。
    """
    import sys
    import types

    from guanlan.cli import main

    _evict_mcp_modules(monkeypatch)
    monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))  # 有顶层包、无 v2 子模块

    rc = main(["-C", str(tmp_path), "mcp"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "mcp>=2" in err and "v1.x 已不支持" in err


def test_mcp_internal_import_error_is_not_masked(tmp_path, monkeypatch):
    """guanlan 自己模块链上的 ImportError **不得**被冒充成「缺 extra」——须原样抛出（决策P4.18-9）。

    守卫只探 SDK；把 `from .mcp import serve_mcp` 一起裹进 `except ImportError` 会让重构改名、半装的
    间接依赖等真实 bug 显示成"请装 extra"，用户照做仍失败且看不到真因。
    """
    import sys

    import pytest

    from guanlan.cli import main

    pytest.importorskip("mcp.server.mcpserver")  # 需真 SDK 在场，才能证「过了 SDK 探针之后」的行为
    _evict_mcp_modules(monkeypatch)
    # 让 guanlan 自己的模块链炸掉（模拟改名/半装依赖），SDK 本身完好。
    # **打在 `guanlan.mcp.server` 而不是包根 `guanlan.mcp` 上**：包根现在是 PEP 562 惰性壳，
    # 建 parser 时要从 `guanlan.mcp.defaults` 读默认值——把包根打死会让 ImportError 在
    # **解析期**就抛出，这条用例照样绿，却再也证明不了「`_cmd_mcp` 没把它冒充成缺 extra」
    # （日后真有人把 `from .mcp import serve_mcp` 裹进 except，这里也不会红）。打在子模块上
    # 既保住原意，也更贴近真实故障形态：重构改名坏的是某个子模块，不是整个包。
    monkeypatch.setitem(sys.modules, "guanlan.mcp.server", None)

    with pytest.raises(ImportError):
        main(["-C", str(tmp_path), "mcp"])


# ───────────── 宿主默认值：常量是唯一来源，cli 不许抄一份 ─────────────

# (常量所在模块, 常量名, 该子命令的最小 argv, argparse dest, 测试用的探针值)
_HOST_DEFAULTS = [
    ("guanlan.web.defaults", "DEFAULT_PORT", ["web"], "port", 9911),
    ("guanlan.web.defaults", "DEFAULT_MAX_CONVERSATIONS", ["web"], "max_conversations", 77),
    ("guanlan.web.defaults", "DEFAULT_CONFIRM_TIMEOUT", ["web"], "confirm_timeout", 4321.0),
    # 探针值**不能与既有文案撞车**：拿 "workspace-write" / "auto" 当探针的话，它们本就出现在
    # 邻近 help 里，那条 help 断言会永远绿（假绿）。下面的 `not in baseline_help` 守着这一点。
    # argparse 不校验 default 是否属于 choices，故探针可以是个不存在的取值。
    ("guanlan.web.defaults", "DEFAULT_MODE", ["web"], "mode", "zz-mode-probe"),
    ("guanlan.web.defaults", "DEFAULT_CONFIRM", ["web"], "confirm", "zz-confirm-probe"),
    ("guanlan.im.defaults", "DEFAULT_MAX_CONVERSATIONS", ["im", "--platform", "weixin", "--allow-all-users"], "max_conversations", 77),
    ("guanlan.im.defaults", "DEFAULT_IDLE_TTL", ["im", "--platform", "weixin", "--allow-all-users"], "idle_ttl", 4321.0),
    ("guanlan.im.defaults", "DEFAULT_MCP_REQUEST_TIMEOUT", ["im", "--platform", "weixin", "--allow-all-users"], "mcp_request_timeout", 4321.0),
    ("guanlan.im.defaults", "DEFAULT_IDENTIFY_SECONDS", ["im-identify", "--platform", "weixin"], "seconds", 4321.0),
    ("guanlan.mcp.defaults", "DEFAULT_TRANSPORT", ["mcp"], "transport", "zz-transport-probe"),
    ("guanlan.mcp.defaults", "DEFAULT_HOST", ["mcp"], "host", "10.1.2.3"),
    ("guanlan.mcp.defaults", "DEFAULT_PORT", ["mcp"], "port", 9911),
]


def _rendered(value):
    """help 文案里该值的渲染形态（float 用 `:g`，与 cli 的 f-string 一致）。"""
    return f"{value:g}" if isinstance(value, float) else str(value)


def _squash(text: str) -> str:
    """去掉全部空白再比对。

    argparse 的 `textwrap` 会**在连字符处断行**（`break_on_hyphens`），中文 help 里列宽又窄，
    于是 `zz-confirm-probe` 会被劈成 `zz-confirm-` + 换行 + `probe`——直接 `in` 判断会假红。
    """
    return "".join(text.split())


@pytest.mark.parametrize(
    ("module_name", "const", "argv", "dest", "probe"),
    _HOST_DEFAULTS,
    ids=[f"{m.split('.')[1]}:{c}" for m, c, *_ in _HOST_DEFAULTS],
)
def test_host_cli_default_follows_the_constant(module_name, const, argv, dest, probe, monkeypatch, capsys):
    """把常量改掉，CLI 的默认值与 help 文案**必须跟着动**。

    这是本组唯一有意义的断言形式。只断言"默认值等于常量"是测不出东西的——两边各写一份
    `100` 时它照样绿。而 IM / Web 此前正是各写一份（连 help 里的中文「默认 100」是第三份），
    于是常量沦为装饰：调 `DEFAULT_IDLE_TTL` 对 CLI 用户毫无效果，docstring 和 `--help`
    从此说假话，**没有任何测试会红**。改一处即处处生效，才是常量存在的全部意义。

    顺带守住另一件事：这几个默认值必须能从**零依赖叶子模块**读到。若哪天有人把它们挪回
    `im/server.py` / `web/server.py`，本测试会因导入 agentao / fastapi 而变慢甚至在缺
    `[web]` extra 的环境里直接崩——那正是当初分出 `defaults.py` 的原因。
    """
    import importlib

    module = importlib.import_module(module_name)
    baseline = build_parser().parse_args(argv)
    assert getattr(baseline, dest) == getattr(module, const)

    with pytest.raises(SystemExit):
        build_parser().parse_args([*argv[:1], "--help"])
    baseline_help = capsys.readouterr().out
    # 探针若本就出现在文案里（如拿 "auto" 当探针），下面那条 help 断言会**永远绿**。
    assert _rendered(probe) not in _squash(baseline_help), "探针值与既有文案撞车，这条用例会假绿"

    monkeypatch.setattr(module, const, probe)
    assert getattr(build_parser().parse_args(argv), dest) == probe, "cli 抄了一份字面量"
    with pytest.raises(SystemExit):
        build_parser().parse_args([*argv[:1], "--help"])
    assert _rendered(probe) in _squash(capsys.readouterr().out), "help 文案没跟着常量走（第三份拷贝）"


def test_web_mode_choices_come_from_the_constant(monkeypatch):
    """`--mode` / `--confirm` 的**合法取值**同样取自常量，不在 cli 里再列一遍。

    两头各列一份的后果不是抽象的：cli 收下一个运行期不认的值，用户会拿到一个
    422 / ValueError 而不是「invalid choice」——错在哪一层都看不出来。
    """
    from guanlan.web import defaults

    monkeypatch.setattr(defaults, "WEB_MODES", ("read-only", "workspace-write", "demo-mode"))
    assert build_parser().parse_args(["web", "--mode", "demo-mode"]).mode == "demo-mode"
    monkeypatch.setattr(defaults, "CONFIRM_MODES", ("ask", "auto", "demo-confirm"))
    assert build_parser().parse_args(["web", "--confirm", "demo-confirm"]).confirm == "demo-confirm"


def test_mcp_transport_choices_come_from_the_constant(monkeypatch):
    """`--transport` 的合法取值同样取自 `TRANSPORTS`，不在 cli 里再列一遍。

    上面那条参数化用例只探**默认值**——而 argparse 根本不校验 default 是否属于 choices，
    所以把 `choices=TRANSPORTS` 抄回 `("stdio", "http")` 时它照样绿。少了本条，choices
    那份拷贝就是无人看守的。
    """
    from guanlan.mcp import defaults as mcp_defaults

    monkeypatch.setattr(mcp_defaults, "TRANSPORTS", ("stdio", "http", "demo-transport"))
    args = build_parser().parse_args(["mcp", "--transport", "demo-transport"])
    assert args.transport == "demo-transport"


def test_mcp_port_help_quotes_the_web_constant(monkeypatch, capsys):
    """`--port` 的 help 里那句「与 web 8765 错开」也得取 web 的常量，不能抄第四份。

    抄了的话，改 web 默认端口时这句话立刻开始说假话，而它恰恰是**解释这个默认值为什么是
    8766** 的唯一去处。
    """
    from guanlan.web import defaults as web_defaults

    monkeypatch.setattr(web_defaults, "DEFAULT_PORT", 9123)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["mcp", "--help"])
    assert "9123" in _squash(capsys.readouterr().out)


def test_mcp_defaults_module_is_a_dependency_free_leaf():
    """`guanlan mcp --help` 在**没装 `[mcp]` extra** 时必须仍然可用。

    包根 `guanlan/mcp/__init__.py` 为此改成了 PEP 562 惰性壳。若哪天有人把 `serve_mcp`
    的导入挪回模块顶层，建 parser 就会连带拉起官方 SDK，用户看到的是 traceback，
    而不是 `_cmd_mcp` 那句「请先 pip install 'guanlan-wiki[mcp]'」。
    """
    import subprocess
    import sys

    code = (
        "import sys;"
        "sys.modules['mcp'] = None;"  # `import mcp...` → ImportError
        "import guanlan.mcp.defaults as d;"
        "from guanlan.cli import build_parser;"
        "a = build_parser().parse_args(['mcp']);"
        "assert (a.transport, a.host, a.port) == (d.DEFAULT_TRANSPORT, d.DEFAULT_HOST, d.DEFAULT_PORT);"
        "assert 'guanlan.mcp.server' not in sys.modules"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_web_defaults_module_is_a_dependency_free_leaf():
    """`web/defaults.py` 必须能在**没装 `[web]` extra** 的环境里导入。

    它是 `guanlan web --help` 的前提：`_cmd_web` 那套「缺 extra 就优雅引导安装」的降级，
    发生在**解析之后**——parser 建不起来的话，用户看到的是 traceback，不是那句安装提示。
    """
    import subprocess
    import sys

    code = (
        "import sys;"
        "sys.modules['fastapi'] = None; sys.modules['uvicorn'] = None;"
        "import guanlan.web.defaults as d;"
        "from guanlan.cli import build_parser;"
        "assert build_parser().parse_args(['web']).port == d.DEFAULT_PORT;"
        "assert 'fastapi' not in [m for m in sys.modules if sys.modules[m] is not None]"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_p3_dispatch_end_to_end(tmp_path):
    """三命令经 main 真正分发到各 entrypoint：在 init 出的库上各自退 0。"""
    from guanlan.cli import main

    assert main(["-C", str(tmp_path), "init"]) == 0
    assert main(["-C", str(tmp_path), "health"]) == 0
    assert main(["-C", str(tmp_path), "lint"]) == 0
    assert main(["-C", str(tmp_path), "graph", "--json-only"]) == 0
    assert (tmp_path / "graph" / "graph.json").is_file()
