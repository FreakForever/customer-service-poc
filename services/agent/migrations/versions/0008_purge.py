"""Purge also covers everything else that holds conversation text: job payloads, the demo mailbox, feedback (BS-31, D-047).

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_DAYS = """keep_days int := coalesce(retention_days, nullif(current_setting('mcs.retention_days', true), '')::int, 30);"""


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS purge_expired(int)")
    op.execute(f"""
    CREATE FUNCTION purge_expired(retention_days int DEFAULT NULL)
      RETURNS TABLE (conversations_deleted int, traces_deleted int, jobs_deleted int, mail_deleted int, feedback_deleted int)
      LANGUAGE plpgsql AS $$
    DECLARE
      {_DAYS}
      cutoff timestamptz := now() - make_interval(days => keep_days);
      n1 int; n2 int; n3 int; n4 int; n5 int;
    BEGIN
      IF keep_days < 1 THEN RAISE EXCEPTION 'retention must be at least one day'; END IF;
      DELETE FROM conversations c WHERE c.expires_at < now();
      GET DIAGNOSTICS n1 = ROW_COUNT;
      DELETE FROM traces t WHERE t.created_at < cutoff;
      GET DIAGNOSTICS n2 = ROW_COUNT;
      DELETE FROM jobs j WHERE j.created_at < cutoff AND j.status IN ('done', 'failed');
      GET DIAGNOSTICS n3 = ROW_COUNT;
      DELETE FROM mailbox_messages m WHERE m.created_at < cutoff;
      GET DIAGNOSTICS n4 = ROW_COUNT;
      DELETE FROM feedback f WHERE f.created_at < cutoff;
      GET DIAGNOSTICS n5 = ROW_COUNT;
      RETURN QUERY SELECT n1, n2, n3, n4, n5;
    END $$""")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS purge_expired(int)")
    op.execute(f"""
    CREATE FUNCTION purge_expired(retention_days int DEFAULT NULL)
      RETURNS TABLE (conversations_deleted int, traces_deleted int) LANGUAGE plpgsql AS $$
    DECLARE
      {_DAYS}
      n_conv int; n_traces int;
    BEGIN
      DELETE FROM conversations c WHERE c.expires_at < now();
      GET DIAGNOSTICS n_conv = ROW_COUNT;
      DELETE FROM traces t WHERE t.created_at < now() - make_interval(days => keep_days);
      GET DIAGNOSTICS n_traces = ROW_COUNT;
      RETURN QUERY SELECT n_conv, n_traces;
    END $$""")
