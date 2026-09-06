"""Tests for Microsoft 365 XOAUTH2 support in mail.py / msauth.py.

No network is touched: msal is mocked and SMTP/IMAP sessions are fakes.
Covers the base64-vs-raw asymmetry, config validation, the password path
staying untouched, and the silent-hit / device-flow / non-tty token paths.
"""

import base64
import sys

import pytest

from crm import mail, msauth


# --- auth-string construction -------------------------------------------

def test_build_xoauth2_string_is_raw_sasl():
    s = msauth.build_xoauth2_string("me@x.com", "TOK")
    assert s == "user=me@x.com\x01auth=Bearer TOK\x01\x01"


def test_smtp_path_base64_encodes(monkeypatch):
    """SMTP sends AUTH XOAUTH2 <base64>; decoding it recovers the raw SASL."""
    monkeypatch.setattr(msauth, "get_token", lambda cfg: "TOK")
    sent = {}
    order = []

    class FakeSMTP:
        def ehlo(self, *a):
            order.append("ehlo")
            return 250, b"ok"

        def docmd(self, cmd, arg):
            order.append("docmd")
            sent["cmd"], sent["arg"] = cmd, arg
            return 235, b"2.7.0 Authentication successful"

    mail._smtp_authenticate(FakeSMTP(), {"auth": "oauth-ms", "user": "me@x.com"})
    # EHLO must precede AUTH or Exchange replies 503 "Send hello first".
    assert order == ["ehlo", "docmd"]
    assert sent["cmd"] == "AUTH"
    mechanism, blob = sent["arg"].split(" ", 1)
    assert mechanism == "XOAUTH2"
    decoded = base64.b64decode(blob).decode()
    assert decoded == "user=me@x.com\x01auth=Bearer TOK\x01\x01"


def test_smtp_oauth_failure_maps_535(monkeypatch):
    monkeypatch.setattr(msauth, "get_token", lambda cfg: "TOK")

    class FakeSMTP:
        def ehlo(self, *a):
            return 250, b"ok"

        def docmd(self, cmd, arg):
            return 535, b"5.7.3 Authentication unsuccessful"

    with pytest.raises(mail.MailAuthError) as exc:
        mail._smtp_authenticate(FakeSMTP(), {"auth": "oauth-ms", "user": "me@x.com"})
    msg = str(exc.value)
    assert "535" in msg
    assert "admin-consented" in msg
    assert "TOK" not in msg  # never leak the token


def test_imap_path_returns_raw_bytes_not_base64(monkeypatch):
    """imaplib base64-encodes internally, so the authobject returns RAW bytes."""
    monkeypatch.setattr(msauth, "get_token", lambda cfg: "TOK")
    captured = {}

    class FakeIMAP:
        def authenticate(self, mechanism, authobject):
            captured["mechanism"] = mechanism
            captured["value"] = authobject(b"")  # server challenge ignored
            return "OK", [b"success"]

    mail._imap_authenticate(FakeIMAP(), {"auth": "oauth-ms", "user": "me@x.com"})
    assert captured["mechanism"] == "XOAUTH2"
    # RAW SASL bytes, NOT base64 — this is the asymmetry vs SMTP.
    assert captured["value"] == b"user=me@x.com\x01auth=Bearer TOK\x01\x01"


# --- password path unaffected -------------------------------------------

def test_smtp_password_path_calls_login():
    calls = {}

    class FakeSMTP:
        def login(self, user, password):
            calls["login"] = (user, password)

        def docmd(self, *a):  # pragma: no cover - must not be reached
            raise AssertionError("password path must not send AUTH XOAUTH2")

    mail._smtp_authenticate(FakeSMTP(), {"user": "u", "password": "p"})
    assert calls["login"] == ("u", "p")


def test_imap_absent_auth_defaults_to_password():
    calls = {}

    class FakeIMAP:
        def login(self, user, password):
            calls["login"] = (user, password)

    # No "auth" key at all == password behavior.
    mail._imap_authenticate(FakeIMAP(), {"user": "u", "password": "p"})
    assert calls["login"] == ("u", "p")


# --- config validation --------------------------------------------------

@pytest.mark.parametrize("cfg", [
    {"auth": "oauth-ms", "user": "u", "tenant_id": "t"},          # no client_id
    {"auth": "oauth-ms", "user": "u", "client_id": "c"},          # no tenant_id
    {"auth": "oauth-ms", "client_id": "c", "tenant_id": "t"},     # no user
])
def test_validate_oauth_config_missing_fields(cfg):
    with pytest.raises(msauth.OAuthError):
        msauth.validate_oauth_config(cfg)


