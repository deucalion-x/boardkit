import pathlib

import pytest

from boardkit.admin import plan, wanted
from boardkit.config import Config, ProjectField
from boardkit.gh import BoardError

CONFIG = Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets",
                status_order=("Ready", "Done"), fields=("workstream", "effort"))


def test_the_status_field_is_built_from_the_configured_columns():
    status = wanted(CONFIG)[0]
    assert status.name == "status" and status.options == ("Ready", "Done")
    assert status.data_type == "SINGLE_SELECT"


def test_a_status_field_declared_explicitly_wins_over_the_columns():
    config = CONFIG.model_copy(update={
        "fields": (ProjectField(name="status", options=("A", "B")),
                   ProjectField(name="effort"))})
    assert wanted(config)[0].options == ("A", "B")


def test_fields_that_already_exist_are_left_alone():
    present = (ProjectField(name="status", id="F_S"),
               ProjectField(name="workstream", id="F_W"))
    assert [field.name for field in plan(CONFIG, present)] == ["effort"]


def test_everything_is_missing_when_the_project_is_new():
    assert [f.name for f in plan(CONFIG, ())] == ["status", "workstream", "effort"]


def test_a_type_gh_cannot_create_is_refused_before_anything_is_made():
    config = CONFIG.model_copy(update={
        "fields": (ProjectField(name="sprint", data_type="ITERATION"),)})
    with pytest.raises(BoardError, match="field-create"):
        plan(config, ())


def test_a_second_create_finds_the_first_rather_than_making_another(monkeypatch):
    """Idempotence without a number pasted back into config."""
    from boardkit import admin
    monkeypatch.setattr(admin, "gh_json", lambda *args: {
        "projects": [{"number": 9, "title": "Widgets"}]})
    assert admin.locate(CONFIG.model_copy(update={"project_name": "Widgets"})) == "9"


def test_no_matching_project_reads_as_not_yet_created(monkeypatch):
    from boardkit import admin
    monkeypatch.setattr(admin, "gh_json", lambda *args: {"projects": []})
    assert admin.locate(CONFIG.model_copy(update={"project_name": "Widgets"})) == ""
