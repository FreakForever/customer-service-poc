"""Citation coverage (LLD-11, FR-VF-01)."""

import re
from dataclasses import dataclass

MARK = re.compile(r"\[E\d+\]")
_LIST = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_SPLIT = re.compile(r"(?<=[.!?])(?<!\bNo\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bapprox\.)\s+(?=[A-Z0-9*\"'(])")
_ENTITY = re.compile(r"\d|\b(INR|MT-[A-Z])")
_POLICY = re.compile(r"\b(warrant\w*|AMC|cover(ed|s|age)?|chargeable|free|contract|renew\w*|grace|policy|consumables?|"
                     r"dispatch\w*|cause[sd]?|faulty|fail(ed|ure)?|degraded|clogged|leak\w*)\b", re.IGNORECASE)
_IMPERATIVE = re.compile(r"^(please )?(isolate|switch|turn|check|inspect|clean|replace|remove|descale|refill|reset|raise|"
                         r"reduce|set|open|close|measure|verify|stop|disconnect|fit|re-?seat|calibrate|call|contact|"
                         r"restore|do not|don't|never)\b", re.IGNORECASE)
SAFETY = re.compile(r"\b(isolate|switch (it |the machine )?off|do not (restart|open|touch|bypass)|power off|safety|"
                    r"protective)\b", re.IGNORECASE)


@dataclass
class Coverage:
    sentences: list[str]
    factual: int
    covered: int
    uncovered: list[str]

    @property
    def score(self) -> float:
        return 1.0 if not self.factual else self.covered / self.factual


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for line in text.split("\n"):
        body = _LIST.sub("", line).strip()
        # a marker after the full stop belongs to the sentence before it: "… rotor. [E1] Next"
        body = re.sub(r"([.!?])((?:\s*\[E\d+\])+)", lambda m: m.group(2).strip() + m.group(1), body)
        out += [s.strip() for s in _SPLIT.split(body) if s.strip()]
    return out


def is_factual(sentence: str) -> bool:
    s = MARK.sub("", sentence).strip(" *")
    if not s or s.endswith((":", "?")):
        return False
    return bool(_ENTITY.search(s) or _POLICY.search(s) or _IMPERATIVE.search(s))


def measure(text: str) -> Coverage:
    sents = split_sentences(text)
    factual = [s for s in sents if is_factual(s)]
    unc = [s for s in factual if not MARK.search(s)]
    return Coverage(sents, len(factual), len(factual) - len(unc), unc)


def drop_uncovered(text: str) -> str:
    """Remove uncovered factual sentences, except safety warnings (dropping one is worse than it being uncited)."""
    bad = {s for s in measure(text).uncovered if not SAFETY.search(s)}
    lines = []
    for line in text.split("\n"):
        sents = split_sentences(line)
        keep = [s for s in sents if s not in bad]
        if not sents or len(keep) == len(sents):
            lines.append(line)
        elif keep:
            m = _LIST.match(line)
            lines.append((m.group(0) if m else "") + " ".join(keep))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
