import pathlib

import pytest

from boardkit.config import Config
from boardkit.gh import BoardError
from boardkit.config import ProjectField
from boardkit.skill import install, render
from boardkit.store import Snapshot


def config(tmp_path: pathlib.Path, **overrides) -> Config:
    base = dict(root=tmp_path, owner="octo", project="4", repo="octo/places",
                fields=("workstream", "effort"), skill_name="places-github")
    return Config(**(base | overrides))


def test_every_placeholder_is_filled(tmp_path):
    text = render(config(tmp_path))
    assert "$" not in text
    assert "name: places-github" in text
    assert "https://github.com/users/octo/projects/4" in text
    assert "https://github.com/octo/places/issues/1" in text


def test_the_frontmatter_stays_first_so_the_skill_still_loads(tmp_path):
    assert render(config(tmp_path)).startswith("---\nname: places-github")


def flat(text: str) -> str:
    """The template hard-wraps, so compare sentences without their line breaks."""
    return " ".join(text.split())


def test_field_names_come_from_the_config(tmp_path):
    assert "Status, workstream and effort are Project fields" in flat(render(config(tmp_path)))
    one = render(config(tmp_path, fields=("team",)))
    assert "Status and team are Project fields" in flat(one)


def test_the_rollup_label_follows_the_grouping_field(tmp_path):
    assert "| `board` | Workstream-by-status rollup." in render(config(tmp_path))
    assert "| `board` | Team-by-status rollup." in render(config(tmp_path, fields=("team",)))


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


def test_the_skill_tells_an_agent_reads_are_free(tmp_path):
    """The old text told it to filter server-side, which is now the wrong
    instruction: it would cost a round trip per question."""
    text = flat(render(config(tmp_path)))
    assert "Reads are free" in text
    assert "answers from a snapshot on disk" in text
    assert "filtering server-side" not in text


def test_the_skill_covers_changing_the_board_not_only_reading_it(tmp_path):
    text = render(config(tmp_path))
    for verb in ("board set", "board add", "board rejected", "board sync"):
        assert verb in text


def test_the_skill_explains_how_a_draft_is_named(tmp_path):
    assert "~a1b2c3" in render(config(tmp_path))


def board(**overrides) -> Snapshot:
    """A board as a sync found it: nothing of this is declared in config."""
    base = dict(project_number="5",
                fields=(ProjectField(name="status", options=("Inbox", "Todo", "Done")),
                        ProjectField(name="area"), ProjectField(name="effort")))
    return Snapshot(**(base | overrides))


def test_a_board_that_declares_nothing_still_names_its_columns(tmp_path):
    """boardkit's own config declares no fields and no statuses — they are
    discovered on sync — so rendering from config alone left the sentences
    empty."""
    bare = config(tmp_path, fields=(), project="")
    text = flat(render(bare, board()))
    assert "`Inbox`, `Todo`, `Done`" in text
    assert "Status, area and effort are Project fields" in text
    assert "https://github.com/users/octo/projects/5" in text


def test_declared_fields_win_over_discovered_ones(tmp_path):
    text = flat(render(config(tmp_path), board()))
    assert "Status, workstream, effort and area are Project fields" in text


def test_before_the_first_sync_it_claims_no_particular_columns(tmp_path):
    text = flat(render(config(tmp_path, fields=()), Snapshot()))
    assert "run `board` to see the ones this board carries" in text
    assert "every other field the board carries" in text


def test_a_skill_left_by_an_earlier_name_is_reported(tmp_path, capsys):
    """Renaming writes the new directory and leaves the old one loading, with
    a description close enough to the new one to be picked instead."""
    install(config(tmp_path, skill_name="old-board"))
    install(config(tmp_path, skill_name="new-board"))
    assert "old-board/SKILL.md is left over" in capsys.readouterr().out
    assert install(config(tmp_path, skill_name="new-board"), check=True) == 1


def test_a_hand_written_skill_beside_it_is_left_alone(tmp_path, capsys):
    """Only what `render` banners is ours; anything else in that folder is
    someone's own work."""
    mine = tmp_path / ".claude/skills" / "notes" / "SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("---\nname: notes\n---\n\nhand written\n", encoding="utf-8")
    install(config(tmp_path, skill_name="new-board"))
    assert "notes" not in capsys.readouterr().out
    assert mine.exists()
