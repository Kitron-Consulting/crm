"""Terminal output, ANSI colors, curses pickers, and interactive prompts.

All print/curses/input interaction lives here. Pure logic modules
(contacts, stages, due, notes, storage) do not import this module —
the cli layer wires them together.

The ANSI color constants are bound at import time based on whether
stdout is a TTY; non-TTY runs get empty strings so output stays clean
when piped or redirected.
"""

import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import curses

os.environ.setdefault("ESCDELAY", "25")

from .contacts import find_contact, _contact_filter
from .due import relative_date
from .stages import get_stages
from .storage import get_tz


# --- ANSI colors ---

if sys.stdout.isatty():
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    RESET = "\033[0m"
else:
    BOLD = DIM = RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = RESET = ""

STAGE_COLOR_CYCLE = [DIM, BLUE, CYAN, YELLOW, MAGENTA]
STAGE_COLOR_SPECIAL = {"won": GREEN, "lost": RED, "dormant": DIM}


def stage_color(stage, stages):
    if stage in STAGE_COLOR_SPECIAL:
        return STAGE_COLOR_SPECIAL[stage]
    try:
        idx = stages.index(stage)
    except ValueError:
        return ""
    return STAGE_COLOR_CYCLE[idx % len(STAGE_COLOR_CYCLE)]


def display_stamp(stamp, data):
    """Convert a UTC timestamp to local time for display."""
    if not stamp or len(stamp) <= 10:  # date-only or empty
        return stamp
    try:
        dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
        dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(get_tz(data))
        return local_dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return stamp


# --- $EDITOR + simple input prompts ---

def edit_text(initial="", header=""):
    """Open $EDITOR for text editing. Returns edited text or None if cancelled.

    header can be a string or a list of strings; each becomes a # comment line.
    """
    editor = os.environ.get("EDITOR", "nano")
    content = ""
    if header:
        lines = header if isinstance(header, list) else [header]
        for line in lines:
            content += f"# {line}\n"
        content += "# Lines starting with # are ignored.\n\n"
    content += initial

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp_path = f.name

    try:
        # Ensure terminal is in a clean state (curses may not fully restore)
        try:
            curses.endwin()
        except curses.error:
            pass
        subprocess.run([editor, tmp_path])
        with open(tmp_path, "r", encoding="utf-8") as f:
            result = f.read()
        # Strip comment lines and trim
        lines = [l for l in result.splitlines() if not l.startswith("#")]
        text = "\n".join(lines).strip()
        if not text:
            return None
        return text
    except Exception as e:
        print(f"{RED}Error: {e}{RESET}")
        return None
    finally:
        os.unlink(tmp_path)


def prompt_input(label, default="", required=False):
    """Styled input prompt with optional default."""
    if default:
        display = f"  {BOLD}{label}{RESET} {DIM}[{default}]{RESET}: "
    else:
        req = f" {RED}*{RESET}" if required else ""
        display = f"  {BOLD}{label}{RESET}{req}: "
    try:
        value = input(display).strip()
        if not value and default:
            return default
        if not value and required:
            print(f"  {RED}Required.{RESET}")
            return None
        return value
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def prompt_confirm(message, default=False):
    """Styled y/N confirmation prompt."""
    hint = f"{BOLD}y{RESET}/{DIM}N{RESET}" if not default else f"{DIM}y{RESET}/{BOLD}N{RESET}"
    try:
        answer = input(f"  {message} [{hint}] ").strip().lower()
        if not answer:
            return default
        return answer == "y"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# --- curses primitives ---

ANSI_RE = re.compile(r'\033\[([0-9;]*)m')

CURSES_COLOR_MAP = {}  # initialized in curses functions


def _init_curses_colors():
    curses.use_default_colors()
    pairs = [
        (1, curses.COLOR_GREEN),
        (2, curses.COLOR_RED),
        (3, curses.COLOR_CYAN),
        (4, curses.COLOR_YELLOW),
        (5, curses.COLOR_BLUE),
        (6, curses.COLOR_MAGENTA),
    ]
    for pair_id, color in pairs:
        curses.init_pair(pair_id, color, -1)
    CURSES_COLOR_MAP.update({
        31: curses.color_pair(2),  # red
        32: curses.color_pair(1),  # green
        33: curses.color_pair(4),  # yellow
        34: curses.color_pair(5),  # blue
        35: curses.color_pair(6),  # magenta
        36: curses.color_pair(3),  # cyan
    })


