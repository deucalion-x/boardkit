"""`[board] url`: the board named once, the way a person copies it."""
import pathlib
import tomllib

import pytest

from boardkit import setup
from boardkit.config import Config, load
from boardkit.gh import BoardError

URL = "https://github.com/orgs/octo/projects/16"


def config_from(tmp_path: pathlib.Path, text: str, name: str = "boardkit.toml") -> Config:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return load(tmp_path)


def test_the_url_says_owner_kind_and_number(tmp_path):
    config = config_from(tmp_path, f'repo = "octo/places"\n[board]\nurl = "{URL}"\n')
    assert (config.owner, config.owner_type, config.project) == ("octo", "org", "16")
    assert config.url == URL


def test_a_personal_board_and_a_trailing_slash(tmp_path):
    config = config_from(tmp_path, 'repo = "me/places"\n[board]\n'
                         'url = "https://github.com/users/me/projects/3/"\n')
    assert (config.owner, config.owner_type, config.project) == ("me", "user", "3")
    assert config.url == "https://github.com/users/me/projects/3"


def test_inside_pyproject(tmp_path):
    config = config_from(tmp_path, f'[tool.boardkit]\nrepo = "octo/places"\n'
                         f'[tool.boardkit.board]\nurl = "{URL}"\n', "pyproject.toml")
    assert config.project == "16"


def test_agreeing_old_keys_are_accepted(tmp_path):
    config = config_from(tmp_path, f'owner = "Octo"\nowner_type = "org"\nproject = 16\n'
                         f'repo = "octo/places"\n[board]\nurl = "{URL}"\n')
    assert config.project == "16"


@pytest.mark.parametrize("stated", ['owner = "other"', 'owner_type = "user"', "project = 4"])
def test_a_key_disagreeing_with_the_url_is_refused(tmp_path, stated):
    with pytest.raises(BoardError, match="disagrees with url"):
        config_from(tmp_path, f'{stated}\nrepo = "octo/places"\n[board]\nurl = "{URL}"\n')


def test_a_board_of_another_owner_is_refused(tmp_path):
    with pytest.raises(BoardError, match="only holds its own owner's issues"):
        config_from(tmp_path, f'repo = "other/places"\n[board]\nurl = "{URL}"\n')


@pytest.mark.parametrize("url", ["https://github.com/octo/places/projects/1",
                                 "https://github.com/orgs/octo/projects/new",
                                 "github.com/orgs/octo/projects/1"])
def test_anything_but_a_board_url_is_refused_with_its_shape(tmp_path, url):
    with pytest.raises(BoardError, match=r"orgs/<org>/projects/<number>"):
        config_from(tmp_path, f'repo = "octo/places"\n[board]\nurl = "{url}"\n')


def test_project_url_is_the_old_spelling(tmp_path):
    config = config_from(tmp_path, f'repo = "octo/places"\nproject_url = "{URL}"\n')
    assert (config.owner, config.project) == ("octo", "16")


def test_old_keys_still_work(tmp_path):
    config = config_from(tmp_path, 'owner = "octo"\nrepo = "octo/places"\nproject = 4\n')
    assert config.url == "https://github.com/users/octo/projects/4"
    assert config.legacy_identity


def test_with_url_replaces_the_naming_keys_and_keeps_the_rest():
    before = ('# Which board this is.\nowner = "octo"\nowner_type = "org"\n'
              'repo = "octo/places"\nproject_name = "Places"\n\n[board]\n'
              '# The columns.\nstatus_order = ["Todo"]\n\n[[board.views]]\nname = "All"\n')
    after = setup.with_url(before, URL)
    assert after == ('# Which board this is.\nrepo = "octo/places"\n\n[board]\n'
                     f'url = "{URL}"\n# The columns.\nstatus_order = ["Todo"]\n\n'
                     '[[board.views]]\nname = "All"\n')


def test_with_url_in_pyproject_touches_only_boardkit():
    before = ('[project]\nname = "x"\n[tool.other]\nowner = "keep"\n'
              '[tool.boardkit]\nowner = "octo"\nrepo = "octo/places"\nproject = 4\n')
    data = tomllib.loads(setup.with_url(before, URL))
    assert data["tool"]["other"] == {"owner": "keep"}
    assert data["tool"]["boardkit"] == {"repo": "octo/places", "board": {"url": URL}}


def test_init_on_an_existing_board_writes_its_url(tmp_path):
    text = setup.rendered({"owner": "octo", "owner_type": "org", "repo": "octo/places",
                           "project_name": "Places", "url": URL}, nested=True)
    config = config_from(tmp_path, text, "pyproject.toml")
    assert (config.project, config.project_url, config.project_name) == ("16", URL, "")


def test_a_created_board_is_recorded_by_url(tmp_path, capsys):
    config = config_from(tmp_path, setup.rendered(
        {"owner": "octo", "owner_type": "org", "repo": "octo/places",
         "project_name": "Places"}))
    assert config.project_name == "Places" and not config.project
    setup.record_url(config, URL)
    again = load(tmp_path)
    assert (again.project, again.project_url, again.project_name) == ("16", URL, "")
    assert again.status_order == config.status_order
    assert "recorded [board] url" in capsys.readouterr().out


def test_admin_create_records_the_new_board_by_url(tmp_path, monkeypatch):
    """The number exists only once GitHub has made the board, so `create`
    is where the config learns it."""
    from boardkit import admin, credential
    config = config_from(tmp_path, setup.rendered(
        {"owner": "octo", "owner_type": "org", "repo": "octo/places",
         "project_name": "Places"}))
    monkeypatch.setattr(admin.auth, "require", lambda *_: None)
    monkeypatch.setattr(credential, "elevated", lambda *_: None)
    monkeypatch.setattr(admin, "locate", lambda _: "")
    monkeypatch.setattr(admin, "existing", lambda _c, n: ("PVT_1", ()) if n else None)
    monkeypatch.setattr(admin, "plan", lambda *_: [])
    monkeypatch.setattr(admin, "missing_labels", lambda _: [])
    monkeypatch.setattr(admin, "fetch_fields", lambda *_: [])
    monkeypatch.setattr(admin, "apply_views", lambda *_, **__: None)
    monkeypatch.setattr(admin, "gh_json", lambda *args: {"number": 16, "url": URL})
    admin.apply(config, browser=False)
    assert load(tmp_path).project_url == URL
