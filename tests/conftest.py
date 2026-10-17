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


@pytest.fixture(autouse=True)
def no_real_token(monkeypatch):
    """Which token is in use is read with `gh auth token`. A test must not reach
    the machine's own login for it, so it is unknown unless a test says."""
    from boardkit import auth
    monkeypatch.setattr(auth, "fingerprint", lambda: "")


@pytest.fixture(autouse=True)
def no_daily_doctor(monkeypatch):
    """`board` mentions installation problems once a day, from the real home.
    A test must neither read that home nor leave its stamp there."""
    monkeypatch.setenv("BOARDKIT_NO_DOCTOR", "1")


@pytest.fixture(autouse=True)
def no_real_identities(monkeypatch):
    """The coding sign-in and the person's gh login live on the machine. A test
    must not read either, so neither exists unless a test says."""
    from boardkit import coding, doctor
    monkeypatch.setattr(coding, "stored", lambda: None)
    monkeypatch.setattr(doctor, "personal_login", lambda: False)
