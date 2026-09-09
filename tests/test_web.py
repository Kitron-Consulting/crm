"""Tests for `crm serve` — API logic (no socket) plus real HTTP round-trips.

No external network: mail access is mocked and load_data/save_data are
stubbed. Every data dict sets config.timezone explicitly, because get_tz()
persists an auto-detected timezone (via the real save_data) when it's absent.
"""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

from crm import web

TZ = "UTC+02:00"
TZINFO = timezone(timedelta(hours=2))


def _data(contacts=None, **config):
    cfg = {"timezone": TZ, "stages": ["cold", "contacted", "won"], "sources": ["cold", "referral"]}
    cfg.update(config)
    return {"contacts": contacts if contacts is not None else [], "removed": [], "config": cfg}


def _contact(**over):
    c = {"name": "Ada", "email": "ada@acme.fi", "phone": "", "company": "Acme", "role": "",
         "source": "cold", "stage": "cold", "next_action": "", "next_date": "",
         "notes": [{"date": "2026-01-01 23:30", "text": "Added to CRM"}]}
    c.update(over)
    return c


def _today_local():
    return datetime.now(TZINFO).strftime("%Y-%m-%d")


# --- state ----------------------------------------------------------------

def test_build_state():
    data = _data([_contact()])
    s = web.build_state(data)
    assert s["stages"] == ["cold", "contacted", "won"]
    assert s["sources"] == ["cold", "referral"]
    assert s["contacts"][0]["name"] == "Ada"
    assert s["contacts"][0]["id"] == 0
    assert s["today"] == _today_local()


def test_build_state_localizes_note_dates_without_mutating():
    data = _data([_contact(notes=[
        {"date": "2026-01-01 23:30", "text": "utc stamp"},   # crosses midnight in UTC+2
        {"date": "2025-12-31", "text": "date-only"},
        {"date": "", "text": "no date"},
    ])])
    s = web.build_state(data)
    dates = [n["date"] for n in s["contacts"][0]["notes"]]
    assert dates == ["2026-01-02 01:30", "2025-12-31", ""]
    # stored data untouched
    assert data["contacts"][0]["notes"][0]["date"] == "2026-01-01 23:30"
    assert "id" not in data["contacts"][0]


# --- contact identity -----------------------------------------------------

def test_get_contact_rejects_stale_id_or_name():
    data = _data([_contact()])
    assert web._get_contact(data, {"id": 0, "name": "Ada"})[0] == 0
    for body in ({"id": 1, "name": "Ada"}, {"id": -1, "name": "Ada"},
                 {"id": 0, "name": "Bob"}, {"id": "0", "name": "Ada"}, {}):
        with pytest.raises(web.StaleError):
            web._get_contact(data, body)


# --- add ------------------------------------------------------------------

def test_api_add_contact_defaults_and_note():
    data = _data()
    res = web.api_add_contact(data, {"fields": {"name": " New ", "email": "n@x.fi", "company": "X"}})
    c = res["contact"]
    assert c["id"] == 0 and c["name"] == "New" and c["company"] == "X"
    assert c["stage"] == "cold" and c["source"] == "cold"
    assert c["next_action"] == "" and c["next_date"] == ""
    assert c["notes"][0]["text"] == "Added to CRM"
    stored = data["contacts"][0]
    assert stored["notes"][0]["text"] == "Added to CRM"
    assert len(stored["notes"][0]["date"]) == 16  # "YYYY-MM-DD HH:MM" UTC stamp
    assert set(stored) == {"name", "email", "phone", "company", "role", "source", "stage",
                           "next_action", "next_date", "notes", "stage_history"}
    assert stored["stage_history"] == [{"date": stored["notes"][0]["date"], "from": "", "to": "cold"}]


@pytest.mark.parametrize("fields,msg", [
    ({"name": ""}, "Name is required"),
    ({"name": "X", "email": "nope"}, "Invalid email"),
    ({"name": "X", "stage": "bogus"}, "Invalid stage"),
    ({"name": "X", "source": "bogus"}, "Invalid source"),
])
def test_api_add_contact_validation(fields, msg):
    data = _data()
    with pytest.raises(ValueError, match=msg):
        web.api_add_contact(data, {"fields": fields})
    assert data["contacts"] == []


