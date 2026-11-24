"""P5.5.1 画像默认路径（`~/.guanlan/USER.md`）：CLI 只读 query 自动带上，见 docs/P5.5.1-画像默认路径.md。

`~` 由 conftest 的 autouse 夹具 `_isolated_home` 指向空临时目录；CLI 用例把 `cli.run_query` 换成注入了
fake runner 的同一个函数，`use_default_profile` 的取值仍由真实的 `_cmd_query` 决定。
"""

import functools
from pathlib import Path

import pytest

from conftest import make_runner

import guanlan.cli as cli
import guanlan.query as query
from guanlan.errors import EXIT_AGENT_ERROR, EXIT_OK, EXIT_USAGE
from guanlan.query import PROFILE_MAX_CHARS, build_profile_instructions, run_query


@pytest.fixture
def home(_isolated_home: Path) -> Path:
    return _isolated_home


def _write_default(home: Path, text: str = "## 回答方式\n先给结论。") -> Path:
    p = home / ".guanlan" / "USER.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _notice_file(home: Path) -> Path:
    return home / ".guanlan" / "profile-notice"


@pytest.fixture
def tty(monkeypatch):
    """把 stderr 当作终端（capsys 捕获时 isatty 为 False，且 readouterr 会换掉 stderr 对象）。"""
    monkeypatch.setattr(query, "_stderr_is_tty", lambda: True)


def _cli(kb: Path, monkeypatch, *args: str, runner=None):
    runner = runner or make_runner(None, final_text="ans")
    monkeypatch.setattr(cli, "run_query", functools.partial(run_query, runner=runner))
    rc = cli.main(["-C", str(kb), "query", "q", *args])
    return rc, runner


# ───────── 测试隔离本身（§5 首条）─────────


def test_home_is_isolated_by_conftest(home: Path):
    """夹具失效时这条先红：否则旧用例会读开发机上的真实画像，而 CI（没有画像）照样全绿。"""
    assert Path.home() == home
    assert not (Path.home() / ".guanlan").exists()


# ───────── D1 来源 ─────────


def test_no_default_file_behaves_like_p55(kb: Path, home: Path, monkeypatch, capsys):
    rc, runner = _cli(kb, monkeypatch)
    assert rc == EXIT_OK
    assert "instructions" not in runner.calls[0]
    assert "画像" not in capsys.readouterr().err
    assert not _notice_file(home).exists()


def test_default_file_injected_same_as_explicit(kb: Path, home: Path, monkeypatch, capsys):
    p = _write_default(home)
    _, by_default = _cli(kb, monkeypatch)
    _, explicit = _cli(kb, monkeypatch, "--profile", str(p))
    assert by_default.calls[0]["instructions"] == explicit.calls[0]["instructions"]
    assert by_default.calls[0]["prompt"] == explicit.calls[0]["prompt"]
    assert capsys.readouterr().out == "ans\nans\n"  # stdout 只有答案


def test_no_profile_flag_skips_default(kb: Path, home: Path, monkeypatch, capsys, tty):
    _write_default(home)
    rc, runner = _cli(kb, monkeypatch, "--no-profile")
    assert rc == EXIT_OK and "instructions" not in runner.calls[0]
    assert "画像" not in capsys.readouterr().err
    assert not _notice_file(home).exists()


def test_explicit_profile_wins_and_never_notices(kb: Path, home: Path, tmp_path_factory, monkeypatch, capsys, tty):
    _write_default(home, "默认画像")
    other = tmp_path_factory.mktemp("other") / "p.md"
    other.write_text("另一份画像", encoding="utf-8")
    _, runner = _cli(kb, monkeypatch, "--profile", str(other))
    assert runner.calls[0]["instructions"].endswith("另一份画像")
    assert "已附" not in capsys.readouterr().err
    assert not _notice_file(home).exists()


def test_profile_and_no_profile_conflict(kb: Path, home: Path, monkeypatch, capsys):
    def fail(*a, **k):
        raise AssertionError("不应走到取锁或 run_query")

    monkeypatch.setattr(cli, "run_locked", fail)
    monkeypatch.setattr(cli, "run_query", fail)
    with pytest.raises(SystemExit) as exc:
        cli.main(["-C", str(kb), "query", "q", "--no-profile", "--profile", str(_write_default(home))])
    assert exc.value.code == 2
    assert "--no-profile" in capsys.readouterr().err


