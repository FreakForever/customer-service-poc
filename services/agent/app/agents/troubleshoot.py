"""Troubleshooting agent (LLD-10, FR-AG-01). The diagnosis and its wording come from the graph, so repair and safety
steps are never reworded by a model; symptoms that match no error code use retrieval + the strong model."""

import re
from typing import Any

from app.agents.base import AgentCtx, AgentResult, cite, remember
from app.agents.rag_answerer import answer
from app.kg.templates import run_template
from app.kg.verbalize import verbalize
from app.orchestrator.state import Citation
from app.retrieval.evidence import Evidence
from app.retrieval.graphrag import documented_chunks, graph_evidence

_SYN = {"temp": "temperature", "temps": "temperature", "regen": "regeneration", "dewpoint": "dew"}
_DONE = r"(?:clean|replac|chang|check|reset|refill|descal|inspect|calibrat)\w*"
_TRIED = re.compile(rf"\b(\w+)\s+(?:\w+\s+){{0,2}}?(?:already|was|were|has been|have been)\s+{_DONE}|"
                    rf"\b(?:we|i|have|already)\s+(?:have |already )*{_DONE}\s+(?:the |our |all )?(\w+)", re.IGNORECASE)
_HAS_MATERIAL = re.compile(r"\b(has|have|full of|enough) material\b", re.IGNORECASE)
_HINTS = [(re.compile(r"\bbore ?well|hard water|untreated water\b", re.IGNORECASE), "C-SCALE", "your water source")]
YES = re.compile(r"^\s*(yes|yeah|yep|ok(ay)?|sure|please do|haan|ha|go ahead)\b", re.IGNORECASE)
_RELEVANT = re.compile(r"\b(clean|replac|check|reset|still|again|tried|cause|fix|step|next|work|not)\w*", re.IGNORECASE)
_MEANS = re.compile(r"\b(what (?:does|is)|meaning of)\b.*\b(mean\w*|stand for|code|error)\b|\bmeaning of\b", re.IGNORECASE)
_WHICH_CODES = re.compile(r"\b(which|what|list|show)\b.*\b(error )?codes?\b", re.IGNORECASE)
_BY_CAUSE = re.compile(r"\b(?:caused by|due to|because of|result of|from)\b(.+)", re.IGNORECASE | re.DOTALL)
_SHARE = re.compile(r"\b(share[sd]?|same|common)\b.*\bcauses?\b", re.IGNORECASE)
_SMALL = {"a", "an", "the", "of", "or", "and", "in", "on", "with", "to"}
_GENERIC = {"the", "it", "them", "that", "this", "machine", "unit", "already"}


def words(text: str) -> set[str]:
    return {_SYN.get(w, w) for w in re.findall(r"[a-z]+", text.lower())}


def errors_from_titles(ctx: AgentCtx, family: str | None) -> list[str]:
    """Error codes whose whole title is in the text ("dew point high", "regen temp is also low")."""
    have = words(ctx.text)
    out = []
    for nid, n in ctx.linker.nodes.items():
        if n["label"] == "ErrorCode" and family and ctx.linker.family_of.get(nid) == family:
            title = words(n["props"]["name"].split(" ", 1)[-1])
            if len(title) > 1 and title <= have:
                out.append(nid)
    return sorted(out)


def tried_causes(ctx: AgentCtx, causes: dict[str, str]) -> set[str]:
    nouns = {g.lower() for m in _TRIED.finditer(ctx.text) for g in m.groups() if g} - _GENERIC
    out = {cid for cid, name in causes.items() if any(re.search(rf"\b{re.escape(n)}", name.lower()) for n in nouns)}
    if _HAS_MATERIAL.search(ctx.text):
        out |= {cid for cid, name in causes.items() if "empty" in name.lower()}
    return out


