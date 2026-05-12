"""Local-filesystem JSON backend.

Atomic writes via tempfile + os.replace so a mid-write crash can't
truncate the data file. Parent directory is created on first save.
"""

import json
import os
from pathlib import Path

from .errors import StorageCorrupt


class LocalBackend:
    def __init__(self, path):
        self.path = Path(path)

    def describe(self):
        return f"file:{self.path}"

    def load(self):
        if not self.path.exists():
            return {"contacts": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            raise StorageCorrupt(f"{self.path}: {e}") from e
        if not isinstance(data, dict):
            return {"contacts": []}
        return data

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise
