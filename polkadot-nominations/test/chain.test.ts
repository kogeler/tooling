import assert from "node:assert/strict";
import test from "node:test";
import {
  fetchActiveValidators,
  fetchCommissions,
  fetchNominators,
  fetchSelfStakes,
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

test("fetchSelfStakes resolves stashes through Bonded without a full scan", async () => {
  const ledgers = new Map<string, bigint>([
    ["controller-a", 5n * DOT_PLANCK],
    ["stash-b", 7n * DOT_PLANCK],
  ]);
  const scanned: string[] = [];
  const api = {
    query: {
      Staking: {
        Bonded: {
          getValue: async (stash: string) =>
            stash === "stash-a" ? "controller-a" : undefined,
        },
        Ledger: {
          getValue: async (key: string) => {
            scanned.push(key);
            const active = ledgers.get(key);
            return active === undefined ? undefined : { stash: key, active };
          },
          getEntries: async () => {
            throw new Error("fetchSelfStakes must not scan the whole map");
          },
        },
      },
    },
  } as unknown as ChainApi;

  const balances = await fetchSelfStakes(api, [
    "stash-a",
    "stash-b",
    "stash-missing",
  ]);

  assert.deepEqual([...scanned].sort(), [
    "controller-a",
    "stash-b",
    "stash-missing",
  ]);
  assert.deepEqual(
    balances.dots,
    new Map([
      ["stash-a", 5],
      ["stash-b", 7],
    ]),
  );
  assert.deepEqual(
    balances.planck,
    new Map([
      ["stash-a", 5n * DOT_PLANCK],
      ["stash-b", 7n * DOT_PLANCK],
    ]),
  );
});
