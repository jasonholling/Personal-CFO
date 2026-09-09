"""
Projection engine — retirement with proper bucket tracking and RMDs.

Buckets:
  pretax   — Traditional 401k + Traditional IRA — RMDs start at 73 or 75,
             see rmd_start_age() below (SECURE Act 2.0's birth-year rule)
  roth     — Roth 401k + Roth IRA — no RMDs
  taxable  — Brokerage — drawn first, most flexible
  hsa      — HSA — no RMDs during lifetime

RMD factors from IRS Uniform Lifetime Table (SECURE Act 2.0, age 73+).
"""

from typing import List, Dict
import datetime
import math

from annual_engine import AccountState, DEFAULT_ORDER, marginal_bracket_tax_model, simulate_withdrawal_year
from annual_inputs import build_annual_income_inputs
from timeline_engine import (CURRENT_YEAR, Timeline, build_cumulative_inflation, build_timeline, healthcare_for_age,
                              build_two_person_timeline)

COLLEGE_COST_INFLATION = 0.04
COLLEGE_YEARS          = 4
UNL_CURRENT_ANNUAL     = 0  # fallback only — read from inputs at runtime

# 529 contributions are assumed to stop once you hit this age — no more
# earned income funding them after retirement. Matches the "Retire 60"
# scenario used as the default assumption elsewhere in the app.
PARENT_RETIREMENT_AGE_ASSUMPTION = 60

# Real EE/I savings bonds stop earning interest at final maturity — 30
# years from issue. We only track a bond's current balance, not its issue
# date, so this caps projected growth at a reasonable stand-in instead of
# compounding indefinitely (which would otherwise run 40+ years straight
# through to age 60 for a young kid).
BOND_MAX_GROWTH_YEARS = 30

# Pension / SS defaults — these are fallbacks only; real values always come
# from planning_inputs (set per-user in Settings), never hardcoded here.
PENSION_100J_S_DEFAULT   = {55: 0, 60: 0, 65: 0}
JASON_SS_EARLY_DEFAULT   = 0
JASON_SS_DELAYED_DEFAULT = 0
JASON_SS_DELAYED_RATIO   = 1.0  # fallback ratio only — no assumption without real inputs

# Justin spousal benefit = 50% of Jason FRA benefit, claimed at his FRA (67).
# Reduced if claimed before 67 — we model claiming at 67 for full amount.
JUSTIN_SPOUSAL_ANNUAL = 0
JUSTIN_SPOUSAL_AGE    = 67          # full benefit at his FRA

# Social Security claiming-age formula (CALCULATION_CONTRACT.md section
# 44, design approved by Jason 2026-09-08) -- the real SSA early-
# reduction rate (5/9%/month for the first 36 months before FRA, 5/12%/
# month beyond that) and delayed-credit rate (2/3%/month, i.e. 8%/year,
# from FRA to 70). FRA is assumed 67 everywhere in this app (matching
# JUSTIN_SPOUSAL_AGE's own existing convention) -- birth-year-dependent
# FRA has never been modeled here and stays out of scope.
SS_FRA_AGE = 67
SS_CLAIM_AGE_MIN = 62
SS_CLAIM_AGE_MAX = 70
# Worker-benefit reduction/credit rates (Jason's own record).
_SS_REDUCTION_FRACTION_AT_AGE = {62: 0.30, 63: 0.25, 64: 0.20, 65: 2/15, 66: 1/15}
_SS_CREDIT_FRACTION_AT_AGE    = {68: 0.08, 69: 0.16, 70: 0.24}
# Spousal-benefit reduction rates (independent review, 2026-09-08, ninth
# follow-up, finding 4): the SSA's early-reduction formula for a SPOUSAL
# benefit uses different monthly rates than a worker's own record --
# 25/36 of 1% per month for the first 36 months before FRA (worker: 5/9%),
# then 5/12 of 1% per month beyond 36 months (same rate as worker) -- a
# spousal benefit is reduced 35% at 62 (worker: 30%), not the same shape.
# Justin's own SS fields (justin_social_security/justin_ss_early/
# justin_ss_70) have always modeled a spousal benefit (see
# JUSTIN_SPOUSAL_ANNUAL/JUSTIN_SPOUSAL_AGE's own naming/docstring above),
# so his own interpolation uses this table, not the worker one. Spousal
# benefits earn no delayed credit past FRA under real SSA rules, but
# since this app anchors to the household's own REAL dollar inputs at
# 67/70 rather than re-deriving the rule, the worker credit table's own
# SHAPE is still used to interpolate between two real anchors when a
# household happens to provide a nonzero justin_ss_70 different from
# justin_social_security -- if they leave it at the default (equal to
# the FRA figure), the interpolation is correctly flat regardless.
_SS_SPOUSAL_REDUCTION_FRACTION_AT_AGE = {62: 0.35, 63: 0.30, 64: 0.25, 65: 1/6, 66: 1/12}


def _clamp_claim_age(claim_age):
    """Independent review, 2026-09-08, ninth follow-up, guard note: an
    out-of-range claim age must be REJECTED at the age itself, not just
    have its resulting dollar amount silently clamped while the START
    DATE stays whatever was given -- an input of 60 must not be able to
    receive the age-62 amount starting at age 60 (2 years of benefit
    that was never modeled). Clamps to [62, 70] before any lookup."""
    if claim_age is None:
        return None
    return max(SS_CLAIM_AGE_MIN, min(SS_CLAIM_AGE_MAX, claim_age))


def ss_benefit_for_claim_age(benefit_62: float, benefit_67: float, benefit_70: float, claim_age: int,
                              benefit_type: str = "worker") -> float:
    """The household's own annual SS benefit for claiming at `claim_age`
    (62-70), anchored EXACTLY to its three real dollar inputs (from a
    real SSA.gov statement) at 62/FRA/70 -- ages in between are
    interpolated using the real SSA formula's own shape (the reduction
    rate genuinely changes at the 3-year-early mark, so simple linear
    interpolation between 62 and 67 would be measurably wrong), scaled
    so the endpoints match the household's own real numbers exactly
    rather than a single derived PIA (which could disagree with the
    real anchors due to that household's own COLA/rounding history).

    benefit_type="worker" (default, Jason's own record) or "spousal"
    (Justin's own field -- see the module-level comment on
    _SS_SPOUSAL_REDUCTION_FRACTION_AT_AGE for why these need different
    reduction rates before FRA; the credit rates from FRA to 70 are
    shared, since they only matter when a household provides a real
    justin_ss_70 anchor different from the FRA figure).

    claim_age is clamped to [62, 70] before any lookup (see
    _clamp_claim_age) -- an out-of-range age can never receive an
    amount without also being treated as claimed at the clamped age."""
    claim_age = _clamp_claim_age(claim_age)
    reduction_table = _SS_SPOUSAL_REDUCTION_FRACTION_AT_AGE if benefit_type == "spousal" else _SS_REDUCTION_FRACTION_AT_AGE
    reduction_at_62 = reduction_table[62]
    if claim_age <= 62:
        return benefit_62
    if claim_age >= 70:
        return benefit_70
    if claim_age == SS_FRA_AGE:
        return benefit_67
    if claim_age < SS_FRA_AGE:
        progress = (reduction_at_62 - reduction_table[claim_age]) / reduction_at_62  # 0 at 62, 1 at 67
        return benefit_62 + progress * (benefit_67 - benefit_62)
    progress = _SS_CREDIT_FRACTION_AT_AGE[claim_age] / 0.24  # 0 at 67, 1 at 70
    return benefit_67 + progress * (benefit_70 - benefit_67)


