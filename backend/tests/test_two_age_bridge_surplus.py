"""
Two-age bridge-income surplus fix (2026-09-10) — correctness follow-up
to the single-age fix (CALCULATION_CONTRACT.md section 73), separate
from Milestone 4's tax/ownership design.

CALCULATION_CONTRACT.md section 73 found and fixed the single-age bug
in run_retirement_projection (projection_engine.py's deterministic
projection), then checked (but did not fix) the identical clamp in
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

Review follow-up, CALCULATION_CONTRACT.md section 76: a SIBLING bug of
the exact same shape survived in simulation_engine.py's single-age
_run_single (shared by single-age run_monte_carlo and run_stress_tests)
-- section 73 only fixed the deterministic projection, not this
Monte-Carlo/Stress-Tests trial loop, which still netted
`max(0, target - bridge)` and never added bridge income to its own
`fixed` (guaranteed_income). TestSingleAgeBridgeSurplusSibling below
verifies that fix using the same exact-value, PORT_STD==0-determinism
technique as the two-age Monte Carlo/Tax Efficiency tests above.
"""
import time

import pytest

import simulation_engine
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

    def test_bridge_is_jason_specific_when_jason_retires_first(self):
        """Bridge income is Jason's OWN individual income -- the gate is
        literally `jason_ret_age == 55` inside need_for_year, unrelated
        to who is later_retiree. Jason retires first here (55), Justin
        later (58) and still working -- confirm the bridge surplus is
        credited on top of, and distinct from, Justin's own gap-income
        offset (an exact three-way sum: opening + bridge surplus +
        Justin's gap income, not two of the three silently substituting
        for each other)."""
        jason_first = base_inputs(jason_age=53, justin_age=53, retirement_income_today_dollars=100000, retirement_end_age=59,
                                   bridge_income_55=150000, bridge_years_55=1, justin_w2_salary=50000)
        r1 = run_two_dimensional_retirement_projection(jason_first, TAXABLE(500000), jason_ret_age=55, justin_ret_age=58)
        assert r1["later_retiree"] == "justin"
        y0 = r1["yearly_detail"][0]
        assert y0["bridge_income"] == 150000
        assert y0["still_working_spouse_income"] > 0  # Justin's own gap income, a SEPARATE offset
        assert y0["portfolio_balance"] == 500000 + (150000 - 100000) + y0["still_working_spouse_income"]

        # Same household with bridge income zeroed out: the gap-income
        # contribution alone must be byte-identical, proving the bridge
        # term above isn't silently reusing or duplicating that number.
        no_bridge = base_inputs(jason_age=53, justin_age=53, retirement_income_today_dollars=100000, retirement_end_age=59,
                                 justin_w2_salary=50000)
        r1b = run_two_dimensional_retirement_projection(no_bridge, TAXABLE(500000), jason_ret_age=55, justin_ret_age=58)
        y0_no_bridge = r1b["yearly_detail"][0]
        assert y0_no_bridge["bridge_income"] == 0
        assert y0_no_bridge["still_working_spouse_income"] == y0["still_working_spouse_income"]
        assert y0["portfolio_balance"] - y0_no_bridge["portfolio_balance"] == 150000  # isolates the bridge's own contribution

    def test_bridge_does_not_apply_when_justin_retires_first(self):
        """The mirror case: Justin retires first (55), Jason retires
        LATER (58) and is the one still working. The bridge gate
        (`jason_ret_age == 55`) is NOT satisfied here (jason_ret_age is
        58), so bridge_income must be exactly zero every year, and the
        only income offset present during Justin's early-retirement
        phase must be Jason's own gap income -- exercising the
        later-retiree income path the bridge-specific test above does
        not reach, and confirming bridge eligibility isn't accidentally
        keyed off "whoever retires first" instead of Jason specifically."""
        justin_first = base_inputs(jason_age=53, justin_age=53, retirement_income_today_dollars=100000, retirement_end_age=59,
                                    bridge_income_55=150000, bridge_years_55=1, w2_salary=40000)
        r2 = run_two_dimensional_retirement_projection(justin_first, TAXABLE(500000), jason_ret_age=58, justin_ret_age=55)
        assert r2["later_retiree"] == "jason"
        for y in r2["yearly_detail"]:
            assert y["bridge_income"] == 0
        y0b = r2["yearly_detail"][0]
        assert y0b["still_working_spouse_income"] > 0  # Jason's own gap income, the later-retiree path
        assert y0b["portfolio_balance"] == 500000 - 100000 + y0b["still_working_spouse_income"]

        # Same household with jason_ret_age flipped to 55 (bridge-
        # eligible) but otherwise identical -- confirms the zero above is
        # really the gate, not some other unrelated zeroing.
        jason_bridge_eligible = base_inputs(jason_age=53, justin_age=53, retirement_income_today_dollars=100000, retirement_end_age=59,
                                             bridge_income_55=150000, bridge_years_55=1)
        r2b = run_two_dimensional_retirement_projection(jason_bridge_eligible, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        assert r2b["yearly_detail"][0]["bridge_income"] == 150000

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
    """Exact-value checks (not floor/success-rate thresholds a broken
    bridge path could still pass) confirming each of the other 6 call
    sites sweeps a bridge surplus via its OWN existing surplus-handling
    mechanism, exactly once, with the correct (untaxed, pension-parity)
    tax-rate assumption and owner attribution -- not a re-derivation of
    run_two_dimensional_retirement_projection's own logic."""

    def test_owner_split_projection_credits_surplus_to_jason(self):
        """run_owner_split_two_dimensional_projection: bridge income is
        Jason's own individual income (the bridge phase is keyed to his
        retirement specifically) -- confirms the surplus is attributed to
        the "jason" owner bucket, not lost or misattributed to joint, AND
        that a zero-bridge household lands at the byte-identical
        no-bridge figure (isolating the bridge's own $150,000
        contribution exactly, not a coincidental match)."""
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

        no_bridge_inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56)
        no_bridge_result = run_owner_split_two_dimensional_projection(no_bridge_inputs, accounts, jason_ret_age=55, justin_ret_age=55)
        y0_no_bridge = no_bridge_result["yearly_detail"][0]
        assert y0_no_bridge["bridge_income"] == 0
        assert y0_no_bridge["owner_balances"]["jason"]["taxable"] == 400000  # 500000 - 100000 spending, no surplus
        assert y0["owner_balances"]["jason"]["taxable"] - y0_no_bridge["owner_balances"]["jason"]["taxable"] == 150000

    def test_monte_carlo_two_age_reflects_bridge_surplus(self, monkeypatch):
        """run_monte_carlo (two-age dispatch): forcing PORT_STD to 0
        makes every one of the 1000 trials identical (random.gauss(mu,
        0) == mu), so median_final_balance becomes an EXACT, hand-
        computable figure -- $500,000 opening + ($150,000 bridge -
        $100,000 spending) surplus, swept via simulate_withdrawal_year's
        existing mechanism = $550,000 precisely. A broken/ignored bridge
        path would land at $400,000 (spending drawn with no offset) or
        some other wrong number, not $550,000 -- unlike a bare
        success_rate==100.0 check, which an already-fully-funded
        household could satisfy regardless of whether bridge income was
        ever counted."""
        monkeypatch.setattr(simulation_engine, "PORT_STD", 0.0)
        inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56,
                              bridge_income_55=150000, bridge_years_55=1)
        result = run_monte_carlo(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        assert result["success_rate"] == 100.0
        assert result["median_final_balance"] == 550000

        no_bridge_inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56)
        no_bridge_result = run_monte_carlo(no_bridge_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        assert no_bridge_result["median_final_balance"] == 400000  # zero-bridge parity, unaffected by the fix
        assert result["median_final_balance"] - no_bridge_result["median_final_balance"] == 150000

    def test_stress_tests_two_age_reflects_bridge_surplus(self):
        # "base" (post_ret every year, no historical override, no
        # randomness) is fully deterministic -- assert its exact value,
        # the same hand-computable $550,000 figure as Monte Carlo above,
        # rather than a floor a broken bridge path could still clear.
        bridge_inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56,
                                     bridge_income_55=150000, bridge_years_55=1)
        no_bridge_inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56)
        with_bridge = run_stress_tests(bridge_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        without_bridge = run_stress_tests(no_bridge_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)

        assert with_bridge["scenarios"]["base"]["final_balance"] == 550000
        assert without_bridge["scenarios"]["base"]["final_balance"] == 400000

        # Every remaining scenario applies real (sometimes negative)
        # historical returns to the SAME return path in both the
        # with-bridge and no-bridge runs, so a strict improvement (not
        # just >=, which a completely-ignored bridge could also satisfy
        # via equality) is required in each one: extra guaranteed income
        # that's never withdrawn can only ever help, never hurt or leave
        # a well-funded household unchanged.
        strict_improvements = 0
        for name, scenario in with_bridge["scenarios"].items():
            no_bridge_balance = without_bridge["scenarios"][name]["final_balance"]
            assert scenario["final_balance"] > no_bridge_balance, f"scenario {name!r} did not improve with bridge income"
            strict_improvements += 1
        assert strict_improvements == len(with_bridge["scenarios"])

    def test_roth_conversion_two_age_with_and_without_conversions_both_credit_surplus(self):
        """_run_roth_conversion_analysis_two_age has TWO call sites
        (with-conversions schedule, no-conversions baseline) -- both
        must credit the same bridge surplus, or the "without
        conversions" comparison would itself be biased by an
        inconsistency between the two paths. Zero pretax balance forces
        optimal_conversion to 0 every year (nothing to convert), which
        isolates the surplus-sweep arithmetic from conversion-tax
        arithmetic entirely, making taxable_after an EXACT figure."""
        inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=75,
                              bridge_income_55=150000, bridge_years_55=1)
        accounts = [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 500000}]
        result = run_roth_conversion_analysis(inputs, accounts, jason_ret_age=55, justin_ret_age=55)
        assert result["schedule"][0]["optimal_conversion"] == 0  # nothing to convert -- pure surplus-sweep math below
        assert result["schedule"][0]["taxable_after"] == 550000  # 500000 + (150000 bridge - 100000 spending), untaxed like pension
        assert result["schedule"][0]["unmet_need"] == 0
        assert result["pretax_at_rmd_age_no_conversion"] == 0

        no_bridge_inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=75)
        no_bridge_result = run_roth_conversion_analysis(no_bridge_inputs, accounts, jason_ret_age=55, justin_ret_age=55)
        assert no_bridge_result["schedule"][0]["taxable_after"] == 400000  # 500000 - 100000, zero-bridge parity
        assert result["schedule"][0]["taxable_after"] - no_bridge_result["schedule"][0]["taxable_after"] == 150000

    def test_tax_efficiency_two_age_reflects_bridge_surplus(self, monkeypatch):
        """Same PORT_STD==0 determinism trick as Monte Carlo. With the
        bridge surplus alone covering spending, no strategy ever needs
        to draw from any bucket (net_need clamps to 0 after
        _cash_available_offsets_need's surplus credit), so ALL THREE
        draw-order policies converge on the identical, exact
        $550,000 figure and pay zero lifetime tax -- a strategy-specific
        floor/threshold check couldn't distinguish "bridge correctly
        swept" from "bridge ignored but happens to still clear the
        bar," an exact equality with a zero-bridge comparison can."""
        monkeypatch.setattr(simulation_engine, "PORT_STD", 0.0)
        inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56,
                              bridge_income_55=150000, bridge_years_55=1)
        no_bridge_inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56)
        result = run_tax_efficiency_simulation(inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        no_bridge_result = run_tax_efficiency_simulation(no_bridge_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        # Zero-bridge parity figures differ by strategy: "taxable_first"/
        # "roth_first" draw the $100,000 need from taxable and gross up
        # for the flat TAX_TAXABLE rate (500000 - 100000/(1-0.15) =
        # 382353); "optimal" applies its own capital-gains-aware cost-
        # basis assumption instead, landing at 399806. Both are legitimate,
        # PRE-EXISTING per-strategy tax treatments unrelated to bridge
        # income -- confirmed independently (without this fix, without
        # PORT_STD patched) so the bridge fix isn't blamed for a
        # difference that already existed between strategies.
        no_bridge_expected = {"taxable_first": 382353, "roth_first": 382353, "optimal": 399806}
        for strategy_key in ("taxable_first", "roth_first", "optimal"):
            strategy = result["strategies"][strategy_key]
            no_bridge_strategy = no_bridge_result["strategies"][strategy_key]
            assert strategy["median_final_balance"] == 550000
            assert strategy["success_rate"] == 100.0
            assert strategy["median_lifetime_tax"] == 0  # surplus swept untaxed, like pension -- no draw needed at all
            assert no_bridge_strategy["median_final_balance"] == no_bridge_expected[strategy_key]


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


class TestSingleAgeBridgeSurplusSibling:
    """Sibling fix (CALCULATION_CONTRACT.md section 76): simulation_
    engine.py's single-age _run_single -- shared by single-age
    run_monte_carlo and run_stress_tests -- had the identical clamp bug
    as the two-age one this file otherwise covers, but section 74/75's
    fix never touched it (a different function, in a different part of
    the same file). Same exact-value/zero-bridge-parity technique as
    the two-age Monte Carlo/Stress tests above: PORT_STD monkeypatched
    to 0.0 makes every Monte Carlo trial identical, and Stress Tests'
    "base" scenario is already fully deterministic, so both give a
    hand-computable $550,000 figure a broken/ignored bridge path could
    not produce."""

    def test_single_age_monte_carlo_exact_bridge_surplus(self, monkeypatch):
        monkeypatch.setattr(simulation_engine, "PORT_STD", 0.0)
        inputs = base_inputs(jason_age=55, justin_age=55, retirement_income_today_dollars=100000, retirement_end_age=56,
                              bridge_income_55=150000, bridge_years_55=1)
        result = run_monte_carlo(inputs, TAXABLE(500000), ret_age=55)
        assert result["success_rate"] == 100.0
        assert result["median_final_balance"] == 550000  # 500000 + (150000 bridge - 100000 spending)

        no_bridge_inputs = base_inputs(jason_age=55, justin_age=55, retirement_income_today_dollars=100000, retirement_end_age=56)
        no_bridge_result = run_monte_carlo(no_bridge_inputs, TAXABLE(500000), ret_age=55)
        assert no_bridge_result["median_final_balance"] == 400000  # zero-bridge parity, unaffected by the fix
        assert result["median_final_balance"] - no_bridge_result["median_final_balance"] == 150000

    def test_single_age_stress_tests_exact_and_strict_bridge_surplus(self, monkeypatch):
        # PORT_STD forced to 0 here too: single-age run_stress_tests
        # (unlike run_monte_carlo) never calls random.seed(), so
        # "early_sequence"'s random-filled tail years are NOT
        # reproducible between calls -- with PORT_STD==0,
        # random.gauss(mu, 0) == mu regardless of the RNG's actual
        # state, making every scenario fully deterministic and the
        # with-bridge/without-bridge comparison meaningful rather than
        # incidentally flaky (pre-existing, unrelated to this fix --
        # reported in CALCULATION_CONTRACT.md section 76, not silently
        # fixed here).
        monkeypatch.setattr(simulation_engine, "PORT_STD", 0.0)
        # An 11-year horizon (longer than every SCENARIOS override table,
        # the longest of which is 10 years for stagflation_1970s/
        # lost_decade) sidesteps a PRE-EXISTING, bridge-unrelated crash:
        # run_stress_tests's `normal_returns[max(0, yr-len(overrides))]`
        # is an eager dict.get default argument, evaluated every
        # iteration even when `overrides` already has that key -- with a
        # horizon at or below an override table's length, normal_returns
        # is empty and this indexes out of range regardless of bridge
        # income (reproduced independently with bridge_income_55=0).
        # Pre-existing, out of scope for this fix (the two-age version
        # was already fixed for this exact issue, per its own "Lazy
        # branch, not dict.get's eager default arg" comment) -- reported
        # in CALCULATION_CONTRACT.md section 76, not silently fixed here.
        # A large balance and small annual spend keep 11 years of $10,000
        # spending easily affordable in every scenario, base included.
        bridge_inputs = base_inputs(jason_age=55, justin_age=55, retirement_income_today_dollars=10000, retirement_end_age=66,
                                     bridge_income_55=150000, bridge_years_55=1)
        no_bridge_inputs = base_inputs(jason_age=55, justin_age=55, retirement_income_today_dollars=10000, retirement_end_age=66)
        with_bridge = run_stress_tests(bridge_inputs, TAXABLE(5000000), ret_age=55)
        without_bridge = run_stress_tests(no_bridge_inputs, TAXABLE(5000000), ret_age=55)

        # "base" (post_ret every year, no override, no randomness) is
        # fully deterministic -- exact value, not a floor: 5,000,000 +
        # (150,000 bridge in year 0) - 11 * 10,000 spending.
        assert with_bridge["scenarios"]["base"]["final_balance"] == 5000000 + 150000 - 11 * 10000
        assert without_bridge["scenarios"]["base"]["final_balance"] == 5000000 - 11 * 10000

        # Every remaining (historical-override) scenario applies the
        # SAME return path to both runs, so extra guaranteed income that
        # is never withdrawn can only strictly help -- >, not >=, which
        # a fully-ignored bridge could also satisfy via equality.
        strict_improvements = 0
        for name, scenario in with_bridge["scenarios"].items():
            no_bridge_balance = without_bridge["scenarios"][name]["final_balance"]
            assert scenario["final_balance"] > no_bridge_balance, f"scenario {name!r} did not improve with bridge income"
            strict_improvements += 1
        assert strict_improvements == len(with_bridge["scenarios"])  # every scenario was actually checked, none skipped

    def test_single_age_zero_bridge_household_unchanged(self, monkeypatch):
        """Regression guard: a household with no bridge income at all
        must be completely unaffected by this fix -- both Monte Carlo
        and Stress Tests reduce to the plain opening-balance-minus-
        spending arithmetic that always existed."""
        monkeypatch.setattr(simulation_engine, "PORT_STD", 0.0)
        inputs = base_inputs(jason_age=60, justin_age=60, retirement_income_today_dollars=80000, retirement_end_age=61)
        mc_result = run_monte_carlo(inputs, TAXABLE(500000), ret_age=60)
        assert mc_result["median_final_balance"] == 420000  # 500000 - 80000, no bridge configured at all

        # 11-year horizon here too, for the same pre-existing-crash reason
        # as the test above -- a plain (no-bridge) household is otherwise
        # affected by it identically, confirming it's unrelated to bridge.
        stress_inputs = base_inputs(jason_age=55, justin_age=55, retirement_income_today_dollars=10000, retirement_end_age=66)
        stress_result = run_stress_tests(stress_inputs, TAXABLE(5000000), ret_age=55)
        assert stress_result["scenarios"]["base"]["final_balance"] == 5000000 - 11 * 10000

    def test_single_age_bridge_surplus_matches_two_age_sibling(self, monkeypatch):
        """The single-age and two-age fixes are independent
        implementations of the identical formula -- confirm they land on
        the exact same number for a matched household, rather than each
        merely being internally self-consistent."""
        monkeypatch.setattr(simulation_engine, "PORT_STD", 0.0)
        single_inputs = base_inputs(jason_age=55, justin_age=55, retirement_income_today_dollars=100000, retirement_end_age=56,
                                     bridge_income_55=150000, bridge_years_55=1)
        two_age_inputs = base_inputs(retirement_income_today_dollars=100000, retirement_end_age=56,
                                      bridge_income_55=150000, bridge_years_55=1)
        single_result = run_monte_carlo(single_inputs, TAXABLE(500000), ret_age=55)
        two_age_result = run_monte_carlo(two_age_inputs, TAXABLE(500000), jason_ret_age=55, justin_ret_age=55)
        assert single_result["median_final_balance"] == two_age_result["median_final_balance"] == 550000
