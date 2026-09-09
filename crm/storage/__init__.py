"""Storage layer: backend selection, JSON→SQLite upgrade, tz helpers.

The store is a single SQLite database file (crm/db.py). Backends are dumb
transports for that file's bytes:

  unset / "file:..."     → local backend (sqlite opened in place)
  bare path              → local backend at that path
  "s3://bucket/key.json" → S3 backend (pull bytes / conditional PUT)

A session is `db = open_db()` … `push_db(db)` (after writes) … `close_db(db)`.
open_db transparently upgrades a legacy JSON blob to SQLite the first time it
sees one — same S3 key, same If-Match ETag concurrency — so existing users and
every device migrate with no reconfiguration.

CRM_DATA is still honored as the local path when CRM_STORAGE is unset. cli.py's
--data flag calls use_local_path() to switch backends mid-process.
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
import os

from ..db import Db
from ..stages import DEFAULT_STAGES, DEFAULT_SOURCES
from .errors import ConcurrentWriteError, StorageCorrupt  # noqa: F401 (re-exported)
from .local import LocalBackend


DEFAULT_DATA_FILE = Path.home() / ".config" / "kitron-crm" / "crm_data.json"

# Highest legacy JSON schema version; consumed once when importing a JSON blob.
CURRENT_VERSION = 4

# First 16 bytes of every SQLite database file — how we tell a db from JSON.
SQLITE_MAGIC = b"SQLite format 3\x00"


def _build_backend():
    raw = os.environ.get("CRM_STORAGE", "").strip()
    if raw.startswith("s3://"):
        from .s3 import S3Backend  # lazy: avoids requiring requests for local users
        parsed = urlparse(raw)
        return S3Backend(
            bucket=parsed.netloc,
            key=parsed.path.lstrip("/"),
            endpoint_url=os.environ.get("CRM_S3_ENDPOINT") or None,
        )
    if raw.startswith("file:"):
        return LocalBackend(Path(os.path.expanduser(raw[5:])))
    if raw:
        return LocalBackend(Path(os.path.expanduser(raw)))
    return LocalBackend(Path(os.environ.get("CRM_DATA") or DEFAULT_DATA_FILE))


_backend = None


def _ensure_backend():
    """Build the backend on first access. Deferred so commands that don't
    touch storage (--version, help, …) don't pay the S3 client init cost."""
    global _backend
    if _backend is None:
        _backend = _build_backend()
    return _backend


def use_local_path(path):
    """Switch the active backend to a local one at the given path.
    cli's --data flag uses this."""
    global _backend
    _backend = LocalBackend(Path(path))


def current_backend():
    """Return the active backend (useful for diagnostics)."""
    return _ensure_backend()


# --- Legacy JSON migrations -------------------------------------------------
# Applied once, in memory, when importing an old JSON blob into SQLite.
# Key = target version. Only add an entry when the JSON schema actually changed.

def migrate_to_1(data):
    """Initial schema: ensure config, stages, removed exist. Move top-level timezone to config."""
    if "config" not in data:
        data["config"] = {}
    if "timezone" in data:
        data["config"]["timezone"] = data.pop("timezone")
    if "removed" not in data:
        data["removed"] = []
    if "last_contact" in data:
        del data["last_contact"]
    for c in data.get("contacts", []):
        c.pop("last_contact", None)


def migrate_to_2(data):
    """Move stages from top-level into config."""
    if "stages" in data:
        data["config"]["stages"] = data.pop("stages")
    if "stages" not in data["config"]:
        data["config"]["stages"] = DEFAULT_STAGES[:]


def migrate_to_3(data):
    """Add sources to config."""
    if "sources" not in data["config"]:
        data["config"]["sources"] = DEFAULT_SOURCES[:]


def migrate_to_4(data):
    """Convert timestamps from local time to UTC."""
    tz_str = data.get("config", {}).get("timezone")
    if not tz_str:
        return  # no timezone configured, can't convert
    local_tz = _parse_tz(tz_str)

    def to_utc(stamp):
        if not stamp or len(stamp) <= 10:  # date-only or empty
            return stamp
        try:
            dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
            dt = dt.replace(tzinfo=local_tz)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return stamp

    for c in data.get("contacts", []):
        for note in c.get("notes", []):
            note["date"] = to_utc(note["date"])
    for c in data.get("removed", []):
        for note in c.get("notes", []):
            note["date"] = to_utc(note["date"])
        if "removed_at" in c:
            c["removed_at"] = to_utc(c["removed_at"])


