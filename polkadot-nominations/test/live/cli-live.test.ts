import assert from "node:assert/strict";
import { randomInt } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import test from "node:test";
import { connect, fetchActiveValidators } from "../../src/chain.js";
import { rpcUrl } from "../../src/config.js";
import type {
  NominationsOutput,
  SelfStakeStats,
  ValidatorInfoOutput,
} from "../../src/analysis.js";
import type {
  RewardProjection,
  ValidatorRewards,
} from "../../src/rewards.js";

const PROJECT_ROOT = dirname(
  dirname(dirname(dirname(fileURLToPath(import.meta.url)))),
);
const SAMPLE_SIZE = 3;

function sampleWithoutReplacement(
  values: readonly string[],
  count: number,
): string[] {
  const pool = [...values];
  const result: string[] = [];
  while (result.length < count) {
    const index = randomInt(pool.length);
    const selected = pool[index];
    if (selected === undefined) {
      throw new Error("Cannot sample more validators than are available");
    }
    result.push(selected);
    pool.splice(index, 1);
  }
  return result;
}

function runCli<T>(flag: string, configPath: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const child = spawn(process.execPath, ["dist/index.js", flag], {
      cwd: PROJECT_ROOT,
      env: { ...process.env, CONFIG_PATH: configPath },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`${flag} exited with ${code}:\n${stderr}`));
        return;
      }

      try {
        resolve(JSON.parse(stdout) as T);
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        reject(new Error(`${flag} returned invalid JSON: ${message}`));
      }
    });
  });
}

function assertAddressMap(
  report: Readonly<Record<string, unknown>>,
  validators: readonly string[],
): void {
  assert.deepEqual(Object.keys(report).sort(), [...validators].sort());
}

function requireMetric(
  metrics: Readonly<Record<string, number>>,
  name: string,
): number {
  const value = metrics[name];
  if (value === undefined) {
    assert.fail(`${name} must be present`);
  }
  return value;
}

