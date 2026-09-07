"""
Retirement Simulation Engine
Monte Carlo (1000 runs) + Historical Stress Tests
Uses full bucket structure: pretax (RMDs at 73 or 75, see rmd_start_age()), roth, taxable, hsa
"""

import random
import math
from typing import List, Dict, Tuple
from projection_engine import (
    JASON_SS_EARLY_DEFAULT as JASON_SS_EARLY,
    JASON_SS_DELAYED_DEFAULT as JASON_SS_DELAYED,
    JUSTIN_SPOUSAL_ANNUAL, JUSTIN_SPOUSAL_AGE, CURRENT_YEAR,
    _fv, _fv_annuity, _fv_annuity_monthly, _rmd, pension_for_age, rmd_start_age,
    _split_life_events, _post_retirement_year_effects, _post_retirement_asset_sale_events
)
# Module-level, not lazy/per-call — _pretax_marginal_tax_rate below is
# called once per simulated year per trial (up to ~40 years x 1000 trials
# per Monte Carlo/SWR request), so a per-call `from X import Y` measurably
# slowed the whole suite (112s -> 164s) the first time this was wired in
# as a lazy import matching projection_engine.py's own pattern. No import
# cycle here (retirement_tools_engine imports FROM projection_engine, not
# from this module), so there's no reason for it to be lazy in this file.
from retirement_tools_engine import marginal_rate as _marginal_rate, STD_DEDUCTION_MFJ_2026 as _STD_DEDUCTION

# Historical return parameters (annual, nominal)
EQUITY_MEAN   = 0.09    # ~9% long-run US equity (conservative)
EQUITY_STD    = 0.17    # ~17% std dev
BOND_MEAN     = 0.04
BOND_STD      = 0.06
EQUITY_WEIGHT = 0.80    # 80/20 portfolio assumption
BOND_WEIGHT   = 0.20

# Blended portfolio stats — targets ~7% mean to match planning assumptions
PORT_MEAN = 0.07
PORT_STD  = math.sqrt((EQUITY_WEIGHT * EQUITY_STD)**2 + (BOND_WEIGHT * BOND_STD)**2)

# Stress scenarios: list of (year_offset, return_override)
# year_offset 0 = first year of retirement
SCENARIOS = {
    "crash_2008": {
        "label": "2008 Market Crash",
        "description": "Year 1 down 37%, Year 2 down 5%, recovery follows",
        "overrides": {0: -0.37, 1: -0.05, 2: 0.265, 3: 0.15, 4: 0.02},
        "inflation_mult": 1.0,
    },
    "stagflation_1970s": {
        "label": "1970s Stagflation",
        "description": "10 years of 2% real returns with 8% inflation",
        "overrides": {i: 0.02 for i in range(10)},
        "inflation_mult": 4.0,
    },
    "lost_decade": {
        "label": "Lost Decade",
        "description": "10 years flat nominal returns (Japan/2000s), then recovery",
        "overrides": {i: 0.00 for i in range(10)},
        "inflation_mult": 1.0,
    },
    "early_sequence": {
        "label": "Early Retirement Sequence Risk",
        "description": "Worst 5 years land at retirement — down 30%, 18%, 12%, 25%, 8% — then recovery",
        "overrides": {0: -0.30, 1: -0.18, 2: -0.12, 3: -0.25, 4: -0.08, 5: 0.26, 6: 0.18, 7: 0.15},
        "inflation_mult": 1.0,
    },
    "bridge_job_loss": {
        "label": "Bridge Job Loss at Year 2",
        "description": "Bridge job ends at age 57 instead of 60 — 3 years of full retirement costs early",
        "overrides": {},
        "inflation_mult": 1.0,
        "bridge_years_override": 2,
    },
    "ss_reduction": {
        "label": "Social Security Cut 25%",
        "description": "SS benefits reduced to 75% of projected — reflects projected 2033 shortfall",
        "overrides": {},
        "inflation_mult": 1.0,
        "ss_reduction": 0.25,
    },
}


def _pretax_marginal_tax_rate(year_pen, year_jss, year_uss, rmd, state_tax_rate=0.0):
    """Same estimate run_retirement_projection's own withdrawal waterfall
    already uses for a year's pretax withdrawal — the current MFJ bracket
    table plus the household's state rate, applied to guaranteed income
    (pension + 85%-taxable SS) plus this year's RMD. Shared here so every
    withdrawal loop in this file prices a pretax dollar the same way
    run_retirement_projection does, instead of each one either inventing
    its own flat approximation or (until this fix) treating pretax
    withdrawals as tax-free entirely (external audit 2026-09-07 — Monte
    Carlo/Stress Tests/SWR did the latter)."""
    taxable_income_est = max(0, year_pen + (year_jss + year_uss) * 0.85 + rmd - _STD_DEDUCTION)
    return min(0.90, _marginal_rate(taxable_income_est) + max(0, state_tax_rate or 0))


def _grossed_up_draw(remaining_need, bucket_balance, tax_rate):
    """Draw from a taxed bucket so its AFTER-TAX proceeds (not the gross
    withdrawal) cover remaining_need — the pattern already established in
    run_retirement_projection's waterfall and reused piecemeal elsewhere
    in this file. Returns (new_bucket_balance, tax_paid, new_remaining_need).
    tax_rate == 0 degenerates to a plain untaxed draw."""
    if tax_rate <= 0:
        draw = min(remaining_need, bucket_balance)
        return bucket_balance - draw, 0.0, remaining_need - draw
    gross = remaining_need / (1 - tax_rate)
    draw  = min(gross, bucket_balance)
    tax   = draw * tax_rate
    return bucket_balance - draw, tax, remaining_need - (draw - tax)


