import { calcPercentiles } from "./stats.js";

export interface NominatorEntry {
  readonly keyArgs: readonly [string, ...unknown[]];
  readonly value: {
    readonly targets: readonly string[];
    readonly submitted_in: number;
    readonly suppressed: boolean;
  };
}

export interface NominationTarget {
  readonly address: string;
  readonly commission: number;
}

export interface NominationReport {
  readonly nominator: string;
  readonly stake: number;
  readonly targets: readonly NominationTarget[];
  readonly submitted_in: number;
  readonly suppressed: boolean;
  readonly [key: string]: unknown;
}

export interface ValidatorNominationsReport {
  readonly total_nominators: number;
  readonly unique_targets: number;
  readonly nominations: readonly NominationReport[];
  readonly [key: string]: unknown;
}

export type NominationsOutput = Record<string, ValidatorNominationsReport>;

export interface BuildNominationsOptions {
  readonly validators: readonly string[];
  readonly commissions: ReadonlyMap<string, number>;
  readonly nominatorEntries: readonly NominatorEntry[];
  readonly stakes: ReadonlyMap<string, number>;
  readonly minStakeDot: number;
  readonly percentiles: readonly number[];
}

interface NominationsState {
  readonly nominations: NominationReport[];
  readonly uniqueTargets: Map<string, number>;
}

function roundPercentage(numerator: number, denominator: number): number {
  return denominator > 0
    ? Math.round((numerator / denominator) * 10_000) / 100
    : 0;
}

export function buildNominationsReport({
  validators,
  commissions,
  nominatorEntries,
  stakes,
  minStakeDot,
  percentiles,
}: BuildNominationsOptions): NominationsOutput {
  const validatorSet = new Set(validators);
  const reports = new Map<string, NominationsState>(
    validators.map((validator) => [
      validator,
      { nominations: [], uniqueTargets: new Map<string, number>() },
    ]),
  );

  for (const entry of nominatorEntries) {
    const matchedValidators = entry.value.targets.filter((target) =>
      validatorSet.has(target),
    );
    if (matchedValidators.length === 0) continue;

    const nominator = entry.keyArgs[0];
    const stake = stakes.get(nominator) ?? 0;
    if (minStakeDot > 0 && stake < minStakeDot) continue;

    const targets: NominationTarget[] = [];
    const targetCommissions: number[] = [];
    for (const address of entry.value.targets) {
      const commission = commissions.get(address);
      if (commission === undefined) continue;
      targets.push({ address, commission });
      targetCommissions.push(commission);
    }

    const nomination: NominationReport = {
      nominator,
      stake,
      targets,
      ...calcPercentiles(targetCommissions, {
        descending: true,
        prefix: "commission_",
        percentiles,
      }),
      submitted_in: entry.value.submitted_in,
      suppressed: entry.value.suppressed,
    };

    for (const validator of matchedValidators) {
      const report = reports.get(validator);
      if (report === undefined) {
        throw new Error(`Missing report state for validator ${validator}`);
      }
      report.nominations.push(nomination);
      for (const target of targets) {
        report.uniqueTargets.set(target.address, target.commission);
      }
    }
  }

  return Object.fromEntries(
    validators.map((validator) => {
      const report = reports.get(validator);
      if (report === undefined) {
        throw new Error(`Missing report state for validator ${validator}`);
      }
      const { nominations, uniqueTargets } = report;
      return [
        validator,
        {
          total_nominators: nominations.length,
          unique_targets: uniqueTargets.size,
          ...calcPercentiles([...uniqueTargets.values()], {
            descending: true,
            prefix: "commission_",
            percentiles,
          }),
          ...calcPercentiles(
            nominations.map((nomination) => nomination.stake),
            { prefix: "stake_", percentiles },
          ),
          nominations,
        },
      ];
    }),
  );
}

export interface ValidatorThresholdStats {
  readonly total: number;
  readonly meeting_minimum: number;
  readonly meeting_minimum_pct: number;
}

