"""Provider abstraction, FakeProvider and resilience chain (BS-02, LLD-2)."""

import asyncio
import math
import socket
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.config.settings import Settings
from app.providers import (
    ProviderError,
    ProviderRefusal,
    ProviderTimeout,
    ProviderUnavailable,
    ResilientEmbedder,
    ResilientLLM,
    build_embedder,
    build_llm,
    get_llm,
)
from app.providers.base import LLM, Embedder, Msg, parse_json, split_system
from app.providers.fake import FakeProvider, FakeScriptMissing, hash_embedding

pytestmark = pytest.mark.req("NFR-REL-01")


class Intent(BaseModel):
    intent: str
    confidence: float


def msgs(text="hello", pid=None):
    return [Msg(role="system", content="You are a router.", prompt_id=pid), Msg(role="user", content=text)]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


# ---------- FakeProvider ----------

def test_fake_satisfies_protocols():
    f = FakeProvider()
    assert isinstance(f, LLM) and isinstance(f, Embedder)


async def test_fake_embeddings_deterministic_normalised_and_similar():
    f = FakeProvider(dim=64)
    a, b, c = await f.embed(["washing machine not draining", "washing machine won't drain", "AMC renewal price"],
                            kind="document")
    assert a == hash_embedding("washing machine not draining", 64)
    assert len(a) == 64 and math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-9)
    assert cos(a, b) > cos(a, c)


async def test_fake_scripted_text_dict_list_callable_and_exception():
    f = FakeProvider(script={
        "t": "scripted", "seq": ["one", "two"], "fn": lambda m: m[-1].content.upper(),
        "boom": ProviderError("bad", transient=False),
    })
    assert (await f.complete(msgs(pid="t"), model="m", timeout_ms=100)).text == "scripted"
    assert [(await f.complete(msgs(pid="seq"), model="m", timeout_ms=100)).text for _ in range(2)] == ["one", "two"]
    assert (await f.complete(msgs("hi", pid="fn"), model="m", timeout_ms=100)).text == "HI"
    with pytest.raises(ProviderError):
        await f.complete(msgs(pid="boom"), model="m", timeout_ms=100)


async def test_fake_unscripted_text_echoes_and_schema_requires_script():
    f = FakeProvider(script={"router.v1": {"intent": "troubleshoot", "confidence": 0.9}})
    out = await f.complete(msgs("my fridge is noisy"), model="m", timeout_ms=100)
    assert out.text == "[fake:default] my fridge is noisy"
    parsed = await f.complete(msgs(pid="router.v1"), model="m", schema=Intent, timeout_ms=100)
    assert parsed.parsed == Intent(intent="troubleshoot", confidence=0.9)
    with pytest.raises(FakeScriptMissing):
        await f.complete(msgs(pid="other"), model="m", schema=Intent, timeout_ms=100)


async def test_fake_stream_concatenates_to_completion_text():
    f = FakeProvider(script={"s": "the quick brown fox"})
    chunks = [c async for c in f.stream(msgs(pid="s"), model="m", timeout_ms=100)]
    assert len(chunks) == 4 and "".join(chunks) == "the quick brown fox"


# ---------- ResilientLLM ----------

async def test_first_attempt_success():
    out = await ResilientLLM(FakeProvider(script={"p": "ok"})).complete(msgs(pid="p"), model="fast", timeout_ms=200)
    assert (out.text, out.attempts, out.fallback_used) == ("ok", 1, False)


async def test_one_transient_failure_is_retried_on_same_model():
    primary = FakeProvider(script={"p": "ok"}).fail_next(1)
    out = await ResilientLLM(primary, fallback=FakeProvider(name="fb")).complete(msgs(pid="p"), model="fast",
                                                                              timeout_ms=200)
    assert (out.text, out.attempts, out.fallback_used, out.provider) == ("ok", 2, False, "fake")
    assert [c["model"] for c in primary.calls] == ["fast", "fast"]


async def test_two_failures_go_to_fallback_provider_and_model():
    primary = FakeProvider().fail_next(2)
    fb = FakeProvider(name="fb", script={"p": "from fallback"})
    out = await ResilientLLM(primary, fallback=fb, fallback_model="backup").complete(msgs(pid="p"), model="fast",
                                                                                    timeout_ms=200)
    assert (out.text, out.provider, out.model, out.attempts, out.fallback_used) == (
        "from fallback", "fb", "backup", 3, True)


async def test_fallback_model_on_same_provider_when_no_fallback_provider():
    primary = FakeProvider(script={"p": ["x", "y", "z"]}).fail_next(2)
    out = await ResilientLLM(primary, fallback_model="backup").complete(msgs(pid="p"), model="fast", timeout_ms=200)
    assert out.model == "backup" and out.fallback_used and [c["model"] for c in primary.calls][-1] == "backup"


