"""Which board a write goes to (#119): the config's, and only when it is the
repository's. These run the real check; every other test stubs it."""
import pytest

from boardkit import create, target
from boardkit.config import Config
from boardkit.gh import BoardError
from boardkit.store import Snapshot, Store

pytestmark = pytest.mark.link_check


@pytest.fixture
def repo(tmp_path):
    config = Config(root=tmp_path, owner="novatix-team", owner_type="org", project="25",
                    repo="novatix-team/governance",
                    project_url="https://github.com/orgs/novatix-team/projects/25")
    return config, Store(config)


def linked(monkeypatch, *boards):
    """The repository's linked projects, as GitHub lists them; counts calls."""
    asked = []

    def answer(query):
        asked.append(query)
        return {"data": {"repository": {"projectsV2": {"nodes": [
            {"id": project_id, "number": number, "owner": {"login": owner},
             "url": f"https://github.com/orgs/{owner}/projects/{number}"}
            for owner, number, project_id in boards]}}}}

    monkeypatch.setattr(target, "graphql_partial", answer)
    return asked


def test_a_snapshot_of_the_old_owners_board_is_discarded(repo, capsys):
    """The repository moved from a user to an organisation. Its snapshot still
    named the user's project 1, and the organisation has a project 1 too."""
    config, store = repo
    store.save_state(Snapshot(project_id="PVT_user1", project_number="1",
                              owner="antoine", repo="novatix-team/governance",
                              ids={"project": {"antoine/1": "PVT_user1"}}))
    target.forget_other(config, store)
    assert store.state() == Snapshot()
    said = capsys.readouterr().err
    assert "owner antoine, not novatix-team" in said and "project 1, not 25" in said


def test_an_older_snapshot_is_known_by_its_url(repo):
    config, store = repo
    store.save_state(Snapshot(project_id="PVT_user1", project_number="25",
                              project_url="https://github.com/users/antoine/projects/25"))
    target.forget_other(config, store)
    assert store.state() == Snapshot()


def test_a_snapshot_of_this_board_is_kept(repo, capsys):
    config, store = repo
    kept = Snapshot(project_id="PVT_25", project_number="25", owner="novatix-team",
                    repo="novatix-team/governance")
    store.save_state(kept)
    target.forget_other(config, store)
    assert store.state() == kept and capsys.readouterr().err == ""


def test_a_linked_board_is_looked_up_once(repo, monkeypatch):
    config, store = repo
    asked = linked(monkeypatch, ("novatix-team", 25, "PVT_25"))
    assert target.check(config, store) == "PVT_25"
    assert target.check(config, store, "PVT_25") == "PVT_25"
    assert len(asked) == 1


def test_the_same_number_under_another_owner_is_not_this_board(repo, monkeypatch):
    config, store = repo
    linked(monkeypatch, ("antoine", 25, "PVT_user25"))
    with pytest.raises(BoardError, match="is not linked to novatix-team/governance"):
        target.check(config, store)


def test_an_unlinked_board_is_refused_with_the_command_that_links_it(repo, monkeypatch):
    config, store = repo
    linked(monkeypatch, ("novatix-team", 1, "PVT_other"))
    with pytest.raises(BoardError) as refused:
        target.check(config, store)
    said = str(refused.value)
    assert "board admin link --as-me" in said and "Nothing was written" in said
    assert "projects/1" in said
    assert store.state().linked == ""


def test_a_write_carrying_another_boards_id_is_refused(repo, monkeypatch):
    config, store = repo
    linked(monkeypatch, ("novatix-team", 25, "PVT_25"))
    with pytest.raises(BoardError, match="PVT_other"):
        target.check(config, store, "PVT_other")


def test_a_new_board_in_config_is_checked_again(repo, monkeypatch):
    config, store = repo
    asked = linked(monkeypatch, ("novatix-team", 25, "PVT_25"), ("novatix-team", 26, "PVT_26"))
    target.check(config, store)
    assert target.check(config.model_copy(update={"project": "26"}), store) == "PVT_26"
    assert len(asked) == 2


def test_no_issue_is_created_for_a_board_that_is_not_the_repositorys(repo, monkeypatch):
    """The board `board add` resolved is not the linked one: refused before the
    issue is made, so nothing lands anywhere."""
    config, store = repo
    linked(monkeypatch, ("novatix-team", 25, "PVT_25"))
    monkeypatch.setattr(create, "lookup", lambda *a, **k: {"project": "PVT_other"})
    made = []
    monkeypatch.setattr(create, "create_all", lambda *a: made.append(a) or ([], []))
    with pytest.raises(BoardError, match="refused"):
        create.add_many(config, store, [create.Asked(title="A thing")])
    assert made == []
