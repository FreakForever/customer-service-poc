"""CSV uploads and graph extraction end to end on mcs_test and the knowledge graph (BS-28; FR-KB-02, FR-KB-03). The extraction model is
scripted; everything written to the shared graph is removed again by its own source."""

import asyncio
import json
import uuid

import pytest
import pytest_asyncio
from neo4j.exceptions import ServiceUnavailable
from sqlalchemy import text

from app.api import admin
from app.cache.store import exact_lookup
from app.config.settings import get_settings
from app.db import get_engine
from app.ingest import csv_loaders, manage
from app.ingest.extraction import make_extract_handler
from app.ingest.pipeline import make_ingest_handler
from app.jobs.queue import drain
from app.kg import changes
from app.kg.templates import run_template
from app.providers import ResilientLLM
from app.providers.fake import FakeProvider
from app.rules import context
from tests.e2e.conftest import http, url  # noqa: F401 - fixtures
from tests.e2e.test_chat_api import rows
from tests.unit.test_staff_auth import SECRET, token
from tests.unit.test_uploads_unit import DATA, E11, R2, SB

pytestmark = pytest.mark.network
EMB = FakeProvider(dim=768)
SAMPLES = DATA / "upload-samples"
FULL = {"nodes": [E11, R2,
                  {"id": "C-T-RBELT", "label": "Cause", "props": {"name": "Rotor drive belt slipping or broken"}},
                  {"id": "C-T-RMOTOR", "label": "Cause", "props": {"name": "Rotor drive motor failed"}},
                  {"id": "R-DHD-C-T-RBELT", "label": "Resolution", "props": {"steps": "Replace drive belt kit.", "performed_by": "technician", "est_minutes": 30}},
                  {"id": "R-DHD-C-T-RMOTOR", "label": "Resolution", "props": {"steps": "Replace motor.", "performed_by": "technician", "est_minutes": 60}},
                  {"id": "MT-DHD-RBELT", "label": "SparePart", "props": {"name": "Drive belt kit", "price_inr": 1450}},
                  {"id": "MT-DHD-RMOTOR", "label": "SparePart", "props": {"name": "Rotor drive motor", "price_inr": 8900}}],
        "edges": [{"src": a, "type": t, "dst": b, "props": p} for a, t, b, p in (
            ("DHD-100", "CAN_RAISE", "DHD-E11", {}), ("DHD-150", "CAN_RAISE", "DHD-E11", {}), ("DHD-E11", "CAUSED_BY", "C-T-RBELT", {"likelihood": .6}),
            ("DHD-E11", "CAUSED_BY", "C-T-RMOTOR", {"likelihood": .4}), ("C-T-RBELT", "RESOLVED_BY", "R-DHD-C-T-RBELT", {}),
            ("C-T-RMOTOR", "RESOLVED_BY", "R-DHD-C-T-RMOTOR", {}), ("R-DHD-C-T-RBELT", "REQUIRES_PART", "MT-DHD-RBELT", {}),
            ("R-DHD-C-T-RMOTOR", "REQUIRES_PART", "MT-DHD-RMOTOR", {}), ("MT-DHD-ROTOR-R2", "FITS", "DHD-150", {}),
            ("MT-DHD-ROTOR-R2", "REPLACES", "MT-DHD-ROTOR", {"applies_to": "serial numbers from 25-xxxxx onwards"}))], "confidence": 0.95}


async def graph(cypher, **p):
    async with changes.get_write_driver().session(database=get_settings().neo4j_database or None) as s:
        return [r.data() async for r in await s.run(cypher, p)]


