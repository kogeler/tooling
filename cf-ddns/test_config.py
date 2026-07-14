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
    }


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


def test_non_numeric_ttl_exits(required_env, monkeypatch):
    monkeypatch.setenv("CF_DDNS_TTL", "abc")
    with pytest.raises(SystemExit):
        cf_ddns.parse_env()


def test_low_ttl_warns_but_is_accepted(required_env, monkeypatch, caplog):
    monkeypatch.setenv("CF_DDNS_TTL", "30")
    with caplog.at_level(logging.WARNING):
        config = cf_ddns.parse_env()
    assert config["ttl"] == 30
    assert any("TTL" in message for message in caplog.messages)


@pytest.mark.parametrize("value", ["True", "true", "TRUE"])
def test_proxied_true_values(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_PROXIED", value)
    assert cf_ddns.parse_env()["proxied"] is True


@pytest.mark.parametrize("value", ["False", "false", "anything", ""])
def test_proxied_false_values(required_env, monkeypatch, value):
    monkeypatch.setenv("CF_DDNS_PROXIED", value)
    assert cf_ddns.parse_env()["proxied"] is False
