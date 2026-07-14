# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Tests for parse_env() configuration handling."""

import logging

import pytest

import cf_ddns

REQUIRED = ("CF_DDNS_TOKEN", "CF_DDNS_ZONE_ID", "CF_DDNS_HOST")
OPTIONAL = (
    "CF_DDNS_INTERVAL",
    "CF_DDNS_TTL",
    "CF_DDNS_PROXIED",
    "CF_DDNS_METRICS_PORT",
    "CF_DDNS_MAX_FAILURES",
    "CF_DDNS_RECONCILE_INTERVAL",
    "CF_DDNS_CONFIRM_CYCLES",
)


@pytest.fixture(autouse=True)
def clean_optional_env(monkeypatch):
    """Make sure optional vars from the outer environment don't leak in."""
    for var in OPTIONAL:
        monkeypatch.delenv(var, raising=False)


def test_missing_required_vars_exit(monkeypatch):
    for var in REQUIRED:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


@pytest.mark.parametrize("missing", REQUIRED)
def test_each_required_var_is_required(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


def test_defaults(required_env):
    config = cf_ddns.parse_env()
    assert config == {
        "token": "test_token",
        "zone_id": "test_zone",
        "host": "test.example.com",
        "interval": 10,
        "ttl": 120,
        "proxied": False,
        "metrics_port": 9101,
        "max_failures": 10,
        "reconcile_interval": 3600,
        "confirm_cycles": 2,
    }


@pytest.mark.parametrize("var", REQUIRED)
def test_whitespace_only_required_var_exits(required_env, monkeypatch, var):
    monkeypatch.setenv(var, "   ")
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


@pytest.mark.parametrize("raw,normalized", [
    ("Test.Example.COM", "test.example.com"),
    ("test.example.com.", "test.example.com"),
    ("пример.example.com", "xn--e1afmkfd.example.com"),
])
def test_host_is_idna_normalized(required_env, monkeypatch, raw, normalized):
    monkeypatch.setenv("CF_DDNS_HOST", raw)
    assert cf_ddns.parse_env()["host"] == normalized


@pytest.mark.parametrize("bad_host", ["bad host.example.com", "a..b.example.com"])
def test_invalid_host_exits(required_env, monkeypatch, bad_host):
    monkeypatch.setenv("CF_DDNS_HOST", bad_host)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


@pytest.mark.parametrize("value", ["0", "-1", "abc", ""])
def test_invalid_max_failures_exits(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_MAX_FAILURES", value)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


def test_max_failures_accepted(required_env, monkeypatch):
    monkeypatch.setenv("CF_DDNS_MAX_FAILURES", "3")
    assert cf_ddns.parse_env()["max_failures"] == 3


@pytest.mark.parametrize("value", ["0", "-5", "abc", ""])
def test_invalid_interval_exits(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_INTERVAL", value)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


def test_minimal_interval_accepted(required_env, monkeypatch):
    monkeypatch.setenv("CF_DDNS_INTERVAL", "1")
    assert cf_ddns.parse_env()["interval"] == 1


@pytest.mark.parametrize("value", ["0", "-1", "65536", "70000", "abc"])
def test_invalid_metrics_port_exits(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_METRICS_PORT", value)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


@pytest.mark.parametrize("value", ["1", "80", "9101", "65535"])
def test_valid_metrics_port_accepted(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_METRICS_PORT", value)
    assert cf_ddns.parse_env()["metrics_port"] == int(value)


@pytest.mark.parametrize("value", ["abc", "", "0", "-1", "2", "29", "86401"])
def test_invalid_ttl_exits(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_TTL", value)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


@pytest.mark.parametrize("value", ["1", "30", "60", "120", "86400"])
def test_valid_ttl_accepted(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_TTL", value)
    assert cf_ddns.parse_env()["ttl"] == int(value)


def test_enterprise_ttl_warns_but_is_accepted(required_env, monkeypatch, caplog):
    monkeypatch.setenv("CF_DDNS_TTL", "30")
    with caplog.at_level(logging.WARNING):
        config = cf_ddns.parse_env()
    assert config["ttl"] == 30
    assert any("Enterprise" in message for message in caplog.messages)


@pytest.mark.parametrize("value", ["True", "true", "TRUE"])
def test_proxied_true_values(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_PROXIED", value)
    assert cf_ddns.parse_env()["proxied"] is True


@pytest.mark.parametrize("value", ["False", "false", "FALSE"])
def test_proxied_false_values(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_PROXIED", value)
    assert cf_ddns.parse_env()["proxied"] is False


@pytest.mark.parametrize("value", ["treu", "yes", "1", "", "anything"])
def test_proxied_typo_exits_instead_of_silently_disabling(required_env,
                                                          monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_PROXIED", value)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


def test_proxied_normalizes_effective_ttl_to_auto(required_env, monkeypatch,
                                                  caplog):
    """The default TTL (120) + proxied=true must not reconcile-flap forever."""
    monkeypatch.setenv("CF_DDNS_PROXIED", "true")
    with caplog.at_level(logging.WARNING):
        config = cf_ddns.parse_env()
    assert config["ttl"] == 1
    assert any("Auto" in message for message in caplog.messages)


def test_proxied_with_auto_ttl_does_not_warn(required_env, monkeypatch, caplog):
    monkeypatch.setenv("CF_DDNS_PROXIED", "true")
    monkeypatch.setenv("CF_DDNS_TTL", "1")
    with caplog.at_level(logging.WARNING):
        config = cf_ddns.parse_env()
    assert config["ttl"] == 1
    assert not any("ignored" in message for message in caplog.messages)


@pytest.mark.parametrize("value", ["-1", "abc", ""])
def test_invalid_reconcile_interval_exits(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_RECONCILE_INTERVAL", value)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


def test_reconcile_can_be_disabled(required_env, monkeypatch):
    monkeypatch.setenv("CF_DDNS_RECONCILE_INTERVAL", "0")
    assert cf_ddns.parse_env()["reconcile_interval"] == 0


@pytest.mark.parametrize("value", ["0", "-3", "abc", ""])
def test_invalid_confirm_cycles_exits(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_CONFIRM_CYCLES", value)
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


def test_confirm_cycles_one_restores_immediate_updates(required_env, monkeypatch):
    monkeypatch.setenv("CF_DDNS_CONFIRM_CYCLES", "1")
    assert cf_ddns.parse_env()["confirm_cycles"] == 1
