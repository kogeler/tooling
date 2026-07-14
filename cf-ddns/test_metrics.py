# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Metrics tests against a fresh CollectorRegistry: single-series ip_info
invariant, write-vs-change split, restart-safe semantics, and the ban
on prometheus_client private APIs.
"""

import inspect
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from prometheus_client import CollectorRegistry

import cf_ddns
from cf_ddns import DdnsState, HttpClients, Outcome

RECORD = {"id": "rid", "content": "1.1.1.1", "ttl": 120, "proxied": False,
          "modified_on": "2026-07-14T10:00:00+00:00"}
MODIFIED_EPOCH = datetime.fromisoformat("2026-07-14T10:00:00+00:00").timestamp()


@pytest.fixture
def registry(monkeypatch):
    """Fresh registry + real metrics wired into the module under test."""
    fresh = CollectorRegistry()
    monkeypatch.setattr(cf_ddns, "prometheus_metrics",
                        cf_ddns.create_metrics(fresh))
    return fresh


def sample(registry, name, labels=None):
    return registry.get_sample_value(name, labels or {})


def series_count(registry, name):
    for family in registry.collect():
        if family.name == name:
            return len(family.samples)
    return 0


def _clients():
    return HttpClients(cloudflare=object(), check_ip=object())


def _patch_loop(monkeypatch, *, ip, handle):
    for name, mock in (
        ("get_external_ip", MagicMock(return_value=ip)),
        ("handle_dns_update", MagicMock(return_value=handle)),
    ):
        monkeypatch.setattr(cf_ddns, name, mock)


# --- no private API access --------------------------------------------------


def test_no_private_value_pokes_in_source():
    assert "._value" not in inspect.getsource(cf_ddns)


def test_initialize_metrics_materializes_children(registry, config):
    cf_ddns.initialize_metrics(config)

    for host in ("checkip.amazonaws.com", "api.ipify.org"):
        assert sample(registry, "cf_ddns_ip_retrieval_errors_total",
                      {"check_ip_service_host": host}) == 0
    assert sample(registry, "cf_ddns_build_info",
                  {"version": cf_ddns.__version__}) == 1
    # unlabeled counters/gauges exist at 0 without any pokes
    assert sample(registry, "cf_ddns_ip_updates_total") == 0
    assert sample(registry, "cf_ddns_ip_changes_total") == 0
    assert sample(registry, "cf_ddns_last_ip_update_timestamp_seconds") == 0


# --- single-series ip_info invariant ----------------------------------------


def test_ip_change_leaves_single_series(registry, monkeypatch, config):
    _patch_loop(monkeypatch, ip="9.9.9.9", handle=(Outcome.OK, "rid"))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    cf_ddns.run_iteration(config, state, _clients())

    labels_new = {"cf_host": config["host"], "ip": "9.9.9.9"}
    labels_old = {"cf_host": config["host"], "ip": "1.1.1.1"}
    assert sample(registry, "cf_ddns_ip_info", labels_new) == 1
    assert sample(registry, "cf_ddns_ip_info", labels_old) is None  # removed
    assert series_count(registry, "cf_ddns_ip_info") == 1


def test_real_change_increments_changes_and_updates(registry, monkeypatch,
                                                    config):
    _patch_loop(monkeypatch, ip="9.9.9.9", handle=(Outcome.OK, "rid"))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    cf_ddns.run_iteration(config, state, _clients())

    assert sample(registry, "cf_ddns_ip_changes_total") == 1
    assert sample(registry, "cf_ddns_ip_updates_total") == 1


def test_first_run_creation_is_a_write_not_a_change(registry, monkeypatch,
                                                    config):
    _patch_loop(monkeypatch, ip="9.9.9.9", handle=(Outcome.OK, "new_id"))
    state = DdnsState()  # no previous IP

    cf_ddns.run_iteration(config, state, _clients())

    assert sample(registry, "cf_ddns_ip_updates_total") == 1
    assert sample(registry, "cf_ddns_ip_changes_total") == 0
    assert series_count(registry, "cf_ddns_ip_info") == 1


def test_settings_rewrite_is_a_write_not_a_change(registry, monkeypatch,
                                                  config):
    _patch_loop(monkeypatch, ip="1.1.1.1", handle=(Outcome.OK, "rid"))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid", force_update=True)

    cf_ddns.run_iteration(config, state, _clients())

    assert sample(registry, "cf_ddns_ip_updates_total") == 1
    assert sample(registry, "cf_ddns_ip_changes_total") == 0
    assert series_count(registry, "cf_ddns_ip_info") == 1


# --- restart-safe semantics --------------------------------------------------


def test_restart_with_unchanged_ip_is_metrically_quiet(registry, monkeypatch,
                                                       config):
    """Startup against an existing record + one steady iteration: no writes,
    no change events, the exact same single series as before the restart,
    and the process-local update timestamp stays 0."""
    monkeypatch.setattr(cf_ddns, "get_dns_record",
                        MagicMock(return_value=(Outcome.OK, RECORD)))
    state = cf_ddns.startup_state(config, _clients())

    handle = MagicMock()
    monkeypatch.setattr(cf_ddns, "handle_dns_update", handle)
    monkeypatch.setattr(cf_ddns, "get_external_ip",
                        MagicMock(return_value="1.1.1.1"))
    cf_ddns.run_iteration(config, state, _clients())

    handle.assert_not_called()
    assert sample(registry, "cf_ddns_ip_changes_total") == 0
    assert sample(registry, "cf_ddns_ip_updates_total") == 0
    assert sample(registry, "cf_ddns_ip_info",
                  {"cf_host": config["host"], "ip": "1.1.1.1"}) == 1
    assert series_count(registry, "cf_ddns_ip_info") == 1
    # process-local semantics: no write by this process yet
    assert sample(registry, "cf_ddns_last_ip_update_timestamp_seconds") == 0
    # provider state is exposed separately
    assert sample(registry,
                  "cf_ddns_record_modified_timestamp_seconds") == MODIFIED_EPOCH


def test_startup_absent_record_exposes_no_series_until_confirmed(registry,
                                                                 monkeypatch,
                                                                 config):
    monkeypatch.setattr(cf_ddns, "get_dns_record",
                        MagicMock(return_value=(Outcome.ABSENT, None)))
    state = cf_ddns.startup_state(config, _clients())

    assert series_count(registry, "cf_ddns_ip_info") == 0

    # after the confirmed first write the series appears
    _patch_loop(monkeypatch, ip="9.9.9.9", handle=(Outcome.OK, "new_id"))
    cf_ddns.run_iteration(config, state, _clients())
    assert sample(registry, "cf_ddns_ip_info",
                  {"cf_host": config["host"], "ip": "9.9.9.9"}) == 1
    assert series_count(registry, "cf_ddns_ip_info") == 1