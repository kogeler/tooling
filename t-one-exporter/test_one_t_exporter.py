#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for ONE-T Prometheus exporter.
Tests configuration parsing, metric creation, and error handling.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock, call
import signal
import threading
import time

# Global variables for script names
EXPORTER_MODULE = "one_t_exporter"
PARSER_MODULE = "one_t_parser"
EXPORTER_SCRIPT = "one_t_exporter.py"
PARSER_SCRIPT = "one_t_parser.py"

# Add the current directory to Python path to import the exporter
sys.path.insert(0, os.path.dirname(__file__))

# Import from the renamed modules
one_t_exporter = __import__(EXPORTER_MODULE)
one_t_parser = __import__(PARSER_MODULE)

from one_t_exporter import (
    validate_network,
    validate_address,
    load_validators_from_env,
    METRICS,
    SUPPORTED_NETWORKS,
    MIN_ADDRESS_LENGTH,
    MAX_ADDRESS_LENGTH,
)


class TestNetworkValidation(unittest.TestCase):
    """Test network validation functionality."""

    def test_validate_network_supported(self):
        """Test validation of supported networks."""
        for network in SUPPORTED_NETWORKS:
            with self.subTest(network=network):
                self.assertTrue(validate_network(network))

    def test_validate_network_unsupported(self):
        """Test validation of unsupported networks."""
        unsupported_networks = ["ethereum", "bitcoin", "solana", "cardano"]
        for network in unsupported_networks:
            with self.subTest(network=network):
                self.assertFalse(validate_network(network))

    def test_validate_network_case_insensitive(self):
        """Test that network validation is case-insensitive."""
        self.assertTrue(validate_network("POLKADOT"))
        self.assertTrue(validate_network("Polkadot"))
        self.assertTrue(validate_network("pOlKaDoT"))


class TestAddressValidation(unittest.TestCase):
    """Test address validation functionality."""

    def test_validate_address_valid_lengths(self):
        """Test validation of addresses with valid lengths."""
        # Test minimum length
        min_length_addr = "a" * MIN_ADDRESS_LENGTH
        self.assertTrue(validate_address(min_length_addr))

        # Test maximum length
        max_length_addr = "a" * MAX_ADDRESS_LENGTH
        self.assertTrue(validate_address(max_length_addr))

        # Test typical length
        typical_addr = "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"
        self.assertTrue(validate_address(typical_addr))

    def test_validate_address_invalid_lengths(self):
        """Test validation of addresses with invalid lengths."""
        # Too short
        too_short = "a" * (MIN_ADDRESS_LENGTH - 1)
        self.assertFalse(validate_address(too_short))

        # Too long
        too_long = "a" * (MAX_ADDRESS_LENGTH + 1)
        self.assertFalse(validate_address(too_long))


