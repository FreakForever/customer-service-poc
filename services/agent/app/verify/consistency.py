"""Entity and number consistency (LLD-11, FR-VF-03). Values must appear in the evidence (or be the customer's own
words, or the result of an action this turn). Existing in the catalog isn't enough: a wrong part is usually another
real part (D-036). A price must appear in evidence that also names the part it is stated for."""

import datetime as dt
import re
from typing import Any

from app.retrieval.evidence import Evidence
from app.verify.coverage import split_sentences

PATTERNS = {
    "serial": re.compile(r"\bMT-[A-Z0-9]+-\d{2}-\d{5}\b"),
    "part": re.compile(r"\bMT-[A-Z]{3}-[A-Z0-9]+(?:-[A-Z0-9]+)*\b"),
    "ticket": re.compile(r"\bSR-\d{4}-\d{5}\b"),
    "amc": re.compile(r"\bAMC-\d{4}-\d{4}\b"),
    "code": re.compile(r"(?<![\w-])(?:[A-Z]{3}-)?([A-Z]\d{1,2})(?![\w-])"),
}
_PRICE = re.compile(r"(?:INR|Rs\.?|₹)\s?([\d,]+)", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_EXAMPLE = re.compile(r"\((?:[^()]*\b)?(?:for example|e\.g\.)[^()]*\)", re.IGNORECASE)
_CITE = re.compile(r"\[E\d+(?:\s*,\s*E?\d+)*\]")


def _date(y: int, m: int, d: int) -> str | None:
    try:
        return dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def values(text: str) -> dict[str, set[str]]:
    text = _CITE.sub(" ", _EXAMPLE.sub(" ", text))
    out: dict[str, set[str]] = {}
    for k, p in PATTERNS.items():
        out[k] = {(m.group(1) if k == "code" else m.group(0)).upper() for m in p.finditer(text)}
    out["part"] -= out["serial"]
    out["price"] = {p.replace(",", "").rstrip() for p in _PRICE.findall(text) if p.strip(",")}
    dates = [_date(int(y), int(m), int(d)) for y, m, d in _ISO.findall(text)]
    dates += [_date(int(y), _MONTHS[m[:3].lower()], int(d)) for d, m, y in _DMY.findall(text) if m[:3].lower() in _MONTHS]
    dates += [_date(int(y), _MONTHS[m[:3].lower()], int(d)) for m, d, y in _MDY.findall(text) if m[:3].lower() in _MONTHS]
    out["date"] = {d for d in dates if d}
    return out


def check(text: str, evidence: list[Evidence], *, question: str = "", actions: list[dict[str, Any]] | None = None) -> list[str]:
    """Issues found; [] = consistent."""
    sources = [f"{e.text} {' '.join(e.path_ids)} {e.metadata}" for e in evidence]
    known = values(" ".join([*sources, " ".join(str(a) for a in actions or [])]))
    asked = values(question)
    issues = []
    for kind, vals in values(text).items():
        allowed = known[kind] | (asked[kind] if kind != "price" else set())
        issues += [f"{kind} {v} is not in the evidence" for v in sorted(vals - allowed)]
    per_source = [values(s) for s in sources]
    for s in split_sentences(text):
        v = values(s)
        if len(v["part"]) == 1 and v["price"]:
            part = next(iter(v["part"]))
            for price in v["price"] & known["price"]:
                if not any(part in x["part"] and price in x["price"] for x in per_source):
                    issues.append(f"price {price} is not stated for part {part}")
    return issues
