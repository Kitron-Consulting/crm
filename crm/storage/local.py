"""Local-filesystem backend.

The store is a SQLite database file opened in place — sqlite handles atomicity
and intra-machine locking. A legacy JSON file at the same path is auto-upgraded
to SQLite on first open (see storage.open_db), so existing local users migrate
transparently.
"""

from pathlib import Path


class LocalBackend:
    is_remote = False

    def __init__(self, path):
        self.path = Path(path)

    def describe(self):
        return f"file:{self.path}"
