"""Email parsing, sender authentication, rendering, Gmail adapter (BS-21; FR-EM-01, FR-EM-05). No database."""

import base64
import json

import httpx
import pytest

from app.channels.email.adapter import OutboundMail
from app.channels.email.gmail import GmailAdapter
from app.channels.email.parse import from_rfc822, html_to_text, strip_body
from app.channels.email.render import footnotes, render, subject_for, ticket_ref
from app.orchestrator.state import Citation
from app.rules.questions import questions


def raw(headers="", body="Hello", frm="Rohit <rohit@cust.example>"):
    return (f"From: {frm}\r\nTo: support@matsui-demo.example\r\nSubject: Hi\r\nMessage-ID: <a@b>\r\n{headers}\r\n{body}").encode()


@pytest.mark.parametrize(("body", "want"), [
    ("New text\n\nOn Mon, 1 Dec 2026 at 10:00, Matsui <s@m.example> wrote:\n> old", "New text"),
    ("New\n> quoted", "New"), ("Q?\nRegards,\nRohit", "Q?"), ("A\n-----Original Message-----\nB", "A"),
    ("Thanks\nThis line is long enough that the first word is clearly not a sign off because much more text follows here ok",
     "Thanks\nThis line is long enough that the first word is clearly not a sign off because much more text follows here ok")])
def test_strip_body(body, want):
    assert strip_body(body) == want


@pytest.mark.req("FR-EM-02")
def test_questions():
    assert questions("Hello,\n1) What is the drying temperature for PC?\n2) How long does a rotor last?\nThank you") == [
        "What is the drying temperature for PC?", "How long does a rotor last?"]
    assert questions("It is hot. Can you send someone today? Also what does it cost to repair?") == [
        "Can you send someone today?", "Also what does it cost to repair?"]
    assert questions("ok?") == []


@pytest.mark.req("FR-EM-04")
@pytest.mark.parametrize(("h", "ok"), [
    ("Authentication-Results: mx.google.com; dkim=pass header.i=@cust.example; spf=pass; dmarc=pass\r\n", True),
    ("Authentication-Results: mx.google.com; dkim=pass header.i=@cust.example; dmarc=none\r\n", True),
    ("Authentication-Results: mx.google.com; dkim=pass header.i=@evil.example; spf=pass\r\n", False),
    ("Authentication-Results: mx.google.com; dkim=pass header.i=@cust.example.evil.com\r\n", False),
    ("Authentication-Results: evil.example; dmarc=pass\r\n", False), ("", False),
    ("Authentication-Results: mx.google.com; dmarc=fail\r\nAuthentication-Results: mx.google.com; dmarc=pass\r\n", False)])
def test_sender_authentication(h, ok):
    assert from_rfc822(raw(h)).authenticated is ok


@pytest.mark.parametrize(("h", "frm", "auto"), [
    ("Auto-Submitted: auto-replied\r\n", "a@b.example", True), ("Precedence: bulk\r\n", "a@b.example", True),
    ("", "MAILER-DAEMON@x.example", True), ("", "no-reply@x.example", True), ("Auto-Submitted: no\r\n", "a@b.example", False)])
def test_auto_mail_is_recognised(h, frm, auto):
    assert from_rfc822(raw(h, frm=frm)).auto is auto


def test_multipart_html_and_attachment():
    msg = (b'From: a@b.example\r\nMessage-ID: <m@x>\r\nReferences: <r1@x> <r2@x>\r\nIn-Reply-To: <r2@x>\r\nMIME-Version: 1.0\r\n'
           b'Content-Type: multipart/mixed; boundary=B\r\n\r\n--B\r\nContent-Type: text/html\r\n\r\n<style>x</style><p>Dew &amp; point</p>\r\n'
           b'--B\r\nContent-Type: image/png\r\nContent-Disposition: attachment; filename="../../n.png"\r\nContent-Transfer-Encoding: base64\r\n\r\n'
           + base64.b64encode(b"PNG") + b"\r\n--B--\r\n")
    m = from_rfc822(msg)
    assert m.text.strip() == "Dew & point" and m.references == ["<r1@x>", "<r2@x>"] and m.in_reply_to == "<r2@x>"
    assert m.attachments[0].data == b"PNG" and html_to_text("<script>x</script>a<br>b") == "a\nb"


