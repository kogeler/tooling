# Traffic Masking: Architecture Review & Improvement Plan

## Context

The masking system runs as a parallel UDP stream alongside the main encrypted user tunnel inside a multiplexed encrypted transport layer. The external observer sees a single encrypted connection (e.g. QUIC on UDP 443) to a server with a valid TLS certificate. Both the user tunnel and the masking stream are multiplexed into this single connection and are indistinguishable at the packet level.

## Threat Model

The observer cannot read content but can analyze the **aggregate** encrypted stream:
- Throughput shape over time (volume per interval)
- Upload/download ratio
- Session duration and continuity
- Burst patterns and periodicity
- Idle/active transitions

The goal is to make the aggregate stream look like **normal client behavior** (web browsing, media consumption, file downloads) regardless of what the user is actually doing inside the tunnel.

## Current System Assessment

### Architecture

```
Multiplexed encrypted transport:
  ├── User tunnel (UDP) — real user traffic
  └── Masking stream (UDP) — cover traffic from this system
```

The observer sees one encrypted connection. QUIC-level framing already blurs individual packet boundaries. The primary observable is **throughput shape** at second-scale granularity, not individual packet signatures.

### What Works Well

1. **Profile-based generation** (web_browsing, video_streaming, voip, gaming, mixed) — conceptually correct approach for this architecture
2. **Protocol mimicry with session lifecycle** — sessions with start/active/idle/end phases
3. **Floating rate with physics model** — momentum/velocity/acceleration produces organic-looking rate changes
4. **Markov chains per profile** — different statistical distributions per traffic type
5. **Session-level modeling** — not just constant noise, but structured sessions

### What Needs Improvement

The system has **zero awareness of actual user traffic**. The `send_loop()` and `receive_loop()` in the server are completely independent. The generator is self-contained — it does not observe, react to, or compensate for real tunnel traffic.

This is the root cause of all issues below.

---

## Problems To Solve

### Problem 1: Aggregate Profile Can Be Implausible

When the user tunnel carries steady bidirectional traffic and the masking system independently generates its own profile, the aggregate can look like **two overlapping sessions** — abnormal for a single client talking to one server.

The masking system must **complement** the user traffic to form a plausible aggregate, not blindly add on top.

### Problem 2: Upload/Download Ratio Leaks Activity Type

Normal HTTPS clients are heavily download-dominant (~95%+ download). When the user tunnel generates significant upload (e.g. camera feed, file upload, interactive session), the aggregate upload ratio becomes abnormally high.

Current `--response 0.3` (30% upload) **worsens** the problem by adding more upload.

### Problem 3: No Idle Periods

A real client has natural pauses — reading a page, between sessions, overnight. The masking system runs continuously, which is itself a detectable anomaly. No legitimate client generates traffic 24/7 without pauses.

### Problem 4: Steady Throughput Is a Signature

Constant bitrate or predictably oscillating throughput (sine waves, random walks within fixed bounds) is **more suspicious** than what it tries to hide. Real HTTPS traffic is bursty and irregular.

### Problem 5: Burst Events Not Compensated

Periodic burst patterns from user traffic (e.g. keyframe bursts from video codecs every 1-2 sec) are visible in the aggregate throughput. The masking system cannot counteract them because it doesn't know they're happening.

---

## Implementation Plan

### Phase 1: Traffic-Aware Adaptive Mode

**Goal:** The masking system monitors the user tunnel interface and adapts its output to maintain a plausible aggregate profile.

#### 1.1 Tunnel Traffic Monitor

- Poll user tunnel interface statistics (`/sys/class/net/<iface>/statistics/` or `ip -s link`) at ~50-100ms intervals
- Track instantaneous throughput (tx/rx bytes), upload/download ratio, burstiness
- Expose as a shared data structure for the generator to consume

#### 1.2 Activity Classifier

Based on observed tunnel metrics, classify current user activity into categories:

| Observed Pattern | Classification | Masking Strategy |
|---|---|---|
| High steady bidirectional | Interactive/streaming session | Disguise as media consumption — high download bursts (buffering), suppress masking upload |
| Low bursty, download-dominant | Web browsing | Light masking or none — traffic already looks normal |
| High download, low upload | File download / streaming | Minimal masking — already plausible |
| Near-silent | Idle | Generate idle-appropriate background (keepalives, rare small bursts) |
| High upload, low download | Upload session | Disguise as form submission / file upload — add compensating download |

#### 1.3 Aggregate-Aware Rate Control

Replace the current self-contained floating rate with:

