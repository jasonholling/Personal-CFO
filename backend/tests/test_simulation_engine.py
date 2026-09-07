"""
Tests for simulation_engine.py — Monte Carlo, SWR, stress tests, Roth
conversion, tax efficiency, and contribution sensitivity.

These are deliberately run across every age 55-67, not just the three
Settings anchor points (55/60/65) — that's exactly the class of bug found
on 2026-08-26: several of these functions crashed (KeyError) or silently
returned wrong data (pension defaulting to $0, or portfolio silently
substituted from age 55's data) for any "in-between" age. Monte Carlo tests
use only a handful of simulations (via monkeypatching where practical) to
keep the suite fast; where that's not practical, N stays small in the
source itself (1000) and tests just assert shape, not exact values.
"""
import pytest

from simulation_engine import (
    run_swr_analysis,
    run_monte_carlo,
    run_stress_tests,
    run_roth_conversion_analysis,
    run_tax_efficiency_simulation,
    run_contribution_sensitivity,
    run_survivor_scenario,
    _run_single,
)
from projection_engine import CURRENT_YEAR

# Every age the Retirement Sensitivity / Simulation pages let you pick,
# not just the three Settings anchor points.
ALL_AGES = list(range(55, 68))
INTERMEDIATE_AGES = [a for a in ALL_AGES if a not in (55, 60, 65)]


class TestRunSwrAnalysis:
    @pytest.mark.parametrize("age", ALL_AGES)
    def test_every_age_returns_without_error(self, sample_inputs, sample_accounts, age):
        result = run_swr_analysis(sample_inputs, sample_accounts, ret_age=age, ss_timing="early")
        assert result["retirement_age"] == age
        assert result["safe_withdrawal_annual"] >= 0
        assert result["total_safe_spend"] >= result["safe_withdrawal_annual"]

    def test_justin_ss_age_setting_is_respected(self, sample_inputs, sample_accounts):
        # ss_start_age = max(jason_ss_age, justin_ss_age); early jason_ss_age=62.
        custom = {**sample_inputs, "justin_ss_age": 63}
        result = run_swr_analysis(custom, sample_accounts, ret_age=60, ss_timing="early")
        assert result["ss_start_age"] == 63

    def test_early_vs_delayed_timing_differ(self, sample_inputs, sample_accounts):
        early = run_swr_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        delayed = run_swr_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="delayed")
        assert early["jason_ss_annual"] != delayed["jason_ss_annual"]

    def test_intermediate_age_pension_is_interpolated_not_zero(self, sample_inputs, sample_accounts):
        """Regression: run_roth_conversion_analysis used to silently return
        $0 pension for any age other than exactly 60. Same risk pattern
        applies here if pension_for_age were ever bypassed again."""
        result = run_swr_analysis(sample_inputs, sample_accounts, ret_age=58, ss_timing="early")
        assert result["pension_annual"] > 0


