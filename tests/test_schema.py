"""SQLite schema-migration framework: fresh dbs land at SCHEMA_VERSION, older
dbs upgrade in order on open, and an up-to-date db runs nothing."""

import sqlite3

from crm.db import Db, SCHEMA_VERSION, _migrate_1


def _version(db):
    return int(db.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"])


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_db_is_at_current_version_with_all_tables():
    db = Db.open_memory()
    assert _version(db) == SCHEMA_VERSION
    assert {"config", "contacts", "notes", "stage_history", "meetings", "accounts", "meta"} <= _tables(db.conn)
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(contacts)")}
    assert "account_id" in cols


def test_old_v1_db_upgrades_on_open(tmp_path):
    # Hand-build a v1-only db: baseline schema, no accounts, schema_version = 1.
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    _migrate_1(conn)
    conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO contacts(name, company) VALUES ('Ada', 'Acme')")
    conn.commit()
    assert "accounts" not in _tables(conn)
    conn.close()

    # Reopening through Db applies migration 2.
    db = Db.open(path)
    assert _version(db) == SCHEMA_VERSION
    assert "accounts" in _tables(db.conn)
    assert "account_id" in {r["name"] for r in db.conn.execute("PRAGMA table_info(contacts)")}
    # Existing row preserved; account_id NULL until the contact is next written.
    c = db.get_contact(1)
    assert c["name"] == "Ada" and c["account_id"] is None
    db.close()


def test_reopen_current_db_is_a_noop(tmp_path):
    path = tmp_path / "c.db"
    Db.open(path).close()
    db = Db.open(path)            # already current — no migration, no error
    assert _version(db) == SCHEMA_VERSION
    db.close()
