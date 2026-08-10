import assert from "node:assert/strict";
import { randomInt } from "node:crypto";
import test from "node:test";
import { connect, fetchActiveValidators } from "../../src/chain.js";

test(
  "typed RPC returns the current active set and validator preferences",
  { timeout: 120_000 },
  async (t) => {
    const { client, api } = connect();

    try {
      const activeValidators = await fetchActiveValidators(api);
      assert.ok(activeValidators.length > 0);
      assert.equal(new Set(activeValidators).size, activeValidators.length);

      const validator = activeValidators[randomInt(activeValidators.length)];
      assert.ok(validator);
      const preferences =
        await api.query.Staking.Validators.getValue(validator);

      assert.ok(preferences, "an active validator must have validator preferences");
      assert.equal(typeof preferences.commission, "number");
      assert.ok(preferences.commission >= 0);
      assert.ok(preferences.commission <= 1_000_000_000);

      const [
        activeEra,
        minimumSelfStake,
        optimumSelfStake,
        hardCapSelfStake,
        slopeFactor,
      ] = await Promise.all([
        api.query.Staking.ActiveEra.getValue(),
        api.query.Staking.MinValidatorBond.getValue(),
        api.query.Staking.OptimumSelfStake.getValue(),
        api.query.Staking.HardCapSelfStake.getValue(),
        api.query.Staking.SelfStakeSlopeFactor.getValue(),
      ]);
      assert.ok(activeEra);
      assert.ok(minimumSelfStake > 0n);
      assert.ok(optimumSelfStake >= minimumSelfStake);
      assert.ok(hardCapSelfStake >= optimumSelfStake);
      assert.ok(slopeFactor >= 0);
      assert.ok(slopeFactor <= 1_000_000_000);

      const completedEra = activeEra.index - 1;
      const [incentiveBudget, totalIncentiveWeight] = await Promise.all([
        api.query.Staking.ErasValidatorIncentiveBudget.getValue(completedEra),
        api.query.Staking.ErasSumValidatorIncentiveWeight.getValue(completedEra),
      ]);
      assert.ok(incentiveBudget > 0n);
      assert.ok(totalIncentiveWeight > 0n);
      t.diagnostic(`sampled active validator: ${validator}`);
    } finally {
      client.destroy();
    }
  },
);
