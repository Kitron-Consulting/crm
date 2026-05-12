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

import email as emaillib
import imaplib
import re
import smtplib
from datetime import datetime, timezone
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime


def get_templates(data):
    return data.get("config", {}).get("templates", {})


def get_smtp_config(data):
    return data.get("config", {}).get("smtp")


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


def send_email(smtp_cfg, msg):
    """Send an EmailMessage via SMTP. Raises exception on failure."""
    host = smtp_cfg["host"]
    port = smtp_cfg.get("port", 587)
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(smtp_cfg["user"], smtp_cfg["password"])
        s.send_message(msg)


def save_to_sent(imap_cfg, msg):
    """Append a sent message to the IMAP Sent folder."""
    host = imap_cfg["host"]
    port = imap_cfg.get("port", 993)
    user = imap_cfg["user"]
    password = imap_cfg["password"]
    folder = imap_cfg.get("sent_folder", "Sent")

    with imaplib.IMAP4_SSL(host, port) as m:
        m.login(user, password)
        # \Seen flag so it doesn't show as unread
        m.append(folder, "\\Seen", imaplib.Time2Internaldate(datetime.now().timestamp()), msg.as_bytes())


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


def extract_body(msg):
    """Extract plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition", "").startswith("attachment"):
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
        # Fall back to HTML if no plain text
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                    # Crude HTML strip
                    return re.sub(r'<[^>]+>', '', html)
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return msg.get_payload() or ""


def _fetch_folder(imap, folder, contact_email, direction):
    """Fetch messages from a folder involving contact_email. Returns list of dicts."""
    results = []
    try:
        typ, _ = imap.select(folder, readonly=True)
        if typ != "OK":
            return results
        # Search both From and To for the contact
        if direction == "inbound":
            typ, data = imap.search(None, 'FROM', f'"{contact_email}"')
        else:  # outbound
            typ, data = imap.search(None, 'TO', f'"{contact_email}"')
        if typ != "OK" or not data or not data[0]:
            return results
        ids = data[0].split()
        # Limit to most recent 50
        for msg_id in ids[-50:]:
            typ, fetched = imap.fetch(msg_id, "(RFC822)")
            if typ != "OK":
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
            results.append({
                "dt": dt,
                "date": dt.strftime("%Y-%m-%d %H:%M") if dt else date_str,
                "direction": direction,
                "from": from_addr,
                "to": to_addr,
                "subject": decode_mime_header(msg.get("Subject", "")),
                "body": extract_body(msg),
            })
    except Exception:
        pass
    return results


def fetch_thread(imap_cfg, contact_email):
    """Fetch recent messages between the user and a contact. Returns sorted list."""
    host = imap_cfg["host"]
    port = imap_cfg.get("port", 993)
    user = imap_cfg["user"]
    password = imap_cfg["password"]
    inbox = imap_cfg.get("inbox_folder", "INBOX")
    sent = imap_cfg.get("sent_folder", "Sent")

    messages = []
    with imaplib.IMAP4_SSL(host, port) as m:
        m.login(user, password)
        messages.extend(_fetch_folder(m, inbox, contact_email, "inbound"))
        messages.extend(_fetch_folder(m, sent, contact_email, "outbound"))

    # Sort by date (newest first)
    messages.sort(key=lambda x: x["dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return messages
