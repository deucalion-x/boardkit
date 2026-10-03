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


def test_an_iteration_field_is_planned_like_any_other():
    """`gh project field-create` cannot make one; the API can, so boardkit does."""
    config = CONFIG.model_copy(update={
        "fields": (ProjectField(name="sprint", data_type="ITERATION"),)})
    assert [f.name for f in plan(config, ())] == ["status", "sprint"]


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


def test_a_field_is_created_under_a_readable_name_not_the_lookup_key():
    """Config names fields by the key `item-list` emits, which is lowercased."""
    assert ProjectField(name="status").display == "Status"
    assert ProjectField(name="target_date").display == "Target Date"
    assert ProjectField(name="area", title="Work area").display == "Work area"


def test_aligning_options_keeps_the_ids_of_those_that_already_exist():
    from boardkit.admin import align
    present = ProjectField(name="status", id="F_S", options=("Todo", "Done"),
                           option_ids={"Todo": "o1", "Done": "o2"})
    wanted = ProjectField(name="status", options=("Todo", "In Review", "Done"))
    assert align(wanted, present) == [
        {"name": "Todo", "id": "o1"},
        {"name": "In Review"},
        {"name": "Done", "id": "o2"}]


def test_an_option_config_does_not_know_about_is_kept_not_deleted():
    """Dropping an option unsets every item in it; create must not do that."""
    from boardkit.admin import align
    present = ProjectField(name="status", id="F_S", options=("Todo", "Archived"),
                           option_ids={"Todo": "o1", "Archived": "o9"})
    wanted = ProjectField(name="status", options=("Todo",))
    assert align(wanted, present) == [{"name": "Todo", "id": "o1"},
                                      {"name": "Archived", "id": "o9"}]


def test_options_that_already_match_send_no_mutation():
    from boardkit.admin import align
    same = ProjectField(name="status", id="F_S", options=("Todo", "Done"),
                        option_ids={"Todo": "o1", "Done": "o2"})
    assert align(ProjectField(name="status", options=("Todo", "Done")), same) is None


def test_every_option_carries_the_colour_the_mutation_demands():
    """updateProjectV2Field rejects an option without colour or description."""
    from boardkit.admin import rendered
    text = rendered([{"id": "o1", "name": "Todo", "color": "GREEN",
                      "description": "not started"},
                     {"name": "Inbox", "color": "GRAY", "description": ""}])
    assert text == ('{id: "o1", name: "Todo", color: GREEN, description: "not started"}, '
                    '{name: "Inbox", color: GRAY, description: ""}')


def test_an_iteration_field_carries_its_configuration_into_the_mutation():
    from boardkit.admin import creation_query
    from boardkit.config import Iteration
    field = ProjectField(name="sprint", data_type="ITERATION",
                         iteration=Iteration(start_date="2026-10-05", duration=7,
                                             iterations=4))
    query = creation_query("PVT_1", field)
    assert "dataType: ITERATION" in query
    assert 'iterationConfiguration: {startDate: "2026-10-05", duration: 7' in query
    assert query.count('title: "Sprint ') == 4


def test_a_multi_select_sends_its_own_option_key():
    from boardkit.admin import creation_query
    field = ProjectField(name="area", data_type="MULTI_SELECT",
                         options=("sync", "cli"))
    query = creation_query("PVT_1", field)
    assert "multiSelectOptions: [" in query and "singleSelectOptions" not in query
    assert 'name: "sync", color: GRAY, description: ""' in query


def test_a_select_with_no_options_is_refused_before_the_call():
    from boardkit.admin import creation_query
    with pytest.raises(BoardError, match="no options"):
        creation_query("PVT_1", ProjectField(name="area", data_type="MULTI_SELECT"))


def test_a_count_of_iterations_becomes_consecutive_dated_periods():
    from boardkit.admin import iterations_for
    from boardkit.config import Iteration
    field = ProjectField(name="sprint", data_type="ITERATION",
                         iteration=Iteration(start_date="2026-10-05", duration=7,
                                             iterations=3))
    text = iterations_for(field)
    assert 'startDate: "2026-10-05", duration: 7, title: "Sprint 1"' in text
    assert 'startDate: "2026-10-12", duration: 7, title: "Sprint 2"' in text
    assert 'startDate: "2026-10-19", duration: 7, title: "Sprint 3"' in text