@pytest.mark.req("FR-EM-05")
@pytest.mark.parametrize(("s", "t", "want"), [
    ("Question", None, "Re: Question"), ("RE: re: Question", None, "Re: Question"), ("Down", "SR-2026-00141", "Re: Down [SR-2026-00141]"),
    ("Ticket SR-2026-00141?", "SR-2026-00141", "Re: Ticket SR-2026-00141?"), ("a\r\nBcc: x@y.example", None, "Re: a Bcc: x@y.example"),
    ("", None, "Re: Your enquiry")])
def test_subject(s, t, want):
    assert subject_for(s, t) == want


def test_ticket_ref_only_own_tickets():
    assert ticket_ref([], "about SR-2026-00105", set()) is None
    assert ticket_ref([], "about sr-2026-00105", {"SR-2026-00105"}) == "SR-2026-00105"
    assert ticket_ref([{"ticket_id": "SR-2026-00141"}], "x", set()) == "SR-2026-00141"


C = [Citation(marker="[E1]", evidence_id="D#a", doc_id="DOC-X", anchor="e04-dew-point"), Citation(marker="[E2]", evidence_id="kg:x"),
     Citation(marker="[E3]", evidence_id="D#a", doc_id="DOC-X", anchor="e04-dew-point")]


@pytest.mark.req("FR-EM-05")
def test_footnotes_and_both_parts():
    out, notes = footnotes("A [E2]. B [E1]. C [E3]. D [E9].", C, {"D#a": "Dryer manual"})
    assert out == "A [1]. B [2]. C [2]. D ." and notes == ["Matsui product data", "Dryer manual § e04 dew point"]
    txt, html = render(name="Rohit", text="**Troubleshooting**\nDo this [E1]:\n1. One\n2. Two", citations=C, titles={"D#a": "Dryer manual"}, ticket="SR-2026-00141")
    for part in (txt, html):
        for want in ("Dear Rohit", "Troubleshooting", "Dryer manual", "SR-2026-00141", "Matsui Customer Care", "AI-assisted", "synthetic"):
            assert want in part
    assert "<ol><li>One</li><li>Two</li></ol>" in html and "1. One" in txt and "**" not in txt + html


def test_html_is_escaped():
    _, html = render(name="<b>x</b>", text="<script>alert(1)</script> [E1]", citations=C, titles={"D#a": "<i>t</i>"}, ticket=None)
    assert "<script>" not in html and "<b>x" not in html and "<i>t" not in html


async def token():
    return "tok"


def gmail(handler):
    return GmailAdapter(token, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.req("FR-EM-01")
async def test_gmail_history_paging_fetch_and_send():
    sent = {}

    def h(req):
        assert req.headers["authorization"] == "Bearer tok"
        p = req.url.path.split("/users/me", 1)[1]
        if p == "/history":
            if "pageToken" not in req.url.params:
                return httpx.Response(200, json={"history": [{"messagesAdded": [{"message": {"id": "1"}}]}], "nextPageToken": "n", "historyId": "9"})
            return httpx.Response(200, json={"history": [{"messagesAdded": [{"message": {"id": "1"}}, {"message": {"id": "2"}}]}], "historyId": "12"})
        if p == "/messages/send":
            sent.update(json.loads(req.content))
            return httpx.Response(200, json={"id": "x"})
        return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(raw()).decode().rstrip("="), "threadId": "T"})
    g = gmail(h)
    assert await g.new_messages("5") == (["1", "2"], "12")
    m = await g.fetch("1")
    assert m.message_id == "<a@b>" and m.thread_id == "T" and m.from_addr == "rohit@cust.example"
    await g.send(OutboundMail("<r@x>", "a@b.example", "Re: Hi", "t", "<p>h</p>", "<a@b>", ["<a@b>"], "T"), None)
    mime = base64.urlsafe_b64decode(sent["raw"]).decode()
    assert sent["threadId"] == "T" and "In-Reply-To: <a@b>" in mime and "Auto-Submitted: auto-replied" in mime and "text/html" in mime
