"""JSON logs (NFR-OBS-02) and the dev debug route (BS-11)."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.obs import logging as obslog
from app.obs.tracing import TraceRecorder


@pytest.fixture
def json_logs(capsys, monkeypatch):
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    monkeypatch.setattr(obslog, "_configured", False)
    obslog.configure_logging("INFO")
    yield lambda: [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    root.handlers[:], _ = saved
    root.setLevel(saved[1])


@pytest.mark.req("NFR-OBS-02")
def test_logs_are_json_with_severity_component_trace_and_masked_pii(json_logs):
    rec = TraceRecorder()
    token = obslog.request_id_var.set("req-123")
    with rec:
        logging.getLogger("mcs.test").warning("customer rohit@demo.example called +91 98765 43210 about %s", "E04")
    obslog.request_id_var.reset(token)
    [line] = [x for x in json_logs() if x.get("component") == "mcs.test"]
    assert line["severity"] == "WARNING" and line["trace_id"] == str(rec.trace_id) and line["request_id"] == "req-123"
    assert line["message"] == "customer [email] called [phone] about E04" and "timestamp" in line


def test_mask_pii_covers_gstin():
    assert obslog.mask_pii("GSTIN 27AAPFU0939F1ZV ok") == "GSTIN [gstin] ok"


def _client(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app.config.settings import get_settings
    from app.main import create_app
    get_settings.cache_clear()
    return TestClient(create_app())


def test_debug_run_returns_trace_with_node_spans(monkeypatch):
    client = _client(monkeypatch, ENV="dev", DATABASE_URL="", DB_PASSWORD="")
    r = client.post("/v1/debug/run", json={"text": "status of SR-2026-00105?", "persist": False,
                                           "meta": {"stub": {"intents": [{"name": "service.ticket_status",
                                                                          "confidence": 0.97}]}}})
    assert r.status_code == 200 and r.headers["X-Request-ID"]
    body = r.json()
    assert body["path"] == [s["node"] for s in body["trace"]["spans"]]
    assert body["trace"]["answered_tier"] == "T0" and body["response"]["text"] == "[stub] tool result"
    assert body["persisted"] is False and body["trace"]["mode"] == "debug"


def test_debug_route_is_not_mounted_outside_dev(monkeypatch):
    assert _client(monkeypatch, ENV="demo").post("/v1/debug/run", json={"text": "x"}).status_code == 404
    assert _client(monkeypatch, ENV="demo", DEBUG_API="true").post(
        "/v1/debug/run", json={"text": "x", "persist": False}).status_code == 200
