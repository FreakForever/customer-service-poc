"""Branded reply (FR-EM-05): text and HTML parts from one block structure. Jinja autoescapes the HTML."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from app.orchestrator.state import Citation

_MARK = re.compile(r"\[E(\d+)\]")
_REF = re.compile(r"\bSR-\d{4}-\d{5}\b")


@lru_cache
def _env(html: bool) -> Environment:
    return Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=html, trim_blocks=True, lstrip_blocks=True)


def subject_for(original: str, ticket: str | None) -> str:
    s = re.sub(r"[\r\n\t]+", " ", original).strip() or "Your enquiry"
    s = re.sub(r"^((re|fwd?|aw)\s*:\s*)+", "", s, flags=re.IGNORECASE)[:200]
    tag = f" [{ticket}]" if ticket and ticket not in s else ""
    return f"Re: {s}{tag}"


def footnotes(text: str, citations: list[Citation], titles: dict[str, str]) -> tuple[str, list[str]]:
    """[E#] → [1], [2]… in order of appearance, one footnote per source."""
    by_mark = {c.marker: c for c in citations}
    order: dict[str, int] = {}
    notes: list[str] = []

    def sub(m: re.Match[str]) -> str:
        c = by_mark.get(m.group(0))
        if c is None:
            return ""
        label = (titles.get(c.evidence_id) or c.doc_id or "Matsui product data") if c.doc_id else "Matsui product data"
        label += f" § {c.anchor.replace('-', ' ')}" if c.doc_id and c.anchor else ""
        if label not in order:
            order[label] = len(order) + 1
            notes.append(label)
        return f"[{order[label]}]"
    out = re.sub(r"(\[\d+\])(?:\s*\1)+", r"\1", _MARK.sub(sub, text))
    return out, notes


def blocks(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if m := re.fullmatch(r"\*\*(.+)\*\*", s):
            out.append({"kind": "heading", "text": m.group(1)})
        elif m := re.match(r"(\d+[.)]|[-*•])\s+(.*)", s):
            kind = "ol" if m.group(1)[0].isdigit() else "ul"
            if not (out and out[-1]["kind"] == kind):
                out.append({"kind": kind, "items": []})
            out[-1]["items"].append(m.group(2).replace("**", ""))
        else:
            out.append({"kind": "p", "text": s.replace("**", "")})
    return out


def render(*, name: str, text: str, citations: list[Citation], titles: dict[str, str], ticket: str | None) -> tuple[str, str]:
    body, notes = footnotes(text, citations, titles)
    ctx = {"name": re.sub(r"\s+", " ", name).strip()[:60] or "Customer", "blocks": blocks(body), "notes": notes, "ticket": ticket}
    return _env(False).get_template("reply.txt.j2").render(**ctx).strip() + "\n", _env(True).get_template("reply.html.j2").render(**ctx)


def render_manual(text: str) -> str:
    """HTML for a reply a person wrote or edited: their text is the whole message (greeting and signature included)."""
    return _env(True).get_template("manual.html.j2").render(paragraphs=[p.split("\n") for p in re.split(r"\n{2,}", text.strip())])


def ticket_ref(actions: list[dict[str, Any]], subject: str, allowed: set[str]) -> str | None:
    for a in actions:
        if a.get("ticket_id"):
            return str(a["ticket_id"])
    return next((t for t in _REF.findall(subject.upper()) if t in allowed), None)
