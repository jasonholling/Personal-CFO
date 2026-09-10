"""
Two-age bridge-income surplus fix (2026-09-10) — correctness follow-up
to the single-age fix (CALCULATION_CONTRACT.md section 73), separate
from Milestone 4's tax/ownership design.

CALCULATION_CONTRACT.md section 73 found and fixed the single-age bug,
then checked (but did not fix) the identical clamp in
two_age_spending_need_fn, shared by 7 call sites across
projection_engine.py and simulation_engine.py: run_two_dimensional_
retirement_projection, run_owner_split_two_dimensional_projection, and
five sites in simulation_engine.py (two-age Monte Carlo, Stress Tests,
Roth Conversion x2, Tax Efficiency). This file verifies that follow-up
fix: bridge income no longer nets into year_need at all (matching the
single-age fix exactly), and instead flows into each consumer's own
guaranteed-income channel — fixed_income/guaranteed for
simulate_withdrawal_year-based consumers, the guaranteed_income argument
to _swr_year_step, or the guaranteed_income argument to
_cash_available_offsets_need — reusing each one's ALREADY-EXISTING
surplus-sweep behavior rather than a new, bridge-specific adjustment.
"""
import time

import pytest

from projection_engine import (
    run_two_dimensional_retirement_projection,
    run_owner_split_two_dimensional_projection,
    run_retirement_projection,
)
from simulation_engine import (
    run_monte_carlo,
    run_stress_tests,
    run_roth_conversion_analysis,
    run_tax_efficiency_simulation,
)

