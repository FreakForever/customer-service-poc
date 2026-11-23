"""Guardrails (LLD-6; FR-RT-04, FR-RT-05).

safety       → immediate safety instructions + P1 handoff (lexicon incl. Hinglish)
injection    → refuse (heuristics + optional classifier hook)
exfiltration → refuse: other customers' data, bulk lists/exports, system internals
oos          → polite redirect (strong off-topic patterns with no product/account entity; the kNN `oos` class in
               BS-13 catches the rest)
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.kg.linker import Entities

SAFETY = re.compile(
    r"\b(smok(e|es|ing|y)|burn(ing|t|ed)?( smell)?|fire|flames?|spark(s|ing)?|(electric(al)? )?shock(ed)?|"
    r"electrocut\w*|injur(ed|y|ies)|hurt|bleeding|refrigerant leak\w*|gas leak\w*|ammonia leak\w*|explo(de|ded|sion)|"
    r"melt(ed|ing)|dhuan|dhua|jal raha|jal rahi|aag|current laga|jhatka|chot)\b", re.IGNORECASE)
_NOT_SAFETY = re.compile(r"\bshock absorber|\bfire(d|wall)\b|\bburn(ing)? question\b|\bcease ?fire\b", re.IGNORECASE)

INJECTION = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in (
    (r"\b(ignore|disregard|forget|override|bypass)\b.{0,30}\b(previous|prior|above|all|earlier|your|the)\b.{0,20}"
    r"\b(instructions?|rules?|prompts?|guidelines|directions)\b"),
    r"\bsystem prompt\b", r"\b(reveal|print|show|repeat|output|leak)\b.{0,25}\b(prompt|instructions|configuration|rules)\b",
    r"\byou are now\b", r"\b(developer|debug|god|admin|jailbreak|dan) mode\b", r"\bjailbreak\b", r"\bact as (an? )?(admin|system|developer)\b",
    r"\bpretend (you are|to be)\b", r"^\s*(system|assistant|developer|admin)\s*:", r"<\|?(im_start|im_end|system)\|?>",
    r"\[/?INST\]", r"</?s>", r"\bnew instructions?\b", r"\bauthori[sz]ed by (your|the) (admin|developer|manager)\b",
    r"\byou are (a|an) (unfiltered|uncensored)\b",
)]
_BULK = re.compile(r"\b(all|every|each|full|entire|complete|whole)\b.{0,25}\b(customers?|clients?|users?|contacts?|"
                   r"phone numbers?|emails?|addresses|tickets|contracts|database|records?|list)\b|"
                   r"\b(customer|client|contact)s?\b.{0,10}\b(list|database|export|dump|directory)\b|"
                   r"\b(export|dump|download)\b.{0,20}\b(data|database|table|records?|customers?)\b|"
                   r"\bother customers?'?s?\b", re.IGNORECASE)
_ACCOUNT_WORDS = re.compile(r"\b(price|paid|pays|cost|amc|contract|ticket|phone|email|address|serial|machines?|"
                            r"orders?|invoice|account|details)\b", re.IGNORECASE)
OOS = re.compile(
    r"\b(share|stock) price\b|\bpoem|\bsong|\bjoke|\bstory\b|\belection|\bpolitic|\bprime minister|\bcricket|"
    r"\bfootball|\bipl\b|\bmatch score|\bweather\b|\brecipe|\bmovie|\bbitcoin|\bcrypto|\bhoroscope|\bhomework|"
    r"\bwrite (me )?(an? )?(essay|code|program|script)|\btranslate\b|\bcapital of\b|\bwho (is|was) the president\b|"
    r"\bdate me\b|\bgirlfriend|\bboyfriend", re.IGNORECASE)

InjectionClassifier = Callable[[str], float]


@dataclass
class GuardrailResult:
    flags: set[str] = field(default_factory=set)
    reasons: dict[str, str] = field(default_factory=dict)


def check(text: str, ents: Entities, *, own_company: str | None = None, companies: set[str] | None = None,
          classifier: InjectionClassifier | None = None, classifier_threshold: float = 0.8) -> GuardrailResult:
    res = GuardrailResult()
    if (m := SAFETY.search(text)) and not _NOT_SAFETY.search(text):
        res.flags.add("safety")
        res.reasons["safety"] = m.group(0)
    for p in INJECTION:
        if m := p.search(text):
            res.flags.add("injection")
            res.reasons["injection"] = m.group(0)[:80]
            break
    if "injection" not in res.flags and classifier is not None and classifier(text) >= classifier_threshold:
        res.flags.add("injection")
        res.reasons["injection"] = "classifier"
    if m := _BULK.search(text):
        res.flags.add("exfiltration")
        res.reasons["exfiltration"] = m.group(0)[:80]
    elif companies and _ACCOUNT_WORDS.search(text):
        low = text.lower()
        own = (own_company or "").lower()
        for name in companies:
            if name.lower() in low and name.lower() != own:
                res.flags.add("exfiltration")
                res.reasons["exfiltration"] = f"asks about another customer: {name}"
                break
    product = bool(ents.items or ents.has_account_ids or ents.part_nos)
    if (m := OOS.search(text)) and not product and "safety" not in res.flags:
        res.flags.add("oos")
        res.reasons["oos"] = m.group(0)
    return res


_ROLE_TAG = re.compile(r"^\s*(system|assistant|developer|admin|user)\s*:\s*", re.IGNORECASE | re.MULTILINE)
_SPECIAL = re.compile(r"<\|[^|>]{0,20}\|>|\[/?INST\]|</?s>|<<\s*/?SYS\s*>>", re.IGNORECASE)


def scan_evidence(text: str) -> bool:
    """True if retrieved/uploaded content contains instruction-like text (flag it in the trace; FR-RT-05)."""
    return any(p.search(text) for p in INJECTION)


def neutralize_evidence(text: str) -> str:
    """Retrieved content is data: drop chat special tokens and role prefixes before it goes into a prompt.
    The answer prompts (LLD-8 T2, Text2Cypher) also tell the model not to follow instructions in evidence."""
    return _ROLE_TAG.sub("", _SPECIAL.sub("", text))
