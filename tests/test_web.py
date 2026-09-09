"""Tests for `crm serve` — API logic (no socket) plus real HTTP round-trips.

No external network: mail access is mocked. Logic tests drive an in-memory Db;
HTTP tests point the storage backend at a temp SQLite file. Contact ids are now
stable PKs starting at 1 (not list positions).
"""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

from crm import storage, web
from crm.db import Db

TZ = "UTC+02:00"
TZINFO = timezone(timedelta(hours=2))

_FIELDS = ("name", "email", "phone", "company", "role", "source", "stage",
           "next_action", "next_date")


@pytest.fixture(autouse=True)
def _reset_backend():
    saved = storage._backend
    yield
    storage._backend = saved


def _db(contacts=None, **config):
    """In-memory Db seeded with config + contacts (each a fields dict)."""
    db = Db.open_memory()
    db.set_config("timezone", config.pop("timezone", TZ))
    db.set_config("stages", config.pop("stages", ["cold", "contacted", "won"]))
    db.set_config("sources", config.pop("sources", ["cold", "referral"]))
    for k, v in config.items():
        db.set_config(k, v)
    for c in (contacts or []):
        _seed(db, c)
    return db


def _seed(db, over=None, stamp="2026-01-01 23:30"):
    """Insert a contact like the old _contact() default (seeds 'Added to CRM')."""
    fields = {"name": "Ada", "email": "ada@acme.fi", "company": "Acme",
              "source": "cold", "stage": "cold"}
    fields.update({k: v for k, v in (over or {}).items() if k in _FIELDS})
    return db.add_contact(fields, stamp=stamp)


def _today_local():
    return datetime.now(TZINFO).strftime("%Y-%m-%d")


# --- state ----------------------------------------------------------------

def test_build_state():
    db = _db([{}])
    s = web.build_state(db)
    assert s["stages"] == ["cold", "contacted", "won"]
    assert s["sources"] == ["cold", "referral"]
    assert s["contacts"][0]["name"] == "Ada"
    assert s["contacts"][0]["id"] == 1
    assert s["today"] == _today_local()


def test_build_state_localizes_note_dates_without_mutating():
    db = _db()
    c = db.add_contact({"name": "Ada", "stage": "cold"}, note_text=None)
    cid = c["id"]
    # insert oldest-first so newest-first order is [utc, date-only, no date]
    db.add_note(cid, "no date", stamp="")
    db.add_note(cid, "date-only", stamp="2025-12-31")
    db.add_note(cid, "utc stamp", stamp="2026-01-01 23:30")

    s = web.build_state(db)
    dates = [n["date"] for n in s["contacts"][0]["notes"]]
    assert dates == ["2026-01-02 01:30", "2025-12-31", ""]
    # stored data untouched (still UTC)
    assert db.get_contact(cid)["notes"][0]["date"] == "2026-01-01 23:30"


# --- contact identity -----------------------------------------------------

def test_get_contact_rejects_stale_id_or_name():
    db = _db([{}])
    assert web._get_contact(db, {"id": 1, "name": "Ada"})["id"] == 1
    for body in ({"id": 2, "name": "Ada"}, {"id": -1, "name": "Ada"},
                 {"id": 1, "name": "Bob"}, {"id": "1", "name": "Ada"}, {}):
        with pytest.raises(web.StaleError):
            web._get_contact(db, body)


# --- add ------------------------------------------------------------------

def test_api_add_contact_defaults_and_note():
    db = _db()
    res = web.api_add_contact(db, {"fields": {"name": " New ", "email": "n@x.fi", "company": "X"}})
    c = res["contact"]
    assert c["id"] == 1 and c["name"] == "New" and c["company"] == "X"
    assert c["stage"] == "cold" and c["source"] == "cold"
    assert c["next_action"] == "" and c["next_date"] == ""
    assert c["notes"][0]["text"] == "Added to CRM"
    stored = db.get_contact(1)
    assert stored["notes"][0]["text"] == "Added to CRM"
    assert len(stored["notes"][0]["date"]) == 16  # "YYYY-MM-DD HH:MM" UTC stamp
    assert set(stored) == {"id", "name", "email", "phone", "company", "role", "source",
                           "stage", "next_action", "next_date", "account_id", "notes", "stage_history"}
    assert stored["stage_history"] == [{"date": stored["notes"][0]["date"], "from": "", "to": "cold"}]