def _addstr_ansi(stdscr, row, col, text, max_w, extra_attr=0):
    """Render ANSI-colored text using curses attributes."""
    pos = col
    attr = extra_attr
    for part in ANSI_RE.split(text):
        if not part:
            continue
        # Check if this is an ANSI code parameter
        if part.isdigit() or (';' in part and all(p.isdigit() for p in part.split(';'))):
            codes = [int(c) for c in part.split(';') if c]
            for code in codes:
                if code == 0:
                    attr = extra_attr
                elif code == 1:
                    attr |= curses.A_BOLD
                elif code == 2:
                    attr |= curses.A_DIM
                elif code in CURSES_COLOR_MAP:
                    # Clear previous color, add new
                    for cv in CURSES_COLOR_MAP.values():
                        attr &= ~cv
                    attr |= CURSES_COLOR_MAP[code]
        else:
            # Regular text
            remaining = max_w - (pos - col)
            if remaining <= 0:
                break
            text_part = part[:remaining]
            try:
                stdscr.addstr(row, pos, text_part, attr)
            except curses.error:
                pass
            pos += len(text_part)


def _curses_pick_one(stdscr, items, prompt, format_fn, filter_fn):
    """Picker implementation using curses."""
    curses.curs_set(0)
    _init_curses_colors()

    cursor = 0
    query = ""
    filtered = list(items)

    # Strip ANSI codes for display in curses
    ansi_re = re.compile(r'\033\[[0-9;]*m')
    def strip_ansi(s):
        return ansi_re.sub('', s)

    def do_filter():
        nonlocal filtered, cursor
        if not query:
            filtered = list(items)
        elif filter_fn:
            filtered = [item for item in items if filter_fn(item, query)]
        else:
            q = query.lower()
            filtered = [item for item in items if q in strip_ansi(format_fn(item)).lower()]
        cursor = min(cursor, max(0, len(filtered) - 1))

    need_clear = False
    prev_size = None
    while True:
        try:
            ts = os.get_terminal_size()
            new_size = (ts.lines, ts.columns)
        except OSError:
            new_size = stdscr.getmaxyx()
        if new_size != prev_size:
            curses.resizeterm(*new_size)
            stdscr.clear()
            prev_size = new_size
        elif need_clear:
            stdscr.clear()
            need_clear = False
        else:
            stdscr.erase()
        h, w = stdscr.getmaxyx()

        stdscr.addnstr(0, 0, f"{prompt}:", w - 1, curses.A_BOLD)

        if query:
            stdscr.addnstr(1, 2, f"Filter: {query}", w - 3)
        else:
            stdscr.addnstr(1, 2, "Type to filter...", w - 3, curses.A_DIM)

        max_visible = h - 5
        total = len(filtered)

        if total <= max_visible:
            start, end = 0, total
        else:
            half = max_visible // 2
            if cursor < half:
                start = 0
            elif cursor >= total - half:
                start = total - max_visible
            else:
                start = cursor - half
            end = start + max_visible

        row = 3
        if not filtered:
            stdscr.addnstr(row, 2, "No matches", w - 3, curses.A_DIM)
            row += 1
        else:
            if start > 0:
                stdscr.addnstr(row, 4, f"↑ {start} more", w - 5, curses.A_DIM)
                row += 1
            for i in range(start, end):
                label = format_fn(filtered[i])
                if i == cursor:
                    try:
                        stdscr.addstr(row, 2, "▸ ", curses.color_pair(1))
                    except curses.error:
                        pass
                    _addstr_ansi(stdscr, row, 4, label, w - 5, curses.A_BOLD)
                else:
                    _addstr_ansi(stdscr, row, 4, label, w - 5)
                row += 1
            if end < total:
                stdscr.addnstr(row, 4, f"↓ {total - end} more", w - 5, curses.A_DIM)
                row += 1

        footer = "↑↓ navigate · enter select · esc cancel"
        stdscr.addnstr(h - 1, 2, footer, w - 3, curses.A_DIM)

        stdscr.refresh()

        try:
            key = stdscr.get_wch()
        except curses.error:
            need_clear = True
            continue

        if key == curses.KEY_RESIZE:
            need_clear = True
            continue
        elif key == "\x1b" or key == "\x03":  # Esc or Ctrl-C
            return None
        elif key == "\n" or key == "\r" or key == curses.KEY_ENTER:
            if filtered:
                return filtered[cursor]
            return None
        elif key == curses.KEY_UP:
            if filtered:
                cursor = (cursor - 1) % len(filtered)
        elif key == curses.KEY_DOWN:
            if filtered:
                cursor = (cursor + 1) % len(filtered)
        elif key in ("\x7f", "\x08", curses.KEY_BACKSPACE):
            if query:
                query = query[:-1]
                do_filter()
        elif isinstance(key, str) and key.isprintable():
            query += key
            do_filter()


