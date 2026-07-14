#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

import os
import re
import sys
import time
import random
import signal
import logging
import ipaddress
import threading
import requests
from dataclasses import dataclass
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

# RFC 1123 hostname label (the stdlib idna codec passes ASCII labels through
# unvalidated, so labels must be checked explicitly).
_HOSTNAME_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_host(host_raw: str) -> str:
    """
    Normalize a hostname (lowercase, IDNA-encode, strip the root dot) and
    validate it as a DNS name. Raises ValueError when invalid (M6).
    """
    host = host_raw.strip().rstrip(".").lower()
    if not host:
        raise ValueError("empty hostname")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as e:
        raise ValueError(f"IDNA encoding failed: {e}") from e
    if len(host) > 253:
        raise ValueError("hostname longer than 253 characters")
    for label in host.split("."):
        if not _HOSTNAME_LABEL_RE.match(label):
            raise ValueError(f"invalid DNS label {label!r}")
    return host


class Outcome(str, Enum):
    """Classified result of a Cloudflare API call (frozen contract)."""

    OK = "ok"                # request succeeded
    ABSENT = "absent"        # confirmed: no record (HTTP 200, empty result list)
    GONE = "gone"            # record id invalid (HTTP 404 and/or CF code 81044)
    EXISTS = "exists"        # create refused: record already exists (81057, 81058)
    AMBIGUOUS = "ambiguous"  # multiple matching A records; do not guess ownership
    TRANSIENT = "transient"  # network / 5xx / 429 — retries exhausted
    PERMANENT = "permanent"  # other 4xx (auth, validation) — not retryable


# Set by SIGTERM/SIGINT; every wait and every new HTTP attempt observes it.
# Shutdown is bounded by one in-flight read timeout plus scheduling margin.
shutdown_event = threading.Event()


