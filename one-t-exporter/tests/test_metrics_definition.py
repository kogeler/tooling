"""Prometheus metric definition tests for one_t_exporter."""

import unittest

from tests.common import METRICS


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
        expected_labels = ["network", "address", "identity", "env"]

        for metric_name, metric in METRICS.items():
            # Skip the errors metric which doesn't have labels
            if metric_name == "one_t_errors":
                continue

            with self.subTest(metric=metric_name):
                # Check that metric has the _labelnames attribute with expected labels
                self.assertEqual(set(metric._labelnames), set(expected_labels))
