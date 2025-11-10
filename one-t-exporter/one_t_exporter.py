#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Prometheus exporter for TurboFlakes ONE-T validator performance metrics.
Exports metrics for validators configured via environment variables.
"""

import os
import sys
import time
import signal
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import List, Tuple, Dict, Any
from prometheus_client import start_http_server, Gauge, Counter, REGISTRY
import one_t_parser as one_t_lib

# Environment variable configuration
ONE_T_PORT = int(os.getenv("ONE_T_PORT", "8000"))
ONE_T_COLLECT_PERIOD = int(os.getenv("ONE_T_COLLECT_PERIOD", "60"))
ONE_T_LOG_LEVEL = os.getenv("ONE_T_LOG_LEVEL", "INFO").upper()
ONE_T_ENV = os.getenv("ONE_T_ENV", "")

# Configure logging
logging.basicConfig(
    level=getattr(logging, ONE_T_LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Set external modules to WARNING level to avoid excessive debug logs
if ONE_T_LOG_LEVEL == "DEBUG":
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

# Supported networks for validation
SUPPORTED_NETWORKS = {"polkadot", "kusama", "westend", "paseo"}

# Validator address length validation (typical Substrate SS58 addresses)
MIN_ADDRESS_LENGTH = 32
MAX_ADDRESS_LENGTH = 48

# Health check status tracking
HEALTH_STATUS = {
    "healthy": False,  # Becomes True after first successful collection
    "last_error": None,
    "last_success_time": None,
    "total_validators": 0,
    "successful_validators": 0,
}

# Shutdown event for graceful termination
shutdown_event = threading.Event()
health_server = None

# Prometheus metrics consolidated into a dictionary
# Note: Using Gauge for all session metrics since they represent absolute values
# that can go up or down between sessions
METRICS = {
    # Performance metrics (all Gauges)
    "one_t_grade_numeric": Gauge(
        "one_t_grade_numeric",
        "ONE-T numeric grade value (higher is better)",
        ["network", "address", "identity", "env"],
    ),
    "one_t_performance_score": Gauge(
        "one_t_performance_score",
        "ONE-T performance score (0.0-1.0)",
        ["network", "address", "identity", "env"],
    ),
    "one_t_mvr": Gauge(
        "one_t_mvr",
        "Missed Vote Ratio (MVR)",
        ["network", "address", "identity", "env"],
    ),
    "one_t_bar": Gauge(
        "one_t_bar",
        "Bitfields Availability Ratio (BAR)",
        ["network", "address", "identity", "env"],
    ),
    "one_t_points_normalized": Gauge(
        "one_t_points_normalized",
        "Normalized points component",
        ["network", "address", "identity", "env"],
    ),
    "one_t_pv_sessions_ratio": Gauge(
        "one_t_pv_sessions_ratio",
        "Para-validator sessions ratio",
        ["network", "address", "identity", "env"],
    ),
    # Voting metrics (Gauges for absolute values from current session)
    "one_t_missed_votes": Gauge(
        "one_t_missed_votes",
        "Total missed votes in current session",
        ["network", "address", "identity", "env"],
    ),
    "one_t_bitfields_unavailability": Gauge(
        "one_t_bitfields_unavailability",
        "Total bitfields unavailability in current session",
        ["network", "address", "identity", "env"],
    ),
    "one_t_explicit_votes": Gauge(
        "one_t_explicit_votes",
        "Total explicit votes in current session",
        ["network", "address", "identity", "env"],
    ),
    "one_t_implicit_votes": Gauge(
        "one_t_implicit_votes",
        "Total implicit votes in current session",
        ["network", "address", "identity", "env"],
    ),
    "one_t_bitfields_availability": Gauge(
        "one_t_bitfields_availability",
        "Total bitfields availability in current session",
        ["network", "address", "identity", "env"],
    ),
    # Session metrics (Gauges for absolute values from current session)
    "one_t_points": Gauge(
        "one_t_points",
        "Total session points in current session",
        ["network", "address", "identity", "env"],
    ),
    "one_t_authored_blocks_count": Gauge(
        "one_t_authored_blocks_count",
        "Total authored blocks in current session",
        ["network", "address", "identity", "env"],
    ),
    "one_t_para_points": Gauge(
        "one_t_para_points",
        "Total para points in current session",
        ["network", "address", "identity", "env"],
    ),
    # System metrics (Counter for cumulative errors)
    "one_t_errors": Counter(
        "one_t_errors", "Total errors encountered during metric collection"
    ),
}


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP handler for health check endpoint."""

    def log_message(self, format, *args):
        """Override to suppress request logging."""
        pass

    def do_GET(self):
        """Handle GET requests for health check."""
        if self.path == "/health":
            if HEALTH_STATUS["healthy"]:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                message = f"OK - {HEALTH_STATUS['successful_validators']}/{HEALTH_STATUS['total_validators']} validators healthy"
                self.wfile.write(message.encode())
            else:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                error_msg = (
                    HEALTH_STATUS["last_error"] or "No successful collection yet"
                )
                message = f"UNHEALTHY - {error_msg}"
                self.wfile.write(message.encode())
        else:
            self.send_response(404)
            self.end_headers()


