"""Jobs: run_after for exponential backoff between attempts (LLD-13, D-023).

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE jobs ADD COLUMN run_after timestamptz NOT NULL DEFAULT now();
    CREATE INDEX jobs_claim ON jobs (status, run_after) WHERE status = 'queued';
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS jobs_claim; ALTER TABLE jobs DROP COLUMN IF EXISTS run_after;")
