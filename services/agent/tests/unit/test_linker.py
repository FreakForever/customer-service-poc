"""Entity linker (BS-09, LLD-9, FR-KG-02) on the seed graph files — no Neo4j needed."""

import json
from pathlib import Path

import pytest

from app.kg.linker import Linker, lucene_query
from app.providers.fake import FakeProvider

pytestmark = pytest.mark.req("FR-KG-02")
DATA = Path(__file__).resolve().parents[4] / "mock-data" / "data"
NODES = [json.loads(line) for line in (DATA / "kg" / "nodes.jsonl").open()]
EDGES = [json.loads(line) for line in (DATA / "kg" / "edges.jsonl").open()]
LABEL = {n["id"]: n["label"] for n in NODES}
L = Linker(NODES, EDGES)


def ids(q):
    return [e.id for e in L.link_dictionary(q).items]


def expected(q):
    path = [x for x in q["gold_kg_path"] if x in LABEL]
    want = {x for x in path if LABEL[x] in ("Model", "ErrorCode")}
    if "warehouse" in q["question"]:
        want |= {x for x in path if LABEL[x] == "Warehouse"}
    if "Platinum" in q["question"]:
        want.add("AMC-PLAN-PLATINUM")
    return want


def test_linking_accuracy_on_multi_hop_eval_items():
    qs = [json.loads(line) for line in (DATA / "eval" / "questions.jsonl").open()]
    multi = [q for q in qs if q["type"] == "multi_hop"]
    hits = total = 0
    for q in multi:
        got = set(ids(q["question"]))
        want = expected(q)
        hits += len(want & got)
        total += len(want)
    assert len(multi) == 54 and total > 100
    assert hits / total >= 0.95


@pytest.mark.parametrize(("q", "want"), [
    ("My DHD-150 is showing DEW PT HIGH", ["DHD-150", "DHD-E04"]),
    ("My MTC-W90 is showing PHASE", ["MTC-W90", "MTC-E07"]),
    ("Our GR-600 shows PHASE", ["GR-600", "RCY-G4"]),
    ("my dhd150 says dew pt high", ["DHD-150", "DHD-E04"]),            # case + hyphen tolerant, cue present
    ("E06 and LOW FLOW on the MTC-W90", ["MTC-E06", "MTC-W90"]),        # two mentions of one code, deduped
    ("my chiller shows A01", ["CHL", "CHL-A01"]),                       # family named instead of a model
    ("Is MT-DHD-ROTOR in stock at the Pune warehouse?", ["MT-DHD-ROTOR", "WH-PUN"]),
    ("Which service centre covers Pune?", ["CITY-pune"]),
    ("Is it covered under Platinum AMC or the comprehensive plan?", ["AMC-PLAN-PLATINUM", "AMC-PLAN-COMPREHENSIVE"]),
    ("My HAD-25 is showing LO.", ["HAD-25", "HAD-E5"]),
])
def test_dictionary_links(q, want):
    assert ids(q) == want


def test_short_and_lowercase_display_texts_need_case_or_a_cue():
    assert ids("he says the hopper is low") == []                      # "he", "lo"... are words, not codes
    assert ids("I need a new filter for my DHD-100") == ["DHD-100"]   # plain word, no cue
    assert ids("my DHD-100 is showing filter") == ["DHD-100", "DHD-E06"]
    assert "basic" not in {e.mention.lower() for e in L.link_dictionary("a basic question about AMC").items}


def test_code_in_several_families_without_context_is_ambiguous():
    ents = L.link_dictionary("What does PHASE mean on the display?")
    assert ents.error_codes == [] and len(ents.ambiguous) == 1
    assert len(ents.ambiguous[0].candidates) == 5 and ents.ambiguous[0].mention == "PHASE"
    e06 = L.link_dictionary("what does E06 mean")
    assert e06.ambiguous and {"MTC-E06", "DHD-E06"} <= set(e06.ambiguous[0].candidates)
    assert L.link_dictionary("what does E06 mean on a DHD-100").error_codes == ["DHD-E06"]


def test_entities_helpers():
    ents = L.link_dictionary("Our MTC-W90 shows E06, is MT-MTC-DESCALER at the Pune warehouse?")
    assert ents.model == "MTC-W90" and ents.family == "MTC" and ents.error_codes == ["MTC-E06"]
    assert ents.ids("SparePart") == ["MT-MTC-DESCALER"] and ents.ids("Warehouse") == ["WH-PUN"]
    assert all(e.source == "dictionary" and e.score == 1.0 for e in ents.items)


class StubFullText:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    async def search(self, query, limit):
        self.calls.append(query)
        return self.hits


async def test_fallback_order_dictionary_then_fulltext_then_embedding():
    ft = StubFullText([("C-ROTOR-DEG", "Cause", 3.2), ("CMP-ROTOR", "Component", 0.4)])
    hit = await L.link("my DHD-150 shows E04", fulltext=ft)
    assert ft.calls == [] and hit.error_codes == ["DHD-E04"]          # dictionary hit: no fallback queries
    res = await L.link("rotor looks contaminated with oil", fulltext=ft)
    assert ft.calls and [(e.id, e.source) for e in res.items] == [("C-ROTOR-DEG", "fulltext")]   # below min dropped
    name = next(n["props"]["name"] for n in NODES if n["id"] == "C-ROTOR-DEG")
    emb = await L.link(name, fulltext=StubFullText([]), embedder=FakeProvider(dim=128))
    assert emb.items[0].id == "C-ROTOR-DEG" and emb.items[0].source == "embedding" and emb.items[0].score > 0.99


def test_lucene_query_escapes_operators():
    assert lucene_query('rotor AND (name:x)* "q"') == 'rotor and \\(name\\:x\\)\\* \\"q\\"'
