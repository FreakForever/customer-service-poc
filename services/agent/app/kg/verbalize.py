"""Turn template / Text2Cypher rows into short factual sentences with path ids (LLD-9, FR-KG-05).

e.g. "DHD-150 can raise E04 (Dew point high, severity warning). Possible cause: Desiccant rotor degraded or
contaminated (likelihood 40%). Resolution: Inspect rotor ... Done by: technician. Parts: MT-DHD-ROTOR."
"""

import hashlib
from typing import Any

from app.retrieval.evidence import Evidence


def _pct(x: Any) -> str:
    return f"{round(float(x) * 100)}%" if x is not None else "unknown"


def _ev(tid: str, key: str, text: str, path_ids: list[str], score: float, **meta: Any) -> Evidence:
    return Evidence(id=f"kg:{tid}:{key}", kind="kg_path", text=text, source=tid, score=score, path_ids=path_ids,
                    metadata=meta)


def verbalize(tid: str, params: dict[str, Any], rows: list[dict[str, Any]],
              names: dict[str, str] | None = None) -> list[Evidence]:
    """names: optional id → display name (warehouses, plans) for nicer sentences."""
    names = names or {}
    if not rows:
        return []
    if tid == "T-DIAG":
        out = []
        for r in rows:
            safety = " The machine stops automatically (safety stop)." if r.get("safety_stop") else ""
            parts = f" Parts: {', '.join(r['parts'])}." if r.get("parts") else " No spare part needed."
            text = (f"{params['model']} can raise {r['code']} ({r['error'].split(' ', 1)[-1]}, severity {r['severity']})."
                    f"{safety} Possible cause: {r['cause']} (likelihood {_pct(r['likelihood'])}). "
                    f"Resolution: {r['steps']} Done by: {r['performed_by']}.{parts}")
            out.append(_ev(tid, r["path_id"].split(":", 1)[1], text, [r["path_id"]], float(r["likelihood"] or 0),
                           error_id=r["error_id"], cause_id=r["cause_id"], parts=r.get("parts", [])))
        return out
    if tid == "T-COVER":
        causes = sorted({r["cause_id"] for r in rows})
        lead = ("the most likely cause that needs a part is" if len(causes) == 1 else
                f"{len(causes)} causes are equally likely and need parts:")
        items = "; ".join(f"{r['cause']} (likelihood {_pct(r['likelihood'])}) needs {r['part_no']} ({r['part']}), "
                          f"covered by: {', '.join(r['covered_by']) if r['covered_by'] else 'no AMC plan'}"
                          for r in rows)
        text = f"For {params['code']} on {params['model']}, {lead} {items}."
        return [_ev(tid, f"{params['model']}>{params['code']}", text, [r["path_id"] for r in rows], 1.0,
                    part_nos=[r["part_no"] for r in rows],
                    covered_by=sorted({p for r in rows for p in r["covered_by"]}),
                    coverage={r["part_no"]: r["covered_by"] for r in rows})]
    if tid == "T-STOCK":
        first = rows[0]
        lines = []
        for r in rows:
            home = " (home warehouse for " + params["city"] + ")" if r["is_home_warehouse"] else ""
            lines.append(f"{names.get(r['warehouse'], r['warehouse'])} [{r['warehouse']}]: {r['qty']}{home}")
        text = (f"Stock of {first['part_no']} ({first['part']}): " + "; ".join(lines) +
                f". If unavailable, lead time is {first['lead_time_days']} days.")
        return [_ev(tid, f"{first['part_no']}@{params['city'].lower()}", text, [r["path_id"] for r in rows], 1.0,
                    part_no=first["part_no"], stock={r["warehouse"]: r["qty"] for r in rows})]
    if tid == "T-PARTS":
        items = "; ".join(f"{r['part_no']} ({r['part']}, INR {r['price_inr']})" for r in rows)
        text = f"Parts needed to resolve {params['cause']} on {params['family']} equipment: {items}."
        return [_ev(tid, f"{params['cause']}@{params['family']}", text, [r["path_id"] for r in rows], 1.0)]
    if tid == "T-SHARED":
        items = "; ".join(f"{r['code']} ({r['error'].split(' ', 1)[-1]}) via {', '.join(r['shared_causes'])}" for r in rows)
        text = f"Error codes in {params['family']} sharing a cause with {params['error_id']}: {items}."
        return [_ev(tid, params["error_id"], text, [r["path_id"] for r in rows], 1.0)]
    if tid == "T-BYCAUSE":
        items = "; ".join(f"{r['code']} ({r['error'].split(' ', 1)[-1]})" for r in rows)
        text = f"{params['family']} error codes caused by {params['cause']}: {items}."
        return [_ev(tid, f"{params['cause']}@{params['family']}", text, [r["path_id"] for r in rows], 1.0)]
    if tid == "T-SC":
        items = "; ".join(f"{r['name']} [{r['id']}] in {r['city']}, {r['state']} ({r['technicians']} technicians, "
                          f"home warehouse {r['home_warehouse']})" for r in rows)
        text = f"Service centre covering {params['city']}: {items}."
        return [_ev(tid, params["city"].lower(), text, [r["path_id"] for r in rows], 1.0)]
    raise ValueError(f"no verbalizer for {tid}")


def verbalize_query_rows(cypher: str, rows: list[dict[str, Any]], max_rows: int = 10) -> list[Evidence]:
    """Generic sentence for Text2Cypher results (no path semantics): one evidence with the first rows."""
    if not rows:
        return []
    shown = rows[:max_rows]
    lines = ["; ".join(f"{k}={v}" for k, v in r.items()) for r in shown]
    more = f" (+{len(rows) - max_rows} more rows)" if len(rows) > max_rows else ""
    key = hashlib.sha1(cypher.encode()).hexdigest()[:10]
    return [Evidence(id=f"kg:text2cypher:{key}", kind="kg_path", source="text2cypher", score=0.8,
                     text="Knowledge graph query result: " + " | ".join(lines) + more,
                     path_ids=[f"text2cypher:{key}"], metadata={"cypher": cypher, "rows": len(rows)})]
