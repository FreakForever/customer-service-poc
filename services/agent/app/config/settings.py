"""Typed runtime settings (LLD-2). Values come from the environment or a git-ignored .env file.

Secrets are SecretStr so they never appear in repr(), logs or tracebacks (NFR-SEC-02). Locally they come from
`make env` (Secret Manager -> .env); on Cloud Run from --set-secrets.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["vertex", "anthropic", "openai_compat", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore")

    env: str = "dev"

    # Postgres. Locally DATABASE_URL points at the Cloud SQL Auth Proxy (127.0.0.1:5432). On Cloud Run
    # DATABASE_URL is empty and the URL is built from the Cloud SQL unix socket plus DB_PASSWORD.
    database_url: SecretStr = SecretStr("")
    database_url_ro: SecretStr = SecretStr("")
    database_url_test: SecretStr = SecretStr("")
    cloudsql_instance: str = ""
    db_user: str = "mcs"
    db_name: str = "mcs"
    db_password: SecretStr = SecretStr("")

    # Neo4j (AuraDB Free names user and database after the instance id)
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = ""
    neo4j_ro_user: str = ""
    neo4j_ro_password: SecretStr = SecretStr("")

    # LLM / embeddings (OD-002, OD-004: defaults below until the bake-off decides)
    provider: ProviderName = "vertex"
    model_fast: str = "gemini-3.5-flash-lite"     # interim (D-032): served on the Gemini API free tier
    model_strong: str = "gemini-3.5-flash-lite"   # interim until a stronger model is reachable (OD-002, BS-30)
    fallback_provider: ProviderName | Literal[""] = ""  # empty = no cross-provider fallback
    model_fallback: str = ""  # model used on the fallback provider (or on the primary if no fallback provider)
    embed_provider: ProviderName | Literal[""] = ""  # empty = same as provider (anthropic has no embeddings -> vertex)
    embed_model: str = "text-embedding-005"
    embed_dim: int = 768
    embed_timeout_ms: int = 3000
    # Reranker (OD-005 → D-025): local ONNX cross-encoder via fastembed; "none" keeps RRF order
    reranker: Literal["cross_encoder", "none"] = "cross_encoder"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_cache_dir: str = ""  # baked into the image at /opt/models; default: fastembed's temp cache
    rerank_max_length: int = 256  # tokens per (query, passage) pair; latency grows with it
    rerank_top: int = 20

    # Router (LLD-6, D-030)
    knn_sim_ref: float = 0.80
    thinking_budget_fast: int = 0       # Gemini thinking tokens for MODEL_FAST; -1 = model default
    warm_on_startup: bool = True        # build kNN index + rule dictionaries when the API starts

    # Debug API (/v1/debug/run): on in dev, off elsewhere unless set explicitly
    debug_api: bool | None = None
    log_level: str = "INFO"
    demo_personas: bool | None = None   # chat persona picker; default on in dev and demo only

    # Provider credentials and endpoints
    gcp_project: str = ""
    gcp_region: str = "asia-south1"
    vertex_location: str = ""  # embeddings; empty = gcp_region
    vertex_llm_location: str = "global"  # generation endpoint (current Gemini models are served on "global")
    # Gemini generation through the Gemini Developer API (API key, free tier) instead of Vertex — D-032. Embeddings
    # stay on Vertex. Synthetic data only: free-tier prompts may be used to improve Google's products.
    gemini_via: Literal["vertex", "api"] = "vertex"
    gemini_api_key: SecretStr = SecretStr("")
    anthropic_via: Literal["vertex", "api"] = "vertex"
    anthropic_vertex_region: str = "global"
    anthropic_api_key: SecretStr = SecretStr("")
    openai_base_url: str = ""
    openai_api_key: SecretStr = SecretStr("")

    # Channels
    mail_adapter: Literal["demo", "gmail"] = "demo"
    gmail_delegated_user: str = ""
    gmail_pubsub_topic: str = ""
    gmail_push_audience: str = ""
    gmail_push_service_account: str = ""   # identity Pub/Sub signs the push with
    support_address: str = "support@matsui-demo.example"

    # Storage / retention
    gcs_bucket: str = ""
    retention_days: int = 30

    # Staff auth
    nextauth_secret: SecretStr = SecretStr("")
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    staff_allowlist: str = ""

    @property
    def personas_enabled(self) -> bool:
        """Anyone reaching the chat can pick a persona, so this is for synthetic data only (OD-006 → D-038)."""
        return self.demo_personas if self.demo_personas is not None else self.env in ("dev", "demo")

    def resolved_database_url(self) -> str:
        if url := self.database_url.get_secret_value():
            return url
        pw = self.db_password.get_secret_value()
        if self.cloudsql_instance and pw:
            return f"postgresql+psycopg://{self.db_user}:{pw}@/{self.db_name}?host=/cloudsql/{self.cloudsql_instance}"
        return ""

    @property
    def resolved_vertex_location(self) -> str:
        return self.vertex_location or self.gcp_region


@lru_cache
def get_settings() -> Settings:
    return Settings()
