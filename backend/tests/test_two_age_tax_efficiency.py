"""
Independent, hand-verified reference cases for two-age Tax Efficiency --
written BEFORE jason_ret_age/justin_ret_age exist on
run_tax_efficiency_simulation (backend/docs/CALCULATION_CONTRACT.md
section 34, 2026-09-08, Milestone 3 of 4). This file is expected to
fail at collection until that implementation exists.

Verification strategy: monkeypatched random.gauss=0.0 (with the 0%
expected_return_post_retirement default in base_inputs) makes every one
of the N=1000 pre-generated trials identical, so median/p10/p90 all
collapse to the same single deterministic value -- computed by hand by
replaying the shared, already-reviewed primitives' own contracts
(_ordered_draw's DEFAULT_ORDER=taxable,pretax,hsa,roth taxed at flat
TAX_PRETAX=0.22/TAX_TAXABLE=0.15/TAX_ROTH=0.00, ROTH_FIRST_ORDER=roth,
taxable,pretax,hsa, _optimal_draw's cap-gains-then-pretax-then-taxable-
remainder policy), not inferred from the code under test.
"""

import pytest
import random as random_module

from simulation_engine import run_tax_efficiency_simulation

TAXABLE = lambda balance: [{"name": "Brokerage", "account_type": "taxable", "owner": "joint", "balance": balance}]
PRETAX = lambda balance: [{"name": "IRA", "account_type": "ira", "owner": "joint", "balance": balance}]


def base_inputs(**overrides):
    inputs = {
        "jason_age": 61, "justin_age": 61,
        "inflation_rate": 0.0,
        "expected_return_pre_retirement": 0.0,
        "expected_return_post_retirement": 0.0,
        "retirement_income_today_dollars": 50000,
        "annual_hsa_contribution": 0, "annual_rsu_value": 0,
        "jason_social_security": 0, "justin_social_security": 0,
        "healthcare_pre_medicare": 0, "healthcare_post_medicare": 0,
        "justin_w2_salary": 0, "justin_employee_401k_pct": 0, "justin_employer_401k_pct": 0,
        "justin_annual_bonus_pct": 0, "justin_annual_rsu_value": 0,
        "w2_salary": 0, "employee_401k_pct": 0, "employer_401k_pct": 0,
        "annual_bonus_pct": 0,
        "pension_55": 0, "pension_60": 0, "pension_65": 0,
        "retirement_end_age": 63,
    }
    inputs.update(overrides)
    return inputs


@pytest.fixture(autouse=True)
def _deterministic_returns(monkeypatch):
    monkeypatch.setattr(random_module, "gauss", lambda mu, sigma: 0.0)


