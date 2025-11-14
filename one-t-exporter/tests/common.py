"""Shared helpers and imports for one_t_exporter tests."""

import os
import sys
from pathlib import Path

# Ensure the project root (one-t-exporter) is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXPORTER_MODULE = "one_t_exporter"
PARSER_MODULE = "one_t_parser"
EXPORTER_SCRIPT = "one_t_exporter.py"
PARSER_SCRIPT = "one_t_parser.py"

# Import modules after adjusting sys.path
one_t_exporter = __import__(EXPORTER_MODULE)
one_t_parser = __import__(PARSER_MODULE)

from one_t_exporter import (
    MAX_ADDRESS_LENGTH,
    METRICS,
    MIN_ADDRESS_LENGTH,
    SUPPORTED_NETWORKS,
    load_validators_from_env,
    validate_address,
    validate_network,
)

__all__ = [
    "EXPORTER_MODULE",
    "PARSER_MODULE",
    "EXPORTER_SCRIPT",
    "PARSER_SCRIPT",
    "one_t_exporter",
    "one_t_parser",
    "METRICS",
    "MIN_ADDRESS_LENGTH",
    "MAX_ADDRESS_LENGTH",
    "SUPPORTED_NETWORKS",
    "load_validators_from_env",
    "validate_address",
    "validate_network",
    "clear_validator_env",
    "reset_metrics",
]


def clear_validator_env():
    """Remove all ONE_T_VAL_* variables from the environment."""
    for key in list(os.environ.keys()):
        if key.startswith("ONE_T_VAL_"):
            del os.environ[key]


def reset_metrics():
    """Clear gauge/counter state between tests."""
    for metric in METRICS.values():
        if hasattr(metric, "_metrics"):
            metric._metrics.clear()
        if hasattr(metric, "_value"):
            try:
                metric._value.set(0)
            except AttributeError:
                # Counters do not allow direct set when using prometheus_client; ignore
                pass