def _curses_form_edit(stdscr, fields, title, pick_fn):
    """Form implementation using curses."""
    curses.curs_set(1)
    _init_curses_colors()

    active = 0
    cursor_pos = [len(f.get("value", "")) for f in fields]
    values = [f.get("value", "") for f in fields]
    errors = ["" for _ in fields]
    on_save = False

    def validate_field(i):
        f = fields[i]
        v = values[i]
        if f.get("required") and not v:
            errors[i] = "Required"
            return
        vfn = f.get("validate")
        if vfn and v:
            err = vfn(v)
            errors[i] = err or ""
        else:
            errors[i] = ""

    need_clear = False
    prev_size = None
    while True:
        try:
            ts = os.get_terminal_size()
            new_size = (ts.lines, ts.columns)
        except OSError:
            new_size = stdscr.getmaxyx()
        if new_size != prev_size:
            curses.resizeterm(*new_size)
            stdscr.clear()
            prev_size = new_size
        elif need_clear:
            stdscr.clear()
            need_clear = False
        else:
            stdscr.erase()
        h, w = stdscr.getmaxyx()
        row = 0

        if title:
            stdscr.addnstr(row, 0, title, w - 1, curses.A_BOLD)
            row += 2

        for i, f in enumerate(fields):
            name = f["name"]
            v = values[i]
            err = errors[i]
            is_active = (i == active and not on_save)

            if is_active:
                active_row = row
                stdscr.addnstr(row, 2, "▸ ", w - 3, curses.color_pair(1))
                stdscr.addnstr(row, 4, f"{name}: ", w - 5, curses.A_BOLD)
                field_col = 4 + len(name) + 2
                avail = w - field_col - 1
                cp = cursor_pos[i]
                # Horizontal scroll: show a window of text around cursor
                if avail > 0:
                    if cp < avail:
                        scroll = 0
                    else:
                        scroll = cp - avail + 1
                    visible = v[scroll:scroll + avail]
                    stdscr.addnstr(row, field_col, visible, avail)
                    cursor_screen_col = field_col + cp - scroll
                else:
                    cursor_screen_col = field_col
            else:
                stdscr.addnstr(row, 4, f"{name}:", w - 5, curses.A_DIM)
                val_col = 4 + len(name) + 2
                avail = w - val_col - 1
                if avail > 0:
                    if f.get("options") and v:
                        stdscr.addnstr(row, val_col, v[:avail], avail, curses.color_pair(3))
                    elif v:
                        stdscr.addnstr(row, val_col, v[:avail], avail)
                    else:
                        stdscr.addnstr(row, val_col, "—", avail, curses.A_DIM)

            if err:
                err_col = min(4 + len(name) + 2 + len(v) + 1, w - len(err) - 4)
                if 0 < err_col < w - 3:
                    try:
                        stdscr.addnstr(row, err_col, f"← {err}", w - err_col - 1, curses.color_pair(2))
                    except curses.error:
                        pass
            elif is_active and f.get("required") and not v:
                hint_col = 4 + len(name) + 2 + 1
                if hint_col < w - 12:
                    try:
                        stdscr.addnstr(row, hint_col, "(required)", w - hint_col - 1, curses.A_DIM)
                    except curses.error:
                        pass
            elif is_active and f.get("options"):
                hint_col = min(4 + len(name) + 2 + len(v) + 1, w - 15)
                if 0 < hint_col < w - 3:
                    try:
                        stdscr.addnstr(row, hint_col, "(tab to pick)", w - hint_col - 1, curses.A_DIM)
                    except curses.error:
                        pass

            row += 1

        row += 1
        if on_save:
            stdscr.addnstr(row, 2, "▸ [ Save ]", w - 3, curses.color_pair(1) | curses.A_BOLD)
        else:
            stdscr.addnstr(row, 4, "[ Save ]", w - 5, curses.A_DIM)

        footer = "↑↓ navigate · tab pick option · enter save · esc cancel"
        stdscr.addnstr(h - 1, 2, footer, w - 3, curses.A_DIM)

        # Position cursor after all rendering
        if on_save:
            curses.curs_set(0)
        else:
            curses.curs_set(1)
            try:
                stdscr.move(active_row, cursor_screen_col)
            except curses.error:
                pass

        stdscr.refresh()

        try:
            key = stdscr.get_wch()
        except curses.error:
            need_clear = True
            continue

        if key == curses.KEY_RESIZE:
            need_clear = True
            continue
        elif key == "\x1b" or key == "\x03":
            return None

        elif key == "\n" or key == "\r" or key == curses.KEY_ENTER:
            if on_save:
                has_error = False
                for i in range(len(fields)):
                    validate_field(i)
                    if errors[i]:
                        has_error = True
                if has_error:
                    on_save = False
                    for i in range(len(fields)):
                        if errors[i]:
                            active = i
                            break
                    continue
                return {f["name"]: values[i] for i, f in enumerate(fields)}
            else:
                validate_field(active)
                if active < len(fields) - 1:
                    active += 1
                    cursor_pos[active] = len(values[active])
                else:
                    on_save = True

        elif key == curses.KEY_UP:
            if on_save:
                on_save = False
                active = len(fields) - 1
                cursor_pos[active] = len(values[active])
            elif active > 0:
                validate_field(active)
                active -= 1
                cursor_pos[active] = len(values[active])

        elif key == curses.KEY_DOWN:
            if not on_save:
                validate_field(active)
                if active < len(fields) - 1:
                    active += 1
                    cursor_pos[active] = len(values[active])
                else:
                    on_save = True

        elif key == "\t":
            if not on_save and fields[active].get("options"):
                picked = pick_fn(fields[active]["options"], f"Select {fields[active]['name']}")
                if picked:
                    values[active] = picked
                    cursor_pos[active] = len(picked)

        elif key == curses.KEY_LEFT:
            if not on_save and cursor_pos[active] > 0:
                cursor_pos[active] -= 1

        elif key == curses.KEY_RIGHT:
            if not on_save and cursor_pos[active] < len(values[active]):
                cursor_pos[active] += 1

        elif key == curses.KEY_HOME or key == "\x01":
            if not on_save:
                cursor_pos[active] = 0

        elif key == curses.KEY_END or key == "\x05":
            if not on_save:
                cursor_pos[active] = len(values[active])

        elif key in ("\x7f", "\x08", curses.KEY_BACKSPACE):
            if not on_save and not fields[active].get("options") and cursor_pos[active] > 0:
                v = values[active]
                p = cursor_pos[active]
                values[active] = v[:p-1] + v[p:]
                cursor_pos[active] -= 1
                errors[active] = ""

        elif isinstance(key, str) and key.isprintable() and not on_save and not fields[active].get("options"):
            v = values[active]
            p = cursor_pos[active]
            values[active] = v[:p] + key + v[p:]
            cursor_pos[active] += 1
            errors[active] = ""


