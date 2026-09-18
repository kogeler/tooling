import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parseConfig } from "./config-schema.js";
export { DOT_DECIMALS, DOT_PLANCK } from "./constants.js";

const configPath = resolve(process.env.CONFIG_PATH || "config.json");
const config = parseConfig(JSON.parse(readFileSync(configPath, "utf-8")));

export const {
  validators,
  percentiles,
  minStakeDot,
  rewardEras,
  connectTimeoutMs,
  requestTimeoutMs,
  requestRetries,
  maxConcurrency,
  progressIntervalMs,
} = config;
export const rpcUrl = process.env.RPC_URL || config.rpcUrl;
