"""Reads must answer from the local store. Nothing but sync may touch GitHub."""
import json

import pytest

from boardkit import cli, gh
from boardkit.board import ProjectItem
from boardkit.config import Config, ProjectField
from boardkit.store import Snapshot, Store

TABLE = ('[tool.boardkit]\nowner = "octo"\nproject = "4"\nrepo = "octo/widgets"\n'
         'fields = ["workstream"]\nstatus_order = ["Ready", "In progress", "Done"]\n')


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(TABLE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Any subprocess at all is a failure for a verb that claims to be local.
    monkeypatch.setattr(gh, "run", lambda args: pytest.fail(f"ran {args[0]}"))
    return tmp_path


@pytest.fixture
def seeded(repo):
    store = Store(Config(root=repo, owner="octo", project="4", repo="octo/widgets",
                         fields=("workstream",)))
    store.save_state(Snapshot(
        project_id="PVT_1", synced_at="2026-10-03T00:00:00+00:00",
        fields=(ProjectField(name="status", id="F_S",
                             options=("Inbox", "Todo", "In progress", "Done"),
                             option_ids={"Todo": "O_R", "In progress": "O_P"}),
                ProjectField(name="workstream", id="F_W", option_ids={"Maps": "O_M"})),
        items=(ProjectItem(number=7, id="I_7", title="Corridor",
                           fields={"status": "Todo", "workstream": "Maps"}),
               ProjectItem(number=8, id="I_8", title="Later",
                           fields={"status": "Done", "workstream": "Maps"}),
               ProjectItem(id="I_D1", title="A jotted thought", type="DRAFT_ISSUE",
                           fields={"status": "Inbox", "workstream": "Maps"}))))
    return store


def test_an_unknown_verb_prints_usage_without_querying_github(repo, capsys):
    assert cli.main(["badverb"]) == 2
    assert "board find <terms>" in capsys.readouterr().out


def test_item_without_a_reference_prints_usage_rather_than_crashing(repo):
    """A reference is an issue number or a draft handle, so it is not checked
    for being digits — only for being there."""
    assert cli.main(["item"]) == 2


def test_help_exits_clean(repo):
    assert cli.main(["--help"]) == 0


def test_a_missing_config_is_an_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ready"]) == 1
    assert "no boardkit configuration" in capsys.readouterr().err


def test_a_read_with_no_snapshot_says_to_sync_rather_than_fetching(repo, capsys):
    assert cli.main(["list"]) == 1
    assert "Run `board sync`" in capsys.readouterr().out


def test_the_listing_answers_from_the_snapshot(seeded, capsys):
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "Maps" in out and "#7" in out


def test_json_stays_flat_for_a_jq_pipeline(seeded, capsys):
    assert cli.main(["json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["number"] == 7 and rows[0]["status"] == "Todo"


def test_later_queues_without_reaching_for_the_network(seeded, capsys):
    assert cli.main(["set", "7", "status", "In progress", "--later"]) == 0
    assert "Queued" in capsys.readouterr().out
    pending = seeded.pending()
    assert (pending[0].field, pending[0].value, pending[0].was) == (
        "status", "In progress", "Todo")


def test_a_change_is_pushed_when_it_is_made(seeded, monkeypatch, capsys):
    """The queue is durability, not a schedule: a change goes now."""
    from boardkit import sync
    sent = []
    monkeypatch.setattr(sync, "push_now",
                        lambda config, store: (sent.append(1), (1, 0, ""))[1])
    assert cli.main(["set", "7", "status", "In progress"]) == 0
    assert sent == [1]


def test_a_change_that_cannot_be_sent_is_kept_and_says_why(seeded, monkeypatch, capsys):
    from boardkit import sync
    monkeypatch.setattr(sync, "push_now", lambda config, store: (0, 0, "offline"))
    assert cli.main(["set", "7", "status", "In progress"]) == 0
    out = capsys.readouterr().out
    assert "offline" in out and "board sync push" in out
    assert len(seeded.pending()) == 1


def test_setting_an_item_that_is_not_on_the_board_is_refused(seeded, capsys):
    assert cli.main(["set", "99", "status", "Done"]) == 1
    assert "not on the board" in capsys.readouterr().out


def test_the_rollup_mentions_queued_work_so_it_is_not_forgotten(seeded, capsys):
    cli.main(["set", "7", "status", "In progress", "--later"])
    capsys.readouterr()
    cli.main(["list"])
    assert "1 change(s) queued" in capsys.readouterr().out


def test_sync_status_needs_no_network(seeded, capsys):
    assert cli.main(["sync", "status"]) == 0
    assert "3 items" in capsys.readouterr().out


def test_init_shows_how_to_declare_a_board_to_create(tmp_path, monkeypatch):
    """A new repo should not have to find the shape of these tables in the
    docs; it should not have to use them either, hence commented."""
    from boardkit.setup import rendered
    text = rendered({"owner": "octo", "repo": "octo/places", "project_name": "P"})
    assert "# [board.fields.area]" in text
    import tomllib
    board = tomllib.loads(text)["board"]
    assert board["status_order"][:2] == ["Inbox", "Todo"]
    assert [v["name"] for v in board["views"]] == ["Open work", "Everything"]


def test_init_goes_all_the_way_to_a_board(tmp_path, monkeypatch):
    """Writing config and stopping would only ask for one more command."""
    from boardkit import setup
    done = []
    monkeypatch.setattr(setup, "detect", lambda: {
        "owner": "octo", "owner_type": "user", "repo": "octo/places",
        "project_name": "Places"})
    monkeypatch.setattr(setup, "board_exists", lambda settings: False)
    monkeypatch.setattr(setup, "root", lambda: tmp_path)
    import boardkit.admin, boardkit.sync
    monkeypatch.setattr(boardkit.admin, "apply", lambda config, dry=False: done.append("apply"))
    monkeypatch.setattr(boardkit.sync, "pull", lambda config, store: done.append("pull"))
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    setup.init()
    assert done == ["apply", "pull"]
    assert "[tool.boardkit.board]" in (tmp_path / "pyproject.toml").read_text()


def test_the_starter_config_is_read_back_wherever_it_is_written(tmp_path):
    """In pyproject every table has to sit under [tool.boardkit]; a bare
    [board] there is a different table that nothing reads."""
    from boardkit.config import load
    from boardkit.setup import rendered
    settings = {"owner": "octo", "repo": "octo/places", "project_name": "Places"}

    own = tmp_path / "own"
    own.mkdir()
    (own / "boardkit.toml").write_text(rendered(settings), encoding="utf-8")
    assert load(own).repo == "octo/places"

    packaged = tmp_path / "packaged"
    packaged.mkdir()
    (packaged / "pyproject.toml").write_text(
        rendered(settings, nested=True), encoding="utf-8")
    assert load(packaged).repo == "octo/places"
    assert load(packaged).status_order == (
        "Inbox", "Todo", "In progress", "In review", "Done")
    assert [v.name for v in load(packaged).views] == ["Open work", "Everything"]


def test_the_starter_states_the_board_it_would_build(tmp_path):
    """Stated rather than commented: a board that exists ignores it, and a new
    one is built from it."""
    from boardkit.config import load
    from boardkit.setup import rendered
    settings = {"owner": "octo", "repo": "octo/places", "project_name": "Places"}
    (tmp_path / "boardkit.toml").write_text(rendered(settings), encoding="utf-8")
    assert load(tmp_path).status_order[0] == "Inbox"


def test_views_lists_the_tabs_the_board_has(seeded, capsys):
    """Declared in config and invisible from the CLI until now."""
    state = seeded.state()
    seeded.save_state(state.model_copy(update={"views": ("Open work", "Everything")}))
    assert cli.main(["views"]) == 0
    assert capsys.readouterr().out.split() == ["Open", "work", "Everything"]


def test_an_item_with_no_description_says_so(seeded, capsys):
    """Silence read as the skill having lied about showing the full text.
    A draft, because looking up an issue's children needs the network."""
    assert cli.main(["item", "~I_D1"]) == 0
    assert "(no description)" in capsys.readouterr().out
