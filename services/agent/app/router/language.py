"""Romanised-Hinglish detection (FR-CH-07). Replies are always English (OD-008 → D-030); the language is
recorded on the turn so prompts can say "the user wrote in Hinglish; answer in clear English"."""

import re
from typing import Literal

_HINDI = {
    "hai", "hain", "ho", "hota", "hoti", "kya", "kyun", "kyon", "nahi", "nahin", "mein", "mai", "ko", "se", "ka", "ki",
    "ke", "kar", "karo", "karna", "karein", "kaise", "kaisa", "kitna", "kitne", "kab", "kahan", "raha", "rahi", "rahe",
    "sakte", "sakta", "chahiye", "bhi", "aur", "par", "pe", "abhi", "kaun", "kaunsa", "accha", "theek", "bataiye",
    "batao", "mera", "meri", "mere", "hamara", "hamari", "aap", "aapka", "yeh", "woh", "kuch", "bahut", "jaldi",
    "lagega", "milega", "aayega", "chal", "gaya", "gayi", "tha", "thi", "ji", "bhai", "haan", "matlab", "wala", "wali",
}
_WORD = re.compile(r"[a-z]+")
Language = Literal["en", "hinglish", "mixed"]


def detect_language(text: str) -> Language:
    words = _WORD.findall(text.lower())
    if not words:
        return "en"
    share = sum(w in _HINDI for w in words) / len(words)
    if share >= 0.25:
        return "hinglish"
    return "mixed" if share > 0 else "en"
