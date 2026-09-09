"""SQLite data layer for the CRM — replaces the single-JSON-blob store.

The database *file* is the unit of storage and sync. Local backends keep it on
disk; the S3 backend pulls the `.db` object before a session and pushes a
consistent snapshot after a write, using the same If-Match ETag concurrency the
JSON blob used (see crm/storage). All persistent access goes through `Db`; no
code outside this module writes SQL or depends on the old dict shape.

Shapes returned to the rest of the app (unchanged from the JSON era, so the pure
domain helpers in contacts.py/due.py/stages.py keep working):

    contact = {id, name, email, phone, company, role, source, stage,
               next_action, next_date, notes:[{date,text}],
               stage_history:[{date,from,to}], removed_at?}
    note    = {date, text}           # newest-first
    stage_history entry = {date, from, to}   # oldest-first, from="" = creation

Timestamps are UTC strings exactly as before: "%Y-%m-%d %H:%M", or date-only
"%Y-%m-%d" for next_date / removed_at. Localisation happens at render time only.
"""

import json
import sqlite3

from .stages import DEFAULT_STAGES, DEFAULT_SOURCES

# Bump when the SQLite schema changes (independent of the old JSON `version`,
# which tops out at 4 and is consumed once by the JSON->SQLite import).
SCHEMA_VERSION = 1

# Stored contact columns, in the canonical order. `id` and `removed_at` are
# handled separately; the rest mirror the old contact dict's editable body.
CONTACT_COLUMNS = ("name", "email", "phone", "company", "role", "source",
                   "stage", "next_action", "next_date")

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL          -- JSON-encoded value
);
CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    company     TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    stage       TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    next_date   TEXT NOT NULL DEFAULT '',
    removed_at  TEXT,            -- NULL = active; set = soft-deleted
    created_seq INTEGER          -- import/restore order tiebreaker
);
CREATE INDEX IF NOT EXISTS idx_contacts_active ON contacts(removed_at);
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    date       TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_notes_contact ON notes(contact_id);
CREATE TABLE IF NOT EXISTS stage_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    date       TEXT NOT NULL DEFAULT '',
    from_stage TEXT NOT NULL DEFAULT '',
    to_stage   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sh_contact ON stage_history(contact_id);
-- Derived mail cache: upcoming/past meetings correlated to a contact by the
-- counterparty email. Rebuildable from IMAP; synced so every device shares it.
CREATE TABLE IF NOT EXISTS meetings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id  INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
    email       TEXT NOT NULL DEFAULT '',
    start       TEXT NOT NULL DEFAULT '',
    "end"       TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',
    organizer   TEXT NOT NULL DEFAULT '',
    join_url    TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT '',
    all_day     INTEGER NOT NULL DEFAULT 0,
    folder      TEXT NOT NULL DEFAULT '',
    uid         TEXT NOT NULL DEFAULT '',
    fetched_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_meetings_contact ON meetings(contact_id);