class TestBasicTaxableFirstTwoAge:
    """Both retire now at 61, 2-year horizon (ages 61-62), $80,000
    taxable / $300,000 pretax, $50,000/yr spending, 0% inflation/growth,
    no guaranteed income. taxable_first (DEFAULT_ORDER = taxable,
    pretax, hsa, roth) -- taxable draws are NOT tax-free in this tool:
    _ordered_draw is called with tax_taxable_rate=TAX_TAXABLE=0.15 (the
    LTCG approximation this tool's own docstring documents), unlike
    Roth Conversion's simulate_withdrawal_year calls (taxable_rate=0.0
    there). Every number below replays _ordered_draw's own grossed-up
    arithmetic by hand with THAT rate, not assumed tax-free.

    Year 0 (age 61): taxable draw grossed up for the 15% rate:
    gross=50000/0.85=58823.53, tax=58823.53*0.15=8823.53,
    draw=min(58823.53,80000)=58823.53 (affordable) ->
    taxable=80000-58823.53=21176.47, need fully funded.
    Year 1 (age 62): taxable draw grossed up again, but bal=21176.47 <
    the full 58823.53 needed -> draw=21176.47 (all of it),
    tax=21176.47*0.15=3176.47, net=21176.47-3176.47=18000.00,
    remaining=50000-18000=32000 -> falls through to pretax (22%):
    gross=32000/0.78=41025.64, tax=41025.64*0.22=9025.64,
    pretax_after=300000-41025.64=258974.36. Year 1 total tax =
    3176.47+9025.64=12202.11.

    lifetime_tax (rounded ONCE per trial, not per year) =
    round(8823.53+12202.11) = round(21025.64) = 21026. final_balance =
    round(258974.36+0+0+0) = 258974. Fully funded both years ->
    success_rate=100% (every one of the N=1000 identical deterministic
    trials)."""

    def test_two_year_taxable_first(self):
        inputs = base_inputs()
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=61)
        assert r["mode"] == "two_age"
        tf = r["strategies"]["taxable_first"]
        assert tf["median_lifetime_tax"] == 21026
        assert tf["p10_lifetime_tax"] == 21026
        assert tf["p90_lifetime_tax"] == 21026
        assert tf["median_final_balance"] == 258974
        assert tf["success_rate"] == 100.0

    def test_roth_first_draws_roth_before_taxable_or_pretax(self):
        """Same household, $50,000 Roth added -- ROTH_FIRST_ORDER (roth,
        taxable, pretax, hsa) draws entirely from Roth first (untaxed --
        Roth is the one bucket with a real 0% rate in this tool), so
        with $50,000/yr spending and $50,000 Roth, year 0 is fully
        funded from Roth alone: $0 tax. Year 1: Roth exhausted, falls to
        taxable at the 15% rate exactly like the basic test's own year 0
        (ample $80,000 balance): gross=50000/0.85=58823.53,
        tax=8823.53, taxable_after=80000-58823.53=21176.47.
        lifetime_tax=round(0+8823.53)=8824 -- LESS than taxable_first's
        21026 in the same household (Roth's own 0% rate + one less year
        needing the 15%/22% brackets at all), but not $0."""
        inputs = base_inputs()
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000) +
                                           [{"name": "Roth", "account_type": "roth_ira", "owner": "joint", "balance": 50000}],
                                           jason_ret_age=61, justin_ret_age=61)
        rf = r["strategies"]["roth_first"]
        assert rf["median_lifetime_tax"] == 8824
        assert rf["median_final_balance"] == 321176  # 300000 (pretax, untouched) + 21176.47 (taxable_after)
        assert rf["success_rate"] == 100.0


class TestEitherRetirementOrderIsSymmetric:
    """Justin retires first (Jason's own salary funds gap income) vs.
    Jason retiring first (mirror) -- since Tax Efficiency's tax model is
    a flat rate with no bracket-capacity concept at all (section 34's
    contract), working income can only ever affect the NET spending
    offset, never a tax rate -- results must be byte-identical in both
    directions, not just similar."""

    def test_symmetric_gap_income_produces_identical_results(self):
        justin_first = base_inputs(retirement_income_today_dollars=50000, w2_salary=100000)
        jason_first  = base_inputs(retirement_income_today_dollars=50000, justin_w2_salary=100000)
        r_a = run_tax_efficiency_simulation(justin_first, PRETAX(300000) + TAXABLE(80000),
                                             jason_ret_age=63, justin_ret_age=61)
        r_b = run_tax_efficiency_simulation(jason_first, PRETAX(300000) + TAXABLE(80000),
                                             jason_ret_age=61, justin_ret_age=63)
        assert r_a["later_retiree"] == "jason"
        assert r_b["later_retiree"] == "justin"
        assert r_a["strategies"]["taxable_first"] == r_b["strategies"]["taxable_first"]
        assert r_a["strategies"]["roth_first"] == r_b["strategies"]["roth_first"]
        assert r_a["strategies"]["optimal"] == r_b["strategies"]["optimal"]
        assert r_a["still_working_spouse_income_first_year"] == r_b["still_working_spouse_income_first_year"]


class TestPastRetirementSelectionAndUnequalAges:
    def test_past_retirement_selection_starts_immediately(self):
        """Jason already 2 years past his selected retirement age (63
        today, selected 61) -- phase2 must start at his real current
        age, not the stale selection."""
        inputs = base_inputs(jason_age=63, justin_age=63, retirement_end_age=65)
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=63)
        assert r["phase2_start_age"] == 63
        assert r["strategies"]["taxable_first"]["success_rate"] == 100.0

    def test_unequal_ages_use_each_spouses_own_ss_claim_age(self):
        """8-year age gap (Jason 61, Justin 53). Justin's own spousal SS
        (claim age 67 default) is nowhere near active at phase2_start."""
        inputs = base_inputs(jason_age=61, justin_age=53, justin_social_security=20000,
                              retirement_end_age=63)
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=61)
        # No SS active in year 0 (Justin only 53) -- same taxable_first
        # numbers as the no-SS baseline test above.
        assert r["strategies"]["taxable_first"]["median_lifetime_tax"] == 21026


