"""Settings and channel policy profiles (BS-02, LLD-2)."""

import pytest

from app.config.settings import Settings
from app.orchestrator.policies import get_policy, load_policies


@pytest.mark.req("NFR-SEC-02")
def test_secrets_are_masked_in_repr_and_dump():
    s = Settings(_env_file=None, neo4j_password="hunter2", db_password="pw-123", anthropic_api_key="sk-x")
    text = repr(s) + str(s.model_dump())
    assert "hunter2" not in text and "pw-123" not in text and "sk-x" not in text
    assert s.neo4j_password.get_secret_value() == "hunter2"


def test_settings_defaults_match_lld():
    s = Settings(_env_file=None, provider="vertex")
    assert (s.gcp_region, s.embed_dim, s.mail_adapter, s.retention_days) == ("asia-south1", 768, "demo", 30)
    assert s.resolved_vertex_location == "asia-south1"
    assert Settings(_env_file=None, vertex_location="global").resolved_vertex_location == "global"


def test_unknown_provider_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, provider="nope")


@pytest.mark.req("FR-OR-04")
def test_policies_load_with_lld_values():
    p = load_policies()
    assert set(p) == {"chat", "email", "voice"}
    assert p["chat"].budget_ms["total"] == 6000 and p["chat"].on_low_confidence == "handoff"
    assert p["email"].thresholds["auto_send"] == 0.80 and p["email"].verification.judge == "sync"
    assert p["chat"].verification.judge_sample_rate == 0.2


def test_voice_inherits_chat_and_overrides_budget():
    voice, chat = get_policy("voice"), get_policy("chat")
    assert voice.budget_ms == {"total": 3000, "first_token": 800, "status_event": 500}
    assert voice.node_timeouts_ms == chat.node_timeouts_ms and voice.thresholds == chat.thresholds


def test_timeout_lookup_and_unknown_channel():
    chat = get_policy("chat")
    assert chat.timeout_ms("generate") == 4500 and chat.timeout_ms("judge") == 6000  # no judge entry -> total
    with pytest.raises(KeyError):
        get_policy("fax")


def test_inheritance_cycle_detected(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text("a: {inherits: b}\nb: {inherits: a}\n")
    with pytest.raises(ValueError, match="cycle"):
        load_policies(f)
