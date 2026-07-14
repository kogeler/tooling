# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Tests for the three Cloudflare API functions (current behavior)."""

import requests

from conftest import FakeResponse

import cf_ddns

RECORD = {"id": "record123", "content": "1.2.3.4", "ttl": 120, "proxied": False}


def _responses(monkeypatch, method, items):
    """Feed canned responses/exceptions to requests.<method>; return call log."""
    calls = []
    queue = iter(items)

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        item = next(queue)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(cf_ddns.requests, method, fake)
    return calls


# --- get_dns_record -------------------------------------------------------


def test_get_record_success(monkeypatch):
    _responses(monkeypatch, "get", [
        FakeResponse(json_data={"success": True, "result": [RECORD]}),
    ])
    record = cf_ddns.get_dns_record("token", "zone", "host")
    assert record == RECORD


def test_get_record_empty_result_returns_none(monkeypatch):
    _responses(monkeypatch, "get", [
        FakeResponse(json_data={"success": True, "result": []}),
    ])
    assert cf_ddns.get_dns_record("token", "zone", "host") is None


def test_get_record_api_error_no_retry(monkeypatch, api_error_metric):
    calls = _responses(monkeypatch, "get", [
        FakeResponse(json_data={"success": False, "errors": [{"code": 9999}]}),
    ])
    assert cf_ddns.get_dns_record("token", "zone", "host") is None
    assert len(calls) == 1
    api_error_metric.inc.assert_called_once()


def test_get_record_retries_with_backoff(monkeypatch, sleep_calls, api_error_metric):
    _responses(monkeypatch, "get", [
        requests.ConnectionError("net"),
        requests.ConnectionError("net"),
        FakeResponse(json_data={"success": True, "result": [RECORD]}),
    ])
    record = cf_ddns.get_dns_record("token", "zone", "host", max_retries=3)
    assert record == RECORD
    assert sleep_calls == [1, 2]


def test_get_record_gives_up_after_max_retries(monkeypatch, sleep_calls,
                                               api_error_metric):
    calls = _responses(monkeypatch, "get", [requests.Timeout("t")] * 3)
    assert cf_ddns.get_dns_record("token", "zone", "host", max_retries=3) is None
    assert len(calls) == 3
    assert api_error_metric.inc.call_count == 3


# --- create_dns_record ----------------------------------------------------


def test_create_success(monkeypatch):
    calls = _responses(monkeypatch, "post", [
        FakeResponse(json_data={"success": True, "result": {"id": "new_id"}}),
    ])
    record_id = cf_ddns.create_dns_record(
        "token", "zone", "test.example.com", "1.2.3.4", 120, False
    )
    assert record_id == "new_id"
    assert calls[0][1]["json"] == {
        "type": "A",
        "name": "test.example.com",
        "content": "1.2.3.4",
        "ttl": 120,
        "proxied": False,
    }


def test_create_retries_then_succeeds(monkeypatch, sleep_calls, api_error_metric):
    _responses(monkeypatch, "post", [
        requests.ConnectionError("net"),
        FakeResponse(json_data={"success": True, "result": {"id": "new_id"}}),
    ])
    record_id = cf_ddns.create_dns_record(
        "token", "zone", "host", "1.2.3.4", 120, False, max_retries=3
    )
    assert record_id == "new_id"
    assert sleep_calls == [1]


def test_create_api_error_exhausts_retries(monkeypatch, sleep_calls,
                                           api_error_metric):
    calls = _responses(monkeypatch, "post", [
        FakeResponse(json_data={"success": False, "errors": [{"code": 81057}]}),
    ] * 3)
    record_id = cf_ddns.create_dns_record(
        "token", "zone", "host", "1.2.3.4", 120, False, max_retries=3
    )
    assert record_id is None
    assert len(calls) == 3
    assert api_error_metric.inc.call_count == 3


# --- update_cloudflare_record ----------------------------------------------


def test_update_success(monkeypatch):
    calls = _responses(monkeypatch, "put", [
        FakeResponse(json_data={"success": True}),
    ])
    assert cf_ddns.update_cloudflare_record(
        "token", "zone", "rid", "host", "1.2.3.4", 120, False
    ) is True
    assert "rid" in calls[0][0][0]  # record id is part of the URL


def test_update_api_error_returns_false(monkeypatch, sleep_calls, api_error_metric):
    _responses(monkeypatch, "put", [
        FakeResponse(json_data={"success": False, "errors": [{"code": 9999}]}),
    ] * 3)
    assert cf_ddns.update_cloudflare_record(
        "token", "zone", "rid", "host", "1.2.3.4", 120, False, max_retries=3
    ) is False


def test_update_http_error_retries_then_false(monkeypatch, sleep_calls,
                                              api_error_metric):
    calls = _responses(monkeypatch, "put", [FakeResponse(status_code=404)] * 3)
    assert cf_ddns.update_cloudflare_record(
        "token", "zone", "rid", "host", "1.2.3.4", 120, False, max_retries=3
    ) is False
    # Current behavior (H1 in the review): HTTP 404 is retried like a network
    # error because raise_for_status() fires before the body is inspected.
    # Stage 2 replaces this with immediate GONE classification.
    assert len(calls) == 3