# --- update ---------------------------------------------------------------

def test_api_update_contact_stage_change_adds_cmd_stage_note():
    data = _data([_contact()])
    res = web.api_update_contact(data, {"id": 0, "name": "Ada",
                                        "fields": {"stage": "contacted", "role": "CTO"}})
    c = data["contacts"][0]
    assert c["stage"] == "contacted" and c["role"] == "CTO"
    assert c["notes"][0]["text"] == "Stage: cold → contacted"
    assert c["notes"][1]["text"] == "Added to CRM"
    assert res["contact"]["id"] == 0 and res["contact"]["notes"][0]["text"] == "Stage: cold → contacted"


def test_api_update_contact_same_stage_no_note_and_ignores_unknown_keys():
    data = _data([_contact()])
    web.api_update_contact(data, {"id": 0, "name": "Ada",
                                  "fields": {"stage": "cold", "name": "Ada L.", "bogus": 1,
                                             "next_action": "sneaky"}})
    c = data["contacts"][0]
    assert c["name"] == "Ada L." and "bogus" not in c and c["next_action"] == ""
    assert len(c["notes"]) == 1


def test_api_update_contact_validation_is_atomic():
    data = _data([_contact()])
    with pytest.raises(ValueError, match="Invalid stage"):
        web.api_update_contact(data, {"id": 0, "name": "Ada",
                                      "fields": {"role": "CTO", "stage": "bogus"}})
    assert data["contacts"][0]["role"] == ""  # nothing written
    with pytest.raises(ValueError, match="Invalid source"):
        web.api_update_contact(data, {"id": 0, "name": "Ada", "fields": {"source": "bogus"}})
    with pytest.raises(ValueError, match="Invalid email"):
        web.api_update_contact(data, {"id": 0, "name": "Ada", "fields": {"email": "nope"}})
    with pytest.raises(ValueError, match="Name is required"):
        web.api_update_contact(data, {"id": 0, "name": "Ada", "fields": {"name": "  "}})
    with pytest.raises(web.StaleError):
        web.api_update_contact(data, {"id": 0, "name": "Bob", "fields": {"role": "x"}})


# --- note -----------------------------------------------------------------

def test_api_add_note_prepends_and_rejects_blank():
    data = _data([_contact()])
    res = web.api_add_note(data, {"id": 0, "name": "Ada", "text": "  Called  "})
    notes = data["contacts"][0]["notes"]
    assert notes[0]["text"] == "Called" and notes[1]["text"] == "Added to CRM"
    assert len(notes[0]["date"]) == 16
    assert res["contact"]["notes"][0]["text"] == "Called"
    with pytest.raises(ValueError, match="Note text is required"):
        web.api_add_note(data, {"id": 0, "name": "Ada", "text": "   "})


# --- next -----------------------------------------------------------------

def test_api_set_next_absolute_and_relative():
    data = _data([_contact()])
    web.api_set_next(data, {"id": 0, "name": "Ada", "action": "Send proposal", "date": "2030-03-04"})
    c = data["contacts"][0]
    assert (c["next_action"], c["next_date"]) == ("Send proposal", "2030-03-04")
    assert len(c["notes"]) == 1  # cmd_next adds no note

    res = web.api_set_next(data, {"id": 0, "name": "Ada", "action": "Call", "date": "+7d"})
    expected = (datetime.now(TZINFO) + timedelta(days=7)).strftime("%Y-%m-%d")
    assert c["next_date"] == expected
    assert res["contact"]["next_action"] == "Call"


