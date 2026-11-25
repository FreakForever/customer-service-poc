"""Structured CSV uploads (LLD-13, FR-KB-02): error codes, spare parts, FAQs. Valid rows are applied, invalid ones reported with their line."""

import csv
import io
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.cache.normalize import normalize
from app.cache.store import CURATED, CacheWriteRejected, _vec, invalidate_faq, write_answer
from app.config.settings import get_settings
from app.kg.changes import get_write_driver, upsert_error_codes, upsert_parts
from app.kg.client import get_read_driver, read_query
from app.providers.base import Embedder
from app.rules.context import bump_kg_version
from app.rules.pii import mask_pii

MAX_ROWS = 2000
SIMILAR = 0.92
CsvType = Literal["error_codes", "spare_parts", "faqs"]
Family = Literal["MTC", "DHD", "HAD", "CHL", "MDH", "CCS", "RCY"]
WAREHOUSES = ("WH-NOI", "WH-PUN", "WH-CHE", "WH-AMD")


class BadFile(ValueError):
    pass


def _bool(v: Any) -> Any:
    s = str(v).strip().lower()
    return True if s in ("true", "yes", "1") else False if s in ("false", "no", "0") else v


def _pipe(v: Any) -> Any:
    return [x.strip() for x in v.split("|") if x.strip()] if isinstance(v, str) else v


Bool = Annotated[bool, BeforeValidator(_bool)]
Ids = Annotated[list[str], BeforeValidator(_pipe)]
Floats = Annotated[list[Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]], BeforeValidator(_pipe)]
Qty = Annotated[int, Field(ge=0, le=100_000)]


class ErrorCodeRow(BaseModel):
    family: Family
    code: str = Field(pattern=r"^[A-Z]{1,3}\d{1,3}$")
    title: str = Field(min_length=3, max_length=120)
    display_text: str = Field(min_length=1, max_length=30)
    severity: Literal["info", "warning", "critical"]
    safety_stop: Bool
    cause_ids: Ids = Field(min_length=1, max_length=10)
    likelihoods: Floats
    notes: str = Field(default="", max_length=500)

    @property
    def key(self) -> str:
        return f"{self.family}-{self.code}"


class SparePartRow(BaseModel):
    part_no: str = Field(pattern=r"^MT-[A-Z]{3}-[A-Z0-9]+(-[A-Z0-9]+)*$", max_length=40)
    family: Family
    name: str = Field(min_length=3, max_length=120)
    price_inr: int = Field(ge=0, le=100_000_000)
    consumable: Bool
    fits_models: Ids = Field(min_length=1, max_length=50)
    stock: dict[str, Qty]
    lead_time_days: int = Field(ge=0, le=365)
    replaces: str = ""

    @property
    def key(self) -> str:
        return self.part_no


class FaqRow(BaseModel):
    id: str = Field(pattern=r"^FAQ-[A-Z0-9-]{1,20}$")
    category: str = Field(pattern=r"^[a-z_]{2,30}$")
    question: str = Field(min_length=5, max_length=300)
    paraphrases: Ids = Field(max_length=20)
    answer: str = Field(min_length=5, max_length=4000)
    source_doc: str = Field(min_length=3, max_length=100)

    @property
    def key(self) -> str:
        return self.id


SCHEMAS: dict[str, type[ErrorCodeRow] | type[SparePartRow] | type[FaqRow]] = {"error_codes": ErrorCodeRow, "spare_parts": SparePartRow, "faqs": FaqRow}
COLUMNS = {"error_codes": ["family", "code", "title", "display_text", "severity", "safety_stop", "cause_ids", "likelihoods", "notes"],
           "spare_parts": ["part_no", "family", "name", "price_inr", "consumable", "fits_models", *(f"stock_{w}" for w in WAREHOUSES), "lead_time_days"],
           "faqs": ["id", "category", "question", "paraphrases", "answer", "source_doc"]}


@dataclass
class Result:
    applied: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def reject(self, line: int, error: str) -> None:
        self.rejected.append({"line": line, "error": error})

    def warn(self, line: int, message: str) -> None:
        self.warnings.append({"line": line, "message": message})