def start_health_server(port: int):
    """Start the health check HTTP server in a separate thread."""
    global health_server
    try:
        health_server = HTTPServer(("", port), HealthCheckHandler)
        thread = threading.Thread(target=health_server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"Health check server started on port {port}")
        return health_server
    except Exception as e:
        logger.error(f"Failed to start health check server on port {port}: {e}")
        return None


def validate_network(network: str) -> bool:
    """Validate if network is supported."""
    return network.lower() in SUPPORTED_NETWORKS


def validate_address(address: str) -> bool:
    """Validate address format."""
    return MIN_ADDRESS_LENGTH <= len(address) <= MAX_ADDRESS_LENGTH


def load_validators_from_env() -> List[Tuple[str, str]]:
    """
    Load validators from environment variables.
    Format: ONE_T_VAL_1, ONE_T_VAL_NETWORK_1, ONE_T_VAL_2, ONE_T_VAL_NETWORK_2, etc.
    Stops when an index is not found.
    """
    validators = []
    index = 1

    while True:
        address_key = f"ONE_T_VAL_{index}"
        network_key = f"ONE_T_VAL_NETWORK_{index}"

        address = os.getenv(address_key)
        network = os.getenv(network_key)

        # Stop if either variable is missing
        if address is None or network is None:
            logger.debug(
                f"Stopping validator loading at index {index}: address={address}, network={network}"
            )
            break

        # Validate network and address
        if not validate_network(network):
            logger.error(
                f"Invalid network '{network}' for validator {index}. Supported networks: {SUPPORTED_NETWORKS}"
            )
            METRICS["one_t_errors"].inc()
            index += 1
            continue

        if not validate_address(address):
            logger.error(
                f"Invalid address length for validator {index}: '{address}' (length: {len(address)})"
            )
            METRICS["one_t_errors"].inc()
            index += 1
            continue

        validators.append((network, address))
        logger.info(
            f"Loaded validator {index}: network={network}, address={address[:8]}...{address[-8:]}"
        )
        index += 1

    if not validators:
        logger.warning("No validators configured via environment variables")
    else:
        logger.debug(f"Loaded {len(validators)} validators from environment")

    return validators


def safe_get_value(data: Dict[str, Any], path: str, default: Any = 0) -> Any:
    """
    Safely get a value from nested dictionary using dot notation.
    Example: safe_get_value(data, 'components.mvr', 0.0)
    """
    try:
        keys = path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key, {})
            else:
                return default
        return value if value != {} else default
    except Exception as e:
        logger.debug(f"Error getting value for path '{path}': {e}")
        return default


