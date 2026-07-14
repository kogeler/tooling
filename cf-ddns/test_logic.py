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
        CF_SESSION, "test_zone", "test.example.com", "1.2.3.4", 120, False,
        stop_event=cf_ddns.shutdown_event
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


# --- flap damping (M9/R2) ----------------------------------------------------


def test_single_deviant_reading_never_writes(monkeypatch, config, loop_metrics):
    config["confirm_cycles"] = 2
    mocks = _patch_loop(monkeypatch, ip="9.9.9.9")
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    state = cf_ddns.run_iteration(config, state, _clients())

    mocks["handle_dns_update"].assert_not_called()
    assert (state.pending_ip, state.pending_seen) == ("9.9.9.9", 1)
    assert state.last_ip == "1.1.1.1"


def test_change_confirmed_on_nth_reading_writes_once(monkeypatch, config,
                                                     loop_metrics):
    config["confirm_cycles"] = 2
    mocks = _patch_loop(monkeypatch, ip="9.9.9.9", handle=(Outcome.OK, "rid"))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    state = cf_ddns.run_iteration(config, state, _clients())  # 1st observation
    state = cf_ddns.run_iteration(config, state, _clients())  # 2nd → write

    mocks["handle_dns_update"].assert_called_once()
    assert state.last_ip == "9.9.9.9"
    assert (state.pending_ip, state.pending_seen) == (None, 0)