@pytest_asyncio.fixture
async def api(http, monkeypatch, tmp_path):  # noqa: F811
    for k, v in {"NEXTAUTH_SECRET": SECRET, "GCS_BUCKET": ""}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    changes.get_write_driver.cache_clear()
    monkeypatch.setattr("app.ingest.pipeline.LOCAL_UPLOADS", tmp_path)
    monkeypatch.setattr(admin, "get_embedder", lambda: EMB)
    sources: list[str] = []

    script = {"extract.v1": lambda m: json.dumps(FULL if "Rotor drive" in m[-1].content.split("SECTION:")[1] else {"nodes": [], "edges": [], "confidence": 1})}

    class Api:
        def __init__(self):
            self.script = script

        async def csv(self, kind, data, role="admin"):
            data = data.encode() if isinstance(data, str) else data
            r = await http.post(f"/v1/admin/csv?type={kind}", headers=token(role), files={"file": ("t.csv", data)})
            if r.status_code == 200:
                sources.append(f"csv:{r.json()['job_id']}")
            return r

        async def call(self, method, path, **kw):
            return await http.request(method, f"/v1/admin{path}", headers=token("admin"), **kw)

        async def upload(self, data=SB, name=None):
            name = name or f"sb-{uuid.uuid4().hex[:8]}.md"
            r = await self.call("POST", "/documents", files={"file": (name, data.encode())}, data={"title": "Rotor bulletin", "doc_type": "bulletin", "family": "DHD"})
            assert r.status_code == 202, r.text
            sources.append(f"upload:{r.json()['doc_id']}")
            return r.json()["doc_id"]

        async def work(self):
            e = get_engine()
            i = make_ingest_handler(e, EMB)
            return await drain(e, {"ingest_document": i, "reindex_document": i, "delete_document": manage.make_delete_handler(e),
                                   "extract_graph": make_extract_handler(e, lambda: ResilientLLM(FakeProvider(dim=768, script=dict(self.script))))})

        async def result(self, doc):
            [r] = await rows("SELECT status, result, error FROM jobs WHERE kind = 'extract_graph' AND payload->>'doc_id' = :d ORDER BY created_at DESC LIMIT 1", d=doc)
            return r

        async def changes(self, doc, status):
            return [c for c in (await self.call("GET", f"/kg/changes?status={status}")).json()["items"] if c["doc_id"] == doc]
    yield Api()
    async with get_engine().begin() as c:                  # a queued retry must not run in a later test: its facts would have no owner
        await c.execute(text("DELETE FROM jobs"))
    await graph("MATCH ()-[r]->() WHERE r.source IN $s DELETE r", s=sources)
    await graph("MATCH (n:Entity) WHERE n.source IN $s DETACH DELETE n", s=sources)
    await changes.get_write_driver().close()
    changes.get_write_driver.cache_clear()


# ---------- CSV ----------

@pytest.mark.req("FR-KB-02")
async def test_error_codes_reach_postgres_the_graph_and_diagnosis(api):
    r = await api.csv("error_codes", (SAMPLES / "new_error_codes.csv").read_bytes())
    assert r.status_code == 200 and r.json()["applied"] == 2 and r.json()["rejected"] == [], r.text
    [g5] = await rows("SELECT code, severity, safety_stop, causes, source FROM error_codes WHERE id = 'RCY-G5'")
    assert g5.source == f"csv:{r.json()['job_id']}" and [(c["cause_id"], c["likelihood"], c["resolution_id"]) for c in g5.causes] == [
        ("C-MOT-OVL", 0.7, "R-RCY-C-MOT-OVL"), ("C-BLADE-DULL", 0.3, "R-RCY-C-BLADE-DULL")]
    for eid, want in (("RCY-G5", {"C-MOT-OVL", "C-BLADE-DULL"}), ("CCS-L7", {"C-SOL-STUCK"})):
        got = await graph("MATCH (e:ErrorCode {id: $i})-[c:CAUSED_BY]->(k:Cause) WHERE k.source = 'seed' RETURN k.id AS k", i=eid)
        assert {x["k"] for x in got} == want
    [m] = (await graph("MATCH (m:Model)-[:BELONGS_TO]->({id: 'RCY'}) RETURN m.id AS id ORDER BY id LIMIT 1"))
    diag = (await run_template("T-DIAG", {"model": m["id"], "code": "G5"})).rows
    assert [d["cause_id"] for d in diag] == ["C-MOT-OVL", "C-BLADE-DULL"] and diag[0]["error"] == "G5 Bearing temperature high"
    jobs = (await api.call("GET", "/jobs")).json()["items"]
    assert next(j for j in jobs if j["id"] == r.json()["job_id"])["result"] == {"type": "error_codes", "applied": 2, "rejected": 0, "warnings": 0}


