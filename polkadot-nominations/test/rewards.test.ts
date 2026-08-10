import assert from "node:assert/strict";
import test from "node:test";
import type { ChainApi } from "../src/chain.js";
import { DOT_PLANCK } from "../src/constants.js";
import {
  buildRewardProjection,
  buildSelfStakeScenarioTargets,
  calculateValidatorIncentiveWeight,
  computeEraReward,
  fetchValidatorRewardHistory,
  fetchValidatorRewards,
} from "../src/rewards.js";
import type { RewardCurveParameters } from "../src/rewards.js";

const CURVE: RewardCurveParameters = {
  minimumSelfStake: 10_000n * DOT_PLANCK,
  optimumSelfStake: 30_000n * DOT_PLANCK,
  hardCapSelfStake: 100_000n * DOT_PLANCK,
  slopeFactor: 500_000_000n,
};

test("calculateValidatorIncentiveWeight follows the runtime curve", () => {
  assert.equal(
    calculateValidatorIncentiveWeight(10_000n * DOT_PLANCK, CURVE),
    10_000_000n,
  );
  assert.equal(
    calculateValidatorIncentiveWeight(30_000n * DOT_PLANCK, CURVE),
    17_320_508n,
  );
  assert.equal(
    calculateValidatorIncentiveWeight(50_000n * DOT_PLANCK, CURVE),
    18_708_286n,
  );
  assert.equal(
    calculateValidatorIncentiveWeight(100_000n * DOT_PLANCK, CURVE),
    21_794_494n,
  );
  assert.equal(
    calculateValidatorIncentiveWeight(150_000n * DOT_PLANCK, CURVE),
    21_794_494n,
  );
});

test("buildSelfStakeScenarioTargets uses the live hard cap as the final point", () => {
  const parameters = {
    ...CURVE,
    hardCapSelfStake: 95_000n * DOT_PLANCK,
  };
  const targets = buildSelfStakeScenarioTargets(
    10_100n * DOT_PLANCK,
    parameters,
  ).map((value) => Number(value / DOT_PLANCK));

  assert.deepEqual(targets, [
    12_500,
    15_000,
    17_500,
    20_000,
    22_500,
    25_000,
    27_500,
    30_000,
    40_000,
    50_000,
    60_000,
    70_000,
    80_000,
    90_000,
    95_000,
  ]);
});

test("computeEraReward includes commission, own stake, and validator incentive", async () => {
  const validator = "validator-a";
  const api = {
    query: {
      Staking: {
        ErasStakersOverview: {
          getValue: async (era: number, address: string) => {
            assert.equal(era, 42);
            assert.equal(address, validator);
            return {
              own: 100n * DOT_PLANCK,
              total: 1_000n * DOT_PLANCK,
              page_count: 2,
            };
          },
        },
        ErasValidatorReward: {
          getValue: async () => 1_000n * DOT_PLANCK,
        },
        ErasRewardPoints: {
          getValue: async () => ({
            total: 100,
            individual: [[validator, 20]],
          }),
        },
        ErasValidatorPrefs: {
          getValue: async () => ({ commission: 100_000_000 }),
        },
        ClaimedRewards: { getValue: async () => [0, 1] },
        ErasValidatorIncentiveBudget: {
          getValue: async () => 500n * DOT_PLANCK,
        },
        ErasValidatorIncentiveWeight: { getValue: async () => 10n },
        ErasSumValidatorIncentiveWeight: { getValue: async () => 100n },
      },
    },
  } as unknown as ChainApi;

  const result = await computeEraReward(api, validator, 42);

  assert.equal(result.reward, 88);
  assert.equal(result.commission_reward, 20);
  assert.equal(result.own_stake_reward, 18);
  assert.equal(result.validator_incentive_reward, 50);
  assert.equal(result.reward_planck, (88n * DOT_PLANCK).toString());
  assert.equal(result.claimed, true);
});

test("computeEraReward reports an inactive era without exposure", async () => {
  const api = {
    query: {
      Staking: {
        ErasStakersOverview: { getValue: async () => undefined },
        ErasValidatorReward: { getValue: async () => 1n },
        ErasRewardPoints: { getValue: async () => ({}) },
        ErasValidatorPrefs: { getValue: async () => undefined },
        ClaimedRewards: { getValue: async () => [] },
        ErasValidatorIncentiveBudget: { getValue: async () => 1n },
        ErasValidatorIncentiveWeight: { getValue: async () => undefined },
        ErasSumValidatorIncentiveWeight: { getValue: async () => 1n },
      },
    },
  } as unknown as ChainApi;

  const result = await computeEraReward(api, "validator-a", 42);
  assert.equal(result.active, false);
  assert.equal(result.has_data, true);
  assert.equal(result.reward, 0);
  assert.equal(result.validator_incentive_reward, 0);
});

