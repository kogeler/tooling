export interface PercentileOptions {
  readonly descending?: boolean;
  readonly prefix?: string;
  readonly percentiles?: readonly number[];
}

export function calcPercentiles(
  values: readonly number[],
  {
    descending = false,
    prefix = "",
    percentiles = [0.5, 0.75, 0.9],
  }: PercentileOptions = {},
): Record<string, number> {
  if (values.length === 0) return {};

  const sorted = [...values].sort((left, right) =>
    descending ? right - left : left - right,
  );
  const result: Record<string, number> = {};
  for (const percentile of percentiles) {
    const index = percentile * (sorted.length - 1);
    const lowerIndex = Math.floor(index);
    const upperIndex = Math.ceil(index);
    const lower = sorted[lowerIndex];
    const upper = sorted[upperIndex];
    if (lower === undefined || upper === undefined) {
      throw new RangeError("Percentile index is outside the sorted sample");
    }

    const value =
      lowerIndex === upperIndex
        ? lower
        : lower + (upper - lower) * (index - lowerIndex);
    result[`${prefix}p${percentile * 100}`] =
      Math.round(value * 10_000) / 10_000;
  }
  return result;
}
