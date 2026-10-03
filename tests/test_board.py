import pathlib

from boardkit.board import ProjectItem, Shape, normalize, rank, rollup
from boardkit.config import Config

# Columns are named here rather than relied on as a default: boardkit ships
# GitHub's generic three, and a consuming repo names its own.
CONFIG = Config(root=pathlib.Path("/repo"), owner="octo", project="4",
                repo="octo/places", fields=("workstream", "effort"),
                status_order=("Ready", "Inbox", "Done"))

ITEMS = [
    {"content": {"number": 1, "title": "Corridor", "url": "u1"},
     "status": "Ready", "workstream": "Maps", "effort": "M"},
    {"content": {"number": 2, "title": "Closed one", "url": "u2"},
     "status": "Done", "workstream": "Maps", "effort": "S"},
    {"content": {"number": 3, "title": "Unfiled", "url": "u3"}, "status": "Inbox"},
    {"content": {}, "status": "Ready"},
]


def test_closed_issues_are_dropped_even_though_the_board_still_lists_them():
    items = normalize(ITEMS, {1, 3}, CONFIG)
    assert [item.number for item in items] == [1, 3]


def test_a_draft_item_with_no_issue_is_skipped():
    assert all(item.number for item in normalize(ITEMS, {1, 3}, CONFIG))


def test_missing_fields_fall_back_per_role():
    item = normalize(ITEMS, {3}, CONFIG)[0]
    # The grouping field needs a bucket name; any other field is just absent.
    assert (item.field("workstream"), item.field("effort")) == ("Unassigned", "-")


VIEW = Shape(columns=("Ready", "Inbox", "Done"), names=("workstream", "effort"),
             grouping="workstream")


def test_rank_orders_by_configured_columns_and_parks_the_unknown_last():
    assert rank("Ready", VIEW) < rank("Done", VIEW) < rank("Mystery", VIEW)


def test_rollup_counts_by_the_grouping_field(capsys):
    rollup(normalize(ITEMS, {1, 2, 3}, CONFIG), VIEW)
    out = capsys.readouterr().out
    assert "Maps" in out and "Ready=1" in out and "Done=1" in out
    assert "Unassigned  Inbox=1" in out


def test_board_json_keeps_the_flat_shape_a_jq_pipeline_expects():
    item = normalize(ITEMS, {1}, CONFIG)[0]
    assert item.flat() == {"ref": "1", "number": 1, "title": "Corridor",
                           "url": "u1", "status": "Ready", "workstream": "Maps",
                           "effort": "M"}


def test_status_is_reachable_as_a_field_so_a_board_can_group_by_it():
    item = normalize(ITEMS, {1}, CONFIG)[0]
    assert item.field("status") == "Ready"


# The shapes `gh project item-list` really returns, taken from a live board.
LIVE = [
    {"id": "PVTI_x", "status": "Done", "workstream": "UI style",
     "sprint": {"duration": 14, "iterationId": "149447a6",
                "startDate": "2026-09-26", "title": "Sprint 1"},
     "labels": ["feature", "design"],
     "content": {"number": 62, "title": "Short place names", "type": "Issue",
                 "url": "https://github.com/agerardin/places/issues/62"}},
]
LIVE_CONFIG = Config(root=pathlib.Path("/repo"), owner="octo", project="4",
                     repo="octo/places", fields=("workstream", "sprint", "labels"),
                     status_order=("Ready", "Done"))


def test_the_item_type_gh_prints_is_read_as_the_graphql_enum():
    assert normalize(LIVE, {62}, LIVE_CONFIG)[0].type == "ISSUE"


def test_an_iteration_value_prints_its_title_not_its_object():
    assert normalize(LIVE, {62}, LIVE_CONFIG)[0].field("sprint") == "Sprint 1"


def test_a_list_value_is_joined_rather_than_stringified():
    assert normalize(LIVE, {62}, LIVE_CONFIG)[0].field("labels") == "feature, design"


def test_an_unrecognised_item_kind_is_recorded_rather_than_rejected():
    hidden = [{"content": {"number": 1, "type": "Mystery"}}]
    assert normalize(hidden, {1}, LIVE_CONFIG)[0].type == "REDACTED"


def test_a_board_that_declares_nothing_is_read_from_its_own_fields():
    from boardkit.board import shape
    from boardkit.config import ProjectField
    bare = Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets")
    found = (ProjectField(name="status", options=("Inbox", "Todo", "Done")),
             ProjectField(name="area", options=("sync", "docs")))
    view = shape(bare, found)
    assert view.columns == ("Inbox", "Todo", "Done")
    assert view.names == ("area",) and view.grouping == "area"


