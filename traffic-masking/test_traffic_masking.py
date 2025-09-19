#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Unified test suite for Traffic Masking System
Tests all modules and verifies real data transmission
"""

import subprocess
import time
import sys
import os
import signal
import re
import socket
import threading
import argparse
from pathlib import Path

# Test results storage
test_results = {
    'modules': {},
    'integration': {},
    'transmission': {}
}

def print_header(title):
    """Print formatted section header"""
    print()
    print("=" * 60)
    print(f" {title}")
    print("=" * 60)
    print()

def test_module_imports():
    """Test that all required modules can be imported"""
    print_header("MODULE IMPORT TEST")

    modules = [
        ('masking_lib', 'Core library'),
        ('traffic_masking_server', 'Server module'),
        ('traffic_masking_client', 'Client module'),
    ]

    # Optional enhanced modules
    enhanced_modules = [
        ('enhanced.timing', 'Adaptive timing'),
        ('enhanced.correlation', 'Correlation breaker'),
        ('enhanced.ml_resistance', 'ML resistance'),
        ('enhanced.entropy', 'Entropy enhancer'),
        ('enhanced.state_machine', 'Protocol state machine'),
    ]

    all_passed = True

    # Test core modules
    for module_name, description in modules:
        try:
            __import__(module_name)
            print(f"✓ {description} ({module_name})")
            test_results['modules'][module_name] = True
        except ImportError as e:
            print(f"✗ {description} ({module_name}): {e}")
            test_results['modules'][module_name] = False
            all_passed = False

    # Test enhanced modules (optional)
    print("\nEnhanced modules (optional):")
    for module_name, description in enhanced_modules:
        try:
            __import__(module_name)
            print(f"✓ {description} ({module_name})")
            test_results['modules'][module_name] = True
        except ImportError:
            print(f"○ {description} ({module_name}) - not available")
            test_results['modules'][module_name] = None

    return all_passed

def test_core_functions():
    """Test core library functions"""
    print_header("CORE FUNCTION TEST")

    try:
        from masking_lib import (
            TrafficProfile, ProtocolMimicry, DynamicObfuscator,
            stream_generator, parse_profile, build_obfuscator
        )

        tests_passed = 0
        tests_total = 0

        # Test 1: Profile parsing
        tests_total += 1
        try:
            profile = parse_profile('mixed')
            assert profile == TrafficProfile.MIXED
            print("✓ Profile parsing works")
            tests_passed += 1
        except Exception as e:
            print(f"✗ Profile parsing failed: {e}")

        # Test 2: Pattern generation
        tests_total += 1
        try:
            patterns = ProtocolMimicry.for_profile(TrafficProfile.MIXED)
            assert len(patterns) > 0
            print(f"✓ Pattern generation works ({len(patterns)} patterns)")
            tests_passed += 1
        except Exception as e:
            print(f"✗ Pattern generation failed: {e}")

        # Test 3: Obfuscator creation
        tests_total += 1
        try:
            obf = DynamicObfuscator()
            test_data = b"test packet data"
            fragments, delay = obf.obfuscate(test_data)
            assert len(fragments) > 0
            assert delay >= 0
            print(f"✓ Obfuscator works ({len(fragments)} fragments)")
            tests_passed += 1
        except Exception as e:
            print(f"✗ Obfuscator failed: {e}")

        # Test 4: Stream generator
        tests_total += 1
        try:
            gen = stream_generator(TrafficProfile.MIXED, target_mbps=1.0)
            fragments, delay = next(gen)
            assert len(fragments) > 0
            assert delay > 0
            print(f"✓ Stream generator works")
            tests_passed += 1
        except Exception as e:
            print(f"✗ Stream generator failed: {e}")

        # Test 5: Floating rate generator
        tests_total += 1
        try:
            gen = stream_generator(TrafficProfile.MIXED, min_mbps=1.0, max_mbps=5.0)
            fragments, delay = next(gen)
            assert len(fragments) > 0
            assert delay > 0
            print(f"✓ Floating rate generator works")
            tests_passed += 1
        except Exception as e:
            print(f"✗ Floating rate generator failed: {e}")

        print(f"\nCore tests: {tests_passed}/{tests_total} passed")
        test_results['modules']['core_functions'] = (tests_passed == tests_total)
        return tests_passed == tests_total

    except ImportError as e:
        print(f"✗ Cannot test core functions: {e}")
        test_results['modules']['core_functions'] = False
        return False

def test_network_connectivity():
    """Test basic network connectivity"""
    print_header("NETWORK CONNECTIVITY TEST")

    try:
        # Test UDP socket creation
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        print(f"✓ UDP socket creation works (bound to port {port})")

        # Test loopback send/receive
        def receiver():
            data, addr = sock.recvfrom(1024)
            test_results['modules']['loopback'] = (data == b"TEST")

        recv_thread = threading.Thread(target=receiver)
        recv_thread.daemon = True
        recv_thread.start()

        time.sleep(0.1)
        sock.sendto(b"TEST", ('127.0.0.1', port))
        recv_thread.join(timeout=1)

        sock.close()

        if test_results['modules'].get('loopback'):
            print("✓ Loopback communication works")
            return True
        else:
            print("✗ Loopback communication failed")
            return False

    except Exception as e:
        print(f"✗ Network test failed: {e}")
        return False

def run_transmission_test(duration=30, show_output=False):
    """Run full system test with real data transmission"""
    print_header(f"TRANSMISSION TEST ({duration} seconds)")

    # Paths for log files
    server_log_path = "server_test.log"
    client_log_path = "client_test.log"

    server_proc = None
    client_proc = None

    try:
        # Start server
        print("[TEST] Starting server with floating rate (2-8 Mbps)...")
        if show_output:
            server_proc = subprocess.Popen(
                [sys.executable, "traffic_masking_server.py",
                 "--port", "8888",
                 "--min-mbps", "2",
                 "--max-mbps", "8",
                 "--advanced",
                 "--profile", "mixed",
                 "--stats-interval", "2"]
            )
        else:
            with open(server_log_path, "w") as server_log:
                server_proc = subprocess.Popen(
                    [sys.executable, "traffic_masking_server.py",
                     "--port", "8888",
                     "--min-mbps", "2",
                     "--max-mbps", "8",
                     "--advanced",
                     "--profile", "mixed",
                     "--stats-interval", "2"],
                    stdout=server_log,
                    stderr=subprocess.STDOUT
                )

        # Wait for server to start
        time.sleep(2)

        if server_proc.poll() is not None:
            print("[ERROR] Server failed to start")
            test_results['transmission']['server_started'] = False
            return False

        print("[TEST] Server started successfully")
        test_results['transmission']['server_started'] = True

        # Start client
        print("[TEST] Starting client with 30% response ratio...")
        if show_output:
            client_proc = subprocess.Popen(
                [sys.executable, "traffic_masking_client.py",
                 "--server", "127.0.0.1",
                 "--port", "8888",
                 "--response", "0.3",
                 "--advanced",
                 "--uplink-profile", "mixed",
                 "--stats-interval", "2"]
            )
        else:
            with open(client_log_path, "w") as client_log:
                client_proc = subprocess.Popen(
                    [sys.executable, "traffic_masking_client.py",
                     "--server", "127.0.0.1",
                     "--port", "8888",
                     "--response", "0.3",
                     "--advanced",
                     "--uplink-profile", "mixed",
                     "--stats-interval", "2"],
                    stdout=client_log,
                    stderr=subprocess.STDOUT
                )

        # Wait for client to connect
        time.sleep(2)

        if client_proc.poll() is not None:
            print("[ERROR] Client failed to start")
            test_results['transmission']['client_started'] = False
            return False

        print("[TEST] Client connected successfully")
        test_results['transmission']['client_started'] = True

        # Monitor transmission
        print(f"[TEST] Running transmission test...")
        if not show_output:
            for i in range(duration):
                time.sleep(1)
                progress = (i + 1) / duration * 100
                print(f"[TEST] Progress: {progress:.0f}% ({i+1}/{duration}s)", end='\r')

                # Check processes are still running
                if server_proc.poll() is not None:
                    print("\n[WARNING] Server stopped unexpectedly")
                    break
                if client_proc.poll() is not None:
                    print("\n[WARNING] Client stopped unexpectedly")
                    break
            print()  # New line after progress
        else:
            print(f"[TEST] Waiting {duration} seconds... (Press Ctrl+C to stop early)")
            try:
                time.sleep(duration)
            except KeyboardInterrupt:
                print("\n[TEST] Interrupted by user")

        # Stop processes
        print("[TEST] Stopping client...")
        if client_proc:
            client_proc.terminate()
            try:
                client_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                client_proc.kill()
                client_proc.wait()

        print("[TEST] Stopping server...")
        if server_proc:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait()

        # Analyze logs if not showing output
        if not show_output:
            return analyze_transmission_logs(server_log_path, client_log_path)
        else:
            print("\n[TEST] Transmission test completed (manual verification required)")
            return True

    except Exception as e:
        print(f"\n[ERROR] Transmission test failed: {e}")
        # Cleanup
        if server_proc and server_proc.poll() is None:
            server_proc.kill()
        if client_proc and client_proc.poll() is None:
            client_proc.kill()
        return False
    finally:
        # Ensure processes are terminated
        if server_proc and server_proc.poll() is None:
            server_proc.terminate()
        if client_proc and client_proc.poll() is None:
            client_proc.terminate()

def analyze_transmission_logs(server_log, client_log):
    """Analyze logs to verify transmission"""
    print("\n[TEST] Analyzing transmission logs...")

    def extract_stats(line):
        """Extract statistics from log line"""
        stats = {}

        # Extract rate in Mbps
        rate_match = re.search(r'Rate:\s*([0-9.]+)\s*Mbps', line)
        if rate_match:
            stats['rate'] = float(rate_match.group(1))

        # Extract Rx/Tx rates for client
        rx_match = re.search(r'Rx:\s*([0-9.]+)\s*Mbps', line)
        tx_match = re.search(r'Tx:\s*([0-9.]+)\s*Mbps', line)
        if rx_match:
            stats['rx'] = float(rx_match.group(1))
        if tx_match:
            stats['tx'] = float(tx_match.group(1))

        # Extract client count
        clients_match = re.search(r'Clients:\s*(\d+)', line)
        if clients_match:
            stats['clients'] = int(clients_match.group(1))

        return stats

    server_stats = []
    client_stats = []

    # Parse server log
    try:
        with open(server_log, 'r') as f:
            for line in f:
                if '[STATS]' in line:
                    stats = extract_stats(line)
                    if stats:
                        server_stats.append(stats)
    except Exception as e:
        print(f"[WARNING] Could not parse server log: {e}")

    # Parse client log
    try:
        with open(client_log, 'r') as f:
            for line in f:
                if '[STATS]' in line:
                    stats = extract_stats(line)
                    if stats:
                        client_stats.append(stats)
    except Exception as e:
        print(f"[WARNING] Could not parse client log: {e}")

    # Analyze results
    success = True

    if server_stats:
        rates = [s.get('rate', 0) for s in server_stats if 'rate' in s]
        if rates:
            avg_rate = sum(rates) / len(rates)
            min_rate = min(rates)
            max_rate = max(rates)
            print(f"✓ Server: Avg={avg_rate:.2f} Mbps, Min={min_rate:.2f} Mbps, Max={max_rate:.2f} Mbps")
            test_results['transmission']['server_rate'] = avg_rate

            # Check if rate is within expected floating range (2-8 Mbps)
            if min_rate >= 1.0 and max_rate <= 10.0:
                print(f"✓ Server rate within expected range")
            else:
                print(f"⚠ Server rate outside expected range")

        # Check client connections
        clients = [s.get('clients', 0) for s in server_stats if 'clients' in s]
        if clients and max(clients) > 0:
            print(f"✓ Server had {max(clients)} client(s) connected")
            test_results['transmission']['clients_connected'] = True
        else:
            print(f"✗ No clients connected to server")
            test_results['transmission']['clients_connected'] = False
            success = False
    else:
        print("✗ No server statistics found")
        success = False

    if client_stats:
        rx_rates = [s.get('rx', 0) for s in client_stats if 'rx' in s]
        tx_rates = [s.get('tx', 0) for s in client_stats if 'tx' in s]

        if rx_rates:
            avg_rx = sum(rx_rates) / len(rx_rates)
            print(f"✓ Client Rx: {avg_rx:.2f} Mbps average")
            test_results['transmission']['client_rx'] = avg_rx

            if avg_rx > 0.5:
                print(f"✓ Client receiving data successfully")
            else:
                print(f"✗ Client receive rate too low")
                success = False

        if tx_rates:
            avg_tx = sum(tx_rates) / len(tx_rates)
            print(f"✓ Client Tx: {avg_tx:.2f} Mbps average")
            test_results['transmission']['client_tx'] = avg_tx

            if avg_tx > 0.1:
                print(f"✓ Client transmitting response traffic")
            else:
                print(f"⚠ Client transmit rate low")
    else:
        print("✗ No client statistics found")
        success = False

    return success

def print_summary():
    """Print test summary"""
    print_header("TEST SUMMARY")

    total_tests = 0
    passed_tests = 0

    # Count module tests
    for module, result in test_results['modules'].items():
        if result is not None:  # Skip optional modules
            total_tests += 1
            if result:
                passed_tests += 1

    # Count transmission tests
    for test, result in test_results['transmission'].items():
        if isinstance(result, bool):
            total_tests += 1
            if result:
                passed_tests += 1

    print(f"Total tests run: {total_tests}")
    print(f"Tests passed: {passed_tests}")
    print(f"Tests failed: {total_tests - passed_tests}")
    print(f"Success rate: {(passed_tests/total_tests*100) if total_tests > 0 else 0:.1f}%")

    if passed_tests == total_tests:
        print("\n🎉 ALL TESTS PASSED! The Traffic Masking System is working correctly.")
    elif passed_tests > total_tests * 0.7:
        print("\n✓ Most tests passed. The system is mostly functional.")
    else:
        print("\n⚠ Several tests failed. Please check the issues above.")

    return passed_tests == total_tests

def main():
    """Main test runner"""
    parser = argparse.ArgumentParser(description='Test Traffic Masking System')
    parser.add_argument('--duration', type=int, default=30,
                       help='Transmission test duration in seconds (default: 30)')
    parser.add_argument('--quick', action='store_true',
                       help='Run quick tests only (skip long transmission test)')
    parser.add_argument('--output', action='store_true',
                       help='Show server and client output during transmission test')
    parser.add_argument('--modules-only', action='store_true',
                       help='Test modules only, skip transmission test')

    args = parser.parse_args()

    print("=" * 60)
    print(" TRAFFIC MASKING SYSTEM - COMPLETE TEST SUITE")
    print("=" * 60)

    try:
        # Run module tests
        modules_ok = test_module_imports()
        if modules_ok:
            core_ok = test_core_functions()
            network_ok = test_network_connectivity()
        else:
            print("\n[ERROR] Core modules failed to import, skipping other tests")
            return 1

        # Run transmission test unless skipped
        if not args.modules_only:
            if args.quick:
                transmission_ok = run_transmission_test(duration=10, show_output=args.output)
            else:
                transmission_ok = run_transmission_test(duration=args.duration, show_output=args.output)
        else:
            print("\n[INFO] Skipping transmission test (--modules-only)")
            transmission_ok = None

        # Print summary
        all_passed = print_summary()

        return 0 if all_passed else 1

    except KeyboardInterrupt:
        print("\n\n[TEST] Test suite interrupted by user")
        return 1
    except Exception as e:
        print(f"\n[ERROR] Test suite failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
