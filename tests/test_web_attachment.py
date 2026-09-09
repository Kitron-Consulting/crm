"""Tests for the attachment download endpoint of `crm serve` (mail fetch mocked)."""

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from crm import web

CFG = {"contacts": [], "removed": [],
       "config": {"timezone": "UTC+02:00", "imap": {"host": "h"}, "stages": ["cold"], "sources": ["cold"]}}


def test_api_attachment_validation():
    with pytest.raises(RuntimeError, match="IMAP not configured"):
        web.api_attachment({"contacts": [], "config": {}}, "INBOX", "1", "2")
    with pytest.raises(ValueError, match="folder"):
        web.api_attachment(CFG, "", "1", "2")
    with pytest.raises(ValueError, match="numeric"):
        web.api_attachment(CFG, "INBOX", "abc", "2")
    with pytest.raises(ValueError, match="numeric"):
        web.api_attachment(CFG, "INBOX", "1", "x")


def test_api_attachment_delegates_to_mail(monkeypatch):
    calls = {}

    def fake(cfg, folder, uid, part):
        calls.update(folder=folder, uid=uid, part=part)
        return b"%PDF", "application/pdf", "a.pdf"

    monkeypatch.setattr("crm.mail.fetch_attachment", fake)
    assert web.api_attachment(CFG, "Sent Items", "4711", "3") == (b"%PDF", "application/pdf", "a.pdf")
    assert calls == {"folder": "Sent Items", "uid": "4711", "part": "3"}


def test_http_attachment_download_headers_404_and_auth(monkeypatch):
    monkeypatch.setattr(web, "load_data", lambda: CFG)

    def fake(cfg, folder, uid, part):
        if uid == "404":
            raise LookupError("Message not found")
        return b"%PDF-1.4", "application/pdf", 'Tarjouspyyntö "QC".pdf'

    monkeypatch.setattr("crm.mail.fetch_attachment", fake)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    httpd.token = "secret"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/api/attachment"
    try:
        # token via ?t= (a plain <a href> can't set headers)
        r = urllib.request.urlopen(f"{base}?folder=Sent%20Items&uid=4711&part=3&t=secret")
        assert r.status == 200 and r.read() == b"%PDF-1.4"
        assert r.headers["Content-Type"] == "application/pdf"
        cd = r.headers["Content-Disposition"]
        assert cd.startswith("inline;")                        # PDF previews in the browser
        assert "filename*=UTF-8''Tarjouspyynt%C3%B6" in cd     # RFC 5987 UTF-8 name
        assert '"' not in cd.split("filename=")[1].split(";")[0][1:-1]  # quotes sanitised
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["Content-Security-Policy"] == "default-src 'none'"

        # explicit download override for a previewable type
        r = urllib.request.urlopen(f"{base}?folder=INBOX&uid=4711&part=3&t=secret&download=1")
        assert r.headers["Content-Disposition"].startswith("attachment;")

        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}?folder=INBOX&uid=404&part=1&t=secret")
        assert e.value.code == 404

        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}?folder=INBOX&uid=1&part=1")   # no token
        assert e.value.code == 403

        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}?folder=INBOX&uid=abc&part=1&t=secret")
        assert e.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize("ctype,expect", [
    ("application/pdf", "inline"),
    ("image/png", "inline"),
    ("text/plain", "inline"),
    ("text/html", "attachment"),          # never inline: script in the app origin
    ("image/svg+xml", "attachment"),      # SVG can carry script
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "attachment"),
    ("", "attachment"),                    # unknown -> octet-stream download
])
def test_inline_allowlist_decides_disposition(monkeypatch, ctype, expect):
    monkeypatch.setattr(web, "load_data", lambda: CFG)
    monkeypatch.setattr("crm.mail.fetch_attachment", lambda cfg, f, u, p: (b"x", ctype, "f.bin"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    httpd.token = "secret"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = urllib.request.urlopen(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/attachment?folder=INBOX&uid=1&part=1&t=secret")
        assert r.headers["Content-Disposition"].split(";")[0] == expect
        assert r.headers["Content-Type"] == (ctype or "application/octet-stream")
    finally:
        httpd.shutdown()
        httpd.server_close()
