# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Tests for IP validation and external-IP retrieval."""

import pytest
import requests

from conftest import FakeResponse, FakeSession

import cf_ddns

# Only globally routable unicast IPv4 addresses are valid DDNS content
VALID_IPS = [
    "1.2.3.4",
    "8.8.8.8",
    "93.184.216.34",
    "138.199.6.244",
]

MALFORMED_IPS = [
    "not.an.ip.address",
    "256.256.256.256",
    "1.2.3",
    "localhost",
    "1.2.3.4.5",
    "",
    None,
    "192.168.1.-1",
    "192.168.1.256",
    # strict parsing: forms the old int()-based validator accepted
    "192.168.001.1",
    "1. 2.3.4",
    "+1.2.3.4",
    "1_0.2.3.4",
    " 1.2.3.4 ",
]

NON_PUBLIC_IPS = [
    "0.0.0.0",           # unspecified
    "127.0.0.1",         # loopback
    "10.0.0.1",          # RFC1918
    "172.16.0.1",        # RFC1918
    "192.168.1.1",       # RFC1918
    "169.254.10.10",     # link-local
    "100.64.0.1",        # CGNAT
    "224.0.0.1",         # multicast (is_global=True — must be excluded explicitly)
    "239.255.255.250",   # multicast
    "233.1.1.1",         # GLOP multicast
    "240.0.0.1",         # reserved
    "255.255.255.255",   # limited broadcast
]

INVALID_IPS = MALFORMED_IPS + NON_PUBLIC_IPS


@pytest.mark.parametrize("ip", VALID_IPS)
def test_validate_ipv4_accepts(ip):
    assert cf_ddns.validate_ipv4(ip) is True


@pytest.mark.parametrize("ip", INVALID_IPS)
def test_validate_ipv4_rejects(ip):
    assert cf_ddns.validate_ipv4(ip) is False


def test_get_external_ip_first_service_ok():
    session = FakeSession([FakeResponse(text="1.2.3.4\n")])
    assert cf_ddns.get_external_ip(session) == "1.2.3.4"
    assert len(session.calls) == 1
    assert session.calls[0][2].get("stream") is True


def test_get_external_ip_falls_through_on_invalid_body(ip_error_metric):
    session = FakeSession([
        FakeResponse(text="<html>not an ip</html>"),
        FakeResponse(text="5.6.7.8"),
    ])
    assert cf_ddns.get_external_ip(session) == "5.6.7.8"
    ip_error_metric.labels.assert_called_once()


def test_get_external_ip_falls_through_on_http_error(ip_error_metric):
    session = FakeSession([
        FakeResponse(status_code=503),
        FakeResponse(text="5.6.7.8"),
    ])
    assert cf_ddns.get_external_ip(session) == "5.6.7.8"
    ip_error_metric.labels.assert_called_once()


def test_get_external_ip_oversize_body_is_rejected_and_closed(ip_error_metric):
    oversized = FakeResponse(text="x" * 200)
    session = FakeSession([oversized, FakeResponse(text="5.6.7.8")])

    assert cf_ddns.get_external_ip(session) == "5.6.7.8"
    assert oversized.closed is True
    ip_error_metric.labels.assert_called_once()


def test_get_external_ip_responses_are_closed_on_success():
    response = FakeResponse(text="1.2.3.4")
    session = FakeSession([response])
    assert cf_ddns.get_external_ip(session) == "1.2.3.4"
    assert response.closed is True


def test_get_external_ip_all_services_fail(ip_error_metric):
    session = FakeSession([
        requests.ConnectionError("boom"),
        requests.ConnectionError("boom"),
    ])
    assert cf_ddns.get_external_ip(session) is None
    assert ip_error_metric.labels.call_count == len(cf_ddns.check_ip_services)
