import pathlib

from boardkit.board import ProjectItem, Shape, normalize, rank
from boardkit.config import Config, ProjectField

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


def test_an_item_with_no_content_at_all_is_skipped():
    """Not a draft — a draft is kept. This is an entry GitHub returned with
    nothing in it, which has no number to match against the open set."""
    assert all(item.number for item in normalize(ITEMS, {1, 3}, CONFIG))


def test_a_field_an_item_does_not_carry_reads_as_absent():
    item = normalize(ITEMS, {3}, CONFIG)[0]
    assert (item.field("workstream"), item.field("effort")) == ("-", "-")


VIEW = Shape(columns=("Ready", "Inbox", "Done"), names=("workstream", "effort"))


def test_rank_orders_by_configured_columns_and_parks_the_unknown_last():
    assert rank("Ready", VIEW) < rank("Done", VIEW) < rank("Mystery", VIEW)


def test_board_json_keeps_the_flat_shape_a_jq_pipeline_expects():
    item = normalize(ITEMS, {1}, CONFIG)[0]
    assert item.flat() == {"ref": "1", "number": 1, "title": "Corridor",
                           "url": "u1", "status": "Ready", "workstream": "Maps",
                           "effort": "M"}


def test_status_is_reachable_as_a_field_like_any_other():
    item = normalize(ITEMS, {1}, CONFIG)[0]
    assert item.field("status") == "Ready"


# One item as `flatten` hands it over, taken from a live board. Field values
# have already been reduced to a scalar or a list of names by `value_of`.
LIVE = [
    {"id": "PVTI_x", "status": "Done", "workstream": "UI style",
     "sprint": "Sprint 1",
     "labels": ["feature", "design"],
     "content": {"number": 62, "title": "Short place names", "type": "Issue",
                 "url": "https://github.com/agerardin/places/issues/62"}},
]
LIVE_CONFIG = Config(root=pathlib.Path("/repo"), owner="octo", project="4",
                     repo="octo/places", fields=("workstream", "sprint", "labels"),
                     status_order=("Ready", "Done"))


def test_a_list_value_is_joined_rather_than_stringified():
    assert normalize(LIVE, {62}, LIVE_CONFIG)[0].field("labels") == "feature, design"


def test_an_unrecognised_item_kind_is_recorded_rather_than_rejected():
    hidden = [{"content": {"number": 1, "type": "Mystery"}}]
    assert normalize(hidden, {1}, LIVE_CONFIG)[0].type == "REDACTED"


def test_a_board_that_declares_nothing_is_read_from_its_own_fields():
    from boardkit.board import shape
    bare = Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets")
    found = (ProjectField(name="status", options=("Inbox", "Todo", "Done")),
             ProjectField(name="area", options=("sync", "docs")))
    view = shape(bare, found)
    assert view.columns == ("Inbox", "Todo", "Done")
    assert view.names == ("area",)


def test_what_a_repo_does_declare_wins_over_the_board():
    from boardkit.board import shape
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


def test_an_already_spelled_enum_passes_through_untouched():
    """It once went through a camel-case split, which turned DRAFT_ISSUE into
    D_R_A_F_T___I_S_S_U_E and landed a draft in REDACTED as a hidden item."""
    from boardkit.board import ProjectItem
    assert ProjectItem(number=1, type="DRAFT_ISSUE").type == "DRAFT_ISSUE"
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


def test_an_empty_listing_says_what_the_board_can_hold():
    """Otherwise the first card has to be filed against guessed names: the
    listing is the only place the board's shape appears."""
    import contextlib
    import io

    from boardkit.board import show
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        show([], Shape(columns=("Inbox", "Todo"), names=("area", "blocked")))
    printed = out.getvalue()
    assert "columns: Inbox, Todo" in printed and "fields:  area, blocked" in printed


def test_json_reports_an_unset_field_as_empty_rather_than_a_dash():
    """`-` is a listing's way of filling a column, not a value: a jq pipeline
    had to know the convention, and that it applied to only some rows."""
    item = ProjectItem(number=1, title="A thing",
                       fields={"status": "Todo", "blocked": "-"})
    assert item.flat()["blocked"] == ""
    # The listing still shows the dash.
    assert item.field("blocked") == "-"


# GitHub stores a NUMBER field as a float, so `value_of` hands a whole number
# over as `3.0`. These pin the two things that depended on it reading as "3":
# a legible column, and a write that can be recognised as already true.
NUMBERED = [{"content": {"number": 1, "title": "T"}, "effort": 3.0}]


def test_a_whole_number_field_reads_without_the_trailing_zero():
    assert normalize(NUMBERED, {1}, CONFIG)[0].field("effort") == "3"


def test_a_fractional_number_field_keeps_its_fraction():
    halved = [{"content": {"number": 1, "title": "T"}, "effort": 2.5}]
    assert normalize(halved, {1}, CONFIG)[0].field("effort") == "2.5"


def test_kept_closed_items_are_marked_rather_than_dropped():
    every = normalize(ITEMS, {1, 3}, CONFIG, keep_closed=True)
    assert [(i.number, i.closed) for i in every if i.number] == [(1, False), (2, True), (3, False)]


def test_comments_are_carried_and_searched():
    from boardkit.board import matching
    payload = [{"content": {"number": 1, "title": "Plan", "comments": {
        "totalCount": 3, "nodes": [
            {"author": {"login": "ada"}, "createdAt": "2026-10-11T00:00:00Z",
             "body": "Decision: go with the device flow."}]}}}]
    item = normalize(payload, {1}, CONFIG)[0]
    assert item.comment_count == 3 and item.comments[0].author == "ada"
    assert matching([item], "device flow") == [item]


def test_pull_request_state_and_linked_prs_are_carried():
    payload = [
        {"type": "PULL_REQUEST", "content": {
            "number": 9, "title": "Fix", "reviewDecision": "CHANGES_REQUESTED",
            "mergeable": "MERGEABLE", "statusCheckRollup": {"state": "FAILURE"}}},
        {"content": {"number": 1, "title": "Bug", "closedByPullRequestsReferences": {
            "nodes": [{"number": 9, "state": "OPEN"}]}}}]
    pr, bug = normalize(payload, {1, 9}, CONFIG)
    assert (pr.review, pr.mergeable, pr.checks) == ("changes requested", "mergeable", "failure")
    assert bug.linked_prs == ("#9 open",)