async def test_timeout_counts_as_transient_failure():
    slow = FakeProvider(delay_ms=200)
    fb = FakeProvider(name="fb", script={"p": "fast answer"})
    out = await ResilientLLM(slow, fallback=fb).complete(msgs(pid="p"), model="m", timeout_ms=30)
    assert out.text == "fast answer" and out.attempts == 3


async def test_non_transient_error_skips_retry_and_uses_fallback():
    primary = FakeProvider().fail_next(1, ProviderRefusal("declined"))
    fb = FakeProvider(name="fb", script={"p": "ok"})
    out = await ResilientLLM(primary, fallback=fb).complete(msgs(pid="p"), model="m", timeout_ms=200)
    assert len(primary.calls) == 1 and out.provider == "fb" and out.attempts == 2


async def test_all_attempts_fail_raises_provider_unavailable_with_each_error():
    primary = FakeProvider().fail_next(2)
    fb = FakeProvider(name="fb").fail_next(1)
    with pytest.raises(ProviderUnavailable) as ei:
        await ResilientLLM(primary, fallback=fb).complete(msgs(), model="m", timeout_ms=200)
    assert len(ei.value.errors) == 3 and all(isinstance(e, ProviderTimeout) for e in ei.value.errors)


async def test_no_fallback_configured_fails_after_one_retry():
    primary = FakeProvider().fail_next(5)
    with pytest.raises(ProviderUnavailable):
        await ResilientLLM(primary).complete(msgs(), model="m", timeout_ms=200)
    assert len(primary.calls) == 2


async def test_malformed_structured_output_is_retried():
    f = FakeProvider(script={"r": ["not json", '{"intent": "parts", "confidence": 0.7}']})
    out = await ResilientLLM(f).complete(msgs(pid="r"), model="m", schema=Intent, timeout_ms=200)
    assert out.parsed == Intent(intent="parts", confidence=0.7) and out.attempts == 2


async def test_stream_falls_back_before_first_chunk():
    primary = FakeProvider().fail_next(2)
    fb = FakeProvider(name="fb", script={"p": "hello there"})
    chunks = [c async for c in ResilientLLM(primary, fallback=fb).stream(msgs(pid="p"), model="m", timeout_ms=200)]
    assert "".join(chunks) == "hello there"


async def test_stream_raises_when_every_start_fails():
    with pytest.raises(ProviderUnavailable):
        _ = [c async for c in ResilientLLM(FakeProvider().fail_next(3)).stream(msgs(), model="m", timeout_ms=200)]


# ---------- ResilientEmbedder ----------

async def test_embedder_retry_then_success_and_shape():
    e = ResilientEmbedder(FakeProvider(dim=32).fail_next(1), timeout_ms=200)
    vecs = await e.embed(["a b", "c d"], kind="query")
    assert len(vecs) == 2 and all(len(v) == 32 for v in vecs)
    assert await e.embed([], kind="query") == []


async def test_embedder_fallback_and_dim_guard():
    e = ResilientEmbedder(FakeProvider(dim=16).fail_next(2), fallback=FakeProvider(name="fb", dim=16))
    assert len((await e.embed(["x"], kind="document"))[0]) == 16
    with pytest.raises(ValueError):
        ResilientEmbedder(FakeProvider(dim=16), fallback=FakeProvider(dim=8))


# ---------- factory ----------

def test_factory_builds_fake_chain_from_settings():
    s = Settings(_env_file=None, provider="fake", fallback_provider="fake", model_fallback="fb-model", embed_dim=24)
    llm, emb = build_llm(s), build_embedder(s)
    assert isinstance(llm.primary, FakeProvider) and isinstance(llm.fallback, FakeProvider)
    assert llm.fallback_model == "fb-model" and emb.dim == 24


def test_default_test_environment_uses_fake_provider():
    assert isinstance(get_llm().primary, FakeProvider)


def test_anthropic_llm_is_paired_with_vertex_embeddings(monkeypatch):
    from app import providers

    made = []
    monkeypatch.setattr(providers, "make_provider",
                        lambda name, s: made.append(name) or FakeProvider(name=name, dim=s.embed_dim))
    providers.build_embedder(Settings(_env_file=None, provider="anthropic"))
    assert made == ["vertex"]


# ---------- adapters (SDK clients stubbed; no network) ----------

class _StubMessages:
    def __init__(self, resp):
        self.resp, self.kwargs = resp, None

    async def create(self, **kw):
        self.kwargs = kw
        return self.resp


