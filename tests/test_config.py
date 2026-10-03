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
    assert config.grouping_field == "workstream"


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


def test_the_fetch_sizes_are_modest_because_graphql_charges_for_them(tmp_path):
    """Points are charged for nodes requested, not requests made."""
    write(tmp_path, "pyproject.toml", BARE)
    config = load(tmp_path)
    assert (config.item_limit, config.field_limit) == (100, 30)
