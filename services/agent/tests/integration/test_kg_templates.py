"""Knowledge graph load and Cypher templates (BS-05, LLD-4) against AuraDB dev.

Needs the graph loaded (`make kg-load`). Expected answers are computed from the mock-data catalog, not hardcoded,
so the templates are checked against the source of truth for every error code.
"""

import json
from collections import defaultdict
from pathlib import Path

import pytest
import pytest_asyncio
from neo4j.exceptions import ClientError

from app.config.settings import Settings
from app.kg.client import make_read_driver, read_query
from app.kg.templates import TEMPLATES, TemplateError, run_template

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module")]
DATA = Path(__file__).resolve().parents[4] / "mock-data" / "data"


def load(rel):
    p = DATA / rel
    if p.suffix == ".jsonl":
        return [json.loads(line) for line in p.open(encoding="utf-8")]
    return json.loads(p.read_text(encoding="utf-8"))


SUMMARY = load("SUMMARY.json")["counts"]
NODES, EDGES = load("kg/nodes.jsonl"), load("kg/edges.jsonl")
ERRORS = load("catalog/error_codes.json")
RES = {r["id"]: r for r in load("catalog/resolutions.json")}
PARTS = {p["part_no"]: p for p in load("catalog/spare_parts.json")}
CENTRES = load("ops/service_centres.json")
NAMES = {n["id"]: n["props"].get("name") for n in NODES}
COVERS = defaultdict(set)
for e in EDGES:
    if e["type"] == "COVERS_PART":
        COVERS[e["dst"]].add(NAMES[e["src"]])


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def kg():
    s = Settings()
    if not (s.neo4j_uri and s.neo4j_password.get_secret_value()):
        pytest.skip("NEO4J_* not configured (run `make env`)")
    driver = make_read_driver(s)
    yield driver, s.neo4j_database
    await driver.close()


async def run(kg, tid, **params):
    driver, db = kg
    return await run_template(tid, params, driver=driver, database=db)


async def q(kg, cypher, **params):
    driver, db = kg
    return (await read_query(driver, cypher, params, database=db))[0]


# ---------- load (FR-KG-01) ----------

@pytest.mark.req("FR-KG-01")
async def test_counts_match_summary_and_source(kg):
    assert (await q(kg, "MATCH (n:Entity) RETURN count(n) AS n"))[0]["n"] == SUMMARY["kg_nodes"]
    assert (await q(kg, "MATCH ()-[r]->() RETURN count(r) AS n"))[0]["n"] == SUMMARY["kg_edges"]
    by_label = {r["label"]: r["n"] for r in await q(
        kg, "MATCH (n:Entity) UNWIND [l IN labels(n) WHERE l <> 'Entity'] AS label RETURN label, count(*) AS n")}
    want = defaultdict(int)
    for n in NODES:
        want[n["label"]] += 1
    assert by_label == dict(want)
    rows = await q(kg, "MATCH (n) WHERE n.source IS NULL OR n.source <> 'seed' RETURN count(n) AS n "
                       "UNION ALL MATCH ()-[r]->() WHERE r.source IS NULL OR r.source <> 'seed' RETURN count(r) AS n")
    assert [r["n"] for r in rows] == [0, 0]


@pytest.mark.req("FR-KG-01")
async def test_constraint_and_indexes_exist(kg):
    cons = {r["name"] for r in await q(kg, "SHOW CONSTRAINTS YIELD name RETURN name")}
    idx = {r["name"] for r in await q(kg, "SHOW INDEXES YIELD name RETURN name")}
    assert "entity_id" in cons
    assert {"entity_fulltext", "errorcode_code", "model_id", "sparepart_id"} <= idx


# ---------- templates (FR-KG-03) ----------

def first_model(ec):
    return ec["applies_to_models"][0]


@pytest.mark.req("FR-KG-03")
async def test_t_diag_matches_catalog_for_every_error_code(kg):
    for ec in ERRORS:
        r = await run(kg, "T-DIAG", model=first_model(ec), code=ec["code"])
        want = sorted(ec["causes"], key=lambda c: (-c["likelihood"], c["cause_id"]))
        assert [(x["cause_id"], x["likelihood"], x["resolution_id"]) for x in r.rows] == \
               [(c["cause_id"], c["likelihood"], c["resolution_id"]) for c in want], ec["id"]
        for row in r.rows:
            assert sorted(row["parts"]) == sorted(RES[row["resolution_id"]]["parts"])
            assert row["error_id"] == ec["id"] and row["safety_stop"] == ec["safety_stop"]
            assert row["path_id"] == f"T-DIAG:{first_model(ec)}>{ec['id']}>{row['cause_id']}>{row['resolution_id']}"


@pytest.mark.req("FR-KG-03")
async def test_t_diag_unknown_model_code_pair_returns_nothing(kg):
    # a code the model cannot raise: no CAN_RAISE edge, so no answer
    assert (await run(kg, "T-DIAG", model="DHD-150", code="E99")).rows == []
    assert (await run(kg, "T-DIAG", model="DHD-150", code="E04' OR 1=1 //")).rows == []   # value, not Cypher


