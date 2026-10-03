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
                             option_ids={"Ready": "O_R", "In progress": "O_P"}),
                ProjectField(name="workstream", id="F_W", option_ids={"Maps": "O_M"})),
        items=(ProjectItem(number=7, id="I_7", title="Corridor",
                           fields={"status": "Ready", "workstream": "Maps"}),
               ProjectItem(number=8, id="I_8", title="Later",
                           fields={"status": "Done", "workstream": "Maps"}))))
    return store


def test_an_unknown_verb_prints_usage_without_querying_github(repo, capsys):
    assert cli.main(["badverb"]) == 2
    assert "board find <terms>" in capsys.readouterr().out


def test_item_without_a_number_prints_usage_rather_than_crashing(repo):
    assert cli.main(["item"]) == 2
    assert cli.main(["item", "not-a-number"]) == 2


def test_help_exits_clean(repo):
    assert cli.main(["--help"]) == 0


def test_a_missing_config_is_an_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ready"]) == 1
    assert "no boardkit configuration" in capsys.readouterr().err


def test_a_read_with_no_snapshot_says_to_sync_rather_than_fetching(repo, capsys):
    assert cli.main(["rollup"]) == 1
    assert "Run `board sync`" in capsys.readouterr().out


def test_the_rollup_answers_from_the_snapshot(seeded, capsys):
    assert cli.main(["rollup"]) == 0
    out = capsys.readouterr().out
    assert "Maps" in out and "Ready=1" in out and "Done=1" in out


def test_ready_takes_the_first_configured_column(seeded, capsys):
    assert cli.main(["ready"]) == 0
    out = capsys.readouterr().out
    assert "#7" in out and "#8" not in out


def test_json_stays_flat_for_a_jq_pipeline(seeded, capsys):
    assert cli.main(["json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["number"] == 7 and rows[0]["status"] == "Ready"


def test_setting_a_field_queues_it_rather_than_pushing(seeded, capsys):
    assert cli.main(["set", "7", "status", "In progress"]) == 0
    assert "Queued" in capsys.readouterr().out
    pending = seeded.pending()
    assert (pending[0].field, pending[0].value, pending[0].was) == (
        "status", "In progress", "Ready")


def test_setting_an_item_that_is_not_on_the_board_is_refused(seeded, capsys):
    assert cli.main(["set", "99", "status", "Done"]) == 1
    assert "not on the board" in capsys.readouterr().out


def test_the_rollup_mentions_queued_work_so_it_is_not_forgotten(seeded, capsys):
    cli.main(["set", "7", "status", "In progress"])
    capsys.readouterr()
    cli.main(["rollup"])
    assert "1 change(s) queued" in capsys.readouterr().out


def test_sync_status_needs_no_network(seeded, capsys):
    assert cli.main(["sync", "status"]) == 0
    assert "2 items" in capsys.readouterr().out
