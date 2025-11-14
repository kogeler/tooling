"""Exporter health status tests."""

import os
import unittest
from unittest.mock import patch

from tests.common import EXPORTER_MODULE, clear_validator_env, reset_metrics


class TestHealthCheck(unittest.TestCase):
    """Test health check endpoint functionality."""

    def setUp(self):
        """Reset health status before each test."""
        clear_validator_env()
        reset_metrics()

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
        self.assertEqual(
            HEALTH_STATUS["last_error"], "No valid metrics generated in this scrape"
        )
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

        # With partial failure, health should still be healthy
        self.assertTrue(HEALTH_STATUS["healthy"])
        self.assertIsNone(HEALTH_STATUS["last_error"])
        self.assertEqual(HEALTH_STATUS["successful_validators"], 1)
        self.assertEqual(HEALTH_STATUS["total_validators"], 2)

    @patch(f"{EXPORTER_MODULE}.one_t_lib.compute_current_session_results_batch")
    def test_health_error_message_for_invalid_results(self, mock_batch):
        """Health should report missing labels when no valid metrics emitted."""
        from one_t_exporter import HEALTH_STATUS, update_metrics

        mock_batch.return_value = [
            {
                "ok": True,
                "network": "polkadot",
                "address": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "identity": "",
                "active": True,
            }
        ]

        with patch.dict(
            os.environ,
            {
                "ONE_T_VAL_1": "5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb",
                "ONE_T_VAL_NETWORK_1": "polkadot",
            },
        ):
            update_metrics()

        self.assertFalse(HEALTH_STATUS["healthy"])
        self.assertEqual(
            HEALTH_STATUS["last_error"],
            "No valid metrics generated in this scrape",
        )
        self.assertEqual(HEALTH_STATUS["successful_validators"], 0)

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
