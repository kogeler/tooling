import assert from "node:assert/strict";
import test from "node:test";
import {
  buildNominationsReport,
  buildSelfStakeStats,
  buildValidatorInfo,
} from "../src/analysis.js";
import type { NominatorEntry } from "../src/analysis.js";

function nomination(
  nominator: string,
  targets: readonly string[],
  submittedIn = 100,
): NominatorEntry {
  return {
    keyArgs: [nominator],
    value: { targets, submitted_in: submittedIn, suppressed: false },
  };
}

test("buildNominationsReport groups results by configured validator", () => {
  const output = buildNominationsReport({
    validators: ["validator-a", "validator-b"],
    commissions: new Map([
      ["validator-a", 1],
      ["validator-b", 5],
      ["other", 10],
    ]),
    nominatorEntries: [
      nomination("nominator-a", ["validator-a", "other"]),
      nomination("below-minimum", ["validator-a", "validator-b"]),
      nomination("nominator-b", ["validator-b"]),
    ],
    stakes: new Map([
      ["nominator-a", 2_000],
      ["below-minimum", 999],
      ["nominator-b", 1_500],
    ]),
    minStakeDot: 1_000,
    percentiles: [0.5],
  });

  assert.deepEqual(Object.keys(output), ["validator-a", "validator-b"]);
  const validatorA = output["validator-a"];
  const validatorB = output["validator-b"];
  assert.ok(validatorA);
  assert.ok(validatorB);
  assert.equal(validatorA.total_nominators, 1);
  assert.equal(validatorA.unique_targets, 2);
  assert.equal(validatorA.commission_p50, 5.5);
  assert.equal(validatorA.stake_p50, 2_000);
  assert.deepEqual(validatorA.nominations[0]?.targets, [
    { address: "validator-a", commission: 1 },
    { address: "other", commission: 10 },
  ]);
  assert.equal(validatorB.total_nominators, 1);
  assert.equal(validatorB.nominations[0]?.nominator, "nominator-b");
});

test("buildNominationsReport keeps an empty object for unmatched validators", () => {
  const output = buildNominationsReport({
    validators: ["validator-a"],
    commissions: new Map(),
    nominatorEntries: [],
    stakes: new Map(),
    minStakeDot: 0,
    percentiles: [0.5],
  });

  assert.deepEqual(output, {
    "validator-a": {
      total_nominators: 0,
      unique_targets: 0,
      nominations: [],
    },
  });
});

test("buildNominationsReport adds a shared nominator to every matched validator", () => {
  const output = buildNominationsReport({
    validators: ["validator-a", "validator-b"],
    commissions: new Map([
      ["validator-a", 1],
      ["validator-b", 2],
    ]),
    nominatorEntries: [
      nomination("shared-nominator", ["validator-a", "validator-b"]),
    ],
    stakes: new Map([["shared-nominator", 5_000]]),
    minStakeDot: 0,
    percentiles: [0.5],
  });

  const validatorA = output["validator-a"];
  const validatorB = output["validator-b"];
  assert.ok(validatorA);
  assert.ok(validatorB);
  assert.equal(validatorA.total_nominators, 1);
  assert.equal(validatorB.total_nominators, 1);
  assert.equal(
    validatorA.nominations[0]?.nominator,
    "shared-nominator",
  );
  assert.equal(
    validatorB.nominations[0]?.nominator,
    "shared-nominator",
  );
});

test("buildSelfStakeStats caps stakes above 30k without excluding validators", () => {
  const output = buildSelfStakeStats({
    validatorAddresses: ["a", "b", "c", "d", "e"],
    activeValidators: ["b", "e"],
    stakes: new Map([
      ["a", 0],
      ["b", 10_000],
      ["c", 20_000],
      ["d", 30_000],
      ["e", 40_000],
    ]),
    percentiles: [0.25, 0.5, 0.75],
  });

  assert.deepEqual(output.active_validators, {
    total: 2,
    meeting_minimum: 2,
    meeting_minimum_pct: 100,
  });
  assert.deepEqual(output.all_validators, {
    total: 5,
    meeting_minimum: 4,
    meeting_minimum_pct: 80,
  });
  assert.deepEqual(output.reward_effective_self_stake, {
    minimum_dot: 10_000,
    cap_dot: 30_000,
    validator_count: 4,
    capped_validator_count: 1,
    self_stake_p25: 17_500,
    self_stake_p50: 25_000,
    self_stake_p75: 30_000,
  });
});

test("buildSelfStakeStats handles an empty validator set", () => {
  assert.deepEqual(
    buildSelfStakeStats({
      validatorAddresses: [],
      activeValidators: [],
      stakes: new Map(),
      percentiles: [0.5],
    }),
    {
      minimum_self_stake_dot: 10_000,
      active_validators: {
        total: 0,
        meeting_minimum: 0,
        meeting_minimum_pct: 0,
      },
      all_validators: {
        total: 0,
        meeting_minimum: 0,
        meeting_minimum_pct: 0,
      },
      reward_effective_self_stake: {
        minimum_dot: 10_000,
        cap_dot: 30_000,
        validator_count: 0,
        capped_validator_count: 0,
      },
    },
  );
});

test("buildValidatorInfo uses validator addresses as output keys", () => {
  const rewards = { eras_requested: 20, eras: [] };
  const rewardProjection = { scenarios: [] };
  assert.deepEqual(
    buildValidatorInfo(
      ["validator-a", "validator-b"],
      new Map([["validator-a", 12_345]]),
      new Map([
        [
          "validator-a",
          { rewards, reward_projection: rewardProjection },
        ],
        [
          "validator-b",
          { rewards, reward_projection: rewardProjection },
        ],
      ]),
    ),
    {
      "validator-a": {
        self_stake: 12_345,
        rewards,
        reward_projection: rewardProjection,
      },
      "validator-b": {
        self_stake: 0,
        rewards,
        reward_projection: rewardProjection,
      },
    },
  );
});
