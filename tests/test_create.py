import re

import pytest

from boardkit.config import Config, ProjectField
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
# the number every other command addresses an item by. These stub GitHub and
# watch what is sent and what is done with the answers.
REPO_TYPES = [{"id": "IT_TASK", "name": "Task"}, {"id": "IT_BUG", "name": "Bug"}]


class FakeGitHub:
    """Answers the lookup, create, add and field documents `add` sends."""

    def __init__(self):
        self.documents: list[str] = []
        self.refuse: set[str] = set()       # create aliases to refuse
        self.parents = {7: "I_PARENT7"}
        self.labels = {"dev": "LA_DEV", "bug": "LA_BUG"}
        self.milestones = {"Cadrage": "MI_CADRAGE"}
        self.users = {"octo": "U_OCTO"}
        self.issues: dict[str, dict] = {}   # node id -> what the create made
        self.stale: set[str] = set()        # ids GitHub no longer resolves

    def content(self, node: str) -> dict:
        made = self.issues[node]
        return {"id": node, "number": made["number"], "title": made["title"],
                "body": made["body"],
                "url": f"https://github.com/octo/widgets/issues/{made['number']}",
                "state": "OPEN", "parent": {"number": made["parent"]} if made["parent"] else None}

    def __call__(self, document: str) -> dict:
        self.documents.append(document)
        if document.startswith("{ repository"):
            repo: dict = {"id": "R_REPO", "issueTypes": {"nodes": REPO_TYPES}}
            for alias, name in re.findall(r'(l\d+): label\(name: "([^"]*)"\)', document):
                repo[alias] = {"id": self.labels[name], "name": name} if name in self.labels else None
            for alias, name in re.findall(r'(u\d+): assignableUsers\(first: 20, query: "([^"]*)"\)', document):
                repo[alias] = {"nodes": [{"id": self.users[name], "login": name}]
                               if name in self.users else []}
            for alias, name in re.findall(r'(m\d+): milestones\(first: 20, query: "([^"]*)"', document):
                repo[alias] = {"nodes": [{"id": self.milestones[name], "title": name}]
                               if name in self.milestones else []}
            errors = []
            for alias, number in re.findall(r"(p\d+): issue\(number: (\d+)\)", document):
                found = self.parents.get(int(number))
                repo[alias] = {"id": found} if found else None
                if not found:
                    errors.append({"path": ["repository", alias],
                                   "message": "Could not resolve to an Issue"})
            return {"data": {"repository": repo,
                             "owner": {"projectV2": {"id": "PVT_BOARD"}}},
                    **({"errors": errors} if errors else {})}
        if document.startswith("mutation { c0"):
            data, errors = {}, []
            numbers = {made["number"] for made in self.issues.values()}
            for chunk in re.split(r"(?=c\d+: createIssue)", document)[1:]:
                alias = chunk.split(":")[0]
                if alias in self.refuse or any(f'"{one}"' in chunk for one in self.stale):
                    data[alias] = None
                    why = ("Could not resolve to a node with the global id of 'x'"
                           if alias not in self.refuse else "title is too long")
                    errors.append({"path": [alias], "message": why})
                    continue
                number = 42
                while number in numbers:
                    number += 1
                numbers.add(number)
                title = re.search(r'title: "((?:[^"\\]|\\.)*)"', chunk).group(1)
                parent = re.search(r'parentIssueId: "([^"]*)"', chunk)
                body = re.search(r'body: "((?:[^"\\]|\\.)*)"', chunk).group(1)
                self.issues[f"I_{number}"] = {
                    "number": number, "title": title, "body": body,
                    "parent": next((n for n, i in self.parents.items()
                                    if parent and i == parent.group(1)), 0)}
                data[alias] = {"issue": {
                    "id": f"I_{number}", "number": number,
                    "url": f"https://github.com/octo/widgets/issues/{number}"}}
            return {"data": data, **({"errors": errors} if errors else {})}
        if "mutation { a0" in document:
            return {"data": {alias: {"item": {
                        "id": "PVTI_" + content.removeprefix("I_"), "type": "ISSUE",
                        "content": self.content(content), "fieldValues": {"nodes": []}}}
                    for alias, content in re.findall(
                        r'(a\d+): addProjectV2ItemById\(input: \{ projectId: "[^"]*", '
                        r'contentId: "([^"]*)"', document)}}
        raise AssertionError(f"unexpected document: {document[:80]}")

    def creates(self) -> list[str]:
        return [d for d in self.documents if d.startswith("mutation { c0")]

    def lookups(self) -> list[str]:
        return [d for d in self.documents if d.startswith("{ repository")]


