"""A portfolio: the Initiatives of several repositories, copied onto one board."""
import json
import tomllib

import pytest

from boardkit import cli, config, gh, portfolio, setup
from boardkit.config import ProjectField
from boardkit.gh import BoardError
from boardkit.portfolio import Initiative, Item

IDENTITY = 'owner = "octo"\nowner_type = "org"\nrepo = "octo/portfolio"\nproject = "9"\n'
PORTFOLIO = IDENTITY + ('[board]\npreset = "portfolio"\n'
                        '[portfolio]\nrepos = ["widgets", "octo/gadgets"]\n')


def loaded(tmp_path, text=PORTFOLIO):
    (tmp_path / "boardkit.toml").write_text(text, encoding="utf-8")
    return config.load(tmp_path)


FIELDS = (
    ProjectField(name="status", id="F_S", options=("Todo", "In Progress", "Done"),
                 option_ids={"Todo": "O_T", "In Progress": "O_P", "Done": "O_D"}),
    ProjectField(name="start", id="F_ST", data_type="DATE"),
    ProjectField(name="target", id="F_TA", data_type="DATE"),
    ProjectField(name="blocked", id="F_B", data_type="TEXT"),
)


def test_repos_are_named_in_full_under_the_boards_own_owner(tmp_path):
    found = loaded(tmp_path)
    assert found.portfolio.repos == ("octo/widgets", "octo/gadgets")


def test_a_repo_of_another_organisation_is_refused_by_name(tmp_path):
    with pytest.raises(BoardError, match="'acme/tools' is not octo's"):
        loaded(tmp_path, IDENTITY + '[portfolio]\nrepos = ["acme/tools"]\n')


def test_a_board_without_the_section_is_not_a_portfolio(tmp_path):
    assert loaded(tmp_path, IDENTITY).portfolio is None


def test_the_preset_is_its_own_shape_not_work_with_more(tmp_path):
    found = loaded(tmp_path)
    assert found.preset == "portfolio"
    assert [(v.name, v.layout, v.filter) for v in found.views] == [
        ("Roadmap", "ROADMAP", "type:Initiative"),
        ("Projects", "TABLE", "type:Initiative is:open"),
        ("Blocked", "TABLE", "type:Initiative is:open has:blocked"),
        ("Late", "TABLE", "type:Initiative is:open target:<@today"),
    ]
    assert [(f.name, f.data_type) for f in found.fields] == [
        ("start", "DATE"), ("target", "DATE")]
    steps = config.checklist("portfolio")
    assert [s.get("view") or s.get("page") for s in steps] == [
        "Roadmap", "Projects", "workflows"]


def test_the_copied_fields_are_status_the_declared_ones_and_blocked(tmp_path):
    names = [f.name for f in portfolio.copied(loaded(tmp_path), FIELDS)]
    assert names == ["status", "start", "target", "blocked"]


def initiative(number, repo="octo/widgets", **values):
    return Initiative(f"I_{number}", repo, number, f"Initiative {number}", values)


def test_a_missing_initiative_is_added_with_its_values(tmp_path):
    result = portfolio.plan(loaded(tmp_path), FIELDS, [],
                            [initiative(1, status="In Progress", target="2026-12-01")])
    assert [i.number for i in result.add] == [1]
    assert [(f.name, value) for _, f, value, _ in result.set] == [
        ("status", "In Progress"), ("target", "2026-12-01")]


def test_a_value_that_already_matches_is_left_alone(tmp_path):
    on_board = Item("PVTI_1", "I_1", "ISSUE", "octo/widgets", "Initiative", 1,
                    {"status": "Todo", "target": "2026-12-01"})
    result = portfolio.plan(loaded(tmp_path), FIELDS, [on_board],
                            [initiative(1, status="Todo", target="2026-12-01")])
    assert not result.add and not result.set and not result.remove


