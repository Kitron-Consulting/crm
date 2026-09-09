"""SMTP/IMAP/template I/O for the followup and thread commands.

This module isn't in the spec's stated layout, but the alternative is
to push ~150 lines of network I/O into cli.py — which would muddy the
"cli is glue, side effects go through a module" principle. Treating
mail the same way storage is treated (I/O behind an importable API)
keeps cli.py focused on argv dispatch and human-readable output.

Pure-ish: build_message/render_template/contact_context have no side
effects. send_email, save_to_sent, _fetch_folder, fetch_thread perform
network I/O. The cli layer decides when to call them; this module
doesn't print or sys.exit.
"""

import base64
import email as emaillib
import imaplib
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser


class MailAuthError(Exception):
    """Raised when SMTP/IMAP authentication fails (password or OAuth)."""


# How many messages to pull per IMAP FETCH when scanning the Sent folder.
# Batching turns one round-trip-per-message into one per chunk.
_FETCH_CHUNK = 500

# Exchange Online throttles IMAP hard; without a socket timeout a stalled
# connection hangs a request forever instead of failing with a clear error.
IMAP_TIMEOUT = 60


def _imap_mailbox(name):
    """Quote an IMAP mailbox name when it needs it.

    imaplib does NOT quote mailbox arguments, so a name with a space (e.g.
    Exchange's "Sent Items") is sent as two tokens and the server rejects it
    with a BAD command-argument error. Wrap such names in an IMAP quoted
    string; leave simple names (INBOX, Sent) bare.
    """
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        return name  # already quoted
    if any(c in name for c in ' "\\(){%*'):
        return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return name


def contact_context(c):
    """Build substitution context for a contact."""
    name = c.get("name", "")
    first_name = name.split()[0] if name else ""
    return {
        "name": name,
        "first_name": first_name,
        "company": c.get("company", ""),
        "role": c.get("role", ""),
        "email": c.get("email", ""),
        "phone": c.get("phone", ""),
    }


def render_template(text, ctx):
    """Replace {field} placeholders. Missing fields become empty strings."""
    def replace(match):
        return ctx.get(match.group(1), "")
    return re.sub(r'\{(\w+)\}', replace, text)


def build_message(smtp_cfg, to_addr, subject, body):
    """Build an EmailMessage."""
    msg = EmailMessage()
    from_addr = smtp_cfg["user"]
    from_name = smtp_cfg.get("from_name", "")
    msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1] if "@" in from_addr else "localhost")
    msg.set_content(body)
    return msg


def _smtp_authenticate(s, cfg):
    """Authenticate an open SMTP session per cfg["auth"].

    password (or absent) → basic login (unchanged path).
    oauth-ms            → XOAUTH2 with a Microsoft 365 bearer token. SMTP
                          requires the SASL string base64-encoded manually.
    """
    if cfg.get("auth") == "oauth-ms":
        from . import msauth
        token = msauth.get_token(cfg)
        auth_string = msauth.build_xoauth2_string(cfg["user"], token)
        encoded = base64.b64encode(auth_string.encode()).decode()
        # starttls() reset smtplib's EHLO state; the raw AUTH docmd() below
        # would otherwise be rejected with 503 "Send hello first". login()
        # re-EHLOs for us on the password path, but we bypass it here.
        s.ehlo()
        code, resp = s.docmd("AUTH", "XOAUTH2 " + encoded)
        if code != 235:
            detail = resp.decode(errors="replace") if isinstance(resp, bytes) else str(resp)
            hint = ""
            if code == 535:  # 5.7.3 Authentication unsuccessful
                hint = (
                    "\nCheck that the app's delegated SMTP.Send permission was "
                    "admin-consented and that SMTP AUTH is enabled for the mailbox."
                )
            raise MailAuthError(f"SMTP OAuth login failed: {code} {detail}{hint}")
    else:
        s.login(cfg["user"], cfg["password"])


def _imap_authenticate(m, cfg):
    """Authenticate an open IMAP session per cfg["auth"].

    oauth-ms uses imaplib's authenticate(), which base64-encodes the
    authobject's return value internally — so it returns the RAW SASL bytes,
    unlike the SMTP path which pre-encodes.
    """
    if cfg.get("auth") == "oauth-ms":
        from . import msauth
        token = msauth.get_token(cfg)
        auth_string = msauth.build_xoauth2_string(cfg["user"], token)
        try:
            m.authenticate("XOAUTH2", lambda _: auth_string.encode())
        except imaplib.IMAP4.error as e:
            raise MailAuthError(
                "IMAP OAuth authentication failed. Check that the app's "
                "delegated IMAP.AccessAsUser.All permission was admin-consented "
                f"and that IMAP is enabled for the mailbox. ({e})"
            )
    else:
        m.login(cfg["user"], cfg["password"])