class TestNonzeroInflationAndReturns:
    def test_spending_and_taxes_scale_with_inflation_and_growth(self):
        """A positive growth rate must show up in the final balance
        (more growth -> a materially different number than the 0%
        baseline) -- 0% everywhere else in this file would mask a
        broken per-year loop entirely."""
        inputs = base_inputs(expected_return_post_retirement=0.05, inflation_rate=0.02)
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=61)
        tf = r["strategies"]["taxable_first"]
        assert tf["median_final_balance"] != 258974
        assert tf["success_rate"] == 100.0


class TestRmdIsForcedRegardlessOfSpendingNeed:
    """Both 76 today (RMD_START_AGE=73, born 1950), retiring now, $0
    spending need -- RMD is still forced every year and its after-tax
    proceeds sweep into taxable as pure surplus (no spending to absorb
    them), same "cash_available exceeds need" surplus path
    _cash_available_offsets_need's own contract already establishes for
    every other income source in this file. $500,000 pretax, 0%
    inflation/growth. Year 0 (age 76): rmd=500000/23.7=21097.05,
    tax=21097.05*0.22=4641.35, pretax_after=478902.95, the $16455.70
    after-tax remainder swept to taxable. Year 1 (age 77):
    rmd=478902.95/22.9=20912.79, tax=4600.81,
    pretax_after=457990.16, taxable+=16311.98 (total 32767.67).
    lifetime_tax=round(4641.35+4600.81)=9242. final_balance=
    round(457990.16+32767.67)=490758."""

    def test_rmd_forces_a_taxable_pretax_draw_with_no_spending_need(self):
        inputs = base_inputs(jason_age=76, justin_age=76, retirement_income_today_dollars=0,
                              retirement_end_age=78)
        r = run_tax_efficiency_simulation(inputs, PRETAX(500000),
                                           jason_ret_age=76, justin_ret_age=76)
        tf = r["strategies"]["taxable_first"]
        assert tf["median_lifetime_tax"] == 9242
        assert tf["median_final_balance"] == 490758
        assert tf["success_rate"] == 100.0


class TestShortfallReportsUnmetNeed:
    def test_insufficient_resources_fail_every_trial(self):
        """$0 everywhere, $50,000/yr spending, no guaranteed income --
        every trial fails every year -- success_rate must be 0%, not a
        false 100%."""
        inputs = base_inputs()
        r = run_tax_efficiency_simulation(inputs, PRETAX(0) + TAXABLE(0),
                                           jason_ret_age=61, justin_ret_age=61)
        for strat in ("taxable_first", "roth_first", "optimal"):
            assert r["strategies"][strat]["success_rate"] == 0.0


class TestAllThreeStrategiesShareTheSameTrialConditions:
    def test_zero_spending_makes_all_three_strategies_identical(self):
        """With $0 spending need, no strategy ever draws anything --
        all three must report the exact same (unchanged) final balance
        and $0 tax, proving they run against the same starting
        balances/timeline/returns and only differ in draw POLICY, not
        underlying trial conditions."""
        inputs = base_inputs(retirement_income_today_dollars=0)
        r = run_tax_efficiency_simulation(inputs, PRETAX(300000) + TAXABLE(80000),
                                           jason_ret_age=61, justin_ret_age=61)
        tf, rf, opt = (r["strategies"][s] for s in ("taxable_first", "roth_first", "optimal"))
        assert tf["median_final_balance"] == rf["median_final_balance"] == opt["median_final_balance"] == 380000
        assert tf["median_lifetime_tax"] == rf["median_lifetime_tax"] == opt["median_lifetime_tax"] == 0


class TestTwoAgeTaxEfficiencyModeRequiresBothAges:
    def test_only_jason_ret_age_raises(self):
        inputs = base_inputs()
        with pytest.raises(ValueError):
            run_tax_efficiency_simulation(inputs, PRETAX(300000), jason_ret_age=61)

    def test_only_justin_ret_age_raises(self):
        inputs = base_inputs()
        with pytest.raises(ValueError):
            run_tax_efficiency_simulation(inputs, PRETAX(300000), justin_ret_age=61)


class TestSingleAgeModeUnaffected:
    def test_default_call_has_no_two_age_fields(self, sample_inputs, sample_accounts):
        r = run_tax_efficiency_simulation(sample_inputs, sample_accounts, ret_age=60, ss_timing="early")
        assert "mode" not in r
        assert "jason_ret_age" not in r
