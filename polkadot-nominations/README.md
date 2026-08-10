# polkadot-nominations

A strictly typed TypeScript CLI that fetches nominations, self-stake
statistics, and validator information from Polkadot Asset Hub.

## Runtime requirements

- GNU Make
- Podman configured in rootless mode

Running `node`, `npm`, or `npx` directly on the host is prohibited. The CLI also
checks that it is running in a container. All supported commands go through the
Makefile, which:

1. verifies that Podman is running rootless;
2. starts an ephemeral, digest-pinned `node:22-bookworm-slim` container;
3. streams a filtered tar archive of the project into container tmpfs without
   mounting any host path;
4. installs npm dependencies there;
5. creates `.papi/polkadot-api.json`, downloads metadata, and generates PAPI
   descriptors there;
6. compiles the TypeScript sources into container-only `dist/`;
7. removes the container and all generated dependencies after the command.

No source or config directory is exposed as a container mount. No
`node_modules`, `dist`, or generated `.papi` files are written to the host.

### Container isolation

The Makefile applies the same hardened profile to CLI runs, tests, and lock-file
generation:

- the process runs as the invoking non-root UID in a private user namespace;
- all Linux capabilities are dropped and `no-new-privileges` is enabled;
- the image root filesystem is read-only, with size-limited `/work` and `/tmp`
  tmpfs mounts as the only application-writable locations;
- PID, IPC, UTS, cgroup, and network namespaces are private, no ports are
  published, and host aliases are omitted from `/etc/hosts`;
- CPU, memory, swap, and PID counts are limited;
- npm lifecycle scripts are disabled, including npm commands started by PAPI;
- Podman log persistence is disabled; command output remains attached to the
  invoking terminal.

Outbound networking remains enabled because dependency installation, PAPI
metadata generation, and live RPC queries require it. `make package-lock` is
the only target that intentionally changes a project file: it receives the new
lock-file over container stdout and atomically replaces `package-lock.json` on
the host. `make run ARGS="..."` accepts exactly one known CLI flag so its value
cannot become an arbitrary host-shell command.

## Configuration

Create the local configuration if it does not exist:

```bash
cp config.json.example config.json
```

```json
{
  "validators": [
    "1FirstValidatorAddress____________________________",
    "1SecondValidatorAddress___________________________"
  ],
  "percentiles": [0.25, 0.5, 0.75, 0.9],
  "minStakeDot": 1000,
  "rewardEras": 20,
  "rpcUrl": "wss://rpc-assethub.novasama-tech.org"
}
```

| Field | Description |
|---|---|
| `validators` | Non-empty list of validator SS58 addresses |
| `percentiles` | Percentile thresholds in the inclusive `0..1` range |
| `minStakeDot` | Minimum nominator stake included by `--nominations` |
| `rewardEras` | Completed eras included by `--validator-info` |
| `rpcUrl` | Asset Hub WebSocket RPC; defaults to the URL shown above |

The `RPC_URL` environment variable overrides `rpcUrl` for a single command:

```bash
RPC_URL=wss://other-node.example.com make nominations
```

## Commands

```bash
make nominations
make self-stake-stats
make validator-info
```

The script writes JSON to stdout and operational logs to stderr. Redirecting a
report therefore produces a clean JSON file:

```bash
make nominations > nominations.json
```

`make nominations` returns a map keyed by each address from `validators`:

```json
{
  "1Validator...": {
    "total_nominators": 212,
    "unique_targets": 352,
    "commission_p50": 3,
    "stake_p50": 2236,
    "nominations": []
  }
}
```

`make self-stake-stats` analyzes every registered validator, not only configured
addresses. It reports the percentages of active and all validators meeting the
10,000 DOT minimum. Reward-effective percentiles include every validator meeting
that minimum. Self-stake above 30,000 DOT is capped to 30,000 for percentile
calculation rather than excluded, because the network top-up no longer improves
the reward percentage above that point. The validator still remains part of the
competitive distribution and still earns on its actual own stake.