@pytest.fixture
def made(tmp_path, monkeypatch):
    """A repo whose GitHub calls are answered here rather than sent."""
    from boardkit import create, sync

    calls: list[tuple] = []
    config = Config(root=tmp_path, owner="octo", project="4", repo="octo/widgets")
    store = Store(config)
    store.save_state(Snapshot(synced_at="2026-10-03T00:00:00+00:00",
                              fields=(ProjectField(name="status", id="F_STATUS"),)))
    github = FakeGitHub()

    def graphql(document):
        calls.append(("graphql", document))
        return github(document)

    def gh_json(*args, **kwargs):
        # Drafts are still made by `gh project item-create`.
        calls.append(("gh_json", args))
        return {"id": "I_NEW123456"}

    def apply_and_read(config, writes, snapshot):
        # The field writes answer with each item, its fields as written.
        calls.append(("fields", tuple(writes)))
        rows = {}
        for write in writes:
            item = snapshot.item(write.ref)
            node = f"I_{write.ref}"
            if node not in github.issues:
                continue
            rows.setdefault(item.id, {"id": item.id, "content": {
                **github.content(node), "type": "ISSUE"}})[write.field] = write.value
        return list(writes), list(rows.values())

    def refresh(config, store, ids):
        calls.append(("refresh", tuple(ids)))
        return False

    monkeypatch.setattr(create, "graphql_partial", graphql)
    monkeypatch.setattr(create, "gh_json", gh_json)
    monkeypatch.setattr(sync, "pull", lambda c, s: calls.append(("pull", ())))
    monkeypatch.setattr(sync, "refresh", refresh)
    monkeypatch.setattr(sync, "apply_and_read", apply_and_read)
    store.github = github
    return config, store, calls


def sent(calls) -> list[str]:
    return [args for kind, args in calls if kind == "graphql"]


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


def test_the_issue_number_is_read_back_from_what_github_answered(made):
    from boardkit.create import add
    config, store, _ = made
    add(config, store, "A real thing", "with a plan", {})
    assert [item.number for item in store.state().items] == [42]
    assert store.state().items[0].id == "PVTI_42"


def test_a_fully_fielded_issue_takes_three_calls_once_ids_are_known(made):
    """Labels, type and parent were a call or three apiece; the create now
    takes them all. The first add looks the ids up; the next does not, and
    nothing is read back: each call answers with what the snapshot needs."""
    from boardkit.create import add
    config, store, calls = made
    add(config, store, "A real thing", "", {"type": "Task", "parent": "7",
                                            "labels": "dev", "status": "Inbox"})
    assert [kind for kind, _ in calls] == ["graphql", "graphql", "graphql", "fields"]
    calls.clear()
    add(config, store, "Another", "", {"type": "Task", "parent": "7",
                                       "labels": "dev", "status": "Inbox"})
    assert [kind for kind, _ in calls] == ["graphql", "graphql", "fields"]
    assert len(store.github.lookups()) == 1


def test_an_issue_is_created_on_the_board_by_graphql_not_rest_or_gh_issue(made):
    """`gh issue create` reads the default branch for templates, which needs
    Contents; a board's app has Issues only. `createIssue` needs no more."""
    from boardkit.create import add
    config, store, calls = made
    add(config, store, "A real thing", "with a plan", {})
    create = store.github.creates()[0]
    assert 'repositoryId: "R_REPO"' in create
    assert 'title: "A real thing"' in create and 'body: "with a plan"' in create
    assert not any(kind == "gh_json" for kind, _ in calls)


def test_a_title_with_quotes_is_escaped_in_the_document(made):
    from boardkit.create import add
    config, store, _ = made
    add(config, store, 'Say "hi"', "line\nbreak", {})
    create = store.github.creates()[0]
    assert r'title: "Say \"hi\""' in create and r'body: "line\nbreak"' in create


def test_fields_are_set_from_the_id_the_create_returned_not_after_a_pull(made):
    """A pull straight after creating does not reliably see the new item —
    GitHub takes a moment to index it — and the writes were being rejected
    for an item that was plainly there. So the fields go first."""
    from boardkit.create import add
    config, store, calls = made
    add(config, store, "A real thing", "", {"status": "Inbox"})
    order = [kind for kind, _ in calls]
    assert "fields" in order and "pull" not in order and "refresh" not in order
    assert store.state().item("42").field("status") == "Inbox"


