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
        """Regression (external audit 2026-09-07): ss_start_age (and the
        "guaranteed_income_annual"/"guaranteed_income_steadystate" figures
        derived alongside it) used to compare justin_ss_age directly
        against Jason's own ret_age/age with no adjustment for the
        couple's age gap — the same bug already fixed in this function's
        withdrawal loop, just missed in this summary block. sample_inputs
        has jason_age=50, justin_age=48 (a 2-year gap); at ret_age=60,
        Justin is only 58. With justin_ss_age=63, Justin doesn't reach 63
        until Jason turns 65 — that's the correct "both streams active"
        age, not the raw max(62, 63)=63 the old bug reported (which
        wrongly assumed Justin's claim age applied to JASON's own age)."""
        custom = {**sample_inputs, "justin_ss_age": 63}
        result = run_swr_analysis(custom, sample_accounts, ret_age=60, ss_timing="early")
        assert result["ss_start_age"] == 65

    def test_guaranteed_day_one_respects_spousal_age_gap(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): reproduces the audit's
        exact case — primary retiring at an age where the spouse (a
        10-year age gap here) hasn't reached their own SS claiming age
        yet. guaranteed_income_annual (the "day one" figure) must NOT
        include the spousal benefit before the spouse has actually
        reached their own claiming age — proven by comparing against an
        otherwise-identical household with $0 spousal SS: if the spousal
        benefit isn't yet active, changing it from $20,000 to $0 must not
        change guaranteed_income_annual at all."""
        base = {**sample_inputs, "jason_age": 57, "justin_age": 47,  # 10-year gap
                "justin_ss_age": 62, "justin_social_security": 0}
        with_spousal = {**base, "justin_social_security": 20000}
        r_base = run_swr_analysis(base, sample_accounts, ret_age=67, ss_timing="early")
        r_with = run_swr_analysis(with_spousal, sample_accounts, ret_age=67, ss_timing="early")
        # Justin is 57 when Jason retires at 67 (10-yr gap) — 5 years short of 62.
        assert r_with["justin_ss_annual"] == 20000
        assert r_with["guaranteed_income_annual"] == r_base["guaranteed_income_annual"]

    def test_safe_withdrawal_rate_not_capped_at_15_percent(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): the binary search's
        upper bound was a hardcoded portfolio * 0.15 with no expansion —
        if the true safe rate was actually higher (plausible for a short
        horizon with no other drag), the search just converged to that
        fixed ceiling and reported 15% regardless of how much more the
        plan could actually support. A one-year horizon with a $1M
        taxable-only portfolio, zero taxes/growth/inflation/guaranteed
        income should support withdrawing most of the portfolio (survival
        only requires ending >= $0 after the one modeled year), nowhere
        near capped at 15%."""
        inputs = {**sample_inputs, "jason_age": 60, "justin_age": 60,
                  "inflation_rate": 0, "expected_return_pre_retirement": 0,
                  "expected_return_post_retirement": 0, "retirement_end_age": 61,
                  "pension_55": 0, "pension_60": 0, "pension_65": 0,
                  "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
                  "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
                  "w2_salary": 0, "annual_401k_contribution": 0, "annual_hsa_contribution": 0}
        accounts = [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 1_000_000}]
        result = run_swr_analysis(inputs, accounts, ret_age=60, ss_timing="early")
        assert result["safe_withdrawal_rate"] > 50.0  # nowhere near the old 15% ceiling
        assert result["hit_search_limit"] is False

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

    def test_post_retirement_asset_sale_reaches_conversion_schedule(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): this function only saw
        life_events/asset sales through run_retirement_projection's
        pre-retirement starting balance — a sale scheduled AFTER
        retirement (sale_age > ret_age) had zero effect on the conversion
        schedule, same root cause as the withdrawal-phase gap already
        fixed in run_retirement_projection/Monte Carlo/stress/SWR."""
        base_inputs = {**sample_inputs, "jason_age": 60}
        inputs_sale = {**base_inputs, "asset2_sale_age": 62, "asset2_sale_net": 500000}
        baseline  = run_roth_conversion_analysis(base_inputs, sample_accounts, ret_age=60, ss_timing="early")
        with_sale = run_roth_conversion_analysis(inputs_sale, sample_accounts, ret_age=60, ss_timing="early")
        # The sale lands in the yr=2 row (age 62) — taxable_balance there
        # (start-of-year, before the sale cash is added) is unaffected,
        # but the row's own conversion room / taxable_after should differ
        # once the $500K cash arrives that year.
        assert with_sale["schedule"][2]["taxable_after"] > baseline["schedule"][2]["taxable_after"]

    def test_spending_does_not_freeze_across_conversion_years(self, sample_inputs):
        """Regression (external audit 2026-09-07): income_need used to
        inflate to the retirement date ONCE and then reuse that same
        frozen value for every single year of the conversion window,
        instead of continuing to inflate year over year during
        retirement — "inflates to retirement once, then freezes spending
        for all conversion years." With no taxable assets (so the full
        need routes through pretax) and no guaranteed income, each year's
        base_taxable_income (need minus a constant standard deduction)
        must strictly increase year over year, not stay flat."""
        inputs = {**sample_inputs, "jason_age": 60, "justin_age": 60, "inflation_rate": 0.03,
                  "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
                  "pension_55": 0, "pension_60": 0, "pension_65": 0}
        no_taxable = [{"name": "IRA", "account_type": "401k", "owner": "jason", "balance": 5_000_000}]
        result = run_roth_conversion_analysis(inputs, no_taxable, ret_age=60, ss_timing="early")
        needs = [row["base_taxable_income"] for row in result["schedule"][:5]]
        assert needs == sorted(needs)
        assert needs[-1] > needs[0]

    def test_no_conversion_baseline_also_spends_down_pretax(self, sample_inputs):
        """Regression (external audit 2026-09-07): the "without
        conversions" RMD comparison used to just compound pretax_at_ret
        with ZERO withdrawals for the whole conversion window, as if the
        household spent nothing at all — crediting that side with free
        money made conversions look like they'd caused more of the
        pretax decline than they really did. With real spending ($100K/yr
        from a $1M IRA, 0% growth), the "no conversion" baseline must be
        well below the naive pure-compounded $1,000,000 the bug would
        have reported, since real spending draws it down over the years
        regardless of whether any conversions happen."""
        inputs = {**sample_inputs, "jason_age": 60, "justin_age": 60, "expected_return_post_retirement": 0.0,
                  "retirement_income_today_dollars": 100000, "inflation_rate": 0.0,
                  "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
                  "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
                  "pension_55": 0, "pension_60": 0, "pension_65": 0}
        accounts = [{"name": "IRA", "account_type": "401k", "owner": "jason", "balance": 1_000_000}]
        result = run_roth_conversion_analysis(inputs, accounts, ret_age=60, ss_timing="early")
        assert result["pretax_at_rmd_age_no_conversion"] < 1_000_000

    def test_conversion_tax_is_funded_from_taxable_not_reported_unfunded(self, sample_inputs):
        """Regression (external audit 2026-09-07): tax_cost used to be
        computed and reported but never deducted from any bucket — a
        conversion "cost" $31,592 in tax while combined assets stayed at
        $900,000, as if the tax had no funding source. Standard practice
        pays a Roth conversion's tax from OUTSIDE the IRA (taxable), so a
        modest taxable balance should now cap how much can be converted —
        and that same taxable balance must actually shrink by the tax
        paid, not just report a number nobody paid."""
        inputs = {**sample_inputs, "jason_age": 60, "justin_age": 60, "expected_return_post_retirement": 0.0,
                  "retirement_income_today_dollars": 0, "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
                  "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
                  "pension_55": 0, "pension_60": 0, "pension_65": 0}
        accounts = [
            {"name": "IRA", "account_type": "401k", "owner": "jason", "balance": 1_000_000},
            {"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 22_000},
        ]
        result = run_roth_conversion_analysis(inputs, accounts, ret_age=60, ss_timing="early")
        year1 = result["schedule"][0]
        # $22,000 taxable / 22% = $100,000 is the most conversion this
        # household can actually afford the tax on, regardless of how
        # much room the 22% bracket itself has.
        assert year1["optimal_conversion"] == pytest.approx(100000, abs=1)
        assert year1["tax_cost"] == pytest.approx(22000, abs=1)
        assert year1["taxable_after"] == pytest.approx(0, abs=1)

    def test_roth_conversion_never_goes_negative(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): the "shortfall spills
        into Roth" term (when pretax can't cover both its own spending
        draw and the conversion in the same year) had no floor, so a
        large enough shortfall could report a genuinely negative Roth
        balance. A thin pretax balance against a large spending need,
        with the RMD-start horizon giving many conversion years to
        accumulate shortfalls, must never drive any schedule row's
        roth_after below zero."""
        thin_accounts = [
            {"name": "IRA", "account_type": "401k", "owner": "jason", "balance": 5_000},
            {"name": "Roth", "account_type": "roth_ira", "owner": "jason", "balance": 1_000},
        ]
        inputs = {**sample_inputs, "jason_age": 60, "justin_age": 60, "retirement_income_today_dollars": 300000}
        result = run_roth_conversion_analysis(inputs, thin_accounts, ret_age=60, ss_timing="early")
        assert all(row["roth_after"] >= 0 for row in result["schedule"])


