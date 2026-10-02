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
    with pytest.raises(BoardError, match="missing `project`"):
        load(tmp_path)


def test_derived_names(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE)
    config = load(tmp_path)
    assert config.url == "https://github.com/users/octo/projects/4"
    assert config.name == "Places"
    assert config.grouping_field == "workstream"


def test_an_org_project_lives_under_a_different_path(tmp_path):
    write(tmp_path, "pyproject.toml", TABLE + 'owner_type = "org"\n')
    assert load(tmp_path).url == "https://github.com/orgs/octo/projects/4"