async def diagnose(ctx: AgentCtx, errors: list[str]) -> tuple[list[dict[str, Any]], list[Evidence], str | None]:
    """T-DIAG rows for each code, merged by cause: codes explained first, then summed likelihood."""
    model = ctx.dialog.get("model")
    by_cause: dict[str, dict[str, Any]] = {}
    evidence: list[Evidence] = []
    used = None
    for eid in errors:
        fam = ctx.linker.family_of.get(eid)
        models = [model] if model else sorted(n for n, f in ctx.linker.family_of.items()
                                              if f == fam and ctx.linker.nodes[n]["label"] == "Model")
        for m in models:
            params = {"model": m, "code": ctx.linker.nodes[eid]["props"]["code"]}
            res = await run_template("T-DIAG", params, driver=ctx.deps.kg_driver, database=ctx.deps.kg_database,
                                     timeout_s=ctx.deps.kg_timeout_s)
            if res.rows:
                used = m
                evidence += verbalize("T-DIAG", params, res.rows)
                for r in res.rows:
                    c = by_cause.setdefault(r["cause_id"], {**r, "score": 0.0, "codes": []})
                    c["score"] += float(r["likelihood"])
                    c["codes"].append(r["code"])
                break
    return sorted(by_cause.values(), key=lambda c: (-len(c["codes"]), -c["score"], c["cause_id"])), evidence, used


def _cited(text: str, marker: str) -> str:
    """A marker on every sentence (a step can have several)."""
    from app.verify.coverage import split_sentences

    return " ".join(f"{s.rstrip('.')} {marker}." for s in split_sentences(text))


async def code_list(ctx: AgentCtx) -> AgentResult | None:
    """"Which dryer codes can be caused by a clogged filter?", "which codes share a cause with E05?": straight from the graph."""
    family = ctx.ents.family
    if not family or not _WHICH_CODES.search(ctx.text):
        return None
    calls: list[tuple[str, dict[str, str]]] = []
    if _SHARE.search(ctx.text) and ctx.ents.error_codes:
        calls = [("T-SHARED", {"error_id": ctx.ents.error_codes[0], "family": family})]
    elif m := _BY_CAUSE.search(ctx.text):
        want = words(m.group(1)) - _SMALL
        calls = [("T-BYCAUSE", {"cause": nid, "family": family}) for nid, n in sorted(ctx.linker.nodes.items())
                 if n["label"] == "Cause" and want and want <= words(n["props"]["name"])]
    evidence: list[Evidence] = []
    for tid, params in calls[:3]:
        res = await run_template(tid, params, driver=ctx.deps.kg_driver, database=ctx.deps.kg_database, timeout_s=ctx.deps.kg_timeout_s)
        names = ctx.linker.nodes
        text_ids = {k: names[v]["props"].get("name", v) for k, v in params.items() if v in names}
        for e in verbalize(tid, params, res.rows):
            if tid == "T-BYCAUSE":                           # ids mean nothing to a customer
                e = e.model_copy(update={"text": e.text.replace(params["cause"], f"\"{text_ids['cause']}\"")})
            evidence.append(e)
    if not evidence:
        return None
    return AgentResult("\n".join(f"{e.text.rstrip('.')} [E{i}]." for i, e in enumerate(evidence, 1)), active_agent="troubleshoot", evidence=evidence,
                       citations=cite(evidence))