def test_a_value_removed_on_its_own_board_is_cleared_here(tmp_path):
    on_board = Item("PVTI_1", "I_1", "ISSUE", "octo/widgets", "Initiative", 1,
                    {"blocked": "waiting on IT"})
    result = portfolio.plan(loaded(tmp_path), FIELDS, [on_board], [initiative(1)])
    assert [(f.name, value, was) for _, f, value, was in result.set] == [
        ("blocked", "", "waiting on IT")]


def test_a_column_the_portfolio_lacks_is_skipped_and_said(tmp_path):
    result = portfolio.plan(loaded(tmp_path), FIELDS, [],
                            [initiative(1, status="Recette")])
    assert not result.set
    assert result.skipped == ["widgets#1 status 'Recette': the portfolio has no such option"]


def test_what_is_no_longer_a_listed_initiative_leaves(tmp_path):
    items = [
        Item("A", "I_1", "ISSUE", "octo/widgets", "Task", 1),        # retyped
        Item("B", "I_2", "ISSUE", "octo/elsewhere", "Initiative", 2),  # unlisted
        Item("C", "PR_3", "PULL_REQUEST", "octo/widgets", "", 3),
        Item("D", "", "DRAFT_ISSUE"),                                  # a note
        Item("E", "", "ISSUE"),                                        # unseen
        Item("F", "I_6", "ISSUE", "octo/gadgets", "Initiative", 6),    # stays
    ]
    result = portfolio.plan(loaded(tmp_path), FIELDS, items, [])
    assert [item.id for item in result.remove] == ["A", "B", "C"]


class FakeGitHub:
    """Answers the portfolio's queries and records the mutations sent."""

    def __init__(self, items=(), initiatives=None, unreadable=()):
        self.items = list(items)
        self.initiatives = initiatives or {}
        self.unreadable = set(unreadable)
        self.sent: list[str] = []

    def __call__(self, args):
        query = args[-1].removeprefix("query=")
        if query.startswith("mutation"):
            self.sent.append(query)
            data = {}
            for alias in [part.split(":")[0] for part in query[11:].split(") { ")
                          if part.strip().startswith("a")]:
                data[alias.strip()] = {"item": {"id": f"PVTI_new_{alias.strip()}"}}
            return {"data": data}
        if "projectV2(number" in query:
            return {"data": {"organization": {"projectV2": {
                "id": "PVT_P",
                "fields": {"pageInfo": {}, "nodes": [
                    {"id": "F_S", "name": "Status", "dataType": "SINGLE_SELECT",
                     "options": [{"id": "O_T", "name": "Todo"},
                                 {"id": "O_P", "name": "In Progress"}]},
                    {"id": "F_TA", "name": "Target", "dataType": "DATE"},
                    {"id": "F_B", "name": "blocked", "dataType": "TEXT"},
                    {"id": "F_R", "name": "Repository", "dataType": "REPOSITORY"}]},
                "items": {"pageInfo": {"hasNextPage": False}, "nodes": self.items}}}}}
        name = query.split('name: "')[1].split('"')[0]
        if name in self.unreadable:
            return {"data": {"repository": None}}
        return {"data": {"repository": {"issues": {
            "pageInfo": {"hasNextPage": False},
            "nodes": self.initiatives.get(name, [])}}}}


def issue(number, title, *boards):
    """An Initiative as GitHub lists it, on the given boards' values."""
    return {"id": f"I_{number}", "number": number, "title": title,
            "projectItems": {"nodes": [
                {"project": {"id": project}, "fieldValues": {"nodes": [
                    {"name" if field == "Status" else "date" if field == "Target"
                     else "text": value, "field": {"name": field}}
                    for field, value in values.items()]}}
                for project, values in boards]}}


@pytest.fixture
def github(tmp_path, monkeypatch):
    (tmp_path / "boardkit.toml").write_text(PORTFOLIO, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portfolio.auth, "require", lambda config, tier: None)
    fake = FakeGitHub(initiatives={"widgets": [issue(
        1, "Search", ("PVT_W", {"Status": "In Progress", "Target": "2026-12-01"}),
        # The portfolio's own stale value is never the source.
        ("PVT_P", {"Status": "Todo"}))]})
    monkeypatch.setattr(portfolio, "run_json", fake)
    return fake