@pytest.mark.req("FR-KB-02")
async def test_invalid_rows_are_reported_and_valid_ones_applied(api):
    head = "family,code,title,display_text,severity,safety_stop,cause_ids,likelihoods,notes\n"
    r = (await api.csv("error_codes", head + "RCY,Z91,Good one,ZED,info,false,C-MOT-OVL,0.5,\nRCY,Z92,Bad cause,ZED,info,false,C-NOPE,1,\n"
                       "RCY,Z93,Two one,ZED,info,false,C-MOT-OVL,0.5|0.5,\nRCY,Z94,Over one,ZED,info,false,C-MOT-OVL|C-BLADE-DULL,0.9|0.9,\n"
                       "RCY,Z95,No steps,ZED,info,false,C-DPS-FAULT,1,\n")).json()
    assert r["applied"] == 2 and [x["line"] for x in r["rejected"]] == [3, 4, 5] and "unknown cause: C-NOPE" in r["rejected"][0]["error"]
    assert [w["line"] for w in r["warnings"]] == [6]
    assert {x.id for x in await rows("SELECT id FROM error_codes WHERE code LIKE 'Z9_'")} == {"RCY-Z91", "RCY-Z95"}
    again = (await api.csv("error_codes", head + "RCY,Z91,Renamed,ZED,warning,false,C-BLADE-DULL,1,\n")).json()      # upsert
    assert again["applied"] == 1
    [n] = await graph("MATCH (e {id: 'RCY-Z91'})-[:CAUSED_BY]->(k) RETURN e.name AS name, e.severity AS s, collect(k.id) AS k")
    assert n == {"name": "Z91 Renamed", "s": "warning", "k": ["C-BLADE-DULL"]}


@pytest.mark.req("FR-KB-02")
async def test_spare_parts(api):
    head = ",".join(csv_loaders.COLUMNS["spare_parts"]) + ",replaces\n"
    r = (await api.csv("spare_parts", head + "MT-DHD-TZ1,DHD,Test part one,100,false,DHD-100|DHD-150,1,2,3,0,7,MT-DHD-ROTOR\n"
                       "MT-DHD-TZ2,DHD,Test part two,100,false,CHL-10W,0,0,0,0,7,\nMT-CHL-TZ3,DHD,Wrong family,1,no,DHD-100,0,0,0,0,7,\n"
                       "MT-DHD-TZ4,DHD,Unknown old,1,no,DHD-100,0,0,0,0,7,MT-DHD-GHOST\nMT-DHD-TZ5,DHD,Old was rejected,1,no,DHD-100,0,0,0,0,7,MT-DHD-TZ4\n")).json()
    assert r["applied"] == 1 and [x["line"] for x in r["rejected"]] == [3, 4, 5, 6], r
    [p] = await rows("SELECT stock, fits_models, lead_time_days FROM spare_parts WHERE part_no = 'MT-DHD-TZ1'")
    assert p.stock == {"WH-NOI": 1, "WH-PUN": 2, "WH-CHE": 3, "WH-AMD": 0} and p.fits_models == ["DHD-100", "DHD-150"]
    got = (await run_template("T-PART", {"part": "MT-DHD-ROTOR"})).rows
    assert next(x for x in got if x["replaced_by"] == "MT-DHD-TZ1")["replacement_fits"]
    assert sum(s["qty"] for s in (await run_template("T-PART", {"part": "MT-DHD-TZ1"})).rows[0]["stock"]) == 6


