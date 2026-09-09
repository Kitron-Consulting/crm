"""Tests for attachment listing/fetching and calendar-invite detection in crm.mail.

Messages are built with email.message.EmailMessage; IMAP is faked. No network."""

from datetime import timedelta, timezone
from email.message import EmailMessage

import pytest

from crm import mail
from crm.mail import (_ics_unfold, extract_event, fetch_attachment, list_attachments)

TZ3 = timezone(timedelta(hours=3))  # the user's configured zone in these tests


def _msg_with_parts(body="Hi", pdf=True, inline_img=True, ics=None):
    m = EmailMessage()
    m["From"] = "matti@konepaja.fi"
    m["To"] = "me@kitron.dev"
    m["Subject"] = "Tarjouspyyntö"
    m.set_content(body)
    # Real MIME order: multipart/related (body + inline logo) first, then
    # attachments wrap it in multipart/mixed. add_related after add_attachment
    # is rejected by the email library.
    if inline_img:
        m.add_related(b"\x89PNG fake", maintype="image", subtype="png", cid="<logo@sig>",
                      disposition="inline", filename="logo.png")
    if pdf:
        m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                         filename="Tarjouspyyntö_QC.pdf")
    if ics is not None:
        m.add_attachment(ics.encode(), maintype="text", subtype="calendar",
                         filename="invite.ics", params={"method": "REQUEST"})
    return m


# --- list_attachments -----------------------------------------------------

def test_lists_real_attachment_but_not_body_or_inline_logo_or_ics():
    m = _msg_with_parts(ics="BEGIN:VCALENDAR\nEND:VCALENDAR")
    atts = list_attachments(m)
    assert [a["name"] for a in atts] == ["Tarjouspyyntö_QC.pdf"]
    a = atts[0]
    assert a["type"] == "application/pdf" and a["size"] == len(b"%PDF-1.4 fake")
    assert a["part"].isdigit()


def test_no_attachments_is_empty_list():
    assert list_attachments(_msg_with_parts(pdf=False, inline_img=False)) == []


# --- fetch_attachment / UID-based folder fetch (fake IMAP) ------------------

class FakeIMAP:
    def __init__(self, raw, uid=b"4711"):
        self.raw, self.uid_bytes = raw, uid
        self.selected = None

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def select(self, folder, readonly=False):
        self.selected = folder
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        if cmd == "search":
            return "OK", [self.uid_bytes]
        if cmd == "fetch":
            if args[0] not in (self.uid_bytes, self.uid_bytes.decode()):
                return "OK", [None]
            return "OK", [(b"1 (UID 4711 RFC822 {%d}" % len(self.raw), self.raw), b")"]
        raise AssertionError(cmd)


def test_fetch_folder_uses_uids_and_carries_attachments_and_folder(monkeypatch):
    m = _msg_with_parts()
    fake = FakeIMAP(m.as_bytes())
    monkeypatch.setattr(mail, "_imap_authenticate", lambda i, cfg: None)
    out = mail._fetch_folder(fake, "Sent Items", "matti@konepaja.fi", "outbound", tz=TZ3)
    assert fake.selected == '"Sent Items"'          # spaced folder quoted
    assert len(out) == 1
    msg = out[0]
    assert msg["uid"] == "4711" and msg["folder"] == "Sent Items"
    assert [a["name"] for a in msg["attachments"]] == ["Tarjouspyyntö_QC.pdf"]
    assert msg["event"] is None


def test_fetch_attachment_returns_bytes_type_name(monkeypatch):
    m = _msg_with_parts()
    part_idx = list_attachments(m)[0]["part"]
    monkeypatch.setattr(mail, "_imap_authenticate", lambda i, cfg: None)
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda h, p, **kw: FakeIMAP(m.as_bytes()))
    data, ctype, name = fetch_attachment({"host": "h"}, "INBOX", "4711", part_idx)
    assert data == b"%PDF-1.4 fake" and ctype == "application/pdf" and name == "Tarjouspyyntö_QC.pdf"


def test_fetch_attachment_missing_part_or_message(monkeypatch):
    m = _msg_with_parts()
    monkeypatch.setattr(mail, "_imap_authenticate", lambda i, cfg: None)
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda h, p, **kw: FakeIMAP(m.as_bytes()))
    with pytest.raises(LookupError):
        fetch_attachment({"host": "h"}, "INBOX", "4711", "99")
    with pytest.raises(LookupError):
        fetch_attachment({"host": "h"}, "INBOX", "1", "1")  # unknown uid


# --- calendar / Teams detection --------------------------------------------

TEAMS_URL = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%7d"

