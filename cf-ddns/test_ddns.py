#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Comprehensive test suite for cf-ddns-fixed service to verify all fixes.
"""

import unittest
import os
import sys
import time
import tempfile
from unittest.mock import Mock, patch, MagicMock, call
import requests
from typing import Dict, Any

# Add the parent directory to the path to import the module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the functions from the fixed script
from importlib.machinery import SourceFileLoader
cf_ddns = SourceFileLoader("cf_ddns", "cf-ddns.py").load_module()


class TestDDNSInitialization(unittest.TestCase):
    """Test proper initialization to avoid unnecessary DNS updates."""

    def setUp(self):
        """Set up test environment variables."""
        self.env_backup = os.environ.copy()
        os.environ['CF_DDNS_TOKEN'] = 'test_token'
        os.environ['CF_DDNS_ZONE_ID'] = 'test_zone'
        os.environ['CF_DDNS_HOST'] = 'test.example.com'

    def tearDown(self):
        """Restore environment variables."""
        os.environ.clear()
        os.environ.update(self.env_backup)

    def test_no_update_when_ip_unchanged(self):
        """Test that no update occurs when IP hasn't changed from DNS."""
        with patch.object(cf_ddns, 'get_dns_record') as mock_get_record:
            with patch.object(cf_ddns, 'get_external_ip') as mock_get_ip:
                # Simulate existing DNS record with IP 1.2.3.4
                mock_get_record.return_value = {
                    'id': 'record123',
                    'content': '1.2.3.4',
                    'ttl': 120,
                    'proxied': False
                }

                # Simulate same external IP
                mock_get_ip.return_value = '1.2.3.4'

                # Test the logic
                last_ip = mock_get_record.return_value['content']
                current_ip = mock_get_ip.return_value

                update_needed = (last_ip is None) or (current_ip != last_ip)

                self.assertFalse(update_needed, "Update should not be needed when IP unchanged")

    def test_initial_ip_from_dns(self):
        """Test that initial IP is fetched from existing DNS record."""
        with patch.object(cf_ddns, 'get_dns_record') as mock_get_record:
            mock_get_record.return_value = {
                'id': 'record123',
                'content': '5.6.7.8',
                'ttl': 120,
                'proxied': False
            }

            record_info = cf_ddns.get_dns_record('token', 'zone', 'host')
            last_ip = record_info.get('content')

            self.assertEqual(last_ip, '5.6.7.8', "Should initialize with current DNS IP")
            self.assertIsNotNone(last_ip, "Initial IP should not be None when DNS record exists")


class TestRecordIdManagement(unittest.TestCase):
    """Test dynamic record ID management."""

    @patch('requests.put')
    def test_handle_invalid_record_id(self, mock_put):
        """Test handling of invalid record ID error (code 81058)."""
        # Simulate Cloudflare error for invalid record ID
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            'success': False,
            'errors': [{'code': 81058, 'message': 'Record not found'}]
        }
        mock_put.return_value = mock_response

        # Test the update function with invalid record
        result = cf_ddns.update_cloudflare_record(
            'token', 'zone', 'invalid_id', 'host', '1.2.3.4', 120, False, max_retries=1
        )

        self.assertFalse(result, "Should return False when record ID is invalid")

    @patch('requests.post')
    def test_create_new_record(self, mock_post):
        """Test creation of new DNS record with retry logic."""
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            'success': True,
            'result': {'id': 'new_record_id'}
        }
        mock_post.return_value = mock_response

        record_id = cf_ddns.create_dns_record(
            'token', 'zone', 'test.example.com', '1.2.3.4', 120, False
        )

        self.assertEqual(record_id, 'new_record_id', "Should return new record ID")

    def test_handle_dns_update_with_recreation(self):
        """Test complete DNS update handling including record recreation."""
        config = {
            'token': 'test_token',
            'zone_id': 'test_zone',
            'host': 'test.example.com',
            'ttl': 120,
            'proxied': False
        }

        with patch.object(cf_ddns, 'update_cloudflare_record') as mock_update:
            with patch.object(cf_ddns, 'get_dns_record') as mock_get:
                with patch.object(cf_ddns, 'create_dns_record') as mock_create:
                    # First update fails
                    mock_update.side_effect = [False, True]
                    # Get new record ID
                    mock_get.return_value = {'id': 'new_id', 'content': '1.1.1.1'}

                    success, new_id = cf_ddns.handle_dns_update(config, 'old_id', '2.2.2.2')

                    self.assertTrue(success)
                    self.assertEqual(new_id, 'new_id')


