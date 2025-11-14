"""Metrics update and filtering tests for one_t_exporter."""

import os
import unittest
from unittest.mock import patch

from tests.common import (
    EXPORTER_MODULE,
    METRICS,
    clear_validator_env,
    load_validators_from_env,
    reset_metrics,
)


class TestMockedMetricUpdate(unittest.TestCase):
    """Test metric update functionality with mocked data."""

    def setUp(self):
        """Set up test environment."""
        reset_metrics()
        clear_validator_env()

    @patch(f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch")
    def test_update_metrics_success(self, mock_batch):
        """Test successful metric update with mocked data."""
        # Mock successful result
        mock_result = {
            "ok": True,
            "network": "polkadot",
            "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
            "identity": "TestValidator",
            "active": True,
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
            "env": "",
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

    @patch(f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch")
    def test_missing_identity_is_rejected(self, mock_batch):
        """Ensure missing identity is treated as invalid result data."""
        from one_t_exporter import HEALTH_STATUS, update_metrics

        mock_batch.return_value = [
            {
                "ok": True,
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "",  # Missing identity should be rejected
                "active": True,
                "grade_numeric": 9.0,
                "performance_score": 0.95,
                "components": {"mvr": 0.05},
                "key_metrics": {"missed_votes_total": 0},
                "current_session_details": {"points": 0},
            }
        ]

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            initial_errors = METRICS["one_t_errors"]._value.get()
            update_metrics()

        self.assertEqual(METRICS["one_t_errors"]._value.get(), initial_errors + 1)
        self.assertEqual(len(METRICS["one_t_grade_numeric"]._metrics), 0)
        self.assertFalse(HEALTH_STATUS["healthy"])
        self.assertEqual(HEALTH_STATUS["successful_validators"], 0)
        self.assertEqual(
            HEALTH_STATUS["last_error"], "No valid metrics generated in this scrape"
        )

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

    def test_inactive_validator_filtering(self):
        """Test that inactive validators are filtered out and don't get metrics."""
        from one_t_exporter import update_metrics

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
                "ONE_T_VAL_2": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                "ONE_T_VAL_NETWORK_2": "kusama",
            },
        ):
            # Mock results with one active and one inactive validator
            mock_results = [
                {
                    "ok": True,
                    "network": "polkadot",
                    "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                    "identity": "TestValidator1",
                    "active": True,
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
                    "ok": True,
                    "network": "kusama",
                    "address": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                    "identity": "TestValidator2",
                    "active": False,  # This validator is inactive
                    "grade": "-",
                    "grade_numeric": -1.0,
                    "performance_score": 0.75,
                    "components": {
                        "mvr": 0.0,
                        "bar": 1.0,
                        "points_normalized": 0.0,
                        "pv_sessions_ratio": 0.0,
                    },
                    "key_metrics": {
                        "missed_votes_total": 0,
                        "bitfields_unavailability_total": 0,
                    },
                    "current_session_details": {
                        "points": 0,
                        "authored_blocks_count": 0,
                        "para_points": 0,
                    },
                },
            ]

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = mock_results
                update_metrics()

            # Verify only active validator has metrics
            active_labels = {
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator1",
                "env": "",
            }
            inactive_labels = {
                "network": "kusama",
                "address": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                "identity": "TestValidator2",
                "env": "",
            }

            # Active validator should have metrics
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**active_labels)._value.get(), 9.0
            )
            self.assertEqual(
                METRICS["one_t_performance_score"].labels(**active_labels)._value.get(),
                0.95,
            )

            # Inactive validator should NOT have metrics (should be cleared)
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**inactive_labels)._value.get(),
                0.0,
            )
            self.assertEqual(
                METRICS["one_t_performance_score"]
                .labels(**inactive_labels)
                ._value.get(),
                0.0,
            )

    def test_all_validators_inactive(self):
        """Test behavior when all validators are inactive."""
        from one_t_exporter import HEALTH_STATUS, update_metrics

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
                "ONE_T_VAL_2": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                "ONE_T_VAL_NETWORK_2": "kusama",
            },
        ):
            # Mock results with all validators inactive
            mock_results = [
                {
                    "ok": True,
                    "network": "polkadot",
                    "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                    "identity": "TestValidator1",
                    "active": False,
                    "grade": "-",
                    "grade_numeric": -1.0,
                    "performance_score": 0.75,
                    "components": {
                        "mvr": 0.0,
                        "bar": 1.0,
                        "points_normalized": 0.0,
                        "pv_sessions_ratio": 0.0,
                    },
                    "key_metrics": {
                        "missed_votes_total": 0,
                        "bitfields_unavailability_total": 0,
                    },
                    "current_session_details": {
                        "points": 0,
                        "authored_blocks_count": 0,
                        "para_points": 0,
                    },
                },
                {
                    "ok": True,
                    "network": "kusama",
                    "address": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                    "identity": "TestValidator2",
                    "active": False,
                    "grade": "-",
                    "grade_numeric": -1.0,
                    "performance_score": 0.75,
                    "components": {
                        "mvr": 0.0,
                        "bar": 1.0,
                        "points_normalized": 0.0,
                        "pv_sessions_ratio": 0.0,
                    },
                    "key_metrics": {
                        "missed_votes_total": 0,
                        "bitfields_unavailability_total": 0,
                    },
                    "current_session_details": {
                        "points": 0,
                        "authored_blocks_count": 0,
                        "para_points": 0,
                    },
                },
            ]

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = mock_results
                update_metrics()

            # Health status should reflect that no validators were processed
            self.assertEqual(HEALTH_STATUS["successful_validators"], 0)
            self.assertEqual(HEALTH_STATUS["total_validators"], 2)
            self.assertFalse(HEALTH_STATUS["healthy"])
            self.assertEqual(
                HEALTH_STATUS["last_error"],
                "No valid metrics generated in this scrape",
            )

            # No metrics should be set for inactive validators
            labels1 = {
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator1",
                "env": "",
            }
            labels2 = {
                "network": "kusama",
                "address": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                "identity": "TestValidator2",
                "env": "",
            }

            # All metrics should be at default values (0.0) for inactive validators
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels1)._value.get(), 0.0
            )
            self.assertEqual(
                METRICS["one_t_performance_score"].labels(**labels1)._value.get(), 0.0
            )
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels2)._value.get(), 0.0
            )
            self.assertEqual(
                METRICS["one_t_performance_score"].labels(**labels2)._value.get(), 0.0
            )

    def test_inactive_validator_metric_clearing(self):
        """Test that metrics are cleared for inactive validators."""
        from one_t_exporter import update_metrics

        # First, set up some metrics for validators
        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
                "ONE_T_VAL_2": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                "ONE_T_VAL_NETWORK_2": "kusama",
            },
        ):
            # Mock successful results for both validators
            mock_results = [
                {
                    "ok": True,
                    "network": "polkadot",
                    "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                    "identity": "TestValidator1",
                    "active": True,
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
                    "ok": True,
                    "network": "kusama",
                    "address": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                    "identity": "TestValidator2",
                    "active": True,
                    "grade_numeric": 8.0,
                    "performance_score": 0.85,
                    "components": {
                        "mvr": 0.10,
                        "bar": 0.95,
                        "points_normalized": 0.75,
                        "pv_sessions_ratio": 0.90,
                    },
                    "key_metrics": {
                        "missed_votes_total": 15,
                        "bitfields_unavailability_total": 8,
                    },
                    "current_session_details": {
                        "points": 800,
                        "authored_blocks_count": 3,
                        "para_points": 740,
                    },
                },
            ]

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = mock_results
                update_metrics()

            # Verify both validators have metrics
            active_key = (
                "polkadot",
                "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "TestValidator1",
                "",
            )
            second_key = (
                "kusama",
                "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                "TestValidator2",
                "",
            )
            self.assertIn(active_key, METRICS["one_t_grade_numeric"]._metrics)
            self.assertIn(second_key, METRICS["one_t_grade_numeric"]._metrics)
            self.assertEqual(
                METRICS["one_t_grade_numeric"]._metrics[active_key]._value.get(), 9.0
            )
            self.assertEqual(
                METRICS["one_t_grade_numeric"]._metrics[second_key]._value.get(), 8.0
            )

            # Now simulate second validator becoming inactive
            mock_results_inactive = [
                {
                    "ok": True,
                    "network": "polkadot",
                    "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                    "identity": "TestValidator1",
                    "active": True,
                    "grade_numeric": 9.5,
                    "performance_score": 0.96,
                    "components": {
                        "mvr": 0.04,
                        "bar": 0.99,
                        "points_normalized": 0.87,
                        "pv_sessions_ratio": 0.98,
                    },
                    "key_metrics": {
                        "missed_votes_total": 8,
                        "bitfields_unavailability_total": 3,
                    },
                    "current_session_details": {
                        "points": 1050,
                        "authored_blocks_count": 6,
                        "para_points": 930,
                    },
                },
                {
                    "ok": True,
                    "network": "kusama",
                    "address": "5Dv8i8YqQZ7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q7Q",
                    "identity": "TestValidator2",
                    "active": False,
                    "error": "Validator not active in current session",
                },
            ]

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = mock_results_inactive
                update_metrics()

            # Verify active validator still has metrics (with updated values)
            self.assertEqual(
                METRICS["one_t_grade_numeric"]._metrics[active_key]._value.get(), 9.5
            )

            # Verify inactive validator's metrics are cleared
            self.assertNotIn(second_key, METRICS["one_t_grade_numeric"]._metrics)


