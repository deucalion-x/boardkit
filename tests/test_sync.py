import pathlib

import pytest

from boardkit.board import ProjectItem
from boardkit.config import Config, ProjectField
from boardkit.gh import BoardError
from boardkit.store import Snapshot, Store, Write
from boardkit.sync import edit_args, key, parse_fields, plan

CONFIG = Config(root=pathlib.Path("/repo"), owner="octo", project="4",
                repo="octo/widgets", fields=("workstream", "due"),
                status_order=("Ready", "In progress", "Done"))

# What `gh project field-list --format json` reports: the name as configured,
# and for a single select its options with their ids.
FIELD_PAYLOAD = [
    {"id": "F_S", "name": "Status", "type": "ProjectV2SingleSelectField",
     "options": [{"id": "O_R", "name": "Ready"}, {"id": "O_P", "name": "In progress"}]},
    {"id": "F_W", "name": "Workstream", "type": "ProjectV2SingleSelectField",
     "options": [{"id": "O_M", "name": "Maps"}]},
    {"id": "F_D", "name": "Due", "dataType": "DATE"},
    {"id": "F_X", "name": "Undeclared", "dataType": "TEXT"},
]


def snapshot() -> Snapshot:
    return Snapshot(
        project_id="PVT_1", synced_at="2026-10-03T00:00:00+00:00",
        fields=parse_fields(FIELD_PAYLOAD, CONFIG),
        items=(ProjectItem(number=7, id="I_7", title="Corridor",
                           fields={"status": "Ready", "workstream": "Maps", "due": "-"}),))


def test_field_names_match_across_the_two_commands_that_spell_them_differently():
    assert key("Target date") == key("targetDate") == "targetdate"


def test_only_declared_fields_are_kept_and_under_the_name_config_uses():
    fields = parse_fields(FIELD_PAYLOAD, CONFIG)
    assert [field.name for field in fields] == ["status", "workstream", "due"]
    assert fields[0].option_ids == {"Ready": "O_R", "In progress": "O_P"}
    assert fields[2].data_type == "DATE"


def test_a_graphql_type_name_is_read_as_a_data_type():
    assert parse_fields(FIELD_PAYLOAD, CONFIG)[1].data_type == "SINGLE_SELECT"


def test_a_single_select_write_resolves_the_option_id():
    args = edit_args(Write(number=7, field="status", value="In progress", was="Ready"),
                     snapshot(), CONFIG)
    assert args[:2] == ["project", "item-edit"]
    assert "--single-select-option-id" in args and "O_P" in args
    assert "I_7" in args and "F_S" in args and "PVT_1" in args


def test_a_date_write_uses_the_date_flag():
    args = edit_args(Write(number=7, field="due", value="2026-11-01"), snapshot(), CONFIG)
    assert args[-2:] == ["--date", "2026-11-01"]


def test_a_value_that_is_not_an_option_is_refused_before_the_call():
    with pytest.raises(BoardError, match="not an option"):
        edit_args(Write(number=7, field="status", value="Sideways"), snapshot(), CONFIG)


def test_writing_an_item_absent_from_the_snapshot_asks_for_a_pull():
    with pytest.raises(BoardError, match="pull first"):
        edit_args(Write(number=99, field="status", value="Ready"), snapshot(), CONFIG)


def test_a_write_applies_when_the_remote_still_holds_what_it_was_decided_against():
    applies, lost = plan([Write(number=7, field="status", value="In progress",
                                was="Ready")], snapshot())
    assert len(applies) == 1 and lost == []


def test_a_write_loses_when_the_field_moved_on_github():
    applies, lost = plan([Write(number=7, field="status", value="In progress",
                                was="Done")], snapshot())
    assert applies == [] and lost[0][1] == "Ready"


def test_a_write_already_true_remotely_is_dropped_rather_than_pushed():
    applies, lost = plan([Write(number=7, field="status", value="Ready",
                                was="Ready")], snapshot())
    assert applies == [] and lost == []


def test_a_write_against_an_item_that_left_the_board_loses():
    applies, lost = plan([Write(number=99, field="status", value="Ready")], snapshot())
    assert applies == [] and "no longer on the board" in lost[0][1]


def test_rejected_writes_land_in_the_store_with_both_values(tmp_path):
    kept = Store(Config(root=tmp_path, owner="octo", project="4",
                        repo="octo/widgets", fields=("workstream",)))
    write = Write(number=7, field="status", value="In progress", was="Done")
    kept.reject(write, remote="Ready")
    assert kept.rejected()[0].remote == "Ready"
    assert kept.rejected()[0].write.value == "In progress"


PROJECTS = [{"number": 4, "title": "Places"}, {"number": 9, "title": "boardkit"}]


def test_the_project_number_is_looked_up_by_title_not_declared():
    from boardkit.sync import find_project
    config = CONFIG.model_copy(update={"project_name": "boardkit"})
    assert find_project(PROJECTS, config) == "9"


def test_a_title_that_matches_nothing_lists_what_is_there():
    from boardkit.sync import find_project
    config = CONFIG.model_copy(update={"project_name": "Ghost"})
    with pytest.raises(BoardError, match="Places, boardkit"):
        find_project(PROJECTS, config)


def test_an_ambiguous_title_asks_to_be_pinned_rather_than_guessing():
    from boardkit.sync import find_project
    config = CONFIG.model_copy(update={"project_name": "Twin"})
    twins = [{"number": 1, "title": "Twin"}, {"number": 2, "title": "Twin"}]
    with pytest.raises(BoardError, match="Set `project`"):
        find_project(twins, config)


def test_a_misspelled_column_is_reported_rather_than_silently_empty():
    from boardkit.sync import drift
    # The board spells it "In progress"; config got the capital wrong.
    config = CONFIG.model_copy(update={"status_order": ("Ready", "In Progress")})
    warnings = drift(snapshot(), config)
    assert any("'In Progress'" in w and "no column" in w for w in warnings)


def test_a_column_the_board_has_but_config_omits_is_reported():
    from boardkit.sync import drift
    config = CONFIG.model_copy(update={"status_order": ("Ready",)})
    assert any("'In progress'" in w and "sort last" in w for w in drift(snapshot(), config))


def test_matching_columns_warn_about_nothing():
    from boardkit.sync import drift
    config = CONFIG.model_copy(update={"status_order": ("Ready", "In progress")})
    aligned = snapshot()
    assert drift(aligned, config.model_copy(
        update={"status_order": tuple(aligned.field("status").options)})) == []
