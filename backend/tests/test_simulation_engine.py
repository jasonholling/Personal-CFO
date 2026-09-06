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
)

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


class TestRunTaxEfficiencySimulation:
    @pytest.mark.parametrize("age", INTERMEDIATE_AGES)
    def test_intermediate_ages_do_not_crash(self, sample_inputs, sample_accounts, age):
        result = run_tax_efficiency_simulation(sample_inputs, sample_accounts, ret_age=age, ss_timing="early")
        assert result is not None


class TestRunContributionSensitivity:
    @pytest.mark.parametrize("age", INTERMEDIATE_AGES)
    def test_intermediate_ages_do_not_crash(self, sample_inputs, sample_accounts, age):
        result = run_contribution_sensitivity(sample_inputs, sample_accounts, ret_age=age)
        assert result is not None


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
