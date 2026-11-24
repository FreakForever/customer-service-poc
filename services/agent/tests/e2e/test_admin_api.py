"""Knowledge admin API on mcs_test and the knowledge graph (BS-26; FR-KB-01, FR-KB-04, FR-UI-05)."""

import asyncio
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config.settings import get_settings
from app.db import get_engine
from app.ingest.manage import make_delete_handler
from app.ingest.pipeline import make_ingest_handler
from app.jobs.queue import drain
from app.kg import changes
from app.providers.fake import FakeProvider
from tests.e2e.test_chat_api import Client, one, rows, txt
from tests.integration.test_ingest import SB
from tests.unit.test_staff_auth import SECRET, token

pytestmark = pytest.mark.network


@pytest_asyncio.fixture
async def api(http, monkeypatch, tmp_path):
    for k, v in {"NEXTAUTH_SECRET": SECRET, "GCS_BUCKET": ""}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    changes.get_write_driver.cache_clear()
    monkeypatch.setattr("app.ingest.pipeline.LOCAL_UPLOADS", tmp_path)
    monkeypatch.setattr("app.api.admin.get_embedder", lambda: FakeProvider(dim=768))

    class Api:
        async def call(self, method, path, role="admin", **kw):
            return await http.request(method, f"/v1/admin{path}", headers=token(role), **kw)

        async def get(self, path):
            r = await self.call("GET", path)
            assert r.status_code == 200, r.text
            return r.json()

        async def upload(self, name="note.md", data=b"# T\n\n## S\n\nBody text.\n", **form):
            return await self.call("POST", "/documents", files={"file": (name, data)}, data={"title": "Test note", "doc_type": "bulletin", **form})

        async def work(self):
            e = get_engine()
            i = make_ingest_handler(e, FakeProvider(dim=768))
            return await drain(e, {"ingest_document": i, "reindex_document": i, "delete_document": make_delete_handler(e)})
    yield Api()
    await changes.get_write_driver().close()
    changes.get_write_driver.cache_clear()


async def graph(cypher, **p):
    async with changes.get_write_driver().session(database=get_settings().neo4j_database or None) as s:
        return [r.data() async for r in await s.run(cypher, p)]


@pytest.mark.req("FR-UI-05")
async def test_upload_to_answer_then_delete(api, http):
    name = f"sb-{uuid.uuid4().hex[:8]}.md"
    q = "What is the rotor drive motor stall code ROTOR STALL on the revised rotor bulletin?"
    r = await api.upload(name, SB.read_bytes(), family="DHD")
    assert r.status_code == 202
    doc = r.json()["doc_id"]
    await api.work()
    [j] = [x for x in (await api.get("/jobs"))["items"] if x["id"] == r.json()["job_id"]]
    assert j["status"] == "done" and j["result"]["chunks"] >= 2 and j["doc_id"] == doc
    ev = await (await Client(http).start()).say(q)
    assert any((c["url"] or "").startswith(f"/a/{doc}#") for c in one(ev, "citations")), ev
    # dependent cache entry and provenance-only graph facts
    tag = uuid.uuid4().hex[:8].upper()
    mine, shared = f"TEST-{tag}", f"TEST-S-{tag}"
    drv = changes.get_write_driver()
    try:
        for node in (mine, shared):
            await changes.apply_change(drv, "add_node", {"id": node, "label": "Component", "props": {"name": node}}, doc, None)
        await changes.apply_change(drv, "add_edge", {"src": "DHD-150", "type": "HAS_COMPONENT", "dst": mine}, doc, None)
        await changes.apply_change(drv, "add_edge", {"src": "DHD-150", "type": "HAS_COMPONENT", "dst": shared}, "DOC-OTHER-TEST", None)
        async with get_engine().begin() as c:
            await c.execute(text("INSERT INTO answer_cache (key, answer, source_docs) VALUES (:k, '{}', ARRAY[:d])"), {"k": tag, "d": doc})
        assert (await api.call("DELETE", f"/documents/{doc}")).status_code == 202
        assert not await rows("SELECT 1 FROM answer_cache WHERE key = :k", k=tag)
        ev = await (await Client(http).start()).say(q)                                  # gone before the job has run
        assert doc not in json.dumps(ev)
        await api.work()
        assert not await rows("SELECT 1 FROM kb_chunks WHERE doc_id = :d", d=doc)
        left = {r["id"] for r in await graph("MATCH (n:Entity) WHERE n.id IN $i RETURN n.id AS id", i=[mine, shared, "DHD-150"])}
        assert left == {shared, "DHD-150"}                                             # another document still links to it
    finally:
        await graph("MATCH (n:Entity) WHERE n.id IN $i DETACH DELETE n", i=[mine, shared])
    assert (await api.call("DELETE", f"/documents/{doc}")).status_code == 404
    assert (await http.get(f"/v1/kb/articles/{doc}")).status_code == 404