INVITE = f"""BEGIN:VCALENDAR
METHOD:REQUEST
BEGIN:VEVENT
SUMMARY:QC-linjan selvitys – aloituspalaveri
DTSTART:20260915T070000Z
DTEND:20260915T080000Z
LOCATION:Microsoft Teams Meeting
ORGANIZER;CN=Matti Meikäläinen:mailto:matti@konepaja.fi
DESCRIPTION:Tervetuloa.\\nLiity kokoukseen:\\n {TEAMS_URL}
X-MICROSOFT-SKYPETEAMSMEETINGURL:{TEAMS_URL}
END:VEVENT
END:VCALENDAR"""


def test_teams_invite_parsed_and_localised():
    e = extract_event(_msg_with_parts(ics=INVITE, pdf=False, inline_img=False), tz=TZ3)
    assert e["method"] == "REQUEST" and e["status"] == "invitation"
    assert e["summary"] == "QC-linjan selvitys – aloituspalaveri"
    assert e["start"] == "2026-09-15 10:00" and e["end"] == "2026-09-15 11:00"   # 07:00Z -> UTC+3
    assert e["all_day"] is False
    assert e["location"] == "Microsoft Teams Meeting"
    assert e["organizer"] == "Matti Meikäläinen <matti@konepaja.fi>"
    assert e["join_url"] == TEAMS_URL and e["provider"] == "teams"


def test_cancel_and_reply_statuses():
    cancel = INVITE.replace("METHOD:REQUEST", "METHOD:CANCEL")
    assert extract_event(_msg_with_parts(ics=cancel, pdf=False, inline_img=False))["status"] == "cancelled"
    reply = (INVITE.replace("METHOD:REQUEST", "METHOD:REPLY")
             .replace("END:VEVENT", "ATTENDEE;PARTSTAT=ACCEPTED;CN=Me:mailto:me@kitron.dev\nEND:VEVENT"))
    assert extract_event(_msg_with_parts(ics=reply, pdf=False, inline_img=False))["status"] == "accepted"


def test_all_day_and_windows_tzid_shown_as_given():
    ics = ("BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nSUMMARY:Messut\n"
           "DTSTART;VALUE=DATE:20260920\nDTEND;VALUE=DATE:20260921\nEND:VEVENT\nEND:VCALENDAR")
    e = extract_event(_msg_with_parts(ics=ics, pdf=False, inline_img=False), tz=TZ3)
    assert e["all_day"] is True and e["start"] == "2026-09-20" and e["end"] == "2026-09-21"
    assert e["join_url"] == "" and e["provider"] == ""

    win = ("BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nSUMMARY:Palaveri\n"
           "DTSTART;TZID=FLE Standard Time:20260916T140000\nEND:VEVENT\nEND:VCALENDAR")
    e = extract_event(_msg_with_parts(ics=win, pdf=False, inline_img=False), tz=TZ3)
    assert e["start"] == "2026-09-16 14:00"   # unknown Windows TZID: shown as given, not shifted


def test_folded_lines_are_unfolded():
    folded = "SUMMARY:Very long\n  title continues\nX:1"
    assert _ics_unfold(folded)[0] == "SUMMARY:Very long title continues"


def test_quoted_tzid_with_colon_does_not_break_dtstart():
    # Outlook: DTSTART;TZID="(UTC+02:00) Helsinki, Kyiv, Riga, Sofia, Tallinn, Vilnius":20260915T100000
    ics = ('BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nSUMMARY:Palaveri\n'
           'DTSTART;TZID="(UTC+02:00) Helsinki, Kyiv, Riga, Sofia, Tallinn, Vilnius":20260915T100000\n'
           'DTEND;TZID="(UTC+02:00) Helsinki, Kyiv, Riga, Sofia, Tallinn, Vilnius":20260915T110000\n'
           'END:VEVENT\nEND:VCALENDAR')
    e = extract_event(_msg_with_parts(ics=ics, pdf=False, inline_img=False), tz=TZ3)
    assert e["start"] == "2026-09-15 10:00" and e["end"] == "2026-09-15 11:00"
    name, params, value = mail._ics_split(
        'DTSTART;TZID="(UTC+02:00) Helsinki, Kyiv":20260915T100000')
    assert name == "DTSTART" and params == {"TZID": "(UTC+02:00) Helsinki, Kyiv"} and value == "20260915T100000"


def test_valarm_inside_event_does_not_shadow_event_properties():
    ics = ("BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\n"
           "BEGIN:VALARM\nDESCRIPTION:Reminder\nTRIGGER:-PT15M\nACTION:DISPLAY\nEND:VALARM\n"
           "SUMMARY:Real title\nDTSTART:20260915T070000Z\n"
           f"DESCRIPTION:Join: {TEAMS_URL}\nEND:VEVENT\nEND:VCALENDAR")
    e = extract_event(_msg_with_parts(ics=ics, pdf=False, inline_img=False), tz=TZ3)
    assert e["summary"] == "Real title" and e["start"] == "2026-09-15 10:00"
    assert e["join_url"] == TEAMS_URL   # real DESCRIPTION, not the alarm's


