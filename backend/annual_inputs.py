"""
Shared withdrawal-phase annual-INPUT builder.

Consolidation task (2026-09-07, follow-up to the timeline normalizer in
timeline_engine.py and the withdrawal ledger in annual_engine.py):
every withdrawal-phase consumer (run_retirement_projection, Monte Carlo/
Stress's _run_single, run_swr_analysis, run_roth_conversion_analysis,
run_tax_efficiency_simulation) independently recomputed the same four
things for "what does this withdrawal-loop year `yr` look like on the
income side":

  1. Healthcare cost (healthcare_for_age, already shared -- this module
     just applies inflation on top of it consistently).
  2. Social Security for both spouses -- COLA'd from each person's own
     claim age, in their own age terms, correctly handling a claim age
     already in the past relative to effective_start_age (the exact
     class of bug independent review found three times over: 2026-09-06's
     Justin-age-gap-off-by-N bug, 2026-09-07's past-ret-age timeline bug,
     and this module's own justin_ss default-age bug in
     run_survivor_scenario). One consumer (Monte Carlo/Stress's
     _run_single) had already generalized this correctly to handle
     variable per-year inflation (stress scenarios' inflation_mults) via
     Timeline.pre_start_cola + a cumulative-inflation ratio; the others
     used an algebraically-equivalent-but-only-for-constant-inflation
     shortcut, `(1+inflation)**(age-claim_age)`. This module adopts the
     more general form as the single implementation -- it produces
     bit-identical results to the shortcut whenever inflation is in fact
     constant across the horizon (see
     tests/test_annual_inputs.py::TestConstantInflationMatchesShortcut),
     and correctly handles the variable case the shortcut silently got
     wrong.
  3. Life events landing in this calendar year (already shared via
     _post_retirement_year_effects/_split_life_events in
     projection_engine.py -- this module just calls it at the right
     calendar year for the loop's current `yr`).
  4. Pension is deliberately NOT computed here. Each consumer's pension
     policy (frozen no-COLA in run_retirement_projection; a policy-driven
     annual figure elsewhere) is exactly the kind of "explicit,
     deliberate difference between tools" this consolidation is
     instructed to preserve, not erase -- callers resolve their own
     pension_annual for the year and combine it with this module's
     social_security into whatever "guaranteed_income" figure their own
     withdrawal call expects.

Deliberately does NOT combine healthcare + income into a single
spending_need, for the same reason annual_engine.py's docstring gives
for not owning this: not every consumer wants the same spending-need
shape (SWR wants one undifferentiated figure; the others want
income+healthcare inflated separately, some summed before the
withdrawal call, others exposed per-row in an output table). Callers
combine `income_today_at_start * cum_inflation[yr] + result.healthcare`
(or their own equivalent) themselves; see CALCULATION_CONTRACT.md 2.4.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from timeline_engine import Timeline, healthcare_for_age


@dataclass
class AnnualIncomeInputs:
    """One withdrawal-loop year's income-side inputs, in that year's
    (already-inflated) dollars. `age`/`justin_age`/`calendar_year` are
    reported back so callers building a per-year output row don't need
    to recompute them from `yr`."""
    age: int
    justin_age: int
    calendar_year: int
    healthcare: float
    jason_ss: float
    justin_ss: float
    social_security: float       # jason_ss + justin_ss, for callers that only want the combined figure
    life_event_cash: float       # one-time, signed (positive = windfall/asset sale, lands in taxable)
    life_event_monthly: float    # recurring annualized, signed (positive reduces need, negative increases it)
    justin_gap_income: float     # second-earner gap income this year (2026-09-08) -- see build_annual_income_inputs


def build_annual_income_inputs(
    timeline: Timeline,
    yr: int,
    cum_inflation: List[float],
    inflation: float,
    healthcare_pre_at_start: float,
    healthcare_post_at_start: float,
    jason_ss_annual: float,
    jason_ss_age: float,
    justin_ss_annual: float,
    justin_ss_age: float,
    post_life_events: Optional[List[Dict]] = None,
    post_retirement_year_effects=None,
    justin_gap_years: int = 0,
    justin_gap_income_at_start: float = 0.0,
    salary_growth_pct: float = 0.0,
) -> AnnualIncomeInputs:
    """Build the shared income-side bundle for withdrawal-loop year `yr`.

    healthcare_pre_at_start/healthcare_post_at_start: today's-dollar
    healthcare figures already inflated up to effective_start_age (yr=0)
    dollars -- the same "callers pre-inflate to the loop's starting
    point, this function/cum_inflation handles the rest" convention
    annual_engine.py's spending_need uses. Pass raw today's-dollar
    figures with `1.0` prepended to cum_inflation (i.e. an unshifted
    cum_inflation array) if a consumer has no separate "at start" step.

    jason_ss_annual/justin_ss_annual: nominal (today's-dollar, unshifted)
    annual benefit amounts -- this function applies pre_start_cola AND
    the ongoing cum_inflation ratio itself, unlike the healthcare
    params above, since Social Security's COLA clock starts at the
    person's OWN claim age, not uniformly at effective_start_age.

    jason_ss_age: Jason's own claim age (Jason-age terms).
    justin_ss_age: Justin's own claim age (Justin-age terms -- this
    function converts it to Jason-age terms internally via
    timeline.age_gap, the same conversion every consumer's own inline
    version already applied after the 2026-09-06 age-gap bug fix).

    post_retirement_year_effects: injected to avoid a circular import
    (projection_engine.py owns _post_retirement_year_effects and already
    imports this module's sibling annual_engine.py); pass
    projection_engine._post_retirement_year_effects. Defaults to a
    always-(0, 0) no-op so callers with no life events can omit it.

    justin_gap_years/justin_gap_income_at_start (2026-09-08,
    CALCULATION_CONTRACT.md section 13, backlog item 1): from
    projection_engine.justin_gap_income_inputs() -- see that function's
    own docstring for the full second-earner-gap-income convention (net-
    of-tax approximation, salary_growth_pct wage-growth, not inflation).
    Both default to 0, so a caller that never touches this feature (or a
    household that never fills in justin_ret_age) gets justin_gap_income
    == 0 every year, identical to before this parameter existed.
    salary_growth_pct only matters together with justin_gap_income_at_start
    (grows the gap-year figure at the same wage-growth rate that produced
    it) -- unrelated to any other field this function returns.
    """
    age = timeline.age(yr)
    justin_age_this_year = timeline.justin_age_at(age)
    calendar_year = timeline.calendar_year(yr)

    healthcare_today = healthcare_for_age(age, healthcare_pre_at_start, healthcare_post_at_start)
    healthcare = healthcare_today * cum_inflation[yr]

    def _ss(annual: float, claim_age_jason_years: float, eligible: bool) -> float:
        if not eligible or annual <= 0:
            return 0.0
        idx = timeline.claim_year_index(claim_age_jason_years)
        return (annual * timeline.pre_start_cola(claim_age_jason_years, inflation)
                * (cum_inflation[yr] / cum_inflation[idx]))

    jason_ss = _ss(jason_ss_annual, jason_ss_age, age >= jason_ss_age)
    justin_claim_age_in_jason_years = justin_ss_age + timeline.age_gap
    justin_ss = _ss(justin_ss_annual, justin_claim_age_in_jason_years, justin_age_this_year >= justin_ss_age)

    if post_retirement_year_effects is None:
        life_event_cash, life_event_monthly = 0.0, 0.0
    else:
        life_event_cash, life_event_monthly = post_retirement_year_effects(post_life_events or [], calendar_year)

    justin_gap_income = (
        justin_gap_income_at_start * ((1 + salary_growth_pct) ** yr)
        if yr < justin_gap_years else 0.0
    )

    return AnnualIncomeInputs(
        age=age,
        justin_age=justin_age_this_year,
        calendar_year=calendar_year,
        healthcare=healthcare,
        jason_ss=jason_ss,
        justin_ss=justin_ss,
        social_security=jason_ss + justin_ss,
        life_event_cash=life_event_cash,
        life_event_monthly=life_event_monthly,
        justin_gap_income=justin_gap_income,
    )