def test_sync_adds_then_copies_then_reports(github, capsys):
    assert cli.main(["portfolio", "sync"]) == 0
    out = capsys.readouterr().out
    assert "add widgets#1 Search" in out
    assert "set widgets#1 status = In Progress (was unset)" in out
    added, copied = github.sent[:2]
    assert 'addProjectV2ItemById(input: { projectId: "PVT_P", contentId: "I_1" })' in added
    assert '"PVTI_new_a0"' in copied and 'singleSelectOptionId: "O_P"' in copied
    assert 'date: "2026-12-01"' in copied


def test_a_dry_run_sends_nothing(github, capsys):
    assert cli.main(["portfolio", "sync", "-n"]) == 0
    assert "would add widgets#1 Search" in capsys.readouterr().out
    assert github.sent == []


def test_an_unreadable_repo_is_said_and_costs_the_others_nothing(github, capsys):
    github.unreadable = {"gadgets"}
    github.items = [{"id": "PVTI_9", "type": "ISSUE", "content": {
        "id": "I_9", "number": 9, "repository": {"nameWithOwner": "octo/gadgets"},
        "issueType": {"name": "Initiative"}}, "fieldValues": {"nodes": []}}]
    assert cli.main(["portfolio", "sync"]) == 1
    out = capsys.readouterr().out
    assert "warning: cannot read octo/gadgets" in out
    assert "add widgets#1" in out and "remove" not in out


def test_a_converged_portfolio_says_so(github, capsys):
    github.items = [{"id": "PVTI_1", "type": "ISSUE", "content": {
        "id": "I_1", "number": 1, "repository": {"nameWithOwner": "octo/widgets"},
        "issueType": {"name": "Initiative"}}, "fieldValues": {"nodes": [
            {"name": "In Progress", "field": {"name": "Status"}},
            {"date": "2026-12-01", "field": {"name": "Target"}}]}}]
    assert cli.main(["portfolio", "sync"]) == 0
    assert "nothing to change" in capsys.readouterr().out
    assert github.sent == []


def test_sync_on_a_board_that_is_not_a_portfolio_says_what_to_add(tmp_path, monkeypatch, capsys):
    (tmp_path / "boardkit.toml").write_text(IDENTITY, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["portfolio", "sync"]) == 1
    assert "[portfolio] repos" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["set", "1", "status", "Done"], ["add", "A title"], ["close", "1"],
    ["comment", "1", "text"], ["edit", "1", "--title", "t"]])
def test_items_are_not_changed_on_a_portfolio(github, monkeypatch, capsys, argv):
    monkeypatch.setattr(gh, "run", lambda args: pytest.fail(f"ran {args[0]}"))
    assert cli.main(argv) == 1
    assert "this board is a portfolio" in capsys.readouterr().err


def test_init_writes_the_portfolio_section():
    text = setup.rendered({"owner": "octo", "repo": "octo/portfolio",
                           "project_name": "Portfolio"}, preset="portfolio")
    parsed = tomllib.loads(text)
    assert parsed["board"]["preset"] == "portfolio"
    assert parsed["portfolio"] == {"repos": []}


def test_init_nests_the_portfolio_section_in_pyproject():
    text = setup.rendered({"owner": "octo", "repo": "octo/portfolio",
                           "project_name": "Portfolio"}, nested=True, preset="portfolio")
    assert tomllib.loads(text)["tool"]["boardkit"]["portfolio"] == {"repos": []}


def test_the_action_installs_a_pinned_boardkit(monkeypatch):
    import boardkit
    monkeypatch.setattr(boardkit, "__version__", "1.2.3")
    text = setup.workflow()
    assert "boardkit@v1.2.3" in text and "board portfolio sync" in text
    assert "${{ secrets.BOARDKIT_TOKEN }}" in text
    monkeypatch.setattr(boardkit, "__version__", "1.2.4.dev3+gabc")
    assert "boardkit@main" in setup.workflow()


def test_the_json_schema_describes_the_section():
    assert "portfolio" in json.dumps(config.Config.model_json_schema())