def _claude_resp(stop="end_turn", text="hi"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=5, output_tokens=2))


async def test_anthropic_adapter_request_shape_and_refusal():
    from app.providers.anthropic import AnthropicProvider

    p = AnthropicProvider(via="api", api_key="test")
    p._client = SimpleNamespace(messages=_StubMessages(_claude_resp()))
    out = await p.complete(msgs("q"), model="claude-haiku-5-5", temperature=0.7, timeout_ms=1500)
    kw = p._client.messages.kwargs
    assert out.text == "hi" and out.input_tokens == 5
    assert kw["system"] == "You are a router." and kw["messages"] == [{"role": "user", "content": "q"}]
    assert "temperature" not in kw and kw["timeout"] == 1.5
    p._client = SimpleNamespace(messages=_StubMessages(_claude_resp(stop="refusal")))
    with pytest.raises(ProviderRefusal):
        await p.complete(msgs(), model="claude-haiku-5-5", timeout_ms=100)


async def test_openai_compat_adapter_parses_schema():
    from app.providers.openai_compat import OpenAICompatProvider

    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"intent":"sales","confidence":1}'),
                                                    finish_reason="stop")],
                           usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4))
    p = OpenAICompatProvider(base_url="http://localhost:9/v1")
    stub = _StubMessages(resp)
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=stub))
    out = await p.complete(msgs(), model="m", schema=Intent, timeout_ms=100)
    assert out.parsed == Intent(intent="sales", confidence=1.0)
    assert stub.kwargs["response_format"] == {"type": "json_object"}
    assert stub.kwargs["messages"][0]["content"].startswith("Reply with a single JSON object")


def test_helpers_split_system_and_parse_fenced_json():
    system, rest = split_system([Msg(role="system", content="a"), Msg(role="user", content="u"),
                                 Msg(role="system", content="b")])
    assert system == "a\n\nb" and [m.role for m in rest] == ["user"]
    assert parse_json(Intent, '```json\n{"intent":"x","confidence":0.5}\n```', provider="t").intent == "x"


# ---------- offline guarantee ----------

def test_network_is_blocked_in_unit_tests():
    with pytest.raises(RuntimeError, match="network access is disabled"):
        socket.create_connection(("example.com", 443), timeout=1)


async def test_whole_chain_runs_without_event_loop_leaks():
    llm = ResilientLLM(FakeProvider(script={"p": "ok"}))
    results = await asyncio.gather(*(llm.complete(msgs(pid="p"), model="m", timeout_ms=200) for _ in range(20)))
    assert {r.text for r in results} == {"ok"}


def test_vertex_adapter_client_modes(monkeypatch):
    """Embeddings always on Vertex in VERTEX_LOCATION; generation on the API key (D-032) or VERTEX_LLM_LOCATION."""
    from google import genai

    from app.providers import make_provider

    made = []
    monkeypatch.setattr(genai, "Client", lambda **kw: made.append(kw) or SimpleNamespace(**kw))
    p = make_provider("vertex", Settings(_env_file=None, gcp_project="p", gcp_region="asia-south1",
                                         gemini_via="api", gemini_api_key="k"))
    assert made == [{"vertexai": True, "project": "p", "location": "asia-south1"}, {"api_key": "k"}]
    assert p._llm is not p._client and p.thinking_budgets == {"gemini-3.5-flash-lite": 0}
    made.clear()
    make_provider("vertex", Settings(_env_file=None, gcp_project="p", gcp_region="asia-south1", gemini_via="vertex",
                                     gemini_api_key="ignored", vertex_llm_location="global"))
    assert made == [{"vertexai": True, "project": "p", "location": "asia-south1"},
                    {"vertexai": True, "project": "p", "location": "global"}]


async def test_vertex_deadline_floor_but_our_budget_still_applies(monkeypatch):
    """The Gemini API rejects deadlines < 10 s; a 100 ms budget must still cut the call off at 100 ms."""
    from app.providers.vertex import VertexProvider

    seen = []

    class Models:
        async def generate_content(self, **kw):
            seen.append(kw["config"].http_options.timeout)
            await asyncio.sleep(5)
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **kw: SimpleNamespace(aio=SimpleNamespace(models=Models())))
    p = VertexProvider(project="p", location="asia-south1", api_key="k")
    start = asyncio.get_running_loop().time()
    with pytest.raises(ProviderUnavailable):
        await ResilientLLM(p).complete(msgs(), model="gemini-3.5-flash-lite", timeout_ms=100)
    assert set(seen) == {10_000} and asyncio.get_running_loop().time() - start < 1