def test_alternating_readings_never_write(monkeypatch, config, loop_metrics):
    config["confirm_cycles"] = 2
    ips = iter(["9.9.9.9", "1.1.1.1", "9.9.9.9", "1.1.1.1"])
    mocks = {
        "get_external_ip": MagicMock(side_effect=lambda *a, **k: next(ips)),
        "handle_dns_update": MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(cf_ddns, name, mock)
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    for _ in range(4):
        state = cf_ddns.run_iteration(config, state, _clients())

    mocks["handle_dns_update"].assert_not_called()
    assert state.last_ip == "1.1.1.1"
    # every settle-back discarded the pending candidate
    assert loop_metrics["unconfirmed_ip_counter"].inc.call_count == 2


def test_candidate_replacement_discards_and_restarts_window(monkeypatch, config,
                                                            loop_metrics):
    config["confirm_cycles"] = 3
    ips = iter(["8.8.8.8", "9.9.9.9"])
    monkeypatch.setattr(cf_ddns, "get_external_ip",
                        MagicMock(side_effect=lambda *a, **k: next(ips)))
    handle = MagicMock()
    monkeypatch.setattr(cf_ddns, "handle_dns_update", handle)
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    state = cf_ddns.run_iteration(config, state, _clients())
    state = cf_ddns.run_iteration(config, state, _clients())

    handle.assert_not_called()
    assert (state.pending_ip, state.pending_seen) == ("9.9.9.9", 1)
    loop_metrics["unconfirmed_ip_counter"].inc.assert_called_once()


def test_confirm_cycles_one_writes_immediately(monkeypatch, config, loop_metrics):
    config["confirm_cycles"] = 1
    mocks = _patch_loop(monkeypatch, ip="9.9.9.9", handle=(Outcome.OK, "rid"))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")

    state = cf_ddns.run_iteration(config, state, _clients())

    mocks["handle_dns_update"].assert_called_once()
    assert state.last_ip == "9.9.9.9"


def test_first_run_creation_requires_confirmation(monkeypatch, config,
                                                  loop_metrics):
    config["confirm_cycles"] = 2
    mocks = _patch_loop(monkeypatch, ip="9.9.9.9", handle=(Outcome.OK, "new_id"))
    state = DdnsState()  # no record, no last_ip

    state = cf_ddns.run_iteration(config, state, _clients())
    mocks["handle_dns_update"].assert_not_called()  # not yet confirmed

    state = cf_ddns.run_iteration(config, state, _clients())
    mocks["handle_dns_update"].assert_called_once()
    assert state.last_ip == "9.9.9.9"


# --- force_update (M2) --------------------------------------------------------


def test_force_update_rewrites_confirmed_ip_immediately(monkeypatch, config,
                                                        loop_metrics):
    config["confirm_cycles"] = 2  # confirmation must NOT delay this write
    mocks = _patch_loop(monkeypatch, ip="1.1.1.1", handle=(Outcome.OK, "rid"))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid", force_update=True)

    state = cf_ddns.run_iteration(config, state, _clients())

    mocks["handle_dns_update"].assert_called_once()
    assert state.force_update is False  # cleared by the successful write


def test_force_update_survives_transient_failure(monkeypatch, config,
                                                 loop_metrics):
    _patch_loop(monkeypatch, ip="1.1.1.1", handle=(Outcome.TRANSIENT, "rid"))
    state = DdnsState(last_ip="1.1.1.1", record_id="rid", force_update=True)

    state = cf_ddns.run_iteration(config, state, _clients())

    assert state.force_update is True  # retried next iteration
    assert state.cf_failures == 1


def test_startup_settings_drift_sets_force_update(monkeypatch, config,
                                                  loop_metrics):
    drifted = dict(RECORD, ttl=300)
    _patch_startup(monkeypatch, get=(Outcome.OK, drifted), ip="1.1.1.1")

    state = cf_ddns.startup_state(config, _clients())

    assert state.force_update is True


def test_startup_proxied_record_matches_normalized_config(monkeypatch,
                                                          loop_metrics):
    """proxied=true + default TTL must not force-rewrite forever: the config
    TTL is already normalized to Auto (1)."""
    proxied_config = {
        "token": "t", "zone_id": "z", "host": "h",
        "ttl": 1, "proxied": True,  # what parse_env produces for proxied=true
        "interval": 10, "metrics_port": 9101, "max_failures": 10,
        "reconcile_interval": 3600, "confirm_cycles": 1,
    }
    record = dict(RECORD, ttl=1, proxied=True)
    _patch_startup(monkeypatch, get=(Outcome.OK, record), ip="1.1.1.1")

    state = cf_ddns.startup_state(proxied_config, _clients())

    assert state.force_update is False


# --- periodic reconciliation (M3) ----------------------------------------------


def _reconcile_setup(monkeypatch, config, *, get, handle=(Outcome.OK, "rid")):
    """State + clock where reconciliation fires on the second iteration."""
    config["reconcile_interval"] = 100
    clock = iter([1000.0, 1200.0])  # 1st call arms the timer, 2nd is >= +100
    mocks = _patch_loop(monkeypatch, ip="1.1.1.1", handle=handle)
    mocks["get_dns_record"] = MagicMock(return_value=get)
    monkeypatch.setattr(cf_ddns, "get_dns_record", mocks["get_dns_record"])
    state = DdnsState(last_ip="1.1.1.1", record_id="rid")
    return mocks, state, lambda: next(clock)


def test_reconcile_not_due_makes_no_read(monkeypatch, config, loop_metrics):
    mocks, state, now = _reconcile_setup(
        monkeypatch, config, get=(Outcome.OK, RECORD))
    config["reconcile_interval"] = 10_000  # never due with this clock

    state = cf_ddns.run_iteration(config, state, _clients(), now=now)
    state = cf_ddns.run_iteration(config, state, _clients(), now=now)

    mocks["get_dns_record"].assert_not_called()


def test_reconcile_converged_record_writes_nothing(monkeypatch, config,
                                                   loop_metrics):
    record = dict(RECORD, id="rid", content="1.1.1.1")
    mocks, state, now = _reconcile_setup(monkeypatch, config,
                                         get=(Outcome.OK, record))

    state = cf_ddns.run_iteration(config, state, _clients(), now=now)
    state = cf_ddns.run_iteration(config, state, _clients(), now=now)

    mocks["get_dns_record"].assert_called_once()
    mocks["handle_dns_update"].assert_not_called()


def test_reconcile_detects_external_content_drift(monkeypatch, config,
                                                  loop_metrics):
    drifted = dict(RECORD, id="rid", content="5.5.5.5")  # someone edited it
    mocks, state, now = _reconcile_setup(monkeypatch, config,
                                         get=(Outcome.OK, drifted))

    state = cf_ddns.run_iteration(config, state, _clients(), now=now)
    state = cf_ddns.run_iteration(config, state, _clients(), now=now)

    mocks["handle_dns_update"].assert_called_once()
    write_ip = mocks["handle_dns_update"].call_args[0][2]
    assert write_ip == "1.1.1.1"  # converges to the confirmed IP


def test_reconcile_adopts_externally_changed_record_id(monkeypatch, config,
                                                       loop_metrics):
    recreated = dict(RECORD, id="other_id", content="1.1.1.1")
    mocks, state, now = _reconcile_setup(monkeypatch, config,
                                         get=(Outcome.OK, recreated))

    state = cf_ddns.run_iteration(config, state, _clients(), now=now)
    state = cf_ddns.run_iteration(config, state, _clients(), now=now)

    assert state.record_id == "other_id"
    mocks["handle_dns_update"].assert_not_called()  # content converged


def test_reconcile_recreates_externally_deleted_record(monkeypatch, config,
                                                       loop_metrics):
    mocks, state, now = _reconcile_setup(monkeypatch, config,
                                         get=(Outcome.ABSENT, None),
                                         handle=(Outcome.OK, "new_id"))

    state = cf_ddns.run_iteration(config, state, _clients(), now=now)
    state = cf_ddns.run_iteration(config, state, _clients(), now=now)

    mocks["handle_dns_update"].assert_called_once()
    assert state.record_id == "new_id"


def test_reconcile_transient_read_counts_failure_no_write(monkeypatch, config,
                                                          loop_metrics):
    mocks, state, now = _reconcile_setup(monkeypatch, config,
                                         get=(Outcome.TRANSIENT, None))

    state = cf_ddns.run_iteration(config, state, _clients(), now=now)
    state = cf_ddns.run_iteration(config, state, _clients(), now=now)

    mocks["handle_dns_update"].assert_not_called()
    assert state.cf_failures == 1


def test_reconcile_permanent_read_is_fatal(monkeypatch, config, loop_metrics):
    mocks, state, now = _reconcile_setup(monkeypatch, config,
                                         get=(Outcome.PERMANENT, None))

    state = cf_ddns.run_iteration(config, state, _clients(), now=now)
    state = cf_ddns.run_iteration(config, state, _clients(), now=now)

    assert state.fatal == "permanent"
    mocks["handle_dns_update"].assert_not_called()


def test_reconcile_disabled_never_reads(monkeypatch, config, loop_metrics):
    mocks, state, now = _reconcile_setup(monkeypatch, config,
                                         get=(Outcome.OK, RECORD))
    config["reconcile_interval"] = 0

    state = cf_ddns.run_iteration(config, state, _clients(), now=now)
    state = cf_ddns.run_iteration(config, state, _clients(), now=now)

    mocks["get_dns_record"].assert_not_called()


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
