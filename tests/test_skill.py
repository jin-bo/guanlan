"""P2 skill 可发现性测试：bundled 解析、按需装入全局、显式 install-skill（不打真实 LLM）。"""

from pathlib import Path

import pytest

from guanlan import skill


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把 ~/.agentao 重定向到 tmp（user_root() 经 Path.home 惰性解析，故 patch home 即可）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_bundled_skill_dir_resolves():
    src = skill.bundled_skill_dir()
    assert src.is_dir()
    assert (src / "SKILL.md").is_file()


def test_install_skill_copies_to_global(fake_home):
    dest = skill.install_skill()
    assert dest == fake_home / ".agentao" / "skills" / "guanlan-wiki"
    assert (dest / "SKILL.md").is_file()


def test_install_skill_idempotent_preserves_user_edits(fake_home):
    dest = skill.install_skill()
    (dest / "SKILL.md").write_text("USER EDIT", encoding="utf-8")
    again = skill.install_skill()  # 非 force：保留
    assert again == dest
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "USER EDIT"


def test_install_skill_force_reinstalls(fake_home):
    dest = skill.install_skill()
    (dest / "SKILL.md").write_text("USER EDIT", encoding="utf-8")
    skill.install_skill(force=True)  # 覆盖
    assert (dest / "SKILL.md").read_text(encoding="utf-8") != "USER EDIT"


def test_ensure_skips_when_repo_skill_present(fake_home, tmp_path):
    """working_directory 下已有完整 skills/guanlan-wiki（开发期）→ 不写全局。"""
    wd = tmp_path / "wd"
    repo_skill = wd / "skills" / "guanlan-wiki"
    repo_skill.mkdir(parents=True)
    (repo_skill / "SKILL.md").write_text("---\nname: guanlan-wiki\n---\n", encoding="utf-8")
    assert skill.ensure_skill_available(wd) is None
    assert not skill.global_skill_dir().exists()


def test_ensure_installs_when_not_discoverable(fake_home, tmp_path):
    wd = tmp_path / "extkb"
    wd.mkdir()
    dest = skill.ensure_skill_available(wd)
    assert dest == skill.global_skill_dir()
    assert (dest / "SKILL.md").is_file()
    # 再调一次：已可发现 → no-op。
    assert skill.ensure_skill_available(wd) is None


def test_ensure_skips_when_global_present(fake_home, tmp_path):
    skill.install_skill()  # 先装全局
    wd = tmp_path / "extkb2"
    wd.mkdir()
    assert skill.ensure_skill_available(wd) is None


def test_partial_stub_not_discoverable_and_gets_repaired(fake_home, tmp_path):
    """缺 SKILL.md 的半成品目录不算已装；ensure/install 应修复它。"""
    wd = tmp_path / "extkb3"
    stub = wd / "skills" / "guanlan-wiki"
    stub.mkdir(parents=True)  # 空桩，无 SKILL.md
    assert skill.is_discoverable(wd) is False

    # 全局也建个半成品桩，install 非 force 也应修复（而非原样返回）。
    gstub = skill.global_skill_dir()
    gstub.mkdir(parents=True)
    dest = skill.install_skill()
    assert (dest / "SKILL.md").is_file()
