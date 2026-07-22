# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Import smoke tests for the dependency-free runtime modules."""

import importlib
import subprocess
import sys

import pytest

CORE_MODULES = [
    "control_protocol",
    "masking_lib",
    "traffic_masking_server",
    "traffic_masking_client",
]

@pytest.mark.parametrize("module", CORE_MODULES)
def test_core_module_imports(module):
    assert importlib.import_module(module) is not None


def test_runtime_imports_do_not_load_numpy():
    modules = ", ".join(CORE_MODULES)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import {modules}; import sys; assert 'numpy' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