def _run_single(
    pretax_start, roth_start, taxable_start, hsa_start,
    ret_age, jason_age, justin_age,
    pension_annual, jason_ss_annual, jason_ss_age,
    income_at_ret, inflation, post_ret,
    annual_returns: List[float],
    inflation_mults: List[float] = None,
    phase_inputs: dict = None,
    post_life_events: List[Dict] = None,
    justin_ss_annual: float = JUSTIN_SPOUSAL_ANNUAL,
    justin_ss_age: float = JUSTIN_SPOUSAL_AGE,
    retirement_end_age: int = 99,
    state_tax_rate: float = 0.0,
) -> Tuple[bool, List[float], List[float]]:
    """
    Run a single retirement simulation.
    Returns success through the configured horizon and annual bucket balances.

    post_life_events: withdrawal-phase life events already classified by
    _split_life_events() (i.e. only the "post" half) — the caller derives
    these once outside the N-run Monte Carlo loop rather than re-splitting
    life_events on every single simulated run. Defaults to None/no-op so
    every existing call site is unaffected.

    state_tax_rate: added on top of the estimated federal marginal rate
    for pretax withdrawals — every draw here used to move money between
    buckets with NO tax modeling at all, as if every dollar (RMDs
    included) were tax-free, unlike run_retirement_projection's own
    waterfall (external audit 2026-09-07, reproduced: $1M IRA / $100K
    spend / 1yr / 0% growth ended at $900,000 here vs. the deterministic
    projection's correctly-taxed $888,889). Now uses the same
    _pretax_marginal_tax_rate/_grossed_up_draw helpers as the rest of this
    file. Defaults to 0.0 (added federal-only if a caller doesn't pass a
    state rate) so this is purely additive for existing callers.

    justin_ss_annual/justin_ss_age: default to the module-level fallback
    constants (both 0) only for backward compatibility with any caller that
    doesn't pass real values — run_monte_carlo/run_stress_tests now always
    pass the actual configured Settings values (see their own docstrings);
    previously these were silently hardcoded to the JUSTIN_SPOUSAL_ANNUAL/
    JUSTIN_SPOUSAL_AGE fallback constants (both 0) regardless of what was
    actually configured in Settings, so spousal Social Security never
    showed up in Monte Carlo/Historical Stress at all (external audit
    2026-09-06).

    `survived` (the first tuple element) now also requires that no
    simulated year came up short on funding its spending need — previously
    it only checked whether the final balance was positive, so a run that
    silently rationed spending down to whatever a blocked withdrawal step
    allowed (see the RMD-gate fix below) could still be counted a
    "success" (external audit 2026-09-06, same root cause as
    projection_engine.py's on_track fix).
    """
    mort_age   = max(ret_age + 1, min(110, int(retirement_end_age or 99)))
    retire_yrs = mort_age - ret_age
    retirement_year = CURRENT_YEAR + max(0, ret_age - jason_age)

    pretax  = pretax_start
    roth    = roth_start
    taxable = taxable_start
    hsa     = hsa_start

    balances       = []
    pretax_bals    = []
    roth_bals      = []
    taxable_bals   = []
    _rmd_start     = rmd_start_age(jason_age)
    any_unmet_need = False

    for yr in range(retire_yrs):
        age      = ret_age + yr
        ret      = annual_returns[yr] if yr < len(annual_returns) else random.gauss(post_ret, PORT_STD)
        inf_mult = inflation_mults[yr] if inflation_mults and yr < len(inflation_mults) else 1.0
        eff_inf  = inflation * inf_mult

        # healthcare_pre/healthcare_post used to only get read out of
        # phase_inputs inside the `ret_age == 55` branch below, so every
        # other retirement age (56, 57, 58...) silently modeled $0
        # healthcare cost for the entire retirement -- an in-between-age
        # gap in the same family as the ones already fixed elsewhere in
        # this codebase (see CLAUDE.md). Read unconditionally here instead;
        # the age-55 branch below still owns the bridge-job/kids-at-home
        # phasing, but no longer needs its own separate hc_pre/hc_post
        # copies since it can just reuse these.
        healthcare_pre  = (phase_inputs or {}).get("healthcare_pre", 0) * ((1 + inflation) ** max(0, ret_age - jason_age))
        healthcare_post = (phase_inputs or {}).get("healthcare_post", 0) * ((1 + inflation) ** max(0, ret_age - jason_age))
        hc_this_year = healthcare_pre if age < 65 else healthcare_post
        if phase_inputs and ret_age == 55:
            bridge_years  = phase_inputs.get("bridge_years", 0)
            kids_years    = phase_inputs.get("kids_years", 0)
            kids_cost     = phase_inputs.get("kids_annual_cost", 0) * ((1 + inflation) ** max(0, ret_age - jason_age))
            bridge_income = phase_inputs.get("bridge_income", 0) * ((1 + inflation) ** max(0, ret_age - jason_age))
            hc_kids       = phase_inputs.get("healthcare_kids", 0) * ((1 + inflation) ** max(0, ret_age - jason_age))
            if yr < bridge_years:
                hc_this_year = 0
                year_need = max(0, income_at_ret*(1+eff_inf)**yr + kids_cost*(1+eff_inf)**yr - bridge_income*(1+eff_inf)**yr)
            elif yr < kids_years and age < 65:
                hc_this_year = hc_kids
                year_need = income_at_ret*(1+eff_inf)**yr + kids_cost*(1+eff_inf)**yr + hc_kids*(1+eff_inf)**yr
            elif age < 65:
                hc_this_year = healthcare_pre
                year_need = income_at_ret*(1+eff_inf)**yr + healthcare_pre*(1+eff_inf)**yr
            else:
                hc_this_year = healthcare_post
                year_need = income_at_ret*(1+eff_inf)**yr + healthcare_post*(1+eff_inf)**yr
        else:
            year_need = income_at_ret * ((1 + eff_inf) ** yr) + hc_this_year * ((1 + eff_inf) ** yr)

        # Life events active in the withdrawal phase — same treatment as
        # projection_engine.run_retirement_projection's yearly loop: a
        # recurring monthly delta adjusts year_need directly, a one-time
        # delta lands in taxable below instead.
        calendar_year = retirement_year + yr
        life_event_cash, life_event_monthly = _post_retirement_year_effects(post_life_events or [], calendar_year)
        year_need -= life_event_monthly

        year_pen  = pension_annual  # frozen pension, no COLA
        year_jss  = (jason_ss_annual * ((1 + eff_inf) ** max(0, age - jason_ss_age))
                     if age >= jason_ss_age else 0)
        # justin_ss_age is JUSTIN's own claiming age, so it has to be
        # compared against Justin's own current age, not Jason's `age` —
        # comparing it against `age` directly started/stopped the benefit
        # off by the couple's age gap whenever jason_age != justin_age
        # (external audit 2026-09-06, same bug as
        # projection_engine.run_retirement_projection's yearly loop).
        justin_age_this_year = age - (jason_age - justin_age)
        year_uss  = (justin_ss_annual * ((1 + eff_inf) ** max(0, justin_age_this_year - justin_ss_age))
                     if justin_age_this_year >= justin_ss_age else 0)
        fixed     = year_pen + year_jss + year_uss
        net_need  = max(0, year_need - fixed)

        if life_event_cash:
            taxable += life_event_cash

        # RMD
        rmd = _rmd(pretax, age, _rmd_start)
        remaining = net_need + max(0, -taxable)
        taxable = max(0, taxable)
        taxable += max(0, fixed - year_need)

        pretax_tax_rate = _pretax_marginal_tax_rate(year_pen, year_jss, year_uss, rmd, state_tax_rate)

        if rmd > 0:
            actual_rmd = min(rmd, pretax)
            pretax -= actual_rmd
            rmd_tax = actual_rmd * pretax_tax_rate
            after_tax_rmd = actual_rmd - rmd_tax
            if after_tax_rmd <= remaining:
                remaining -= after_tax_rmd
            else:
                taxable += after_tax_rmd - remaining
                remaining = 0

        if remaining > 0 and taxable > 0:
            draw = min(remaining, taxable)
            taxable -= draw; remaining -= draw

        # Further pretax withdrawal used to be gated on `rmd == 0`, blocking
        # any additional draw for the rest of the plan once RMD age was
        # reached even with plenty of pretax balance and a large unmet
        # need left — external audit 2026-09-06, same bug as
        # projection_engine.py's withdrawal waterfall (see its comment).
        # Grossed up so its after-tax proceeds (not the gross withdrawal)
        # cover `remaining` — this and the RMD tax above used to be
        # entirely untaxed, unlike run_retirement_projection's own
        # waterfall (external audit 2026-09-07).
        if remaining > 0 and pretax > 0:
            pretax, tax_paid, remaining = _grossed_up_draw(remaining, pretax, pretax_tax_rate)

        if remaining > 0 and hsa > 0:
            draw = min(remaining, hsa)
            hsa -= draw; remaining -= draw

        if remaining > 0 and roth > 0:
            draw = min(remaining, roth)
            roth -= draw; remaining -= draw

        if remaining > 0:
            any_unmet_need = True

        # Grow at this year's return
        pretax  = max(0, pretax  * (1 + ret))
        roth    = max(0, roth    * (1 + ret))
        taxable = max(0, taxable * (1 + ret))
        hsa     = max(0, hsa     * (1 + ret))

        total = pretax + roth + taxable + hsa
        balances.append(round(total))
        pretax_bals.append(round(pretax))
        roth_bals.append(round(roth))
        taxable_bals.append(round(taxable))

    survived = (balances[-1] > 0 if balances else False) and not any_unmet_need
    return survived, balances, pretax_bals, roth_bals, taxable_bals


