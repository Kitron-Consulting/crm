"""Data file I/O, schema migrations, and timezone helpers.

`DATA_FILE` is a module-level attribute that callers can reassign (cli's
--data flag does this). load_data/save_data read it at call time.

The timezone helpers live here because get_tz mutates+saves data on
first run (auto-detects local tz and persists it), which is a storage
side effect. display_stamp (pure formatting) belongs in display.py and
imports get_tz from here.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .stages import DEFAULT_STAGES, DEFAULT_SOURCES

# Default data file lives in the user's config dir, not next to the script.
# The previous __file__-based default broke inside a PyInstaller bundle
# (the bundled `cli.py` ends up in a temp _MEIPASS dir, not a stable home).
# Override with CRM_DATA env var or the --data CLI flag.
DEFAULT_DATA_FILE = Path.home() / ".config" / "kitron-crm" / "crm_data.json"
DATA_FILE = Path(os.environ.get("CRM_DATA") or DEFAULT_DATA_FILE)

CURRENT_VERSION = 4


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
            utc_dt = dt.astimezone(timezone.utc)
            return utc_dt.strftime("%Y-%m-%d %H:%M")
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
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {"contacts": []}
        except (json.JSONDecodeError, ValueError):
            print(f"Error: {DATA_FILE} is corrupt or unreadable.")
            print("Fix the file manually or remove it to start fresh.")
            sys.exit(1)
    else:
        data = {"contacts": []}

    version = data.get("version", 0)
    if version > CURRENT_VERSION:
        print(f"Warning: data file is version {version}, but this crm is version {CURRENT_VERSION}.")
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
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
