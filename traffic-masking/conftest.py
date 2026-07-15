# Copyright © 2026 kogeler
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures and the live-process harness for the traffic-masking suite.

Test modules live at the project top level (uniform with cf-ddns), so they import
the runtime modules directly. The `live` marker is registered here instead of in a
separate pytest.ini.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent
SERVER = str(BASE_DIR / "traffic_masking_server.py")
CLIENT = str(BASE_DIR / "traffic_masking_client.py")
TEST_PSK = b"traffic-masking-test-key-material-32"


@dataclass
class SpawnedProcess:
    """A live-test child process and its current log cursor."""

    process: subprocess.Popen
    log_path: Path
    log_offset: int = 0

    def mark_log(self):
        """Move the cursor to the current end of the process log."""
        try:
            self.log_offset = self.log_path.stat().st_size
        except FileNotFoundError:
            self.log_offset = 0
        return self.log_offset


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: bounded end-to-end tests that spawn real client/server subprocesses",
    )


@pytest.fixture
def psk_file(tmp_path):
    """Create a restrictive binary PSK file shared by live client/server."""
    path = tmp_path / "control.psk"
    path.write_bytes(TEST_PSK)
    path.chmod(0o600)
    return path


def free_udp_port():
    """Return a currently-free UDP port on loopback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _path_from_log(log):
    return log.log_path if isinstance(log, SpawnedProcess) else Path(log)


def read_log(log, offset=0):
    """Return process log text from ``offset`` (empty if the log is absent)."""
    try:
        return _path_from_log(log).read_bytes()[offset:].decode(errors="replace")
    except FileNotFoundError:
        return ""


def _log_tail(log, offset=0, limit=4000):
    contents = read_log(log, offset=offset)
    return contents[-limit:] if contents else "<empty log>"


def wait_for(log, needle, timeout, offset=0, report_failure=True):
    """Poll a process log for ``needle``, reporting a useful tail on failure."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in read_log(log, offset=offset):
            return True
        if isinstance(log, SpawnedProcess) and log.process.poll() is not None:
            break
        time.sleep(0.1)
    if report_failure:
        print(
            f"Timed out waiting for {needle!r} in {_path_from_log(log)}:\n"
            f"{_log_tail(log, offset=offset)}",
            file=sys.stderr,
        )
    return False


def last_match(log, pattern, offset=0):
    """Return the last regex group-1 match in a log as float, or None."""
    import re

    values = re.findall(pattern, read_log(log, offset=offset))
    return float(values[-1]) if values else None


def stop_process(spawned, timeout=3):
    """Terminate a spawned process group, escalating to SIGKILL after timeout."""
    process = spawned.process if isinstance(spawned, SpawnedProcess) else spawned
    if process.poll() is not None:
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=timeout)


@pytest.fixture
def spawn(tmp_path):
    """Launch traffic-masking scripts as subprocesses; guarantee teardown.

    Returns a ``SpawnedProcess`` with process, log path, and current log offset.
    """
    procs = []

    def _spawn(script, args, name):
        log_path = tmp_path / f"{name}.log"
        handle = log_path.open("w")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [sys.executable, script, *args],
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        spawned = SpawnedProcess(proc, log_path)
        procs.append((spawned, handle))
        return spawned

    yield _spawn

    for spawned, handle in reversed(procs):
        stop_process(spawned)
        handle.close()


@pytest.fixture
def start_server(spawn):
    """Start a server on a late-reserved port, retrying bind races only."""

    def _start(args_for_port, name, attempts=5, port=None):
        last_log = None
        for attempt in range(attempts):
            selected_port = port if port is not None else free_udp_port()
            spawned = spawn(
                SERVER, args_for_port(selected_port), f"{name}-{attempt}"
            )
            if wait_for(
                spawned, "started", 5.0, report_failure=False
            ):
                return spawned, selected_port

            last_log = read_log(spawned)
            bind_conflict = (
                "address already in use" in last_log.lower()
                or "errno 98" in last_log.lower()
            )
            stop_process(spawned)
            if not bind_conflict:
                pytest.fail(f"Server failed to start:\n{_log_tail(spawned)}")
            time.sleep(0.1)

        pytest.fail(
            f"Server port remained busy after {attempts} attempts:\n"
            f"{last_log or '<empty log>'}"
        )

    return _start