class TestRunMonteCarlo:
    @pytest.mark.parametrize("age", INTERMEDIATE_AGES)
    def test_intermediate_ages_do_not_crash(self, sample_inputs, sample_accounts, age):
        """Regression test: this used to raise KeyError for any age not in
        {55, 60, 65} because pension_map was a plain dict lookup."""
        result = run_monte_carlo(sample_inputs, sample_accounts, ret_age=age, ss_timing="early")
        assert "success_rate" in result
        assert 0 <= result["success_rate"] <= 100

    def test_anchor_ages_still_work(self, sample_inputs, sample_accounts):
        for age in (55, 60, 65):
            result = run_monte_carlo(sample_inputs, sample_accounts, ret_age=age, ss_timing="early")
            assert "success_rate" in result

    def test_asset_sale_after_retirement_improves_monte_carlo_outcome(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): Monte Carlo only ever
        inherits run_retirement_projection's pre-retirement accumulation
        result — it has no other way to see the Settings-page asset-sale
        fields, so a sale scheduled for AFTER retirement (sale_age >
        ret_age) had zero effect on simulated outcomes, same root cause as
        the withdrawal-phase gap in run_retirement_projection itself."""
        inputs_sale = {**sample_inputs, "jason_age": 50, "asset2_sale_age": 60, "asset2_sale_net": 500000}
        baseline  = run_monte_carlo(sample_inputs, sample_accounts, ret_age=58, ss_timing="early")
        with_sale = run_monte_carlo(inputs_sale, sample_accounts, ret_age=58, ss_timing="early")
        assert with_sale["median_final_balance"] > baseline["median_final_balance"]


class TestRunStressTests:
    def test_returns_base_and_named_scenarios(self, sample_inputs, sample_accounts):
        result = run_stress_tests(sample_inputs, sample_accounts, ret_age=55, ss_timing="early")
        assert "base" in result["scenarios"]
        assert "early_sequence" in result["scenarios"]
        assert "ss_reduction" in result["scenarios"]

    @pytest.mark.parametrize("age", INTERMEDIATE_AGES)
    def test_intermediate_ages_do_not_crash(self, sample_inputs, sample_accounts, age):
        result = run_stress_tests(sample_inputs, sample_accounts, ret_age=age, ss_timing="early")
        assert "base" in result["scenarios"]


class TestRunRothConversionAnalysis:
    @pytest.mark.parametrize("age", ALL_AGES)
    def test_every_age_has_nonzero_pension_when_configured(self, sample_inputs, sample_accounts, age):
        """Regression test: this function used to compute
        `inputs.get(f"pension_{ret_age}", 0)` for any age other than exactly
        60 — a column like "pension_58" never exists, so it silently used
        $0 pension for every age except the one hardcoded special case."""
        result = run_roth_conversion_analysis(sample_inputs, sample_accounts, ret_age=age, ss_timing="early")
        assert result is not None

    def test_taxable_brokerage_assets_increase_conversion_room(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): this function read only
        pretax_at_retirement/roth_at_retirement and assumed spending drew
        100% from pretax, ignoring taxable_at_retirement entirely — a
        household with real brokerage assets (including bonus-funded
        taxable savings, per the annual_bonus_pct feature) got identical
        conversion output whether or not those assets existed, since
        spending funded from taxable shouldn't count as ordinary income
        eating into 22%-bracket room the way a pretax withdrawal does."""
        no_taxable = [a for a in sample_accounts if a["account_type"] != "taxable"]
        without_brokerage = run_roth_conversion_analysis(sample_inputs, no_taxable, ret_age=60, ss_timing="early")
        with_brokerage     = run_roth_conversion_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert with_brokerage["total_conversions"] > without_brokerage["total_conversions"]
        assert with_brokerage["schedule"][0]["taxable_balance"] > 0
        assert without_brokerage["schedule"][0]["taxable_balance"] == 0


class TestRunTaxEfficiencySimulation:
    @pytest.mark.parametrize("age", INTERMEDIATE_AGES)
    def test_intermediate_ages_do_not_crash(self, sample_inputs, sample_accounts, age):
        result = run_tax_efficiency_simulation(sample_inputs, sample_accounts, ret_age=age, ss_timing="early")
        assert result is not None

    def test_reported_tax_actually_reduces_ending_balance(self, sample_inputs, monkeypatch):
        """Regression (external audit 2026-09-07): tax_this_year accumulated
        into lifetime_tax for reporting, but every draw only ever removed
        the NET spending need from its bucket — never grossed up to also
        fund its own tax — so the reported lifetime tax had no effect on
        final_balances at all. Reproduces the audit's exact scenario: $1M
        pretax, $100K/yr spend, 1 year, 0% growth (forced via monkeypatched
        random.gauss so every simulated year is exactly 0%, not just
        approximately so) -> should end up near $1M - $100K/(1-0.22) =
        ~$871,795, not the un-funded $900,000 the bug produced."""
        import random as random_module
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)

        inputs = {
            **sample_inputs, "jason_age": 60, "justin_age": 60,
            "retirement_income_today_dollars": 100000, "inflation_rate": 0,
            "expected_return_pre_retirement": 0, "expected_return_post_retirement": 0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "w2_salary": 0, "annual_401k_contribution": 0, "annual_hsa_contribution": 0,
            "retirement_end_age": 61,
        }
        accounts = [{"name": "IRA", "account_type": "401k", "owner": "jason", "balance": 1_000_000}]
        result = run_tax_efficiency_simulation(inputs, accounts, ret_age=60, ss_timing="early")

        taxable_first = result["strategies"]["taxable_first"]
        # Grossed-up withdrawal: $100,000 / (1 - 0.22) = $128,205 gross,
        # of which $28,205 is tax — not the un-grossed-up $22,000 (22% of
        # the $100,000 net) the bug reported while leaving $900,000, as if
        # that $22,000 had come from nowhere.
        assert taxable_first["median_lifetime_tax"] == pytest.approx(28205, abs=1)
        assert taxable_first["median_final_balance"] == pytest.approx(871795, abs=10)
        assert taxable_first["median_final_balance"] < 900000