def test_home_unavailable_warns_and_skips(kb: Path, monkeypatch, capsys):
    def boom():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(boom))
    rc, runner = _cli(kb, monkeypatch)
    assert rc == EXIT_OK and "instructions" not in runner.calls[0]
    assert "找不到主目录" in capsys.readouterr().err


def test_default_profile_read_once(kb: Path, home: Path, monkeypatch, tty):
    """只读一次：校验、提示摘要与注入来自同一次读取（决策D2）。"""
    _write_default(home, "画像正文")
    calls = []
    real = query.load_profile
    monkeypatch.setattr(query, "load_profile", lambda *a, **k: calls.append(a) or real(*a, **k))
    _, runner = _cli(kb, monkeypatch)
    assert len(calls) == 1
    assert runner.calls[0]["instructions"] == build_profile_instructions(kb, "画像正文")


def test_not_a_kb_reports_only_kb_error(home: Path, tmp_path_factory, monkeypatch, capsys):
    _write_default(home)
    not_kb = tmp_path_factory.mktemp("not-kb")
    rc, runner = _cli(not_kb, monkeypatch)
    err = capsys.readouterr().err
    assert rc == EXIT_USAGE and runner.calls == []
    assert "画像" not in err


# ───────── D3 首次提示 ─────────


def test_notice_once_per_content(kb: Path, home: Path, monkeypatch, capsys, tty):
    p = _write_default(home, "甲")
    _cli(kb, monkeypatch)
    first = capsys.readouterr().err
    assert first.count("已附提问者画像") == 1 and "之后不再提示" in first
    assert "~" in first  # 主目录下显示成 ~/…
    assert _notice_file(home).exists()

    _cli(kb, monkeypatch)
    assert "已附" not in capsys.readouterr().err  # 同一内容不再提示

    p.write_text("\n甲\n\n", encoding="utf-8")  # 只改首尾空白：不算改动
    _cli(kb, monkeypatch)
    assert "已附" not in capsys.readouterr().err

    p.write_text("乙", encoding="utf-8")  # 改了内容：再提示一次
    _cli(kb, monkeypatch)
    assert "已附" in capsys.readouterr().err

    p.write_text("甲", encoding="utf-8")  # A→B→A：状态只存最近一份，改回也再提示
    _cli(kb, monkeypatch)
    assert "已附" in capsys.readouterr().err


def test_notice_not_recorded_when_stderr_not_tty(kb: Path, home: Path, monkeypatch, capsys):
    """重定向时没人看得到：每次照打、不记状态；之后到终端里仍会提示一次。"""
    _write_default(home)
    for _ in range(2):
        _cli(kb, monkeypatch)
        err = capsys.readouterr().err
        assert "已附提问者画像" in err and "之后不再提示" not in err
    assert not _notice_file(home).exists()

    monkeypatch.setattr(query, "_stderr_is_tty", lambda: True)
    _cli(kb, monkeypatch)
    assert "之后不再提示" in capsys.readouterr().err


def test_notice_state_unwritable_notices_every_time(kb: Path, home: Path, monkeypatch, capsys, tty):
    _write_default(home)
    _notice_file(home).mkdir()  # 状态路径是个目录：读不了也写不进
    for _ in range(2):
        rc, _ = _cli(kb, monkeypatch)
        assert rc == EXIT_OK
        err = capsys.readouterr().err
        assert "已附提问者画像" in err
        assert "之后不再提示" not in err  # 没记下就不许承诺不再提示


def test_notice_state_corrupt_notices_once_and_rewrites(kb: Path, home: Path, monkeypatch, capsys, tty):
    _write_default(home)
    _notice_file(home).write_bytes(b"\xff\xfe garbage")
    _cli(kb, monkeypatch)
    assert "已附" in capsys.readouterr().err
    _cli(kb, monkeypatch)
    assert "已附" not in capsys.readouterr().err


def test_notice_recorded_even_if_runner_fails(kb: Path, home: Path, monkeypatch, capsys, tty):
    _write_default(home)
    failing = make_runner(None, ok=False, error_type="runtime_error")
    rc, _ = _cli(kb, monkeypatch, runner=failing)
    assert rc == EXIT_AGENT_ERROR
    assert "已附" in capsys.readouterr().err
    _cli(kb, monkeypatch)
    assert "已附" not in capsys.readouterr().err


# ───────── D4 默认文件不合格：警告后不带画像继续 ─────────


def _bad_empty(home):
    _write_default(home, "  \n ")


def _bad_too_long(home):
    _write_default(home, "字" * (PROFILE_MAX_CHARS + 1))


def _bad_not_utf8(home):
    p = _write_default(home)
    p.write_bytes("画像".encode("gbk"))