@pytest.mark.parametrize("fields,msg", [
    ({"name": ""}, "Name is required"),
    ({"name": "X", "email": "nope"}, "Invalid email"),
    ({"name": "X", "stage": "bogus"}, "Invalid stage"),
    ({"name": "X", "source": "bogus"}, "Invalid source"),
])
def test_api_add_contact_validation(fields, msg):
    db = _db()
    with pytest.raises(ValueError, match=msg):
        web.api_add_contact(db, {"fields": fields})
    assert db.list_contacts() == []


# --- update ---------------------------------------------------------------

def test_api_update_contact_stage_change_adds_cmd_stage_note():
    db = _db([{}])
    res = web.api_update_contact(db, {"id": 1, "name": "Ada",
                                      "fields": {"stage": "contacted", "role": "CTO"}})
    c = db.get_contact(1)
    assert c["stage"] == "contacted" and c["role"] == "CTO"
    assert c["notes"][0]["text"] == "Stage: cold → contacted"
    assert c["notes"][1]["text"] == "Added to CRM"
    assert res["contact"]["id"] == 1 and res["contact"]["notes"][0]["text"] == "Stage: cold → contacted"


def test_api_update_contact_can_unset_stage():
    db = _db([{}])
    res = web.api_update_contact(db, {"id": 1, "name": "Ada", "fields": {"stage": ""}})
    assert res["contact"]["stage"] == ""
    c = db.get_contact(1)
    assert c["stage"] == ""
    assert c["notes"][0]["text"] == "Stage: cold → (none)"


def test_api_update_contact_same_stage_no_note_and_ignores_unknown_keys():
    db = _db([{}])
    web.api_update_contact(db, {"id": 1, "name": "Ada",
                                "fields": {"stage": "cold", "name": "Ada L.", "bogus": 1,
                                           "next_action": "sneaky"}})
    c = db.get_contact(1)
    assert c["name"] == "Ada L." and "bogus" not in c and c["next_action"] == ""
    assert len(c["notes"]) == 1


def test_api_update_contact_validation_is_atomic():
    db = _db([{}])
    with pytest.raises(ValueError, match="Invalid stage"):
        web.api_update_contact(db, {"id": 1, "name": "Ada",
                                    "fields": {"role": "CTO", "stage": "bogus"}})
    assert db.get_contact(1)["role"] == ""  # nothing written
    with pytest.raises(ValueError, match="Invalid source"):
        web.api_update_contact(db, {"id": 1, "name": "Ada", "fields": {"source": "bogus"}})
    with pytest.raises(ValueError, match="Invalid email"):
        web.api_update_contact(db, {"id": 1, "name": "Ada", "fields": {"email": "nope"}})
    with pytest.raises(ValueError, match="Name is required"):
        web.api_update_contact(db, {"id": 1, "name": "Ada", "fields": {"name": "  "}})
    with pytest.raises(web.StaleError):
        web.api_update_contact(db, {"id": 1, "name": "Bob", "fields": {"role": "x"}})


# --- note -----------------------------------------------------------------

def test_api_add_note_prepends_and_rejects_blank():
    db = _db([{}])
    res = web.api_add_note(db, {"id": 1, "name": "Ada", "text": "  Called  "})
    notes = db.get_contact(1)["notes"]
    assert notes[0]["text"] == "Called" and notes[1]["text"] == "Added to CRM"
    assert len(notes[0]["date"]) == 16
    assert res["contact"]["notes"][0]["text"] == "Called"
    with pytest.raises(ValueError, match="Note text is required"):
        web.api_add_note(db, {"id": 1, "name": "Ada", "text": "   "})


# --- next -----------------------------------------------------------------

def test_api_set_next_absolute_and_relative():
    db = _db([{}])
    web.api_set_next(db, {"id": 1, "name": "Ada", "action": "Send proposal", "date": "2030-03-04"})
    c = db.get_contact(1)
    assert (c["next_action"], c["next_date"]) == ("Send proposal", "2030-03-04")
    assert len(c["notes"]) == 1  # cmd_next adds no note

    res = web.api_set_next(db, {"id": 1, "name": "Ada", "action": "Call", "date": "+7d"})
    expected = (datetime.now(TZINFO) + timedelta(days=7)).strftime("%Y-%m-%d")
    assert db.get_contact(1)["next_date"] == expected
    assert res["contact"]["next_action"] == "Call"