def parse(kind: CsvType, data: bytes, res: Result) -> list[tuple[int, Any]]:
    """(line, row) for every row that fits the schema. BadFile when the file as a whole can't be used."""
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BadFile("the file isn't UTF-8 text") from exc
    if "\x00" in content:
        raise BadFile("the file isn't a CSV file")
    reader = csv.reader(io.StringIO(content, newline=""), strict=True)
    out: list[tuple[int, Any]] = []
    seen: dict[str, int] = {}
    try:
        head = [h.strip() for h in next(reader, [])]
        missing = [c for c in COLUMNS[kind] if c not in head]
        if missing:
            raise BadFile(f"missing columns: {', '.join(missing)}")
        if len(set(head)) != len(head):
            raise BadFile("a column appears twice")
        line = reader.line_num + 1
        for n, cells in enumerate(reader):
            here, line = line, reader.line_num + 1
            if not any(c.strip() for c in cells):
                continue
            if n >= MAX_ROWS:
                raise BadFile(f"more than {MAX_ROWS} rows")
            if len(cells) != len(head):
                res.reject(here, f"{len(cells)} values for {len(head)} columns")
                continue
            raw: dict[str, Any] = {k: v.strip() for k, v in zip(head, cells, strict=True)}
            if kind == "spare_parts":
                raw["stock"] = {w: raw[f"stock_{w}"] for w in WAREHOUSES}
            try:
                row = SCHEMAS[kind].model_validate(raw)
            except ValidationError as exc:
                res.reject(here, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:4]))
                continue
            if row.key in seen:
                res.reject(here, f"{row.key} is already on line {seen[row.key]}")
                continue
            seen[row.key] = here
            out.append((here, row))
    except csv.Error as exc:
        raise BadFile(f"line {reader.line_num}: {exc}") from exc
    if not out and not res.rejected:
        raise BadFile("the file has no rows")
    return out


async def load_csv(engine: AsyncEngine, embedder: Embedder, kind: CsvType, data: bytes, filename: str) -> tuple[str, Result]:
    """Validate and apply. Postgres and Neo4j are written inside one Postgres transaction: if the graph write fails, nothing is kept; the
    graph writes are repeatable, so uploading the file again is always safe. Recorded as an `ingest_csv` job."""
    res, job = Result(), uuid.uuid4()
    rows = parse(kind, data, res)
    source = f"csv:{job}"
    vectors: dict[str, list[float]] = {}
    if kind == "faqs" and rows:
        texts = list(dict.fromkeys(t for _, r in rows for t in (r.question, *r.paraphrases)))
        for i in range(0, len(texts), 64):
            vectors |= dict(zip(texts[i:i + 64], await embedder.embed(texts[i:i + 64], kind="query"), strict=True))
    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('csv'))"))             # one upload at a time
        if kind == "error_codes":
            await _error_codes(conn, rows, source, res)
        elif kind == "spare_parts":
            await _spare_parts(conn, rows, source, res)
        else:
            await _faqs(conn, rows, vectors, res)
        if kind != "faqs" and res.applied:
            await bump_kg_version(conn)
        res.rejected.sort(key=lambda r: r["line"])
        await conn.execute(text("INSERT INTO jobs (id, kind, payload, status, attempts, started_at, finished_at, result) VALUES (:i, 'ingest_csv', "
                                "CAST(:p AS jsonb), 'done', 1, now(), now(), CAST(:r AS jsonb))"),
                           {"i": job, "p": json.dumps({"doc_id": filename, "type": kind}),
                            "r": json.dumps({"type": kind, "applied": res.applied, "rejected": len(res.rejected), "warnings": len(res.warnings)})})
    return str(job), res


async def _kg(cypher: str, **p: Any) -> list[dict[str, Any]]:
    return (await read_query(get_read_driver(), cypher, p, database=get_settings().neo4j_database))[0]


