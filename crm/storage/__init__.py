"""Storage layer: backend selection, schema migrations, tz helpers.

Backend is chosen at import time based on CRM_STORAGE:

  unset / "file:..."     → local backend
  bare path              → local backend at that path
  "s3://bucket/key.json" → S3 backend  (added in a follow-up commit)

CRM_DATA is still honored as a back-compat shortcut for the local
backend path when CRM_STORAGE is unset.

cli.py's --data flag calls use_local_path() to switch backends
mid-process rather than mutating a global.

Migrations and timezone helpers live here (not in a backend) because
they operate on the loaded data dict, independently of where it came
from.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..stages import DEFAULT_STAGES, DEFAULT_SOURCES
from .errors import ConcurrentWriteError, StorageCorrupt
from .local import LocalBackend


DEFAULT_DATA_FILE = Path.home() / ".config" / "kitron-crm" / "crm_data.json"

CURRENT_VERSION = 4


def _build_backend():
    raw = os.environ.get("CRM_STORAGE", "").strip()
    if raw.startswith("s3://"):
        from .s3 import S3Backend  # lazy: avoids requiring boto3 for local users
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


_backend = _build_backend()


def use_local_path(path):
    """Switch the active backend to a local one at the given path.
    cli's --data flag uses this."""
    global _backend
    _backend = LocalBackend(Path(path))


def current_backend():
    """Return the active backend (useful for diagnostics)."""
    return _backend


# --- Migrations ---
# Key = target version. Only add an entry when the schema actually changes.

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


def load_data():
    try:
        data = _backend.load()
    except StorageCorrupt as e:
        print(f"Error: {e}")
        print("Fix the data manually or remove it to start fresh.")
        sys.exit(1)

    version = data.get("version", 0)
    if version > CURRENT_VERSION:
        print(f"Warning: data is version {version}, but this crm is version {CURRENT_VERSION}.")
        print("Update your crm or you may lose data.")
        sys.exit(1)
    if version < CURRENT_VERSION:
        while version < CURRENT_VERSION:
            version += 1
            if version in MIGRATIONS:
                MIGRATIONS[version](data)
        data["version"] = CURRENT_VERSION
        save_data(data)

    return data


def save_data(data):
    _backend.save(data)


def _parse_tz(tz_str):
    """Parse a timezone string like 'UTC+03:00' or 'UTC-05:00' into a timezone object."""
    if tz_str == "UTC":
        return timezone.utc
    sign = 1 if "+" in tz_str else -1
    parts = tz_str.split("+")[-1].split("-")[-1]
    h, m = parts.split(":")
    return timezone(timedelta(hours=sign * int(h), minutes=sign * int(m)))


def get_tz(data):
    cfg = data["config"]
    tz_str = cfg.get("timezone")
    if not tz_str:
        local_tz = datetime.now().astimezone().tzinfo
        offset = local_tz.utcoffset(datetime.now())
        total = int(offset.total_seconds())
        sign = "+" if total >= 0 else "-"
        total = abs(total)
        hours, remainder = divmod(total, 3600)
        minutes = remainder // 60
        tz_str = f"UTC{sign}{hours:02d}:{minutes:02d}"
        cfg["timezone"] = tz_str
        data["config"] = cfg
        save_data(data)
        print(f"Timezone set to {tz_str}. Change with: crm config timezone UTC+XX:XX")
    return _parse_tz(tz_str)
