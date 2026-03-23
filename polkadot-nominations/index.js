import { validator, minStakeDot, rpcUrl } from "./src/config.js";
import {
  connect,
  fetchActiveValidators,
  fetchCommissions,
  fetchNominators,
  fetchStakes,
} from "./src/chain.js";
import { calcPercentiles } from "./src/stats.js";

async function main() {
  console.error(`Connecting to ${rpcUrl}...`);
  console.error(`Validator: ${validator}`);
  if (minStakeDot > 0) console.error(`Min stake filter: ${minStakeDot} DOT`);

  const { client, api } = connect();

  console.error("Fetching chain data...");
  const [activeValidators, commissions, nominatorEntries, stakes] =
    await Promise.all([
      fetchActiveValidators(api),
      fetchCommissions(api),
      fetchNominators(api),
      fetchStakes(api),
    ]);
  const activeSet = new Set(activeValidators);
  console.error(
    `Loaded: ${commissions.size} validators (${activeSet.size} active), ${nominatorEntries.length} nominators, ${stakes.size} ledgers`,
  );

  const SELF_STAKE_THRESHOLD = 10_000;
  let activeAbove = 0;
  let allAbove = 0;
  for (const addr of commissions.keys()) {
    const selfStake = stakes.get(addr) ?? 0;
    if (selfStake >= SELF_STAKE_THRESHOLD) {
      allAbove++;
      if (activeSet.has(addr)) activeAbove++;
    }
  }
  const activeSelfStakePct =
    activeSet.size > 0
      ? Math.round((activeAbove / activeSet.size) * 1e4) / 1e2
      : 0;
  const allSelfStakePct =
    commissions.size > 0
      ? Math.round((allAbove / commissions.size) * 1e4) / 1e2
      : 0;

  const nominations = [];
  const uniqueTargets = new Map();

  for (const entry of nominatorEntries) {
    if (!entry.value.targets.includes(validator)) continue;

    const nominator = entry.keyArgs[0];
    const stake = stakes.get(nominator) ?? 0;

    if (minStakeDot > 0 && stake < minStakeDot) continue;

    const targets = [];
    const targetCommissions = [];

    for (const addr of entry.value.targets) {
      if (!commissions.has(addr)) continue;
      const c = commissions.get(addr);
      targets.push({ address: addr, commission: c });
      targetCommissions.push(c);
      uniqueTargets.set(addr, c);
    }

    nominations.push({
      nominator,
      stake,
      targets,
      ...calcPercentiles(targetCommissions, {
        descending: true,
        prefix: "commission_",
      }),
      submitted_in: entry.value.submitted_in,
      suppressed: entry.value.suppressed,
    });
  }
  nominatorEntries.length = 0;

  const nominatorStakes = nominations.map((n) => n.stake);

  const output = {
    validator,
    total_nominators: nominations.length,
    unique_targets: uniqueTargets.size,
    active_validators_self_stake_gte_10k_pct: activeSelfStakePct,
    all_validators_self_stake_gte_10k_pct: allSelfStakePct,
    ...calcPercentiles([...uniqueTargets.values()], {
      descending: true,
      prefix: "commission_",
    }),
    ...calcPercentiles(nominatorStakes, { prefix: "stake_" }),
    nominations,
  };

  console.error(`Found ${nominations.length} nominators for this validator`);
  console.log(JSON.stringify(output, null, 2));

  client.destroy();
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
