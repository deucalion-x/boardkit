from boardkit.config import Config
from boardkit.create import parse
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