@pytest.mark.parametrize("body,msg", [
    ({"action": "", "date": "+1d"}, "Action is required"),
    ({"action": "x", "date": ""}, "Due date is required"),
    ({"action": "x", "date": "2030-13-01"}, "Invalid date"),
    ({"action": "x", "date": "+xd"}, "Invalid date"),
    ({"action": "x", "date": "tomorrow"}, "Invalid date"),
])
def test_api_set_next_validation(body, msg):
    data = _data([_contact()])
    with pytest.raises(ValueError, match=msg):
        web.api_set_next(data, {"id": 0, "name": "Ada", **body})
    assert data["contacts"][0]["next_action"] == ""


# --- done -----------------------------------------------------------------

def test_api_done_notes_and_clears():
    data = _data([_contact(next_action="Send proposal", next_date="2030-01-01")])
    res = web.api_done(data, {"id": 0, "name": "Ada"})
    c = data["contacts"][0]
    assert c["next_action"] == "" and c["next_date"] == ""
    assert c["notes"][0]["text"] == "Done: Send proposal"
    assert res["contact"]["notes"][0]["text"] == "Done: Send proposal"


def test_api_done_without_action_is_error():
    data = _data([_contact()])
    with pytest.raises(ValueError, match="No action set for Ada"):
        web.api_done(data, {"id": 0, "name": "Ada"})
    assert len(data["contacts"][0]["notes"]) == 1


# --- remove ---------------------------------------------------------------

def test_api_remove_contact_soft_deletes():
    data = _data([_contact(), _contact(name="Bob")])
    assert web.api_remove_contact(data, {"id": 0, "name": "Ada"}) == {"ok": True}
    assert [c["name"] for c in data["contacts"]] == ["Bob"]
    assert data["removed"][0]["name"] == "Ada"
    assert len(data["removed"][0]["removed_at"]) == 16
    # Bob is now id 0; the old id 1 is stale
    with pytest.raises(web.StaleError):
        web.api_remove_contact(data, {"id": 1, "name": "Bob"})


def test_api_remove_contact_creates_removed_list():
    data = _data([_contact()])
    del data["removed"]
    web.api_remove_contact(data, {"id": 0, "name": "Ada"})
    assert data["removed"][0]["name"] == "Ada"


# --- thread ---------------------------------------------------------------

def test_api_thread_no_imap_raises():
    with pytest.raises(RuntimeError, match="IMAP not configured"):
        web.api_thread(_data(), "ada@acme.fi")


def test_api_thread_drops_dt_and_keeps_order(monkeypatch):
    data = _data(imap={"host": "h", "user": "me@kitron.dev"})
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
    out = web.api_thread(data, " ada@acme.fi ")
    assert seen == {"cfg": {"host": "h", "user": "me@kitron.dev"}, "email": "ada@acme.fi"}
    assert [m["body"] for m in out["messages"]] == ["later", "earlier"]
    assert all("dt" not in m for m in out["messages"])
    assert set(out["messages"][0]) == {"date", "direction", "from", "to", "subject", "body", "quoted",
                                       "uid", "folder", "attachments", "event"}
    json.dumps(out)  # serializable


def test_api_thread_blank_email():
    data = _data(imap={"host": "h"})
    with pytest.raises(ValueError):
        web.api_thread(data, "")


# --- import (unchanged) ---------------------------------------------------

def test_api_scan_no_imap_raises():
    with pytest.raises(RuntimeError):
        web.api_scan({"contacts": [], "config": {}}, None)


def test_api_scan_filters_and_guesses_company(monkeypatch):
    data = {"contacts": [{"name": "Known", "email": "known@x.com"}],
            "config": {"imap": {"host": "h", "user": "me@kitron.dev"},
                       "stages": ["cold", "contacted"], "sources": ["cold"]}}
    monkeypatch.setattr(
        "crm.mail.fetch_sent_recipients",
        lambda cfg, since_days=None: [
            {"email": "new@acme.fi", "name": "New Lead"},
            {"email": "known@x.com", "name": "Known"},   # existing -> dropped
            {"email": "me@kitron.dev", "name": "Me"},     # own -> dropped
        ],
    )
    out = web.api_scan(data, None)
    assert [c["email"] for c in out["candidates"]] == ["new@acme.fi"]
    assert out["candidates"][0]["company"] == "Acme"


