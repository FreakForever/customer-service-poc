"""Split a message into its separate questions (FR-EM-02)."""

import re

_NUM = re.compile(r"^\s*(?:\(?\d{1,2}[.)]|[-*•])\s+")


def questions(body: str) -> list[str]:
    """Separate questions in a message (numbered, bulleted or sentences ending in '?')."""
    out = []
    for line in body.split("\n"):
        for s in re.split(r"(?<=[?])\s+", _NUM.sub("", line).strip()):
            s = re.split(r"(?<=[.!])\s+", s)[-1].strip() if s.endswith("?") else ""
            if len(s.split()) >= 3:
                out.append(s)
    return list(dict.fromkeys(out))
