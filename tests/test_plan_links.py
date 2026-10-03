import pathlib

import pytest

from boardkit.config import Config
from boardkit.plan_links import patterns

CONFIG = Config(root=pathlib.Path("/repo"), owner="octo", project="4",
                repo="octo/places", docs_prefix="docs/")
BARE, LINKED, UNPINNED, BLOB = patterns(CONFIG)


def test_a_bare_path_in_backticks_is_a_candidate():
    assert BARE.findall("see `docs/places/plan.md` for the rule") == [("docs/places/plan.md", "")]


def test_an_anchor_travels_with_the_path():
    assert BARE.findall("`docs/a.md#why`") == [("docs/a.md", "#why")]


def test_an_already_linked_path_is_left_alone():
    assert BARE.findall(f"[`docs/a.md`]({BLOB}/abc1234/docs/a.md)") == []


def test_a_path_outside_the_configured_prefix_is_not_touched():
    assert BARE.findall("`src/places/map.py` and `notes/a.md`") == []


def test_a_sha_pinned_link_is_recognised_for_verification():
    assert LINKED.findall(f"({BLOB}/abc1234def/docs/a.md)") == [("abc1234def", "docs/a.md")]


def test_a_branch_pinned_link_is_reported_rather_than_accepted():
    assert UNPINNED.findall(f"({BLOB}/main/docs/a.md)") == [("main", "docs/a.md")]
    assert UNPINNED.findall(f"({BLOB}/abc1234def/docs/a.md)") == []


def test_the_prefix_is_configurable():
    bare, _, _, _ = patterns(
        Config(root=pathlib.Path("/repo"), owner="o", project="1", repo="o/r",
               docs_prefix="documentation/"))
    assert bare.findall("`documentation/a.md`") == [("documentation/a.md", "")]


# The regexes above are pure. Everything below reaches for git and for `gh`,
# and edits issue bodies when it does — so each of these stubs both and
# watches what would have been written.
@pytest.fixture
def repo(monkeypatch):
    """A repo where every doc exists at sha `abc1234`, and edits are recorded."""
    from boardkit import plan_links

    edits: list[tuple] = []
    monkeypatch.setattr(plan_links, "gh",
                        lambda *args, **kw: edits.append(args) or "")
    monkeypatch.setattr(plan_links, "git", lambda *args: "abc1234")
    monkeypatch.setattr(plan_links, "git_ok", lambda *args: True)
    return edits


def issues_are(monkeypatch, *bodies):
    from boardkit import plan_links
    monkeypatch.setattr(plan_links, "issues", lambda config: [
        {"number": n, "body": body} for n, body in enumerate(bodies, start=1)])


def test_a_bare_path_becomes_a_link_pinned_to_the_sha(repo, monkeypatch, capsys):
    from boardkit.plan_links import rewrite
    issues_are(monkeypatch, "see `docs/a.md` for the rule")
    rewrite(CONFIG, dry=False)
    body = repo[0][-1]
    assert body == f"see [`docs/a.md`]({BLOB}/abc1234/docs/a.md) for the rule"


def test_a_dry_run_says_what_it_would_do_and_edits_nothing(repo, monkeypatch,
                                                           capsys):
    from boardkit.plan_links import rewrite
    issues_are(monkeypatch, "see `docs/a.md`")
    rewrite(CONFIG, dry=True)
    assert repo == []
    assert "would be updated" in capsys.readouterr().out


def test_a_body_with_nothing_to_link_is_left_alone(repo, monkeypatch):
    from boardkit.plan_links import rewrite
    issues_are(monkeypatch, "no paths here at all")
    rewrite(CONFIG, dry=False)
    assert repo == []


def test_a_path_no_pushed_commit_contains_is_reported_not_linked(monkeypatch,
                                                                 capsys):
    """Linking it anyway would produce a URL that 404s, which is worse than
    leaving the path as prose."""
    from boardkit import plan_links
    from boardkit.plan_links import rewrite
    edits: list[tuple] = []
    monkeypatch.setattr(plan_links, "gh", lambda *a, **k: edits.append(a) or "")
    monkeypatch.setattr(plan_links, "sha_for", lambda path, config: None)
    issues_are(monkeypatch, "see `docs/ghost.md`")
    rewrite(CONFIG, dry=False)
    assert edits == []
    assert "no pushed commit contains docs/ghost.md" in capsys.readouterr().out


def test_the_mirror_travels_with_the_edit_so_both_land_in_one_commit(
        repo, monkeypatch):
    from boardkit import issue_mirror
    from boardkit.plan_links import rewrite
    wrote = []
    monkeypatch.setattr(issue_mirror, "write", lambda config: wrote.append(1))
    issues_are(monkeypatch, "see `docs/a.md`")
    rewrite(CONFIG.model_copy(update={"mirror_path": "docs/mirror.md"}), dry=False)
    assert wrote == [1]


