"""Next-meeting feature: pure correlation, DAL cache, and the web endpoint.
The live IMAP scan (mail.fetch_upcoming_meetings) is mocked — only its pure
folding (mail.upcoming_meetings) and the DAL/web wiring are exercised here."""

from crm import mail, web
from crm.db import Db

NOW = "2026-09-10 12:00"


# ---------------- pure folding ----------------

def _ev(start, summary="Sync", join_url="", status="event"):
    return {"start": start, "end": "", "summary": summary, "status": status,
            "location": "", "organizer": "", "join_url": join_url, "provider": "",
            "all_day": False}


def test_upcoming_meetings_filters_past_and_cancelled():
    pairs = [
        ("a@x.fi", _ev("2026-09-15 09:00", "Future")),
        ("a@x.fi", _ev("2026-01-01 09:00", "Past")),          # before now -> dropped
        ("b@x.fi", _ev("2026-09-20 09:00", "Cancelled call", status="cancelled")),
        ("c@x.fi", _ev("", "No time")),                        # no start -> dropped
    ]
    out = mail.upcoming_meetings(pairs, NOW)
    assert [m["summary"] for m in out] == ["Future"]
    assert out[0]["email"] == "a@x.fi"


def test_upcoming_meetings_dedupes_and_sorts():
    pairs = [
        ("a@x.fi", _ev("2026-10-01 09:00", "Kickoff", join_url="https://teams/x")),
        ("a@x.fi", _ev("2026-10-01 09:00", "Re: Kickoff", join_url="https://teams/x")),  # dup join+start
        ("b@x.fi", _ev("2026-09-12 09:00", "Earlier")),
    ]
    out = mail.upcoming_meetings(pairs, NOW)
    assert [m["summary"] for m in out] == ["Earlier", "Kickoff"]  # sorted by start, deduped


# ---------------- DAL cache ----------------

def _db():
    db = Db.open_memory()
    db.add_contact({"name": "Ada", "email": "Ada@Acme.fi", "stage": "cold"})   # id 1
    db.add_contact({"name": "Bo", "email": "bo@x.fi", "stage": "cold"})        # id 2
    return db


def test_set_meetings_correlates_by_email_case_insensitive():
    db = _db()
    db.set_meetings([
        {"email": "ada@acme.fi", "start": "2026-09-15 09:00", "summary": "A1"},
        {"email": "ada@acme.fi", "start": "2026-09-20 09:00", "summary": "A2"},
        {"email": "nobody@x.fi", "start": "2026-09-15 09:00", "summary": "orphan"},
    ])
    nm = db.next_meetings(NOW)
    assert set(nm) == {1}                       # Bo has none; orphan uncorrelated
    assert nm[1]["summary"] == "A1"             # soonest wins
    assert nm[1]["start"] == "2026-09-15 09:00"


def test_next_meetings_excludes_past_and_rebuilds():
    db = _db()
    db.set_meetings([{"email": "ada@acme.fi", "start": "2026-01-01 09:00", "summary": "old"}])
    assert db.next_meetings(NOW) == {}          # all in the past
    assert db.meetings_fetched_at()             # stamp recorded
    # a rescan fully replaces the cache
    db.set_meetings([{"email": "bo@x.fi", "start": "2026-12-01 09:00", "summary": "new"}])
    nm = db.next_meetings(NOW)
    assert set(nm) == {2} and nm[2]["summary"] == "new"


# ---------------- web wiring ----------------

def test_build_state_attaches_next_meeting():
    db = Db.open_memory()
    db.set_config("timezone", "UTC")
    db.set_config("stages", ["cold"])
    db.set_config("sources", ["cold"])
    db.add_contact({"name": "Ada", "email": "ada@acme.fi", "stage": "cold"})
    db.add_contact({"name": "Bo", "email": "bo@x.fi", "stage": "cold"})
    db.set_meetings([{"email": "ada@acme.fi", "start": "2099-09-15 09:00", "summary": "Demo",
                      "join_url": "https://teams/x", "provider": "teams"}])
    s = web.build_state(db)
    by_name = {c["name"]: c for c in s["contacts"]}
    assert by_name["Ada"]["next_meeting"]["summary"] == "Demo"
    assert by_name["Ada"]["next_meeting"]["join_url"] == "https://teams/x"
    assert "next_meeting" not in by_name["Bo"]
    assert s["meetings_fetched_at"]


def test_api_refresh_meetings_no_imap_raises():
    db = Db.open_memory()
    import pytest
    with pytest.raises(RuntimeError, match="IMAP not configured"):
        web.api_refresh_meetings(db, {})


def test_api_refresh_meetings_scans_and_caches(monkeypatch):
    db = Db.open_memory()
    db.set_config("timezone", "UTC")
    db.set_config("imap", {"host": "h"})
    db.add_contact({"name": "Ada", "email": "ada@acme.fi", "stage": "cold"})

    def fake_scan(imap_cfg, tz=None, days_back=45, now_str=None):
        assert imap_cfg == {"host": "h"}
        return [{"email": "ada@acme.fi", "start": "2099-01-01 09:00", "summary": "Kickoff"}]

    monkeypatch.setattr("crm.mail.fetch_upcoming_meetings", fake_scan)
    res = web.api_refresh_meetings(db, {})
    assert res["count"] == 1 and res["fetched_at"]
    assert db.next_meetings("2026-09-10 12:00")[1]["summary"] == "Kickoff"
