"""Unit tests for the SQLite data layer (crm/db.py).

These pin the DAL directly (in-memory db), independent of the CLI/web call
sites that will be migrated onto it — so they keep catching regressions even as
those higher layers (and their own tests) move to the new API.
"""

from crm.db import Db
from crm.stages import DEFAULT_STAGES, stage_segments


def _db():
    return Db.open_memory()


# ---------------- schema / config ----------------

def test_schema_and_defaults():
    db = _db()
    assert db.stages() == list(DEFAULT_STAGES)
    assert db.get_config("nope") is None
    assert db.get_config("nope", 5) == 5


def test_config_roundtrip_json_values():
    db = _db()
    db.set_config("stages", ["a", "b"])
    db.set_config("imap", {"host": "x", "port": 993})
    db.set_config("import_ignore", ["a@b.com", "@spam.com"])
    assert db.stages() == ["a", "b"]
    assert db.imap() == {"host": "x", "port": 993}
    assert db.all_config()["import_ignore"] == ["a@b.com", "@spam.com"]


def test_set_config_upsert_and_replace():
    db = _db()
    db.set_config("k", 1)
    db.set_config("k", 2)
    assert db.get_config("k") == 2
    db.replace_config({"only": "this"})
    assert db.all_config() == {"only": "this"}


# ---------------- contacts ----------------

def test_add_contact_seeds_note_and_history():
    db = _db()
    c = db.add_contact({"name": "Ada", "email": "ada@acme.fi", "stage": "cold"}, stamp="2026-01-01 10:00")
    assert isinstance(c["id"], int)
    assert c["name"] == "Ada"
    assert c["notes"] == [{"date": "2026-01-01 10:00", "text": "Added to CRM"}]
    assert c["stage_history"] == [{"date": "2026-01-01 10:00", "from": "", "to": "cold"}]
    assert "removed_at" not in c


def test_add_contact_defaults_blank_fields():
    db = _db()
    c = db.add_contact({"name": "NoEmail"})
    for col in ("email", "phone", "company", "role", "source", "stage", "next_action", "next_date"):
        assert c[col] == ""


def test_update_contact_subset_only():
    db = _db()
    c = db.add_contact({"name": "Ada", "stage": "cold", "company": "Acme"})
    assert db.update_contact(c["id"], {"stage": "won"})
    got = db.get_contact(c["id"])
    assert got["stage"] == "won"
    assert got["company"] == "Acme"          # untouched
    assert got["name"] == "Ada"
    assert db.update_contact(999, {"stage": "x"}) is False


def test_next_action_set_and_clear():
    db = _db()
    c = db.add_contact({"name": "Ada"})
    db.set_next(c["id"], "Call", "2026-02-02")
    got = db.get_contact(c["id"])
    assert (got["next_action"], got["next_date"]) == ("Call", "2026-02-02")
    db.clear_next(c["id"])
    got = db.get_contact(c["id"])
    assert (got["next_action"], got["next_date"]) == ("", "")


def test_stable_ids_survive_deletes():
    db = _db()
    a = db.add_contact({"name": "A"})
    b = db.add_contact({"name": "B"})
    c = db.add_contact({"name": "C"})
    assert [a["id"], b["id"], c["id"]] == [1, 2, 3]
    db.remove_contact(b["id"])
    # ids are stable PKs, not positions: A and C keep 1 and 3.
    assert [x["id"] for x in db.list_contacts()] == [1, 3]
    assert db.get_contact(3)["name"] == "C"


# ---------------- soft delete / restore ----------------

def test_remove_and_restore():
    db = _db()
    c = db.add_contact({"name": "Gone"})
    assert db.remove_contact(c["id"], stamp="2026-03-03 12:00")
    assert [x["name"] for x in db.list_contacts()] == []
    removed = db.list_removed()
    assert removed[0]["name"] == "Gone"
    assert removed[0]["removed_at"] == "2026-03-03 12:00"
    assert db.remove_contact(c["id"]) is False       # already removed
    assert db.restore_contact(c["id"])
    assert [x["name"] for x in db.list_contacts()] == ["Gone"]
    assert "removed_at" not in db.get_contact(c["id"])


# ---------------- notes ----------------

def test_notes_newest_first():
    db = _db()
    c = db.add_contact({"name": "Ada"}, note_text=None)
    db.add_note(c["id"], "first", stamp="2026-01-01 10:00")
    db.add_note(c["id"], "second", stamp="2026-01-02 10:00")
    texts = [n["text"] for n in db.get_contact(c["id"])["notes"]]
    assert texts == ["second", "first"]


# ---------------- stage history ----------------

def test_record_stage_change_and_noop():
    db = _db()
    c = db.add_contact({"name": "Ada", "stage": "cold"}, stamp="2026-01-01 10:00")
    db.record_stage_change(c["id"], "cold", "cold", stamp="2026-01-02 10:00")   # no-op
    db.record_stage_change(c["id"], "cold", "won", stamp="2026-01-03 10:00")
    hist = db.get_contact(c["id"])["stage_history"]
    assert hist == [
        {"date": "2026-01-01 10:00", "from": "", "to": "cold"},
        {"date": "2026-01-03 10:00", "from": "cold", "to": "won"},
    ]


# ---------------- JSON import ----------------

def _legacy():
    return {
        "version": 4,
        "config": {"stages": ["cold", "won"], "sources": ["cold"], "timezone": "UTC",
                   "imap": {"host": "h"}, "import_ignore": ["x@y.com"]},
        "contacts": [
            {"name": "Ada", "email": "ada@acme.fi", "phone": "", "company": "Acme",
             "role": "CTO", "source": "cold", "stage": "won", "next_action": "Ship",
             "next_date": "2026-05-01",
             "notes": [{"date": "2026-02-02 09:00", "text": "newer"},
                       {"date": "2026-01-01 09:00", "text": "older"}],
             "stage_history": [{"date": "2026-01-01 09:00", "from": "", "to": "cold"},
                               {"date": "2026-02-02 09:00", "from": "cold", "to": "won"}]},
        ],
        "removed": [
            {"name": "Old", "email": "old@x.fi", "stage": "cold",
             "removed_at": "2026-03-03 12:00",
             "notes": [{"date": "2026-01-01 09:00", "text": "n"}], "stage_history": []},
        ],
    }


def test_import_json_roundtrip():
    db = _db()
    db.import_json(_legacy())

    assert db.stages() == ["cold", "won"]
    assert db.imap() == {"host": "h"}
    assert db.all_config()["import_ignore"] == ["x@y.com"]

    contacts = db.list_contacts()
    assert len(contacts) == 1
    ada = contacts[0]
    assert ada["name"] == "Ada" and ada["stage"] == "won"
    assert (ada["next_action"], ada["next_date"]) == ("Ship", "2026-05-01")
    # note order preserved: newest-first
    assert [n["text"] for n in ada["notes"]] == ["newer", "older"]
    # stage history preserved: oldest-first, drives stage_segments
    segs = stage_segments(ada, "2026-06-01")
    assert [s["stage"] for s in segs] == ["cold", "won"]

    removed = db.list_removed()
    assert len(removed) == 1 and removed[0]["name"] == "Old"
    assert removed[0]["removed_at"] == "2026-03-03 12:00"
    assert db.list_contacts() == [ada]        # removed not in active list


def test_import_json_preserves_source_order():
    db = _db()
    data = {"config": {}, "contacts": [{"name": n} for n in ("A", "B", "C")], "removed": []}
    db.import_json(data)
    assert [c["name"] for c in db.list_contacts()] == ["A", "B", "C"]