@pytest.mark.req("FR-KB-04")
async def test_versions_supersede_and_reindex(api):
    name = f"v-{uuid.uuid4().hex[:8]}.md"
    a, b = (await api.upload(name)).json(), (await api.upload(name)).json()
    assert a["doc_id"] == b["doc_id"] and (a["version"], b["version"]) == ("1.0", "2.0")
    other = (await api.upload(f"o-{uuid.uuid4().hex[:8]}.md", supersedes=a["doc_id"])).json()
    await api.work()
    items = {(d["doc_id"], d["version"]): d for d in (await api.get("/documents"))["items"]}
    assert items[(a["doc_id"], "1.0")]["status"] == items[(a["doc_id"], "2.0")]["status"] == "superseded"
    assert items[(other["doc_id"], "1.0")]["status"] == "active" and items[(other["doc_id"], "1.0")]["chunks"] == 1
    assert (await api.call("POST", f"/documents/{a['doc_id']}/reindex")).status_code == 404
    r = await api.call("POST", f"/documents/{other['doc_id']}/reindex")
    await api.work()
    [j] = [x for x in (await api.get("/jobs"))["items"] if x["id"] == r.json()["job_id"]]
    assert (j["kind"], j["status"], j["result"]["chunks"]) == ("reindex_document", "done", 1)


async def test_concurrent_uploads_get_different_versions(api):
    name = f"c-{uuid.uuid4().hex[:8]}.md"
    res = await asyncio.gather(*(api.upload(name) for _ in range(3)))
    assert sorted(r.json()["version"] for r in res) == ["1.0", "2.0", "3.0"]
    assert (await rows("SELECT count(*) FROM kb_documents WHERE doc_id = :d AND status = 'active'", d=res[0].json()["doc_id"]))[0][0] == 1


async def test_delete_during_ingestion_wins(api):
    r = (await api.upload(f"d-{uuid.uuid4().hex[:8]}.md")).json()
    await api.call("DELETE", f"/documents/{r['doc_id']}")
    await api.work()
    assert not await rows("SELECT 1 FROM kb_chunks WHERE doc_id = :d", d=r["doc_id"])


@pytest.mark.parametrize(("name", "data", "form", "code"), [
    ("x.exe", b"MZ", {}, 415), ("x.md", b"  ", {}, 422), ("x.pdf", b"not a pdf", {}, 422), ("x.md", b"a" * (10 * 1024 * 1024 + 1), {}, 413),
    ("x.md", b"# a", {"family": "ZZZ"}, 422), ("x.md", b"# a", {"doc_type": "joke"}, 422), ("x.md", b"# a", {"supersedes": "NOPE"}, 422), ("....md", b"# a", {}, 422)])
async def test_bad_uploads(api, name, data, form, code):
    n = (await rows("SELECT count(*) FROM kb_documents"))[0][0]
    assert (await api.upload(name, data, **form)).status_code == code
    assert (await rows("SELECT count(*) FROM kb_documents"))[0][0] == n


async def test_admin_role_is_needed(api, http):
    for m, p in (("GET", "/jobs"), ("GET", "/documents"), ("DELETE", "/documents/X"), ("GET", "/kg/search?q=x"), ("GET", "/faqs")):
        assert (await api.call(m, p, role="agent")).status_code == 403
        assert (await http.request(m, f"/v1/admin{p}")).status_code == 401


