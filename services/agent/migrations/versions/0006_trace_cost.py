"""More decimals for cost per trace: one small-model call costs less than 0.00001 dollars (BS-27, D-044).

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE traces ALTER COLUMN cost_usd TYPE numeric(14,8)")
    op.execute("CREATE INDEX feedback_trace ON feedback (trace_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS feedback_trace")
    op.execute("ALTER TABLE traces ALTER COLUMN cost_usd TYPE numeric(10,5)")