def run_swr_analysis(inputs: Dict, accounts: List[Dict], ret_age: int = 60, ss_timing: str = "early",
                      target_success: float = 0.95, life_events: List[Dict] = None,
                      surplus_allocations: List[Dict] = None) -> Dict:
    """Find the safe withdrawal rate at target success rate (default 95%).

    life_events: threaded through to run_retirement_projection below so
    pre-retirement events affect the starting portfolio; withdrawal-phase
    events also adjust each trial's available cash and spending.

    surplus_allocations: threaded through to run_retirement_projection
    below for the starting portfolio only — this feature has no
    withdrawal-phase half at all (see projection_engine.py), so there is
    nothing further to wire in here."""
    random.seed(42)

    jason_age    = inputs["jason_age"]
    justin_age   = inputs["justin_age"]
    inflation    = inputs["inflation_rate"]
    post_ret     = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]
    healthcare_pre  = inputs.get("healthcare_pre_medicare", 0)
    healthcare_post = inputs.get("healthcare_post_medicare", 0)

    years_to_ret = max(0, ret_age - jason_age)
    pension_annual = pension_for_age(inputs, ret_age)
    jason_ss_early  = inputs.get("jason_social_security", JASON_SS_EARLY)
    jason_ss_delayed = inputs.get("jason_ss_delayed", JASON_SS_DELAYED)
    jason_ss_annual = jason_ss_early if ss_timing == "early" else jason_ss_delayed
    jason_ss_age    = 62 if ss_timing == "early" else 67
    justin_ss       = inputs.get("justin_social_security", JUSTIN_SPOUSAL_ANNUAL)
    justin_ss_age   = inputs.get("justin_ss_age", JUSTIN_SPOUSAL_AGE)

    # Get portfolio at retirement from projection engine — pass ret_age explicitly
    # so this works for any age, not just the default 55/60/65 anchors.
    from projection_engine import run_retirement_projection
    _proj     = run_retirement_projection(inputs, accounts, ret_ages=[ret_age], life_events=life_events,
                                           surplus_allocations=surplus_allocations)
    _scenario = next((s for s in _proj["scenarios"] if s["label"] == f"age_{ret_age}_{ss_timing}"), None)
    pretax_at_ret  = _scenario["pretax_at_retirement"]
    roth_at_ret    = _scenario["roth_at_retirement"]
    taxable_at_ret = _scenario["taxable_at_retirement"]
    hsa_at_ret     = _scenario["hsa_at_retirement"]
    portfolio = pretax_at_ret + roth_at_ret + taxable_at_ret + hsa_at_ret

    end_age = max(ret_age + 1, min(110, int(inputs.get("retirement_end_age") or 99)))
    retire_yrs = end_age - ret_age
    N          = 1000
    _rmd_start = rmd_start_age(jason_age)

    # Pre-generate random returns for reproducibility
    all_returns = [[random.gauss(post_ret, PORT_STD) for _ in range(retire_yrs)] for _ in range(N)]

    retirement_year = CURRENT_YEAR + years_to_ret
    _, post_events = _split_life_events(life_events, retirement_year)
    post_events = post_events + _post_retirement_asset_sale_events(inputs, jason_age, ret_age)

    def success_at_withdrawal(annual_withdrawal_today):
        """How many of N simulations survive with this portfolio withdrawal?"""
        successes = 0
        for returns in all_returns:
            pretax  = pretax_at_ret
            roth    = roth_at_ret
            taxable = taxable_at_ret
            hsa     = hsa_at_ret
            survived = True

            for yr in range(retire_yrs):
                age = ret_age + yr
                ret = returns[yr]

                # Guaranteed income this year
                year_pen = pension_annual
                year_jss = jason_ss_annual * ((1+inflation)**max(0,age-jason_ss_age)) if age >= jason_ss_age else 0
                # justin_ss_age is Justin's own claiming age — compare it to
                # Justin's own current age (age offset by the couple's age
                # gap), not Jason's `age` directly (external audit
                # 2026-09-06, same bug fixed in _run_single/
                # run_retirement_projection).
                justin_age_this_year = age - (jason_age - justin_age)
                year_uss = justin_ss * ((1+inflation)**max(0,justin_age_this_year-justin_ss_age)) if justin_age_this_year >= justin_ss_age else 0
                guaranteed = year_pen + year_jss + year_uss

                # Portfolio withdrawal needed (inflation-adjusted, on top of guaranteed)
                portfolio_draw = annual_withdrawal_today * ((1+inflation)**yr)

                # RMD
                rmd = _rmd(pretax, age, _rmd_start)
                event_cash, event_monthly = _post_retirement_year_effects(post_events, retirement_year + yr)
                taxable += event_cash
                remaining = max(0, portfolio_draw - event_monthly) + max(0, -taxable)
                taxable = max(0, taxable) + max(0, event_monthly - portfolio_draw)

                pretax_tax_rate = _pretax_marginal_tax_rate(year_pen, year_jss, year_uss, rmd,
                                                              inputs.get("state_income_tax_rate", 0))

                if rmd > 0:
                    actual_rmd = min(rmd, pretax)
                    pretax -= actual_rmd
                    rmd_tax = actual_rmd * pretax_tax_rate
                    after_tax_rmd = actual_rmd - rmd_tax
                    if after_tax_rmd <= remaining:
                        remaining -= after_tax_rmd
                    else:
                        taxable += after_tax_rmd - remaining
                        remaining = 0

                if remaining > 0 and taxable > 0:
                    draw = min(remaining, taxable); taxable -= draw; remaining -= draw
                # Previously gated on `rmd == 0`, blocking any further
                # pretax withdrawal for the rest of the plan once RMD age
                # was reached — external audit 2026-09-06, same bug as
                # projection_engine.py/_run_single (see their comments).
                # Grossed up for tax like the RMD above — this whole loop
                # used to treat every pretax withdrawal as tax-free
                # (external audit 2026-09-07, same root cause as
                # _run_single).
                if remaining > 0 and pretax > 0:
                    pretax, _tax, remaining = _grossed_up_draw(remaining, pretax, pretax_tax_rate)
                if remaining > 0 and hsa > 0:
                    draw = min(remaining, hsa); hsa -= draw; remaining -= draw
                if remaining > 0 and roth > 0:
                    draw = min(remaining, roth); roth -= draw; remaining -= draw

                # Fail if the portfolio is fully depleted, OR if spending
                # need went unmet this year even though buckets still held
                # money (rationing) — previously only the full-depletion
                # case counted as failure, so the success rate never
                # reflected a plan quietly failing to fund its stated
                # withdrawal (external audit 2026-09-06).
                total = pretax + roth + taxable + hsa
                if remaining > 0:
                    survived = False
                    break

                pretax  = max(0, pretax  * (1 + ret))
                roth    = max(0, roth    * (1 + ret))
                taxable = max(0, taxable * (1 + ret))
                hsa     = max(0, hsa     * (1 + ret))

            if survived:
                successes += 1
        return successes / N

    # Binary search for the withdrawal amount that hits target_success
    lo, hi = 0, portfolio * 0.15  # search between 0 and 15% of portfolio
    for _ in range(20):
        mid = (lo + hi) / 2
        rate = success_at_withdrawal(mid)
        if rate >= target_success:
            lo = mid
        else:
            hi = mid

    safe_withdrawal = lo
    safe_withdrawal_rate = safe_withdrawal / portfolio if portfolio > 0 else 0

    # Guaranteed income steady-state (once all income sources active)
    # Show pension + SS as they'll be in the first full year all sources are running
    ss_start_age   = max(jason_ss_age, justin_ss_age)  # age when both SS streams active
    years_to_ss    = max(0, ss_start_age - ret_age)
    guaranteed_first_year = (
        pension_annual +
        jason_ss_annual * ((1 + inflation) ** years_to_ss) +
        justin_ss * ((1 + inflation) ** years_to_ss)
    )
    # Also track day-one guaranteed (pension only if retiring before SS)
    guaranteed_day_one = pension_annual
    if ret_age >= jason_ss_age:
        guaranteed_day_one += jason_ss_annual
    if ret_age >= justin_ss_age:
        guaranteed_day_one += justin_ss

    total_safe_spend    = safe_withdrawal + guaranteed_day_one
    income_target       = (income_today + healthcare_pre) * ((1+inflation)**years_to_ret)
    cushion_pct         = round((total_safe_spend / income_target - 1) * 100, 1) if income_target > 0 else 0

    return {
        "portfolio_at_retirement":   round(portfolio),
        "safe_withdrawal_annual":    round(safe_withdrawal),
        "safe_withdrawal_rate":      round(safe_withdrawal_rate * 100, 2),
        "guaranteed_income_annual":  round(guaranteed_day_one),
        "guaranteed_income_steadystate": round(guaranteed_first_year),
        "pension_annual":            round(pension_annual),
        "jason_ss_annual":           round(jason_ss_annual),
        "justin_ss_annual":          round(justin_ss),
        "ss_start_age":              ss_start_age,
        "total_safe_spend":          round(total_safe_spend),
        "income_target":             round(income_target),
        "cushion_pct":               cushion_pct,
        "on_track":                  total_safe_spend >= income_target,
        "target_success_rate":       round(target_success * 100),
        "retirement_age":            ret_age,
        "ss_timing":                 ss_timing,
    }