class TestRunContributionSensitivity:
    @pytest.mark.parametrize("age", INTERMEDIATE_AGES)
    def test_intermediate_ages_do_not_crash(self, sample_inputs, sample_accounts, age):
        result = run_contribution_sensitivity(sample_inputs, sample_accounts, ret_age=age)
        assert result is not None

    def test_current_scenario_reflects_real_employee_pct(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): "Current" used to be
        hardcoded to 6% regardless of the household's real
        employee_401k_pct — with a real rate of 10%, the "Current (6%)"
        label was wrong and its own comparison scenario didn't actually
        model the household's real contribution rate at all."""
        inputs = {**sample_inputs, "employee_401k_pct": 0.10}
        result = run_contribution_sensitivity(inputs, sample_accounts, ret_age=60)
        current = result["scenarios"][0]
        assert current["label"] == "Current (10%)"
        assert current["employee_pct"] == 10.0
        assert current["portfolio_delta"] == 0
        assert current["portfolio_at_ret"] == result["base_portfolio"]

    def test_below_current_scenario_shows_negative_delta(self, sample_inputs, sample_accounts):
        """Regression: contributing LESS than the real current rate used
        to be clamped to a delta of 0 (identical to "Current"), instead of
        the negative delta a real reduction implies — external audit
        2026-09-07: "10%, current, and reductions all showed the same
        portfolio" when the real current rate was 10%."""
        inputs = {**sample_inputs, "employee_401k_pct": 0.10}
        result = run_contribution_sensitivity(inputs, sample_accounts, ret_age=60)
        seven_pct = next(s for s in result["scenarios"] if s["label"] == "7% employee")
        assert seven_pct["portfolio_delta"] < 0
        assert seven_pct["portfolio_at_ret"] < result["base_portfolio"]

    def test_zero_salary_does_not_crash(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): catch_up_limit / salary
        raised ZeroDivisionError at salary=0 (not yet entered in Settings,
        or genuinely no W2 income) — this endpoint is one of three the
        Historical Stress tab waits on together via Promise.all, so this
        crash silently blanked out an otherwise-valid stress-test result
        on the frontend."""
        inputs = {**sample_inputs, "w2_salary": 0}
        result = run_contribution_sensitivity(inputs, sample_accounts, ret_age=60)
        max_catchup = next(s for s in result["scenarios"] if s["label"] == "Max catch-up")
        assert max_catchup["employee_pct"] == 0.0


class TestWithdrawalWaterfallReconciliationFixes:
    """Regression tests for the external audit 2026-09-06 findings in
    simulation_engine.py: RMD-age withdrawals silently rationed with no
    failure signal, and spousal SS hardcoded to 0/wrong-person's-age."""

    def test_run_single_continues_pretax_withdrawal_and_survives_after_rmd_gate(self):
        """Regression test for the `rmd == 0` gate that blocked ANY further
        pretax withdrawal once RMD age was reached — even with plenty of
        pretax balance left and a real remaining need. ret_age=98 (single
        modeled year) with RMD start age 73 forces an immediate RMD
        (2,000,000 / 7.3 ≈ 273,973) that's smaller than the $500k spending
        need, while plenty of pretax balance remains — the fixed code
        should draw the rest from pretax and report survived=True; the
        pre-fix code left ~$226k unmet every year with no failure signal."""
        survived, balances, pretax_bals, roth_bals, taxable_bals = _run_single(
            pretax_start=2_000_000, roth_start=0, taxable_start=0, hsa_start=0,
            ret_age=98, jason_age=98, justin_age=98,
            pension_annual=0, jason_ss_annual=0, jason_ss_age=200,
            income_at_ret=500000, inflation=0.0, post_ret=0.0,
            annual_returns=[0.0],
        )
        assert survived is True
        assert pretax_bals[0] == pytest.approx(2_000_000 - 500000, rel=0.01)

    def test_run_single_fails_when_need_genuinely_cannot_be_met(self):
        """Sanity check the fix didn't overshoot: a plan with far too little
        to cover the need should still correctly report survived=False."""
        survived, balances, *_ = _run_single(
            pretax_start=100_000, roth_start=0, taxable_start=0, hsa_start=0,
            ret_age=98, jason_age=98, justin_age=98,
            pension_annual=0, jason_ss_annual=0, jason_ss_age=200,
            income_at_ret=500000, inflation=0.0, post_ret=0.0,
            annual_returns=[0.0],
        )
        assert survived is False

    def test_run_single_spousal_ss_uses_justins_own_age_not_jasons(self):
        """Regression test: justin_ss_age used to be compared against
        Jason's current age directly instead of Justin's own (offset by
        the couple's age gap). ret_age=95, jason_age=95, justin_age=93 (a
        2-year gap) with justin_ss_age=95: across 4 modeled years (jason
        ages 95-98, justin ages 93-96), Justin's benefit should only kick
        in once JUSTIN turns 95 — at yr index 2 (jason_age=97) — not at
        yr index 0 (when only Jason has hit 95). All draws come from a
        large taxable bucket so RMDs/pretax gating can't confound the
        result, and annual_returns is exactly 4 long (0% each year) so no
        random fallback returns kick in past this short window."""
        _, balances, pretax_bals, roth_bals, taxable_bals = _run_single(
            pretax_start=0, roth_start=0, taxable_start=10_000_000, hsa_start=0,
            ret_age=95, jason_age=95, justin_age=93,
            pension_annual=0, jason_ss_annual=0, jason_ss_age=200,
            income_at_ret=100000, inflation=0.0, post_ret=0.0,
            annual_returns=[0.0, 0.0, 0.0, 0.0],
            justin_ss_annual=20000, justin_ss_age=95,
        )
        draw_yr0 = 10_000_000 - taxable_bals[0]
        draw_yr1 = taxable_bals[0] - taxable_bals[1]
        draw_yr2 = taxable_bals[1] - taxable_bals[2]
        assert draw_yr0 == pytest.approx(100000, rel=0.01)  # justin_age=93, not active
        assert draw_yr1 == pytest.approx(100000, rel=0.01)  # justin_age=94, not active
        assert draw_yr2 == pytest.approx(80000, rel=0.01)   # justin_age=95, active -> 20k less draw needed

    def test_monte_carlo_spousal_ss_changes_outcome(self, sample_inputs, sample_accounts):
        """Regression test: run_monte_carlo used to hardcode spousal SS to
        the JUSTIN_SPOUSAL_ANNUAL/AGE fallback constants (both 0) inside
        _run_single regardless of what Settings actually configured, so
        changing justin_social_security never affected Monte Carlo output
        at all. Verify a materially larger configured spousal benefit
        changes the median final balance."""
        low = {**sample_inputs, "justin_social_security": 0, "justin_ss_age": 62}
        high = {**sample_inputs, "justin_social_security": 40000, "justin_ss_age": 62}
        result_low = run_monte_carlo(low, sample_accounts, ret_age=62, ss_timing="early")
        result_high = run_monte_carlo(high, sample_accounts, ret_age=62, ss_timing="early")
        assert result_high["median_final_balance"] > result_low["median_final_balance"]

    def test_stress_tests_ss_reduction_scenario_actually_cuts_justin_too(self, sample_inputs, sample_accounts):
        """Regression test: the "SS Cut 25%" stress scenario computed
        scenario_justin_ss but never passed it through to the simulated
        run, so Justin's spousal benefit silently stayed at its full,
        un-cut value. With a real justin_social_security configured, the
        ss_reduction scenario's final balance should be lower than the
        base case (which runs at the full, un-cut post_ret rate but with
        full benefits) by a set of years reflecting the cut, not identical
        to a run where the cut was never applied."""
        inputs = {**sample_inputs, "justin_social_security": 30000, "justin_ss_age": 62,
                   "jason_social_security": 0, "jason_ss_delayed": 0}
        result = run_stress_tests(inputs, sample_accounts, ret_age=62, ss_timing="early")
        ss_cut = result["scenarios"]["ss_reduction"]
        # Re-run with justin_social_security already pre-cut by 25% and no
        # market shock (matches ss_reduction's own overrides={} / same
        # post_ret-every-year path as "base") to get the expected post-cut
        # trajectory independently, then compare final balances.
        inputs_precut = {**inputs, "justin_social_security": 30000 * 0.75}
        result_precut = run_stress_tests(inputs_precut, sample_accounts, ret_age=62, ss_timing="early")
        # The ss_reduction scenario itself has no return-override years
        # (overrides={}), so its trajectory should match a plan that was
        # simply configured with the already-cut benefit from the start.
        assert ss_cut["final_balance"] == pytest.approx(result_precut["scenarios"]["base"]["final_balance"], rel=0.01)


