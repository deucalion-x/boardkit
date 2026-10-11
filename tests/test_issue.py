"""Changing the issue behind an item, under the repo's own credential."""

import pytest

from boardkit import issue, sync
from boardkit.board import ProjectItem
from boardkit.config import Config
from boardkit.gh import BoardError
from boardkit.store import Snapshot, Store, now


@pytest.fixture
def world(tmp_path, monkeypatch):
    config = Config(root=tmp_path, owner="octo", repo="octo/widgets", project="4")
    store = Store(config)
    store.save_state(Snapshot(project_id="PVT_1", synced_at=now(), items=(
        ProjectItem(number=7, id="I_7", title="Open", labels=("bug", "ui")),
        ProjectItem(id="I_D", title="Draft", type="DRAFT_ISSUE")),
        closed=(ProjectItem(number=3, id="I_3", title="Old", closed=True),)))
    sent = []
    monkeypatch.setattr(issue, "gh", lambda *args: sent.append(args) or "")
    monkeypatch.setattr(sync, "pull", lambda config, store: None)
    return config, store, sent


def test_comment_goes_to_the_issue(world):
    config, store, sent = world
    issue.comment(config, store, "#7", "Verified: tests pass.")
    assert sent == [("issue", "comment", "7", "--repo", "octo/widgets",
                     "--body", "Verified: tests pass.")]


def test_close_not_planned_names_the_reason(world):
    config, store, sent = world
    issue.close(config, store, "7", "not-planned")
    assert sent[0][-2:] == ("--reason", "not planned")


def test_a_closed_item_can_be_reopened(world):
    config, store, sent = world
    issue.reopen(config, store, "3")
    assert sent[0][:3] == ("issue", "reopen", "3")


def test_labels_become_exactly_the_list_given(world):
    config, store, sent = world
    issue.set_meta(config, store, "7", "labels", "bug, backend")
    assert sent[0] == ("issue", "edit", "7", "--repo", "octo/widgets",
                       "--add-label", "backend", "--remove-label", "ui")


def test_a_draft_has_no_issue_to_change(world):
    config, store, _ = world
    with pytest.raises(BoardError, match="board promote"):
        issue.comment(config, store, "~" + ProjectItem(id="I_D").ref.lstrip("~"), "x")


def test_edit_needs_something_to_change(world):
    config, store, _ = world
    with pytest.raises(BoardError, match="--title, --body"):
        issue.edit(config, store, "7", None, None)


def test_set_routes_issue_metadata_away_from_board_fields(world, monkeypatch):
    from boardkit import cli
    config, _store, _ = world
    routed = []
    monkeypatch.setattr(issue, "set_meta", lambda *a: routed.append(a[2:]) or 0)
    monkeypatch.chdir(config.root)
    (config.root / "pyproject.toml").write_text(
        '[tool.boardkit]\nowner = "octo"\nrepo = "octo/widgets"\nproject = "4"\n')
    assert cli.main(["set", "7", "assignees", "ada"]) == 0
    assert routed == [("7", "assignees", "ada")]




def test_offline_the_change_is_kept_and_the_next_sync_sends_it(world, monkeypatch, capsys):
    from boardkit.gh import Unreachable
    config, store, sent = world

    def offline(*args):
        raise Unreachable("no route")
    monkeypatch.setattr(issue, "gh", offline)
    issue.comment(config, store, "7", "Result: done.")
    assert "kept, offline" in capsys.readouterr().out
    assert [a.kind for a in store.actions()] == ["comment"]

    monkeypatch.setattr(issue, "gh", lambda *args: sent.append(args) or "")
    assert issue.send(config, store) == (1, "")
    assert store.actions() == [] and sent[0][:3] == ("issue", "comment", "7")


def test_a_refused_change_is_dropped_with_the_reason(world, monkeypatch, capsys):
    config, store, _ = world

    def refused(*args):
        raise BoardError("HTTP 422: label does not exist")
    monkeypatch.setattr(issue, "gh", refused)
    issue.close(config, store, "7", "completed")
    assert store.actions() == []
    assert "refused" in capsys.readouterr().out
