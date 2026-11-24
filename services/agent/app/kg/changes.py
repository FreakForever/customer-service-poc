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
