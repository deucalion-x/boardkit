"""Presets: a shipped board shape that a repo's own [board] section lays over."""
import tomllib

import pytest

from boardkit import admin, cli, config, setup
from boardkit.gh import BoardError

IDENTITY = 'owner = "octo"\nrepo = "octo/widgets"\nproject = "4"\n'


def loaded(tmp_path, text, **kwargs):
    (tmp_path / "boardkit.toml").write_text(IDENTITY + text, encoding="utf-8")
    return config.load(tmp_path, **kwargs)


def test_the_roadmap_preset_declares_the_columns_dates_and_five_views(tmp_path):
    found = loaded(tmp_path, '[board]\npreset = "roadmap"\n')
    assert found.preset == "roadmap"
    assert found.status_order == ("Inbox", "Todo", "In Progress", "In Review", "Done")
    assert [(f.name, f.data_type) for f in found.fields] == [
        ("start", "DATE"), ("target", "DATE")]
    assert [(v.name, v.layout, v.filter) for v in found.views] == [
        ("Current", "BOARD", "-type:Initiative"),
        ("All", "TABLE", ""),
        ("Roadmap", "TABLE", "type:Initiative"),
        ("Timeline", "ROADMAP", "has:parent-issue"),
        ("Initiatives", "ROADMAP", "type:Initiative"),
    ]
    assert found.field("start").display == "Start"


def test_the_repo_wins_by_name_and_adds_to_the_preset(tmp_path):
    found = loaded(tmp_path, (
        'status_order = ["Backlog", "Doing", "Done"]\n'
        '[board]\npreset = "roadmap"\n'
        '[[board.views]]\nname = "Current"\nlayout = "TABLE"\n'
        '[[board.views]]\nname = "Mine"\nfilter = "assignee:@me"\n'
        '[board.fields.area]\noptions = ["Core", "UI"]\n'))
    assert found.status_order == ("Backlog", "Doing", "Done")
    views = {v.name: v for v in found.views}
    assert views["Current"].layout == "TABLE" and views["Current"].filter == ""
    assert [v.name for v in found.views][-1] == "Mine"
    assert len(found.views) == 6
    assert found.field_names == ("start", "target", "area")


def test_naming_a_preset_field_only_keeps_its_definition(tmp_path):
    found = loaded(tmp_path, '[board]\npreset = "roadmap"\nfields = ["target"]\n')
    assert found.field("target").data_type == "DATE"


def test_a_preset_given_for_one_run_applies_without_the_file_naming_it(tmp_path):
    found = loaded(tmp_path, "", preset="roadmap")
    assert found.preset == "roadmap" and len(found.views) == 5


def test_a_top_level_preset_is_lifted_like_any_board_setting(tmp_path):
    assert loaded(tmp_path, 'preset = "roadmap"\n').preset == "roadmap"


def test_an_unknown_preset_names_the_ones_that_exist(tmp_path):
    with pytest.raises(BoardError, match="roadmap"):
        loaded(tmp_path, '[board]\npreset = "kanban"\n')


def test_init_with_a_preset_names_it_instead_of_the_starter_views():
    text = setup.rendered({"owner": "o", "repo": "o/r", "project_name": "r"},
                          exists=False, preset="roadmap")
    parsed = tomllib.loads(text)
    assert parsed["board"]["preset"] == "roadmap"
    assert "views" not in parsed["board"]
    assert parsed["board"]["status_order"][0] == "Inbox"


def test_init_with_a_preset_nests_it_in_pyproject():
    text = setup.rendered({"owner": "o", "repo": "o/r", "project_name": "r"},
                          nested=True, preset="roadmap")
    parsed = tomllib.loads(text)
    assert parsed["tool"]["boardkit"]["board"]["preset"] == "roadmap"


def test_init_refuses_an_unknown_preset_before_writing():
    with pytest.raises(BoardError, match="no preset"):
        setup.rendered({"owner": "o", "repo": "o/r"}, preset="nope")


def test_the_checklist_links_each_step_to_where_it_is_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(admin, "existing_views", lambda config, number: {
        "Timeline": admin.ExistingView("V4", number=4),
        "Initiatives": admin.ExistingView("V5", number=5)})
    found = loaded(tmp_path, '[board]\npreset = "roadmap"\n')
    admin.checklist(found)
    out = capsys.readouterr().out
    base = "https://github.com/users/octo/projects/4"
    assert out.startswith("Finish the roadmap board in the browser")
    assert "1. Timeline view: group by Parent issue, sort by Start" in out
    assert f"{base}/views/4" in out and f"{base}/views/5" in out
    assert f"{base}/workflows" in out
    for said in ("Auto-close issue", 'is:closed reason:"not planned"'):
        assert said in out


def test_the_checklist_stands_without_links_when_views_cannot_be_read(
        tmp_path, monkeypatch, capsys):
    def refuse(config, number):
        raise BoardError("offline")
    monkeypatch.setattr(admin, "existing_views", refuse)
    admin.checklist(loaded(tmp_path, '[board]\npreset = "roadmap"\n'))
    out = capsys.readouterr().out
    assert "Timeline view:" in out and "/views/" not in out


def test_the_checklist_without_a_preset_says_so(tmp_path, capsys):
    admin.checklist(loaded(tmp_path, ""))
    assert "names no preset" in capsys.readouterr().out


def test_admin_checklist_is_a_verb(tmp_path, monkeypatch, capsys):
    (tmp_path / "boardkit.toml").write_text(
        IDENTITY + '[board]\npreset = "roadmap"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(admin, "existing_views", lambda config, number: {})
    assert cli.main(["admin", "checklist"]) == 0
    assert "Auto-archive items" in capsys.readouterr().out


def test_the_preset_flag_takes_its_value_out_of_the_arguments():
    assert cli.option(["apply", "--preset", "roadmap", "-n"], "--preset") == (
        "roadmap", ["apply", "-n"])
    assert cli.option(["apply"], "--preset") == ("", ["apply"])
    with pytest.raises(BoardError):
        cli.option(["apply", "--preset"], "--preset")
