import pathlib

from boardkit.board import normalize, rank, rollup
from boardkit.config import Config

CONFIG = Config(root=pathlib.Path("/repo"), owner="octo", project="4",
                repo="octo/places", fields=("workstream", "effort"))

ITEMS = [
    {"content": {"number": 1, "title": "Corridor", "url": "u1"},
     "status": "Ready", "workstream": "Maps", "effort": "M"},
    {"content": {"number": 2, "title": "Closed one", "url": "u2"},
     "status": "Done", "workstream": "Maps", "effort": "S"},
    {"content": {"number": 3, "title": "Unfiled", "url": "u3"}, "status": "Inbox"},
    {"content": {}, "status": "Ready"},
]


def test_closed_issues_are_dropped_even_though_the_board_still_lists_them():
    rows = normalize(ITEMS, {1, 3}, CONFIG)
    assert [row["number"] for row in rows] == [1, 3]


def test_a_draft_item_with_no_issue_is_skipped():
    assert all(row["number"] is not None for row in normalize(ITEMS, {1, 3}, CONFIG))


def test_missing_fields_fall_back_per_role():
    row = normalize(ITEMS, {3}, CONFIG)[0]
    # The grouping field needs a bucket name; any other field is just absent.
    assert (row["workstream"], row["effort"]) == ("Unassigned", "-")


def test_rank_orders_by_configured_columns_and_parks_the_unknown_last():
    assert rank("Ready", CONFIG) < rank("Done", CONFIG) < rank("Mystery", CONFIG)


def test_rollup_counts_by_the_grouping_field(capsys):
    rollup(normalize(ITEMS, {1, 2, 3}, CONFIG), CONFIG)
    out = capsys.readouterr().out
    assert "Maps" in out and "Ready=1" in out and "Done=1" in out
    assert "Unassigned  Inbox=1" in out
