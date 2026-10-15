"""Reads must answer from the local store. Nothing but sync may touch GitHub."""
import json
import sys

import pytest

from boardkit import cli, gh
from boardkit.board import ProjectItem
from boardkit.config import Config, ProjectField
from boardkit.store import Snapshot, Store, now

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
        # Stamped now, not with a fixed date: a read refreshes a snapshot
        # older than the floor, and these tests assert that reads are local.
        project_id="PVT_1", synced_at=now(),
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


def test_version_needs_no_configuration(tmp_path, monkeypatch, capsys):
    """Checking what a tag installed should not depend on where you stand."""
    from boardkit import __version__
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == f"boardkit {__version__}\n"


@pytest.mark.parametrize("argv", [["install-skill", "--help"], ["admin", "apply", "-h"],
                                  ["sync", "--help"]])
def test_a_verb_asked_for_help_prints_it_rather_than_running(repo, monkeypatch, capsys, argv):
    from boardkit import admin, skill
    monkeypatch.setattr(skill, "install", lambda *a, **k: pytest.fail("installed"))
    monkeypatch.setattr(admin, "apply", lambda *a, **k: pytest.fail("applied"))
    assert cli.main(argv) == 0
    assert "board install-skill" in capsys.readouterr().out


def test_help_as_body_text_is_not_a_flag(repo, monkeypatch):
    from boardkit import create
    called = []
    monkeypatch.setattr(create, "add", lambda *a: called.append(a) or 0)
    assert cli.main(["add", "Title", "--body", "--help"]) == 0
    assert called


def test_admin_reconcile_flag_reaches_apply(repo, monkeypatch):
    from boardkit import admin
    called = []
    monkeypatch.setattr(admin, "apply",
                        lambda config, dry=False, reconcile=False, prune=False:
                        called.append((dry, reconcile, prune)) or 0)
    assert cli.main(["admin", "apply", "-n", "--reconcile"]) == 0
    assert called == [(True, True, False)]


def test_prune_views_flag_reaches_apply_and_is_off_by_default(repo, monkeypatch):
    """Deleting a tab is opt-in per invocation, never implied by --reconcile."""
    from boardkit import admin
    called = []
    monkeypatch.setattr(admin, "apply",
                        lambda config, dry=False, reconcile=False, prune=False:
                        called.append(prune) or 0)
    assert cli.main(["admin", "apply", "--reconcile"]) == 0
    assert cli.main(["admin", "apply", "--prune-views"]) == 0
    assert called == [False, True]


def test_a_missing_config_is_an_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["list"]) == 1
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
    assert "# [board.fields.workstream]" in text
    import tomllib
    board = tomllib.loads(text)["board"]
    assert board["status_order"][:2] == ["Inbox", "Todo"]
    # The views are the work preset's, named rather than restated.
    assert board["preset"] == "work"
    assert "views" not in board
    (tmp_path / "boardkit.toml").write_text(text, encoding="utf-8")
    from boardkit.config import load
    views = load(tmp_path).views
    # The work without the roadmap pieces; everything.
    assert [(v.name, v.layout, v.filter) for v in views] == [
        ("Current", "BOARD", "-type:Initiative"), ("All", "TABLE", "")]


def test_init_goes_all_the_way_to_a_board(tmp_path, monkeypatch):
    """Writing config and stopping would only ask for one more command."""
    from boardkit import setup
    done = []
    monkeypatch.setattr(setup, "detect", lambda: {
        "owner": "octo", "owner_type": "user", "repo": "octo/places",
        "project_name": "Places"})
    monkeypatch.setattr(setup, "board_exists", lambda settings: False)
    monkeypatch.setattr(setup, "root", lambda: tmp_path)
    import boardkit.admin
    import boardkit.sync
    monkeypatch.setattr(boardkit.admin, "apply", lambda config, dry=False, **_: done.append("apply"))
    monkeypatch.setattr(boardkit.sync, "pull", lambda config, store: done.append("pull"))
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    setup.init()
    assert done == ["apply", "pull"]
    assert "[tool.boardkit.board]" in (tmp_path / "pyproject.toml").read_text()


def test_init_names_a_shared_app_beside_the_repo(tmp_path):
    from boardkit.config import load
    from boardkit.setup import rendered
    settings = {"owner": "octo", "owner_type": "org", "repo": "octo/places",
                "project_name": "Places", "app_client_id": "Iv23liABCDEF12345678"}
    for nested, name in ((False, "boardkit.toml"), (True, "pyproject.toml")):
        where = tmp_path / name
        where.mkdir()
        (where / name).write_text(rendered(settings, nested=nested), encoding="utf-8")
        assert load(where).app_client_id == "Iv23liABCDEF12345678"
    assert "app_client_id" not in rendered({**settings, "app_client_id": ""})


