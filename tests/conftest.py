import pytest

from boardkit import credential


@pytest.fixture(autouse=True)
def inherited_credential(monkeypatch):
    """The CLI points `gh` at a focused token per run. A test that went
    through it must not leave the next one needing a keychain."""
    monkeypatch.setattr(credential, "repo", None)
    monkeypatch.setattr(credential, "as_me", False)
    monkeypatch.setattr(credential, "app", "")
