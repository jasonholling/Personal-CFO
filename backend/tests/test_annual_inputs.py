"""
Reference tests for annual_inputs.build_annual_income_inputs, written
BEFORE any consumer is migrated onto it (per the consolidation task's
explicit instruction: independently calculated reference cases first,
then verify full-year outputs and multi-year balances against them).

Every expected value below is expressed as a closed-form formula written
directly in the test -- either the pre-existing "shortcut" formula every
consumer used inline before this module existed
(`annual * (1+inflation)**(age-claim_age)`), or an independent
cumulative-product loop for the variable-inflation case -- never by
calling into annual_inputs.py's own implementation and comparing it to
itself.
"""

import pytest

from annual_inputs import build_annual_income_inputs
from annual_engine import AccountState, DEFAULT_ORDER, no_tax_model, simulate_withdrawal_year
from projection_engine import _post_retirement_year_effects
from timeline_engine import build_cumulative_inflation, build_timeline, healthcare_for_age


# ── Scenario A: current retirement, unequal spouse ages, an early SS claim ──
# Jason 65, Justin 63 (2-year gap), retiring at 65 (== jason_age, so this
# also covers the ordinary/no-past-selection case as a baseline). Jason
# claimed SS at 62 -- already in the past relative to effective_start_age
# -- and Justin claims at 64 in his own age terms, which lands inside
# the 3-year horizon (his SS turns on mid-projection).

INFLATION_A = 0.02
JASON_SS_ANNUAL_A, JASON_SS_AGE_A = 20000, 62
JUSTIN_SS_ANNUAL_A, JUSTIN_SS_AGE_A = 15000, 64
HC_PRE_A, HC_POST_A = 10000, 8000


def _timeline_a():
    return build_timeline(jason_age=65, justin_age=63, ret_age=65, retirement_end_age=68)


def _shortcut_ss(annual, claim_age, current_age, inflation):
    """The pre-existing per-consumer formula, independently re-expressed
    here rather than imported -- exact whenever inflation is constant,
    which every scenario in this class holds true."""
    return annual * ((1 + inflation) ** max(0, current_age - claim_age)) if current_age >= claim_age else 0.0


class TestAnnualIncomeInputsHandCalculated:
    def test_matches_the_pre_existing_shortcut_formula_year_by_year(self):
        timeline = _timeline_a()
        cum_inflation = build_cumulative_inflation(INFLATION_A, timeline.retire_yrs)
        assert timeline.retire_yrs == 3

        for yr in range(timeline.retire_yrs):
            age = timeline.effective_start_age + yr
            justin_age = age - timeline.age_gap
            result = build_annual_income_inputs(
                timeline, yr, cum_inflation, INFLATION_A,
                healthcare_pre_at_start=HC_PRE_A, healthcare_post_at_start=HC_POST_A,
                jason_ss_annual=JASON_SS_ANNUAL_A, jason_ss_age=JASON_SS_AGE_A,
                justin_ss_annual=JUSTIN_SS_ANNUAL_A, justin_ss_age=JUSTIN_SS_AGE_A,
            )
            expected_hc = healthcare_for_age(age, HC_PRE_A, HC_POST_A) * ((1 + INFLATION_A) ** yr)
            expected_jason_ss = _shortcut_ss(JASON_SS_ANNUAL_A, JASON_SS_AGE_A, age, INFLATION_A)
            expected_justin_ss = _shortcut_ss(JUSTIN_SS_ANNUAL_A, JUSTIN_SS_AGE_A, justin_age, INFLATION_A)

            assert result.age == age
            assert result.justin_age == justin_age
            assert result.healthcare == pytest.approx(expected_hc)
            assert result.jason_ss == pytest.approx(expected_jason_ss)
            assert result.justin_ss == pytest.approx(expected_justin_ss)
            assert result.social_security == pytest.approx(expected_jason_ss + expected_justin_ss)

    def test_justins_ss_is_zero_before_his_own_claim_age_not_jasons(self):
        # yr=0: Jason 65, Justin 63 -- below Justin's own claim age of 64.
        timeline = _timeline_a()
        cum_inflation = build_cumulative_inflation(INFLATION_A, timeline.retire_yrs)
        result = build_annual_income_inputs(
            timeline, 0, cum_inflation, INFLATION_A,
            healthcare_pre_at_start=HC_PRE_A, healthcare_post_at_start=HC_POST_A,
            jason_ss_annual=JASON_SS_ANNUAL_A, jason_ss_age=JASON_SS_AGE_A,
            justin_ss_annual=JUSTIN_SS_ANNUAL_A, justin_ss_age=JUSTIN_SS_AGE_A,
        )
        assert result.justin_ss == 0.0
        assert result.jason_ss > 0.0  # Jason already claimed at 62, well before 65

    def test_jasons_early_claim_gets_full_pre_start_cola_at_the_effective_start(self):
        # yr=0, age 65: 3 years of COLA already accrued since the age-62 claim.
        timeline = _timeline_a()
        cum_inflation = build_cumulative_inflation(INFLATION_A, timeline.retire_yrs)
        result = build_annual_income_inputs(
            timeline, 0, cum_inflation, INFLATION_A,
            healthcare_pre_at_start=HC_PRE_A, healthcare_post_at_start=HC_POST_A,
            jason_ss_annual=JASON_SS_ANNUAL_A, jason_ss_age=JASON_SS_AGE_A,
            justin_ss_annual=JUSTIN_SS_ANNUAL_A, justin_ss_age=JUSTIN_SS_AGE_A,
        )
        assert result.jason_ss == pytest.approx(JASON_SS_ANNUAL_A * (1 + INFLATION_A) ** 3)


