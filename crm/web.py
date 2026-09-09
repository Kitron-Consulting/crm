"""`crm serve` — a local web UI for the tasks a terminal handles badly.

Stdlib only (http.server): no new dependencies, so the PyApp binary stays
lean. Binds to 127.0.0.1 and reuses the same storage backend and mail module
as the CLI, so your data (local or S3) and OAuth token never leave the box.
A per-run token gates the /api/* endpoints so a stray web page (or a DNS
rebind) can't drive your CRM.

The frontend is a Svelte 5 app in web/ (see web/README.md), built by Vite into
the single self-contained file crm/static/index.html that GET / serves.
Screens: a drag-and-drop pipeline board, a sortable contacts table, a due
dashboard, a next-action calendar (drag to reschedule), a stage-history
timeline (segments derived from stage_history / "Stage:" notes), a contact drawer
(fields, notes, next action, done, email thread with attachments and meeting
cards, remove), and import triage (add / edit / ignore the people you've
emailed).

The API logic (build_state / api_* functions) is kept free of the HTTP layer
so it can be unit-tested without opening a socket. Each api_* function takes
the loaded `data` dict plus request params, mutates `data` in place where
relevant, and returns the JSON-ready response; the HTTP handler persists.
Every mutation mirrors the corresponding CLI command (cmd_add, cmd_stage,
cmd_note, cmd_next, cmd_done, cmd_rm) so the data file looks the same no
matter which front end wrote it.

Contact identity: contacts carry no ids, so the API uses the list index as
`id`. Mutation bodies carry {"id", "name"} and both must match the current
list, otherwise the request is rejected as stale (HTTP 409).
"""

import json
import secrets
import sys
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .display import display_stamp
from .stages import stage_segments
from .storage import current_backend, get_tz, open_db, push_db, close_db
from .storage.errors import ConcurrentWriteError
# The Svelte UI (web/, built with Vite + vite-plugin-singlefile) compiles to
# this single self-contained file. It's gitignored build output, shipped in the
# wheel via hatchling `artifacts` (CI builds it before the wheel). Read per
# request so a rebuild is picked up without restarting the server.
_STATIC_INDEX = Path(__file__).parent / "static" / "index.html"

