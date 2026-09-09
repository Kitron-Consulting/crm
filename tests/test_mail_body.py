"""Tests for HTML-mail -> text conversion, body extraction, and quoted-history
splitting in crm.mail. Pure functions; no network."""

from email.message import EmailMessage

from crm.mail import extract_body, html_to_text, split_quoted


# --- html_to_text ---------------------------------------------------------

def test_paragraphs_and_breaks_become_newlines():
    html = "<html><body><p>Hello&nbsp;there,</p><p>Second<br>line</p></body></html>"
    assert html_to_text(html) == "Hello there,\n\nSecond\nline"


def test_style_and_script_content_dropped_and_entities_decoded():
    html = ("<head><style>p{color:red}</style><title>x</title></head>"
            "<body><script>var a=1;</script><p>Tom &amp; Jerry &#39;quoted&#39; &lt;tag&gt;</p></body>")
    out = html_to_text(html)
    assert out == "Tom & Jerry 'quoted' <tag>"
    assert "color" not in out and "var a" not in out


def test_lists_get_bullets():
    html = "<p>Agenda:</p><ul><li>One</li><li>Two &amp; a half</li></ul><p>End</p>"
    assert html_to_text(html) == "Agenda:\n\n• One\n• Two & a half\n\nEnd"


def test_blockquote_gets_quote_prefix():
    html = "<p>Reply</p><blockquote><p>Original line</p></blockquote>"
    assert html_to_text(html) == "Reply\n\n> Original line"


def test_whitespace_collapses_like_a_browser():
    html = "<div>  lots   of\n\n   space  </div><div>next</div>"
    assert html_to_text(html) == "lots of space\nnext"


def test_table_cells_separated():
    html = "<table><tr><td>Name</td><td>Matti</td></tr><tr><td>Role</td><td>CEO</td></tr></table>"
    assert html_to_text(html) == "Name\tMatti\nRole\tCEO"


def test_broken_html_never_raises():
    assert "text" in html_to_text("<p>text<div><<<")


# --- extract_body ---------------------------------------------------------

def _alt(plain, html):
    m = EmailMessage()
    m["Subject"] = "x"
    m.set_content(plain)
    m.add_alternative(html, subtype="html")
    return m


def test_prefers_real_plain_part():
    m = _alt("Plain version here, perfectly fine.", "<p>HTML version</p>")
    assert extract_body(m).strip() == "Plain version here, perfectly fine."


def test_placeholder_plain_falls_back_to_html():
    m = _alt("This message requires HTML.", "<p>Real <b>content</b> here</p>")
    assert extract_body(m) == "Real content here"


def test_single_part_html_is_converted_not_raw():
    m = EmailMessage()
    m.set_content("<html><body><p>Hi&nbsp;Matti</p><p>Regards</p></body></html>", subtype="html")
    out = extract_body(m)
    assert out == "Hi Matti\n\nRegards"
    assert "<" not in out


def test_plain_only_message_unchanged():
    m = EmailMessage()
    m.set_content("Just text.\n\nBye")
    assert extract_body(m).strip() == "Just text.\n\nBye"


# --- split_quoted ---------------------------------------------------------

def test_gmail_on_wrote_intro():
    text = "Thanks, sounds good.\n\nOn Tue, Sep 8, 2026 at 11:00 AM Matti <m@x.fi> wrote:\n> earlier\n> stuff"
    main, quoted = split_quoted(text)
    assert main == "Thanks, sounds good."
    assert quoted.startswith("On Tue") and "> earlier" in quoted


def test_finnish_kirjoitti_intro_wrapped_over_two_lines():
    text = ("Kiitos viestistä.\n\nti 8. syysk. 2026 klo 11.00 Matti Meikäläinen\n"
            "<matti@konepaja.fi> kirjoitti:\n> Hei")
    main, quoted = split_quoted(text)
    assert main == "Kiitos viestistä."
    assert quoted.startswith("ti 8. syysk.")


def test_outlook_header_block_with_underscore_rule():
    text = ("Ok, let's do Thursday.\n\n________________________________\nFrom: Liisa <l@v.fi>\n"
            "Sent: Monday\nTo: me\nSubject: Re: agenda\n\nold text")
    main, quoted = split_quoted(text)
    assert main == "Ok, let's do Thursday."
    assert quoted.startswith("____") and "From: Liisa" in quoted


def test_original_message_separator():
    text = "Reply here.\n-----Original Message-----\nFrom: x\nblah"
    main, quoted = split_quoted(text)
    assert main == "Reply here." and quoted.startswith("-----Original")


def test_bare_quote_lines():
    text = "Agreed.\n\n> line 1\n> line 2\n> line 3"
    main, quoted = split_quoted(text)
    assert main == "Agreed." and quoted == "> line 1\n> line 2\n> line 3"


def test_no_marker_returns_whole_text():
    assert split_quoted("Nothing quoted here.") == ("Nothing quoted here.", "")


def test_pure_forward_is_not_hidden():
    text = "-----Original Message-----\nFrom: a\nSubject: b\n\nforwarded body"
    assert split_quoted(text) == (text, "")


def test_from_line_in_prose_is_not_a_header_block():
    text = "From: my perspective this is fine.\nLet's proceed."
    assert split_quoted(text) == (text, "")