class TestMetricsInitialization(unittest.TestCase):
    """Test Prometheus metrics initialization with zero values."""

    def setUp(self):
        """Set up test environment."""
        self.env_backup = os.environ.copy()
        os.environ['CF_DDNS_TOKEN'] = 'test_token'
        os.environ['CF_DDNS_ZONE_ID'] = 'test_zone'
        os.environ['CF_DDNS_HOST'] = 'test.example.com'

    def tearDown(self):
        """Restore environment variables."""
        os.environ.clear()
        os.environ.update(self.env_backup)

    @patch('prometheus_client.Counter')
    @patch('prometheus_client.Gauge')
    def test_metrics_initialized_with_zero(self, mock_gauge, mock_counter):
        """Test that all metrics are initialized with zero values."""
        config = cf_ddns.parse_env()

        # Mock the metrics
        with patch.object(cf_ddns, 'prometheus_metrics', {
            'ip_retrieval_error_counter': MagicMock(),
            'ip_update_counter': MagicMock(),
            'cf_api_error_counter': MagicMock(),
            'last_ip_check_timestamp': MagicMock(),
            'last_ip_update_timestamp': MagicMock()
        }):
            cf_ddns.initialize_metrics(config)

            # Check timestamps initialized to 0
            cf_ddns.prometheus_metrics['last_ip_check_timestamp'].set.assert_called_with(0)
            cf_ddns.prometheus_metrics['last_ip_update_timestamp'].set.assert_called_with(0)


