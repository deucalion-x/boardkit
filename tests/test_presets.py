"""Presets: a shipped board shape that a repo's own [board] section lays over."""
import tomllib

import pytest

from boardkit import admin, cli, config, setup
from boardkit.gh import BoardError

IDENTITY = 'owner = "octo"\nrepo = "octo/widgets"\nproject = "4"\n'


def loaded(tmp_path, text, **kwargs):
    (tmp_path / "boardkit.toml").write_text(IDENTITY + text, encoding="utf-8")
    return config.load(tmp_path, **kwargs)


def test_the_pm_preset_declares_the_columns_dates_and_five_views(tmp_path):
    found = loaded(tmp_path, '[board]\npreset = "pm"\n')
    assert found.preset == "pm"
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
        '[board]\npreset = "pm"\n'
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
    found = loaded(tmp_path, '[board]\npreset = "pm"\nfields = ["target"]\n')
    assert found.field("target").data_type == "DATE"


def test_a_preset_given_for_one_run_applies_without_the_file_naming_it(tmp_path):
    found = loaded(tmp_path, "", preset="pm")
    assert found.preset == "pm" and len(found.views) == 5


def test_pm_is_work_with_the_management_views_on_top(tmp_path):
    work = loaded(tmp_path, '[board]\npreset = "work"\n')
    assert [v.name for v in work.views] == ["Current", "All"]
    assert work.fields == ()
    assert work.status_order[0] == "Inbox"
    pm = loaded(tmp_path, '[board]\npreset = "pm"\n')
    assert [v.name for v in pm.views][:2] == ["Current", "All"]
    assert pm.status_order == work.status_order


def test_work_alone_leaves_only_the_workflows_to_the_browser(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(admin, "existing_views", lambda config, number: {})
    admin.checklist(loaded(tmp_path, '[board]\npreset = "work"\n'))
    out = capsys.readouterr().out
    assert "Auto-close issue" in out and "Timeline" not in out


def test_a_preset_that_extends_itself_is_refused(tmp_path, monkeypatch):
    (tmp_path / "loop.toml").write_text('extends = "loop"\n', encoding="utf-8")
    monkeypatch.setattr(config, "PRESETS", tmp_path)
    with pytest.raises(BoardError, match="extends itself"):
        config.layers("loop")


def test_roadmap_is_still_accepted_as_the_pm_preset(tmp_path):
    found = loaded(tmp_path, '[board]\npreset = "roadmap"\n')
    assert found.preset == "pm" and len(found.views) == 5


def test_a_top_level_preset_is_lifted_like_any_board_setting(tmp_path):
    assert loaded(tmp_path, 'preset = "pm"\n').preset == "pm"


def test_an_unknown_preset_names_the_ones_that_exist(tmp_path):
    with pytest.raises(BoardError, match="has pm, portfolio, work"):
        loaded(tmp_path, '[board]\npreset = "kanban"\n')


def test_init_with_a_preset_names_it_instead_of_the_starter_views():
    text = setup.rendered({"owner": "o", "repo": "o/r", "project_name": "r"},
                          exists=False, preset="pm")
    parsed = tomllib.loads(text)
    assert parsed["board"]["preset"] == "pm"
    assert "views" not in parsed["board"]
    assert parsed["board"]["status_order"][0] == "Inbox"


def test_init_with_a_preset_nests_it_in_pyproject():
    text = setup.rendered({"owner": "o", "repo": "o/r", "project_name": "r"},
                          nested=True, preset="pm")
    parsed = tomllib.loads(text)
    assert parsed["tool"]["boardkit"]["board"]["preset"] == "pm"


def test_init_refuses_an_unknown_preset_before_writing():
    with pytest.raises(BoardError, match="no preset"):
        setup.rendered({"owner": "o", "repo": "o/r"}, preset="nope")


def test_the_checklist_links_each_step_to_where_it_is_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(admin, "existing_views", lambda config, number: {
        "Timeline": admin.ExistingView("V4", number=4),
        "Initiatives": admin.ExistingView("V5", number=5)})
    found = loaded(tmp_path, '[board]\npreset = "pm"\n')
    admin.checklist(found)
    out = capsys.readouterr().out
    base = "https://github.com/users/octo/projects/4"
    assert out.startswith("Finish the board in the browser")
    # Base's steps first, then the ones pm adds.
    assert "1. Workflows: turn on \"Auto-close issue\"" in out
    assert "3. Timeline view: group by Parent issue, sort by Start" in out
    assert f"{base}/views/4" in out and f"{base}/views/5" in out
    assert f"{base}/workflows" in out
    for said in ("Auto-close issue", 'is:closed reason:"not planned"'):
        assert said in out


def test_the_checklist_stands_without_links_when_views_cannot_be_read(
        tmp_path, monkeypatch, capsys):
    def refuse(config, number):
        raise BoardError("offline")
    monkeypatch.setattr(admin, "existing_views", refuse)
    admin.checklist(loaded(tmp_path, '[board]\npreset = "pm"\n'))
    out = capsys.readouterr().out
    assert "Timeline view:" in out and "/views/" not in out


def test_the_checklist_without_a_preset_says_so(tmp_path, capsys):
    admin.checklist(loaded(tmp_path, ""))
    assert "names no preset" in capsys.readouterr().out


def test_admin_checklist_is_a_verb(tmp_path, monkeypatch, capsys):
    (tmp_path / "boardkit.toml").write_text(
        IDENTITY + '[board]\npreset = "pm"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(admin, "existing_views", lambda config, number: {})
    assert cli.main(["admin", "checklist"]) == 0
    assert "Auto-archive items" in capsys.readouterr().out


def test_the_preset_flag_takes_its_value_out_of_the_arguments():
    assert cli.option(["apply", "--preset", "pm", "-n"], "--preset") == (
        "pm", ["apply", "-n"])
    assert cli.option(["apply"], "--preset") == ("", ["apply"])
    with pytest.raises(BoardError):
        cli.option(["apply", "--preset"], "--preset")


def test_the_work_preset_declares_the_labels_the_skill_relies_on(tmp_path):
    found = loaded(tmp_path, '[board]\npreset = "work"\n')
    assert [label.name for label in found.labels] == ["dev", "area:security"]
    assert found.labels[0].color == "0E8A16"


def test_a_repo_label_replaces_the_presets_by_name_and_adds_to_them(tmp_path):
    found = loaded(tmp_path, (
        '[board]\npreset = "pm"\n'
        '[[board.labels]]\nname = "dev"\ncolor = "#123456"\n'
        '[[board.labels]]\nname = "area:avatar"\n'))
    assert [label.name for label in found.labels] == ["dev", "area:security", "area:avatar"]
    assert found.labels[0].color == "123456"
