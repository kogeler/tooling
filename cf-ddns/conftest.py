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
    status-classification tests. iter_content()/close() support the streamed
    check-IP path.
    """

    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers or {}
        self.closed = False

    def json(self):
        if self._json_data is None:
            raise requests.exceptions.JSONDecodeError("no JSON", "", 0)
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size=1):
        data = self.text.encode() if isinstance(self.text, str) else self.text
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    """Session stand-in feeding canned responses/exceptions; records calls."""

    def __init__(self, items=()):
        self._queue = iter(items)
        self.calls = []  # (method, url, kwargs)
        self.headers = {}

    def _next(self):
        item = next(self._queue)
        if isinstance(item, Exception):
            raise item
        return item

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._next()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._next()


@pytest.fixture
def required_env(monkeypatch):
    """Set the three required environment variables to test values."""
    monkeypatch.setenv("CF_DDNS_TOKEN", "test_token")
    monkeypatch.setenv("CF_DDNS_ZONE_ID", "test_zone")
    monkeypatch.setenv("CF_DDNS_HOST", "test.example.com")


@pytest.fixture
def sleep_calls(monkeypatch):
    """Record time.sleep() delays instead of sleeping; disable jitter."""
    calls = []
    monkeypatch.setattr(cf_ddns.time, "sleep", calls.append)
    monkeypatch.setattr(cf_ddns.random, "uniform", lambda a, b: 0.0)
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
    """A parsed-config dict as the orchestration functions expect it."""
    return {
        "token": "test_token",
        "zone_id": "test_zone",
        "host": "test.example.com",
        "ttl": 120,
        "proxied": False,
        "interval": 10,
        "metrics_port": 9101,
        "max_failures": 10,
    }


@pytest.fixture
def loop_metrics(monkeypatch):
    """Replace the metrics touched by the loop with mocks; return them."""
    metrics = {
        name: MagicMock()
        for name in (
            "ip_update_counter",
            "ip_info_gauge",
            "last_ip_check_timestamp",
            "last_ip_update_timestamp",
        )
    }
    for name, metric in metrics.items():
        monkeypatch.setitem(cf_ddns.prometheus_metrics, name, metric)
    return metrics