export interface RewardEffectiveSelfStake {
  readonly minimum_dot: number;
  readonly cap_dot: number;
  readonly validator_count: number;
  readonly capped_validator_count: number;
  readonly [key: string]: number;
}

export interface SelfStakeStats {
  readonly minimum_self_stake_dot: number;
  readonly active_validators: ValidatorThresholdStats;
  readonly all_validators: ValidatorThresholdStats;
  readonly reward_effective_self_stake: RewardEffectiveSelfStake;
}

export interface BuildSelfStakeOptions {
  readonly validatorAddresses: Iterable<string>;
  readonly activeValidators: Iterable<string>;
  readonly stakes: ReadonlyMap<string, number>;
  readonly percentiles: readonly number[];
  readonly minimumDot?: number;
  readonly rewardCapDot?: number;
}

export function buildSelfStakeStats({
  validatorAddresses,
  activeValidators,
  stakes,
  percentiles,
  minimumDot = 10_000,
  rewardCapDot = 30_000,
}: BuildSelfStakeOptions): SelfStakeStats {
  const activeSet = new Set(activeValidators);
  const allSet = new Set(validatorAddresses);
  const activeStakes: number[] = [];
  const allStakes: number[] = [];

  for (const address of allSet) {
    const selfStake = stakes.get(address) ?? 0;
    allStakes.push(selfStake);
    if (activeSet.has(address)) activeStakes.push(selfStake);
  }

  const activeMeetingMinimum = activeStakes.filter(
    (stake) => stake >= minimumDot,
  ).length;
  const allMeetingMinimum = allStakes.filter(
    (stake) => stake >= minimumDot,
  ).length;
  const eligibleStakes = allStakes.filter((stake) => stake >= minimumDot);
  const effectiveStakes = eligibleStakes.map((stake) =>
    Math.min(stake, rewardCapDot),
  );
  const cappedValidatorCount = eligibleStakes.filter(
    (stake) => stake > rewardCapDot,
  ).length;

  return {
    minimum_self_stake_dot: minimumDot,
    active_validators: {
      total: activeSet.size,
      meeting_minimum: activeMeetingMinimum,
      meeting_minimum_pct: roundPercentage(
        activeMeetingMinimum,
        activeSet.size,
      ),
    },
    all_validators: {
      total: allSet.size,
      meeting_minimum: allMeetingMinimum,
      meeting_minimum_pct: roundPercentage(allMeetingMinimum, allSet.size),
    },
    reward_effective_self_stake: {
      minimum_dot: minimumDot,
      cap_dot: rewardCapDot,
      validator_count: effectiveStakes.length,
      capped_validator_count: cappedValidatorCount,
      ...calcPercentiles(effectiveStakes, {
        prefix: "self_stake_",
        percentiles,
      }),
    },
  };
}

export interface ValidatorInfoDetails<TRewards, TProjection> {
  readonly rewards: TRewards;
  readonly reward_projection: TProjection;
}

export interface ValidatorInfo<TRewards, TProjection> {
  readonly self_stake: number;
  readonly rewards: TRewards;
  readonly reward_projection: TProjection;
}

export type ValidatorInfoOutput<TRewards, TProjection> = Record<
  string,
  ValidatorInfo<TRewards, TProjection>
>;

export function buildValidatorInfo<TRewards, TProjection>(
  validators: readonly string[],
  stakes: ReadonlyMap<string, number>,
  detailsByValidator: ReadonlyMap<
    string,
    ValidatorInfoDetails<TRewards, TProjection>
  >,
): ValidatorInfoOutput<TRewards, TProjection> {
  return Object.fromEntries(
    validators.map((validator) => {
      const details = detailsByValidator.get(validator);
      if (details === undefined) {
        throw new Error(`Missing validator information for ${validator}`);
      }
      return [
        validator,
        { self_stake: stakes.get(validator) ?? 0, ...details },
      ];
    }),
  );
}