def test_an_issue_the_pull_did_not_return_is_still_readable(made):
    """`pull` is stubbed to return nothing, which is exactly the case this
    guards: the item is on the board and absent from the snapshot."""
    from boardkit.create import add
    config, store, _ = made
    add(config, store, "A real thing", "the body", {"status": "Inbox"})
    item = store.state().item("42")
    assert item is not None
    assert item.body == "the body" and item.field("status") == "Inbox"


def test_a_new_item_is_recorded_as_created_for_the_next_pulls(made):
    from boardkit.create import add
    config, store, _ = made
    add(config, store, "A real thing", "", {})
    assert "PVTI_42" in store.state().created


def test_new_issues_are_put_on_the_board_by_id_not_by_the_create(made):
    """GitHub adds an item asked for by `projectV2Ids` behind its answer, so
    the answer names no item, and adding it again is refused as already
    there. Adding by id answers with the item, every card in one document."""
    from boardkit.create import add_from
    config, store, calls = made
    path = store.directory.parent / "cards.json"
    path.write_text('[{"title": "One"}, {"title": "Two"}]')
    add_from(config, store, str(path))
    assert "projectV2Ids" not in store.github.creates()[0]
    (adding,) = [d for d in sent(calls) if "mutation { a0" in d]
    assert adding.count("addProjectV2ItemById") == 2
    assert 'projectId: "PVT_BOARD"' in adding and '"I_43"' in adding
    assert sorted(item.id for item in store.state().items) == ["PVTI_42", "PVTI_43"]


def test_an_issue_the_board_already_holds_is_found_not_dropped(made, monkeypatch):
    """A project's workflow may put a new sub-issue on the board before we
    ask, and the add is then refused as already there: the item is read."""
    from boardkit import create
    config, store, _ = made
    fake = store.github

    def already(document):
        if "mutation { a0" in document:
            return {"data": {"a0": None}, "errors": [
                {"path": ["a0"], "message": "Content already exists in this project"}]}
        if document.startswith("{ n0"):
            return {"data": {"n0": {"projectItems": {"nodes": [
                {"id": "PVTI_OTHER", "project": {"id": "PVT_ELSEWHERE"}},
                {"id": "PVTI_THERE", "project": {"id": "PVT_BOARD"}}]}}}}
        return fake(document)

    monkeypatch.setattr(create, "graphql_partial", already)
    create.add(config, store, "A child", "", {"parent": "7", "status": "Todo"})
    item = store.state().item("42")
    assert item.id == "PVTI_THERE" and item.field("status") == "Todo"


def test_issues_the_board_refused_are_still_reported_as_created(made, capsys,
                                                                monkeypatch):
    from boardkit import create
    config, store, _ = made
    fake = store.github

    def refuse_board(document):
        if "mutation { a0" in document:
            raise BoardError("no permission on the project")
        return fake(document)

    monkeypatch.setattr(create, "graphql_partial", refuse_board)
    assert create.add(config, store, "A thing", "", {"status": "Inbox"}) == 0
    out = capsys.readouterr().out
    assert "Created https://github.com/octo/widgets/issues/42" in out
    assert "not put on the board" in out and "`board set` on it" in out


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


def test_the_board_config_names_wins_over_the_snapshot(made):
    """#119: a snapshot kept from the repository's old board said project 9,
    config named 4, and the issue went to the 9 of the new owner, another
    team's board. The snapshot is a cache of config's board, never which one."""
    from boardkit.create import add
    config, store, calls = made
    store.save_state(Snapshot(synced_at="2026-10-03T00:00:00+00:00",
                              project_number="9"))
    add(config, store, "A thing", "", {})
    assert "projectV2(number: 4)" in sent(calls)[0]
    assert "number: 9" not in sent(calls)[0]


def test_a_board_named_by_title_uses_the_number_the_snapshot_found(made):
    """Config may name the project by title rather than number, and the
    snapshot is where the looked-up number is kept."""
    from boardkit.create import add
    config, store, calls = made
    store.save_state(Snapshot(synced_at="2026-10-03T00:00:00+00:00",
                              project_number="9"))
    add(config.model_copy(update={"project": ""}), store, "A thing", "", {})
    assert "projectV2(number: 9)" in sent(calls)[0]