class TestActiveValidatorFiltering(unittest.TestCase):
    """Test active validator filtering functionality."""

    def setUp(self):
        """Set up test environment."""
        reset_metrics()
        clear_validator_env()

    def test_active_field_usage(self):
        """Test that exporter uses the active field from parser correctly."""
        from one_t_exporter import update_metrics

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            # Mock result with active=False
            mock_result = {
                "ok": True,
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "active": False,  # Explicitly inactive
                "grade": "-",  # Grade is "-" so validator is inactive
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
            }

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = [mock_result]
                update_metrics()

            # Even though data is provided, validator should not have metrics due to active=False
            labels = {
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "env": "",
            }

            # Metrics should be at default values (0.0) for inactive validator
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels)._value.get(), 0.0
            )
            self.assertEqual(
                METRICS["one_t_performance_score"].labels(**labels)._value.get(), 0.0
            )

    def test_metric_clearing_on_update(self):
        """Test that metrics are cleared between updates."""
        from one_t_exporter import update_metrics

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            # First update - active validator
            mock_results_active = [
                {
                    "ok": True,
                    "network": "polkadot",
                    "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                    "identity": "TestValidator",
                    "active": True,
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
                }
            ]

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = mock_results_active
                update_metrics()

            labels = {
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "env": "",
            }

            # Should have metrics after first update
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels)._value.get(), 9.0
            )

            # Second update - same validator becomes inactive
            mock_results_inactive = [
                {
                    "ok": True,
                    "network": "polkadot",
                    "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                    "identity": "TestValidator",
                    "active": False,  # Now inactive
                    "grade": "-",  # Grade is "-" so validator is inactive
                    "grade_numeric": -1.0,
                    "performance_score": 0.75,
                    "components": {
                        "mvr": 0.0,
                        "bar": 1.0,
                        "points_normalized": 0.0,
                        "pv_sessions_ratio": 0.0,
                    },
                    "key_metrics": {
                        "missed_votes_total": 0,
                        "bitfields_unavailability_total": 0,
                    },
                    "current_session_details": {
                        "points": 0,
                        "authored_blocks_count": 0,
                        "para_points": 0,
                    },
                }
            ]

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = mock_results_inactive
                update_metrics()

            # Metrics should be cleared for inactive validator
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels)._value.get(), 0.0
            )
            self.assertEqual(
                METRICS["one_t_performance_score"].labels(**labels)._value.get(), 0.0
            )


