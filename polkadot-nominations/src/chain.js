import { createClient } from "polkadot-api";
import { getWsProvider } from "polkadot-api/ws-provider";
import { ah } from "@polkadot-api/descriptors";
import WebSocket from "ws";
import { rpcUrl, DOT_PLANCK } from "./config.js";

const PERBILL = 1_000_000_000;

export function connect() {
  const provider = getWsProvider(rpcUrl, { websocketClass: WebSocket });
  const client = createClient(provider);
  const api = client.getTypedApi(ah);
  return { client, api };
}

export async function fetchCommissions(api) {
  const entries = await api.query.Staking.Validators.getEntries();
  const map = new Map();
  for (const entry of entries) {
    const pct = (entry.value.commission / PERBILL) * 100;
    map.set(entry.keyArgs[0], Math.round(pct * 1e4) / 1e4);
  }
  return map;
}

export async function fetchNominators(api) {
  return api.query.Staking.Nominators.getEntries();
}

export async function fetchStakes(api) {
  const entries = await api.query.Staking.Ledger.getEntries();
  const map = new Map();
  for (const entry of entries) {
    const stash = entry.value.stash;
    const activeDot = Number(entry.value.active / DOT_PLANCK);
    map.set(stash, activeDot);
  }
  return map;
}