# Shown only in a dev checkout where the bundle hasn't been built yet.
_UNBUILT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>crm — UI not built</title>
<style>body{font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:60px auto;padding:0 20px;color:#222}
code,pre{background:#f3f3f1;border-radius:6px;padding:2px 6px}pre{padding:12px}</style></head>
<body><h1>Web UI not built</h1>
<p>The frontend bundle <code>crm/static/index.html</code> is missing. Build it once:</p>
<pre>cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>Then reload this page — the server picks up the build without restarting.</p>
<p>For live-reload development: <code>cd web &amp;&amp; npm run dev</code>, then open the
Vite URL with your <code>?t=…</code> token appended.</p>
</body></html>"""


def _index_html():
    try:
        return _STATIC_INDEX.read_text(encoding="utf-8")
    except OSError:
        return _UNBUILT_HTML


class StaleError(Exception):
    """The {id, name} in a mutation body no longer matches the contact list."""


STALE_MESSAGE = "Contact changed — reload and retry."

# The only contact keys the add/update endpoints will write.
CONTACT_FIELDS = ("name", "email", "phone", "company", "role", "source", "stage")

# Keys of a fetch_thread message that are JSON-serializable (drops `dt`).
THREAD_MESSAGE_KEYS = ("date", "direction", "from", "to", "subject", "body", "quoted",
                       "uid", "folder", "attachments", "event")

# Attachment types the browser may render inline. Deliberately excludes
# text/html, image/svg+xml and any XML: served from the app's origin they
# could execute script. Mirrored in the frontend for the open-vs-download UX.
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif",
                "image/webp", "image/bmp", "text/plain"}


# --- Helpers --------------------------------------------------------------

def _today(db):
    """Today's date in the user's configured tz — same as the CLI's overdue math."""
    return datetime.now(get_tz(db)).strftime("%Y-%m-%d")


def _serialize_contact(db, c):
    """JSON-ready copy of a DAL contact: converts note/stage stamps to local
    time (like `display_stamp`) and adds derived `segments`. `c` already carries
    its stable `id`, `notes` and `stage_history`."""
    out = dict(c)
    out["notes"] = [
        {**n, "date": display_stamp(n.get("date", ""), db)}
        for n in c.get("notes", [])
    ]
    out["stage_history"] = [
        {**e, "date": display_stamp(e.get("date", ""), db)}
        for e in c.get("stage_history", [])
    ]
    # Timeline bars, derived from the localised copy so day boundaries are local.
    out["segments"] = stage_segments(out, _today(db))
    return out


def _get_contact(db, body):
    """Resolve {id, name} from a mutation body to a DAL contact dict.

    Raises StaleError if the id is gone or the name no longer matches — guards
    against acting on a contact another client renamed or removed. Ids are now
    stable PKs, so a stale *position* can no longer point at the wrong contact."""
    cid = body.get("id")
    if isinstance(cid, bool) or not isinstance(cid, int):
        raise StaleError(STALE_MESSAGE)
    c = db.get_contact(cid)
    if c is None or c.get("removed_at") is not None or c.get("name") != body.get("name"):
        raise StaleError(STALE_MESSAGE)
    return c


def _resolve_date(s, tz):
    """Same rules as due.parse_date ("YYYY-MM-DD" or "+Nd" relative to today
    in `tz`) but raises ValueError instead of printing and returning None."""
    if s.startswith("+") and s.endswith("d"):
        try:
            days = int(s[1:-1])
        except ValueError:
            raise ValueError(f"Invalid date: {s}. Use YYYY-MM-DD or +Nd")
        return (datetime.now(tz) + timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Invalid date: {s}. Use YYYY-MM-DD or +Nd")
    return s


def _validate_email(email):
    if email and "@" not in email:
        raise ValueError(f"Invalid email: {email}")


def _validate_stage(stage, stages):
    if stage not in stages:
        raise ValueError(f"Invalid stage: {stage}. Use: {', '.join(stages)}")


def _validate_source(source, sources):
    if source not in sources:
        raise ValueError(f"Invalid source: {source}. Use: {', '.join(sources)}")


def _str(v):
    return "" if v is None else str(v)


# --- API logic (no HTTP; unit-testable) ---------------------------------

def build_state(db):
    """Snapshot the board needs: contacts (with ids, local note dates, and the
    next upcoming meeting when cached), the stage/source vocabularies, and
    today's date in the user's tz."""
    now = datetime.now(get_tz(db))
    upcoming = db.next_meetings(now.strftime("%Y-%m-%d %H:%M"))
    contacts = []
    for c in db.list_contacts():
        sc = _serialize_contact(db, c)
        if c["id"] in upcoming:
            sc["next_meeting"] = upcoming[c["id"]]
        contacts.append(sc)
    return {
        "contacts": contacts,
        "stages": db.stages(),
        "sources": db.sources(),
        "today": now.strftime("%Y-%m-%d"),
        "meetings_fetched_at": db.meetings_fetched_at(),
    }


def api_add_contact(db, body):
    """Create a contact like `cmd_add` (non-interactive mode).

    body = {"fields": {name, email, phone, company, role, source, stage}}.
    Name required; email must contain "@" if given; source/stage default to
    "cold" and must be in the vocabulary. Adds the "Added to CRM" note.
    """
    fields = body.get("fields") or {}
    name = _str(fields.get("name")).strip()
    if not name:
        raise ValueError("Name is required.")
    email = _str(fields.get("email")).strip()
    _validate_email(email)
    source = _str(fields.get("source")).strip() or "cold"
    _validate_source(source, db.sources())
    stage = _str(fields.get("stage")).strip() or "cold"
    _validate_stage(stage, db.stages())

    contact = db.add_contact({
        "name": name, "email": email, "phone": _str(fields.get("phone")),
        "company": _str(fields.get("company")), "role": _str(fields.get("role")),
        "source": source, "stage": stage,
    })
    return {"contact": _serialize_contact(db, contact)}


def api_update_contact(db, body):
    """Update editable fields like `cmd_edit --flag value`; a stage change
    additionally records the `cmd_stage` note ("Stage: old → new").

    body = {"id", "name", "fields": {subset of CONTACT_FIELDS}}. Unknown
    keys are ignored. Validation happens before any field is written.
    """
    c = _get_contact(db, body)
    fields = body.get("fields") or {}

    updates = {}
    for key in CONTACT_FIELDS:
        if key not in fields:
            continue
        val = _str(fields[key])
        if key in ("name", "email", "stage", "source"):
            val = val.strip()
        if key == "name" and not val:
            raise ValueError("Name is required.")
        if key == "email":
            _validate_email(val)
        if key == "stage":
            _validate_stage(val, db.stages())
        if key == "source":
            _validate_source(val, db.sources())
        updates[key] = val

    old_stage = c.get("stage")
    db.update_contact(c["id"], updates)
    new_stage = updates.get("stage", old_stage)
    if "stage" in updates and new_stage != old_stage:
        db.add_note(c["id"], f"Stage: {old_stage} → {new_stage}")
        db.record_stage_change(c["id"], old_stage, new_stage)

    return {"contact": _serialize_contact(db, db.get_contact(c["id"]))}


def api_add_note(db, body):
    """Prepend a timestamped note like `cmd_note`. body = {"id", "name", "text"}."""
    c = _get_contact(db, body)
    text = _str(body.get("text")).strip()
    if not text:
        raise ValueError("Note text is required.")
    db.add_note(c["id"], text)
    return {"contact": _serialize_contact(db, db.get_contact(c["id"]))}


def api_set_next(db, body):
    """Set next action + due date like `cmd_next`.

    body = {"id", "name", "action", "date"}; `date` is "YYYY-MM-DD" or a
    relative "+Nd" (resolved against today in the user's tz). Both required.
    """
    c = _get_contact(db, body)
    action = _str(body.get("action")).strip()
    if not action:
        raise ValueError("Action is required.")
    date = _str(body.get("date")).strip()
    if not date:
        raise ValueError("Due date is required. Use YYYY-MM-DD or +Nd")
    parsed = _resolve_date(date, get_tz(db))
    db.set_next(c["id"], action, parsed)
    return {"contact": _serialize_contact(db, db.get_contact(c["id"]))}


def api_done(db, body):
    """Complete the pending action like `cmd_done`: note "Done: <action>"
    and clear next_action/next_date. No pending action → ValueError (400)."""
    c = _get_contact(db, body)
    action = c.get("next_action", "")
    if not action:
        raise ValueError(f"No action set for {c['name']}.")
    db.add_note(c["id"], f"Done: {action}")
    db.clear_next(c["id"])
    return {"contact": _serialize_contact(db, db.get_contact(c["id"]))}


def api_remove_contact(db, body):
    """Soft-delete like `cmd_rm contact -y`: set removed_at. body = {"id", "name"}."""
    c = _get_contact(db, body)
    db.remove_contact(c["id"])
    return {"ok": True}


def api_thread(db, email):
    """Recent email exchange with `email` via IMAP (newest first), as JSON-
    ready dicts (the non-serializable `dt` is dropped).

    Raises RuntimeError when IMAP isn't configured, ValueError on a blank
    address; network/auth failures propagate (HTTP 500).
    """
    from . import mail

    imap_cfg = db.imap()
    if not imap_cfg:
        raise RuntimeError("IMAP not configured — add config.imap to view threads.")
    email = _str(email).strip()
    if not email:
        raise ValueError("email is required.")
    messages = mail.fetch_thread(imap_cfg, email, tz=get_tz(db))
    return {"messages": [{k: m.get(k) for k in THREAD_MESSAGE_KEYS} for m in messages]}


def api_attachment(db, folder, uid, part):
    """Fetch one attachment part; returns (bytes, content_type, filename).

    ValueError on bad params (400), RuntimeError when IMAP isn't configured
    (400), LookupError when the message/part doesn't exist (404).
    """
    from . import mail

    imap_cfg = db.imap()
    if not imap_cfg:
        raise RuntimeError("IMAP not configured — add config.imap to download attachments.")
    folder, uid, part = _str(folder).strip(), _str(uid).strip(), _str(part).strip()
    if not folder:
        raise ValueError("folder is required.")
    if not uid.isdigit() or not part.isdigit():
        raise ValueError("uid and part must be numeric.")
    return mail.fetch_attachment(imap_cfg, folder, uid, part)


def api_scan(db, days):
    """Return importable candidates from the Sent folder.

    Raises RuntimeError with a friendly message when IMAP isn't configured.
    """
    from . import mail
    from .cli import import_candidates

    imap_cfg = db.imap()
    if not imap_cfg:
        raise RuntimeError("IMAP not configured — add config.imap to use import.")
    recipients = mail.fetch_sent_recipients(imap_cfg, since_days=days)
    return {"candidates": import_candidates(db, recipients)}


def api_commit(db, body):
    """Apply an import commit.

    body = {"add": [contact-like dicts], "ignore": [email, ...]}. Contacts are
    inserted with an import note; ignore entries are merged (lowercased) into
    config.import_ignore. Returns a summary dict.
    """
    stages = db.stages()
    sources = db.sources()
    added = 0
    for c in body.get("add", []):
        email = (c.get("email") or "").strip()
        name = (c.get("name") or "").strip() or email.split("@")[0]
        if not email:
            continue
        stage = c.get("stage") if c.get("stage") in stages else "contacted"
        source = c.get("source") if c.get("source") in sources else "cold"
        db.add_contact({
            "name": name, "email": email, "phone": c.get("phone", ""),
            "company": c.get("company", ""), "role": c.get("role", ""),
            "source": source, "stage": stage,
        }, note_text="Imported from sent mail")
        added += 1

    ignored = 0
    if body.get("ignore"):
        lst = db.get_config("import_ignore", [])
        for em in body["ignore"]:
            em = (em or "").strip().lower()
            if em and em not in lst:
                lst.append(em)
                ignored += 1
        db.set_config("import_ignore", lst)

    return {"added": added, "ignored": ignored}


def api_refresh_meetings(db, body):
    """Rescan the mailbox for upcoming meetings and rebuild the (synced) cache.
    body = {"days": int?}. Returns {"count", "fetched_at"}."""
    from . import mail

    imap_cfg = db.imap()
    if not imap_cfg:
        raise RuntimeError("IMAP not configured — add config.imap to sync meetings.")
    days = body.get("days")
    kwargs = {"days_back": int(days)} if days else {}
    meetings = mail.fetch_upcoming_meetings(imap_cfg, tz=get_tz(db), **kwargs)
    db.set_meetings(meetings)
    return {"count": len(meetings), "fetched_at": db.meetings_fetched_at()}


# POST routes that load → mutate → save. Each maps to an api_* function
# taking (data, body) and returning the response dict.
_MUTATIONS = {
    "/api/contacts/add": api_add_contact,
    "/api/contacts/update": api_update_contact,
    "/api/contacts/note": api_add_note,
    "/api/contacts/next": api_set_next,
    "/api/contacts/done": api_done,
    "/api/contacts/remove": api_remove_contact,
    "/api/import/commit": api_commit,
    "/api/meetings/refresh": api_refresh_meetings,
}


# --- HTTP layer ----------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "crm-serve"

    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, status, body, content_type="application/json"):
        if content_type == "application/json":
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, data, content_type, filename, force_download=False):
        """Serve attachment bytes.

        Previewable types (PDF, common images, plain text) go out inline so the
        browser shows them in a tab. Everything else — notably anything
        HTML/SVG/XML, which could run script inside the app's origin — is
        forced to download; nosniff and a deny-all CSP back that up. The
        filename is sent as an ASCII fallback plus RFC 5987 UTF-8 (http.server
        headers are latin-1).
        """
        ctype = (content_type or "application/octet-stream").lower()
        disposition = "inline" if ctype in INLINE_TYPES and not force_download else "attachment"
        safe = (filename or "attachment").replace('"', "'").replace("\\", "_")
        safe = safe.replace("\r", " ").replace("\n", " ")
        ascii_name = safe.encode("ascii", "replace").decode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(safe)}')
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self, query):
        token = self.server.token
        supplied = self.headers.get("X-CRM-Token") or (query.get("t", [""])[0])
        return secrets.compare_digest(supplied, token)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            self._send(200, _index_html(), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/api/"):
            if not self._authed(query):
                self._send(403, {"error": "forbidden"})
                return
            self._handle_api_get(parsed.path, query)
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not parsed.path.startswith("/api/") or not self._authed(query):
            self._send(403, {"error": "forbidden"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "JSON body must be an object"})
            return
        self._handle_api_post(parsed.path, body)

    def _handle_api_get(self, path, query):
        db = None
        try:
            db = open_db()
            if path == "/api/state":
                self._send(200, build_state(db))
            elif path == "/api/thread":
                self._send(200, api_thread(db, query.get("email", [""])[0]))
            elif path == "/api/attachment":
                blob, ctype, name = api_attachment(
                    db, query.get("folder", [""])[0],
                    query.get("uid", [""])[0], query.get("part", [""])[0])
                self._send_file(blob, ctype, name,
                                force_download=query.get("download", [""])[0] in ("1", "true"))
            elif path == "/api/import/scan":
                days = query.get("days", [None])[0]
                days = int(days) if days else None
                self._send(200, api_scan(db, days))
            else:
                self._send(404, {"error": "not found"})
        except (ValueError, RuntimeError) as e:
            self._send(400, {"error": str(e)})
        except LookupError as e:
            self._send(404, {"error": str(e)})
        except Exception as e:  # surface, don't crash the server
            self._send(500, {"error": str(e)})
        finally:
            if db is not None:
                close_db(db)

    def _handle_api_post(self, path, body):
        fn = _MUTATIONS.get(path)
        if fn is None:
            self._send(404, {"error": "not found"})
            return
        db = None
        try:
            db = open_db()
            result = fn(db, body)
            push_db(db)
            self._send(200, result)
        except StaleError as e:
            self._send(409, {"error": str(e)})
        except ConcurrentWriteError:
            self._send(409, {"error": "The data changed elsewhere. Reload and retry."})
        except (ValueError, RuntimeError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            if db is not None:
                close_db(db)


def run(args):
    port = 8765
    open_browser = True
    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                print(f"Invalid --port: {args[i + 1]}")
                return
            i += 2
        elif args[i] == "--no-browser":
            open_browser = False
            i += 1
        else:
            i += 1

    # Never start a Microsoft device-code login from inside a web request: it
    # would block the browser while prompting in this terminal. The API returns
    # a clear "log in via the CLI once" error instead.
    from . import msauth
    msauth.ALLOW_DEVICE_FLOW = False

    token = secrets.token_urlsafe(16)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    except OSError as e:
        print(f"Could not bind 127.0.0.1:{port} — {e}")
        print("Try a different port: crm serve --port 8790")
        return
    httpd.token = token

    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
    print(f"crm serve → {url}")
    print(f"Data backend: {current_backend().describe()}")
    print("Press Ctrl+C to stop.", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()
