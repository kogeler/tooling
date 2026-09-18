import { createClient } from "polkadot-api";
import { getWsProvider } from "polkadot-api/ws";
import { ah } from "@polkadot-api/descriptors";
import WebSocket from "ws";
import { connectTimeoutMs, rpcUrl } from "./config.js";
import { planckToDot } from "./constants.js";
import { log as defaultLog } from "./log.js";
import { directRunner, withTimeout } from "./rpc.js";
import type { RpcRunner } from "./rpc.js";

const PERBILL = 1_000_000_000;

/** Socket logger events that only add noise to the operational log. */
const IGNORED_SOCKET_EVENTS = new Set(["IN", "OUT"]);

export interface ConnectOptions {
  readonly log: (message: string) => void;
}

export function connect({ log = defaultLog }: Partial<ConnectOptions> = {}) {
  const provider = getWsProvider(rpcUrl, {
    websocketClass: WebSocket as unknown as typeof globalThis.WebSocket,
    timeout: connectTimeoutMs,
    onStatusChanged: (status) => {
      log(`RPC socket: ${String(status.type)}`);
    },
    logger: (event) => {
      // String() drops the enum type so this keeps compiling across
      // polkadot-api versions that reshuffle the SocketEvents members.
      const type = String(event.type);
      if (IGNORED_SOCKET_EVENTS.has(type)) return;
      log(`RPC socket event: ${type}`);
    },
  });
  const client = createClient(provider);
  const api = client.getTypedApi(ah);
  return { client, api };
}

export type ChainApi = ReturnType<typeof connect>["api"];

/**
 * Fails fast on an endpoint that accepts the socket but never answers, instead
 * of letting the first real query hang forever.
 */
export async function probeConnection(
  api: ChainApi,
  timeoutMs: number,
): Promise<number | null> {
  const era = await withTimeout("probe Staking.ActiveEra", timeoutMs, () =>
    api.query.Staking.ActiveEra.getValue(),
  );
  return era?.index ?? null;
}

export async function fetchCommissions(
  api: ChainApi,
  runner: RpcRunner = directRunner,
): Promise<Map<string, number>> {
  const entries = await runner.run("Staking.Validators.getEntries", () =>
    api.query.Staking.Validators.getEntries(),
  );
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

export async function fetchActiveValidators(
  api: ChainApi,
  runner: RpcRunner = directRunner,
): Promise<string[]> {
  const era = await runner.run("Staking.ActiveEra.getValue", () =>
    api.query.Staking.ActiveEra.getValue(),
  );
  if (era === undefined) {
    throw new Error("Staking.ActiveEra is unavailable");
  }
  const entries = await runner.run(
    "Staking.ErasStakersOverview.getEntries",
    () => api.query.Staking.ErasStakersOverview.getEntries(era.index),
  );
  return entries.map(
    (entry: { readonly keyArgs: readonly [number, string] }) => entry.keyArgs[1],
  );
}

export async function fetchNominators(
  api: ChainApi,
  runner: RpcRunner = directRunner,
) {
  return runner.run("Staking.Nominators.getEntries", () =>
    api.query.Staking.Nominators.getEntries(),
  );
}

export interface StakeBalances {
  readonly dots: ReadonlyMap<string, number>;
  readonly planck: ReadonlyMap<string, bigint>;
}

/** Full ledger scan; only the network-wide modes need every stash. */
export async function fetchStakeBalances(
  api: ChainApi,
  runner: RpcRunner = directRunner,
): Promise<StakeBalances> {
  const entries = await runner.run("Staking.Ledger.getEntries", () =>
    api.query.Staking.Ledger.getEntries(),
  );
  const dots = new Map<string, number>();
  const planck = new Map<string, bigint>();
  for (const entry of entries) {
    dots.set(entry.value.stash, planckToDot(entry.value.active));
    planck.set(entry.value.stash, entry.value.active);
  }
  return { dots, planck };
}

/**
 * Self-stake for a known set of stashes without scanning the whole Ledger map.
 * Ledger is keyed by controller, so each stash is resolved through Bonded and
 * falls back to a direct lookup for runtimes where stash == controller.
 */
export async function fetchSelfStakes(
  api: ChainApi,
  stashes: readonly string[],
  runner: RpcRunner = directRunner,
): Promise<StakeBalances> {
  const dots = new Map<string, number>();
  const planck = new Map<string, bigint>();

  const resolved = await Promise.all(
    stashes.map(async (stash) => {
      const controller = await runner.run(
        `Staking.Bonded.getValue(${stash})`,
        () => api.query.Staking.Bonded.getValue(stash),
      );
      const ledgerKey = controller ?? stash;
      const ledger = await runner.run(
        `Staking.Ledger.getValue(${ledgerKey})`,
        () => api.query.Staking.Ledger.getValue(ledgerKey),
      );
      return [stash, ledger?.active] as const;
    }),
  );

  for (const [stash, active] of resolved) {
    if (active === undefined) continue;
    dots.set(stash, planckToDot(active));
    planck.set(stash, active);
  }
  return { dots, planck };
}

export async function fetchStakes(
  api: ChainApi,
  runner: RpcRunner = directRunner,
): Promise<Map<string, number>> {
  return new Map((await fetchStakeBalances(api, runner)).dots);
}