```json
{
  "minimum_self_stake_dot": 10000,
  "active_validators": {
    "total": 297,
    "meeting_minimum": 240,
    "meeting_minimum_pct": 80.81
  },
  "all_validators": {
    "total": 1300,
    "meeting_minimum": 500,
    "meeting_minimum_pct": 38.46
  },
  "reward_effective_self_stake": {
    "minimum_dot": 10000,
    "cap_dot": 30000,
    "validator_count": 500,
    "capped_validator_count": 200,
    "self_stake_p25": 15000,
    "self_stake_p50": 25000,
    "self_stake_p75": 30000,
    "self_stake_p90": 30000
  }
}
```

`make validator-info` reports current self-stake, actual historical personal
rewards, and what-if rewards for higher self-stake values. The scenario range
ends at the current on-chain `HardCapSelfStake` (`C`), not at a locally fixed
limit. Targets advance by 2,500 DOT through the current `OptimumSelfStake`
(currently 30,000 DOT), then by 10,000 DOT through `C`; exact optimum and `C`
points are always included even when a step does not land on them.

```json
{
  "1Validator...": {
    "self_stake": 10000,
    "rewards": {
      "eras_requested": 20,
      "total_reward": 829.848,
      "unclaimed_reward": 41.4924,
      "eras": [
        {
          "era": 2256,
          "reward": 41.4924,
          "commission_reward": 0.7063,
          "own_stake_reward": 0,
          "validator_incentive_reward": 40.7861
        }
      ]
    },
    "reward_projection": {
      "basis": {
        "eras_requested": 20,
        "eras_used": 20,
        "current_self_stake": 10000,
        "actual_total_reward": 829.848,
        "actual_average_reward_per_era": 41.4924
      },
      "curve": {
        "minimum_self_stake": 10000,
        "optimum_self_stake": 30000,
        "hard_cap_self_stake": 100000,
        "slope_factor": 0.5
      },
      "scenarios": [
        {
          "self_stake": 12500,
          "additional_self_stake": 2500,
          "estimated_total_reward": 927.12,
          "estimated_average_reward_per_era": 46.356,
          "estimated_total_commission_reward": 14.126,
          "estimated_total_own_stake_reward": 43.22,
          "estimated_total_validator_incentive_reward": 869.774,
          "increase_pct": 11.7217
        }
      ]
    }
  }
}
```

`basis.actual_total_reward` and every scenario cover only completed active eras
in the requested window that contain validator-incentive data. The comparison
is independent for each configured validator and uses that validator's current
ledger self-stake. A scenario changes only its self-stake: historical reward
points, commission, nominator stake, incentive budget, and all other validator
weights remain fixed. `increase_pct` is the estimated percentage change from
the matching `basis.actual_total_reward`; it is `null` when that baseline is
zero. These estimates are intended for comparison and do not predict election,
future era points, commission changes, or nominator movement.

## Tests

Run the strict TypeScript compiler without emitting files:

```bash
make typecheck
```

The project uses TypeScript 7 with `strict`, `noUncheckedIndexedAccess`, and
`exactOptionalPropertyTypes`. The checked source includes production code,
unit tests, RPC integration tests, and live tests.

Run unit tests and the lightweight RPC integration test together:

```bash
make test
```

The integration test connects to the configured Asset Hub RPC, loads the
current active set, randomly selects one active validator, and verifies typed
validator preferences, live curve constants, and latest completed-era incentive
storage. It contains no production validator addresses.

Run the separate full live suite:

```bash
make test-live
```

The live suite loads the current active set over RPC, randomly samples three
validators, writes a temporary container-only config, and executes all CLI
modes. It validates nomination maps, global self-stake statistics, and validator
information against live data. It does not use or hardcode the validator list
from the local `config.json`.

All validation targets install dependencies, generate PAPI files, compile the
TypeScript project, and execute Node.js only inside an ephemeral Podman
container.

## Maintenance

After changing dependency versions in `package.json`, regenerate the lock-file
inside Podman:

```bash
make package-lock
```

This target installs no dependencies on the host and replaces only
`package-lock.json`.
