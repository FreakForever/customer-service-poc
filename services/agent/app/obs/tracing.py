"""Traces and spans (LLD-16, NFR-OBS-01).

`@traced("node")` wraps an async function and records a span (start, duration, model, tokens, cache_hit, attrs)
on the current `TraceRecorder` (a context variable, so concurrent requests never mix spans). Code inside a span
adds details with `span_set(model=..., tokens_in=...)`. `save_trace` writes the root `traces` row and its
`spans` rows to Postgres. Without an active recorder, `@traced` is a no-op wrapper.
"""

import contextvars
import datetime as dt
import functools
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, ParamSpec, Self, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

P = ParamSpec("P")
R = TypeVar("R")

_recorder: contextvars.ContextVar["TraceRecorder | None"] = contextvars.ContextVar("trace_recorder", default=None)
_span: contextvars.ContextVar["Span | None"] = contextvars.ContextVar("trace_span", default=None)


@dataclass
class Span:
    node: str
    started_at: dt.datetime
    duration_ms: int = 0
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cache_hit: bool | None = None
    status: str = "ok"                       # ok | error | timeout
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceRecorder:
    trace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation_id: uuid.UUID | None = None
    channel: str | None = None
    mode: str = "live"
    started: float = field(default_factory=time.perf_counter)
    spans: list[Span] = field(default_factory=list)
    root: dict[str, Any] = field(default_factory=dict)   # answered_tier, intents, confidence, handoff, ...
    first_token_ms: int | None = None

    def __enter__(self) -> Self:
        self._token = _recorder.set(self)
        return self

    def __exit__(self, *exc: object) -> None:
        _recorder.reset(self._token)

    @property
    def latency_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def mark_first_token(self) -> None:
        if self.first_token_ms is None:
            self.first_token_ms = self.latency_ms

    def totals(self) -> dict[str, int]:
        return {"tokens_in": sum(s.tokens_in for s in self.spans), "tokens_out": sum(s.tokens_out for s in self.spans)}

    def as_dict(self) -> dict[str, Any]:
        return {"trace_id": str(self.trace_id), "conversation_id": str(self.conversation_id) if self.conversation_id else None,
                "channel": self.channel, "mode": self.mode, "latency_ms": self.latency_ms,
                "first_token_ms": self.first_token_ms, **self.totals(), **self.root,
                "spans": [{**asdict(s), "started_at": s.started_at.isoformat()} for s in self.spans]}


def current_recorder() -> TraceRecorder | None:
    return _recorder.get()


def current_trace_id() -> str | None:
    rec = _recorder.get()
    return str(rec.trace_id) if rec else None


def span_set(**fields: Any) -> None:
    """Attach details to the innermost open span: model, tokens_in, tokens_out, cache_hit, or free-form attrs."""
    s = _span.get()
    if s is None:
        return
    for k, v in fields.items():
        if k in ("model", "tokens_in", "tokens_out", "cache_hit", "status"):
            setattr(s, k, v)
        else:
            s.attrs[k] = v


def traced(node: str) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    def deco(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            rec = _recorder.get()
            if rec is None:
                return await fn(*args, **kwargs)
            span = Span(node=node, started_at=dt.datetime.now(dt.UTC))
            token = _span.set(span)
            t0 = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            except TimeoutError:
                span.status = "timeout"
                raise
            except BaseException as exc:
                span.status = "error"
                span.attrs["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                raise
            finally:
                span.duration_ms = int((time.perf_counter() - t0) * 1000)
                _span.reset(token)
                rec.spans.append(span)
        return wrapper
    return deco


def _json(v: Any) -> str:
    return json.dumps(v, default=str)


async def save_trace(conn: AsyncConnection, rec: TraceRecorder) -> None:
    r = rec.root
    await conn.execute(text("""
        INSERT INTO traces (id, conversation_id, channel, answered_tier, intents, confidence, groundedness, handoff,
                            latency_ms, first_token_ms, tokens_in, tokens_out, cost_usd, mode, detail)
        VALUES (:id, :conv, :channel, :tier, :intents, :conf, :ground, :handoff, :lat, :ft, :tin, :tout, :cost,
                :mode, CAST(:detail AS jsonb))"""),
        {"id": rec.trace_id, "conv": rec.conversation_id, "channel": rec.channel, "tier": r.get("answered_tier"),
         "intents": r.get("intents") or [], "conf": r.get("confidence"), "ground": r.get("groundedness"),
         "handoff": r.get("handoff"), "lat": rec.latency_ms, "ft": rec.first_token_ms, 
             "tin": rec.totals()["tokens_in"], "tout": rec.totals()["tokens_out"],
         "cost": r.get("cost_usd"), "mode": rec.mode, "detail": _json(r.get("detail", {}))})
    if rec.spans:
        await conn.execute(text("""
            INSERT INTO spans (trace_id, node, started_at, duration_ms, model, tokens_in, tokens_out, cache_hit, attrs)
            VALUES (:t, :node, :start, :dur, :model, :tin, :tout, :cache, CAST(:attrs AS jsonb))"""),
            [{"t": rec.trace_id, "node": s.node, "start": s.started_at, "dur": s.duration_ms, "model": s.model,
              "tin": s.tokens_in, "tout": s.tokens_out, "cache": s.cache_hit,
              "attrs": _json({**s.attrs, "status": s.status})} for s in rec.spans])