async def run(ctx: AgentCtx) -> AgentResult:
    d = ctx.dialog
    if (listed := await code_list(ctx)) is not None:
        remember(ctx)
        return listed
    if ctx.sticky:
        if YES.match(ctx.text) and d.get("offered_ticket"):
            from app.agents import service_request

            return await service_request.run(ctx)
        known = ctx.ents.items or ctx.ents.serials or ctx.ents.ambiguous
        if not known and not _RELEVANT.search(ctx.text) and len(ctx.text.split()) > 3:
            return AgentResult("", handback=True)
    remember(ctx)
    family = d.get("family")
    errors = sorted(set(d.get("errors") or []) | set(errors_from_titles(ctx, family)))
    if errors:
        d["errors"] = errors
    if not d.get("model") and not d.get("asked_model") and not (family and errors and _MEANS.search(ctx.text)) and (errors or d.get("pending") or not family):
        d["asked_model"] = True
        what = f"{ctx.linker.nodes[errors[0]]['props']['name']}" if errors else "that"
        return AgentResult(f"I can help with {what}. Which model is it (it's on the nameplate, for example CHL-10W)?",
                           active_agent="troubleshoot")
    if not errors:
        return await _symptom(ctx)

    causes, evidence, _ = await diagnose(ctx, errors)
    if not causes:
        return await _symptom(ctx)
    tried = set(d.get("tried") or []) | tried_causes(ctx, {c["cause_id"]: c["cause"] for c in causes})
    d["tried"] = sorted(tried)
    open_ = [c for c in causes if c["cause_id"] not in tried]
    ranked = open_ or causes
    evidence.sort(key=lambda e: [c["cause_id"] for c in causes].index(e.metadata["cause_id"]))
    chunks = await documented_chunks(errors, ctx.deps)
    cites: list[Citation] = cite([*evidence, *chunks])
    mark = {c["cause_id"]: next(x.marker for x, e in zip(cites, evidence, strict=False)
                                if e.metadata["cause_id"] == c["cause_id"]) for c in causes}

    names = " and ".join(f"{ctx.linker.nodes[e]['props']['name']}" for e in errors)
    top = ranked[0]
    both = f" It explains both {' and '.join(top['codes'])}." if len(set(top["codes"])) > 1 else ""
    first = mark[ranked[0]["cause_id"]]           # every row carries the code, severity and safety stop
    out = [f"{d.get('model') or 'Your machine'}: {names} {first}."]
    if any(c.get("safety_stop") for c in causes):
        stop = next(mark[c["cause_id"]] for c in causes if c.get("safety_stop"))
        out.append(f"Safety: the machine stops automatically for this code {stop}. Isolate power before any check and do "
                   f"not restart until the cause is fixed {stop}.")
    ties = [c for c in ranked if (len(c["codes"]), round(c["score"], 6)) == (len(top["codes"]), round(top["score"], 6))]
    if len(ties) > 1:                        # never pick one of several equally likely causes (D-027)
        out.append("Most likely causes (equally likely): " + "; ".join(f"{c['cause']} {mark[c['cause_id']]}" for c in ties) + ".")
    else:
        out.append(f"Most likely cause: {top['cause']}. {mark[top['cause_id']]}" + (f"{both[:-1]} {mark[top['cause_id']]}." if both else ""))
    customer = [c for c in ranked if c["performed_by"] == "customer"]
    tech = [c for c in ranked if c["performed_by"] != "customer"]
    if customer:
        out.append("You can check:\n" + "\n".join(
            f"{i}. {_cited(c['cause'] + ': ' + c['steps'], mark[c['cause_id']])}" for i, c in enumerate(customer, 1)))
    if tech:
        out.append("Needs a technician:\n" + "\n".join(
            "- " + _cited(f"{c['cause']}: {c['steps']}" + (f" Parts: {', '.join(c['parts'])}." if c["parts"] else ""),
                          mark[c["cause_id"]]) for c in tech))
    for pattern, cid, why in _HINTS:
        if pattern.search(ctx.text) and (hit := next((c for c in ranked[1:] if c["cause_id"] == cid), None)):
            out.append(f"Given {why}, also check early: {hit['cause']}. {mark[cid]}")
    if tried and open_:
        done = ", ".join(c["cause"] for c in causes if c["cause_id"] in tried)
        out.append(f"I've set aside what you already checked: {done} "
                   + "".join(mark[c["cause_id"]] for c in causes if c["cause_id"] in tried) + ".")
    if chunks:
        out.append("Manual: " + " ".join(c.marker for c in cites[len(evidence):]))
    actions = []
    if tech:
        out.append("Would you like me to raise a service request?")
        d["offered_ticket"] = True
        actions = [{"type": "offer_ticket", "prefill": {"serial_no": d.get("serial"), "error_code": errors[0]}}]
    d["top_cause"] = top["cause_id"]
    return AgentResult("\n".join(out), active_agent="troubleshoot", evidence=[*evidence, *chunks], citations=cites,
                       actions=actions)


async def _symptom(ctx: AgentCtx) -> AgentResult:
    ge = await graph_evidence(ctx.text, ctx.ents, "hybrid", ctx.deps)
    res = await answer(ctx.text, ge.evidence, ctx.llm, model=ctx.model_strong, timeout_ms=ctx.timeout_ms)
    return AgentResult(res.draft.text, active_agent=None if res.draft.abstained else "troubleshoot",
                       evidence=ge.evidence, citations=res.draft.citations, abstained=res.draft.abstained,
                       generated=res.draft.generated)
