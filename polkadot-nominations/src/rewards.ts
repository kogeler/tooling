import type { ChainApi } from "./chain.js";
import { DOT_PLANCK, planckToDot } from "./constants.js";
import { directRunner } from "./rpc.js";
import type { RpcRunner } from "./rpc.js";

const PERBILL = 1_000_000_000n;
const BELOW_OPTIMUM_STEP = 2_500n * DOT_PLANCK;
const ABOVE_OPTIMUM_STEP = 10_000n * DOT_PLANCK;

export interface EraReward {
  readonly era: number;
  readonly active: boolean;
  readonly has_data: boolean;
  readonly reward: number;
  readonly commission_reward: number;
  readonly own_stake_reward: number;
  readonly validator_incentive_reward: number;
  readonly reward_planck: string;
  readonly commission_pct: number | null;
  readonly own_stake: number;
  readonly total_stake: number;
  readonly claimed: boolean;
  readonly claimed_pages: number;
  readonly total_pages: number;
}

export interface ValidatorRewards {
  readonly eras_requested: number;
  readonly active_era: number | null;
  readonly is_validator: boolean;
  readonly current_commission_pct: number | null;
  readonly latest_era_commission_pct: number | null;
  readonly reward_collapse_pending: boolean;
  readonly total_reward: number;
  readonly unclaimed_reward: number;
  readonly eras: readonly EraReward[];
}

export interface RewardCurveParameters {
  readonly minimumSelfStake: bigint;
  readonly optimumSelfStake: bigint;
  readonly hardCapSelfStake: bigint;
  readonly slopeFactor: bigint;
}

export interface RewardProjectionScenario {
  readonly self_stake: number;
  readonly additional_self_stake: number;
  readonly estimated_total_reward: number;
  readonly estimated_average_reward_per_era: number;
  readonly estimated_total_commission_reward: number;
  readonly estimated_total_own_stake_reward: number;
  readonly estimated_total_validator_incentive_reward: number;
  readonly increase_pct: number | null;
}

export interface RewardProjection {
  readonly basis: {
    readonly eras_requested: number;
    readonly eras_used: number;
    readonly current_self_stake: number;
    readonly actual_total_reward: number;
    readonly actual_average_reward_per_era: number;
  };
  readonly curve: {
    readonly minimum_self_stake: number;
    readonly optimum_self_stake: number;
    readonly hard_cap_self_stake: number;
    readonly slope_factor: number;
  };
  readonly scenarios: readonly RewardProjectionScenario[];
}

export interface ValidatorRewardReport {
  readonly rewards: ValidatorRewards;
  readonly reward_projection: RewardProjection;
}

interface ExposureOverview {
  readonly own: bigint;
  readonly total: bigint;
  readonly page_count: number;
}

interface EraRewardComputation {
  readonly output: EraReward;
  readonly rewardPlanck: bigint;
  readonly eraPayout: bigint | undefined;
  readonly overview: ExposureOverview | undefined;
  readonly totalPoints: bigint;
  readonly validatorPoints: bigint;
  readonly commission: bigint;
  readonly incentiveBudget: bigint;
  readonly incentiveWeight: bigint;
  readonly totalIncentiveWeight: bigint;
}

export interface ValidatorRewardHistory {
  readonly rewards: ValidatorRewards;
  readonly computations: readonly EraRewardComputation[];
}

interface RewardParts {
  readonly commission: bigint;
  readonly ownStake: bigint;
  readonly incentive: bigint;
}

function perbillFromRational(numerator: bigint, denominator: bigint): bigint {
  if (numerator <= 0n || denominator <= 0n) return 0n;
  const parts = (numerator * PERBILL) / denominator;
  return parts > PERBILL ? PERBILL : parts;
}

function perbillMulFloor(parts: bigint, value: bigint): bigint {
  return (value * parts) / PERBILL;
}

function ratioMulFloor(
  value: bigint,
  numerator: bigint,
  denominator: bigint,
): bigint {
  return perbillMulFloor(
    perbillFromRational(numerator, denominator),
    value,
  );
}

function integerSquareRoot(value: bigint): bigint {
  if (value < 0n) throw new Error("Cannot calculate the square root of a negative value");
  if (value < 2n) return value;

  let estimate = value;
  let next = (estimate + 1n) / 2n;
  while (next < estimate) {
    estimate = next;
    next = (estimate + value / estimate) / 2n;
  }
  return estimate;
}

