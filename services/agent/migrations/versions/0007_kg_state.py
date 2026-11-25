"""Knowledge-graph version: processes reload their dictionaries when it changes (BS-28, D-045).

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE kg_state (id int PRIMARY KEY CHECK (id = 1), version bigint NOT NULL); INSERT INTO kg_state VALUES (1, 0)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kg_state")
