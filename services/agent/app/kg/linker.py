"""Entity linker (LLD-9, FR-KG-02): dictionary → Neo4j full-text → embedding similarity.

The dictionary is built from the knowledge graph (seed + uploads) at startup and on KG change. Error codes and
display texts ("DEW PT HIGH") are scoped to a product family and disambiguated with the model's family; when a
code exists in several families and nothing names the family, the result carries an ambiguity so the agent can
ask one clarifying question.
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol

ANCHOR_LABELS = {"Model", "ErrorCode", "SparePart", "Component", "Cause", "ProductFamily"}
EMBED_LABELS = ("Model", "ErrorCode", "Component", "Cause", "SparePart", "Resolution", "ProductFamily")
_CUE = re.compile(r"\b(show(?:s|ing)?|display(?:s|ing)?|says|reads|error|alarm|code|fault|message)\b[\s:'\"`-]*$",
                  re.IGNORECASE)
_LUCENE = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')
FAMILY_ALIASES = {  # generic words like "dryer" are ambiguous (DHD vs HAD), so only specific phrases
    "MTC": ["mold temperature controller", "mould temperature controller", "mtc"],
    "DHD": ["dehumidifying dryer", "desiccant dryer"],
    "HAD": ["hot air dryer"],
    "CHL": ["chiller", "chillers"],
    "MDH": ["mold dehumidifier", "mould dehumidifier"],
    "CCS": ["conveying system", "vacuum loader", "vacuum conveying"],
    "RCY": ["granulator", "granulators", "recycling granulator"],
}


@dataclass(frozen=True)
class Alias:
    text: str
    node_id: str
    label: str
    family: str | None = None
    case_sensitive: bool = False
    needs_cue: bool = False   # lower-case display texts only count after "showing", "error", ...


@dataclass
class LinkedEntity:
    id: str
    label: str
    mention: str
    start: int
    end: int
    source: str            # dictionary | fulltext | embedding
    score: float = 1.0
    family: str | None = None


@dataclass
class Ambiguity:
    mention: str
    candidates: list[str]


@dataclass
class Entities:
    items: list[LinkedEntity] = field(default_factory=list)
    ambiguous: list[Ambiguity] = field(default_factory=list)

    def ids(self, label: str) -> list[str]:
        return [e.id for e in self.items if e.label == label]

    @property
    def model(self) -> str | None:
        return next(iter(self.ids("Model")), None)

    @property
    def family(self) -> str | None:
        fams = [e.family for e in self.items if e.label in ("Model", "ErrorCode", "SparePart") and e.family]
        return fams[0] if fams else next(iter(self.ids("ProductFamily")), None)

    @property
    def error_codes(self) -> list[str]:
        return self.ids("ErrorCode")


class FullText(Protocol):
    async def search(self, query: str, limit: int) -> list[tuple[str, str, float]]: ...  # (id, label, score)


class _Embedder(Protocol):
    async def embed(self, texts: list[str], *, kind: Any) -> list[list[float]]: ...


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class Linker:
    def __init__(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        self.nodes = {n["id"]: n for n in nodes}
        self.family_of: dict[str, str] = {}
        for e in edges:
            if e["type"] in ("BELONGS_TO", "FOR_FAMILY"):
                self.family_of[e["src"]] = e["dst"]
        for n in nodes:
            if n["label"] == "SparePart" and n["id"] not in self.family_of:
                fam = n["id"].split("-")[1] if n["id"].count("-") >= 2 else None
                if fam in FAMILY_ALIASES:
                    self.family_of[n["id"]] = fam
        self.aliases = self._build_aliases(nodes)
        self._pattern = self._compile(self.aliases)
        self._by_text: dict[str, list[Alias]] = defaultdict(list)
        for a in self.aliases:
            self._by_text[a.text if a.case_sensitive else a.text.lower()].append(a)
        self._name_vectors: list[tuple[str, str, list[float]]] | None = None

    # ---------- dictionary ----------

    def _build_aliases(self, nodes: list[dict[str, Any]]) -> list[Alias]:
        out: list[Alias] = []
        for n in nodes:
            nid, label, p = n["id"], n["label"], n.get("props", {})
            fam = self.family_of.get(nid)
            if label == "Model":
                out += [Alias(nid, nid, label, fam), Alias(nid.replace("-", ""), nid, label, fam)]
            elif label == "ErrorCode":
                out.append(Alias(nid, nid, label, fam))
                if code := p.get("code"):
                    out.append(Alias(code, nid, label, fam))
                if disp := p.get("display_text"):
                    short = len(disp) <= 3
                    out.append(Alias(disp, nid, label, fam, case_sensitive=True))
                    if not short:
                        out.append(Alias(disp, nid, label, fam, needs_cue=True))
            elif label == "SparePart":
                out.append(Alias(nid, nid, label, fam))
            elif label == "Warehouse":
                city = p.get("city", "")
                out += [Alias(nid, nid, label), Alias(p.get("name", nid), nid, label),
                        Alias(f"{city} warehouse", nid, label), Alias(f"{city} regional warehouse", nid, label)]
            elif label == "ServiceCentre":
                out += [Alias(nid, nid, label), Alias(p.get("name", nid), nid, label)]
            elif label == "City":
                out.append(Alias(p.get("name", nid), nid, label))
            elif label == "AMCPlan":
                name = p.get("name", "")
                tier = name.replace(" AMC", "")
                out += [Alias(nid, nid, label), Alias(name, nid, label), Alias(f"{tier} plan", nid, label)]
                if tier.lower() != "basic":   # "basic" is too common a word on its own
                    out.append(Alias(tier, nid, label))
            elif label == "ProductFamily":
                out += [Alias(nid, nid, label, nid, case_sensitive=True), Alias(p.get("name", nid), nid, label, nid)]
                out += [Alias(a, nid, label, nid, case_sensitive=a.isupper()) for a in FAMILY_ALIASES.get(nid, [])]
            elif label == "Document":
                out.append(Alias(nid, nid, label))
        return [a for a in out if a.text]

    @staticmethod
    def _compile(aliases: list[Alias]) -> re.Pattern[str]:
        texts = sorted({a.text for a in aliases}, key=len, reverse=True)   # longest first wins
        return re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, texts)) + r")(?![\w-])", re.IGNORECASE)

    def _dictionary(self, query: str) -> tuple[list[LinkedEntity], list[tuple[str, int, int, list[Alias]]]]:
        found: list[LinkedEntity] = []
        family_scoped: list[tuple[str, int, int, list[Alias]]] = []
        for m in self._pattern.finditer(query):
            mention, start = m.group(1), m.start(1)
            cands = [a for a in self._by_text.get(mention.lower(), []) if not a.case_sensitive] + \
                    [a for a in self._by_text.get(mention, []) if a.case_sensitive]
            cue = bool(_CUE.search(query[max(0, start - 40):start]))
            cands = [a for a in cands if not a.needs_cue or cue or mention.isupper()]
            # case-insensitive display-text aliases are only there for the cue path; drop exact duplicates
            uniq = {(a.node_id, a.label): a for a in cands}
            cands = list(uniq.values())
            if not cands:
                continue
            if cands[0].label == "ErrorCode" or len({a.node_id for a in cands}) > 1:
                family_scoped.append((mention, start, m.end(1), cands))
            else:
                a = cands[0]
                found.append(LinkedEntity(a.node_id, a.label, mention, start, m.end(1), "dictionary", 1.0, a.family))
        return found, family_scoped

    def link_dictionary(self, query: str) -> Entities:
        found, scoped = self._dictionary(query)
        families = {e.family for e in found if e.family and e.label in ("Model", "ProductFamily", "SparePart")}
        ents = Entities(items=found)
        for mention, start, end, cands in scoped:
            pick = [a for a in cands if a.family in families] if families else cands
            ids = sorted({a.node_id for a in pick})
            if len(ids) == 1:
                a = pick[0]
                ents.items.append(LinkedEntity(a.node_id, a.label, mention, start, end, "dictionary", 1.0, a.family))
            elif ids:
                ents.ambiguous.append(Ambiguity(mention, ids))
        unique: dict[str, LinkedEntity] = {}
        for e in sorted(ents.items, key=lambda e: e.start):   # "E06" and "LOW FLOW" may name the same code
            unique.setdefault(e.id, e)
        ents.items = list(unique.values())
        return ents

    # ---------- fallbacks ----------

    async def link(self, query: str, *, fulltext: FullText | None = None, embedder: _Embedder | None = None,
                   fulltext_min: float = 1.0, embed_min: float = 0.6, top: int = 3) -> Entities:
        """Dictionary first; full-text and then embedding similarity only when no anchor entity was found."""
        ents = self.link_dictionary(query)
        if any(e.label in ANCHOR_LABELS for e in ents.items) or ents.ambiguous:
            return ents
        if fulltext is not None:
            for nid, label, score in await fulltext.search(lucene_query(query), top):
                if score >= fulltext_min and nid in self.nodes:
                    ents.items.append(LinkedEntity(nid, label, query, 0, len(query), "fulltext", score,
                                                   self.family_of.get(nid)))
            if any(e.source == "fulltext" for e in ents.items):
                return ents
        if embedder is not None:
            for nid, label, score in await self.similar(query, embedder, top):
                if score >= embed_min:
                    ents.items.append(LinkedEntity(nid, label, query, 0, len(query), "embedding", score,
                                                   self.family_of.get(nid)))
        return ents

    async def similar(self, query: str, embedder: _Embedder, top: int = 3) -> list[tuple[str, str, float]]:
        if self._name_vectors is None:
            named = [(nid, n["label"], n.get("props", {}).get("name") or nid) for nid, n in self.nodes.items()
                     if n["label"] in EMBED_LABELS]
            vecs = []
            for i in range(0, len(named), 100):
                vecs += await embedder.embed([t for _, _, t in named[i:i + 100]], kind="document")
            self._name_vectors = [(nid, label, _norm(v)) for (nid, label, _), v in zip(named, vecs, strict=True)]
        q = _norm((await embedder.embed([query], kind="query"))[0])
        scored = [(nid, label, sum(a * b for a, b in zip(q, v, strict=True))) for nid, label, v in self._name_vectors]
        return sorted(scored, key=lambda s: -s[2])[:top]


def lucene_query(text: str) -> str:
    """Escape Lucene syntax so user text can't change the full-text query's meaning."""
    words = [w.lower() if w in ("AND", "OR", "NOT", "TO") else w for w in re.split(r"\s+", text) if w]
    return " ".join(_LUCENE.sub(r"\\\1", w) for w in words)


class Neo4jFullText:
    """Full-text fallback over the `entity_fulltext` index (fixed query, user text only as a parameter)."""

    def __init__(self, driver: Any, database: str | None = None) -> None:
        self.driver, self.database = driver, database

    async def search(self, query: str, limit: int) -> list[tuple[str, str, float]]:
        from app.kg.client import read_query

        rows, _ = await read_query(self.driver, """
            CALL db.index.fulltext.queryNodes('entity_fulltext', $q) YIELD node, score
            RETURN node.id AS id, [l IN labels(node) WHERE l <> 'Entity'][0] AS label, score
            ORDER BY score DESC LIMIT $limit""", {"q": query, "limit": limit}, database=self.database)
        return [(r["id"], r["label"], float(r["score"])) for r in rows]


async def load_graph(driver: Any, database: str | None = None) -> tuple[list[dict], list[dict]]:
    """Nodes and family edges from Neo4j, in the same shape as mock-data/data/kg/*.jsonl."""
    from app.kg.client import read_query

    nodes, _ = await read_query(driver, """
        MATCH (n:Entity) RETURN n.id AS id, [l IN labels(n) WHERE l <> 'Entity'][0] AS label,
               properties(n) AS props""", {}, database=database)
    edges, _ = await read_query(driver, """
        MATCH (a:Entity)-[r:BELONGS_TO|FOR_FAMILY]->(b:Entity) RETURN a.id AS src, type(r) AS type, b.id AS dst""",
        {}, database=database)
    return nodes, edges
