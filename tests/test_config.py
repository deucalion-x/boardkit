import pathlib

import pytest

from boardkit.config import load
from boardkit.gh import BoardError

TABLE = """
[tool.boardkit]
owner = "octo"
project = 4
repo = "octo/places"
fields = ["workstream", "effort"]
"""


# The table form of `fields` cannot coexist with the flat list in one file.
BARE = """
[tool.boardkit]
owner = "octo"
project = 4
repo = "octo/places"
"""


def write(tmp_path: pathlib.Path, name: str, text: str) -> pathlib.Path:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_reads_the_table_from_pyproject(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE)
    config = load(tmp_path)
    assert (config.owner, config.repo) == ("octo", "octo/places")
    # A project number written as an integer still addresses `gh` as a string.
    assert config.project == "4"


def test_found_by_walking_up_from_a_subdirectory(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE)
    nested = tmp_path / "apps" / "api"
    nested.mkdir(parents=True)
    assert load(nested).root == tmp_path


def test_standalone_file_for_a_repo_without_python_packaging(tmp_path):
    write(tmp_path, "boardkit.toml", 'owner = "octo"\nproject = "4"\nrepo = "octo/places"\n')
    assert load(tmp_path).owner == "octo"


def test_a_pyproject_without_the_table_is_not_a_match(tmp_path):
    write(tmp_path, "pyproject.toml", '[project]\nname = "unrelated"\n')
    with pytest.raises(BoardError, match="no boardkit configuration"):
        load(tmp_path)


def test_a_misspelled_setting_is_reported_rather_than_ignored(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE + 'mirror = "docs/mirror.md"\n')
    with pytest.raises(BoardError, match="unknown boardkit setting"):
        load(tmp_path)


def test_a_missing_required_setting_names_itself(tmp_path):
    write(tmp_path, "pyproject.toml", '[tool.boardkit]\nowner = "octo"\n')
    with pytest.raises(BoardError, match="missing `repo`"):
        load(tmp_path)


def test_a_repo_without_a_board_yet_still_loads(tmp_path):
    """`admin create` produces the number, so it cannot be required before it."""
    write(tmp_path, "pyproject.toml",
          '[tool.boardkit]\nowner = "octo"\nrepo = "octo/widgets"\n')
    config = load(tmp_path)
    assert config.project == ""
    with pytest.raises(BoardError, match="project is not set"):
        config.require("project")


def test_derived_names(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE)
    config = load(tmp_path)
    assert config.url == "https://github.com/users/octo/projects/4"
    assert config.name == "Places"


def test_an_org_project_lives_under_a_different_path(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE + 'owner_type = "org"\n')
    assert load(tmp_path).url == "https://github.com/orgs/octo/projects/4"


def test_a_flat_field_list_still_loads_and_defaults_to_single_select(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE)
    config = load(tmp_path)
    assert config.field_names == ("workstream", "effort")
    assert config.fields[0].data_type == "SINGLE_SELECT"
    assert config.fields[0].options == () and config.fields[0].id == ""


def test_a_field_table_carries_the_type_and_options_create_needs(tmp_path):
    write(tmp_path, "pyproject.toml", BARE + """
[tool.boardkit.fields.workstream]
options = ["Maps", "Infra"]

[tool.boardkit.fields.due]
data_type = "date"
""")
    config = load(tmp_path)
    assert config.field_names == ("workstream", "due")
    assert config.fields[0].options == ("Maps", "Infra")
    # Written lowercase in TOML, stored in GitHub's spelling.
    assert config.fields[1].data_type == "DATE"


def test_an_unusable_field_type_is_refused(tmp_path):
    write(tmp_path, "pyproject.toml", BARE + """
[tool.boardkit.fields.due]
data_type = "sundial"
""")
    with pytest.raises(BoardError, match="fields"):
        load(tmp_path)


def test_a_project_number_written_as_an_integer_is_accepted(tmp_path):
    write(tmp_path, "pyproject.toml", BARE)
    assert load(tmp_path).project == "4"