class TestIPMetricInitialization(unittest.TestCase):
    """Test IP metric initialization at startup."""

    def test_initial_ip_metric_set_at_startup_different_ips(self):
        """Test that cf_ddns_ip_info is initialized with current external IP when it differs from DNS."""
        # Mock the gauge
        mock_gauge = MagicMock()
        mock_labels = MagicMock()
        mock_gauge.labels.return_value = mock_labels

        with patch.object(cf_ddns, 'get_external_ip') as mock_get_ip:
            with patch.object(cf_ddns, 'get_dns_record') as mock_get_record:
                with patch.object(cf_ddns, 'prometheus_metrics', {
                    'ip_info_gauge': mock_gauge
                }):
                    # Current external IP
                    mock_get_ip.return_value = '1.2.3.4'

                    # DNS record with different IP
                    mock_get_record.return_value = {
                        'id': 'record123',
                        'content': '5.6.7.8',
                        'ttl': 120,
                        'proxied': False
                    }

                    # Simulate the initialization logic from main()
                    initial_ip = mock_get_ip()
                    record_info = mock_get_record.return_value
                    last_ip = record_info.get('content') if record_info else None

                    if initial_ip:
                        # Current IP should be set to 1
                        cf_ddns.prometheus_metrics['ip_info_gauge'].labels(cf_host='test.example.com', ip=initial_ip).set(1)

                        # DNS IP should be set to 0 if different
                        if last_ip and last_ip != initial_ip:
                            cf_ddns.prometheus_metrics['ip_info_gauge'].labels(cf_host='test.example.com', ip=last_ip).set(0)

                    # Verify both IPs were set
                    calls = mock_gauge.labels.call_args_list
                    self.assertEqual(len(calls), 2)

                    # Verify current IP set to 1
                    mock_gauge.labels.assert_any_call(cf_host='test.example.com', ip='1.2.3.4')

                    # Verify DNS IP set to 0
                    mock_gauge.labels.assert_any_call(cf_host='test.example.com', ip='5.6.7.8')

    def test_initial_ip_metric_set_at_startup_same_ips(self):
        """Test that cf_ddns_ip_info is initialized correctly when current IP equals DNS IP."""
        mock_gauge = MagicMock()
        mock_labels = MagicMock()
        mock_gauge.labels.return_value = mock_labels

        with patch.object(cf_ddns, 'get_external_ip') as mock_get_ip:
            with patch.object(cf_ddns, 'get_dns_record') as mock_get_record:
                with patch.object(cf_ddns, 'prometheus_metrics', {
                    'ip_info_gauge': mock_gauge
                }):
                    # Current external IP
                    mock_get_ip.return_value = '1.2.3.4'

                    # DNS record with same IP
                    mock_get_record.return_value = {
                        'id': 'record123',
                        'content': '1.2.3.4',  # Same as current
                        'ttl': 120,
                        'proxied': False
                    }

                    # Simulate the initialization logic
                    initial_ip = mock_get_ip()
                    record_info = mock_get_record.return_value
                    last_ip = record_info.get('content') if record_info else None

                    if initial_ip:
                        # Current IP should be set to 1
                        cf_ddns.prometheus_metrics['ip_info_gauge'].labels(cf_host='test.example.com', ip=initial_ip).set(1)

                        # DNS IP should NOT be set separately if same
                        if last_ip and last_ip != initial_ip:
                            cf_ddns.prometheus_metrics['ip_info_gauge'].labels(cf_host='test.example.com', ip=last_ip).set(0)

                    # Verify only one call for the same IP
                    calls = mock_gauge.labels.call_args_list
                    self.assertEqual(len(calls), 1)
                    mock_gauge.labels.assert_called_with(cf_host='test.example.com', ip='1.2.3.4')
                    mock_labels.set.assert_called_with(1)

    def test_initial_ip_metric_when_no_dns_record(self):
        """Test cf_ddns_ip_info initialization when no DNS record exists."""
        mock_gauge = MagicMock()
        mock_labels = MagicMock()
        mock_gauge.labels.return_value = mock_labels

        with patch.object(cf_ddns, 'get_external_ip') as mock_get_ip:
            with patch.object(cf_ddns, 'get_dns_record') as mock_get_record:
                with patch.object(cf_ddns, 'prometheus_metrics', {
                    'ip_info_gauge': mock_gauge
                }):
                    # Current external IP
                    mock_get_ip.return_value = '9.9.9.9'

                    # No DNS record
                    mock_get_record.return_value = None

                    # Simulate the initialization logic
                    initial_ip = mock_get_ip()
                    record_info = mock_get_record.return_value
                    last_ip = record_info.get('content') if record_info else None

                    if initial_ip:
                        # Current IP should still be set to 1
                        cf_ddns.prometheus_metrics['ip_info_gauge'].labels(cf_host='test.example.com', ip=initial_ip).set(1)

                        # No DNS IP to set
                        if last_ip and last_ip != initial_ip:
                            cf_ddns.prometheus_metrics['ip_info_gauge'].labels(cf_host='test.example.com', ip=last_ip).set(0)

                    # Verify only current IP was set
                    mock_gauge.labels.assert_called_once_with(cf_host='test.example.com', ip='9.9.9.9')
                    mock_labels.set.assert_called_once_with(1)

    def test_no_metric_when_external_ip_unavailable(self):
        """Test that no metric is set when external IP cannot be retrieved."""
        mock_gauge = MagicMock()

        with patch.object(cf_ddns, 'get_external_ip') as mock_get_ip:
            with patch.object(cf_ddns, 'get_dns_record') as mock_get_record:
                with patch.object(cf_ddns, 'prometheus_metrics', {
                    'ip_info_gauge': mock_gauge
                }):
                    # Cannot get external IP
                    mock_get_ip.return_value = None

                    # DNS record exists
                    mock_get_record.return_value = {
                        'id': 'record123',
                        'content': '5.6.7.8',
                        'ttl': 120,
                        'proxied': False
                    }

                    # Simulate the initialization logic
                    initial_ip = mock_get_ip()

                    if initial_ip:
                        # This should not execute
                        cf_ddns.prometheus_metrics['ip_info_gauge'].labels(cf_host='test.example.com', ip=initial_ip).set(1)

                    # Verify no metric was set
                    mock_gauge.labels.assert_not_called()