def resolve_ss_claim_ages(inputs: Dict, jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    """Independent review, 2026-09-08, ninth follow-up, finding 1: the
    single, correct 3-tier resolution for each spouse's OWN claim age,
    used EVERYWHERE a claim age is determined (previously each of the
    six two-age dispatch blocks in simulation_engine.py did its own
    ad-hoc `inputs = {**inputs, "jason_ss_claim_age": jason_ss_claim_age,
    ...}` merge, which overwrote a spouse's own SAVED value with None
    whenever the OTHER spouse's claim age was the only one explicitly
    given -- reproduced: inputs already carries a saved
    justin_ss_claim_age=68; overriding only jason_ss_claim_age silently
    reset Justin back to his flat, unadjusted FRA figure).

    Per spouse, independently: explicit override (this call's own
    jason_ss_claim_age/justin_ss_claim_age argument) > saved Settings
    value (inputs["jason_ss_claim_age"]/["justin_ss_claim_age"]) > None
    (every consumer's own resolve_ss_benefits then falls back to its
    ss_timing early/delayed toggle, unchanged). Also applies the
    [62, 70] clamp guard to whichever value wins."""
    jason = jason_ss_claim_age if jason_ss_claim_age is not None else inputs.get("jason_ss_claim_age")
    justin = justin_ss_claim_age if justin_ss_claim_age is not None else inputs.get("justin_ss_claim_age")
    return _clamp_claim_age(jason), _clamp_claim_age(justin)


def resolve_ss_benefits(inputs: Dict, ss_timing: str = "early",
                         jason_ss_claim_age: int = None, justin_ss_claim_age: int = None):
    """Single shared resolver for (jason_ss_annual, jason_ss_age,
    justin_ss_annual, justin_ss_age) -- CALCULATION_CONTRACT.md section
    44, milestone 2: replaces the ~20 independent
    `jason_ss_age = 62 if ss_timing == "early" else 67` call sites,
    migrated one consumer at a time to call this instead of hand-
    rolling the ternary. `jason_ss_claim_age`/`justin_ss_claim_age` here
    are the ALREADY-RESOLVED final claim ages for this call (see
    resolve_ss_claim_ages, which callers should use first to correctly
    layer explicit-override/saved-Settings/legacy-default) -- a spouse
    left at None keeps the exact existing `ss_timing`-derived early(62)/
    delayed(67) behavior; a spouse given a claim age gets their real
    benefit at that exact age via `ss_benefit_for_claim_age` (Jason as
    a worker benefit, Justin as a spousal benefit -- finding 4), reading
    `jason_ss_70`/`justin_ss_early`/`justin_ss_70` the same way every
    other opt-in call site does (falling back to the existing FRA
    figure when not yet supplied, never crashing on a household that
    hasn't filled in the new fields)."""
    jason_ss_early   = inputs.get("jason_social_security", JASON_SS_EARLY_DEFAULT)
    jason_ss_delayed = inputs.get("jason_ss_delayed", jason_ss_early * JASON_SS_DELAYED_RATIO)
    if jason_ss_claim_age is not None:
        # External audit review of commit 0c1a569, finding 2 (P1): the
        # planning_inputs.jason_ss_70/justin_ss_early/justin_ss_70
        # columns default to REAL DEFAULT 0 (db.py), so the column is
        # ALWAYS present in every household's row -- `inputs.get(key,
        # fallback)` never falls back to the estimate, because the dict
        # key is never actually MISSING, only zero-valued. Enabling
        # Jason's claim-age slider with a real $30,000 FRA benefit but
        # an untouched (0) age-70 field made ss_benefit_for_claim_age
        # interpolate straight down toward that $0 "anchor": $30,000 at
        # 67, $20,000 at 68, $10,000 at 69, $0 at 70 -- a household's
        # real benefit silently erased rather than estimated. `or`
        # (not `.get`'s default) treats a falsy stored 0 the same as a
        # genuinely missing key, falling back to the same delayed-credit
        # estimate `jason_ss_delayed` itself already uses when unset. A
        # real SS benefit is never actually $0, so this never discards
        # genuine household data.
        jason_ss_70 = inputs.get("jason_ss_70") or jason_ss_delayed
        jason_ss_annual = ss_benefit_for_claim_age(jason_ss_early, jason_ss_delayed, jason_ss_70, jason_ss_claim_age,
                                                     benefit_type="worker")
        jason_ss_age = _clamp_claim_age(jason_ss_claim_age)
    else:
        jason_ss_annual = jason_ss_early if ss_timing != "delayed" else jason_ss_delayed
        jason_ss_age    = 62 if ss_timing != "delayed" else 67

    justin_ss_annual = inputs.get("justin_social_security", JUSTIN_SPOUSAL_ANNUAL)
    justin_ss_age    = inputs.get("justin_ss_age", JUSTIN_SPOUSAL_AGE)
    if justin_ss_claim_age is not None:
        # Same finding-2 fix as jason_ss_70 above, for Justin's two new
        # anchor fields.
        justin_ss_62 = inputs.get("justin_ss_early") or justin_ss_annual
        justin_ss_70 = inputs.get("justin_ss_70") or justin_ss_annual
        justin_ss_annual = ss_benefit_for_claim_age(justin_ss_62, justin_ss_annual, justin_ss_70, justin_ss_claim_age,
                                                      benefit_type="spousal")
        justin_ss_age = _clamp_claim_age(justin_ss_claim_age)

    return jason_ss_annual, jason_ss_age, justin_ss_annual, justin_ss_age


# Flat net-of-tax approximation applied to income streams that land
# directly in a taxable-equivalent bucket without going through this
# engine's own marginal-bracket withdrawal-tax model: RSU/bonus proceeds,
# and (2026-09-08) second-earner gap income. NOT a real payroll/capital-
# gains tax calculation — no brackets, no FICA, no filing status, no
# state tax. Named and centralized here per CALCULATION_CONTRACT.md
# section 13, backlog item 3: previously three separate bare `0.65`
# literals with the same meaning, now one documented, findable policy.
SECOND_EARNER_NET_OF_TAX_FACTOR = 0.65

# Kids contribution defaults — overridden by planning_inputs at runtime
ABBY_MONTHLY_529_DEFAULT   = 0
COOPER_MONTHLY_529_DEFAULT = 0
KIDS_ROTH_MONTHLY_DEFAULT  = 0
KIDS_CUST_MONTHLY_DEFAULT  = 0

# IRS Uniform Lifetime Table — age: distribution period
RMD_TABLE = {
    73:26.5, 74:25.5, 75:24.6, 76:23.7, 77:22.9, 78:22.0, 79:21.1,
    80:20.2, 81:19.4, 82:18.5, 83:17.7, 84:16.8, 85:16.0, 86:15.2,
    87:14.4, 88:13.7, 89:12.9, 90:12.2, 91:11.5, 92:10.8, 93:10.1,
    94:9.5,  95:8.9,  96:8.4,  97:7.8,  98:7.3,  99:6.8,
}

# 401k constants — now read from accounts + planning_inputs at runtime
# These are kept as fallbacks only if planning_inputs not available
PRETAX_401K_BALANCE_DEFAULT  = 0
ROTH_401K_BALANCE_DEFAULT    = 0
ANNUAL_401K_PRETAX_DEFAULT   = 0
ANNUAL_401K_ROTH_DEFAULT     = 0

# The "today" year assumed everywhere life events / the yearly withdrawal
# loop below need a calendar year — must stay in sync with the yearly
# loop's own "year" field formula (CURRENT_YEAR + yr + years_to_retire),
# which was already hardcoded to 2026 before life events existed. Kept as
# one named constant instead of two literals so the two can't drift apart.
# Canonical definition now lives in timeline_engine.py (consolidation
# follow-up, 2026-09-07), imported at the top of this file — so that
# module has no dependency on this one, and every existing
# `from projection_engine import CURRENT_YEAR` call site elsewhere is
# unaffected (Python re-exports it here unchanged).


def _fv(pv, r, n):
    if n <= 0: return pv
    return pv * ((1 + r) ** n)

def _fv_annuity(pmt, r, n):
    if n <= 0: return 0.0
    if r == 0: return pmt * n
    return pmt * (((1 + r) ** n - 1) / r)

def _fv_growing_annuity(pmt, r, g, n):
    """Future value of a series of deposits that itself grows at rate g
    each year (e.g. a 401k contribution tied to a salary that gets annual
    raises), each deposit then compounding at r for the years remaining.
    `pmt` is the FIRST year's deposit, not scaled by g yet. Reduces to
    _fv_annuity when g == 0."""
    if n <= 0: return 0.0
    if r == g:
        # The (r - g) denominator below would divide by zero — every
        # deposit grows exactly as fast as the return compounds it, so
        # each of the n deposits is worth pmt * (1+r)^(n-1) at the end.
        return pmt * n * ((1 + r) ** (n - 1))
    return pmt * (((1 + r) ** n - (1 + g) ** n) / (r - g))

def _fv_annuity_monthly(pmt, r, n):
    if n <= 0: return 0.0
    mr = r / 12
    months = n * 12
    if mr == 0: return pmt * months
    return pmt * (((1 + mr) ** months - 1) / mr)

def _pv_annuity(pmt, r, n):
    if n <= 0: return 0.0
    if r == 0: return pmt * n
    return pmt * (1 - (1 + r) ** -n) / r

def rmd_start_age(current_age: int) -> int:
    """SECURE Act 2.0's RMD start age depends on birth year, not a single
    fixed age: 73 for anyone born 1951-1959 (the window in force through
    2032), stepping up to 75 for anyone born 1960 or later (reaching 74
    after 2032). Previously hardcoded to 73 everywhere — caught by
    external audit 2026-09-05. Approximates birth year from current age
    and today's date since the app only tracks whole-year ages, not exact
    birthdates; that's consistent with this engine's precision elsewhere."""
    birth_year = datetime.date.today().year - current_age
    return 75 if birth_year >= 1960 else 73


def _rmd(balance, age, start_age=73):
    """Calculate required minimum distribution for a given age. Pass the
    caller's own rmd_start_age(current_age) as start_age — the 73 default
    here only covers legacy callers that haven't been updated."""
    if age < start_age or balance <= 0:
        return 0.0
    factor = RMD_TABLE.get(age, RMD_TABLE[99])
    return balance / factor

def _split_life_events(life_events: List[Dict], retirement_year: int):
    """Classify each life event as pre- or post-retirement given a calendar
    retirement_year, normalizing each into a plain dict of
    event_year/one_time/monthly/duration_months. Shared by
    run_retirement_projection (pre-retirement events compound into
    taxable_at_ret) and simulation_engine.py's own year-by-year loops
    (Monte Carlo / stress tests), which need the post-retirement list
    applied directly during their withdrawal phase since they don't
    re-derive buckets from run_retirement_projection on every simulated
    year. Callers are expected to have already filtered out any event
    whose included_in_projection flag is off.

    Finite recurring events are split at retirement without duplicating
    their one-time cash amount. Zero duration retains its documented
    meaning: pre-retirement events stop at retirement; post events continue.

    Events with a truthy target_debt_account_id are skipped entirely here
    — those model a one-time lump payment toward a SPECIFIC debt account,
    not money that was ever going to be invested. This engine's buckets
    (pretax/roth/taxable/hsa) are asset-only and never track debt/mortgage
    balances as a liability, so a debt-targeted event correctly has ZERO
    effect on portfolio_at_retirement/projected_surplus — the cash was
    never destined for the taxable bucket to begin with. That money is
    instead modeled by debt_engine.py's project_debt_schedule (via
    main.py's /api/debts/payoff-plan and /recommendation routes), which
    actually reduces the target debt's balance and payoff schedule. Giving
    it special debt-aware treatment in THIS engine would double-count it
    (once here as investable cash, once there as debt payoff)."""
    pre, post = [], []
    for event in (life_events or []):
        if event.get("target_debt_account_id"):
            continue
        event_year = int(event["event_year"])
        # An event dated before CURRENT_YEAR already happened — its cash
        # impact is already baked into today's account balances (the
        # accounts table's `balance` fields), so replaying it into the
        # projection double-counts it (external audit 2026-09-06: $100,000
        # currently invested + a $100,000 life event dated last year
        # inflated portfolio_at_retirement by another ~$100,000+, as if the
        # windfall happened twice). An event dated in CURRENT_YEAR itself is
        # NOT skipped — this app only tracks whole years, not exact dates,
        # so "dated this year" can't be distinguished from "already
        # reflected in the current balance" the way a truly past year can;
        # we treat the current year as still in progress and keep modeling
        # it, consistent with CURRENT_YEAR being the first compounding year
        # everywhere else in this engine (e.g. _pre_retirement_taxable_add).
        # Skipping it here (rather than only in run_retirement_projection)
        # fixes every consumer of this shared classifier at once, including
        # simulation_engine.py's Monte Carlo/stress/SWR runs.
        if event_year < CURRENT_YEAR:
            continue
        one_time = float(event.get("one_time_cash_delta") or 0)
        monthly  = float(event.get("monthly_cash_flow_delta") or 0)
        duration_months = max(0, int(event.get("duration_months") or 0))
        rec = {"event_year": event_year, "one_time": one_time,
               "monthly": monthly, "duration_months": duration_months}
        if event_year < retirement_year:
            pre.append(rec)
            remaining_months = duration_months - (retirement_year - event_year) * 12
            if monthly and remaining_months > 0:
                post.append({**rec, "event_year": retirement_year,
                             "one_time": 0, "duration_months": remaining_months})
        else:
            post.append(rec)
    return pre, post


def _post_retirement_asset_sale_events(inputs: Dict, jason_age: int, effective_start_age: int) -> List[Dict]:
    """Asset 1/2 sales (Settings page) scheduled to happen AFTER the
    withdrawal phase's own effective start date. The accumulation-phase
    code in run_retirement_projection only ever handles a sale at or
    before that date (assetN_sale_age <= effective_start_age) — a sale
    scheduled for partway through retirement simply vanished from every
    per-year cash flow, and Monte Carlo/stress-test/SWR simulations
    (which only inherit run_retirement_projection's pre-retirement
    accumulation result and have no other way to see this Settings
    field) never modeled it at all (external audit 2026-09-07: "retire
    at 58, sell an asset for $500,000 at 60 -> annual results identical
    to no sale"). Returns life-event-shaped one-time-cash dicts — the
    same {"event_year","one_time","monthly","duration_months"} shape
    _split_life_events produces — meant to be appended directly to a
    post_life_events list. Sales at or before effective_start_age are
    intentionally excluded here (already counted via the accumulation-
    phase code); appending them here too would double-count.

    `effective_start_age` (not the raw, possibly-past ret_age) is what
    this boundary must be measured against — independent review,
    2026-09-07, third follow-up: comparing against raw ret_age let a
    sale dated between an already-past ret_age and the household's real
    current age fall through both this function's exclusion AND the
    accumulation phase's own inclusion (each checked against the wrong
    end of the gap), silently vanishing entirely rather than double-
    counting. Reference implementation corrected together with every
    consumer here, not left agreeing with a known bug."""
    events = []
    for label, appreciates in (("asset1", True), ("asset2", False)):
        sale_age = inputs.get(f"{label}_sale_age", 0)
        sale_net = inputs.get(f"{label}_sale_net", 0)
        if not sale_age or sale_age <= effective_start_age:
            continue
        yrs_from_now = max(0, sale_age - jason_age)
        # Matches the accumulation-phase code's own asset1-vs-asset2
        # treatment: asset1 appreciates from today to its sale date at
        # asset1_appreciation; asset2_sale_net is already a sale-date
        # figure with no pre-sale appreciation modeled.
        proceeds = sale_net * ((1 + inputs.get("asset1_appreciation", 0.03)) ** yrs_from_now) if appreciates else sale_net
        events.append({"event_year": CURRENT_YEAR + yrs_from_now, "one_time": proceeds,
                        "monthly": 0.0, "duration_months": 0})
    return events


def _pre_retirement_taxable_add(pre_events: List[Dict], pre_ret: float, retirement_year: int) -> float:
    """Future-value a list of pre-retirement life events into the taxable
    bucket at retirement — the one-time delta compounds from event_year to
    retirement_year (same treatment as the existing asset1/asset2-sale
    bridge logic), and a recurring monthly delta compounds as an annuity
    from event start through whichever comes first: retirement, or the end
    of its duration_months (0 meaning "runs through retirement")."""
    total = 0.0
    for ev in pre_events:
        yrs_to_grow = retirement_year - ev["event_year"]
        total += ev["one_time"] * ((1 + pre_ret) ** yrs_to_grow)
        if ev["monthly"]:
            annual_delta = ev["monthly"] * 12
            contrib_end_year = (retirement_year if ev["duration_months"] == 0
                                 else min(retirement_year, ev["event_year"] + ev["duration_months"] / 12))
            contrib_years = contrib_end_year - ev["event_year"]
            if contrib_years > 0:
                fv_at_stop = _fv_annuity(annual_delta, pre_ret, contrib_years)
                total += _fv(fv_at_stop, pre_ret, retirement_year - contrib_end_year)
    return total


# Surplus-allocation goals that actually represent money invested toward
# retirement, and therefore get compounded into the projection below. Of the
# seven fixed goals in the surplus_allocations table, only these two are
# "invest this monthly amount toward retirement" money:
#   - "Retirement contributions" splits across pretax/roth 401k using the
#     SAME pretax_401k_pct/roth_pct ratio already used elsewhere in
#     run_retirement_projection for the existing 401k contributions.
#   - "Taxable investing" lands in the taxable bucket.
# The other goals are deliberately excluded — they are cash reserves,
# debt paydown, per-kid 529 education goals (a separate engine), a tax
# set-aside, or an undefined catch-all, none of which are dollars invested
# toward retirement:
#   - "Emergency reserve"                 — cash reserve, not invested
#   - "High-interest debt payoff"         — pays down debt, doesn't grow assets
#   - "Education funding - Abby"/"Cooper" — modeled by run_education_projection/
#                                            run_kids_projection (as an extra
#                                            monthly 529 contribution for that
#                                            specific kid — see main.py's
#                                            _get_kids_surplus_529_monthly),
#                                            would double-count if also added
#                                            here
#   - "Tax reserve"                       — set aside to pay taxes, not invested
#   - "Other goal"                        — undefined catch-all, no assumption
# This is a judgment call, not an oversight — a future reader adding a new
# goal to the fixed list should decide explicitly whether it belongs here.
SURPLUS_GOAL_RETIREMENT_CONTRIB = "Retirement contributions"
SURPLUS_GOAL_TAXABLE_INVESTING  = "Taxable investing"

# Per-kid education-funding goals — replaced the single shared "Education
# funding" goal 2026-09-06 so a household can direct surplus specifically
# to one kid's 529 ("abby's will go to abby, cooper will go to cooper")
# rather than a pooled amount split by some formula. The stored `goal`
# string is this stable key, NOT the kid's configurable display name
# (kid1_name/kid2_name in planning_inputs) — a rename in Settings must
# never orphan an existing surplus_allocations row. See
# main.py._get_kids_surplus_529_monthly for how these feed
# run_education_projection/run_kids_projection's surplus_529_monthly param.
SURPLUS_GOAL_EDUCATION_ABBY   = "Education funding - Abby"
SURPLUS_GOAL_EDUCATION_COOPER = "Education funding - Cooper"


def _surplus_allocations_at_retirement(surplus_allocations: List[Dict], pre_ret: float,
                                        years_to_retire: int) -> Dict[str, float]:
    """Future-value the two retirement-relevant surplus allocation goals
    (see comment above) as monthly annuities compounding from *today*
    through retirement — surplus allocations have no start date of their
    own (unlike life_events' event_year), they're simply an ongoing amount
    assumed active as of right now. Uses the same monthly-annuity family
    as _fv_annuity_monthly (also used by run_education_projection/
    run_kids_projection) rather than the annual _fv_annuity, since these
    are monthly assignments in practice.

    Non-positive (zero or negative) monthly_amount rows are skipped —
    zero is a no-op and negative shouldn't occur (the PUT endpoint
    validates >= 0) but is guarded here defensively rather than assumed.

    Returns {"retirement_contrib": fv, "taxable_investing": fv}, both 0.0
    if no matching rows (including the None/empty default)."""
    retirement_contrib = 0.0
    taxable_investing  = 0.0
    for row in (surplus_allocations or []):
        monthly = float(row.get("monthly_amount") or 0)
        if monthly <= 0:
            continue
        goal = row.get("goal")
        if goal == SURPLUS_GOAL_RETIREMENT_CONTRIB:
            retirement_contrib += _fv_annuity_monthly(monthly, pre_ret, years_to_retire)
        elif goal == SURPLUS_GOAL_TAXABLE_INVESTING:
            taxable_investing += _fv_annuity_monthly(monthly, pre_ret, years_to_retire)
        # All other goals intentionally excluded — see module comment above.
    return {"retirement_contrib": retirement_contrib, "taxable_investing": taxable_investing}


def _post_retirement_year_effects(post_events: List[Dict], calendar_year: int):
    """For a single withdrawal-phase calendar year, returns
    (one_time_cash, monthly_adjustment_annual): the one-time_cash_delta of
    any event landing exactly in this year (added to the taxable bucket,
    same treatment as rmd_reinvested), and the annualized monthly delta of
    any event active during this year (positive reduces that year's need,
    negative increases it) — active meaning duration_months==0 (runs
    through the rest of retirement) or the year falls before the event's
    duration_months elapse."""
    one_time_cash = 0.0
    monthly_adj = 0.0
    for ev in post_events:
        if calendar_year < ev["event_year"]:
            continue
        if ev["event_year"] == calendar_year:
            one_time_cash += ev["one_time"]
        elapsed_months = (calendar_year - ev["event_year"]) * 12
        active_months = 12 if ev["duration_months"] == 0 else max(0, min(12, ev["duration_months"] - elapsed_months))
        monthly_adj += ev["monthly"] * active_months
    return one_time_cash, monthly_adj


def justin_years_to_retire_for(inputs: Dict, justin_age: int, years_to_retire: int) -> int:
    """Justin's own years-to-retire: if justin_ret_age is set, his OWN
    years-to-retire (independent of whichever Jason ret_age a scenario is
    sweeping); if unset (0, the default), falls back to years_to_retire —
    the pre-2026-09-08 age-gap-implied timing every consumer used before
    independent Justin income existed. Single source of truth for this
    value — used both for Justin's own contribution-accumulation window
    (run_retirement_projection) and for second-earner gap income
    (justin_gap_income_inputs below), which need to agree on it."""
    justin_ret_age = inputs.get("justin_ret_age", 0)
    return max(0, justin_ret_age - justin_age) if justin_ret_age else years_to_retire


def justin_gap_income_inputs(inputs: Dict, justin_years_to_retire: int, years_to_retire: int,
                              salary_growth_pct: float = 0.0):
    """Second-earner gap income (2026-09-08, CALCULATION_CONTRACT.md
    section 13, backlog items 1-3): if Justin's own years-to-retire is
    LATER than a consumer's own effective withdrawal start
    (years_to_retire), Justin is still earning during the first
    `justin_gap_years` of that withdrawal phase — that income should
    offset withdrawal need directly, the same mechanism bridge_income_55
    already used for a fixed-duration income offset, generalized to any
    ret_age and keyed to Justin's own real retirement date.

    Returns (justin_gap_years, justin_gap_income_at_start) — the latter a
    today's-dollar figure already grown to the loop's starting point
    (years_to_retire years from now), using salary_growth_pct (the same
    assumed-raise convention every other second-earner income stream —
    401k contributions, RSU, bonus — already grows with), NOT CPI
    inflation, which would conflate wage growth with a different
    assumption entirely (fixed 2026-09-08 — this used to grow with
    `inflation` instead, item 2 in the same backlog section). Defaults to
    0 (flat nominal salary) if no raise assumption is set, matching every
    other salary_growth_pct consumer's own default.

    Net-of-tax at the flat SECOND_EARNER_NET_OF_TAX_FACTOR approximation
    — not a real payroll-tax model (backlog item 3, same section).

    Shared by every withdrawal-phase consumer so this formula lives in
    exactly one place — projection_engine.py's own run_retirement_projection
    (via annual_inputs.py's build_annual_income_inputs) and
    simulation_engine.py's consumers both call this directly. Caller
    applies further per-year growth via (1+salary_growth_pct)**yr for
    yr < justin_gap_years, the same growing-annuity convention
    contributions already use."""
    justin_gap_years = max(0, justin_years_to_retire - years_to_retire)
    justin_salary = inputs.get("justin_w2_salary", 0)
    justin_gap_income_at_start = (
        justin_salary * SECOND_EARNER_NET_OF_TAX_FACTOR * ((1 + salary_growth_pct) ** years_to_retire)
        if justin_gap_years > 0 else 0.0
    )
    return justin_gap_years, justin_gap_income_at_start


def justin_gap_income_for_year(yr: int, justin_gap_years: int, justin_gap_income_at_start: float,
                                salary_growth_pct: float) -> float:
    """Allocation-free per-year lookup for second-earner gap income —
    pure floats in/out, no object construction, so it's cheap enough to
    call inside run_swr_analysis's binary-search inner loop and
    run_tax_efficiency_simulation's per-trial strategies (both
    documented performance exceptions to the shared annual_engine.py/
    annual_inputs.py machinery — see CALCULATION_CONTRACT.md sections
    4/10, and 13/14 for why this feature needed its own lightweight
    path rather than requiring those two to eat the migration cost they
    were explicitly measured too slow for).

    Single documented wage-growth convention: salary_growth_pct (the
    same assumed-raise rate every other second-earner income stream —
    401k contributions, RSU, bonus, and justin_gap_income_at_start
    itself — already uses), never inflation. See
    justin_gap_income_inputs's own docstring for the full reasoning
    (CALCULATION_CONTRACT.md section 14, item 2).

    annual_inputs.py's build_annual_income_inputs calls this directly
    too (2026-09-08 second follow-up) rather than keeping its own
    parallel copy of the same one-line formula — every consumer now
    reads gap income for a given year through exactly this function,
    whether via the dataclass-returning shared builder or directly.

    Returns 0.0 for yr >= justin_gap_years — Justin has already retired
    by this year (or was never given a gap to begin with, the
    justin_gap_years == 0 default-safe case)."""
    if yr >= justin_gap_years:
        return 0.0
    return justin_gap_income_at_start * ((1 + salary_growth_pct) ** yr)


def pension_for_age(inputs: Dict, age: int) -> float:
    """Pension is defined at 55/60/65 in Settings; interpolate linearly between
    those anchor points for any other retirement age (e.g. a sensitivity sweep
    or Monte Carlo run at an in-between age). Shared by every module that needs
    a pension figure for an arbitrary retirement age — do not reimplement this
    interpolation inline elsewhere.

    Intentionally a single household-level figure, not split per spouse:
    Settings labels this "Pension (100% Joint & Survivor)" — one employer
    pension (Jason's, in the default data) with a joint-and-survivor
    election that continues paying Justin after Jason's death, not two
    independent pensions. `age` is always Jason's age at every call site
    (his own selected/swept ret_age) — there is no separate justin_ret_age
    version of this lookup, and none is planned unless a household with a
    genuinely independent second pension asks for one. See
    docs/CALCULATION_CONTRACT.md section 13, backlog item 4."""
    p55 = inputs.get("pension_55", PENSION_100J_S_DEFAULT[55])
    p60 = inputs.get("pension_60", PENSION_100J_S_DEFAULT[60])
    p65 = inputs.get("pension_65", PENSION_100J_S_DEFAULT[65])
    if age <= 55:   return p55
    elif age <= 60: return p55 + (p60 - p55) * (age - 55) / 5
    elif age <= 65: return p60 + (p65 - p60) * (age - 60) / 5
    else:           return p65


def run_retirement_projection(inputs: Dict, accounts: List[Dict], ret_ages: List[int] = None,
                               salary_growth_pct: float = None, life_events: List[Dict] = None,
                               surplus_allocations: List[Dict] = None,
                               jason_ss_claim_age: int = None, justin_ss_claim_age: int = None) -> Dict:
    """salary_growth_pct: assumed annual raise rate applied to the 401k
    contribution base (annual_401k_pretax/roth, both derived from salary)
    every year until retirement, compounding — e.g. 0.03 for 3%/yr raises.
    Defaults to 0 (flat contributions, the historical behavior) so every
    other caller is unaffected; only the What-If tool currently sets it.

    life_events: rows from the life_events table (already filtered by the
    caller to only the ones with included_in_projection true). Defaults to
    None/empty so every existing caller that doesn't pass this is
    completely unaffected. See _split_life_events/_pre_retirement_taxable_add/
    _post_retirement_year_effects above for the actual math.

    surplus_allocations: rows from the surplus_allocations table (only
    "Retirement contributions" and "Taxable investing" matter here — see
    _surplus_allocations_at_retirement's comment for why the other five
    goals are excluded). Unlike life_events, this has no post-retirement
    half at all: the extra money simply stops being contributed the moment
    retirement starts, same as any other 401k/taxable contribution above.
    Defaults to None/empty so every existing caller is unaffected.

    Justin's own income (justin_w2_salary/justin_employee_401k_pct/
    justin_employer_401k_pct/justin_annual_bonus_pct/justin_annual_rsu_value,
    2026-09-08): a second, independent pre-retirement earnings profile for a
    household where both spouses work full-time, instead of one combined
    "breadwinner" income. justin_w2_salary defaults to 0, so this changes
    nothing for a household that never fills it in. justin_ret_age (0 =
    unset) independently controls how many years Justin's OWN contributions
    run, decoupled from whichever Jason ret_age this scenario column is
    sweeping — 0 falls back to years_to_retire (same age-gap-implied timing
    every consumer used before this existed).

    What this does NOT do: model Justin continuing to earn (and therefore
    reducing withdrawal-phase spending need) during years where Jason has
    already retired but Justin hasn't reached justin_ret_age yet — i.e. no
    "phased retirement, one spouse still working" bridge income for this
    general case. bridge_income_55/bridge_years_55 already model a narrower
    version of exactly that, but only for the ret_age==55 scenario column
    specifically (a pre-existing, separately-scoped limitation, not
    introduced here) — see CALCULATION_CONTRACT.md if that gap is worth
    closing later."""
    salary_growth_pct = inputs.get("_salary_growth_pct", 0.0) if salary_growth_pct is None else salary_growth_pct
    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    inflation  = inputs["inflation_rate"]
    pre_ret    = inputs["expected_return_pre_retirement"]
    post_ret   = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]
    annual_hsa   = inputs["annual_hsa_contribution"]
    annual_rsu   = inputs["annual_rsu_value"]
    # Justin takes spousal benefit = 50% of Jason FRA benefit at his FRA 67
    # Cannot claim until Jason has filed, so start age = max(Justin FRA, Jason SS age)
    # External audit review of commit aaa3cf5, finding 1 (P1): this used
    # to duplicate resolve_ss_benefits' own anchor-resolution logic
    # inline, so it never got finding 2's falsy-aware `or` fallback fix
    # (section 51) -- an unfilled (0-valued) justin_ss_early/
    # justin_ss_70 anchor still interpolated straight down/up toward $0
    # here, even though Settings' own preview and every other consumer
    # (Monte Carlo, Stress Tests, etc.) already estimated a sensible
    # FRA-based figure instead. Delegating to resolve_ss_benefits keeps
    # this the single source of anchor-resolution logic, so a future
    # fix to that fallback never needs a second, parallel update here
    # again. ss_timing="early" here only affects Jason's half of the
    # return value (immediately discarded below, since Jason's own
    # early/delayed/custom scenarios are still generated per ret_age in
    # the loop that follows) -- it has no effect on Justin's resolution,
    # which never depended on ss_timing even before this fix.
    _, _, justin_ss_annual, justin_ss_age = resolve_ss_benefits(inputs, "early", None, justin_ss_claim_age)

    # ── Starting balances by bucket ───────────────────────────────────────────
    # Get 401k total from accounts, apply split % from planning_inputs
    total_401k = sum(a["balance"] for a in accounts if a["account_type"] == "401k")
    pretax_pct = inputs.get("pretax_401k_pct", 0.75)
    roth_pct   = 1.0 - pretax_pct
    pretax_401k = total_401k * pretax_pct if total_401k > 0 else PRETAX_401K_BALANCE_DEFAULT
    roth_401k   = total_401k * roth_pct   if total_401k > 0 else ROTH_401K_BALANCE_DEFAULT

    # Get annual contribution rates from planning_inputs
    salary      = inputs.get("w2_salary", 0)
    emp_pct     = inputs.get("employee_401k_pct", 0.06)
    er_pct      = inputs.get("employer_401k_pct", 0.09)
    annual_401k_roth   = inputs.get("annual_401k_roth_employee",  salary * emp_pct)
    annual_401k_pretax = inputs.get("annual_401k_pretax_employer", salary * er_pct)
    # Recurring annual bonus, as a fraction of salary (unlike annual_rsu_value,
    # a flat dollar amount) — scales with salary_growth_pct the same way the
    # 401k contributions above do, since a bonus expressed as % of salary
    # naturally grows alongside raises.
    annual_bonus = salary * inputs.get("annual_bonus_pct", 0)

    # Justin's own pre-retirement earnings (2026-09-08) — mirrors the block
    # above for a household where both spouses work full-time instead of one
    # combined/breadwinner-shaped income. justin_w2_salary defaults to 0, so
    # every contribution term below is 0 and an existing single-earner
    # household sees no change at all. justin_years_to_retire (used below,
    # per ret_age) is what actually decouples Justin's own accumulation
    # window from whichever Jason ret_age this scenario column represents.
    justin_salary  = inputs.get("justin_w2_salary", 0)
    justin_emp_pct = inputs.get("justin_employee_401k_pct", 0.06)
    justin_er_pct  = inputs.get("justin_employer_401k_pct", 0.03)
    justin_annual_401k_roth   = justin_salary * justin_emp_pct
    justin_annual_401k_pretax = justin_salary * justin_er_pct
    justin_annual_bonus = justin_salary * inputs.get("justin_annual_bonus_pct", 0)
    justin_annual_rsu   = inputs.get("justin_annual_rsu_value", 0)

    # Pre-tax: traditional 401k slice + Justin IRA
    pretax_start = pretax_401k + sum(
        a["balance"] for a in accounts
        if a["account_type"] == "ira" and a["owner"] not in ("abby","cooper")
    )
    # Roth: roth 401k slice + all Roth IRAs (excluding kids)
    roth_start = roth_401k + sum(
        a["balance"] for a in accounts
        if a["account_type"] == "roth_ira" and a["owner"] not in ("abby","cooper")
    )
    # Taxable brokerage
    taxable_start = sum(
        a["balance"] for a in accounts
        if a["account_type"] == "taxable" and a["owner"] not in ("abby","cooper")
    )  # DAF excluded — charitable money, not investable
    # HSA
    hsa_start = sum(
        a["balance"] for a in accounts
        if a["account_type"] == "hsa"
    )

    scenarios = []

    jason_ss_early   = inputs.get("jason_social_security", JASON_SS_EARLY_DEFAULT)
    jason_ss_delayed = inputs.get("jason_ss_delayed", jason_ss_early * JASON_SS_DELAYED_RATIO)

    # Continuous SS claiming age 62-70 (CALCULATION_CONTRACT.md section
    # 44, milestone 1; made an EXPLICIT param rather than read off
    # `inputs` as of milestone 6 -- see justin_ss_claim_age's identical
    # note above) -- opt-in only. When set, this REPLACES the
    # two-scenario early/delayed sweep below with a single "custom"
    # scenario at the exact claim age, computed via the same real-anchor
    # formula -- every existing caller that doesn't set jason_ss_claim_age
    # gets the unchanged early+delayed pair, same scenarios/labels as
    # before.
    if jason_ss_claim_age is not None:
        # External audit review of commit aaa3cf5, finding 1 (P1): same
        # fix as Justin's block above -- this used to resolve
        # jason_ss_70 with the old, non-falsy-aware
        # inputs.get("jason_ss_70", jason_ss_delayed), which never
        # fired its fallback for an existing household's stored 0
        # (present key, not a missing one). Reproduced: Monte Carlo
        # (which goes through resolve_ss_benefits and got finding 2's
        # fix) estimated the FRA figure at 70 with a $0 anchor and
        # ended at $1,300,000, while this function's own income-sources
        # chart still showed $0 SS/yr for the same household. Delegating
        # to resolve_ss_benefits makes both agree, and any future
        # anchor-fallback fix only needs to change one place.
        _jason_ss_annual, _jason_ss_age, _, _ = resolve_ss_benefits(inputs, "early", jason_ss_claim_age, None)
        jason_ss_options = [
            ("custom", _jason_ss_annual, _jason_ss_age),
        ]
    else:
        jason_ss_options = [
            ("early",   jason_ss_early,   62),
            ("delayed", jason_ss_delayed, 67),
        ]

    for ret_age in (ret_ages if ret_ages is not None else [55, 60, 65]):
        years_to_retire = max(0, ret_age - jason_age)
        # The retirement-age button list is a fixed [55..67] range shown
        # regardless of the household's actual current age — someone
        # jason_age=65 can still pick ret_age=55. years_to_retire correctly
        # clamps to 0 for that case (already past that age), but the
        # withdrawal-phase loop below used to label every row's age
        # starting from ret_age itself (55, 56, 57, ...) instead of the
        # household's REAL current age — a fictional decade-younger
        # household, with Social Security claiming ages evaluated against
        # that fictional timeline instead of the real one, so SS that's
        # actually already due got modeled as still years away (external
        # audit 2026-09-06). withdrawal_start_age anchors the yearly loop's
        # age labeling (and the mortality/retire_years window below it) to
        # whichever is later: the selected ret_age (the normal, still-in-
        # the-future case, where this is just ret_age unchanged) or the
        # household's actual current age (the already-past-that-age case).
        # timeline_engine.build_timeline is the single shared source of
        # this computation now (consolidation follow-up, 2026-09-07) —
        # this function was the reference implementation every other
        # withdrawal-phase consumer's own version was modeled on, so it
        # moves onto the shared module too rather than staying a 6th
        # independent copy.
        timeline = build_timeline(jason_age, justin_age, ret_age, inputs.get("retirement_end_age"))
        withdrawal_start_age = timeline.effective_start_age

        # Justin's own accumulation window (2026-09-08) — if justin_ret_age
        # is set, Justin's contributions run for HIS OWN years-to-retire,
        # independent of whichever Jason ret_age this scenario column is
        # sweeping. If unset (0, the default), falls back to years_to_retire
        # — the same age-gap-implied timing every consumer already used
        # before this existed, so a household that never fills in Justin's
        # own retirement age sees identical numbers to before.
        justin_years_to_retire = justin_years_to_retire_for(inputs, justin_age, years_to_retire)
        # Justin's contributions only accrue up through whichever comes
        # first: his own retirement, or this scenario's own withdrawal
        # start (years_to_retire) — contributions he'd make WHILE Jason has
        # already retired but Justin hasn't yet (justin_years_to_retire >
        # years_to_retire) aren't modeled here, matching this function's
        # documented scope (no phased-retirement bridge income for the
        # general case). If Justin retires EARLIER (the more common ask —
        # a second full-time income with its own, often shorter, runway),
        # his contribution sum doesn't just stop growing at that point — it
        # sits invested and keeps compounding for the remaining years until
        # this scenario's own withdrawal start, same "contributions stop
        # early, balance keeps compounding" pattern _project_529_saving_phase
        # already uses for the 529 saving phase.
        justin_contrib_years = min(justin_years_to_retire, years_to_retire)
        justin_dormant_years = max(0, years_to_retire - justin_years_to_retire)

        # Second-earner gap income — see justin_gap_income_inputs's own
        # docstring (CALCULATION_CONTRACT.md section 13, backlog items
        # 1-3). Computed once here (independent of ss_label, same as
        # justin_contrib_years above) and reused in the yearly withdrawal
        # loop below.
        justin_gap_years, justin_gap_income_at_start = justin_gap_income_inputs(
            inputs, justin_years_to_retire, years_to_retire, salary_growth_pct)

        def _justin_contrib_fv(annual_amount):
            if annual_amount <= 0:
                return 0.0
            fv_at_his_stop = (
                _fv_growing_annuity(annual_amount, pre_ret, salary_growth_pct, justin_contrib_years)
                if salary_growth_pct else _fv_annuity(annual_amount, pre_ret, justin_contrib_years)
            )
            return _fv(fv_at_his_stop, pre_ret, justin_dormant_years)

        pension_annual  = pension_for_age(inputs, ret_age)

        # Life events split by calendar year relative to this ret_age's
        # retirement year — independent of ss_label, so computed once here
        # rather than inside the ss_label loop below.
        retirement_year_for_events = timeline.retirement_year
        pre_life_events, post_life_events = _split_life_events(life_events, retirement_year_for_events)
        post_life_events = post_life_events + _post_retirement_asset_sale_events(inputs, jason_age, timeline.effective_start_age)
        life_events_taxable_add = _pre_retirement_taxable_add(pre_life_events, pre_ret, retirement_year_for_events)

        # Surplus allocations compound from today through this ret_age's
        # retirement — generic across every ret_age in the loop, computed
        # once here (independent of ss_label) same as the life events above.
        surplus_at_ret = _surplus_allocations_at_retirement(surplus_allocations, pre_ret, years_to_retire)

        for ss_label, jason_ss_annual, jason_ss_age in jason_ss_options:
            # ── Project each bucket to retirement ─────────────────────────────
            # Contributions run every year up to retirement regardless of
            # retirement age — this used to stop entirely for the 55
            # scenario even when retirement was years away (bug found by
            # external audit 2026-09-05: someone currently 40 planning to
            # retire at 55 got 0 years of modeled 401k/Roth/HSA/RSU
            # contributions, understating their projected balance). The
            # annuity functions already return 0 when years_to_retire is 0
            # (already at/past that age), so this is correct for every case.
            #
            # 401k contributions are salary-derived (salary * pct) — if an
            # annual raise rate is assumed, grow them at that rate each year
            # rather than holding them flat to retirement.
            pretax_contrib_fv = (
                _fv_growing_annuity(annual_401k_pretax, pre_ret, salary_growth_pct, years_to_retire)
                if salary_growth_pct else _fv_annuity(annual_401k_pretax, pre_ret, years_to_retire)
            )
            roth_contrib_fv = (
                _fv_growing_annuity(annual_401k_roth, pre_ret, salary_growth_pct, years_to_retire)
                if salary_growth_pct else _fv_annuity(annual_401k_roth, pre_ret, years_to_retire)
            )
            # Justin's own 401k contributions, over HIS OWN accumulation
            # window — capped at this scenario's own withdrawal start, then
            # (if he stops contributing earlier) compounding further
            # untouched until that point. Existing pretax_start/roth_start
            # account balances already include both spouses' accounts
            # regardless of owner, so only the contribution FLOW needs its
            # own timing here.
            pretax_contrib_fv += _justin_contrib_fv(justin_annual_401k_pretax)
            roth_contrib_fv   += _justin_contrib_fv(justin_annual_401k_roth)
            pretax_at_ret = _fv(pretax_start, pre_ret, years_to_retire) + pretax_contrib_fv
            roth_at_ret   = _fv(roth_start,   pre_ret, years_to_retire) + roth_contrib_fv
            taxable_at_ret = _fv(taxable_start, pre_ret, years_to_retire)
            hsa_at_ret = (
                _fv(hsa_start, pre_ret, years_to_retire) +
                _fv_annuity(annual_hsa, pre_ret, years_to_retire)
            )
            if annual_rsu > 0:
                taxable_at_ret += _fv_annuity(annual_rsu * SECOND_EARNER_NET_OF_TAX_FACTOR, pre_ret, years_to_retire)
            taxable_at_ret += _justin_contrib_fv(justin_annual_rsu * SECOND_EARNER_NET_OF_TAX_FACTOR)
            if annual_bonus > 0:
                # Same net-of-tax treatment as RSUs above (bonuses are
                # withheld at a higher supplemental-wage rate in practice,
                # and this codebase doesn't model payroll tax brackets in
                # this pre-retirement accumulation phase, so it reuses the
                # existing approximation rather than inventing a second
                # one). Grows with salary_growth_pct like the 401k
                # contributions above, since this is defined as a % of
                # salary rather than a flat dollar amount.
                bonus_fv = (
                    _fv_growing_annuity(annual_bonus * SECOND_EARNER_NET_OF_TAX_FACTOR, pre_ret, salary_growth_pct, years_to_retire)
                    if salary_growth_pct else _fv_annuity(annual_bonus * SECOND_EARNER_NET_OF_TAX_FACTOR, pre_ret, years_to_retire)
                )
                taxable_at_ret += bonus_fv
            taxable_at_ret += _justin_contrib_fv(justin_annual_bonus * SECOND_EARNER_NET_OF_TAX_FACTOR)

            # Sale proceeds — a sale is real regardless of which retirement
            # age this scenario column happens to be modeling, same as life
            # events just below ("generic for every ret_age, not just the
            # age-55 bridge case"). This used to run only inside an
            # `if ret_age == 55:` gate, written back when 55 was the only
            # bridge scenario that needed it — but that left every other
            # age (56-67) silently ignoring these proceeds entirely, and
            # even within the 55 branch a sale_age set *after* 55 (e.g. 56)
            # failed the "already happened" check and was dropped there
            # too, so it was possible for an asset sale to count in exactly
            # zero scenarios (found via a bug-hunt sandbox, confirmed
            # against this app's real data: a sale_age of 56 meant this
            # $75K+ never appeared in any of the 55-67 columns).
            # Compared against timeline.effective_start_age, not raw
            # ret_age (independent review, 2026-09-07, third follow-up —
            # see _post_retirement_asset_sale_events' docstring for the
            # full account): a sale dated between an already-past
            # ret_age and the household's real current age used to fall
            # through both this check AND that function's own exclusion,
            # vanishing entirely.
            #
            # yrs_to_grow is measured as (years from TODAY to
            # effective_start_age) minus (years from TODAY to the sale,
            # floored at 0) — a follow-up correction (independent review,
            # 2026-09-07, fourth pass) to the fix above, which measured
            # yrs_to_grow as `effective_start_age - sale_age` directly.
            # That silently credited pre-retirement INVESTMENT RETURN for
            # calendar years already in the past whenever the sale
            # predated jason_age (reproduced: age 65 today, retiring at
            # 70, a sale at 60 with 10% returns added $259,374 instead of
            # the correct $161,051 — 10 years of compounding instead of
            # the actual 5 remaining accumulation years to retirement).
            # This formula reduces to the exact original
            # `years_to_retire - yrs_assetN` whenever ret_age >=
            # jason_age (max(0, effective_start_age-jason_age) ==
            # years_to_retire there), and gives 0 growth years (proceeds
            # added at face value, not zero — see the inclusion check
            # above) for a sale that's already behind TODAY even in the
            # past-ret_age case, rather than inventing a historical
            # reconstruction this app has no data to support.
            years_from_today_to_start = max(0, timeline.effective_start_age - jason_age)
            asset1_sale_age = inputs.get("asset1_sale_age", 0)
            asset1_sale_net = inputs.get("asset1_sale_net", 0)
            asset1_app      = inputs.get("asset1_appreciation", 0.03)
            if asset1_sale_age and asset1_sale_age <= timeline.effective_start_age:
                yrs_asset1 = max(0, asset1_sale_age - jason_age)
                yrs_to_grow = years_from_today_to_start - yrs_asset1
                asset1_proceeds = asset1_sale_net * ((1 + asset1_app) ** yrs_asset1)
                taxable_at_ret  += asset1_proceeds * ((1 + pre_ret) ** yrs_to_grow)
            asset2_sale_age = inputs.get("asset2_sale_age", 0)
            asset2_sale_net = inputs.get("asset2_sale_net", 0)
            if asset2_sale_age and asset2_sale_age <= timeline.effective_start_age:
                yrs_asset2    = max(0, asset2_sale_age - jason_age)
                yrs_to_grow    = years_from_today_to_start - yrs_asset2
                taxable_at_ret += asset2_sale_net * ((1 + pre_ret) ** yrs_to_grow)

            # Life events dated before retirement (compounded above) —
            # generic for every ret_age, not just the age-55 bridge case.
            taxable_at_ret += life_events_taxable_add

            # Surplus allocations ("Assign Surplus" page) that represent
            # money actually invested toward retirement — see
            # _surplus_allocations_at_retirement's comment above for why
            # only these two goals qualify. "Retirement contributions"
            # splits pretax/roth using the SAME ratio as the existing
            # 401k contributions above (pretax_pct/roth_pct); "Taxable
            # investing" lands entirely in the taxable bucket. Both stop
            # contributing at the moment of retirement — there is
            # deliberately no post-retirement half of this feature (unlike
            # life events), so nothing further happens to these dollars
            # in the yearly withdrawal loop below beyond normal growth.
            if surplus_at_ret["retirement_contrib"]:
                pretax_at_ret += surplus_at_ret["retirement_contrib"] * pretax_pct
                roth_at_ret   += surplus_at_ret["retirement_contrib"] * roth_pct
            if surplus_at_ret["taxable_investing"]:
                taxable_at_ret += surplus_at_ret["taxable_investing"]

            portfolio_at_ret = pretax_at_ret + roth_at_ret + taxable_at_ret + hsa_at_ret

            # ── Capitalization for summary ────────────────────────────────────
            income_at_ret = income_today * ((1 + inflation) ** years_to_retire)
            # The planning horizon belongs to the household, not a hidden
            # engine constant. Keep a reasonable guardrail so a typo cannot
            # produce a negative/implausible drawdown period.
            # Anchored to withdrawal_start_age, not ret_age, for the same
            # reason as above — otherwise an already-past ret_age would
            # compute retire_years from the wrong (younger, fictional)
            # starting point and run the withdrawal loop years past the
            # household's real mortality/planning-horizon age. Identical to
            # the old `ret_age`-based formula whenever ret_age >= jason_age
            # (withdrawal_start_age == ret_age in that case).
            mort_age      = timeline.end_age
            retire_years  = timeline.retire_yrs
            cum_inflation = build_cumulative_inflation(inflation, retire_years)

            healthcare_pre       = inputs.get("healthcare_pre_medicare", 0)
            healthcare_post      = inputs.get("healthcare_post_medicare", 0)
            healthcare_kids      = inputs.get("healthcare_kids", 0)
            kids_annual_cost     = inputs.get("kids_annual_cost", 0)
            bridge_income        = inputs.get("bridge_income_55", 0)
            bridge_years         = inputs.get("bridge_years_55", 0)
            kids_years           = inputs.get("kids_years_at_home_55", 0)

            # Convert today's-dollar expenses and bridge income once;
            # both annual rows and headline funding use these cash flows.
            healthcare_pre_at_ret   = healthcare_pre   * ((1 + inflation) ** years_to_retire)
            healthcare_post_at_ret  = healthcare_post  * ((1 + inflation) ** years_to_retire)
            healthcare_kids_at_ret  = healthcare_kids  * ((1 + inflation) ** years_to_retire)
            kids_annual_cost_at_ret = kids_annual_cost * ((1 + inflation) ** years_to_retire)
            bridge_income_at_ret    = bridge_income    * ((1 + inflation) ** years_to_retire)

            # ── Year-by-year with buckets and RMDs ───────────────────────────
            pretax  = pretax_at_ret
            roth    = roth_at_ret
            taxable = taxable_at_ret
            hsa     = hsa_at_ret
            jason_rmd_start_age = rmd_start_age(jason_age)
            # Lazy import to avoid a circular import (retirement_tools_engine
            # imports from this module at load time); cheap after the first
            # call since Python caches the module.
            from retirement_tools_engine import marginal_rate as _marginal_rate, STD_DEDUCTION_MFJ_2026 as _STD_DED

            yearly = []
            for yr in range(retire_years):
                age = timeline.age(yr)
                calendar_year = timeline.calendar_year(yr)

                # Shared annual-input builder (consolidation, 2026-09-07):
                # Social Security (COLA'd from each spouse's own claim
                # age) and signed life-event offsets, computed once here
                # instead of duplicated inline below. Healthcare is
                # consolidated too, EXCEPT inside the ret_age==55
                # bridge/kids branch just below, which applies a
                # genuinely different, deliberate policy (family/kids
                # healthcare, bridge-job phase) this consolidation is
                # instructed to preserve rather than erase.
                income = build_annual_income_inputs(
                    timeline, yr, cum_inflation, inflation,
                    healthcare_pre_at_start=healthcare_pre_at_ret, healthcare_post_at_start=healthcare_post_at_ret,
                    jason_ss_annual=jason_ss_annual, jason_ss_age=jason_ss_age,
                    justin_ss_annual=justin_ss_annual, justin_ss_age=justin_ss_age,
                    post_life_events=post_life_events, post_retirement_year_effects=_post_retirement_year_effects,
                    justin_gap_years=justin_gap_years, justin_gap_income_at_start=justin_gap_income_at_start,
                    salary_growth_pct=salary_growth_pct,
                )
                life_event_cash_this_year = income.life_event_cash
                life_event_monthly_this_year = income.life_event_monthly
                justin_age_this_year = income.justin_age

                # Income need this year (includes healthcare, phased for age 55)
                if ret_age == 55:
                    kids_still_home = yr < kids_years
                    bridge_active   = yr < bridge_years
                    if bridge_active:
                        # Phase 1: bridge job covers healthcare, net of bridge income
                        healthcare_this_year = 0
                        kids_cost = kids_annual_cost_at_ret * ((1 + inflation) ** yr)
                        bridge    = bridge_income_at_ret    * ((1 + inflation) ** yr)
                        year_need = max(0, income_at_ret * ((1 + inflation) ** yr) + kids_cost - bridge)
                    elif kids_still_home and age < 65:
                        # Phase 2: retired, kids home, family healthcare
                        healthcare_this_year = healthcare_kids_at_ret
                        kids_cost = kids_annual_cost_at_ret * ((1 + inflation) ** yr)
                        year_need = income_at_ret * ((1 + inflation) ** yr) + kids_cost + healthcare_kids_at_ret * ((1 + inflation) ** yr)
                    elif age < 65:
                        # Phase 3: empty nest, pre-Medicare
                        healthcare_this_year = healthcare_pre_at_ret
                        year_need = income_at_ret * ((1 + inflation) ** yr) + healthcare_pre_at_ret * ((1 + inflation) ** yr)
                    else:
                        # Phase 4: Medicare
                        healthcare_this_year = healthcare_post_at_ret
                        year_need = income_at_ret * ((1 + inflation) ** yr) + healthcare_post_at_ret * ((1 + inflation) ** yr)
                    healthcare_inflated = healthcare_this_year * ((1 + inflation) ** yr)
                else:
                    healthcare_inflated  = income.healthcare
                    year_need = income_at_ret * ((1 + inflation) ** yr) + healthcare_inflated

                # Life events landing in the withdrawal phase — generic
                # across every ret_age, not just 55. A recurring monthly
                # delta adjusts this year's need directly (positive delta
                # is extra income, so it reduces need); a one-time delta
                # lands in the taxable bucket below instead of the income
                # need, same as rmd_reinvested's treatment. Computed above
                # via the shared builder for both branches.
                year_need -= life_event_monthly_this_year

                # Second-earner gap income — see justin_gap_income_inputs's
                # docstring. Active only for the first justin_gap_years of
                # the withdrawal phase; Justin's contribution/salary
                # timeline is otherwise entirely a pre-retirement concept,
                # so this is the one place it reaches into the withdrawal
                # loop. Computed by the shared builder above (same
                # salary_growth_pct wage-growth convention every other
                # second-earner income stream uses — backlog item 2: this
                # used to grow with inflation instead, fixed 2026-09-08).
                year_need -= income.justin_gap_income

                # Fixed income sources. Pension is frozen/no-COLA — a
                # deliberate, consumer-specific policy this consolidation
                # preserves rather than folding into the shared builder
                # (see annual_inputs.py's module docstring). Social
                # Security is the shared builder's output.
                year_pen = pension_annual
                year_jss = income.jason_ss
                year_uss = income.justin_ss
                fixed_income = year_pen + year_jss + year_uss

                # RMD on pre-tax bucket
                rmd = _rmd(pretax, age, jason_rmd_start_age)

                # Estimated tax on pretax withdrawals — previously
                # withdrawal_pretax and reinvested RMD excess were treated
                # as tax-free, overstating both "after-tax spending" and the
                # taxable bucket (RMD excess was reinvested at its full
                # pretax amount, though you never actually get to keep the
                # untaxed dollars). Approximated with the same MFJ bracket
                # table used elsewhere in the app and the same
                # 85%-of-SS-is-taxable convention as simulation_engine.py.
                # Still approximate — no NIIT, no itemizing, no tax lots or
                # credits, single blended rate per year — but far closer than
                # treating distributions as tax-free. State tax is an
                # explicit optional planning input, not an implicit guess.
                taxable_income_est = max(0, year_pen + (year_jss + year_uss) * 0.85 + rmd - _STD_DED)
                pretax_tax_rate = min(0.90, _marginal_rate(taxable_income_est) + max(0, float(inputs.get("state_income_tax_rate") or 0)))

                # Draw order: taxable first, then pretax (satisfies RMD
                # minimum), then HSA, then roth last (let it compound).
                # This is the shared withdrawal-phase step
                # (annual_engine.simulate_withdrawal_year) — see
                # docs/CALCULATION_CONTRACT.md. taxable_rate=0 preserves
                # this engine's existing "no tax modeled on taxable draws"
                # simplification (CALCULATION_CONTRACT.md 3.1) exactly.
                year_result = simulate_withdrawal_year(
                    opening=AccountState(pretax=pretax, roth=roth, taxable=taxable, hsa=hsa),
                    spending_need=year_need,
                    guaranteed_income=fixed_income,
                    life_event_cash=life_event_cash_this_year,
                    rmd_amount=rmd,
                    tax_model=marginal_bracket_tax_model(pretax_rate=pretax_tax_rate, taxable_rate=0.0),
                    growth_rate=post_ret,
                    order=DEFAULT_ORDER,
                )

                withdrawal_taxable = year_result.draws.get("taxable", 0.0)
                # draws["pretax"] includes both the mandatory RMD and any
                # further discretionary pretax draw; withdrawal_pretax
                # reports their sum, matching this field's existing
                # meaning (see yearly.append below).
                withdrawal_pretax  = year_result.draws.get("pretax", 0.0)
                withdrawal_hsa     = year_result.draws.get("hsa", 0.0)
                withdrawal_roth    = year_result.draws.get("roth", 0.0)
                rmd_reinvested     = year_result.rmd_reinvested
                # pretax_tax_owed reports tax on pretax draws only (RMD +
                # discretionary), matching this field's existing meaning —
                # taxable/hsa/roth draws are untaxed in this model, so
                # total_tax and the pretax-only tax are the same figure,
                # but computed explicitly rather than assumed identical
                # (a future taxable_rate change here shouldn't silently
                # relabel taxable's own tax as "pretax tax owed").
                pretax_tax_owed = year_result.taxes_paid.get("rmd", 0.0) + year_result.taxes_paid.get("pretax", 0.0)

                # Whatever's left after every bucket has been tried is
                # genuine unmet spending need — the plan literally could
                # not fund it this year. Previously this was silently
                # dropped on the floor: the success/on_track check only
                # ever looked at whether money remained, never whether the
                # year's spending need was actually met (external audit
                # 2026-09-06). Surfaced per-year below and rolled into
                # on_track/percent_funded at the scenario level.
                unmet_need = year_result.unmet_need

                total_withdrawal = withdrawal_taxable + withdrawal_pretax + withdrawal_roth + withdrawal_hsa

                pretax  = year_result.closing.pretax
                roth    = year_result.closing.roth
                taxable = year_result.closing.taxable
                hsa     = year_result.closing.hsa

                total_portfolio = pretax + roth + taxable + hsa

                yearly.append({
                    "jason_age":        age,
                    "justin_age":       timeline.justin_age_at(age),
                    "year":             calendar_year,
                    "income_need":      round(year_need),
                    "healthcare_cost":   round(healthcare_inflated),
                    "pension":          round(year_pen),
                    "social_security":  round(year_jss + year_uss),
                    "bridge_income":    round(bridge_income_at_ret * ((1+inflation)**yr)) if ret_age == 55 and yr < bridge_years else 0,
                    # Backlog item 5 (CALCULATION_CONTRACT.md section 13):
                    # exposed as its own line so a lower income_need during
                    # the gap years is explained, not just implied.
                    "justin_gap_income": round(income.justin_gap_income),
                    "life_event_cash":              round(life_event_cash_this_year),
                    "life_event_monthly_adjustment": round(life_event_monthly_this_year),
                    "rmd":              round(rmd),
                    "rmd_reinvested":   round(rmd_reinvested),
                    "estimated_tax":    round(pretax_tax_owed),  # approximate — see comment above
                    "withdrawal_pretax":round(withdrawal_pretax),
                    "withdrawal_taxable":round(withdrawal_taxable),
                    "withdrawal_roth":  round(withdrawal_roth),
                    "withdrawal_hsa":   round(withdrawal_hsa),
                    "withdrawal":       round(total_withdrawal),
                    "unmet_need":       round(unmet_need),
                    "pretax_balance":   round(pretax),
                    "roth_balance":     round(roth),
                    "taxable_balance":  round(taxable),
                    "hsa_balance":      round(hsa),
                    "portfolio_balance":round(total_portfolio),
                })

            # Discount the same beginning-of-year cash flows shown in the
            # table, including events and withdrawal taxes, to retirement.
            total_cap_need = sum(
                (max(0, y["income_need"]) + y["estimated_tax"] + max(0, -y["life_event_cash"]))
                / ((1 + post_ret) ** yr) for yr, y in enumerate(yearly))
            total_cap_income = sum(
                (y["pension"] + y["social_security"] + max(0, y["life_event_cash"]) + max(0, -y["income_need"]))
                / ((1 + post_ret) ** yr) for yr, y in enumerate(yearly))
            cap_needed_from_assets = max(0, total_cap_need - total_cap_income)
            surplus = portfolio_at_ret + total_cap_income - total_cap_need
            underfunded = any(y["unmet_need"] > 0 for y in yearly)
            pct_funded = max(0, min(99 if underfunded else 100, round(
                portfolio_at_ret / cap_needed_from_assets * 100
                if cap_needed_from_assets > 0 else 100)))

            scenarios.append({
                "label":                         f"age_{ret_age}_{ss_label}",
                "retirement_age":                ret_age,
                "ss_timing":                     ss_label,
                "years_to_retirement":           years_to_retire,
                "income_first_year":             round(income_at_ret),
                "income_today_dollars":          round(income_today),
                "pension_annual":                round(pension_annual),
                "jason_ss_annual":               round(jason_ss_annual),
                "jason_ss_start_age":            jason_ss_age,
                "justin_ss_annual":              round(justin_ss_annual),
                "justin_ss_start_age":           justin_ss_age,
                "healthcare_pre_annual":         inputs.get("healthcare_pre_medicare", 0),
                "healthcare_post_annual":        inputs.get("healthcare_post_medicare", 0),
                "healthcare_gap_years":          max(0, 65 - ret_age),
                "healthcare_gap_total":          inputs.get("healthcare_pre_medicare", 0) * max(0, 65 - ret_age),
                "total_capitalized_need":        round(total_cap_need),
                "capitalized_income_sources":    round(total_cap_income),
                "capitalized_needed_from_assets":round(cap_needed_from_assets),
                "portfolio_at_retirement":       round(portfolio_at_ret),
                "pretax_at_retirement":          round(pretax_at_ret),
                "roth_at_retirement":            round(roth_at_ret),
                "taxable_at_retirement":         round(taxable_at_ret),
                "hsa_at_retirement":             round(hsa_at_ret),
                "current_investable_assets":     round(pretax_start + roth_start + taxable_start + hsa_start),
                "projected_surplus":             round(surplus),
                "any_year_underfunded":          any(y["unmet_need"] > 0 for y in yearly),
                "on_track":                      surplus >= -0.5 and not underfunded,
                "percent_funded":                pct_funded,
                "retirement_end_age":            mort_age,
                "state_income_tax_rate":         inputs.get("state_income_tax_rate", 0),
                "yearly_detail":                 yearly,
                # Second-earner net-of-tax approximation transparency
                # (backlog P2, CALCULATION_CONTRACT.md section 16) —
                # surfaced so the UI/docs can flag that justin_gap_income
                # (and RSU/bonus accumulation) use this flat factor, not
                # a real payroll-tax calculation.
                "second_earner_net_of_tax_factor": SECOND_EARNER_NET_OF_TAX_FACTOR,
            })

    return {"scenarios": scenarios, "generated_at": datetime.datetime.now().isoformat()}


