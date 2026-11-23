"""Runtime settings (LLD-2). Values come from the environment or a git-ignored .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore")

    env: str = "dev"

    # Postgres. Locally DATABASE_URL points at the Cloud SQL Auth Proxy (127.0.0.1:5432).
    # On Cloud Run DATABASE_URL is left empty and the URL is built from the Cloud SQL unix socket
    # mounted by --add-cloudsql-instances plus DB_PASSWORD from Secret Manager.
    database_url: str = ""
    database_url_ro: str = ""
    cloudsql_instance: str = ""
    db_user: str = "mcs"
    db_name: str = "mcs"
    db_password: str = ""

    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = ""  # AuraDB Free names the database after the instance id; empty = server default

    provider: str = "vertex"
    gcp_project: str = ""
    gcp_region: str = "asia-south1"
    gcs_bucket: str = ""

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if self.cloudsql_instance and self.db_password:
            return (
                f"postgresql+psycopg://{self.db_user}:{self.db_password}@/{self.db_name}"
                f"?host=/cloudsql/{self.cloudsql_instance}"
            )
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
