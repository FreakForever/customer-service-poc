"""Who may call which route (BS-31; NFR-SEC-01). Every route of the API must be listed here: a new route without a decision fails."""

import re
import uuid

import httpx
import pytest
import pytest_asyncio

from app.api.chat import engine_dep
from app.config.settings import get_settings
from tests.unit.test_staff_auth import SECRET, token

PUBLIC = {("GET", "/health"), ("GET", "/ready"), ("GET", "/v1/kb/home"), ("GET", "/v1/kb/families/{family}"), ("GET", "/v1/kb/articles/{doc_id}"),
          ("GET", "/v1/kb/search"), ("POST", "/v1/chat/sessions"), ("POST", "/v1/email/inbound")}
OWN_CHECK = {("POST", "/v1/email/gmail/push")}                                      # Pub/Sub identity token
DEV_ONLY = {("POST", "/v1/debug/run")}
SESSION = {("GET", "/v1/chat/sessions/{sid}"), ("POST", "/v1/chat/sessions/{sid}/messages"), ("POST", "/v1/chat/sessions/{sid}/forms/{form_id}"),
           ("GET", "/v1/chat/sessions/{sid}/events"), ("POST", "/v1/feedback")}
AGENT = ("/v1/console/", "/v1/analytics/")
ADMIN = ("/v1/admin/", "/v1/demo/")


def make(monkeypatch, **env):
    from app.main import create_app
    for k in ("K_SERVICE", "DEBUG_API"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {"NEXTAUTH_SECRET": SECRET, **env}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return create_app()


def routes(app):
    return sorted((m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops if m in ("get", "post", "put", "patch", "delete"))


def url(path):
    path = re.sub(r"\{(sid|cid|mid|id|change_id|message_id|trace_id|conversation_id|tid)\}", str(uuid.uuid4()), path)
    return re.sub(r"\{[^}]+\}", "X", path)


@pytest_asyncio.fixture
async def client(monkeypatch):
    app = make(monkeypatch, ENV="dev")

    class NoDatabase:                    # a request that gets past its checks must not reach a store in this test
        def __getattr__(self, name):
            raise AssertionError("reached the database without permission")
    app.dependency_overrides[engine_dep] = lambda: NoDatabase()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        c.app = app
        yield c
    get_settings.cache_clear()


@pytest.mark.req("NFR-SEC-01")
def test_every_route_has_a_decision(monkeypatch):
    for m, p in routes(make(monkeypatch, ENV="dev")):
        groups = [(m, p) in g for g in (PUBLIC, OWN_CHECK, DEV_ONLY, SESSION)] + [p.startswith(AGENT), p.startswith(ADMIN)]
        assert sum(groups) == 1, f"{m} {p} is not classified"


def of(client, *prefixes):
    return [(m, p) for m, p in routes(client.app) if p.startswith(prefixes)]


@pytest.mark.req("NFR-SEC-01")
async def test_staff_routes_need_a_valid_staff_token(client):
    bad = [{}, {"Authorization": "Bearer x"}, {"Authorization": "Basic YTpi"}, token("agent", exp=1), token("agent", secret="other"),
           token("agent", aud="other"), token("agent", iss="other"), token("agent", alg="none"), token("admin", prefix="chat-session:")]
    for m, p in of(client, *AGENT, *ADMIN):
        for headers in bad:
            assert (await client.request(m, url(p), headers=headers)).status_code == 401, (m, p, headers)
        for role in ("customer", "", None, "Admin"):
            assert (await client.request(m, url(p), headers=token(role))).status_code == 403, (m, p, role)


@pytest.mark.req("NFR-SEC-01")
async def test_agents_cannot_use_admin_routes(client):
    assert len(of(client, *ADMIN)) >= 12
    for m, p in of(client, *ADMIN):
        assert (await client.request(m, url(p), headers=token("agent"))).status_code == 403, (m, p)


async def test_session_routes_need_the_token_of_that_session(client):
    from app.api import session_token
    other = {"Authorization": f"Bearer {session_token.issue(uuid.uuid4(), 3600)}"}
    for m, p in sorted(SESSION):
        sid = str(uuid.uuid4())
        body = {"text": "hi", "fields": {}, "message_id": sid, "rating": 1, "session_id": sid} if m == "POST" else None
        for headers in ({}, other, token("admin"), {"Authorization": f"Bearer {session_token.issue(uuid.UUID(sid), -1)}"}):
            r = await client.request(m, p.replace("{sid}", sid).replace("{form_id}", "service_request"), headers=headers, json=body)
            assert r.status_code == 401, (m, p, r.status_code)


async def test_gmail_push_is_closed_unless_configured(client):
    assert (await client.post("/v1/email/gmail/push", json={})).status_code == 404


@pytest.mark.parametrize(("env", "there"), [({"ENV": "dev"}, True), ({"ENV": "dev", "K_SERVICE": "mcs-agent-api"}, False), ({"ENV": "demo"}, False)])
def test_debug_route_is_never_on_the_internet_by_default(monkeypatch, env, there):
    assert (("POST", "/v1/debug/run") in routes(make(monkeypatch, **env))) is there
    get_settings.cache_clear()
