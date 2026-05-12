"""Date parsing, relative-date formatting, and due/overdue bucketing.

`parse_date` currently prints on error and returns None — kept as-is for
backwards-compatible caller behavior. A future pass can swap it for a
raised exception once tests are in place.
"""

from datetime import datetime, timedelta


def parse_date(s, tz=None):
    if not s:
        return ""
    if s.startswith("+") and s.endswith("d"):
        try:
            days = int(s[1:-1])
        except ValueError:
            print(f"Invalid date: {s}. Use YYYY-MM-DD or +Nd")
            return None
        return (datetime.now(tz) + timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        print(f"Invalid date: {s}. Use YYYY-MM-DD or +Nd")
        return None
    return s


def relative_date(date_str, today_str):
    """Format a date relative to today."""
    if not date_str or not today_str:
        return date_str
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        t = datetime.strptime(today_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    diff = (d - t).days
    if diff < -1:
        return f"{abs(diff)}d overdue"
    if diff == -1:
        return "yesterday"
    if diff == 0:
        return "today"
    if diff == 1:
        return "tomorrow"
    if diff <= 14:
        return f"in {diff}d"
    return date_str


def bucket_due(contacts, today, cutoff):
    """Split contacts into (overdue, due) based on their next_date.

    overdue: next_date < today
    due:     today <= next_date <= cutoff
    Contacts with no next_date are skipped.

    Returned lists are in input order — callers sort as needed.
    """
    overdue = []
    due = []
    for c in contacts:
        nd = c.get("next_date", "")
        if not nd:
            continue
        if nd < today:
            overdue.append(c)
        elif nd <= cutoff:
            due.append(c)
    return overdue, due