def update_metrics():
    """Update all Prometheus metrics with current validator data."""
    global HEALTH_STATUS

    validators = load_validators_from_env()

    if not validators:
        logger.warning("No validators to monitor")
        HEALTH_STATUS["last_error"] = "No validators configured"
        HEALTH_STATUS["healthy"] = False
        return

    logger.info(f"Collecting metrics for {len(validators)} validators")

    try:
        # Use batch processing from the library
        logger.debug(
            f"Calling one_t_lib.compute_current_session_results_batch with {len(validators)} validators"
        )
        results = one_t_lib.compute_current_session_results_batch(validators)
        logger.debug(f"Received {len(results)} results from batch processing")

        successful_count = 0
        failed_count = 0
        HEALTH_STATUS["total_validators"] = len(validators)

        logger.debug(
            f"Successfully processed {successful_count} validators, {failed_count} failed"
        )

        # Clear all metrics before setting new ones to ensure only active validators are shown
        logger.debug("Clearing all metrics to ensure only active validators are shown")
        for metric_name, metric in METRICS.items():
            if hasattr(metric, "_metrics"):
                metric._metrics.clear()

        # Now set metrics for active validators
        for i, result in enumerate(results):
            try:
                logger.debug(
                    f"Processing result {i + 1}/{len(results)}: {result.get('network')}/{result.get('address')}"
                )

                if not result.get("ok", False):
                    logger.error(
                        f"Failed to get metrics for {result.get('network')}/{result.get('address')}: {result.get('error')}"
                    )
                    METRICS["one_t_errors"].inc()
                    failed_count += 1
                    HEALTH_STATUS["last_error"] = (
                        f"API error: {result.get('error', 'Unknown')}"
                    )
                    continue

                # Check if validator is active in current session
                if not result.get("active", False):
                    logger.info(
                        f"Validator {result.get('network')}/{result.get('address')} is not active in current session - skipping metrics"
                    )
                    continue

                network = result.get("network", "")
                address = result.get("address", "")
                identity = result.get("identity", "")

                if not network or not address:
                    logger.error(f"Missing network or address in result: {result}")
                    METRICS["one_t_errors"].inc()
                    failed_count += 1
                    HEALTH_STATUS["last_error"] = "Invalid result data"
                    continue

                # Extract labels for metrics
                labels = {
                    "network": network,
                    "address": address,
                    "identity": identity,
                    "env": ONE_T_ENV,
                }
                logger.debug(f"Setting metrics with labels: {labels}")

                try:
                    # Set gauge metrics for performance scores
                    METRICS["one_t_grade_numeric"].labels(**labels).set(
                        safe_get_value(result, "grade_numeric", -1.0)
                    )
                    METRICS["one_t_performance_score"].labels(**labels).set(
                        safe_get_value(result, "performance_score", 0.0)
                    )
                    METRICS["one_t_mvr"].labels(**labels).set(
                        safe_get_value(result, "components.mvr", 0.0)
                    )
                    METRICS["one_t_bar"].labels(**labels).set(
                        safe_get_value(result, "components.bar", 0.0)
                    )
                    METRICS["one_t_points_normalized"].labels(**labels).set(
                        safe_get_value(result, "components.points_normalized", 0.0)
                    )
                    METRICS["one_t_pv_sessions_ratio"].labels(**labels).set(
                        safe_get_value(result, "components.pv_sessions_ratio", 0.0)
                    )

                    logger.debug(
                        f"Gauge metrics set for {network}/{address[:8]}...{address[-8:]}: "
                        f"grade_numeric={safe_get_value(result, 'grade_numeric')}, "
                        f"performance_score={safe_get_value(result, 'performance_score')}, "
                        f"mvr={safe_get_value(result, 'components.mvr')}, "
                        f"bar={safe_get_value(result, 'components.bar')}, "
                        f"points_normalized={safe_get_value(result, 'components.points_normalized')}, "
                        f"pv_sessions_ratio={safe_get_value(result, 'components.pv_sessions_ratio')}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error setting performance metrics for {network}/{address}: {e}"
                    )
                    METRICS["one_t_errors"].inc()

                try:
                    # Set voting metrics (using .set() for absolute values)
                    key_metrics = result.get("key_metrics", {})
                    METRICS["one_t_missed_votes"].labels(**labels).set(
                        safe_get_value(key_metrics, "missed_votes_total", 0)
                    )
                    METRICS["one_t_bitfields_unavailability"].labels(**labels).set(
                        safe_get_value(key_metrics, "bitfields_unavailability_total", 0)
                    )
                    METRICS["one_t_explicit_votes"].labels(**labels).set(
                        safe_get_value(key_metrics, "explicit_votes", 0)
                    )
                    METRICS["one_t_implicit_votes"].labels(**labels).set(
                        safe_get_value(key_metrics, "implicit_votes", 0)
                    )
                    METRICS["one_t_bitfields_availability"].labels(**labels).set(
                        safe_get_value(key_metrics, "bitfields_availability_total", 0)
                    )

                    logger.debug(
                        f"Voting metrics set for {network}/{address[:8]}...{address[-8:]}: "
                        f"missed_votes={safe_get_value(key_metrics, 'missed_votes_total')}, "
                        f"bitfields_unavailability={safe_get_value(key_metrics, 'bitfields_unavailability_total')}, "
                        f"explicit_votes={safe_get_value(key_metrics, 'explicit_votes')}, "
                        f"implicit_votes={safe_get_value(key_metrics, 'implicit_votes')}, "
                        f"bitfields_availability={safe_get_value(key_metrics, 'bitfields_availability_total')}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error setting voting metrics for {network}/{address}: {e}"
                    )
                    METRICS["one_t_errors"].inc()

                try:
                    # Set session details (using .set() for absolute values)
                    session_details = result.get("current_session_details", {})
                    METRICS["one_t_points"].labels(**labels).set(
                        safe_get_value(session_details, "points", 0)
                    )
                    METRICS["one_t_authored_blocks_count"].labels(**labels).set(
                        safe_get_value(session_details, "authored_blocks_count", 0)
                    )
                    METRICS["one_t_para_points"].labels(**labels).set(
                        safe_get_value(session_details, "para_points", 0)
                    )

                    logger.debug(
                        f"Session details set for {network}/{address[:8]}...{address[-8:]}: "
                        f"points={safe_get_value(session_details, 'points')}, "
                        f"authored_blocks_count={safe_get_value(session_details, 'authored_blocks_count')}, "
                        f"para_points={safe_get_value(session_details, 'para_points')}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error setting session metrics for {network}/{address}: {e}"
                    )
                    METRICS["one_t_errors"].inc()

                logger.info(
                    f"Updated metrics for {network}/{address[:8]}...{address[-8:]}"
                )
                successful_count += 1

            except Exception as e:
                logger.error(f"Error processing result {i + 1}: {e}")
                METRICS["one_t_errors"].inc()
                failed_count += 1
                HEALTH_STATUS["last_error"] = str(e)

        # Update health status
        HEALTH_STATUS["successful_validators"] = successful_count
        if successful_count > 0 and failed_count == 0:
            HEALTH_STATUS["healthy"] = True
            HEALTH_STATUS["last_error"] = None
            HEALTH_STATUS["last_success_time"] = time.time()
        elif failed_count > 0:
            HEALTH_STATUS["healthy"] = False

    except Exception as e:
        logger.error(f"Error during metric collection: {e}")
        METRICS["one_t_errors"].inc()
        HEALTH_STATUS["healthy"] = False
        HEALTH_STATUS["last_error"] = str(e)


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    signal_name = signal.Signals(signum).name
    logger.info(f"Received {signal_name} signal, initiating graceful shutdown...")
    shutdown_event.set()

    # Stop health check server if running
    global health_server
    if health_server:
        try:
            health_server.shutdown()
            logger.info("Health check server stopped")
        except Exception as e:
            logger.error(f"Error stopping health check server: {e}")


