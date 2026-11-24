"""Analytics routes (LLD-14; FR-UI-06, NFR-COST-01). Agent role."""

import datetime as dt
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics import queries
from app.api.chat import EngineDep
from app.api.staff import require

router = APIRouter(prefix="/v1/analytics", tags=["analytics"], dependencies=[Depends(require("agent"))])
MAX_DAYS = 92


class Period:
    def __init__(self, start: Annotated[dt.date | None, Query(alias="from")] = None, end: Annotated[dt.date | None, Query(alias="to")] = None,
                 channel: Literal["", "chat", "email", "voice"] = "") -> None:
        self.end = end or dt.datetime.now(ZoneInfo(queries.TZ)).date()
        self.start = start or (self.end - dt.timedelta(days=6) if self.end > dt.date(1, 1, 7) else self.end)
        self.channel = channel
        if not 0 <= (self.end - self.start).days < MAX_DAYS or self.end.year > 9000 or self.start.year < 1900:
            raise HTTPException(422, f"choose a period of 1 to {MAX_DAYS} days")


PeriodDep = Annotated[Period, Depends()]


@router.get("/summary")
async def summary(p: PeriodDep, engine: EngineDep) -> dict[str, Any]:
    async with engine.connect() as raw:
        # one snapshot: a turn that arrives meanwhile is in all of the numbers or in none
        conn = await raw.execution_options(isolation_level="REPEATABLE READ")
        return await queries.summary(conn, p.start, p.end, p.channel)


@router.get("/flagged")
async def flagged(p: PeriodDep, engine: EngineDep, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict[str, Any]:
    async with engine.connect() as conn:
        return {"items": await queries.flagged(conn, p.start, p.end, p.channel, limit)}


@router.get("/benchmark")
async def benchmark(engine: EngineDep) -> dict[str, Any]:
    async with engine.connect() as conn:
        return await queries.benchmark(conn)
