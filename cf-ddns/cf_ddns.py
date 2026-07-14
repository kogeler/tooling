#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import time
import random
import logging
import requests
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from urllib.parse import urlparse
from typing import Optional, Dict, Any, NamedTuple, Tuple

from prometheus_client import start_http_server, Counter, Gauge

check_ip_services = [
    "https://checkip.amazonaws.com",
    "https://api.ipify.org/?format=text"
]

CF_API_BASE = "https://api.cloudflare.com/client/v4"

# (connect, read) timeouts; the read timeout stays well below a container's
# default 10s stop grace period so shutdown is bounded by one in-flight read.
DEFAULT_TIMEOUT = (3.05, 5)
DEFAULT_MAX_RETRIES = 3

# A 429 Retry-After longer than this ends the call as TRANSIENT instead of
# stalling the iteration; the loop cadence and failure counters take over.
RETRY_AFTER_CAP_SECONDS = 60

# A check-IP response should contain a single IPv4 address; anything larger
# is a provider failure and must not be buffered.
CHECK_IP_MAX_BODY_BYTES = 64

# Cloudflare API error codes with special meaning for this utility.
CF_CODE_RECORD_NOT_FOUND = 81044         # "Record does not exist"
CF_CODES_RECORD_EXISTS = {81057, 81058}  # "record (with those settings) exists"


class Outcome(str, Enum):
    """Classified result of a Cloudflare API call (frozen contract)."""

    OK = "ok"                # request succeeded
    ABSENT = "absent"        # confirmed: no record (HTTP 200, empty result list)
    GONE = "gone"            # record id invalid (HTTP 404 and/or CF code 81044)
    EXISTS = "exists"        # create refused: record already exists (81057, 81058)
    AMBIGUOUS = "ambiguous"  # multiple matching A records; do not guess ownership
    TRANSIENT = "transient"  # network / 5xx / 429 — retries exhausted
    PERMANENT = "permanent"  # other 4xx (auth, validation) — not retryable


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


def create_http_clients(token: str) -> Tuple[requests.Session, requests.Session]:
    """
    Create the two persistent HTTP sessions: an authenticated Cloudflare
    session and a plain check-IP session.

    The bearer token lives only on the Cloudflare session; the check-IP
    session must never carry credentials.
    """
    cf_session = requests.Session()
    cf_session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    ip_session = requests.Session()
    return cf_session, ip_session


class _CfResult(NamedTuple):
    """Internal classified result of one Cloudflare API exchange."""

    kind: str                # "ok" | "api_error" | "http_error" | "transient"
    status_code: Optional[int]
    body: Optional[Dict[str, Any]]
    codes: Tuple[int, ...]   # Cloudflare error codes extracted from the body


