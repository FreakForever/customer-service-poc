"""CSV parsing and the rules that decide what a model-proposed graph change may do (BS-28; FR-KB-02, FR-KB-03). No I/O."""

import json
from pathlib import Path

import pytest

from app.ingest.csv_loaders import COLUMNS, BadFile, Result, parse
from app.ingest.extraction import Extraction, document_links, plan

DATA = Path(__file__).resolve().parents[4] / "mock-data"
SB = (DATA / "upload-samples" / "SB-2026-12_DHD_rotor_revision.md").read_text()
NODES = {n["id"]: {"label": n["label"], "props": n["props"]} for n in map(json.loads, (DATA / "data" / "kg" / "nodes.jsonl").open())}
E11 = {"id": "DHD-E11", "label": "ErrorCode", "props": {"code": "E11", "name": "Rotor drive motor stall", "display_text": "ROTOR STALL",
                                                         "severity": "critical", "safety_stop": True}}
R2 = {"id": "MT-DHD-ROTOR-R2", "label": "SparePart", "props": {"name": "Rotor R2", "price_inr": 52500, "lead_time_days_if_unavailable": 25}}
CAUSE = {"id": "C-RBELT", "label": "Cause", "props": {"name": "Rotor drive belt slipping or broken"}}


def ex(nodes=(), edges=(), confidence=0.95):
    return Extraction.model_validate({"nodes": list(nodes), "edges": [dict(zip(("src", "type", "dst", "props"), e, strict=False)) for e in edges],
                                      "confidence": confidence})


def run(*found, edges=frozenset(), family="DHD", doc=SB):
    return plan([(i, f) for i, f in enumerate(found, 1)], doc_text=doc, family=family, nodes=NODES, edges=set(edges))


def key(c):
    return c.payload.get("id") or (c.payload["src"], c.payload["type"], c.payload["dst"])


@pytest.mark.req("FR-KB-03")
def test_additions_stated_in_the_document_are_applied_with_provenance():
    p = run(ex([E11, R2, CAUSE], [("DHD-150", "CAN_RAISE", "DHD-E11"), ("MT-DHD-ROTOR-R2", "REPLACES", "MT-DHD-ROTOR", {"applies_to": "25-xxxxx onwards"}),
                                  ("DHD-E11", "CAUSED_BY", "C-RBELT", {"likelihood": 0.6}), ("MT-DHD-ROTOR-R2", "STOCKED_AT", "WH-PUN", {"qty": 3})]))
    assert p.dropped == [] and all(c.auto and c.chunk_id == 1 for c in p.changes) and len(p.changes) == 7
    e11 = next(c for c in p.changes if key(c) == "DHD-E11")
    assert e11.payload["props"]["name"] == "E11 Rotor drive motor stall"
    assert next(c for c in p.changes if c.payload.get("type") == "CAUSED_BY").payload["props"] == {"likelihood": 0.6, "family": "DHD"}
    document_links(p, doc_id="DOC-X", title="T", doc_type="bulletin", version="1.0", known=False)
    assert {("DHD-E11", "FOR_FAMILY", "DHD"), ("DHD-E11", "DOCUMENTED_IN", "DOC-X"), "DOC-X"} <= {key(c) for c in p.changes}


@pytest.mark.req("FR-KB-03")
def test_low_confidence_waits():
    p = run(ex([E11], [("DHD-150", "CAN_RAISE", "DHD-E11")], confidence=0.84))
    assert [c.auto for c in p.changes] == [False, False]


@pytest.mark.parametrize("props", [{"price_inr": 5250}, {"price_inr": 525000}, {"lead_time_days_if_unavailable": 26}])
def test_numbers_not_in_the_document_wait(props):
    [c, e] = run(ex([{**R2, "props": {**R2["props"], **props}}], [("MT-DHD-ROTOR-R2", "FITS", "DHD-150")])).changes
    assert not c.auto and not e.auto                                         # and so does everything that hangs on it


@pytest.mark.parametrize("edge", [("DHD-E11", "CAUSED_BY", "C-RBELT", {"likelihood": 0.7}), ("MT-DHD-ROTOR-R2", "STOCKED_AT", "WH-PUN", {"qty": 77}),
                                  ("DHD-300M", "CAN_RAISE", "DHD-E11"), ("MT-DHD-ROTOR-R2", "FITS", "DHD-50")])
def test_edges_not_in_the_document_wait(edge):
    c = run(ex([E11, R2, CAUSE], [edge])).changes[-1]
    assert c.op == "add_edge" and not c.auto


@pytest.mark.parametrize("node", [
    {**E11, "id": "DHD-E99", "props": {**E11["props"], "code": "E99"}}, {**E11, "id": "CHL-E11"}, {**E11, "id": "E11"},
    {**E11, "props": {"code": "E11", "name": "E11", "severity": "critical"}}, {**E11, "props": {"code": "E11", "name": "Rotor stall"}},
    {**R2, "id": "MT-DHD-GHOST"}, {"id": "C-X", "label": "Cause", "props": {}}, {"id": "R-DHD-C-X", "label": "Resolution", "props": {"name": "x"}},
    {**CAUSE, "id": "x y'}) DETACH DELETE"}, {"id": "DHD-150", "label": "SparePart", "props": {"name": "x"}}])
def test_bad_nodes_are_dropped(node):
    p = run(ex([node]))
    assert p.changes == [] and len(p.dropped) == 1


