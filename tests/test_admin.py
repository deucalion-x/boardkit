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
    assert [field.name for field in plan(CONFIG, present)] == ["effort", "blocked"]


def test_everything_is_missing_when_the_project_is_new():
    assert [f.name for f in plan(CONFIG, ())] == ["status", "workstream", "effort", "blocked"]


def test_an_iteration_field_is_planned_like_any_other():
    """`gh project field-create` cannot make one; the API can, so boardkit does."""
    config = CONFIG.model_copy(update={
        "fields": (ProjectField(name="sprint", data_type="ITERATION"),)})
    assert [f.name for f in plan(config, ())] == ["status", "sprint", "blocked"]


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


# Everything above is pure. Below is the half that mutates a real board, so
# each of these stubs the GraphQL call and asserts on the document that would
# have been sent — the only place the never-delete promise is actually kept.
@pytest.fixture
def sent(monkeypatch):
    """Every GraphQL document `admin` would send, in order."""
    from boardkit import admin, auth

    queries: list[str] = []

    def run_json(args):
        queries.append(args[-1])
        return {"data": {"user": {"projectV2": {"views": {"nodes": []},
                                                "field": None}},
                         "createProjectV2View": {"projectV2View": {"id": "V_NEW"}}}}

    monkeypatch.setattr(admin, "run_json", run_json)
    monkeypatch.setattr(auth, "require", lambda config, tier: None)
    return queries


def test_a_new_option_is_sent_without_an_id_and_an_existing_one_with_it():
    """`rendered` emits `id:` only when the option carries one.

    Why that matters to GitHub is not recorded anywhere here and is not
    asserted: this pins the shape the code produces, not a claim about the
    API's behaviour on a malformed list.
    """
    from boardkit.admin import rendered
    out = rendered([{"id": "O_1", "name": "Todo", "color": "GRAY", "description": ""},
                    {"name": "New", "color": "GRAY", "description": ""}])
    assert 'id: "O_1", name: "Todo"' in out
    assert 'id:' not in out.split("}, {")[1]


def test_every_option_keeps_the_colour_the_board_already_gave_it(sent, monkeypatch):
    """The mutation carries the whole list, so a colour not read back first
    would be repainted to the default on every apply."""
    from boardkit import admin
    monkeypatch.setattr(admin, "run_json", lambda args: {"data": {"user": {
        "projectV2": {"field": {"options": [
            {"id": "O_1", "name": "Todo", "color": "BLUE", "description": "now"}]}}}}})
    full = admin.decorated(CONFIG, "4", ProjectField(name="status", id="F_S"),
                           [{"id": "O_1", "name": "Todo"}])
    assert full[0]["color"] == "BLUE" and full[0]["description"] == "now"


def test_a_description_config_declares_wins_over_the_one_on_the_board(monkeypatch):
    """That is the point of declaring it."""
    from boardkit import admin
    monkeypatch.setattr(admin, "run_json", lambda args: {"data": {"user": {
        "projectV2": {"field": {"options": [
            {"id": "O_1", "name": "Todo", "color": "BLUE", "description": "stale"}]}}}}})
    field = ProjectField(name="status", id="F_S",
                         descriptions={"Todo": "refined enough to pick up now"})
    full = admin.decorated(CONFIG, "4", field, [{"id": "O_1", "name": "Todo"}])
    assert full[0]["description"] == "refined enough to pick up now"


def test_an_option_with_no_colour_anywhere_still_gets_one(monkeypatch):
    """`updateProjectV2Field` requires a colour on every entry."""
    from boardkit import admin
    monkeypatch.setattr(admin, "run_json",
                        lambda args: {"data": {"user": {"projectV2": {"field": None}}}})
    full = admin.decorated(CONFIG, "4", ProjectField(name="status", id="F_S"),
                           [{"name": "Todo"}])
    assert full[0]["color"] == admin.DEFAULT_COLOR


def test_a_view_that_already_exists_is_never_recreated(sent, monkeypatch):
    """Matched by name and never deleted: a tab someone made in the browser
    is not boardkit's to touch."""
    from boardkit import admin
    from boardkit.config import View
    monkeypatch.setattr(admin, "existing_views", lambda config, number: {"Open": "V_1"})
    config = CONFIG.model_copy(update={"views": (View(name="Open"),)})
    admin.apply_views(config, "4", "PVT_1", ())
    assert not any("createProjectV2View" in q for q in sent)


def test_a_declared_filter_is_a_second_mutation_after_the_view_is_made(sent):
    """`createProjectV2View` does not take one."""
    from boardkit import admin
    from boardkit.config import View
    config = CONFIG.model_copy(update={
        "views": (View(name="Open", filter="-status:Done"),)})
    admin.apply_views(config, "4", "PVT_1", ())
    made = [q for q in sent if "ProjectV2View" in q]
    assert "createProjectV2View" in made[0]
    assert "updateProjectV2View" in made[1] and "-status:Done" in made[1]


def test_a_view_with_no_filter_sends_only_the_one_mutation(sent):
    from boardkit import admin
    from boardkit.config import View
    config = CONFIG.model_copy(update={"views": (View(name="Open"),)})
    admin.apply_views(config, "4", "PVT_1", ())
    assert not any("updateProjectV2View" in q for q in sent)


def test_a_repo_declaring_no_views_asks_the_board_about_none(sent):
    from boardkit import admin
    admin.apply_views(CONFIG, "4", "PVT_1", ())
    assert sent == []


def test_a_dry_run_sends_no_mutation_at_all(sent, monkeypatch, capsys):
    """The whole point of `-n`: it is run against a board nobody wants
    changed yet."""
    from boardkit import admin
    monkeypatch.setattr(admin, "locate", lambda config: "4")
    monkeypatch.setattr(admin, "existing", lambda config, number: ("PVT_1", ()))
    admin.apply(CONFIG, dry=True)
    assert not any("mutation" in q for q in sent)
    assert "would add" in capsys.readouterr().out


def test_linking_without_a_project_says_to_create_one_first(monkeypatch):
    from boardkit import admin, auth
    monkeypatch.setattr(auth, "require", lambda config, tier: None)
    monkeypatch.setattr(admin, "locate", lambda config: "")
    with pytest.raises(BoardError, match="no project to link"):
        admin.link(CONFIG)


def test_applying_is_gated_on_admin_rather_than_on_write(monkeypatch):
    """`apply` asks for the `admin` tier, not `write`.

    The tier is what the code states; the reason for the split is not
    recorded in this module, so this asserts only which one is asked for.
    """
    from boardkit import admin, auth
    asked = []
    monkeypatch.setattr(auth, "require",
                        lambda config, tier: asked.append(tier))
    monkeypatch.setattr(admin, "locate", lambda config: "4")
    monkeypatch.setattr(admin, "existing", lambda config, number: ("PVT_1", ()))
    monkeypatch.setattr(admin, "existing_views", lambda config, number: {})
    admin.apply(CONFIG, dry=True)
    assert asked == ["admin"]
