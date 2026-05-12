"""Pure contact-lookup logic.

find_contact: substring match across name/company/email (the legacy
"fuzzy" CLI argument lookup).

_contact_filter: broader predicate used by interactive pickers — matches
name, company, role, email, stage, source. Kept as `_contact_filter`
because it's a callback the curses picker consumes by reference.

search_contacts: full-text search across fields + note bodies. Returns
plain (contact, [match_strings]) tuples; the caller decides how to
render. Takes a `stamp_fmt` callback so the function stays free of
display/tz imports — caller passes a lambda that does tz conversion.

Mutation operations (add/edit/delete) still live in cli.py; they're
intertwined with interactive forms and will get cleaner extraction
when tests land.
"""


def find_contact(data, query):
    """Substring match on name/company/email. Case-insensitive."""
    query = query.lower()
    matches = []
    for c in data["contacts"]:
        if query in c["name"].lower() or query in c["company"].lower() or query in c["email"].lower():
            matches.append(c)
    return matches


def _contact_filter(c, query):
    """Picker filter predicate — matches across more fields than find_contact."""
    q = query.lower()
    return (q in c.get("name", "").lower()
            or q in c.get("company", "").lower()
            or q in c.get("role", "").lower()
            or q in c.get("email", "").lower()
            or q in c.get("stage", "").lower()
            or q in c.get("source", "").lower())


_SEARCHABLE_FIELDS = ["name", "company", "email", "phone", "role", "source", "next_action"]


def search_contacts(contacts, term, stamp_fmt=None):
    """Full-text search across contact fields and note bodies.

    Returns list of (contact, [match_str, ...]). Each match_str is
    pre-formatted so the caller just prints it.

    `stamp_fmt` is an optional callback `str -> str` for converting
    UTC note timestamps to display strings. Defaults to identity.
    """
    if stamp_fmt is None:
        stamp_fmt = lambda d: d  # noqa: E731
    term = term.lower()
    results = []
    for c in contacts:
        matches = []
        for field in _SEARCHABLE_FIELDS:
            if term in c.get(field, "").lower():
                matches.append(f"{field}: {c.get(field)}")
        for note in c.get("notes", []):
            if term in note["text"].lower():
                matches.append(f"[{stamp_fmt(note['date'])}] {note['text'][:60]}...")
        if matches:
            results.append((c, matches))
    return results
