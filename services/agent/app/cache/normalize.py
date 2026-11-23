"""Query normalizer for the exact cache (LLD-7, FR-CA-01): lowercase, expand shorthand, drop greetings and
politeness, strip punctuation and extra spaces. key = sha1(normalized)."""

import hashlib
import re

_SHORTHAND = {
    "pls": "please", "plz": "please", "u": "you", "ur": "your", "r": "are", "wat": "what", "wht": "what",
    "abt": "about", "hrs": "hours", "hr": "hour", "yr": "year", "yrs": "years", "mnth": "month", "mnths": "months",
    "amc's": "amc", "annual maintenance contract": "amc", "maintenance contract": "amc", "&": "and", "w/o": "without",
    "thx": "thanks", "info": "information", "qty": "quantity", "approx": "approximately",
}
_GREETING = re.compile(r"^((hi+|hello+|hey+)( (team|there|all|sir|madam|matsui))?|dear (team|sir|madam)|"
                       r"good (morning|afternoon|evening)|namaste)\b[\s,!.-]*",
                       re.IGNORECASE)
_POLITE = re.compile(r"\b(please|kindly|thanks|thank you|(can|could) you (please )?tell me|tell me|i want to know|"
                     r"i would like to know)\b", re.IGNORECASE)


def normalize(text: str) -> str:
    t = text.lower().strip()
    for _ in range(2):                                   # "hi team, hello" → both greetings go
        t = _GREETING.sub("", t).strip()
    for k in sorted(_SHORTHAND, key=len, reverse=True):
        t = re.sub(rf"(?<![\w']){re.escape(k)}(?![\w'])", _SHORTHAND[k], t)
    t = _POLITE.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def cache_key(text: str) -> str:
    return hashlib.sha1(normalize(text).encode()).hexdigest()
