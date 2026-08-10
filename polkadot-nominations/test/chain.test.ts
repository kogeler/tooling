import assert from "node:assert/strict";
import test from "node:test";
import {
  fetchActiveValidators,
  fetchCommissions,
  fetchNominators,
  fetchStakeBalances,
  fetchStakes,
} from "../src/chain.js";
import type { ChainApi } from "../src/chain.js";
import { DOT_PLANCK } from "../src/constants.js";

test("chain fetchers convert typed storage results", async () => {
  const nominators = [{ keyArgs: ["nominator-a"], value: { targets: [] } }];
  const api = {
    query: {
      Staking: {
        ActiveEra: { getValue: async () => ({ index: 42 }) },
        ErasStakersOverview: {
          getEntries: async (era: number) => {
            assert.equal(era, 42);
            return [
              { keyArgs: [42, "validator-a"] },
              { keyArgs: [42, "validator-b"] },
            ];
          },
        },
        Validators: {
          getEntries: async () => [
            {
              keyArgs: ["validator-a"],
              value: { commission: 50_000_000 },
            },
          ],
        },
        Nominators: { getEntries: async () => nominators },
        Ledger: {
          getEntries: async () => [
            {
              value: {
                stash: "validator-a",
                active: 12n * DOT_PLANCK + DOT_PLANCK / 2n,
              },
            },
          ],
        },
      },
    },
  } as unknown as ChainApi;

  assert.deepEqual(await fetchActiveValidators(api), [
    "validator-a",
    "validator-b",
  ]);
  assert.deepEqual(await fetchCommissions(api), new Map([["validator-a", 5]]));
  assert.equal(await fetchNominators(api), nominators);
  assert.deepEqual(await fetchStakeBalances(api), {
    dots: new Map([["validator-a", 12.5]]),
    planck: new Map([
      ["validator-a", 12n * DOT_PLANCK + DOT_PLANCK / 2n],
    ]),
  });
  assert.deepEqual(await fetchStakes(api), new Map([["validator-a", 12.5]]));
});