def _bad_directory(home):
    (home / ".guanlan" / "USER.md").mkdir(parents=True)


def _bad_dangling_symlink(home):
    (home / ".guanlan").mkdir()
    (home / ".guanlan" / "USER.md").symlink_to(home / "nowhere.md")


@pytest.mark.parametrize(
    "make_bad, needle",
    [
        (_bad_empty, "空的"),
        (_bad_too_long, "超过上限"),
        (_bad_not_utf8, "UTF-8"),
        (_bad_directory, "不是普通文件"),
        (_bad_dangling_symlink, "不存在"),
    ],
)
def test_bad_default_warns_and_continues(kb: Path, home: Path, monkeypatch, capsys, tty, make_bad, needle):
    make_bad(home)
    for _ in range(2):  # 每次都警告，不受「只提示一次」影响
        rc, runner = _cli(kb, monkeypatch)
        assert rc == EXIT_OK  # 退出码由问答决定
        assert runner.calls and "instructions" not in runner.calls[0]
        err = capsys.readouterr().err
        assert "默认画像未使用" in err and needle in err
        assert str(Path("~") / ".guanlan" / "USER.md") in err  # 路径显示成 ~/…，不是绝对路径
    assert not _notice_file(home).exists()


def test_default_inside_kb_warns_and_continues(kb: Path, monkeypatch, capsys):
    """知识库根恰是主目录：默认画像落在库内，降级而不是报错。"""
    monkeypatch.setenv("HOME", str(kb))
    monkeypatch.setenv("USERPROFILE", str(kb))
    _write_default(kb)
    rc, runner = _cli(kb, monkeypatch)
    assert rc == EXIT_OK and "instructions" not in runner.calls[0]
    assert "知识库目录内" in capsys.readouterr().err


def test_default_symlink_into_kb_warns_and_continues(kb: Path, home: Path, monkeypatch, capsys):
    target = kb / "workspace" / "USER.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("画像", encoding="utf-8")
    (home / ".guanlan").mkdir()
    (home / ".guanlan" / "USER.md").symlink_to(target)
    rc, runner = _cli(kb, monkeypatch)
    assert rc == EXIT_OK and "instructions" not in runner.calls[0]
    assert "知识库目录内" in capsys.readouterr().err


def test_default_symlink_outside_kb_is_used(kb: Path, home: Path, tmp_path_factory, monkeypatch):
    target = tmp_path_factory.mktemp("sync") / "USER.md"
    target.write_text("同步盘里的画像", encoding="utf-8")
    (home / ".guanlan").mkdir()
    (home / ".guanlan" / "USER.md").symlink_to(target)
    _, runner = _cli(kb, monkeypatch)
    assert runner.calls[0]["instructions"].endswith("同步盘里的画像")


def test_explicit_bad_profile_still_usage_error(kb: Path, home: Path, monkeypatch, capsys):
    """同样不合格的文件经 --profile 显式给出：仍 EXIT_USAGE、不调 runner（P5.5 D4 不变）。"""
    p = _write_default(home, "  ")
    rc, runner = _cli(kb, monkeypatch, "--profile", str(p))
    assert rc == EXIT_USAGE and runner.calls == []


# ───────── D5 回填完全不看默认路径 ─────────


@pytest.mark.parametrize("text", ["合格画像", "  "])
def test_backfill_ignores_default(kb: Path, home: Path, monkeypatch, capsys, tty, text):
    _write_default(home, text)
    rc, runner = _cli(kb, monkeypatch, "--backfill")
    assert rc == EXIT_OK
    assert "instructions" not in runner.calls[0]
    assert "画像" not in capsys.readouterr().err
    assert not _notice_file(home).exists()


def test_no_profile_with_backfill_allowed(kb: Path, home: Path, monkeypatch):
    rc, runner = _cli(kb, monkeypatch, "--backfill", "--no-profile")
    assert rc == EXIT_OK and "instructions" not in runner.calls[0]


# ───────── D2 其它调用者不认默认路径 ─────────


def test_run_query_default_param_ignores_default_file(kb: Path, home: Path, capsys):
    """Web 回填作业等直接调 run_query 的路径：默认参数下不带画像、不提示。"""
    _write_default(home)
    for backfill in (False, True):
        runner = make_runner(None, final_text="ans")
        assert run_query("q", root=kb, backfill=backfill, runner=runner) == EXIT_OK
        assert "instructions" not in runner.calls[0]
    assert "画像" not in capsys.readouterr().err