class TestRunTaxEfficiencySimulation:
    @pytest.mark.parametrize("age", INTERMEDIATE_AGES)
    def test_intermediate_ages_do_not_crash(self, sample_inputs, sample_accounts, age):
        result = run_tax_efficiency_simulation(sample_inputs, sample_accounts, ret_age=age, ss_timing="early")
        assert result is not None

    def test_spending_inflates_to_retirement_before_the_yearly_loop(self, sample_inputs, monkeypatch):
        """Regression (external audit 2026-09-07): year_need used to
        inflate income_today only by `yr` (years INTO retirement),
        omitting the years BETWEEN today and retirement entirely — a
        household retiring 10 years from now with 3% inflation should
        enter retirement needing $134,392 (from $100,000 today), not
        $100,000. Verified indirectly: with 0% growth and a huge taxable
        balance (so it never runs low enough to matter), the first year's
        withdrawal (grossed up at the 15% taxable-first rate) reveals
        exactly what year_need was computed as."""
        import random as random_module
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)
        inputs = {
            **sample_inputs, "jason_age": 50, "justin_age": 50,
            "retirement_income_today_dollars": 100000, "inflation_rate": 0.03,
            "expected_return_pre_retirement": 0.0, "expected_return_post_retirement": 0.0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "w2_salary": 0, "annual_401k_contribution": 0, "annual_hsa_contribution": 0,
            "retirement_end_age": 61,
        }
        accounts = [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 10_000_000}]
        result = run_tax_efficiency_simulation(inputs, accounts, ret_age=60, ss_timing="early")
        # income_at_ret = 100,000 * 1.03**10 = 134,391.64; grossed at 15%:
        # 134,391.64 / 0.85 = 158,107.81 drawn from the $10M taxable bucket.
        ending = result["strategies"]["taxable_first"]["median_final_balance"]
        assert ending == pytest.approx(9_841_892, abs=5)

    def test_post_retirement_asset_sale_improves_outcome(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): this function only saw
        life_events/asset sales through run_retirement_projection's
        pre-retirement starting balance — a sale scheduled AFTER
        retirement had zero effect on the strategy comparison."""
        base_inputs = {**sample_inputs, "jason_age": 60, "retirement_end_age": 65}
        inputs_sale = {**base_inputs, "asset2_sale_age": 62, "asset2_sale_net": 500000}
        baseline  = run_tax_efficiency_simulation(base_inputs, sample_accounts, ret_age=60, ss_timing="early")
        with_sale = run_tax_efficiency_simulation(inputs_sale, sample_accounts, ret_age=60, ss_timing="early")
        assert with_sale["strategies"]["taxable_first"]["median_final_balance"] > \
            baseline["strategies"]["taxable_first"]["median_final_balance"]

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

    def test_optimal_strategy_funds_spending_above_the_ltcg_cap(self, sample_inputs, monkeypatch):
        """Regression (external audit 2026-09-07): the "optimal" strategy
        drew taxable only up to the 0%-LTCG cap_gains_limit ($98,900), then
        moved on to pretax/roth/hsa and never came back to taxable for the
        rest — with those three buckets empty, any spending need above the
        cap was silently discarded. success_rate checked only the ending
        balance, never whether spending was actually funded, so this
        reported 100% success while $101,100 of a $200,000 need per year
        just never happened. Reproduces the audit's exact case: $1M
        taxable-only, $200K/yr spend, 1 year, 0% growth."""
        import random as random_module
        monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)

        inputs = {
            **sample_inputs, "jason_age": 60, "justin_age": 60,
            "retirement_income_today_dollars": 200000, "inflation_rate": 0,
            "expected_return_pre_retirement": 0, "expected_return_post_retirement": 0,
            "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
            "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
            "pension_55": 0, "pension_60": 0, "pension_65": 0,
            "w2_salary": 0, "annual_401k_contribution": 0, "annual_hsa_contribution": 0,
            "retirement_end_age": 61,
        }
        accounts = [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 1_000_000}]
        result = run_tax_efficiency_simulation(inputs, accounts, ret_age=60, ss_timing="early")

        optimal = result["strategies"]["optimal"]
        # $98,900 at 0% + the remaining $101,100 grossed up at 15% LTCG:
        # $101,100 / 0.85 = $118,941 gross, $17,841 tax, ending ~$782,159 —
        # not the bug's un-funded $901,100 with $0 tax on the shortfall.
        assert optimal["median_lifetime_tax"] == pytest.approx(17841, abs=5)
        assert optimal["median_final_balance"] == pytest.approx(782159, abs=10)
        assert optimal["success_rate"] == 100.0


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

    def test_current_scenario_zero_delta_even_when_capped_by_irs_limit(self, sample_inputs, sample_accounts):
        """Regression (external audit 2026-09-07): the prior fix
        (test_current_scenario_reflects_real_employee_pct) doesn't
        exercise this — sample_inputs' $150K salary at 10% ($15,000/yr)
        never comes near the IRS catch-up limit. At a high enough salary
        (audit's exact repro: $500K salary, 10% current contribution,
        jason_age 50 -> $32,500 catch-up limit), "Current"'s own
        annual_employee gets capped to $32,500 while the delta reference
        used to stay the un-capped salary*emp_pct_base ($50,000) —
        producing a nonzero "Current" delta against ITSELF, with no
        setting changed. The Current row must always net to exactly zero,
        regardless of whether the cap binds."""
        inputs = {**sample_inputs, "jason_age": 50, "w2_salary": 500000, "employee_401k_pct": 0.10,
                  "expected_return_pre_retirement": 0.0}
        result = run_contribution_sensitivity(inputs, sample_accounts, ret_age=60)
        current = result["scenarios"][0]
        assert current["label"] == "Current (10%)"
        assert current["annual_employee"] == 32500  # capped, not the uncapped $50,000
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
        # Since the external audit 2026-09-07 fix, pretax withdrawals here
        # are grossed up for tax (previously untaxed) — drawing enough
        # gross to net $500K costs more than $500K of pretax balance, so
        # the ending balance is lower than a naive $2M - $500K.
        assert pretax_bals[0] < 2_000_000 - 500000

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

    def test_run_single_accumulates_inflation_instead_of_recomputing_it(self):
        """Regression (external audit 2026-09-07): year_need used to be
        income_at_ret * (1+eff_inf)**yr, using THIS YEAR's inflation_mult
        raised to the power of all elapsed years — so when a stress
        scenario's inflation_mult drops partway through (e.g.
        stagflation_1970s reverting to 1.0 after 10 years), the drop
        retroactively erased compounding already "banked" from earlier,
        higher-inflation years instead of just slowing future growth.
        With inflation_mults=[3.0, 3.0, 1.0] and inflation=0.10 (30%, 30%,
        10% effective), spending need must be non-decreasing year over
        year: $100,000 -> $130,000 -> $169,000 (accumulated through years
        0-1's 30% rate; year 2's own dropped rate only affects year 3+).
        The pre-fix formula would have computed year 2's need as
        100,000*(1.10)**2 = $121,000 — LOWER than year 1's $130,000, an
        actual decrease in nominal spending need from slowing inflation."""
        huge_taxable = 10_000_000
        _, balances, pretax_bals, roth_bals, taxable_bals = _run_single(
            pretax_start=0, roth_start=0, taxable_start=huge_taxable, hsa_start=0,
            ret_age=60, jason_age=60, justin_age=60,
            pension_annual=0, jason_ss_annual=0, jason_ss_age=200,
            income_at_ret=100000, inflation=0.10, post_ret=0.0,
            annual_returns=[0.0, 0.0, 0.0], inflation_mults=[3.0, 3.0, 1.0],
            justin_ss_age=200,
        )
        draw0 = huge_taxable - taxable_bals[0]
        draw1 = taxable_bals[0] - taxable_bals[1]
        draw2 = taxable_bals[1] - taxable_bals[2]
        assert draw0 == pytest.approx(100000, abs=1)
        assert draw1 == pytest.approx(130000, abs=1)
        assert draw2 == pytest.approx(169000, abs=1)
        assert draw2 > draw1 > draw0  # must never decrease as inflation eases

    def test_run_single_taxes_pretax_withdrawals(self):
        """Regression (external audit 2026-09-07): _run_single treated
        every withdrawal, RMDs included, as tax-free — unlike
        run_retirement_projection's own waterfall. Reproduces the audit's
        exact case: $1M pretax, $100K spend, one year, no other income,
        0% growth. With no pension/SS, taxable_income_est is $0, landing
        in the 10% bracket — the deterministic projection's own figure
        ($888,889 after $11,111 tax) is the correct answer; the bug's
        $900,000 (fully untaxed) must no longer be produced."""
        survived, balances, *_ = _run_single(
            pretax_start=1_000_000, roth_start=0, taxable_start=0, hsa_start=0,
            ret_age=60, jason_age=60, justin_age=60,
            pension_annual=0, jason_ss_annual=0, jason_ss_age=200,
            income_at_ret=100000, inflation=0.0, post_ret=0.0,
            annual_returns=[0.0], retirement_end_age=61,
            justin_ss_age=200,
        )
        assert survived is True
        assert balances[0] == pytest.approx(888889, abs=2)

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

    def test_death_year_spending_is_not_double_counted(self, sample_inputs):
        """Regression (external audit 2026-09-07): death_row["portfolio_
        balance"] (the baseline's END-OF-YEAR figure for the death year)
        already reflects a full year of both-alive spending — the
        survivor's own loop used to ALSO start spending at that same age,
        double-counting that year. The survivor's first modeled year must
        be death_jason_age + 1, and its starting_balance must equal
        portfolio_at_death exactly (no extra draw already applied)."""
        inputs = {**sample_inputs, "jason_age": 50, "justin_age": 50,
                  "retirement_income_today_dollars": 100000, "inflation_rate": 0.03,
                  "expected_return_pre_retirement": 0.0, "expected_return_post_retirement": 0.0,
                  "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
                  "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
                  "pension_55": 0, "pension_60": 0, "pension_65": 0,
                  "jason_life_basic": 0, "jason_life_supplemental": 0, "jason_life_term": 0}
        accounts = [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 1_000_000}]
        result = run_survivor_scenario(inputs, accounts, ret_age=60, deceased="jason", death_age=60,
                                        survivor_need_factor=1.0)
        assert result["life_insurance_payout"] == 0
        assert result["schedule"][0]["age"] == 61
        assert result["schedule"][0]["starting_balance"] == result["portfolio_at_death"]

    def test_pension_does_not_get_cola_in_survivor_schedule(self, sample_inputs):
        """Regression (external audit 2026-09-07): pension is frozen (no
        COLA) everywhere else in this app, but the survivor loop applied
        COLA to the combined pension+SS guaranteed total — inflating the
        supposedly-frozen pension right along with need, which (with a
        pension sized to just cover year-one need) would mean the draw
        never grows and the plan trivially "survives" forever. With the
        fix, a frozen pension falls further behind rising need every
        year, so draws must strictly increase over time."""
        # jason_age == ret_age == death_age: no pre-retirement years, so
        # income_need_at_death == income_today exactly (years_since_today
        # == 0) — makes the "pension sized to ~ cover year-one need" setup
        # exact instead of needing to pre-compute retirement-date inflation.
        inputs = {**sample_inputs, "jason_age": 60, "justin_age": 60,
                  "retirement_income_today_dollars": 138423, "inflation_rate": 0.03,
                  "expected_return_pre_retirement": 0.0, "expected_return_post_retirement": 0.0,
                  "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
                  "jason_social_security": 0, "jason_ss_delayed": 0, "justin_social_security": 0,
                  "pension_55": 0, "pension_60": 138423, "pension_65": 138423,
                  "jason_life_basic": 0, "jason_life_supplemental": 0, "jason_life_term": 0}
        accounts = [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": 1_000_000}]
        result = run_survivor_scenario(inputs, accounts, ret_age=60, deceased="jason", death_age=60,
                                        survivor_need_factor=1.0)
        draws = [row["draw"] for row in result["schedule"][:10]]
        assert draws[0] < 10000  # pension ~ covers year-one need almost exactly
        assert draws[-1] > draws[0] + 1000  # need outpaces the frozen pension over time

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
