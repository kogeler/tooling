# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Tests for handle_dns_update() orchestration (current behavior)."""

from unittest.mock import MagicMock

import cf_ddns


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
    mocks = _patch_api(monkeypatch, update={"return_value": True})

    success, record_id = cf_ddns.handle_dns_update(config, "rid", "9.9.9.9")

    assert (success, record_id) == (True, "rid")
    mocks["get_dns_record"].assert_not_called()
    mocks["create_dns_record"].assert_not_called()


def test_failed_update_refetches_record_id(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        update={"side_effect": [False, True]},
        get={"return_value": {"id": "new_id", "content": "1.1.1.1"}},
    )

    success, record_id = cf_ddns.handle_dns_update(config, "old_id", "9.9.9.9")

    assert (success, record_id) == (True, "new_id")
    assert mocks["update_cloudflare_record"].call_count == 2
    mocks["create_dns_record"].assert_not_called()


def test_deleted_record_is_recreated(monkeypatch, config):
    mocks = _patch_api(
        monkeypatch,
        update={"return_value": False},
        get={"return_value": None},
        create={"return_value": "new_record_id"},
    )

    success, record_id = cf_ddns.handle_dns_update(config, "old_id", "9.9.9.9")

    assert (success, record_id) == (True, "new_record_id")
    mocks["create_dns_record"].assert_called_once()


def test_first_run_creates_record(monkeypatch, config):
    mocks = _patch_api(monkeypatch, create={"return_value": "new_record_id"})

    success, record_id = cf_ddns.handle_dns_update(config, None, "1.2.3.4")

    assert (success, record_id) == (True, "new_record_id")
    mocks["create_dns_record"].assert_called_once_with(
        "test_token", "test_zone", "test.example.com", "1.2.3.4", 120, False
    )
    mocks["update_cloudflare_record"].assert_not_called()


def test_failed_create_reports_failure(monkeypatch, config):
    _patch_api(monkeypatch, create={"return_value": None})

    success, record_id = cf_ddns.handle_dns_update(config, None, "1.2.3.4")

    assert (success, record_id) == (False, None)
