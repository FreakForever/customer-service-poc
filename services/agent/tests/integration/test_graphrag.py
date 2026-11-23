"""Hybrid GraphRAG evidence on the real graph (AuraDB dev) and mcs_test chunks (BS-10, LLD-9, FR-KG-05).

Acceptance: every multi_hop eval item gets at least one KG path, and the manual chunk (via DOCUMENTED_IN) where
the item has a gold document. Fake embeddings for the RAG leg; no model calls.
"""

import asyncio
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings
from app.ingest.seed_kb import seed_kb
from app.kg.client import make_read_driver
from app.kg.linker import Linker, load_graph
from app.providers import ResilientLLM
from app.providers.fake import FakeProvider
from app.retrieval.graphrag import GraphRAGDeps, graph_evidence
from app.retrieval.rerank import NoopReranker

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module"), pytest.mark.req("FR-KG-05")]
AGENT = Path(__file__).resolve().parents[2]
ROOT = AGENT.parents[1]
QS = [json.loads(x) for x in (ROOT / "mock-data" / "data" / "eval" / "questions.jsonl").open()]
MULTI = [q for q in QS if q["type"] == "multi_hop"]
EMB = FakeProvider(dim=768)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def deps():
    s = Settings()
    url = os.environ.get("DATABASE_URL_TEST") or s.database_url_test.get_secret_value()
    if not url or not (s.neo4j_uri and s.neo4j_password.get_secret_value()):
        pytest.skip("DATABASE_URL_TEST / NEO4J_* not configured")
    for args in (("downgrade", "base"), ("upgrade", "head")):
        await asyncio.to_thread(subprocess.run, ["uv", "run", "alembic", "-x", f"url={url}", *args], cwd=AGENT,
                                check=True, capture_output=True, text=True)
    spec = importlib.util.spec_from_file_location("seed_script", ROOT / "scripts" / "seed.py")
    seed_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_mod)
    sync = create_engine(url)
    with sync.begin() as c:
        seed_mod.seed(c)
    sync.dispose()
    engine = create_async_engine(url)
    await seed_kb(engine, EMB)
    driver = make_read_driver(s)
    nodes, edges = await load_graph(driver, s.neo4j_database)
    async with engine.connect() as conn:
        yield GraphRAGDeps(conn=conn, embedder=EMB, reranker=NoopReranker(), kg_driver=driver,
                           kg_database=s.neo4j_database, linker=Linker(nodes, edges))
    await driver.close()
    await engine.dispose()


def covered_nodes(result, deps):
    names = {n["props"].get("name"): nid for nid, n in deps.linker.nodes.items()}
    ids = set()
    for e in result.kg:
        for pid in e.path_ids:
            ids |= set(re.split(r"[>~<]", pid.split(":", 1)[1]))
        ids |= {names[p] for p in e.metadata.get("covered_by", []) if p in names}
        if e.source == "T-COVER":   # a coverage answer settles the plan question even when the answer is "no"
            ids |= {nid for nid, n in deps.linker.nodes.items() if n["label"] == "AMCPlan"}
    return ids


async def test_every_multi_hop_item_gets_kg_paths_and_its_manual_chunk(deps):
    missing_kg, missing_doc, gold_cov = [], [], []
    for q in MULTI:
        ents = deps.linker.link_dictionary(q["question"])
        res = await graph_evidence(q["question"], ents, "hybrid", deps)
        assert not res.errors, (q["id"], res.errors)
        if not res.kg:
            missing_kg.append(q["id"])
        for doc in q.get("gold_docs", []):
            if not any(e.doc_id == doc and e.source == "documented_in" for e in res.chunks):
                missing_doc.append(q["id"])
        gold = {x for x in q["gold_kg_path"] if x in deps.linker.nodes}
        gold_cov.append(len(gold & covered_nodes(res, deps)) / len(gold))
    assert missing_kg == [] and missing_doc == []
    assert sum(gold_cov) / len(gold_cov) == 1.0    # every gold path node appears in the evidence path ids


async def test_dependent_stock_lookup_and_fact_text(deps):
    q = "Our MTC-W90 shows E06. If it's the most likely cause, which spare part do we need and is it available at the Pune warehouse?"
    res = await graph_evidence(q, deps.linker.link_dictionary(q), "hybrid", deps)
    assert [t for t, _ in res.plan] == ["T-DIAG", "T-COVER", "T-STOCK"]
    assert res.plan[-1][1] == {"part": "MT-MTC-DESCALER", "city": "Pune"}
    stock = next(e for e in res.kg if e.source == "T-STOCK")
    assert "home warehouse for Pune" in stock.text and "T-STOCK:MT-MTC-DESCALER>WH-PUN" in stock.path_ids
    diag = [e for e in res.kg if e.source == "T-DIAG"]
    assert diag[0].metadata["cause_id"] == "C-SCALE" and diag[0].text.startswith("MTC-W90 can raise E06")
    assert [e.score for e in diag] == sorted((e.score for e in diag), reverse=True)


async def test_modes_rag_kg_hybrid(deps):
    q = "My DHD-150 is showing DEW PT HIGH. What is the most likely cause and what should we do?"
    ents = deps.linker.link_dictionary(q)
    rag = await graph_evidence(q, ents, "rag", deps)
    kg = await graph_evidence(q, ents, "kg", deps)
    hyb = await graph_evidence(q, ents, "hybrid", deps)
    assert rag.kg == [] and rag.chunks and {e.source for e in rag.chunks} == {"hybrid"}
    assert kg.kg and {e.source for e in kg.chunks} == {"documented_in"}
    assert kg.chunks[0].anchor == "e04-dew-point-high"
    assert hyb.kg and any(e.source == "hybrid" for e in hyb.chunks)
    ids = [e.id for e in hyb.evidence]
    assert len(ids) == len(set(ids))                                   # manual chunk not duplicated
    e04 = next(e for e in hyb.chunks if e.anchor == "e04-dew-point-high")
    assert e04.source == "documented_in"
    assert hyb.evidence.index(hyb.kg[-1]) < hyb.evidence.index(hyb.chunks[0])   # facts before chunks


async def test_text2cypher_fallback_and_failure_tolerance(deps):
    q = "How many error codes does the chiller family have?"
    ents = deps.linker.link_dictionary(q)
    good = GraphRAGDeps(**{**deps.__dict__, "llm": ResilientLLM(FakeProvider(script={"text2cypher.v1": {
        "cypher": "MATCH (e:ErrorCode)-[:FOR_FAMILY]->(:ProductFamily {id: $family}) RETURN count(e) AS error_codes"}})),
        "llm_model": "strong"})
    res = await graph_evidence(q, ents, "hybrid", good)
    assert res.plan == [("text2cypher", {})] and res.kg[0].source == "text2cypher" and "error_codes=" in res.kg[0].text
    bad = GraphRAGDeps(**{**deps.__dict__, "llm": ResilientLLM(FakeProvider(script={"text2cypher.v1": [
        {"cypher": "CREATE (n:Entity) RETURN n"}, {"cypher": "CREATE (n:Entity) RETURN n"}]})), "llm_model": "s"})
    res = await graph_evidence(q, ents, "hybrid", bad)
    assert res.kg == [] and res.chunks and any("text2cypher" in e for e in res.errors)   # RAG still answers
