"""Staff token checks (BS-22; NFR-SEC-01). No database."""

import hashlib
import time

import httpx
import jwt
import pytest
import pytest_asyncio

from app.config.settings import get_settings

SECRET = get_settings().nextauth_secret.get_secret_value() or "unit-secret"
CLAIMS = {"iss": "mcs-web", "aud": "mcs-agent-api", "sub": "a@m.example", "name": "A"}


def token(role, secret=None, prefix="staff-api:", alg="HS256", **over):
    now = int(time.time())
    c = {**CLAIMS, "role": role, "iat": now, "exp": now + 300, **over}
    key = "" if alg == "none" else hashlib.sha256(f"{prefix}{secret or SECRET}".encode()).digest()
    return {"Authorization": "Bearer " + jwt.encode({k: v for k, v in c.items() if v is not None}, key, algorithm=alg)}


@pytest_asyncio.fixture
async def http(monkeypatch):
    from app.api.chat import engine_dep
    from app.main import create_app
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[engine_dep] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        yield c
    get_settings.cache_clear()


@pytest.mark.req("NFR-SEC-01")
@pytest.mark.parametrize(("make", "code"), [          # minted inside the test: tokens live 5 minutes, a full run takes longer
    (lambda: {}, 401), (lambda: {"Authorization": "Bearer x"}, 401), (lambda: token(None), 403), (lambda: token("customer"), 403),
    (lambda: token("agent"), 403), (lambda: token("admin", secret="other"), 401), (lambda: token("admin", prefix="chat-session:"), 401),
    (lambda: token("admin", alg="none"), 401), (lambda: token("admin", exp=1), 401), (lambda: token("admin", exp=None), 401),
    (lambda: token("admin", aud="x"), 401), (lambda: token("admin", iss="x"), 401)])
async def test_admin_route(http, make, code):
    assert (await http.post("/v1/demo/send-sample-emails", headers=make())).status_code == code


@pytest.mark.req("NFR-SEC-01")
async def test_console_route_by_role(http):
    for h, code in (({}, 401), (token(None), 403)):
        assert (await http.get("/v1/console/mailbox", headers=h)).status_code == code
    for role in ("agent", "admin"):
        assert (await http.get("/v1/console/mailbox", headers=token(role))).status_code == 500      # past auth; no database here


async def test_every_staff_route_is_guarded(http):
    from app.main import create_app

    calls = [(m, p) for p, ms in create_app().openapi()["paths"].items() for m in ms
             if p.startswith(("/v1/console", "/v1/admin", "/v1/demo", "/v1/analytics"))]
    assert len(calls) >= 2
    for m, p in calls:
        r = await http.request(m, p.replace("{", "").replace("}", ""))
        assert r.status_code == 401, (m, p, r.status_code)


async def test_no_secret_means_no_access(http, monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", "")
    get_settings.cache_clear()
    h = token("admin")
    assert (await http.get("/v1/console/mailbox", headers=h)).status_code == 401
