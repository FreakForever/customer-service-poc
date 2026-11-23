"""Ops and observability: jobs, KG change requests, traces/spans, eval runs; read-only role; retention (LLD-3).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE jobs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), kind text, payload jsonb,
      status text DEFAULT 'queued', attempts int DEFAULT 0, error text, result jsonb,
      created_at timestamptz DEFAULT now(), started_at timestamptz, finished_at timestamptz);
    CREATE INDEX jobs_status_created ON jobs (status, created_at);   -- worker: SELECT ... FOR UPDATE SKIP LOCKED
    CREATE TABLE kg_change_requests (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), job_id uuid, doc_id text,
      chunk_id bigint, op text, payload jsonb, confidence real, status text DEFAULT 'pending',
      decided_by text, decided_at timestamptz);

    -- observability
    CREATE TABLE traces (id uuid PRIMARY KEY, conversation_id uuid, channel text, answered_tier text, intents text[],
      confidence real, groundedness real, handoff bool, latency_ms int, first_token_ms int, tokens_in int,
      tokens_out int, cost_usd numeric(10,5), mode text DEFAULT 'live', created_at timestamptz DEFAULT now(),
      detail jsonb);
    CREATE INDEX traces_created ON traces (created_at);
    CREATE TABLE spans (id bigserial PRIMARY KEY, trace_id uuid REFERENCES traces ON DELETE CASCADE, node text,
      started_at timestamptz, duration_ms int, model text, tokens_in int, tokens_out int, cache_hit bool,
      attrs jsonb);
    CREATE INDEX spans_trace ON spans (trace_id);
    CREATE TABLE eval_runs (id uuid PRIMARY KEY, mode text, started_at timestamptz, summary jsonb, report_uri text);

    -- read-only role (D-020): the LOGIN password is set by infra/gcp/db_init.py from Secret Manager,
    -- never in a migration. Create it password-less if bootstrap hasn't run (e.g. CI containers).
    DO $$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mcs_ro') THEN CREATE ROLE mcs_ro NOLOGIN; END IF;
      EXECUTE format('GRANT CONNECT ON DATABASE %I TO mcs_ro', current_database());
    END $$;
    GRANT USAGE ON SCHEMA public TO mcs_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcs_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcs_ro;

    -- retention (NFR-SEC-04, D-020): days = argument, else the mcs.retention_days setting, else 30.
    -- Messages/attachments and spans go with their parents (ON DELETE CASCADE).
    CREATE FUNCTION purge_expired(retention_days int DEFAULT NULL)
      RETURNS TABLE (conversations_deleted int, traces_deleted int)
      LANGUAGE plpgsql AS $$
    DECLARE
      keep_days int := coalesce(retention_days, nullif(current_setting('mcs.retention_days', true), '')::int, 30);
      n_conv int; n_traces int;
    BEGIN
      DELETE FROM conversations c WHERE c.expires_at < now();
      GET DIAGNOSTICS n_conv = ROW_COUNT;
      DELETE FROM traces t WHERE t.created_at < now() - make_interval(days => keep_days);
      GET DIAGNOSTICS n_traces = ROW_COUNT;
      RETURN QUERY SELECT n_conv, n_traces;
    END $$;
    """)


def downgrade() -> None:
    # The cluster-wide mcs_ro role is kept (other databases use it); only this schema's grants go.
    op.execute("""
    DROP FUNCTION IF EXISTS purge_expired(int);
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM mcs_ro;
    DROP TABLE IF EXISTS eval_runs, spans, traces, kg_change_requests, jobs;
    """)
