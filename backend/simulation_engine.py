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
    _split_life_events, _post_retirement_year_effects, _post_retirement_asset_sale_events,
    justin_years_to_retire_for, justin_gap_income_inputs,
)
from annual_engine import (AccountState, DEFAULT_ORDER, ROTH_FIRST_ORDER, marginal_bracket_tax_model, no_tax_model,
                           simulate_conversion, simulate_withdrawal_year)
from timeline_engine import build_cumulative_inflation, build_timeline, healthcare_for_age
from annual_inputs import build_annual_income_inputs
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


def _swr_year_step(pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly,
                    rmd, pretax_tax_rate):
    """SWR's own per-year withdrawal step, extracted (consolidation
    follow-up, 2026-09-07, item 3) so it can be parity-tested against
    annual_engine.simulate_withdrawal_year across randomized cases,
    rather than only asserted correct by inspection. Kept as its own
    fast, allocation-free implementation — the same measured performance
    reason as _ordered_draw/_optimal_draw: this loop runs the withdrawal
    step up to ~32x more often than any other consumer (binary search x
    1000 trials x ~40yrs), where the shared engine's per-year dataclass/
    dict allocations stop being free (documented in
    CALCULATION_CONTRACT.md's run_swr_analysis exception).

    Note SWR's own material assumption (CALCULATION_CONTRACT.md 3.5):
    `portfolio_draw` is drawn from the portfolio ON TOP OF guaranteed
    income, which this function never subtracts — SWR answers "how much
    can I safely withdraw from the portfolio alone," a different
    question from every other consumer's "given my itemized spending
    need, do I survive." Guaranteed income only enters this step
    indirectly, via the caller-supplied `pretax_tax_rate` (priced against
    the household's real bracket, which includes guaranteed income).

    Draw order (taxable, pretax, hsa, roth) matches
    annual_engine.DEFAULT_ORDER exactly. Returns (pretax, roth, taxable,
    hsa, remaining) — remaining > 0 means unmet need this year."""
    taxable += event_cash
    remaining = max(0, portfolio_draw - event_monthly) + max(0, -taxable)
    taxable = max(0, taxable) + max(0, event_monthly - portfolio_draw)

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
    # Previously gated on `rmd == 0`, blocking any further pretax
    # withdrawal for the rest of the plan once RMD age was reached —
    # external audit 2026-09-06, same bug as
    # projection_engine.py/_run_single (see their comments). Grossed up
    # for tax like the RMD above — this whole loop used to treat every
    # pretax withdrawal as tax-free (external audit 2026-09-07, same
    # root cause as _run_single).
    if remaining > 0 and pretax > 0:
        pretax, _tax, remaining = _grossed_up_draw(remaining, pretax, pretax_tax_rate)
    if remaining > 0 and hsa > 0:
        draw = min(remaining, hsa); hsa -= draw; remaining -= draw
    if remaining > 0 and roth > 0:
        draw = min(remaining, roth); roth -= draw; remaining -= draw

    return pretax, roth, taxable, hsa, remaining


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
    justin_gap_years: int = 0,
    justin_gap_income_at_start: float = 0.0,
    salary_growth_pct: float = 0.0,
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
    projection's correctly-taxed $888,889). Now uses
    annual_engine.simulate_withdrawal_year — the same shared engine
    run_retirement_projection's waterfall uses — via
    marginal_bracket_tax_model, priced with the module-level
    _pretax_marginal_tax_rate helper. Defaults to 0.0 (added federal-only
    if a caller doesn't pass a state rate) so this is purely additive for
    existing callers.

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

    justin_gap_years/justin_gap_income_at_start/salary_growth_pct
    (2026-09-08, CALCULATION_CONTRACT.md section 13, backlog item 1):
    from projection_engine.justin_gap_income_inputs() — the caller
    (run_monte_carlo/run_stress_tests) computes these once outside the
    N-run loop, same precedent as pension_annual/income_at_ret above.
    All default to 0, so an existing caller that never touches this
    feature sees no change.
    """
    # `timeline` is the single shared source of effective_start_age,
    # retirement_year, end_age/retire_yrs, and age-gap arithmetic —
    # extracted into timeline_engine.py (2026-09-07 consolidation
    # follow-up) after the third independent review found this exact
    # computation duplicated (and, until an earlier fix, WRONG in this
    # copy specifically) across every withdrawal-phase consumer. Mirrors
    # run_retirement_projection's own convention exactly: `ret_age`
    # itself is left untouched below for genuinely age-55-specific
    # POLICY (the bridge-job/kids-at-home branch) — that's a selection,
    # not a timeline, same precedent as pension_annual using raw ret_age
    # via the caller's pension_for_age.
    timeline = build_timeline(jason_age, justin_age, ret_age, retirement_end_age)
    withdrawal_start_age = timeline.effective_start_age
    retire_yrs = timeline.retire_yrs
    retirement_year = timeline.retirement_year

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

    # A true cumulative inflation index (timeline_engine.build_cumulative_
    # inflation) — cum_inflation[k] is the accumulated price-growth factor
    # from the start of retirement through the start of year k
    # (cum_inflation[0] == 1.0, today's dollars). Every inflated figure
    # below used to be computed as (1 + eff_inf)**yr — using THIS year's
    # inflation_mult (which some stress scenarios, e.g. stagflation_1970s,
    # deliberately change partway through the horizon) raised to the
    # power of ALL elapsed years. That retroactively re-derives the
    # entire price history from whatever rate happens to be in effect
    # this year, instead of accumulating it — a rate that drops next year
    # doesn't just slow future growth, it silently erases the compounding
    # already "banked" from earlier, higher-inflation years (external
    # audit 2026-09-07, reproduced: spending step from $100,000 to
    # $112,000 to $106,090 as inflation eased, an actual DECREASE in
    # nominal spending need that should never happen just because the
    # inflation *rate* slowed). Reduces to the exact original
    # (1+inflation)**yr whenever inf_mult is constant across the whole
    # horizon — true for every stress scenario except stagflation_1970s
    # and for ordinary Monte Carlo, so this is a zero-behavior-change fix
    # for the overwhelming majority of runs.
    cum_inflation = build_cumulative_inflation(inflation, retire_yrs, inflation_mults)

    for yr in range(retire_yrs):
        age      = timeline.age(yr)
        ret      = annual_returns[yr] if yr < len(annual_returns) else random.gauss(post_ret, PORT_STD)
        inf_mult = inflation_mults[yr] if inflation_mults and yr < len(inflation_mults) else 1.0
        eff_inf  = inflation * inf_mult
        cum_inf  = cum_inflation[yr]

        # healthcare_pre/healthcare_post used to only get read out of
        # phase_inputs inside the `ret_age == 55` branch below, so every
        # other retirement age (56, 57, 58...) silently modeled $0
        # healthcare cost for the entire retirement -- an in-between-age
        # gap in the same family as the ones already fixed elsewhere in
        # this codebase (see CLAUDE.md). Read unconditionally here instead;
        # the age-55 branch below still owns the bridge-job/kids-at-home
        # phasing, but no longer needs its own separate hc_pre/hc_post
        # copies since it can just reuse these.
        healthcare_pre  = (phase_inputs or {}).get("healthcare_pre", 0) * ((1 + inflation) ** max(0, withdrawal_start_age - jason_age))
        healthcare_post = (phase_inputs or {}).get("healthcare_post", 0) * ((1 + inflation) ** max(0, withdrawal_start_age - jason_age))
        # hc_this_year is only meaningful inside the ret_age==55 bridge/kids
        # branch below (which overwrites it); the non-55 path uses
        # income.healthcare from the shared builder instead. Left unset here
        # on purpose -- don't reintroduce the unconditional healthcare_for_age
        # call this hot loop (N=1000 Monte Carlo trials x retire_yrs) used to
        # pay for on every non-55 iteration with no observable effect.

        # Shared annual-input builder (consolidation, 2026-09-07): Social
        # Security (COLA'd from each spouse's own claim age, including
        # the pre-loop-start leg) and signed life-event offsets,
        # generalized from this exact function's own formulas below into
        # annual_inputs.py so run_retirement_projection and every other
        # consumer share it instead of five independent copies. Healthcare
        # is consolidated too, EXCEPT inside the ret_age==55 bridge/kids
        # branch just below, which is a genuinely different, deliberate
        # policy this consolidation preserves rather than erasing.
        income = build_annual_income_inputs(
            timeline, yr, cum_inflation, inflation,
            healthcare_pre_at_start=healthcare_pre, healthcare_post_at_start=healthcare_post,
            jason_ss_annual=jason_ss_annual, jason_ss_age=jason_ss_age,
            justin_ss_annual=justin_ss_annual, justin_ss_age=justin_ss_age,
            post_life_events=post_life_events, post_retirement_year_effects=_post_retirement_year_effects,
            justin_gap_years=justin_gap_years, justin_gap_income_at_start=justin_gap_income_at_start,
            salary_growth_pct=salary_growth_pct,
        )

        if phase_inputs and ret_age == 55:
            bridge_years  = phase_inputs.get("bridge_years", 0)
            kids_years    = phase_inputs.get("kids_years", 0)
            kids_cost     = phase_inputs.get("kids_annual_cost", 0) * ((1 + inflation) ** max(0, withdrawal_start_age - jason_age))
            bridge_income = phase_inputs.get("bridge_income", 0) * ((1 + inflation) ** max(0, withdrawal_start_age - jason_age))
            hc_kids       = phase_inputs.get("healthcare_kids", 0) * ((1 + inflation) ** max(0, withdrawal_start_age - jason_age))
            if yr < bridge_years:
                hc_this_year = 0
                year_need = max(0, income_at_ret*cum_inf + kids_cost*cum_inf - bridge_income*cum_inf)
            elif yr < kids_years and age < 65:
                hc_this_year = hc_kids
                year_need = income_at_ret*cum_inf + kids_cost*cum_inf + hc_kids*cum_inf
            elif age < 65:
                hc_this_year = healthcare_pre
                year_need = income_at_ret*cum_inf + healthcare_pre*cum_inf
            else:
                hc_this_year = healthcare_post
                year_need = income_at_ret*cum_inf + healthcare_post*cum_inf
        else:
            year_need = income_at_ret * cum_inf + income.healthcare

        # Life events active in the withdrawal phase — computed above via
        # the shared builder (same _post_retirement_year_effects call
        # this function used inline before).
        calendar_year = retirement_year + yr
        life_event_cash, life_event_monthly = income.life_event_cash, income.life_event_monthly
        year_need -= life_event_monthly

        # Second-earner gap income (2026-09-08, CALCULATION_CONTRACT.md
        # section 13, backlog item 1) — computed by the shared builder
        # above from the justin_gap_years/justin_gap_income_at_start
        # params this function's own callers pass in. 0 by default, so
        # an existing caller that never touches this feature sees no
        # change.
        year_need -= income.justin_gap_income

        # SS COLA — computed above via the shared builder (same formula
        # this function's own comment/history documents: COLA relative to
        # each person's OWN claim year, cumulative-inflation-accurate
        # under variable per-trial inflation_mults, with the pre-loop-
        # start leg for a claim that already happened before
        # effective_start_age).
        year_pen  = pension_annual  # frozen pension, no COLA
        year_jss  = income.jason_ss
        year_uss  = income.justin_ss
        fixed     = year_pen + year_jss + year_uss

        # RMD
        rmd = _rmd(pretax, age, _rmd_start)
        pretax_tax_rate = _pretax_marginal_tax_rate(year_pen, year_jss, year_uss, rmd, state_tax_rate)

        # Shared withdrawal-phase step (annual_engine.simulate_withdrawal_year)
        # — same RMD -> taxable -> grossed-up pretax -> HSA -> Roth order,
        # growth applied last, as run_retirement_projection's waterfall.
        # Further pretax withdrawal used to be gated on `rmd == 0`, blocking
        # any additional draw for the rest of the plan once RMD age was
        # reached even with plenty of pretax balance and a large unmet
        # need left — external audit 2026-09-06, same bug as
        # projection_engine.py's withdrawal waterfall (see its comment).
        # RMD tax and further pretax draws used to be entirely untaxed here,
        # unlike run_retirement_projection's own waterfall (external audit
        # 2026-09-07) — both fixes now live once, in the shared engine.
        year_result = simulate_withdrawal_year(
            opening=AccountState(pretax=pretax, roth=roth, taxable=taxable, hsa=hsa),
            spending_need=year_need,
            guaranteed_income=fixed,
            life_event_cash=life_event_cash,
            rmd_amount=rmd,
            tax_model=marginal_bracket_tax_model(pretax_rate=pretax_tax_rate, taxable_rate=0.0),
            growth_rate=ret,
            order=DEFAULT_ORDER,
        )
        if year_result.unmet_need > 0:
            any_unmet_need = True

        pretax  = year_result.closing.pretax
        roth    = year_result.closing.roth
        taxable = year_result.closing.taxable
        hsa     = year_result.closing.hsa

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

    # timeline_engine.build_timeline: the single shared source of
    # effective_start_age/end_age/retire_yrs, used by every withdrawal-
    # phase consumer (consolidation follow-up, 2026-09-07) — a household
    # selecting an already-past ret_age must simulate forward from its
    # actual current age, not re-run the years already behind it.
    timeline = build_timeline(jason_age, justin_age, ret_age, inputs.get("retirement_end_age"))
    withdrawal_start_age = timeline.effective_start_age
    end_age = timeline.end_age
    retire_yrs = timeline.retire_yrs
    N          = 1000
    _rmd_start = rmd_start_age(jason_age)

    # Pre-generate random returns for reproducibility
    all_returns = [[random.gauss(post_ret, PORT_STD) for _ in range(retire_yrs)] for _ in range(N)]

    retirement_year = CURRENT_YEAR + years_to_ret
    _, post_events = _split_life_events(life_events, retirement_year)
    post_events = post_events + _post_retirement_asset_sale_events(inputs, jason_age, timeline.effective_start_age)

    def success_at_withdrawal(annual_withdrawal_today):
        """How many of N simulations survive with this portfolio withdrawal?"""
        successes = 0
        for returns in all_returns:
            pretax  = pretax_at_ret
            roth    = roth_at_ret
            taxable = taxable_at_ret
            hsa     = hsa_at_ret
            survived = True

            # SS formula below is the same one annual_inputs.
            # build_annual_income_inputs() generalizes — deliberately
            # kept inline as a documented performance exception (see
            # docs/CALCULATION_CONTRACT.md, "run_swr_analysis's inner loop"
            # section, and _swr_year_step's own docstring):
            # this loop runs N times per binary-search iteration, and is
            # algebraically equivalent to the shared builder's output for
            # every case this tool exercises (no stress inflation_mults
            # apply here; healthcare is already baked into
            # annual_withdrawal_today by the earlier run_retirement_
            # projection call rather than computed per-year here).
            # test_annual_inputs.py's TestPerformanceExceptionParity
            # verifies this equivalence directly against the builder.
            for yr in range(retire_yrs):
                age = timeline.age(yr)
                ret = returns[yr]

                # Guaranteed income this year
                year_pen = pension_annual
                year_jss = jason_ss_annual * ((1+inflation)**max(0,age-jason_ss_age)) if age >= jason_ss_age else 0
                # justin_ss_age is Justin's own claiming age — compare it to
                # Justin's own current age (age offset by the couple's age
                # gap), not Jason's `age` directly (external audit
                # 2026-09-06, same bug fixed in _run_single/
                # run_retirement_projection).
                justin_age_this_year = timeline.justin_age_at(age)
                year_uss = justin_ss * ((1+inflation)**max(0,justin_age_this_year-justin_ss_age)) if justin_age_this_year >= justin_ss_age else 0
                guaranteed = year_pen + year_jss + year_uss

                # Portfolio withdrawal needed (inflation-adjusted, on top of guaranteed)
                portfolio_draw = annual_withdrawal_today * ((1+inflation)**yr)

                # RMD
                rmd = _rmd(pretax, age, _rmd_start)
                event_cash, event_monthly = _post_retirement_year_effects(post_events, retirement_year + yr)
                pretax_tax_rate = _pretax_marginal_tax_rate(year_pen, year_jss, year_uss, rmd,
                                                              inputs.get("state_income_tax_rate", 0))
                pretax, roth, taxable, hsa, remaining = _swr_year_step(
                    pretax, roth, taxable, hsa, portfolio_draw, event_cash, event_monthly,
                    rmd, pretax_tax_rate)

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

    # Binary search for the withdrawal amount that hits target_success.
    # The upper bound used to be a hardcoded portfolio * 0.15 with no
    # expansion — if the TRUE safe withdrawal rate was actually higher
    # than 15% (plausible for a short horizon, strong guaranteed income,
    # or event cash covering most of retirement), the search just
    # converged to that fixed ceiling and silently reported 15% as "the"
    # safe rate instead of the real, higher number (external audit
    # 2026-09-07, reproduced: $1M brokerage, one modeled retirement year,
    # no taxes/returns/inflation -> true safe spend is most of the $1M,
    # not the $150K the old fixed cap reported). Expand hi by doubling
    # until it actually fails target_success (bounded so this can't loop
    # forever), then binary-search within that real bracket.
    lo, hi = 0, portfolio * 0.15 if portfolio > 0 else 0
    hit_search_limit = False
    if portfolio > 0:
        expansions = 0
        while success_at_withdrawal(hi) >= target_success and expansions < 12:
            lo = hi
            hi *= 2
            expansions += 1
        hit_search_limit = expansions >= 12
    for _ in range(20):
        mid = (lo + hi) / 2
        rate = success_at_withdrawal(mid)
        if rate >= target_success:
            lo = mid
        else:
            hi = mid

    safe_withdrawal = lo
    safe_withdrawal_rate = safe_withdrawal / portfolio if portfolio > 0 else 0

    # Guaranteed income steady-state (once all income sources active).
    # justin_ss_age is JUSTIN's own claiming age — comparing it against
    # ret_age (Jason's retirement age) directly, with no adjustment for
    # the couple's age gap, is the same class of bug already fixed in the
    # withdrawal loop just above (and in _run_single/
    # run_retirement_projection) — just missed here, in this summary-only
    # block (external audit 2026-09-07, reproduced: primary age 67, spouse
    # age 57, spouse claiming at 62 — 5 years still to wait — reported as
    # "$20K guaranteed income on day one").
    # "Day one" here means the day the withdrawal-phase loop above
    # actually starts (withdrawal_start_age), not the nominal (possibly
    # already-past) ret_age — same fix as the loop itself (independent
    # review, 2026-09-07 second follow-up).
    year_gap          = jason_age - justin_age
    justin_age_at_ret = withdrawal_start_age - year_gap
    years_until_jason_claims  = max(0, jason_ss_age - withdrawal_start_age)
    years_until_justin_claims = max(0, justin_ss_age - justin_age_at_ret)
    years_to_ss    = max(years_until_jason_claims, years_until_justin_claims)  # both active
    # Jason's own age once both SS streams are active — replaces the old
    # ss_start_age = max(jason_ss_age, justin_ss_age), which mixed two
    # different people's raw claim ages together with no age-gap
    # adjustment and wasn't even a meaningful single "age" for the couple.
    ss_start_age   = withdrawal_start_age + years_to_ss
    guaranteed_first_year = (
        pension_annual +
        jason_ss_annual * ((1 + inflation) ** years_to_ss) +
        justin_ss * ((1 + inflation) ** years_to_ss)
    )
    # Also track day-one guaranteed (pension only if retiring before SS)
    guaranteed_day_one = pension_annual
    if withdrawal_start_age >= jason_ss_age:
        guaranteed_day_one += jason_ss_annual
    if justin_age_at_ret >= justin_ss_age:
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
        "hit_search_limit":          hit_search_limit,
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
    # Second-earner gap income (2026-09-08, CALCULATION_CONTRACT.md
    # section 13, backlog item 1) — computed once here, same precedent as
    # pension_annual/income_at_ret below, and passed into every _run_single
    # call in the N-run loop.
    _salary_growth_pct = inputs.get("_salary_growth_pct", 0.0)
    _justin_years_to_retire = justin_years_to_retire_for(inputs, justin_age, years_to_ret)
    justin_gap_years, justin_gap_income_at_start = justin_gap_income_inputs(
        inputs, _justin_years_to_retire, years_to_ret, _salary_growth_pct)
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

    # timeline_engine.build_timeline: same shared source _run_single uses
    # internally — end_age/retire_yrs/the chart's own age labels must
    # anchor to whichever is later, ret_age or the household's actual
    # current age, or a past-ret_age selection reports a wildly wrong
    # number of simulated years and mislabeled ages on the chart even
    # though _run_single's own internal loop is correct.
    timeline = build_timeline(jason_age, justin_age, ret_age, inputs.get("retirement_end_age"))
    withdrawal_start_age = timeline.effective_start_age
    end_age = timeline.end_age
    retire_yrs = timeline.retire_yrs
    N = 1000

    # Withdrawal-phase life events, split once outside the N-run loop —
    # the pre-retirement half was already folded into the bucket values
    # above via run_retirement_projection. retirement_year is already
    # correct unchanged: years_to_ret == withdrawal_start_age - jason_age
    # in both branches (years_to_ret is already max(0, ret_age-jason_age),
    # which equals withdrawal_start_age-jason_age whether or not the
    # clamp binds) -- same value as timeline.retirement_year.
    retirement_year = CURRENT_YEAR + years_to_ret
    _, post_life_events = _split_life_events(life_events, retirement_year)
    post_life_events = post_life_events + _post_retirement_asset_sale_events(inputs, jason_age, timeline.effective_start_age)

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
            justin_gap_years=justin_gap_years,
            justin_gap_income_at_start=justin_gap_income_at_start,
            salary_growth_pct=_salary_growth_pct,
        )
        if survived: successes += 1
        all_balances.append(balances)

    success_rate = round(successes / N * 100, 1)

    # Percentile bands — every 2 years for chart
    ages = list(range(withdrawal_start_age, end_age))
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
            depletion_age = withdrawal_start_age + i
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
    # See run_monte_carlo's identical comment — second-earner gap income
    # (2026-09-08, CALCULATION_CONTRACT.md section 13, backlog item 1),
    # computed once here and passed into every _run_single call below.
    _salary_growth_pct = inputs.get("_salary_growth_pct", 0.0)
    _justin_years_to_retire = justin_years_to_retire_for(inputs, justin_age, years_to_ret)
    justin_gap_years, justin_gap_income_at_start = justin_gap_income_inputs(
        inputs, _justin_years_to_retire, years_to_ret, _salary_growth_pct)
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

    # timeline_engine.build_timeline: same shared source run_monte_carlo
    # uses — end_age/retire_yrs and every chart/depletion-age label below
    # must anchor to whichever is later, ret_age or the household's
    # actual current age.
    timeline = build_timeline(jason_age, justin_age, ret_age, inputs.get("retirement_end_age"))
    withdrawal_start_age = timeline.effective_start_age
    end_age = timeline.end_age
    retire_yrs = timeline.retire_yrs

    # Withdrawal-phase life events, split once — the pre-retirement half
    # is already folded into the bucket values above.
    retirement_year = CURRENT_YEAR + years_to_ret
    _, post_life_events = _split_life_events(life_events, retirement_year)
    post_life_events = post_life_events + _post_retirement_asset_sale_events(inputs, jason_age, timeline.effective_start_age)

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
        justin_gap_years=justin_gap_years,
        justin_gap_income_at_start=justin_gap_income_at_start,
        salary_growth_pct=_salary_growth_pct,
    )

    results = {"base": {
        "label": f"Base Case ({post_ret * 100:g}% every year)",
        "survived": base_survived,
        "final_balance": base_bals[-1],
        "chart": [{"age": withdrawal_start_age+i, "balance": b} for i, b in enumerate(base_bals) if i%2==0],
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
            justin_gap_years=justin_gap_years,
            justin_gap_income_at_start=justin_gap_income_at_start,
            salary_growth_pct=_salary_growth_pct,
        )

        # Find depletion age
        dep_age = end_age
        for i, b in enumerate(bals):
            if b <= 0:
                dep_age = withdrawal_start_age + i
                break

        results[key] = {
            "label": scenario["label"],
            "description": scenario["description"],
            "survived": scen_survived,
            "final_balance": bals[-1],
            "depletion_age": dep_age,
            "lowest_balance": min(bals),
            "lowest_balance_age": withdrawal_start_age + bals.index(min(bals)),
            "chart": [{"age": withdrawal_start_age+i, "balance": b, "base": base_bals[i]}
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
    # Healthcare wasn't modeled in this function's spending need at all —
    # every other withdrawal-phase loop in this file (run_tax_efficiency_
    # simulation, _run_single, run_swr_analysis) adds it, so leaving it
    # out here understated real spending and overstated how much 22%-
    # bracket room was actually available for a genuine conversion
    # (external audit 2026-09-07). Doesn't attempt the age-55 bridge-job/
    # kids-at-home phase modeling _run_single has — that's a separate,
    # larger scope than this fix; the simple pre/post-Medicare split
    # already matches run_tax_efficiency_simulation's own treatment.
    healthcare_pre  = inputs.get("healthcare_pre_medicare", 0)
    healthcare_post = inputs.get("healthcare_post_medicare", 0)
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

    # Second-earner gap income (2026-09-08, CALCULATION_CONTRACT.md
    # section 13, backlog item 1) — see run_monte_carlo's identical
    # comment. Computed once here, passed to both build_annual_income_inputs
    # calls below (with-conversion and without-conversion loops), so the
    # comparison stays apples-to-apples.
    _salary_growth_pct = inputs.get("_salary_growth_pct", 0.0)
    _justin_years_to_retire = justin_years_to_retire_for(inputs, justin_age, years_to_ret)
    justin_gap_years, justin_gap_income_at_start = justin_gap_income_inputs(
        inputs, _justin_years_to_retire, years_to_ret, _salary_growth_pct)

    schedule = []
    pretax  = pretax_at_ret
    roth    = roth_at_ret
    taxable = taxable_at_ret

    # timeline_engine.build_timeline: same shared source every other
    # withdrawal-phase consumer in this file uses — a household selecting
    # an already-past ret_age must start its conversion window from its
    # actual current age, not re-open a window that (nominally) started
    # years ago. retirement_end_age doesn't apply to this function's own
    # horizon (RMD_START_AGE does instead — see conversion_years below),
    # so it's omitted here.
    timeline = build_timeline(jason_age, justin_age, ret_age)
    withdrawal_start_age = timeline.effective_start_age
    conversion_years = RMD_START_AGE - withdrawal_start_age

    # Pre-inflate today's-dollars figures to the retirement start date —
    # the yearly loop below then inflates further by `yr` each year. This
    # used to inflate income_need by years_to_ret ONCE and then reuse that
    # SAME value for every year of the conversion window, effectively
    # freezing spending at the retirement-year figure for the whole
    # horizon instead of continuing to inflate year over year (external
    # audit 2026-09-07 — "inflates to retirement once, then freezes
    # spending for all conversion years").
    income_at_ret        = income_today * ((1 + inflation) ** years_to_ret)
    healthcare_pre_at_ret  = healthcare_pre  * ((1 + inflation) ** years_to_ret)
    healthcare_post_at_ret = healthcare_post * ((1 + inflation) ** years_to_ret)

    # Post-retirement life events and Settings-page asset sales — this
    # loop used to have no way to see either, unlike run_retirement_
    # projection/Monte Carlo/stress/SWR, which all apply them during the
    # withdrawal phase. A $500K sale dated after retirement produced
    # identical Roth-conversion output with or without it (external audit
    # 2026-09-07).
    retirement_year = CURRENT_YEAR + years_to_ret
    _, post_events = _split_life_events(life_events, retirement_year)
    post_events = post_events + _post_retirement_asset_sale_events(inputs, jason_age, timeline.effective_start_age)
    # Shared annual-input builder (consolidation, 2026-09-07): same SS
    # COLA and life-event formulas this function's own comment history
    # documents fixing inline, now sourced from annual_inputs.py instead
    # of a fifth independent copy. No stress inflation_mults apply to
    # this tool, so a flat (mults=None) cum_inflation array is exactly
    # equivalent to the pre-existing `(1+inflation)**yr` shortcut.
    cum_inflation = build_cumulative_inflation(inflation, conversion_years)

    for yr in range(conversion_years):
        age = timeline.age(yr)

        income = build_annual_income_inputs(
            timeline, yr, cum_inflation, inflation,
            healthcare_pre_at_start=healthcare_pre_at_ret, healthcare_post_at_start=healthcare_post_at_ret,
            jason_ss_annual=jason_ss, jason_ss_age=jason_ss_age,
            justin_ss_annual=justin_ss, justin_ss_age=justin_ss_age,
            post_life_events=post_events, post_retirement_year_effects=_post_retirement_year_effects,
            justin_gap_years=justin_gap_years, justin_gap_income_at_start=justin_gap_income_at_start,
            salary_growth_pct=_salary_growth_pct,
        )
        # Income this year (portfolio draw + pension + SS if active)
        income_need   = income_at_ret * ((1 + inflation) ** yr) + income.healthcare
        year_pen      = pension_annual
        year_jss      = income.jason_ss
        year_uss      = income.justin_ss
        guaranteed    = year_pen + year_jss + year_uss

        life_event_cash, life_event_monthly = income.life_event_cash, income.life_event_monthly

        # Migrated onto the shared withdrawal engine (backend/annual_engine.py,
        # calculation-engine consolidation Phase 4). Previously this
        # function counted the pretax-funded portion of spending as
        # taxable income when computing 22%-bracket room, but never
        # actually deducted that tax from any balance — only the
        # conversion's own tax was ever paid. That's the same class of
        # "untaxed withdrawal" bug fixed everywhere else in this file
        # (Monte Carlo/Stress Tests/SWR, external audit 2026-09-07);
        # confirmed materially wrong here too (2026-09-07 consolidation
        # follow-up): a scenario with a modest taxable balance produced a
        # year with $63,284 of self-reported taxable income and $0 of tax
        # paid anywhere. Fixed per explicit product decision (Jason,
        # 2026-09-07) rather than silently: real pretax draws are now
        # taxed at the household's actual marginal rate (same
        # _pretax_marginal_tax_rate helper run_retirement_projection/
        # _run_single use), gross-up included, so numbers WILL move for
        # any household whose taxable brokerage runs dry during the
        # conversion window — that's the fix working as intended, not a
        # regression.
        #
        # Draw order is taxable, then pretax, then Roth as a LAST-RESORT
        # spending fallback — corrected from an earlier version of this
        # migration that dropped Roth from the order entirely on the
        # mistaken claim that "this tool has never modeled Roth spending."
        # It has: the pre-migration code's roth_after formula explicitly
        # spilled any spending shortfall into Roth whenever pretax
        # couldn't cover both the year's own draw and the conversion
        # (`roth + optimal_conversion - max(0, pretax_draw - max(0, pretax
        # - optimal_conversion))`). Dropping Roth from the order silently
        # stopped funding spending once pretax ran out and reported the
        # untouched Roth balance as if the plan were still fully funded —
        # caught by independent review, 2026-09-07 (reproduced: $1M Roth
        # only / $100K spend / 0% growth/inflation/income — the
        # unmigrated tool depletes after 10 years; this migration was
        # reporting $1M untouched at RMD age instead). HSA is still
        # excluded — the original never modeled HSA spending here and
        # this tool has no HSA input at all.
        pretax_tax_rate = _pretax_marginal_tax_rate(year_pen, year_jss, year_uss, 0.0,
                                                      inputs.get("state_income_tax_rate", 0) or 0)
        base_result = simulate_withdrawal_year(
            opening=AccountState(pretax=pretax, roth=roth, taxable=taxable, hsa=0.0),
            # Second-earner gap income (backlog item 1) offsets need
            # directly, same as life_event_monthly.
            spending_need=income_need - life_event_monthly - income.justin_gap_income,
            guaranteed_income=guaranteed,
            life_event_cash=life_event_cash,
            rmd_amount=0.0,
            tax_model=marginal_bracket_tax_model(pretax_rate=pretax_tax_rate, taxable_rate=0.0),
            growth_rate=post_ret,
            order=("taxable", "pretax", "roth"),
        )
        pretax_draw = base_result.draws.get("pretax", 0.0)

        # Taxable income before conversion
        # Simplified: pension + SS (85% includable) + the pretax-funded
        # portion of the portfolio draw (taxable-funded spending excluded
        # — see above)
        ss_taxable    = (year_jss + year_uss) * 0.85
        base_taxable  = year_pen + ss_taxable + pretax_draw - STD_DEDUCTION

        # Room in 22% bracket
        room_in_22 = max(0, BRACKET_TOP_22 - base_taxable)

        # Standard practice: pay the conversion's tax bill from OUTSIDE
        # the IRA (taxable/brokerage), not from the IRA itself — otherwise
        # you're taxed on money that never makes it into Roth at all. Cap
        # the conversion itself by what its own tax bill can actually
        # afford from remaining taxable (post spending-draw, post-growth —
        # simulate_conversion is layered on top of an already-computed
        # withdrawal year, per its own contract), rather than ever
        # leaving a tax bill unfunded.
        max_conversion_affordable = (base_result.closing.taxable / TAX_BRACKET_22) if TAX_BRACKET_22 > 0 else float("inf")

        # Optimal conversion = fill 22% bracket, capped by what's actually
        # affordable (funds available, and a tax bill that can be paid),
        # and by what's actually left in pretax after this year's own
        # spending draw and growth — the original capped this by the
        # YEAR'S OPENING pretax balance instead, which could silently
        # over-convert past what the draw left behind (floored to 0
        # rather than actually capped); using the post-draw balance fixes
        # that as a side effect of the same migration.
        optimal_conversion = min(room_in_22, base_result.closing.pretax, max_conversion_affordable)

        conv_result = simulate_conversion(
            base_result, optimal_conversion,
            tax_model=marginal_bracket_tax_model(pretax_rate=TAX_BRACKET_22, taxable_rate=0.0),
            tax_funding_order=("taxable",),
        )
        tax_cost = conv_result.taxes_paid.get("conversion", 0.0) + conv_result.taxes_paid.get("conversion_shortfall", 0.0)

        pretax_after  = conv_result.closing.pretax
        roth_after    = conv_result.closing.roth
        taxable_after = conv_result.closing.taxable

        # `optimal_conversion` is capped by base_result.closing.pretax,
        # which already reflects THIS year's growth (simulate_conversion
        # layers the conversion on top of an already-grown year, per its
        # own contract) — so the dollar amount landing in Roth already
        # represents its value at the END of year `age` / the START of
        # year `age + 1`. Compounding it forward by `RMD_START_AGE - age`
        # more years double-counts that year's growth (independent
        # review, 2026-09-07 — reproduced: a $100K IRA growing to $110K
        # at 10% and converting at year-end reported $121K/$29,040 tax
        # avoided at RMD age instead of the actual $110K/$26,400). One
        # fewer year of compounding is needed than the naive age
        # difference suggests.
        yrs_to_rmd   = max(0, RMD_START_AGE - age - 1)
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
            # Surfaced rather than silently dropped (independent review,
            # 2026-09-07) — nonzero only if taxable+pretax+Roth together
            # can't fund the year's spending need even after RMD/growth;
            # a schedule row is NOT a fully-funded trajectory if this is
            # nonzero, regardless of how healthy the balances look.
            "unmet_need":         round(base_result.unmet_need),
        })

        pretax  = pretax_after
        roth    = roth_after
        taxable = taxable_after

    total_conversions  = sum(s["optimal_conversion"] for s in schedule)
    total_tax_cost     = sum(s["tax_cost"] for s in schedule)

    # "Without conversions" RMD baseline — used to just compound
    # pretax_at_ret with ZERO withdrawals for the whole conversion
    # window, as if the household spent nothing at all during that time.
    # That's not a fair comparison to the with-conversions path above
    # (which DOES spend down pretax for real living expenses every year)
    # — crediting the no-conversion side with spending nothing made
    # conversions look like they'd caused more of the pretax decline than
    # they actually did, overstating the apparent RMD reduction
    # attributable to converting (external audit 2026-09-07). Re-run the
    # identical spending pattern (same income/guaranteed income/life
    # events), with conversions forced to zero, so only the conversion
    # policy differs between the two paths.
    no_conv_pretax  = pretax_at_ret
    no_conv_taxable = taxable_at_ret
    # Track Roth too, for the same reason as the with-conversions loop
    # above: the two paths are meant to isolate the conversion policy
    # difference, holding everything else (including the spending
    # waterfall's bucket order) identical — leaving Roth out of the
    # baseline while the with-conversions path can fall back to it would
    # make the comparison measure "conversions AND a Roth spending
    # fallback" vs. "neither," not conversions in isolation.
    no_conv_roth = roth_at_ret
    for yr in range(conversion_years):
        age = timeline.age(yr)
        income = build_annual_income_inputs(
            timeline, yr, cum_inflation, inflation,
            healthcare_pre_at_start=healthcare_pre_at_ret, healthcare_post_at_start=healthcare_post_at_ret,
            jason_ss_annual=jason_ss, jason_ss_age=jason_ss_age,
            justin_ss_annual=justin_ss, justin_ss_age=justin_ss_age,
            post_life_events=post_events, post_retirement_year_effects=_post_retirement_year_effects,
            justin_gap_years=justin_gap_years, justin_gap_income_at_start=justin_gap_income_at_start,
            salary_growth_pct=_salary_growth_pct,
        )
        income_need = income_at_ret * ((1 + inflation) ** yr) + income.healthcare
        year_pen = pension_annual
        year_jss = income.jason_ss
        year_uss = income.justin_ss
        guaranteed = year_pen + year_jss + year_uss
        life_event_cash, life_event_monthly = income.life_event_cash, income.life_event_monthly
        # Same shared-engine migration and same tax fix as the with-
        # conversions loop above, applied to the baseline — otherwise the
        # comparison would still be apples-to-oranges (a correctly-taxed
        # "with conversions" path against a still-untaxed "without" one).
        no_conv_pretax_rate = _pretax_marginal_tax_rate(year_pen, year_jss, year_uss, 0.0,
                                                          inputs.get("state_income_tax_rate", 0) or 0)
        no_conv_result = simulate_withdrawal_year(
            opening=AccountState(pretax=no_conv_pretax, roth=no_conv_roth, taxable=no_conv_taxable, hsa=0.0),
            # Second-earner gap income (backlog item 1) offsets need
            # directly, same as the with-conversions loop above.
            spending_need=income_need - life_event_monthly - income.justin_gap_income,
            guaranteed_income=guaranteed,
            life_event_cash=life_event_cash,
            rmd_amount=0.0,
            tax_model=marginal_bracket_tax_model(pretax_rate=no_conv_pretax_rate, taxable_rate=0.0),
            growth_rate=post_ret,
            order=("taxable", "pretax", "roth"),
        )
        no_conv_pretax  = no_conv_result.closing.pretax
        no_conv_taxable = no_conv_result.closing.taxable
        no_conv_roth    = no_conv_result.closing.roth
    estimated_rmd_base = _rmd(no_conv_pretax, RMD_START_AGE, RMD_START_AGE)

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
        # Same fix as estimated_rmd_base above — this used to be pure
        # compounding with no withdrawals subtracted, the identical bug
        # duplicated in a second field.
        "pretax_at_rmd_age_no_conversion":   round(no_conv_pretax),
        "pretax_at_rmd_age_with_conversion": round(pretax),
        "roth_at_rmd_age_with_conversion":   round(roth),
        "conversion_years":       conversion_years,
        "ret_age":                ret_age,
        "rmd_start_age":          RMD_START_AGE,
        "total_unmet_need":       round(sum(s["unmet_need"] for s in schedule)),
        "any_unmet_need":         any(s["unmet_need"] > 0 for s in schedule),
    }

def _cash_available_offsets_need(year_need, guaranteed_income, life_event_cash, life_event_monthly):
    """How much of a year's spending need remains after guaranteed income
    and signed life-event cash (one-time + recurring) are applied, and
    how much surplus (if any) should be swept into taxable as savings —
    extracted from run_tax_efficiency_simulation's per-year setup
    (independent review, 2026-09-07 follow-up) so this exact cash-flow
    arithmetic is a single testable function instead of inline code with
    two DIFFERENT ways to get it wrong depending on the sign of the net
    effect:

    - A negative one-time event large enough to exceed available cash
      must INCREASE net_need, not just get subtracted from a bucket with
      nothing tracking the resulting deficit (the first fix, 2026-09-07).
    - A positive recurring event large enough to exceed the ordinary
      spending need must be BANKED as savings via the surplus branch, not
      discarded by flooring the target at 0 before comparing it to cash
      available (this second fix — the first fix's own
      `max(0, year_need - life_event_monthly)` introduced this).

    `year_need - life_event_monthly` is deliberately NOT floored at 0 —
    matches annual_engine.simulate_withdrawal_year's own convention
    exactly (spending_need is never floored there either; see its
    cash_available >= spending_need branch), which is what lets a large
    enough recurring income correctly produce a negative target and flow
    the entire excess into the surplus branch below.

    Returns (net_need, surplus_credit) — net_need is what's left to draw
    from the withdrawal-order buckets (always >= 0); surplus_credit is
    what to add to taxable (0.0 whenever there's a net_need instead)."""
    cash_available = guaranteed_income + life_event_cash
    spending_target = year_need - life_event_monthly
    if cash_available >= spending_target:
        return 0.0, cash_available - spending_target
    return spending_target - cash_available, 0.0


def _ordered_draw(pretax, roth, taxable, hsa, remaining, order, tax_pretax_rate, tax_taxable_rate):
    """Draw `remaining` need from the four buckets in `order`, taxed/
    grossed-up exactly like annual_engine.simulate_withdrawal_year's own
    per-bucket loop, but as four plain floats in/four plain floats out —
    no dataclass, no dict, no Transfer list. Extracted from
    run_tax_efficiency_simulation's inline taxable_first/roth_first
    strategy blocks (calculation-engine consolidation Phase 4) so the two
    strategies share one order-driven implementation instead of two
    copy-pasted ones, and so a parity test (test_annual_engine_reference.py)
    can prove this gives byte-identical results to the shared engine.

    NOT migrated onto simulate_withdrawal_year itself: measured >2x
    slower at this call volume (1000 trials x ~35 years x 2 of 3
    strategies, timed 2026-09-07) — same class of hot-path exception as
    run_swr_analysis's, documented in CALCULATION_CONTRACT.md. This
    function is the shared-formula compromise: one implementation, zero
    extra allocation, verified equivalent to the engine rather than
    merely assumed so."""
    total_tax = 0.0
    for bucket in order:
        if remaining <= 0:
            break
        if bucket == "pretax":
            bal = pretax
        elif bucket == "roth":
            bal = roth
        elif bucket == "taxable":
            bal = taxable
        else:
            bal = hsa
        if bal <= 0:
            continue
        rate = tax_pretax_rate if bucket == "pretax" else (tax_taxable_rate if bucket == "taxable" else 0.0)
        if rate > 0:
            gross = remaining / (1 - rate) if rate < 1 else remaining
            draw = min(gross, bal)
            tax = draw * rate
            net = draw - tax
        else:
            draw = min(remaining, bal)
            tax = 0.0
            net = draw
        if bucket == "pretax":
            pretax = bal - draw
        elif bucket == "roth":
            roth = bal - draw
        elif bucket == "taxable":
            taxable = bal - draw
        else:
            hsa = bal - draw
        remaining -= net
        total_tax += tax
    return pretax, roth, taxable, hsa, remaining, total_tax


def _optimal_draw(pretax, roth, taxable, hsa, remaining, cap_gains_limit, tax_pretax_rate, tax_taxable_rate):
    """The 'optimal' strategy's own draw policy — genuinely different
    from `_ordered_draw`'s single fixed bucket order, not just a
    performance twin of it: taxable up to `cap_gains_limit` at 0% (the
    LTCG threshold), then pretax/roth/hsa in that order, then any
    taxable remainder ABOVE the threshold at `tax_taxable_rate`. Same
    consolidation-follow-up extraction as `_ordered_draw` (item 4 of the
    2026-09-07 task list: "its taxable-gain threshold policy can remain
    distinct, but it should use the shared ledger conventions for
    funding, taxes, shortfalls, growth, and transfers") — this function
    IS the shared-ledger conventions (gross-up-so-after-tax-proceeds-
    fund-the-need, unmet need never silently dropped), just not a literal
    call into `simulate_withdrawal_year` for the same measured
    performance reason `_ordered_draw` isn't either. Parity-tested
    against a hand-composed 3-step `simulate_withdrawal_year` chain in
    test_tax_efficiency_engine_parity.py, the same way `_ordered_draw`
    is proven equivalent to a single call."""
    total_tax = 0.0
    # Phase 1: taxable up to cap_gains_limit, untaxed.
    if remaining > 0 and taxable > 0:
        draw = min(remaining, taxable, cap_gains_limit)
        taxable -= draw
        remaining -= draw
    # Phase 2: pretax (taxed, grossed-up), then roth, then hsa (both untaxed).
    for bucket, rate in (("pretax", tax_pretax_rate), ("roth", 0.0), ("hsa", 0.0)):
        if remaining <= 0:
            break
        bal = pretax if bucket == "pretax" else (roth if bucket == "roth" else hsa)
        if bal <= 0:
            continue
        if rate > 0:
            gross = remaining / (1 - rate) if rate < 1 else remaining
            draw = min(gross, bal)
            tax = draw * rate
            net = draw - tax
        else:
            draw = min(remaining, bal)
            tax = 0.0
            net = draw
        if bucket == "pretax":
            pretax = bal - draw
        elif bucket == "roth":
            roth = bal - draw
        else:
            hsa = bal - draw
        remaining -= net
        total_tax += tax
    # Phase 3: taxable again, above the 0% threshold, taxed at tax_taxable_rate.
    if remaining > 0 and taxable > 0:
        rate = tax_taxable_rate
        gross = remaining / (1 - rate) if rate < 1 else remaining
        draw = min(gross, taxable)
        tax = draw * rate
        taxable -= draw
        total_tax += tax
        remaining -= (draw - tax)
    return pretax, roth, taxable, hsa, remaining, total_tax


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

    # income_today/healthcare_pre/healthcare_post are today's-dollars
    # estimates — this loop used to inflate them only by `yr` (years INTO
    # retirement), omitting the years BETWEEN today and retirement
    # entirely, unlike run_retirement_projection's income_at_ret (which
    # inflates by years_to_retire before the yearly loop inflates further)
    # and _run_single's identical treatment. A household retiring 10 years
    # from now with 3% inflation should enter retirement with a $134,392
    # target (from $100,000 today), not $100,000 (external audit
    # 2026-09-07). Matches run_roth_conversion_analysis's own
    # years_to_ret_te-style pre-inflation, kept consistent across both.
    years_to_ret_te      = max(0, ret_age - jason_age)
    income_at_ret_te     = income_today * ((1 + inflation) ** years_to_ret_te)
    healthcare_pre_te    = healthcare_pre * ((1 + inflation) ** years_to_ret_te)
    healthcare_post_te   = healthcare_post * ((1 + inflation) ** years_to_ret_te)

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

    # timeline_engine.build_timeline: same shared source every other
    # withdrawal-phase consumer in this file uses — a household selecting
    # an already-past ret_age must simulate forward from its actual
    # current age.
    timeline = build_timeline(jason_age, justin_age, ret_age, inputs.get("retirement_end_age"))
    withdrawal_start_age = timeline.effective_start_age
    end_age = timeline.end_age
    retire_yrs = timeline.retire_yrs
    N = 1000
    TAX_PRETAX   = 0.22
    TAX_TAXABLE  = 0.15
    TAX_ROTH     = 0.00

    all_returns = [[random.gauss(post_ret, PORT_STD) for _ in range(retire_yrs)] for _ in range(N)]
    _rmd_start = rmd_start_age(jason_age)

    # Post-retirement life events and Settings-page asset sales — this
    # loop had no way to see either, unlike run_retirement_projection/
    # Monte Carlo/stress/SWR, which all apply them during the withdrawal
    # phase (external audit 2026-09-07).
    retirement_year_te = CURRENT_YEAR + years_to_ret_te
    _, post_events_te = _split_life_events(life_events, retirement_year_te)
    post_events_te = post_events_te + _post_retirement_asset_sale_events(inputs, jason_age, timeline.effective_start_age)

    def run_strategy(strategy):
        """strategy: 'taxable_first', 'roth_first', 'optimal'"""
        total_taxes = []
        final_balances = []
        unmet_flags = []

        for returns in all_returns:
            pretax  = pretax_start
            roth    = roth_start
            taxable = taxable_start
            hsa     = hsa_start
            lifetime_tax = 0
            any_unmet_need = False

            # SS/healthcare/life-event formulas below are the same ones
            # annual_inputs.build_annual_income_inputs() generalizes —
            # deliberately kept inline rather than migrated, as a
            # documented performance exception matching
            # run_swr_analysis's own precedent (docs/CALCULATION_CONTRACT.md,
            # "run_tax_efficiency_simulation's ordered strategies" section):
            # this loop runs N=1000 * retire_yrs * 3
            # strategies times per call, and both are algebraically
            # equivalent to the shared builder's output for every case
            # this tool exercises (no stress inflation_mults apply here).
            # test_annual_inputs.py's TestPerformanceExceptionParity
            # verifies this equivalence directly against the builder.
            for yr in range(retire_yrs):
                age = timeline.age(yr)
                ret = returns[yr]
                hc  = healthcare_for_age(age, healthcare_pre_te, healthcare_post_te)
                year_need  = income_at_ret_te * ((1+inflation)**yr) + hc * ((1+inflation)**yr)
                year_pen   = pension_annual
                year_jss   = jason_ss_annual * ((1+inflation)**max(0,age-jason_ss_age)) if age >= jason_ss_age else 0
                justin_age_this_year = timeline.justin_age_at(age)
                year_uss   = justin_ss * ((1+inflation)**max(0,justin_age_this_year-justin_ss_age)) if justin_age_this_year >= justin_ss_age else 0
                guaranteed = year_pen + year_jss + year_uss

                calendar_year_te = retirement_year_te + yr
                life_event_cash, life_event_monthly = _post_retirement_year_effects(post_events_te, calendar_year_te)
                # Signed life-event cash must offset (or add to) this
                # year's spending need BEFORE anything is drawn — not get
                # unconditionally credited/debited to taxable while the
                # need calc pretends it doesn't exist. The old
                # "taxable += life_event_cash" version worked out fine for
                # a positive windfall (extra savings on top of an
                # unadjusted draw) but for a negative one-time cost with
                # taxable at or near zero, it drove taxable negative with
                # nothing tracking the resulting deficit — floored to 0 at
                # the end of the year, silently erasing the expense
                # (independent review, 2026-09-07 — reproduced: a $100K
                # one-time expense against a $1M pretax-only household
                # with zero ordinary spending produced $0 tax, 100%
                # success, and a final balance barely below $1M in every
                # strategy, instead of the shared engine's own
                # correctly-taxed ~$888,889). Matches the cash_available-
                # offsets-need convention every other migrated consumer
                # already uses (annual_engine.simulate_withdrawal_year):
                # guaranteed income + life-event cash covers need first: a
                # shortfall increases what must be drawn from the
                # buckets below, a surplus is swept into taxable as
                # savings — never both, never neither.
                # spending_target is NOT floored at 0 — a large enough
                # recurring life_event_monthly can make it negative (income
                # exceeding the ordinary spending need), and that excess
                # must still land in taxable as savings via the surplus
                # branch below. An earlier version of this fix floored it
                # at 0, which silently discarded any recurring income
                # above spending entirely (independent review, 2026-09-07
                # follow-up — reproduced: $1M pretax-only / $0 ordinary
                # spending / $10,000/mo recurring income for 24 months
                # ($240,000 total) ended at ~$980,121, the same as if the
                # $240,000 had never existed). Matches
                # annual_engine.simulate_withdrawal_year's own convention
                # exactly (spending_need is never floored there either —
                # see its cash_available >= spending_need branch).
                net_need, surplus_credit = _cash_available_offsets_need(
                    year_need, guaranteed, life_event_cash, life_event_monthly)
                taxable += surplus_credit

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

                if strategy in ('taxable_first', 'roth_first'):
                    _order = DEFAULT_ORDER if strategy == 'taxable_first' else ROTH_FIRST_ORDER
                    pretax, roth, taxable, hsa, remaining, _tax = _ordered_draw(
                        pretax, roth, taxable, hsa, remaining, _order, TAX_PRETAX, TAX_TAXABLE)
                    tax_this_year += _tax

                else:  # optimal — fill 0% LTCG bracket from taxable, then pretax/roth/hsa
                    # 0% LTCG threshold, MFJ 2026 — matches TaxPlanning.jsx's
                    # LTCG_2026 table. (Chain of stale figures here: $89,250
                    # was 2024, then $96,700 — caught 2026-09-05 — was 2025.)
                    # Extracted to _optimal_draw (consolidation follow-up,
                    # 2026-09-07, item 4) — a distinct draw POLICY from
                    # _ordered_draw's fixed bucket order, but the same
                    # shared-ledger conventions (gross-up, unmet need never
                    # silently dropped — see that function's docstring for
                    # the "$98,900 ever funded, reported 100% success
                    # anyway" bug this already fixed) and the same parity-
                    # tested-against-the-shared-engine standard.
                    cap_gains_limit = 98900
                    pretax, roth, taxable, hsa, remaining, _tax = _optimal_draw(
                        pretax, roth, taxable, hsa, remaining, cap_gains_limit, TAX_PRETAX, TAX_TAXABLE)
                    tax_this_year += _tax

                if remaining > 0:
                    any_unmet_need = True

                lifetime_tax += tax_this_year
                pretax  = max(0, pretax  * (1 + ret))
                roth    = max(0, roth    * (1 + ret))
                taxable = max(0, taxable * (1 + ret))
                hsa     = max(0, hsa     * (1 + ret))

            total_taxes.append(round(lifetime_tax))
            final_balances.append(round(pretax + roth + taxable + hsa))
            unmet_flags.append(any_unmet_need)

        taxes_sorted = sorted(total_taxes)
        bals_sorted  = sorted(final_balances)
        return {
            "median_lifetime_tax":    taxes_sorted[N//2],
            "p10_lifetime_tax":       taxes_sorted[int(N*0.10)],
            "p90_lifetime_tax":       taxes_sorted[int(N*0.90)],
            "median_final_balance":   bals_sorted[N//2],
            # A trial with money left but an unmet spending year in
            # between (the "optimal" strategy's fall-through gap above,
            # before this fix) used to still count as a success — checked
            # only the ending balance, never whether every year's need was
            # actually funded (external audit 2026-09-07, same root cause
            # as _run_single's on_track fix).
            "success_rate":           round(sum(1 for b, unmet in zip(final_balances, unmet_flags) if b > 0 and not unmet) / N * 100, 1),
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

    # Every scenario's own annual_employee is capped at the IRS limit —
    # the reference point for "how much extra is this scenario
    # contributing" must be capped the exact same way, or the "Current"
    # row disagrees with itself: at a high enough salary (e.g. $500K at
    # 10% = $50,000/yr, above the $32,500 catch-up limit), "Current"'s own
    # annual_employee gets capped to $32,500 while the un-capped
    # salary * emp_pct_base ($50,000) was used as the delta reference,
    # producing a nonzero "Current" delta against itself — the base
    # projection's own $1.65M portfolio and "Current"'s reported $1.475M
    # disagreeing with no setting having changed at all (external audit
    # 2026-09-07).
    current_annual_employee = min(salary * emp_pct_base, catch_up_limit)

    for label, emp_pct in contribution_scenarios:
        annual_employee = min(salary * emp_pct, catch_up_limit)
        annual_employer = salary * er_pct
        annual_total    = annual_employee + annual_employer
        monthly_cost    = (annual_employee - current_annual_employee) / 12
        monthly_spending_cut = max(0, (annual_employee - current_annual_employee) * (1 - 0.32) / 12)

        # Extra (or reduced) Roth contributions vs. the real current rate,
        # compounded to retirement — signed, not clamped to a minimum of 0.
        # A scenario contributing LESS than emp_pct_base is a real,
        # legitimate comparison point (test it against a rate below your
        # own) and must show a negative delta, not an identical-to-current
        # 0 like every other below-current scenario would then also show
        # (external audit 2026-09-07 — "10%, current, and reductions all
        # showed the same portfolio").
        extra_annual    = annual_employee - current_annual_employee
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

    # timeline_engine.build_timeline: same shared source every other
    # withdrawal-phase consumer uses. The death_age default below was the
    # second of the two adjacent cases flagged alongside the asset-sale
    # timing fix (independent review, 2026-09-07, third follow-up): "10
    # years into retirement" defaulted to `ret_age + 10`, which for an
    # already-past selected ret_age (e.g. 55 while actually 65 today)
    # could default to a death age at or before the household's REAL
    # current age — effectively "already dead" rather than 10 years from
    # now.
    timeline = build_timeline(jason_age, justin_age, ret_age, inputs.get("retirement_end_age"))
    if death_age is None:
        # Fixed to effective_start_age but computed in JASON's terms
        # unconditionally — wrong for deceased="justin", since the code
        # just below (death_jason_age = death_age if deceased == "jason"
        # else death_age + age_gap) treats `death_age` as already being
        # in the DECEASED person's own age terms whenever deceased !=
        # "jason" (independent review, 2026-09-07, fourth follow-up —
        # reproduced: Jason 65, Justin 55, requested retirement 55,
        # deceased="justin" defaulted to death_age=75, read as "Justin
        # dies at 75," but actually indexed to Jason's age 85 — twenty
        # years from today, not the intended ten. $1M taxable-only, $10K/
        # yr spending, 0% growth/inflation selected a $790,000 baseline
        # instead of the correct $890,000). Default in the SAME person's
        # own age terms the rest of this function already expects:
        # effective_start_age converted to Justin's own age
        # (timeline.justin_age_at) when Justin is the deceased spouse.
        deceased_effective_start_age = (timeline.effective_start_age if deceased == "jason"
                                         else timeline.justin_age_at(timeline.effective_start_age))
        death_age = deceased_effective_start_age + 10

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
    # Total years from TODAY to the death year, not just from retirement
    # to death — the old years_since_ret omitted the years between today
    # and retirement entirely, understating income_need_at_death by
    # exactly the same "missing pre-retirement inflation" bug already
    # fixed elsewhere in this file for run_tax_efficiency_simulation
    # (external audit 2026-09-07).
    years_since_today    = death_row["year"] - CURRENT_YEAR
    income_today         = inputs["retirement_income_today_dollars"]
    income_need_at_death = income_today * ((1 + inflation) ** years_since_today) * survivor_need_factor

    schedule = []
    bal = starting_balance
    depleted_age = None
    # The baseline's death_row["portfolio_balance"] is an END-OF-YEAR
    # figure — it already reflects a full year of BOTH spouses' spending
    # for the death year itself. Starting the survivor's own (reduced)
    # spending pattern at that SAME age double-counted that year's
    # spending: once implicitly (baked into portfolio_at_death), once
    # explicitly (this loop's first iteration). The survivor's distinct
    # spending pattern only actually applies from the year AFTER death
    # (external audit 2026-09-07, reproduced: baseline age 60 ends at
    # $865,608 after spending $134,392; survivor age 60 then spent
    # another $100,000 in the same nominal year).
    for i, age in enumerate(range(death_jason_age + 1, end_age)):
        need       = income_need_at_death * ((1 + inflation) ** (i + 1))
        # Pension is frozen (no COLA) everywhere else in this app — only
        # Social Security gets an annual COLA. guaranteed_at_death used to
        # apply COLA to the combined pension+SS total, inflating the
        # (supposedly frozen) pension portion right along with SS
        # (external audit 2026-09-07).
        guaranteed = pension_annual + survivor_ss_annual * ((1 + inflation) ** (i + 1))
        draw       = max(0, need - guaranteed)
        # Catch the edge case where the portfolio is already at (or below)
        # zero going into this year and there's still a real gap to cover —
        # without this check, a starting_balance of 0 never triggers the
        # bal_after<=0-and-bal>0 transition below, so an already-depleted
        # plan would be silently reported as "survives".
        if depleted_age is None and bal <= 0 and draw > 0:
            depleted_age = age
        # Migrated onto the shared withdrawal engine (backend/annual_engine.py,
        # calculation-engine consolidation Phase 4): a single untaxed
        # "taxable" bucket holding the whole post-payout portfolio, no RMD,
        # no_tax_model() (this function has never modeled the MFJ->single
        # tax-bracket jump — see the recommendation text below). One real
        # behavior change inherited from the shared engine, not previously
        # possible in the hand-rolled version: a year where guaranteed
        # income (pension+SS) exceeds survivor need now sweeps the surplus
        # into the portfolio as savings, same as every other migrated
        # consumer, instead of silently discarding it. Verified via golden
        # diff against the pre-migration implementation across 8 synthetic
        # scenarios (tools/capture_survivor_golden.py) — none of them
        # happen to exercise a surplus year, so none of the golden numbers
        # moved; a real household whose guaranteed income outgrows a
        # reduced survivor need late in retirement will now see a higher
        # ending balance than before.
        result = simulate_withdrawal_year(
            opening=AccountState(taxable=max(0.0, bal)),
            spending_need=need,
            guaranteed_income=guaranteed,
            life_event_cash=0.0,
            rmd_amount=0.0,
            tax_model=no_tax_model(),
            growth_rate=post_ret,
            order=("taxable",),
        )
        bal_after = result.closing.total()
        schedule.append({"age": age, "starting_balance": round(bal), "draw": round(draw), "ending_balance": round(bal_after)})
        if bal_after <= 0 and depleted_age is None and bal > 0:
            depleted_age = age
        bal = bal_after

    survives = depleted_age is None

    additional_insurance_needed = 0
    if not survives:
        # Day-one (pre-COLA) guaranteed figure — same today's-dollars
        # approximation this capitalized-need estimate already made
        # before the death-year-boundary fix above.
        guaranteed_day_one = pension_annual + survivor_ss_annual
        net_need  = max(0, income_need_at_death - guaranteed_day_one)
        real_rate = ((1 + post_ret) / (1 + inflation) - 1) if post_ret != inflation else 0.0001
        # Years remaining is now end_age - (death_jason_age + 1), matching
        # the loop's actual range above (the death year itself is no
        # longer part of the survivor's own spending window).
        cap_need  = _pv_annuity(net_need, real_rate, end_age - death_jason_age - 1)
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
