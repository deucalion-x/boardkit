import pathlib

from boardkit.config import Config
from boardkit.skill import SKILLS, install, render


def config(tmp_path: pathlib.Path, **overrides) -> Config:
    base = dict(root=tmp_path, owner="octo", project="4", repo="octo/places")
    return Config(**(base | overrides))


def test_the_text_is_the_same_in_every_repo():
    """Rendered per board, two repos open at once carried two different skills
    under one name."""
    assert "$" not in render("board")
    assert "$" not in render("board-admin")


def test_the_frontmatter_stays_first_so_the_skill_still_loads():
    assert render("board").startswith("---\nname: board\n")
    assert render("board-admin").startswith("---\nname: board-admin\n")


def flat(text: str) -> str:
    """The template hard-wraps, so compare sentences without their line breaks."""
    return " ".join(text.split())


def test_the_skill_tells_an_agent_reads_are_free():
    text = flat(render("board"))
    assert "Reads are free" in text
    assert "filtering server-side" not in text


def test_the_skill_covers_changing_work_and_the_admin_one_the_board():
    work, admin = render("board"), render("board-admin")
    for verb in ("board set", "board add", "board rejected", "board sync"):
        assert verb in work
    assert "board admin apply" in admin and "[board]" in admin
    # Shape and contents stay in their own skill.
    assert "board admin apply" not in work


def test_the_skill_explains_how_a_draft_is_named():
    assert "~a1b2c3" in render("board")


def test_install_writes_both_and_check_reports_drift(tmp_path):
    settings = config(tmp_path)
    assert install(settings) == 0
    written = [tmp_path / ".claude/skills" / name / "SKILL.md" for name in SKILLS]
    assert all(path.exists() for path in written)
    assert install(settings, check=True) == 0
    written[0].write_text("hand edited\n", encoding="utf-8")
    assert install(settings, check=True) == 1


def test_a_skill_left_by_an_earlier_name_is_reported(tmp_path, capsys):
    """Skills were once named per repo; one left from then still loads."""
    old = tmp_path / ".claude/skills" / "places-github" / "SKILL.md"
    old.parent.mkdir(parents=True)
    old.write_text(render("board"), encoding="utf-8")
    install(config(tmp_path))
    assert "places-github/SKILL.md is left over" in capsys.readouterr().out
    assert install(config(tmp_path), check=True) == 1


def test_a_hand_written_skill_beside_them_is_left_alone(tmp_path, capsys):
    mine = tmp_path / ".claude/skills" / "notes" / "SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("---\nname: notes\n---\n\nhand written\n", encoding="utf-8")
    install(config(tmp_path))
    assert "notes" not in capsys.readouterr().out
    assert mine.exists()
