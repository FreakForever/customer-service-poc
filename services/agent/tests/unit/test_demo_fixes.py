"""Questions that went wrong on the live demo (BS-32): warranty policy by e-mail, lists of codes by cause."""

import pytest

from app.agents.troubleshoot import _BY_CAUSE, _SMALL, _WHICH_CODES, words
from app.rules.entities import extract_entities
from app.rules.intents import exact_intents
from tests.security.test_guardrails import CTX


@pytest.mark.parametrize(("text", "policy"), [
    ("How long is the warranty on a new dryer?", True), ("Warranty question\nHow long is the warranty on a new dryer?", True), ("what is the warranty period", True),
    ("How many months is the warranty?", True), ("How long is the warranty on our dryer?", False), ("How long is my warranty", False),
    ("what is the warranty status", False), ("Is my dryer still under warranty?", False), ("what's the warranty on MT-DHD150-23-00417", False),
    ("What is wrong with it? The warranty ended.", False), ("How long do we have left on the warranty?", False)])
def test_policy_or_own_machine(text, policy):
    got = [i.name for i in exact_intents(text, extract_entities(text, CTX.linker))]
    assert (got == ["faq.warranty"]) is policy, got


@pytest.mark.parametrize(("text", "causes"), [
    ("Which Dehumidifying dryer error codes can be caused by a clogged filter?", {"C-FLT-CLOG"}), ("What codes are due to a refrigerant leak?", {"C-REF-LEAK"}),
    ("Which codes can be caused by bad luck?", set())])
def test_cause_is_found_by_its_words(text, causes):
    assert _WHICH_CODES.search(text)
    want = words(_BY_CAUSE.search(text).group(1)) - _SMALL
    assert {n for n, x in CTX.linker.nodes.items() if x["label"] == "Cause" and want <= words(x["props"]["name"])} == causes