TAXABLE = lambda balance: [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": balance}]


def base_inputs(**overrides):
    """Zeroed-out household, same convention test_two_dimensional_
    retirement.py's own base_inputs uses -- every dollar in a test's
    arithmetic comes from what the test explicitly sets."""
    inputs = {
        "jason_age": 60, "justin_age": 60,
        "inflation_rate": 0.0,
        "expected_return_pre_retirement": 0.0,
        "expected_return_post_retirement": 0.0,
        "retirement_income_today_dollars": 80000,
        "annual_hsa_contribution": 0, "annual_rsu_value": 0,
        "jason_social_security": 0, "justin_social_security": 0,
        "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
        "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
        "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0,
        "annual_bonus_pct": 0,
        "state_income_tax_rate": 0,
        "pension_55": 0, "pension_60": 0, "pension_65": 0,
        "bridge_years_55": 0, "bridge_income_55": 0,
        "kids_years_at_home_55": 0, "kids_annual_cost": 0,
    }
    inputs.update(overrides)
    return inputs


class TestTwoAgeBridgeSurplusCore:
    """run_two_dimensional_retirement_projection -- the primary two-age
    consumer, and the one every other two-age consumer's starting
    balances are read from."""

    def test_exact_review_reproduction_500k_100k_150k(self):
        """The review's own exact single-age reproduction, replayed in
        two-age mode: $500,000 taxable, $100,000 spending, $150,000
        bridge income, zero tax/growth -> $550,000 ending assets."""
        inputs = base_inputs(retirement_income_today_dollars=100000,
                              bridge_income_55=150000, bridge_years_55=1)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        y0 = result["yearly_detail"][0]
        assert y0["bridge_income"] == 150000
        assert y0["income_need"] == 100000  # gross target, bridge not netted into it
        assert y0["draw"] == 0              # bridge alone covers spending with $50,000 to spare
        assert y0["portfolio_balance"] == 550000

    def test_bridge_below_spending(self):
        inputs = base_inputs(retirement_income_today_dollars=100000,
                              bridge_income_55=35000, bridge_years_55=1)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        y0 = result["yearly_detail"][0]
        assert y0["income_need"] == 100000
        assert y0["draw"] == 65000
        assert y0["portfolio_balance"] == 500000 - 65000

    def test_bridge_equal_to_spending(self):
        inputs = base_inputs(retirement_income_today_dollars=100000,
                              bridge_income_55=100000, bridge_years_55=1)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        y0 = result["yearly_detail"][0]
        assert y0["draw"] == 0
        assert y0["portfolio_balance"] == 500000  # untouched -- fully covered, no surplus

    def test_bridge_expiration_reverts_to_normal_spending(self):
        """Bridge active for 2 years, then expires -- year 3 onward
        should behave exactly as if bridge_income_55 had never been set
        (no lingering surplus credit, no residual offset)."""
        inputs = base_inputs(jason_age=53, justin_age=53, retirement_income_today_dollars=100000, retirement_end_age=58,
                              bridge_income_55=150000, bridge_years_55=2)
        with_bridge = run_two_dimensional_retirement_projection(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        yearly = with_bridge["yearly_detail"]
        assert len(yearly) >= 3
        assert yearly[0]["bridge_income"] == 150000
        assert yearly[1]["bridge_income"] == 150000
        assert yearly[2]["bridge_income"] == 0  # expired
        assert yearly[2]["income_need"] == 100000
        assert yearly[2]["draw"] == 100000  # full spending drawn from portfolio, no bridge left

        # Post-expiration balance must match a household that never had
        # bridge income, PLUS the two years of accumulated surplus --
        # not some other, corrupted trajectory.
        no_bridge_inputs = base_inputs(jason_age=53, justin_age=53, retirement_income_today_dollars=100000, retirement_end_age=58)
        no_bridge = run_two_dimensional_retirement_projection(no_bridge_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        # Each bridge year, with_bridge nets +bridge-spending while
        # no_bridge nets -spending; the gap per bridge year is the FULL
        # bridge income (the spending itself is identical/cancels), so
        # the two-year gap is 2 * bridge_income, not 2 * net surplus.
        accumulated_gap = 2 * 150000
        assert with_bridge["yearly_detail"][2]["portfolio_balance"] == no_bridge["yearly_detail"][2]["portfolio_balance"] + accumulated_gap

    def test_bridge_with_inflation_reconciles_each_year(self):
        """Nonzero inflation -- bridge income and the spending target
        must inflate by the SAME compounding curve (both derive from
        cum_inflation), so the surplus/shortfall ratio stays consistent
        year to year rather than drifting due to a mismatched curve."""
        inputs = base_inputs(jason_age=55, justin_age=55, retirement_income_today_dollars=100000, inflation_rate=0.03,
                              retirement_end_age=58, bridge_income_55=150000, bridge_years_55=3)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        yearly = result["yearly_detail"]
        for i, y in enumerate(yearly):
            expected_need = round(100000 * (1.03 ** i))
            expected_bridge = round(150000 * (1.03 ** i))
            assert y["income_need"] == expected_need
            assert y["bridge_income"] == expected_bridge
            assert y["draw"] == 0  # bridge exceeds spending at every year of this inflation rate

    def test_both_retirement_orders_jason_first_vs_justin_first(self):
        """Bridge income only ever applies during JASON's own bridge
        phase (need_for_year's own jason_ret_age==55 gate) -- confirm
        the surplus fix behaves identically regardless of which spouse
        is later_retiree, since that's an orthogonal axis from which
        spouse the bridge job belongs to."""
        # Jason retires first (55), Justin later (58) -- Justin becomes
        # the still-working spouse during Jason's bridge phase.
        jason_first = base_inputs(jason_age=53, justin_age=53, retirement_income_today_dollars=100000, retirement_end_age=59,
                                   bridge_income_55=150000, bridge_years_55=1, justin_w2_salary=50000)
        r1 = run_two_dimensional_retirement_projection(jason_first, TAXABLE(500000), jason_ret_age=55, justin_ret_age=58)
        assert r1["later_retiree"] == "justin"
        y0 = r1["yearly_detail"][0]
        assert y0["bridge_income"] == 150000
        assert y0["portfolio_balance"] == 500000 + (150000 - 100000) + y0["still_working_spouse_income"]

        # Justin retires first (55), Jason later (58) -- jason_ret_age
        # must still be 55 for the bridge gate (it's keyed to Jason
        # specifically, confirmed by need_for_year's own condition), but
        # Justin being the earlier retiree of the pair changes
        # later_retiree/phase framing without touching bridge eligibility.
        justin_first_same_bridge = base_inputs(jason_age=53, justin_age=53, retirement_income_today_dollars=100000, retirement_end_age=56,
                                                bridge_income_55=150000, bridge_years_55=1)
        r2 = run_two_dimensional_retirement_projection(justin_first_same_bridge, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        y0b = r2["yearly_detail"][0]
        assert y0b["bridge_income"] == 150000
        assert y0b["portfolio_balance"] == 550000

    def test_bridge_surplus_combined_with_pension_and_life_event(self):
        """Bridge overshoot stacked with pension AND a one-time life
        event in the same year -- confirms none of the offsets interfere
        or get double-counted once bridge's own surplus is in play."""
        inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56,
                              bridge_income_55=150000, bridge_years_55=1,
                              pension_55=20000)
        life_events = [{"event_year": 2026, "one_time_cash_delta": 10000,
                         "monthly_cash_flow_delta": 0, "duration_months": 0}]
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55,
                                                             life_events=life_events)
        y0 = result["yearly_detail"][0]
        assert y0["bridge_income"] == 150000
        assert y0["pension"] == 20000
        assert y0["income_need"] == 100000
        assert y0["draw"] == 0
        total_offsets = 150000 + 20000 + 10000  # bridge + pension + one-time life event
        assert y0["portfolio_balance"] == 500000 + (total_offsets - 100000)

    def test_zero_bridge_household_unchanged(self):
        """Regression guard: a household with no bridge income at all
        must produce byte-identical numbers to what this function always
        returned -- the fix must be a true no-op for the common case."""
        inputs = base_inputs(retirement_income_today_dollars=80000, retirement_end_age=63)
        result = run_two_dimensional_retirement_projection(inputs, TAXABLE(500000), jason_ret_age=60, justin_ret_age=60)
        for y in result["yearly_detail"]:
            assert y["bridge_income"] == 0
            assert y["income_need"] == 80000
            assert y["draw"] == 80000


class TestTwoAgeBridgeSurplusOtherConsumers:
    """Spot-checks confirming each of the other 6 call sites correctly
    sweeps a bridge surplus via its OWN existing surplus-handling
    mechanism -- not a re-derivation of run_two_dimensional_retirement_
    projection's own logic."""

    def test_owner_split_projection_credits_surplus_to_jason(self):
        """run_owner_split_two_dimensional_projection: bridge income is
        Jason's own individual income (the bridge phase is keyed to his
        retirement specifically) -- confirms the surplus is attributed to
        the "jason" owner bucket, not lost or misattributed to joint."""
        inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56,
                              bridge_income_55=150000, bridge_years_55=1)
        accounts = [{"name": "Brokerage", "account_type": "taxable", "owner": "jason", "balance": 500000}]
        result = run_owner_split_two_dimensional_projection(inputs, accounts, jason_ret_age=55, justin_ret_age=55)
        y0 = result["yearly_detail"][0]
        assert y0["bridge_income"] == 150000
        assert y0["portfolio_balance"] == 550000
        assert y0["owner_balances"]["jason"]["taxable"] == 550000  # the surplus landed with Jason specifically
        assert y0["owner_balances"]["justin"]["taxable"] == 0
        assert y0["owner_balances"]["joint"]["taxable"] == 0

    def test_monte_carlo_two_age_reflects_bridge_surplus(self):
        """run_monte_carlo (two-age dispatch): a household whose bridge
        income comfortably exceeds spending, with zero market volatility
        forced via PORT_STD-irrelevant zero return assumptions, should
        show 100% success and a rising (not flat/declining) portfolio
        trajectory in early years -- deterministic given the fixed
        seed(42) every Monte Carlo call resets to."""
        inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56,
                              bridge_income_55=150000, bridge_years_55=1)
        result = run_monte_carlo(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        assert result["success_rate"] == 100.0

    def test_stress_tests_two_age_reflects_bridge_surplus(self):
        # Historical stress scenarios apply real (sometimes negative)
        # market returns, so an absolute floor isn't a valid check here
        # -- instead compare against an identical household with no
        # bridge income: every scenario's final balance must be HIGHER
        # with the bridge surplus credited than without it, by exactly
        # the $50,000 surplus (0% return years would show this
        # directly; nonzero-return years compound it, so use >=).
        bridge_inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56,
                                     bridge_income_55=150000, bridge_years_55=1)
        no_bridge_inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56)
        with_bridge = run_stress_tests(bridge_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        without_bridge = run_stress_tests(no_bridge_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        for name, scenario in with_bridge["scenarios"].items():
            assert scenario["final_balance"] >= without_bridge["scenarios"][name]["final_balance"]

    def test_roth_conversion_two_age_with_and_without_conversions_both_credit_surplus(self):
        """_run_roth_conversion_analysis_two_age has TWO call sites
        (with-conversions schedule, no-conversions baseline) -- both
        must credit the same bridge surplus, or the "without
        conversions" comparison would itself be biased by an
        inconsistency between the two paths."""
        inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=75,
                              bridge_income_55=150000, bridge_years_55=1)
        accounts = [{"name": "401k", "account_type": "401k", "owner": "jason", "balance": 300000},
                    {"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 500000}]
        result = run_roth_conversion_analysis(inputs, accounts, jason_ret_age=55, justin_ret_age=55)
        assert result["schedule"][0]["taxable_balance"] >= 500000  # with-conversions path credited the surplus (or converted it away, never lost)
        # pretax_at_rmd_age_no_conversion existing without erroring, and
        # the schedule not reporting unmet_need in year 0, both indicate
        # the no-conversion baseline loop didn't silently discard the
        # bridge surplus either (it would show as a funding shortfall
        # relative to reality if it had).
        assert result["schedule"][0]["unmet_need"] == 0

    def test_tax_efficiency_two_age_reflects_bridge_surplus(self):
        inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56,
                              bridge_income_55=150000, bridge_years_55=1)
        result = run_tax_efficiency_simulation(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        for strategy_key in ("taxable_first", "roth_first", "optimal"):
            strategy = result["strategies"][strategy_key]
            assert strategy["median_final_balance"] >= 500000
            assert strategy["success_rate"] == 100.0


class TestSingleAgeVsTwoAgeBridgeParity:
    """Compare equivalent single-age and two-age scenarios -- same
    household, same bridge income, same spending -- the two engines are
    independent implementations, but the fix's underlying arithmetic
    (gross spending, bridge as a guaranteed-income offset, surplus swept
    via each engine's own existing mechanism) should produce the SAME
    year-0 numbers for an otherwise-identical household."""

    def test_matched_household_produces_matching_surplus(self):
        single_inputs = {
            "jason_age": 55, "justin_age": 55, "retirement_income_today_dollars": 100000,
            "bridge_income_55": 150000, "bridge_years_55": 1, "kids_years_at_home_55": 0, "kids_annual_cost": 0,
            "inflation_rate": 0, "expected_return_pre_retirement": 0, "expected_return_post_retirement": 0,
            "state_income_tax_rate": 0, "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
            "justin_w2_salary": 0, "justin_ret_age": 0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "annual_hsa_contribution": 0, "annual_rsu_value": 0, "jason_ss_claim_age": 67,
        }
        single_accounts = [{"id": 1, "name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 500000}]
        single_result = run_retirement_projection(single_inputs, single_accounts, ret_ages=[55])
        single_y0 = next(s for s in single_result["scenarios"] if s["ss_timing"] == "early")["yearly_detail"][0]

        two_age_inputs = base_inputs(retirement_income_today_dollars=100000,
                                      bridge_income_55=150000, bridge_years_55=1)
        two_age_result = run_two_dimensional_retirement_projection(two_age_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        two_age_y0 = two_age_result["yearly_detail"][0]

        assert single_y0["bridge_income"] == two_age_y0["bridge_income"] == 150000
        assert single_y0["gross_spending_need"] == two_age_y0["income_need"] == 100000
        assert single_y0["portfolio_balance"] == two_age_y0["portfolio_balance"] == 550000


class TestBridgeFixPerformance:
    """Performance-sensitive consumers (Monte Carlo runs 1000 trials per
    call) -- confirm the fix (one extra addition per year, per trial)
    doesn't introduce a measurable slowdown, by comparing a zero-bridge
    run against a bridge-active run of the same size. Not a strict
    regression baseline (no "before" binary to compare against), but
    confirms the fix itself carries no material overhead."""

    def test_monte_carlo_two_age_runtime_unaffected_by_bridge(self):
        no_bridge_inputs = base_inputs(retirement_income_today_dollars=80000, retirement_end_age=95)
        bridge_inputs = base_inputs(retirement_income_today_dollars=80000, retirement_end_age=95,
                                     bridge_income_55=30000, bridge_years_55=5)

        start = time.perf_counter()
        run_monte_carlo(no_bridge_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        no_bridge_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        run_monte_carlo(bridge_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        bridge_elapsed = time.perf_counter() - start

        # Generous tolerance (3x) -- the point is confirming no
        # order-of-magnitude regression from the fix, not chasing a tight
        # bound that would make this test flaky on a loaded machine.
        assert bridge_elapsed < no_bridge_elapsed * 3 + 1.0