async def _error_codes(conn: AsyncConnection, rows: list[tuple[int, ErrorCodeRow]], source: str, res: Result) -> None:
    causes = {r["id"]: r["fams"] for r in await _kg("MATCH (k:Cause) OPTIONAL MATCH (k)-[rb:RESOLVED_BY]->(r:Resolution) WITH k, rb, r "
                                                    "RETURN k.id AS id, [x IN collect([rb.family, r.id]) WHERE x[1] IS NOT NULL] AS fams")}
    good: list[dict[str, Any]] = []
    for line, r in rows:
        unknown = [c for c in r.cause_ids if c not in causes]
        if unknown:
            res.reject(line, f"unknown cause: {', '.join(unknown)}")
        elif len(set(r.cause_ids)) != len(r.cause_ids):
            res.reject(line, "a cause is listed twice")
        elif len(r.cause_ids) != len(r.likelihoods):
            res.reject(line, f"{len(r.cause_ids)} causes but {len(r.likelihoods)} likelihoods")
        elif sum(r.likelihoods) > 1.001:
            res.reject(line, "likelihoods add up to more than 1")
        else:
            res_of = {c: next((i for f, i in causes[c] if f == r.family), None) for c in r.cause_ids}
            for c in (c for c, i in res_of.items() if i is None):
                res.warn(line, f"{c} has no repair steps for {r.family} yet, so it won't appear in a diagnosis")
            good.append({"id": r.key, "family": r.family, "code": r.code, "title": r.title, "display_text": r.display_text, "severity": r.severity,
                         "safety_stop": r.safety_stop, "notes": r.notes,
                         "causes": [{"cause_id": c, "likelihood": x, "resolution_id": res_of[c]} for c, x in zip(r.cause_ids, r.likelihoods, strict=True)]})
    if not good:
        return
    await conn.execute(text("""
        INSERT INTO error_codes (id, family, code, title, display_text, severity, safety_stop, causes, source)
        VALUES (:id, :family, :code, :title, :display_text, :severity, :safety_stop, CAST(:causes_json AS jsonb), :source)
        ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, display_text = EXCLUDED.display_text, severity = EXCLUDED.severity,
          safety_stop = EXCLUDED.safety_stop, causes = EXCLUDED.causes, source = EXCLUDED.source"""),
        [{**{k: g[k] for k in ("id", "family", "code", "title", "display_text", "severity", "safety_stop")}, "causes_json": json.dumps(g["causes"]),
          "source": source} for g in good])
    await upsert_error_codes(get_write_driver(), good, source)
    res.applied = len(good)


async def _spare_parts(conn: AsyncConnection, rows: list[tuple[int, SparePartRow]], source: str, res: Result) -> None:
    models = {r["id"]: r["family"] for r in await _kg("MATCH (m:Model)-[:BELONGS_TO]->(f:ProductFamily) RETURN m.id AS id, f.id AS family")}
    parts = {r["id"] for r in await _kg("MATCH (p:SparePart) RETURN p.id AS id")}
    passed = []
    for line, r in rows:
        wrong = [m for m in r.fits_models if models.get(m) != r.family]
        if r.part_no.split("-")[1] != r.family:
            res.reject(line, f"the part number doesn't belong to {r.family}")
        elif wrong:
            res.reject(line, f"not a {r.family} model: {', '.join(wrong)}")
        elif r.replaces == r.part_no:
            res.reject(line, "a part can't replace itself")
        else:
            passed.append((line, r))
    while True:                                     # a rejected row may be what another row replaces
        known = parts | {r.part_no for _, r in passed}
        bad = [(line, r) for line, r in passed if r.replaces and r.replaces not in known]
        if not bad:
            break
        for line, r in bad:
            res.reject(line, f"unknown part in replaces: {r.replaces}")
        passed = [x for x in passed if x not in bad]
    good = [r.model_dump() for _, r in passed]
    if not good:
        return
    await conn.execute(text("""
        INSERT INTO spare_parts (part_no, family, name, price_inr, consumable, fits_models, stock, lead_time_days, source)
        VALUES (:part_no, :family, :name, :price_inr, :consumable, :fits_models, CAST(:stock_json AS jsonb), :lead_time_days, :source)
        ON CONFLICT (part_no) DO UPDATE SET name = EXCLUDED.name, price_inr = EXCLUDED.price_inr, consumable = EXCLUDED.consumable,
          fits_models = EXCLUDED.fits_models, stock = EXCLUDED.stock, lead_time_days = EXCLUDED.lead_time_days, source = EXCLUDED.source"""),
        [{**{k: g[k] for k in ("part_no", "family", "name", "price_inr", "consumable", "fits_models", "lead_time_days")},
          "stock_json": json.dumps(g["stock"]), "source": source} for g in good])
    await upsert_parts(get_write_driver(), good, source)
    res.applied = len(good)


