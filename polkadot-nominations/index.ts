import { existsSync } from "node:fs";
import { parseCliArgs, USAGE } from "./src/cli.js";
import type { RunnableCommand } from "./src/cli.js";

function assertContainerRuntime(): void {
  if (!existsSync("/run/.containerenv") && !existsSync("/.dockerenv")) {
    throw new Error(
      "Direct host execution is prohibited. Run this tool through make and Podman.",
    );
  }
}

async function runCommand(command: RunnableCommand): Promise<unknown> {
  const [config, chain, analysis, rewards] = await Promise.all([
    import("./src/config.js"),
    import("./src/chain.js"),
    import("./src/analysis.js"),
    import("./src/rewards.js"),
  ]);

  console.error(`Connecting to ${config.rpcUrl}...`);
  const { client, api } = chain.connect();

  try {
    if (command === "nominations") {
      console.error(
        `Fetching nominations for ${config.validators.length} configured validator(s)...`,
      );
      if (config.minStakeDot > 0) {
        console.error(`Min nominator stake filter: ${config.minStakeDot} DOT`);
      }

      const [commissions, nominatorEntries, stakes] = await Promise.all([
        chain.fetchCommissions(api),
        chain.fetchNominators(api),
        chain.fetchStakes(api),
      ]);
      console.error(
        `Loaded: ${commissions.size} validators, ${nominatorEntries.length} nominators, ${stakes.size} ledgers`,
      );

      const output = analysis.buildNominationsReport({
        validators: config.validators,
        commissions,
        nominatorEntries,
        stakes,
        minStakeDot: config.minStakeDot,
        percentiles: config.percentiles,
      });
      for (const [validator, report] of Object.entries(output)) {
        console.error(
          `Found ${report.total_nominators} nominators for ${validator}`,
        );
      }
      return output;
    }

    if (command === "self-stake-stats") {
      console.error("Fetching network-wide validator self-stake data...");
      const [activeValidators, commissions, stakes] = await Promise.all([
        chain.fetchActiveValidators(api),
        chain.fetchCommissions(api),
        chain.fetchStakes(api),
      ]);
      console.error(
        `Loaded: ${commissions.size} validators (${activeValidators.length} active), ${stakes.size} ledgers`,
      );

      return analysis.buildSelfStakeStats({
        validatorAddresses: commissions.keys(),
        activeValidators,
        stakes,
        percentiles: config.percentiles,
      });
    }

    console.error(
      `Fetching general information for ${config.validators.length} configured validator(s)...`,
    );
    console.error(`Reward lookback: last ${config.rewardEras} eras`);
    const [stakeBalances, rewardHistories, rewardCurve] = await Promise.all([
      chain.fetchStakeBalances(api),
      Promise.all(
        config.validators.map(async (validator) =>
          [
            validator,
            await rewards.fetchValidatorRewardHistory(
              api,
              validator,
              config.rewardEras,
            ),
          ] as const,
        ),
      ),
      rewards.fetchRewardCurveParameters(api),
    ]);

    const detailsByValidator = new Map(
      rewardHistories.map(([validator, history]) => [
        validator,
        rewards.buildValidatorRewardReport(
          history,
          stakeBalances.planck.get(validator) ?? 0n,
          rewardCurve,
        ),
      ]),
    );

    return analysis.buildValidatorInfo(
      config.validators,
      stakeBalances.dots,
      detailsByValidator,
    );
  } finally {
    client.destroy();
  }
}

async function main(): Promise<void> {
  assertContainerRuntime();
  const command = parseCliArgs(process.argv.slice(2));
  if (command === "help") {
    console.log(USAGE);
    return;
  }

  const output = await runCommand(command);
  console.log(JSON.stringify(output, null, 2));
}

main().catch((error: unknown) => {
  console.error("Error:", error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
