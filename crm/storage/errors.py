"""Exceptions raised by storage backends."""


class StorageCorrupt(Exception):
    """Raised by a backend when stored data fails to parse."""


class ConcurrentWriteError(Exception):
    """Raised by a backend when a conditional write is rejected
    because the underlying object changed since it was last loaded."""
