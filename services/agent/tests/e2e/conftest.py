"""Fixtures shared by the API end-to-end tests: `url` (rebuilt, seeded mcs_test) and `http` (API with the scripted graph)."""

from tests.e2e.test_chat_api import http, url

__all__ = ["http", "url"]