class TestEnvLabelSupport(unittest.TestCase):
    """Test ONE_T_ENV environment variable support."""

    def setUp(self):
        """Set up test environment."""
        reset_metrics()
        clear_validator_env()

    def test_env_label_with_value(self):
        """Test that env label is included when ONE_T_ENV is set."""
        from one_t_exporter import update_metrics

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
                "ONE_T_ENV": "production",  # Set env value
            },
        ):
            mock_result = {
                "ok": True,
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "active": True,
                "grade": "A+",
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
            }

            with patch(f"{EXPORTER_MODULE}.ONE_T_ENV", "production"):
                with patch(
                    f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
                ) as mock_batch:
                    mock_batch.return_value = [mock_result]
                    update_metrics()

            labels = {
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "env": "production",  # Should have env value
            }

            # Metrics should be set with env label
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels)._value.get(), 9.0
            )
            self.assertEqual(
                METRICS["one_t_performance_score"].labels(**labels)._value.get(), 0.95
            )

    def test_env_label_empty_string(self):
        """Test that env label is empty string when ONE_T_ENV is not set."""
        from one_t_exporter import update_metrics

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
                # ONE_T_ENV not set
            },
        ):
            mock_result = {
                "ok": True,
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "active": True,
                "grade": "A+",
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
            }

            with patch(f"{EXPORTER_MODULE}.ONE_T_ENV", ""):
                with patch(
                    f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
                ) as mock_batch:
                    mock_batch.return_value = [mock_result]
                    update_metrics()

            labels = {
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "env": "",  # Should be empty string when not set
            }

            # Metrics should be set with empty env label
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels)._value.get(), 9.0
            )


