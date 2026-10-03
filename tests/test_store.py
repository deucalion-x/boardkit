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


def test_a_refusal_is_recorded_with_the_budget_and_github_s_own_words(tmp_path):
    """Our paraphrase is what made today's limit hard to diagnose."""
    import subprocess

    from boardkit import gh as plumbing
    store(tmp_path)          # constructing the store names the trail

    def limited(args):
        if args[:3] == ["gh", "api", "graphql"]:
            return subprocess.CompletedProcess(
                args, 0, "HTTP/2.0 200\nX-Ratelimit-Remaining: 9\n"
                         f"X-Ratelimit-Reset: {int(plumbing.time.time()) + 60}\n\n{{}}", "")
        return subprocess.CompletedProcess(args, 1, "", "API rate limit exceeded for user")

    original = plumbing.once
    plumbing.once = limited
    try:
        plumbing.run(["gh", "project", "view"])
    except plumbing.RateLimited as refused:
        assert "GitHub said: API rate limit exceeded for user" in str(refused)
    finally:
        plumbing.once = original

    written = (tmp_path / ".boardkit" / "log.jsonl").read_text()
    assert '"remaining": 9' in written
    assert "API rate limit exceeded" in written


def test_the_trail_rotates_rather_than_growing_without_bound(tmp_path):
    from boardkit import trail
    store(tmp_path)
    trail.MAX_BYTES, original = 2048, trail.MAX_BYTES
    try:
        for index in range(400):
            trail.note("queued", number=index, field="status", value="x" * 40)
    finally:
        trail.MAX_BYTES = original
    here = tmp_path / ".boardkit"
    assert (here / "log.jsonl").stat().st_size < 8192
    assert (here / "log.jsonl.1").exists()


def test_a_second_process_is_refused_rather_than_interleaved(tmp_path):
    """Agents are the intended caller, so two at once is the normal case."""
    from boardkit.store import Busy
    kept = store(tmp_path)
    with kept.held():
        try:
            with kept.held():
                raise AssertionError("the lock let a second holder in")
        except Busy as busy:
            assert "another boardkit process" in str(busy)


def test_the_lock_is_released_even_when_the_work_fails(tmp_path):
    kept = store(tmp_path)
    try:
        with kept.held():
            raise ValueError("the work went wrong")
    except ValueError:
        pass
    with kept.held():
        pass          # free again


def test_a_lock_left_by_a_dead_process_is_broken(tmp_path):
    import os
    import time

    from boardkit.store import directory
    kept = store(tmp_path)
    lock = directory(kept.config) / "lock"
    lock.write_text("999999")
    old = time.time() - (16 * 60)
    os.utime(lock, (old, old))
    with kept.held():
        pass


def test_recording_the_same_rejection_twice_changes_nothing(tmp_path):
    kept = seeded(tmp_path)
    write = kept.queue(7, "status", "In progress")
    kept.reject(write, remote="Blocked", reason="moved")
    kept.reject(write, remote="Blocked", reason="moved")
    assert len(kept.rejected()) == 1


def test_setting_a_field_twice_queues_one_change_not_two(tmp_path):
    kept = seeded(tmp_path)
    kept.queue(7, "status", "In progress")
    second = kept.queue(7, "status", "Done")
    assert [(w.field, w.value) for w in kept.pending()] == [("status", "Done")]
    # The original value GitHub showed us is what a conflict is judged against.
    assert second.was == "Ready"


def test_a_second_field_on_the_same_item_is_its_own_change(tmp_path):
    kept = seeded(tmp_path)
    kept.queue(7, "status", "Done")
    kept.queue(7, "workstream", "Infra")
    assert len(kept.pending()) == 2
