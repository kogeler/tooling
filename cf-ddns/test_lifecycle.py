# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Tests for graceful shutdown: signal handling, interruptible waits,
no post-signal HTTP calls or DNS mutations, and main() lifecycle cleanup.
"""

import signal
import threading
from unittest.mock import MagicMock

import pytest

import cf_ddns
from cf_ddns import DdnsState, HttpClients, Outcome

from conftest import FakeResponse, FakeSession


# --- signal handler -----------------------------------------------------------


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_handler_sets_shutdown_event(signum):
    assert not cf_ddns.shutdown_event.is_set()
    cf_ddns._handle_signal(signum, None)
    assert cf_ddns.shutdown_event.is_set()


# --- interruptible waits ------------------------------------------------------


def test_wait_dispatches_to_stop_event():
    event = MagicMock()
    cf_ddns._wait(5, event)
    event.wait.assert_called_once_with(timeout=5)


def test_wait_returns_immediately_when_event_set():
    event = threading.Event()
    event.set()
    # A 100s wait on a set event must not block.
    cf_ddns._wait(100, event)


# --- no new HTTP calls / mutations after shutdown ------------------------------


def test_cf_request_starts_no_call_after_shutdown():
    cf_ddns.shutdown_event.set()
    session = FakeSession([FakeResponse(json_data={"success": True, "result": {}})])

    result = cf_ddns._cf_request(session, "PATCH", "https://x/",
                                 stop_event=cf_ddns.shutdown_event)

    assert result.kind == "transient"
    assert session.calls == []


def test_get_external_ip_starts_no_call_after_shutdown():
    cf_ddns.shutdown_event.set()
    session = FakeSession([FakeResponse(text="1.2.3.4")])

    assert cf_ddns.get_external_ip(
        session, stop_event=cf_ddns.shutdown_event) is None
    assert session.calls == []


def test_no_dns_mutation_after_shutdown(config, api_error_metric):
    """End-to-end through the real orchestration: a pending update must not
    reach the wire once the shutdown event is set."""
    cf_ddns.shutdown_event.set()
    session = FakeSession([FakeResponse(json_data={"success": True, "result": {}})])

    outcome, record_id = cf_ddns.handle_dns_update(
        config, "rid", "9.9.9.9", session
    )

    assert outcome is Outcome.TRANSIENT
    assert session.calls == []  # nothing was sent


def test_shutdown_failures_do_not_count_toward_budget(monkeypatch, config,
                                                      loop_metrics):
    """A None IP caused by shutdown must not increment failure counters."""
    def fake_get_ip(session, stop_event=None):
        cf_ddns.shutdown_event.set()
        return None

    monkeypatch.setattr(cf_ddns, "get_external_ip", fake_get_ip)
    state = DdnsState(last_ip="1.1.1.1", ip_failures=9)

    state = cf_ddns.run_iteration(config, state, HttpClients(object(), object()))

    assert state.ip_failures == 9  # unchanged: not a real network failure


# --- run_loop -----------------------------------------------------------------


def test_run_loop_zero_iterations_when_event_preset(monkeypatch, config):
    cf_ddns.shutdown_event.set()
    iteration = MagicMock()
    monkeypatch.setattr(cf_ddns, "run_iteration", iteration)

    cf_ddns.run_loop(config, DdnsState(), HttpClients(object(), object()))

    iteration.assert_not_called()


def test_run_loop_exits_after_signal_mid_iteration(monkeypatch, config):
    def iteration(cfg, state, clients):
        cf_ddns.shutdown_event.set()  # signal arrives during the iteration
        return state

    iteration_mock = MagicMock(side_effect=iteration)
    monkeypatch.setattr(cf_ddns, "run_iteration", iteration_mock)

    cf_ddns.run_loop(config, DdnsState(), HttpClients(object(), object()))

    iteration_mock.assert_called_once()  # no second iteration, no sleep-wait


# --- main() lifecycle cleanup ---------------------------------------------------


@pytest.fixture
def wired_main(monkeypatch, required_env):
    """main() with every external effect mocked; returns the mocks."""
    httpd, thread = MagicMock(), MagicMock()
    clients = HttpClients(cloudflare=MagicMock(), check_ip=MagicMock())
    mocks = {
        "initialize_metrics": MagicMock(),
        "start_http_server": MagicMock(return_value=(httpd, thread)),
        "create_http_clients": MagicMock(return_value=clients),
        "startup_state": MagicMock(return_value=DdnsState()),
        "run_loop": MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(cf_ddns, name, mock)
    # signal.signal must not be called from a test thread context problem;
    # main runs in the main thread here, so real registration is fine.
    return {"httpd": httpd, "thread": thread, "clients": clients, **mocks}


def test_main_cleans_up_on_normal_shutdown(wired_main):
    cf_ddns.main()

    wired_main["httpd"].shutdown.assert_called_once()
    wired_main["httpd"].server_close.assert_called_once()
    wired_main["thread"].join.assert_called_once()
    wired_main["clients"].cloudflare.close.assert_called_once()
    wired_main["clients"].check_ip.close.assert_called_once()


def test_main_cleans_up_even_on_fatal_startup(wired_main):
    wired_main["startup_state"].return_value = DdnsState(fatal="permanent")

    with pytest.raises(SystemExit):
        cf_ddns.main()

    wired_main["httpd"].shutdown.assert_called_once()
    wired_main["clients"].cloudflare.close.assert_called_once()
    wired_main["run_loop"].assert_not_called()