export function calculateValidatorIncentiveWeight(
  selfStake: bigint,
  parameters: RewardCurveParameters,
): bigint {
  const { optimumSelfStake, hardCapSelfStake, slopeFactor } = parameters;
  if (selfStake <= 0n || (optimumSelfStake === 0n && hardCapSelfStake === 0n)) {
    return 0n;
  }
  if (optimumSelfStake > hardCapSelfStake) {
    throw new Error("Optimum self-stake cannot exceed the hard cap");
  }
  if (slopeFactor < 0n || slopeFactor > PERBILL) {
    throw new Error("Self-stake slope factor must be a Perbill value");
  }

  if (selfStake <= optimumSelfStake) {
    return integerSquareRoot(selfStake);
  }

  const effectiveStake = selfStake <= hardCapSelfStake
    ? selfStake
    : hardCapSelfStake;
  const slopeSquared = perbillMulFloor(slopeFactor, slopeFactor);
  const excess = effectiveStake - optimumSelfStake;
  const dampenedExcess = perbillMulFloor(slopeSquared, excess);
  return integerSquareRoot(optimumSelfStake + dampenedExcess);
}

function addTarget(targets: Set<bigint>, value: bigint, cap: bigint): void {
  if (value > 0n && value <= cap) targets.add(value);
}

export function buildSelfStakeScenarioTargets(
  currentSelfStake: bigint,
  parameters: RewardCurveParameters,
): bigint[] {
  const { minimumSelfStake, optimumSelfStake, hardCapSelfStake } = parameters;
  if (hardCapSelfStake <= 0n) return [];
  if (optimumSelfStake > hardCapSelfStake) {
    throw new Error("Optimum self-stake cannot exceed the hard cap");
  }

  const targets = new Set<bigint>();
  if (minimumSelfStake <= optimumSelfStake) {
    for (
      let target = minimumSelfStake;
      target < optimumSelfStake;
      target += BELOW_OPTIMUM_STEP
    ) {
      addTarget(targets, target, hardCapSelfStake);
    }
  }
  addTarget(targets, optimumSelfStake, hardCapSelfStake);

  for (
    let target = optimumSelfStake + ABOVE_OPTIMUM_STEP;
    target < hardCapSelfStake;
    target += ABOVE_OPTIMUM_STEP
  ) {
    addTarget(targets, target, hardCapSelfStake);
  }
  addTarget(targets, hardCapSelfStake, hardCapSelfStake);

  return [...targets]
    .filter((target) => target > currentSelfStake)
    .sort((left, right) => (left < right ? -1 : left > right ? 1 : 0));
}

function calculateStakerRewardParts(
  validatorTotalPayout: bigint,
  commission: bigint,
  ownStake: bigint,
  totalExposure: bigint,
): Pick<RewardParts, "commission" | "ownStake"> {
  const commissionPayout = perbillMulFloor(commission, validatorTotalPayout);
  const leftover = validatorTotalPayout - commissionPayout;
  const ownStakePayout = ratioMulFloor(leftover, ownStake, totalExposure);
  return { commission: commissionPayout, ownStake: ownStakePayout };
}

function calculateIncentivePayout(
  budget: bigint,
  validatorWeight: bigint,
  totalWeight: bigint,
): bigint {
  return ratioMulFloor(budget, validatorWeight, totalWeight);
}

