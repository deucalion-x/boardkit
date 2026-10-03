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


def test_every_field_is_kept_whether_or_not_config_mentions_it():
    """Declaring a field orders it; it does not hide the others."""
    fields = parse_fields(FIELD_PAYLOAD, CONFIG)
    assert [field.name for field in fields] == ["status", "workstream", "due",
                                                "undeclared"]
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
    assert "changed on GitHub" in lost[0][2]


def test_a_write_already_true_remotely_is_dropped_rather_than_pushed():
    applies, lost = plan([Write(number=7, field="status", value="Ready",
                                was="Ready")], snapshot())
    assert applies == [] and lost == []


def test_a_write_against_an_item_that_left_the_board_loses():
    applies, lost = plan([Write(number=99, field="status", value="Ready")], snapshot())
    # The reason is a reason, not a value dressed up as one.
    assert applies == [] and lost[0][1] == ""
    assert "closed or no longer on the board" in lost[0][2]


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


MULTI = Snapshot(
    project_id="PVT_1", synced_at="2026-10-03T00:00:00+00:00",
    fields=(ProjectField(name="area", id="F_A", data_type="MULTI_SELECT",
                         options=("config", "docs", "sync"),
                         option_ids={"config": "o1", "docs": "o2", "sync": "o3"}),),
    items=(ProjectItem(number=8, id="I_8", fields={"area": "config"}),))


def test_a_multi_select_write_resolves_every_option_it_names():
    from boardkit.sync import chosen
    write = Write(number=8, field="area", value="config, docs")
    assert chosen(write, MULTI.field("area")) == ["o1", "o2"]


def test_a_multi_select_write_goes_through_its_own_mutation():
    from boardkit.sync import multi_select_query
    query = multi_select_query(Write(number=8, field="area", value="config, docs"), MULTI)
    assert 'multiSelectOptionIds: ["o1", "o2"]' in query
    assert 'itemId: "I_8"' in query and 'fieldId: "F_A"' in query


def test_one_bad_option_refuses_the_whole_multi_select_write():
    from boardkit.sync import chosen
    with pytest.raises(BoardError, match="not an option"):
        chosen(Write(number=8, field="area", value="config, nonsense"),
               MULTI.field("area"))


def test_a_repo_that_names_no_columns_is_not_warned_about_them():
    from boardkit.sync import drift
    assert drift(snapshot(), CONFIG.model_copy(update={"status_order": ()})) == []


def headers(remaining: int, in_seconds: int) -> str:
    """What a real GraphQL response carries. `gh api rate_limit` reports a
    different, lower number — reading that is what made a spent budget look
    like something else entirely."""
    from boardkit import gh as plumbing
    return (f"HTTP/2.0 200 OK\nX-Ratelimit-Limit: 5000\n"
            f"X-Ratelimit-Remaining: {remaining}\nX-Ratelimit-Resource: graphql\n"
            f"X-Ratelimit-Reset: {int(plumbing.time.time()) + in_seconds}\n\n{{}}")


def test_a_rate_limited_call_says_when_the_budget_returns(monkeypatch):
    import subprocess

    from boardkit import gh as plumbing

    def limited(args):
        if args[:3] == ["gh", "api", "graphql"]:
            return subprocess.CompletedProcess(args, 0, headers(16, 120), "")
        return subprocess.CompletedProcess(args, 1, "", "API rate limit exceeded")

    monkeypatch.setattr(plumbing, "once", limited)
    with pytest.raises(plumbing.RateLimited, match="16 of 5000 points left"):
        plumbing.run(["gh", "project", "view"])


def test_a_failure_that_is_not_about_rate_is_raised_as_itself(monkeypatch):
    import subprocess

    from boardkit import gh as plumbing
    calls = []

    def refused(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "could not resolve to a Project")

    monkeypatch.setattr(plumbing, "once", refused)
    with pytest.raises(BoardError, match="could not resolve"):
        plumbing.run(["gh", "project", "view"])
    assert len(calls) == 1


def test_what_was_written_is_folded_in_rather_than_pulled_again():
    from boardkit.sync import patched
    after = patched(snapshot(), [Write(number=7, field="status", value="In progress",
                                       was="Ready")])
    assert after.item(7).field("status") == "In progress"
    # Untouched fields survive the copy.
    assert after.item(7).field("workstream") == "Maps"


def test_folding_in_leaves_items_nobody_wrote_to_alone():
    from boardkit.sync import patched
    before = snapshot()
    after = patched(before, [Write(number=99, field="status", value="Done")])
    assert after.item(7) == before.item(7)


# What GraphQL returns, where `gh project field-list` reports a multi-select
# field as {"id": "", "name": "", ...} and makes it unusable.
GRAPHQL_FIELDS = [
    {"id": "F_S", "name": "Status", "dataType": "SINGLE_SELECT",
     "options": [{"id": "O_R", "name": "Ready"}]},
    {"id": "F_A", "name": "Area", "dataType": "MULTI_SELECT",
     "multiSelectOptions": [{"id": "O_C", "name": "config"},
                            {"id": "O_D", "name": "docs"}]},
]


