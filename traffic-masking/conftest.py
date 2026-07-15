# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures and the live-process harness for the traffic-masking suite.

Test modules live at the project top level (uniform with cf-ddns), so they import
the runtime modules directly. The `live` marker is registered here instead of in a
separate pytest.ini.
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent
SERVER = str(BASE_DIR / "traffic_masking_server.py")
CLIENT = str(BASE_DIR / "traffic_masking_client.py")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: bounded end-to-end tests that spawn real client/server subprocesses",
    )


def free_udp_port():
    """Return a currently-free UDP port on loopback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def read_log(log_path):
    """Return the current contents of a spawned process log (empty if absent)."""
    try:
        return Path(log_path).read_text(errors="replace")
    except FileNotFoundError:
        return ""


def wait_for(log_path, needle, timeout):
    """Poll a process log until it contains `needle` or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in read_log(log_path):
            return True
        time.sleep(0.1)
    return False


def last_match(log_path, pattern):
    """Return the last regex group-1 match in a log as float, or None."""
    import re

    values = re.findall(pattern, read_log(log_path))
    return float(values[-1]) if values else None


@pytest.fixture
def spawn(tmp_path):
    """Launch traffic-masking scripts as subprocesses; guarantee teardown.

    Returns spawn(script, args, name) -> (Popen, log_path).
    """
    procs = []

    def _spawn(script, args, name):
        log_path = tmp_path / f"{name}.log"
        handle = open(log_path, "w")
        proc = subprocess.Popen(
            [sys.executable, script, *args],
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        procs.append((proc, handle))
        return proc, str(log_path)

    yield _spawn

    for proc, handle in procs:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        handle.close()
