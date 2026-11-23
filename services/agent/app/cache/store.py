"""Exact answer cache and semantic FAQ cache (LLD-7; FR-CA-01..04).

Exact: `answer_cache(key = sha1(normalized question))` holds approved public answers only.
Semantic: nearest `faq_variants` embedding; accepted at >= tau_cache (chat 0.90, email 0.92) when the turn has no
account entity (serial / ticket / AMC id) and the intent is faq.* (the graph only dispatches faq.* here).
Write guard: anything customer-scoped or volatile (stock, prices at a point in time, account data) is rejected.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.cache.normalize import cache_key, normalize

SERVABLE = ("active", "needs_review")      # needs_review is still served until a person looks (LLD-7 §5)
_ACCOUNT_ID = re.compile(r"\b(SR-\d{4}-\d{5}|AMC-\d{4}-\d{4}|MT-[A-Z0-9]+-\d{2}-\d{5}|CUST-\d{4})\b", re.IGNORECASE)
_VOLATILE = re.compile(r"\b(in stock|out of stock|stock (is|of)|available (at|in) (the )?\w+ warehouse|"
                       r"\d+ (units?|pcs|pieces) (left|available))\b", re.IGNORECASE)


@dataclass
class CacheHit:
    layer: str                         # exact | semantic
    answer: str
    source_docs: list[str]
    faq_id: str | None = None
    similarity: float | None = None
    matched: str | None = None
    server_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class CacheWriteRejected(ValueError):
    pass


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


# ---------- exact ----------

async def exact_lookup(conn: AsyncConnection, question: str) -> CacheHit | None:
    t0 = time.perf_counter()
    row = (await conn.execute(text("""
        UPDATE answer_cache SET hits = hits + 1
        WHERE key = :k AND (expires_at IS NULL OR expires_at > now())
        RETURNING answer, source_docs"""), {"k": cache_key(question)})).first()
    if row is None:
        return None
    a = row.answer or {}
    return CacheHit("exact", a.get("answer", ""), list(row.source_docs or []), faq_id=a.get("faq_id"),
                    matched=a.get("question"), server_ms=(time.perf_counter() - t0) * 1000)


async def write_answer(conn: AsyncConnection, question: str, answer: str, source_docs: list[str], *,
                       faq_id: str | None = None, origin: str = "faq", customer_scoped: bool = False,
                       volatile: bool = False, ttl_days: int | None = None) -> str:
    """The only way into the exact cache (FR-CA-04): public, source-backed, non-volatile answers."""
    if customer_scoped:
        raise CacheWriteRejected("customer-scoped answers are never cached")
    if volatile or _VOLATILE.search(answer):
        raise CacheWriteRejected("answers with real-time stock or point-in-time data are never cached")
    # curated FAQs are human-reviewed public text (FAQ-028 shows an example ticket id); promoted and uploaded
    # answers must not carry account ids, and no cached question may contain one
    if _ACCOUNT_ID.search(question) or (origin != "faq" and _ACCOUNT_ID.search(answer)):
        raise CacheWriteRejected("answer or question mentions an account id")
    if not source_docs:
        raise CacheWriteRejected("cached answers must cite a source document")
    key = cache_key(question)
    await conn.execute(text("""
        INSERT INTO answer_cache (key, answer, source_docs, expires_at)
        VALUES (:k, CAST(:a AS jsonb), :docs, CASE WHEN CAST(:ttl AS int) IS NULL THEN NULL
                                                   ELSE now() + make_interval(days => CAST(:ttl AS int)) END)
        ON CONFLICT (key) DO UPDATE SET answer = EXCLUDED.answer, source_docs = EXCLUDED.source_docs,
                                        expires_at = EXCLUDED.expires_at"""),
        {"k": key, "a": json.dumps({"answer": answer, "faq_id": faq_id, "question": question,
                                    "normalized": normalize(question), "origin": origin}),
         "docs": source_docs, "ttl": ttl_days})
    return key


async def seed_exact_from_faqs(conn: AsyncConnection) -> int:
    """Curated FAQs (question + paraphrases) → exact cache. Idempotent; skips FAQs without a source doc."""
    rows = (await conn.execute(text("""
        SELECT e.id, e.answer, e.source_doc, v.text FROM faq_variants v JOIN faq_entries e ON e.id = v.faq_id
        WHERE e.status = 'active' AND e.source_doc IS NOT NULL"""))).all()
    n = 0
    for r in rows:
        await write_answer(conn, r.text, r.answer, [r.source_doc], faq_id=r.id)
        n += 1
    return n


# ---------- semantic ----------

def semantic_allowed(*, has_account_ids: bool, intent: str | None) -> bool:
    return not has_account_ids and (intent is None or intent.startswith("faq."))


async def semantic_lookup(conn: AsyncConnection, qvec: list[float], threshold: float, *,
                          has_account_ids: bool = False, intent: str | None = None,
                          exclude_texts: set[str] | None = None) -> CacheHit | None:
    if not semantic_allowed(has_account_ids=has_account_ids, intent=intent):
        return None
    t0 = time.perf_counter()
    rows = (await conn.execute(text("""
        SELECT v.faq_id, v.text, e.answer, e.source_doc, e.status,
               1 - (v.embedding <=> CAST(:q AS vector)) AS sim
        FROM faq_variants v JOIN faq_entries e ON e.id = v.faq_id
        WHERE v.embedding IS NOT NULL AND e.status = ANY(:servable)
        ORDER BY v.embedding <=> CAST(:q AS vector) LIMIT 5"""), {"q": _vec(qvec), "servable": list(SERVABLE)})).all()
    for r in rows:
        if exclude_texts and r.text.lower() in exclude_texts:       # eval leave-one-out
            continue
        if r.sim < threshold:
            return None
        return CacheHit("semantic", r.answer, [r.source_doc] if r.source_doc else [], faq_id=r.faq_id,
                        similarity=round(float(r.sim), 4), matched=r.text, server_ms=(time.perf_counter() - t0) * 1000,
                        meta={"status": r.status})
    return None


# ---------- invalidation ----------

async def invalidate_document(conn: AsyncConnection, doc_id: str, change: str) -> dict[str, int]:
    """FR-CA-03: a superseded/deleted/updated source drops dependent exact-cache rows; FAQs citing it go to
    needs_review (still served) — or are withdrawn if the document was deleted."""
    deleted = (await conn.execute(text("DELETE FROM answer_cache WHERE source_docs && ARRAY[CAST(:d AS text)]"),
                                  {"d": doc_id})).rowcount
    status = "withdrawn" if change == "deleted" else "needs_review"
    faqs = (await conn.execute(text("UPDATE faq_entries SET status = :s WHERE source_doc = :d AND status <> :s"),
                               {"d": doc_id, "s": status})).rowcount
    return {"answer_cache_deleted": deleted, "faqs_flagged": faqs}


async def invalidate_faq(conn: AsyncConnection, faq_id: str) -> int:
    """An edited FAQ drops its exact-cache rows (re-seeded from the new text)."""
    return (await conn.execute(text("DELETE FROM answer_cache WHERE answer->>'faq_id' = :f"), {"f": faq_id})).rowcount
