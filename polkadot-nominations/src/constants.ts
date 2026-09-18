export const DOT_DECIMALS = 10n;
export const DOT_PLANCK = 10n ** DOT_DECIMALS;
export const DEFAULT_RPC_URL = "wss://asset-hub-polkadot-rpc.n.dwellir.com";

export function planckToDot(planck: bigint): number {
  const whole = planck / DOT_PLANCK;
  const fraction = planck % DOT_PLANCK;
  const dot = Number(whole) + Number(fraction) / Number(DOT_PLANCK);
  return Math.round(dot * 10_000) / 10_000;
}