@pytest.mark.parametrize("edge", [("DHD-150", "CAN_RAISE", "C-RBELT"), ("DHD-150", "FITS", "DHD-100"), ("GHOST", "CAN_RAISE", "DHD-E04"),
                                  ("DHD-E11", "CAUSED_BY", "C-RBELT"), ("MT-DHD-ROTOR", "REPLACES", "MT-DHD-ROTOR"), ("MT-DHD-ROTOR", "STOCKED_AT", "WH-PUN")])
def test_bad_edges_are_dropped(edge):
    p = run(ex([E11, CAUSE], [edge]))
    assert [c.op for c in p.changes] == ["add_node", "add_node"] and len(p.dropped) == 1


def test_circular_replacement_is_dropped():
    p = run(ex([R2], [("MT-DHD-ROTOR", "REPLACES", "MT-DHD-ROTOR-R2")]), edges={("MT-DHD-ROTOR-R2", "REPLACES", "MT-DHD-ROTOR")})
    assert [c.op for c in p.changes] == ["add_node"]


@pytest.mark.req("FR-KB-03")
def test_existing_facts_are_never_rewritten_automatically():
    doc = SB + " MT-DHD-ROTOR now INR 61,000."
    node = {"id": "MT-DHD-ROTOR", "label": "SparePart", "props": {"name": "Other name", "price_inr": 61000}}
    [c] = run(ex([node]), doc=doc).changes
    assert (c.op, c.auto, c.payload) == ("update_node", False, {"id": "MT-DHD-ROTOR", "props": {"price_inr": 61000}})
    assert run(ex([node])).changes == []                                     # 61000 isn't in the bulletin
    assert run(ex([{**node, "props": {"name": "x", "price_inr": NODES["MT-DHD-ROTOR"]["props"]["price_inr"]}}])).changes == []
    have = ("DHD-150", "CAN_RAISE", "DHD-E04")
    assert run(ex([], [have]), edges={have}).changes == []


def test_the_best_supported_description_wins():
    vague = {**E11, "props": {**E11["props"], "display_text": "INVENTED"}}
    for order in ((ex([vague], confidence=0.9), ex([E11])), (ex([E11]), ex([vague], confidence=0.99))):
        p = run(*order)
        assert [(c.auto, c.payload["props"]["display_text"]) for c in p.changes] == [(True, "ROTOR STALL")]


# ---------- CSV ----------

def csv_(kind, *rows, head=None):
    r = Result()
    return parse(kind, ("\n".join([head or ",".join(COLUMNS[kind]), *rows]) + "\n").encode(), r), r


EC = "RCY,G5,Bearing temperature high,BRG TEMP,critical,true,C-MOT-OVL|C-BLADE-DULL,0.7|0.3,note"


@pytest.mark.req("FR-KB-02")
def test_samples_parse_completely():
    for name, kind, n in (("new_error_codes.csv", "error_codes", 2), ("faq_additions.csv", "faqs", 2)):
        r = Result()
        assert len(parse(kind, (DATA / "upload-samples" / name).read_bytes(), r)) == n and r.rejected == []


@pytest.mark.req("FR-KB-02")
def test_bad_rows_are_reported_with_their_line_and_good_ones_kept():
    ok, r = csv_("error_codes", EC, "XXX,G6,Title,T,critical,true,C-A,1,", "", 'RCY,G7,"Two\nlines",T,fatal,maybe,C-A,nan,', EC, "RCY,G8,short")
    assert [(line, x.key) for line, x in ok] == [(2, "RCY-G5")]
    assert [x["line"] for x in r.rejected] == [3, 5, 7, 8]
    assert "family" in r.rejected[0]["error"] and {"severity", "safety_stop"} <= {w.strip(":") for w in r.rejected[1]["error"].split()}
    assert "already on line 2" in r.rejected[2]["error"] and "3 values for 9 columns" in r.rejected[3]["error"]


def test_spare_part_stock_columns():
    ok, r = csv_("spare_parts", "MT-DHD-ROTOR-R2,DHD,Rotor R2,52500,false,DHD-100|DHD-150,2,3,0,0,25,MT-DHD-ROTOR", "MT-DHD-X,DHD,Name,-1,no,DHD-100,1,x,0,0,5,",
                 head=",".join(COLUMNS["spare_parts"]) + ",replaces")
    assert ok[0][1].stock == {"WH-NOI": 2, "WH-PUN": 3, "WH-CHE": 0, "WH-AMD": 0} and ok[0][1].replaces == "MT-DHD-ROTOR"
    assert "price_inr" in r.rejected[0]["error"] and "stock.WH-PUN" in r.rejected[0]["error"]


@pytest.mark.parametrize("data", [b"", b"a,b\n1,2\n", b"\xff\xfe\x00", b"id,category,question,paraphrases,answer,source_doc\n",
                                  b"id,id,category,question,paraphrases,answer,source_doc\n", b'id,category,question,paraphrases,answer,source_doc\n"x'])
def test_unusable_files(data):
    with pytest.raises(BadFile):
        parse("faqs", data, Result())


def test_excel_bom_and_crlf():
    ok, r = Result(), Result()
    assert len(parse("error_codes", ("﻿" + ",".join(COLUMNS["error_codes"]) + "\r\n" + EC + "\r\n").encode(), r)) == 1 and ok.rejected == r.rejected