ACCOUNT_OWNERSHIP_LIMITATION_NOTE = (
    "Portfolio buckets (pretax/roth/taxable/hsa) are a single aggregated household total, not attributed to "
    "either spouse. During the middle phase, the still-working spouse's income surplus is swept into savings "
    "the same as every other consumer's -- it cannot be traced back to whose paycheck it came from."
)


def two_age_spending_need_fn(inputs: Dict, income_today: float, inflation: float, timeline,
                              inflation_mults: List[float] = None):
    """Factory returning a per-year spending-need function shared between
    run_two_dimensional_retirement_projection and simulation_engine.py's
    two-age Monte Carlo/Stress Tests trial loop (`_run_single_two_age`)
    -- ONE set of income/healthcare/bridge/kids formulas, not two
    independent copies (CALCULATION_CONTRACT.md section 22, explicit
    instruction: "avoid copying another independent set of income,
    pension, bridge, and contribution formulas").

    Encapsulates the same two branches run_two_dimensional_retirement_
    projection's own withdrawal loop used inline before this refactor:
    the age-55 bridge-job/kids-at-home spending phases (mirrors
    run_retirement_projection's `ret_age == 55` branch, anchored to
    timeline.jason_effective_start_age rather than the raw selected
    jason_ret_age -- CALCULATION_CONTRACT.md section 21), and the plain
    healthcare-only default for every other selected age. Every "at
    effective start" reference dollar figure is computed ONCE here
    (outside the returned per-year function) -- the same precedent
    run_retirement_projection/_run_single already use for their own
    once-per-scenario reference figures, so this factory itself is
    meant to be called once per scenario/trial-batch, not once per year.

    inflation_mults (independent review, 2026-09-08, "Incomplete scope"
    finding, CALCULATION_CONTRACT.md section 23): optional per-loop-year
    inflation multiplier list, same convention timeline_engine.
    build_cumulative_inflation already uses for stress scenarios like
    stagflation_1970s that deliberately run inflation hot for part of
    the horizon. Defaults to None (flat `inflation` every year), which
    reduces the cumulative-inflation curve below to the exact original
    (1+inflation)**yr formula for every index -- run_two_dimensional_
    retirement_projection's own calls never pass this, so its behavior
    (and every existing test asserting against it) is completely
    unaffected. Only simulation_engine.py's two-age Stress Tests passes
    a real (non-None) sequence.

    Returns need_for_year(age, yr) -> (year_need, healthcare_inflated,
    bridge_income_this_year), where `age` is Jason's absolute age this
    loop year and `yr` is 0-indexed relative to phase2_start (used only
    by the non-bridge default branch's own inflation compounding, same
    convention every other consumer's `yr` already uses)."""
    jason_ret_age = timeline.jason_ret_age
    jason_effective_start_age = timeline.jason_effective_start_age
    phase2_years = timeline.phase2_start_years

    healthcare_pre  = inputs.get("healthcare_pre_medicare", 0)
    healthcare_post = inputs.get("healthcare_post_medicare", 0)
    healthcare_kids      = inputs.get("healthcare_kids", 0)
    kids_annual_cost     = inputs.get("kids_annual_cost", 0)
    bridge_income        = inputs.get("bridge_income_55", 0)
    bridge_years         = inputs.get("bridge_years_55", 0)
    kids_years           = inputs.get("kids_years_at_home_55", 0)

    # Every dollar figure's pre-loop compounding anchors to phase2_start
    # -- the TRUE pre-loop boundary -- never to jason_effective_start_age,
    # even inside the bridge/kids branch (independent review, 2026-09-08,
    # third follow-up, P1). Under flat inflation these two anchors always
    # produced the identical number (compounding the same total number of
    # years via two different splits is the same arithmetic either way),
    # which is why the original section-21 design could anchor the
    # bridge/kids branch to jason_effective_start_age without it
    # mattering. That equivalence breaks under a VARIABLE per-year rate:
    # whenever Justin retires first, jason_effective_start_age can fall
    # strictly after phase2_start_age, meaning the years between them are
    # already INSIDE the withdrawal loop and must be subject to
    # inflation_mults like any other loop year -- anchoring to
    # jason_effective_start_age instead treated that whole span as flat
    # pre-loop compounding, and (worse) the per-year multiplier was then
    # rebased to exactly 1.0 at the phase boundary, discarding every year
    # of already-accumulated elevated inflation outright. Reproduced:
    # both spouses 53, Justin already retired, Jason retiring at 55,
    # $100,000 spend, 8% stressed inflation -- year 3 (age 55, the first
    # bridge year) reverted to $104,040 (the flat, no-stress figure)
    # instead of the correct $116,640. There is now only ONE dollar
    # baseline and one cum_inflation[yr] multiplier, shared by both
    # branches -- jason_yr (below) is used ONLY to compare against
    # bridge_years/kids_years, a genuinely separate "how long has this
    # phase been active" duration counter that legitimately does restart
    # at Jason's own retirement, not a second inflation clock.
    healthcare_pre_at_start   = healthcare_pre   * ((1 + inflation) ** phase2_years)
    healthcare_post_at_start  = healthcare_post  * ((1 + inflation) ** phase2_years)
    healthcare_kids_at_start  = healthcare_kids  * ((1 + inflation) ** phase2_years)
    kids_annual_cost_at_start = kids_annual_cost * ((1 + inflation) ** phase2_years)
    bridge_income_at_start    = bridge_income    * ((1 + inflation) ** phase2_years)
    income_at_start = income_today * ((1 + inflation) ** phase2_years)

    # cum_inflation[yr] is the accumulated price-growth factor from
    # phase2_start through the start of loop year yr -- the SAME
    # accumulate-don't-retroactively-erase-history technique
    # build_cumulative_inflation's own docstring explains, needed so a
    # stress scenario's inflation_mults can vary year to year without
    # silently un-compounding whatever came before. One curve, shared by
    # both branches -- no rebasing, no second anchor.
    cum_inflation = build_cumulative_inflation(inflation, timeline.retire_yrs, inflation_mults)

    def need_for_year(age, yr):
        cum = cum_inflation[yr]
        if jason_ret_age == 55 and age >= jason_effective_start_age:
            jason_yr = age - jason_effective_start_age  # duration counter only -- see docstring above
            kids_still_home = jason_yr < kids_years
            bridge_active   = jason_yr < bridge_years
            if bridge_active:
                healthcare_this_year = 0
                kids_cost = kids_annual_cost_at_start * cum
                bridge    = bridge_income_at_start    * cum
                year_need = max(0, income_at_start * cum + kids_cost - bridge)
                bridge_income_this_year = bridge
            elif kids_still_home and age < 65:
                healthcare_this_year = healthcare_kids_at_start
                kids_cost = kids_annual_cost_at_start * cum
                year_need = income_at_start * cum + kids_cost + healthcare_kids_at_start * cum
                bridge_income_this_year = 0.0
            elif age < 65:
                healthcare_this_year = healthcare_pre_at_start
                year_need = income_at_start * cum + healthcare_pre_at_start * cum
                bridge_income_this_year = 0.0
            else:
                healthcare_this_year = healthcare_post_at_start
                year_need = income_at_start * cum + healthcare_post_at_start * cum
                bridge_income_this_year = 0.0
            healthcare_inflated = healthcare_this_year * cum
        else:
            healthcare_inflated = healthcare_for_age(age, healthcare_pre_at_start, healthcare_post_at_start) * cum
            year_need = income_at_start * cum + healthcare_inflated
            bridge_income_this_year = 0.0
        return year_need, healthcare_inflated, bridge_income_this_year

    return need_for_year