```
target_profile = classify(tunnel_throughput)
masking_rate = compute_complement(tunnel_throughput, target_profile)
```

The masking rate is the **difference** between the desired aggregate profile and the actual tunnel throughput, not an independent value.

### Phase 2: Upload Suppression & Ratio Control

**Goal:** Keep aggregate upload/download ratio within normal HTTPS bounds (3-8% upload).

- Calculate real-time aggregate ratio: `(tunnel_upload + masking_upload) / (tunnel_download + masking_download)`
- If ratio > threshold: reduce masking upload, increase masking download
- If ratio < threshold: slightly increase masking upload (rare — most scenarios are upload-heavy)
- Inverse response ratio: when tunnel upload is high, masking upload should be near-zero

### Phase 3: Realistic Session Modeling

**Goal:** The aggregate stream should have human-like activity patterns with natural idle periods.

#### 3.1 Session Scheduler

- Generate realistic "browsing sessions" (5-30 min active, 1-10 min idle)
- During idle periods: only keepalives and minimal background traffic
- **Critical constraint:** If user tunnel is active during a scheduled idle period, generate enough "background" traffic to prevent tunnel traffic from being exposed as the only activity. The "idle" must look like the client is still connected but doing light background work.

#### 3.2 Diurnal Patterns

- Optional time-of-day awareness: less traffic at night, peaks during day
- Configurable timezone and activity profile
- Long idle periods (sleep hours) where only keepalives flow

#### 3.3 Session Transitions

- Smooth transitions between activity levels (not instant jumps)
- Realistic ramp-up (page load burst → steady reading) and ramp-down (gradual disengagement)

### Phase 4: Aggregate Validation

**Goal:** Continuously validate that the aggregate stream matches expected statistical properties of legitimate traffic.

#### 4.1 Aggregate Statistics Collector

- Compute sliding-window statistics on the **aggregate** (tunnel + masking):
  - Throughput mean, variance, autocorrelation (1-sec, 10-sec, 60-sec windows)
  - Upload/download ratio
  - Burst frequency and amplitude
  - Idle period distribution

#### 4.2 Profile Comparator

- Compare aggregate statistics against reference profiles of legitimate traffic
- If deviation exceeds threshold: adjust masking parameters in real-time
- Reference profiles should be derived from real traffic captures (web browsing, video streaming, file downloads)

#### 4.3 ML Resistance on Aggregate (not individual packets)

- Move the existing ML resistance logic from per-packet to per-aggregate level
- The adversarial features should target aggregate throughput shape, not individual packet sizes
- Since the encrypted transport already blurs packet-level features, focus ML resistance entirely on volume/timing analysis

### Phase 5: Anti-Burst Compensation

**Goal:** Smooth out periodic burst patterns from user traffic that leak through to the aggregate.

- When tunnel throughput spikes: optionally reduce masking rate slightly (so aggregate spike is dampened)
- When tunnel throughput dips: increase masking to fill the gap
- This is NOT constant bitrate — the aggregate still varies, but the **variance introduced by user traffic** is partially absorbed
- Tunable aggressiveness: full compensation (more constant, slightly suspicious) vs. partial compensation (more natural, less protection)

---

## Implementation Notes

### Interface Monitoring

The tunnel interface name should be configurable (`--tunnel-iface`). Polling via sysfs is lightweight (~0 CPU cost). Fallback to `psutil` or socket-level monitoring if sysfs is unavailable.

### Backward Compatibility

- Current standalone mode (no tunnel awareness) should remain as `--mode standalone`
- New adaptive mode activated via `--mode adaptive --tunnel-iface <name>`
- All existing profiles and generation logic are reused as building blocks

### Performance Considerations

- Tunnel monitoring at 50-100ms granularity adds negligible overhead
- Activity classification should be lightweight (threshold-based, not ML)
- Aggregate validation can run at 1-sec intervals (not per-packet)

### Testing Strategy

- Unit tests: activity classifier with synthetic throughput traces
- Integration tests: run masking alongside simulated tunnel traffic, validate aggregate statistics
- Capture real traffic profiles (web browsing, streaming) as reference baselines for validation
- Compare aggregate statistical fingerprint with and without masking enabled

---

## Priority Order

1. **Phase 1** (Traffic-Aware Mode) — without this, everything else is cosmetic
2. **Phase 2** (Upload Suppression) — most detectable anomaly after awareness
3. **Phase 3** (Session Modeling) — prevents long-running constant-traffic detection
4. **Phase 5** (Anti-Burst) — smooths out the most obvious leaks
5. **Phase 4** (Aggregate Validation) — continuous quality assurance layer
