import pathlib

import pytest

from boardkit.config import Config
from boardkit.gh import BoardError
from boardkit.skill import install, render


def config(tmp_path: pathlib.Path, **overrides) -> Config:
    base = dict(root=tmp_path, owner="octo", project="4", repo="octo/places",
                fields=("workstream", "effort"), skill_name="places-github")
    return Config(**(base | overrides))


def test_every_placeholder_is_filled(tmp_path):
    text = render(config(tmp_path))
    assert "$" not in text
    assert "name: places-github" in text
    assert "https://github.com/users/octo/projects/4" in text
    assert "parent-issue:octo/places#18" in text


def test_the_frontmatter_stays_first_so_the_skill_still_loads(tmp_path):
    assert render(config(tmp_path)).startswith("---\nname: places-github")


def test_field_names_come_from_the_config(tmp_path):
    assert "Status, workstream and effort are Project fields" in render(config(tmp_path))
    one = render(config(tmp_path, fields=("team",)))
    assert "Status and team are Project fields" in one


def test_project_notes_are_appended_verbatim(tmp_path):
    (tmp_path / "notes.md").write_text("## Project notes\n\nExclude #30-#43.\n", encoding="utf-8")
    assert "Exclude #30-#43." in render(config(tmp_path, skill_notes="notes.md"))


def test_missing_notes_are_an_error_rather_than_a_silent_omission(tmp_path):
    with pytest.raises(BoardError, match="does not exist"):
        render(config(tmp_path, skill_notes="gone.md"))


def test_install_writes_the_skill_and_check_reports_drift(tmp_path):
    settings = config(tmp_path)
    assert install(settings) == 0
    target = tmp_path / ".claude" / "skills" / "places-github" / "SKILL.md"
    assert target.exists()
    assert install(settings, check=True) == 0
    target.write_text("hand edited\n", encoding="utf-8")
    assert install(settings, check=True) == 1