def two_age_pension_for_year(pension_annual: float, age: int, jason_effective_start_age: int) -> float:
    """Jason's own pension starts only once he's actually retired --
    shared by run_two_dimensional_retirement_projection and its Monte
    Carlo/Stress Tests counterparts so this one-line gate isn't a second
    independent copy (CALCULATION_CONTRACT.md section 20)."""
    return pension_annual if age >= jason_effective_start_age else 0.0


def two_age_still_working_income_inputs(inputs: Dict, timeline, salary_growth_pct: float):
    """Phase2 duration and the still-working spouse's net-of-tax income
    baseline -- symmetric reuse of the same SECOND_EARNER_NET_OF_TAX_FACTOR
    approximation and per-year lookup (justin_gap_income_for_year) every
    other consumer's second-earner gap income already uses, applied to
    WHICHEVER spouse is timeline.later_retiree (not hardcoded to
    Justin). Shared by run_two_dimensional_retirement_projection and its
    Monte Carlo/Stress Tests counterparts.

    Returns (phase2_duration_years, still_working_income_at_start) --
    caller looks up a given year's actual amount via
    justin_gap_income_for_year(yr, phase2_duration_years,
    still_working_income_at_start, salary_growth_pct), the same call
    every existing gap-income consumer already makes."""
    phase2_duration_years = timeline.phase3_start_years - timeline.phase2_start_years
    if timeline.later_retiree == "justin":
        still_working_salary = inputs.get("justin_w2_salary", 0)
    elif timeline.later_retiree == "jason":
        still_working_salary = inputs.get("w2_salary", 0)
    else:
        still_working_salary = 0.0
    still_working_income_at_start = (
        still_working_salary * SECOND_EARNER_NET_OF_TAX_FACTOR * ((1 + salary_growth_pct) ** timeline.phase2_start_years)
        if phase2_duration_years > 0 else 0.0
    )
    return phase2_duration_years, still_working_income_at_start


