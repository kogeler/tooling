import { validator, rewardEras, DOT_PLANCK } from "./config.js";

// Commission is stored on-chain as a Perbill (parts per billion).
const PERBILL = 1_000_000_000n;

function toDot(planck) {
  // Per-era, single-validator rewards stay far below 2^53 planck, so the
  // Number conversion is lossless here. Round to 4 decimals like the rest.
  return Math.round((Number(planck) / Number(DOT_PLANCK)) * 1e4) / 1e4;
}

// Compute the reward that is paid to the validator's own stash for one era.
//
// This mirrors `pallet_staking::do_payout_stakers_by_page`: the validator
// personally receives its commission cut plus the staking reward on its own
// bonded stake. Nominator payouts are intentionally excluded.
//
//   validator_total_payout = era_payout * validator_points / total_points
//   commission_payout      = commission * validator_total_payout
//   leftover               = validator_total_payout - commission_payout
//   own_stake_reward       = leftover * own / total
//   personal_reward        = commission_payout + own_stake_reward
async function computeEraReward(api, era) {
  const [overview, eraPayout, points, prefs, claimedPages] = await Promise.all([
    api.query.Staking.ErasStakersOverview.getValue(era, validator),
    api.query.Staking.ErasValidatorReward.getValue(era),
    api.query.Staking.ErasRewardPoints.getValue(era),
    api.query.Staking.ErasValidatorPrefs.getValue(era, validator),
    api.query.Staking.ClaimedRewards.getValue(era, validator),
  ]);

  // No era payout at all => the era has no reward data on this chain. This is
  // the case for eras that predate the Asset Hub staking migration as well as
  // for the not-yet-finalized current era.
  const hasData = eraPayout != null;

  // No exposure overview => the validator was not in the active set this era
  // (was not elected), so there is nothing to be paid out to it.
  if (!hasData || !overview) {
    return {
      era,
      active: false,
      has_data: hasData,
      reward: 0,
      commission_reward: 0,
      own_stake_reward: 0,
      reward_planck: "0",
      commission_pct: null,
      own_stake: 0,
      total_stake: 0,
      claimed: false,
      claimed_pages: 0,
      total_pages: overview ? overview.page_count : 0,
    };
  }

  const totalPages = overview.page_count;
  const claimed = Array.isArray(claimedPages) ? claimedPages : [];
  // The validator's own-stake reward is only paid out with page 0, while its
  // commission is spread across every page, so it is fully paid only once all
  // pages have been claimed.
  const fullyClaimed = totalPages > 0 && claimed.length >= totalPages;

  const totalPoints = BigInt(points?.total ?? 0);
  const vp = (points?.individual ?? []).find(([addr]) => addr === validator);
  const validatorPoints = vp ? BigInt(vp[1]) : 0n;

  const commission = BigInt(prefs?.commission ?? 0); // Perbill (0..1e9)

  let commissionPayout = 0n;
  let ownReward = 0n;
  if (totalPoints > 0n && validatorPoints > 0n) {
    const validatorTotalPayout = (eraPayout * validatorPoints) / totalPoints;
    commissionPayout = (validatorTotalPayout * commission) / PERBILL;
    const leftover = validatorTotalPayout - commissionPayout;
    ownReward =
      overview.total > 0n ? (leftover * overview.own) / overview.total : 0n;
  }
  const personalPlanck = commissionPayout + ownReward;

  return {
    era,
    active: true,
    has_data: true,
    reward: toDot(personalPlanck),
    // Split so it is clear whether the reward comes from commission (which the
    // 0% referendum removes) or from the validator's own stake.
    commission_reward: toDot(commissionPayout),
    own_stake_reward: toDot(ownReward),
    reward_planck: personalPlanck.toString(),
    commission_pct: Number(commission) / 1e7, // Perbill -> percent
    own_stake: toDot(overview.own),
    total_stake: toDot(overview.total),
    claimed: fullyClaimed,
    claimed_pages: claimed.length,
    total_pages: totalPages,
  };
}

// Report the validator's personal reward for the last `rewardEras` completed
// eras (the active era is still running and has no payout yet).
export async function fetchValidatorRewards(api) {
  const [activeEra, curPrefs] = await Promise.all([
    api.query.Staking.ActiveEra.getValue(),
    // Current on-chain commission. Per-era prefs are snapshotted at era start,
    // so a recent commission change (e.g. the 0% referendum) is only visible
    // here until the next era snapshots it.
    api.query.Staking.Validators.getValue(validator),
  ]);
  if (!activeEra) {
    return { eras_requested: rewardEras, eras: [] };
  }

  const eraIndices = [];
  for (
    let e = activeEra.index - 1;
    e >= 0 && eraIndices.length < rewardEras;
    e--
  ) {
    eraIndices.push(e);
  }

  const eras = await Promise.all(
    eraIndices.map((era) => computeEraReward(api, era)),
  );

  let totalReward = 0;
  let unclaimedReward = 0;
  for (const e of eras) {
    totalReward += e.reward;
    if (e.active && !e.claimed) unclaimedReward += e.reward;
  }

  // The latest completed era still reflects the pre-change commission snapshot.
  const latest = eras.find((e) => e.active);
  const currentCommissionPct = curPrefs
    ? Number(curPrefs.commission) / 1e7
    : null;
  // Rewards will collapse once the new (lower) commission is snapshotted, if the
  // validator has no own-stake cushion to earn from.
  const rewardCollapsePending =
    latest != null &&
    currentCommissionPct != null &&
    latest.reward > 0 &&
    latest.commission_pct > currentCommissionPct &&
    latest.own_stake_reward === 0;

  return {
    eras_requested: rewardEras,
    active_era: activeEra.index,
    is_validator: curPrefs != null,
    current_commission_pct: currentCommissionPct,
    latest_era_commission_pct: latest ? latest.commission_pct : null,
    reward_collapse_pending: rewardCollapsePending,
    total_reward: Math.round(totalReward * 1e4) / 1e4,
    unclaimed_reward: Math.round(unclaimedReward * 1e4) / 1e4,
    eras,
  };
}
