"""Retention purge and database roles on mcs_test (BS-31; NFR-SEC-03, NFR-SEC-04)."""

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings
from app.jobs.purge import make_purge_handler, schedule_purge
from app.jobs.queue import drain, enqueue
from tests.integration.test_schema import _alembic, _test_url

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def url():
    u = _test_url()
    _alembic(u, "downgrade", "base")
    _alembic(u, "upgrade", "head")
    return u


@pytest_asyncio.fixture
async def engine(url):
    e = create_async_engine(url)
    async with e.begin() as c:
        await c.execute(text("DELETE FROM jobs"))
    yield e
    await e.dispose()


async def fill(engine, days):
    ids = [uuid.uuid4() for _ in range(2)]
    async with engine.begin() as c:
        p = {"d": days, "conv": ids[0], "tr": ids[1], "m": f"<{ids[0]}>"}
        for sql in ("INSERT INTO conversations (id, channel, expires_at) VALUES (:conv, 'chat', now() + interval '30 days' - make_interval(days => :d))",
                    "INSERT INTO messages (conversation_id, role, content) VALUES (:conv, 'customer', 'secret text')",
                    "INSERT INTO traces (id, conversation_id, created_at) VALUES (:tr, :conv, now() - make_interval(days => :d))",
                    "INSERT INTO spans (trace_id, node) VALUES (:tr, 'x')",
                    "INSERT INTO feedback (trace_id, rating, created_at) VALUES (:tr, 1, now() - make_interval(days => :d))",
                    "INSERT INTO mailbox_messages (box, message_id, created_at) VALUES ('inbox', :m, now() - make_interval(days => :d))",
                    "INSERT INTO jobs (kind, status, payload, created_at) VALUES ('email_reply', 'done', '{}', now() - make_interval(days => :d))",
                    "INSERT INTO jobs (kind, status, payload, created_at) VALUES ('email_reply', 'queued', '{}', now() - make_interval(days => :d))"):
            await c.execute(text(sql), p)


COUNTS = {"conversations": 1, "messages": 1, "traces": 1, "spans": 1, "feedback": 1, "mailbox_messages": 1}


async def counts(engine):
    async with engine.connect() as c:
        return {t: (await c.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one() for t in COUNTS}


@pytest.mark.req("NFR-SEC-04")
async def test_the_job_deletes_expired_rows_everywhere_and_keeps_the_rest(engine):
    await fill(engine, 31)
    await fill(engine, 29)
    assert await schedule_purge(engine)
    assert await drain(engine, {"purge": make_purge_handler(engine, 30)}) == 1
    assert await counts(engine) == COUNTS                                            # only the 29-day rows are left
    async with engine.connect() as c:
        [r] = (await c.execute(text("SELECT status, result FROM jobs WHERE kind = 'purge'"))).all()
        left = (await c.execute(text("SELECT status, count(*) FROM jobs WHERE kind = 'email_reply' GROUP BY 1 ORDER BY 1"))).all()
    assert r.status == "done" and r.result == {"conversations_deleted": 1, "traces_deleted": 1, "jobs_deleted": 1, "mail_deleted": 1, "feedback_deleted": 1}
    assert [tuple(x) for x in left] == [("done", 1), ("queued", 2)]                    # work that hasn't run is never dropped


@pytest.mark.req("NFR-SEC-04")
async def test_retention_is_configurable_but_never_zero(engine):
    await fill(engine, 10)
    await fill(engine, 5)
    async with engine.begin() as c:
        await enqueue(c, "purge", {})
    await drain(engine, {"purge": make_purge_handler(engine, 7)})
    assert (await counts(engine))["traces"] == 1                                     # only the 5-day one
    async with engine.connect() as c:
        with pytest.raises(DBAPIError):
            await c.execute(text("SELECT * FROM purge_expired(0)"))


@pytest.mark.req("NFR-SEC-04")
async def test_it_is_scheduled_once_a_day_even_with_several_workers(engine):
    assert sorted(await asyncio.gather(*(schedule_purge(engine) for _ in range(4)))) == [False, False, False, True]
    async with engine.begin() as c:
        await c.execute(text("UPDATE jobs SET created_at = now() - interval '25 hours', status = 'done' WHERE kind = 'purge'"))
    assert await schedule_purge(engine) and not await schedule_purge(engine)


@pytest.mark.req("NFR-SEC-03")
def test_read_only_role_has_no_write_privilege_on_any_table(url):
    e = create_engine(url)
    with e.connect() as c:
        tables = c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars().all()
        assert len(tables) > 25
        for t in tables:
            for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not c.execute(text("SELECT has_table_privilege('mcs_ro', :t, :p)"), {"t": f'public."{t}"', "p": priv}).scalar(), (t, priv)
        assert not c.execute(text("SELECT has_schema_privilege('mcs_ro', 'public', 'CREATE')")).scalar()
        assert not c.execute(text("SELECT bool_or(rolsuper OR rolcreaterole OR rolcreatedb) FROM pg_roles WHERE rolname = 'mcs_ro'")).scalar()
        seqs = c.execute(text("SELECT count(*) FROM pg_sequences WHERE schemaname = 'public' AND has_sequence_privilege('mcs_ro', "
                              "quote_ident(schemaname) || '.' || quote_ident(sequencename), 'USAGE')")).scalar()
        assert seqs == 0
    e.dispose()


@pytest.mark.req("NFR-SEC-03")
def test_read_only_login_cannot_write(url):
    ro = os.environ.get("DATABASE_URL_RO") or Settings().database_url_ro.get_secret_value()
    if not ro:
        pytest.skip("DATABASE_URL_RO not set (CI has no read-only login)")
    e = create_engine(make_url(ro).set(database=make_url(url).database))
    with e.connect() as c:
        assert c.execute(text("SELECT count(*) FROM customers")).scalar() is not None
    for sql in ("INSERT INTO feedback (rating) VALUES (1)", "DELETE FROM feedback", "CREATE TABLE x (i int)", "SELECT nextval('ticket_seq')",
                "SELECT * FROM purge_expired(30)"):
        with e.connect() as c, pytest.raises(DBAPIError, match="permission denied"):
            c.execute(text(sql)).all()
    e.dispose()