def test_init_refuses_what_is_not_a_client_id_before_writing(tmp_path, monkeypatch):
    from boardkit import setup
    from boardkit.gh import BoardError
    monkeypatch.setattr(setup, "root", lambda: tmp_path)
    for wrong in ("ghp_secret0123456789", 'Iv23li"; x = "', "Iv1"):
        with pytest.raises(BoardError, match="not a GitHub App client ID"):
            setup.init(app=wrong)
    assert not (tmp_path / "boardkit.toml").exists()


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
        "Inbox", "Todo", "In Progress", "In Review", "Done")
    assert [v.name for v in load(packaged).views] == ["Current", "All"]


def test_an_organisations_repo_starts_an_organisations_board(tmp_path, monkeypatch):
    """`gh repo view` names the owner but not its kind; without asking, every
    org board was configured as a personal one."""
    from boardkit import setup
    from boardkit.config import load
    answers = {("repo", "view"): {"nameWithOwner": "octo/places", "name": "places",
                                  "owner": {"id": "O_1", "login": "octo"}},
               ("api", "users/octo"): {"login": "octo", "type": "Organization"}}
    monkeypatch.setattr(setup, "gh_json", lambda *args: answers[args[:2]])
    settings = setup.detect()
    assert settings["owner_type"] == "org"
    (tmp_path / "boardkit.toml").write_text(setup.rendered(settings), encoding="utf-8")
    assert load(tmp_path).owner_type == "org"


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


def test_a_refused_value_never_reaches_the_queue(seeded, capsys):
    """It would stay there forever: a refusal on push is not a rejection, so
    `board rejected --clear` does not touch it and every later push retries."""
    assert cli.main(["set", "7", "status", "Nonsense"]) == 1
    assert "not an option" in capsys.readouterr().err
    assert seeded.pending() == []


def test_a_field_can_be_cleared(seeded, monkeypatch, capsys):
    from boardkit import sync
    monkeypatch.setattr(sync, "push_now", lambda config, store: (1, 0, ""))
    assert cli.main(["set", "7", "workstream", ""]) == 0
    assert "workstream = " in capsys.readouterr().out


def test_children_are_listed_from_the_snapshot_without_asking_github(seeded,
                                                                     capsys):
    """Each item carries its parent, so the child list is a pass over the
    snapshot. It was a search per invocation: the one read that always spent
    quota, and the one the read-age window could not bound because there was
    no stored value whose age it could check. The `repo` fixture fails any
    subprocess, so reaching GitHub here would fail this test."""
    state = seeded.state()
    seeded.save_state(state.model_copy(update={"items": (
        *(item for item in state.items if item.ref != "8"),
        state.item("8").model_copy(update={"parent": 7}))}))
    assert cli.main(["item", "7"]) == 0
    printed = capsys.readouterr().out
    assert "Corridor" in printed
    assert "children:" in printed
    assert "#8" in printed


def test_an_item_with_no_children_says_nothing_about_them(seeded, capsys):
    assert cli.main(["item", "7"]) == 0
    assert "children:" not in capsys.readouterr().out


def test_a_draft_has_no_children_and_costs_no_lookup(seeded, capsys):
    """Nothing can name a draft as its parent: it has no issue number."""
    draft = next(item for item in seeded.state().items if item.draft)
    assert cli.main(["item", draft.ref]) == 0
    assert "children:" not in capsys.readouterr().out


# --- A read may not answer from a snapshot of any age -----------------------

@pytest.fixture
def aged(seeded, monkeypatch):
    """The seeded board, stamped old enough that a read must refresh it."""
    state = seeded.state()
    seeded.save_state(state.model_copy(update={
        "synced_at": "2026-10-03T00:00:00+00:00"}))
    pulls = []

    def pulled(config, store):
        pulls.append(config.repo)
        return state.model_copy(update={"synced_at": now()})

    monkeypatch.setattr(cli.auth, "require", lambda config, tier: None)
    monkeypatch.setattr(cli.sync, "pull", pulled)
    return pulls


def test_a_read_refreshes_a_snapshot_older_than_the_floor(aged, capsys):
    assert cli.main(["list"]) == 0
    assert aged == ["octo/widgets"], "the read should have pulled first"
    assert "#7" in capsys.readouterr().out