test(
  "all CLI modes work against random validators from the current active set",
  { timeout: 300_000 },
  async (t) => {
    const { client, api } = connect();
    let activeValidators: string[];
    try {
      activeValidators = await fetchActiveValidators(api);
    } finally {
      client.destroy();
    }

    assert.ok(activeValidators.length >= SAMPLE_SIZE);
    const validators = sampleWithoutReplacement(activeValidators, SAMPLE_SIZE);
    t.diagnostic(`random active validators: ${validators.join(", ")}`);

    const configDirectory = await mkdtemp(
      join(tmpdir(), "polkadot-nominations-live-"),
    );
    const configPath = join(configDirectory, "config.json");
    await writeFile(
      configPath,
      JSON.stringify(
        {
          validators,
          percentiles: [0.25, 0.5, 0.75, 0.9],
          minStakeDot: 0,
          rewardEras: 3,
          rpcUrl,
        },
        null,
        2,
      ),
    );

    try {
      await t.test("--nominations", async () => {
        const report = await runCli<NominationsOutput>(
          "--nominations",
          configPath,
        );
        assertAddressMap(report, validators);

        for (const validator of validators) {
          const validatorReport = report[validator];
          assert.ok(validatorReport);
          assert.equal(
            validatorReport.total_nominators,
            validatorReport.nominations.length,
          );
          const uniqueTargets = new Set(
            validatorReport.nominations.flatMap((nomination) =>
              nomination.targets.map((target) => target.address),
            ),
          );
          assert.equal(validatorReport.unique_targets, uniqueTargets.size);
          for (const nomination of validatorReport.nominations) {
            assert.ok(nomination.stake >= 0);
            assert.ok(
              nomination.targets.some((target) => target.address === validator),
            );
          }
        }
      });

      await t.test("--self-stake-stats", async () => {
        const report = await runCli<SelfStakeStats>(
          "--self-stake-stats",
          configPath,
        );
        assert.equal(report.minimum_self_stake_dot, 10_000);
        assert.ok(report.active_validators.total > 0);
        assert.ok(report.all_validators.total >= report.active_validators.total);
        assert.ok(
          report.active_validators.meeting_minimum <=
            report.active_validators.total,
        );
        assert.ok(
          report.all_validators.meeting_minimum <= report.all_validators.total,
        );
        const effectiveStake = report.reward_effective_self_stake;
        assert.equal(effectiveStake.minimum_dot, 10_000);
        assert.equal(effectiveStake.cap_dot, 30_000);
        assert.equal(
          effectiveStake.validator_count,
          report.all_validators.meeting_minimum,
        );
        assert.ok(effectiveStake.validator_count > 0);
        assert.ok(effectiveStake.capped_validator_count >= 0);
        assert.ok(
          effectiveStake.capped_validator_count <=
            effectiveStake.validator_count,
        );
        const p25 = requireMetric(effectiveStake, "self_stake_p25");
        const p50 = requireMetric(effectiveStake, "self_stake_p50");
        const p75 = requireMetric(effectiveStake, "self_stake_p75");
        const p90 = requireMetric(effectiveStake, "self_stake_p90");
        assert.ok(p25 >= 10_000);
        assert.ok(p90 <= 30_000);
        assert.ok(p25 <= p50);
        assert.ok(p50 <= p75);
        assert.ok(p75 <= p90);
      });

      await t.test("--validator-info", async () => {
        const report = await runCli<
          ValidatorInfoOutput<ValidatorRewards, RewardProjection>
        >("--validator-info", configPath);
        assertAddressMap(report, validators);

        for (const validator of validators) {
          const validatorReport = report[validator];
          assert.ok(validatorReport);
          assert.ok(validatorReport.self_stake >= 0);
          assert.equal(validatorReport.rewards.eras_requested, 3);
          assert.equal(validatorReport.rewards.is_validator, true);
          assert.equal(validatorReport.rewards.eras.length, 3);
          assert.ok(validatorReport.rewards.total_reward >= 0);
          assert.ok(validatorReport.rewards.unclaimed_reward >= 0);
          assert.ok(
            validatorReport.rewards.unclaimed_reward <=
              validatorReport.rewards.total_reward,
          );
          for (const era of validatorReport.rewards.eras) {
            assert.ok(era.validator_incentive_reward >= 0);
            assert.ok(
              Math.abs(
                era.reward -
                  era.commission_reward -
                  era.own_stake_reward -
                  era.validator_incentive_reward,
              ) <= 0.0003,
            );
          }

          const projection = validatorReport.reward_projection;
          assert.equal(projection.basis.eras_requested, 3);
          assert.ok(projection.basis.eras_used >= 0);
          assert.ok(projection.basis.eras_used <= 3);
          assert.equal(
            projection.basis.current_self_stake,
            validatorReport.self_stake,
          );
          assert.ok(
            projection.curve.optimum_self_stake >=
              projection.curve.minimum_self_stake,
          );
          assert.ok(
            projection.curve.hard_cap_self_stake >=
              projection.curve.optimum_self_stake,
          );

          let previousStake = projection.basis.current_self_stake;
          for (const scenario of projection.scenarios) {
            assert.ok(scenario.self_stake > previousStake);
            assert.ok(
              scenario.self_stake <= projection.curve.hard_cap_self_stake,
            );
            assert.ok(scenario.additional_self_stake > 0);
            assert.ok(scenario.estimated_total_reward >= 0);
            assert.ok(
              Math.abs(
                scenario.estimated_total_reward -
                  scenario.estimated_total_commission_reward -
                  scenario.estimated_total_own_stake_reward -
                  scenario.estimated_total_validator_incentive_reward,
              ) <= 0.0003,
            );
            assert.equal(
              Object.hasOwn(scenario, "relative_to_current_pct"),
              false,
            );
            previousStake = scenario.self_stake;
          }
          if (
            projection.basis.current_self_stake <
            projection.curve.hard_cap_self_stake
          ) {
            assert.equal(
              projection.scenarios.at(-1)?.self_stake,
              projection.curve.hard_cap_self_stake,
            );
          }
        }
      });
    } finally {
      await rm(configDirectory, { recursive: true });
    }
  },
);
