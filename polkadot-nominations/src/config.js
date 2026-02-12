import { readFileSync } from "node:fs";

const config = JSON.parse(readFileSync("config.json", "utf-8"));
const papiConfig = JSON.parse(readFileSync(".papi/polkadot-api.json", "utf-8"));

export const validator = config.validator;
export const percentiles = config.percentiles ?? [0.5, 0.75, 0.9];
export const minStakeDot = config.minStakeDot ?? 0;
export const rpcUrl = process.env.RPC_URL || papiConfig.entries.ah.wsUrl;

// DOT has 10 decimals
export const DOT_DECIMALS = 10n;
export const DOT_PLANCK = 10n ** DOT_DECIMALS;
