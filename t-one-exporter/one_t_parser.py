#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
TurboFlakes ONE-T: Performance score calculation for CURRENT SESSION ONLY
All parameters are taken from the current session only, no historical data.

Formula from https://github.com/turboflakes/one-t/blob/main/SCORES.md:
performance_score = (1 - mvr) * 0.50 + bar * 0.25 + ((avg_pts - min_avg_pts) / (max_avg_pts - min_avg_pts)) * 0.18 + (pv_sessions / total_sessions) * 0.07

Usage:
  python one_t_current_session_only.py polkadot 5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb
"""

import sys
import json
import requests
from typing import Any, Dict, Optional, Tuple

TIMEOUT = 15
UA = {"User-Agent": "onet-current-session/1.0"}


def compute_current_session_result(network: str, addr: str) -> Dict[str, Any]:
    """
    Compute ONE-T performance score and related metrics for the CURRENT SESSION ONLY.
    Returns a dict with the computed data (without fields: formula, normalization_basis, grade_scale, window).
    """
    try:
        base = f"https://{network}-onet-api.turboflakes.io/api/v1"
    except Exception as e:
        return {
            "ok": False,
            "network": network,
            "address": addr,
            "error": f"Invalid network configuration: {e}",
        }

    # Fetch current validator info
    try:
        st, current, err = jget(f"{base}/validators/{addr}")
        if st != 200 or not current:
            return {
                "ok": False,
                "network": network,
                "address": addr,
                "error": f"Failed to fetch current validator data: {st} {err}",
            }

        current_session = current.get("session")
        is_para = current.get("is_para", False)
    except Exception as e:
        return {
            "ok": False,
            "network": network,
            "address": addr,
            "error": f"Error processing validator data: {e}",
        }
    # Fetch validator profile (identity) from dedicated endpoint
    try:
        stp, prof, perr = jget(f"{base}/validators/{addr}/profile")
        identity_str = ""
        if stp == 200 and prof:
            ident = prof.get("identity")
            if isinstance(ident, dict):
                name = ident.get("name") or ""
                sub = ident.get("sub")
                identity_str = f"{name}/{sub}" if name and sub else (name or "")
            elif isinstance(ident, str):
                identity_str = ident or ""
    except Exception as e:
        identity_str = ""  # Continue with empty identity on error

    try:
        current_points, current_ab_count = extract_points_and_ab(
            current.get("auth", {}) or {}
        )
        current_para_points = calc_para_points(current_points, current_ab_count)
    except Exception as e:
        current_points, current_ab_count, current_para_points = 0, 0, 0

    # Fetch grade for CURRENT SESSION ONLY (number_last_sessions=1)
    grade_url = f"{base}/validators/{addr}/grade?number_last_sessions=1"
    st, grade, err = jget(grade_url)
    if st != 200 or not grade:
        return {
            "ok": False,
            "network": network,
            "address": addr,
            "current_session": current_session,
            "error": f"Failed to fetch grade: {st} {err}",
        }

    # Voting metrics (current session)
    try:
        missed_votes = safe_int(grade.get("missed_votes_total", 0))
        explicit_votes = safe_int(grade.get("explicit_votes_total", 0))
        implicit_votes = safe_int(grade.get("implicit_votes_total", 0))
        total_votes = explicit_votes + implicit_votes + missed_votes

        # MVR
        mvr = (missed_votes / total_votes) if total_votes > 0 else 0.0

        # Bitfields availability metrics (current session)
        bitfields_available = safe_int(grade.get("bitfields_availability_total", 0))
        bitfields_unavailable = safe_int(grade.get("bitfields_unavailability_total", 0))
        bitfields_total = bitfields_available + bitfields_unavailable

        # BAR
        bar = (bitfields_available / bitfields_total) if bitfields_total > 0 else 1.0
    except Exception as e:
        # Set default values on error
        missed_votes, explicit_votes, implicit_votes = 0, 0, 0
        bitfields_available, bitfields_unavailable = 0, 0
        mvr, bar = 0.0, 1.0

    # Points normalization (using para points)
    points_norm = 0.0
    if is_para and current_session:
        try:
            url = f"{base}/validators?session={current_session}&role=para_authority"
            st, data, _ = jget(url)
            if st == 200 and data:
                validators = data.get("data", []) or data.get("validators", [])
                para_pts_values = []
                for v in validators:
                    if not isinstance(v, dict):
                        continue
                    try:
                        auth = v.get("auth", {}) or {}
                        points, ab_count = extract_points_and_ab(auth)
                        para_pts = calc_para_points(points, ab_count)
                        para_pts_values.append(float(para_pts))
                    except Exception:
                        continue  # Skip invalid validator data
                if para_pts_values:
                    min_para_pts = min(para_pts_values)
                    max_para_pts = max(para_pts_values)
                    if max_para_pts > min_para_pts:
                        points_norm = (float(current_para_points) - min_para_pts) / (
                            max_para_pts - min_para_pts
                        )
                    else:
                        # No spread: only zero out points component, keep others
                        points_norm = 0.0
                    points_norm = clamp01(points_norm)
        except Exception as e:
            points_norm = 0.0  # Default to 0 on error

    # PV sessions ratio (current session)
    try:
        pv_ratio = float(grade.get("para_authority_inclusion", 0))
    except (ValueError, TypeError):
        pv_ratio = 0.0

    # Apply performance score formula
    try:
        mvr_component = (1.0 - mvr) * 0.50
        bar_component = bar * 0.25
        points_component = points_norm * 0.18
        pv_component = pv_ratio * 0.07
        performance_score = (
            mvr_component + bar_component + points_component + pv_component
        )
    except Exception as e:
        performance_score = 0.0  # Default score on calculation error

    result = {
        "ok": True,
        "network": network,
        "address": addr,
        "identity": identity_str,
        "current_session": current_session,
        "grade": grade.get("grade"),
        "grade_numeric": grade_to_numeric(grade.get("grade", "N/A")),
        "key_metrics": {
            "missed_votes_total": missed_votes,
            "bitfields_unavailability_total": bitfields_unavailable,
            "explicit_votes": explicit_votes,
            "implicit_votes": implicit_votes,
            "bitfields_availability_total": bitfields_available,
        },
        "components": {
            "mvr": mvr,
            "bar": bar,
            "points_normalized": points_norm,
            "pv_sessions_ratio": pv_ratio,
        },
        "performance_score": performance_score,
        "current_session_details": {
            "points": current_points,
            "authored_blocks_count": current_ab_count,
            "para_points": current_para_points,
        },
    }
    return result


def compute_current_session_results_batch(
    items: list[tuple[str, str]],
) -> list[Dict[str, Any]]:
    """
    Batch compute results for a list of (network, address) tuples.
    Returns a list of dicts (one per validator).
    """
    out: list[Dict[str, Any]] = []
    for network, addr in items:
        try:
            result = compute_current_session_result(
                network.strip().lower(), addr.strip()
            )
            out.append(result)
        except Exception as e:
            # Return error result for this validator
            out.append(
                {
                    "ok": False,
                    "network": network,
                    "address": addr,
                    "error": f"Batch processing error: {e}",
                }
            )
    return out


def jget(url: str) -> Tuple[int, Optional[dict], str]:
    """Fetch JSON from URL."""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
        try:
            data = r.json()
        except Exception:
            data = None
        return r.status_code, data, r.text[:300] if r.text else ""
    except Exception as e:
        return 0, None, str(e)


def clamp01(x: float) -> float:
    """Clamp value between 0 and 1."""
    return max(0.0, min(1.0, x))


def grade_to_numeric(grade: str) -> float:
    """
    Convert letter grade to numeric value (higher is better).

    Possible grades (from best to worst):
    A+ = 10.0 (Excellent - Top performance)
    A  = 9.0  (Excellent)
    A- = 8.0  (Very Good)
    B+ = 7.0  (Good)
    B  = 6.0  (Good)
    B- = 5.0  (Above Average)
    C+ = 4.0  (Average)
    C  = 3.0  (Average)
    C- = 2.0  (Below Average)
    D  = 1.0  (Poor)
    F  = 0.0  (Fail - Very poor performance)
    """
    grade_map = {
        "A+": 10.0,
        "A": 9.0,
        "A-": 8.0,
        "B+": 7.0,
        "B": 6.0,
        "B-": 5.0,
        "C+": 4.0,
        "C": 3.0,
        "C-": 2.0,
        "D": 1.0,
        "F": 0.0,
    }
    return grade_map.get(grade, -1.0)  # Return -1 if grade not recognized


def safe_int(x: Optional[Any], default: int = 0) -> int:
    try:
        if x is None:
            return default
        if isinstance(x, (int, float)):
            return int(x)
        return int(str(x))
    except Exception:
        return default


def extract_points_and_ab(auth: Dict[str, Any]) -> Tuple[int, int]:
    """
    Extract final session points and authored blocks count from 'auth' object.
    - points = ep if present else sp
    - ab is a list of authored blocks => authored_blocks_count = len(ab)
    """
    try:
        if not isinstance(auth, dict):
            return 0, 0
        sp = safe_int(auth.get("sp"), 0)
        ep = auth.get("ep", None)
        points = safe_int(ep, sp)  # use ep if exists, else sp
        ab_list = auth.get("ab") or []
        ab_count = len(ab_list) if isinstance(ab_list, list) else 0
        return points, ab_count
    except Exception:
        return 0, 0


def calc_para_points(points: int, authored_blocks_count: int) -> int:
    """
    para points = max(0, points - 20 * authored_blocks_count)
    Matches ONE-T logic for para points.
    """
    return max(0, points - 20 * authored_blocks_count)


def main():
    if len(sys.argv) != 3:
        print(
            "Usage: python one_t_current_session_only.py <network> <validator_address>",
            file=sys.stderr,
        )
        sys.exit(2)

    network = sys.argv[1].strip().lower()
    addr = sys.argv[2].strip()
    base = f"https://{network}-onet-api.turboflakes.io/api/v1"

    print("=" * 80)
    print("TurboFlakes ONE-T Performance Score - CURRENT SESSION ONLY")
    print("=" * 80)
    print(f"Network: {network}")
    print(f"Address: {addr}")
    print()

    # Use library function to compute and print result (avoids duplicating logic here)
    result = compute_current_session_result(network, addr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
