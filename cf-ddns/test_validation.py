# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Tests for IP validation and external-IP retrieval."""

import pytest
import requests

from conftest import FakeResponse

import cf_ddns

VALID_IPS = [
    "1.2.3.4",
    "192.168.1.1",
    "0.0.0.0",
    "255.255.255.255",
    "10.0.0.1",
]

INVALID_IPS = [
    "not.an.ip.address",
    "256.256.256.256",
    "1.2.3",
    "localhost",
    "1.2.3.4.5",
    "",
    None,
    "192.168.1.-1",
    "192.168.1.256",
]


@pytest.mark.parametrize("ip", VALID_IPS)
def test_validate_ipv4_accepts(ip):
    assert cf_ddns.validate_ipv4(ip) is True


@pytest.mark.parametrize("ip", INVALID_IPS)
def test_validate_ipv4_rejects(ip):
    assert cf_ddns.validate_ipv4(ip) is False


def test_get_external_ip_first_service_ok(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        return FakeResponse(text="1.2.3.4\n")

    monkeypatch.setattr(cf_ddns.requests, "get", fake_get)
    assert cf_ddns.get_external_ip() == "1.2.3.4"
    assert len(calls) == 1


def test_get_external_ip_falls_through_on_invalid_body(monkeypatch, ip_error_metric):
    responses = iter([FakeResponse(text="<html>not an ip</html>"),
                      FakeResponse(text="5.6.7.8")])
    monkeypatch.setattr(cf_ddns.requests, "get", lambda url, timeout: next(responses))

    assert cf_ddns.get_external_ip() == "5.6.7.8"
    ip_error_metric.labels.assert_called_once()


def test_get_external_ip_falls_through_on_http_error(monkeypatch, ip_error_metric):
    responses = iter([FakeResponse(status_code=503),
                      FakeResponse(text="5.6.7.8")])
    monkeypatch.setattr(cf_ddns.requests, "get", lambda url, timeout: next(responses))

    assert cf_ddns.get_external_ip() == "5.6.7.8"
    ip_error_metric.labels.assert_called_once()


def test_get_external_ip_all_services_fail(monkeypatch, ip_error_metric):
    def fake_get(url, timeout):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(cf_ddns.requests, "get", fake_get)

    assert cf_ddns.get_external_ip() is None
    assert ip_error_metric.labels.call_count == len(cf_ddns.check_ip_services)
