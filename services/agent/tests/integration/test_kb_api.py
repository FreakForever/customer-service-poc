"""Help-centre API on mcs_test (BS-23; FR-UI-01, FR-UI-02)."""

import asyncio

import httpx
import pytest
import pytest_asyncio

from app.api.chat import engine_dep
from tests.e2e.test_chat_api import url  # noqa: F401 - module fixture: rebuilt and seeded mcs_test

pytestmark = pytest.mark.network


@pytest_asyncio.fixture
async def http(url):  # noqa: F811
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.main import create_app
    eng = create_async_engine(url)
    app = create_app()
    app.dependency_overrides[engine_dep] = lambda: eng
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await eng.dispose()


@pytest.mark.req("FR-UI-01")
@pytest.mark.parametrize(("q", "url_"), [
    ("E04 DHD", "/a/DOC-MAN-DHD#e04"), ("LP TRIP", "/a/DOC-MAN-CHL#"), ("warranty rotor", "/a/DOC-POL-WARRANTY-V2"), ("desicc", "/a/DOC-MAN-DHD")])
async def test_search(http, q, url_):
    items = (await http.get("/v1/kb/search", params={"q": q})).json()["items"]
    assert any((i["url"] or "").startswith(url_) for i in items[:6]), items
    assert all("WARRANTY-V1" not in (i["url"] or "") for i in items)


@pytest.mark.parametrize("q", ["", "   ", "'; DROP TABLE x; --", "a & | ! ( :*", "<script>", "ж", "x" * 200])
async def test_search_is_safe_with_odd_input(http, q):
    assert (await http.get("/v1/kb/search", params={"q": q})).status_code == 200


@pytest.mark.req("FR-UI-02")
async def test_article_has_every_citation_anchor(http):
    a = (await http.get("/v1/kb/articles/DOC-MAN-DHD")).json()
    anchors = [s["anchor"] for s in a["sections"]]
    assert len(anchors) == len(set(anchors)) and any(x.startswith("e04-") for x in anchors)
    assert a["document"]["version"] == "1.0" and a["related"] and a["superseded_by"] is None


@pytest.mark.req("FR-UI-02")
async def test_superseded_and_missing_articles(http):
    a = (await http.get("/v1/kb/articles/DOC-POL-WARRANTY-V1")).json()
    assert a["document"]["status"] == "superseded" and a["superseded_by"]["doc_id"] == "DOC-POL-WARRANTY-V2"
    assert (await http.get("/v1/kb/articles/NOPE")).status_code == 404
    assert (await http.get("/v1/kb/articles/DOC-MAN-DHD", params={"version": "9"})).status_code == 404


async def test_home_and_family(http):
    h, f, n = await asyncio.gather(http.get("/v1/kb/home"), http.get("/v1/kb/families/CHL"), http.get("/v1/kb/families/ZZZ"))
    assert len(h.json()["popular"]) == 6 and n.status_code == 404
    f = f.json()
    assert f["models"] and f["parts"] and all(c["anchor"] for c in f["error_codes"])
