"""Schema and migrations (BS-03, LLD-3) against the `mcs_test` database (DATABASE_URL_TEST, via `make proxy`).

The module rebuilds mcs_test from scratch: downgrade to base, then upgrade to head, so every migration's
downgrade and upgrade both run.
"""

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from app.config.settings import Settings

pytestmark = pytest.mark.network
AGENT_DIR = Path(__file__).resolve().parents[2]

TABLES = {
    "customers", "assets", "amc_contracts", "tickets", "parts_orders", "leads", "models", "error_codes",
    "spare_parts", "kb_documents", "kb_chunks", "faq_entries", "faq_variants", "answer_cache", "jobs",
    "kg_change_requests", "conversations", "messages", "attachments", "mailbox_messages", "feedback", "traces",
    "spans", "eval_runs",
}


def _test_url() -> str:
    url = os.environ.get("DATABASE_URL_TEST") or Settings().database_url_test.get_secret_value()
    if not url:
        pytest.skip("DATABASE_URL_TEST not set (run `make env` and `make proxy`)")
    return url


def _alembic(url: str, *args: str) -> None:
    subprocess.run(["uv", "run", "alembic", "-x", f"url={url}", *args], cwd=AGENT_DIR, check=True,
                   capture_output=True, text=True)


@pytest.fixture(scope="module")
def engine():
    url = _test_url()
    _alembic(url, "downgrade", "base")
    _alembic(url, "upgrade", "head")
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """A connection whose writes are rolled back after the test."""
    with engine.connect() as conn:
        tx = conn.begin()
        yield conn
        tx.rollback()


def scalar(db: Connection, sql: str, **params):
    return db.execute(text(sql), params).scalar()


# ---------- structure ----------