def send_email(smtp_cfg, msg):
    """Send an EmailMessage via SMTP. Raises exception on failure."""
    host = smtp_cfg["host"]
    port = smtp_cfg.get("port", 587)
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        _smtp_authenticate(s, smtp_cfg)
        s.send_message(msg)


def save_to_sent(imap_cfg, msg):
    """Append a sent message to the IMAP Sent folder.

    Note: Exchange Online (auth: "oauth-ms") auto-saves SMTP-submitted mail to
    Sent Items, so the caller skips this for that path to avoid a duplicate.
    """
    host = imap_cfg["host"]
    port = imap_cfg.get("port", 993)
    folder = imap_cfg.get("sent_folder", "Sent")

    with imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT) as m:
        _imap_authenticate(m, imap_cfg)
        # \Seen flag so it doesn't show as unread
        m.append(_imap_mailbox(folder), "\\Seen", imaplib.Time2Internaldate(datetime.now().timestamp()), msg.as_bytes())


def decode_mime_header(raw):
    """Decode MIME-encoded header (RFC 2047) to a plain string."""
    if not raw:
        return ""
    parts = []
    for chunk, enc in decode_header(raw):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(enc or "utf-8", errors="replace"))
            except Exception:
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


# --- HTML mail -> readable text -----------------------------------------
# Most business mail is HTML/rich text. A tag-strip regex leaves <style> CSS as
# text, never decodes entities, and flattens paragraphs — unreadable. This is a
# small structure-aware converter (stdlib only): paragraph/line breaks for
# block elements, bullets for lists, "> " prefixes for blockquotes, entities
# decoded, style/script dropped. It's not a renderer, just enough to read.

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table",
               "section", "article", "header", "footer", "address"}
_LINE_TAGS = {"div", "tr", "br", "dd", "dt", "option", "caption"}
_SKIP_TAGS = {"style", "script", "head", "title", "template", "noscript"}


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)   # &nbsp; &amp; &#39; -> characters
        self.out, self.skip, self.bq, self.pre = [], 0, 0, 0

    def _tail(self):
        return "".join(self.out[-2:])

    def _nl(self, n):
        """Ensure the output ends with at least n newlines."""
        tail = self._tail()
        have = len(tail) - len(tail.rstrip("\n"))
        if have < n:
            self.out.append("\n" * (n - have))

    def _prefix(self):
        """At a line start inside a blockquote, emit the '> ' marker(s)."""
        if self.bq and (not self.out or self._tail().endswith("\n")):
            self.out.append("> " * self.bq)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "blockquote":
            self._nl(2); self.bq += 1
        elif tag == "pre":
            self._nl(2); self.pre += 1
        elif tag in _BLOCK_TAGS:
            self._nl(2)
        elif tag == "li":
            self._nl(1); self._prefix(); self.out.append("• ")
        elif tag == "hr":
            self._nl(1); self._prefix(); self.out.append("————"); self._nl(1)
        elif tag in _LINE_TAGS:
            self._nl(1)
        elif tag in ("td", "th") and self.out and not self._tail().endswith(("\n", "\t")):
            self.out.append("\t")

    def handle_startendtag(self, tag, attrs):  # <br/>, <hr/>
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "blockquote":
            self.bq = max(0, self.bq - 1); self._nl(2)
        elif tag == "pre":
            self.pre = max(0, self.pre - 1); self._nl(2)
        elif tag in _BLOCK_TAGS:
            self._nl(2)
        elif tag in ("li", "tr", "div"):
            self._nl(1)

    def handle_data(self, data):
        if self.skip:
            return
        data = data.replace("\xa0", " ")
        if not self.pre:
            data = re.sub(r"[ \t\r\n]+", " ", data)   # HTML whitespace collapsing
            if not data.strip():
                # inter-tag whitespace: keep a single space only mid-line
                if self.out and not self._tail().endswith(("\n", " ", "\t")):
                    self.out.append(" ")
                return
            if not self.out or self._tail().endswith("\n"):
                data = data.lstrip()
        self._prefix()
        self.out.append(data)

    def text(self):
        text = "\n".join(line.rstrip() for line in "".join(self.out).split("\n"))
        return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def html_to_text(html):
    """Readable plain text for an HTML email body."""
    parser = _HTMLText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)  # last resort, never fail the thread
    return parser.text()


