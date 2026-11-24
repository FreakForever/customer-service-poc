"""Typed, customer-scoped tools (LLD-10; FR-AG-03, FR-AG-06, NFR-SEC-03).

`call_tool` is the only entry point: an allow-list with validated arguments. The customer comes from the server-side
context, never from arguments (extra arguments are rejected). A record that belongs to someone else gives the same
result as one that doesn't exist. Unidentified customers get needs_verification.
"""

import datetime as dt
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from app.tools.repo import OpsRepo, Row

GRACE_DAYS = 30
WARRANTY_RULE = "12 months from commissioning or 18 months from dispatch, whichever is earlier"
PHONE_PLACEHOLDER = "+91-90000-000000 (demo placeholder)"
_STOPPED = re.compile(r"\b(production|line|plant)\b.{0,25}\b(stop\w*|down|halt\w*|stuck)\b", re.IGNORECASE)
_REDUCED = re.compile(r"\b(reduced|low(er)?|slow(er)?|partial)\b.{0,20}\b(output|production|throughput|speed)\b", re.IGNORECASE)

Upper = StringConstraints(strip_whitespace=True, to_upper=True)
TicketId = Annotated[str, Upper, Field(pattern=r"(?i)^\s*SR-\d{4}-\d{5}\s*$")]
Serial = Annotated[str, Upper, Field(pattern=r"(?i)^\s*MT-[A-Z0-9]+-\d{2}-\d{5}\s*$")]
ContractId = Annotated[str, Upper, Field(pattern=r"(?i)^\s*AMC-\d{4}-\d{4}\s*$")]
PartNo = Annotated[str, Upper, Field(pattern=r"(?i)^\s*MT-[A-Z0-9-]+\s*$", max_length=40)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TicketArgs(Args):
    ticket_id: TicketId


class SerialArgs(Args):
    serial_no: Serial


class ContractArgs(Args):
    contract_id: ContractId | None = None
    serial_no: Serial | None = None

    @model_validator(mode="after")
    def one(self) -> "ContractArgs":
        if not (self.contract_id or self.serial_no):
            raise ValueError("contract_id or serial_no is required")
        return self


class CityArgs(Args):
    city: Text


class PartArgs(Args):
    part_no: PartNo
    city: Text | None = None


class Contact(Args):
    name: Text
    phone: Text | None = None
    email: Text | None = None

    @model_validator(mode="after")
    def reachable(self) -> "Contact":
        if not (self.phone or self.email):
            raise ValueError("phone or email is required")
        return self


class CreateTicketArgs(Args):
    serial_no: Serial
    description: Text
    contact: Contact
    error_code: Text | None = None
    priority: Literal["P1", "P2", "P3"] | None = None


class QuoteArgs(Args):
    part_no: PartNo
    qty: int = Field(ge=1, le=1000)
    serial_no: Serial | None = None


class LeadArgs(Args):
    product_interest: Text
    application: Text
    throughput: Text
    location: Text
    contact: Contact


Status = Literal["ok", "needs_verification", "not_found", "invalid"]


@dataclass
class ToolContext:
    repo: OpsRepo
    customer_id: str | None = None
    channel: str = "chat"
    safety: bool = False
    today: dt.date = field(default_factory=dt.date.today)


@dataclass
class ToolResult:
    tool: str
    status: Status
    data: Row = field(default_factory=dict)
    message: str = ""


Handler = Callable[[Any, ToolContext], Awaitable[Row | None]]


async def get_ticket_status(a: TicketArgs, c: ToolContext) -> Row | None:
    t = await c.repo.ticket(a.ticket_id, c.customer_id or "")
    return t and {"ticket_id": t["id"], "status": t["status"], "priority": t["priority"], "summary": t["summary"],
                  "engineer": t["assigned_engineer"], "scheduled_visit": t["scheduled_visit"],
                  "last_events": (t["history"] or [])[-3:]}


def amc_status(end: dt.date, today: dt.date) -> str:
    if today <= end:
        return "active"
    return "grace" if (today - end).days <= GRACE_DAYS else "expired"


async def get_asset(a: SerialArgs, c: ToolContext) -> Row | None:
    x = await c.repo.asset(a.serial_no, c.customer_id or "")
    if not x:
        return None
    amc = await c.repo.contract(c.customer_id or "", contract_id=None, serial_no=a.serial_no)
    return {k: x[k] for k in ("serial_no", "model", "family", "commissioned_on", "dispatched_on", "warranty_end",
                              "site_city", "running_hours")} | {"amc_id": amc["id"] if amc else None}


async def get_warranty(a: SerialArgs, c: ToolContext) -> Row | None:
    x = await c.repo.asset(a.serial_no, c.customer_id or "")
    if not x:
        return None
    end = x["warranty_end"]
    return {"serial_no": x["serial_no"], "model": x["model"], "in_warranty": bool(end and c.today <= end),
            "warranty_end": end, "rule": WARRANTY_RULE, "policy": "DOC-POL-WARRANTY-V2"}


async def get_amc_contract(a: ContractArgs, c: ToolContext) -> Row | None:
    k = await c.repo.contract(c.customer_id or "", contract_id=a.contract_id, serial_no=a.serial_no)
    if not k:
        return None
    status = amc_status(k["end_date"], c.today)
    return {"contract_id": k["id"], "plan": k["plan"], "status": status, "start": k["start_date"],
            "end": k["end_date"], "serials": k["serials"], "visits_used": k["visits_used"],
            "renewal_needs_inspection": status == "expired", "coverage_policy": "DOC-POL-AMC"}


async def find_service_centre(a: CityArgs, c: ToolContext) -> Row | None:
    s = await c.repo.service_centre(a.city)
    return s and {"id": s["id"], "name": s["name"], "city": s["city"], "state": s["state"],
                  "covers": a.city.title(), "warehouse": s["home_warehouse"], "phone": PHONE_PLACEHOLDER}


async def get_part_availability(a: PartArgs, c: ToolContext) -> Row | None:
    p = await c.repo.part(a.part_no)
    if not p:
        return None
    sc = await c.repo.service_centre(a.city) if a.city else None
    stock = p["stock"] or {}
    return {"part_no": p["part_no"], "name": p["name"], "price_inr": p["price_inr"], "consumable": p["consumable"],
            "stock": stock, "total": sum(stock.values()), "home_warehouse": sc["home_warehouse"] if sc else None,
            "lead_time_days": p["lead_time_days"]}


def ticket_priority(description: str, *, safety: bool, safety_stop: bool, asked: str | None) -> str:
    """LLD-10 rule; a requested priority can raise the result but never lower it."""
    rule = "P1" if safety or safety_stop or _STOPPED.search(description) else \
        "P2" if _REDUCED.search(description) else "P3"
    return min(rule, asked) if asked else rule


async def create_ticket(a: CreateTicketArgs, c: ToolContext) -> Row | None:
    asset = await c.repo.asset(a.serial_no, c.customer_id) if c.customer_id else None
    if c.customer_id and asset is None:
        return None                                        # identified customer, serial isn't theirs
    stop = bool(a.error_code) and await c.repo.safety_stop((a.error_code or "").upper(), a.serial_no, c.customer_id)
    prio = ticket_priority(a.description, safety=c.safety, safety_stop=stop, asked=a.priority)
    event: Row = {"at": c.today.isoformat(), "event": "created", "contact": a.contact.model_dump(exclude_none=True)}
    if c.customer_id is None:
        event["flag"] = "unverified_customer"
    tid = await c.repo.insert_ticket({
        "customer_id": c.customer_id, "serial_no": a.serial_no, "model": asset["model"] if asset else None,
        "error_code": a.error_code, "priority": prio, "channel": c.channel, "summary": a.description,
        "history": [event]})
    return {"ticket_id": tid, "priority": prio, "status": "new", "unverified": c.customer_id is None}


async def create_parts_quote(a: QuoteArgs, c: ToolContext) -> Row | None:
    part = await c.repo.part(a.part_no)
    if part is None or (a.serial_no and await c.repo.asset(a.serial_no, c.customer_id or "") is None):
        return None
    qid = f"QR-{uuid.uuid4().hex[:12].upper()}"
    await c.repo.insert_quote({"id": qid, "customer_id": c.customer_id, "lines": [
        {"part_no": a.part_no, "qty": a.qty, "serial_no": a.serial_no}]})
    return {"quote_request_id": qid, "part_no": a.part_no, "qty": a.qty}


async def create_lead(a: LeadArgs, c: ToolContext) -> Row | None:
    return {"lead_id": await c.repo.insert_lead({**a.model_dump(exclude={"contact"}), "customer_id": c.customer_id,
                                                 "contact": a.contact.model_dump(exclude_none=True)})}


@dataclass(frozen=True)
class Tool:
    args: type[Args]
    run: Handler
    customer_required: bool


TOOLS: dict[str, Tool] = {
    "get_ticket_status": Tool(TicketArgs, get_ticket_status, True),
    "get_asset": Tool(SerialArgs, get_asset, True),
    "get_warranty": Tool(SerialArgs, get_warranty, True),
    "get_amc_contract": Tool(ContractArgs, get_amc_contract, True),
    "find_service_centre": Tool(CityArgs, find_service_centre, False),
    "get_part_availability": Tool(PartArgs, get_part_availability, False),
    "create_ticket": Tool(CreateTicketArgs, create_ticket, False),
    "create_parts_quote": Tool(QuoteArgs, create_parts_quote, True),
    "create_lead": Tool(LeadArgs, create_lead, False),
}
NEEDS_VERIFICATION = ("To look that up I need to verify your account. Please share your machine's serial number and "
                      "the email address registered with Matsui.")
NOT_FOUND = "I couldn't find that record under your account. Please check the number, or I can connect you with our team."


async def call_tool(name: str, args: Row, ctx: ToolContext) -> ToolResult:
    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult(name, "invalid", message=f"unknown tool {name}")
    try:
        parsed = tool.args.model_validate(args)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, e["loc"])) or e["msg"] for e in exc.errors())
        return ToolResult(name, "invalid", message=f"invalid arguments: {fields}")
    if tool.customer_required and not ctx.customer_id:
        return ToolResult(name, "needs_verification", message=NEEDS_VERIFICATION)
    data = await tool.run(parsed, ctx)
    return ToolResult(name, "ok", data) if data else ToolResult(name, "not_found", message=NOT_FOUND)