def run_two_dimensional_retirement_projection(inputs: Dict, accounts: List[Dict], jason_ret_age: int, justin_ret_age: int,
                                                ss_timing: str = "early", salary_growth_pct: float = None,
                                                life_events: List[Dict] = None,
                                                surplus_allocations: List[Dict] = None,
                                                jason_ss_claim_age: int = None, justin_ss_claim_age: int = None) -> Dict:
    """Two independent, explicit retirement ages instead of
    run_retirement_projection's single ret_age -- a new, additive
    function per docs/TWO_DIMENSIONAL_RETIREMENT_DESIGN.md section 7.4,
    not a mode flag on the existing reference implementation. v1 scope
    (section 7, 2026-09-08): explicit ages for both spouses, one
    scenario at a time (no 13-age sweep, no heatmap); Survivor Scenario,
    real payroll-tax modeling, and an owner-attributed ledger are all
    explicitly out of scope here (see ACCOUNT_OWNERSHIP_LIMITATION_NOTE
    above).

    Reuses run_retirement_projection's own accumulation-phase helpers
    (_fv/_fv_annuity/_fv_growing_annuity, _split_life_events,
    _post_retirement_asset_sale_events, _pre_retirement_taxable_add,
    _surplus_allocations_at_retirement, _post_retirement_year_effects,
    pension_for_age) and the shared withdrawal-phase step
    (simulate_withdrawal_year) exactly as-is -- this function does not
    reimplement withdrawal-order, tax, or RMD policy, only the
    two-dimensional timing around it.

    A household passes through up to two loop phases here (accumulation
    before either retirement isn't modeled by this function, same as
    run_retirement_projection): "phase2" (one spouse retired, one still
    working -- new) and "phase3" (both retired, identical to
    run_retirement_projection's existing single-phase withdrawal loop).
    The still-working spouse's income during phase2 is modeled as a
    flat SECOND_EARNER_NET_OF_TAX_FACTOR (0.65) offset to spending need
    -- the SAME approximation and constant every other consumer's
    second-earner gap income already uses, applied symmetrically to
    whichever spouse is later_retiree (not hardcoded to Justin). No
    401k contribution is modeled from that income once the withdrawal
    loop has started (section 7.3) -- a documented simplification, not
    an oversight; real payroll-tax modeling during a withdrawal-phase
    loop is out of scope for v1.

    Regression property: when justin_ret_age == jason_ret_age (or falls
    back to it via the existing 0/unset sentinel), phase2 has zero
    years and this reduces exactly to run_retirement_projection's own
    single-axis output for that age -- verified numerically in
    test_two_dimensional_retirement.py's simultaneous-retirement case."""
    salary_growth_pct = inputs.get("_salary_growth_pct", 0.0) if salary_growth_pct is None else salary_growth_pct
    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    inflation  = inputs["inflation_rate"]
    pre_ret    = inputs["expected_return_pre_retirement"]
    post_ret   = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]
    annual_hsa   = inputs.get("annual_hsa_contribution", 0)
    annual_rsu   = inputs.get("annual_rsu_value", 0)

    # SS resolution (2026-09-08, CALCULATION_CONTRACT.md section 44,
    # milestone 5; made EXPLICIT params rather than read off `inputs`
    # as of milestone 6/ninth follow-up finding 1 -- a caller passing
    # the full inputs dict through for an unrelated reason must not
    # silently switch this function into continuous-claim-age mode).
    # resolve_ss_benefits replaces this function's own independent
    # ss_timing ternary.
    jason_ss_annual, jason_ss_age, justin_ss_annual, justin_ss_age = resolve_ss_benefits(
        inputs, ss_timing, jason_ss_claim_age, justin_ss_claim_age)

    # ── Starting balances by bucket -- identical to run_retirement_projection ──
    total_401k = sum(a["balance"] for a in accounts if a["account_type"] == "401k")
    pretax_pct = inputs.get("pretax_401k_pct", 0.75)
    roth_pct   = 1.0 - pretax_pct
    pretax_401k = total_401k * pretax_pct if total_401k > 0 else PRETAX_401K_BALANCE_DEFAULT
    roth_401k   = total_401k * roth_pct   if total_401k > 0 else ROTH_401K_BALANCE_DEFAULT

    salary      = inputs.get("w2_salary", 0)
    emp_pct     = inputs.get("employee_401k_pct", 0.06)
    er_pct      = inputs.get("employer_401k_pct", 0.09)
    annual_401k_roth   = inputs.get("annual_401k_roth_employee",  salary * emp_pct)
    annual_401k_pretax = inputs.get("annual_401k_pretax_employer", salary * er_pct)
    annual_bonus = salary * inputs.get("annual_bonus_pct", 0)

    justin_salary  = inputs.get("justin_w2_salary", 0)
    justin_emp_pct = inputs.get("justin_employee_401k_pct", 0.06)
    justin_er_pct  = inputs.get("justin_employer_401k_pct", 0.03)
    justin_annual_401k_roth   = justin_salary * justin_emp_pct
    justin_annual_401k_pretax = justin_salary * justin_er_pct
    justin_annual_bonus = justin_salary * inputs.get("justin_annual_bonus_pct", 0)
    justin_annual_rsu   = inputs.get("justin_annual_rsu_value", 0)

    pretax_start = pretax_401k + sum(
        a["balance"] for a in accounts if a["account_type"] == "ira" and a["owner"] not in ("abby", "cooper"))
    roth_start = roth_401k + sum(
        a["balance"] for a in accounts if a["account_type"] == "roth_ira" and a["owner"] not in ("abby", "cooper"))
    taxable_start = sum(
        a["balance"] for a in accounts if a["account_type"] == "taxable" and a["owner"] not in ("abby", "cooper"))
    hsa_start = sum(a["balance"] for a in accounts if a["account_type"] == "hsa")

    timeline = build_two_person_timeline(jason_age, justin_age, jason_ret_age, justin_ret_age,
                                          inputs.get("retirement_end_age"))
    phase2_years = timeline.phase2_start_years
    phase2_start_age = jason_age + phase2_years
    phase3_start_age = jason_age + timeline.phase3_start_years
    phase2_duration_years = timeline.phase3_start_years - phase2_years

    # ── Accumulation to phase2_start (whichever spouse retires FIRST) ──
    # Each spouse's own contributions run for their own years-to-retire,
    # capped at phase2_years -- whoever retires LATER only gets
    # contribution credit through phase2_start here; the rest of their
    # working years (phase2 itself) are modeled as income below, not
    # further contributions (section 7.3).
    jason_contrib_years  = min(timeline.jason_years_to_retire, phase2_years)
    jason_dormant_years  = max(0, phase2_years - jason_contrib_years)
    justin_contrib_years = min(timeline.justin_years_to_retire, phase2_years)
    justin_dormant_years = max(0, phase2_years - justin_contrib_years)

    def _contrib_fv(annual_amount, contrib_years, dormant_years):
        if annual_amount <= 0:
            return 0.0
        fv_at_stop = (
            _fv_growing_annuity(annual_amount, pre_ret, salary_growth_pct, contrib_years)
            if salary_growth_pct else _fv_annuity(annual_amount, pre_ret, contrib_years)
        )
        return _fv(fv_at_stop, pre_ret, dormant_years)

    def _contrib_fv_flat(annual_amount, contrib_years, dormant_years):
        """Same dormant-years compounding pattern as _contrib_fv, but
        NEVER escalates with salary_growth_pct -- Jason's own RSU grant
        is a flat dollar amount in run_retirement_projection (line
        ~731: `_fv_annuity(annual_rsu * ..., pre_ret, years_to_retire)`,
        unconditionally, unlike 401k contributions/bonus which ARE
        salary-derived percentages and DO grow). Independent review,
        2026-09-08, second follow-up: this function's shared _contrib_fv
        helper incorrectly applied salary growth to Jason's RSU too,
        reproduced ($195,000 correct vs. $215,150 with growth wrongly
        applied over 3 years at 10% salary growth). Justin's own RSU
        deliberately stays on the growing-eligible _contrib_fv above --
        run_retirement_projection's own justin_annual_rsu handling
        (via _justin_contrib_fv) already lets it grow with
        salary_growth_pct, an existing asymmetry between the two
        spouses' RSU treatment that predates this branch and is
        preserved here exactly, not resolved."""
        if annual_amount <= 0:
            return 0.0
        fv_at_stop = _fv_annuity(annual_amount, pre_ret, contrib_years)
        return _fv(fv_at_stop, pre_ret, dormant_years)

    pension_annual = pension_for_age(inputs, jason_ret_age)

    retirement_year_for_events = timeline.retirement_year
    pre_life_events, post_life_events = _split_life_events(life_events, retirement_year_for_events)
    post_life_events = post_life_events + _post_retirement_asset_sale_events(inputs, jason_age, phase2_start_age)
    life_events_taxable_add = _pre_retirement_taxable_add(pre_life_events, pre_ret, retirement_year_for_events)
    surplus_at_ret = _surplus_allocations_at_retirement(surplus_allocations, pre_ret, phase2_years)

    pretax_contrib_fv = (_contrib_fv(annual_401k_pretax, jason_contrib_years, jason_dormant_years)
                          + _contrib_fv(justin_annual_401k_pretax, justin_contrib_years, justin_dormant_years))
    roth_contrib_fv = (_contrib_fv(annual_401k_roth, jason_contrib_years, jason_dormant_years)
                        + _contrib_fv(justin_annual_401k_roth, justin_contrib_years, justin_dormant_years))
    pretax_at_start = _fv(pretax_start, pre_ret, phase2_years) + pretax_contrib_fv
    roth_at_start   = _fv(roth_start, pre_ret, phase2_years) + roth_contrib_fv
    taxable_at_start = _fv(taxable_start, pre_ret, phase2_years)
    hsa_at_start = _fv(hsa_start, pre_ret, phase2_years) + _fv_annuity(annual_hsa, pre_ret, phase2_years)

    taxable_at_start += _contrib_fv_flat(annual_rsu * SECOND_EARNER_NET_OF_TAX_FACTOR, jason_contrib_years, jason_dormant_years)
    taxable_at_start += _contrib_fv(justin_annual_rsu * SECOND_EARNER_NET_OF_TAX_FACTOR, justin_contrib_years, justin_dormant_years)
    taxable_at_start += _contrib_fv(annual_bonus * SECOND_EARNER_NET_OF_TAX_FACTOR, jason_contrib_years, jason_dormant_years)
    taxable_at_start += _contrib_fv(justin_annual_bonus * SECOND_EARNER_NET_OF_TAX_FACTOR, justin_contrib_years, justin_dormant_years)

    asset1_sale_age = inputs.get("asset1_sale_age", 0)
    asset1_sale_net = inputs.get("asset1_sale_net", 0)
    asset1_app      = inputs.get("asset1_appreciation", 0.03)
    if asset1_sale_age and asset1_sale_age <= phase2_start_age:
        yrs_asset1 = max(0, asset1_sale_age - jason_age)
        yrs_to_grow = phase2_years - yrs_asset1
        asset1_proceeds = asset1_sale_net * ((1 + asset1_app) ** yrs_asset1)
        taxable_at_start += asset1_proceeds * ((1 + pre_ret) ** yrs_to_grow)
    asset2_sale_age = inputs.get("asset2_sale_age", 0)
    asset2_sale_net = inputs.get("asset2_sale_net", 0)
    if asset2_sale_age and asset2_sale_age <= phase2_start_age:
        yrs_asset2 = max(0, asset2_sale_age - jason_age)
        yrs_to_grow = phase2_years - yrs_asset2
        taxable_at_start += asset2_sale_net * ((1 + pre_ret) ** yrs_to_grow)

    taxable_at_start += life_events_taxable_add
    if surplus_at_ret["retirement_contrib"]:
        pretax_at_start += surplus_at_ret["retirement_contrib"] * pretax_pct
        roth_at_start   += surplus_at_ret["retirement_contrib"] * roth_pct
    if surplus_at_ret["taxable_investing"]:
        taxable_at_start += surplus_at_ret["taxable_investing"]

    portfolio_at_start = pretax_at_start + roth_at_start + taxable_at_start + hsa_at_start

    mort_age = timeline.end_age
    retire_years = timeline.retire_yrs
    cum_inflation = build_cumulative_inflation(inflation, retire_years)
    jason_effective_start_age = timeline.jason_effective_start_age

    # Per-year spending need (income/healthcare/bridge/kids), pension
    # gating, and the still-working spouse's income baseline are all
    # shared with simulation_engine.py's two-age Monte Carlo/Stress
    # Tests trial loop via these three module-level helpers rather than
    # a second independent copy of the same formulas (CALCULATION_
    # CONTRACT.md section 22).
    need_for_year = two_age_spending_need_fn(inputs, income_today, inflation, timeline)
    phase2_duration_years, still_working_income_at_start = two_age_still_working_income_inputs(
        inputs, timeline, salary_growth_pct)

    pretax, roth, taxable, hsa = pretax_at_start, roth_at_start, taxable_at_start, hsa_at_start
    jason_rmd_start_age = rmd_start_age(jason_age)
    from retirement_tools_engine import marginal_rate as _marginal_rate, STD_DEDUCTION_MFJ_2026 as _STD_DED

    yearly = []
    for yr in range(retire_years):
        age = timeline.age(yr)
        calendar_year = timeline.calendar_year(yr)
        justin_age_this_year = timeline.justin_age_at(age)
        phase = "phase2" if timeline.in_phase2(age) else "phase3"

        year_need, healthcare_inflated, bridge_income_this_year = need_for_year(age, yr)

        life_event_cash_this_year, life_event_monthly_this_year = _post_retirement_year_effects(post_life_events, calendar_year)
        year_need -= life_event_monthly_this_year

        still_working_income_this_year = justin_gap_income_for_year(
            yr, phase2_duration_years, still_working_income_at_start, salary_growth_pct)
        year_need -= still_working_income_this_year

        # Pension is frozen (no COLA), Social Security COLA'd from each
        # spouse's own claim age -- same formulas run_retirement_projection
        # uses via the shared annual_inputs builder, computed directly
        # here instead since that builder is coupled to Timeline's
        # single-axis fields (effective_start_age/claim_year_index), not
        # TwoPersonTimeline's.
        year_pen = two_age_pension_for_year(pension_annual, age, jason_effective_start_age)
        year_jss = jason_ss_annual * ((1 + inflation) ** max(0, age - jason_ss_age)) if age >= jason_ss_age else 0.0
        year_uss = (justin_ss_annual * ((1 + inflation) ** max(0, justin_age_this_year - justin_ss_age))
                    if justin_age_this_year >= justin_ss_age else 0.0)
        fixed_income = year_pen + year_jss + year_uss

        rmd = _rmd(pretax, age, jason_rmd_start_age)
        taxable_income_est = max(0, year_pen + (year_jss + year_uss) * 0.85 + rmd - _STD_DED)
        pretax_tax_rate = min(0.90, _marginal_rate(taxable_income_est) + max(0, float(inputs.get("state_income_tax_rate") or 0)))

        year_result = simulate_withdrawal_year(
            opening=AccountState(pretax=pretax, roth=roth, taxable=taxable, hsa=hsa),
            spending_need=year_need,
            guaranteed_income=fixed_income,
            life_event_cash=life_event_cash_this_year,
            rmd_amount=rmd,
            tax_model=marginal_bracket_tax_model(pretax_rate=pretax_tax_rate, taxable_rate=0.0),
            growth_rate=post_ret,
            order=DEFAULT_ORDER,
        )

        withdrawal_taxable = year_result.draws.get("taxable", 0.0)
        withdrawal_pretax  = year_result.draws.get("pretax", 0.0)
        withdrawal_hsa     = year_result.draws.get("hsa", 0.0)
        withdrawal_roth    = year_result.draws.get("roth", 0.0)
        total_withdrawal = withdrawal_taxable + withdrawal_pretax + withdrawal_roth + withdrawal_hsa
        unmet_need = year_result.unmet_need

        pretax, roth, taxable, hsa = year_result.closing.pretax, year_result.closing.roth, year_result.closing.taxable, year_result.closing.hsa
        total_portfolio = pretax + roth + taxable + hsa

        yearly.append({
            "jason_age":  age,
            "justin_age": justin_age_this_year,
            "year":       calendar_year,
            "phase":      phase,
            "income_need": round(year_need + still_working_income_this_year),  # need BEFORE the still-working offset, matching run_retirement_projection's own field meaning
            "still_working_spouse_income": round(still_working_income_this_year),
            "bridge_income": round(bridge_income_this_year),
            "healthcare_cost": round(healthcare_inflated),
            "pension":         round(year_pen),
            "social_security": round(year_jss + year_uss),
            "rmd":             round(rmd),
            "estimated_tax":   round(year_result.taxes_paid.get("rmd", 0.0) + year_result.taxes_paid.get("pretax", 0.0)),
            "withdrawal":      round(total_withdrawal),
            "draw":            round(max(0.0, year_need - fixed_income)),
            "unmet_need":      round(unmet_need),
            "portfolio_balance": round(total_portfolio),
        })

    underfunded = any(y["unmet_need"] > 0 for y in yearly)

    return {
        "has_data": True,
        "jason_ret_age":  jason_ret_age,
        "justin_ret_age": justin_ret_age,
        "phase2_start_age": phase2_start_age,
        "phase3_start_age": phase3_start_age,
        "later_retiree":    timeline.later_retiree,
        "portfolio_at_phase2_start": round(portfolio_at_start),
        # Bucket-level breakdown of the same starting portfolio -- added
        # so simulation_engine.py's two-age Monte Carlo/Stress Tests can
        # read these starting balances the same way run_monte_carlo/
        # run_stress_tests already do from run_retirement_projection's
        # own scenario dict (pretax_at_retirement/roth_at_retirement/
        # etc.), rather than re-deriving the accumulation-phase
        # contribution/RSU/bonus/asset-sale/life-event/surplus-
        # allocation math a second time (CALCULATION_CONTRACT.md
        # section 22).
        #
        # Deliberately UNROUNDED (independent review, 2026-09-08, P2):
        # these four are consumed directly as _run_single_two_age's
        # opening balances, not displayed anywhere -- rounding them here
        # meant the deterministic Monte Carlo/Stress trial started from a
        # slightly different number than run_two_dimensional_retirement_
        # projection's own full-precision arithmetic used for the SAME
        # scenario, reproduced as a $2 drift ($424,713 vs $424,711) under
        # otherwise identical fixed returns -- small, but real and
        # unnecessary. round() belongs at display time (the frontend's
        # own Intl.NumberFormat), not baked into a value another engine
        # consumes as an input.
        "pretax_at_phase2_start":  pretax_at_start,
        "roth_at_phase2_start":    roth_at_start,
        "taxable_at_phase2_start": taxable_at_start,
        "hsa_at_phase2_start":     hsa_at_start,
        "retirement_end_age": mort_age,
        "any_year_underfunded": underfunded,
        "on_track": not underfunded,
        "yearly_detail": yearly,
        "second_earner_net_of_tax_factor": SECOND_EARNER_NET_OF_TAX_FACTOR,
        "account_ownership_limitation": ACCOUNT_OWNERSHIP_LIMITATION_NOTE,
    }


# Milestone 4 (CALCULATION_CONTRACT.md sections 36-37): the four owner
# buckets every account-ownership-aware calculation in this file uses.
# NOT invented -- accounts.owner is already a required, populated
# column (jason/justin/joint/abby/cooper/trust, confirmed by reading
# db.py's schema and Accounts.jsx's own fixed domain); kids' accounts
# (abby/cooper) are excluded upstream by every caller, exactly as the
# existing pooled functions already do, before any of these four
# buckets come into play.
OWNER_BUCKETS = ("jason", "justin", "joint", "trust")


def _owner_bucket_for_account(account: Dict) -> str:
    """Maps an account's raw `owner` field to one of OWNER_BUCKETS.
    Defensive fallback to "joint" for any value outside the known
    domain (should not occur given Accounts.jsx's fixed dropdown, but
    this file has no way to enforce that at the data layer) -- "joint"
    is the conservative choice for an unrecognized owner, since it's
    the one bucket every existing pooled consumer already includes in
    the household total without a per-spouse claim on it."""
    owner = account.get("owner")
    return owner if owner in OWNER_BUCKETS else "joint"