# ── Variable per-year inflation (stress-scenario inflation_mults) ──────────
# This is exactly the case the pre-existing per-consumer shortcut got
# wrong (it always compounds at THIS year's flat rate, silently erasing
# whatever different rate applied in earlier years). The reference here
# is an independently written cumulative-product loop, not a closed-form
# power -- because with variable rates there IS no single closed form.

class TestVariableInflationDivergesFromTheOldShortcut:
    def test_variable_inflation_matches_an_independently_accumulated_product_not_the_flat_shortcut(self):
        timeline = build_timeline(jason_age=70, justin_age=70, ret_age=70, retirement_end_age=74)
        assert timeline.retire_yrs == 4
        base_inflation = 0.02
        mults = [1.0, 3.0, 0.5, 1.0]  # a stress spike in year 1, then a lull

        cum_inflation = build_cumulative_inflation(base_inflation, timeline.retire_yrs, mults)
        # Independently accumulated reference (a fresh loop, not calling
        # build_cumulative_inflation's own logic by reference identity --
        # written out here so a bug shared by both would still be caught
        # if the formulas diverged even slightly).
        independent_cum = [1.0]
        for m in mults:
            independent_cum.append(independent_cum[-1] * (1 + base_inflation * m))
        assert cum_inflation == pytest.approx(independent_cum)

        ss_annual, ss_age = 24000, 65  # already claimed before the loop starts (age 70 > 65)
        for yr in range(timeline.retire_yrs):
            result = build_annual_income_inputs(
                timeline, yr, cum_inflation, base_inflation,
                healthcare_pre_at_start=0, healthcare_post_at_start=0,
                jason_ss_annual=ss_annual, jason_ss_age=ss_age,
                justin_ss_annual=0, justin_ss_age=999,
            )
            expected = ss_annual * ((1 + base_inflation) ** (70 - ss_age)) * independent_cum[yr]
            flat_shortcut_wrong_value = ss_annual * ((1 + base_inflation) ** (70 + yr - ss_age))
            assert result.jason_ss == pytest.approx(expected)
            if yr >= 2:
                # mults[0] (yr 0->1) is a no-op 1.0x, so yr=1 alone can't
                # distinguish the two formulas; the year-1->2 stress spike
                # (mults[1]=3.0) only shows up in cum_inflation from yr=2
                # onward. The old flat-rate shortcut ignores it entirely
                # -- demonstrate the two formulas actually disagree here,
                # so this test would fail if build_annual_income_inputs
                # silently regressed to the naive shortcut.
                assert result.jason_ss != pytest.approx(flat_shortcut_wrong_value)


# ── Signed life-event offsets, including a negative (recurring cost) one ──

