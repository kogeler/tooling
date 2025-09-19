#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import time
import logging
import requests
from urllib.parse import urlparse
from typing import Optional, Dict, Any, Tuple

from prometheus_client import start_http_server, Counter, Gauge

check_ip_services = [
    "https://checkip.amazonaws.com",
    "https://api.ipify.org/?format=text"
]

# Prometheus metrics map
prometheus_metrics = {
    "ip_update_counter": Counter(
        "cf_ddns_ip_updates_total",
        "Number of times the IP address was updated."
    ),
    "ip_info_gauge": Gauge(
        "cf_ddns_ip_info",
        "Indicator for IP addresses used per host (1 for current IP, 0 for old IPs).",
        ["cf_host", "ip"]
    ),
    "ip_retrieval_error_counter": Counter(
        "cf_ddns_ip_retrieval_errors_total",
        "Number of times external IP retrieval from specific service failed.",
        ["check_ip_service_host"]
    ),
    "cf_api_error_counter": Counter(
        "cf_ddns_cloudflare_api_errors_total",
        "Number of Cloudflare API errors."
    ),
    "last_ip_check_timestamp": Gauge(
        "cf_ddns_last_ip_check_timestamp_seconds",
        "Unix timestamp of the last IP check."
    ),
    "last_ip_update_timestamp": Gauge(
        "cf_ddns_last_ip_update_timestamp_seconds",
        "Unix timestamp of the last successful IP update."
    )
}


