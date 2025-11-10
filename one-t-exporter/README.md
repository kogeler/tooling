# ONE-T Prometheus Exporter

Prometheus exporter for TurboFlakes ONE-T validator performance metrics.

## Changelog

### v1.0.5
- fix the wrong name of the exporter

### v1.0.4
- Added `ONE_T_ENV` environment variable support
- Added `env` label to all metrics for environment segregation

### v1.0.3
- Initial release

## Overview

This exporter collects performance metrics for Polkadot/Kusama validators from the TurboFlakes ONE-T API and exposes them in Prometheus format.

**Source Repository**: https://github.com/turboflakes/one-t  
**API Endpoint**: `https://{network}-onet-api.turboflakes.io/api/v1`

## Metrics

### Performance Score Metrics

- `one_t_performance_score` (gauge): **Overall Performance Score** (0.0-1.0)
  - Composite score calculated as: `(1 - MVR) * 0.50 + BAR * 0.25 + normalized_points * 0.18 + PV_ratio * 0.07`
  - Represents overall validator health and reliability
  - Higher values indicate better performance and lower slashing risk

- `one_t_grade_numeric` (gauge): **Numeric Grade Value** (-1.0 to 10.0)
  - Converts letter grade to numeric scale for current session performance
  - 10.0 = A+ (Excellent), 9.0 = A, 8.0 = A-, 7.0 = B+, 6.0 = B, 5.0 = B-, 4.0 = C+, 3.0 = C, 2.0 = C-, 1.0 = D, 0.0 = F (Fail)
  - Returns -1.0 if grade cannot be determined

### Voting Performance Metrics

- `one_t_mvr` (gauge): **Missed Vote Ratio** (0.0-1.0)
  - Ratio of GRANDPA finality votes missed by the validator in the current session
  - Critical metric: High MVR (above 0.1) can lead to slashing
  - Formula: `missed_votes / (explicit_votes + implicit_votes + missed_votes)`
  - Lower values indicate better voting reliability

- `one_t_missed_votes` (gauge): **Missed Votes in Current Session**
  - Absolute count of missed votes in the current session
  - Updated to the current session value on each collection
  - Resets when a new session begins

- `one_t_explicit_votes` (gauge): **Explicit Votes in Current Session**
  - Absolute count of explicit GRANDPA votes in the current session
  - Votes where validator actively cast a vote in the finality process
  - Shows current session participation in consensus

- `one_t_implicit_votes` (gauge): **Implicit Votes in Current Session**
  - Absolute count of implicit GRANDPA votes in the current session
  - Votes where validator's agreement was implicit (followed the supermajority)
  - Normal part of GRANDPA consensus when validator agrees with majority

### Availability Metrics

- `one_t_bar` (gauge): **Bitfields Availability Ratio** (0.0-1.0)
  - Ratio of availability bitfields successfully submitted in the current session
  - Critical for parachain data availability and erasure coding chunks distribution
  - Formula: `available_bitfields / (available_bitfields + unavailable_bitfields)`
  - Values below 0.9 may indicate connectivity issues

- `one_t_bitfields_availability` (gauge): **Available Bitfields in Current Session**
  - Absolute count of successful bitfield submissions in the current session
  - Bitfields confirm parachain block data availability
  - Part of the availability distribution scheme

- `one_t_bitfields_unavailability` (gauge): **Unavailable Bitfields in Current Session**
  - Absolute count of missed bitfield submissions in the current session
  - High values indicate potential network, CPU, or storage issues
  - Should be significantly lower than available bitfields

### Session Performance Metrics

- `one_t_points_normalized` (gauge): **Normalized Points Component** (0.0-1.0)
  - Session points normalized against other para-validators in the same session
  - Formula: `(validator_para_points - min_para_points) / (max_para_points - min_para_points)`
  - Shows relative performance compared to peers

- `one_t_pv_sessions_ratio` (gauge): **Para-Validator Inclusion Ratio** (0.0-1.0)
  - Current session's para-validator inclusion rate
  - Shows if validator is actively selected for parachain validation duties
  - Value of 1.0 means validator is a para-validator in current session

- `one_t_points` (gauge): **Session Points**
  - Current session's total points
  - Points from block production (20 per block) and parachain validation
  - Updated to current session value on each collection

- `one_t_authored_blocks_count` (gauge): **Authored Blocks in Current Session**
  - Number of blocks authored in the current session
  - Blocks produced by validator as BABE block author
  - Each block awards 20 points in the session

- `one_t_para_points` (gauge): **Para Points in Current Session**
  - Para-validation points in the current session
  - Calculated as: `session_points - (20 * authored_blocks_count)`
  - Represents points earned specifically from parachain validation duties

### System Metrics

- `one_t_errors` (counter): **Total Collection Errors**
  - Cumulative count of errors encountered during metric collection
  - Monitors exporter health and API connectivity

**Labels**: All metrics include `network`, `address`, `identity`, and `env` labelsidentity` labels.

## Health Check

The exporter provides a health check endpoint on port `ONE_T_PORT + 1` (default: 8001):

- **Endpoint**: `http://localhost:8001/health`
- **Healthy (200 OK)**: After first successful collection of all validators
- **Unhealthy (503 Service Unavailable)**: 
  - No successful collection yet
  - Any errors during last collection
  - No validators configured
- **Response**: Plain text status message with validator count

The health check becomes green after the first successful collection and turns red if any errors occur. It returns to green once collection succeeds again.

## Quick Start

```bash
# Set up environment
export ONE_T_VAL_1="5C5cD4LaiSwqFwxUWRWfNMKLYctDH5bPkkstGNQGzYYaPtgb"
export ONE_T_VAL_NETWORK_1="polkadot"
export ONE_T_PORT=8000
export ONE_T_COLLECT_PERIOD=60

# Run exporter
python one_t_exporter.py
```

## Configuration

### Environment Variables

- `ONE_T_PORT`: Metrics exporter port (default: 8000, health check on 8001)
- `ONE_T_COLLECT_PERIOD`: Collection interval in seconds (default: 60)
- `ONE_T_LOG_LEVEL`: Log level (DEBUG, INFO, WARNING, ERROR)
- `ONE_T_VAL_{N}`: Validator address for index N
- `ONE_T_VAL_NETWORK_{N}`: Network for validator N (polkadot, kusama, westend, paseo)

### Multiple Validators

```bash
export ONE_T_VAL_1="validator_address_1"
export ONE_T_VAL_NETWORK_1="polkadot"
export ONE_T_VAL_2="validator_address_2" 
export ONE_T_VAL_NETWORK_2="kusama"
```

## Usage

- **Metrics endpoint**: `http://localhost:8000/metrics`
- **Health check endpoint**: `http://localhost:8001/health`

Example health check:
```bash
curl http://localhost:8001/health
# Returns: OK - 1/1 validators healthy (200 OK)
# Or: UNHEALTHY - API error: Failed to fetch (503 Service Unavailable)
```

## Metric Importance

### Critical for Slashing Prevention
- **MVR (Missed Vote Ratio)**: Values above 0.1 (10%) increase slashing risk
- **BAR (Bitfields Availability Ratio)**: Values below 0.9 (90%) may indicate issues
- **Note**: All session metrics show absolute values from the current session

### Performance Indicators
- **Performance Score**: Overall health and reliability
- **Grade**: Quick assessment of validator quality
- **Points**: Direct correlation with rewards

### Operational Health
- **Error Count**: Monitors exporter and API connectivity
- **Session Metrics**: Tracks validator participation and rewards

These metrics provide comprehensive visibility into validator performance, helping operators maintain optimal node health and avoid slashing risks.