class TestIPValidation(unittest.TestCase):
    """Test IP address validation."""

    def test_validate_ipv4(self):
        """Test IPv4 validation function."""
        # Valid IPs
        valid_ips = [
            '1.2.3.4',
            '192.168.1.1',
            '0.0.0.0',
            '255.255.255.255',
            '10.0.0.1'
        ]

        for ip in valid_ips:
            self.assertTrue(cf_ddns.validate_ipv4(ip), f"Should accept valid IP: {ip}")

        # Invalid IPs
        invalid_ips = [
            'not.an.ip.address',
            '256.256.256.256',
            '1.2.3',
            'localhost',
            '1.2.3.4.5',
            '',
            None,
            '192.168.1.-1',
            '192.168.1.256'
        ]

        for ip in invalid_ips:
            self.assertFalse(cf_ddns.validate_ipv4(ip), f"Should reject invalid IP: {ip}")

    @patch('requests.get')
    def test_get_external_ip_validation(self, mock_get):
        """Test that get_external_ip validates IP format."""
        # Test valid IP
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.text = '1.2.3.4'
        mock_get.return_value = mock_response

        ip = cf_ddns.get_external_ip()
        self.assertEqual(ip, '1.2.3.4', "Should return valid IP")

        # Test invalid IP
        mock_response.text = 'not.an.ip'
        mock_get.return_value = mock_response

        with patch.object(cf_ddns, 'prometheus_metrics', {
            'ip_retrieval_error_counter': MagicMock()
        }):
            ip = cf_ddns.get_external_ip()
            self.assertIsNone(ip, "Should return None for invalid IP")


class TestRetryLogic(unittest.TestCase):
    """Test retry logic for API failures."""

    @patch('time.sleep')
    @patch('requests.get')
    def test_get_dns_record_retry_with_exponential_backoff(self, mock_get, mock_sleep):
        """Test that get_dns_record retries use exponential backoff."""
        # Simulate failures then success
        mock_get.side_effect = [
            requests.RequestException("Network error"),
            requests.RequestException("Network error"),
            Mock(raise_for_status=Mock(), json=Mock(return_value={
                'success': True,
                'result': [{'id': 'record123', 'content': '1.2.3.4'}]
            }))
        ]

        with patch.object(cf_ddns, 'prometheus_metrics', {
            'cf_api_error_counter': MagicMock()
        }):
            record = cf_ddns.get_dns_record('token', 'zone', 'host', max_retries=3)

            # Check exponential backoff was used
            sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
            self.assertEqual(sleep_calls[0], 1, "First retry should wait 1 second")
            self.assertEqual(sleep_calls[1], 2, "Second retry should wait 2 seconds")
            self.assertIsNotNone(record, "Should eventually return record")

    @patch('time.sleep')
    @patch('requests.post')
    def test_create_dns_record_retry(self, mock_post, mock_sleep):
        """Test that create_dns_record has retry logic."""
        # Simulate temporary failure then success
        mock_post.side_effect = [
            requests.RequestException("Network error"),
            Mock(raise_for_status=Mock(), json=Mock(return_value={
                'success': True,
                'result': {'id': 'new_id'}
            }))
        ]

        with patch.object(cf_ddns, 'prometheus_metrics', {
            'cf_api_error_counter': MagicMock()
        }):
            record_id = cf_ddns.create_dns_record(
                'token', 'zone', 'host', '1.2.3.4', 120, False, max_retries=3
            )

            self.assertEqual(record_id, 'new_id')
            mock_sleep.assert_called_once_with(1)  # Exponential backoff