def test_a_repo_that_names_no_columns_takes_the_board_s_own(tmp_path):
    """Empty means "whatever the board says"; `shape` fills it from the snapshot."""
    write(tmp_path, "pyproject.toml", BARE)
    config = load(tmp_path)
    assert config.status_order == () and not config.declares


def test_a_dedicated_file_wins_over_a_table_in_pyproject(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE)
    write(tmp_path, "boardkit.toml",
          'owner = "dedicated"\nproject = "9"\nrepo = "dedicated/widgets"\n')
    assert load(tmp_path).owner == "dedicated"


def test_the_page_size_is_a_page_not_a_cap(tmp_path):
    """A pull pages until the board is exhausted; a truncated snapshot would
    make an absent item indistinguishable from an unfetched one."""
    write(tmp_path, "pyproject.toml", BARE)
    assert load(tmp_path).page_size == 100


def test_a_page_larger_than_github_allows_is_refused(tmp_path):
    """`first: 150` is rejected outright with EXCESSIVE_PAGINATION."""
    write(tmp_path, "pyproject.toml", BARE + "page_size = 150\n")
    with pytest.raises(BoardError, match="page_size"):
        load(tmp_path)


ADMIN = """
owner = "octo"
repo = "octo/places"
project_name = "Places"

[board]
status_order = ["Inbox", "Todo", "Done"]

[board.fields.workstream]
data_type = "SINGLE_SELECT"
options = ["Maps", "Infra"]

[[board.views]]
name = "Open work"
layout = "BOARD"
"""


def test_an_admin_table_groups_what_only_admin_apply_needs(tmp_path):
    """Grouping is for the reader: the values mean exactly what they mean at
    the top level."""
    write(tmp_path, "boardkit.toml", ADMIN)
    config = load(tmp_path)
    assert config.field_names == ("workstream",)
    assert config.status_order == ("Inbox", "Todo", "Done")
    assert [view.name for view in config.views] == ["Open work"]


def test_the_flat_form_still_works(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE)
    assert load(tmp_path).field_names == ("workstream", "effort")


def test_a_setting_that_is_not_the_boards_is_refused_there(tmp_path):
    write(tmp_path, "boardkit.toml", 'owner = "octo"\nrepo = "o/p"\n\n[board]\nowner = "someone"\n')
    with pytest.raises(BoardError, match="cannot go under \\[board\\]"):
        load(tmp_path)


def test_declaring_a_setting_in_both_places_is_refused(tmp_path):
    write(tmp_path, "boardkit.toml",
          'owner = "octo"\nrepo = "o/p"\nstatus_order = ["Todo"]\n\n[board]\nstatus_order = ["Done"]\n')
    with pytest.raises(BoardError, match="declared twice"):
        load(tmp_path)


def test_an_error_names_the_file_that_was_actually_read(tmp_path):
    """Three files can hold this configuration; naming the wrong one sends
    someone to edit a file nothing reads."""
    write(tmp_path, "boardkit.toml", 'owner = "octo"\nrepo = "o/p"\nnonsense = 1\n')
    with pytest.raises(BoardError, match="boardkit.toml") as caught:
        load(tmp_path)
    assert "pyproject.toml" not in str(caught.value)


def test_require_points_at_that_same_file(tmp_path):
    write(tmp_path, "boardkit.toml", 'owner = "octo"\nrepo = "o/p"\n')
    config = load(tmp_path)
    with pytest.raises(BoardError, match="boardkit.toml"):
        config.require("project")


def test_the_internal_source_field_is_not_offered_as_a_setting(tmp_path):
    write(tmp_path, "boardkit.toml", 'owner = "octo"\nrepo = "o/p"\nnonsense = 1\n')
    with pytest.raises(BoardError) as caught:
        load(tmp_path)
    assert "source" not in str(caught.value).split("Known settings:")[1]


def test_the_skill_name_is_the_same_in_every_repo(tmp_path):
    write(tmp_path, "boardkit.toml", 'owner = "octo"\nrepo = "octo/places"\n')
    assert load(tmp_path).skill_name == "board"
