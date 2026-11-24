"""Analytics over the trace table (LLD-16, FR-UI-06, NFR-COST-01). Computed live, so every number matches the traces (D-044).

Only live customer turns count (`mode = 'live'`). Days are Indian Standard Time.
"""

import datetime as dt
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from app.channels.console_store import Row, rows

TZ = "Asia/Kolkata"
TIERS = ("T0", "T1", "T2", "T3", "T4")

T = f"""
WITH t AS (
  SELECT id, coalesce(conversation_id, id) AS cid, coalesce(channel, 'unknown') AS channel, coalesce(answered_tier, 'none') AS tier, latency_ms,
    groundedness, (detail->'verification'->>'coverage')::float AS coverage, coalesce(handoff, false) AS handoff, tokens_in, tokens_out,
    cost_usd, created_at, (created_at AT TIME ZONE '{TZ}')::date AS day, detail,
    coalesce(jsonb_typeof(detail->'handoff') = 'object', false) OR coalesce(detail->>'sent' = 'false', false) AS held
  FROM traces
  WHERE mode = 'live' AND created_at >= CAST(:a AS date)::timestamp AT TIME ZONE '{TZ}'
    AND created_at < (CAST(:b AS date) + 1)::timestamp AT TIME ZONE '{TZ}' AND (:ch = '' OR channel = :ch)),
c AS (
  SELECT cid, min(channel) AS channel, bool_or(handoff) AS handoff,
    bool_or(handoff OR held) OR EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = t.cid AND m.role = 'agent') AS person,
    sum(cost_usd) AS cost, bool_and(cost_usd IS NOT NULL) AS priced, count(*) AS turns, sum(tokens_in + tokens_out) AS tokens
  FROM t GROUP BY cid),
f AS (SELECT f.trace_id, f.rating, f.reason, t.day FROM feedback f JOIN t ON t.id = f.trace_id)
"""
PCT = "percentile_cont(ARRAY[0.05, 0.25, 0.5, 0.75, 0.95]) WITHIN GROUP (ORDER BY latency_ms)"


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def _box(p: list[float] | None, n: int) -> Row:
    vals: list[float | None] = [*p] if p else [None] * 5
    return {"n": n, **dict(zip(("p5", "p25", "p50", "p75", "p95"), vals, strict=True))}


async def summary(conn: AsyncConnection, start: dt.date, end: dt.date, channel: str) -> Row:
    p = {"a": start, "b": end, "ch": channel}

    async def q(sql: str) -> list[Row]:
        return await rows(conn, T + sql, **p)

    tot = (await q(f"""SELECT count(*) AS turns, {PCT} AS pct, avg(groundedness) AS ground, count(groundedness) AS judged,
        avg(coverage) AS coverage, count(coverage) AS covered, sum(cost_usd) AS cost, count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced,
        coalesce(sum(tokens_in), 0) AS tin, coalesce(sum(tokens_out), 0) AS tout FROM t"""))[0]
    con = (await q("""SELECT count(*) AS n, count(*) FILTER (WHERE handoff) AS handoff, count(*) FILTER (WHERE NOT person) AS deflected,
        avg(cost) FILTER (WHERE priced) AS avg_cost, count(*) FILTER (WHERE priced) AS priced FROM c"""))[0]
    channels = await q("""SELECT t.channel, count(DISTINCT cid) AS conversations, count(*) AS turns FROM t GROUP BY 1 ORDER BY 1""")
    tiers = await q(f"""SELECT tier, count(*) AS turns, {PCT} AS pct, sum(cost_usd) AS cost, count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced,
        coalesce(sum(tokens_in), 0) AS tokens_in, coalesce(sum(tokens_out), 0) AS tokens_out FROM t GROUP BY 1 ORDER BY 1""")
    days = await q("""SELECT d::date AS day, count(t.id) AS turns, count(DISTINCT t.cid) AS conversations,
        count(t.id) FILTER (WHERE tier = 'T0') AS t0, count(t.id) FILTER (WHERE tier = 'T1') AS t1, count(t.id) FILTER (WHERE tier = 'T2') AS t2,
        count(t.id) FILTER (WHERE tier = 'T3') AS t3, count(t.id) FILTER (WHERE tier = 'T4') AS t4,
        count(t.id) FILTER (WHERE tier NOT IN ('T0', 'T1', 'T2', 'T3', 'T4')) AS other,
        avg(groundedness) AS groundedness, sum(cost_usd) AS cost,
        (SELECT count(*) FROM f WHERE f.day = d::date AND rating > 0) AS up, (SELECT count(*) FROM f WHERE f.day = d::date AND rating < 0) AS down
        FROM generate_series(CAST(:a AS date), CAST(:b AS date), interval '1 day') d LEFT JOIN t ON t.day = d::date GROUP BY d ORDER BY d""")
    fb = (await q("SELECT count(*) FILTER (WHERE rating > 0) AS up, count(*) FILTER (WHERE rating < 0) AS down FROM f"))[0]
    models = await q("""SELECT x->>'model' AS model, count(*) AS calls, sum((x->>'tokens_in')::int) AS tokens_in,
        sum((x->>'tokens_out')::int) AS tokens_out, sum((x->>'cost_usd')::float) AS cost, bool_or(x ? 'estimated') AS estimated
        FROM t JOIN spans s ON s.trace_id = t.id, jsonb_array_elements(s.attrs->'llm_calls') x GROUP BY 1 ORDER BY cost DESC NULLS LAST, 1""")
    costly = await q("""SELECT c.cid AS id, c.channel, c.turns, c.tokens, c.cost, v.subject, EXISTS (SELECT 1 FROM conversations z WHERE z.id = c.cid)
        AS available FROM c LEFT JOIN conversations v ON v.id = c.cid WHERE c.priced AND c.cost > 0 ORDER BY c.cost DESC, c.cid LIMIT 10""")
    turns, n = tot["turns"], con["n"]
    return {
        "from": start, "to": end, "channel": channel or None, "generated_at": dt.datetime.now(dt.UTC),
        "volume": {"conversations": n, "turns": turns, "by_channel": channels},
        "tier_mix": [{"tier": r["tier"], "turns": r["turns"], "share": _ratio(r["turns"], turns)} for r in tiers],
        "latency_p50_p95": {"overall": _box(tot["pct"], turns), "by_tier": [{"tier": r["tier"], **_box(r["pct"], r["turns"])} for r in tiers]},
        "deflection": {"rate": _ratio(con["deflected"], n), "conversations": con["deflected"]},
        "handoff_rate": {"rate": _ratio(con["handoff"], n), "conversations": con["handoff"]},
        "groundedness": {"score": _num(tot["ground"]), "judged": tot["judged"], "citation_coverage": _num(tot["coverage"]), "checked": tot["covered"]},
        "feedback": {**fb, "positive": _ratio(fb["up"], fb["up"] + fb["down"])},
        "cost": {"total": _num(tot["cost"]), "per_conversation": _num(con["avg_cost"]), "priced_conversations": con["priced"], "unpriced_turns": tot["unpriced"],
                 "tokens_in": tot["tin"], "tokens_out": tot["tout"], "by_model": [{**r, "cost": _num(r["cost"])} for r in models],
                 "by_tier": [{"tier": r["tier"], "turns": r["turns"], "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"], "unpriced": r["unpriced"],
                              "total": _num(r["cost"]), "per_turn": float(r["cost"]) / (r["turns"] - r["unpriced"]) if r["cost"] is not None else None}
                             for r in tiers],
                 "top_conversations": [{**r, "cost": _num(r["cost"])} for r in costly]},
        "by_day": [{**r, "groundedness": _num(r["groundedness"]), "cost": _num(r["cost"])} for r in days],
    }