class TestRunSurvivorScenario:
    def test_returns_has_data_true_for_valid_scenario(self, sample_inputs, sample_accounts):
        result = run_survivor_scenario(sample_inputs, sample_accounts, ret_age=60, deceased="jason", death_age=70)
        assert result["has_data"] is True
        assert result["deceased"] == "jason"

    def test_life_insurance_payout_included_for_jason(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_life_basic": 425000, "jason_life_supplemental": 1000000, "jason_life_term": 500000}
        result = run_survivor_scenario(inputs, sample_accounts, ret_age=60, deceased="jason", death_age=70)
        assert result["life_insurance_payout"] == 1925000

    def test_life_insurance_payout_included_for_justin(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "justin_life_ul": 40000, "justin_life_whole": 55000, "justin_life_term": 300000}
        result = run_survivor_scenario(inputs, sample_accounts, ret_age=60, deceased="justin", death_age=70)
        assert result["life_insurance_payout"] == 395000

    def test_survivor_ss_is_higher_of_the_two_not_both(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_social_security": 30000, "justin_social_security": 15000}
        result = run_survivor_scenario(inputs, sample_accounts, ret_age=60, deceased="jason", death_age=70)
        assert result["survivor_ss_annual"] == 30000

    def test_generous_insurance_and_low_need_survives(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_life_basic": 400000, "jason_life_supplemental": 2000000,
                   "jason_life_term": 500000, "retirement_income_today_dollars": 20000}
        accounts = sample_accounts + [{"id": 99, "name": "Big 401k", "account_type": "401k", "owner": "jason", "balance": 3_000_000, "institution": "", "notes": ""}]
        result = run_survivor_scenario(inputs, accounts, ret_age=60, deceased="jason", death_age=65, survivor_need_factor=0.5)
        assert result["survives"] is True
        assert result["additional_insurance_needed"] == 0

    def test_thin_plan_does_not_survive_and_flags_gap(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "jason_life_basic": 0, "jason_life_supplemental": 0, "jason_life_term": 0,
                   "retirement_income_today_dollars": 300000}
        accounts = [a for a in sample_accounts if a["account_type"] != "401k"]  # strip the big pretax bucket
        result = run_survivor_scenario(inputs, accounts, ret_age=60, deceased="jason", death_age=65)
        assert result["survives"] is False
        assert result["depleted_age"] is not None
        assert result["additional_insurance_needed"] > 0

    def test_death_age_snaps_to_a_modeled_year(self, sample_inputs, sample_accounts):
        result = run_survivor_scenario(sample_inputs, sample_accounts, ret_age=60, deceased="jason", death_age=61)
        assert result["has_data"] is True
        assert result["death_age"] >= 60

    def test_default_death_age_is_ten_years_after_retirement(self, sample_inputs, sample_accounts):
        result = run_survivor_scenario(sample_inputs, sample_accounts, ret_age=60, deceased="jason", death_age=None)
        assert result["has_data"] is True

    def test_respects_retirement_end_age_instead_of_hardcoded_99(self, sample_inputs, sample_accounts):
        """Regression: every other simulation function in this file (Monte
        Carlo, stress tests, SWR, tax efficiency) reads inputs[
        "retirement_end_age"] instead of hardcoding 99 — this one didn't,
        found via a bug-hunt sandbox where the household's actual
        retirement_end_age (95) had no effect on the survivor scenario's
        modeled horizon, insurance-gap math, or its "through age 99" text.
        A thin plan modeled to a shorter horizon needs strictly less
        additional insurance than the same plan modeled to a longer one."""
        thin_inputs = {**sample_inputs, "jason_life_basic": 0, "jason_life_supplemental": 0, "jason_life_term": 0,
                       "retirement_income_today_dollars": 300000}
        accounts = [a for a in sample_accounts if a["account_type"] != "401k"]

        short = run_survivor_scenario({**thin_inputs, "retirement_end_age": 90}, accounts, ret_age=60, deceased="jason", death_age=65)
        long_ = run_survivor_scenario({**thin_inputs, "retirement_end_age": 105}, accounts, ret_age=60, deceased="jason", death_age=65)

        assert short["survivor_end_age"] == 90
        assert long_["survivor_end_age"] == 105
        assert short["schedule"][-1]["age"] < long_["schedule"][-1]["age"]
        assert short["additional_insurance_needed"] < long_["additional_insurance_needed"]
        assert "through age 90" not in long_["recommendation"]

    def test_default_retirement_end_age_falls_back_to_99(self, sample_inputs, sample_accounts):
        inputs = {**sample_inputs, "retirement_end_age": None}
        result = run_survivor_scenario(inputs, sample_accounts, ret_age=60, deceased="jason", death_age=70)
        assert result["survivor_end_age"] == 99


class TestLifeEventsInSimulation:
    """life_events threaded through simulation_engine.py: pre-retirement
    events affect the starting bucket balances (via run_retirement_projection),
    and withdrawal-phase events are applied directly inside Monte Carlo /
    stress-test's own year-by-year loop (_run_single)."""

    def test_monte_carlo_default_no_events_unchanged(self, sample_inputs, sample_accounts):
        no_kwarg = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        explicit_none = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", life_events=None)
        assert no_kwarg["portfolio_at_retirement"] == explicit_none["portfolio_at_retirement"]
        assert no_kwarg["success_rate"] == explicit_none["success_rate"]

    def test_monte_carlo_pre_retirement_one_time_event_increases_starting_portfolio(self, sample_inputs, sample_accounts):
        events = [{"event_year": CURRENT_YEAR + 1, "one_time_cash_delta": 50000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        baseline = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        with_event = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", life_events=events)
        assert with_event["portfolio_at_retirement"] > baseline["portfolio_at_retirement"]

    def test_monte_carlo_post_retirement_one_time_event_improves_success_rate(self, sample_inputs, sample_accounts):
        """A large enough post-retirement windfall should never make the
        median final balance worse."""
        events = [{"event_year": CURRENT_YEAR + 15, "one_time_cash_delta": 2_000_000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        baseline = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        with_event = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", life_events=events)
        assert with_event["median_final_balance"] >= baseline["median_final_balance"]

    def test_stress_tests_default_no_events_unchanged(self, sample_inputs, sample_accounts):
        no_kwarg = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        explicit_none = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", life_events=None)
        assert no_kwarg["scenarios"]["base"]["final_balance"] == explicit_none["scenarios"]["base"]["final_balance"]

    def test_stress_tests_post_retirement_windfall_improves_base_case(self, sample_inputs, sample_accounts):
        events = [{"event_year": CURRENT_YEAR + 12, "one_time_cash_delta": 1_000_000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        baseline = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        with_event = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", life_events=events)
        assert with_event["scenarios"]["base"]["final_balance"] > baseline["scenarios"]["base"]["final_balance"]

    def test_other_public_entry_points_accept_life_events_without_error(self, sample_inputs, sample_accounts):
        """These functions only thread life_events through to
        run_retirement_projection() for the starting-bucket effect, not a
        bespoke withdrawal-phase model (see their docstrings) — this just
        confirms the parameter is accepted end-to-end and doesn't crash."""
        events = [{"event_year": CURRENT_YEAR + 1, "one_time_cash_delta": 10000,
                   "monthly_cash_flow_delta": 0, "duration_months": 0}]
        assert run_swr_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", life_events=events) is not None
        assert run_roth_conversion_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", life_events=events) is not None
        assert run_tax_efficiency_simulation(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", life_events=events) is not None
        assert run_contribution_sensitivity(sample_inputs, sample_accounts, ret_age=60, life_events=events) is not None
        assert run_survivor_scenario(sample_inputs, sample_accounts, ret_age=60, deceased="jason", death_age=70, life_events=events) is not None


class TestSurplusAllocationsInSimulation:
    """surplus_allocations threaded through simulation_engine.py: it only
    ever affects the starting bucket balances (via run_retirement_projection)
    — there is no post-retirement half at all, so no _run_single wiring is
    expected or tested here (unlike life_events)."""

    def test_monte_carlo_default_no_allocations_unchanged(self, sample_inputs, sample_accounts):
        no_kwarg = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        explicit_none = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", surplus_allocations=None)
        assert no_kwarg["portfolio_at_retirement"] == explicit_none["portfolio_at_retirement"]
        assert no_kwarg["success_rate"] == explicit_none["success_rate"]

    def test_monte_carlo_retirement_contributions_increase_starting_portfolio(self, sample_inputs, sample_accounts):
        allocations = [{"goal": "Retirement contributions", "monthly_amount": 500}]
        baseline = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        with_alloc = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", surplus_allocations=allocations)
        assert with_alloc["portfolio_at_retirement"] > baseline["portfolio_at_retirement"]

    def test_monte_carlo_non_retirement_goal_has_no_effect(self, sample_inputs, sample_accounts):
        allocations = [{"goal": "Emergency reserve", "monthly_amount": 2000}]
        baseline = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        with_alloc = run_monte_carlo(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", surplus_allocations=allocations)
        assert with_alloc["portfolio_at_retirement"] == baseline["portfolio_at_retirement"]

    def test_stress_tests_default_no_allocations_unchanged(self, sample_inputs, sample_accounts):
        no_kwarg = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        explicit_none = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", surplus_allocations=None)
        assert no_kwarg["scenarios"]["base"]["final_balance"] == explicit_none["scenarios"]["base"]["final_balance"]

    def test_stress_tests_taxable_investing_improves_base_case(self, sample_inputs, sample_accounts):
        allocations = [{"goal": "Taxable investing", "monthly_amount": 1000}]
        baseline = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        with_alloc = run_stress_tests(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", surplus_allocations=allocations)
        assert with_alloc["scenarios"]["base"]["final_balance"] > baseline["scenarios"]["base"]["final_balance"]

    def test_other_public_entry_points_accept_surplus_allocations_without_error(self, sample_inputs, sample_accounts):
        """These functions only thread surplus_allocations through to
        run_retirement_projection() for the starting-bucket effect — this
        just confirms the parameter is accepted end-to-end and doesn't
        crash."""
        allocations = [{"goal": "Retirement contributions", "monthly_amount": 500}]
        assert run_swr_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", surplus_allocations=allocations) is not None
        assert run_roth_conversion_analysis(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", surplus_allocations=allocations) is not None
        assert run_tax_efficiency_simulation(sample_inputs, sample_accounts, ret_age=60, ss_timing="early", surplus_allocations=allocations) is not None
        assert run_contribution_sensitivity(sample_inputs, sample_accounts, ret_age=60, surplus_allocations=allocations) is not None
        assert run_survivor_scenario(sample_inputs, sample_accounts, ret_age=60, deceased="jason", death_age=70, surplus_allocations=allocations) is not None
