"""Storage-session tests: local sqlite, in-place JSON upgrade, and a fake
remote backend exercising the byte transport + ETag concurrency."""

import json

import pytest

from crm import storage
from crm.storage.errors import ConcurrentWriteError, StorageCorrupt


@pytest.fixture(autouse=True)
def _reset_backend():
    """Each test sets its own backend; restore the module global afterwards."""
    saved = storage._backend
    yield
    storage._backend = saved


# ---------------- local: fresh + roundtrip ----------------

def test_local_fresh_creates_empty_db(tmp_path):
    storage.use_local_path(tmp_path / "crm.db")
    db = storage.open_db()
    assert db.list_contacts() == []
    c = db.add_contact({"name": "Ada", "stage": "cold"})
    storage.push_db(db)
    storage.close_db(db)

    db2 = storage.open_db()
    assert [x["name"] for x in db2.list_contacts()] == ["Ada"]
    assert db2.get_contact(c["id"])["stage"] == "cold"
    storage.close_db(db2)


def test_local_creates_parent_dir(tmp_path):
    storage.use_local_path(tmp_path / "nested" / "deep" / "crm.db")
    db = storage.open_db()
    db.add_contact({"name": "X"})
    storage.push_db(db)
    storage.close_db(db)
    assert (tmp_path / "nested" / "deep" / "crm.db").exists()


# ---------------- local: legacy JSON upgrade ----------------

def _legacy_blob():
    return {
        "version": 4,
        "config": {"timezone": "UTC", "stages": ["cold", "won"], "sources": ["cold"]},
        "contacts": [{"name": "Ada", "email": "a@b.fi", "stage": "won",
                      "notes": [{"date": "2026-02-02 09:00", "text": "newer"},
                                {"date": "2026-01-01 09:00", "text": "older"}],
                      "stage_history": [{"date": "2026-01-01 09:00", "from": "", "to": "won"}]}],
        "removed": [],
    }


def test_local_json_upgraded_in_place(tmp_path):
    path = tmp_path / "crm_data.json"
    path.write_text(json.dumps(_legacy_blob()))
    storage.use_local_path(path)

    db = storage.open_db()
    contacts = db.list_contacts()
    assert [c["name"] for c in contacts] == ["Ada"]
    assert [n["text"] for n in contacts[0]["notes"]] == ["newer", "older"]  # order kept
    assert db.stages() == ["cold", "won"]
    storage.close_db(db)

    # Original JSON preserved as a backup; the path is now a SQLite db.
    assert (tmp_path / "crm_data.json.jsonbak").exists()
    assert path.read_bytes()[:16] == storage.SQLITE_MAGIC

    # Reopening finds SQLite (no second upgrade), backup untouched.
    db2 = storage.open_db()
    assert [c["name"] for c in db2.list_contacts()] == ["Ada"]
    storage.close_db(db2)


def test_local_pre_v1_json_migrates(tmp_path):
    """A very old blob (top-level timezone/stages, no config) still imports."""
    path = tmp_path / "old.json"
    path.write_text(json.dumps({
        "timezone": "UTC", "stages": ["cold", "won"],
        "contacts": [{"name": "Old", "stage": "cold", "notes": [], "stage_history": []}],
    }))
    storage.use_local_path(path)
    db = storage.open_db()
    assert [c["name"] for c in db.list_contacts()] == ["Old"]
    assert db.stages() == ["cold", "won"]
    assert db.sources() == ["cold", "referral", "inbound"]  # added by migrate_to_3
    storage.close_db(db)


def test_local_corrupt_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    storage.use_local_path(path)
    with pytest.raises(StorageCorrupt):
        storage.open_db()


# ---------------- remote: fake backend ----------------

class FakeRemote:
    """In-memory S3-like backend: one object, one monotonic ETag."""
    is_remote = True

    def __init__(self, blob=None):
        self.blob = blob
        self.etag = '"v1"' if blob is not None else None
        self._n = 1

    def describe(self):
        return "fake://store"

    def fetch(self):
        return self.blob, self.etag

    def store(self, blob, etag):
        if etag != self.etag:
            raise ConcurrentWriteError("data changed remotely")
        self._n += 1
        self.blob = blob
        self.etag = f'"v{self._n}"'
        return self.etag


def test_remote_roundtrip_and_json_upgrade():
    remote = FakeRemote(json.dumps(_legacy_blob()).encode())
    storage._backend = remote

    db = storage.open_db()
    assert [c["name"] for c in db.list_contacts()] == ["Ada"]
    db.add_contact({"name": "Bo", "stage": "cold"})
    storage.push_db(db)
    storage.close_db(db)

    # The object is now a SQLite file, not JSON.
    assert remote.blob[:16] == storage.SQLITE_MAGIC

    db2 = storage.open_db()
    assert sorted(c["name"] for c in db2.list_contacts()) == ["Ada", "Bo"]
    storage.close_db(db2)


def test_remote_first_create():
    remote = FakeRemote(None)  # nothing stored yet
    storage._backend = remote
    db = storage.open_db()
    db.add_contact({"name": "First"})
    storage.push_db(db)
    storage.close_db(db)
    assert remote.blob is not None and remote.blob[:16] == storage.SQLITE_MAGIC


def test_remote_conflict_raises():
    remote = FakeRemote(json.dumps(_legacy_blob()).encode())
    storage._backend = remote
    db = storage.open_db()
    db.add_contact({"name": "Bo"})
    # Someone else writes in the meantime → our ETag is stale.
    remote.etag = '"other"'
    with pytest.raises(ConcurrentWriteError):
        storage.push_db(db)
    storage.close_db(db)
