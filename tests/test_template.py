"""A new board copied from a template board (#118): `copyProjectV2` carries what
the API cannot set, so a copy needs no browser checklist."""
import pathlib

import pytest
from pydantic import ValidationError

from boardkit import admin, gh, setup
from boardkit.config import Config

TEMPLATE = "https://github.com/orgs/novatix-team/projects/26"


def config(**more) -> Config:
    return Config(**{"root": pathlib.Path("/repo"), "owner": "novatix-team",
                     "owner_type": "org", "repo": "novatix-team/widgets", "preset": "pm",
                     "template": TEMPLATE, **more})


def test_the_template_is_a_board_url():
    assert config().template == TEMPLATE
    with pytest.raises(ValidationError, match="not a board URL"):
        config(template="novatix-team/26")


def test_init_writes_the_template_beside_the_preset():
    text = setup.rendered({"owner": "novatix-team", "owner_type": "org",
                           "repo": "novatix-team/widgets", "project_name": "Widgets",
                           "template": TEMPLATE}, False, preset="pm")
    board = text.split("[board]", 1)[1]
    assert 'preset = "pm"\n# New boards are copied' in board
    assert f'template = "{TEMPLATE}"' in board


def test_the_copy_names_the_template_the_owner_and_no_drafts(monkeypatch):
    sent = []
    answers = iter([
        {"data": {"template": {"projectV2": {"id": "PVT_template"}},
                  "owner": {"id": "O_novatix"}}},
        {"data": {"copyProjectV2": {"projectV2": {"id": "PVT_new", "number": 31,
                                                  "url": "https://github.com/orgs/novatix-team/projects/31"}}}},
    ])
    monkeypatch.setattr(gh, "graphql_partial", lambda query: sent.append(query) or next(answers))
    made = admin.copy_template(config())
    assert made["number"] == 31
    assert 'repositoryOwner(login: "novatix-team")' in sent[0] and "projectV2(number: 26)" in sent[0]
    copy = sent[1]
    assert 'projectId: "PVT_template"' in copy and 'ownerId: "O_novatix"' in copy
    assert 'title: "Widgets"' in copy and "includeDraftIssues: false" in copy


def test_a_template_this_credential_cannot_see_creates_nothing(monkeypatch):
    sent = []
    monkeypatch.setattr(gh, "graphql_partial", lambda query: sent.append(query) or
                        {"data": {"template": None, "owner": {"id": "O_novatix"}}})
    with pytest.raises(gh.BoardError, match="could not read the template"):
        admin.copy_template(config())
    assert len(sent) == 1  # no copy was asked for


def apply_new_board(monkeypatch, cfg: Config) -> list:
    """`admin apply` where no board exists yet, everything after creation stubbed."""
    made = []
    monkeypatch.setattr(admin, "locate", lambda config: "")
    monkeypatch.setattr(admin.auth, "require", lambda config, tier: None)
    monkeypatch.setattr(admin, "gh_json", lambda *a: made.append(("create", a)) or
                        {"number": 7, "id": "PVT_7", "url": ""})
    monkeypatch.setattr(admin, "copy_template", lambda config: made.append(("copy",)) or
                        {"number": 31, "id": "PVT_31", "url": ""})
    for name, stub in (("record_url", lambda *a: None), ("attach", lambda *a: None),
                       ("existing", lambda c, n: None), ("plan", lambda *a: []),
                       ("missing_labels", lambda c: []), ("missing_issue_types", lambda c: []),
                       ("apply_views", lambda *a, **k: None), ("fetch_fields", lambda *a: []),
                       ("checklist", lambda *a: made.append(("checklist",)))):
        monkeypatch.setattr(admin, name, stub)
    admin.apply(cfg)
    return made


def test_a_new_board_is_a_copy_and_needs_no_checklist(monkeypatch, capsys):
    made = apply_new_board(monkeypatch, config())
    assert made == [("copy",)]
    assert f"a copy of {TEMPLATE}" in capsys.readouterr().out


def test_without_a_template_a_new_board_is_made_and_the_checklist_printed(monkeypatch):
    made = apply_new_board(monkeypatch, config(template=""))
    assert [entry[0] for entry in made] == ["create", "checklist"]


def test_the_checklist_on_request_says_where_new_boards_come_from(capsys):
    admin.checklist(config(project="31"), "")
    out = capsys.readouterr().out
    assert TEMPLATE in out and "made before `template` was set" in out


def test_apply_takes_the_template_for_one_run(monkeypatch, tmp_path):
    """novatix-pm's setup creates a board with `admin apply`, and keeps no
    [board] settings in the repository: the template is needed once, then."""
    from boardkit import cli
    seen = []
    monkeypatch.setattr(cli, "load", lambda preset="": Config(
        root=tmp_path, owner="novatix-team", owner_type="org", repo="novatix-team/widgets"))
    monkeypatch.setattr(cli.admin, "apply", lambda config, **k: seen.append(config.template) or 0)
    assert cli.dispatch("admin", ["apply", "--template", TEMPLATE]) == 0
    assert seen == [TEMPLATE]
