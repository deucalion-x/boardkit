import pathlib

import pytest

from boardkit import sync
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
    args = edit_args(Write(ref="7", field="status", value="In progress", was="Ready"),
                     snapshot(), CONFIG)
    assert args[:2] == ["project", "item-edit"]
    assert "--single-select-option-id" in args and "O_P" in args
    assert "I_7" in args and "F_S" in args and "PVT_1" in args


def test_a_date_write_uses_the_date_flag():
    args = edit_args(Write(ref="7", field="due", value="2026-11-01"), snapshot(), CONFIG)
    assert args[-2:] == ["--date", "2026-11-01"]


def test_a_value_that_is_not_an_option_is_refused_before_the_call():
    with pytest.raises(BoardError, match="not an option"):
        edit_args(Write(ref="7", field="status", value="Sideways"), snapshot(), CONFIG)


def test_writing_an_item_absent_from_the_snapshot_asks_for_a_pull():
    with pytest.raises(BoardError, match="pull first"):
        edit_args(Write(ref="99", field="status", value="Ready"), snapshot(), CONFIG)


def test_a_write_applies_when_the_remote_still_holds_what_it_was_decided_against():
    applies, lost = plan([Write(ref="7", field="status", value="In progress",
                                was="Ready")], snapshot())
    assert len(applies) == 1 and lost == []


def test_a_write_loses_when_the_field_moved_on_github():
    applies, lost = plan([Write(ref="7", field="status", value="In progress",
                                was="Done")], snapshot())
    assert applies == [] and lost[0][1] == "Ready"
    assert "changed on GitHub" in lost[0][2]


def test_a_write_already_true_remotely_is_dropped_rather_than_pushed():
    applies, lost = plan([Write(ref="7", field="status", value="Ready",
                                was="Ready")], snapshot())
    assert applies == [] and lost == []


def test_a_write_against_an_item_that_left_the_board_loses():
    applies, lost = plan([Write(ref="99", field="status", value="Ready")], snapshot())
    # The reason is a reason, not a value dressed up as one.
    assert applies == [] and lost[0][1] == ""
    assert "closed or no longer on the board" in lost[0][2]


def test_rejected_writes_land_in_the_store_with_both_values(tmp_path):
    kept = Store(Config(root=tmp_path, owner="octo", project="4",
                        repo="octo/widgets", fields=("workstream",)))
    write = Write(ref="7", field="status", value="In progress", was="Done")
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
    write = Write(ref="8", field="area", value="config, docs")
    assert chosen(write, MULTI.field("area")) == ["o1", "o2"]


def test_a_multi_select_write_goes_through_its_own_mutation():
    from boardkit.sync import multi_select_query
    query = multi_select_query(Write(ref="8", field="area", value="config, docs"), MULTI)
    assert 'multiSelectOptionIds: ["o1", "o2"]' in query
    assert 'itemId: "I_8"' in query and 'fieldId: "F_A"' in query


