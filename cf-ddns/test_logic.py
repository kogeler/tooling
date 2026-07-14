# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Tests for the fail-closed handle_dns_update() orchestration.

Core invariant (C1): create_dns_record() is called only after a confirmed
ABSENT in the same attempt; TRANSIENT/PERMANENT/AMBIGUOUS never mutate.
"""

from unittest.mock import MagicMock

import pytest

import cf_ddns
from cf_ddns import Outcome

CF_SESSION = object()  # opaque sentinel; the adapter only forwards it


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


def test_update_ok_keeps_record_id(monkeypatch, config):
    mocks = _patch_api(monkeypatch, update={"return_value": Outcome.OK})

    result = cf_ddns.handle_dns_update(config, "rid", "9.9.9.9", CF_SESSION)

    assert result == (True, "rid")
    mocks["get_dns_record"].assert_not_called()
    mocks["create_dns_record"].assert_not_called()


def test_gone_record_refetched_and_updated(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        update={"side_effect": [Outcome.GONE, Outcome.OK]},
        get={"return_value": (Outcome.OK, {"id": "new_id", "content": "1.1.1.1",
                                           "ttl": 120, "proxied": False})},
    )

    result = cf_ddns.handle_dns_update(config, "old_id", "9.9.9.9", CF_SESSION)

    assert result == (True, "new_id")
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

    assert result == (True, "new_record_id")
    mocks["create_dns_record"].assert_called_once()


@pytest.mark.parametrize("outcome", [Outcome.TRANSIENT, Outcome.PERMANENT])
def test_failed_update_never_mutates_further(monkeypatch, config, outcome):
    mocks = _patch_api(monkeypatch, update={"return_value": outcome})

    result = cf_ddns.handle_dns_update(config, "rid", "9.9.9.9", CF_SESSION)

    assert result == (False, "rid")
    mocks["get_dns_record"].assert_not_called()
    mocks["create_dns_record"].assert_not_called()


def test_no_record_id_reads_before_updating(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        get={"return_value": (Outcome.OK, {"id": "found_id", "content": "1.1.1.1",
                                           "ttl": 120, "proxied": False})},
        update={"return_value": Outcome.OK},
    )

    result = cf_ddns.handle_dns_update(config, None, "9.9.9.9", CF_SESSION)

    assert result == (True, "found_id")
    mocks["create_dns_record"].assert_not_called()


def test_first_run_creates_after_confirmed_absent(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        get={"return_value": (Outcome.ABSENT, None)},
        create={"return_value": (Outcome.OK, "new_record_id")},
    )

    result = cf_ddns.handle_dns_update(config, None, "1.2.3.4", CF_SESSION)

    assert result == (True, "new_record_id")
    mocks["create_dns_record"].assert_called_once_with(
        CF_SESSION, "test_zone", "test.example.com", "1.2.3.4", 120, False
    )
    mocks["update_cloudflare_record"].assert_not_called()


# --- C1 regressions: errors must never lead to a create --------------------


@pytest.mark.parametrize("outcome", [
    Outcome.TRANSIENT, Outcome.PERMANENT, Outcome.AMBIGUOUS,
])
def test_read_error_never_creates(monkeypatch, config, outcome):
    mocks = _patch_api(monkeypatch, get={"return_value": (outcome, None)})

    result = cf_ddns.handle_dns_update(config, None, "1.2.3.4", CF_SESSION)

    assert result == (False, None)
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

    assert result == (False, None)
    mocks["create_dns_record"].assert_not_called()


@pytest.mark.parametrize("create_outcome", [
    Outcome.EXISTS, Outcome.TRANSIENT, Outcome.PERMANENT,
])
def test_failed_create_reports_failure_without_retry(monkeypatch, config,
                                                     create_outcome):
    mocks = _patch_api(
        monkeypatch,
        get={"return_value": (Outcome.ABSENT, None)},
        create={"return_value": (create_outcome, None)},
    )

    result = cf_ddns.handle_dns_update(config, None, "1.2.3.4", CF_SESSION)

    assert result == (False, None)
    mocks["create_dns_record"].assert_called_once()
