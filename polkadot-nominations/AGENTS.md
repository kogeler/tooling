# polkadot-nominations — Agent Context

## Project Overview

A Node.js CLI tool that fetches and analyzes staking nominations for a specific validator on the Polkadot network via Asset Hub RPC. Uses the [polkadot-api (PAPI)](https://papi.how/) library for typed chain queries.

## Current Implementation

### Architecture

```
polkadot-nominations/
  index.js              Entry point — orchestrates fetching, filtering, enrichment
  config.json           User configuration (validator, percentiles, minStakeDot)
  package.json          ES modules, polkadot-api dependency, postinstall codegen
  .papi/
    polkadot-api.json   PAPI chain config (wsUrl, genesis, codeHash) — committed to git
    descriptors/        Generated type descriptors — gitignored, rebuilt on npm install
    metadata/           Downloaded chain metadata — gitignored, rebuilt on npm install
  src/
    config.js           Loads config.json and .papi/polkadot-api.json, exports constants
    chain.js            RPC connection and data fetching (validators, nominators, ledgers)
    stats.js            Percentile calculation with configurable direction and prefix
  .gitignore            Excludes node_modules, .papi/descriptors, .papi/metadata
  README.md             User-facing documentation
```

### What It Does

1. Connects to Polkadot Asset Hub via WebSocket RPC (default: `wss://rpc-assethub.novasama-tech.org`)
2. Fetches three datasets **in parallel** via `Promise.all`:
   - `Staking.Validators.getEntries()` — all validators with commission (Perbill)
   - `Staking.Nominators.getEntries()` — all nominators with their target lists
   - `Staking.Ledger.getEntries()` — all staking ledgers with active stake
3. Builds lookup maps: `commissions` (validator address -> commission %), `stakes` (stash -> active DOT)
4. Filters nominations to those targeting the configured validator
5. Optionally filters by minimum stake (`minStakeDot`, 0 = disabled)
6. Drops targets that are no longer registered validators (not in `Staking.Validators`)
7. Enriches each target with its commission %
8. Computes commission percentiles per nomination (descending — higher percentile = lower commission)
9. Computes global commission percentiles across all **unique** targets (deduplicated by address)
10. Computes global stake percentiles across all matching nominators (ascending — standard)
11. Outputs JSON to stdout, logs to stderr

### Key Technical Details

- **Runtime**: Node.js >= 22 (ES modules)
- **Chain**: Polkadot Asset Hub (staking migrated from relay chain as of runtime v2.0.0)
- **RPC library**: `polkadot-api` (PAPI) with generated type descriptors
- **WebSocket**: Uses `ws` npm package passed as `websocketClass` to `getWsProvider` (Node 22 has native WebSocket but PAPI needs the class passed explicitly)
- **Commission format**: Stored on-chain as Perbill (0..1,000,000,000), converted to % with 4 decimal precision
- **Stake format**: Stored as Planck (bigint), converted to whole DOT via `active / 10n**10n`
- **RPC URL**: Read from `.papi/polkadot-api.json` (entries.ah.wsUrl), overridable via `RPC_URL` env var
- **PAPI codegen**: `npx papi add ah -w <wsUrl>` downloads metadata and generates typed descriptors; `npx papi` regenerates from existing config; runs automatically on `npm install` via postinstall

### config.json Schema

```json
{
  "validator": "SS58 address of the target validator",
  "percentiles": [0.25, 0.5, 0.75, 0.9],
  "minStakeDot": 0
}
```

- `validator` — required, SS58 address to filter nominations for
- `percentiles` — optional, array of thresholds (0-1), default `[0.5, 0.75, 0.9]`
- `minStakeDot` — optional, minimum active stake in whole DOT, default `0` (disabled)

### Output Structure

```json
{
  "validator": "SS58...",
  "total_nominators": 212,
  "unique_targets": 352,
  "commission_p25": 5,
  "commission_p50": 3,
  "commission_p75": 1.5,
  "commission_p90": 0.1,
  "stake_p25": 878,
  "stake_p50": 2236,
  "stake_p75": 8061,
  "stake_p90": 23569.3,
  "nominations": [
    {
      "nominator": "SS58...",
      "stake": 6717,
      "targets": [
        { "address": "SS58...", "commission": 4 }
      ],
      "commission_p50": 3,
      "commission_p75": 1,
      "commission_p90": 0,
      "submitted_in": 1738,
      "suppressed": false
    }
  ]
}
```

### Chain Storage Items Used

| Storage | Key | Value | Used For |
|---|---|---|---|
| `Staking.Validators` | SS58String | `{ commission: Perbill, blocked: bool }` | Validator commission lookup |
| `Staking.Nominators` | SS58String | `{ targets: SS58String[], submitted_in: u32, suppressed: bool }` | Nominator -> validator mappings |
| `Staking.Ledger` | SS58String | `{ stash: SS58String, total: u128, active: u128, unlocking: [...] }` | Active stake per nominator |

All three are fetched via `.getEntries()` (full storage scan) in a single parallel batch.

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
