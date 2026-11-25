"""Runtime-upload demo (BS-28, FR-KB-02/03): ask the requires_upload eval questions, upload the samples, ask again.

    make demo-upload                     # against the database and graph in .env; needs `make proxy`
    make demo-upload ARGS=--reset        # take the samples out again, so the demo can be repeated

Uses the same code as the admin page and the worker, with the configured models. Budgets are widened: this shows what is known, not speed.
A question passes when the facts of its gold answer are missing before the upload and present after it.
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent"))

from sqlalchemy import text

from app.config.settings import get_settings
from app.db import get_engine
from app.ingest import csv_loaders, manage
from app.ingest.extraction import make_extract_handler
from app.ingest.pipeline import doc_id_from_filename, make_ingest_handler, submit_document
from app.jobs.queue import drain
from app.kg.changes import get_write_driver
from app.kg.client import get_read_driver, read_query
from app.orchestrator import graph as g
from app.orchestrator.state import Envelope
from app.providers import get_embedder, get_llm
from app.rules.context import bump_kg_version, get_rule_context, refresh_rule_context

GOLD = {"Q-180": r"(?=.*rotor drive motor)(?=.*(stall|belt))", "Q-181": r"MT-DHD-ROTOR-R2", "Q-182": r"bearing temperature high",
        "Q-183": r"remote monitoring kit"}
SB, CODES, FAQS = "SB-2026-12_DHD_rotor_revision.md", "new_error_codes.csv", "faq_additions.csv"
DOC = doc_id_from_filename(SB)
IDS, FAQ_IDS = ["RCY-G5", "CCS-L7"], ["FAQ-101", "FAQ-102"]


async def ask(q: str) -> dict[str, Any]:
    final = await g.run_turn(Envelope(channel="chat", text=q))
    r = final["response"]
    return {"tier": r["answered_tier"], "text": r["text"], "intents": [i.name for i in final.get("intents") or []], "path": final.get("path"),
            "failed": final.get("node_error")}


async def ask_all(qs: list[dict[str, Any]], *, want: bool) -> dict[str, dict[str, Any]]:
    out = {}
    for q in qs:
        for attempt in range(3):                    # the free tier refuses or times out now and then
            a = await ask(q["question"])
            a["found"] = re.search(GOLD[q["id"]], a["text"], re.IGNORECASE | re.DOTALL) is not None
            a["attempts"] = attempt + 1
            if a["found"] == want:
                break
            await asyncio.sleep(8)
        out[q["id"]] = a
        await asyncio.sleep(3)
    return out


async def reset() -> dict[str, int]:
    e, s = get_engine(), get_settings()
    assert e is not None
    async with e.begin() as c:
        await manage.delete_document(c, DOC)
        n = (await c.execute(text("DELETE FROM error_codes WHERE id = ANY(:i) AND source LIKE 'csv:%'"), {"i": IDS})).rowcount
        await c.execute(text("DELETE FROM answer_cache WHERE answer->>'faq_id' = ANY(:i)"), {"i": FAQ_IDS})
        f = (await c.execute(text("DELETE FROM faq_entries WHERE id = ANY(:i) AND origin = 'upload'"), {"i": FAQ_IDS})).rowcount
        await bump_kg_version(c)
    async with get_write_driver().session(database=s.neo4j_database or None) as ses:
        await (await ses.run("MATCH (n:ErrorCode) WHERE n.id IN $i AND n.source STARTS WITH 'csv:' DETACH DELETE n", {"i": IDS})).consume()
    await drain(e, {"delete_document": manage.make_delete_handler(e)})
    async with e.begin() as c:                      # no trace of the sample document: the next upload is version 1.0 again
        await c.execute(text("DELETE FROM kg_change_requests WHERE doc_id = :d"), {"d": DOC})
        await c.execute(text("DELETE FROM kb_documents WHERE doc_id = :d AND status = 'deleted'"), {"d": DOC})
        await bump_kg_version(c)
    return {"error_codes": n, "faqs": f}


async def upload(folder: Path) -> None:
    e, s = get_engine(), get_settings()
    assert e is not None
    async with e.begin() as c:
        d = await submit_document(c, filename=SB, data=(folder / SB).read_bytes(), title="Service bulletin SB-2026-12: DHD rotor revision and code E11",
                                  doc_type="bulletin", family="DHD", bucket=s.gcs_bucket)
    handlers = {"ingest_document": make_ingest_handler(e, get_embedder()), "extract_graph": make_extract_handler(e, get_llm)}
    for _ in range(8):                              # waits out retries (5 s, then 10 s)
        await drain(e, handlers)
        async with e.connect() as c:
            jobs = (await c.execute(text("SELECT kind, status, result, error FROM jobs WHERE payload->>'doc_id' = :d AND payload->>'version' = :v "
                                         "AND created_at > now() - interval '15 minutes' ORDER BY created_at"), {"d": DOC, "v": d["version"]})).all()
        if len(jobs) >= 2 and all(j.status in ("done", "failed") for j in jobs) or any(j.status == "failed" for j in jobs):
            break
        await asyncio.sleep(6)
    for j in jobs:
        print(f"  job {j.kind}: {j.status} {j.result or (j.error or '').strip().splitlines()[-1:]}")
    for kind, name in (("error_codes", CODES), ("faqs", FAQS)):
        _, r = await csv_loaders.load_csv(e, get_embedder(), kind, (folder / name).read_bytes(), name)
        print(f"  {name}: applied {r.applied}, rejected {r.rejected}, warnings {r.warnings}")


async def facts() -> dict[str, bool]:
    """FR-KB-02/03 acceptance, straight from the graph."""
    s = get_settings()

    async def q(c: str, **p: Any) -> list[dict[str, Any]]:
        return (await read_query(get_read_driver(), c, p, database=s.neo4j_database))[0]
    nodes = {r["id"] for r in await q("MATCH (n:Entity) WHERE n.id IN $i RETURN n.id AS id", i=["DHD-E11", "MT-DHD-ROTOR-R2", "MT-DHD-RBELT", "MT-DHD-RMOTOR", *IDS])}
    rep = await q("MATCH (a {id: 'MT-DHD-ROTOR-R2'})-[r:REPLACES]->(b {id: 'MT-DHD-ROTOR'}) RETURN r.source AS s, r.chunk_id AS c")
    g5 = {r["k"] for r in await q("MATCH (e {id: 'RCY-G5'})-[:CAUSED_BY]->(k) RETURN k.id AS k")}
    l7 = {r["k"] for r in await q("MATCH (e {id: 'CCS-L7'})-[:CAUSED_BY]->(k) RETURN k.id AS k")}
    diag = await q("MATCH (m:Model)-[:CAN_RAISE]->(:ErrorCode {id: 'DHD-E11'})-[:CAUSED_BY]->(:Cause)-[:RESOLVED_BY]->(:Resolution)-[:REQUIRES_PART]->(p) "
                   "RETURN m.id AS m, p.id AS p")
    return {"nodes": {"DHD-E11", "MT-DHD-ROTOR-R2", "MT-DHD-RBELT", "MT-DHD-RMOTOR"} <= nodes, "replaces with provenance": bool(rep and rep[0]["s"] and rep[0]["c"]),
            "E11 diagnosis reaches both parts": {r["p"] for r in diag} >= {"MT-DHD-RBELT", "MT-DHD-RMOTOR"},
            "G5 and L7 linked to existing causes": g5 == {"C-MOT-OVL", "C-BLADE-DULL"} and l7 == {"C-SOL-STUCK"}}


def widen() -> None:
    import tempfile

    import yaml

    from app.orchestrator.policies import DEFAULT_PATH, load_policies

    raw = yaml.safe_load(DEFAULT_PATH.read_text())
    raw["chat"].update(budget_ms={**raw["chat"]["budget_ms"], "total": 60000},
                       node_timeouts_ms={**raw["chat"].get("node_timeouts_ms", {}), "cache": 8000, "retrieval": 15000, "generate": 30000, "kg": 5000, "tool": 10000,
                                         "router_llm": 15000, "rerank": 5000})
    tmp = Path(tempfile.mkdtemp()) / "policies.yaml"
    tmp.write_text(yaml.safe_dump(raw))
    pols = load_policies(tmp)
    g.get_policy = lambda ch: pols[ch]  # type: ignore[assignment]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path, nargs="?", default=ROOT / "mock-data" / "upload-samples")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    print("removed earlier samples:", await reset())
    if args.reset:
        return 0
    widen()
    await get_rule_context()
    qs = [q for q in map(json.loads, (ROOT / "mock-data" / "data" / "eval" / "questions.jsonl").open()) if q["type"] == "requires_upload"]
    before = await ask_all(qs, want=False)
    print("uploading…")
    await upload(args.folder)
    await refresh_rule_context()
    ok = await facts()
    after = await ask_all(qs, want=True)
    for q in qs:
        b, a = before[q["id"]], after[q["id"]]
        print(f"\n{q['id']} {q['question']}\n  before [{b['tier']}] {b['text'][:200]!r}\n  after  [{a['tier']}, attempt {a['attempts']}] {a['text'][:400]!r}"
              f"\n  {'FLIPPED' if a['found'] and not b['found'] else 'FAILED'} intents {b['intents']} -> {a['intents']}")
    flipped = sum(after[q["id"]]["found"] and not before[q["id"]]["found"] for q in qs)
    print(f"\ngraph: {ok}\n{flipped}/{len(qs)} flipped to correct")
    return 0 if flipped == len(qs) and all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