class TestLifeEventSignedOffsets:
    def test_one_time_and_negative_recurring_offsets_land_in_the_right_years(self):
        timeline = build_timeline(jason_age=65, justin_age=65, ret_age=65, retirement_end_age=68)
        cum_inflation = build_cumulative_inflation(0.0, timeline.retire_yrs)
        events = [{"event_year": timeline.calendar_year(1), "one_time": 5000, "monthly": -500, "duration_months": 0}]

        before = build_annual_income_inputs(
            timeline, 0, cum_inflation, 0.0, 0, 0, 0, 999, 0, 999,
            post_life_events=events, post_retirement_year_effects=_post_retirement_year_effects,
        )
        during = build_annual_income_inputs(
            timeline, 1, cum_inflation, 0.0, 0, 0, 0, 999, 0, 999,
            post_life_events=events, post_retirement_year_effects=_post_retirement_year_effects,
        )
        after = build_annual_income_inputs(
            timeline, 2, cum_inflation, 0.0, 0, 0, 0, 999, 0, 999,
            post_life_events=events, post_retirement_year_effects=_post_retirement_year_effects,
        )

        assert (before.life_event_cash, before.life_event_monthly) == (0.0, 0.0)
        assert during.life_event_cash == pytest.approx(5000)
        assert during.life_event_monthly == pytest.approx(-500 * 12)
        # A duration_months==0 recurring event runs through the rest of
        # retirement -- the one-time cash doesn't repeat, but the
        # negative monthly adjustment (a recurring cost) persists.
        assert after.life_event_cash == pytest.approx(0.0)
        assert after.life_event_monthly == pytest.approx(-500 * 12)

    def test_no_post_retirement_year_effects_callable_is_a_safe_no_op(self):
        timeline = build_timeline(jason_age=65, justin_age=65, ret_age=65, retirement_end_age=68)
        cum_inflation = build_cumulative_inflation(0.0, timeline.retire_yrs)
        result = build_annual_income_inputs(
            timeline, 0, cum_inflation, 0.0, 0, 0, 0, 999, 0, 999,
        )
        assert (result.life_event_cash, result.life_event_monthly) == (0.0, 0.0)


# ── Integration: builder + shared withdrawal ledger, multi-year balances ──

def _run_year(opening, spending_need, pension, income_row, life_event_cash=None):
    guaranteed = pension + income_row.social_security
    cash = life_event_cash if life_event_cash is not None else income_row.life_event_cash
    return simulate_withdrawal_year(
        opening=opening, spending_need=spending_need, guaranteed_income=guaranteed,
        life_event_cash=cash, rmd_amount=0.0, tax_model=no_tax_model(),
        growth_rate=0.0, order=DEFAULT_ORDER,
    )


class TestIntegrationDepletedAccountsAcrossPastRetirementUnequalAgesAndANegativeEvent:
    """Jason 65, Justin 63, ret_age 65 (current -- see the past-ret_age
    class below for that case), Jason already claiming SS at 62, a
    negative recurring life event starting year 1, and a small starting
    balance that runs out -- covers unequal spouse ages, an early SS
    claim, a negative event, and depleted accounts (unmet_need) all in
    one multi-year chain, with reconcile() checked every year."""

    def test_three_year_chain(self):
        timeline = _timeline_a()
        cum_inflation = build_cumulative_inflation(INFLATION_A, timeline.retire_yrs)
        events = [{"event_year": timeline.calendar_year(1), "one_time": 5000, "monthly": -500, "duration_months": 0}]
        spending_today = 30000

        balances = AccountState(pretax=0, roth=0, taxable=5000, hsa=0)
        results = []
        for yr in range(timeline.retire_yrs):
            income = build_annual_income_inputs(
                timeline, yr, cum_inflation, INFLATION_A,
                healthcare_pre_at_start=HC_PRE_A, healthcare_post_at_start=HC_POST_A,
                jason_ss_annual=JASON_SS_ANNUAL_A, jason_ss_age=JASON_SS_AGE_A,
                justin_ss_annual=JUSTIN_SS_ANNUAL_A, justin_ss_age=JUSTIN_SS_AGE_A,
                post_life_events=events, post_retirement_year_effects=_post_retirement_year_effects,
            )
            spending_need = spending_today * ((1 + INFLATION_A) ** yr) + income.healthcare - income.life_event_monthly
            result = _run_year(balances, spending_need, pension=0.0, income_row=income)
            assert result.reconcile() is None, result.reconcile()
            results.append((income, spending_need, result))
            balances = result.closing

        # yr0: guaranteed income (jason_ss only, ~$21,224) well short of
        # spending_need ($38,000); the $5,000 taxable balance covers part
        # of the gap and the rest is reported as unmet_need -- never
        # silently dropped.
        income0, need0, r0 = results[0]
        expected_jason_ss_0 = JASON_SS_ANNUAL_A * (1 + INFLATION_A) ** 3
        assert income0.social_security == pytest.approx(expected_jason_ss_0)
        expected_unmet_0 = need0 - expected_jason_ss_0 - 5000
        assert expected_unmet_0 > 0  # sanity: this scenario is deliberately underfunded
        assert r0.unmet_need == pytest.approx(expected_unmet_0)
        assert r0.closing.total() == pytest.approx(0.0)

        # yr1: accounts are already at zero; even the $5,000 one-time
        # life-event cash and Justin's newly-started SS aren't enough to
        # close the gap given the recurring $6,000/yr negative event on
        # top -- unmet_need continues to grow, and no bucket goes negative.
        income1, need1, r1 = results[1]
        assert r1.opening.total() == pytest.approx(0.0)
        assert r1.unmet_need == pytest.approx(need1 - income1.social_security - 5000)
        assert r1.closing.total() == pytest.approx(0.0)

        for _, _, r in results:
            for bucket in ("pretax", "roth", "taxable", "hsa"):
                assert getattr(r.closing, bucket) >= 0.0