@pytest.mark.req("FR-KB-02")
async def test_faqs_are_served_from_the_cache_and_guarded(api):
    r = (await api.csv("faqs", (SAMPLES / "faq_additions.csv").read_bytes())).json()
    assert r["applied"] == 2, r
    async with get_engine().begin() as c:
        hit = await exact_lookup(c, "IoT monitoring?")
    assert hit and hit.faq_id == "FAQ-101" and hit.source_docs == ["UPLOAD-FAQ"]
    from app.orchestrator.nodes.cache import _draft
    assert [x.doc_id for x in _draft(hit).citations] == [None]                    # no broken link
    head = "id,category,question,paraphrases,answer,source_doc\n"
    bad = (await api.csv("faqs", head + "FAQ-001,product,Is this a seed FAQ?,,Yes it is one.,DOC-POL-AMC\n"
                         "FAQ-T02,product,What is the phone number?,,Call +91-90000-000010 now.,UPLOAD-FAQ\nFAQ-T03,product,Where is the source?,,No idea at all.,DOC-GHOST\n"
                         "FAQ-T04,product,iot monitoring,,Second owner of it.,UPLOAD-FAQ\nFAQ-T05,product,Is the rotor in stock?,,It is in stock today.,UPLOAD-FAQ\n"
                         "FAQ-T06,product,Can I get a good one?,,Yes you can get one.,UPLOAD-FAQ\n")).json()
    assert bad["applied"] == 1 and [x["line"] for x in bad["rejected"]] == [2, 3, 4, 5, 6], bad
    assert not await rows("SELECT 1 FROM faq_entries WHERE id = 'FAQ-T05'")       # rejected by the cache guard: nothing left behind
    edit = (await api.csv("faqs", head + "FAQ-101,product,Do you offer remote monitoring for dryers?,,Changed answer text.,UPLOAD-FAQ\n")).json()
    assert edit["applied"] == 1
    async with get_engine().begin() as c:
        assert (await exact_lookup(c, "Do you offer remote monitoring for dryers?")).answer == "Changed answer text."
        assert await exact_lookup(c, "iot monitoring") is None                    # its paraphrase was removed
    twin = (await api.csv("faqs", head + "FAQ-T07,product,You do offer remote monitoring for dryers,,Twin answer here.,UPLOAD-FAQ\n")).json()
    assert "FAQ-101" in twin["warnings"][0]["message"]
    assert [x.status for x in await rows("SELECT status FROM faq_entries WHERE id = 'FAQ-101'")] == ["needs_review"]
    ok = await api.call("PUT", "/faqs/FAQ-101", json={"question": "Do you offer remote monitoring for dryers?", "answer": "Approved again.", "status": "active"})
    assert ok.status_code == 200, ok.text


async def test_csv_access_and_unusable_files(api, http, monkeypatch):  # noqa: F811
    assert (await http.post("/v1/admin/csv?type=faqs", files={"file": ("a.csv", b"x")})).status_code == 401
    assert (await api.csv("faqs", b"x", role="agent")).status_code == 403
    assert (await api.csv("passwords", b"x")).status_code == 422
    assert (await api.csv("faqs", b"a,b\n1,2\n")).status_code == 422
    assert (await api.csv("faqs", b"x" * (2 * 1024 * 1024 + 1))).status_code == 413

    async def down(*a):
        raise ServiceUnavailable("x")
    monkeypatch.setattr(csv_loaders, "upsert_error_codes", down)
    head = "family,code,title,display_text,severity,safety_stop,cause_ids,likelihoods,notes\n"
    assert (await api.csv("error_codes", head + "RCY,Z81,Not kept,ZED,info,false,C-MOT-OVL,0.5,\n")).status_code == 503
    assert not await rows("SELECT 1 FROM error_codes WHERE id = 'RCY-Z81'")       # all or nothing across both stores


# ---------- extraction ----------

@pytest.mark.req("FR-KB-03")
async def test_bulletin_becomes_graph_facts_and_goes_with_the_document(api):
    doc = await api.upload()
    await api.work()
    r = await api.result(doc)
    assert r.status == "done" and r.result["waiting"] == 0 and r.result["dropped"] == 0, (r.result, r.error)
    src = f"upload:{doc}"
    mine = {x["id"] for x in await graph("MATCH (n:Entity) WHERE n.source = $s AND n.chunk_id IS NOT NULL RETURN n.id AS id", s=src)}
    assert {"DHD-E11", "MT-DHD-ROTOR-R2", "MT-DHD-RBELT", "MT-DHD-RMOTOR"} <= mine
    [e] = await graph("MATCH ({id: 'MT-DHD-ROTOR-R2'})-[r:REPLACES]->({id: 'MT-DHD-ROTOR'}) RETURN r.source AS s, r.chunk_id AS c, r.applies_to AS a")
    assert e["s"] == src and e["c"] and "25-xxxxx" in e["a"]
    diag = (await run_template("T-DIAG", {"model": "DHD-150", "code": "E11"})).rows
    assert [(d["likelihood"], d["parts"]) for d in diag] == [(0.6, ["MT-DHD-RBELT"]), (0.4, ["MT-DHD-RMOTOR"])]
    applied = await api.changes(doc, "applied")
    assert applied and all(c["decided_by"] == "auto" for c in applied)
    assert (await api.call("POST", f"/documents/{doc}/reindex")).status_code == 202     # again: nothing new, nothing doubled
    await api.work()
    assert (await api.result(doc)).result["applied"] == 0
    assert (await api.call("DELETE", f"/documents/{doc}")).status_code == 202
    await api.work()
    assert not await graph("MATCH (n:Entity) WHERE n.source = $s RETURN n.id AS id", s=src)
    assert (await run_template("T-DIAG", {"model": "DHD-150", "code": "E11"})).rows == []
    assert (await graph("MATCH (n {id: 'MT-DHD-ROTOR'}) RETURN n.source AS s")) == [{"s": "seed"}]


