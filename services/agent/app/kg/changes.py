"""Graph changes from uploads (LLD-13; FR-KB-03, FR-KB-04): apply an approved change request, remove a document's own facts.
The only code that writes to Neo4j at runtime. Labels, relationship types and property names are allow-listed."""

import re
from functools import lru_cache
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.config.settings import get_settings
from app.kg.text2cypher import LABELS, REL_TYPES

OPS = ("add_node", "update_node", "add_edge")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:-]{0,79}")
_RESERVED = {"id", "source", "chunk_id"}


class InvalidChange(ValueError):
    pass


@lru_cache
def get_write_driver() -> AsyncDriver:
    s = get_settings()
    if not s.neo4j_uri:
        raise RuntimeError("NEO4J_URI is not configured")
    return AsyncGraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password.get_secret_value()))


def source_of(doc_id: str) -> str:
    return f"upload:{doc_id}"


def _id(v: Any) -> str:
    if not isinstance(v, str) or not _ID.fullmatch(v):
        raise InvalidChange(f"invalid id {v!r}")
    return v


def _props(v: Any) -> dict[str, Any]:
    out = {}
    for k, x in (v or {}).items():
        if not _KEY.fullmatch(str(k)) or k in _RESERVED or not isinstance(x, str | int | float | bool):
            raise InvalidChange(f"invalid property {k!r}")
        out[k] = x[:1000] if isinstance(x, str) else x
    return out


def statement(op: str, payload: dict[str, Any], doc_id: str, chunk_id: int | None) -> tuple[str, dict[str, Any]]:
    """Validated Cypher for one change; every statement returns n = number of matched targets."""
    p: dict[str, Any] = {"props": _props(payload.get("props")), "source": source_of(doc_id), "chunk": chunk_id}
    if op in ("add_node", "update_node"):
        p["id"] = _id(payload.get("id"))
    if op == "add_node":
        label = payload.get("label")
        if label not in LABELS - {"Entity"}:
            raise InvalidChange(f"unknown label {label!r}")
        # an existing node (seed or another document) keeps its properties and its provenance
        return (f"MERGE (n:Entity {{id: $id}}) ON CREATE SET n:{label}, n += $props, n.source = $source, n.chunk_id = $chunk "
                "RETURN count(n) AS n"), p
    if op == "update_node":
        return "MATCH (n:Entity {id: $id}) SET n += $props RETURN count(n) AS n", p
    if op == "add_edge":
        rel = payload.get("type")
        if rel not in REL_TYPES:
            raise InvalidChange(f"unknown relationship {rel!r}")
        p |= {"src": _id(payload.get("src")), "dst": _id(payload.get("dst"))}
        return (f"MATCH (a:Entity {{id: $src}}), (b:Entity {{id: $dst}}) MERGE (a)-[r:{rel}]->(b) "
                "ON CREATE SET r += $props, r.source = $source, r.chunk_id = $chunk RETURN count(r) AS n"), p
    raise InvalidChange(f"unsupported change {op!r}")


async def apply_changes(driver: AsyncDriver, changes: list[tuple[str, dict[str, Any], int | None]], doc_id: str) -> None:
    """All in one transaction, nodes first."""
    todo = [statement(op, payload, doc_id, chunk) for op, payload, chunk in sorted(changes, key=lambda c: c[0] == "add_edge")]
    if not todo:
        return

    async def work(tx):
        for cypher, params in todo:
            rec = await (await tx.run(cypher, params)).single()
            if not rec or rec["n"] != 1:
                raise InvalidChange(f"a node this change refers to doesn't exist: {params.get('src')} {params.get('dst')}")
    async with driver.session(database=get_settings().neo4j_database or None) as s:
        await s.execute_write(work)


# ---------- CSV uploads (FR-KB-02): curated by an admin, so they also update existing nodes; new ones carry source csv:<job> ----------

_EDGE = "ON CREATE SET x.source = $source"
_ERROR_CODES = [
    "UNWIND $rows AS r MERGE (n:Entity {id: r.id}) ON CREATE SET n:ErrorCode, n.source = $source SET n += r.props, n.updated_by = $source",
    f"UNWIND $rows AS r MATCH (n:ErrorCode {{id: r.id}}), (f:ProductFamily {{id: r.family}}) MERGE (n)-[x:FOR_FAMILY]->(f) {_EDGE}",
    f"UNWIND $rows AS r MATCH (n:ErrorCode {{id: r.id}}), (m:Model)-[:BELONGS_TO]->(:ProductFamily {{id: r.family}}) MERGE (m)-[x:CAN_RAISE]->(n) {_EDGE}",
    "UNWIND $rows AS r MATCH (n:ErrorCode {id: r.id})-[x:CAUSED_BY]->(k) WHERE NOT k.id IN [c IN r.causes | c.cause_id] DELETE x",
    (f"UNWIND $rows AS r UNWIND r.causes AS c MATCH (n:ErrorCode {{id: r.id}}), (k:Cause {{id: c.cause_id}}) MERGE (n)-[x:CAUSED_BY]->(k) {_EDGE} "
    "SET x.likelihood = c.likelihood, x.family = r.family")]
