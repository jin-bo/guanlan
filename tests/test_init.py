"""P1 `guanlan init` 测试：在空目录生成预期模板、可重复运行、日期落地。"""

from pathlib import Path

from guanlan.init import run_init

EXPECTED = {
    "AGENTAO.md",
    "SCHEMA.md",
    "raw/.gitkeep",
    "wiki/index.md",
    "wiki/log.md",
    "wiki/overview.md",
}


def test_init_creates_minimal_template(tmp_path: Path):
    result = run_init(tmp_path, today="2026-06-03")

    assert set(result.created) == EXPECTED
    assert result.skipped == []
    for rel in EXPECTED:
        assert (tmp_path / rel).is_file()

    # 日期占位符已替换。
    log = (tmp_path / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "## [2026-06-03] init | 知识库初始化" in log
    assert "__DATE__" not in log

    overview = (tmp_path / "wiki" / "overview.md").read_text(encoding="utf-8")
    assert "last_updated: 2026-06-03" in overview
    assert "__DATE__" not in overview


def test_init_is_idempotent_and_non_destructive(tmp_path: Path):
    run_init(tmp_path, today="2026-06-03")
    (tmp_path / "SCHEMA.md").write_text("USER EDIT", encoding="utf-8")

    result = run_init(tmp_path, today="2026-06-04")

    assert result.created == []
    assert set(result.skipped) == EXPECTED
    # 用户改动未被覆盖。
    assert (tmp_path / "SCHEMA.md").read_text(encoding="utf-8") == "USER EDIT"


def test_init_seed_pages_carry_frontmatter(tmp_path: Path):
    run_init(tmp_path, today="2026-06-03")
    overview = (tmp_path / "wiki" / "overview.md").read_text(encoding="utf-8")
    assert overview.startswith("---\n")
    assert "type: synthesis" in overview
