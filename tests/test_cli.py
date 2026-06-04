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
