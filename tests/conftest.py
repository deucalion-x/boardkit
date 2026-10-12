import pytest

from boardkit import credential


@pytest.fixture(autouse=True)
def inherited_credential(monkeypatch):
    """The CLI points `gh` at a focused token per run. A test that went
    through it must not leave the next one needing a keychain."""
    monkeypatch.setattr(credential, "repo", None)
    monkeypatch.setattr(credential, "as_me", False)
    monkeypatch.setattr(credential, "app", "")


@pytest.fixture(autouse=True)
def no_inherited_usage(monkeypatch):
    """Usage totals go wherever the last store pointed them. A test that made
    one must not count the next test's calls into its file."""
    from boardkit import quota
    monkeypatch.setattr(quota, "_path", None)
    monkeypatch.setattr(quota, "_verb", "board")