async function fetchEraRewardComputation(
  api: ChainApi,
  validator: string,
  era: number,
  runner: RpcRunner = directRunner,
): Promise<EraRewardComputation> {
  const [
    overview,
    eraPayout,
    points,
    preferences,
    claimedPages,
    incentiveBudget,
    incentiveWeight,
    totalIncentiveWeight,
  ] = await Promise.all([
    runner.run(`Staking.ErasStakersOverview.getValue(${era})`, () =>
      api.query.Staking.ErasStakersOverview.getValue(era, validator),
    ),
    runner.run(`Staking.ErasValidatorReward.getValue(${era})`, () =>
      api.query.Staking.ErasValidatorReward.getValue(era),
    ),
    runner.run(`Staking.ErasRewardPoints.getValue(${era})`, () =>
      api.query.Staking.ErasRewardPoints.getValue(era),
    ),
    runner.run(`Staking.ErasValidatorPrefs.getValue(${era})`, () =>
      api.query.Staking.ErasValidatorPrefs.getValue(era, validator),
    ),
    runner.run(`Staking.ClaimedRewards.getValue(${era})`, () =>
      api.query.Staking.ClaimedRewards.getValue(era, validator),
    ),
    runner.run(`Staking.ErasValidatorIncentiveBudget.getValue(${era})`, () =>
      api.query.Staking.ErasValidatorIncentiveBudget.getValue(era),
    ),
    runner.run(`Staking.ErasValidatorIncentiveWeight.getValue(${era})`, () =>
      api.query.Staking.ErasValidatorIncentiveWeight.getValue(era, validator),
    ),
    runner.run(`Staking.ErasSumValidatorIncentiveWeight.getValue(${era})`, () =>
      api.query.Staking.ErasSumValidatorIncentiveWeight.getValue(era),
    ),
  ]);

  const totalPoints = BigInt(points?.total ?? 0);
  const validatorPointEntry = (points?.individual ?? []).find(
    ([address]: readonly [string, number]) => address === validator,
  );
  const validatorPoints = BigInt(validatorPointEntry?.[1] ?? 0);
  const commission = BigInt(preferences?.commission ?? 0);
  const budget = incentiveBudget ?? 0n;
  const weight = incentiveWeight ?? 0n;
  const sumWeight = totalIncentiveWeight ?? 0n;

  if (eraPayout === undefined || overview === undefined) {
    return {
      output: {
        era,
        active: false,
        has_data: eraPayout !== undefined,
        reward: 0,
        commission_reward: 0,
        own_stake_reward: 0,
        validator_incentive_reward: 0,
        reward_planck: "0",
        commission_pct: null,
        own_stake: 0,
        total_stake: 0,
        claimed: false,
        claimed_pages: 0,
        total_pages: overview?.page_count ?? 0,
      },
      rewardPlanck: 0n,
      eraPayout,
      overview,
      totalPoints,
      validatorPoints,
      commission,
      incentiveBudget: budget,
      incentiveWeight: weight,
      totalIncentiveWeight: sumWeight,
    };
  }

  const claimed: readonly number[] = claimedPages ?? [];
  const totalPages = overview.page_count;
  const fullyClaimed = totalPages > 0 && claimed.length >= totalPages;
  let parts: RewardParts = { commission: 0n, ownStake: 0n, incentive: 0n };

  if (totalPoints > 0n && validatorPoints > 0n) {
    const validatorTotalPayout = ratioMulFloor(
      eraPayout,
      validatorPoints,
      totalPoints,
    );
    const stakerParts = calculateStakerRewardParts(
      validatorTotalPayout,
      commission,
      overview.own,
      overview.total,
    );
    parts = {
      ...stakerParts,
      incentive: calculateIncentivePayout(budget, weight, sumWeight),
    };
  }

  const rewardPlanck = parts.commission + parts.ownStake + parts.incentive;
  return {
    output: {
      era,
      active: true,
      has_data: true,
      reward: planckToDot(rewardPlanck),
      commission_reward: planckToDot(parts.commission),
      own_stake_reward: planckToDot(parts.ownStake),
      validator_incentive_reward: planckToDot(parts.incentive),
      reward_planck: rewardPlanck.toString(),
      commission_pct: Number(commission) / 10_000_000,
      own_stake: planckToDot(overview.own),
      total_stake: planckToDot(overview.total),
      claimed: fullyClaimed,
      claimed_pages: claimed.length,
      total_pages: totalPages,
    },
    rewardPlanck,
    eraPayout,
    overview,
    totalPoints,
    validatorPoints,
    commission,
    incentiveBudget: budget,
    incentiveWeight: weight,
    totalIncentiveWeight: sumWeight,
  };
}

export async function computeEraReward(
  api: ChainApi,
  validator: string,
  era: number,
  runner: RpcRunner = directRunner,
): Promise<EraReward> {
  return (await fetchEraRewardComputation(api, validator, era, runner)).output;
}

