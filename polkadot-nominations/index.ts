import { existsSync } from "node:fs";
import { parseCliArgs, USAGE } from "./src/cli.js";
import type { RunnableCommand } from "./src/cli.js";
import {
  describeError,
  installExitLogging,
  log,
  phase,
  redactUrl,
  reportFatal,
} from "./src/log.js";
import { createRpcRunner, startProgress } from "./src/rpc.js";
import type { RpcRunner } from "./src/rpc.js";

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

  const runner: RpcRunner = createRpcRunner(
    {
      requestTimeoutMs: config.requestTimeoutMs,
      requestRetries: config.requestRetries,
      maxConcurrency: config.maxConcurrency,
    },
    {
      onRetry: (operation, attempt, error) => {
        log(`RETRY ${attempt} for ${operation}: ${describeError(error)}`);
      },
    },
  );

  log(`Mode: --${command} on ${process.version}`);
  log(`RPC endpoint: ${redactUrl(config.rpcUrl)}`);
  log(
    `Limits: connect ${config.connectTimeoutMs} ms, request ${config.requestTimeoutMs} ms,` +
      ` retries ${config.requestRetries}, max ${config.maxConcurrency} concurrent calls`,
  );
  const { client, api } = chain.connect({ log });

  const stopProgress = startProgress({
    label: command,
    intervalMs: config.progressIntervalMs,
    runner,
    log,
  });

  try {
    const activeEra = await phase("connect", () =>
      chain.probeConnection(api, config.connectTimeoutMs),
    );
    log(`Active era: ${activeEra ?? "unavailable"}`);

    if (command === "nominations") {
      log(
        `Fetching nominations for ${config.validators.length} configured validator(s)`,
      );
      if (config.minStakeDot > 0) {
        log(`Min nominator stake filter: ${config.minStakeDot} DOT`);
      }

      const [commissions, nominatorEntries, stakes] = await Promise.all([
        phase("scan Staking.Validators", () =>
          chain.fetchCommissions(api, runner),
        ),
        phase("scan Staking.Nominators", () =>
          chain.fetchNominators(api, runner),
        ),
        phase("scan Staking.Ledger", () => chain.fetchStakes(api, runner)),
      ]);
      log(
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
        log(`Found ${report.total_nominators} nominators for ${validator}`);
      }
      return output;
    }

    if (command === "self-stake-stats") {
      log("Fetching network-wide validator self-stake data");
      const [activeValidators, commissions, stakes] = await Promise.all([
        phase("read active set", () =>
          chain.fetchActiveValidators(api, runner),
        ),
        phase("scan Staking.Validators", () =>
          chain.fetchCommissions(api, runner),
        ),
        phase("scan Staking.Ledger", () => chain.fetchStakes(api, runner)),
      ]);
      log(
        `Loaded: ${commissions.size} validators (${activeValidators.length} active), ${stakes.size} ledgers`,
      );

      return analysis.buildSelfStakeStats({
        validatorAddresses: commissions.keys(),
        activeValidators,
        stakes,
        percentiles: config.percentiles,
      });
    }

    log(
      `Fetching general information for ${config.validators.length} configured validator(s)`,
    );
    log(`Reward lookback: last ${config.rewardEras} eras`);

    // Only the configured stashes are needed here, so this mode deliberately
    // avoids the full Staking.Ledger scan that the network-wide modes require.
    const [stakeBalances, rewardHistories, rewardCurve] = await Promise.all([
      phase("read self-stake", () =>
        chain.fetchSelfStakes(api, config.validators, runner),
      ),
      Promise.all(
        config.validators.map((validator) =>
          phase(`rewards ${validator}`, async () => {
            const history = await rewards.fetchValidatorRewardHistory(
              api,
              validator,
              config.rewardEras,
              runner,
            );
            return [validator, history] as const;
          }),
        ),
      ),
      phase("read reward curve", () =>
        rewards.fetchRewardCurveParameters(api, runner),
      ),
    ]);
    log(`Self-stake resolved for ${stakeBalances.dots.size} stash(es)`);

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
    stopProgress();
    const stats = runner.stats();
    log(
      `RPC summary: ${stats.completed} completed, ${stats.retried} retried,` +
        ` ${stats.failed} failed, ${stats.inFlight} still in flight`,
    );
    client.destroy();
  }
}

function writeStdout(text: string): Promise<void> {
  return new Promise((resolve, reject) => {
    process.stdout.write(text, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

async function main(): Promise<void> {
  assertContainerRuntime();
  const command = parseCliArgs(process.argv.slice(2));
  if (command === "help") {
    await writeStdout(`${USAGE}\n`);
    return;
  }

  const output = await runCommand(command);
  await writeStdout(`${JSON.stringify(output, null, 2)}\n`);
  log("Report written to stdout");
}

installExitLogging();

main().then(
  () => {
    // A half-open RPC socket can keep the event loop alive after the report is
    // written, so exit explicitly once stdout has drained.
    process.exit(0);
  },
  (error: unknown) => {
    reportFatal(error);
    process.exit(1);
  },
);
