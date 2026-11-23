#!/usr/bin/env python3
"""
Load mock-data/data/kg/{nodes,edges}.jsonl into Neo4j 5.x.

    make kg-load                      # uses NEO4J_* from .env (make env); runs in the agent's uv env
    NEO4J_URI=... NEO4J_USER=... NEO4J_PASSWORD=... [NEO4J_DATABASE=...] python mock-data/kg/load_kg.py [--wipe]

Exits non-zero if the loaded node/relationship counts differ from data/SUMMARY.json (FR-KG-01).

Every node also gets the generic label :Entity with a unique `id`, so runtime ingestion (uploads)
can MERGE on (:Entity {id}) regardless of type. Provenance: `source` = 'seed' for these rows.
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from neo4j import GraphDatabase

DATA = Path(__file__).resolve().parents[1] / "data" / "kg"
SUMMARY = Path(__file__).resolve().parents[1] / "data" / "SUMMARY.json"
LABELS = ["Industry", "ProductFamily", "Model", "Component", "Cause", "ErrorCode", "Resolution", "Warehouse",
          "SparePart", "ServiceCentre", "City", "Document", "AMCPlan"]


def batched(rows, n=500):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def main():
    wipe = "--wipe" in sys.argv
    drv = GraphDatabase.driver(os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                               auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "matsui-demo")))
    nodes = [json.loads(l) for l in (DATA / "nodes.jsonl").open(encoding="utf-8")]
    edges = [json.loads(l) for l in (DATA / "edges.jsonl").open(encoding="utf-8")]
    with drv.session(database=os.environ.get("NEO4J_DATABASE") or None) as s:
        if wipe:
            s.run("MATCH (n) DETACH DELETE n")
        s.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE")
        for lb in LABELS:
            s.run(f"CREATE INDEX {lb.lower()}_name IF NOT EXISTS FOR (n:{lb}) ON (n.name)")
            s.run(f"CREATE INDEX {lb.lower()}_id IF NOT EXISTS FOR (n:{lb}) ON (n.id)")
        s.run("CREATE INDEX errorcode_code IF NOT EXISTS FOR (n:ErrorCode) ON (n.code)")
        s.run("CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.code, n.display_text]")

        by_label = defaultdict(list)
        for n in nodes:
            by_label[n["label"]].append({"id": n["id"], "props": n["props"]})
        for lb, rows in by_label.items():
            assert lb in LABELS, lb
            for chunk in batched(rows):
                s.run(f"UNWIND $rows AS r MERGE (n:Entity {{id: r.id}}) SET n:{lb}, n += r.props, n.source = 'seed'", rows=chunk)

        by_type = defaultdict(list)
        for e in edges:
            by_type[e["type"]].append(e)
        for typ, rows in by_type.items():
            for chunk in batched(rows):
                s.run(f"UNWIND $rows AS r MATCH (a:Entity {{id: r.src}}), (b:Entity {{id: r.dst}}) "
                      f"MERGE (a)-[x:{typ}]->(b) SET x += r.props, x.source = 'seed'", rows=chunk)
        cnt = s.run("MATCH (n:Entity) RETURN count(n) AS n").single()["n"]
        rel = s.run("MATCH ()-[r]->() RETURN count(r) AS r").single()["r"]
    drv.close()
    want = json.loads(SUMMARY.read_text())["counts"]
    ok = (cnt, rel) == (want["kg_nodes"], want["kg_edges"])
    print(f"loaded nodes={cnt} rels={rel} (SUMMARY.json: nodes={want['kg_nodes']} rels={want['kg_edges']})"
          f" {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
