"""Test defaults: fake provider and no network for unit tests (NFR-REL-01: suite runs offline).

Integration tests (tests/integration, BS-03+) opt back in to the network with `@pytest.mark.network`.
"""

import os
import socket

import pytest

os.environ["PROVIDER"] = "fake"
os.environ.setdefault("FALLBACK_PROVIDER", "")


class NetworkBlocked(RuntimeError):
    pass


def _blocked(*_a, **_k):
    raise NetworkBlocked("network access is disabled in unit tests (PROVIDER=fake); mark the test @pytest.mark.network")


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        yield
        return
    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        if self.family == getattr(socket, "AF_UNIX", None):
            return real_connect(self, address)
        _blocked()

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    yield


@pytest.fixture(autouse=True)
def _fresh_caches():
    from app.config.settings import get_settings
    from app.providers import get_embedder, get_llm

    for f in (get_settings, get_llm, get_embedder):
        f.cache_clear()
    yield
    for f in (get_settings, get_llm, get_embedder):
        f.cache_clear()