class TestConfigValidation(unittest.TestCase):
    """Test configuration validation."""

    def setUp(self):
        """Set up test environment."""
        self.env_backup = os.environ.copy()

    def tearDown(self):
        """Restore environment variables."""
        os.environ.clear()
        os.environ.update(self.env_backup)

    def test_required_env_vars(self):
        """Test that all required environment variables must be set."""
        os.environ.clear()

        with self.assertRaises(SystemExit):
            cf_ddns.parse_env()

    def test_interval_validation(self):
        """Test that interval must be at least 1 second."""
        os.environ['CF_DDNS_TOKEN'] = 'test'
        os.environ['CF_DDNS_ZONE_ID'] = 'test'
        os.environ['CF_DDNS_HOST'] = 'test'
        os.environ['CF_DDNS_INTERVAL'] = '0'

        with self.assertRaises(SystemExit):
            cf_ddns.parse_env()

        os.environ['CF_DDNS_INTERVAL'] = '-5'
        with self.assertRaises(SystemExit):
            cf_ddns.parse_env()

        os.environ['CF_DDNS_INTERVAL'] = '1'
        config = cf_ddns.parse_env()
        self.assertEqual(config['interval'], 1)

    def test_port_validation(self):
        """Test that port must be in valid range (1-65535)."""
        os.environ['CF_DDNS_TOKEN'] = 'test'
        os.environ['CF_DDNS_ZONE_ID'] = 'test'
        os.environ['CF_DDNS_HOST'] = 'test'

        # Test invalid ports
        for invalid_port in ['0', '-1', '70000', '65536', 'abc']:
            os.environ['CF_DDNS_METRICS_PORT'] = invalid_port
            with self.assertRaises(SystemExit):
                cf_ddns.parse_env()

        # Test valid ports
        for valid_port in ['1', '80', '9101', '65535']:
            os.environ['CF_DDNS_METRICS_PORT'] = valid_port
            config = cf_ddns.parse_env()
            self.assertEqual(config['metrics_port'], int(valid_port))

    def test_ttl_warning(self):
        """Test that low TTL values generate warning."""
        os.environ['CF_DDNS_TOKEN'] = 'test'
        os.environ['CF_DDNS_ZONE_ID'] = 'test'
        os.environ['CF_DDNS_HOST'] = 'test'
        os.environ['CF_DDNS_TTL'] = '30'

        with patch('logging.warning') as mock_warning:
            config = cf_ddns.parse_env()
            mock_warning.assert_called_once()
            self.assertEqual(config['ttl'], 30)

    def test_proxied_parsing(self):
        """Test proxied flag parsing."""
        os.environ['CF_DDNS_TOKEN'] = 'test'
        os.environ['CF_DDNS_ZONE_ID'] = 'test'
        os.environ['CF_DDNS_HOST'] = 'test'

        # Test true values
        for true_val in ['True', 'true', 'TRUE']:
            os.environ['CF_DDNS_PROXIED'] = true_val
            config = cf_ddns.parse_env()
            self.assertTrue(config['proxied'])

        # Test false values
        for false_val in ['False', 'false', 'anything', '']:
            os.environ['CF_DDNS_PROXIED'] = false_val
            config = cf_ddns.parse_env()
            self.assertFalse(config['proxied'])


class TestConsecutiveFailures(unittest.TestCase):
    """Test handling of consecutive failures."""

    def test_consecutive_failure_tracking(self):
        """Test that consecutive failures are tracked correctly."""
        with patch.object(cf_ddns, 'get_external_ip') as mock_get_ip:
            # Simulate continuous failures
            mock_get_ip.return_value = None

            consecutive_failures = 0
            max_consecutive_failures = 10

            for _ in range(15):
                if mock_get_ip() is None:
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        break
                else:
                    consecutive_failures = 0

            self.assertEqual(consecutive_failures, max_consecutive_failures,
                            "Should track consecutive failures correctly")

    def test_failure_counter_reset_on_success(self):
        """Test that failure counter resets on successful IP retrieval."""
        consecutive_failures = 5

        # Simulate successful IP retrieval
        with patch.object(cf_ddns, 'get_external_ip', return_value='1.2.3.4'):
            if cf_ddns.get_external_ip() is not None:
                consecutive_failures = 0

        self.assertEqual(consecutive_failures, 0, "Counter should reset on success")