def test_a_fresh_snapshot_is_answered_from_without_asking_github(seeded, aged,
                                                                 capsys):
    seeded.save_state(seeded.state().model_copy(update={"synced_at": now()}))
    assert cli.main(["list"]) == 0
    assert aged == [], "a snapshot inside the floor must not pull"


def test_offline_answers_from_the_snapshot_whatever_its_age(aged, capsys):
    assert cli.main(["list", "--offline"]) == 0
    assert aged == []
    assert "#7" in capsys.readouterr().out


def test_offline_is_not_taken_for_a_search_term(aged, capsys):
    assert cli.main(["find", "--offline", "Corridor"]) == 0
    assert aged == []
    assert "#7" in capsys.readouterr().out


def test_a_floor_of_zero_never_syncs_on_read(aged, repo, capsys):
    (repo / "pyproject.toml").write_text(
        TABLE + "max_read_age_seconds = 0\n", encoding="utf-8")
    assert cli.main(["list"]) == 0
    assert aged == []


def test_a_read_that_cannot_refresh_still_answers_and_says_so(aged, monkeypatch,
                                                              capsys):
    def refused(config, store):
        raise gh.Unreachable("no network")

    monkeypatch.setattr(cli.sync, "pull", refused)
    assert cli.main(["list"]) == 0
    caught = capsys.readouterr()
    assert "#7" in caught.out, "the question is answered from disk"
    assert "could not refresh" in caught.err


def test_no_snapshot_at_all_is_not_staleness(repo, monkeypatch, capsys):
    """A first read says to sync rather than fetching one unasked."""
    monkeypatch.setattr(cli.sync, "pull",
                        lambda c, s: pytest.fail("pulled behind the caller"))
    assert cli.main(["list"]) == 1
    assert "Run `board sync`" in capsys.readouterr().out


# --- The refresh is the retry for a queue an offline write left behind ------

@pytest.fixture
def stranded(seeded, aged, monkeypatch):
    """An aged snapshot with one write still queued, and a push that records
    its calls rather than making them."""
    seeded.queue("7", "status", "In progress")
    pushes = []
    monkeypatch.setattr(cli.sync, "push_now",
                        lambda config, store: pushes.append("pushed") or (1, 0, ""))
    return pushes


def test_a_refreshing_read_pushes_the_queue_first(stranded, aged, capsys):
    assert cli.main(["list"]) == 0
    assert stranded == ["pushed"], "the window elapsing is the retry"
    assert aged == ["octo/widgets"], "and the pull still happens"
    assert "pushed 1 queued change" in capsys.readouterr().err


def test_a_read_inside_the_window_does_not_push(seeded, stranded, capsys):
    seeded.save_state(seeded.state().model_copy(update={"synced_at": now()}))
    assert cli.main(["list"]) == 0
    assert stranded == [], "no refresh, no retry"


def test_an_offline_read_does_not_push(stranded, capsys):
    assert cli.main(["list", "--offline"]) == 0
    assert stranded == []


def test_an_empty_queue_costs_no_push_attempt(seeded, aged, monkeypatch):
    monkeypatch.setattr(cli.sync, "push_now",
                        lambda config, store: pytest.fail("pushed nothing"))
    assert cli.main(["list"]) == 0
    assert aged == ["octo/widgets"]


def test_a_read_only_token_still_reads_with_a_queue_waiting(seeded, aged,
                                                            monkeypatch, capsys):
    """The write scope is missing. That must cost the push, not the read."""
    def scoped(config, tier):
        if tier == "write":
            raise gh.BoardError("the `write` tier needs scope(s) project")
        return None

    seeded.queue("7", "status", "In progress")
    monkeypatch.setattr(cli.auth, "require", scoped)
    monkeypatch.setattr(cli.sync, "push_now",
                        lambda config, store: pytest.fail("pushed unscoped"))
    assert cli.main(["list"]) == 0
    assert "#7" in capsys.readouterr().out


def test_a_push_that_cannot_reach_github_still_leaves_the_read_answered(
        seeded, aged, monkeypatch, capsys):
    seeded.queue("7", "status", "In progress")
    monkeypatch.setattr(cli.sync, "push_now",
                        lambda config, store: (0, 0, "offline"))
    assert cli.main(["list"]) == 0
    assert aged == ["octo/widgets"]
    assert "#7" in capsys.readouterr().out


def test_a_rejection_from_the_retry_is_named_on_stderr(seeded, aged, monkeypatch,
                                                       capsys):
    seeded.queue("7", "status", "In progress")
    monkeypatch.setattr(cli.sync, "push_now",
                        lambda config, store: (0, 1, ""))
    assert cli.main(["list"]) == 0
    assert "1 queued change(s) rejected" in capsys.readouterr().err