def _variant(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t.lower())).strip()


async def _faqs(conn: AsyncConnection, rows: list[tuple[int, FaqRow]], vectors: dict[str, list[float]], res: Result) -> None:
    active = set((await conn.execute(text("SELECT doc_id FROM kb_documents WHERE status = 'active'"))).scalars())
    owners: dict[str, str] = {r.normalized: r.faq_id for r in await conn.execute(text("SELECT normalized, faq_id FROM faq_variants"))}
    origin: dict[str, str] = {r.id: r.origin for r in await conn.execute(text("SELECT id, origin FROM faq_entries"))}
    for line, r in rows:
        texts = {v: t for t in (*reversed(r.paraphrases), r.question) if (v := _variant(t))}
        taken = sorted({o for v in texts if (o := owners.get(v)) and o != r.id})
        if origin.get(r.id, "upload") != "upload":
            res.reject(line, f"{r.id} already exists; edit it on the FAQ page")
        elif r.source_doc != CURATED and r.source_doc not in active:
            res.reject(line, f"source_doc must be an active document or {CURATED}")
        elif mask_pii(r.question + r.answer + "".join(r.paraphrases)) != r.question + r.answer + "".join(r.paraphrases):
            res.reject(line, "remove personal details (phone, e-mail, GSTIN)")
        elif not _variant(r.question) or not normalize(r.question):
            res.reject(line, "the question has no words that can be matched")
        elif taken:
            res.reject(line, f"{', '.join(taken)} already answers this question")
        else:
            point = await conn.begin_nested()
            try:
                await conn.execute(text("""INSERT INTO faq_entries (id, category, question, answer, source_doc, origin) VALUES (:i, :c, :q, :a, :d, 'upload')
                    ON CONFLICT (id) DO UPDATE SET category = EXCLUDED.category, question = EXCLUDED.question, answer = EXCLUDED.answer,
                      source_doc = EXCLUDED.source_doc, status = 'active'"""),
                    {"i": r.id, "c": r.category, "q": r.question, "a": r.answer, "d": r.source_doc})
                await conn.execute(text("DELETE FROM faq_variants WHERE faq_id = :i"), {"i": r.id})
                await conn.execute(text("INSERT INTO faq_variants (faq_id, text, normalized, embedding) VALUES (:i, :t, :n, CAST(:e AS vector))"),
                                   [{"i": r.id, "t": t, "n": v, "e": _vec(vectors[t])} for v, t in texts.items()])
                await invalidate_faq(conn, r.id)
                for t in texts.values():
                    await write_answer(conn, t, r.answer, [r.source_doc], faq_id=r.id, origin="upload")
            except CacheWriteRejected as exc:
                await point.rollback()
                res.reject(line, str(exc))
                continue
            await point.commit()
            owners = {v: o for v, o in owners.items() if o != r.id} | dict.fromkeys(texts, r.id)
            origin[r.id] = "upload"
            res.applied += 1
            near = (await conn.execute(text("""
                SELECT e.id, max(1 - (v.embedding <=> CAST(:q AS vector))) AS sim FROM faq_variants v JOIN faq_entries e ON e.id = v.faq_id
                WHERE e.id <> :i AND e.status = 'active' AND v.embedding IS NOT NULL GROUP BY e.id
                HAVING max(1 - (v.embedding <=> CAST(:q AS vector))) >= :s ORDER BY 2 DESC LIMIT 1"""),
                {"q": _vec(vectors[r.question]), "i": r.id, "s": SIMILAR})).first()
            if near is not None:            # both could answer a similar question: a person decides which is right
                await conn.execute(text("UPDATE faq_entries SET status = 'needs_review' WHERE id = :i"), {"i": near.id})
                res.warn(line, f"very similar to {near.id}, which is now marked for review")