function buildValidatorRewards(
  rewardEras: number,
  activeEraIndex: number | null,
  isValidator: boolean,
  currentCommissionPct: number | null,
  computations: readonly EraRewardComputation[],
): ValidatorRewards {
  let totalReward = 0n;
  let unclaimedReward = 0n;
  for (const computation of computations) {
    totalReward += computation.rewardPlanck;
    if (computation.output.active && !computation.output.claimed) {
      unclaimedReward += computation.rewardPlanck;
    }
  }

  const eras = computations.map(({ output }) => output);
  const latest = eras.find((era) => era.active);
  const rewardCollapsePending =
    latest !== undefined &&
    latest.commission_pct !== null &&
    currentCommissionPct !== null &&
    latest.commission_reward > 0 &&
    latest.commission_pct > currentCommissionPct &&
    latest.own_stake_reward === 0 &&
    latest.validator_incentive_reward === 0;

  return {
    eras_requested: rewardEras,
    active_era: activeEraIndex,
    is_validator: isValidator,
    current_commission_pct: currentCommissionPct,
    latest_era_commission_pct: latest?.commission_pct ?? null,
    reward_collapse_pending: rewardCollapsePending,
    total_reward: planckToDot(totalReward),
    unclaimed_reward: planckToDot(unclaimedReward),
    eras,
  };
}

export async function fetchValidatorRewardHistory(
  api: ChainApi,
  validator: string,
  rewardEras: number,
  runner: RpcRunner = directRunner,
): Promise<ValidatorRewardHistory> {
  const [activeEra, currentPreferences] = await Promise.all([
    runner.run("Staking.ActiveEra.getValue", () =>
      api.query.Staking.ActiveEra.getValue(),
    ),
    runner.run(`Staking.Validators.getValue(${validator})`, () =>
      api.query.Staking.Validators.getValue(validator),
    ),
  ]);
  const currentCommissionPct =
    currentPreferences === undefined
      ? null
      : Number(currentPreferences.commission) / 10_000_000;

  if (activeEra === undefined) {
    return {
      rewards: buildValidatorRewards(
        rewardEras,
        null,
        currentPreferences !== undefined,
        currentCommissionPct,
        [],
      ),
      computations: [],
    };
  }

  const eraIndices: number[] = [];
  for (
    let era = activeEra.index - 1;
    era >= 0 && eraIndices.length < rewardEras;
    era--
  ) {
    eraIndices.push(era);
  }
  const computations = await Promise.all(
    eraIndices.map((era) =>
      fetchEraRewardComputation(api, validator, era, runner),
    ),
  );

  return {
    rewards: buildValidatorRewards(
      rewardEras,
      activeEra.index,
      currentPreferences !== undefined,
      currentCommissionPct,
      computations,
    ),
    computations,
  };
}

export async function fetchValidatorRewards(
  api: ChainApi,
  validator: string,
  rewardEras: number,
  runner: RpcRunner = directRunner,
): Promise<ValidatorRewards> {
  return (
    await fetchValidatorRewardHistory(api, validator, rewardEras, runner)
  ).rewards;
}

export async function fetchRewardCurveParameters(
  api: ChainApi,
  runner: RpcRunner = directRunner,
): Promise<RewardCurveParameters> {
  const [minimumSelfStake, optimumSelfStake, hardCapSelfStake, slopeFactor] =
    await Promise.all([
      runner.run("Staking.MinValidatorBond.getValue", () =>
        api.query.Staking.MinValidatorBond.getValue(),
      ),
      runner.run("Staking.OptimumSelfStake.getValue", () =>
        api.query.Staking.OptimumSelfStake.getValue(),
      ),
      runner.run("Staking.HardCapSelfStake.getValue", () =>
        api.query.Staking.HardCapSelfStake.getValue(),
      ),
      runner.run("Staking.SelfStakeSlopeFactor.getValue", () =>
        api.query.Staking.SelfStakeSlopeFactor.getValue(),
      ),
    ]);

  return {
    minimumSelfStake: minimumSelfStake ?? 0n,
    optimumSelfStake: optimumSelfStake ?? 0n,
    hardCapSelfStake: hardCapSelfStake ?? 0n,
    slopeFactor: BigInt(slopeFactor ?? 0),
  };
}

