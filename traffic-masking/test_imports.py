# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Import smoke tests for the core and optional enhanced modules."""

import importlib

import pytest

CORE_MODULES = [
    "control_protocol",
    "masking_lib",
    "traffic_masking_server",
    "traffic_masking_client",
]

ENHANCED_MODULES = [
    "enhanced.timing",
    "enhanced.correlation",
    "enhanced.ml_resistance",
    "enhanced.entropy",
    "enhanced.state_machine",
]


@pytest.mark.parametrize("module", CORE_MODULES)
def test_core_module_imports(module):
    assert importlib.import_module(module) is not None


@pytest.mark.parametrize("module", ENHANCED_MODULES)
def test_enhanced_module_imports(module):
    # Enhanced modules are optional at runtime; skip only if the runtime itself
    # could not import them (they are present in this repo, so this should pass).
    try:
        mod = importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - only if enhanced/ is stripped
        pytest.skip(f"optional module {module} unavailable: {exc}")
    assert mod is not None