class TestSimplifiedActiveLogic(unittest.TestCase):
    """Test the simplified active field logic based only on grade."""

    def setUp(self):
        reset_metrics()
        clear_validator_env()

    def test_active_based_on_grade(self):
        """Test that active field is determined solely by grade value."""
        from one_t_exporter import update_metrics

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            # Test case 1: Validator with grade "A+" should be active
            mock_result_active = {
                "ok": True,
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "active": True,  # Active because grade is "A+"
                "grade": "A+",
                "grade_numeric": 10.0,
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
            }

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = [mock_result_active]
                update_metrics()

            labels = {
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "env": "",
            }

            # Active validator should have metrics
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels)._value.get(), 10.0
            )

            # Clear metrics for next test
            for metric_name, metric in METRICS.items():
                if hasattr(metric, "_metrics"):
                    metric._metrics.clear()

            # Test case 2: Validator with grade "-" should be inactive
            mock_result_inactive = {
                "ok": True,
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "active": False,  # Inactive because grade is "-"
                "grade": "-",
                "grade_numeric": -1.0,
                "performance_score": 0.75,
                "components": {
                    "mvr": 0.0,
                    "bar": 1.0,
                    "points_normalized": 0.0,
                    "pv_sessions_ratio": 0.0,
                },
                "key_metrics": {
                    "missed_votes_total": 0,
                    "bitfields_unavailability_total": 0,
                },
                "current_session_details": {
                    "points": 0,
                    "authored_blocks_count": 0,
                    "para_points": 0,
                },
            }

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = [mock_result_inactive]
                update_metrics()

            # Inactive validator should not have metrics (should be 0.0)
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels)._value.get(), 0.0
            )

    def test_grade_none_makes_inactive(self):
        """Test that validator with None grade is inactive."""
        from one_t_exporter import update_metrics

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            # Validator with None grade should be inactive
            mock_result = {
                "ok": True,
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "active": False,  # Inactive because grade is None
                "grade": None,
                "grade_numeric": -1.0,
                "performance_score": 0.75,
                "components": {
                    "mvr": 0.0,
                    "bar": 1.0,
                    "points_normalized": 0.0,
                    "pv_sessions_ratio": 0.0,
                },
                "key_metrics": {
                    "missed_votes_total": 0,
                    "bitfields_unavailability_total": 0,
                },
                "current_session_details": {
                    "points": 0,
                    "authored_blocks_count": 0,
                    "para_points": 0,
                },
            }

            with patch(
                f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch"
            ) as mock_batch:
                mock_batch.return_value = [mock_result]
                update_metrics()

            labels = {
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "TestValidator",
                "env": "",
            }

            # Inactive validator should not have metrics
            self.assertEqual(
                METRICS["one_t_grade_numeric"].labels(**labels)._value.get(), 0.0
            )
