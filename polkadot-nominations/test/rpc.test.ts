import assert from "node:assert/strict";
import test from "node:test";
import {
  createRpcRunner,
  delay,
  directRunner,
  RpcRequestError,
  RpcTimeoutError,
  startProgress,
  withTimeout,
} from "../src/rpc.js";

const NO_RETRY = { requestTimeoutMs: 50, requestRetries: 0, maxConcurrency: 4 };

test("withTimeout resolves fast calls and rejects stalled ones", async () => {
  assert.equal(await withTimeout("fast", 1_000, async () => "value"), "value");

  await assert.rejects(
    withTimeout("stalled", 20, () => new Promise(() => undefined)),
    (error: unknown) =>
      error instanceof RpcTimeoutError && /stalled/.test(error.message),
  );
});

test("withTimeout without a deadline passes the call through", async () => {
  assert.equal(await withTimeout("no deadline", 0, async () => 7), 7);
});

test("runner retries a failing call and reports attempts", async () => {
  const retries: number[] = [];
  const runner = createRpcRunner(
    { requestTimeoutMs: 1_000, requestRetries: 2, maxConcurrency: 1 },
    { onRetry: (_operation, attempt) => retries.push(attempt) },
  );

  let calls = 0;
  const value = await runner.run("flaky", async () => {
    calls += 1;
    if (calls < 3) throw new Error("temporary failure");
    return "ok";
  });

  assert.equal(value, "ok");
  assert.equal(calls, 3);
  assert.deepEqual(retries, [1, 2]);
  assert.equal(runner.stats().completed, 1);
  assert.equal(runner.stats().retried, 2);
  assert.equal(runner.stats().failed, 0);
});

test("runner gives up with the underlying error as the cause", async () => {
  const runner = createRpcRunner(NO_RETRY);
  await assert.rejects(
    runner.run("always broken", async () => {
      throw new Error("endpoint rejected the request");
    }),
    (error: unknown) =>
      error instanceof RpcRequestError &&
      /always broken/.test(error.message) &&
      /endpoint rejected the request/.test(error.message),
  );
  assert.equal(runner.stats().failed, 1);
  assert.equal(runner.stats().inFlight, 0);
});

test("runner turns a hanging call into a timeout instead of waiting forever", async () => {
  const runner = createRpcRunner(NO_RETRY);
  await assert.rejects(
    runner.run("hanging", () => new Promise(() => undefined)),
    (error: unknown) =>
      error instanceof RpcRequestError &&
      error.cause instanceof RpcTimeoutError,
  );
});

test("runner never exceeds the configured concurrency", async () => {
  const runner = createRpcRunner({
    requestTimeoutMs: 1_000,
    requestRetries: 0,
    maxConcurrency: 3,
  });

  let active = 0;
  let peak = 0;
  await Promise.all(
    Array.from({ length: 12 }, (_value, index) =>
      runner.run(`call-${index}`, async () => {
        active += 1;
        peak = Math.max(peak, active);
        await delay(5);
        active -= 1;
        return index;
      }),
    ),
  );

  assert.equal(peak, 3);
  assert.equal(runner.stats().completed, 12);
  assert.equal(runner.stats().inFlight, 0);
  assert.equal(runner.stats().queued, 0);
});

test("directRunner passes calls through unchanged", async () => {
  assert.equal(await directRunner.run("direct", async () => 42), 42);
});

test("startProgress reports in-flight work and flags a stall", async () => {
  const messages: string[] = [];
  const runner = createRpcRunner({
    requestTimeoutMs: 1_000,
    requestRetries: 0,
    maxConcurrency: 2,
  });

  const pending = runner.run("slow", () => delay(60));
  const stop = startProgress({
    label: "test",
    intervalMs: 10,
    runner,
    log: (message) => messages.push(message),
  });
  await pending;
  stop();

  assert.ok(messages.length > 0);
  assert.ok(messages.some((message) => message.includes("in flight 1")));
  assert.ok(
    messages.some((message) => message.includes("no response since last tick")),
  );

  messages.length = 0;
  await delay(30);
  assert.equal(messages.length, 0, "stop() must silence the reporter");
});

test("startProgress is disabled by a non-positive interval", async () => {
  const messages: string[] = [];
  const stop = startProgress({
    label: "test",
    intervalMs: 0,
    runner: directRunner,
    log: (message) => messages.push(message),
  });
  await delay(20);
  stop();
  assert.deepEqual(messages, []);
});
