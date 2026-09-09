"""Tests for `crm import` — Sent-folder contact harvesting.

No network: imaplib is faked and the mail fetch is stubbed; data lives in a
temp SQLite backend.
"""

import sys

import pytest

from crm import cli, mail, storage


@pytest.fixture(autouse=True)
def _reset_backend():
    saved = storage._backend
    yield
    storage._backend = saved


# --- pure helpers -------------------------------------------------------

def test_noise_addresses_detected():
    for addr in ["no-reply@x.com", "noreply@x.com", "mailer-daemon@x.com",
                 "postmaster@x.com", "bounces@x.com", "do-not-reply@x.com"]:
        assert cli._is_noise_address(addr), addr


def test_real_person_not_noise():
    assert not cli._is_noise_address("matti@konepaja.fi")
    assert not cli._is_noise_address("liisa.virtanen@example.com")


def test_company_from_corporate_domain():
    assert cli._company_from_email("matti@konepaja.fi") == "Konepaja"
    assert cli._company_from_email("j@acme-corp.co.uk") == "Acme-corp"


def test_company_blank_for_freemail():
    assert cli._company_from_email("someone@gmail.com") == ""
    assert cli._company_from_email("someone@outlook.com") == ""


def test_is_ignored_exact_and_domain():
    ignore = {"foo@bar.com", "@vendor.com"}
    assert cli._is_ignored("foo@bar.com", ignore)          # exact
    assert cli._is_ignored("FOO@BAR.com", ignore)          # case-insensitive
    assert cli._is_ignored("anyone@vendor.com", ignore)    # whole-domain entry
    assert not cli._is_ignored("foo@other.com", ignore)
    assert not cli._is_ignored("bar@bar.com", ignore)      # only foo@ is listed


# --- fetch_sent_recipients (mocked IMAP) --------------------------------

def test_imap_mailbox_quoting():
    assert mail._imap_mailbox("INBOX") == "INBOX"
    assert mail._imap_mailbox("Sent") == "Sent"
    assert mail._imap_mailbox("Sent Items") == '"Sent Items"'
    assert mail._imap_mailbox('"Sent Items"') == '"Sent Items"'  # already quoted
    assert mail._imap_mailbox('Odd"name') == '"Odd\\"name"'


class FakeIMAP:
    def __init__(self, messages):
        # messages: list of raw header byte-blobs, oldest first
        self._messages = messages
        self.selected = None
        self.fetch_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def select(self, folder, readonly=False):
        self.selected = folder
        return "OK", [str(len(self._messages)).encode()]

    def search(self, charset, *criteria):
        ids = " ".join(str(i + 1) for i in range(len(self._messages)))
        return "OK", [ids.encode()]

    def fetch(self, msg_set, spec):
        # Mimic imaplib's batched response: one (envelope, header) tuple per
        # message, each followed by a bare b")" token.
        self.fetch_calls += 1
        out = []
        for tok in msg_set.split(","):
            idx = int(tok) - 1
            out.append((b"%d (headers)" % (idx + 1), self._messages[idx]))
            out.append(b")")
        return "OK", out


def test_fetch_dedupes_and_keeps_display_name(monkeypatch):
    monkeypatch.setattr(mail, "_imap_authenticate", lambda m, cfg: None)
    msgs = [
        b'To: "Matti M" <matti@konepaja.fi>\r\nCc: liisa@virtanen.fi\r\n\r\n',
        b'To: matti@konepaja.fi\r\n\r\n',  # same person, no display name, newer
    ]
    fake = FakeIMAP(msgs)
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda host, port, **kw: fake)

    out = mail.fetch_sent_recipients({"host": "h", "sent_folder": "Sent Items"})
    # The spaced folder must have been quoted for the EXAMINE/SELECT command.
    assert fake.selected == '"Sent Items"'
    # Both messages fetched in a single batched FETCH, not one call each.
    assert fake.fetch_calls == 1
    emails = [r["email"] for r in out]
    assert set(emails) == {"matti@konepaja.fi", "liisa@virtanen.fi"}
    matti = next(r for r in out if r["email"] == "matti@konepaja.fi")
    assert matti["name"] == "Matti M"        # kept from the first message
    # Most-recently-emailed first: matti appears in the newer message.
    assert emails[0] == "matti@konepaja.fi"