def run_monte_carlo(inputs: Dict, accounts: List[Dict], ret_age: int = 60, ss_timing: str = "early",
                     life_events: List[Dict] = None, surplus_allocations: List[Dict] = None) -> Dict:
    """Run 1000 Monte Carlo simulations.

    life_events: optional list of life_events rows (already filtered to
    included_in_projection=true by the caller). Pre-retirement events
    affect the starting bucket balances via run_retirement_projection
    below; withdrawal-phase events are applied inside each simulated run
    via _run_single. Defaults to None/no-op.

    surplus_allocations: optional list of surplus_allocations rows (only
    the two retirement-relevant goals matter — see projection_engine.py).
    Affects only the starting bucket balances via run_retirement_projection
    below — this feature has no withdrawal-phase half, so _run_single is
    untouched. Defaults to None/no-op."""
    random.seed(42)  # reproducible

    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    inflation  = inputs["inflation_rate"]
    pre_ret    = inputs["expected_return_pre_retirement"]
    post_ret   = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]
    annual_hsa   = inputs["annual_hsa_contribution"]
    annual_rsu   = inputs["annual_rsu_value"]

    years_to_ret = max(0, ret_age - jason_age)
    pension_annual  = pension_for_age(inputs, ret_age)
    jason_ss_early  = inputs.get("jason_social_security", JASON_SS_EARLY)
    jason_ss_delayed = inputs.get("jason_ss_delayed", JASON_SS_DELAYED)
    jason_ss_annual = jason_ss_early if ss_timing == "early" else jason_ss_delayed
    jason_ss_age    = 62 if ss_timing == "early" else 67
    # Previously hardcoded to the JUSTIN_SPOUSAL_ANNUAL/JUSTIN_SPOUSAL_AGE
    # fallback constants (both 0) inside _run_single regardless of what was
    # actually configured — Monte Carlo never reflected a real spousal SS
    # benefit no matter what Settings said (external audit 2026-09-06).
    # Read the same way run_retirement_projection/run_swr_analysis already
    # do.
    justin_ss_annual = inputs.get("justin_social_security", JUSTIN_SPOUSAL_ANNUAL)
    justin_ss_age    = inputs.get("justin_ss_age", JUSTIN_SPOUSAL_AGE)
    income_at_ret   = income_today * ((1 + inflation) ** years_to_ret)

    # Pull bucket values from projection_engine — pass ret_age explicitly so
    # this works for any age, not just the default 55/60/65 anchors.
    from projection_engine import (run_retirement_projection)
    _proj = run_retirement_projection(inputs, accounts, ret_ages=[ret_age], life_events=life_events,
                                       surplus_allocations=surplus_allocations)
    _scenario = next((s for s in _proj["scenarios"] if s["label"] == f"age_{ret_age}_{ss_timing}"), None)
    pretax_at_ret  = _scenario["pretax_at_retirement"]
    roth_at_ret    = _scenario["roth_at_retirement"]
    taxable_at_ret = _scenario["taxable_at_retirement"]
    hsa_at_ret     = _scenario["hsa_at_retirement"]

    end_age = max(ret_age + 1, min(110, int(inputs.get("retirement_end_age") or 99)))
    retire_yrs = end_age - ret_age
    N = 1000

    # Withdrawal-phase life events, split once outside the N-run loop —
    # the pre-retirement half was already folded into the bucket values
    # above via run_retirement_projection.
    retirement_year = CURRENT_YEAR + years_to_ret
    _, post_life_events = _split_life_events(life_events, retirement_year)
    post_life_events = post_life_events + _post_retirement_asset_sale_events(inputs, jason_age, ret_age)

    successes = 0
    all_balances = []

    # Built once outside the N-run loop — doesn't depend on the loop
    # variable. Always populated (not gated to ret_age==55): healthcare_pre/
    # healthcare_post need to reach _run_single for every retirement age, not
    # just the age-55 bridge scenario (see _run_single's own comment for the
    # in-between-age bug this fixes). The bridge/kids fields are harmless for
    # other ages since _run_single only reads them when ret_age == 55.
    _phase = {
        "bridge_years":     inputs.get("bridge_years_55", 0),
        "kids_years":       inputs.get("kids_years_at_home_55", 0),
        "kids_annual_cost": inputs.get("kids_annual_cost", 0),
        "bridge_income":    inputs.get("bridge_income_55", 0),
        "healthcare_kids":  inputs.get("healthcare_kids", 0),
        "healthcare_pre":   inputs.get("healthcare_pre_medicare", 0),
        "healthcare_post":  inputs.get("healthcare_post_medicare", 0),
    }

    for _ in range(N):
        returns = [random.gauss(post_ret, PORT_STD) for _ in range(retire_yrs)]
        survived, balances, *_ = _run_single(
            pretax_at_ret, roth_at_ret, taxable_at_ret, hsa_at_ret,
            ret_age, jason_age, justin_age,
            pension_annual, jason_ss_annual, jason_ss_age,
            income_at_ret, inflation, post_ret, returns,
            phase_inputs=_phase,
            post_life_events=post_life_events,
            justin_ss_annual=justin_ss_annual,
            justin_ss_age=justin_ss_age,
            retirement_end_age=end_age,
            state_tax_rate=inputs.get("state_income_tax_rate", 0),
        )
        if survived: successes += 1
        all_balances.append(balances)

    success_rate = round(successes / N * 100, 1)

    # Percentile bands — every 2 years for chart
    ages = list(range(ret_age, end_age))
    p10, p25, p50, p75, p90 = [], [], [], [], []
    for yr in range(retire_yrs):
        vals = sorted(b[yr] if yr < len(b) else 0 for b in all_balances)
        p10.append({"age": ages[yr], "balance": vals[int(N*0.10)]})
        p25.append({"age": ages[yr], "balance": vals[int(N*0.25)]})
        p50.append({"age": ages[yr], "balance": vals[int(N*0.50)]})
        p75.append({"age": ages[yr], "balance": vals[int(N*0.75)]})
        p90.append({"age": ages[yr], "balance": vals[int(N*0.90)]})

    # Chart data combining percentiles
    chart = [{"age": ages[yr],
              "p10": p10[yr]["balance"], "p25": p25[yr]["balance"],
              "p50": p50[yr]["balance"],
              "p75": p75[yr]["balance"], "p90": p90[yr]["balance"]}
             for yr in range(0, retire_yrs, 2)]

    # Median depletion age
    median_run = sorted(all_balances, key=lambda b: b[-1])[N//2]
    depletion_age = end_age
    for i, bal in enumerate(median_run):
        if bal <= 0:
            depletion_age = ret_age + i
            break

    return {
        "success_rate": success_rate,
        "retirement_age": ret_age,
        "retirement_end_age": end_age,
        "ss_timing": ss_timing,
        "portfolio_at_retirement": round(pretax_at_ret + roth_at_ret + taxable_at_ret + hsa_at_ret),
        "median_final_balance": round(sorted(b[-1] for b in all_balances)[N//2]),
        "median_depletion_age": depletion_age,
        "simulations": N,
        "chart": chart,
    }


def run_stress_tests(inputs: Dict, accounts: List[Dict], ret_age: int = 60, ss_timing: str = "early",
                      life_events: List[Dict] = None, surplus_allocations: List[Dict] = None) -> Dict:
    """Run deterministic stress test scenarios.

    life_events: see run_monte_carlo — same optional, defaults-to-no-op
    wiring.

    surplus_allocations: see run_monte_carlo — same optional,
    starting-balance-only wiring."""
    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    inflation  = inputs["inflation_rate"]
    pre_ret    = inputs["expected_return_pre_retirement"]
    post_ret   = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]
    annual_hsa   = inputs["annual_hsa_contribution"]
    annual_rsu   = inputs["annual_rsu_value"]

    years_to_ret   = max(0, ret_age - jason_age)
    pension_annual  = pension_for_age(inputs, ret_age)
    jason_ss_early  = inputs.get("jason_social_security", JASON_SS_EARLY)
    jason_ss_delayed = inputs.get("jason_ss_delayed", JASON_SS_DELAYED)
    jason_ss_annual = jason_ss_early if ss_timing == "early" else jason_ss_delayed
    jason_ss_age    = 62 if ss_timing == "early" else 67
    # See run_monte_carlo's identical comment — previously hardcoded to 0
    # inside _run_single regardless of Settings (external audit 2026-09-06).
    justin_ss_annual = inputs.get("justin_social_security", JUSTIN_SPOUSAL_ANNUAL)
    justin_ss_age    = inputs.get("justin_ss_age", JUSTIN_SPOUSAL_AGE)
    income_at_ret   = income_today * ((1 + inflation) ** years_to_ret)

    from projection_engine import run_retirement_projection
    _proj = run_retirement_projection(inputs, accounts, ret_ages=[ret_age], life_events=life_events,
                                       surplus_allocations=surplus_allocations)
    _scenario = next(s for s in _proj["scenarios"] if s["label"] == f"age_{ret_age}_{ss_timing}")
    pretax_at_ret  = _scenario["pretax_at_retirement"]
    roth_at_ret    = _scenario["roth_at_retirement"]
    taxable_at_ret = _scenario["taxable_at_retirement"]
    hsa_at_ret     = _scenario["hsa_at_retirement"]

    end_age = max(ret_age + 1, min(110, int(inputs.get("retirement_end_age") or 99)))
    retire_yrs = end_age - ret_age

    # Withdrawal-phase life events, split once — the pre-retirement half
    # is already folded into the bucket values above.
    retirement_year = CURRENT_YEAR + years_to_ret
    _, post_life_events = _split_life_events(life_events, retirement_year)
    post_life_events = post_life_events + _post_retirement_asset_sale_events(inputs, jason_age, ret_age)

    # Base case — deterministic at post_ret every year
    base_returns = [post_ret] * retire_yrs
    _phase_base = {
        "bridge_years":     inputs.get("bridge_years_55", 0),
        "kids_years":       inputs.get("kids_years_at_home_55", 0),
        "kids_annual_cost": inputs.get("kids_annual_cost", 0),
        "bridge_income":    inputs.get("bridge_income_55", 0),
        "healthcare_kids":  inputs.get("healthcare_kids", 0),
        "healthcare_pre":   inputs.get("healthcare_pre_medicare", 0),
        "healthcare_post":  inputs.get("healthcare_post_medicare", 0),
    }  # always populated -- see _run_single's comment on the in-between-age fix
    base_survived, base_bals, *_ = _run_single(
        pretax_at_ret, roth_at_ret, taxable_at_ret, hsa_at_ret,
        ret_age, jason_age, justin_age,
        pension_annual, jason_ss_annual, jason_ss_age,
        income_at_ret, inflation, post_ret, base_returns,
        phase_inputs=_phase_base,
        post_life_events=post_life_events,
        justin_ss_annual=justin_ss_annual,
        justin_ss_age=justin_ss_age,
        retirement_end_age=end_age,
        state_tax_rate=inputs.get("state_income_tax_rate", 0),
    )

    results = {"base": {
        "label": f"Base Case ({post_ret * 100:g}% every year)",
        "survived": base_survived,
        "final_balance": base_bals[-1],
        "chart": [{"age": ret_age+i, "balance": b} for i, b in enumerate(base_bals) if i%2==0],
    }}

    for key, scenario in SCENARIOS.items():
        overrides  = scenario["overrides"]
        inf_mult   = scenario.get("inflation_mult", 1.0)
        ss_mult    = 1.0 - scenario.get("ss_reduction", 0.0)
        bridge_override = scenario.get("bridge_years_override", None)

        # For bridge job loss — modify inputs copy
        sim_inputs = dict(inputs)
        if bridge_override is not None:
            sim_inputs["bridge_years_55"] = bridge_override

        # For SS reduction
        scenario_ss = jason_ss_annual * ss_mult
        scenario_justin_ss = inputs.get("justin_social_security", JUSTIN_SPOUSAL_ANNUAL) * ss_mult

        returns = []
        inf_mults = []
        for yr in range(retire_yrs):
            if yr in overrides:
                returns.append(overrides[yr])
                inf_mults.append(inf_mult if yr < 10 else 1.0)
            else:
                returns.append(post_ret)
                inf_mults.append(1.0)

        # Re-project buckets if bridge years changed
        if bridge_override is not None and ret_age == 55:
            _proj2 = run_retirement_projection(sim_inputs, accounts, ret_ages=[ret_age], life_events=life_events,
                                                surplus_allocations=surplus_allocations)
            _s2 = next(s for s in _proj2["scenarios"] if s["label"] == f"age_{ret_age}_{ss_timing}")
            sim_pretax  = _s2["pretax_at_retirement"]
            sim_roth    = _s2["roth_at_retirement"]
            sim_taxable = _s2["taxable_at_retirement"]
            sim_hsa     = _s2["hsa_at_retirement"]
        else:
            sim_pretax, sim_roth, sim_taxable, sim_hsa = pretax_at_ret, roth_at_ret, taxable_at_ret, hsa_at_ret

        # For early sequence risk — sort worst returns to front
        if key == "early_sequence":
            normal_returns = [random.gauss(post_ret, PORT_STD) for _ in range(retire_yrs - len(overrides))]
            normal_returns.sort()  # worst first after the override years
            seq_returns = [overrides.get(yr, normal_returns[max(0, yr-len(overrides))]) for yr in range(retire_yrs)]
            returns = seq_returns

        # Use scenario SS for ss_reduction scenario — previously only
        # Jason's benefit was ever actually reduced and passed through;
        # scenario_justin_ss was computed above but never used, so the "SS
        # Cut 25%" stress scenario silently left Justin's spousal benefit
        # at its full, un-cut value (external audit 2026-09-06, same
        # hardcoded/unwired-spousal-SS root cause as run_monte_carlo's fix
        # above).
        run_ss = scenario_ss if scenario.get("ss_reduction") else jason_ss_annual
        run_justin_ss = scenario_justin_ss if scenario.get("ss_reduction") else justin_ss_annual

        _phase_st = {
            "bridge_years":     sim_inputs.get("bridge_years_55", inputs.get("bridge_years_55", 0)),
            "kids_years":       inputs.get("kids_years_at_home_55", 0),
            "kids_annual_cost": inputs.get("kids_annual_cost", 0),
            "bridge_income":    inputs.get("bridge_income_55", 0),
            "healthcare_kids":  inputs.get("healthcare_kids", 0),
            "healthcare_pre":   inputs.get("healthcare_pre_medicare", 0),
            "healthcare_post":  inputs.get("healthcare_post_medicare", 0),
        }  # always populated -- see _run_single's comment on the in-between-age fix
        scen_survived, bals, ptx, rth, txb = _run_single(
            sim_pretax, sim_roth, sim_taxable, sim_hsa,
            ret_age, jason_age, justin_age,
            pension_annual, run_ss, jason_ss_age,
            income_at_ret, inflation, post_ret, returns, inf_mults,
            phase_inputs=_phase_st,
            post_life_events=post_life_events,
            justin_ss_annual=run_justin_ss,
            justin_ss_age=justin_ss_age,
            retirement_end_age=end_age,
            state_tax_rate=inputs.get("state_income_tax_rate", 0),
        )

        # Find depletion age
        dep_age = end_age
        for i, b in enumerate(bals):
            if b <= 0:
                dep_age = ret_age + i
                break

        results[key] = {
            "label": scenario["label"],
            "description": scenario["description"],
            "survived": scen_survived,
            "final_balance": bals[-1],
            "depletion_age": dep_age,
            "lowest_balance": min(bals),
            "lowest_balance_age": ret_age + bals.index(min(bals)),
            "chart": [{"age": ret_age+i, "balance": b, "base": base_bals[i]}
                      for i, b in enumerate(bals) if i%2==0],
        }

    return {
        "retirement_age": ret_age,
        "retirement_end_age": end_age,
        "ss_timing": ss_timing,
        "scenarios": results,
    }

def run_roth_conversion_analysis(inputs: Dict, accounts: List[Dict], ret_age: int = 60, ss_timing: str = "early",
                                  life_events: List[Dict] = None,
                                  surplus_allocations: List[Dict] = None) -> Dict:
    """
    Find optimal annual Roth conversion amount between retirement and RMD age.
    Goal: fill the 22% bracket each year to minimize lifetime taxes.

    life_events/surplus_allocations: threaded through to
    run_retirement_projection below for the starting pretax/roth balances
    only; both default to None/no-op."""
    inflation    = inputs["inflation_rate"]
    post_ret     = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]
    jason_age    = inputs["jason_age"]
    justin_age   = inputs["justin_age"]
    pension_annual = pension_for_age(inputs, ret_age)
    jason_ss     = inputs.get("jason_social_security" if ss_timing == "early" else "jason_ss_delayed", 0)
    justin_ss    = inputs.get("justin_social_security", 0)
    jason_ss_age = 62 if ss_timing == "early" else 67
    # Read the spouse's own claiming age instead of hardcoding 67 — and,
    # below, compare it against the spouse's own current age rather than
    # Jason's `age` directly (external audit 2026-09-06, same bug as
    # elsewhere in this file).
    justin_ss_age = inputs.get("justin_ss_age", JUSTIN_SPOUSAL_AGE)

    # Derived from the canonical 2026 MFJ bracket table in
    # retirement_tools_engine.py instead of a local hardcoded copy — this
    # used to be a stale 2024 figure ($201,050/$29,200) that had drifted out
    # of sync with every other tax calc in the app.
    from retirement_tools_engine import ORDINARY_BRACKETS_MFJ_2026, STD_DEDUCTION_MFJ_2026
    BRACKET_TOP_22  = next(cap for rate, cap in ORDINARY_BRACKETS_MFJ_2026 if rate == 0.22)
    STD_DEDUCTION   = STD_DEDUCTION_MFJ_2026
    TAX_BRACKET_22  = 0.22
    TAX_BRACKET_24  = 0.24
    # SECURE Act 2.0: 73 or 75 depending on birth year, not a fixed 73 —
    # caught by external audit 2026-09-05.
    RMD_START_AGE   = rmd_start_age(jason_age)

    from projection_engine import run_retirement_projection
    _proj = run_retirement_projection(inputs, accounts, ret_ages=[ret_age], life_events=life_events,
                                       surplus_allocations=surplus_allocations)
    _s = next(s for s in _proj["scenarios"] if s["label"] == f"age_{ret_age}_{ss_timing}")

    years_to_ret   = max(0, ret_age - jason_age)
    pretax_at_ret  = _s["pretax_at_retirement"]
    roth_at_ret    = _s["roth_at_retirement"]
    taxable_at_ret = _s["taxable_at_retirement"]

    schedule = []
    pretax  = pretax_at_ret
    roth    = roth_at_ret
    taxable = taxable_at_ret

    conversion_years = RMD_START_AGE - ret_age

    for yr in range(conversion_years):
        age = ret_age + yr

        # Income this year (portfolio draw + pension + SS if active)
        income_need   = income_today * ((1 + inflation) ** years_to_ret)
        year_pen      = pension_annual
        year_jss      = jason_ss * ((1+inflation)**max(0,age-jason_ss_age)) if age >= jason_ss_age else 0
        justin_age_this_year = age - (jason_age - justin_age)
        year_uss      = justin_ss * ((1+inflation)**max(0,justin_age_this_year-justin_ss_age)) if justin_age_this_year >= justin_ss_age else 0
        guaranteed    = year_pen + year_jss + year_uss
        portfolio_draw = max(0, income_need - guaranteed)

        # Spending draws from taxable brokerage before pretax — same
        # preference order as the household's actual withdrawal waterfall
        # elsewhere in this app (run_retirement_projection: taxable first,
        # pretax next). This used to assume 100% of portfolio_draw came
        # from pretax regardless of any taxable/brokerage balance, so a
        # household with real brokerage assets got identical conversion
        # room whether or not those assets existed (external audit
        # 2026-09-07) — spending funded from taxable isn't ordinary
        # income, so it shouldn't eat into the 22%-bracket room being
        # measured for actual Roth conversions.
        taxable_draw = min(portfolio_draw, taxable)
        pretax_draw  = portfolio_draw - taxable_draw

        # Taxable income before conversion
        # Simplified: pension + SS (85% includable) + the pretax-funded
        # portion of the portfolio draw (taxable-funded spending excluded
        # — see above)
        ss_taxable    = (year_jss + year_uss) * 0.85
        base_taxable  = year_pen + ss_taxable + pretax_draw - STD_DEDUCTION

        # Room in 22% bracket
        room_in_22 = max(0, BRACKET_TOP_22 - base_taxable)

        # Optimal conversion = fill 22% bracket
        optimal_conversion = min(room_in_22, pretax)

        # Tax cost of conversion
        tax_cost = optimal_conversion * TAX_BRACKET_22

        # Project balances
        pretax_after   = max(0, (pretax - pretax_draw - optimal_conversion) * (1 + post_ret))
        roth_after     = (roth + optimal_conversion - max(0, pretax_draw - max(0, pretax - optimal_conversion))) * (1 + post_ret)
        taxable_after  = max(0, (taxable - taxable_draw) * (1 + post_ret))

        yrs_to_rmd   = max(0, RMD_START_AGE - age)
        roth_fv_73   = optimal_conversion * ((1 + post_ret) ** yrs_to_rmd)
        tax_avoided  = roth_fv_73 * 0.24  # 24% bracket at RMD age
        net_benefit  = tax_avoided - tax_cost

        schedule.append({
            "age":                age,
            "year":               2026 + years_to_ret + yr,
            "pretax_balance":     round(pretax),
            "roth_balance":       round(roth),
            "taxable_balance":    round(taxable),
            "base_taxable_income": round(base_taxable),
            "room_in_22_bracket": round(room_in_22),
            "optimal_conversion": round(optimal_conversion),
            "tax_cost":           round(tax_cost),
            "roth_fv_at_73":      round(roth_fv_73),
            "tax_avoided_at_73":  round(tax_avoided),
            "net_benefit":        round(net_benefit),
            "pretax_after":       round(pretax_after),
            "roth_after":         round(roth_after),
            "taxable_after":      round(taxable_after),
        })

        pretax  = pretax_after
        roth    = roth_after
        taxable = taxable_after

    total_conversions  = sum(s["optimal_conversion"] for s in schedule)
    total_tax_cost     = sum(s["tax_cost"] for s in schedule)
    estimated_rmd_base = _rmd(pretax_at_ret * ((1+post_ret)**conversion_years), RMD_START_AGE, RMD_START_AGE)

    total_tax_avoided = sum(s["tax_avoided_at_73"] for s in schedule)
    net_lifetime_benefit = total_tax_avoided - total_tax_cost

    return {
        "schedule":               schedule,
        "total_conversions":      round(total_conversions),
        "total_tax_cost":         round(total_tax_cost),
        "total_tax_avoided":      round(total_tax_avoided),
        "net_lifetime_benefit":   round(net_lifetime_benefit),
        "estimated_rmd_without_conversions": round(estimated_rmd_base),
        "estimated_rmd_with_conversions":    round(_rmd(pretax, RMD_START_AGE, RMD_START_AGE)),
        "pretax_at_rmd_age_no_conversion":   round(pretax_at_ret * ((1+post_ret)**conversion_years)),
        "pretax_at_rmd_age_with_conversion": round(pretax),
        "roth_at_rmd_age_with_conversion":   round(roth),
        "conversion_years":       conversion_years,
        "ret_age":                ret_age,
        "rmd_start_age":          RMD_START_AGE,
    }

def run_tax_efficiency_simulation(inputs: Dict, accounts: List[Dict], ret_age: int = 60, ss_timing: str = "early",
                                   life_events: List[Dict] = None,
                                   surplus_allocations: List[Dict] = None) -> Dict:
    """
    Run 1000 market scenarios for 3 draw order strategies.
    Simplified tax: flat 22% on pretax withdrawals, 0% on Roth, 15% on taxable gains.

    life_events/surplus_allocations: threaded through to
    run_retirement_projection below for the starting bucket balances only;
    both default to None/no-op."""
    random.seed(42)

    jason_age    = inputs["jason_age"]
    justin_age   = inputs["justin_age"]
    inflation    = inputs["inflation_rate"]
    post_ret     = inputs["expected_return_post_retirement"]
    income_today = inputs["retirement_income_today_dollars"]
    healthcare_pre  = inputs.get("healthcare_pre_medicare", 0)
    healthcare_post = inputs.get("healthcare_post_medicare", 0)

    pension_annual = pension_for_age(inputs, ret_age)
    jason_ss_early  = inputs.get("jason_social_security", JASON_SS_EARLY)
    jason_ss_delayed = inputs.get("jason_ss_delayed", JASON_SS_DELAYED)
    jason_ss_annual = jason_ss_early if ss_timing == "early" else jason_ss_delayed
    jason_ss_age    = 62 if ss_timing == "early" else 67
    justin_ss       = inputs.get("justin_social_security", JUSTIN_SPOUSAL_ANNUAL)
    justin_ss_age   = inputs.get("justin_ss_age", JUSTIN_SPOUSAL_AGE)

    from projection_engine import run_retirement_projection
    _proj = run_retirement_projection(inputs, accounts, ret_ages=[ret_age], life_events=life_events,
                                       surplus_allocations=surplus_allocations)
    _s = next(s for s in _proj["scenarios"] if s["label"] == f"age_{ret_age}_{ss_timing}")
    pretax_start  = _s["pretax_at_retirement"]
    roth_start    = _s["roth_at_retirement"]
    taxable_start = _s["taxable_at_retirement"]
    hsa_start     = _s["hsa_at_retirement"]

    end_age = max(ret_age + 1, min(110, int(inputs.get("retirement_end_age") or 99)))
    retire_yrs = end_age - ret_age
    N = 1000
    TAX_PRETAX   = 0.22
    TAX_TAXABLE  = 0.15
    TAX_ROTH     = 0.00

    all_returns = [[random.gauss(post_ret, PORT_STD) for _ in range(retire_yrs)] for _ in range(N)]
    _rmd_start = rmd_start_age(jason_age)

    def run_strategy(strategy):
        """strategy: 'taxable_first', 'roth_first', 'optimal'"""
        total_taxes = []
        final_balances = []

        for returns in all_returns:
            pretax  = pretax_start
            roth    = roth_start
            taxable = taxable_start
            hsa     = hsa_start
            lifetime_tax = 0

            for yr in range(retire_yrs):
                age = ret_age + yr
                ret = returns[yr]
                hc  = healthcare_pre if age < 65 else healthcare_post
                year_need  = income_today * ((1+inflation)**yr) + hc * ((1+inflation)**yr)
                year_pen   = pension_annual
                year_jss   = jason_ss_annual * ((1+inflation)**max(0,age-jason_ss_age)) if age >= jason_ss_age else 0
                justin_age_this_year = age - (jason_age - justin_age)
                year_uss   = justin_ss * ((1+inflation)**max(0,justin_age_this_year-justin_ss_age)) if justin_age_this_year >= justin_ss_age else 0
                guaranteed = year_pen + year_jss + year_uss
                net_need   = max(0, year_need - guaranteed)

                # RMD — must take regardless of strategy
                rmd = _rmd(pretax, age, _rmd_start)
                tax_this_year = 0
                remaining = net_need

                # Every taxed draw below is grossed up so its AFTER-TAX
                # proceeds (not the gross withdrawal) cover `remaining` —
                # otherwise the tax accumulated in tax_this_year/lifetime_tax
                # is purely a reported number with no funding source: the
                # bucket only ever shrinks by the net spending need, so
                # final_balances/success_rate come out as if every
                # withdrawal had been tax-free while still claiming a real
                # lifetime tax bill was paid (external audit 2026-09-07,
                # reproduced with a $1M IRA / $100K spend / 1yr / 0% growth
                # — $22K "tax paid" alongside a $900K ending balance that
                # never actually paid for it). Matches
                # run_retirement_projection's existing gross-up pattern.
                if rmd > 0:
                    actual_rmd = min(rmd, pretax)
                    pretax -= actual_rmd
                    rmd_tax = actual_rmd * TAX_PRETAX
                    tax_this_year += rmd_tax
                    after_tax_rmd = actual_rmd - rmd_tax
                    if after_tax_rmd <= remaining:
                        remaining -= after_tax_rmd
                    else:
                        taxable += after_tax_rmd - remaining
                        remaining = 0

                if strategy == 'taxable_first':
                    if remaining > 0 and taxable > 0:
                        gross = remaining / (1 - TAX_TAXABLE)
                        draw = min(gross, taxable); taxable -= draw
                        tax = draw * TAX_TAXABLE; tax_this_year += tax; remaining -= (draw - tax)
                    if remaining > 0 and pretax > 0:
                        gross = remaining / (1 - TAX_PRETAX)
                        draw = min(gross, pretax); pretax -= draw
                        tax = draw * TAX_PRETAX; tax_this_year += tax; remaining -= (draw - tax)
                    if remaining > 0 and hsa > 0:
                        draw = min(remaining, hsa); hsa -= draw; remaining -= draw
                    if remaining > 0 and roth > 0:
                        draw = min(remaining, roth); roth -= draw; remaining -= draw

                elif strategy == 'roth_first':
                    if remaining > 0 and roth > 0:
                        draw = min(remaining, roth); roth -= draw; remaining -= draw
                    if remaining > 0 and taxable > 0:
                        gross = remaining / (1 - TAX_TAXABLE)
                        draw = min(gross, taxable); taxable -= draw
                        tax = draw * TAX_TAXABLE; tax_this_year += tax; remaining -= (draw - tax)
                    if remaining > 0 and pretax > 0:
                        gross = remaining / (1 - TAX_PRETAX)
                        draw = min(gross, pretax); pretax -= draw
                        tax = draw * TAX_PRETAX; tax_this_year += tax; remaining -= (draw - tax)
                    if remaining > 0 and hsa > 0:
                        draw = min(remaining, hsa); hsa -= draw; remaining -= draw

                else:  # optimal — fill 22% bracket from pretax, rest from taxable/roth
                    # Draw from taxable first up to capital gains threshold.
                    # 0% LTCG threshold, MFJ 2026 — matches TaxPlanning.jsx's
                    # LTCG_2026 table. (Chain of stale figures here: $89,250
                    # was 2024, then $96,700 — caught 2026-09-05 — was 2025.)
                    cap_gains_limit = 98900
                    if remaining > 0 and taxable > 0:
                        draw = min(remaining, taxable, cap_gains_limit)
                        taxable -= draw; remaining -= draw
                        # 0% tax if within threshold — no gross-up needed
                    if remaining > 0 and pretax > 0:
                        gross = remaining / (1 - TAX_PRETAX)
                        draw = min(gross, pretax); pretax -= draw
                        tax = draw * TAX_PRETAX; tax_this_year += tax; remaining -= (draw - tax)
                    if remaining > 0 and roth > 0:
                        draw = min(remaining, roth); roth -= draw; remaining -= draw
                    if remaining > 0 and hsa > 0:
                        draw = min(remaining, hsa); hsa -= draw; remaining -= draw

                lifetime_tax += tax_this_year
                pretax  = max(0, pretax  * (1 + ret))
                roth    = max(0, roth    * (1 + ret))
                taxable = max(0, taxable * (1 + ret))
                hsa     = max(0, hsa     * (1 + ret))

            total_taxes.append(round(lifetime_tax))
            final_balances.append(round(pretax + roth + taxable + hsa))

        taxes_sorted = sorted(total_taxes)
        bals_sorted  = sorted(final_balances)
        return {
            "median_lifetime_tax":    taxes_sorted[N//2],
            "p10_lifetime_tax":       taxes_sorted[int(N*0.10)],
            "p90_lifetime_tax":       taxes_sorted[int(N*0.90)],
            "median_final_balance":   bals_sorted[N//2],
            "success_rate":           round(sum(1 for b in final_balances if b > 0) / N * 100, 1),
        }

    taxable_first = run_strategy('taxable_first')
    roth_first    = run_strategy('roth_first')
    optimal       = run_strategy('optimal')

    best_tax = min(taxable_first["median_lifetime_tax"],
                   roth_first["median_lifetime_tax"],
                   optimal["median_lifetime_tax"])

    return {
        "retirement_age": ret_age,
        "retirement_end_age": end_age,
        "ss_timing":      ss_timing,
        "strategies": {
            "taxable_first": {**taxable_first, "label": "Taxable First", "description": "Draw taxable → pretax → Roth last"},
            "roth_first":    {**roth_first,    "label": "Roth First",    "description": "Draw Roth → taxable → pretax last"},
            "optimal":       {**optimal,        "label": "Optimal",       "description": "Fill 0% cap gains bracket, then pretax, Roth as buffer"},
        },
        "best_strategy_tax": best_tax,
        "simulations": N,
    }


def run_contribution_sensitivity(inputs: Dict, accounts: List[Dict], ret_age: int = 60,
                                  life_events: List[Dict] = None,
                                  surplus_allocations: List[Dict] = None) -> Dict:
    """
    Compare retirement outcomes at different contribution rates for remaining working years.
    Extra contributions above 6% go to Roth. Employer stays fixed at 9%.

    life_events/surplus_allocations: threaded through to the base
    run_retirement_projection call below only, for the base_portfolio/
    base_surplus comparison figures; both default to None/no-op.
    """
    from projection_engine import run_retirement_projection, _fv, _fv_annuity

    jason_age    = inputs["jason_age"]
    salary       = inputs.get("w2_salary", 0)
    pre_ret      = inputs["expected_return_pre_retirement"]
    inflation    = inputs["inflation_rate"]
    emp_pct_base = inputs.get("employee_401k_pct", 0.06)
    er_pct       = inputs.get("employer_401k_pct", 0.09)
    years_to_ret = max(0, ret_age - jason_age)

    # IRS 401(k) elective-deferral limits, 2026 (was a stale 2024 figure,
    # $23,000/$30,500) — verify against the current-year IRS figures
    # annually, same caveat as retirement_tools_engine.py's Roth phase-out
    # thresholds.
    LIMIT_UNDER_50  = 24500
    LIMIT_CATCHUP   = 32500  # age 50+ catch-up (base + $8,000)

    scenarios = []
    catch_up_limit = LIMIT_CATCHUP if jason_age >= 50 else LIMIT_UNDER_50

    contribution_scenarios = [
        # "Current" used to be hardcoded to 0.06 regardless of the
        # household's real employee_401k_pct — with a real rate of, say,
        # 10%, the fixed comparison points at or below 10% (including the
        # mislabeled "Current (6%)" row itself) all showed identical
        # projected portfolios, since the delta math below only ever
        # measured a scenario against this hardcoded 6% baseline, never
        # against what the household actually contributes (external
        # audit 2026-09-07).
        (f"Current ({emp_pct_base*100:.0f}%)", emp_pct_base),
        ("7% employee",      0.07),
        ("8% employee",      0.08),
        ("10% employee",     0.10),
        # salary=0 (not yet entered in Settings, or genuinely retired/no
        # W2 income) used to divide by zero here — a valid $0 shouldn't
        # crash Contribution Sensitivity, and by extension the whole
        # Historical Stress tab, which waits on this endpoint alongside
        # Roth conversion and its own stress-test call via Promise.all
        # and swallows any one of their failures (external audit
        # 2026-09-07). A scenario expressed as "% of salary" is
        # meaningless at $0 salary, so it contributes nothing rather than
        # crashing.
        ("Max catch-up",     min(catch_up_limit / salary, 0.99) if salary > 0 else 0.0),
    ]

    # Base retirement projection for comparison
    base_result = run_retirement_projection(inputs, accounts, ret_ages=[ret_age], life_events=life_events,
                                             surplus_allocations=surplus_allocations)
    timing = inputs.get("_ss_timing", "early")
    base_scenario = next(s for s in base_result["scenarios"] if s["label"] == f"age_{ret_age}_{timing}")
    base_surplus   = base_scenario["projected_surplus"]
    base_portfolio = base_scenario["portfolio_at_retirement"]

    for label, emp_pct in contribution_scenarios:
        annual_employee = min(salary * emp_pct, catch_up_limit)
        annual_employer = salary * er_pct
        annual_total    = annual_employee + annual_employer
        monthly_cost    = (annual_employee - salary * emp_pct_base) / 12
        monthly_spending_cut = max(0, (annual_employee - salary * emp_pct_base) * (1 - 0.32) / 12)

        # Extra (or reduced) Roth contributions vs. the real current rate,
        # compounded to retirement — signed, not clamped to a minimum of 0.
        # A scenario contributing LESS than emp_pct_base is a real,
        # legitimate comparison point (test it against a rate below your
        # own) and must show a negative delta, not an identical-to-current
        # 0 like every other below-current scenario would then also show
        # (external audit 2026-09-07 — "10%, current, and reductions all
        # showed the same portfolio").
        extra_annual    = annual_employee - salary * emp_pct_base
        extra_fv_at_ret = _fv_annuity(extra_annual, pre_ret, years_to_ret)

        # For age 55: contributions stop at retirement (no employer match on extra)
        # For age 60+: contributions continue so full FV applies
        portfolio_delta = round(extra_fv_at_ret)
        surplus_delta   = round(extra_fv_at_ret)

        # Breakeven: how many years of retirement spending does the extra surplus buy
        # (only meaningful for a genuine gain — a reduced-contribution scenario's
        # negative surplus_delta doesn't "buy" years of anything).
        annual_spend = inputs["retirement_income_today_dollars"]
        breakeven_years = surplus_delta / annual_spend if annual_spend > 0 and surplus_delta > 0 else 0

        scenarios.append({
            "label":                 label,
            "employee_pct":          round(emp_pct * 100, 1),
            "annual_employee":       round(annual_employee),
            "annual_employer":       round(annual_employer),
            "annual_total":          round(annual_total),
            "monthly_spending_cut":  round(monthly_spending_cut),
            "portfolio_at_ret":      round(base_portfolio + portfolio_delta),
            "surplus_at_ret":        round(base_surplus + surplus_delta),
            "portfolio_delta":       round(portfolio_delta),
            "surplus_delta":         round(surplus_delta),
            "breakeven_years":       round(breakeven_years, 1),
        })

    return {
        "base_portfolio":  base_portfolio,
        "base_surplus":    base_surplus,
        "years_to_retire": years_to_ret,
        "retirement_age":  ret_age,
        "salary":          salary,
        "scenarios":       scenarios,
    }


def run_survivor_scenario(inputs: Dict, accounts: List[Dict], ret_age: int = 60,
                           deceased: str = "jason", death_age: int = None,
                           survivor_need_factor: float = 0.75,
                           life_events: List[Dict] = None,
                           surplus_allocations: List[Dict] = None) -> Dict:
    """Stress-tests the plan assuming one spouse dies during retirement:
    the deceased's life insurance payout is added to the portfolio, Social
    Security switches to the survivor benefit (the higher of the two, not
    both — that's how SS survivor benefits actually work), pension is
    assumed 100% Joint & Survivor (matching this codebase's existing
    PENSION_100J_S assumption), and income need is scaled down by
    survivor_need_factor to reflect one person's living costs instead of
    two (0.75 is a common financial-planning rule of thumb; adjustable).

    Does NOT fully model the MFJ->Single tax-bracket change on ongoing
    withdrawals — that's a real additional drag this doesn't capture,
    flagged in the recommendation rather than silently baked in as a
    precise number, since integrating it would mean re-deriving the whole
    withdrawal-order/tax engine rather than reusing the baseline
    projection the way everything else here does.

    life_events/surplus_allocations: threaded through to
    run_retirement_projection below for the pre-death baseline portfolio
    only; both default to None/no-op.
    """
    from projection_engine import run_retirement_projection, _pv_annuity

    jason_age  = inputs["jason_age"]
    justin_age = inputs["justin_age"]
    post_ret   = inputs["expected_return_post_retirement"]
    inflation  = inputs["inflation_rate"]
    age_gap    = jason_age - justin_age  # positive: jason is older

    if death_age is None:
        death_age = ret_age + 10

    _proj = run_retirement_projection(inputs, accounts, ret_ages=[ret_age], life_events=life_events,
                                       surplus_allocations=surplus_allocations)
    _scenario = next((s for s in _proj["scenarios"] if s["label"] == f"age_{ret_age}_early"), None)
    if not _scenario:
        return {"has_data": False}

    # death_age is the DECEASED spouse's age — convert to jason_age terms
    # to index into yearly_detail, which is always keyed by jason's age.
    death_jason_age = death_age if deceased == "jason" else death_age + age_gap

    yearly = _scenario["yearly_detail"]
    death_row = next((y for y in yearly if y["jason_age"] >= death_jason_age), None)
    if not death_row:
        return {"has_data": False}
    death_jason_age = death_row["jason_age"]  # snap to an actual modeled year

    # Every other simulation in this file (run_monte_carlo/run_stress_tests/
    # run_swr_analysis/run_tax_efficiency_simulation) reads the household's
    # actual retirement_end_age instead of hardcoding 99 — this function
    # didn't, so a survivor plan was modeled and reported against a
    # mortality age that could be years off from what the rest of the app
    # (and the household's own Settings) uses.
    end_age = max(death_jason_age + 1, min(110, int(inputs.get("retirement_end_age") or 99)))

    if deceased == "jason":
        payout = (inputs.get("jason_life_basic", 0) + inputs.get("jason_life_supplemental", 0)
                  + inputs.get("jason_life_term", 0))
    else:
        payout = (inputs.get("justin_life_ul", 0) + inputs.get("justin_life_whole", 0)
                  + inputs.get("person2_life_employer", 0) + inputs.get("justin_life_term", 0)
                  + inputs.get("justin_life_kids", 0))

    portfolio_at_death   = death_row["portfolio_balance"]
    starting_balance     = portfolio_at_death + payout
    survivor_ss_annual   = max(inputs.get("jason_social_security", 0), inputs.get("justin_social_security", 0))
    pension_annual       = death_row["pension"]  # 100% J&S assumption already baked into this figure
    years_since_ret      = death_row["year"] - (2026 + max(0, ret_age - jason_age))
    income_today         = inputs["retirement_income_today_dollars"]
    income_need_at_death = income_today * ((1 + inflation) ** years_since_ret) * survivor_need_factor
    guaranteed_at_death  = pension_annual + survivor_ss_annual

    schedule = []
    bal = starting_balance
    depleted_age = None
    for i, age in enumerate(range(death_jason_age, end_age)):
        need       = income_need_at_death * ((1 + inflation) ** i)
        guaranteed = guaranteed_at_death * ((1 + inflation) ** i)
        draw       = max(0, need - guaranteed)
        # Catch the edge case where the portfolio is already at (or below)
        # zero going into this year and there's still a real gap to cover —
        # without this check, a starting_balance of 0 never triggers the
        # bal_after<=0-and-bal>0 transition below, so an already-depleted
        # plan would be silently reported as "survives".
        if depleted_age is None and bal <= 0 and draw > 0:
            depleted_age = age
        bal_after  = max(0, (bal - draw) * (1 + post_ret))
        schedule.append({"age": age, "starting_balance": round(bal), "draw": round(draw), "ending_balance": round(bal_after)})
        if bal_after <= 0 and depleted_age is None and bal > 0:
            depleted_age = age
        bal = bal_after

    survives = depleted_age is None

    additional_insurance_needed = 0
    if not survives:
        net_need  = max(0, income_need_at_death - guaranteed_at_death)
        real_rate = ((1 + post_ret) / (1 + inflation) - 1) if post_ret != inflation else 0.0001
        cap_need  = _pv_annuity(net_need, real_rate, end_age - death_jason_age)
        additional_insurance_needed = max(0, round(cap_need - starting_balance))

    if survives:
        recommendation = (
            f"If {deceased} dies at {death_age}, the ${payout:,.0f} life insurance payout plus the portfolio "
            f"(${portfolio_at_death:,.0f} at that point) covers the survivor's needs through age {end_age}, assuming "
            f"living costs drop to {survivor_need_factor*100:.0f}% of the couple's target and Social Security "
            f"switches to the higher of the two benefits. This doesn't account for the tax-bracket jump from "
            f"filing jointly to filing single, which would add real drag on top of this."
        )
    else:
        recommendation = (
            f"If {deceased} dies at {death_age}, the plan runs out around age {depleted_age} for the survivor — "
            f"about ${additional_insurance_needed:,.0f} more life insurance on {deceased} would close that gap "
            f"(today's dollars, before accounting for the MFJ-to-single tax-bracket jump, which would push the "
            f"real number higher)."
        )

    return {
        "has_data": True,
        "deceased": deceased,
        "death_age": death_jason_age if deceased == "jason" else death_age,
        "survivor_end_age": end_age,
        "portfolio_at_death": round(portfolio_at_death),
        "life_insurance_payout": round(payout),
        "starting_balance_after_payout": round(starting_balance),
        "survivor_ss_annual": round(survivor_ss_annual),
        "pension_annual": round(pension_annual),
        "income_need_at_death": round(income_need_at_death),
        "survivor_need_factor": survivor_need_factor,
        "survives": survives,
        "depleted_age": depleted_age,
        "additional_insurance_needed": additional_insurance_needed,
        "recommendation": recommendation,
        "schedule": schedule[::2],
    }