def test_api_commit_adds_and_ignores_with_dedupe():
    data = {"contacts": [], "config": {"stages": ["cold", "contacted"], "sources": ["cold"]}}
    res = web.api_commit(data, {
        "add": [{"email": "a@b.com", "name": "A", "company": "B",
                 "stage": "contacted", "source": "cold"}],
        "ignore": ["x@y.com", "X@Y.com"],  # same address twice
    })
    assert res == {"added": 1, "ignored": 1}
    c = data["contacts"][0]
    assert c["email"] == "a@b.com" and c["stage"] == "contacted"
    assert c["notes"][0]["text"] == "Imported from sent mail"
    assert data["config"]["import_ignore"] == ["x@y.com"]


def test_api_commit_bad_stage_falls_back():
    data = {"contacts": [], "config": {"stages": ["cold", "contacted"], "sources": ["cold"]}}
    web.api_commit(data, {"add": [{"email": "a@b.com", "stage": "bogus", "source": "bogus"}]})
    c = data["contacts"][0]
    assert c["stage"] == "contacted" and c["source"] == "cold"
    assert c["name"] == "a"  # derived from local part when name missing


# --- HTTP layer ----------------------------------------------------------

def _serve(monkeypatch, data, saved):
    """Start a handler on an ephemeral port with storage stubbed. Returns (httpd, port)."""
    monkeypatch.setattr(web, "load_data", lambda: data)
    monkeypatch.setattr(web, "save_data", lambda d: saved.append(d))
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


def test_http_serves_index_and_gates_api(monkeypatch):
    httpd, port = _serve(monkeypatch, _data(stages=["cold"], sources=["cold"]), [])
    try:
        # index page, no token needed
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/")
        assert r.status == 200
        assert b"<!doctype html>" in r.read().lower()

        # /api without token -> 403
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state")
        assert exc.value.code == 403

        # /api with token -> 200 + state
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
                                     headers={"X-CRM-Token": "secret"})
        body = json.loads(urllib.request.urlopen(req).read())
        assert body["stages"] == ["cold"]
        assert body["today"] == _today_local()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_post_mutation_saves_and_maps_errors(monkeypatch):
    data = _data([_contact()])
    saved = []
    httpd, port = _serve(monkeypatch, data, saved)
    try:
        # happy path: note added, persisted, contact returned with id + local date
        status, body = _post(port, "/api/contacts/note", {"id": 0, "name": "Ada", "text": "Called"})
        assert status == 200
        assert body["contact"]["id"] == 0
        assert body["contact"]["notes"][0]["text"] == "Called"
        assert body["contact"]["notes"][1]["date"] == "2026-01-02 01:30"  # localized
        assert saved == [data] and data["contacts"][0]["notes"][0]["text"] == "Called"

        # stale identity -> 409, nothing saved
        status, body = _post(port, "/api/contacts/note", {"id": 0, "name": "Bob", "text": "x"})
        assert status == 409 and body == {"error": "Contact changed — reload and retry."}

        # validation -> 400
        status, body = _post(port, "/api/contacts/next", {"id": 0, "name": "Ada", "action": "x", "date": "soon"})
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
        assert len(saved) == 1
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_concurrent_write_is_409(monkeypatch):
    from crm.storage.errors import ConcurrentWriteError

    data = _data([_contact()])
    monkeypatch.setattr(web, "load_data", lambda: data)

    def boom(d):
        raise ConcurrentWriteError("etag mismatch")

    monkeypatch.setattr(web, "save_data", boom)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    httpd.token = "secret"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, body = _post(httpd.server_address[1], "/api/contacts/done", {"id": 0, "name": "Ada"})
        # no pending action -> 400 takes precedence (validation before save)
        assert status == 400
        data["contacts"][0]["next_action"] = "Call"
        status, body = _post(httpd.server_address[1], "/api/contacts/done", {"id": 0, "name": "Ada"})
        assert status == 409 and "changed elsewhere" in body["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