def test_a_multi_select_field_survives_being_read():
    # Built rather than copied: model_copy skips the validator that turns a
    # name into a ProjectField.
    config = Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets",
                    fields=("area",))
    fields = parse_fields(GRAPHQL_FIELDS, config)
    area = next(f for f in fields if f.name == "area")
    assert area.data_type == "MULTI_SELECT" and area.id == "F_A"
    assert area.option_ids == {"config": "O_C", "docs": "O_D"}


def test_a_push_re_reads_only_the_items_it_targets(tmp_path, monkeypatch):
    """The board is a one-time cost; a push needs a handful of nodes."""
    from boardkit import sync
    from boardkit.store import Store

    config = Config(root=tmp_path, owner="octo", repo="octo/widgets")
    kept = Store(config)
    kept.save_state(Snapshot(
        project_id="PVT_1", synced_at="2026-10-03T00:00:00+00:00",
        fields=(ProjectField(name="status", id="F_S",
                             option_ids={"Ready": "O_R", "Done": "O_D"}),),
        items=(ProjectItem(number=7, id="I_7", fields={"status": "Ready"}),
               ProjectItem(number=8, id="I_8", fields={"status": "Ready"}))))
    asked = []

    def only_targets(config, ids):
        asked.append(ids)
        return [{"id": "I_7", "content": {"number": 7, "state": "OPEN"},
                 "status": "Done"}]

    monkeypatch.setattr(sync, "fetch_some", only_targets)
    fresh = sync.refreshed(config, kept, [Write(number=7, field="status", value="x")])
    assert asked == [["I_7"]]                      # not I_8
    assert fresh.item(7).field("status") == "Done"  # the re-read value
    assert fresh.item(8).field("status") == "Ready" # left as it was


def test_an_item_that_closed_since_the_pull_drops_out_for_rejection(tmp_path, monkeypatch):
    from boardkit import sync
    from boardkit.store import Store

    config = Config(root=tmp_path, owner="octo", repo="octo/widgets")
    kept = Store(config)
    kept.save_state(Snapshot(
        project_id="PVT_1", synced_at="2026-10-03T00:00:00+00:00",
        items=(ProjectItem(number=7, id="I_7", fields={"status": "Ready"}),)))
    monkeypatch.setattr(sync, "fetch_some", lambda config, ids: [])
    fresh = sync.refreshed(config, kept, [Write(number=7, field="status", value="x")])
    assert fresh.item(7) is None


def test_the_board_is_paged_until_exhausted(monkeypatch):
    """A snapshot missing its tail would make absent and unfetched identical."""
    from boardkit import sync

    pages = [
        {"data": {"user": {"projectV2": {
            "id": "PVT_1",
            "fields": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                       "nodes": [{"id": "F_S", "name": "Status",
                                  "dataType": "SINGLE_SELECT"}]},
            "items": {"pageInfo": {"hasNextPage": True, "endCursor": "CUR1"},
                      "nodes": [{"id": "I_1", "content": {"number": 1}}]}}}}},
        {"data": {"user": {"projectV2": {"items": {
            "pageInfo": {"hasNextPage": True, "endCursor": "CUR2"},
            "nodes": [{"id": "I_2", "content": {"number": 2}}]}}}}},
        {"data": {"user": {"projectV2": {"items": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"id": "I_3", "content": {"number": 3}}]}}}}},
    ]
    asked = []

    def paged(args):
        asked.append(args[-1])
        return pages[len(asked) - 1]

    monkeypatch.setattr(sync, "run_json", paged)
    board = sync.fetch_board(CONFIG, "4")
    assert [row["content"]["number"] for row in board["items"]] == [1, 2, 3]
    assert 'after: "CUR1"' in asked[1] and 'after: "CUR2"' in asked[2]
    # The fields come with the first page and are not re-fetched per page.
    assert board["fields"][0]["name"] == "Status"


def test_a_truncated_label_list_is_reported_not_passed_off_as_complete():
    from boardkit.sync import truncated
    rows = [{"content": {"number": 7,
                         "labels": {"pageInfo": {"hasNextPage": True},
                                    "nodes": [{"name": "a"}]}}},
            {"content": {"number": 8,
                         "labels": {"pageInfo": {"hasNextPage": False},
                                    "nodes": []}}}]
    assert truncated(rows) == ["#7 has more than 100 labels"]


def test_a_failed_page_aborts_the_pull_and_keeps_the_previous_snapshot(tmp_path, monkeypatch):
    """Stitching pages from different moments would be inconsistent, which is
    worse than stale."""
    from boardkit import sync
    from boardkit.store import Store

    config = Config(root=tmp_path, owner="octo", repo="octo/widgets", project="4")
    kept = Store(config)
    before = Snapshot(project_id="PVT_1", synced_at="earlier",
                      items=(ProjectItem(number=1, id="I_1"),))
    kept.save_state(before)

    def dies(config, number):
        raise BoardError("API rate limit exceeded")

    monkeypatch.setattr(sync, "fetch_board", dies)
    with pytest.raises(BoardError):
        sync.pull(config, kept)
    assert kept.state().synced_at == "earlier"
    assert kept.state().item(1) is not None
