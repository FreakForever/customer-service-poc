"""Mail adapter state (Gmail history cursor) (BS-21, D-039).

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE mail_state (key text PRIMARY KEY, value text NOT NULL, updated_at timestamptz DEFAULT now())")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mail_state")
