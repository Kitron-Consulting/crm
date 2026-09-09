"""Stage and source vocabulary, plus accessors over the config blob.

Pure logic — takes/returns plain data. No print, no sys.exit.
"""

DEFAULT_STAGES = ["cold", "contacted", "responded", "meeting", "proposal", "won", "lost", "dormant"]
DEFAULT_SOURCES = ["cold", "referral", "inbound"]


import re

# The note the CLI and web UI have always written on a stage move.
STAGE_NOTE_RE = re.compile(r"^Stage:\s*(.+?)\s*(?:→|->)\s*(.+?)\s*$")


def get_stages(data):
    return data["config"]["stages"]


def get_sources(data):
    return data["config"]["sources"]


def record_stage_change(contact, old, new, stamp):
    """Append a structured `stage_history` entry for a stage move.

    Additive to the "Stage: a → b" note (kept for humans); this is the exact,
    parse-free record the Timeline reads. `from` is "" for the creation entry.
    """
    if old == new:
        return
    contact.setdefault("stage_history", []).append(
        {"date": stamp, "from": old or "", "to": new})


def _day(stamp):
    """'YYYY-MM-DD[ HH:MM]' -> 'YYYY-MM-DD', else None."""
    s = (stamp or "")[:10]
    return s if len(s) == 10 and s[4] == "-" and s[7] == "-" else None


def stage_segments(contact, today):
    """Chronological stage segments for a contact: [{stage, start, end}].

    The last segment is the current stage with end=None (ongoing). Prefers
    structured `stage_history`; otherwise reconstructs moves from the
    "Stage: a → b" notes; a contact with neither is one ongoing segment from
    its earliest note (or today). If the derived history doesn't end in the
    contact's actual stage (e.g. a move made without a note), the ongoing
    segment is relabelled to the real stage and flagged approx=True.
    Dates are day-granular; pass already-localised stamps for local days.
    """
    changes = []
    for e in contact.get("stage_history") or []:
        d = _day(e.get("date"))
        if d and e.get("to"):
            changes.append((d, e.get("from") or "", e["to"]))
    if not changes:
        for n in contact.get("notes") or []:
            m = STAGE_NOTE_RE.match((n.get("text") or "").strip())
            d = _day(n.get("date"))
            if m and d:
                changes.append((d, m.group(1), m.group(2)))
    changes.sort(key=lambda c: c[0])

    note_days = [d for d in (_day(n.get("date")) for n in contact.get("notes") or []) if d]
    created = min(note_days) if note_days else (changes[0][0] if changes else today)
    if changes and changes[0][0] < created:
        created = changes[0][0]

    current = contact.get("stage") or ""
    # Initial stage: what the first move came *from*, unless the first entry is
    # the creation entry (from == "") — then the contact started in its `to`.
    if changes and changes[0][1] == "":
        stage, start = changes[0][2], changes[0][0]
        rest = changes[1:]
    else:
        stage, start = (changes[0][1] if changes else current), created
        rest = changes

    segments = []
    for day, frm, to in rest:
        if frm == "" or to == stage:
            continue                      # stray creation entry / no-op
        segments.append({"stage": stage, "start": start, "end": day})
        stage, start = to, day

    last = {"stage": stage, "start": start, "end": None}
    if current and stage != current:
        last["stage"], last["approx"] = current, True   # moved without a record
    segments.append(last)
    return segments
