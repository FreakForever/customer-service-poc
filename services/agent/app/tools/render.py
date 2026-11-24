"""Tool results → customer text (T0, no model)."""

from app.tools.account import ToolResult


def render(r: ToolResult) -> str:
    if r.status != "ok":
        return r.message
    d = r.data
    if r.tool == "get_ticket_status":
        s = f"Ticket {d['ticket_id']} ({d['summary']}). Status: {d['status'].replace('_', ' ')}; priority {d['priority']}."
        if d["engineer"]:
            s += f" Engineer: {d['engineer']}."
        return s + (f" Visit scheduled for {d['scheduled_visit']}." if d["scheduled_visit"] else "")
    if r.tool == "get_amc_contract":
        s = f"{d['contract_id']}: {d['plan']} AMC, {d['status']}, ends {d['end']}."
        if d["status"] == "grace":
            s += " It is within the 30-day grace period, so it can be renewed without an inspection."
        return s + (" Renewal needs an inspection visit because it lapsed more than 30 days ago."
                    if d["renewal_needs_inspection"] else "")
    if r.tool == "get_warranty":
        state = "is under warranty until" if d["in_warranty"] else "is out of warranty; it ended on"
        return f"{d['model']} {d['serial_no']} {state} {d['warranty_end']}. Rule: {d['rule']}."
    if r.tool == "get_asset":
        return (f"{d['serial_no']}: {d['model']}, installed at {d['site_city']}, commissioned {d['commissioned_on']}, "
                f"warranty end {d['warranty_end']}, AMC {d['amc_id'] or 'none'}.")
    if r.tool == "get_part_availability":
        stock = ", ".join(f"{w} {n}" for w, n in d["stock"].items()) or "none"
        tail = "" if d["total"] else f" Not in stock; lead time {d['lead_time_days']} days."
        return f"{d['part_no']} ({d['name']}): INR {d['price_inr']}. Stock: {stock}.{tail}"
    raise ValueError(f"no renderer for {r.tool}")