def test_validate_oauth_config_complete_ok():
    msauth.validate_oauth_config(
        {"auth": "oauth-ms", "user": "u", "client_id": "c", "tenant_id": "t"}
    )  # no raise


# --- token acquisition (msal mocked) ------------------------------------

FULL_CFG = {"auth": "oauth-ms", "user": "me@x.com", "client_id": "c", "tenant_id": "t"}


class FakeCache:
    has_state_changed = False

    def deserialize(self, s):
        pass

    def serialize(self):
        return "{}"


def _install_fake_msal(monkeypatch, app):
    """Put a fake msal module in place for the in-function `import msal`."""
    import types

    fake = types.SimpleNamespace(
        SerializableTokenCache=FakeCache,
        PublicClientApplication=lambda *a, **k: app,
    )
    monkeypatch.setitem(sys.modules, "msal", fake)


def test_get_token_silent_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(msauth, "CACHE_FILE", tmp_path / "cache.json")

    class App:
        def get_accounts(self, username=None):
            return [{"username": username}]

        def acquire_token_silent(self, scopes, account):
            assert scopes == msauth.SCOPES
            return {"access_token": "SILENT"}

        def initiate_device_flow(self, scopes):  # pragma: no cover
            raise AssertionError("silent hit must not start device flow")

    _install_fake_msal(monkeypatch, App())
    assert msauth.get_token(FULL_CFG) == "SILENT"


def test_get_token_silent_miss_runs_device_flow(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(msauth, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    class App:
        def get_accounts(self, username=None):
            return []

        def acquire_token_silent(self, scopes, account):  # pragma: no cover
            raise AssertionError("no accounts → silent should be skipped")

        def initiate_device_flow(self, scopes):
            return {"user_code": "ABC123", "message": "Go to https://microsoft.com/devicelogin and enter ABC123"}

        def acquire_token_by_device_flow(self, flow):
            return {"access_token": "DEVICE"}

    _install_fake_msal(monkeypatch, App())
    assert msauth.get_token(FULL_CFG) == "DEVICE"
    # Device instructions go to stderr, not stdout.
    err = capsys.readouterr().err
    assert "devicelogin" in err
    assert "ABC123" in err


def test_get_token_non_tty_fails_fast(monkeypatch, tmp_path):
    monkeypatch.setattr(msauth, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    class App:
        def get_accounts(self, username=None):
            return []

        def initiate_device_flow(self, scopes):  # pragma: no cover
            raise AssertionError("non-tty must not start device flow")

    _install_fake_msal(monkeypatch, App())
    with pytest.raises(msauth.OAuthError) as exc:
        msauth.get_token(FULL_CFG)
    assert "interactive" in str(exc.value).lower()


def test_get_token_missing_msal(monkeypatch, tmp_path):
    monkeypatch.setattr(msauth, "CACHE_FILE", tmp_path / "cache.json")
    # `import msal` raises ImportError when the module entry is None.
    monkeypatch.setitem(sys.modules, "msal", None)
    with pytest.raises(msauth.OAuthError) as exc:
        msauth.get_token(FULL_CFG)
    assert "pip install msal" in str(exc.value)


def test_get_token_failure_includes_error_description(monkeypatch, tmp_path):
    monkeypatch.setattr(msauth, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    class App:
        def get_accounts(self, username=None):
            return []

        def initiate_device_flow(self, scopes):
            return {"user_code": "X", "message": "msg"}

        def acquire_token_by_device_flow(self, flow):
            return {"error": "expired_token", "error_description": "AADSTS70020: code expired"}

    _install_fake_msal(monkeypatch, App())
    with pytest.raises(msauth.OAuthError) as exc:
        msauth.get_token(FULL_CFG)
    assert "AADSTS70020" in str(exc.value)


def test_get_token_public_client_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(msauth, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    class App:
        def get_accounts(self, username=None):
            return []

        def initiate_device_flow(self, scopes):
            return {"user_code": "X", "message": "msg"}

        def acquire_token_by_device_flow(self, flow):
            return {
                "error": "invalid_client",
                "error_description": "AADSTS7000218: The request body must contain "
                "the following parameter: 'client_assertion' or 'client_secret'.",
            }

    _install_fake_msal(monkeypatch, App())
    with pytest.raises(msauth.OAuthError) as exc:
        msauth.get_token(FULL_CFG)
    msg = str(exc.value)
    assert "AADSTS7000218" in msg
    assert "Allow public client flows" in msg