def configure_logging():
    """
    Configure logging level based on CF_DDNS_LOGLEVEL environment variable.
    Defaults to INFO if not set or invalid.
    """
    log_level_str = os.environ.get("CF_DDNS_LOGLEVEL", "INFO").upper()
    valid_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    log_level = valid_levels.get(log_level_str, logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    logging.info(f"Log level set to {log_level_str}.")


def parse_env() -> Dict[str, Any]:
    """
    Read and validate necessary environment variables.
    Returns a dictionary of configuration parameters or terminates the script if invalid.
    """
    token = os.environ.get("CF_DDNS_TOKEN")
    zone_id = os.environ.get("CF_DDNS_ZONE_ID")
    host = os.environ.get("CF_DDNS_HOST")

    if not all([token, zone_id, host]):
        logging.error(
            "Environment variables CF_DDNS_TOKEN, CF_DDNS_ZONE_ID, and CF_DDNS_HOST must be set."
        )
        sys.exit(1)

    interval_str = os.environ.get("CF_DDNS_INTERVAL", "10")
    ttl_str = os.environ.get("CF_DDNS_TTL", "120")
    proxied_str = os.environ.get("CF_DDNS_PROXIED", "False")

    try:
        interval = int(interval_str)
        if interval < 1:
            raise ValueError("Interval must be at least 1 second")
    except ValueError as e:
        logging.error(f"Invalid value for CF_DDNS_INTERVAL: {interval_str}. {e}")
        sys.exit(1)

    try:
        ttl = int(ttl_str)
        if ttl < 60:
            logging.warning(f"TTL value {ttl} is very low. Cloudflare may override it.")
    except ValueError:
        logging.error(f"Invalid value for CF_DDNS_TTL: {ttl_str}.")
        sys.exit(1)

    proxied = proxied_str.lower() == "true"

    # Prometheus metrics endpoint port
    metrics_port_str = os.environ.get("CF_DDNS_METRICS_PORT", "9101")
    try:
        metrics_port = int(metrics_port_str)
        if not (1 <= metrics_port <= 65535):
            raise ValueError("Port must be between 1 and 65535")
    except ValueError as e:
        logging.error(f"Invalid value for CF_DDNS_METRICS_PORT: {metrics_port_str}. {e}")
        sys.exit(1)

    return {
        "token": token,
        "zone_id": zone_id,
        "host": host,
        "interval": interval,
        "ttl": ttl,
        "proxied": proxied,
        "metrics_port": metrics_port
    }


def validate_ipv4(ip: str) -> bool:
    """
    Validate IPv4 address format.
    Returns True if valid, False otherwise.
    """
    try:
        parts = ip.split('.')
        if len(parts) != 4:
            return False
        for part in parts:
            num = int(part)
            if not (0 <= num <= 255):
                return False
        return True
    except (ValueError, AttributeError):
        return False


def get_external_ip() -> Optional[str]:
    """
    Attempt to retrieve external IP address from check services.
    Returns the IP address as a string, or None if all attempts fail.
    """
    for service in check_ip_services:
        try:
            response = requests.get(service, timeout=10)
            response.raise_for_status()
            ip = response.text.strip()

            # Validate IP format
            if validate_ipv4(ip):
                return ip
            else:
                logging.error(f"Invalid IP format received from {service}: {ip}")
                prometheus_metrics["ip_retrieval_error_counter"].labels(
                    check_ip_service_host=urlparse(service).hostname).inc()

        except requests.exceptions.RequestException as e:
            logging.error(f"Error retrieving external IP from {service}: {e}")
            prometheus_metrics["ip_retrieval_error_counter"].labels(
                check_ip_service_host=urlparse(service).hostname).inc()

    return None


def get_dns_record(token: str, zone_id: str, host: str, max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """
    Retrieve DNS A record information from Cloudflare with retry logic.
    Returns a dictionary with record information or None if not found or on error.
    """
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A&name={host}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                logging.error(f"Cloudflare API returned an error: {data}")
                prometheus_metrics["cf_api_error_counter"].inc()
                return None

            results = data.get("result", [])
            if not results:
                logging.warning(f"No DNS A record found for host '{host}'.")
                return None

            record = results[0]
            return {
                "id": record.get("id"),
                "content": record.get("content"),  # Current IP in DNS
                "ttl": record.get("ttl"),
                "proxied": record.get("proxied")
            }

        except requests.exceptions.RequestException as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries}: Error retrieving DNS record: {e}")
            prometheus_metrics["cf_api_error_counter"].inc()
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff

    return None


def create_dns_record(token: str, zone_id: str, host: str, ip: str,
                     ttl: int, proxied: bool, max_retries: int = 3) -> Optional[str]:
    """
    Create a new A record in Cloudflare with retry logic.
    Returns the record ID on success or None on failure.
    """
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = {
        "type": "A",
        "name": host,
        "content": ip,
        "ttl": ttl,
        "proxied": proxied
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                record_id = result.get("result", {}).get("id")
                logging.info(f"DNS A record for host {host} created with IP {ip}.")
                return record_id
            else:
                logging.error(f"Failed to create DNS record. Cloudflare API error: {result}")
                prometheus_metrics["cf_api_error_counter"].inc()

        except requests.exceptions.RequestException as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries}: Error creating DNS record: {e}")
            prometheus_metrics["cf_api_error_counter"].inc()

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # Exponential backoff

    return None


def update_cloudflare_record(token: str, zone_id: str, record_id: str, host: str,
                           new_ip: str, ttl: int, proxied: bool, max_retries: int = 3) -> bool:
    """
    Update an A record in Cloudflare via API with retry logic.
    Returns True on success and False otherwise.
    """
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = {
        "type": "A",
        "name": host,
        "content": new_ip,
        "ttl": ttl,
        "proxied": proxied
    }

    for attempt in range(max_retries):
        try:
            response = requests.put(url, json=data, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                logging.info(f"DNS record for host {host} successfully updated to IP {new_ip}.")
                return True
            else:
                logging.error(f"Cloudflare API returned an error: {result}")
                prometheus_metrics["cf_api_error_counter"].inc()

                # Check if error indicates record doesn't exist
                errors = result.get("errors", [])
                for error in errors:
                    if error.get("code") == 81058:  # Record not found
                        logging.warning("Record ID is invalid. Will try to recreate.")
                        return False

        except requests.exceptions.RequestException as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries}: Error updating DNS record: {e}")
            prometheus_metrics["cf_api_error_counter"].inc()

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # Exponential backoff

    return False


def initialize_metrics(config: Dict[str, Any]):
    """
    Initialize all Prometheus metrics with zero values.
    """
    # Initialize error counters for each IP check service
    for service in check_ip_services:
        hostname = urlparse(service).hostname
        if hostname:
            # Create metric with initial value of 0
            prometheus_metrics["ip_retrieval_error_counter"].labels(
                check_ip_service_host=hostname
            )._value.set(0)

    # Initialize other counters with 0
    prometheus_metrics["ip_update_counter"]._value.set(0)
    prometheus_metrics["cf_api_error_counter"]._value.set(0)

    # Initialize timestamps with 0 (never checked/updated)
    prometheus_metrics["last_ip_check_timestamp"].set(0)
    prometheus_metrics["last_ip_update_timestamp"].set(0)

    logging.debug("All metrics initialized with zero values.")


def handle_dns_update(config: Dict[str, Any], record_id: Optional[str],
                     current_ip: str) -> Tuple[bool, Optional[str]]:
    """
    Handle DNS update logic including record recreation if needed.
    Returns (success, new_record_id)
    """
    success = False
    new_record_id = record_id

    if record_id:
        # Try to update existing record
        success = update_cloudflare_record(
            config["token"],
            config["zone_id"],
            record_id,
            config["host"],
            current_ip,
            config["ttl"],
            config["proxied"]
        )

        # If update failed (possibly due to invalid record ID), try to get new one
        if not success:
            logging.info("Update failed. Attempting to retrieve current record ID.")
            record_info = get_dns_record(config["token"], config["zone_id"], config["host"])
            if record_info:
                new_record_id = record_info.get("id")
                if new_record_id:
                    success = update_cloudflare_record(
                        config["token"],
                        config["zone_id"],
                        new_record_id,
                        config["host"],
                        current_ip,
                        config["ttl"],
                        config["proxied"]
                    )
            else:
                # Record doesn't exist anymore
                new_record_id = None

    # If no record exists or update failed, create new one
    if not success and not new_record_id:
        logging.info("Creating new DNS record.")
        new_record_id = create_dns_record(
            config["token"],
            config["zone_id"],
            config["host"],
            current_ip,
            config["ttl"],
            config["proxied"]
        )
        success = (new_record_id is not None)

    return success, new_record_id


def main():
    configure_logging()
    config = parse_env()

    # Initialize Prometheus metrics with zero values
    initialize_metrics(config)

    # Start Prometheus metrics server
    start_http_server(config["metrics_port"])
    logging.info(f"Prometheus metrics server started on port {config['metrics_port']}")

    # Get initial DNS record information to avoid unnecessary updates
    record_info = get_dns_record(config["token"], config["zone_id"], config["host"])

    if record_info:
        last_ip = record_info.get("content")
        record_id = record_info.get("id")
        logging.info(f"Current DNS record found. Host: {config['host']}, IP: {last_ip}")
    else:
        logging.warning(f"No existing DNS record found for host '{config['host']}'. Will create one on first IP retrieval.")
        last_ip = None
        record_id = None

    # Get current external IP and initialize metric
    logging.info("Getting initial external IP...")
    initial_ip = get_external_ip()
    if initial_ip:
        logging.info(f"Current external IP: {initial_ip}")
        # Set current external IP metric to 1
        prometheus_metrics["ip_info_gauge"].labels(cf_host=config["host"], ip=initial_ip).set(1)

        # If DNS IP exists and differs from current IP, set it to 0
        if last_ip and last_ip != initial_ip:
            prometheus_metrics["ip_info_gauge"].labels(cf_host=config["host"], ip=last_ip).set(0)
            logging.info(f"DNS IP {last_ip} differs from current IP {initial_ip}")
    else:
        logging.warning("Could not retrieve initial external IP")

    consecutive_failures = 0
    max_consecutive_failures = 10

    try:
        while True:
            try:
                # Update check timestamp
                prometheus_metrics["last_ip_check_timestamp"].set(time.time())

                current_ip = get_external_ip()

                if current_ip is None:
                    consecutive_failures += 1
                    logging.error(f"Could not retrieve external IP. Failure {consecutive_failures}/{max_consecutive_failures}")

                    if consecutive_failures >= max_consecutive_failures:
                        logging.critical(f"Failed to retrieve IP {max_consecutive_failures} times. Exiting.")
                        sys.exit(1)
                else:
                    consecutive_failures = 0  # Reset counter on success

                    update_needed = (last_ip is None) or (current_ip != last_ip)

                    if update_needed:
                        if last_ip is None:
                            logging.info(f"First IP retrieval. IP: {current_ip}")
                        else:
                            logging.info(f"IP changed from {last_ip} to {current_ip}. Updating DNS record.")

                        # Handle DNS update with possible record recreation
                        success, record_id = handle_dns_update(config, record_id, current_ip)

                        if success:
                            # Update metrics
                            prometheus_metrics["ip_update_counter"].inc()
                            prometheus_metrics["last_ip_update_timestamp"].set(time.time())

                            # Update IP gauge - keep all IPs in history
                            if last_ip is not None:
                                prometheus_metrics["ip_info_gauge"].labels(cf_host=config["host"], ip=last_ip).set(0)

                            prometheus_metrics["ip_info_gauge"].labels(cf_host=config["host"], ip=current_ip).set(1)

                            last_ip = current_ip
                        else:
                            logging.error("Failed to update DNS record after all attempts.")
                    else:
                        logging.debug(f"External IP remains {current_ip}. No update required.")

            except Exception as loop_error:
                logging.exception(f"An error occurred in the main loop: {loop_error}")
                consecutive_failures += 1

                if consecutive_failures >= max_consecutive_failures:
                    logging.critical(f"Too many consecutive failures. Exiting.")
                    sys.exit(1)

            logging.debug(f"Waiting {config['interval']} seconds before the next check...")
            time.sleep(config["interval"])

    except KeyboardInterrupt:
        logging.info("Caught KeyboardInterrupt. Exiting gracefully.")
    except Exception as e:
        logging.exception(f"Unhandled exception in main: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