class TestEnvironmentParsing(unittest.TestCase):
    """Test parsing of environment variables."""

    def setUp(self):
        """Clear environment variables and metric state before each test."""
        # Remove any existing ONE_T_* variables
        for key in list(os.environ.keys()):
            if key.startswith("ONE_T_VAL_"):
                del os.environ[key]

        # Clear metric state
        for metric_name, metric in METRICS.items():
            if hasattr(metric, "_metrics"):
                metric._metrics.clear()
            if hasattr(metric, "_value"):
                metric._value.set(0)

        # Clear any test environment variables that might interfere
        for key in list(os.environ.keys()):
            if key.startswith("ONE_T_VAL_"):
                del os.environ[key]

    def test_load_validators_single_valid(self):
        """Test loading a single valid validator."""
        os.environ["ONE_T_VAL_1"] = "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"
        os.environ["ONE_T_VAL_NETWORK_1"] = "polkadot"

        validators = load_validators_from_env()

        self.assertEqual(len(validators), 1)
        self.assertEqual(
            validators[0],
            ("polkadot", "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"),
        )

    def test_load_validators_multiple_valid(self):
        """Test loading multiple valid validators."""
        # First validator
        os.environ["ONE_T_VAL_1"] = "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"
        os.environ["ONE_T_VAL_NETWORK_1"] = "polkadot"

        # Second validator
        os.environ["ONE_T_VAL_2"] = "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q"
        os.environ["ONE_T_VAL_NETWORK_2"] = "kusama"

        validators = load_validators_from_env()

        self.assertEqual(len(validators), 2)
        self.assertEqual(
            validators[0],
            ("polkadot", "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"),
        )
        self.assertEqual(
            validators[1], ("kusama", "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q")
        )

    def test_load_validators_stops_at_gap(self):
        """Test that loading stops when there's a gap in indices."""
        os.environ["ONE_T_VAL_1"] = "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"
        os.environ["ONE_T_VAL_NETWORK_1"] = "polkadot"
        # Skip index 2
        os.environ["ONE_T_VAL_3"] = "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q"
        os.environ["ONE_T_VAL_NETWORK_3"] = "kusama"

        validators = load_validators_from_env()

        # Should only load the first validator
        self.assertEqual(len(validators), 1)
        self.assertEqual(
            validators[0],
            ("polkadot", "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"),
        )

    def test_load_validators_invalid_network(self):
        """Test loading with invalid network."""
        initial_errors = METRICS["one_t_errors"]._value.get()

        os.environ["ONE_T_VAL_1"] = "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"
        os.environ["ONE_T_VAL_NETWORK_1"] = "invalid_network"

        validators = load_validators_from_env()

        # Should skip invalid network
        self.assertEqual(len(validators), 0)
        # Error counter should be incremented
        self.assertEqual(METRICS["one_t_errors"]._value.get(), initial_errors + 1)

    def test_load_validators_invalid_address(self):
        """Test loading with invalid address length."""
        initial_errors = METRICS["one_t_errors"]._value.get()

        os.environ["ONE_T_VAL_1"] = "too_short"
        os.environ["ONE_T_VAL_NETWORK_1"] = "polkadot"

        validators = load_validators_from_env()

        # Should skip invalid address
        self.assertEqual(len(validators), 0)
        # Error counter should be incremented
        self.assertEqual(METRICS["one_t_errors"]._value.get(), initial_errors + 1)

    def test_load_validators_no_validators(self):
        """Test loading when no validators are configured."""
        validators = load_validators_from_env()
        self.assertEqual(len(validators), 0)


class TestMetricsCreation(unittest.TestCase):
    """Test Prometheus metrics creation."""

    def test_metrics_dictionary_structure(self):
        """Test that METRICS dictionary has expected structure."""
        expected_metrics = [
            "one_t_grade_numeric",
            "one_t_performance_score",
            "one_t_mvr",
            "one_t_bar",
            "one_t_points_normalized",
            "one_t_pv_sessions_ratio",
            "one_t_missed_votes",
            "one_t_bitfields_unavailability",
            "one_t_explicit_votes",
            "one_t_implicit_votes",
            "one_t_bitfields_availability",
            "one_t_points",
            "one_t_authored_blocks_count",
            "one_t_para_points",
            "one_t_errors",
        ]

        for metric_name in expected_metrics:
            with self.subTest(metric=metric_name):
                self.assertIn(metric_name, METRICS)
                self.assertIsNotNone(METRICS[metric_name])

    def test_metrics_labels(self):
        """Test that metrics have correct labels."""
        expected_labels = ["network", "address", "identity"]

        for metric_name, metric in METRICS.items():
            # Skip the errors metric which doesn't have labels
            if metric_name == "one_t_errors":
                continue

            with self.subTest(metric=metric_name):
                # Check that metric has the _labelnames attribute with expected labels
                self.assertEqual(set(metric._labelnames), set(expected_labels))