function calculateScenarioParts(
  computation: EraRewardComputation,
  targetSelfStake: bigint,
  parameters: RewardCurveParameters,
): RewardParts {
  const { eraPayout, overview, totalPoints, validatorPoints } = computation;
  if (
    eraPayout === undefined ||
    overview === undefined ||
    totalPoints <= 0n ||
    validatorPoints <= 0n
  ) {
    return { commission: 0n, ownStake: 0n, incentive: 0n };
  }

  const validatorTotalPayout = ratioMulFloor(
    eraPayout,
    validatorPoints,
    totalPoints,
  );
  const nominatorStake = overview.total > overview.own
    ? overview.total - overview.own
    : 0n;
  const stakerParts = calculateStakerRewardParts(
    validatorTotalPayout,
    computation.commission,
    targetSelfStake,
    nominatorStake + targetSelfStake,
  );

  if (computation.incentiveWeight > computation.totalIncentiveWeight) {
    throw new Error(
      `Validator incentive weight exceeds the total in era ${computation.output.era}`,
    );
  }
  const targetWeight = calculateValidatorIncentiveWeight(
    targetSelfStake,
    parameters,
  );
  const adjustedTotalWeight =
    computation.totalIncentiveWeight -
    computation.incentiveWeight +
    targetWeight;

  return {
    ...stakerParts,
    incentive: calculateIncentivePayout(
      computation.incentiveBudget,
      targetWeight,
      adjustedTotalWeight,
    ),
  };
}

function averageReward(total: bigint, eraCount: number): number {
  return eraCount > 0 ? planckToDot(total / BigInt(eraCount)) : 0;
}

function calculateIncreasePercentage(
  currentReward: bigint,
  estimatedReward: bigint,
): number | null {
  if (currentReward <= 0n) return null;
  const percentage =
    ((Number(estimatedReward) / Number(currentReward)) - 1) * 100;
  return Math.round(percentage * 10_000) / 10_000;
}

export function buildRewardProjection(
  history: ValidatorRewardHistory,
  currentSelfStake: bigint,
  parameters: RewardCurveParameters,
): RewardProjection {
  const basisComputations = history.computations.filter(
    (computation) =>
      computation.output.active &&
      computation.incentiveBudget > 0n &&
      computation.totalIncentiveWeight > 0n,
  );
  const actualTotalReward = basisComputations.reduce(
    (total, computation) => total + computation.rewardPlanck,
    0n,
  );
  const targets = buildSelfStakeScenarioTargets(currentSelfStake, parameters);

  const scenarios = targets.map((targetSelfStake) => {
    let commission = 0n;
    let ownStake = 0n;
    let incentive = 0n;
    for (const computation of basisComputations) {
      const parts = calculateScenarioParts(
        computation,
        targetSelfStake,
        parameters,
      );
      commission += parts.commission;
      ownStake += parts.ownStake;
      incentive += parts.incentive;
    }
    const estimatedTotalReward = commission + ownStake + incentive;

    return {
      self_stake: planckToDot(targetSelfStake),
      additional_self_stake: planckToDot(targetSelfStake - currentSelfStake),
      estimated_total_reward: planckToDot(estimatedTotalReward),
      estimated_average_reward_per_era: averageReward(
        estimatedTotalReward,
        basisComputations.length,
      ),
      estimated_total_commission_reward: planckToDot(commission),
      estimated_total_own_stake_reward: planckToDot(ownStake),
      estimated_total_validator_incentive_reward: planckToDot(incentive),
      increase_pct: calculateIncreasePercentage(
        actualTotalReward,
        estimatedTotalReward,
      ),
    };
  });

  return {
    basis: {
      eras_requested: history.rewards.eras_requested,
      eras_used: basisComputations.length,
      current_self_stake: planckToDot(currentSelfStake),
      actual_total_reward: planckToDot(actualTotalReward),
      actual_average_reward_per_era: averageReward(
        actualTotalReward,
        basisComputations.length,
      ),
    },
    curve: {
      minimum_self_stake: planckToDot(parameters.minimumSelfStake),
      optimum_self_stake: planckToDot(parameters.optimumSelfStake),
      hard_cap_self_stake: planckToDot(parameters.hardCapSelfStake),
      slope_factor: Number(parameters.slopeFactor) / Number(PERBILL),
    },
    scenarios,
  };
}

export function buildValidatorRewardReport(
  history: ValidatorRewardHistory,
  currentSelfStake: bigint,
  parameters: RewardCurveParameters,
): ValidatorRewardReport {
  return {
    rewards: history.rewards,
    reward_projection: buildRewardProjection(
      history,
      currentSelfStake,
      parameters,
    ),
  };
}
