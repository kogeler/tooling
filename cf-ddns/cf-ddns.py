#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import time
import logging
import requests

from prometheus_client import start_http_server, Counter, Gauge

# Prometheus metrics map
prometheus_metrics = {
    "ip_update_counter": Counter(
        "cf_ddns_ip_updates_total",
        "Number of times the IP address was updated."
    ),
    "ip_info_gauge": Gauge(
        "cf_ddns_ip_info",
        "Indicator for IP addresses used per domain (1 for current IP, 0 for old IPs).",
        ["domain", "ip"]
    ),
    "ip_retrieval_error_counter": Counter(
        "cf_ddns_ip_retrieval_errors_total",
        "Number of times external IP retrieval from specific service failed.",
        ["service_domain"]
    )
}

def configure_logging():
    """
    Configures the logging level based on the CF_DDNS_LOGLEVEL environment variable.
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

def parse_env():
    """
    Reads and validates the necessary environment variables.
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
    except ValueError:
        logging.error(f"Invalid value for CF_DDNS_INTERVAL: {interval_str}.")
        sys.exit(1)

    try:
        ttl = int(ttl_str)
    except ValueError:
        logging.error(f"Invalid value for CF_DDNS_TTL: {ttl_str}.")
        sys.exit(1)

    proxied = proxied_str.lower() == "true"

    # Prometheus metrics endpoint port
    metrics_port_str = os.environ.get("CF_DDNS_METRICS_PORT", "9101")
    try:
        metrics_port = int(metrics_port_str)
    except ValueError:
        logging.error(f"Invalid value for CF_DDNS_METRICS_PORT: {metrics_port_str}.")
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

def get_external_ip():
    """
    Attempts to retrieve the external IP address from two different services.
    Returns the IP address as a string, or None if both attempts fail.
    """
    # First service
    domain = "checkip.amazonaws.com"
    try:
        response = requests.get(f"https://{domain}/", timeout=10)
        response.raise_for_status()
        return response.text.strip()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving external IP from {domain}: {e}")
        prometheus_metrics["ip_retrieval_error_counter"].labels(service_domain=domain).inc()

    # Second service
    domain = "api.ipify.org"
    try:
        response = requests.get(f"https://{domain}?format=text", timeout=10)
        response.raise_for_status()
        return response.text.strip()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving external IP from {domain}: {e}")
        prometheus_metrics["ip_retrieval_error_counter"].labels(service_domain=domain).inc()

    return None

def get_record_id(token, zone_id, host):
    """
    Retrieves the record ID for a specific A-type DNS record from Cloudflare.
    Returns the record ID as a string or None if not found or on error.
    """
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A&name={host}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            logging.error(f"Cloudflare API returned an error: {data}")
            return None

        results = data.get("result", [])
        if not results:
            logging.error(f"No DNS record found for host '{host}' in Cloudflare response.")
            return None

        record_id = results[0].get("id")
        return record_id
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving Cloudflare DNS record ID: {e}")
        return None

def update_cloudflare_record(token, zone_id, record_id, host, new_ip, ttl, proxied):
    """
    Updates an A record in Cloudflare via the API.
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

    try:
        response = requests.put(url, json=data, headers=headers, timeout=10)
        response.raise_for_status()
        result = response.json()

        if result.get("success"):
            logging.info(f"DNS record for host {host} successfully updated to IP {new_ip}.")
            return True
        else:
            logging.error(f"Cloudflare API returned an error: {result}")
            return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error contacting the Cloudflare API: {e}")
        return False

def main():
    configure_logging()
    config = parse_env()

    # Start Prometheus metrics server
    start_http_server(config["metrics_port"])
    logging.info(f"Prometheus metrics server started on port {config['metrics_port']}")

    record_id = get_record_id(config["token"], config["zone_id"], config["host"])
    if not record_id:
        logging.error(f"Failed to retrieve record ID for host '{config['host']}'. Exiting.")
        sys.exit(1)

    last_ip = None

    try:
        while True:
            try:
                current_ip = get_external_ip()
                if current_ip is None:
                    logging.error("Could not retrieve external IP. Will try again later.")
                else:
                    update_needed = (last_ip is None) or (current_ip != last_ip)
                    if update_needed:
                        if last_ip is None:
                            logging.info("Performing the first DNS record update.")
                        else:
                            logging.info(f"IP changed from {last_ip} to {current_ip}. Updating DNS record.")

                        success = update_cloudflare_record(
                            config["token"],
                            config["zone_id"],
                            record_id,
                            config["host"],
                            current_ip,
                            config["ttl"],
                            config["proxied"]
                        )
                        if success:
                            # Increment counter for IP updates
                            prometheus_metrics["ip_update_counter"].inc()

                            # Invalidate old IP gauge if we had one
                            if last_ip is not None:
                                prometheus_metrics["ip_info_gauge"].labels(domain=config["host"], ip=last_ip).set(0)

                            # Set new IP gauge
                            prometheus_metrics["ip_info_gauge"].labels(domain=config["host"], ip=current_ip).set(1)

                            last_ip = current_ip
                    else:
                        logging.debug("External IP remains the same. No update required.")
            except Exception as loop_error:
                logging.exception(f"An error occurred in the main loop: {loop_error}")

            logging.debug(f"Waiting {config['interval']} seconds before the next check...")
            time.sleep(config["interval"])
    except KeyboardInterrupt:
        logging.info("Caught KeyboardInterrupt. Exiting gracefully.")
    except Exception as e:
        logging.exception(f"Unhandled exception in main: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