def test_part_summary_lists_structure_without_bodies():
    parts = mail.part_summary(_msg_with_parts(ics="BEGIN:VCALENDAR\nEND:VCALENDAR"))
    types = [p["type"] for p in parts]
    assert "text/plain" in types and "application/pdf" in types and "text/calendar" in types
    pdf = next(p for p in parts if p["type"] == "application/pdf")
    assert pdf["name"] == "Tarjouspyyntö_QC.pdf" and pdf["disposition"] == "attachment" and pdf["size"] > 0
    assert all("body" not in p for p in parts)


def test_bare_teams_link_in_body_is_a_meeting_link():
    m = _msg_with_parts(body=f"Hop on quickly? {TEAMS_URL} thanks", pdf=False, inline_img=False)
    e = extract_event(m)
    assert e["status"] == "link" and e["provider"] == "teams" and e["join_url"] == TEAMS_URL


def test_plain_message_has_no_event():
    assert extract_event(_msg_with_parts(body="No meeting here.", pdf=False, inline_img=False)) is None


# --- no-.ics invites (Exchange rendering of SENT Teams invites) --------------

OUTLOOK_HTML = f"""<html><body>
<div>When: Monday, September 15, 2026 10:00 AM-11:00 AM (UTC+02:00) Helsinki, Kyiv, Riga, Sofia, Tallinn, Vilnius.</div>
<div>Where: Microsoft Teams Meeting</div>
<p>Hei, tervetuloa aloituspalaveriin.</p>
<div><a href="{TEAMS_URL}">Join the meeting now</a></div>
<div>Meeting ID: 123 456 789</div>
</body></html>"""


def _html_only(subject, html=OUTLOOK_HTML, sender="me@kitron.dev"):
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "matti@konepaja.fi"
    m["Subject"] = subject
    m.set_content(html, subtype="html")
    return m


def test_sent_teams_invite_without_ics_is_detected_from_html():
    e = extract_event(_html_only("Aloituspalaveri"))
    assert e["status"] == "invitation"
    assert e["join_url"] == TEAMS_URL and e["provider"] == "teams"   # URL lived only in the href
    assert e["start"].startswith("Monday, September 15, 2026 10:00 AM-11:00 AM")
    assert e["location"] == "Microsoft Teams Meeting"
    assert e["summary"] == "Aloituspalaveri"


def test_subject_prefix_gives_status_and_clean_title():
    assert extract_event(_html_only("Canceled: Aloituspalaveri"))["status"] == "cancelled"
    assert extract_event(_html_only("Canceled: Aloituspalaveri"))["summary"] == "Aloituspalaveri"
    assert extract_event(_html_only("Hyväksytty: Aloituspalaveri"))["status"] == "accepted"
    assert extract_event(_html_only("Declined: Aloituspalaveri"))["status"] == "declined"
    assert extract_event(_html_only("Alustava: Aloituspalaveri"))["status"] == "tentative"


def test_finnish_when_where_lines():
    html = (f"<div>Aika: maanantai 15. syyskuuta 2026 10.00-11.00 (UTC+02:00) Helsinki</div>"
            f"<div>Paikka: Neuvotteluhuone 2</div><p>Tervetuloa.</p>")
    e = extract_event(_html_only("Palaveri", html=html))
    assert e["status"] == "invitation" and e["location"] == "Neuvotteluhuone 2"
    assert e["start"].startswith("maanantai 15. syyskuuta 2026")
    assert e["join_url"] == ""


def test_outbound_direction_parses_invites_too(monkeypatch):
    m = _msg_with_parts(ics=INVITE, pdf=False, inline_img=False)
    monkeypatch.setattr(mail, "_imap_authenticate", lambda i, cfg: None)
    out = mail._fetch_folder(FakeIMAP(m.as_bytes()), "Sent Items", "matti@konepaja.fi", "outbound", tz=TZ3)
    assert out[0]["direction"] == "outbound"
    assert out[0]["event"]["status"] == "invitation" and out[0]["event"]["provider"] == "teams"


def test_reply_that_only_quotes_an_invite_is_not_an_event(monkeypatch):
    m = EmailMessage()
    m["From"] = "matti@konepaja.fi"
    m["To"] = "me@kitron.dev"
    m["Subject"] = "Re: Aloituspalaveri"
    m.set_content("Sounds good, see you then!\n\nOn Mon, Sep 8, 2026 Eemil wrote:\n"
                  f"> When: Monday, September 15, 2026 10:00 AM\n> {TEAMS_URL}")
    monkeypatch.setattr(mail, "_imap_authenticate", lambda i, cfg: None)
    out = mail._fetch_folder(FakeIMAP(m.as_bytes()), "INBOX", "matti@konepaja.fi", "inbound")
    assert out[0]["body"] == "Sounds good, see you then!"
    assert out[0]["event"] is None