@pytest.mark.parametrize("body,msg", [
    ({"action": "", "date": "+1d"}, "Action is required"),
    ({"action": "x", "date": ""}, "Due date is required"),
    ({"action": "x", "date": "2030-13-01"}, "Invalid date"),
    ({"action": "x", "date": "+xd"}, "Invalid date"),
    ({"action": "x", "date": "tomorrow"}, "Invalid date"),
])
def test_api_set_next_validation(body, msg):
    db = _db([{}])
    with pytest.raises(ValueError, match=msg):
        web.api_set_next(db, {"id": 1, "name": "Ada", **body})
    assert db.get_contact(1)["next_action"] == ""


# --- done -----------------------------------------------------------------

def test_api_done_notes_and_clears():
    db = _db([{"next_action": "Send proposal", "next_date": "2030-01-01"}])
    res = web.api_done(db, {"id": 1, "name": "Ada"})
    c = db.get_contact(1)
    assert c["next_action"] == "" and c["next_date"] == ""
    assert c["notes"][0]["text"] == "Done: Send proposal"
    assert res["contact"]["notes"][0]["text"] == "Done: Send proposal"


def test_api_done_without_action_is_error():
    db = _db([{}])
    with pytest.raises(ValueError, match="No action set for Ada"):
        web.api_done(db, {"id": 1, "name": "Ada"})
    assert len(db.get_contact(1)["notes"]) == 1


# --- remove ---------------------------------------------------------------

def test_api_remove_contact_soft_deletes():
    db = _db([{}, {"name": "Bob"}])
    assert web.api_remove_contact(db, {"id": 1, "name": "Ada"}) == {"ok": True}
    assert [c["name"] for c in db.list_contacts()] == ["Bob"]
    removed = db.list_removed()
    assert removed[0]["name"] == "Ada"
    assert len(removed[0]["removed_at"]) == 16
    # stable ids: Bob is still id 2, and removing id 1 again is a no-op stale
    with pytest.raises(web.StaleError):
        web.api_remove_contact(db, {"id": 1, "name": "Ada"})
    assert db.get_contact(2)["name"] == "Bob"


# --- thread ---------------------------------------------------------------

def test_api_thread_no_imap_raises():
    with pytest.raises(RuntimeError, match="IMAP not configured"):
        web.api_thread(_db(), "ada@acme.fi")


def test_api_thread_drops_dt_and_keeps_order(monkeypatch):
    db = _db(imap={"host": "h", "user": "me@kitron.dev"})
    seen = {}

    def fake_fetch(cfg, email, tz=None):
        seen["cfg"], seen["email"] = cfg, email
        return [
            {"dt": datetime(2026, 2, 2, tzinfo=timezone.utc), "date": "2026-02-02 10:00",
             "direction": "inbound", "from": "ada@acme.fi", "to": "me@kitron.dev",
             "subject": "Re: hi", "body": "later"},
            {"dt": None, "date": "2026-01-01 09:00",
             "direction": "outbound", "from": "me@kitron.dev", "to": "ada@acme.fi",
             "subject": "hi", "body": "earlier"},
        ]

    monkeypatch.setattr("crm.mail.fetch_thread", fake_fetch)
    out = web.api_thread(db, " ada@acme.fi ")
    assert seen == {"cfg": {"host": "h", "user": "me@kitron.dev"}, "email": "ada@acme.fi"}
    assert [m["body"] for m in out["messages"]] == ["later", "earlier"]
    assert all("dt" not in m for m in out["messages"])
    assert set(out["messages"][0]) == {"date", "direction", "from", "to", "subject", "body", "quoted",
                                       "uid", "folder", "attachments", "event"}
    json.dumps(out)  # serializable


def test_api_thread_blank_email():
    db = _db(imap={"host": "h"})
    with pytest.raises(ValueError):
        web.api_thread(db, "")


# --- import ---------------------------------------------------------------

def test_api_scan_no_imap_raises():
    with pytest.raises(RuntimeError):
        web.api_scan(_db(), None)


def test_api_scan_filters_and_guesses_company(monkeypatch):
    db = _db([{"name": "Known", "email": "known@x.com"}],
             imap={"host": "h", "user": "me@kitron.dev"},
             stages=["cold", "contacted"], sources=["cold"])
    monkeypatch.setattr(
        "crm.mail.fetch_sent_recipients",
        lambda cfg, since_days=None: [
            {"email": "new@acme.fi", "name": "New Lead"},
            {"email": "known@x.com", "name": "Known"},   # existing -> dropped
            {"email": "me@kitron.dev", "name": "Me"},     # own -> dropped
        ],
    )
    out = web.api_scan(db, None)
    assert [c["email"] for c in out["candidates"]] == ["new@acme.fi"]
    assert out["candidates"][0]["company"] == "Acme"