def test_a_created_issue_is_not_lost_when_its_fields_are_refused(made, capsys,
                                                                monkeypatch):
    """The issue exists on GitHub by then. Reporting the refusal and keeping
    it is the only honest outcome; raising would strand a real issue."""
    from boardkit import sync
    from boardkit.create import add
    config, store, _ = made

    def refuse(writes, snapshot):
        raise BoardError("'Nope' is not an option of status")

    monkeypatch.setattr(sync, "apply_and_read", lambda c, w, s: refuse(w, s))
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


def test_a_new_issue_is_created_in_its_milestone_not_as_a_board_field(made):
    from boardkit.create import add
    config, store, calls = made
    add(config, store, "A phase task", "", {"milestone": "Cadrage"})
    assert 'milestoneId: "MI_CADRAGE"' in store.github.creates()[0]
    assert not any(kind == "fields" for kind, _ in calls)


def test_an_unknown_milestone_stops_the_create_before_anything_is_made(made):
    from boardkit import create
    config, store, _ = made
    with pytest.raises(BoardError, match=r"no milestone 'Nope'.*nothing was created"):
        create.add(config, store, "A phase task", "", {"milestone": "Nope"})
    assert store.github.creates() == []


def test_labels_and_assignees_are_given_to_the_create_not_to_the_board(made):
    """`labels=dev` used to be looked up as a project field, which no board
    has, so the issue was made and its fields were all dropped."""
    from boardkit.create import add
    config, store, calls = made
    add(config, store, "A dev task", "", {"labels": "dev, bug", "assignees": "octo"})
    create = store.github.creates()[0]
    assert 'labelIds: ["LA_DEV", "LA_BUG"]' in create
    assert 'assigneeIds: ["U_OCTO"]' in create
    assert not any(kind == "fields" for kind, _ in calls)


def test_an_unknown_label_stops_the_create_rather_than_being_made_up(made):
    """A typo must not quietly become a new label on a real issue."""
    from boardkit.create import add
    config, store, _ = made
    with pytest.raises(BoardError, match="no label 'dve'"):
        add(config, store, "A dev task", "", {"labels": "dve"})
    assert store.github.creates() == []


def test_the_type_and_parent_are_given_to_the_create(made, capsys):
    from boardkit.create import add
    config, store, _ = made
    add(config, store, "A child", "", {"type": "task", "parent": "#7"})
    create = store.github.creates()[0]
    assert 'issueTypeId: "IT_TASK"' in create and 'parentIssueId: "I_PARENT7"' in create
    out = capsys.readouterr().out
    assert "#42 is a task" in out and "#42 is a sub-issue of #7" in out
    assert store.state().item("42").parent == 7


def test_an_unknown_type_stops_the_create_naming_the_ones_there_are(made):
    """It used to be set after the create, so a typo left an untyped issue."""
    from boardkit.create import add
    config, store, _ = made
    with pytest.raises(BoardError, match=r"no issue type 'Chore' \(it has: Bug, Task\)"):
        add(config, store, "A thing", "", {"type": "Chore"})
    assert store.github.creates() == []


def test_a_parent_that_does_not_exist_stops_the_create(made):
    from boardkit.create import add
    config, store, _ = made
    with pytest.raises(BoardError, match="no issue #99 to be a parent"):
        add(config, store, "A child", "", {"parent": "99"})
    assert store.github.creates() == []


def test_many_cards_are_one_lookup_one_create_and_one_field_write(made, tmp_path):
    import json

    from boardkit.create import add_from
    config, store, calls = made
    cards = tmp_path / "cards.json"
    cards.write_text(json.dumps([
        {"title": f"Piece {n}", "type": "Task", "parent": 7, "labels": ["dev"],
         "status": "Inbox"} for n in range(3)]))
    assert add_from(config, store, str(cards)) == 0
    assert [kind for kind, _ in calls] == ["graphql", "graphql", "graphql", "fields"]
    assert store.github.creates()[0].count("createIssue") == 3
    fields = next(args for kind, args in calls if kind == "fields")
    assert store.state().item("44").field("status") == "Inbox"
    assert [(w.ref, w.field, w.value) for w in fields] == [
        ("42", "status", "Inbox"), ("43", "status", "Inbox"), ("44", "status", "Inbox")]
    assert sorted(item.number for item in store.state().items) == [42, 43, 44]
    assert all(item.parent == 7 for item in store.state().items)