# --- thread-level enrichment: the real-world shape from Exchange ------------
# Sent Items copy of a Teams invite = text/html with the join link ONLY (no .ics,
# no When:). The attendee's "Accepted:" reply carries the VEVENT with the time.

TEAMS_ONLY_HTML = f'<div>Microsoft Teams meeting</div><div><a href="{TEAMS_URL}">Join the meeting now</a></div><div>Meeting ID: 123</div>'

ACCEPTED_ICS = """BEGIN:VCALENDAR
METHOD:REPLY
BEGIN:VEVENT
SUMMARY:Accepted: Biohiililaitos - Toimeksianto
DTSTART:20260526T080000Z
DTEND:20260526T090000Z
LOCATION:Microsoft Teams Meeting
ATTENDEE;PARTSTAT=ACCEPTED:mailto:juha@sftec.fi
END:VEVENT
END:VCALENDAR"""


def _accepted_reply():
    m = EmailMessage()
    m["From"] = "juha@sftec.fi"
    m["To"] = "me@kitron.dev"
    m["Subject"] = "Accepted: Biohiililaitos - Toimeksianto"
    m.set_content("")
    m.add_attachment(ACCEPTED_ICS.encode(), maintype="text", subtype="calendar",
                     filename="invite.ics", params={"method": "REPLY"})
    return m


def test_norm_subject_collapses_prefixes():
    for s in ["Biohiililaitos - Toimeksianto", "Accepted: Biohiililaitos - Toimeksianto",
              "Re: Biohiililaitos - Toimeksianto", "VS: Re:  Biohiililaitos - Toimeksianto",
              "Hyväksytty: biohiililaitos - toimeksianto"]:
        assert mail._norm_subject(s) == "biohiililaitos - toimeksianto", s


def test_reply_summary_prefix_is_stripped():
    e = extract_event(_accepted_reply(), tz=TZ3)
    assert e["status"] == "accepted" and e["summary"] == "Biohiililaitos - Toimeksianto"
    assert e["start"] == "2026-05-26 11:00" and e["end"] == "2026-05-26 12:00"


class FolderIMAP:
    """UID search/fetch answering with a different message per selected folder."""
    def __init__(self, by_folder):
        self.by_folder, self.folder = by_folder, None

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def select(self, folder, readonly=False):
        self.folder = folder.strip('"')
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        raw = self.by_folder.get(self.folder)
        if raw is None:
            return "OK", [b""]
        if cmd == "search":
            return "OK", [b"7"]
        return "OK", [(b"1 (UID 7 RFC822 {%d}" % len(raw), raw), b")"]


def test_sent_link_only_invite_borrows_time_from_accepted_reply(monkeypatch):
    sent = _html_only("Biohiililaitos - Toimeksianto", html=TEAMS_ONLY_HTML)   # what Exchange keeps in Sent
    fake = FolderIMAP({"INBOX": _accepted_reply().as_bytes(), "Sent Items": sent.as_bytes()})
    monkeypatch.setattr(mail, "_imap_authenticate", lambda i, cfg: None)
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda h, p, **kw: fake)

    msgs = mail.fetch_thread({"host": "h", "sent_folder": "Sent Items"}, "juha@sftec.fi", tz=TZ3)
    inv = next(m for m in msgs if m["direction"] == "outbound")["event"]
    assert inv["status"] == "invitation"                       # was "link"
    assert inv["start"] == "2026-05-26 11:00" and inv["end"] == "2026-05-26 12:00"
    assert inv["location"] == "Microsoft Teams Meeting"
    assert inv["summary"] == "Biohiililaitos - Toimeksianto"
    assert inv["join_url"] == TEAMS_URL and inv["provider"] == "teams"   # own link kept
    assert inv["time_source"] == "thread"


def test_enrichment_leaves_unrelated_link_alone(monkeypatch):
    other = _html_only("Quick sync", html=TEAMS_ONLY_HTML.replace("meeting_abc", "meeting_zzz"))
    fake = FolderIMAP({"INBOX": _accepted_reply().as_bytes(), "Sent Items": other.as_bytes()})
    monkeypatch.setattr(mail, "_imap_authenticate", lambda i, cfg: None)
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda h, p, **kw: fake)
    msgs = mail.fetch_thread({"host": "h", "sent_folder": "Sent Items"}, "juha@sftec.fi", tz=TZ3)
    e = next(m for m in msgs if m["direction"] == "outbound")["event"]
    assert e["status"] == "link" and e["start"] == "" and "time_source" not in e
