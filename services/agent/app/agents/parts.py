"""Parts agent (LLD-10, FR-AG-04): resolve the part, then price, stock, home warehouse and ETA; quote requests."""

import asyncio
import logging
import re

from app.agents.base import AgentCtx, AgentResult, cite, remember
from app.agents.troubleshoot import words
from app.kg.templates import run_template
from app.kg.verbalize import verbalize
from app.retrieval.evidence import Evidence
from app.tools.account import call_tool
from app.verify.coverage import split_sentences

log = logging.getLogger("mcs.parts")

_QUOTE = re.compile(r"\b(quote|quotation|order it|place (an )?order)\b", re.IGNORECASE)
_QTY = re.compile(r"\b(\d{1,3})\s*(?:nos?|pcs|pieces|units?|sets?)\b", re.IGNORECASE)
CUES = re.compile(r"\b(parts?|spares?|stock|available|availability|price|cost|deliver\w*|lead time|warehouse|quote|"
                  r"quotation|order)\b", re.IGNORECASE)
_STOP = {"set", "part", "spare", "for", "and", "the", "machine", "assembly", "need", "a"}
ETA = {"home": "dispatches the same business day if confirmed before 14:00 IST", "other": "transfer adds 2–3 business days"}


def _singular(ws: set[str]) -> set[str]:
    return {w[:-1] if w.endswith("s") and len(w) > 3 else "blade" if w == "knife" else w for w in ws}


async def resolve(ctx: AgentCtx) -> tuple[list[str], list[Evidence]]:
    d = ctx.dialog
    if parts := ctx.ents.ids("SparePart") or ctx.ents.part_nos:
        return parts[:3], []
    if d.get("model"):
        family = ctx.linker.nodes.get(d.get("family") or "", {}).get("props", {}).get("name", "")
        asked = _singular(words(ctx.text)) - _STOP - _singular(words(family))     # "granulator" isn't a part name
        rows = await ctx.tools.repo.parts_for_model(d["model"])
        scored = sorted(((len((_singular(words(r["name"].split("(")[0])) - _STOP) & asked), r["part_no"]) for r in rows),
                        key=lambda x: (-x[0], x[1]))
        if scored and scored[0][0]:
            return [p for n, p in scored if n == scored[0][0]][:3], []
        if d.get("errors"):
            params = {"model": d["model"], "code": ctx.linker.nodes[d["errors"][0]]["props"]["code"]}
            res = await run_template("T-COVER", params, driver=ctx.deps.kg_driver, database=ctx.deps.kg_database,
                                     timeout_s=ctx.deps.kg_timeout_s)
            return list(dict.fromkeys(r["part_no"] for r in res.rows))[:3], verbalize("T-COVER", params, res.rows)
    return (d.get("parts") or [])[:3], []


async def _graph(ctx: AgentCtx, part: str) -> tuple[Evidence | None, list[Evidence]]:
    """(the part as the graph knows it, parts that replace it on the customer's model). Optional: a slow graph never fails the answer."""
    if ctx.deps.kg_driver is None:
        return None, []
    try:
        res = await run_template("T-PART", {"part": part}, driver=ctx.deps.kg_driver, database=ctx.deps.kg_database, timeout_s=ctx.deps.kg_timeout_s)
    except Exception as exc:  # noqa: BLE001
        log.warning("T-PART failed: %s", type(exc).__name__)
        return None, []
    if not res.rows:
        return None, []
    own = verbalize("T-PART", {"part": part}, [{**res.rows[0], "replaced_by": None}])[0]
    model = ctx.dialog.get("model")
    newer = [r for r in res.rows if r["replaced_by"] and (not model or not r["replacement_fits"] or model in r["replacement_fits"])]
    return own, verbalize("T-PART", {"part": part}, newer)


def _newer(lines: list[str], evidence: list[Evidence], newer: list[Evidence]) -> None:
    for e in newer:
        evidence.append(e)
        lines.append(" ".join(f"{s.rstrip('.')} [E{len(evidence)}]." for s in split_sentences(e.text)))


async def run(ctx: AgentCtx) -> AgentResult:
    d = ctx.dialog
    if ctx.sticky and not (CUES.search(ctx.text) or ctx.ents.items or ctx.ents.part_nos or _QTY.search(ctx.text)):
        return AgentResult("", handback=True)
    remember(ctx)
    if ctx.sticky and _QUOTE.search(ctx.text) and d.get("parts"):
        return await _quote(ctx)
    parts, evidence = await resolve(ctx)
    if not parts:
        return AgentResult("Which part do you need? Please share the part number, or the machine model and the part "
                           "name.", active_agent="parts")
    d["parts"] = parts
    city = d.get("city") or (await ctx.tools.repo.customer_city(ctx.tools.customer_id) if ctx.tools.customer_id else None)
    lines: list[str] = []
    graph = await asyncio.gather(*(_graph(ctx, p) for p in parts))
    for p, (own, newer) in zip(parts, graph, strict=True):
        r = await call_tool("get_part_availability", {"part_no": p, **({"city": city} if city else {})}, ctx.tools)
        if r.status != "ok":
            if own:                                # known from an uploaded document only: no live stock record
                evidence.append(own)
                lines.append(f"{own.text} [E{len(evidence)}]")
            else:
                lines.append(f"{p}: I couldn't find this part number.")
            _newer(lines, evidence, newer)
            continue
        x = r.data
        home, stock = x["home_warehouse"], x["stock"]
        others = {w: n for w, n in stock.items() if n and w != home}
        if home and stock.get(home):
            eta = f"in stock at {home}, which supplies {city}: {ETA['home']}"
        elif others:
            where = ", ".join(f"{w} {n}" for w, n in others.items())
            eta = (f"not in stock at {home}, which supplies {city}; available at {where}: {ETA['other']}" if home
                   else f"available at {where}")
        else:
            eta = f"out of stock; lead time {x['lead_time_days']} days"
        e = Evidence(id=f"tool:get_part_availability:{p}", kind="tool", source="get_part_availability", score=1.0,
                     text=f"{p} INR {x['price_inr']} stock {stock}", metadata={"stock": stock})
        evidence.append(e)
        lines.append(f"{p} ({x['name']}): INR {x['price_inr']}; {eta}. [E{len(evidence)}]")
        _newer(lines, evidence, newer)
    text = "\n".join(lines) + "\nWould you like me to request a quote?"
    return AgentResult(text, active_agent="parts", evidence=evidence, citations=cite(evidence),
                       actions=[{"type": "offer_quote", "parts": parts}])


async def _quote(ctx: AgentCtx) -> AgentResult:
    d = ctx.dialog
    m = _QTY.search(ctx.text)
    args = {"part_no": d["parts"][0], "qty": int(m.group(1)) if m else 1}
    key = f"{args['part_no']}x{args['qty']}"
    if key in d.get("quotes", {}):
        return AgentResult(f"That quote request is already logged as {d['quotes'][key]}.", tier="T0")
    r = await call_tool("create_parts_quote", args, ctx.tools)
    if r.status != "ok":
        return AgentResult(r.message, tier="T0", active_agent="parts")
    d.setdefault("quotes", {})[key] = r.data["quote_request_id"]
    return AgentResult(f"I've logged quote request {r.data['quote_request_id']} for {args['qty']} × {args['part_no']}. "
                       "No payment is taken here.", tier="T0", actions=[{"type": "quote_requested", **r.data}])
