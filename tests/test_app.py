"""Creating a repository's own app: the manifest, the exchange, the config."""
import io
import json
import pathlib

from boardkit import app
from boardkit.config import Config


def config_at(tmp_path: pathlib.Path) -> Config:
    file = tmp_path / "boardkit.toml"
    file.write_text('owner = "octo"\nowner_type = "org"\nrepo = "octo/widgets"\n')
    return Config(root=tmp_path, source=file, owner="octo", owner_type="org",
                  repo="octo/widgets")


def test_the_manifest_names_the_app_and_asks_only_for_agent_permissions(tmp_path):
    made = app.manifest(config_at(tmp_path), "http://127.0.0.1:1/done")
    assert made["name"] == "boardkit-octo-widgets" and made["public"] is False
    assert made["default_permissions"]["checks"] == "read"
    assert "administration" not in made["default_permissions"]
    assert made["hook_attributes"]["active"] is False


def test_long_names_fit_githubs_limit(tmp_path):
    long = Config(root=tmp_path, owner="o", repo="an-organisation/a-very-long-repository-name")
    assert len(app.name_for(long)) <= 34


def test_the_exchange_keeps_no_secret(monkeypatch):
    answer = {"client_id": "Iv23x", "slug": "boardkit-octo-widgets", "html_url": "u",
              "owner": {"login": "octo", "type": "Organization"},
              "client_secret": "s", "pem": "-----BEGIN", "webhook_secret": "w"}
    monkeypatch.setattr(app.urllib.request, "urlopen",
                        lambda request, timeout: io.BytesIO(json.dumps(answer).encode()))
    kept = app.convert("code")
    assert kept == {"client_id": "Iv23x", "slug": "boardkit-octo-widgets", "html_url": "u",
                    "owner": "octo"}


def test_an_app_made_under_another_account_is_refused(tmp_path):
    """The browser, not the form, decides who owns the app; a personal app
    cannot reach an organisation's board, so it must not be recorded."""
    import pytest

    from boardkit.gh import BoardError
    config = config_at(tmp_path)
    made = {"client_id": "Iv23x", "slug": "boardkit-octo-widgets", "html_url": "u",
            "owner": "someone-else"}
    with pytest.raises(BoardError, match=r"someone-else, not octo"):
        app.check_owner(config, made)
    assert "app_client_id" not in config.source.read_text()
    app.check_owner(config, {**made, "owner": "Octo"})


def test_the_client_id_is_written_into_the_config(tmp_path):
    config = config_at(tmp_path)
    app.remember(config, "Iv23first")
    app.remember(config, "Iv23second")
    text = config.source.read_text()
    assert text.count("app_client_id") == 1 and 'app_client_id = "Iv23second"' in text
    assert text.index("repo =") < text.index("app_client_id")


def test_the_page_posts_the_manifest_to_the_org(tmp_path):
    body = app.page(config_at(tmp_path), "http://127.0.0.1:1/done", "st")
    assert "organizations/octo/settings/apps/new?state=st" in body
    assert 'name="manifest"' in body


