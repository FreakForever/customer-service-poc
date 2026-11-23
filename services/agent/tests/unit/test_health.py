import pytest
from fastapi.testclient import TestClient

from app.config import settings as settings_mod
from app.config.settings import Settings
from app.main import create_app


@pytest.fixture
def unconfigured(monkeypatch):
    for var in ("DATABASE_URL", "CLOUDSQL_INSTANCE", "DB_PASSWORD", "NEO4J_URI", "NEO4J_PASSWORD"):
        monkeypatch.setenv(var, "")
    settings_mod.get_settings.cache_clear()
    yield
    settings_mod.get_settings.cache_clear()


@pytest.mark.req("NFR-DEP-01")
@pytest.mark.parametrize("path", ["/health", "/healthz"])
def test_health_reports_dependency_status(unconfigured, path):
    body = TestClient(create_app()).get(path).json()
    assert body["db"] == "not_configured"
    assert body["neo4j"] == "not_configured"
    assert body["status"] == "degraded"


@pytest.mark.req("NFR-DEP-01")
@pytest.mark.parametrize("path", ["/ready", "/readyz"])
def test_ready_is_503_when_dependencies_missing(unconfigured, path):
    assert TestClient(create_app()).get(path).status_code == 503


def test_cloud_run_socket_url_is_built_from_parts():
    s = Settings(_env_file=None, database_url="", cloudsql_instance="p:asia-south1:mcs-dev", db_password="pw")
    assert s.resolved_database_url() == "postgresql+psycopg://mcs:pw@/mcs?host=/cloudsql/p:asia-south1:mcs-dev"


def test_explicit_database_url_wins():
    s = Settings(_env_file=None, database_url="postgresql+psycopg://a:b@127.0.0.1:5432/mcs", db_password="x")
    assert s.resolved_database_url().endswith("127.0.0.1:5432/mcs")
