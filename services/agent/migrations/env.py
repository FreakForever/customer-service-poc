"""Alembic environment: plain-SQL migrations (no ORM metadata), sync psycopg engine."""

from alembic import context
from sqlalchemy import create_engine, pool

from app.config.settings import get_settings


def _url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or get_settings().resolved_database_url()
    if not url:
        raise SystemExit("No database URL: set DATABASE_URL (make env) or pass -x url=...")
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()