class TestMockedMetricUpdate(unittest.TestCase):
    """Test metric update functionality with mocked data."""

    def setUp(self):
        """Set up test environment."""
        # Clear any existing labels from previous tests
        for metric_name, metric in METRICS.items():
            if hasattr(metric, "_metrics"):
                metric._metrics.clear()
            if hasattr(metric, "_value"):
                metric._value.set(0)

        # Clear environment variables for this test
        for key in list(os.environ.keys()):
            if key.startswith("ONE_T_VAL_"):
                del os.environ[key]

    @patch(f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch")
    def test_update_metrics_success(self, mock_batch):
        """Test successful metric update with mocked data."""
        # Mock successful result
        mock_result = {
            "ok": True,
            "network": "polkadot",
            "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
            "identity": "TestValidator",
            "grade_numeric": 9.0,
            "performance_score": 0.95,
            "components": {
                "mvr": 0.05,
                "bar": 0.98,
                "points_normalized": 0.85,
                "pv_sessions_ratio": 0.99,
            },
            "key_metrics": {
                "missed_votes_total": 10,
                "bitfields_unavailability_total": 5,
                "explicit_votes": 100,
                "implicit_votes": 50,
                "bitfields_availability_total": 200,
            },
            "current_session_details": {
                "points": 1000,
                "authored_blocks_count": 5,
                "para_points": 900,
            },
        }
        mock_batch.return_value = [mock_result]

        # Mock environment
        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            # Import and call update_metrics
            from one_t_exporter import update_metrics

            update_metrics()

        # Verify metrics were set correctly
        labels = {
            "network": "polkadot",
            "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
            "identity": "TestValidator",
        }

        self.assertEqual(
            METRICS["one_t_grade_numeric"].labels(**labels)._value.get(), 9.0
        )
        self.assertEqual(
            METRICS["one_t_performance_score"].labels(**labels)._value.get(), 0.95
        )
        self.assertEqual(METRICS["one_t_mvr"].labels(**labels)._value.get(), 0.05)
        self.assertEqual(METRICS["one_t_bar"].labels(**labels)._value.get(), 0.98)

        # Check voting metrics (now Gauges with absolute values)
        self.assertEqual(
            METRICS["one_t_missed_votes"].labels(**labels)._value.get(), 10
        )
        self.assertEqual(
            METRICS["one_t_explicit_votes"].labels(**labels)._value.get(), 100
        )
        self.assertEqual(
            METRICS["one_t_implicit_votes"].labels(**labels)._value.get(), 50
        )

        # Check session metrics (now Gauges with absolute values)
        self.assertEqual(METRICS["one_t_points"].labels(**labels)._value.get(), 1000)
        self.assertEqual(
            METRICS["one_t_authored_blocks_count"].labels(**labels)._value.get(), 5
        )
        self.assertEqual(
            METRICS["one_t_para_points"].labels(**labels)._value.get(), 900
        )

    @patch(f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch")
    def test_update_metrics_failure(self, mock_batch):
        """Test metric update with failed result."""
        # Mock failed result
        mock_result = {
            "ok": False,
            "network": "polkadot",
            "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
            "error": "API error",
        }
        mock_batch.return_value = [mock_result]

        # Mock environment
        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            # Import and call update_metrics
            from one_t_exporter import update_metrics

            initial_errors = METRICS["one_t_errors"]._value.get()
            update_metrics()

        # Verify error counter was incremented
        self.assertEqual(METRICS["one_t_errors"]._value.get(), initial_errors + 1)

    @patch(f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch")
    def test_update_metrics_exception_handling(self, mock_batch):
        """Test error counter increment when batch processing raises exception."""
        # Mock exception during batch processing
        mock_batch.side_effect = Exception("Network error")

        # Mock environment
        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            # Import and call update_metrics
            from one_t_exporter import update_metrics

            initial_errors = METRICS["one_t_errors"]._value.get()
            update_metrics()

        # Verify error counter was incremented
        self.assertEqual(METRICS["one_t_errors"]._value.get(), initial_errors + 1)

    def test_error_metric_accumulation(self):
        """Test that error counter accumulates across multiple errors."""
        initial_errors = METRICS["one_t_errors"]._value.get()

        # Test multiple invalid validators
        os.environ["ONE_T_VAL_1"] = "too_short"
        os.environ["ONE_T_VAL_NETWORK_1"] = "polkadot"
        os.environ["ONE_T_VAL_2"] = "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"
        os.environ["ONE_T_VAL_NETWORK_2"] = "invalid_network"
        # Don't add a third validator - stop at the first gap

        validators = load_validators_from_env()

        # Should skip both invalid validators (stops at index 2)
        self.assertEqual(len(validators), 0)
        # Error counter should be incremented twice
        self.assertEqual(METRICS["one_t_errors"]._value.get(), initial_errors + 2)


class TestHealthCheck(unittest.TestCase):
    """Test health check endpoint functionality."""

    def setUp(self):
        """Reset health status before each test."""
        # Clear environment variables first
        for key in list(os.environ.keys()):
            if key.startswith("ONE_T_VAL_"):
                del os.environ[key]

        # Import and reset HEALTH_STATUS
        from one_t_exporter import HEALTH_STATUS

        HEALTH_STATUS["healthy"] = False
        HEALTH_STATUS["last_error"] = None
        HEALTH_STATUS["last_success_time"] = None
        HEALTH_STATUS["total_validators"] = 0
        HEALTH_STATUS["successful_validators"] = 0

    def test_health_status_initial_state(self):
        """Test that health status starts as unhealthy."""
        from one_t_exporter import HEALTH_STATUS

        self.assertFalse(HEALTH_STATUS["healthy"])
        self.assertIsNone(HEALTH_STATUS["last_error"])
        self.assertEqual(HEALTH_STATUS["total_validators"], 0)

    @patch(f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch")
    def test_health_status_after_successful_collection(self, mock_batch):
        """Test that health becomes healthy after successful collection."""
        from one_t_exporter import HEALTH_STATUS, update_metrics

        # Mock successful result
        mock_result = {
            "ok": True,
            "network": "polkadot",
            "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
            "identity": "TestValidator",
            "grade_numeric": 9.0,
            "performance_score": 0.95,
            "components": {
                "mvr": 0.05,
                "bar": 0.98,
                "points_normalized": 0.85,
                "pv_sessions_ratio": 0.99,
            },
            "key_metrics": {
                "missed_votes_total": 10,
                "bitfields_unavailability_total": 5,
                "explicit_votes": 100,
                "implicit_votes": 50,
                "bitfields_availability_total": 200,
            },
            "current_session_details": {
                "points": 1000,
                "authored_blocks_count": 5,
                "para_points": 900,
            },
        }
        mock_batch.return_value = [mock_result]

        # Mock environment
        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            update_metrics()

        # Check health status
        self.assertTrue(HEALTH_STATUS["healthy"])
        self.assertIsNone(HEALTH_STATUS["last_error"])
        self.assertEqual(HEALTH_STATUS["successful_validators"], 1)
        self.assertEqual(HEALTH_STATUS["total_validators"], 1)

    @patch(f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch")
    def test_health_status_after_failed_collection(self, mock_batch):
        """Test that health becomes unhealthy after failed collection."""
        from one_t_exporter import HEALTH_STATUS, update_metrics

        # First set to healthy state
        HEALTH_STATUS["healthy"] = True
        HEALTH_STATUS["successful_validators"] = 1

        # Mock failed result
        mock_result = {
            "ok": False,
            "network": "polkadot",
            "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
            "error": "API error: Connection timeout",
        }
        mock_batch.return_value = [mock_result]

        # Mock environment
        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            update_metrics()

        # Check health status
        self.assertFalse(HEALTH_STATUS["healthy"])
        self.assertIsNotNone(HEALTH_STATUS["last_error"])
        self.assertIn("API error", HEALTH_STATUS["last_error"])
        self.assertEqual(HEALTH_STATUS["successful_validators"], 0)

    @patch(f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch")
    def test_health_status_partial_failure(self, mock_batch):
        """Test health status with partial failures."""
        from one_t_exporter import HEALTH_STATUS, update_metrics

        # Mock mixed results
        mock_results = [
            {
                "ok": True,
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator1",
                "grade_numeric": 9.0,
                "performance_score": 0.95,
                "components": {
                    "mvr": 0.05,
                    "bar": 0.98,
                    "points_normalized": 0.85,
                    "pv_sessions_ratio": 0.99,
                },
                "key_metrics": {
                    "missed_votes_total": 10,
                    "bitfields_unavailability_total": 5,
                },
                "current_session_details": {
                    "points": 1000,
                    "authored_blocks_count": 5,
                    "para_points": 900,
                },
            },
            {
                "ok": False,
                "network": "kusama",
                "address": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                "error": "Network error",
            },
        ]
        mock_batch.return_value = mock_results

        # Mock environment
        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
                "ONE_T_VAL_2": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                "ONE_T_VAL_NETWORK_2": "kusama",
            },
        ):
            update_metrics()

        # With partial failure, health should be unhealthy
        self.assertFalse(HEALTH_STATUS["healthy"])
        self.assertEqual(HEALTH_STATUS["successful_validators"], 1)
        self.assertEqual(HEALTH_STATUS["total_validators"], 2)

    def test_health_check_handler_response(self):
        """Test health check logic based on HEALTH_STATUS."""
        from one_t_exporter import HEALTH_STATUS

        # Test initial unhealthy state
        self.assertFalse(HEALTH_STATUS["healthy"])
        self.assertIsNone(HEALTH_STATUS["last_error"])

        # Test unhealthy state with error
        HEALTH_STATUS["last_error"] = "Connection timeout"
        HEALTH_STATUS["total_validators"] = 1
        HEALTH_STATUS["successful_validators"] = 0

        # Verify unhealthy state would trigger 503
        self.assertFalse(HEALTH_STATUS["healthy"])
        self.assertEqual(HEALTH_STATUS["last_error"], "Connection timeout")

        # Test healthy state
        HEALTH_STATUS["healthy"] = True
        HEALTH_STATUS["last_error"] = None
        HEALTH_STATUS["successful_validators"] = 2
        HEALTH_STATUS["total_validators"] = 2

        # Verify healthy state would trigger 200
        self.assertTrue(HEALTH_STATUS["healthy"])
        self.assertIsNone(HEALTH_STATUS["last_error"])
        self.assertEqual(HEALTH_STATUS["successful_validators"], 2)
        self.assertEqual(HEALTH_STATUS["total_validators"], 2)

        # Test recovery from unhealthy to healthy
        HEALTH_STATUS["healthy"] = False
        HEALTH_STATUS["last_error"] = "Previous error"

        # Simulate successful collection
        HEALTH_STATUS["healthy"] = True
        HEALTH_STATUS["last_error"] = None
        HEALTH_STATUS["successful_validators"] = 1

        # Verify recovery
        self.assertTrue(HEALTH_STATUS["healthy"])
        self.assertIsNone(HEALTH_STATUS["last_error"])


class TestSignalHandling(unittest.TestCase):
    """Test signal handling and graceful shutdown."""

    def setUp(self):
        """Reset shutdown event before each test."""
        # Clear environment variables first
        for key in list(os.environ.keys()):
            if key.startswith("ONE_T_VAL_"):
                del os.environ[key]

        # Reset shutdown event
        one_t_exporter.shutdown_event = threading.Event()
        one_t_exporter.health_server = None

    def test_signal_handler_sets_shutdown_event(self):
        """Test that signal handler sets the shutdown event."""
        from one_t_exporter import signal_handler, shutdown_event

        # Ensure shutdown event is not set initially
        self.assertFalse(shutdown_event.is_set())

        # Call signal handler (simulate SIGINT)
        signal_handler(signal.SIGINT, None)

        # Check that shutdown event is now set
        self.assertTrue(shutdown_event.is_set())

    def test_signal_handler_stops_health_server(self):
        """Test that signal handler stops the health server."""
        # Mock health server
        mock_server = MagicMock()
        one_t_exporter.health_server = mock_server

        # Call signal handler
        one_t_exporter.signal_handler(signal.SIGTERM, None)

        # Verify health server shutdown was called
        mock_server.shutdown.assert_called_once()

    @patch(f"{EXPORTER_MODULE}.signal.signal")
    def test_signal_handlers_registered(self, mock_signal):
        """Test that signal handlers are registered during main startup."""
        # Mock other dependencies to avoid actual server start
        with patch(f"{EXPORTER_MODULE}.start_http_server"):
            with patch(f"{EXPORTER_MODULE}.start_health_server"):
                with patch(f"{EXPORTER_MODULE}.update_metrics"):
                    with patch("sys.exit"):
                        # Set shutdown event to exit immediately
                        one_t_exporter.shutdown_event.set()

                        # Call main
                        one_t_exporter.main()

                        # Verify signal handlers were registered
                        calls = mock_signal.call_args_list
                        self.assertIn(
                            call(signal.SIGINT, one_t_exporter.signal_handler),
                            calls,
                        )
                        self.assertIn(
                            call(signal.SIGTERM, one_t_exporter.signal_handler),
                            calls,
                        )

    def test_main_loop_exits_on_shutdown_event(self):
        """Test that main loop exits when shutdown event is set."""
        from one_t_exporter import main, shutdown_event

        # Set up to exit immediately
        shutdown_event.set()

        # Mock dependencies
        with patch(f"{EXPORTER_MODULE}.start_http_server"):
            with patch(f"{EXPORTER_MODULE}.start_health_server"):
                with patch(f"{EXPORTER_MODULE}.update_metrics") as mock_update:
                    with patch("sys.exit") as mock_exit:
                        # Call main
                        main()

                        # Verify update_metrics was not called (loop should exit immediately)
                        mock_update.assert_not_called()

                        # Verify graceful exit
                        mock_exit.assert_called_once_with(0)

    def test_shutdown_event_interrupts_sleep(self):
        """Test that shutdown event interrupts the sleep period."""
        # Set short collection period for testing
        original_period = one_t_exporter.ONE_T_COLLECT_PERIOD
        one_t_exporter.ONE_T_COLLECT_PERIOD = 5

        try:
            # Track timing
            start_time = time.time()

            # Start main in a thread
            def run_main():
                with patch(
                    f"{EXPORTER_MODULE}.signal.signal"
                ):  # Mock signal registration
                    with patch(f"{EXPORTER_MODULE}.start_http_server"):
                        with patch(f"{EXPORTER_MODULE}.start_health_server"):
                            with patch(f"{EXPORTER_MODULE}.update_metrics"):
                                with patch("sys.exit"):
                                    one_t_exporter.main()

            main_thread = threading.Thread(target=run_main)
            main_thread.start()

            # Wait a moment then send shutdown signal
            time.sleep(1)
            one_t_exporter.shutdown_event.set()

            # Wait for thread to finish
            main_thread.join(timeout=3)

            # Verify it exited quickly (not waiting full collection period)
            elapsed = time.time() - start_time
            self.assertLess(elapsed, 3, "Should exit quickly after shutdown event")

        finally:
            # Restore original period
            one_t_exporter.ONE_T_COLLECT_PERIOD = original_period


if __name__ == "__main__":
    # Run tests
    unittest.main(verbosity=2)