CREATE INDEX IF NOT EXISTS idx_meetings_start ON meetings(start);
"""


def _row_contact(row):
    """sqlite3.Row -> contact dict (without the joined children)."""
    c = {"id": row["id"]}
    for col in CONTACT_COLUMNS:
        c[col] = row[col]
    if row["removed_at"] is not None:
        c["removed_at"] = row["removed_at"]
    return c


class Db:
    """Thin repository over a sqlite3 connection. One instance per session."""

    def __init__(self, conn):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def open(cls, path):
        """Open (creating if needed) a database at `path` and ensure the schema."""
        conn = sqlite3.connect(str(path))
        db = cls(conn)
        db.init_schema()
        return db

    @classmethod
    def open_memory(cls):
        db = cls(sqlite3.connect(":memory:"))
        db.init_schema()
        return db

    def init_schema(self):
        self.conn.executescript(_DDL)
        cur = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        if cur.fetchone() is None:
            self.conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                              (str(SCHEMA_VERSION),))
        self.conn.commit()

    def close(self):
        self.conn.close()

    def commit(self):
        self.conn.commit()

    # ---------------- config ----------------

    def get_config(self, key, default=None):
        row = self.conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def set_config(self, key, value):
        self.conn.execute(
            "INSERT INTO config(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)))

    def delete_config(self, key):
        self.conn.execute("DELETE FROM config WHERE key = ?", (key,))

    def all_config(self):
        """The whole config as a plain dict (mirrors the old data['config'])."""
        return {r["key"]: json.loads(r["value"])
                for r in self.conn.execute("SELECT key, value FROM config")}

    def replace_config(self, cfg):
        """Replace the entire config table with `cfg` (for `config edit`)."""
        self.conn.execute("DELETE FROM config")
        for k, v in (cfg or {}).items():
            self.set_config(k, v)

    def stages(self):
        return self.get_config("stages", list(DEFAULT_STAGES))

    def sources(self):
        return self.get_config("sources", list(DEFAULT_SOURCES))

    def templates(self):
        return self.get_config("templates", {})

    def imap(self):
        return self.get_config("imap")

    def smtp(self):
        return self.get_config("smtp")

    # ---------------- contacts ----------------

    def _children(self, ids):
        """Return (notes_by_cid, history_by_cid) for the given contact ids."""
        notes, hist = {}, {}
        if not ids:
            return notes, hist
        qs = ",".join("?" * len(ids))
        # Notes newest-first: prepends gave later inserts the larger id.
        for r in self.conn.execute(
                f"SELECT contact_id, date, text FROM notes WHERE contact_id IN ({qs}) "
                "ORDER BY id DESC", ids):
            notes.setdefault(r["contact_id"], []).append({"date": r["date"], "text": r["text"]})
        # Stage history oldest-first.
        for r in self.conn.execute(
                f"SELECT contact_id, date, from_stage, to_stage FROM stage_history "
                f"WHERE contact_id IN ({qs}) ORDER BY id ASC", ids):
            hist.setdefault(r["contact_id"], []).append(
                {"date": r["date"], "from": r["from_stage"], "to": r["to_stage"]})
        return notes, hist

    def _hydrate(self, rows):
        """Attach notes + stage_history to a list of contact rows."""
        contacts = [_row_contact(r) for r in rows]
        notes, hist = self._children([c["id"] for c in contacts])
        for c in contacts:
            c["notes"] = notes.get(c["id"], [])
            c["stage_history"] = hist.get(c["id"], [])
        return contacts

    def list_contacts(self, include_removed=False):
        """Active contacts (optionally including soft-deleted), each a full dict."""
        sql = "SELECT * FROM contacts"
        if not include_removed:
            sql += " WHERE removed_at IS NULL"
        sql += " ORDER BY id ASC"
        return self._hydrate(self.conn.execute(sql).fetchall())

    def list_removed(self):
        rows = self.conn.execute(
            "SELECT * FROM contacts WHERE removed_at IS NOT NULL ORDER BY removed_at DESC"
        ).fetchall()
        return self._hydrate(rows)

    def get_contact(self, cid):
        row = self.conn.execute("SELECT * FROM contacts WHERE id = ?", (cid,)).fetchone()
        if row is None:
            return None
        return self._hydrate([row])[0]

    def add_contact(self, fields, note_text="Added to CRM", stamp=None):
        """Insert a contact, seeding the creation note + stage_history entry
        (matching the old add literals). Returns the new contact dict."""
        from .notes import utc_stamp
        stamp = stamp or utc_stamp()
        vals = {col: str(fields.get(col, "") or "") for col in CONTACT_COLUMNS}
        cols = ",".join(CONTACT_COLUMNS)
        ph = ",".join("?" * len(CONTACT_COLUMNS))
        cur = self.conn.execute(
            f"INSERT INTO contacts({cols}, created_seq) VALUES({ph}, "
            "(SELECT COALESCE(MAX(created_seq), 0) + 1 FROM contacts))",
            [vals[c] for c in CONTACT_COLUMNS])
        cid = cur.lastrowid
        if note_text:
            self.conn.execute("INSERT INTO notes(contact_id, date, text) VALUES(?,?,?)",
                              (cid, stamp, note_text))
        stage = vals["stage"]
        if stage:
            self.conn.execute(
                "INSERT INTO stage_history(contact_id, date, from_stage, to_stage) VALUES(?,?,?,?)",
                (cid, stamp, "", stage))
        return self.get_contact(cid)

    def update_contact(self, cid, fields):
        """Write a subset of CONTACT_COLUMNS. Returns True if the contact exists."""
        sets = [(col, str(fields[col] if fields[col] is not None else ""))
                for col in CONTACT_COLUMNS if col in fields]
        if not sets:
            return self.get_contact(cid) is not None
        assign = ", ".join(f"{c} = ?" for c, _ in sets)
        cur = self.conn.execute(f"UPDATE contacts SET {assign} WHERE id = ?",
                                [v for _, v in sets] + [cid])
        return cur.rowcount > 0

    def set_next(self, cid, action, date):
        self.conn.execute("UPDATE contacts SET next_action = ?, next_date = ? WHERE id = ?",
                          (action, date, cid))

    def clear_next(self, cid):
        self.conn.execute("UPDATE contacts SET next_action = '', next_date = '' WHERE id = ?", (cid,))

    def remove_contact(self, cid, stamp=None):
        """Soft-delete: set removed_at. Returns True if a row was affected."""
        from .notes import utc_stamp
        cur = self.conn.execute(
            "UPDATE contacts SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
            (stamp or utc_stamp(), cid))
        return cur.rowcount > 0

    def restore_contact(self, cid):
        cur = self.conn.execute(
            "UPDATE contacts SET removed_at = NULL WHERE id = ? AND removed_at IS NOT NULL", (cid,))
        return cur.rowcount > 0

    # ---------------- notes ----------------

    def add_note(self, cid, text, stamp=None):
        from .notes import utc_stamp
        stamp = stamp or utc_stamp()
        self.conn.execute("INSERT INTO notes(contact_id, date, text) VALUES(?,?,?)",
                          (cid, stamp, text))
        return {"date": stamp, "text": text}

    # ---------------- stage history ----------------

    def record_stage_change(self, cid, old, new, stamp=None):
        """Append a stage_history entry unless it's a no-op (old == new)."""
        if old == new:
            return
        from .notes import utc_stamp
        self.conn.execute(
            "INSERT INTO stage_history(contact_id, date, from_stage, to_stage) VALUES(?,?,?,?)",
            (cid, stamp or utc_stamp(), old or "", new))

    # ---------------- one-time JSON import ----------------

    def _insert_contact_verbatim(self, c, removed_at=None):
        """Insert an existing contact dict as-is (its own notes + stage_history),
        without seeding anything. Used only by the JSON import."""
        vals = [str(c.get(col, "") or "") for col in CONTACT_COLUMNS]
        cols = ",".join(CONTACT_COLUMNS)
        ph = ",".join("?" * len(CONTACT_COLUMNS))
        cur = self.conn.execute(
            f"INSERT INTO contacts({cols}, removed_at, created_seq) VALUES({ph}, ?, "
            "(SELECT COALESCE(MAX(created_seq), 0) + 1 FROM contacts))",
            vals + [removed_at])
        cid = cur.lastrowid
        # Notes are stored newest-first in the JSON list. Insert oldest-first so
        # the autoincrement id grows with age and ORDER BY id DESC = newest-first.
        for n in reversed(c.get("notes", []) or []):
            self.conn.execute("INSERT INTO notes(contact_id, date, text) VALUES(?,?,?)",
                              (cid, n.get("date", "") or "", n.get("text", "") or ""))
        # Stage history is oldest-first in the JSON list; keep that order.
        for e in c.get("stage_history", []) or []:
            self.conn.execute(
                "INSERT INTO stage_history(contact_id, date, from_stage, to_stage) VALUES(?,?,?,?)",
                (cid, e.get("date", "") or "", e.get("from", "") or "", e.get("to", "") or ""))
        return cid

    def import_json(self, data):
        """Populate an empty database from a legacy JSON `data` dict (v4 shape).
        Idempotent only against an empty db — callers guard on that."""
        for k, v in (data.get("config") or {}).items():
            self.set_config(k, v)
        for c in data.get("contacts") or []:
            self._insert_contact_verbatim(c)
        for c in data.get("removed") or []:
            self._insert_contact_verbatim(c, removed_at=c.get("removed_at") or "")
        self.commit()

    def is_empty(self):
        return self.conn.execute("SELECT 1 FROM contacts LIMIT 1").fetchone() is None \
            and self.conn.execute("SELECT 1 FROM config LIMIT 1").fetchone() is None