def test_sync_pull_drains_the_queue_too(seeded, aged, monkeypatch, capsys):
    """An explicit pull is a snapshot sync, and drains on the same terms."""
    pushes = []
    monkeypatch.setattr(cli.sync, "push_now",
                        lambda config, store: pushes.append("pushed") or (1, 0, ""))
    seeded.queue("7", "status", "In progress")
    assert cli.main(["sync", "pull"]) == 0
    assert pushes == ["pushed"]


# --- A queued write is visible to reads before it lands --------------------

def test_a_queued_write_shows_in_reads_and_is_marked_as_queued(seeded, capsys):
    seeded.queue("7", "status", "In progress")
    assert cli.main(["json"]) == 0
    caught = capsys.readouterr()
    rows = {row["ref"]: row for row in json.loads(caught.out)}
    assert rows["7"]["status"] == "In progress", "the agent's own change"
    assert "1 queued change(s) included below" in caught.err


def test_the_stored_snapshot_still_holds_what_github_last_held(seeded, capsys):
    """The fold is for the read. The file is the conflict check's evidence."""
    seeded.queue("7", "status", "In progress")
    assert cli.main(["json"]) == 0
    assert seeded.state().item("7").field("status") == "Todo"


def test_an_empty_queue_says_nothing_and_changes_nothing(seeded, capsys):
    assert cli.main(["json"]) == 0
    caught = capsys.readouterr()
    assert "queued change" not in caught.err
    rows = {row["ref"]: row for row in json.loads(caught.out)}
    assert rows["7"]["status"] == "Todo"


def test_an_offline_read_shows_the_queue_it_cannot_push(seeded, capsys):
    """The case the fold exists for: the queue is full because GitHub is gone."""
    seeded.queue("7", "status", "In progress")
    assert cli.main(["list", "--offline"]) == 0
    assert "In progress" in capsys.readouterr().out


# --- A draft stays in intake unless moving it is what was asked ------------

@pytest.fixture
def intake(seeded, repo, monkeypatch):
    """The seeded board with an order whose first column the field really has,
    and a push that records nothing — the gate is decided before any call."""
    (repo / "pyproject.toml").write_text(
        TABLE.replace('status_order = ["Ready", "In progress", "Done"]',
                      'status_order = ["Inbox", "Todo", "In progress", "Done"]'),
        encoding="utf-8")
    state = seeded.state()
    field = state.fields[0].model_copy(update={
        "option_ids": {"Inbox": "O_I", "Todo": "O_R", "In progress": "O_P",
                       "Done": "O_D"}})
    seeded.save_state(state.model_copy(update={"fields": (field, *state.fields[1:])}))
    monkeypatch.setattr(cli.sync, "push_now", lambda config, store: (1, 0, ""))
    return next(item for item in seeded.state().items if item.draft)


def test_moving_a_draft_out_of_intake_is_refused_with_the_way_forward(intake,
                                                                      capsys):
    """GitHub allows it; the point is that an agent does not do it by default."""
    assert cli.main(["set", intake.ref, "status", "In progress"]) == 1
    said = capsys.readouterr().err
    assert "drafts stay in `Inbox`" in said
    assert f"board promote {intake.ref}" in said, "name the command they wanted"
    assert "--anyway" in said


def test_asked_for_explicitly_the_draft_moves(intake, capsys):
    assert cli.main(["set", intake.ref, "status", "In progress", "--anyway"]) == 0
    assert "In progress" in capsys.readouterr().out


def test_a_draft_may_still_be_set_within_intake(intake, capsys):
    assert cli.main(["set", intake.ref, "status", "Inbox"]) == 0


def test_an_issue_is_not_gated(intake, capsys):
    assert cli.main(["set", "7", "status", "In progress"]) == 0


def test_a_drafts_other_fields_are_not_gated(intake, capsys):
    """The rule is about leaving intake, not about drafts being read-only."""
    assert cli.main(["set", intake.ref, "workstream", "Maps"]) == 0


def test_a_repo_declaring_no_order_gets_no_opinion(intake, repo, capsys):
    """boardkit must not assert one workflow for every consumer of it."""
    (repo / "pyproject.toml").write_text(
        TABLE.replace('status_order = ["Ready", "In progress", "Done"]\n', ""),
        encoding="utf-8")
    assert cli.main(["set", intake.ref, "status", "In progress"]) == 0