async def flagged(conn: AsyncConnection, start: dt.date, end: dt.date, channel: str, limit: int) -> list[Row]:
    """Answers worth a look: thumbs down, failed checks, held back, or poorly grounded. Newest first."""
    return await rows(conn, T + """, g AS (SELECT t.*, (SELECT count(*) FROM f WHERE f.trace_id = t.id AND rating < 0) AS down,
          (SELECT max(reason) FROM f WHERE f.trace_id = t.id AND rating < 0) AS reason FROM t)
        SELECT g.id AS trace_id, v.id AS conversation_id, g.channel, g.tier, g.created_at, g.groundedness, g.reason,
          array_remove(ARRAY[CASE WHEN down > 0 THEN 'thumbs down' END, CASE WHEN detail->'verification'->>'passed' = 'false' THEN 'failed checks' END,
            CASE WHEN detail->>'suggested' IS NOT NULL THEN 'held back' END, CASE WHEN groundedness < 0.8 THEN 'low groundedness' END,
            CASE WHEN detail->>'failed' = 'true' THEN 'turn failed' END], NULL) AS reasons,
          (SELECT left(m.content, 160) FROM messages m WHERE m.trace_id = g.id AND m.role = 'assistant' ORDER BY m.created_at LIMIT 1) AS answer
        FROM g LEFT JOIN conversations v ON v.id = g.cid
        WHERE down > 0 OR detail->'verification'->>'passed' = 'false' OR detail->>'suggested' IS NOT NULL OR groundedness < 0.8
          OR detail->>'failed' = 'true'
        ORDER BY g.created_at DESC, g.id LIMIT :n""", a=start, b=end, ch=channel, n=limit)


async def benchmark(conn: AsyncConnection) -> Row:
    """Latest run per mode. `eval_runs.summary` (written by the eval harness, BS-29) holds: n, correctness, groundedness, p50_ms, p95_ms,
    tokens, cost_usd and by_type {type: {n, correctness}}. Missing fields stay empty."""
    runs = await rows(conn, "SELECT id, mode, started_at, summary, report_uri FROM eval_runs ORDER BY started_at DESC NULLS LAST, id LIMIT 50")
    latest: dict[str, Row] = {}
    for r in runs:
        if isinstance(r["summary"], dict):
            latest.setdefault(r["mode"], r)
    keys = ("n", "correctness", "groundedness", "p50_ms", "p95_ms", "tokens", "cost_usd")
    types: dict[str, Row] = {}
    for mode, r in latest.items():
        for typ, v in (r["summary"].get("by_type") or {}).items():
            types.setdefault(typ, {"type": typ, "modes": {}})["modes"][mode] = v
    return {"runs": [{k: r[k] for k in ("id", "mode", "started_at", "report_uri")} for r in runs],
            "by_mode": [{"mode": m, "started_at": r["started_at"], **{k: r["summary"].get(k) for k in keys}} for m, r in sorted(latest.items())],
            "by_type": [types[k] for k in sorted(types)]}
