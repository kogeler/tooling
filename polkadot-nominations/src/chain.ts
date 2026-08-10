import { createClient } from "polkadot-api";
import { getWsProvider } from "polkadot-api/ws";
import { ah } from "@polkadot-api/descriptors";
import WebSocket from "ws";
import { rpcUrl } from "./config.js";
import { planckToDot } from "./constants.js";

const PERBILL = 1_000_000_000;

export function connect() {
  const provider = getWsProvider(rpcUrl, {
    websocketClass: WebSocket as unknown as typeof globalThis.WebSocket,
  });
  const client = createClient(provider);
  const api = client.getTypedApi(ah);
  return { client, api };
}

export type ChainApi = ReturnType<typeof connect>["api"];

export async function fetchCommissions(
  api: ChainApi,
): Promise<Map<string, number>> {
  const entries = await api.query.Staking.Validators.getEntries();
  const commissions = new Map<string, number>();
  for (const entry of entries) {
    const percentage = (entry.value.commission / PERBILL) * 100;
    commissions.set(
      entry.keyArgs[0],
      Math.round(percentage * 10_000) / 10_000,
    );
  }
  return commissions;
}

export async function fetchActiveValidators(api: ChainApi): Promise<string[]> {
  const era = await api.query.Staking.ActiveEra.getValue();
  if (era === undefined) {
    throw new Error("Staking.ActiveEra is unavailable");
  }
  const entries = await api.query.Staking.ErasStakersOverview.getEntries(
    era.index,
  );
  return entries.map(
    (entry: { readonly keyArgs: readonly [number, string] }) => entry.keyArgs[1],
  );
}

export async function fetchNominators(api: ChainApi) {
  return api.query.Staking.Nominators.getEntries();
}

export interface StakeBalances {
  readonly dots: ReadonlyMap<string, number>;
  readonly planck: ReadonlyMap<string, bigint>;
}

export async function fetchStakeBalances(
  api: ChainApi,
): Promise<StakeBalances> {
  const entries = await api.query.Staking.Ledger.getEntries();
  const dots = new Map<string, number>();
  const planck = new Map<string, bigint>();
  for (const entry of entries) {
    dots.set(entry.value.stash, planckToDot(entry.value.active));
    planck.set(entry.value.stash, entry.value.active);
  }
  return { dots, planck };
}

export async function fetchStakes(api: ChainApi): Promise<Map<string, number>> {
  return new Map((await fetchStakeBalances(api)).dots);
}