def _wait(delay: float, stop_event=None) -> None:
    """Sleep; interruptible when a stop event is provided (wired in Stage 4)."""
    if stop_event is not None:
        stop_event.wait(timeout=delay)
    else:
        time.sleep(delay)


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff (1s, 2s, ...) with a small jitter."""
    return 2 ** (attempt - 1) + random.uniform(0, 0.5)


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


def _extract_error_codes(body: Any) -> Tuple[int, ...]:
    """Pull Cloudflare error codes out of a (possibly malformed) response body."""
    if not isinstance(body, dict):
        return ()
    codes = []
    for error in body.get("errors") or []:
        if isinstance(error, dict) and isinstance(error.get("code"), int):
            codes.append(error["code"])
    return tuple(codes)


def _cf_request(session: requests.Session, method: str, url: str, *,
                json_body: Optional[Dict[str, Any]] = None,
                params: Optional[Dict[str, str]] = None,
                timeout: Tuple[float, float] = DEFAULT_TIMEOUT,
                max_retries: int = DEFAULT_MAX_RETRIES,
                idempotent: bool = True,
                validate=None,
                stop_event=None) -> _CfResult:
    """
    One classified Cloudflare API exchange with bounded retries.

    Network errors, 5xx, in-budget 429s, and protocol failures (malformed
    "success" bodies) are retried with backoff — but only for idempotent
    calls. A non-idempotent POST gets a single attempt: any uncertain outcome
    is reported as transient so the caller re-reads state instead of blindly
    re-sending. Other 4xx and explicit API refusals are never retried.
    """
    attempts = max_retries if idempotent else 1
    last = _CfResult("transient", None, None, ())

    for attempt in range(1, attempts + 1):
        if stop_event is not None and stop_event.is_set():
            return _CfResult("transient", None, None, ())

        retry_delay = None
        try:
            response = session.request(
                method, url, json=json_body, params=params, timeout=timeout
            )
        except requests.exceptions.RequestException as e:
            logging.error(
                f"{method} {url}: network error (attempt {attempt}/{attempts}): {e}"
            )
            prometheus_metrics["cf_api_error_counter"].inc()
            last = _CfResult("transient", None, None, ())
            retry_delay = _backoff_delay(attempt)
        else:
            try:
                body = response.json()
            except ValueError:
                body = None
            codes = _extract_error_codes(body)
            status = response.status_code

            if 200 <= status < 300:
                if isinstance(body, dict) and body.get("success") is True:
                    problem = validate(body) if validate else None
                    if problem is None:
                        return _CfResult("ok", status, body, codes)
                    # success:true with a malformed result is a protocol
                    # failure, never OK — retryable for idempotent calls.
                    logging.error(f"{method} {url}: malformed API response: {problem}")
                    prometheus_metrics["cf_api_error_counter"].inc()
                    last = _CfResult("transient", status, body, codes)
                    retry_delay = _backoff_delay(attempt)
                elif isinstance(body, dict) and body.get("success") is False:
                    logging.error(
                        f"{method} {url}: Cloudflare API error, codes {list(codes)}: "
                        f"{body.get('errors')}"
                    )
                    prometheus_metrics["cf_api_error_counter"].inc()
                    return _CfResult("api_error", status, body, codes)
                else:
                    logging.error(
                        f"{method} {url}: malformed API response "
                        f"(HTTP {status}, non-conforming body)"
                    )
                    prometheus_metrics["cf_api_error_counter"].inc()
                    last = _CfResult("transient", status, body, codes)
                    retry_delay = _backoff_delay(attempt)
            elif status == 429:
                prometheus_metrics["cf_api_error_counter"].inc()
                advertised = _parse_retry_after(response.headers.get("Retry-After"))
                if advertised is not None and advertised > RETRY_AFTER_CAP_SECONDS:
                    logging.warning(
                        f"{method} {url}: rate limited, Retry-After "
                        f"{advertised:.0f}s exceeds the "
                        f"{RETRY_AFTER_CAP_SECONDS}s budget; giving up this call"
                    )
                    return _CfResult("transient", status, body, codes)
                logging.warning(f"{method} {url}: rate limited (429)")
                last = _CfResult("transient", status, body, codes)
                retry_delay = (advertised if advertised is not None
                               else _backoff_delay(attempt))
            elif status >= 500:
                logging.error(
                    f"{method} {url}: server error HTTP {status} "
                    f"(attempt {attempt}/{attempts})"
                )
                prometheus_metrics["cf_api_error_counter"].inc()
                last = _CfResult("transient", status, body, codes)
                retry_delay = _backoff_delay(attempt)
            else:
                # Remaining 4xx: a definitive answer (auth, validation, not
                # found). Retrying cannot change it — classify and return.
                errors = body.get("errors") if isinstance(body, dict) else None
                logging.error(
                    f"{method} {url}: HTTP {status}, codes {list(codes)}: {errors}"
                )
                prometheus_metrics["cf_api_error_counter"].inc()
                return _CfResult("http_error", status, body, codes)

        if attempt < attempts and retry_delay is not None:
            _wait(retry_delay, stop_event)

    return last


def _validate_record(record: Any) -> bool:
    """Schema check for one DNS record object from the API."""
    return (
        isinstance(record, dict)
        and isinstance(record.get("id"), str) and bool(record["id"])
        and isinstance(record.get("content"), str)
        and isinstance(record.get("ttl"), int)
        and isinstance(record.get("proxied"), bool)
    )


def get_external_ip(ip_session: requests.Session, *,
                    stop_event=None) -> Optional[str]:
    """
    Retrieve the external IPv4 address from the check services.
    Returns the IP address as a string, or None if all attempts fail.
    """
    for service in check_ip_services:
        if stop_event is not None and stop_event.is_set():
            return None
        hostname = urlparse(service).hostname
        try:
            response = ip_session.get(service, timeout=DEFAULT_TIMEOUT, stream=True)
            try:
                response.raise_for_status()
                data = b""
                for chunk in response.iter_content(
                        chunk_size=CHECK_IP_MAX_BODY_BYTES + 1):
                    data += chunk
                    if len(data) > CHECK_IP_MAX_BODY_BYTES:
                        raise ValueError(
                            f"response exceeds {CHECK_IP_MAX_BODY_BYTES} bytes"
                        )
                ip = data.decode("utf-8").strip()
            finally:
                response.close()

            if validate_ipv4(ip):
                return ip
            logging.error(f"Invalid IP format received from {service}: {ip!r}")

        except (requests.exceptions.RequestException, ValueError) as e:
            logging.error(f"Error retrieving external IP from {service}: {e}")

        prometheus_metrics["ip_retrieval_error_counter"].labels(
            check_ip_service_host=hostname).inc()

    return None


def get_dns_record(cf_session: requests.Session, zone_id: str, host: str, *,
                   stop_event=None) -> Tuple[Outcome, Optional[Dict[str, Any]]]:
    """
    Read the A record for `host`.

    Returns (OK, record) | (ABSENT, None) | (AMBIGUOUS, None)
    | (TRANSIENT, None) | (PERMANENT, None). ABSENT is reported only on a
    confirmed empty result — never on an error (C1).
    """
    url = f"{CF_API_BASE}/zones/{zone_id}/dns_records"

    def _validate(body: Dict[str, Any]) -> Optional[str]:
        records = body.get("result")
        if not isinstance(records, list):
            return "result is not a list"
        for record in records:
            if not _validate_record(record):
                return f"malformed record object: {record!r}"
        return None

    result = _cf_request(
        cf_session, "GET", url,
        params={"type": "A", "name": host},
        validate=_validate,
        stop_event=stop_event,
    )

    if result.kind == "ok":
        records = result.body["result"]
        if not records:
            logging.debug(f"No DNS A record found for host '{host}'.")
            return Outcome.ABSENT, None
        if len(records) > 1:
            ids = [record["id"] for record in records]
            logging.error(
                f"{len(records)} A records exist for host '{host}' ({ids}); "
                "refusing to guess ownership. Remove the duplicates manually."
            )
            return Outcome.AMBIGUOUS, None
        record = records[0]
        return Outcome.OK, {
            "id": record["id"],
            "content": record["content"],  # Current IP in DNS
            "ttl": record["ttl"],
            "proxied": record["proxied"],
        }
    if result.kind == "transient":
        return Outcome.TRANSIENT, None
    return Outcome.PERMANENT, None


def create_dns_record(cf_session: requests.Session, zone_id: str, host: str,
                      ip: str, ttl: int, proxied: bool, *,
                      stop_event=None) -> Tuple[Outcome, Optional[str]]:
    """
    Create a new A record. POST is not idempotent, so there is exactly one
    attempt; any uncertain outcome returns TRANSIENT and the caller must GET
    before deciding whether another create is allowed.

    Returns (OK, record_id) | (EXISTS, None) | (TRANSIENT, None)
    | (PERMANENT, None).
    """
    url = f"{CF_API_BASE}/zones/{zone_id}/dns_records"
    data = {
        "type": "A",
        "name": host,
        "content": ip,
        "ttl": ttl,
        "proxied": proxied
    }

    def _validate(body: Dict[str, Any]) -> Optional[str]:
        record = body.get("result")
        if not (isinstance(record, dict)
                and isinstance(record.get("id"), str) and record["id"]):
            return f"result has no usable record id: {record!r}"
        return None

    result = _cf_request(
        cf_session, "POST", url,
        json_body=data,
        idempotent=False,
        validate=_validate,
        stop_event=stop_event,
    )

    if result.kind == "ok":
        record_id = result.body["result"]["id"]
        logging.info(f"DNS A record for host {host} created with IP {ip}.")
        return Outcome.OK, record_id
    if result.kind in ("api_error", "http_error"):
        if CF_CODES_RECORD_EXISTS.intersection(result.codes):
            logging.warning(f"A record for host {host} already exists; not created.")
            return Outcome.EXISTS, None
        return Outcome.PERMANENT, None
    return Outcome.TRANSIENT, None


def update_cloudflare_record(cf_session: requests.Session, zone_id: str,
                             record_id: str, host: str, new_ip: str, ttl: int,
                             proxied: bool, *, stop_event=None) -> Outcome:
    """
    Update the A record via PATCH (edit), sending only the fields this
    utility owns so unmanaged metadata (comment, tags, settings) survives.

    Returns OK | GONE | TRANSIENT | PERMANENT.
    """
    url = f"{CF_API_BASE}/zones/{zone_id}/dns_records/{record_id}"
    data = {
        "type": "A",
        "name": host,
        "content": new_ip,
        "ttl": ttl,
        "proxied": proxied
    }

    result = _cf_request(
        cf_session, "PATCH", url,
        json_body=data,
        stop_event=stop_event,
    )

    if result.kind == "ok":
        logging.info(f"DNS record for host {host} successfully updated to IP {new_ip}.")
        return Outcome.OK
    if result.kind in ("api_error", "http_error"):
        if (result.status_code == 404
                or CF_CODE_RECORD_NOT_FOUND in result.codes):
            logging.warning(f"DNS record {record_id} no longer exists (host {host}).")
            return Outcome.GONE
        return Outcome.PERMANENT
    return Outcome.TRANSIENT


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
                      current_ip: str,
                      cf_session: requests.Session) -> Tuple[bool, Optional[str]]:
    """
    Fail-closed DNS update orchestration.

    Invariant (C1): create_dns_record() is called only after a confirmed
    ABSENT in the same attempt; TRANSIENT, PERMANENT, and AMBIGUOUS outcomes
    never mutate anything.

    Returns (success, record_id).

    # TODO(stage-3): replaced by the full decision table with DdnsState.
    """
    zone_id = config["zone_id"]
    host = config["host"]
    ttl = config["ttl"]
    proxied = config["proxied"]

    if record_id:
        outcome = update_cloudflare_record(
            cf_session, zone_id, record_id, host, current_ip, ttl, proxied
        )
        if outcome is Outcome.OK:
            return True, record_id
        if outcome is not Outcome.GONE:
            # TRANSIENT or PERMANENT: nothing may be mutated this attempt.
            return False, record_id
        # The record id is confirmed dead — fall through to a fresh read.
        logging.info("Record is gone. Re-reading DNS state.")
        record_id = None

    get_outcome, record = get_dns_record(cf_session, zone_id, host)

    if get_outcome is Outcome.OK:
        new_record_id = record["id"]
        outcome = update_cloudflare_record(
            cf_session, zone_id, new_record_id, host, current_ip, ttl, proxied
        )
        return outcome is Outcome.OK, new_record_id

    if get_outcome is Outcome.ABSENT:
        logging.info("Creating new DNS record.")
        create_outcome, new_record_id = create_dns_record(
            cf_session, zone_id, host, current_ip, ttl, proxied
        )
        if create_outcome is Outcome.OK:
            return True, new_record_id
        return False, None

    # TRANSIENT, PERMANENT, or AMBIGUOUS read: fail closed, no create.
    return False, None


def main():
    configure_logging()
    config = parse_env()

    # Initialize Prometheus metrics with zero values
    initialize_metrics(config)

    # Start Prometheus metrics server
    start_http_server(config["metrics_port"])
    logging.info(f"Prometheus metrics server started on port {config['metrics_port']}")

    cf_session, ip_session = create_http_clients(config["token"])

    # Get initial DNS record information to avoid unnecessary updates
    outcome, record_info = get_dns_record(cf_session, config["zone_id"], config["host"])

    if outcome is Outcome.OK:
        last_ip = record_info["content"]
        record_id = record_info["id"]
        logging.info(f"Current DNS record found. Host: {config['host']}, IP: {last_ip}")
    elif outcome is Outcome.ABSENT:
        logging.warning(
            f"No existing DNS record found for host '{config['host']}'. "
            "Will create one on first IP retrieval."
        )
        last_ip = None
        record_id = None
    else:
        logging.warning(
            f"Could not read DNS state at startup ({outcome.value}); "
            "the main loop will re-read before any write."
        )
        last_ip = None
        record_id = None

    # Get current external IP and initialize metric
    logging.info("Getting initial external IP...")
    initial_ip = get_external_ip(ip_session)
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

                current_ip = get_external_ip(ip_session)

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
                        success, record_id = handle_dns_update(
                            config, record_id, current_ip, cf_session
                        )

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
                    logging.critical("Too many consecutive failures. Exiting.")
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