def test_a_view_names_its_visible_fields_by_id():
    from boardkit.admin import make_view
    from boardkit.config import View
    sent = []

    import boardkit.admin as admin
    original = admin.run_json
    admin.run_json = lambda args: (sent.append(args[-1]), {
        "data": {"createProjectV2View": {"projectV2View": {"id": "V_1"}}}})[1]
    try:
        made = make_view("PVT_1", View(name="Board", layout="BOARD",
                                       fields=("status", "area")),
                         {"status": "F_S", "area": "F_A", "effort": "F_E"})
    finally:
        admin.run_json = original
    assert made == "V_1"
    assert "layout: BOARD_LAYOUT" in sent[0]
    assert 'visibleFieldIds: ["F_S", "F_A"]' in sent[0]   # not F_E
    assert 'name: "Board"' in sent[0]


def test_a_view_that_already_exists_is_left_alone(monkeypatch):
    from boardkit import admin
    from boardkit.config import View
    config = CONFIG.model_copy(update={"views": (View(name="Board"),)})
    monkeypatch.setattr(admin, "existing_views", lambda config, number: {"Board": "V_1"})
    monkeypatch.setattr(admin, "make_view",
                        lambda *a: (_ for _ in ()).throw(AssertionError("made again")))
    admin.apply_views(config, "5", "PVT_1", ())


def test_an_option_differing_only_in_case_is_renamed_not_duplicated():
    """GitHub seeds `In Progress`; a config asking for `In progress` was
    getting both, with the items split between two columns."""
    from boardkit.admin import align
    wanted = ProjectField(name="status", data_type="SINGLE_SELECT",
                          options=("Inbox", "Todo", "In progress", "Done"))
    present = ProjectField(name="status", data_type="SINGLE_SELECT",
                           options=("Todo", "In Progress", "Done"),
                           option_ids={"Todo": "a", "In Progress": "b", "Done": "c"})
    sent = align(wanted, present)
    assert [o["name"] for o in sent] == ["Inbox", "Todo", "In progress", "Done"]
    # The id travels with it, so the one card in that column stays put.
    assert next(o for o in sent if o["name"] == "In progress")["id"] == "b"


def test_an_option_the_board_has_and_config_does_not_is_kept():
    from boardkit.admin import align
    wanted = ProjectField(name="status", data_type="SINGLE_SELECT",
                          options=("Todo", "Done"))
    present = ProjectField(name="status", data_type="SINGLE_SELECT",
                           options=("Todo", "Blocked", "Done"),
                           option_ids={"Todo": "a", "Blocked": "b", "Done": "c"})
    assert [o["name"] for o in align(wanted, present)] == ["Todo", "Done", "Blocked"]


def test_a_board_already_carrying_both_spellings_keeps_both():
    """Dropping one would unset every item sitting in it — the thing this
    verb promises never to do."""
    from boardkit.admin import align, duplicates
    wanted = ProjectField(name="status", data_type="SINGLE_SELECT",
                          options=("Todo", "In progress", "Done"))
    present = ProjectField(name="status", data_type="SINGLE_SELECT",
                           options=("Todo", "In progress", "Done", "In Progress"),
                           option_ids={"Todo": "a", "In progress": "mine",
                                       "Done": "c", "In Progress": "githubs"})
    sent = align(wanted, present)
    assert {o.get("id") for o in sent} == {"a", "mine", "c", "githubs"}
    # The one config names keeps the id that has the items in it.
    assert next(o for o in sent if o["name"] == "In progress")["id"] == "mine"
    assert duplicates(present) == [("In progress", "In Progress")]


def test_options_that_agree_are_not_reported_as_duplicates():
    from boardkit.admin import duplicates
    assert duplicates(ProjectField(name="status", data_type="SINGLE_SELECT",
                                   options=("Todo", "Done"))) == []
