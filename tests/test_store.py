import json
import pathlib

from boardkit.board import ProjectItem
from boardkit.config import Config, ProjectField
from boardkit.store import Snapshot, Store, Write


def store(tmp_path: pathlib.Path) -> Store:
    return Store(Config(root=tmp_path, owner="octo", project="4",
                        repo="octo/widgets", fields=("workstream",)))


def seeded(tmp_path: pathlib.Path) -> Store:
    kept = store(tmp_path)
    kept.save_state(Snapshot(
        project_id="PVT_1", synced_at="2026-10-03T00:00:00+00:00",
        fields=(ProjectField(name="workstream", id="F_1",
                             option_ids={"Maps": "O_1"}),),
        items=(ProjectItem(number=7, id="I_7", title="Corridor",
                           fields={"status": "Ready", "workstream": "Maps"}),)))
    return kept


def test_nothing_exists_until_something_is_written(tmp_path):
    kept = store(tmp_path)
    assert kept.state().empty and kept.pending() == [] and kept.rejected() == []
    assert not (tmp_path / ".boardkit").exists()


def test_a_snapshot_round_trips_with_the_ids_a_push_needs(tmp_path):
    read_back = seeded(tmp_path).state()
    assert read_back.project_id == "PVT_1" and not read_back.empty
    assert read_back.item(7).id == "I_7"
    assert read_back.field("workstream").option_ids == {"Maps": "O_1"}


def test_a_queued_write_remembers_what_it_was_decided_against(tmp_path):
    kept = seeded(tmp_path)
    write = kept.queue(7, "status", "In progress")
    assert (write.field, write.value, write.was) == ("status", "In progress", "Ready")
    assert [w.id for w in kept.pending()] == [write.id]


def test_a_write_against_an_unknown_item_records_no_prior_value(tmp_path):
    assert store(tmp_path).queue(99, "status", "Done").was == ""


def test_a_rejection_keeps_both_values(tmp_path):
    kept = seeded(tmp_path)
    write = kept.queue(7, "status", "In progress")
    kept.reject(write, remote="Blocked")
    rejection = kept.rejected()[0]
    assert (rejection.write.value, rejection.remote) == ("In progress", "Blocked")
    assert kept.clear_rejected() == 1 and kept.rejected() == []


def test_a_write_is_replaced_atomically_and_leaves_no_temporary(tmp_path):
    kept = seeded(tmp_path)
    kept.save_pending([Write(number=1, field="status", value="Done")])
    assert list((tmp_path / ".boardkit").glob("*.tmp")) == []
    assert json.loads((tmp_path / ".boardkit" / "pending.json").read_text())[0]["number"] == 1


def test_the_cache_excludes_itself_from_git(tmp_path):
    assert store(tmp_path).ignore_file().read_text() == "*\n"
