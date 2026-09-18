import assert from "node:assert/strict";
import test from "node:test";
import {
  describeError,
  formatDuration,
  formatError,
  phase,
  redactUrl,
} from "../src/log.js";

test("describeError unwraps the cause chain", () => {
  const root = new Error("socket closed");
  const wrapped = new Error("query failed", { cause: root });
  assert.equal(describeError(wrapped), "query failed <- socket closed");
  assert.equal(describeError("plain"), "plain");
  assert.equal(describeError(new Error("")), "Error");
});

test("describeError survives a self-referencing cause", () => {
  const looping = new Error("loops");
  Object.defineProperty(looping, "cause", { value: looping });
  assert.equal(describeError(looping), "loops");
});

test("formatError keeps the chain and every stack", () => {
  const root = new Error("socket closed");
  const wrapped = new Error("query failed", { cause: root });
  const formatted = formatError(wrapped);
  assert.ok(formatted.startsWith("query failed <- socket closed"));
  assert.ok(formatted.includes("Error: socket closed"));
});

test("redactUrl hides credentials carried in the path or query", () => {
  assert.equal(
    redactUrl("wss://api-asset-hub-polkadot.n.dwellir.com/secret-key-uuid"),
    "wss://api-asset-hub-polkadot.n.dwellir.com/<redacted>",
  );
  assert.equal(
    redactUrl("wss://node.example.com/?apiKey=secret"),
    "wss://node.example.com/<redacted>",
  );
  assert.equal(
    redactUrl("wss://asset-hub-polkadot-rpc.n.dwellir.com"),
    "wss://asset-hub-polkadot-rpc.n.dwellir.com",
  );
  assert.equal(redactUrl("not a url"), "<unparsable rpc url>");
});

test("formatDuration stays readable across scales", () => {
  assert.equal(formatDuration(250), "250ms");
  assert.equal(formatDuration(1_500), "1.5s");
  assert.equal(formatDuration(95_000), "1m35s");
});

test("phase returns the value and preserves the payload type", async () => {
  const value = await phase("typed", async () => ({ total: 3n }));
  assert.equal(value.total, 3n);
});

test("phase rethrows the original failure", async () => {
  const failure = new Error("scan rejected");
  await assert.rejects(
    phase("failing", async () => {
      throw failure;
    }),
    (error: unknown) => error === failure,
  );
});
