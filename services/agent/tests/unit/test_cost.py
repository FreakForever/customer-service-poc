"""Token and cost recording per request (BS-27; NFR-COST-01)."""

import pytest

from app.config.settings import Settings
from app.obs.cost import cost_usd, prices
from app.obs.tracing import TraceRecorder, traced
from app.providers import Msg, ResilientLLM
from app.providers.base import Completion

MSG = [Msg(role="user", content="x" * 400)]


class Stub:
    name = "stub"

    async def complete(self, messages, *, model, **kw):
        return Completion(text="ok", model=model, provider="stub", input_tokens=1000, output_tokens=100)

    async def stream(self, messages, *, model, timeout_ms):
        for t in ("a" * 20, "b" * 20):
            yield t


def test_prices():
    assert cost_usd("gemini-3.5-flash-lite", 1_000_000, 1_000_000) == pytest.approx(2.80)
    assert cost_usd("gemma-4-26b-a4b-it", 5, 5) == 0 and cost_usd("nope", 5, 5) is None
    s = Settings(_env_file=None)
    assert {s.model_fast, s.model_strong} <= set(prices())                 # defaults are always priced


@pytest.mark.req("NFR-COST-01")
async def test_every_call_is_counted_once_on_its_span():
    llm = ResilientLLM(Stub())

    @traced("a")
    async def a():
        await llm.complete(MSG, model="gemini-3.5-flash-lite", timeout_ms=1000)
        await llm.complete(MSG, model="gemini-3.1-flash-lite", timeout_ms=1000)

    @traced("b")
    async def b():
        assert [t async for t in llm.stream(MSG, model="gemini-3.5-flash-lite", timeout_ms=1000)]
    with TraceRecorder() as rec:
        await a()
        await b()
    sa, sb = rec.spans
    assert (sa.tokens_in, sa.tokens_out, len(sa.attrs["llm_calls"])) == (2000, 200, 2)
    assert (sb.tokens_in, sb.tokens_out, sb.attrs["llm_calls"][0]["estimated"]) == (100, 10, True)
    assert rec.totals() == {"tokens_in": 2100, "tokens_out": 210}
    assert rec.cost == pytest.approx((1100 * .30 + 110 * 2.5 + 1000 * .25 + 100 * 1.5) / 1e6)
    assert rec.as_dict()["cost_usd"] == rec.cost


async def test_unpriced_model_makes_the_cost_unknown_not_zero():
    with TraceRecorder() as rec:
        await ResilientLLM(Stub()).complete(MSG, model="mystery", timeout_ms=1000)
    assert rec.cost is None and rec.totals()["tokens_in"] == 1000
    with TraceRecorder() as free:
        pass
    assert free.cost == 0


async def test_calls_outside_a_request_are_ignored():
    assert (await ResilientLLM(Stub()).complete(MSG, model="mystery", timeout_ms=1000)).text == "ok"
