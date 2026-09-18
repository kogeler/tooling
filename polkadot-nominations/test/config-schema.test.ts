import assert from "node:assert/strict";
import test from "node:test";
import { parseConfig } from "../src/config-schema.js";

test("parseConfig applies defaults to a validator list", () => {
  assert.deepEqual(parseConfig({ validators: ["one", "two"] }), {
    validators: ["one", "two"],
    percentiles: [0.5, 0.75, 0.9],
    minStakeDot: 0,
    rewardEras: 20,
    rpcUrl: "wss://asset-hub-polkadot-rpc.n.dwellir.com",
    connectTimeoutMs: 20_000,
    requestTimeoutMs: 120_000,
    requestRetries: 2,
    maxConcurrency: 16,
    progressIntervalMs: 10_000,
  });
});

test("parseConfig validates lists and numeric settings", () => {
  assert.throws(() => parseConfig({ validator: "one" }), /validators/);
  assert.throws(() => parseConfig({ validators: [] }), /validators/);
  assert.throws(
    () => parseConfig({ validators: ["one", "one"] }),
    /duplicate/,
  );
  assert.throws(
    () => parseConfig({ validators: ["one"], percentiles: [1.1] }),
    /percentiles/,
  );
  assert.throws(
    () => parseConfig({ validators: ["one"], minStakeDot: -1 }),
    /minStakeDot/,
  );
  assert.throws(
    () => parseConfig({ validators: ["one"], rewardEras: 1.5 }),
    /rewardEras/,
  );
  assert.throws(
    () => parseConfig({ validators: ["one"], rpcUrl: "" }),
    /rpcUrl/,
  );
});

test("parseConfig validates resilience settings", () => {
  assert.throws(
    () => parseConfig({ validators: ["one"], connectTimeoutMs: 10 }),
    /connectTimeoutMs/,
  );
  assert.throws(
    () => parseConfig({ validators: ["one"], requestTimeoutMs: 1.5 }),
    /requestTimeoutMs/,
  );
  assert.throws(
    () => parseConfig({ validators: ["one"], requestRetries: -1 }),
    /requestRetries/,
  );
  assert.throws(
    () => parseConfig({ validators: ["one"], maxConcurrency: 0 }),
    /maxConcurrency/,
  );
  assert.throws(
    () => parseConfig({ validators: ["one"], progressIntervalMs: -1 }),
    /progressIntervalMs/,
  );

  const config = parseConfig({
    validators: ["one"],
    connectTimeoutMs: 5_000,
    requestTimeoutMs: 30_000,
    requestRetries: 0,
    maxConcurrency: 4,
    progressIntervalMs: 0,
  });
  assert.equal(config.connectTimeoutMs, 5_000);
  assert.equal(config.requestTimeoutMs, 30_000);
  assert.equal(config.requestRetries, 0);
  assert.equal(config.maxConcurrency, 4);
  assert.equal(config.progressIntervalMs, 0);
});
