"""Parameterized Cypher templates (LLD-4, FR-KG-03).

Each template returns plain rows plus a `path_id` ("T-DIAG:DHD-150>DHD-E04>C-...>R-...") used for citations.
Values only ever reach Cypher as parameters; templates run in READ transactions (app.kg.client).
The model's family comes from (Model)-[:BELONGS_TO]->(ProductFamily); RESOLVED_BY is family-specific (D-021).
"""

from dataclasses import dataclass, field
from typing import Any

from neo4j import AsyncDriver

from app.kg.client import get_read_driver, read_query


@dataclass(frozen=True)
class Template:
    id: str
    description: str
    params: tuple[str, ...]
    cypher: str


@dataclass
class TemplateResult:
    template: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    server_ms: int = 0

    @property
    def path_ids(self) -> list[str]:
        return [r["path_id"] for r in self.rows]


_TEMPLATES = [
    Template("T-DIAG", "Diagnose an error code on a model: causes by likelihood, resolution and parts",
             ("model", "code"), """
MATCH (m:Model {id: $model})-[:BELONGS_TO]->(f:ProductFamily)
MATCH (m)-[:CAN_RAISE]->(e:ErrorCode {code: $code})-[c:CAUSED_BY]->(k:Cause)-[rb:RESOLVED_BY]->(r:Resolution)
WHERE rb.family = f.id
OPTIONAL MATCH (r)-[:REQUIRES_PART]->(p:SparePart)
WITH m, e, k, c, r, collect(p.id) AS parts
RETURN e.id AS error_id, e.code AS code, e.name AS error, e.severity AS severity, e.safety_stop AS safety_stop,
       k.id AS cause_id, k.name AS cause, c.likelihood AS likelihood, r.id AS resolution_id, r.name AS resolution,
       r.steps AS steps, r.performed_by AS performed_by, parts,
       m.id + '>' + e.id + '>' + k.id + '>' + r.id AS path
ORDER BY likelihood DESC, cause_id"""),
    Template("T-PARTS", "Spare parts needed to resolve a cause in a product family", ("cause", "family"), """
MATCH (k:Cause {id: $cause})-[rb:RESOLVED_BY]->(r:Resolution)-[:REQUIRES_PART]->(p:SparePart)
WHERE rb.family = $family
RETURN p.id AS part_no, p.name AS part, p.price_inr AS price_inr, p.consumable AS consumable,
       r.id AS resolution_id, r.name AS resolution, k.id + '>' + r.id + '>' + p.id AS path
ORDER BY part_no"""),
    Template("T-STOCK", "Part availability by warehouse, home warehouse of the city's service centre first",
             ("part", "city"), """
MATCH (p:SparePart {id: $part})-[s:STOCKED_AT]->(w:Warehouse)
OPTIONAL MATCH (sc:ServiceCentre)-[:COVERS]->(c:City) WHERE toLower(c.name) = toLower($city)
OPTIONAL MATCH (sc)-[:SUPPLIED_BY]->(home:Warehouse)
WITH p, w, s, collect(DISTINCT home.id) AS homes
RETURN p.id AS part_no, p.name AS part, w.id AS warehouse, w.city AS warehouse_city, s.qty AS qty,
       w.id IN homes AS is_home_warehouse, p.lead_time_days_if_unavailable AS lead_time_days,
       p.id + '>' + w.id AS path
ORDER BY is_home_warehouse DESC, qty DESC, warehouse"""),
    Template("T-COVER", "Is the part for the most likely cause (that needs a part) covered by an AMC plan?",
             ("model", "code"), """
MATCH (m:Model {id: $model})-[:BELONGS_TO]->(f:ProductFamily)
MATCH (m)-[:CAN_RAISE]->(e:ErrorCode {code: $code})-[c:CAUSED_BY]->(k:Cause)-[rb:RESOLVED_BY]->(r:Resolution)
      -[:REQUIRES_PART]->(p:SparePart)
WHERE rb.family = f.id
WITH m, e, k, c, r, p ORDER BY c.likelihood DESC, k.id, p.id LIMIT 1
OPTIONAL MATCH (a:AMCPlan)-[:COVERS_PART]->(p)
WITH m, e, k, c, r, p, a ORDER BY a.name
RETURN p.id AS part_no, p.name AS part, k.id AS cause_id, k.name AS cause, c.likelihood AS likelihood,
       collect(a.name) AS covered_by, m.id + '>' + e.id + '>' + k.id + '>' + r.id + '>' + p.id AS path"""),
    Template("T-SHARED", "Error codes in a family that share a cause with a given error code",
             ("error_id", "family"), """
MATCH (e:ErrorCode {id: $error_id})-[:CAUSED_BY]->(k:Cause)<-[:CAUSED_BY]-(o:ErrorCode)
      -[:FOR_FAMILY]->(:ProductFamily {id: $family})
WHERE o <> e
WITH e, o, k ORDER BY k.name
RETURN o.id AS error_id, o.code AS code, o.name AS error, collect(k.name) AS shared_causes,
       e.id + '~' + o.id AS path
ORDER BY code"""),
    Template("T-BYCAUSE", "Error codes of a family caused by a given cause", ("cause", "family"), """
MATCH (k:Cause {id: $cause})<-[c:CAUSED_BY]-(e:ErrorCode)-[:FOR_FAMILY]->(:ProductFamily {id: $family})
RETURN e.id AS error_id, e.code AS code, e.name AS error, c.likelihood AS likelihood, k.id + '<' + e.id AS path
ORDER BY code"""),
    Template("T-SC", "Service centre covering a city, with its home warehouse", ("city",), """
MATCH (sc:ServiceCentre)-[:COVERS]->(c:City) WHERE toLower(c.name) = toLower($city)
OPTIONAL MATCH (sc)-[:SUPPLIED_BY]->(w:Warehouse)
RETURN sc.id AS id, sc.name AS name, sc.city AS city, sc.state AS state, sc.technicians AS technicians,
       w.id AS home_warehouse, sc.id + '>' + c.id AS path
ORDER BY id"""),
]
TEMPLATES: dict[str, Template] = {t.id: t for t in _TEMPLATES}


class TemplateError(ValueError):
    pass


def _check(tid: str, params: dict[str, Any]) -> Template:
    t = TEMPLATES.get(tid)
    if t is None:
        raise TemplateError(f"unknown template {tid!r}; known: {', '.join(TEMPLATES)}")
    if set(params) != set(t.params):
        raise TemplateError(f"{tid} takes {list(t.params)}, got {sorted(params)}")
    for k, v in params.items():
        if not isinstance(v, str) or not v.strip():
            raise TemplateError(f"{tid}: parameter {k!r} must be a non-empty string")
    return t


async def run_template(tid: str, params: dict[str, Any], *, driver: AsyncDriver | None = None,
                       database: str | None = None, timeout_s: float = 5.0) -> TemplateResult:
    t = _check(tid, params)
    if driver is None:
        from app.config.settings import get_settings

        driver, database = get_read_driver(), database or get_settings().neo4j_database
    rows, server_ms = await read_query(driver, t.cypher, {k: v.strip() for k, v in params.items()},
                                       database=database, timeout_s=timeout_s)
    for r in rows:
        r["path_id"] = f"{tid}:{r.pop('path')}"
    return TemplateResult(template=tid, rows=rows, server_ms=server_ms)
