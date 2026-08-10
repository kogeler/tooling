# polkadot-nominations — Agent Context

## Project Overview

A container-only, strictly typed TypeScript CLI with separate modes for
nomination reports, network-wide self-stake statistics, and per-validator
information on Polkadot Asset Hub. It uses
[polkadot-api (PAPI)](https://papi.how/) for typed queries.

### Mandatory Container-Only Rule

**Never run `node`, `npm`, `npx`, or any JavaScript test directly on the host.**
This applies to agents, maintainers, CI commands, live runs, dependency updates,
PAPI codegen, and tests. Use only the Podman-backed Make targets:

- `make nominations`
- `make self-stake-stats`
- `make validator-info`
- `make typecheck`
- `make test`
- `make test-live`
- `make package-lock`
- `make run ARGS="--help"`

No host path is mounted into the container. A filtered project archive is
streamed over stdin and extracted into container tmpfs. Dependencies, compiler
output, metadata, `.papi/polkadot-api.json`, and generated descriptors must
remain inside the ephemeral container. The CLI also rejects direct host
execution.

Rootless Podman is mandatory and enforced by `check-podman`. Every container
runs under `--userns=keep-id` with all capabilities dropped,
`no-new-privileges`, a read-only root filesystem, private namespaces, no host
aliases or published ports, resource limits, and only size-limited `/work` and
`/tmp` tmpfs mounts. Do not weaken this profile or add a host bind mount without
an explicit requirement and security review.

`make run` validates `ARGS` against the fixed CLI flag allowlist before building
the shell recipe. Keep user-controlled values out of generated host-shell text;
pass runtime values such as `CONFIG_PATH` and `RPC_URL` through Podman
environment handling.

## Current Implementation

### Architecture

```
polkadot-nominations/
  index.ts              Container guard and CLI command orchestration
  Makefile              Ephemeral Podman runs, tests, and lock-file generation
  config.json           Local config (validators, percentiles, filters, RPC)
  package.json          ES modules, scripts, and pinned dependency ranges
  tsconfig.json         Strict TypeScript 7 compiler configuration
  src/
    analysis.ts         Pure nomination/self-stake/validator-info report builders
    chain.ts            RPC connection and data fetching (validators, nominators, ledgers)
    cli.ts              CLI flag parsing and help text
    config-schema.ts    Pure unknown-to-Config validation and defaults
    config.ts           Runtime config.json loader
    constants.ts        DOT units and default Asset Hub RPC URL
    rewards.ts          Actual personal payouts and self-stake reward projections
    stats.ts            Percentile calculation with configurable direction and prefix
  test/
    *.test.ts           Deterministic unit tests with no live chain dependency
    fixtures/           Tracked RPC-only config used to bootstrap test containers
    integration/        Lightweight real-RPC typed API checks
    live/               Full CLI checks using random current active validators
  dist/                 Container-only compiler output; never written to the host
  .gitignore            Excludes node_modules, all generated .papi data, local config
  README.md             User-facing documentation
```

### What It Does

The CLI requires exactly one mode:

1. `--nominations` fetches nominations for every configured address, filters by
   `minStakeDot`, enriches targets with commission, and computes commission and
   nominator-stake percentiles. Output is keyed by validator address.
2. `--self-stake-stats` scans every registered validator. It reports the share
   of active and all validators with at least 10,000 DOT self-stake. Percentiles
   include all validators meeting that minimum, with values above 30,000 DOT
   capped to 30,000 rather than excluded.
3. `--validator-info` emits a validator-address-keyed map with current self-stake,
   historical personal rewards including the validator incentive, and independent
   higher-self-stake reward scenarios through the live runtime hard cap.

All modes emit JSON to stdout and operational logs to stderr. They fetch only
the chain datasets needed by the selected mode, with independent queries run in
parallel via `Promise.all`.

### Test Suites

- `make typecheck` compiles all production and test TypeScript with no output.
- `make test` runs all deterministic unit tests and the RPC integration suite in
  one ephemeral container. The integration test loads the current active set,
  randomly chooses one address from it, and verifies typed validator
  preferences, live reward-curve constants, and completed-era incentive data.
- `make test-live` is separate because it performs full storage scans and runs
  every CLI mode end to end. It loads the current active set, randomly samples
  three addresses, creates a temporary config inside container tmpfs, and checks
  `--nominations`, `--self-stake-stats`, and `--validator-info`.
- Live tests must never embed or copy the validator addresses from the local
  `config.json`. Validator samples must always be derived at runtime from the
  current active set returned by RPC.
- Both suites require RPC access. Neither suite may write `node_modules`, `.papi`
  data, generated descriptors, or temporary configs to the host.

### Key Technical Details

- **Runtime**: Node.js >= 22 (ES modules)
- **Container image**: the multi-architecture `node:22-bookworm-slim` OCI index
  is pinned by digest in `Makefile`; dependency and image updates must update
  this digest deliberately
- **Language/compiler**: TypeScript `^7.0.2`; `strict`,
  `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitReturns`,
  and `verbatimModuleSyntax` are enabled. Source imports use `.js` extensions as
  required by NodeNext ESM and compile into container-only `dist/`.
- **Chain**: Polkadot Asset Hub (staking migrated from relay chain as of runtime v2.0.0)
- **RPC library**: `polkadot-api` (PAPI) `^2.2.2` with generated type descriptors. The ws provider is imported from `polkadot-api/ws`
- **WebSocket**: Uses `ws` npm package passed as `websocketClass` to `getWsProvider` (Node 22 has native WebSocket but PAPI needs the class passed explicitly)
- **Commission format**: Stored on-chain as Perbill (0..1,000,000,000), converted to % with 4 decimal precision
- **Stake format**: Stored as Planck (`bigint`), converted to DOT with four decimal places without truncating the fractional part
- **RPC URL**: Read from `config.json.rpcUrl`, defaulting to `wss://rpc-assethub.novasama-tech.org`; `RPC_URL` overrides it
- **PAPI codegen**: each Make run executes `npm ci --ignore-scripts`, then the lock-resolved `./node_modules/.bin/papi add ah -w <rpcUrl>` inside container tmpfs. `npm_config_ignore_scripts=true` also applies to npm started internally by PAPI. No `.papi` file is sourced from or written to the host. PAPI's `tsc-prog@2.3.0` is moved under `@polkadot-api/cli/node_modules` before codegen so it resolves the CLI's TypeScript 6 dependency; project builds still use root TypeScript 7.
- **Host isolation**: project files are copied through a filtered stdin tar stream,
  never through a bind mount. The container is rootless/non-root, has zero Linux
  capabilities, cannot gain privileges, has a read-only root filesystem and
  private namespaces, and receives explicit CPU, memory, swap, and PID limits.
  Only `make package-lock` intentionally writes one host file from stdout.
- **Lock maintenance**: `make package-lock` runs `npm install --package-lock-only --ignore-scripts` in Podman and replaces only the host `package-lock.json`

### config.json Schema

```json
{
  "validators": ["SS58 address", "another SS58 address"],
  "percentiles": [0.25, 0.5, 0.75, 0.9],
  "minStakeDot": 0,
  "rewardEras": 20,
  "rpcUrl": "wss://rpc-assethub.novasama-tech.org"
}
```

- `validators` — required non-empty unique list used by address-specific modes
- `percentiles` — optional, array of thresholds (0-1), default `[0.5, 0.75, 0.9]`
- `minStakeDot` — optional, minimum active stake in whole DOT, default `0` (disabled)
- `rewardEras` — optional, number of most recent completed eras to report validator rewards for, default `20`
- `rpcUrl` — optional non-empty WebSocket URL; defaults to the current Asset Hub RPC above

### Output Structure

Address-specific reports are maps keyed by the exact configured address.

`--nominations`:

```json
{
  "SS58...": {
    "total_nominators": 212,
    "unique_targets": 352,
    "commission_p50": 3,
    "stake_p50": 2236,
    "nominations": [
      {
        "nominator": "SS58...",
        "stake": 6717,
        "targets": [{ "address": "SS58...", "commission": 4 }],
        "commission_p50": 3,
        "submitted_in": 1738,
        "suppressed": false
      }
    ]
  }
}
```

`--self-stake-stats`:

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
    "self_stake_p50": 25000,
    "self_stake_p90": 30000
  }
}
```

`reward_effective_self_stake` models the network top-up competition. Every
registered validator with at least 10,000 DOT remains in the percentile sample.
For this calculation only, `min(actual_self_stake, 30_000)` is used. A validator
above 30,000 DOT therefore still affects the distribution as a 30,000 DOT
validator; it is not removed. This cap does not change its actual self-stake or
the reward earned directly on that own stake.

`--validator-info`:

```json
{
  "SS58...": {
    "self_stake": 10000,
    "rewards": {
      "eras_requested": 20,
      "active_era": 2257,
      "is_validator": true,
      "current_commission_pct": 0,
      "latest_era_commission_pct": 10,
      "reward_collapse_pending": false,
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

The scenario grid is derived from live runtime constants. It advances by 2,500
DOT through `OptimumSelfStake` (currently 30,000 DOT), then by 10,000 DOT up to
the current `HardCapSelfStake` (`C`). Exact optimum and hard-cap values are
always included. Only targets strictly above the validator's current ledger
self-stake are emitted, so a validator already at or above `C` has no scenarios.

Each validator is modeled independently against its own actual reward history.
Only completed active eras with incentive data participate in `basis` and the
projection. Historical reward points, commission, nominator stake, incentive
budget, and other validators' weights are held fixed; the selected validator's
self-stake, exposure share, curve weight, and resulting total incentive weight
are recomputed. `increase_pct` compares the scenario total with
`basis.actual_total_reward` and is `null` for a zero baseline. The projection
does not model elections or future changes in points, commissions, nominators,
budgets, or competitors.

### Chain Storage Items Used

| Storage | Key | Value | Used For |
|---|---|---|---|
| `Staking.Validators` | SS58String | `{ commission: Perbill, blocked: bool }` | Validator commission lookup |
| `Staking.Nominators` | SS58String | `{ targets: SS58String[], submitted_in: u32, suppressed: bool }` | Nominator -> validator mappings |
| `Staking.Ledger` | SS58String | `{ stash: SS58String, total: u128, active: u128, unlocking: [...] }` | Active stake per nominator |
| `Staking.ActiveEra` | — | `{ index: u32, start?: u64 }` | Current era (rewards look back from `index - 1`) |
| `Staking.ErasValidatorReward` | EraIndex | `u128` | Total payout minted for the whole era |
| `Staking.ErasRewardPoints` | EraIndex | `{ total: u32, individual: [SS58, u32][] }` | Era reward points, per validator |
| `Staking.ErasValidatorPrefs` | (EraIndex, SS58) | `{ commission: Perbill, blocked: bool }` | Per-era commission |
| `Staking.ErasStakersOverview` | (EraIndex, SS58) | `{ total: u128, own: u128, nominator_count: u32, page_count: u32 }` | Per-era exposure (own/total, page count) |
| `Staking.ClaimedRewards` | (EraIndex, SS58) | `u32[]` | Claimed page indices for the era |
| `Staking.ErasValidatorIncentiveBudget` | EraIndex | `u128` | Separate validator incentive pool for an era |
| `Staking.ErasValidatorIncentiveWeight` | (EraIndex, SS58) | `u128` | Snapshotted validator incentive weight |
| `Staking.ErasSumValidatorIncentiveWeight` | EraIndex | `u128` | Sum of snapshotted incentive weights |
| `Staking.MinValidatorBond` | — | `u128` | Live minimum self-stake and first scenario target |
| `Staking.OptimumSelfStake` | — | `u128` | Curve transition and scenario step boundary |
| `Staking.HardCapSelfStake` | — | `u128` | Live upper scenario boundary `C` |
| `Staking.SelfStakeSlopeFactor` | — | `Perbill` | Dampening factor above optimum self-stake |

Full storage scans are selected per command rather than always fetched together.
Reward items are read via `.getValue()` for each configured validator across the
last `rewardEras` completed eras.

### Reward Computation (src/rewards.ts)

Replicates `pallet_staking::do_payout_stakers_by_page`. The **validator's own** reward for an era is:

```
validator_total_payout = ErasValidatorReward(era) * validator_points / total_points
commission_payout      = commission * validator_total_payout          // Perbill
leftover               = validator_total_payout - commission_payout
own_stake_reward       = leftover * overview.own / overview.total
validator_incentive    = incentive_budget * validator_weight / sum_weights
personal_reward        = commission_payout + own_stake_reward + validator_incentive
```

Nominator payouts are intentionally excluded. Arithmetic is done in `BigInt` Planck to match on-chain integer math, then converted to DOT.

The validator incentive curve uses the live `OptimumSelfStake` (`T`),
`HardCapSelfStake` (`C`), and `SelfStakeSlopeFactor` (`k`):

```
weight(s) = sqrt(s)                         when s <= T
weight(s) = sqrt(T + k^2 * (s - T))         when T < s <= C
weight(s) = weight(C)                       when s > C
```

The separate incentive is paid only to validators with nonzero era reward
points. The what-if calculation replaces the selected validator's snapshotted
weight in the historical total with `weight(target_self_stake)` before dividing
the historical incentive budget.

**Claimed flag:** the own-stake reward is paid only with page `0`; commission is spread across all pages. A reward is therefore *fully* claimed only when `ClaimedRewards(era, validator).length == overview.page_count`. Most validators have a single page, so this is effectively binary.

**Migration boundary:** eras predating the Asset Hub staking migration (and the not-yet-finalized current era) have no `ErasValidatorReward`, so they are reported with `has_data: false`, `active: false`, `reward: 0`. Requesting more `rewardEras` than exist since the migration is safe.

**Commission-change detection:** per-era commission comes from `ErasValidatorPrefs`, which is snapshotted at era start. A live commission change only shows in `current_commission_pct` (from `Staking.Validators`) until the next era snapshots it. `reward_collapse_pending` is `true` only when the latest reward is commission-only, with neither own-stake nor incentive income, and the current commission is lower than the snapshot.

### Typical Data Volumes (Polkadot, Feb 2025)

- ~1,300 registered validators
- ~29,500 nominators
- ~54,000 staking ledgers
- Fetch time: ~30-60s depending on RPC node

---

## Election Prediction Research

### Goal

Predict which validators would be elected to the active set if an election happened now, using on-chain data.

### Algorithm Background

Polkadot uses **Nominated Proof-of-Stake (NPoS)** with the following election algorithms:

- **Sequential Phragmen** (seq-phragmen) — the default, linear time complexity O(m * |E|) where m = validators to elect, |E| = nomination edges
- **PhragMMS** — newer alternative by W3F, provides constant-factor approximation for maximin support, ~10x slower

The system is flexible: `pallet_election_provider_multi_phase` accepts solutions computed off-chain by "staking miners" who can use either algorithm. Solutions are verified on-chain.

### Algorithm Inputs

All available from chain storage (we already fetch most of this):

```
voters:           Vec<(AccountId, VoteWeight, Vec<AccountId>)>  — nominators with stake and targets
targets:          Vec<AccountId>                                — validator candidates
desired_targets:  usize                                         — active set size (~297 on Polkadot)
```

### Algorithm Outputs

```
winners:      Vec<(AccountId, Support)>      — elected validators with total backing
assignments:  Vec<Assignment<AccountId>>     — stake distribution from each nominator to winners
score:        ElectionScore                  — minimal_stake, sum_stake, sum_stake_squared
```

### Reference Implementation

- **Crate**: `sp-npos-elections` in [polkadot-sdk](https://github.com/paritytech/polkadot-sdk) monorepo
- **Path**: `substrate/primitives/npos-elections/`
- **Docs**: https://paritytech.github.io/polkadot-sdk/master/sp_npos_elections/index.html
- **Key modules**: `phragmen` (seq-phragmen), `phragmms`, `balancing` (star balancing post-processing)

### Existing JS/TS Implementations

**None exist.** No npm packages implement Phragmen or PhragMMS. All computation is done in Rust.

### JS Reimplementation Feasibility

| Factor | Assessment |
|---|---|
| Algorithm complexity | ~1000+ lines of Rust, seq-phragmen is linear time |
| Performance | JS significantly slower than Rust, but ~300 validators / ~30k nominators is manageable |
| Arithmetic precision | Requires careful fixed-point math (Rust uses `Rational128`) |
| Testing | Must cross-validate against Rust reference output |
| Maintenance burden | Algorithm evolves (PhragMMS was added later), need to track upstream |

**Verdict**: Feasible for prototyping, not recommended for production.

### polkadot-staking-miner — Deep Dive

The official Rust CLI tool by Parity for computing and submitting NPoS election solutions.

#### Project Status (as of Feb 2026)

- **Repository**: https://github.com/paritytech/polkadot-staking-miner
- **Status**: Actively maintained, last commit Feb 3 2026, not archived
- **Latest release**: v1.7.0 (January 2025)
- **Asset Hub compatible**: Yes, fully redesigned for post-migration staking on Asset Hub
- **Daily CI**: Integration tests run against polkadot master

#### How to Get It

- **Pre-built binaries**: Attached to [GitHub Releases](https://github.com/paritytech/polkadot-staking-miner/releases)
- **Docker**: `docker pull paritytech/polkadot-staking-miner`
- **Cargo**: `cargo install polkadot-staking-miner`
- **Source**: Clone and `cargo build --release`

#### Operating Modes

1. **`monitor`** — Main mode: watches chain for election phases, computes solution, submits on-chain as signed extrinsic to earn rewards (~1 DOT). Requires seed phrase and bond deposit.
2. **`predict`** — Offline election prediction (added Jan 2026). No on-chain submission. Generates JSON output files.

#### `predict` Command Output

Generates **two JSON files** in `--output-dir` (default: `results/`):

**`validators_prediction.json`**:
```json
{
  "metadata": {
    "timestamp": "2026-01-15T10:30:00Z",
    "desired_validators": 297,
    "round": 5432,
    "block_number": 13196110,
    "solution_score": {
      "minimal_stake": "1234567890000000",
      "sum_stake": "9876543210000000",
      "sum_stake_squared": "12345678901234567890"
    },
    "data_source": "Snapshot"
  },
  "results": [
    {
      "account": "15S7YtE...",
      "total_stake": "5000000000000",
      "self_stake": "1000000000000",
      "nominator_count": 256,
      "nominators": [
        { "address": "14ShU...", "allocated_stake": "500000000000" }
      ]
    }
  ]
}
```

**`nominators_prediction.json`**:
```json
{
  "nominators": [
    {
      "address": "14ShU...",
      "stake": "2000000000000",
      "active_validators": [
        { "validator": "15S7Y...", "allocated_stake": "500000000000" }
      ],
      "inactive_validators": ["13UVJ..."],
      "waiting_validators": ["16aP3..."]
    }
  ]
}
```

All stake values are strings in **Planck** (1 DOT = 10^10 Planck).

Key distinction per nominator:
- `active_validators` — elected validators that received stake allocation from this nominator
- `inactive_validators` — elected validators nominated by this nominator but received no allocation (Phragmen optimization)
- `waiting_validators` — nominated validators that were NOT elected

#### What-If Scenarios via `--overrides`

The `predict` command supports **custom overrides** to simulate scenarios. It fetches live chain data first, then applies modifications:

```bash
staking-miner --uri wss://rpc-assethub.novasama-tech.org predict \
  --overrides scenario.json \
  --output-dir ./results/scenario1
```

**Override file format** (`ElectionOverrides`):
```json
{
  "candidates_include": ["SS58..."],
  "candidates_exclude": ["SS58..."],
  "voters_include": [
    ["nominator_address", 1000000000000, ["target1", "target2"]]
  ],
  "voters_exclude": ["SS58..."]
}
```

Application order (from source `src/dynamic/election_data.rs`):
1. Remove `candidates_exclude` from candidate list
2. Add `candidates_include`
3. Remove `voters_exclude` from voter list
4. Add/update voters from `voters_include` (each entry: `[account, stake_planck, [targets]]`)

#### All `predict` CLI Flags

```
--uri <WS_URL>                    WebSocket endpoint (required)
--overrides <PATH>                Path to ElectionOverrides JSON file
--desired-validators <NUMBER>     Override target validator count
--block-number <NUMBER>           Fetch data at specific historical block
--algorithm <ALGO>                seq-phragmen (default) or phragmms
--balancing-iterations <NUMBER>   Balancing rounds (default: 10)
--do-reduce                       Enable solution reduction
--output-dir <PATH>               Output directory (default: "results")
```

#### Using as a Rust Library

The crate is published on [crates.io](https://crates.io/crates/polkadot-staking-miner) but the `predict` command is `pub(crate)` (not exported). For programmatic use:

- Use `sp-npos-elections` crate directly for the raw algorithm:
  ```rust
  use sp_npos_elections::{seq_phragmen, phragmms, ElectionResult};
  let result = seq_phragmen(desired_targets, candidates, voters, Some((10, 0)))?;
  ```
- Use the `epm` module for snapshot fetching and solution mining
- Fork the repo to expose `predict_cmd` if needed

#### `sp-npos-elections` Core Types

```rust
pub struct ElectionResult<AccountId, P: PerThing> {
    pub winners: Vec<(AccountId, ExtendedBalance)>,     // elected + approval stake
    pub assignments: Vec<Assignment<AccountId, P>>,     // voter stake distribution
}

pub struct Assignment<AccountId, P: PerThing> {
    pub who: AccountId,                       // voter
    pub distribution: Vec<(AccountId, P)>,    // (target, ratio) — ratios sum to 1.0
}

pub struct Support<AccountId> {
    pub total: ExtendedBalance,                         // total backing
    pub voters: Vec<(AccountId, ExtendedBalance)>,     // individual contributions
}

pub struct ElectionScore {
    pub minimal_stake: ExtendedBalance,     // lowest-backed winner
    pub sum_stake: ExtendedBalance,         // total stake
    pub sum_stake_squared: ExtendedBalance, // lower = more balanced distribution
}
```

### Practical Integration Scenarios

#### Scenario A: "Would our validator survive if N nominators left?"

1. Run our tool to get the list of nominators for our validator
2. Build an overrides file with `voters_exclude` containing those nominators
3. Run `staking-miner predict --overrides overrides.json`
4. Check if our validator appears in `validators_prediction.json`

#### Scenario B: "What's the current predicted active set?"

1. Run `staking-miner predict` with no overrides
2. Parse `validators_prediction.json` for the full elected set
3. Cross-reference with our nomination data

#### Scenario C: "What if a competitor validator lowers commission?"

Commission doesn't affect elections directly (it's a post-election reward split), but it affects which nominators choose which validators over time. This is an analysis our tool already provides — the percentile data shows commission landscape.

### Recommended Approaches (ranked by practicality)

#### Option 1: Shell out to `polkadot-staking-miner` (recommended)

- Install via Docker or pre-built binary (no Rust toolchain needed for end users)
- Spawn `staking-miner predict` as child process, parse JSON output
- Use `--overrides` for what-if scenarios

**Pros**: battle-tested, maintained by Parity, exact same algorithm as production, supports what-if
**Cons**: external binary dependency, ~30-60s execution time

#### Option 2: Compile `sp-npos-elections` to WASM

Compile the Rust election crate to WebAssembly and call from JS:
- Use `wasm-pack` to build `sp-npos-elections` as a WASM module
- Import into Node.js via native WASM support
- Pass voter/target data from our existing fetchers

**Pros**: runs in-process, no external binary, Rust-quality results
**Cons**: complex build pipeline, WASM memory limits, needs custom Rust wrapper crate

#### Option 3: Runtime API dry-run

Some Substrate runtimes expose election dry-run via runtime API calls:
- `ElectionProviderMultiPhase` may provide a `predict` or `dry_run` method
- Would use PAPI's `api.apis` interface to call runtime APIs
- Depends on whether Asset Hub exposes this

**Pros**: uses the actual on-chain logic, no external tools
**Cons**: not guaranteed to be available, may be resource-limited by RPC node

#### Option 4: JS reimplementation of seq-phragmen

Implement Sequential Phragmen from scratch in JavaScript:
- Reference: [W3F Research — Sequential Phragmen Method](https://research-test.readthedocs.io/en/latest/polkadot/NPoS/phragmen/)
- Paper: [Cevallos & Stewart 2021](https://arxiv.org/abs/2004.12990)
- ~500-800 lines for core algorithm, ~200-300 for balancing

**Pros**: no external dependencies, full control, educational value
**Cons**: risk of bugs, precision issues, maintenance burden, no star balancing initially

### Additional Chain Storage Needed for Election Prediction

Beyond what we already fetch, election prediction requires:

| Storage | Purpose |
|---|---|
| `Staking.CounterForValidators` | Total validator candidate count |
| `Staking.ValidatorCount` | Desired active set size (desired_targets) |
| `Staking.MinNominatorBond` | Minimum bond to be included as voter |
| `Staking.MinValidatorBond` | Minimum bond to be included as candidate |
| `Staking.MaxNominatorsCount` | Cap on voters in election snapshot |
| `Staking.MaxValidatorsCount` | Cap on candidates in election snapshot |
| `ElectionProviderMultiPhase.Snapshot` | Pre-built election snapshot (available during election phase only) |

### Key Resources

- [NPoS Election Algorithms — Polkadot Wiki](https://wiki.polkadot.network/docs/learn-phragmen)
- [sp_npos_elections — Rust Docs](https://paritytech.github.io/polkadot-sdk/master/sp_npos_elections/index.html)
- [Sequential Phragmen — W3F Research](https://research-test.readthedocs.io/en/latest/polkadot/NPoS/phragmen/)
- [PhragMMS Paper (arXiv)](https://arxiv.org/abs/2004.12990)
- [polkadot-staking-miner](https://github.com/paritytech/polkadot-staking-miner)
- [Substrate Debug Kit — offline-election](https://github.com/paritytech/substrate-debug-kit/tree/master/offline-election)
- [pallet_election_provider_multi_phase](https://paritytech.github.io/polkadot-sdk/master/pallet_election_provider_multi_phase/index.html)