def _decode_part(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


# text/plain parts that are really just "go read the HTML" stubs
_PLACEHOLDER_PLAIN = re.compile(r"html|rich ?text|not supported|view (this|it) (in|with) (a |your )?browser", re.I)


def extract_body(msg):
    """Readable text body of a message.

    Prefers the text/plain part. Falls back to converting the HTML part when
    plain is missing or is a placeholder stub. A single-part text/html message
    is converted too (it used to be returned as raw markup).
    """
    plain = html = None
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        try:
            if ctype == "text/plain" and plain is None:
                plain = _decode_part(part)
            elif ctype == "text/html" and html is None:
                html = _decode_part(part)
        except Exception:
            continue

    if plain is not None and plain.strip():
        stripped = plain.strip()
        placeholder = len(stripped) < 80 and _PLACEHOLDER_PLAIN.search(stripped)
        if not (placeholder and html):
            return plain
    if html:
        return html_to_text(html)
    if plain is not None:
        return plain
    # Non-text single part: best effort, never raise.
    try:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        pass
    payload = msg.get_payload()
    return payload if isinstance(payload, str) else ""


# --- quoted-history splitting ---------------------------------------------
# Every reply drags the whole earlier conversation along ("On … wrote:", an
# Outlook From:/Sent:/To: block, "-----Original Message-----", or '>' lines).
# In a thread view each message already appears on its own, so the quoted
# tail is pure noise — split it off so the UI can hide it behind a toggle.

_QUOTE_INTRO = re.compile(r"\b(wrote|kirjoitti|schrieb|a écrit|escribió|skrev)\s*:\s*$", re.I)
_ORIG_MSG = re.compile(r"^-{2,}\s*(Original Message|Alkuperäinen viesti|Ursprüngliche Nachricht|"
                       r"Forwarded message|Välitetty viesti)\s*-{2,}$", re.I)
_HDR_FROM = re.compile(r"^(From|Lähettäjä|Von|De)\s*:", re.I)
_HDR_NEXT = re.compile(r"^(Sent|Date|To|Cc|Subject|Lähetetty|Vastaanottaja|Aihe|Gesendet|An|Betreff)\s*:", re.I)


def split_quoted(text):
    """Split message text into (own text, quoted history).

    Returns (text, "") when no quote marker is found — or when cutting would
    leave no own text at all, so a pure forward is never hidden.
    """
    lines = text.split("\n")
    cut = None
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if _ORIG_MSG.match(s):
            cut = i
            break
        # "On <date>, X wrote:" / "… klo 11.00 X kirjoitti:" — may wrap to a 2nd line
        joined = s
        if i + 1 < len(lines) and not _QUOTE_INTRO.search(joined):
            joined = s + " " + lines[i + 1].strip()
        if len(s) < 200 and _QUOTE_INTRO.search(joined) and (
                re.match(r"^(On|Am|Le|El|Den)\b", s) or re.search(r"\bklo\b|\d{4}", s)):
            cut = i
            break
        if _HDR_FROM.match(s) and any(_HDR_NEXT.match(l.strip()) for l in lines[i + 1:i + 5]):
            # Outlook block; swallow the "________" rule Outlook puts above it
            cut = i - 1 if i > 0 and re.match(r"^_{5,}$", lines[i - 1].strip()) else i
            break
        if s.startswith(">"):
            rest = [l for l in lines[i:] if l.strip()]
            if rest and sum(l.lstrip().startswith(">") for l in rest) / len(rest) >= 0.6:
                cut = i
                break
    if cut is None:
        return text, ""
    main = "\n".join(lines[:cut]).rstrip()
    if not main.strip():
        return text, ""
    return main, "\n".join(lines[cut:]).strip("\n")


# --- attachments -------------------------------------------------------------

def _human_filename(part):
    name = part.get_filename()
    return decode_mime_header(name) if name else ""


def list_attachments(msg):
    """Real attachments of a message: [{part, name, type, size}].

    `part` is the index in msg.walk(); fetch_attachment re-derives it from the
    same message bytes. Skips the text body alternatives, calendar parts (they
    surface as `event`), and inline images referenced by Content-ID (signature
    logos).
    """
    out = []
    for i, part in enumerate(msg.walk()):
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = part.get_content_disposition()
        name = _human_filename(part)
        if ctype in ("text/plain", "text/html") and disp != "attachment":
            continue
        if ctype == "text/calendar" or name.lower().endswith(".ics"):
            continue
        if disp == "inline" and part.get("Content-ID") and ctype.startswith("image/"):
            continue
        if not name and disp != "attachment":
            continue
        payload = part.get_payload(decode=True) or b""
        out.append({"part": str(i), "name": name or f"attachment-{i}", "type": ctype, "size": len(payload)})
    return out


def part_summary(msg):
    """MIME structure without bodies — [{type, name, size, disposition}].

    Diagnostic aid (`crm thread --mime`, and in --json) for seeing exactly what
    the server delivered when an invite or attachment doesn't parse as expected.
    """
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        try:
            size = len(part.get_payload(decode=True) or b"")
        except Exception:
            size = 0
        out.append({"type": part.get_content_type(), "name": _human_filename(part),
                    "size": size, "disposition": part.get_content_disposition() or ""})
    return out


def fetch_attachment(imap_cfg, folder, uid, part_index):
    """Fetch one attachment part by folder/UID/walk-index.

    Returns (bytes, content_type, filename). Raises LookupError when the
    message or part can't be found.
    """
    with imaplib.IMAP4_SSL(imap_cfg["host"], imap_cfg.get("port", 993), timeout=IMAP_TIMEOUT) as m:
        _imap_authenticate(m, imap_cfg)
        typ, _ = m.select(_imap_mailbox(folder), readonly=True)
        if typ != "OK":
            raise LookupError(f"Could not open folder {folder!r}")
        typ, fetched = m.uid("fetch", str(uid), "(RFC822)")
        if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
            raise LookupError("Message not found")
        msg = emaillib.message_from_bytes(fetched[0][1])
    for i, part in enumerate(msg.walk()):
        if str(i) == str(part_index) and not part.is_multipart():
            return (part.get_payload(decode=True) or b"", part.get_content_type(),
                    _human_filename(part) or f"attachment-{i}")
    raise LookupError("Attachment not found")


# --- calendar invites / meeting links -----------------------------------------
# Invites arrive as a text/calendar (or .ics) part; Teams/Meet/Zoom put a join
# URL in the description, location, or (Outlook/Teams) X-MICROSOFT-SKYPETEAMSMEETINGURL.

_JOIN_URL_RE = re.compile(
    r"https?://(?:teams\.microsoft\.com/l/meetup-join/|teams\.live\.com/meet/|meet\.google\.com/"
    r"|[\w.-]*zoom\.us/j/|[\w.-]*webex\.com/)[^\s\"'<>)\]]+", re.I)


def _provider(url):
    u = url.lower()
    if "teams.microsoft.com" in u or "teams.live.com" in u:
        return "teams"
    if "meet.google.com" in u:
        return "meet"
    if "zoom.us" in u:
        return "zoom"
    if "webex.com" in u:
        return "webex"
    return "other"


def _ics_unfold(text):
    """Join iCalendar continuation lines (a line starting with space/tab)."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_unescape(value):
    return (value.replace("\\n", "\n").replace("\\N", "\n")
            .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _ics_split(line):
    """Split 'NAME;P=V;Q="a:b":value' into (NAME, {P: V, Q: 'a:b'}, value).

    The name/params end at the first ':' OUTSIDE double quotes — Outlook
    emits TZIDs like "(UTC+02:00) Helsinki, Kyiv, …" that contain colons, and
    a naive first-colon split turns DTSTART into garbage. Returns None for a
    line with no property separator.
    """
    inq, cut = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            inq = not inq
        elif ch == ":" and not inq:
            cut = i
            break
    if cut < 0:
        return None
    head, value = line[:cut], line[cut + 1:]
    fields, buf, inq = [], "", False
    for ch in head:                      # params split on ';' outside quotes
        if ch == '"':
            inq = not inq
        if ch == ";" and not inq:
            fields.append(buf)
            buf = ""
        else:
            buf += ch
    fields.append(buf)
    params = {}
    for p in fields[1:]:
        k, _, v = p.partition("=")
        params[k.strip().upper()] = v.strip().strip('"')
    return fields[0].strip().upper(), params, value


def _ics_props(lines):
    """Properties of the first VEVENT (+ calendar-level METHOD) and its ATTENDEEs.

    Nested components inside the event (VALARM: its own DESCRIPTION/TRIGGER)
    are skipped so they can't shadow the event's real properties.
    Returns ({NAME: (params, value)}, [(params, value), ...]).
    """
    props, attendees, method = {}, [], None
    in_event, skipping = False, None
    for line in lines:
        split = _ics_split(line)
        if not split:
            continue
        name, params, value = split
        v = value.strip().upper()
        if skipping:
            if name == "END" and v == skipping:
                skipping = None
            continue
        if name == "BEGIN":
            if v == "VEVENT" and not in_event:
                in_event = True
            elif in_event:
                skipping = v             # VALARM etc. — ignore until its END
            continue
        if name == "END" and v == "VEVENT":
            break                        # first VEVENT done
        if not in_event:
            if name == "METHOD":
                method = value.strip().upper()
            continue
        if name == "ATTENDEE":
            attendees.append((params, value))
        elif name not in props:
            props[name] = (params, value)
    if method:
        props.setdefault("METHOD", ({}, method))
    return props, attendees


def _ics_datetime(value, params, tz):
    """iCalendar DATE/DATE-TIME -> (display string, all_day).

    UTC ('Z') and IANA TZIDs are converted to `tz`; Windows TZIDs (e.g. "FLE
    Standard Time") and floating times are shown as given.
    """
    v = value.strip()
    if params.get("VALUE") == "DATE" or (len(v) == 8 and v.isdigit()):
        try:
            return datetime.strptime(v, "%Y%m%d").strftime("%Y-%m-%d"), True
        except ValueError:
            return v, True
    utc = v.endswith("Z")
    core = v[:-1] if utc else v
    dt = None
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            dt = datetime.strptime(core, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        return v, False
    if utc:
        dt = dt.replace(tzinfo=timezone.utc)
    elif params.get("TZID"):
        try:
            import zoneinfo
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo(params["TZID"]))
        except Exception:
            return dt.strftime("%Y-%m-%d %H:%M"), False
    else:
        return dt.strftime("%Y-%m-%d %H:%M"), False
    if tz is not None:
        dt = dt.astimezone(tz)
    return dt.strftime("%Y-%m-%d %H:%M"), False


# Outlook prefixes meeting-response subjects; Exchange renders sent/received
# meeting requests that lack an .ics with "When:"/"Where:" lines in the body.
_SUBJECT_STATUS_RE = re.compile(
    r"^\s*(?:(?P<cancel>Canceled|Cancelled|Peruttu|Abgesagt)|(?P<acc>Accepted|Hyväksytty|Angenommen)"
    r"|(?P<dec>Declined|Hylätty|Abgelehnt)|(?P<tent>Tentative|Alustava(?:sti hyväksytty)?|Mit Vorbehalt))"
    r"\s*:\s*(?P<rest>.*)$", re.I)
_WHEN_RE = re.compile(r"^\s*(?:When|Aika|Milloin|Wann)\s*:\s*(.+?)\s*$", re.I | re.M)
_WHERE_RE = re.compile(r"^\s*(?:Where|Paikka|Missä|Wo)\s*:\s*(.+?)\s*$", re.I | re.M)
_MEETING_HINT_RE = re.compile(r"join the meeting|liity kokoukseen|microsoft teams|meeting id|kokouksen tunnus", re.I)


def _subject_status(subject):
    """('cancelled'|'accepted'|'declined'|'tentative'|'', subject without the prefix)."""
    subject = (subject or "").strip()
    m = _SUBJECT_STATUS_RE.match(subject)
    if not m:
        return "", subject
    for group, status in (("cancel", "cancelled"), ("acc", "accepted"),
                          ("dec", "declined"), ("tent", "tentative")):
        if m.group(group):
            return status, m.group("rest").strip()
    return "", subject


def extract_event(msg, tz=None, body_text=None):
    """Calendar/meeting info for a message, or None. Direction-agnostic: sent
    invites in Sent Items are parsed the same as received ones.

    Primary source is the text/calendar (or .ics) part. Without one — common
    for Exchange's IMAP rendering of *sent* Teams invites — fall back to the
    "When:"/"Where:" lines Outlook writes into the body, the subject prefix
    (Canceled:/Accepted:/…), and a join URL that may only exist in an <a href>
    of the HTML part. `body_text` should be the message's OWN text (quoted
    history excluded) so a reply merely quoting an invite isn't tagged.
    """
    ics = raw_html = None
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        name = _human_filename(part).lower()
        try:
            if ics is None and (ctype == "text/calendar" or name.endswith(".ics")):
                ics = _decode_part(part)
            elif raw_html is None and ctype == "text/html":
                raw_html = _decode_part(part)
        except Exception:
            continue
    if body_text is None:
        body_text = split_quoted(extract_body(msg))[0]
    subj_status, subj_title = _subject_status(decode_mime_header(msg.get("Subject", "")))

    def find_join(*texts):
        for t in texts:
            m = _JOIN_URL_RE.search(t or "")
            if m:
                return m.group(0)
        return ""

    if ics:
        props, attendees = _ics_props(_ics_unfold(ics))

        def get(key):
            return _ics_unescape(props[key][1]).strip() if key in props else ""

        method = get("METHOD").upper()
        start, all_day = _ics_datetime(props["DTSTART"][1], props["DTSTART"][0], tz) if "DTSTART" in props else ("", False)
        end, _ = _ics_datetime(props["DTEND"][1], props["DTEND"][0], tz) if "DTEND" in props else ("", False)
        organizer = re.sub(r"^mailto:", "", get("ORGANIZER"), flags=re.I)
        cn = props.get("ORGANIZER", ({}, ""))[0].get("CN", "")
        if cn and organizer and cn.lower() != organizer.lower():
            organizer = f"{cn} <{organizer}>"
        join = find_join(get("X-MICROSOFT-SKYPETEAMSMEETINGURL"), get("URL"), get("LOCATION"),
                         get("DESCRIPTION"), body_text, raw_html)
        status = {"REQUEST": "invitation", "CANCEL": "cancelled", "PUBLISH": "event"}.get(method, "")
        if method == "REPLY":
            partstat = next((p.get("PARTSTAT", "").lower() for p, _ in attendees if p.get("PARTSTAT")), "")
            status = partstat if partstat in ("accepted", "declined", "tentative") else "reply"
        if not status:
            status = subj_status or "event"
        # Outlook puts the prefixed subject ("Accepted: X") into SUMMARY on replies.
        return {"method": method, "status": status, "summary": _subject_status(get("SUMMARY"))[1] or subj_title,
                "start": start, "end": end, "all_day": all_day, "location": get("LOCATION"),
                "organizer": organizer, "join_url": join, "provider": _provider(join) if join else ""}

    # No .ics: Outlook/Exchange body rendering of a meeting request.
    when = _WHEN_RE.search(body_text)
    where = _WHERE_RE.search(body_text)
    join = find_join(body_text)
    if not join and (when or _MEETING_HINT_RE.search(body_text)):
        join = find_join(raw_html)   # link text is "Join the meeting now"; URL only in href
    if not join and not when:
        return None
    status = subj_status or ("invitation" if when else "link")
    return {"method": "", "status": status,
            "summary": subj_title if (when or subj_status) else "",
            "start": when.group(1) if when else "", "end": "", "all_day": False,
            "location": where.group(1) if where else "", "organizer": "",
            "join_url": join, "provider": _provider(join) if join else ""}


_REPLY_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd|vs|vl|aw|wg)\s*:\s*)+", re.I)   # incl. Finnish VS:/VL:


def _norm_subject(subject):
    """Subject reduced to the meeting it's about: strips Accepted:/Canceled:…,
    Re:/FW:/VS:/VL: chains, and whitespace/case."""
    title = _subject_status(subject)[1]
    title = _REPLY_PREFIX_RE.sub("", title)
    return re.sub(r"\s+", " ", title).strip().lower()


def _enrich_events(messages):
    """Fill missing meeting times from sibling messages about the same meeting.

    Exchange's Sent Items copy of a Teams invite carries no .ics — only the
    join link — so it has no time. But attendee replies (Accepted:/Declined:)
    do carry the VEVENT. Match on identical join URL or normalised subject and
    borrow start/end/location (and the title if missing). A link-only message
    with such a sibling is, in practice, the invitation itself.
    """
    with_time = [m for m in messages if m.get("event") and m["event"].get("start")]
    if not with_time:
        return
    for m in messages:
        e = m.get("event")
        if not e or e.get("start"):
            continue
        key = _norm_subject(m.get("subject", ""))
        src = next((s for s in with_time
                    if (e.get("join_url") and s["event"].get("join_url") == e["join_url"])
                    or (key and _norm_subject(s.get("subject", "")) == key)), None)
        if src is None:
            continue
        se = src["event"]
        e["start"], e["end"], e["all_day"] = se["start"], se["end"], se["all_day"]
        if not e.get("location"):
            e["location"] = se.get("location", "")
        if not e.get("summary"):
            e["summary"] = se.get("summary", "")
        if e.get("status") == "link":
            e["status"] = "invitation"
        e["time_source"] = "thread"


def _fetch_folder(imap, folder, contact_email, direction, tz=None):
    """Fetch messages from a folder involving contact_email. Returns list of dicts.

    Uses IMAP UIDs (stable across sessions) so attachments can be re-fetched later.
    """
    results = []
    try:
        typ, _ = imap.select(_imap_mailbox(folder), readonly=True)
        if typ != "OK":
            return results
        # Search both From and To for the contact
        if direction == "inbound":
            typ, data = imap.uid("search", None, "FROM", f'"{contact_email}"')
        else:  # outbound
            typ, data = imap.uid("search", None, "TO", f'"{contact_email}"')
        if typ != "OK" or not data or not data[0]:
            return results
        uids = data[0].split()
        # Limit to most recent 50
        for uid in uids[-50:]:
            typ, fetched = imap.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            raw = fetched[0][1]
            msg = emaillib.message_from_bytes(raw)
            date_str = msg.get("Date", "")
            try:
                dt = parsedate_to_datetime(date_str)
            except Exception:
                dt = None
            from_name, from_addr = parseaddr(msg.get("From", ""))
            to_name, to_addr = parseaddr(msg.get("To", ""))
            full_body = extract_body(msg)
            body, quoted = split_quoted(full_body)
            results.append({
                "dt": dt,
                "date": dt.strftime("%Y-%m-%d %H:%M") if dt else date_str,
                "direction": direction,
                "from": from_addr,
                "to": to_addr,
                "subject": decode_mime_header(msg.get("Subject", "")),
                "body": body,
                "quoted": quoted,
                "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                "folder": folder,
                "attachments": list_attachments(msg),
                # own text only: a reply quoting an invite must not become an event
                "event": extract_event(msg, tz, body_text=body),
                "parts": part_summary(msg),
            })
    except Exception:
        pass
    return results


def upcoming_meetings(pairs, now_str):
    """Pure: fold (counterparty_email, event) pairs into de-duped upcoming
    meetings. Keeps events whose `start` is >= now_str (string compare on
    "YYYY-MM-DD HH:MM"), drops cancellations, dedupes on join URL / normalised
    subject + start. Returns meeting dicts (event fields + `email`), soonest first."""
    seen = {}
    for email, ev in pairs:
        if not ev:
            continue
        start = (ev.get("start") or "").strip()
        if not start or start < now_str or ev.get("status") == "cancelled":
            continue
        key = (ev.get("join_url") or _norm_subject(ev.get("summary", "")) or start) + "|" + start
        if key not in seen:
            seen[key] = {**ev, "email": (email or "").strip()}
    return sorted(seen.values(), key=lambda m: m["start"])


# A BODYSTRUCTURE (lowercased) names a calendar part like `"text" "calendar"`
# or carries an `.ics` attachment filename — a cheap, reliable "this is an
# invite" signal without downloading the message body.
_CAL_HINT_RE = re.compile(rb'"calendar"|\.ics')


def _calendar_candidates(imap, nums):
    """From a set of message numbers, return only those whose BODYSTRUCTURE has a
    calendar part — one cheap batched metadata fetch instead of N body downloads."""
    typ, meta = imap.fetch(b",".join(nums), "(BODYSTRUCTURE)")
    if typ != "OK" or not meta:
        return list(nums)  # can't pre-filter → fall back to scanning all
    out = []
    for item in meta:
        raw = item[0] if isinstance(item, tuple) else item
        if not raw:
            continue
        m = re.match(rb"\s*(\d+)\s+\(", raw)
        if m and _CAL_HINT_RE.search(raw.lower()):
            out.append(m.group(1))
    return out


def _scan_folder_events(imap, folder, direction, tz, since, limit):
    """Yield (counterparty_email, event) for recent messages in `folder` that
    carry a calendar event. `direction` picks the counterparty side: inbound
    reads From, outbound reads To. Only invite-looking messages (calendar part)
    are body-fetched, so a large mailbox stays fast."""
    out = []
    try:
        typ, _ = imap.select(_imap_mailbox(folder), readonly=True)
        if typ != "OK":
            return out
        typ, data = imap.search(None, "SINCE", since) if since else imap.search(None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return out
        nums = data[0].split()[-limit:]
        if not nums:
            return out
        for num in _calendar_candidates(imap, nums):
            typ, fetched = imap.fetch(num, "(RFC822)")
            if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            msg = emaillib.message_from_bytes(fetched[0][1])
            ev = extract_event(msg, tz)
            if not ev or not ev.get("start"):
                continue
            _, addr = parseaddr(msg.get("From", "") if direction == "inbound" else msg.get("To", ""))
            out.append((addr, {**ev, "folder": folder,
                               "uid": num.decode() if isinstance(num, bytes) else str(num)}))
    except Exception:
        pass
    return out


def fetch_upcoming_meetings(imap_cfg, tz=None, days_back=45, now_str=None):
    """Scan Inbox + Sent for calendar events and return de-duped upcoming
    meetings, each carrying the counterparty `email`. Live-mailbox dependent;
    the pure folding is `upcoming_meetings`, unit-tested separately."""
    host = imap_cfg["host"]
    port = imap_cfg.get("port", 993)
    inbox = imap_cfg.get("inbox_folder", "INBOX")
    sent = imap_cfg.get("sent_folder", "Sent")
    since = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
    if now_str is None:
        now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    pairs = []
    with imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT) as m:
        _imap_authenticate(m, imap_cfg)
        pairs += _scan_folder_events(m, inbox, "inbound", tz, since, 400)
        pairs += _scan_folder_events(m, sent, "outbound", tz, since, 400)
    return upcoming_meetings(pairs, now_str)


def fetch_thread(imap_cfg, contact_email, tz=None):
    """Fetch recent messages between the user and a contact. Returns sorted list.

    `tz` (a tzinfo) localises calendar-event times; None leaves UTC as UTC.
    """
    host = imap_cfg["host"]
    port = imap_cfg.get("port", 993)
    inbox = imap_cfg.get("inbox_folder", "INBOX")
    sent = imap_cfg.get("sent_folder", "Sent")

    messages = []
    with imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT) as m:
        _imap_authenticate(m, imap_cfg)
        messages.extend(_fetch_folder(m, inbox, contact_email, "inbound", tz))
        messages.extend(_fetch_folder(m, sent, contact_email, "outbound", tz))

    _enrich_events(messages)

    # Sort by date (newest first)
    messages.sort(key=lambda x: x["dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return messages


def fetch_sent_recipients(imap_cfg, since_days=None, limit=2000):
    """Scan the Sent folder and return the people you've emailed.

    Reads only the To/Cc headers (not bodies) for speed. Returns a list of
    {"email", "name"} dicts, deduped by lowercased address and ordered
    most-recently-emailed first. `name` is the display name from the header
    when present. Used by `crm import` to seed contacts from outreach.
    """
    host = imap_cfg["host"]
    port = imap_cfg.get("port", 993)
    folder = imap_cfg.get("sent_folder", "Sent")

    # address -> {"email", "name"}, and address -> last-seen order index
    found = {}
    order = {}
    with imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT) as m:
        _imap_authenticate(m, imap_cfg)
        typ, _ = m.select(_imap_mailbox(folder), readonly=True)
        if typ != "OK":
            raise MailAuthError(f"Could not open sent folder {folder!r}")
        if since_days:
            since = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
            typ, data = m.search(None, "SINCE", since)
        else:
            typ, data = m.search(None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()[-limit:]
        # Fetch headers in batches — one FETCH per chunk instead of one per
        # message — so we pay a handful of round-trips, not one per mail.
        idx = 0
        for start in range(0, len(ids), _FETCH_CHUNK):
            chunk = ids[start:start + _FETCH_CHUNK]
            msg_set = b",".join(chunk).decode("ascii")
            typ, fetched = m.fetch(msg_set, "(BODY.PEEK[HEADER.FIELDS (TO CC)])")
            if typ != "OK" or not fetched:
                continue
            for item in fetched:
                # Each fetched message is a (envelope, header-bytes) tuple;
                # the closing ")" tokens between them are bare bytes — skip them.
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                hdr = emaillib.message_from_bytes(item[1])
                for display, addr in getaddresses(hdr.get_all("To", []) + hdr.get_all("Cc", [])):
                    if not addr or "@" not in addr:
                        continue
                    key = addr.lower()
                    name = decode_mime_header(display).strip()
                    # Later messages (higher idx) win: keep the freshest display
                    # name and remember recency for ordering.
                    if key not in found or name:
                        found[key] = {"email": addr, "name": name or found.get(key, {}).get("name", "")}
                    order[key] = idx
                idx += 1

    return [found[k] for k in sorted(found, key=lambda k: order[k], reverse=True)]