def test_a_card_refused_in_a_batch_leaves_the_others_made(made, tmp_path, capsys):
    """The creates run in order and do not roll back: what was made is kept
    and said, and what was not is named with GitHub's reason."""
    import json

    from boardkit.create import add_from
    config, store, _ = made
    store.github.refuse = {"c1"}
    cards = tmp_path / "cards.json"
    cards.write_text(json.dumps([{"title": "One"}, {"title": "Two"}, {"title": "Three"}]))
    assert add_from(config, store, str(cards)) == 1
    out = capsys.readouterr().out
    assert "Not created: 'Two': title is too long" in out
    assert sorted(item.title for item in store.state().items) == ["One", "Three"]


def test_a_batch_without_titles_is_refused_before_the_network(made, tmp_path):
    import json

    from boardkit.create import add_from
    config, store, calls = made
    cards = tmp_path / "cards.json"
    cards.write_text(json.dumps([{"title": "One"}, {"body": "no title"}]))
    with pytest.raises(BoardError, match="needs a title"):
        add_from(config, store, str(cards))
    assert calls == []


def test_a_batch_that_is_not_a_list_says_what_it_wants(made, tmp_path):
    from boardkit.create import add_from
    config, store, _ = made
    cards = tmp_path / "cards.json"
    cards.write_text('{"title": "One"}')
    with pytest.raises(BoardError, match="list of objects"):
        add_from(config, store, str(cards))


REMEMBER_MANY = """
import sys
from boardkit.config import Config
from boardkit.create import remember
from boardkit.store import Store
store = Store(Config(root=sys.argv[1], owner="o", repo="o/p"))
for n in range(5):
    remember(store, f"I_{sys.argv[2]}_{n}", int(sys.argv[2]) * 10 + n + 1,
             "card", "", {"status": "Inbox"})
"""


def test_parallel_adds_all_land_in_the_snapshot(tmp_path):
    """#96: several `board add` at once each read the snapshot, added their
    item and wrote it back, and the last writer dropped the others' items."""
    import subprocess
    import sys

    store = Store(Config(root=tmp_path, owner="o", repo="o/p"))
    store.save_state(Snapshot(synced_at="2026-10-03T00:00:00+00:00"))
    running = [subprocess.Popen([sys.executable, "-c", REMEMBER_MANY,
                                 str(tmp_path), str(worker)])
               for worker in range(8)]
    assert all(process.wait(timeout=60) == 0 for process in running)
    assert len(store.state().items) == 40


def test_a_kept_id_gone_stale_is_looked_up_again_and_the_create_retried(made, capsys):
    """A label renamed or deleted since it was kept: GitHub refuses its id,
    so the ids are looked up afresh and the card created once more."""
    from boardkit.create import add
    config, store, _ = made
    add(config, store, "First", "", {"labels": "dev"})
    store.github.labels["dev"] = "LA_DEV_NEW"
    store.github.stale = {"LA_DEV"}
    assert add(config, store, "Second", "", {"labels": "dev"}) == 0
    assert "Created https://github.com/octo/widgets/issues/43" in capsys.readouterr().out
    assert len(store.github.lookups()) == 2
    assert 'labelIds: ["LA_DEV_NEW"]' in store.github.creates()[-1]
    assert store.state().ids["labels"]["dev"] == "LA_DEV_NEW"


def test_a_parent_in_the_snapshot_needs_no_lookup(made):
    from boardkit.board import ProjectItem
    from boardkit.create import add
    config, store, _ = made
    add(config, store, "Warm the ids", "", {})
    store.save_state(store.state().model_copy(update={"items": (
        *store.state().items, ProjectItem(number=9, id="PVTI_9", node="I_NINE"))}))
    store.github.parents[9] = "I_NINE"
    add(config, store, "A child", "", {"parent": "9"})
    assert len(store.github.lookups()) == 1
    assert 'parentIssueId: "I_NINE"' in store.github.creates()[-1]


def test_a_new_card_starts_in_the_first_column_unless_told_otherwise(tmp_path):
    """GitHub's default workflow put every new item in Todo, so an idea
    nobody had looked at read as ready work."""
    from boardkit.config import Config
    from boardkit.create import in_intake
    config = Config(root=tmp_path, owner="o", project="1", repo="o/r",
                    status_order=("Inbox", "Todo", "Done"))
    assert in_intake(config, {}) == {"status": "Inbox"}
    assert in_intake(config, {"Status": "Todo"}) == {"Status": "Todo"}
    assert in_intake(config, {"area": "Core"}) == {"area": "Core", "status": "Inbox"}
    bare = Config(root=tmp_path, owner="o", project="1", repo="o/r")
    assert in_intake(bare, {}) == {}
