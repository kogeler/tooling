import assert from "node:assert/strict";
import test from "node:test";
import { calcPercentiles } from "../src/stats.js";

test("calcPercentiles interpolates in ascending and descending order", () => {
  assert.deepEqual(
    calcPercentiles([0, 10, 20], { percentiles: [0.25, 0.5, 0.75] }),
    { p25: 5, p50: 10, p75: 15 },
  );
  assert.deepEqual(
    calcPercentiles([1, 5, 10], {
      descending: true,
      prefix: "commission_",
      percentiles: [0.25, 0.5],
    }),
    { commission_p25: 7.5, commission_p50: 5 },
  );
});

test("calcPercentiles returns no fields for an empty sample", () => {
  assert.deepEqual(calcPercentiles([], { percentiles: [0.5] }), {});
});