test("fetchValidatorRewards aggregates claimed eras and detects a pending collapse", async () => {
  const validator = "validator-a";
  const api = {
    query: {
      Staking: {
        ActiveEra: { getValue: async () => ({ index: 3 }) },
        Validators: {
          getValue: async (address: string) => {
            assert.equal(address, validator);
            return { commission: 0 };
          },
        },
        ErasStakersOverview: {
          getValue: async () => ({
            own: 0n,
            total: 100n * DOT_PLANCK,
            page_count: 1,
          }),
        },
        ErasValidatorReward: {
          getValue: async () => 100n * DOT_PLANCK,
        },
        ErasRewardPoints: {
          getValue: async () => ({
            total: 10,
            individual: [[validator, 10]],
          }),
        },
        ErasValidatorPrefs: {
          getValue: async () => ({ commission: 100_000_000 }),
        },
        ClaimedRewards: {
          getValue: async (era: number) => (era === 1 ? [0] : []),
        },
        ErasValidatorIncentiveBudget: {
          getValue: async () => 50n * DOT_PLANCK,
        },
        ErasValidatorIncentiveWeight: { getValue: async () => undefined },
        ErasSumValidatorIncentiveWeight: { getValue: async () => 100n },
      },
    },
  } as unknown as ChainApi;

  const result = await fetchValidatorRewards(api, validator, 2);

  assert.equal(result.total_reward, 20);
  assert.equal(result.unclaimed_reward, 10);
  assert.equal(result.current_commission_pct, 0);
  assert.equal(result.latest_era_commission_pct, 10);
  assert.equal(result.reward_collapse_pending, true);
  assert.deepEqual(
    result.eras.map(({ era, claimed }) => ({ era, claimed })),
    [
      { era: 2, claimed: false },
      { era: 1, claimed: true },
    ],
  );
});

test("buildRewardProjection compares every target with the validator actual reward", async () => {
  const validator = "validator-a";
  const currentSelfStake = 10_000n * DOT_PLANCK;
  const currentWeight = calculateValidatorIncentiveWeight(
    currentSelfStake,
    CURVE,
  );
  const api = {
    query: {
      Staking: {
        ActiveEra: { getValue: async () => ({ index: 43 }) },
        Validators: { getValue: async () => ({ commission: 0 }) },
        ErasStakersOverview: {
          getValue: async () => ({
            own: currentSelfStake,
            total: 100_000n * DOT_PLANCK,
            page_count: 1,
          }),
        },
        ErasValidatorReward: {
          getValue: async () => 1_000n * DOT_PLANCK,
        },
        ErasRewardPoints: {
          getValue: async () => ({
            total: 100,
            individual: [[validator, 100]],
          }),
        },
        ErasValidatorPrefs: { getValue: async () => ({ commission: 0 }) },
        ClaimedRewards: { getValue: async () => [] },
        ErasValidatorIncentiveBudget: {
          getValue: async () => 1_000n * DOT_PLANCK,
        },
        ErasValidatorIncentiveWeight: {
          getValue: async () => currentWeight,
        },
        ErasSumValidatorIncentiveWeight: {
          getValue: async () => 1_000_000_000n,
        },
      },
    },
  } as unknown as ChainApi;

  const history = await fetchValidatorRewardHistory(api, validator, 1);
  const projection = buildRewardProjection(history, currentSelfStake, CURVE);

  assert.equal(projection.basis.eras_used, 1);
  assert.equal(projection.basis.actual_total_reward, 110);
  assert.equal(projection.scenarios[0]?.self_stake, 12_500);
  assert.equal(projection.scenarios.at(-1)?.self_stake, 100_000);

  const atOptimum = projection.scenarios.find(
    ({ self_stake }) => self_stake === 30_000,
  );
  assert.ok(atOptimum);
  assert.equal(atOptimum.estimated_total_own_stake_reward, 250);
  assert.ok(atOptimum.estimated_total_validator_incentive_reward > 17);
  assert.ok(atOptimum.estimated_total_reward > 267);
  assert.ok(atOptimum.increase_pct !== null);
  assert.ok(atOptimum.increase_pct > 142);
  assert.equal(
    Object.hasOwn(atOptimum, "relative_to_current_pct"),
    false,
  );
});

test("fetchValidatorRewards handles a missing active era", async () => {
  const api = {
    query: {
      Staking: {
        ActiveEra: { getValue: async () => undefined },
        Validators: { getValue: async () => undefined },
      },
    },
  } as unknown as ChainApi;

  assert.deepEqual(await fetchValidatorRewards(api, "validator-a", 20), {
    eras_requested: 20,
    active_era: null,
    is_validator: false,
    current_commission_pct: null,
    latest_era_commission_pct: null,
    reward_collapse_pending: false,
    total_reward: 0,
    unclaimed_reward: 0,
    eras: [],
  });
});
