"""Tests for retirement_tools_engine.py — RMD planning, pension vs lump
sum, and backdoor Roth eligibility."""
import pytest

from retirement_tools_engine import (
    marginal_rate,
    run_rmd_planning,
    pension_vs_lump_sum,
    backdoor_roth_eligibility,
    qcd_planner,
    hsa_stealth_ira_strategy,
    ROTH_MAGI_PHASEOUT_MFJ,
    QCD_ANNUAL_LIMIT_2026,
    QCD_MIN_AGE,
)


class TestMarginalRate:
    def test_low_income_lowest_bracket(self):
        assert marginal_rate(10000) == 0.10

    def test_top_bracket(self):
        assert marginal_rate(1_000_000) == 0.37

    def test_at_a_boundary(self):
        assert marginal_rate(24800) == 0.10
        assert marginal_rate(24801) == 0.12


class TestRunRmdPlanning:
    def test_no_pretax_balance(self, sample_inputs):
        result = run_rmd_planning(sample_inputs, [{"account_type": "checking", "balance": 1000}])
        assert result == {"has_pretax_balance": False}

    def test_projects_and_schedules_rmds(self, sample_inputs, sample_accounts):
        result = run_rmd_planning(sample_inputs, sample_accounts)
        assert result["has_pretax_balance"] is True
        assert result["projected_balance_at_start_age"] > result["pretax_balance_today"]
        assert result["first_rmd_amount"] > 0
        assert result["lifetime_rmd_total"] > result["first_rmd_amount"]
        assert len(result["schedule"]) > 0

    def test_bracket_jump_detection_with_large_balance(self, sample_inputs):
        accounts = [{"account_type": "401k", "balance": 5_000_000, "owner": "jason"}]
        inputs = {**sample_inputs, "pension_65": 0, "jason_social_security": 0, "justin_social_security": 0}
        result = run_rmd_planning(inputs, accounts)
        assert result["bracket_jump"] is True

    def test_no_bracket_jump_when_already_in_top_bracket(self, sample_inputs):
        accounts = [{"account_type": "401k", "balance": 10000, "owner": "jason"}]
        inputs = {**sample_inputs, "pension_65": 900000, "jason_social_security": 0, "justin_social_security": 0}
        result = run_rmd_planning(inputs, accounts)
        assert result["bracket_jump"] is False

    def test_recommendation_mentions_roth_when_bracket_jumps(self, sample_inputs):
        accounts = [{"account_type": "401k", "balance": 5_000_000, "owner": "jason"}]
        inputs = {**sample_inputs, "pension_65": 0, "jason_social_security": 0, "justin_social_security": 0}
        result = run_rmd_planning(inputs, accounts)
        assert "Roth" in result["recommendation"]


class TestPensionVsLumpSum:
    def test_generous_lump_sum_favors_lump_sum(self):
        result = pension_vs_lump_sum(
            monthly_pension=1000, lump_sum=500000, current_age=60,
            pension_start_age=65, life_expectancy_age=85, discount_rate=0.06,
        )
        assert result["favors"] == "lump_sum"

    def test_stingy_lump_sum_favors_pension(self):
        result = pension_vs_lump_sum(
            monthly_pension=3000, lump_sum=50000, current_age=60,
            pension_start_age=65, life_expectancy_age=90, discount_rate=0.06,
        )
        assert result["favors"] == "pension"

    def test_implied_rate_is_reasonable(self):
        result = pension_vs_lump_sum(
            monthly_pension=3000, lump_sum=400000, current_age=55,
            pension_start_age=65, life_expectancy_age=90, discount_rate=0.06,
        )
        assert 0 < result["implied_discount_rate_pct"] < 30

    def test_zero_pension_returns_none_implied_rate(self):
        result = pension_vs_lump_sum(
            monthly_pension=0, lump_sum=100000, current_age=60,
            pension_start_age=65, life_expectancy_age=90,
        )
        assert result["implied_discount_rate_pct"] is None


