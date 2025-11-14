"""Validation and environment parsing tests for one_t_exporter."""

import os
import unittest

from tests.common import (
    MAX_ADDRESS_LENGTH,
    METRICS,
    MIN_ADDRESS_LENGTH,
    SUPPORTED_NETWORKS,
    clear_validator_env,
    load_validators_from_env,
    reset_metrics,
    validate_address,
    validate_network,
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
        clear_validator_env()
        reset_metrics()

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