_PARTS = [
    "UNWIND $rows AS r MERGE (n:Entity {id: r.id}) ON CREATE SET n:SparePart, n.source = $source SET n += r.props, n.updated_by = $source",
    "UNWIND $rows AS r MATCH (n:SparePart {id: r.id})-[x:FITS]->(m) WHERE NOT m.id IN r.fits DELETE x",
    f"UNWIND $rows AS r UNWIND r.fits AS f MATCH (n:SparePart {{id: r.id}}), (m:Model {{id: f}}) MERGE (n)-[x:FITS]->(m) {_EDGE}",
    f"UNWIND $rows AS r UNWIND r.stock AS s MATCH (n:SparePart {{id: r.id}}), (w:Warehouse {{id: s.wh}}) MERGE (n)-[x:STOCKED_AT]->(w) {_EDGE} SET x.qty = s.qty",
    f"UNWIND $rows AS r WITH r WHERE r.replaces <> '' MATCH (n:SparePart {{id: r.id}}), (o:SparePart {{id: r.replaces}}) MERGE (n)-[x:REPLACES]->(o) {_EDGE}"]


async def _batch(driver: AsyncDriver, statements: list[str], rows: list[dict[str, Any]], source: str) -> None:
    async def work(tx):
        got = await (await tx.run("MATCH (n:Entity) WHERE n.id IN $ids RETURN n.id AS id, labels(n) AS l", {"ids": [r["id"] for r in rows]})).data()
        label = "ErrorCode" if statements is _ERROR_CODES else "SparePart"
        if wrong := [g["id"] for g in got if label not in g["l"]]:
            raise InvalidChange(f"{wrong[0]} is not a {label} in the graph")
        for q in statements:
            await (await tx.run(q, {"rows": rows, "source": source})).consume()
    async with driver.session(database=get_settings().neo4j_database or None) as s:
        await s.execute_write(work)


async def upsert_error_codes(driver: AsyncDriver, rows: list[dict[str, Any]], source: str) -> None:
    await _batch(driver, _ERROR_CODES, [
        {"id": r["id"], "family": r["family"], "causes": [{k: c[k] for k in ("cause_id", "likelihood")} for c in r["causes"]],
         "props": {"name": f"{r['code']} {r['title']}", **{k: r[k] for k in ("code", "display_text", "severity", "safety_stop")},
                   **({"notes": r["notes"]} if r["notes"] else {})}} for r in rows], source)


async def upsert_parts(driver: AsyncDriver, rows: list[dict[str, Any]], source: str) -> None:
    await _batch(driver, _PARTS, [
        {"id": r["part_no"], "fits": r["fits_models"], "replaces": r["replaces"], "stock": [{"wh": w, "qty": q} for w, q in r["stock"].items()],
         "props": {"name": r["name"], "price_inr": r["price_inr"], "consumable": r["consumable"],
                   "lead_time_days_if_unavailable": r["lead_time_days"]}} for r in rows], source)


async def apply_change(driver: AsyncDriver, op: str, payload: dict[str, Any], doc_id: str, chunk_id: int | None) -> None:
    cypher, params = statement(op, payload, doc_id, chunk_id)

    async def work(tx):
        rec = await (await tx.run(cypher, params)).single()
        if not rec or rec["n"] != 1:
            raise InvalidChange("a node this change refers to doesn't exist")       # rolls the write back
    async with driver.session(database=get_settings().neo4j_database or None) as s:
        await s.execute_write(work)


async def remove_document_facts(driver: AsyncDriver, doc_id: str) -> dict[str, int]:
    """Edges and nodes whose only source is this document. A node something else still links to stays."""
    async def work(tx):
        e = await (await tx.run("MATCH ()-[r]->() WHERE r.source = $s DELETE r RETURN count(r) AS n", {"s": source_of(doc_id)})).single()
        n = await (await tx.run("MATCH (n:Entity) WHERE n.source = $s AND NOT (n)--() DELETE n RETURN count(n) AS n", {"s": source_of(doc_id)})).single()
        return {"edges": e["n"], "nodes": n["n"]}
    async with driver.session(database=get_settings().neo4j_database or None) as s:
        return await s.execute_write(work)