def test_the_mirror_is_not_rewritten_for_a_dry_run(repo, monkeypatch):
    from boardkit import issue_mirror
    from boardkit.plan_links import rewrite
    wrote = []
    monkeypatch.setattr(issue_mirror, "write", lambda config: wrote.append(1))
    issues_are(monkeypatch, "see `docs/a.md`")
    rewrite(CONFIG.model_copy(update={"mirror_path": "docs/mirror.md"}), dry=True)
    assert wrote == []


def test_check_passes_when_every_link_resolves(repo, monkeypatch, capsys):
    from boardkit.plan_links import check
    issues_are(monkeypatch, f"[`docs/a.md`]({BLOB}/abc1234/docs/a.md)")
    assert check(CONFIG) == 0
    assert "Plan links resolve: 0 broken" in capsys.readouterr().out


def test_check_reports_a_file_that_is_missing_at_the_sha_it_names(monkeypatch,
                                                                  capsys):
    """The sha resolves forever, but only to what it actually contained."""
    from boardkit import plan_links
    from boardkit.plan_links import check
    monkeypatch.setattr(plan_links, "git_ok", lambda *args: False)
    issues_are(monkeypatch, f"[`docs/a.md`]({BLOB}/abc1234/docs/a.md)")
    assert check(CONFIG) == 1
    assert "docs/a.md missing at abc1234" in capsys.readouterr().out


def test_check_refuses_a_link_pinned_to_a_branch(repo, monkeypatch, capsys):
    """It resolves today and rots the moment the branch moves, which is the
    whole failure this module exists to prevent."""
    from boardkit.plan_links import check
    issues_are(monkeypatch, f"[`docs/a.md`]({BLOB}/main/docs/a.md)")
    assert check(CONFIG) == 1
    assert "pinned to 'main', not a sha" in capsys.readouterr().out


def test_the_newest_pushed_commit_holding_the_path_is_the_one_chosen(monkeypatch):
    from boardkit import plan_links
    from boardkit.plan_links import sha_for
    monkeypatch.setattr(plan_links, "git", lambda *args: "newest older oldest")
    monkeypatch.setattr(plan_links, "git_ok",
                        lambda *args: args[-1].startswith("newest:"))
    assert sha_for("docs/a.md", CONFIG) == "newest"


def test_a_commit_that_deleted_the_path_falls_back_to_its_parent(monkeypatch):
    """`rev-list` lists the commit that removed a file too, and the file is
    not in it — its parent is the last sha the link can point at."""
    from boardkit import plan_links
    from boardkit.plan_links import sha_for
    monkeypatch.setattr(plan_links, "git",
                        lambda *args: "parentsha" if "rev-parse" in args else "deleter")
    monkeypatch.setattr(plan_links, "git_ok",
                        lambda *args: args[-1].startswith("parentsha:"))
    assert sha_for("docs/a.md", CONFIG) == "parentsha"


def test_a_path_in_no_pushed_commit_at_all_is_none(monkeypatch):
    from boardkit import plan_links
    from boardkit.plan_links import sha_for
    monkeypatch.setattr(plan_links, "git", lambda *args: "")
    assert sha_for("docs/a.md", CONFIG) is None


def test_both_issue_commands_look_past_the_first_hundred(monkeypatch, capsys):
    """`page_size` is a per-request page for the Project API, capped at
    GitHub's own ceiling of 100. `gh issue list --limit` is a total. Passing
    one as the other made "every open issue" mean "the first hundred", with
    nothing said about the rest."""
    from boardkit import issue_mirror, plan_links

    asked = []

    def listing(*args):
        asked.append(args)
        return [{"number": n, "body": ""} for n in range(1, 251)]

    monkeypatch.setattr(issue_mirror, "gh_json", listing)
    plan_links.issues(CONFIG)
    limit = asked[0][asked[0].index("--limit") + 1]
    assert int(limit) > 100
    # 250 came back, so the old limit would have silently dropped 150.
    assert len(plan_links.issues(CONFIG)) == 250


def test_a_listing_that_hits_the_limit_says_so(monkeypatch, capsys):
    """On the same grounds as the pageInfo warnings in `sync`: a caller that
    cannot tell a short list from a cut one reads the cut one as complete."""
    from boardkit import issue_mirror

    monkeypatch.setattr(issue_mirror, "gh_json", lambda *args: [
        {"number": n, "body": ""} for n in range(issue_mirror.ISSUE_LIMIT)])
    issue_mirror.open_issues(CONFIG, "number,body")
    assert "were not looked at" in capsys.readouterr().out


def test_a_listing_well_under_the_limit_says_nothing(monkeypatch, capsys):
    from boardkit import issue_mirror

    monkeypatch.setattr(issue_mirror, "gh_json",
                        lambda *args: [{"number": 1, "body": ""}])
    issue_mirror.open_issues(CONFIG, "number,body")
    assert capsys.readouterr().out == ""
