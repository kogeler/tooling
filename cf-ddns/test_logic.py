# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Tests for the orchestration layer: handle_dns_update decision table,
run_iteration, startup_state, and the failure policy.

Core invariant (C1): create_dns_record() is called only after a confirmed
ABSENT in the same iteration; TRANSIENT/PERMANENT/AMBIGUOUS never mutate.
"""

from unittest.mock import MagicMock

import pytest

import cf_ddns
from cf_ddns import DdnsState, HttpClients, Outcome

CF_SESSION = object()  # opaque sentinel; orchestration only forwards it


def _clients():
    return HttpClients(cloudflare=CF_SESSION, check_ip=object())


def _patch_api(monkeypatch, *, update=None, get=None, create=None):
    """Replace the three API functions with mocks; return them."""
    mocks = {
        "update_cloudflare_record": MagicMock(**(update or {})),
        "get_dns_record": MagicMock(**(get or {})),
        "create_dns_record": MagicMock(**(create or {})),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(cf_ddns, name, mock)
    return mocks


RECORD = {"id": "found_id", "content": "1.1.1.1", "ttl": 120, "proxied": False}


# --- handle_dns_update decision table ---------------------------------------


def test_update_ok_keeps_record_id(monkeypatch, config):
    mocks = _patch_api(monkeypatch, update={"return_value": Outcome.OK})

    result = cf_ddns.handle_dns_update(config, "rid", "9.9.9.9", CF_SESSION)

    assert result == (Outcome.OK, "rid")
    mocks["get_dns_record"].assert_not_called()
    mocks["create_dns_record"].assert_not_called()


def test_gone_record_refetched_and_updated(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        update={"side_effect": [Outcome.GONE, Outcome.OK]},
        get={"return_value": (Outcome.OK, dict(RECORD, id="new_id"))},
    )

    result = cf_ddns.handle_dns_update(config, "old_id", "9.9.9.9", CF_SESSION)

    assert result == (Outcome.OK, "new_id")
    assert mocks["update_cloudflare_record"].call_count == 2
    mocks["create_dns_record"].assert_not_called()


def test_gone_record_recreated_after_confirmed_absent(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        update={"return_value": Outcome.GONE},
        get={"return_value": (Outcome.ABSENT, None)},
        create={"return_value": (Outcome.OK, "new_record_id")},
    )

    result = cf_ddns.handle_dns_update(config, "old_id", "9.9.9.9", CF_SESSION)

    assert result == (Outcome.OK, "new_record_id")
    mocks["create_dns_record"].assert_called_once()


@pytest.mark.parametrize("outcome", [Outcome.TRANSIENT, Outcome.PERMANENT])
def test_failed_update_never_mutates_further(monkeypatch, config, outcome):
    mocks = _patch_api(monkeypatch, update={"return_value": outcome})

    result = cf_ddns.handle_dns_update(config, "rid", "9.9.9.9", CF_SESSION)

    assert result == (outcome, "rid")
    mocks["get_dns_record"].assert_not_called()
    mocks["create_dns_record"].assert_not_called()


def test_no_record_id_reads_before_updating(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        get={"return_value": (Outcome.OK, RECORD)},
        update={"return_value": Outcome.OK},
    )

    result = cf_ddns.handle_dns_update(config, None, "9.9.9.9", CF_SESSION)

    assert result == (Outcome.OK, "found_id")
    mocks["create_dns_record"].assert_not_called()


def test_first_run_creates_after_confirmed_absent(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        get={"return_value": (Outcome.ABSENT, None)},
        create={"return_value": (Outcome.OK, "new_record_id")},
    )

    result = cf_ddns.handle_dns_update(config, None, "1.2.3.4", CF_SESSION)

    assert result == (Outcome.OK, "new_record_id")
    mocks["create_dns_record"].assert_called_once_with(
        CF_SESSION, "test_zone", "test.example.com", "1.2.3.4", 120, False
    )
    mocks["update_cloudflare_record"].assert_not_called()


def test_create_exists_adopts_record_never_second_create(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        get={"side_effect": [
            (Outcome.ABSENT, None),                       # initial read
            (Outcome.OK, dict(RECORD, id="raced_id")),    # adoption read
        ]},
        create={"return_value": (Outcome.EXISTS, None)},
        update={"return_value": Outcome.OK},
    )

    result = cf_ddns.handle_dns_update(config, None, "1.2.3.4", CF_SESSION)

    assert result == (Outcome.OK, "raced_id")
    mocks["create_dns_record"].assert_called_once()  # never a second create


def test_create_exists_then_unstable_read_is_transient(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        get={"side_effect": [(Outcome.ABSENT, None), (Outcome.TRANSIENT, None)]},
        create={"return_value": (Outcome.EXISTS, None)},
    )

    result = cf_ddns.handle_dns_update(config, None, "1.2.3.4", CF_SESSION)

    assert result == (Outcome.TRANSIENT, None)
    mocks["create_dns_record"].assert_called_once()
    mocks["update_cloudflare_record"].assert_not_called()


# --- C1 regressions: errors must never lead to a create ---------------------


@pytest.mark.parametrize("outcome", [
    Outcome.TRANSIENT, Outcome.PERMANENT, Outcome.AMBIGUOUS,
])
def test_read_error_never_creates(monkeypatch, config, outcome):
    mocks = _patch_api(monkeypatch, get={"return_value": (outcome, None)})

    result = cf_ddns.handle_dns_update(config, None, "1.2.3.4", CF_SESSION)

    assert result == (outcome, None)
    mocks["create_dns_record"].assert_not_called()
    mocks["update_cloudflare_record"].assert_not_called()


@pytest.mark.parametrize("outcome", [
    Outcome.TRANSIENT, Outcome.PERMANENT, Outcome.AMBIGUOUS,
])
def test_gone_then_read_error_never_creates(monkeypatch, config, outcome):
    mocks = _patch_api(
        monkeypatch,
        update={"return_value": Outcome.GONE},
        get={"return_value": (outcome, None)},
    )

    result = cf_ddns.handle_dns_update(config, "old_id", "9.9.9.9", CF_SESSION)

    assert result == (outcome, None)
    mocks["create_dns_record"].assert_not_called()


@pytest.mark.parametrize("create_outcome", [Outcome.TRANSIENT, Outcome.PERMANENT])
def test_failed_create_reports_outcome_without_retry(monkeypatch, config,
                                                     create_outcome):
    mocks = _patch_api(
        monkeypatch,
        get={"return_value": (Outcome.ABSENT, None)},
        create={"return_value": (create_outcome, None)},
    )

    result = cf_ddns.handle_dns_update(config, None, "1.2.3.4", CF_SESSION)

    assert result == (create_outcome, None)
    mocks["create_dns_record"].assert_called_once()


# --- run_iteration -----------------------------------------------------------


def _patch_loop(monkeypatch, *, ip, handle=(Outcome.OK, "rid")):
    mocks = {
        "get_external_ip": MagicMock(return_value=ip),
        "handle_dns_update": MagicMock(return_value=handle),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(cf_ddns, name, mock)
    return mocks


def test_iteration_unchanged_ip_makes_no_cf_calls(monkeypatch, config,
                                                  loop_metrics):
    mocks = _patch_loop(monkeypatch, ip="1.1.1.1")
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    state = cf_ddns.run_iteration(config, state, _clients())

    mocks["handle_dns_update"].assert_not_called()
    assert (state.last_ip, state.record_id) == ("1.1.1.1", "rid")
    assert state.ip_failures == 0 and state.cf_failures == 0
    loop_metrics["last_ip_check_timestamp"].set.assert_called_once()


def test_iteration_changed_ip_updates_state_and_metrics(monkeypatch, config,
                                                        loop_metrics):
    mocks = _patch_loop(monkeypatch, ip="2.2.2.2", handle=(Outcome.OK, "rid"))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid", cf_failures=3)

    state = cf_ddns.run_iteration(config, state, _clients())

    mocks["handle_dns_update"].assert_called_once()
    assert state.last_ip == "2.2.2.2"
    assert state.cf_failures == 0
    loop_metrics["ip_update_counter"].inc.assert_called_once()
    loop_metrics["last_ip_update_timestamp"].set.assert_called_once()


def test_iteration_ip_failure_counts_and_skips_dns(monkeypatch, config,
                                                   loop_metrics):
    mocks = _patch_loop(monkeypatch, ip=None)
    state = DdnsState(last_ip="1.1.1.1", ip_failures=1)

    state = cf_ddns.run_iteration(config, state, _clients())

    assert state.ip_failures == 2
    mocks["handle_dns_update"].assert_not_called()


def test_iteration_ip_success_resets_counter(monkeypatch, config, loop_metrics):
    _patch_loop(monkeypatch, ip="1.1.1.1")
    state = DdnsState(last_ip="1.1.1.1", ip_failures=7)

    state = cf_ddns.run_iteration(config, state, _clients())

    assert state.ip_failures == 0


def test_iteration_transient_dns_failure_counts(monkeypatch, config,
                                                loop_metrics):
    _patch_loop(monkeypatch, ip="2.2.2.2", handle=(Outcome.TRANSIENT, "rid"))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    state = cf_ddns.run_iteration(config, state, _clients())

    assert state.cf_failures == 1
    assert state.last_ip == "1.1.1.1"  # unchanged: the write did not happen
    loop_metrics["ip_update_counter"].inc.assert_not_called()


@pytest.mark.parametrize("outcome", [Outcome.PERMANENT, Outcome.AMBIGUOUS])
def test_iteration_unrecoverable_outcome_sets_fatal(monkeypatch, config,
                                                    loop_metrics, outcome):
    _patch_loop(monkeypatch, ip="2.2.2.2", handle=(outcome, None))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    state = cf_ddns.run_iteration(config, state, _clients())

    assert state.fatal == outcome.value


# --- startup_state -----------------------------------------------------------


def _patch_startup(monkeypatch, *, get, ip):
    mocks = {
        "get_dns_record": MagicMock(return_value=get),
        "get_external_ip": MagicMock(return_value=ip),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(cf_ddns, name, mock)
    return mocks


def test_startup_adopts_existing_record(monkeypatch, config, loop_metrics):
    _patch_startup(monkeypatch, get=(Outcome.OK, RECORD), ip="1.1.1.1")

    state = cf_ddns.startup_state(config, _clients())

    assert (state.last_ip, state.record_id) == ("1.1.1.1", "found_id")
    assert state.fatal is None


def test_startup_absent_record_starts_empty(monkeypatch, config, loop_metrics):
    _patch_startup(monkeypatch, get=(Outcome.ABSENT, None), ip="1.1.1.1")

    state = cf_ddns.startup_state(config, _clients())

    assert (state.last_ip, state.record_id, state.fatal) == (None, None, None)


def test_startup_transient_read_is_not_fatal(monkeypatch, config, loop_metrics):
    _patch_startup(monkeypatch, get=(Outcome.TRANSIENT, None), ip="1.1.1.1")

    state = cf_ddns.startup_state(config, _clients())

    assert (state.last_ip, state.record_id, state.fatal) == (None, None, None)


@pytest.mark.parametrize("outcome", [Outcome.PERMANENT, Outcome.AMBIGUOUS])
def test_startup_unrecoverable_read_is_fatal(monkeypatch, config, loop_metrics,
                                             outcome):
    mocks = _patch_startup(monkeypatch, get=(outcome, None), ip="1.1.1.1")

    state = cf_ddns.startup_state(config, _clients())

    assert state.fatal == outcome.value
    mocks["get_external_ip"].assert_not_called()


# --- enforce_failure_policy ---------------------------------------------------


def test_policy_passes_healthy_state(config):
    cf_ddns.enforce_failure_policy(config, DdnsState(ip_failures=9, cf_failures=9))


def test_policy_exits_on_fatal(config):
    with pytest.raises(SystemExit):
        cf_ddns.enforce_failure_policy(config, DdnsState(fatal="permanent"))


@pytest.mark.parametrize("field", ["ip_failures", "cf_failures"])
def test_policy_exits_on_exhausted_budget(config, field):
    state = DdnsState(**{field: config["max_failures"]})
    with pytest.raises(SystemExit):
        cf_ddns.enforce_failure_policy(config, state)


def test_permanent_anywhere_exits(monkeypatch, config, loop_metrics):
    """Integration: a PERMANENT outcome flows through iteration to SystemExit."""
    _patch_loop(monkeypatch, ip="2.2.2.2", handle=(Outcome.PERMANENT, None))
    state = DdnsState(last_ip="1.1.1.1")

    state = cf_ddns.run_iteration(config, state, _clients())
    with pytest.raises(SystemExit):
        cf_ddns.enforce_failure_policy(config, state)
