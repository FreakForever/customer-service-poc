"""Service-request state machine (LLD-10, FR-AG-02): collect_serial → collect_issue → collect_contact → confirm →
created. `confirm` is asked only when a field was prefilled rather than typed by the customer (D-035)."""

import re
from typing import Any

from app.agents import troubleshoot
from app.agents.base import AgentCtx, AgentResult, remember
from app.tools.account import call_tool

_PHONE = re.compile(r"(?<![\w-])((?:\+91[-\s]?|0)?(?:\d{5}[-\s]?\d{5,6}|\d{10})|\d{2}x{4,8}\d{2})(?![\w-])", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CANCEL = re.compile(r"\b(cancel|never ?mind|forget it|stop this|don'?t (raise|create)|rehne do)\b", re.IGNORECASE)
_NO = re.compile(r"^\s*(no|nope|nahi|not now)\b", re.IGNORECASE)
_OPENER = re.compile(r"\b(raise|log|register|create|open|book|file)\b.{0,30}\b(complaint|ticket|request|call|visit)\b|"
                     r"\bsend (an? )?(engineer|technician)\b", re.IGNORECASE)
ASK = {"serial": "Please share the machine's serial number (on the nameplate, for example MT-GR200-24-00000).",
       "issue": "What is the problem? If the display shows a code, please include it.",
       "contact": "Which phone number or email should our engineer use to contact you?"}


def form(s: dict[str, Any]) -> dict[str, Any]:
    return {"form_id": "service_request", "fields": [
        {"name": "serial_no", "required": True, "pattern": r"^MT-[A-Z0-9]+-\d{2}-\d{5}$", "value": s.get("serial")},
        {"name": "description", "required": True, "value": s.get("issue")},
        {"name": "contact", "required": True, "value": s.get("contact")}]}


async def run(ctx: AgentCtx) -> AgentResult:
    d = ctx.dialog
    s = d.setdefault("service", {})
    if _CANCEL.search(ctx.text):
        d.pop("service")
        return AgentResult("Okay, I won't raise a service request. Anything else I can help with?", tier="T0")
    if s.get("step") == "confirm":
        if troubleshoot.YES.match(ctx.text):
            return await _create(ctx, s)
        if _NO.match(ctx.text):
            for k in ("serial", "issue", "contact", "step"):
                s.pop(k, None)
            d.pop("serial", None)
            s["no_prefill"] = True
            return AgentResult("No problem. " + ASK["serial"], tier="T0", active_agent="service_request", form=form(s))
    started = bool(s)
    remember(ctx)
    tier, intro, evidence, citations = "T0", "", [], []

    # fields typed now
    if ctx.ents.serials:
        serial = ctx.ents.serials[0]
        if ctx.tools.customer_id and (await call_tool("get_asset", {"serial_no": serial}, ctx.tools)).status != "ok":
            d.pop("serial", None)
            return AgentResult(f"I couldn't find {serial} under your account. Please check the serial number.", tier="T0",
                               active_agent="service_request", form=form(s))
        s["serial"] = serial
    if m := _PHONE.search(ctx.text) or _EMAIL.search(ctx.text):
        s["contact"] = m.group(0).strip()
    errors = list(ctx.ents.error_codes)
    asked = s.get("step")
    plain = ctx.text if ctx.env.channel == "email" else re.sub(_PHONE, "", ctx.text)
    if not s.get("issue") and (errors or ctx.env.channel == "email" or
                               (started and asked == "issue" and not ctx.ents.serials and len(plain.split()) > 1)):
        s["issue"] = plain.strip()[:2000]
        if errors:
            s["error_code"] = errors[0]
            if ctx.sticky and d.get("model"):        # a diagnosable code: help while collecting the rest
                diag = await troubleshoot.run(_fresh(ctx))
                d.pop("offered_ticket", None)
                intro = diag.text.replace("Would you like me to raise a service request?", "").strip() + "\n"
                tier, evidence, citations = "T3", diag.evidence, diag.citations

    # prefill from the earlier conversation / the sender, marked so we confirm before creating
    if not s.get("no_prefill"):
        if not s.get("serial") and d.get("serial"):
            s["serial"], s["prefilled"] = d["serial"], True
        if not s.get("issue") and d.get("errors") and not _OPENER.search(ctx.text):
            s["issue"] = f"{d.get('model') or ''} {ctx.linker.nodes[d['errors'][0]]['props']['name']}".strip()
            s["error_code"], s["prefilled"] = d["errors"][0], True
        if not s.get("contact") and ctx.env.channel == "email" and ctx.env.customer and ctx.env.customer.contact_email:
            s["contact"] = ctx.env.customer.contact_email

    missing = [k for k in ("issue", "serial", "contact") if not s.get(k)]
    if not s.get("issue") and not s.get("serial"):
        missing = ["issue", *[k for k in missing if k != "issue"]]
    if missing:
        s["step"] = missing[0]
        lead = "" if started else "I'm sorry for the trouble. I'll raise a service request for you. "
        ask = " ".join(ASK[k] for k in missing) if ctx.env.channel == "email" else ASK[missing[0]]
        return AgentResult(intro + lead + ask, tier=tier, active_agent="service_request", form=form(s),
                           evidence=evidence, citations=citations)
    if s.get("prefilled") and ctx.env.channel != "email":
        s["step"] = "confirm"
        return AgentResult(f"{intro}Shall I raise this? Machine {s['serial']}, issue: {s['issue']}, contact: "
                           f"{s['contact']}. (yes/no)", tier=tier, active_agent="service_request", form=form(s))
    return await _create(ctx, s)


def _fresh(ctx: AgentCtx) -> AgentCtx:
    from dataclasses import replace

    return replace(ctx, sticky=False)


async def _create(ctx: AgentCtx, s: dict[str, Any]) -> AgentResult:
    who = ctx.env.meta.get("from_name") or "Customer"
    contact = {"name": who, "email" if "@" in s["contact"] else "phone": s["contact"]}
    args = {"serial_no": s["serial"], "description": s["issue"], "contact": contact, "error_code": s.get("error_code")}
    r = await call_tool("create_ticket", {k: v for k, v in args.items() if v}, ctx.tools)
    if r.status != "ok":
        s.pop("serial", None)
        s["step"] = "serial"
        return AgentResult(r.message + " " + ASK["serial"], tier="T0", active_agent="service_request", form=form(s))
    for k in ("service", "offered_ticket", "serial"):
        ctx.dialog.pop(k, None)
    ctx.dialog["last_ticket"] = r.data["ticket_id"]
    note = " We'll confirm your account details when we contact you." if r.data["unverified"] else ""
    masked = " That number looks incomplete, so please reply with the full number or an email." if "x" in s["contact"].lower() \
        and "@" not in s["contact"] else ""
    return AgentResult(f"Your service request is {r.data['ticket_id']} (priority {r.data['priority']}). Our team will "
                       f"contact you at {s['contact']}.{masked}{note}", tier="T0",
                       actions=[{"type": "ticket_created", **r.data}])