def test_api_commit_adds_and_ignores_with_dedupe():
    db = _db(stages=["cold", "contacted"], sources=["cold"])
    res = web.api_commit(db, {
        "add": [{"email": "a@b.com", "name": "A", "company": "B",
                 "stage": "contacted", "source": "cold"}],
        "ignore": ["x@y.com", "X@Y.com"],  # same address twice
    })
    assert res == {"added": 1, "ignored": 1}
    c = db.list_contacts()[0]
    assert c["email"] == "a@b.com" and c["stage"] == "contacted"
    assert c["notes"][0]["text"] == "Imported from sent mail"
    assert db.get_config("import_ignore") == ["x@y.com"]


def test_api_commit_bad_stage_falls_back():
    db = _db(stages=["cold", "contacted"], sources=["cold"])
    web.api_commit(db, {"add": [{"email": "a@b.com", "stage": "bogus", "source": "bogus"}]})
    c = db.list_contacts()[0]
    assert c["stage"] == "contacted" and c["source"] == "cold"
    assert c["name"] == "a"  # derived from local part when name missing


# --- HTTP layer ----------------------------------------------------------

def _serve(tmp_path, contacts=None, **config):
    """Seed a temp SQLite backend and start a handler. Returns (httpd, port)."""
    path = tmp_path / "crm.db"
    storage.use_local_path(path)
    db = storage.open_db()
    db.set_config("timezone", config.pop("timezone", TZ))
    db.set_config("stages", config.pop("stages", ["cold", "contacted", "won"]))
    db.set_config("sources", config.pop("sources", ["cold", "referral"]))
    for k, v in config.items():
        db.set_config(k, v)
    for c in (contacts or []):
        _seed(db, c)
    storage.push_db(db)
    storage.close_db(db)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    httpd.token = "secret"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _post(port, path, body, token="secret"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"X-CRM-Token": token, "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _reopen():
    db = storage.open_db()
    try:
        return db.list_contacts()
    finally:
        storage.close_db(db)


def test_http_serves_index_and_gates_api(tmp_path):
    httpd, port = _serve(tmp_path, stages=["cold"], sources=["cold"])
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/")
        assert r.status == 200
        assert b"<!doctype html>" in r.read().lower()

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state")
        assert exc.value.code == 403

        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
                                     headers={"X-CRM-Token": "secret"})
        body = json.loads(urllib.request.urlopen(req).read())
        assert body["stages"] == ["cold"]
        assert body["today"] == _today_local()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_post_mutation_saves_and_maps_errors(tmp_path):
    httpd, port = _serve(tmp_path, [{}])
    try:
        # happy path: note added, persisted, contact returned with id + local date
        status, body = _post(port, "/api/contacts/note", {"id": 1, "name": "Ada", "text": "Called"})
        assert status == 200
        assert body["contact"]["id"] == 1
        assert body["contact"]["notes"][0]["text"] == "Called"
        assert body["contact"]["notes"][1]["date"] == "2026-01-02 01:30"  # localized
        assert _reopen()[0]["notes"][0]["text"] == "Called"

        # stale identity -> 409
        status, body = _post(port, "/api/contacts/note", {"id": 1, "name": "Bob", "text": "x"})
        assert status == 409 and body == {"error": "Contact changed — reload and retry."}

        # validation -> 400
        status, body = _post(port, "/api/contacts/next", {"id": 1, "name": "Ada", "action": "x", "date": "soon"})
        assert status == 400 and "Invalid date" in body["error"]

        # thread without IMAP -> 400 via GET
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/thread?email=a@b.c",
                                     headers={"X-CRM-Token": "secret"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 400
        assert "IMAP not configured" in json.loads(exc.value.read())["error"]

        # unknown route -> 404; bad token -> 403
        assert _post(port, "/api/contacts/nope", {})[0] == 404
        assert _post(port, "/api/contacts/note", {}, token="wrong")[0] == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_concurrent_write_is_409(tmp_path, monkeypatch):
    from crm.storage.errors import ConcurrentWriteError

    httpd, port = _serve(tmp_path, [{"next_action": "Call", "next_date": "2030-01-01"}])

    def boom(_db):
        raise ConcurrentWriteError("etag mismatch")

    try:
        # validation runs before the push, so a no-op still succeeds pre-push;
        # with push stubbed to conflict, a real mutation surfaces 409.
        monkeypatch.setattr(web, "push_db", boom)
        status, body = _post(port, "/api/contacts/done", {"id": 1, "name": "Ada"})
        assert status == 409 and "changed elsewhere" in body["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
