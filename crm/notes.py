"""Pure note operations.

UI (curses viewer, editor invocation) belongs in cli/display. This module
only handles data mutation and the canonical UTC timestamp format used
throughout the data file.
"""

from datetime import datetime, timezone


def utc_stamp():
    """Canonical 'YYYY-MM-DD HH:MM' UTC timestamp used for note dates,
    next-action transitions, and removed_at markers."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def add_note(contact, text, stamp=None):
    """Prepend a note to a contact's notes list. Mutates `contact` in place
    and returns the new note dict."""
    if stamp is None:
        stamp = utc_stamp()
    note = {"date": stamp, "text": text}
    contact.setdefault("notes", []).insert(0, note)
    return note


def edit_note(note, text):
    """Replace a note's text. Mutates `note` in place."""
    note["text"] = text


def delete_note(notes_list, note):
    """Remove a note from the list. Mutates `notes_list` in place."""
    notes_list.remove(note)