# ── Performance-exception parity ────────────────────────────────────────
# run_swr_analysis's success_at_withdrawal inner loop and
# run_tax_efficiency_simulation's run_strategy inner loop both run their
# own inline SS-COLA formula rather than calling
# build_annual_income_inputs(), as a documented performance exception
# (both loops run N=1000-trial Monte-Carlo-style, matching the same
# rationale annual_engine.py's module docstring already documents for
# SWR's _swr_year_step). These tests prove the two formulas actually
# agree, rather than just asserting it in a comment.

class TestPerformanceExceptionParity:
    def test_swr_and_tax_efficiencys_inline_ss_formula_matches_the_shared_builder(self):
        timeline = build_timeline(jason_age=63, justin_age=60, ret_age=60, retirement_end_age=66)
        cum_inflation = build_cumulative_inflation(0.025, timeline.retire_yrs)
        jason_ss_annual, jason_ss_age = 22000, 62  # already claiming, before effective_start_age
        justin_ss_annual, justin_ss_age = 12000, 63  # starts mid-horizon, in Justin's own terms

        for yr in range(timeline.retire_yrs):
            age = timeline.effective_start_age + yr
            justin_age_this_year = timeline.justin_age_at(age)

            # The exact inline formula both hot loops use.
            inline_jss = (jason_ss_annual * ((1 + 0.025) ** max(0, age - jason_ss_age))
                          if age >= jason_ss_age else 0)
            inline_uss = (justin_ss_annual * ((1 + 0.025) ** max(0, justin_age_this_year - justin_ss_age))
                          if justin_age_this_year >= justin_ss_age else 0)

            result = build_annual_income_inputs(
                timeline, yr, cum_inflation, 0.025,
                healthcare_pre_at_start=0, healthcare_post_at_start=0,
                jason_ss_annual=jason_ss_annual, jason_ss_age=jason_ss_age,
                justin_ss_annual=justin_ss_annual, justin_ss_age=justin_ss_age,
            )
            assert result.jason_ss == pytest.approx(inline_jss)
            assert result.justin_ss == pytest.approx(inline_uss)


class TestIntegrationPastRetirementSelectionWithIncomeExceedingSpending:
    """Jason 70, Justin 68, requested retirement at 60 (already past --
    effective_start_age clamps to 70), both spouses already well past
    their own SS claim ages, and a modest spending target -- guaranteed
    income exceeds spending_need, so the surplus is swept into taxable
    rather than vanishing, and reconcile() confirms the ledger balances."""

    def test_surplus_year_sweeps_into_taxable_and_reconciles(self):
        timeline = build_timeline(jason_age=70, justin_age=68, ret_age=60, retirement_end_age=72)
        assert timeline.effective_start_age == 70
        inflation = 0.03
        cum_inflation = build_cumulative_inflation(inflation, timeline.retire_yrs)
        jason_ss_annual, jason_ss_age = 24000, 62
        justin_ss_annual, justin_ss_age = 18000, 65  # Justin's own age terms; 68 > 65, already claiming

        income0 = build_annual_income_inputs(
            timeline, 0, cum_inflation, inflation,
            healthcare_pre_at_start=0, healthcare_post_at_start=6000,
            jason_ss_annual=jason_ss_annual, jason_ss_age=jason_ss_age,
            justin_ss_annual=justin_ss_annual, justin_ss_age=justin_ss_age,
        )
        expected_jason_ss = jason_ss_annual * (1 + inflation) ** (70 - jason_ss_age)
        expected_justin_ss = justin_ss_annual * (1 + inflation) ** (68 - justin_ss_age)
        assert income0.jason_ss == pytest.approx(expected_jason_ss)
        assert income0.justin_ss == pytest.approx(expected_justin_ss)

        spending_need = 15000 + income0.healthcare  # deliberately modest vs. six-figure guaranteed income
        opening = AccountState(pretax=100_000, roth=50_000, taxable=20_000, hsa=0)
        result = _run_year(opening, spending_need, pension=0.0, income_row=income0)
        assert result.reconcile() is None, result.reconcile()

        guaranteed = expected_jason_ss + expected_justin_ss
        expected_surplus = guaranteed - spending_need
        assert expected_surplus > 0  # sanity: this scenario is deliberately overfunded
        assert result.unmet_need == pytest.approx(0.0)
        assert result.closing.taxable == pytest.approx(opening.taxable + expected_surplus)
        assert result.closing.pretax == pytest.approx(opening.pretax)  # untouched -- no draw needed
        assert result.closing.roth == pytest.approx(opening.roth)
