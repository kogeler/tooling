# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Classification tests for the Cloudflare API layer (Outcome contract)."""

import pytest
import requests

from conftest import FakeResponse, FakeSession

import cf_ddns
from cf_ddns import Outcome

RECORD = {"id": "record123", "content": "1.2.3.4", "ttl": 120, "proxied": False}


def ok_get(records):
    return FakeResponse(json_data={"success": True, "result": records})


# --- session factory --------------------------------------------------------


def test_bearer_token_only_on_cloudflare_session():
    clients = cf_ddns.create_http_clients("secret-token")
    assert clients.cloudflare.headers["Authorization"] == "Bearer secret-token"
    assert "Authorization" not in clients.check_ip.headers


# --- get_dns_record ---------------------------------------------------------


def test_get_ok(api_error_metric):
    session = FakeSession([ok_get([RECORD])])
    outcome, record = cf_ddns.get_dns_record(session, "zone", "host")
    assert outcome is Outcome.OK
    assert record == {**RECORD, "modified_on": None}
    method, url, kwargs = session.calls[0]
    assert kwargs["params"] == {"type": "A", "name": "host"}


def test_get_ok_projects_modified_on(api_error_metric):
    stamped = dict(RECORD, modified_on="2026-07-14T10:00:00Z")
    session = FakeSession([ok_get([stamped])])
    outcome, record = cf_ddns.get_dns_record(session, "zone", "host")
    assert outcome is Outcome.OK
    assert record["modified_on"] == "2026-07-14T10:00:00Z"


def test_get_empty_result_is_absent(api_error_metric):
    session = FakeSession([ok_get([])])
    assert cf_ddns.get_dns_record(session, "zone", "host") == (Outcome.ABSENT, None)


def test_get_multiple_records_is_ambiguous(api_error_metric):
    second = dict(RECORD, id="record456", content="5.6.7.8")
    session = FakeSession([ok_get([RECORD, second])])
    assert cf_ddns.get_dns_record(session, "zone", "host") == (Outcome.AMBIGUOUS, None)


def test_get_api_refusal_is_permanent_no_retry(api_error_metric, sleep_calls):
    session = FakeSession([
        FakeResponse(json_data={"success": False, "errors": [{"code": 9999}]}),
    ])
    assert cf_ddns.get_dns_record(session, "zone", "host") == (Outcome.PERMANENT, None)
    assert len(session.calls) == 1
    assert sleep_calls == []


def test_get_403_is_permanent_no_retry(api_error_metric, sleep_calls):
    session = FakeSession([FakeResponse(status_code=403, json_data={
        "success": False, "errors": [{"code": 9109, "message": "Invalid token"}],
    })])
    assert cf_ddns.get_dns_record(session, "zone", "host") == (Outcome.PERMANENT, None)
    assert len(session.calls) == 1
    assert sleep_calls == []


@pytest.mark.parametrize("bad_body", [
    None,                                          # invalid JSON
    {"success": True},                             # missing result
    {"success": True, "result": {"id": "x"}},      # result not a list
    {"success": True, "result": [{"id": ""}]},     # malformed record
    {"result": [{"id": "x"}]},                     # missing success flag
])
def test_get_malformed_body_never_ok_or_absent(api_error_metric, sleep_calls,
                                               bad_body):
    session = FakeSession([FakeResponse(json_data=bad_body)] * 3)
    outcome, record = cf_ddns.get_dns_record(session, "zone", "host")
    assert outcome is Outcome.TRANSIENT
    assert record is None
    assert len(session.calls) == 3  # idempotent: protocol failures retried


@pytest.mark.parametrize("failure", [
    FakeResponse(status_code=500),
    requests.ConnectionError("net down"),
    requests.Timeout("read timeout"),
])
def test_get_transient_failures_retried_with_backoff(api_error_metric,
                                                     sleep_calls, failure):
    session = FakeSession([failure, failure, ok_get([RECORD])])
    outcome, record = cf_ddns.get_dns_record(session, "zone", "host")
    assert outcome is Outcome.OK
    assert record == {**RECORD, "modified_on": None}
    assert sleep_calls == [1, 2]


def test_get_retries_exhausted_is_transient(api_error_metric, sleep_calls):
    session = FakeSession([requests.Timeout("t")] * 3)
    assert cf_ddns.get_dns_record(session, "zone", "host") == (Outcome.TRANSIENT, None)
    assert len(session.calls) == 3
    assert api_error_metric.inc.call_count == 3


def test_429_waits_advertised_delay_then_retries(api_error_metric, sleep_calls):
    session = FakeSession([
        FakeResponse(status_code=429, json_data={}, headers={"Retry-After": "2"}),
        ok_get([RECORD]),
    ])
    outcome, _ = cf_ddns.get_dns_record(session, "zone", "host")
    assert outcome is Outcome.OK
    assert sleep_calls == [2.0]


def test_429_over_budget_gives_up_without_waiting(api_error_metric, sleep_calls):
    session = FakeSession([
        FakeResponse(status_code=429, json_data={}, headers={"Retry-After": "3600"}),
    ])
    assert cf_ddns.get_dns_record(session, "zone", "host") == (Outcome.TRANSIENT, None)
    assert len(session.calls) == 1
    assert sleep_calls == []


# --- create_dns_record ------------------------------------------------------