# --- A read says which board it answered about ----------------------------

def test_a_listing_names_the_board_it_answered_about(seeded, capsys):
    """Nobody has to ask "which board?" — and no rule has to be remembered."""
    state = seeded.state()
    seeded.save_state(state.model_copy(update={
        "project_url": "https://github.com/users/octo/projects/4"}))
    assert cli.main(["list"]) == 0
    assert "https://github.com/users/octo/projects/4" in capsys.readouterr().out


def test_json_carries_no_header(seeded, capsys):
    """`board json` feeds jq, where a header line is a parse error."""
    state = seeded.state()
    seeded.save_state(state.model_copy(update={
        "project_url": "https://github.com/users/octo/projects/4"}))
    assert cli.main(["json"]) == 0
    json.loads(capsys.readouterr().out)


def test_a_snapshot_without_the_url_prints_nothing(seeded, capsys):
    """Taken before the field existed. No line beats a wrong link."""
    assert cli.main(["list"]) == 0
    assert "https://github.com/users" not in capsys.readouterr().out


@pytest.fixture
def with_closed(seeded):
    seeded.save_state(seeded.state().model_copy(update={"closed": (
        ProjectItem(number=3, id="I_3", title="Corridor survey", closed=True,
                    body="Shipped in March.", fields={"status": "Done"}),)}))
    return seeded


def test_a_closed_item_is_read_from_the_snapshot(with_closed, capsys):
    assert cli.main(["item", "3"]) == 0
    out = capsys.readouterr().out
    assert "Corridor survey" in out and "closed" in out and "Shipped in March." in out


def test_find_answers_was_this_already_done_locally(with_closed, capsys):
    assert cli.main(["find", "corridor"]) == 0
    out = capsys.readouterr().out
    assert out.index("Corridor") < out.index("closed:") < out.index("Corridor survey")


def test_the_listing_still_shows_open_work_only(with_closed, capsys):
    assert cli.main([]) == 0
    assert "Corridor survey" not in capsys.readouterr().out


def test_the_superuser_hatch_is_logged(repo, monkeypatch, capsys):
    import io

    from boardkit import trail
    noted = []
    monkeypatch.setattr(trail, "note", lambda action, **d: noted.append((action, d)))
    monkeypatch.setattr(sys, "stdin", io.StringIO("gh issue view 3 --repo other/repo"))
    assert cli.main(["log", "--record-gh"]) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO("ls -la"))
    assert cli.main(["log", "--record-gh"]) == 0
    assert cli.main(["views", "--as-me"]) in (0, 1)
    supers = [d for action, d in noted if action == "superuser"]
    assert [s["via"] for s in supers] == ["gh", "--as-me"]
    assert supers[0]["command"] == "gh issue view 3 --repo other/repo"


def test_report_answers_from_the_snapshot(seeded, capsys):
    assert cli.main(["report"]) == 0
    assert "No Initiatives" in capsys.readouterr().out
    assert cli.main(["report", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["initiatives"] == []


def test_an_item_that_does_not_exist_says_so(seeded, capsys):
    assert cli.main(["item", "99"]) == 1
    assert "#99 does not exist in octo/widgets" in capsys.readouterr().out


def test_a_closed_card_can_be_moved_to_done(seeded, monkeypatch, capsys):
    """Closing an accepted issue in the browser can leave its card in review.
    It is still a card, so `board set` moves it rather than refusing."""
    from boardkit import sync
    state = seeded.state()
    seeded.save_state(state.model_copy(update={"closed": (
        ProjectItem(number=9, id="I_9", title="Accepted", closed=True,
                    state_reason="COMPLETED",
                    fields={"status": "Todo", "workstream": "Maps"}),)}))
    monkeypatch.setattr(sync, "push_now", lambda config, store: (0, 0, "offline"))
    assert cli.main(["set", "9", "status", "In progress"]) == 0
    [write] = seeded.pending()
    assert (write.ref, write.value, write.was) == ("9", "In progress", "Todo")
    capsys.readouterr()
    assert cli.main(["item", "9", "--offline"]) == 0
    assert capsys.readouterr().out.splitlines()[1].startswith("In progress")


def test_an_issue_off_the_board_is_still_refused(seeded, capsys):
    state = seeded.state()
    seeded.save_state(state.model_copy(update={"elsewhere": (
        ProjectItem(number=12, title="Archived", on_board=False),)}))
    assert cli.main(["set", "12", "status", "Done"]) == 1
    assert "#12 is not on the board" in capsys.readouterr().out