def owner_split_starting_balances_two_age(inputs: Dict, accounts: List[Dict], jason_ret_age: int, justin_ret_age: int,
                                            salary_growth_pct: float = None,
                                            life_events: List[Dict] = None,
                                            surplus_allocations: List[Dict] = None) -> Dict:
    """Owner-attributed starting balances at phase2_start -- the SAME
    accumulation-phase dollar formulas run_two_dimensional_retirement_
    projection already uses (CALCULATION_CONTRACT.md section 37.4:
    "no invented ownership, read from the existing owner field"), just
    partitioned into OWNER_BUCKETS instead of summed into one pooled
    total. This is new, ADDITIVE scope for Survivor Scenario
    specifically -- SWR/Monte Carlo/Roth Conversion/Tax Efficiency/the
    existing pooled Projection all keep reading the unmodified pooled
    function exactly as before; nothing here changes their behavior.

    Attribution rules (section 37.4):
    - Each account's own `owner` field determines its bucket (via
      _owner_bucket_for_account) -- no guessing.
    - 401k accounts are split pretax/roth using the SAME household-
      level pretax_401k_pct assumption the pooled function uses (no
      per-spouse pretax/roth election exists in this app's inputs),
      applied per owner-group's own 401k balance.
    - Ongoing CONTRIBUTIONS (401k employee/employer, RSU, bonus) are
      inherently individual -- a paycheck can't fund a "joint" 401k --
      so Jason's own contribution formulas land in the jason bucket,
      Justin's own in the justin bucket, exactly matching which half
      of the pooled function's own `+` they already were.
    - Household-level pre-retirement cash inflows with no natural
      individual owner in this app's inputs (asset sales, life events,
      surplus allocations, the single household HSA contribution
      figure) are attributed to the JOINT bucket -- an explicit,
      documented v1 approximation (see `account_ownership_limitation`
      in the return value), not a claim that they're literally jointly
      titled.

    Core correctness property (verified by
    TestOwnerSplitReconcilesAgainstPooledTotals): summing all four
    buckets for any account type reproduces run_two_dimensional_
    retirement_projection's own pooled `*_at_phase2_start` figure
    EXACTLY, for identical inputs -- the split re-partitions the same
    dollar amounts, it does not recompute them differently."""
    salary_growth_pct = inputs.get("_salary_growth_pct", 0.0) if salary_growth_pct is None else salary_growth_pct
    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    pre_ret    = inputs["expected_return_pre_retirement"]
    annual_hsa = inputs.get("annual_hsa_contribution", 0)
    annual_rsu = inputs.get("annual_rsu_value", 0)

    pretax_401k_pct = inputs.get("pretax_401k_pct", 0.75)
    roth_401k_pct   = 1.0 - pretax_401k_pct

    salary      = inputs.get("w2_salary", 0)
    emp_pct     = inputs.get("employee_401k_pct", 0.06)
    er_pct      = inputs.get("employer_401k_pct", 0.09)
    annual_401k_roth   = inputs.get("annual_401k_roth_employee",  salary * emp_pct)
    annual_401k_pretax = inputs.get("annual_401k_pretax_employer", salary * er_pct)
    annual_bonus = salary * inputs.get("annual_bonus_pct", 0)

    justin_salary  = inputs.get("justin_w2_salary", 0)
    justin_emp_pct = inputs.get("justin_employee_401k_pct", 0.06)
    justin_er_pct  = inputs.get("justin_employer_401k_pct", 0.03)
    justin_annual_401k_roth   = justin_salary * justin_emp_pct
    justin_annual_401k_pretax = justin_salary * justin_er_pct
    justin_annual_bonus = justin_salary * inputs.get("justin_annual_bonus_pct", 0)
    justin_annual_rsu   = inputs.get("justin_annual_rsu_value", 0)

    timeline = build_two_person_timeline(jason_age, justin_age, jason_ret_age, justin_ret_age,
                                          inputs.get("retirement_end_age"))
    phase2_years = timeline.phase2_start_years
    phase2_start_age = jason_age + phase2_years

    jason_contrib_years  = min(timeline.jason_years_to_retire, phase2_years)
    jason_dormant_years  = max(0, phase2_years - jason_contrib_years)
    justin_contrib_years = min(timeline.justin_years_to_retire, phase2_years)
    justin_dormant_years = max(0, phase2_years - justin_contrib_years)

    def _contrib_fv(annual_amount, contrib_years, dormant_years):
        if annual_amount <= 0:
            return 0.0
        fv_at_stop = (
            _fv_growing_annuity(annual_amount, pre_ret, salary_growth_pct, contrib_years)
            if salary_growth_pct else _fv_annuity(annual_amount, pre_ret, contrib_years)
        )
        return _fv(fv_at_stop, pre_ret, dormant_years)

    def _contrib_fv_flat(annual_amount, contrib_years, dormant_years):
        # Matches run_two_dimensional_retirement_projection's own
        # _contrib_fv_flat exactly -- Jason's RSU grant never escalates
        # with salary_growth_pct, Justin's does (an existing asymmetry
        # predating this branch, preserved here unchanged).
        if annual_amount <= 0:
            return 0.0
        fv_at_stop = _fv_annuity(annual_amount, pre_ret, contrib_years)
        return _fv(fv_at_stop, pre_ret, dormant_years)

    retirement_year_for_events = timeline.retirement_year
    pre_life_events, _post_life_events = _split_life_events(life_events, retirement_year_for_events)
    life_events_taxable_add = _pre_retirement_taxable_add(pre_life_events, pre_ret, retirement_year_for_events)
    surplus_at_ret = _surplus_allocations_at_retirement(surplus_allocations, pre_ret, phase2_years)

    asset1_sale_age = inputs.get("asset1_sale_age", 0)
    asset1_sale_net = inputs.get("asset1_sale_net", 0)
    asset1_app      = inputs.get("asset1_appreciation", 0.03)
    asset1_proceeds_grown = 0.0
    if asset1_sale_age and asset1_sale_age <= phase2_start_age:
        yrs_asset1 = max(0, asset1_sale_age - jason_age)
        yrs_to_grow = phase2_years - yrs_asset1
        asset1_proceeds = asset1_sale_net * ((1 + asset1_app) ** yrs_asset1)
        asset1_proceeds_grown = asset1_proceeds * ((1 + pre_ret) ** yrs_to_grow)
    asset2_sale_age = inputs.get("asset2_sale_age", 0)
    asset2_sale_net = inputs.get("asset2_sale_net", 0)
    asset2_proceeds_grown = 0.0
    if asset2_sale_age and asset2_sale_age <= phase2_start_age:
        yrs_asset2 = max(0, asset2_sale_age - jason_age)
        yrs_to_grow = phase2_years - yrs_asset2
        asset2_proceeds_grown = asset2_sale_net * ((1 + pre_ret) ** yrs_to_grow)

    buckets = {owner: {"pretax": 0.0, "roth": 0.0, "taxable": 0.0, "hsa": 0.0} for owner in OWNER_BUCKETS}

    for owner in OWNER_BUCKETS:
        owned = [a for a in accounts if _owner_bucket_for_account(a) == owner and a.get("owner") not in ("abby", "cooper")]
        total_401k_owner = sum(a["balance"] for a in owned if a["account_type"] == "401k")
        pretax_401k_owner = total_401k_owner * pretax_401k_pct
        roth_401k_owner   = total_401k_owner * roth_401k_pct
        ira_owner       = sum(a["balance"] for a in owned if a["account_type"] == "ira")
        roth_ira_owner  = sum(a["balance"] for a in owned if a["account_type"] == "roth_ira")
        taxable_owner   = sum(a["balance"] for a in owned if a["account_type"] == "taxable")
        # hsa_start's own pooled formula never filters by owner at all
        # (not even abby/cooper) -- matched here via the SAME [a for a
        # in accounts ...] scan per owner bucket, not the `owned`
        # abby/cooper-excluded list above, to reconcile exactly.
        hsa_owner = sum(a["balance"] for a in accounts if a["account_type"] == "hsa"
                         and _owner_bucket_for_account(a) == owner)

        pretax_start_owner  = pretax_401k_owner + ira_owner
        roth_start_owner    = roth_401k_owner + roth_ira_owner

        pretax_contrib_fv = 0.0
        roth_contrib_fv   = 0.0
        taxable_extra     = 0.0
        if owner == "jason":
            pretax_contrib_fv = _contrib_fv(annual_401k_pretax, jason_contrib_years, jason_dormant_years)
            roth_contrib_fv   = _contrib_fv(annual_401k_roth, jason_contrib_years, jason_dormant_years)
            taxable_extra += _contrib_fv_flat(annual_rsu * SECOND_EARNER_NET_OF_TAX_FACTOR, jason_contrib_years, jason_dormant_years)
            taxable_extra += _contrib_fv(annual_bonus * SECOND_EARNER_NET_OF_TAX_FACTOR, jason_contrib_years, jason_dormant_years)
        elif owner == "justin":
            pretax_contrib_fv = _contrib_fv(justin_annual_401k_pretax, justin_contrib_years, justin_dormant_years)
            roth_contrib_fv   = _contrib_fv(justin_annual_401k_roth, justin_contrib_years, justin_dormant_years)
            taxable_extra += _contrib_fv(justin_annual_rsu * SECOND_EARNER_NET_OF_TAX_FACTOR, justin_contrib_years, justin_dormant_years)
            taxable_extra += _contrib_fv(justin_annual_bonus * SECOND_EARNER_NET_OF_TAX_FACTOR, justin_contrib_years, justin_dormant_years)

        pretax_final  = _fv(pretax_start_owner, pre_ret, phase2_years) + pretax_contrib_fv
        roth_final    = _fv(roth_start_owner, pre_ret, phase2_years) + roth_contrib_fv
        taxable_final = _fv(taxable_owner, pre_ret, phase2_years) + taxable_extra
        hsa_final     = _fv(hsa_owner, pre_ret, phase2_years)

        if owner == "jason":
            hsa_final += _fv_annuity(annual_hsa, pre_ret, phase2_years)
        if owner == "joint":
            # Household-level cash inflows with no individual owner in
            # this app's inputs -- attributed to joint, an explicit
            # documented approximation (see account_ownership_limitation
            # below), not a claim they're literally jointly titled.
            taxable_final += asset1_proceeds_grown + asset2_proceeds_grown + life_events_taxable_add
            if surplus_at_ret["retirement_contrib"]:
                pretax_final += surplus_at_ret["retirement_contrib"] * pretax_401k_pct
                roth_final   += surplus_at_ret["retirement_contrib"] * roth_401k_pct
            if surplus_at_ret["taxable_investing"]:
                taxable_final += surplus_at_ret["taxable_investing"]

        buckets[owner]["pretax"]  = pretax_final
        buckets[owner]["roth"]    = roth_final
        buckets[owner]["taxable"] = taxable_final
        buckets[owner]["hsa"]     = hsa_final

    buckets["phase2_start_age"] = phase2_start_age
    buckets["account_ownership_limitation"] = (
        "Owner attribution reads each account's own `owner` field directly -- nothing is guessed. "
        "Household-level pre-retirement cash inflows with no individual owner in this app's inputs "
        "(asset sales, life events, surplus allocations, the single household HSA contribution figure) "
        "are attributed to the joint bucket as an explicit approximation, not a claim they're jointly titled."
    )
    return buckets


# Withdrawal owner-order policy (CALCULATION_CONTRACT.md section 37.4):
# joint funds a shortfall before either spouse's own individually-titled
# accounts are touched -- a stated policy choice, not an emergent
# property of anything else in this file. Trust is LAST, not excluded:
# this order governs the PRE-death walk, which must reconcile exactly
# against the existing pooled run_two_dimensional_retirement_projection
# -- that function has always spent trust-owned account balances as
# part of its single pooled total (it only excludes abby/cooper),
# so excluding trust here would silently protect trust money the
# pooled reference function itself already draws down, breaking
# reconciliation. (Section 37.1's "trust never auto-included" rule is
# about the DEATH-TRANSITION question -- does trust merge into what
# the SURVIVOR can spend post-death -- a separate decision applied
# later, at the transition itself, not here.)
WITHDRAWAL_OWNER_ORDER = ("joint", "jason", "justin", "trust")


def _allocate_type_delta_across_owners(owner_balances_this_type: Dict[str, float], delta: float,
                                        surplus_source_shares: Dict[str, float] = None) -> Dict[str, float]:
    """Splits a pooled per-type balance CHANGE (`delta`, already net of
    growth -- see run_owner_split_two_dimensional_projection's own
    reconciliation argument) across owner sub-balances of that same
    type, using WITHDRAWAL_OWNER_ORDER for a reduction (delta < 0, a
    real withdrawal happened) and, for a positive delta (a surplus/
    RMD-reinvestment inflow -- only ever possible for the taxable type,
    per simulate_withdrawal_year's own contract), crediting it by
    `surplus_source_shares` (section 37.4: "transfers land in the SAME
    owner-bucket the income source belongs to" -- e.g. Jason's own
    pension surplus sweeps into Jason's own taxable bucket, not joint).
    `surplus_source_shares` is a {"jason":.., "justin":.., "joint":..}
    fraction-of-delta breakdown the caller derives from that year's own
    income sources (pension+Jason's SS -> jason, Justin's SS/gap income
    -> justin, unattributed life-event cash -> joint); omitted or falsy
    (e.g. a pure RMD-reinvestment year with no cash-income surplus to
    attribute) falls back to the original all-joint default, since
    household-level inflows with no single natural owner still default
    to joint, the same convention owner_split_starting_balances_two_age
    already established for pre-retirement inflows.
    Returns {"jason": delta_j, "justin": delta_u, "joint": delta_o,
    "trust": delta_t} -- see WITHDRAWAL_OWNER_ORDER's own comment for
    why trust IS included in a pre-death reduction, unlike the separate
    post-death survivor-availability question. Fixed: independent
    review, 2026-09-08, finding 4 (P1) -- every positive delta used to
    go entirely to joint regardless of source, so e.g. Jason's own
    pension surplus became joint money, violating section 37.4's own
    stated rule (reproduced: disabling joint survivorship then left the
    survivor $15,000 instead of $30,000 of what was actually Jason's own
    pension surplus)."""
    result = {"jason": 0.0, "justin": 0.0, "joint": 0.0, "trust": 0.0}
    if delta > 0:
        if surplus_source_shares:
            for owner, share in surplus_source_shares.items():
                result[owner] = delta * share
        else:
            result["joint"] = delta
        return result
    remaining_reduction = -delta
    for owner in WITHDRAWAL_OWNER_ORDER:
        if remaining_reduction <= 0:
            break
        available = max(0.0, owner_balances_this_type.get(owner, 0.0))
        take = min(available, remaining_reduction)
        result[owner] = -take
        remaining_reduction -= take
    # Any leftover (shouldn't happen if the pooled total already covered
    # the draw, but floating-point edge cases get the residual dumped on
    # "joint" rather than silently discarded)
    if remaining_reduction > 1e-6:
        result["joint"] -= remaining_reduction
    return result


def run_owner_split_two_dimensional_projection(inputs: Dict, accounts: List[Dict], jason_ret_age: int, justin_ret_age: int,
                                                 ss_timing: str = "early", salary_growth_pct: float = None,
                                                 life_events: List[Dict] = None,
                                                 surplus_allocations: List[Dict] = None,
                                                 death_jason_age: int = None, deceased: str = None,
                                                 jason_ss_claim_age: int = None, justin_ss_claim_age: int = None) -> Dict:
    """Owner-attributed year-by-year walk (CALCULATION_CONTRACT.md
    section 37.4) -- carries jason/justin/joint/trust buckets through
    EVERY year from phase2_start onward, not just a one-time split at
    initialization (section 36's own insufficient version). New,
    ADDITIVE scope for Survivor Scenario specifically -- the pooled
    run_two_dimensional_retirement_projection is completely unmodified
    and remains what SWR/Monte Carlo/Roth Conversion/Tax Efficiency/the
    existing pooled Projection all read; nothing here changes their
    behavior.

    Reconciliation is BY CONSTRUCTION, not by coincidence: every year,
    this function runs the EXACT SAME simulate_withdrawal_year call the
    pooled function's own per-year loop makes (same need/guaranteed/
    RMD/tax-rate/order -- RMD stays aggregate, Jason-anchored, matching
    every other two-age consumer; per-spouse RMDs during normal
    both-alive operation are explicitly out of scope per section 37.4)
    against the POOLED total (summed across this function's own owner
    buckets each year) -- then allocates that single pooled result's
    per-type balance CHANGE across the owner buckets via
    WITHDRAWAL_OWNER_ORDER (a real withdrawal) or, for a surplus/RMD-
    reinvestment inflow, by that year's own income-source proportions
    (finding 4 fix, see _allocate_type_delta_across_owners's own
    docstring) -- never recomputing the underlying tax/RMD/draw math a
    second, independent way. Growth is applied per owner
    AFTER allocation, at the identical rate the pooled call already
    used -- linear, so summing the four owners' post-growth balances
    reproduces the pooled post-growth balance exactly.

    Returns the same shape as run_two_dimensional_retirement_projection
    plus an owner-split `yearly_detail` (each year holding a
    `{owner: {"pretax":..,"roth":..,"taxable":..,"hsa":..}}` closing
    snapshot) and the final `ending_buckets`.

    death_jason_age/deceased (independent review, 2026-09-08, eighth
    follow-up, P1): optional -- when both are given, the FIRST year
    with jason-age >= death_jason_age incorporates the deceased's own
    individual RMD obligation into THAT YEAR's single calculation,
    before tax-rate determination, before funding spending, before
    ownership allocation, and before growth -- not as a downstream
    patch applied to an already-finished year (the approach every
    earlier round of this fix used, and the review kept finding new
    ways it went wrong: double-withdrawing, bypassing tax, bypassing
    growth, and now this -- funding spending from the wrong money and
    using a stale tax rate). Survivor Scenario is the only caller that
    passes these; every other two-age consumer leaves them at their
    None default and this function's behavior for them is completely
    unchanged."""
    salary_growth_pct = inputs.get("_salary_growth_pct", 0.0) if salary_growth_pct is None else salary_growth_pct
    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    inflation  = inputs["inflation_rate"]
    post_ret   = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]

    # SS resolution (2026-09-08, CALCULATION_CONTRACT.md section 44,
    # milestone 5; made EXPLICIT params as of milestone 6/ninth follow-up
    # finding 1 -- see run_two_dimensional_retirement_projection's
    # identical comment).
    jason_ss_annual, jason_ss_age, justin_ss_annual, justin_ss_age = resolve_ss_benefits(
        inputs, ss_timing, jason_ss_claim_age, justin_ss_claim_age)
    pension_annual = pension_for_age(inputs, jason_ret_age)

    timeline = build_two_person_timeline(jason_age, justin_age, jason_ret_age, justin_ret_age,
                                          inputs.get("retirement_end_age"))
    phase2_start_age = jason_age + timeline.phase2_start_years
    phase3_start_age = jason_age + timeline.phase3_start_years
    jason_effective_start_age = timeline.jason_effective_start_age
    retire_years = timeline.retire_yrs
    jason_rmd_start_age = rmd_start_age(jason_age)

    retirement_year_for_events = timeline.retirement_year
    _, post_life_events = _split_life_events(life_events, retirement_year_for_events)
    post_life_events = post_life_events + _post_retirement_asset_sale_events(inputs, jason_age, phase2_start_age)

    need_for_year = two_age_spending_need_fn(inputs, income_today, inflation, timeline)
    phase2_duration_years, still_working_income_at_start = two_age_still_working_income_inputs(
        inputs, timeline, salary_growth_pct)

    starting = owner_split_starting_balances_two_age(inputs, accounts, jason_ret_age, justin_ret_age,
                                                       salary_growth_pct, life_events, surplus_allocations)
    buckets = {owner: dict(starting[owner]) for owner in OWNER_BUCKETS}

    from retirement_tools_engine import marginal_rate as _marginal_rate, STD_DEDUCTION_MFJ_2026 as _STD_DED

    death_year_applied = False

    yearly = []
    for yr in range(retire_years):
        age = timeline.age(yr)
        calendar_year = timeline.calendar_year(yr)
        justin_age_this_year = timeline.justin_age_at(age)

        year_need, _healthcare_inflated, _bridge_income = need_for_year(age, yr)
        life_event_cash_this_year, life_event_monthly_this_year = _post_retirement_year_effects(post_life_events, calendar_year)
        year_need -= life_event_monthly_this_year
        still_working_income_this_year = justin_gap_income_for_year(
            yr, phase2_duration_years, still_working_income_at_start, salary_growth_pct)
        year_need -= still_working_income_this_year

        year_pen = two_age_pension_for_year(pension_annual, age, jason_effective_start_age)
        year_jss = jason_ss_annual * ((1 + inflation) ** max(0, age - jason_ss_age)) if age >= jason_ss_age else 0.0
        year_uss = (justin_ss_annual * ((1 + inflation) ** max(0, justin_age_this_year - justin_ss_age))
                    if justin_age_this_year >= justin_ss_age else 0.0)
        fixed_income = year_pen + year_jss + year_uss

        pooled_pretax = sum(buckets[o]["pretax"] for o in OWNER_BUCKETS)
        rmd = _rmd(pooled_pretax, age, jason_rmd_start_age)

        # Death-year integration (independent review, 2026-09-08, eighth
        # follow-up, P1) -- the deceased's own individual RMD obligation
        # (their own age/balance, not the aggregate Jason-anchored
        # figure above) is folded into THIS year's `rmd` BEFORE the tax
        # rate is estimated below, so a bigger forced distribution
        # correctly pushes the marginal rate up in the SAME calculation
        # that uses it, not a stale rate computed before the addition.
        deceased_forced_from_own_account = 0.0
        if deceased is not None and death_jason_age is not None and not death_year_applied and age >= death_jason_age:
            death_year_applied = True
            deceased_age_at_death = age if deceased == "jason" else justin_age_this_year
            deceased_own_rmd_start_age = rmd_start_age(inputs["jason_age"] if deceased == "jason" else inputs["justin_age"])
            deceased_pretax_at_start = buckets[deceased]["pretax"]
            deceased_individual_rmd = _rmd(deceased_pretax_at_start, deceased_age_at_death, deceased_own_rmd_start_age)
            deceased_forced_from_own_account = min(deceased_individual_rmd, deceased_pretax_at_start)
            rmd = max(rmd, deceased_individual_rmd)

        taxable_income_est = max(0, year_pen + (year_jss + year_uss) * 0.85 + rmd - _STD_DED)
        pretax_tax_rate = min(0.90, _marginal_rate(taxable_income_est) + max(0, float(inputs.get("state_income_tax_rate") or 0)))

        pooled_opening = {t: sum(buckets[o][t] for o in OWNER_BUCKETS) for t in ("pretax", "roth", "taxable", "hsa")}
        year_result = simulate_withdrawal_year(
            opening=AccountState(**pooled_opening),
            spending_need=year_need,
            guaranteed_income=fixed_income,
            life_event_cash=life_event_cash_this_year,
            rmd_amount=rmd,
            tax_model=marginal_bracket_tax_model(pretax_rate=pretax_tax_rate, taxable_rate=0.0),
            growth_rate=post_ret,
            order=DEFAULT_ORDER,
        )
        pooled_closing_pregrowth = {
            t: getattr(year_result.closing, t) / (1 + post_ret) if (1 + post_ret) != 0 else getattr(year_result.closing, t)
            for t in ("pretax", "roth", "taxable", "hsa")
        }

        # Pretax allocated FIRST (independent review, 2026-09-08, fifth
        # follow-up, finding 2 fix, part 1) -- its own per-owner
        # reduction/increase this year is needed BEFORE attributing any
        # same-year taxable-type surplus (RMD proceeds reinvested) to
        # the SAME owner(s) that RMD/draw actually came from, instead of
        # defaulting reinvested RMD proceeds to joint (reproduced: Jason
        # 75/Justin 61, Jason's own $1M IRA, joint survivorship
        # disabled -- survivor resources understated by the RMD-
        # reinvestment surplus that used to be credited to joint instead
        # of Jason).
        pretax_delta = pooled_closing_pregrowth["pretax"] - pooled_opening["pretax"]
        pretax_owner_balances = {o: buckets[o]["pretax"] for o in OWNER_BUCKETS}
        if deceased_forced_from_own_account > 0:
            # The deceased's own account contributes AT LEAST its own
            # individual RMD first; whatever's left of pretax_delta is
            # allocated normally (WITHDRAWAL_OWNER_ORDER) across the
            # REMAINING balances, which may still include the deceased's
            # own account for any further draw beyond their own minimum.
            remaining_balances = dict(pretax_owner_balances)
            remaining_balances[deceased] -= deceased_forced_from_own_account
            remaining_delta = pretax_delta + deceased_forced_from_own_account
            pretax_allocation = _allocate_type_delta_across_owners(remaining_balances, remaining_delta)
            pretax_allocation[deceased] = pretax_allocation.get(deceased, 0.0) - deceased_forced_from_own_account
        else:
            pretax_allocation = _allocate_type_delta_across_owners(pretax_owner_balances, pretax_delta)
        for o in OWNER_BUCKETS:
            pregrowth = buckets[o]["pretax"] + pretax_allocation.get(o, 0.0)
            buckets[o]["pretax"] = max(0.0, pregrowth) * (1 + post_ret)

        # Owner cash-flow waterfall (independent review, 2026-09-08,
        # seventh follow-up, finding 1 (P1)) -- reweighting a pooled
        # ending-balance change by GROSS income shares (the findings
        # 2-4/sixth-follow-up-finding-1 approach) mixes untaxed income
        # surplus with gross (pre-tax) pretax distributions, and clamps
        # negative life-event costs to $0, dropping them from the
        # calculation entirely. Reproduced (all three: spouses 75, $0
        # growth/inflation, trust availability disabled):
        # - $60,000 Jason pension / $30,000 spending / $1,000,000 trust
        #   IRA: the pension alone funds need with a real $30,000
        #   leftover that's entirely Jason's own; the trust's forced
        #   RMD is separate money that's entirely the trust's own. The
        #   old approach (weighting Jason's real $30,000 NET surplus
        #   against the trust's $40,650 GROSS, pre-tax RMD) reported
        #   $27,929 survivor resources instead of the correct $30,000
        #   (trust excluded).
        # - Same, plus a $40,000 one-time (or equivalent recurring)
        #   expense: clamped to $0 and dropped entirely, inventing a
        #   phantom $30,000 pension surplus that doesn't exist once the
        #   real expense is netted in -- reported $10,944 instead of
        #   the correct $0 (the expense consumes the pension entirely,
        #   leaving only the trust's own excluded RMD proceeds).
        # - No pension, $9,000 spending, $10,000 joint IRA + $990,000
        #   trust IRA: the joint IRA's own $9,000 after-tax RMD
        #   proceeds already exactly fund spending -- $0 should be left
        #   over for ANY owner. The old proportional split still
        #   credited some of the trust's own proceeds as if joint's
        #   fully-consumed distribution hadn't used its own money up --
        #   reported $6,786 instead of the correct $0.
        #
        # Fixed: track each owner's own ACTUAL cash this year --
        # guaranteed income (+ gap income for whichever spouse is
        # later_retiree) for jason/justin, SIGNED life-event cash (one-
        # time and recurring, no longer clamped to zero) for joint, and
        # each owner's own AFTER-TAX pretax distribution (their own
        # share of pretax_allocation's reduction above, taxed at this
        # year's own pretax_tax_rate) for whichever owner(s) actually
        # supplied this year's RMD/draw. Then fund the TRUE underlying
        # need (year_need_baseline, adding back what life-event/gap
        # income already reduced year_need by) from those owner cash
        # amounts in WITHDRAWAL_OWNER_ORDER -- the SAME funding-order
        # convention every account draw already uses -- and credit only
        # what's left over, per owner, as the attribution weights for
        # the actual pooled surplus delta (preserving exact
        # reconciliation against the pooled total, which this does not
        # re-derive a second, independent way).
        owner_pretax_aftertax = {
            o: max(0.0, -pretax_allocation[o]) * (1 - pretax_tax_rate) for o in OWNER_BUCKETS
        }
        jason_gap_this_year  = still_working_income_this_year if timeline.later_retiree == "jason" else 0.0
        justin_gap_this_year = still_working_income_this_year if timeline.later_retiree == "justin" else 0.0
        owner_cash = {
            "jason":  year_pen + year_jss + jason_gap_this_year + owner_pretax_aftertax["jason"],
            "justin": year_uss + justin_gap_this_year + owner_pretax_aftertax["justin"],
            "joint":  life_event_cash_this_year + life_event_monthly_this_year + owner_pretax_aftertax["joint"],
            "trust":  owner_pretax_aftertax["trust"],
        }
        year_need_baseline = year_need + life_event_monthly_this_year + still_working_income_this_year
        remaining_need = year_need_baseline
        owner_leftover = {}
        for o in WITHDRAWAL_OWNER_ORDER:
            cash = owner_cash[o]
            if cash >= 0:
                used = min(cash, max(0.0, remaining_need))
                owner_leftover[o] = cash - used
                remaining_need -= used
            else:
                # A net negative contribution (e.g. a big expense funded
                # by joint) increases what still needs funding from
                # whoever's next in the order -- it contributes nothing
                # of its own to leave over.
                remaining_need += -cash
                owner_leftover[o] = 0.0
        total_leftover = sum(owner_leftover.values())
        surplus_source_shares = (
            {o: owner_leftover[o] / total_leftover for o in OWNER_BUCKETS} if total_leftover > 1e-9 else None
        )

        year_owner_closing = {"pretax": {o: buckets[o]["pretax"] for o in OWNER_BUCKETS}}
        for t in ("roth", "taxable", "hsa"):
            delta = pooled_closing_pregrowth[t] - pooled_opening[t]
            owner_balances_this_type = {o: buckets[o][t] for o in OWNER_BUCKETS}
            allocation = _allocate_type_delta_across_owners(owner_balances_this_type, delta, surplus_source_shares)
            for o in OWNER_BUCKETS:
                pregrowth = buckets[o][t] + allocation.get(o, 0.0)
                buckets[o][t] = max(0.0, pregrowth) * (1 + post_ret)
            year_owner_closing[t] = {o: buckets[o][t] for o in OWNER_BUCKETS}

        yearly.append({
            "jason_age": age, "justin_age": justin_age_this_year, "year": calendar_year,
            "phase": "phase2" if timeline.in_phase2(age) else "phase3",
            "pension": round(year_pen), "social_security": round(year_jss + year_uss),
            "rmd": round(rmd), "unmet_need": round(year_result.unmet_need),
            "portfolio_balance": round(sum(buckets[o][t] for o in OWNER_BUCKETS for t in ("pretax", "roth", "taxable", "hsa"))),
            "owner_balances": {o: {t: round(buckets[o][t]) for t in ("pretax", "roth", "taxable", "hsa")} for o in OWNER_BUCKETS},
            # Exposed so a death-transition consumer (Survivor Scenario's
            # own deceased-final-RMD catch-up) can tax a same-year
            # shortfall distribution at the IDENTICAL rate this year's
            # own normal draw already used, instead of recomputing the
            # formula a second, independent way (independent review,
            # 2026-09-08, sixth follow-up, finding 2).
            "pretax_tax_rate": round(pretax_tax_rate, 6),
        })

    return {
        "starting_buckets": starting,
        "ending_buckets": buckets,
        "yearly_detail": yearly,
        "phase2_start_age": phase2_start_age,
        "phase3_start_age": phase3_start_age,
        "retirement_end_age": timeline.end_age,
        "later_retiree": timeline.later_retiree,
        "account_ownership_limitation": starting["account_ownership_limitation"],
    }


