import assert from "node:assert/strict";
import test from "node:test";
import { parseConfig } from "../src/config-schema.js";

test("parseConfig applies defaults to a validator list", () => {
  assert.deepEqual(parseConfig({ validators: ["one", "two"] }), {
    validators: ["one", "two"],
    percentiles: [0.5, 0.75, 0.9],
    minStakeDot: 0,
    rewardEras: 20,
    rpcUrl: "wss://rpc-assethub.novasama-tech.org",
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