def main():
    """Main function to start the Prometheus exporter."""
    logger.info(f"Starting ONE-T Prometheus exporter on port {ONE_T_PORT}")
    logger.info(f"Collection period: {ONE_T_COLLECT_PERIOD} seconds")
    logger.info(f"Log level: {ONE_T_LOG_LEVEL}")

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)  # Handle Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Handle termination signal (k8s)
    logger.info("Signal handlers registered for graceful shutdown")

    # Start HTTP server for Prometheus metrics
    try:
        start_http_server(ONE_T_PORT)
        logger.info(f"Metrics server started on port {ONE_T_PORT}")
    except Exception as e:
        logger.error(f"Failed to start metrics server on port {ONE_T_PORT}: {e}")
        return

    # Start health check server on port + 1
    health_port = ONE_T_PORT + 1
    start_health_server(health_port)

    # Main collection loop
    while not shutdown_event.is_set():
        try:
            update_metrics()
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
            METRICS["one_t_errors"].inc()

        # Sleep with periodic checks for shutdown signal
        logger.debug(f"Sleeping for {ONE_T_COLLECT_PERIOD} seconds")
        for _ in range(ONE_T_COLLECT_PERIOD):
            if shutdown_event.is_set():
                break
            time.sleep(1)

    # Graceful shutdown
    logger.info("Shutting down ONE-T Prometheus exporter...")
    logger.info(
        f"Final metrics: {HEALTH_STATUS['successful_validators']}/{HEALTH_STATUS['total_validators']} validators were healthy"
    )
    logger.info("ONE-T Prometheus exporter stopped gracefully")
    sys.exit(0)


if __name__ == "__main__":
    main()
