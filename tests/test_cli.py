"""The verbs that need no network should not reach for it."""
import pytest

from boardkit import cli


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.boardkit]\nowner = "octo"\nproject = "4"\nrepo = "octo/places"\n',
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.board_module, "rows",
                        lambda config: pytest.fail("the board was fetched"))
    return tmp_path


def test_an_unknown_verb_prints_usage_without_querying_github(repo, capsys):
    assert cli.main(["badverb"]) == 2
    assert "board find <terms>" in capsys.readouterr().out


def test_item_without_a_number_prints_usage_rather_than_crashing(repo):
    assert cli.main(["item"]) == 2
    assert cli.main(["item", "not-a-number"]) == 2


def test_help_exits_clean(repo, capsys):
    assert cli.main(["--help"]) == 0


def test_a_missing_config_is_an_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ready"]) == 1
    assert "no boardkit configuration" in capsys.readouterr().err