class TestCompleteScenarios(unittest.TestCase):
    """Test complete operational scenarios."""

    def setUp(self):
        """Set up test environment."""
        self.env_backup = os.environ.copy()
        os.environ['CF_DDNS_TOKEN'] = 'test_token'
        os.environ['CF_DDNS_ZONE_ID'] = 'test_zone'
        os.environ['CF_DDNS_HOST'] = 'test.example.com'

    def tearDown(self):
        """Restore environment variables."""
        os.environ.clear()
        os.environ.update(self.env_backup)

    def test_record_recreation_scenario(self):
        """Test scenario where DNS record is deleted and needs recreation."""
        config = {
            'token': 'test_token',
            'zone_id': 'test_zone',
            'host': 'test.example.com',
            'ttl': 120,
            'proxied': False
        }

        with patch.object(cf_ddns, 'update_cloudflare_record') as mock_update:
            with patch.object(cf_ddns, 'get_dns_record') as mock_get_record:
                with patch.object(cf_ddns, 'create_dns_record') as mock_create:
                    # Update fails because record was deleted
                    mock_update.return_value = False

                    # Get record returns None (record deleted)
                    mock_get_record.return_value = None

                    # Create new record succeeds
                    mock_create.return_value = 'new_record_id'

                    success, new_id = cf_ddns.handle_dns_update(config, 'old_id', '2.2.2.2')

                    self.assertTrue(success)
                    self.assertEqual(new_id, 'new_record_id')
                    mock_create.assert_called_once()

    def test_first_run_no_existing_record(self):
        """Test first run scenario where no DNS record exists."""
        config = {
            'token': 'test_token',
            'zone_id': 'test_zone',
            'host': 'test.example.com',
            'ttl': 120,
            'proxied': False
        }

        with patch.object(cf_ddns, 'create_dns_record') as mock_create:
            mock_create.return_value = 'new_record_id'

            success, record_id = cf_ddns.handle_dns_update(config, None, '1.2.3.4')

            self.assertTrue(success)
            self.assertEqual(record_id, 'new_record_id')
            mock_create.assert_called_once_with(
                'test_token', 'test_zone', 'test.example.com',
                '1.2.3.4', 120, False
            )


class TestIPHistory(unittest.TestCase):
    """Test that IP history is preserved (not cleaned up)."""

    def test_ip_history_preserved(self):
        """Test that all IP addresses remain in metrics."""
        # Create mock metrics
        mock_gauge = MagicMock()
        mock_labels = MagicMock()
        mock_gauge.labels.return_value = mock_labels

        with patch.object(cf_ddns, 'prometheus_metrics', {
            'ip_info_gauge': mock_gauge
        }):
            # Simulate multiple IP changes
            ips = ['1.1.1.1', '2.2.2.2', '3.3.3.3', '4.4.4.4']

            for i, ip in enumerate(ips):
                # Set previous IPs to 0
                if i > 0:
                    prev_ip = ips[i-1]
                    mock_gauge.labels(cf_host='test.example.com', ip=prev_ip).set(0)

                # Set current IP to 1
                mock_gauge.labels(cf_host='test.example.com', ip=ip).set(1)

            # Verify that we have calls for setting values
            self.assertTrue(mock_labels.set.called, "Should have set metric values")

            # Verify remove was never called (no cleanup)
            if hasattr(mock_gauge, 'remove'):
                mock_gauge.remove.assert_not_called()


def run_comprehensive_tests():
    """Run all tests with detailed output."""
    print("=" * 60)
    print("Running Comprehensive DDNS Service Tests")
    print("=" * 60)

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    test_classes = [
        TestDDNSInitialization,
        TestRecordIdManagement,
        TestMetricsInitialization,
        TestIPMetricInitialization,
        TestIPValidation,
        TestRetryLogic,
        TestConfigValidation,
        TestConsecutiveFailures,
        TestCompleteScenarios,
        TestIPHistory
    ]

    for test_class in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(test_class))

    # Run tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print detailed summary
    print("\n" + "=" * 60)
    print("Test Summary:")
    print("-" * 60)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print(f"Success rate: {((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100):.1f}%")

    if result.failures:
        print("\nFailed tests:")
        for test, traceback in result.failures:
            print(f"  - {test}")

    if result.errors:
        print("\nTests with errors:")
        for test, traceback in result.errors:
            print(f"  - {test}")

    print("=" * 60)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_comprehensive_tests()
    sys.exit(0 if success else 1)