def test_create_ok_sends_exactly_owned_fields(api_error_metric):
    session = FakeSession([
        FakeResponse(json_data={"success": True, "result": {"id": "new_id"}}),
    ])
    outcome, record_id = cf_ddns.create_dns_record(
        session, "zone", "test.example.com", "1.2.3.4", 120, False
    )
    assert (outcome, record_id) == (Outcome.OK, "new_id")
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert kwargs["json"] == {
        "type": "A",
        "name": "test.example.com",
        "content": "1.2.3.4",
        "ttl": 120,
        "proxied": False,
    }


@pytest.mark.parametrize("response", [
    FakeResponse(json_data={"success": False, "errors": [{"code": 81057}]}),
    FakeResponse(status_code=400,
                 json_data={"success": False, "errors": [{"code": 81058}]}),
])
def test_create_existing_record_is_exists(api_error_metric, response):
    session = FakeSession([response])
    outcome, record_id = cf_ddns.create_dns_record(
        session, "zone", "host", "1.2.3.4", 120, False
    )
    assert (outcome, record_id) == (Outcome.EXISTS, None)


@pytest.mark.parametrize("failure", [
    requests.Timeout("uncertain"),
    requests.ConnectionError("net"),
    FakeResponse(status_code=502),
    FakeResponse(json_data={"success": True, "result": {}}),  # no usable id
])
def test_create_uncertain_outcome_single_post_no_retry(api_error_metric,
                                                       sleep_calls, failure):
    # POST is not idempotent: one attempt only, TRANSIENT so the caller
    # re-reads state instead of blindly re-sending (C1 companion rule).
    session = FakeSession([failure])
    outcome, record_id = cf_ddns.create_dns_record(
        session, "zone", "host", "1.2.3.4", 120, False
    )
    assert (outcome, record_id) == (Outcome.TRANSIENT, None)
    assert len(session.calls) == 1
    assert sleep_calls == []


def test_create_permanent_refusal(api_error_metric):
    session = FakeSession([FakeResponse(status_code=403, json_data={
        "success": False, "errors": [{"code": 9109}],
    })])
    outcome, record_id = cf_ddns.create_dns_record(
        session, "zone", "host", "1.2.3.4", 120, False
    )
    assert (outcome, record_id) == (Outcome.PERMANENT, None)


# --- update_cloudflare_record -----------------------------------------------


def test_update_ok_uses_patch_never_put(api_error_metric):
    session = FakeSession([FakeResponse(json_data={"success": True, "result": {}})])
    outcome = cf_ddns.update_cloudflare_record(
        session, "zone", "rid", "host", "9.9.9.9", 120, False
    )
    assert outcome is Outcome.OK
    method, url, kwargs = session.calls[0]
    assert method == "PATCH"
    assert url.endswith("/zones/zone/dns_records/rid")
    assert kwargs["json"] == {
        "type": "A",
        "name": "host",
        "content": "9.9.9.9",
        "ttl": 120,
        "proxied": False,
    }


@pytest.mark.parametrize("response", [
    FakeResponse(status_code=404,
                 json_data={"success": False, "errors": [{"code": 81044}]}),
    FakeResponse(status_code=404, json_data=None),
    FakeResponse(json_data={"success": False, "errors": [{"code": 81044}]}),
])
def test_update_missing_record_is_gone_no_retry(api_error_metric, sleep_calls,
                                                response):
    session = FakeSession([response])
    outcome = cf_ddns.update_cloudflare_record(
        session, "zone", "rid", "host", "9.9.9.9", 120, False
    )
    assert outcome is Outcome.GONE
    assert len(session.calls) == 1
    assert sleep_calls == []


def test_update_403_is_permanent_no_retry(api_error_metric, sleep_calls):
    session = FakeSession([FakeResponse(status_code=403, json_data={
        "success": False, "errors": [{"code": 9109}],
    })])
    outcome = cf_ddns.update_cloudflare_record(
        session, "zone", "rid", "host", "9.9.9.9", 120, False
    )
    assert outcome is Outcome.PERMANENT
    assert len(session.calls) == 1
    assert sleep_calls == []


def test_update_transient_failures_retried(api_error_metric, sleep_calls):
    session = FakeSession([
        requests.ConnectionError("net"),
        FakeResponse(json_data={"success": True, "result": {}}),
    ])
    outcome = cf_ddns.update_cloudflare_record(
        session, "zone", "rid", "host", "9.9.9.9", 120, False
    )
    assert outcome is Outcome.OK
    assert sleep_calls == [1]


def test_update_retries_exhausted_is_transient(api_error_metric, sleep_calls):
    session = FakeSession([FakeResponse(status_code=500)] * 3)
    outcome = cf_ddns.update_cloudflare_record(
        session, "zone", "rid", "host", "9.9.9.9", 120, False
    )
    assert outcome is Outcome.TRANSIENT
    assert len(session.calls) == 3


# --- Retry-After parsing ----------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("2", 2.0),
    ("0", 0.0),
    ("", None),
    (None, None),
    ("garbage", None),
])
def test_parse_retry_after_simple_forms(value, expected):
    assert cf_ddns._parse_retry_after(value) == expected


def test_parse_retry_after_http_date():
    # An HTTP-date in the past clamps to 0 rather than going negative.
    assert cf_ddns._parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
