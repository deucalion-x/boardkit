import pytest

from boardkit.config import Config
from boardkit.create import parse
from boardkit.gh import BoardError
from boardkit.store import Snapshot, Store


def test_a_title_is_whatever_is_not_a_flag_or_a_field():
    title, body, values, _ = parse(["Short", "place", "names"])
    assert title == "Short place names" and body == "" and values == {}


def test_fields_are_named_rather_than_flagged():
    """Which fields exist is the board's business, not the parser's."""
    title, _, values, _ = parse(["Fix", "it", "status=Todo", "area=sync"])
    assert title == "Fix it" and values == {"status": "Todo", "area": "sync"}


def test_the_body_is_taken_whole():
    title, body, _, _ = parse(["Fix", "it", "--body", "Done when: it works"])
    assert title == "Fix it" and body == "Done when: it works"


def test_a_multi_select_value_keeps_its_commas():
    _, _, values, _ = parse(["T", "area=config, docs"])
    assert values == {"area": "config, docs"}


def test_a_draft_is_asked_for_by_flag_and_is_not_part_of_the_title():
    title, _, _, draft = parse(["--draft", "Try", "a", "sticky", "note"])
    assert draft and title == "Try a sticky note"


def test_without_the_flag_an_issue_is_what_gets_made():
    assert parse(["Real", "work"])[3] is False


def test_an_item_the_pull_missed_is_remembered(tmp_path):
    """GitHub takes a moment to index a new item, so the pull that follows
    creating one does not reliably return it."""
    from boardkit.create import remember
    store = Store(Config(root=tmp_path, owner="o", repo="o/p"))
    store.save_state(Snapshot(synced_at="2026-10-03T00:00:00+00:00"))
    remember(store, "I_NEW", 0, "A thought", "", {"status": "Inbox"})
    items = store.state().items
    assert [i.title for i in items] == ["A thought"]
    assert items[0].field("status") == "Inbox"


def test_an_item_the_pull_did_see_is_not_added_twice(tmp_path):
    from boardkit.board import ProjectItem
    from boardkit.create import remember
    store = Store(Config(root=tmp_path, owner="o", repo="o/p"))
    store.save_state(Snapshot(synced_at="2026-10-03T00:00:00+00:00",
                              items=(ProjectItem(id="I_NEW", title="A thought"),)))
    remember(store, "I_NEW", 0, "A thought", "", {})
    assert len(store.state().items) == 1


# Creating is the one verb that must reach the network: only GitHub can mint
# the number every other command addresses an item by. These stub the three
# calls it makes and watch what it does with the answers.
@pytest.fixture
def made(tmp_path, monkeypatch):
    """A repo whose `gh` calls are recorded rather than sent."""
    from boardkit import create, sync

    calls: list[tuple] = []
    config = Config(root=tmp_path, owner="octo", project="4", repo="octo/widgets")
    store = Store(config)
    store.save_state(Snapshot(synced_at="2026-10-03T00:00:00+00:00"))

    def gh(*args, **kwargs):
        calls.append(("gh", args))
        return "https://github.com/octo/widgets/issues/42\n"

    def gh_json(*args, **kwargs):
        calls.append(("gh_json", args))
        return {"id": "I_NEW123456"}

    monkeypatch.setattr(create, "gh", gh)
    monkeypatch.setattr(create, "gh_json", gh_json)
    monkeypatch.setattr(sync, "pull", lambda c, s: calls.append(("pull", ())))
    monkeypatch.setattr(sync, "refresh", lambda c, s, ids: False)
    monkeypatch.setattr(sync, "apply_all", lambda writes, snap: list(writes))
    return config, store, calls


def test_an_issue_without_a_title_is_refused_before_the_network(made):
    from boardkit.create import add
    config, store, calls = made
    with pytest.raises(BoardError, match="needs a title"):
        add(config, store, "   ", "", {})
    assert calls == []


def test_a_draft_without_a_title_is_refused_before_the_network(made):
    from boardkit.create import add_draft
    config, store, calls = made
    with pytest.raises(BoardError, match="needs a title"):
        add_draft(config, store, "", "", {})
    assert calls == []


