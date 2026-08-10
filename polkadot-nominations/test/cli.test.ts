import assert from "node:assert/strict";
import test from "node:test";
import { parseCliArgs } from "../src/cli.js";

test("parseCliArgs recognizes every command", () => {
  assert.equal(parseCliArgs(["--nominations"]), "nominations");
  assert.equal(parseCliArgs(["--self-stake-stats"]), "self-stake-stats");
  assert.equal(parseCliArgs(["--validator-info"]), "validator-info");
  assert.equal(parseCliArgs(["--help"]), "help");
  assert.equal(parseCliArgs(["-h"]), "help");
});

test("parseCliArgs rejects missing, unknown, and multiple commands", () => {
  assert.throws(() => parseCliArgs([]), /Exactly one command is required/);
  assert.throws(() => parseCliArgs(["--unknown"]), /Exactly one command/);
  assert.throws(
    () => parseCliArgs(["--nominations", "--validator-info"]),
    /Exactly one command/,
  );
});