def test_what_a_repo_does_declare_wins_over_the_board():
    from boardkit.board import shape
    from boardkit.config import ProjectField
    stated = Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets",
                    status_order=("Todo", "Done"), fields=("effort",))
    found = (ProjectField(name="status", options=("Inbox", "Todo", "Done")),
             ProjectField(name="area"))
    view = shape(stated, found)
    assert view.columns == ("Todo", "Done")
    # Declared first, then whatever else the board turned out to have.
    assert view.names == ("effort", "area")


def test_the_issue_text_travels_with_the_item():
    """Deciding what to work on means reading the acceptance criteria."""
    carried = [{"content": {"number": 1, "title": "T", "body": "Done when: it works"}}]
    assert normalize(carried, {1}, CONFIG)[0].body == "Done when: it works"


def test_json_leaves_the_body_out_so_a_pipeline_stays_readable():
    carried = [{"content": {"number": 1, "title": "T", "body": "pages of prose"}}]
    assert "body" not in normalize(carried, {1}, CONFIG)[0].flat()


LABELLED = [{"content": {"number": 1, "title": "T",
                         "labels": {"nodes": [{"name": "feature"},
                                              {"name": "concept:offline"}]},
                         "assignees": {"nodes": [{"login": "agerardin"}]},
                         "milestone": {"title": "v1"}}}]


def test_issue_metadata_an_agent_reads_travels_with_the_item():
    item = normalize(LABELLED, {1}, CONFIG)[0]
    assert item.labels == ("feature", "concept:offline")
    assert item.assignees == ("agerardin",) and item.milestone == "v1"


def test_json_carries_labels_but_omits_what_an_item_does_not_have():
    row = normalize(LABELLED, {1}, CONFIG)[0].flat()
    assert row["labels"] == "feature, concept:offline" and row["milestone"] == "v1"
    bare = normalize([{"content": {"number": 2, "title": "T"}}], {2}, CONFIG)[0].flat()
    assert "labels" not in bare and "milestone" not in bare


DRAFT = [{"id": "PVTI_lAHOabcdef123456", "type": "DRAFT_ISSUE",
          "content": {"title": "Try a sticky note", "body": "no plan yet"}}]


def test_a_draft_is_named_by_a_handle_since_it_has_no_number():
    item = normalize(DRAFT, set(), LIVE_CONFIG)[0]
    assert item.number == 0 and item.ref == "~123456" and item.draft
    assert item.title == "Try a sticky note" and item.body == "no plan yet"


def test_a_draft_is_kept_even_though_it_can_be_neither_open_nor_closed():
    """Closed issues are dropped by number; a draft has no number to check."""
    assert len(normalize(DRAFT, set(), LIVE_CONFIG)) == 1


def test_an_issue_is_still_named_by_its_number():
    assert normalize(LIVE, {62}, LIVE_CONFIG)[0].ref == "62"


def test_the_kind_is_taken_in_either_spelling():
    """GraphQL says DRAFT_ISSUE, `gh` said DraftIssue, and splitting an
    already-spelled value on capitals turned it into nonsense."""
    from boardkit.board import ProjectItem
    assert ProjectItem(number=1, type="DRAFT_ISSUE").type == "DRAFT_ISSUE"
    assert ProjectItem(number=1, type="DraftIssue").type == "DRAFT_ISSUE"
    assert ProjectItem(number=1, type="Issue").type == "ISSUE"
    assert ProjectItem(number=1, type="Mystery").type == "REDACTED"


SEARCHABLE = [
    ProjectItem(number=1, title="Short place names", body="drop the country",
                labels=("feature",)),
    ProjectItem(number=2, title="Prune the suite", body="delete what no longer earns"),
]


def test_search_reads_title_body_and_labels_from_the_snapshot():
    from boardkit.board import matching
    assert [i.number for i in matching(SEARCHABLE, "country")] == [1]
    assert [i.number for i in matching(SEARCHABLE, "feature")] == [1]
    assert [i.number for i in matching(SEARCHABLE, "prune")] == [2]


def test_every_word_has_to_appear_somewhere():
    from boardkit.board import matching
    assert [i.number for i in matching(SEARCHABLE, "place country")] == [1]
    assert matching(SEARCHABLE, "place suite") == []


def test_searching_for_nothing_finds_nothing():
    from boardkit.board import matching
    assert matching(SEARCHABLE, "   ") == []
