#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Test script for realistic traffic pattern generation
Verifies that floating rate creates natural variations reaching min/max boundaries
"""

import subprocess
import time
import sys
import os
import signal
import re
import statistics
import math
from collections import defaultdict
from datetime import datetime

def extract_rate_from_output(output_line):
    """Extract rate in Mbps from server/client output"""
    match = re.search(r'Rate:\s*([0-9.]+)\s*Mbps', output_line)
    if match:
        return float(match.group(1))
    return None

def calculate_rate_distribution(rates, min_mbps, max_mbps, bins=10):
    """Calculate distribution of rates across the range"""
    if not rates:
        return None

    distribution = defaultdict(int)
    range_size = max_mbps - min_mbps
    bin_size = range_size / bins

    for rate in rates:
        if rate < min_mbps:
            bin_idx = -1  # Below minimum
        elif rate > max_mbps:
            bin_idx = bins  # Above maximum
        else:
            bin_idx = int((rate - min_mbps) / bin_size)
            bin_idx = min(bin_idx, bins - 1)
        distribution[bin_idx] += 1

    return distribution

def analyze_boundary_visits(rates, min_mbps, max_mbps, threshold=0.1):
    """Analyze how often rates visit the boundaries"""
    if not rates:
        return None

    range_size = max_mbps - min_mbps
    near_min_threshold = min_mbps + range_size * threshold
    near_max_threshold = max_mbps - range_size * threshold

    visits = {
        'near_min': 0,
        'near_max': 0,
        'at_min': 0,
        'at_max': 0,
        'middle': 0
    }

    for rate in rates:
        if rate <= min_mbps + 0.01:
            visits['at_min'] += 1
        elif rate >= max_mbps - 0.01:
            visits['at_max'] += 1
        elif rate <= near_min_threshold:
            visits['near_min'] += 1
        elif rate >= near_max_threshold:
            visits['near_max'] += 1
        else:
            visits['middle'] += 1

    return visits

def detect_pattern_changes(rates, window_size=10):
    """Detect pattern changes in rate sequence"""
    if len(rates) < window_size * 2:
        return []

    changes = []
    for i in range(window_size, len(rates) - window_size):
        prev_window = rates[i-window_size:i]
        next_window = rates[i:i+window_size]

        prev_avg = statistics.mean(prev_window)
        next_avg = statistics.mean(next_window)
        prev_std = statistics.stdev(prev_window) if len(prev_window) > 1 else 0
        next_std = statistics.stdev(next_window) if len(next_window) > 1 else 0

        # Detect significant changes
        avg_change = abs(next_avg - prev_avg)
        std_change = abs(next_std - prev_std)

        if avg_change > 0.5 or std_change > 0.3:
            changes.append({
                'index': i,
                'time': i,  # Assuming 1 sample per second
                'avg_change': avg_change,
                'std_change': std_change
            })

    return changes

def visualize_rate_graph(rates, min_mbps, max_mbps, width=70):
    """Create ASCII graph of rate over time"""
    if not rates:
        return []

    graph = []
    range_size = max_mbps - min_mbps

    # Create header
    graph.append(f"Rate over time (min={min_mbps}, max={max_mbps} Mbps)")
    graph.append("=" * width)

    # Create graph lines
    for i, rate in enumerate(rates):
        if rate < min_mbps:
            position = 0
            marker = '<'  # Below minimum
        elif rate > max_mbps:
            position = width - 1
            marker = '>'  # Above maximum
        else:
            position = int((rate - min_mbps) / range_size * (width - 1))
            marker = '*'

        line = [' '] * width
        line[position] = marker

        # Mark boundaries
        min_pos = 0
        max_pos = width - 1
        if line[min_pos] == ' ':
            line[min_pos] = '|'
        if line[max_pos] == ' ':
            line[max_pos] = '|'

        # Mark center
        center_pos = width // 2
        if line[center_pos] == ' ':
            line[center_pos] = '.'

        time_label = f"{i:3d}s "
        rate_label = f" {rate:5.2f}"
        graph.append(time_label + ''.join(line) + rate_label)

    return graph

def test_realistic_patterns(min_mbps, max_mbps, duration=60):
    """Test realistic traffic patterns"""
    print(f"\n{'='*80}")
    print(f" REALISTIC PATTERN TEST")
    print(f" Range: {min_mbps}-{max_mbps} Mbps | Duration: {duration} seconds")
    print(f"{'='*80}\n")

    server_proc = None
    client_proc = None
    rates = []

    try:
        # Start server
        print(f"Starting server with floating rate {min_mbps}-{max_mbps} Mbps...")
        server_proc = subprocess.Popen(
            [sys.executable, "./traffic_masking_server.py",
             "--min-mbps", str(min_mbps),
             "--max-mbps", str(max_mbps),
             "--advanced",
             "--profile", "mixed",
             "--stats-interval", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )

        time.sleep(2)

        # Start client
        print("Starting client...")
        client_proc = subprocess.Popen(
            [sys.executable, "./traffic_masking_client.py",
             "--server", "127.0.0.1",
             "--response", "0.2",
             "--stats-interval", "1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        time.sleep(1)

        # Monitor rates
        print(f"\nMonitoring rates for {duration} seconds...\n")
        start_time = time.time()

        while time.time() - start_time < duration:
            line = server_proc.stdout.readline()
            if line and '[STATS]' in line:
                rate = extract_rate_from_output(line)
                if rate is not None:
                    rates.append(rate)
                    elapsed = int(time.time() - start_time)

                    # Show progress
                    progress = elapsed / duration * 100
                    status = ""
                    if rate <= min_mbps + 0.1:
                        status = "MIN"
                    elif rate >= max_mbps - 0.1:
                        status = "MAX"
                    print(f"[{elapsed:3d}s] Rate: {rate:5.2f} Mbps  Progress: {progress:5.1f}%  {status}", end='\r')

        print("\n")

        # Analyze results
        if rates:
            print(f"\n{'='*80}")
            print(" ANALYSIS RESULTS")
            print(f"{'='*80}\n")

            # Basic statistics
            avg_rate = statistics.mean(rates)
            median_rate = statistics.median(rates)
            min_observed = min(rates)
            max_observed = max(rates)
            std_dev = statistics.stdev(rates) if len(rates) > 1 else 0

            print("📊 BASIC STATISTICS:")
            print(f"  Samples: {len(rates)}")
            print(f"  Average: {avg_rate:.2f} Mbps")
            print(f"  Median: {median_rate:.2f} Mbps")
            print(f"  Min observed: {min_observed:.2f} Mbps")
            print(f"  Max observed: {max_observed:.2f} Mbps")
            print(f"  Std deviation: {std_dev:.2f} Mbps")
            print(f"  Range utilization: {(max_observed - min_observed) / (max_mbps - min_mbps) * 100:.1f}%")

            # Boundary analysis
            visits = analyze_boundary_visits(rates, min_mbps, max_mbps)
            if visits:
                total = sum(visits.values())
                print(f"\n🎯 BOUNDARY VISITS:")
                print(f"  At minimum ({min_mbps:.1f}): {visits['at_min']} ({visits['at_min']/total*100:.1f}%)")
                print(f"  Near minimum: {visits['near_min']} ({visits['near_min']/total*100:.1f}%)")
                print(f"  Middle range: {visits['middle']} ({visits['middle']/total*100:.1f}%)")
                print(f"  Near maximum: {visits['near_max']} ({visits['near_max']/total*100:.1f}%)")
                print(f"  At maximum ({max_mbps:.1f}): {visits['at_max']} ({visits['at_max']/total*100:.1f}%)")

            # Distribution analysis
            distribution = calculate_rate_distribution(rates, min_mbps, max_mbps, bins=10)
            if distribution:
                print(f"\n📈 RATE DISTRIBUTION (10 bins):")
                range_size = max_mbps - min_mbps
                bin_size = range_size / 10

                for i in range(10):
                    bin_start = min_mbps + i * bin_size
                    bin_end = bin_start + bin_size
                    count = distribution.get(i, 0)
                    bar_len = int(count / len(rates) * 40)
                    bar = '█' * bar_len
                    percentage = count / len(rates) * 100
                    print(f"  [{bin_start:4.1f}-{bin_end:4.1f}]: {bar:40} {percentage:5.1f}%")

            # Pattern changes
            changes = detect_pattern_changes(rates)
            print(f"\n🔄 PATTERN CHANGES DETECTED: {len(changes)}")
            if changes[:3]:  # Show first 3 changes
                for change in changes[:3]:
                    print(f"  At {change['time']}s: avg_change={change['avg_change']:.2f}, std_change={change['std_change']:.2f}")

            # Rate graph (last 30 seconds)
            if len(rates) > 30:
                print(f"\n📉 RATE GRAPH (last 30 seconds):")
                graph = visualize_rate_graph(rates[-30:], min_mbps, max_mbps, width=60)
                for line in graph[:35]:  # Show first 35 lines
                    print("  " + line)

            # Quality assessment
            print(f"\n{'='*80}")
            print(" QUALITY ASSESSMENT")
            print(f"{'='*80}\n")

            # Check if pattern is realistic
            boundary_coverage = (visits['at_min'] + visits['at_max'] + visits['near_min'] + visits['near_max']) / total * 100
            range_utilization = (max_observed - min_observed) / (max_mbps - min_mbps) * 100

            quality_score = 0
            quality_notes = []

            if boundary_coverage >= 30:
                quality_score += 25
                quality_notes.append("✅ Good boundary coverage")
            else:
                quality_notes.append("❌ Poor boundary coverage")

            if range_utilization >= 80:
                quality_score += 25
                quality_notes.append("✅ Good range utilization")
            else:
                quality_notes.append("❌ Limited range utilization")

            if len(changes) >= 2:
                quality_score += 25
                quality_notes.append("✅ Dynamic pattern changes")
            else:
                quality_notes.append("❌ Static pattern")

            if std_dev >= (max_mbps - min_mbps) * 0.15:
                quality_score += 25
                quality_notes.append("✅ Good variation")
            else:
                quality_notes.append("❌ Low variation")

            print(f"Quality Score: {quality_score}/100")
            for note in quality_notes:
                print(f"  {note}")

            if quality_score >= 75:
                print(f"\n🎉 EXCELLENT: Traffic pattern is highly realistic!")
            elif quality_score >= 50:
                print(f"\n✅ GOOD: Traffic pattern shows realistic variations")
            else:
                print(f"\n⚠️ NEEDS IMPROVEMENT: Traffic pattern lacks realism")

            return quality_score >= 50

        else:
            print("❌ No rate data collected")
            return False

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cleanup
        if client_proc:
            client_proc.terminate()
            client_proc.wait(timeout=2)
        if server_proc:
            server_proc.terminate()
            server_proc.wait(timeout=2)

def main():
    print("="*80)
    print(" REALISTIC TRAFFIC PATTERN TEST SUITE")
    print("="*80)

    # Test different rate ranges
    test_configs = [
        (1.0, 3.0, 60),   # Narrow range
        (2.0, 10.0, 60),  # Wide range
        (0.5, 2.0, 60),   # Low speed range
    ]

    results = []

    for min_mbps, max_mbps, duration in test_configs:
        print(f"\n\nTest {len(results)+1}: {min_mbps}-{max_mbps} Mbps")
        result = test_realistic_patterns(min_mbps, max_mbps, duration)
        results.append((f"{min_mbps}-{max_mbps} Mbps", result))
        time.sleep(2)

    # Summary
    print("\n" + "="*80)
    print(" TEST SUMMARY")
    print("="*80)

    for config, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"  {config}: {status}")

    passed_count = sum(1 for _, passed in results if passed)
    print(f"\nOverall: {passed_count}/{len(results)} tests passed")

    if passed_count == len(results):
        print("\n🎉 All tests passed! Traffic patterns are realistic.")
    elif passed_count > 0:
        print("\n⚠️ Some tests passed. Review failed configurations.")
    else:
        print("\n❌ All tests failed. Pattern generation needs improvement.")

if __name__ == "__main__":
    main()
