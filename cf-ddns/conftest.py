# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures and helpers for the cf-ddns test suite."""

from unittest.mock import MagicMock

import pytest
import requests

import cf_ddns


class FakeResponse:
    """Minimal stand-in for requests.Response.

    raise_for_status() raises requests.HTTPError with the response attached
    for status >= 400, mirroring the real library closely enough for the
    status-classification tests.
    """

    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json_data is None:
            raise requests.exceptions.JSONDecodeError("no JSON", "", 0)
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


@pytest.fixture
def required_env(monkeypatch):
    """Set the three required environment variables to test values."""
    monkeypatch.setenv("CF_DDNS_TOKEN", "test_token")
    monkeypatch.setenv("CF_DDNS_ZONE_ID", "test_zone")
    monkeypatch.setenv("CF_DDNS_HOST", "test.example.com")


@pytest.fixture
def sleep_calls(monkeypatch):
    """Record time.sleep() delays instead of sleeping."""
    calls = []
    monkeypatch.setattr(cf_ddns.time, "sleep", calls.append)
    return calls


@pytest.fixture
def api_error_metric(monkeypatch):
    """Replace the Cloudflare API error counter with a mock."""
    metric = MagicMock()
    monkeypatch.setitem(cf_ddns.prometheus_metrics, "cf_api_error_counter", metric)
    return metric


@pytest.fixture
def ip_error_metric(monkeypatch):
    """Replace the IP-retrieval error counter with a mock."""
    metric = MagicMock()
    monkeypatch.setitem(
        cf_ddns.prometheus_metrics, "ip_retrieval_error_counter", metric
    )
    return metric


@pytest.fixture
def config():
    """A parsed-config dict as handle_dns_update() expects it."""
    return {
        "token": "test_token",
        "zone_id": "test_zone",
        "host": "test.example.com",
        "ttl": 120,
        "proxied": False,
    }
