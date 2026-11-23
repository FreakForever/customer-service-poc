"""Idempotent database init run by bootstrap.sh through the Cloud SQL Python connector (no proxy, no psql).

Creates the pgvector extension and a plain (non-cloudsqlsuperuser) read-only role `mcs_ro` in each database.
Env: CLOUDSQL_INSTANCE, DB_PASSWORD (role mcs), DB_RO_PASSWORD. Args: database names.
"""

import os
import sys

from google.cloud.sql.connector import Connector


def init_db(connector: Connector, instance: str, db: str, pw: str, ro_pw: str) -> None:
    conn = connector.connect(instance, "pg8000", user="mcs", password=pw, db=db)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'mcs_ro'")
    verb = "ALTER" if cur.fetchone() else "CREATE"
    # Role passwords can't be bound as parameters; the value is generated hex from Secret Manager.
    assert ro_pw.isalnum(), "DB_RO_PASSWORD must be alphanumeric"
    cur.execute(f"{verb} ROLE mcs_ro LOGIN PASSWORD '{ro_pw}'")
    for stmt in (
        f'GRANT CONNECT ON DATABASE "{db}" TO mcs_ro',
        "GRANT USAGE ON SCHEMA public TO mcs_ro",
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcs_ro",
        "ALTER DEFAULT PRIVILEGES FOR ROLE mcs IN SCHEMA public GRANT SELECT ON TABLES TO mcs_ro",
    ):
        cur.execute(stmt)
    cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    print(f"  {db}: vector {cur.fetchone()[0]}, mcs_ro ok")
    conn.close()


def main() -> None:
    instance, pw, ro_pw = (os.environ[k] for k in ("CLOUDSQL_INSTANCE", "DB_PASSWORD", "DB_RO_PASSWORD"))
    with Connector() as connector:
        for db in sys.argv[1:]:
            init_db(connector, instance, db, pw, ro_pw)


if __name__ == "__main__":
    main()
