/**
 * Client-side mirror of backend/projection_engine.py's
 * ss_benefit_for_claim_age() — CALCULATION_CONTRACT.md sections 44 and
 * 49 (ninth follow-up review, finding 4). Used ONLY to show a live
 * "benefit at this age" preview next to the claim-age slider in
 * Settings, so a household sees their real dollar figure change as
 * they drag — the actual saved projection always uses the backend's
 * own copy of this formula. Keep these two in sync by hand; there's no
 * shared-source-of-truth mechanism between Python and JS here.
 */

// Worker-benefit reduction: 5/9% per month for the first 36 months
// before FRA, 5/12%/month beyond (30% max reduction at 62).
const REDUCTION_WORKER = { 62: 0.30, 63: 0.25, 64: 0.20, 65: 2 / 15, 66: 1 / 15 }
// Spousal-benefit reduction: 25/36% per month for the first 36 months
// before FRA, 5/12%/month beyond (35% max reduction at 62) — a
// different SSA schedule than the worker's own record.
const REDUCTION_SPOUSAL = { 62: 0.35, 63: 0.30, 64: 0.25, 65: 1 / 6, 66: 1 / 12 }
// Delayed credit (FRA to 70): 2/3% per month = 8%/year, same for both
// benefit types — it only matters when a household provides a real
// benefit_70 anchor different from the FRA figure.
const CREDIT = { 68: 0.08, 69: 0.16, 70: 0.24 }

export const SS_FRA_AGE = 67
export const SS_CLAIM_AGE_MIN = 62
export const SS_CLAIM_AGE_MAX = 70

export function clampClaimAge(age) {
  if (age == null) return age
  return Math.max(SS_CLAIM_AGE_MIN, Math.min(SS_CLAIM_AGE_MAX, age))
}

// benefitType: "worker" (Jason's own record) or "spousal" (Justin's field).
export function ssBenefitForClaimAge(benefit62, benefit67, benefit70, claimAge, benefitType = 'worker') {
  const age = clampClaimAge(claimAge)
  const reductionTable = benefitType === 'spousal' ? REDUCTION_SPOUSAL : REDUCTION_WORKER
  const reductionAt62 = reductionTable[62]
  if (age <= 62) return benefit62
  if (age >= 70) return benefit70
  if (age === SS_FRA_AGE) return benefit67
  if (age < SS_FRA_AGE) {
    const progress = (reductionAt62 - reductionTable[age]) / reductionAt62
    return benefit62 + progress * (benefit67 - benefit62)
  }
  const progress = CREDIT[age] / 0.24
  return benefit67 + progress * (benefit70 - benefit67)
}
