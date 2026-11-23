"""GraphRAG planning, verbalization and merge without databases (BS-10, LLD-9, FR-KG-05)."""

import json
from pathlib import Path

import pytest

from app.kg.linker import Linker
from app.kg.verbalize import verbalize, verbalize_query_rows
from app.retrieval.evidence import Evidence
from app.retrieval.graphrag import GraphRAGDeps, choose_templates, merge

pytestmark = pytest.mark.req("FR-KG-05")
DATA = Path(__file__).resolve().parents[4] / "mock-data" / "data" / "kg"
L = Linker([json.loads(x) for x in (DATA / "nodes.jsonl").open()], [json.loads(x) for x in (DATA / "edges.jsonl").open()])
DEPS = GraphRAGDeps(linker=L)


def plan(q, intent=None):
    return choose_templates(q, L.link_dictionary(q), DEPS, intent)


def test_template_plans_follow_question_shape():
    assert plan("My MTC-W90 is showing LOW FLOW. What is the most likely cause?") == [
        ("T-DIAG", {"model": "MTC-W90", "code": "E06"})]
    assert plan("Would the part needed for E06 on a MTC-W90 be covered under a Platinum AMC?") == [
        ("T-DIAG", {"model": "MTC-W90", "code": "E06"}), ("T-COVER", {"model": "MTC-W90", "code": "E06"})]
    assert plan("Our MTC-W90 shows E06, which spare part and is it available at the Pune warehouse?") == [
        ("T-DIAG", {"model": "MTC-W90", "code": "E06"}), ("T-COVER", {"model": "MTC-W90", "code": "E06"})]
    assert plan("Is MT-DHD-ROTOR in stock at the Pune warehouse?") == [
        ("T-STOCK", {"part": "MT-DHD-ROTOR", "city": "Pune"})]
    assert plan("Which service centre engineer covers Pune?") == [("T-SC", {"city": "Pune"})]
    assert plan("Which other codes share a cause with E04 on my DHD-150?")[-1] == (
        "T-SHARED", {"error_id": "DHD-E04", "family": "DHD"})
    assert plan("How many error codes does the chiller family have?") == []          # → Text2Cypher
    assert plan("my DHD-150 shows E04", intent="parts.availability")[1][0] == "T-COVER"


DIAG_ROW = {"error_id": "DHD-E04", "code": "E04", "error": "E04 Dew point high", "severity": "warning",
            "safety_stop": False, "cause_id": "C-ROTOR-DEG", "cause": "Desiccant rotor degraded or contaminated",
            "likelihood": 0.4, "resolution_id": "R-DHD-C-ROTOR-DEG", "resolution": "x",
            "steps": "Inspect rotor; replace desiccant rotor.", "performed_by": "technician", "parts": ["MT-DHD-ROTOR"],
            "path_id": "T-DIAG:DHD-150>DHD-E04>C-ROTOR-DEG>R-DHD-C-ROTOR-DEG"}


def test_verbalize_diag_and_stock():
    [ev] = verbalize("T-DIAG", {"model": "DHD-150", "code": "E04"}, [DIAG_ROW])
    assert ev.text.startswith("DHD-150 can raise E04 (Dew point high, severity warning). Possible cause: "
                              "Desiccant rotor degraded or contaminated (likelihood 40%).")
    assert "Parts: MT-DHD-ROTOR." in ev.text and ev.path_ids == [DIAG_ROW["path_id"]]
    assert ev.citation == "kg:" + DIAG_ROW["path_id"] and ev.metadata["error_id"] == "DHD-E04"
    stock = [{"part_no": "MT-DHD-ROTOR", "part": "Desiccant rotor", "warehouse": w, "warehouse_city": c, "qty": q,
              "is_home_warehouse": h, "lead_time_days": 23, "path_id": f"T-STOCK:MT-DHD-ROTOR>{w}"}
             for w, c, q, h in [("WH-PUN", "Pune", 1, True), ("WH-NOI", "Noida", 2, False)]]
    [s] = verbalize("T-STOCK", {"part": "MT-DHD-ROTOR", "city": "Pune"}, stock, {"WH-PUN": "Pune regional warehouse"})
    assert "Pune regional warehouse [WH-PUN]: 1 (home warehouse for Pune)" in s.text and "lead time is 23 days" in s.text
    assert s.path_ids == ["T-STOCK:MT-DHD-ROTOR>WH-PUN", "T-STOCK:MT-DHD-ROTOR>WH-NOI"]
    assert verbalize("T-DIAG", {}, []) == []


def test_verbalize_cover_and_text2cypher_rows():
    [c] = verbalize("T-COVER", {"model": "DHD-150", "code": "E04"}, [{
        "part_no": "MT-DHD-ROTOR", "part": "Desiccant rotor", "cause_id": "C-ROTOR-DEG", "cause": "Rotor degraded",
        "likelihood": 0.4, "covered_by": [], "path_id": "T-COVER:DHD-150>DHD-E04>C-ROTOR-DEG>R>MT-DHD-ROTOR"}])
    assert "the most likely cause that needs a part is Rotor degraded (likelihood 40%) needs MT-DHD-ROTOR" in c.text
    assert c.text.endswith("covered by: no AMC plan.") and c.metadata["covered_by"] == []
    tie = verbalize("T-COVER", {"model": "CHL-10W", "code": "A05"}, [
        {"part_no": p, "part": p, "cause_id": k, "cause": k, "likelihood": 0.35, "covered_by": cov,
         "path_id": f"T-COVER:CHL-10W>CHL-A05>{k}>R>{p}"}
        for k, p, cov in [("C-FSW-FAULT", "MT-CHL-FSW", []), ("C-PUMP-SEIZED", "MT-CHL-PUMP", ["Platinum AMC"])]])[0]
    assert "2 causes are equally likely" in tie.text and len(tie.path_ids) == 2
    assert tie.metadata["coverage"] == {"MT-CHL-FSW": [], "MT-CHL-PUMP": ["Platinum AMC"]}
    [t] = verbalize_query_rows("MATCH (e:ErrorCode) RETURN count(e) AS n", [{"n": 44}])
    assert t.source == "text2cypher" and "n=44" in t.text
    assert t.id == verbalize_query_rows("MATCH (e:ErrorCode) RETURN count(e) AS n", [{"n": 1}])[0].id   # stable id


def test_merge_orders_dedupes_and_caps():
    def ev(i, kind, score=0.5, source="hybrid"):
        return Evidence(id=i, kind=kind, text=i, source=source, score=score)
    kg = [ev("kg:a", "kg_path", 0.2), ev("kg:b", "kg_path", 0.7), ev("kg:a", "kg_path", 0.2)]
    documented = [ev("doc:M#e04", "chunk", 1.0, "documented_in")]
    rag = [ev("doc:M#e04", "chunk"), ev("doc:P#x", "chunk")]
    out = merge(kg, documented, rag)
    assert [e.id for e in out] == ["kg:b", "kg:a", "doc:M#e04", "doc:P#x"]
    assert out[2].source == "documented_in" and out[2].metadata["also_from"] == ["hybrid"]
    many = merge([ev(f"kg:{i}", "kg_path") for i in range(20)], [], [ev(f"doc:{i}", "chunk") for i in range(20)])
    assert sum(e.kind == "kg_path" for e in many) == 8 and sum(e.kind == "chunk" for e in many) == 6
