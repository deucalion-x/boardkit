import json
import subprocess

import pytest
from test_cli import aged, repo, seeded  # noqa: F401  (fixtures)

from boardkit import cli, gh, quota
from boardkit.store import now

HEADERS = ("HTTP/2.0 200 OK\nContent-Type: application/json\n"
           "X-Ratelimit-Remaining: 4990\nX-Ratelimit-Resource: {resource}\n"
           "X-Ratelimit-Used: 10\n\n")


@pytest.fixture
def usage(tmp_path):
    quota.configure(tmp_path / quota.NAME)
    quota.begin("sync")
    return tmp_path / quota.NAME


def totals(usage) -> dict:
    return json.loads(usage.read_text(encoding="utf-8"))[quota.today()]


# Pricing a query --------------------------------------------------------

def test_a_query_is_asked_its_price():
    assert gh.priced("{ viewer { login } }") == (
        "{ " + gh.COST + " viewer { login } }")


def test_the_price_goes_in_the_operation_not_a_fragment_before_it():
    document = "fragment f on X { name } { node { ...f } }"
    priced = gh.priced(document)
    assert priced.startswith("fragment f on X { name } { " + gh.COST)


def test_a_named_query_with_variables_is_priced():
    priced = gh.priced("query Q($n: Int) { node(n: $n) { id } }")
    assert priced == "query Q($n: Int) { " + gh.COST + " node(n: $n) { id } }"


def test_a_mutation_is_never_priced_because_github_would_refuse_it():
    document = 'mutation { addComment(input: {body: "{ a }"}) { clientMutationId } }'
    assert gh.priced(document) == document


def test_braces_in_a_string_do_not_move_the_price():
    document = 'fragment f on X { name(q: "} {\\" }") } { node { id } }'
    assert gh.priced(document).endswith("{ " + gh.COST + " node { id } }")


def test_only_gh_api_is_instrumented_and_a_probe_is_left_alone():
    assert gh.instrumented(["gh", "issue", "list"]) == ["gh", "issue", "list"]
    assert gh.instrumented(gh.PROBE) is gh.PROBE
    sent = gh.instrumented(["gh", "api", "graphql", "-f", "query={ a }"])
    assert sent[:3] == ["gh", "api", "--include"]
    assert gh.COST in sent[-1]


# Accounting for a call ---------------------------------------------------

def test_the_caller_sees_the_body_it_asked_for_and_the_cost_is_recorded(
        usage, monkeypatch):
    body = {"data": {gh.COST_ALIAS: {"cost": 3}, "viewer": {"login": "a"}}}
    sent = []

    def answered(args, **kw):
        sent.append(args)
        return subprocess.CompletedProcess(
            args, 0, HEADERS.format(resource="graphql") + json.dumps(body), "")

    monkeypatch.setattr(subprocess, "run", answered)
    monkeypatch.setattr(gh, "environment", lambda args: None)
    got = gh.run_json(["gh", "api", "graphql", "-f", "query={ viewer { login } }"])
    assert got == {"data": {"viewer": {"login": "a"}}}
    assert "--include" in sent[0]
    day = totals(usage)
    assert (day["calls"], day["points"], day["unpriced"]) == (1, 3, 0)
    assert day["lowest"] == {"graphql": 4990}
    assert day["verbs"]["sync"]["points"] == 3


def test_a_rest_call_costs_one_point(usage):
    result = subprocess.CompletedProcess(
        [], 0, HEADERS.format(resource="core") + "[]", "")
    gh.account(["gh", "api", "repos/o/r"], result, stripped=True)
    assert result.stdout == "[]"
    assert (totals(usage)["points"], totals(usage)["unpriced"]) == (1, 0)