def test_fetch_empty_folder(monkeypatch):
    monkeypatch.setattr(mail, "_imap_authenticate", lambda m, cfg: None)

    class Empty(FakeIMAP):
        def search(self, charset, *criteria):
            return "OK", [b""]

    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda host, port, **kw: Empty([]))
    assert mail.fetch_sent_recipients({"host": "h"}) == []


# --- cmd_import filtering (dry-run path) --------------------------------

def _seed(tmp_path, config, contacts=()):
    storage.use_local_path(tmp_path / "crm.db")
    db = storage.open_db()
    for k, v in config.items():
        db.set_config(k, v)
    for c in contacts:
        db.add_contact(c)
    storage.push_db(db)
    storage.close_db(db)


def _config_after():
    db = storage.open_db()
    try:
        return db.all_config()
    finally:
        storage.close_db(db)


def test_import_filters_existing_own_and_noise(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, {
        "imap": {"host": "h", "user": "me@kitron.dev"},
        "smtp": {"user": "me@kitron.dev"},
        "stages": ["cold", "contacted"], "sources": ["cold", "referral"],
    }, [{"name": "Known", "email": "known@x.com", "stage": "cold"}])
    monkeypatch.setattr(
        "crm.mail.fetch_sent_recipients",
        lambda cfg, since_days=None: [
            {"email": "known@x.com", "name": "Known"},        # already a contact
            {"email": "me@kitron.dev", "name": "Me"},          # own address
            {"email": "no-reply@corp.com", "name": ""},        # noise
            {"email": "new@konepaja.fi", "name": "New Lead"},  # keeper
        ],
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)  # forces listing, no add

    cli.cmd_import(["--dry-run"])
    out = capsys.readouterr().out
    assert "new@konepaja.fi" in out
    assert "known@x.com" not in out
    assert "me@kitron.dev" not in out
    assert "no-reply@corp.com" not in out
    assert "1 new contact" in out


def test_import_respects_ignore_list(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, {
        "imap": {"host": "h", "user": "me@kitron.dev"},
        "stages": ["cold", "contacted"], "sources": ["cold"],
        "import_ignore": ["skip@me.com", "@vendor.com"],
    })
    monkeypatch.setattr(
        "crm.mail.fetch_sent_recipients",
        lambda cfg, since_days=None: [
            {"email": "skip@me.com", "name": "Exact"},      # ignored (exact)
            {"email": "rep@vendor.com", "name": "Vendor"},  # ignored (domain)
            {"email": "keep@konepaja.fi", "name": "Keep"},  # keeper
        ],
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    cli.cmd_import(["--dry-run"])
    out = capsys.readouterr().out
    assert "keep@konepaja.fi" in out
    assert "skip@me.com" not in out
    assert "rep@vendor.com" not in out
    assert "1 new contact" in out


def test_import_i_choice_persists_to_ignore(tmp_path, monkeypatch):
    _seed(tmp_path, {"imap": {"host": "h", "user": "me@kitron.dev"},
                     "stages": ["cold", "contacted"], "sources": ["cold"]})
    monkeypatch.setattr(
        "crm.mail.fetch_sent_recipients",
        lambda cfg, since_days=None: [{"email": "junk@spam.com", "name": "Junk"}],
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "i")  # ignore the one candidate

    cli.cmd_import([])
    cfg = _config_after()
    assert "junk@spam.com" in cfg["import_ignore"]
    db = storage.open_db()
    try:
        assert db.list_contacts() == []  # nothing added
    finally:
        storage.close_db(db)