def form_edit(fields, title=""):
    """Interactive form editor using curses.

    fields: list of dicts with keys:
        name: field label
        value: current value (string)
        required: bool (optional)
        options: list of strings for picker fields (optional)
        validate: fn(value) -> error string or None (optional)

    Returns dict of {name: value} or None if cancelled.
    """
    if not sys.stdin.isatty():
        return _form_edit_simple(fields, title)

    result = [None]

    def run(stdscr):
        def pick_fn(options, prompt):
            return _curses_pick_one(stdscr, options, prompt, str, None)
        result[0] = _curses_form_edit(stdscr, fields, title, pick_fn)

    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
    return result[0]


def _form_edit_simple(fields, title=""):
    """Fallback form for non-interactive terminals."""
    if title:
        print(f"\n{title}")
    result = {}
    for f in fields:
        v = prompt_input(f["name"], default=f.get("value", ""), required=f.get("required", False))
        if v is None:
            return None
        vfn = f.get("validate")
        if vfn and v:
            err = vfn(v)
            if err:
                print(f"  {RED}{err}{RESET}")
                return None
        result[f["name"]] = v
    return result


def pick_one(items, prompt="Select", format_fn=None, filter_fn=None):
    """Interactive picker with arrow keys and type-to-filter."""
    if not items:
        print("No items to select from.")
        return None

    if format_fn is None:
        format_fn = str

    if not sys.stdin.isatty():
        return _pick_one_simple(items, prompt, format_fn)

    result = [None]

    def run(stdscr):
        result[0] = _curses_pick_one(stdscr, items, prompt, format_fn, filter_fn)

    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
    return result[0]