def test_one_bad_option_refuses_the_whole_multi_select_write():
    from boardkit.sync import chosen
    with pytest.raises(BoardError, match="not an option"):
        chosen(Write(ref="8", field="area", value="config, nonsense"),
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
    with pytest.raises(BoardError, match="could not resolve") as raised:
        plumbing.run(["gh", "project", "view"])
    assert len(calls) == 1
    # GitHub's "Could not resolve to a ..." is a refusal, not the network:
    # read as offline, it was queued and retried rather than reported.
    assert not isinstance(raised.value, plumbing.Unreachable)


def test_what_was_written_is_folded_in_rather_than_pulled_again():
    from boardkit.sync import patched
    after = patched(snapshot(), [Write(ref="7", field="status", value="In progress",
                                       was="Ready")])
    assert after.item(7).field("status") == "In progress"
    # Untouched fields survive the copy.
    assert after.item(7).field("workstream") == "Maps"


def test_folding_in_leaves_items_nobody_wrote_to_alone():
    from boardkit.sync import patched
    before = snapshot()
    after = patched(before, [Write(ref="99", field="status", value="Done")])
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
    fresh = sync.refreshed(config, kept, [Write(ref="7", field="status", value="x")])
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
    fresh = sync.refreshed(config, kept, [Write(ref="7", field="status", value="x")])
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


def test_every_write_travels_in_one_request(monkeypatch):
    """GitHub takes one field per mutation; GraphQL takes many per document."""
    from boardkit import sync
    sent = []
    monkeypatch.setattr(sync, "run_json",
                        lambda args: (sent.append(args[-1]), {"data": {}})[1])
    writes = [Write(ref="7", field="status", value="In progress"),
              Write(ref="7", field="workstream", value="Maps"),
              Write(ref="7", field="due", value="2026-11-01")]
    landed = sync.apply_all(writes, snapshot())
    assert len(sent) == 1                      # one request, not three
    assert sent[0].count("updateProjectV2ItemFieldValue") == 3
    assert "m0:" in sent[0] and "m2:" in sent[0]
    assert len(landed) == 3


def test_a_write_github_names_in_an_error_stays_queued(monkeypatch):
    from boardkit import sync
    monkeypatch.setattr(sync, "run_json", lambda args: {
        "data": {}, "errors": [{"path": ["m1"], "message": "nope"}]})
    writes = [Write(ref="7", field="status", value="In progress"),
              Write(ref="7", field="workstream", value="Maps")]
    landed = sync.apply_all(writes, snapshot())
    assert [write.field for write in landed] == ["status"]


def test_each_field_type_is_sent_in_the_shape_the_mutation_wants(monkeypatch):
    from boardkit import sync
    sent = []
    monkeypatch.setattr(sync, "run_json",
                        lambda args: (sent.append(args[-1]), {"data": {}})[1])
    sync.apply_all([Write(ref="7", field="status", value="In progress"),
                    Write(ref="7", field="due", value="2026-11-01")], snapshot())
    assert 'singleSelectOptionId: "O_P"' in sent[0]
    assert 'date: "2026-11-01"' in sent[0]




def test_a_draft_survives_the_pull_that_used_to_drop_it():
    """`flatten` puts the kind inside `content`; the skip only looked on top."""
    from boardkit.board import normalize
    from boardkit.sync import flatten
    node = {"id": "PVTI_abcdef123456", "type": "DRAFT_ISSUE",
            "content": {"title": "An idea", "body": ""},
            "fieldValues": {"nodes": []}}
    row = flatten(node)
    assert row["content"]["type"] == "DRAFT_ISSUE"
    kept = normalize([row], set(), CONFIG)
    assert len(kept) == 1 and kept[0].ref.startswith("~")


def test_a_field_declared_with_a_capital_keeps_its_value():
    """`flatten` keys values by `key(name)`; config keeps "Start" as written,
    so reading it back by the declared spelling always found nothing."""
    from boardkit.board import normalize
    from boardkit.sync import flatten
    config = Config(root=pathlib.Path("."), owner="o", repo="o/p",
                    fields=(ProjectField(name="Start", data_type="DATE"),))
    node = {"id": "PVTI_1", "type": "ISSUE",
            "content": {"number": 7, "title": "Dated"},
            "fieldValues": {"nodes": [{"field": {"name": "Start"},
                                       "date": "2026-10-14"}]}}
    item = normalize([flatten(node)], {7}, config)[0]
    assert item.field("Start") == "2026-10-14"


def test_a_field_config_declares_and_the_board_lacks_is_reported():
    """Boards are edited in the browser; config quietly becoming fiction is
    what `admin apply` would then act on."""
    from boardkit.sync import field_drift
    config = Config(root=pathlib.Path("."), owner="o", repo="o/p",
                    fields=(ProjectField(name="area", options=("Core", "Docs")),
                            ProjectField(name="effort")))
    board = Snapshot(fields=(ProjectField(name="area", options=("Core",)),))
    said = " ".join(field_drift(board, config))
    assert "'Docs'" in said and "'effort' field" in said


def test_a_field_the_board_has_and_config_does_not_is_not_drift():
    from boardkit.sync import field_drift
    config = Config(root=pathlib.Path("."), owner="o", repo="o/p")
    board = Snapshot(fields=(ProjectField(name="sprint"),))
    assert field_drift(board, config) == []


def test_a_view_deleted_in_the_browser_is_reported():
    """`admin apply` would put it straight back, so the disagreement is worth
    a line before that happens."""
    from boardkit.sync import view_drift
    config = Config(root=pathlib.Path("."), owner="o", repo="o/p",
                    views=({"name": "Open work"}, {"name": "Everything"}))
    board = Snapshot(views=("Everything",))
    assert "'Open work'" in " ".join(view_drift(board, config))


def test_no_views_in_the_snapshot_says_nothing():
    """A snapshot from before views were carried is silent, not wrong."""
    from boardkit.sync import view_drift
    config = Config(root=pathlib.Path("."), owner="o", repo="o/p",
                    views=({"name": "Open work"},))
    assert view_drift(Snapshot(), config) == []


def test_a_pull_carries_a_field_config_never_declared():
    """Declaring one field narrowed the snapshot to it, so any other field's
    value was dropped on every pull while the board still held it."""
    from boardkit.board import normalize
    config = Config(root=pathlib.Path("."), owner="o", repo="o/p",
                    fields=(ProjectField(name="area"),))
    names = ("status", "area", "blocked")
    row = {"id": "I_1", "status": "Todo", "area": "Core",
           "blocked": "waiting on a decision",
           "content": {"number": 4, "title": "A thing", "type": "ISSUE"}}
    item = normalize([row], {4}, config, names)[0]
    assert item.field("blocked") == "waiting on a decision"


def test_clearing_a_field_is_its_own_mutation(monkeypatch):
    """`update` has no way to say "no value", so an empty write has to use
    `clearProjectV2ItemFieldValue` or it reads as an invalid update."""
    from boardkit import sync
    sent = {}
    monkeypatch.setattr(sync, "run_json",
                        lambda args: (sent.update(q=args[-1]), {"data": {}})[1])
    snapshot = Snapshot(
        project_id="PVT_1", synced_at="t",
        fields=(ProjectField(name="area", id="F_A", options=("sync",),
                             option_ids={"sync": "O_S"}),),
        items=(ProjectItem(number=1, id="I_1", fields={"area": "sync"}),))
    sync.apply_all([Write(ref="1", field="area", value="")], snapshot)
    assert "clearProjectV2ItemFieldValue" in sent["q"]
    assert "updateProjectV2ItemFieldValue" not in sent["q"]


def test_a_value_that_is_not_an_option_is_refused_before_it_is_recorded():
    """`check` runs before the queue. A refusal that reached the queue would
    stay there: a push refusal is not a rejection, so nothing clears it."""
    from boardkit import sync
    snapshot = Snapshot(
        project_id="PVT_1", synced_at="t",
        fields=(ProjectField(name="area", id="F_A", options=("sync",),
                             option_ids={"sync": "O_S"}),),
        items=(ProjectItem(number=1, id="I_1"),))
    with pytest.raises(BoardError, match="not an option"):
        sync.check(Write(ref="1", field="area", value="nonsense"), snapshot)


def test_clearing_passes_the_check_for_every_field_type():
    from boardkit import sync
    snapshot = Snapshot(
        project_id="PVT_1", synced_at="t",
        fields=(ProjectField(name="area", id="F_A", options=("sync",),
                             option_ids={"sync": "O_S"}),),
        items=(ProjectItem(number=1, id="I_1"),))
    assert sync.check(Write(ref="1", field="area", value=""), snapshot) is None


def test_a_field_only_github_can_judge_is_left_to_github():
    """An unknown field is not refused locally: the snapshot may simply be
    behind, and GitHub's own words are the better error."""
    from boardkit import sync
    snapshot = Snapshot(project_id="PVT_1", synced_at="t",
                        items=(ProjectItem(number=1, id="I_1"),))
    assert sync.check(Write(ref="1", field="whatever", value="x"), snapshot) is None


def test_a_write_already_true_remotely_leaves_the_queue(tmp_path, monkeypatch):
    """Found by `scripts/contract.py` against a real board.

    `plan` skips such a write — it is neither applied nor lost — and `push`
    was clearing only what landed and what lost. So the write stayed pending
    forever: retried on every push, and counted as queued by every listing,
    with nothing able to clear it because it can never land or be rejected.
    """
    from boardkit import sync
    from boardkit.store import Store

    config = Config(root=tmp_path, owner="octo", repo="octo/widgets")
    kept = Store(config)
    kept.save_state(Snapshot(
        project_id="PVT_1", synced_at="2026-10-03T00:00:00+00:00",
        fields=(ProjectField(name="status", id="F_S",
                             option_ids={"Ready": "O_R"}),),
        items=(ProjectItem(number=7, id="I_7", fields={"status": "Ready"}),)))
    kept.queue("7", "status", "Ready")
    # Already true on GitHub, which is what makes it a no-op rather than a win.
    monkeypatch.setattr(sync, "fetch_some", lambda config, ids: [
        {"id": "I_7", "content": {"number": 7, "state": "OPEN"}, "status": "Ready"}])
    def nothing_to_send(writes, snapshot):
        assert writes == [], "a value already true should send no mutation"
        return []

    monkeypatch.setattr(sync, "apply_all", nothing_to_send)

    pushed, lost = sync.push(config, kept)
    assert (pushed, lost) == (0, 0)
    assert kept.pending() == []
    assert kept.rejected() == []


def test_a_change_queued_during_a_push_is_not_dropped_by_it(tmp_path, monkeypatch):
    """The queue is re-read under the lock, so clearing names what it clears.

    Dropping everything the push did not attempt would be the shorter way to
    write this, and it would discard a `board set` made while the push was in
    flight.
    """
    from boardkit import sync
    from boardkit.store import Store

    config = Config(root=tmp_path, owner="octo", repo="octo/widgets")
    kept = Store(config)
    kept.save_state(Snapshot(
        project_id="PVT_1", synced_at="2026-10-03T00:00:00+00:00",
        fields=(ProjectField(name="status", id="F_S",
                             option_ids={"Ready": "O_R", "Done": "O_D"}),),
        items=(ProjectItem(number=7, id="I_7", fields={"status": "Ready"}),)))
    kept.queue("7", "status", "Done")
    monkeypatch.setattr(sync, "fetch_some", lambda config, ids: [
        {"id": "I_7", "content": {"number": 7, "state": "OPEN"}, "status": "Ready"}])

    def lands_and_someone_else_queues(writes, snapshot):
        kept.queue("7", "status", "Ready")
        return writes

    monkeypatch.setattr(sync, "apply_all", lands_and_someone_else_queues)
    pushed, _lost = sync.push(config, kept)
    assert pushed == 1
    assert [write.value for write in kept.pending()] == ["Ready"]


def test_a_number_write_matching_the_board_is_settled_rather_than_resent():
    """GitHub stores a NUMBER as a float, so a board holding 3 reports 3.0.
    Compared as text that never equalled `board set 7 effort 3`, so the write
    was re-sent on every push. `as_text` drops the `.0`; this is the seam
    that depended on it.
    """
    from boardkit.board import normalize
    config = Config(root=pathlib.Path("/repo"), owner="octo", project="4",
                    repo="octo/widgets", fields=("effort",))
    board = normalize([{"content": {"number": 7, "title": "T"}, "effort": 3.0}],
                      {7}, config)
    remote = Snapshot(project_id="PVT_1", synced_at="2026-10-03T00:00:00+00:00",
                      fields=(ProjectField(name="effort", id="F_E",
                                           data_type="NUMBER"),),
                      items=tuple(board))
    applies, lost = plan([Write(ref="7", field="effort", value="3", was="3")],
                         remote)
    assert applies == [] and lost == []


def pushable(tmp_path, fields, items):
    """A store holding one snapshot, ready for `push`."""
    from boardkit.store import Store
    config = Config(root=tmp_path, owner="octo", repo="octo/widgets", project="4")
    kept = Store(config)
    kept.save_state(Snapshot(project_id="PVT_1", project_number="4",
                             synced_at="2026-10-03T00:00:00+00:00",
                             fields=fields, items=items))
    return config, kept


def test_a_field_added_in_the_browser_is_looked_up_rather_than_failing(
        tmp_path, monkeypatch):
    """`check` lets a write to an unknown field be queued, on the grounds that
    only GitHub can say whether it exists. So the push has to ask."""
    from boardkit import sync

    config, kept = pushable(
        tmp_path, (ProjectField(name="status", id="F_S",
                                option_ids={"Ready": "O_R"}),),
        (ProjectItem(number=7, id="I_7", fields={"status": "Ready"}),))
    kept.queue("7", "effort", "3")
    monkeypatch.setattr(sync, "fetch_fields", lambda config, number: [
        {"id": "F_S", "name": "Status", "dataType": "SINGLE_SELECT",
         "options": [{"id": "O_R", "name": "Ready"}]},
        {"id": "F_E", "name": "Effort", "dataType": "NUMBER"}])
    monkeypatch.setattr(sync, "fetch_some", lambda config, ids: [
        {"id": "I_7", "content": {"number": 7, "state": "OPEN"},
         "status": "Ready"}])
    sent = {}

    def record(args):
        sent["document"] = args[-1]
        return {"data": {}}

    monkeypatch.setattr(sync, "run_json", record)
    landed, lost = sync.push(config, kept)
    assert (landed, lost) == (1, 0)
    assert "F_E" in sent["document"]
    assert kept.pending() == []


def test_one_write_to_a_field_the_board_lacks_no_longer_strands_the_others(
        tmp_path, monkeypatch):
    """`apply_all` raises on a field with no id, and raises before the document
    is built — so one such write used to take the whole push down with it and
    every other queued change went nowhere."""
    from boardkit import sync

    config, kept = pushable(
        tmp_path, (ProjectField(name="status", id="F_S",
                                option_ids={"Ready": "O_R", "Done": "O_D"}),),
        (ProjectItem(number=7, id="I_7", fields={"status": "Ready"}),))
    kept.queue("7", "ghost", "whatever")
    kept.queue("7", "status", "Done")
    # GitHub confirms the field really does not exist.
    monkeypatch.setattr(sync, "fetch_fields", lambda config, number: [
        {"id": "F_S", "name": "Status", "dataType": "SINGLE_SELECT",
         "options": [{"id": "O_R", "name": "Ready"}, {"id": "O_D", "name": "Done"}]}])
    monkeypatch.setattr(sync, "fetch_some", lambda config, ids: [
        {"id": "I_7", "content": {"number": 7, "state": "OPEN"},
         "status": "Ready"}])
    monkeypatch.setattr(sync, "run_json", lambda args: {"data": {}})
    landed, lost = sync.push(config, kept)
    # The good write landed; the impossible one was rejected rather than
    # raising, so the queue is empty and the loss is on the record.
    assert (landed, lost) == (1, 1)
    assert kept.pending() == []
    rejected = kept.rejected()
    assert len(rejected) == 1
    assert rejected[0].write.field == "ghost"
    assert "no field `ghost`" in rejected[0].reason


def test_an_ordinary_push_asks_for_no_extra_field_read(tmp_path, monkeypatch):
    """The lookup is the exception, not a cost added to every push."""
    from boardkit import sync

    config, kept = pushable(
        tmp_path, (ProjectField(name="status", id="F_S",
                                option_ids={"Ready": "O_R", "Done": "O_D"}),),
        (ProjectItem(number=7, id="I_7", fields={"status": "Ready"}),))
    kept.queue("7", "status", "Done")

    def refuse(config, number):
        raise AssertionError("fields were re-read for a push that did not need it")

    monkeypatch.setattr(sync, "fetch_fields", refuse)
    monkeypatch.setattr(sync, "fetch_some", lambda config, ids: [
        {"id": "I_7", "content": {"number": 7, "state": "OPEN"},
         "status": "Ready"}])
    monkeypatch.setattr(sync, "run_json", lambda args: {"data": {}})
    assert sync.push(config, kept) == (1, 0)


# --- A standing rejection guards its field, once ---------------------------

def contested_store(tmp_path, monkeypatch):
    """A store holding one rejection: #1 area lost to `Maps`."""
    from boardkit.store import Rejection

    config = Config(root=tmp_path, owner="octo", project="4", repo="octo/widgets")
    store = Store(config)
    store.save_rejected([Rejection(
        write=Write(ref="1", field="area", value="Trails"),
        remote="Maps", reason="changed remotely")])
    return store


def test_a_write_to_a_field_that_lost_is_refused(tmp_path, monkeypatch):
    store = contested_store(tmp_path, monkeypatch)
    with pytest.raises(BoardError) as caught:
        sync.contested(Write(ref="1", field="area", value="Trails"), store)
    said = str(caught.value)
    assert "the board holds `Maps`" in said
    assert "repeat this command" in said


def test_the_refusal_consumes_the_rejection_so_the_repeat_stands(tmp_path,
                                                                 monkeypatch):
    """One-shot: deliberate repetition is how an agent says it still means it."""
    store = contested_store(tmp_path, monkeypatch)
    with pytest.raises(BoardError):
        sync.contested(Write(ref="1", field="area", value="Trails"), store)
    assert store.rejected() == []
    assert sync.contested(Write(ref="1", field="area", value="Trails"),
                          store) is None


def test_another_field_on_the_same_item_is_not_gated(tmp_path, monkeypatch):
    """The conflict was about one field. A batch must lose only that one."""
    store = contested_store(tmp_path, monkeypatch)
    assert sync.contested(Write(ref="1", field="status", value="Done"),
                          store) is None
    assert sync.contested(Write(ref="2", field="area", value="Trails"),
                          store) is None
    assert len(store.rejected()) == 1, "an ungated write spends nothing"


def test_a_partial_answer_is_an_error_not_an_empty_board(tmp_path, monkeypatch):
    """A credential that sees the project but not its items' repository gets
    the project with `items: null` and an error. That is not an empty board."""
    from boardkit import sync
    config = Config(root=tmp_path, owner="octo", owner_type="org",
                    repo="octo/widgets", project="4")
    monkeypatch.setattr(sync, "run_json", lambda args: {
        "data": {"organization": {"projectV2": {"id": "PVT_1", "items": None}}},
        "errors": [{"message": "Resource not accessible by integration"}]})
    with pytest.raises(BoardError, match=r"not accessible.*app is installed on octo/widgets"):
        sync.fetch_board(config, "4")


def test_repo_issues_are_cached_whole_and_refreshed_by_change(tmp_path, monkeypatch):
    """The first pull reads every issue; the next asks only for what changed
    since, and merges it, so the cache holds the whole history cheaply."""
    from boardkit import sync
    from boardkit.store import Store
    config = Config(root=tmp_path, owner="octo", repo="octo/widgets", project="4")
    store = Store(config)
    asked = []

    def issues(config, since=""):
        asked.append(since)
        if not since:
            return [{"id": "", "type": "ISSUE", "content": {
                        "number": 65, "state": "CLOSED", "title": "Archived",
                        "stateReason": "COMPLETED"}},
                    {"id": "", "type": "ISSUE", "content": {
                        "number": 66, "state": "OPEN", "title": "Old"}}]
        return [{"id": "", "type": "ISSUE", "content": {
                    "number": 66, "state": "CLOSED", "title": "Old, now done"}}]
    monkeypatch.setattr(sync, "fetch_repo_issues", issues)

    first, at = sync.repo_issues(config, store, ())
    assert [(i.number, i.closed) for i in first] == [(65, True), (66, False)]
    store.save_state(store.state().model_copy(update={
        "repo_issues": first, "issues_synced_at": at, "synced_at": "now"}))
    second, _ = sync.repo_issues(config, store, ())
    assert asked == ["", at]
    assert [(i.number, i.closed, i.title) for i in second] == [
        (65, True, "Archived"), (66, True, "Old, now done")]
    assert all(not i.on_board for i in second)

def test_a_refused_check_rollup_does_not_fail_the_pull():
    from boardkit import sync
    assert sync.tolerated({"path": ["organization", "projectV2", "items", "nodes", 0,
                                    "content", "statusCheckRollup"]})
    assert not sync.tolerated({"path": ["organization", "projectV2", "items"]})


def refreshing(tmp_path, monkeypatch, nodes):
    """A store holding three issues, and GitHub answering `nodes` for a refresh."""
    config = Config(root=tmp_path, owner="octo", project="4", repo="octo/widgets")
    store = Store(config)
    store.save_state(Snapshot(project_id="PVT_1", synced_at="2026-10-12T00:00:00Z",
                              items=(ProjectItem(number=7, id="I_7", title="Seven"),
                                     ProjectItem(number=8, id="I_8", title="Eight")),
                              closed=(ProjectItem(number=3, id="I_3", title="Three",
                                                  closed=True),)))
    sent = []
    monkeypatch.setattr(sync, "run_json",
                        lambda args: (sent.append(args[-1]), {"data": {"nodes": nodes}})[1])
    return config, store, sent


def test_a_refresh_reads_only_the_item_it_names_and_keeps_the_rest(tmp_path, monkeypatch):
    """A comment used to re-pull the whole board: two queries and every body
    on it. The item it touched is all that moved."""
    node = {"id": "I_7", "type": "ISSUE",
            "content": {"number": 7, "title": "Seven", "state": "OPEN", "body": "",
                        "comments": {"totalCount": 1, "nodes": [
                            {"author": {"login": "octo"}, "createdAt": "2026-10-12",
                             "body": "Verified."}]}},
            "fieldValues": {"nodes": []}}
    config, store, sent = refreshing(tmp_path, monkeypatch, [node])
    assert sync.refresh(config, store, ["I_7"])
    assert len(sent) == 1 and '"I_7"' in sent[0] and "items(" not in sent[0]
    after = store.state()
    assert [item.number for item in after.items] == [8, 7]
    assert after.item(7).comments and after.item(8).title == "Eight"


def test_an_item_closed_by_the_change_moves_to_closed(tmp_path, monkeypatch):
    node = {"id": "I_7", "type": "ISSUE",
            "content": {"number": 7, "title": "Seven", "state": "CLOSED",
                        "stateReason": "NOT_PLANNED"},
            "fieldValues": {"nodes": []}}
    config, store, _ = refreshing(tmp_path, monkeypatch, [node])
    assert sync.refresh(config, store, ["I_7"])
    after = store.state()
    assert after.item(7) is None
    assert sorted(item.number for item in after.closed) == [3, 7]


def test_an_item_that_cannot_be_read_back_leaves_the_snapshot_alone(tmp_path, monkeypatch):
    """GitHub takes a moment to index a new item. Saying so lets the caller
    pull instead, rather than dropping what the snapshot held."""
    config, store, _ = refreshing(tmp_path, monkeypatch, [None])
    before = store.state()
    assert not sync.refresh(config, store, ["I_7"])
    assert store.state() == before
    assert not sync.refresh(config, store, [""])


def pulling(tmp_path, monkeypatch, created, listed=()):
    """A pull whose board lists `listed`, with `created` noted as made here."""
    config = Config(root=tmp_path, owner="octo", project="4", repo="octo/widgets")
    store = Store(config)
    store.save_state(Snapshot(project_id="PVT_1", project_number="4",
                              synced_at="2026-10-12T00:00:00Z", created=created))
    monkeypatch.setattr(sync, "fetch_board", lambda config, number: {
        "id": "PVT_1", "url": "u", "views": [], "fields": [], "items": list(listed)})
    monkeypatch.setattr(sync, "repo_issues", lambda config, store, names: ((), ""))
    asked = []

    def by_id(args):
        asked.append(args[-1])
        return {"data": {"nodes": [{"id": "I_NEW", "type": "ISSUE",
                                    "content": {"number": 9, "title": "Just made",
                                                "state": "OPEN"},
                                    "fieldValues": {"nodes": []}}]}}

    monkeypatch.setattr(sync, "run_json", by_id)
    return config, store, asked


def test_a_pull_keeps_an_item_made_moments_ago_that_the_board_does_not_list_yet(
        tmp_path, monkeypatch):
    """GitHub lists a new item a few seconds after adding it. A sync in that
    window replaced the snapshot without it, and the item vanished locally."""
    from boardkit.store import now
    config, store, asked = pulling(tmp_path, monkeypatch, {"I_NEW": now()})
    sync.pull(config, store)
    assert store.state().item(9) is not None
    assert len(asked) == 1 and '"I_NEW"' in asked[0]


def test_an_item_made_long_ago_is_not_read_by_id_again(tmp_path, monkeypatch):
    """Missing from the board after minutes means removed, not unlisted."""
    config, store, asked = pulling(tmp_path, monkeypatch,
                                   {"I_NEW": "2020-01-01T00:00:00+00:00"})
    sync.pull(config, store)
    assert store.state().item(9) is None and asked == []
    assert store.state().created == {}
