import { DEFAULT_RPC_URL } from "./constants.js";

const DEFAULT_PERCENTILES = [0.5, 0.75, 0.9] as const;

const DEFAULT_CONNECT_TIMEOUT_MS = 20_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 120_000;
const DEFAULT_REQUEST_RETRIES = 2;
const DEFAULT_MAX_CONCURRENCY = 16;
const DEFAULT_PROGRESS_INTERVAL_MS = 10_000;

export interface Config {
  readonly validators: readonly string[];
  readonly percentiles: readonly number[];
  readonly minStakeDot: number;
  readonly rewardEras: number;
  readonly rpcUrl: string;
  readonly connectTimeoutMs: number;
  readonly requestTimeoutMs: number;
  readonly requestRetries: number;
  readonly maxConcurrency: number;
  readonly progressIntervalMs: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyStringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item: unknown) => typeof item === "string" && item.trim() !== "")
  );
}

function isPercentileArray(value: unknown): value is number[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (item: unknown) =>
        typeof item === "number" && Number.isFinite(item) && item >= 0 && item <= 1,
    )
  );
}

function parseCount(
  value: unknown,
  fallback: number,
  name: string,
  minimum: number,
): number {
  const candidate = value ?? fallback;
  if (
    typeof candidate !== "number" ||
    !Number.isInteger(candidate) ||
    candidate < minimum
  ) {
    throw new Error(`${name} must be an integer of at least ${minimum}`);
  }
  return candidate;
}

export function parseConfig(value: unknown): Config {
  if (!isRecord(value)) {
    throw new Error("config.json must contain a JSON object");
  }

  if (!isNonEmptyStringArray(value.validators)) {
    throw new Error('config.json must contain a non-empty "validators" array');
  }
  const validators = [...value.validators];
  if (new Set(validators).size !== validators.length) {
    throw new Error("validators must not contain duplicate addresses");
  }

  const percentilesValue = value.percentiles ?? DEFAULT_PERCENTILES;
  if (!isPercentileArray(percentilesValue)) {
    throw new Error("percentiles must be a non-empty array of numbers from 0 to 1");
  }
  const percentiles = [...percentilesValue];

  const minStakeDot = value.minStakeDot ?? 0;
  if (
    typeof minStakeDot !== "number" ||
    !Number.isFinite(minStakeDot) ||
    minStakeDot < 0
  ) {
    throw new Error("minStakeDot must be a non-negative number");
  }

  const rewardEras = value.rewardEras ?? 20;
  if (
    typeof rewardEras !== "number" ||
    !Number.isInteger(rewardEras) ||
    rewardEras < 0
  ) {
    throw new Error("rewardEras must be a non-negative integer");
  }

  const rpcUrl = value.rpcUrl ?? DEFAULT_RPC_URL;
  if (typeof rpcUrl !== "string" || rpcUrl.trim() === "") {
    throw new Error("rpcUrl must be a non-empty string");
  }

  return {
    validators,
    percentiles,
    minStakeDot,
    rewardEras,
    rpcUrl,
    connectTimeoutMs: parseCount(
      value.connectTimeoutMs,
      DEFAULT_CONNECT_TIMEOUT_MS,
      "connectTimeoutMs",
      1_000,
    ),
    requestTimeoutMs: parseCount(
      value.requestTimeoutMs,
      DEFAULT_REQUEST_TIMEOUT_MS,
      "requestTimeoutMs",
      1_000,
    ),
    requestRetries: parseCount(
      value.requestRetries,
      DEFAULT_REQUEST_RETRIES,
      "requestRetries",
      0,
    ),
    maxConcurrency: parseCount(
      value.maxConcurrency,
      DEFAULT_MAX_CONCURRENCY,
      "maxConcurrency",
      1,
    ),
    progressIntervalMs: parseCount(
      value.progressIntervalMs,
      DEFAULT_PROGRESS_INTERVAL_MS,
      "progressIntervalMs",
      0,
    ),
  };
}