def _pick_one_simple(items, prompt, format_fn):
    """Fallback picker for non-interactive terminals."""
    ansi_re = re.compile(r'\033\[[0-9;]*m')
    print(f"\n{prompt}:")
    for i, item in enumerate(items, 1):
        print(f"  {i}. {format_fn(item)}")
    print(f"  0. Cancel")
    try:
        choice = input("\n> ").strip()
        if not choice or choice == "0":
            return None
        idx = int(choice) - 1
        if 0 <= idx < len(items):
            return items[idx]
        print("Invalid selection.")
        return None
    except (ValueError, EOFError, KeyboardInterrupt):
        print()
        return None


# --- contact formatters and pickers ---

def format_contact_option(c, stages=None):
    role = c.get('role', '')
    role_str = f" {DIM}—{RESET} {CYAN}{role}{RESET}" if role else ""
    s = c['stage']
    sc = stage_color(s, stages or [])
    return f"{BOLD}{c['name']}{RESET} {DIM}({RESET}{c['company']}{role_str}{DIM}){RESET} {sc}[{s.upper()}]{RESET}"


def pick_contact_from_all(data, prompt="Select contact"):
    """Pick any contact interactively."""
    stages = get_stages(data)
    order = {s: i for i, s in enumerate(stages)}
    contacts = sorted(data["contacts"], key=lambda c: (order.get(c["stage"], 99), c["name"]))
    fmt = lambda c: format_contact_option(c, stages)
    return pick_one(contacts, prompt=prompt, format_fn=fmt, filter_fn=_contact_filter)


def pick_contact_from_matches(data, matches):
    """Pick from search results."""
    if len(matches) == 1:
        return matches[0]
    stages = get_stages(data)
    fmt = lambda c: format_contact_option(c, stages)
    return pick_one(matches, prompt=f"Multiple matches ({len(matches)})", format_fn=fmt, filter_fn=_contact_filter)


def get_contact(data, query=None, prompt="Select contact"):
    """Get a contact - by query if provided, or interactive picker."""
    if query:
        matches = find_contact(data, query)
        if not matches:
            print(f"No contacts matching '{query}'")
            return None
        return pick_contact_from_matches(data, matches)
    else:
        return pick_contact_from_all(data, prompt)


def format_contact_line(c, stages=None, today=None):
    s = c["stage"]
    sc = stage_color(s, stages or [])
    due = c.get("next_date", "")
    action = c.get("next_action", "")
    if due and action:
        overdue = today and due < today
        rel = relative_date(due, today)
        if overdue:
            due_str = f" {DIM}→{RESET} {RED}{BOLD}{rel}{RESET}{DIM}:{RESET} {RED}{action}{RESET}"
        else:
            due_str = f" {DIM}→{RESET} {GREEN}{rel}{RESET}{DIM}:{RESET} {action}"
    else:
        due_str = ""
    role = c.get('role', '')
    role_str = f" {DIM}—{RESET} {CYAN}{role}{RESET}" if role else ""
    return f"  {BOLD}{c['name']}{RESET} {DIM}({RESET}{c['company']}{role_str}{DIM}){RESET} {sc}[{s.upper()}]{RESET}{due_str}"