def _project_529_saving_phase(starting_balance, monthly_contribution, years_to_college,
                               years_until_parent_retires, edu_return=0.07,
                               extra_years_of_contributions=0):
    """529 balance at the moment college starts: `starting_balance` grows
    for the full `years_to_college`, while `monthly_contribution` compounds
    monthly for as long as contributions actually run (capped by whichever
    comes first — the college start, or the parent's assumed retirement,
    same PARENT_RETIREMENT_AGE_ASSUMPTION cutoff both callers already
    apply), then that accumulated sum sits and compounds untouched for
    whatever's left of years_to_college.

    Calculation-engine consolidation Phase 5: previously
    run_education_projection (inside its year-by-year saving-phase loop,
    evaluated at its last iteration) and run_kids_projection (as a direct
    closed-form call) each computed this exact figure independently —
    verified to already agree by
    TestEducationAndKidsProjectionsAgreeOn529AtCollege in
    test_projection_engine.py, which is what made unifying them safe: this
    function reproduces both existing implementations' numbers exactly,
    it isn't introducing new behavior. Only the two functions' genuinely
    duplicated saving-phase formula moves here — run_education_projection's
    own year-by-year loop (needed for its chart) and run_kids_projection's
    drawdown/rollover/timeline math (which run_education_projection
    doesn't even share conceptually — Kids' timeline runs to age 60,
    Education's chart stops after college) are NOT touched: both are
    dense, already audit-hardened, off-by-one-sensitive code with no
    matching duplication to remove, and forcing them into one shape for
    its own sake was explicitly scoped out (see CALCULATION_CONTRACT.md's
    Phase 5 section).

    `extra_years_of_contributions` supports run_education_projection's
    `continue_contributions_during_college` option, which lets
    contributions run past college's start (they still can't run past the
    college-start point WITHIN this function's own saving-phase figure,
    since contributions after college has started fund the drawdown, not
    this balance — see `contribution_years` below, capped at
    years_to_college for the FV math even though the returned
    contribution_years itself may exceed it, matching
    run_education_projection's own `contribution_years` field, which the
    drawdown loop also needs uncapped).

    Returns (balance_at_college, contribution_years) — contribution_years
    is the raw (possibly college-window-extended) figure callers may need
    downstream; the balance calculation itself only ever counts
    contributions up to college's start."""
    contribution_window = years_to_college + extra_years_of_contributions
    contribution_years = min(contribution_window, years_until_parent_retires)
    contribution_years_before_college = min(contribution_years, years_to_college)
    years_dormant = max(0, years_to_college - contribution_years_before_college)
    fv_contrib_at_stop = _fv_annuity_monthly(monthly_contribution, edu_return, contribution_years_before_college)
    balance = _fv(starting_balance, edu_return, years_to_college) + _fv(fv_contrib_at_stop, edu_return, years_dormant)
    return balance, contribution_years


def _project_college_drawdown(starting_balance, monthly_contribution, edu_return,
                               unl_annual_cost, years_to_college, contribution_years,
                               track_unclamped=False, contributions_continue_during_college=False):
    """Simulates the COLLEGE_YEARS of 529 drawdown from `starting_balance`
    (the balance at the moment college starts, e.g. from
    _project_529_saving_phase). Each year: compound by edu_return, add one
    year's contribution (`_fv_annuity_monthly` for n=1) if contributions
    are still active for that year, subtract that year's cost-inflated
    college cost, floor the displayed balance at 0.

    Calculation-engine consolidation, item 7 (2026-09-07): previously
    run_education_projection and run_kids_projection each ran this exact
    recurrence independently (same COLLEGE_COST_INFLATION/UNL_CURRENT_ANNUAL
    cost formula, same compound-then-subtract-cost shape) with two
    behavioral differences, now the two flags below. Extracted only after
    confirming byte-identical output against both existing (already
    audit-hardened) implementations across a 384-scenario golden diff
    varying kid ages, parent age (including past the contribution cutoff),
    529 balances, contribution amounts, and continue_contributions_during_college
    — see tests/test_projection_engine.py::TestSharedCollegeDrawdownHelper.
    The age-60 timeline, Roth/custodial/bond tracks, and the 529-to-Roth
    rollover remain independent per CALCULATION_CONTRACT.md's Phase 5
    scoping — none of those have an Education-side equivalent to unify with.

    contributions_continue_during_college: run_education_projection's own
    option (default False, matching run_kids_projection's — Kids never
    contributes into the 529 once college starts). When True, a college
    year still gets a contribution if `years_to_college + yr <
    contribution_years` (contribution_years may exceed years_to_college
    exactly when this flag pushed it there — see
    _project_529_saving_phase's extra_years_of_contributions).

    track_unclamped: when True, also tracks the real (unfloored) balance
    so a genuine shortfall shows up as a negative number instead of
    vanishing at the floor — run_education_projection's `worst_deficit`.
    run_kids_projection doesn't need this (its own headline number is
    just the final floored balance), so it leaves this off and ignores
    worst_deficit/worst_deficit_years_out.

    Returns (yearly_balances, worst_deficit, worst_deficit_years_out):
    yearly_balances is a plain list of COLLEGE_YEARS floored balances
    (one per college year); worst_deficit is the most negative the
    unclamped balance ever reached (0.0 if track_unclamped is False or it
    never went negative); worst_deficit_years_out is
    years_to_college + (the 1-indexed college year it happened in),
    defaulting to years_to_college if it never went negative."""
    cost_base = unl_annual_cost * ((1 + COLLEGE_COST_INFLATION) ** years_to_college)
    bal = starting_balance
    bal_unclamped = starting_balance
    worst_deficit = 0.0
    worst_deficit_years_out = years_to_college
    yearly_balances = []
    for yr in range(COLLEGE_YEARS):
        college_year_index = years_to_college + yr
        contributing = contributions_continue_during_college and college_year_index < contribution_years
        contrib_fv = _fv_annuity_monthly(monthly_contribution, edu_return, 1) if contributing else 0
        cost = cost_base * ((1 + COLLEGE_COST_INFLATION) ** yr)
        bal_unclamped = bal_unclamped * (1 + edu_return) + contrib_fv - cost
        bal = max(0, bal * (1 + edu_return) + contrib_fv - cost)
        if track_unclamped and bal_unclamped < worst_deficit:
            worst_deficit = bal_unclamped
            worst_deficit_years_out = years_to_college + yr + 1
        yearly_balances.append(bal)
    return yearly_balances, worst_deficit, worst_deficit_years_out


def run_education_projection(inputs: Dict, accounts: List[Dict],
                              continue_contributions_during_college: bool = False,
                              surplus_529_monthly: Dict[str, float] = None) -> Dict:
    """surplus_529_monthly: optional {"abby": amount, "cooper": amount} —
    extra monthly 529 contributions directed via the "Assign Surplus" page's
    per-kid education-funding goals (see SURPLUS_GOAL_EDUCATION_ABBY/COOPER
    above and main.py._get_kids_surplus_529_monthly). Added ON TOP OF the
    flat abby_529_monthly/cooper_529_monthly planning-input rate, not in
    place of it. Defaults to None/empty so every existing caller that
    doesn't pass this is completely unaffected."""
    edu_return = 0.07
    surplus_529_monthly = surplus_529_monthly or {}

    abby_balance   = sum(a["balance"] for a in accounts if a["account_type"]=="529" and a["owner"]=="abby")
    cooper_balance = sum(a["balance"] for a in accounts if a["account_type"]=="529" and a["owner"]=="cooper")
    abby_monthly   = inputs.get("abby_529_monthly",   ABBY_MONTHLY_529_DEFAULT) + float(surplus_529_monthly.get("abby", 0) or 0)
    cooper_monthly = inputs.get("cooper_529_monthly", COOPER_MONTHLY_529_DEFAULT) + float(surplus_529_monthly.get("cooper", 0) or 0)
    kid1_age = inputs.get("kid1_age", 0)
    kid2_age = inputs.get("kid2_age", 0)

    # Contributions can't outlast your earning years — assume they stop once
    # you hit PARENT_RETIREMENT_AGE_ASSUMPTION, regardless of whether a kid
    # is still in the saving phase or college by then. This is a hard cap
    # applied on top of whatever the college-window assumption below allows.
    jason_age = inputs.get("jason_age", 45)
    years_until_parent_retires = max(0, PARENT_RETIREMENT_AGE_ASSUMPTION - jason_age)

    goals = []
    # "child" stays a fixed internal key (abby/cooper) matching account owners;
    # display name comes from planning_inputs kid1_name/kid2_name at the API layer.
    child_names = {"Abby": inputs.get("kid1_name", "Child 1"), "Cooper": inputs.get("kid2_name", "Child 2")}
    for child, balance_529, current_age, monthly_contrib in [
        ("Abby",   abby_balance,   kid1_age, abby_monthly),
        ("Cooper", cooper_balance, kid2_age, cooper_monthly),
    ]:
        years_to_college  = 18 - current_age
        unl_base = inputs.get("unl_annual_cost", UNL_CURRENT_ANNUAL)
        annual_cost_start = unl_base * ((1 + COLLEGE_COST_INFLATION) ** years_to_college)
        total_cost        = sum(annual_cost_start * ((1+COLLEGE_COST_INFLATION)**yr) for yr in range(COLLEGE_YEARS))

        # How many years from today contributions actually continue — the
        # college-window assumption (stop at college start, or keep going
        # through all 4 years if continue_contributions_during_college),
        # capped by the parent's retirement cutoff above, whichever is
        # sooner. This single number drives both the chart simulation below
        # and the funding_percent/gap figures — previously those were two
        # separate calculations (a closed-form _fv/_fv_annuity_monthly pair
        # for funding math, a year-by-year loop for the chart) that agreed
        # only by coincidence of both assuming uninterrupted contributions;
        # once contributions can stop early or extend, keeping two parallel
        # implementations risks exactly the kind of calc-drift bug this
        # codebase has been bitten by before, so the chart simulation below
        # is now the single source of truth for both.
        #
        # projected_529 (the balance at the moment college starts) comes
        # from _project_529_saving_phase — shared with run_kids_projection's
        # own version of this exact figure (calculation-engine
        # consolidation Phase 5). The year-by-year `yearly` chart below
        # still needs its own loop (interim balances, not just the final
        # one), and reproduces this same closed form at its last iteration
        # by construction — verified by test_education_yearly_chart_
        # agrees_with_shared_saving_phase_helper.
        extra_years = COLLEGE_YEARS if continue_contributions_during_college else 0
        projected_529, contribution_years = _project_529_saving_phase(
            balance_529, monthly_contrib, years_to_college, years_until_parent_retires,
            edu_return, extra_years_of_contributions=extra_years)

        # Saving-phase balance at each year boundary, computed in closed
        # form rather than iteratively adding monthly_contrib*12 once a
        # year: contributions actually land monthly and compound monthly
        # (same as _fv_annuity_monthly always assumed), so an annual lump-
        # sum step understates growth relative to that — a real regression
        # introduced when this was first rewritten to respect the
        # contribution cutoff below. This decomposition reproduces the
        # original monthly-compounding numbers exactly whenever the cutoff
        # isn't binding, and only changes the number when contributions
        # genuinely do stop early: contributions compound monthly for
        # whichever years they're actually active, then that accumulated
        # sum sits and compounds normally for any years left before college.
        yearly = []
        for yr in range(years_to_college):
            years_elapsed        = yr + 1
            contrib_years_so_far = min(contribution_years, years_elapsed)
            years_dormant        = years_elapsed - contrib_years_so_far
            fv_contrib_at_stop   = _fv_annuity_monthly(monthly_contrib, edu_return, contrib_years_so_far)
            fv_contrib_now       = _fv(fv_contrib_at_stop, edu_return, years_dormant)
            bal = _fv(balance_529, edu_return, years_elapsed) + fv_contrib_now
            yearly.append({"year_label": f"Age {current_age+yr+1}", "balance": round(bal), "phase": "saving"})

        # College-years drawdown still needs a year-by-year loop (costs are
        # withdrawn annually, so there's no clean closed form once
        # withdrawals and any continuing contributions overlap) — but a
        # still-active year's contribution now gets one year's worth of
        # monthly compounding credit (_fv_annuity_monthly for n=1) instead
        # of a flat, uncompounded monthly_contrib*12 add, for the same
        # reason as above.
        # Track the UNCLAMPED balance alongside the displayed (floored-at-0)
        # one, so a real shortfall shows up as a negative number we can
        # measure instead of vanishing into the floor. This is what the
        # "gap"/recommendation below is now based on — the actual worst
        # point the account hits during the 4 college years, not a snapshot
        # taken at day 1 that ignores the growth still to come. That old
        # 90%-target-at-day-1 snapshot was routinely flagging a "gap" (and
        # recommending more savings) even for a trajectory that already
        # ends college with money left over, which made no sense.
        # Shared with run_kids_projection's own drawdown loop — see
        # _project_college_drawdown's docstring (calculation-engine
        # consolidation, item 7). unl_base/years_to_college reproduce
        # this function's own annual_cost_start internally.
        drawdown_yearly, worst_deficit, worst_deficit_years_out = _project_college_drawdown(
            projected_529, monthly_contrib, edu_return, unl_base, years_to_college, contribution_years,
            track_unclamped=True, contributions_continue_during_college=continue_contributions_during_college)
        bal = drawdown_yearly[-1]
        for yr, yr_balance in enumerate(drawdown_yearly):
            yearly.append({"year_label": f"College yr {yr+1}", "balance": round(yr_balance), "phase": "drawdown"})

        gap = -worst_deficit if worst_deficit < 0 else 0
        funding_pct = 100 if gap == 0 else max(0, min(100, round((1 - gap / total_cost) * 100) if total_cost > 0 else 0))

        # How much extra it'd take to actually prevent that real shortfall:
        # extra monthly contributions run for as long as contributions can
        # actually happen (capped by the retirement cutoff, same as the
        # real ones), accumulate to a lump sum, then that lump sum keeps
        # growing untouched until the year the deficit actually hits.
        years_saving  = max(0, min(years_to_college, contribution_years))
        years_growth_after_saving = max(0, worst_deficit_years_out - years_saving)
        monthly_rate  = edu_return / 12
        months_saving = years_saving * 12
        if gap > 0 and months_saving > 0 and monthly_rate > 0:
            fv_annuity_needed = gap / ((1+edu_return) ** years_growth_after_saving)
            monthly_needed = fv_annuity_needed * monthly_rate / (((1+monthly_rate)**months_saving) - 1)
        else:
            monthly_needed = 0
        lump_sum = gap / ((1+edu_return)**worst_deficit_years_out) if gap>0 else 0

        goals.append({
            "child": child, "child_name": child_names[child], "current_age": current_age,
            "years_to_college": years_to_college,
            "current_529_balance": round(balance_529),
            "monthly_contribution": round(monthly_contrib),
            "contributions_stop_in_years": contribution_years,
            "projected_529_at_college": round(projected_529),
            "current_annual_cost": round(unl_base),
            "projected_total_cost": round(total_cost),
            "funding_percent": funding_pct,
            "funding_gap": round(gap),
            "monthly_savings_to_close_gap": round(monthly_needed),
            "lump_sum_to_close_gap": round(lump_sum),
            # funding_percent/gap now come from the same drawdown simulation
            # as balance_after_college — the worst point the (unclamped)
            # balance ever hits during the 4 college years — so the toggle
            # affects all of these consistently instead of just this last one.
            "balance_after_college": round(bal),
            "depleted_during_college": worst_deficit < 0,
            "yearly_chart": yearly,
        })

    return {"goals": goals}