def test_a_mutation_and_a_gh_subcommand_are_counted_as_unpriced(usage):
    mutation = subprocess.CompletedProcess(
        [], 0, HEADERS.format(resource="graphql") + '{"data": {}}', "")
    gh.account(["gh", "api", "graphql"], mutation, stripped=True)
    gh.account(["gh", "issue", "comment"],
               subprocess.CompletedProcess([], 0, "ok", ""), stripped=False)
    day = totals(usage)
    assert (day["calls"], day["points"], day["unpriced"]) == (2, 0, 2)


def test_a_failed_call_still_hands_back_its_error_body(usage):
    result = subprocess.CompletedProcess(
        [], 1, HEADERS.format(resource="graphql") + '{"message": "nope"}', "gh: nope")
    gh.account(["gh", "api", "graphql"], result, stripped=True)
    assert result.stdout == '{"message": "nope"}'
    assert totals(usage)["calls"] == 1


# Reads, and the record ----------------------------------------------------

def test_reads_are_counted_by_how_they_were_answered(seeded, aged,  # noqa: F811
                                                      monkeypatch, capsys):
    assert cli.main(["list"]) == 0                  # pulls: the snapshot was old
    old = seeded.state()
    seeded.save_state(old.model_copy(update={"synced_at": now()}))
    assert cli.main(["list"]) == 0                  # fresh: local
    assert cli.main(["item", "7", "--offline"]) == 0

    def refused(config, store):
        raise gh.Unreachable("no network")

    monkeypatch.setattr(cli.sync, "pull", refused)
    seeded.save_state(old)
    assert cli.main(["list"]) == 0                  # cannot pull: stale
    reads = json.loads(quota._path.read_text())[quota.today()]["reads"]
    assert reads == {"local": 2, "refreshed": 1, "stale": 1}


def test_a_read_with_no_snapshot_counts_nothing(repo, capsys):  # noqa: F811
    cli.main(["list"])
    assert not (repo / ".boardkit" / quota.NAME).exists()


def test_turning_the_log_off_turns_the_totals_off(tmp_path, monkeypatch):
    monkeypatch.setenv("BOARDKIT_LOG", "off")
    quota.configure(tmp_path / quota.NAME)
    quota.read("local")
    quota.call("gh api graphql", "graphql", 2, 4000)
    assert not (tmp_path / quota.NAME).exists()


def test_only_the_last_days_are_kept(usage):
    usage.write_text(json.dumps({f"2020-01-{d:02d}": {} for d in range(1, 32)}
                                | {f"2020-02-{d:02d}": {} for d in range(1, 29)}
                                | {f"2020-03-{d:02d}": {} for d in range(1, 32)}))
    quota.read("local")
    kept = json.loads(usage.read_text())
    assert len(kept) == quota.DAYS
    assert quota.today() in kept and "2020-01-01" not in kept


def test_a_corrupt_file_starts_again_rather_than_failing(usage):
    usage.write_text("{not json")
    quota.read("local")
    assert totals(usage)["reads"]["local"] == 1


def test_the_report_says_how_many_reads_never_reached_github(usage, capsys):
    for _ in range(3):
        quota.read("local")
    quota.read("refreshed")
    quota.call("gh api graphql", "graphql", 2, 4980)
    quota.begin("set")
    quota.call("gh api graphql", "graphql", None, 4979)
    assert quota.report() == 0
    out = capsys.readouterr().out
    assert "3 of 4 reads (75%) answered without calling GitHub." in out
    assert "graphql 4979" in out
    assert "sync" in out and "set" in out


def test_the_report_with_nothing_recorded_says_so(usage, capsys):
    assert quota.report() == 0
    assert "No usage recorded yet." in capsys.readouterr().out


def test_log_usage_reaches_the_report(seeded, capsys):  # noqa: F811
    assert cli.main(["log", "--usage"]) == 0
    assert "No usage recorded yet." in capsys.readouterr().out


def test_gh_auth_is_local_and_not_counted(usage):
    gh.account(["gh", "auth", "status"],
               subprocess.CompletedProcess([], 0, "Logged in", ""), stripped=False)
    assert not usage.exists()