def _handle_signal(signum, frame):
    """Signal handler: request a graceful shutdown."""
    logging.info(f"Received {signal.Signals(signum).name}; shutting down.")
    shutdown_event.set()


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
    "unconfirmed_ip_counter": Counter(
        "cf_ddns_unconfirmed_ip_readings_total",
        "Number of new-IP readings discarded before confirmation."
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
    token = (os.environ.get("CF_DDNS_TOKEN") or "").strip()
    zone_id = (os.environ.get("CF_DDNS_ZONE_ID") or "").strip()
    host_raw = (os.environ.get("CF_DDNS_HOST") or "").strip().rstrip(".")

    if not all([token, zone_id, host_raw]):
        logging.error(
            "Environment variables CF_DDNS_TOKEN, CF_DDNS_ZONE_ID, and CF_DDNS_HOST "
            "must be set to non-empty values."
        )
        sys.exit(1)

    # Normalize and validate the hostname as an IDNA DNS name (M6)
    try:
        host = _normalize_host(host_raw)
    except ValueError as e:
        logging.error(f"Invalid value for CF_DDNS_HOST: {host_raw!r} is not a valid DNS name ({e}).")
        sys.exit(1)

    interval_str = os.environ.get("CF_DDNS_INTERVAL", "10")
    try:
        interval = int(interval_str)
        if interval < 1:
            raise ValueError("Interval must be at least 1 second")
    except ValueError as e:
        logging.error(f"Invalid value for CF_DDNS_INTERVAL: {interval_str}. {e}")
        sys.exit(1)

    # TTL (M6): 1 = Cloudflare Auto, otherwise 30-86400 (30-59 Enterprise only)
    ttl_str = os.environ.get("CF_DDNS_TTL", "120")
    try:
        ttl = int(ttl_str)
    except ValueError:
        logging.error(f"Invalid value for CF_DDNS_TTL: {ttl_str}.")
        sys.exit(1)
    if ttl != 1 and not (30 <= ttl <= 86400):
        logging.error(
            f"Invalid value for CF_DDNS_TTL: {ttl}. "
            "Cloudflare accepts 1 (Auto) or 30-86400 seconds."
        )
        sys.exit(1)
    if 30 <= ttl <= 59:
        logging.warning(
            f"TTL {ttl} requires a Cloudflare Enterprise zone; "
            "standard zones accept 60-86400."
        )

    # Proxied (M6): strict boolean — a typo must not silently disable the proxy
    proxied_str = (os.environ.get("CF_DDNS_PROXIED", "False")).strip().lower()
    if proxied_str not in ("true", "false"):
        logging.error(
            f"Invalid value for CF_DDNS_PROXIED: {proxied_str!r}. Use 'true' or 'false'."
        )
        sys.exit(1)
    proxied = proxied_str == "true"

    # Proxied records always use Cloudflare Auto TTL; normalize the effective
    # TTL so reconciliation comparisons cannot flap forever (M6).
    if proxied and ttl != 1:
        logging.warning(
            f"CF_DDNS_PROXIED=true forces TTL Auto (1); configured TTL {ttl} is ignored."
        )
        ttl = 1

    # Prometheus metrics endpoint port
    metrics_port_str = os.environ.get("CF_DDNS_METRICS_PORT", "9101")
    try:
        metrics_port = int(metrics_port_str)
        if not (1 <= metrics_port <= 65535):
            raise ValueError("Port must be between 1 and 65535")
    except ValueError as e:
        logging.error(f"Invalid value for CF_DDNS_METRICS_PORT: {metrics_port_str}. {e}")
        sys.exit(1)

    # Consecutive-failure budget shared by IP retrieval and DNS updates (H2)
    max_failures_str = os.environ.get("CF_DDNS_MAX_FAILURES", "10")
    try:
        max_failures = int(max_failures_str)
        if max_failures < 1:
            raise ValueError("Max consecutive failures must be at least 1")
    except ValueError as e:
        logging.error(f"Invalid value for CF_DDNS_MAX_FAILURES: {max_failures_str}. {e}")
        sys.exit(1)

    # Periodic reconciliation interval in seconds; 0 disables (M3)
    reconcile_str = os.environ.get("CF_DDNS_RECONCILE_INTERVAL", "3600")
    try:
        reconcile_interval = int(reconcile_str)
        if reconcile_interval < 0:
            raise ValueError("Reconcile interval must be 0 (disabled) or positive")
    except ValueError as e:
        logging.error(f"Invalid value for CF_DDNS_RECONCILE_INTERVAL: {reconcile_str}. {e}")
        sys.exit(1)

    # A changed IP must be observed this many consecutive iterations (M9/R2)
    confirm_str = os.environ.get("CF_DDNS_CONFIRM_CYCLES", "2")
    try:
        confirm_cycles = int(confirm_str)
        if confirm_cycles < 1:
            raise ValueError("Confirm cycles must be at least 1")
    except ValueError as e:
        logging.error(f"Invalid value for CF_DDNS_CONFIRM_CYCLES: {confirm_str}. {e}")
        sys.exit(1)

    return {
        "token": token,
        "zone_id": zone_id,
        "host": host,
        "interval": interval,
        "ttl": ttl,
        "proxied": proxied,
        "metrics_port": metrics_port,
        "max_failures": max_failures,
        "reconcile_interval": reconcile_interval,
        "confirm_cycles": confirm_cycles
    }


def validate_ipv4(ip: str) -> bool:
    """
    Validate that `ip` is a strictly formatted, globally routable unicast
    IPv4 address — the only thing a public DDNS record may contain (M7/L1).

    Rejects malformed forms (leading zeros, whitespace, signs) and
    non-public ranges: private, loopback, link-local, CGNAT, unspecified,
    reserved, broadcast — and multicast explicitly, because `is_global`
    alone is True for multicast addresses.
    """
    if not isinstance(ip, str):
        return False
    try:
        address = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return address.is_global and not address.is_multicast


@dataclass
class DdnsState:
    """Mutable loop state threaded through run_iteration() (frozen contract)."""

    last_ip: Optional[str] = None
    record_id: Optional[str] = None
    ip_failures: int = 0         # consecutive external-IP retrieval failures
    cf_failures: int = 0         # consecutive Cloudflare transient-failure iterations
    fatal: Optional[str] = None  # unrecoverable outcome; main() exits when set
    force_update: bool = False   # settings drift detected; rewrite on next pass (M2)
    pending_ip: Optional[str] = None  # candidate new IP awaiting confirmation (M9)
    pending_seen: int = 0             # consecutive observations of pending_ip
    last_reconcile: Optional[float] = None  # monotonic ts of last reconciliation (M3)


@dataclass
class HttpClients:
    """The two persistent HTTP sessions (frozen contract)."""

    cloudflare: requests.Session  # carries the bearer header; CF hosts only
    check_ip: requests.Session    # never carries Cloudflare credentials


def create_http_clients(token: str) -> HttpClients:
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
    return HttpClients(cloudflare=cf_session, check_ip=ip_session)


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
    prometheus_metrics["unconfirmed_ip_counter"]._value.set(0)

    # Initialize timestamps with 0 (never checked/updated)
    prometheus_metrics["last_ip_check_timestamp"].set(0)
    prometheus_metrics["last_ip_update_timestamp"].set(0)

    logging.debug("All metrics initialized with zero values.")


def handle_dns_update(config: Dict[str, Any], record_id: Optional[str],
                      current_ip: str,
                      cf_session: requests.Session) -> Tuple[Outcome, Optional[str]]:
    """
    DNS write orchestration (full decision table, C1 fix).

    Invariant: create_dns_record() is called only after a confirmed ABSENT in
    the same iteration; TRANSIENT, PERMANENT, and AMBIGUOUS outcomes never
    mutate anything; a create is never issued twice.

    Returns (aggregated outcome, best-known record id). The aggregated
    outcome is one of OK, TRANSIENT, PERMANENT, AMBIGUOUS.
    """
    zone_id = config["zone_id"]
    host = config["host"]
    ttl = config["ttl"]
    proxied = config["proxied"]

    def _update(rid: str) -> Outcome:
        return update_cloudflare_record(
            cf_session, zone_id, rid, host, current_ip, ttl, proxied,
            stop_event=shutdown_event
        )

    if record_id:
        outcome = _update(record_id)
        if outcome is Outcome.OK:
            return Outcome.OK, record_id
        if outcome is not Outcome.GONE:
            # TRANSIENT or PERMANENT: nothing may be mutated this attempt.
            return outcome, record_id
        # The record id is confirmed dead — fall through to a fresh read.
        logging.info("Record is gone. Re-reading DNS state.")

    get_outcome, record = get_dns_record(cf_session, zone_id, host,
                                         stop_event=shutdown_event)

    if get_outcome is Outcome.OK:
        outcome = _update(record["id"])
        if outcome is Outcome.GONE:
            # Deleted between read and write; the next iteration re-reads.
            return Outcome.TRANSIENT, None
        return outcome, record["id"]

    if get_outcome is Outcome.ABSENT:
        logging.info("Creating new DNS record.")
        create_outcome, new_record_id = create_dns_record(
            cf_session, zone_id, host, current_ip, ttl, proxied,
            stop_event=shutdown_event
        )
        if create_outcome is Outcome.OK:
            return Outcome.OK, new_record_id
        if create_outcome is Outcome.EXISTS:
            # Raced with another writer: adopt the existing record and update
            # it — never issue a second create.
            adopt_outcome, adopted = get_dns_record(cf_session, zone_id, host,
                                                    stop_event=shutdown_event)
            if adopt_outcome is Outcome.OK:
                outcome = _update(adopted["id"])
                if outcome is Outcome.GONE:
                    return Outcome.TRANSIENT, None
                return outcome, adopted["id"]
            if adopt_outcome in (Outcome.PERMANENT, Outcome.AMBIGUOUS):
                return adopt_outcome, None
            # ABSENT-after-EXISTS or TRANSIENT: unstable — retry next iteration.
            return Outcome.TRANSIENT, None
        return create_outcome, None  # TRANSIENT or PERMANENT

    # AMBIGUOUS, TRANSIENT, or PERMANENT read: fail closed, no create.
    return get_outcome, None


def startup_state(config: Dict[str, Any], clients: HttpClients) -> DdnsState:
    """
    Prime loop state from the existing DNS record and the current external IP.

    A transient read failure leaves the state empty — the loop re-reads before
    any write (C1). A PERMANENT or AMBIGUOUS read marks the state fatal.
    """
    state = DdnsState()
    outcome, record_info = get_dns_record(
        clients.cloudflare, config["zone_id"], config["host"],
        stop_event=shutdown_event
    )

    if outcome is Outcome.OK:
        state.last_ip = record_info["content"]
        state.record_id = record_info["id"]
        logging.info(f"Current DNS record found. Host: {config['host']}, IP: {state.last_ip}")

        # Startup reconciliation (M2): converge ttl/proxied even when the IP
        # is unchanged. config["ttl"] is already the effective desired TTL
        # (Auto for proxied records), so this cannot flap.
        if (record_info["ttl"] != config["ttl"]
                or record_info["proxied"] != config["proxied"]):
            logging.info(
                f"Record settings drift: ttl {record_info['ttl']} -> {config['ttl']}, "
                f"proxied {record_info['proxied']} -> {config['proxied']}. "
                "Will rewrite on the next iteration."
            )
            state.force_update = True
    elif outcome is Outcome.ABSENT:
        logging.warning(
            f"No existing DNS record found for host '{config['host']}'. "
            "Will create one on first IP retrieval."
        )
    elif outcome is Outcome.TRANSIENT:
        logging.warning(
            "Could not read DNS state at startup (transient); "
            "the main loop will re-read before any write."
        )
    else:  # PERMANENT or AMBIGUOUS: a config/ownership problem — fail fast
        state.fatal = outcome.value
        return state

    # Get current external IP and initialize metric
    logging.info("Getting initial external IP...")
    initial_ip = get_external_ip(clients.check_ip, stop_event=shutdown_event)
    if initial_ip:
        logging.info(f"Current external IP: {initial_ip}")
        # Set current external IP metric to 1
        prometheus_metrics["ip_info_gauge"].labels(cf_host=config["host"], ip=initial_ip).set(1)

        # If DNS IP exists and differs from current IP, set it to 0
        if state.last_ip and state.last_ip != initial_ip:
            prometheus_metrics["ip_info_gauge"].labels(cf_host=config["host"], ip=state.last_ip).set(0)
            logging.info(f"DNS IP {state.last_ip} differs from current IP {initial_ip}")
    else:
        logging.warning("Could not retrieve initial external IP")

    return state


def _reconcile(config: Dict[str, Any], state: DdnsState,
               clients: HttpClients) -> Optional[str]:
    """
    Periodic reconciliation (M3): re-read the record and detect external
    drift. Returns the IP to (re)write, or None when converged. Errors are
    counted/marked on the state; creation stays inside handle_dns_update
    behind its confirmed-ABSENT invariant (C1).
    """
    logging.debug("Reconciliation: re-reading DNS state.")
    outcome, record = get_dns_record(
        clients.cloudflare, config["zone_id"], config["host"],
        stop_event=shutdown_event
    )

    if outcome is Outcome.OK:
        if record["id"] != state.record_id:
            logging.warning(
                f"Reconciliation: record id changed externally "
                f"({state.record_id} -> {record['id']}); adopting."
            )
            state.record_id = record["id"]
        if (record["content"] != state.last_ip
                or record["ttl"] != config["ttl"]
                or record["proxied"] != config["proxied"]):
            logging.warning("Reconciliation: record drifted externally; converging.")
            return state.last_ip
        logging.debug("Reconciliation: record matches the desired state.")
        return None
    if outcome is Outcome.ABSENT:
        logging.warning("Reconciliation: record was deleted externally; recreating.")
        state.record_id = None
        return state.last_ip
    if outcome is Outcome.TRANSIENT:
        if not shutdown_event.is_set():
            state.cf_failures += 1
            logging.error(
                f"Reconciliation read failed (transient). "
                f"Failure {state.cf_failures}/{config['max_failures']}"
            )
        return None
    state.fatal = outcome.value  # PERMANENT or AMBIGUOUS
    return None


def run_iteration(config: Dict[str, Any], state: DdnsState,
                  clients: HttpClients, *, now=time.monotonic) -> DdnsState:
    """
    One check/update cycle. Never sleeps and never exits — main() enforces
    the failure policy on the returned state.

    A changed external IP is written only after `confirm_cycles` consecutive
    identical readings (M9/R2). Reconciliation and force_update writes reuse
    the already-confirmed IP and are exempt from confirmation. `now` is the
    monotonic clock, injectable for tests.
    """
    if shutdown_event.is_set():
        return state

    prometheus_metrics["last_ip_check_timestamp"].set(time.time())

    current_ip = get_external_ip(clients.check_ip, stop_event=shutdown_event)

    if current_ip is None:
        if shutdown_event.is_set():
            return state  # failure caused by shutdown, not by the network
        state.ip_failures += 1
        logging.error(
            f"Could not retrieve external IP. "
            f"Failure {state.ip_failures}/{config['max_failures']}"
        )
        return state
    state.ip_failures = 0

    write_ip: Optional[str] = None
    ip_changed = current_ip != state.last_ip

    if ip_changed:
        # Flap damping (M9): a new IP must be confirmed over consecutive
        # readings before it may touch DNS.
        if current_ip == state.pending_ip:
            state.pending_seen += 1
        else:
            if state.pending_ip is not None:
                logging.warning(
                    f"Discarding unconfirmed IP reading {state.pending_ip}; "
                    f"now observing {current_ip}."
                )
                prometheus_metrics["unconfirmed_ip_counter"].inc()
            state.pending_ip = current_ip
            state.pending_seen = 1

        if state.pending_seen >= config["confirm_cycles"]:
            write_ip = current_ip
        else:
            logging.info(
                f"New IP {current_ip} observed "
                f"({state.pending_seen}/{config['confirm_cycles']} confirmations); "
                "not writing yet."
            )
    else:
        if state.pending_ip is not None:
            logging.warning(
                f"Discarding unconfirmed IP reading {state.pending_ip}; "
                f"the external IP settled back to {state.last_ip}."
            )
            prometheus_metrics["unconfirmed_ip_counter"].inc()
            state.pending_ip = None
            state.pending_seen = 0

        if state.force_update:
            # Startup-detected settings drift (M2): rewrite the confirmed IP.
            write_ip = current_ip
        elif config["reconcile_interval"]:
            ts = now()
            if state.last_reconcile is None:
                state.last_reconcile = ts
            elif ts - state.last_reconcile >= config["reconcile_interval"]:
                state.last_reconcile = ts
                write_ip = _reconcile(config, state, clients)
                if state.fatal:
                    return state

    if write_ip is None:
        if not ip_changed:
            logging.debug(f"External IP remains {current_ip}. No update required.")
        return state

    if state.last_ip is None:
        logging.info(f"First confirmed IP: {write_ip}. Creating DNS record.")
    elif write_ip != state.last_ip:
        logging.info(f"IP changed from {state.last_ip} to {write_ip}. Updating DNS record.")
    else:
        logging.info(f"Rewriting DNS record for {write_ip} to converge settings.")

    outcome, record_id = handle_dns_update(
        config, state.record_id, write_ip, clients.cloudflare
    )
    state.record_id = record_id

    if outcome is Outcome.OK:
        state.cf_failures = 0
        state.force_update = False
        state.pending_ip = None
        state.pending_seen = 0
        prometheus_metrics["ip_update_counter"].inc()
        prometheus_metrics["last_ip_update_timestamp"].set(time.time())

        # Update IP gauge - keep all IPs in history (bounded in stage 6)
        if state.last_ip is not None and state.last_ip != write_ip:
            prometheus_metrics["ip_info_gauge"].labels(cf_host=config["host"], ip=state.last_ip).set(0)
        prometheus_metrics["ip_info_gauge"].labels(cf_host=config["host"], ip=write_ip).set(1)

        state.last_ip = write_ip
    elif outcome is Outcome.TRANSIENT:
        if shutdown_event.is_set():
            return state  # failure caused by shutdown, not by the API
        state.cf_failures += 1
        logging.error(
            f"DNS update failed (transient). "
            f"Failure {state.cf_failures}/{config['max_failures']}"
        )
    else:  # PERMANENT or AMBIGUOUS — unrecoverable, main() exits
        state.fatal = outcome.value

    return state


def enforce_failure_policy(config: Dict[str, Any], state: DdnsState) -> None:
    """
    Exit fatally on unrecoverable outcomes or an exhausted consecutive-failure
    budget (H2). Both failure classes share the same budget.
    """
    if state.fatal:
        logging.critical(
            f"Unrecoverable condition ({state.fatal}); exiting. "
            "See the errors above — this will not heal by retrying."
        )
        sys.exit(1)

    max_failures = config["max_failures"]
    if state.ip_failures >= max_failures:
        logging.critical(
            f"Failed to retrieve the external IP {max_failures} consecutive times. Exiting."
        )
        sys.exit(1)
    if state.cf_failures >= max_failures:
        logging.critical(
            f"DNS update failed {max_failures} consecutive times. Exiting."
        )
        sys.exit(1)


def run_loop(config: Dict[str, Any], state: DdnsState,
             clients: HttpClients) -> None:
    """
    The main loop: iterate, enforce the failure policy, wait interruptibly.
    Returns when the shutdown event is set.
    """
    while not shutdown_event.is_set():
        try:
            state = run_iteration(config, state, clients)
        except Exception as loop_error:
            logging.exception(f"An error occurred in the main loop: {loop_error}")
            state.cf_failures += 1

        enforce_failure_policy(config, state)

        logging.debug(f"Waiting {config['interval']} seconds before the next check...")
        shutdown_event.wait(timeout=config["interval"])


def main():
    configure_logging()
    config = parse_env()

    # Initialize Prometheus metrics with zero values
    initialize_metrics(config)

    # Graceful shutdown: SIGTERM (container stop) and SIGINT (Ctrl+C) both
    # set the shutdown event; waits and new HTTP attempts observe it, so the
    # exit is bounded by one in-flight read timeout plus a small margin.
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Start Prometheus metrics server
    httpd, metrics_thread = start_http_server(config["metrics_port"])
    logging.info(f"Prometheus metrics server started on port {config['metrics_port']}")

    clients = create_http_clients(config["token"])

    try:
        state = startup_state(config, clients)
        enforce_failure_policy(config, state)
        run_loop(config, state, clients)
        logging.info("Shutting down.")
    finally:
        httpd.shutdown()
        httpd.server_close()
        metrics_thread.join(timeout=5)
        clients.cloudflare.close()
        clients.check_ip.close()


if __name__ == "__main__":
    main()
