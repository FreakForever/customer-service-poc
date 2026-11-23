"""Linker built from the live graph and the Neo4j full-text fallback (BS-09, FR-KG-02) on AuraDB dev."""

import pytest
import pytest_asyncio

from app.config.settings import Settings
from app.kg.client import make_read_driver
from app.kg.linker import Linker, Neo4jFullText, load_graph

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module"), pytest.mark.req("FR-KG-02")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def kg():
    s = Settings()
    if not (s.neo4j_uri and s.neo4j_password.get_secret_value()):
        pytest.skip("NEO4J_* not configured")
    driver = make_read_driver(s)
    yield driver, s.neo4j_database
    await driver.close()


async def test_linker_from_graph_matches_file_based_linker(kg):
    driver, db = kg
    nodes, edges = await load_graph(driver, db)
    assert len(nodes) == 343 and {e["type"] for e in edges} == {"BELONGS_TO", "FOR_FAMILY"}
    linker = Linker(nodes, edges)
    assert [e.id for e in linker.link_dictionary("My DHD-150 is showing DEW PT HIGH").items] == ["DHD-150", "DHD-E04"]


async def test_fulltext_fallback_finds_causes_and_parts(kg):
    driver, db = kg
    nodes, edges = await load_graph(driver, db)
    linker = Linker(nodes, edges)
    ents = await linker.link("desiccant rotor contaminated", fulltext=Neo4jFullText(driver, db))
    assert ents.items and all(e.source == "fulltext" for e in ents.items)
    assert {"C-ROTOR-DEG", "MT-DHD-ROTOR", "CMP-ROTOR"} & {e.id for e in ents.items}
    weird = await linker.link('rotor") OR name:* AND (x', fulltext=Neo4jFullText(driver, db))   # escaped, no error
    assert isinstance(weird.items, list)