@pytest.mark.req("FR-KB-03")
async def test_low_confidence_waits_for_approval_and_a_no_is_remembered(api):
    api.script = {"extract.v1": lambda m: json.dumps({"nodes": [E11], "edges": [{"src": "DHD-150", "type": "CAN_RAISE", "dst": "DHD-E11"}], "confidence": 0.5})}
    doc = await api.upload()
    await api.work()
    pend = await api.changes(doc, "pending")
    assert {c["op"] for c in pend} == {"add_node", "add_edge"} and not await graph("MATCH (n {id: 'DHD-E11', source: $s}) RETURN n.id AS id", s=f"upload:{doc}")
    node = next(c for c in pend if c["op"] == "add_node")
    assert node["payload"]["label"] == "ErrorCode" and node["chunk_id"]
    assert (await api.call("POST", f"/kg/changes/{node['id']}/decision", json={"decision": "reject"})).status_code == 200
    await api.call("POST", f"/documents/{doc}/reindex")
    await api.work()
    assert await api.changes(doc, "pending") == []                                   # neither the node nor edges to it come back


async def test_model_failure_keeps_the_document_and_writes_nothing(api):
    from app.providers import ProviderUnavailable
    api.script = {"extract.v1": ProviderUnavailable("down", [])}
    doc = await api.upload()
    await api.work()
    r = await api.result(doc)
    assert r.status == "queued" and "ProviderUnavailable" in r.error                # retried later by the queue
    assert await rows("SELECT 1 FROM kb_chunks WHERE doc_id = :d", d=doc) and not await api.changes(doc, "")


async def test_delete_before_extraction_wins(api):
    doc = await api.upload()
    e = get_engine()
    await drain(e, {"ingest_document": make_ingest_handler(e, EMB)})
    await api.call("DELETE", f"/documents/{doc}")
    await api.work()
    assert (await api.result(doc)).result["skipped"] == "deleted"
    assert not await graph("MATCH (n:Entity) WHERE n.source = $s RETURN n.id AS id", s=f"upload:{doc}")


async def test_other_processes_pick_up_a_new_code_without_a_restart(api, monkeypatch):
    context.set_rule_context(None)
    try:
        ctx = await context.get_rule_context()
        assert "RCY-Z71" not in ctx.linker.nodes
        async def elsewhere():                                                       # as if another process had handled the upload
            return None
        monkeypatch.setattr(admin, "refresh_rule_context", elsewhere)
        head = "family,code,title,display_text,severity,safety_stop,cause_ids,likelihoods,notes\n"
        assert (await api.csv("error_codes", head + "RCY,Z71,New here,ZED ONE,info,false,C-MOT-OVL,0.5,\n")).json()["applied"] == 1
        monkeypatch.setattr(context, "CHECK_S", 0.0)
        for _ in range(40):
            if "RCY-Z71" in (await context.get_rule_context()).linker.nodes:
                break
            await asyncio.sleep(0.25)
        assert (await context.get_rule_context()).linker.link_dictionary("What does Z71 mean on a granulator?").error_codes == ["RCY-Z71"]
        if context._task:
            await context._task
    finally:
        context.set_rule_context(None)


async def test_version_is_bumped_only_when_the_graph_changed(api):
    async def v():
        async with get_engine().connect() as c:
            return (await c.execute(text("SELECT version FROM kg_state"))).scalar_one()
    a = await v()
    await api.csv("faqs", "id,category,question,paraphrases,answer,source_doc\nFAQ-T20,product,Is the version bumped?,,It is not bumped.,UPLOAD-FAQ\n")
    assert await v() == a