@pytest.mark.req("FR-KG-03")
async def test_t_parts_and_t_cover_match_catalog(kg):
    checked_cover = 0
    for ec in ERRORS:
        fam, model = ec["family"], first_model(ec)
        for c in ec["causes"]:
            r = await run(kg, "T-PARTS", cause=c["cause_id"], family=fam)
            assert [x["part_no"] for x in r.rows] == sorted(RES[c["resolution_id"]]["parts"])
        # T-COVER: every part of every cause tied at the top likelihood among causes that need a part (D-027)
        with_parts = [c for c in ec["causes"] if RES[c["resolution_id"]]["parts"]]
        cover = await run(kg, "T-COVER", model=model, code=ec["code"])
        if not with_parts:
            assert cover.rows == []
            continue
        top = max(c["likelihood"] for c in with_parts)
        want = sorted((c["cause_id"], p) for c in with_parts if c["likelihood"] == top
                      for p in RES[c["resolution_id"]]["parts"])
        assert [(r["cause_id"], r["part_no"]) for r in cover.rows] == want
        for r in cover.rows:
            assert r["covered_by"] == sorted(COVERS[r["part_no"]])
            checked_cover += bool(COVERS[r["part_no"]])
    assert checked_cover > 0   # at least one code exercises a covered part


@pytest.mark.req("FR-KG-03")
async def test_t_stock_lists_home_warehouse_first(kg):
    home = {city.lower(): c["warehouse"] for c in CENTRES for city in c["covers"]}
    for part_no in ("MT-DHD-ROTOR", "MT-MTC-HTR"):
        r = await run(kg, "T-STOCK", part=part_no, city="Pune")
        assert {x["warehouse"]: x["qty"] for x in r.rows} == PARTS[part_no]["stock"]
        assert r.rows[0]["warehouse"] == home["pune"] and r.rows[0]["is_home_warehouse"]
        assert sum(x["is_home_warehouse"] for x in r.rows) == 1
        assert r.rows[0]["lead_time_days"] == PARTS[part_no]["lead_time_days_if_unavailable"]


@pytest.mark.req("FR-KG-03")
async def test_t_shared_and_t_bycause(kg):
    ec = next(e for e in ERRORS if e["id"] == "DHD-E04")
    mine = {c["cause_id"] for c in ec["causes"]}
    want = sorted(o["code"] for o in ERRORS
                  if o["family"] == "DHD" and o["id"] != ec["id"] and mine & {c["cause_id"] for c in o["causes"]})
    assert [x["code"] for x in (await run(kg, "T-SHARED", error_id="DHD-E04", family="DHD")).rows] == want
    want_by = sorted(o["code"] for o in ERRORS
                     if o["family"] == "DHD" and "C-ROTOR-DEG" in {c["cause_id"] for c in o["causes"]})
    assert [x["code"] for x in (await run(kg, "T-BYCAUSE", cause="C-ROTOR-DEG", family="DHD")).rows] == want_by


@pytest.mark.req("FR-KG-03")
async def test_t_sc_every_covered_city_case_insensitive(kg):
    for c in CENTRES:
        for city in c["covers"]:
            rows = (await run(kg, "T-SC", city=city.upper())).rows
            assert [r["id"] for r in rows] == [c["id"]] and rows[0]["home_warehouse"] == c["warehouse"]
    assert (await run(kg, "T-SC", city="Atlantis")).rows == []


@pytest.mark.req("FR-KG-03")
async def test_template_p95_under_50ms_server_side(kg):
    cases = [("T-DIAG", {"model": "DHD-150", "code": "E04"}), ("T-PARTS", {"cause": "C-ROTOR-DEG", "family": "DHD"}),
             ("T-STOCK", {"part": "MT-DHD-ROTOR", "city": "Pune"}), ("T-COVER", {"model": "DHD-150", "code": "E04"}),
             ("T-SHARED", {"error_id": "DHD-E04", "family": "DHD"}),
             ("T-BYCAUSE", {"cause": "C-ROTOR-DEG", "family": "DHD"}), ("T-SC", {"city": "Pune"}), ("T-PART", {"part": "MT-DHD-ROTOR"})]
    assert {t for t, _ in cases} == set(TEMPLATES)
    for tid, p in cases:   # warm the plan cache
        await run(kg, tid, **p)
    for tid, p in cases:
        times = sorted([(await run(kg, tid, **p)).server_ms for _ in range(20)])
        p95 = times[int(0.95 * len(times)) - 1]
        assert p95 < 50, f"{tid} p95 {p95} ms"


# ---------- safety ----------

async def test_parameters_are_validated():
    with pytest.raises(TemplateError, match="unknown template"):
        await run_template("T-NOPE", {})
    with pytest.raises(TemplateError, match="takes"):
        await run_template("T-SC", {"city": "Pune", "extra": "x"})
    with pytest.raises(TemplateError, match="non-empty string"):
        await run_template("T-SC", {"city": ""})


async def test_writes_are_rejected_in_read_transactions(kg):
    with pytest.raises(ClientError):
        await q(kg, "CREATE (n:Entity {id: 'probe-write'}) RETURN n.id AS id")
    assert (await q(kg, "MATCH (n:Entity {id: 'probe-write'}) RETURN count(n) AS n"))[0]["n"] == 0