MIGRATIONS = {
    1: migrate_to_1,
    2: migrate_to_2,
    3: migrate_to_3,
    4: migrate_to_4,
}


def _migrate_json_to_v4(data):
    """Run the legacy migrations forward so `data` is v4-shaped before import."""
    if not isinstance(data, dict):
        data = {"contacts": []}
    version = data.get("version", 0)
    while version < CURRENT_VERSION:
        version += 1
        if version in MIGRATIONS:
            MIGRATIONS[version](data)
    data["version"] = CURRENT_VERSION
    return data


def _parse_json(blob, where):
    try:
        return json.loads(blob)
    except (json.JSONDecodeError, ValueError) as e:
        raise StorageCorrupt(f"{where}: {e}") from e


# --- Session lifecycle ------------------------------------------------------

class _RemoteSync:
    """Push handle for a remote-backed session: uploads the local working db
    file under the same If-Match ETag concurrency as the old JSON blob."""

    def __init__(self, backend, path, etag):
        self.backend = backend
        self.path = Path(path)
        self.etag = etag

    def push(self, db):
        db.commit()
        self.etag = self.backend.store(self.path.read_bytes(), self.etag)

    def close(self):
        try:
            self.path.unlink()
            self.path.parent.rmdir()
        except OSError:
            pass


def _open_local(path):
    """Open (or create) a local SQLite db, upgrading a legacy JSON file in place."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.stat().st_size:
        with open(p, "rb") as f:
            head = f.read(16)
        if head != SQLITE_MAGIC:
            data = _migrate_json_to_v4(_parse_json(p.read_bytes(), str(p)))
            p.rename(p.with_suffix(p.suffix + ".jsonbak"))  # keep the original
            db = Db.open(p)
            db.import_json(data)
            return db
    return Db.open(p)


def open_db():
    """Open a Db bound to the active backend, pulling + upgrading as needed."""
    backend = _ensure_backend()
    if not backend.is_remote:
        db = _open_local(backend.path)
        db._sync = None
        return db
    raw, etag = backend.fetch()
    tmp = Path(tempfile.mkdtemp(prefix="crm-store-")) / "store.db"
    db = Db.open(tmp)
    if raw and raw[:16] == SQLITE_MAGIC:
        db.close()
        tmp.write_bytes(raw)
        db = Db.open(tmp)
    elif raw:
        db.import_json(_migrate_json_to_v4(_parse_json(raw, backend.describe())))
    db._sync = _RemoteSync(backend, tmp, etag)
    return db


def push_db(db):
    """Persist a session's writes: commit locally, and upload if remote."""
    sync = getattr(db, "_sync", None)
    if sync is None:
        db.commit()
    else:
        sync.push(db)


def close_db(db):
    """Close a session and clean up any remote working file."""
    sync = getattr(db, "_sync", None)
    db.close()
    if sync is not None:
        sync.close()


# --- Timezone ---------------------------------------------------------------

def _parse_tz(tz_str):
    """Parse a timezone string like 'UTC+03:00' or 'UTC-05:00' into a timezone object."""
    if tz_str == "UTC":
        return timezone.utc
    sign = 1 if "+" in tz_str else -1
    parts = tz_str.split("+")[-1].split("-")[-1]
    h, m = parts.split(":")
    return timezone(timedelta(hours=sign * int(h), minutes=sign * int(m)))


def get_tz(db):
    """The configured tz, freezing an auto-detected one on first use so overdue
    math stays stable if the machine's tz later changes."""
    tz_str = db.get_config("timezone")
    if not tz_str:
        local_tz = datetime.now().astimezone().tzinfo
        offset = local_tz.utcoffset(datetime.now())
        total = int(offset.total_seconds())
        sign = "+" if total >= 0 else "-"
        total = abs(total)
        hours, remainder = divmod(total, 3600)
        minutes = remainder // 60
        tz_str = f"UTC{sign}{hours:02d}:{minutes:02d}"
        db.set_config("timezone", tz_str)
        push_db(db)
        print(f"Timezone set to {tz_str}. Change with: crm config timezone UTC+XX:XX")
    return _parse_tz(tz_str)