@pytest.mark.req("FR-UI-05")
async def test_faq_edit_reaches_customers_and_withdraw_removes_it(api, http):
    [f] = [x for x in (await api.get("/faqs?q=FAQ-003"))["items"]]
    ask = f["question"]
    assert one(await (await Client(http).start()).say(ask), "done")["tier"] == "T1"
    new = {"question": ask, "answer": "Edited claim answer for customers.", "status": "active"}
    assert (await api.call("PUT", "/faqs/FAQ-003", json=new)).status_code == 200
    assert "Edited claim answer" in txt(await (await Client(http).start()).say(ask))
    assert (await api.call("PUT", "/faqs/FAQ-003", json={**new, "question": "What is the warranty period on Matsui equipment?"})).status_code == 409
    assert (await api.call("PUT", "/faqs/FAQ-003", json={**new, "status": "withdrawn"})).status_code == 200
    assert "Edited claim answer" not in txt(await (await Client(http).start()).say(ask))
    assert (await api.call("PUT", "/faqs/NOPE", json=new)).status_code == 404


async def request(op, payload, doc="DOC-TEST-KG"):
    async with get_engine().begin() as c:
        return (await c.execute(text("INSERT INTO kg_change_requests (doc_id, op, payload, confidence) VALUES (:d, :o, CAST(:p AS jsonb), 0.7) RETURNING id"),
                                {"d": doc, "o": op, "p": json.dumps(payload)})).scalar_one()


@pytest.mark.req("FR-UI-05")
async def test_change_requests(api):
    nid = f"TEST-{uuid.uuid4().hex[:8].upper()}"
    try:
        add, edge, bad, no = [await request(*x) for x in (("add_node", {"id": nid, "label": "Component", "props": {"name": "Test hub"}}),
                              ("add_edge", {"src": nid, "type": "BELONGS_TO", "dst": "DHD"}), ("add_edge", {"src": "NO-SUCH", "type": "FITS", "dst": "DHD-150"}),
                              ("add_node", {"id": nid + "X", "label": "Component"}))]
        ids = {i["id"] for i in (await api.get("/kg/changes"))["items"]}
        assert {str(add), str(edge), str(bad), str(no)} <= ids

        async def decide(i, d):
            return (await api.call("POST", f"/kg/changes/{i}/decision", json={"decision": d})).status_code
        assert sorted(await asyncio.gather(decide(add, "approve"), decide(add, "approve"))) == [200, 409]
        assert [await decide(edge, "approve"), await decide(bad, "approve"), await decide(no, "reject"), await decide(uuid.uuid4(), "reject")] == [200, 422, 200, 404]
        assert (await rows("SELECT status FROM kg_change_requests WHERE id = :i", i=bad))[0][0] == "pending"
        n = await api.get(f"/kg/node/{nid}")
        assert [x["id"] for x in n["nodes"]] == [nid, "DHD"] and n["edges"][0]["source"] == "upload:DOC-TEST-KG"
        # an approved "add" for an existing seed node leaves it as it is
        before = await graph("MATCH (n:Entity {id: 'DHD-150'}) RETURN properties(n) AS p")
        await changes.apply_change(changes.get_write_driver(), "add_node", {"id": "DHD-150", "label": "Model", "props": {"name": "hijack"}}, "DOC-TEST-KG", None)
        assert await graph("MATCH (n:Entity {id: 'DHD-150'}) RETURN properties(n) AS p") == before
    finally:
        await graph("MATCH (n:Entity) WHERE n.id STARTS WITH $i DETACH DELETE n", i=nid)


async def test_kg_explorer(api):
    assert "DHD-150" in {i["id"] for i in (await api.get("/kg/search?q=dhd-150"))["items"]}
    one_, two = await api.get("/kg/node/DHD-150"), await api.get("/kg/node/DHD-150?depth=2")
    ids = {n["id"] for n in one_["nodes"]}
    assert one_["nodes"][0]["id"] == "DHD-150" and all(e["src"] in ids and e["dst"] in ids for e in one_["edges"])
    assert len(two["nodes"]) > len(one_["nodes"]) and len(two["nodes"]) <= 61
    assert (await api.call("GET", "/kg/node/NOPE")).status_code == 404 and (await api.call("GET", "/kg/node/DHD-150?depth=3")).status_code == 422