def test_at_head_with_all_tables_and_extensions(db):
    assert scalar(db, "SELECT version_num FROM alembic_version") == "0003"
    tables = {r[0] for r in db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
    assert TABLES <= tables
    exts = {r[0] for r in db.execute(text("SELECT extname FROM pg_extension"))}
    assert {"vector", "pg_trgm"} <= exts


def test_vector_and_fts_indexes(db):
    defs = dict(db.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")).all())
    assert "USING hnsw (embedding vector_cosine_ops)" in defs["kb_chunks_hnsw"]
    assert "USING hnsw (embedding vector_cosine_ops)" in defs["faq_variants_hnsw"]
    assert "USING gin (tsv)" in defs["kb_chunks_tsv"]
    assert "USING gin (source_docs)" in defs["answer_cache_source_docs"]
    assert "UNIQUE" in defs["conversations_thread"] and "UNIQUE" in defs["faq_variants_normalized"]


def test_ticket_sequence_starts_at_141(db):
    assert scalar(db, "SELECT start_value FROM pg_sequences WHERE sequencename = 'ticket_seq'") == 141


def test_constraints_reject_bad_values(engine):
    for sql in ("INSERT INTO tickets (id, priority) VALUES ('T-x', 'P9')",
                "INSERT INTO messages (role, content) VALUES ('bot', 'x')",
                "INSERT INTO kb_documents (doc_id, version, status) VALUES ('d', '1', 'draft')"):
        with engine.connect() as conn, pytest.raises(Exception, match="check constraint"):
            conn.execute(text(sql))


# ---------- knowledge: chunks, FTS, vectors, versioning ----------

def _doc(db, doc_id="warranty-policy", version="v1", status="active", **kw):
    db.execute(text("INSERT INTO kb_documents (doc_id, version, title, status, effective_from, effective_to) "
                    "VALUES (:d, :v, :t, :s, :f, :to)"),
               {"d": doc_id, "v": version, "t": kw.get("title", "Warranty"), "s": status,
                "f": kw.get("effective_from"), "to": kw.get("effective_to")})


def _vec(i: int) -> str:
    v = [0.0] * 768
    v[i] = 1.0
    return json.dumps(v)


def test_chunk_fts_and_cosine_search(db):
    _doc(db)
    for ord_, (heading, content, axis) in enumerate([
        (["Warranty", "Coverage"], "The dryer warranty covers rotor bearings for 24 months.", 0),
        (["AMC"], "Annual maintenance contract visits are scheduled quarterly.", 1),
    ]):
        db.execute(text("INSERT INTO kb_chunks (doc_id, version, ord, heading_path, content, metadata, embedding) "
                        "VALUES ('warranty-policy', 'v1', :o, :h, :c, :m, CAST(:e AS vector))"),
                   {"o": ord_, "h": heading, "c": content, "m": json.dumps({"family": "dryer"}), "e": _vec(axis)})
    hit = scalar(db, "SELECT content FROM kb_chunks WHERE tsv @@ websearch_to_tsquery('english', 'rotor bearing')")
    assert "rotor bearings" in hit
    # heading_path is part of the tsvector too
    assert scalar(db, "SELECT count(*) FROM kb_chunks WHERE tsv @@ to_tsquery('english', 'coverage')") == 1
    nearest = scalar(db, "SELECT ord FROM kb_chunks ORDER BY embedding <=> CAST(:q AS vector) LIMIT 1", q=_vec(1))
    assert nearest == 1
    assert scalar(db, "SELECT count(*) FROM kb_chunks WHERE metadata->>'family' = 'dryer'") == 2


@pytest.mark.req("FR-KB-05")
def test_document_versions_superseded_and_active(db, engine):
    _doc(db, version="v1", status="superseded", effective_from="2024-01-01", effective_to="2025-03-31")
    _doc(db, version="v2", status="active", effective_from="2025-04-01")
    rows = db.execute(text("SELECT version, status, effective_to FROM kb_documents "
                           "WHERE doc_id = 'warranty-policy' ORDER BY version")).all()
    assert [(r.version, r.status) for r in rows] == [("v1", "superseded"), ("v2", "active")]
    assert str(rows[0].effective_to) == "2025-03-31"
    # only one active version per document
    with pytest.raises(Exception, match="kb_documents_one_active"), db.begin_nested():
        _doc(db, version="v3", status="active")


def test_deleting_a_document_version_cascades_to_chunks(db):
    _doc(db, doc_id="d1")
    db.execute(text("INSERT INTO kb_chunks (doc_id, version, ord, content) VALUES ('d1', 'v1', 0, 'x')"))
    db.execute(text("DELETE FROM kb_documents WHERE doc_id = 'd1'"))
    assert scalar(db, "SELECT count(*) FROM kb_chunks WHERE doc_id = 'd1'") == 0


# ---------- conversations (FR-CH-02) ----------

@pytest.mark.req("FR-CH-02")
def test_conversation_state_and_transcript_persist(db):
    cid = scalar(db, "INSERT INTO conversations (channel, external_thread_id, active_agent, state) "
                     "VALUES ('chat', 'sess-1', 'troubleshoot', CAST(:s AS jsonb)) RETURNING id",
                 s=json.dumps({"entities": {"model": "MJ-120", "serial": "MJ120-0042"}, "slots": ["symptom"]}))
    for role, content in [("customer", "MJ-120 shows E07"), ("assistant", "Check the rotor."),
                          ("customer", "and the rotor?")]:
        db.execute(text("INSERT INTO messages (conversation_id, role, content) VALUES (:c, :r, :t)"),
                   {"c": cid, "r": role, "t": content})
    state = scalar(db, "SELECT state FROM conversations WHERE channel = 'chat' AND external_thread_id = 'sess-1'")
    assert state["entities"]["model"] == "MJ-120" and state["slots"] == ["symptom"]
    roles = [r[0] for r in db.execute(text("SELECT role FROM messages WHERE conversation_id = :c "
                                           "ORDER BY created_at, id"), {"c": cid})]
    assert len(roles) == 3 and roles.count("customer") == 2
    with pytest.raises(Exception, match="conversations_thread"), db.begin_nested():
        db.execute(text("INSERT INTO conversations (channel, external_thread_id) VALUES ('chat', 'sess-1')"))


# ---------- retention (NFR-SEC-04) ----------

@pytest.mark.req("NFR-SEC-04")
def test_purge_expired_deletes_only_expired_rows_and_children(db):
    old, fresh = uuid.uuid4(), uuid.uuid4()
    expired = scalar(db, "INSERT INTO conversations (channel, expires_at) VALUES ('chat', now() - interval '1 day') "
                         "RETURNING id")
    live = scalar(db, "INSERT INTO conversations (channel, expires_at) VALUES ('chat', now() + interval '1 day') "
                      "RETURNING id")
    db.execute(text("INSERT INTO messages (conversation_id, role, content) VALUES (:c, 'customer', 'hi')"),
               {"c": expired})
    db.execute(text("INSERT INTO traces (id, created_at) VALUES (:o, now() - interval '31 days'), (:f, now())"),
               {"o": old, "f": fresh})
    db.execute(text("INSERT INTO spans (trace_id, node) VALUES (:o, 'router')"), {"o": old})

    counts = db.execute(text("SELECT * FROM purge_expired()")).one()   # default 30 days
    assert tuple(counts) == (1, 1)
    assert scalar(db, "SELECT count(*) FROM conversations WHERE id IN (:a, :b)", a=expired, b=live) == 1
    assert scalar(db, "SELECT count(*) FROM messages WHERE conversation_id = :c", c=expired) == 0
    assert scalar(db, "SELECT count(*) FROM spans WHERE trace_id = :o", o=old) == 0
    assert scalar(db, "SELECT count(*) FROM traces WHERE id = :f", f=fresh) == 1


@pytest.mark.req("NFR-SEC-04")
def test_purge_retention_comes_from_argument_or_setting(db):
    db.execute(text("INSERT INTO traces (id, created_at) VALUES (:i, now() - interval '10 days')"),
               {"i": uuid.uuid4()})
    assert tuple(db.execute(text("SELECT * FROM purge_expired(30)")).one()) == (0, 0)
    db.execute(text("SET LOCAL mcs.retention_days = '7'"))
    assert tuple(db.execute(text("SELECT * FROM purge_expired()")).one()) == (0, 1)


# ---------- read-only role ----------

def test_read_only_role_can_select_but_not_write(db):
    for table in ("customers", "kb_chunks", "traces", "conversations"):
        assert scalar(db, "SELECT has_table_privilege('mcs_ro', :t, 'SELECT')", t=table)
        assert not scalar(db, "SELECT has_table_privilege('mcs_ro', :t, 'INSERT')", t=table)
        assert not scalar(db, "SELECT has_table_privilege('mcs_ro', :t, 'DELETE')", t=table)
    # tables created later inherit the grant through default privileges
    db.execute(text("CREATE TABLE _ro_probe (x int)"))
    assert scalar(db, "SELECT has_table_privilege('mcs_ro', '_ro_probe', 'SELECT')")