def run_kids_projection(accounts: List[Dict], inputs: Dict = None,
                         surplus_529_monthly: Dict[str, float] = None) -> Dict:
    """surplus_529_monthly: optional {"abby": amount, "cooper": amount} —
    same extra per-kid 529 contribution as run_education_projection's
    parameter of the same name (see that docstring and
    SURPLUS_GOAL_EDUCATION_ABBY/COOPER above). Added on top of the flat
    abby_529_monthly/cooper_529_monthly rate; everything downstream
    (proj_529_at_18, the age-22 college drawdown, and the existing
    SECURE 2.0 529-to-Roth-IRA rollover capped at $35,000) operates on the
    resulting bal_529/proj_529_at_18 unchanged — a bigger 529 balance from
    added surplus simply produces a correspondingly larger (still capped)
    rollover, with no changes needed to that math itself."""
    edu_return  = 0.07
    roth_return = 0.07
    if inputs is None: inputs = {}
    surplus_529_monthly = surplus_529_monthly or {}

    abby_529_mo   = inputs.get("abby_529_monthly",       ABBY_MONTHLY_529_DEFAULT) + float(surplus_529_monthly.get("abby", 0) or 0)
    cooper_529_mo = inputs.get("cooper_529_monthly",     COOPER_MONTHLY_529_DEFAULT) + float(surplus_529_monthly.get("cooper", 0) or 0)
    kids_roth_mo  = inputs.get("kids_roth_monthly",      KIDS_ROTH_MONTHLY_DEFAULT)
    kids_cust_mo  = inputs.get("kids_custodial_monthly", KIDS_CUST_MONTHLY_DEFAULT)

    kid1_age = inputs.get("kid1_age", 0)
    kid2_age = inputs.get("kid2_age", 0)
    child_names = {"Abby": inputs.get("kid1_name", "Child 1"), "Cooper": inputs.get("kid2_name", "Child 2")}

    # 529 contributions stop at whichever comes first: the child turning 18,
    # or the parent hitting the same PARENT_RETIREMENT_AGE_ASSUMPTION cutoff
    # run_education_projection already applies to its own 529 contributions
    # (contribution_years = min(contribution_window, years_until_parent_retires)
    # there). This function used to ignore the parent's retirement entirely
    # and contribute all the way to 18 regardless — for a parent close to (or
    # past) that cutoff, that meant Education and Kids projected wildly
    # different 529-at-college balances for the literal same inputs (external
    # audit 2026-09-06: parent age 59, $100/mo, $0 starting balance —
    # Education (correctly capped at 1 more contribution year) predicted
    # $1,990; Kids (contributing for all 8 years to age 18) predicted
    # $12,820). Same rule, same constant, both functions now agree.
    jason_age = inputs.get("jason_age", 45)
    years_until_parent_retires = max(0, PARENT_RETIREMENT_AGE_ASSUMPTION - jason_age)

    kids = []
    for child, current_age, monthly_529 in [
        ("Abby",   kid1_age, abby_529_mo),
        ("Cooper", kid2_age, cooper_529_mo),
    ]:
        years_to_18 = 18 - current_age

        bal_529  = sum(a["balance"] for a in accounts if a["account_type"]=="529"      and a["owner"]==child.lower())
        bal_roth = sum(a["balance"] for a in accounts if a["account_type"]=="roth_ira" and a["owner"]==child.lower())
        bal_cust = sum(a["balance"] for a in accounts if a["account_type"]=="custodial" and a["owner"]==child.lower())
        bonds    = sum(a["balance"] for a in accounts if a["account_type"]=="other"    and a["owner"]==child.lower() and "bond" in a["name"].lower())

        # Shared with run_education_projection's own saving-phase figure —
        # see _project_529_saving_phase's docstring (calculation-engine
        # consolidation Phase 5). 529 contributions stop at whichever comes
        # first, college (18) or the parent's assumed retirement.
        proj_529_at_18, contribution_years_529 = _project_529_saving_phase(
            bal_529, monthly_529, years_to_18, years_until_parent_retires, edu_return)

        # Drawdown 100% of annual costs — shared with run_education_projection's
        # own drawdown loop, see _project_college_drawdown's docstring
        # (calculation-engine consolidation, item 7). Kids never contributes
        # into the 529 once college starts (contributions_continue_during_college
        # left at its default False), and doesn't need the unclamped/
        # worst-deficit tracking Education uses for its funding-gap figure.
        unl_base_kid = inputs.get("unl_annual_cost", UNL_CURRENT_ANNUAL)
        drawdown_yearly, _, _ = _project_college_drawdown(
            proj_529_at_18, monthly_529, edu_return, unl_base_kid, years_to_18, contribution_years_529)
        bal_after = drawdown_yearly[-1]
        roth_rollover  = min(bal_after, 35000)
        # A rollover is a real transfer — money moved from the 529 into the
        # Roth, not money that magically exists in both places. This used
        # to add roth_rollover into the Roth balance below while STILL
        # reporting the pre-rollover bal_after as proj_529_at_22 (external
        # audit 2026-09-06: a $10,000 529 growing to $13,108 with no college
        # costs showed that same $13,108 rolled into the Roth AND still sitting
        # in the 529). Net it out of the 529's own headline number here.
        proj_529_at_22 = round(bal_after - roth_rollover)

        proj_roth_at_18 = _fv(bal_roth, roth_return, years_to_18) + _fv_annuity_monthly(kids_roth_mo, roth_return, years_to_18)
        years_18_to_60  = 42
        proj_roth_at_22 = _fv(proj_roth_at_18, roth_return, 4) + roth_rollover
        proj_roth_at_60 = _fv(proj_roth_at_22, roth_return, years_18_to_60 - 4)

        proj_cust_at_18 = _fv(bal_cust, edu_return, years_to_18) + _fv_annuity_monthly(kids_cust_mo, edu_return, years_to_18)
        # Grown from the (already contributions-stop-at-18) age-18 value,
        # not from a separate formula that kept contributing all the way to
        # 24 — that separate formula used to disagree with the timeline
        # below (which has always stopped custodial contributions at 18,
        # same as every other per-kid contribution in this file), producing
        # a bigger headline number than the account timeline ever actually
        # showed (external audit 2026-09-06: $28,404 headline vs. $19,770 in
        # the timeline for the same $100/mo, age-10 inputs). Custodial
        # accounts have no "still earning income" assumption tied to
        # college like the 529/Roth kid contributions don't either — 18 is
        # simply where every other kid-contribution figure in this file
        # already stops, so the headline now matches instead of the other
        # way around.
        proj_cust_at_24 = _fv(proj_cust_at_18, edu_return, 24 - 18)
        proj_bonds_at_18 = _fv(bonds, 0.04, years_to_18) if bonds > 0 else 0
        # Nothing in this model actually spends the custodial account or
        # the savings bonds — unlike the 529 (drawn down for college) and
        # the Roth (its whole point is a value at 60), these just sat with
        # a projection that stopped at 18/24 and implied the money
        # vanished. Continue compounding both out to 60 — custodial picks
        # up from its already-computed age-24 value (contributions already
        # stopped at 18 in that number), bonds from today's balance at the
        # same 4% bond rate used for its age-18 projection.
        #
        # Bonds specifically stop earning interest at final maturity —
        # unlike custodial, which can keep compounding indefinitely — so
        # bond growth is additionally capped at BOND_MAX_GROWTH_YEARS.
        years_to_60 = max(0, 60 - current_age)
        bond_growth_years = min(years_to_60, BOND_MAX_GROWTH_YEARS)
        proj_cust_at_60 = _fv(proj_cust_at_24, edu_return, 60 - 24)
        proj_bonds_at_60 = _fv(bonds, 0.04, bond_growth_years) if bonds > 0 else 0

        # Timeline to 24. yr==0 (age == current_age, i.e. "right now") must
        # record the actual starting balances completely un-grown — this
        # used to apply a full year of growth (and a contribution) at yr==0
        # too, as if a year had already elapsed before the timeline even
        # starts, which silently added one extra year of compounding to
        # every later entry (the same class of bug as the roth_to_60 chart
        # fix below: e.g. this made the age-18 timeline entry disagree with
        # proj_529_at_18/proj_cust_at_18, which are computed directly via
        # _fv/_fv_annuity_monthly and were never subject to this off-by-
        # one). Every activity branch below is now skipped on yr==0 and the
        # age boundary for "still contributing" moved from `age < 18` to
        # `age <= 18` to compensate — the transition that LANDS exactly on
        # 18 is the contribution-years-th (or years_to_18-th) transition,
        # not one before it, once yr==0 no longer double-counts.
        timeline = []
        r_bal = bal_roth
        c_529 = bal_529
        c_cust = bal_cust
        for yr in range(24 - current_age + 1):
            age = current_age + yr
            if yr > 0:
                if age <= 18:
                    # `yr` is years elapsed since current_age, i.e. exactly
                    # which contribution year this transition represents —
                    # same parent-retirement cutoff as proj_529_at_18 above.
                    c_529 = c_529*(1+edu_return) + (monthly_529*12 if yr <= contribution_years_529 else 0)
                elif age <= 22:
                    yr_in_college = age - 1 - 18
                    unl_base = inputs.get('unl_annual_cost', UNL_CURRENT_ANNUAL)
                    cost = unl_base*((1+COLLEGE_COST_INFLATION)**(years_to_18+yr_in_college))
                    c_529 = max(0, c_529*(1+edu_return) - cost)
                    if age == 22:
                        # The rollover leaves the 529 for the Roth at this
                        # exact point (see roth_rollover netting above) —
                        # must decrease here too, not just compound
                        # untouched, or the timeline shows the same dollars
                        # sitting in both accounts at once (external audit
                        # 2026-09-06).
                        c_529 = max(0, c_529 - roth_rollover)
                else:
                    c_529 = c_529*(1+edu_return)
                if age <= 18:
                    r_bal = r_bal*(1+roth_return) + kids_roth_mo*12
                elif age == 22:
                    r_bal = r_bal*(1+roth_return) + roth_rollover
                else:
                    r_bal = r_bal*(1+roth_return)
                c_cust = c_cust*(1+edu_return) + (kids_cust_mo*12 if age <= 18 else 0)
            timeline.append({"age": age, "roth": round(r_bal), "529": round(c_529), "custodial": round(c_cust)})

        # yr==0 (age 18) must record proj_roth_at_18 itself, un-grown — this
        # used to grow `r` by one year BEFORE checking/recording age 18,
        # so the age-18 entry (and every entry after it, including the
        # age-60 entry against which proj_roth_at_60 is compared) carried
        # one extra year of compounding versus the summary figures above,
        # off by exactly a factor of (1+roth_return) (external audit
        # 2026-09-06: $171,443 summary vs. $183,444 chart at "age 60" for a
        # $10,000/no-further-contributions starting point — a 7% gap
        # matching roth_return exactly). Skipping the growth step on yr==0
        # makes this loop apply exactly `years_18_to_60` compoundings by the
        # time yr reaches years_18_to_60, identical to proj_roth_at_60's
        # own (4 + (years_18_to_60-4)) = years_18_to_60 total compoundings.
        roth_to_60 = []
        r = proj_roth_at_18
        for yr in range(years_18_to_60+1):
            age = 18 + yr
            if yr > 0:
                r = r*(1+roth_return)
                if age == 22:
                    r += roth_rollover
            if age % 5 == 0 or age == 18 or age == 22:
                roth_to_60.append({"age": age, "balance": round(r),
                                   "note": "529 rollover" if age == 22 and roth_rollover > 0 else None})

        kids.append({
            "child": child, "child_name": child_names[child], "current_age": current_age,
            "529":       {"current": round(bal_529), "at_18": round(proj_529_at_18), "at_22": proj_529_at_22, "monthly_contribution": round(monthly_529)},
            "roth":      {"current": round(bal_roth), "at_18": round(proj_roth_at_18), "at_22": round(proj_roth_at_22), "at_60": round(proj_roth_at_60), "monthly_contribution": kids_roth_mo, "529_rollover": round(roth_rollover)},
            "custodial": {"current": round(bal_cust), "at_18": round(proj_cust_at_18), "at_24": round(proj_cust_at_24), "at_60": round(proj_cust_at_60), "monthly_contribution": round(kids_cust_mo)},
            "bonds":     {"current": round(bonds), "at_18": round(proj_bonds_at_18), "at_60": round(proj_bonds_at_60)},
            "timeline":  timeline,
            "roth_to_60":roth_to_60,
        })

    return {"kids": kids}


def run_insurance_analysis(inputs: Dict, accounts: List[Dict]) -> Dict:
    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    inflation  = inputs["inflation_rate"]
    post_ret   = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]

    from debt_engine import DEBT_TYPES
    total_debt    = sum(a["balance"] for a in accounts if a["account_type"] in DEBT_TYPES)
    abby_529      = sum(a["balance"] for a in accounts if a["account_type"]=="529" and a["owner"]=="abby")
    cooper_529    = sum(a["balance"] for a in accounts if a["account_type"]=="529" and a["owner"]=="cooper")
    # years_to_college mirrors run_education_projection/run_kids_projection
    # (18 - current age, from the same planning_inputs kid1_age/kid2_age) —
    # this used to hardcode 7/11 regardless of the actual ages in Planning
    # Inputs, so the insurance page's college-funding gap would silently
    # drift out of sync with the Education/Kids pages as the kids got older.
    abby_years_to_college   = max(0, 18 - inputs.get("kid1_age", 0))
    cooper_years_to_college = max(0, 18 - inputs.get("kid2_age", 0))
    abby_proj     = _fv(abby_529, 0.07, abby_years_to_college)   + _fv_annuity_monthly(inputs.get("abby_529_monthly", ABBY_MONTHLY_529_DEFAULT), 0.07, abby_years_to_college)
    cooper_proj   = _fv(cooper_529, 0.07, cooper_years_to_college) + _fv_annuity_monthly(inputs.get("cooper_529_monthly", COOPER_MONTHLY_529_DEFAULT), 0.07, cooper_years_to_college)
    unl_base      = inputs.get("unl_annual_cost", UNL_CURRENT_ANNUAL)
    abby_cost     = sum(unl_base*((1+COLLEGE_COST_INFLATION)**(abby_years_to_college+yr)) for yr in range(4))
    cooper_cost   = sum(unl_base*((1+COLLEGE_COST_INFLATION)**(cooper_years_to_college+yr)) for yr in range(4))
    total_529_gap = max(0, abby_cost - abby_proj) + max(0, cooper_cost - cooper_proj)

    justin_years_to_ret = max(0, (60 - (jason_age - justin_age)) - justin_age)
    income_at_ret       = income_today * ((1+inflation)**justin_years_to_ret)
    real_rate           = ((1 + post_ret) / (1 + inflation) - 1) if post_ret != inflation else 0.0001
    retire_years        = 99 - (60 - (jason_age - justin_age))
    cap_income_need     = _pv_annuity(income_at_ret, real_rate, retire_years)

    investable = sum(
        a["balance"] for a in accounts
        if a["account_type"] in {"roth_ira","ira","hsa","taxable","401k"}
        and a["owner"] not in ("abby","cooper")
    )
    proj_assets  = _fv(investable, post_ret, justin_years_to_ret)
    income_gap   = max(0, cap_income_need - proj_assets)

    jason_total_need     = total_debt + total_529_gap + income_gap
    jason_current_coverage = inputs.get("jason_life_basic", 0) + inputs.get("jason_life_supplemental", 0) + inputs.get("jason_life_term", 0)
    jason_surplus        = jason_current_coverage - jason_total_need

    justin_total_need    = total_debt + total_529_gap
    justin_current_coverage = (inputs.get("justin_life_ul", 0) + inputs.get("justin_life_whole", 0)
                                + inputs.get("person2_life_employer", 0) + inputs.get("justin_life_term", 0)
                                + inputs.get("justin_life_kids", 0))
    justin_surplus       = justin_current_coverage - justin_total_need

    primary_key  = inputs.get("primary_residence_key", "")
    rental_key   = inputs.get("rental_property_key", "")
    home_value   = sum(a["balance"] for a in accounts if a["account_type"]=="real_estate" and primary_key and primary_key in a["name"])
    rental_value = sum(a["balance"] for a in accounts if a["account_type"]=="real_estate" and rental_key and rental_key in a["name"])

    # Umbrella adequacy: the standard advisor rule of thumb is coverage at
    # least equal to net worth (what a lawsuit could actually go after),
    # rounded up to the nearest $1M since that's how umbrella policies are
    # typically sold in $1M increments.
    from net_worth_engine import compute_net_worth
    net_worth = compute_net_worth(accounts)["net_worth"]
    umbrella_coverage = inputs.get("umbrella", 0)
    recommended_umbrella = max(1_000_000, math.ceil(max(0, net_worth) / 1_000_000) * 1_000_000)
    umbrella_gap = max(0, recommended_umbrella - umbrella_coverage)

    return {
        "jason":  {"total_need": round(jason_total_need), "debt_payoff": round(total_debt),
                   "college_funding": round(total_529_gap), "income_replacement": round(income_gap),
                   "current_coverage": jason_current_coverage, "surplus_gap": round(jason_surplus), "on_track": jason_surplus >= 0},
        "justin": {"total_need": round(justin_total_need), "debt_payoff": round(total_debt),
                   "college_funding": round(total_529_gap), "income_replacement": 0,
                   "current_coverage": justin_current_coverage, "surplus_gap": round(justin_surplus), "on_track": justin_surplus >= -100000},  # within $100k is acceptable since Jason keeps earning
        "property": {"primary_home_value": round(home_value), "primary_home_insured": inputs.get("home_insured", 0),
                     "primary_home_gap": round(max(0, home_value - inputs.get("home_insured", 0))),
                     "rental_value": round(rental_value), "rental_insured": 0, "umbrella": umbrella_coverage,
                     "net_worth": round(net_worth), "recommended_umbrella": recommended_umbrella,
                     "umbrella_gap": round(umbrella_gap), "umbrella_adequate": umbrella_gap <= 0},
        "disability": {"monthly_benefit": inputs.get("disability_monthly", 0), "to_age": 65, "funded_by": "Employer group policy"},
        "ltc": {"daily_benefit": inputs.get("ltc_daily", 200), "max_benefit": inputs.get("ltc_max", 0), "premium_annual": 369,
                "omaha_daily_cost_low": 134, "omaha_daily_cost_high": 248},
    }
