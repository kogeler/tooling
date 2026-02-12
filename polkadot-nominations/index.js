import { validator, minStakeDot, rpcUrl } from "./src/config.js";
import {
  connect,
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
  const [commissions, nominatorEntries, stakes] = await Promise.all([
    fetchCommissions(api),
    fetchNominators(api),
    fetchStakes(api),
  ]);
  console.error(
    `Loaded: ${commissions.size} validators, ${nominatorEntries.length} nominators, ${stakes.size} ledgers`,
  );

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