class TestBackdoorRothEligibility:
    def test_below_phaseout_is_directly_eligible(self):
        result = backdoor_roth_eligibility(magi=ROTH_MAGI_PHASEOUT_MFJ[0] - 10000)
        assert result["direct_roth_eligible"] is True
        assert result["needs_backdoor"] is False

    def test_above_phaseout_needs_backdoor(self):
        result = backdoor_roth_eligibility(magi=ROTH_MAGI_PHASEOUT_MFJ[1] + 10000)
        assert result["direct_roth_eligible"] is False
        assert result["needs_backdoor"] is True

    def test_clean_backdoor_no_existing_ira_is_fully_nontaxable(self):
        result = backdoor_roth_eligibility(
            magi=300000, existing_traditional_ira_balance=0, existing_traditional_ira_basis=0,
            planned_contribution=7000,
        )
        assert result["pro_rata_applies"] is False
        assert result["taxable_amount_of_conversion"] == 0

    def test_pro_rata_rule_applies_with_existing_pretax_ira(self):
        result = backdoor_roth_eligibility(
            magi=300000, existing_traditional_ira_balance=50000, existing_traditional_ira_basis=0,
            planned_contribution=7000,
        )
        assert result["pro_rata_applies"] is True
        # 7000 basis / 57000 total = 12.28% nontaxable
        assert result["nontaxable_fraction"] == pytest.approx(0.123, abs=0.001)
        assert result["taxable_amount_of_conversion"] == pytest.approx(6140, abs=5)

    def test_partial_phaseout_percentage(self):
        low, high = ROTH_MAGI_PHASEOUT_MFJ
        midpoint = (low + high) / 2
        result = backdoor_roth_eligibility(magi=midpoint)
        assert result["phaseout_pct"] == pytest.approx(0.5, abs=0.01)


class TestQcdPlanner:
    def test_not_eligible_under_min_age(self):
        result = qcd_planner(age=65, ira_balance=500000, rmd_amount=0, desired_qcd_amount=10000)
        assert result["eligible"] is False

    def test_eligible_at_min_age(self):
        result = qcd_planner(age=QCD_MIN_AGE, ira_balance=500000, rmd_amount=0, desired_qcd_amount=10000)
        assert result["eligible"] is True

    def test_qcd_capped_at_annual_limit(self):
        result = qcd_planner(age=75, ira_balance=5_000_000, rmd_amount=0,
                              desired_qcd_amount=QCD_ANNUAL_LIMIT_2026 + 50000)
        assert result["qcd_amount"] == QCD_ANNUAL_LIMIT_2026

    def test_qcd_capped_at_ira_balance(self):
        result = qcd_planner(age=75, ira_balance=5000, rmd_amount=0, desired_qcd_amount=50000)
        assert result["qcd_amount"] == 5000

    def test_qcd_satisfies_rmd_dollar_for_dollar_up_to_rmd(self):
        result = qcd_planner(age=75, ira_balance=500000, rmd_amount=20000, desired_qcd_amount=15000)
        assert result["rmd_satisfied_by_qcd"] == 15000
        assert result["remaining_taxable_rmd"] == 5000

    def test_qcd_exceeding_rmd_fully_satisfies_it(self):
        result = qcd_planner(age=75, ira_balance=500000, rmd_amount=10000, desired_qcd_amount=25000)
        assert result["rmd_satisfied_by_qcd"] == 10000
        assert result["remaining_taxable_rmd"] == 0

    def test_zero_desired_amount_no_crash(self):
        result = qcd_planner(age=75, ira_balance=500000, rmd_amount=10000, desired_qcd_amount=0)
        assert result["qcd_amount"] == 0

    def test_tax_savings_uses_marginal_rate(self):
        result = qcd_planner(age=75, ira_balance=500000, rmd_amount=0, desired_qcd_amount=10000,
                              other_taxable_income=10000)  # 10% bracket
        assert result["marginal_rate"] == 0.10
        assert result["estimated_tax_savings"] == 1000


class TestHsaStealthIraStrategy:
    def test_no_expense_returns_has_expense_false(self):
        assert hsa_stealth_ira_strategy(oop_expense_this_year=0, years_to_delay=10) == {"has_expense": False}

    def test_computes_future_value_and_extra_value(self):
        result = hsa_stealth_ira_strategy(oop_expense_this_year=1000, years_to_delay=10, growth_rate=0.07)
        assert result["has_expense"] is True
        assert result["future_value_if_delayed"] == round(1000 * 1.07**10)
        assert result["extra_value_from_delaying"] == result["future_value_if_delayed"] - 1000

    def test_zero_years_delay_has_no_extra_value(self):
        result = hsa_stealth_ira_strategy(oop_expense_this_year=1000, years_to_delay=0)
        assert result["extra_value_from_delaying"] == 0
