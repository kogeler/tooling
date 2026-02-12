import { percentiles as configPercentiles } from "./config.js";

export function calcPercentiles(
  values,
  { descending = false, prefix = "" } = {},
) {
  if (values.length === 0) return {};
  const sorted = descending
    ? [...values].sort((a, b) => b - a)
    : [...values].sort((a, b) => a - b);
  const result = {};
  for (const p of configPercentiles) {
    const idx = p * (sorted.length - 1);
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    const val =
      lo === hi
        ? sorted[lo]
        : sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
    result[`${prefix}p${p * 100}`] = Math.round(val * 1e4) / 1e4;
  }
  return result;
}