def test_the_issue_number_is_read_back_out_of_the_url_github_printed(made):
    from boardkit.create import add
    config, store, _ = made
    add(config, store, "A real thing", "with a plan", {})
    assert [item.number for item in store.state().items] == [42]


def test_an_issue_is_created_then_added_to_the_board(made):
    from boardkit.create import add
    config, store, calls = made
    add(config, store, "A real thing", "", {})
    verbs = [args[0] for kind, args in calls if kind.startswith("gh")]
    assert verbs == ["issue", "project"]


def test_fields_are_set_from_the_id_item_add_returned_not_after_a_pull(made):
    """A pull straight after creating does not reliably see the new item —
    GitHub takes a moment to index it — and the writes were being rejected
    for an item that was plainly there. So the fields go first."""
    from boardkit.create import add
    config, store, calls = made
    add(config, store, "A real thing", "", {"status": "Inbox"})
    order = [kind for kind, _ in calls]
    assert order.index("pull") == len(order) - 1


def test_an_issue_the_pull_did_not_return_is_still_readable(made):
    """`pull` is stubbed to return nothing, which is exactly the case this
    guards: the item is on the board and absent from the snapshot."""
    from boardkit.create import add
    config, store, _ = made
    add(config, store, "A real thing", "the body", {"status": "Inbox"})
    item = store.state().item("42")
    assert item is not None
    assert item.body == "the body" and item.field("status") == "Inbox"


def test_a_draft_prints_the_handle_because_nothing_else_knows_it(made, capsys):
    """A draft has no number, so without this the only way to address the
    card just created was to list the whole board again."""
    from boardkit.create import add_draft
    config, store, _ = made
    add_draft(config, store, "A jotted thought", "", {})
    assert "~123456" in capsys.readouterr().out


def test_a_draft_is_remembered_as_a_draft_not_an_issue(made):
    from boardkit.create import add_draft
    config, store, _ = made
    add_draft(config, store, "A jotted thought", "", {})
    item = store.state().item("~123456")
    assert item is not None and item.draft and item.number == 0


def test_the_project_number_comes_from_the_snapshot_when_it_has_one(tmp_path,
                                                                   monkeypatch):
    """Config may name the project by title rather than number, and the
    snapshot is where the looked-up number is kept."""
    from boardkit import create, sync
    seen: list[str] = []
    config = Config(root=tmp_path, owner="octo", repo="octo/widgets",
                    project_name="widgets")
    store = Store(config)
    store.save_state(Snapshot(synced_at="2026-10-03T00:00:00+00:00",
                              project_number="9"))
    monkeypatch.setattr(create, "gh",
                        lambda *a, **k: "https://github.com/octo/widgets/issues/42\n")
    monkeypatch.setattr(create, "gh_json",
                        lambda *a, **k: (seen.append(a[2]), {"id": "I_X"})[1])
    monkeypatch.setattr(sync, "pull", lambda c, s: None)
    monkeypatch.setattr(sync, "refresh", lambda c, s, ids: False)
    create.add(config, store, "A thing", "", {})
    assert seen == ["9"]


def test_a_created_issue_is_not_lost_when_its_fields_are_refused(made, capsys,
                                                                monkeypatch):
    """The issue exists on GitHub by then. Reporting the refusal and keeping
    it is the only honest outcome; raising would strand a real issue."""
    from boardkit import sync
    from boardkit.create import add
    config, store, _ = made

    def refuse(writes, snapshot):
        raise BoardError("'Nope' is not an option of status")

    monkeypatch.setattr(sync, "apply_all", refuse)
    assert add(config, store, "A real thing", "", {"status": "Nope"}) == 0
    out = capsys.readouterr().out
    assert "the issue was created; its fields were not" in out
    assert store.state().item("42") is not None


def test_a_board_that_returns_no_id_for_the_new_item_says_so(made):
    """Without an id there is nothing to write a field against, and a silent
    skip would read as the fields having been set."""
    from boardkit.create import place
    config, store, _ = made
    with pytest.raises(BoardError, match="did not return an id"):
        place(config, store, 42, "", "A thing", {"status": "Inbox"})
